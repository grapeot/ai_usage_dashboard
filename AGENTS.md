# AI Usage Dashboard

## What This Repo Is

This project aggregates local AI usage data from Codex, Cursor, GLM/Z.ai, Claude Code, OpenCode, and DeepSeek Harness (DSH). It can print daily token/cost tables, generate a desktop chart, emit an E1002-friendly JSON payload, and serve that payload through a local FastAPI service. The FastAPI service exposes typed Pydantic response models (`dashboard_models.py`) so `/openapi.json` is self-describing for AI agents.

The repository is being prepared for public GitHub publication. Public files must contain only fake examples, generic paths, and local-only setup instructions.

## Working Environment

Use the project `.venv`. Dependencies are managed with `uv`.

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/python -m pytest tests/ -v
```

Core commands:

```bash
.venv/bin/python auto_usage.py -d 7
.venv/bin/python auto_usage.py -d 30 --skip-desktop-chart
.venv/bin/python -m uvicorn local_display_service:app --host 127.0.0.1 --port 7995
```

## Privacy Boundaries

Never commit `.env`, cookies, bearer tokens, Wi-Fi credentials, generated usage exports, charts, local JSON payloads, logs, or local databases. The public repo may document paths such as `~/.codex` or `~/.local/share/opencode` as generic local data sources, but must not include personal absolute paths, private hostnames, fixed LAN IPs, or real usage artifacts.

E1002 local configuration belongs in `eink/e1002/secrets.h`; only `secrets.h.example` is public.

Before copying `eink/e1002/secrets.h.example` to `eink/e1002/secrets.h`, check whether `secrets.h` already exists. Never overwrite an existing `secrets.h`; it may contain the user's real Wi-Fi and dashboard configuration and is gitignored, so git cannot recover it.

## Code Boundaries

Keep this pass scaffold-oriented. Do not move root scripts into `src/` unless a migration is explicitly requested. Preserve existing command entry points (`auto_usage.py`, `codex_usage.py`, `opencode_token_analyzer.py`, `local_display_service.py`) because local workflows and tests depend on them.

`eink/` is a hardware companion subproject. Python unit tests cover mirrored pure logic; Arduino compile checks cover firmware integrity when Arduino tooling is available.

## Validation

After scaffold or code changes, run:

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"
```

Also run a privacy scan for fixed LAN IPs, personal absolute paths, private deployment hostnames, old workspace paths, and secret-manager references.

If firmware files changed and `arduino-cli` is installed, also compile `eink/e1002/e1002.ino` with the documented ESP32-S3 FQBN.
