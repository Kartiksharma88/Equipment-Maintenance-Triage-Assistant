"""Seed demo data into the REAL database (run deliberately, not a test).

Usage:  python scripts/seed_demo.py [--reset]
  --reset  wipe reports/triage_runs/work_orders first (history is rebuilt fresh).

Creates 6 reports across all machine types with varied urgencies, runs full
triage on each, then exercises the human workflow: confirmations, an approval,
a rejection, and a follow-up round (which also demonstrates superseding).
Safe to re-run without --reset (adds another batch).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as appmod

REPORTS = [
    dict(equipment_type="Centrifugal Pump", equipment_id="PUMP-101",
         issue_description="Grinding noise near the bearings, machine feels hot, vibration getting worse over 2 days",
         operating_events="Stopped once yesterday, restarted fine. Oil changed 5 months ago.",
         sensor_readings={"temperature_c": 96, "vibration_mm_s": 8.2, "oil_level_pct": 22}),
    dict(equipment_type="Air Compressor", equipment_id="COMP-201",
         issue_description="Discharge temperature trip twice this week, sump running hot",
         operating_events="Tripped Monday and Wednesday, reset OK after cooldown.",
         sensor_readings={"temperature_c": 98, "oil_level_pct": 18, "pressure_bar": 7.5}),
    dict(equipment_type="HVAC Unit", equipment_id="HVAC-301",
         issue_description="Weak cooling in the server room since this morning",
         operating_events="Filters changed last month, thermostat untouched.", sensor_readings={}),
    dict(equipment_type="Conveyor Belt", equipment_id="CONV-401",
         issue_description="Belt squealing loudly under full load, left edge looks frayed",
         operating_events="none observed", sensor_readings={"noise_db": 95}),
    dict(equipment_type="Diesel Generator", equipment_id="GEN-501",
         issue_description="Generator cranks slowly but will not start up",
         operating_events="Weekly exercise run failed this morning.", sensor_readings={}),
    dict(equipment_type="Industrial Fan", equipment_id="FAN-601",
         issue_description="Loud rumble and shaking at high speed since yesterday",
         operating_events="none observed", sensor_readings={"vibration_mm_s": 5.5}),
]


def main() -> None:
    from store.db import connect

    if "--reset" in sys.argv:
        with connect() as c:
            c.execute("DELETE FROM work_orders")
            c.execute("DELETE FROM triage_runs")
            c.execute("DELETE FROM reports")
        print("wiped existing reports/runs/work orders")

    c = appmod.app.test_client()

    ids = []
    for rep in REPORTS:
        r = c.post("/api/reports", json=rep)
        assert r.status_code == 201, r.get_json()
        j = r.get_json()
        ids.append((j["report_id"], j["work_order_id"]))
        print(f"report {rep['equipment_id']:10s} -> {j['triage']['priority']['level']} basis {rep['equipment_id']}")

    (r1, w1), (r2, w2), (r3, w3), (r4, w4), (r5, w5), (r6, w6) = ids

    # Human workflow demo data
    c.post(f"/api/reports/{r1}/confirm", json={
        "confirmed_findings": ["Bearing housing 96C verified by IR gun; oil black with metal particles."],
        "confirmed_by": "kartik"})
    c.post(f"/api/reports/{r1}/followup", json={
        "answers": [{"question": "What is the oil colour?", "answer": "Black with metal particles"}]})
    print("PUMP-101: confirmed finding + follow-up round (old draft superseded)")

    print("approve COMP-201:", c.post(
        f"/api/work-orders/{w2}/approve",
        json={"approved_by": "kartik", "decision_note": "Cooler cleaned, oil topped up, verified on site"}).status_code)
    print("reject CONV-401:", c.post(
        f"/api/work-orders/{w4}/reject",
        json={"rejected_by": "kartik", "decision_note": "Duplicate of existing belt replacement job"}).status_code)
    print("approve GEN-501:", c.post(
        f"/api/work-orders/{w5}/approve",
        json={"approved_by": "sana", "decision_note": "Battery bank replaced, start test passed"}).status_code)

    d = c.get("/api/dashboard").get_json()["stats"]
    print("dashboard now:", d)


if __name__ == "__main__":
    main()
