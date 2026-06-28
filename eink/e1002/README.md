# reTerminal E1002 Arduino Sketch

This directory contains a minimal native-rendering POC sketch for the Seeed Studio reTerminal E1002. It is a reference implementation, not a required AI Usage Dashboard setup step.

## Files

- `e1002.ino`: main sketch
- `driver.h`: Seeed_GFX E1002 screen selection using `BOARD_SCREEN_COMBO 521`
- `secrets.h`: local Wi-Fi, dashboard URL, and device ID config; local only, ignored by git
- `secrets.h.example`: public placeholder template

## Opening In Arduino IDE

Only copy `secrets.h.example` to `secrets.h` when you intend to compile or flash the reTerminal E1002. Normal command-line usage does not need this file.

Open this file directly in Arduino IDE:

```text
eink/e1002/e1002.ino
```

Arduino will include `driver.h` and `secrets.h` from the same directory.

## Board Settings

- Board: `ESP32S3 Dev Module`
- PSRAM: `OPI PSRAM` or `Enabled`
- Flash Mode: `QIO 80MHz`
- Upload Speed: start with `115200`

## Libraries

Install:

- `Seeed GFX`
- `ArduinoJson`

ESP32 core includes:

- `WiFi`
- `HTTPClient`

## Current Behavior

On boot, the sketch:

1. initializes the E1002 display
2. connects to Wi-Fi silently
3. requests the local dashboard update URL from `secrets.h`
4. synchronizes local time
5. reads local battery voltage and estimates battery percentage
6. parses the latest 30-day JSON and renders the dashboard in 7-day mode by default
7. enters light sleep and wakes every hour

The screen is split into two zones: the left ~550px shows the stacked token bar
chart and the AI Active Hours chart; the right ~220px shows a `Quotas` panel with
one horizontal usage bar per provider window (GLM 5h/weekly/monthly, Codex
5h/weekly). Each bar's filled portion uses the provider's palette color (GLM =
green, Codex = yellow, Claude = red) and the reset time is printed below the bar
as `r MM/DD HH:MM`. When no quota data is present the panel shows `--`.

Wake sources:

- **Timer**: wakes every hour by default; automatic network update happens only between 08:00 and 22:00.
- **Physical buttons**: the white button toggles 7D/30D; the green button forces an update.

## Interaction Model

- Default mode: last 7 days.
- Green button (`GPIO3`): force update from local FastAPI and return the latest JSON.
- White buttons (`GPIO4` / `GPIO5`): toggle 7-day and 30-day views using cached data only.
- Any button wake emits a very short confirmation tone.

The device stores the current view mode in RTC memory. White-button view switching does not make a network request.

## First Flashing Tips

If upload is unstable:

1. Lower Upload Speed to `115200`.
2. Reconnect the USB cable.
3. Wake the device with a button before uploading.
4. If it still fails, hold `BOOT` and click Upload.

## Notes

- `secrets.h` contains local Wi-Fi credentials and must not be committed.
- The title uses a compact format such as `2.54B tokens | $2258` to avoid legend overlap.
- The dashboard layout is split: left zone (x=36..560) for the token and hours charts, right zone (x=575..795) for the quota panel. The legend is reflowed to fit the left zone.
- Startup does not show intermediate status pages; success goes directly to the final visualization.
- Battery percentage is estimated from `GPIO21 -> GPIO1 ADC` voltage readings and calibration, not from a fuel gauge.
- Green and white buttons are configured as light-sleep wake sources.
- The buzzer uses `GPIO45` for short wake confirmation tones.
- The device uses light sleep; the automatic update window is controlled by synchronized local time.
