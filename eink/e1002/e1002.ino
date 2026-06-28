#include "driver.h"
#if __has_include("secrets.h")
#include "secrets.h"
#endif

#include <ArduinoJson.h>
#include "TFT_eSPI.h"

#include "dashboard_logic.h"
#include "dashboard_network.h"
#include "dashboard_render.h"
#include "dashboard_types.h"

namespace {

#ifndef AI_USAGE_DASHBOARD_UPDATE_URL
#define AI_USAGE_DASHBOARD_UPDATE_URL "http://YOUR_LOCAL_HOST:7995/api/v1/display/update"
#endif

#ifndef AI_USAGE_DASHBOARD_CACHED_URL
#define AI_USAGE_DASHBOARD_CACHED_URL "http://YOUR_LOCAL_HOST:7995/token_usage.json"
#endif

#ifndef AI_USAGE_DASHBOARD_DEVICE_ID
#define AI_USAGE_DASHBOARD_DEVICE_ID "example-e1002"
#endif

#ifndef AI_USAGE_WIFI_SSID
#define AI_USAGE_WIFI_SSID "YOUR_WIFI_SSID"
#endif

#ifndef AI_USAGE_WIFI_PASSWORD
#define AI_USAGE_WIFI_PASSWORD "YOUR_WIFI_PASSWORD"
#endif

constexpr const char* kDashboardUpdateUrl = AI_USAGE_DASHBOARD_UPDATE_URL;
constexpr const char* kDashboardCachedUrl = AI_USAGE_DASHBOARD_CACHED_URL;
constexpr const char* kDeviceId = AI_USAGE_DASHBOARD_DEVICE_ID;
constexpr const char* kWifiSsid = AI_USAGE_WIFI_SSID;
constexpr const char* kWifiPassword = AI_USAGE_WIFI_PASSWORD;
constexpr uint64_t kAutoUpdateOnSleepMicros = 1ULL * 60ULL * 60ULL * 1000000ULL;
constexpr uint32_t kWifiTimeoutMs = 30000;
constexpr uint32_t kHttpTimeoutMs = 60000;
constexpr int kScreenWidth = 800;
constexpr int kScreenHeight = 480;
constexpr int kMargin = 18;
constexpr int kBatteryEnablePin = 21;
constexpr int kBatteryAdcPin = 1;
constexpr int kGreenButtonPin = 3;
constexpr int kWhiteButton1Pin = 4;
constexpr int kWhiteButton2Pin = 5;
constexpr int kBuzzerPin = 45;

RTC_DATA_ATTR int gViewModeState = static_cast<int>(ViewMode::SevenDays);
constexpr const char* kTimeZone = "PST8PDT,M3.2.0,M11.1.0";
constexpr const char* kNtpServer1 = "time.apple.com";
constexpr const char* kNtpServer2 = "pool.ntp.org";

EPaper epaper;
bool gHasLastData = false;

DashboardData gLastData{};

ViewMode currentViewMode() {
  return gViewModeState == static_cast<int>(ViewMode::ThirtyDays) ? ViewMode::ThirtyDays : ViewMode::SevenDays;
}

void toggleViewMode() {
  gViewModeState = currentViewMode() == ViewMode::SevenDays ? static_cast<int>(ViewMode::ThirtyDays) : static_cast<int>(ViewMode::SevenDays);
}

const char* wakeCauseLabel(esp_sleep_wakeup_cause_t cause) {
  switch (cause) {
    case ESP_SLEEP_WAKEUP_EXT0:
      return "EXT0";
    case ESP_SLEEP_WAKEUP_EXT1:
      return "EXT1";
    case ESP_SLEEP_WAKEUP_TIMER:
      return "TIMER";
    case ESP_SLEEP_WAKEUP_TOUCHPAD:
      return "TOUCHPAD";
    case ESP_SLEEP_WAKEUP_ULP:
      return "ULP";
    case ESP_SLEEP_WAKEUP_GPIO:
      return "GPIO";
    case ESP_SLEEP_WAKEUP_UART:
      return "UART";
    default:
      return "OTHER";
  }
}

void prepareButtons() {
  pinMode(kGreenButtonPin, INPUT_PULLUP);
  pinMode(kWhiteButton1Pin, INPUT_PULLUP);
  pinMode(kWhiteButton2Pin, INPUT_PULLUP);
}

void beepConfirm() {
  tone(kBuzzerPin, 800, 60);
  delay(70);
  noTone(kBuzzerPin);
  Serial.println("[buzzer] beep confirm");
}

BatteryStatus readBatteryStatus() {
  BatteryStatus status;
  pinMode(kBatteryEnablePin, OUTPUT);
  digitalWrite(kBatteryEnablePin, HIGH);
  analogReadResolution(12);
  analogSetPinAttenuation(kBatteryAdcPin, ADC_11db);
  delay(10);
  int millivolts = analogReadMilliVolts(kBatteryAdcPin);
  digitalWrite(kBatteryEnablePin, LOW);

  status.voltage = (static_cast<float>(millivolts) / 1000.0f) * 2.0f;
  status.percentage = estimateBatteryPercentage(status.voltage);
  Serial.printf("[battery] raw_mV=%d voltage=%.3f percentage=%d\n", millivolts, status.voltage, status.percentage);
  return status;
}

void goToLightSleep() {
  Serial.printf("[sleep] light sleep: timer=%s buttons={%d,%d,%d}\n",
                "3600 sec",
                kGreenButtonPin,
                kWhiteButton1Pin,
                kWhiteButton2Pin);
  Serial.flush();
  WiFi.disconnect(true, true);
  WiFi.mode(WIFI_OFF);
  epaper.sleep();
  delay(100);
  prepareButtons();
  uint64_t wakeMask = (1ULL << kGreenButtonPin) | (1ULL << kWhiteButton1Pin) | (1ULL << kWhiteButton2Pin);
  esp_sleep_enable_ext1_wakeup(wakeMask, ESP_EXT1_WAKEUP_ANY_LOW);
  esp_sleep_enable_timer_wakeup(kAutoUpdateOnSleepMicros);
  esp_light_sleep_start();
  Serial.println("[wake] resumed from light sleep");
}

void renderCachedDashboardWithError(const String& message) {
  if (!gHasLastData) {
    showError(epaper, "Fetch failed", message, kMargin);
    return;
  }
  epaper.wake();
  BatteryStatus battery = readBatteryStatus();
  renderDashboard(epaper, gLastData, battery, currentViewMode(), kMargin);
  epaper.setTextColor(TFT_BLACK, TFT_WHITE);
  epaper.setTextSize(1);
  epaper.drawString("Warning: stale data", kMargin, 438);
  epaper.update();
  delay(3000);
}

void doFullUpdate(const char* reason) {
  epaper.wake();

  String wifiErrorMessage = "Wi-Fi failed";
  if (!connectWifi(kWifiSsid, kWifiPassword, kWifiTimeoutMs, &wifiErrorMessage)) {
    renderCachedDashboardWithError(wifiErrorMessage);
    return;
  }

  syncLocalClock(kTimeZone, kNtpServer1, kNtpServer2);

  if (String(reason) == "scheduled_hourly" && !shouldFetchForScheduledWake()) {
    Serial.println("[wake] outside auto-update window, skip fetch");
    return;
  }

  DashboardData data{};
  String errorMessage;
  if (!fetchDashboardData(data, errorMessage, reason, currentViewMode(), kDashboardUpdateUrl, kDashboardCachedUrl, kDeviceId, kHttpTimeoutMs)) {
    renderCachedDashboardWithError(errorMessage);
    return;
  }

  gLastData = data;
  gHasLastData = true;
  BatteryStatus battery = readBatteryStatus();
  renderDashboard(epaper, data, battery, currentViewMode(), kMargin);
  delay(3000);
}

void renderCachedDashboard() {
  if (!gHasLastData) {
    doFullUpdate("startup");
    return;
  }
  epaper.wake();
  BatteryStatus battery = readBatteryStatus();
  renderDashboard(epaper, gLastData, battery, currentViewMode(), kMargin);
  delay(3000);
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(300);
  prepareButtons();
  epaper.begin();

  Serial.printf("[boot] active mode=%s\n",
                viewModeLabel(currentViewMode()));

  doFullUpdate("startup");
  goToLightSleep();
}

void loop() {
  delay(200);

  esp_sleep_wakeup_cause_t cause = esp_sleep_get_wakeup_cause();

  if (cause == ESP_SLEEP_WAKEUP_TIMER) {
    Serial.println("[wake] timer trigger");
    doFullUpdate("scheduled_hourly");
  } else if (cause == ESP_SLEEP_WAKEUP_EXT1) {
    uint64_t pins = esp_sleep_get_ext1_wakeup_status();
    bool whiteWake = (pins & (1ULL << kWhiteButton1Pin)) || (pins & (1ULL << kWhiteButton2Pin));
    bool greenWake = (pins & (1ULL << kGreenButtonPin)) != 0;

    if (whiteWake || greenWake) {
      beepConfirm();
    }
    if (whiteWake) {
      ViewMode before = currentViewMode();
      toggleViewMode();
      Serial.printf("[wake] toggled mode %s -> %s\n", viewModeLabel(before), viewModeLabel(currentViewMode()));
      renderCachedDashboard();
    }
    if (greenWake) {
      Serial.println("[wake] force update");
      doFullUpdate("force_button");
    }
  }

  goToLightSleep();
}
