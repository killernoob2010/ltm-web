# Web 版数据结构草案

## 1. 数据建模原则

当前桌面版使用多个 SQLite 文件和散落在模块内的建表语句。Web 版应改为统一数据库，并按业务域组织表。

本草案先不绑定最终数据库，但按 PostgreSQL/MySQL 可落地的方式设计：

- 主键使用自增整数或 UUID 均可，第一版建议自增整数降低迁移成本。
- 时间字段统一使用 `created_at`、`updated_at`，由服务端写入。
- 用户字段统一使用 `created_by`、`updated_by`，关联用户表。
- 金额、价格、汇率、手续费使用定点数类型。
- 高频查询字段建立索引。
- 交易类表保留操作日志，不依赖物理删除追溯历史。

## 2. 系统和权限表

### 2.1 `users`

来源：桌面版 `users`

建议字段：

| 字段 | 说明 |
| --- | --- |
| `id` | 用户 ID |
| `name` | 用户名 |
| `department` | 部门 |
| `password_hash` | 密码哈希 |
| `role` | 角色，例如管理员、普通用户 |
| `status` | 启用、停用 |
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |

变更点：

- 原字段 `password` 改为 `password_hash`。
- 建议密码算法从 SHA-256 升级为 bcrypt 或 Argon2。
- 新增 `status`，便于停用账号而不是删除。

### 2.2 `module_permissions`

来源：桌面版 `permissions`

建议字段：

| 字段 | 说明 |
| --- | --- |
| `id` | 权限 ID |
| `user_id` | 用户 ID |
| `module_code` | 模块标识 |
| `can_view` | 是否可查看 |
| `can_edit` | 是否可编辑 |
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |

唯一约束：

- `user_id + module_code`

### 2.3 `user_sessions`

来源：桌面版 `user_sessions`

建议字段：

| 字段 | 说明 |
| --- | --- |
| `id` | 会话 ID |
| `user_id` | 用户 ID |
| `token_hash` | Token 哈希 |
| `login_time` | 登录时间 |
| `last_activity` | 最后活动时间 |
| `expires_at` | 过期时间 |
| `status` | 活跃、已注销、已过期 |

### 2.4 `operation_logs`

来源：桌面版 `operation_logs` 及各台账自己的 operation logs

建议字段：

| 字段 | 说明 |
| --- | --- |
| `id` | 日志 ID |
| `user_id` | 操作人 |
| `module_code` | 模块 |
| `entity_type` | 对象类型 |
| `entity_id` | 对象 ID |
| `operation_type` | 操作类型 |
| `description` | 描述 |
| `before_data` | 操作前 JSON |
| `after_data` | 操作后 JSON |
| `created_at` | 操作时间 |

变更点：

- 建议合并系统日志、钢材出口日志、子公司日志为统一操作日志。
- 如第一版为降低风险，也可保留业务表内日志，并同步写入统一日志。

## 3. 上海均能台账

### 3.1 `sh_junneng_trades`

来源：桌面版 `trades`

建议字段：

| 字段 | 说明 |
| --- | --- |
| `id` | 交易 ID |
| `contract_month` | 合约月份 |
| `direction` | 方向 |
| `open_price` | 开仓价格 |
| `close_price` | 平仓价格 |
| `current_price` | 当前价格 |
| `trade_quantity` | 交易数量 |
| `hold_quantity` | 持仓数量 |
| `open_fee` | 开仓手续费 |
| `close_fee` | 平仓手续费 |
| `profit` | 盈亏 |
| `open_date` | 开仓日期 |
| `close_date` | 平仓日期 |
| `status` | 状态 |
| `is_closed` | 是否平仓 |
| `created_by` | 创建人 |
| `created_at` | 创建时间 |
| `updated_by` | 修改人 |
| `updated_at` | 修改时间 |

建议索引：

- `contract_month`
- `open_date`
- `close_date`
- `status`

### 3.2 `market_daily_prices`

来源：桌面版 `daily_prices`

建议字段：

| 字段 | 说明 |
| --- | --- |
| `id` | ID |
| `source_module` | 来源模块 |
| `info_type` | 指标或品种类型 |
| `contract_code` | 合约代码 |
| `trade_date` | 交易日期 |
| `open_price` | 开盘价 |
| `high_price` | 最高价 |
| `low_price` | 最低价 |
| `close_price` | 收盘价 |
| `volume` | 成交量 |
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |

唯一约束：

- `source_module + info_type + contract_code + trade_date`

说明：

- 桌面版在不同数据库里都有 `daily_prices`，Web 版建议统一成行情日线价格表。

## 4. 钢材出口套保台账

### 4.1 `steel_export_trades`

来源：桌面版 `steel_export_trades`

保留核心字段：

| 字段 | 说明 |
| --- | --- |
| `id` | 开仓交易 ID |
| `order_no` | 订单号 |
| `company` | 公司 |
| `account` | 账户 |
| `open_order_date` | 开仓挂单日期 |
| `open_date` | 开仓成交日期 |
| `contract_month` | 合约 |
| `direction` | 方向 |
| `open_amount` | 开仓数量 |
| `open_order_price` | 开仓挂单价格 |
| `open_deal_price` | 开仓成交价格 |
| `open_spread` | 开仓价差 |
| `open_fee_rate` | 开仓手续费率 |
| `open_fee` | 开仓手续费 |
| `open_fee_usd` | 美元手续费 |
| `remaining_amount` | 剩余数量 |
| `status` | 状态 |
| `order_mode` | 订单模式 |
| `exchange_rate` | 汇率 |
| `trade_type` | 交易类型 |
| `customs_declaration_no` | 报关单号 |
| `export_country` | 出口国家 |
| `settlement_currency` | 结算币种 |
| `cargo_value` | 货值 |
| `transportation_method` | 运输方式 |
| `created_by` | 创建人 |
| `created_at` | 创建时间 |
| `updated_by` | 修改人 |
| `updated_at` | 修改时间 |

### 4.2 `steel_export_close_trades`

来源：桌面版 `steel_export_close_trades`

保留核心字段：

| 字段 | 说明 |
| --- | --- |
| `id` | 平仓 ID |
| `trade_id` | 关联开仓交易 |
| `order_no` | 平仓订单号 |
| `close_account` | 平仓账户 |
| `close_order_date` | 平仓挂单日期 |
| `close_date` | 平仓成交日期 |
| `close_amount` | 平仓数量 |
| `close_order_price` | 平仓挂单价格 |
| `close_deal_price` | 平仓成交价格 |
| `close_spread_net` | 平仓净价差 |
| `settlement_profit_net` | 结算净收益 |
| `usd_settlement_profit` | 美元结算收益 |
| `rmb_settlement_profit` | 人民币结算收益 |
| `close_fee_rate` | 平仓手续费率 |
| `close_fee` | 平仓手续费 |
| `close_fee_usd` | 美元平仓手续费 |
| `close_direction` | 平仓方向 |
| `status` | 状态 |
| `remaining_amount_after` | 平仓后剩余数量 |
| `exchange_rate` | 汇率 |
| `usd_amount` | 美元金额 |
| `created_by` | 创建人 |
| `created_at` | 创建时间 |
| `updated_by` | 修改人 |
| `updated_at` | 修改时间 |

## 5. 子公司套保台账

第一版建议保留独立表：

- `subsidiary_trades`
- `subsidiary_close_trades`

字段结构与钢材出口套保台账基本一致。

保留独立表的理由：

- 降低从桌面版迁移数据的风险。
- 避免在业务差异尚未完全确认时过早合并。
- 后续如果两类台账规则稳定一致，再通过视图或统一服务抽象。

## 6. 期权交易台账

### 6.1 `option_trades`

来源：桌面版 `option_trades`

建议字段：

| 字段 | 说明 |
| --- | --- |
| `id` | 交易 ID |
| `trade_date` | 交易日期 |
| `underlying` | 标的 |
| `futures_contract` | 期货合约 |
| `expiry_date` | 到期日 |
| `strike_price` | 行权价 |
| `option_type` | 看涨或看跌 |
| `direction` | 买入或卖出 |
| `trade_quantity` | 数量 |
| `open_price` | 开仓价格 |
| `close_price` | 平仓价格 |
| `trade_amount` | 交易金额 |
| `strategy` | 策略 |
| `open_fee` | 开仓手续费 |
| `close_fee` | 平仓手续费 |
| `fee_per_lot` | 每手手续费 |
| `close_date` | 平仓日期 |
| `settlement_price` | 结算价 |
| `delta` | Delta |
| `gamma` | Gamma |
| `theta` | Theta |
| `vega` | Vega |
| `rho` | Rho |
| `created_by` | 创建人 |
| `created_at` | 创建时间 |
| `updated_by` | 修改人 |
| `updated_at` | 修改时间 |

建议新增：

- `status`：未平仓、部分平仓、已平仓。
- `parent_trade_id`：如果平仓记录继续采用新增一条记录的方式，可关联原开仓记录。

## 7. 信息汇总和预警

### 7.1 `info_calculated_data`

来源：桌面版 `calculated_data`

| 字段 | 说明 |
| --- | --- |
| `id` | ID |
| `info_type` | 指标类型 |
| `year` | 年份 |
| `month` | 月份 |
| `calc_date` | 计算日期 |
| `t_1_value` | T-1 值 |
| `t_2_value` | T-2 值 |
| `mean_value` | 均值 |
| `min_value` | 最小值 |
| `max_value` | 最大值 |
| `std_value` | 标准差 |
| `created_at` | 创建时间 |

唯一约束：

- `info_type + year + month + calc_date`

### 7.2 `trading_days`

来源：桌面版 `trading_days`

| 字段 | 说明 |
| --- | --- |
| `date` | 交易日 |
| `market` | 市场，第一版可为空或默认 |

### 7.3 `alert_settings`

来源：桌面版 `alert_settings`

| 字段 | 说明 |
| --- | --- |
| `id` | 预警 ID |
| `info_type` | 指标类型 |
| `contract_year` | 合约年份 |
| `contract_month` | 合约月份 |
| `alert_value` | 预警值 |
| `direction` | 触发方向 |
| `status` | 启用或停用 |
| `created_by` | 创建人 |
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |

### 7.4 `alert_history`

来源：桌面版 `alert_history`

| 字段 | 说明 |
| --- | --- |
| `id` | 历史 ID |
| `alert_id` | 预警规则 ID |
| `alert_time` | 触发时间 |
| `current_value` | 当前值 |
| `alert_value` | 预警值 |
| `direction` | 触发方向 |
| `status` | 未读、已读 |

## 8. 事中风险监控

### 8.1 `strategy_groups`

来源：桌面版 `strategy_groups`

| 字段 | 说明 |
| --- | --- |
| `id` | 策略组 ID |
| `group_name` | 策略组名称 |
| `created_by` | 创建人 |
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |

唯一约束：

- `group_name`

### 8.2 `strategy_positions`

来源：桌面版 `strategy_positions`

| 字段 | 说明 |
| --- | --- |
| `id` | 持仓 ID |
| `group_id` | 策略组 ID |
| `variety` | 品种 |
| `variety_name` | 品种名称 |
| `direction` | 方向 |
| `open_price` | 开仓价 |
| `quantity` | 数量 |
| `multiplier` | 乘数 |
| `contract` | 合约 |
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |

## 9. 后台数据管理

建议新增 Web 专用表：

### 9.1 `backup_jobs`

| 字段 | 说明 |
| --- | --- |
| `id` | 备份任务 ID |
| `backup_name` | 备份名称 |
| `description` | 描述 |
| `status` | 成功、失败、进行中 |
| `file_path` | 备份文件路径 |
| `file_size` | 文件大小 |
| `created_by` | 创建人 |
| `created_at` | 创建时间 |

### 9.2 `import_export_jobs`

| 字段 | 说明 |
| --- | --- |
| `id` | 任务 ID |
| `job_type` | 导入或导出 |
| `module_code` | 模块 |
| `status` | 状态 |
| `file_path` | 文件路径 |
| `error_message` | 错误信息 |
| `created_by` | 创建人 |
| `created_at` | 创建时间 |

## 10. 待确认问题

这些问题不阻塞第一版架构，但会影响最终表结构：

- 钢材出口套保和子公司套保是否长期保持独立业务，还是未来合并为统一套保台账。
- 期权平仓是否继续沿用“新增平仓记录并修改原记录”的方式，还是拆分为开仓表和平仓表。
- 行情日线数据是否需要按来源记录，例如 AkShare、Sina、TQSDK。
- 预警通知是否需要用户级已读状态，还是全局已读状态即可。
- 备份恢复是否允许在 Web 页面直接触发生产数据库恢复。
