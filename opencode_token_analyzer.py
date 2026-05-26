#!/usr/bin/env python3
"""
OpenCode Token Usage Analyzer

分析 OpenCode SQLite 数据库中的 token 使用情况，尝试推断 Claude 配额。

原理参考 CC Monitor:
- 5小时滚动 session 窗口
- P90 (90th percentile) 限额检测
- 燃烧率计算

用法:
    python opencode_token_analyzer.py                    # 显示总体统计
    python opencode_token_analyzer.py --session          # 按 session 分析
    python opencode_token_analyzer.py --detect-limit     # 尝试检测限额
    python opencode_token_analyzer.py --provider claude  # 只看特定 provider
"""

import sqlite3
import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
import statistics

OPENCODE_DB = Path.home() / '.local' / 'share' / 'opencode' / 'opencode.db'

# 已知的套餐限额 (参考 CC Monitor)
KNOWN_LIMITS = {
    'pro': 19_000,
    'max5': 88_000,
    'max20': 220_000,
}


def get_db_connection():
    """以只读模式连接数据库"""
    return sqlite3.connect(f'file:{OPENCODE_DB}?mode=ro', uri=True)


def extract_tokens(data: dict) -> dict:
    """从消息数据中提取 token 信息"""
    tokens = data.get('tokens', {})
    return {
        'total': tokens.get('total', 0),
        'input': tokens.get('input', 0),
        'output': tokens.get('output', 0),
        'reasoning': tokens.get('reasoning', 0),
        'cache_read': tokens.get('cache', {}).get('read', 0),
        'cache_write': tokens.get('cache', {}).get('write', 0),
    }


def sum_tokens(tokens_list: list[dict]) -> dict:
    """汇总多个 token 字典"""
    result = {'total': 0, 'input': 0, 'output': 0, 'reasoning': 0, 'cache_read': 0, 'cache_write': 0}
    for t in tokens_list:
        for k in result:
            result[k] += t.get(k, 0)
    return result


def format_number(n: int) -> str:
    """格式化数字，添加千位分隔符"""
    return f"{n:,}"


def format_tokens(tokens: dict) -> str:
    """格式化 token 显示"""
    return (
        f"total={format_number(tokens['total'])} "
        f"(in={format_number(tokens['input'])} "
        f"out={format_number(tokens['output'])} "
        f"cache_r={format_number(tokens['cache_read'])} "
        f"cache_w={format_number(tokens['cache_write'])})"
    )


def get_all_messages(provider_filter: str = None, hours: int = None):
    """获取所有 assistant 消息"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = """
        SELECT id, session_id, time_created, data
        FROM message
        WHERE json_extract(data, '$.role') = 'assistant'
    """
    params = []
    
    if provider_filter:
        query += " AND json_extract(data, '$.providerID') LIKE ?"
        params.append(f'%{provider_filter}%')
    
    if hours:
        cutoff = int((datetime.now() - timedelta(hours=hours)).timestamp() * 1000)
        query += " AND time_created > ?"
        params.append(cutoff)
    
    query += " ORDER BY time_created DESC"
    
    cursor.execute(query, params)
    
    messages = []
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


def analyze_by_provider(messages: list[dict]):
    """按 provider 统计"""
    by_provider = defaultdict(lambda: {'count': 0, 'tokens': []})
    
    for msg in messages:
        provider = msg['provider']
        by_provider[provider]['count'] += 1
        by_provider[provider]['tokens'].append(msg['tokens'])
    
    print("\n" + "="*80)
    print("按 Provider 统计")
    print("="*80)
    
    for provider in sorted(by_provider.keys(), key=lambda p: sum(t['total'] for t in by_provider[p]['tokens']), reverse=True):
        data = by_provider[provider]
        total_tokens = sum_tokens(data['tokens'])
        print(f"\n{provider}:")
        print(f"  消息数: {data['count']}")
        print(f"  Tokens: {format_tokens(total_tokens)}")


def analyze_by_model(messages: list[dict]):
    """按 model 统计"""
    by_model = defaultdict(lambda: {'count': 0, 'tokens': []})
    
    for msg in messages:
        model = msg['model']
        by_model[model]['count'] += 1
        by_model[model]['tokens'].append(msg['tokens'])
    
    print("\n" + "="*80)
    print("按 Model 统计")
    print("="*80)
    
    for model in sorted(by_model.keys(), key=lambda m: sum(t['total'] for t in by_model[m]['tokens']), reverse=True):
        data = by_model[model]
        total_tokens = sum_tokens(data['tokens'])
        print(f"\n{model}:")
        print(f"  消息数: {data['count']}")
        print(f"  Tokens: {format_tokens(total_tokens)}")


def analyze_by_session(messages: list[dict]):
    """按 session 统计，模拟 5 小时滚动窗口"""
    by_session = defaultdict(lambda: {'messages': [], 'start_time': None, 'end_time': None})
    
    for msg in messages:
        session_id = msg['session_id']
        by_session[session_id]['messages'].append(msg)
        if by_session[session_id]['start_time'] is None or msg['time'] < by_session[session_id]['start_time']:
            by_session[session_id]['start_time'] = msg['time']
        if by_session[session_id]['end_time'] is None or msg['time'] > by_session[session_id]['end_time']:
            by_session[session_id]['end_time'] = msg['time']
    
    print("\n" + "="*80)
    print("按 Session 统计 (5小时窗口)")
    print("="*80)
    
    # 按 session 开始时间排序
    sorted_sessions = sorted(
        [(sid, data) for sid, data in by_session.items()],
        key=lambda x: x[1]['start_time'],
        reverse=True
    )
    
    session_totals = []
    
    for session_id, data in sorted_sessions[:20]:  # 只显示最近 20 个
        tokens_list = [m['tokens'] for m in data['messages']]
        total = sum_tokens(tokens_list)
        duration = data['end_time'] - data['start_time']
        
        session_totals.append(total['total'])
        
        print(f"\nSession: {session_id[:20]}...")
        print(f"  时间: {data['start_time'].strftime('%Y-%m-%d %H:%M')} - {data['end_time'].strftime('%H:%M')} ({duration})")
        print(f"  消息数: {len(data['messages'])}")
        print(f"  Tokens: {format_tokens(total)}")
    
    return session_totals


def detect_limit(session_totals: list[int], hours: int = 192):
    """
    使用 P90 算法检测限额
    
    原理: 如果你曾经触达过限额，那你的最高使用量应该接近限额。
    使用 90th percentile 来过滤掉异常值。
    """
    print("\n" + "="*80)
    print("限额检测 (P90 算法)")
    print("="*80)
    
    if not session_totals:
        print("没有足够的 session 数据")
        return None
    
    session_totals_sorted = sorted(session_totals)
    
    # 计算各种统计量
    p50 = statistics.median(session_totals_sorted)
    p90 = statistics.quantiles(session_totals_sorted, n=10)[8]  # 90th percentile
    p95 = statistics.quantiles(session_totals_sorted, n=20)[18]  # 95th percentile
    max_val = max(session_totals_sorted)
    
    print(f"\nSession 统计 (共 {len(session_totals)} 个):")
    print(f"  P50 (中位数): {format_number(int(p50))}")
    print(f"  P90:         {format_number(int(p90))}")
    print(f"  P95:         {format_number(int(p95))}")
    print(f"  Max:         {format_number(max_val)}")
    
    print(f"\n已知套餐限额:")
    for plan, limit in KNOWN_LIMITS.items():
        usage_pct = (p90 / limit * 100) if limit > 0 else 0
        print(f"  {plan:6s}: {format_number(limit):>10s} (P90 使用率: {usage_pct:.1f}%)")
    
    # 尝试匹配最可能的套餐
    print(f"\n推断结果:")
    if max_val < KNOWN_LIMITS['pro'] * 0.5:
        print(f"  ⚠️ 最高使用量 ({format_number(max_val)}) 远低于 Pro 限额")
        print(f"     可能原因: 你还没有触达过限额，无法准确推断")
        print(f"     建议: 手动指定你的套餐类型")
    elif p90 < KNOWN_LIMITS['pro']:
        print(f"  推测套餐: Pro (~{format_number(KNOWN_LIMITS['pro'])})")
    elif p90 < KNOWN_LIMITS['max5']:
        print(f"  推测套餐: Max5 (~{format_number(KNOWN_LIMITS['max5'])})")
    else:
        print(f"  推测套餐: Max20 (~{format_number(KNOWN_LIMITS['max20'])})")
    
    print(f"\n💡 P90 推断限额: {format_number(int(p90))}")
    
    return int(p90)


def analyze_daily(messages: list[dict]):
    """按日期统计"""
    by_date = defaultdict(lambda: {'tokens': [], 'messages': 0})
    
    for msg in messages:
        date_key = msg['time'].date()
        by_date[date_key]['tokens'].append(msg['tokens'])
        by_date[date_key]['messages'] += 1
    
    print("\n" + "="*80)
    print("按日期统计 (最近 14 天)")
    print("="*80)
    
    sorted_dates = sorted(by_date.keys(), reverse=True)[:14]
    
    for date in sorted_dates:
        data = by_date[date]
        total = sum_tokens(data['tokens'])
        print(f"\n{date}:")
        print(f"  消息数: {data['messages']}")
        print(f"  Tokens: {format_tokens(total)}")


def show_current_session(messages: list[dict], detected_limit: int = None):
    """显示当前 session 的使用情况"""
    if not messages:
        print("没有消息数据")
        return
    
    # 按时间排序
    sorted_msgs = sorted(messages, key=lambda m: m['time_created'])
    
    # 找到最近的 session
    latest_session = sorted_msgs[-1]['session_id']
    session_msgs = [m for m in sorted_msgs if m['session_id'] == latest_session]
    
    if not session_msgs:
        return
    
    start_time = min(m['time'] for m in session_msgs)
    end_time = max(m['time'] for m in session_msgs)
    now = datetime.now()
    
    # 计算 5 小时窗口
    window_end = start_time + timedelta(hours=5)
    remaining_time = window_end - now if now < window_end else timedelta(0)
    
    tokens_list = [m['tokens'] for m in session_msgs]
    total = sum_tokens(tokens_list)
    
    print("\n" + "="*80)
    print("当前 Session 状态")
    print("="*80)
    print(f"\nSession ID: {latest_session[:30]}...")
    print(f"开始时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"最后活动: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"窗口结束: {window_end.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"剩余时间: {remaining_time}")
    print(f"消息数: {len(session_msgs)}")
    print(f"已用 Tokens: {format_tokens(total)}")
    
    if detected_limit:
        usage_pct = (total['total'] / detected_limit * 100)
        remaining = max(0, detected_limit - total['total'])
        print(f"\n配额状态 (限额: {format_number(detected_limit)}):")
        print(f"  已用: {usage_pct:.1f}%")
        print(f"  剩余: {format_number(remaining)}")
        
        # 计算燃烧率
        if session_msgs:
            elapsed = (end_time - start_time).total_seconds() / 60  # 分钟
            if elapsed > 0:
                burn_rate = total['total'] / elapsed  # tokens per minute
                if burn_rate > 0 and remaining > 0:
                    time_to_limit = remaining / burn_rate  # 分钟
                    print(f"  燃烧率: {burn_rate:.1f} tokens/min")
                    print(f"  预计耗尽: {time_to_limit:.0f} 分钟 ({time_to_limit/60:.1f} 小时)")


def main():
    parser = argparse.ArgumentParser(description='OpenCode Token 使用分析器')
    parser.add_argument('--provider', '-p', type=str, help='过滤 provider (如 claude, anthropic)')
    parser.add_argument('--hours', type=int, default=192, help='分析最近 N 小时的数据 (默认 192/8天)')
    parser.add_argument('--session', '-s', action='store_true', help='按 session 分析')
    parser.add_argument('--detect-limit', '-d', action='store_true', help='尝试检测限额')
    parser.add_argument('--daily', action='store_true', help='按日期统计')
    parser.add_argument('--model', '-m', action='store_true', help='按 model 统计')
    
    args = parser.parse_args()
    
    print(f"OpenCode Token Analyzer")
    print(f"数据库: {OPENCODE_DB}")
    print(f"分析范围: 最近 {args.hours} 小时")
    if args.provider:
        print(f"Provider 过滤: {args.provider}")
    
    # 获取数据
    messages = get_all_messages(provider_filter=args.provider, hours=args.hours)
    print(f"\n共找到 {len(messages)} 条 assistant 消息")
    
    if not messages:
        print("没有数据")
        return
    
    # 默认显示所有统计
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
    
    # 显示当前 session
    show_current_session(messages, detected_limit)
    
    print("\n" + "="*80)
    print("完成")
    print("="*80)


if __name__ == '__main__':
    main()
