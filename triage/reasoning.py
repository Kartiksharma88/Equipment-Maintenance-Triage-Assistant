"""Triage reasoning: observations vs possible causes vs confirmed findings.

SAFETY: this module only *suggests*. It never controls equipment and never
approves work. Work orders are always created as DRAFT for human review.
Possible causes are explicitly labelled unconfirmed with citations.

Pipeline (always in this order):
  1. Local retrieval (manual sections) + deterministic threshold checks.
  2. Local cause patterns / follow-ups / inspection steps / priority.
  3. OPTIONAL Gemini enhancement — validated, merged, and clearly badged.
     Deterministic rules stay authoritative: the final priority is the MORE
     urgent of (rules priority, AI suggestion), so AI can escalate but never
     de-escalate below a fired rule. AI failures are surfaced, never hidden.
"""
from __future__ import annotations

import time as _time

from triage import applog as _applog
from triage import gemini as _gemini
from triage import openrouter as _openrouter
from triage.llm import llm_status, provider_name
from triage.llm import llm_status
from triage.retrieval import retrieve
from triage.rules import data_quality, threshold_checks

CAUSE_PATTERNS = [
    (("vibrat", "shak", "rattl", "rumble", "grind", "imbalance", "misalign"),
     "Bearing wear / misalignment / imbalance (UNCONFIRMED)",
     "Vibration signature matches rotating-element distress. Confirm by tri-axial readings and bolt-torque check — do not assume.",
     0.75),
    (("hot", "overheat", "temperatur", "smoke", "smell", "burn"),
     "Lubrication failure / cooling blockage causing overheating (UNCONFIRMED)",
     "Heat with rotating equipment often traces to oil starvation or blocked cooling. Confirm oil level/colour + IR survey before concluding.",
     0.7),
    (("leak", "drip", "seep", "seal", "puddle"),
     "Mechanical seal / gasket leak (UNCONFIRMED)",
     "Visible fluid needs fluid-ID (oil vs water vs condensate) plus drip-rate count to confirm source.",
     0.7),
    (("pressure", "no flow", "no water", "cavitat", "priming", "weak"),
     "Suction blockage / air-lock / worn wear-rings (UNCONFIRMED)",
     "Pressure/flow loss has several mimics (valve position, strainer, seal). Confirm with dual-gauge reading.",
     0.65),
    (("noise", "squeal", "screech", "loud", "knock", "belt", "slip"),
     "Belt / idler distress or looseness (UNCONFIRMED)",
     "Tone matters more than loudness. Confirm by tension-sag measurement and hand-spin of idlers under lockout.",
     0.6),
    (("trip", "overload", "breaker", "current", "shutdown", "no start", "crank"),
     "Electrical overload / protection trip (UNCONFIRMED)",
     "Trips protect downstream faults (jam, short, AVR). Confirm relay setting vs nameplate and per-phase current before reset.",
     0.65),
    (("filter", "frost", "no cool", "warm", "airflow", "refrigerant"),
     "Filtration / refrigerant / capacitor fault (UNCONFIRMED)",
     "Weak cooling mimics filter, charge, capacitor and thermostat faults. Confirm stepwise, cheapest checks first.",
     0.6),
]

PRIORITY_ORDER = ["P1 - Emergency", "P2 - Urgent", "P3 - Planned", "P4 - Routine"]


def _clip(text: str, n: int) -> str:
    """Shorten to ~n chars without cutting mid-word (readable, no dangling fragments)."""
    t = (text or "").strip()
    if len(t) <= n:
        return t
    cut = t[:n].rsplit(" ", 1)
    return (cut[0] if cut and cut[0] else t[:n]) + "…"


def _match_causes(text: str) -> list[dict]:
    t = (text or "").lower()
    out = []
    for keywords, title, note, conf in CAUSE_PATTERNS:
        if any(k in t for k in keywords):
            out.append({"title": title, "note": note, "confidence": conf, "matched": [k for k in keywords if k in t]})
    if not out:
        out.append({"title": "Undifferentiated fault — further inspection required (UNCONFIRMED)",
                    "note": "Symptoms do not map to a known pattern. Follow the inspection steps and answer the follow-up questions.",
                    "confidence": 0.35, "matched": []})
    return out[:4]


def _priority(checks: list[dict], issue: str, events: str) -> tuple[str, str]:
    crit = [c for c in checks if c["severity"] == "critical"]
    warns = [c for c in checks if c["severity"] == "warn"]
    danger = any(w in f"{issue} {events}".lower() for w in ("smoke", "fire", "spark", "explosion", "injury", "trapped", "burst"))
    if danger or len(crit) >= 2:
        return "P1 - Emergency", "Danger keywords present or 2+ critical thresholds breached. Stop equipment, isolate energy, escalate immediately."
    if len(crit) == 1 or len(warns) >= 2:
        return "P2 - Urgent", "One critical breach or multiple warnings. Inspect within 24 h; do not run unattended."
    if len(warns) == 1:
        return "P3 - Planned", "Single warning threshold breached or degraded performance. Schedule within 7 days."
    return "P4 - Routine", "No threshold breach. Monitor trend and include in next preventive window."


def _followups(report: dict, dq: dict, retrieved: list[dict]) -> list[str]:
    q: list[str] = []
    for m in dq["missing"][:4]:
        q.append(f"Can you provide the '{m}' reading (with units)? It is missing and needed to rule out related faults.")
    for c in dq["conflicts"][:3]:
        q.append(f"Conflict check: {c['message']} Can you confirm sensor location, units, and when the reading was taken?")
    for inv in dq["invalid"][:2]:
        q.append(f"Data check: {inv['message']} Please re-enter a valid value.")
    text = f"{report.get('issue_description','')} {report.get('operating_events','')}".lower()
    if "vibrat" in text or "shak" in text:
        q.append("Is the vibration constant or only under load / at certain speeds? Any recent overhaul or foundation work?")
    if any(w in text for w in ("hot", "overheat", "smoke")):
        q.append("When did the heat start, and is cooling airflow / oil sight-glass visibly abnormal? Any recent oil change?")
    if "leak" in text or "drip" in text:
        q.append("What fluid is leaking (colour/smell), where exactly, and roughly how many drops per minute?")
    if "pressure" in text or "flow" in text:
        q.append("What do the suction AND discharge gauges read together, and are any valves recently moved?")
    if "noise" in text or "squeal" in text:
        q.append("Describe the noise tone (squeal/grind/knock), when it occurs, and whether it changes with speed?")
    if retrieved and len(q) < 6:
        q.append(f"Manual {retrieved[0]['id']} suggests '{retrieved[0].get('inspection_ref','')}': can you perform that check and report the result?")
    seen, dedup = set(), []
    for item in q:
        if item not in seen:
            seen.add(item); dedup.append(item)
    return dedup[:6] or ["No further questions — proceed with the inspection steps below and record confirmed findings."]


# ---------- Gemini merge (validated) ----------

def _force_unconfirmed(title: str) -> str:
    t = (title or "Possible fault").strip()[:160] or "Possible fault"
    if "unconfirm" not in t.lower():
        t += " (UNCONFIRMED)"
    return t


def _clamp_conf(v) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.5


def _count_answers(report: dict) -> int:
    try:
        import json as _j
        ans = report.get("followup_answers")
        if isinstance(ans, str):
            ans = _j.loads(ans)
        return len(ans) if isinstance(ans, list) else 0
    except Exception:
        return 0


def _split_events(events: str) -> tuple[str, bool]:
    """Separate the original events from appended follow-up Q&A blobs."""
    marker = "Follow-up answers:"
    if marker in (events or ""):
        return events.split(marker, 1)[0].strip(), True
    return (events or "").strip(), False
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.5


def apply_ai_enhancement(result: dict, ai_data: dict, retrieved: list[dict],
                         checks: list[dict], source: str = "gemini") -> dict:
    """Merge validated Gemini output into a local triage result. Pure function (testable, no I/O).

    Guarantees enforced here:
      * every AI cause title carries (UNCONFIRMED); confidence clamped 0..1;
      * AI citations restricted to retrieved KB ids / fired RULE ids / EVENT;
      * final priority = MORE urgent of (rules priority, AI suggestion);
      * work-order draft stays status='draft'; AI text only appends a note.
    """
    if not isinstance(ai_data, dict):
        return result
    valid_ids = {r["id"] for r in retrieved}
    valid_rules = {c["rule_id"] for c in checks}

    # --- possible causes: AI suggestions appended after local ones, clearly sourced ---
    ai_causes = ai_data.get("possible_causes") or []
    if isinstance(ai_causes, list):
        for c in ai_causes[:4]:
            if not isinstance(c, dict):
                continue
            raw_cites = c.get("citations") or c.get("citation") or []
            if isinstance(raw_cites, str):
                raw_cites = [raw_cites]
            cites = [x for x in raw_cites if x in valid_ids or x in valid_rules]
            if not cites:
                cites = [r["id"] for r in retrieved[:1]] or ["EVENT"]
                cite_note = " (AI suggestion — verify against cited manual on site)"
            else:
                cite_note = ""
            result["possible_causes"].append({
                "title": _force_unconfirmed(str(c.get("title", ""))),
                "note": (str(c.get("note", "")).strip()[:600] + cite_note) or "AI-suggested hypothesis — confirm on site before acting.",
                "confidence": _clamp_conf(c.get("confidence", 0.5)),
                "citations": cites,
                "supporting_evidence": [f"AI-enhanced suggestion; evidence: {', '.join(cites)}"],
                "status": "possible (unconfirmed)",
                "source": source,
            })
        result["possible_causes"] = result["possible_causes"][:6]

    # --- follow-up questions: AI extras appended, deduped ---
    ai_qs = ai_data.get("followup_questions") or []
    if isinstance(ai_qs, list):
        seen = {q.lower() for q in result["followup_questions"]}
        for q in ai_qs[:5]:
            if isinstance(q, str) and q.strip() and q.strip().lower() not in seen:
                result["followup_questions"].append(q.strip()[:400])
                seen.add(q.strip().lower())
        result["followup_questions"] = result["followup_questions"][:8]

    # --- inspection steps: AI steps appended with enforced citations ---
    ai_steps = ai_data.get("inspection_steps") or []
    if isinstance(ai_steps, list):
        for s in ai_steps[:6]:
            if isinstance(s, str):
                s = {"step": s, "citation": "EVENT"}
            if not isinstance(s, dict) or not str(s.get("step", "")).strip():
                continue
            cite = str(s.get("citation", "")).strip()
            if cite not in valid_ids and cite not in valid_rules and cite != "EVENT":
                cite = "EVENT"
                step = _clip(str(s["step"]), 220) + " (AI-suggested — verify against manual before acting.)"
            else:
                step = _clip(str(s["step"]), 220)
            result["inspection_steps"].append({"step": step, "detail": "",
                                               "citation": cite, "source": source})
        result["inspection_steps"] = result["inspection_steps"][:12]

    # --- priority: AI may escalate, never de-escalate below fired rules ---
    sug = ai_data.get("priority_suggestion") or {}
    if isinstance(sug, dict) and sug.get("level") in PRIORITY_ORDER:
        rules_level = result["priority"]["level"]
        ai_level = sug["level"]
        if PRIORITY_ORDER.index(ai_level) < PRIORITY_ORDER.index(rules_level):
            result["priority"] = {
                "level": ai_level,
                "rationale": f"Escalated by Gemini review: {str(sug.get('rationale',''))[:300]} (Rules floor was {rules_level}.)",
                "rules_floor": rules_level,
                "ai_suggestion": ai_level,
            }
        else:
            result["priority"]["ai_suggestion"] = ai_level
            if checks:  # rules fired: record that the floor held
                result["priority"]["rationale"] += f" Gemini concurred ({ai_level}); deterministic rules floor applied."

    # --- work-order note: appended, draft enforced ---
    note = ai_data.get("work_order_note")
    if isinstance(note, str) and note.strip():
        result["work_order_draft"]["description"] += f"\nAI note (unconfirmed): {note.strip()[:800]}"
        result["work_order_draft"].setdefault("sections", {})["ai_note"] = note.strip()[:800]
        result["work_order_draft"]["citations"] = sorted(set(result["work_order_draft"]["citations"]))
    result["work_order_draft"]["status"] = "draft"
    return result


def run_triage(report: dict, kb_sections: list[dict], gemini_api_key: str | None = None,
               ai_provider: str | None = None, ai_key: str | None = None) -> dict:
    _t0 = _time.perf_counter()
    provider = provider_name(ai_provider)
    # gemini_api_key kept as a backwards-compatible alias for "explicit key".
    explicit_key = ai_key if (ai_key or "").strip() else gemini_api_key
    equipment_type = report.get("equipment_type", "")
    issue = report.get("issue_description", "")
    events = report.get("operating_events", "")
    sensor = report.get("sensor_readings") or {}
    query = f"{equipment_type} {issue} {events} {' '.join(f'{k} {v}' for k, v in sensor.items() if v not in (None, ''))}"

    retrieval = retrieve(query, kb_sections, equipment_type=equipment_type, top_k=4)
    retrieved = retrieval["results"] if retrieval["status"] == "ok" else []

    checks = threshold_checks(sensor, equipment_type)
    dq = data_quality(sensor, issue, events)

    causes_raw = _match_causes(f"{issue} {events}")
    possible_causes = []
    for c in causes_raw:
        cites = [r["id"] for r in retrieved[:2]] or (["(no manual section retrieved — cause is text-pattern only)"])
        ev = []
        for ch in checks[:3]:
            ev.append(f"[{ch['rule_id']}: {ch['sensor']}={ch['value']}]")
        possible_causes.append({
            "title": c["title"],  # always carries (UNCONFIRMED)
            "note": c["note"],
            "confidence": c["confidence"],
            "citations": cites,
            "supporting_evidence": ev or ["No threshold breach — based on symptom keywords only."],
            "status": "possible (unconfirmed)",
            "source": "local",
        })

    inspection_steps = []
    for r in retrieved:
        # Short action title + separate detail: the citation chip is rendered
        # separately by the UI, so the KB id is NOT repeated inside the text.
        inspection_steps.append({
            "step": (r.get("inspection_ref") or r["title"]).strip()[:220],
            "detail": _clip(r["content"], 200),
            "citation": r["id"],
        })
    for ch in checks:
        if ch["severity"] == "critical":
            inspection_steps.insert(0, {"step": f"SAFETY FIRST — {ch['message']}",
                                        "detail": "Isolate energy / lockout before touching.",
                                        "citation": ch["rule_id"]})
    if not inspection_steps:
        inspection_steps.append({"step": "Record baseline readings (temperature, vibration, pressure, oil)",
                                 "detail": "Re-run triage once values are available.",
                                 "citation": "general"})

    priority, rationale = _priority(checks, issue, events)
    questions = _followups(report, dq, retrieved)

    observations = [
        f"Report: {equipment_type} {report.get('equipment_id','')} — {issue[:200]}",
        f"Operating events noted: {(events or 'none provided')[:200]}",
        f"Deterministic rules fired: {len(checks)} ({sum(1 for c in checks if c['severity']=='critical')} critical).",
        f"Sensor gaps: {len(dq['missing'])} missing, {len(dq['invalid'])} invalid, {len(dq['conflicts'])} conflict(s).",
    ]

    title_slug = (issue or "general inspection")[:60]
    ev_main, has_qa = _split_events(events)
    n_ans = _count_answers(report)
    ev_short = _clip(ev_main, 300) or "—"
    if has_qa or n_ans:
        ev_short += (f" (+{n_ans} answered follow-up(s) — see report history)"
                     if n_ans else " (+follow-up answers — see report history)")
    work_order_draft = {
        "title": f"Inspect {equipment_type} {report.get('equipment_id','')} — {title_slug}",
        "priority": priority,
        "description": f"OBSERVED: {issue}\nEVENTS: {events}\nPOSSIBLE (unconfirmed) causes: "
                       + "; ".join(c["title"] for c in possible_causes)
                       + "\nConfirm on site before repairing.",
        "sections": {
            "observed": _clip(issue, 400),
            "events": ev_short,
            "possible_causes": [c["title"] for c in possible_causes],
            "ai_note": "",
        },
        "inspection_steps": [s["step"] for s in inspection_steps[:8]],
        "safety_notes": "Lockout/tagout, stored-energy and burn-hazard precautions per cited manual sections. No remote operation.",
        "citations": sorted({c for c in ([r["id"] for r in retrieved] + [ch["rule_id"] for ch in checks])}),
        "status": "draft",  # HARD RULE: never anything else from AI
    }

    result = {
        "observations": observations,
        "threshold_checks": checks,
        "data_quality": dq,
        "retrieved_sections": retrieved,
        "retrieval_status": {"status": retrieval["status"], "message": retrieval["message"]},
        "possible_causes": possible_causes,   # never confirmed
        "confirmed_findings": [],             # only a human may fill this
        "inspection_steps": inspection_steps,
        "followup_questions": questions,
        "priority": {"level": priority, "rationale": rationale},
        "work_order_draft": work_order_draft,
        "ai": llm_status(explicit_key, provider),
    }

    # --- optional provider enhancement (validated merge; failures surfaced) ---
    if provider == "openrouter":
        enhancement = _openrouter.enhance_triage(report, retrieved, checks, dq, explicit_key)
    else:
        enhancement = _gemini.enhance_triage(report, retrieved, checks, dq, explicit_key)
    result["ai"].update({"draft_status": enhancement["status"], "draft_message": enhancement["message"]})
    if enhancement["status"] == "ok":
        result = apply_ai_enhancement(result, enhancement["data"], retrieved, checks,
                                       source=provider)
        if enhancement.get("used_model"):
            result["ai"]["model"] = enhancement["used_model"]
        # keep WO inspection steps in sync with merged list
        result["work_order_draft"]["inspection_steps"] = [s["step"] for s in result["inspection_steps"][:8]]
        result["work_order_draft"]["priority"] = result["priority"]["level"]
        result["work_order_draft"]["status"] = "draft"
    # Structured AI-workflow log (key_source only — the key itself is never logged).
    _applog.log_event(
        "triage_run",
        f"triage for {equipment_type} {report.get('equipment_id', '')}: "
        f"{len(checks)} rules, {retrieval['status']} retrieval, ai={enhancement['status']}",
        report_id=report.get("id"), equipment_id=report.get("equipment_id"),
        retrieval_status=retrieval["status"], sections=len(retrieved),
        rules_fired=[c["rule_id"] for c in checks], priority=result["priority"]["level"],
        mode=result["ai"].get("mode"), provider=result["ai"].get("provider"),
        model=result["ai"].get("model"), key_source=result["ai"].get("key_source"),
        ai_status=enhancement["status"],
        duration_ms=int((_time.perf_counter() - _t0) * 1000))
    return result
