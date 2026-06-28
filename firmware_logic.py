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


def reset_countdown_label(reset_ms: int | None, now_ts: float | None = None) -> str:
    """Reset countdown label from epoch milliseconds.

    'reset in Xd Yh' or 'reset in X.Yh'. Returns '' when reset_ms is None or 0.
    Uses epoch directly to avoid mktime timezone issues.
    """
    import time as _time
    if not reset_ms:
        return ''
    if now_ts is None:
        now_ts = _time.time()
    diff_sec = int(reset_ms // 1000) - int(now_ts)
    if diff_sec < 0:
        diff_sec = 0
    total_minutes = int(diff_sec // 60)
    total_hours = total_minutes // 60
    days = total_hours // 24
    hours = total_hours % 24
    if days > 0:
        return f'reset in {days}d {hours}h'
    return f'reset in {total_minutes / 60.0:.1f}h'


def provider_display_name(provider: str) -> str:
    """Normalize provider names for display: GLM all-caps, others title-cased.

    Mirrors the e-ink firmware's providerDisplayName.
    """
    if provider == 'glm':
        return 'GLM'
    return provider.capitalize()


def provider_color(provider: str) -> str:
    """Return the palette color name for a quota provider.

    Mirrors the e-ink firmware's providerColor. Used by Python tests that
    verify the firmware selects the same color as the stacked-chart palette.
    """
    return {
        'glm': 'green',
        'codex': 'yellow',
        'claude': 'red',
        'ollama': 'cyan',
    }.get(provider, 'black')


def quota_bar_fill_width(width: int, percentage: int) -> int:
    """Filled pixel width for a horizontal quota bar.

    Mirrors the e-ink firmware's bar fill calculation. Clamps to [0, width].
    """
    pct = max(0, min(100, percentage))
    return (width * pct) // 100
