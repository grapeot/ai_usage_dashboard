from datetime import datetime, timedelta, timezone
import json
import sys

import pytest

import plan_usage
from plan_usage import UsageEvent

T = datetime(2024, 1, 1, tzinfo=timezone.utc)
RESET = int((T + timedelta(days=7)).timestamp() * 1000)


def sample(hour, pct, plan='grok', label='Weekly', reset=RESET, **extra):
    return {'provider': plan, 'label': label, 'observed_at': (T + timedelta(hours=hour)).isoformat(),
            'percentage': pct, 'percentage_resolution': 0.1, 'reset_ms': reset, **extra}


def event(hour, dollars, plan='grok', **extra):
    return UsageEvent(plan=plan, time=T + timedelta(hours=hour), cost_usd=dollars, tokens=100, **extra)


def test_recovers_a_synthetic_fixed_budget_and_monthly_basis():
    samples = [sample(0, 0), sample(1, 10), sample(2, 30), sample(3, 50)]
    usage = [event(0.5, 10), event(1.5, 20), event(2.5, 20)]
    result = plan_usage.analyze_plan('grok', samples, usage)
    window = result['windows'][0]
    assert result['status'] == 'estimated'
    assert window['capacity_usd'] == pytest.approx(100)
    assert window['endpoint_capacity_usd'] == pytest.approx(100)
    assert window['fit']['r_squared'] == pytest.approx(1)
    assert window['monthly_equivalent_usd'] == pytest.approx(100 * 365.25 / 12 / 7)


def test_early_codex_reset_splits_calibration_without_announcements():
    new_reset = RESET + 3 * 3600 * 1000
    samples = [sample(0, 0, 'codex', '7d'), sample(1, 10, 'codex', '7d'), sample(2, 30, 'codex', '7d'),
               sample(3, 2, 'codex', '7d', new_reset), sample(4, 12, 'codex', '7d', new_reset), sample(5, 32, 'codex', '7d', new_reset)]
    usage = [event(0.5, 10, 'codex'), event(1.5, 20, 'codex'), event(2.5, 500, 'codex'),
             event(3.5, 10, 'codex'), event(4.5, 20, 'codex')]
    result = plan_usage.analyze_plan('codex', samples, usage)
    assert len(result['windows']) == 2
    assert [w['capacity_usd'] for w in result['windows']] == pytest.approx([100, 100])
    assert result['reset_events'][0]['kind'] == 'early_reset'


def test_counter_drop_without_new_reset_also_splits():
    result = plan_usage.analyze_plan('grok', [sample(0, 10), sample(1, 20), sample(2, 1)], [])
    assert result['reset_events'][0]['kind'] == 'counter_drop'
    assert len(result['windows']) == 2


def test_reset_jitter_does_not_create_an_extra_epoch():
    samples = [sample(0, 0), sample(1, 10, reset=RESET + 60_000), sample(2, 20)]
    result = plan_usage.analyze_plan('grok', samples, [event(0.5, 10), event(1.5, 10)])
    assert len(result['windows']) == 1
    assert result['reset_events'] == []


@pytest.mark.parametrize('percentages,status', [([10, 10, 10], 'insufficient_change'), ([10, 10.1, 10.2], 'insufficient_change'), ([90, 95, 100], 'saturated')])
def test_flat_small_or_clipped_meters_do_not_become_infinite_or_zero_capacity(percentages, status):
    result = plan_usage.analyze_plan('grok', [sample(i, p) for i, p in enumerate(percentages)], [event(0.5, 10)])
    assert result['status'] == status
    assert 'capacity_usd' not in result['windows'][0]


def test_changed_quota_without_local_records_is_usage_missing():
    result = plan_usage.analyze_plan('grok', [sample(0, 0), sample(1, 10), sample(2, 20)], [])
    assert result['status'] == 'usage_missing'
    assert 'capacity_usd' not in result['windows'][0]


def test_unpriced_usage_is_not_silently_ignored():
    result = plan_usage.analyze_plan('grok', [sample(0, 0), sample(1, 10), sample(2, 20)], [event(0.5, 10), event(1.5, None)])
    assert result['status'] == 'unpriced_usage'


def test_cached_replays_do_not_calibrate_a_plan():
    samples = [sample(0, 0, measurement_source='cache'), sample(1, 10, measurement_source='cache'), sample(2, 20, measurement_source='cache')]
    result = plan_usage.analyze_plan('grok', samples, [event(0.5, 10)])
    assert result['status'] == 'quota_stale'
    assert result['ignored_stale_samples'] == 3


def test_missing_reset_is_a_barrier_not_a_bridge_between_samples():
    result = plan_usage.analyze_plan('grok', [sample(0, 0), sample(1, 10, reset=None), sample(2, 20)], [event(0.5, 10), event(1.5, 10)])
    assert not any(w['status'] == 'estimated' for w in result['windows'])
    assert any(w['status'] == 'window_identity_missing' for w in result['windows'])


def test_known_absolute_dollar_limit_does_not_require_usage_or_a_percentage_change():
    result = plan_usage.analyze_plan('cursor', [sample(0, 9, 'cursor', 'Models', reset=None, absolute_limit_usd=100)], [])
    assert result['status'] == 'known_limit'
    assert result['windows'][0]['capacity_usd'] == 100
    assert result['windows'][0]['monthly_equivalent_usd'] == 100


def test_known_different_accounts_do_not_mix_usage():
    samples = [sample(0, 0, account_fingerprint='fake-a'), sample(1, 10, account_fingerprint='fake-a'), sample(2, 20, account_fingerprint='fake-a')]
    usage = [event(0.5, 10, account_fingerprint='fake-a'), event(1.5, 10, account_fingerprint='fake-a'), event(1.5, 500, account_fingerprint='fake-b')]
    assert plan_usage.analyze_plan('grok', samples, usage)['windows'][0]['capacity_usd'] == pytest.approx(100)


def test_five_hour_capacity_is_not_multiplied_into_a_month():
    samples = [sample(0, 0, label='5h'), sample(1, 10, label='5h'), sample(2, 20, label='5h')]
    result = plan_usage.analyze_plan('grok', samples, [event(0.5, 10), event(1.5, 10)])
    assert result['windows'][0]['monthly_equivalent_usd'] is None


def test_family_max_is_not_an_independent_antigravity_pool():
    samples = [sample(i, i * 10, 'antigravity', 'Gemini 5h', quota_scope='family_max') for i in range(3)]
    events = [event(0.5, 10, 'antigravity', model='gemini-3-flash'), event(1.5, 10, 'antigravity', model='gemini-3-flash')]
    assert plan_usage.analyze_plan('antigravity', samples, events)['status'] == 'quota_scope_unresolved'


def test_moving_antigravity_family_deadlines_are_not_claimed_as_early_refills():
    samples = [sample(i, 0, 'antigravity', 'Claude 5h', reset=RESET + i * 3600 * 1000) for i in range(3)]
    result = plan_usage.analyze_plan('antigravity', samples, [])
    assert all(e['kind'] == 'window_identity_changed' for e in result['reset_events'])


def test_source_errors_prevent_complete_capacity_claims():
    samples = [sample(0, 0), sample(1, 10), sample(2, 20)]
    result = plan_usage.analyze_plan('grok', samples, [event(0.5, 10), event(1.5, 10)], source_errors={'opencode': 'OSError'})
    assert result['status'] == 'usage_incomplete'


def test_cli_all_plans_empty_history_is_read_only_and_offline(tmp_path, monkeypatch, capsys):
    import auto_usage
    monkeypatch.setattr(auto_usage, 'load_env', lambda: None)
    missing = tmp_path / 'missing.db'
    monkeypatch.setattr(sys, 'argv', ['plan_usage', '--all-plans', '--json', '--history-db', str(missing)])
    def no_source_read(*args, **kwargs):
        raise AssertionError('no history means no local source scan')
    monkeypatch.setattr(plan_usage, 'collect_usage', no_source_read)
    plan_usage.main()
    report = json.loads(capsys.readouterr().out)
    assert len(report['plans']) == 7
    assert all(p['status'] == 'quota_unavailable' for p in report['plans'])
    assert not missing.exists()


def test_window_filter_is_independent_of_plan_and_supports_exact_labels(monkeypatch):
    samples = [sample(0, 9, 'cursor', 'Models'), sample(1, 9, 'cursor', 'Other')]
    monkeypatch.setattr(plan_usage.history_store, 'read_quota_samples', lambda **kwargs: samples)
    monkeypatch.setattr(plan_usage, 'collect_usage', lambda *args: ([], {'source_errors': {}, 'excluded_records': {}}))
    report = plan_usage.build_report(['cursor'], now=T + timedelta(days=1), window='7d')
    assert report['plans'][0]['status'] == 'window_unavailable'
    report = plan_usage.build_report(['cursor'], now=T + timedelta(days=1), window='Models')
    assert len(report['plans'][0]['windows']) == 1
    assert report['plans'][0]['windows'][0]['label'] == 'Models'
