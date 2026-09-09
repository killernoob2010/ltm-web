# Agent V2 代码盘点与升级缺口

日期：2026-09-09。只读代码证据基线：origin/staging `e343d765b1e02bf793c5aa942b99cbc5db4ad8db`。未调用数据库、真实模型、行情、企微或生产接口；下列存在性不等于运行验收。

## 1. 已发现可复用能力

| 能力 | 代码证据 | 复用边界 |
|---|---|---|
| 成交有效事实及分页 | `backend/app/trading_effective_facts.py`：query_effective_trades | 封装宏源范围、权限及全量聚合，不能只取页面数据 |
| 最新有效持仓 | 同文件：query_effective_positions、infer_positions_from_fills | 结算基线、后续成交与 FIFO 已有实现；不是任意历史时点查询承诺 |
| 浮盈亏 | `backend/app/trading_valuation.py`：calculate_live_position_floating_pnl | 与可含剩余开仓费用的另一估值函数有区别，适配器须复用正确路径 |
| 单合约及持仓 Greeks | 同文件：calculate_black76_option_metrics、calculate_option_position_valuation、calculate_option_display_greeks | 已有单位指标、数量乘数加权敞口及展示转换；不能混用 |
| 全量事实持仓估值 | `backend/app/trading_management.py`：query_fact_position_valuation、/facts/positions/valuation | 已接统一有效持仓与浮盈亏；该路径尚未输出完整 Greeks |
| 已归类期权持仓估值 | 同文件：业务 positions 路径筛选 assignment_status == classified，调用 calculate_option_position_valuation | 有 Greeks 汇总，但不是全量 WH＋结算有效持仓风险结果，不能直接作为 V2 全量数据源 |
| 原 Agent 会话与幂等 | `backend/app/closing_review_agent.py`：process_message；closing_review_agent_store.py | 可复用存储思想，需核实 V2 数据结构兼容性 |
| 模型网关 | `backend/app/closing_review_model_gateway.py` | 当前 resolve_intent，不是自由工具执行循环 |
| 日常摘要及调度 | closing_trading_review.py、closing_review_scheduler.py | 保持已有确定性任务，不扩大通知 |

## 2. 必须改变或补充

1. **旧路由固定范围**：process_message 最终调用 build_option_daily_review；权限绑定 closing_review.agent 与 trading.options，完成检查固定账户/品种。V2 需使用各业务工具对应权限，不能只保留期权权限覆盖全部期货。
2. **历史归属缺口**：query_effective_positions 在历史分支前调用 _position_assignment_map；该映射读取当前归属。因此现有路径不能直接满足按当时归属。当前无标签按未分组；未来需要历史有效期或快照证据后再开放历史分组。
3. **历史时间能力有限**：end_date 分支选择该日 active 结算快照；不能由此声称任意盘中时间点可还原。先发布实际支持的日期能力目录。
4. **Greeks 单位**：calculate_option_position_valuation 使用方向×数量×乘数；calculate_option_display_greeks 对 Theta /360、Vega/Rho /100。风险工具必须返回明确单位与种类，避免把显示值当原始敞口。
5. **品种与到期支持**：存在 _standard_dce_option_expiry 特定合约解析和工作日推算兜底，另有行情合约到期时间路径。需核验实际宏源各品种的来源覆盖，不能泛化工作日兜底为可靠交易日历。
6. **缺失覆盖率**：现有汇总跳过 None 后求和，需为风险工具补每项指标覆盖数量、缺失列表及部分状态。不能称为完整组合指标。
7. **快照一致性**：现有持仓生成时间及行情时间不能自动组成跨工具统一快照；需设计结果引用与版本绑定。
8. **MCP/企微/搜索**：本次对 backend/app 与 requirements 的定向搜索未发现相关接入证据；按新增适配规划，不推断仓库外没有其他服务。
9. **工具循环和推论**：旧网关、投影器、固定模板需增加兼容的 V2 路径；不删除原标准流程与测试。
10. **授权与数据出口**：新增企微身份映射、群共享范围、搜索脱敏出口、DeepSeek 最小上下文和缓存权限隔离。
11. **全量 Greeks 前置服务**：将全量有效持仓接入已有计算函数，未归类不能遗漏；来源协调与去重沿用事实层，已平仓交易不计当前敞口。只补共享业务计算链路，不要求先改造期权台账页面或录入归类功能。用户已确认此修订。
12. **Eval 结构**：以 Agent＋Harness 通用能力矩阵为主线，工具正确性和端到端答案共同验收。业务示例仅作测试载体，不固定题目、措辞或工具调用序列；加入新组合、留出问题、异常变体和实际使用回归。用户已确认此修订。

## 3. 实施前工程核验清单

- 宏源唯一账户映射及现有有效权限如何在工具服务中执行。
- 最新持仓+估值同口径固定样本，全量行数、合计和缺行情状态。
- 实际合约清单对应到期、乘数、行情及 Greeks 覆盖；不为通过测试改数据。
- 对比未归类及已归类期权在统一全量结果中的覆盖，核对当前持仓数量、方向与敞口；同一输入下旧计算与新服务公式一致，不继承旧台账的子集范围。
- 是否已有可调用情景重定价入口；本次仅确认基础定价函数，未确认业务情景工具。
- 企微身份绑定与消息通道、模型工具调用、MCP 连接及公开检索供应商的当前官方合同；实施时再核验，不依据记忆写 API。
- 旧权限、幂等、标准回答、调度的回归影响。

## 4. 已有相关测试线索

`tests/test_trading_valuation.py` 包含单合约样本、持仓加权、卖方符号、展示单位、同快照行情案例；`tests/test_trading_effective_facts.py` 覆盖持仓推算；`tests/test_trading_management.py` 包含期权估值；`tests/test_closing_review_*` 包含原 Agent 相关回归。

本轮未运行测试，不宣称这些测试当前通过。下一阶段选择对应固定样本与通用能力 Eval，并以真实工具、真实模型及企微路径补齐验收。现有 Greeks 等业务测试不代替通用 Agent＋Harness 评估，通用执行通过也不代替计算和业务结果正确性。

## 5. 结论与本轮边界

可复用的数据及计算代码已足够支撑 V2 详细设计；主要新增为受控工具适配、字段语义、执行循环、公开研究与数据出口、企微及 Eval。不能仅接上 MCP 就宣称全部分析可用。

当前无需用户再确认组别或风险阈值；不补造标签、不预设风险等级。账户运行配置或计算覆盖存在真实阻塞时，再提出具体问题。
