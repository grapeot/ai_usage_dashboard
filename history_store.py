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
import os
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(SCRIPT_DIR, 'quota_history.db')

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
    return conn


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


# Providers compute a window's reset as "now + TTL", so the same physical
# window can report reset times that differ by sub-second jitter between reads.
# Rounding the reset to the minute collapses that jitter: two reads of the same
# window land on the same minute, while a genuine rollover moves by the full
# window length (hours or days) and stays distinct.
_RESET_BUCKET_SECONDS = 60


def normalize_reset(reset_ms: int | None) -> int | None:
    """Round a reset timestamp to the nearest minute to absorb provider jitter.

    Sub-minute differences are an artifact of the provider recomputing
    ``now + TTL`` at request time, not a real window change.
    """
    if reset_ms is None:
        return None
    bucket = _RESET_BUCKET_SECONDS * 1000
    return int(round(reset_ms / bucket) * bucket)


def record_quota_snapshots(
    quotas: Sequence[Mapping[str, Any]],
    *,
    source: str = 'dashboard',
    observed_at: str | None = None,
    path: str | None = None,
) -> int:
    """Append every quota reading, one row per window per call.

    No deduplication: an unchanged reading is still recorded so the series
    carries how long a percentage stayed flat. The reset timestamp is
    normalized to the minute so provider jitter cannot masquerade as a window
    change. There is no retention policy yet; rows accumulate until one is
    added.
    """
    if not quotas:
        return 0
    observed_at = observed_at or _now()
    conn = _connect(path)
    inserted = 0
    try:
        for q in quotas:
            conn.execute(
                'INSERT INTO quota_samples '
                '(observed_at, provider, label, percentage, reset_iso, reset_ms, usage, remaining, source) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    observed_at,
                    str(q.get('provider', 'unknown')),
                    str(q.get('label', 'unknown')),
                    float(q.get('percentage', 0) or 0),
                    q.get('next_reset_iso'),
                    normalize_reset(q.get('next_reset_time_ms')),
                    q.get('usage'),
                    q.get('remaining'),
                    source,
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
    where: list[str] = []
    params: list[Any] = []
    if provider:
        where.append('provider = ?')
        params.append(provider)
    if label:
        where.append('label = ?')
        params.append(label)
    if days:
        where.append('observed_at >= ?')
        params.append((datetime.now() - timedelta(days=days)).replace(microsecond=0).isoformat())
    sql = 'SELECT observed_at, provider, label, percentage, reset_iso, reset_ms, usage, remaining, source FROM quota_samples'
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY observed_at, id'
    conn = _connect(path)
    try:
        return [dict(row) for row in conn.execute(sql, params)]
    finally:
        conn.close()


def usage_history(days: int | None = None, *, path: str | None = None) -> list[dict[str, Any]]:
    """Return per-day aggregate usage rows ordered by date."""
    sql = 'SELECT * FROM usage_daily'
    params: list[Any] = []
    if days:
        cutoff = (datetime.now() - timedelta(days=days)).date().isoformat()
        sql += ' WHERE date >= ?'
        params.append(cutoff)
    sql += ' ORDER BY date'
    conn = _connect(path)
    try:
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
