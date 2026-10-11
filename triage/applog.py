"""Structured JSON logs for the application and the AI workflow (stdlib only).

Every line of data/app.log is one JSON object:
  {"ts", "level", "event", "msg", ...context fields}

Events: app_start, http, report_created, triage_run, confirmed, wo_edited,
        wo_decision, ai_test, kb_search(not logged — read-only).

Security: the API key is NEVER logged — only key_source (request|env|none).
The FIELDS whitelist drops anything else (e.g. an accidental api_key kwarg).

Disable file logging with TRIAGE_LOG_DISABLE=1 (the test suite uses this).
"""
from __future__ import annotations

import json
import logging
import os
import time
from logging.handlers import RotatingFileHandler

from config import data_dir

_SINGLETON: logging.Logger | None = None
LOG_FILE = "app.log"


class JSONFormatter(logging.Formatter):
    # Whitelist: only these extra keys are emitted. Anything else (including
    # any key material) is silently dropped — this is the anti-leak guarantee.
    FIELDS = ("event", "report_id", "work_order_id", "equipment_id",
              "mode", "provider", "model", "key_source",
              "retrieval_status", "sections", "rules_fired", "priority",
              "ai_status", "duration_ms", "method", "path", "http_status",
              "decision", "by", "detail")

    def format(self, record: logging.LogRecord) -> str:
        obj: dict = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
                     "level": record.levelname}
        for f in self.FIELDS:
            v = getattr(record, f, None)
            if v is not None:
                obj[f] = v
        obj["msg"] = record.getMessage()
        if record.exc_info:
            obj["error"] = self.formatException(record.exc_info)[:500]
        return json.dumps(obj, ensure_ascii=False)


def reset_logger() -> None:
    """Close handlers and drop the singleton (used by tests to redirect output)."""
    global _SINGLETON
    if _SINGLETON is not None:
        for h in list(_SINGLETON.handlers):
            try:
                h.close()
            except Exception:
                pass
            _SINGLETON.removeHandler(h)
    _SINGLETON = None
    logging.getLogger("maint_triage").handlers.clear()


def get_logger() -> logging.Logger:
    global _SINGLETON
    if _SINGLETON is not None:
        return _SINGLETON
    lg = logging.getLogger("maint_triage")
    lg.setLevel(logging.INFO)
    lg.propagate = False
    fmt = JSONFormatter()
    if os.environ.get("TRIAGE_LOG_DISABLE") != "1":
        try:
            fh = RotatingFileHandler(str(data_dir() / LOG_FILE), maxBytes=512_000,
                                     backupCount=3, encoding="utf-8")
            fh.setFormatter(fmt)
            lg.addHandler(fh)
        except OSError:
            pass  # read-only FS etc.: console logging still works
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    lg.addHandler(sh)
    _SINGLETON = lg
    return lg


def log_event(event: str, msg: str = "", level: int = logging.INFO, **fields) -> None:
    """Emit one structured event. Unknown fields are dropped (never logged)."""
    try:
        allowed = set(JSONFormatter.FIELDS) - {"event"}
        safe = {k: v for k, v in fields.items() if k in allowed}
        get_logger().log(level, msg, extra={"event": event, **safe})
    except Exception:
        pass  # logging must never break the app


def read_recent(limit: int = 100) -> list[dict]:
    """Newest-first parsed tail of the log file (powers GET /api/logs)."""
    limit = max(1, min(int(limit or 100), 500))
    path = data_dir() / LOG_FILE
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-limit:]:
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except json.JSONDecodeError:
            continue
    return list(reversed(out))
