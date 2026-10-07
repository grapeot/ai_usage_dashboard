"""Append-only SQLite history for the AI Usage Dashboard.

The dashboard itself is stateless: every run overwrites ``token_usage_eink.json``
with the latest snapshot and throws the previous one away. This module adds the
missing temporal dimension by persisting two things to a local, gitignored
SQLite database:

- ``quota_samples``: a time series of provider quota bars (5h / weekly /
  monthly windows). One row is written on *every* observation — no
  deduplication and no retention policy yet — so the table is a faithful record
  of each read, including how long a percentage stayed flat. This is the
  primary signal. Quota bars are live readings scraped from provider pages;
  once a window rolls over the old reading is gone and cannot be reconstructed
  from any source database.
- ``usage_daily``: the dashboard's per-day aggregate token/cost rows, UPSERTed
  by date. Token volume is secondary here because it can always be rebuilt from
  the source databases (OpenCode, Claude Code, DSH, ...).

The main writer is the E1002 firmware: it wakes hourly and POSTs
``/api/v1/display/update``, which runs the full build and therefore records a
sample every hour. No separate scheduled sampler is needed.

Writers never block the dashboard: every ``record_*`` call is best-effort and
returns the number of rows written. Callers should wrap them in try/except so a
history failure can never break a dashboard run.

The database path is ``<repo>/quota_history.db`` by default, overridable with
the ``AI_USAGE_HISTORY_DB`` environment variable. It is listed in ``.gitignore``.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(SCRIPT_DIR, 'quota_history.db')

_CAPTURE_COLUMNS = {
    'recorded_at': 'TEXT',
    'measurement_source': 'TEXT',
    'percentage_resolution': 'REAL',
    'pool_id': 'TEXT',
    'account_fingerprint': 'TEXT',
    'model_quotas_json': 'TEXT',
    'raw_reset_ms': 'INTEGER',
    'quota_scope': 'TEXT',
    'absolute_limit_usd': 'REAL',
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS quota_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    provider TEXT NOT NULL,
    label TEXT NOT NULL,
    percentage REAL NOT NULL,
    reset_iso TEXT,
    reset_ms INTEGER,
    usage INTEGER,
    remaining INTEGER,
    source TEXT
);
CREATE INDEX IF NOT EXISTS idx_quota_samples_lookup
    ON quota_samples (provider, label, observed_at);

CREATE TABLE IF NOT EXISTS usage_daily (
    date TEXT PRIMARY KEY,
    source TEXT,
    updated_at TEXT NOT NULL,
    cursor INTEGER DEFAULT 0,
    glm INTEGER DEFAULT 0,
    gemini INTEGER DEFAULT 0,
    claude INTEGER DEFAULT 0,
    gpt INTEGER DEFAULT 0,
    deepseek INTEGER DEFAULT 0,
    grok INTEGER DEFAULT 0,
    qwen INTEGER DEFAULT 0,
    other INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    cost_usd REAL,
    ai_hours REAL DEFAULT 0
);
"""


def db_path(path: str | None = None) -> str:
    return path or os.environ.get('AI_USAGE_HISTORY_DB') or DEFAULT_DB_PATH


def _connect(path: str | None = None) -> sqlite3.Connection:
    """Open (and lazily initialise) the history database.

    WAL mode plus a busy timeout lets the dashboard and a concurrent reader
    (or a future second writer) touch the file without tripping over each other.
    """
    target = db_path(path)
    conn = sqlite3.connect(target, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=10000')
    conn.executescript(_SCHEMA)
    columns = {row['name'] for row in conn.execute('PRAGMA table_info(quota_samples)')}
    if not set(_CAPTURE_COLUMNS).issubset(columns):
        conn.execute('BEGIN IMMEDIATE')
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(quota_samples)')}
        for name, sql_type in _CAPTURE_COLUMNS.items():
            if name not in columns:
                conn.execute(f'ALTER TABLE quota_samples ADD COLUMN {name} {sql_type}')
        conn.commit()
    return conn


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec='milliseconds')


# Providers compute a window's reset as "now + TTL", so the same physical
# window can report reset times that differ by sub-second jitter between reads.
# Provider resets are aligned to a whole minute (observed as, e.g.,
# 14:00:00 +/- 0.4s), so rounding to the nearest minute collapses the jitter:
# both 13:59:59.98 and 14:00:00.39 land on 14:00:00. A genuine rollover moves by
# the full window length (hours or days) and stays distinct.
#
# Round (not floor) is deliberate: floor would push the earlier jitter sample
# of a whole-minute reset down to 13:59 and split one window into two, which is
# the exact failure this normalization exists to prevent.
_RESET_BUCKET_SECONDS = 60


def normalize_reset(reset_ms: int | None) -> int | None:
    """Round a reset timestamp to the nearest minute to absorb provider jitter.

    Provider resets are aligned to whole minutes; sub-minute differences are an
    artifact of the provider recomputing ``now + TTL`` at request time, not a
    real window change.
    """
    if reset_ms is None:
        return None
    bucket = _RESET_BUCKET_SECONDS * 1000
    return int(round(reset_ms / bucket) * bucket)


def reset_iso_from_ms(reset_ms: int | None) -> str | None:
    """Local ISO reset time derived from the normalized millisecond value.

    Deriving the ISO form from the normalized ms (rather than storing the raw
    ``next_reset_iso``) keeps the two columns consistent, so consumers using
    either field see the same jitter-free window identity.
    """
    if reset_ms is None:
        return None
    return datetime.fromtimestamp(reset_ms / 1000).replace(microsecond=0).isoformat()


def record_quota_snapshots(
    quotas: Sequence[Mapping[str, Any]],
    *,
    source: str = 'dashboard',
    observed_at: str | None = None,
    path: str | None = None,
) -> int:
    """Append every quota reading, one row per window per call.

    No deduplication: an unchanged reading is still recorded so the series
    carries how long a percentage stayed flat. Both the millisecond and ISO
    reset forms are normalized to the minute so provider jitter cannot
    masquerade as a window change. There is no retention policy yet; rows
    accumulate until one is added.
    """
    if not quotas:
        return 0
    recorded_at = _now()
    conn = _connect(path)
    inserted = 0
    try:
        for q in quotas:
            reset_ms = normalize_reset(q.get('next_reset_time_ms'))
            conn.execute(
                'INSERT INTO quota_samples '
                '(observed_at, provider, label, percentage, reset_iso, reset_ms, usage, remaining, source, '
                'recorded_at, measurement_source, percentage_resolution, pool_id, account_fingerprint, model_quotas_json, raw_reset_ms, quota_scope, absolute_limit_usd) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    observed_at or q.get('observed_at') or recorded_at,
                    str(q.get('provider', 'unknown')),
                    str(q.get('label', 'unknown')),
                    float(q.get('percentage', 0) or 0),
                    reset_iso_from_ms(reset_ms),
                    reset_ms,
                    q.get('usage'),
                    q.get('remaining'),
                    source,
                    recorded_at,
                    q.get('measurement_source'),
                    q.get('percentage_resolution'),
                    q.get('pool_id'),
                    q.get('account_fingerprint'),
                    json.dumps(q['model_quotas']) if q.get('model_quotas') is not None else None,
                    q.get('next_reset_time_ms'),
                    q.get('quota_scope'),
                    q.get('absolute_limit_usd'),
                ),
            )
            inserted += 1
        conn.commit()
    finally:
        conn.close()
    return inserted


def record_usage_daily(
    daily_entries: Sequence[Mapping[str, Any]],
    *,
    source: str = 'dashboard',
    path: str | None = None,
) -> int:
    """UPSERT the dashboard's per-day aggregate rows, keyed by date."""
    if not daily_entries:
        return 0
    updated_at = _now()
    conn = _connect(path)
    written = 0
    try:
        for entry in daily_entries:
            day = entry.get('date')
            if not day:
                continue
            categories = entry.get('categories') or {}
            conn.execute(
                """
                INSERT INTO usage_daily
                    (date, source, updated_at, cursor, glm, gemini, claude, gpt, deepseek, grok, qwen, other,
                     total_tokens, cost_usd, ai_hours)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    source = excluded.source,
                    updated_at = excluded.updated_at,
                    cursor = excluded.cursor,
                    glm = excluded.glm,
                    gemini = excluded.gemini,
                    claude = excluded.claude,
                    gpt = excluded.gpt,
                    deepseek = excluded.deepseek,
                    grok = excluded.grok,
                    qwen = excluded.qwen,
                    other = excluded.other,
                    total_tokens = excluded.total_tokens,
                    cost_usd = excluded.cost_usd,
                    ai_hours = excluded.ai_hours
                """,
                (
                    day,
                    source,
                    updated_at,
                    int(categories.get('cursor', 0) or 0),
                    int(categories.get('glm', 0) or 0),
                    int(categories.get('gemini', 0) or 0),
                    int(categories.get('claude', 0) or 0),
                    int(categories.get('gpt_opencode', 0) or 0),
                    int(categories.get('deepseek', 0) or 0),
                    int(categories.get('grok', 0) or 0),
                    int(categories.get('qwen', 0) or 0),
                    int(categories.get('other', 0) or 0),
                    int(entry.get('total_tokens', 0) or 0),
                    entry.get('cost_usd'),
                    float(entry.get('ai_hours', 0) or 0),
                ),
            )
            written += 1
        conn.commit()
    finally:
        conn.close()
    return written


def quota_history(
    provider: str | None = None,
    label: str | None = None,
    days: int | None = None,
    *,
    path: str | None = None,
) -> list[dict[str, Any]]:
    """Return quota samples ordered by observation time."""
    rows = read_quota_samples(path=path, provider=provider, label=label)
    if days is not None:
        cutoff = (datetime.now().astimezone() - timedelta(days=days)).timestamp()
        rows = [r for r in rows if datetime.fromisoformat(r['observed_at']).timestamp() >= cutoff]
    return sorted(rows, key=lambda r: datetime.fromisoformat(r['observed_at']).timestamp())


def _decode_quota_row(row: dict[str, Any]) -> dict[str, Any]:
    row.pop('id', None)
    encoded = row.pop('model_quotas_json', None)
    if encoded:
        try:
            row['model_quotas'] = json.loads(encoded)
        except (ValueError, TypeError):
            row['model_quotas'] = None
    return row


def _read_connection(path: str | None = None) -> sqlite3.Connection | None:
    target = Path(db_path(path))
    if not target.exists():
        return None
    conn = sqlite3.connect(f'{target.resolve().as_uri()}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def read_quota_samples(*, path: str | None = None, provider: str | None = None,
                       label: str | None = None) -> list[dict[str, Any]]:
    """Read old or current history without creating a DB or migrating its schema."""
    conn = _read_connection(path)
    if conn is None:
        return []
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='quota_samples'").fetchone():
            return []
        where, params = [], []
        if provider:
            where.append('provider = ?')
            params.append(provider)
        if label:
            where.append('label = ?')
            params.append(label)
        sql = 'SELECT * FROM quota_samples'
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        sql += ' ORDER BY observed_at, id'
        return [_decode_quota_row(dict(row)) for row in conn.execute(sql, params)]
    finally:
        conn.close()


def usage_history(days: int | None = None, *, path: str | None = None) -> list[dict[str, Any]]:
    """Return per-day aggregate usage rows ordered by date."""
    sql = 'SELECT * FROM usage_daily'
    params: list[Any] = []
    if days is not None:
        cutoff = (datetime.now() - timedelta(days=days)).date().isoformat()
        sql += ' WHERE date >= ?'
        params.append(cutoff)
    sql += ' ORDER BY date'
    conn = _read_connection(path)
    if conn is None:
        return []
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='usage_daily'").fetchone():
            return []
        return [dict(row) for row in conn.execute(sql, params)]
    finally:
        conn.close()


def _main() -> None:
    parser = argparse.ArgumentParser(description='Inspect the AI usage history database')
    parser.add_argument('--provider', help='Filter quota samples by provider, e.g. ollama')
    parser.add_argument('--label', help='Filter quota samples by window label, e.g. 7d')
    parser.add_argument('--days', type=int, help='Only show the last N days')
    parser.add_argument('--usage', action='store_true', help='Show the per-day usage history instead of quota samples')
    args = parser.parse_args()

    if args.usage:
        rows = usage_history(args.days)
        print(f"{'date':<12} {'total_tokens':>14} {'cost_usd':>10} {'ai_hours':>9}")
        for row in rows:
            cost = row.get('cost_usd')
            print(f"{row['date']:<12} {row['total_tokens']:>14,} "
                  f"{(f'${cost:.2f}' if cost is not None else '-'):>10} {row['ai_hours']:>9.2f}")
        return

    rows = quota_history(args.provider, args.label, args.days)
    print(f"{'observed_at':<20} {'provider':<10} {'label':<12} {'pct':>6} {'reset':<20}")
    for row in rows:
        print(f"{row['observed_at']:<20} {row['provider']:<10} {row['label']:<12} "
              f"{row['percentage']:>6.1f} {str(row.get('reset_iso') or ''):<20}")


if __name__ == '__main__':
    _main()
