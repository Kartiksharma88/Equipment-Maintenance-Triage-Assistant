"""Tests for the OpenRouter provider: resolution, validation, dispatch (no network)."""
import json

import triage.openrouter as O
from triage.kb_seed import SECTIONS
from triage.llm import provider_name


def _canned_ok(*args, **kwargs):
    inner = {"possible_causes": [{"title": "Seal wear", "note": "Check drip rate.",
                                  "confidence": 0.8}],
             "followup_questions": ["What colour is the fluid?"],
             "inspection_steps": [{"step": "Count seal drips", "citation": "KB-PUMP-04"}],
             "priority_suggestion": {"level": "P2 - Urgent", "rationale": "active leak"},
             "work_order_note": "OR summary."}
    return json.dumps({"choices": [{"message": {"content": json.dumps(inner)}}]})


def test_no_key_means_unavailable():
    out = O.enhance_triage({}, [], [], {}, api_key=None)
    assert out["status"] == "unavailable" and "OPENROUTER_API_KEY" in out["message"]


def test_key_resolution_order(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    assert O.resolve_key("explicit-key") == ("explicit-key", "request")
    assert O.resolve_key(None) == ("env-key", "env")
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert O.resolve_key(None) == (None, "none")


def test_provider_switch(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openrouter")
    assert provider_name() == "openrouter"
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    assert provider_name() == "gemini"
    monkeypatch.delenv("AI_PROVIDER")
    assert provider_name() == "gemini"  # default


def test_provider_autodetect_openrouter_key(monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-x")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert provider_name() == "openrouter"
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")  # both keys, no switch -> gemini default
    monkeypatch.setenv("AI_PROVIDER", "")
    assert provider_name() == "gemini"
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def test_enhance_ok_path_monkeypatched(monkeypatch):
    monkeypatch.setattr(O, "_post_chat", _canned_ok)
    out = O.enhance_triage({"equipment_type": "Centrifugal Pump"}, [], [], {},
                           api_key="fake-key")
    assert out["status"] == "ok"
    assert out["data"]["possible_causes"][0]["title"] == "Seal wear"


def test_malformed_model_json_is_failure(monkeypatch):
    monkeypatch.setattr(O, "_post_chat", lambda *a, **k: json.dumps(
        {"choices": [{"message": {"content": "{not json"}}]}))
    out = O.enhance_triage({}, [], [], {}, api_key="fake-key")
    assert out["status"] == "failure" and "malformed JSON" in out["message"]


def test_provider_error_envelope_is_failure(monkeypatch):
    monkeypatch.setattr(O, "_post_chat", lambda *a, **k: json.dumps(
        {"error": {"message": "No auth credentials"}}))
    out = O.enhance_triage({}, [], [], {}, api_key="fake-key")
    assert out["status"] == "failure" and "No auth credentials" in out["message"]


def test_retired_model_404_is_clean_and_helpful():
    import io
    import urllib.error
    body = json.dumps({"error": {"message": "This model is unavailable for free.", "code": 404}}).encode()
    exc = urllib.error.HTTPError("http://x", 404, "Not Found", {}, io.BytesIO(body))
    msg = O._clean_http_error(exc)
    assert "This model is unavailable for free." in msg
    assert "OPENROUTER_MODEL" in msg and "{\\u" not in msg and '"error"' not in msg


class _Resp:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.payload


def _http_error(code: int, body: dict, headers: dict | None = None):
    import io
    import urllib.error
    return urllib.error.HTTPError("http://x", code, "Err", headers or {},
                                  io.BytesIO(json.dumps(body).encode()))


def test_retry_then_success(monkeypatch):
    import urllib.request
    calls, sleeps = [], []
    ok = json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode()
    err_body = {"error": {"message": "rate-limited", "code": 429}}

    def fake(req, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            raise _http_error(429, err_body)
        return _Resp(ok)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    monkeypatch.setattr(O, "_sleep", lambda s: sleeps.append(s))
    assert O._post_chat("hi", "k", 5) == ok.decode()
    assert len(calls) == 2 and len(sleeps) == 1


def test_retry_exhaustion_is_clean_failure(monkeypatch):
    import urllib.request
    nested = {"error": {"message": "Provider returned error", "code": 429,
                        "metadata": {"raw": "model is temporarily rate-limited upstream",
                                     "remedy_hint": "Retry shortly"}}}
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=None: (_ for _ in ()).throw(_http_error(429, nested)))
    monkeypatch.setattr(O, "_sleep", lambda s: None)
    out = O.enhance_triage({}, [], [], {}, api_key="fake-key")
    assert out["status"] == "failure"
    assert "temporarily rate-limited upstream" in out["message"]  # nested raw surfaced
    assert "free shared pool is busy" in out["message"]
    assert '"error"' not in out["message"]  # no raw JSON blob


def test_run_triage_openrouter_without_key_stays_local():
    from triage.reasoning import run_triage
    report = {"equipment_type": "HVAC Unit", "equipment_id": "HVAC-OR",
              "issue_description": "Weak cooling in server room since morning",
              "operating_events": "none observed", "sensor_readings": {}}
    t = run_triage(report, SECTIONS, ai_provider="openrouter")
    assert t["ai"]["provider"] is None  # no key -> local
    assert t["ai"]["draft_status"] == "unavailable"
    assert t["work_order_draft"]["status"] == "draft"
    assert all("UNCONFIRMED" in c["title"] for c in t["possible_causes"])


def test_run_triage_openrouter_enhanced_monkeypatched(monkeypatch):
    from triage.reasoning import run_triage
    monkeypatch.setattr(O, "_post_chat", _canned_ok)
    report = {"equipment_type": "Centrifugal Pump", "equipment_id": "PUMP-OR",
              "issue_description": "Water dripping from the pump seal housing",
              "operating_events": "none observed", "sensor_readings": {}}
    t = run_triage(report, SECTIONS, ai_provider="openrouter", ai_key="fake-key")
    assert t["ai"]["provider"] == "openrouter"
    assert t["ai"]["draft_status"] == "ok"
    assert any(c.get("source") == "openrouter" for c in t["possible_causes"])
    assert t["work_order_draft"]["status"] == "draft"


def test_dotenv_loader(tmp_path, monkeypatch):
    import config
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nOPENROUTER_API_KEY=sk-or-dotenv\nEMPTY=\n", encoding="utf-8")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    config._load_dotenv()
    assert config.os.environ["OPENROUTER_API_KEY"] == "sk-or-dotenv"
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


def test_fallback_models_default_and_override(monkeypatch):
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_FALLBACKS", raising=False)
    defaults = O.fallback_models()
    assert defaults and all(":free" in m for m in defaults)
    assert O.model_name() not in defaults  # primary never duplicated
    monkeypatch.setenv("OPENROUTER_FALLBACKS", "a-free-model:free, b-free-model:free")
    assert O.fallback_models() == ["a-free-model:free", "b-free-model:free"]


def test_default_model_is_paid_reliable():
    import os
    assert os.environ.get("OPENROUTER_MODEL", O.model_name()) == O.model_name()
    assert O.model_name() == "openai/gpt-4o-mini"


def test_ai_debug_never_leaks_key():
    import app as appmod
    c = appmod.app.test_client()
    r = c.post("/api/ai/debug", json={"ai_api_key": "sk-or-SECRET-KEY-1234567890"})
    j = r.get_json()
    assert j["ok"] and j["key_present"] and j["key_length"] == len("sk-or-SECRET-KEY-1234567890")
    assert j["key_head"] == "sk-or-S" and j["key_tail"] == "7890"
    assert "sk-or-SECRET-KEY-1234567890" not in str(j)  # full key nowhere in response
    assert j["provider"] in ("gemini", "openrouter")


def test_request_carries_fallback_array(monkeypatch):
    import urllib.request
    seen = {}

    class _Req:
        def __init__(self, url, data, headers, method):
            seen["body"] = json.loads(data.decode())

    def fake_urlopen(req, timeout=None):
        raise AssertionError("should not reach network in this test")

    monkeypatch.setattr(urllib.request, "Request", _Req)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    try:
        O._post_chat_once("hi", "k", 5)
    except AssertionError:
        pass
    assert seen["body"]["model"] == O.model_name()
    assert seen["body"]["models"] == O.fallback_models() and seen["body"]["models"]


def test_used_model_propagates_to_triage(monkeypatch):
    from triage.reasoning import run_triage

    def canned_with_model(*a, **k):
        inner = {"possible_causes": []}
        return json.dumps({"model": "google/gemma-4-26b-a4b-it:free",
                           "choices": [{"message": {"content": json.dumps(inner)}}]})

    monkeypatch.setattr(O, "_post_chat", canned_with_model)
    report = {"equipment_type": "HVAC Unit", "equipment_id": "HVAC-U",
              "issue_description": "Weak cooling in server room since morning",
              "operating_events": "none observed", "sensor_readings": {}}
    t = run_triage(report, SECTIONS, ai_provider="openrouter", ai_key="fake-key")
    assert t["ai"]["draft_status"] == "ok"
    assert t["ai"]["model"] == "google/gemma-4-26b-a4b-it:free"
