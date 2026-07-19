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
    _antigravity_model_family,
    _entry_to_date,
    _entry_total,
    parse_antigravity_usage_response,
    fetch_antigravity_trajectories,
    load_antigravity,
    calc_antigravity_cost,
    export_antigravity_quota,
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
        with patch('auto_usage._discover_antigravity_connections', return_value=[]), \
             patch('auto_usage._load_antigravity_cache', return_value=[]), \
             patch('auto_usage._save_antigravity_cache'):
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
             patch('auto_usage._antigravity_rpc', return_value=usage_resp), \
             patch('auto_usage._load_antigravity_cache', return_value=[]), \
             patch('auto_usage._save_antigravity_cache'):
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
             patch('auto_usage._antigravity_rpc', return_value=usage_resp), \
             patch('auto_usage._load_antigravity_cache', return_value=[]), \
             patch('auto_usage._save_antigravity_cache'):
            result = load_antigravity()
        # deduped: only counted once
        total = sum(result['gemini'].values())
        assert total == 1000

    def test_cache_provides_data_when_ls_offline(self):
        """When no LS is running, cached entries should still provide token data."""
        cached = [
            {"model": "gemini-3-flash-a", "timestamp": 1711447200000,
             "input": 5000, "output": 500, "cache_read": 10000,
             "cache_write": 0, "thinking": 100, "response_id": "cached-1",
             "session_id": "old-session"},
        ]
        with patch('auto_usage._discover_antigravity_connections', return_value=[]), \
             patch('auto_usage._load_antigravity_cache', return_value=cached), \
             patch('auto_usage._save_antigravity_cache'):
            result = load_antigravity()
        total = sum(result['gemini'].values())
        assert total == 15600  # 5000 + 500 + 10000 + 100

    def test_cache_and_live_merged_with_dedup(self):
        """Cache + live data should merge, with response_id dedup."""
        cached = [
            {"model": "gemini-3-flash-a", "timestamp": 1711447200000,
             "input": 1000, "output": 0, "cache_read": 0,
             "cache_write": 0, "thinking": 0, "response_id": "shared-id",
             "session_id": "old"},
        ]
        conn = AntigravityConnection(pid=1, port=9999, csrf_token="x")
        trajectories = [{"cascadeId": "sess-1"}]
        usage_resp = {
            "generatorMetadata": [
                {
                    "chatModel": {
                        "responseModel": "gemini-3-flash-a",
                        "retryInfos": [
                            {"usage": {"inputTokens": 1000, "outputTokens": 0, "responseId": "shared-id", "timestamp": 1711447200000}},
                            {"usage": {"inputTokens": 2000, "outputTokens": 0, "responseId": "new-id", "timestamp": 1711447200000}},
                        ],
                    }
                }
            ]
        }
        with patch('auto_usage._discover_antigravity_connections', return_value=[conn]), \
             patch('auto_usage.fetch_antigravity_trajectories', return_value=trajectories), \
             patch('auto_usage._antigravity_rpc', return_value=usage_resp), \
             patch('auto_usage._load_antigravity_cache', return_value=cached), \
             patch('auto_usage._save_antigravity_cache') as mock_save:
            result = load_antigravity()
        # shared-id deduped (1000 counted once), new-id added (2000)
        total = sum(result['gemini'].values())
        assert total == 3000
        # verify cache was written with merged data
        mock_save.assert_called_once()
        saved_entries = mock_save.call_args[0][0]
        saved_rids = {e['response_id'] for e in saved_entries if e.get('response_id')}
        assert 'shared-id' in saved_rids
        assert 'new-id' in saved_rids

    def test_load_missing_cascades_from_disk_db(self):
        conn = AntigravityConnection(pid=1, port=9999, csrf_token="x")
        mock_resp = {
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
        
        # Mock sqlite3 connection
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        # First call is sqlite_master table check -> returns ('gen_metadata',)
        mock_cursor.fetchone.return_value = ('gen_metadata',)
        # Second call is SELECT idx -> returns list of indices
        mock_cursor.fetchall.return_value = [(0,), (1,)]
        mock_conn.cursor.return_value = mock_cursor
        
        with patch('os.path.exists', return_value=True), \
             patch('glob.glob', return_value=['/mock/conversations/12345-uuid.db']), \
             patch('sqlite3.connect', return_value=mock_conn), \
             patch('auto_usage._discover_antigravity_connections', return_value=[conn]), \
             patch('auto_usage.fetch_antigravity_trajectories', return_value=[]), \
             patch('auto_usage._antigravity_rpc', return_value=mock_resp) as mock_rpc, \
             patch('auto_usage._load_antigravity_cache', return_value=[]), \
             patch('auto_usage._save_antigravity_cache'), \
             patch('auto_usage._load_antigravity_sync_metadata', return_value={}), \
             patch('auto_usage._save_antigravity_sync_metadata'):
            result = load_antigravity()
            
        # Verify that we queried the LS for the cascade ID from the disk database
        mock_rpc.assert_called_with(conn, 'GetCascadeTrajectoryGeneratorMetadata', {'cascadeId': '12345-uuid'})
        assert result['gemini'] != {}
        total = sum(result['gemini'].values())
        assert total == 6200

    def test_load_missing_cascades_subset_mismatch_regression(self):
        """Test case where cache count equals DB count due to unit mismatch (retry vs generation),
        but there is an actual missing generation on disk.
        """
        conn = AntigravityConnection(pid=1, port=9999, csrf_token="x")
        
        # Cache has 2 entries, but both belong to gen_idx=0 (retries for same generation)
        cached = [
            {"model": "gemini-3-flash-a", "timestamp": 1711447200000,
             "input": 1000, "output": 0, "cache_read": 0, "response_id": "r1",
             "session_id": "sess-1", "gen_idx": 0},
            {"model": "gemini-3-flash-a", "timestamp": 1711447200000,
             "input": 1000, "output": 0, "cache_read": 0, "response_id": "r2",
             "session_id": "sess-1", "gen_idx": 0},
        ]
        
        # SQLite DB has idx values {0, 1} (2 generations, gen 1 is missing from cache)
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ('gen_metadata',)
        mock_cursor.fetchall.return_value = [(0,), (1,)]
        mock_conn.cursor.return_value = mock_cursor
        
        # LS returns both generations (0 and 1)
        mock_resp = {
            "generatorMetadata": [
                {
                    "chatModel": {
                        "responseModel": "gemini-3-flash-a",
                        "retryInfos": [
                            {"usage": {"inputTokens": 1000, "responseId": "r1", "timestamp": 1711447200000}},
                            {"usage": {"inputTokens": 1000, "responseId": "r2", "timestamp": 1711447200000}},
                        ],
                    }
                },
                {
                    "chatModel": {
                        "responseModel": "gemini-3-flash-a",
                        "retryInfos": [
                            {"usage": {"inputTokens": 5000, "responseId": "r3", "timestamp": 1711447200000}},
                        ],
                    }
                }
            ]
        }
        
        with patch('os.path.exists', return_value=True), \
             patch('glob.glob', return_value=['/mock/conversations/sess-1.db']), \
             patch('sqlite3.connect', return_value=mock_conn), \
             patch('auto_usage._discover_antigravity_connections', return_value=[conn]), \
             patch('auto_usage.fetch_antigravity_trajectories', return_value=[]), \
             patch('auto_usage._antigravity_rpc', return_value=mock_resp) as mock_rpc, \
             patch('auto_usage._load_antigravity_cache', return_value=cached), \
             patch('auto_usage._save_antigravity_cache') as mock_save, \
             patch('auto_usage._load_antigravity_sync_metadata', return_value={}), \
             patch('auto_usage._save_antigravity_sync_metadata'):
            result = load_antigravity()
            
        # Verify that we did query the LS (it wasn't fooled by count = 2 vs 2)
        mock_rpc.assert_called_once_with(conn, 'GetCascadeTrajectoryGeneratorMetadata', {'cascadeId': 'sess-1'})
        # Verify cache was written with all 3 entries (r1, r2, r3)
        mock_save.assert_called_once()
        saved = mock_save.call_args[0][0]
        assert len(saved) == 3
        rids = {e['response_id'] for e in saved}
        assert rids == {"r1", "r2", "r3"}
        
        # Verify output sums: gen_idx 0 (r1/r2 counted once each) + gen_idx 1 (r3) = 1000 + 1000 + 5000 = 7000
        total = sum(result['gemini'].values())
        assert total == 7000

    def test_load_antigravity_twice_sync_metadata_avoid_rpc_regression(self):
        """Regression test verifying that a second load_antigravity refresh on a
        legacy cache (or a newly synced cache) avoids querying the LS repeatedly,
        properly updating the sync metadata file.
        """
        conn = AntigravityConnection(pid=1, port=9999, csrf_token="x")
        
        # Legacy cache without any gen_idx
        cached = [
            {"model": "gemini-3-flash-a", "timestamp": 1711447200000,
             "input": 1000, "output": 200, "cache_read": 5000, "response_id": "r1",
             "session_id": "sess-legacy"},
        ]
        
        # SQLite DB has generations {0, 1} (legacy gen 0, and new gen 1)
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ('gen_metadata',)
        mock_cursor.fetchall.return_value = [(0,), (1,)]
        mock_conn.cursor.return_value = mock_cursor
        
        # LS returns both generations (0 and 1)
        mock_resp = {
            "generatorMetadata": [
                {
                    "chatModel": {
                        "responseModel": "gemini-3-flash-a",
                        "retryInfos": [
                            {"usage": {"inputTokens": 1000, "responseId": "r1", "timestamp": 1711447200000}},
                        ],
                    }
                },
                {
                    "chatModel": {
                        "responseModel": "gemini-3-flash-a",
                        "retryInfos": [
                            {"usage": {"inputTokens": 5000, "responseId": "r2", "timestamp": 1711447200000}},
                        ],
                    }
                }
            ]
        }
        
        # We will track sync_meta in a local dict in the test to simulate disk state
        in_memory_sync_meta = {}
        
        def mock_load_sync():
            return dict(in_memory_sync_meta)
            
        def mock_save_sync(meta):
            in_memory_sync_meta.clear()
            in_memory_sync_meta.update(meta)

        # ----------------------------------------------------
        # Run 1: First sync (performs migration & queries LS for missing gen 1)
        # ----------------------------------------------------
        with patch('os.path.exists', return_value=True), \
             patch('glob.glob', return_value=['/mock/conversations/sess-legacy.db']), \
             patch('sqlite3.connect', return_value=mock_conn), \
             patch('auto_usage._discover_antigravity_connections', return_value=[conn]), \
             patch('auto_usage.fetch_antigravity_trajectories', return_value=[]), \
             patch('auto_usage._antigravity_rpc', return_value=mock_resp) as mock_rpc, \
             patch('auto_usage._load_antigravity_cache', return_value=cached), \
             patch('auto_usage._save_antigravity_cache') as mock_save, \
             patch('auto_usage._load_antigravity_sync_metadata', side_effect=mock_load_sync), \
             patch('auto_usage._save_antigravity_sync_metadata', side_effect=mock_save_sync):
            
            result1 = load_antigravity()
            
        # Verify that we did query the LS
        mock_rpc.assert_called_once_with(conn, 'GetCascadeTrajectoryGeneratorMetadata', {'cascadeId': 'sess-legacy'})
        
        # Sync metadata should be updated to [0, 1]
        assert in_memory_sync_meta == {'sess-legacy': [0, 1]}
        
        # Verify cache was written with merged entries containing gen_idx
        mock_save.assert_called_once()
        saved_entries = mock_save.call_args[0][0]
        # Check that the saved entries contain gen_idx
        assert any(e.get('gen_idx') is not None for e in saved_entries)
        
        # Prepare cache for the second run
        cached_after_run1 = saved_entries

        # ----------------------------------------------------
        # Run 2: Second sync (should avoid LS queries entirely!)
        # ----------------------------------------------------
        with patch('os.path.exists', return_value=True), \
             patch('glob.glob', return_value=['/mock/conversations/sess-legacy.db']), \
             patch('sqlite3.connect', return_value=mock_conn), \
             patch('auto_usage._discover_antigravity_connections', return_value=[conn]), \
             patch('auto_usage.fetch_antigravity_trajectories', return_value=[]), \
             patch('auto_usage._antigravity_rpc') as mock_rpc2, \
             patch('auto_usage._load_antigravity_cache', return_value=cached_after_run1), \
             patch('auto_usage._save_antigravity_cache') as mock_save2, \
             patch('auto_usage._load_antigravity_sync_metadata', side_effect=mock_load_sync), \
             patch('auto_usage._save_antigravity_sync_metadata', side_effect=mock_save_sync):
            
            result2 = load_antigravity()
            
        # Verify that we did NOT query the LS this time!
        mock_rpc2.assert_not_called()



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


class TestModelFamily:
    def test_gemini_from_label(self):
        assert _antigravity_model_family('Gemini 3.5 Flash (Medium)', 'MODEL_PLACEHOLDER_M20') == 'Gemini'

    def test_gemini_from_model_id(self):
        assert _antigravity_model_family('Some Label', 'gemini-3-flash-a') == 'Gemini'

    def test_claude_from_label(self):
        assert _antigravity_model_family('Claude Sonnet 4.6 (Thinking)', 'MODEL_PLACEHOLDER_M35') == 'Claude'

    def test_gpt_from_label(self):
        assert _antigravity_model_family('GPT-OSS 120B (Medium)', 'MODEL_OPENAI_GPT_OSS_120B_MEDIUM') == 'GPT'

    def test_unknown(self):
        assert _antigravity_model_family('Mystery Model', 'UNKNOWN_M99') == 'Other'


class TestExportAntigravityQuota:
    def test_no_connections_returns_empty(self):
        with patch('auto_usage._discover_antigravity_connections', return_value=[]):
            result = export_antigravity_quota()
        assert result == []

    def test_basic_quota_response(self):
        conn = AntigravityConnection(pid=1, port=9999, csrf_token="x")
        mock_resp = {
            "clientModelConfigs": [
                {
                    "label": "Gemini 3.5 Flash (Medium)",
                    "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M20"},
                    "quotaInfo": {"remainingFraction": 0.92, "resetTime": "2026-06-30T00:38:31Z"},
                },
                {
                    "label": "Claude Sonnet 4.6 (Thinking)",
                    "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M35"},
                    "quotaInfo": {"remainingFraction": 1.0, "resetTime": "2026-06-30T01:04:05Z"},
                },
            ]
        }
        with patch('auto_usage._discover_antigravity_connections', return_value=[conn]), \
             patch('auto_usage._antigravity_rpc', return_value=mock_resp):
            result = export_antigravity_quota()
        assert len(result) == 2
        # Gemini: remaining 0.92 → used 8%
        gemini = [q for q in result if 'Gemini' in q['label']]
        assert len(gemini) == 1
        assert gemini[0]['percentage'] == 8
        assert gemini[0]['provider'] == 'antigravity'
        assert gemini[0]['next_reset_time_ms'] is not None
        # Claude: remaining 1.0 → used 0%
        claude = [q for q in result if 'Claude' in q['label']]
        assert len(claude) == 1
        assert claude[0]['percentage'] == 0

    def test_models_grouped_by_family(self):
        """Multiple Gemini models with same quota should produce one entry."""
        conn = AntigravityConnection(pid=1, port=9999, csrf_token="x")
        mock_resp = {
            "clientModelConfigs": [
                {"label": "Gemini 3.5 Flash (Medium)", "modelOrAlias": {"model": "M20"},
                 "quotaInfo": {"remainingFraction": 0.92, "resetTime": "2026-06-30T00:38:31Z"}},
                {"label": "Gemini 3.5 Flash (High)", "modelOrAlias": {"model": "M132"},
                 "quotaInfo": {"remainingFraction": 0.92, "resetTime": "2026-06-30T00:38:31Z"}},
                {"label": "Gemini 3.1 Pro (Low)", "modelOrAlias": {"model": "M36"},
                 "quotaInfo": {"remainingFraction": 0.92, "resetTime": "2026-06-30T00:38:31Z"}},
            ]
        }
        with patch('auto_usage._discover_antigravity_connections', return_value=[conn]), \
             patch('auto_usage._antigravity_rpc', return_value=mock_resp):
            result = export_antigravity_quota()
        assert len(result) == 1
        assert 'Gemini' in result[0]['label']

    def test_max_used_per_family(self):
        """When models in the same family have different usage, report the max."""
        conn = AntigravityConnection(pid=1, port=9999, csrf_token="x")
        mock_resp = {
            "clientModelConfigs": [
                {"label": "Gemini 3.5 Flash (Medium)", "modelOrAlias": {"model": "M20"},
                 "quotaInfo": {"remainingFraction": 0.8, "resetTime": "2026-06-30T00:38:31Z"}},
                {"label": "Gemini 3.1 Pro (High)", "modelOrAlias": {"model": "M16"},
                 "quotaInfo": {"remainingFraction": 0.5, "resetTime": "2026-06-30T00:38:31Z"}},
            ]
        }
        with patch('auto_usage._discover_antigravity_connections', return_value=[conn]), \
             patch('auto_usage._antigravity_rpc', return_value=mock_resp):
            result = export_antigravity_quota()
        assert len(result) == 1
        # max used: 50% (from 0.5 remaining)
        assert result[0]['percentage'] == 50

    def test_missing_quota_info_skipped(self):
        conn = AntigravityConnection(pid=1, port=9999, csrf_token="x")
        mock_resp = {
            "clientModelConfigs": [
                {"label": "Gemini 3.5 Flash", "modelOrAlias": {"model": "M20"}},
                {"label": "Claude Sonnet", "modelOrAlias": {"model": "M35"},
                 "quotaInfo": {"remainingFraction": 0.5, "resetTime": "2026-06-30T01:00:00Z"}},
            ]
        }
        with patch('auto_usage._discover_antigravity_connections', return_value=[conn]), \
             patch('auto_usage._antigravity_rpc', return_value=mock_resp):
            result = export_antigravity_quota()
        # Only Claude should appear (Gemini has no quotaInfo)
        assert len(result) == 1
        assert 'Claude' in result[0]['label']

    def test_rpc_failure_returns_empty(self):
        conn = AntigravityConnection(pid=1, port=9999, csrf_token="x")
        with patch('auto_usage._discover_antigravity_connections', return_value=[conn]), \
             patch('auto_usage._antigravity_rpc', return_value=None):
            result = export_antigravity_quota()
        assert result == []


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

    def test_live_quota_returns_data(self):
        result = export_antigravity_quota()
        assert len(result) > 0, "LS is running but no quota data returned"
        for q in result:
            assert q['provider'] == 'antigravity'
            assert 0 <= q['percentage'] <= 100
            assert q['label'] != ''