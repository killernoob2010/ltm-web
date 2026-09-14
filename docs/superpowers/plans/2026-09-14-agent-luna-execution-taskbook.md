# 智能贸易助手受控灵活分析：Luna 分批执行任务书

> 执行方式：使用 executing-plans 按批实现。主 Agent 管需求、设计、业务口径、审阅和正式验收；仅在用户明确要求派发时，建立隔离的 gpt-5.6-luna / max 执行者。不自动创建任何子 Agent，不嵌套委派。

**状态：** v1.0，2026-09-14；仅生成执行说明，未实施、未派发。

**目标：** 在既有权限和确定性工具范围内，支持自然语言驱动的来源选择、多对象分析、补查和连续追问。

**设计依据：** [完整设计](2026-09-14-agent-capability-planning-complete-design.md)。执行者必须同时读取；本文细化接口与任务边界，不另建方法论。

**架构与技术：** 现有 Python/FastAPI/Pydantic/MCP/Worker/JavaScript；新增少量职责模块，复用模型适配器、业务服务和存储。一个主执行循环。

## 1. 派发边界与基线

编写时 HEAD `8afdbb8024d676732f502e336e79119de21bc158`，工作目录 `/Users/wangjingze/.codex/worktrees/debb/轻量化交易管理系统WEB`。完整设计文档仍未提交；不得清理或丢失。

Luna 可做：指定隔离工作树内的实现、本地合成测试、非生产提交和差异整理。Luna 不做：推 main、生产部署、生产数据操作、真实模型/搜索付费调用、读取凭据、调整预算/权限、增加依赖、迁移数据库、重新设计业务语义。已有生产授权由主 Agent 执行，不传递为 Luna 自主生产权限。

每批交接必须填写实际起点 SHA、隔离工作树、允许文件、已完成依赖提交和该批测试。没有真实值不能发出派单。本文不是自动委派授权。

执行前阅读项目规定的 onboarding 文档及本文。检测工作树变化、设计冲突或依赖缺失时，回报主 Agent；不通过 reset、覆盖文件或整分支合并处理。

## 2. 已确定的实现决策

### 2.1 业务与安全分工

- Planner 生成任务；Harness 校验、批准并监督；MCP/工具执行时再次核权。
- 保留 auth.py 的实时用户/账户/会话检查，所有结果复用仍通过 store 的访问检查。
- 业务能力目录来自现有 TOOL_SPECS、字段定义和当前权限，不能自成授权表。
- 业务来源的关键词匹配退出主决策路径；明确禁止联网等约束保留程序检查。简单字面否定不能代表所有自然语言限制，规划器必须输出所理解的限制，冲突或不确定时公开工具保持关闭并说明。
- 规划不能包含身份、账户授权列表、任意 SQL/代码、执行令牌或增加预算的请求。
- 不仅更新 Harness 的 tools/list；active_plan、MCP list_tools、_public_gate 必须消费同一服务器批准记录。

### 2.2 净额口径：修正完整设计中的过宽示例

以 `docs/2026-09-14-agent-domain-recovered-rules-v0.1.md` 的既有定义为准：净卖量=卖出量−买入量；负值表示净买；实际浮盈亏由实际持仓逐条计算再汇总。基础组成保留月份、Call/Put、方向及明细，不构造净仓成本。

“Call 净买、Put 净卖各多少”的默认实现是查询指定范围内的两类双边实际持仓，分别计算净额并据符号显示实际净方向。若 Call 实际为净卖，明确纠正用户前提，不能过滤掉净卖部分制造净买结果。

只有用户明确要求“只统计净买合约组”等净额后选择时，才进入 post-net 选择。这项不在第一批新增工具范围内；已有工具不能表示时交回主 Agent。完整设计第 6 节的“先按净方向选组”不能作为所有净买/净卖问题的默认路径。

“各总手数”同时交付 gross quantity 和净额，标清两者；对应浮盈亏为该目标范围实际持仓的合计，不改成单边浮盈。用户明确单边要求时使用独立目标且标清口径。

### 2.3 规划与预算

规划占第一次模型调用，使用现有 model.next_turn / ModelTurn 适配；此阶段 schemas 为空，不执行模型返回的 tool_calls。使用结构化 JSON 任务，不输出思维链。

调用数和截止时间沿用 RuntimeLimits 实际值。代码默认模型 6、工具 8、搜索 2、任务 90 秒；不得提升。模型解析修复最多一次且计入预算。计划永久失败只输出明确限制或已证实部分，不恢复关键词路径后声称成功。

保留至少一次答案组织的模型额度。执行动作、重规划、补正文共享原预算；重复失败不重置任务。第一版顺序执行，不在本任务顺带增加并行工具管理。

## 3. 合同：实现时使用这些字段

创建 `backend/app/trading_agent/planning_contracts.py`，所有模型使用现有 StrictModel，限制 extra 字段，列表和文字按下表约束。模型可输出的结构与服务器补充结构分开。

| 类型 | 字段与约束 |
|---|---|
| AnalysisTarget | id（任务内唯一，≤40字符）；domain=positions/dataset/public/knowledge；filters（必须由对应既有查询 Schema 验证）；metrics（≤16）；group_by（≤8）；net_intent=none/net_buy/net_sell/net；label（≤120） |
| Requirement | id（≤40）；question（≤400）；targets（1—8）；source_intent=internal/public/both/knowledge/discover；depends_on（≤8 requirement ID）；needs_full_text（bool）；time_requirement（≤160） |
| ConditionOrigin | target_id；field；origin=current/inherited/default；evidence（≤160，当前原文片段或前一状态键） |
| UserRestriction | kind=no_web；scope=turn/conversation；evidence（≤160）；解除也必须有本轮明确原文，不接受无依据清除 |
| TaskPlan | schema_version=1.0；objective（≤500）；topic_action=continue/refine/presentation_only/refresh/new_topic；requirements（1—8）；condition_origins（≤32）；restrictions（≤8）；presentation=auto/text/table/chart；prohibited_presentations；clarification（可空，≤240） |
| RequirementCoverage | requirement_id；status=answered/partial/blocked/needs_clarification；result_refs；missing_codes；delivery_blocks；actual_scope；time_status |
| CoverageReport | schema_version=1.0；items（逐项对应 requirement）；complete（仅所有必答项 answered 才 true） |
| ConversationState | schema_version=1.0；topic；targets；condition_origins；restrictions（仅 conversation 级）；presentation；result_refs（≤8）；open_requirements（≤8）；source_task_id（服务端填写） |

约束：id 不重复；依赖不能引用不存在节点或成环；公开目标不能携带私有业务值；合法字段必须来自授权目录；不能为不存在能力补出工具名。filters 内部必须经过对应既有类型验证，不能因为外围是 dict 就绕过。

拟公开接口固定如下，类型放在 planning_contracts.py；具体参数对象复用现有模型与 Budget 类型，不引入另一套 runtime：

```python
# capability_catalog.py
def build_catalog(principal, capability_payload) -> dict: ...
# planner.py
async def create_plan(user_text, conversation_state, catalog, model, budget) -> TaskPlan: ...
def validate_plan(plan, catalog, restrictions) -> TaskPlan: ...
# coverage.py
def assess_evidence(plan, envelopes) -> CoverageReport: ...
def assess_delivery(plan, coverage, validated_answer) -> CoverageReport: ...
# conversation_state.py
def update_conversation_state(previous, plan, coverage, result_refs) -> ConversationState: ...
```

签名省略号仅用于说明接口，不允许提交空实现。create_plan 的实际调用必须通过 Harness 已有计时/计数封装，不得在模型适配器内私建预算。

## 4. 服务器批准记录与旧接口兼容

当前 active_plan 从 agent_v2_events 中读取 research_policy 事件的 status、error_code、tool_name；目录与执行入口已经依赖这一记录。复用此载体，不建表、不把整个计划塞进 tool_name。

1. 任务开始记录未批准公开动作的状态；即使上一个任务允许联网，也不得继承执行权限。
2. 取得目录与历史后生成、验证 TaskPlan。目录可以说明公开研究服务是否可用，但规划前的实际可调用工具不包含公开动作。
3. 服务端根据计划中 public/both 的需求、用户限制、当前身份、服务状态计算批准结果。
4. 通过现有 append_event 写 research_policy，沿用当前可识别 mode/reason/domains；另以安全事件记录 plan revision 和 requirement ID，不把敏感查询原文写入普通日志。
5. 写入失败不得只在本地认为已批准；该次公开调用保持关闭，内部可继续。
6. 批准后刷新可调用目录；MCP 和 dispatch 都读取当前运行的最新服务器记录。
7. 补查需要新公开需求时先修订并重新验证 TaskPlan，再写新批准记录；旧 grant 不产生超出当前策略的权限。
8. 无 public 需求或 no_web 有效时撤回本轮公开许可；所有真实网络调用仍经过出口校验。

现有 RequestPlan 作为运行授权投影保留，不再承担完整语义解析。enforce_research_policy 可供旧测试/旧任务兼容，但不得覆盖新 TaskPlan 的来源选择。需保留的明确禁止检查抽成专门函数，避免同时运行两套业务分类。

业务数据不得由 Harness 直接查库。store 写事件、取受控运行状态不等于直接读取业务事实。新增合同仅写入既有 assistant structured_payload 的 task_plan、conversation_state、coverage 字段；保存前校验，旧字段不删。

## 5. 分批任务

### P0：主 Agent 的前置核对，不派给 Luna 自行决定

- [ ] 确认实际 HEAD/部署版本、完整设计和本文进入可交接工作树。
- [ ] 只读取得 #116 的原问题、表格视图、字段覆盖和事件，定位“空字段”。未取得时该缺陷保持未验收，不拦截无关基础模块。
- [ ] 核对本任务书第 2.2 节与既有领域合同；特殊 post-net 选择不授权新增。
- [ ] 确认 append_event/store 现有字段能容纳批准投影，schema 不需迁移。
- [ ] 生成真实起点和允许文件的派单。P0 未闭合时只可派 P1 这种不依赖缺字段结论的批次。

### P1：结构与业务能力目录（对应总方案 T1）

允许创建：planning_contracts.py、capability_catalog.py、tests/agent_v2/test_planning_contracts.py、test_capability_catalog.py。
允许修改：catalog.py、semantic_catalog.py、tools.py 的目录输出相关函数，不改执行授权。

- [ ] 先测试非法身份字段、未知过滤字段、循环依赖、空 requirements、未授权能力泄露。
- [ ] 按第 3 节实现合同；目录由现有注册信息派生，补充支持任务和空值/覆盖说明。
- [ ] 未取得实时覆盖时返回 unknown，不扫描整库。
- [ ] 运行新测试及 test_dataset_catalog.py、test_dataset_permissions.py、test_contracts.py。

最小断言示意（implementation 后的正式测试使用真实 Schema）：

```python
def test_task_plan_rejects_model_owned_identity(valid_plan):
    with pytest.raises(ValidationError):
        TaskPlan.model_validate({**valid_plan, "user_id": 999})

def test_task_plan_rejects_cycle(valid_plan):
    valid_plan["requirements"][0]["depends_on"] = [valid_plan["requirements"][0]["id"]]
    with pytest.raises(ValidationError):
        TaskPlan.model_validate(valid_plan)
```

交付：目录/合同代码、测试命令和结果、字段兼容说明。此批完成不宣称模型会正确解析问题。

### P2：规划器与上下文输入（对应 T2）

依赖 P1。允许创建 planner.py、test_planner.py。允许修改 prompts.py、request_contract.py、harness.py 中规划调用接入点；model.py 只在现有 ModelTurn 无法承载时提出修改给主 Agent。

- [ ] 复用 tests/agent_v2/test_harness.py 的 ScriptedModel/FakeMCP 思路，新增计划 JSON 回合。
- [ ] create_plan 调用前取得授权目录和最小会话状态，schemas 为空；意外 tool_calls 拒绝，不执行。
- [ ] 验证天气发运计划包含内部与公开需求；纯内部不包含公开需求；知识问题无不必要数据调用。
- [ ] 非法 JSON 最多纠正一次；错误不得绕过共享预算。
- [ ] 替换宽泛提示词触发，保留只在相应任务需要时出现的库存、持仓指导。
- [ ] 运行 test_planner.py、test_prompts.py、test_model.py 及受影响 Harness 测试。

验收断言：规划先于业务调用；计划内身份/未知工具被拒；规划调用数出现在同一预算。Fake 只证明行为合同，不证明自然语言泛化。

### P3：授权一致与自动公开研究（对应 T3）

依赖 P2。允许修改 harness.py、research_policy.py、mcp_server.py、tools.py、research.py、store.py 的策略记录函数；auth.py 原授权逻辑禁止放宽。
测试范围：test_harness.py、test_research_policy.py、test_mcp.py、test_research.py、test_egress.py、test_auth.py、test_scope.py。

- [ ] 按第 4 节实现批准、持久化、目录刷新与执行复核。
- [ ] 移除新路径旧关键词提前禁用，保留显式 no_web。
- [ ] 搜索仅摘要时继续 read_public；一个正文失败可有限替代，不自动提高预算。
- [ ] 覆盖批准写入失败、过期任务、伪造 policy 参数、运行中撤权和旧缓存访问。

必须断言：

```text
天气+发运：工具轨迹有内部证据、search_public、read_public。
仅内部：搜索调用数=0。
禁止联网：任何查询词下均无外部 HTTP 请求。
伪造/无批准/批准写入失败：直调公开工具被拒，网络调用数=0。
撤权：新工具调用及历史结果访问均不能继续泄露原范围。
公开实体可检索；内部账户、客户、订单、金额不出现在搜索请求。
```

交付时分别列 Harness、MCP、dispatch 的测试证据。只修改 tools/list 视为未完成。

### P4：覆盖检查和有界补查（对应 T4）

依赖 P3。允许创建 coverage.py、test_task_coverage.py；修改 harness.py、facts.py、request_contract.py、position_semantics.py 的目标选择/展示，禁止重写估值公式。

- [ ] 以第 6 节固定数据实现多目标净额、gross 数量和浮盈核算断言。
- [ ] assess_evidence 对每个 requirement 检查字段、范围、时间和全文证据，不只在全文寻找 Call/Put 字样。
- [ ] scope_mismatch/missing_metric 等可补项回执行；引用格式错误留给答案修复。
- [ ] 连续两次同动作同错误且无新证据停止该路径；其他独立需求继续。
- [ ] 留一次模型额度组织答案；预算不足明确 partial，不重启任务。
- [ ] 测试行情缺失、只有预览、字段投影丢失与源字段为空，输出不同缺口类别。

验收：不改变已核验盈亏口径，不根据模型复述数字判范围；不得新建任意聚合/表达式工具。

### P5：追问状态、事实清单与答案（对应 T5）

依赖 P4。允许创建 conversation_state.py、test_conversation_state.py；修改 store.py、prompts.py、answer_v21.py、answer_contracts.py、request_contract.py、harness.py。

- [ ] 保存可选 task_plan/conversation_state/coverage；旧消息和 2.0 兼容路径仍可读。
- [ ] 只继承相关主题条件，显式新条件覆盖，new_topic 不继承无关 filters。
- [ ] presentation_only 复用仍有权限的有效结果；refresh 重新取数。
- [ ] 只将 conversation 级 no_web 留存；turn 级限制不得污染以后任务。
- [ ] 提供带维度/单位的事实引用清单；用 assess_delivery 检查必答对象与答案位置。
- [ ] 局部错误不删除全部有效结果；未知不补零、负号不丢失。

具体序列断言：2701全部期权→只看Put（月份不变）→2705（Put保留）→图表（无新业务查询）→刷新（新快照）→天气（无旧持仓过滤）。

### P6：页签和最小质量诊断（对应 T6）

依赖 P5 的结果状态合同。允许修改 frontend/closing_review_agent.js、frontend/agent_quality.js、quality.py 及对应测试。

- [ ] 新建放左侧；按最后业务活动而不是选择动作更新顺序，同秒 ID 倒序。
- [ ] 测试切换、排序、运行中刷新、删除和撤销后 owner、draft、选中项不变。
- [ ] 质量页展示缺口类别和版本，不把未评估标为通过。
- [ ] 所有新增时间到秒，普通日志无问题原文和完整业务结果。

### P7：离线执行评估与主 Agent 验收交接（对应 T7）

允许修改 scripts/run_agent_v2_evals.py、evals/trading_agent_v2/、test_eval_runner.py。不由 Luna 发起真实服务调用。

- [ ] 区分 definition、offline execution、real model、human review；定义检查不能输出真实通过率。
- [ ] 每个用例有数据 fixture、允许工具、必需证据、禁止行为和最终结果断言。
- [ ] 回放现有已知失败；新的留出问法由主 Agent 冻结并保留，不交给执行者针对性调试。
- [ ] 提交完整回归结果、未完成项和真实验收清单，不发布。

主 Agent 后续：代码审阅→核对授权和剩余费用→相关真实模型测试→选择性正式发布→管理员真实问答→记录实际结果。新代码只能在主 Agent 审阅后进入生产。未取得正文、真实问题失败、重大漏项均保留未完成。

## 6. 固定业务数据与预期结果

测试使用合成数据，通过现有规范化 fixture 适配，不从生产拷贝账户明细。所有数量单位为手，盈亏为 CNY；不同合约不构造虚拟成本。

| 合约 | 类型 | 买数量 | 卖数量 | 买方实际浮盈亏 | 卖方实际浮盈亏 |
|---|---|---:|---:|---:|---:|
| i2701-C-800 | Call | 100 | 30 | 1000 | -120 |
| i2701-C-850 | Call | 10 | 50 | -200 | 300 |
| i2701-P-700 | Put | 20 | 90 | -100 | -600 |

Call：gross=190，net_sell=-30，因此净买30，浮盈亏980。Put：gross=110，net_sell=70，因此净卖70，浮盈亏-700。总 gross=300，net_sell=40，浮盈亏280。

仅选择净买合约组时得到的是第一行净买70，与全部 Call 净买30不同；测试必须证明默认任务不会误用70。部分字段为空时相应指标标部分或不可用，不把空值当零。

天气 fixture：内部仅澳洲上周发运100、本周80，同口径单位；公开本周港口暴雨正文，但无停港或装船延误证明。断言发运减少20、降幅20%；可说明天气为可能影响因素，不得写“暴雨确定造成全部20减量”。内部只有上周时，不得输出本周实际降幅。该数据纯测试，不能展示为市场事实。

公开失败 fixture：搜索返回两项，第一项读取失败、第二项有日期与摘录。允许替代路径；两项都失败时保留未读正文限制。用户 no_web 时上述传输函数调用次数必须为零。

## 7. 统一验证命令与停点

每批先运行所列新增/受影响测试，再按实际修改扩大回归。使用项目 Python 环境，不要求重建临时环境或安装新依赖。

```bash
python -m pytest tests/agent_v2 -q
node --check frontend/closing_review_agent.js
node --test tests/agent_answer_renderer.test.mjs tests/closing_review_agent_frontend.test.mjs
git diff --check
git status --short --branch
```

以下情况停止该批并交主 Agent：领域合同矛盾；新增权限或工具类型；需 DB/配置/依赖变更；预算无法完成批准任务；回归失败无法定位；旧结果兼容破坏；真实数据根因与假设不符。可完成不依赖该决策的独立本地检查，不以扩大权限、删除测试或编造 fallback 绕过。

每批报告固定六项：实现结果、修改文件、测试命令与实际结果、与验收要求的对应、剩余问题、提交 SHA/工作树状态。未提交也明确说明；不把本地通过写成线上通过。

## 8. 主 Agent 派单模板

仅在用户明确要求派发执行时使用，真实字段由主 Agent 当次填入。不得将模板中未填写的基线直接交给执行者。

```text
角色：Luna / max，隔离执行者。不要创建子 Agent。
本批：P编号及标题。
环境：主 Agent 已核验的隔离路径与起点 SHA；仅非生产。
阅读：完整设计、本任务书、项目 AGENTS.md 与本批所依赖合同。
目标：本批最终可验证的行为。
依赖：前序批次提交与接口已核验情况。
允许文件：逐项复制本批清单，不使用整个仓库通配范围。
口径：继承任务书第2节，禁止自行改变业务解释或计算。
测试：本批断言、固定数据、命令和禁止行为。
边界：不访问生产，不运行真实模型/外部搜索，不读凭据，不改预算。
停点：按第7节执行。疑问交主 Agent，不能自行设计替代架构。
交付：六项报告；完成本批即停，不自动执行下一批。
```

## 9. 本任务书的完成含义

本文足以支持主 Agent 按批派发工程任务；它不意味着未查明的 #116 空字段已经关闭，也不把生产/真实评估转交给 Luna。基础批次可先开发，依赖未确认业务事实的部分明确保持停点。

主 Agent 审阅时必须核对：新旧研究策略只有一个有效授权来源；净买/净卖没有偷偷变成 post-net 合约筛选；可选 payload 兼容；所有真实服务调用由主 Agent 控制；评估记录区分定义、执行和人工判断。
