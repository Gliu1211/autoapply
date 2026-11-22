"""Paths and environment settings. Runtime settings (changed from the UI) live in the DB, see settings.py."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DATA = Path(os.getenv("AUTOAPPLY_DATA", str(ROOT / "data")))
DB_PATH = DATA / "autoapply.db"
RESUMES = DATA / "resumes"
FILES = DATA / "files"          # generated cover letters etc.
SHOTS = DATA / "shots"          # screenshots of each application
BROWSER_PROFILE = DATA / "browser-profile"
FRONTEND_DIST = ROOT / "frontend" / "dist"

for d in (DATA, RESUMES, FILES, SHOTS, BROWSER_PROFILE):
    d.mkdir(parents=True, exist_ok=True)

# Claude. One model for everything by default; split per role if you want to trade quality for cost.
MODEL = os.getenv("AUTOAPPLY_MODEL", "claude-opus-5-5")
MODEL_SCORING = os.getenv("AUTOAPPLY_MODEL_SCORING", MODEL)
MODEL_ANSWERS = os.getenv("AUTOAPPLY_MODEL_ANSWERS", MODEL)

# Browser: "chrome" (installed Google Chrome), "msedge", or "chromium" (Playwright's bundled build).
BROWSER_CHANNEL = os.getenv("BROWSER_CHANNEL", "chrome")
# Optional: attach to a Chrome you started yourself with --remote-debugging-port=9222 (use a separate profile dir).
BROWSER_CDP_URL = os.getenv("BROWSER_CDP_URL", "")

# Gmail over IMAP (needs an app password: Google Account > Security > App passwords). Can also be set in the UI.
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

# Password used when the agent has to create a Workday (or similar) candidate account. Generated if unset.
ACCOUNT_PASSWORD = os.getenv("ACCOUNT_PASSWORD", "")

HOST = os.getenv("AUTOAPPLY_HOST", "127.0.0.1")
PORT = int(os.getenv("AUTOAPPLY_PORT", "8742"))
