"""Discovery: pull jobs from every enabled source into the jobs table."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from .. import db
from .. import profile as prof
from .. import settings as cfg
from ..util import detect_ats, html_to_text, parse_dt, parse_workday, search_queries
from . import boards

log = logging.getLogger("autoapply.sources")


def upsert_job(j: dict) -> bool:
    """Insert a job; if it exists, fill in a missing description. Returns True if new."""
    existing = db.q1("SELECT id, description FROM jobs WHERE uid=?", (j["uid"],))
    if existing:
        if not existing["description"] and j.get("description"):
            db.x("UPDATE jobs SET description=? WHERE id=?", (j["description"], existing["id"]))
        return False
    db.x("""INSERT INTO jobs(uid, source, ats, board, ext_id, company, title, location, remote, url, apply_url,
            description, salary, posted_at, discovered_at, status)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'new')""",
         (j["uid"], j.get("source"), j.get("ats"), j.get("board"), j.get("ext_id"), j.get("company"),
          j.get("title"), j.get("location"), int(j.get("remote") or 0), j.get("url"), j.get("apply_url"),
          j.get("description") or "", j.get("salary") or "", j.get("posted_at"), db.now()))
    return True


def job_from_url(url: str, company: str = "", title: str = "", source: str = "manual") -> dict:
    url = url.strip()
    info = detect_ats(url)
    ats, board, ext = info["ats"], info["board"], info["ext_id"]
    apply_url = url.split("?")[0] if ats in ("lever", "ashby", "workday", "workable") else url
    if ats == "greenhouse" and board and ext:
        apply_url = f"https://job-boards.greenhouse.io/{board}/jobs/{ext}"
    elif ats == "lever" and not apply_url.rstrip("/").endswith("/apply"):
        apply_url = apply_url.rstrip("/") + "/apply"
    elif ats == "ashby" and not apply_url.rstrip("/").endswith("/application"):
        apply_url = apply_url.rstrip("/") + "/application"
    elif ats == "workday" and not apply_url.rstrip("/").endswith("/apply"):
        apply_url = apply_url.rstrip("/") + "/apply"
    uid = f"{ats}:{board}:{ext}" if ext else f"{source}:{url.split('?')[0]}"
    if ats in ("greenhouse", "lever", "ashby", "workable", "workday") and board:
        boards.add_board(ats, board, company or None)
    return {"uid": uid, "source": source, "ats": ats, "board": board, "ext_id": ext, "company": company,
            "title": title, "location": "", "remote": 0, "url": url, "apply_url": apply_url, "description": ""}


async def fetch_feeds(urls: list[str], max_age_days: int, need_sponsorship: bool) -> list[dict]:
    out: list[dict] = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).timestamp()
    async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers=boards.UA) as c:
        for url in urls:
            try:
                r = await c.get(url)
                r.raise_for_status()
                items = r.json()
            except (httpx.HTTPError, ValueError) as e:
                log.warning("feed %s failed: %s", url, e)
                continue
            for it in items:
                if not it.get("active") or it.get("is_visible") is False:
                    continue
                if (it.get("date_posted") or 0) < cutoff:
                    continue
                spons = (it.get("sponsorship") or "").lower()
                if need_sponsorship and ("does not" in spons or "citizenship" in spons):
                    continue
                j = job_from_url(it.get("url", ""), it.get("company_name", ""), it.get("title", ""), source="simplify")
                locs = it.get("locations") or []
                j["location"] = " · ".join(locs)
                j["remote"] = int(any("remote" in l.lower() for l in locs))
                j["posted_at"] = parse_dt(it.get("date_posted"))
                out.append(j)
    return out


async def resolve_greenhouse_board(c: httpx.AsyncClient, url: str, company: str, job_id: str) -> str | None:
    """A job like company.com/careers/123?gh_jid=123: find which Greenhouse board it lives on."""
    cands: list[str] = []
    try:
        r = await c.get(url)
        cands += re.findall(r"greenhouse\.io/(?:embed/job_board(?:/js)?\?for=|embed/job_app\?for=)([A-Za-z0-9_-]+)", r.text)
        cands += re.findall(r"(?:boards|job-boards)\.greenhouse\.io/([A-Za-z0-9_-]+)/jobs", r.text)
    except httpx.HTTPError:
        pass
    base = re.sub(r"[^a-z0-9]", "", (company or "").lower())
    host = (url.split("/")[2] if "//" in url else "").lower()
    labels = [l for l in host.split(".") if l not in ("www", "com", "jobs", "careers", "io", "co", "org", "net")]
    cands += [base, re.sub(r"(inc|llc|corp|technologies|labs)$", "", base)] + labels
    seen = set()
    for b in cands:
        if not b or b in seen:
            continue
        seen.add(b)
        try:
            r = await c.get(f"https://boards-api.greenhouse.io/v1/boards/{b}/jobs/{job_id}")
            if r.status_code == 200:
                return b
        except httpx.HTTPError:
            continue
    return None


async def hydrate(job: dict) -> dict:
    """Fetch a description (and better company name) for jobs that came in without one."""
    if job.get("description"):
        return job
    desc, company = "", None
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=True, headers=boards.UA) as c:
            ats, board, ext = job.get("ats"), job.get("board"), job.get("ext_id")
            if ats == "greenhouse" and ext and not board:
                board = await resolve_greenhouse_board(c, job.get("url") or "", job.get("company") or "", ext)
                if board:
                    job["board"] = board
                    job["apply_url"] = f"https://job-boards.greenhouse.io/{board}/jobs/{ext}"
                    db.x("UPDATE jobs SET board=?, apply_url=? WHERE id=?", (board, job["apply_url"], job["id"]))
                    boards.add_board("greenhouse", board, job.get("company"))
            if ats == "greenhouse" and board and ext:
                r = await c.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{ext}")
                if r.status_code == 200:
                    d = r.json()
                    desc, company = html_to_text(d.get("content")), d.get("company_name")
            elif ats == "lever" and board and ext:
                r = await c.get(f"https://api.lever.co/v0/postings/{board}/{ext}")
                if r.status_code == 200:
                    d = r.json()
                    desc = d.get("descriptionPlain", "") + "\n" + "\n".join(
                        f"{l.get('text','')}\n{html_to_text(l.get('content'))}" for l in d.get("lists") or [])
            elif ats == "workday" and board and ext:
                d = await boards.workday_detail(c, board, ext)
                desc, company = d["description"], d.get("company")
            elif ats != "handshake":
                r = await c.get(job.get("url") or job.get("apply_url"))
                if r.status_code == 200 and "html" in r.headers.get("content-type", ""):
                    desc = html_to_text(r.text)[:12000]
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.info("hydrate %s failed: %s", job.get("uid"), e)
    if desc:
        job["description"] = desc
        db.x("UPDATE jobs SET description=? WHERE id=?", (desc, job["id"]))
    if company and company != job.get("company"):
        job["company"] = company
        db.x("UPDATE jobs SET company=? WHERE id=?", (company, job["id"]))
    return job


async def discover() -> dict:
    """One discovery pass. Returns counts per source."""
    s = cfg.get()
    p = prof.get()
    src = s["sources"]
    boards.seed_boards()
    counts: dict[str, int] = {}

    queries = search_queries(p["looking_for"].get("roles") or [], p["looking_for"].get("levels"))

    # Highest-priority site first (Handshake by default), so its jobs are in before anything else.
    if src.get("handshake"):
        from ..apply import handshake
        try:
            hs = await handshake.discover(queries, s["handshake"])
            counts["handshake"] = sum(upsert_job(j) for j in hs)
        except handshake.NeedsLogin:
            db.event("handshake", "Handshake needs you to sign in. Open Settings > Accounts > Sign in to Handshake.")
        except Exception as e:  # browser hiccups shouldn't stop discovery
            log.exception("handshake discovery failed")
            db.event("handshake", f"Handshake search failed: {e}")

    if src.get("simplify_feeds"):
        need = (p["eligibility"].get("need_sponsorship") or "").lower() == "yes"
        feed_jobs = await fetch_feeds(s["feeds"], s["max_job_age_days"], need)
        counts["simplify"] = sum(upsert_job(j) for j in feed_jobs)

    # Don't refetch a board more than every 3 hours.
    stale = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(timespec="seconds")
    got = await boards.fetch_all({k: bool(src.get(k)) for k in ("greenhouse", "lever", "ashby", "workable", "workday")},
                                 queries, stale_before=stale)
    for j in got:
        counts[j["ats"]] = counts.get(j["ats"], 0) + int(upsert_job(j))

    total = sum(counts.values())
    db.event("discover", f"Found {total} new jobs " + ", ".join(f"{k} {v}" for k, v in counts.items() if v))
    return counts


def add_job_url(url: str) -> int:
    j = job_from_url(url)
    if j["ats"] == "workday":
        wd = parse_workday(url)
        j["company"] = j["company"] or (wd["tenant"].title() if wd else "")
    if not j["company"]:
        host = re.sub(r"^(www|jobs|careers|apply|boards|job-boards)\.", "", (url.split("/")[2] if "//" in url else url))
        j["company"] = j["board"] if j["ats"] in ("greenhouse", "lever", "ashby", "workable") else host.split(".")[0]
        j["company"] = (j["company"] or "").replace("-", " ").title()
    j["title"] = j["title"] or "Job"
    upsert_job(j)
    row = db.q1("SELECT id FROM jobs WHERE uid=?", (j["uid"],))
    return row["id"]
