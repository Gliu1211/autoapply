"""Runs one application end to end in the browser."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

from playwright.async_api import Frame, Page
from playwright.async_api import Error as PWError
from playwright.async_api import TimeoutError as PWTimeout

from .. import db, inbox, llm
from .. import profile as prof
from .. import settings as cfg
from ..config import SHOTS
from ..util import detect_ats
from . import fill as F
from .answers import cover_letter, cover_letter_html, cover_letter_path, llm_answers, pick_option, rule_answer
from .browser import BrowserUnavailable, browser
from .dom import CODE_INPUTS_JS, CODE_PROMPT_RE, EXTRACT_JS, NEXT_RE, STATE_JS, SUBMIT_RE, SUCCESS_RE

log = logging.getLogger("autoapply.engine")
CODE_HINT_RE = (r"one[\s_-]?time|\botp\b|passcode|"
                r"(security|verification|verify|confirmation|auth\w*|access)[\s_-]*(code|input|pin|digit|char)|\bcode\b")
NOT_CODE_RE = (r"zip|postal|post[\s_-]?code|country|area[\s_-]?code|phone|dial|promo|coupon|discount|referr|invit|"
               r"source|tracking|employee|requisition|\bjob\b")


def pick_code_inputs(inputs: list[dict], before: set) -> list[dict]:
    """Which input(s) want the emailed verification code: a row of one-character boxes, or one text box.
    `before` holds the keys of inputs that were already visible before submitting; ones that appeared with the
    code prompt are the strongest sign. Returns [] when it isn't clear, so a person types it instead of a guess."""
    cands = []
    for i in inputs:
        if re.search(NOT_CODE_RE, i["hint"], re.I):
            continue
        named = i["ac"] == "one-time-code" or bool(re.search(CODE_HINT_RE, i["hint"], re.I))
        cands.append({**i, "new": i["key"] not in before, "named": named})
    boxes = [c for c in cands if c["maxlength"] and c["maxlength"] <= 2]
    for pool in ([b for b in boxes if b["new"] and b["named"]], [b for b in boxes if b["named"]], [b for b in boxes if b["new"]]):
        if 4 <= len(pool) <= 10:
            return pool
    wide = [c for c in cands if not (c["maxlength"] and c["maxlength"] <= 2)]
    named_new = [c for c in wide if c["named"] and c["new"]]
    named = [c for c in wide if c["named"]]
    new = [c for c in wide if c["new"]]
    if named_new:
        return named_new[:1]
    if len(named) == 1:
        return named
    if len(new) == 1:
        return new
    return []
APPLY_LINK_RE = r"^(apply|apply now|apply for this (job|position|role)|apply to this job|i'?m interested|start (your )?application|apply on company (website|site)|apply externally)$"


RESUME_DETAIL = re.compile(r"^(education|work experience):|\bgpa\b|grade point|graduat|\bdegree\b|discipline|\bmajor\b|field of study|"
                           r"school|university|college|coursework|start date|end date|employer|company name|job title|"
                           r"responsibilit|current (company|title|role|position)|years of experience|skills", re.I)


async def decide(fields: list[dict], p: dict, job: dict, cache: dict | None = None, log_fn=None,
                 resume_on_form: bool = False) -> dict[str, tuple]:
    """What goes in each field: profile rules, earlier answers, remembered answers, then Claude for the rest.
    Returns {field_id: (value, source)}; source "needs_user" means a person has to answer it.
    Shared by the browser engine and the Chrome extension."""
    cache = cache or {}
    decisions: dict[str, tuple] = {}
    ask_llm = []
    resume_on_form = resume_on_form or any(f["kind"] == "file" and re.search(r"resume|\bcv\b", f["question"], re.I) for f in fields)
    for f in fields:
        key = prof.qkey(f["question"])
        # With the resume attached, optional education/work details are left to the resume instead of retyped.
        if resume_on_form and not f.get("required") and f["kind"] != "file" and RESUME_DETAIL.search(f["question"] or ""):
            decisions[f["id"]] = ("", "resume")
            continue
        v, src = rule_answer(f, p, job)
        if v is None:
            cached = cache.get(key)
            if cached and cached.get("answer") and cached.get("source") in ("generated", "remembered", "you"):
                v, src = cached.get("value", cached["answer"]), cached["source"]
        if v is None:
            r = prof.recall(f["question"])
            if r:
                v, src = r, "remembered"
        opts = f.get("options") or []
        choice = f["kind"] in ("select", "radio", "buttons", "checkboxes", "listbox") or (f["kind"] == "combobox" and 0 < len(opts) < 40)
        if v not in (None, "", []) and choice and opts and f["kind"] != "date" and not any(
                pick_option(x, opts) for x in (v if isinstance(v, list) else [v])):
            f["hint"] = f"The profile says: {v if not isinstance(v, list) else v[0]}. Pick the option that means the same."
            v = None
        if v is None:
            ask_llm.append(f)
        else:
            decisions[f["id"]] = (v, src)
    if ask_llm and llm.available():
        if log_fn:
            log_fn(f"Writing answers for {len(ask_llm)} questions")
        try:
            res = await llm_answers(ask_llm, p, job)
        except llm.LLMError as e:
            if log_fn:
                log_fn(f"Claude unavailable: {e}")
            res = {}
        for f in ask_llm:
            a = res.get(f["id"])
            if not a:
                continue
            val = a["values"] if a["values"] and f["kind"] in ("checkboxes", "multiselect") else a["value"]
            decisions[f["id"]] = (val, "generated" if not a["needs_user"] else "needs_user")
    return decisions


class NeedsUser(Exception):
    """Stop and hand over to the person. `wait` keeps the tab open so they can finish it there."""

    def __init__(self, detail: str, questions: list[dict] | None = None, wait: bool = False):
        super().__init__(detail)
        self.detail, self.questions, self.wait = detail, questions or [], wait


class Run:
    def __init__(self, app: dict, job: dict, page: Page, dry_run: bool = False):
        self.app, self.job, self.page = app, job, page
        self.app_id = app["id"]
        self.p = prof.get()
        self.s = cfg.get()
        self.dry_run = dry_run
        self.answers: dict[str, dict] = {prof.qkey(a["question"]): a for a in app.get("answers") or []}
        self.shots: list[str] = list(app.get("screenshots") or [])
        self.blank_required: dict[str, dict] = {}
        self.cover_path = None

    # ---------- bookkeeping ----------
    def log(self, msg: str) -> None:
        log.info("[app %s] %s", self.app_id, msg)
        db.app_log(self.app_id, msg)
        db.app_update(self.app_id, status_detail=msg)

    def save(self) -> None:
        db.app_update(self.app_id, answers=list(self.answers.values()), screenshots=self.shots[-12:],
                      blank_required=len(self.blank_required))

    async def shot(self, label: str) -> None:
        name = f"{self.app_id}-{len(self.shots) + 1}-{label}.png"
        try:
            await self.page.screenshot(path=str(SHOTS / name), full_page=True, timeout=15000)
            self.shots.append(name)
        except (PWError, PWTimeout):
            pass

    # ---------- page helpers ----------
    async def settle(self, ms: int = 1500) -> None:
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=20000)
            await self.page.wait_for_load_state("networkidle", timeout=6000)
        except PWTimeout:
            pass
        await self.page.wait_for_timeout(ms)

    def frames(self) -> list[Frame]:
        out = [self.page.main_frame]
        for fr in self.page.frames[1:]:
            if re.search(r"recaptcha|hcaptcha|google|doubleclick|facebook|stripe|cloudflare|youtube|vimeo", fr.url or ""):
                continue
            out.append(fr)
        return out

    async def state(self) -> dict:
        try:
            st = await self.page.evaluate(STATE_JS)
        except PWError:
            await self.page.wait_for_timeout(1500)
            st = await self.page.evaluate(STATE_JS)
        if st.get("cookieButtons") and not getattr(self, "_cookies_done", False):
            self._cookies_done = True
            if await self.dismiss_cookies(st):
                st = await self.page.evaluate(STATE_JS)
        return st

    async def dismiss_cookies(self, st: dict) -> bool:
        """Decline non-essential cookies when a consent banner is in the way."""
        order = [r"^(deny|decline|reject)( all)?$", r"^reject (all|non-essential|optional)",
                 r"necessary (cookies )?only|only (necessary|essential)|essential only", r"^(decline|reject|deny)"]
        for pat in order:
            b = next((b for b in st["cookieButtons"] if re.search(pat, b["text"], re.I)), None)
            if b:
                try:
                    await self.page.locator(f'[data-aab="{b["id"]}"]').first.click(timeout=3000)
                    await self.page.wait_for_timeout(600)
                    return True
                except (PWError, PWTimeout):
                    return False
        return False

    async def extract(self) -> list[dict]:
        try:
            await self.page.evaluate("() => { const a = document.activeElement; if (a && a !== document.body && a.blur) a.blur(); }")
        except PWError:
            pass
        out = []
        for fr in self.frames():
            try:
                fs = await fr.evaluate(EXTRACT_JS)
            except PWError:
                continue
            for f in fs:
                f["_frame"] = fr
                out.append(f)
        return out

    async def click_button(self, b: dict) -> None:
        loc = self.page.locator(f'[data-aab="{b["id"]}"]').first
        await robust_click(loc)

    def find_button(self, st: dict, pattern: str) -> dict | None:
        hits = [b for b in st["buttons"] if re.search(pattern, b["text"].strip(), re.I)]
        hits.sort(key=lambda b: (b["type"] != "submit", len(b["text"])))
        return hits[0] if hits else None

    # ---------- answers ----------
    async def ensure_cover_letter(self):
        if self.cover_path and self.cover_path.exists():
            return self.cover_path
        if not (self.s["write_cover_letters"] and llm.available()):
            return None
        body = self.app.get("cover_letter") or await cover_letter(self.p, self.job)
        db.app_update(self.app_id, cover_letter=body)
        self.app["cover_letter"] = body
        path = cover_letter_path(self.app_id, self.p)
        await browser.render_pdf(cover_letter_html(self.p, self.job, body), path)
        self.cover_path = path
        return path

    async def fill_one(self, f: dict, v) -> bool:
        fr, k = f["_frame"], f["kind"]
        first = v[0] if isinstance(v, list) and v else v
        if k == "file":
            path = prof.copy_resume_for_upload(self.p) if v in ("__RESUME__", "__AUTOFILL__") else await self.ensure_cover_letter()
            ok = bool(path) and await F.fill_file(fr, f, path)
            if ok and v == "__AUTOFILL__":
                await self.wait_for_autofill()
            return ok
        if k == "textarea":
            return await F.fill_text(fr, f, first)
        if k == "text":
            if await F.fill_text(fr, f, first):
                return True
            return await F.fill_combobox(self.page, fr, f, v, strict=False)   # autocomplete that clears free text
        if k == "select":
            return await F.fill_select(fr, f, v)
        if k in ("radio", "checkboxes"):
            return await F.fill_choice_group(fr, f, v)
        if k == "checkbox":
            return await F.fill_checkbox(fr, f, v)
        if k == "buttons":
            return await F.fill_buttons(fr, f, v)
        if k == "combobox":
            return await F.fill_combobox(self.page, fr, f, v, strict=bool(f.get("options")))
        if k == "listbox":
            return await F.fill_listbox(self.page, fr, f, v)
        if k == "multiselect":
            return await F.fill_multiselect(self.page, fr, f, v)
        if k == "date":
            return await F.fill_date(fr, f, first)
        return False

    async def wait_for_autofill(self, timeout_ms: int = 25000) -> None:
        """After a resume goes into a site's autofill box, wait until the site has finished reading it."""
        self.log("Letting the site read the resume")
        busy = re.compile(r"parsing|autofilling|auto-filling|analy[sz]ing|reading your resume|processing your resume|uploading", re.I)
        waited = 0
        await self.page.wait_for_timeout(1500)
        while waited < timeout_ms:
            try:
                text = await self.page.evaluate("document.body.innerText")
            except PWError:
                text = ""
            if not busy.search(text or ""):
                break
            await self.page.wait_for_timeout(1000)
            waited += 1000
        await self.page.wait_for_timeout(1500)

    async def fill_page(self, retry: bool = False) -> int:
        """Read the visible form, decide every answer, and fill. Repeats to catch fields that appear conditionally."""
        handled: set[str] = set()
        filled = 0
        late_checked = False
        for rnd in range(5):
            fields = await self.extract()
            todo = []
            for f in fields:
                if f["id"] in handled:
                    continue
                if f["current"] and not (retry and f["required"] and f["kind"] in ("text", "textarea")):
                    v, src = rule_answer(f, self.p, self.job) if f["kind"] in ("text", "combobox", "select") else (None, "")
                    if f["kind"] in ("combobox", "select") and src == "profile" and v not in (None, "", []):
                        conflict = pick_option(v, [str(f["current"])]) is None
                    else:
                        v = v[0] if isinstance(v, list) and v else v
                        conflict = (src == "profile" and isinstance(v, str) and v.strip()
                                    and re.sub(r"\W", "", v.lower()) != re.sub(r"\W", "", str(f["current"]).lower()))
                    if not conflict:
                        handled.add(f["id"])
                        key = prof.qkey(f["question"])
                        if key and key not in self.answers and f["kind"] != "checkbox":
                            shown = "your uploaded resume" if f["kind"] == "file" else str(f["current"])
                            self.answers[key] = {"question": f["question"], "answer": shown, "value": None,
                                                 "source": "prefilled", "required": f["required"], "kind": f["kind"], "ok": True}
                        continue
                todo.append(f)
            if not todo:
                if filled and not late_checked:
                    late_checked = True              # conditional fields (e.g. race after Hispanic=No) can render late
                    await self.page.wait_for_timeout(1200)
                    continue
                break
            for f in todo:
                if f["kind"] in ("combobox", "listbox") and not f.get("options"):
                    f["options"] = await F.probe_options(self.page, f["_frame"], f)

            if any(f["kind"] == "file" and re.search(r"resume|\bcv\b|autofill", f["question"], re.I) for f in fields):
                self.resume_on_form = True
            # The site's own resume reader goes first; everything else is read again after it finishes.
            autofill = [f for f in todo if f["kind"] == "file" and rule_answer(f, self.p, self.job)[0] == "__AUTOFILL__"
                        and f["id"] not in handled]
            if autofill and rnd == 0:
                for f in autofill:
                    handled.add(f["id"])
                    ok = await self.fill_one(f, "__AUTOFILL__")
                    self.answers[prof.qkey(f["question"]) or "autofill"] = {
                        "question": "Autofill from resume", "answer": "your uploaded resume" if ok else "", "value": None,
                        "source": "profile", "required": False, "kind": "file", "ok": ok}
                continue
            decisions = await decide(todo, self.p, self.job, self.answers, self.log, resume_on_form=getattr(self, "resume_on_form", False))

            for f in todo:
                handled.add(f["id"])
                v, src = decisions.get(f["id"], (None, ""))
                key = prof.qkey(f["question"])
                empty = v in (None, "", []) or src == "needs_user"
                ok = False
                if not empty:
                    try:
                        ok = await self.fill_one(f, v)
                    except (PWError, PWTimeout) as e:
                        log.info("fill failed for %s: %s", f["question"][:50], e)
                    filled += int(ok)
                if (empty or not ok) and f["required"]:
                    self.blank_required[key] = {"question": f["question"], "kind": f["kind"],
                                                "options": f.get("options", [])[:400], "required": True}
                elif key in self.blank_required and ok:
                    self.blank_required.pop(key, None)
                if f["kind"] == "file" and v == "__COVER__" and ok:
                    shown = self.cover_path.name if self.cover_path else "cover letter"
                elif f["kind"] == "file":
                    shown = "your uploaded resume" if v == "__RESUME__" else ""
                else:
                    shown = " | ".join(map(str, v)) if isinstance(v, list) else ("" if v is None else str(v))
                    if isinstance(v, list) and f["kind"] not in ("checkboxes", "multiselect"):
                        shown = str(v[0]) if v else ""
                    if not empty and f.get("options") and f["kind"] in ("select", "radio", "buttons", "listbox", "combobox"):
                        shown = pick_option(v, f["options"]) or shown
                if f["kind"] == "checkbox" and not shown:
                    continue
                self.answers[key] = {"question": f["question"], "answer": shown if not empty else "", "value": v if not empty else None,
                                     "source": src or "", "required": f["required"], "kind": f["kind"], "ok": ok}
            await self.page.wait_for_timeout(700)
        self.save()
        return filled

    # ---------- submitting ----------
    async def code_inputs(self) -> list[dict]:
        out = []
        for fr in self.frames():
            try:
                items = await fr.evaluate(CODE_INPUTS_JS)
            except PWError:
                continue
            for i in items:
                i["_frame"], i["key"] = fr, (i["doc"], i["id"])   # data-aa numbers restart in every frame and page
                out.append(i)
        return out

    async def type_code(self, targets: list[dict], code: str) -> bool:
        """Type the code and read it back. True only when the boxes hold exactly the code."""
        want = re.sub(r"[\s-]", "", code).lower()
        locs = [t["_frame"].locator(f'[data-aa="{t["id"]}"]').first for t in targets]

        async def typed() -> str:
            vals = []
            for loc in locs:
                try:
                    vals.append(await loc.input_value(timeout=2000))
                except (PWError, PWTimeout):
                    vals.append("")
            return re.sub(r"[\s-]", "", "".join(vals)).lower()

        if len(targets) == 1:
            await F.fill_text(targets[0]["_frame"], targets[0], code)
        else:
            for t, ch in zip(targets, code):
                await F.fill_text(t["_frame"], t, ch)
        if await typed() == want:
            return True
        # Widgets that move focus box to box on their own: clear them, then type into the first one like a person.
        try:
            for loc in locs:
                await loc.fill("", timeout=3000)
            await locs[0].click(timeout=3000)
            await self.page.keyboard.type(code, delay=60)
        except (PWError, PWTimeout):
            return False
        return await typed() == want

    async def handle_code(self, since: datetime, before: set) -> None:
        self.log("Waiting for the verification code email")
        targets: list[dict] = []
        for _ in range(5):   # the boxes can render a moment after the prompt text
            targets = pick_code_inputs(await self.code_inputs(), before)
            if targets:
                break
            await self.page.wait_for_timeout(1000)
        if not targets:
            raise NeedsUser("The form wants an emailed verification code, but I couldn't tell which box it goes in. "
                            "Type the code in the browser window.", wait=True)
        if len(targets) > 1:
            length = len(targets)
        else:
            m = targets[0]["maxlength"]
            length = m if m and 4 <= m <= 10 else None
        hints = ["greenhouse", "lever", "ashby", "workday", "workable", "security code", "verification"]
        code = await inbox.wait_for_code(since, self.job.get("company") or "", hints, length=length, timeout=180)
        if not code:
            raise NeedsUser("The form wants an emailed verification code. Connect Gmail in Settings, or type the code in the browser window.", wait=True)
        self.log("Got the code from Gmail")
        if not await self.type_code(targets, code):
            raise NeedsUser("I got the verification code from Gmail but it didn't go into the form correctly. "
                            "Type it in the browser window.", wait=True)
        await self.page.wait_for_timeout(500)
        st = await self.state()
        b = self.find_button(st, SUBMIT_RE) or self.find_button(st, r"^(verify|confirm|continue)")
        if b:
            await self.click_button(b)

    async def submit(self) -> str:
        """Click submit and watch what happens. Returns 'sent', 'errors' or 'unknown'."""
        before = await self.state()
        btn = self.find_button(before, SUBMIT_RE)
        if not btn:
            return "no_button"
        t0 = datetime.now(timezone.utc)
        inputs_before = {i["key"] for i in await self.code_inputs()}
        await self.shot("filled")
        self.log("Submitting")
        await self.click_button(btn)
        code_done = False
        for i in range(40):
            await self.page.wait_for_timeout(1000)
            try:
                st = await self.state()
            except PWError:
                continue
            new_success = re.search(SUCCESS_RE, st["text"], re.I) and not re.search(SUCCESS_RE, before["text"], re.I)
            url_success = st["url"] != before["url"] and re.search(r"/(thanks|thank-you|thankyou|confirmation|submitted|success|applied)", st["url"], re.I)
            if new_success or url_success:
                return "sent"
            if st["captchaChallenge"]:
                raise NeedsUser("A CAPTCHA appeared. Solve it in the browser window and press submit; I'll notice when it's sent.", wait=True)
            if not code_done and re.search(CODE_PROMPT_RE, st["text"], re.I) and not re.search(CODE_PROMPT_RE, before["text"], re.I):
                code_done = True
                await self.handle_code(t0, inputs_before)
                continue
            if i >= 4 and (st["errors"] or st["invalid"]):
                self.last_errors = st["errors"]
                return "errors"
        fields_left = [f for f in await self.extract() if f["kind"] != "file"]
        return "sent_unconfirmed" if not fields_left else "unknown"

    async def finish_single_page(self) -> tuple[str, str]:
        """Fill and submit a single- or multi-page form with Next/Submit buttons."""
        for step in range(12):
            st = await self.state()
            if st["captchaChallenge"]:
                raise NeedsUser("A CAPTCHA is blocking the form. Solve it in the browser window and submit.", wait=True)
            await self.fill_page()
            st = await self.state()
            if self.blank_required and self.s["blank_required"] == "ask":
                raise NeedsUser(f"{len(self.blank_required)} required questions need your answer",
                                list(self.blank_required.values()))
            if self.find_button(st, SUBMIT_RE):
                if self.dry_run or (self.s["submit_mode"] == "review" and not self.app.get("approved")):
                    await self.shot("ready")
                    return "ready", "Filled in and waiting for your OK" if not self.dry_run else "Dry run: filled, not submitted"
                result = await self.submit()
                if result == "errors":
                    self.log("The form flagged problems; fixing")
                    await self.fill_page(retry=True)
                    result = await self.submit()
                if result == "sent":
                    await self.shot("sent")
                    return "sent", "Submitted"
                if result == "sent_unconfirmed":
                    await self.shot("sent")
                    return "sent", "Submitted (no confirmation page seen)"
                if result == "errors":
                    errs = "; ".join(getattr(self, "last_errors", [])[:3])
                    raise NeedsUser(f"The form didn't accept it: {errs or 'validation errors'}",
                                    list(self.blank_required.values()), wait=True)
                raise NeedsUser("Submitted, but no confirmation appeared. Check the browser window.", wait=True)
            nxt = self.find_button(st, NEXT_RE)
            if not nxt:
                if re.search(SUCCESS_RE, st["text"], re.I):
                    return "sent", "Already submitted"
                raise NeedsUser("Couldn't find the submit button on this form.", wait=True)
            if self.blank_required and self.s["blank_required"] == "ask":
                raise NeedsUser("Required questions need your answer", list(self.blank_required.values()))
            self.log("Next page")
            url0 = st["url"]
            await self.click_button(nxt)
            await self.settle(1200)
            st2 = await self.state()
            if st2["errors"] and st2["url"] == url0:
                await self.fill_page(retry=True)
                await self.click_button(nxt)
                await self.settle(1200)
        raise NeedsUser("The form had too many pages.", wait=True)


async def robust_click(loc) -> None:
    try:
        await loc.scroll_into_view_if_needed(timeout=3000)
        await loc.click(timeout=4000)
    except (PWError, PWTimeout):
        try:
            await loc.click(force=True, timeout=3000)
        except (PWError, PWTimeout):
            await loc.evaluate("e => e.click()")


ATS_HOSTS = {"greenhouse": "greenhouse.io", "lever": "lever.co", "ashby": "ashbyhq.com", "workable": "workable.com"}


async def resolve_job_url(run: Run) -> None:
    job = run.job
    if job.get("ats") == "greenhouse" and job.get("ext_id") and not job.get("board"):
        import httpx
        from ..sources import resolve_greenhouse_board
        from ..sources.boards import UA
        async with httpx.AsyncClient(headers=UA, timeout=20, follow_redirects=True) as c:
            board = await resolve_greenhouse_board(c, job.get("url") or "", job.get("company") or "", job["ext_id"])
        if board:
            job["board"] = board
            job["apply_url"] = f"https://job-boards.greenhouse.io/{board}/jobs/{job['ext_id']}"
            db.x("UPDATE jobs SET board=?, apply_url=? WHERE id=?", (board, job["apply_url"], job["id"]))
            run.log(f"Found the Greenhouse application ({board})")


def on_ats_form(run: Run) -> bool:
    host = ATS_HOSTS.get(run.job.get("ats") or "")
    return not host or host in (run.page.url or "")


async def open_form(run: Run) -> None:
    await resolve_job_url(run)
    if not run.job.get("description"):
        from ..sources import hydrate
        await hydrate(run.job)     # written answers need the posting text
    page = run.page
    url = run.job.get("apply_url") or run.job.get("url")
    run.log("Opening the application")
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    await run.settle()
    job = run.job
    if job.get("ats") == "greenhouse" and job.get("board") and job.get("ext_id") and "greenhouse.io" not in page.url:
        embed = f"https://job-boards.greenhouse.io/embed/job_app?for={job['board']}&token={job['ext_id']}"
        run.log("Greenhouse sent us to the company site; opening its embedded application form")
        await page.goto(embed, wait_until="domcontentloaded", timeout=45000)
        await run.settle()
        job["apply_url"] = embed
        db.x("UPDATE jobs SET apply_url=? WHERE id=?", (embed, job["id"]))
    st = await run.state()
    if re.search(r"(job|position|posting) (is )?(no longer|not) (available|accepting|open)|page (you're looking for )?(could not be|can't be|cannot be) found|404", st["text"][:2000], re.I) \
            and not await run.extract():
        raise JobClosed("The posting is closed")
    for fr in page.frames[1:]:
        if re.search(r"greenhouse\.io/(embed/)?job_app|greenhouse\.io/embed|jobs\.lever\.co/.+/apply|jobs\.ashbyhq\.com/.+(embed|application)|apply\.workable\.com|myworkdayjobs\.com|myworkdaysite\.com", fr.url or ""):
            run.log("Opening the embedded application form directly")
            target_url = fr.url
            info = detect_ats(target_url)
            if info["ats"] in ("greenhouse", "lever", "ashby", "workable") and info["board"]:
                db.x("UPDATE jobs SET ats=?, board=?, apply_url=? WHERE id=?", (info["ats"], info["board"], target_url, run.job["id"]))
            await page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
            await run.settle()
            return
    fields = [f for f in await run.extract() if f["kind"] != "file" or f["question"]]
    if len(fields) >= 2 and on_ats_form(run):
        return
    btn = run.find_button(st, APPLY_LINK_RE)
    if not btn:
        link = page.get_by_role("link", name=re.compile(APPLY_LINK_RE, re.I)).first
        if not await link.count():
            return
        target = link
    else:
        target = page.locator(f'[data-aab="{btn["id"]}"]').first
    before = set(page.context.pages)
    await robust_click(target)
    await page.wait_for_timeout(2500)
    new = [p for p in page.context.pages if p not in before]
    if new:
        run.page = new[-1]
        await page.close()
    await run.settle()
    if not on_ats_form(run):
        for fr in run.page.frames[1:]:
            if ATS_HOSTS[run.job["ats"]] in (fr.url or ""):
                await run.page.goto(fr.url, wait_until="domcontentloaded", timeout=45000)
                await run.settle()
                return
        raise NeedsUser(f"Couldn't reach the {run.job['ats'].title()} application from the company's careers page. "
                        "Open it in the browser and apply there, or paste the direct application link.", wait=True)


class JobClosed(Exception):
    pass


async def generic_apply(run: Run) -> tuple[str, str]:
    await open_form(run)
    ats = detect_ats(run.page.url)["ats"]
    if ats == "workday" and run.job.get("ats") != "workday":
        from . import workday
        return await workday.apply(run, already_open=True)
    return await run.finish_single_page()


async def watch_for_user(page: Page, app_id: int, timeout_s: int = 900) -> None:
    """After handing over, watch the tab: if the person submits it, mark it sent."""
    start = asyncio.get_running_loop().time()
    baseline = ""
    try:
        baseline = (await page.evaluate("document.body.innerText"))[:5000]
    except PWError:
        pass
    while asyncio.get_running_loop().time() - start < timeout_s:
        await asyncio.sleep(4)
        if page.is_closed():
            return
        try:
            text = (await page.evaluate("document.body.innerText"))[:8000]
            url = page.url
        except PWError:
            continue
        if (re.search(SUCCESS_RE, text, re.I) and not re.search(SUCCESS_RE, baseline, re.I)) or \
                re.search(r"/(thanks|thank-you|confirmation|submitted)", url, re.I):
            db.app_update(app_id, status="sent", sent_at=db.now(), status_detail="You finished it in the browser")
            db.app_log(app_id, "Sent (finished by you in the browser)")
            row = db.q1("SELECT job_id FROM applications WHERE id=?", (app_id,))
            if row:
                db.x("UPDATE jobs SET status='applied' WHERE id=?", (row["job_id"],))
            await asyncio.sleep(3)
            await page.close()
            return
    try:
        await page.close()
    except PWError:
        pass


def _browser_closed(e: BaseException) -> bool:
    """Playwright's "page/context/browser has been closed" (TargetClosedError isn't exported publicly)."""
    return type(e).__name__ == "TargetClosedError" or "has been closed" in str(e)


async def run_application(app_id: int, dry_run: bool = False) -> str:
    app = db.q1("SELECT * FROM applications WHERE id=?", (app_id,))
    job = db.q1("SELECT * FROM jobs WHERE id=?", (app["job_id"],)) if app else None
    if not app or not job:
        return "missing"
    if prof.resume_path() is None:
        db.app_update(app_id, status="needs_you", status_detail="Upload your resume in Profile first")
        return "needs_you"
    db.app_update(app_id, status="applying", started_at=db.now(), attempts=(app["attempts"] or 0) + 1,
                  status_detail="Starting", pending_questions=[])
    try:
        page = await browser.new_page()
    except BrowserUnavailable as e:
        # Not this application's fault: put it back and let the caller pause the queue.
        db.app_update(app_id, status="queued", status_detail="Waiting: the browser couldn't start; will retry",
                      attempts=app["attempts"] or 0)
        db.event("error", f"Browser couldn't start: {e}")
        raise
    run = Run(app, job, page, dry_run=dry_run)
    keep_open = False
    status = "failed"
    try:
        if job["ats"] == "workday":
            from . import workday
            status, detail = await workday.apply(run)
        elif job["ats"] == "handshake":
            from . import handshake
            status, detail = await handshake.apply(run)
        else:
            status, detail = await generic_apply(run)
        fields = dict(status=status, status_detail=detail)
        if status == "sent":
            fields["sent_at"] = db.now()
            db.x("UPDATE jobs SET status='applied' WHERE id=?", (job["id"],))
        run.save()
        db.app_update(app_id, **fields)
        db.app_log(app_id, detail)
        db.event("apply", f"{job['company']}: {detail}")
    except NeedsUser as e:
        status = "needs_you"
        await run.shot("needs-you")
        run.save()
        keep_open = e.wait and not browser.headless and not dry_run
        detail = e.detail + (" (the tab is open)" if keep_open else "")
        db.app_update(app_id, status="needs_you", status_detail=detail, pending_questions=e.questions)
        db.app_log(app_id, detail)
        db.event("needs_you", f"{job['company']}: {e.detail}")
    except JobClosed as e:
        status = "skipped"
        db.app_update(app_id, status="skipped", status_detail=str(e))
        db.x("UPDATE jobs SET status='closed' WHERE id=?", (job["id"],))
    except Exception as e:  # noqa: BLE001 — one bad page must not kill the queue
        if _browser_closed(e):
            # Not this application's fault (AutoApply shutting down, or the window was closed). Never resend blindly
            # if it may already have gone out; otherwise put it back and let the caller pause the queue.
            step = (db.q1("SELECT status_detail FROM applications WHERE id=?", (app_id,)) or {}).get("status_detail") or ""
            if step.startswith(("Submitting", "Waiting for the verification code", "Got the code")):
                status = "needs_you"
                db.app_update(app_id, status="needs_you", status_detail="The browser closed while submitting this one. "
                              "Check the employer site or your email before retrying.")
            else:
                status = "queued"
                db.app_update(app_id, status="queued", status_detail="Waiting: the browser closed; will retry",
                              attempts=app["attempts"] or 0)
            db.event("error", f"Browser closed while applying to {job['company']}")
            raise BrowserUnavailable("The browser window closed") from e
        log.exception("application %s failed", app_id)
        await run.shot("error")
        run.save()
        db.app_update(app_id, status="failed", status_detail=f"{type(e).__name__}: {str(e)[:200]}")
        db.app_log(app_id, f"Failed: {e}")
    finally:
        if keep_open:
            asyncio.create_task(watch_for_user(run.page, app_id))
        elif not dry_run or status != "ready":
            try:
                await run.page.close()
            except PWError:
                pass
    return status
