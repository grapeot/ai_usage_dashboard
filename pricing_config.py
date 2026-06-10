"""
Model pricing config: lookup official API prices by model name, independent of source.
Reference: docs/rfc.md
Updated: 2026-05
"""
# model name -> official price ($/M tokens)
MODEL_PRICING = {
    "gpt-5.5": {"input": 5.0, "cached": 0.5, "output": 30.0},
    "gpt-5.4": {"input": 2.5, "cached": 0.25, "output": 15.0},
    "gpt-5.4-mini": {"input": 0.75, "cached": 0.075, "output": 4.5},
    "gpt-5.4-mini-fast": {"input": 0.75, "cached": 0.075, "output": 4.5},
    "gpt-5.2": {"input": 1.75, "cached": 0.175, "output": 14.0},
    "gpt-5.2-codex": {"input": 1.75, "cached": 0.175, "output": 14.0},
    "gpt-5.3-codex": {"input": 1.75, "cached": 0.175, "output": 14.0},
    "grok-4": {"input": 3.0, "cached": 0.75, "output": 15.0},
    "grok-4-1-fast": {"input": 0.2, "cached": 0.05, "output": 0.5},
    "grok-code-fast-1": {"input": 0.2, "cached": 0.02, "output": 1.5},
    "glm-5.1": {"input": 1.4, "cached": 0.26, "output": 4.4},
    "glm-5": {"input": 1.0, "cached": 0.2, "output": 3.2},
    "glm-5-turbo": {"input": 1.2, "cached": 0.24, "output": 4.0},
    "glm-5-code": {"input": 1.2, "cached": 0.3, "output": 5.0},
    "claude-sonnet-4.6": {"input": 3.0, "cache_read": 0.3, "cache_write": 3.75, "cache_write_1h": 6.0, "output": 15.0},
    "claude-opus-4.6": {"input": 5.0, "cache_read": 0.5, "cache_write": 6.25, "cache_write_1h": 10.0, "output": 25.0},
    "claude-opus-4-6-fast": {"input": 30.0, "cache_read": 3.0, "cache_write": 37.5, "cache_write_1h": 60.0, "output": 150.0},
    "claude-fable-5": {"input": 10.0, "cache_read": 1.0, "cache_write": 12.5, "cache_write_1h": 20.0, "output": 50.0},
    "claude-haiku-4.5": {"input": 1.0, "cache_read": 0.1, "cache_write": 1.25, "cache_write_1h": 2.0, "output": 5.0},
    "gemini-3-flash": {"input": 0.5, "output": 3.0},
    "gemini-3-flash-preview": {"input": 0.5, "output": 3.0},
    "antigravity-gemini-3-flash": {"input": 0.5, "output": 3.0},
    "gemini-3-pro": {"input": 2.0, "output": 12.0},
    "gemini-3.1-pro-preview": {"input": 2.0, "output": 12.0},
    "deepseek-v4-flash": {"input": 0.14, "cached": 0.0028, "output": 0.28},
    "deepseek-v4-pro": {"input": 0.435, "cached": 0.003625, "output": 0.87},
    "deepseek-chat": {"input": 0.14, "cached": 0.0028, "output": 0.28},
    "deepseek-reasoner": {"input": 0.14, "cached": 0.0028, "output": 0.28},
    "grok-build-0.1": {"input": 1.0, "cached": 0.2, "output": 2.0},
    "kimi-k2.6": {"input": 0.95, "cached": 0.16, "output": 4.0},
    "minimax-m3": {"input": 0.3, "cached": 0.06, "output": 1.2},
    "qwen3.5-397b-a17b": {"input": 0.39, "output": 2.34},
}

# modelID variants -> canonical model names
MODEL_ALIASES = {
    "antigravity-gemini-3-flash": "gemini-3-flash",
    "deepseek-chat": "deepseek-v4-flash",
    "deepseek-reasoner": "deepseek-v4-flash",
    "grok-4-1-fast-reasoning": "grok-4-1-fast",
    "grok-4-1-fast-non-reasoning": "grok-4-1-fast",
    "grok-4.20-experimental-beta-0304-non-reasoning": "grok-4-1-fast",
    "antigravity-gemini-3-pro": "gemini-3.1-pro-preview",
    "qwen3.5:397b": "qwen3.5-397b-a17b",
    "qwen3.5:397b-cloud": "qwen3.5-397b-a17b",
    "qwen3.5-397b": "qwen3.5-397b-a17b",
}

Pricing = dict[str, float]


def get_pricing(model_id: str) -> Pricing | None:
    """Look up pricing by model_id with alias support. Returns {input, cached?, output} or None."""
    model_lower = (model_id or "").lower().strip()
    if not model_lower:
        return None
    # direct match
    if model_lower in MODEL_PRICING:
        return MODEL_PRICING[model_lower].copy()
    # alias mapping
    if model_lower in MODEL_ALIASES:
        canonical = MODEL_ALIASES[model_lower]
        return MODEL_PRICING.get(canonical, {}).copy()
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
        if "fast" in model_lower:
            return MODEL_PRICING["claude-opus-4-6-fast"].copy()
        return MODEL_PRICING["claude-opus-4.6"].copy()
    if "fable" in model_lower and "claude" in model_lower:
        return MODEL_PRICING["claude-fable-5"].copy()
    if "sonnet" in model_lower and "claude" in model_lower:
        return MODEL_PRICING["claude-sonnet-4.6"].copy()
    if "haiku" in model_lower and "claude" in model_lower:
        return MODEL_PRICING["claude-haiku-4.5"].copy()
    if model_lower.startswith("grok-4.20"):
        return MODEL_PRICING["grok-4-1-fast"].copy()
    if model_lower.startswith("grok-code-fast-1"):
        return MODEL_PRICING["grok-code-fast-1"].copy()
    if model_lower.startswith("grok-build-0.1"):
        return MODEL_PRICING["grok-build-0.1"].copy()
    if model_lower.startswith("grok-4-1-fast"):
        return MODEL_PRICING["grok-4-1-fast"].copy()
    if model_lower.startswith("grok-4"):
        return MODEL_PRICING["grok-4"].copy()
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
