"""Pydantic response models for the AI Usage Dashboard local API.

These models are the source of truth for the FastAPI ``/openapi.json`` schema.
Every field carries a ``description`` so an AI agent reading the OpenAPI spec
can understand the payload without inspecting the source.

The models mirror the dict shape produced by ``auto_usage.build_eink_dashboard_payload``.
All fields are optional with defaults so a minimal cached payload (for example a
stale ``token_usage_eink.json`` written by an older version) still validates and
serializes without dropping fields. The service serializes with
``response_model_exclude_defaults=True`` so a minimal payload round-trips back to
its minimal shape.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class DashboardMeta(BaseModel):
    """Metadata describing the dashboard generation run."""

    version: int = Field(default=1, description='Payload schema version. Incremented on breaking shape changes.')
    generated_at: Optional[str] = Field(default=None, description='ISO timestamp (local, seconds precision) marking when the payload was generated.')
    start_date: Optional[str] = Field(default=None, description='Inclusive start date of the daily window, YYYY-MM-DD.')
    end_date: Optional[str] = Field(default=None, description='Inclusive end date of the daily window, YYYY-MM-DD.')
    days: Optional[int] = Field(default=None, description='Number of days covered by the daily array, inclusive of both endpoints.')
    currency: Optional[str] = Field(default=None, description='Currency code for the cost fields, e.g. USD.')


class CategoryTotals(BaseModel):
    """Per-provider token totals summed across the whole date window."""

    cursor: int = Field(default=0, description='Total Cursor tokens for the window.')
    glm: int = Field(default=0, description='Total GLM / Z.ai tokens for the window (Z.ai usage API plus OpenCode-routed GLM).')
    gemini: int = Field(default=0, description='Total Google Gemini tokens for the window.')
    claude: int = Field(default=0, description='Total Anthropic Claude tokens for the window (Claude Code plus OpenCode-routed Anthropic).')
    gpt_opencode: int = Field(default=0, description='Total OpenAI GPT tokens for the window, including Codex.')
    deepseek: int = Field(default=0, description='Total DeepSeek tokens for the window.')
    other: int = Field(default=0, description='Total tokens from providers not classified into the buckets above.')


class DashboardSummary(BaseModel):
    """Aggregate totals across the full date window."""

    total_tokens: int = Field(default=0, description='Sum of all provider token totals for the window.')
    total_ai_hours: float = Field(default=0.0, description='Cumulative estimated AI active time in hours across the window. Parallel agents are counted cumulatively, so this is total labor, not wall-clock coverage.')
    categories: CategoryTotals = Field(default_factory=CategoryTotals, description='Per-provider token totals for the window.')
    total_cost_usd: Optional[float] = Field(default=None, description='Estimated API-equivalent USD cost for the window. Present only when cost estimation is enabled.')


class DailyEntry(BaseModel):
    """One row of per-day token, active-time, and cost data."""

    date: Optional[str] = Field(default=None, description='Calendar date for this row, YYYY-MM-DD.')
    categories: CategoryTotals = Field(default_factory=CategoryTotals, description='Per-provider token totals for this day.')
    total_tokens: int = Field(default=0, description='Sum of all provider tokens for this day.')
    ai_hours: float = Field(default=0.0, description='Estimated AI active time for this day in hours.')
    cost_usd: Optional[float] = Field(default=None, description='Estimated API-equivalent USD cost for this day. Present only when cost estimation is enabled.')


class GlmQuotaUsageDetail(BaseModel):
    """Per-model usage breakdown for the monthly tool quota."""

    modelCode: Optional[str] = Field(default=None, description='Z.ai model/tool code, e.g. search-prime, web-reader, zread.')
    usage: Optional[int] = Field(default=None, description='Count consumed by this model/tool in the current window.')


class GlmQuotaSnapshot(BaseModel):
    """One rolling quota window for the Z.ai coding plan."""

    label: str = Field(description='Human-readable window label, e.g. "5 Hours Quota", "Weekly Quota", "Monthly Web Search / Reader / Zread Quota". Falls back to "<type> unit=<unit>" for unknown window codes.')
    type: str = Field(description='Raw Z.ai limit type, e.g. TOKENS_LIMIT or TIME_LIMIT.')
    unit: int = Field(description='Raw Z.ai unit code identifying the window within its type.')
    percentage: int = Field(default=0, description='Percentage of the window already used (0-100).')
    next_reset_time_ms: Optional[int] = Field(default=None, description='Epoch-millisecond timestamp at which the window resets. May be absent.')
    next_reset_iso: Optional[str] = Field(default=None, description='Local ISO timestamp (seconds precision) at which the window resets. Derived from next_reset_time_ms.')
    usage: Optional[int] = Field(default=None, description='Absolute count already used. Present only for the monthly tool quota (TIME_LIMIT).')
    current_value: Optional[int] = Field(default=None, description='Current consumed count. Present only for the monthly tool quota (TIME_LIMIT).')
    remaining: Optional[int] = Field(default=None, description='Remaining count before the window resets. Present only for the monthly tool quota (TIME_LIMIT).')
    usage_details: Optional[list[GlmQuotaUsageDetail]] = Field(default=None, description='Per-model usage breakdown. Present only for the monthly tool quota (TIME_LIMIT).')


class QuotaSnapshot(BaseModel):
    """One rolling quota window for any provider (unified across GLM, Codex, Claude Code)."""

    provider: str = Field(description='Provider name, e.g. glm, codex, claude.')
    label: str = Field(description='Human-readable window label, e.g. "5 Hours", "Weekly", "5 Hours Quota".')
    percentage: int = Field(default=0, description='Percentage of the window already used (0-100).')
    next_reset_time_ms: Optional[int] = Field(default=None, description='Epoch-millisecond timestamp at which the window resets. May be absent.')
    next_reset_iso: Optional[str] = Field(default=None, description='Local ISO timestamp (seconds precision) at which the window resets. Derived from next_reset_time_ms.')
    usage: Optional[int] = Field(default=None, description='Absolute count already used. Present only for the GLM monthly tool quota.')
    remaining: Optional[int] = Field(default=None, description='Remaining count before the window resets. Present only for the GLM monthly tool quota.')


class AutomationQuotaSnapshot(BaseModel):
    """Automation-oriented view of one provider quota window."""

    provider: str = Field(description='Provider name, e.g. glm, codex, ollama, claude, or antigravity.')
    label: str = Field(description='Human-readable quota window label, e.g. "5h", "7d", or "Gemini 5h".')
    used_percentage: int = Field(description='Percentage of the quota window already used (0-100).')
    remaining_percentage: int = Field(description='Percentage of the quota window remaining (0-100), calculated as 100 minus used_percentage.')
    next_reset_time_ms: Optional[int] = Field(default=None, description='Epoch-millisecond timestamp at which the quota window resets. May be absent.')
    next_reset_iso: Optional[str] = Field(default=None, description='Local ISO timestamp at which the quota window resets. May be absent.')
    usage: Optional[int] = Field(default=None, description='Absolute count already used when the upstream provider exposes it.')
    remaining: Optional[int] = Field(default=None, description='Absolute count remaining when the upstream provider exposes it.')


class QuotasResponse(BaseModel):
    """Compact quota response intended for scripts and agents."""

    generated_at: Optional[str] = Field(default=None, description='Generation time of the cached dashboard snapshot in local ISO format.')
    quotas: list[AutomationQuotaSnapshot] = Field(default_factory=list, description='Current quota windows across all available providers.')


class DashboardPayload(BaseModel):
    """Full dashboard payload served by the local API and written to token_usage_eink.json."""

    meta: DashboardMeta = Field(default_factory=DashboardMeta, description='Metadata describing the generation run.')
    summary: DashboardSummary = Field(default_factory=DashboardSummary, description='Aggregate totals across the date window.')
    daily: list[DailyEntry] = Field(default_factory=list, description='One entry per day in the date window, ordered by date.')
    glm_quota: Optional[list[GlmQuotaSnapshot]] = Field(default=None, description='Z.ai coding-plan quota snapshots (5-hour, weekly, monthly windows). Present only when GLM_BEARER_TOKEN is set and the quota fetch succeeds. Deprecated alias of quotas; prefer quotas for new consumers.')
    quotas: Optional[list[QuotaSnapshot]] = Field(default=None, description='Unified quota snapshots across providers (GLM 5h/weekly/monthly, Codex 5h/weekly). Present when any provider quota is available. The e-ink firmware and stdout render this single array.')


class HealthResponse(BaseModel):
    """Service liveness probe."""

    status: str = Field(description='Service status, e.g. "ok".')
    service: str = Field(description='Service name, e.g. "ai_usage_dashboard".')
    generated_at: Optional[str] = Field(default=None, description='ISO timestamp (local, seconds precision) of the health check.')


class UpdateRequest(BaseModel):
    """Request body for forcing a dashboard refresh."""

    reason: str = Field(description='Why the refresh is requested, e.g. force_button.')
    view: str = Field(description='Requested view window, e.g. "7d" or "30d".')
    device_id: str = Field(description='Identifier of the device requesting the refresh.')


class AntigravityIngestRequest(BaseModel):
    """Request body for pushing Antigravity entries from a satellite machine.

    Each entry follows the cache-entry shape used by
    ``antigravity_usage_cache.json``: model, timestamp (epoch-ms), token
    counts, and response_id for deduplication.
    """

    entries: list[dict] = Field(description='Antigravity usage entries from the satellite machine. Each entry should have: model, timestamp (epoch-ms int or ISO string), input, output, cache_read, cache_write, thinking, response_id, session_id.')
    source: Optional[str] = Field(default=None, description='Optional label identifying the satellite machine, e.g. "macbook-air". Used for logging only.')


class AntigravityIngestResponse(BaseModel):
    """Response body for the Antigravity ingest endpoint."""

    received: int = Field(description='Number of entries received in the request body.')
    new: int = Field(description='Number of entries that were new (not already in cache).')
    duplicate: int = Field(description='Number of entries that were already in cache (deduplicated by response_id).')
    total_cache: int = Field(description='Total number of entries in the cache after this ingest.')
