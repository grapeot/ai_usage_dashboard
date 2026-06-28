#pragma once

#include "TFT_eSPI.h"

#include "dashboard_logic.h"

inline void drawLegendItem(EPaper& epaper, int x, int y, uint16_t fillColor, const String& label, bool borderOnly = false) {
  if (!borderOnly) {
    epaper.fillRect(x, y, 12, 12, fillColor);
  }
  epaper.drawRect(x, y, 12, 12, TFT_BLACK);
  epaper.setTextColor(TFT_BLACK, TFT_WHITE);
  epaper.setTextSize(1);
  epaper.drawString(label, x + 18, y - 1);
}

// Draw 1px 45-degree lines from bottom-left to top-right, spaced `spacing` px apart.
inline void drawDiagonalStripes(EPaper& epaper, int rx, int ry, int w, int h, int spacing = 10) {
  for (int c = spacing; c < w + h; c += spacing) {
    int x0 = (c >= h) ? (c - h + 1) : 0;
    int y0 = (c < h) ? c : (h - 1);
    int x1 = (c < w) ? c : (w - 1);
    int y1 = (c >= w) ? (c - w + 1) : 0;
    if (x0 >= w || y1 >= h) continue;
    epaper.drawLine(rx + x0, ry + y0, rx + x1, ry + y1, TFT_BLACK);
  }
}

inline void drawLegendItemStriped(EPaper& epaper, int x, int y, uint16_t fillColor, const String& label) {
  epaper.fillRect(x, y, 12, 12, fillColor);
  drawDiagonalStripes(epaper, x, y, 12, 12, 4);
  epaper.drawRect(x, y, 12, 12, TFT_BLACK);
  epaper.setTextColor(TFT_BLACK, TFT_WHITE);
  epaper.setTextSize(1);
  epaper.drawString(label, x + 18, y - 1);
}

inline void drawAxisAndTicks(EPaper& epaper, const ChartRect& rect, double maxValue, const char* axisLabel, int tickCount) {
  epaper.drawRect(rect.x, rect.y, rect.w, rect.h, TFT_BLACK);
  epaper.setTextColor(TFT_BLACK, TFT_WHITE);
  epaper.setTextSize(1);
  epaper.drawString(axisLabel, rect.x, rect.y - 14);

  for (int i = 0; i <= tickCount; ++i) {
    int y = rect.y + rect.h - static_cast<int>((static_cast<double>(i) / tickCount) * rect.h);
    epaper.drawFastHLine(rect.x - 4, y, 4, TFT_BLACK);

    char label[16];
    snprintf(label, sizeof(label), "%.0f", (maxValue / tickCount) * i);
    epaper.drawString(String(label), rect.x - 24, y - 4);
  }
}

inline void drawStackedChart(EPaper& epaper, const DashboardData& data, const ChartRect& rect, size_t startIndex, size_t count, ViewMode mode) {
  double maxValue = maxStackValue(data, startIndex, count);
  drawAxisAndTicks(epaper, rect, maxValue, "Tokens (1e8)", 4);

  if (count == 0) {
    return;
  }

  int gap = mode == ViewMode::ThirtyDays ? 2 : 10;
  int barWidth = (rect.w - (static_cast<int>(count) + 1) * gap) / static_cast<int>(count);
  if (barWidth < 18) {
    barWidth = 18;
  }
  int labelStride = mode == ViewMode::ThirtyDays ? 5 : 1;

  for (size_t visibleIndex = 0; visibleIndex < count; ++visibleIndex) {
    size_t i = startIndex + visibleIndex;
    int x = rect.x + gap + static_cast<int>(visibleIndex) * (barWidth + gap);
    int yBottom = rect.y + rect.h;

    const struct Segment {
      uint64_t value;
      uint16_t color;
      bool borderOnly;
      bool striped;
    } segments[] = {
        {data.daily[i].cursor, TFT_WHITE, true, false},
        {data.daily[i].glm, TFT_GREEN, false, false},
        {data.daily[i].gemini, TFT_WHITE, false, true},
        {data.daily[i].claude, TFT_RED, false, false},
        {data.daily[i].gpt, TFT_YELLOW, false, false},
        {data.daily[i].deepseek, TFT_BLUE, false, false},
        {data.daily[i].other, TFT_BLACK, false, false},
    };

    for (const Segment& segment : segments) {
      double yi = static_cast<double>(segment.value) / 1e8;
      int height = scaledHeight(yi, maxValue, rect.h - 2);
      if (height <= 0) {
        continue;
      }
      yBottom -= height;
      if (!segment.borderOnly) {
        epaper.fillRect(x, yBottom, barWidth, height, segment.color);
        if (segment.striped) {
          drawDiagonalStripes(epaper, x, yBottom, barWidth, height);
        }
      }
      epaper.drawRect(x, yBottom, barWidth, height, TFT_BLACK);
    }

    if (visibleIndex % labelStride == 0 || visibleIndex == count - 1) {
      epaper.setTextColor(TFT_BLACK, TFT_WHITE);
      epaper.setTextSize(1);
      epaper.drawString(data.daily[i].dateLabel, x, rect.y + rect.h + 6);
    }
  }
}

inline void drawHoursChart(EPaper& epaper, const DashboardData& data, const ChartRect& rect, size_t startIndex, size_t count, ViewMode mode) {
  double maxValue = maxHoursValue(data, startIndex, count);
  drawAxisAndTicks(epaper, rect, maxValue, "Hours", 3);

  if (count == 0) {
    return;
  }

  int gap = mode == ViewMode::ThirtyDays ? 2 : 10;
  int barWidth = (rect.w - (static_cast<int>(count) + 1) * gap) / static_cast<int>(count);
  if (barWidth < 18) {
    barWidth = 18;
  }
  int labelStride = mode == ViewMode::ThirtyDays ? 5 : 1;

  for (size_t visibleIndex = 0; visibleIndex < count; ++visibleIndex) {
    size_t i = startIndex + visibleIndex;
    int x = rect.x + gap + static_cast<int>(visibleIndex) * (barWidth + gap);
    int height = scaledHeight(data.daily[i].aiHours, maxValue, rect.h - 2);
    int y = rect.y + rect.h - height;
    epaper.fillRect(x, y, barWidth, height, TFT_BLUE);
    epaper.drawRect(x, y, barWidth, height, TFT_BLACK);
    if (visibleIndex % labelStride == 0 || visibleIndex == count - 1) {
      epaper.setTextColor(TFT_BLACK, TFT_WHITE);
      epaper.setTextSize(1);
      epaper.drawString(data.daily[i].dateLabel, x, rect.y + rect.h + 6);
    }
  }
}

// Draw a horizontal usage bar for one quota window.
// Filled portion (used) is colored; the remainder is white with a black outline.
// Below the bar, the window label and reset time are drawn in small text.
inline void drawQuotaBar(EPaper& epaper, int x, int y, int w, const QuotaWindow& qw) {
  int barH = 14;
  int pct = qw.percentage;
  if (pct < 0) {
    pct = 0;
  }
  if (pct > 100) {
    pct = 100;
  }
  int fillW = (w * pct) / 100;

  uint16_t color = providerColor(qw.provider);
  epaper.fillRect(x, y, w, barH, TFT_WHITE);
  if (fillW > 0) {
    epaper.fillRect(x, y, fillW, barH, color);
  }
  epaper.drawRect(x, y, w, barH, TFT_BLACK);

  epaper.setTextColor(TFT_BLACK, TFT_WHITE);
  epaper.setTextSize(1);
  String head = qw.provider + " " + qw.label;
  epaper.drawString(head, x, y + barH + 2);
  String reset = compactResetLabel(qw.nextResetIso);
  if (reset.length() > 0) {
    epaper.drawString(reset, x, y + barH + 14);
  }
}

// Draw the right-hand quota panel: a header and one bar per quota window.
inline void drawQuotaPanel(EPaper& epaper, const DashboardData& data, int x, int y, int w) {
  epaper.setTextColor(TFT_BLACK, TFT_WHITE);
  epaper.setTextSize(1);
  epaper.drawString("Quotas", x, y);

  if (data.quotaCount == 0) {
    epaper.drawString("--", x, y + 18);
    return;
  }

  int rowH = 36;
  int barY = y + 16;
  for (size_t i = 0; i < data.quotaCount; ++i) {
    drawQuotaBar(epaper, x, barY, w, data.quotas[i]);
    barY += rowH;
  }
}

inline void renderDashboard(EPaper& epaper,
                            const DashboardData& data,
                            const BatteryStatus& battery,
                            ViewMode mode,
                            int margin) {
  epaper.fillScreen(TFT_WHITE);
  epaper.setTextColor(TFT_BLACK, TFT_WHITE);
  size_t startIndex = displayStartIndex(data, mode);
  size_t count = displayCount(data, mode);
  WindowSummary windowSummary = computeWindowSummary(data, startIndex, count);
  Serial.printf("[render] mode=%s start=%u count=%u windowTokens=%llu windowCost=%.2f windowHours=%.2f quotaCount=%u\n",
                viewModeLabel(mode),
                static_cast<unsigned>(startIndex),
                static_cast<unsigned>(count),
                static_cast<unsigned long long>(windowSummary.totalTokens),
                windowSummary.totalCostUsd,
                windowSummary.totalAiHours,
                static_cast<unsigned>(data.quotaCount));

  // Left zone (charts) occupies x=10..560 (width 550).
  // Right zone (quota panel) occupies x=575..795 (width 220).
  constexpr int kChartX = 36;
  constexpr int kLeftWidth = 524;
  constexpr int kQuotaX = 575;
  constexpr int kQuotaW = 220;

  epaper.setTextSize(1);
  char titleBuffer[96];
  snprintf(titleBuffer, sizeof(titleBuffer), "%s tokens | $%.0f | %s", formatMillions(windowSummary.totalTokens).c_str(), windowSummary.totalCostUsd, viewModeLabel(mode));
  epaper.drawString(String(titleBuffer), margin, 14);

  // Legend is reflowed to fit the left zone.
  drawLegendItem(epaper, margin, 30, TFT_WHITE, "Cursor", true);
  drawLegendItem(epaper, margin + 70, 30, TFT_GREEN, "GLM");
  drawLegendItem(epaper, margin + 140, 30, TFT_RED, "Claude");
  drawLegendItemStriped(epaper, margin, 46, TFT_WHITE, "Gemini");
  drawLegendItem(epaper, margin + 70, 46, TFT_YELLOW, "GPT");
  drawLegendItem(epaper, margin + 140, 46, TFT_BLUE, "DeepSeek");
  drawLegendItem(epaper, margin + 210, 46, TFT_BLACK, "Other");

  epaper.setTextSize(1);
  epaper.drawString("AI Active Time total: " + formatHours(windowSummary.totalAiHours), margin, 62);
  char batteryBuffer[48];
  snprintf(batteryBuffer, sizeof(batteryBuffer), "Battery: %d%% (%.2fV)", battery.percentage, battery.voltage);
  epaper.drawString(String(batteryBuffer), 610, 456);

  ChartRect stackedRect{kChartX, 78, kLeftWidth, 214};
  ChartRect hoursRect{kChartX, 336, kLeftWidth, 88};

  drawStackedChart(epaper, data, stackedRect, startIndex, count, mode);
  drawHoursChart(epaper, data, hoursRect, startIndex, count, mode);
  drawQuotaPanel(epaper, data, kQuotaX, 78, kQuotaW);

  epaper.setTextSize(1);
  epaper.drawString("Updated: " + data.generatedAt + " , " + autoUpdateLabel(), margin, 456);
  epaper.update();
}

inline void showError(EPaper& epaper, const String& title, const String& detail, int margin) {
  epaper.fillScreen(TFT_WHITE);
  epaper.setTextColor(TFT_BLACK, TFT_WHITE);
  epaper.setTextSize(2);
  epaper.drawString(title, margin, 160);
  epaper.drawString(detail, margin, 190);
  epaper.update();
  delay(5000);
}
