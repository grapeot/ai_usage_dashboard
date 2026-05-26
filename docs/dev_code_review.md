# Developer Code Review

## 1. Overall Assessment

The active time work is connected to the main `auto_usage.py` flow rather than being implemented as a side script. The product path is coherent:

- data sources are limited to OpenCode and Codex
- the algorithm uses turn windows instead of broad session windows
- stdout and PNG output show the same metric

The feature is usable as implemented. From an engineering standpoint, there is no obvious design flaw that would immediately produce incorrect results.

## 2. What Works Well

### 2.1 Data Semantics Are Coherent

- OpenCode uses `user -> last assistant`, which matches the local SQLite shape.
- Codex uses `user_message -> task_complete`, which matches the JSONL event stream.
- Daily aggregation splits by day and sums turn durations directly, which matches the total-labor interpretation.

### 2.2 Product Output Is Integrated

- stdout includes `AI Hours`.
- the PNG uses two stacked charts with a shared date axis.

This is better than adding another command because users can keep running `python auto_usage.py -d 7`.

### 2.3 Unit Tests Cover The Algorithm Skeleton

Tests cover:

- cross-day splitting
- OpenCode turn-window construction
- Codex turn-window construction
- cumulative aggregation across sources

These tests cover the core logic rather than only surface formatting.

## 3. Design Concerns

### 3.1 `auto_usage.py` Is Still Growing

The file currently owns:

- data export
- source parsing
- cost estimation
- active time estimation
- stdout formatting
- PNG rendering

This is still maintainable in the short term, but active-time logic is already a cohesive domain. If later work adds idle-gap splitting, window counts, or longest-window reporting, this logic should move into a smaller module.

A likely future split:

- `active_time.py` for interval and turn-window logic
- `pricing.py` or the existing `pricing_config.py` for pricing
- `dashboard.py` for stdout and chart rendering

This is technical debt, not a blocker for the current release.

### 3.2 OpenCode Turn Definition Is Approximate

The current algorithm tracks each user message through the last following assistant message in the same session. This is much better than a session window, but it can still overcount if a turn has a long idle gap before another assistant event.

The metric name already calls this an estimate. If precision matters later, idle-gap splitting is the next improvement.

### 3.3 Codex Fallback Is Conservative

If a Codex `user_message` has no `task_complete`, the algorithm closes the turn at the final event timestamp. This avoids losing the turn, but interrupted sessions may be overcounted. That is acceptable for V1 and should be tracked if precision becomes important.

## 4. Refactor Candidates

### 4.1 Extract Interval Logic

These functions form a natural module:

- `split_interval_by_day()`
- `merge_intervals()`
- `build_opencode_turn_intervals()`
- `build_codex_turn_intervals()`
- `compute_daily_ai_active_seconds()`

They can move to `active_time.py` when the feature grows.

### 4.2 Reduce `generate_dashboard()` Arguments

`generate_dashboard()` now accepts many token dictionaries plus cost and active-time dictionaries. If more fields are added, move toward a structured daily view model.

### 4.3 Extract Table Column Definitions

The stdout table still hardcodes column order and formatting. That is acceptable for now; if more columns are added, define the table as structured column metadata.

## 5. Test Coverage Gaps

### 5.1 No Real Fixture Regression Tests

The current tests use synthetic samples. They are good for algorithm behavior but do not catch schema drift.

Useful future fixtures:

- OpenCode messages with user/assistant/GLM exclusion cases
- Codex events with `user_message`, `task_complete`, and incomplete turns

### 5.2 No Dashboard-Level Smoke Test

There is no direct test that the PNG chart is generated with the second subplot. A future smoke test could call `generate_dashboard()` and confirm a PNG is written without exceptions.

### 5.3 Missing Local-Source Absence Cases

The code handles missing local databases and directories. Tests now cover the most important fresh-clone absence cases, but more can be added around empty date ranges and source-level gaps.

## 6. Design Verdict

There is no fatal design flaw. The remaining issues are technical debt and precision boundaries:

- `auto_usage.py` is too large for long-term growth.
- idle gaps can still overcount.
- Codex incomplete-turn fallback is conservative.

The current version is suitable for release and can be iterated later.

## 7. Suggested Next Iteration

1. Extract active-time logic into a small module.
2. Add optional idle-gap splitting.
3. Add real fixture regression tests.
4. Add `Window Count` or `Longest Window` only if the metric becomes useful in practice.
