from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pricing_config import get_pricing  # noqa: E402
from dsh_usage import (  # noqa: E402
    _session_log_generation,
    calc_dsh_cost,
    is_dsh_glm_model,
    iter_dsh_usage_records,
    load_dsh,
    load_dsh_detailed,
)


def _ms(year: int, month: int, day: int, hour: int = 10, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute).timestamp() * 1000)


def _event(event_type: str, data: dict, time_ms: int, seq: int = 0, **extra) -> str:
    event = {'type': event_type, 'seq': seq, 'time': time_ms, 'data': data}
    event.update(extra)
    return json.dumps(event)


def _chunk_usage(turn: int, step: int, usage: dict, time_ms: int) -> str:
    return _event('assistant/chunk', {'turn': turn, 'step': step, 'chunk': {'type': 'usage', 'usage': usage}}, time_ms)


def _assistant_message(turn: int, step: int, usage: dict | None, provider: str, model: str, time_ms: int) -> str:
    data = {
        'turn': turn,
        'step': step,
        'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'ok'}], 'source': {'provider': provider, 'model': model}},
    }
    if usage is not None:
        data['usage'] = usage
    return _event('assistant/message', data, time_ms)


def _write_session(root: Path, workspace: str, session_dir: str, lines: list[str], filename: str = 'session.jsonl') -> Path:
    directory = root / workspace / session_dir
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path


def _sample_log(session_id: str = 'session-abc') -> list[str]:
    return [
        _event('session', {}, _ms(2026, 8, 14, 9), id=session_id, cwd='/tmp/project', createdAt=_ms(2026, 8, 14, 9)),
        _event('request/header', {'header': {'config': {'provider': 'zai', 'model': 'glm-5.3'}}}, _ms(2026, 8, 14, 9, minute=1)),
        # Step 1: early chunk sample replaced by the finalized message sample.
        _chunk_usage(1, 1, {'inputTokens': 100, 'outputTokens': 5}, _ms(2026, 8, 14, 10)),
        _assistant_message(1, 1, {'inputTokens': 200, 'outputTokens': 20, 'cacheReadTokens': 30, 'cacheWriteTokens': 10}, 'zai', 'glm-5.3', _ms(2026, 8, 14, 10, minute=1)),
        # Step 2: chunk-only step (request failed later) still counts.
        _chunk_usage(1, 2, {'inputTokens': 50, 'outputTokens': 5}, _ms(2026, 8, 14, 11)),
    ]


def test_last_wins_replaces_chunk_sample(tmp_path: Path) -> None:
    _write_session(tmp_path, 'ws', 'session-abc', _sample_log())
    records = list(iter_dsh_usage_records(sessions_dir=tmp_path))
    step1 = [r for r in records if r['input'] == 200]
    assert len(step1) == 1
    assert step1[0]['output'] == 20
    assert step1[0]['cache_read'] == 30
    assert step1[0]['cache_write'] == 10
    # The chunk-only step survives with its own sample.
    assert any(r['input'] == 50 for r in records)


def test_model_attribution_prefers_message_source(tmp_path: Path) -> None:
    log = _sample_log() + [
        _assistant_message(2, 1, {'inputTokens': 10, 'outputTokens': 2}, 'lmstudio', 'qwen3.8-27b-mlx', _ms(2026, 8, 14, 12)),
    ]
    _write_session(tmp_path, 'ws', 'session-abc', log)
    detailed = load_dsh_detailed(sessions_dir=tmp_path)
    models = detailed[date(2026, 8, 14)]
    assert models['zai/glm-5.3']['input'] == 250  # 200 + chunk-only 50
    assert models['lmstudio/qwen3.8-27b-mlx']['input'] == 10


def test_header_config_attributes_chunk_only_steps(tmp_path: Path) -> None:
    _write_session(tmp_path, 'ws', 'session-abc', _sample_log())
    records = list(iter_dsh_usage_records(sessions_dir=tmp_path))
    chunk_only = [r for r in records if r['input'] == 50]
    assert chunk_only[0]['model'] == 'zai/glm-5.3'


def test_session_log_generation_parsing() -> None:
    assert _session_log_generation('session.jsonl') == 0
    assert _session_log_generation('session.jsonl.zstd') == 0
    assert _session_log_generation('session.v1.jsonl') == 1
    assert _session_log_generation('session.v4.jsonl.zstd') == 4
    assert _session_log_generation('session.v27.jsonl') == 27
    # Invalid names must not be mistaken for logs.
    assert _session_log_generation('session.v0.jsonl') is None  # v0 is not a generation file
    assert _session_log_generation('session.v01.jsonl') is None  # no leading zeros
    assert _session_log_generation('session.jsonl.tmp') is None
    assert _session_log_generation('session.jsonl.zstd.tmp') is None
    assert _session_log_generation('session.lock') is None
    assert _session_log_generation('session.jsonl.bak') is None


def _migrated_log(session_id: str = 'session-mig') -> list[str]:
    """A generation-4 replay: same session, turn 1 finalized, turn 2 new."""
    return [
        _event('session', {}, _ms(2026, 9, 1), 9, id=session_id, cwd='/tmp/project', createdAt=_ms(2026, 9, 1, 9)),
        _event('request/header', {'header': {'config': {'provider': 'zai', 'model': 'glm-5.3'}}}, _ms(2026, 9, 1, 9, minute=1)),
        _assistant_message(1, 1, {'inputTokens': 200, 'outputTokens': 20}, 'zai', 'glm-5.3', _ms(2026, 9, 1, 10)),
        _assistant_message(2, 1, {'inputTokens': 70, 'outputTokens': 7}, 'zai', 'glm-5.3', _ms(2026, 9, 2, 10)),
    ]


def test_newest_generation_wins(tmp_path: Path) -> None:
    """When a session directory holds several generations, only the newest is counted.

    The older generation is a replay of the same session; counting both
    would double count every shared turn/step.
    """
    # Older generation (0): turn 1 with a chunk-only provisional sample.
    old = [
        _event('session', {}, _ms(2026, 9, 1, 9), id='session-mig', cwd='/tmp/project', createdAt=_ms(2026, 9, 1, 9)),
        _event('request/header', {'header': {'config': {'provider': 'zai', 'model': 'glm-5.3'}}}, _ms(2026, 9, 1, 9, minute=1)),
        _chunk_usage(1, 1, {'inputTokens': 100, 'outputTokens': 5}, _ms(2026, 9, 1, 10)),
    ]
    _write_session(tmp_path, 'ws', 'session-mig', old, filename='session.jsonl')
    _write_session(tmp_path, 'ws', 'session-mig', _migrated_log(), filename='session.v4.jsonl')
    daily = load_dsh(sessions_dir=tmp_path)
    # Only the v4 replay counts: 200+20 + 70+7 = 297 across two days.
    assert daily == {date(2026, 9, 1): 220, date(2026, 9, 2): 77}


def test_newest_generation_selected_among_many(tmp_path: Path) -> None:
    _write_session(tmp_path, 'ws', 'session-mig', _migrated_log(), filename='session.jsonl')
    _write_session(tmp_path, 'ws', 'session-mig', _migrated_log(), filename='session.v2.jsonl')
    _write_session(tmp_path, 'ws', 'session-mig', _migrated_log(), filename='session.v4.jsonl')
    daily = load_dsh(sessions_dir=tmp_path)
    # Exactly one generation is counted, whatever the generation number.
    assert daily == {date(2026, 9, 1): 220, date(2026, 9, 2): 77}


def test_daily_totals_and_cache_tokens(tmp_path: Path) -> None:
    _write_session(tmp_path, 'ws', 'session-abc', _sample_log())
    daily = load_dsh(sessions_dir=tmp_path)
    # step1: 200+20+30+10 = 260; step2: 50+5 = 55
    assert daily == {date(2026, 8, 14): 315}


def test_date_filtering(tmp_path: Path) -> None:
    other_day = [
        _event('session', {}, _ms(2026, 8, 1, 9), id='session-old', createdAt=_ms(2026, 8, 1, 9)),
        _assistant_message(1, 1, {'inputTokens': 1000, 'outputTokens': 100}, 'zai', 'glm-5.3', _ms(2026, 8, 1, 10)),
    ]
    _write_session(tmp_path, 'ws2', 'session-old', other_day)
    _write_session(tmp_path, 'ws', 'session-abc', _sample_log())
    daily = load_dsh(sessions_dir=tmp_path, start_date=date(2026, 8, 14))
    assert date(2026, 8, 1) not in daily
    assert daily == {date(2026, 8, 14): 315}


def test_dual_encoding_counts_session_once(tmp_path: Path) -> None:
    _write_session(tmp_path, 'ws', 'session-abc', _sample_log(), filename='session.jsonl')
    # A stale same-generation twin (here plain bytes under a .zstd name)
    # must not double count; one log per session directory is selected.
    _write_session(tmp_path, 'ws', 'session-abc', _sample_log(), filename='session.jsonl.zstd')
    daily = load_dsh(sessions_dir=tmp_path)
    assert daily == {date(2026, 8, 14): 315}


def test_torn_final_line_is_skipped(tmp_path: Path) -> None:
    lines = _sample_log()
    lines.append('{"type": "assistant/message", "data": {"turn')  # torn mid-append
    _write_session(tmp_path, 'ws', 'session-abc', lines)
    daily = load_dsh(sessions_dir=tmp_path)
    assert daily == {date(2026, 8, 14): 315}


def test_glm_routing() -> None:
    assert is_dsh_glm_model('zai/glm-5.3')
    assert is_dsh_glm_model('zai/glm-4.7')
    assert not is_dsh_glm_model('lmstudio/qwen3.8-27b-mlx')
    assert not is_dsh_glm_model('lmstudio/glm-5.3')  # local GLM is free compute
    assert not is_dsh_glm_model('deepseek/chat')


def test_cost_pricing(tmp_path: Path) -> None:
    _write_session(tmp_path, 'ws', 'session-abc', _sample_log())
    detailed = load_dsh_detailed(sessions_dir=tmp_path)
    costs = calc_dsh_cost(detailed)
    # zai/glm-5.3 aliases to glm-5.1 pricing: nonzero API-equivalent cost.
    assert costs[date(2026, 8, 14)] > 0
    local_only = {date(2026, 8, 14): {'lmstudio/qwen3.8-27b-mlx': {'input': 1000, 'output': 100, 'cache_read': 0, 'cache_write': 0}}}
    # Local LM Studio / MLX models are in the table at $0.
    assert calc_dsh_cost(local_only) == {date(2026, 8, 14): 0.0}


def test_cost_bills_non_cached_input(tmp_path: Path) -> None:
    # Regression: DSH stores `input` as NON-cached input with `cache_read`
    # separate. Passing it as a cache-inclusive total used to subtract the
    # cache again and collapse to $0 for the input portion.
    detailed = {
        date(2026, 8, 14): {
            'zai/glm-5.3': {'input': 1_000_000, 'output': 100_000, 'cache_read': 1_000_000, 'cache_write': 0}
        }
    }
    cost = calc_dsh_cost(detailed)[date(2026, 8, 14)]
    p = get_pricing('glm-5.3')
    expected = (
        1_000_000 * p['input'] / 1_000_000
        + 1_000_000 * p['cached'] / 1_000_000
        + 100_000 * p['output'] / 1_000_000
    )
    assert abs(cost - expected) < 1e-9
    assert cost > 0
