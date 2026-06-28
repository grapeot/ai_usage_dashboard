# RFC: AI Active Time Estimation In `auto_usage.py`

## 1. Overview

This document defines how `auto_usage.py` estimates cumulative AI active time and displays it alongside existing token and cost output.

Implementation goals:

- Preserve existing token and cost semantics.
- Add OpenCode + Codex turn-window cumulative active time.
- Display the result in stdout and the desktop PNG.

## 2. Data Model

```python
TimeInterval = tuple[datetime, datetime]
DailyActiveSeconds = dict[date, float]
```

Constraints:

- Timestamps are normalized into local naive `datetime` objects.
- Intervals use inclusive start and exclusive end semantics in practice.
- Zero-length intervals can exist but contribute zero seconds.

Notes:

- V1 intervals do not carry source metadata.
- Source-level logic is used while constructing turns, then normalized into plain time intervals before daily aggregation.

## 3. OpenCode Algorithm

### 3.1 Read Scope

Read the `message` table fields:

- `session_id`
- `time_created`
- `data`

Keep only:

- `role == user`
- `role == assistant`

For assistant messages, skip GLM providers.

### 3.2 Turn Construction

For each `session_id`:

1. Sort by `time_created` ascending.
2. Maintain `pending_user_start` and `last_assistant_time`.
3. On `user`:
   - If the pending turn has assistant output, close the previous turn.
   - Start a new turn.
4. On `assistant`:
   - If a pending turn exists, set `last_assistant_time` to the assistant timestamp.
5. At session end:
   - If the pending turn has assistant output, emit `[pending_user_start, last_assistant_time]`.

### 3.3 Boundaries

- Multiple assistant messages belong to the same turn; the last assistant timestamp ends the turn.
- Consecutive user messages drop the previous pending turn if it has no assistant output; otherwise they close it first.
- Assistant messages without a user message are ignored.

## 4. Codex Algorithm

### 4.1 Event Selection

Read all events from each `.jsonl` file.

Relevant `event_msg.payload.type` values:

- `user_message`
- `task_complete`

Use the top-level timestamp of the final event as a fallback when needed.

### 4.2 Turn Construction

1. Maintain `pending_user_start`.
2. On `user_message`:
   - If a previous turn is still open, close it at the current event timestamp.
   - Start a new turn.
3. On `task_complete`:
   - If a pending turn exists, emit `[pending_user_start, task_complete_time]` and close it.
4. At file end:
   - If a pending turn remains, close it at the final event timestamp.

### 4.3 Why Not `response_item`

Codex event streams mix visible messages, reasoning, tool calls, and task lifecycle events. V1 uses `user_message -> task_complete` because that more closely represents an agent execution lifecycle than a visible text response.

## 5. Daily Aggregation

### 5.1 Split By Day

Any cross-day interval is split into pieces:

```python
[(date1, (start, midnight)), (date2, (midnight, end))]
```

### 5.2 Sum

For each day:

1. Split all intervals by day.
2. Add each piece duration directly.
3. Do not collapse overlapping windows.

### 5.3 Output

The result is:

```python
{date: seconds}
```

The metric represents cumulative AI labor. Overlapping parallel agents are intentionally counted more than once.

## 6. Code Changes

### 6.1 New Functions In `auto_usage.py`

- `split_interval_by_day()`
- `build_opencode_turn_intervals()`
- `load_opencode_turn_intervals()`
- `build_codex_turn_intervals()`
- `load_codex_turn_intervals()`
- `compute_daily_ai_active_seconds()`

### 6.2 `generate_dashboard()` Extension

New parameter:

```python
daily_active_seconds: dict[date, float] | None
```

New behavior:

- stdout includes `AI Hours`
- desktop PNG uses a `2 x 1` subplot layout

## 7. Output Design

### 7.1 Table Column

Insert after `Total` and before `Est. $`:

```text
AI Hours
```

It is a core output, less primary than total tokens but more actionable than cost for workflow analysis.

### 7.2 Image Layout

Top chart:

- existing token stacked bar

Bottom chart:

- one bar per day for AI Hours
- y-axis: `Hours`
- title: `AI Active Time (cumulative est.)`

## 8. Test Strategy

Add unit tests for:

- day splitting
- OpenCode turn construction
- Codex turn construction
- cross-source cumulative aggregation

Use small synthetic samples instead of real local databases.

## 9. Compatibility

- Do not remove existing token columns.
- Do not change cost estimation logic.
- Cursor and GLM stay in token statistics but do not contribute to AI active time.

## 10. Documentation Changes

- Add `docs/prd.md`.
- Add `docs/rfc.md`.
- Update `README.md`.
- Update `docs/WORKING.md`.
- Remove the old pricing-estimate design note.

## 11. Open Questions

- Whether OpenCode should later add idle-gap splitting.
- Whether Codex should later use a more precise visible-output end signal.

V1 prioritizes stability, explainability, and regression coverage.

## 12. GLM / Z.ai Coding Plan Quota

### 12.1 Overview

This section defines how `auto_usage.py` fetches, normalizes, and displays the
Z.ai coding-plan quota snapshot. The snapshot complements the existing token
usage time series: usage answers "how many tokens were used", the quota answers
"how much of each rolling window is left and when does it reset".

### 12.2 Data Model

```python
GlmQuotaSnapshot = TypedDict(
    'GlmQuotaSnapshot',
    {
        'label': str,
        'type': str,
        'unit': int,
        'percentage': int,
        'next_reset_time_ms': int | None,
        'next_reset_iso': str | None,
        'usage': int | None,
        'current_value': int | None,
        'remaining': int | None,
        'usage_details': list[GlmQuotaUsageDetail] | None,
    },
    total=False,
)
```

`label` is derived from a static `(type, unit) -> label` map; unknown pairs
fall back to `"<type> unit=<unit>"` so new windows stay visible. Absolute
count fields (`usage`, `current_value`, `remaining`, `usage_details`) are
`None` for token-limit windows that only report a percentage.

### 12.3 Algorithm

1. If `GLM_BEARER_TOKEN` is set, `GET /api/monitor/usage/quota/limit` with the
   bearer token (no query params, no org/project headers required).
2. Cache the raw response verbatim to `glm_quota.json`.
3. `normalize_glm_quota()` walks `data.limits`, maps each entry to a
   `GlmQuotaSnapshot`, and converts `nextResetTime` (epoch ms) to a local ISO
   string.
4. `format_glm_quota_block()` renders the snapshots as a stdout block after the
   token/cost table.
5. The snapshots are passed through `build_eink_dashboard_payload()` and
   embedded under `payload['glm_quota']` when non-empty.

### 12.4 Endpoint Contract

```text
GET https://api.z.ai/api/monitor/usage/quota/limit
Authorization: Bearer <token>
Accept: application/json
```

Sample response (truncated):

```json
{
  "code": 200,
  "data": {
    "limits": [
      {"type": "TIME_LIMIT", "unit": 5, "number": 1, "usage": 4000,
       "currentValue": 0, "remaining": 4000, "percentage": 0,
       "nextResetTime": 1784225431994,
       "usageDetails": [{"modelCode": "search-prime", "usage": 0}]},
      {"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "percentage": 13,
       "nextResetTime": 1782684009923},
      {"type": "TOKENS_LIMIT", "unit": 6, "number": 1, "percentage": 43,
       "nextResetTime": 1782843031997}
    ],
    "level": "max"
  },
  "success": true
}
```

### 12.5 Output Design

stdout block:

```text
GLM / Z.ai Coding Plan Quota:
  5 Hours Quota: 13% used  resets 2026-06-28T22:00:09
  Weekly Quota: 43% used  resets 2026-06-30T18:10:31
  Monthly Web Search / Reader / Zread Quota: 0% used  used 0/4000  resets 2026-07-16T18:10:31
```

JSON payload (added to the existing `meta`/`summary`/`daily` shape):

```json
"glm_quota": [
  {
    "label": "5 Hours Quota",
    "type": "TOKENS_LIMIT",
    "unit": 3,
    "percentage": 13,
    "next_reset_time_ms": 1782684009923,
    "next_reset_iso": "2026-06-28T22:00:09",
    "usage": null,
    "current_value": null,
    "remaining": null,
    "usage_details": null
  }
]
```

### 12.6 Test Strategy

Add unit tests for:

- raw response -> snapshots normalization (all three known window types)
- unknown `(type, unit)` fallback labeling
- `nextResetTime` epoch-ms -> ISO conversion (and `None` handling)
- `load_glm_quota()` returning `[]` when the file is absent or malformed
- `format_glm_quota_block()` rendering, including the monthly absolute-count line

Add a `live_api`-marked integration test that hits the real endpoint when
`GLM_BEARER_TOKEN` is set, and is skipped otherwise.

### 12.7 Compatibility

- Do not remove existing token columns or cost estimation.
- `glm_quota` is optional in the JSON payload; old consumers ignore it.
- A missing or expired token produces an empty quota block and no crash.

## 13. Codex Quota (Local JSONL)

### 13.1 Overview

The Codex CLI does not expose an HTTP quota endpoint. It embeds a `rate_limits`
snapshot in every `token_count` event of its session JSONL
(`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` and `~/.codex/archived_sessions/`).
`load_codex_quota()` scans session files newest-first and returns the latest
`rate_limits` block seen, normalized into the unified `QuotaSnapshot` shape.

### 13.2 Data Model

```python
QuotaSnapshot = TypedDict(
    'QuotaSnapshot',
    {
        'provider': str,        # 'glm' | 'codex' | 'claude'
        'label': str,           # '5 Hours', 'Weekly', '5 Hours Quota', ...
        'percentage': int,      # 0-100
        'next_reset_time_ms': int | None,
        'next_reset_iso': str | None,
        'usage': int | None,    # GLM monthly tool quota only
        'remaining': int | None,# GLM monthly tool quota only
    },
    total=False,
)
```

### 13.3 Codex rate_limits Shape

```json
"rate_limits": {
  "limit_id": "codex",
  "primary":   {"used_percent": 12.0, "window_minutes": 300,   "resets_at": 1781811012},
  "secondary": {"used_percent": 4.0,  "window_minutes": 10080, "resets_at": 1782338757},
  "credits": null,
  "plan_type": "pro"
}
```

`used_percent` is 0-100 (unlike Claude's 0-1 utilization). `resets_at` is unix
seconds; converted to epoch-ms and ISO for consistency with GLM. `window_minutes`
300 = 5 Hours (primary), 10080 = Weekly (secondary); unknown values fall back to
`"<minutes>m"`.

### 13.4 Unified `quotas` Array

The JSON payload gains a `quotas` array that merges all providers:

```json
"quotas": [
  {"provider": "glm", "label": "5 Hours Quota", "percentage": 13, ...},
  {"provider": "codex", "label": "5 Hours", "percentage": 12, ...}
]
```

`glm_quota` is kept as a deprecated backward-compat alias. The e-ink firmware
and stdout render the unified `quotas` array. Claude Code is not yet wired in;
its endpoint (`/api/oauth/usage`) is documented in `docs/plan_quota_display.md`
for a future phase.

## 14. Ollama Quota (HTML Parse)

### 14.1 Overview

Ollama does not expose a JSON API for usage quota. The settings page at
`https://ollama.com/settings` server-renders two usage bars (Session = 5h,
Weekly) into the HTML, requiring a browser cookie (cf_clearance + session).
`export_ollama_quota()` fetches the HTML; `normalize_ollama_quota()` parses the
percentage from the `X% used` spans and the reset time from the
`data-time="ISO"` attribute on the local-time div. The two windows are paired
in document order: Session -> '5 Hours', Weekly -> 'Weekly'.

### 14.2 Data Model

Same `QuotaSnapshot` shape, `provider='ollama'`. `next_reset_iso` is the raw
UTC ISO string from `data-time`; `next_reset_time_ms` is the epoch-ms form.

### 14.3 Provider Order

`quotas` is assembled in display order: z.ai GLM -> Ollama -> Codex. This
matches the e-ink firmware's rendering top-to-bottom in the quota panel.

## 15. Pydantic Response Models For The Local API

### 13.1 Overview

The local FastAPI service previously returned `dict[str, Any]`, so `/openapi.json`
described every response as an opaque `object` with `additionalProperties: true`.
An AI agent reading the OpenAPI spec could not discover the dashboard shape or
the meaning of any field. This section defines the Pydantic response models that
now back the API, making `/openapi.json` self-describing.

### 13.2 Module

`dashboard_models.py` owns the response models. It is the single source of truth
for the OpenAPI schema. `auto_usage.py` is unchanged and still returns plain
dicts so the CLI entry point stays dependency-light; the service layer validates
and serializes through the models.

Models:

- `DashboardMeta`
- `CategoryTotals`
- `DashboardSummary`
- `DailyEntry`
- `GlmQuotaUsageDetail`
- `GlmQuotaSnapshot`
- `DashboardPayload`
- `HealthResponse`
- `UpdateRequest`

### 13.3 Field Descriptions

Every field carries a `Field(description=...)` so the generated OpenAPI schema
documents what each value means. A test (`test_model_fields_carry_descriptions_for_openapi`)
asserts no model field is missing a description, guarding against silent schema
erosion.

### 13.4 Backward Compatibility

All payload fields are optional with defaults. A stale or minimal cached
`token_usage_eink.json` (for example one written by an older version that
predates `glm_quota`) still validates. The service does not use
`response_model_exclude_defaults`, so default-valued fields are serialized:
clients always see the full documented shape, and a minimal cached payload is
enriched to the full shape rather than rejected.

### 13.5 Code Changes

- `local_display_service.py` imports the models and declares them as
  `response_model` on `/health`, `/token_usage.json`, and
  `/api/v1/display/update`. It also sets app-level `title`, `description`, and
  `version`, and per-route `summary`/`description`.
- `pyproject.toml` registers `dashboard_models` as a py-module.
- `tests/test_local_display_service.py` was updated to assert structural fields
  rather than raw dict equality, since typed response models enrich minimal
  payloads with defaults.
- `tests/test_dashboard_models.py` covers full-shape validation, minimal-shape
  defaults, round-trip serialization, required-field enforcement, and the
  description-presence guard.

### 13.6 OpenAPI Impact

Before: `GET /token_usage.json` 200 schema was
`{"additionalProperties": true, "type": "object"}`.

After: it is `{"$ref": "#/components/schemas/DashboardPayload"}` and
`components/schemas` lists every model with per-field descriptions. An AI agent
can read the spec and understand `glm_quota[].next_reset_iso`,
`summary.total_ai_hours`, and every other field without reading source.
