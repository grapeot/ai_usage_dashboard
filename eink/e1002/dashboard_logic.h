#pragma once

#include "dashboard_types.h"

inline const char* viewModeLabel(ViewMode mode) {
  return mode == ViewMode::SevenDays ? "7D" : "30D";
}

inline const char* autoUpdateLabel() {
  return "Auto 08-22";
}

inline size_t displayCount(const DashboardData& data, ViewMode mode) {
  if (mode == ViewMode::ThirtyDays) {
    return data.dailyCount;
  }
  return data.dailyCount > 7 ? 7 : data.dailyCount;
}

inline size_t displayStartIndex(const DashboardData& data, ViewMode mode) {
  size_t count = displayCount(data, mode);
  return data.dailyCount > count ? data.dailyCount - count : 0;
}

inline WindowSummary computeWindowSummary(const DashboardData& data, size_t startIndex, size_t count) {
  WindowSummary summary;
  for (size_t i = startIndex; i < startIndex + count; ++i) {
    summary.totalTokens += data.daily[i].totalTokens;
    summary.totalCostUsd += data.daily[i].costUsd;
    summary.totalAiHours += data.daily[i].aiHours;
  }
  return summary;
}

inline int estimateBatteryPercentage(float voltage) {
  struct BatteryPoint {
    float voltage;
    int percentage;
  };
  constexpr BatteryPoint curve[] = {
      {4.15f, 100}, {3.96f, 90}, {3.91f, 80}, {3.85f, 70}, {3.80f, 60}, {3.75f, 50},
      {3.68f, 40},  {3.58f, 30}, {3.49f, 20}, {3.41f, 10}, {3.30f, 5},  {3.27f, 0},
  };

  if (voltage >= curve[0].voltage) {
    return 100;
  }
  if (voltage <= curve[(sizeof(curve) / sizeof(curve[0])) - 1].voltage) {
    return 0;
  }

  for (size_t i = 0; i < (sizeof(curve) / sizeof(curve[0])) - 1; ++i) {
    const BatteryPoint& upper = curve[i];
    const BatteryPoint& lower = curve[i + 1];
    if (voltage <= upper.voltage && voltage >= lower.voltage) {
      float ratio = (voltage - lower.voltage) / (upper.voltage - lower.voltage);
      return lower.percentage + static_cast<int>(ratio * (upper.percentage - lower.percentage));
    }
  }
  return 0;
}

inline String formatMillions(uint64_t value) {
  char buffer[32];
  if (value >= 1000000000ULL) {
    snprintf(buffer, sizeof(buffer), "%.2fB", static_cast<double>(value) / 1000000000.0);
  } else if (value >= 1000000ULL) {
    snprintf(buffer, sizeof(buffer), "%.1fM", static_cast<double>(value) / 1000000.0);
  } else if (value >= 1000ULL) {
    snprintf(buffer, sizeof(buffer), "%.1fK", static_cast<double>(value) / 1000.0);
  } else {
    snprintf(buffer, sizeof(buffer), "%llu", static_cast<unsigned long long>(value));
  }
  return String(buffer);
}

inline String formatUsd(double value) {
  char buffer[32];
  snprintf(buffer, sizeof(buffer), "$%.2f", value);
  return String(buffer);
}

inline String formatHours(double value) {
  char buffer[32];
  snprintf(buffer, sizeof(buffer), "%.2f h", value);
  return String(buffer);
}

inline String compactDateLabel(const String& isoDate) {
  if (isoDate.length() >= 10) {
    return isoDate.substring(5, 7) + "/" + isoDate.substring(8, 10);
  }
  return isoDate;
}

inline double maxStackValue(const DashboardData& data, size_t startIndex, size_t count) {
  double maxValue = 0.0;
  for (size_t i = startIndex; i < startIndex + count; ++i) {
    double yi = static_cast<double>(data.daily[i].totalTokens) / 1e8;
    if (yi > maxValue) {
      maxValue = yi;
    }
  }
  return maxValue > 0.0 ? maxValue : 1.0;
}

inline double maxHoursValue(const DashboardData& data, size_t startIndex, size_t count) {
  double maxValue = 0.0;
  for (size_t i = startIndex; i < startIndex + count; ++i) {
    if (data.daily[i].aiHours > maxValue) {
      maxValue = data.daily[i].aiHours;
    }
  }
  return maxValue > 0.0 ? maxValue : 1.0;
}

inline int scaledHeight(double value, double maxValue, int chartHeight) {
  if (maxValue <= 0.0) {
    return 0;
  }
  int h = static_cast<int>((value / maxValue) * chartHeight);
  if (h < 0) {
    return 0;
  }
  if (h > chartHeight) {
    return chartHeight;
  }
  return h;
}

// Per-provider quota display color, matching the stacked-chart palette.
inline uint16_t providerColor(const String& provider) {
  if (provider == "glm") {
    return TFT_GREEN;
  }
  if (provider == "codex") {
    return TFT_YELLOW;
  }
  if (provider == "claude") {
    return TFT_RED;
  }
  if (provider == "ollama") {
    return TFT_CYAN;
  }
  if (provider == "claude") {
    return TFT_RED;
  }
  return TFT_BLACK;
}

// Normalize provider names for display: GLM all-caps, others title-cased.
inline String providerDisplayName(const String& provider) {
  if (provider == "glm") {
    return "GLM";
  }
  if (provider.length() > 0) {
    String out = provider;
    out[0] = toupper(out[0]);
    return out;
  }
  return provider;
}

// Reset label: "reset in Xd Yh" or "reset in X.Yh" from an ISO timestamp.
// Requires NTP-synchronized local time. Returns "" only when the timestamp
// is too short to parse. When the reset time is in the past (e.g. the 5h
// window is about to roll over and the device clock is slightly ahead),
// shows "reset in 0.0h" instead of hiding the label.
inline String compactResetLabel(const String& iso) {
  if (iso.length() < 16) {
    return "";
  }
  // Parse the ISO local timestamp into a time_t.
  int year = iso.substring(0, 4).toInt();
  int month = iso.substring(5, 7).toInt();
  int day = iso.substring(8, 10).toInt();
  int hour = iso.substring(11, 13).toInt();
  int minute = iso.substring(14, 16).toInt();
  struct tm tm_parts = {0};
  tm_parts.tm_year = year - 1900;
  tm_parts.tm_mon = month - 1;
  tm_parts.tm_mday = day;
  tm_parts.tm_hour = hour;
  tm_parts.tm_min = minute;
  time_t reset_epoch = mktime(&tm_parts);
  time_t now_epoch = time(nullptr);
  long diff_sec = (long)(reset_epoch - now_epoch);
  if (diff_sec < 0) {
    diff_sec = 0;
  }
  long total_minutes = diff_sec / 60;
  long total_hours = total_minutes / 60;
  long days = total_hours / 24;
  long hours = total_hours % 24;
  String out = "reset in ";
  if (days > 0) {
    out += String(days);
    out += "d ";
    out += String(hours);
    out += "h";
  } else {
    // No days: show hours with one decimal place.
    double precise_hours = (double)total_minutes / 60.0;
    out += String(precise_hours, 1);
    out += "h";
  }
  return out;
}
