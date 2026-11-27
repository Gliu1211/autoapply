"""HTTP API + serves the built React UI."""
from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import companies, db, inbox, llm, matching, outreach
from . import profile as prof
from . import settings as cfg
from .autopilot import STATE, worker
from .config import FRONTEND_DIST, SHOTS
from .extension_api import router as ext_router
from .sources import add_job_url
from .sources.boards import add_board, seed_boards
from .util import detect_ats

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.conn()
    seed_boards()
    worker.start()
    yield
    worker.stopping = True     # shutting down: don't start more applications while the browser goes away


app = FastAPI(title="AutoApply", lifespan=lifespan)
# The Chrome extension calls this API from its background worker (chrome-extension:// origin). Nothing else may.
app.add_middleware(CORSMiddleware, allow_origin_regex=r"^chrome-extension://[a-p]{32}$",
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
app.include_router(ext_router)


def _ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


APP_COLS = """a.id, a.status, a.status_detail, a.ats, a.created_at, a.started_at, a.sent_at, a.updated_at, a.attempts,
              a.blank_required, a.response, a.response_at, a.approved, a.answers, a.screenshots,
              j.id AS job_id, j.company, j.title, j.location, j.score, j.score_reason, j.url, j.apply_url, j.posted_at, j.source"""


def _slim(a: dict) -> dict:
    a = dict(a)
    ans = a.pop("answers", []) or []
    a["questions_answered"] = sum(1 for x in ans if x.get("answer") and x.get("kind") != "file")
    a["used_resume"] = any(x.get("kind") == "file" and x.get("answer") for x in ans)
    a["shot"] = (a.pop("screenshots", None) or [None])[-1]
    return a


# ---------------- overview ----------------

@app.get("/api/state")
def state():
    p, s = prof.get(), cfg.get()
    stats = {
        "sent_total": db.scalar("SELECT COUNT(*) FROM applications WHERE status='sent'"),
        "sent_today": matching.sent_today(),
        "sent_7d": db.scalar("SELECT COUNT(*) FROM applications WHERE status='sent' AND sent_at >= ?", (_ago(7),)),
        "interviews": db.scalar("SELECT COUNT(*) FROM applications WHERE response IN ('interview','assessment','offer')"),
        "replies_30d": db.scalar("SELECT COUNT(*) FROM messages WHERE received_at >= ? AND category NOT IN ('receipt','other','not_job')", (_ago(30),)),
        "emailed_back": db.scalar("SELECT COUNT(DISTINCT application_id) FROM messages WHERE application_id IS NOT NULL AND category NOT IN ('receipt','not_job')"),
        "not_sent": db.scalar("SELECT COUNT(*) FROM applications WHERE status IN ('queued','applying','needs_you','ready','failed')"),
        "needs_you": db.scalar("SELECT COUNT(*) FROM applications WHERE status IN ('needs_you','ready')"),
        "queued": db.scalar("SELECT COUNT(*) FROM applications WHERE status='queued'"),
        "applying": db.scalar("SELECT COUNT(*) FROM applications WHERE status='applying'"),
        "jobs_matched": db.scalar("SELECT COUNT(*) FROM jobs WHERE status='scored' AND score >= ?", (s["min_match"],)),
        "jobs_total": db.scalar("SELECT COUNT(*) FROM jobs"),
    }
    last_sent = db.scalar("SELECT MAX(sent_at) FROM applications WHERE status='sent'")
    local_midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    month_start = datetime.now().astimezone().replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    spend = {
        "today": db.scalar("SELECT COALESCE(SUM(cost),0) FROM llm_usage WHERE t >= ?", (local_midnight.isoformat(timespec="seconds"),)),
        "month": db.scalar("SELECT COALESCE(SUM(cost),0) FROM llm_usage WHERE t >= ?", (month_start.isoformat(timespec="seconds"),)),
        "mode": s.get("cost_mode", "balanced"),
    }
    recent = db.q(f"SELECT {APP_COLS} FROM applications a JOIN jobs j ON j.id=a.job_id "
                  "ORDER BY COALESCE(a.sent_at, a.updated_at) DESC LIMIT 8")
    return {
        "name": prof.display_name(p), "stats": stats, "last_sent": last_sent, "spend": spend,
        "autopilot": {**STATE, "enabled": s["autopilot"], "interval_minutes": s["interval_minutes"],
                      "daily_cap": s["daily_cap"], "submit_mode": s["submit_mode"], "per_company": s["per_company_30d"]},
        "gmail": inbox.status(),
        "llm": llm.available(),
        "missing": prof.missing_essentials(p), "gaps": prof.optional_gaps(p),
        "recent": [_slim(a) for a in recent],
        "events": db.q("SELECT t, kind, msg FROM events ORDER BY id DESC LIMIT 12"),
    }


@app.post("/api/autopilot")
def set_autopilot(body: dict = Body(...)):
    cfg.update({"autopilot": bool(body.get("enabled"))})
    worker.kick(cycle=bool(body.get("enabled")))
    return {"ok": True}


@app.post("/api/run-now")
def run_now():
    missing = prof.missing_essentials()
    if missing:
        raise HTTPException(400, f"Finish your profile first: {', '.join(missing)}")
    worker.kick(cycle=True)
    return {"ok": True}


@app.get("/api/events")
def events(limit: int = 50):
    return db.q("SELECT t, kind, msg FROM events ORDER BY id DESC LIMIT ?", (limit,))


# ---------------- applications ----------------

@app.get("/api/applications")
def applications(tab: str = "everything", q: str = "", days: int = 0, response: str = "", limit: int = 300):
    where, params = [], []
    if tab == "not_sent":
        where.append("a.status IN ('queued','applying','needs_you','ready','failed')")
    elif tab == "sent":
        where.append("a.status='sent'")
    elif tab == "needs_you":
        where.append("a.status IN ('needs_you','ready')")
    elif tab == "skipped":
        where.append("a.status='skipped'")
    else:
        where.append("a.status != 'skipped'")
    if days:
        where.append("COALESCE(a.sent_at, a.created_at) >= ?")
        params.append(_ago(days))
    if response == "none":
        where.append("a.response IS NULL")
    elif response:
        where.append("a.response = ?")
        params.append(response)
    if q:
        where.append("(j.company LIKE ? OR j.title LIKE ? OR a.answers LIKE ?)")
        params += [f"%{q}%"] * 3
    rows = db.q(f"SELECT {APP_COLS} FROM applications a JOIN jobs j ON j.id=a.job_id WHERE {' AND '.join(where)} "
                "ORDER BY CASE WHEN a.status IN ('needs_you','ready','applying','queued','failed') THEN 0 ELSE 1 END, "
                "COALESCE(a.sent_at, a.updated_at) DESC LIMIT ?", params + [limit])
    return [_slim(r) for r in rows]


@app.get("/api/applications/{app_id}")
def application(app_id: int):
    a = db.q1("SELECT * FROM applications WHERE id=?", (app_id,))
    if not a:
        raise HTTPException(404)
    a["job"] = db.q1("SELECT id, company, title, location, url, apply_url, score, score_reason, salary, posted_at, source, "
                     "substr(description,1,3000) AS description FROM jobs WHERE id=?", (a["job_id"],))
    a["messages"] = db.q("SELECT id, sender, subject, summary, category, received_at FROM messages WHERE application_id=? "
                         "ORDER BY received_at DESC", (app_id,))
    a["outreach"] = db.q1("SELECT * FROM outreach WHERE application_id=?", (app_id,))
    return a


def _requeue(app_id: int, **extra) -> dict:
    a = db.q1("SELECT id FROM applications WHERE id=?", (app_id,))
    if not a:
        raise HTTPException(404)
    db.app_update(app_id, status="queued", status_detail="Queued to send", **extra)
    worker.kick()
    return {"ok": True}


@app.post("/api/applications/{app_id}/retry")
def retry(app_id: int):
    return _requeue(app_id, manual=1)


@app.post("/api/applications/{app_id}/approve")
def approve(app_id: int):
    return _requeue(app_id, approved=1)


@app.post("/api/applications/{app_id}/skip")
def skip(app_id: int):
    db.app_update(app_id, status="skipped", status_detail="Skipped by you")
    return {"ok": True}


@app.post("/api/applications/{app_id}/mark-sent")
def mark_sent(app_id: int):
    db.app_update(app_id, status="sent", sent_at=db.now(), status_detail="Marked sent by you")
    return {"ok": True}


@app.post("/api/applications/{app_id}/answers")
def answer_pending(app_id: int, body: dict = Body(...)):
    p = prof.get()
    for q, a in (body.get("answers") or {}).items():
        a = str(a).strip()
        if not a:
            continue
        prof.remember(q, a)
        # Questions worded many ways map onto one profile field, so one answer covers them all.
        low = q.lower()
        if re.search(r"salary|compensation|desired pay|pay expectation", low) and not p["eligibility"].get("salary_expectation"):
            prof.save({"eligibility": {"salary_expectation": a}})
        elif re.search(r"18 years|at least 18|over 18|legal age", low) and a in ("Yes", "No"):
            prof.save({"eligibility": {"over_18": a}})
    return _requeue(app_id, pending_questions=[])


@app.post("/api/applications/{app_id}/open")
def open_app(app_id: int):
    a = db.q1("SELECT j.apply_url, j.url FROM applications a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (app_id,))
    if not a:
        raise HTTPException(404)
    return _open_visible(a["apply_url"] or a["url"])


# ---------------- jobs ----------------

@app.get("/api/jobs")
def jobs(status: str = "matched", q: str = "", limit: int = 200, offset: int = 0):
    s = cfg.get()
    where, params = [], []
    if status == "matched":
        where.append("status IN ('scored','queued') AND score >= ?")
        params.append(s["min_match"])
    elif status == "below":
        where.append("status='scored' AND score < ?")
        params.append(s["min_match"])
    elif status == "filtered":
        where.append("status='filtered'")
    elif status == "pending":
        where.append("status IN ('new','candidate')")
    elif status != "all":
        where.append("status=?")
        params.append(status)
    if q:
        where.append("(company LIKE ? OR title LIKE ? OR location LIKE ?)")
        params += [f"%{q}%"] * 3
    w = " AND ".join(where) or "1=1"
    rows = db.q(f"SELECT id, company, title, location, remote, url, ats, source, score, score_reason, status, filter_reason, "
                f"posted_at, salary, role_rank, company_rank FROM jobs WHERE {w} ORDER BY {matching.order_sql(s)}, "
                f"COALESCE(score,-1) DESC, COALESCE(posted_at,'') DESC LIMIT ? OFFSET ?", params + [limit, offset])
    roles = prof.get()["looking_for"].get("roles") or []
    known = companies.stored()
    only = len(matching.wanted_types(s)) if s.get("company_types_only") else 0
    for r in rows:
        rk = r.get("role_rank")
        r["role"] = roles[rk] if isinstance(rk, int) and rk < len(roles) else None
        r["company_types"] = [companies.TYPES[t][0] for t in companies.types_for(r["company"], known) or []]
        cr = r.pop("company_rank", None)
        r["off_type"] = bool(only and cr is not None and cr >= only)   # skipped: not one of your company types
    counts = {r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")}
    return {"jobs": rows, "counts": counts}


@app.post("/api/jobs/{job_id}/apply")
def apply_job(job_id: int):
    app_id = matching.create_application(job_id)
    db.app_update(app_id, manual=1)
    worker.kick()
    return {"ok": True, "application_id": app_id}


@app.post("/api/jobs/{job_id}/skip")
def skip_job(job_id: int):
    db.x("UPDATE jobs SET status='skipped' WHERE id=?", (job_id,))
    return {"ok": True}


@app.post("/api/jobs/add")
def add_job(body: dict = Body(...)):
    url = (body.get("url") or "").strip()
    if not re.match(r"https?://", url):
        raise HTTPException(400, "Paste a full job link (https://...)")
    job_id = add_job_url(url)
    app_id = matching.create_application(job_id)
    db.app_update(app_id, manual=1)
    worker.kick()
    return {"ok": True, "job_id": job_id, "application_id": app_id}


# ---------------- profile ----------------

@app.get("/api/profile")
def get_profile():
    p = prof.get()
    return {"profile": p, "resume_summary": prof.resume_summary(p), "missing": prof.missing_essentials(p)}


@app.put("/api/profile")
def put_profile(body: dict = Body(...)):
    body.pop("resume", None)
    before = prof.get()["looking_for"].get("roles")
    p = prof.save(body)
    if p["looking_for"].get("roles") != before:
        matching.update_role_ranks()     # role order is the apply-first order
    return {"profile": p, "missing": prof.missing_essentials(p)}


@app.post("/api/profile/resume")
async def upload_resume(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    import asyncio
    parse = llm.available() and file.filename.lower().endswith(".pdf")
    try:
        p = await asyncio.to_thread(prof.attach_resume, file.filename, data, parse)
        note = "Read your resume and filled in the blanks." if parse else "Saved. Add a Claude API key to read it automatically."
    except llm.LLMError as e:
        p = prof.get()
        note = f"Saved, but couldn't read it: {e}"
    return {"profile": p, "resume_summary": prof.resume_summary(p), "note": note}


@app.get("/api/remembered")
def remembered():
    return prof.remembered_all()


@app.post("/api/remembered")
def add_remembered(body: dict = Body(...)):
    prof.remember(body.get("question", ""), body.get("answer", ""))
    return prof.remembered_all()


@app.put("/api/remembered/{rid}")
def edit_remembered(rid: int, body: dict = Body(...)):
    db.x("UPDATE remembered SET answer=?, updated_at=? WHERE id=?", (body.get("answer", ""), db.now(), rid))
    return {"ok": True}


@app.delete("/api/remembered/{rid}")
def del_remembered(rid: int):
    db.x("DELETE FROM remembered WHERE id=?", (rid,))
    return {"ok": True}


# ---------------- settings & accounts ----------------

@app.get("/api/settings")
def get_settings():
    boards = db.q("SELECT ats, status, COUNT(*) AS n, SUM(job_count) AS jobs FROM boards GROUP BY ats, status")
    return {"settings": cfg.get(), "gmail": inbox.status(),
            "account_password": cfg.account_password(), "llm": llm.available(),
            "accounts": db.q("SELECT host, email, created_at, verified FROM accounts ORDER BY created_at DESC"),
            "boards": boards, "company_types": companies.summary()}


@app.put("/api/settings")
def put_settings(body: dict = Body(...)):
    s = cfg.update(body)
    if {"company_types", "company_types_only"} & body.keys():
        matching.update_company_ranks(s)       # with what's already sorted; new companies are sorted in the background
        matching.unqueue_unwanted(s)
        if matching.wanted_types(s):
            worker.submit(matching.refresh_company_types())
    worker.kick()
    return {"settings": s}


@app.post("/api/gmail")
def set_gmail(body: dict = Body(...)):
    addr = (body.get("address") or "").strip()
    pw = (body.get("app_password") or "").replace(" ", "")
    err = inbox.test_login(addr, pw)      # test first; only a working login is saved
    if err:
        raise HTTPException(400, err)
    cfg.set_gmail(addr, pw)
    worker.kick()
    return {"ok": True}


@app.post("/api/inbox/sync")
def sync_inbox():
    if not inbox.configured():
        st = inbox.status()
        raise HTTPException(400, st["error"] or "Connect Gmail first (Settings > Accounts)")
    fut = worker.submit(inbox.sync())
    return {"new": fut.result(timeout=180)}


@app.post("/api/boards")
def add_board_route(body: dict = Body(...)):
    url = body.get("url", "").strip()
    info = detect_ats(url) if url else {"ats": body.get("ats"), "board": body.get("token")}
    if info["ats"] not in ("greenhouse", "lever", "ashby", "workable", "workday") or not info["board"]:
        raise HTTPException(400, "Paste a Greenhouse, Lever, Ashby, Workable or Workday careers link")
    add_board(info["ats"], info["board"])
    return {"ok": True, "ats": info["ats"], "token": info["board"]}


def _open_visible(url: str) -> dict:
    """Open a tab in AutoApply's own browser window and wait until it's really there, so errors reach the UI."""
    from .apply.browser import browser
    try:
        name = worker.submit(browser.open_for_user(url)).result(timeout=90)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Couldn't open the browser window: {e}")
    return {"ok": True, "browser": name}


@app.post("/api/browser/open")
def browser_open(body: dict = Body(...)):
    return _open_visible(body.get("url") or "about:blank")


# ---------------- messages & outreach ----------------

@app.get("/api/messages")
def messages(category: str = ""):
    if category:
        return db.q("SELECT * FROM messages WHERE category=? ORDER BY received_at DESC LIMIT 300", (category,))
    return db.q("SELECT * FROM messages WHERE category != 'not_job' ORDER BY received_at DESC LIMIT 300")


@app.post("/api/messages/{mid}/read")
def read_message(mid: int):
    db.x("UPDATE messages SET is_read=1 WHERE id=?", (mid,))
    return {"ok": True}


@app.get("/api/outreach")
def outreach_list():
    rows = db.q("""SELECT a.id, a.sent_at, a.response, j.company, j.title, o.linkedin_note, o.email_subject, o.email_body
                   FROM applications a JOIN jobs j ON j.id=a.job_id LEFT JOIN outreach o ON o.application_id=a.id
                   WHERE a.status='sent' ORDER BY a.sent_at DESC LIMIT 200""")
    for r in rows:
        r["links"] = outreach.search_links(r["company"] or "", r["title"] or "")
    return rows


@app.post("/api/outreach/{app_id}")
def outreach_draft(app_id: int):
    try:
        return outreach.draft(app_id)
    except llm.LLMError as e:
        raise HTTPException(400, str(e))


# ---------------- files & UI ----------------

@app.get("/shots/{name}")
def shot(name: str):
    path = (SHOTS / name).resolve()
    if path.parent != SHOTS.resolve() or not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"detail": "UI not built. Run: cd frontend && npm install && npm run build"}, status_code=503)
