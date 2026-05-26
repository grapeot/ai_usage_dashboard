# PRD：AI 活跃时间估算

## 1. 背景

`auto_usage.py` 目前已经能把多平台 token 用量统一汇总成两种输出：

- stdout 表格
- `token_usage_dashboard.png`

它回答的是用了多少 token、折合多少钱，但还不能回答另一个很重要的问题：AI 一共工作了多久。

这里的目标不是估算真实 CPU 计算时间，也不是估算人的注意力时间，而是估算 AI 被触发后持续处理任务的活跃时间窗。

## 2. 目标

在 `auto_usage.py` 中新增 AI 活跃时间估算能力，基于本地已有日志输出：

- 每日 AI 活跃时间（小时）
- 总 AI 活跃时间（小时）
- 新增一张 dashboard 子图，用于展示每日 AI 活跃时间趋势

## 3. 非目标

- 不统计 Cursor
- 不统计 GLM / Cloud Code
- 不估算真实 compute time
- 不做人类工作时间分析
- 不做 session 语义搜索或对话内容回放

## 4. 第一版范围

仅统计两个数据源：

- Codex
- OpenCode（排除 GLM provider）

采用 `turn window`，不采用 `session window`。

## 5. 核心定义

### 5.1 指标名称

英文：`AI Active Time (cumulative est.)`

中文：`AI 活跃时间估算`

### 5.2 什么是 turn window

一轮 turn window 定义为：

- 开始：用户发出一轮任务或消息，AI 开始处理
- 结束：这一轮 AI 完成输出或任务完成

这是一个工作时间窗估算，不是精确 compute time。

### 5.3 为什么不用 session window

session window 会把长时间 idle 一起算进去，偏高。

turn window 更贴近实际：按一轮一轮请求来算，误差更可控。

## 6. 数据源定义

### 6.1 OpenCode

数据源：`~/.local/share/opencode/opencode.db`

依赖字段：

- `message.session_id`
- `message.time_created`
- `message.data.role`
- `message.data.providerID`

第一版规则：

- 只看 `role in ('user', 'assistant')`
- 排除 `providerID` 属于 GLM provider 的 assistant 消息
- 对每个 session 按时间排序
- 每个 user 消息开启一个 pending turn
- 后续 assistant 消息持续延长该 turn
- 下一个 user 消息到来前，如果上一轮已有 assistant 输出，则关闭上一轮
- 文件结束时，如 pending turn 已有 assistant 输出，则关闭

### 6.2 Codex

数据源：`~/.codex/sessions/YYYY/MM/DD/*.jsonl`

依赖事件：

- `event_msg.payload.type = user_message`
- `event_msg.payload.type = task_complete`

第一版规则：

- `user_message` 作为 turn 开始
- 最近一次对应的 `task_complete` 作为 turn 结束
- 若 session 结束时仍无 `task_complete`，则回退到最后事件时间作为结束

## 7. 聚合逻辑

### 7.1 每个 turn 生成一个区间

区间结构：

- `source`
- `start_time`
- `end_time`

### 7.2 按天切分

跨天区间要切分成多段，分别记到对应日期。

### 7.3 做累计求和

同一天内，把所有 OpenCode + Codex turn window 按持续时间直接累计。

也就是说：

- 多个 agent 并行时，时间会重复累计
- 这个指标表示 AI 总劳动量，不表示时间轴覆盖长度

结果：

- 每日总活跃秒数
- 每日总活跃小时数

## 8. 输出要求

### 8.1 stdout

在现有每日 token 表格中新增：

- `AI Hours`

在总计行新增：

- 总 AI Hours

### 8.2 PNG

改成上下两张共享 x 轴的 subplot：

- 上图：现有 token stacked bar
- 下图：每日 AI 活跃时间（小时）

下图第一版使用单柱，表示当日累计 AI 活跃小时数。

## 9. 成功标准

- `python auto_usage.py -d 7` 可以稳定输出 AI Hours
- `token_usage_dashboard.png` 新增第二张子图
- OpenCode + Codex 的 turn window 可以覆盖最近真实数据
- 同一天多 agent 并行时长会重复累计

## 10. 风险与限制

- 这是活跃时间窗估算，不是严格工作时间
- 若同一轮中间存在长时间 idle，仍可能轻微高估
- 多个 agent 并行时会重复累计，因此结果更接近总劳动量而不是 wall clock 覆盖时长
- Codex 单轮若缺失 `task_complete`，需要回退策略
- OpenCode 某些 session 若只有 user 无 assistant，需要跳过

## 11. 版本策略

### V1

- OpenCode + Codex
- turn window
- stdout + PNG

### V2 可选

- 增加 idle gap 切分
- 增加每日窗口数、最长窗口
- 增加 source-level breakdown 报表
