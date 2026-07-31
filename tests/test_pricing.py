"""Tests for pricing_config."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pricing_config import get_pricing, calc_cost


class TestGetPricing:
    def test_direct_match(self):
        assert get_pricing("gpt-5.6-sol") == {"input": 5.0, "cached": 0.5, "cache_write": 6.25, "output": 30.0}
        assert get_pricing("gpt-5.6-terra") == {"input": 2.5, "cached": 0.25, "cache_write": 3.125, "output": 15.0}
        assert get_pricing("gpt-5.6-luna") == {"input": 1.0, "cached": 0.1, "cache_write": 1.25, "output": 6.0}
        assert get_pricing("gpt-5.5") == {"input": 5.0, "cached": 0.5, "output": 30.0}
        assert get_pricing("gpt-5.4") == {"input": 2.5, "cached": 0.25, "output": 15.0}
        assert get_pricing("gpt-5.4-mini") == {"input": 0.75, "cached": 0.075, "output": 4.5}
        assert get_pricing("gpt-5.4-mini-fast") == {"input": 0.75, "cached": 0.075, "output": 4.5}
        assert get_pricing("gpt-5.2") == {"input": 1.75, "cached": 0.175, "output": 14.0}
        assert get_pricing("gpt-5.3-codex") == {"input": 1.75, "cached": 0.175, "output": 14.0}
        assert get_pricing("grok-4") == {"input": 3.0, "cached": 0.75, "output": 15.0}
        assert get_pricing("grok-4.5") == {"input": 2.0, "cached": 0.5, "output": 6.0}
        assert get_pricing("xai/grok-4.5") == {"input": 2.0, "cached": 0.5, "output": 6.0}
        assert get_pricing("x-ai/grok-4.5") == {"input": 2.0, "cached": 0.5, "output": 6.0}
        assert get_pricing("grok-4.5-fast") == {"input": 4.0, "cached": 1.0, "output": 18.0}
        assert get_pricing("grok-4-1-fast") == {"input": 0.2, "cached": 0.05, "output": 0.5}
        assert get_pricing("grok-code-fast-1") == {"input": 0.2, "cached": 0.02, "output": 1.5}
        assert get_pricing("glm-5.1") == {"input": 1.4, "cached": 0.26, "output": 4.4}
        assert get_pricing("glm-5") == {"input": 1.0, "cached": 0.2, "output": 3.2}
        assert get_pricing("glm-5-turbo") == {"input": 1.2, "cached": 0.24, "output": 4.0}
        assert get_pricing("gemini-3-flash") == {"input": 0.5, "output": 3.0}
        assert get_pricing("gemini-3-flash-preview") == {"input": 0.5, "output": 3.0}
        assert get_pricing("gemini-3.1-pro-preview") == {"input": 2.0, "output": 12.0}
        assert get_pricing("deepseek-v4-flash") == {"input": 0.14, "cached": 0.0028, "output": 0.28}
        assert get_pricing("deepseek-v4-pro") == {"input": 0.435, "cached": 0.003625, "output": 0.87}
        assert get_pricing("claude-fable-5") == {"input": 10.0, "cache_read": 1.0, "cache_write": 12.5, "cache_write_1h": 20.0, "output": 50.0}
        assert get_pricing("grok-build-0.1") == {"input": 1.0, "cached": 0.2, "output": 2.0}
        assert get_pricing("kimi-k2.6") == {"input": 0.95, "cached": 0.16, "output": 4.0}
        assert get_pricing("minimax-m3") == {"input": 0.3, "cached": 0.06, "output": 1.2}
        assert get_pricing("qwen3.5-397b-a17b") == {"input": 0.39, "output": 2.34}

    def test_alias_antigravity(self):
        p = get_pricing("antigravity-gemini-3-flash")
        assert p == {"input": 0.5, "output": 3.0}
        assert get_pricing("antigravity-gemini-3-pro") == {"input": 2.0, "output": 12.0}

    def test_alias_deepseek_legacy_ids(self):
        assert get_pricing("deepseek-chat") == {"input": 0.14, "cached": 0.0028, "output": 0.28}
        assert get_pricing("deepseek-reasoner") == {"input": 0.14, "cached": 0.0028, "output": 0.28}

    def test_deepseek_prefix_match(self):
        assert get_pricing("deepseek-v4-flash-thinking") == {"input": 0.14, "cached": 0.0028, "output": 0.28}

    def test_case_insensitive(self):
        assert get_pricing("GPT-5.3-CODEX") == get_pricing("gpt-5.3-codex")
        assert get_pricing("GROK-4") == get_pricing("grok-4")

    def test_claude_variants(self):
        assert get_pricing("claude-opus-4.6") == {"input": 5.0, "cache_read": 0.5, "cache_write": 6.25, "cache_write_1h": 10.0, "output": 25.0}
        assert get_pricing("claude-sonnet-4.6") == {"input": 3.0, "cache_read": 0.3, "cache_write": 3.75, "cache_write_1h": 6.0, "output": 15.0}

    def test_claude_opus_5(self):
        # Opus 5 same base price as Opus 4.8/4.6; standard Anthropic cache rates.
        assert get_pricing("claude-opus-5") == {"input": 5.0, "cache_read": 0.5, "cache_write": 6.25, "cache_write_1h": 10.0, "output": 25.0}
        assert get_pricing("claude-opus-5-thinking-high") == get_pricing("claude-opus-5")
        # Generic opus fallback still resolves to 4.6 when no version digit present.
        assert get_pricing("claude-opus-unknown") == get_pricing("claude-opus-4.6")

    def test_cursor_composer(self):
        # Standard $0.50/$2.50; Fast $3.00/$15.00 (Cursor official blog).
        assert get_pricing("cursor-composer-2.5") == {"input": 0.5, "output": 2.5}
        assert get_pricing("cursor-composer-2.5-fast") == {"input": 3.0, "output": 15.0}
        assert get_pricing("composer-2.5-fast") == get_pricing("cursor-composer-2.5-fast")
        assert get_pricing("composer-2.5") == get_pricing("cursor-composer-2.5")

    def test_claude_fast_variant(self):
        assert get_pricing("claude-opus-4-6-fast") == {"input": 30.0, "cache_read": 3.0, "cache_write": 37.5, "cache_write_1h": 60.0, "output": 150.0}

    def test_grok_aliases(self):
        assert get_pricing("grok-4-1-fast-non-reasoning") == {"input": 0.2, "cached": 0.05, "output": 0.5}
        assert get_pricing("grok-4-1-fast-reasoning") == {"input": 0.2, "cached": 0.05, "output": 0.5}
        assert get_pricing("grok-4.20-experimental-beta-0304-non-reasoning") == {"input": 0.2, "cached": 0.05, "output": 0.5}

    def test_new_model_aliases(self):
        assert get_pricing("qwen3.5:397b") == {"input": 0.39, "output": 2.34}
        assert get_pricing("qwen3.5:397b-cloud") == {"input": 0.39, "output": 2.34}
        assert get_pricing("minimax-m3:cloud") == get_pricing("minimax-m3")
        assert get_pricing("kimi-k2.6:cloud") == get_pricing("kimi-k2.6")

    def test_unknown_returns_none(self):
        assert get_pricing("unknown-model-xyz") is None
        assert get_pricing("") is None


class TestCalcCost:
    def test_basic(self):
        p = {"input": 1.0, "output": 2.0}
        # 1M input + 1M output = 1 + 2 = $3
        assert abs(calc_cost(p, 1_000_000, 1_000_000) - 3.0) < 0.01

    def test_with_cached(self):
        p = {"input": 1.0, "cached": 0.1, "output": 2.0}
        # 500k non-cached input + 500k cached + 100k output
        cost = calc_cost(p, 1_000_000, 100_000, cached_tokens=500_000)
        expected = 0.5 * 1.0 + 0.5 * 0.1 + 0.1 * 2.0  # 0.5 + 0.05 + 0.2 = 0.75
        assert abs(cost - expected) < 0.01

    def test_codex_example(self):
        p = get_pricing("gpt-5.3-codex")
        # 1M input, 100k cached, 50k output
        cost = calc_cost(p, 1_000_000, 50_000, cached_tokens=100_000)
        expected = 0.9 * 1.75 + 0.1 * 0.175 + 0.05 * 14.0  # 1.575 + 0.0175 + 0.7 = 2.29
        assert abs(cost - expected) < 0.01

    def test_zero_pricing_returns_zero(self):
        assert calc_cost(None, 1_000_000, 1_000_000) == 0.0

    def test_with_cache_write_and_cache_read(self):
        p = {"input": 3.0, "cache_read": 0.3, "cache_write": 3.75, "cache_write_1h": 6.0, "output": 15.0}
        cost = calc_cost(
            p,
            input_tokens=300_000,
            output_tokens=100_000,
            cached_tokens=200_000,
            cache_write_tokens=50_000,
            cache_write_1h_tokens=25_000,
        )
        expected = (0.1 * 3.0) + (0.2 * 0.3) + (0.05 * 3.75) + (0.025 * 6.0) + (0.1 * 15.0)
        assert abs(cost - expected) < 0.01

    def test_gpt_5_6_cache_write_rate(self):
        p = get_pricing("gpt-5.6-sol")
        cost = calc_cost(p, input_tokens=0, cache_write_tokens=1_000_000)
        assert cost == 6.25
