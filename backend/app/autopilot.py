"""The autopilot loop. Runs on its own thread + event loop, which also owns the browser."""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from . import db, inbox, matching
from . import settings as cfg
from .sources import discover

log = logging.getLogger("autoapply.autopilot")

STATE: dict = {"phase": "idle", "detail": "", "last_cycle": None, "next_cycle": None, "running": [], "last_error": ""}


class Worker:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.kick_event: asyncio.Event | None = None
        self.want_cycle = False
        self.want_queue = False
        self.stopping = False    # set on shutdown: start no new applications

    def start(self) -> None:
        if self.thread:
            return
        ready = threading.Event()

        def run() -> None:
            self.loop = asyncio.new_event_loop()   # Proactor on Windows, which Playwright needs
            asyncio.set_event_loop(self.loop)
            self.kick_event = asyncio.Event()
            ready.set()
            self.loop.create_task(main_loop())
            self.loop.run_forever()

        self.thread = threading.Thread(target=run, name="autoapply-worker", daemon=True)
        self.thread.start()
        ready.wait(10)

    def submit(self, coro) -> concurrent.futures.Future:
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def kick(self, cycle: bool = False) -> None:
        self.want_queue = True
        self.want_cycle = self.want_cycle or cycle
        if self.loop and self.kick_event:
            self.loop.call_soon_threadsafe(self.kick_event.set)


worker = Worker()


def _set(phase: str, detail: str = "") -> None:
    STATE["phase"], STATE["detail"] = phase, detail


async def cycle() -> None:
    s = cfg.get()
    _set("discovering", "Looking for new jobs")
    counts = await discover()
    _set("matching", "Checking which jobs fit")
    matching.prefilter()
    _set("matching", "Sorting companies by type")
    await matching.refresh_company_types()
    # Score only as many as could go out soon: about 3 candidates per open slot, minus good matches already waiting.
    room = max(0, s["daily_cap"] - matching.sent_today()
               - (db.scalar("SELECT COUNT(*) FROM applications WHERE status IN ('queued','applying')") or 0))
    waiting = db.scalar("SELECT COUNT(*) FROM jobs WHERE status='scored' AND score >= ?", (s["min_match"],)) or 0
    to_score = max(0, min(80, room * 3 - waiting))
    if to_score:
        _set("scoring", f"Scoring the best {to_score} new matches")
        await matching.score_candidates(limit=to_score)
    queued_now = db.scalar("SELECT COUNT(*) FROM applications WHERE status IN ('queued','applying')") or 0
    room = max(0, s["daily_cap"] - matching.sent_today() - queued_now)
    n = matching.queue_best(room)
    STATE["last_cycle"] = db.now()
    db.kv_set("last_cycle", STATE["last_cycle"])
    db.event("cycle", f"Queued {n} applications ({sum(counts.values())} new jobs found)")


async def _run_one(app_id: int) -> None:
    from .apply.engine import run_application
    STATE["running"].append(app_id)
    try:
        await run_application(app_id)
    finally:
        STATE["running"].remove(app_id)


async def process_queue() -> None:
    from .apply.browser import BrowserUnavailable
    s = cfg.get()
    sem = asyncio.Semaphore(max(1, int(s["concurrency"])))
    tasks: set[asyncio.Task] = set()
    passed: set[int] = set()
    browser_down = asyncio.Event()
    matching.unqueue_unwanted(s)
    while True:
        if worker.stopping:
            break
        if browser_down.is_set():
            _set("paused", "The browser couldn't start; trying again next round")
            break
        if matching.sent_today() + len(tasks) >= s["daily_cap"]:
            _set("paused", f"Daily cap of {s['daily_cap']} reached")
            break
        rows = db.q(f"""SELECT a.id, a.approved, a.manual, j.company, j.ats FROM applications a JOIN jobs j ON j.id=a.job_id
                       WHERE a.status='queued'
                       ORDER BY a.approved DESC, a.manual DESC, {matching.order_sql(s, "j.")}, COALESCE(j.score,0) DESC, a.id LIMIT 50""")
        rows = [r for r in rows if r["id"] not in passed and r["id"] not in STATE["running"]]
        if not rows:
            break
        r = rows[0]
        passed.add(r["id"])
        blocked = matching.site_blocked(r["ats"])
        if blocked and not (r["approved"] or r["manual"]):
            db.app_update(r["id"], status="skipped", status_detail=blocked)
            continue
        ok, why = matching.company_ok(r["company"], exclude_app=r["id"])
        if not ok:
            db.app_update(r["id"], status_detail=f"Waiting: {why}")
            continue
        await sem.acquire()
        _set("applying", f"Applying to {r['company']}")

        async def go(app_id: int) -> None:
            try:
                await _run_one(app_id)
            except BrowserUnavailable:
                browser_down.set()        # stop launching more; the app was already put back in the queue
            except Exception:  # noqa: BLE001
                log.exception("application %s crashed", app_id)
            finally:
                sem.release()

        t = asyncio.create_task(go(r["id"]))
        tasks.add(t)
        t.add_done_callback(tasks.discard)
        await asyncio.sleep(2)
        if browser_down.is_set():
            continue
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def main_loop() -> None:
    await asyncio.sleep(2)
    # Anything left "applying" from a crash goes back in the queue.
    # One caught mid-submit may already have reached the employer: never resend it blindly.
    db.x("""UPDATE applications SET status='needs_you',
            status_detail='AutoApply restarted while submitting this one. Check the employer site or your email before retrying.'
            WHERE status='applying' AND (status_detail LIKE 'Submitting%' OR status_detail LIKE 'Waiting for the verification code%'
                                         OR status_detail LIKE 'Got the code%')""")
    db.x("UPDATE applications SET status='queued', status_detail='Restarted' WHERE status='applying'")
    STATE["last_cycle"] = db.kv_get("last_cycle")
    last_inbox = 0.0
    while not worker.stopping:
        s = cfg.get()
        try:
            last = STATE["last_cycle"]
            due = s["autopilot"] and (not last or datetime.now(timezone.utc) - datetime.fromisoformat(last)
                                      >= timedelta(minutes=s["interval_minutes"]))
            nxt = (datetime.fromisoformat(last) + timedelta(minutes=s["interval_minutes"])) if last else datetime.now(timezone.utc)
            STATE["next_cycle"] = nxt.isoformat(timespec="seconds") if s["autopilot"] else None
            if due or worker.want_cycle:
                worker.want_cycle = False
                await cycle()
            if s["autopilot"] or worker.want_queue:
                worker.want_queue = False
                await process_queue()
            if inbox.configured() and time.monotonic() - last_inbox > 600:
                last_inbox = time.monotonic()
                _set("inbox", "Checking Gmail for replies")
                await inbox.sync()
            STATE["last_error"] = ""
        except Exception as e:  # noqa: BLE001 — keep the loop alive no matter what
            log.exception("autopilot loop error")
            STATE["last_error"] = f"{type(e).__name__}: {e}"
            db.event("error", STATE["last_error"][:300])
        if STATE["phase"] != "paused" or not s["autopilot"]:
            _set("idle", "")
        worker.kick_event.clear()
        try:
            await asyncio.wait_for(worker.kick_event.wait(), timeout=20)
        except asyncio.TimeoutError:
            pass
