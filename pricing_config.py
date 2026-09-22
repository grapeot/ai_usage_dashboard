"""
Model pricing config: lookup official API prices by model name, independent of source.
Reference: docs/rfc.md
Updated: 2026-09 (full pricing audit 2026-09-15; evidence in workspace tmp/pricing_audit_20260915/)
"""
import re
# model name -> official price ($/M tokens)
MODEL_PRICING = {
    # GPT-5.6 promotional pricing (at least through 2026-11-21); long context >272K doubles input.
    "gpt-5.6-sol": {"input": 4.0, "cached": 0.4, "cache_write": 5.0, "output": 20.0},
    "gpt-5.6-terra": {"input": 2.0, "cached": 0.2, "cache_write": 2.5, "output": 12.0},
    "gpt-5.6-luna": {"input": 0.2, "cached": 0.02, "cache_write": 0.25, "output": 1.2},
    # GPT-6 Astra (standard); fast mode = 2x, billed per-token.
    "gpt-6-astra": {"input": 10.0, "cached": 1.0, "cache_write": 12.5, "output": 50.0},
    "gpt-5.5": {"input": 5.0, "cached": 0.5, "output": 30.0},
    "gpt-5.4": {"input": 2.5, "cached": 0.25, "output": 15.0},
    "gpt-5.4-mini": {"input": 0.75, "cached": 0.075, "output": 4.5},
    # Fast mode is a service tier, not a model; OpenAI publishes no separate
    # gpt-5.4-mini-fast price. Kept as alias of the standard rate.
    "gpt-5.4-mini-fast": {"input": 0.75, "cached": 0.075, "output": 4.5},
    "gpt-5.2": {"input": 1.75, "cached": 0.175, "output": 14.0},
    "gpt-5.2-codex": {"input": 1.75, "cached": 0.175, "output": 14.0},
    "gpt-5.3-codex": {"input": 1.75, "cached": 0.175, "output": 14.0},
    # xAI pricing (verified via /v1/language-models, 2026-09-21): the current
    # Text API table lists grok-4.6 and grok-4.7, both at $2/$0.50/$6
    # (short-context <200k); fast tier bills 2x. grok-4.5 / grok-4.3 /
    # grok-build-0.1 / grok-4.20 also remain. Keep 4.6 as its own key: historical
    # sessions carry the grok-4.6 model id and must price at the real 4.6 rate,
    # not fall through to the grok-4.3 legacy bucket.
    # grok-4 and grok-4-1-fast slugs were retired 2026-05-15 and bill at
    # grok-4.3 rates; kept here with grok-4.3 prices for cost estimation.
    "grok-4.3": {"input": 1.25, "cached": 0.125, "output": 2.5},
    # xAI docs / Cursor: base grok-4.5 = $2/$6; cached input $0.30 per models.md.
    "grok-4.5": {"input": 2.0, "cached": 0.3, "output": 6.0},
    # Cursor Fast variant for Grok 4.5 ($4/$1/$18 per Cursor docs).
    "grok-4.5-fast": {"input": 4.0, "cached": 1.0, "output": 18.0},
    # Grok 4.6: $2/$0.50/$6; still billable and used by historical sessions.
    "grok-4.6": {"input": 2.0, "cached": 0.5, "output": 6.0},
    # Cursor Fast variant for Grok 4.6 ($4/$1/$12).
    "grok-4.6-fast": {"input": 4.0, "cached": 1.0, "output": 12.0},
    # Grok 4.7 (current release 2026-09): same rates as 4.6, $2/$0.50/$6.
    "grok-4.7": {"input": 2.0, "cached": 0.5, "output": 6.0},
    # Cursor Fast variant for Grok 4.7 ($4/$1/$12).
    "grok-4.7-fast": {"input": 4.0, "cached": 1.0, "output": 12.0},
    # Retired slugs (May 15, 2026) redirected to grok-4.3 billing; keep the
    # redirected prices so legacy session ids still estimate correctly.
    "grok-4": {"input": 1.25, "cached": 0.125, "output": 2.5},
    "grok-4-1-fast": {"input": 1.25, "cached": 0.125, "output": 2.5},
    "grok-code-fast-1": {"input": 1.0, "cached": 0.2, "output": 2.0},
    "glm-5.1": {"input": 1.4, "cached": 0.26, "output": 4.4},
    # Z.ai official pricing page: GLM-5.2 matches GLM-5.1 list rates.
    "glm-5.2": {"input": 1.4, "cached": 0.26, "output": 4.4},
    # Z.ai API pricing (Aug 18, 2026): unchanged from GLM-5.2.
    "glm-5.3": {"input": 1.4, "cached": 0.26, "output": 4.4},
    # GLM-5.3-Flash 50% promo ended 2026-09-09; use list prices.
    "glm-5.3-flash": {"input": 0.15, "cached": 0.03, "output": 0.5},
    "glm-5": {"input": 1.0, "cached": 0.2, "output": 3.2},
    "glm-5-turbo": {"input": 1.2, "cached": 0.24, "output": 4.0},
    "glm-5-code": {"input": 1.2, "cached": 0.3, "output": 5.0},
    "claude-sonnet-4.6": {"input": 3.0, "cache_read": 0.3, "cache_write": 3.75, "cache_write_1h": 6.0, "output": 15.0},
    # Anthropic Sonnet 5: $2/$10 launch price is now the long-term standard price.
    "claude-sonnet-5": {"input": 2.0, "cache_read": 0.2, "cache_write": 2.5, "cache_write_1h": 4.0, "output": 10.0},
    "claude-opus-4.6": {"input": 5.0, "cache_read": 0.5, "cache_write": 6.25, "cache_write_1h": 10.0, "output": 25.0},
    # Fast mode is a request parameter (speed:"fast"), not a model id; Opus 5/4.8
    # fast rate is 2x standard: $10/$50 (docs.claude.com fast-mode page).
    "claude-opus-fast": {"input": 10.0, "cache_read": 1.0, "cache_write": 12.5, "cache_write_1h": 20.0, "output": 50.0},
    "claude-opus-5": {"input": 5.0, "cache_read": 0.5, "cache_write": 6.25, "cache_write_1h": 10.0, "output": 25.0},
    # Cursor Composer 2.5 (Cursor official blog): no published cache discount.
    # Standard $0.50/$2.50; Fast $3.00/$15.00.
    "cursor-composer-2.5": {"input": 0.5, "output": 2.5},
    "cursor-composer-2.5-fast": {"input": 3.0, "output": 15.0},
    "claude-fable-5": {"input": 10.0, "cache_read": 1.0, "cache_write": 12.5, "cache_write_1h": 20.0, "output": 50.0},
    # Fable 5.1 cache hit drops to 0.025x base input ($0.25).
    "claude-fable-5-1": {"input": 10.0, "cache_read": 0.25, "cache_write": 12.5, "cache_write_1h": 20.0, "output": 50.0},
    "claude-haiku-4.5": {"input": 1.0, "cache_read": 0.1, "cache_write": 1.25, "cache_write_1h": 2.0, "output": 5.0},
    "gemini-3-flash": {"input": 0.5, "output": 3.0},
    "gemini-3-flash-preview": {"input": 0.5, "output": 3.0},
    "antigravity-gemini-3-flash": {"input": 0.5, "output": 3.0},
    # Google Gemini API / Vertex: intro Standard rates through 2026-12-31;
    # 3.8 / 3.7 share the same intro price, 3.5 is the older non-promo tier.
    "gemini-3.6-flash": {"input": 0.75, "cached": 0.075, "output": 3.75},
    "gemini-3.8-flash": {"input": 0.75, "cached": 0.075, "output": 3.75},
    "gemini-3.7-flash": {"input": 0.75, "cached": 0.075, "output": 3.75},
    "gemini-3.5-flash": {"input": 1.5, "cached": 0.15, "output": 9.0},
    "gemini-3-pro": {"input": 2.0, "output": 12.0},
    "gemini-3.1-pro-preview": {"input": 2.0, "output": 12.0},
    # DeepSeek peak/off-peak dual-track pricing (effective 2026-08-16): cost
    # estimates use the off-peak (conservative-low) column. deepseek-v4-pro
    # requests have been routed to V4.1 Flash at flash rates since 2026-09-14.
    "deepseek-v4-flash": {"input": 0.22, "cached": 0.007, "output": 0.66},
    "deepseek-v4-pro": {"input": 0.22, "cached": 0.007, "output": 0.66},
    "deepseek-chat": {"input": 0.22, "cached": 0.007, "output": 0.66},
    "deepseek-reasoner": {"input": 0.22, "cached": 0.007, "output": 0.66},
    "grok-build-0.1": {"input": 1.0, "cached": 0.2, "output": 2.0},
    "kimi-k2.6": {"input": 0.95, "cached": 0.16, "output": 4.0},
    "minimax-m3": {"input": 0.3, "cached": 0.06, "output": 1.2},
    # Alibaba Model Studio international (Singapore) rates.
    "qwen3.5-397b-a17b": {"input": 0.6, "output": 3.6},
    "qwen3.8-27b": {"input": 0.5, "output": 3.0},
    "local-free": {"input": 0.0, "cached": 0.0, "output": 0.0},
}

# modelID variants -> canonical model names
MODEL_ALIASES = {
    "antigravity-gemini-3-flash": "gemini-3-flash",
    "deepseek-chat": "deepseek-v4-flash",
    "deepseek-reasoner": "deepseek-v4-flash",
    # Retired xAI slugs (May 15, 2026) now bill at grok-4.3 rates.
    "grok-4-1-fast-reasoning": "grok-4.3",
    "grok-4-1-fast-non-reasoning": "grok-4.3",
    "grok-4.20-experimental-beta-0304-non-reasoning": "grok-4.3",
    "xai/grok-4.5": "grok-4.5",
    "x-ai/grok-4.5": "grok-4.5",
    "grok-4.5-fast-reasoning": "grok-4.5-fast",
    "grok-4.5-fast-non-reasoning": "grok-4.5-fast",
    "xai/grok-4.6": "grok-4.6",
    "x-ai/grok-4.6": "grok-4.6",
    "cursor-grok-4.6-high": "grok-4.6",
    "cursor-grok-4.6": "grok-4.6",
    "grok-4.6-fast-reasoning": "grok-4.6-fast",
    "grok-4.6-fast-non-reasoning": "grok-4.6-fast",
    "xai/grok-4.7": "grok-4.7",
    "x-ai/grok-4.7": "grok-4.7",
    "cursor-grok-4.7-high": "grok-4.7",
    "cursor-grok-4.7": "grok-4.7",
    "grok-4.7-fast-reasoning": "grok-4.7-fast",
    "grok-4.7-fast-non-reasoning": "grok-4.7-fast",
    "antigravity-gemini-3-pro": "gemini-3.1-pro-preview",
    "qwen3.5:397b": "qwen3.5-397b-a17b",
    "qwen3.5:397b-cloud": "qwen3.5-397b-a17b",
    "qwen3.5-397b": "qwen3.5-397b-a17b",
    # DSH routes Z.ai GLM 5.3 through its own client; same published rates.
    "zai/glm-5.3": "glm-5.3",
    "zai/glm-5.2": "glm-5.2",
}

Pricing = dict[str, float]


def get_pricing(model_id: str) -> Pricing | None:
    """Look up pricing by model_id with alias support. Returns {input, cached?, output} or None."""
    model_lower = (model_id or "").lower().strip()
    if not model_lower:
        return None
    # Strip common provider prefixes from OpenRouter / OpenCode-style ids.
    if "/" in model_lower:
        prefix, remainder = model_lower.split("/", 1)
        if prefix in {"xai", "x-ai", "openrouter", "opencode"} and remainder:
            model_lower = remainder
    # direct match
    if model_lower in MODEL_PRICING:
        return MODEL_PRICING[model_lower].copy()
    # alias mapping
    if model_lower in MODEL_ALIASES:
        canonical = MODEL_ALIASES[model_lower]
        return MODEL_PRICING.get(canonical, {}).copy()
    if (model_id or "").lower().strip() in MODEL_ALIASES:
        canonical = MODEL_ALIASES[(model_id or "").lower().strip()]
        return MODEL_PRICING.get(canonical, {}).copy()
    # OpenAI Fast mode (service_tier=priority/fast) is billed at 2x standard
    # rates; usage records surface it as a "-fast" suffix on the model id.
    # -pro / -low / -medium / -high suffixes are reasoning parameters, not
    # separate billing models, so they price at the base model rate.
    if model_lower.startswith("gpt-"):
        base = re.sub(r"-(fast|pro|low|medium|high)$", "", model_lower)
        if base != model_lower:
            base_pricing = MODEL_PRICING.get(base)
            if base_pricing:
                if model_lower.endswith("-fast"):
                    return {k: v * 2.0 for k, v in base_pricing.items()}
                return base_pricing.copy()
    # partial match: antigravity-gemini-* -> gemini-*
    if "antigravity-" in model_lower:
        base = model_lower.replace("antigravity-", "")
        if base in MODEL_PRICING:
            return MODEL_PRICING[base].copy()
        if "gemini-3-flash" in model_lower:
            return MODEL_PRICING["gemini-3-flash"].copy()
        if "gemini-3-pro" in model_lower:
            return MODEL_PRICING["gemini-3-pro"].copy()
    # Claude variants
    if "opus" in model_lower and "claude" in model_lower:
        # Opus 5 / 4.8 / 4.6 share the $5/$25 base price; fast mode (request
        # parameter) bills at 2x standard per Anthropic's fast-mode page.
        if "fast" in model_lower:
            return MODEL_PRICING["claude-opus-fast"].copy()
        return MODEL_PRICING["claude-opus-5"].copy()
    if "fable" in model_lower and "claude" in model_lower:
        if "5-1" in model_lower or "5.1" in model_lower:
            return MODEL_PRICING["claude-fable-5-1"].copy()
        return MODEL_PRICING["claude-fable-5"].copy()
    if "sonnet" in model_lower and "claude" in model_lower:
        if "sonnet-5" in model_lower:
            return MODEL_PRICING["claude-sonnet-5"].copy()
        return MODEL_PRICING["claude-sonnet-4.6"].copy()
    if "haiku" in model_lower and "claude" in model_lower:
        return MODEL_PRICING["claude-haiku-4.5"].copy()
    if model_lower.startswith("grok-4.20"):
        return MODEL_PRICING["grok-4.3"].copy()
    if model_lower.startswith("grok-code-fast-1"):
        return MODEL_PRICING["grok-code-fast-1"].copy()
    if model_lower.startswith("grok-build-0.1"):
        return MODEL_PRICING["grok-build-0.1"].copy()
    if model_lower.startswith("grok-4-1-fast"):
        return MODEL_PRICING["grok-4.3"].copy()
    if "grok-4.7" in model_lower and "fast" in model_lower:
        return MODEL_PRICING["grok-4.7-fast"].copy()
    if "grok-4.7" in model_lower:
        return MODEL_PRICING["grok-4.7"].copy()
    if "grok-4.6" in model_lower and "fast" in model_lower:
        return MODEL_PRICING["grok-4.6-fast"].copy()
    if "grok-4.6" in model_lower:
        return MODEL_PRICING["grok-4.6"].copy()
    if "grok-4.5" in model_lower and "fast" in model_lower:
        return MODEL_PRICING["grok-4.5-fast"].copy()
    if model_lower.startswith("grok-4.5") or model_lower == "grok-4.5":
        return MODEL_PRICING["grok-4.5"].copy()
    if model_lower.startswith("grok-4.3"):
        return MODEL_PRICING["grok-4.3"].copy()
    if model_lower.startswith("grok-4"):
        return MODEL_PRICING["grok-4.3"].copy()
    if model_lower.startswith("glm-5.3-flash"):
        return MODEL_PRICING["glm-5.3-flash"].copy()
    if model_lower.startswith("glm-5.2") or model_lower == "glm-5.2":
        return MODEL_PRICING["glm-5.2"].copy()
    if "gemini-3.8-flash" in model_lower:
        return MODEL_PRICING["gemini-3.8-flash"].copy()
    if "gemini-3.7-flash" in model_lower:
        return MODEL_PRICING["gemini-3.7-flash"].copy()
    if "gemini-3.5-flash" in model_lower:
        return MODEL_PRICING["gemini-3.5-flash"].copy()
    if "gemini-3.6-flash" in model_lower:
        return MODEL_PRICING["gemini-3.6-flash"].copy()
    if model_lower.startswith("lmstudio/") or "-mlx" in model_lower:
        return MODEL_PRICING["local-free"].copy()
    if model_lower.startswith("deepseek-v4-flash"):
        return MODEL_PRICING["deepseek-v4-flash"].copy()
    if model_lower.startswith("deepseek-v4-pro"):
        return MODEL_PRICING["deepseek-v4-pro"].copy()
    if model_lower.startswith("deepseek-chat"):
        return MODEL_PRICING["deepseek-v4-flash"].copy()
    if model_lower.startswith("deepseek-reasoner"):
        return MODEL_PRICING["deepseek-v4-flash"].copy()
    if model_lower.startswith("deepseek-"):
        return MODEL_PRICING["deepseek-v4-flash"].copy()
    if model_lower.startswith("kimi-k2.6"):
        return MODEL_PRICING["kimi-k2.6"].copy()
    if model_lower.startswith("minimax-m3"):
        return MODEL_PRICING["minimax-m3"].copy()
    if model_lower.startswith("qwen3.5:397b") or model_lower.startswith("qwen3.5-397b"):
        return MODEL_PRICING["qwen3.5-397b-a17b"].copy()
    if model_lower.startswith("qwen3.8-27b") or model_lower.startswith("qwen-3.8-27b"):
        return MODEL_PRICING["qwen3.8-27b"].copy()
    # Cursor Composer 2.5 variants (hosted-only; no public cache discount).
    if "composer-2.5" in model_lower:
        if "fast" in model_lower:
            return MODEL_PRICING["cursor-composer-2.5-fast"].copy()
        return MODEL_PRICING["cursor-composer-2.5"].copy()
    return None


def calc_cost(
    pricing: Pricing | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
    cache_write_tokens: int = 0,
    cache_write_1h_tokens: int = 0,
) -> float:
    """Calculate cost in USD from pricing."""
    if not pricing:
        return 0.0
    inp = pricing.get("input", 0) or 0
    out = pricing.get("output", 0) or 0
    cached_rate = pricing.get("cache_read")
    if cached_rate is None:
        cached_rate = pricing.get("cached")
    if cached_rate is None:
        cached_rate = inp * 0.1
    cache_write_rate = pricing.get("cache_write")
    if cache_write_rate is None:
        cache_write_rate = inp * 1.25
    cache_write_1h_rate = pricing.get("cache_write_1h")
    if cache_write_1h_rate is None:
        cache_write_1h_rate = inp * 2.0
    non_cached = max(0, input_tokens - cached_tokens)
    cost = (
        non_cached * inp / 1_000_000
        + cached_tokens * cached_rate / 1_000_000
        + cache_write_tokens * cache_write_rate / 1_000_000
        + cache_write_1h_tokens * cache_write_1h_rate / 1_000_000
        + output_tokens * out / 1_000_000
    )
    return cost
