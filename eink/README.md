# ai_usage_dashboard/eink

`eink/` 是 AI Usage Dashboard 的电子纸显示 reference implementation。它不是主项目安装的必需部分。

当前实现针对 **Seeed Studio reTerminal E1002**（800×480 彩色 e-paper）。绝大多数用户可以忽略本目录；只有想把 dashboard 显示到这块硬件上时才需要继续阅读。

当前 POC 路线：

- 服务端生成 `token_usage_eink.json`
- 本地 FastAPI 服务将该数据通过局域网 HTTP 返回给设备端
- 设备端通过局域网请求该 JSON 并本地原生绘制

当前已包含 E1002 Arduino POC sketch。

## 文件说明

- `docs/prd.md`：产品目标、范围、成功标准
- `docs/rfc.md`：技术边界、系统关系、最小实现路径
- `docs/poc.md`：reTerminal E1002 刷机、库选择、部署方式、POC 验证路径
- `e1002/`：设备端 Arduino sketch 与说明

## 当前结论

- 目标设备：Seeed Studio reTerminal E1002（800×480 彩色 e-paper）
- 技术路线：本机运行 FastAPI，设备通过局域网 HTTP 请求结构化数据（JSON），在设备端原生绘制
- 当前判断：可行，但属于可选硬件参考实现，不是普通用户的默认使用路径

后续是否进入实现，以 PRD / RFC 审阅结论为准。
