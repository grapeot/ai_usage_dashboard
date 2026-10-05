import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import history_store


@pytest.fixture()
def hist_db(tmp_path, monkeypatch):
    path = tmp_path / 'history.db'
    monkeypatch.setenv('AI_USAGE_HISTORY_DB', str(path))
    return str(path)


def _quota(provider='ollama', label='7d', pct=2.5, reset='2026-10-12T00:00', reset_ms=1000):
    return {
        'provider': provider,
        'label': label,
        'percentage': pct,
        'next_reset_iso': reset,
        'next_reset_time_ms': reset_ms,
        'usage': None,
        'remaining': None,
    }


def test_every_reading_is_recorded(hist_db):
    assert history_store.record_quota_snapshots([_quota()]) == 1


def test_repeated_reading_is_not_deduplicated(hist_db):
    # No dedup: an unchanged read is still recorded, so the series carries how
    # long a percentage stayed flat.
    history_store.record_quota_snapshots([_quota()])
    assert history_store.record_quota_snapshots([_quota()]) == 1
    assert len(history_store.quota_history(provider='ollama', label='7d')) == 2


def test_changed_percentage_is_recorded(hist_db):
    history_store.record_quota_snapshots([_quota(pct=2.5)])
    assert history_store.record_quota_snapshots([_quota(pct=3.1)]) == 1
    rows = history_store.quota_history(provider='ollama', label='7d')
    assert [r['percentage'] for r in rows] == [2.5, 3.1]


def test_window_rollover_is_recorded(hist_db):
    # reset_iso is derived from the normalized reset_ms, so distinct windows are
    # distinguished by their reset_ms (a full window apart).
    wk1 = 1_791_264_000_000
    wk2 = wk1 + 7 * 24 * 3600 * 1000
    history_store.record_quota_snapshots([_quota(pct=0.5, reset_ms=wk1)])
    assert history_store.record_quota_snapshots([_quota(pct=0.5, reset_ms=wk2)]) == 1
    rows = history_store.quota_history(provider='ollama', label='7d')
    assert [r['reset_ms'] for r in rows] == [wk1, wk2]
    assert rows[0]['reset_iso'] != rows[1]['reset_iso']


def test_reset_jitter_is_normalized_to_the_minute(hist_db):
    # Real providers align resets to a whole minute and report "now + TTL" with
    # sub-second jitter. Two reads of 14:00:00 that land at :59.98 and :00.39
    # must normalize to the SAME minute, so jitter cannot masquerade as two
    # windows.
    base = 1_791_234_000_000  # 14:00:00.000
    history_store.record_quota_snapshots([_quota(reset_ms=base - 423)])
    history_store.record_quota_snapshots([_quota(reset_ms=base + 386)])
    rows = history_store.quota_history(provider='ollama', label='7d')
    assert rows[0]['reset_ms'] == rows[1]['reset_ms'] == base
    assert rows[0]['reset_iso'] == rows[1]['reset_iso']
    assert rows[0]['reset_ms'] % 60_000 == 0


def test_reset_iso_is_derived_from_normalized_ms(hist_db):
    # reset_iso must match the normalized reset_ms, not the raw provider value,
    # so consumers using either field see the same window identity.
    base = 1_791_234_000_000
    history_store.record_quota_snapshots([_quota(reset_ms=base - 423, reset='2066-01-01T00:00')])
    row = history_store.quota_history(provider='ollama', label='7d')[0]
    assert row['reset_iso'] == history_store.reset_iso_from_ms(base)


def test_providers_are_tracked_independently(hist_db):
    history_store.record_quota_snapshots([_quota(provider='ollama'), _quota(provider='codex')])
    history_store.record_quota_snapshots([_quota(provider='ollama'), _quota(provider='codex', pct=9.0)])
    assert len(history_store.quota_history(provider='ollama')) == 2
    assert len(history_store.quota_history(provider='codex')) == 2


def test_empty_quota_list_writes_nothing(hist_db):
    assert history_store.record_quota_snapshots([]) == 0


def test_usage_daily_upserts_by_date(hist_db):
    row = {
        'date': '2026-10-05',
        'categories': {'deepseek': 100, 'gpt_opencode': 5},
        'total_tokens': 105,
        'cost_usd': 1.5,
        'ai_hours': 2.0,
    }
    history_store.record_usage_daily([row])
    row2 = dict(row, categories={'deepseek': 200, 'gpt_opencode': 5}, total_tokens=205, cost_usd=2.5)
    history_store.record_usage_daily([row2])
    rows = history_store.usage_history()
    assert len(rows) == 1
    assert rows[0]['deepseek'] == 200
    assert rows[0]['total_tokens'] == 205
    assert rows[0]['cost_usd'] == 2.5


def test_quota_history_filters(hist_db):
    history_store.record_quota_snapshots([_quota(provider='ollama', label='5h'), _quota(provider='ollama', label='7d')])
    assert len(history_store.quota_history(provider='ollama')) == 2
    assert len(history_store.quota_history(provider='ollama', label='7d')) == 1
    assert len(history_store.quota_history(provider='codex')) == 0


def test_days_zero_does_not_return_full_history(hist_db):
    # days=0 must mean "nothing in the trailing window", not "no filter"
    # (the bug was `if days:` treating 0 as falsy -> full history).
    history_store.record_quota_snapshots([_quota()], observed_at='2020-01-01T00:00:00')
    history_store.record_usage_daily([
        {'date': '2020-01-01', 'categories': {}, 'total_tokens': 1, 'cost_usd': 0.0, 'ai_hours': 0.0}
    ])
    assert history_store.quota_history(days=0) == []
    assert history_store.usage_history(days=0) == []
    # ...while no filter still returns everything.
    assert len(history_store.quota_history()) == 1
    assert len(history_store.usage_history()) == 1


def test_record_history_hook_persists_quotas_and_daily(hist_db):
    import auto_usage

    payload = {
        'quotas': [_quota(provider='ollama', label='7d', pct=2.0)],
        'daily': [{'date': '2026-10-05', 'categories': {'deepseek': 10}, 'total_tokens': 10, 'cost_usd': 0.1, 'ai_hours': 1.0}],
    }
    auto_usage.record_history(payload)
    assert len(history_store.quota_history(provider='ollama', label='7d')) == 1
    assert [r['date'] for r in history_store.usage_history()] == ['2026-10-05']


def test_record_history_hook_swallows_failures(hist_db, monkeypatch, capsys):
    import auto_usage

    def boom(*args, **kwargs):
        raise RuntimeError('disk on fire')

    monkeypatch.setattr(history_store, 'record_quota_snapshots', boom)
    # Must not raise: a history failure can never break a dashboard run.
    auto_usage.record_history({'quotas': [_quota()], 'daily': []})
    assert 'Quota history record skipped' in capsys.readouterr().err


def test_record_history_quota_failure_does_not_skip_usage(hist_db, monkeypatch):
    import auto_usage

    def boom(*args, **kwargs):
        raise RuntimeError('quota write failed')

    monkeypatch.setattr(history_store, 'record_quota_snapshots', boom)
    auto_usage.record_history({
        'quotas': [_quota()],
        'daily': [{'date': '2026-10-05', 'categories': {'deepseek': 10}, 'total_tokens': 10, 'cost_usd': 0.1, 'ai_hours': 1.0}],
    })
    # The two writes are independent: quota failing must not drop the daily row.
    assert [r['date'] for r in history_store.usage_history()] == ['2026-10-05']


def test_record_history_hook_handles_empty_payload(hist_db):
    import auto_usage

    auto_usage.record_history({'quotas': [], 'daily': []})
    assert history_store.quota_history() == []
    assert history_store.usage_history() == []
