from datetime import datetime
import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from display_contract import (
    green_button_should_fetch,
    is_within_auto_update_window,
    white_button_should_fetch,
)
import local_display_service


def test_white_button_is_local_only():
    assert white_button_should_fetch() is False


def test_green_button_triggers_fetch():
    assert green_button_should_fetch() is True


def test_auto_update_window_excludes_before_8am():
    assert is_within_auto_update_window(datetime(2026, 4, 1, 7, 59)) is False


def test_auto_update_window_includes_8am():
    assert is_within_auto_update_window(datetime(2026, 4, 1, 8, 0)) is True


def test_auto_update_window_includes_959pm():
    assert is_within_auto_update_window(datetime(2026, 4, 1, 21, 59)) is True


def test_auto_update_window_includes_10pm():
    assert is_within_auto_update_window(datetime(2026, 4, 1, 22, 0)) is True


def test_health_endpoint_returns_ok_status():
    client = TestClient(local_display_service.app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_get_token_usage_json_returns_dashboard_shape(monkeypatch):
    payload = {
        "meta": {"version": 1},
        "summary": {"total_tokens": 1},
        "daily": [],
    }
    monkeypatch.setattr(local_display_service, "_cached_payload", None)
    monkeypatch.setattr(local_display_service, "generate_latest_payload", lambda: payload)

    client = TestClient(local_display_service.app)
    response = client.get("/token_usage.json")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) >= {"daily", "meta", "summary"}
    assert body["summary"]["total_tokens"] == 1
    assert body["daily"] == []


def test_post_update_returns_fresh_dashboard_shape(monkeypatch):
    payload = {
        "meta": {"version": 1, "generated_at": "2026-04-01T10:00:00"},
        "summary": {"total_tokens": 42},
        "daily": [{"date": "2026-04-01", "total_tokens": 42}],
    }
    monkeypatch.setattr(local_display_service, "_cached_payload", None)
    monkeypatch.setattr(local_display_service, "generate_latest_payload", lambda: payload)

    client = TestClient(local_display_service.app)
    response = client.post(
        "/api/v1/display/update",
        json={"reason": "force_button", "view": "7d", "device_id": "example-device"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["generated_at"] == "2026-04-01T10:00:00"
    assert body["summary"]["total_tokens"] == 42
    assert body["daily"][0]["date"] == "2026-04-01"
    assert body["daily"][0]["total_tokens"] == 42


def test_get_token_usage_json_falls_back_to_disk_when_refresh_fails(monkeypatch, tmp_path):
    payload = {
        "meta": {"version": 1},
        "summary": {"total_tokens": 5},
        "daily": [],
    }
    payload_path = tmp_path / "token_usage_eink.json"
    payload_path.write_text(json.dumps(payload))

    monkeypatch.setattr(local_display_service, "_cached_payload", None)
    monkeypatch.setattr(local_display_service, "_payload_path", payload_path)
    monkeypatch.setattr(local_display_service, "generate_latest_payload", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    client = TestClient(local_display_service.app)
    response = client.get("/token_usage.json")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_tokens"] == 5
    assert body["daily"] == []


def test_post_update_returns_cached_payload_when_refresh_fails(monkeypatch):
    cached = {
        "meta": {"version": 1},
        "summary": {"total_tokens": 7},
        "daily": [],
    }
    monkeypatch.setattr(local_display_service, "_cached_payload", cached)
    monkeypatch.setattr(local_display_service, "generate_latest_payload", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    client = TestClient(local_display_service.app)
    response = client.post(
        "/api/v1/display/update",
        json={"reason": "force_button", "view": "7d", "device_id": "example-device"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_tokens"] == 7
    assert body["daily"] == []
