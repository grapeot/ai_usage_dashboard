#!/usr/bin/env python3
"""
Export and aggregate token usage from Codex, Cursor, GLM, and OpenCode.
Supports API-equivalent USD cost estimation. See docs/rfc.md.
"""
import json
import csv
import importlib
import subprocess
import os
import sys
import argparse
import sqlite3
from datetime import date, datetime, timedelta
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import TypedDict, cast
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.font_manager import FontProperties
import requests

from pricing_config import get_pricing, calc_cost

plt.rcParams['axes.unicode_minus'] = False

# Assumed GLM input/output split when the API only returns total tokens.
GLM_INPUT_RATIO = 0.7
GLM_OUTPUT_RATIO = 0.3

# Separate fonts keep desktop charts readable across platforms.
# Fall back to matplotlib defaults if the preferred fonts are unavailable.
FONT_ZH = FontProperties(family=['STHeiti', 'Heiti TC', 'PingFang HK', 'Kaiti SC', 'Songti SC', 'Arial Unicode MS'])
FONT_EN = FontProperties(family=['Helvetica Neue', 'Helvetica', 'Arial', 'DejaVu Sans'])

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OPENCODE_STORAGE = Path.home() / '.local' / 'share' / 'opencode' / 'storage' / 'message'
OPENCODE_DB = Path.home() / '.local' / 'share' / 'opencode' / 'opencode.db'
CLAUDE_PROJECT_DIRS = [
    Path.home() / '.claude' / 'projects',
    Path.home() / '.config' / 'claude' / 'projects',
]

GLM_PROVIDERS = ('zai-coding-plan', 'zai-coding-plan/glm-4.7')

DailyTokens = dict[date, int]
DailyCosts = dict[date, float]
DailyModelTokens = Mapping[date, Mapping[str, dict[str, int]]]
TimeInterval = tuple[datetime, datetime]
DailyActiveSeconds = dict[date, float]
OUTPUT_EINK_JSON = 'token_usage_eink.json'


class OpencodeTurnMessage(TypedDict):
    session_id: str
    time: datetime
    role: str | None
    provider_id: str
    model_id: str


class CodexTurnEvent(TypedDict):
    type: str | None
    payload_type: str | None
    time: datetime


class ClaudeUsageRecord(TypedDict):
    time: datetime
    model: str
    speed: str
    input: int
    output: int
    cache_read: int
    cache_write: int
    cache_write_5m: int
    cache_write_1h: int


def date_to_epoch_ms(target_date: date) -> int:
    return int(datetime.combine(target_date, datetime.min.time()).timestamp() * 1000)


def classify_opencode_bucket(provider_id: str, model_id: str, exclude_glm: bool = True) -> str | None:
    provider_lower = (provider_id or '').lower()
    model_lower = (model_id or '').lower()

    if exclude_glm and provider_lower in GLM_PROVIDERS:
        return None
    if 'anthropic' in provider_lower or model_lower.startswith('anthropic/'):
        return 'anthropic'
    if provider_lower == 'openai' or model_lower.startswith('gpt-'):
        return 'gpt_opencode'
    if 'deepseek' in provider_lower or model_lower.startswith(('deepseek-', 'deepseek/')):
        return 'deepseek'
    return 'opencode_other'

def load_env():
    env_path = os.path.join(SCRIPT_DIR, '.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    if key not in os.environ:
                        os.environ[key] = value


def configure_opencode_skill_path():
    path = os.environ.get('AI_USAGE_OPENCODE_SKILL_PATH', '').strip()
    if path and path not in sys.path:
        sys.path.insert(0, path)

def get_date_range(days=30):
    today = datetime.now()
    start = today - timedelta(days=days - 1)
    
    start_date = start.strftime('%Y-%m-%d')
    end_date = today.strftime('%Y-%m-%d')
    
    start_ts = int(start.timestamp() * 1000)
    end_ts = int(today.timestamp() * 1000)
    
    return start_date, end_date, start_ts, end_ts


def list_dates_in_range(start_date: str, end_date: str) -> list[date]:
    start = datetime.strptime(start_date, '%Y-%m-%d').date()
    end = datetime.strptime(end_date, '%Y-%m-%d').date()
    current = start
    all_dates: list[date] = []
    while current <= end:
        all_dates.append(current)
        current += timedelta(days=1)
    return all_dates


def merge_daily_tokens(*sources: DailyTokens) -> DailyTokens:
    merged: defaultdict[date, int] = defaultdict(int)
    for source in sources:
        for d, value in source.items():
            merged[d] += value
    return dict(merged)


def build_eink_dashboard_payload(
    cursor: DailyTokens,
    glm: DailyTokens,
    claude: DailyTokens,
    gpt_opencode: DailyTokens,
    deepseek: DailyTokens,
    other: DailyTokens,
    start_date: str,
    end_date: str,
    daily_costs: DailyCosts | None = None,
    daily_active_seconds: DailyActiveSeconds | None = None,
) -> dict[str, object]:
    all_dates = list_dates_in_range(start_date, end_date)
    has_costs = daily_costs is not None

    daily_entries: list[dict[str, object]] = []
    summary_totals = {
        'cursor': 0,
        'glm': 0,
        'claude': 0,
        'gpt_opencode': 0,
        'deepseek': 0,
        'other': 0,
    }
    total_tokens = 0
    total_ai_hours = 0.0
    total_cost_usd = 0.0

    for current_date in all_dates:
        categories = {
            'cursor': cursor.get(current_date, 0),
            'glm': glm.get(current_date, 0),
            'claude': claude.get(current_date, 0),
            'gpt_opencode': gpt_opencode.get(current_date, 0),
            'deepseek': deepseek.get(current_date, 0),
            'other': other.get(current_date, 0),
        }
        day_total = sum(categories.values())
        ai_hours = round((daily_active_seconds or {}).get(current_date, 0.0) / 3600, 2)
        total_ai_hours += ai_hours
        total_tokens += day_total
        for key, value in categories.items():
            summary_totals[key] += value

        entry: dict[str, object] = {
            'date': current_date.isoformat(),
            'categories': categories,
            'total_tokens': day_total,
            'ai_hours': ai_hours,
        }
        if has_costs:
            cost = round((daily_costs or {}).get(current_date, 0.0), 2)
            total_cost_usd += cost
            entry['cost_usd'] = cost
        daily_entries.append(entry)

    meta: dict[str, object] = {
        'version': 1,
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'start_date': start_date,
        'end_date': end_date,
        'days': len(all_dates),
        'currency': 'USD',
    }
    summary: dict[str, object] = {
        'total_tokens': total_tokens,
        'total_ai_hours': round(total_ai_hours, 2),
        'categories': summary_totals,
    }
    payload: dict[str, object] = {
        'meta': meta,
        'summary': summary,
        'daily': daily_entries,
    }
    if has_costs:
        summary['total_cost_usd'] = round(total_cost_usd, 2)
    return payload


def write_eink_dashboard_payload(
    cursor: DailyTokens,
    glm: DailyTokens,
    claude: DailyTokens,
    gpt_opencode: DailyTokens,
    deepseek: DailyTokens,
    other: DailyTokens,
    start_date: str,
    end_date: str,
    daily_costs: DailyCosts | None = None,
    daily_active_seconds: DailyActiveSeconds | None = None,
    output_path: str | None = None,
) -> dict[str, object]:
    payload = build_eink_dashboard_payload(
        cursor,
        glm,
        claude,
        gpt_opencode,
        deepseek,
        other,
        start_date,
        end_date,
        daily_costs=daily_costs,
        daily_active_seconds=daily_active_seconds,
    )
    target = output_path or os.path.join(SCRIPT_DIR, OUTPUT_EINK_JSON)
    with open(target, 'w') as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return payload

def parse_args():
    parser = argparse.ArgumentParser(description='Aggregate token usage across AI coding tools')
    parser.add_argument('-d', '--days', type=int, default=30, help='Analyze the last N days (default: 30)')
    parser.add_argument('-s', '--since', type=str, help='Start date (YYYYMMDD or YYYY-MM-DD)')
    parser.add_argument('--no-cost', action='store_true', help='Skip USD cost estimation')
    parser.add_argument('--skip-desktop-chart', action='store_true', help='Skip the desktop PNG chart; output text and JSON only')
    return parser.parse_args()

def export_codex(start_date):
    result = subprocess.run(
        ['npx', '-y', '@ccusage/codex@latest', '--json', '-s', start_date.replace('-', '')],
        capture_output=True,
        text=True,
        cwd=SCRIPT_DIR,
        timeout=180,
    )
    if result.returncode != 0:
        print(f"cc-usage error: {result.stderr}", file=sys.stderr)
        return None

    raw = result.stdout.strip()
    if not raw:
        print("cc-usage returned no JSON because stdout was empty. Possible causes:", file=sys.stderr)
        print("  - First npx run may need package installation. Try: npx @ccusage/codex@latest --json -s 20260302", file=sys.stderr)
        if result.stderr:
            print(f"stderr: {result.stderr[:500]}", file=sys.stderr)
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"cc-usage returned non-JSON output: {e}", file=sys.stderr)
        print(f"First 500 stdout characters: {raw[:500]!r}", file=sys.stderr)
        return None
    with open(os.path.join(SCRIPT_DIR, 'usage.json'), 'w') as f:
        json.dump(data, f, indent=2)
    return data

def export_cursor(cookie_str, start_ts, end_ts):
    url = f'https://cursor.com/api/dashboard/export-usage-events-csv?startDate={start_ts}&endDate={end_ts}'
    headers = {
        'User-Agent': 'Mozilla/5.0',
        'Cookie': cookie_str,
    }
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    
    csv_path = os.path.join(SCRIPT_DIR, 'cursor.csv')
    with open(csv_path, 'w') as f:
        f.write(resp.text)
    return csv_path

def export_glm(bearer_token, start_date, end_date):
    """Export GLM usage data, splitting into monthly chunks to avoid API limits."""
    start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
    end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()

    # Split into ~30-day chunks
    chunks: list[tuple[str, str]] = []
    current = start_dt
    while current <= end_dt:
        chunk_end = min(current + timedelta(days=29), end_dt)
        chunks.append((current.isoformat(), chunk_end.isoformat()))
        current = chunk_end + timedelta(days=1)

    merged_x_time: list[str] = []
    merged_tokens: list[int | None] = []

    headers = {
        'Authorization': f'Bearer {bearer_token}',
        'User-Agent': 'Mozilla/5.0',
    }

    for chunk_start, chunk_end in chunks:
        url = f'https://api.z.ai/api/monitor/usage/model-usage?startTime={chunk_start}+00:00:00&endTime={chunk_end}+23:59:59'
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()

        body = resp.json()
        if not body.get('success', False) or 'data' not in body:
            print(f"  GLM API warning for {chunk_start}..{chunk_end}: {body.get('msg', 'unknown error')}")
            continue

        chunk_data = body['data']
        chunk_times = chunk_data.get('x_time', [])
        chunk_tokens = chunk_data.get('tokensUsage', [])

        # Deduplicate: skip first entry if it overlaps with previous chunk's last date
        if merged_x_time and chunk_times:
            last_merged_date = merged_x_time[-1].split(' ')[0]
            first_chunk_date = chunk_times[0].split(' ')[0]
            if last_merged_date == first_chunk_date:
                chunk_times = chunk_times[1:]
                chunk_tokens = chunk_tokens[1:]

        merged_x_time.extend(chunk_times)
        merged_tokens.extend(chunk_tokens)

    result = {'code': 200, 'msg': 'OK', 'success': True,
              'data': {'x_time': merged_x_time, 'tokensUsage': merged_tokens}}

    json_path = os.path.join(SCRIPT_DIR, 'glm.json')
    with open(json_path, 'w') as f:
        json.dump(result, f, indent=2)
    return result

def load_codex(path=None):
    if path is None:
        path = os.path.join(SCRIPT_DIR, 'usage.json')
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        data = json.load(f)
    daily = {}
    for entry in data.get('daily', []):
        dt = datetime.strptime(entry['date'], '%b %d, %Y')
        daily[dt.date()] = entry['totalTokens']
    return daily

def load_cursor(path=None):
    if path is None:
        path = os.path.join(SCRIPT_DIR, 'cursor.csv')
    if not os.path.exists(path):
        return {}
    daily = defaultdict(int)
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                dt = datetime.fromisoformat(row['Date'].replace('Z', '+00:00'))
                total = int(row['Total Tokens']) if row['Total Tokens'] else 0
                daily[dt.date()] += total
            except (KeyError, ValueError):
                continue
    return dict(daily)

def load_glm(path=None):
    if path is None:
        path = os.path.join(SCRIPT_DIR, 'glm.json')
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        data = json.load(f)
    if not data.get('success', False) or 'data' not in data:
        return {}
    daily = defaultdict(int)
    for ts, tokens in zip(data['data']['x_time'], data['data']['tokensUsage']):
        if tokens is None or ts in (None, ''):
            continue
        try:
            dt = datetime.fromisoformat(str(ts).replace(' ', 'T'))
        except ValueError:
            continue
        daily[dt.date()] += tokens
    return dict(daily)


def iter_claude_session_files(project_dirs: list[Path] | None = None):
    roots = project_dirs or CLAUDE_PROJECT_DIRS
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for path in root.glob('**/*.jsonl'):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield resolved


def parse_claude_timestamp(timestamp: str | None) -> datetime | None:
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp.replace('Z', '+00:00')).astimezone().replace(tzinfo=None)
    except ValueError:
        return None


def normalize_claude_model_id(model_id: str, speed: str) -> str:
    model_lower = (model_id or 'unknown').lower()
    if speed == 'fast' and 'claude' in model_lower and 'opus' in model_lower:
        return 'claude-opus-4-6-fast'
    return model_lower


def get_claude_record_identity(raw: dict[str, object], path: Path) -> tuple[str, str]:
    message_obj = raw.get('message')
    message = message_obj if isinstance(message_obj, dict) else {}
    message_id = message.get('id')
    request_id = raw.get('requestId')
    uuid = raw.get('uuid')
    timestamp = raw.get('timestamp', '')
    if message_id:
        return ('message', str(message_id))
    if request_id:
        return ('request', str(request_id))
    if uuid:
        return ('uuid', str(uuid))
    return ('path-time', f'{path}:{timestamp}')


def split_claude_cache_write_tokens(record: ClaudeUsageRecord) -> tuple[int, int, int]:
    cache_write_1h = record['cache_write_1h']
    cache_write_5m = record['cache_write_5m']
    flat_cache_write = record['cache_write']
    nested_total = cache_write_5m + cache_write_1h
    if nested_total < flat_cache_write:
        cache_write_5m += flat_cache_write - nested_total
        nested_total = cache_write_5m + cache_write_1h
    elif nested_total == 0:
        cache_write_5m = flat_cache_write
        nested_total = flat_cache_write
    return cache_write_5m, cache_write_1h, nested_total


def iter_claude_usage_records(project_dirs: list[Path] | None = None, start_date: date | None = None, end_date: date | None = None):
    seen_keys: set[tuple[str, str]] = set()
    for path in iter_claude_session_files(project_dirs=project_dirs):
        try:
            with path.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if raw.get('type') != 'assistant':
                        continue
                    message = raw.get('message', {})
                    usage = message.get('usage', {})
                    if not usage:
                        continue
                    timestamp = parse_claude_timestamp(raw.get('timestamp'))
                    if not timestamp:
                        continue
                    record_date = timestamp.date()
                    if start_date and record_date < start_date:
                        continue
                    if end_date and record_date > end_date:
                        continue
                    dedup_key = get_claude_record_identity(raw, path)
                    if dedup_key in seen_keys:
                        continue
                    seen_keys.add(dedup_key)
                    record: ClaudeUsageRecord = {
                        'time': timestamp,
                        'model': message.get('model', '') or 'unknown',
                        'speed': usage.get('speed', 'standard'),
                        'input': int(usage.get('input_tokens', 0) or 0),
                        'output': int(usage.get('output_tokens', 0) or 0),
                        'cache_read': int(usage.get('cache_read_input_tokens', 0) or 0),
                        'cache_write': int(usage.get('cache_creation_input_tokens', 0) or 0),
                        'cache_write_5m': int(usage.get('cache_creation', {}).get('ephemeral_5m_input_tokens', 0) or 0),
                        'cache_write_1h': int(usage.get('cache_creation', {}).get('ephemeral_1h_input_tokens', 0) or 0),
                    }
                    yield record
        except OSError:
            continue


def load_claude_code(project_dirs: list[Path] | None = None, start_date: date | None = None, end_date: date | None = None):
    daily = defaultdict(int)
    for record in iter_claude_usage_records(project_dirs=project_dirs, start_date=start_date, end_date=end_date):
        _, _, cache_write_total = split_claude_cache_write_tokens(record)
        total = record['input'] + record['output'] + record['cache_read'] + cache_write_total
        if total <= 0:
            continue
        daily[record['time'].date()] += total
    return dict(daily)


def load_claude_code_detailed(project_dirs: list[Path] | None = None, start_date: date | None = None, end_date: date | None = None):
    daily_models = defaultdict(lambda: defaultdict(lambda: {
        'input': 0,
        'output': 0,
        'cache_read': 0,
        'cache_write': 0,
        'cache_write_1h': 0,
    }))
    for record in iter_claude_usage_records(project_dirs=project_dirs, start_date=start_date, end_date=end_date):
        model_id = normalize_claude_model_id(record['model'], record['speed'])
        dt = record['time'].date()
        cache_write_5m, cache_write_1h, _ = split_claude_cache_write_tokens(record)
        daily_models[dt][model_id]['input'] += record['input']
        daily_models[dt][model_id]['output'] += record['output']
        daily_models[dt][model_id]['cache_read'] += record['cache_read']
        daily_models[dt][model_id]['cache_write'] += cache_write_5m
        daily_models[dt][model_id]['cache_write_1h'] += cache_write_1h
    return dict(daily_models)

def empty_opencode_totals() -> dict[str, DailyTokens]:
    return {
        'anthropic': {},
        'gpt_opencode': {},
        'deepseek': {},
        'opencode_other': {},
    }


def load_opencode_from_db(exclude_glm: bool = True, start_ts: int | None = None, end_ts: int | None = None) -> dict[str, DailyTokens]:
    totals = {
        'anthropic': defaultdict(int),
        'gpt_opencode': defaultdict(int),
        'deepseek': defaultdict(int),
        'opencode_other': defaultdict(int),
    }
    if not OPENCODE_DB.exists():
        return empty_opencode_totals()

    try:
        conn = sqlite3.connect(f'file:{OPENCODE_DB}?mode=ro', uri=True)
        cur = conn.cursor()
        query = "SELECT time_created, data FROM message WHERE json_extract(data, '$.role') = 'assistant'"
        params: list[int] = []
        if start_ts is not None:
            query += ' AND time_created >= ?'
            params.append(start_ts)
        if end_ts is not None:
            query += ' AND time_created < ?'
            params.append(end_ts)
        cur.execute(query, params)
        for time_created, data_str in cur:
            try:
                msg = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            bucket = classify_opencode_bucket(msg.get('providerID', ''), msg.get('modelID', ''), exclude_glm=exclude_glm)
            if bucket is None:
                continue
            tokens = msg.get('tokens', {})
            total = int(tokens.get('input', 0) or 0) + int(tokens.get('output', 0) or 0)
            cache = tokens.get('cache', {})
            if isinstance(cache, dict):
                total += int(cache.get('read', 0) or 0) + int(cache.get('write', 0) or 0)
            total += int(tokens.get('reasoning', 0) or 0)
            if total <= 0:
                continue
            created_ts = msg.get('time', {}).get('created') or time_created
            if not created_ts:
                continue
            totals[bucket][datetime.fromtimestamp(created_ts / 1000).date()] += total
        conn.close()
    except (sqlite3.Error, TypeError, ValueError):
        return empty_opencode_totals()

    return {key: dict(value) for key, value in totals.items()}


def load_opencode(exclude_glm: bool = True, start_ts: int | None = None, end_ts: int | None = None):
    """Load OpenCode token usage, using opencode_skill archive support when available."""
    configure_opencode_skill_path()
    try:
        ocs_query = importlib.import_module('opencode_skill.query')
    except ImportError as e:
        print(f"opencode_skill not importable: {e}. Falling back to the main local OpenCode DB.", file=sys.stderr)
        return load_opencode_from_db(exclude_glm=exclude_glm, start_ts=start_ts, end_ts=end_ts)

    totals = {
        'anthropic': defaultdict(int),
        'gpt_opencode': defaultdict(int),
        'deepseek': defaultdict(int),
        'opencode_other': defaultdict(int),
    }
    for m in ocs_query.iter_assistant_messages(since_ms=start_ts, until_ms=end_ts):
        bucket = classify_opencode_bucket(m.provider or '', m.model or '', exclude_glm=exclude_glm)
        if bucket is None:
            continue
        total = m.tokens_input + m.tokens_output + m.tokens_reasoning + m.tokens_cache_read + m.tokens_cache_write
        if total <= 0:
            continue
        totals[bucket][datetime.fromtimestamp(m.time_created / 1000).date()] += total

    return {key: dict(value) for key, value in totals.items()}


def load_opencode_detailed(exclude_glm: bool = True, start_ts: int | None = None, end_ts: int | None = None):
    """
    Load per-model token breakdown for cost calculation.
    Returns: {date: {model_id: {input, output, cache_read, cache_write}}}
    """
    daily_models = defaultdict(lambda: defaultdict(lambda: {'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0}))
    if not OPENCODE_DB.exists():
        return dict(daily_models)

    try:
        conn = sqlite3.connect(f'file:{OPENCODE_DB}?mode=ro', uri=True)
        cur = conn.cursor()
        query = "SELECT time_created, data FROM message WHERE json_extract(data, '$.role') = 'assistant'"
        params: list[int] = []
        if start_ts is not None:
            query += ' AND time_created >= ?'
            params.append(start_ts)
        if end_ts is not None:
            query += ' AND time_created < ?'
            params.append(end_ts)
        cur.execute(query, params)
        for time_created, data_str in cur:
            try:
                msg = json.loads(data_str)
                provider_id = msg.get('providerID', '')
                model_id = msg.get('modelID', '') or 'unknown'
                if classify_opencode_bucket(provider_id, model_id, exclude_glm=exclude_glm) is None:
                    continue
                tokens = msg.get('tokens', {})
                inp = tokens.get('input', 0)
                out = tokens.get('output', 0)
                cache = tokens.get('cache', {})
                cache_r = cache.get('read', 0)
                cache_w = cache.get('write', 0)
                if inp + out + cache_r + cache_w == 0:
                    continue
                created_ts = msg.get('time', {}).get('created') or time_created
                if not created_ts:
                    continue
                dt = datetime.fromtimestamp(created_ts / 1000).date()
                daily_models[dt][model_id]['input'] += inp
                daily_models[dt][model_id]['output'] += out
                daily_models[dt][model_id]['cache_read'] += cache_r
                daily_models[dt][model_id]['cache_write'] += cache_w
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        conn.close()
    except sqlite3.Error:
        pass
    return dict(daily_models)


def parse_codex_event_time(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp.replace('Z', '+00:00')).astimezone().replace(tzinfo=None)


def split_interval_by_day(start: datetime, end: datetime) -> list[tuple[date, TimeInterval]]:
    if end < start:
        return []

    pieces: list[tuple[date, TimeInterval]] = []
    current_start = start
    while current_start.date() < end.date():
        next_midnight = datetime.combine(current_start.date() + timedelta(days=1), datetime.min.time())
        pieces.append((current_start.date(), (current_start, next_midnight)))
        current_start = next_midnight
    if end > current_start or not pieces:
        pieces.append((current_start.date(), (current_start, end)))
    return pieces


def merge_intervals(intervals: list[TimeInterval]) -> list[TimeInterval]:
    if not intervals:
        return []

    sorted_intervals = sorted(intervals, key=lambda item: item[0])
    merged: list[TimeInterval] = []
    current_start, current_end = sorted_intervals[0]

    for start, end in sorted_intervals[1:]:
        if start <= current_end:
            if end > current_end:
                current_end = end
        else:
            merged.append((current_start, current_end))
            current_start, current_end = start, end

    merged.append((current_start, current_end))
    return merged


def build_opencode_turn_intervals(messages: list[OpencodeTurnMessage], exclude_glm: bool = True) -> list[TimeInterval]:
    by_session: dict[str, list[OpencodeTurnMessage]] = defaultdict(list)
    for message in messages:
        role = message.get('role')
        if role not in {'user', 'assistant'}:
            continue
        if role == 'assistant':
            if classify_opencode_bucket(message.get('provider_id', ''), message.get('model_id', ''), exclude_glm=exclude_glm) is None:
                continue
        by_session[message['session_id']].append(message)

    intervals: list[TimeInterval] = []
    for session_messages in by_session.values():
        pending_user_start: datetime | None = None
        last_assistant_time: datetime | None = None
        for message in sorted(session_messages, key=lambda item: item['time']):
            role = message['role']
            timestamp = message['time']
            if role == 'user':
                if pending_user_start is not None and last_assistant_time is not None and last_assistant_time >= pending_user_start:
                    intervals.append((pending_user_start, last_assistant_time))
                pending_user_start = timestamp
                last_assistant_time = None
            elif pending_user_start is not None:
                last_assistant_time = timestamp

        if pending_user_start is not None and last_assistant_time is not None and last_assistant_time >= pending_user_start:
            intervals.append((pending_user_start, last_assistant_time))

    return intervals


def load_opencode_turn_intervals(exclude_glm: bool = True, start_ts: int | None = None, end_ts: int | None = None) -> list[TimeInterval]:
    if not OPENCODE_DB.exists():
        return []

    messages: list[OpencodeTurnMessage] = []
    try:
        conn = sqlite3.connect(f'file:{OPENCODE_DB}?mode=ro', uri=True)
        cur = conn.cursor()
        query = """
            SELECT session_id, time_created, data
            FROM message
            WHERE json_extract(data, '$.role') IN ('user', 'assistant')
        """
        params: list[int] = []
        if start_ts is not None:
            query += ' AND time_created >= ?'
            params.append(start_ts)
        if end_ts is not None:
            query += ' AND time_created < ?'
            params.append(end_ts)
        query += ' ORDER BY session_id, time_created'
        cur.execute(query, params)
        for session_id, time_created, data_str in cur:
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            messages.append({
                'session_id': session_id,
                'time': datetime.fromtimestamp(time_created / 1000),
                'role': data.get('role'),
                'provider_id': data.get('providerID', ''),
                'model_id': data.get('modelID', ''),
            })
        conn.close()
    except sqlite3.Error:
        return []

    return build_opencode_turn_intervals(messages, exclude_glm=exclude_glm)


def build_codex_turn_intervals(events: list[CodexTurnEvent]) -> list[TimeInterval]:
    intervals: list[TimeInterval] = []
    pending_user_start: datetime | None = None
    last_event_time: datetime | None = None

    sorted_events = sorted(events, key=lambda item: item['time'])
    for event in sorted_events:
        timestamp = event['time']
        last_event_time = timestamp
        if event.get('type') != 'event_msg':
            continue

        payload_type = event.get('payload_type')
        if payload_type == 'user_message':
            if pending_user_start is not None and timestamp >= pending_user_start:
                intervals.append((pending_user_start, timestamp))
            pending_user_start = timestamp
        elif payload_type == 'task_complete' and pending_user_start is not None and timestamp >= pending_user_start:
            intervals.append((pending_user_start, timestamp))
            pending_user_start = None

    if pending_user_start is not None and last_event_time is not None and last_event_time >= pending_user_start:
        intervals.append((pending_user_start, last_event_time))

    return intervals


def load_codex_turn_intervals(start_date: str, end_date: str) -> list[TimeInterval]:
    start_d = datetime.strptime(start_date, '%Y-%m-%d').date()
    end_d = datetime.strptime(end_date, '%Y-%m-%d').date()
    sessions_root = Path.home() / '.codex' / 'sessions'
    if not sessions_root.exists():
        return []

    intervals: list[TimeInterval] = []
    current = start_d
    while current <= end_d:
        day_dir = sessions_root / f'{current.year:04d}' / f'{current.month:02d}' / f'{current.day:02d}'
        if day_dir.exists():
            for session_file in sorted(day_dir.glob('*.jsonl')):
                events: list[CodexTurnEvent] = []
                try:
                    with session_file.open() as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                raw = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            timestamp = raw.get('timestamp')
                            if not timestamp:
                                continue
                            payload = raw.get('payload', {})
                            events.append({
                                'type': raw.get('type'),
                                'payload_type': payload.get('type'),
                                'time': parse_codex_event_time(timestamp),
                            })
                except OSError:
                    continue
                intervals.extend(build_codex_turn_intervals(events))
        current += timedelta(days=1)

    return intervals


def compute_daily_ai_active_seconds(opencode_intervals: list[TimeInterval], codex_intervals: list[TimeInterval], start_date: str, end_date: str) -> DailyActiveSeconds:
    start_dt = datetime.strptime(start_date, '%Y-%m-%d')
    end_dt = datetime.combine(datetime.strptime(end_date, '%Y-%m-%d').date() + timedelta(days=1), datetime.min.time())

    per_day_seconds: dict[date, float] = defaultdict(float)
    for interval_start, interval_end in opencode_intervals + codex_intervals:
        clipped_start = max(interval_start, start_dt)
        clipped_end = min(interval_end, end_dt)
        if clipped_end < clipped_start:
            continue
        for day, piece in split_interval_by_day(clipped_start, clipped_end):
            per_day_seconds[day] += (piece[1] - piece[0]).total_seconds()

    return {day: total for day, total in per_day_seconds.items() if total > 0}


def calc_codex_cost(usage_path=None) -> DailyCosts:
    """Codex cost from usage.json, returns {date: cost_usd}."""
    path = usage_path or os.path.join(SCRIPT_DIR, 'usage.json')
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        data = json.load(f)
    result = {}
    for entry in data.get('daily', []):
        dt = datetime.strptime(entry['date'], '%b %d, %Y').date()
        total_cost = 0.0
        for model_name, m in entry.get('models', {}).items():
            p = get_pricing(model_name)
            if p:
                total_cost += calc_cost(
                    p,
                    input_tokens=m.get('inputTokens', 0),
                    output_tokens=m.get('outputTokens', 0) + m.get('reasoningOutputTokens', 0),
                    cached_tokens=m.get('cachedInputTokens', 0),
                )
        result[dt] = total_cost
    return result


def calc_glm_cost(glm_daily: DailyTokens) -> DailyCosts:
    """GLM cost using glm-5 default, total × (0.7×input + 0.3×output) assumption."""
    p = get_pricing('glm-5')
    if not p:
        return {d: 0.0 for d in glm_daily}
    avg_per_token = (GLM_INPUT_RATIO * p['input'] + GLM_OUTPUT_RATIO * p['output']) / 1_000_000
    return {d: total * avg_per_token for d, total in glm_daily.items()}


def calc_opencode_cost(detailed: DailyModelTokens) -> DailyCosts:
    """OpenCode cost from per-model breakdown. Returns {date: cost_usd}."""
    result = defaultdict(float)
    for dt, models in detailed.items():
        for model_id, tok in models.items():
            p = get_pricing(model_id)
            if p:
                result[dt] += calc_cost(
                    p,
                    input_tokens=tok['input'] + tok['cache_read'],
                    output_tokens=tok['output'],
                    cached_tokens=tok['cache_read'],
                    cache_write_tokens=tok['cache_write'],
                )
    return dict(result)


def calc_claude_code_cost(detailed: DailyModelTokens) -> DailyCosts:
    result = defaultdict(float)
    for dt, models in detailed.items():
        for model_id, tok in models.items():
            p = get_pricing(model_id)
            if p:
                result[dt] += calc_cost(
                    p,
                    input_tokens=tok['input'] + tok['cache_read'],
                    output_tokens=tok['output'],
                    cached_tokens=tok['cache_read'],
                    cache_write_tokens=tok.get('cache_write', 0),
                    cache_write_1h_tokens=tok.get('cache_write_1h', 0),
                )
    return dict(result)


def compute_daily_costs(start_date: str, end_date: str, start_ts: int, end_ts: int, codex: DailyTokens, glm: DailyTokens) -> DailyCosts | None:
    start_d = datetime.strptime(start_date, '%Y-%m-%d').date()
    end_d = datetime.strptime(end_date, '%Y-%m-%d').date()
    daily_costs = defaultdict(float)
    for d, v in calc_codex_cost().items():
        if start_d <= d <= end_d:
            daily_costs[d] += v
    for d, v in calc_glm_cost(glm).items():
        if start_d <= d <= end_d:
            daily_costs[d] += v
    opencode_detailed = load_opencode_detailed(exclude_glm=True, start_ts=start_ts, end_ts=end_ts)
    for d, v in calc_opencode_cost(opencode_detailed).items():
        if start_d <= d <= end_d:
            daily_costs[d] += v
    claude_code_detailed = load_claude_code_detailed(start_date=start_d, end_date=end_d)
    for d, v in calc_claude_code_cost(claude_code_detailed).items():
        if start_d <= d <= end_d:
            daily_costs[d] += v
    return dict(daily_costs)


def generate_dashboard_desktop(cursor, glm, claude, gpt_opencode, deepseek, other, start_date, end_date, daily_costs=None, daily_active_seconds: DailyActiveSeconds | None = None):
    start = datetime.strptime(start_date, '%Y-%m-%d').date()
    end = datetime.strptime(end_date, '%Y-%m-%d').date()
    
    all_dates = sorted(
        set(cursor) | set(glm) | set(claude) | set(gpt_opencode) | set(deepseek) | set(other) | set(daily_active_seconds or {})
    )
    all_dates = [d for d in all_dates if start <= d <= end]
    
    has_costs = daily_costs is not None
    grand_total = sum(cursor.get(d, 0) + glm.get(d, 0) + claude.get(d, 0) + gpt_opencode.get(d, 0) + deepseek.get(d, 0) + other.get(d, 0) for d in all_dates)
    cost_total = sum((daily_costs or {}).get(d, 0.0) for d in all_dates) if has_costs else 0.0
    active_hours_total = sum((daily_active_seconds or {}).get(d, 0.0) for d in all_dates) / 3600

    dates = [datetime.combine(d, datetime.min.time()) for d in all_dates]
    date_nums = mdates.date2num(dates)
    
    # Richer palette for desktop
    PALETTE = {
        'Cursor': '#f97316',
        'GLM': '#22c55e',
        'Claude': '#f59e0b',
        'GPT': '#8b5cf6',
        'DeepSeek': '#3b82f6',
        'Other': '#64748b',
    }
    
    fig, (ax, ax_active) = plt.subplots(2, 1, figsize=(14, 10), sharex=True, height_ratios=[2, 1])
    
    width = 0.8
    vals = {
        'Cursor': [cursor.get(d.date(), 0) / 1e8 for d in dates],
        'GLM': [glm.get(d.date(), 0) / 1e8 for d in dates],
        'Claude': [claude.get(d.date(), 0) / 1e8 for d in dates],
        'GPT': [gpt_opencode.get(d.date(), 0) / 1e8 for d in dates],
        'DeepSeek': [deepseek.get(d.date(), 0) / 1e8 for d in dates],
        'Other': [other.get(d.date(), 0) / 1e8 for d in dates],
    }
    
    bottom = [0.0] * len(dates)
    for label, color in PALETTE.items():
        ax.bar(date_nums, vals[label], width, bottom=bottom, label=label, color=color)
        bottom = [b + v for b, v in zip(bottom, vals[label])]
    
    ax.set_ylabel('Tokens (100M)', fontproperties=FONT_EN)
    total_yi = grand_total / 1e8
    title = f'{start_date} ~ {end_date} - Total {total_yi:.2f} x 100M tokens'
    if has_costs:
        title += f' | Est. ${cost_total:.2f}'
    ax.set_title(title, fontproperties=FONT_ZH)
    ax.legend(loc='upper left', prop=FONT_EN)
    
    active_hour_vals = [(daily_active_seconds or {}).get(d.date(), 0.0) / 3600 for d in dates]
    ax_active.bar(date_nums, active_hour_vals, width=0.8, color=PALETTE['GPT'])
    ax_active.set_ylabel('Hours', fontproperties=FONT_EN)
    active_title = f'AI Active Time (cumulative est.) - Total {active_hours_total:.2f} hours'
    ax_active.set_title(active_title, fontproperties=FONT_ZH)
    
    ax_active.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    ax_active.xaxis.set_major_locator(mdates.AutoDateLocator())
    
    for a in [ax, ax_active]:
        for label in a.get_xticklabels() + a.get_yticklabels():
            label.set_fontproperties(FONT_EN)
    
    plt.setp(ax_active.get_xticklabels(), rotation=45, ha='right')
    plt.tight_layout()
    
    output_path = os.path.join(SCRIPT_DIR, 'token_usage_dashboard.png')
    plt.savefig(output_path, dpi=150)
    print(f"Desktop chart saved to {output_path}")
    plt.close(fig)

def generate_dashboard(cursor, glm, claude, gpt_opencode, deepseek, other, start_date, end_date, daily_costs=None, daily_active_seconds: DailyActiveSeconds | None = None, *, skip_desktop_chart: bool = False):
    start = datetime.strptime(start_date, '%Y-%m-%d').date()
    end = datetime.strptime(end_date, '%Y-%m-%d').date()
    
    all_dates = sorted(
        set(cursor) | set(glm) | set(claude) | set(gpt_opencode) | set(deepseek) | set(other) | set(daily_active_seconds or {})
    )
    all_dates = [d for d in all_dates if start <= d <= end]
    
    has_costs = daily_costs is not None
    cols = ('Date', 'Cursor', 'GLM', 'Claude', 'GPT', 'DeepSeek', 'Other', 'Total', 'AI Hours')
    if has_costs:
        cols = cols + ('Est. $',)
    col_width = 12
    header = ' '.join(f'{c:>{col_width}}' for c in cols)
    print(f"\n{header}")
    print("-" * len(header))
    grand_total = 0
    cost_total = 0.0
    active_hours_total = 0.0
    for d in all_dates:
        u = cursor.get(d, 0)
        g = glm.get(d, 0)
        cl = claude.get(d, 0)
        go = gpt_opencode.get(d, 0)
        ds = deepseek.get(d, 0)
        o = other.get(d, 0)
        total = u + g + cl + go + ds + o
        grand_total += total
        active_hours = (daily_active_seconds or {}).get(d, 0.0) / 3600
        active_hours_total += active_hours
        cost = daily_costs.get(d, 0.0) if has_costs else 0.0
        cost_total += cost
        row = f"{d!s:>{col_width}} {u:>{col_width},} {g:>{col_width},} {cl:>{col_width},} {go:>{col_width},} {ds:>{col_width},} {o:>{col_width},} {total:>{col_width},} {active_hours:>{col_width}.2f}"
        if has_costs:
            row += f" ${cost:>{col_width - 2}.2f}"
        print(row)
    
    print("-" * len(header))
    totals = (
        sum(cursor.get(d, 0) for d in all_dates),
        sum(glm.get(d, 0) for d in all_dates),
        sum(claude.get(d, 0) for d in all_dates),
        sum(gpt_opencode.get(d, 0) for d in all_dates),
        sum(deepseek.get(d, 0) for d in all_dates),
        sum(other.get(d, 0) for d in all_dates),
    )
    total_row = f"{'TOTAL':>{col_width}} {totals[0]:>{col_width},} {totals[1]:>{col_width},} {totals[2]:>{col_width},} {totals[3]:>{col_width},} {totals[4]:>{col_width},} {totals[5]:>{col_width},} {grand_total:>{col_width},} {active_hours_total:>{col_width}.2f}"
    if has_costs:
        total_row += f" ${cost_total:>{col_width - 2}.2f}"
    print(total_row)
    print(f"\nAI Active Time (cumulative est.): {active_hours_total:.2f} hours")
    if has_costs:
        print(f"\nEst. Total (API equiv.): ${cost_total:.2f}")

    if not skip_desktop_chart:
        generate_dashboard_desktop(cursor, glm, claude, gpt_opencode, deepseek, other, start_date, end_date, daily_costs, daily_active_seconds)
    return write_eink_dashboard_payload(cursor, glm, claude, gpt_opencode, deepseek, other, start_date, end_date, daily_costs, daily_active_seconds)


def build_latest_dashboard_payload(days: int = 30, *, no_cost: bool = False, skip_desktop_chart: bool = True) -> dict[str, object]:
    load_env()

    start_date, end_date, start_ts, end_ts = get_date_range(days)
    print(f"Date range: {start_date} to {end_date} ({days} days)")

    print("Exporting Codex data...")
    export_codex(start_date)
    codex = load_codex()

    cursor_cookie = os.environ.get('CURSOR_COOKIE', '')
    glm_token = os.environ.get('GLM_BEARER_TOKEN', '')

    if cursor_cookie:
        print("Exporting Cursor data...")
        try:
            export_cursor(cursor_cookie, start_ts, end_ts)
        except Exception as e:
            print(f"Failed to export Cursor: {e}")
    cursor = load_cursor()

    if glm_token:
        print("Exporting GLM data...")
        try:
            export_glm(glm_token, start_date, end_date)
        except Exception as e:
            print(f"Failed to export GLM: {e}")
    glm = load_glm()

    print("Loading Claude Code data...")
    start_d = datetime.strptime(start_date, '%Y-%m-%d').date()
    end_d = datetime.strptime(end_date, '%Y-%m-%d').date()
    start_day_ts = date_to_epoch_ms(start_d)
    next_day_ts = date_to_epoch_ms(end_d + timedelta(days=1))

    claude_code = load_claude_code(start_date=start_d, end_date=end_d)

    print("Loading OpenCode data (excluding GLM, split: Anthropic / GPT / DeepSeek / other)...")
    opencode_data = load_opencode(exclude_glm=True, start_ts=start_day_ts, end_ts=next_day_ts)
    anthropic = opencode_data['anthropic']
    gpt_opencode = opencode_data['gpt_opencode']
    opencode_deepseek = opencode_data['deepseek']
    opencode_other = opencode_data['opencode_other']

    claude_combined = defaultdict(int)
    for d, v in claude_code.items():
        claude_combined[d] += v
    for d, v in anthropic.items():
        claude_combined[d] += v

    print("Estimating AI active time from OpenCode + Codex turn windows...")
    opencode_turn_intervals = load_opencode_turn_intervals(exclude_glm=True, start_ts=start_day_ts, end_ts=next_day_ts)
    codex_turn_intervals = load_codex_turn_intervals(start_date, end_date)
    daily_active_seconds = compute_daily_ai_active_seconds(opencode_turn_intervals, codex_turn_intervals, start_date, end_date)

    daily_costs = compute_daily_costs(start_date, end_date, start_day_ts, next_day_ts, codex, glm) if not no_cost else None
    gpt_combined = merge_daily_tokens(gpt_opencode, codex)
    return generate_dashboard(cursor, glm, dict(claude_combined), gpt_combined, opencode_deepseek, opencode_other, start_date, end_date, daily_costs, daily_active_seconds=daily_active_seconds, skip_desktop_chart=skip_desktop_chart)

def main():
    args = parse_args()
    if args.since:
        s = args.since.replace('-', '')
        start = datetime.strptime(s, '%Y%m%d')
        days = (datetime.now() - start).days + 1
    else:
        days = args.days

    build_latest_dashboard_payload(days=days, no_cost=args.no_cost, skip_desktop_chart=args.skip_desktop_chart)

if __name__ == '__main__':
    main()
