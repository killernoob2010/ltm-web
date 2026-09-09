# 交易 Agent V2 接续状态

- 当前日期：2026-09-09。隔离工作区 agent-v2-design-20260909，分支 codex/agent-v2-design-20260909；本地包含 C1-EVAL-FIXTURES 未推送提交，Production 未操作。Staging 仍为此前 0504e2f，Agent/企微开关保持关闭。
- A/B 本地修复已完成：答案校验返回安全 code/path/message；一次有限纠错保留失败草稿但不持久化；修复回合不调用新工具；存储/内部异常不伪装成模型格式错误；内部引用路径统一核验；Eval 分为 definition 与 synthetic offline-behavior。
- 新鲜证据：C1-EVAL-FIXTURES 的 `fixture-replay` 为 5/5；`./.runtime/agent-v2/bin/python -m pytest tests/agent_v2 -q` 为 133 passed、4 个依赖弃用警告；effective facts 与 scope 定向回归 38 passed；题库 definition regression 44/44、holdout 12/12；offline-behavior 8/8 passed；前端行为测试 196/196；完整 Python 测试 1,196 passed、1 failed，失败项已在 origin/staging 基线复现，real_model_evaluated=false、release_readiness=not_evaluated。
- 业务证据边界：以上均为本地合成/受控行为，不能证明真实 DeepSeek 最终问答、当前实时持仓、云端同实例时延、网页登录后业务问答、企微、联网、真实行情/Greeks 或持续运行。
- 已知限制：C1 已实现来源观察和公开引用任务绑定；五个合成 fixture 已冻结并可回放，但仍不替代真实模型/网页/企微验收；旧 44+12 题尚未送入真实模型。
- 默认下一步：若要继续，先明确 C1-REAL-SMOKE 的单轮费用上限；建议最多 3 元覆盖 5 个冻结场景及 3 个同义问法，第一批不启用 Brave/企微。现有 spot ledger 基线失败需单独修复或从本项目发布范围排除；在费用上限和范围明确前不得恢复付费调用、开启 Agent、推送或进入 Production。
- 关键文件：docs/superpowers/plans/2026-09-09-agent-v2-luna-repair-taskbook.md、docs/superpowers/analysis/2026-09-09-trading-agent-v2-execution.md、evals/trading_agent_v2/README.md。
- 禁止：读取或输出 .runtime 凭据；改 Production/正式交易数据；执行任何真实交易或资金操作；把本批离线通过包装成 Agent 已可用。
