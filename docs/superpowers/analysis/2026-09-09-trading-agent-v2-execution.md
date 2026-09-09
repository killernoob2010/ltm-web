# Agent V2 开发记录

用户已明确批准按完整实施计划开发；本记录不代表上线验收。

- 环境：隔离本地 worktree agent-v2-design-20260909，分支 codex/agent-v2-design-20260909；仅非生产。
- 基线：e343d765b1e02bf793c5aa942b99cbc5db4ad8db；开发前 fetch 后确认无新增 Staging 提交，设计提交到 bff68a0。
- Gate A：D3/T3/R3/C1；范围为已批准 T0–T11，权限、事实计算、外部出口为重点验证项。
- 验收：底层事实/估值回归、账户隔离、真实本地 MCP、持久任务及预算、通用能力 Eval；真实模型和企微另需实际接入证据。
- 排除：生产发布、生产数据、实际交易、期权台账页面重构、群开放、付费购买、其他内部业务模块。
- 回滚：本分支新增实现可回退至 bff68a0；数据库尚无迁移。后续迁移单独备份与验证。
- 当前阶段：T0–T10 本地实现、回归和结构性 Eval，以及 V2.1 S1–S3 的同实例本地适配已完成；S4–S7 需真实 Staging 接入条件。
- 外部前提：用户已确认试点用户名 wangjingze；获准模型/搜索配置和企微沙盒尚未核验，不影响本地实现。
- T11 预检（2026-09-09）：fetch 后 `origin/staging` 仍为 e343d765b1e02bf793c5aa942b99cbc5db4ad8db；当前隔离 shell 未注入 Staging 数据库、DeepSeek、Brave 或企微变量，仓库 `render.yaml` 仍只声明 Web 服务，Agent 由 `render_start.sh` 的同实例监督器按开关启动；未读取 `.env` 或任何密钥值。

## 本地验证进度

- Python 3.12.14 独立环境可运行；MCP 2.2.0、企微 SDK 1.0.2 导入通过。FastAPI 保持 0.115.6，Starlette 解析为 0.41.3，sse-starlette 3.0.3；pip check 通过。
- 补充测试用 httpx 0.28.1（MCP 使用 httpx2，不能替代旧 TestClient 的 httpx）。锁文件含测试依赖；尚未验证 Linux 安装。
- 真实 loopback MCP 初始化/list/call 通过；2.x Python 字段是 protocol_version、structured_content；结构化返回需要可解析的完整类型。
- T1 合同、账户过滤、权限撤销/停用、当前与历史持仓测试以及事实/估值回归均通过；最终 Agent V2 套件 76 项、既有交易模块合并回归共 298 项通过。现有 TestClient 启动含后台初始化噪声，不作为页面验收。
- T2 幂等竞争、任务竞争、快照不变、过期、终态撤令牌、中断不重跑的本地 SQLite 测试通过；Postgres 与真实迁移尚未执行。
- 迁移实现采用单事务建表、RLS 与 grants；当前 Supabase changelog 已读取，新增表默认授权变更不影响显式撤权策略。没有数据库正式数据变更。
- 实施细化：复用旧任务唯一键，但入队任务、消息、V2 run 必须在同一事务提交，因此没有调用会独立提交的旧 claim_user_task；保持其幂等合同与旧接口不变。

## 本地实现进度（T3–T10）

- T3/T4 已完成：事实服务以一致性读物化宏源全量成交、已核验平仓和最新有效持仓；事务外冻结行情，浮盈亏沿用既有实时成交价口径；历史没有同期行情时明确不可用。全量期权 Greeks 接入未归类持仓，风险按标的独立分组，逐项返回覆盖率和 Black76 假设；情景变化与账面浮盈亏分开。
- T5/T6 已完成：工具由单一白名单注册，MCP 仅 loopback 且每次请求重验短期执行凭证；Harness 在模型回合前读取实际 MCP 工具目录，DeepSeek 适配器不持久化思考过程，最终答案必须是 AnswerDraft 并通过证据引用渲染。事实指标和多标的风险分组均支持受控引用路径。
- T7/T8 已完成：公开搜索与正文读取独立隔离，私有词、编号和敏感数值不会发往公开服务；外部正文标记为不可信，不触发工具。任务预算为最多 8 次工具、2 次公开搜索、6 次模型回合和 90 秒，租约、心跳、过期、中断及执行凭证撤销均有本地测试。
- T9 已完成本地边界：网页 V2 能力探测失败自动回退 V1；企微首版只接受已配对本人私聊文本，配对码一次性且不写业务聊天，重复消息不重放终态答案；官方 SDK 连接由独立 worker 管理，加入单主机 bot 锁，SDK 自带断线重连。群、图片、附件和主动群推送仍关闭。
- T10 已完成结构性门禁：回归 44 例、留出 12 例，每项通用能力均有覆盖；确定性套件 regression 与 holdout 均为 hard failures=0、min soft score=10、release_pass=true。该套件验证合同和门禁，不等同真实 DeepSeek/企微答案验收。

## V2.1 同实例本地适配（S1–S3）

- 启动与依赖：`render_start.sh` 进入小型监督器，Web 始终是可用性边界，Agent 仅在 `AGENT_V2_ENABLED=true` 时启动；仓库根 `.python-version` 固定 Python 3.12，根 `requirements.txt` 已加入 MCP 2.2.0 与企微 SDK 1.0.2，独立 worker 锁保持可用。
- 资源与执行：Agent 初始启动/异常退出采用有限退避重启；任务总期限、单次调用期限、工具/搜索/模型预算、队列上限及过期终态已实现。无法读取可靠 cgroup 指标时停止取新任务，压力恢复采用低阈值和稳定窗口。
- 云端竞争与默认值：PostgreSQL 使用专用会话 advisory lock 按环境与 bot 单主占用；SQLite 保留本地文件锁。MCP 可选列表参数统一为空列表，避免模型省略参数时进入无效请求。
- 新鲜回归：目标 Python 回归 220 项、前端回归 38 项通过；`pip check`、Python 编译、启动脚本语法和差异检查通过。该证据仍限本地/合成数据，未替代 Render、PostgreSQL、DeepSeek 或企微真实验收。

## S4 Staging 只读前置核对（2026-09-09）

- Render `ltm-web-staging` 成功运行 `b2a5ac3`；仅检查环境变量名称，不读取或输出任何值。当前没有 `AGENT_V2_*`、DeepSeek、Brave 或企微接入配置，Agent 默认关闭。
- Supabase `LTM WEB STAGING` 项目状态为健康；只读表清单确认 `agent_v2_runs`、`agent_v2_results`、`agent_v2_events`、`agent_v2_wecom_bindings`、`agent_v2_pair_codes`、`agent_v2_execution_grants` 均尚未创建。没有读取业务行，也没有执行迁移。
- 现有历史备份早于本次代码和数据变化，未作为本次迁移前恢复点。六张附表迁移仍需一份当前、可恢复且经过恢复核验的 Staging 备份，以及受保护环境中的真实服务配置。

## 当前未完成与下一步

- 未执行 Staging 数据库迁移、真实 DeepSeek/Brave 请求或企微常驻连接；没有读取或写入任何真实凭据、业务数据或生产环境。迁移脚本默认为 dry-run，`--apply` 仍要求 Staging 映射、备份及恢复核验凭证。
- 需要进入 S4 时，先在 Staging 核对数据库映射并完成六张附表迁移和 RLS/grants 回读，再做固定快照核对、本人企微双向问答、至少一条开放组合问题和一条联网推论，最后再按现有发布流程观察共载。生产、群开放和第三方 Agent 仍不在本轮范围。

## 配置与访问范围修订（2026-09-09，用户已授权）

- AUTH-02 / Gate A：取消必须逐人填写试点用户名，默认沿用系统账号和现有 Agent/交易数据权限；保留可选单用户试点开关供回退。范围仅菜单、V2 Web 入口和工具身份复验，以及对应测试与配置文档，不修改业务计算、用户权限数据或群授权。
- CONFIG-02：仅为 Render ltm-web-staging 添加非密钥基础配置，Agent 与企微开关保持关闭；API key 由用户直接配置，不新增付费服务、不修改 Production。
- 验收：未配置名字时多名有权限用户可进入，各自会话隔离；无权限/停用用户拒绝；显式试点配置仍有效。定向权限、Web、企微回归；云端回读配置名称和已知非密钥值。真实问答仍受数据库迁移和密钥未配置限制。
- 回滚点：07bf339；下一步先复现未配置名字被拒绝，再修复并验证、部署测试版。
- 自动化证据：新增测试先复现未配置用户名时工具拒绝 503、Web 拒绝 404；修复后权限/Web/企微/菜单回归 47 passed，7 项依赖弃用警告；编译及 git diff --check 通过。两个早期测试命令选到 Python 3.9/缺 pytest 的运行时，已切回本项目 .runtime/agent-v2 Python 3.12 完成回归。
- 云配置证据：在测试服务 Environment UI 添加并回读 DEEPSEEK_API_BASE=https://api.deepseek.com、DEEPSEEK_MODEL=deepseek-v4-flash、AGENT_V2_ENV=staging、AGENT_V2_ENABLED=false、AGENT_V2_WECOM_ENABLED=false，使用 Save only；不配置可选试点用户名，不访问现有密钥值。下一次 Staging 部署加载以上配置。
- 发布前检查：AUTH-02 与 CONFIG-02 的修改均在批准范围，权限数据/业务计算/数据库/生产未变更；真实多用户与模型验收仍未完成，不据此开启 Agent。
