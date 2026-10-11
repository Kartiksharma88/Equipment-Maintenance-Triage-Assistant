"""Tests for structured logging: JSON shape, key-material whitelist, triage log event."""
import io
import json
import logging

from triage import applog as A


def test_formatter_emits_json_with_required_keys():
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    rec.event = "triage_run"
    rec.report_id = "RPT-1"
    out = json.loads(A.JSONFormatter().format(rec))
    assert out["level"] == "INFO" and out["event"] == "triage_run"
    assert out["report_id"] == "RPT-1" and out["msg"] == "hello world" and "ts" in out


def test_api_key_can_never_be_logged():
    # Even if a caller mistakenly passes key material, the whitelist drops it.
    A.reset_logger()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(A.JSONFormatter())
    lg = A.get_logger()
    lg.addHandler(handler)
    try:
        A.log_event("triage_run", "x", api_key="AIza-SECRET-123", gemini_api_key="AQ-SECRET",
                    key_source="request")
        logged = stream.getvalue()
    finally:
        lg.removeHandler(handler)
        A.reset_logger()
    assert "AIza-SECRET-123" not in logged and "AQ-SECRET" not in logged
    assert '"key_source": "request"' in logged  # source label is fine, value is not


def test_triage_run_writes_log_event_without_key(monkeypatch, tmp_path):
    from triage.kb_seed import SECTIONS
    from triage.reasoning import run_triage
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TRIAGE_LOG_DISABLE", "")
    A.reset_logger()
    try:
        report = {"id": "RPT-t", "equipment_type": "HVAC Unit", "equipment_id": "HVAC-3",
                  "issue_description": "Weak cooling in server room since morning",
                  "operating_events": "none observed", "sensor_readings": {}}
        run_triage(report, SECTIONS)  # no key -> local path, no network
        lines = (tmp_path / "app.log").read_text(encoding="utf-8").splitlines()
    finally:
        A.reset_logger()
    entry = [json.loads(ln) for ln in lines if json.loads(ln).get("event") == "triage_run"]
    assert entry and entry[0]["report_id"] == "RPT-t"
    assert entry[0]["key_source"] == "none" and "duration_ms" in entry[0]


def test_read_recent_empty_and_capped(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert A.read_recent(10) == []
