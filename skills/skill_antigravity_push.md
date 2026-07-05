# Antigravity Cross-Machine Push Skill

## When To Use

Use this skill on a **satellite machine** (e.g. a laptop connected via Tailscale) to push local Antigravity IDE token usage to the central dashboard host (e.g. a Mac Studio that runs 24/7). The dashboard host aggregates data from all machines; without pushing, the satellite's Antigravity usage is invisible to the host's dashboard.

Trigger words: "push antigravity usage", "sync antigravity to host", "push antigravity 用量", "同步 antigravity".

## Prerequisites

- The satellite machine has a clone of the `ai_usage_dashboard` repo with `.venv` set up (`.venv/bin/python` exists).
- `.env` contains `AI_USAGE_DASHBOARD_INGEST_URL` pointing to the dashboard host's ingest endpoint (e.g. `http://<host>.<tailnet>.ts.net:7995/api/v1/antigravity/ingest`).
- The dashboard host's `local_display_service` is running and reachable over Tailscale.
- Antigravity IDE may or may not be running on the satellite — the push sends cached history regardless.

## What To Do

Run the push script from the repo root:

```bash
.venv/bin/python scripts/push-antigravity
```

It does three things:
1. Calls `load_antigravity()` to refresh the local cache from any running Antigravity Language Server.
2. Reads all entries from `antigravity_usage_cache.json`.
3. POSTs them to `AI_USAGE_DASHBOARD_INGEST_URL`.

The host deduplicates by `response_id`, so pushing the same entries multiple times is safe and idempotent.

## Output Contract

The script prints to stdout:
- Number of entries in the local cache.
- The POST result: `received=N new=M duplicate=K total_cache=T`.

On success (HTTP 200), the satellite's Antigravity usage is now visible on the dashboard host. On the next `POST /api/v1/display/update` or cron refresh on the host, `load_antigravity()` reads the merged cache and the satellite's tokens appear in the dashboard.

## Acceptance Criteria

- The script exits 0.
- The POST result shows `received > 0` (unless the satellite has never used Antigravity).
- `total_cache` on the host is greater than or equal to the satellite's entry count.

## Failure Modes

- **Host unreachable**: The script errors with a connection error. Verify the host service is running (`curl <host_url>/health`), Tailscale is connected on both machines, and `AI_USAGE_DASHBOARD_INGEST_URL` is correct.
- **No Antigravity on satellite**: The cache may be empty; `received=0`. This is valid — there is simply nothing to push.
- **URL not configured**: The script prints an error and exits 1. Add `AI_USAGE_DASHBOARD_INGEST_URL` to `.env`.

## Dry Run

To verify the configuration without POSTing:

```bash
.venv/bin/python scripts/push-antigravity --dry-run
```

Prints the entry count and target URL without sending data.

## Privacy

The entries sent over Tailscale contain only: model ID, timestamp, token counts (input/output/cache_read/cache_write/thinking), and response_id. No project paths, file contents, or code snippets are transmitted.