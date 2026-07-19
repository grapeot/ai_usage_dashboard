"""Google Antigravity IDE usage, cache, and quota integration.

This module contains the provider-specific implementation. ``auto_usage``
remains the compatibility facade and injects its patchable collaborators into
the orchestration functions below.
"""

import glob
import json
import os
import socket
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime
from typing import Callable, TypedDict

from pricing_config import calc_cost, get_pricing


DailyTokens = dict[date, int]
DailyCosts = dict[date, float]


class AntigravityConnection(TypedDict):
    pid: int
    port: int
    csrf_token: str


class IngestResult(TypedDict):
    received: int
    new: int
    duplicate: int
    total_cache: int


ANTIGRAVITY_MODEL_PLACEHOLDER_MAP: dict[str, str] = {
    'model_placeholder_m20': 'gemini-3-flash-a',
}


def classify_model(
    model_id: str,
    *,
    placeholder_map: dict[str, str] | None = None,
) -> str:
    """Map an Antigravity LS model ID to a dashboard bucket name."""
    model = (model_id or '').lower().strip()
    if not model:
        return 'opencode_other'
    if model.startswith('model_placeholder_'):
        resolved = (placeholder_map or ANTIGRAVITY_MODEL_PLACEHOLDER_MAP).get(model)
        if resolved:
            model = resolved
        else:
            print(
                f"Antigravity: unknown model placeholder {model_id!r}, "
                "defaulting to gemini bucket",
                file=sys.stderr,
            )
            return 'gemini'
    if 'gemini' in model:
        return 'gemini'
    if 'claude' in model:
        return 'anthropic'
    if 'gpt' in model:
        return 'gpt_opencode'
    if 'deepseek' in model:
        return 'deepseek'
    return 'opencode_other'


def extract_flag_value(argv: str, flag: str) -> str | None:
    """Extract a --flag value from a command-line string."""
    compact = f'{flag}='
    idx = argv.find(compact)
    if idx >= 0:
        rest = argv[idx + len(compact):]
        return rest.split()[0] if rest else None
    idx = argv.find(flag)
    if idx < 0:
        return None
    rest = argv[idx + len(flag):]
    tokens = rest.split()
    return tokens[0] if tokens else None


def is_ls_command(command: str) -> bool:
    lower = command.lower()
    return (
        'language_server' in lower
        and ('antigravity' in lower or '--app_data_dir antigravity' in lower)
    )


def find_listening_ports(pid: int) -> list[int]:
    """Use lsof to find TCP listening ports for a PID."""
    try:
        result = subprocess.run(
            ['lsof', '-Pan', '-p', str(pid), '-iTCP', '-sTCP:LISTEN'],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    ports: list[int] = []
    for line in result.stdout.splitlines():
        for token in line.split():
            for prefix in ('127.0.0.1:', 'localhost:', '*:', '::1:'):
                if token.startswith(prefix):
                    cleaned = token[len(prefix):].rstrip('(LISTEN),')
                    try:
                        port = int(cleaned)
                        if port not in ports:
                            ports.append(port)
                    except ValueError:
                        pass
    return ports


def probe_heartbeat(port: int, csrf_token: str) -> bool:
    """Send a Heartbeat gRPC call to verify the port is an Antigravity LS."""
    body = '{"uuid":"00000000-0000-0000-0000-000000000000"}'
    request = (
        f'POST /exa.language_server_pb.LanguageServerService/Heartbeat HTTP/1.1\r\n'
        f'Host: 127.0.0.1:{port}\r\n'
        f'Content-Type: application/json\r\n'
        f'Content-Length: {len(body)}\r\n'
        f'Connect-Protocol-Version: 1\r\n'
        f'X-Codeium-Csrf-Token: {csrf_token}\r\n'
        f'Connection: close\r\n\r\n{body}'
    )
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=3) as connection:
            connection.sendall(request.encode())
            response = connection.recv(4096)
        return response.startswith(b'HTTP/1.1 200')
    except (OSError, socket.timeout):
        return False


def discover_connections(
    *,
    extract_flag: Callable[[str, str], str | None] = extract_flag_value,
    command_matches: Callable[[str], bool] = is_ls_command,
    find_ports: Callable[[int], list[int]] = find_listening_ports,
    heartbeat_probe: Callable[[int, str], bool] = probe_heartbeat,
) -> list[AntigravityConnection]:
    """Find and verify running Antigravity Language Server processes."""
    try:
        result = subprocess.run(
            ['ps', '-ww', '-eo', 'pid,args'],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    candidates: list[tuple[int, str]] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if command_matches(parts[1]):
            candidates.append((pid, parts[1]))

    connections: list[AntigravityConnection] = []
    for pid, command in candidates:
        csrf = extract_flag(command, '--csrf_token')
        if not csrf or len(csrf) < 32:
            continue
        for port in find_ports(pid):
            if heartbeat_probe(port, csrf):
                connections.append(
                    AntigravityConnection(pid=pid, port=port, csrf_token=csrf)
                )
                break
    connections.sort(key=lambda connection: connection['pid'], reverse=True)
    return connections


def rpc(
    connection: AntigravityConnection,
    method: str,
    body: dict,
    *,
    decode_chunks: Callable[[bytes], bytes] | None = None,
) -> dict | None:
    """Call an Antigravity LS gRPC method over HTTP/JSON."""
    payload = json.dumps(body)
    request = (
        f'POST /exa.language_server_pb.LanguageServerService/{method} HTTP/1.1\r\n'
        f'Host: 127.0.0.1:{connection["port"]}\r\n'
        f'Content-Type: application/json\r\n'
        f'Content-Length: {len(payload)}\r\n'
        f'Connect-Protocol-Version: 1\r\n'
        f'X-Codeium-Csrf-Token: {connection["csrf_token"]}\r\n'
        f'Connection: close\r\n\r\n{payload}'
    )
    try:
        with socket.create_connection(
            ('127.0.0.1', connection['port']), timeout=10
        ) as stream:
            stream.sendall(request.encode())
            buffer = b''
            while True:
                chunk = stream.recv(262144)
                if not chunk:
                    break
                buffer += chunk
    except (OSError, socket.timeout):
        return None

    header_end = buffer.find(b'\r\n\r\n')
    if header_end < 0:
        return None
    header = buffer[:header_end].decode(errors='replace')
    body_raw = buffer[header_end + 4:]
    status_parts = header.split()
    if len(status_parts) < 2 or status_parts[1] != '200':
        return None
    if 'transfer-encoding: chunked' in header.lower():
        body_raw = (decode_chunks or dechunk)(body_raw)
    try:
        return json.loads(body_raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def dechunk(data: bytes) -> bytes:
    """Decode HTTP chunked transfer-encoding body."""
    output = bytearray()
    index = 0
    while index < len(data):
        newline = data.find(b'\r\n', index)
        if newline < 0:
            break
        try:
            size = int(data[index:newline], 16)
        except ValueError:
            break
        if size == 0:
            break
        output.extend(data[newline + 2:newline + 2 + size])
        index = newline + 2 + size + 2
    return bytes(output)


def parse_timestamp(value) -> int | None:
    """Parse an epoch-ms integer or ISO timestamp into epoch milliseconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            pass
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return int(parsed.timestamp() * 1000)
        except ValueError:
            pass
    return None


def to_int(value) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value))
    except (ValueError, TypeError):
        return 0


Rpc = Callable[[AntigravityConnection, str, dict], dict | None]


def fetch_trajectories(
    connections: list[AntigravityConnection],
    *,
    rpc_call: Rpc,
) -> list[dict]:
    """Fetch all cascade trajectory summaries from all LS connections."""
    seen: set[str] = set()
    trajectories: list[dict] = []
    for connection in connections:
        response = rpc_call(connection, 'GetAllCascadeTrajectories', {})
        if not response:
            continue
        summaries = (
            response.get('trajectorySummaries')
            or response.get('cascadeTrajectories')
            or []
        )
        if isinstance(summaries, dict):
            summaries = [
                {'cascadeId': key, **(value if isinstance(value, dict) else {})}
                for key, value in summaries.items()
            ]
        if not isinstance(summaries, list):
            continue
        for summary in summaries:
            if not isinstance(summary, dict):
                continue
            cascade_id = (
                summary.get('cascadeId')
                or summary.get('trajectoryId')
                or summary.get('id')
            )
            if not cascade_id or cascade_id in seen:
                continue
            seen.add(cascade_id)
            trajectories.append(summary)
    return trajectories


def parse_usage_response(
    response: dict,
    session_id: str = '',
    *,
    parse_time: Callable[[object], int | None] = parse_timestamp,
    coerce_int: Callable[[object], int] = to_int,
) -> list[dict]:
    """Parse generator metadata into normalized token usage entries."""
    metadata = response.get('generatorMetadata', [])
    if not isinstance(metadata, list):
        return []

    entries: list[dict] = []
    for generation_index, item in enumerate(metadata):
        if not isinstance(item, dict):
            continue
        chat_model = item.get('chatModel', item)
        if not isinstance(chat_model, dict):
            continue
        model_id = chat_model.get('responseModel') or chat_model.get('model') or 'unknown'
        start_metadata = chat_model.get('chatStartMetadata')
        created_at = (
            start_metadata.get('createdAt')
            if isinstance(start_metadata, dict)
            else None
        )
        fallback_timestamp = parse_time(created_at)
        retry_infos = chat_model.get('retryInfos', [])
        if not isinstance(retry_infos, list):
            continue
        for retry in retry_infos:
            if not isinstance(retry, dict):
                continue
            usage = retry.get('usage', retry)
            if not isinstance(usage, dict):
                usage = {}
            input_tokens = coerce_int(usage.get('inputTokens'))
            output_tokens = coerce_int(usage.get('outputTokens'))
            cache_read = coerce_int(usage.get('cacheReadTokens'))
            cache_write = coerce_int(usage.get('cacheWriteTokens'))
            thinking = coerce_int(usage.get('thinkingOutputTokens'))
            if input_tokens + output_tokens + cache_read + cache_write + thinking == 0:
                continue
            timestamp = parse_time(
                usage.get('createdAt') or usage.get('timestamp')
            ) or fallback_timestamp
            entries.append({
                'model': model_id,
                'timestamp_ms': timestamp,
                'input': input_tokens,
                'output': output_tokens,
                'cache_read': cache_read,
                'cache_write': cache_write,
                'thinking': thinking,
                'response_id': usage.get('responseId'),
                'session_id': session_id,
                'gen_idx': generation_index,
            })
    return entries


def load_sync_metadata(path: str) -> dict[str, list[int]]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as stream:
            data = json.load(stream)
        if isinstance(data, dict):
            return {
                key: list(value)
                for key, value in data.items()
                if isinstance(value, list)
            }
    except Exception:
        pass
    return {}


def save_sync_metadata(path: str, metadata: dict[str, list[int]]) -> None:
    try:
        with open(path, 'w', encoding='utf-8') as stream:
            json.dump(metadata, stream, indent=2)
    except Exception:
        pass


def load_cache(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding='utf-8') as stream:
            data = json.load(stream)
        entries = data.get('entries', [])
        return entries if isinstance(entries, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_cache(path: str, entries: list[dict]) -> None:
    """Write usage entries to the cache, deduplicated by response ID."""
    deduped: list[dict] = []
    seen: set[str] = set()
    for entry in entries:
        response_id = entry.get('response_id')
        if response_id:
            if response_id in seen:
                continue
            seen.add(response_id)
        deduped.append(entry)
    with open(path, 'w', encoding='utf-8') as stream:
        json.dump({
            'version': 1,
            'updated_at': datetime.now().isoformat(),
            'entries': deduped,
        }, stream, indent=2)


def entry_to_date(entry: dict) -> date | None:
    timestamp = entry.get('timestamp')
    if timestamp is None:
        timestamp = entry.get('timestamp_ms')
    if isinstance(timestamp, (int, float)) and timestamp > 0:
        return datetime.fromtimestamp(timestamp / 1000).date()
    if isinstance(timestamp, str) and timestamp:
        try:
            return datetime.fromisoformat(timestamp.replace('Z', '+00:00')).date()
        except ValueError:
            pass
    return None


def entry_total(entry: dict) -> int:
    return (
        int(entry.get('input', 0) or 0)
        + int(entry.get('output', 0) or 0)
        + int(entry.get('cache_read', 0) or 0)
        + int(entry.get('cache_write', 0) or 0)
        + int(entry.get('thinking', 0) or 0)
    )


def ingest_entries(
    new_entries: list[dict],
    *,
    load_cached: Callable[[], list[dict]],
    save_cached: Callable[[list[dict]], None],
) -> IngestResult:
    cached = load_cached()
    seen = {
        entry.get('response_id')
        for entry in cached
        if entry.get('response_id')
    }
    merged = list(cached)
    new_count = 0
    duplicate_count = 0
    for entry in new_entries:
        response_id = entry.get('response_id')
        if response_id and response_id in seen:
            duplicate_count += 1
            continue
        if response_id:
            seen.add(response_id)
        merged.append(entry)
        new_count += 1
    save_cached(merged)
    return IngestResult(
        received=len(new_entries),
        new=new_count,
        duplicate=duplicate_count,
        total_cache=len(merged),
    )


def scan_conversation_databases(
    conversation_dirs: list[str],
) -> dict[str, set[int]]:
    discovered: dict[str, set[int]] = {}
    for directory in conversation_dirs:
        if not os.path.exists(directory):
            continue
        for database_path in glob.glob(os.path.join(directory, '*.db')):
            cascade_id = os.path.splitext(os.path.basename(database_path))[0]
            connection = None
            try:
                connection = sqlite3.connect(
                    f'file:{database_path}?mode=ro', uri=True
                )
                cursor = connection.cursor()
                cursor.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='gen_metadata';"
                )
                if cursor.fetchone():
                    cursor.execute('SELECT idx FROM gen_metadata;')
                    discovered[cascade_id] = {
                        int(row[0])
                        for row in cursor.fetchall()
                        if row[0] is not None
                    }
            except Exception as error:
                print(
                    f'Warning: Failed to scan Antigravity DB {database_path}: {error}',
                    file=sys.stderr,
                )
            finally:
                if connection:
                    connection.close()
    return discovered


def load_usage(
    *,
    load_cached: Callable[[], list[dict]],
    save_cached: Callable[[list[dict]], None],
    load_sync: Callable[[], dict[str, list[int]]],
    save_sync: Callable[[dict[str, list[int]]], None],
    discover: Callable[[], list[AntigravityConnection]],
    fetch: Callable[[list[AntigravityConnection]], list[dict]],
    rpc_call: Rpc,
    parse_usage: Callable[[dict, str], list[dict]],
    classify: Callable[[str], str],
    get_entry_date: Callable[[dict], date | None] = entry_to_date,
    get_entry_total: Callable[[dict], int] = entry_total,
    conversation_dirs: list[str],
) -> dict[str, DailyTokens]:
    """Load usage from cache and the live Language Server."""
    cached = load_cached()
    all_entries = list(cached)
    seen_response_ids = {
        entry.get('response_id')
        for entry in cached
        if entry.get('response_id')
    }
    sync_metadata = load_sync()
    sync_metadata_updated = False
    discovered = scan_conversation_databases(conversation_dirs)
    to_query = {
        cascade_id
        for cascade_id, database_indices in discovered.items()
        if not database_indices.issubset(
            set(sync_metadata.get(cascade_id, []))
        )
    }

    connections = discover()
    if connections:
        for summary in fetch(connections):
            cascade_id = (
                summary.get('cascadeId')
                or summary.get('trajectoryId')
                or summary.get('id')
            )
            if cascade_id:
                to_query.add(cascade_id)

        for cascade_id in sorted(to_query):
            for connection in connections:
                response = rpc_call(
                    connection,
                    'GetCascadeTrajectoryGeneratorMetadata',
                    {'cascadeId': cascade_id},
                )
                if not response:
                    continue
                for entry in parse_usage(response, cascade_id):
                    response_id = entry.get('response_id')
                    if response_id and response_id in seen_response_ids:
                        continue
                    if response_id:
                        seen_response_ids.add(response_id)
                    all_entries.append({
                        'model': entry['model'],
                        'timestamp': entry.get('timestamp_ms'),
                        'input': entry['input'],
                        'output': entry['output'],
                        'cache_read': entry['cache_read'],
                        'cache_write': entry['cache_write'],
                        'thinking': entry['thinking'],
                        'response_id': entry.get('response_id'),
                        'session_id': entry.get('session_id'),
                        'gen_idx': entry.get('gen_idx'),
                    })
                metadata = response.get('generatorMetadata', [])
                if isinstance(metadata, list):
                    sync_metadata[cascade_id] = list(range(len(metadata)))
                    sync_metadata_updated = True
                break

    if all_entries:
        save_cached(all_entries)
    if sync_metadata_updated:
        save_sync(sync_metadata)

    totals: dict[str, defaultdict[date, int]] = {
        'gemini': defaultdict(int),
        'anthropic': defaultdict(int),
        'gpt_opencode': defaultdict(int),
        'deepseek': defaultdict(int),
        'opencode_other': defaultdict(int),
    }
    for entry in all_entries:
        entry_date = get_entry_date(entry)
        if not entry_date:
            continue
        total = get_entry_total(entry)
        if total > 0:
            totals[classify(entry.get('model', ''))][entry_date] += total
    return {key: dict(value) for key, value in totals.items()}


def load_detailed(
    *,
    load_cached: Callable[[], list[dict]],
    get_entry_date: Callable[[dict], date | None] = entry_to_date,
) -> dict[date, dict[str, dict[str, int]]]:
    daily_models: defaultdict[date, defaultdict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(
            lambda: {
                'input': 0,
                'output': 0,
                'cache_read': 0,
                'cache_write': 0,
                'thinking': 0,
            }
        )
    )
    for entry in load_cached():
        entry_date = get_entry_date(entry)
        if not entry_date:
            continue
        model_id = entry.get('model', 'unknown')
        daily_models[entry_date][model_id]['input'] += int(entry.get('input', 0) or 0)
        daily_models[entry_date][model_id]['output'] += (
            int(entry.get('output', 0) or 0)
            + int(entry.get('thinking', 0) or 0)
        )
        daily_models[entry_date][model_id]['cache_read'] += int(
            entry.get('cache_read', 0) or 0
        )
        daily_models[entry_date][model_id]['cache_write'] += int(
            entry.get('cache_write', 0) or 0
        )
    return {entry_date: dict(models) for entry_date, models in daily_models.items()}


def calculate_cost(
    detailed: dict[date, dict[str, dict[str, int]]],
    *,
    pricing_lookup: Callable[[str], dict | None] = get_pricing,
    cost_calculator: Callable[..., float] = calc_cost,
) -> DailyCosts:
    result: defaultdict[date, float] = defaultdict(float)
    for entry_date, models in detailed.items():
        for model_id, tokens in models.items():
            pricing = pricing_lookup(model_id) or pricing_lookup(
                f'antigravity-{model_id}'
            )
            if not pricing and 'gemini' in (model_id or '').lower():
                pricing = pricing_lookup('gemini-3-flash')
            if not pricing:
                continue
            result[entry_date] += cost_calculator(
                pricing,
                input_tokens=tokens['input'] + tokens['cache_read'],
                output_tokens=tokens['output'],
                cached_tokens=tokens['cache_read'],
                cache_write_tokens=tokens['cache_write'],
            )
    return dict(result)


def model_family(label: str, model_id: str) -> str:
    combined = f'{label} {model_id}'.lower()
    if any(value in combined for value in (
        'gemini',
        'placeholder_m20',
        'placeholder_m132',
        'placeholder_m187',
        'placeholder_m36',
        'placeholder_m16',
    )):
        return 'Gemini'
    if any(value in combined for value in (
        'claude',
        'placeholder_m35',
        'placeholder_m26',
    )):
        return 'Claude'
    if 'gpt' in combined or 'gpt-oss' in combined:
        return 'GPT'
    return 'Other'


def export_quota(
    *,
    discover: Callable[[], list[AntigravityConnection]],
    rpc_call: Rpc,
    family_for_model: Callable[[str, str], str] = model_family,
) -> list[dict]:
    connections = discover()
    if not connections:
        return []
    response = None
    for connection in connections:
        response = rpc_call(connection, 'GetCascadeModelConfigData', {})
        if response:
            break
    if not response:
        return []
    configs = response.get('clientModelConfigs', [])
    if not isinstance(configs, list):
        return []

    families: dict[str, dict] = {}
    for config in configs:
        if not isinstance(config, dict):
            continue
        label = config.get('label', '')
        model_or_alias = config.get('modelOrAlias')
        model_id = (
            model_or_alias.get('model', '')
            if isinstance(model_or_alias, dict)
            else ''
        )
        quota_info = config.get('quotaInfo', {})
        if not isinstance(quota_info, dict):
            continue
        remaining = quota_info.get('remainingFraction')
        if not isinstance(remaining, (int, float)):
            continue
        family = family_for_model(label, model_id)
        used_percentage = max(0, min(100, int(round((1 - remaining) * 100))))
        existing = families.get(family)
        if existing is None or used_percentage > existing['percentage']:
            families[family] = {
                'percentage': used_percentage,
                'reset_iso': quota_info.get('resetTime'),
            }

    snapshots: list[dict] = []
    for family, info in sorted(families.items()):
        reset_iso = info.get('reset_iso')
        reset_ms: int | None = None
        reset_iso_local: str | None = None
        if reset_iso:
            try:
                parsed = datetime.fromisoformat(reset_iso.replace('Z', '+00:00'))
                reset_ms = int(parsed.timestamp() * 1000)
                reset_iso_local = (
                    parsed.astimezone()
                    .replace(tzinfo=None)
                    .isoformat(timespec='minutes')
                )
            except ValueError:
                pass
        snapshots.append({
            'provider': 'antigravity',
            'label': f'{family} 5h',
            'percentage': info['percentage'],
            'next_reset_time_ms': reset_ms,
            'next_reset_iso': reset_iso_local,
        })
    return snapshots
