"""Tests for the E1002 Python simulator.

These verify the simulator renders a dashboard from a synthetic payload without
raising, produces an 800x480 image, and mirrors the firmware layout constants
and quota-panel logic. They do not assert exact pixel values; the simulator is a
UX preview tool, so the contract is "renders without error at the right size and
the quota panel reflects the payload".
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eink_simulator import (
    DashboardData,
    DailyEntry,
    QuotaWindow,
    BatteryStatus,
    EPaperSim,
    SCREEN_WIDTH,
    SCREEN_HEIGHT,
    parse_dashboard_payload,
    render_dashboard,
    provider_color,
    compact_reset_label,
    TFT_GREEN,
    TFT_CYAN,
    TFT_YELLOW,
)


def _sample_payload():
    return {
        'meta': {'generated_at': '2026-06-28T11:38:22', 'start_date': '2026-06-22', 'end_date': '2026-06-28'},
        'summary': {'total_tokens': 2000000000, 'total_cost_usd': 50.0, 'total_ai_hours': 57.2, 'categories': {
            'cursor': 49_000_000, 'glm': 1_200_000_000, 'gemini': 35_000_000,
            'claude': 0, 'gpt_opencode': 649_000_000, 'deepseek': 67_000_000, 'other': 0}},
        'daily': [
            {'date': f'2026-06-{22 + i:02d}', 'categories': {
                'cursor': 1_000_000, 'glm': 100_000_000 + i * 10_000_000,
                'gemini': 1_000_000, 'claude': 0, 'gpt_opencode': 80_000_000,
                'deepseek': 5_000_000, 'other': 0},
             'total_tokens': 200_000_000 + i * 10_000_000,
             'ai_hours': 7.0 + i * 0.5, 'cost_usd': 5.0}
            for i in range(7)
        ],
        'quotas': [
            {'provider': 'glm', 'label': '5h', 'percentage': 13, 'next_reset_iso': '2026-06-28T15:00:09'},
            {'provider': 'glm', 'label': '7d', 'percentage': 43, 'next_reset_iso': '2026-06-30T11:10:31'},
            {'provider': 'ollama', 'label': '5h', 'percentage': 48, 'next_reset_iso': '2026-06-28T22:00:00Z'},
            {'provider': 'ollama', 'label': '7d', 'percentage': 48, 'next_reset_iso': '2026-06-29T00:00:00Z'},
            {'provider': 'codex', 'label': '5h', 'percentage': 12, 'next_reset_iso': '2026-06-18T12:30:12'},
            {'provider': 'codex', 'label': '7d', 'percentage': 4, 'next_reset_iso': '2026-06-24T15:05:57'},
        ],
    }


def test_simulator_renders_800x480_image():
    epaper = render_dashboard(parse_dashboard_payload(_sample_payload()), BatteryStatus(voltage=3.92, percentage=80), mode='7d')

    assert epaper.image.size == (SCREEN_WIDTH, SCREEN_HEIGHT)


def test_simulator_renders_30d_mode_without_error():
    epaper = render_dashboard(parse_dashboard_payload(_sample_payload()), BatteryStatus(), mode='30d')

    assert epaper.image.size == (SCREEN_WIDTH, SCREEN_HEIGHT)


def test_simulator_handles_empty_quotas():
    payload = _sample_payload()
    payload['quotas'] = []
    epaper = render_dashboard(parse_dashboard_payload(payload), BatteryStatus(), mode='7d')

    assert epaper.image.size == (SCREEN_WIDTH, SCREEN_HEIGHT)


def test_simulator_handles_empty_daily():
    payload = _sample_payload()
    payload['daily'] = []
    epaper = render_dashboard(parse_dashboard_payload(payload), BatteryStatus(), mode='7d')

    assert epaper.image.size == (SCREEN_WIDTH, SCREEN_HEIGHT)


def test_simulator_provider_color_includes_ollama():
    assert provider_color('glm') == TFT_GREEN
    assert provider_color('ollama') == TFT_CYAN
    assert provider_color('codex') == TFT_YELLOW


def test_simulator_compact_reset_label_formats_iso():
    assert compact_reset_label('2026-06-28T22:00:00Z') == 'reset @ 06/28 22:00'


def test_simulator_parse_dashboard_payload_reads_quotas():
    data = parse_dashboard_payload(_sample_payload())

    assert len(data.quotas) == 6
    assert data.quotas[2].provider == 'ollama'
    assert data.quotas[2].label == '5h'


def test_simulator_epaper_save(tmp_path):
    epaper = render_dashboard(parse_dashboard_payload(_sample_payload()), BatteryStatus(), mode='7d')
    out = tmp_path / 'preview.png'
    epaper.save(str(out))

    assert out.exists()
    assert out.stat().st_size > 0