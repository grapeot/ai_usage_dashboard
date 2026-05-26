from datetime import datetime
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from auto_usage import build_latest_dashboard_payload


class UpdateRequest(BaseModel):
    reason: str
    view: str
    device_id: str


app = FastAPI(title="ai_usage_dashboard")

_cached_payload: dict[str, Any] | None = None
_payload_path = Path(__file__).resolve().parent / "token_usage_eink.json"


def _read_payload_from_disk() -> dict[str, Any] | None:
    if not _payload_path.exists():
        return None
    with _payload_path.open() as f:
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


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "ai_usage_dashboard",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


@app.get("/token_usage.json")
def token_usage_json() -> dict[str, Any]:
    return read_cached_payload()


@app.post("/api/v1/display/update")
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
