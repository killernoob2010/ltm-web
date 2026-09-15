# Agent 正式验收失败修复执行计划

> **For agentic workers:** 使用 `superpowers:executing-plans`，由一个执行者顺序完成；不自动创建子 Agent。用户自行转交本计划不等于授权 Production、真实模型或新业务决策。

**Goal:** 修复 #137 暴露的计划时间/指标偏离、证据范围误判、引用规则遗漏和修正反馈丢失；在保留权限与证据门禁的前提下，让有证据的数量能被正确交付。

**Architecture:** 保留现有 SDK 两阶段主链、MCP、确定性交易计算、Worker 生命周期和管理员试用路由。优先修正服务端规范化及各阶段契约，不另建自然语言解析框架、不更换 SDK、不改交易公式。

**Tech Stack:** 仓库现有 Python 3.12、Pydantic AI 2.43.0、MCP、pytest、原生 JavaScript；沿用已锁依赖。

**Spec:** [A/B/C 修改与失败根因检查](../analysis/2026-09-15-agent-abc-change-and-failure-review.md)。同时读取原 [分批任务书](2026-09-15-pydantic-ai-luna-max-taskbook.md) 的边界及接口约定。本计划细化检查报告 F1—F6，优先于其中概括性的修复建议。

## 1. 工作量与本次选择

这是中等偏大的集成修复：约 6—9 个产品文件、4—6 个测试文件，分四个可独立审查的提交包。主要工作量在业务边界、诊断贯通和行为复现，不在替换框架。

粗略人工工程量参考为 1—2 个工作日（本地实现、离线测试和自审），不是模型运行时间承诺；正式部署、真实复验和返工另计。不能承诺改几行提示词即可完成。

本次主 Agent 只产出计划与诊断文档，不顺手修改某个小点：共享 planner/coverage/delivery_gate 同时影响新旧路径，先单改时间或放松范围可能导致误报通过，先改提示词也不能解决错误反馈丢失。

## 2. 基线与执行边界

- 产品基线：正式部署代码 `efb63f944947fffa03286439de8c52804f4981bf`；开始编制时本地 HEAD `66ad2c8`，是同产品代码的文档后继。
- 工作树：`/Users/wangjingze/.codex/worktrees/447c/轻量化交易管理系统WEB`；分支 `codex/agent-batch-c-six-fixes-20260915-447c`。诊断和本计划会形成后续纯文档提交，执行时以实际 HEAD 和 diff 核对，不假定必须等于66ad2c8。
- 项目主目录的旧 staging 存在无关修改，禁止作为修复来源。若另建隔离工作树，从包含本计划的实际提交出发；不能复制其他目录的 .env、凭据或运行数据库。
- 上轮已核实正式新运行时停用。本计划不授权启用、更改环境变量、push、merge main、部署或回滚正式代码。
- 只用合成数据/临时 SQLite/SDK FunctionModel，禁止真实模型/搜索调用，不读取密码、Key、Cookie、完整数据库连接串，不再查询正式业务数据。
- 不更改交易事实、净额正负口径、估值来源、权限、DB schema、依赖版本、进程监督、资源限制。无新增业务工具或数据源。
- 仍保留模型6次、工具8次、搜索2次、总90秒、每次模型15秒、每次工具25秒；答案修正最多1次、finish至多一次、取消后停止及最终撤销授权。
- 所有时间展示到秒。不得为了测试通过将失败强改complete、将事实改knowledge、展示无证据数字或关闭检查。
- 执行者不得覆盖他人修改；发现同文件并行变更先对齐。默认单执行者，不派生其他 Agent。
- 本地回归完成后停止，返回提交、逐项证据和未测项；不能自行声明真实业务验收通过。

## 3. 对原修复建议的四处调整

1. **时间和范围一起修。** 不能只把2024改成2026；当前持仓是latest快照，不是最近14天。positions域还可能包含trades/closes，不能把所有positions目标都强改latest。
2. **区分交付指标与辅助取数字段。** 用户只要数量时不增加浮盈需求，但允许既有确定性服务为计算净额读取买/卖数量；不能因此先过滤掉卖方或改变净买净卖符号。
3. **引用协议和修正反馈一起验收。** 最小共享规则优先，避免新旧复制两套提示；“增加一串参考路径”不是必做的新功能，现有指标/分组路径足够时不扩展工具输出。
4. **源时点缺失只允许诚实部分交付。** latest表示取数选择，不保证数据源实时；有数量无data_as_of时保留可核验数量和未知时点说明，不能因为有候选就写observed，也不能把所有内容删光刷安全分。

## 4. 开工检查

- [ ] 按项目规则阅读 AGENTS、README、流程与交接；以最新发布记录及本计划核对过时的README候选描述。
- [ ] 运行下列只读命令，确认基线、文档及产品文件未被他人改变。

```sh
git status --short --branch
git rev-parse HEAD
git log -5 --oneline
git diff efb63f9 -- backend frontend requirements.txt requirements-agent-v2.lock
```

- [ ] 读取 `tests/agent_v2/conftest.py`，使用 `app.trading_agent` 导入和既有隔离fixture，禁止混用 `backend.app` 生成另一份数据库模块。
- [ ] 先运行下列已知相关基线；上轮为34项通过，本次必须返回新结果。

```sh
env -u DATABASE_URL .runtime/agent-v2/bin/python -m pytest -q tests/agent_v2/test_batch_b_review.py tests/agent_v2/test_batch_c_acceptance.py tests/agent_v2/test_model_tool_projection.py tests/agent_v2/test_pydantic_runtime.py
```

不存在解释器时只按仓库锁建立隔离环境，不安装到系统环境、不复制凭据。

## 5. 包一：计划忠实于问题，时间与范围匹配

**修复编号：P1/P2；对应根因G1/G2/G3。**

**文件：**
- Modify: `backend/app/trading_agent/planner.py`
- Modify: `backend/app/trading_agent/pydantic_runtime.py`（规划输入及规范化调用）
- Modify: `backend/app/trading_agent/coverage.py`（仅时间期望构造/匹配）
- Modify if needed: `backend/app/trading_agent/planning_contracts.py`（只允许可选兼容字段，须说明具体用途）
- Test: 新增 `tests/agent_v2/test_production_plan_repair.py`；补充既有 `test_planner.py` / `test_task_coverage.py`。

**接口：**保留 `apply_time_windows(task_plan, user_text, *, now=None) -> TaskPlan`，现有调用保持兼容；若需要可信历史参数只能以可选keyword加入。`validate_plan(plan,catalog,restrictions=None)` 仍负责合法性，不让模型自报的origin成为日期来源证明。覆盖入口 `assess_evidence(plan,envelopes)` 不改调用约定。

- [ ] 先增加失败样本：用户说“当前期权只要手数”，模型返回2024 default窗口；固定业务时钟2026-09-15，规范化后不允许保留该窗口。
- [ ] 增加第二个失败样本：模型窗口为空、time_requirement为中文“当前持仓（最新快照）”；结果应是latest快照语义，而非最近14天。
- [ ] 增加真实失败形状的范围样本。必须用ToolEnvelope，不能用普通dict冒充：

```python
from app.trading_agent.contracts import ToolEnvelope

def latest_call_envelope():
    return ToolEnvelope.model_validate({
        'status': 'complete',
        'result_ref': '00000000-0000-0000-0000-000000000001',
        'captured_at': '2026-09-15T11:00:28Z',
        'calculation_version': 'synthetic-repair',
        'payload': {'kind': 'positions',
            'selection': {'asset_type': 'option', 'direction': 'all',
                          'filters': {'option_type': 'call'}},
            'as_of': {'mode': 'latest', 'date': None}},
        'metrics': {'net_quantity': {'value': '12', 'unit': '手',
            'status': 'complete', 'covered_rows': 1, 'eligible_rows': 1}}
    })
```

数字12是合成值，不是正式验收真值。原代码在错误2024窗口下必须复现scope_mismatch；修复规范化后同份数量证据可匹配，不能再报metric.net_quantity缺失。源时点未知由包三单独处理。

- [ ] 增加“不额外问浮盈/比较”的计划样本；去除 `PLANNER_SYSTEM` 强制附加浮盈的规则，改为按用户所问指标设置交付目标，保留双边净额与Call/Put拆分。
- [ ] 在规划输入提供服务端固定时区的当前业务日期，使用传入时钟便于测试。对日期来源进行服务端核对：当前问题的明确日期、可信授权历史继承、服务端默认分开；模型自行声称user/default不算证明。
- [ ] 对明确“只数量/不看浮盈”等限制做服务端最小一致性审查；不创建通用自然语言解析器。模型增加的无依据指标/比较不能直接进入执行计划；可确定的冲突规范化，无法安全判定的歧义返回明确规划失败/澄清，不猜测新业务规则。
- [ ] 时间判定至少保留以下反例：明确2024历史问题不能被改成今天；2701/2610是合约月份；本周库存不能退化latest；成交期间不能变成当前持仓；混合内部持仓与外部天气不能共享一个错误默认期间；“只看Put”不得丢失授权继承条件。
- [ ] 运行新增和直接影响测试；确认失败来自行为，而非导入/fixture；最小修改后重跑。

```sh
env -u DATABASE_URL .runtime/agent-v2/bin/python -m pytest -q tests/agent_v2/test_production_plan_repair.py tests/agent_v2/test_planner.py tests/agent_v2/test_planning_contracts.py tests/agent_v2/test_task_coverage.py tests/agent_v2/test_migration_semantics.py tests/agent_v2/test_position_semantics.py
```

- [ ] 自审并仅提交本包明确文件，提交说明 `fix(agent): normalize requested scope before execution`。

**停点：** 如果无法从既有业务语义区分latest持仓和期间事实，不自行扩大工具契约或交易规则，交回主 Agent 说明具体冲突。

## 6. 包二：引用协议与一次修正反馈贯通

**修复编号：P3/P4；对应G4/G5。**

**文件：**
- Modify: `backend/app/trading_agent/prompts.py`
- Modify: `backend/app/trading_agent/pydantic_runtime.py`
- Modify: `backend/app/trading_agent/delivery_gate.py`
- Modify if needed: `backend/app/trading_agent/pydantic_tools.py`（仅现有准许字段的引用提示）
- Test: 新增 `tests/agent_v2/test_production_reference_repair.py`，必要补充 `test_pydantic_runtime.py` / `test_delivery_gate.py`。

**接口：**抽取 `prompts.EVIDENCE_REFERENCE_GUIDANCE: str`，由旧SYSTEM_PROMPT、新execution_agent及repair_agent共用；不得只在普通user消息里塞一份而漏掉修正系统规则。保留 `_repair_feedback(plan,validated,envelopes)->dict`；在已有有限字段中增加安全的底层错误信息，或新增最多80项的 `span_issues`，每项只允许合法片段ID和已知错误码。不返回原文、异常repr或模型私有数据。

- [ ] 先写完整反馈链复现：构造 ValidatedAnswer21，limitations含invalid_reference及s1；经过validate_delivery后进入_repair_feedback，必须保留invalid_reference与合法片段关联。现状会丢码。
- [ ] 把引用规则抽成最小共享常量，覆盖正文fact语法、blocks.refs、行/分组路径、metadata_ref、公开正文引用与无效值边界。保持旧模型输出约束不变，不复制一套逐渐分叉的规则。

```python
# 必须包含的合法/非法对照，UUID为合成示例。
valid = '{{fact:00000000-0000-0000-0000-000000000001#/metrics/net_quantity}}'
invalid = '{{fact:00000000-0000-0000-0000-000000000001#/metrics/net_quantity/value}}'
# ModelAnswer21接收字符串不是最终业务通过；使用实际引用解析/验证入口核对。
```

- [ ] 在门禁摘要/反馈中合并底层limitations的已知安全错误码，并去重、截断；未知字符串不得伪装错误码出站。保留现有scope/metric/coverage诊断，不能用底层错误覆盖它们。
- [ ] 用实际 SDK FunctionModel 检查执行和修正消息：缺引用规则就返回错误引用；只有收到invalid_reference及片段ID才修正。不能按“第5次调用”无条件返回正确答案。
- [ ] 在临时SQLite中保存合成结果，正确修正后由真实store加载和answer校验，断言最终正文包含正确替换的数量；不得只检查状态complete或含“已核验”。
- [ ] 对照错误范围计划：即使引用语法修好也不能冒报完整，不能在修正阶段偷偷重规划或新增工具。
- [ ] 验证现有search/source_ref、public_read正文、持仓semantic_groups/截断投影仍保留，敏感诱饵不会进入新增诊断。

```sh
env -u DATABASE_URL .runtime/agent-v2/bin/python -m pytest -q tests/agent_v2/test_production_reference_repair.py tests/agent_v2/test_pydantic_runtime.py tests/agent_v2/test_delivery_gate.py tests/agent_v2/test_model_tool_projection.py tests/agent_v2/test_model_egress.py tests/agent_v2/test_batch_c_acceptance.py
```

- [ ] 自审并提交本包文件，说明 `fix(agent): carry evidence grammar and validation feedback through SDK`。

## 7. 包三：源时点与部分交付，防止误报通过

**修复编号：P5；对应G6及交付目标。**

**文件：**
- Modify: `backend/app/trading_agent/coverage.py`
- Modify: `backend/app/trading_agent/delivery_gate.py`
- Modify if needed: `backend/app/trading_agent/quality.py`、`answer_v21.py`（只在现有结果保留/映射确需修正时）
- Test: 新增 `tests/agent_v2/test_production_time_delivery.py`，复用`test_task_coverage.py` / `test_quality.py`。

**接口：**继续使用RequirementCoverage.time_status和现有partial/failed协议；不新增“半通过”顶层状态、不改DB。未知时点为unknown，observed只能有可核验的源时点/期间元数据。`captured_at`只表示查询保存时间，`as_of.date`只表示选择条件，均不能单独证明源时点。

- [ ] 先用包一的无data_as_of样本复现：旧代码有候选就observed。新断言time_status=unknown，不能将时间检查记passed。
- [ ] 增加具备真实合成源时点的正例和期间覆盖不全的负例；数据集按登记观测日期覆盖用户期间，公开正文按有效日期/地点/需求关联，不能一律拿抓取时间当事实日期。
- [ ] 构造已通过引用核验的数量正文，源时点未知：保留数字及来源、补明确未知说明，整体partial；不是全删为空failed，也不是complete。
- [ ] 浮盈缺失但用户只问数量时，不把浮盈变成必答缺口；用户明确问浮盈时必须保留该缺口，不能补零。
- [ ] 质量列表、详情、summary读取同一最终状态；未人工评价仍not_reviewed。若必须改前端映射，只改必要状态消费者并运行其既有渲染测试，不重做页面。

```sh
env -u DATABASE_URL .runtime/agent-v2/bin/python -m pytest -q tests/agent_v2/test_production_time_delivery.py tests/agent_v2/test_task_coverage.py tests/agent_v2/test_delivery_gate.py tests/agent_v2/test_quality.py tests/agent_v2/test_harness.py
```

- [ ] 自审并提交本包文件，说明 `fix(agent): preserve unknown source time and verified partial quantities`。

## 8. 包四：完整链路回归与交接

**修复编号：P6；解决测试盲区。**

**文件：**在前三包新测试文件及现有test_pydantic_runtime中补集成用例；文档只更新现有handoff与本计划勾选状态，不将未部署结果写入发布记录。

- [ ] 必须用中文原问题跑 SDK合成链：计划→Call/Put工具→带月份分组的答案→真实引用解析→覆盖门禁→store保存。检查最终内容、对象、分组、缺口、搜索次数，而非只有调用次数或状态。
- [ ] 对“只看Put”的连续问题，使用已保存授权历史，检查Call不混入、月份/格式限制仍保留；无权限或引用过期不能复用。
- [ ] 添加“本周库存”与“最新库存”的非持仓回归；添加公开search→read正文契约回归，防止复用提示破坏另一域。
- [ ] 验证六次模型总额、修正一次、工具串行、取消后停止、finish一次、grant撤销未回退。不要发起真实调用验证。
- [ ] 在最终代码上完整运行一次：

```sh
env -u DATABASE_URL .runtime/agent-v2/bin/python -m pytest -q tests/agent_v2
node --test tests/agent_v2_frontend.test.mjs tests/agent_quality_migration.test.mjs tests/closing_review_agent_frontend.test.mjs
.runtime/agent-v2/bin/python -m pip check
git diff --check
```

- [ ] 共享业务组件变更若引出其他直接影响的失败，定位并记录；不能删除旧测试、改成无意义断言或把新失败写成“环境问题”而无证据。
- [ ] 回查诊断G1—G6与本计划P1—P6逐项覆盖，每项写失败复现、修复函数、测试断言与剩余限制。
- [ ] 明确提交本包测试/交接文件，说明 `test(agent): cover production failure path end to end`；保持工作树干净或准确列出他人未提交文件。

## 9. 给主 Agent 的最终回执

必须返回：

1. 实际工作树/分支/起止SHA，每个提交的责任范围。
2. P1—P6对照表，包括红灯原因、绿灯结果和最终内容断言。
3. 自动化命令与新鲜passed/failed/skipped、警告；合成数据必须标合成。
4. 是否改共享legacy路径及其回归；是否改任何契约字段、依赖/数据库/业务语义（默认后三者应无）。
5. 原题与“只看Put”最终合成正文、匹配范围、数据源时点状态及引用检查；不给真实持仓快照。
6. 缺陷仍未解决、证据不充分或需决策处；没有真实部署/调用必须明确写未做。
7. 停在本地提交，不自动继续正式验收。

主 Agent 接回后才负责：复核代码→核算已授权预算余额→确认实际部署及旧worker退出/运行时选择→启用管理员小范围试用→新对话原题及追问→更多业务场景。原始137草稿未持久化，不能要求执行者伪造其原文来“重放成功”。

## 10. 用户可直接转发的执行指令

> 请按 `docs/superpowers/plans/2026-09-15-agent-production-failure-repair.md` 执行四个本地修复包，同时读取其链接的 A/B/C 诊断报告。先核对实际工作树及基线，按每包的失败复现、最小修复、定向回归、独立提交顺序执行。重点修正当前快照/期间语义、用户只要数量的限制、完整引用规则、底层错误进入一次修正反馈、未知源时点和可核验数量的部分交付，并完成中文原题及只看Put的SDK合成端到端验收。不要创建子Agent、不要push/部署/改正式开关，不调用真实模型或搜索，不读取凭据、不改交易公式/事实/权限/依赖/DB。遇到超出本计划的业务或架构决定先返回具体证据。完成后按第9节提交回执并停止，由主Agent负责真实业务验收。
