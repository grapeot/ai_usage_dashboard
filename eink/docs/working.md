# E1002 Working Notes

## Current State

- Added the E1002 native e-paper implementation under `eink/`, including `e1002/e1002.ino`.
- Added `driver.h` with `BOARD_SCREEN_COMBO 521` to select the reTerminal E1002 Seeed_GFX configuration.
- Added `secrets.h.example` and excluded real Wi-Fi credentials from version control.
- Completed the first server JSON to device-native rendering loop.
- Moved the native UI from a summary-card page toward the two-chart layout used by the Python e-paper image.
- Condensed the title to `2.54B tokens | $2258` to avoid overlapping the legend.
- Simplified startup into a quiet path that renders the final visualization directly on success.
- Added battery monitoring via `GPIO21` enable, `GPIO1` ADC voltage read, and official calibration-curve percentage estimation.
- Switched the device to a 1-hour light-sleep wake cycle.
- Added green-button force update and white-button 7D/30D view switching.
- Added a short buzzer confirmation tone for button wake events.

## Lessons

- E1002 is verified with `XIAO_ESP32S3`, ESP32 core `3.3.10`, OPI PSRAM, QIO, and `BOARD_SCREEN_COMBO 521` through Seeed_GFX. This board selection avoids the unknown-board 1MHz SPI fallback.
- Arduino's bundled `ctags 5.8-arduino11` must remain in place. Homebrew `universal-ctags` produces incompatible sketch prototypes; restore the bundled tool and rebuild with `--clean` if it was replaced.
- Real Wi-Fi credentials must stay in local `secrets.h` and out of the repo.
- The first native port should prioritize structural fidelity over building a generic widget system.
- On color e-paper, title, legend, small text, and border density fail first. Remove information before adding features.
