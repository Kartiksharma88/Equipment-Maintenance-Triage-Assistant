"""Tests for Gemini enhancement: validation, merge guarantees, graceful fallback (no network)."""
import triage.gemini as G
from triage.kb_seed import SECTIONS
from triage.reasoning import apply_ai_enhancement, run_triage


def _base_result():
    report = {"equipment_type": "Centrifugal Pump", "equipment_id": "PUMP-101",
              "issue_description": "Grinding noise and high vibration from bearing housing",
              "operating_events": "Tripped once yesterday",
              "sensor_readings": {"vibration_mm_s": 8.0, "temperature_c": 96}}
    return run_triage(report, SECTIONS), report  # no key -> local path, no network


def test_no_key_means_unavailable_not_failure():
    out = G.enhance_triage({}, [], [], {}, api_key=None)
    assert out["status"] == "unavailable" and "API key" in out["message"]


def test_key_resolution_order(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    assert G.resolve_key("explicit-key") == ("explicit-key", "request")
    assert G.resolve_key(None) == ("env-key", "env")
    monkeypatch.delenv("GEMINI_API_KEY")
    assert G.resolve_key(None) == (None, "none")


def test_status_shapes():
    s = G.gemini_status(None)
    assert set(("mode", "provider", "model", "key_source", "message")) <= set(s)


def test_merge_forces_unconfirmed_and_valid_citations():
    result, _rep = _base_result()
    retrieved = result["retrieved_sections"]
    real_id = retrieved[0]["id"]
    ai_data = {
        "possible_causes": [{"title": "Bearing failure", "note": "Likely.", "confidence": 0.9,
                             "citations": ["KB-FAKE-99"]}],
        "followup_questions": ["What is the oil colour?"],
        "inspection_steps": [{"step": "Check oil sight-glass", "citation": "KB-FAKE-99"}],
        "priority_suggestion": {"level": "P4 - Routine", "rationale": "looks fine"},
        "work_order_note": "AI summary here.",
    }
    merged = apply_ai_enhancement(result, ai_data, retrieved, result["threshold_checks"])
    ai_causes = [c for c in merged["possible_causes"] if c.get("source") == "gemini"]
    assert ai_causes and "UNCONFIRMED" in ai_causes[0]["title"]
    assert "KB-FAKE-99" not in ai_causes[0]["citations"]  # fake citations rejected
    assert real_id in ai_causes[0]["citations"] or "EVENT" in ai_causes[0]["citations"]
    # rules fired critical -> AI must NOT de-escalate to P4
    assert merged["priority"]["level"].startswith("P1")
    assert merged["work_order_draft"]["status"] == "draft"
    assert "AI summary here." in merged["work_order_draft"]["description"]


def test_merge_escalation_allowed():
    result, _rep = _base_result()
    # simulate a mild local result, AI suggests P1
    result["priority"] = {"level": "P4 - Routine", "rationale": "local calm"}
    ai_data = {"priority_suggestion": {"level": "P1 - Emergency", "rationale": "smoke risk"}}
    merged = apply_ai_enhancement(result, ai_data, result["retrieved_sections"], [])
    assert merged["priority"]["level"] == "P1 - Emergency"
    assert merged["priority"]["rules_floor"] == "P4 - Routine"


def test_merge_ignores_garbage():
    result, _rep = _base_result()
    n_causes = len(result["possible_causes"])
    merged = apply_ai_enhancement(result, {"possible_causes": "nonsense", "priority_suggestion": {"level": "P9"}}, result["retrieved_sections"], result["threshold_checks"])
    assert len(merged["possible_causes"]) == n_causes
    assert merged["priority"]["level"].startswith("P1")  # unchanged
    assert merged["work_order_draft"]["status"] == "draft"


def test_confidence_clamped():
    result, _rep = _base_result()
    ai_data = {"possible_causes": [{"title": "X", "note": "Y", "confidence": 99}]}
    merged = apply_ai_enhancement(result, ai_data, result["retrieved_sections"], [])
    assert merged["possible_causes"][-1]["confidence"] == 1.0
