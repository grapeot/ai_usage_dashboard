"""Parse DeepSeek Harness (DSH) session logs for token usage accounting.

DSH persists one session log per session under ``~/.dsh/sessions`` as
Zstandard-compressed JSONL (``session.jsonl.zstd``) or plain JSONL
(``session.jsonl``). Provider-reported usage rides on two event types, both
keyed by ``(turn, step)``:

- ``assistant/chunk`` with ``chunk.type == "usage"`` — an early sample that
  survives a later request failure
- ``assistant/message`` with ``data.usage`` — the final sample for the same
  turn/step

A repeated sample for one turn/step replaces the earlier value instead of
adding to it (mirroring DSH's ``tokenUsage`` projection in
``packages/llm/token-meter/src/usage-projection.ts``), so replaying the log
with last-wins semantics per key reproduces the harness's own totals.

Local LM Studio models (``lmstudio/*``) are free local compute; they are
reported for volume only and price to $0 through the pricing table.
"""
from __future__ import annotations

import json
import subprocess
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator, TypedDict

from pricing_config import calc_cost, get_pricing

DEFAULT_DSH_SESSIONS_DIR = Path.home() / '.dsh' / 'sessions'

# GLM models served by the Z.ai provider. The Z.ai usage monitor API does not
# see DSH-routed calls, so these merge into the dashboard GLM bucket (same
# precedent as OpenCode-routed GLM).
DSH_GLM_PROVIDER = 'zai'
DSH_GLM_MODEL_PREFIXES = ('glm-',)

DailyTokens = dict[date, int]


class DshUsageRecord(TypedDict):
    """One finalized usage sample: the last report for a (turn, step) key."""

    time: datetime
    model: str  # 'provider/model' or 'unknown'
    input: int
    output: int
    cache_read: int
    cache_write: int


def is_dsh_glm_model(model: str) -> bool:
    """True for Z.ai-served GLM models that belong in the GLM bucket."""
    if '/' not in model:
        return False
    provider, remainder = model.split('/', 1)
    return provider == DSH_GLM_PROVIDER and remainder.startswith(DSH_GLM_MODEL_PREFIXES)


def _iter_session_files(sessions_dir: Path) -> list[Path]:
    if not sessions_dir.is_dir():
        return []
    return sorted(sessions_dir.glob('*/*/session.jsonl*'))


def _read_session_text(file_path: Path) -> str:
    if file_path.suffix == '.zstd':
        completed = subprocess.run(
            ['zstd', '-d', '-c', '--', str(file_path)],
            capture_output=True,
            check=False,
        )
        # A torn final frame still emits the complete earlier frames on stdout;
        # only a fully unusable artifact fails (same tolerance as the exporter).
        if completed.returncode != 0 and not completed.stdout:
            raise RuntimeError(f"zstd exited {completed.returncode} with no decompressed output")
        return completed.stdout.decode('utf-8', errors='replace')
    return file_path.read_text(encoding='utf-8', errors='replace')


def _format_model(provider: Any, model: Any) -> str:
    provider = str(provider or '').strip()
    model = str(model or '').strip()
    if provider and model:
        return f'{provider}/{model}'
    return model or 'unknown'


def _usage_of(event: dict[str, Any]) -> dict[str, Any] | None:
    """Extract a usage sample from a chunk or finalized-message event."""
    data = event.get('data')
    if not isinstance(data, dict):
        return None
    if event.get('type') == 'assistant/chunk':
        chunk = data.get('chunk')
        if isinstance(chunk, dict) and chunk.get('type') == 'usage':
            usage = chunk.get('usage')
            return usage if isinstance(usage, dict) else None
        return None
    if event.get('type') == 'assistant/message':
        usage = data.get('usage')
        return usage if isinstance(usage, dict) else None
    return None


def _int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _parse_session_log(text: str) -> tuple[str, list[DshUsageRecord]]:
    """Replay one session log with the harness's last-wins usage semantics."""
    session_id = ''
    current_model = 'unknown'
    # (turn, step) -> index into samples; replacement overwrites in place.
    samples: list[DshUsageRecord] = []
    sample_keys: dict[tuple[int, int], int] = {}

    for raw_line in text.splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            # Live logs are appended in durable batches; a torn final record
            # is discarded rather than failing the whole session.
            continue
        if not isinstance(event, dict):
            continue

        event_type = event.get('type')
        data = event.get('data')
        data = data if isinstance(data, dict) else {}

        if event_type == 'session':
            session_id = str(event.get('id') or session_id)
            continue

        if event_type == 'request/header':
            header = data.get('header')
            config = header.get('config') if isinstance(header, dict) else None
            config = config if isinstance(config, dict) else {}
            model = _format_model(config.get('provider'), config.get('model'))
            if model != 'unknown':
                current_model = model
            continue

        if event_type == 'assistant/message':
            message = data.get('message')
            source = message.get('source') if isinstance(message, dict) else None
            if isinstance(source, dict):
                model = _format_model(source.get('provider'), source.get('model'))
                if model != 'unknown':
                    current_model = model

        usage = _usage_of(event)
        if usage is None:
            continue
        raw_time = event.get('time')
        time_ms = raw_time if isinstance(raw_time, int) and not isinstance(raw_time, bool) else 0
        if not time_ms:
            continue  # a sample without a timestamp cannot be placed on a day
        key = (int(data.get('turn') or 0), int(data.get('step') or 0))
        record: DshUsageRecord = {
            'time': datetime.fromtimestamp(time_ms / 1000),
            'model': current_model,
            'input': _int(usage.get('inputTokens')),
            'output': _int(usage.get('outputTokens')),
            'cache_read': _int(usage.get('cacheReadTokens')),
            'cache_write': _int(usage.get('cacheWriteTokens')),
        }
        index = sample_keys.get(key)
        if index is None:
            sample_keys[key] = len(samples)
            samples.append(record)
        else:
            samples[index] = record

    return session_id, samples


def iter_dsh_usage_records(
    sessions_dir: Path | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> Iterator[DshUsageRecord]:
    sessions_dir = sessions_dir or DEFAULT_DSH_SESSIONS_DIR
    seen_session_ids: set[str] = set()
    for file_path in _iter_session_files(sessions_dir):
        try:
            session_id, samples = _parse_session_log(_read_session_text(file_path))
        except (OSError, RuntimeError):
            continue  # one unreadable session never blocks the rest
        # A session directory can hold both encodings during a config change;
        # count each session once.
        dedup_id = session_id or str(file_path)
        if dedup_id in seen_session_ids:
            continue
        seen_session_ids.add(dedup_id)
        for record in samples:
            record_date = record['time'].date()
            if start_date and record_date < start_date:
                continue
            if end_date and record_date > end_date:
                continue
            yield record


def _record_total(record: DshUsageRecord) -> int:
    return record['input'] + record['output'] + record['cache_read'] + record['cache_write']


def load_dsh(
    sessions_dir: Path | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> DailyTokens:
    """Daily total tokens across all DSH sessions."""
    daily: defaultdict[date, int] = defaultdict(int)
    for record in iter_dsh_usage_records(sessions_dir=sessions_dir, start_date=start_date, end_date=end_date):
        total = _record_total(record)
        if total <= 0:
            continue
        daily[record['time'].date()] += total
    return dict(daily)


def load_dsh_detailed(
    sessions_dir: Path | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[date, dict[str, dict[str, int]]]:
    """Per-model per-day token breakdown (same shape as the Claude loader)."""
    daily_models: defaultdict[date, defaultdict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0})
    )
    for record in iter_dsh_usage_records(sessions_dir=sessions_dir, start_date=start_date, end_date=end_date):
        entry = daily_models[record['time'].date()][record['model']]
        entry['input'] += record['input']
        entry['output'] += record['output']
        entry['cache_read'] += record['cache_read']
        entry['cache_write'] += record['cache_write']
    return {d: dict(m) for d, m in daily_models.items()}


def calc_dsh_cost(detailed: dict[date, dict[str, dict[str, int]]]) -> dict[date, float]:
    """API-equivalent cost estimate; local models price to $0 via the table."""
    result: defaultdict[date, float] = defaultdict(float)
    for dt, models in detailed.items():
        for model_id, tok in models.items():
            pricing = get_pricing(model_id)
            if pricing is None and '/' in model_id:
                pricing = get_pricing(model_id.split('/', 1)[1])
            if pricing is None:
                continue
            result[dt] += calc_cost(
                pricing,
                input_tokens=tok['input'],
                output_tokens=tok['output'],
                cached_tokens=tok['cache_read'],
                cache_write_tokens=tok['cache_write'],
            )
    return dict(result)
