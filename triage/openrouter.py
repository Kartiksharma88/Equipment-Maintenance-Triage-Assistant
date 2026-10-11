"""OpenRouter integration for enhanced triage drafting.

Same contract as triage/gemini.py: local retrieval + deterministic rules always
run first and stay authoritative; the provider only enhances wording. Output is
validated by triage/reasoning.apply_ai_enhancement before it touches a result.

Key resolution (never stored, never logged — only key_source is recorded):
  per-request key (X-OpenRouter-Key / X-AI-Key header, or openrouter_api_key /
  ai_api_key body field) > OPENROUTER_API_KEY env > none.

Model: OPENROUTER_MODEL env (default a free-tier model — pick any `:free`
model from https://openrouter.ai/models if the default stops working).
Uses OpenRouter's OpenAI-compatible /chat/completions endpoint with JSON mode.
stdlib only (urllib), like the Gemini module.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from triage.gemini import build_prompt

MODEL_DEFAULT = "openai/gpt-4o-mini"
API_URL = "https://openrouter.ai/api/v1/chat/completions"
# Retry policy for rate limits / overloaded free pools (module-level for tests).
MAX_ATTEMPTS = 3
BACKOFF_S = (3, 8)
RETRYABLE = (429, 503, 529)
_sleep = time.sleep  # indirection so tests can stub waiting out


def fallback_models() -> list[str]:
    """Free fallback models OpenRouter tries if the primary is throttled.

    Override with OPENROUTER_FALLBACKS="model-a,model-b". The primary model
    is never duplicated in the list.
    """
    raw = os.environ.get("OPENROUTER_FALLBACKS", "google/gemma-4-26b-a4b-it:free")
    out = []
    for m in raw.split(","):
        m = m.strip()
        if m and m != model_name() and m not in out:
            out.append(m)
    return out[:3]


def _clean_http_error(exc: urllib.error.HTTPError) -> str:
    """Turn an HTTPError into a one-line human message (not raw JSON)."""
    detail = ""
    try:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
    except Exception:
        pass
    msg = detail or str(exc.reason)
    try:
        err = (json.loads(detail).get("error") or {})
        if isinstance(err, dict):
            msg = err.get("message") or msg
            meta = err.get("metadata") or {}
            # OpenRouter wraps the real upstream text in metadata.raw when the
            # top-level message is a generic "Provider returned error".
            if (not msg or msg.strip().lower() in ("provider returned error", "error")) \
                    and isinstance(meta, dict) and meta.get("raw"):
                msg = str(meta["raw"])[:400]
            remedy = (meta.get("remedy_hint") or "") if isinstance(meta, dict) else ""
            if remedy and remedy not in msg:
                msg += f" ({remedy[:200]})"
    except Exception:
        pass
    if exc.code == 404:
        msg += " — the configured model looks retired. Set OPENROUTER_MODEL to a current free id from https://openrouter.ai/models"
    elif exc.code == 429:
        msg += " — the free shared pool is busy. The app retries automatically; otherwise wait a minute or pick another :free model"
    elif exc.code in (400, 401, 402, 403):
        msg += " — is the key correct and does it have credit/free-model access?"
    return f"OpenRouter API failure (HTTP {exc.code}): {msg}"


def resolve_key(explicit: str | None = None) -> tuple[str | None, str]:
    """Return (key_or_None, source). Key value never logged."""
    if explicit and explicit.strip():
        return explicit.strip(), "request"
    if os.environ.get("OPENROUTER_API_KEY", "").strip():
        return os.environ["OPENROUTER_API_KEY"].strip(), "env"
    return None, "none"


def model_name() -> str:
    return os.environ.get("OPENROUTER_MODEL", MODEL_DEFAULT).strip() or MODEL_DEFAULT


def active() -> bool:
    return os.environ.get("AI_PROVIDER", "gemini").strip().lower() == "openrouter"


def openrouter_status(explicit: str | None = None) -> dict:
    key, source = resolve_key(explicit)
    if key:
        return {"mode": "ai-enhanced", "provider": "openrouter", "model": model_name(),
                "key_source": source,  # 'request' or 'env' — never the key itself
                "message": f"OpenRouter ({model_name()}) key found via {source}. "
                           "Local retrieval + deterministic rules still run first and stay authoritative."}
    return {"mode": "local", "provider": None, "model": model_name(), "key_source": "none",
            "message": "OpenRouter API key not configured on the server (OPENROUTER_API_KEY). "
                       "Running local retrieval + deterministic rules, which work fully offline."}


def _post_chat_once(prompt: str, api_key: str, timeout: int) -> str:
    payload: dict = {
        "model": model_name(),
        "messages": [
            {"role": "system",
             "content": "You are a careful industrial-maintenance assistant. Advisory only. Reply with raw JSON only."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 2048,
        "response_format": {"type": "json_object"},
    }
    fallbacks = fallback_models()
    if fallbacks:
        # OpenRouter tries `model` first, then each entry of `models` in order
        # when the primary is rate-limited/overloaded — one request, no extra code.
        payload["models"] = fallbacks
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL, data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}",
                 "HTTP-Referer": "http://127.0.0.1:5000",
                 "X-Title": "Equipment Maintenance Triage Assistant"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        if isinstance(exc, urllib.error.HTTPError):
            raise
        raise RuntimeError(f"OpenRouter API network failure: {exc.reason}. Check connectivity.") from exc
    except TimeoutError as exc:
        raise RuntimeError("OpenRouter API timed out (free models can be slow — retry). Local draft used instead.") from exc


def _retry_delay(exc: urllib.error.HTTPError, attempt: int) -> float:
    """Honor Retry-After when present (capped); otherwise exponential backoff."""
    try:
        after = float((exc.headers or {}).get("Retry-After", ""))
        return max(1.0, min(after, 20.0))
    except (TypeError, ValueError):
        pass
    return float(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])


def _post_chat(prompt: str, api_key: str, timeout: int) -> str:
    """POST with retries for rate-limited/overloaded free pools. Raises the
    last HTTPError (cleaned by the caller) if all attempts are exhausted."""
    last: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return _post_chat_once(prompt, api_key, timeout)
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in RETRYABLE or attempt == MAX_ATTEMPTS - 1:
                raise
            _sleep(_retry_delay(exc, attempt))
        except RuntimeError:
            raise
    raise last  # pragma: no cover - loop always returns or raises first


def _extract_text(raw: str) -> tuple[str, str | None]:
    """Return (model-output-text, actual-model-id-or-None)."""
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OpenRouter returned a non-JSON envelope: {exc}") from exc
    if isinstance(doc, dict) and doc.get("error"):
        err = doc["error"]
        msg = err.get("message", err) if isinstance(err, dict) else err
        raise RuntimeError(f"OpenRouter error: {msg}")
    try:
        text = doc["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"OpenRouter returned an unexpected shape: {exc}") from exc
    text = (text or "").strip()
    if not text:
        raise RuntimeError("OpenRouter returned an empty response. Local draft used instead.")
    used = doc.get("model") if isinstance(doc, dict) else None
    return text, (used if isinstance(used, str) else None)


def enhance_triage(report: dict, retrieved: list[dict], checks: list[dict],
                   data_quality: dict, api_key: str | None, timeout: int = 30) -> dict:
    """Call OpenRouter and validate its JSON. Returns {status, data?, message}.

    status 'ok'          -> data holds the validated enhancement dict.
    status 'unavailable' -> no key; caller keeps the local draft.
    status 'failure'     -> key/call/parse problem; message explains it for the UI.
    """
    key, _source = resolve_key(api_key)
    if not key:
        return {"status": "unavailable",
                "message": "OpenRouter API key not configured on the server (OPENROUTER_API_KEY). "
                           "Local rules-based draft used instead — set the key server-side to enable enhanced drafting."}
    try:
        raw = _post_chat(build_prompt(report, retrieved, checks, data_quality), key, timeout)
        text, used_model = _extract_text(raw)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"OpenRouter returned malformed JSON: {exc}. Local draft used instead.") from exc
        if not isinstance(data, dict):
            raise RuntimeError("OpenRouter returned an unexpected shape (not an object). Local draft used instead.")
        who = used_model or model_name()
        return {"status": "ok", "data": data, "used_model": used_model,
                "message": f"Enhanced via OpenRouter ({who}). Validated and merged with local rules."}
    except RuntimeError as exc:
        return {"status": "failure", "message": str(exc)}
    except urllib.error.HTTPError as exc:  # retries exhausted
        return {"status": "failure", "message": f"{_clean_http_error(exc)} Local draft used instead."}
    except Exception as exc:  # never let AI break triage
        return {"status": "failure", "message": f"OpenRouter enhancement failed unexpectedly: {exc}. Local draft used instead."}


def ping(api_key: str | None, timeout: int = 20) -> dict:
    """Minimal validation call. Returns {ok, message}."""
    key, _source = resolve_key(api_key)
    if not key:
        return {"ok": False, "message": "No API key provided."}
    body = json.dumps({
        "model": model_name(),
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "temperature": 0,
        "max_tokens": 16,
    }).encode("utf-8")
    req = urllib.request.Request(
        API_URL, data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}",
                 "HTTP-Referer": "http://127.0.0.1:5000",
                 "X-Title": "Equipment Maintenance Triage Assistant"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            doc = json.loads(resp.read().decode("utf-8", errors="replace"))
        if isinstance(doc, dict) and doc.get("error"):
            err = doc["error"]
            msg = err.get("message", err) if isinstance(err, dict) else err
            return {"ok": False, "message": f"OpenRouter rejected the request: {msg}"}
        if (doc.get("choices") or []):
            return {"ok": True, "message": f"OpenRouter key works (model {model_name()}). Enhanced drafting is available."}
        return {"ok": False, "message": "OpenRouter returned an unexpected response."}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "message": f"OpenRouter test failed: {_clean_http_error(exc)}"}
    except Exception as exc:
        return {"ok": False, "message": f"OpenRouter test failed: {exc}"}
