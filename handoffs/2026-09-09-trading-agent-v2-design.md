# 交易 Agent V2 设计阶段交接

- 日期：2026-09-09；已完成业务设计、技术合同、T0–T10本地实现和回归，未执行真实联调或部署。
- 分支：codex/agent-v2-design-20260909；从 origin/staging e343d765b1e02bf793c5aa942b99cbc5db4ad8db 隔离建立，当前实现尚未推送。
- 设计：`docs/superpowers/specs/2026-09-09-trading-agent-v2-upgrade-design.md`。
- 证据与缺口：`docs/superpowers/analysis/2026-09-09-trading-agent-v2-gap-analysis.md`。
- 用户已确认：宏源全部期货期权、最新默认、无标签未分组、未来历史按当时归属、开放证据推论、企微本人首试、联网不带私有数据、DeepSeek 最小必要数据。
- 主要发现：已有持仓/FIFO/盈亏/Greeks 可复用；旧 Agent 仍固定任务；历史查询使用当前归属映射；Greeks 展示与敞口单位不同；全部品种覆盖及统一快照尚需核验。
- 用户已确认两项修订：先补全量有效持仓 Greeks 服务（包含未归类），不以期权台账页面改造为前置；Eval 以 Agent＋Harness 通用能力为主线，兼顾工具正确性与最终答案，业务示例不是固定题库。
- 技术合同：`docs/superpowers/specs/2026-09-09-trading-agent-v2-technical-contracts.md`；实施计划：`docs/superpowers/plans/2026-09-09-trading-agent-v2-implementation.md`。
- 已完成：独立 Python 3.12 worker 依赖；宏源账户 SQL 范围与实时重核权；事实/持仓快照、全量 Greeks、Black76 情景；白名单工具、loopback MCP、DeepSeek 合同、公开搜索出口、有限 Harness、网页 V2 路由和企微本人私聊适配；持久化、幂等、配对码、单主机 bot 锁与证据引用。
- 本地证据：Agent V2 76 项、既有交易模块合并回归共 298 项、前端 38 项通过；regression 44 例和 holdout 12 例确定性 Eval 均 hard failures=0、min soft score=10、release_pass=true。真实模型/企微未验收。
- 下一步：进入 T11 前先确认 Staging 备份/恢复核验和 DeepSeek、公开搜索、企微配置；执行六张附表迁移、RLS/grants 回读和固定快照业务核对，再做本人企微双向联调。保持单主 Agent，不重问已确认范围。
- 禁止：Production、真实交易、自动改归属、凭据回显、任意 SQL、未经核验的新计算方法自动执行。原工作目录存在其他修改，未触碰。
