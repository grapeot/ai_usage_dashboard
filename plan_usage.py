"""Estimate API-equivalent subscription capacity from local quota history."""
from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
import sqlite3
from typing import Any, Iterable

import history_store
from plan_sources import PLANS, OLLAMA_PRICE_DATE, OLLAMA_PRICE_URL, UsageEvent, collect_usage, utc_time

MONTHS_PER_WEEK = 365.25 / (12 * 7)
SOURCE_PLANS = {
    'opencode': {'ollama', 'grok', 'codex', 'claude', 'glm'},
    'codex': {'ollama', 'codex'}, 'claude': {'claude'},
    'dsh': {'ollama', 'glm'}, 'cursor': {'cursor'},
    'antigravity': {'antigravity'}, 'grok_native': {'grok'},
}


def window_kind(plan: str, label: str) -> str:
    lower = label.lower()
    if plan == 'cursor':
        return 'monthly'
    if 'week' in lower or lower == '7d':
        return 'weekly'
    if '5h' in lower or '5 hour' in lower:
        return '5h'
    return label


def _resolution(sample: dict) -> float:
    value = sample.get('percentage_resolution')
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) and value > 0 else 1.0


def _pool_records(plan: str, label: str, samples: list[dict], usage: list[UsageEvent]) -> list[UsageEvent]:
    account = samples[0].get('account_fingerprint')
    result = []
    for event in usage:
        if event.plan != plan:
            continue
        if account and event.account_fingerprint and account != event.account_fingerprint:
            continue
        if plan == 'cursor' and event.pool != label:
            continue
        if plan == 'antigravity':
            import antigravity_usage
            family = label.split()[0]
            if antigravity_usage.model_family('', event.model) != family:
                continue
        result.append(event)
    return sorted(result, key=lambda e: utc_time(e.time))


def _fit(xs: list[float], ys: list[float]) -> dict | None:
    mx, my = math.fsum(xs) / len(xs), math.fsum(ys) / len(ys)
    variance = math.fsum((x - mx) ** 2 for x in xs)
    if variance <= 0:
        return None
    slope = math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys)) / variance
    if slope <= 0:
        return None
    intercept = my - slope * mx
    residuals = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
    total_variance = math.fsum((y - my) ** 2 for y in ys)
    return {'capacity_usd': 100 / slope,
            'r_squared': 1 - math.fsum(r * r for r in residuals) / total_variance if total_variance else None,
            'max_residual_percentage_points': max(abs(r) for r in residuals)}


def estimate_segment(plan: str, label: str, samples: list[dict], usage: list[UsageEvent], *, min_change: float = 3.0) -> dict:
    first, last = samples[0], samples[-1]
    start, end = first['_time'], last['_time']
    delta = float(last['percentage']) - float(first['percentage'])
    kind = window_kind(plan, label)
    records = _pool_records(plan, label, samples, usage)
    times = [utc_time(r.time) for r in records]
    lo, hi = bisect_left(times, start), bisect_left(times, end)
    matched = records[lo:hi]
    result: dict[str, Any] = {
        'label': label, 'window_kind': kind, 'pool_id': first.get('pool_id'),
        'account_fingerprint': first.get('account_fingerprint'),
        'start': start.isoformat(), 'end': end.isoformat(),
        'reset_ms': first.get('reset_ms'), 'samples': len(samples),
        'start_percentage': first['percentage'], 'end_percentage': last['percentage'],
        'delta_percentage_points': delta, 'records': len(matched),
        'tokens': sum(r.tokens for r in matched),
        'sources': dict(Counter(r.source for r in matched)),
        'models': dict(Counter(r.model for r in matched)),
        'legacy_samples': sum(not s.get('measurement_source') for s in samples),
        'status': 'insufficient_change',
    }
    absolute = last.get('absolute_limit_usd')
    if isinstance(absolute, (int, float)) and math.isfinite(absolute) and absolute > 0:
        result.update(status='known_limit', capacity_usd=float(absolute), method='upstream_absolute_usd',
                      monthly_equivalent_usd=float(absolute) * MONTHS_PER_WEEK if kind == 'weekly' else float(absolute) if kind == 'monthly' else None)
        return result
    if len(samples) < 3:
        result['reason'] = 'At least three distinct observations are required.'
        return result
    if any(s['percentage'] >= 100 for s in samples):
        result['status'] = 'saturated'
        result['reason'] = 'A full meter may be clipped; its change is not an exact denominator.'
        return result
    if delta < min_change or end <= start:
        result['reason'] = 'Quota movement is too small to calibrate a capacity.'
        return result
    if not matched:
        result['status'] = 'usage_missing'
        result['reason'] = 'Quota changed, but no corresponding local usage was found.'
        return result
    if plan == 'antigravity':
        result['status'] = 'quota_scope_unresolved'
        result['reason'] = 'Per-family maxima do not identify which model allocations share a billing pool.'
        return result
    if any(r.cost_usd is None or not math.isfinite(r.cost_usd) or r.cost_usd < 0 for r in matched):
        result['status'] = 'unpriced_usage'
        result['reason'] = 'Some matched usage has no verified price basis.'
        return result
    costs = [float(r.cost_usd or 0) for r in records]
    prefix = [0.0]
    for cost in costs:
        prefix.append(prefix[-1] + cost)
    consumed = math.fsum(float(r.cost_usd) for r in matched)
    if consumed <= 0:
        result['status'] = 'usage_missing'
        result['reason'] = 'No positive priced consumption is available for this interval.'
        return result
    result['consumption_usd'] = consumed
    result['endpoint_capacity_usd'] = consumed * 100 / delta
    xs = [prefix[bisect_left(times, s['_time'])] - prefix[lo] for s in samples]
    ys = [float(s['percentage']) for s in samples]
    fit = _fit(xs, ys)
    result['fit'] = fit
    capacity = fit['capacity_usd'] if fit else result['endpoint_capacity_usd']
    result['capacity_usd'] = capacity
    result['monthly_equivalent_usd'] = capacity * MONTHS_PER_WEEK if kind == 'weekly' else capacity if kind == 'monthly' else None
    error = (_resolution(first) + _resolution(last)) / 2
    result['rounding_only_endpoint_band_usd'] = {
        'low': consumed * 100 / (delta + error),
        'high': consumed * 100 / (delta - error) if delta > error else None,
    }
    result['status'] = 'unstable_measurement' if fit and (fit['r_squared'] or 0) < 0.9 else 'estimated'
    result['reason'] = 'API-equivalent capacity for the observed workload, not an official dollar entitlement.'
    return result


def _segments(samples: list[dict], *, fixed_window: bool = True) -> tuple[list[list[dict]], list[dict]]:
    segments: list[list[dict]] = []
    resets: list[dict] = []
    current: list[dict] = []
    for sample in samples:
        if sample.get('reset_ms') is None:
            if current:
                segments.append(current)
                current = []
            segments.append([sample])
            continue
        if current:
            previous = current[-1]
            old, new = previous.get('reset_ms'), sample.get('reset_ms')
            reset_changed = old is not None and new is not None and abs(new - old) > 90_000
            dropped = sample['percentage'] < previous['percentage'] - max(_resolution(sample), _resolution(previous)) / 2
            if reset_changed or dropped:
                kind = 'counter_drop'
                if reset_changed:
                    kind = ('early_reset' if sample['_time'].timestamp() * 1000 < old else 'scheduled_reset') if fixed_window else 'window_identity_changed'
                resets.append({'observed_at': sample['_time'].isoformat(), 'kind': kind,
                               'previous_reset_ms': old, 'next_reset_ms': new,
                               'previous_percentage': previous['percentage'], 'next_percentage': sample['percentage']})
                segments.append(current)
                current = []
        # Re-reading a cached/same-time value is not an independent observation.
        if not current or (sample['_time'], sample['percentage']) != (current[-1]['_time'], current[-1]['percentage']):
            current.append(sample)
    if current:
        segments.append(current)
    return segments, resets


def analyze_plan(plan: str, samples: Iterable[dict], usage: list[UsageEvent], *, min_change: float = 3.0,
                 source_errors: dict[str, str] | None = None) -> dict:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    invalid = stale = 0
    for original in samples:
        if original.get('provider') != plan:
            continue
        try:
            row = dict(original, _time=utc_time(original['observed_at']))
            row['percentage'] = float(row['percentage'])
            if not math.isfinite(row['percentage']) or not 0 <= row['percentage'] <= 100:
                invalid += 1
                continue
        except (ValueError, TypeError, KeyError):
            invalid += 1
            continue
        if row.get('measurement_source') in ('cache', 'local_log'):
            stale += 1
            continue
        key = (row.get('label', 'Unknown'), row.get('pool_id'), row.get('account_fingerprint'))
        groups[key].append(row)
    windows = []
    resets = []
    for (label, pool, account), rows in groups.items():
        ordered = sorted(rows, key=lambda s: s['_time'])
        valid = [s for s in ordered if s.get('reset_ms') is not None]
        if not valid:
            if ordered[-1].get('absolute_limit_usd') is not None:
                windows.append(estimate_segment(plan, label, ordered, usage, min_change=min_change))
            else:
                windows.append({'label': label, 'status': 'window_identity_missing', 'samples': len(rows)})
            continue
        segments, events = _segments(ordered, fixed_window=plan != 'antigravity')
        resets.extend(dict(event, label=label, pool_id=pool) for event in events)
        for segment in segments:
            if segment[0].get('reset_ms') is None:
                windows.append({'label': label, 'status': 'window_identity_missing', 'samples': len(segment)})
                continue
            window = estimate_segment(plan, label, segment, usage, min_change=min_change)
            reset = segment[-1].get('reset_ms')
            if reset is not None and segment[-1]['_time'].timestamp() * 1000 >= reset:
                window['status'] = 'quota_stale'
                window['reason'] = 'The upstream reset time is not in the future at this observation.'
            windows.append(window)
    errors = {s: e for s, e in (source_errors or {}).items() if plan in SOURCE_PLANS.get(s, set())}
    if errors:
        for window in windows:
            if window['status'] in ('estimated', 'unstable_measurement'):
                window['status'] = 'usage_incomplete'
    if any(w['status'] == 'known_limit' for w in windows):
        status = 'known_limit'
    elif any(w['status'] == 'estimated' for w in windows):
        status = 'estimated'
    elif windows:
        priority = ('usage_incomplete', 'usage_missing', 'unpriced_usage', 'quota_scope_unresolved',
                    'unstable_measurement', 'saturated', 'quota_stale', 'window_identity_missing', 'insufficient_change')
        status = next((s for s in priority if any(w['status'] == s for w in windows)), 'insufficient_change')
    else:
        status = 'quota_stale' if stale else 'quota_unavailable'
    return {'plan': plan, 'status': status, 'windows': windows, 'reset_events': resets,
            'ignored_stale_samples': stale, 'invalid_samples': invalid, 'source_errors': errors}


def build_report(plans: list[str], *, days: float = 7, path: str | None = None, window: str | None = None,
                 min_change: float = 3, now: datetime | None = None) -> dict:
    if not math.isfinite(days) or not math.isfinite(min_change) or days <= 0 or min_change <= 0:
        raise ValueError('days and min_change must be positive')
    end = utc_time(now or datetime.now(timezone.utc))
    start = end - timedelta(days=days)
    samples = []
    available = Counter()
    for row in history_store.read_quota_samples(path=path):
        try:
            observed = utc_time(row['observed_at'])
        except (KeyError, ValueError, TypeError):
            continue
        if row.get('provider') in plans and start <= observed <= end:
            available[row['provider']] += 1
            if window:
                requested = {'7d': 'weekly', 'weekly': 'weekly', '5h': '5h', 'monthly': 'monthly'}.get(window.lower())
                if requested and window_kind(row['provider'], row.get('label', '')) != requested:
                    continue
                if not requested and row.get('label', '').lower() != window.lower():
                    continue
            samples.append(row)
    events: list[UsageEvent] = []
    diagnostics: dict = {'source_errors': {}, 'excluded_records': {}}
    if samples:
        earliest = min(utc_time(s['observed_at']) for s in samples)
        latest = max(utc_time(s['observed_at']) for s in samples)
        active = {s['provider'] for s in samples}
        events, diagnostics = collect_usage(active, earliest, latest)
    results = [analyze_plan(p, samples, events, min_change=min_change,
                           source_errors=diagnostics['source_errors']) for p in plans]
    for result in results:
        if window and available[result['plan']] and not any(s['provider'] == result['plan'] for s in samples):
            result['status'] = 'window_unavailable'
    return {'schema_version': 1, 'generated_at': end.isoformat(),
            'history_start': start.isoformat(), 'history_end': end.isoformat(), 'snapshot_count': len(samples),
            'projection_assumption': 'Nominal weekly cadence; extra resets are not forecast or included in the monthly equivalent.',
            'price_basis': {'ollama': {'source': OLLAMA_PRICE_URL, 'as_of': OLLAMA_PRICE_DATE},
                            'cursor': 'recorded charged cents for included plan calls',
                            'others': 'current pricing_config API reference rates'},
            'plans': results,
            'usage_diagnostics': diagnostics}


def format_report(report: dict) -> str:
    lines = ['API-equivalent plan capacity (observed workload; not official dollar credits)',
             f"History: {report['history_start']} .. {report['history_end']} ({report['snapshot_count']} samples)"]
    for plan in report['plans']:
        lines.append(f"\n{plan['plan']}: {plan['status']}")
        shown = set()
        for window in plan['windows']:
            key = (window['label'], window['status'])
            amount = window.get('capacity_usd') if window['status'] in ('estimated', 'known_limit') else None
            if amount is None and key in shown:
                continue
            shown.add(key)
            monthly = window.get('monthly_equivalent_usd') if amount is not None else None
            value = f"${amount:.2f}/window" if amount is not None else '-'
            if monthly is not None:
                value += f", ${monthly:.2f}/month equivalent"
            lines.append(f"  {window['label']}: {window['status']} | {value} | {window['samples']} samples")
        for event in plan['reset_events']:
            if event['kind'] in ('early_reset', 'counter_drop'):
                lines.append(f"  reset: {event['kind']} at {event['observed_at']} (segments estimated separately)")
    return '\n'.join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description='Estimate plan capacity from local quota snapshots and usage')
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument('--plan', choices=PLANS)
    scope.add_argument('--all-plans', action='store_true')
    parser.add_argument('--days', type=float, default=7)
    parser.add_argument('--window', help='Window kind or label, e.g. 7d, 5h, Weekly, Models')
    parser.add_argument('--history-db', help='Override the local history database')
    parser.add_argument('--min-change', type=float, default=3, help='Minimum movement in percentage points')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    import auto_usage
    auto_usage.load_env()
    try:
        report = build_report(list(PLANS) if args.all_plans else [args.plan], days=args.days,
                              path=args.history_db, window=args.window, min_change=args.min_change)
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, allow_nan=False) if args.json else format_report(report))


if __name__ == '__main__':
    main()
