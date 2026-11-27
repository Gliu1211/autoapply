"""What kind of employer each company is (big tech, Fortune 500, defense, government...), for "what to apply to first".

Obvious names are matched by short built-in lists; every other company is labeled once by Claude (the cheap model,
100 companies per request, about $0.001) and cached in the companies table.
"""
from __future__ import annotations

import json
import logging
import re

from . import db, llm
from .llm import I, arr, obj
from .util import norm_company

log = logging.getLogger("autoapply.companies")

# key: (label, what it covers; shown in Settings and given to the classifier)
TYPES: dict[str, tuple[str, str]] = {
    "big_tech": ("Big tech", "Large, established tech companies: Google, Meta, Apple, Amazon, Microsoft, Nvidia, Netflix, "
                             "Salesforce, Adobe and the like."),
    "fortune_500": ("Fortune 500", "US companies on the current Fortune 500 (the 500 largest by revenue), "
                                   "including their divisions."),
    "defense": ("Defense & aerospace", "Defense contractors, defense tech and military aerospace: Lockheed Martin, RTX, "
                                       "Northrop Grumman, Anduril, Booz Allen, CACI and the like."),
    "government": ("Government", "Federal, state and local government agencies, national laboratories and other "
                                 "public bodies."),
    "startup": ("Startups", "Private, venture-backed companies, usually under about 1,000 people."),
    "finance": ("Finance & trading", "Banks, trading firms, hedge funds, asset managers, insurers and fintech."),
}
ORDER = list(TYPES)

# Built-in lists, matched on the normalized name (see util.norm_company). Exact for short or ambiguous names
# ("Apple Bank" isn't Apple), prefix for distinctive ones ("Amazon Web Services" is Amazon).
_EXACT = {
    "big_tech": {"meta", "metaplatforms", "facebook", "apple", "intel", "oracle", "uber", "ibm", "amd", "aws", "adobe",
                 "cisco", "tesla", "intuit", "paypal", "ebay", "spotify", "pinterest", "dropbox", "doordash", "airbnb",
                 "lyft", "sony", "openai", "anthropic", "github", "youtube", "deepmind"},
    "defense": {"rtx", "saic", "caci", "mitre", "v2x", "kbr", "epirus", "leidos", "peraton", "amentum", "mantech"},
}
_PREFIX = {
    "big_tech": ("google", "alphabet", "amazon", "microsoft", "nvidia", "salesforce", "linkedin", "netflix", "qualcomm",
                 "servicenow", "bytedance", "tiktok", "advancedmicrodevices", "broadcom", "samsung"),
    "defense": ("lockheedmartin", "northropgrumman", "raytheon", "generaldynamics", "boeing", "baesystems", "l3harris",
                "boozallen", "anduril", "palantir", "huntingtoningalls", "textron", "generalatomics", "collinsaerospace",
                "prattwhitney", "prattandwhitney", "kratosdefense", "mercurysystems", "aerovironment", "saronic",
                "nightwing", "castelion", "scienceapplicationsinternational"),
}
_GOVERNMENT = re.compile(
    r"\b(?:department of|dept\.? of|county of|city of|town of|village of|state of|commonwealth of|government of|"
    r"federal reserve|national laborator(?:y|ies)|transportation authority|transit authority|port authority|"
    r"housing authority|school district|u\.?s\.? (?:army|navy|air force|marine corps|coast guard|space force)|"
    r"nasa|noaa|national institutes of health|centers for disease control|veterans affairs)\b"
    r"|\bcounty\b(?!.*\b(?:bank|credit union|insurance|realty|brewing)\b)", re.I)


def seed_types(name: str | None) -> set[str]:
    k = norm_company(name)
    out = set()
    if not k:
        return out
    for t, names in _EXACT.items():
        if k in names:
            out.add(t)
    for t, prefixes in _PREFIX.items():
        if k.startswith(prefixes):
            out.add(t)
    if _GOVERNMENT.search(name or ""):
        out.add("government")
    return out


def stored() -> dict[str, list[str]]:
    """Every company Claude has labeled: {normalized name: [types]}."""
    out = {}
    for r in db.q("SELECT key, types FROM companies"):
        try:
            out[r["key"]] = json.loads(r["types"] or "[]")
        except ValueError:
            out[r["key"]] = []
    return out


def types_for(name: str | None, known: dict[str, list[str]] | None = None) -> list[str] | None:
    """The company's types in display order, or None if it hasn't been labeled yet and no built-in list knows it."""
    known = stored() if known is None else known
    seeds = seed_types(name)
    k = norm_company(name)
    if k in known:
        found = seeds | {t for t in known[k] if t in TYPES}
    elif seeds:
        found = seeds
    else:
        return None
    return sorted(found, key=ORDER.index)


def rank(types: list[str] | None, wanted: list[str]) -> int | None:
    """Position of the company's best type in your list (0 = first), len(wanted) if none match, None if unknown yet."""
    if types is None:
        return None
    hits = [wanted.index(t) for t in types if t in wanted]
    return min(hits) if hits else len(wanted)


def summary() -> dict:
    """Open jobs per type, and how many companies are still waiting to be labeled."""
    known = stored()
    jobs = dict.fromkeys(TYPES, 0)
    pending = 0
    for r in db.q("SELECT company, COUNT(*) AS n FROM jobs WHERE status IN ('candidate','scored','queued') GROUP BY company"):
        t = types_for(r["company"], known)
        if t is None:
            pending += 1
            continue
        for x in t:
            jobs[x] += r["n"]
    return {"types": [{"key": k, "label": lab, "hint": hint, "jobs": jobs[k]} for k, (lab, hint) in TYPES.items()],
            "pending": pending}


# ---------------- labeling with Claude ----------------

SCHEMA = obj({"results": arr(obj({"i": I, "types": arr({"type": "string", "enum": ORDER})}))})

SYSTEM = ("You label employers for a job seeker who filters jobs by type of company. For each numbered company, "
          "return every type that fits (zero, one or several):\n"
          + "\n".join(f"- {k}: {hint}" for k, (_, hint) in TYPES.items())
          + """
Rules:
- A division, subsidiary or brand counts as its parent (AWS is Amazon; General Dynamics Information Technology is General Dynamics).
- fortune_500: US companies (or their divisions) on the Fortune 500, i.e. roughly $7.5B+ in annual revenue and publicly reporting it. Mid-size public companies near the cutoff count; private companies that don't publish revenue don't.
- startup only for companies you know are private and venture-backed, never just because a name is unfamiliar.
- Universities, hospitals and nonprofits are none of these, unless government-run (a county agency is government).
- If you don't recognize a company and its name doesn't make the type obvious, return an empty list. Never guess.""")

BATCH = 100


async def classify_pending(limit: int = 1000) -> int:
    """Label companies with open jobs that haven't been labeled yet. Returns how many were labeled."""
    if not llm.available():
        return 0
    known = set(stored())
    todo, seen = [], set()
    for r in db.q("""SELECT company, MIN(title) AS title FROM jobs
                     WHERE status IN ('candidate','scored','queued') AND COALESCE(company,'') != ''
                     GROUP BY company ORDER BY COUNT(*) DESC"""):
        k = norm_company(r["company"])
        if k and k not in known and k not in seen:
            seen.add(k)
            todo.append(r)
    done = 0
    for i in range(0, min(len(todo), limit), BATCH):
        batch = todo[i:i + BATCH]
        listing = "\n".join(f'{n}. {r["company"]} (hiring for "{r["title"]}")' for n, r in enumerate(batch, 1))
        try:
            data = await llm.ajson_call(SYSTEM, listing, SCHEMA, effort="low", model=llm.model_for("sorting"),
                                        max_tokens=8000, purpose="companies")
        except llm.LLMError as e:
            log.warning("couldn't label companies: %s", e)
            break
        got = {r["i"]: r["types"] for r in data["results"] if 1 <= r["i"] <= len(batch)}
        db.many("""INSERT INTO companies(key, name, types, source, updated_at) VALUES(?,?,?,?,?)
                   ON CONFLICT(key) DO UPDATE SET name=excluded.name, types=excluded.types,
                   source=excluded.source, updated_at=excluded.updated_at""",
                [(norm_company(batch[n - 1]["company"]), batch[n - 1]["company"],
                  json.dumps([t for t in types if t in TYPES]), "claude", db.now()) for n, types in got.items()])
        done += len(got)
    if done:
        db.event("companies", f"Sorted {done} companies by type")
    return done
