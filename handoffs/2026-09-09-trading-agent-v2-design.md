# 交易 Agent V2 设计阶段交接

- 日期：2026-09-09；已完成业务设计、技术合同、T0–T10 及 V2.1 S1–S3 的本地适配和回归，未执行真实联调或部署。
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
- V2.1 已确认：复用正式 Render ltm-web 的 1 CPU/2 GB、25 美元/月单实例；测试版 Free。控制台资源核查已完成；同实例监督器、依赖、资源保护、期限/队列控制和 PostgreSQL bot 单主锁已完成本地适配，真实运行未验收。
- 新总体设计：`docs/superpowers/specs/2026-09-09-trading-agent-v2-shared-render-design.md`；接续计划：`docs/superpowers/plans/2026-09-09-trading-agent-v2-shared-render-implementation.md`，取代旧 T11 默认另找主机安排。
- 本地证据：目标 Python 回归 220 项、前端回归 38 项通过；运行时包检查、编译和 shell 语法检查通过。真实配置需核对云端，不从本地缺变量推断云端缺失。本人用户名 wangjingze 已确认。
- 下一步：按新版 S4 核对 Staging 映射，完成备份/恢复核验、六张附表迁移、真实 DeepSeek/企微联调和共载观察。Agent 默认关闭；未修改云配置、数据、费用或部署。
- 当前日期：2026-09-09。隔离工作区 agent-v2-design-20260909，分支 codex/agent-v2-design-20260909；本地最新修复提交 5ad33cb，未推送、未部署，Production 未操作。Staging 仍为此前 0504e2f，Agent/企微开关保持关闭。
- A/B 本地修复已完成：答案校验返回安全 code/path/message；一次有限纠错保留失败草稿但不持久化；修复回合不调用新工具；存储/内部异常不伪装成模型格式错误；内部引用路径统一核验；Eval 分为 definition 与 synthetic offline-behavior。
- 新鲜证据：A/B 基线为 121 passed；C1 本地来源观察和公开引用实现后 `./.runtime/agent-v2/bin/python -m pytest tests/agent_v2 -q` 为 126 passed、4 个依赖弃用警告；题库 definition regression 44/44、holdout 12/12；offline-behavior 8/8 passed，real_model_evaluated=false、release_readiness=not_evaluated。
- 业务证据边界：以上均为本地合成/受控行为，不能证明真实 DeepSeek 最终问答、当前实时持仓、云端同实例时延、网页登录后业务问答、企微、联网、真实行情/Greeks 或持续运行。
- 已知限制：C1 已实现来源观察和公开引用任务绑定；跨段落推论仍需固定 Eval/人工判据，旧 44+12 题尚未送入真实模型，也没有可回放 fixture。
- 默认下一步：先确定 C1-EVAL-FIXTURES 的脱敏数据与冻结 oracle；完成后才评估真实 smoke。不得自动恢复付费调用、开启 Agent、推送或进入 Production。
- 关键文件：docs/superpowers/plans/2026-09-09-agent-v2-luna-repair-taskbook.md、docs/superpowers/analysis/2026-09-09-trading-agent-v2-execution.md、evals/trading_agent_v2/README.md。
- 禁止：读取或输出 .runtime 凭据；改 Production/正式交易数据；执行任何真实交易或资金操作；把本批离线通过包装成 Agent 已可用。
