# AI Usage Dashboard

AI Usage Dashboard is a local-first token usage dashboard for Codex, Claude Code, OpenCode, Cursor, and GLM/Z.ai. It aggregates local usage data, estimates API-equivalent cost from public pricing assumptions, and can produce a terminal table, a desktop chart, and an optional e-paper JSON payload.

It is not a cloud monitoring service and does not upload your usage data. Raw exports, generated charts, JSON payloads, and local logs stay on your machine and are excluded by `.gitignore`.

## Installation

```bash
git clone <this-repo> ai_usage_dashboard
cd ai_usage_dashboard
cp .env.example .env
uv venv .venv
uv pip install --python .venv/bin/python -e '.[dev]'
```

`.env` is only for local private configuration. Start by enabling only the data sources you actually use.

## Data Source Setup

You do not need every platform connected on day one. The tool enables each source independently based on what exists on your machine:

- **Codex**: If you use Codex CLI, the tool reads local Codex sessions by default. No API key is required.
- **Claude Code**: If you use Claude Code, the tool reads local Claude Code project JSONL logs by default. No API key is required.
- **OpenCode**: If you use OpenCode, the tool reads the main local OpenCode SQLite database by default. If you also use `opencode_skill` for archive querying, set `AI_USAGE_OPENCODE_SKILL_PATH` in `.env`.
- **Cursor**: To include Cursor dashboard exports, set `CURSOR_COOKIE` in `.env`. This browser cookie must stay private.
- **GLM/Z.ai**: To include the GLM/Z.ai usage API, set `GLM_BEARER_TOKEN` in `.env`. This bearer token must stay private.

A minimal `.env` can be empty. The tool will still use local sources it can discover automatically; sources without credentials are skipped or read from existing local caches.

The current version uses each tool's default local data directory. If you need custom paths, prefer the source tool's own configuration or explicit variables such as `AI_USAGE_OPENCODE_SKILL_PATH`. Do not put personal absolute paths in public documentation.

## Usage

```bash
# Last 7 days
.venv/bin/python auto_usage.py -d 7

# Last 30 days, text + E1002 JSON only, no desktop chart
.venv/bin/python auto_usage.py -d 30 --skip-desktop-chart

# Skip cost estimation
.venv/bin/python auto_usage.py -d 7 --no-cost
```

Outputs:

- Terminal table: daily token counts, AI Hours, and estimated cost.
- `token_usage_dashboard.png`: desktop matplotlib chart.
- `token_usage_eink.json`: structured JSON for the E1002 and local display service.

These files are local private artifacts and are ignored by default.

## Local Display Service

The FastAPI service can serve the latest dashboard JSON to local devices such as an e-paper display.

```bash
scripts/ai-usage-service
```

Default endpoints:

- `http://127.0.0.1:7995/health`
- `http://127.0.0.1:7995/token_usage.json`
- `http://127.0.0.1:7995/api/v1/display/update`

If a LAN device needs access, configure the host through private local config or your own launch script. Do not commit fixed private IP addresses.

## Data Sources

- Codex: `npx @ccusage/codex@latest --json`
- Cursor: `cursor.com/api/dashboard/export-usage-events-csv`, with a private browser cookie
- GLM/Z.ai: usage API, with a private bearer token
- Claude Code: local Claude Code JSONL session logs
- OpenCode: local OpenCode SQLite database; optional archive support can use a separate `opencode_skill` installation

All paths and credentials are local environment details. The public repository documents contracts only; it does not include real data.

## E-Paper Reference Implementation

`eink/` is optional hardware reference code, not part of the normal installation path. Most users only need the terminal table, desktop chart, and local JSON output.

The current reference implementation targets the **Seeed Studio reTerminal E1002** 800x480 color e-paper display. If you have that device, see `eink/README.md` and `eink/e1002/README.md`.

Hardware configuration lives in `eink/e1002/secrets.h` next to the Arduino sketch. That file contains Wi-Fi and local service URLs and must not be committed. The public template is `eink/e1002/secrets.h.example`:

```cpp
constexpr const char* kWifiSsid = "YOUR_WIFI_SSID";
constexpr const char* kWifiPassword = "YOUR_WIFI_PASSWORD";

#define AI_USAGE_DASHBOARD_UPDATE_URL "http://YOUR_LOCAL_HOST:7995/api/v1/display/update"
#define AI_USAGE_DASHBOARD_CACHED_URL "http://YOUR_LOCAL_HOST:7995/token_usage.json"
#define AI_USAGE_DASHBOARD_DEVICE_ID "example-e1002"
```

Normal setup does not require `secrets.h`. Create it only when compiling or flashing the reTerminal E1002 sketch.

## For AI Agents

The repo-local root skill is:

```text
skills/skill_ai_usage_dashboard.md
```

When a user asks to inspect AI usage, estimate token cost, refresh the local dashboard, or debug the E1002 JSON contract, read this skill first. Workspace-level skill files can point to this file; the repo-local file is the source of truth.

## Development And Verification

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"
git check-ignore .env token_usage_eink.json token_usage_dashboard.png usage.json cursor.csv glm.json update.log tmp/example.txt
```

Before publishing, also run a privacy scan for fixed private IPs, personal absolute paths, private deployment domains, old workspace paths, and secret-manager references.

See `docs/test.md` for the fuller test strategy.

## Privacy

This repository is designed to be publishable with only fake examples. Real cookies, bearer tokens, Wi-Fi credentials, local usage exports, generated charts, generated JSON payloads, and logs must remain in private ignored files.
