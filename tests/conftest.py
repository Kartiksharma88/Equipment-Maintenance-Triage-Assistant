"""Test isolation: AI provider env vars must never leak into tests.

Developers run the suite with their real .env present (auto-loaded by
config.py) — without this fixture, tests would flip providers, pick up real
keys, and even make billable network calls (as happened once: a 'no key'
test went out to OpenRouter with the developer's real key).
"""
import pytest

_AI_ENV_VARS = ("AI_PROVIDER", "OPENROUTER_API_KEY", "GEMINI_API_KEY",
                "GOOGLE_API_KEY", "OPENROUTER_MODEL", "GEMINI_MODEL")


@pytest.fixture(autouse=True)
def _isolate_ai_env(monkeypatch):
    for var in _AI_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _hermetic_data_dir(tmp_path, monkeypatch):
    """Redirect ALL file writes (DB + logs) to a temp dir.

    Lesson learned the hard way: an earlier workflow deleted the real
    data/app.db during 'cleanup', wiping the user's reports. The suite now
    cannot see or touch real data, so no cleanup step is ever needed again.
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TRIAGE_LOG_DISABLE", "1")
    from triage import applog
    applog.reset_logger()
    from store.db import init_db
    init_db()  # fresh schema in the temp dir for this test
    yield
    applog.reset_logger()
