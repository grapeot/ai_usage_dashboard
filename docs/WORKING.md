# Working Notes

## Changelog

### 2026-09-21

- Added `grok-4.7` (xAI's current release) to `pricing_config.py` at $2/$0.50/$6 (fast $4/$1/$12), matching rates verified live against `/v1/language-models`. Kept `grok-4.6` as its own model key rather than replacing it: both 4.6 and 4.7 remain billable in the xAI Text API with identical rates, and historical sessions carry the `grok-4.6` model id. A bare rename would have dropped `grok-4.6` usage through the generic `startswith("grok-4")` fallback to grok-4.3 rates ($1.25/$2.5), under-costing historical Grok by ~40%, and left `cursor-grok-4.6-high` at $0. Restored the 4.6 key, aliases, and substring match alongside the new 4.7 entries; `classify_model_bucket` is version-agnostic (`'grok' in model_id`) so no change was needed there.

### 2026-07-21

- Corrected E1002 Arduino guidance from `ESP32S3 Dev Module` to the hardware-verified `XIAO_ESP32S3` configuration: ESP32 core `3.3.10`, OPI PSRAM, QIO, `BOARD_SCREEN_COMBO 521`, and 115200 upload speed.
- Recorded that Homebrew `universal-ctags` is incompatible with Arduino sketch prototype generation. Restore Arduino's bundled `ctags 5.8-arduino11` and use `--clean` after a replacement.

### 2026-05-25

- Began public-repo scaffold pass.
- Added `.env.example`, `AGENTS.md`, `pyproject.toml`, `scripts/`, and repo-local root skill.
- Moved E1002 local service URL and device ID expectations into private `secrets.h` configuration via the public `secrets.h.example` template.
- Rewrote public README and test docs to remove private workspace paths, fixed LAN IPs, and private deployment assumptions.

## Lessons Learned

- Generated usage files are private artifacts, even when aggregated. Keep `usage.json`, `cursor.csv`, `glm.json`, `token_usage_dashboard.png`, `token_usage_eink.json`, and logs ignored.
- The canonical skill should live inside the project repo. Global workspace skill entries should point to it rather than duplicating content.
- Keep local hardware/network configuration in ignored files. Public firmware should rely on `secrets.h.example` placeholders.
