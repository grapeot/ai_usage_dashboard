# 开发者代码审查

## 1. 总体结论

这次改动已经把 AI 活跃时间估算真正接进了 `auto_usage.py` 主流程，产品路径是顺的：

- 数据源选择收敛到 `OpenCode + Codex`
- 算法从较粗的 session window 收敛到 turn window
- stdout 和 PNG 统一展示，不是额外旁路脚本

从功能完整性看，这一版已经可用。从工程角度看，也没有明显会立刻导致错误结果的 design flaw。

## 2. 目前实现里做对的地方

### 2.1 数据语义是自洽的

- OpenCode 使用 `user -> 最后一个 assistant`，符合本地 SQLite 数据形态
- Codex 使用 `user_message -> task_complete`，符合 JSONL 事件流形态
- 最终按天切分后直接累计各个 turn 的持续时间，更贴近“总劳动量”这个指标

### 2.2 产品输出是一体的

- stdout 增加 `AI Hours`
- PNG 改成上下两张图，共享日期轴

这比额外再造一个脚本更好，因为用户仍然只需要记住 `python auto_usage.py -d 7`。

### 2.3 单元测试方向是对的

新增测试覆盖了：

- interval merge
- 跨天切分
- OpenCode turn window 构造
- Codex turn window 构造
- 跨 source 的累计聚合

这批测试覆盖的是算法骨架，而不是表面格式，价值比较高。

## 3. 目前最值得注意的设计问题

### 3.1 `auto_usage.py` 继续变大了

这是当前最明显的结构问题。

现在这个文件同时负责：

- 数据导出
- 各 source 解析
- 成本估算
- AI 活跃时间估算
- stdout 排版
- PNG 绘图

短期还能维护，长期会越来越难改。尤其是 active-time 这一块已经形成了独立的领域逻辑，后面如果再加 idle-gap split、窗口数统计、最长连续窗口等功能，继续塞在 `auto_usage.py` 里会明显变脆。

建议后续重构方向：

- `active_time.py`：只放 interval / turn-window 相关逻辑
- `pricing.py` 或继续保留 `pricing_config.py`
- `dashboard.py`：只放 stdout + chart 展示逻辑

这不是当前必须做的事，但已经值得放进下一轮技术债列表。

### 3.2 OpenCode turn 定义仍然是近似值

当前算法是：同一 session 内，每个 user 后面跟到最后一个 assistant。

这个定义比 session window 好很多，但它依然有一个已知问题：如果某一轮里间隔很久才继续输出 assistant，仍然会高估。当前 PRD/RFC 已经明确把这个指标叫成 active time estimate，这个命名是对的。

后续如果要提升精度，最优先的方向不是换 source，而是增加 idle gap split。

### 3.3 Codex 回退逻辑偏保守

当前 Codex 算法里，如果 `user_message` 之后没有 `task_complete`，就回退到最后事件时间。

这个回退策略能保证结果不丢，但它也可能把异常中断 session 算长。这个问题当前不是 bug，因为逻辑已经在 PRD/RFC 中定义过，而且比直接丢失更好。但如果后面你更追求精度，可以考虑把这种 case 单独计数，并在 stdout 或调试日志里暴露出来。

## 4. 可以考虑的 refactor 点

### 4.1 把 interval 相关逻辑抽成小模块

以下函数已经形成一个小而完整的 cohesive 单元：

- `split_interval_by_day`
- `merge_intervals`
- `build_opencode_turn_intervals`
- `build_codex_turn_intervals`
- `compute_daily_ai_active_seconds`

后续完全可以独立到 `active_time.py`。

### 4.2 `generate_dashboard()` 参数已经偏多

现在它同时接收多组 token dict、cost dict、active time dict，函数签名已经开始膨胀。

如果下次还要加字段，我建议改成一个统一的 daily view model，而不是继续往函数参数上叠。

### 4.3 stdout 列格式后面可能要抽象

当前表格还是硬编码列顺序和格式。现在还可接受，因为列数有限。但如果后面再加 `Window Count`、`Longest Window`，建议把列定义抽成结构化配置，而不是继续手写 format string。

## 5. 测试覆盖还缺什么

### 5.1 目前缺少真实样本回归测试

现在测试都是 synthetic sample。它们适合验证算法骨架，但不能防 schema drift。

建议后续补两类 fixture：

- OpenCode message fixture：包含 user/assistant/GLM exclusion
- Codex event fixture：包含 `user_message` / `task_complete` / incomplete turn

### 5.2 目前没有 dashboard 级测试

当前没有测试 stdout 列是否真的插入成功，也没有测试第二张 subplot 是否真的生成。

第一版可以接受，因为这更偏集成验证。但如果未来图表继续变复杂，建议至少补一个 smoke test：

- 调 `generate_dashboard()`
- 确认 PNG 能生成
- 确认函数不抛异常

### 5.3 目前没有覆盖本机真实 DB 缺失场景

例如：

- OpenCode DB 不存在
- Codex session 目录不存在
- 某天没有任何 active interval

这些在代码里做了防御，但还没有专门测试。

## 6. 是否有明显 design flaw

如果把“明显 design flaw”定义成会导致结果失真、结构不可持续或后续无法扩展的问题，我的结论是：

- 没有致命 flaw
- 有明确的结构性技术债，集中在 `auto_usage.py` 过大
- 有已知精度边界，集中在 idle gap 和 Codex incomplete turn fallback

这意味着当前版本适合交付，后续值得继续迭代，但不需要因为架构洁癖推翻这次实现。

## 7. 建议的下一轮优先级

1. 给 active-time 逻辑拆模块
2. 增加 idle-gap split 作为可选模式
3. 增加真实 fixture 回归测试
4. 视需要增加 `Window Count` / `Longest Window`
