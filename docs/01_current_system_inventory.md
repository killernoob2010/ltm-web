# 现有桌面版系统梳理

依据源码包：`/Users/wangjingze/Desktop/v6.04_source_code.zip`

检查时间：2026-06-11

## 1. 总体现状

当前系统是 Python 桌面程序，入口文件为 `main_application.py`，界面框架为 `tkinter/ttk`。

主窗口采用左侧树形菜单加右侧内容区的结构，菜单分为三组：

| 菜单组 | 模块 | 模块标识 |
| --- | --- | --- |
| 台账管理 | 上海钧能台账 | `sh_junneng` |
| 台账管理 | 钢材出口套保台账 | `steel_export` |
| 台账管理 | 子公司套保台账 | `subsidiary_hedging` |
| 台账管理 | 期权交易台账 | `option_trading` |
| 信息预警管理 | 实时信息汇总 | `info_summary` |
| 信息预警管理 | 风险预警 | `risk_alert` |
| 信息预警管理 | 事中风险监控 | `mid_event_monitor` |
| 后台管理 | 用户管理 | `user_management` |
| 后台管理 | 数据管理 | `data_management` |

后台管理菜单只对管理员显示。每次切换模块时，主程序会检查登录状态、会话状态和模块查看权限。

## 2. 主要代码结构

| 文件或目录 | 作用 | 迁移判断 |
| --- | --- | --- |
| `main_application.py` | 桌面主窗口、登录、菜单、模块加载 | 页面结构可参考，Tkinter 代码需重写 |
| `modules/db_manager.py` | 统一生成本地 SQLite 路径 | 需改为 Web 数据库配置层 |
| `modules/backend_management/user_management/` | 用户、权限、操作日志、会话 | 概念保留，认证和会话机制重构 |
| `modules/backend_management/data_management/` | 数据备份、恢复、导出、路径配置 | 保留能力，改为后台管理 API |
| `modules/sh_junneng/` | 上海钧能台账 | 业务规则需抽离，页面重写 |
| `modules/steel_export/` | 钢材出口套保台账 | 业务规则需抽离，页面重写 |
| `modules/subsidiary_hedging/` | 子公司套保台账 | 业务规则需抽离，页面重写 |
| `modules/option_trading/` | 期权交易、Greeks、行情 | 数据层和计算逻辑可较多复用 |
| `modules/info_alert/` | 信息汇总、风险预警、事中监控 | 计算和缓存可复用，线程和 UI 重构 |
| `akshare_data_fetcher.py` | AkShare 行情获取 | 可抽为行情服务 |
| `futures_data_fetcher.py` | 多来源期货行情获取 | 可抽为行情服务 |

源码中还包含若干历史版本文件，例如 `info_summary_v0.2.py` 到 `info_summary_v0.7.py`、`steel_export_backup.py`、`steel_hedging_ledger_v0.5_broken.py`。Web 迁移时应以当前主入口实际调用的版本为准，历史文件仅作为行为参考。

## 3. 台账管理模块

### 3.1 上海钧能台账

主要文件：`modules/sh_junneng/steel_hedging_ledger_v0.5.py`

核心能力：

- 交易记录增删改查。
- 开仓、平仓、未平仓、已平仓状态管理。
- 按合约、方向、日期等条件查询和筛选。
- 自动或手动更新当前价格。
- 根据方向、开平仓价格、手数、手续费计算盈亏。
- 计算资金占用收益。
- 导出 Excel。
- 已平仓概览。

主要数据表：

- `trades`
- `daily_prices`

迁移判断：

- 盈亏计算、日期规范化、历史价格查询逻辑可以抽离为服务函数。
- `Treeview` 表格、弹窗表单、文件选择、桌面自动刷新需要重写。
- 当前字段里存在固定默认修改人 `王景泽`，Web 版应改为当前登录用户。

### 3.2 钢材出口套保台账

主要文件：`modules/steel_export/steel_export.py`

核心能力：

- 开仓挂单、确认成交、持仓管理、平仓挂单、确认平仓。
- 区分订单列表、持仓列表、已平仓列表。
- 支持部分平仓和全部平仓。
- 计算净价差、美元结算收益、人民币结算收益、手续费。
- 按公司、账户、合约、日期等维度汇总。
- 导出 Excel。
- 操作日志。
- 套保策略基础表。

主要数据表：

- `steel_export_trades`
- `steel_export_close_trades`
- `steel_export_operation_logs`
- `hedging_strategies`

迁移判断：

- 开仓和平仓状态流转是核心规则，必须先抽成后端领域服务。
- 与子公司套保模块结构高度相似，Web 版可设计统一的套保台账模型或共享服务。
- 原桌面版大量确认弹窗和双击事件需要改为明确的 Web 操作按钮和详情页。

### 3.3 子公司套保台账

主要文件：`modules/subsidiary_hedging/subsidiary_hedging.py`

核心能力基本与钢材出口套保台账一致：

- 开仓挂单、确认成交、持仓、平仓、已平仓。
- 部分平仓和全部平仓。
- 盈亏、手续费、汇率、美元和人民币收益计算。
- 查询、汇总、导出。
- 操作日志。

主要数据表：

- `subsidiary_trades`
- `subsidiary_close_trades`
- `subsidiary_operation_logs`
- `hedging_strategies`

迁移判断：

- 建议与钢材出口套保台账共用后端服务结构，通过 `ledger_type` 或独立路由区分业务。
- 是否合并数据表需要在数据模型设计阶段谨慎处理。第一版可保留独立表，降低迁移风险。

### 3.4 期权交易台账

主要文件：

- `modules/option_trading/option_trading.py`
- `modules/option_trading/database.py`
- `modules/option_trading/greeks_calculator.py`
- `modules/option_trading/data_fetcher.py`
- `modules/option_trading/data_fetcher_tqsdk.py`
- `modules/option_trading/option_expiry_rules.py`

核心能力：

- 期权交易新增、编辑、删除。
- 开仓持仓列表和已平仓列表。
- 部分平仓和全部平仓。
- 根据标的、行权价、到期日、期权类型计算 Greeks。
- 获取期货和期权实时价格。
- 自动刷新持仓价格和盈亏。
- 期权到期日规则。

主要数据表：

- `option_trades`

迁移判断：

- `database.py` 已经有相对清晰的数据操作函数，优先迁移价值高。
- `greeks_calculator.py`、`option_expiry_rules.py` 可直接抽入后端服务层。
- TQSDK 和 AkShare 相关行情获取需要作为后端行情适配器处理，避免浏览器直接访问。

## 4. 信息预警管理模块

### 4.1 实时信息汇总

主要文件：`modules/info_alert/info_summary.py`

核心能力：

- 计算螺纹差、螺矿比、煤矿比、月差、内外盘价差等指标。
- 获取历史行情和实时行情。
- 缓存计算结果和每日价格。
- 自动刷新和实时更新状态。

主要数据表：

- `calculated_data`
- `daily_prices`
- `trading_days`

迁移判断：

- 指标计算和行情获取可复用。
- `threading`、`after()`、Tkinter 状态栏更新需要改为后台任务加前端轮询或 WebSocket。
- 配置文件 `info_summary_config.json` 需要改为数据库配置或服务端配置。

### 4.2 风险预警

主要文件：`modules/info_alert/risk_alert.py`

核心能力：

- 维护预警规则。
- 批量启用、停用、删除预警。
- 后台监控指标是否触发。
- 保存预警历史。
- 桌面弹窗提醒。

主要数据表：

- `alert_settings`
- `alert_history`

迁移判断：

- 预警规则和历史记录保留。
- 桌面弹窗改为 Web 通知、页面角标、消息中心或 WebSocket 推送。
- `AlertMonitor` 单例线程改为后端定时任务。

### 4.3 事中风险监控

主要文件：`modules/info_alert/mid_event_monitor.py`

核心能力：

- 策略组管理。
- 策略持仓管理。
- 按策略组计算浮动盈亏。
- 刷新价格。
- 实时更新。

主要数据表：

- `strategy_groups`
- `strategy_positions`

迁移判断：

- 策略组、持仓和盈亏计算可以抽离。
- 实时价格刷新改为服务端任务。
- 页面建议使用左侧策略组列表加右侧持仓表格和盈亏汇总。

## 5. 后台管理模块

### 5.1 用户管理

主要文件：

- `modules/backend_management/user_management/user_management.py`
- `modules/backend_management/user_management/permission_manager.py`

核心能力：

- 用户新增、编辑、删除。
- 密码 SHA-256 哈希。
- 管理员默认拥有所有权限。
- 模块查看和编辑权限。
- 操作日志。
- 会话记录。

主要数据表：

- `users`
- `permissions`
- `operation_logs`
- `user_sessions`

迁移判断：

- 权限模型保留。
- 登录认证需要改为 Web Token 或服务端 Session。
- 密码存储建议从普通 SHA-256 升级为 bcrypt 或 Argon2。
- 默认管理员 `管理员/admin` 只能用于初始化，生产部署必须强制修改。

### 5.2 数据管理

主要文件：`modules/backend_management/data_management/data_management.py`

核心能力：

- 创建备份。
- 查看备份。
- 恢复备份。
- 从本地文件恢复。
- 删除备份。
- 自动备份配置。
- 安全模式查看备份内容。
- 导出 CSV/JSON。
- 数据库路径配置。

迁移判断：

- 备份和恢复能力保留，但 Web 版必须限制为管理员操作。
- 文件选择框改为上传和下载接口。
- 数据库路径配置不建议暴露给普通 Web 用户，建议改为部署配置。
- 恢复前自动创建紧急备份的规则应保留。

## 6. 当前技术依赖和迁移影响

| 依赖或机制 | 当前用途 | Web 迁移处理 |
| --- | --- | --- |
| `tkinter/ttk` | 全部桌面界面 | 不复用，前端重写 |
| `sqlite3` | 本地数据存储 | 抽象为统一数据层 |
| `threading` | 自动刷新、预警监控 | 后端任务队列或调度器 |
| `requests` | 行情接口请求 | 后端行情服务 |
| `akshare` | 行情数据 | 后端行情服务 |
| `tqsdk` | 期权和期货行情 | 后端行情服务，需单独配置凭证 |
| `pandas/openpyxl` | 导出 Excel、数据处理 | 后端导出服务 |
| `messagebox` | 桌面弹窗提示 | 前端通知和确认框 |
| `filedialog` | 本地文件保存/恢复 | 浏览器上传下载 |
| `after()` | Tkinter 定时刷新 | 前端轮询、WebSocket 或后端推送 |

## 7. 迁移边界结论

可复用：

- 盈亏和资金收益计算。
- 期权 Greeks 计算。
- 期权到期日规则。
- 行情获取适配器。
- Excel 导出思路。
- 预警和指标计算规则。
- 现有数据表作为新模型输入。

需重构：

- 增删改查流程。
- 权限校验。
- 预警监控线程。
- 自动刷新机制。
- 备份恢复流程。
- 操作日志记录。

需重写：

- 全部 Tkinter 页面。
- 弹窗、双击事件、右侧内容区切换。
- 文件选择。
- Treeview 表格。
- 桌面窗口生命周期管理。
