# Plan: Quota Display on E1002 + Claude Code & Codex Quota Sources

Status: planning (not yet implemented). This doc records the e-ink layout design and the two additional quota sources confirmed available, so the next implementation pass has a single reference.

## 1. E-ink quota display layout (E1002, 800×480)

### 1.1 Current screen real estate

The dashboard currently uses three horizontal bands:

- Top band (y 0–68): title (`<tokens> | $<cost> | 7D`), the AI Active Time total line, and the seven-item legend on the right.
- Middle band (y 78–292): the stacked token bar chart, `ChartRect{56, 78, 720, 214}`.
- Bottom band (y 336–424): the AI Active Hours bar chart, `ChartRect{56, 336, 720, 88}`.
- Footer (y 456): `Updated: <ts>, Auto 08-22` on the left, `Battery: ..% (..V)` on the right.

There is no free vertical strip between the two charts, and the footer is a single text line at y=456 with ~660px of horizontal room to the left of the battery string.

### 1.2 Why a dedicated quota column is the wrong frame

The first instinct was "reserve a column for quota." That fights the existing layout: the two charts already span x=56..776, and carving a vertical column out of the middle band would shrink the stacked chart below the point where 30-day bars stay readable (bar width is already floored at 18px). A side column on the right (x>776) leaves only 24px, too narrow for three quota lines with reset timestamps.

The quota is a snapshot, not a time series, so it does not need chart real estate. It needs three short text lines. The right place is the band that is currently underused: the footer area between the `Updated:` line and the battery string, plus a small block above the hours chart.

### 1.3 Proposed layout: quota block in the lower-left footer zone

Reserve a three-line block at the bottom-left, reflowing the existing footer:

- Keep `Updated: ...` but move it to y=440, left margin (x=10), textSize 1.
- Add the quota block immediately below it, y=456, x=10, three lines at textSize 1 (each line ~10px tall, total ~30px, fits before the bottom edge at 480):
  - `GLM 5h 13% r 06/28 15:00`
  - `GLM wk 43% r 06/30 11:10`
  - `GLM mo 0% 4000/8000 r 07/16`
- Move `Battery: ..% (..V)` to the bottom-right corner, y=456, x=610 (unchanged).

Line format is compact on purpose: `<provider> <window> <pct>% [r <reset>]`. `r` = resets, shown as `MM/DD HH:MM` (local, minute precision) to save width. The monthly tool line drops the reset date when space is tight and keeps `used/total` since that is the more actionable number for the tool quota.

### 1.4 Extension for Claude Code and Codex

Each provider gets up to two windows (Claude: 5h + 7d; Codex: 5h primary + 7d secondary). That would push the block to five lines if we show all three providers. Two options:

- Option A (recommended for V1): show only the most urgent window per provider. "Most urgent" = highest utilization percentage. That keeps the block at three lines (one per provider) and surfaces the window that matters most for deciding whether to switch tools.
- Option B: show all windows and let the block grow to 5–7 lines, accepting that the hours chart shrinks. Rejected for V1 because the hours chart is already short (88px) and five lines of text below it would collide with the footer.

Option A means the firmware picks, per provider, the window with the largest `used_percent` and renders one line: `GLM 43% wk r 06/30`, `Claude 53% 7d r 07/01`, `Codex 12% 5h r 06/18`. The window label stays inline so the user knows which limit is binding.

### 1.5 Firmware changes required

1. `dashboard_types.h`: add a `QuotaWindow` struct (`provider`, `label`, `usedPercent`, `resetsAtEpoch`, optional `usedCount`/`totalCount`) and a `DashboardData::quota` array (max ~6 entries) + `quotaCount`.
2. `dashboard_network.h` `parseDashboardPayload`: extend the parser to read the new `claude_quota` and `codex_quota` arrays from the JSON (see §3) and populate `data.quota`.
3. `dashboard_render.h` `renderDashboard`: after drawing the two charts, draw the quota block in the lower-left footer zone per §1.3, selecting the most-urgent window per provider per §1.4.
4. `dashboard_logic.h`: add a helper `mostUrgentQuotaWindow(provider)` that returns the window with the highest `usedPercent`.

No change to the chart geometry (`stackedRect`, `hoursRect`) — the quota block lives in the footer zone that is currently either empty or single-line.

### 1.6 E-ink refresh cost

Adding three text lines does not change the partial-refresh strategy: the quota block is static text, redrawn on every full update like the title and footer. No new partial-refresh region is needed for V1.

## 2. Claude Code quota source (confirmed)

- Endpoint: `GET https://api.anthropic.com/api/oauth/usage`
- Auth: OAuth Bearer from the macOS Keychain generic-password item `Claude Code-credentials` (account = username), with header `anthropic-beta: oauth-2025-04-20`. Token expires ~hourly; refresh via the OAuth refresh flow (ccusage already does this and rewrites the credentials file in place).
- Response shape (per rate-limit window):
  ```json
  {
    "five_hour":      {"utilization": 0.07, "resets_at": "2026-06-28T19:59:59Z"},
    "seven_day":      {"utilization": 0.53, "resets_at": "2026-07-01T03:00:00Z"},
    "seven_day_sonnet": {"utilization": 0.39, "resets_at": "..."},
    "seven_day_opus":   {"utilization": 0.20, "resets_at": "..."},
    "extra_usage":      {"spent_usd": 0.0, "limit_usd": 1000.0}
  }
  ```
  `utilization` is a float 0–1 (multiply by 100 for percent). `resets_at` is ISO-8601. Windows: `five_hour`, `seven_day` (plus model-specific sub-windows `seven_day_sonnet`/`seven_day_opus`, and an `extra_usage` overage bucket).
- Caveats: undocumented endpoint; known `429 retry-after: 0` bug for some Max users (retry/backoff needed). Model-specific sub-windows are newer; older builds only had `five_hour` + `seven_day`.

Implementation plan: add `export_claude_quota()` to `auto_usage.py` that reads the keychain entry via `security find-generic-password -s "Claude Code-credentials" -w`, parses the JSON for `accessToken`, calls `/api/oauth/usage`, and normalizes into the same `GlmQuotaSnapshot`-style shape (provider=`claude`, label=`5 Hours`/`7 Days`). The keychain read is a privileged operation but local-only and does not exfiltrate the token.

## 3. Codex quota source (confirmed)

- Mechanism: parse `rate_limits` from `token_count` event_msgs in `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` (and `~/.codex/archived_sessions/`). No HTTP call needed — the data is already in the session files we parse for token totals.
- Shape (verified on this machine):
  ```json
  "rate_limits": {
    "limit_id": "codex",
    "primary":   {"used_percent": 12.0, "window_minutes": 300,   "resets_at": 1781811012},
    "secondary": {"used_percent": 4.0,  "window_minutes": 10080, "resets_at": 1782338757},
    "credits": null,
    "plan_type": "pro"
  }
  ```
  `used_percent` is 0–100 (already a percent, unlike Claude's 0–1). `resets_at` is unix seconds. `window_minutes` 300 = 5h (primary), 10080 = 7d (secondary).
- Caveats: no official HTTP quota endpoint (the `backend-api/codex` SSE stream carries it inline, but hitting it directly is ToS-risky and fragile). The local-file path is stable and supported-equivalent.

Implementation plan: extend the existing `load_codex` / `build_codex_turn_intervals` path (or add a sibling `load_codex_quota()`) to scan the newest `token_count` event across session files and extract `rate_limits.primary` and `rate_limits.secondary` into `GlmQuotaSnapshot`-style entries (provider=`codex`, label=`5 Hours`/`Weekly`). `resets_at` (unix seconds) converts to epoch-ms + ISO for consistency with the GLM path.

## 4. Unified quota data model

To keep the dashboard and e-ink uniform, normalize all three providers into one quota array. Proposed extension of the existing `glm_quota` JSON field into a generic `quotas` array (with `glm_quota` kept as a deprecated alias for backward compat):

```json
"quotas": [
  {"provider": "glm",     "label": "5 Hours",  "percentage": 13, "next_reset_iso": "...", "next_reset_time_ms": ...},
  {"provider": "glm",     "label": "Weekly",   "percentage": 43, "next_reset_iso": "...", "next_reset_time_ms": ...},
  {"provider": "claude",  "label": "5 Hours",   "percentage": 7,  "next_reset_iso": "...", "next_reset_time_ms": ...},
  {"provider": "claude",  "label": "7 Days",    "percentage": 53, "next_reset_iso": "...", "next_reset_time_ms": ...},
  {"provider": "codex",   "label": "5 Hours",   "percentage": 12, "next_reset_iso": "...", "next_reset_time_ms": ...},
  {"provider": "codex",   "label": "Weekly",    "percentage": 4,  "next_reset_iso": "...", "next_reset_time_ms": ...}
]
```

The e-ink firmware reads `quotas`, groups by `provider`, and renders one line per provider (most-urgent window) per §1.4.

## 5. Implementation phasing

- Phase 4a: add `export_claude_quota()` + `load_codex_quota()`, normalize into `quotas` array, print in stdout, embed in JSON. Unit tests + live test for Claude (keychain-gated); Codex test reads a synthetic JSONL. (No e-ink change yet.)
- Phase 4b: e-ink firmware — extend `dashboard_types.h` / `dashboard_network.h` / `dashboard_render.h` per §1.5, render the most-urgent-per-provider quota block in the footer zone. Arduino compile check (when `arduino-cli` available). Python unit tests mirror the selection logic.

Phase 4a is the prerequisite; 4b is independent and can land after.