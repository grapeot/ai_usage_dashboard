from datetime import datetime, timezone
import json
import os
import sqlite3

from fastapi.testclient import TestClient
import pytest

import auto_usage
import history_store
import local_display_service
import quota_capture
from eink_simulator import parse_dashboard_payload
from firmware_logic import quota_bar_fill_width


def test_fractional_quota_survives_parser_history_api_and_display_boundary(tmp_path, monkeypatch):
    raw = '<span>11.9% used</span><div data-time="2024-01-08T00:00:00Z"></div>'
    snapshots = quota_capture.capture(auto_usage.normalize_ollama_quota(raw), observed_at='2024-01-01T12:00:00+00:00')
    path = str(tmp_path / 'history.db')
    history_store.record_quota_snapshots(snapshots, path=path)
    row = history_store.read_quota_samples(path=path)[0]
    assert row['percentage'] == 11.9
    assert row['percentage_resolution'] == 0.1
    assert row['observed_at'] == '2024-01-01T12:00:00+00:00'
    assert row['recorded_at'] != row['observed_at']
    monkeypatch.setattr(local_display_service, '_cached_payload', {'meta': {}, 'quotas': snapshots})
    body = TestClient(local_display_service.app).get('/api/v1/quotas').json()['quotas'][0]
    assert body['used_percentage'] == 11.9
    assert body['remaining_percentage'] == pytest.approx(88.1)
    assert body['observed_at'] == row['observed_at']
    display = parse_dashboard_payload({'meta': {}, 'summary': {}, 'daily': [], 'quotas': snapshots})
    assert display.quotas[0].percentage == 11.9
    assert quota_bar_fill_width(100, display.quotas[0].percentage) == 12


def test_history_migration_preserves_legacy_integer_readings(tmp_path):
    path = str(tmp_path / 'old.db')
    with sqlite3.connect(path) as conn:
        conn.executescript(history_store._SCHEMA)
        conn.execute("INSERT INTO quota_samples(observed_at,provider,label,percentage) VALUES ('2024-01-01','ollama','7d',12)")
    before = (tmp_path / 'old.db').stat().st_mtime_ns
    rows = history_store.read_quota_samples(path=path)
    assert rows[0]['percentage'] == 12
    assert 'recorded_at' not in rows[0]
    assert (tmp_path / 'old.db').stat().st_mtime_ns == before
    history_store.record_quota_snapshots([{'provider': 'ollama', 'label': '7d', 'percentage': 12.6,
                                          'percentage_resolution': 0.1}], path=path)
    rows = history_store.read_quota_samples(path=path)
    assert rows[0]['percentage'] == 12
    assert rows[0]['percentage_resolution'] is None
    assert rows[1]['percentage'] == 12.6
    assert rows[1]['percentage_resolution'] == 0.1


def test_history_api_reads_do_not_migrate_or_create_tables(tmp_path):
    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as conn:
        conn.executescript(history_store._SCHEMA)
        conn.execute("INSERT INTO quota_samples(observed_at,provider,label,percentage) VALUES ('2024-01-01','ollama','7d',12)")
    assert history_store.quota_history(path=str(path))[0]['percentage'] == 12
    assert history_store.usage_history(path=str(path)) == []
    with sqlite3.connect(path) as conn:
        assert 'recorded_at' not in {r[1] for r in conn.execute('PRAGMA table_info(quota_samples)')}


def test_history_order_handles_different_explicit_time_zones(tmp_path):
    path = str(tmp_path / 'history.db')
    history_store.record_quota_snapshots([{'provider': 'ollama', 'label': '7d', 'percentage': 1}],
                                       observed_at='2024-01-01T09:00:00+02:00', path=path)
    history_store.record_quota_snapshots([{'provider': 'ollama', 'label': '7d', 'percentage': 2}],
                                       observed_at='2024-01-01T08:00:00+00:00', path=path)
    assert [r['percentage'] for r in history_store.quota_history(path=path)] == [1, 2]


def test_read_missing_history_does_not_create_it(tmp_path):
    path = tmp_path / 'missing.db'
    assert history_store.read_quota_samples(path=str(path)) == []
    assert not path.exists()


def test_claude_small_percentage_is_not_guessed_to_be_a_ratio():
    assert auto_usage._normalize_usage_percentage(0.7) == 0.7
    assert auto_usage._normalize_usage_percentage(0.007, unit='ratio') == 0.7
    assert quota_capture.fraction_used(0.925) == 7.5


def test_account_metadata_is_opaque_not_a_token_or_raw_identity():
    assert quota_capture.openai_account_fingerprint({'accountId': 'synthetic-account'}) == quota_capture.account_fingerprint('openai:synthetic-account')
    assert quota_capture.openai_account_fingerprint({'access': 'not-a-jwt'}) is None


def test_metadata_includes_unrounded_model_readings(tmp_path):
    models = [{'model_id': 'fake-model', 'label': 'Example', 'percentage': 12.345, 'remaining_fraction': 0.87655}]
    sample = {'provider': 'antigravity', 'label': 'Gemini 5h', 'percentage': 12.345,
              'quota_scope': 'family_max', 'model_quotas': models}
    path = str(tmp_path / 'history.db')
    history_store.record_quota_snapshots([sample], path=path)
    assert history_store.read_quota_samples(path=path)[0]['model_quotas'] == models


def test_cached_capture_keeps_original_time_instead_of_manufacturing_a_fresh_read(monkeypatch, tmp_path):
    monkeypatch.setattr(auto_usage, 'SCRIPT_DIR', str(tmp_path))
    file = tmp_path / 'ollama_settings.html'
    file.write_text('<span>2.5% used</span>')
    original_capture = datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(file, (original_capture, original_capture))
    for key in ('GLM_BEARER_TOKEN', 'CURSOR_COOKIE', 'GROK_COOKIE'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('OLLAMA_COOKIE', 'fake-cookie')
    def unavailable(*args, **kwargs):
        raise RuntimeError('offline test')
    monkeypatch.setattr(auto_usage, 'export_ollama_quota', unavailable)
    monkeypatch.setattr(auto_usage, 'load_glm_quota', lambda: [])
    monkeypatch.setattr(auto_usage, 'load_codex_quota', lambda: [])
    monkeypatch.setattr(auto_usage, 'export_claude_code_quota', lambda: [])
    monkeypatch.setattr(auto_usage, 'export_antigravity_quota', lambda: [])
    _, snapshots = auto_usage.collect_quotas(verbose=False)
    assert snapshots[0]['measurement_source'] == 'cache'
    assert datetime.fromisoformat(snapshots[0]['observed_at']).timestamp() == pytest.approx(original_capture, rel=0, abs=1e-6)
