"""Runtime settings, editable from the UI, stored in the kv table."""
from __future__ import annotations

import secrets
import string
from copy import deepcopy
from typing import Any

from . import db
from .config import ACCOUNT_PASSWORD, GMAIL_ADDRESS, GMAIL_APP_PASSWORD

DEFAULTS: dict[str, Any] = {
    "autopilot": False,            # off until you turn it on
    "interval_minutes": 60,        # how often autopilot looks for new jobs
    "daily_cap": 25,               # max applications sent per day
    "per_company_30d": 3,          # never more than N to one company in 30 days...
    "company_gap_days": 2,         # ...and at least this many days apart
    "min_match": 55,               # minimum match % to apply
    "max_job_age_days": 21,        # ignore postings older than this
    "concurrency": 1,              # applications filled at the same time
    "submit_mode": "auto",         # "auto" = submit; "review" = fill, screenshot, wait for your OK
    "blank_required": "try",       # "try" = submit and let the form decide; "ask" = ask you first
    "headless": False,             # show the browser window (needed for Handshake/Workday sign-in)
    "browser": "",                 # "chrome" or "msedge"; "" = BROWSER_CHANNEL from .env (Chrome)
    "write_cover_letters": True,
    "supported_only": True,        # autopilot only applies on sites it handles well; the rest are for the extension
    "cost_mode": "balanced",       # quality = Opus for everything; balanced = Haiku scores jobs and sorts email; saver = + Sonnet writes answers
    # Where to apply first. Among jobs that clear min_match, earlier sites are scored, queued and sent first.
    "source_priority": ["handshake", "greenhouse", "workday", "ashby", "workable", "lever"],
    # Kinds of company you want, most wanted first (keys of companies.TYPES); [] = no preference.
    "company_types": [],
    "company_types_only": False,   # True = skip companies of other types; False = apply to them after these
    # Which order wins when they disagree: your roles, company types, or sites (Where to look).
    "priority_order": ["role", "company", "site"],
    "sources": {
        "greenhouse": True, "lever": False, "ashby": True, "workable": True, "workday": True,   # Lever: bot CAPTCHAs
        "simplify_feeds": True, "handshake": False,
    },
    "feeds": [
        "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
        "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
    ],
    "handshake": {"base_url": "https://app.joinhandshake.com", "max_per_query": 40},
}


def _gen_password() -> str:
    # Satisfies typical Workday rules: upper, lower, digit, special, 14+ chars.
    alphabet = string.ascii_letters + string.digits
    core = "".join(secrets.choice(alphabet) for _ in range(12))
    return f"Aa{core}9!"


def get() -> dict[str, Any]:
    stored = db.kv_get("settings", {}) or {}
    out = deepcopy(DEFAULTS)
    for k, v in stored.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    if stored.get("priority_first") == "site" and "priority_order" not in stored:   # older two-way setting
        out["priority_order"] = ["site", "role", "company"]
    return out


def update(patch: dict[str, Any]) -> dict[str, Any]:
    stored = db.kv_get("settings", {}) or {}
    for k, v in patch.items():
        if k not in DEFAULTS:
            continue
        if isinstance(v, dict) and isinstance(stored.get(k), dict):
            stored[k].update(v)
        else:
            stored[k] = v
    db.kv_set("settings", stored)
    return get()


# ---- secrets kept locally (never sent anywhere except the site they belong to) ----

def gmail_creds() -> tuple[str, str]:
    s = db.kv_get("gmail", {}) or {}
    return (s.get("address") or GMAIL_ADDRESS, s.get("app_password") or GMAIL_APP_PASSWORD)


def set_gmail(address: str, app_password: str) -> None:
    db.kv_set("gmail", {"address": address.strip(), "app_password": app_password.replace(" ", "")})


def account_password() -> str:
    if ACCOUNT_PASSWORD:
        return ACCOUNT_PASSWORD
    pw = db.kv_get("account_password")
    if not pw:
        pw = _gen_password()
        db.kv_set("account_password", pw)
    return pw
