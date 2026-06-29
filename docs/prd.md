# PRD: AI Active Time Estimation

## 1. Background

`auto_usage.py` already aggregates multi-platform token usage into two outputs:

- a terminal table
- a desktop dashboard image

It answers how many tokens were used and the API-equivalent cost, but it does not answer how long AI agents were actively working.

The goal is not to estimate true compute time or human attention time. The goal is to estimate active task windows after an AI agent is triggered.

## 2. Goals

Add AI active time estimation to `auto_usage.py` using local logs:

- daily AI active time in hours
- total AI active time in hours
- a new dashboard subplot showing the daily trend

## 3. Non-Goals

- Do not count Cursor.
- Do not count GLM or Cloud Code for active time.
- Do not estimate true compute time.
- Do not analyze human working time.
- Do not replay conversations or run semantic search over sessions.

## 4. V1 Scope

V1 counts two sources:

- OpenCode, excluding GLM providers
- Codex

The metric uses turn windows rather than session windows.

## 5. Core Definition

### 5.1 Metric Name

`AI Active Time (cumulative est.)`

### 5.2 Turn Window

A turn window is one estimated unit of agent work:

- Start: the user sends a task or message.
- End: the agent completes the corresponding output or task.

This is an estimated work window, not exact compute time.

### 5.3 Why Not Session Windows

Session windows include idle time and tend to overcount. Turn windows track request-level activity and keep the error boundary easier to reason about.

## 6. Data Sources

### 6.1 OpenCode

Data source: `~/.local/share/opencode/opencode.db`

Required fields:

- `session_id`
- `time_created`
- `data.role`
- `data.providerID`
- `data.modelID`

V1 rules:

- Keep only `role in ('user', 'assistant')`.
- Exclude assistant messages from GLM providers.
- Sort each session by time.
- Each user message starts a pending turn.
- Assistant messages extend the current turn.
- If the next user message arrives and the previous turn has assistant output, close the previous turn.
- At session end, close a pending turn only if it has assistant output.

### 6.2 Codex

Data source: `~/.codex/sessions/YYYY/MM/DD/*.jsonl`

Required events:

- `event_msg.payload.type == user_message`
- `event_msg.payload.type == task_complete`

V1 rules:

- `user_message` starts a turn.
- The matching `task_complete` ends the turn.
- If a session ends without `task_complete`, fall back to the last event timestamp.

## 7. Aggregation

### 7.1 Interval Model

Each turn produces one interval:

```python
(start_datetime, end_datetime)
```

### 7.2 Day Splitting

Intervals that cross midnight are split into per-day pieces.

### 7.3 Cumulative Sum

For each day, sum all OpenCode and Codex turn durations directly.

This means parallel agents are counted cumulatively. The metric represents total AI labor, not wall-clock coverage.

Outputs:

- daily active seconds
- daily active hours

## 8. Output Requirements

Add `AI Hours` to the existing daily token table and the total row.

Update the desktop PNG to use two vertically stacked subplots:

- top: existing token stacked bar chart
- bottom: daily AI active time in hours

The bottom chart uses one bar per day in V1.

## 9. Success Criteria

- `python auto_usage.py -d 7` reliably prints AI Hours.
- `token_usage_dashboard.png` includes the second subplot.
- OpenCode and Codex turn windows cover recent real local data.
- Parallel agent durations on the same day are cumulatively counted.

## 10. Risks And Limits

- This is an active window estimate, not strict work time.
- Long idle gaps inside a turn can still overcount.
- Parallel agents are counted cumulatively, so the result is closer to total labor than wall-clock coverage.
- Codex turns without `task_complete` require a fallback.
- OpenCode sessions with user messages but no assistant messages are skipped.

## 11. Version Strategy

### V1

- Turn-window estimation for OpenCode and Codex.
- Daily table column.
- Dashboard subplot.
- Unit tests for interval logic.

### Optional V2

- Idle-gap splitting.
- Daily window counts and longest window.
- Source-level active time breakdown.

## Appendix C: Google Antigravity IDE Token Usage

### Background

Google Antigravity IDE runs a Go-based Language Server (LS) that mediates all
model calls. The LS holds per-generation token usage in memory and exposes it
through a local gRPC service. Local transcript files
(`~/.gemini/antigravity-ide/brain/<id>/.system_generated/logs/transcript_full.jsonl`)
and conversation SQLite stores (`~/.gemini/antigravity-ide/conversations/<id>.db`)
do **not** contain token usage fields — only the LS gRPC does.

### Goals

Add Antigravity IDE token usage to `auto_usage.py`:

- discover the running LS process and its gRPC endpoint automatically
- fetch per-trajectory token usage via `GetCascadeTrajectoryGeneratorMetadata`
- aggregate by date and merge into the existing Gemini bucket (Antigravity's
  primary model is Gemini; non-Gemini models route to their respective buckets)
- no user configuration required (no `.env` keys, no cookies, no tokens)

### Non-Goals

- Do not parse local transcript or conversation DB files for token counts (they
  do not contain usage data).
- Do not estimate tokens via tokenizer heuristics.
- Do not fetch quota snapshots from the LS in V1 (the LS exposes model credits
  but the protobuf is opaque; quota display is a future phase).
- Do not persist a sync cache; data is fetched live each run. When the LS is not
  running, Antigravity contributes zero tokens for that run.

### Discovery: No Configuration Needed

The LS process is discovered at runtime:

1. `ps -ww -eo pid,args` finds processes matching `language_server` with
   `--app_data_dir antigravity` or `antigravity-ide`.
2. `--csrf_token` is extracted from argv (hex string, ≥32 chars).
3. `lsof -Pan -p <pid> -iTCP -sTCP:LISTEN` finds TCP listening ports.
4. Each port is probed with a `Heartbeat` gRPC call (HTTP/JSON, Connect-Protocol
   header). The port returning HTTP 200 is the LS HTTP gRPC endpoint.

All discovery inputs (csrf token, port) come from the live process — nothing is
configured or stored.

### Data Flow

```
ps + lsof → LS port + csrf
  ↓
GetAllCascadeTrajectories → [{cascadeId, stepCount, lastModified}]
  ↓
GetCascadeTrajectoryGeneratorMetadata(cascadeId) → generatorMetadata[]
  ↓
generatorMetadata[].chatModel.retryInfos[].usage
  → {inputTokens, outputTokens, cacheReadTokens, thinkingOutputTokens}
  ↓
classify by model → gemini / claude / gpt / other bucket
  ↓
{date: total_tokens}
```

### Bucket Assignment

Antigravity models map to existing dashboard buckets:

| LS model ID | Dashboard bucket | Reason |
|---|---|---|
| `gemini-3-flash-a`, `MODEL_PLACEHOLDER_M20` | `gemini` | Gemini 3.5 Flash (Medium) |
| `claude-*` (if used via Antigravity) | `claude` | Claude routed through Antigravity |
| `gpt-*` (if used via Antigravity) | `gpt_opencode` | GPT routed through Antigravity |

The `MODEL_PLACEHOLDER_M*` enum is Antigravity's internal model code. Known
mappings: `M20` = Gemini 3.5 Flash (Medium). Unknown placeholders default to
`gemini` (Antigravity's default model family) but are logged to stderr.

### Success Criteria

- `python auto_usage.py -d 7` includes Antigravity token counts in the Gemini
  column when the LS is running.
- When the LS is not running, no crash and no error — Antigravity simply
  contributes zero.
- Unit tests cover: LS discovery logic (mocked ps/lsof), gRPC response parsing,
  model-to-bucket classification, and date aggregation.
- No new `.env` keys, cookies, or user configuration.

### Risks And Limits

- **Live-only data**: token usage is only available while the LS is running.
  Historical sessions whose LS has exited cannot be recovered. A future phase
  could cache sync results (like tokscale's `antigravity sync`).
- **Undocumented gRPC**: the `GetCascadeTrajectoryGeneratorMetadata` endpoint is
  not publicly documented. It may change between Antigravity versions. The
  parser is defensive: missing fields default to zero, unknown model
  placeholders are logged but do not crash.
- **No cacheWrite**: the LS response includes `cacheReadTokens` and
  `thinkingOutputTokens` but not `cacheWriteTokens`. `cacheWrite` is recorded as
  0 for Antigravity entries.
- **Multiple LS instances**: multiple Antigravity windows or the Agent Manager
  may spawn separate LS processes. All are probed; usage is deduplicated by
  `responseId` when present.

### Quota Display

The LS also exposes per-model quota via `GetCascadeModelConfigData`. Each model
entry has `quotaInfo.remainingFraction` (0-1, 1 = full) and `quotaInfo.resetTime`
(ISO UTC). Models are grouped by family (Gemini, Claude, GPT) and the highest
used percentage per family is reported as a `QuotaSnapshot` with
`provider='antigravity'` and `label='<Family> 5h'`.

The Antigravity quota entries are appended to the unified `quotas` array after
Claude Code. They appear in the stdout quota block, the e-ink JSON payload, and
the simulator render. The e-ink firmware renders them with the cyan color
(same as Ollama, since Antigravity is a multi-model platform like Ollama).

No `.env` keys are needed — quota is fetched from the same live LS process that
provides token usage.

## Appendix B: GLM / Z.ai Coding Plan Quota

### Background

`auto_usage.py` already exports GLM/Z.ai token usage via the
`/api/monitor/usage/model-usage` endpoint. The Z.ai web dashboard also exposes a
quota view (`/manage-apikey/coding-plan/personal/usage`) that shows how much of
each rolling quota window has been consumed and when it resets. That view is
powered by a separate JSON endpoint:

```text
GET https://api.z.ai/api/monitor/usage/quota/limit
Authorization: Bearer <GLM_BEARER_TOKEN>
```

No query parameters are required; the bearer token identifies the plan. The
response is a snapshot (not a time series) and reuses the same `GLM_BEARER_TOKEN`
already configured in `.env`.

### Goals

Add the coding-plan quota snapshot to `auto_usage.py`:

- fetch the snapshot when `GLM_BEARER_TOKEN` is set
- print a compact quota block after the token/cost table
- embed the snapshot in the e-ink / dashboard JSON payload under `glm_quota`
- cache the raw response to `glm_quota.json` for offline reuse

### Quota Windows

The API returns a `limits` array. Each entry has a `(type, unit)` pair that
maps to a human-readable window label:

| type           | unit | label                                        | fields                                                           |
|----------------|------|----------------------------------------------|------------------------------------------------------------------|
| `TOKENS_LIMIT` | 3    | 5 Hours Quota                                | `percentage`, `nextResetTime`                                   |
| `TOKENS_LIMIT` | 6    | Weekly Quota                                 | `percentage`, `nextResetTime`                                    |
| `TIME_LIMIT`   | 5    | Monthly Web Search / Reader / Zread Quota    | `usage`, `currentValue`, `remaining`, `percentage`, `nextResetTime`, `usageDetails` |

`nextResetTime` is an epoch-millisecond timestamp. It is converted to a local
ISO string for display and JSON.

### Non-Goals

- Do not predict future quota consumption.
- Do not poll the quota endpoint on a schedule; it is fetched once per run.
- Do not surface the `tool-usage` time series in V1 (it is empty for this plan).
- Do not parse the Ollama HTML quota page in this phase; GLM is API-driven.

### Success Criteria

- `python auto_usage.py -d 7` prints the quota block when the token is set.
- The JSON payload contains a `glm_quota` array with labeled snapshots.
- A missing or expired token degrades gracefully (empty quota block, no crash).
- Unknown `(type, unit)` pairs are surfaced with a fallback label instead of
  being silently dropped, so new windows added by Z.ai remain visible.

### Risks And Limits

- The quota endpoint is undocumented and may change shape; the normalizer is
  defensive and falls back to generic labels for unknown window codes.
- Reset timestamps are wall-clock values from the API; timezone display follows
  the local machine.
- The monthly tool quota reports absolute counts (`usage`/`remaining`) while the
  token quotas report only a `percentage`; the display reflects that asymmetry.
