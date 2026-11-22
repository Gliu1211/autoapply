"""Endpoints for the Chrome extension: it reads a form in your own browser, asks here what to put in each box,
fills it, and reports back. Same answer logic as the autopilot (apply.engine.decide)."""
from __future__ import annotations

import re

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse

from . import db
from . import profile as prof
from .apply.answers import cover_letter, cover_letter_html, cover_letter_path, pick_option, rule_answer
from .apply.engine import decide
from .sources import add_job_url, hydrate
from .util import detect_ats

router = APIRouter(prefix="/api/ext")
CHOICE = ("select", "radio", "buttons", "listbox", "combobox", "checkboxes", "multiselect")


def _job_for(url: str, title: str, page_text: str) -> dict:
    clean = url.split("#")[0]
    job = db.q1("SELECT * FROM jobs WHERE url=? OR apply_url=? OR url=? OR apply_url=? LIMIT 1",
                (url, url, clean, clean))
    if not job:
        info = detect_ats(url)
        if info["ext_id"]:
            job = db.q1("SELECT * FROM jobs WHERE ats=? AND ext_id=? LIMIT 1", (info["ats"], info["ext_id"]))
    if not job:
        job = db.q1("SELECT * FROM jobs WHERE id=?", (add_job_url(url),))
        t = re.sub(r"\s*[|\-–]\s*(job application|careers?|apply|greenhouse|lever|ashby|workday).*$", "", title or "", flags=re.I).strip()
        if t and job["title"] in ("", "Job"):
            db.x("UPDATE jobs SET title=? WHERE id=?", (t[:200], job["id"]))
            job["title"] = t[:200]
    if not job.get("description") and page_text:
        # The page the extension is on usually shows the posting; good enough context for written answers.
        db.x("UPDATE jobs SET description=? WHERE id=?", (page_text[:12000], job["id"]))
        job["description"] = page_text[:12000]
    return job


def _app_for(job: dict) -> dict:
    a = db.q1("SELECT * FROM applications WHERE job_id=?", (job["id"],))
    if a:
        if a["status"] not in ("sent",):
            db.app_update(a["id"], status="in_browser", status_detail="Being filled in your browser")
        return db.q1("SELECT * FROM applications WHERE id=?", (a["id"],))
    app_id = db.x("INSERT INTO applications(job_id, status, status_detail, ats, created_at, updated_at) VALUES(?,?,?,?,?,?)",
                  (job["id"], "in_browser", "Being filled in your browser", job.get("ats"), db.now(), db.now()))
    return db.q1("SELECT * FROM applications WHERE id=?", (app_id,))


@router.get("/ping")
def ping():
    p = prof.get()
    return {"ok": True, "name": prof.display_name(p), "missing": prof.missing_essentials(p)}


@router.post("/answer")
async def answer(body: dict = Body(...)):
    """Body: {url, title, page_text, fields:[the same field dicts the in-page reader produces]}."""
    fields = body.get("fields") or []
    if not fields:
        raise HTTPException(400, "No form fields found on this page")
    p = prof.get()
    missing = prof.missing_essentials(p)
    if "Resume" in missing:
        raise HTTPException(400, "Upload your resume in AutoApply's Profile first")
    job = _job_for(body.get("url", ""), body.get("title", ""), body.get("page_text", ""))
    if not job.get("description"):
        job = await hydrate(job)
    app = _app_for(job)
    cache = {prof.qkey(a["question"]): a for a in app.get("answers") or []}
    # Fields the site already filled (e.g. from its own resume reader) are kept unless they contradict the profile.
    keep = {}
    for f in [f for f in fields if f.get("current")]:
        v, src = rule_answer(f, p, job)
        if src == "profile" and v not in (None, "", []) and pick_option(v, [str(f["current"])]) is None:
            f["current"] = ""          # wrong: answer it again below
        else:
            keep[f["id"]] = {"keep": True, "source": "prefilled"}
    fields = [f for f in fields if f["id"] not in keep]
    decisions = await decide(fields, p, job, cache, resume_on_form=bool(body.get("resume_on_form")))
    out = dict(keep)
    for f in fields:
        v, src = decisions.get(f["id"], (None, ""))
        if src == "needs_user" or v in (None, "", []):
            out[f["id"]] = {"value": None, "source": src or "none", "needs_user": bool(f.get("required"))}
            continue
        if v in ("__RESUME__", "__AUTOFILL__"):
            out[f["id"]] = {"file": "resume", "source": src, "autofill": v == "__AUTOFILL__"}
            continue
        if v == "__COVER__":
            out[f["id"]] = {"file": "cover", "source": src}
            continue
        opts = f.get("options") or []
        if f["kind"] in CHOICE and opts:
            vals = v if isinstance(v, list) else [v]
            if f["kind"] in ("checkboxes", "multiselect"):
                picked = [o for x in vals if (o := pick_option(x, opts))]
                v = list(dict.fromkeys(picked)) or vals
            else:
                v = pick_option(vals, opts) or vals
        out[f["id"]] = {"value": v, "source": src}
    return {"app_id": app["id"], "job": {"id": job["id"], "company": job.get("company"), "title": job.get("title")},
            "answers": out}


@router.get("/resume")
def resume():
    path = prof.copy_resume_for_upload()
    if not path:
        raise HTTPException(404, "No resume uploaded")
    return FileResponse(path, filename=path.name, media_type="application/pdf")


@router.get("/cover/{app_id}")
def cover(app_id: int):
    """Tailored cover letter PDF for this application (written once, then reused)."""
    from .apply.browser import browser
    from .autopilot import worker
    row = db.q1("SELECT a.id, a.cover_letter, j.* FROM applications a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (app_id,))
    if not row:
        raise HTTPException(404)
    p = prof.get()
    path = cover_letter_path(app_id, p)
    if not path.exists():
        async def make():
            body = row["cover_letter"] or await cover_letter(p, row)
            db.app_update(app_id, cover_letter=body)
            await browser.render_pdf(cover_letter_html(p, row, body), path)
        try:
            worker.submit(make()).result(timeout=180)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"Couldn't write the cover letter: {e}")
    return FileResponse(path, filename=path.name, media_type="application/pdf")


@router.post("/result")
def result(body: dict = Body(...)):
    """Body: {app_id, status: "filled" | "sent", answers:[{question, answer, source, required, kind, ok}]}."""
    app_id = int(body.get("app_id") or 0)
    a = db.q1("SELECT * FROM applications WHERE id=?", (app_id,))
    if not a:
        raise HTTPException(404)
    answers = body.get("answers")
    fields = {}
    if isinstance(answers, list):
        fields["answers"] = answers[:300]
        fields["blank_required"] = sum(1 for x in answers if x.get("required") and not x.get("answer"))
    if body.get("status") == "sent":
        fields.update(status="sent", sent_at=db.now(), status_detail="Sent from your browser")
        db.x("UPDATE jobs SET status='applied' WHERE id=?", (a["job_id"],))
        db.event("apply", "Sent from your browser")
    else:
        blanks = fields.get("blank_required", 0)
        fields.update(status="in_browser",
                      status_detail=f"Filled in your browser; {blanks} required left for you" if blanks else "Filled in your browser; press Submit")
    db.app_update(app_id, **fields)
    db.app_log(app_id, fields["status_detail"])
    return {"ok": True}
