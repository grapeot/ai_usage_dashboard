from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from firmware_logic import (
    SEVEN_DAYS,
    THIRTY_DAYS,
    compact_date_label,
    display_count,
    display_start_index,
    green_button_should_fetch,
    is_within_auto_update_window,
    toggle_view_mode,
    white_button_should_fetch,
)


def test_white_button_stays_local_only():
    assert white_button_should_fetch() is False


def test_green_button_triggers_fetch():
    assert green_button_should_fetch() is True


def test_auto_update_window_before_8am_is_disabled():
    assert is_within_auto_update_window(datetime(2026, 4, 1, 7, 59)) is False


def test_auto_update_window_8am_is_enabled():
    assert is_within_auto_update_window(datetime(2026, 4, 1, 8, 0)) is True


def test_auto_update_window_10_59pm_is_enabled_by_current_hour_gate():
    assert is_within_auto_update_window(datetime(2026, 4, 1, 22, 59)) is True


def test_auto_update_window_11pm_is_disabled():
    assert is_within_auto_update_window(datetime(2026, 4, 1, 23, 0)) is False


def test_toggle_view_mode_flips_7d_to_30d():
    assert toggle_view_mode(SEVEN_DAYS) == THIRTY_DAYS


def test_toggle_view_mode_flips_30d_to_7d():
    assert toggle_view_mode(THIRTY_DAYS) == SEVEN_DAYS


def test_display_count_returns_all_rows_for_30d():
    assert display_count(12, THIRTY_DAYS) == 12


def test_display_count_caps_7d_mode_at_7():
    assert display_count(12, SEVEN_DAYS) == 7


def test_display_count_keeps_small_7d_windows():
    assert display_count(5, SEVEN_DAYS) == 5


def test_display_start_index_returns_tail_window_for_7d():
    assert display_start_index(12, SEVEN_DAYS) == 5


def test_display_start_index_zero_when_not_enough_rows():
    assert display_start_index(5, SEVEN_DAYS) == 0


def test_display_start_index_zero_for_30d():
    assert display_start_index(12, THIRTY_DAYS) == 0


def test_compact_date_label_shortens_iso_date():
    assert compact_date_label("2026-04-01") == "04/01"


def test_compact_date_label_keeps_non_iso_text():
    assert compact_date_label("today") == "today"
