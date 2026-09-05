# 轻量化交易管理系统 Web

这是从 Windows 桌面版迁移到 Web 版的第一版框架。

## 当前已实现

- Web 登录页。
- 主工作台布局。
- 左侧菜单保留旧台账管理，并新增独立一级菜单“交易管理”。
- 后端 SQLite 数据库初始化。
- 默认管理员账号：`管理员 / admin`。
- 上海钧能台账的查询、筛选、新增、编辑、删除、平仓、价格刷新、CSV 导出。
- 风险预警规则的新增、编辑、启停、删除。
- 风险预警历史查看。
- 事中风险监控的策略组和持仓接口骨架。
- 订单融资管理：从本地订单融资 Excel 台账导入合同、融资、信用证、交单、收汇、还款和额度数据，并以独立快照融合出口船舶动态，提供 `订单与船舶总览`、`订单融资进度` 与 `融资资金监控` 三个页面。
- 订单全流程管理（测试版）：与旧订单融资表并行的主卡/子记录模型，支持融资与过单两类业务、WPS/邮件标准化批次、状态/风险/数据异常、分页筛选和人工 FCR/字段覆盖；默认不自动连接来源。
- 用户与权限管理：独立登录账号、用户/领导/管理员类型、部门默认权限、个人例外、查看/日常/敏感操作分级、自助改密、管理员重置和账号停用。
- 交易管理：单个期货公司日结/月结 TXT 自动识别、完整预检、重复与版本覆盖、期初持仓连续性、支持账户与日/月/季/自定义范围筛选的只读总览、持仓与交易明细、整笔业务归属、上海钧能台账和全量期权台账。总览“全部”显示事实盈亏，“基础套保 / 战略套保”显示业务归属盈亏；普通平仓、行权、履约和到期放弃统一显示在“平仓记录”并以了结类型区分；行权只关联账单中真实形成的期货开仓，不生成交易。首版浮动盈亏与期权风险指标统一显示“待计算”，汇总与导出保留入口暂不执行导出。
- 收盘交易复盘 Agent Phase 1（Staging 已部署）：提供固定宏源账户铁矿石期权指定日期的只读摘要接口，按真实月份、Call/Put、买卖方向和行权价区间动态分组，分别返回不扣手续费的真实平仓盈亏与日结算口径持仓浮盈浮亏；日结单优先，只有月结单时明确降级为部分完成。当前仅完成确定性计算底座，尚未形成合格的网页 Agent 测试版；后续统一对话、DeepSeek、推荐问题、自动日常结果、会话隔离和试点权限以 `docs/superpowers/specs/2026-09-03-closing-trading-review-agent-staging-requirements.md` 为准。
- 贸易台账管理（Staging 已接真实源）：新增“现货业务台账管理”，覆盖说明书定义的 51 个 A:AY 字段、系统同步/人工字段、待补录、同步异常、组合筛选、敏感权限编辑、战略套保全开全平录入、服务端分页和全量 Excel 导出。列表、待补录和同步异常默认每页 20 条，可切换 20/50/100 条并只查询当前页；列表只返回表格摘要，打开单条详情时才读取该记录的完整 51 字段。Render Staging 已通过个人服务账号认证连接正式服务端 JSON 只读接口，并把 7 个销售组内的生效现货销售合同明细同步到独立 Supabase Staging；本地仍使用明确标注的 fixture 验收，不连接真实源。
- WH6 成交与持仓采集器 V2.1（Staging 开发版）：在全量只读采集、实时优先、本地断网/重启保留和服务端设备绑定基础上，按账户、环境和当前有效完整月结单下发正向历史上传白名单；日结/月结按字段优先级协调并保留追加审计，上传按 100 条分批逐条确认，成交明细支持真正的 20/50/100 服务端分页。客户端版本源固定为 `0.3.0`，验证码自动决定测试版或正式版，界面不提供环境地址选择；策略不可用时只继续当前交易日本地排队，历史暂停。跨期组合的真实 WH6 原始编码尚未取得，解析状态保持 `unknown_format`，不凭猜测拆腿或生成夹具。Windows 构建直接输出单个便携 EXE；真实 WH6、Windows 和 Staging 页面验收仍须按方案 A 计划完成，当前 Mac 工作区不宣称已完成 Windows/WH6 实机验收。
- 铁矿石期现：历史 Excel 作为存量底库，新增 EBC 现货指标与新浪 I0 收盘价 API 增量同步；按版本化业务规则计算并保存精简结果与完整明细。期现数据管理提供只读分页查询，期现数据展示提供独立最优仓单、港口页签和按品种/年份绘制的日度基差图表。

## 本地运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

如需只连接本地 SQLite 试用，避免读取 `.env` 中的云端 `DATABASE_URL`：

```bash
env -u DATABASE_URL .venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8001 --reload
```

打开：

```text
http://127.0.0.1:8000
```

如果使用上面的本地 SQLite 试用命令，则打开：

```text
http://127.0.0.1:8001
```

WH6 V2.1 的本地回归使用临时 SQLite，不连接云端数据库；完整命令、Staging 备份/迁移和 Windows 实机证据边界见方案 A 计划。协调脚本默认 dry-run，只有在已确认目标环境、备份和数量后才允许显式 `--apply`，本计划不执行 Production：

```bash
env -u DATABASE_URL python3 -m pytest -q \
  tests/test_trading_collector_reconciliation.py \
  tests/test_wh6_collector_migrations.py \
  tests/test_wh6_collector_policy.py \
  tests/test_wh6_collector_store.py \
  tests/test_wh6_collector_scheduler.py \
  tests/test_wh6_collector_cli.py \
  tests/test_reconcile_wh6_intraday_script.py
node --test tests/trading_collector_frontend.test.mjs
```

现货业务台账的本地同步验收使用 `tests/fixtures/spot_ledger_sales_contract_fixture.json`，覆盖 7 个销售组、数量回退、源系统销售类型原文、跨组标识、待补录和同步异常。销售类型 D 优先取正式源报表“业务类别”完整原文并按销售合同商品明细 ID 回接，不做本地代码转换；只有源报表没有完整原文时才保留代码并标记同步异常。落地货关系只依据完整原文中的“落地”或明确的 B09 系列，不能把 B05/B07 裸代码当成落地货，因为源字典中同一基础代码存在多个业务类别。记录详情保留全部非人工、非技术隐藏字段的位置，源系统空值统一显示为横杠 `—` 并用异常色提示，不把横杠写入数据库。自动同步调度仅在显式设置 `SPOT_LEDGER_AUTO_SYNC_ENABLED=true` 时启动，按北京时间 09:00—18:00 每小时执行；服务在同步时段内启动时只执行最近一个已到小时，不补跑当天此前所有小时，数据库已有同一时段记录时跳过，19:00 后启动不补跑；不提供实时同步或手动立即同步。Staging 当前使用 `SPOT_LEDGER_SOURCE_MODE=official_json` 并启用销售类型原文回接；本地默认不启用。

2026-08-25 经用户授权在已登录浏览器中完成只读认证与接口复核：销售合同列表实际调用 `POST https://tds-api.ejianlong.com/tradeing/saleContract/saleContractList`，返回 200 JSON，并使用 Bearer 认证；统一认证采用登录页公钥 RSA 加密密码、`POST /login/pwd` 返回一次性 code、`GET /login?code=...` 换取 Bearer 令牌，令牌失效后没有独立 refresh token，需重新登录。现货适配器因此支持从服务端 Secret 读取 `SPOT_LEDGER_SOURCE_USERNAME`、`SPOT_LEDGER_SOURCE_PASSWORD`，使用同一内存会话重新登录一次，并且不会记录账号、密码、票据、令牌或源响应。

真实同步统一使用 `tds-api.ejianlong.com` 的服务端 JSON 接口，不复用个人浏览器会话，不复制 Cookie，也不做网页爬虫；销售业务 AF 优先读取报表明确返回的“需求业务员”，报表缺失时才回退已匹配需求详情的 `workManName`，二者冲突时保留报表值并标记同步异常。销售执行 AG 独立读取销售合同详情的 `workManName`，不读取合同创建人。销售类型完整原文通过同一服务端认证读取已确认的 `tds-api.ejianlong.com/jmreport/show` 报表“业务类别”，并以销售合同商品明细 ID 做只读回接，不把本地映射表当作事实源。源端列表筛选参数不能可靠限制现货和销售组，因此适配器分页读取需求头，在服务端本地按现货和已确认的 7 个销售组筛选，只为命中需求读取关联合同链、销售合同、结算、采购、匹配和资源明细；仅保留状态为 `70/生效` 的销售合同，并优先使用销售合同商品明细 ID。报表回接失败或没有完整业务类别、两个来源都没有需求业务员时不软隐藏旧记录，并在对应行标记同步异常。Staging Secret 只保存 `SPOT_LEDGER_SOURCE_USERNAME`、`SPOT_LEDGER_SOURCE_PASSWORD`，代码、日志、接口响应和文档均不得记录其值。

销售价格优先采用源接口明确返回的含税字段 `taxPrice`，没有该字段时才回退 `unitPrice` 或 `price`，不得自行乘税率推算。操作抬头、供应商和商品分类使用版本化显式字典；供应商法定全称现在是 Q 的标准主数据，已确认简称只作为页面展示别名，未命中简称但有完整法定全称不再作为同步异常；商品分类 AU 继续采用系统显式分类字典，Excel 中重复 H 的 AU 不覆盖系统分类。历史工作簿导入会在前 20 行识别真实表头，只处理 U >= 2026-01-01 的记录，并忽略只有预填公式、没有合同/商品/价格/数量业务标识的空行；当前完整 Excel 的回填身份键为 AD/H/U，X/L 数量和 Z 价格只做一致性核对：已核实的 13% 税率表示差异不覆盖系统价格，结算数量差异不覆盖系统数量。唯一身份匹配且 Excel 有值才迁移人工字段或空白 K；销售类型 D 遵循“系统有值优先，系统为空才采用 Excel 原值”，系统已有值与 Excel 不一致时记录冲突并保持系统值。无法匹配、存在歧义或非空冲突时保持原状，数值字段中的文字说明会跳过而不会写入错误类型。

测试版现货台账的 Excel 回填使用 `scripts/import_spot_ledger_staging.py`，默认只做 dry-run，要求通过 `STAGING_LEDGER_USERNAME` 和 `STAGING_LEDGER_PASSWORD` 环境变量登录，并且脚本只接受 `https://ltm-web-staging.onrender.com`。脚本通过管理员专用的 2026 年匹配快照一次读取识别字段和待回填字段；确认 dry-run 后显式增加 `--apply` 才写入测试版，写入接口会校验快照中的原值仍未被其他操作修改。脚本只回填 Excel 实际有值且唯一精确匹配的字段，不写 Q/AU，也不把空值写成占位符。

现货业务台账数据库沿用项目现有 `db.init_db()` 双数据库兼容路径：本地使用 SQLite，Render/Supabase 使用 PostgreSQL 专用的 `TEXT`、`DOUBLE PRECISION` 和幂等 `CREATE TABLE IF NOT EXISTS`。两张台账表只允许服务端 FastAPI 连接访问，PostgreSQL 路径启用 RLS 并撤销 `anon` / `authenticated` 的直接表权限；页面不通过 Supabase Data API 直连。Staging 已写入真实只读源同步结果；系统字段随源刷新，人工字段在重复同步时保留，只有完整成功扫描才允许软隐藏缺失记录。Production 仍未启用该来源或写入该台账，必须单独通过 Gate B。

## 铁矿石基差 Excel 导入

导入命令默认只校验文件、字段、业务唯一键和两张数据表的一致性，不写数据库：

```bash
env -u DATABASE_URL .venv/bin/python scripts/import_iron_ore_basis.py /绝对路径/铁矿石港口基差基础数据库_2024至今.xlsx
```

校验通过后，显式增加 `--apply` 才会在单一事务中写入或更新期现结果表和计算明细表；同一文件重复导入不会产生重复业务记录：

```bash
env -u DATABASE_URL .venv/bin/python scripts/import_iron_ore_basis.py /绝对路径/铁矿石港口基差基础数据库_2024至今.xlsx --apply
```

连接 PostgreSQL 时由后端统一读取 `DATABASE_URL`。上线环境执行写入前必须先确认环境映射并完成数据库备份，不能把生产连接信息写入命令、文档或日志。

## 交易管理实时估值

交易管理的上海钧能与期权业务台账只读取已经完成业务归属的数据。实时估值通过单一、只读的天勤行情会话获取，服务端只读取 `TQSDK_USERNAME` 和 `TQSDK_PASSWORD`，不配置期货公司或真实交易账户，也不调用委托、撤单或账户持仓交易接口。`TqApi` 按 SDK 默认使用本地 `TqSim` 上下文建立行情连接，本系统不读取或操作该模拟账户。

未配置天勤认证或行情失败时，接口会使用同一 TXT 结算快照中的期权结算价计算参考浮动盈亏，IV 和 Greeks 保持空值，不把结算快照伪装成实时风险值。期权持仓表只展示合约、方向、手数、持仓均价、估值价、看涨/看跌、行权价、IV、浮动盈亏和四项 Greeks，不展示标的、标的价格、到期日或估值日。页面每 15 秒自动检查一次行情，并明确显示上次更新时间以及“数据已更新 / 已检查，行情无变化 / 更新失败”等状态。实时行情、结算快照估值、浮动盈亏、IV 和 Greeks 只在接口响应中计算，不写入交易事实表。

商品期货期权使用同一行情快照中的期权价、标的期货价和到期时间，按 Black-76 统一反算 IV 并计算 Greeks，不直接使用天勤内置的 Black-Scholes Greeks。期权持仓明细的 Delta、Gamma、Theta 和 Vega 采用带买卖方向的每手口径，不乘持仓手数或合约乘数；Theta 表示每日时间衰减（年化原值除以 360），Vega 表示 IV 变化 1 个百分点的价格敏感度（原值除以 100）。组合汇总按每手值乘剩余手数聚合，同样不乘合约乘数。

期权台账的四项希腊字母固定显示四位小数。若最新 TXT 持仓快照尚未更新，而其中期权已经超过到期日，系统仍保留该行用于核对来源，但不再展示或汇总其当前估值、标的价格、IV、浮动盈亏和 Greeks，并在到期日标记“已到期”。系统不会根据过期快照自行推断行权或放弃结果；真实了结状态以后续导入的交易所结算单为准。

### CZSC Gate A 纯行情只读通道

Staging 可通过受保护的 `GET /api/internal/futures-market/klines` 向个人工作台可行性测试提供纯行情 K 线。服务端继续复用同一 TqSdk 行情提供器，只接受铁矿石、螺纹钢、热卷主连，以及 30 分钟、60 分钟、日线三个周期；响应仅包含合约、时间、开高低收、成交量和主力合约映射，不查询交易业务台账、资金或持仓，也不提供任何写入方法。

通道使用独立的 `FUTURES_MARKET_READONLY_SHARED_SECRET` Bearer Secret。该值只配置在 Render Staging 和本机受保护凭据存储中，不得写入仓库、日志、接口响应或版本记录。认证缺失或错误时接口返回 404；TqSdk 或行情缺失时返回 503，不使用结算单或模拟数据冒充真实 K 线。Production 默认不配置该变量。

### 历史期权研究数据门槛

历史期权研究与交易事实完全隔离，使用 `option_research_contracts`、`option_research_bars`、`option_research_runs` 和 `option_research_gaps` 四张独立表。仅凭历史期货价格不得生成或宣称真实期权回测收益；期货数据只可用于压力情景或明确标注的合成机制检查。

Staging 部署会使用现有 `TQSDK_USERNAME`、`TQSDK_PASSWORD` 自动执行一次有界、只读的能力探针，依次验证铁矿石具体期货发现、对应历史期权发现、样本日线、普通接口最多 10000 根 5 分钟线的覆盖范围和专业 `DataDownloader` 权限。探针只返回状态、计数和覆盖日期，不返回认证值、行情价格或原始异常。结果可通过只读接口查看：

```text
GET /api/option-research/readiness
```

首轮回测只允许使用通过逐合约覆盖检查的近月、次月有效窗口；普通 5 分钟线未覆盖到策略所需起点时标记 `BLOCKED_DATA`。没有专业下载权限时，不做完整存续期、任意区间或逐笔级结果声明。Production 默认禁用；需要本地测试时可显式配置 `OPTION_RESEARCH_ENABLED=true`，并用 `OPTION_RESEARCH_PROBE_REFRESH_HOURS` 调整重复检查间隔。

冻结协议下的第一轮回测入口为 Staging 登录后的只读研究接口：

```text
POST /api/option-research/backtest/start
GET  /api/option-research/backtest/status
GET  /api/option-research/backtest/results
```

回测入口默认先执行 `daily_data_audit`：显式传入 `max_options=0`、`max_futures=0` 表示不限制合约数量，逐月核对具体期货、对应期权、真实日线和交易所结算价，完整性门槛未通过时不计算收益。具体铁矿石期权始终映射到代码中同月份的具体期货（例如 `DCE.i2605-C-800` 映射 `DCE.i2605`），主力连续只可用于选月参考，不能替代期权估值、到期实值判断或行权处理。通过日线门槛后的 V2 初筛明确标记为 `daily_v2_screen`，以交易所结算价盯市、次日开盘成交，并将已实现月度收益与期末残仓浮盈亏分开，不能替代最终 5 分钟回测。接口会把合约和行情写入独立研究表，并在 `option_research_results` 保存精简结果；超过数据上限、映射错误或缺口的数据必须标记为 `BLOCKED_DATA`。接口需要已登录用户，仅在 Staging 可用，不执行下单、撤单、行权或账户操作。

## 铁矿石基差 API 增量同步

铁矿石期现的目标架构是“Production 单一采集源、Staging 受保护快照跟随”的双库模式。两个环境仍只连接各自的 Supabase，不允许跨库直连。Gate B 前为避免提前改变正式环境，Staging 继续使用 `source` 做本次验收；角色切换和正式环境变量变更必须另行确认。

两个环境均显式配置：

```text
IRON_ORE_BASIS_AUTO_SYNC_ENABLED
IRON_ORE_BASIS_SYNC_MODE
```

采集源使用 `IRON_ORE_BASIS_SYNC_MODE=source`，读取 `EBC_ACCOUNT`、`EBC_PASSWORD`，可选读取 `EBC_MAINBOARD`、`EBC_CPU`。凭据只保存在采集源 Render 环境变量中。启用后，Web 服务启动时补查最近数据，并按北京时间每日 09:30、10:30、21:30 检查相应时间窗。

目标 Staging 跟随端使用 `IRON_ORE_BASIS_SYNC_MODE=snapshot_follower_on_start`，不配置 EBC 凭据，并配置：

```text
IRON_ORE_BASIS_SNAPSHOT_UPSTREAM_URL=https://ltm-web-staging.onrender.com
```

两个服务通过服务端 Bearer Secret 访问 `/api/internal/iron-ore-basis/snapshot`。接口用内容版本返回标准 `ETag`；跟随端发送 `If-None-Match`，版本未变化时收到 `304`，不读取 JSON 正文、不写业务数据。可配置专用 `IRON_ORE_BASIS_SNAPSHOT_SHARED_SECRET`；未配置时兼容复用现有 `ORDER_FINANCE_SNAPSHOT_SHARED_SECRET`，实际值不得进入仓库、日志、接口响应或版本记录。快照仅包含 `2026-07-13` 起由 API 生成的期现结果和计算明细，不包含数据库 ID、用户、权限、日志或源站凭据。

采集源仅在最近存在 `success` 或 `partial` 源同步批次、结果与明细一一对应且批次覆盖最新数据日期时发布内容哈希版本。连续 `snapshot_follower` 模式保留用于分阶段兼容；`snapshot_follower_on_start` 每次唤醒最多尝试 3 次，成功或 `304` 立即退出，失败后保留上次成功数据和版本并停止。版本变化时先校验字段、行数、最新日期、重复业务键和内容哈希，再在单一事务中只追加缺失业务键。同一业务键只要与本地既有结果或明细不同，整包拒绝且不覆盖历史。

`IRON_ORE_BASIS_AUTO_SYNC_ENABLED` 未显式设为 `true`，或同步模式及其必需配置不完整时，不会启动相应后台任务。

手工命令默认只抓取、计算和汇总，不写数据库：

```bash
.venv/bin/python scripts/sync_iron_ore_basis.py --start-date 2026-07-13 --end-date 2026-07-13
```

确认目标环境、数据库备份和 dry-run 结果后，显式增加 `--apply` 才写入：

```bash
.venv/bin/python scripts/sync_iron_ore_basis.py --start-date 2026-07-13 --end-date 2026-07-13 --apply
```

增量写入按来源、指标、业务日期和结果业务键去重。同一来源点首次写入后作为历史口径保留，后续观测到变化只记录差异，不覆盖已有历史源值或已生成的基差结果；缺少任一必要数据的组合跳过。页面顶部仅显示当前结果表的最新数据日期。

## 操作日志保留与归档

- 在线日志默认每次加载 100 条，使用游标分页；页面只在管理员打开操作日志时请求数据。
- 在线保留规则为最近 12 个月，并以 20 万条作为软上限；只归档已经结束的完整自然月。
- 归档文件为 gzip NDJSON，存放在 Supabase 私有 bucket `operation-log-archives`。只有管理员主动下载历史归档时才读取文件。
- 正式归档需要在服务端配置 `SUPABASE_URL` 和 `SUPABASE_SERVICE_ROLE_KEY`；service-role 不得进入前端、仓库、日志或接口响应。

归档命令默认只预览，不写数据库或 Storage：

```bash
.venv/bin/python scripts/archive_operation_logs.py --environment staging
```

确认 dry-run 后才使用 `--apply`。恢复命令同样默认只预览，必须显式传入归档 ID 和 `--apply`：

```bash
.venv/bin/python scripts/restore_operation_logs.py 1
.venv/bin/python scripts/restore_operation_logs.py 1 --apply
```

归档过程先上传并校验文件，再在数据库事务中写入元数据和删除对应在线日志；校验失败、删除行数不一致或恢复 ID 冲突都会停止并回滚数据库写入。当前没有自动创建 Render Cron，是否增加付费定时服务需单独确认。

订单融资 Excel 台账当前默认读取本机新模板文件，包含 2025 和 2026 全部项次：

```text
/Users/wangjingze/建龙/贸易处/YOLANDA和香港建龙出口钢材信用证台账.xlsx
```

导入时只读取 `订单`、`额度`、`预警` 三个页签：`订单`是唯一订单事实来源，`额度`提供银行授信与占用，`预警`按项次关联风险提示。工作簿中的其他页签全部忽略，不参与字段补全或状态判断。“订单与船舶总览”使用另一张隔离快照表，不修改这条订单融资事实链。

### 订单与船舶快照导入

订单与船舶总览通过业务编号精确关联当前订单融资数据；不做模糊匹配。当前确认基线为 `2026-08-10 R1`，页面只作为预览/影子版本，不改写或替代线下 R1。汇报还款到期日来自 R1；邮件台账只用于逐业务编号核对；WPS 的 `finance_due_date` 独立显示为资金执行到期日，不能覆盖汇报日期。船舶字段缺失时页面显示为 `—`，不会写回 `order_finance_progress`。

导入命令默认只校验锁定的定稿文件、来源日期、字段、业务编号唯一性和汇总值，不写数据库：

```bash
env -u DATABASE_URL .venv/bin/python scripts/import_order_vessel_snapshot.py /绝对路径/出口船舶动态定稿.xlsx
```

显式增加 `--apply` 才会幂等写入 `order_vessel_snapshots`。连接 PostgreSQL 时，脚本只允许 `LTM WEB STAGING`；其他 PostgreSQL 项目会被拒绝：

```bash
.venv/bin/python scripts/import_order_vessel_snapshot.py /绝对路径/出口船舶动态定稿.xlsx --apply
```

可通过 `--email-checks-json` 同步写入已经人工确认来源的邮件核对结果。JSON 顶层为 `checks` 数组，每项必须包含 `business_no`、`email_due_dates`、`source` 和 `source_date`；业务编号必须精确命中当前活动 R1。空数组、多日期和单边缺失都会保留为核对状态，不会改写 R1 日期：

```bash
.venv/bin/python scripts/import_order_vessel_snapshot.py /绝对路径/出口船舶动态定稿.xlsx \
  --email-checks-json /绝对路径/邮件核对结果.json --apply
```

### 订单全流程管理测试版

新页面 `订单全流程管理` 使用独立的 `order_lifecycle_*` 表组，不迁移或改写旧 `order_finance_progress` 事实。WPS 只读取 `YOLANDA`、`JLHK`、`天津建龙` 三张原始业务表；邮件批次要求阿城、北满、承德、东钢、抚顺、西林六个附件齐全后才解析。来源行先标准化为主卡、合同、融资、船舶、单据、客户回款和银行还款子记录，批次使用来源版本/键集幂等处理，首次缩减只记录删除候选，连续相同键集才删除整卡。

测试环境可以用 `POST /api/order-lifecycle/import-local` 传入受控的 WPS `.xlsx` 或六附件目录；页面本身不提供整卡新建、普通删除或立即更新。来源自动同步只有显式设置 `ORDER_LIFECYCLE_AUTO_SYNC_ENABLED=true` 才启动：工作日 WPS 北京时间 09:00–18:00 每小时一次，周一邮件台账 09:00–11:00 每小时一次，失败 5 分钟后只重试一次。IMAP 使用只读模式；未配置 IMAP 时可使用受控附件落地目录。所有连接变量见 `.env.example`，密钥不进入仓库、页面、日志或接口响应。

### 订单融资 WPS 自动同步

订单融资通过已经审批的企业 WPS 应用和单一用户授权只读同步同一份源格式 Excel。Production 是唯一 WPS 源端；Staging 通过受保护的事实快照跟随，不再单独刷新同一枚可轮换 refresh token。实际值不得进入仓库、日志、接口响应或版本记录。

两个环境均配置：

```text
ORDER_FINANCE_WPS_AUTO_SYNC_ENABLED
ORDER_FINANCE_SYNC_MODE
ORDER_FINANCE_SNAPSHOT_SHARED_SECRET
```

Production 使用 `ORDER_FINANCE_SYNC_MODE=wps_source`，并继续配置现有 WPS 读取变量：

```text
WPS_APP_ID
WPS_APP_SECRET
WPS_USER_REFRESH_TOKEN
ORDER_FINANCE_WPS_DRIVE_ID
ORDER_FINANCE_WPS_FILE_ID
```

目标 Staging 使用 `ORDER_FINANCE_SYNC_MODE=snapshot_follower_on_start`，并配置：

```text
ORDER_FINANCE_SNAPSHOT_UPSTREAM_URL
```

跟随模式不会构造 WPS 客户端，也不会读取或刷新 WPS token。它只通过 HTTPS 和服务端 Bearer Secret 读取源端 `/api/internal/order-finance/snapshot`；快照仅包含当前有效 WPS 事实字段，不包含数据库 ID、用户、权限、日志或下一步、备注、人工装船确认等环境本地管理字段。Production 不配置 Staging 的数据库连接，Staging 也不配置 Production 的数据库连接。

仅当 `ORDER_FINANCE_WPS_AUTO_SYNC_ENABLED=true` 且当前模式所需配置完整时启动后台任务。两个源模式都以每天北京时间 09:00 至 18:00 的每个整点为业务时点，包括周末和节假日；源端只调用用户 token 刷新、文件元数据和源文件下载接口，不调用上传、修改、分享或删除接口。跟随端通过 `ETag`/`If-None-Match` 避免重复下载：未变化时收到 `304`、不解析 JSON、不写业务数据；变化时按源版本和事实哈希幂等写入自己的数据库。`snapshot_follower_on_start` 只比较当前时间之前最近的小时源时点，最多尝试 3 次，成功或 `304` 后退出，源端尚未准备好或最终失败均保留上次成功状态。页面只显示最近一次成功自动同步时间和该次实际变化条数；日志只记录脱敏的模块、阶段、HTTP 状态、耗时、记录数和错误类型。

### Render 套餐与启动成本边界

Workspace Hobby、Production Standard 和 Staging Free 是不同层级的成本设置；本项目代码和 `render.yaml` 不管理 Render `plan`，也不包含自动升级或套餐变更 API。Free 休眠/冷启动是预期行为；只有在首次完整跟随、二次冷启动 `304` 零写入和资源门槛均验证通过后，才由人工决定是否降为 Free。资源测试不通过时保留现有套餐并报告证据，不自动升级。

## 测试版验证

- 测试版地址：`https://ltm-web-staging.onrender.com`
- 推送 `staging` 后，优先用 Codex 内置浏览器打开 `https://ltm-web-staging.onrender.com/?codex=<commit>` 做页面验证。
- 每次开始新的浏览器验证前，先关闭之前打开过的本项目测试页签，再新开干净页签测试；不要复用旧测试页签判断最新结果。
- 验证重点：页面 URL 和标题正确、控制台无应用报错、前端静态资源版本已更新（例如 `/static/app.js?v=...`、`/static/styles.css?v=...`）、目标页面功能可见可操作。
- `curl` 或 `python3 scripts/check_staging_health.py` 只作为辅助连通性检查；如果命令行外网探测失败，不应直接判定测试版部署失败，应先用内置浏览器复验。

## 后续方向

- 接入原桌面版 `risk_alert.py` 中的真实指标计算和预警扫描逻辑。
- 接入行情数据源。
- 将 SQLite 替换或迁移到 PostgreSQL/Supabase Postgres。
- 逐步迁移台账模块。
