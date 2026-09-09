# 交易 Agent V2 接续状态

- 当前日期：2026-09-09。隔离工作区 agent-v2-design-20260909，分支 codex/agent-v2-design-20260909；Staging 已部署代码 dd39ee6。正式环境未操作。
- 最新用户要求：正常登录和已有授权的点击自行完成，不提前交回；取消逐个用户名白名单，沿用系统权限。企微需自行绑定系统身份，群聊未实现。
- 已完成：宏源全部期货期权的只读工具与 MCP/Harness 基础代码、同实例监督器；修复 worker 已启动后退出无法重启的问题。最新范围以 shared-render-design 和 technical-contracts 为准。
- 配置：Render ltm-web-staging 的 DeepSeek 地址/模型、staging 环境、用户保存的 API key 均存在；Agent 与企微开关保持 false，不设置试点用户名。没有读取模型密钥内容或实际模型调用。
- 数据库：用户提供测试库受保护连接后，20260909-175221 已完成 custom 全库归档；public 106 张业务表在本地 PostgreSQL 恢复且行数匹配。六张 agent_v2 附表已在本地验证后迁移 Staging，RLS/公开角色拒绝与 wangjingze 系统权限回读通过。不能再重复声称缺表或缺测试连接。
- 验证：16 项权限/路由/运行回归通过；恢复库的真实数据入队→领取→持仓快照通过，20 行/4344 手仅是备份时点副本；无行情浮盈亏 unavailable。并未完成实时行情、Greeks、真实模型、企微或24小时运行验收。
- 下一步：已询问用户本轮最多 1 元已有 DeepSeek API 余额的测试授权，不充值；获准后继续真实模型联调。企微及搜索凭据仍需接入。不得把用户保存 key 当作真实 API 测试通过。
- 敏感本地资料：.runtime/staging-backup.env；备份与恢复证据位于 .runtime/backups/20260909-175221。禁止输出、提交或复制到报告；运行时仅加载配置，不回显。
- 关键文档：docs/superpowers/analysis/2026-09-09-trading-agent-v2-execution.md；docs/superpowers/plans/2026-09-09-trading-agent-v2-shared-render-implementation.md；docs/backup_restore.md；版本更新记录.md。
- 边界：只允许 Staging/本地；Production、正式数据、付费新增服务和真实金融交易禁止。原主工作目录有无关修改，不动。数据库回退优先保持 Agent 关闭、保留附表，不自动覆盖云库或删除数据。
