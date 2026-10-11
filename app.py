"""Equipment Maintenance Triage Assistant — Flask API + static frontend.

Safety invariants (enforced here and tested):
  1. AI output is advisory only. Work orders are ALWAYS created as status='draft'.
  2. Only explicit human endpoints (/approve, /reject, PATCH) change work-order status.
  3. There is intentionally NO endpoint for remote equipment control.
"""
from __future__ import annotations

import json
import time as _time
from pathlib import Path

from flask import Flask, g, jsonify, request, send_from_directory

from config import EQUIPMENT_TYPES, PRIORITY_DEFINITIONS, SAFETY_NOTICE, SENSOR_SCHEMA, THRESHOLDS, server_config
from store.db import connect, from_json, init_db, new_id, now, to_json
from triage import applog as _applog
from triage import gemini as _gemini
from triage import openrouter as _openrouter
from triage.llm import provider_name as _provider_name
from triage.kb_seed import load_kb
from triage.llm import llm_status
from triage.reasoning import run_triage
from triage.retrieval import retrieve

BASE = Path(__file__).resolve().parent

app = Flask(__name__, static_folder=str(BASE / "static"), static_url_path="")
init_db()
KB = load_kb()
_applog.log_event("app_start", f"KB={len(KB)} sections", sections=len(KB),
                  mode=llm_status().get("mode"), provider=llm_status().get("provider"))


@app.before_request
def _start_timer():
    g._t0 = _time.perf_counter()


@app.after_request
def _access_log(response):
    # Structured HTTP log for API calls (static assets + health excluded to cut noise).
    if request.path.startswith("/api/") and request.path not in ("/api/health", "/api/logs"):
        _applog.log_event("http", "", method=request.method, path=request.path,
                          http_status=response.status_code,
                          duration_ms=int((_time.perf_counter() - g.get("_t0", _time.perf_counter())) * 1000))
    return response


# ---------- helpers ----------
def err(message: str, code: int = 400):
    return jsonify({"ok": False, "error": message}), code


def validate_report(payload: dict) -> tuple[dict, str | None]:
    if not isinstance(payload, dict):
        return {}, "Request body must be JSON."
    etype = (payload.get("equipment_type") or "").strip()
    eid = "".join((payload.get("equipment_id") or "").split()).upper()
    issue = (payload.get("issue_description") or "").strip()
    events = (payload.get("operating_events") or "")
    sensors = payload.get("sensor_readings") or {}
    if etype not in EQUIPMENT_TYPES:
        return {}, f"equipment_type must be one of: {', '.join(EQUIPMENT_TYPES)}."
    if not eid:
        return {}, "equipment_id is required (e.g. PUMP-101)."
    if len(eid) > 64:
        return {}, "equipment_id too long (max 64 chars)."
    if not issue or len(issue) < 10:
        return {}, "issue_description is required (min 10 characters) — describe symptoms."
    if len(issue) > 4000:
        return {}, "issue_description too long (max 4000 chars)."
    if not isinstance(events, str):
        return {}, "operating_events must be text."
    if len(events.strip()) < 3:
        return {}, ("operating_events is required — describe recent starts, trips, load changes, alarms, "
                    "or recent service (write 'none observed' if truly nothing happened).")
    if len(events) > 4000:
        return {}, "operating_events too long (max 4000 chars)."
    if not isinstance(sensors, dict):
        return {}, "sensor_readings must be an object like {\"temperature_c\": 88}."
    clean: dict = {}
    for k, v in sensors.items():
        if k not in SENSOR_SCHEMA:
            return {}, f"Unknown sensor '{k}'. Allowed: {', '.join(sorted(SENSOR_SCHEMA))}."
        if v in (None, ""):
            continue
        try:
            clean[k] = float(v)
        except (TypeError, ValueError):
            return {}, f"Sensor '{k}' must be a number."
    return {"equipment_type": etype, "equipment_id": eid, "issue_description": issue,
            "operating_events": events.strip(), "sensor_readings": clean}, None


def do_triage(report_row: dict, ai_key: str | None = None,
              ai_provider: str | None = None) -> dict:
    return run_triage(report_row, KB, ai_key=ai_key, ai_provider=ai_provider)


def request_ai_key(payload: dict | None = None) -> str | None:
    """API key resolution: per-request header > per-request body field > env.

    Accepts generic (X-AI-Key / ai_api_key) or provider-specific header/field
    names. The key is used for the single call and never stored or logged."""
    for header in ("X-AI-Key", "X-OpenRouter-Key", "X-Gemini-Api-Key"):
        header_key = (request.headers.get(header) or "").strip()
        if header_key:
            return header_key
    if isinstance(payload, dict):
        for field in ("ai_api_key", "openrouter_api_key", "gemini_api_key"):
            body_key = (payload.get(field) or "").strip()
            if body_key:
                return body_key
    from triage.llm import provider_name as _prov
    if _prov() == "openrouter":
        env_key, _source = _openrouter.resolve_key(None)
    else:
        env_key, _source = _gemini.resolve_key(None)
    return env_key


# kept for backwards compatibility (delegates to request_ai_key)
def request_gemini_key(payload: dict | None = None) -> str | None:
    return request_ai_key(payload)


def save_triage_and_workorder(report_id: str, triage: dict) -> dict:
    wo = triage["work_order_draft"]
    wo_id = new_id("WO")
    ts = now()
    with connect() as c:
        c.execute("INSERT INTO triage_runs (id, report_id, created_at, result) VALUES (?,?,?,?)",
                  (new_id("TR"), report_id, ts, to_json(triage)))
        # One active draft per report: older drafts are SUPERSEDED (kept for
        # history, hidden from the queue) instead of piling up.
        for row in c.execute("SELECT id, history FROM work_orders WHERE report_id=? AND status='draft'",
                             (report_id,)).fetchall():
            hist = from_json(row["history"], []) + [
                {"at": ts, "by": "system",
                 "action": f"SUPERSEDED by newer triage draft {wo_id} (kept for history)"}]
            c.execute("UPDATE work_orders SET status='superseded', updated_at=?, history=? WHERE id=?",
                      (ts, to_json(hist), row["id"]))
        c.execute(
            "INSERT INTO work_orders (id, report_id, created_at, updated_at, title, priority, description,"
            " inspection_steps, safety_notes, citations, status, decision_note, history, sections)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (wo_id, report_id, ts, ts, wo["title"], wo["priority"], wo["description"],
             to_json(wo["inspection_steps"]), wo["safety_notes"], to_json(wo["citations"]),
             "draft", "", to_json([{"at": ts, "by": "system", "action": "draft created by triage (requires human review)"}]),
             to_json(wo.get("sections") or {})))
    return {"work_order_id": wo_id}


# ---------- pages ----------
@app.get("/")
def index():
    return send_from_directory(str(BASE / "static"), "index.html")


# ---------- meta / AI ----------
@app.get("/api/health")
def health():
    return jsonify({"ok": True, "kb_sections": len(KB), "ai": llm_status(), "safety": SAFETY_NOTICE})


@app.get("/api/ai/status")
def ai_status():
    """Which AI mode the server would use (active provider via AI_PROVIDER env)."""
    st = llm_status(provider=_provider_name())
    return jsonify({"ok": True, **st})


@app.post("/api/ai/test")
def ai_test():
    """Validate a provider key without running triage. Key never stored/logged."""
    payload = request.get_json(silent=True) or {}
    provider = _provider_name(payload.get("ai_provider"))
    key = ""
    for field in ("ai_api_key", "openrouter_api_key", "gemini_api_key"):
        if (payload.get(field) or "").strip():
            key = payload[field].strip()
            break
    if not key:
        key = (request.headers.get("X-AI-Key") or request.headers.get("X-OpenRouter-Key")
               or request.headers.get("X-Gemini-Api-Key") or "").strip()
    if not key:
        key = request_ai_key(payload)
    if not key:
        return jsonify({"ok": False, "error": "No API key provided. Set the provider key server-side to test."}), 400
    out = _openrouter.ping(key) if provider == "openrouter" else _gemini.ping(key)
    _applog.log_event("ai_test", out["message"][:200], ai_status=("ok" if out["ok"] else "failure"))
    return jsonify({"ok": out["ok"], "message": out["message"], "provider": provider})


@app.get("/api/dashboard")
def dashboard():
    with connect() as c:
        n_reports = c.execute("SELECT COUNT(*) c FROM reports").fetchone()["c"]
        n_runs = c.execute("SELECT COUNT(*) c FROM triage_runs").fetchone()["c"]
        by_status = {r["status"]: r["c"] for r in c.execute(
            "SELECT status, COUNT(*) c FROM work_orders GROUP BY status").fetchall()}
        recent = [dict(r) for r in c.execute(
            "SELECT id, created_at, equipment_type, equipment_id, issue_description FROM reports"
            " ORDER BY created_at DESC LIMIT 5").fetchall()]
        drafts = [dict(r) for r in c.execute(
            "SELECT id, title, priority, updated_at FROM work_orders WHERE status='draft'"
            " ORDER BY updated_at DESC LIMIT 5").fetchall()]
    return jsonify({"ok": True, "stats": {
        "reports": n_reports, "triage_runs": n_runs,
        "draft": by_status.get("draft", 0), "approved": by_status.get("approved", 0),
        "rejected": by_status.get("rejected", 0), "kb_sections": len(KB)},
        "recent_reports": recent, "pending_drafts": drafts, "ai": llm_status()})


@app.get("/api/meta")
def meta():
    return jsonify({"ok": True, "equipment_types": EQUIPMENT_TYPES, "sensor_schema": SENSOR_SCHEMA,
                    "thresholds": THRESHOLDS, "priorities": PRIORITY_DEFINITIONS, "safety": SAFETY_NOTICE})


@app.get("/api/equipment")
def equipment():
    """Known machines registry (distinct IDs seen in reports) for the picker."""
    with connect() as c:
        rows = c.execute("SELECT equipment_type, equipment_id, COUNT(*) n, MAX(created_at) last"
                         " FROM reports GROUP BY equipment_type, equipment_id"
                         " ORDER BY equipment_type, equipment_id").fetchall()
    return jsonify({"ok": True, "equipment": [dict(r) for r in rows]})


@app.get("/api/kb")
def kb_search():
    q = request.args.get("q", "")
    et = request.args.get("equipment_type", "") or None
    if et and et not in EQUIPMENT_TYPES:
        return err(f"Unknown equipment_type '{et}'.")
    out = retrieve(q or (et or ""), KB, equipment_type=et, top_k=8)
    return jsonify({"ok": True, **out})


# ---------- reports ----------
@app.post("/api/reports")
def create_report():
    payload = request.get_json(silent=True) or {}
    clean, problem = validate_report(payload)
    if problem:
        return err(problem)
    rid = new_id("RPT")
    ts = now()
    with connect() as c:
        c.execute("INSERT INTO reports (id, created_at, equipment_type, equipment_id, issue_description,"
                  " operating_events, sensor_readings) VALUES (?,?,?,?,?,?,?)",
                  (rid, ts, clean["equipment_type"], clean["equipment_id"], clean["issue_description"],
                   clean["operating_events"], to_json(clean["sensor_readings"])))
    with connect() as c:
        row = dict(c.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone())
    report = {**row, "sensor_readings": from_json(row["sensor_readings"], {})}
    try:
        triage = do_triage(report, request_ai_key(payload))
    except Exception as exc:  # AI/retrieval failures shown clearly, report still saved
        return jsonify({"ok": False, "report_id": rid,
                        "error": f"Triage failed clearly (report saved): {exc}. Open the report and retry."}), 500
    ids = save_triage_and_workorder(rid, triage)
    _applog.log_event("report_created", f"{clean['equipment_type']} {clean['equipment_id']}",
                      report_id=rid, equipment_id=clean["equipment_id"],
                      priority=triage["priority"]["level"],
                      rules_fired=[c["rule_id"] for c in triage["threshold_checks"]],
                      retrieval_status=triage["retrieval_status"]["status"],
                      ai_status=triage["ai"].get("draft_status"))
    return jsonify({"ok": True, "report_id": rid, "triage": triage, **ids}), 201


@app.get("/api/reports")
def list_reports():
    """Report cards: each row enriched with latest priority, triage-run count
    and work-order summary so the UI can show everything without extra calls."""
    with connect() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM reports ORDER BY created_at DESC LIMIT 100").fetchall()]
        runs = [dict(x) for x in c.execute(
            "SELECT report_id, result FROM triage_runs ORDER BY created_at DESC").fetchall()]
        wos = [dict(x) for x in c.execute(
            "SELECT report_id, id, status, priority FROM work_orders").fetchall()]
    latest_priority: dict[str, str] = {}
    run_counts: dict[str, int] = {}
    for run in runs:
        rid = run["report_id"]
        run_counts[rid] = run_counts.get(rid, 0) + 1
        if rid not in latest_priority:
            try:
                res = json.loads(run["result"]) if isinstance(run["result"], str) else {}
            except Exception:
                res = {}
            latest_priority[rid] = (res.get("priority") or {}).get("level", "—")
    wo_by: dict[str, list] = {}
    for w in wos:
        wo_by.setdefault(w["report_id"], []).append(
            {"id": w["id"], "status": w["status"], "priority": w["priority"]})
    for r in rows:
        r["sensor_readings"] = from_json(r["sensor_readings"], {})
        r["confirmed_findings"] = from_json(r.get("confirmed_findings", "[]"), [])
        r["latest_priority"] = latest_priority.get(r["id"], "—")
        r["triage_runs"] = run_counts.get(r["id"], 0)
        r["work_orders"] = wo_by.get(r["id"], [])
    return jsonify({"ok": True, "reports": rows})


@app.get("/api/reports/<rid>")
def get_report(rid: str):
    with connect() as c:
        row = c.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
        if not row:
            return err("Report not found.", 404)
        report = dict(row)
        runs = [dict(r) for r in c.execute(
            "SELECT * FROM triage_runs WHERE report_id=? ORDER BY created_at DESC", (rid,)).fetchall()]
        wos = [dict(r) for r in c.execute(
            "SELECT * FROM work_orders WHERE report_id=? ORDER BY created_at DESC", (rid,)).fetchall()]
    report["sensor_readings"] = from_json(report["sensor_readings"], {})
    report["followup_answers"] = from_json(report.get("followup_answers", "[]"), [])
    report["confirmed_findings"] = from_json(report.get("confirmed_findings", "[]"), [])
    for w in wos:
        w["inspection_steps"] = from_json(w["inspection_steps"], [])
        w["citations"] = from_json(w["citations"], [])
        w["history"] = from_json(w["history"], [])
        w["sections"] = from_json(w.get("sections", ""), {})
        w["equipment_type"] = report.get("equipment_type")
        w["equipment_id"] = report.get("equipment_id")
    latest_triage = from_json(runs[0]["result"], {}) if runs else {}
    return jsonify({"ok": True, "report": report, "triage": latest_triage, "work_orders": wos,
                    "triage_runs": len(runs)})


@app.post("/api/reports/<rid>/followup")
def answer_followup(rid: str):
    payload = request.get_json(silent=True) or {}
    answers = payload.get("answers")
    if not isinstance(answers, list) or not answers:
        return err("Provide 'answers' as a non-empty list of {question, answer}.")
    with connect() as c:
        row = c.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
        if not row:
            return err("Report not found.", 404)
        report = dict(row)
        prev = from_json(report.get("followup_answers", "[]"), [])
        merged = prev + [{"question": str(a.get("question", ""))[:500],
                          "answer": str(a.get("answer", ""))[:2000]} for a in answers if isinstance(a, dict)]
        c.execute("UPDATE reports SET followup_answers=? WHERE id=?", (to_json(merged), rid))
        report["followup_answers"] = merged
        report["sensor_readings"] = from_json(report["sensor_readings"], {})
        # optional extra sensor values supplied alongside answers
        extra = payload.get("sensor_readings") or {}
        if isinstance(extra, dict):
            for k, v in extra.items():
                if k in SENSOR_SCHEMA and v not in (None, ""):
                    try:
                        report["sensor_readings"][k] = float(v)
                    except (TypeError, ValueError):
                        return err(f"Sensor '{k}' must be a number.")
            c.execute("UPDATE reports SET sensor_readings=? WHERE id=?",
                      (to_json(report["sensor_readings"]), rid))
    # re-run triage with answers folded into events (history preserved via new triage_run row)
    enriched = dict(report)
    qa_text = " ".join(f"Q:{a['question']} A:{a['answer']}" for a in merged)
    enriched["operating_events"] = f"{report.get('operating_events','')} Follow-up answers: {qa_text}"
    triage = do_triage(enriched, request_ai_key(payload))
    save_triage_and_workorder(rid, triage)
    return jsonify({"ok": True, "triage": triage})


@app.post("/api/reports/<rid>/confirm")
def confirm_findings(rid: str):
    """Only a human technician sets confirmed findings. AI can never write here."""
    payload = request.get_json(silent=True) or {}
    findings = payload.get("confirmed_findings")
    by = (payload.get("confirmed_by") or "technician").strip()[:80]
    if not isinstance(findings, list) or not findings:
        return err("Provide 'confirmed_findings' as a non-empty list of strings (your on-site verified facts).")
    with connect() as c:
        if not c.execute("SELECT id FROM reports WHERE id=?", (rid,)).fetchone():
            return err("Report not found.", 404)
        c.execute("UPDATE reports SET confirmed_findings=? WHERE id=?",
                  (to_json([str(f)[:1000] for f in findings]), rid))
    _applog.log_event("confirmed", f"{len(findings)} finding(s) by {by}", report_id=rid, by=by,
                      detail=f"{len(findings)} confirmed finding(s)")
    return jsonify({"ok": True, "message": f"Confirmed findings recorded by {by}. AI suggestions remain labelled unconfirmed.",
                    "confirmed_by": by, "confirmed_findings": findings})


# ---------- work orders (human-gated) ----------
def _get_wo(wid: str):
    with connect() as c:
        row = c.execute("SELECT w.*, r.equipment_type, r.equipment_id FROM work_orders w"
                        " LEFT JOIN reports r ON r.id=w.report_id WHERE w.id=?", (wid,)).fetchone()
        if not row:
            return None
        wo = dict(row)
    wo["inspection_steps"] = from_json(wo["inspection_steps"], [])
    wo["citations"] = from_json(wo["citations"], [])
    wo["history"] = from_json(wo["history"], [])
    wo["sections"] = from_json(wo.get("sections", ""), {})
    return wo


@app.get("/api/work-orders")
def list_wos():
    status = request.args.get("status", "")
    if status not in ("", "draft", "approved", "rejected", "superseded"):
        return err("Unknown status filter. Use draft, approved, rejected or superseded.")
    with connect() as c:
        if status:
            rows = c.execute("SELECT w.*, r.equipment_type, r.equipment_id FROM work_orders w"
                             " LEFT JOIN reports r ON r.id=w.report_id WHERE w.status=? ORDER BY w.updated_at DESC",
                             (status,)).fetchall()
        else:
            rows = c.execute("SELECT w.*, r.equipment_type, r.equipment_id FROM work_orders w"
                             " LEFT JOIN reports r ON r.id=w.report_id"
                             " ORDER BY w.updated_at DESC LIMIT 100").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["inspection_steps"] = from_json(d["inspection_steps"], [])
            d["citations"] = from_json(d["citations"], [])
            d["history"] = from_json(d["history"], [])
            d["sections"] = from_json(d.get("sections", ""), {})
            out.append(d)
    return jsonify({"ok": True, "work_orders": out})


@app.get("/api/work-orders/<wid>")
def get_wo(wid: str):
    wo = _get_wo(wid)
    if not wo:
        return err("Work order not found.", 404)
    return jsonify({"ok": True, "work_order": wo})


@app.patch("/api/work-orders/<wid>")
def edit_wo(wid: str):
    wo = _get_wo(wid)
    if not wo:
        return err("Work order not found.", 404)
    if wo["status"] != "draft":
        return err(f"Only draft work orders can be edited (current: {wo['status']}).", 409)
    payload = request.get_json(silent=True) or {}
    fields = {}
    for key in ("title", "priority", "description", "safety_notes", "decision_note"):
        if key in payload:
            if not isinstance(payload[key], str):
                return err(f"'{key}' must be text.")
            fields[key] = payload[key][:4000]
    if "inspection_steps" in payload:
        if not isinstance(payload["inspection_steps"], list):
            return err("'inspection_steps' must be a list of strings.")
        fields["inspection_steps"] = to_json([str(s)[:1000] for s in payload["inspection_steps"]][:30])
    if "priority" in fields and fields["priority"] not in PRIORITY_DEFINITIONS + ("P1 - Emergency", "P2 - Urgent", "P3 - Planned", "P4 - Routine"):
        # accept both short and long forms
        allowed = ["P1 - Emergency", "P2 - Urgent", "P3 - Planned", "P4 - Routine", "P1", "P2", "P3", "P4"]
        if fields["priority"] not in allowed:
            return err(f"priority must be one of {allowed}.")
    if not fields:
        return err("Nothing to update — send title/priority/description/inspection_steps/safety_notes.")
    by = str((payload.get("edited_by") or "technician"))[:80]
    hist = wo["history"] + [{"at": now(), "by": by, "action": f"edited draft fields: {', '.join(sorted(fields))}"}]
    # Display snapshots (sections) are cleared on edit so the UI falls back
    # to the freshly edited description text instead of stale rows.
    sets = ", ".join(f"{k}=?" for k in fields) + ", updated_at=?, history=?, sections=''"
    vals = [fields[k] for k in fields] + [now(), to_json(hist)]
    with connect() as c:
        c.execute(f"UPDATE work_orders SET {sets} WHERE id=?", (*vals, wid))
    _applog.log_event("wo_edited", f"draft {wid} edited by {by}", work_order_id=wid, by=by,
                      detail=f"fields: {', '.join(sorted(fields))}")
    return jsonify({"ok": True, "work_order": _get_wo(wid)})


@app.post("/api/work-orders/<wid>/approve")
def approve_wo(wid: str):
    wo = _get_wo(wid)
    if not wo:
        return err("Work order not found.", 404)
    if wo["status"] != "draft":
        return err(f"Only drafts can be approved (current: {wo['status']}).", 409)
    payload = request.get_json(silent=True) or {}
    by = str(payload.get("approved_by") or "").strip()
    if not by:
        return err("'approved_by' (technician name/id) is required — approval cannot be anonymous or automatic.")
    note = str(payload.get("decision_note", ""))[:2000]
    hist = wo["history"] + [{"at": now(), "by": by, "action": f"APPROVED by human technician. Note: {note or '—'}"}]
    with connect() as c:
        c.execute("UPDATE work_orders SET status='approved', decision_note=?, updated_at=?, history=? WHERE id=?",
                  (note, now(), to_json(hist), wid))
    _applog.log_event("wo_decision", f"{wid} APPROVED by {by}", work_order_id=wid,
                      decision="approved", by=by)
    return jsonify({"ok": True, "work_order": _get_wo(wid)})


@app.post("/api/work-orders/<wid>/reject")
def reject_wo(wid: str):
    wo = _get_wo(wid)
    if not wo:
        return err("Work order not found.", 404)
    if wo["status"] != "draft":
        return err(f"Only drafts can be rejected (current: {wo['status']}).", 409)
    payload = request.get_json(silent=True) or {}
    by = str(payload.get("rejected_by") or "").strip()
    if not by:
        return err("'rejected_by' (technician name/id) is required.")
    note = str(payload.get("decision_note", ""))[:2000]
    if not note:
        return err("'decision_note' (reason) is required to reject.")
    hist = wo["history"] + [{"at": now(), "by": by, "action": f"REJECTED by human technician. Reason: {note}"}]
    with connect() as c:
        c.execute("UPDATE work_orders SET status='rejected', decision_note=?, updated_at=?, history=? WHERE id=?",
                  (note, now(), to_json(hist), wid))
    _applog.log_event("wo_decision", f"{wid} REJECTED by {by}", work_order_id=wid,
                      decision="rejected", by=by)
    return jsonify({"ok": True, "work_order": _get_wo(wid)})


# ---------- structured logs ----------
@app.get("/api/logs")
def read_logs():
    try:
        limit = int(request.args.get("limit", "100"))
    except (TypeError, ValueError):
        limit = 100
    return jsonify({"ok": True, "events": _applog.read_recent(limit)})


@app.route("/api/ai/debug", methods=["GET", "POST"])
def ai_debug():
    """Safe diagnostics: WHAT the app would send (key fingerprint only).

    Shows provider, model, key source, key length and first/last few chars so
    you can compare against Postman WITHOUT ever exposing the full key.
    Nothing here is logged.
    """
    provider = _provider_name()
    key = request_ai_key(request.get_json(silent=True) if request.is_json else None) or ""
    return jsonify({"ok": True, "provider": provider,
                    "model": _openrouter.model_name() if provider == "openrouter" else _gemini.model_name(),
                    "fallbacks": _openrouter.fallback_models() if provider == "openrouter" else [],
                    "key_source": ("env" if key == (_openrouter.resolve_key(None)[0]
                                   if provider == "openrouter" else _gemini.resolve_key(None)[0])
                                   and key else ("request" if key else "none")),
                    "key_present": bool(key),
                    "key_length": len(key),
                    "key_head": key[:7] if key else "",
                    "key_tail": key[-4:] if key else ""})


# ---------- history per equipment ----------
@app.get("/api/history/<equipment_id>")
def history(equipment_id: str):
    with connect() as c:
        reps = [dict(r) for r in c.execute(
            "SELECT * FROM reports WHERE equipment_id=? ORDER BY created_at ASC", (equipment_id,)).fetchall()]
        wos = [dict(r) for r in c.execute(
            "SELECT * FROM work_orders WHERE report_id IN (SELECT id FROM reports WHERE equipment_id=?)"
            " ORDER BY created_at ASC", (equipment_id,)).fetchall()]
    for r in reps:
        r["sensor_readings"] = from_json(r["sensor_readings"], {})
        r["confirmed_findings"] = from_json(r.get("confirmed_findings", "[]"), [])
    for w in wos:
        w["inspection_steps"] = from_json(w["inspection_steps"], [])
        w["citations"] = from_json(w["citations"], [])
        w["history"] = from_json(w["history"], [])
        w["sections"] = from_json(w.get("sections", ""), {})
        w["equipment_type"] = next((r.get("equipment_type") for r in reps if r["id"] == w["report_id"]), None)
        w["equipment_id"] = equipment_id
    return jsonify({"ok": True, "equipment_id": equipment_id, "reports": reps, "work_orders": wos})


if __name__ == "__main__":
    cfg = server_config()
    app.run(host=cfg["host"], port=cfg["port"], debug=cfg["debug"])  # noqa: S201 - local dev default; deploy via gunicorn/Docker (see README)
