# WH6 方案 A：结算单治理采集与统一环境路由整体推进 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. The project keeps one primary Agent by default; do not create a child Agent unless the user explicitly requests delegation. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以 2026 年 6—8 月已生效月结单作为完整基线，从 2026-09-01 起只采集仍需补充的 WH6 每日数据；通过同一客户端、同一接口合同和验证码自动路由到 Staging 或 Production，并以本地、服务端和数据库三层约束保证多电脑上传后仍不重复、不污染正式统计。

**Architecture:** Windows 客户端只读用户选定的 WH6 `Record`，先取得账户级日期策略，再把白名单内记录写入本地队列并上传。验证码包含不可作为权限使用的环境路由标识，客户端据此选择内部服务地址，服务端再校验完整验证码、账户和环境；用户界面不提供环境选择。云端保留设备原始观察层，以服务端重算的规范成交键形成唯一事实层，再按“月结单 > 日结单 > WH6”生成有效业务数据。Staging 和 Production 继续使用彼此隔离的 Supabase 项目。

**Tech Stack:** Python 3.11、FastAPI、Pydantic、SQLite、PostgreSQL/Supabase、PyInstaller、GitHub Actions Windows runner、vanilla JavaScript。

**Spec:** `docs/superpowers/specs/2026-09-04-wh6-settlement-governed-upload-repair-design.md`。本计划记录后续确认的方案 A；当该设计或旧实施计划与本计划冲突时，以本计划第 2 节“冻结口径”为准。

**Plan Status:** 业务口径已确认；待按阶段在 Staging 实施和验收。本文不授权 Production 数据、配置、合并或部署。

---

## 1. 业务结论与当前基线

截至 2026-09-04，本轮已经核验的业务基线如下：

- 2026 年 6、7、8 月月结单都已在 `LTM WEB STAGING` 中生效，文件哈希与本地权威来源一致，批次申报数量与落库数量一致，没有发现月外日期或重复成交身份。
- 因此不需要再次上传 8 月结算单，也不需要把 6—8 月 WH6 缓存重新作为有效业务数据导入。
- Staging 已存在的 6—8 月 WH6 原始观察应保留为审计证据，并统一协调为“已被月结单覆盖”；不得物理删除，也不得继续进入正式统计。
- 首次账户级采集起点固定为 `2026-09-01`。在 9 月月结单尚未生效期间，9 月每日成交应持续采集和上传。
- 9 月完整月结单以后补传并生效时，9 月关闭，正常采集起点自动推进到 10 月 1 日。
- 当前功能分支已实现部分 V2.1 候选能力，但真实 Staging 尚未完整发布日期策略接口；当前 Windows 流水线仍输出 `Setup.exe`，并且客户端仍硬编码 Staging 名称。这些都不能算方案 A 已完成。
- 当前分支落后最新 `origin/staging`；远端仍可能继续前进。正式实施前必须重新 fetch 并同步最新 Staging，避免在旧基线上继续开发。

以上数量和状态是本计划形成时的证据快照。执行迁移和验收时必须重新读取环境，不得把快照数字硬编码到程序。

## 2. 冻结口径

### 2.1 一个程序、一个入口、验证码决定环境

- 用户拿到的最终文件只有一个便携式 `WH6成交采集器.exe`，不是 `Setup.exe`，不要求在目标电脑运行 PowerShell、Python、安装器或开发命令。
- 首次使用只做两件事：选择正确的 WH6 `Record`，输入一次性验证码。
- 用户不选择 Staging 或 Production；验证码生成于哪个环境，就只能绑定哪个环境及其中指定的账户。
- Staging 和 Production 使用同一套 API 路径、请求格式和客户端代码，但数据库仍物理隔离，不能合库。
- 程序可以显示“测试版”或“正式版”的绑定结果供用户核对，但不能把该显示项变成可切换的环境选择器。
- 验证码中的环境前缀或路由声明只用于找到服务端，不构成授权。真正授权仍由对应环境保存的一次性验证码哈希、过期时间、账户绑定和服务端校验决定。

建议的新验证码格式为版本化前缀加高熵随机码，例如 `LTM1-S-...` 和 `LTM1-P-...`。前缀仅用于内部路由；完整验证码必须一次使用、短时有效、服务端只存哈希。旧版无路由前缀验证码不做长期兼容，已有设备令牌则通过配置迁移继续使用。

### 2.2 正向日期白名单，不做全量历史同步

账户级策略至少包含：

```json
{
  "schema_version": 2,
  "environment": "staging",
  "history_start_date": "2026-09-01",
  "upload_ranges": [
    {"range_start": "2026-09-01", "range_end": "2026-09-03"}
  ],
  "closed_ranges": [
    {"month": "2026-06", "range_start": "2026-06-01", "range_end": "2026-06-30"},
    {"month": "2026-07", "range_start": "2026-07-01", "range_end": "2026-07-31"},
    {"month": "2026-08", "range_start": "2026-08-01", "range_end": "2026-08-31"}
  ],
  "current_trade_date": "2026-09-04",
  "minimum_client_version": "0.3.0",
  "policy_revision": "...",
  "generated_at": "..."
}
```

规则固定如下：

1. `history_start_date` 是首次治理起点，本账户固定为 2026-09-01。
2. 当前交易日始终允许只读采集并进入本地待上传队列。
3. 历史交易日只有落入服务端明确返回的 `upload_ranges` 才能进入上传队列。
4. `closed_ranges` 只来自当前环境、当前账户中 `active` 且完整覆盖自然月的月结单。
5. 日结单可以补全和纠错，但不能关闭月份、不能推进起点。
6. 缺月时不能用最大结算日期“一刀切”。已关闭的月份跳过，仍缺失的月份继续允许上传。
7. 日期判断使用规范化后的业务 `trade_date`，不是文件修改时间；时间边界按 `Asia/Shanghai`，所有用户可见时间只显示到秒。
8. 策略在激活、程序启动、历史扫描前获取，并最多缓存 5 分钟。策略不可用时，当前交易日数据可继续本地排队，历史扫描暂停。
9. 服务端在每批写入前再次检查日期策略。即使客户端缓存了旧策略，新月结单刚生效后也不能继续把该月写成有效盘中事实。

三种业务场景必须通过同一算法得到结果：

- 7 月、8 月已月结：若账户起点为 9 月 1 日，则从 9 月开始上传。
- 9 月、10 月一直未上传月结单：9 月和 10 月持续保持开放，不能因进入 11 月而丢失。
- 11 月补传并生效 9 月、10 月月结单：9 月和 10 月关闭，正常起点推进到 11 月 1 日。

如果一个物理缓存文件混合多个交易日，程序可以只读解析该文件以识别记录日期，但白名单外记录不得写入上传队列、不得发送云端、不得进入正式统计。

### 2.3 数据完整不等于把所有缓存全部导入

“上传原始观察”和“导入正式业务事实”是两个不同动作。白名单内 WH6 观察可以及时上传到证据层，但只有完成规范化、去重和可用结算来源协调后，才能成为页面和统计使用的有效事实；尚未取得日结或月结的当月记录必须明确标为临时数据，不能冒充月度最终结果。

完整数据按以下顺序形成：

1. 只采集日期白名单内的原始观察。
2. 本地队列消除同一设备重复扫描产生的重复项。
3. 服务端不信任客户端提交的 `source_event_key`，必须根据已校验字段重算规范成交键。
4. 数据库唯一约束阻止两台电脑并发上传形成两条规范成交事实。
5. 日结单和月结单按来源优先级补全、纠错并留下差异审计。
6. 正式统计和页面只读取最终有效事实，不读取原始观察总量。

因此，原始证据可以多条，规范成交只能一条，最终业务事实也只能有一条当前有效版本。

### 2.4 多电脑去重的三层边界

- 本地层：同一设备按规范化记录内容和本地事件序号幂等入队，重复扫描不重复上传。
- 服务端层：重算规范成交键；有可靠成交编号时优先使用账户、交易日、交易所和成交编号，没有可靠成交编号时使用稳定业务字段加文件内出现序号。
- 数据库层：对账户与规范成交键建立唯一约束，以数据库处理并发竞争；冲突返回逐条结果，不把整批请求判为成功。
- 两笔业务字段完全相同但实际都合法的成交，在没有可靠成交编号时不能粗暴合并；必须保留出现序号或进入“待协调”状态。
- 每台设备的原始观察继续保留，便于证明哪台电脑、哪个文件、何时观察到该记录。

### 2.5 Supabase 数据层与权限

不新建 Supabase 项目。每个现有环境在自己的项目内继续使用三层数据：

- `trading_intraday_fill_observations`：设备原始观察，追加式证据区。
- `trading_intraday_fills`：服务端规范化、跨设备去重后的唯一成交层。
- `trading_intraday_fill_reconciliations`：WH6、日结单、月结单之间的匹配、差异、覆盖和当前有效来源。

权限口径如下：

- Windows 客户端不持有 Supabase URL、数据库密码、`service_role` 或用户登录令牌。
- 设备令牌只允许调用激活后的心跳、日期策略和上传接口；不提供查询业务表、修改既有业务记录、删除、建表或管理接口。
- 原始观察对设备是追加式；协调状态和规范事实只能由受信任服务端处理。
- `anon` 和 `authenticated` 对上述表无直接权限，RLS 必须开启；RLS 与 grants 两项分别验收。
- 服务端需要为了去重、协调和状态流转执行受控写入，但不暴露给客户端。系统不提供任何客户端删除数据库的路径。

### 2.6 结算单优先级与跨期价差

- 字段来源优先级固定为：已生效月结单 200、已生效日结单 100、WH6 0。
- 高优先级来源没有提供的字段不得把低优先级有效字段清空。例如月结单没有成交时刻时，应保留 WH6 的成交时刻及来源标识。
- 生效月结单关闭对应完整自然月，并使该月 WH6 规范事实退出正式统计；原始观察仍保留。
- 未匹配、歧义匹配或字段冲突必须进入异常清单，不得静默选择一方。
- 跨月价差只按一笔组合成交保存，不做复杂拆腿还原；不能可靠识别的格式保留为异常，不猜测、不计入普通期权成交量。

## 3. 本次范围与非目标

### 本次必须完成

- 在最新 Staging 基线上完成方案 A 的代码重构、数据库迁移、数据协调、部署和真实回读。
- 给宏源期货账户设置 `history_start_date=2026-09-01`。
- 让真实 Staging 策略明确返回 6—8 月已关闭、9 月开放。
- 让 Staging 中已有的 6—8 月 WH6 原始观察保留但不参与正式统计。
- 生成并验收一个 Windows x64 便携 EXE，目标电脑不运行安装器或 PowerShell。
- 在多电脑并发条件下证明云端不会产生重复规范成交。
- 持续接收 9 月白名单内每日数据，直到 9 月完整月结单生效。

### 本次明确不做

- 不创建新的 Supabase 项目。
- 不把 Staging 和 Production 数据库合并。
- 不做客户端全量云端数据读取或双向同步。
- 不把 6—8 月所有缓存重新上传。
- 不物理删除 6—8 月已有原始证据。
- 不做复杂跨月价差拆腿、盈亏重建或猜测性匹配。
- 不接入下单、撤单、改单、平仓、行权、进程注入、内存读取、网络截获或 WH6 界面控制。
- 不在本计划授权下修改 Production 数据、环境变量、数据库或部署。

## 4. 当前完成情况与主要困难

| 项目 | 当前状态 | 业务结论 |
| --- | --- | --- |
| 6—8 月月结单 | 已核验 | 三个月均完整生效；8 月不需要补传 |
| 6—8 月 WH6 原始观察 | 已存在一批 Staging 证据 | 应保留并标记月结覆盖，不能作为正式统计重复计数 |
| 月结/日结/WH6 优先级 | 功能分支已有候选实现 | 仍需同步最新 Staging、补方案 A 测试并真实部署验收 |
| 日期策略 | 功能分支只有 `closed_ranges` 候选 | 需升级为含 9 月起点和正向 `upload_ranges` 的策略 V2 |
| 环境选择 | 客户端仍硬编码 Staging | 需改成验证码自动路由，界面不出现环境选择 |
| 多电脑去重 | 已有本地与部分云端幂等 | 服务端仍需重算规范键并用数据库唯一约束验证并发 |
| Windows 交付 | 当前流水线输出 Setup 安装包 | 未满足单个便携 EXE 和真实 Windows 验收 |
| 真实 Staging | 当前公开环境缺少完整策略能力 | 不能把本地测试或分支代码视为已上线 |
| Production | 未实施、未授权 | 只保留未来 Gate B，不在本轮推进 |

当前最关键的困难不是“能否上传”，而是同时保证以下四件事：

1. 只上传应该上传的日期，且补传月结单后边界能自动改变。
2. 多台电脑并发上传仍只有一条规范成交。
3. 原始证据保留，但不会混入结算后的正式业务数据。
4. 同一个 EXE 可以绑定两个环境，而用户不需要理解或配置环境地址。

## 5. AI-SDLC 交付档位与追踪编号

- Delivery profile：D3。涉及一个完整业务模块的客户端、API、数据库和统计入口。
- Testing profile：T3。必须贯穿真实来源、本地队列、API、Supabase、协调结果和 Staging 页面，并增加 Windows 实机验收。
- Risk profile：R3。涉及数据库迁移、设备身份、环境路由和金融数据完整性；Production 另设独立门禁。
- Coordination profile：C2。由一个主 Agent 依次推进；未经用户明确要求不创建子 Agent。
- Impact test scope：`whole_module`。

稳定需求编号：

- `WH6-A-001`：一个客户端与验证码自动环境/账户路由。
- `WH6-A-002`：账户起点、完整月结关闭和缺月不跨越。
- `WH6-A-003`：正向日期白名单与离线失败关闭。
- `WH6-A-004`：本地、服务端、数据库三层去重和原始证据分层。
- `WH6-A-005`：月结、日结、WH6 字段级协调与异常留痕。
- `WH6-A-006`：单个便携 Windows EXE 与 No Setup 验收。
- `WH6-A-007`：最小可验证备份、RLS 和 grants 权限边界。
- `WH6-A-008`：Staging 先验收，Production 独立 Gate B。

## 6. 分阶段推进计划

### Phase 0：同步基线并冻结验收合同

**涉及文件：**

- `docs/superpowers/plans/2026-09-04-wh6-scheme-a-overall-rollout-plan.md`
- `docs/superpowers/specs/2026-09-04-wh6-settlement-governed-upload-repair-design.md`
- `AI_SDLC_PROJECT.md`

- [ ] 记录当前功能分支 HEAD、最新 `origin/staging` 和环境映射。
- [ ] 将当前功能分支同步到最新 `origin/staging`，只处理 WH6 相关冲突，不带入无关改动。
- [ ] 把本文 8 个需求编号写入实现任务和测试名称，旧 V2.1 文档的冲突条款标记为被本计划覆盖。
- [ ] 再次只读核验 Staging 的 6、7、8 月月结批次均为 `active monthly`，并确认 9 月月结尚未生效。
- [ ] 若环境映射、账户或月结状态与本计划不同，停止数据步骤并先更新业务基线。

**通过条件：** 新开发基线包含最新 Staging；需求、环境和账户没有歧义；没有 Production 写操作。

### Phase 1：先写方案 A 合同测试

**主要测试文件：**

- `tests/test_wh6_collector_policy.py`
- `tests/test_wh6_collector_cli.py`
- `tests/test_wh6_setup_ui.py`
- `tests/test_trading_collector_api.py`
- `tests/test_trading_collector_service.py`
- `tests/test_trading_collector_reconciliation.py`
- `tests/test_wh6_collector_v2_end_to_end.py`

- [ ] 先新增失败测试：Staging 验证码只能路由 Staging，Production 验证码只能路由 Production，用户不能传入或切换环境。
- [ ] 先新增失败测试：策略起点 2026-09-01、6—8 月关闭、9 月开放。
- [ ] 先新增失败测试：9 月和 10 月缺月时持续开放，11 月补齐后起点推进到 11 月 1 日。
- [ ] 先新增失败测试：9 月缺失但 10 月已关闭时，只开放 9 月及 11 月等未关闭区间，不能跨越 9 月。
- [ ] 先新增失败测试：策略离线时当前日只入本地队列、历史暂停。
- [ ] 先新增失败测试：伪造客户端 `source_event_key` 不影响服务端重算结果。
- [ ] 先新增并发测试：两台设备同时上传同一成交，只产生一条规范事实，两条原始观察均可审计。
- [ ] 先新增测试：相同字段的两笔合法成交在无成交编号时不会被错误合并。

**通过条件：** 新测试在旧实现上明确失败，失败原因分别对应 `WH6-A-001` 至 `WH6-A-005`，不是测试环境故障。

### Phase 2：完成最小数据库准备与一次备份

**主要文件：**

- `supabase/migrations/20260902_wh6_collector.sql`
- `supabase/migrations/20260903_wh6_intraday_fills_positions.sql`
- `supabase/migrations/20260904_wh6_settlement_reconciliation.sql`
- 新增一个方案 A 的向前迁移文件，文件名使用执行日和明确用途。
- `backend/app/db.py`

- [ ] 只对 `LTM WEB STAGING` 做迁移前清单：项目身份、受影响表、迁移版本、关键行数和当前策略状态。
- [ ] 生成一次最小、可恢复、可验证的 Staging 备份。优先使用 Supabase 已批准的备份/PITR；否则保存受影响表数据与 schema 的逻辑快照。
- [ ] 验证备份文件非空、表清单完整、行数清单可读，并至少做一次隔离恢复或结构读取验证。失败的导出不得称为备份成功。
- [ ] 新增账户级采集策略存储，写入宏源账户的 `history_start_date=2026-09-01`；不在代码中硬编码账户主键。
- [ ] 将规范成交唯一键改为服务端计算字段，并建立账户级数据库唯一约束。
- [ ] 保留原始观察唯一约束和协调审计表，不做物理删除迁移。
- [ ] 开启相关表 RLS，并分别撤销 `anon`、`authenticated` 的表和序列直接权限。
- [ ] 为每个迁移写清精确回滚 SQL 或向前修复路径。

**通过条件：** 备份可验证；迁移可重复执行；RLS 与 grants 均符合要求；Staging 原有结算与 WH6 证据数量没有无解释减少。

### Phase 3：重构统一激活、路由和日期策略

**主要文件：**

- `backend/app/trading_collector.py`
- `backend/app/trading_collector_service.py`
- `backend/app/trading_collector_reconciliation.py`
- `collector/wh6_collector/cli.py`
- `collector/wh6_collector/setup_ui.py`
- `collector/wh6_collector/uploader.py`
- `collector/wh6_collector/policy.py`
- `collector/wh6_collector/version.py`

- [ ] 将 `DEFAULT_STAGING_URL`、`staging_url`、`StagingUploader` 等业务命名改为通用 collector 命名。
- [ ] 保持统一 API 路径 `/api/trading-collector/device/*`，不为客户端建立两套请求合同。
- [ ] 生成版本化一次性验证码；验证码绑定环境、账户、有效期和单次使用状态。
- [ ] 客户端只根据验证码路由标识选择内置服务地址，激活后校验服务端返回的环境身份并保存绑定。
- [ ] 现有 Staging 设备配置自动迁移到通用 `collector_url`，有效设备令牌不要求用户重新输入。
- [ ] 将策略升级为 schema v2，返回 `history_start_date`、`upload_ranges`、`closed_ranges`、当前交易日、最低版本和 revision。
- [ ] 服务端按完整自然月月结单动态生成关闭区间，并从起点到当前日压缩生成开放区间。
- [ ] 上传入口逐条复核账户、环境、客户端最低版本和允许日期；白名单外返回明确的 `outside_upload_policy` 或 `settlement_covered`，不能默默接收。
- [ ] 客户端界面只显示绑定结果，不提供环境选择框、地址输入框或 Supabase 配置。

**通过条件：** 一个 EXE 对两类验证码走同一合同；错误环境、过期、重复使用和账户不匹配均失败关闭；6—8 月不能重新成为有效盘中事实。

### Phase 4：完成选择性采集、三层去重和有效事实协调

**主要文件：**

- `collector/wh6_collector/monitor.py`
- `collector/wh6_collector/parser.py`
- `collector/wh6_collector/local_store.py`
- `collector/wh6_collector/migrations.py`
- `backend/app/trading_collector_service.py`
- `backend/app/trading_collector_reconciliation.py`
- `backend/app/trading_management.py`
- `scripts/reconcile_wh6_intraday.py`

- [ ] 历史扫描只把 `upload_ranges` 内记录写入队列；当前交易日保持实时优先。
- [ ] 将旧本地队列、检查点和配置做幂等迁移；迁移前只保留一个本地 SQLite 安全副本，不制造多层备份流程。
- [ ] 客户端事件键仅作为观察证据；服务端从规范字段重算成交键，并校验客户端值是否异常。
- [ ] 数据库冲突使用原子插入/唯一约束解决，不采用“先查再插”的竞态方案。
- [ ] 对可靠成交编号和无成交编号两条路径分别测试，保留合法重复成交的出现序号。
- [ ] 对日结、月结和 WH6 做字段级协调；高优先级空值不覆盖低优先级有效值。
- [ ] 跨期价差仅保存一笔组合；未知格式进入异常。
- [ ] 协调脚本默认 dry-run，输出拟变更数量、覆盖月份、冲突和异常；只有目标、备份和数量均确认后才允许 `--apply`。

**通过条件：** 多设备并发测试通过；重复上传幂等；原始观察可追溯；正式查询只出现一条有效成交；冲突进入异常而不是静默覆盖。

### Phase 5：部署并整理真实 Staging 数据

**主要验证入口：**

- `https://ltm-web-staging.onrender.com/?codex=<commit>`
- `/api/trading-collector/device/collection-policy`
- `/api/trading-collector/device/ingest`
- 采集设备后台和交易管理页面

- [ ] 完成本地 Python、Node、SQLite/PostgreSQL 等价性测试和迁移静态检查。
- [ ] 将功能分支推送并按 Staging 流程部署，记录真实 commit 和服务版本。
- [ ] 用 Staging 页面生成新的带路由验证码，并确认只绑定 `LTM WEB STAGING` 中指定账户。
- [ ] 对已有 6—8 月 WH6 观察先 dry-run，再执行协调：原始观察保留，规范事实标记为月结覆盖，正式统计以月结单为准。
- [ ] 不重新上传 8 月结算单；如重新核验发现 8 月批次失效或不完整，停止并报告，不自动补传。
- [ ] 回读策略：6、7、8 月位于关闭区间，历史起点为 2026-09-01，9 月位于开放区间。
- [ ] 回读正式页面和 API：6—8 月无重复计数，9 月数据能够进入临时有效层，异常清单可见且可追溯。
- [ ] 更新 Staging 发布记录；健康检查、API 200 或数据库写入都不能单独替代真实业务回读。

**通过条件：** 真实 Staging 端到端成立，且 6—8 月结算事实、9 月临时事实、原始观察三者边界清楚。

### Phase 6：生成并验收单个 Windows 便携 EXE

**主要文件：**

- `collector/WH6成交采集器.spec`
- `.github/workflows/build-wh6-windows.yml`
- `collector/launcher.py`
- `collector/installer/build_windows.ps1`（仅供构建流水线使用，目标电脑不运行）
- `docs/superpowers/plans/2026-09-03-wh6-intraday-fills-positions-collector-acceptance-runbook.md`
- 新增 `tests/test_wh6_portable_bundle.py`
- `tests/test_wh6_installer.py`（移除把 Setup 作为目标交付物的断言）

- [ ] 移除最终交付链路对 Inno Setup 和 `Setup.exe` 的依赖，流水线直接发布 PyInstaller 单文件 EXE。
- [ ] 内部流水线可以保留 SHA-256 作为审计证据，但交给用户操作的业务文件只有一个 EXE。
- [ ] 在全新 Windows x64 环境双击运行，不安装 Python、不运行 PowerShell、不要求管理员权限。
- [ ] 不把“关闭 Windows 安全保护”作为用户操作步骤；如果签名或信誉拦截导致标准电脑无法直接运行，作为交付阻断单独报告，任何付费代码签名另行确认。
- [ ] 首次运行只选择 `Record`、核对掩码账户并输入验证码；程序自动显示绑定到测试版还是正式版。
- [ ] 本地配置、设备令牌和 SQLite 存放于 `%LOCALAPPDATA%\WH6成交采集器`，不写入 WH6 `Record`。
- [ ] 验收 6—8 月白名单外记录不进入上传队列，9 月 1 日起记录能够上传。
- [ ] 验收离线排队、重启续传、客户端最低版本阻断、切换账户暂停和设备撤销。
- [ ] 用第二台 Windows 设备绑定同一账户并上传重复样本，验证原始观察可有两条、规范成交仍只有一条。
- [ ] 用真实自然新增成交验证时效；缓存回放不能替代真实 WH6/Windows/Staging 验收。

**通过条件：** 用户可在任意满足 WH6 缓存访问条件的 Windows x64 电脑上直接运行同一 EXE，只通过 `Record + 验证码` 完成绑定和上传。

### Phase 7：9 月日常运行与观测

- [ ] 从 2026-09-01 起持续接收 9 月白名单数据；用户不手工维护日期范围。
- [ ] 每日核对上传记录数、规范成交数、重复数、异常数、最后心跳和策略 revision。
- [ ] 只对异常做报告和证据保留，不自动修改结算数据或猜测冲突答案。
- [ ] 若策略服务短时不可用，保留当前日本地队列并暂停历史；恢复后按新策略续传。
- [ ] 若新增完整月结单生效，下一次策略刷新自动调整范围，不要求重新打包 EXE。

**通过条件：** 9 月日常数据连续、可追溯、无跨设备重复规范事实，且没有 6—8 月历史重新污染。

### Phase 8：9 月月结关闭

- [ ] 用户通过现有结算单入口上传 9 月完整月结单。
- [ ] 系统完成预览、数量校验、期间校验和人工确认后，批次才变为 `active monthly`。
- [ ] 在同一事务或可重试任务中协调 9 月 WH6、日结和月结事实，输出已匹配、未匹配、歧义和字段冲突清单。
- [ ] 9 月正式统计切换为月结优先，WH6 原始观察继续保留。
- [ ] 日期策略将 9 月加入关闭区间；若没有更早缺月，起点推进到 2026-10-01。
- [ ] Windows 客户端刷新策略后停止补传 9 月历史，只继续 10 月及以后开放日期。

**通过条件：** 9 月月结事实完整、异常显式、起点正确推进，且不需要用户清空缓存或重装程序。

### Phase 9：Production 独立 Gate B（本轮不执行）

只有 Staging 和 Windows 验收全部通过后，才向用户提交一份短的 Production 发布摘要，至少包含：

- 将发布的 commit、数据库迁移和客户端版本。
- Staging 真实证据与尚存异常。
- Production 项目和 Render 服务的精确映射。
- Production 独立备份及验证结果。
- 回滚点、回滚方式和预计数据影响。
- Production 验证码生成和最小试运行账户。

未取得用户对 Production 的单独明确确认前，不合并 `main`、不推送 `main`、不迁移或写入 Production 数据、不修改 Production 环境变量、不发布 Production。

## 7. 验证命令与证据要求

实施过程中至少执行以下本地门禁；具体测试文件可随实现重命名，但需求编号不能丢失：

```bash
python3 -m pytest -q \
  tests/test_wh6_collector_policy.py \
  tests/test_wh6_collector_cli.py \
  tests/test_wh6_setup_ui.py \
  tests/test_wh6_collector_store.py \
  tests/test_wh6_collector_v2_end_to_end.py \
  tests/test_trading_collector_api.py \
  tests/test_trading_collector_service.py \
  tests/test_trading_collector_reconciliation.py \
  tests/test_wh6_installer.py

node --test tests/trading_collector_frontend.test.mjs
git diff --check
```

数据库和真实环境证据必须包括：

- 迁移前后受影响表行数和约束清单。
- 备份文件或官方备份标识、非空验证和恢复/读取验证。
- RLS 状态、`anon`/`authenticated` grants、设备令牌实际可调用路径。
- 两设备并发上传同一成交后的原始层与规范层数量。
- 6—8 月关闭、9 月开放的真实策略响应。
- Staging 页面上月结事实、盘中临时事实和异常状态的真实回读。
- Windows EXE 文件哈希、版本、首次绑定、重启、离线续传和自然新增成交证据。

不得把以下任一单项包装为完成：测试通过、EXE 生成、API 200、数据库出现记录、Render health 正常或缓存回放成功。

## 8. 验收矩阵

| 需求 | 最终验收结果 |
| --- | --- |
| `WH6-A-001` | 同一 EXE 无环境选择；两类验证码分别只能绑定对应环境和账户 |
| `WH6-A-002` | 6—8 月关闭，起点为 9 月 1 日；缺月不被后月跨越 |
| `WH6-A-003` | 仅白名单历史进入队列；策略离线时历史失败关闭、当前日仅本地排队 |
| `WH6-A-004` | 两电脑重复上传保留两条观察但只有一条规范成交；合法同值成交不误合并 |
| `WH6-A-005` | 月结 > 日结 > WH6，空值不擦除，冲突和未知价差进入异常 |
| `WH6-A-006` | 交付一个便携 EXE；目标 Windows 不安装、不运行 PowerShell/Python |
| `WH6-A-007` | Staging 迁移前有一次可验证备份；客户端无数据库凭据和删除能力；RLS/grants 合格 |
| `WH6-A-008` | Staging 与 Windows 完整验收；Production 保持未改，等待独立确认 |

## 9. 回滚和停止条件

### 回滚原则

- 代码：保留 Staging 上一个已知可用 commit，可回滚 Render 服务版本。
- 数据库：迁移只做向前兼容新增或可逆约束调整；执行前记录精确回滚 SQL 和备份标识。
- 数据：原始观察不删除，协调状态可依据审计重算；禁止用批量删除解决重复。
- 设备：新设备令牌可单独撤销；错误环境验证码不能跨环境复用。
- 客户端：服务端用 `minimum_client_version` 阻止已知不安全旧版本继续上传，但不远程删除本地缓存。

### 必须停止并回报的情况

- Staging/Production 映射或验证码来源不能被服务端证明。
- 备份无法生成、为空或无法验证。
- 6—8 月任一月结单不再是完整有效月结。
- 策略把 2026-09-01 以前日期列入本次开放范围，或错误跨过缺月。
- 服务端规范键在真实样本中合并两笔合法成交，或并发时产生重复规范事实。
- 协调会删除原始证据、让高优先级空值擦除有效值，或把歧义静默判定为成功。
- Windows 实机要求安装运行库、PowerShell、Python或管理员权限才能工作。
- 任何步骤触及真实交易控制能力。
- 任何步骤需要进入 Production，而尚未取得用户单独明确确认。

## 10. 双方后续安排

### 由 Codex 推进

1. 同步最新 Staging，按测试先行方式完成方案 A 的定向重构。
2. 准备并验证一次 Staging 备份，执行迁移、6—8 月状态协调和 Staging 部署。
3. 完成真实策略/API/页面回读，证明 6—8 月关闭、9 月开放。
4. 生成单个便携 EXE，并完成 Windows 实机、离线、重启和多电脑去重验收。
5. 9 月运行期间持续按证据检查完整性和异常，不手工改变日期口径。
6. 9 月月结生效后完成协调并验证起点自动推进。

### 需要用户配合

1. 确认本计划后，授权开始 Staging 实施；无需为本轮重复确认普通 Staging 开发步骤。
2. Windows 验收时提供一台已安装并登录 WH6 的电脑，选择正确 `Record`，粘贴 Staging 页面生成的验证码。
3. 在实际使用期间保持 WH6 缓存可读和网络可用；异常时提供对应日期或界面现象即可，不需要运行命令。
4. 9 月完整月结单取得后，通过现有结算单入口上传并确认。
5. 只有准备进入正式环境时，再单独确认 Production Gate B。

用户不需要新建 Supabase 项目、不需要手工维护日期白名单、不需要运行 PowerShell，也不需要自己处理数据库去重或备份命令。

## 11. 与旧 V2.1 计划的关系

旧计划 `docs/superpowers/plans/2026-09-04-wh6-settlement-governed-upload-repair.md` 中仍然有效的部分包括：结算来源优先级、原始证据保留、服务端分页、本地队列迁移、逐条上传回执、真实 Staging 与 Windows 验收边界。

以下条款由本计划替换：

- “只返回 `closed_ranges`”替换为“账户起点 + 正向 `upload_ranges` + `closed_ranges`”。
- “客户端固定 Staging”替换为“验证码自动路由，用户不选择环境”。
- “自包含 Setup 覆盖安装”替换为“单个便携 EXE，无安装步骤”。
- “客户端成交键作为云端唯一身份”替换为“服务端重算规范键 + 数据库唯一约束”。
- “6—8 月是否存在待核验”替换为“当前 Staging 已核验完整，不重复上传；执行时只做状态复核”。
