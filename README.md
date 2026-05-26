# AI Usage Dashboard

本项目是一个本地优先的 AI token usage dashboard。它可以从 Codex、Claude Code、OpenCode、Cursor、GLM/Z.ai 等数据源汇总 token 用量，按公开 API 价格估算成本，并生成控制台表格、桌面图表和可选的电子纸显示 JSON。

它不是云端监控服务，也不会上传你的 usage 数据。所有原始导出、图表、JSON payload 和本地日志都留在本机，并被 `.gitignore` 排除。

## 安装

```bash
git clone <this-repo> ai_usage_dashboard
cd ai_usage_dashboard
cp .env.example .env
uv venv .venv
uv pip install --python .venv/bin/python -e '.[dev]'
```

`.env` 只用于本地私有配置。首次设置时先确认你实际使用哪些数据源，只配置你有的那一层。

## 初始设置：按数据源分层启用

这个工具不是要求你一次接入所有平台。它按本机已有数据源逐层启用：

- **Codex**：如果你使用 Codex CLI，默认读取本机 Codex session；不需要 API key。
- **Claude Code**：如果你使用 Claude Code，默认读取本机 Claude Code project JSONL；不需要 API key。
- **OpenCode**：如果你使用 OpenCode，默认读取本机 OpenCode SQLite。若你还安装了 `opencode_skill` archive 查询层，可在 `.env` 里设置 `AI_USAGE_OPENCODE_SKILL_PATH`。
- **Cursor**：如果你要统计 Cursor dashboard export，在 `.env` 里设置 `CURSOR_COOKIE`。这是浏览器 cookie，只放私有 `.env`。
- **GLM/Z.ai**：如果你要统计 GLM/Z.ai usage API，在 `.env` 里设置 `GLM_BEARER_TOKEN`。这是 bearer token，只放私有 `.env`。

最小可用配置可以是空 `.env`。这样仍可统计本机能自动发现的数据源；没有凭证的数据源会被跳过或读取已有本地缓存。

当前版本使用各工具的默认本机数据目录。需要非默认路径时，优先通过对应工具自己的配置或 `AI_USAGE_OPENCODE_SKILL_PATH` 这类明确支持的变量接入；不要把个人绝对路径写进公开文档。

## 使用

```bash
# 最近 7 天
.venv/bin/python auto_usage.py -d 7

# 最近 30 天，只生成文本和 E1002 JSON，跳过桌面图
.venv/bin/python auto_usage.py -d 30 --skip-desktop-chart

# 跳过成本估算
.venv/bin/python auto_usage.py -d 7 --no-cost
```

输出包括：

- 控制台表格：每日各平台 token 数、AI Hours、Est. $
- `token_usage_dashboard.png`：桌面版 matplotlib 图表
- `token_usage_eink.json`：E1002 / 本地显示服务使用的结构化 JSON

这些输出都属于本地私有 artifact，默认不提交到 git。

## 本地显示服务

本地 FastAPI 服务可以给局域网中的电子纸设备返回最新 dashboard JSON。

```bash
scripts/ai-usage-service
```

默认地址：

- `http://127.0.0.1:7995/health`
- `http://127.0.0.1:7995/token_usage.json`
- `http://127.0.0.1:7995/api/v1/display/update`

如果需要让局域网设备访问，请通过私有配置或启动脚本指定实际 host。不要把固定内网 IP 写进公开文件。

## 数据来源

- Codex: `npx @ccusage/codex@latest --json`
- Cursor: `cursor.com/api/dashboard/export-usage-events-csv`，需要私有 cookie
- GLM/Z.ai: usage API，需要私有 bearer token
- Claude Code: 本机 Claude Code JSONL session 日志
- OpenCode: 本机 OpenCode SQLite 数据库；如需跨 archive 查询，可配置单独安装的 `opencode_skill`

这些路径和凭证都属于用户本机环境。公开 repo 只描述数据契约，不包含真实数据。

## 电子纸 Reference Implementation

`eink/` 是可选硬件参考实现，不是主项目安装的必需步骤。绝大多数用户只需要命令行表格、桌面图和本地 JSON；可以完全忽略 `eink/`。

当前 reference implementation 针对 **Seeed Studio reTerminal E1002**（800x480 彩色 e-paper）。如果你刚好有这块硬件，可以参考 `eink/README.md` 和 `eink/e1002/README.md`。

硬件配置使用 Arduino sketch 旁边的 `eink/e1002/secrets.h`。这个文件包含 Wi-Fi 和本地服务 URL，不提交。公开模板是 `eink/e1002/secrets.h.example`：

```cpp
constexpr const char* kWifiSsid = "YOUR_WIFI_SSID";
constexpr const char* kWifiPassword = "YOUR_WIFI_PASSWORD";

#define AI_USAGE_DASHBOARD_UPDATE_URL "http://YOUR_LOCAL_HOST:7995/api/v1/display/update"
#define AI_USAGE_DASHBOARD_CACHED_URL "http://YOUR_LOCAL_HOST:7995/token_usage.json"
#define AI_USAGE_DASHBOARD_DEVICE_ID "example-e1002"
```

普通初始化不需要创建 `secrets.h`。只有在你要编译/刷写 reTerminal E1002 sketch 时才需要复制这个 example。

## 给 AI Agent

本项目的 root skill 在：

```text
skills/skill_ai_usage_dashboard.md
```

当用户要求统计 AI usage、估算 token 成本、刷新本地 dashboard 或调试 E1002 JSON contract 时，先读这个 skill，再执行对应命令。全局 workspace 可以只保留一个指向该文件的入口，不需要维护第二份 skill。

## 开发与验证

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"
git check-ignore .env token_usage_eink.json token_usage_dashboard.png usage.json cursor.csv glm.json update.log tmp/example.txt
```

发布前还需要做一轮隐私扫描，覆盖固定内网 IP、个人绝对路径、私有部署域名、旧 workspace 路径和 secret-manager 引用。

更完整的测试策略见 `docs/test.md`。

## Privacy

This repository is designed to be publishable with only fake examples. Real cookies, bearer tokens, Wi-Fi credentials, local usage exports, generated charts, generated JSON payloads, and logs must remain in private ignored files.
