# Agent 正式验收失败修复接续

- 当前阶段：四个本地修复包已完成并通过本地回归；测试版和正式版均已用 `42a57349f2cca72adfddc96c7a363ef8bb2a5c66` 部署。正式版 `AGENT_V2_PYDANTIC_DISABLED` 已临时设为 `false`，用于已授权的管理员试用验收；尚未完成真实问答验收。
- 工作树/分支：`/Users/wangjingze/.codex/worktrees/447c/轻量化交易管理系统WEB`，`codex/agent-batch-c-six-fixes-20260915-447c`。关键提交依次为 `b8290d4`（请求范围与时间）、`e6dc821`（引用协议与修正反馈）、`78821b0`（源时点与端到端回归）、`b1ebd56` 和 `42a5734`（回执与诊断）。
- 已完成：当前持仓不再接受模型偷偷带入的历史窗口；单日历史持仓转为 `settlement_date`；只要数量时清掉未请求浮盈/总手及比较需求；只看 Put 保留月份等范围。共享引用规则已接入旧提示、SDK 执行和修正系统提示，`invalid_reference` 会带合法片段编号进入一次修正反馈。来源时点缺失标为 `unknown`，有数量仍可核验但最终交付为 `partial`。
- 测试证据：`tests/agent_v2` 共 545 项通过；前端 Node 测试 17 项通过；`pip check` 无坏依赖；`git diff --check` 通过。新增测试使用临时 SQLite、合成 ToolEnvelope 和 Pydantic AI FunctionModel，端到端正文实际解析出合成 `3 手`、`2 手` 与 `2701`，并验证 Call/Put 分开、无搜索、工具串行、授权撤销和只部分交付。
- 未完成/下一步：正式版代码部署和环境启动已确认，但浏览器重连后无法挂接原管理员页签，尚未执行 `runAdminAcceptance`。恢复该页签后复核当前 SHA、管理员身份和旧 worker 状态，再重跑原题及“只看 Put”追问；验收后恢复 Auto-Deploy 并记录最终结论。仍需观察正式数据源能否提供统一 `data_as_of`；没有该证据时不得把正式答案报为完整。
- 风险边界：未修改 DB schema、依赖、交易事实、净额/估值公式、权限或真实交易；不把合成数量当作真实持仓，不复写历史失败答案，不读取或保存密码/密钥。
