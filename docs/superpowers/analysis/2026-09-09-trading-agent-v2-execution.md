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

- 启动与依赖：`render_start.sh` 进入小型监督器，Web 始终是可用性边界，Agent 仅在 `AGENT_V2_ENABLED=true` 时启动；根 `requirements.txt` 已加入 MCP 2.2.0 与企微 SDK 1.0.2，独立 worker 锁保持可用。
- 资源与执行：Agent 初始启动/异常退出采用有限退避重启；任务总期限、单次调用期限、工具/搜索/模型预算、队列上限及过期终态已实现。无法读取可靠 cgroup 指标时停止取新任务，压力恢复采用低阈值和稳定窗口。
- 云端竞争与默认值：PostgreSQL 使用专用会话 advisory lock 按环境与 bot 单主占用；SQLite 保留本地文件锁。MCP 可选列表参数统一为空列表，避免模型省略参数时进入无效请求。
- 新鲜回归：目标 Python 回归 220 项、前端回归 38 项通过；`pip check`、Python 编译、启动脚本语法和差异检查通过。该证据仍限本地/合成数据，未替代 Render、PostgreSQL、DeepSeek 或企微真实验收。

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

- 发布回读：8646ab5 在 ltm-web-staging 为 Live；登录页、标题、静态脚本 URL 已回读，未进行登录业务验收。完成 AUTH-02 代码/自动化与 CONFIG-02 非密钥配置，下一步用户直接配置 DeepSeek key，我方继续数据库准备和真实联调。

## S4 接入检查续行（2026-09-09）
- 用户已保存 DeepSeek key。Render 测试服务 UI 确认 DEEPSEEK_API_KEY 名称存在，8646ab5 为 Live；不打开密钥值，不据此声称真实 API 调用通过。
- Supabase 工具确认测试项目 ACTIVE_HEALTHY；六张 agent_v2 附表仍不存在。当前受控本地 DATABASE_URL 不匹配测试项目，未用于连接、备份或迁移；不输出连接值。
- S1 修复：发现进程登记使用 Agent 大写、退出检测使用 agent 小写，导致已启动后退出的 worker 不重启。范围限 runtime.py 键名归一化和退出后有界重启行为测试，不改业务、数据库、启用开关或预算。回滚点 d004022。
- 新增测试先复现进程仅启动 1 次未重试，修复后监督器、资源保护、worker 回归 9 passed。真实共载与数据库恢复验证仍未完成。

- dd39ee6 已部署 Staging Live。现有 Supabase 连接器可核查表结构，但没有备份下载能力；浏览器已主动点击 GitHub 登录，在 Chrome 与 Codex 内均落到未填凭据的 GitHub 登录表单，未发现可复用登录会话。没有读取密码、创建新账号、重置凭据或绕过备份要求。当前下一步需恢复正常控制台登录，检查可用备份入口；现有本地连接非 Staging，禁止用于迁移。

## S4 测试库备份、恢复和迁移通过（2026-09-09）
- 使用用户保存在受保护本地文件的连接，先校验测试项目映射，再验证连接成功；未显示连接值。
- 创建 PostgreSQL custom 全库备份，使用导出快照获取 public 106 张表的行数基线；备份大小 57,948,648 字节，记录 SHA-256。恢复到仅本机 Unix socket 可访问的独立 PostgreSQL 17 临时实例，public 全部 106 张业务表行数一致。Supabase 平台管理 schema 已归档，但未在本地重建其托管服务；不把此次核验称作整个 Supabase 平台灾备演练。
- 六张 Agent 辅助表迁移先在恢复库通过，再使用 scripts/migrate_agent_v2.py 的 staging 映射与已验证 receipt 保护执行云端迁移。云端六表 RLS 开启，anon/authenticated 不能读取；wangjingze 的 Agent、交易事实权限及宏源账户解析通过。迁移没有更改既有业务表。
- 恢复库真实数据测试：任务入队、领取和持仓快照保存通过，捕获 20 行持仓汇总、4344 手；这是备份时点的本地副本，不能冒充当前实时持仓。行情提供方为空时浮盈亏明确 unavailable，不使用合成价格。
- 权限、路由、监督器和 worker 定向回归 16 passed；仍没有实际调用 DeepSeek、Brave 或企微，没有开启云端 Agent。真实 DeepSeek 小额测试按项目付费外部操作规则向用户申请最多 1 元已有余额，不充值。
- 本地受保护证据：.runtime/backups/20260909-175221/{full.dump,manifest.json,receipt.json,restore.log}、.runtime/staging-migration-verification.json、.runtime/restored-agent-flow-check.json；均不提交 Git。
- 下一步：取得真实 API 测试费用授权后继续模型联调；企微凭据与消息路径尚未接入。代码版本仍为 dd39ee6，Production 未修改。

## S4 真实 DeepSeek 有限额联调（2026-09-09）
- 用户明确允许本轮使用最多 1 元已有 API 余额，不充值/订阅。通过已登录 Render 页面复制测试服务模型 key，仅存放 .runtime 临时保护文件，不输出、不提交；测试结束删除临时 key。云端 Agent 与企微开关仍关闭。
- 首次真实模型连接成功，响应“连接成功”，用量 13 tokens。随后真实 DeepSeek + loopback MCP + Staging 查询失败；本机至远端的一次完整持仓快照耗时实测 46 秒，超过15秒工具期限。不得归因为云端同实例运行同样耗时，也未直接扩大生产超时。
- 已修复确认问题：MCP 客户端不再继承系统代理；请求超时交由 MCP SDK 管理，HTTP 流不以15秒读取超时取消调用方，AsyncExitStack 统一清理。新增无效代理与慢工具后仍可复用会话的真实本地 MCP 测试。
- 进一步用已核验的测试库恢复副本测试真实模型：成功查询持仓/汇总工具（4344手仅为备份数据），但最终答案仍未验收通过。模型曾使用非法日期模式、返回JSON外文本/顶层多余字段、写入未引用数字；证据门禁正确拦截。
- 已补齐 query_positions 的 MCP 枚举与日期互斥说明、完整 AnswerDraft JSON Schema、JSON-only 响应模式与2048输出token上限，以及日期时刻不误判为业务数字的验证。一次纠错预算保持，验证失败只记录错误类别，不把原始异常/数据库详情发给模型。
- 最新全部 Agent 回归 93 passed，4项依赖弃用警告；这不是最终业务回答通过。最后一次真实答案仍因顶层多余 evidence_refs 等错误被拒绝，不能称作已可用。
- 本轮累计费用按高于已核对官方价格的单价作保守估算，上限约0.621元（包含0.02元连接测试预留）；不是账单精确扣费。预算包装器每次发送前预留输入字节上界与输出上限，最多1元；本轮停止进一步付费调用，剩余额度未使用。
- 下一步先用已保留的脱敏失败结构改进最终答案合同反馈和确定性复测，再评估剩余额度内的实时联调；另需云端同实例时延、实时行情/Greeks、企微和联网验证。Production 未改动，无任何交易动作。

- 发布回读：0504e2f 在 Render Staging 为 Live，网站登录入口及标题正常；Agent仍关闭，未完成登录后的业务问答验收。临时key/原始草稿已删除，本地恢复实例已停止；云端本轮唯一残留过期测试任务已通过既有恢复规则收敛。

## C0 设计收口（2026-09-09）

- 只读核对了 effective facts、Agent facts、公开研究、答案渲染和 44+12 题库，形成 `docs/superpowers/analysis/2026-09-09-trading-agent-v2-c0-design-closeout.md`；本阶段没有调用真实模型、联网、写 Staging/Production 或修改业务代码。
- 来源时点结论：结算快照只有业务观察日，WH6 有单快照 `snapshot_timestamp`，推导持仓混合多个来源；`captured_at` 是读取保存时间，当前 effective-facts 的 `as_of_time` 是查询时钟，不能称为数据截至时间。整体混源时 `data_as_of` 必须保持 `null`，另给分源覆盖和新鲜度。
- 公开来源结论：`search_public`/`read_public` 已有结果引用和父结果，但最终答案仍跳过 URL，且 `store.load_result` 尚未以当前 `task_id` 强制绑定；C1 需新增 `public_search_ref`/`public_read_ref` 解析和任务/父链校验，不能靠关键词黑名单宣称解决推论语义。
- Eval 结论：44 个 regression（37 个 fixture 标签）和 12 个 holdout 只有问题、工具白名单和 oracle 字段，没有数据快照、哈希、工具回放或数值预期；继续保持 definition-only，C1 先冻结脱敏 fixture 和 oracle，再申请真实模型验收。
- C1 最小顺序已在 C0 记录中固定为 provenance、公开引用、Eval fixture、真实 smoke；任何新权限、底层事实算法、秘密或付费边界均停回主 Agent。当前不宣布业务 Agent 可用。

## C1 本地最小实现（2026-09-09）

- 来源观察：effective facts 现在为持仓结果提供 `provenance.source_observations`，区分 `settlement`、`wh6`、`derived` 和 `unknown`，保留业务观察日、WH6 快照采集时间、事实状态和新鲜度；混合来源仍保持 `ToolEnvelope.data_as_of=null`，没有拿查询时间或备份时间补造截至时间。
- 公开证据：答案校验支持 `research_uuid#/sources/index`（搜索摘要）和 `public_read_uuid#/payload/text`（正文读取），拒绝直接 URL；正文引用必须有当前任务内的搜索父结果，当前任务之外的同会话结果不能复用。提示同步了新引用协议。
- 回归：`./.runtime/agent-v2/bin/python -m pytest tests/agent_v2 -q` 为 126 passed、4 个依赖弃用警告；effective facts 与 Agent scope 定向回归 38 passed；compileall 和 `git diff --check` 通过。
- 未做：没有创建真实 Eval fixture、没有调用 DeepSeek/Brave/企微、没有修改 Staging/Production 或数据库迁移。C1-EVAL-FIXTURES 仍需先确定可回放数据与冻结 oracle；C1-REAL-SMOKE 需要另行确认付费 API 和网页验收边界。
