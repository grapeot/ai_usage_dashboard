# Antigravity IDE Token Parsing — Probing Experiment

Date: 2026-06-29
Goal: 了解 Google Antigravity IDE 如何记录和计算 token 用量，评估能否接入 `ai_usage_dashboard`。

## 现有 Dashboard 的 parse 模式（对照基线）

`auto_usage.py` 对每个 provider 用同一套范式：`export_*` 拉原始数据 → `load_*` 解析成 `{date: token_count}` → 按 provider bucket 汇总。数据来源分三类：

1. 本地日志/DB 解析 — Claude Code（`~/.claude/projects/**/*.jsonl` 的 `usage` 字段）、OpenCode（`~/.local/share/opencode/opencode.db` 的 `tokens` 字段）、Codex（`~/.codex/sessions/**/*.jsonl` 的 `token_count` event）。逐次 assistant turn 都带 input/output/cache_read/cache_write，本地可完整重建 token 数。
2. 官方 API — Cursor（`cursor.com/api/dashboard/get-filtered-usage-events`，带 inputTokens/outputTokens）、GLM/Z.ai（`api.z.ai/api/monitor/usage/model-usage`，按天总量）、Ollama（HTML 解析）。需要 cookie/bearer。
3. Quota API（百分比，非 token 数）— Codex wham/usage、Claude OAuth usage、GLM quota、Ollama settings。

关键：能 parse 出 token 数的 provider，本地一定存了逐次 turn 的 usage 字段。

## Antigravity 架构（探测结果）

### 进程与数据目录

Antigravity IDE 是 VSCode fork（Electron）。核心是 Language Server（LS）二进制 `/Applications/Antigravity IDE.app/Contents/Resources/app/extensions/antigravity/bin/language_server_macos_arm`（Go 写的 protobuf+gRPC 服务）。

LS 启动参数（来自 `ls-main.log`）：
```
--app_data_dir antigravity-ide --subclient_type ide
--cloud_code_endpoint https://cloudcode-pa.googleapis.com
```

真实数据目录（`--app_data_dir` 解析后）：`~/.gemini/antigravity-ide/`。不是 `~/.antigravity-ide/`（那是扩展安装目录），也不是 `~/Library/Application Support/Antigravity IDE/`（那是 Electron 的 VSCode user data）。

```
~/.gemini/antigravity-ide/
  conversations/         # 每次 agent 会话（trajectory）
    <uuid>.db             # SQLite trajectory store（当前活跃）
    <uuid>.pb             # protobuf 副本（旧会话）
  brain/<uuid>/          # artifacts：task.md, implementation_plan.md, screenshots
  browser_recordings/    # agent 浏览器操作的截图帧
  implicit/              # 隐式 trajectory（shell 命令等）
  user_settings.pb
  installation_id
```

### Conversation DB schema（今天的 8a425409）

```sql
CREATE TABLE trajectory_meta(trajectory_id, cascade_id, trajectory_type, source);
CREATE TABLE steps(
  idx, step_type, status, has_subtrajectory,
  metadata BLOB, error_details BLOB, permissions BLOB,
  task_details BLOB, render_info BLOB, step_payload BLOB, step_format
);
CREATE TABLE gen_metadata(idx, data BLOB, size);
CREATE TABLE executor_metadata(idx, data BLOB);
CREATE TABLE trajectory_metadata_blob(id, data BLOB);
```

今天 trajectory 实测：
- 109 steps（user 5, assistant 51, tool_call 12, tool_result 12, plan 1, …）
- 50 gen_metadata 条
- 模型：`gemini-3-flash-a`（display: `Gemini 3.5 Flash (Medium)`）
- 时间跨度可从 step metadata 的 f5.f1（epoch 秒）重建
- tool calls：view_file 12, list_dir 9, grep_search 9, write_to_file 6, search_web 1, run_command 1, list_permissions 1

### Token usage — 本地不存在

这是探测的核心结论：**conversation DB 和 gen_metadata 里没有逐次 token usage 字段**。

- `gen_metadata` 存的是 generation config（max_output_tokens=65536、thinking_budget=50、sessionID、model enum），不是实际 usage。
- `step_payload` 存的是 step 的文本内容和 tool call，没有 input/output token 计数。
- `executor_metadata` 存的是 tokenized context 块（f10 固定 4445 字节，可能是 context window 快照），不含 usage 统计。

Antigravity 的 token 计费完全在 Google 云端完成。LS 通过 gRPC 调用 `cloudcode-pa.googleapis.com/v1internal:recordCodeAssistMetrics` 上报，云端按 "work done"（agent 工作复杂度）计算配额消耗，不是简单的 input+output token 求和。这与 Google 官方博客一致：rate limits "correlated with the amount of work done by the agent, which can differ from prompt to prompt"。

### 本地能拿到的配额信息

`state.vscdb`（`~/Library/Application Support/Antigravity IDE/User/globalStorage/state.vscdb`）里的 `antigravityUnifiedStateSync.userStatus` 是 base64 编码的 protobuf，含当前账户的 model 列表和配额状态。解码后能看到：
- 可用模型（Gemini 3.5 Flash Medium、Claude、GPT-OSS 等）及其 thinking level
- `modelCredits`（availableCreditsSentinelKey、minimumCreditAmountForUsageKey）— 哨兵键，不是真实数字
- 配额百分比 — 在 protobuf 深层，需完整 proto schema 才能可靠提取

这就是 IDE 里 `/credits` 面板和 statusline 显示的来源。

### 第三方代理工具（com.lbjlaq.antigravity-tools）

`~/.antigravity_tools/` 是一个独立的第三方代理（非 Google 官方），自带 `token_stats.db`（schema 含 `token_usage(timestamp, account_email, model, input_tokens, output_tokens, total_tokens)`）和 `proxy_logs.db`。它作为中间代理拦截 Antigravity 的 API 流量来记 token。但本机当前 proxy 是 disabled 的，token_usage 表为空（0 行）。

如果想要逐次 token 数据，这类代理是唯一本地来源——因为它能拦截 LS→cloudcode 的 HTTP 流量。但需要在 Antigravity 里把代理设为 upstream，且这是第三方工具，不是官方能力。

## 接入 ai_usage_dashboard 的可行性

| 路径 | 能拿到的 | 代价 | 评估 |
|---|---|---|---|
| 解析 conversation DB | step 数、模型、时间跨度、tool call 统计 | 中（protobuf 无 schema，靠逆向） | 可做 activity 指标，无 token 数 |
| 解析 userStatus protobuf | 配额百分比（5h/7d） | 高（深层 protobuf，需完整 schema） | 可补 quota 维度，对齐 GLM/Ollama 的现有 quota 桶 |
| 调 cloudcode-pa API | 真实 token usage | 高（需 OAuth token，在 Electron Safe Storage 加密）+ Google 内部 API 不稳定 | 最准但最脆弱 |
| 第三方代理 token_stats.db | 逐次 input/output token | 需启用代理 + 依赖第三方工具 | 可行但非官方 |

## 突破：LS gRPC 暴露逐次 token usage

### 发现过程

`~/.gemini/antigravity-ide/brain/<session>/.system_generated/logs/transcript_full.jsonl` 是完整语义化 transcript（767 行，含每个 step 的 content/tool_calls），但**不含 token usage 字段**。conversation DB 同样不含。

调研发现开源项目 [tokscale](https://github.com/junhoyeo/tokscale) 已支持 Antigravity IDE，方法是 **live RPC against the local language server**——不解析本地文件，而是通过 gRPC 直接问正在运行的 LS。

### tokscale 的方法（`crates/tokscale-cli/src/antigravity.rs`）

1. `ps` 找 `language_server` 进程，从 argv 提取 `--csrf_token` 和 `--extension_server_port`
2. `lsof` 找该 PID 的 TCP LISTEN 端口
3. 用 HTTP/JSON over gRPC（Connect-Protocol-Version: 1）调 `Heartbeat` 探测哪个端口是 LS 的 HTTP gRPC
4. 调 `GetAllCascadeTrajectories` 拿 trajectory 列表（cascadeId、stepCount、lastModified）
5. 对每个 trajectory 调 `GetCascadeTrajectoryGeneratorMetadata`（body: `{"cascadeId": "..."}`）
6. response 的 `generatorMetadata[].chatModel.retryInfos[].usage` 含 `inputTokens`、`outputTokens`、`cacheReadTokens`、`thinkingOutputTokens`、`responseId`
7. 转成 `{"type":"usage","input":N,"output":N,...}` jsonl 行缓存

### 实测结果（2026-06-29）

LS 进程 PID 9111，HTTP gRPC 端口 52500，csrf `a0de2797-...`。

今天 3 个 trajectory：

| trajectory | model | usage entries | input | output | cacheRead | thinking | total |
|---|---|---|---|---|---|---|---|
| 549f4f9d | gemini-3-flash-a | 70 | 394,807 | 33,621 | 3,765,463 | 21,082 | 4,214,973 |
| 8a425409 | gemini-3-flash-a | 339 | 5,123,140 | 372,450 | 52,100,988 | 129,679 | 57,726,257 |
| bd33747b | gemini-3-flash-a | 75 | 548,797 | 39,151 | 7,569,033 | 24,621 | 8,181,602 |
| **合计** | | **484** | **6,066,744** | **445,222** | **63,435,484** | **175,382** | **70,122,832** |

关键观察：cacheRead 占 90%（6340 万 / 7012 万）。这是 agentic 场景的典型特征——每步都带完整 context，context 命中 cache 的部分按 cacheRead 计。input 只占 8.6%，thinking 占 0.25%，output 占 0.63%。这验证了之前"agentic 真实消耗主要在 input/context"的判断，但实际数字比预估更极端：不是 input 占大头，而是 cacheRead 占大头（Gemini 的 cache read 价格只有 input 的 1/4，所以实际成本没有 token 数看起来那么吓人）。

### 对之前"用 tokenizer 数"方案的修正

tokenizer 数 transcript 的方案确实不可行：transcript 里没有 token usage，而 LS gRPC 能拿到 provider 返回的真实 usage（含 cacheRead 这种 tokenizer 根本推不出来的字段）。**不需要 tokenizer 估算，有精确数据源**。

### 接入 ai_usage_dashboard 的推荐路径

在 `auto_usage.py` 加一个 `load_antigravity()` 函数：
1. `ps` + `lsof` 找 LS 进程的 HTTP gRPC 端口和 csrf_token
2. 调 `GetAllCascadeTrajectories` 拿 trajectory 列表
3. 对每个 trajectory 调 `GetCascadeTrajectoryGeneratorMetadata`，汇总 `retryInfos[].usage` 的 input/output/cacheRead/thinking
4. 用 `chatStartMetadata.createdAt` 或 usage 的 timestamp 归到日期
5. 返回 `{date: total_tokens}` dict，接入现有 Gemini bucket 或新建 Antigravity bucket

限制：只有 LS 在运行时才能拿到数据。LS 关掉后，历史 trajectory 的 usage 丢失（本地文件不存）。需要定期 sync 到本地缓存（tokscale 的做法），或在 dashboard 运行时实时拉取。

### 复现命令

```python
# Python gRPC over HTTP 示例（无需 protoc，纯 HTTP/JSON）
import json, socket
PORT=52500; CSRF="a0de2797-..."  # from ps + lsof
def rpc(method, body):
    payload=json.dumps(body)
    req=f"POST /exa.language_server_pb.LanguageServerService/{method} HTTP/1.1\r\nHost: 127.0.0.1:{PORT}\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\nConnect-Protocol-Version: 1\r\nX-Codeium-Csrf-Token: {CSRF}\r\nConnection: close\r\n\r\n{payload}"
    s=socket.create_connection(("127.0.0.1",PORT),timeout=10); s.sendall(req.encode())
    buf=b""
    while True:
        c=s.recv(262144)
        if not c: break
        buf+=c
    s.close()
    # ... de-chunk + json.loads
# List trajectories
rpc("GetAllCascadeTrajectories", {})
# Get usage for one
rpc("GetCascadeTrajectoryGeneratorMetadata", {"cascadeId":"<uuid>"})
```

## 复现命令

```bash
# 今天活跃的 conversation
ls -lt ~/.gemini/antigravity-ide/conversations/*.db | head

# step 分布
sqlite3 ~/.gemini/antigravity-ide/conversations/<uuid>.db \
  "SELECT step_type, COUNT(*) FROM steps GROUP BY step_type"

# 模型（gen_metadata 解码见 probing 脚本）
sqlite3 ~/.gemini/antigravity-ide/conversations/<uuid>.db \
  "SELECT idx, size FROM gen_metadata ORDER BY idx"
```