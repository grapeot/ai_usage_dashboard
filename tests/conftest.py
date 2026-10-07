import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def isolate_generated_display_payload(monkeypatch, tmp_path):
    import auto_usage
    monkeypatch.setattr(auto_usage, 'OUTPUT_EINK_JSON', str(tmp_path / 'token_usage_eink.json'))
