# ai_usage_dashboard/eink - Working Notes

## Changelog

### 2026-03-29

- 建立 `eink/` 子目录下的 E1002 原生电子纸实现入口，新增 `e1002/e1002.ino`
- 增加 `driver.h`，通过 `BOARD_SCREEN_COMBO 521` 固定选择 reTerminal E1002 的 Seeed_GFX 配置
- 增加 `secrets.h.example`，把真实 Wi‑Fi 凭证文件从版本管理中排除
- 完成服务端 JSON → 设备端原生渲染的第一版闭环
- 原生端从摘要卡片页推进到接近 Python 版 `token_usage_epaper.png` 的双图表布局
- 标题收敛为 `2.54B tokens | $2258` 风格，避免与右上角图例重叠
- 启动路径进一步收敛为静默模式，成功时不再显示中间状态页，直接进入最终可视化界面
- 设备端新增电池监测：通过 `GPIO21` 使能、`GPIO1` ADC 读取电压，并按官方校准曲线估算百分比
- 设备端保持 2 小时 deep sleep 唤醒节奏，作为当前默认刷新周期
- 绿色按钮（GPIO3）已接入为 deep sleep 唤醒源，休眠时按下可立即触发一次刷新
- 远端 JSON 已扩展为最近 30 天；设备端默认显示最近 7 天，并可通过白色按钮切换 7 天 / 30 天模式
- 绿色按钮进一步演化为 `Auto-update on/off` 切换开关；开启时按 1 小时自动刷新，关闭时只接受按钮唤醒
- 任意按钮唤醒后会先通过 GPIO45 蜂鸣器给出极短确认音，再继续联网与渲染

## Lessons Learned

- E1002 在 Arduino 下按 `ESP32S3 Dev Module` + `BOARD_SCREEN_COMBO 521` 即可走通 Seeed_GFX 路线
- `Seeed_GFX` 会提示 Unknown board using default SPI settings (1MHz)，当前阶段是 note，不阻塞编译和运行
- 真实 Wi‑Fi 凭证必须留在本地 `secrets.h`，不要提交到 repo
- 首轮原生移植先追求“结构忠实”，不要过早抽象成通用 widget 系统
- 彩色电子纸上最先出问题的是标题、图例、小字和边框密度，优先删信息，不要优先加功能
