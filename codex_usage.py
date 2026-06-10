#!/usr/bin/env python3
"""
Export and calculate Codex token usage with custom pricing.
"""
import argparse
import json
import subprocess
import os
import sys
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

PRICING = {
    'gpt-5.3-codex': {
        'input': 1.75,
        'cached_input': 0.175,
        'output': 14.00,
    },
}

def get_date_range(since=None):
    today = datetime.now()
    if since:
        if len(since) == 8:
            start = datetime.strptime(since, '%Y%m%d')
        else:
            start = datetime.strptime(since, '%Y-%m-%d')
    else:
        start = today - timedelta(days=29)
    return start.strftime('%Y%m%d'), today.strftime('%Y%m%d')

def parse_args():
    parser = argparse.ArgumentParser(description='Codex usage with custom pricing')
    parser.add_argument('-s', '--since', type=str, help='Start date (YYYYMMDD or YYYY-MM-DD)')
    parser.add_argument('--days', type=int, help='Last N days (alternative to --since)')
    return parser.parse_args()

def fetch_codex_data(since_date):
    result = subprocess.run(
        ['npx', '-y', 'ccusage', 'codex', 'daily', '--json', '-s', since_date],
        capture_output=True,
        text=True,
        cwd=SCRIPT_DIR,
        timeout=180,
    )
    if result.returncode != 0:
        print(f"Error: {result.stderr}", file=sys.stderr)
        return None
    return json.loads(result.stdout)


def parse_ccusage_daily_date(value):
    try:
        return datetime.strptime(value, '%b %d, %Y').strftime('%Y-%m-%d')
    except ValueError:
        return datetime.strptime(value, '%Y-%m-%d').strftime('%Y-%m-%d')

def calculate_cost_for_model(model_data, model_name):
    if model_name not in PRICING:
        return None
    p = PRICING[model_name]
    input_tokens = model_data.get('inputTokens', 0)
    cached_tokens = model_data.get('cachedInputTokens', 0)
    output_tokens = model_data.get('outputTokens', 0)
    
    non_cached = input_tokens - cached_tokens
    input_cost = non_cached * p['input'] / 1_000_000
    cached_cost = cached_tokens * p['cached_input'] / 1_000_000
    output_cost = output_tokens * p['output'] / 1_000_000
    return input_cost + cached_cost + output_cost

def main():
    args = parse_args()
    
    if args.days:
        since = (datetime.now() - timedelta(days=args.days - 1)).strftime('%Y%m%d')
    elif args.since:
        s = args.since
        since = s.replace('-', '') if '-' in s else s
    else:
        since, _ = get_date_range()
    
    print(f"Fetching Codex data since {since}...")
    
    data = fetch_codex_data(since)
    if not data:
        sys.exit(1)
    
    with open(os.path.join(SCRIPT_DIR, 'usage.json'), 'w') as f:
        json.dump(data, f, indent=2)
    
    print(f"\n{'Date':<12} {'Input':>12} {'Cached':>12} {'Output':>10} {'Reason':>10} {'Cost':>10}")
    print("-" * 78)
    
    total_cost = 0
    total_input = 0
    total_cached = 0
    total_output = 0
    total_reasoning = 0
    
    for entry in data.get('daily', []):
        date_str = entry['date']
        dt = parse_ccusage_daily_date(date_str)
        
        models = entry.get('models', {})
        original_cost = entry.get('costUSD', 0)
        custom_priced_cost = 0
        has_custom_pricing = False
        
        for model_name, model_data in models.items():
            model_cost = calculate_cost_for_model(model_data, model_name)
            if model_cost is not None:
                custom_priced_cost += model_cost
                has_custom_pricing = True
        
        if original_cost == 0:
            day_cost = custom_priced_cost
        elif has_custom_pricing and original_cost > 0:
            day_cost = original_cost + custom_priced_cost
        else:
            day_cost = original_cost
        
        inp = entry.get('inputTokens', 0)
        cached = entry.get('cachedInputTokens', 0)
        out = entry.get('outputTokens', 0)
        reason = entry.get('reasoningOutputTokens', 0)
        
        total_input += inp
        total_cached += cached
        total_output += out
        total_reasoning += reason
        total_cost += day_cost
        
        print(f"{dt} {inp:>12,} {cached:>12,} {out:>10,} {reason:>10,} ${day_cost:>9.2f}")
    
    print("-" * 78)
    print(f"{'TOTAL':<12} {total_input:>12,} {total_cached:>12,} {total_output:>10,} {total_reasoning:>10,} ${total_cost:>9.2f}")
    
    original_total = data.get('totals', {}).get('costUSD', 0)
    print(f"\nOriginal ccusage total: ${original_total:.2f}")
    print(f"Recalculated total:     ${total_cost:.2f}")
    print(f"Difference:             ${total_cost - original_total:.2f}")

if __name__ == '__main__':
    main()
