import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import window_usage
import pytest
import antigravity_usage


def test_parse_bound_iso_datetime():
    assert window_usage.parse_bound('2026-10-05T14:30') == datetime(2026, 10, 5, 14, 30)


def test_parse_bound_bare_date_is_midnight():
    assert window_usage.parse_bound('2026-10-05') == datetime(2026, 10, 5, 0, 0)


def test_aggregate_end_before_start_raises():
    try:
        window_usage.aggregate(datetime(2026, 10, 6), datetime(2026, 10, 5))
    except ValueError:
        return
    raise AssertionError('expected ValueError')


def test_aggregate_unknown_source_raises():
    try:
        window_usage.aggregate(datetime(2026, 10, 5), datetime(2026, 10, 6), sources=['nope'])
    except ValueError:
        return
    raise AssertionError('expected ValueError')


def test_aggregate_empty_window_returns_empty(monkeypatch):
    # A source that yields nothing in the window returns no totals.
    monkeypatch.setitem(window_usage.SOURCES, 'empty', lambda s, e: iter(()))
    totals = window_usage.aggregate(datetime(2026, 10, 5), datetime(2026, 10, 6), sources=['empty'])
    assert totals == {}


def test_window_totals_properties():
    t = window_usage.WindowTotals(input_non_cached=100, input_cached=300, output=50)
    assert t.total == 450
    assert abs(t.cache_hit_rate - 0.75) < 1e-9


def test_window_totals_cache_hit_rate_none_when_no_input():
    t = window_usage.WindowTotals(output=10)
    assert t.cache_hit_rate is None


def test_format_report_lists_providers_and_total():
    totals = {
        'deepseek': window_usage.WindowTotals(input_non_cached=100, output=10, cost_usd=1.5, requests=2),
    }
    report = window_usage.format_report(datetime(2026, 10, 5), datetime(2026, 10, 6), totals)
    assert 'deepseek' in report
    assert 'TOTAL' in report
    assert '1.50' in report


def test_format_report_handles_empty():
    report = window_usage.format_report(datetime(2026, 10, 5), datetime(2026, 10, 6), {})
    assert 'No usage in window' in report


def test_cursor_is_excluded_from_cost_basis():
    # Parity with the dashboard, which reports Cursor tokens but does not price
    # them. Only the Cursor provider is unpriced.
    assert 'cursor' in window_usage._UNPRICED_PROVIDERS
    assert 'deepseek' not in window_usage._UNPRICED_PROVIDERS


def test_aggregate_sums_records_and_prices(monkeypatch):
    # Inject a synthetic source so the aggregation + cost path is exercised
    # deterministically (no real local data).
    from datetime import datetime

    from pricing_config import get_pricing

    recs = [
        window_usage.UsageRecord(
            time=datetime(2026, 10, 5, 1, 0),
            provider='deepseek',
            model='deepseek-v4-flash',
            input_non_cached=1_000_000,
            input_cached=1_000_000,
            output=100_000,
        ),
        window_usage.UsageRecord(
            time=datetime(2026, 10, 5, 2, 0),
            provider='deepseek',
            model='deepseek-v4-flash',
            input_non_cached=500_000,
            output=50_000,
        ),
    ]
    monkeypatch.setitem(window_usage.SOURCES, 'fake', lambda s, e: iter(recs))
    totals = window_usage.aggregate(datetime(2026, 10, 5), datetime(2026, 10, 6), sources=['fake'])
    ds = totals['deepseek']
    assert ds.input_non_cached == 1_500_000
    assert ds.input_cached == 1_000_000
    assert ds.output == 150_000
    assert ds.requests == 2
    p = get_pricing('deepseek-v4-flash')
    expected = (
        1_500_000 * p['input'] / 1e6
        + 1_000_000 * p['cached'] / 1e6
        + 150_000 * p['output'] / 1e6
    )
    assert abs(ds.cost_usd - expected) < 1e-9


def test_aggregate_provider_filter(monkeypatch):
    recs = [
        window_usage.UsageRecord(time=datetime(2026, 10, 5, 1), provider='deepseek', model='deepseek-v4-flash', input_non_cached=10),
        window_usage.UsageRecord(time=datetime(2026, 10, 5, 2), provider='grok', model='grok-4.7', input_non_cached=20),
    ]
    monkeypatch.setitem(window_usage.SOURCES, 'fake2', lambda s, e: iter(recs))
    totals = window_usage.aggregate(datetime(2026, 10, 5), datetime(2026, 10, 6), provider='grok', sources=['fake2'])
    assert set(totals) == {'grok'}
    assert totals['grok'].input_non_cached == 20


def test_aggregate_excludes_out_of_window_records(monkeypatch):
    # aggregate re-checks the window defensively, so a source that returns
    # extra records cannot leak out-of-window usage into the totals.
    recs = [
        window_usage.UsageRecord(time=datetime(2026, 10, 5, 0, 30), provider='grok', model='grok-4.7', input_non_cached=5),
        window_usage.UsageRecord(time=datetime(2026, 10, 6, 0, 30), provider='grok', model='grok-4.7', input_non_cached=999),
    ]
    monkeypatch.setitem(window_usage.SOURCES, 'fake3', lambda s, e: iter(recs))
    totals = window_usage.aggregate(datetime(2026, 10, 5), datetime(2026, 10, 6), sources=['fake3'])
    assert totals['grok'].requests == 1
    assert totals['grok'].input_non_cached == 5


@pytest.mark.parametrize('model,bucket', [
    ('gemini-3-flash', 'gemini'),
    ('claude-opus-4.6', 'anthropic'),
    ('gpt-5.4', 'gpt_opencode'),
    ('deepseek-v4-flash', 'deepseek'),
    ('qwen3.8-27b', 'qwen'),
    ('model_placeholder_m20', 'gemini'),
])
def test_antigravity_window_uses_dashboard_model_buckets(monkeypatch, model, bucket):
    start = datetime(2026, 10, 5)
    entry = {'timestamp': int(start.timestamp() * 1000), 'model': model, 'input': 100}
    monkeypatch.setattr(window_usage._auto, '_load_antigravity_cache', lambda: [entry])
    totals = window_usage.aggregate(start, datetime(2026, 10, 6), sources=['antigravity'])
    assert set(totals) == {bucket}
    assert totals[bucket].total == 100
    assert totals[bucket].models == {model: 100}


@pytest.mark.parametrize('model', ['gemini-unresolved-test', 'model_placeholder_test', 'custom-test-model'])
def test_antigravity_window_cost_matches_dashboard_fallback(monkeypatch, model):
    start = datetime(2026, 10, 5)
    tokens = {'input': 1_000, 'cache_read': 500, 'output': 100, 'cache_write': 200}
    entry = {'timestamp': int(start.timestamp() * 1000), 'model': model, **tokens}
    monkeypatch.setattr(window_usage._auto, '_load_antigravity_cache', lambda: [entry])
    # Only the prefixed lookup resolves the custom model; unresolved Gemini and
    # placeholders must instead use the Gemini fallback. All prices are fake.
    prices = {
        'antigravity-custom-test-model': {'input': 2, 'cached': 0.2, 'output': 5, 'cache_write': 3},
        'gemini-3-flash': {'input': 1, 'cached': 0.1, 'output': 4, 'cache_write': 2},
    }
    monkeypatch.setattr(window_usage, 'get_pricing', prices.get)
    expected = antigravity_usage.calculate_cost(
        {start.date(): {model: tokens}}, pricing_lookup=prices.get,
    )[start.date()]
    totals = window_usage.aggregate(start, datetime(2026, 10, 6), sources=['antigravity'])
    assert expected > 0
    assert sum(t.cost_usd for t in totals.values()) == pytest.approx(expected)


def test_antigravity_fallback_does_not_price_other_sources(monkeypatch):
    start = datetime(2026, 10, 5)
    records = [window_usage.UsageRecord(
        time=start, provider='gemini', model='gemini-unresolved-test', input_non_cached=1_000,
    )]
    monkeypatch.setitem(window_usage.SOURCES, 'fake', lambda s, e: iter(records))
    prices = {'gemini-3-flash': {'input': 1, 'output': 4}}
    monkeypatch.setattr(window_usage, 'get_pricing', prices.get)
    totals = window_usage.aggregate(start, datetime(2026, 10, 6), sources=['fake'])
    assert totals['gemini'].total == 1_000
    assert totals['gemini'].cost_usd == 0


@pytest.mark.parametrize('nested_5m,nested_1h,expected_5m', [(20, 30, 70), (0, 0, 100), (70, 30, 70)])
def test_claude_window_preserves_reconciled_cache_writes(monkeypatch, nested_5m, nested_1h, expected_5m):
    start = datetime(2026, 10, 5)
    record = {
        'time': start, 'model': 'claude-opus-4-6', 'speed': '',
        'input': 10, 'cache_read': 40, 'output': 5,
        'cache_write': 100, 'cache_write_5m': nested_5m, 'cache_write_1h': nested_1h,
    }
    monkeypatch.setattr(window_usage._auto, 'iter_claude_usage_records', lambda **kwargs: iter([record]))
    totals = window_usage.aggregate(start, datetime(2026, 10, 6), sources=['claude'])
    claude = totals['anthropic']
    assert claude.cache_write == expected_5m
    assert claude.cache_write_1h == nested_1h
    assert claude.total == 155
    # Opus 4.6: input $5/M, cache read $0.5/M, output $25/M,
    # 5-minute cache write $6.25/M, 1-hour cache write $10/M.
    expected_cost = (10 * 5 + 40 * 0.5 + 5 * 25 + expected_5m * 6.25 + nested_1h * 10) / 1e6
    assert claude.cost_usd == pytest.approx(expected_cost)
