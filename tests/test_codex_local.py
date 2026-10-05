import json
import sys
from datetime import date, datetime, timedelta
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

# Fixture timestamps are UTC; events group by LOCAL date, so derive the
# expected day from the timestamp rather than hard-coding it, to keep these
# tests timezone-independent (CI runs UTC, the author runs PST).
_TURN_TS = '2026-03-20T10:00:00.000Z'
_TOKEN_TS = '2026-03-20T10:00:05.000Z'


def _local_day(ts: str) -> date:
    return datetime.fromisoformat(ts.replace('Z', '+00:00')).astimezone().date()


DAY = _local_day(_TOKEN_TS)


def _write_session(root: Path, day: str, name: str, events: list[dict]) -> Path:
    y, m, d = day.split('-')
    day_dir = root / y / m / d
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / name
    path.write_text('\n'.join(json.dumps(e) for e in events))
    return path


def _turn(model: str) -> dict:
    return {'timestamp': _TURN_TS, 'type': 'turn_context', 'payload': {'model': model}}


def _token_count(input_tokens: int, cached: int, output: int, reasoning: int = 0, cache_write: int = 0,
                 cumulative: int | None = None, timestamp: str = _TOKEN_TS) -> dict:
    return {
        'timestamp': timestamp,
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
                'total_token_usage': {'total_tokens': cumulative} if cumulative is not None else None,
            },
        },
    }


def test_iter_session_files_filters_by_filename_date(tmp_path):
    sessions = tmp_path / 'sessions'
    _write_session(sessions, '2026-03-20', 'rollout-2026-03-20T10-00-00-abc.jsonl', [])
    _write_session(sessions, '2026-03-25', 'rollout-2026-03-25T10-00-00-def.jsonl', [])
    found = list(iter_session_files(sessions_dir=sessions, archive_dir=tmp_path / 'none', start_date=date(2026, 3, 18), end_date=date(2026, 3, 21)))
    assert [p.name for p in found] == ['rollout-2026-03-20T10-00-00-abc.jsonl']


def test_iter_session_files_keeps_earlier_file_that_spills_into_window(tmp_path):
    # A session that started before the window can append events inside it, so
    # its (earlier) filename must NOT be dropped by a start-date prefilter.
    sessions = tmp_path / 'sessions'
    _write_session(sessions, '2026-03-18', 'rollout-2026-03-18T23-00-00-abc.jsonl', [
        _turn('gpt-5-codex'),
        _token_count(100, 0, 10),
    ])
    found = list(iter_session_files(sessions_dir=sessions, archive_dir=tmp_path / 'none', start_date=DAY, end_date=DAY))
    assert len(found) == 1
    assert load_daily(sessions_dir=sessions, archive_dir=tmp_path / 'none', start_date=DAY, end_date=DAY) == {DAY: 110}


def test_result_is_independent_of_end_date(tmp_path):
    # A session's filename date is its start in UTC, but events group by LOCAL
    # date. Extending the window's end must never change a day's total.
    sessions = tmp_path / 'sessions'
    # Name the file one day after DAY's UTC-ish date to exercise the allowance.
    _write_session(sessions, '2026-03-21', 'rollout-2026-03-21T03-00-00-abc.jsonl', [
        _turn('gpt-5-codex'),
        _token_count(100, 0, 10),
    ])
    one_day = load_daily(sessions_dir=sessions, archive_dir=tmp_path / 'none', start_date=DAY, end_date=DAY)
    two_day = load_daily(sessions_dir=sessions, archive_dir=tmp_path / 'none', start_date=DAY, end_date=DAY + timedelta(days=1))
    assert DAY in one_day and one_day[DAY] == 110
    assert one_day == two_day


def test_duplicate_token_count_events_are_skipped(tmp_path):
    # Codex re-emits a token_count sample without the cumulative advancing;
    # those repeats are not new work and must not be summed twice.
    sessions = tmp_path / 'sessions'
    _write_session(sessions, '2026-03-20', 'rollout-2026-03-20T10-00-00-abc.jsonl', [
        _turn('gpt-5-codex'),
        _token_count(1000, 600, 100, cumulative=1100),
        _token_count(1000, 600, 100, cumulative=1100),  # repeat: same cumulative
        _token_count(2000, 1000, 200, cumulative=3200),  # advanced: new work
    ])
    detailed = load_detailed(sessions_dir=sessions, archive_dir=tmp_path / 'none')
    # first sample only contributes (400 non-cached + 600 cached + 100 out),
    # plus the advanced sample (1000 + 1000 + 200)
    assert detailed[DAY]['gpt-5-codex'] == {'input': 1400, 'output': 300, 'cache_read': 1600, 'cache_write': 0}


def test_non_cached_input_is_total_minus_cached(tmp_path):
    # Codex input_tokens is cache-inclusive; non-cached = input - cached.
    sessions = tmp_path / 'sessions'
    _write_session(sessions, '2026-03-20', 'rollout-2026-03-20T10-00-00-abc.jsonl', [
        _turn('gpt-5-codex'),
        _token_count(input_tokens=1000, cached=800, output=50),
    ])
    detailed = load_detailed(sessions_dir=sessions, archive_dir=tmp_path / 'none')
    assert detailed[DAY]['gpt-5-codex'] == {'input': 200, 'output': 50, 'cache_read': 800, 'cache_write': 0}


def test_reasoning_folds_into_output(tmp_path):
    sessions = tmp_path / 'sessions'
    _write_session(sessions, '2026-03-20', 'rollout-2026-03-20T10-00-00-abc.jsonl', [
        _turn('gpt-5-codex'),
        _token_count(input_tokens=100, cached=0, output=50, reasoning=25),
    ])
    detailed = load_detailed(sessions_dir=sessions, archive_dir=tmp_path / 'none')
    assert detailed[DAY]['gpt-5-codex']['output'] == 75


def test_model_follows_most_recent_turn_context(tmp_path):
    sessions = tmp_path / 'sessions'
    _write_session(sessions, '2026-03-20', 'rollout-2026-03-20T10-00-00-abc.jsonl', [
        _turn('gpt-5-codex'),
        _token_count(100, 0, 10),
        _turn('glm-5.3-flash'),
        _token_count(200, 0, 20),
    ])
    detailed = load_detailed(sessions_dir=sessions, archive_dir=tmp_path / 'none')
    day = detailed[DAY]
    assert day['gpt-5-codex']['input'] == 100
    assert day['glm-5.3-flash']['input'] == 200


def test_load_daily_sums_all_parts(tmp_path):
    sessions = tmp_path / 'sessions'
    _write_session(sessions, '2026-03-20', 'rollout-2026-03-20T10-00-00-abc.jsonl', [
        _turn('gpt-5-codex'),
        _token_count(input_tokens=1000, cached=600, output=100),
    ])
    # 400 non-cached + 600 cached + 100 output = 1100
    assert load_daily(sessions_dir=sessions, archive_dir=tmp_path / 'none') == {DAY: 1100}


def test_cost_matches_canonical_rates(tmp_path):
    sessions = tmp_path / 'sessions'
    _write_session(sessions, '2026-03-20', 'rollout-2026-03-20T10-00-00-abc.jsonl', [
        _turn('gpt-5.3-codex'),
        _token_count(input_tokens=1_000_000, cached=400_000, output=100_000),
    ])
    detailed = load_detailed(sessions_dir=sessions, archive_dir=tmp_path / 'none')
    cost = calc_cost(detailed)[DAY]
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
    assert 'unknown' in detailed[DAY]


def test_torn_and_malformed_lines_are_skipped(tmp_path):
    sessions = tmp_path / 'sessions'
    path = _write_session(sessions, '2026-03-20', 'rollout-2026-03-20T10-00-00-abc.jsonl', [_turn('gpt-5-codex'), _token_count(100, 0, 10)])
    with path.open('a') as f:
        f.write('\n{"type": "event_msg", "payload": {"type": "token')  # torn
    _sid, records = parse_session_file(path)
    assert len(records) == 1


def test_missing_dirs_return_empty(tmp_path):
    assert load_daily(sessions_dir=tmp_path / 'nope', archive_dir=tmp_path / 'nada') == {}
