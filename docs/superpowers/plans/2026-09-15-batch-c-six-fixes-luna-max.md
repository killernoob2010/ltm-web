# 批次 C 六项验收缺陷修复说明书（Luna Max）

> **For agentic workers:** 使用 `superpowers:executing-plans`，由一个 Luna Max 执行者顺序完成本文。用户将自行安排执行；本文不表示已经启动执行，不允许嵌套创建子 Agent。用户指定模型为 `gpt-5.6-luna`、reasoning `max`，优先于技能中的默认模型建议。

**Goal:** 修复 C 阶段验收发现的六项缺陷，交付可供主 Agent 复验的本地代码、失败到通过的测试证据及精确提交。

**Architecture:** 保留现有 Pydantic AI 主循环、MCP 只读工具、服务端权限、Worker 生命周期和共享交付门禁；只修正错误分支、时间边界、工具串行、质量摘要、覆盖报告和修正反馈。不替换框架或新增业务能力。

**Tech Stack:** 仓库锁定 Python 3.12、Pydantic AI、pytest、原生 JavaScript/Node；沿用 `requirements-agent-v2.lock`，不升级依赖。

**Spec:** [迁移母方案](2026-09-15-pydantic-ai-controlled-migration.md) §3、§5、P2、P4、P5，以及 [原 Luna 任务书](2026-09-15-pydantic-ai-luna-max-taskbook.md)。本文覆盖本次六项修复的执行范围；不执行原任务书的后续真实验收或默认切换批次。

## 1. 工作量与当前状态

这是一个中等偏大的受限修复包：六个问题主要集中在四个后端文件，个别公共帮助函数或质量展示可能需要小幅调整；工作量主要在故障、取消和实际 SDK 调用的行为测试。单个 Luna 顺序完成即可，无需拆成多个执行者。

- 工作区：`/Users/wangjingze/.codex/worktrees/447c/轻量化交易管理系统WEB`。
- 被审查代码：`abdf200`；审查时 HEAD 为交接文档提交 `254f971`，detached HEAD，工作区干净。
- 本说明书的文档提交可以位于 `254f971` 之后。执行前核对祖先和实际 diff；允许纯文档后继提交，不假定代码仍未变化。
- 最新验收结论：**C 未通过，六项待修复**。旧 README/交接文档中的“C 已完成”只代表曾经接入代码，不能当作验收通过。
- 最近一次已有回归：后端 506 passed、4 个依赖弃用警告；前端 17 passed；依赖和差异检查通过。新增复现仍发现六项缺陷，故不能用这个旧数量放行。
- R1—R4 已使用合成数据复现；R5—R6 为代码调用链确认，实施时也须补行为复现。
- 当前没有本批次真实业务验收或正式部署证据。真实余额、服务器容量和管理员问答不属于 Luna 本次任务。

### 评估和范围

D3：一个 Agent 模块内的运行、结果与质量页面联动；R3：涉及敏感信息与权限停止；执行复杂度 C2。自动化覆盖模块回归及直接链路；完整业务验收仍需主 Agent 的实际页面与业务证据。没有新增独立业务模块，不升为跨系统改造。

## 2. 执行边界与文件

- 目标环境为本地隔离工作区和临时 SQLite/合成数据。允许局部实现、测试、明确文件的本地 Git 提交。
- 用户将自行启动 Luna；执行者不新建任务、不派生其他 Agent。若需隔离分支，使用 `codex/` 前缀，从上述候选代码建立，不从旧 main 猜测起点。
- 本包不 push、不合并 main、不部署、不改云端变量、不调用真实模型或收费搜索；不读取密码、Key、Cookie、完整数据库连接串，不复制其他工作区的凭据文件。
- 不访问或修改真实交易事实，不执行交易或资金操作。不新增 DB migration，不改交易口径、路由默认值、试点用户或模型供应商。
- 保留单任务上限：模型 6 次、工具尝试 8 次、搜索 2 次、总时长 90 秒、每次模型请求 15 秒、每次工具尝试 25 秒。修正最多一次；工具故障任务级最多一次额外重试。
- 所有尝试计数包含失败；取消/租约丢失后不继续查询、保存业务结果或投递。保留 `store.finish` 至多一次、False 不投递、授权最终撤销。
- 对外时间到秒。不新增原始模型消息、原始错误正文、工具 payload 的持久化或云端追踪。

主要允许修改：

| 文件（相对仓库根） | 用途 |
|---|---|
| `backend/app/trading_agent/pydantic_tools.py` | R1 脱敏、R4 取消时阻止后续调用 |
| `backend/app/trading_agent/pydantic_runtime.py` | R2 兜底状态、R3 超时、R4 串行、R6 修正反馈 |
| `backend/app/trading_agent/harness.py` | R5 保留完整覆盖报告 |
| `backend/app/trading_agent/quality.py` | R2/R5 数据映射与读取健壮性 |
| `backend/app/trading_agent/delivery_gate.py` | 仅 R5/R6 所需的最终覆盖或安全诊断投影，不改业务通过标准 |
| `backend/app/trading_agent/egress_policy.py`、`runtime_budget.py` | 仅复用现有函数无法完成 R1/R3/R6 时做最小增量 |
| `frontend/agent_quality.js` | 仅实际页面消费者映射确需修改时使用，不重设计页面 |
| `tests/agent_v2/test_batch_c_acceptance.py`（新增） | 六项集成与负例集中入口 |
| `tests/agent_v2/test_pydantic_tools.py`、`test_pydantic_runtime.py`、`test_quality.py`、`test_harness.py`、`test_worker.py`、`test_delivery_gate.py` | 修改相关行为测试，复用 fixture |
| `tests/agent_quality_migration.test.mjs` | 真实执行渲染函数的页面状态测试 |
| `handoffs/2026-09-09-trading-agent-v2-design.md`、`README.md` | 完成时记录本地修复状态、证据和剩余验收 |

若修改前端 JS，允许同步 `frontend/index.html` 该脚本的版本参数及对应版本断言。不要修改无关资源版本。不得重构 `auth.py`、`mcp_client.py`、`worker.py`、`runtime.py`、`resources.py`、交易计算模块或依赖锁；发现确需超出范围，说明原因交回主 Agent。

## 3. 开工与实施顺序

- [ ] 读取项目 AGENTS、本文、母方案有关章节，核对实际代码及 Git 状态，保留已有用户修改。
- [ ] 读取 `tests/agent_v2/conftest.py`；只用 `app.trading_agent` 导入，禁止产生另一份 `backend.app` 数据库实例。
- [ ] 复用 `test_pydantic_tools._context`、`test_pydantic_runtime` 的 SDK 合成模型与 MCP fixture；测试文件保留在 `tests/agent_v2/` 下以继承临时库和禁外网保护。
- [ ] 每个 R 项先新增行为测试、确认因该缺陷失败，再做最小修改、运行该项测试。环境导入失败不算有效失败复现。
- [ ] 按 A 组 R1/R3/R4 → B 组 R2/R5 → C 组 R6 顺序实施。各组提交一次，末尾做一次完整回归。

所有测试中的客户名、凭证和数量均为明确的合成值；不用真实数据构造诱饵，不读取真实凭据列表来实现脱敏。

## 4. A 组：错误出口与执行控制

### R1（P1）：临时故障也必须先检查再返回、再审计

**现状和复现：** `pydantic_tools.py` 的 `transient_code` 分支在 `_model_summary` 前直接审计并返回。给 `_context` 的 `sensitive_values` 中放入 `INTERNAL-CUSTOMER-CANARY`，在 `temporarily_unavailable` envelope 的 `payload.code` 放同一标记：原版模型返回值和审计记录都包含标记，预算未取消。

**输入/输出：** `invoke_registered_tool(name, arguments, *, ctx)` 保持现有 dict 返回约定；业务结果仍仅由原 MCP 获取。所有状态的 envelope 先扫描，再决定重试、审计或投影。

- [ ] 参数化复现位置：`payload.code`、`payload.error_code`、嵌套 payload、envelope 元数据，以及包含合成 grant 的错误码；覆盖 complete、partial、temporarily_unavailable。
- [ ] 将现有整份 envelope 敏感值检查放到所有 envelope 状态分支之前。命中时取消预算，返回/审计固定安全原因；不以该 envelope 继续重试，不将其加入模型证据列表。
- [ ] 未经确认的上游字符串不能直接当公开错误码。对临时错误保留代码中真实需要的、有限的已知类别映射，未知值统一为固定临时错误码；ASCII 或长度校验不等于脱敏。普通上游错误不自动获得重试权限，权限拒绝和隐私拒绝不能重试。
- [ ] 验证失败审计本身报错仍停止；合法临时故障首次失败后成功只发出两次 MCP 请求，分别计数；第二次失败不继续自动重试。

核心断言示例（复用现有 fixture，测试应同时检查调用次数）：

```python
summary = await invoke_registered_tool("query_positions", {}, ctx=ctx)
assert "INTERNAL-CUSTOMER-CANARY" not in str(summary)
assert "INTERNAL-CUSTOMER-CANARY" not in str(ctx.store.events)
assert ctx.budget.cancelled
assert ctx.envelopes == []
assert len(ctx.mcp.calls) == 1
```

**验收：** 原复现转绿；合法工具最小摘要仍保留；无诱饵进入模型可见返回值或审计记录。这里只证明指定边界有效，不宣称完成所有自然语言内容的防泄露审计。

### R3（P1）：分别限制单次请求与整个 SDK 阶段

**现状和复现：** `_run_agent` 的 `wait_for(agent.run(...))` 使用 `model_timeout_seconds`，而一次 `agent.run` 包含多个模型和工具往返。缩比实验：单次限制 0.05 秒、任务总限制 1 秒，两个各 0.03 秒步骤在原版被提前取消。

**输入/输出：** 保留 `_run_agent` 的入参和结果对象；`RuntimeBudget` 跨规划、执行、修正共享；`_DeadlineChatModel.request` 保留单模型 15 秒限制。

- [ ] 先测完整 SDK 多轮阶段：每次请求都低于单次期限、工具低于工具期限，但阶段累计超过单次期限，应成功完成且实际调用数准确。缩比定时需留充足余量，配合事件同步，避免依赖几毫秒调度差异。
- [ ] 完整 `agent.run` 外层使用任务剩余时间；请求层继续限制单次模型调用。不能简单删除所有外层期限。
- [ ] 验证工具尝试期限和自动重试的关系：SDK Tool 包装的超时不能把两个各自合法的尝试错误合并为一次 25 秒限制；每次尝试仍受适配器和总任务期限约束。
- [ ] `CancelledError` 原样传播；执行中总期限、Worker 租约丢失都能取消 SDK 和工具；单次模型故意挂起仍被请求层终止。
- [ ] 成功、异常、取消时均记录已发生模型请求，不能只在 `_run_agent` 成功返回后补计。锁定 SDK 有 `RunUsage`，按其当前 `Agent.run` 参数复核后使用可观察 usage 或已有等效机制，在 finally 完成实际计数；避免重复计费式计数，不把工具循环重新实现一遍。

边界代码方向：

```python
# 外层是整个阶段，单次请求限制仍在 model.request 层。
stage_timeout = budget.remaining_seconds()
result = await asyncio.wait_for(
    agent.run(
        prompt,
        message_history=message_history,
        deps=agent_deps,
        usage_limits=UsageLimits(
            request_limit=available,
            tool_calls_limit=max(0, budget.max_tools - budget.tool_calls),
        ),
    ),
    timeout=stage_timeout,
)
# 这是时间边界替换点；实现时另须按上文增加异常/取消后的 usage 统计。
```

**验收：** 多轮合规执行成功；单次超限与任务超限分别停止；失败/取消后次数不漏记；不提高任何生产限额。

### R4（P1）：在真实 SDK 工具循环中强制串行和停止

**现状和复现：** `_build_sdk_tool` 未设置串行，当前 SDK 的 `Tool.sequential` 默认 False。同一个 FunctionModel 响应中发出两个工具调用，原版实测并发峰值为 2。

**输入/输出：** `_build_sdk_tool(name, context) -> Tool` 保持独立 typed schema；同一任务所有注册工具串行执行，仍走原 MCP。

- [ ] 使用真实 `Agent(FunctionModel(...))`，在一条响应内返回两个 `ToolCallPart`；合成工具用活动计数器记录峰值，先证明原版为 2。
- [ ] 当前锁定 SDK 支持 `Tool(..., sequential=True)`，可作为最小实现；若使用等效 SDK 全局串行机制，必须给出实际行为证据。只在 prompt 写“串行”不合格。
- [ ] 每个即将开始的调用及重试再次检查取消和授权；第一个调用发生权限拒绝、隐私拒绝或审计失败时，第二个不得进入 MCP。
- [ ] 在 SDK 回调边界把“预算已取消但适配器返回错误 dict”转为实际停止，不能等模型再生成一轮才发现取消。区分取消信号与可整理已有证据的普通工具失败。
- [ ] 保留授权最终撤销与 finish 至多一次。安全拒绝后如需终结，可保存固定无业务内容的失败说明，不得再用旧证据构造业务答案；真实任务取消/租约丢失按 Worker 原职责结束，不自行投递。

具体注册方向和行为断言：

```python
# 在已有 Tool 构造中增加，不改 schema 或 grant 传递规则：
sequential=True
# SDK 同轮多工具行为测试：
assert peak_active_calls == 1
# 第一个调用撤权的独立场景：
assert mcp_started_names == ["describe_capabilities"]
```

**验收：** 正常同轮调用峰值为 1；安全拒绝后无第二次 MCP 请求、无后续业务答案生成请求，授权撤销。

## 5. B 组：可信质量状态和逐项覆盖

### R2（P1）：未完成核验的兜底不得显示通过

**现状和复现：** `_failure_answer` 把多数检查设为 not_applicable，正文非空便将 rendered_content 设 passed；质量映射于是返回 passed。已复现答案 failed、原因 model_timeout、核验 passed。

**固定决定：** 完全未运行共享交付检查的异常兜底使用 `validation_summary=None`，质量状态为 not_run。已经实际经过门禁的答案保留真实 summary；规则失败为 failed。不能从答案 failed/partial 或正文非空反推检查结果，也不能把所有 partial 粗暴归为 failed。

- [ ] 复现模型超时、工具安全拒绝及无证据异常的兜底，断言均不增加 validation_passed_count。
- [ ] 删除兜底中凭正文/refs 人工拼出“通过”的摘要；异常原因保留在既有限制/错误字段中。
- [ ] 保留七项检查结构和版本，旧记录/缺失/非法摘要仍为 not_run。实际门禁得出的失败与人工评价独立。
- [ ] 测试 `quality.migration_status`，再通过临时库读取质量列表、详情、summary 核对一致。前端测试实际执行渲染函数或 DOM 消费逻辑，不只搜索字符串存在。

固定组合：

| 答案/核验输入 | 自动核验输出 | 人工评价 |
|---|---|---|
| failed + 未执行门禁 | not_run | 未评价仍未评价 |
| partial + 缺失旧摘要 | not_run | 不变 |
| 实际 summary 任一检查 failed | failed | 不变 |
| 实际适用检查 passed + 人工 incorrect | passed | rejected |

断言入口：

```python
answer = rt._failure_answer(principal, deps, [], "model_timeout", "auto")
dimensions = quality.migration_status({"state": "failed", "structured_payload": answer.model_dump(mode="json")})
assert dimensions["validation"] == "not_run"
```

**验收：** 失败说明不会制造“七项核验通过”；实际规则检查与人工结果分别可见；不更改历史业务答案。

### R5（P2）：保留最终的 CoverageReport

**现状：** `harness.finish_checked` 先把完整 `CoverageReport` 写入 `task_context['coverage']`，随后用 `checked.coverage` 的 `AnswerCoverage` 覆盖；后者不含 complete/items。`quality._preview_payload` 与前端却按 complete/items 读取。

**固定字段职责：**

- 顶层 `ValidatedAnswer21.coverage` 仍为 `AnswerCoverage`，供既有答案消费者使用。
- `agent_context.coverage` 必须为最终 `CoverageReport`，含 `complete`、`items`，逐项有 `requirement_id/status/missing_codes/delivery_blocks`。
- `agent_context.conversation_state` 从最终门禁后的报告生成，不能保留门禁前“完整”的旧状态。

- [ ] 用开启 planning 的 legacy 路径跑一条合成任务，读取实际保存 payload，先证明 items 被覆盖。不要只对手工构造字典做单元测试。
- [ ] 门禁完成后重算或复用完整报告，更新 task_context 和 conversation_state；不要把 AnswerCoverage 赋给该键。
- [ ] 同一用例分别覆盖完整需求与缺一项需求；通过质量详情投影仍保留各 requirement_id 和缺口，完整例显示 complete=True。
- [ ] 验证顶层旧 AnswerCoverage 结构仍可读，新 Pydantic 路径的相同字段也一致。

最终 payload 的核心断言：

```python
report = persisted_payload["agent_context"]["coverage"]
assert {item["requirement_id"] for item in report["items"]} == {r.id for r in plan.requirements}
assert report["complete"] is expected_complete
assert "matched_result_refs" in persisted_payload["coverage"]
```

**验收：** 旧路径逐项覆盖、质量页和续问状态一致；不通过删去所有覆盖显示来消除错误。

## 6. C 组：R6（P2）向唯一一次修正提供安全、具体的反馈

**现状：** 修正 prompt 只有通用文字，message_history 仅原执行对话；校验的失败需求和原因没有传给模型。已有 ScriptedSDK 在第五次调用直接给好答案，并不证明实际反馈有效。

**固定反馈结构：** 在当前任务内存中构造 JSON，最多七个 failed_checks、最多四十个安全 unresolved_codes、以及计划内 requirement_id 对应的安全 missing_codes/delivery_blocks；可含服务端确认的 presentation/prohibited_presentations。不要传原始异常、客户/账号信息、工具原始数据、整个 validated 对象或无限长消息。

- [ ] 使用 FunctionModel 检查真实修正请求消息：只有收到特定失败 code 和相应 requirement_id 才返回修正答案，否则保持不合格，先确认原版失败。
- [ ] 从 `validated.validation_summary` 和 `task_coverage.assess_evidence/assess_delivery` 导出诊断。使用项目已知错误类别及已审查计划 ID；与 R1 一样对模型出口做投影，拒绝任意字符串伪装错误码。
- [ ] 将精简诊断放入修正 prompt；只修正已有答案，不新增工具、不扩大时间/来源/业务范围，不重复创建 TaskPlan。
- [ ] 测试至少一个有明确需求缺口的场景和一个纯展示/正文失败场景；后者不凭空造需求编号。修正消息必须反映真正的失败原因。
- [ ] 验证修正仍无业务工具、最多一次，失败后保留可核验 partial 或固定失败说明，不继续模型/工具尝试。合成诱饵不出现在反馈中；最终保存一次。

模型侧反馈示例（值来自实际服务端报告）：

```json
{"failed_checks":["evidence"],"unresolved_codes":["answer_reference_missing"],"requirements":[{"requirement_id":"positions","missing_codes":[],"delivery_blocks":["answer_reference_missing"]}],"presentation":"text","prohibited_presentations":["table"]}
```

**验收：** 模型实际收到了可行动的服务端诊断，修正后重新经过共享门禁；不通过伪造第五轮答案或放宽门禁获得测试绿灯。

## 7. 统一验证与交付

各 R 项完成先运行其新测试和直接影响测试，不必每改一行跑全量。三组结束后执行以下完整检查一次；若出现新失败，定位原因后只重跑失败与直接影响，最终代码发生实质变化再重跑模块回归。

```sh
env -u DATABASE_URL .runtime/agent-v2/bin/python -m pytest -q tests/agent_v2/test_batch_c_acceptance.py
env -u DATABASE_URL .runtime/agent-v2/bin/python -m pytest -q tests/agent_v2
node --test tests/agent_v2_frontend.test.mjs tests/agent_quality_migration.test.mjs tests/closing_review_agent_frontend.test.mjs
.runtime/agent-v2/bin/python -m pip check
node --check frontend/agent_quality.js
git diff --check
```

若隔离工作区没有 `.runtime/agent-v2`，只按仓库锁建立独立测试环境；不得复制 .env 或改系统 Python。环境无法安装时准确报告未运行项，不能复制 506/17 作为新证据。

**交回主 Agent 的必需内容：**

- [ ] R1—R6 对照表：修改文件/函数、失败复现、修复后断言和结果，逐项说明。
- [ ] SDK 真实合成循环证据：并发峰值 1、故障停止、修正请求包含诊断、实际模型/工具计数、finish 调用次数和授权撤销。
- [ ] 新鲜的测试结果、警告、失败或未运行项；不删测试、放松断言、改业务通过标准来消除失败。
- [ ] 分组本地提交、最终 HEAD、工作区状态与改动范围；只提交明确文件，不夹带用户修改或测试原始日志。
- [ ] 更新交接文档为“六项本地修复完成，待主 Agent 复验”，不要标“正式验收通过”。没有部署则不更新发布记录。
- [ ] 停在本地交付。主 Agent 复验六项及页面证据后，依既有项目授权处理真实管理员/正式验收，不由 Luna 自动进入后续批次。

**升级条件：** 不明确的新增业务规则、必须改授权模型/交易事实/依赖/数据库、要求真实凭据或新收费调用、跨出允许文件且无直接六项依据时，保留已完成代码与测试，说明具体冲突交回主 Agent。普通范围内修复和失败定位自行完成，不逐次询问是否继续。

## 8. 用户可直接交给 Luna 的启动指令

> 使用 gpt-5.6-luna、max reasoning，执行《批次 C 六项验收缺陷修复说明书》。先核对工作区、候选代码 abdf200 及后续文档提交，按 R1—R6 顺序和分组完成本地修复、合成测试、回归与本地提交。重点证明错误结果不泄露敏感标记、失败兜底不误报通过、多轮执行不误套单次超时、工具实际串行且取消后停止、逐项覆盖报告保留、唯一一次修正收到真实失败诊断。保留业务口径和默认路由，遵守文档文件边界；不创建子 Agent，不部署或调用真实模型。交回六项测试证据与提交后停止，由主 Agent 复验。
