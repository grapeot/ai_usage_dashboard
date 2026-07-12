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


def test_get_quotas_returns_compact_automation_shape(monkeypatch):
    payload = {
        "meta": {"generated_at": "2026-07-11T22:47:45"},
        "summary": {},
        "daily": [],
        "quotas": [
            {
                "provider": "codex",
                "label": "5h",
                "percentage": 29,
                "next_reset_time_ms": 1783842841000,
                "next_reset_iso": "2026-07-12T00:54:01",
            },
            {
                "provider": "glm",
                "label": "monthly-tools",
                "percentage": 25,
                "usage": 1000,
                "remaining": 3000,
            },
        ],
    }
    monkeypatch.setattr(local_display_service, "_cached_payload", payload)

    response = TestClient(local_display_service.app).get("/api/v1/quotas")

    assert response.status_code == 200
    assert response.json() == {
        "generated_at": "2026-07-11T22:47:45",
        "quotas": [
            {
                "provider": "codex",
                "label": "5h",
                "used_percentage": 29,
                "remaining_percentage": 71,
                "next_reset_time_ms": 1783842841000,
                "next_reset_iso": "2026-07-12T00:54:01",
                "usage": None,
                "remaining": None,
            },
            {
                "provider": "glm",
                "label": "monthly-tools",
                "used_percentage": 25,
                "remaining_percentage": 75,
                "next_reset_time_ms": None,
                "next_reset_iso": None,
                "usage": 1000,
                "remaining": 3000,
            },
        ],
    }


def test_get_quotas_reuses_cache_without_refresh(monkeypatch):
    monkeypatch.setattr(local_display_service, "_cached_payload", {
        "meta": {"generated_at": "2026-07-11T22:47:45"},
        "summary": {},
        "daily": [],
        "quotas": [],
    })
    monkeypatch.setattr(local_display_service, "generate_latest_payload", lambda: (_ for _ in ()).throw(AssertionError("unexpected refresh")))

    response = TestClient(local_display_service.app).get("/api/v1/quotas")

    assert response.status_code == 200
    assert response.json() == {"generated_at": "2026-07-11T22:47:45", "quotas": []}


def test_get_quotas_reads_disk_without_refresh(monkeypatch, tmp_path):
    payload_path = tmp_path / "token_usage_eink.json"
    payload_path.write_text(json.dumps({
        "meta": {"generated_at": "2026-07-11T22:47:45"},
        "summary": {},
        "daily": [],
        "quotas": [{"provider": "codex", "label": "7d", "percentage": 13}],
    }))
    monkeypatch.setattr(local_display_service, "_cached_payload", None)
    monkeypatch.setattr(local_display_service, "_payload_path", payload_path)
    monkeypatch.setattr(local_display_service, "generate_latest_payload", lambda: (_ for _ in ()).throw(AssertionError("unexpected refresh")))

    response = TestClient(local_display_service.app).get("/api/v1/quotas")

    assert response.status_code == 200
    assert response.json()["quotas"][0]["remaining_percentage"] == 87


def test_get_quotas_returns_empty_when_no_cache_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(local_display_service, "_cached_payload", None)
    monkeypatch.setattr(local_display_service, "_payload_path", tmp_path / "missing.json")
    monkeypatch.setattr(local_display_service, "generate_latest_payload", lambda: (_ for _ in ()).throw(AssertionError("unexpected refresh")))

    response = TestClient(local_display_service.app).get("/api/v1/quotas")

    assert response.status_code == 200
    assert response.json() == {"generated_at": None, "quotas": []}


def test_get_quotas_has_typed_openapi_response():
    schema = TestClient(local_display_service.app).get("/openapi.json").json()

    response_schema = schema["paths"]["/api/v1/quotas"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert response_schema == {"$ref": "#/components/schemas/QuotasResponse"}


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


def test_post_antigravity_ingest_accepts_entries(monkeypatch, tmp_path):
    cache_path = tmp_path / "antigravity_usage_cache.json"
    monkeypatch.setattr("auto_usage.ANTIGRAVITY_CACHE_FILE", str(cache_path))

    client = TestClient(local_display_service.app)
    entries = [
        {"model": "gemini-3-flash-a", "timestamp": 1711447200000,
         "input": 1000, "output": 200, "cache_read": 5000,
         "cache_write": 0, "thinking": 50, "response_id": "r1",
         "session_id": "s1"},
    ]
    response = client.post(
        "/api/v1/antigravity/ingest",
        json={"entries": entries, "source": "macbook-air"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["received"] == 1
    assert body["new"] == 1
    assert body["duplicate"] == 0
    assert body["total_cache"] == 1


def test_post_antigravity_ingest_deduplicates_existing_entries(monkeypatch, tmp_path):
    cache_path = tmp_path / "antigravity_usage_cache.json"
    monkeypatch.setattr("auto_usage.ANTIGRAVITY_CACHE_FILE", str(cache_path))

    client = TestClient(local_display_service.app)
    entries = [
        {"model": "gemini-3-flash-a", "timestamp": 1711447200000,
         "input": 1000, "response_id": "dup-1", "session_id": "s1"},
    ]
    # First push
    client.post("/api/v1/antigravity/ingest", json={"entries": entries})
    # Second push (same response_id)
    response = client.post("/api/v1/antigravity/ingest", json={"entries": entries})
    assert response.status_code == 200
    body = response.json()
    assert body["received"] == 1
    assert body["new"] == 0
    assert body["duplicate"] == 1
    assert body["total_cache"] == 1


def test_post_antigravity_ingest_accepts_empty_entries(monkeypatch, tmp_path):
    cache_path = tmp_path / "antigravity_usage_cache.json"
    monkeypatch.setattr("auto_usage.ANTIGRAVITY_CACHE_FILE", str(cache_path))

    client = TestClient(local_display_service.app)
    response = client.post(
        "/api/v1/antigravity/ingest",
        json={"entries": [], "source": "macbook-air"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["received"] == 0
    assert body["new"] == 0
    assert body["duplicate"] == 0
    assert body["total_cache"] == 0
