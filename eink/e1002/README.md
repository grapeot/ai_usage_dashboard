# E1002 Arduino POC Sketch

这个目录是 Seeed Studio reTerminal E1002 的最小原生绘制 POC sketch。它是 reference implementation，不是 AI Usage Dashboard 的默认安装步骤。

## 文件说明

- `e1002.ino`：主程序
- `driver.h`：Seeed_GFX 的 E1002 屏幕选择开关（`BOARD_SCREEN_COMBO 521`）
- `secrets.h`：本地 Wi‑Fi、dashboard URL、device id 配置（本地文件，不提交）
- `secrets.h.example`：公开占位模板

## Arduino IDE 打开方式

只有在你要编译或刷写 reTerminal E1002 时，才需要复制 `secrets.h.example` 为 `secrets.h` 并填入本地配置。普通命令行使用不需要创建这个文件。

在 Arduino IDE 中直接打开：

- `e1002/e1002.ino`

Arduino 会自动把同目录下的 `driver.h` 和 `secrets.h` 一起参与编译。

## 板子设置

- Board: `ESP32S3 Dev Module`
- PSRAM: `OPI PSRAM` 或 `Enabled`
- Flash Size: `32MB`
- Upload Speed: 先用 `115200`
- USB CDC On Boot: `Enabled`

## 依赖库

- `Seeed_GFX`
- `ArduinoJson`

ESP32 core 自带：

- `WiFi.h`
- `HTTPClient.h`
- `time.h`

## 当前行为

开机后程序会：

1. 初始化 E1002 屏幕
2. 静默连接 Wi‑Fi
3. 访问 `secrets.h` 中配置的本地 dashboard update URL
4. 同步本地时间
5. 读取本地电池电压并估算电量百分比
6. 解析最近 30 天 JSON，并默认以最近 7 天模式绘制 dashboard
7. 进入 light sleep；每 1 小时定时唤醒一次

唤醒源：

- **定时器**：默认每 1 小时唤醒；仅在 08:00–22:00 之间向本地服务请求自动更新
- **物理按钮**：白色按钮切换 7D/30D，绿色按钮触发 force update

## 交互模式

- 默认模式：最近 7 天
- 绿色按钮（GPIO3）：触发一次 force update，请求本地 FastAPI 刷新并返回最新 JSON
- 白色按钮（GPIO4 / GPIO5）：在 7 天 / 30 天模式之间切换，只用当前缓存数据本地重绘
- 任意按钮唤醒后会先发出一个非常短的确认音

设备端通过 RTC 内存保存当前视图状态；white button 切换视图时不会触发网络请求。

## 第一次刷机建议

如果上传不稳定：

1. 把 Upload Speed 先降到 `115200`
2. 重新插 USB 数据线
3. 上传时先按一下设备按键唤醒
4. 如果还不行，按住 `BOOT` 再点 Upload

## 注意事项

- `secrets.h` 含本地 Wi‑Fi 凭证，不建议提交到远端仓库
- 当前标题已简化为 `2.54B tokens | $2258` 这种格式，避免和图例重叠
- 启动阶段默认不显示中间状态页，成功时直接进入最终可视化界面
- 电池百分比来自 `GPIO21 -> GPIO1 ADC` 的电压读取与校准曲线估算，不是 fuel gauge 直读
- 绿色按钮（GPIO3）和两个白色按钮（GPIO4 / GPIO5）都已配置为 light sleep 唤醒源
- 蜂鸣器使用 `GPIO45`，当前采用短促确认音提示按钮唤醒已被接收
- 设备使用 light sleep；自动更新时间窗由同步后的本地时间控制
