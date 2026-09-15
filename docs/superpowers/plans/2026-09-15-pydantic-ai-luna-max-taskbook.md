# Pydantic AI 受控迁移：Luna Max 分批实施任务书

> **For agentic workers:** 使用 `superpowers:executing-plans` 单主 Agent 按批实施；禁止自动创建子 Agent。先读本文和母方案。本文是交接规格，不表示当前已经启动实施，也不授予 Production、真实付费调用或业务规则变更权限。

**Goal:** Luna 能在不依赖聊天上下文的情况下完成可审查的本地实现；主 Agent 控制依赖决策、真实调用、正式发布和最终业务验收。

**Architecture:** 官方 SDK 负责规划/工具运行机制；原 MCP 和后端核权负责访问；原确定性服务负责数字；项目交付门禁负责切题、证据、格式和状态。保留 Worker 租约和进程监督，不把旧 Harness 包成 SDK 的一个万能工具。

**Tech Stack:** 现有 Python/FastAPI/Pydantic/MCP/原生 JS；拟新增 `pydantic-ai-slim` 的必要模型依赖，精确锁定版本先经过 L1 复核。

**Spec:** [完整设计与执行方案](2026-09-15-pydantic-ai-controlled-migration.md)，2026-09-15。母方案定义业务与发布目标；本文细化执行接口并修正其概念示例与现有代码的字段差异。冲突按本文 §2 明确修正处理；其他实质冲突停止提交主 Agent。

## Global Constraints

- 默认 Luna 执行范围是隔离 worktree 的代码、临时 SQLite/合成数据、自动化测试和明确文件的 Git 提交；不得推 main、改正式配置或访问真实交易操作。
- 不调用真实模型或收费/扣额度搜索。真实探针/业务验证由主 Agent 按既有授权核实预算后执行。若用户另外明确扩大 Luna 本次权限，仍不能突破项目真实交易禁止规则。
- 不读取或复制任何密码、API Key、Cookie、完整 DATABASE_URL，不运行会输出整份环境变量的命令。
- 不新增业务数据源、业务公式、任意 SQL/代码工具、多 Agent、长期记忆、云端追踪或托管平台。
- 不改 runtime.py/resources.py 的进程监督与资源准入逻辑；不重写 auth.py 的授权模型；不得绕过 MCP 直接调用业务 dispatch。
- 不放松数值证据/账户范围/时间覆盖/出网检查换取通过率，不删除旧测试来消除失败。
- 不调整默认模型和已确认业务口径。保留单任务 6 次模型、8 次工具尝试、2 次搜索、90 秒总时长、15 秒模型调用、25 秒工具调用上限。
- 未经批准不新增数据库迁移；接口扩展使用可选字段与现有附表。计划涉及的新事件 kind 不代表可以任意改变旧事件读取逻辑。
- 所有展示/API 时间精度到秒。页签排序和页面重设计不在范围。
- 不以安装成功、脚本返回 0、HTTP 200、已有历史测试记录或 delivered 证明当前业务验收通过。

## 0. 交接方式与默认停点

工作目录基线：`/Users/wangjingze/.codex/worktrees/debb/轻量化交易管理系统WEB`；本轮核对分支 `codex/agent-answer-research-conversation-repair-20260914`，HEAD `9471c4e3c4b941dc616a952972dafa6ee065720d`。两份方案目前是本地文档；新 worktree 不会自动继承未提交文件，启动前确认两份可读。不得把另一 worktree 的 .env 或依赖环境复制进来。

分批：

| 批次 | Luna 工作 | 交回主 Agent 的条件 |
|---|---|---|
| A | L0 基线/样本 + L1 依赖候选/离线兼容性 | 默认首次只执行此批；交回精确锁候选、测试、未核实项，停止 |
| B | L2 合同/计算 + L3 工具/预算/出网 | A 的依赖/协议决策通过后才启动；提交安全与业务测试证据后停止 |
| C | L4 SDK 主链 + L5 交付与质量页面 | B 审查通过后启动；全量本地回归后停止 |
| D | L6 真实验收准备与问题修正 | 主 Agent 执行真实调用/发布；Luna 仅处理明确返回的局部修正 |
| E | L7 路由切换/观察后清理 | 主 Agent明确发布/清理决定后另行交接；不是首次任务的一部分 |

每个任务执行：读本节指定文件 → 写失败测试 → 运行且确认因缺少目标行为失败 → 最小实现 → 定向测试 → 包内回归 → diff 审查 → 单独提交。失败由测试环境/导入错误导致时先纠正，不能把任何红灯都当作有效 TDD。

每个任务允许独立提交但每批末必须停下，不能自己标记主 Agent 的审批门已通过。出现普通实现问题先自行定位；只有新增业务决策、越界或安全未知才升级。

## 1. 已核对的集成点

后端短路径均相对于 `backend/app/trading_agent/`，测试使用 `from app.trading_agent ...`；禁止另导入 backend.app，避免产生第二份数据库模块绕开测试隔离。

| 文件/接口 | 当前事实 | 新代码使用方式 |
|---|---|---|
| `harness.run_task(task_id, deps)` | 返回 AnswerDraft；最终 2.1 数据通过 store.finish 保存 | 新路径保留 Worker 返回约定，不自行创建任务 |
| `RuntimeDeps/RuntimeLimits` | 在 harness.py；model 是旧 next_turn 模型 | 增加 sdk_model 注入，不能把 next_turn 当 SDK Model |
| `store.finish(...) -> bool` | 仅当当前运行持有有效租约时终结并写消息 | False 视为未拥有任务，禁止再次 finish/投递 |
| `store.append_event(...)` | arguments 只存 hash，不存原 JSON | 不把它当任意 JSON 持久化；runtime 名可以存 status |
| `store.task_history(task_id,user_id,limit)` | 原助手正文被元信息替换，最多 12 条 | 保留授权正文与条件；不将历史正文当权限来源 |
| `MCPToolClient.call_tool(name, arguments, grant)` | 返回 ToolEnvelope；客户端 Authorization 可变 | 每任务独立客户端、任务内串行，grant 不暴露给模型 |
| `planner.apply_time_windows/validate_plan` | 已有纯规范化与审查 | 保留纯函数；新路径不调用旧 create_plan 的模型循环 |
| `answer.validate_answer21(principal,draft,store,...)` | 实际证据校验入口，重导出自 answer_v21 | delivery_gate 复用；不要再新造数字引用验证器 |
| `compare_values` | 返回 delta/delta_pct/comparison_status | 保留 missing_period/unstable_base/invalid_input 等区别 |

## 2. 固定设计决定（不要让 Luna 再做选型）

### 2.1 差异修正

1. 用户语义字段 `ranking_measure='pct_change'` 映射到既有结果 `delta_pct`；不改旧结果字段名，不新增 pct_change 数据列。
2. 不新增概念性的 `comparable` 列；根据 comparison_status、参与排序字段是否有限数值判定该项可比。
3. 保留 compare_values 对微小基数/负值的既有保护。unstable_base 可按已核验 delta 排数量变化，不可按 delta_pct 排比例；missing_period 不参与数值排名。
4. 新模型别名从既有 DEEPSEEK_MODEL 读取，保持 thinking disabled、max_tokens=4096 和模型名称一致。结构化输出协议可以因 SDK 适配而不同，必须记录差异，不能宣称协议完全相同。
5. 规划结构修正最多 1 次，最终答案业务修正最多 1 次；全部包含在模型总次数 6 内。Tool 参数验证回合也计入模型请求，底层 HTTP 自动重试关闭。
6. 新旧会话间只持久化既有合法用户/助手内容、服务端状态与证据；SDK 原始 tool-call 消息只保留当前任务内存，不新建原始消息持久化。重启后从已核验状态重建，不能伪造旧工具调用。
7. `needs_clarification` 是答案/质量显示状态，任务表仍用 partial；无证据失败用 failed；delivery_unknown 保留为 unknown，不强行映射为失败或已投递。

### 2.2 新文件与明确职责

| 新文件 | 只负责 |
|---|---|
| runtime_budget.py | 项目总请求/尝试次数、时长、取消 |
| pydantic_tools.py | typed 工具注册、调用现有 MCP、上下文 |
| egress_policy.py | 模型发送前字段/文本审查与脱敏，不另造搜索传输 |
| pydantic_runtime.py | SDK model 工厂、规划与执行两阶段连接 |
| runtime_dispatch.py | 选择新旧执行入口，不包含业务判断 |
| delivery_gate.py | 组合既有验证、覆盖、表现与最终内容检查 |

禁止为了每个工具额外建一个类/文件；现有业务模块只做方案明确的增量。新增辅助函数可局部实现，不得擅自搭建插件平台。

### 2.3 无歧义接口补充

以下是项目接口契约，不是可直接复制的 SDK API：

- `RuntimeBudget(limits, *, clock, started_at)`：limits 沿用 RuntimeLimits，clock 可注入；`reserve(kind)`、`remaining_models()`、`remaining_seconds()`、`cancel()`；公开只读 counters 字典 model/tool/search。异常 `BudgetExceeded(code)`；code 仅 budget_exhausted/deadline_exceeded/cancelled。
- `sanitize_model_payload(value, *, allowed_paths, sensitive_values) -> dict`：位于 egress_policy.py；只允许字典输入，按精确 JSON 路径投影，拒绝未知嵌套字段/身份字段，任何值命中 sensitive_values 则抛 `EgressDenied(code='private_content')`。sensitive_values 仅测试诱饵/已有任务内部标识内存值，不读密钥构建列表。不能把这个字符串检查称作完整 DLP；真实自然语言字段出网仍要主 Agent 确认字段策略。
- `rank_comparison_rows(rows, *, measure, descending, top_k)`：位于 dv_analysis.py；measure 为 value/delta/pct_change/abs_delta，映射 current_value/delta/delta_pct/abs(delta)，Decimal 排序；稳定 tie-break 为业务组键的规范元组，不能只用行号。
- `select_backend(user_id, conversation_id, config, *, bound_backend=None) -> str`：位于 runtime_dispatch.py；纯函数，不读环境/数据库。config 使用 §7 规定的字段。
- `migration_status(payload) -> dict`：位于 quality.py，输入既有 state/delivery_state/structured_payload 和服务端评估记录；输出 execution/answer/validation/delivery/human_review 五个键；缺值为 unknown/not_run/not_reviewed，不推断通过。
- `validate_migration_case(case) -> list[str]`、`validate_live_receipt(receipt) -> list[str]`：位于 eval 脚本，返回错误码列表，空列表仅代表结构合法。

ModelAnswer21/TaskPlan/Principal/ToolEnvelope 沿用现有类型，不另建同义对象。`ValidationSummary` 新增于 answer_contracts.py，字段 version、checks、unresolved_codes；version 固定 migration-v1。checks 七项齐全：scope/time/metrics/evidence/analysis/presentation/rendered_content，各项 passed/failed/not_applicable，模型不能写入。版本或必需项缺失就是 not_run。

## 3. L0：可复核样本与当前失败基线

**允许文件：** `evals/trading_agent_v2/migration_cases.jsonl`（新）、`evals/trading_agent_v2/README.md`、`scripts/run_agent_v2_evals.py`、`tests/agent_v2/test_eval_runner.py`。

**输入：** 母方案 §6 的历史线索与现有脚本。**输出：** 30 条场景定义、冻结时钟、独立 oracle、合成原路径测试结果。真实历史不可读时标 historical_unverified，不能伪造回执。

- [ ] 记录工作树、SHA、当前 Python/Node、隔离测试规则。执行 `git status --short --branch`、`git rev-parse HEAD`，不 fetch/push 正式分支。
- [ ] 先写下方定义检查测试，确认缺少校验器而失败。
- [ ] 新增 `--mode migration-definition`，读取 migration_cases.jsonl；不改旧 definition 含义，不触发数据库或网络。缺字段、重复 ID、非法 split、空 oracle 或冻结时钟缺时区都返回非零。
- [ ] 编制 30 个场景：库存 8、天气发运 6、持仓 4、连续追问 4、失败/状态 4、安全 4。前 24 条 regression，余 6 条 holdout；holdout 由主 Agent复核并冻结，不能把执行者已调试样本当真正未见泛化证据。
- [ ] 历史记录的错误、来源、版本未知即明确未知。快照只用合成数据或已授权脱敏材料；本批不访问正式数据。
- [ ] 运行 `env -u DATABASE_URL .runtime/agent-v2/bin/python -m pytest -q tests/agent_v2/test_eval_runner.py`；缺解释器先按 L1 建立候选环境，不临时安装到系统 Python。

固定可复制场景：

```json
{"id":"M-I01","origin":"synthetic","source_task_ref":null,"split":"regression","turns":["本周各港口库存是多少？"],"snapshot_id":"inventory-two-old-periods-v1","clock":"2026-09-15T10:00:00+08:00","expected_requirements":["current_week_inventory"],"oracle":{"available_dates":["2026-08-25","2026-09-01"],"requested_start":"2026-09-14","requested_end":"2026-09-15"},"forbidden":["old_data_labeled_current","answer_complete"],"required_evidence":["observation_date"],"expected_answer_state":"partial","live_required":false}
```

测试写在现有 test_eval_runner.py，复用其脚本加载方式，校验函数名固定：

```python
def test_migration_case_rejects_empty_oracle():
    from scripts.run_agent_v2_evals import validate_migration_case
    errors = validate_migration_case({'id': 'M-X', 'oracle': {}})
    assert 'empty_oracle' in errors
    assert 'missing:clock' in errors
```

**交付标准：** manifest 合法不能生成 business_pass；样本有独立预期；记录运行过什么，不复制历史 passed 数量。

## 4. L1：依赖候选与离线协议适配（批次 A 终点）

**允许文件：** `requirements.txt`、`requirements-agent-v2.in`、`requirements-agent-v2.lock`、`pydantic_runtime.py`（仅工厂）、`tests/agent_v2/test_pydantic_compatibility.py`（新）；测试环境为新建临时 venv。

**输入：** model.py 真实配置字段。**输出：** 精确依赖候选、来源/许可证/漏洞/兼容性核对、工厂离线协议测试。候选提交不等于批准上线。

- [ ] 先核查官方 PyPI 的稳定版本/依赖约束，保存包名、版本、wheel hash 和对应文档版本，不保存整份远程网页或系统环境。
- [ ] 只允许探索 pydantic-ai-slim 的 OpenAI/DeepSeek 兼容适配及必要传递依赖；不装完整 harness/logfire/evals/浏览器。是否需要 openai extra 以候选版本声明为准，不能为凑安装成功安装 all。
- [ ] 在 `mktemp -d` 返回的实际目录建立独立 venv，使用当前已支持 Python 版本。测试工具为 pytest/pytest-asyncio，漏洞审查工具仅在临时环境，不能加入 Production requirements。
- [ ] 解析候选锁，比较原锁所有差异；pip check、Web import、现有 MCP 真 loopback 测试。出现必须升级无关 Web/MCP 主版本则停止，不自行兼容大改。
- [ ] 新增 `create_sdk_model(*, api_key, base_url, model_name, http_client)` 工厂，参数均由受保护配置或测试注入；工厂不从聊天获取 Key，不记录值。创建对象不能立即联网。
- [ ] 使用候选版本官方 TestModel/FunctionModel 或 HTTP fake：测试工具参数/返回序列化、结构化中文输出、未知字段拒绝、timeout、401、429、5xx、取消。assert 未用 next_turn；SDK 默认重试必须显式关闭。
- [ ] 测试 provider 发送的 model/thinking/max_tokens、目标主机、TLS 开启、禁止重定向和代理配置；实际 SDK 的参数名写入测试，不虚构跨版本 API。
- [ ] 运行新环境的 `python -m pip check` 和 `python -m pytest -q tests/agent_v2/test_pydantic_compatibility.py tests/agent_v2/test_sdk_runtime.py`。

必须返回的兼容矩阵：Python/FastAPI/Pydantic/MCP/Pydantic-AI/Provider 版本、变更原因、安装结果、测试结果、已知风险。未知风险写 unknown，不写“安全”。

**强制停点 A：** 主 Agent 根据候选做依赖选择；并在可核实预算内执行合成真实模型协议探针。Luna 不持有预算批准时不得执行该探针。此门通过后才能开始 B。

## 5. L2：业务契约、比较与历史

**允许文件：** planning_contracts.py、planner.py、capability_catalog.py、semantic_catalog.py、dv_contracts.py、dv_analysis.py、conversation_state.py、store.py、prompts.py；测试 `test_migration_semantics.py`（新）、test_task_coverage.py、test_store.py、test_position_semantics.py。

**输入：** 母方案 AnalysisSpec 与当前字段。**输出：** 一个审查后计划、可核算比较结果、未丢失的授权历史。

- [ ] 先写固定排序、零基期/微基期、coverage 空集、追问实际正文、时间范围测试。
- [ ] AnalysisSpec 按母方案增加，但 canonical 结果字段按本文 §2；Comparison 的显式基期通过 existing filters 解析，不在模型自然语言里留未解析时间。
- [ ] rank 的完整集在 compare_dataset 已加载的不可变 saved.rows 上处理，不能从 read_result_page 第一页排序。只在 args 指定排名时截 top_k，旧调用不改变结果顺序；返回排名方法、参与/排除计数、范围、基期和原 row_ref。
- [ ] 保留价格/库存各自 allow_pct 与 MICRO_BASE 规则，不把所有指标强制套一个百分比公式；负库存等 invalid_input 不能悄悄进入完整答案。
- [ ] coverage 要求 ID 恰好等于 plan 的 ID；匹配 target、dataset、filters、period、metric 和真正 delivery blocks，不能只要 envelope 非空就 answered。
- [ ] current_week 与 latest 分开；无本周观测时只能说明缺口＋可选最近数据。两个不同观测期不算重复，同业务键/期冲突不自动求平均。
- [ ] task_history 返回上一轮实际交付正文及结构状态，但撤权后不得因正文已保存就重发敏感历史。引用受限/过期时，对应正文从模型上下文排除；其他无关主题不继承。
- [ ] 能力目录从授权工具与数据语义读取；没有天气内部数据就允许合法 external requirement，不建立发运/库存关键词一刀切 internal-only。
- [ ] 运行母方案 P3 指定四组 pytest，再运行本批新增测试。

新增测试可直接落地：

```python
from decimal import Decimal
from app.trading_agent.dv_analysis import compare_values, rank_comparison_rows

def test_delta_ranking_uses_existing_field_contract():
    rows = [
        {'port': 'A', 'delta': '-10', 'delta_pct': '-10', 'comparison_status': 'complete'},
        {'port': 'B', 'delta': '-40', 'delta_pct': '-20', 'comparison_status': 'complete'},
        {'port': 'C', 'delta': '5', 'delta_pct': '10', 'comparison_status': 'complete'},
        {'port': 'D', 'delta': None, 'delta_pct': None, 'comparison_status': 'missing_period'},
    ]
    ranked = rank_comparison_rows(rows, measure='pct_change', descending=False, top_k=2)
    assert [r['port'] for r in ranked] == ['B', 'A']
    assert all('pct_change' not in r for r in ranked)

def test_small_baseline_protection_is_preserved():
    value = compare_values(Decimal('1'), Decimal('0.001'),
                           allow_pct=True, micro_base=Decimal('0.01'))
    assert value['delta'] == '0.999'
    assert value['delta_pct'] is None
    assert value['comparison_status'] == 'unstable_base'
```

**升级条件：** 业务排名默认口径无法从已有规则确定、净买净卖含义不清、未知指标字段没有来源。不得用随意默认掩盖。

## 6. L3：工具权限、出网和全局预算（批次 B 终点）

**允许文件：** pydantic_tools.py、runtime_budget.py、egress_policy.py（新）；必要修改 mcp_client.py/research_policy.py；新测试 test_pydantic_tools.py/test_runtime_budget.py/test_model_egress.py。

**输入：** 已批准 SDK 版本、L2 计划、现有 tools.tool_schemas 与授权链。**输出：** 每任务独立工具上下文、受控模型摘要、同一预算计数器。

- [ ] 先写参数额外身份字段、跨会话结果、撤权、审计写失败、预算原子性、取消的失败测试。
- [ ] AgentRunContext 使用母方案字段；一个 MCP 连接只属于一个任务，不复用全局 grant。工具名/输入 schema 来自既有工具白名单，各自注册；禁止公开 invoke_registered_tool 万能入口给模型。
- [ ] 服务端先发行受限 grant；允许既有能力描述，但公开能力仍关闭。TaskPlan 通过 validate_plan，并且 task_plan/research_policy 两项审计写入均成功后才允许公开工具。任一失败时不把候选计划当批准计划；不静默回退成“内部已完整回答”。
- [ ] 工具执行业务身份检查仍在原 MCP/dispatch 端，目录隐藏仅为便利不是安全保证。SDK 包装调用前再检查允许工具和参数，完成后只给模型最小摘要。
- [ ] evidence 已由原业务工具保存时不再次保存同一完整快照；运行 context 只收集 envelope/ref，模型只见政策允许内容。
- [ ] 模型允许字段先覆盖合成测试：result_ref/kind/数据日期/单位/canonical 数值/公开港口名；客户、合同、订单号、grant、原始 SQL、异常 repr 默认拒绝。真实字段策略需要主 Agent在协议探针后按业务数据审定，Luna 不自行放宽。
- [ ] 字符串不能仅靠 key 名白名单：允许字段里嵌入敏感诱饵同样阻断；自然语言消息采用经审查的模板/已授权历史，禁止把工具原始 JSON dump 拼进提示词。
- [ ] reserve('search') 同时消耗 tool/search，原子检查；工具失败也计数。model 包含规划、参数修正、答案修正，底层请求 hook 计数一次，不能入口与 hook 重复计算。
- [ ] 新 RuntimeBudget 默认 asyncio 单事件循环使用、reserve 无 await；若需跨线程调用则加锁，不能依赖 GIL 多步修改。MCP 工具串行，避免提前批量调用越限。
- [ ] 测试网络目的地、禁私网/重定向、日志脱敏、HTTP header 不被追踪；关闭 telemetry 不是阻止模型收到业务内容，分别记录两种控制结果。
- [ ] 定向测试全通过后运行 Agent 全量 pytest，交回主 Agent进行安全审查。

预算测试：

```python
import pytest
from app.trading_agent.harness import RuntimeLimits
from app.trading_agent.runtime_budget import RuntimeBudget, BudgetExceeded

def test_search_reservation_does_not_partially_increment():
    clock = lambda: 10.0
    b = RuntimeBudget(RuntimeLimits(max_tools=1, max_search=2), clock=clock, started_at=10.0)
    b.reserve('search')
    assert b.counters == {'model': 0, 'tool': 1, 'search': 1}
    with pytest.raises(BudgetExceeded):
        b.reserve('search')
    assert b.counters == {'model': 0, 'tool': 1, 'search': 1}

def test_cancel_prevents_next_request():
    b = RuntimeBudget(RuntimeLimits(), clock=lambda: 10.0, started_at=10.0)
    b.cancel()
    with pytest.raises(BudgetExceeded) as err:
        b.reserve('model')
    assert err.value.code == 'cancelled'
```

出站测试：

```python
import pytest
from app.trading_agent.egress_policy import sanitize_model_payload, EgressDenied

def test_private_value_in_allowed_field_is_rejected():
    with pytest.raises(EgressDenied):
        sanitize_model_payload({'summary': '客户 INTERNAL-CUSTOMER-CANARY'},
            allowed_paths={'summary'}, sensitive_values={'INTERNAL-CUSTOMER-CANARY'})
```

**强制停点 B：** 权限、安全与语义测试通过才进入 C；测试中的敏感诱饵检查不等于完整数据泄露审计，不得这样写回执。

## 7. L4：两阶段 SDK 主链与服务端路由

**允许文件：** pydantic_runtime.py、runtime_dispatch.py（新）、harness.py、worker.py、store.py；新测试 test_pydantic_runtime.py/test_runtime_dispatch.py，现有 test_worker.py/test_harness.py。

**输入：** L1 工厂、L2 计划、L3 工具/预算。**输出：** Worker 不变业务契约的新路径，默认仍 legacy。

- [ ] 先用 SDK TestModel/FunctionModel 测“规划→两工具→答案”；测试要求 store.finish 最多成功一次、grant 最终撤销、没有旧 next_turn 调用。
- [ ] RuntimeDeps 只增 `sdk_model: object | None = None`；生产工厂从既有配置创建，测试注入 SDK fake。shared RuntimeLimits 原地保留，避免与旧 harness 循环互相 import。
- [ ] 规划输出 TaskPlan，先服务器规范化再审查/审计。新路径直接使用 SDK 模型运行，不调用 planner.create_plan 旧协议。
- [ ] 执行输出 ModelAnswer21。SDK 负责工具循环；项目只能组织两阶段和已有 policy，不重新手写 while next_turn 协议。参数与最终输出的 SDK 错误也必须纳入共同 deadline/budget。
- [ ] 运行中只发 progress，不流式发布未核验业务正文；最终质量 gate 的结果才允许写入助手消息。
- [ ] store.finish 的 False 必须传播为 lease_lost，不重发；finally 撤销 grant、关闭客户端。CancelledError 原样传播给 Worker，不能 broad except 转成成功。
- [ ] Worker 改为调用 runtime_dispatch.run_task；保留 heartbeat/claim_next/fail_owned/wecom 投递行为，避免双投递。
- [ ] 路由配置由服务端读取：backend=legacy|pydantic（默认 legacy），pilot_user_ids 整数集合（试点时非空），disabled 布尔（紧急开关）。环境名为 AGENT_V2_RUNTIME_BACKEND、AGENT_V2_RUNTIME_PILOT_USER_IDS、AGENT_V2_PYDANTIC_DISABLED；全部默认不启用新路径。
- [ ] select_backend 顺序：disabled→legacy；已绑定且合法→绑定；backend 非法→配置错误；新会话配置 pydantic 且命中 pilot→pydantic；其余 legacy。全面开放只能主 Agent另行配置，不能把空 pilot 列表解释为全员。
- [ ] 持久绑定用现有 agent_v2_events 的 `kind='runtime_selected', status='legacy'|'pydantic'`，通过 task JOIN conversation 读取最早选择。新 store.bind_runtime(principal, desired)->str 在一个事务锁 conversation 后查/写，SQLite 沿用 _transaction，PG 行锁；不把 arguments hash 当可回读 metadata。该函数执行时核验当前任务所有权。
- [ ] 紧急禁用不改历史绑定；恢复后绑定仍可追溯。一个会话同时领取两任务测试只能产生一致绑定；主 Agent确认全面切换后才扩展配置，不在本批增加随机流量路由。
- [ ] 运行 `env -u DATABASE_URL .runtime/agent-v2/bin/python -m pytest -q tests/agent_v2/test_pydantic_runtime.py tests/agent_v2/test_runtime_dispatch.py tests/agent_v2/test_worker.py tests/agent_v2/test_harness.py tests/agent_v2/test_store.py`。

纯路由断言：

```python
from app.trading_agent.runtime_dispatch import select_backend

def test_empty_pilot_does_not_enable_everyone():
    config = {'backend': 'pydantic', 'pilot_user_ids': set(), 'disabled': False}
    assert select_backend(1, 101, config) == 'legacy'

def test_emergency_disable_overrides_binding():
    config = {'backend': 'pydantic', 'pilot_user_ids': {1}, 'disabled': True}
    assert select_backend(1, 101, config, bound_backend='pydantic') == 'legacy'
```

**升级条件：** 既有 schema 限制无法保存指定事件/事务绑定、模型兼容失败、需新增运行服务或改变部署架构。不能偷偷改 schema。

## 8. L5：最终答案、真实状态和回执核验（批次 C 终点）

**允许文件：** delivery_gate.py（新）、answer_contracts.py、answer_v21.py、answer.py（仅必要重导出）、coverage.py、presentation.py、quality.py、quality_routes.py、harness.py、pydantic_runtime.py；frontend/agent_quality.js、frontend/closing_review_agent.js；eval 脚本；对应母方案 P4/P5 测试和 test_delivery_gate.py、tests/agent_quality_migration.test.mjs。

**输入：** ModelAnswer21、TaskPlan、授权证据与原 validated 协议。**输出：** 七维完整检查、中文最终文本、五维质量状态、可审查真实回执。

- [ ] 先写“行。”、缺排名结论、纯文字出现表格、相同结论重复、覆盖空集、旧 delivered 记录测试。
- [ ] validate_delivery 调用现有 answer.validate_answer21，再 assess_evidence/assess_delivery；返回 ValidatedAnswer21 并带 ValidationSummary。每项 checks 必须有实际计算依据，不能把所有字段设 passed。
- [ ] 对失败块按整个 block 删除或要求修正，删除后重新编译 offset/ref/dependency；不对字符串切片后直接发送。数字变动、时间标题、单位、views 必须在最终渲染再核对。
- [ ] 有可用证据则有限修正一次；达预算或缺数据则保留完整缺口说明。安全错误不重试越权请求。需要正文却只有 snippet 的外部需求不得 answered。
- [ ] 排名结论绑定确定性结果；若正文宣称最大对象与结果不一致，failed analysis。没有“最大/前三”结论仅罗列表格也失败；缺基期需说明排除范围。
- [ ] 五维显示：delivery_unknown→unknown；无 ValidationSummary→not_run；checks 缺项/版本未知→not_run；任一 failed→failed；应检查项不可全部 not_applicable；人工 incorrect→rejected，needs_review/无记录→not_reviewed，只有用户正式 correct→accepted。主 Agent技术复核另记 reviewer，不伪装用户人工意见。
- [ ] needs_clarification 时正文是具体缺少的选择，后台终态 partial。未迁移旧记录的 answer 不可直接推断 complete；显示“历史结果，未按新标准检查”。
- [ ] `--mode live-import --receipts` 必须验证真实性材料，不信任 receipt 自报业务 passed；增加 validate_live_receipt。缺最终答案可回读引用/工具证据/版本则 unverifiable。
- [ ] live-import 默认无网络、无 DB persist；读取受控 bundle 中最小脱敏 final_answer/evidence/trace，逐项按 oracle 计算。只有 URL/ref 而无本地核对材料不算验证。主 Agent另通过实际页面/服务回读确认 bundle 与任务一致。
- [ ] 外部完整研究要求 search_public + read_public 的有效轨迹与正文日期/地点，不能从 search_calls>0 推断有正文。不是所有题型都强制搜索或固定工具顺序。
- [ ] Eval 保留 definition_pass、offline_pass、answer_checks、provenance_verification、human_review 各自结果；replay/test fake 与实际 live 严格区分。
- [ ] 运行母方案 P5 测试和 `env -u DATABASE_URL .runtime/agent-v2/bin/python -m pytest -q tests/agent_v2`，再 `node --test tests/agent_v2_frontend.test.mjs tests/agent_quality_migration.test.mjs`。

真实回执结构检查测试：

```python
def test_receipt_cannot_self_declare_success():
    from scripts.run_agent_v2_evals import validate_live_receipt
    errors = validate_live_receipt({'case_id': 'W01', 'passed': True})
    assert 'missing:task_id' in errors
    assert 'missing:deployment_sha' in errors
    assert 'missing:final_answer_ref' in errors
```

**强制停点 C：** 返回可审查代码、测试结果、依赖锁和剩余风险。未有真实业务测试时必须写“本地实现完成，真实验收未做”，不能写“Agent 迁移全部完成”。

## 9. L6：真实调用/业务验收交接单（主 Agent 执行）

Luna 仅准备本节执行材料。主 Agent审查代码、安全与余额后，按既有授权选择性发布，不整分支合并，不以免费 Staging 不可用替代正式验收。

真实用例必须从页面新建授权测试会话，记录具体部署 SHA，不冒充历史任务。每个主要场景新旧路径使用同一冻结数据依据；无法冻结的天气网页单列实时差异。

| 顺序 | 输入/过程 | 必查内容 |
|---|---|---|
| R0 | 合成数字工具＋结构化中文回答 | 当前模型协议、HTTP尝试数、无业务数据外发 |
| R1 | 本周库存→为什么两个数→与上期比→按数量降幅前三→简短文字 | 日期/基期/全量排名/追问/中文/最终展示/状态 |
| R2 | 根据近期天气分析发运风险 | 内部只读查询、搜索、正文、地点日期、推断边界 |
| R3 | 与 R2 同题但明确不要联网 | 搜索 0、外部缺口清楚 |
| R4 | 净买 Call、净卖 Put 分别数量与浮盈→只看 Put | 原确定性口径、多对象不混算、缩小范围 |
| R5 | 只查内部库存数值、明确时间 | 搜索 0、数字日期与源证据一致 |
| R6 | 现有真实服务发生不可用时核对降级 | 保留失败分类和部分结果；不在正式环境人为改 Key 制造故障 |

主方案要求 6 个代表性多轮场景各重复 3 次；主 Agent从 R1—R6 锁定具体输入与当期日期。真实外部失败不能自然遇到时，该失败分支保留离线已测/真实未测，不能伪造。成本余额不够时报告未完成批次并停止。

每次记录：真实 task_id、会话、版本、backend、模型设置 ID、数据快照/正文引用、工具名/状态/计数、最终答案引用、校验结果、耗时（秒）、模型费用/搜索用量、失败与未测项。

上线放行使用母方案 §7：关键测试全通过，样本误报完整为 0，有数据时不能全拒答刷分，新旧同口径质量不退步；费用/时延门槛为待执行试点标准而非已获预算。小样本 P95 仅作观测，不包装为稳定总体性能。

## 10. L7：默认切换与清理（另行下发）

- 新路径正式切换由主 Agent确认；Luna 不自行放开 pilot。
- 回退优先停止接收新 pydantic 任务，活动任务按租约完成/取消，紧急禁用切 legacy。不得自动把失败任务整链重跑。
- 共享门禁或语义回归时，runtime 开关不足以恢复，使用预先记录的完整可回退 SHA。保留审计/对话/交易事实。
- 至少 20 个真实后续任务且不少于 3 个使用日，通过主 Agent审查后才清理旧循环；不自行创建监控自动化。
- 旧代码删除需先 rg 检查引用、跑全量回归、单独提交，不能删除进程监督/原 MCP/事实服务。新主链依赖 RuntimeDeps 等类型时先迁移公共类型再删旧循环，不能删断引用。

## 11. 每批回执（必须用实际值）

```text
批次与任务：
工作树/分支/起始SHA/最终SHA：
业务结果：已实现什么；仍不能证明什么。
文件与接口变更：
测试命令及 passed/failed/skipped 数量：
失败、跳过、未执行的原因：
是否使用真实模型/搜索/正式数据：
依赖候选/已批准版本与差异：
安全负例结果与未验证的网络边界：
费用：未调用写0；无法核实写未知，不能填0。
需主 Agent决策事项：
下一批：未授权/等待复核，不自动执行。
```

不要用无业务依据的覆盖百分比，不以测试文件存在当通过。不提交 .runtime、密钥、原始网页/数据库快照或整份模型日志。

## 12. 可直接发给 Luna Max 的启动指令

> 请按 `docs/superpowers/plans/2026-09-15-pydantic-ai-luna-max-taskbook.md` 执行批次 A（L0、L1），同时读取母方案 `docs/superpowers/plans/2026-09-15-pydantic-ai-controlled-migration.md`。先核对实际工作树、SHA、两份文档是否齐全和现有测试隔离。只做本地样本/验收定义、最小依赖候选、SDK 工厂与离线兼容性测试；不得调用真实模型或搜索，不访问 Production，不更改业务规则，不创建子 Agent。按文件白名单逐任务 TDD、审查差异并单独提交。完成批次 A 后按任务书回执格式报告并停止，等待主 Agent确认精确依赖与真实模型协议后再进入 B。遇到依赖冲突、缺失关键业务规则、权限或网络安全未知，不自行放宽约束，提交证据及最小决策。不得将合成测试或安装成功表述为真实业务验收通过。

## 13. 文档核对边界

本轮仅生成执行文档，未安装/运行 Pydantic AI、未发起真实测试、未部署。SDK API 待 L1 固定版本后按该版本验证，不能把本文项目函数名当官方 API。

官方参考：[工具校验与重试](https://pydantic.dev/docs/ai/tools-toolsets/tools-advanced/)、[测试模型与阻止真实请求](https://pydantic.dev/docs/ai/guides/testing/)。这些机制不替代本文的权限、全局预算和业务验收。
