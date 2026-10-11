"""SQLite persistence: reports, triage runs, work orders. History preserved, never deleted."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid

from config import data_dir


def _db_path():
    # Resolved per call (not at import) so tests can redirect DATA_DIR
    # to a temp folder — the suite must NEVER touch the real database.
    return data_dir() / "app.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
  id TEXT PRIMARY KEY,
  created_at REAL NOT NULL,
  equipment_type TEXT NOT NULL,
  equipment_id TEXT NOT NULL,
  issue_description TEXT NOT NULL,
  operating_events TEXT NOT NULL DEFAULT '',
  sensor_readings TEXT NOT NULL DEFAULT '{}',
  followup_answers TEXT NOT NULL DEFAULT '[]',
  confirmed_findings TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS triage_runs (
  id TEXT PRIMARY KEY,
  report_id TEXT NOT NULL,
  created_at REAL NOT NULL,
  result TEXT NOT NULL,
  FOREIGN KEY(report_id) REFERENCES reports(id)
);
CREATE TABLE IF NOT EXISTS work_orders (
  id TEXT PRIMARY KEY,
  report_id TEXT NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  title TEXT NOT NULL,
  priority TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  inspection_steps TEXT NOT NULL DEFAULT '[]',
  safety_notes TEXT NOT NULL DEFAULT '',
  citations TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'draft',
  decision_note TEXT NOT NULL DEFAULT '',
  history TEXT NOT NULL DEFAULT '[]',
  sections TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(report_id) REFERENCES reports(id)
);
"""


def connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as c:
        c.executescript(SCHEMA)
        # Migration for DBs created before the sections column existed.
        try:
            c.execute("ALTER TABLE work_orders ADD COLUMN sections TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # column already there


def now() -> float:
    return time.time()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def to_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def from_json(s: str, default):
    try:
        return json.loads(s) if s else default
    except Exception:
        return default
