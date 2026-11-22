"""SQLite storage. One shared connection guarded by a lock; every call is short."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Iterable

from .config import DB_PATH

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  uid TEXT UNIQUE NOT NULL,
  source TEXT, ats TEXT, board TEXT, ext_id TEXT,
  company TEXT, title TEXT, location TEXT, remote INTEGER DEFAULT 0,
  url TEXT, apply_url TEXT, description TEXT, salary TEXT,
  posted_at TEXT, discovered_at TEXT,
  score INTEGER, score_reason TEXT, scored_at TEXT,
  status TEXT DEFAULT 'new',
  filter_reason TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS jobs_company ON jobs(company);

CREATE TABLE IF NOT EXISTS applications (
  id INTEGER PRIMARY KEY,
  job_id INTEGER UNIQUE NOT NULL,
  status TEXT NOT NULL,
  status_detail TEXT,
  ats TEXT,
  created_at TEXT, started_at TEXT, sent_at TEXT, updated_at TEXT,
  attempts INTEGER DEFAULT 0,
  answers TEXT DEFAULT '[]',
  blank_required INTEGER DEFAULT 0,
  pending_questions TEXT DEFAULT '[]',
  screenshots TEXT DEFAULT '[]',
  log TEXT DEFAULT '[]',
  response TEXT,
  response_at TEXT,
  cover_letter TEXT,
  approved INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS apps_status ON applications(status);

CREATE TABLE IF NOT EXISTS remembered (
  id INTEGER PRIMARY KEY,
  question TEXT NOT NULL,
  qkey TEXT UNIQUE NOT NULL,
  answer TEXT NOT NULL,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  msg_id TEXT UNIQUE,
  application_id INTEGER,
  company TEXT, sender TEXT, subject TEXT, body TEXT,
  received_at TEXT, category TEXT, summary TEXT,
  is_read INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS outreach (
  id INTEGER PRIMARY KEY,
  application_id INTEGER UNIQUE,
  linkedin_note TEXT, email_subject TEXT, email_body TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS boards (
  ats TEXT NOT NULL, token TEXT NOT NULL, company TEXT,
  status TEXT DEFAULT 'ok', last_fetched TEXT, job_count INTEGER DEFAULT 0,
  PRIMARY KEY (ats, token)
);

CREATE TABLE IF NOT EXISTS accounts (
  host TEXT PRIMARY KEY, email TEXT, password TEXT, created_at TEXT, verified INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS llm_usage (
  id INTEGER PRIMARY KEY, t TEXT, purpose TEXT, model TEXT,
  input INTEGER, output INTEGER, cache_read INTEGER, cache_write INTEGER, cost REAL
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, t TEXT, kind TEXT, msg TEXT
);
"""

JSON_COLS = {"answers", "pending_questions", "screenshots", "log"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA busy_timeout=5000")
            _conn.executescript(SCHEMA)
            cols = {r[1] for r in _conn.execute("PRAGMA table_info(applications)")}
            if "manual" not in cols:   # jobs you picked yourself bypass the autopilot site filter
                _conn.execute("ALTER TABLE applications ADD COLUMN manual INTEGER DEFAULT 0")
        return _conn


def _decode(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k in JSON_COLS & d.keys():
        try:
            d[k] = json.loads(d[k] or "[]")
        except (TypeError, ValueError):
            d[k] = []
    return d


def q(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    with _lock:
        return [_decode(r) for r in conn().execute(sql, tuple(params)).fetchall()]


def q1(sql: str, params: Iterable[Any] = ()) -> dict | None:
    with _lock:
        return _decode(conn().execute(sql, tuple(params)).fetchone())


def x(sql: str, params: Iterable[Any] = ()) -> int:
    """Execute a write; returns lastrowid."""
    with _lock:
        cur = conn().execute(sql, tuple(params))
        return cur.lastrowid or 0


def scalar(sql: str, params: Iterable[Any] = ()) -> Any:
    with _lock:
        r = conn().execute(sql, tuple(params)).fetchone()
        return r[0] if r else None


# ---- key/value ----

def kv_get(key: str, default: Any = None) -> Any:
    r = q1("SELECT value FROM kv WHERE key=?", (key,))
    if not r:
        return default
    try:
        return json.loads(r["value"])
    except ValueError:
        return default


def kv_set(key: str, value: Any) -> None:
    x("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      (key, json.dumps(value)))


# ---- applications ----

def app_update(app_id: int, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now()
    cols, vals = [], []
    for k, v in fields.items():
        cols.append(f"{k}=?")
        vals.append(json.dumps(v) if k in JSON_COLS else v)
    vals.append(app_id)
    x(f"UPDATE applications SET {', '.join(cols)} WHERE id=?", vals)


def app_log(app_id: int, msg: str) -> None:
    with _lock:
        row = q1("SELECT log FROM applications WHERE id=?", (app_id,))
        if not row:
            return
        log = row["log"][-80:]
        log.append({"t": now(), "msg": msg})
        x("UPDATE applications SET log=?, updated_at=? WHERE id=?", (json.dumps(log), now(), app_id))


def event(kind: str, msg: str) -> None:
    x("INSERT INTO events(t,kind,msg) VALUES(?,?,?)", (now(), kind, msg))
    x("DELETE FROM events WHERE id < (SELECT COALESCE(MAX(id),0) - 500 FROM events)")
