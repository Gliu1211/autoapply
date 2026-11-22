"""Short notes to recruiters / hiring managers for jobs you applied to. Drafts only; you send them."""
from __future__ import annotations

from urllib.parse import quote

from . import db, llm
from . import profile as prof
from .llm import S, obj

SCHEMA = obj({"linkedin_note": S, "email_subject": S, "email_body": S})
SYSTEM = """Write outreach for a candidate who just applied to a job, to send to a recruiter or the hiring manager.
linkedin_note: a LinkedIn connection note, max 280 characters, no greeting name placeholder beyond "Hi," .
email_subject: under 9 words. email_body: 70-120 words, first person, names the role, one specific reason they fit drawn
only from the profile, and a low-pressure ask (a quick chat or pointer to the right person). Sign with the candidate's first name.
No clichés, no em dashes, no exclamation marks, never invent facts."""


def search_links(company: str, title: str) -> dict:
    q = quote(f"{company} recruiter")
    q2 = quote(f"{company} {title.split(',')[0]} hiring manager")
    return {"recruiters": f"https://www.linkedin.com/search/results/people/?keywords={q}",
            "managers": f"https://www.linkedin.com/search/results/people/?keywords={q2}"}


def draft(app_id: int) -> dict:
    row = db.q1("""SELECT a.id, j.company, j.title, j.description FROM applications a JOIN jobs j ON j.id=a.job_id
                   WHERE a.id=?""", (app_id,))
    if not row:
        raise ValueError("no such application")
    p = prof.get()
    import json
    profile = json.dumps({k: p[k] for k in ('contact', 'about', 'experience', 'education', 'projects', 'skills')})
    content = f"<job>\n{row['title']} at {row['company']}\n{(row['description'] or '')[:4000]}\n</job>"
    data = llm.json_call(SYSTEM, content, SCHEMA, effort="low", model=llm.model_for("writing"),
                         context=f"<profile>\n{profile}\n</profile>", purpose="outreach")
    db.x("""INSERT INTO outreach(application_id, linkedin_note, email_subject, email_body, created_at) VALUES(?,?,?,?,?)
            ON CONFLICT(application_id) DO UPDATE SET linkedin_note=excluded.linkedin_note,
            email_subject=excluded.email_subject, email_body=excluded.email_body, created_at=excluded.created_at""",
         (app_id, data["linkedin_note"][:300], data["email_subject"], data["email_body"], db.now()))
    return data
