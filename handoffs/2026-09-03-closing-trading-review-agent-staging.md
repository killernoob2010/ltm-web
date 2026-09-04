# 收盘交易复盘 Agent V1 Staging 开发交接

- 日期：2026-09-03
- 状态：Agent V1 本地实现与回归已完成；Staging 变更前门禁待补齐
- 目标：交付合格的 Agent Staging，等待用户后续扩大真实页面验收

## 权威文件

1. 需求、设计、Gate A 与验收清单：`docs/superpowers/specs/2026-09-03-closing-trading-review-agent-staging-requirements.md`
2. 新模型逐任务实施计划：`docs/superpowers/plans/2026-09-03-closing-trading-review-agent-staging-implementation.md`
3. 已完成底座的历史计划：`docs/superpowers/plans/2026-09-03-closing-trading-review-agent-phase0-phase1.md`

接手模型必须完整阅读前两份文件；不要从聊天摘要或旧设计稿重新猜需求。

## 当前事实

- 当前工作树为隔离的 detached worktree，HEAD 为 `0430bc4`；`origin/staging` 仍为 `4eca33c`，本地 Agent 实现领先 8 个提交，尚未推送。
- 本地回归已通过：Python `833 passed`，Node 前端 `157 passed`，目标后端编译、前端语法和 diff 检查均通过。
- Phase 1 确定性底座已在 Staging：`backend/app/closing_trading_review.py`。
- 已有只读 API：`GET /api/closing-trading-review/options/daily-summary`。
- Agent V1 已增加会话/消息/任务数据边界、DeepSeek 受限意图网关、八类任务编排、自动日常结果、Staging 回放入口、统一 Agent 页面、试点权限和安全回归。
- 通过 Supabase 只读核对确认目标项目为 `LTM WEB STAGING` 且健康；三张新 Agent 表目前尚不存在，未误触发迁移。
- 本机当前没有 Staging `DATABASE_URL`；能找到的项目 `.env` 属于 Production，已明确不使用。
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

## 当前阻塞与下一步

Staging 变更前必须先按 `docs/backup_restore.md` 运行一次 `scripts/backup_database.py --mode all`。当前缺少可用于 `pg_dump` 的 Staging 连接凭据，因此不能安全地备份，也不能推送会触发数据库初始化的部署。需要在受保护环境提供 Staging `DATABASE_URL`（不发送到聊天、不写仓库），或在已具备该变量的 Staging 运维环境执行备份并交回备份位置/校验结果。之后再继续推送、配置仅 Staging 的 Secret、真实页面验收和 2026 年 6—8 月月结单导入。

完成全部开发检查但用户尚未亲自扩大测试时，最终状态写“Agent V1 Staging 已交付，等待用户扩大真实页面验收”。
