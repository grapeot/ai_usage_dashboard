# RFC：`auto_usage.py` 中的 AI 活跃时间估算实现

## 1. 概述

本文档定义如何在 `auto_usage.py` 中实现 AI 活跃时间累计估算，并与现有 token / cost 输出并列展示。

实现目标：

- 不改变现有 token / cost 语义
- 新增 OpenCode + Codex 的 turn-window cumulative active time
- 在 stdout 和 PNG 中一起展示

## 2. 数据模型

### 2.1 Interval

```python
(start: datetime, end: datetime)
```

约束：

- `end >= start`
- 零长度区间允许保留，但聚合时贡献为 0

说明：

- 第一版实现里 interval 不携带 `source`
- source 级信息在 turn 构造阶段使用，进入每日 union 前统一退化为时间区间 tuple

### 2.2 Daily Active Seconds

```python
dict[date, float]
```

值为当天 merge 后的总秒数。

## 3. OpenCode 算法

### 3.1 读取范围

读取 `message` 表，字段：

- `session_id`
- `time_created`
- `data`

只保留：

- `role in ('user', 'assistant')`

对于 assistant：

- 若 `providerID` 属于 `GLM_PROVIDERS`，跳过

### 3.2 turn 构造

对每个 `session_id`：

1. 按 `time_created` 升序排序
2. 维护 `pending_user_start` 和 `last_assistant_time`
3. 遇到 `user`：
   - 如果当前 pending turn 已经有 assistant 输出，则先收口上一轮
   - 开启新 turn，`pending_user_start = user_time`
   - `last_assistant_time = None`
4. 遇到 `assistant`：
   - 若当前存在 `pending_user_start`，更新 `last_assistant_time = assistant_time`
5. session 结束：
   - 如果 pending turn 有 assistant 输出，则输出 `[pending_user_start, last_assistant_time]`

### 3.3 边界处理

- 连续多个 assistant：都属于同一轮，结束时间取最后一个 assistant
- 连续多个 user：前一轮如果没有 assistant，直接丢弃；否则先收口
- 没有 user 的 assistant：忽略

## 4. Codex 算法

### 4.1 事件选择

从 `.jsonl` 中读取所有事件。

关心两类 `event_msg.payload.type`：

- `user_message`
- `task_complete`

以及必要时使用文件最后一条事件顶层 `timestamp` 做回退。

### 4.2 turn 构造

1. 维护 `pending_user_start`
2. 遇到 `user_message`：
   - 如果已有未关闭 turn，则用当前事件时间或上一事件时间回退关闭旧 turn
   - 开启新 turn
3. 遇到 `task_complete`：
   - 若存在 pending turn，则输出 `[pending_user_start, task_complete_time]`，并关闭
4. 文件结束：
   - 若仍存在 pending turn，则用最后事件时间关闭

### 4.3 为什么不用 `response_item` assistant message

Codex 的事件流里可见消息、reasoning、tool call、task lifecycle 混在一起。

第一版用 `user_message -> task_complete` 更稳，因为它更接近一次 agent 执行生命周期，而不是某条可见文本输出。

## 5. 每日聚合

### 5.1 切天

任何跨天区间都切成多段：

- `[start, midnight)`
- `[midnight, next_midnight)`
- ...

### 5.2 累计

同一天内：

1. 把所有区间按天切分
2. 对每个分片直接累加 `(end - start)`
3. 不做 overlap collapse

### 5.3 输出

得到：

- `daily_active_seconds`
- `daily_active_hours = seconds / 3600`

说明：

- 该指标表示累计劳动量
- 并行 agent 的重叠时间会重复累计

## 6. 代码改动建议

### 6.1 `auto_usage.py` 新增函数

- `load_opencode_turn_intervals(exclude_glm=True)`
- `load_codex_turn_intervals()`
- `split_interval_by_day(start, end)`
- `merge_intervals(intervals)`
- `compute_daily_ai_active_seconds(opencode_intervals, codex_intervals, start_date, end_date)`

### 6.2 `generate_dashboard()` 扩展

新增参数：

- `daily_active_seconds=None`

新增行为：

- stdout 增加 `AI Hours`
- 图像改成 `2 x 1` subplot

## 7. UI/输出设计

### 7.1 表格列

在 `Total` 后、`Est. $` 前插入：

- `AI Hours`

理由：它是另一个核心结果，重要性高于 cost，但低于 token 总量。

### 7.2 图像布局

上图：

- 保持现有 token stacked bar

下图：

- 每日 AI Hours 单柱图（累计值）
- y 轴：`Hours`
- 标题：`AI Active Time (cumulative est.)`

## 8. 测试策略

新增单元测试覆盖：

- interval merge
- 按天切分
- OpenCode turn 构造
- Codex turn 构造

优先采用小样本构造数据，不依赖真实本机数据库。

## 9. 向后兼容

- 不删除现有 token 列
- 不改变 cost 估算逻辑
- Cursor / GLM 仍保留在 token 统计中，只是不进入 AI 活跃时间统计

## 10. 文档改动

- 新增 `docs/prd.md`
- 新增 `docs/rfc.md`
- 更新 `README.md`
- 更新 `docs/WORKING.md`
- 删除 `docs/PRICING_ESTIMATE_DESIGN.md`

## 11. 待确认但不阻塞实现的点

- OpenCode 是否需要后续引入 idle-gap 二次切分
- Codex 是否需要以后支持更细的 assistant 可见输出结束判定

第一版先追求稳定、可解释、可回归验证。
