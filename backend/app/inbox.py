"""Gmail over IMAP: verification codes, account-verification links, and employer replies."""
from __future__ import annotations

import asyncio
import email
import imaplib
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

from . import db, llm
from . import settings as cfg
from .llm import S, arr, obj
from .util import html_to_text

log = logging.getLogger("autoapply.inbox")
ATS_SENDERS = ("greenhouse", "lever.co", "ashbyhq", "myworkday", "workday", "workablemail", "workable",
               "smartrecruiters", "icims", "joinhandshake", "jobvite", "taleo", "oraclecloud", "eightfold",
               "successfactors", "bamboohr", "rippling", "hire.", "talent", "recruit", "careers")


def configured() -> bool:
    """Credentials are saved AND Google hasn't rejected them. Use this before any Gmail work."""
    st = status()
    return st["configured"] and st["ok"] is not False


def _key(addr: str, pw: str) -> str:
    import hashlib
    return hashlib.sha256(f"{addr}\0{pw}".encode()).hexdigest()[:16]


def friendly_error(e: Exception | str) -> str:
    s = str(e)
    if re.search(r"application-specific password|app password", s, re.I):
        return ("Google wants an app password here, not your normal Gmail password. Create one at "
                "myaccount.google.com/apppasswords (needs 2-Step Verification) and paste the 16 characters.")
    if re.search(r"AUTHENTICATIONFAILED|invalid credentials|username and password not accepted", s, re.I):
        return "Gmail rejected that address and app password. Check both and try again."
    if re.search(r"IMAP access is disabled|web login required", s, re.I):
        return "Turn on IMAP in Gmail settings (See all settings > Forwarding and POP/IMAP), then try again."
    return f"Couldn't sign in to Gmail: {s}"


def status() -> dict:
    """{configured, ok (True/False/None=not checked yet), error, address}"""
    addr, pw = cfg.gmail_creds()
    if not (addr and pw):
        return {"configured": False, "ok": None, "error": "", "address": addr}
    st = db.kv_get("gmail_status") or {}
    if st.get("key") != _key(addr, pw):
        return {"configured": True, "ok": None, "error": "", "address": addr}
    return {"configured": True, "ok": st.get("ok"), "error": st.get("error", ""), "address": addr}


def _record(addr: str, pw: str, ok: bool, error: str = "") -> None:
    db.kv_set("gmail_status", {"key": _key(addr, pw), "ok": ok, "error": error, "at": db.now()})


def _imap(addr: str | None = None, pw: str | None = None) -> imaplib.IMAP4_SSL:
    if addr is None:
        addr, pw = cfg.gmail_creds()
    m = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=30)
    try:
        m.login(addr, pw)
    except imaplib.IMAP4.error as e:
        # Remember the rejection so we stop retrying a password Google won't accept.
        _record(addr, pw, False, friendly_error(e))
        raise
    _record(addr, pw, True)
    return m


def test_login(addr: str | None = None, pw: str | None = None) -> str | None:
    """None if the login works, else a plain-language reason."""
    try:
        m = _imap(addr, pw)
        m.logout()
        return None
    except imaplib.IMAP4.error as e:
        return friendly_error(e)
    except OSError as e:
        return f"Couldn't reach Gmail: {e}"


def _decode(v: str | None) -> str:
    try:
        return str(make_header(decode_header(v or "")))
    except (ValueError, LookupError):
        return v or ""


def _parse(uid: str, raw: bytes) -> dict:
    msg = email.message_from_bytes(raw)
    text, html_part = "", ""
    for part in msg.walk():
        if part.get_content_maintype() == "multipart" or part.get("Content-Disposition", "").startswith("attachment"):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        s = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if part.get_content_type() == "text/plain" and not text:
            text = s
        elif part.get_content_type() == "text/html" and not html_part:
            html_part = s
    try:
        dt = parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        dt = datetime.now(timezone.utc)
    links = re.findall(r'href=["\'](https?://[^"\']+)', html_part) + re.findall(r"https?://[^\s<>\"')]+", text)
    return {"uid": uid, "msg_id": msg.get("Message-ID") or f"uid-{uid}", "from": _decode(msg.get("From")),
            "subject": _decode(msg.get("Subject")), "date": dt,
            "body": (text or html_to_text(html_part)).strip(), "links": [l.replace("&amp;", "&") for l in links]}


def recent(days: int = 1, limit: int = 40, query: str = "") -> list[dict]:
    m = _imap()
    try:
        m.select('"[Gmail]/All Mail"', readonly=True)
        raw_q = f"newer_than:{days}d {query}".strip()
        typ, data = m.uid("SEARCH", None, "X-GM-RAW", f'"{raw_q}"')
        uids = (data[0] or b"").split()[-limit:]
        out = []
        for uid in reversed(uids):
            typ, msg_data = m.uid("FETCH", uid, "(BODY.PEEK[])")
            if typ == "OK" and msg_data and isinstance(msg_data[0], tuple):
                out.append(_parse(uid.decode(), msg_data[0][1]))
        return out
    finally:
        try:
            m.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


COMMON = {"verification", "security", "password", "application", "greenhouse", "workday", "thank", "please",
          "submitted", "confirmation", "candidate", "account", "continue", "required", "expires", "minutes",
          "verify", "passcode", "recruiting", "careers", "privacy", "hello", "regards"}
_ANCHOR = re.compile(r"\b(code|passcode|otp)\b", re.I)
_TOKEN = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9]{4,10})(?![A-Za-z0-9])")
_WORDLIKE = re.compile(r"^([A-Z]?[a-z]+)$|^([A-Z][a-z]+){2,}$|^Mc[A-Z][a-z]+$")   # hello, Hello, DoorDash, McKinsey
_used_codes: set[str] = set()   # message ids whose code was already handed out, so one email never fills two forms


def _norm_company(name: str) -> str:
    name = re.sub(r"[,.]?\s*\b(inc|llc|ltd|corp|corporation|co|company|plc|gmbh)\b\.?$", "", name.strip(), flags=re.I)
    return re.sub(r"\s+", " ", name).strip().lower()


def extract_code(text: str, length: int | None = None, avoid: str = "") -> str | None:
    """The verification code in an email. `length`, when the form tells us, is the exact number of characters.
    `avoid` is text whose words are never the code (the company name)."""
    text = re.sub(r"https?://\S+|www\.\S+|\S+@\S+\.\w+", " ", text)   # ids inside links and addresses aren't codes
    avoid_words = {w.lower() for w in re.findall(r"[A-Za-z0-9]+", avoid)}

    def ok(tok: str, standalone: bool) -> bool:
        if length and len(tok) != length:
            return False
        if not length and len(tok) < 6:
            return False
        if tok.lower() in COMMON or tok.lower() in avoid_words:
            return False
        if tok.isdigit() or (any(c.isdigit() for c in tok) and any(c.isalpha() for c in tok)):
            return True
        # letters only: a code on a line of its own may look like anything; inline, skip ordinary words and names
        return standalone or not _WORDLIKE.match(tok)

    # "123456 is your verification code"
    for m in re.finditer(r"(?<![A-Za-z0-9])([A-Za-z0-9]{4,10})\s+is\s+your\b[^\n]{0,40}?\b(code|passcode|otp)\b", text, re.I):
        if ok(m.group(1), False):
            return m.group(1)
    best: str | None = None
    for m in _ANCHOR.finditer(text):
        window = text[m.end(): m.end() + 400]
        for line in window.splitlines():   # a code shown on its own line is the strongest sign
            line = line.strip().strip(".:")
            if _TOKEN.fullmatch(line) and ok(line, True):
                return line
        if best is None:
            best = next((tok for tok in _TOKEN.findall(window) if ok(tok, False)), None)
    return best


async def wait_for_code(since: datetime, company: str = "", hints: list[str] | None = None, length: int | None = None,
                        timeout: int = 180) -> str | None:
    """Poll Gmail for a verification code sent after `since`. Emails naming `company` win; otherwise one matching
    `hints`. Each email's code is handed out once."""
    if not configured():
        return None
    hints = [h.lower() for h in (hints or []) if h]
    who = _norm_company(company)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            msgs = await asyncio.to_thread(recent, 1, 15)
        except (imaplib.IMAP4.error, OSError) as e:
            log.warning("imap: %s", e)
            msgs = []
        fresh = [m for m in msgs if m["date"] >= since - timedelta(seconds=60) and m["msg_id"] not in _used_codes]
        ours, other = [], []
        for msg in fresh:
            hay = f"{msg['from']} {msg['subject']} {msg['body'][:3000]}".lower()
            if not _ANCHOR.search(hay):
                continue
            if who and who in hay:
                ours.append(msg)
            elif not hints or any(h in hay for h in hints):
                other.append(msg)
        for msg in ours + other:
            code = extract_code(msg["body"], length, avoid=company) or extract_code(msg["subject"], length, avoid=company)
            if code:
                _used_codes.add(msg["msg_id"])
                log.info("verification code found in %r (%s)", msg["subject"][:80], "company match" if msg in ours else "generic match")
                return code
        await asyncio.sleep(6)
    return None


async def wait_for_link(since: datetime, hints: list[str], keywords=("verify", "activate", "confirm", "validate"),
                        timeout: int = 180) -> str | None:
    if not configured():
        return None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            msgs = await asyncio.to_thread(recent, 1, 15)
        except (imaplib.IMAP4.error, OSError):
            msgs = []
        for msg in msgs:
            if msg["date"] < since - timedelta(minutes=2):
                continue
            hay = f"{msg['from']} {msg['subject']}".lower()
            if hints and not any(h and h.lower() in (hay + " " + " ".join(msg["links"])).lower() for h in hints):
                continue
            for link in msg["links"]:
                if any(k in link.lower() for k in keywords) and not re.search(r"unsubscribe|privacy|terms", link, re.I):
                    return link
        await asyncio.sleep(6)
    return None


# ---------------- replies ----------------

CLASSIFY_SCHEMA = obj({"items": arr(obj({"uid": S, "category": S, "company": S, "summary": S}))})
CLASSIFY_SYSTEM = """Classify emails a job seeker received. category is one of:
receipt (automated "we received your application"), rejection, interview (invitation or scheduling),
assessment (coding test / take-home / online assessment), offer, action_needed (employer asks for something),
other (job-related, none of the above), not_job (unrelated).
company: the employer's name as written, or "". summary: max 18 words, what the email asks or says."""

RANK = {"receipt": 1, "action_needed": 2, "assessment": 3, "rejection": 4, "interview": 5, "offer": 6}


def _regex_category(m: dict) -> str:
    t = f"{m['subject']} {m['body'][:2500]}".lower()
    if re.search(r"unfortunately|not (be )?moving forward|other candidates|not selected|decided to (pursue|move)", t):
        return "rejection"
    if re.search(r"offer letter|pleased to offer|extend an offer", t):
        return "offer"
    if re.search(r"hackerrank|codesignal|codility|assessment|coding challenge|take-?home|online test", t):
        return "assessment"
    if re.search(r"interview|schedule a (call|time|chat)|your availability|calendly|phone screen", t):
        return "interview"
    if re.search(r"(received|receipt of) your application|thank(s| you) for (applying|your application)", t):
        return "receipt"
    return "other"


async def classify(msgs: list[dict]) -> dict[str, dict]:
    """Claude sorts emails (15 per request); falls back to keyword rules for anything it didn't cover."""
    cats: dict[str, dict] = {}
    if llm.available():
        for i in range(0, len(msgs), 15):
            batch = msgs[i:i + 15]
            content = "\n\n".join(f"<email uid=\"{m['uid']}\">\nFrom: {m['from']}\nSubject: {m['subject']}\n{m['body'][:1800]}\n</email>"
                                  for m in batch)
            try:
                model = llm.model_for("sorting")
                data = await llm.ajson_call(CLASSIFY_SYSTEM, content, CLASSIFY_SCHEMA, effort=llm.scoring_effort(model),
                                            model=model, purpose="email")
                cats.update({x["uid"]: x for x in data["items"] if x.get("category") in CATEGORIES})
            except llm.LLMError as e:
                log.warning("classify failed: %s", e)
                break
    for m in msgs:
        cats.setdefault(m["uid"], {"uid": m["uid"], "category": _regex_category(m), "company": "", "summary": m["subject"][:120]})
    return cats


CATEGORIES = {"receipt", "rejection", "interview", "assessment", "offer", "action_needed", "other", "not_job"}


async def sync(days: int = 30) -> int:
    """Pull recent job-related email, classify it, and attach it to applications."""
    if not configured():
        return 0
    from .matching import norm_company
    apps = db.q("""SELECT a.id, a.response, j.company FROM applications a JOIN jobs j ON j.id=a.job_id
                   WHERE a.status='sent' ORDER BY a.sent_at DESC""")
    by_company: dict[str, dict] = {}
    for a in apps:
        k = norm_company(a["company"])
        if len(k) >= 3 and k not in by_company:
            by_company[k] = a
    last = db.kv_get("inbox_synced_at")
    span = days if not last else max(1, min(days, int((datetime.now(timezone.utc) - datetime.fromisoformat(last)).days) + 1))
    msgs = await asyncio.to_thread(recent, span, 150, "-category:promotions -category:social")
    fresh = []
    for m in msgs:
        if db.q1("SELECT id FROM messages WHERE msg_id=?", (m["msg_id"],)):
            continue
        hay = f"{m['from']} {m['subject']} {m['body'][:2000]}".lower()
        flat = re.sub(r"[^a-z0-9]", "", hay)
        app = next((a for k, a in by_company.items() if k in flat), None)
        if app or any(s in m["from"].lower() for s in ATS_SENDERS):
            m["app"] = app
            fresh.append(m)
    if not fresh:
        db.kv_set("inbox_synced_at", db.now())
        return 0
    cats = await classify(fresh)
    n = 0
    for m in fresh:
        c = cats[m["uid"]]
        if c["category"] == "not_job":
            continue
        app = m.get("app")
        if not app and c.get("company"):
            app = by_company.get(norm_company(c["company"]))
        db.x("""INSERT OR IGNORE INTO messages(msg_id, application_id, company, sender, subject, body, received_at, category, summary)
                VALUES(?,?,?,?,?,?,?,?,?)""",
             (m["msg_id"], app["id"] if app else None, (app or {}).get("company") or c.get("company"), m["from"],
              m["subject"], m["body"][:20000], m["date"].isoformat(timespec="seconds"), c["category"], c["summary"]))
        n += 1
        if app and RANK.get(c["category"], 0) >= RANK.get(app.get("response") or "", 0):
            db.app_update(app["id"], response=c["category"], response_at=m["date"].isoformat(timespec="seconds"))
            app["response"] = c["category"]
    db.kv_set("inbox_synced_at", db.now())
    if n:
        db.event("inbox", f"{n} new employer emails")
    return n
