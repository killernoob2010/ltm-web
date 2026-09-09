# 交易 Agent V2 接续状态

- 当前日期：2026-09-09。隔离工作区 agent-v2-design-20260909，分支 codex/agent-v2-design-20260909；Staging 已部署代码 0504e2f。正式环境未操作。
- 最新用户要求：正常登录和已有授权的点击自行完成，不提前交回；取消逐个用户名白名单，沿用系统权限。企微需自行绑定系统身份，群聊未实现。
- 已完成：宏源全部期货期权的只读工具与 MCP/Harness 基础代码、同实例监督器；修复 worker 已启动后退出无法重启的问题。最新范围以 shared-render-design 和 technical-contracts 为准。
- 配置：Render ltm-web-staging 的 DeepSeek 地址/模型、staging 环境、用户保存的 API key 均存在；Agent 与企微开关保持 false，不设置试点用户名。本轮用户已授权最多1元真实模型测试，连接成功；模型key曾仅临时供测试进程使用，未回显/提交，现已删除临时副本。
- 数据库：用户提供测试库受保护连接后，20260909-175221 已完成 custom 全库归档；public 106 张业务表在本地 PostgreSQL 恢复且行数匹配。六张 agent_v2 附表已在本地验证后迁移 Staging，RLS/公开角色拒绝与 wangjingze 系统权限回读通过。不能再重复声称缺表或缺测试连接。
- 验证：16 项权限/路由/运行回归通过；恢复库的真实数据入队→领取→持仓快照通过，20 行/4344 手仅是备份时点副本；无行情浮盈亏 unavailable。并未完成实时行情、Greeks、真实模型、企微或24小时运行验收。
- 最新测试：93项Agent回归通过。真实模型+恢复库MCP可读到持仓，但最终回复仍因多余字段/未引用数字被拦截；本机直连云库查询46秒超过15秒工具期限，云端同实例时延未验收。已修复MCP代理/请求超时清理和部分模型合同说明。
- 下一步：先离线复现并修正答案协议的准确错误反馈，再做有预算保护的云端同实例/模型验证。用户本轮最多1元授权持续有效，保守已使用上限0.621元，剩余约0.379元，不得重置预算。企微及搜索凭据仍需接入。
- 保护证据：.runtime/live-agent-budget.json保存累计usage与保守估算，.runtime/live-agent-check.json保存最后失败状态；不包含API key。云端由本轮连接中断遗留的1个过期测试任务已按recover_interrupted规则收敛，不影响其他任务。
- 敏感本地资料：.runtime/staging-backup.env；备份与恢复证据位于 .runtime/backups/20260909-175221。禁止输出、提交或复制到报告；运行时仅加载配置，不回显。
- 关键文档：docs/superpowers/analysis/2026-09-09-trading-agent-v2-execution.md；docs/superpowers/plans/2026-09-09-trading-agent-v2-shared-render-implementation.md；docs/backup_restore.md；版本更新记录.md。
- 边界：只允许 Staging/本地；Production、正式数据、付费新增服务和真实金融交易禁止。原主工作目录有无关修改，不动。数据库回退优先保持 Agent 关闭、保留附表，不自动覆盖云库或删除数据。
