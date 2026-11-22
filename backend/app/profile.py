"""The candidate profile: everything applications are allowed to say. Plus resume parsing and remembered answers."""
from __future__ import annotations

import re
import shutil
from copy import deepcopy
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from . import db, llm
from .config import RESUMES
from .llm import S, arr, obj

DEFAULT_PROFILE: dict[str, Any] = {
    "contact": {
        "first_name": "", "last_name": "", "preferred_name": "", "email": "", "phone": "",
        "address": "", "city": "", "state": "", "zip": "", "country": "United States",
        "linkedin": "", "github": "", "website": "",
    },
    "looking_for": {
        "roles": [], "levels": ["Entry level", "Internship"], "locations": [], "remote": "Remote or on-site",
        "min_salary": "", "avoid_companies": [], "standing_instructions": "",
        "job_types": ["Full-time", "Internship"],
    },
    "eligibility": {
        "authorized_us": "", "need_sponsorship": "", "willing_relocate": "", "open_hybrid_onsite": "",
        "work_status": "", "years_experience": "", "start_date": "", "notice_period": "",
        "salary_expectation": "", "ok_to_text": "", "felony": "", "drivers_license": "",
        "dismissed": "", "tobacco": "", "relocation_assistance": "", "non_compete": "",
        "clearance": "", "current_employer": "", "degree_status": "As on my resume",
        "no_conflicts": False, "over_18": "",
    },
    "about": "",
    "eeo": {
        "gender": "", "race": "", "hispanic": "", "veteran": "", "orientation": "",
        "transgender": "", "disability": "",
    },
    "languages": [],      # [{name, level}]
    "experience": [],     # [{company, title, location, start, end, current, description}]
    "education": [],      # [{school, degree, field, start, end, gpa}]
    "projects": [],       # [{name, tech, dates, description}]
    "skills": [],
    "resume": None,       # {filename, path, uploaded_at, size}
}

LEVELS = ["Internship", "Entry level", "Mid level", "Senior", "Staff+", "Manager"]


def _merge(base: dict, patch: dict) -> dict:
    out = deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def get() -> dict[str, Any]:
    return _merge(DEFAULT_PROFILE, db.kv_get("profile", {}) or {})


def save(patch: dict[str, Any]) -> dict[str, Any]:
    p = _merge(get(), patch)
    db.kv_set("profile", p)
    return p


def display_name(p: dict | None = None) -> str:
    p = p or get()
    c = p["contact"]
    return (c.get("preferred_name") or c.get("first_name") or "").strip()


def resume_path(p: dict | None = None) -> Path | None:
    p = p or get()
    r = p.get("resume") or {}
    path = Path(r["path"]) if r.get("path") else None
    return path if path and path.exists() else None


def missing_essentials(p: dict | None = None) -> list[str]:
    p = p or get()
    c, e, lf = p["contact"], p["eligibility"], p["looking_for"]
    out = []
    if not resume_path(p):
        out.append("Resume")
    for k, label in [("first_name", "First name"), ("last_name", "Last name"), ("email", "Email"), ("phone", "Phone")]:
        if not c.get(k):
            out.append(label)
    if not lf.get("roles"):
        out.append("Roles you're looking for")
    for k, label in [("authorized_us", "Work authorization"), ("need_sponsorship", "Sponsorship")]:
        if not e.get(k):
            out.append(label)
    return out


def optional_gaps(p: dict | None = None) -> list[str]:
    p = p or get()
    gaps = []
    if not p["eligibility"].get("salary_expectation"):
        gaps.append("Salary expectation")
    if not p.get("about"):
        gaps.append('A few sentences about you (for "why us?" answers)')
    return gaps


# ---------------- resume ----------------

RESUME_SCHEMA = obj({
    "contact": obj({k: S for k in ["first_name", "last_name", "email", "phone", "city", "state", "country",
                                   "linkedin", "github", "website"]}),
    "experience": arr(obj({k: S for k in ["company", "title", "location", "start", "end", "description"]})),
    "education": arr(obj({k: S for k in ["school", "degree", "field", "start", "end", "gpa"]})),
    "projects": arr(obj({k: S for k in ["name", "tech", "dates", "description"]})),
    "skills": arr(S),
    "languages": arr(obj({"name": S, "level": S})),
    "suggested_roles": arr(S),
})

RESUME_SYSTEM = (
    "You extract structured data from a resume. Copy facts exactly as written; never invent or embellish. "
    "Use empty strings for anything not present. Dates as written (e.g. 'Jun 2025', '2027'). "
    "Use 'Present' as end for ongoing roles. Descriptions: keep the original bullets joined by newlines. "
    "languages: spoken languages only if the resume lists them; name without qualifiers (e.g. 'Mandarin Chinese'), "
    "level exactly one of Native, Fluent, Advanced, Intermediate, Beginner, mapped from the resume's wording "
    "(native/bilingual -> Native, fluent -> Fluent, professional working -> Advanced, conversational -> Intermediate, "
    "basic/elementary -> Beginner; unstated -> Intermediate). Never raise the level beyond what's written. "
    "suggested_roles: 3-6 job titles this person is a realistic fit for, given their level."
)


def normalize_url(u: str) -> str:
    u = (u or "").strip()
    if u and not re.match(r"^[a-z]+://", u, re.I) and "." in u and " " not in u:
        u = "https://" + u
    # LinkedIn's canonical form; Workday rejects "https://linkedin.com/in/..." without www.
    u = re.sub(r"^https?://(?:[a-z]{2,3}\.)?linkedin\.com/", "https://www.linkedin.com/", u, flags=re.I)
    return u.rstrip("/") if "linkedin.com/in/" in u else u


def store_resume(src_name: str, data: bytes) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", src_name) or "resume.pdf"
    dest = RESUMES / safe
    dest.write_bytes(data)
    return dest


def parse_resume(path: Path) -> dict[str, Any]:
    """Read the resume with Claude and fill the profile. Only fills blanks; never overwrites your edits."""
    data = llm.json_call(
        RESUME_SYSTEM,
        [llm.pdf_block(path), {"type": "text", "text": "Extract this resume."}],
        RESUME_SCHEMA, effort="medium", purpose="resume",
    )
    p = get()
    for k, v in data["contact"].items():
        if v and not p["contact"].get(k):
            p["contact"][k] = v
    for section in ("experience", "education", "projects"):
        if not p[section] and data[section]:
            rows = data[section]
            if section == "experience":
                for r in rows:
                    r["current"] = (r.get("end", "").lower() in ("present", "current", "now"))
            p[section] = rows
    if not p["skills"]:
        p["skills"] = data["skills"]
    if not p["languages"] and data["languages"]:
        levels = {"Native", "Fluent", "Advanced", "Intermediate", "Beginner"}
        p["languages"] = [{"name": l["name"], "level": l["level"] if l["level"] in levels else "Intermediate"}
                          for l in data["languages"] if l.get("name")]
    for k in ("linkedin", "github", "website"):
        p["contact"][k] = normalize_url(p["contact"].get(k, ""))
    if not p["looking_for"]["roles"]:
        p["looking_for"]["roles"] = data["suggested_roles"]
    db.kv_set("profile", p)
    return p


def attach_resume(filename: str, data: bytes, parse: bool = True) -> dict[str, Any]:
    path = store_resume(filename, data)
    p = save({"resume": {"filename": filename, "path": str(path), "uploaded_at": db.now(), "size": len(data)}})
    if parse:
        p = parse_resume(path)
    return p


def resume_summary(p: dict | None = None) -> str:
    p = p or get()
    r = p.get("resume") or {}
    return (f"{r.get('filename','')} · {len(p['experience'])} roles · {len(p['projects'])} projects · "
            f"{len(p['skills'])} skills") if r else ""


# ---------------- remembered answers ----------------

def qkey(question: str) -> str:
    s = question.lower()
    s = re.sub(r"[\*✱]|\(required\)|\(optional\)", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()[:300]


def remember(question: str, answer: str) -> None:
    k = qkey(question)
    if not k or not str(answer).strip():
        return
    db.x("INSERT INTO remembered(question,qkey,answer,updated_at) VALUES(?,?,?,?) "
         "ON CONFLICT(qkey) DO UPDATE SET answer=excluded.answer, question=excluded.question, updated_at=excluded.updated_at",
         (question.strip(), k, str(answer).strip(), db.now()))


def remembered_all() -> list[dict]:
    return db.q("SELECT id, question, answer, updated_at FROM remembered ORDER BY updated_at DESC")


def recall(question: str) -> str | None:
    """Exact or near-exact match on a previously answered question."""
    k = qkey(question)
    if not k:
        return None
    row = db.q1("SELECT answer FROM remembered WHERE qkey=?", (k,))
    if row:
        return row["answer"]
    best, best_r = None, 0.0
    for r in db.q("SELECT qkey, answer FROM remembered"):
        ratio = SequenceMatcher(None, k, r["qkey"]).ratio()
        if ratio > best_r:
            best, best_r = r["answer"], ratio
    return best if best_r >= 0.9 else None


def copy_resume_for_upload(p: dict | None = None) -> Path | None:
    """Uploads use a clean file name: First_Last_Resume.pdf."""
    p = p or get()
    src = resume_path(p)
    if not src:
        return None
    c = p["contact"]
    name = "_".join(x for x in [c.get("first_name"), c.get("last_name")] if x) or "Resume"
    dest = RESUMES / "upload" / f"{name}_Resume{src.suffix}"
    dest.parent.mkdir(exist_ok=True)
    if not dest.exists() or dest.stat().st_mtime < src.stat().st_mtime:
        shutil.copyfile(src, dest)
    return dest
