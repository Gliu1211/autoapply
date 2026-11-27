"""Filter discovered jobs, score them against the profile, and queue the best ones (respecting pacing)."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from . import db, llm
from . import profile as prof
from . import settings as cfg
from .llm import B, I, S, arr, obj
from .sources import hydrate
from . import companies
from .util import age_days, level_ok, location_ok, norm_company, role_keywords, role_rank, title_matches

log = logging.getLogger("autoapply.matching")


# ---------------- pacing ----------------

def sent_today() -> int:
    start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.scalar("SELECT COUNT(*) FROM applications WHERE status='sent' AND sent_at >= ?",
                     (start.astimezone(timezone.utc).isoformat(timespec="seconds"),)) or 0


def company_ok(company: str, exclude_app: int | None = None) -> tuple[bool, str]:
    s = cfg.get()
    key = norm_company(company)
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
    rows = db.q("""SELECT a.id, a.status, a.sent_at, j.company FROM applications a JOIN jobs j ON j.id=a.job_id
                   WHERE a.status IN ('sent','queued','applying','ready') AND (a.sent_at IS NULL OR a.sent_at >= ?)""",
                (since,))
    mine = [r for r in rows if norm_company(r["company"]) == key and r["id"] != exclude_app]
    if len(mine) >= s["per_company_30d"]:
        return False, f"Already {len(mine)} to {company} this month"
    gap = timedelta(days=s["company_gap_days"])
    for r in mine:
        if r["status"] != "sent" or not r["sent_at"]:
            continue
        if datetime.now(timezone.utc) - datetime.fromisoformat(r["sent_at"]) < gap:
            return False, f"Applied to {company} less than {s['company_gap_days']} days ago"
    return True, ""


# ---------------- filtering ----------------

AUTOPILOT_SITES = ("greenhouse", "lever", "ashby", "workable", "workday", "handshake")


def site_blocked(ats: str | None, s: dict | None = None) -> str:
    """Why autopilot won't apply on this site ("" = it will)."""
    s = s or cfg.get()
    ats = ats or "generic"
    if ats in s["sources"] and not s["sources"][ats]:
        return f"{ats.title()} is turned off in Settings" + (" (it shows bot CAPTCHAs)" if ats == "lever" else "")
    if s.get("supported_only", True) and ats not in AUTOPILOT_SITES:
        return "Site not supported by autopilot; use the browser extension"
    return ""


def priority_sql(s: dict | None = None, col: str = "ats") -> str:
    """SQL expression ranking a job's site by the user's source priority (0 = first)."""
    s = s or cfg.get()
    order = [a for a in s.get("source_priority") or [] if re.fullmatch(r"[a-z]+", a)]
    if not order:
        return "0"
    whens = " ".join(f"WHEN '{a}' THEN {i}" for i, a in enumerate(order))
    return f"(CASE {col} {whens} ELSE {len(order)} END)"


NOISE = re.compile(r"\b(remote|hybrid|on-?site|in-?office|usa?|united states|full[- ]time|part[- ]time)\b")


def title_keys(title: str | None) -> set[str]:
    """Ways one posting's title can be written: whole title, and the part before " - " / " | " (often a location)."""
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", (title or "").lower())     # "(Remote)", "[2027]"
    t = NOISE.sub(" ", t)
    keys = {re.sub(r"[^a-z0-9]+", "", t)}
    head = re.split(r"\s[-–|]\s", t)[0]
    keys.add(re.sub(r"[^a-z0-9]+", "", head))
    return {k for k in keys if len(k) >= 6}


DIMENSIONS = ("role", "company", "site")


def priority_order(s: dict) -> list[str]:
    order = [d for d in s.get("priority_order") or [] if d in DIMENSIONS]
    return list(dict.fromkeys(order + list(DIMENSIONS)))


def wanted_types(s: dict) -> list[str]:
    return [t for t in dict.fromkeys(s.get("company_types") or []) if t in companies.TYPES]


def order_sql(s: dict | None = None, prefix: str = "") -> str:
    """ORDER BY terms for "what to apply to first": role, company type and site priority, in the order the user chose."""
    s = s or cfg.get()
    n = len(wanted_types(s))
    terms = {"role": f"COALESCE({prefix}role_rank, 999)",
             "company": f"COALESCE({prefix}company_rank, {n})" if n else "",
             "site": priority_sql(s, f"{prefix}ats")}
    return ", ".join(t for t in (terms[d] for d in priority_order(s)) if t)


def only_sql(s: dict, prefix: str = "") -> str:
    """Extra WHERE condition when you only want your company types (companies not sorted yet wait)."""
    n = len(wanted_types(s))
    if not (n and s.get("company_types_only")):
        return ""
    return f" AND {prefix}company_rank IS NOT NULL AND {prefix}company_rank < {n}"


def update_company_ranks(s: dict | None = None) -> int:
    """Recompute every open job's company rank (after companies are sorted or your company types change)."""
    s = s or cfg.get()
    wanted = wanted_types(s)
    known = companies.stored() if wanted else {}
    cache: dict[str, int | None] = {}
    vals = []
    for r in db.q("SELECT id, company FROM jobs WHERE status IN ('new','candidate','scored','queued')"):
        c = r["company"] or ""
        if c not in cache:
            cache[c] = companies.rank(companies.types_for(c, known), wanted) if wanted else 0
        vals.append((cache[c], r["id"]))
    db.many("UPDATE jobs SET company_rank=? WHERE id=?", vals)
    return len(vals)


def unqueue_unwanted(s: dict | None = None) -> int:
    """With "only these company types" on, put queued autopilot picks at other companies back (they stay scored,
    so turning it off brings them back). Jobs you picked or OK'd yourself stay."""
    s = s or cfg.get()
    cond = only_sql(s, "j.")
    if not cond:
        return 0
    rows = db.q(f"""SELECT a.id, a.job_id FROM applications a JOIN jobs j ON j.id=a.job_id
                    WHERE a.status='queued' AND a.approved=0 AND COALESCE(a.manual,0)=0
                    AND j.company_rank IS NOT NULL AND NOT (1{cond})""")
    for r in rows:
        db.x("DELETE FROM applications WHERE id=?", (r["id"],))
        db.x("UPDATE jobs SET status='scored' WHERE id=?", (r["job_id"],))
    return len(rows)


async def refresh_company_types() -> None:
    """Sort any new companies by type, then re-rank jobs and drop unwanted ones from the queue."""
    await companies.classify_pending()
    s = cfg.get()
    update_company_ranks(s)
    unqueue_unwanted(s)


def update_role_ranks() -> int:
    """Recompute every open job's role rank (after the role list or its order changes)."""
    roles = prof.get()["looking_for"].get("roles") or []
    rows = db.q("SELECT id, title FROM jobs WHERE status IN ('new','candidate','scored','queued')")
    db.many("UPDATE jobs SET role_rank=? WHERE id=?", [(role_rank(r["title"], roles), r["id"]) for r in rows])
    return len(rows)


def already_applying(company: str, title: str, exclude_job: int | None = None) -> bool:
    """Same company and same role already queued/sent from another site (e.g. Handshake and Greenhouse)."""
    ck, tk = norm_company(company), title_keys(title)
    if not ck or not tk:
        return False
    for r in db.q("""SELECT j.id, j.company, j.title FROM applications a JOIN jobs j ON j.id=a.job_id
                     WHERE a.status NOT IN ('skipped','failed')"""):
        if r["id"] != exclude_job and norm_company(r["company"]) == ck and title_keys(r["title"]) & tk:
            return True
    return False


def prefilter() -> int:
    """Cheap rules on every new job: title, level, location, age, companies to avoid."""
    s = cfg.get()
    p = prof.get()
    lf = p["looking_for"]
    kws = role_keywords(lf.get("roles") or [])
    avoid = {norm_company(c) for c in lf.get("avoid_companies") or [] if c.strip()}
    levels = lf.get("levels") or lf.get("level") or []
    n = 0
    for j in db.q("SELECT id, title, company, location, remote, posted_at, ats FROM jobs WHERE status='new'"):
        reason = site_blocked(j["ats"], s)
        a = age_days(j["posted_at"])
        if reason:
            pass
        elif a is not None and a > s["max_job_age_days"]:
            reason = "Posted too long ago"
        elif kws and not title_matches(j["title"], kws):
            reason = "Title doesn't match your roles"
        elif norm_company(j["company"]) in avoid:
            reason = "Company on your avoid list"
        else:
            ok, why = level_ok(j["title"], levels)
            if not ok:
                reason = why
            else:
                ok, why = location_ok(j["location"], bool(j["remote"]), lf)
                if not ok:
                    reason = why
        if reason:
            db.x("UPDATE jobs SET status='filtered', filter_reason=? WHERE id=?", (reason, j["id"]))
        else:
            db.x("UPDATE jobs SET status='candidate', role_rank=? WHERE id=?",
                 (role_rank(j["title"], lf.get("roles") or []), j["id"]))
            n += 1
    return n


# ---------------- scoring ----------------

SCORE_SCHEMA = obj({"results": arr(obj({
    "id": I, "score": I, "reason": S, "breaks_instructions": B,
}))})

SCORE_SYSTEM = """You rate how well a candidate fits job postings, for an auto-apply tool.
score: 0-100. 80+ strong fit (skills and level line up), 60-79 reasonable, 40-59 stretch, <40 poor.
Weigh: required years of experience vs the candidate's, required degree/clearance/citizenship, core skills overlap, seniority.
A posting that requires several years the candidate lacks should score below 45.
breaks_instructions: true only if the job clearly conflicts with the candidate's standing instructions.
reason: one short phrase (max 14 words) naming the deciding factor."""


def _profile_brief(p: dict) -> str:
    edu = "; ".join(f"{e.get('degree','')} {e.get('field','')} at {e.get('school','')} ({e.get('start','')}-{e.get('end','')})"
                    for e in p["education"])
    exp = "; ".join(f"{e.get('title','')} at {e.get('company','')} ({e.get('start','')}-{e.get('end','')})"
                    for e in p["experience"])
    proj = "; ".join(f"{x.get('name','')} [{x.get('tech','')}]" for x in p["projects"])
    lf, el = p["looking_for"], p["eligibility"]
    return (f"Education: {edu or 'n/a'}\nExperience: {exp or 'none'}\nProjects: {proj or 'none'}\n"
            f"Skills: {', '.join(p['skills'][:60])}\nYears of professional experience: {el.get('years_experience') or 'unknown'}\n"
            f"Wants roles: {', '.join(lf.get('roles') or [])}; levels: {', '.join(lf.get('levels') or [])}\n"
            f"Work authorization: US authorized={el.get('authorized_us') or '?'}, needs sponsorship={el.get('need_sponsorship') or '?'}, "
            f"status={el.get('work_status') or '?'}, clearance={el.get('clearance') or 'none'}\n"
            f"Standing instructions: {lf.get('standing_instructions') or 'none'}")


def _heuristic_score(p: dict, j: dict) -> tuple[int, str]:
    desc = (j.get("description") or "").lower()
    skills = [s.lower() for s in p["skills"] if len(s) > 1]
    hits = [s for s in skills if re.search(rf"(?<![a-z]){re.escape(s)}(?![a-z])", desc)]
    yrs = re.findall(r"(\d+)\+?\s*(?:-\s*\d+\s*)?years", desc)
    need = min((int(y) for y in yrs if int(y) < 20), default=0)
    score = 45 + min(35, len(hits) * 5) - (20 if need >= 3 else 0)
    return max(5, min(90, score)), f"{len(hits)} of your skills mentioned" + (f"; asks {need}+ yrs" if need >= 3 else "")


async def score_candidates(limit: int = 60) -> int:
    p, s = prof.get(), cfg.get()
    jobs = db.q(f"SELECT * FROM jobs WHERE status='candidate'{only_sql(s)} ORDER BY {order_sql(s)}, "
                f"COALESCE(posted_at,'') DESC LIMIT ?", (limit,))
    if not jobs:
        return 0
    for j in jobs:
        if j["ats"] == "handshake" and not j["description"]:
            from .apply import handshake
            try:
                await handshake.detail(j)
            except Exception as e:  # noqa: BLE001 — a login wall or a slow page just means no description
                log.info("handshake detail failed: %s", e)
        else:
            await hydrate(j)
    brief = _profile_brief(p)
    use_llm = llm.available()
    done = 0
    for i in range(0, len(jobs), 10):
        batch = jobs[i:i + 10]
        results: dict[int, dict] = {}
        if use_llm:
            listing = "\n\n".join(
                f"<job id=\"{j['id']}\">\n{j['title']} at {j['company']} — {j['location']}\n{(j['description'] or '')[:1800]}\n</job>"
                for j in batch)
            try:
                model = llm.model_for("scoring")
                data = await llm.ajson_call(SCORE_SYSTEM, listing, SCORE_SCHEMA, effort=llm.scoring_effort(model), model=model,
                                            context=f"<candidate>\n{brief}\n</candidate>", purpose="scoring")
                results = {r["id"]: r for r in data["results"]}
            except llm.LLMError as e:
                log.warning("scoring fell back to heuristics: %s", e)
                use_llm = False
        for j in batch:
            r = results.get(j["id"])
            if r:
                score, reason = max(0, min(100, r["score"])), r["reason"]
                if r["breaks_instructions"]:
                    db.x("UPDATE jobs SET status='filtered', filter_reason=?, score=?, score_reason=?, scored_at=? WHERE id=?",
                         ("Conflicts with your standing instructions", score, reason, db.now(), j["id"]))
                    continue
            else:
                score, reason = _heuristic_score(p, j)
            db.x("UPDATE jobs SET status='scored', score=?, score_reason=?, scored_at=? WHERE id=?",
                 (score, reason, db.now(), j["id"]))
            done += 1
    return done


# ---------------- queueing ----------------

def create_application(job_id: int, status: str = "queued") -> int:
    job = db.q1("SELECT ats FROM jobs WHERE id=?", (job_id,))
    existing = db.q1("SELECT id, status FROM applications WHERE job_id=?", (job_id,))
    if existing:
        if existing["status"] in ("skipped", "failed", "needs_you"):
            db.app_update(existing["id"], status=status, status_detail="")
        return existing["id"]
    app_id = db.x("INSERT INTO applications(job_id, status, ats, created_at, updated_at) VALUES(?,?,?,?,?)",
                  (job_id, status, job["ats"] if job else "", db.now(), db.now()))
    db.x("UPDATE jobs SET status='queued' WHERE id=?", (job_id,))
    return app_id


def queue_best(max_new: int) -> int:
    s = cfg.get()
    queued = 0
    rows = db.q(f"""SELECT id, company, title, score FROM jobs WHERE status='scored' AND score >= ?{only_sql(s)}
                    ORDER BY {order_sql(s)}, score DESC, COALESCE(posted_at,'') DESC LIMIT 300""", (s["min_match"],))
    for j in rows:
        if queued >= max_new:
            break
        if already_applying(j["company"], j["title"], exclude_job=j["id"]):
            db.x("UPDATE jobs SET status='skipped', filter_reason='Same role already queued from a higher-priority site' WHERE id=?", (j["id"],))
            continue
        ok, _ = company_ok(j["company"])
        if not ok:
            continue
        create_application(j["id"])
        queued += 1
    return queued
