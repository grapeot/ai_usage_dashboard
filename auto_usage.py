#!/usr/bin/env python3
"""
Export and aggregate token usage from Codex, Cursor, GLM, and OpenCode.
Supports API-equivalent USD cost estimation. See docs/rfc.md.
"""
import json
import csv
import importlib
import re
import subprocess
import os
import sys
import argparse
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import TypedDict, cast
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.font_manager import FontProperties
import requests

import antigravity_usage as _antigravity_usage
import dsh_usage as _dsh_usage
import grok_usage as _grok_usage
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
OPENCODE_BATCH_DB = Path(os.environ['OPENCODE_BATCH_DB']) if os.environ.get('OPENCODE_BATCH_DB') else None
CLAUDE_PROJECT_DIRS = [
    Path.home() / '.claude' / 'projects',
    Path.home() / '.config' / 'claude' / 'projects',
]

GLM_PROVIDERS = ('zai-coding-plan', 'zai-coding-plan/glm-4.7')
# GLM models routed through non-Z.ai providers (e.g. ollama-cloud/glm-5.2)
# are counted in the OpenCode GLM bucket because the Z.ai usage API does not
# see them. Only zai-coding-plan GLM is excluded to avoid double-counting
# against the GLM/Z.ai usage API totals.
GLM_MODEL_PREFIXES = ('glm-',)

# GLM / Z.ai coding-plan quota endpoint. The web dashboard calls
# /api/monitor/usage/quota/limit (no query params) with the same bearer token
# used for /api/monitor/usage/model-usage. The response describes rolling
# usage windows (5-hour token quota, weekly token quota, monthly tool quota).
GLM_QUOTA_URL = 'https://api.z.ai/api/monitor/usage/quota/limit'
OUTPUT_GLM_QUOTA_JSON = 'glm_quota.json'

# Human-readable labels for the (type, unit) pairs returned by the Z.ai quota
# API. These match the dashboard's own titles and are used for stdout and JSON
# output. The API returns numeric unit codes; callers should not depend on the
# numeric values directly.
GLM_QUOTA_WINDOW_LABELS = {
    ('TOKENS_LIMIT', 3): '5h',
    ('TOKENS_LIMIT', 6): '7d',
    ('TIME_LIMIT', 5): 'Monthly Web Search / Reader / Zread Quota',
}

DailyTokens = dict[date, int]
DailyCosts = dict[date, float]


class GlmQuotaUsageDetail(TypedDict, total=False):
    modelCode: str
    usage: int


class GlmQuotaLimit(TypedDict, total=False):
    type: str
    unit: int
    number: int
    usage: int
    currentValue: int
    remaining: int
    percentage: int
    nextResetTime: int
    usageDetails: list[GlmQuotaUsageDetail]


class GlmQuotaSnapshot(TypedDict, total=False):
    label: str
    type: str
    unit: int
    percentage: int
    next_reset_time_ms: int | None
    next_reset_iso: str | None
    usage: int | None
    current_value: int | None
    remaining: int | None
    usage_details: list[GlmQuotaUsageDetail] | None


# Unified quota snapshot across providers (GLM, Codex, Claude Code).
# The e-ink firmware and stdout render this single array; the per-provider
# GLM-only `glm_quota` field is kept as a backward-compat alias.
class QuotaSnapshot(TypedDict, total=False):
    provider: str
    label: str
    percentage: int
    next_reset_time_ms: int | None
    next_reset_iso: str | None
    usage: int | None
    remaining: int | None


# Codex CLI rate_limits window -> human-readable label. Codex session JSONL
# embeds `rate_limits` in `token_count` event_msgs with `window_minutes`:
# 300 = 5-hour primary window, 10080 = 7-day secondary window.
CODEX_WINDOW_MINUTES_LABELS = {
    300: '5h',
    10080: '7d',
}
# Codex wham/usage API uses limit_window_seconds instead of window_minutes.
# 18000s = 5h, 604800s = 7d.
CODEX_WINDOW_SECONDS_LABELS = {
    18000: '5h',
    604800: '7d',
}
CODEEX_WHAM_USAGE_URL = 'https://chatgpt.com/backend-api/wham/usage'

# Claude Code OAuth usage endpoint. The token is stored in the macOS Keychain
# generic-password item "Claude Code-credentials" (account = username), under
# the claudeAiOauth.accessToken field. utilization is a float 0-1 (not 0-100).
CLAUDE_OAUTH_USAGE_URL = 'https://api.anthropic.com/api/oauth/usage'
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

    if exclude_glm and (provider_lower in GLM_PROVIDERS or model_lower.startswith('zai-coding-plan/')):
        return None
    if model_lower.startswith(GLM_MODEL_PREFIXES):
        return 'glm_opencode'
    if 'google' in provider_lower or model_lower.startswith('google/') or 'gemini' in model_lower:
        return 'gemini'
    if 'anthropic' in provider_lower or model_lower.startswith('anthropic/'):
        return 'anthropic'
    if provider_lower == 'openai' or model_lower.startswith('gpt-'):
        return 'gpt_opencode'
    if 'deepseek' in provider_lower or model_lower.startswith('deepseek'):
        return 'deepseek'
    if (
        provider_lower == 'xai'
        or model_lower.startswith('xai/')
        or 'grok' in model_lower
    ):
        return 'grok'
    if 'qwen' in provider_lower or 'qwen' in model_lower:
        return 'qwen'
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
    if not path:
        sibling_path = Path(SCRIPT_DIR).parent / 'opencode_skill' / 'src'
        if sibling_path.exists():
            path = str(sibling_path)
    if path and path not in sys.path:
        sys.path.insert(0, path)


def _opencode_extra_dbs():
    """Extra DBs to read besides the main DB via iter_assistant_messages.

    Includes any user-configured extra DB (e.g. a separate OpenCode server's
    live DB) via OPENCODE_BATCH_DB, plus the default archive DBs from
    opencode_skill when available. All entries are existence-checked so
    non-configured or missing DBs are silently skipped.
    """
    dbs: list[Path] = []
    if OPENCODE_BATCH_DB is not None and OPENCODE_BATCH_DB.exists():
        dbs.append(OPENCODE_BATCH_DB)
    try:
        configure_opencode_skill_path()
        ocs_query = importlib.import_module('opencode_skill.query')
        for p in getattr(ocs_query, 'DEFAULT_ARCHIVE_DBS', ()):
            if p.exists() and p not in dbs:
                dbs.append(p)
    except ImportError:
        pass
    return dbs

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
    gemini: DailyTokens,
    claude: DailyTokens,
    gpt_opencode: DailyTokens,
    deepseek: DailyTokens,
    grok: DailyTokens,
    qwen: DailyTokens,
    other: DailyTokens,
    start_date: str,
    end_date: str,
    daily_costs: DailyCosts | None = None,
    daily_active_seconds: DailyActiveSeconds | None = None,
    glm_quota: list[GlmQuotaSnapshot] | None = None,
    quotas: list[QuotaSnapshot] | None = None,
) -> dict[str, object]:
    all_dates = list_dates_in_range(start_date, end_date)
    has_costs = daily_costs is not None

    daily_entries: list[dict[str, object]] = []
    summary_totals = {
        'cursor': 0,
        'glm': 0,
        'gemini': 0,
        'claude': 0,
        'gpt_opencode': 0,
        'deepseek': 0,
        'grok': 0,
        'qwen': 0,
        'other': 0,
    }
    total_tokens = 0
    total_ai_hours = 0.0
    total_cost_usd = 0.0

    for current_date in all_dates:
        categories = {
            'cursor': cursor.get(current_date, 0),
            'glm': glm.get(current_date, 0),
            'gemini': gemini.get(current_date, 0),
            'claude': claude.get(current_date, 0),
            'gpt_opencode': gpt_opencode.get(current_date, 0),
            'deepseek': deepseek.get(current_date, 0),
            'grok': grok.get(current_date, 0),
            'qwen': qwen.get(current_date, 0),
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
        'generated_at': datetime.now(ZoneInfo('America/Los_Angeles')).replace(tzinfo=None).isoformat(timespec='seconds'),
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
    if glm_quota:
        payload['glm_quota'] = glm_quota
    if quotas:
        payload['quotas'] = quotas
    return payload


def write_eink_dashboard_payload(
    cursor: DailyTokens,
    glm: DailyTokens,
    gemini: DailyTokens,
    claude: DailyTokens,
    gpt_opencode: DailyTokens,
    deepseek: DailyTokens,
    grok: DailyTokens,
    qwen: DailyTokens,
    other: DailyTokens,
    start_date: str,
    end_date: str,
    daily_costs: DailyCosts | None = None,
    daily_active_seconds: DailyActiveSeconds | None = None,
    output_path: str | None = None,
    glm_quota: list[GlmQuotaSnapshot] | None = None,
    quotas: list[QuotaSnapshot] | None = None,
) -> dict[str, object]:
    # E-ink drops Antigravity Claude/GPT quota bars; full list stays in API via same quotas field
    # when callers pass unfiltered quotas to generate_dashboard stdout and filtered ones here.
    payload = build_eink_dashboard_payload(
        cursor,
        glm,
        gemini,
        claude,
        gpt_opencode,
        deepseek,
        grok,
        qwen,
        other,
        start_date,
        end_date,
        daily_costs=daily_costs,
        daily_active_seconds=daily_active_seconds,
        glm_quota=glm_quota,
        quotas=quotas,
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
        ['npx', '-y', 'ccusage', 'codex', 'daily', '--json', '-s', start_date.replace('-', '')],
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
        print("  - First npx run may need package installation. Try: npx ccusage codex daily --json -s 20260302", file=sys.stderr)
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


def parse_ccusage_daily_date(value: str) -> date:
    try:
        return datetime.strptime(value, '%b %d, %Y').date()
    except ValueError:
        return datetime.strptime(value, '%Y-%m-%d').date()

def export_cursor(cookie_str, start_ts, end_ts):
    url = 'https://cursor.com/api/dashboard/get-filtered-usage-events'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:151.0) Gecko/20100101 Firefox/151.0',
        'Accept': '*/*',
        'Referer': 'https://cursor.com/dashboard/usage',
        'Content-Type': 'application/json',
        'Origin': 'https://cursor.com',
        'Cookie': cookie_str,
    }

    rows: list[dict[str, object]] = []
    page = 1
    page_size = 100
    total_count = None
    while True:
        payload = {
            'teamId': 0,
            'startDate': str(start_ts),
            'endDate': str(end_ts),
            'page': page,
            'pageSize': page_size,
        }
        resp = requests.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        body = resp.json()
        events = body.get('usageEventsDisplay', [])
        if not isinstance(events, list):
            events = []
        if total_count is None:
            total_count = body.get('totalUsageEventsCount')

        for event in events:
            if not isinstance(event, dict):
                continue
            token_usage = event.get('tokenUsage') if isinstance(event.get('tokenUsage'), dict) else {}
            timestamp = event.get('timestamp')
            try:
                event_dt = datetime.fromtimestamp(int(str(timestamp)) / 1000).isoformat()
            except (TypeError, ValueError):
                continue
            input_tokens = int(token_usage.get('inputTokens', 0) or 0)
            output_tokens = int(token_usage.get('outputTokens', 0) or 0)
            cache_read_tokens = int(token_usage.get('cacheReadTokens', 0) or 0)
            cache_write_tokens = int(token_usage.get('cacheWriteTokens', 0) or 0)
            total_tokens = int(
                token_usage.get('totalTokens')
                or input_tokens + output_tokens + cache_read_tokens + cache_write_tokens
            )
            rows.append({
                'Date': event_dt,
                'Total Tokens': total_tokens,
                'Input Tokens': input_tokens,
                'Output Tokens': output_tokens,
                'Cache Read Tokens': cache_read_tokens,
                'Cache Write Tokens': cache_write_tokens,
                'Model': event.get('model', ''),
                'Charged Cents': event.get('chargedCents', ''),
                'Request Costs': event.get('requestsCosts', ''),
                'Kind': event.get('kind', ''),
            })

        if not events:
            break
        if isinstance(total_count, int) and len(rows) >= total_count:
            break
        if len(events) < page_size:
            break
        page += 1
    
    csv_path = os.path.join(SCRIPT_DIR, 'cursor.csv')
    fieldnames = [
        'Date',
        'Total Tokens',
        'Input Tokens',
        'Output Tokens',
        'Cache Read Tokens',
        'Cache Write Tokens',
        'Model',
        'Charged Cents',
        'Request Costs',
        'Kind',
    ]
    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
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
        dt = parse_ccusage_daily_date(entry['date'])
        daily[dt] = entry['totalTokens']
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


def export_glm_quota(bearer_token: str) -> dict[str, object]:
    """Fetch the current Z.ai coding-plan quota snapshot and write glm_quota.json.

    The quota endpoint takes no query parameters; the bearer token identifies
    the plan. The response is cached verbatim to glm_quota.json next to glm.json
    so offline runs and tests can reuse it.
    """
    headers = {
        'Authorization': f'Bearer {bearer_token}',
        'User-Agent': 'Mozilla/5.0',
        'Accept': 'application/json',
    }
    resp = requests.get(GLM_QUOTA_URL, headers=headers)
    resp.raise_for_status()
    body = resp.json()
    if not body.get('success', False):
        print(f"  GLM quota API warning: {body.get('msg', 'unknown error')}")
    json_path = os.path.join(SCRIPT_DIR, OUTPUT_GLM_QUOTA_JSON)
    with open(json_path, 'w') as f:
        json.dump(body, f, indent=2)
    return body


def _glm_quota_label(limit_type: str, unit: int) -> str:
    """Return the human-readable window label for a (type, unit) pair.

    Falls back to a generic ``<type> unit=<unit>`` string for unknown codes so
    new windows added by Z.ai still surface instead of being silently dropped.
    """
    return GLM_QUOTA_WINDOW_LABELS.get((limit_type, unit), f'{limit_type} unit={unit}')


def _epoch_ms_to_iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000).isoformat(timespec='seconds')


def normalize_glm_quota(body: dict[str, object]) -> list[GlmQuotaSnapshot]:
    """Convert the raw Z.ai quota/limit response into labeled snapshots.

    Each entry carries a stable ``label``, the raw ``type``/``unit`` codes, the
    usage percentage, and the next reset time in both epoch-ms and ISO forms.
    Token limits only report ``percentage``; the monthly tool limit also
    reports absolute ``usage``/``currentValue``/``remaining`` counts and a
    per-model ``usage_details`` breakdown.
    """
    data = body.get('data')
    if not isinstance(data, dict):
        return []
    limits = data.get('limits')
    if not isinstance(limits, list):
        return []
    snapshots: list[GlmQuotaSnapshot] = []
    for limit in limits:
        if not isinstance(limit, dict):
            continue
        limit_type = str(limit.get('type', ''))
        unit = int(limit.get('unit', 0))
        next_reset_ms = limit.get('nextResetTime')
        next_reset_ms = int(next_reset_ms) if isinstance(next_reset_ms, (int, float)) else None
        snapshot: GlmQuotaSnapshot = {
            'label': _glm_quota_label(limit_type, unit),
            'type': limit_type,
            'unit': unit,
            'percentage': int(limit.get('percentage', 0)),
            'next_reset_time_ms': next_reset_ms,
            'next_reset_iso': _epoch_ms_to_iso(next_reset_ms),
            'usage': int(limit['usage']) if 'usage' in limit and isinstance(limit['usage'], (int, float)) else None,
            'current_value': int(limit['currentValue']) if 'currentValue' in limit and isinstance(limit['currentValue'], (int, float)) else None,
            'remaining': int(limit['remaining']) if 'remaining' in limit and isinstance(limit['remaining'], (int, float)) else None,
            'usage_details': limit.get('usageDetails') if isinstance(limit.get('usageDetails'), list) else None,
        }
        snapshots.append(snapshot)
    return snapshots


def load_glm_quota(path: str | None = None) -> list[GlmQuotaSnapshot]:
    """Load and normalize the cached glm_quota.json, returning [] when absent."""
    if path is None:
        path = os.path.join(SCRIPT_DIR, OUTPUT_GLM_QUOTA_JSON)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        body = json.load(f)
    return normalize_glm_quota(body)


def format_glm_quota_block(snapshots: list[GlmQuotaSnapshot]) -> str:
    """Render the quota snapshot list as a multi-line stdout block.

    The block is appended after the token/cost table. Each line shows the window
    label, used percentage, and the next reset time (ISO, local) when available.
    """
    if not snapshots:
        return ''
    lines = ['\nGLM / Z.ai Coding Plan Quota:']
    for s in snapshots:
        pct = s.get('percentage', 0)
        reset_iso = s.get('next_reset_iso')
        reset_part = f'  resets {reset_iso}' if reset_iso else ''
        absolute_part = ''
        remaining = s.get('remaining')
        usage = s.get('usage')
        if remaining is not None and usage is not None:
            absolute_part = f'  used {usage}/{usage + remaining}'
        lines.append(f"  {s['label']}: {pct}% used{absolute_part}{reset_part}")
    return '\n'.join(lines)


def glm_quota_to_unified(snapshots: list[GlmQuotaSnapshot]) -> list[QuotaSnapshot]:
    """Convert GLM-specific snapshots into the provider-tagged unified shape.

    Skips the monthly web-search/reader/zread tool quota (TIME_LIMIT); only the
    5-hour and weekly token quotas are surfaced in the unified array.
    """
    unified: list[QuotaSnapshot] = []
    for s in snapshots:
        if s.get('type') == 'TIME_LIMIT':
            continue
        unified.append({
            'provider': 'glm',
            'label': s.get('label', ''),
            'percentage': s.get('percentage', 0),
            'next_reset_time_ms': s.get('next_reset_time_ms'),
            'next_reset_iso': s.get('next_reset_iso'),
            'usage': s.get('usage'),
            'remaining': s.get('remaining'),
        })
    return unified


def _codex_window_label(window_minutes: int | None) -> str:
    if window_minutes is None:
        return 'Unknown'
    return CODEX_WINDOW_MINUTES_LABELS.get(window_minutes, f'{window_minutes}m')


def _codex_window_label_from_seconds(seconds: int | None) -> str:
    if seconds is None:
        return 'Unknown'
    return CODEX_WINDOW_SECONDS_LABELS.get(seconds, f'{seconds}s')


def _epoch_s_to_iso(s: int | float | None) -> str | None:
    if s is None:
        return None
    return datetime.fromtimestamp(int(s)).isoformat(timespec='seconds')


def export_codex_quota() -> list[QuotaSnapshot]:
    """Fetch Codex plan quota from the ChatGPT wham/usage API.

    Reads the OAuth access_token from ~/.codex/auth.json and calls
    GET https://chatgpt.com/backend-api/wham/usage. This reflects the ChatGPT
    plan Codex quota regardless of whether the user drives Codex via the CLI
    or via OpenCode (as long as OpenCode uses the ChatGPT OAuth path, not a
    platform API key). Returns [] when auth.json is missing or the API fails.
    """
    auth_path = Path.home() / '.codex' / 'auth.json'
    if not auth_path.exists():
        return []
    with auth_path.open() as f:
        auth = json.load(f)
    token = auth.get('tokens', {}).get('access_token', '')
    if not token:
        return []
    resp = requests.get(CODEEX_WHAM_USAGE_URL, headers={
        'Authorization': f'Bearer {token}',
        'User-Agent': 'Mozilla/5.0',
        'Accept': 'application/json',
    })
    resp.raise_for_status()
    body = resp.json()
    rate_limit = body.get('rate_limit', {})
    snapshots: list[QuotaSnapshot] = []
    for slot, label_key in (('primary_window', '5h'), ('secondary_window', '7d')):
        window = rate_limit.get(slot)
        if not isinstance(window, dict):
            continue
        used = window.get('used_percent')
        if not isinstance(used, (int, float)):
            continue
        resets_at = window.get('reset_at')
        resets_ms = int(resets_at) * 1000 if isinstance(resets_at, (int, float)) else None
        snapshots.append({
            'provider': 'codex',
            'label': _codex_window_label_from_seconds(window.get('limit_window_seconds')) or label_key,
            'percentage': int(round(used)),
            'next_reset_time_ms': resets_ms,
            'next_reset_iso': _epoch_s_to_iso(resets_at if isinstance(resets_at, (int, float)) else None),
        })
    return snapshots


def normalize_codex_rate_limits(rate_limits: dict[str, object]) -> list[QuotaSnapshot]:
    """Convert a Codex `rate_limits` block into unified quota snapshots.

    Codex session JSONL embeds `rate_limits` in `token_count` event payloads with
    `primary` (5h) and `secondary` (7d) windows. `used_percent` is 0-100 and
    `resets_at` is a unix-seconds timestamp.
    """
    snapshots: list[QuotaSnapshot] = []
    for slot in ('primary', 'secondary'):
        window = rate_limits.get(slot)
        if not isinstance(window, dict):
            continue
        used = window.get('used_percent')
        if not isinstance(used, (int, float)):
            continue
        resets_at = window.get('resets_at')
        resets_ms = int(resets_at) * 1000 if isinstance(resets_at, (int, float)) else None
        snapshots.append({
            'provider': 'codex',
            'label': _codex_window_label(window.get('window_minutes')),
            'percentage': int(round(used)),
            'next_reset_time_ms': resets_ms,
            'next_reset_iso': _epoch_s_to_iso(resets_at if isinstance(resets_at, (int, float)) else None),
        })
    return snapshots


def load_codex_quota(start_date: str | None = None, end_date: str | None = None) -> list[QuotaSnapshot]:
    """Fetch Codex plan quota, preferring the wham/usage API over local JSONL.

    The wham/usage API reflects the ChatGPT plan Codex quota in real time,
    covering both direct Codex CLI usage and OpenCode→Codex traffic (when using
    the ChatGPT OAuth path). Falls back to parsing the latest `rate_limits`
    from local session JSONL when the API is unavailable (no auth.json, network
    error, or expired token). Returns [] when neither source yields data.
    """
    try:
        api_snapshots = export_codex_quota()
        if api_snapshots:
            return api_snapshots
    except Exception as e:
        print(f"Failed to fetch Codex quota from wham/usage API: {e}")

    # Fallback: parse local session JSONL.
    sessions_root = Path.home() / '.codex' / 'sessions'
    archived_root = Path.home() / '.codex' / 'archived_sessions'
    roots = [sessions_root, archived_root]
    files: list[Path] = []
    for root in roots:
        if root.exists():
            files.extend(sorted(root.rglob('*.jsonl'), reverse=True))

    latest_rate_limits: dict[str, object] | None = None
    latest_time: datetime | None = None
    for session_file in files:
        try:
            with session_file.open() as f:
                for line in f:
                    line = line.strip()
                    if not line or '"token_count"' not in line or '"rate_limits"' not in line:
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if raw.get('type') != 'event_msg':
                        continue
                    payload = raw.get('payload', {})
                    if not isinstance(payload, dict) or payload.get('type') != 'token_count':
                        continue
                    rate_limits = payload.get('rate_limits')
                    if not isinstance(rate_limits, dict):
                        continue
                    ts = parse_codex_event_time(raw.get('timestamp', ''))
                    if latest_time is None or ts > latest_time:
                        latest_time = ts
                        latest_rate_limits = rate_limits
        except OSError:
            continue
        if latest_rate_limits is not None:
            break

    if latest_rate_limits is None:
        return []
    return normalize_codex_rate_limits(latest_rate_limits)


def _provider_display_name(provider: str) -> str:
    """Normalize provider names for display: GLM all-caps, others title-cased."""
    if provider == 'glm':
        return 'GLM'
    return provider.capitalize()


def format_quotas_block(snapshots: list[QuotaSnapshot]) -> str:
    """Render the unified quota snapshot list as a multi-line stdout block.

    Grouped by provider. Each line shows provider, window label, used
    percentage, and 'reset at <ISO>' when available.
    """
    if not snapshots:
        return ''
    lines = ['\nAI Usage Quotas:']
    for s in snapshots:
        provider = _provider_display_name(s.get('provider', ''))
        pct = s.get('percentage', 0)
        reset_iso = s.get('next_reset_iso')
        reset_part = f'  reset @ {reset_iso}' if reset_iso else ''
        lines.append(f"  {provider} {s.get('label', '')}: {pct}% used{reset_part}")
    return '\n'.join(lines)


# Ollama web settings page. The quota usage bars are server-rendered in the HTML
# at https://ollama.com/settings (no JSON API). We parse the two windows
# (Session = 5h, Weekly) from the markup: the percentage from the
# "X% used" span and the reset time from the local-time div's data-time attr.
OLLAMA_SETTINGS_URL = 'https://ollama.com/settings'
OUTPUT_OLLAMA_QUOTA_HTML = 'ollama_settings.html'


def export_ollama_quota(cookie: str) -> str:
    """Fetch ollama.com/settings HTML and cache it to ollama_settings.html.

    Requires the full browser cookie string (cf_clearance + session). The page
    is server-rendered; the HTML is the source of quota data.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:152.0) Gecko/20100101 Firefox/152.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Cookie': cookie,
    }
    resp = requests.get(OLLAMA_SETTINGS_URL, headers=headers)
    resp.raise_for_status()
    html_path = os.path.join(SCRIPT_DIR, OUTPUT_OLLAMA_QUOTA_HTML)
    with open(html_path, 'w') as f:
        f.write(resp.text)
    return resp.text


def normalize_ollama_quota(html: str) -> list[QuotaSnapshot]:
    """Parse the two Ollama usage windows (Session = 5h, Weekly) from HTML.

    The page renders each window as a block containing a "<pct>% used" span and
    a local-time div with data-time="<ISO reset time>". The percentage also
    appears in an aria-label attribute; we only match the span text (preceded
    by a tag boundary) to avoid double-counting. Reset times from the HTML are
    UTC (data-time="...Z"); we convert to local time for display.
    """
    if not html:
        return []
    # Match ">X% used" (span text) but not aria-label="...X% used"
    pct_re = re.compile(r'>\s*([\d.]+)%\s*used', re.IGNORECASE)
    time_re = re.compile(r'data-time="([^"]+)"')
    percentages = [float(m.group(1)) for m in pct_re.finditer(html)]
    reset_times = [m.group(1) for m in time_re.finditer(html)]
    snapshots: list[QuotaSnapshot] = []
    labels = ['5h', '7d']
    for i, pct in enumerate(percentages[:2]):
        reset_iso_raw = reset_times[i] if i < len(reset_times) else None
        reset_ms: int | None = None
        reset_iso_local: str | None = None
        if reset_iso_raw:
            try:
                utc_dt = datetime.fromisoformat(reset_iso_raw.replace('Z', '+00:00'))
                reset_ms = int(utc_dt.timestamp() * 1000)
                reset_iso_local = utc_dt.astimezone().replace(tzinfo=None).isoformat(timespec='minutes')
            except ValueError:
                pass
        snapshots.append({
            'provider': 'ollama',
            'label': labels[i] if i < len(labels) else f'Window {i+1}',
            'percentage': int(round(pct)),
            'next_reset_time_ms': reset_ms,
            'next_reset_iso': reset_iso_local,
        })
    return snapshots


def load_ollama_quota(path: str | None = None) -> list[QuotaSnapshot]:
    """Load and parse the cached ollama_settings.html, returning [] when absent."""
    if path is None:
        path = os.path.join(SCRIPT_DIR, OUTPUT_OLLAMA_QUOTA_HTML)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return normalize_ollama_quota(f.read())


def _read_claude_code_oauth_token() -> str:
    """Read the Claude Code OAuth access token from the macOS Keychain.

    Returns '' when the keychain item is missing or the JSON is malformed.
    On non-macOS platforms, returns '' (no keychain access).
    """
    import subprocess
    try:
        result = subprocess.run(
            ['security', 'find-generic-password', '-s', 'Claude Code-credentials', '-w'],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return ''
        data = json.loads(result.stdout.strip())
        return data.get('claudeAiOauth', {}).get('accessToken', '')
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return ''


def _iso_utc_to_local(iso: str | None) -> str | None:
    """Convert a UTC ISO timestamp to local naive ISO (minutes precision)."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        return dt.astimezone().replace(tzinfo=None).isoformat(timespec='minutes')
    except ValueError:
        return None


def _iso_utc_to_epoch_ms(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


def _normalize_usage_percentage(utilization: int | float) -> int:
    """Normalize quota utilization from either ratio (0-1) or percent (0-100)."""
    pct = utilization * 100 if utilization <= 1 else utilization
    return max(0, min(100, int(round(pct))))


def export_claude_code_quota() -> list[QuotaSnapshot]:
    """Fetch Claude Code plan quota from the Anthropic OAuth usage endpoint.

    Reads the OAuth token from the macOS Keychain and calls
    GET https://api.anthropic.com/api/oauth/usage. Returns [] when the token
    is missing/expired or the API fails. utilization may be either a 0-1 ratio
    or a 0-100 percentage; normalized to 0-100. Reset times are UTC ISO;
    converted to local.
    """
    token = _read_claude_code_oauth_token()
    if not token:
        return []
    resp = requests.get(CLAUDE_OAUTH_USAGE_URL, headers={
        'Authorization': f'Bearer {token}',
        'anthropic-beta': 'oauth-2025-04-20',
        'Accept': 'application/json',
    })
    resp.raise_for_status()
    body = resp.json()
    snapshots: list[QuotaSnapshot] = []
    for key, label in (('five_hour', '5h'), ('seven_day', '7d')):
        window = body.get(key)
        if not isinstance(window, dict):
            continue
        utilization = window.get('utilization')
        if not isinstance(utilization, (int, float)):
            continue
        resets_iso = window.get('resets_at')
        snapshots.append({
            'provider': 'claude',
            'label': label,
            'percentage': _normalize_usage_percentage(utilization),
            'next_reset_time_ms': _iso_utc_to_epoch_ms(resets_iso),
            'next_reset_iso': _iso_utc_to_local(resets_iso),
        })
    return snapshots


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
        'gemini': {},
        'glm_opencode': {},
        'deepseek': {},
        'grok': {},
        'opencode_other': {},
    }


def load_opencode_from_db(exclude_glm: bool = True, start_ts: int | None = None, end_ts: int | None = None) -> dict[str, DailyTokens]:
    totals = {
        'anthropic': defaultdict(int),
        'gpt_opencode': defaultdict(int),
        'gemini': defaultdict(int),
        'glm_opencode': defaultdict(int),
        'deepseek': defaultdict(int),
        'grok': defaultdict(int),
        'qwen': defaultdict(int),
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
        'gemini': defaultdict(int),
        'glm_opencode': defaultdict(int),
        'deepseek': defaultdict(int),
        'grok': defaultdict(int),
        'qwen': defaultdict(int),
        'opencode_other': defaultdict(int),
    }
    for m in ocs_query.iter_assistant_messages(since_ms=start_ts, until_ms=end_ts, archive_dbs=_opencode_extra_dbs()):
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
    configure_opencode_skill_path()
    try:
        ocs_query = importlib.import_module('opencode_skill.query')
    except ImportError:
        ocs_query = None

    if ocs_query is not None:
        for m in ocs_query.iter_assistant_messages(since_ms=start_ts, until_ms=end_ts, archive_dbs=_opencode_extra_dbs()):
            model_id = m.model or 'unknown'
            if classify_opencode_bucket(m.provider or '', model_id, exclude_glm=exclude_glm) is None:
                continue
            total = m.tokens_input + m.tokens_output + m.tokens_reasoning + m.tokens_cache_read + m.tokens_cache_write
            if total <= 0:
                continue
            dt = datetime.fromtimestamp(m.time_created / 1000).date()
            daily_models[dt][model_id]['input'] += m.tokens_input
            daily_models[dt][model_id]['output'] += m.tokens_output + m.tokens_reasoning
            daily_models[dt][model_id]['cache_read'] += m.tokens_cache_read
            daily_models[dt][model_id]['cache_write'] += m.tokens_cache_write
        return dict(daily_models)

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
                out = tokens.get('output', 0) + tokens.get('reasoning', 0)
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


# ---------------------------------------------------------------------------
# Google Antigravity IDE
#
# The Antigravity Language Server (LS) is a Go binary that mediates all model
# calls and retains per-generation token usage in memory. The usage is exposed
# through a local gRPC-over-HTTP service (Connect-Protocol). No local file
# contains token counts; the only data source is the live LS process.
#
# Discovery is fully automatic: ps finds the LS process, lsof finds its
# listening ports, and the csrf token is extracted from argv. No .env keys,
# cookies, or user configuration are needed.
# ---------------------------------------------------------------------------

# These names remain public compatibility seams. Wrappers resolve facade
# collaborators at call time so existing ``patch('auto_usage...')`` callers
# keep controlling the provider implementation.
ANTIGRAVITY_MODEL_PLACEHOLDER_MAP = (
    _antigravity_usage.ANTIGRAVITY_MODEL_PLACEHOLDER_MAP
)
AntigravityConnection = _antigravity_usage.AntigravityConnection


def _classify_antigravity_model(model_id: str) -> str:
    return _antigravity_usage.classify_model(
        model_id,
        placeholder_map=ANTIGRAVITY_MODEL_PLACEHOLDER_MAP,
    )


def _extract_flag_value(argv: str, flag: str) -> str | None:
    return _antigravity_usage.extract_flag_value(argv, flag)


def _is_antigravity_ls_command(command: str) -> bool:
    return _antigravity_usage.is_ls_command(command)


def _discover_antigravity_connections() -> list[AntigravityConnection]:
    return _antigravity_usage.discover_connections(
        extract_flag=_extract_flag_value,
        command_matches=_is_antigravity_ls_command,
        find_ports=_find_listening_ports,
        heartbeat_probe=_probe_antigravity_heartbeat,
    )


def _find_listening_ports(pid: int) -> list[int]:
    return _antigravity_usage.find_listening_ports(pid)


def _probe_antigravity_heartbeat(port: int, csrf_token: str) -> bool:
    return _antigravity_usage.probe_heartbeat(port, csrf_token)


def _antigravity_rpc(
    connection: AntigravityConnection, method: str, body: dict,
) -> dict | None:
    return _antigravity_usage.rpc(
        connection,
        method,
        body,
        decode_chunks=_dechunk,
    )


def _dechunk(data: bytes) -> bytes:
    return _antigravity_usage.dechunk(data)


def _parse_antigravity_timestamp(value) -> int | None:
    return _antigravity_usage.parse_timestamp(value)


def _to_int(value) -> int:
    return _antigravity_usage.to_int(value)


def fetch_antigravity_trajectories(
    connections: list[AntigravityConnection],
) -> list[dict]:
    return _antigravity_usage.fetch_trajectories(
        connections,
        rpc_call=_antigravity_rpc,
    )


def parse_antigravity_usage_response(
    response: dict, session_id: str = '',
) -> list[dict]:
    return _antigravity_usage.parse_usage_response(
        response,
        session_id,
        parse_time=_parse_antigravity_timestamp,
        coerce_int=_to_int,
    )


ANTIGRAVITY_CACHE_FILE = os.path.join(SCRIPT_DIR, 'antigravity_usage_cache.json')
ANTIGRAVITY_SYNC_METADATA_FILE = os.path.join(SCRIPT_DIR, 'antigravity_sync_metadata.json')


def _load_antigravity_sync_metadata() -> dict[str, list[int]]:
    return _antigravity_usage.load_sync_metadata(
        ANTIGRAVITY_SYNC_METADATA_FILE
    )


def _save_antigravity_sync_metadata(metadata: dict[str, list[int]]) -> None:
    _antigravity_usage.save_sync_metadata(
        ANTIGRAVITY_SYNC_METADATA_FILE,
        metadata,
    )


def _load_antigravity_cache() -> list[dict]:
    return _antigravity_usage.load_cache(ANTIGRAVITY_CACHE_FILE)


def _save_antigravity_cache(entries: list[dict]) -> None:
    _antigravity_usage.save_cache(ANTIGRAVITY_CACHE_FILE, entries)


def _entry_to_date(entry: dict) -> date | None:
    return _antigravity_usage.entry_to_date(entry)


def _entry_total(entry: dict) -> int:
    return _antigravity_usage.entry_total(entry)


IngestResult = _antigravity_usage.IngestResult


def ingest_antigravity_entries(new_entries: list[dict]) -> IngestResult:
    return _antigravity_usage.ingest_entries(
        new_entries,
        load_cached=_load_antigravity_cache,
        save_cached=_save_antigravity_cache,
    )


def load_antigravity() -> dict[str, DailyTokens]:
    return _antigravity_usage.load_usage(
        load_cached=_load_antigravity_cache,
        save_cached=_save_antigravity_cache,
        load_sync=_load_antigravity_sync_metadata,
        save_sync=_save_antigravity_sync_metadata,
        discover=_discover_antigravity_connections,
        fetch=fetch_antigravity_trajectories,
        rpc_call=_antigravity_rpc,
        parse_usage=parse_antigravity_usage_response,
        classify=_classify_antigravity_model,
        get_entry_date=_entry_to_date,
        get_entry_total=_entry_total,
        conversation_dirs=[
            os.path.expanduser('~/.gemini/antigravity-ide/conversations'),
            os.path.expanduser('~/.gemini/antigravity/conversations'),
            os.path.expanduser('~/.gemini/antigravity-cli/conversations'),
        ],
    )


def load_antigravity_detailed() -> dict[date, dict[str, dict[str, int]]]:
    return _antigravity_usage.load_detailed(
        load_cached=_load_antigravity_cache,
        get_entry_date=_entry_to_date,
    )


def calc_antigravity_cost(detailed: dict[date, dict[str, dict[str, int]]]) -> DailyCosts:
    return _antigravity_usage.calculate_cost(
        detailed,
        pricing_lookup=get_pricing,
        cost_calculator=calc_cost,
    )


def _antigravity_model_family(label: str, model_id: str) -> str:
    return _antigravity_usage.model_family(label, model_id)


def export_antigravity_quota() -> list[QuotaSnapshot]:
    return cast(
        list[QuotaSnapshot],
        _antigravity_usage.export_quota(
            discover=_discover_antigravity_connections,
            rpc_call=_antigravity_rpc,
            family_for_model=_antigravity_model_family,
        ),
    )


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
        dt = parse_ccusage_daily_date(entry['date'])
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


def compute_daily_costs(start_date: str, end_date: str, start_ts: int, end_ts: int, codex: DailyTokens, glm: DailyTokens, dsh_detailed: dict[date, dict[str, dict[str, int]]] | None = None) -> DailyCosts | None:
    start_d = datetime.strptime(start_date, '%Y-%m-%d').date()
    end_d = datetime.strptime(end_date, '%Y-%m-%d').date()
    daily_costs = defaultdict(float)
    for d, v in calc_codex_cost().items():
        if start_d <= d <= end_d:
            daily_costs[d] += v
    for d, v in calc_glm_cost(glm).items():
        if start_d <= d <= end_d:
            daily_costs[d] += v
    if dsh_detailed is None:
        dsh_detailed = _dsh_usage.load_dsh_detailed(start_date=start_d, end_date=end_d)
    for d, v in _dsh_usage.calc_dsh_cost(dsh_detailed).items():
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


def generate_dashboard_desktop(cursor, glm, gemini, claude, gpt_opencode, deepseek, grok, qwen, other, start_date, end_date, daily_costs=None, daily_active_seconds: DailyActiveSeconds | None = None):
    start = datetime.strptime(start_date, '%Y-%m-%d').date()
    end = datetime.strptime(end_date, '%Y-%m-%d').date()
    
    all_dates = sorted(
        set(cursor) | set(glm) | set(gemini) | set(claude) | set(gpt_opencode) | set(deepseek) | set(grok) | set(qwen) | set(other) | set(daily_active_seconds or {})
    )
    all_dates = [d for d in all_dates if start <= d <= end]
    
    has_costs = daily_costs is not None
    grand_total = sum(cursor.get(d, 0) + glm.get(d, 0) + gemini.get(d, 0) + claude.get(d, 0) + gpt_opencode.get(d, 0) + deepseek.get(d, 0) + grok.get(d, 0) + qwen.get(d, 0) + other.get(d, 0) for d in all_dates)
    cost_total = sum((daily_costs or {}).get(d, 0.0) for d in all_dates) if has_costs else 0.0
    active_hours_total = sum((daily_active_seconds or {}).get(d, 0.0) for d in all_dates) / 3600

    dates = [datetime.combine(d, datetime.min.time()) for d in all_dates]
    date_nums = mdates.date2num(dates)
    
    # Richer palette for desktop
    PALETTE = {
        'Cursor': '#f97316',
        'GLM': '#22c55e',
        'Gemini': '#06b6d4',
        'Claude': '#f59e0b',
        'GPT': '#8b5cf6',
        'DeepSeek': '#3b82f6',
        'Grok': '#14b8a6',
        'Qwen': '#e11d48',
        'Other': '#64748b',
    }
    
    fig, (ax, ax_active) = plt.subplots(2, 1, figsize=(14, 10), sharex=True, height_ratios=[2, 1])
    
    width = 0.8
    vals = {
        'Cursor': [cursor.get(d.date(), 0) / 1e8 for d in dates],
        'GLM': [glm.get(d.date(), 0) / 1e8 for d in dates],
        'Gemini': [gemini.get(d.date(), 0) / 1e8 for d in dates],
        'Claude': [claude.get(d.date(), 0) / 1e8 for d in dates],
        'GPT': [gpt_opencode.get(d.date(), 0) / 1e8 for d in dates],
        'DeepSeek': [deepseek.get(d.date(), 0) / 1e8 for d in dates],
        'Grok': [grok.get(d.date(), 0) / 1e8 for d in dates],
        'Qwen': [qwen.get(d.date(), 0) / 1e8 for d in dates],
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

def generate_dashboard(cursor, glm, gemini, claude, gpt_opencode, deepseek, grok, qwen, other, start_date, end_date, daily_costs=None, daily_active_seconds: DailyActiveSeconds | None = None, *, skip_desktop_chart: bool = False, glm_quota: list[GlmQuotaSnapshot] | None = None, quotas: list[QuotaSnapshot] | None = None):
    start = datetime.strptime(start_date, '%Y-%m-%d').date()
    end = datetime.strptime(end_date, '%Y-%m-%d').date()
    
    all_dates = sorted(
        set(cursor) | set(glm) | set(gemini) | set(claude) | set(gpt_opencode) | set(deepseek) | set(grok) | set(qwen) | set(other) | set(daily_active_seconds or {})
    )
    all_dates = [d for d in all_dates if start <= d <= end]
    
    has_costs = daily_costs is not None
    cols = ('Date', 'Cursor', 'GLM', 'Gemini', 'Claude', 'GPT', 'DeepSeek', 'Grok', 'Qwen', 'Other', 'Total', 'AI Hours')
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
        ge = gemini.get(d, 0)
        cl = claude.get(d, 0)
        go = gpt_opencode.get(d, 0)
        ds = deepseek.get(d, 0)
        gk = grok.get(d, 0)
        qw = qwen.get(d, 0)
        o = other.get(d, 0)
        total = u + g + ge + cl + go + ds + gk + qw + o
        grand_total += total
        active_hours = (daily_active_seconds or {}).get(d, 0.0) / 3600
        active_hours_total += active_hours
        cost = daily_costs.get(d, 0.0) if has_costs else 0.0
        cost_total += cost
        row = f"{d!s:>{col_width}} {u:>{col_width},} {g:>{col_width},} {ge:>{col_width},} {cl:>{col_width},} {go:>{col_width},} {ds:>{col_width},} {gk:>{col_width},} {qw:>{col_width},} {o:>{col_width},} {total:>{col_width},} {active_hours:>{col_width}.2f}"
        if has_costs:
            row += f" ${cost:>{col_width - 2}.2f}"
        print(row)
    
    print("-" * len(header))
    totals = (
        sum(cursor.get(d, 0) for d in all_dates),
        sum(glm.get(d, 0) for d in all_dates),
        sum(gemini.get(d, 0) for d in all_dates),
        sum(claude.get(d, 0) for d in all_dates),
        sum(gpt_opencode.get(d, 0) for d in all_dates),
        sum(deepseek.get(d, 0) for d in all_dates),
        sum(grok.get(d, 0) for d in all_dates),
        sum(qwen.get(d, 0) for d in all_dates),
        sum(other.get(d, 0) for d in all_dates),
    )
    total_row = f"{'TOTAL':>{col_width}} {totals[0]:>{col_width},} {totals[1]:>{col_width},} {totals[2]:>{col_width},} {totals[3]:>{col_width},} {totals[4]:>{col_width},} {totals[5]:>{col_width},} {totals[6]:>{col_width},} {totals[7]:>{col_width},} {totals[8]:>{col_width},} {grand_total:>{col_width},} {active_hours_total:>{col_width}.2f}"
    if has_costs:
        total_row += f" ${cost_total:>{col_width - 2}.2f}"
    print(total_row)
    print(f"\nAI Active Time (cumulative est.): {active_hours_total:.2f} hours")
    if has_costs:
        print(f"\nEst. Total (API equiv.): ${cost_total:.2f}")

    if glm_quota:
        print(format_glm_quota_block(glm_quota))

    if quotas:
        print(format_quotas_block(quotas))

    if not skip_desktop_chart:
        generate_dashboard_desktop(cursor, glm, gemini, claude, gpt_opencode, deepseek, grok, qwen, other, start_date, end_date, daily_costs, daily_active_seconds)
    # Full quotas stay in the JSON/API (including Antigravity Claude/GPT).
    # E-ink firmware filters Antigravity to Gemini-only when rendering.
    return write_eink_dashboard_payload(cursor, glm, gemini, claude, gpt_opencode, deepseek, grok, qwen, other, start_date, end_date, daily_costs, daily_active_seconds, glm_quota=glm_quota, quotas=quotas)


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

    glm_quota: list[GlmQuotaSnapshot] = []
    if glm_token:
        print("Exporting GLM quota...")
        try:
            export_glm_quota(glm_token)
        except Exception as e:
            print(f"Failed to export GLM quota: {e}")
    glm_quota = load_glm_quota()

    ollama_cookie = os.environ.get('OLLAMA_COOKIE', '')
    if ollama_cookie:
        print("Exporting Ollama quota...")
        try:
            export_ollama_quota(ollama_cookie)
        except Exception as e:
            print(f"Failed to export Ollama quota: {e}")
    ollama_quota = load_ollama_quota()

    print("Loading Codex quota from wham/usage API...")
    codex_quota = load_codex_quota()

    print("Loading Claude Code quota from OAuth usage endpoint...")
    try:
        claude_quota = export_claude_code_quota()
    except Exception as e:
        print(f"Failed to fetch Claude Code quota: {e}")
        claude_quota = []

    # Provider order for display: z.ai GLM -> Ollama -> Codex -> Claude Code -> Antigravity -> Grok.
    print("Loading Antigravity IDE quota from live Language Server...")
    antigravity_quota: list[QuotaSnapshot] = []
    try:
        antigravity_quota = export_antigravity_quota()
    except Exception as e:
        print(f"Failed to fetch Antigravity quota: {e}")

    grok_quota: list[QuotaSnapshot] = []
    grok_cookie = os.environ.get('GROK_COOKIE', '')
    if grok_cookie:
        print("Loading Grok weekly usage pool from grok.com...")
        try:
            grok_quota = cast(list[QuotaSnapshot], _grok_usage.export_grok_quota(grok_cookie))
        except Exception as e:
            print(f"Failed to fetch Grok quota: {e}")
    quotas = glm_quota_to_unified(glm_quota) + ollama_quota + codex_quota + claude_quota + antigravity_quota + grok_quota

    print("Loading Claude Code data...")
    start_d = datetime.strptime(start_date, '%Y-%m-%d').date()
    end_d = datetime.strptime(end_date, '%Y-%m-%d').date()
    start_day_ts = date_to_epoch_ms(start_d)
    next_day_ts = date_to_epoch_ms(end_d + timedelta(days=1))

    claude_code = load_claude_code(start_date=start_d, end_date=end_d)

    print("Loading DSH (DeepSeek Harness) data...")
    dsh_detailed = _dsh_usage.load_dsh_detailed(start_date=start_d, end_date=end_d)
    dsh_glm: DailyTokens = {}
    dsh_other: DailyTokens = {}
    for d, models in dsh_detailed.items():
        for model_id, tok in models.items():
            total = tok['input'] + tok['output'] + tok['cache_read'] + tok['cache_write']
            if total <= 0:
                continue
            target = dsh_glm if _dsh_usage.is_dsh_glm_model(model_id) else dsh_other
            target[d] = target.get(d, 0) + total

    print("Loading OpenCode data (excluding zai-coding-plan GLM, split: Anthropic / Gemini / GLM / GPT / DeepSeek / Grok / Qwen / other)...")
    opencode_data = load_opencode(exclude_glm=True, start_ts=start_day_ts, end_ts=next_day_ts)
    anthropic = opencode_data['anthropic']
    gemini = opencode_data['gemini']
    glm_opencode = opencode_data['glm_opencode']
    gpt_opencode = opencode_data['gpt_opencode']
    opencode_deepseek = opencode_data['deepseek']
    opencode_grok = opencode_data.get('grok', {})
    opencode_qwen = opencode_data.get('qwen', {})
    opencode_other = opencode_data['opencode_other']

    print("Loading Antigravity IDE data from live Language Server...")
    antigravity_data = load_antigravity()
    for d, v in antigravity_data.get('gemini', {}).items():
        gemini[d] = gemini.get(d, 0) + v
    antigravity_detailed = {}
    if not no_cost:
        antigravity_detailed = load_antigravity_detailed()

    claude_combined = defaultdict(int)
    for d, v in claude_code.items():
        claude_combined[d] += v
    for d, v in anthropic.items():
        claude_combined[d] += v
    for d, v in antigravity_data.get('anthropic', {}).items():
        claude_combined[d] += v
    for d, v in antigravity_data.get('gpt_opencode', {}).items():
        gpt_opencode[d] = gpt_opencode.get(d, 0) + v
    for d, v in antigravity_data.get('deepseek', {}).items():
        opencode_deepseek[d] = opencode_deepseek.get(d, 0) + v
    for d, v in antigravity_data.get('qwen', {}).items():
        opencode_qwen[d] = opencode_qwen.get(d, 0) + v
    for d, v in antigravity_data.get('opencode_other', {}).items():
        opencode_other[d] = opencode_other.get(d, 0) + v

    print("Estimating AI active time from OpenCode + Codex turn windows...")
    opencode_turn_intervals = load_opencode_turn_intervals(exclude_glm=True, start_ts=start_day_ts, end_ts=next_day_ts)
    codex_turn_intervals = load_codex_turn_intervals(start_date, end_date)
    daily_active_seconds = compute_daily_ai_active_seconds(opencode_turn_intervals, codex_turn_intervals, start_date, end_date)

    daily_costs = compute_daily_costs(start_date, end_date, start_day_ts, next_day_ts, codex, glm, dsh_detailed=dsh_detailed) if not no_cost else None
    if daily_costs is not None and antigravity_detailed:
        for d, v in calc_antigravity_cost(antigravity_detailed).items():
            if start_d <= d <= end_d:
                daily_costs[d] = daily_costs.get(d, 0.0) + v
    gpt_combined = merge_daily_tokens(gpt_opencode, codex)
    # DSH zai/glm-* joins the GLM bucket: the Z.ai monitor API does not see
    # DSH-routed calls, so this adds usage the API bucket cannot double count.
    glm_combined = merge_daily_tokens(glm, glm_opencode, dsh_glm)
    for d, v in dsh_other.items():
        opencode_other[d] = opencode_other.get(d, 0) + v
    return generate_dashboard(cursor, glm_combined, gemini, dict(claude_combined), gpt_combined, opencode_deepseek, opencode_grok, opencode_qwen, opencode_other, start_date, end_date, daily_costs, daily_active_seconds=daily_active_seconds, skip_desktop_chart=skip_desktop_chart, glm_quota=glm_quota, quotas=quotas)

def _load_cursor_detailed(path=None) -> dict[date, dict[str, dict[str, int]]]:
    """Load per-model per-day token breakdown from Cursor CSV export."""
    if path is None:
        path = os.path.join(SCRIPT_DIR, 'cursor.csv')
    if not os.path.exists(path):
        return {}
    daily_models: defaultdict[date, defaultdict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0, 'total': 0})
    )
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                dt = datetime.fromisoformat(row['Date'].replace('Z', '+00:00')).date()
            except (KeyError, ValueError):
                continue
            model = row.get('Model', 'unknown')
            entry = daily_models[dt][model]
            entry['input'] += int(float(row.get('Input Tokens', 0) or 0))
            entry['output'] += int(float(row.get('Output Tokens', 0) or 0))
            entry['cache_read'] += int(float(row.get('Cache Read Tokens', 0) or 0))
            entry['cache_write'] += int(float(row.get('Cache Write Tokens', 0) or 0))
            entry['total'] += int(float(row.get('Total Tokens', 0) or 0))
    return {d: dict(m) for d, m in daily_models.items()}


def build_model_breakdown(days: int = 30, *, include_daily: bool = True) -> dict[str, object]:
    """Build per-model token usage breakdown across all data sources.

    Returns a dict suitable for ``ModelBreakdownResponse`` serialization.
    Sources that only provide total tokens (GLM API, Codex) are included
    with per-category fields set to None.
    """
    start_date_str, end_date_str, _, _ = get_date_range(days)
    start_d = datetime.strptime(start_date_str, '%Y-%m-%d').date()
    end_d = datetime.strptime(end_date_str, '%Y-%m-%d').date()
    start_ts = date_to_epoch_ms(start_d)
    next_day_ts = date_to_epoch_ms(end_d + timedelta(days=1))

    all_dates = list_dates_in_range(start_date_str, end_date_str)

    # Collect: {source: {model: {date: {input, output, cache_read, cache_write, total}}}}
    source_data: dict[str, dict[date, dict[str, dict[str, int]]]] = {}

    # OpenCode (per-model detailed)
    oc_detail = load_opencode_detailed(exclude_glm=True, start_ts=start_ts, end_ts=next_day_ts)
    source_data['opencode'] = oc_detail

    # Claude Code (per-model detailed)
    cc_detail = load_claude_code_detailed(start_date=start_d, end_date=end_d)
    source_data['claude_code'] = cc_detail

    # DSH (per-model detailed)
    source_data['dsh'] = _dsh_usage.load_dsh_detailed(start_date=start_d, end_date=end_d)

    # Antigravity (per-model detailed)
    ag_detail = load_antigravity_detailed()
    source_data['antigravity'] = ag_detail

    # Cursor (per-model detailed from CSV)
    cursor_detail = _load_cursor_detailed()
    source_data['cursor'] = cursor_detail

    # GLM (total only, no per-category breakdown from API)
    glm_daily = load_glm()
    glm_models: dict[date, dict[str, dict[str, int]]] = {}
    for d, total in glm_daily.items():
        if start_d <= d <= end_d:
            glm_models[d] = {'glm-coding-plan': {'total': total}}
    source_data['glm'] = glm_models

    # Codex (total only from usage.json)
    codex_daily = load_codex()
    codex_models: dict[date, dict[str, dict[str, int]]] = {}
    for d, total in codex_daily.items():
        if start_d <= d <= end_d:
            codex_models[d] = {'codex-combined': {'total': total}}
    source_data['codex'] = codex_models

    # Aggregate per (source, model)
    model_entries: list[dict] = []
    grand_input = 0
    grand_output = 0
    grand_cache_read = 0
    grand_cache_write = 0
    grand_total = 0

    for source_name, daily_models in source_data.items():
        # {model: {date: tokens}}
        per_model: dict[str, dict[date, dict[str, int]]] = defaultdict(dict)
        for d, models in daily_models.items():
            if not (start_d <= d <= end_d):
                continue
            for model_id, tokens in models.items():
                per_model[model_id][d] = tokens

        for model_id, date_tokens in per_model.items():
            m_input = 0
            m_output = 0
            m_cache_read = 0
            m_cache_write = 0
            m_total = 0
            has_detail = False

            for d in all_dates:
                tokens = date_tokens.get(d)
                if tokens is None:
                    if include_daily:
                        continue
                    else:
                        continue
                t_input = tokens.get('input', 0) if tokens else 0
                t_output = tokens.get('output', 0) if tokens else 0
                t_cr = tokens.get('cache_read', 0) if tokens else 0
                t_cw = tokens.get('cache_write', 0) if tokens else 0
                t_cw_1h = tokens.get('cache_write_1h', 0) if tokens else 0
                t_cw_total = t_cw + t_cw_1h
                t_total = tokens.get('total')
                if t_total is None or t_total == 0:
                    t_total = t_input + t_output + t_cr + t_cw_total

                if t_input or t_output or t_cr or t_cw_total:
                    has_detail = True

                m_input += t_input or 0
                m_output += t_output or 0
                m_cache_read += t_cr or 0
                m_cache_write += t_cw_total or 0
                m_total += t_total or 0

            if m_total == 0:
                continue

            entry: dict = {
                'source': source_name,
                'model': model_id,
                'totals': {
                    'input': m_input if has_detail else None,
                    'output': m_output if has_detail else None,
                    'cache_read': m_cache_read if has_detail else None,
                    'cache_write': m_cache_write if has_detail else None,
                    'total': m_total,
                },
            }

            if include_daily:
                daily_list = []
                for d in all_dates:
                    tokens = date_tokens.get(d)
                    if tokens is None:
                        continue
                    t_input = tokens.get('input', 0) or 0
                    t_output = tokens.get('output', 0) or 0
                    t_cr = tokens.get('cache_read', 0) or 0
                    t_cw = tokens.get('cache_write', 0) or 0
                    t_cw_1h = tokens.get('cache_write_1h', 0) or 0
                    t_cw_total = t_cw + t_cw_1h
                    t_total = tokens.get('total')
                    if t_total is None or t_total == 0:
                        t_total = t_input + t_output + t_cr + t_cw_total
                    if t_total == 0:
                        continue
                    daily_list.append({
                        'date': d.isoformat(),
                        'input': t_input if has_detail else None,
                        'output': t_output if has_detail else None,
                        'cache_read': t_cr if has_detail else None,
                        'cache_write': t_cw_total if has_detail else None,
                        'total': t_total,
                    })
                entry['daily'] = daily_list
            else:
                entry['daily'] = []

            model_entries.append(entry)

            if has_detail:
                grand_input += m_input
                grand_output += m_output
                grand_cache_read += m_cache_read
                grand_cache_write += m_cache_write
            grand_total += m_total

    # Sort by total descending
    model_entries.sort(key=lambda e: e['totals']['total'], reverse=True)

    input_output_ratio = round(grand_input / grand_output, 2) if grand_output > 0 else None
    cache_hit_rate = round(grand_cache_read / (grand_input + grand_cache_read), 4) if (grand_input + grand_cache_read) > 0 else None

    return {
        'meta': {
            'generated_at': datetime.now(ZoneInfo('America/Los_Angeles')).replace(tzinfo=None).isoformat(timespec='seconds'),
            'start_date': start_date_str,
            'end_date': end_date_str,
            'days': days,
        },
        'totals': {
            'input': grand_input,
            'output': grand_output,
            'cache_read': grand_cache_read,
            'cache_write': grand_cache_write,
            'total': grand_total,
            'input_output_ratio': input_output_ratio,
            'cache_hit_rate': cache_hit_rate,
        },
        'models': model_entries,
    }


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
