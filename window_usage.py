"""Aggregate usage over an arbitrary time window, from local sources.

The dashboard reports calendar-day buckets, which is not enough to answer
"how many tokens (and dollars) did provider X use between 14:00 and 18:00?" —
the question that aligns a quota-bar window with actual usage. Every local
usage source stores event-level timestamps, so this module reuses the same
per-record iterators the dashboard uses and aggregates over an exact
``[from, to)`` interval instead of snapping to days.

The canonical token split (non-cached input, cached input, output, cache-write)
and cost come from the same helpers the dashboard uses, so the two can never
drift. Provider buckets match the dashboard's display buckets.

Precision: all six sources carry event timestamps, so results are
second/minute-precise. Cursor is the one caveat — its data is a cloud CSV
exported by the dashboard's Cursor step; if that export has not been refreshed
recently it may not cover the requested window.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Iterator

import auto_usage as _auto
import antigravity_usage as _ag
import codex_local as _codex_local
import dsh_usage as _dsh_usage
from pricing_config import calc_cost_from_parts, get_pricing


@dataclass
class UsageRecord:
    """One timestamped usage event with the canonical split."""

    time: datetime
    provider: str
    model: str
    input_non_cached: int = 0
    input_cached: int = 0
    output: int = 0
    cache_write: int = 0
    cache_write_1h: int = 0
    source: str = ''
    billing_provider: str | None = None
    plan_pool: str | None = None
    plan_included: bool | None = None
    recorded_cost_usd: float | None = None
    account_fingerprint: str | None = None
    record_id: str | None = None

    def total(self) -> int:
        return self.input_non_cached + self.input_cached + self.output + self.cache_write + self.cache_write_1h


@dataclass
class WindowTotals:
    """Aggregated totals for one provider (or model) over the window."""

    input_non_cached: int = 0
    input_cached: int = 0
    output: int = 0
    cache_write: int = 0
    cache_write_1h: int = 0
    cost_usd: float = 0.0
    requests: int = 0
    models: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.input_non_cached + self.input_cached + self.output + self.cache_write + self.cache_write_1h

    @property
    def cache_hit_rate(self) -> float | None:
        denom = self.input_non_cached + self.input_cached
        return self.input_cached / denom if denom else None


def _in_window(t: datetime, start: datetime, end: datetime) -> bool:
    return start <= t < end


def _bucket(provider_id: str, model_id: str) -> str:
    return _auto.classify_model_bucket(provider_id, model_id)


def iter_opencode_records(start: datetime, end: datetime) -> Iterator[UsageRecord]:
    """OpenCode assistant messages with per-message timestamps."""
    _auto.configure_opencode_skill_path()
    import importlib
    try:
        ocs_query = importlib.import_module('opencode_skill.query')
    except ImportError:
        ocs_query = None
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    if ocs_query is not None:
        for m in ocs_query.iter_assistant_messages(since_ms=start_ms, until_ms=end_ms, archive_dbs=_auto._opencode_extra_dbs()):
            t = datetime.fromtimestamp(m.time_created / 1000)
            if not _in_window(t, start, end):
                continue
            yield UsageRecord(
                time=t,
                provider=_bucket(m.provider or '', m.model or ''),
                model=m.model or 'unknown',
                input_non_cached=m.tokens_input,
                input_cached=m.tokens_cache_read,
                output=m.tokens_output + m.tokens_reasoning,
                cache_write=m.tokens_cache_write,
                source='opencode',
                billing_provider=m.provider,
                record_id=m.id,
            )
        return
    yield from _iter_opencode_db_records(start, end, start_ms, end_ms)


def _iter_opencode_db_records(start: datetime, end: datetime, start_ms: int, end_ms: int) -> Iterator[UsageRecord]:
    import json
    import sqlite3
    if not _auto.OPENCODE_DB.exists():
        return
    try:
        conn = sqlite3.connect(f'file:{_auto.OPENCODE_DB}?mode=ro', uri=True)
        cur = conn.cursor()
        cur.execute(
            'SELECT id, time_created, data FROM message '
            "WHERE json_extract(data, '$.role') = 'assistant' AND time_created >= ? AND time_created < ?",
            (start_ms, end_ms),
        )
        for message_id, time_created, data_str in cur:
            try:
                msg = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            tokens = msg.get('tokens', {})
            cache = tokens.get('cache', {}) if isinstance(tokens.get('cache'), dict) else {}
            t = datetime.fromtimestamp(time_created / 1000)
            if not _in_window(t, start, end):
                continue
            provider_id = msg.get('providerID', '')
            model_id = msg.get('modelID', 'unknown')
            yield UsageRecord(
                time=t,
                provider=_bucket(provider_id, model_id),
                model=model_id,
                input_non_cached=int(tokens.get('input', 0) or 0),
                input_cached=int(cache.get('read', 0) or 0),
                output=int(tokens.get('output', 0) or 0) + int(tokens.get('reasoning', 0) or 0),
                cache_write=int(cache.get('write', 0) or 0),
                source='opencode',
                billing_provider=provider_id,
                record_id=message_id,
            )
        conn.close()
    except sqlite3.Error:
        return


def iter_dsh_records(start: datetime, end: datetime) -> Iterator[UsageRecord]:
    for r in _dsh_usage.iter_dsh_usage_records(start_date=start.date(), end_date=end.date()):
        t = r['time']
        if not _in_window(t, start, end):
            continue
        yield UsageRecord(
            time=t,
            provider=_auto.classify_dsh_bucket(r['model']),
            model=r['model'],
            input_non_cached=r['input'],
            input_cached=r['cache_read'],
            output=r['output'],
            cache_write=r['cache_write'],
            source='dsh',
            billing_provider=r['model'].partition('/')[0],
        )


def iter_claude_records(start: datetime, end: datetime) -> Iterator[UsageRecord]:
    for r in _auto.iter_claude_usage_records(start_date=start.date(), end_date=end.date()):
        t = r['time']
        if not _in_window(t, start, end):
            continue
        cache_write_5m, cache_write_1h, _ = _auto.split_claude_cache_write_tokens(r)
        model_id = _auto.normalize_claude_model_id(r['model'], r['speed'])
        yield UsageRecord(
            time=t,
            provider=_bucket('anthropic', model_id),
            model=model_id,
            input_non_cached=r['input'],
            input_cached=r['cache_read'],
            output=r['output'],
            cache_write=cache_write_5m,
            cache_write_1h=cache_write_1h,
            source='claude',
            billing_provider='anthropic',
        )


def iter_codex_records(start: datetime, end: datetime) -> Iterator[UsageRecord]:
    for r in _codex_local.iter_usage_records(start_date=start.date(), end_date=end.date()):
        t = r['time']
        if not _in_window(t, start, end):
            continue
        yield UsageRecord(
            time=t,
            provider='gpt_opencode',
            model=r['model'],
            input_non_cached=r['input_non_cached'],
            input_cached=r['input_cached'],
            output=r['output'],
            cache_write=r['cache_write'],
            source='codex',
            billing_provider=r.get('billing_provider'),
            record_id=r.get('record_id'),
        )


def iter_antigravity_records(start: datetime, end: datetime) -> Iterator[UsageRecord]:
    for entry in _auto._load_antigravity_cache():
        ts = _ag.parse_timestamp(entry.get('timestamp'))
        if not ts:
            continue
        t = datetime.fromtimestamp(ts / 1000)
        if not _in_window(t, start, end):
            continue
        model_id = entry.get('model', 'unknown')
        yield UsageRecord(
            time=t,
            provider=_auto._classify_antigravity_model(model_id),
            model=model_id,
            input_non_cached=int(entry.get('input', 0) or 0),
            input_cached=int(entry.get('cache_read', 0) or 0),
            output=int(entry.get('output', 0) or 0) + int(entry.get('thinking', 0) or 0),
            cache_write=int(entry.get('cache_write', 0) or 0),
            source='antigravity',
            billing_provider='antigravity',
        )


def iter_cursor_records(start: datetime, end: datetime) -> Iterator[UsageRecord]:
    import csv
    import os
    path = os.path.join(_auto.SCRIPT_DIR, 'cursor.csv')
    if not os.path.exists(path):
        return
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                t = datetime.fromisoformat(str(row['Date']).replace('Z', '+00:00')).astimezone().replace(tzinfo=None)
            except (KeyError, ValueError):
                continue
            if not _in_window(t, start, end):
                continue
            model = row.get('Model', 'unknown')
            try:
                recorded_cost = float(row['Charged Cents']) / 100
            except (KeyError, ValueError):
                recorded_cost = None
            kind = row.get('Kind', '').upper()
            yield UsageRecord(
                time=t,
                provider='cursor',
                model=model,
                input_non_cached=int(float(row.get('Input Tokens', 0) or 0)),
                input_cached=int(float(row.get('Cache Read Tokens', 0) or 0)),
                output=int(float(row.get('Output Tokens', 0) or 0)),
                cache_write=int(float(row.get('Cache Write Tokens', 0) or 0)),
                source='cursor',
                billing_provider='cursor',
                plan_pool='Models' if ('composer' in model.lower() or model.lower().startswith(('cursor-', 'auto'))) else 'Other',
                plan_included='INCLUDED' in kind if kind else None,
                recorded_cost_usd=recorded_cost,
            )


# Source registry: name -> iterator. All are timestamp-precise; Cursor depends
# on a recent cloud CSV export.
SOURCES: dict[str, callable] = {
    'opencode': iter_opencode_records,
    'dsh': iter_dsh_records,
    'claude': iter_claude_records,
    'codex': iter_codex_records,
    'antigravity': iter_antigravity_records,
    'cursor': iter_cursor_records,
}

# The dashboard's daily cost model prices Codex, DSH, OpenCode, Claude Code, and
# Antigravity, but not Cursor: Cursor is reported for token volume only, and its
# per-request costs are not comparable to API-equivalent token pricing. Keep the
# same basis here so a full-day window equals the dashboard's day row. Tokens are
# still counted for Cursor; only its cost is suppressed.
_UNPRICED_PROVIDERS = frozenset({'cursor'})


def aggregate(
    start: datetime,
    end: datetime,
    *,
    provider: str | None = None,
    sources: list[str] | None = None,
) -> dict[str, WindowTotals]:
    """Aggregate canonical usage over ``[start, end)``, keyed by provider.

    ``provider`` filters to a single display bucket; ``sources`` limits which
    data sources are read (default: all). Cost is summed on the dashboard's
    basis (see ``_UNPRICED_PROVIDERS``).
    """
    if end < start:
        raise ValueError('end must not precede start')
    totals: dict[str, WindowTotals] = {}
    for name in (sources or list(SOURCES)):
        iterator = SOURCES.get(name)
        if iterator is None:
            raise ValueError(f'unknown source: {name}')
        for record in iterator(start, end):
            if not _in_window(record.time, start, end):
                continue
            if provider and record.provider != provider:
                continue
            entry = totals.setdefault(record.provider, WindowTotals())
            entry.input_non_cached += record.input_non_cached
            entry.input_cached += record.input_cached
            entry.output += record.output
            entry.cache_write += record.cache_write
            entry.cache_write_1h += record.cache_write_1h
            entry.requests += 1
            entry.models[record.model] = entry.models.get(record.model, 0) + record.total()
            if record.provider in _UNPRICED_PROVIDERS:
                continue
            pricing = (
                _ag.resolve_pricing(record.model, pricing_lookup=get_pricing)
                if record.source == 'antigravity'
                else get_pricing(record.model)
            )
            if pricing:
                entry.cost_usd += calc_cost_from_parts(
                    pricing,
                    input_non_cached=record.input_non_cached,
                    input_cached=record.input_cached,
                    output=record.output,
                    cache_write=record.cache_write,
                    cache_write_1h=record.cache_write_1h,
                )
    return totals


def parse_bound(value: str) -> datetime:
    """Parse a window bound: full ISO datetime, or a bare date (local midnight)."""
    value = value.strip()
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.strptime(value, '%Y-%m-%d')


def format_report(start: datetime, end: datetime, totals: dict[str, WindowTotals]) -> str:
    lines = [f"Window: {start.isoformat()} .. {end.isoformat()} (end exclusive)"]
    if not totals:
        lines.append('No usage in window.')
        return '\n'.join(lines)
    grand_tokens = 0
    grand_cost = 0.0
    lines.append(f"{'Provider':<12} {'requests':>9} {'tokens':>14} {'in:out':>8} {'cache%':>7} {'cost':>10}")
    for name in sorted(totals, key=lambda k: totals[k].total, reverse=True):
        t = totals[name]
        grand_tokens += t.total
        grand_cost += t.cost_usd
        in_out = f"{t.input_non_cached / t.output:.1f}:1" if t.output else '-'
        cache_pct = f"{t.cache_hit_rate * 100:.1f}%" if t.cache_hit_rate is not None else '-'
        lines.append(f"{name:<12} {t.requests:>9,} {t.total:>14,} {in_out:>8} {cache_pct:>7} {t.cost_usd:>10.2f}")
    lines.append('-' * 64)
    lines.append(f"{'TOTAL':<12} {'':>9} {grand_tokens:>14,} {'':>8} {'':>7} {grand_cost:>10.2f}")
    return '\n'.join(lines)
