# AI Usage Dashboard Skill

## When To Use

Use this skill when the user asks to summarize local AI usage, estimate API-equivalent cost, inspect recent OpenCode or Claude Code token usage, generate an AI usage chart, or refresh the optional local e-ink dashboard JSON.

This skill is local-first. It reads data from the user's machine and writes local artifacts. It does not upload usage data anywhere.

## Prerequisites

- Working directory: the `ai_usage_dashboard` repo root.
- Python environment: `.venv/` created with `uv`.
- Optional private config: `.env`, copied from `.env.example`.
- Codex and Claude Code: no key required when their default local logs exist.
- OpenCode: local DB support by default; optional archive support can point `AI_USAGE_OPENCODE_SKILL_PATH` to an `opencode_skill` checkout.
- Cursor: optional `CURSOR_COOKIE` in `.env` (full browser cookie string from cursor.com while logged in). When present, the dashboard exports the usage CSV and also fetches `GET /api/usage-summary` to parse the two monthly quota windows shown on the Cursor spending page into the unified `quotas` array: `Cursor Models` (the "Cursor models" bar = Composer + Cursor's own models, `autoPercentUsed`) and `Cursor Other` (the "Other models" bar = other named/frontier models, `apiPercentUsed`). Both reset at the billing-cycle end (`billingCycleEnd`). Note `totalPercentUsed` is the whole-pool gauge behind the "X% of your included total usage" message, not a bar value. An expired cookie yields a non-JSON login page, which is rejected so the last good cached snapshot stays intact.
- GLM/Z.ai: optional `GLM_BEARER_TOKEN` in `.env`. When present, the dashboard fetches the coding-plan **quota** snapshot (5-hour / weekly token quotas and the monthly web-search/reader/zread quota) and prints it after the token table; the same snapshot is embedded in the JSON payload under `glm_quota`. GLM **usage** comes from the local OpenCode database, not the cloud API.
- Ollama: optional `OLLAMA_COOKIE` in `.env` (full browser cookie string from ollama.com/settings). When present, the dashboard fetches the settings HTML and parses the Session (5h) and Weekly usage bars into the unified `quotas` array.
- Codex: no key required. Usage is read from the local rollout JSONL under `~/.codex/sessions` (timestamp-precise, per-turn input/cache/output split); the same files provide `rate_limits` for quota.

Never print `.env`, cookies, bearer tokens, generated usage exports, or local dashboard JSON unless the user explicitly asks for a sanitized excerpt.

## Commands

All commands run from the repo root.

```bash
.venv/bin/python auto_usage.py -d 7
.venv/bin/python auto_usage.py -d 30 --skip-desktop-chart
.venv/bin/python auto_usage.py -d 7 --no-cost
.venv/bin/python auto_usage.py --from 2026-10-05T09:00 --to 2026-10-05T18:00
.venv/bin/python auto_usage.py --from 2026-10-05 --provider deepseek
.venv/bin/python -m history_store --provider ollama --days 7
.venv/bin/python -m history_store --usage --days 30
.venv/bin/python opencode_token_analyzer.py --provider anthropic --hours 5
.venv/bin/python -m uvicorn local_display_service:app --host 127.0.0.1 --port 7995
```

Convenience wrappers:

```bash
scripts/ai-usage -d 7
scripts/ai-usage-service
scripts/update-local-artifacts
```

## Output Contract

`auto_usage.py` prints a daily table with these public categories:

- Cursor
- GLM
- Gemini
- Claude
- GPT
- DeepSeek
- Grok
- Qwen
- Other
- Total
- AI Hours
- Est. $, unless `--no-cost` is used

Optional Grok weekly usage pool: set `GROK_COOKIE` in `.env` (browser cookie from grok.com while logged in). The dashboard calls `POST /grok_api_v2.GrokBuildBilling/GetGrokCreditsConfig` and adds a `provider=grok` weekly quota snapshot. Proto3 omits default float `0.0`, so a 0% week has no `credit_usage_percent` field; the parser treats that as 0% so the bar still renders. Never commit real cookies.

It may write local artifacts:

- `token_usage_dashboard.png`: desktop chart, local/private generated output.
- `token_usage_eink.json`: E1002 display payload, local/private generated output.
- `usage.json`, `cursor.csv`, `glm.json`, `glm_quota.json`, `ollama_settings.html`, `cursor_usage_summary.json`: raw provider exports, local/private generated output.
- `quota_history.db`: append-only SQLite history of quota readings and per-day usage (see below).

These files are intentionally gitignored.

## Exact-Range Aggregation

`auto_usage.py --from <bound> --to <bound>` aggregates usage over an exact
`[from, to)` interval using local event timestamps, instead of calendar days.
This is the tool for aligning a quota-bar window with actual usage (e.g. "how
many tokens did DeepSeek use between 14:00 and 18:00?"). Bounds accept a full
ISO datetime or a bare date (local midnight); a bare `--from` with no `--to`
means that whole day. `--provider <bucket>` filters to one provider.

Every local source (OpenCode, DSH, Claude Code, Codex, Antigravity) is
timestamp-precise; Cursor depends on a recent cloud CSV export. A full-day
window equals the dashboard's day row (same tokens and cost): cost uses the
canonical split and the same provider basis (Cursor tokens are counted but not
priced, matching the dashboard).

## Plan Capacity Estimation

Use `plan_usage` to estimate workload-dependent API-equivalent capacity for a
subscription, rather than merely report dollars already consumed.

```bash
.venv/bin/python -m plan_usage --all-plans --days 7 --json
.venv/bin/python -m plan_usage --plan ollama --window 7d --days 7 --json
.venv/bin/python -m plan_usage --plan codex --window 7d --days 7 --json
```

Supported plans: `ollama`, `grok`, `codex`, `claude`, `antigravity`, `cursor`,
`glm`. Use `--window` for a window kind or exact label, `--history-db` for a
database override, and `--min-change` for the minimum percentage-point movement
(default 3). Analysis reads existing history and local logs; it never refreshes
providers, reads announcements, creates/migrates a database, or sends synthetic
model prompts. If samples are needed, use the existing genuine dashboard
refresh path when requested.

An acceptable answer includes the actual measurement interval and coverage,
sample count and movement, billing scope, explicit price basis, estimate and
diagnostics. Use `plans[].windows` and `reset_events` as evidence. Report every
selected plan's state, including unsupported or incomplete measurements.

- **Billing scope:** Match actual subscription routes, not model or client
  names. OpenCode OAuth and paid keys are distinct; Codex `model_provider` may
  identify Ollama; Claude credentials and third-party overrides matter; Cursor
  included and overage calls differ. Historical logs without identity metadata
  rely on current routing assumptions, not a complete ledger audit.
- **Pricing:** Ollama uses its model/time schedule; other plans use current API
  reference prices; Cursor uses included-call charged cents. Missing prices
  produce `unpriced_usage`, never a fabricated zero. `known_limit` requires an
  explicit, correctly scoped `absolute_limit_usd`, not an untyped raw limit.
- **Evidence:** Require three distinct snapshots and sufficient quota change.
  Preserve fractions through parser/history/API; e-ink alone rounds. Do not
  invent decimals for legacy readings or calibrate from cached/local-log
  replays. Flat, saturated, missing, or incomplete data needs a diagnostic state.
- **Resets:** For Codex, **the god of reset Tibo might interfere with the measurement**.
  The program observes reset changes/counter drops, splits
  segments, and knows only that early resets are possible. It neither consults
  announcements nor identifies the cause. Never pair readings across a refill.
- **Interpretation:** Endpoint bands describe rounding sensitivity, not
  statistical confidence. Fit quality does not prove complete coverage or
  future policy. Monthly equivalents assume ordinary weekly cadence without
  extra resets; never extrapolate a five-hour burst limit into a month.

Use `estimated` or `known_limit` only with their stated assumptions. Inspect
`usage_missing` / `usage_incomplete`, `quota_scope_unresolved`, `saturated`,
`quota_stale`, `quota_unavailable`, window-identity states, or
`unstable_measurement` before making a capacity claim. Antigravity family
maxima do not identify shared billing allocations; detected native Grok CLI
activity is not covered by the token adapters. Genuine missing data is not $0
capacity, and a meter that does not move is not an unlimited plan.

## Quota & Usage History

The dashboard overwrites `token_usage_eink.json` on every run, so the previous snapshot is lost. `history_store.py` persists the temporal dimension to a local, gitignored SQLite database (`quota_history.db`, override with `AI_USAGE_HISTORY_DB`):

- `quota_samples` — a time series of provider quota bars. One row is written on every observation (no dedup, no retention policy yet), so the table records how long each percentage stayed flat. The reset timestamp is normalized to the minute so provider jitter (`now + TTL` recomputed per request) cannot look like a window change. This is the primary signal: quota bars are live readings scraped from provider pages and cannot be rebuilt from any source database once a window rolls over.
- `usage_daily` — the dashboard's per-day aggregate token/cost rows, UPSERTed by date. Secondary, because token volume is always rebuildable from the source databases.

Both are written best-effort from `auto_usage.record_history()`; a history failure is logged and swallowed so it can never break a dashboard run.

History is written only on a genuine refresh. `build_latest_dashboard_payload` is a pure read by default (`record=False`); the refresh paths (`auto_usage.py` CLI and `POST /api/v1/display/update`) pass `record=True`, while read-only paths (a cold cache hit on `GET /token_usage.json`, `GET /api/v1/quotas`) never touch the database, so a pure read cannot manufacture a sample.

The writer is the E1002 firmware: it wakes hourly and POSTs `/api/v1/display/update`, which runs the full build and records a sample. No separate scheduled sampler exists.

Query the history from the CLI or the API:

```bash
.venv/bin/python -m history_store --provider ollama --days 7
.venv/bin/python -m history_store --usage --days 30
curl -s 'http://127.0.0.1:7995/api/v1/quota-history?provider=ollama&days=7'
curl -s 'http://127.0.0.1:7995/api/v1/usage-history?days=30'
```

## Local Display Service

The FastAPI service exposes:

```text
GET  /health
GET  /token_usage.json
GET  /api/v1/quotas
POST /api/v1/display/update
POST /api/v1/antigravity/ingest
```

Responses are typed by Pydantic models in `dashboard_models.py`
(`DashboardPayload`, `DashboardSummary`, `DailyEntry`, `GlmQuotaSnapshot`,
`QuotaSnapshot`, `AutomationQuotaSnapshot`, `QuotasResponse`, `HealthResponse`, `UpdateRequest`, `AntigravityIngestRequest`,
`AntigravityIngestResponse`). Every field carries a description, so
`/openapi.json` is self-describing for AI agents: the response schema for
`/token_usage.json` is a `$ref` to `DashboardPayload` rather than an opaque
object.

### Quota Automation

Use `GET /api/v1/quotas` when the user or an automation needs only current
quota availability and reset times. Do not download `/token_usage.json` and
manually extract `quotas` for this use case.

```bash
curl -s http://127.0.0.1:7995/api/v1/quotas
```

Response shape:

```json
{
  "generated_at": "2026-07-11T22:57:55",
  "quotas": [
    {
      "provider": "codex",
      "label": "5h",
      "used_percentage": 29,
      "remaining_percentage": 71,
      "next_reset_time_ms": 1783842841000,
      "next_reset_iso": "2026-07-12T00:54:01",
      "usage": null,
      "remaining": null
    }
  ]
}
```

Interpretation:

- `used_percentage` and `remaining_percentage` always sum to 100.
- `next_reset_time_ms` is the machine-friendly epoch-millisecond reset time.
- `next_reset_iso` is the same reset in local ISO form.
- `usage` and `remaining` are absolute counts only when the provider exposes
  them; null does not mean zero.
- `generated_at` identifies snapshot freshness.

This endpoint is strictly cache-only. It reads the in-memory dashboard snapshot
or `token_usage_eink.json` and never contacts providers. If neither cache exists,
it returns `{"generated_at": null, "quotas": []}`. An empty array therefore
means no cached quota snapshot, not necessarily that the account has no quota.

When the user explicitly needs fresh provider data, refresh once and then read
the compact endpoint:

```bash
curl -s -X POST http://127.0.0.1:7995/api/v1/display/update \
  -H 'Content-Type: application/json' \
  -d '{"reason":"automation","view":"30d","device_id":"local"}' >/dev/null
curl -s http://127.0.0.1:7995/api/v1/quotas
```

For routine polling, use only `GET /api/v1/quotas`; do not force a full refresh
on every poll.

`POST /api/v1/display/update` accepts:

```json
{
  "reason": "force_button",
  "view": "7d",
  "device_id": "example-device"
}
```

It returns the same dashboard JSON shape used by `token_usage_eink.json`: `meta`, `summary`, and `daily`.

`POST /api/v1/antigravity/ingest` accepts:

```json
{
  "entries": [
    {"model": "gemini-3-flash-a", "timestamp": 1711447200000,
     "input": 1000, "output": 200, "cache_read": 5000,
     "cache_write": 0, "thinking": 50, "response_id": "r1",
     "session_id": "s1"}
  ],
  "source": "macbook-air"
}
```

It deduplicates by `response_id` against the local `antigravity_usage_cache.json`, persists the merged set, and returns `{"received": N, "new": M, "duplicate": K, "total_cache": T}`. Intended for cross-machine aggregation — see `skills/skill_antigravity_push.md` for the satellite-side workflow.

## E-Ink Reference Implementation

`eink/` is optional. It is a reference implementation for Seeed Studio reTerminal E1002, not part of normal setup. Most users can ignore it.

Only create `eink/e1002/secrets.h` when compiling or flashing that hardware sketch. The public `secrets.h.example` shows the required placeholders; real Wi-Fi credentials, local service URLs, and device IDs stay in the ignored private file.

## Privacy Rules

- Treat all generated usage files as private.
- Keep real provider credentials only in `.env`.
- Keep Wi-Fi credentials and E1002 service URLs only in `eink/e1002/secrets.h`; ordinary users do not need this file.
- Public docs must use fake hosts such as `YOUR_LOCAL_HOST` and fake tokens such as `replace-with-your-real-token`.
- Do not add personal absolute paths, private hostnames, fixed LAN IPs, or real usage screenshots to public files.

## Validation

Use these checks after changes:

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"
git check-ignore .env token_usage_eink.json token_usage_dashboard.png usage.json cursor.csv glm.json glm_quota.json ollama_settings.html cursor_usage_summary.json update.log tmp/example.txt
```

Also run a privacy scan for fixed LAN IPs, personal absolute paths, private deployment hostnames, old workspace paths, and secret-manager references.

If firmware changed and Arduino tooling is available, compile `eink/e1002/e1002.ino` with the ESP32-S3 settings documented in `docs/test.md`.

## Known Caveats

- Cursor exports require a private credential and should be treated as optional.
- OpenCode archive support depends on a separate `opencode_skill` installation or path.
- The e-ink firmware is a companion project; Python tests mirror only its pure logic, not hardware behavior.
- The E1002 panel runs in 1-bit mode: solid colors render black, mid-gray renders white (a full bar looks empty). Only black or white+pattern fills are reliably visible. Read `eink/e1002/README.md` ("Panel Color Behavior") before choosing any fill color.
- The display service keeps an in-memory payload snapshot; code changes to `auto_usage.py` (labels, quota fields) require a service restart plus a `POST /api/v1/display/update` refresh before the e-ink sees them.
