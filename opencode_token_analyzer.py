#!/usr/bin/env python3
"""
OpenCode Token Usage Analyzer

Analyze token usage in the OpenCode SQLite database and estimate Claude quota limits.

Inspired by CC Monitor:
- 5-hour rolling session windows
- P90 quota detection
- burn-rate calculation

Usage:
    python opencode_token_analyzer.py                    # show overall stats
    python opencode_token_analyzer.py --session          # analyze by session
    python opencode_token_analyzer.py --detect-limit     # try to detect quota limits
    python opencode_token_analyzer.py --provider claude  # filter to one provider
"""

import sqlite3
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
import statistics
from typing import Any

OPENCODE_DB = Path.home() / '.local' / 'share' / 'opencode' / 'opencode.db'

# Known plan limits, based on CC Monitor references
KNOWN_LIMITS = {
    'pro': 19_000,
    'max5': 88_000,
    'max20': 220_000,
}


def get_db_connection():
    """Connect to the database in read-only mode."""
    return sqlite3.connect(f'file:{OPENCODE_DB}?mode=ro', uri=True)


def extract_tokens(data: dict[str, Any]) -> dict[str, int]:
    """Extract token fields from message data."""
    tokens = data.get('tokens', {})
    return {
        'total': tokens.get('total', 0),
        'input': tokens.get('input', 0),
        'output': tokens.get('output', 0),
        'reasoning': tokens.get('reasoning', 0),
        'cache_read': tokens.get('cache', {}).get('read', 0),
        'cache_write': tokens.get('cache', {}).get('write', 0),
    }


def sum_tokens(tokens_list: list[dict[str, int]]) -> dict[str, int]:
    """Sum multiple token dictionaries."""
    result = {'total': 0, 'input': 0, 'output': 0, 'reasoning': 0, 'cache_read': 0, 'cache_write': 0}
    for t in tokens_list:
        for k in result:
            result[k] += t.get(k, 0)
    return result


def format_number(n: int) -> str:
    """Format a number with thousands separators."""
    return f"{n:,}"


def format_tokens(tokens: dict[str, int]) -> str:
    """Format token counts for display."""
    return (
        f"total={format_number(tokens['total'])} "
        f"(in={format_number(tokens['input'])} "
        f"out={format_number(tokens['output'])} "
        f"cache_r={format_number(tokens['cache_read'])} "
        f"cache_w={format_number(tokens['cache_write'])})"
    )


def get_all_messages(provider_filter: str | None = None, hours: int | None = None) -> list[dict[str, Any]]:
    """Load all assistant messages."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = """
        SELECT id, session_id, time_created, data
        FROM message
        WHERE json_extract(data, '$.role') = 'assistant'
    """
    params: list[str | int] = []
    
    if provider_filter:
        query += " AND json_extract(data, '$.providerID') LIKE ?"
        params.append(f'%{provider_filter}%')
    
    if hours:
        cutoff = int((datetime.now() - timedelta(hours=hours)).timestamp() * 1000)
        query += " AND time_created > ?"
        params.append(cutoff)
    
    query += " ORDER BY time_created DESC"
    
    cursor.execute(query, params)
    
    messages: list[dict[str, Any]] = []
    for msg_id, session_id, time_created, data_str in cursor:
        try:
            data = json.loads(data_str)
            messages.append({
                'id': msg_id,
                'session_id': session_id,
                'time_created': time_created,
                'time': datetime.fromtimestamp(time_created / 1000),
                'tokens': extract_tokens(data),
                'provider': data.get('providerID', 'unknown'),
                'model': data.get('modelID', 'unknown'),
            })
        except json.JSONDecodeError:
            continue
    
    conn.close()
    return messages


def analyze_by_provider(messages: list[dict[str, Any]]) -> None:
    """Group usage by provider."""
    by_provider: defaultdict[str, dict[str, Any]] = defaultdict(lambda: {'count': 0, 'tokens': []})
    
    for msg in messages:
        provider = msg['provider']
        by_provider[provider]['count'] += 1
        by_provider[provider]['tokens'].append(msg['tokens'])
    
    print("\n" + "="*80)
    print("Usage By Provider")
    print("="*80)
    
    for provider in sorted(by_provider.keys(), key=lambda p: sum(t['total'] for t in by_provider[p]['tokens']), reverse=True):
        data = by_provider[provider]
        total_tokens = sum_tokens(data['tokens'])
        print(f"\n{provider}:")
        print(f"  Messages: {data['count']}")
        print(f"  Tokens: {format_tokens(total_tokens)}")


def analyze_by_model(messages: list[dict[str, Any]]) -> None:
    """Group usage by model."""
    by_model: defaultdict[str, dict[str, Any]] = defaultdict(lambda: {'count': 0, 'tokens': []})
    
    for msg in messages:
        model = msg['model']
        by_model[model]['count'] += 1
        by_model[model]['tokens'].append(msg['tokens'])
    
    print("\n" + "="*80)
    print("Usage By Model")
    print("="*80)
    
    for model in sorted(by_model.keys(), key=lambda m: sum(t['total'] for t in by_model[m]['tokens']), reverse=True):
        data = by_model[model]
        total_tokens = sum_tokens(data['tokens'])
        print(f"\n{model}:")
        print(f"  Messages: {data['count']}")
        print(f"  Tokens: {format_tokens(total_tokens)}")


def analyze_by_session(messages: list[dict[str, Any]]) -> list[int]:
    """Group usage by session using a 5-hour rolling window."""
    by_session: defaultdict[str, dict[str, Any]] = defaultdict(lambda: {'messages': [], 'start_time': None, 'end_time': None})
    
    for msg in messages:
        session_id = msg['session_id']
        by_session[session_id]['messages'].append(msg)
        if by_session[session_id]['start_time'] is None or msg['time'] < by_session[session_id]['start_time']:
            by_session[session_id]['start_time'] = msg['time']
        if by_session[session_id]['end_time'] is None or msg['time'] > by_session[session_id]['end_time']:
            by_session[session_id]['end_time'] = msg['time']
    
    print("\n" + "="*80)
    print("Usage By Session (5-hour window)")
    print("="*80)
    
    # Sort by session start time
    sorted_sessions = sorted(
        [(sid, data) for sid, data in by_session.items()],
        key=lambda x: x[1]['start_time'],
        reverse=True
    )
    
    session_totals = []
    
    for session_id, data in sorted_sessions[:20]:  # Show the latest 20 only
        tokens_list = [m['tokens'] for m in data['messages']]
        total = sum_tokens(tokens_list)
        duration = data['end_time'] - data['start_time']
        
        session_totals.append(total['total'])
        
        print(f"\nSession: {session_id[:20]}...")
        print(f"  Time: {data['start_time'].strftime('%Y-%m-%d %H:%M')} - {data['end_time'].strftime('%H:%M')} ({duration})")
        print(f"  Messages: {len(data['messages'])}")
        print(f"  Tokens: {format_tokens(total)}")
    
    return session_totals


def detect_limit(session_totals: list[int], hours: int = 192) -> int | None:
    """
    Detect quota limits with a P90 heuristic.

    If usage has reached a plan limit before, high sessions should be close to that limit.
    The 90th percentile filters out extreme outliers.
    """
    print("\n" + "="*80)
    print("Quota Detection (P90 heuristic)")
    print("="*80)
    
    if not session_totals:
        print("Not enough session data")
        return None
    
    session_totals_sorted = sorted(session_totals)
    
    # Calculate summary statistics
    p50 = statistics.median(session_totals_sorted)
    p90 = statistics.quantiles(session_totals_sorted, n=10)[8]  # 90th percentile
    p95 = statistics.quantiles(session_totals_sorted, n=20)[18]  # 95th percentile
    max_val = max(session_totals_sorted)
    
    print(f"\nSession Stats (total {len(session_totals)} items):")
    print(f"  P50 (median): {format_number(int(p50))}")
    print(f"  P90:         {format_number(int(p90))}")
    print(f"  P95:         {format_number(int(p95))}")
    print(f"  Max:         {format_number(max_val)}")
    
    print(f"\nKnown plan limits:")
    for plan, limit in KNOWN_LIMITS.items():
        usage_pct = (p90 / limit * 100) if limit > 0 else 0
        print(f"  {plan:6s}: {format_number(limit):>10s} (P90 usage: {usage_pct:.1f}%)")
    
    # Try to match the most likely plan
    print(f"\nEstimated Result:")
    if max_val < KNOWN_LIMITS['pro'] * 0.5:
        print(f"  ⚠️ Maximum usage ({format_number(max_val)}) is far below the Pro limit")
        print(f"     Possible reason: usage has not reached the limit yet, so inference is uncertain")
        print(f"     Recommendation: specify your plan manually")
    elif p90 < KNOWN_LIMITS['pro']:
        print(f"  Estimated plan: Pro (~{format_number(KNOWN_LIMITS['pro'])})")
    elif p90 < KNOWN_LIMITS['max5']:
        print(f"  Estimated plan: Max5 (~{format_number(KNOWN_LIMITS['max5'])})")
    else:
        print(f"  Estimated plan: Max20 (~{format_number(KNOWN_LIMITS['max20'])})")
    
    print(f"\n💡 P90 inferred limit: {format_number(int(p90))}")
    
    return int(p90)


def analyze_daily(messages: list[dict[str, Any]]) -> None:
    """Group usage by date."""
    by_date: defaultdict[Any, dict[str, Any]] = defaultdict(lambda: {'tokens': [], 'messages': 0})
    
    for msg in messages:
        date_key = msg['time'].date()
        by_date[date_key]['tokens'].append(msg['tokens'])
        by_date[date_key]['messages'] += 1
    
    print("\n" + "="*80)
    print("Usage By Date (last 14 days)")
    print("="*80)
    
    sorted_dates = sorted(by_date.keys(), reverse=True)[:14]
    
    for date in sorted_dates:
        data = by_date[date]
        total = sum_tokens(data['tokens'])
        print(f"\n{date}:")
        print(f"  Messages: {data['messages']}")
        print(f"  Tokens: {format_tokens(total)}")


def show_current_session(messages: list[dict[str, Any]], detected_limit: int | None = None) -> None:
    """Show current session usage."""
    if not messages:
        print("No message data")
        return
    
    # Sort by time
    sorted_msgs = sorted(messages, key=lambda m: m['time_created'])
    
    # Find the latest session
    latest_session = sorted_msgs[-1]['session_id']
    session_msgs = [m for m in sorted_msgs if m['session_id'] == latest_session]
    
    if not session_msgs:
        return
    
    start_time = min(m['time'] for m in session_msgs)
    end_time = max(m['time'] for m in session_msgs)
    now = datetime.now()
    
    # Calculate the 5-hour window
    window_end = start_time + timedelta(hours=5)
    remaining_time = window_end - now if now < window_end else timedelta(0)
    
    tokens_list = [m['tokens'] for m in session_msgs]
    total = sum_tokens(tokens_list)
    
    print("\n" + "="*80)
    print("Current Session Status")
    print("="*80)
    print(f"\nSession ID: {latest_session[:30]}...")
    print(f"Start time: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Last activity: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Window end: {window_end.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Remaining time: {remaining_time}")
    print(f"Messages: {len(session_msgs)}")
    print(f"Tokens used: {format_tokens(total)}")
    
    if detected_limit:
        usage_pct = (total['total'] / detected_limit * 100)
        remaining = max(0, detected_limit - total['total'])
        print(f"\nQuota status (limit: {format_number(detected_limit)}):")
        print(f"  Used: {usage_pct:.1f}%")
        print(f"  Remaining: {format_number(remaining)}")
        
        # Calculate burn rate
        if session_msgs:
            elapsed = (end_time - start_time).total_seconds() / 60  # minutes
            if elapsed > 0:
                burn_rate = total['total'] / elapsed  # tokens per minute
                if burn_rate > 0 and remaining > 0:
                    time_to_limit = remaining / burn_rate  # minutes
                    print(f"  Burn rate: {burn_rate:.1f} tokens/min")
                    print(f"  Estimated time to limit: {time_to_limit:.0f} minutes ({time_to_limit/60:.1f} hours)")


def main():
    parser = argparse.ArgumentParser(description='OpenCode token usage analyzer')
    parser.add_argument('--provider', '-p', type=str, help='Filter provider, e.g. claude or anthropic')
    parser.add_argument('--hours', type=int, default=192, help='Analyze the last N hours (default: 192 / 8 days)')
    parser.add_argument('--session', '-s', action='store_true', help='Analyze by session')
    parser.add_argument('--detect-limit', '-d', action='store_true', help='Try to detect quota limit')
    parser.add_argument('--daily', action='store_true', help='Group by date')
    parser.add_argument('--model', '-m', action='store_true', help='Group by model')
    
    args = parser.parse_args()
    
    print(f"OpenCode Token Analyzer")
    print(f"Database: {OPENCODE_DB}")
    print(f"Analysis window: last {args.hours} hours")
    if args.provider:
        print(f"Provider filter: {args.provider}")
    
    # Load data
    messages = get_all_messages(provider_filter=args.provider, hours=args.hours)
    print(f"\nFound {len(messages)} assistant messages")
    
    if not messages:
        print("No data")
        return
    
    # Default: show all stats
    session_totals = None
    
    analyze_by_provider(messages)
    
    if args.model:
        analyze_by_model(messages)
    
    if args.daily:
        analyze_daily(messages)
    
    if args.session or args.detect_limit:
        session_totals = analyze_by_session(messages)
    
    detected_limit = None
    if args.detect_limit and session_totals:
        detected_limit = detect_limit(session_totals, args.hours)
    
    # Show current session
    show_current_session(messages, detected_limit)
    
    print("\n" + "="*80)
    print("Done")
    print("="*80)


if __name__ == '__main__':
    main()
