import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from codex_usage import calculate_cost_for_model


def test_calculate_cost_for_model_bills_cache_read():
    # ccusage names cached input `cacheReadTokens`; reading a nonexistent
    # `cachedInputTokens` billed cache at $0. Synthetic numbers.
    model_data = {
        'inputTokens': 1_000_000,     # non-cached
        'cacheReadTokens': 1_000_000,  # cached
        'outputTokens': 100_000,
    }
    cost = calculate_cost_for_model(model_data, 'gpt-5.3-codex')
    expected = (
        1_000_000 * 1.75 / 1_000_000
        + 1_000_000 * 0.175 / 1_000_000
        + 100_000 * 14.0 / 1_000_000
    )
    assert abs(cost - expected) < 1e-9


def test_calculate_cost_ignores_unknown_model():
    assert calculate_cost_for_model({'inputTokens': 1}, 'unknown-model') is None
