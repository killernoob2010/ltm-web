# Agent 正式验收失败修复接续

- 当前阶段：A/B/C 修复、C 六项返修、字段投影、规划兜底和未引用答案恢复均已完成；最新提交 `1df99c9` 已部署正式服务并 Live。正式管理员真实复测任务 `#143` 已通过管理员验收函数的业务断言，页面恢复 Call/Put 按月份数量；最终交付仍为 `partial`，因为来源没有统一业务时点。
- 工作树/分支：`/Users/wangjingze/.codex/worktrees/447c/轻量化交易管理系统WEB`，`codex/agent-batch-c-six-fixes-20260915-447c`。关键收尾提交：`a3d9c67`（规划兜底）、`c4c93f7`（无引用答案恢复）、`1df99c9`（允许“不联网”内部请求）。
- 已完成：严格规划失败时，仅对当前期权数量、明确 Call/Put、最新快照问题生成服务端兜底；显式“不联网”不再被当成公开请求；执行仍经过内部权限、范围、引用和交付门禁。未引用的模型答案可从已保存的持仓结果恢复带证据的纯文字，但不会放宽时间检查。
- 正式证据：`#143` 的 `runtime_selected=pydantic`；`planning.status=fallback_approved`、`fallback_from=plan_invalid`；两次 `query_positions`、0 次搜索、2 份内部 evidence；scope/metrics/evidence/presentation/rendered_content 通过，time 失败，`coverage=complete`，交付 `partial`。两份结果 `data_as_of=null`，来源为 WH6 采集和日结单混合水位。
- 测试证据：Agent V2 `549 passed`，前端 `17 passed`，`pip check` 和 `git diff --check` 通过。正式操作只读，无数据库迁移、交易事实写入、权限扩大、充值或真实交易。
- 未完成/下一步：若要把原题升为完整交付，先在持仓事实服务补齐同一快照的 `data_as_of` 和来源覆盖冲突规则；再用管理员复测原题及“只保留净买 Call / 净卖 Put”的明确过滤题。不要用 `captured_at` 代替业务时点。完成后再决定恢复 Auto-Deploy 或合并主分支。
- 风险边界：不把 `#143` 页面数字包装成统一时点的财务事实；不读取或保存密码/密钥，不改交易计算或真实交易。正式服务手动部署和 Auto-Deploy 暂停状态需在下一次发布决策时显式处理。
