"""Parse Codex CLI rollout JSONL for token usage accounting.

Codex writes one rollout log per session under ``~/.codex/sessions`` in a
``YYYY/MM/DD`` tree (and older ones as flat files under
``~/.codex/archived_sessions``). Each log is a JSONL stream of events. Two
event types matter here:

- ``turn_context`` (payload.model): the model serving the current turn. Usage
  events that follow belong to this model until the next ``turn_context``.
- ``event_msg`` with ``payload.type == "token_count"``: a usage sample. Two
  sub-objects matter:
  - ``payload.info.last_token_usage``: the per-turn sample. ``input_tokens`` is
    the FULL input context for that turn (cache-INCLUSIVE), ``cached_input_tokens``
    the cached subset, ``output_tokens``/``reasoning_output_tokens`` the per-turn
    output deltas, ``cache_write_input_tokens`` the cache-write count.
  - ``payload.info.total_token_usage``: session-cumulative totals (used only to
    validate; we sum the per-turn samples).

Every event carries an ISO ``timestamp``, so this source is timestamp-precise
(seconds/ms), unlike the day-bucketed ``ccusage codex daily`` export it
replaces. Summing the per-turn samples per day reproduces ccusage's daily
totals exactly, including the input/cache/output split:

    input_non_cached = sum(input_tokens) - sum(cached_input_tokens)

The token split is mapped to the canonical three-part form used across the
dashboard: non-cached input, cached input, output (reasoning folded into
output), cache-write.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, TypedDict

from pricing_config import calc_cost_from_parts, get_pricing

DEFAULT_CODEX_SESSIONS_DIR = Path.home() / '.codex' / 'sessions'
DEFAULT_CODEX_ARCHIVE_DIR = Path.home() / '.codex' / 'archived_sessions'

DailyTokens = dict[date, int]


class CodexUsageRecord(TypedDict):
    """One per-turn usage delta attributed to a timestamp and model."""

    time: datetime
    model: str
    input_non_cached: int
    input_cached: int
    output: int
    cache_write: int


def _event_time(raw: dict) -> datetime | None:
    ts = raw.get('timestamp')
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace('Z', '+00:00')).astimezone().replace(tzinfo=None)
    except ValueError:
        return None


def _start_date_from_filename(name: str) -> date | None:
    """Rollout filenames are ``rollout-YYYY-MM-DDThh-mm-ss-<id>.jsonl``."""
    if not name.startswith('rollout-'):
        return None
    try:
        return datetime.strptime(name[8:18], '%Y-%m-%d').date()
    except ValueError:
        return None


def iter_session_files(
    sessions_dir: Path | None = None,
    archive_dir: Path | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> Iterator[Path]:
    """Yield rollout files whose filename date falls in [start_date, end_date].

    The filename carries the session start date, which is a cheap and reliable
    pre-filter: a session's events rarely fall outside the day it started, and
    per-event dates are re-checked against the window afterward.
    """
    sessions_dir = sessions_dir or DEFAULT_CODEX_SESSIONS_DIR
    archive_dir = archive_dir or DEFAULT_CODEX_ARCHIVE_DIR
    seen: set[Path] = set()
    for root in (sessions_dir, archive_dir):
        if not root.is_dir():
            continue
        for path in sorted(root.glob('**/rollout-*.jsonl')):
            if path in seen:
                continue
            seen.add(path)
            file_date = _start_date_from_filename(path.name)
            if file_date is None:
                continue
            if start_date and file_date < start_date:
                continue
            if end_date and file_date > end_date:
                continue
            yield path


def parse_session_file(path: Path) -> tuple[str, list[CodexUsageRecord]]:
    """Parse one rollout log into per-turn usage records.

    Model is taken from the most recent ``turn_context`` preceding each usage
    event. ``last_token_usage`` is the per-turn delta, so records are summed
    directly (no cumulative/last-wins handling needed).
    """
    model = 'unknown'
    session_id = ''
    records: list[CodexUsageRecord] = []
    try:
        with path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(raw, dict):
                    continue
                payload = raw.get('payload')
                payload = payload if isinstance(payload, dict) else {}

                if raw.get('type') == 'session_meta':
                    sid = payload.get('session_id') or payload.get('id')
                    if isinstance(sid, str):
                        session_id = sid
                    continue
                if raw.get('type') == 'turn_context':
                    m = payload.get('model')
                    if isinstance(m, str) and m:
                        model = m
                    continue
                if payload.get('type') != 'token_count':
                    continue
                info = payload.get('info')
                usage = info.get('last_token_usage') if isinstance(info, dict) else None
                if not isinstance(usage, dict):
                    continue
                when = _event_time(raw)
                if when is None:
                    continue
                # input_tokens is cache-inclusive; subtract the cached subset to
                # get the canonical non-cached input.
                total_input = int(usage.get('input_tokens', 0) or 0)
                cached_input = int(usage.get('cached_input_tokens', 0) or 0)
                records.append({
                    'time': when,
                    'model': model,
                    'input_non_cached': max(0, total_input - cached_input),
                    'input_cached': cached_input,
                    'output': int(usage.get('output_tokens', 0) or 0) + int(usage.get('reasoning_output_tokens', 0) or 0),
                    'cache_write': int(usage.get('cache_write_input_tokens', 0) or 0),
                })
    except OSError:
        return session_id, []
    return session_id, records


def iter_usage_records(
    sessions_dir: Path | None = None,
    archive_dir: Path | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> Iterator[CodexUsageRecord]:
    for path in iter_session_files(sessions_dir, archive_dir, start_date, end_date):
        _sid, records = parse_session_file(path)
        for record in records:
            record_date = record['time'].date()
            if start_date and record_date < start_date:
                continue
            if end_date and record_date > end_date:
                continue
            yield record


def _record_total(record: CodexUsageRecord) -> int:
    return record['input_non_cached'] + record['input_cached'] + record['output'] + record['cache_write']


def load_daily(
    sessions_dir: Path | None = None,
    archive_dir: Path | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> DailyTokens:
    """Daily total tokens across all Codex rollout sessions."""
    daily: defaultdict[date, int] = defaultdict(int)
    for record in iter_usage_records(sessions_dir, archive_dir, start_date, end_date):
        total = _record_total(record)
        if total > 0:
            daily[record['time'].date()] += total
    return dict(daily)


def load_detailed(
    sessions_dir: Path | None = None,
    archive_dir: Path | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[date, dict[str, dict[str, int]]]:
    """Per-model per-day canonical split (same shape as the DSH loader)."""
    daily_models: defaultdict[date, defaultdict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0})
    )
    for record in iter_usage_records(sessions_dir, archive_dir, start_date, end_date):
        entry = daily_models[record['time'].date()][record['model']]
        entry['input'] += record['input_non_cached']
        entry['cache_read'] += record['input_cached']
        entry['output'] += record['output']
        entry['cache_write'] += record['cache_write']
    return {d: dict(m) for d, m in daily_models.items()}


def calc_cost(detailed: dict[date, dict[str, dict[str, int]]]) -> dict[date, float]:
    """API-equivalent cost from the canonical split."""
    result: defaultdict[date, float] = defaultdict(float)
    for day, models in detailed.items():
        for model_id, tok in models.items():
            pricing = get_pricing(model_id)
            if pricing is None:
                continue
            result[day] += calc_cost_from_parts(
                pricing,
                input_non_cached=tok['input'],
                input_cached=tok['cache_read'],
                output=tok['output'],
                cache_write=tok['cache_write'],
            )
    return dict(result)
