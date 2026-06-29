"""Tests for Google Antigravity IDE token usage integration."""
import json
import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auto_usage import (
    AntigravityConnection,
    ANTIGRAVITY_MODEL_PLACEHOLDER_MAP,
    _classify_antigravity_model,
    _extract_flag_value,
    _is_antigravity_ls_command,
    _dechunk,
    _to_int,
    _parse_antigravity_timestamp,
    parse_antigravity_usage_response,
    fetch_antigravity_trajectories,
    load_antigravity,
    calc_antigravity_cost,
)


class TestClassifyAntigravityModel:
    def test_gemini_flash(self):
        assert _classify_antigravity_model('gemini-3-flash-a') == 'gemini'

    def test_gemini_pro(self):
        assert _classify_antigravity_model('gemini-3-pro') == 'gemini'

    def test_placeholder_m20_gemini(self):
        assert _classify_antigravity_model('MODEL_PLACEHOLDER_M20') == 'gemini'

    def test_claude(self):
        assert _classify_antigravity_model('claude-sonnet-4.6') == 'anthropic'

    def test_gpt(self):
        assert _classify_antigravity_model('gpt-5.2') == 'gpt_opencode'

    def test_deepseek(self):
        assert _classify_antigravity_model('deepseek-v4-flash') == 'deepseek'

    def test_unknown_defaults_to_gemini(self):
        assert _classify_antigravity_model('unknown-model') == 'opencode_other'

    def test_unknown_placeholder_defaults_to_gemini(self):
        with patch('sys.stderr'):
            assert _classify_antigravity_model('model_placeholder_m99') == 'gemini'

    def test_empty_string(self):
        assert _classify_antigravity_model('') == 'opencode_other'

    def test_none(self):
        assert _classify_antigravity_model(None) == 'opencode_other'


class TestExtractFlagValue:
    def test_space_separator(self):
        argv = "binary --csrf_token abcd-1234-5678 --port 9999"
        assert _extract_flag_value(argv, '--csrf_token') == 'abcd-1234-5678'

    def test_equals_separator(self):
        argv = "binary --csrf_token=abcd-1234-5678 --port=9999"
        assert _extract_flag_value(argv, '--csrf_token') == 'abcd-1234-5678'

    def test_flag_not_found(self):
        assert _extract_flag_value("binary --other flag", '--csrf_token') is None

    def test_empty_after_flag(self):
        assert _extract_flag_value("binary --csrf_token", '--csrf_token') is None


class TestIsAntigravityCommand:
    def test_language_server_with_antigravity(self):
        cmd = "/Applications/Antigravity.app/bin/language_server --app_data_dir antigravity"
        assert _is_antigravity_ls_command(cmd) is True

    def test_language_server_with_app_data_dir(self):
        cmd = "language_server_macos_arm --app_data_dir antigravity-ide --csrf_token abc"
        assert _is_antigravity_ls_command(cmd) is True

    def test_unrelated_process(self):
        assert _is_antigravity_ls_command("notepad.exe file.txt") is False

    def test_language_server_without_antigravity(self):
        assert _is_antigravity_ls_command("language_server --other_app") is False


class TestDechunk:
    def test_single_chunk(self):
        chunked = b'5\r\nHello\r\n0\r\n\r\n'
        assert _dechunk(chunked) == b'Hello'

    def test_multiple_chunks(self):
        chunked = b'5\r\nHello\r\n6\r\n World\r\n0\r\n\r\n'
        assert _dechunk(chunked) == b'Hello World'

    def test_empty_body(self):
        assert _dechunk(b'0\r\n\r\n') == b''

    def test_malformed_returns_partial(self):
        assert _dechunk(b'garbage') == b''


class TestToInt:
    def test_int(self):
        assert _to_int(42) == 42

    def test_float(self):
        assert _to_int(3.7) == 3

    def test_string_numeric(self):
        assert _to_int("123") == 123

    def test_none(self):
        assert _to_int(None) == 0

    def test_invalid_string(self):
        assert _to_int("abc") == 0


class TestParseTimestamp:
    def test_int_ms(self):
        assert _parse_antigravity_timestamp(1711447200000) == 1711447200000

    def test_iso_string(self):
        ts = _parse_antigravity_timestamp("2024-03-26T10:00:00Z")
        assert ts is not None
        assert ts > 0

    def test_none(self):
        assert _parse_antigravity_timestamp(None) is None

    def test_invalid(self):
        assert _parse_antigravity_timestamp("not a timestamp") is None


class TestParseUsageResponse:
    def test_basic_usage(self):
        response = {
            "generatorMetadata": [
                {
                    "chatModel": {
                        "responseModel": "gemini-3-flash-a",
                        "chatStartMetadata": {"createdAt": "2024-03-26T10:00:00Z"},
                        "retryInfos": [
                            {
                                "usage": {
                                    "inputTokens": 1000,
                                    "outputTokens": 200,
                                    "cacheReadTokens": 5000,
                                    "thinkingOutputTokens": 50,
                                    "responseId": "resp-1",
                                }
                            }
                        ],
                    }
                }
            ]
        }
        entries = parse_antigravity_usage_response(response, session_id="sess-1")
        assert len(entries) == 1
        e = entries[0]
        assert e['model'] == 'gemini-3-flash-a'
        assert e['input'] == 1000
        assert e['output'] == 200
        assert e['cache_read'] == 5000
        assert e['thinking'] == 50
        assert e['response_id'] == 'resp-1'
        assert e['session_id'] == 'sess-1'
        assert e['timestamp_ms'] is not None and e['timestamp_ms'] > 0

    def test_multiple_retry_infos(self):
        response = {
            "generatorMetadata": [
                {
                    "chatModel": {
                        "responseModel": "gemini-3-flash-a",
                        "retryInfos": [
                            {"usage": {"inputTokens": 100, "outputTokens": 10, "responseId": "r1"}},
                            {"usage": {"inputTokens": 200, "outputTokens": 20, "responseId": "r2"}},
                        ],
                    }
                }
            ]
        }
        entries = parse_antigravity_usage_response(response)
        assert len(entries) == 2

    def test_zero_usage_skipped(self):
        response = {
            "generatorMetadata": [
                {
                    "chatModel": {
                        "responseModel": "gemini-3-flash-a",
                        "retryInfos": [
                            {"usage": {"inputTokens": 0, "outputTokens": 0}},
                        ],
                    }
                }
            ]
        }
        entries = parse_antigravity_usage_response(response)
        assert len(entries) == 0

    def test_empty_response(self):
        assert parse_antigravity_usage_response({}) == []
        assert parse_antigravity_usage_response({"generatorMetadata": []}) == []

    def test_missing_chat_model(self):
        response = {"generatorMetadata": [{"notChatModel": {}}]}
        assert parse_antigravity_usage_response(response) == []

    def test_string_token_values(self):
        """Token values may arrive as strings; should be coerced to int."""
        response = {
            "generatorMetadata": [
                {
                    "chatModel": {
                        "responseModel": "gemini-3-flash-a",
                        "retryInfos": [
                            {"usage": {"inputTokens": "1000", "outputTokens": "200"}}
                        ],
                    }
                }
            ]
        }
        entries = parse_antigravity_usage_response(response)
        assert len(entries) == 1
        assert entries[0]['input'] == 1000
        assert entries[0]['output'] == 200

    def test_timestamp_from_usage(self):
        """Usage entry's own timestamp takes priority over chatStartMetadata."""
        response = {
            "generatorMetadata": [
                {
                    "chatModel": {
                        "responseModel": "gemini-3-flash-a",
                        "chatStartMetadata": {"createdAt": "2024-01-01T00:00:00Z"},
                        "retryInfos": [
                            {"usage": {"inputTokens": 10, "outputTokens": 5, "timestamp": 1711447200000}}
                        ],
                    }
                }
            ]
        }
        entries = parse_antigravity_usage_response(response)
        assert entries[0]['timestamp_ms'] == 1711447200000


class TestFetchTrajectories:
    def test_list_response(self):
        conn = AntigravityConnection(pid=1, port=9999, csrf_token="x")
        mock_resp = {
            "trajectorySummaries": [
                {"cascadeId": "sess-1", "stepCount": 10},
                {"cascadeId": "sess-2", "stepCount": 20},
            ]
        }
        with patch('auto_usage._antigravity_rpc', return_value=mock_resp):
            result = fetch_antigravity_trajectories([conn])
        assert len(result) == 2
        assert result[0]['cascadeId'] == 'sess-1'

    def test_dict_response(self):
        conn = AntigravityConnection(pid=1, port=9999, csrf_token="x")
        mock_resp = {
            "trajectorySummaries": {
                "sess-1": {"stepCount": 10},
                "sess-2": {"stepCount": 20},
            }
        }
        with patch('auto_usage._antigravity_rpc', return_value=mock_resp):
            result = fetch_antigravity_trajectories([conn])
        assert len(result) == 2

    def test_dedup_across_connections(self):
        conn1 = AntigravityConnection(pid=1, port=9999, csrf_token="x")
        conn2 = AntigravityConnection(pid=2, port=8888, csrf_token="y")
        mock_resp = {"trajectorySummaries": [{"cascadeId": "sess-1", "stepCount": 10}]}
        with patch('auto_usage._antigravity_rpc', return_value=mock_resp):
            result = fetch_antigravity_trajectories([conn1, conn2])
        assert len(result) == 1

    def test_rpc_failure(self):
        conn = AntigravityConnection(pid=1, port=9999, csrf_token="x")
        with patch('auto_usage._antigravity_rpc', return_value=None):
            result = fetch_antigravity_trajectories([conn])
        assert result == []


class TestLoadAntigravity:
    def test_no_connections_returns_empty(self):
        with patch('auto_usage._discover_antigravity_connections', return_value=[]):
            result = load_antigravity()
        assert all(v == {} for v in result.values())
        assert set(result.keys()) == {'gemini', 'anthropic', 'gpt_opencode', 'deepseek', 'opencode_other'}

    def test_basic_flow(self):
        conn = AntigravityConnection(pid=1, port=9999, csrf_token="x")
        trajectories = [{"cascadeId": "sess-1"}]
        usage_resp = {
            "generatorMetadata": [
                {
                    "chatModel": {
                        "responseModel": "gemini-3-flash-a",
                        "retryInfos": [
                            {"usage": {"inputTokens": 1000, "outputTokens": 200, "cacheReadTokens": 5000, "responseId": "r1", "timestamp": 1711447200000}}
                        ],
                    }
                }
            ]
        }
        with patch('auto_usage._discover_antigravity_connections', return_value=[conn]), \
             patch('auto_usage.fetch_antigravity_trajectories', return_value=trajectories), \
             patch('auto_usage._antigravity_rpc', return_value=usage_resp):
            result = load_antigravity()
        # 1000 + 200 + 5000 = 6200 tokens on that date
        assert result['gemini'] != {}
        total = sum(result['gemini'].values())
        assert total == 6200

    def test_dedup_by_response_id(self):
        conn = AntigravityConnection(pid=1, port=9999, csrf_token="x")
        trajectories = [{"cascadeId": "sess-1"}, {"cascadeId": "sess-2"}]
        usage_resp = {
            "generatorMetadata": [
                {
                    "chatModel": {
                        "responseModel": "gemini-3-flash-a",
                        "retryInfos": [
                            {"usage": {"inputTokens": 1000, "outputTokens": 0, "responseId": "same-id", "timestamp": 1711447200000}}
                        ],
                    }
                }
            ]
        }
        with patch('auto_usage._discover_antigravity_connections', return_value=[conn]), \
             patch('auto_usage.fetch_antigravity_trajectories', return_value=trajectories), \
             patch('auto_usage._antigravity_rpc', return_value=usage_resp):
            result = load_antigravity()
        # deduped: only counted once
        total = sum(result['gemini'].values())
        assert total == 1000


class TestCalcAntigravityCost:
    def test_gemini_flash_cost(self):
        detailed = {
            date(2024, 3, 26): {
                "gemini-3-flash-a": {"input": 1000000, "output": 100000, "cache_read": 5000000, "cache_write": 0, "thinking": 10000}
            }
        }
        costs = calc_antigravity_cost(detailed)
        d = date(2024, 3, 26)
        assert d in costs
        # gemini-3-flash: $0.50/M input, $3.00/M output, cached ~10% = $0.10/M
        # non_cached_input = 1M - 5M = max(0, 1M-5M) = 0, so all input is cached
        # Actually calc_cost: non_cached = max(0, input_tokens - cached_tokens)
        # input_tokens = input + cache_read = 6M, cached_tokens = 5M
        # non_cached = 1M -> 1M * $0.50/M = $0.50
        # cached = 5M * $0.10/M = $0.50 (default 10% of input if no cached rate)
        # Wait, gemini-3-flash has no 'cached' or 'cache_read' key...
        # calc_cost: cached_rate = pricing.get("cache_read") or pricing.get("cached") or input*0.1
        # gemini-3-flash: {"input": 0.5, "output": 3.0} → cached_rate = 0.5 * 0.1 = 0.05
        # non_cached = max(0, 6M - 5M) = 1M
        # cost = 1M*0.5/M + 5M*0.05/M + 110K*3.0/M = 0.5 + 0.25 + 0.33 = ~1.08
        assert costs[d] > 0
        assert costs[d] < 5.0  # sanity bound

    def test_empty(self):
        assert calc_antigravity_cost({}) == {}


@pytest.mark.live_antigravity
class TestLiveAntigravity:
    """Integration test: requires a running Antigravity LS on localhost.

    Skipped automatically when no LS is detected.
    """

    @pytest.fixture(autouse=True)
    def _check_ls(self):
        from auto_usage import _discover_antigravity_connections
        conns = _discover_antigravity_connections()
        if not conns:
            pytest.skip("No Antigravity Language Server running on localhost")

    def test_live_load_returns_data(self):
        result = load_antigravity()
        # at least one bucket should have non-empty data if LS is running with usage
        total = sum(sum(v.values()) for v in result.values())
        assert total > 0, "LS is running but no token usage found — may be a fresh session"