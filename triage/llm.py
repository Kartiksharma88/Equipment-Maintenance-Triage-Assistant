"""LLM dispatcher (backwards-compatible shim).

Active provider is chosen by AI_PROVIDER env: 'gemini' (default) or 'openrouter'.
Local rules-based drafting is always available and stays authoritative.
Kept import-compatible with the original module (llm_status / try_llm_draft).
"""
from __future__ import annotations

import os

from triage import gemini as _g
from triage import openrouter as _o


def provider_name(explicit: str | None = None) -> str:
    if explicit and explicit.strip().lower() in ("gemini", "openrouter"):
        return explicit.strip().lower()
    env = os.environ.get("AI_PROVIDER", "").strip().lower()
    if env in ("gemini", "openrouter"):
        return env
    # Auto-detect: if only an OpenRouter key is configured, use it rather than
    # reporting "Gemini key missing" (the exact confusion users hit).
    if os.environ.get("OPENROUTER_API_KEY", "").strip() \
            and not os.environ.get("GEMINI_API_KEY", "").strip():
        return "openrouter"
    return "gemini"


def llm_status(explicit_key: str | None = None, provider: str | None = None) -> dict:
    if provider_name(provider) == "openrouter":
        return _o.openrouter_status(explicit_key)
    return _g.gemini_status(explicit_key)


def try_llm_draft(prompt: str, api_key: str | None = None) -> dict:
    if provider_name() == "openrouter":
        if _o.resolve_key(api_key)[0] is None:
            return {"status": "unavailable",
                    "message": "AI text generation unavailable (no OpenRouter API key). Local rules-based draft used instead."}
    elif _g.resolve_key(api_key)[0] is None:
        return {"status": "unavailable",
                "message": "AI text generation unavailable (no Gemini API key). Local rules-based draft used instead."}
    return {"status": "failure",
            "message": "Use the provider's enhance_triage(report, retrieved, checks, data_quality, api_key) "
                       "for structured enhancement — plain-text drafts are disabled to keep citations enforced."}
