"""Tests for cross-machine Antigravity entry ingest."""
import json
import os
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auto_usage import (
    ingest_antigravity_entries,
    ANTIGRAVITY_CACHE_FILE,
    _load_antigravity_cache,
)


@pytest.fixture
def temp_cache(tmp_path, monkeypatch):
    """Redirect the cache file to a temp path for isolation."""
    monkeypatch.setattr('auto_usage.ANTIGRAVITY_CACHE_FILE', str(tmp_path / 'antigravity_usage_cache.json'))
    yield tmp_path


class TestIngestAntigravityEntries:
    def test_empty_entries_into_empty_cache(self, temp_cache):
        result = ingest_antigravity_entries([])
        assert result == {'received': 0, 'new': 0, 'duplicate': 0, 'total_cache': 0}

    def test_new_entries_into_empty_cache(self, temp_cache):
        entries = [
            {'model': 'gemini-3-flash-a', 'timestamp': 1711447200000,
             'input': 1000, 'output': 200, 'cache_read': 5000,
             'cache_write': 0, 'thinking': 50, 'response_id': 'r1',
             'session_id': 's1'},
            {'model': 'gemini-3-flash-a', 'timestamp': 1711447300000,
             'input': 500, 'output': 100, 'cache_read': 0,
             'cache_write': 0, 'thinking': 0, 'response_id': 'r2',
             'session_id': 's2'},
        ]
        result = ingest_antigravity_entries(entries)
        assert result == {'received': 2, 'new': 2, 'duplicate': 0, 'total_cache': 2}
        cached = _load_antigravity_cache()
        assert len(cached) == 2
        rids = {e['response_id'] for e in cached}
        assert rids == {'r1', 'r2'}

    def test_duplicate_entries_deduped(self, temp_cache):
        entries1 = [
            {'model': 'gemini-3-flash-a', 'timestamp': 1711447200000,
             'input': 1000, 'output': 0, 'cache_read': 0,
             'cache_write': 0, 'thinking': 0, 'response_id': 'shared',
             'session_id': 's1'},
        ]
        ingest_antigravity_entries(entries1)

        # Push same entry again
        result = ingest_antigravity_entries(entries1)
        assert result == {'received': 1, 'new': 0, 'duplicate': 1, 'total_cache': 1}
        cached = _load_antigravity_cache()
        assert len(cached) == 1

    def test_mixed_new_and_duplicate(self, temp_cache):
        first_batch = [
            {'model': 'gemini-3-flash-a', 'timestamp': 1711447200000,
             'input': 1000, 'output': 0, 'cache_read': 0,
             'cache_write': 0, 'thinking': 0, 'response_id': 'old-1',
             'session_id': 's1'},
        ]
        ingest_antigravity_entries(first_batch)

        second_batch = [
            {'model': 'gemini-3-flash-a', 'timestamp': 1711447200000,
             'input': 1000, 'output': 0, 'cache_read': 0,
             'cache_write': 0, 'thinking': 0, 'response_id': 'old-1',
             'session_id': 's1'},
            {'model': 'gemini-3-flash-a', 'timestamp': 1711447300000,
             'input': 2000, 'output': 0, 'cache_read': 0,
             'cache_write': 0, 'thinking': 0, 'response_id': 'new-1',
             'session_id': 's2'},
        ]
        result = ingest_antigravity_entries(second_batch)
        assert result == {'received': 2, 'new': 1, 'duplicate': 1, 'total_cache': 2}

    def test_entries_without_response_id_always_appended(self, temp_cache):
        e1 = [{'model': 'gemini-3-flash-a', 'timestamp': 1711447200000,
               'input': 1000, 'response_id': None, 'session_id': 's1'}]
        result1 = ingest_antigravity_entries(e1)
        assert result1 == {'received': 1, 'new': 1, 'duplicate': 0, 'total_cache': 1}

        # Push again — no response_id so it's appended again (no dedup possible)
        result2 = ingest_antigravity_entries(e1)
        assert result2 == {'received': 1, 'new': 1, 'duplicate': 0, 'total_cache': 2}

    def test_preserves_existing_cache_on_empty_push(self, temp_cache):
        entries = [
            {'model': 'gemini-3-flash-a', 'timestamp': 1711447200000,
             'input': 1000, 'response_id': 'r1', 'session_id': 's1'},
        ]
        ingest_antigravity_entries(entries)
        ingest_antigravity_entries([])
        cached = _load_antigravity_cache()
        assert len(cached) == 1

    def test_multiple_pushes_accumulate(self, temp_cache):
        batch1 = [
            {'model': 'gemini-3-flash-a', 'timestamp': 1711447200000,
             'input': 1000, 'response_id': 'r1', 'session_id': 's1'},
        ]
        batch2 = [
            {'model': 'gemini-3-flash-a', 'timestamp': 1711447300000,
             'input': 2000, 'response_id': 'r2', 'session_id': 's2'},
        ]
        ingest_antigravity_entries(batch1)
        result = ingest_antigravity_entries(batch2)
        assert result['total_cache'] == 2
        assert result['new'] == 1

    def test_ingested_entries_appear_in_load_antigravity(self, temp_cache):
        """After ingest, load_antigravity should see the pushed entries."""
        from auto_usage import load_antigravity

        entries = [
            {'model': 'gemini-3-flash-a', 'timestamp': 1711447200000,
             'input': 1000, 'output': 200, 'cache_read': 5000,
             'cache_write': 0, 'thinking': 50, 'response_id': 'pushed-1',
             'session_id': 'remote-session'},
        ]
        ingest_antigravity_entries(entries)

        with patch('auto_usage._discover_antigravity_connections', return_value=[]), \
             patch('auto_usage._save_antigravity_cache'):
            result = load_antigravity()
        total = sum(result['gemini'].values())
        assert total == 6250  # 1000 + 200 + 5000 + 50 (thinking)