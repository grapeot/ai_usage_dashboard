"""Live integration tests for the GLM / Z.ai quota endpoint.

These hit the real Z.ai API and are skipped unless ``GLM_BEARER_TOKEN`` is set
in the environment. Run with::

    GLM_BEARER_TOKEN=<token> .venv/bin/python -m pytest tests/test_glm_quota_live.py -v -m live_api
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auto_usage import export_glm_quota, load_glm_quota, normalize_glm_quota  # noqa: E402

pytestmark = pytest.mark.live_api


@pytest.fixture(autouse=True)
def _require_token():
    if not os.environ.get('GLM_BEARER_TOKEN'):
        pytest.skip('GLM_BEARER_TOKEN not set; skipping live quota test')


def test_export_glm_quota_returns_success_and_normalizes(monkeypatch, tmp_path):
    monkeypatch.setattr('auto_usage.SCRIPT_DIR', str(tmp_path))
    token = os.environ['GLM_BEARER_TOKEN']

    body = export_glm_quota(token)

    assert body.get('success') is True
    snapshots = normalize_glm_quota(body)
    assert len(snapshots) >= 1
    for snapshot in snapshots:
        assert snapshot['label']
        assert isinstance(snapshot['percentage'], int)
        assert snapshot['next_reset_time_ms'] is None or isinstance(snapshot['next_reset_time_ms'], int)


def test_export_glm_quota_writes_cache_file(monkeypatch, tmp_path):
    monkeypatch.setattr('auto_usage.SCRIPT_DIR', str(tmp_path))
    token = os.environ['GLM_BEARER_TOKEN']

    export_glm_quota(token)

    cached = load_glm_quota()
    assert len(cached) >= 1