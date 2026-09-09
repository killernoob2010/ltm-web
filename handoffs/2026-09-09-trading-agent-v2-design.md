# 交易 Agent V2 设计阶段交接

- 日期：2026-09-09；仅完成设计草案与代码盘点，未开发、运行测试或部署。
- 分支：codex/agent-v2-design-20260909；从 origin/staging e343d765b1e02bf793c5aa942b99cbc5db4ad8db 隔离建立。继续实现前重新 fetch 并核对基线。
- 设计：`docs/superpowers/specs/2026-09-09-trading-agent-v2-upgrade-design.md`。
- 证据与缺口：`docs/superpowers/analysis/2026-09-09-trading-agent-v2-gap-analysis.md`。
- 用户已确认：宏源全部期货期权、最新默认、无标签未分组、未来历史按当时归属、开放证据推论、企微本人首试、联网不带私有数据、DeepSeek 最小必要数据。
- 主要发现：已有持仓/FIFO/盈亏/Greeks 可复用；旧 Agent 仍固定任务；历史查询使用当前归属映射；Greeks 展示与敞口单位不同；全部品种覆盖及统一快照尚需核验。
- 用户已确认两项修订：先补全量有效持仓 Greeks 服务（包含未归类），不以期权台账页面改造为前置；Eval 以 Agent＋Harness 通用能力为主线，兼顾工具正确性与最终答案，业务示例不是固定题库。
- 下一步：用户审阅设计，形成实施计划；保持单主 Agent，不创建子 Agent。不要重新询问已确认范围，不把举例的组别和天气扩展为必建模块。
- 禁止：Production、真实交易、自动改归属、凭据回显、任意 SQL、未经核验的新计算方法自动执行。原工作目录存在其他修改，未触碰。
