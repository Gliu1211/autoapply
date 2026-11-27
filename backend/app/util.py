"""Small shared helpers: HTML to text, ATS detection from URLs, location and title matching."""
from __future__ import annotations

import html
from functools import lru_cache
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse


def html_to_text(s: str | None) -> str:
    if not s:
        return ""
    s = html.unescape(s)  # Greenhouse double-escapes
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</div>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "• ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s)
    return s.strip()


def parse_dt(v) -> str | None:
    """Accepts ISO strings, epoch seconds or ms. Returns ISO UTC."""
    if v in (None, ""):
        return None
    try:
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
            n = float(v)
            if n > 1e12:
                n /= 1000
            return datetime.fromtimestamp(n, tz=timezone.utc).isoformat(timespec="seconds")
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (ValueError, OSError):
        return None


def norm_company(name: str | None) -> str:
    """Comparable company key: lowercase, no suffixes (Inc, LLC, Technologies...) or punctuation."""
    s = (name or "").lower()
    s = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company|technologies|technology|labs|ai)\b\.?", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


def age_days(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso)
        return (datetime.now(timezone.utc) - d).total_seconds() / 86400
    except ValueError:
        return None


def workday_posted_to_iso(posted_on: str) -> str | None:
    """'Posted Today' / 'Posted Yesterday' / 'Posted 3 Days Ago' / 'Posted 30+ Days Ago'."""
    from datetime import timedelta
    s = (posted_on or "").lower()
    now = datetime.now(timezone.utc)
    if "today" in s:
        days = 0
    elif "yesterday" in s:
        days = 1
    else:
        m = re.search(r"(\d+)\+?\s*day", s)
        if not m:
            return None
        days = int(m.group(1)) + (5 if "+" in s else 0)
    return (now - timedelta(days=days)).isoformat(timespec="seconds")


# ---------------- ATS detection ----------------

def detect_ats(url: str) -> dict:
    """Return {'ats', 'board', 'ext_id'} for known applicant tracking systems."""
    if not url:
        return {"ats": "generic", "board": "", "ext_id": ""}
    u = urlparse(url)
    host = (u.netloc or "").lower()
    parts = [p for p in u.path.split("/") if p]
    qs = parse_qs(u.query)

    if "greenhouse.io" in host:
        if "embed" in parts:
            return {"ats": "greenhouse", "board": (qs.get("for") or [""])[0], "ext_id": (qs.get("token") or [""])[0]}
        if len(parts) >= 3 and parts[1] == "jobs":
            return {"ats": "greenhouse", "board": parts[0], "ext_id": re.sub(r"\D", "", parts[2])}
        return {"ats": "greenhouse", "board": parts[0] if parts else "", "ext_id": (qs.get("gh_jid") or [""])[0]}
    if "gh_jid" in qs:
        return {"ats": "greenhouse", "board": "", "ext_id": qs["gh_jid"][0]}
    if host.endswith("lever.co"):
        return {"ats": "lever", "board": parts[0] if parts else "", "ext_id": parts[1] if len(parts) > 1 else ""}
    if host.endswith("ashbyhq.com"):
        return {"ats": "ashby", "board": parts[0] if parts else "", "ext_id": parts[1] if len(parts) > 1 else ""}
    if host.endswith("workable.com"):
        if "j" in parts:
            i = parts.index("j")
            return {"ats": "workable", "board": parts[0] if i > 0 else "", "ext_id": parts[i + 1] if len(parts) > i + 1 else ""}
        return {"ats": "workable", "board": parts[0] if parts else "", "ext_id": ""}
    wd = parse_workday(url)
    if wd:
        return {"ats": "workday", "board": wd["board"], "ext_id": wd["path"]}
    if "joinhandshake.com" in host:
        m = re.search(r"/(?:jobs|job-search|postings)/(\d+)", u.path)
        return {"ats": "handshake", "board": host, "ext_id": m.group(1) if m else ""}
    if "smartrecruiters.com" in host:
        return {"ats": "smartrecruiters", "board": parts[0] if parts else "", "ext_id": parts[1] if len(parts) > 1 else ""}
    if "icims.com" in host:
        return {"ats": "icims", "board": host.split(".")[0], "ext_id": ""}
    if "oraclecloud.com" in host:
        return {"ats": "oracle", "board": host.split(".")[0], "ext_id": ""}
    if "eightfold.ai" in host:
        return {"ats": "eightfold", "board": host.split(".")[0], "ext_id": ""}
    if "jobvite.com" in host:
        return {"ats": "jobvite", "board": parts[0] if parts else "", "ext_id": ""}
    if "bamboohr.com" in host:
        return {"ats": "bamboohr", "board": host.split(".")[0], "ext_id": ""}
    if "rippling.com" in host:
        return {"ats": "rippling", "board": parts[0] if parts else "", "ext_id": ""}
    return {"ats": "generic", "board": host, "ext_id": ""}


def parse_workday(url: str) -> dict | None:
    """Workday career sites: {tenant}.wdN.myworkdayjobs.com/[lang/]{site}/job/...  or  wdN.myworkdaysite.com/[lang/]recruiting/{tenant}/{site}/job/..."""
    u = urlparse(url)
    host = (u.netloc or "").lower()
    parts = [p for p in u.path.split("/") if p]
    if parts and re.fullmatch(r"[a-zA-Z]{2}-[a-zA-Z]{2}", parts[0]):
        parts = parts[1:]
    if host.endswith("myworkdayjobs.com"):
        tenant = host.split(".")[0]
        if not parts:
            return None
        site = parts[0]
        rest = parts[1:]
    elif host.endswith("myworkdaysite.com") and len(parts) >= 3 and parts[0] == "recruiting":
        tenant, site, rest = parts[1], parts[2], parts[3:]
    else:
        return None
    path = ""
    if rest and rest[0] == "job":
        path = "/" + "/".join(rest)
        path = re.sub(r"/(apply|applyManually|autofillWithResume).*$", "", path)
    return {"host": host, "tenant": tenant, "site": site, "board": f"{host}|{tenant}|{site}", "path": path}


def workday_job_url(board: str, path: str) -> str:
    host, tenant, site = board.split("|")
    if host.endswith("myworkdaysite.com"):
        return f"https://{host}/en-US/recruiting/{tenant}/{site}{path}"
    return f"https://{host}/en-US/{site}{path}"


# ---------------- location ----------------

US_STATES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california", "CO": "colorado",
    "CT": "connecticut", "DE": "delaware", "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho",
    "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas", "KY": "kentucky", "LA": "louisiana",
    "ME": "maine", "MD": "maryland", "MA": "massachusetts", "MI": "michigan", "MN": "minnesota",
    "MS": "mississippi", "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada",
    "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico", "NY": "new york", "NC": "north carolina",
    "ND": "north dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon", "PA": "pennsylvania",
    "RI": "rhode island", "SC": "south carolina", "SD": "south dakota", "TN": "tennessee", "TX": "texas",
    "UT": "utah", "VT": "vermont", "VA": "virginia", "WA": "washington", "WV": "west virginia",
    "WI": "wisconsin", "WY": "wyoming", "DC": "district of columbia",
}
US_CITIES = {
    "san francisco", "new york", "nyc", "seattle", "austin", "boston", "chicago", "los angeles", "palo alto",
    "mountain view", "menlo park", "sunnyvale", "san jose", "san mateo", "redwood city", "oakland", "berkeley",
    "santa clara", "cupertino", "denver", "boulder", "atlanta", "miami", "dallas", "houston", "san diego",
    "washington", "arlington", "mclean", "reston", "baltimore", "columbia", "philadelphia", "pittsburgh",
    "raleigh", "durham", "nashville", "salt lake city", "portland", "phoenix", "detroit", "minneapolis",
    "bay area", "sf", "hawthorne", "irvine", "brooklyn", "jersey city", "cambridge", "bellevue", "kirkland",
    "redmond", "plano", "charlotte", "st. louis", "columbus", "cincinnati", "madison", "ann arbor",
}
NON_US = re.compile(
    r"\b(canada|toronto|vancouver|montreal|ottawa|united kingdom|\buk\b|london|england|ireland|dublin|germany|berlin|"
    r"munich|france|paris|spain|madrid|barcelona|netherlands|amsterdam|poland|warsaw|india|bangalore|bengaluru|"
    r"hyderabad|pune|mumbai|delhi|singapore|japan|tokyo|china|beijing|shanghai|shenzhen|guangzhou|hong kong|"
    r"taiwan|taipei|korea|seoul|australia|sydney|melbourne|brazil|são paulo|sao paulo|mexico|argentina|"
    r"israel|tel aviv|yokneam|uae|dubai|switzerland|zurich|sweden|stockholm|denmark|copenhagen|portugal|lisbon|"
    r"italy|milan|romania|czech|prague|philippines|vietnam|indonesia|malaysia|one-north|emea|apac|latam|europe)\b",
    re.I,
)


def is_us(loc: str | None, remote: bool = False) -> bool | None:
    """True if the location is in the US, False if clearly elsewhere, None if unknown."""
    s = (loc or "").strip()
    if not s:
        return True if remote else None
    low = s.lower()
    us_hit = bool(re.search(r"\b(united states|usa|u\.s\.|us-remote|remote[ -]us|us remote|\bus\b)", low))
    if not us_hit:
        for code in US_STATES:
            if re.search(rf"(,|\s|-)\s*{code}\b", s):
                us_hit = True
                break
    if not us_hit:
        us_hit = any(name in low for name in US_STATES.values() if len(name) > 5) or any(c in low for c in US_CITIES)
    if us_hit:
        return True
    if NON_US.search(low):
        return False
    if "remote" in low or "anywhere" in low:
        return True
    return None


def location_ok(loc: str | None, remote: bool, prefs: dict) -> tuple[bool, str]:
    us = is_us(loc, remote)
    if us is False:
        return False, "Outside the US"
    cities = [c.strip().lower() for c in prefs.get("locations") or [] if c.strip()]
    mode = (prefs.get("remote") or "Remote or on-site").lower()
    low = (loc or "").lower()
    is_remote = remote or "remote" in low
    if mode.startswith("remote only") and not is_remote:
        return False, "Not remote"
    if mode.startswith("on-site only") and is_remote and not cities:
        return True, ""
    if cities and not is_remote:
        if not any(c.split(",")[0] in low for c in cities):
            return False, "Not in your cities"
    if us is None and not is_remote:
        return False, "Location unclear"
    return True, ""


# ---------------- titles ----------------

FAMILIES = {
    "software": ["software engineer", "software developer", "swe", "backend", "back-end", "back end", "frontend",
                 "front-end", "front end", "full stack", "fullstack", "full-stack", "web developer", "web engineer",
                 "application engineer", "applications engineer", "platform engineer", "product engineer",
                 "mobile engineer", "ios engineer", "android engineer", "infrastructure engineer",
                 "developer", "programmer", "member of technical staff", "devops",
                 "site reliability", "sre", "cloud engineer", "software development engineer"],
    "data": ["data engineer", "analytics engineer", "data analyst", "business analyst", "business intelligence",
             "bi analyst", "bi engineer", "data scientist", "analytics", "data platform", "etl", "reporting analyst"],
    "ml": ["machine learning", "ml engineer", "mle", "ai engineer", "applied ai", "artificial intelligence",
           "applied scientist", "deep learning", "llm", "research engineer", "nlp", "computer vision", "genai",
           "generative ai", "ml ops", "mlops", "ai/ml", "ml/ai"],
    "fde": ["forward deployed", "forward-deployed", "solutions engineer", "deployment engineer", "field engineer",
            "customer engineer", "implementation engineer", "solutions architect", "technical consultant"],
}
FAMILY_TRIGGERS = {
    "software": ["software", "backend", "back-end", "frontend", "front-end", "full stack", "fullstack", "web",
                 "developer", "swe", "platform", "mobile", "infrastructure", "devops", "sre"],
    "data": ["data", "analyst", "analytics", "business intelligence", "bi "],
    "ml": ["machine learning", "ml", "ai ", "ai", "artificial intelligence", "deep learning", "llm", "nlp"],
    "fde": ["forward deployed", "forward-deployed", "solutions", "deployment", "field engineer", "customer engineer"],
}


def role_keywords(roles: list[str]) -> list[str]:
    kws: set[str] = set()
    for r in roles:
        low = r.lower().strip()
        if not low:
            continue
        kws.add(low)
        stripped = re.sub(r"\b(engineer|engineering|developer|role|jobs?)\b", "", low).strip()
        if len(stripped) >= 4:
            kws.add(stripped)
        padded = f" {low} "
        for fam, triggers in FAMILY_TRIGGERS.items():
            if any((f" {t.strip()} " in padded) or low.startswith(t.strip()) for t in triggers):
                kws.update(FAMILIES[fam])
    return sorted(kws, key=len, reverse=True)


def title_matches(title: str, keywords: list[str]) -> bool:
    low = f" {re.sub(r'[^a-z0-9/+&-]+', ' ', (title or '').lower())} "
    for k in keywords:
        k2 = re.sub(r"[^a-z0-9/+&-]+", " ", k.lower()).strip()
        if k2 and re.search(rf"(?<![a-z]){re.escape(k2)}(?![a-z])", low):
            return True
    return False


SENIOR = re.compile(r"\b(senior|sr\.?|staff|principal|lead|manager|director|head of|vp|vice president|architect|"
                    r"distinguished|fellow|chief|iii|iv|level\s*[3-9]|l[5-9])\b", re.I)
INTERN = re.compile(r"\b(intern|internship|co-?op|summer \d{4}|fall \d{4}|spring \d{4}|apprentice)\b", re.I)
JUNIOR = re.compile(r"\b(new grad|new graduate|entry|junior|jr\.?|associate|early career|university grad|"
                    r"graduate|campus|i\b|level\s*[12]|l[3-4])\b", re.I)


def level_ok(title: str, levels: list[str] | str) -> tuple[bool, str]:
    if isinstance(levels, str):
        levels = [levels]
    lv = {l.lower() for l in levels or []}
    want_intern = any("intern" in l for l in lv)
    want_entry = any("entry" in l for l in lv)
    want_mid = any("mid" in l for l in lv)
    want_senior = any(s in l for l in lv for s in ("senior", "staff", "manager"))
    if not lv:
        return True, ""
    if INTERN.search(title):
        return (want_intern, "" if want_intern else "Internship")
    if SENIOR.search(title) and not JUNIOR.search(title):
        return (want_senior, "" if want_senior else "Too senior")
    if want_entry or want_mid or want_senior:
        return True, ""
    return False, "Not an internship"


FAMILY_QUERY = {"software": "software engineer", "data": "data", "ml": "machine learning", "fde": "forward deployed"}


def search_queries(roles: list[str], levels: list[str] | None = None, limit: int = 4) -> list[str]:
    """A few broad search strings for sites with keyword search (Workday, Handshake)."""
    out: list[str] = []
    for r in roles:
        low = f" {r.lower()} "
        for fam, triggers in FAMILY_TRIGGERS.items():
            if any(f" {t.strip()} " in low or low.strip().startswith(t.strip()) for t in triggers):
                q = FAMILY_QUERY[fam]
                if q not in out:
                    out.append(q)
    if not out:
        out = [r for r in roles[:limit]]
    if levels and any("intern" in l.lower() for l in levels) and len(out) < limit:
        out.append(f"{out[0]} intern" if out else "intern")
    return out[:limit]


# ---------------- role priority ----------------

# For ranking only (jobs already passed the role filter): AI/ML roles also claim titles like "Software Engineer, AI".
AI_EXTRAS = ("ai", "ml", "llm", "llms", "genai", "agentic", "ai agents", "ai/ml", "ml/ai")


@lru_cache(maxsize=256)
def rank_keywords(role: str) -> tuple[str, ...]:
    kws = set(role_keywords([role]))
    if "machine learning" in kws:
        kws |= set(AI_EXTRAS)
    return tuple(sorted(kws, key=len, reverse=True))


def role_rank(title: str, roles: list[str]) -> int:
    """Position of the highest-priority role this title matches (0 = first); len(roles) if none."""
    for i, r in enumerate(roles):
        if r.strip() and title_matches(title, list(rank_keywords(r.strip()))):
            return i
    return len(roles)
