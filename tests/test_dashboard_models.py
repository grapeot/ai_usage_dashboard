"""Tests for the Pydantic response models that back /openapi.json."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard_models import (
    AutomationQuotaSnapshot,
    DashboardPayload,
    GlmQuotaSnapshot,
    HealthResponse,
    QuotaSnapshot,
    QuotasResponse,
    UpdateRequest,
)


def test_dashboard_payload_validates_full_real_shape():
    payload = {
        'meta': {
            'version': 1,
            'generated_at': '2026-06-28T11:20:48',
            'start_date': '2026-06-22',
            'end_date': '2026-06-28',
            'days': 7,
            'currency': 'USD',
        },
        'summary': {
            'total_tokens': 2004831469,
            'total_ai_hours': 56.95,
            'categories': {
                'cursor': 49124065,
                'glm': 1203115607,
                'gemini': 35736447,
                'claude': 0,
                'gpt_opencode': 649239547,
                'deepseek': 67615803,
                'other': 0,
            },
            'total_cost_usd': 12.34,
        },
        'daily': [
            {
                'date': '2026-06-22',
                'categories': {'cursor': 1, 'glm': 2, 'gemini': 3, 'claude': 0, 'gpt_opencode': 0, 'deepseek': 0, 'other': 0},
                'total_tokens': 6,
                'ai_hours': 8.27,
                'cost_usd': 1.5,
            }
        ],
        'glm_quota': [
            {
                'label': '5 Hours Quota',
                'type': 'TOKENS_LIMIT',
                'unit': 3,
                'percentage': 13,
                'next_reset_time_ms': 1782684009923,
                'next_reset_iso': '2026-06-28T15:00:09',
                'usage': None,
                'current_value': None,
                'remaining': None,
                'usage_details': None,
            }
        ],
    }

    model = DashboardPayload.model_validate(payload)

    assert model.meta.days == 7
    assert model.summary.total_cost_usd == 12.34
    assert model.daily[0].ai_hours == 8.27
    assert model.glm_quota[0].label == '5 Hours Quota'
    assert model.glm_quota[0].next_reset_iso == '2026-06-28T15:00:09'


def test_dashboard_payload_accepts_minimal_shape_with_defaults():
    """A stale or minimal cached payload must still validate."""
    model = DashboardPayload.model_validate({'meta': {}, 'summary': {}, 'daily': []})

    assert model.meta.version == 1
    assert model.summary.total_tokens == 0
    assert model.daily == []
    assert model.glm_quota is None


def test_dashboard_payload_round_trips_via_model_dump():
    payload = {
        'meta': {'version': 1, 'start_date': '2026-06-22', 'end_date': '2026-06-28'},
        'summary': {'total_tokens': 100, 'total_ai_hours': 1.5, 'categories': {}},
        'daily': [],
    }

    model = DashboardPayload.model_validate(payload)
    dumped = model.model_dump()

    assert dumped['meta']['start_date'] == '2026-06-22'
    assert dumped['summary']['total_tokens'] == 100


def test_glm_quota_snapshot_requires_label_type_unit():
    with pytest.raises(Exception):
        GlmQuotaSnapshot.model_validate({'percentage': 10})


def test_glm_quota_snapshot_allows_missing_reset_time():
    snap = GlmQuotaSnapshot.model_validate({'label': 'Weekly Quota', 'type': 'TOKENS_LIMIT', 'unit': 6, 'percentage': 43})

    assert snap.next_reset_time_ms is None
    assert snap.next_reset_iso is None


def test_health_response_model_carries_status_and_service():
    resp = HealthResponse.model_validate({'status': 'ok', 'service': 'ai_usage_dashboard', 'generated_at': '2026-06-28T11:20:48'})

    assert resp.status == 'ok'
    assert resp.service == 'ai_usage_dashboard'


def test_update_request_model_validates_required_fields():
    req = UpdateRequest.model_validate({'reason': 'force_button', 'view': '7d', 'device_id': 'dev-1'})

    assert req.reason == 'force_button'
    assert req.view == '7d'
    assert req.device_id == 'dev-1'

    with pytest.raises(Exception):
        UpdateRequest.model_validate({'reason': 'force_button'})


def test_model_fields_carry_descriptions_for_openapi():
    """Every serialized field must have a description so /openapi.json is AI-readable."""
    for model_cls in (DashboardPayload, GlmQuotaSnapshot, QuotaSnapshot, AutomationQuotaSnapshot, QuotasResponse, HealthResponse, UpdateRequest):
        for name, field in model_cls.model_fields.items():
            assert field.description, f'{model_cls.__name__}.{name} missing description'


def test_quota_snapshot_model_validates_codex_shape():
    snap = QuotaSnapshot.model_validate({
        'provider': 'codex',
        'label': '5 Hours',
        'percentage': 12,
        'next_reset_time_ms': 1781811012000,
        'next_reset_iso': '2026-06-18T12:30:12',
    })

    assert snap.provider == 'codex'
    assert snap.percentage == 12
    assert snap.usage is None


def test_dashboard_payload_includes_unified_quotas():
    payload = DashboardPayload.model_validate({
        'meta': {},
        'summary': {},
        'daily': [],
        'quotas': [
            {'provider': 'glm', 'label': '5 Hours Quota', 'percentage': 13},
            {'provider': 'codex', 'label': 'Weekly', 'percentage': 4},
        ],
    })

    assert payload.quotas is not None
    assert len(payload.quotas) == 2
    assert payload.quotas[1].provider == 'codex'


def test_quotas_response_requires_explicit_automation_percentages():
    response = QuotasResponse.model_validate({
        'generated_at': '2026-07-11T22:47:45',
        'quotas': [{
            'provider': 'codex',
            'label': '5h',
            'used_percentage': 29,
            'remaining_percentage': 71,
        }],
    })

    assert response.quotas[0].used_percentage == 29
    assert response.quotas[0].remaining_percentage == 71
