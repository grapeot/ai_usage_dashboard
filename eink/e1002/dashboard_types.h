#pragma once

#include <Arduino.h>

constexpr size_t kMaxDays = 30;
constexpr size_t kMaxQuotas = 12;

enum class ViewMode {
  SevenDays = 0,
  ThirtyDays = 1,
};

struct BatteryStatus {
  float voltage = 0.0f;
  int percentage = -1;
};

struct DailyEntry {
  String dateLabel;
  uint64_t cursor;
  uint64_t glm;
  uint64_t gemini;
  uint64_t claude;
  uint64_t gpt;
  uint64_t deepseek;
  uint64_t grok;
  uint64_t qwen;
  uint64_t other;
  uint64_t totalTokens;
  double aiHours;
  double costUsd;
};

struct QuotaWindow {
  String provider;
  String label;
  int percentage;
  uint64_t nextResetTimeMs;
  String nextResetIso;
};

struct DashboardData {
  String generatedAt;
  String startDate;
  String endDate;
  uint64_t totalTokens;
  double totalCostUsd;
  double totalAiHours;
  uint64_t cursor;
  uint64_t glm;
  uint64_t gemini;
  uint64_t claude;
  uint64_t gpt;
  uint64_t deepseek;
  uint64_t grok;
  uint64_t qwen;
  uint64_t other;
  DailyEntry daily[kMaxDays];
  size_t dailyCount;
  QuotaWindow quotas[kMaxQuotas];
  size_t quotaCount;
};

struct ChartRect {
  int x;
  int y;
  int w;
  int h;
};

struct WindowSummary {
  uint64_t totalTokens = 0;
  double totalCostUsd = 0.0;
  double totalAiHours = 0.0;
};
