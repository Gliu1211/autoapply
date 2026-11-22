"""Handshake runs inside your signed-in browser profile: search jobs there, and apply in-app or follow the external link."""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import quote, urlparse

from playwright.async_api import Error as PWError
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PWTimeout

from .. import db
from ..util import detect_ats, html_to_text
from .browser import browser
from .dom import SUCCESS_RE
from .engine import JobClosed, NeedsUser, Run, generic_apply, robust_click

log = logging.getLogger("autoapply.handshake")
APPLY_BUTTON_RE = re.compile(r"^\s*(quick apply|easy apply|apply now|apply externally|apply on (the )?(company|employer)|apply)\b", re.I)
STEP2_RE = re.compile(r"external application|continue to (the )?(employer|application|company)|apply on (the )?(company|employer)|go to (the )?(application|website)", re.I)
APPLIED_RE =r"you('ve| have)? applied|applied on [A-Z][a-z]{2,8} \d|application (was )?(sent|submitted)"


class NeedsLogin(Exception):
    pass


async def _new_tabs(page: Page, before: set, timeout_ms: int) -> list:
    """Tabs opened since `before`; polls because employer sites can take several seconds to open."""
    waited = 0
    while waited < timeout_ms:
        await page.wait_for_timeout(500)
        waited += 500
        new = [p for p in page.context.pages if p not in before]
        if new:
            return new
    return []


def _logged_out(page: Page) -> bool:
    u = page.url.lower()
    return "/login" in u or "/sign_in" in u or "sso" in urlparse(u).path


def _walk(obj, found: dict) -> None:
    """Find job-shaped objects in Handshake's API responses."""
    if isinstance(obj, dict):
        jid = obj.get("id")
        title = obj.get("title") or obj.get("job_title")
        if not isinstance(title, str):
            title = None
        emp = obj.get("employer") or obj.get("employerName") or obj.get("employer_name") or obj.get("company")
        if jid and title and emp:
            emp_name = emp.get("name") if isinstance(emp, dict) else emp
            locs = obj.get("locations") or obj.get("location") or []
            if isinstance(locs, list):
                loc = " · ".join((l.get("name") or l.get("city") or "") if isinstance(l, dict) else str(l) for l in locs)
            elif isinstance(locs, dict):
                loc = locs.get("name") or ""
            else:
                loc = str(locs)
            found[str(jid)] = {"id": str(jid), "title": title, "company": emp_name or "", "location": loc,
                               "remote": bool(obj.get("remote") or obj.get("isRemote")),
                               "posted": obj.get("createdAt") or obj.get("created_at") or obj.get("apply_start")}
        for v in obj.values():
            _walk(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, found)


async def discover(queries: list[str], conf: dict) -> list[dict]:
    base = (conf.get("base_url") or "https://app.joinhandshake.com").rstrip("/")
    page = await browser.new_page()
    found: dict[str, dict] = {}

    async def on_response(resp) -> None:
        try:
            if "json" not in (resp.headers.get("content-type") or ""):
                return
            if not re.search(r"graphql|job|posting|search", resp.url, re.I):
                return
            _walk(await resp.json(), found)
        except (PWError, ValueError, json.JSONDecodeError):
            pass

    page.on("response", on_response)
    out: list[dict] = []
    host = urlparse(base).netloc
    try:
        for q in queries[:6] or ["software engineer"]:
            await page.goto(f"{base}/job-search?query={quote(q)}&per_page={conf.get('max_per_query', 40)}",
                            wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(4000)
            if _logged_out(page):
                raise NeedsLogin()
            # Fallback: links on the page.
            links = await page.evaluate("""() => [...document.querySelectorAll('a[href]')]
                .map(a => ({href: a.href, text: (a.innerText || a.getAttribute('aria-label') || '').trim()}))
                .filter(x => /\\/(jobs|job-search)\\/\\d+/.test(x.href))""")
            for l in links:
                m = re.search(r"/(?:jobs|job-search)/(\d+)", l["href"])
                if m and m.group(1) not in found:
                    lines = [x.strip() for x in l["text"].split("\n") if x.strip()]
                    if lines:
                        found[m.group(1)] = {"id": m.group(1), "title": lines[0], "company": lines[1] if len(lines) > 1 else "",
                                             "location": next((x for x in lines[2:] if re.search(r",\s*[A-Z]{2}\b|remote", x, re.I)), ""),
                                             "remote": any("remote" in x.lower() for x in lines), "posted": None}
        from ..util import parse_dt
        for j in found.values():
            out.append({"uid": f"handshake:{host}:{j['id']}", "source": "handshake", "ats": "handshake", "board": host,
                        "ext_id": j["id"], "company": j["company"], "title": j["title"], "location": j["location"],
                        "remote": int(bool(j["remote"])), "url": f"{base}/jobs/{j['id']}", "apply_url": f"{base}/jobs/{j['id']}",
                        "description": "", "posted_at": parse_dt(j.get("posted"))})
    finally:
        await page.close()
    return out


async def detail(job: dict) -> dict:
    """Read the posting text (descriptions aren't in search results)."""
    page = await browser.new_page()
    try:
        await page.goto(job["url"], wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(3000)
        if _logged_out(page):
            raise NeedsLogin()
        text = await page.evaluate("(document.querySelector('main') || document.body).innerText")
        desc = html_to_text(text)[:12000]
        if desc:
            job["description"] = desc
            db.x("UPDATE jobs SET description=? WHERE id=?", (desc, job["id"]))
    finally:
        await page.close()
    return job


async def apply(run: Run) -> tuple[str, str]:
    page = run.page
    run.log("Opening the Handshake posting")
    await page.goto(run.job["url"], wait_until="domcontentloaded", timeout=45000)
    await run.settle(2500)
    if _logged_out(page):
        raise NeedsUser("Sign into Handshake in the browser window (Settings > Accounts), then retry.", wait=True)
    text = await page.evaluate("document.body.innerText")
    if re.search(APPLIED_RE, text[:4000], re.I):
        return "sent", "Already applied on Handshake"
    if re.search(r"no longer accepting|applications? (are )?closed|expired", text[:4000], re.I):
        raise JobClosed("The posting is closed")

    target = None
    for _ in range(3):           # the job panel can render a moment after the page
        btn = page.get_by_role("button", name=APPLY_BUTTON_RE).first
        link = page.get_by_role("link", name=APPLY_BUTTON_RE).first
        target = btn if await btn.count() else (link if await link.count() else None)
        if target is not None:
            break
        await page.wait_for_timeout(2000)
    # After a first visit to an external posting, Handshake asks "Did you apply to this job?" and disables
    # "Apply externally"; the way back to the employer's form is its "View application" link.
    view = page.get_by_role("link", name=re.compile(r"^\s*view application", re.I)).first
    if (target is None or await target.is_disabled()) and await view.count():
        target = view
    if target is None:
        raise NeedsUser("Couldn't find Handshake's Apply button.", wait=True)
    label = (await target.inner_text()).strip().lower()
    before = set(page.context.pages)
    await robust_click(target)
    external = bool(re.search(r"extern|company|employer|view application", label))
    new = await _new_tabs(page, before, 15000 if external else 4000)

    # External postings: Handshake first shows "Step 1: Review Employer Instructions", then a
    # "Step 2: External Application" button that opens the employer's site in a new tab.
    step2 = None
    if not new:
        for role in ("button", "link"):
            loc = page.get_by_role(role, name=STEP2_RE).first
            if await loc.count():
                step2 = loc
                break
    if step2 is not None:
        run.log("Following Handshake to the employer's application")
        before = set(page.context.pages)
        await robust_click(step2)
        new = await _new_tabs(page, before, 15000)

    if new or step2 is not None or external:
        ext = new[-1] if new else page
        await ext.wait_for_load_state("domcontentloaded")
        try:
            await ext.wait_for_load_state("networkidle", timeout=8000)   # let redirect links land
        except PWTimeout:
            pass
        ext_url = ext.url
        if "joinhandshake.com" in urlparse(ext_url).netloc:
            raise NeedsUser("Handshake didn't open the employer's application page. Click through it in the browser window.", wait=True)
        info = detect_ats(ext_url)
        run.log(f"Handshake links out to {info['ats']}: {urlparse(ext_url).netloc}")
        db.x("UPDATE jobs SET apply_url=? WHERE id=?", (ext_url, run.job["id"]))
        db.app_update(run.app_id, ats=info["ats"])
        run.job["apply_url"] = ext_url
        if new:
            await page.close()
            run.page = ext
        from ..matching import site_blocked
        blocked = site_blocked(info["ats"])
        if blocked:
            raise NeedsUser(f"This Handshake posting sends you to {urlparse(ext_url).netloc}. {blocked}. "
                            "Open it in your browser and use the AutoApply extension there.")
        if info["ats"] == "workday":
            from . import workday
            return await workday.apply(run, already_open=True)
        return await generic_apply(run)

    # In-app application modal: resume picker, optional cover letter/transcript, employer questions.
    for _ in range(4):
        await run.fill_page()
        st = await run.state()
        sub = run.find_button(st, r"^(submit application|submit|apply|send application)$")
        if sub:
            if run.dry_run or (run.s["submit_mode"] == "review" and not run.app.get("approved")):
                await run.shot("ready")
                return "ready", "Filled in and waiting for your OK"
            await run.shot("filled")
            pre = await page.evaluate("document.body.innerText")
            await run.click_button(sub)
            await page.wait_for_timeout(3500)
            t = await page.evaluate("document.body.innerText")
            done = re.compile(f"{SUCCESS_RE}|{APPLIED_RE}", re.I)
            if done.search(t) and not done.search(pre):
                await run.shot("sent")
                return "sent", "Submitted on Handshake"
            st = await run.state()
            if st["errors"]:
                if any(re.search(r"transcript", e, re.I) for e in st["errors"]):
                    raise NeedsUser("This employer wants a transcript. Upload one to your Handshake documents, then retry.", wait=True)
                continue
        nxt = run.find_button(st, r"^(next|continue)$")
        if nxt:
            await run.click_button(nxt)
            await page.wait_for_timeout(1500)
            continue
        break
    raise NeedsUser("Handshake's application didn't go through. Check the browser window.", list(run.blank_required.values()), wait=True)
