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
- 禁止：Production、真实交易、自动改归属、凭据回显、任意 SQL、未经核验的新计算方法自动执行。原工作目录存在其他修改，未触碰。
- V2.1 已确认：复用正式 Render ltm-web 的 1 CPU/2 GB、25 美元/月单实例；测试版 Free。控制台资源核查已完成；同实例监督器、依赖、资源保护、期限/队列控制和 PostgreSQL bot 单主锁已完成本地适配，真实运行未验收。
- 新总体设计：`docs/superpowers/specs/2026-09-09-trading-agent-v2-shared-render-design.md`；接续计划：`docs/superpowers/plans/2026-09-09-trading-agent-v2-shared-render-implementation.md`，取代旧 T11 默认另找主机安排。
- 本地证据：目标 Python 回归 220 项、前端回归 38 项通过；运行时包检查、编译和 shell 语法检查通过。真实配置需核对云端，不从本地缺变量推断云端缺失。本人用户名 wangjingze 已确认。
- S4 只读前置已核对：Render Staging `b2a5ac3` 已成功运行；环境变量名称中没有 Agent、DeepSeek、Brave 或企微配置。Supabase `LTM WEB STAGING` 状态健康，但六张 `agent_v2_*` 附表尚未创建；未读取密钥或业务行。
- 下一步：先取得本次迁移前的可恢复 Staging 备份并完成恢复核验，再由受保护环境补齐真实服务配置，执行六张附表迁移和权限回读，随后做真实 DeepSeek/企微联调与共载观察。Agent 默认关闭；未修改云配置、数据、费用或 Production。

### 访问配置接续（2026-09-09）
- 用户最新要求取消逐人用户名白名单；8646ab5 已部署 Staging，默认沿用系统权限，企微首次自行配对。47 项定向测试通过，真实多用户/模型问答仍未验收。
- 五项非密钥云配置已保存回读：DeepSeek 地址/模型、staging 环境名、Agent 与企微两个关闭开关；不设置试点用户名。下一步用户仅填写 API key，我方完成数据库准备后联调；不得把关闭状态当成可用 Agent。

### 接入检查续行
- dd39ee6 已在 Staging Live，修复 Agent 退出重启检测；9 项相关测试通过。用户已保存 DeepSeek key，云端配置名称存在，未读取值/未调用模型。
- 两个浏览器已尝试 GitHub 正常登录 Supabase，当前均停在需填写凭据的 GitHub 表单。用户要求正常登录按钮自行点击；仅真正缺少登录条件才交回。数据库备份/恢复/迁移未完成，六张附表仍缺失。不得把现有非 Staging 的本地连接用于此任务。
