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
- 订单融资管理：从本地订单融资 Excel 台账导入合同、融资、信用证、交单、收汇、还款和额度数据，提供 `订单融资进度` 与 `融资资金监控` 两个页面。
- 用户与权限管理：独立登录账号、用户/领导/管理员类型、部门默认权限、个人例外、查看/日常/敏感操作分级、自助改密、管理员重置和账号停用。
- 交易管理：单个期货公司日结/月结 TXT 自动识别、完整预检、重复与版本覆盖、期初持仓连续性、只读事实总览、持仓与交易、整笔业务归属、上海钧能台账和全量期权台账。普通平仓、行权、履约和到期放弃统一显示在“平仓记录”并以了结类型区分；行权只关联账单中真实形成的期货开仓，不生成交易。首版浮动盈亏与期权风险指标统一显示“待计算”，汇总与导出保留入口暂不执行导出。
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

## 铁矿石基差 API 增量同步

铁矿石期现采用“Staging 单一采集源、Production 快照跟随”的双库模式。两个环境仍只连接各自的 Supabase，不允许 Production 直连 Staging 数据库。

两个环境均显式配置：

```text
IRON_ORE_BASIS_AUTO_SYNC_ENABLED
IRON_ORE_BASIS_SYNC_MODE
```

Staging 使用 `IRON_ORE_BASIS_SYNC_MODE=source`，读取 `EBC_ACCOUNT`、`EBC_PASSWORD`，可选读取 `EBC_MAINBOARD`、`EBC_CPU`。凭据只保存在 Staging Render 环境变量中。启用后，Web 服务启动时补查最近数据，并按北京时间每日 09:30、10:30、21:30 检查相应时间窗。

Production 使用 `IRON_ORE_BASIS_SYNC_MODE=snapshot_follower`，不配置 EBC 凭据，并配置：

```text
IRON_ORE_BASIS_SNAPSHOT_UPSTREAM_URL=https://ltm-web-staging.onrender.com
```

两个服务通过服务端 Bearer Secret 访问 `/api/internal/iron-ore-basis/snapshot`。可配置专用 `IRON_ORE_BASIS_SNAPSHOT_SHARED_SECRET`；未配置时兼容复用现有 `ORDER_FINANCE_SNAPSHOT_SHARED_SECRET`，实际值不得进入仓库、日志、接口响应或版本记录。快照仅包含 `2026-07-13` 起由 API 生成的期现结果和计算明细，不包含数据库 ID、用户、权限、日志或源站凭据。

Staging 仅在最近存在 `success` 或 `partial` 源同步批次、结果与明细一一对应且批次覆盖最新数据日期时发布内容哈希版本。Production 每 5 分钟检查一次；先校验字段、行数、最新日期、重复业务键和内容哈希，再在单一事务中只追加缺失业务键。同一业务键只要与 Production 既有结果或明细不同，整包拒绝且不覆盖历史。相同版本重复检查写入 0 行。

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

导入时只读取 `订单`、`额度`、`预警` 三个页签：`订单`是唯一订单事实来源，`额度`提供银行授信与占用，`预警`按项次关联风险提示。工作簿中的其他页签全部忽略，不参与字段补全或状态判断。网页仍保持“订单融资进度”和“融资资金监控”两个页面。

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

Staging 使用 `ORDER_FINANCE_SYNC_MODE=snapshot_follower`，并配置：

```text
ORDER_FINANCE_SNAPSHOT_UPSTREAM_URL
```

跟随模式不会构造 WPS 客户端，也不会读取或刷新 WPS token。它只通过 HTTPS 和服务端 Bearer Secret 读取源端 `/api/internal/order-finance/snapshot`；快照仅包含当前有效 WPS 事实字段，不包含数据库 ID、用户、权限、日志或下一步、备注、人工装船确认等环境本地管理字段。Production 不配置 Staging 的数据库连接，Staging 也不配置 Production 的数据库连接。

仅当 `ORDER_FINANCE_WPS_AUTO_SYNC_ENABLED=true` 且当前模式所需配置完整时启动后台任务。两个模式都以每天北京时间 09:00 和 17:00 为业务时点，包括周末和节假日；源端只调用用户 token 刷新、文件元数据和源文件下载接口，不调用上传、修改、分享或删除接口。跟随端在源端尚未完成当前时点时每 5 分钟重试，成功后按源版本和事实哈希幂等写入自己的数据库。页面只显示最近一次成功自动同步时间和该次实际变化条数；失败保留上次成功状态并写入脱敏服务端日志。

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
