from __future__ import annotations

from datetime import datetime


SEVEN_DAYS = "7d"
THIRTY_DAYS = "30d"


def white_button_should_fetch() -> bool:
    return False


def green_button_should_fetch() -> bool:
    return True


def is_within_auto_update_window(current_time: datetime) -> bool:
    return 8 <= current_time.hour <= 22


def toggle_view_mode(mode: str) -> str:
    return THIRTY_DAYS if mode == SEVEN_DAYS else SEVEN_DAYS


def display_count(daily_count: int, mode: str) -> int:
    if mode == THIRTY_DAYS:
        return daily_count
    return 7 if daily_count > 7 else daily_count


def display_start_index(daily_count: int, mode: str) -> int:
    count = display_count(daily_count, mode)
    return daily_count - count if daily_count > count else 0


def compact_date_label(iso_date: str) -> str:
    if len(iso_date) >= 10:
        return f"{iso_date[5:7]}/{iso_date[8:10]}"
    return iso_date
