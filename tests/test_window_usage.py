import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import window_usage


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
