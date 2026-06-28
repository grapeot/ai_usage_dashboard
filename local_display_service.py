from datetime import datetime
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from auto_usage import build_latest_dashboard_payload
from dashboard_models import (
    DashboardPayload,
    HealthResponse,
    UpdateRequest,
)

app = FastAPI(
    title="ai_usage_dashboard",
    description="Local API for the AI Usage Dashboard. Aggregates token usage, AI active time, USD cost, and the GLM/Z.ai coding-plan quota snapshot from local logs and the Z.ai usage API.",
    version="0.1.0",
)

_cached_payload: dict[str, Any] | None = None
_payload_path = Path(__file__).resolve().parent / "token_usage_eink.json"


def _read_payload_from_disk() -> dict[str, Any] | None:
    if not _payload_path.exists():
        return None
    with open(_payload_path) as f:
        data = json.load(f)
    if isinstance(data, dict) and {"meta", "summary", "daily"}.issubset(data.keys()):
        return data
    return None


def read_cached_payload() -> dict[str, Any]:
    global _cached_payload
    if _cached_payload is None:
        try:
            _cached_payload = generate_latest_payload()
        except Exception:
            disk_payload = _read_payload_from_disk()
            if disk_payload is None:
                raise
            _cached_payload = disk_payload
    return _cached_payload


def generate_latest_payload() -> dict[str, Any]:
    return build_latest_dashboard_payload(days=30, no_cost=False, skip_desktop_chart=True)


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Service liveness probe",
)
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "ai_usage_dashboard",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


@app.get(
    "/token_usage.json",
    response_model=DashboardPayload,
    summary="Return the cached dashboard payload",
    description="Returns the dashboard payload: metadata, per-provider token totals, daily token/active-time/cost rows, and the GLM/Z.ai coding-plan quota snapshots when available. The payload is cached after the first successful generation and refreshed by POST /api/v1/display/update.",
)
def token_usage_json() -> dict[str, Any]:
    return read_cached_payload()


@app.post(
    "/api/v1/display/update",
    response_model=DashboardPayload,
    summary="Force a dashboard refresh and return the fresh payload",
    description="Triggers a full recompute of the dashboard payload (exporting from local logs and the Z.ai API when configured) and returns the fresh payload. Falls back to the cached payload or the on-disk token_usage_eink.json if the refresh fails.",
)
def display_update(request: UpdateRequest) -> dict[str, Any]:
    global _cached_payload
    try:
        _cached_payload = generate_latest_payload()
    except Exception:
        if _cached_payload is not None:
            return _cached_payload
        disk_payload = _read_payload_from_disk()
        if disk_payload is not None:
            _cached_payload = disk_payload
            return _cached_payload
        raise
    return _cached_payload