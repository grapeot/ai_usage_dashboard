import sys
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auto_usage import (
    CodexTurnEvent,
    OpencodeTurnMessage,
    build_eink_dashboard_payload,
    build_codex_turn_intervals,
    build_opencode_turn_intervals,
    calc_claude_code_cost,
    classify_opencode_bucket,
    compute_daily_ai_active_seconds,
    date_to_epoch_ms,
    export_cursor,
    format_glm_quota_block,
    generate_dashboard,
    load_claude_code,
    load_claude_code_detailed,
    load_codex,
    load_cursor,
    load_glm,
    load_glm_quota,
    load_opencode,
    load_opencode_detailed,
    load_opencode_from_db,
    load_opencode_turn_intervals,
    merge_daily_tokens,
    merge_intervals,
    normalize_glm_quota,
    parse_ccusage_daily_date,
    split_interval_by_day,
)


def test_classify_opencode_bucket_anthropic_provider():
    assert classify_opencode_bucket('anthropic', 'claude-opus-4-6') == 'anthropic'


def test_classify_opencode_bucket_poe_anthropic_model_maps_to_anthropic():
    assert classify_opencode_bucket('poe', 'anthropic/claude-sonnet-4.6') == 'anthropic'


def test_classify_opencode_bucket_google_provider_maps_to_gemini():
    assert classify_opencode_bucket('google', 'antigravity-gemini-3-flash') == 'gemini'


def test_classify_opencode_bucket_gemini_provider_maps_to_gemini():
    assert classify_opencode_bucket('google', 'gemini-3.1-pro-preview') == 'gemini'


def test_classify_opencode_bucket_gemini_model_maps_to_gemini():
    assert classify_opencode_bucket('custom-provider', 'gemini-3-flash-preview') == 'gemini'


def test_classify_opencode_bucket_deepseek_provider_maps_to_deepseek():
    assert classify_opencode_bucket('deepseek', 'deepseek-chat') == 'deepseek'


def test_classify_opencode_bucket_deepseek_model_maps_to_deepseek():
    assert classify_opencode_bucket('custom-provider', 'deepseek-v4-flash') == 'deepseek'


def test_classify_opencode_bucket_deepseek_v4_pro_model_maps_to_deepseek():
    assert classify_opencode_bucket('ollama-cloud', 'deepseek-v4-pro') == 'deepseek'


def test_classify_opencode_bucket_poe_deepseek_model_maps_to_deepseek():
    assert classify_opencode_bucket('poe', 'deepseek/deepseek-chat') == 'deepseek'


def test_classify_opencode_bucket_openai_provider_maps_to_gpt_opencode():
    assert classify_opencode_bucket('openai', 'gpt-5.4') == 'gpt_opencode'


def test_classify_opencode_bucket_gpt_model_maps_to_gpt_opencode():
    assert classify_opencode_bucket('custom-provider', 'gpt-5.3-codex') == 'gpt_opencode'


def test_classify_opencode_bucket_xai_provider_maps_to_other():
    assert classify_opencode_bucket('xai', 'custom-model') == 'opencode_other'


def test_classify_opencode_bucket_poe_google_model_maps_to_gemini():
    assert classify_opencode_bucket('poe', 'google/gemini-3.1-pro-preview') == 'gemini'


def test_classify_opencode_bucket_poe_xai_model_stays_other():
    assert classify_opencode_bucket('poe', 'xai/grok-4.20-experimental-beta-0304-non-reasoning') == 'opencode_other'


def test_classify_opencode_bucket_grok_model_maps_to_other():
    assert classify_opencode_bucket('custom-provider', 'grok-4.20-experimental-beta-0304-non-reasoning') == 'opencode_other'


def test_classify_opencode_bucket_glm_provider_is_excluded_by_default():
    assert classify_opencode_bucket('zai-coding-plan', 'glm-5') is None


def test_classify_opencode_bucket_ollama_cloud_glm_model_maps_to_glm_opencode():
    assert classify_opencode_bucket('ollama-cloud', 'glm-5.2') == 'glm_opencode'


def test_classify_opencode_bucket_ollama_cloud_glm_model_not_excluded_when_flag_off():
    assert classify_opencode_bucket('ollama-cloud', 'glm-5.2', exclude_glm=False) == 'glm_opencode'


def test_classify_opencode_bucket_custom_provider_glm_model_maps_to_glm_opencode():
    assert classify_opencode_bucket('custom-provider', 'glm-4.7') == 'glm_opencode'


def test_classify_opencode_bucket_unknown_provider_stays_other():
    assert classify_opencode_bucket('mistral', 'mistral-large-2411') == 'opencode_other'


def test_merge_intervals_collapses_overlap():
    intervals = [
        (datetime(2026, 3, 12, 10, 0), datetime(2026, 3, 12, 11, 0)),
        (datetime(2026, 3, 12, 10, 30), datetime(2026, 3, 12, 12, 0)),
        (datetime(2026, 3, 12, 13, 0), datetime(2026, 3, 12, 14, 0)),
    ]
    assert merge_intervals(intervals) == [
        (datetime(2026, 3, 12, 10, 0), datetime(2026, 3, 12, 12, 0)),
        (datetime(2026, 3, 12, 13, 0), datetime(2026, 3, 12, 14, 0)),
    ]


def test_split_interval_by_day_splits_cross_day_interval():
    pieces = split_interval_by_day(
        datetime(2026, 3, 12, 23, 30),
        datetime(2026, 3, 13, 1, 15),
    )
    assert pieces == [
        (date(2026, 3, 12), (datetime(2026, 3, 12, 23, 30), datetime(2026, 3, 13, 0, 0))),
        (date(2026, 3, 13), (datetime(2026, 3, 13, 0, 0), datetime(2026, 3, 13, 1, 15))),
    ]


def test_build_opencode_turn_intervals_uses_user_to_last_assistant():
    messages: list[OpencodeTurnMessage] = [
        {'session_id': 'ses_1', 'time': datetime(2026, 3, 12, 10, 0), 'role': 'user', 'provider_id': '', 'model_id': ''},
        {'session_id': 'ses_1', 'time': datetime(2026, 3, 12, 10, 5), 'role': 'assistant', 'provider_id': 'anthropic', 'model_id': 'claude-sonnet-4.6'},
        {'session_id': 'ses_1', 'time': datetime(2026, 3, 12, 10, 7), 'role': 'assistant', 'provider_id': 'anthropic', 'model_id': 'claude-sonnet-4.6'},
        {'session_id': 'ses_1', 'time': datetime(2026, 3, 12, 10, 10), 'role': 'user', 'provider_id': '', 'model_id': ''},
        {'session_id': 'ses_1', 'time': datetime(2026, 3, 12, 10, 12), 'role': 'assistant', 'provider_id': 'openai', 'model_id': 'gpt-5.4'},
    ]
    assert build_opencode_turn_intervals(messages) == [
        (datetime(2026, 3, 12, 10, 0), datetime(2026, 3, 12, 10, 7)),
        (datetime(2026, 3, 12, 10, 10), datetime(2026, 3, 12, 10, 12)),
    ]


def test_build_opencode_turn_intervals_skips_excluded_glm_assistant():
    messages: list[OpencodeTurnMessage] = [
        {'session_id': 'ses_1', 'time': datetime(2026, 3, 12, 10, 0), 'role': 'user', 'provider_id': '', 'model_id': ''},
        {'session_id': 'ses_1', 'time': datetime(2026, 3, 12, 10, 5), 'role': 'assistant', 'provider_id': 'zai-coding-plan', 'model_id': 'glm-5'},
        {'session_id': 'ses_1', 'time': datetime(2026, 3, 12, 10, 10), 'role': 'user', 'provider_id': '', 'model_id': ''},
        {'session_id': 'ses_1', 'time': datetime(2026, 3, 12, 10, 12), 'role': 'assistant', 'provider_id': 'anthropic', 'model_id': 'claude-sonnet-4.6'},
    ]
    assert build_opencode_turn_intervals(messages) == [
        (datetime(2026, 3, 12, 10, 10), datetime(2026, 3, 12, 10, 12)),
    ]


def test_build_codex_turn_intervals_uses_user_message_to_task_complete():
    events: list[CodexTurnEvent] = [
        {'type': 'event_msg', 'payload_type': 'user_message', 'time': datetime(2026, 3, 12, 9, 0)},
        {'type': 'response_item', 'payload_type': None, 'time': datetime(2026, 3, 12, 9, 1)},
        {'type': 'event_msg', 'payload_type': 'task_complete', 'time': datetime(2026, 3, 12, 9, 4)},
        {'type': 'event_msg', 'payload_type': 'user_message', 'time': datetime(2026, 3, 12, 9, 10)},
        {'type': 'event_msg', 'payload_type': 'task_complete', 'time': datetime(2026, 3, 12, 9, 25)},
    ]
    assert build_codex_turn_intervals(events) == [
        (datetime(2026, 3, 12, 9, 0), datetime(2026, 3, 12, 9, 4)),
        (datetime(2026, 3, 12, 9, 10), datetime(2026, 3, 12, 9, 25)),
    ]


def test_build_codex_turn_intervals_closes_previous_turn_on_next_user_message():
    events: list[CodexTurnEvent] = [
        {'type': 'event_msg', 'payload_type': 'user_message', 'time': datetime(2026, 3, 12, 9, 0)},
        {'type': 'event_msg', 'payload_type': 'user_message', 'time': datetime(2026, 3, 12, 9, 3)},
        {'type': 'event_msg', 'payload_type': 'task_complete', 'time': datetime(2026, 3, 12, 9, 10)},
    ]
    assert build_codex_turn_intervals(events) == [
        (datetime(2026, 3, 12, 9, 0), datetime(2026, 3, 12, 9, 3)),
        (datetime(2026, 3, 12, 9, 3), datetime(2026, 3, 12, 9, 10)),
    ]


def test_build_codex_turn_intervals_falls_back_to_last_event_for_incomplete_turn():
    events: list[CodexTurnEvent] = [
        {'type': 'event_msg', 'payload_type': 'user_message', 'time': datetime(2026, 3, 12, 9, 0)},
        {'type': 'response_item', 'payload_type': None, 'time': datetime(2026, 3, 12, 9, 6)},
    ]
    assert build_codex_turn_intervals(events) == [
        (datetime(2026, 3, 12, 9, 0), datetime(2026, 3, 12, 9, 6)),
    ]


def test_compute_daily_ai_active_seconds_sums_sources_and_splits_days():
    opencode_intervals = [
        (datetime(2026, 3, 12, 10, 0), datetime(2026, 3, 12, 11, 0)),
        (datetime(2026, 3, 12, 23, 30), datetime(2026, 3, 13, 0, 30)),
    ]
    codex_intervals = [
        (datetime(2026, 3, 12, 10, 30), datetime(2026, 3, 12, 12, 0)),
    ]
    result = compute_daily_ai_active_seconds(opencode_intervals, codex_intervals, '2026-03-12', '2026-03-13')
    assert result == {
        date(2026, 3, 12): 10800,
        date(2026, 3, 13): 1800,
    }


def test_generate_dashboard_keeps_est_cost_column_when_daily_costs_is_empty_dict(capsys):
    generate_dashboard(
        cursor={},
        glm={},
        gemini={},
        claude={},
        gpt_opencode={date(2026, 3, 12): 100},
        deepseek={},
        other={},
        start_date='2026-03-12',
        end_date='2026-03-12',
        daily_costs={},
        daily_active_seconds={date(2026, 3, 12): 3600},
    )
    output = capsys.readouterr().out
    assert 'Est. $' in output
    assert 'AI Hours' in output
    assert 'Codex' not in output
    assert '0.00' in output


def test_merge_daily_tokens_combines_codex_into_gpt_display_bucket():
    result = merge_daily_tokens(
        {date(2026, 3, 12): 100, date(2026, 3, 13): 25},
        {date(2026, 3, 12): 50},
    )
    assert result == {
        date(2026, 3, 12): 150,
        date(2026, 3, 13): 25,
    }


def test_build_eink_dashboard_payload_emits_minimal_device_friendly_shape():
    payload = build_eink_dashboard_payload(
        cursor={date(2026, 3, 12): 10},
        glm={},
        gemini={date(2026, 3, 12): 5},
        claude={date(2026, 3, 12): 20},
        gpt_opencode={date(2026, 3, 12): 100},
        deepseek={},
        other={},
        start_date='2026-03-12',
        end_date='2026-03-13',
        daily_costs={date(2026, 3, 12): 1.23, date(2026, 3, 13): 0.0},
        daily_active_seconds={date(2026, 3, 12): 5400, date(2026, 3, 13): 1800},
    )

    meta = cast(dict[str, object], payload['meta'])
    summary = cast(dict[str, object], payload['summary'])
    daily = cast(list[dict[str, object]], payload['daily'])

    assert meta['start_date'] == '2026-03-12'
    assert meta['end_date'] == '2026-03-13'
    assert meta['days'] == 2
    assert summary['total_tokens'] == 135
    assert summary['total_ai_hours'] == 2.0
    assert summary['total_cost_usd'] == 1.23
    assert summary['categories'] == {
        'cursor': 10,
        'glm': 0,
        'gemini': 5,
        'claude': 20,
        'gpt_opencode': 100,
        'deepseek': 0,
        'other': 0,
    }
    assert len(daily) == 2
    assert daily[0] == {
        'date': '2026-03-12',
        'categories': {
            'cursor': 10,
            'glm': 0,
            'gemini': 5,
            'claude': 20,
            'gpt_opencode': 100,
            'deepseek': 0,
            'other': 0,
        },
        'total_tokens': 135,
        'ai_hours': 1.5,
        'cost_usd': 1.23,
    }


def test_load_codex_returns_empty_when_export_missing(tmp_path):
    assert load_codex(tmp_path / 'missing-usage.json') == {}


def test_load_codex_accepts_old_and_new_ccusage_date_formats(tmp_path):
    usage_path = tmp_path / 'usage.json'
    usage_path.write_text(json.dumps({
        'daily': [
            {'date': 'Mar 20, 2026', 'totalTokens': 10},
            {'date': '2026-03-21', 'totalTokens': 20},
        ],
    }))

    assert load_codex(usage_path) == {
        date(2026, 3, 20): 10,
        date(2026, 3, 21): 20,
    }


def test_parse_ccusage_daily_date_accepts_old_and_new_formats():
    assert parse_ccusage_daily_date('Mar 20, 2026') == date(2026, 3, 20)
    assert parse_ccusage_daily_date('2026-03-20') == date(2026, 3, 20)


def test_load_cursor_returns_empty_when_export_missing(tmp_path):
    assert load_cursor(tmp_path / 'missing-cursor.csv') == {}


def test_export_cursor_writes_csv_from_filtered_usage_events(monkeypatch, tmp_path):
    start_ts = date_to_epoch_ms(date(2026, 3, 20))
    first_event_ts = start_ts + 12 * 60 * 60 * 1000
    second_event_ts = first_event_ts + 60 * 1000

    class FakeResponse:
        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self._body

    calls = []

    def fake_post(url, headers=None, json=None):
        calls.append((url, headers, json))
        page = json['page']
        if page == 1:
            return FakeResponse({
                'totalUsageEventsCount': 2,
                'usageEventsDisplay': [
                    {
                        'timestamp': str(first_event_ts),
                        'model': 'composer-2.5-fast',
                        'chargedCents': 8,
                        'requestsCosts': 2,
                        'kind': 'USAGE_EVENT_KIND_INCLUDED_IN_PRO',
                        'tokenUsage': {
                            'inputTokens': 10,
                            'outputTokens': 5,
                            'cacheReadTokens': 100,
                        },
                    },
                    {
                        'timestamp': str(second_event_ts),
                        'model': 'composer-2.5-fast',
                        'tokenUsage': {
                            'inputTokens': 1,
                            'outputTokens': 2,
                            'cacheReadTokens': 3,
                            'cacheWriteTokens': 4,
                            'totalTokens': 20,
                        },
                    },
                ],
            })
        return FakeResponse({'totalUsageEventsCount': 2, 'usageEventsDisplay': []})

    monkeypatch.setattr('auto_usage.SCRIPT_DIR', str(tmp_path))
    monkeypatch.setattr('auto_usage.requests.post', fake_post)

    csv_path = export_cursor('fake-cookie', start_ts, start_ts + 100000)

    assert Path(csv_path).exists()
    assert calls[0][0] == 'https://cursor.com/api/dashboard/get-filtered-usage-events'
    assert calls[0][1]['Cookie'] == 'fake-cookie'
    assert calls[0][2] == {
        'teamId': 0,
        'startDate': str(start_ts),
        'endDate': str(start_ts + 100000),
        'page': 1,
        'pageSize': 100,
    }
    assert load_cursor(csv_path) == {date(2026, 3, 20): 135}


def test_load_glm_accepts_date_only_and_minute_precision_timestamps(tmp_path):
    glm_path = tmp_path / 'glm.json'
    glm_path.write_text(json.dumps({
        'success': True,
        'data': {
            'x_time': ['2026-03-10', '2026-03-10 12:30', '2026-03-11'],
            'tokensUsage': [100, 50, 25],
        }
    }))

    daily = load_glm(glm_path)

    assert daily == {
        date(2026, 3, 10): 150,
        date(2026, 3, 11): 25,
    }


def test_load_glm_skips_empty_or_invalid_timestamps(tmp_path):
    glm_path = tmp_path / 'glm.json'
    glm_path.write_text(json.dumps({
        'success': True,
        'data': {
            'x_time': ['2026-03-10', None, '', 'not-a-time', '2026-03-11 08:45'],
            'tokensUsage': [100, 30, 40, 50, 25],
        }
    }))

    daily = load_glm(glm_path)

    assert daily == {
        date(2026, 3, 10): 100,
        date(2026, 3, 11): 25,
    }


def test_load_claude_code_counts_main_and_subagent_usage_once(tmp_path):
    projects_dir = tmp_path / 'projects'
    session_dir = projects_dir / 'demo-project'
    subagent_dir = session_dir / 'subagents'
    subagent_dir.mkdir(parents=True)

    main_file = session_dir / 'session.jsonl'
    main_file.write_text(
        '\n'.join([
            '{"type":"assistant","timestamp":"2026-03-20T12:00:00Z","requestId":"req-main","message":{"id":"msg-main","model":"claude-opus-4-6","usage":{"input_tokens":10,"cache_creation_input_tokens":20,"cache_read_input_tokens":30,"output_tokens":40}}}',
            '{"type":"assistant","timestamp":"2026-03-20T12:00:01Z","requestId":"req-main","message":{"id":"msg-main","model":"claude-opus-4-6","usage":{"input_tokens":10,"cache_creation_input_tokens":20,"cache_read_input_tokens":30,"output_tokens":40}}}',
        ])
    )
    subagent_file = subagent_dir / 'agent-1.jsonl'
    subagent_file.write_text(
        '{"type":"assistant","timestamp":"2026-03-20T13:00:00Z","requestId":"req-sub","message":{"id":"msg-sub","model":"claude-sonnet-4-6","usage":{"input_tokens":1,"cache_creation_input_tokens":2,"cache_read_input_tokens":3,"output_tokens":4}}}'
    )

    daily = load_claude_code(project_dirs=[projects_dir])
    assert daily == {date(2026, 3, 20): 110}


def test_load_claude_code_detailed_splits_cache_write_1h(tmp_path):
    projects_dir = tmp_path / 'projects'
    projects_dir.mkdir(parents=True)
    session_file = projects_dir / 'session.jsonl'
    session_file.write_text(
        '{"type":"assistant","timestamp":"2026-03-20T12:00:00Z","requestId":"req-1","message":{"id":"msg-1","model":"claude-opus-4-6","usage":{"input_tokens":100,"cache_creation_input_tokens":70,"cache_read_input_tokens":50,"output_tokens":10,"cache_creation":{"ephemeral_5m_input_tokens":30,"ephemeral_1h_input_tokens":40}}}}'
    )

    detailed = load_claude_code_detailed(project_dirs=[projects_dir])
    assert detailed[date(2026, 3, 20)]["claude-opus-4-6"] == {
        'input': 100,
        'output': 10,
        'cache_read': 50,
        'cache_write': 30,
        'cache_write_1h': 40,
    }


def test_load_claude_code_uses_nested_cache_when_flat_field_missing(tmp_path):
    projects_dir = tmp_path / 'projects'
    projects_dir.mkdir(parents=True)
    session_file = projects_dir / 'session.jsonl'
    session_file.write_text(
        '{"type":"assistant","timestamp":"2026-03-20T12:00:00Z","requestId":"req-1","message":{"id":"msg-1","model":"claude-opus-4-6","usage":{"input_tokens":100,"cache_read_input_tokens":50,"output_tokens":10,"cache_creation":{"ephemeral_5m_input_tokens":30,"ephemeral_1h_input_tokens":40}}}}'
    )

    daily = load_claude_code(project_dirs=[projects_dir])
    assert daily == {date(2026, 3, 20): 230}


def test_load_claude_code_dedups_cross_file_when_request_id_missing_on_one_copy(tmp_path):
    projects_dir = tmp_path / 'projects'
    session_dir = projects_dir / 'demo-project'
    subagent_dir = session_dir / 'subagents'
    subagent_dir.mkdir(parents=True)

    main_file = session_dir / 'session.jsonl'
    main_file.write_text(
        '{"type":"assistant","timestamp":"2026-03-20T12:00:00Z","requestId":"req-main","message":{"id":"msg-shared","model":"claude-opus-4-6","usage":{"input_tokens":10,"cache_creation_input_tokens":20,"cache_read_input_tokens":30,"output_tokens":40}}}'
    )
    subagent_file = subagent_dir / 'agent-1.jsonl'
    subagent_file.write_text(
        '{"type":"assistant","timestamp":"2026-03-20T12:00:00Z","message":{"id":"msg-shared","model":"claude-opus-4-6","usage":{"input_tokens":10,"cache_creation_input_tokens":20,"cache_read_input_tokens":30,"output_tokens":40}}}'
    )

    daily = load_claude_code(project_dirs=[projects_dir])
    assert daily == {date(2026, 3, 20): 100}


def test_load_claude_code_ignores_invalid_timestamp_before_dedup(tmp_path):
    projects_dir = tmp_path / 'projects'
    projects_dir.mkdir(parents=True)
    session_file = projects_dir / 'session.jsonl'
    session_file.write_text(
        '\n'.join([
            '{"type":"assistant","timestamp":"not-a-time","requestId":"req-1","message":{"id":"msg-1","model":"claude-opus-4-6","usage":{"input_tokens":10,"cache_creation_input_tokens":20,"cache_read_input_tokens":30,"output_tokens":40}}}',
            '{"type":"assistant","timestamp":"2026-03-20T12:00:00Z","requestId":"req-1","message":{"id":"msg-1","model":"claude-opus-4-6","usage":{"input_tokens":10,"cache_creation_input_tokens":20,"cache_read_input_tokens":30,"output_tokens":40}}}',
        ])
    )

    daily = load_claude_code(project_dirs=[projects_dir])
    assert daily == {date(2026, 3, 20): 100}


def test_load_claude_code_filters_by_date_range(tmp_path):
    projects_dir = tmp_path / 'projects'
    projects_dir.mkdir(parents=True)
    session_file = projects_dir / 'session.jsonl'
    session_file.write_text(
        '\n'.join([
            '{"type":"assistant","timestamp":"2026-03-19T12:00:00Z","requestId":"req-older","message":{"id":"msg-older","model":"claude-opus-4-6","usage":{"input_tokens":10,"cache_creation_input_tokens":20,"cache_read_input_tokens":30,"output_tokens":40}}}',
            '{"type":"assistant","timestamp":"2026-03-20T12:00:00Z","requestId":"req-in-range","message":{"id":"msg-in-range","model":"claude-opus-4-6","usage":{"input_tokens":1,"cache_creation_input_tokens":2,"cache_read_input_tokens":3,"output_tokens":4}}}',
        ])
    )

    daily = load_claude_code(
        project_dirs=[projects_dir],
        start_date=date(2026, 3, 20),
        end_date=date(2026, 3, 20),
    )
    assert daily == {date(2026, 3, 20): 10}


def test_load_opencode_filters_by_time_range(monkeypatch, tmp_path):
    """auto_usage now reads via opencode_skill.query; stub iter_assistant_messages."""
    in_range_ts = date_to_epoch_ms(date(2026, 3, 20)) + 12 * 60 * 60 * 1000
    older_ts = date_to_epoch_ms(date(2026, 3, 19)) + 12 * 60 * 60 * 1000

    fake_messages = [
        SimpleNamespace(
            id='m1', session_id='ses-old', time_created=older_ts,
            provider='anthropic', model='claude-opus-4-6',
            tokens_input=10, tokens_output=5, tokens_reasoning=0,
            tokens_cache_read=0, tokens_cache_write=0, source_db='main',
        ),
        SimpleNamespace(
            id='m2', session_id='ses-new', time_created=in_range_ts,
            provider='anthropic', model='claude-opus-4-6',
            tokens_input=1, tokens_output=2, tokens_reasoning=0,
            tokens_cache_read=3, tokens_cache_write=4, source_db='main',
        ),
    ]

    def fake_iter(since_ms=None, until_ms=None, **kw):
        for m in fake_messages:
            if since_ms is not None and m.time_created < since_ms:
                continue
            if until_ms is not None and m.time_created >= until_ms:
                continue
            yield m

    fake_query_module = SimpleNamespace(iter_assistant_messages=fake_iter)
    fake_package = SimpleNamespace(query=fake_query_module)
    monkeypatch.setitem(sys.modules, 'opencode_skill', fake_package)
    monkeypatch.setitem(sys.modules, 'opencode_skill.query', fake_query_module)

    daily = load_opencode(
        start_ts=date_to_epoch_ms(date(2026, 3, 20)),
        end_ts=date_to_epoch_ms(date(2026, 3, 21)),
    )

    assert daily['anthropic'] == {date(2026, 3, 20): 10}


def test_load_opencode_honors_env_opencode_skill_path(monkeypatch, tmp_path):
    in_range_ts = date_to_epoch_ms(date(2026, 3, 20)) + 12 * 60 * 60 * 1000
    skill_root = tmp_path / 'opencode_skill_src'
    package_dir = skill_root / 'opencode_skill'
    package_dir.mkdir(parents=True)
    (package_dir / '__init__.py').write_text('')
    (package_dir / 'query.py').write_text(
        "from types import SimpleNamespace\n"
        "def iter_assistant_messages(since_ms=None, until_ms=None):\n"
        f"    m = SimpleNamespace(id='m1', session_id='ses', time_created={in_range_ts}, provider='openai', model='gpt-5.4', tokens_input=1, tokens_output=2, tokens_reasoning=3, tokens_cache_read=4, tokens_cache_write=5, source_db='main')\n"
        "    yield m\n"
    )

    monkeypatch.setenv('AI_USAGE_OPENCODE_SKILL_PATH', str(skill_root))
    monkeypatch.delitem(sys.modules, 'opencode_skill', raising=False)
    monkeypatch.delitem(sys.modules, 'opencode_skill.query', raising=False)

    daily = load_opencode(
        start_ts=date_to_epoch_ms(date(2026, 3, 20)),
        end_ts=date_to_epoch_ms(date(2026, 3, 21)),
    )

    assert daily['gpt_opencode'] == {date(2026, 3, 20): 15}


def test_load_opencode_detailed_uses_skill_and_counts_reasoning_as_output(monkeypatch):
    ts = date_to_epoch_ms(date(2026, 3, 20)) + 12 * 60 * 60 * 1000

    def fake_iter(since_ms=None, until_ms=None, **kw):
        yield SimpleNamespace(
            id='m1', session_id='ses', time_created=ts,
            provider='openai', model='gpt-5.4',
            tokens_input=1, tokens_output=2, tokens_reasoning=3,
            tokens_cache_read=4, tokens_cache_write=5, source_db='opencode_archive.db',
        )

    fake_query_module = SimpleNamespace(iter_assistant_messages=fake_iter)
    fake_package = SimpleNamespace(query=fake_query_module)
    monkeypatch.setitem(sys.modules, 'opencode_skill', fake_package)
    monkeypatch.setitem(sys.modules, 'opencode_skill.query', fake_query_module)

    detailed = load_opencode_detailed(
        start_ts=date_to_epoch_ms(date(2026, 3, 20)),
        end_ts=date_to_epoch_ms(date(2026, 3, 21)),
    )

    assert detailed == {
        date(2026, 3, 20): {
            'gpt-5.4': {'input': 1, 'output': 5, 'cache_read': 4, 'cache_write': 5},
        },
    }


def test_load_opencode_falls_back_to_local_db_when_skill_missing(monkeypatch, tmp_path):
    db_path = tmp_path / 'opencode.db'
    conn = sqlite3.connect(db_path)
    conn.execute('CREATE TABLE message (session_id TEXT, time_created INTEGER, data TEXT)')
    in_range_ts = date_to_epoch_ms(date(2026, 3, 20)) + 12 * 60 * 60 * 1000
    conn.execute(
        'INSERT INTO message (session_id, time_created, data) VALUES (?, ?, ?)',
        (
            'ses-1',
            in_range_ts,
            json.dumps({
                'role': 'assistant',
                'providerID': 'openai',
                'modelID': 'gpt-5.4',
                'tokens': {'input': 1, 'output': 2, 'reasoning': 3, 'cache': {'read': 4, 'write': 5}},
                'time': {'created': in_range_ts},
            }),
        ),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr('auto_usage.OPENCODE_DB', db_path)
    monkeypatch.delitem(sys.modules, 'opencode_skill', raising=False)
    monkeypatch.delitem(sys.modules, 'opencode_skill.query', raising=False)
    monkeypatch.setenv('AI_USAGE_OPENCODE_SKILL_PATH', str(tmp_path / 'missing-skill'))

    daily = load_opencode(
        start_ts=date_to_epoch_ms(date(2026, 3, 20)),
        end_ts=date_to_epoch_ms(date(2026, 3, 21)),
    )

    assert daily['gpt_opencode'] == {date(2026, 3, 20): 15}


def test_load_opencode_from_db_returns_empty_when_db_missing(monkeypatch, tmp_path):
    monkeypatch.setattr('auto_usage.OPENCODE_DB', tmp_path / 'missing.db')
    assert load_opencode_from_db() == {
        'anthropic': {},
        'gpt_opencode': {},
        'gemini': {},
        'glm_opencode': {},
        'deepseek': {},
        'opencode_other': {},
    }


def test_load_opencode_turn_intervals_filters_by_time_range(monkeypatch, tmp_path):
    db_path = tmp_path / 'opencode.db'
    conn = sqlite3.connect(db_path)
    conn.execute('CREATE TABLE message (session_id TEXT, time_created INTEGER, data TEXT)')

    old_user_ts = date_to_epoch_ms(date(2026, 3, 19)) + 10 * 60 * 60 * 1000
    old_assistant_ts = old_user_ts + 5 * 60 * 1000
    new_user_ts = date_to_epoch_ms(date(2026, 3, 20)) + 10 * 60 * 60 * 1000
    new_assistant_ts = new_user_ts + 5 * 60 * 1000

    conn.executemany(
        'INSERT INTO message (session_id, time_created, data) VALUES (?, ?, ?)',
        [
            ('ses-old', old_user_ts, '{"role":"user"}'),
            ('ses-old', old_assistant_ts, '{"role":"assistant","providerID":"anthropic","modelID":"claude-sonnet-4-6"}'),
            ('ses-new', new_user_ts, '{"role":"user"}'),
            ('ses-new', new_assistant_ts, '{"role":"assistant","providerID":"anthropic","modelID":"claude-sonnet-4-6"}'),
        ],
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr('auto_usage.OPENCODE_DB', db_path)

    intervals = load_opencode_turn_intervals(
        start_ts=date_to_epoch_ms(date(2026, 3, 20)),
        end_ts=date_to_epoch_ms(date(2026, 3, 21)),
    )

    assert intervals == [
        (datetime(2026, 3, 20, 10, 0), datetime(2026, 3, 20, 10, 5)),
    ]


def test_calc_claude_code_cost_uses_fast_variant_pricing():
    detailed = {
        date(2026, 3, 20): {
            'claude-opus-4-6-fast': {
                'input': 100_000,
                'output': 10_000,
                'cache_read': 50_000,
                'cache_write': 20_000,
                'cache_write_1h': 0,
            }
        }
    }

    result = calc_claude_code_cost(detailed)
    expected = (0.1 * 30.0) + (0.05 * 3.0) + (0.02 * 37.5) + (0.01 * 150.0)
    assert abs(result[date(2026, 3, 20)] - expected) < 0.01


def test_load_claude_code_detailed_normalizes_fast_opus_variant_with_dotted_model_id(tmp_path):
    projects_dir = tmp_path / 'projects'
    projects_dir.mkdir(parents=True)
    session_file = projects_dir / 'session.jsonl'
    session_file.write_text(
        '{"type":"assistant","timestamp":"2026-03-20T12:00:00Z","requestId":"req-fast","message":{"id":"msg-fast","model":"claude-opus-4.6","usage":{"input_tokens":100,"cache_creation_input_tokens":20,"cache_read_input_tokens":10,"output_tokens":5,"speed":"fast"}}}'
    )

    detailed = load_claude_code_detailed(project_dirs=[projects_dir])
    assert detailed[date(2026, 3, 20)]["claude-opus-4-6-fast"] == {
        'input': 100,
        'output': 5,
        'cache_read': 10,
        'cache_write': 20,
        'cache_write_1h': 0,
    }


# --- GLM / Z.ai quota ---

_GLM_QUOTA_SAMPLE = {
    'code': 200,
    'msg': 'Operation successful',
    'success': True,
    'data': {
        'limits': [
            {
                'type': 'TIME_LIMIT',
                'unit': 5,
                'number': 1,
                'usage': 4000,
                'currentValue': 0,
                'remaining': 4000,
                'percentage': 0,
                'nextResetTime': 1784225431994,
                'usageDetails': [
                    {'modelCode': 'search-prime', 'usage': 0},
                    {'modelCode': 'web-reader', 'usage': 0},
                ],
            },
            {
                'type': 'TOKENS_LIMIT',
                'unit': 3,
                'number': 5,
                'percentage': 13,
                'nextResetTime': 1782684009923,
            },
            {
                'type': 'TOKENS_LIMIT',
                'unit': 6,
                'number': 1,
                'percentage': 43,
                'nextResetTime': 1782843031997,
            },
        ],
        'level': 'max',
    },
}


def test_normalize_glm_quota_maps_known_windows_to_labels():
    snapshots = normalize_glm_quota(_GLM_QUOTA_SAMPLE)

    labels = [s['label'] for s in snapshots]
    assert labels == [
        'Monthly Web Search / Reader / Zread Quota',
        '5 Hours Quota',
        'Weekly Quota',
    ]


def test_normalize_glm_quota_converts_reset_timestamps_to_iso():
    snapshots = normalize_glm_quota(_GLM_QUOTA_SAMPLE)

    monthly = snapshots[0]
    assert monthly['next_reset_time_ms'] == 1784225431994
    assert monthly['next_reset_iso'] is not None
    # ISO form ends with seconds precision; epoch ms maps to a deterministic local second.
    assert monthly['next_reset_iso'].endswith(':31')


def test_normalize_glm_quota_reports_absolute_counts_for_monthly_tool_limit():
    snapshots = normalize_glm_quota(_GLM_QUOTA_SAMPLE)

    monthly = snapshots[0]
    assert monthly['type'] == 'TIME_LIMIT'
    assert monthly['unit'] == 5
    assert monthly['usage'] == 4000
    assert monthly['remaining'] == 4000
    assert monthly['current_value'] == 0
    assert monthly['usage_details'] == [
        {'modelCode': 'search-prime', 'usage': 0},
        {'modelCode': 'web-reader', 'usage': 0},
    ]


def test_normalize_glm_quota_leaves_absolute_counts_none_for_token_limits():
    snapshots = normalize_glm_quota(_GLM_QUOTA_SAMPLE)

    five_hour = snapshots[1]
    assert five_hour['type'] == 'TOKENS_LIMIT'
    assert five_hour['unit'] == 3
    assert five_hour['percentage'] == 13
    assert five_hour['usage'] is None
    assert five_hour['remaining'] is None
    assert five_hour['usage_details'] is None


def test_normalize_glm_quota_falls_back_to_generic_label_for_unknown_window():
    body = {
        'success': True,
        'data': {
            'limits': [
                {'type': 'NEW_LIMIT', 'unit': 9, 'percentage': 7, 'nextResetTime': 1782843031997},
            ],
        },
    }

    snapshots = normalize_glm_quota(body)

    assert snapshots[0]['label'] == 'NEW_LIMIT unit=9'
    assert snapshots[0]['percentage'] == 7


def test_normalize_glm_quota_returns_empty_when_response_missing_limits():
    assert normalize_glm_quota({'success': True, 'data': {}}) == []
    assert normalize_glm_quota({'success': False}) == []
    assert normalize_glm_quota({}) == []


def test_normalize_glm_quota_handles_missing_reset_time():
    body = {
        'success': True,
        'data': {'limits': [{'type': 'TOKENS_LIMIT', 'unit': 3, 'percentage': 0}]},
    }

    snapshots = normalize_glm_quota(body)

    assert snapshots[0]['next_reset_time_ms'] is None
    assert snapshots[0]['next_reset_iso'] is None


def test_load_glm_quota_reads_cached_file(tmp_path):
    quota_path = tmp_path / 'glm_quota.json'
    quota_path.write_text(json.dumps(_GLM_QUOTA_SAMPLE))

    snapshots = load_glm_quota(str(quota_path))

    assert len(snapshots) == 3
    assert snapshots[1]['label'] == '5 Hours Quota'


def test_load_glm_quota_returns_empty_when_file_missing(tmp_path):
    assert load_glm_quota(str(tmp_path / 'missing.json')) == []


def test_load_glm_quota_returns_empty_when_file_malformed(tmp_path):
    quota_path = tmp_path / 'glm_quota.json'
    quota_path.write_text(json.dumps({'success': True, 'data': {'limits': 'not-a-list'}}))

    assert load_glm_quota(str(quota_path)) == []


def test_format_glm_quota_block_renders_percentage_and_reset_for_each_window():
    snapshots = normalize_glm_quota(_GLM_QUOTA_SAMPLE)

    block = format_glm_quota_block(snapshots)

    assert 'GLM / Z.ai Coding Plan Quota:' in block
    assert '5 Hours Quota: 13% used' in block
    assert 'Weekly Quota: 43% used' in block
    assert 'Monthly Web Search / Reader / Zread Quota: 0% used' in block
    assert 'used 4000/8000' in block
    assert 'resets ' in block


def test_format_glm_quota_block_returns_empty_string_for_no_snapshots():
    assert format_glm_quota_block([]) == ''


def test_build_eink_dashboard_payload_includes_glm_quota_when_provided():
    snapshots = normalize_glm_quota(_GLM_QUOTA_SAMPLE)

    payload = build_eink_dashboard_payload(
        cursor={},
        glm={},
        gemini={},
        claude={},
        gpt_opencode={},
        deepseek={},
        other={},
        start_date='2026-06-22',
        end_date='2026-06-28',
        glm_quota=snapshots,
    )

    assert 'glm_quota' in payload
    quota = cast(list[dict[str, object]], payload['glm_quota'])
    assert len(quota) == 3
    assert quota[1]['label'] == '5 Hours Quota'


def test_build_eink_dashboard_payload_omits_glm_quota_when_empty():
    payload = build_eink_dashboard_payload(
        cursor={},
        glm={},
        gemini={},
        claude={},
        gpt_opencode={},
        deepseek={},
        other={},
        start_date='2026-06-22',
        end_date='2026-06-28',
        glm_quota=[],
    )

    assert 'glm_quota' not in payload


def test_generate_dashboard_prints_quota_block(capsys):
    snapshots = normalize_glm_quota(_GLM_QUOTA_SAMPLE)

    generate_dashboard(
        cursor={date(2026, 6, 22): 0},
        glm={},
        gemini={},
        claude={},
        gpt_opencode={},
        deepseek={},
        other={},
        start_date='2026-06-22',
        end_date='2026-06-22',
        daily_costs=None,
        daily_active_seconds=None,
        skip_desktop_chart=True,
        glm_quota=snapshots,
    )

    out = capsys.readouterr().out
    assert 'GLM / Z.ai Coding Plan Quota:' in out
    assert '5 Hours Quota: 13% used' in out
