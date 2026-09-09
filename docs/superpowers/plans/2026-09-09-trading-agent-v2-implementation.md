# 交易 Agent V2 Implementation Plan

> 2026-09-09 V2.1 修订：已确认复用现有 Render 付费实例。部署、依赖、运行保护和后续验收以[新版方案](2026-09-09-trading-agent-v2-shared-render-implementation.md)为准；本文其他业务与接口合同保留。旧 T11 的另找常驻主机安排已被替代，新版适配尚未完成。

> **执行方式：** 单主 Agent 按 `superpowers:executing-plans` 逐任务执行；用户未授权子 Agent，不使用默认子 Agent 工作流。T0–T10 已在隔离分支完成本地实现与回归，以下复选框保留为逐项验收追踪；T11 仍需真实 Staging 条件。

**Goal:** 宏源全量有效持仓、交易和可靠计算通过 MCP 接入 DeepSeek，支持开放查询、公开研究与证据推论，在企微本人私聊完成真实验收。

**Architecture:** 既有 Web 服务仅增加 V2 入队/查询；独立 Python Agent worker 运行 Harness、企微长连接和 loopback MCP。事实和计算复用既有业务层，新增全量 Greeks 接入；不先改期权台账页面。

**Tech Stack:** Python 3.12 候选、既有 FastAPI/Pydantic/SQLite/PostgreSQL、官方 MCP Python SDK 2.2.0 候选、企业微信团队 Python SDK 1.0.2 候选、DeepSeek Chat Completions、Brave Web Search 适配。T0 锁定兼容依赖；不购买服务。

**Spec:** [业务设计](../specs/2026-09-09-trading-agent-v2-upgrade-design.md)、[技术合同](../specs/2026-09-09-trading-agent-v2-technical-contracts.md)、[代码缺口](../analysis/2026-09-09-trading-agent-v2-gap-analysis.md)。冲突时按用户已确认范围修订文档，不自行扩大范围。

## Global Constraints

- 首批宏源全部期货与期权；“现在”默认最新可用；无标签未分组，历史按当时归属但无证据不还原。
- 结算与WH通过有效事实层协调，禁止简单相加；已平仓历史交易不计当前风险。
- 全量 Greeks 服务是前置，期权台账页面/录入改造不是前置。
- 仅查询与计算；不自动交易、不改归属、不生成任意SQL/代码，不新增融资或库存内部工具。
- 现有系统权限＋服务端宏源范围；当前 scope_filter=all 不能当账户隔离。
- DeepSeek最小必要数据；搜索不带私有数据；企微只本人单聊，群和外部Agent后续接入。
- Eval以Agent＋Harness通用能力为主，同时验证工具正确性和最终结果，业务示例不是固定题库。
- 时间到秒；正确权限、数字、数据状态是硬门槛。真实模型/企微未验收不能称完整交付。
- 不改main/Production、现有render.yaml生产定义；新增付费资源或环境映射不明确时停止相应动作。

## 0. 基线与实施边界

设计代码基线 e343d765b1e02bf793c5aa942b99cbc5db4ad8db，设计分支 codex/agent-v2-design-20260909。本计划编写时尚未执行开发测试；当前执行记录见 `docs/superpowers/analysis/2026-09-09-trading-agent-v2-execution.md`。实施前 fetch 并检查最新 origin/staging；把设计提交带入干净实施 worktree，保留原目录所有用户改动。

已有主模块：trading_effective_facts.py、trading_valuation.py、trading_management.py、permissions.py、closing_review_agent.py/store.py/model_gateway.py/scheduler.py、db.py。不得导入 main.py 来取得工具运行环境（其启动会触发调度和初始化）；worker 仅导入明确业务模块。

### 文件责任图

| 文件 | 责任 |
|---|---|
| 新增 backend/app/trading_agent/__init__.py | 包入口，无启动副作用 |
| 新增 contracts.py、catalog.py | 类型/Schema与业务字段说明 |
| 新增 auth.py | 宏源范围、用户/令牌/配对与每次核权 |
| 新增 schema.py、store.py | 六张V2附表、租约、不可变结果、事件与保留规则 |
| 新增 facts.py | 全量事实快照、分页/统计/比較与证据 |
| 新增 risk.py | 复用Greeks、覆盖率、情景重定价 |
| 新增 tools.py、mcp_server.py、mcp_client.py | 单注册表、MCP传输及认证适配 |
| 新增 model.py、prompts.py、answer.py | DeepSeek工具回合、最小上下文、结构化答案及引用渲染 |
| 新增 harness.py、worker.py | 有界循环、持久队列、运行生命周期 |
| 新增 research.py | 搜索供应商、公开查询出口、正文安全读取 |
| 新增 wecom.py、routes.py | 企微文本接入与网页异步API |
| 修改 trading_effective_facts.py | account_ids贯穿取数和汇总，默认旧行为不变 |
| 修改 main.py | 注册V2网页路由，无worker或MCP公网挂载 |
| 修改 frontend/closing_review_agent.js | 仅当服务端能力为V2时采用新API及任务轮询 |
| 新增 requirements-agent-v2.in/.lock | 独立worker环境，现有Web依赖保持不变 |
| 新增 scripts/migrate_agent_v2.py、run_agent_v2_evals.py | 显式迁移与精简Eval执行 |
| 新增 tests/agent_v2/ 与 evals/trading_agent_v2/ | 单元/集成与通用能力案例，禁止原始账户明细入库 |

所有上述短文件名位于 backend/app/trading_agent/。每任务只改列出的文件和直接关联测试，普通格式整理不纳入。

## T0：基线、外部合同与依赖可运行性

**Files:** requirements-agent-v2.in/.lock、tests/agent_v2/conftest.py、docs/superpowers/analysis/2026-09-09-trading-agent-v2-gap-analysis.md。
**Produces:** 干净环境、固定解析依赖、测试数据库隔离、真实接入前提清单。

- [ ] fetch/rebase核对代码差异，运行现有目标回归作为基线；既有失败单独记录，禁止直接归为新功能缺陷。
- [ ] 在独立venv解析 requirements 加 MCP/企微候选包，生成精确锁；执行 `pip check`。若需升级Web框架才能安装，先检查独立worker约束，不改Web框架绕过问题。
- [ ] 核验安装后的 `from mcp.server import MCPServer` 与 `from aibot import WSClient, WSClientOptions`；启动仅本地演示MCP并实际 list/call，确认协议协商。演示不连接业务DB、不进入生产文件。
- [ ] 记录当前官方API/SDK与技术合同一致性；核实试点 username唯一、企微机器人可创建、现有key/额度是否存在，仅记录是否具备不读取打印凭据。没有条件只阻塞真实联调，其他本地开发继续。
- [ ] 创建conftest隔离：monkeypatch db.connect到临时SQLite；禁止未标记live测试的requests/aiohttp网络调用；fixture为合成记录，不复制真实账户导出。沿用现有tests内建库助手。

```bash
python -m pip check
python -c 'from mcp.server import MCPServer; from aibot import WSClient, WSClientOptions'
env -u DATABASE_URL python -m pytest -q tests/test_trading_effective_facts.py tests/test_trading_valuation.py tests/test_closing_review_agent.py
```

交付：更新盘点中的实际依赖与接入状态，提交 `chore: prepare isolated agent v2 runtime`。本任务不要求用户此时发送任何密钥。

## T1：公共合同、属性目录及账户范围

**Files:** contracts.py、catalog.py、auth.py；修改 trading_effective_facts.py；tests/agent_v2/test_contracts.py、test_scope.py。
**Interfaces:** `resolve_principal(user_id,channel,conversation_id,execution_id)->Principal`、`authorize(principal,resource)->Principal`、`EffectiveFactFilters(account_ids:tuple[int,...]|None=None)`。

- [ ] 写失败测试：额外user_id参数拒绝、空账户不等于全账户、非宏源隔离、停用/撤权立即生效、缺字段单位不合格。
- [ ] 在结算、WH临时事实、持仓基线/快照、COUNT、聚合所有SQL路径绑定 account_ids；为空时短路拒绝。保留默认None的旧调用行为。
- [ ] 构建字段枚举与MetricValue/ToolEnvelope，严格区分业务状态与权限错误；catalog仅返回当前真实能力。
- [ ] 将内部期货/期权、方向、日期值转换为现有事实层值；不能靠模型填SQL或账户编号。

```python
def test_empty_scope_never_means_all(scope_db):
    from backend.app.trading_effective_facts import EffectiveFactFilters
    from backend.app.trading_agent.auth import require_account_scope
    import pytest
    with pytest.raises(PermissionError):
        require_account_scope(EffectiveFactFilters(account_ids=()))
```

新增 `require_account_scope(filters)->tuple[int,...]` 到auth.py，在进入全部V2事实查询之前调用；旧调用不调用此V2检查。scope_db fixture造两个账户且每个至少一条WH和一条结算，断言未授权行、count、summary都不出现。

运行 `env -u DATABASE_URL python -m pytest -q tests/agent_v2/test_contracts.py tests/agent_v2/test_scope.py tests/test_trading_effective_facts.py`。预期先因新合同缺失失败，实施后全部通过。提交 `feat: define agent contracts and enforce account scope`。

## T2：持久化、任务幂等、执行令牌与证据

**Files:** schema.py、store.py、auth.py、scripts/migrate_agent_v2.py；tests/agent_v2/test_store.py、test_schema.py。
**Interfaces:** `enqueue(user,conversation_id,request_id,text,channel)->int`、`claim_next(worker_id)->int|None`、`save_result(principal,envelope,rows)->UUID`、`load_result(principal,ref)->StoredResult`、`issue_grant(principal)->str`、`resolve_grant(token)->Principal`。

- [ ] 按技术合同六张表实现SQLite/Postgres显式幂等迁移；同任务封装原claim_user_task，request_hash不同返回409。
- [ ] 写竞争与撤权测试；result和event在短事务中落盘；令牌随机256-bit、hash保存、5分钟TTL、任务终结撤销。
- [ ] 配对码10分钟、一次性且最多5次失败；只本人web会话可申请，message中配对码不存业务聊天。
- [ ] 新表RLS及grants在同一迁移执行；迁移脚本默认dry-run，只在明确Staging且已有备份/恢复检查后允许apply。不调用完整init_db，不改交易事实表。
- [ ] 实现租约及90/365天保留。结果过期返回result_expired；崩溃running不自动重跑模型。

```python
def test_request_id_cannot_be_reused_for_different_text(store_case):
    import pytest
    store = store_case.store
    request_id = '00000000-0000-4000-8000-000000000001'
    task = store.enqueue(store_case.user, store_case.cid, request_id, '查持仓', 'web')
    assert store.enqueue(store_case.user, store_case.cid, request_id, '查持仓', 'web') == task
    with pytest.raises(store.RequestConflict):
        store.enqueue(store_case.user, store_case.cid, request_id, '改为另一问题', 'web')
```

HTTP接口必须实际验证UUID；fixture不放宽协议校验。`RequestConflict`定义在store.py。运行 `env -u DATABASE_URL python -m pytest -q tests/agent_v2/test_store.py tests/agent_v2/test_schema.py`，Staging migration验收另在T11执行。提交 `feat: persist agent tasks evidence and scoped grants`。

## T3：全量事实快照与灵活统计

**Files:** facts.py、contracts.py、catalog.py；tests/agent_v2/test_facts.py。
**Interfaces:** `capture_positions(principal,query,quote_provider)->ToolEnvelope`、`capture_facts(principal,kind,start_date,end_date,filters)->ToolEnvelope`、`summarize(principal,result_ref,group_by,metrics,order_by=None,descending=True)->ToolEnvelope`、`compare(principal,left_ref,right_ref,metrics)->ToolEnvelope`、`read_page(principal,result_ref,page,page_size,fields)->ToolEnvelope`。

- [ ] 固定一个包含已归类/未归类、结算覆盖WH重复、200+行的合成样本；预先列独立数量和盈亏期望。
- [ ] 一致性读事务物化全量，事务外取行情；保存quote深拷贝、source hash和真实时间。复制既有业务语义，不复制一套来源仲裁。
- [ ] 实时浮盈亏使用calculate_live_position_floating_pnl；现有接口返回的all_items必须在pop前由独立服务接住，不复用已分页结果重算。
- [ ] 成交与已核验平仓事实分别捕获；普通平仓及行权/履约/放弃沿用现有规范事实，不重复计算、不以临时事件造真实平仓盈亏。
- [ ] 统计以完整结果引用为输入，未知不补零；历史无归属版本就不提供历史分组；不同估值口径比较返回not_comparable。

```python
def test_summary_does_not_use_preview_only(fact_case):
    result = fact_case.capture_positions(rows=205, preview=20)
    summary = fact_case.summarize(result.result_ref, ['asset_type'], ['quantity'])
    assert summary.metrics['quantity'].value == '205'
    assert summary.metrics['quantity'].covered_rows == 205
```

fact_case 在conftest中通过真实facts函数、临时DB和确定报价组装，不能用stub直接返回期望值。运行 `env -u DATABASE_URL python -m pytest -q tests/agent_v2/test_facts.py tests/test_trading_effective_facts.py`。提交 `feat: expose immutable full-scope trading facts`。

## T4：全量 Greeks 与明确情景计算

**Files:** risk.py；允许为无副作用包装修改 trading_valuation.py；tests/agent_v2/test_risk.py、test_scenario.py。
**Interfaces:** `position_risk(principal,snapshot_ref,include_futures=False)->ToolEnvelope`、`scenario(principal,snapshot_ref,shocks,method='black76_reprice_v1')->ToolEnvelope`。

- [ ] 先写已归类/未归类同合约同参数均计入的失败测试；覆盖方向、乘数、缺数据及不同标的分离。
- [ ] 读取T3冻结快照，复用calculate_option_position_valuation、calculate_black76_option_metrics；不调用期权台账分类查询，不再次取最新行情。
- [ ] unit/display/exposure分别返回单位；各项独立覆盖率；不把缺一个合约的Gamma合计标complete。
- [ ] 为每个实际品种核实合约规格/到期输入和Black76适用性；无有效支持则partial，不能将工作日猜测当交易日历。需要新模型时返回主Agent修订方法，不能自动发明。
- [ ] 情景按技术合同公式做base/shock重定价，验证零冲击为0、同标的期货线性变化、T/IV/F越界拒绝。保持原浮盈亏与情景变化分开。

```python
def test_unclassified_position_contributes_to_exposure(risk_case):
    result = risk_case.evaluate([
        {'classified': True, 'quantity': 2, 'unit_delta': .4},
        {'classified': False, 'quantity': 3, 'unit_delta': .4},
    ], multiplier=100, direction='buy')
    assert float(result.metrics['delta_exposure'].value) == 200
    assert result.metrics['delta_exposure'].covered_rows == 2
```

risk_case 必须调用risk.position_risk并注入两条快照，不直接调用mock聚合。运行 `env -u DATABASE_URL python -m pytest -q tests/agent_v2/test_risk.py tests/agent_v2/test_scenario.py tests/test_trading_valuation.py`。提交 `feat: calculate Greeks for all effective positions`。

## T5：统一工具注册与真实 MCP

**Files:** tools.py、mcp_server.py、mcp_client.py、auth.py；tests/agent_v2/test_tools.py、test_mcp.py。
**Interfaces:** `dispatch(principal,name,args)->ToolEnvelope`、`build_mcp_app()->ASGIApp`、`MCPToolClient.call_tool(name,args,grant)->ToolEnvelope`。所有名称/参数从合同表注册，不允许模型注册新工具。

- [ ] tests用真实SDK server/client在loopback临时端口初始化、list_tools和call_tool，验证结构化输出；同时保留dispatcher直接测试。
- [ ] 参数Schema、权限、结果归属在服务端执行；readOnlyHint只是元数据，不作安全依据。
- [ ] ASGI middleware每请求resolve_grant，将Principal放入请求上下文；并发不同令牌不会污染ContextVar；固定Host/Origin限制。
- [ ] 生命周期正确进入session_manager；关闭无悬挂任务。禁止挂main公网、不接受外部传入MCP URL；worker client endpoint由配置固定loopback。

```python
async def test_mcp_rejects_expired_grant(mcp_case):
    response = await mcp_case.raw_request(token=mcp_case.expired_token,
        method='tools/call', params={'name':'query_positions','arguments':{}})
    assert response.status_code == 401
    assert mcp_case.fact_reads == 0
```

运行 `env -u DATABASE_URL python -m pytest -q tests/agent_v2/test_tools.py tests/agent_v2/test_mcp.py`，测试套件启动真实本地MCP不是生产网络。提交 `feat: serve authenticated trading tools over MCP`。

## T6：DeepSeek 网关与结构化答案

**Files:** model.py、prompts.py、answer.py；tests/agent_v2/test_model.py、test_answer.py。
**Interfaces:** `DeepSeekModel.next_turn(messages,tools,timeout_seconds)->ModelTurn`，ModelTurn包含tool_calls/content/usage/finish_reason；`render_answer(principal,draft,store)->str`。

- [ ] requests POST `/chat/completions`，沿用默认flash模型；显式thinking disabled，tools为MCP目录转换后的function schema；tool_call_id逐一回传。
- [ ] JSON参数重复键/非有限数/未知工具拒绝；401/403立即失败、429/5xx/连接超时可有限重试，预算由Harness统一扣减。
- [ ] prompt明确最新/历史、属性、内部证据与外部推论边界，不枚举固定业务问句；不暴露数据库表、身份令牌和系统路径。
- [ ] 从最近6轮及最小工具投影组成上下文；新任务不携带旧工具对话的原始协议messages。旧数字仅作结果引用。
- [ ] AnswerDraft解析与数字引用渲染；未知引用、越权引用、非固定依据的业务数字不能通过。保留通用知识自然回答，不能把所有数字都误判为账户数据。

```python
def test_answer_rejects_foreign_fact_reference(answer_case):
    import pytest
    from backend.app.trading_agent.answer import InvalidEvidence
    with pytest.raises(InvalidEvidence):
        answer_case.render(text='浮盈{{fact:foreign#/metrics/floating_pnl}}')
```

运行 `env -u DATABASE_URL python -m pytest -q tests/agent_v2/test_model.py tests/agent_v2/test_answer.py`。再用获准配置执行一次最小真实工具回合，未具备key只保留未验收状态。提交 `feat: add DeepSeek tool rounds and evidence rendering`。

## T7：公开检索与数据出口

**Files:** research.py、tools.py；tests/agent_v2/test_research.py、test_egress.py。
**Interfaces:** `validate_public_query(query,private_context)->PublicQuery`、`search_public(principal,query,freshness)->ToolEnvelope`、`read_public(principal,source_ref)->ToolEnvelope`。

- [ ] 用私有金额、编号、客户、内部地点、编码形式和拼接变体写泄露拒绝测试；公共日期和方法正常通过。
- [ ] 独立公开子问题生成，再出口校验；无法确定的搜索不发出，请用户提供不含私有信息的检索词或返回部分答案。
- [ ] Brave固定endpoint、token在header、5结果；不把模型最终回答服务作为事实来源；key不可用返回未配置。
- [ ] 受控正文读取，只读已登记来源；每次DNS/重定向/连接校验，验证私网和重绑定拒绝，剥离外部文本指令。禁止Cookie、URL凭据、跨域凭据转发。
- [ ] HTTP错误、PDF未解析、只有摘要时真实标识；测试搜索结果中的恶意指令不能触发工具调用或数据出站。

```python
def test_private_amount_never_leaves_search_boundary(egress_case):
    import pytest
    with pytest.raises(egress_case.QueryRejected):
        egress_case.search('本账户亏损987654.32怎样评价', private_values=['987654.32'])
    assert egress_case.sent_requests == []
```

QueryRejected 定义于research.py，fixture封装真实validate调用并拦截HTTP。运行 `env -u DATABASE_URL python -m pytest -q tests/agent_v2/test_research.py tests/agent_v2/test_egress.py`；联网真实案例在有获准key后执行。提交 `feat: isolate public research from private data`。

## T8：有界 Harness、任务恢复及通用路由

**Files:** harness.py、worker.py、store.py；tests/agent_v2/test_harness.py、test_worker.py。
**Interfaces:** `RuntimeDeps(store,model,mcp,clock)`、`async run_task(task_id,deps)->AnswerDraft`、`async worker_main()->None`。

- [ ] scripted model测试必须覆盖新问法组合、无需工具知识回答、缺数据部分回答、追问、越权、工具异常；不按题材关键词硬编码分支。
- [ ] 循环：取得运行租约→生成执行令牌→list受控工具→模型回合→检查预算/参数→工具逐个执行→记录→继续或验证最终答案。每任务结束撤销令牌。
- [ ] 8工具/2search/6模型/90秒，重试与修复计入；批量请求超额不执行半批。每个工具结果更新后再次检查取消与权限。
- [ ] worker串行消费queued、心跳租约，启动不重跑过期running任务；绝不导入main触发全项目scheduler。
- [ ] 最终校验失败最多一次修复；没有预算用确定性已有结果结束。无需把模型自述思考过程写入event。

```python
async def test_budget_stops_repeated_tool_requests(harness_case):
    result = await harness_case.run(model_behavior='repeat_tool_forever')
    assert harness_case.tool_calls <= 8
    assert harness_case.model_calls <= 6
    assert result.status in {'partial', 'temporarily_unavailable'}
    assert harness_case.grant_revoked
```

运行 `env -u DATABASE_URL python -m pytest -q tests/agent_v2/test_harness.py tests/agent_v2/test_worker.py`。提交 `feat: orchestrate bounded auditable agent execution`。

## T9：网页复用与企微本人双向交互

**Files:** routes.py、wecom.py、worker.py、main.py、frontend/closing_review_agent.js；tests/agent_v2/test_routes.py、test_wecom.py、tests/agent_v2_frontend.test.mjs。
**Interfaces:** 技术合同第7节REST；`handle_text(frame,client)->None`，调用store.enqueue，依据状态reply_stream。

- [ ] main.py Agent菜单过滤改为旧版或V2任一启用且原模块权限满足；V2只允许试点用户，其他人保持既有V1行为。V2 capability flag关闭时网页沿用V1，开启时新建V2 web会话，POST后轮询task（1、2、3秒渐增至5秒）；90秒结束或失败离开loading并允许新请求重试。
- [ ] 首轮企微只single+已配对本人，未知sender只提供绑定说明；不自动按管理员映射。配对码只在本人登录页面申请，消费成功不写业务消息。
- [ ] 根据官方SDK安装包核验body.msgid/from.userid/chattype等形状，UUID5按bot+msgid幂等；req_id只用于响应关联。
- [ ] 收到问题的处理中回复与最终回复复用stream_id；只发送经过检查的文本。发送未知结果标delivery_unknown，不无限重发。
- [ ] 新建对话命令重置会话锚点；群/附件/图片不进模型；SDK日志不打印消息、secret或原始frame。
- [ ] worker单bot租约防止双连接，断线重连及进程退出不重复处理任务。不得用群webhook成功推送替代双向问答验收。

```python
async def test_duplicate_wecom_message_enqueues_once(wecom_case):
    frame = wecom_case.text_frame(msgid='same-id', chattype='single')
    await wecom_case.deliver(frame)
    await wecom_case.deliver(frame)
    assert wecom_case.unique_tasks == 1
```

运行 `env -u DATABASE_URL python -m pytest -q tests/agent_v2/test_routes.py tests/agent_v2/test_wecom.py` 与 `node --test tests/agent_v2_frontend.test.mjs tests/closing_review_agent_frontend.test.mjs`。提交 `feat: connect web and personal WeCom to agent v2`。

## T10：通用能力 Eval 与回归门禁

**Files:** evals/trading_agent_v2/capabilities.json、cases.jsonl、holdout.jsonl、rubric.md；scripts/run_agent_v2_evals.py；tests/agent_v2/test_eval_runner.py。
**Interfaces:** `python scripts/run_agent_v2_evals.py --mode deterministic|live --suite regression|holdout --output <path>`；默认deterministic，live显式确认Staging及供应商配置，输出精简统计不包含原始会话。

- [ ] 按设计十项通用能力，每项至少4个合成案例：正常、改写/参数变化、缺失或歧义、负向/故障，共至少40个回归案例；额外至少12个留出新组合。Greeks/天气等不作为路由标签。
- [ ] 案例字段：id、capabilities、question、fixture、required_evidence、allowed_tools、forbidden_behaviors、oracle、split。oracle是固定事实/行为约束，不是唯一自然语言答案或调用顺序。
- [ ] 可复现工具/范围/故障用脚本模型测试；真实模型留出评估检查泛化。留出题不放入prompt，评估后若用于调试则补新题。
- [ ] 硬门槛100%：账户/权限、固定数值容差、单位、全量覆盖、无私有出站、无虚构引用；不以平均分掩盖一条越权。
- [ ] 软评分0/1/2：相关性、依据、推论节制、必要澄清、简洁有效五维；每案例至少8/10且无维度为0，首轮未达标不得称开放分析验收通过。该门槛为工程验收默认，不是普遍正确保证。
- [ ] 真实模型每个留出案例重复3次并记录模型/提示词/工具版本；需获准额度，未运行保留未验收。按失败能力汇总，不为了过例题添加关键词特判。

```python
def test_critical_failure_cannot_be_averaged_away():
    from scripts.run_agent_v2_evals import summarize_scores
    scores = [{'hard_pass': True, 'soft_score': 10}] * 39
    scores += [{'hard_pass': False, 'soft_score': 10}]
    assert summarize_scores(scores)['release_pass'] is False
```

运行 `env -u DATABASE_URL python -m pytest -q tests/agent_v2/test_eval_runner.py`，再执行deterministic套件。提交 `test: evaluate general agent and harness capabilities`。

## T11：Staging迁移、真实链路与可恢复部署

**Files:** 更新 README.md、开发流程_备忘.md（仅新环境/worker说明）、版本更新记录.md（部署成功之后）、handoffs/2026-09-09-trading-agent-v2-design.md；生成精简验收记录 docs/acceptance/2026-09-09-trading-agent-v2.md。

- [ ] fetch最新staging、重验差异，记录迁移前代码/数据库目标、备份路径/校验及恢复演练结果。只迁六张附表及必要索引，不重跑全项目init_db；RLS/grants回读单独核对。
- [ ] 现有Web依赖不升级；在常驻且获准的Staging主机部署worker，flag默认关闭。没有合适资源而需新增付费实例时向用户说明具体资源与费用，再继续该步骤。
- [ ] 同一固定快照独立核对全部期货期权持仓数量、浮盈亏与Greeks覆盖；结算/WH不重复、未归类不遗漏、原页面口径不回归。
- [ ] 真实DeepSeek→真实MCP→真实Staging事实→企微本人提问→最终回复→证据追问走通，至少包含一条非预设组合问题和一条联网推论。无配置不以Mock替代。
- [ ] 权限撤销、群拒绝、搜索敏感词、行情缺失、worker重启、重复消息与delivery_unknown在受控测试中验证。
- [ ] 真实留出Eval通过，记录延迟/调用次数/用量以及已知缺失，不能以health200宣称交付。
- [ ] Staging提交/推送/部署按项目规则执行；不推main。回滚先关V2/worker，撤执行令牌，网页恢复V1；附表保留以便审计，不自动DROP表。旧调度不新增企微推送。

最终目标回归：
```bash
env -u DATABASE_URL python -m pytest -q tests/agent_v2 tests/test_trading_effective_facts.py tests/test_trading_valuation.py tests/test_closing_review_agent.py tests/test_closing_review_agent_store.py tests/test_closing_review_scheduler.py tests/test_auth_permissions.py
node --test tests/agent_v2_frontend.test.mjs tests/closing_review_agent_frontend.test.mjs tests/trading_management_frontend.test.mjs
git diff --check
```

满足真实验收后状态写“V2 Staging本人试用已交付”，不能写Production完成或群/第三方Agent已可用。

## 需求追踪与里程碑

| 要求 | 任务 | 阶段成果 |
|---|---|---|
| 可复用技术与身份范围 | T0–T2 | A：合同、隔离、持久化可测试 |
| 全量事实/属性/Greeks | T3–T4 | B：统一业务工具结果可核对 |
| MCP与模型 | T5–T6 | C：模型真正调用受控工具 |
| 公开研究与Harness | T7–T8 | D：开放问题有界执行 |
| 企微本人/网页兼容 | T9 | E：真实交互可联调 |
| 通用能力Eval与部署 | T10–T11 | F：完整Staging试用验收 |

依赖顺序：T0→T1→T2→T3→T4→T5→T6→T7→T8→T9→T10→T11。全部单主Agent顺序推进；T0缺凭据只阻塞live子步骤，不阻塞纯本地阶段。

不需用户重新选择数据口径、组别或风险等级。仅在真实接入缺少机器人权限/密钥、搜索供应商额度、需新增付费常驻资源、生产动作或新计算模型业务取舍时提出具体问题。技术限制不应伪装成用户需求尚未确认。

## 计划自检结果（仅文档）

- 全部业务范围对应T0–T11；全量Greeks在T4，台账页面不列为改造任务。
- Eval十项能力映射T10，工具数值、权限、留出泛化与最终回答均有门槛。
- 六张附表及旧表最小复用在T2；迁移/恢复/线上回读在T11，不因本地通过提前实施生产。
- 新增成交/平仓工具保留原平仓盈亏能力；原日常摘要、调度与V1会话隔离保持。
- 首轮仅本人、群关闭、外部OAuth不作为已完成能力。搜索key、企微配置和常驻主机在T0核实，未具备仅阻塞相关live验收。
- 测试示例中的 *_case 为T0/T对应任务在conftest构建的合成fixture：以真实模块函数与临时DB为被测对象，仅替换外部报价/模型/时钟/HTTP，禁止fixture直接生成期望答案；不需要制作新的业务页面或报表。
