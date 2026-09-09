# Agent V2 开发记录

用户已明确批准按完整实施计划开发；本记录不代表上线验收。

- 环境：隔离本地 worktree agent-v2-design-20260909，分支 codex/agent-v2-design-20260909；仅非生产。
- 基线：e343d765b1e02bf793c5aa942b99cbc5db4ad8db；开发前 fetch 后确认无新增 Staging 提交，设计提交到 bff68a0。
- Gate A：D3/T3/R3/C1；范围为已批准 T0–T11，权限、事实计算、外部出口为重点验证项。
- 验收：底层事实/估值回归、账户隔离、真实本地 MCP、持久任务及预算、通用能力 Eval；真实模型和企微另需实际接入证据。
- 排除：生产发布、生产数据、实际交易、期权台账页面重构、群开放、付费购买、其他内部业务模块。
- 回滚：本分支新增实现可回退至 bff68a0；数据库尚无迁移。后续迁移单独备份与验证。
- 当前阶段：T0 独立 Python 3.12 环境与依赖兼容核验；随后 T1 账户边界。
- 外部前提：用户已确认试点用户名 wangjingze；获准模型/搜索配置和企微沙盒尚未核验，不影响本地实现。

## 本地验证进度

- Python 3.12.14 独立环境可运行；MCP 2.2.0、企微 SDK 1.0.2 导入通过。FastAPI 保持 0.115.6，Starlette 解析为 0.41.3，sse-starlette 3.0.3；pip check 通过。
- 补充测试用 httpx 0.28.1（MCP 使用 httpx2，不能替代旧 TestClient 的 httpx）。锁文件含测试依赖；尚未验证 Linux 安装。
- 真实 loopback MCP 初始化/list/call 通过；2.x Python 字段是 protocol_version、structured_content；结构化返回需要可解析的完整类型。
- T1 合同、账户过滤、权限撤销/停用、当前与历史持仓测试以及事实/估值回归 77 项通过（T2 开发前）。旧 Agent 相关回归另已通过，但现有 TestClient 启动含后台初始化噪声，不作为页面验收。
- T2 幂等竞争、任务竞争、快照不变、过期、终态撤令牌、中断不重跑的本地 SQLite 测试通过；Postgres 与真实迁移尚未执行。
- 迁移实现采用单事务建表、RLS 与 grants；当前 Supabase changelog 已读取，新增表默认授权变更不影响显式撤权策略。没有数据库正式数据变更。
- 实施细化：复用旧任务唯一键，但入队任务、消息、V2 run 必须在同一事务提交，因此没有调用会独立提交的旧 claim_user_task；保持其幂等合同与旧接口不变。
