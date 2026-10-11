# AGENT_USAGE.md — how this app was built with AI assistance

## 1. Tools used

Built with an AI coding agent (OpenCode) using direct tool calls — no task was delegated to subagents (all work was done inline so the full safety chain stayed in one context):

| Tool | Used for |
|---|---|
| `read` / `glob` / `grep` | Auditing every spec point against the code (evidence before claims) |
| `write` / `edit` | Creating `app.py`, `triage/*`, `store/*`, `static/*`, tests, docs, Docker files |
| `shell` | `pip install`, `pytest`, `node --check`, Flask-test-client smoke runs, `git status/log` secret audit |

Representative verification commands (all executed, not assumed):

```powershell
python -m pytest -q            # 24 passed
node --check static/app.js     # JS-OK
python -c "import app; ..."    # end-to-end: create → follow-up → confirm → edit → blocked anon approval → approve → history → /api/logs leak check
git show HEAD:.env.example     # confirmed a pasted API key was never committed
```

## 2. Representative prompts (user requests, condensed)

1. *"Build an Equipment Maintenance Triage Assistant … with no errors or mistakes"* → initial Flask + SQLite + local retrieval/rules build (12 tests).
2. *"It doesn't have good UI … every aspect developed"* → real OpenRouter integration (`triage/openrouter.py`, validated merge, rules-floor priority) + full cream-theme UI rebuild (19 tests).
3. *"Use white creamish tone for professional look"* → walnut/cream redesign (mid-build style steer, applied immediately).
4. *"Are all these points developed or not — and if not, do it right now"* → evidence audit (19/20), fixed the one gap (operating events now required), re-verified (20 tests).
5. *"Add: frontend, backend, persistence, AI workflow with approval, states, structured logs, focused tests, deployment, README, AGENT_USAGE.md, .env.example"* → this round: `triage/applog.py`, `/api/logs`, dashboard activity panel, `Dockerfile`/`render.yaml`, expanded README, this file (24 tests).

## 3. Delegated work

None — no subagents were spawned. Every file was written and verified in the main session so guardrails (draft-only work orders, UNCONFIRMED labelling, no secret logging) could be checked holistically.

## 4. Important agent mistakes & rejected suggestions

| # | Mistake / risk | How caught | Fix |
|---|---|---|---|
| 1 | Seed KB code first landed in `triage/__init__.py` instead of its own module | Immediate read-back | Moved to `triage/kb_seed.py`, `__init__.py` left empty |
| 2 | Duplicate `woSave` click handler in `app.js` (double-PATCH on save) | Read-back during review | Removed the generic handler, kept the explicit one |
| 3 | `operating_events` accepted empty though the spec lists it as required | Point-by-point evidence audit (grep showed `or ""` default) | Required in backend + wizard + new regression test |
| 4 | **Secret incident:** a real-looking `GEMINI_API_KEY` was found sitting in working-copy `.env.example` | `read` during the docs round | Scrubbed immediately, repo-wide grep confirms zero matches, `git show HEAD:.env.example` proved it was never committed; user advised to rotate the key since it lived in plaintext |
| 5 | First LLM design was a stub that never called any provider | Spec re-read ("functional AI agent/LLM workflow") | Replaced with real Gemini REST + validated merge + loud failures |
| 6 | Frontend Gemini key widget (sidebar card + settings modal) | User request: all AI UI must come from the backend, never handle keys in the frontend | Removed the card, modal, session-key storage and key header entirely; mode is now backend-driven (`GEMINI_API_KEY` env only), frontend only displays the reported mode |
| 7 | User provided OpenRouter key instead of Gemini | Add OpenRouter as an equal provider | New `triage/openrouter.py` (same validated contract), `AI_PROVIDER` switch, `.env` auto-load, provider-aware status/test endpoints; test caught a hardcoded `"source": "gemini"` mislabel — fixed with a `source` parameter |
| 8 | Suite picked up the developer's real `.env` key and made a live OpenRouter call mid-test | 4 failures after the user configured a real key | New `tests/conftest.py` autouse fixture strips all AI env vars; suite is hermetic again (0.38 s, zero network) |
| 9 | Free-model 429 rate limits + raw-JSON error banners | User screenshots | Automatic retry (3 attempts, honors `Retry-After`); nested `metadata.raw` message extraction so banners read as sentences; model default refreshed from live free list |
| 10 | 429s persisting on a saturated free pool | User screenshot | In-request fallback via OpenRouter `models` array (`OPENROUTER_FALLBACKS` override) + banner names the model that actually answered |
| 11 | 8 UI upgrades in one go (supersede, filters, units, picker, print, tour, structured follow-ups, a11y) | User request | Backend: supersede-on-retriage, `GET /api/equipment`, ID normalisation; frontend: chips/selects/toggles/picker/print CSS/tour/reading inputs/skip-link; old test updated to edit the active draft |
| 12 | Verification cleanups deleted the user's real `data/app.db` (reports vanished) | User report: "can't see any pre reports" | Apologised; `store/db.py` now resolves the DB path per call + `tests/conftest.py` redirects all test I/O to temp dirs (proven byte-identical `data/` before/after suite); no cleanup step ever needed again |

Design alternatives considered and **rejected**: storing the Gemini key server-side (rejected — session header/env only, structurally un-loggable); letting AI set final priority (rejected — deterministic rules floor, AI may only escalate); adding numpy/scikit-learn for retrieval (rejected — stdlib TF-IDF keeps installs error-free); auto-approving low-risk work orders (rejected — violates the brief's human-approval rule).

## 5. How the output was verified

- **Tests:** 24 pytest cases pinning rules, retrieval, merge guardrails, logging, validation and the no-remote-control invariant — all passing, no network/key needed.
- **Live smoke runs:** Flask test client exercising create → follow-up → confirm → edit → anonymous-approval rejection (400) → approval → history → log-tailing, including a leak check that no key material appears in `/api/logs`.
- **Static checks:** `node --check` on the frontend; repo-wide grep for secret patterns; `git log`/`git show` to confirm the secret never entered history.
- **Fresh-state runs:** `data/app.db` + `data/app.log` removed after every verification round so the delivered workspace starts clean; `.gitignore` covers `data/*.db`, `data/*.log`, `.env`.
