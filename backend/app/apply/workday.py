"""Workday: candidate account per company (created or signed into automatically), then the multi-step wizard."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from playwright.async_api import Error as PWError
from playwright.async_api import TimeoutError as PWTimeout

from .. import db, inbox
from .. import settings as cfg
from .dom import SUCCESS_RE
from .engine import JobClosed, NeedsUser, Run, robust_click

SEL = {
    "apply": '[data-automation-id="adventureButton"], a[data-automation-id="applyButton"]',
    "use_last": '[data-automation-id="useMyLastApplication"]',
    "autofill": '[data-automation-id="autofillWithResume"]',
    "manual": '[data-automation-id="applyManually"]',
    "email": 'input[data-automation-id="email"]:visible',
    "password": 'input[data-automation-id="password"]:visible',
    "verify_pw": 'input[data-automation-id="verifyPassword"]:visible',
    "create_check": 'input[data-automation-id="createAccountCheckbox"]:visible',
    "create_submit": '[data-automation-id="createAccountSubmitButton"]:visible',
    "signin_submit": '[data-automation-id="signInSubmitButton"]:visible',
    "create_link": '[data-automation-id="createAccountLink"]:visible',
    "signin_link": '[data-automation-id="signInLink"]:visible',
    "next": '[data-automation-id="pageFooterNextButton"]:visible, [data-automation-id="bottom-navigation-next-button"]:visible',
    "submit": '[data-automation-id="pageFooterSubmitButton"]:visible',
    "active_step": '[data-automation-id="progressBarActiveStep"]',
    "errors": '[data-automation-id="errorMessage"]:visible, [data-automation-id="errorBanner"]:visible, [data-automation-id="inputAlert"]:visible',
    "file": 'input[data-automation-id="file-upload-input-ref"], input[type=file]',
}


async def _visible(page, sel: str) -> bool:
    try:
        return await page.locator(sel).first.is_visible(timeout=800)
    except (PWError, PWTimeout):
        return False


async def _click_with_filter(page, sel: str) -> None:
    """Workday lays a transparent click_filter div over its buttons."""
    btn = page.locator(sel).first
    filt = btn.locator("xpath=..").locator('[data-automation-id="click_filter"]')
    try:
        if await filt.count():
            await filt.first.click(timeout=4000)
            return
    except (PWError, PWTimeout):
        pass
    await robust_click(btn)


async def _text(page) -> str:
    try:
        return await page.evaluate("document.body.innerText")
    except PWError:
        return ""


async def ensure_signed_in(run: Run) -> None:
    page = run.page
    host = urlparse(page.url).netloc
    email = run.p["contact"]["email"]
    pw = cfg.account_password()
    if not email:
        raise NeedsUser("Add your email in Profile so I can create Workday accounts.")
    for attempt in range(5):
        await page.wait_for_timeout(1500)
        if not await _visible(page, SEL["password"]):
            return
        acct = db.q1("SELECT * FROM accounts WHERE host=?", (host,))
        if run.dry_run:
            raise NeedsUser("Dry run: reached the Workday sign-in / create-account page; stopping before creating an account.")
        if await _visible(page, SEL["verify_pw"]):
            if acct:
                if await _visible(page, SEL["signin_link"]):
                    await robust_click(page.locator(SEL["signin_link"]).first)
                    continue
            run.log(f"Creating a Workday account at {host}")
            await page.locator(SEL["email"]).first.fill(email)
            await page.locator(SEL["password"]).first.fill(pw)
            await page.locator(SEL["verify_pw"]).first.fill(pw)
            if await _visible(page, SEL["create_check"]):
                await page.locator(SEL["create_check"]).first.check(force=True)
            t0 = datetime.now(timezone.utc)
            await _click_with_filter(page, SEL["create_submit"])
            await page.wait_for_timeout(4000)
            text = (await _text(page)).lower()
            if re.search(r"already (exists|in use|registered)|account with this email", text):
                db.x("INSERT OR REPLACE INTO accounts(host,email,password,created_at,verified) VALUES(?,?,?,?,0)",
                     (host, email, pw, db.now()))
                if await _visible(page, SEL["signin_link"]):
                    await robust_click(page.locator(SEL["signin_link"]).first)
                continue
            db.x("INSERT OR REPLACE INTO accounts(host,email,password,created_at,verified) VALUES(?,?,?,?,0)",
                 (host, email, pw, db.now()))
            if re.search(r"verify|verification|confirm your (email|account)|activate", text):
                await _verify_email(run, host, t0)
            continue
        # Sign-in form
        if not acct and await _visible(page, SEL["create_link"]):
            await robust_click(page.locator(SEL["create_link"]).first)
            continue
        run.log(f"Signing into Workday at {host}")
        await page.locator(SEL["email"]).first.fill(email)
        await page.locator(SEL["password"]).first.fill(acct["password"] if acct else pw)
        t0 = datetime.now(timezone.utc)
        await _click_with_filter(page, SEL["signin_submit"])
        await page.wait_for_timeout(4000)
        text = (await _text(page)).lower()
        if re.search(r"(verify|activate) your (account|email)|account (is )?not (yet )?(verified|activated)", text):
            await _verify_email(run, host, t0)
            continue
        if await _visible(page, SEL["password"]) and re.search(r"wrong|invalid|incorrect|locked|doesn't match", text):
            raise NeedsUser(f"Workday sign-in at {host} failed with the saved password. Sign in once in the browser "
                            f"window (or reset that account's password to the one shown in Settings).", wait=True)
    raise NeedsUser(f"Couldn't get past the Workday sign-in at {host}.", wait=True)


async def _verify_email(run: Run, host: str, since: datetime) -> None:
    run.log("Waiting for Workday's account verification email")
    tenant = host.split(".")[0]
    link = await inbox.wait_for_link(since, [tenant, "workday", "myworkday"], timeout=200)
    if not link:
        raise NeedsUser("Workday sent a verification email. Connect Gmail in Settings, or click the link in that email, then retry.")
    page = run.page
    back = page.url
    await page.goto(link, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)
    db.x("UPDATE accounts SET verified=1 WHERE host=?", (host,))
    await page.goto(back, wait_until="domcontentloaded")
    await run.settle()


async def _step_name(page) -> str:
    try:
        loc = page.locator(SEL["active_step"]).first
        if await loc.count():
            return re.sub(r"\s+", " ", await loc.inner_text(timeout=1500)).strip()
    except (PWError, PWTimeout):
        pass
    try:
        return (await page.locator("h2:visible").first.inner_text(timeout=1500)).strip()
    except (PWError, PWTimeout):
        return ""


async def apply(run: Run, already_open: bool = False) -> tuple[str, str]:
    page = run.page
    if not already_open:
        url = re.sub(r"/(apply|applyManually|autofillWithResume)(/.*)?$", "", run.job["url"] or run.job["apply_url"])
        run.log("Opening the Workday posting")
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    await run.settle(2000)
    # Workday renders the posting with JavaScript; wait for its Apply button (or a form) before deciding anything.
    try:
        await page.locator(f'{SEL["apply"]}, {SEL["use_last"]}, {SEL["autofill"]}, {SEL["manual"]}, {SEL["email"]}, {SEL["next"]}').first.wait_for(
            state="visible", timeout=15000)
    except (PWError, PWTimeout):
        pass
    text = await _text(page)
    if re.search(r"you('ve| have) already applied", text, re.I):
        return "sent", "Already applied earlier"
    if re.search(r"no longer (accepting|available)|job (posting )?(is )?(closed|filled)|page (isn't|is not) available", text, re.I):
        raise JobClosed("The posting is closed")

    if await _visible(page, SEL["apply"]):
        await robust_click(page.locator(SEL["apply"]).first)
        await page.wait_for_timeout(2000)
    has_acct = bool(db.q1("SELECT 1 FROM accounts WHERE host=?", (urlparse(page.url).netloc,)))
    for key in (("use_last", "autofill", "manual") if has_acct else ("autofill", "manual", "use_last")):
        if await _visible(page, SEL[key]):
            run.log({"use_last": "Using my last Workday application", "autofill": "Autofill with resume",
                     "manual": "Applying manually"}[key])
            await robust_click(page.locator(SEL[key]).first)
            await page.wait_for_timeout(2500)
            break

    await ensure_signed_in(run)

    last_step, same_step_tries = "", 0
    for step in range(18):
        await run.settle(1500)
        text = await _text(page)
        if re.search(SUCCESS_RE, text, re.I) or re.search(r"application (was )?submitted|congratulations", text, re.I):
            await run.shot("sent")
            return "sent", "Submitted"
        if await _visible(page, SEL["password"]):
            await ensure_signed_in(run)
            continue
        st = await run.state()
        if st["captchaChallenge"]:
            raise NeedsUser("A CAPTCHA is blocking Workday. Solve it in the browser window.", wait=True)
        name = await _step_name(page)
        if name == last_step:
            same_step_tries += 1
            if same_step_tries >= 2:
                errs = "; ".join(st["errors"][:3]) or "it won't move to the next page"
                raise NeedsUser(f"Workday step '{name}': {errs}", list(run.blank_required.values()), wait=True)
        else:
            same_step_tries = 0
        last_step = name
        run.log(f"Workday: {name or 'next step'}")

        on_review = "review" in name.lower() or await _visible(page, SEL["submit"])
        if on_review:
            if run.dry_run or (run.s["submit_mode"] == "review" and not run.app.get("approved")):
                await run.shot("ready")
                return "ready", "Filled in and waiting for your OK" if not run.dry_run else "Dry run: filled, not submitted"
            await run.shot("filled")
            run.log("Submitting")
            if await _visible(page, SEL["submit"]):
                await _click_with_filter(page, SEL["submit"])
            else:
                b = run.find_button(st, r"^submit$")
                if not b:
                    raise NeedsUser("Couldn't find Workday's Submit button.", wait=True)
                await run.click_button(b)
            for _ in range(20):
                await page.wait_for_timeout(1000)
                t = await _text(page)
                if re.search(SUCCESS_RE, t, re.I) or re.search(r"application (was )?submitted|congratulations", t, re.I):
                    await run.shot("sent")
                    return "sent", "Submitted"
            raise NeedsUser("Pressed Submit on Workday but saw no confirmation.", wait=True)

        await run.fill_page(retry=same_step_tries > 0)
        if run.blank_required and run.s["blank_required"] == "ask":
            raise NeedsUser(f"{len(run.blank_required)} required questions need your answer", list(run.blank_required.values()))
        if await _visible(page, SEL["next"]):
            await _click_with_filter(page, SEL["next"])
        else:
            b = run.find_button(await run.state(), r"^(next|continue|save and continue)$")
            if not b:
                raise NeedsUser(f"No Next button on Workday step '{name}'.", wait=True)
            await run.click_button(b)
        await page.wait_for_timeout(2500)
    raise NeedsUser("Workday had more steps than expected.", wait=True)
