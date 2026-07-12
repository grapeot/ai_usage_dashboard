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
- Cursor: optional `CURSOR_COOKIE` in `.env`.
- GLM/Z.ai: optional `GLM_BEARER_TOKEN` in `.env`. When present, the dashboard also fetches the coding-plan quota snapshot (5-hour / weekly token quotas and the monthly web-search/reader/zread quota) and prints it after the token table; the same snapshot is embedded in the JSON payload under `glm_quota`.
- Ollama: optional `OLLAMA_COOKIE` in `.env` (full browser cookie string from ollama.com/settings). When present, the dashboard fetches the settings HTML and parses the Session (5h) and Weekly usage bars into the unified `quotas` array.
- Codex: no key required. The dashboard reads `rate_limits` from the local Codex session JSONL.

Never print `.env`, cookies, bearer tokens, generated usage exports, or local dashboard JSON unless the user explicitly asks for a sanitized excerpt.

## Commands

All commands run from the repo root.

```bash
.venv/bin/python auto_usage.py -d 7
.venv/bin/python auto_usage.py -d 30 --skip-desktop-chart
.venv/bin/python auto_usage.py -d 7 --no-cost
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
- Claude
- GPT
- DeepSeek
- Other
- Total
- AI Hours
- Est. $, unless `--no-cost` is used

It may write local artifacts:

- `token_usage_dashboard.png`: desktop chart, local/private generated output.
- `token_usage_eink.json`: E1002 display payload, local/private generated output.
- `usage.json`, `cursor.csv`, `glm.json`, `glm_quota.json`, `ollama_settings.html`: raw provider exports, local/private generated output.

These files are intentionally gitignored.

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
git check-ignore .env token_usage_eink.json token_usage_dashboard.png usage.json cursor.csv glm.json glm_quota.json ollama_settings.html update.log tmp/example.txt
```

Also run a privacy scan for fixed LAN IPs, personal absolute paths, private deployment hostnames, old workspace paths, and secret-manager references.

If firmware changed and Arduino tooling is available, compile `eink/e1002/e1002.ino` with the ESP32-S3 settings documented in `docs/test.md`.

## Known Caveats

- Cursor and GLM exports require private credentials and should be treated as optional.
- OpenCode archive support depends on a separate `opencode_skill` installation or path.
- The e-ink firmware is a companion project; Python tests mirror only its pure logic, not hardware behavior.
