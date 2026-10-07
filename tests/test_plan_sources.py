from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

import plan_sources
from window_usage import UsageRecord


def record(source='opencode', billing='openai', model='gpt-5.4', **kwargs):
    return UsageRecord(time=datetime(2024, 1, 5, 12, tzinfo=timezone.utc), provider='gpt_opencode',
                       source=source, billing_provider=billing, model=model, **kwargs)


def test_client_identity_does_not_determine_subscription_pool():
    auth = {'openai': {'type': 'oauth'}, 'xai': {'type': 'oauth'}, 'anthropic': {'type': 'api', 'key': 'fake-paid-key'}}
    assert plan_sources.route_plan(record(), auth) == 'codex'
    assert plan_sources.route_plan(record(source='codex', billing='ollama-cloud', model='glm-5.3-flash'), auth) == 'ollama'
    assert plan_sources.route_plan(record(source='codex'), auth, codex_subscription=False) is None
    assert plan_sources.route_plan(record(billing='anthropic', model='claude-opus-4.6'), auth) is None
    assert plan_sources.route_plan(record(billing='xai', model='grok-4.7'), auth) == 'grok'
    assert plan_sources.route_plan(record(source='cursor', billing='cursor', model='grok-4.7', plan_included=True), auth) == 'cursor'
    assert plan_sources.route_plan(record(source='claude', billing='anthropic', model='glm-5.3'), auth, claude_subscription=True) is None


@pytest.mark.parametrize('hour,day,factor', [(11, 5, 0.5), (12, 5, 1), (17, 5, 1), (18, 5, 0.5), (13, 6, 0.5)])
def test_ollama_peak_schedule_uses_utc_and_weekends(hour, day, factor):
    r = record(billing='ollama-cloud', model='deepseek-v4.1-flash', input_non_cached=1_000_000,
               input_cached=1_000_000, output=1_000_000)
    r.time = datetime(2024, 1, day, hour, tzinfo=timezone.utc)
    assert plan_sources.price_record('ollama', r) == pytest.approx(1.506 * factor)


def test_unknown_or_unspecified_prices_are_not_free_usage():
    assert plan_sources.price_record('ollama', record(model='unknown-model')) is None
    assert plan_sources.price_record('ollama', record(model='nemotron-3-nano', input_cached=100)) is None
    assert plan_sources.price_record('cursor', record(source='cursor')) is None


def test_cursor_paid_overage_does_not_consume_included_plan():
    assert plan_sources.route_plan(record(source='cursor', billing='cursor', plan_included=False), {}) is None
    assert plan_sources.route_plan(record(source='cursor', billing='cursor', plan_included=None), {}) is None


def test_collect_usage_is_local_and_deduplicates_archive_copies(tmp_path, monkeypatch):
    auth_dir = tmp_path / '.local/share/opencode'
    auth_dir.mkdir(parents=True)
    (auth_dir / 'auth.json').write_text(json.dumps({'openai': {'type': 'oauth'}}))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    r = record(input_non_cached=100, record_id='fake-message')
    monkeypatch.setitem(plan_sources.SOURCES, 'opencode', lambda start, end: iter([r, r]))
    monkeypatch.setitem(plan_sources.SOURCES, 'codex', lambda start, end: iter(()))
    def no_network(*args, **kwargs):
        raise AssertionError('plan analysis must not fetch providers')
    monkeypatch.setattr(plan_sources.dashboard.requests, 'get', no_network)
    events, diagnostics = plan_sources.collect_usage({'codex'}, datetime(2024, 1, 5, tzinfo=timezone.utc), datetime(2024, 1, 6, tzinfo=timezone.utc))
    assert len(events) == 1
    assert events[0].plan == 'codex'
    assert diagnostics['excluded_records']['duplicate_records'] == 1


def test_native_grok_future_activity_does_not_invalidate_a_past_interval(tmp_path):
    session = tmp_path / 'project/session'
    session.mkdir(parents=True)
    log = session / 'updates.jsonl'
    def item(hour):
        return {'params': {'_meta': {'agentTimestampMs': int(datetime(2024, 1, 5, hour, tzinfo=timezone.utc).timestamp() * 1000)},
                           'update': {'sessionUpdate': 'agent_message_chunk'}}}
    start = datetime(2024, 1, 5, tzinfo=timezone.utc)
    end = datetime(2024, 1, 5, 12, tzinfo=timezone.utc)
    log.write_text(json.dumps(item(13)))
    assert not plan_sources._native_grok_activity(tmp_path, start, end)
    log.write_text(json.dumps(item(11)))
    assert plan_sources._native_grok_activity(tmp_path, start, end)
