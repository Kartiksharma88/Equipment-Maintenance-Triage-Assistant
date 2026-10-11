"""Tests: deterministic rules, retrieval failures, approval gate, evidence labelling."""
from triage.kb_seed import SECTIONS
from triage.reasoning import run_triage
from triage.retrieval import retrieve
from triage.rules import data_quality, threshold_checks


def test_temp_critical_rule():
    out = threshold_checks({"temperature_c": 97}, "Centrifugal Pump")
    assert any(c["rule_id"] == "TEMP_CRIT" and c["severity"] == "critical" for c in out)


def test_vibration_warn_rule():
    out = threshold_checks({"vibration_mm_s": 5.0}, "Centrifugal Pump")
    assert any(c["rule_id"] == "VIB_WARN" for c in out)


def test_oil_critical_rule():
    out = threshold_checks({"oil_level_pct": 10}, "Air Compressor")
    assert any(c["rule_id"] == "OIL_LOW_CRIT" for c in out)


def test_no_false_positive_on_empty():
    assert threshold_checks({}, "Centrifugal Pump") == []


def test_missing_sensors_reported():
    dq = data_quality({}, "pump hot", "ran all day")
    assert "temperature_c" in dq["missing"]


def test_conflict_hot_text_cold_sensor():
    dq = data_quality({"temperature_c": 60}, "bearings overheating and smoking", "")
    assert len(dq["conflicts"]) >= 1


def test_invalid_sensor_flagged():
    dq = data_quality({"temperature_c": 9999}, "hot", "")
    assert len(dq["invalid"]) >= 1


def test_retrieval_ranks_relevant_section():
    r = retrieve("pump grinding vibration bearing noise", SECTIONS, equipment_type="Centrifugal Pump")
    assert r["status"] == "ok"
    assert r["results"][0]["id"] == "KB-PUMP-02"


def test_retrieval_failure_is_explicit():
    r = retrieve("", SECTIONS)
    assert r["status"] == "failure" and "message" in r
    r2 = retrieve("pump vibration", [])
    assert r2["status"] == "failure"


def test_triage_labels_causes_unconfirmed_and_draft_only():
    report = {"equipment_type": "Centrifugal Pump", "equipment_id": "PUMP-101",
              "issue_description": "Grinding noise and high vibration from bearing housing",
              "operating_events": "Tripped once yesterday",
              "sensor_readings": {"vibration_mm_s": 8.0, "temperature_c": 96}}
    t = run_triage(report, SECTIONS)
    assert t["work_order_draft"]["status"] == "draft"  # safety: never auto-approved
    assert t["confirmed_findings"] == []  # only humans confirm
    assert all("UNCONFIRMED" in c["title"] or "unconfirmed" in c["status"] for c in t["possible_causes"])
    assert t["priority"]["level"].startswith("P1")  # critical temp + critical vibration
    assert len(t["followup_questions"]) >= 1
    assert all("citation" in s for s in t["inspection_steps"])


def test_triage_handles_missing_sensors_gracefully():
    report = {"equipment_type": "HVAC Unit", "equipment_id": "HVAC-3",
              "issue_description": "Weak cooling in server room since morning",
              "operating_events": "", "sensor_readings": {}}
    t = run_triage(report, SECTIONS)
    assert t["retrieval_status"]["status"] == "ok"
    assert t["priority"]["level"] in ("P3 - Planned", "P4 - Routine")
    assert any("missing" in q.lower() or "provide" in q.lower() for q in t["followup_questions"])


def test_no_remote_control_endpoint():
    import app as appmod
    rules = sorted(str(r) for r in appmod.app.url_map.iter_rules())
    joined = "\n".join(rules)
    assert "control" not in joined and "actuate" not in joined and "remote" not in joined


def test_operating_events_required():
    from app import validate_report
    base = {"equipment_type": "Centrifugal Pump", "equipment_id": "PUMP-1",
            "issue_description": "Grinding noise from the bearing housing area",
            "operating_events": "", "sensor_readings": {}}
    _clean, problem = validate_report(dict(base))
    assert problem and "operating_events" in problem
    _clean2, problem2 = validate_report(dict(base, operating_events="none observed"))
    assert problem2 is None


def test_reports_list_enriched_cards():
    import app as appmod
    c = appmod.app.test_client()
    r = c.post("/api/reports", json={
        "equipment_type": "Centrifugal Pump", "equipment_id": "PUMP-CARD",
        "issue_description": "Grinding noise from the bearing housing area",
        "operating_events": "none observed", "sensor_readings": {"vibration_mm_s": 8.0}})
    assert r.status_code == 201
    rid = r.get_json()["report_id"]
    reports = c.get("/api/reports").get_json()["reports"]
    mine = [x for x in reports if x["id"] == rid][0]
    assert mine["latest_priority"].startswith("P")  # card shows urgency
    assert mine["triage_runs"] >= 1
    assert mine["work_orders"] and mine["work_orders"][0]["status"] == "draft"


def test_work_order_sections_structured_and_trimmed():
    import app as appmod
    c = appmod.app.test_client()
    r = c.post("/api/reports", json={
        "equipment_type": "Industrial Fan", "equipment_id": "FAN-SEC",
        "issue_description": "Grinding noise from the fan housing for two days",
        "operating_events": "stopped once yesterday", "sensor_readings": {}}).get_json()
    c.post(f"/api/reports/{r['report_id']}/followup", json={
        "answers": [{"question": "Q1?", "answer": "A1"}]})
    t2 = c.get(f"/api/reports/{r['report_id']}").get_json()["triage"]
    sec = t2["work_order_draft"]["sections"]
    assert sec["observed"].startswith("Grinding noise")
    assert "Follow-up answers:" not in sec["events"]  # Q&A blob trimmed out
    assert "follow-up" in sec["events"]  # ...but its presence noted
    assert sec["possible_causes"] and all("UNCONFIRMED" in x for x in sec["possible_causes"])
    wo = c.get(f"/api/work-orders/{r['work_order_id']}").get_json()["work_order"]
    assert wo["sections"]["observed"] == sec["observed"]  # persisted, not just in triage
    # NOTE: the follow-up superseded the original draft — edit the active one.
    active = [w for w in c.get(f"/api/reports/{r['report_id']}").get_json()["work_orders"]
              if w["status"] == "draft"][0]
    c.patch(f"/api/work-orders/{active['id']}",
            json={"description": "hand-edited", "edited_by": "t"})
    wo2 = c.get(f"/api/work-orders/{active['id']}").get_json()["work_order"]
    assert wo2["sections"] == {} and wo2["description"] == "hand-edited"  # edit clears snapshot


def test_inspection_steps_have_title_detail_citation():
    report = {"equipment_type": "Centrifugal Pump", "equipment_id": "PUMP-STEP",
              "issue_description": "Grinding noise and high vibration from bearing housing",
              "operating_events": "none observed",
              "sensor_readings": {"vibration_mm_s": 8.0}}
    t = run_triage(report, SECTIONS)
    for s in t["inspection_steps"]:
        assert "citation" in s and s["citation"]
        assert s["step"] and len(s["step"]) <= 230  # short scannable title
        assert "detail" in s
    kb_steps = [s for s in t["inspection_steps"] if s["citation"].startswith("KB-")]
    assert kb_steps
    # citation chip carries the KB id, so it must not be duplicated inside the title
    assert not any(s["citation"] in s["step"] for s in kb_steps)
    # details end cleanly (word boundary + ellipsis), never mid-word fragments
    for s in kb_steps:
        if s["detail"].endswith("…"):
            assert s["detail"][-2] != " " and len(s["detail"]) <= 201


def test_decided_work_order_carries_decision_audit():
    import app as appmod
    c = appmod.app.test_client()
    r = c.post("/api/reports", json={
        "equipment_type": "Conveyor Belt", "equipment_id": "CONV-DEC",
        "issue_description": "Belt squealing loudly under full load today",
        "operating_events": "none observed", "sensor_readings": {}}).get_json()
    wid = r["work_order_id"]
    c.post(f"/api/work-orders/{wid}/approve",
           json={"approved_by": "tech-Z", "decision_note": "verified on site"})
    wo = c.get(f"/api/work-orders/{wid}").get_json()["work_order"]
    assert wo["status"] == "approved"
    audit = [h for h in wo["history"] if "APPROVED" in h["action"]]
    assert audit and audit[-1]["by"] == "tech-Z"  # UI decision banner reads this
    assert wo["decision_note"] == "verified on site"


def test_work_order_carries_equipment_for_photo():
    import app as appmod
    c = appmod.app.test_client()
    r = c.post("/api/reports", json={
        "equipment_type": "Diesel Generator", "equipment_id": "GEN-PH",
        "issue_description": "Generator cranks slowly and will not start up",
        "operating_events": "none observed", "sensor_readings": {}}).get_json()
    wo = c.get(f"/api/work-orders/{r['work_order_id']}").get_json()["work_order"]
    assert wo["equipment_type"] == "Diesel Generator" and wo["equipment_id"] == "GEN-PH"
    lst = c.get("/api/work-orders").get_json()["work_orders"]
    assert any(w["equipment_type"] == "Diesel Generator" for w in lst)


def test_followup_supersedes_old_draft():
    import app as appmod
    c = appmod.app.test_client()
    r = c.post("/api/reports", json={
        "equipment_type": "Diesel Generator", "equipment_id": "GEN-SUP",
        "issue_description": "Generator cranks slowly and will not start up",
        "operating_events": "none observed", "sensor_readings": {}}).get_json()
    first_wo = r["work_order_id"]
    r2 = c.post(f"/api/reports/{r['report_id']}/followup", json={
        "answers": [{"question": "Battery age?", "answer": "4 years"}]}).get_json()
    assert r2["ok"]
    wos = c.get(f"/api/reports/{r['report_id']}").get_json()["work_orders"]
    by_id = {w["id"]: w["status"] for w in wos}
    assert by_id[first_wo] == "superseded"  # old draft retired, kept for history
    new_wo = [wid for wid, st in by_id.items() if st == "draft"]
    assert len(new_wo) == 1  # exactly one active draft per report
    assert c.post(f"/api/work-orders/{first_wo}/approve",
                  json={"approved_by": "t"}).status_code == 409  # decided/superseded locked
    sup = c.get("/api/work-orders?status=superseded").get_json()["work_orders"]
    assert any(w["id"] == first_wo for w in sup)


def test_equipment_registry_and_id_normalisation():
    import app as appmod
    c = appmod.app.test_client()
    r = c.post("/api/reports", json={
        "equipment_type": "Conveyor Belt", "equipment_id": "  conv-9 ",
        "issue_description": "Belt squealing loudly under full load today",
        "operating_events": "none observed", "sensor_readings": {}}).get_json()
    rep = c.get(f"/api/reports/{r['report_id']}").get_json()["report"]
    assert rep["equipment_id"] == "CONV-9"  # normalised: trimmed, uppercased, no spaces
    reg = c.get("/api/equipment").get_json()["equipment"]
    assert any(e["equipment_id"] == "CONV-9" and e["equipment_type"] == "Conveyor Belt" for e in reg)
