"""Python simulator for the reTerminal E1002 e-ink dashboard.

This module reproduces the Arduino sketch's pixel-level rendering in Python so
the dashboard layout, charts, and quota panel can be previewed as a PNG without
flashing hardware. It mirrors `eink/e1002/dashboard_render.h` and
`dashboard_logic.h` field-for-field; when the firmware layout changes, update
this module to match and regenerate the preview.

The simulator targets a PIL (Pillow) bitmap at the E1002 native 800x480. It uses
a hand-rolled 6x8 monospace glyph renderer to approximate the Adafruit GFX
textSize(1) font used by the sketch, so text positions line up with the
firmware's `drawString(x, y)` baseline convention.

Run as a script to write `tmp/e1002_simulator_preview.png` from the on-disk
`token_usage_eink.json`, or import `render_dashboard(data, battery, mode)` to
render an in-memory payload.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

# --- screen + palette -------------------------------------------------------

SCREEN_WIDTH = 800
SCREEN_HEIGHT = 480

# TFT_eSPI 16-bit colors mapped to PIL (r, g, b). Black text on white bg.
TFT_BLACK = (0, 0, 0)
TFT_WHITE = (255, 255, 255)
TFT_GREEN = (0, 255, 0)
TFT_RED = (255, 0, 0)
TFT_YELLOW = (255, 255, 0)
TFT_BLUE = (0, 0, 255)
TFT_CYAN = (0, 255, 255)

_MAX_DAYS = 30
_MAX_QUOTAS = 12


# --- data model mirroring dashboard_types.h ---------------------------------

@dataclass
class DailyEntry:
    date_label: str = ""
    cursor: int = 0
    glm: int = 0
    gemini: int = 0
    claude: int = 0
    gpt: int = 0
    deepseek: int = 0
    other: int = 0
    total_tokens: int = 0
    ai_hours: float = 0.0
    cost_usd: float = 0.0


@dataclass
class QuotaWindow:
    provider: str = ""
    label: str = ""
    percentage: int = 0
    next_reset_time_ms: int = 0
    next_reset_iso: str = ""


@dataclass
class DashboardData:
    generated_at: str = ""
    start_date: str = ""
    end_date: str = ""
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_ai_hours: float = 0.0
    cursor: int = 0
    glm: int = 0
    gemini: int = 0
    claude: int = 0
    gpt: int = 0
    deepseek: int = 0
    other: int = 0
    daily: list[DailyEntry] = field(default_factory=list)
    quotas: list[QuotaWindow] = field(default_factory=list)


@dataclass
class BatteryStatus:
    voltage: float = 0.0
    percentage: int = -1


# --- logic mirroring dashboard_logic.h --------------------------------------

SEVEN_DAYS = "7d"
THIRTY_DAYS = "30d"


def view_mode_label(mode: str) -> str:
    return "7D" if mode == SEVEN_DAYS else "30D"


def auto_update_label() -> str:
    return "Auto 08-22"


def display_count(daily_count: int, mode: str) -> int:
    if mode == THIRTY_DAYS:
        return daily_count
    return 7 if daily_count > 7 else daily_count


def display_start_index(daily_count: int, mode: str) -> int:
    count = display_count(daily_count, mode)
    return daily_count - count if daily_count > count else 0


@dataclass
class WindowSummary:
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_ai_hours: float = 0.0


def compute_window_summary(data: DashboardData, start_index: int, count: int) -> WindowSummary:
    summary = WindowSummary()
    for i in range(start_index, start_index + count):
        summary.total_tokens += data.daily[i].total_tokens
        summary.total_cost_usd += data.daily[i].cost_usd
        summary.total_ai_hours += data.daily[i].ai_hours
    return summary


def format_millions(value: int) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def format_hours(value: float) -> str:
    return f"{value:.2f} h"


def compact_date_label(iso_date: str) -> str:
    if len(iso_date) >= 10:
        return f"{iso_date[5:7]}/{iso_date[8:10]}"
    return iso_date


def reset_countdown_label(reset_ms, now_ts=None) -> str:
    """Reset countdown from epoch ms. 'reset in Xd Yh' or 'reset in X.Yh'."""
    import time as _time
    if not reset_ms:
        return ""
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
        return f"reset in {days}d {hours}h"
    return f"reset in {total_minutes / 60.0:.1f}h"


def provider_display_name(provider: str) -> str:
    if provider == "glm":
        return "GLM"
    if provider == "antigravity":
        return "Antigravity"
    return provider.capitalize()


def provider_color(provider: str) -> tuple[int, int, int]:
    return {
        "glm": TFT_GREEN,
        "codex": TFT_YELLOW,
        "ollama": TFT_CYAN,
        "claude": TFT_RED,
        "antigravity": TFT_CYAN,
    }.get(provider, TFT_BLACK)


def max_stack_value(data: DashboardData, start_index: int, count: int) -> float:
    max_value = 0.0
    for i in range(start_index, start_index + count):
        yi = data.daily[i].total_tokens / 1e8
        if yi > max_value:
            max_value = yi
    return max_value if max_value > 0.0 else 1.0


def max_hours_value(data: DashboardData, start_index: int, count: int) -> float:
    max_value = 0.0
    for i in range(start_index, start_index + count):
        if data.daily[i].ai_hours > max_value:
            max_value = data.daily[i].ai_hours
    return max_value if max_value > 0.0 else 1.0


def scaled_height(value: float, max_value: float, chart_height: int) -> int:
    if max_value <= 0.0:
        return 0
    h = int((value / max_value) * chart_height)
    if h < 0:
        return 0
    if h > chart_height:
        return chart_height
    return h


# --- GFX-size-1 monospace glyph renderer ------------------------------------
# Adafruit GFX text size 1 is a 5x7 pixel glyph in a 6x8 cell (1px advance).
# We embed a minimal ASCII subset (digits, letters, punctuation, space) drawn
# from the classic GFX font bitmap so the simulator text placement matches the
# firmware's drawString(x, y) convention (x is the left edge, y is the top).

# 5x7 font data for the printable ASCII range we need. Each char is 5 columns
# of 7 bits (top row = bit 6). Kept compact; unknown chars render as a blank
# 5x7 box so text still advances the correct width.
_GFX_FONT: dict[str, tuple[int, int, int, int, int]] = {
    ' ': (0x00, 0x00, 0x00, 0x00, 0x00),
    '!': (0x00, 0x00, 0x5F, 0x00, 0x00),
    '$': (0x24, 0x2A, 0x7F, 0x2A, 0x12),
    '%': (0x23, 0x13, 0x08, 0x64, 0x62),
    '(': (0x00, 0x1C, 0x3F, 0x07, 0x00),
    ')': (0x00, 0x07, 0x3F, 0x1C, 0x00),
    '+': (0x20, 0x04, 0x1F, 0x04, 0x20),
    ',': (0x00, 0x00, 0x0C, 0x0C, 0x00),
    '-': (0x00, 0x08, 0x08, 0x08, 0x00),
    '.': (0x00, 0x00, 0x06, 0x06, 0x00),
    '/': (0x20, 0x10, 0x08, 0x04, 0x02),
    '0': (0x3E, 0x51, 0x49, 0x45, 0x3E),
    '1': (0x00, 0x42, 0x7F, 0x40, 0x00),
    '2': (0x42, 0x61, 0x51, 0x49, 0x46),
    '3': (0x21, 0x41, 0x45, 0x4B, 0x31),
    '4': (0x18, 0x14, 0x12, 0x7F, 0x10),
    '5': (0x27, 0x45, 0x45, 0x45, 0x39),
    '6': (0x3C, 0x4A, 0x49, 0x49, 0x30),
    '7': (0x01, 0x71, 0x09, 0x05, 0x03),
    '8': (0x36, 0x49, 0x49, 0x49, 0x36),
    '9': (0x06, 0x49, 0x49, 0x29, 0x1E),
    ':': (0x00, 0x36, 0x36, 0x00, 0x00),
    'B': (0x7F, 0x49, 0x49, 0x49, 0x36),
    'D': (0x7F, 0x41, 0x41, 0x41, 0x3E),
    'G': (0x3E, 0x41, 0x49, 0x49, 0x4A),
    'H': (0x7F, 0x08, 0x08, 0x08, 0x7F),
    'M': (0x7F, 0x02, 0x04, 0x02, 0x7F),
    'P': (0x7F, 0x09, 0x09, 0x09, 0x06),
    'Q': (0x3E, 0x41, 0x49, 0x49, 0x7A),
    'T': (0x01, 0x01, 0x7F, 0x01, 0x01),
    'V': (0x1F, 0x20, 0x40, 0x20, 0x1F),
    '|': (0x00, 0x00, 0x7F, 0x00, 0x00),
    'a': (0x20, 0x54, 0x54, 0x54, 0x78),
    'c': (0x38, 0x44, 0x44, 0x44, 0x20),
    'd': (0x04, 0x54, 0x54, 0x4C, 0x3E),
    'e': (0x38, 0x54, 0x54, 0x54, 0x18),
    'f': (0x08, 0x7E, 0x09, 0x01, 0x02),
    'g': (0x08, 0x14, 0x54, 0x54, 0x3C),
    'h': (0x7F, 0x08, 0x04, 0x04, 0x78),
    'i': (0x00, 0x48, 0x7D, 0x40, 0x00),
    'k': (0x7F, 0x10, 0x28, 0x44, 0x00),
    'l': (0x00, 0x41, 0x7F, 0x40, 0x00),
    'm': (0x7C, 0x04, 0x78, 0x04, 0x78),
    'n': (0x7C, 0x08, 0x04, 0x04, 0x78),
    'o': (0x38, 0x44, 0x44, 0x44, 0x38),
    'p': (0x7C, 0x14, 0x14, 0x14, 0x08),
    'r': (0x7C, 0x08, 0x04, 0x04, 0x78),
    's': (0x48, 0x54, 0x54, 0x54, 0x20),
    't': (0x04, 0x3F, 0x44, 0x40, 0x20),
    'u': (0x3C, 0x40, 0x40, 0x20, 0x7C),
    'w': (0x1C, 0x20, 0x1C, 0x20, 0x1C),
    'x': (0x44, 0x28, 0x10, 0x28, 0x44),
    'y': (0x0C, 0x50, 0x50, 0x50, 0x3C),
    'z': (0x44, 0x64, 0x54, 0x4C, 0x44),
}


_PIL_FONT = ImageFont.load_default()


def _draw_text(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, color: tuple[int, int, int]) -> None:
    """Draw text in the GFX size-1 6x8 monospace cell, mirroring drawString.

    `x` is the left edge and `y` is the top of the glyph row (the firmware's
    drawString convention). Each char occupies a 6px column advance; the glyph
    is 5px wide, 7px tall, with row 0 at the top.
    """
    # Use Pillow's default bitmap font instead of the embedded GFX bitmap.
    # The simulator is for UX preview and layout review; this avoids mirrored
    # glyph bugs while preserving fixed-pixel placement and approximate size.
    draw.text((x, y), text, fill=color, font=_PIL_FONT)


# --- primitives mirroring EPaper ---------------------------------------------

class EPaperSim:
    """PIL-backed approximation of the EPaper API used by the sketch."""

    def __init__(self, width: int = SCREEN_WIDTH, height: int = SCREEN_HEIGHT) -> None:
        self.width = width
        self.height = height
        self.image = Image.new("RGB", (width, height), TFT_WHITE)
        self.draw = ImageDraw.Draw(self.image)

    def fill_screen(self, color: tuple[int, int, int]) -> None:
        self.draw.rectangle((0, 0, self.width - 1, self.height - 1), fill=color)

    def fill_rect(self, x: int, y: int, w: int, h: int, color: tuple[int, int, int]) -> None:
        if w <= 0 or h <= 0:
            return
        self.draw.rectangle((x, y, x + w - 1, y + h - 1), fill=color)

    def draw_rect(self, x: int, y: int, w: int, h: int, color: tuple[int, int, int]) -> None:
        if w <= 0 or h <= 0:
            return
        self.draw.rectangle((x, y, x + w - 1, y + h - 1), outline=color)

    def draw_line(self, x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int]) -> None:
        self.draw.line((x0, y0, x1, y1), fill=color)

    def draw_fast_hline(self, x: int, y: int, w: int, color: tuple[int, int, int]) -> None:
        self.draw.line((x, y, x + w - 1, y), fill=color)

    def draw_string(self, x: int, y: int, text: str, color: tuple[int, int, int] = TFT_BLACK) -> None:
        _draw_text(self.draw, x, y, text, color)

    def save(self, path: str) -> None:
        self.image.save(path)


# --- render functions mirroring dashboard_render.h --------------------------

@dataclass
class ChartRect:
    x: int
    y: int
    w: int
    h: int


def draw_legend_item(epaper: EPaperSim, x: int, y: int, fill_color: tuple[int, int, int], label: str, border_only: bool = False) -> None:
    if not border_only:
        epaper.fill_rect(x, y, 12, 12, fill_color)
    epaper.draw_rect(x, y, 12, 12, TFT_BLACK)
    epaper.draw_string(x + 18, y - 1, label)


def draw_diagonal_stripes(epaper: EPaperSim, rx: int, ry: int, w: int, h: int, spacing: int = 10) -> None:
    c = spacing
    while c < w + h:
        x0 = (c - h + 1) if c >= h else 0
        y0 = c if c < h else (h - 1)
        x1 = c if c < w else (w - 1)
        y1 = (c - w + 1) if c >= w else 0
        if x0 < w and y1 < h:
            epaper.draw_line(rx + x0, ry + y0, rx + x1, ry + y1, TFT_BLACK)
        c += spacing


def draw_legend_item_striped(epaper: EPaperSim, x: int, y: int, fill_color: tuple[int, int, int], label: str) -> None:
    epaper.fill_rect(x, y, 12, 12, fill_color)
    draw_diagonal_stripes(epaper, x, y, 12, 12, 4)
    epaper.draw_rect(x, y, 12, 12, TFT_BLACK)
    epaper.draw_string(x + 18, y - 1, label)


def draw_axis_and_ticks(epaper: EPaperSim, rect: ChartRect, max_value: float, axis_label: str, tick_count: int) -> None:
    epaper.draw_rect(rect.x, rect.y, rect.w, rect.h, TFT_BLACK)
    epaper.draw_string(rect.x, rect.y - 14, axis_label)
    for i in range(tick_count + 1):
        y = rect.y + rect.h - int((i / tick_count) * rect.h)
        epaper.draw_fast_hline(rect.x - 4, y, 4, TFT_BLACK)
        label = f"{(max_value / tick_count) * i:.0f}"
        epaper.draw_string(rect.x - 24, y - 4, label)


def draw_stacked_chart(epaper: EPaperSim, data: DashboardData, rect: ChartRect, start_index: int, count: int, mode: str) -> None:
    max_value = max_stack_value(data, start_index, count)
    draw_axis_and_ticks(epaper, rect, max_value, "Tokens (1e8)", 4)
    if count == 0:
        return
    gap = 2 if mode == THIRTY_DAYS else 10
    bar_width = (rect.w - (count + 1) * gap) // count
    label_stride = 5 if mode == THIRTY_DAYS else 1
    for visible_index in range(count):
        i = start_index + visible_index
        x = rect.x + gap + visible_index * (bar_width + gap)
        y_bottom = rect.y + rect.h
        segments = [
            (data.daily[i].cursor, TFT_WHITE, True, False),
            (data.daily[i].glm, TFT_GREEN, False, False),
            (data.daily[i].gemini, TFT_WHITE, False, True),
            (data.daily[i].claude, TFT_RED, False, False),
            (data.daily[i].gpt, TFT_YELLOW, False, False),
            (data.daily[i].deepseek, TFT_BLUE, False, False),
            (data.daily[i].other, TFT_BLACK, False, False),
        ]
        for value, color, border_only, striped in segments:
            yi = value / 1e8
            height = scaled_height(yi, max_value, rect.h - 2)
            if height <= 0:
                continue
            y_bottom -= height
            if not border_only:
                epaper.fill_rect(x, y_bottom, bar_width, height, color)
                if striped:
                    draw_diagonal_stripes(epaper, x, y_bottom, bar_width, height)
            epaper.draw_rect(x, y_bottom, bar_width, height, TFT_BLACK)
        if visible_index % label_stride == 0 or visible_index == count - 1:
            epaper.draw_string(x, rect.y + rect.h + 6, data.daily[i].date_label)


def draw_hours_chart(epaper: EPaperSim, data: DashboardData, rect: ChartRect, start_index: int, count: int, mode: str) -> None:
    max_value = max_hours_value(data, start_index, count)
    draw_axis_and_ticks(epaper, rect, max_value, "Hours", 3)
    if count == 0:
        return
    gap = 2 if mode == THIRTY_DAYS else 10
    bar_width = (rect.w - (count + 1) * gap) // count
    label_stride = 5 if mode == THIRTY_DAYS else 1
    for visible_index in range(count):
        i = start_index + visible_index
        x = rect.x + gap + visible_index * (bar_width + gap)
        height = scaled_height(data.daily[i].ai_hours, max_value, rect.h - 2)
        y = rect.y + rect.h - height
        epaper.fill_rect(x, y, bar_width, height, TFT_BLUE)
        epaper.draw_rect(x, y, bar_width, height, TFT_BLACK)
        if visible_index % label_stride == 0 or visible_index == count - 1:
            epaper.draw_string(x, rect.y + rect.h + 6, data.daily[i].date_label)


def draw_quota_bar(epaper: EPaperSim, x: int, y: int, w: int, qw: QuotaWindow) -> None:
    bar_h = 14
    pct = max(0, min(100, qw.percentage))
    fill_w = (w * pct) // 100
    color = provider_color(qw.provider)
    epaper.fill_rect(x, y, w, bar_h, TFT_WHITE)
    if fill_w > 0:
        epaper.fill_rect(x, y, fill_w, bar_h, color)
    epaper.draw_rect(x, y, w, bar_h, TFT_BLACK)
    head = f"{provider_display_name(qw.provider)} {qw.label}"
    reset = reset_countdown_label(qw.next_reset_time_ms)
    if reset:
        head += f", {reset}"
    epaper.draw_string(x, y + bar_h + 2, head)


def draw_quota_panel(epaper: EPaperSim, data: DashboardData, x: int, y: int, w: int) -> None:
    epaper.draw_string(x, y, "Quotas")
    if not data.quotas:
        epaper.draw_string(x, y + 18, "--")
        return
    row_h = 36
    bar_y = y + 16
    for qw in data.quotas:
        draw_quota_bar(epaper, x, bar_y, w, qw)
        bar_y += row_h


def render_dashboard(data: DashboardData, battery: BatteryStatus, mode: str = SEVEN_DAYS, margin: int = 10) -> EPaperSim:
    epaper = EPaperSim()
    epaper.fill_screen(TFT_WHITE)
    start_index = display_start_index(len(data.daily), mode)
    count = display_count(len(data.daily), mode)
    window_summary = compute_window_summary(data, start_index, count)

    k_chart_x = 36
    k_left_width = 524
    k_quota_x = 585
    k_quota_w = 195

    title = f"{format_millions(window_summary.total_tokens)} tokens | ${window_summary.total_cost_usd:.0f} | {view_mode_label(mode)}"
    epaper.draw_string(margin, 14, title)

    epaper.draw_string(margin, 30, "AI Active Time total: " + format_hours(window_summary.total_ai_hours))

    draw_legend_item(epaper, 570, 12, TFT_WHITE, "Cursor", border_only=True)
    draw_legend_item(epaper, 640, 12, TFT_GREEN, "GLM")
    draw_legend_item(epaper, 715, 12, TFT_RED, "Claude")
    draw_legend_item_striped(epaper, 570, 30, TFT_WHITE, "Gemini")
    draw_legend_item(epaper, 640, 30, TFT_YELLOW, "GPT")
    draw_legend_item(epaper, 715, 30, TFT_BLUE, "DeepSeek")
    draw_legend_item(epaper, 715, 48, TFT_BLACK, "Other")

    epaper.draw_string(610, 456, f"Battery: {battery.percentage}% ({battery.voltage:.2f}V)")

    stacked_rect = ChartRect(k_chart_x, 92, k_left_width, 200)
    hours_rect = ChartRect(k_chart_x, 336, k_left_width, 88)
    draw_stacked_chart(epaper, data, stacked_rect, start_index, count, mode)
    draw_hours_chart(epaper, data, hours_rect, start_index, count, mode)
    draw_quota_panel(epaper, data, k_quota_x, 92, k_quota_w)

    epaper.draw_string(margin, 456, "Updated: " + data.generated_at + " , " + auto_update_label())
    return epaper


# --- payload -> DashboardData parser mirroring dashboard_network.h ----------

def parse_dashboard_payload(payload: dict) -> DashboardData:
    data = DashboardData()
    meta = payload.get("meta", {})
    data.generated_at = meta.get("generated_at", "")
    data.start_date = meta.get("start_date", "")
    data.end_date = meta.get("end_date", "")
    summary = payload.get("summary", {})
    data.total_tokens = int(summary.get("total_tokens", 0))
    data.total_cost_usd = float(summary.get("total_cost_usd", 0.0) or 0.0)
    data.total_ai_hours = float(summary.get("total_ai_hours", 0.0) or 0.0)
    cats = summary.get("categories", {})
    data.cursor = int(cats.get("cursor", 0))
    data.glm = int(cats.get("glm", 0))
    data.gemini = int(cats.get("gemini", 0))
    data.claude = int(cats.get("claude", 0))
    data.gpt = int(cats.get("gpt_opencode", 0))
    data.deepseek = int(cats.get("deepseek", 0))
    data.other = int(cats.get("other", 0))

    for day in payload.get("daily", []):
        if len(data.daily) >= _MAX_DAYS:
            break
        entry = DailyEntry()
        entry.date_label = compact_date_label(day.get("date", ""))
        dc = day.get("categories", {})
        entry.cursor = int(dc.get("cursor", 0))
        entry.glm = int(dc.get("glm", 0))
        entry.gemini = int(dc.get("gemini", 0))
        entry.claude = int(dc.get("claude", 0))
        entry.gpt = int(dc.get("gpt_opencode", 0))
        entry.deepseek = int(dc.get("deepseek", 0))
        entry.other = int(dc.get("other", 0))
        entry.total_tokens = int(day.get("total_tokens", 0))
        entry.ai_hours = float(day.get("ai_hours", 0.0) or 0.0)
        entry.cost_usd = float(day.get("cost_usd", 0.0) or 0.0)
        data.daily.append(entry)

    for q in payload.get("quotas", []):
        if len(data.quotas) >= _MAX_QUOTAS:
            break
        data.quotas.append(QuotaWindow(
            provider=str(q.get("provider", "")),
            label=str(q.get("label", "")),
            percentage=int(q.get("percentage", 0)),
            next_reset_time_ms=int(q.get("next_reset_time_ms", 0) or 0),
            next_reset_iso=str(q.get("next_reset_iso", "")),
        ))
    return data


def render_from_payload(payload: dict, battery: Optional[BatteryStatus] = None, mode: str = SEVEN_DAYS) -> EPaperSim:
    data = parse_dashboard_payload(payload)
    return render_dashboard(data, battery or BatteryStatus(), mode=mode)


def main() -> None:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    payload_path = os.path.join(script_dir, "token_usage_eink.json")
    with open(payload_path) as f:
        payload = json.load(f)
    battery = BatteryStatus(voltage=3.92, percentage=80)
    epaper_7d = render_from_payload(payload, battery=battery, mode=SEVEN_DAYS)
    epaper_30d = render_from_payload(payload, battery=battery, mode=THIRTY_DAYS)
    out_path = os.path.join(script_dir, "tmp", "e1002_simulator_preview.png")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    # Stack 7d on top, 30d on bottom into a single image.
    combined = Image.new("RGB", (SCREEN_WIDTH, SCREEN_HEIGHT * 2), TFT_WHITE)
    combined.paste(epaper_7d.image, (0, 0))
    combined.paste(epaper_30d.image, (0, SCREEN_HEIGHT))
    combined.save(out_path)
    print(f"Preview saved to {out_path} (7d + 30d stacked)")


if __name__ == "__main__":
    main()
