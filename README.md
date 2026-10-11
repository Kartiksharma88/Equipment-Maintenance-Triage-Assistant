# Equipment Maintenance Triage Assistant

Report an equipment problem → retrieval-augmented triage (deterministic rules first, **Gemini** enhancement when a key is set) → human-approved work order. Professional cream-themed UI, local-first, SQLite history, structured JSON logs.

## 1. Setup

**Prerequisites:** Python 3.10+ (3.13 tested), `pip`. No Node/build step — the frontend is static HTML/CSS/JS served by Flask.

```powershell
pip install -r requirements.txt
python app.py
# open http://127.0.0.1:5000
```

Easier on Windows: just **double-click `start.bat`** — it installs, opens the browser and starts the server.

Need demo data? `python scripts/seed_demo.py --reset` loads 6 sample reports (mixed urgencies, approvals, a rejection, history) through the real API paths.

**AI enhancement (optional, recommended):** the active provider is chosen by `AI_PROVIDER` (`gemini` default, or `openrouter`). Set the matching key before starting — easiest is copying `.env.example` to `.env` (auto-loaded, gitignored) and filling it in:
- **OpenRouter:** `AI_PROVIDER=openrouter` + `OPENROUTER_API_KEY=sk-or-…` (+ optional `OPENROUTER_MODEL`, default `openai/gpt-4o-mini` — cheap and reliable; use a `:free` model only with no credit). If the primary model is throttled, OpenRouter auto-fails over to fallback models in the same request (`OPENROUTER_FALLBACKS` to override); rate limits are also retried (3 attempts, honors `Retry-After`); if still busy, the banner says so and the local draft stands in. The banner always names the model that actually answered.
- **Gemini:** `AI_PROVIDER=gemini` + `GEMINI_API_KEY=…` (free key at <https://aistudio.google.com/apikey>).
Key handling is server-side only — the UI never asks for or touches keys; it only reflects the backend-reported mode. Test a key without filing a report: `POST /api/ai/test` with `{"ai_provider":"openrouter","ai_api_key":"sk-or-…"}`.

Without a key the app runs fully offline (*Local mode* badge). With a key, triage banners show *✨ Gemini enhanced*; any Gemini failure (bad key, quota, timeout, malformed JSON) appears as an explicit banner and the local draft is used.

**Configuration** (env vars, see `.env.example` — placeholders only, never real secrets):

| Var | Default | Purpose |
|---|---|---|
| `AI_PROVIDER` | `gemini` | Active AI provider: `gemini` or `openrouter` |
| `OPENROUTER_API_KEY` | — | Server-side OpenRouter key (only way to enable it — the UI never handles keys) |
| `OPENROUTER_MODEL` | `openai/gpt-4o-mini` | Any model id from <https://openrouter.ai/models>; cheap and reliable — use a `:free` id only with no credit |
| `GEMINI_API_KEY` | — | Server-side Gemini key (used when `AI_PROVIDER=gemini`) |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Gemini model for enhancement + key test |
| `HOST` / `PORT` | `127.0.0.1` / `5000` | Bind address/port |
| `FLASK_DEBUG` | `1` | Set `0` in production (Dockerfile/gunicorn ignore it) |
| `DATA_DIR` | `./data` | SQLite DB (`app.db`), KB JSON, JSON logs (`app.log`) |
| `TRIAGE_LOG_DISABLE` | — | Set `1` to disable file logging (used by tests) |

## 2. Architecture

```
browser (static/index.html + styles.css + app.js, cream theme, no build)
   │  REST/JSON (+ optional X-Gemini-Api-Key header, never stored)
   ▼
app.py (Flask: validation, human approval gates, audit history, JSON logs)
   ├─ triage/retrieval.py  TF-IDF + equipment-type boost over data/knowledge_base.json
   ├─ triage/rules.py      11 deterministic threshold checks + missing/invalid/conflict analysis
   ├─ triage/gemini.py     Gemini REST via stdlib urllib (validated JSON, loud failures)
   ├─ triage/reasoning.py  merge: local pipeline first, Gemini enhances, rules stay authoritative
   ├─ triage/applog.py     structured JSON logs (key material can never be emitted)
   └─ store/db.py          SQLite: reports, triage_runs, work_orders (history never deleted)
```

**Request flow (`POST /api/reports`):** validate → save report → retrieve manuals → run threshold rules → analyse data quality → local causes/questions/steps/priority → optional Gemini enhancement (validated merge) → save triage run + **draft** work order → return. Follow-up answers re-run the same pipeline and append a new run; confirmed findings are written only by the human-only `/confirm` endpoint.

**Safety invariants (code + tests):** work orders are always created `draft`; only named-human approve/reject changes status (anonymous approval blocked, rejection needs a reason); priority is the *more urgent* of rules vs AI (AI can escalate, never de-escalate below fired rules); AI citations restricted to retrieved `[KB-…]` / fired `[RULE-…]` / `EVENT`; every AI cause forced `(UNCONFIRMED)`; there is intentionally **no** remote-control/actuation endpoint.

## 3. Completed scope

Report wizard (type, normalised ID, issue, **required** events, 8 optional sensors with °C/°F + bar/psi unit toggles) · 19-section / 6-type manual KB with search · retrieval with scores · unconfirmed causes with confidence + citations + evidence · follow-up Q&A with structured reading inputs + re-triage · cited inspection checklist · P1–P4 priority hero with rules floor · draft work-order queue with edit/approve/reject + audit trail + auto-supersede (one active draft per report) · per-equipment history timeline · known-machine picker (`GET /api/equipment`) · dashboard (stats, drafts, activity from logs, first-run tour) · report filters (status/urgency/sort/day groups) + WO text search · printable job card · missing/invalid/conflicting data handling · explicit AI + retrieval failure banners · structured JSON logs + `GET /api/logs` viewer · skip-link, focus rings, live-region toasts.

## 4. Excluded scope (intentionally left out)

Live IoT ingestion, predictive-maintenance models, parts inventory, technician dispatch — per the brief. Also out: multi-user auth/roles (single shared queue; technician identity is a self-declared name field), PostgreSQL (SQLite is enough for the expected load; `DATA_DIR` keeps the swap path open), background job queue (triage runs synchronously; Gemini timeout 30 s), and automaticKB re-indexing (static seed file, TF-IDF in memory).

## 5. Tests

```powershell
pytest -q   # 24 tests, no network, no key needed
```

| File | What it pins |
|---|---|
| `tests/test_triage.py` (13) | All threshold rules, no false positives on empty input, missing/invalid/conflict detection, retrieval ranking + explicit failures, UNCONFIRMED + draft-only guarantees, graceful no-sensor path, events-required validation, no remote-control endpoint |
| `tests/test_gemini.py` (7) | Key resolution order, status shapes, merge forces UNCONFIRMED + rejects fake citations, AI cannot de-escalate below fired rules, escalation allowed, garbage input ignored, confidence clamped |
| `tests/test_logging.py` (4) | JSON log shape, **API key can never be logged** (even if passed by mistake), triage_run event written without key material, `read_recent` empty/cap behaviour |

Also verified: `node --check static/app.js`, and live Flask-test-client smoke runs (create → follow-up → confirm → edit → blocked anonymous approval → approve → history → `/api/logs` leak check).

## 6. UI states

Loading (submit "⏳ Triaging…", skeleton cards, "Testing…" on key test) · Empty (no-triage hero, "Queue clear", "No reports yet", "No results") · Validation (wizard step guards + backend 400 messages in `formErr`) · Success (toasts, "✓ Draft updated", confirmation notes) · Failure (critical/warn/conflict banners, retrieval-failure purple banner, AI-note info banner, error toasts).

## 7. Logs

`data/app.log` — one JSON object per line (`ts, level, event, msg` + context). Events: `app_start, http, report_created, triage_run, confirmed, wo_edited, wo_decision, ai_test`. Rotated at 512 KB × 3. View newest-first in the dashboard *Recent activity* panel or `GET /api/logs?limit=100`. Only `key_source` (request/env/none) is ever recorded — key values are structurally un-loggable (whitelist formatter, covered by test).

## 8. Limitations

- Local retrieval is keyword TF-IDF, not semantic — unusual phrasing may retrieve weakly (surfaced, not hidden).
- Gemini output is constrained by prompt + validator, but a determinedly odd response degrades to the local draft rather than blocking.
- SQLite + single process: fine for team-review use, not for high-concurrency plants (see deployment note on disks).
- Sensor plausibility ranges are generic, not per-asset nameplate values.
- No authentication: anyone with the URL can approve — put it behind your SSO/VPN for production.

## 9. Deployment

**Docker (recommended, any host):** `docker build -t maint-triage .` → `docker run -p 5000:5000 -e AI_PROVIDER=openrouter -e OPENROUTER_API_KEY=... -v triage-data:/srv/data maint-triage` (gunicorn, 2 workers). **`render.yaml` is included** for one-click Render.com Docker deploys (set `OPENROUTER_API_KEY` in their dashboard, attach a disk at `/srv/data` or history resets on redeploy). Any Docker host (Fly.io, Railway, Azure App Service) works the same way. **Local production:** `pip install gunicorn` + `gunicorn "app:app" --bind 0.0.0.0:5000 --workers 2`. Health check: `GET /api/health`. Data persists in `$DATA_DIR` (`app.db`, `knowledge_base.json`, `app.log`) — back up that directory.

## 10. Example report (try it)

Type `Centrifugal Pump`, ID `PUMP-101`, issue *"Grinding noise from bearing housing, hot to touch, vibration rising over 2 days"*, events *"Tripped once yesterday, reset OK."*, sensors `temperature_c=96, vibration_mm_s=8.2` → expect `TEMP_CRIT` + `VIB_CRIT`, **P1**, UNCONFIRMED causes with `[KB-PUMP-…]` citations.
