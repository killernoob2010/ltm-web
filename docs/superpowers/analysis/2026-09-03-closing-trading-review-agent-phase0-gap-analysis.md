# 收盘交易复盘 Agent Phase 0 Gap Analysis

日期：2026-09-03

范围：仅当前隔离 worktree 的仓库代码、合成测试 fixture 和既有测试结构。未打开或读取真实账户结算单原文，未连接生产数据库、生产服务或真实交易终端。

权威设计：`/Users/wangjingze/Documents/轻量化交易管理系统WEB/docs/superpowers/specs/2026-09-03-closing-trading-review-agent-v1-design.md`。本任务以用户委托中“业务设计已确认、继续 Phase 0 + Phase 1”的授权作为实现前提，不修改原设计文档。

## 已确认

| 能力 | 当前证据 | Phase 1 处理 |
| --- | --- | --- |
| FastAPI 入口和权限 | `backend/app/main.py` 注册模块 router；`backend/app/permissions.py` 已有 `trading.options -> trading_options` 映射；交易管理 router 自带登录校验 | 新增独立只读 router，先执行现有 `trading.options/view` 权限，再读取账户 |
| 结算单解析 | `backend/app/trading_settlement.py` 可识别日结/月结 TXT、成交/平仓/持仓/行权区段、账户和日期；合成 fixture 覆盖 GB18030 和期权事件 | 复用已落库事实，不重复解析原文 |
| 真实平仓盈亏 | `trading_management.confirm_settlement_import` 调用 `_statement_close_pnls`，按 `trading_contract_specs` 重算 `fact_close_pnl`；`trading_close_facts` 保留平仓日期和来源行 | 按指定日、宏源账户、铁矿石期权汇总 `fact_close_pnl`，不加手续费 |
| 持仓事实和结算口径 | `trading_position_snapshots` 保存指定快照日、方向、手数、均价、估值价；结算单解析把结算价标为 `settlement_reference` | 只使用日结快照的日终结算价计算浮盈浮亏；Schema 明确不等于 15:00 最后一笔成交价 |
| 合约规格 | `trading_contract_specs` 有大商所铁矿石期权 `i`、乘数 100 的受控配置；现有导入会在缺规格时拒绝计算 | 读取并把规格作为证据；缺失时返回数据异常，不猜乘数 |
| 数据库兼容 | `db.init_db()` 已建立交易事实、来源行、批次、合约规格和权限表，SQLite/PostgreSQL 均有现成定义 | 不新增迁移、不写业务数据，仅只读查询 |
| 测试基线 | 结算单、交易管理、估值、总览、权限目标测试共 `163 passed, 5 warnings` | 追加合成数据服务/合同/API 测试，单独报告全量回归 |

## Gap / 本切片补齐

1. 现有交易管理查询按事实视图工作，没有一个同时绑定“指定日期 + 宏源账户 + 铁矿石期权”的报告合同；新增 `closing_trading_review` 服务作为确定性计算边界。
2. 现有持仓查询不输出按月份、Call/Put、买卖方向和行权价区间的复盘分组，也没有 Call/Put 净卖手数、吨数和万吨换算；新增动态分组和合约级明细。
3. 现有 API 没有统一返回 `data_as_of`、`source`、`calculation_version`、`rule_version`、`freshness`、`completeness`、`warnings`、`evidence_refs`；新增强类型 Pydantic Schema，并在每个关键数值上携带元数据。
4. 现有事实层有 `trading_fact_source_differences`，但业务查询没有把来源差异和关键来源行缺失统一映射为受控结果；新增 `data_anomaly` 状态，异常时不输出确定性数值结论。
5. 现有数据可以保存月结事实，但月结通常只有月末持仓快照；没有可靠的历史每日估值查询。目标日缺日结而只有月结时只能返回已能证明的平仓事实，并把持仓/浮盈浮亏标为未知或部分，不能报“无持仓”。
6. 现有权限按交易模块管理，尚无独立 Agent Task Profile 权限；Phase 1 复用 `trading.options/view`，不新增权限模块。独立 Agent 权限、任务路由、Harness、Trace 和模型接入留 Phase 2+。

## 暂不可确认 / 不纳入本次实现

- 未读取真实宏源日结/月结文件原文，因此真实文件版本差异、字段变体和真实数据日期覆盖仍需后续在受控环境单独核验；本次只依赖已有解析代码和脱敏/合成 fixture。
- 中国期货交易日历、夜盘归属、“昨天/今天”自然语言解析、15:05 调度可靠性尚无本切片所需的已验证数据源；Phase 1 API 只接收明确指定日期，不用相邻日期替代。
- 文华 15:00 最后一笔成交价、历史行情缓存和月结对账流程尚未接入；本次不把实时行情模块或结算价改称收盘最后成交价。
- 企业微信、网页聊天界面、DeepSeek/模型网关、Harness、定时任务、生产配置/数据和任何交易操作均不在范围。

## 结论

现有仓库具备复用 Phase 1 所需的事实、结算价、合约规格、账户权限和数据库基础，不需要新增数据库迁移或接触生产数据。可以安全实现一个只读、确定性的 Phase 1 纵向切片；真实数据字段/覆盖和 15:00 口径仍是后续验收依赖，不能由本地合成测试替代。
