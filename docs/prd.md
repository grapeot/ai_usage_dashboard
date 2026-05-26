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

- Idle gap splitting.
- Daily window counts and longest window.
- Source-level active time breakdown.
