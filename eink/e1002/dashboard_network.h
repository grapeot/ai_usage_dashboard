#pragma once

#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <time.h>

#include "dashboard_logic.h"

inline const char* wifiStatusLabel(wl_status_t status) {
  switch (status) {
    case WL_IDLE_STATUS:
      return "IDLE";
    case WL_NO_SSID_AVAIL:
      return "NO_SSID";
    case WL_SCAN_COMPLETED:
      return "SCAN_DONE";
    case WL_CONNECTED:
      return "CONNECTED";
    case WL_CONNECT_FAILED:
      return "CONNECT_FAILED";
    case WL_CONNECTION_LOST:
      return "CONNECTION_LOST";
    case WL_DISCONNECTED:
      return "DISCONNECTED";
    default:
      return "UNKNOWN";
  }
}

inline String httpErrorDetail(HTTPClient& http, const char* operation, const char* url, int httpCode) {
  String detail = String(operation) + " failed code=" + httpCode;
  if (httpCode < 0) {
    detail += " ";
    detail += http.errorToString(httpCode);
  }
  detail += " url=";
  detail += url;
  detail += " wifi=";
  detail += wifiStatusLabel(WiFi.status());
  if (WiFi.status() == WL_CONNECTED) {
    detail += " ip=";
    detail += WiFi.localIP().toString();
    detail += " rssi=";
    detail += WiFi.RSSI();
  }
  return detail;
}

inline bool connectWifi(const char* ssid, const char* password, uint32_t wifiTimeoutMs, String* errorDetail = nullptr) {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(true, true);
  delay(300);

  int matchingNetworks = 0;
  int bestRssi = -127;
  int networkCount = WiFi.scanNetworks();
  for (int i = 0; i < networkCount; ++i) {
    if (WiFi.SSID(i) == String(ssid)) {
      matchingNetworks += 1;
      bestRssi = max(bestRssi, static_cast<int>(WiFi.RSSI(i)));
    }
  }
  Serial.printf("[wifi] scan networks=%d matches=%d bestRssi=%d\n", networkCount, matchingNetworks, bestRssi);

  WiFi.begin(ssid, password);
  Serial.printf("[wifi] connecting to %s\n", ssid);

  uint32_t started = millis();
  while (WiFi.status() != WL_CONNECTED && (millis() - started) < wifiTimeoutMs) {
    delay(500);
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("[wifi] connected ip=%s\n", WiFi.localIP().toString().c_str());
  } else {
    wl_status_t status = WiFi.status();
    if (errorDetail != nullptr) {
      *errorDetail = String("Wi-Fi ") + wifiStatusLabel(status) + " code=" + static_cast<int>(status) + " seen=" + matchingNetworks + " rssi=" + bestRssi;
    }
    Serial.printf("[wifi] connect timeout status=%s code=%d matches=%d bestRssi=%d\n", wifiStatusLabel(status), static_cast<int>(status), matchingNetworks, bestRssi);
  }
  return WiFi.status() == WL_CONNECTED;
}

inline bool syncLocalClock(const char* timeZone, const char* ntpServer1, const char* ntpServer2) {
  setenv("TZ", timeZone, 1);
  tzset();
  configTime(0, 0, ntpServer1, ntpServer2);
  struct tm timeinfo;
  if (!getLocalTime(&timeinfo, 10000)) {
    Serial.println("[time] sync failed");
    return false;
  }
  Serial.printf("[time] local=%04d-%02d-%02d %02d:%02d\n",
                timeinfo.tm_year + 1900,
                timeinfo.tm_mon + 1,
                timeinfo.tm_mday,
                timeinfo.tm_hour,
                timeinfo.tm_min);
  return true;
}

inline bool shouldFetchForScheduledWake() {
  struct tm timeinfo;
  if (!getLocalTime(&timeinfo, 1000)) {
    return false;
  }
  return timeinfo.tm_hour >= 8 && timeinfo.tm_hour <= 22;
}

inline bool parseDashboardPayload(const String& payload, DashboardData& data, String& errorMessage) {
  Serial.printf("[json] payload bytes=%d\n", payload.length());

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, payload);
  if (err) {
    errorMessage = String("JSON ") + err.c_str();
    Serial.printf("[json] parse error=%s\n", err.c_str());
    return false;
  }

  data.generatedAt = doc["meta"]["generated_at"] | "";
  data.startDate = doc["meta"]["start_date"] | "";
  data.endDate = doc["meta"]["end_date"] | "";
  data.totalTokens = doc["summary"]["total_tokens"] | 0ULL;
  data.totalCostUsd = doc["summary"]["total_cost_usd"] | 0.0;
  data.totalAiHours = doc["summary"]["total_ai_hours"] | 0.0;
  data.cursor = doc["summary"]["categories"]["cursor"] | 0ULL;
  data.glm = doc["summary"]["categories"]["glm"] | 0ULL;
  data.gemini = doc["summary"]["categories"]["gemini"] | 0ULL;
  data.claude = doc["summary"]["categories"]["claude"] | 0ULL;
  data.gpt = doc["summary"]["categories"]["gpt_opencode"] | 0ULL;
  data.deepseek = doc["summary"]["categories"]["deepseek"] | 0ULL;
  data.other = doc["summary"]["categories"]["other"] | 0ULL;

  data.dailyCount = 0;
  JsonArray daily = doc["daily"].as<JsonArray>();
  for (JsonObject day : daily) {
    if (data.dailyCount >= kMaxDays) {
      break;
    }
    DailyEntry& entry = data.daily[data.dailyCount++];
    String rawDate = day["date"] | "";
    entry.dateLabel = compactDateLabel(rawDate);
    entry.cursor = day["categories"]["cursor"] | 0ULL;
    entry.glm = day["categories"]["glm"] | 0ULL;
    entry.gemini = day["categories"]["gemini"] | 0ULL;
    entry.claude = day["categories"]["claude"] | 0ULL;
    entry.gpt = day["categories"]["gpt_opencode"] | 0ULL;
    entry.deepseek = day["categories"]["deepseek"] | 0ULL;
    entry.other = day["categories"]["other"] | 0ULL;
    entry.totalTokens = day["total_tokens"] | 0ULL;
    entry.aiHours = day["ai_hours"] | 0.0;
    entry.costUsd = day["cost_usd"] | 0.0;
  }

  data.quotaCount = 0;
  JsonArray quotas = doc["quotas"].as<JsonArray>();
  for (JsonObject q : quotas) {
    if (data.quotaCount >= kMaxQuotas) {
      break;
    }
    QuotaWindow& qw = data.quotas[data.quotaCount++];
    qw.provider = q["provider"] | "";
    qw.label = q["label"] | "";
    qw.percentage = q["percentage"] | 0;
    qw.nextResetTimeMs = q["next_reset_time_ms"] | 0ULL;
    qw.nextResetIso = q["next_reset_iso"] | "";
  }
  Serial.printf("[json] loaded quotaCount=%u dailyCount=%u totalTokens=%llu totalCost=%.2f totalHours=%.2f\n",
                static_cast<unsigned>(data.quotaCount),
                static_cast<unsigned>(data.dailyCount),
                static_cast<unsigned long long>(data.totalTokens),
                data.totalCostUsd,
                data.totalAiHours);
  return true;
}

inline bool fetchCachedDashboardData(DashboardData& data, String& errorMessage, const char* cachedUrl, uint32_t httpTimeoutMs) {
  HTTPClient http;
  http.setConnectTimeout(httpTimeoutMs);
  http.setTimeout(httpTimeoutMs);
  http.setFollowRedirects(HTTPC_FORCE_FOLLOW_REDIRECTS);

  if (!http.begin(cachedUrl)) {
    errorMessage = "HTTP cached begin failed";
    Serial.println("[json] cached http.begin failed");
    return false;
  }

  Serial.printf("[json] GET %s\n", cachedUrl);
  int httpCode = http.GET();
  if (httpCode != HTTP_CODE_OK) {
    errorMessage = httpErrorDetail(http, "HTTP cached GET", cachedUrl, httpCode);
    Serial.printf("[json] cached http error=%s\n", errorMessage.c_str());
    http.end();
    return false;
  }

  String payload = http.getString();
  http.end();
  return parseDashboardPayload(payload, data, errorMessage);
}

inline bool fetchDashboardData(DashboardData& data,
                               String& errorMessage,
                               const char* reason,
                               ViewMode currentMode,
                               const char* updateUrl,
                               const char* cachedUrl,
                               const char* deviceId,
                               uint32_t httpTimeoutMs) {
  HTTPClient http;
  http.setConnectTimeout(httpTimeoutMs);
  http.setTimeout(httpTimeoutMs);
  http.setFollowRedirects(HTTPC_FORCE_FOLLOW_REDIRECTS);

  if (!http.begin(updateUrl)) {
    errorMessage = "HTTP begin failed";
    Serial.println("[json] http.begin failed");
    return false;
  }

  JsonDocument body;
  body["reason"] = reason;
  body["view"] = currentMode == ViewMode::SevenDays ? "7d" : "30d";
  body["device_id"] = deviceId;
  String requestBody;
  serializeJson(body, requestBody);
  http.addHeader("Content-Type", "application/json");

  Serial.printf("[json] POST %s reason=%s\n", updateUrl, reason);
  int httpCode = http.POST(requestBody);
  if (httpCode != HTTP_CODE_OK) {
    errorMessage = httpErrorDetail(http, "HTTP update POST", updateUrl, httpCode);
    Serial.printf("[json] http error=%s\n", errorMessage.c_str());
    http.end();
    Serial.println("[json] falling back to cached GET");
    return fetchCachedDashboardData(data, errorMessage, cachedUrl, httpTimeoutMs);
  }

  String payload = http.getString();
  http.end();
  return parseDashboardPayload(payload, data, errorMessage);
}
