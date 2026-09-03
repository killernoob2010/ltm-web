# 收盘交易复盘 Agent V1 Staging 开发交接

- 日期：2026-09-03
- 状态：正式需求和实施计划已完成；功能开发尚未开始
- 目标：交付合格的 Agent Staging，等待用户后续扩大真实页面验收

## 权威文件

1. 需求、设计、Gate A 与验收清单：`docs/superpowers/specs/2026-09-03-closing-trading-review-agent-staging-requirements.md`
2. 新模型逐任务实施计划：`docs/superpowers/plans/2026-09-03-closing-trading-review-agent-staging-implementation.md`
3. 已完成底座的历史计划：`docs/superpowers/plans/2026-09-03-closing-trading-review-agent-phase0-phase1.md`

接手模型必须完整阅读前两份文件；不要从聊天摘要或旧设计稿重新猜需求。

## 当前事实

- 本交接形成时工作树分支：`codex/closing-review-agent-requirements`。
- 参考基线：`4eca33c`，当时与 `origin/staging` 一致；接手时必须重新核对。
- Phase 1 确定性底座已在 Staging：`backend/app/closing_trading_review.py`。
- 已有只读 API：`GET /api/closing-trading-review/options/daily-summary`。
- 当前定向基线：`tests/test_closing_trading_review.py` 为 11 passed。
- 尚无统一 Agent 页面、会话表、推荐问题、自动结果、DeepSeek Provider 或 Agent 专属权限。
- Production 未授权，真实交易操作始终禁止。

## 已冻结业务结论

- 八类问题、15:05 业务时间、一个对话入口、DeepSeek 语义理解、确定性数字和三类内容已经确认。
- Staging 可使用用户已授权的 2026 年 6、7、8 月整体月结单；原文件不得进入仓库或模型。
- 试点用户仅管理员和两个已明确的测试身份；按用户 ID 去重，其中非管理员账号必须显式授权。
- 同会话保留最近 6 轮上下文；新对话隔离；正文保留 90 天，脱敏任务元数据保留 12 个月。
- DeepSeek 单次 15 秒，最多一次重试，总等待不超过 30 秒。
- 缺数、冲突、补齐、自动回放和过度回答规则以需求文件为准。

## 开发边界

- 允许：本地开发、测试、Staging 备份/表/测试数据/Secret、提交、推送、部署和真实页面验证。
- 不允许：`main`、Production、付费外部调度、扩大用户或业务域、实时 WH6、企业微信和任何交易动作。
- Staging 新表前先执行 `docs/backup_restore.md` 规定的备份。
- 真实 DeepSeek 验收依赖有效的 Staging API Key；无 Key 时不得用 Fake 冒充完成。
- 当前未授权子 Agent；使用一个主开发模型。

## 唯一下一步

接手模型重新核对 Git、Staging、数据库和 DeepSeek 配置后，从配套实施计划 Task 1 开始，按测试先行完成到 Staging；全部开发检查通过但用户尚未亲自扩大测试时，最终状态写“Agent V1 Staging 已交付，等待用户扩大真实页面验收”。
