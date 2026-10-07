"""Local usage routing and explicit price bases for subscription calibration."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
import sqlite3
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

import auto_usage as dashboard
import antigravity_usage
from pricing_config import calc_cost_from_parts, get_pricing
from window_usage import SOURCES, UsageRecord

PLANS = ('ollama', 'grok', 'codex', 'claude', 'antigravity', 'cursor', 'glm')
OLLAMA_PRICE_DATE = '2026-10-06'
OLLAMA_PRICE_URL = 'https://ollama.com/pricing'
# Published input / cached-input / output USD per million tokens. Only DeepSeek
# has the weekday 12:00-18:00 UTC peak schedule on this price table.
OLLAMA_PRICES = {
    'deepseek-v4.1-flash': (0.30, 0.006, 1.20),
    'deepseek-v4-pro': (1.32, 0.044, 3.96),
    'glm-5.3': (1.40, 0.26, 4.40),
    'glm-5.3-flash': (0.15, 0.03, 0.50),
    'glm-5.2': (1.40, 0.26, 4.40),
    'gemma4': (0.14, 0.05, 0.40),
    'gpt-oss:120b': (0.15, 0.014, 0.60),
    'gpt-oss:20b': (0.07, 0.035, 0.30),
    'kimi-k3': (3.00, 0.30, 15.00),
    'kimi-k2.7-code': (0.95, 0.19, 4.00),
    'kimi-k2.6': (0.95, 0.16, 4.00),
    'minimax-m3': (0.60, 0.12, 2.40),
    'minimax-m2.7': (0.30, 0.06, 1.20),
    'mistral-large-4': (0.68, 0.07, 2.09),
    'gpt-oss:120b-cloud': (0.15, 0.014, 0.60),
    'nemotron-3-nano': (0.06, None, 0.24),
    'nemotron-3-super': (0.015, 0.015, 0.60),
    'nemotron-3-ultra': (0.10, 0.10, 3.00),
}


def utc_time(value: datetime | str) -> datetime:
    dt = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class UsageEvent:
    plan: str
    time: datetime
    cost_usd: float | None
    tokens: int = 0
    model: str = 'unknown'
    source: str = 'unknown'
    pool: str | None = None
    account_fingerprint: str | None = None


def route_plan(record: UsageRecord, auth: Mapping[str, Any], *, codex_subscription: bool = False,
               claude_subscription: bool = False) -> str | None:
    billing = (record.billing_provider or '').replace('_', '-').lower()
    if record.source == 'claude':
        return 'claude' if claude_subscription and record.model.lower().startswith('claude') else None
    if record.source == 'antigravity':
        return 'antigravity'
    if record.source == 'cursor':
        return 'cursor' if record.plan_included is True else None
    if billing == 'ollama-cloud':
        return 'ollama'
    if record.source == 'codex':
        return 'codex' if billing == 'openai' and codex_subscription else None
    if record.source == 'dsh':
        # An ordinary API-key route must not be mistaken for the coding plan.
        return 'glm' if billing == 'zai-coding-plan' else None
    if record.source != 'opencode':
        return None
    credential = auth.get(billing) or {}
    if billing == 'openai' and credential.get('type') == 'oauth':
        return 'codex'
    if billing == 'xai' and credential.get('type') == 'oauth':
        return 'grok'
    if billing == 'anthropic' and (credential.get('type') == 'oauth' or str(credential.get('key', '')).startswith('sk-ant-oat')):
        return 'claude'
    if billing == 'zai-coding-plan':
        return 'glm'
    return None


def price_record(plan: str, record: UsageRecord) -> float | None:
    if plan == 'cursor':
        value = record.recorded_cost_usd
        return value if value is not None and math.isfinite(value) and value >= 0 else None
    if plan == 'ollama':
        model = record.model.lower().removeprefix('ollama-cloud/').removesuffix(':cloud')
        rates = OLLAMA_PRICES.get(model)
        if not rates or record.cache_write or record.cache_write_1h:
            return None
        inp, cached, out = rates
        if cached is None and record.input_cached:
            return None
        cached = cached or 0.0
        utc = utc_time(record.time)
        peak = utc.weekday() < 5 and 12 <= utc.hour < 18
        factor = 0.5 if model.startswith('deepseek-') and not peak else 1.0
        return (record.input_non_cached * inp + record.input_cached * cached + record.output * out) * factor / 1e6
    pricing = antigravity_usage.resolve_pricing(record.model) if plan == 'antigravity' else get_pricing(record.model)
    if pricing is None:
        return None
    return calc_cost_from_parts(pricing, input_non_cached=record.input_non_cached,
                               input_cached=record.input_cached, output=record.output,
                               cache_write=record.cache_write, cache_write_1h=record.cache_write_1h)


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _claude_subscription() -> bool:
    settings = _read_json(Path.home() / '.claude/settings.json').get('env') or {}
    base = os.environ.get('ANTHROPIC_BASE_URL') or settings.get('ANTHROPIC_BASE_URL')
    if base and urlsplit(str(base)).hostname != 'api.anthropic.com':
        return False
    if os.environ.get('ANTHROPIC_API_KEY') or settings.get('ANTHROPIC_API_KEY'):
        return False
    override = os.environ.get('ANTHROPIC_AUTH_TOKEN') or settings.get('ANTHROPIC_AUTH_TOKEN')
    if override:
        return str(override).startswith('sk-ant-oat')
    credentials = _read_json(Path.home() / '.claude/.credentials.json')
    return bool((credentials.get('claudeAiOauth') or {}).get('accessToken') or dashboard._read_claude_code_oauth_token())


def _native_grok_activity(root: Path, start: datetime, end: datetime) -> bool:
    for path in root.glob('*/*/updates.jsonl'):
        if path.stat().st_mtime < start.timestamp():
            continue
        unknown_time = False
        with path.open() as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(item, dict):
                    continue
                params = item.get('params') or {}
                update = params.get('update') or {}
                if update.get('sessionUpdate') not in ('user_message_chunk', 'agent_message_chunk', 'agent_thought_chunk'):
                    continue
                timestamp = (params.get('_meta') or {}).get('agentTimestampMs')
                if timestamp is None and isinstance(item.get('timestamp'), (int, float)):
                    timestamp = item['timestamp'] * 1000
                if not isinstance(timestamp, (int, float)):
                    unknown_time = True
                elif start.timestamp() * 1000 <= timestamp < end.timestamp() * 1000:
                    return True
        if unknown_time:
            return True
    return False


def collect_usage(plans: set[str], start: datetime, end: datetime) -> tuple[list[UsageEvent], dict[str, Any]]:
    """Read only local sources. No provider fetches or history writes occur here."""
    start_local = utc_time(start).astimezone().replace(tzinfo=None)
    end_local = utc_time(end).astimezone().replace(tzinfo=None)
    if os.environ.get('OPENCODE_BATCH_DB'):
        dashboard.OPENCODE_BATCH_DB = Path(os.environ['OPENCODE_BATCH_DB']).expanduser()
    auth = _read_json(Path.home() / '.local/share/opencode/auth.json')
    codex_auth = _read_json(Path.home() / '.codex/auth.json')
    codex_subscription = bool((codex_auth.get('tokens') or {}).get('access_token')) and codex_auth.get('auth_mode') not in ('api', 'apikey')
    claude_subscription = _claude_subscription() if 'claude' in plans else False
    sources: set[str] = set()
    if plans & {'ollama', 'codex', 'grok', 'claude', 'glm'}:
        sources.add('opencode')
    if plans & {'ollama', 'glm'}:
        sources.add('dsh')
    if plans & {'ollama', 'codex'}:
        sources.add('codex')
    if 'claude' in plans:
        sources.add('claude')
    if 'antigravity' in plans:
        sources.add('antigravity')
    if 'cursor' in plans:
        sources.add('cursor')
    # Old Claude logs cannot contain newly appended events; keep recent scans
    # bounded while leaving event timestamps as the authoritative filter.
    original_claude_files = dashboard.iter_claude_session_files
    events: list[UsageEvent] = []
    excluded = Counter()
    errors: dict[str, str] = {}
    seen: set[tuple[str, str]] = set()
    for source in sorted(sources):
        if source == 'claude':
            try:
                files = [p for p in original_claude_files() if p.stat().st_mtime >= start.timestamp()]
            except OSError:
                files = []
                errors[source] = 'OSError'
            dashboard.iter_claude_session_files = lambda project_dirs=None: iter(files)
        try:
            for record in SOURCES[source](start_local, end_local):
                if not utc_time(start) <= utc_time(record.time) < utc_time(end) or record.total() == 0:
                    continue
                if record.record_id:
                    key = (source, record.record_id)
                    if key in seen:
                        excluded['duplicate_records'] += 1
                        continue
                    seen.add(key)
                plan = route_plan(record, auth, codex_subscription=codex_subscription, claude_subscription=claude_subscription)
                if plan not in plans:
                    excluded[f'{source}:not_mapped_to_selected_plan'] += 1
                    continue
                events.append(UsageEvent(plan=plan, time=utc_time(record.time), cost_usd=price_record(plan, record),
                                         tokens=record.total(), model=record.model, source=source,
                                         pool=record.plan_pool, account_fingerprint=record.account_fingerprint))
        except (OSError, ValueError, ImportError, RuntimeError, sqlite3.Error, KeyError, TypeError) as exc:
            errors[source] = type(exc).__name__
        finally:
            if source == 'claude':
                dashboard.iter_claude_session_files = original_claude_files
    # Native Grok CLI logs do not provide a dashboard token adapter yet. Detect
    # observed activity instead of silently claiming that OpenCode is complete.
    if 'grok' in plans:
        root = Path.home() / '.grok/sessions'
        try:
            if root.exists() and _native_grok_activity(root, utc_time(start), utc_time(end)):
                errors['grok_native'] = 'Native Grok CLI usage is not covered by the token adapters'
        except OSError:
            errors['grok_native'] = 'OSError'
    return events, {'source_errors': errors, 'excluded_records': dict(excluded)}
