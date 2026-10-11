"""Gemini (Google AI) integration for enhanced triage drafting.

Design rules:
  * Local retrieval + deterministic threshold checks ALWAYS run first and stay
    authoritative (priority floor, citations, safety notes).
  * Gemini only *enhances* wording: sharper possible causes, extra follow-up
    questions, clearer inspection steps. Its output is validated and merged.
  * The API key is NEVER stored, logged, or returned. Resolution order:
    per-request key (header/body) > GEMINI_API_KEY env > GOOGLE_API_KEY env.
  * Every failure (missing key, HTTP error, bad JSON, timeout) is returned as
    an explicit {status, message} dict — never raised silently, never hidden.
  * stdlib only (urllib) so there are no new dependencies to break installs.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

MODEL_DEFAULT = "gemini-2.0-flash"
API_BASE = "https://generativelanguage.googleapis.com/v1beta"


def resolve_key(explicit: str | None = None) -> tuple[str | None, str]:
    """Return (key_or_None, source) where source is request|env|none. Key value never logged."""
    if explicit and explicit.strip():
        return explicit.strip(), "request"
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if os.environ.get(var, "").strip():
            return os.environ[var].strip(), "env"
    return None, "none"


def model_name() -> str:
    return os.environ.get("GEMINI_MODEL", MODEL_DEFAULT).strip() or MODEL_DEFAULT


def gemini_status(explicit: str | None = None) -> dict:
    key, source = resolve_key(explicit)
    if key:
        return {"mode": "ai-enhanced", "provider": "gemini", "model": model_name(),
                "key_source": source,  # 'request' or 'env' — never the key itself
                "message": f"Gemini ({model_name()}) key found via {source}. "
                           "Local retrieval + deterministic rules still run first and stay authoritative."}
    return {"mode": "local", "provider": None, "model": model_name(), "key_source": "none",
            "message": "Gemini API key not configured on the server (GEMINI_API_KEY). "
                       "Running local retrieval + deterministic rules, which work fully offline."}


def build_prompt(report: dict, retrieved: list[dict], checks: list[dict], data_quality: dict) -> str:
    sec_text = "\n".join(
        f"- [{s['id']}] ({s['equipment_type']} {s.get('section','')}) {s['title']}: {s['content']}"
        for s in retrieved) or "(no manual sections retrieved)"
    checks_text = "\n".join(
        f"- [{c['rule_id']}/{c['severity']}] {c['sensor']}={c['value']}: {c['message']}" for c in checks
    ) or "(no thresholds breached)"
    dq = data_quality or {}
    dq_text = (f"missing sensors: {', '.join(dq.get('missing', [])) or 'none'}; "
               f"invalid: {len(dq.get('invalid', []))}; "
               f"conflicts: {'; '.join(c.get('message','') for c in dq.get('conflicts', [])) or 'none'}")
    sensors = report.get("sensor_readings") or {}
    sensor_text = ", ".join(f"{k}={v}" for k, v in sensors.items()) or "(none provided)"
    return f"""You assist equipment-maintenance triage. You are ADVISORY ONLY: you never control equipment, never approve work, never state a cause as confirmed.

EQUIPMENT: {report.get('equipment_type')} {report.get('equipment_id')}
ISSUE: {report.get('issue_description')}
RECENT OPERATING EVENTS: {report.get('operating_events') or '(none provided)'}
SENSOR READINGS: {sensor_text}

RETRIEVED MANUAL SECTIONS (cite these IDs when relevant):
{sec_text}

DETERMINISTIC THRESHOLD CHECKS (authoritative, already computed):
{checks_text}

DATA QUALITY: {dq_text}

Respond with EXACTLY this JSON shape (no markdown, no extra keys):
{{
  "possible_causes": [{{"title": "short cause name", "note": "why it fits + how to confirm on site", "confidence": 0.0-1.0}}],
  "followup_questions": ["targeted question 1", "..."],
  "inspection_steps": [{{"step": "concrete check with safety precaution", "citation": "KB-XXX section id from above, or RULE-XXX, or EVENT"}}],
  "priority_suggestion": {{"level": "one of: P1 - Emergency, P2 - Urgent, P3 - Planned, P4 - Routine", "rationale": "one sentence"}},
  "work_order_note": "one-paragraph technician-facing summary, unconfirmed language only"
}}
Rules: max 4 possible causes, max 5 follow-up questions, max 6 inspection steps. Every cause title must read as a hypothesis, never a diagnosis. Every inspection step needs a citation from the lists above. Mention lockout/tagout or PPE where hands-on work is implied."""


def _post_generate(prompt: str, api_key: str, timeout: int) -> str:
    url = f"{API_BASE}/models/{model_name()}:generateContent?key={api_key}"
    body = json.dumps({
        "system_instruction": {"parts": [{"text": "You are a careful industrial-maintenance assistant. Advisory only. Reply with raw JSON only."}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2,
                             "maxOutputTokens": 2048},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"Gemini API failure (HTTP {exc.code}): {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Gemini API network failure: {exc.reason}. Check connectivity.") from exc
    except TimeoutError as exc:
        raise RuntimeError("Gemini API timed out. Local draft used instead.") from exc


def _extract_text(raw: str) -> str:
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Gemini returned a non-JSON envelope: {exc}") from exc
    cands = doc.get("candidates") or []
    if not cands:
        # surface provider-level errors (e.g. bad key) which come as {"error": {...}}
        err = doc.get("error", {})
        raise RuntimeError(f"Gemini error: {err.get('message', 'no candidates returned')}")
    parts = (((cands[0].get("content") or {}).get("parts")) or [])
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
    if not text:
        raise RuntimeError("Gemini returned an empty response. Local draft used instead.")
    return text


def enhance_triage(report: dict, retrieved: list[dict], checks: list[dict],
                   data_quality: dict, api_key: str | None, timeout: int = 30) -> dict:
    """Call Gemini and validate its JSON. Returns {status, data?, message}.

    status 'ok'      -> data holds the validated enhancement dict.
    status 'unavailable' -> no key; caller keeps the local draft.
    status 'failure' -> key/call/parse problem; message explains it for the UI.
    """
    key, _source = resolve_key(api_key)
    if not key:
        return {"status": "unavailable",
                "message": "Gemini API key not configured on the server (GEMINI_API_KEY). "
                           "Local rules-based draft used instead — set the key server-side to enable enhanced drafting."}
    try:
        raw = _post_generate(build_prompt(report, retrieved, checks, data_quality), key, timeout)
        text = _extract_text(raw)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Gemini returned malformed JSON: {exc}. Local draft used instead.") from exc
        if not isinstance(data, dict):
            raise RuntimeError("Gemini returned an unexpected shape (not an object). Local draft used instead.")
        return {"status": "ok", "data": data, "message": f"Enhanced by Gemini ({model_name()}). Validated and merged with local rules."}
    except RuntimeError as exc:
        return {"status": "failure", "message": str(exc)}
    except Exception as exc:  # never let AI break triage
        return {"status": "failure", "message": f"Gemini enhancement failed unexpectedly: {exc}. Local draft used instead."}


def ping(api_key: str | None, timeout: int = 20) -> dict:
    """Minimal validation call for the 'Test key' button. Returns {ok, message}."""
    key, _source = resolve_key(api_key)
    if not key:
        return {"ok": False, "message": "No API key provided."}
    url = f"{API_BASE}/models/{model_name()}:generateContent?key={key}"
    body = json.dumps({
        "contents": [{"parts": [{"text": "Reply with exactly: OK"}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 16},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            doc = json.loads(resp.read().decode("utf-8", errors="replace"))
        cands = doc.get("candidates") or []
        if cands:
            return {"ok": True, "message": f"Gemini key works (model {model_name()}). Enhanced drafting is available."}
        err = doc.get("error", {})
        return {"ok": False, "message": f"Gemini rejected the request: {err.get('message', 'unknown error')}"}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        hint = " — is the key correct and is the Generative AI API enabled?" if exc.code in (400, 401, 403) else ""
        return {"ok": False, "message": f"Gemini test failed (HTTP {exc.code}): {detail or exc.reason}{hint}"}
    except Exception as exc:
        return {"ok": False, "message": f"Gemini test failed: {exc}"}
