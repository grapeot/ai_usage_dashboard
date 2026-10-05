import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codex_local import (
    calc_cost,
    iter_session_files,
    load_daily,
    load_detailed,
    parse_session_file,
)
from pricing_config import get_pricing


def _write_session(root: Path, day: str, name: str, events: list[dict]) -> Path:
    y, m, d = day.split('-')
    day_dir = root / y / m / d
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / name
    path.write_text('\n'.join(json.dumps(e) for e in events))
    return path


def _turn(model: str) -> dict:
    return {'timestamp': '2026-03-20T10:00:00.000Z', 'type': 'turn_context', 'payload': {'model': model}}


def _token_count(input_tokens: int, cached: int, output: int, reasoning: int = 0, cache_write: int = 0) -> dict:
    return {
        'timestamp': '2026-03-20T10:00:05.000Z',
        'type': 'event_msg',
        'payload': {
            'type': 'token_count',
            'info': {
                'last_token_usage': {
                    'input_tokens': input_tokens,
                    'cached_input_tokens': cached,
                    'output_tokens': output,
                    'reasoning_output_tokens': reasoning,
                    'cache_write_input_tokens': cache_write,
                },
            },
        },
    }


def test_iter_session_files_filters_by_filename_date(tmp_path):
    sessions = tmp_path / 'sessions'
    _write_session(sessions, '2026-03-20', 'rollout-2026-03-20T10-00-00-abc.jsonl', [])
    _write_session(sessions, '2026-03-25', 'rollout-2026-03-25T10-00-00-def.jsonl', [])
    found = list(iter_session_files(sessions_dir=sessions, archive_dir=tmp_path / 'none', start_date=date(2026, 3, 18), end_date=date(2026, 3, 21)))
    assert [p.name for p in found] == ['rollout-2026-03-20T10-00-00-abc.jsonl']


def test_non_cached_input_is_total_minus_cached(tmp_path):
    # Codex input_tokens is cache-inclusive; non-cached = input - cached.
    sessions = tmp_path / 'sessions'
    _write_session(sessions, '2026-03-20', 'rollout-2026-03-20T10-00-00-abc.jsonl', [
        _turn('gpt-5-codex'),
        _token_count(input_tokens=1000, cached=800, output=50),
    ])
    detailed = load_detailed(sessions_dir=sessions, archive_dir=tmp_path / 'none')
    assert detailed[date(2026, 3, 20)]['gpt-5-codex'] == {'input': 200, 'output': 50, 'cache_read': 800, 'cache_write': 0}


def test_reasoning_folds_into_output(tmp_path):
    sessions = tmp_path / 'sessions'
    _write_session(sessions, '2026-03-20', 'rollout-2026-03-20T10-00-00-abc.jsonl', [
        _turn('gpt-5-codex'),
        _token_count(input_tokens=100, cached=0, output=50, reasoning=25),
    ])
    detailed = load_detailed(sessions_dir=sessions, archive_dir=tmp_path / 'none')
    assert detailed[date(2026, 3, 20)]['gpt-5-codex']['output'] == 75


def test_model_follows_most_recent_turn_context(tmp_path):
    sessions = tmp_path / 'sessions'
    _write_session(sessions, '2026-03-20', 'rollout-2026-03-20T10-00-00-abc.jsonl', [
        _turn('gpt-5-codex'),
        _token_count(100, 0, 10),
        _turn('glm-5.3-flash'),
        _token_count(200, 0, 20),
    ])
    detailed = load_detailed(sessions_dir=sessions, archive_dir=tmp_path / 'none')
    day = detailed[date(2026, 3, 20)]
    assert day['gpt-5-codex']['input'] == 100
    assert day['glm-5.3-flash']['input'] == 200


def test_load_daily_sums_all_parts(tmp_path):
    sessions = tmp_path / 'sessions'
    _write_session(sessions, '2026-03-20', 'rollout-2026-03-20T10-00-00-abc.jsonl', [
        _turn('gpt-5-codex'),
        _token_count(input_tokens=1000, cached=600, output=100),
    ])
    # 400 non-cached + 600 cached + 100 output = 1100
    assert load_daily(sessions_dir=sessions, archive_dir=tmp_path / 'none') == {date(2026, 3, 20): 1100}


def test_cost_matches_canonical_rates(tmp_path):
    sessions = tmp_path / 'sessions'
    _write_session(sessions, '2026-03-20', 'rollout-2026-03-20T10-00-00-abc.jsonl', [
        _turn('gpt-5.3-codex'),
        _token_count(input_tokens=1_000_000, cached=400_000, output=100_000),
    ])
    detailed = load_detailed(sessions_dir=sessions, archive_dir=tmp_path / 'none')
    cost = calc_cost(detailed)[date(2026, 3, 20)]
    p = get_pricing('gpt-5.3-codex')
    expected = (
        600_000 * p['input'] / 1e6
        + 400_000 * p['cached'] / 1e6
        + 100_000 * p['output'] / 1e6
    )
    assert abs(cost - expected) < 1e-9


def test_usage_events_before_any_turn_context_use_unknown(tmp_path):
    sessions = tmp_path / 'sessions'
    _write_session(sessions, '2026-03-20', 'rollout-2026-03-20T10-00-00-abc.jsonl', [
        _token_count(100, 0, 10),
    ])
    detailed = load_detailed(sessions_dir=sessions, archive_dir=tmp_path / 'none')
    assert 'unknown' in detailed[date(2026, 3, 20)]


def test_torn_and_malformed_lines_are_skipped(tmp_path):
    sessions = tmp_path / 'sessions'
    path = _write_session(sessions, '2026-03-20', 'rollout-2026-03-20T10-00-00-abc.jsonl', [_turn('gpt-5-codex'), _token_count(100, 0, 10)])
    with path.open('a') as f:
        f.write('\n{"type": "event_msg", "payload": {"type": "token')  # torn
    _sid, records = parse_session_file(path)
    assert len(records) == 1


def test_missing_dirs_return_empty(tmp_path):
    assert load_daily(sessions_dir=tmp_path / 'nope', archive_dir=tmp_path / 'nada') == {}
