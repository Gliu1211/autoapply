"""Public job-board APIs: Greenhouse, Lever, Ashby, Workable, Workday. All return normalized job dicts."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .. import db
from ..util import html_to_text, parse_dt, workday_job_url, workday_posted_to_iso

log = logging.getLogger("autoapply.sources")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"}

# Starter index of company boards. Bad tokens are marked "missing" on first fetch and skipped after.
# The Simplify feeds and any job URL you add grow this list automatically.
SEED: dict[str, list[str]] = {
    "greenhouse": [
        "airbnb", "stripe", "figma", "discord", "robinhood", "databricks", "cloudflare", "datadog", "mongodb",
        "asana", "dropbox", "lyft", "pinterest", "reddit", "instacart", "gitlab", "twitch", "affirm", "brex",
        "chime", "gusto", "airtable", "anthropic", "scaleai", "spacex", "andurilindustries", "waymo", "nuro",
        "samsara", "verkada", "vercel", "roblox", "flexport", "okta", "toast", "squarespace", "duolingo",
        "carta", "sofi", "nerdwallet", "opendoor", "grammarly", "benchling", "purestorage", "rubrik",
        "cockroachlabs", "fivetran", "dbtlabsinc", "janestreet", "hudsonrivertrading", "imc", "point72",
        "coinbase", "doordashusa", "elastic", "twilio", "zscaler", "riotgames", "epicgames", "peloton",
        "appliedintuition", "mercury", "retool", "sigmacomputing", "abnormalsecurity", "wiz", "snyk",
        "hightouch", "clickhouse", "planetscale", "temporal", "lucidmotors", "relativity", "shieldai",
        "palantirtechnologies", "axon", "drw", "virtu", "optiver", "towerresearchcapital", "akunacapital",
        "cerebrassystems", "sambanovasystems", "groq", "nvidia", "ziprecruiter", "betterment", "oscar",
        "flatironhealth", "zocdoc", "headway", "hims", "calendly", "lattice", "rippling", "faire", "chainguard",
    ],
    "lever": [
        "palantir", "spotify", "weride", "plaid", "shieldai", "zoox", "kraken", "mistral", "whoop", "outreach",
        "attentive", "clari", "netlify", "highspot", "sprinklr", "eventbrite", "lucid", "binance", "ro",
        "circadiahealth", "matchgroup", "veeva", "jumpcloud", "applied", "voleon", "wealthfront",
    ],
    "ashby": [
        "openai", "notion", "ramp", "linear", "replit", "perplexity", "anysphere", "cursor", "modal", "supabase",
        "posthog", "deel", "vanta", "cohere", "elevenlabs", "harvey", "decagon", "sierra", "pinecone",
        "runway", "characterai", "hebbia", "glean", "writer", "baseten", "together", "langchain", "clay",
        "mercor", "browserbase", "zip", "watershed", "plaid", "benchling", "snowflake", "confluent", "airbyte",
        "cognition", "scale", "physicalintuition", "skild", "figureai", "anduril", "hex", "warp", "graphite",
    ],
    "workable": ["talentpluto"],
    # Workday: "host|tenant|site"
    "workday": [
        "nvidia.wd5.myworkdayjobs.com|nvidia|NVIDIAExternalCareerSite",
        "salesforce.wd12.myworkdayjobs.com|salesforce|External_Career_Site",
        "adobe.wd5.myworkdayjobs.com|adobe|external_experienced",
        "capitalone.wd12.myworkdayjobs.com|capitalone|Capital_One",
        "intel.wd1.myworkdayjobs.com|intel|External",
        "mastercard.wd1.myworkdayjobs.com|mastercard|CorporateCareers",
        "workday.wd5.myworkdayjobs.com|workday|Workday",
        "boeing.wd1.myworkdayjobs.com|boeing|EXTERNAL_CAREERS",
        "bah.wd1.myworkdayjobs.com|bah|BAH_Jobs",
        "leidos.wd5.myworkdayjobs.com|leidos|External",
    ],
}


def seed_boards() -> None:
    for ats, tokens in SEED.items():
        for t in tokens:
            db.x("INSERT OR IGNORE INTO boards(ats, token, status) VALUES(?,?,'ok')", (ats, t))


def add_board(ats: str, token: str, company: str | None = None) -> None:
    if not token or ats not in ("greenhouse", "lever", "ashby", "workable", "workday"):
        return
    db.x("INSERT OR IGNORE INTO boards(ats, token, company, status) VALUES(?,?,?,'ok')", (ats, token, company))


def _job(**kw: Any) -> dict:
    kw.setdefault("remote", 0)
    kw.setdefault("salary", "")
    kw["uid"] = f"{kw['ats']}:{kw['board']}:{kw['ext_id']}"
    return kw


async def greenhouse(c: httpx.AsyncClient, token: str) -> list[dict]:
    r = await c.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", params={"content": "true"})
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        out.append(_job(
            source="greenhouse", ats="greenhouse", board=token, ext_id=str(j["id"]),
            company=j.get("company_name") or token, title=j.get("title", ""), location=loc,
            remote=int("remote" in loc.lower()),
            url=j.get("absolute_url"),
            apply_url=f"https://job-boards.greenhouse.io/{token}/jobs/{j['id']}",
            description=html_to_text(j.get("content")),
            posted_at=parse_dt(j.get("first_published") or j.get("updated_at")),
        ))
    return out


async def lever(c: httpx.AsyncClient, token: str) -> list[dict]:
    r = await c.get(f"https://api.lever.co/v0/postings/{token}", params={"mode": "json"})
    r.raise_for_status()
    out = []
    for j in r.json():
        cat = j.get("categories") or {}
        loc = cat.get("location") or ", ".join(cat.get("allLocations") or [])
        desc = j.get("descriptionPlain", "")
        for lst in j.get("lists") or []:
            desc += f"\n\n{lst.get('text','')}\n{html_to_text(lst.get('content'))}"
        desc += "\n\n" + (j.get("additionalPlain") or "")
        sal = j.get("salaryRange") or {}
        out.append(_job(
            source="lever", ats="lever", board=token, ext_id=j["id"], company=token.replace("-", " ").title(),
            title=j.get("text", ""), location=loc, remote=int((j.get("workplaceType") or "") == "remote"),
            url=j.get("hostedUrl"), apply_url=j.get("applyUrl") or f"{j.get('hostedUrl')}/apply",
            description=desc.strip(), posted_at=parse_dt(j.get("createdAt")),
            salary=f"{sal.get('min','')}-{sal.get('max','')} {sal.get('currency','')}".strip(" -") if sal else "",
        ))
    return out


async def ashby(c: httpx.AsyncClient, token: str) -> list[dict]:
    r = await c.get(f"https://api.ashbyhq.com/posting-api/job-board/{token}", params={"includeCompensation": "true"})
    r.raise_for_status()
    data = r.json()
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        addr = ((j.get("address") or {}).get("postalAddress") or {})
        loc = ", ".join(x for x in [locs[0], addr.get("addressRegion"), addr.get("addressCountry")] if x)
        if len(locs) > 1:
            loc += " · " + " · ".join(l for l in locs[1:] if l)
        comp = (j.get("compensation") or {}).get("scrapeableCompensationSalarySummary") or ""
        out.append(_job(
            source="ashby", ats="ashby", board=token, ext_id=j["id"], company=token.replace("-", " ").title(),
            title=j.get("title", ""), location=loc, remote=int(bool(j.get("isRemote"))),
            url=j.get("jobUrl"), apply_url=j.get("applyUrl") or f"{j.get('jobUrl')}/application",
            description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
            posted_at=parse_dt(j.get("publishedAt")), salary=comp,
        ))
    return out


async def workable(c: httpx.AsyncClient, token: str) -> list[dict]:
    r = await c.get(f"https://apply.workable.com/api/v1/widget/accounts/{token}", params={"details": "true"})
    r.raise_for_status()
    data = r.json()
    out = []
    for j in data.get("jobs", []):
        loc = ", ".join(x for x in [j.get("city"), j.get("state"), j.get("country")] if x)
        out.append(_job(
            source="workable", ats="workable", board=token, ext_id=j.get("shortcode", ""),
            company=data.get("name") or token, title=j.get("title", ""), location=loc,
            remote=int(bool(j.get("telecommuting"))), url=j.get("url"),
            apply_url=j.get("application_url") or f"{j.get('url')}/apply",
            description=html_to_text(j.get("description")), posted_at=parse_dt(j.get("published_on")),
        ))
    return out


async def workday(c: httpx.AsyncClient, board: str, queries: list[str]) -> list[dict]:
    host, tenant, site = board.split("|")
    api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    seen: dict[str, dict] = {}
    for qtext in queries[:4] or [""]:
        r = await c.post(api, json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": qtext},
                         headers={"Content-Type": "application/json", "Accept": "application/json"})
        r.raise_for_status()
        for j in r.json().get("jobPostings", []):
            path = j.get("externalPath")
            if not path or path in seen:
                continue
            seen[path] = _job(
                source="workday", ats="workday", board=board, ext_id=path, company=tenant.upper() if len(tenant) <= 4 else tenant.title(),
                title=j.get("title", ""), location=j.get("locationsText", ""),
                remote=int("remote" in (j.get("locationsText") or "").lower()),
                url=workday_job_url(board, path), apply_url=workday_job_url(board, path) + "/apply",
                description="", posted_at=workday_posted_to_iso(j.get("postedOn", "")),
            )
    return list(seen.values())


async def workday_detail(c: httpx.AsyncClient, board: str, path: str) -> dict:
    host, tenant, site = board.split("|")
    r = await c.get(f"https://{host}/wday/cxs/{tenant}/{site}{path}", headers={"Accept": "application/json"})
    r.raise_for_status()
    d = r.json()
    info = d.get("jobPostingInfo") or {}
    org = (d.get("hiringOrganization") or {}).get("name")
    return {"description": html_to_text(info.get("jobDescription")), "company": org,
            "location": info.get("location"), "posted_at": parse_dt(info.get("startDate"))}


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workable": workable}


async def fetch_all(enabled: dict[str, bool], queries: list[str], stale_before: str = "9999",
                    max_boards: int = 400) -> list[dict]:
    """Fetch enabled boards not fetched since `stale_before`, concurrently. Marks dead tokens so they are skipped."""
    boards = db.q("""SELECT ats, token FROM boards WHERE status != 'missing' AND ats != 'workday'
                     AND (last_fetched IS NULL OR last_fetched < ?)
                     ORDER BY COALESCE(last_fetched,'') LIMIT ?""", (stale_before, max_boards))
    # Workday shares rate limits across tenants: fewer boards per pass, two at a time, stop on 429.
    boards += db.q("""SELECT ats, token FROM boards WHERE status != 'missing' AND ats = 'workday'
                      AND (last_fetched IS NULL OR last_fetched < ?)
                      ORDER BY COALESCE(last_fetched,'') LIMIT 40""", (stale_before,))
    sem = asyncio.Semaphore(12)
    wd_sem = asyncio.Semaphore(2)
    wd_blocked = False
    jobs: list[dict] = []

    async with httpx.AsyncClient(headers=UA, timeout=25, follow_redirects=True) as c:
        async def one(b: dict) -> None:
            nonlocal wd_blocked
            ats, token = b["ats"], b["token"]
            if not enabled.get(ats) or (ats == "workday" and wd_blocked):
                return
            async with (wd_sem if ats == "workday" else sem):
                if ats == "workday" and wd_blocked:
                    return
                try:
                    if ats == "workday":
                        got = await workday(c, token, queries)
                        await asyncio.sleep(1.5)
                    else:
                        got = await FETCHERS[ats](c, token)
                    jobs.extend(got)
                    company = got[0]["company"] if got else None
                    db.x("UPDATE boards SET status='ok', last_fetched=?, job_count=?, company=COALESCE(?, company) "
                         "WHERE ats=? AND token=?", (db.now(), len(got), company, ats, token))
                except httpx.HTTPStatusError as e:
                    code = e.response.status_code
                    if code == 429:
                        if ats == "workday":
                            wd_blocked = True
                        return   # rate limited: leave last_fetched alone so it's retried next pass
                    status = "missing" if code in (404, 410, 422) else "error"
                    db.x("UPDATE boards SET status=?, last_fetched=? WHERE ats=? AND token=?",
                         (status, db.now(), ats, token))
                except (httpx.HTTPError, ValueError, KeyError) as e:
                    log.warning("board %s:%s failed: %s", ats, token, e)
                    db.x("UPDATE boards SET status='error', last_fetched=? WHERE ats=? AND token=?",
                         (db.now(), ats, token))

        await asyncio.gather(*(one(b) for b in boards))
    return jobs
