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
  - ``payload.info.total_token_usage``: session-cumulative totals. A re-emitted
    ``token_count`` event can repeat the previous sample without the cumulative
    advancing (a periodic re-report, not new work); those repeats are skipped by
    comparing the cumulative total to the previous sample's.

Every event carries an ISO ``timestamp``, so this source is timestamp-precise
(seconds/ms), unlike the day-bucketed ``ccusage codex daily`` export it
replaces. Grouping the per-turn samples by their event timestamp reproduces
ccusage's daily split:

    input_non_cached = sum(input_tokens) - sum(cached_input_tokens)

Grouping is by EVENT date, not filename date: a session can append events after
midnight, so a file's events may land on days later than its name implies
(observed up to ~8 days). The filename is only an upper-bound prefilter (a
session's events never precede its start), never a lower bound.

The token split is mapped to the canonical three-part form used across the
dashboard: non-cached input, cached input, output (reasoning folded into
output), cache-write. ccusage excludes reasoning from its ``totalTokens``; we
fold it into output to stay consistent with the other local sources and to bill
it (OpenAI bills reasoning as output), so headline totals differ from ccusage by
the reasoning count on days where it is non-zero, while the cost model stays
correct.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator, TypedDict

from pricing_config import calc_cost_from_parts, get_pricing

DEFAULT_CODEX_SESSIONS_DIR = Path.home() / '.codex' / 'sessions'
DEFAULT_CODEX_ARCHIVE_DIR = Path.home() / '.codex' / 'archived_sessions'

DailyTokens = dict[date, int]


class CodexUsageRecord(TypedDict, total=False):
    """One per-turn usage delta attributed to a timestamp and model."""

    time: datetime
    model: str
    input_non_cached: int
    input_cached: int
    output: int
    cache_write: int
    billing_provider: str
    record_id: str


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
    """Yield rollout files that can contain events in the window.

    The filename carries the session START date, and a session's events never
    precede its start. So a filename date AFTER ``end_date`` proves the file has
    no in-window events and can be skipped. A filename date BEFORE ``start_date``
    proves nothing — a session that started earlier can still append events
    inside the window (observed spill of up to ~8 days) — so those files are
    kept and their per-event dates are checked downstream.
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
            # Upper bound: the filename date is the session's start in UTC, but
            # events are grouped by LOCAL date, which can be one day earlier
            # (a session starting at 03:40 UTC is 19:40 the prior local day). So
            # a file dated end_date+1 can still hold events on end_date; only a
            # file dated end_date+2 is guaranteed empty for the window.
            if end_date and file_date > end_date + timedelta(days=1):
                continue
            # Lower bound by mtime: if the file was last written before the
            # window began, it has no in-window events. This keeps the scan
            # bounded for recent windows despite forward spill.
            if start_date:
                try:
                    if datetime.fromtimestamp(path.stat().st_mtime).date() < start_date:
                        continue
                except OSError:
                    continue
            yield path


def parse_session_file(path: Path) -> tuple[str, list[CodexUsageRecord]]:
    """Parse one rollout log into per-turn usage records.

    Model is taken from the most recent ``turn_context`` preceding each usage
    event. ``last_token_usage`` is the per-turn delta, so records are summed
    directly — except that Codex periodically re-emits a ``token_count`` event
    repeating the previous sample without the cumulative ``total_token_usage``
    advancing. Those repeats are not new work and are skipped by comparing the
    cumulative total to the previous sample's. (Observed in ~40% of samples;
    summing them overcounts by billions of tokens.)
    """
    model = 'unknown'
    billing_provider = None
    session_id = ''
    records: list[CodexUsageRecord] = []
    prev_cumulative_total: int | None = None
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
                    provider = payload.get('model_provider')
                    if isinstance(provider, str):
                        billing_provider = provider.replace('_', '-')
                    continue
                if raw.get('type') == 'turn_context':
                    m = payload.get('model')
                    if isinstance(m, str) and m:
                        model = m
                    provider = payload.get('model_provider')
                    if isinstance(provider, str):
                        billing_provider = provider.replace('_', '-')
                    continue
                if payload.get('type') != 'token_count':
                    continue
                info = payload.get('info')
                usage = info.get('last_token_usage') if isinstance(info, dict) else None
                if not isinstance(usage, dict):
                    continue
                cumulative = info.get('total_token_usage') if isinstance(info, dict) else None
                cumulative_total = cumulative.get('total_tokens') if isinstance(cumulative, dict) else None
                # Skip re-emitted samples where the session cumulative did not
                # advance (a repeat, not new work).
                if isinstance(cumulative_total, int) and cumulative_total == prev_cumulative_total:
                    continue
                if isinstance(cumulative_total, int):
                    prev_cumulative_total = cumulative_total
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
                    **({'billing_provider': billing_provider} if billing_provider else {}),
                    **({'record_id': f'{session_id}:{len(records)}'} if session_id else {}),
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
