# 智能贸易助手正式验收后完整修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按正式版 #25–#30 的真实业务证据修复智能贸易助手的权限、公开研究、组合问答、跨年度查询、持仓工具错误、Evaluation 和质量后台，并用正式版管理员真实路径完成业务验收。

**Architecture:** 保留现有 Harness/MCP/确定性事实服务边界，在服务端增加统一的子问题结果契约和 fail-closed 权限策略；数据查询通过受控批次/游标和登记口径提供多年结果，Evaluation 与运行质量使用独立、可追溯的批次和逐例记录。模型只能编排已授权能力，不能授予权限、生成后台或未接入模块数据。

**Tech Stack:** FastAPI/Pydantic、现有只读数据库适配器、Python pytest、Node test、浏览器正式版管理员验收、现有 MCP/Harness/质量后台。

**Spec:** `docs/superpowers/specs/2026-09-10-agent-remediation-after-production-acceptance.md`

## Global Constraints

- 只接入交易管理、数据可视化；贸易台账、信息预警、订单融资保持未接入；后台管理对业务 Agent 永久禁止。
- 所有业务数据和交易事实只读；不创建、修改、取消或确认任何真实交易。
- 公开研究缺少真实搜索配置时必须明确记录为配置受阻，不能模拟为已联网成功。
- 权限判定在模型目录、MCP 入口、工具 dispatch、结果复用和字段投影处均 fail-closed。
- 用户可见时间统一到秒；质量后台使用 Asia/Shanghai 日界线。
- 先本地测试和发布差异核对，再选择性部署 Production；Production 只使用现有管理员会话验收。

---

### Task 1: 建立失败回归与权限/外发门禁

**Files:**
- Modify: `backend/app/trading_agent/dv_queries.py`
- Modify: `backend/app/trading_agent/tools.py`
- Modify: `backend/app/trading_agent/research_policy.py`
- Modify: `backend/app/trading_agent/store.py`
- Test: `tests/agent_v2/test_dataset_permissions.py`
- Test: `tests/agent_v2/test_tools.py`
- Test: `tests/agent_v2/test_research_policy.py`
- Test: `tests/agent_v2/test_store.py`

**Interfaces:**
- `query_dataset` 和 `read_result_page` 输出字段必须是当前 Principal 可读字段的子集。
- `_public_gate` 缺少当前任务、用户、有效计划或计划读取异常时必须拒绝。
- 缺少 `required_resources` 的旧快照只允许严格安全白名单复用，否则返回需要重新查询的状态。

- [ ] **Step 1: Write the failing tests.**

  覆盖 `fields=[]` 不返回 `source_file/source_row/package_id`；显式敏感字段在无 `data_visualization.data` 时拒绝；有权限时仍可读取；`active_plan=None`、计划读取异常、其他任务计划均拒绝公开工具；旧快照缺权限元数据不能按 kind 推断为 display-only。

- [ ] **Step 2: Run only the new tests and verify the expected failures.**

  Run: `.venv/bin/python -m pytest tests/agent_v2/test_dataset_permissions.py tests/agent_v2/test_tools.py tests/agent_v2/test_research_policy.py tests/agent_v2/test_store.py -q`

  Expected: 新增的字段、缺计划和旧快照反例在当前基线失败，失败原因指向默认字段投影或 fail-open 分支。

- [ ] **Step 3: Implement the minimum permission changes.**

  先计算 safe default fields，再投影行；显式请求敏感字段先鉴权再查询；保存快照时保存规范化后的资源集合。把公开工具执行校验移动到 dispatch 之前且缺计划/读取异常直接拒绝；复用历史快照时缺少资源元数据直接要求重查。

- [ ] **Step 4: Run the targeted tests and the existing permission suite.**

  Expected: 新增反例和现有合法访问均通过，任何业务 Agent 入口都不会绕过同一策略。

- [ ] **Step 5: Commit the isolated boundary change.**

  Commit message: `fix(agent): close field and public research authorization gaps`

---

### Task 2: 统一子问题计划、确定性拒答和部分结果交付

**Files:**
- Modify: `backend/app/trading_agent/research_policy.py`
- Modify: `backend/app/trading_agent/harness.py`
- Modify: `backend/app/trading_agent/prompts.py`
- Modify: `backend/app/trading_agent/answer_contracts.py`
- Modify: `backend/app/trading_agent/answer_v21.py`
- Modify: `backend/app/trading_agent/store.py`
- Modify: `frontend/closing_review_agent.js`
- Test: `tests/agent_v2/test_research_policy.py`
- Test: `tests/agent_v2/test_harness.py`
- Test: `tests/agent_v2/test_answer_delivery_recovery.py`
- Test: `tests/agent_v2/test_prompts.py`
- Test: `tests/closing_review_agent_frontend.test.mjs`

**Interfaces:**
- 每个子问题返回 `completed/partial/not_connected/forbidden/unavailable/clarification_required/budget_blocked` 之一、原因码、完成字段和证据引用。
- “订单融资未接入”和“后台管理禁止”由服务端策略生成确定性说明，不进入模型引用校验。
- 当前问题只继承用户明确指向且重新授权的历史结果；独立问题不能带出旧拒答。

- [ ] **Step 1: Add failing tests for negative wording and policy scope.**

  必须证明“不要联网，只查询库存”“不需要实时估值或联网”“无需网络查询”均为 `internal_only`/`forbidden`；“请查询近期外部供需信息”才为 `research_allowed`；只有同一子句真正同时要求和禁止外部研究时才澄清。

- [ ] **Step 2: Add failing tests for deterministic module refusals and mixed coverage.**

  证明订单融资返回“当前尚未接入，无法查询内部融资数据”，后台即使管理员也返回“后台管理信息不通过业务Agent提供”；混合请求保留交易/可视化已完成段，不将拒答标记为“数据完整”。

- [ ] **Step 3: Add failing tests for budget/tool-error recovery and history boundaries.**

  让一个子问题完成、另一个子问题在预算/工具错误中止，断言已完成结果仍被交付；新问题不能重复旧融资/后台段落；“把刚才表格改为图”可以复用重新授权的 result_ref。

- [ ] **Step 4: Implement the structured subquestion envelope and policy normalization.**

  将研究需求、模块、时间段、输出字段和历史引用限制在服务端计划中；把确定性拒答和可恢复结果接入同一答案协议；预算出口使用带 `known_result_refs` 的恢复路径。

- [ ] **Step 5: Implement the frontend conversation sequence guard.**

  为新建对话、切换对话和异步任务增加请求序号/会话身份检查，旧任务响应不能写入新会话；保留当前 loading 守卫和失败状态。

- [ ] **Step 6: Run the focused Python and Node tests.**

  Expected: 原 Agent V2 答案协议测试和新增拒答/局部交付/多轮边界测试全部通过。

---

### Task 3: 修复持仓工具错误并实现免行情数量查询

**Files:**
- Modify: `backend/app/trading_agent/mcp_server.py`
- Modify: `backend/app/trading_agent/mcp_client.py`
- Modify: `backend/app/trading_agent/facts.py`
- Modify: `backend/app/trading_agent/tools.py`
- Test: `tests/agent_v2/test_mcp.py`
- Test: `tests/agent_v2/test_facts.py`
- Test: `tests/agent_v2/test_tools.py`

**Interfaces:**
- `query_positions` 增加兼容的 `valuation_mode`/`required_metrics`，数量查询不调用行情；估值查询仍要求最新成交价。
- MCP 错误保留 `invalid_argument/forbidden/database_timeout/source_unavailable/market_unavailable/snapshot_failed/budget_exhausted` 分类。
- 数量查询数据库失败时不能用旧快照冒充最新，行情失败时已取得的数量仍可按 partial 交付。

- [ ] **Step 1: Reproduce #30 at the smallest boundary and add failing tests.**

  用只请求持仓手数的 query 断言 quote provider 调用次数为 0；用结构化 MCP error 断言客户端保留分类和安全诊断编号，而不是统一抛出 `RuntimeError`。

- [ ] **Step 2: Implement error normalization and explicit valuation mode.**

  先分类工具异常，再按 `required_metrics` 决定行情阶段；把数据库事实、行情和快照保存分别记入事件的 phase/status/error_code。

- [ ] **Step 3: Run the targeted tests and original MCP/facts suites.**

  Expected: #30 的数量路径可以得到真实事实或明确服务不可用，绝不因不需要估值而进入行情调用；所有错误页面不显示异常原文或凭据。

---

### Task 4: 多年查询、按年口径与连续批次

**Files:**
- Modify: `backend/app/trading_agent/dv_contracts.py`
- Modify: `backend/app/trading_agent/dv_queries.py`
- Modify: `backend/app/trading_agent/dv_analysis.py`
- Modify: `backend/app/trading_agent/semantic_catalog.py`
- Modify: `backend/app/trading_agent/presentation.py`
- Modify: `backend/app/trading_agent/mcp_server.py`
- Test: `tests/agent_v2/test_dataset_queries.py`
- Test: `tests/agent_v2/test_dataset_analysis.py`
- Test: `tests/agent_v2/test_dataset_catalog.py`
- Test: `tests/agent_v2/test_dataset_presentation.py`

**Interfaces:**
- 保留单批行数/字节上限；新增绑定筛选和来源版本的服务端 `query_group`/`next_cursor`，按日期和稳定唯一键续查。
- `port_inventory` 支持登记的 `business_year` 派生分组以及期末/观察点均值等明确库存口径；禁止跨日期直接求和。
- 图表和表格读取同一完整结果快照，显示请求期间、真实覆盖期、缺失年份和单位。

- [ ] **Step 1: Add failing synthetic multi-year tests.**

  覆盖 2022–2026 五年、同日超过单批上限、缺失年份、重复/遗漏游标、库存年度期末和观察点均值；断言跨日期库存 sum 仍被拒绝。

- [ ] **Step 2: Implement signed query-group continuation.**

  query group 绑定用户/会话/查询过滤器/资源/来源版本，游标服务端签名并在继续读取时重新授权；来源变化或伪造游标直接停止并要求重新开始。

- [ ] **Step 3: Implement registered annual dimensions and aggregations.**

  只允许语义目录登记的方法；不完整年度标记为 incomplete，不把库存各日相加，也不把不存在的年份补成 0。

- [ ] **Step 4: Align table/chart presentation and capacity fallback.**

  同一结果引用生成表格和图表；超点数时分页/分面或降级完整表格，明确说明，不静默抽样。

- [ ] **Step 5: Run targeted data tests and full Agent V2 regression.**

  Expected: 367 天不因旧业务限制失败；真实数据覆盖不足时如实报告，而不是无限换年份重试。

---

### Task 5: 公开研究 readiness、真实来源链路和安全失败

**Files:**
- Modify: `backend/app/trading_agent/public_transport.py`
- Modify: `backend/app/trading_agent/research.py`
- Modify: `backend/app/trading_agent/research_policy.py`
- Modify: `backend/app/trading_agent/tools.py`
- Modify: `backend/app/trading_agent/quality.py`
- Test: `tests/agent_v2/test_public_transport.py`
- Test: `tests/agent_v2/test_research.py`
- Test: `tests/agent_v2/test_research_policy.py`

**Interfaces:**
- readiness 只返回 `not_configured/auth_failed/unreachable/available` 等非敏感状态；配置缺失不能伪装成意图未识别。
- 公开结论必须绑定搜索结果和至少一个已读取正文，记录 URL、发布日期或 unknown；搜索 0 次不能标记联网成功。

- [ ] **Step 1: Add failing transport/readiness and citation tests.**
- [ ] **Step 2: Implement provider readiness and structured public evidence.**
- [ ] **Step 3: Run local provider contract tests.**
- [ ] **Step 4: If Production provider remains unconfigured, record it as an explicit acceptance blocker; do not fabricate the missing network result.**

---

### Task 6: Evaluation 批次、逐例评分和质量后台真实口径

**Files:**
- Create: `backend/migrations/agent_quality_evaluation.sql` or project-equivalent migration
- Modify: `backend/app/trading_agent/quality.py`
- Modify: `backend/app/trading_agent/quality_routes.py`
- Modify: `scripts/run_agent_v2_evals.py`
- Modify: `frontend/agent_quality.js`
- Modify: `frontend/agent_quality.css`
- Modify: `tests/agent_v2/test_eval_runner.py`
- Modify: `tests/agent_v2/test_quality.py`
- Modify: `tests/agent_v2/test_quality_routes.py`
- Modify: `tests/agent_quality_frontend.test.mjs`

**Interfaces:**
- Evaluation 批次、逐例结果和人工/规则复核分开存储；执行来源区分 deterministic/offline/live/human，未执行保持 `not_run`。
- live runner 必须走当前 Harness/MCP 链路，带显式 case_id、environment、budget 和幂等键；不提供任意 HTTP 一键调用。
- 质量后台统一筛选、分页、聚合；默认 Asia/Shanghai 近 7 天，排除 acceptance/eval 流量，旧记录标 unknown；四个视图都显示真实“未记录”而非固定通过。

- [ ] **Step 1: Add failing migration/runner/backend/frontend tests.**

  断言同一批次可恢复、逐例可回读、预算耗尽为 blocked、人工复核主体类型可区分；空过滤集反馈为 0；北京时间日界线和分页总数一致；live 未执行不能显示 passed。

- [ ] **Step 2: Add the minimal isolated evaluation tables and migration checks.**

  迁移只增加 Evaluation 附表，不改业务事实；先在本地临时库执行、回读 schema、验证回滚/恢复路径，再进入正式发布。

- [ ] **Step 3: Implement runner, deterministic scorer, and quality queries.**

  规则覆盖权限、禁止联网、来源、单位、时间、完整性、引用、预算和子问题覆盖；解释正确性不确定时标 `needs_review`。

- [ ] **Step 4: Implement the four quality views.**

  运行概况、质量与反馈、调用明细、版本验收使用同一个安全筛选对象；详情默认脱敏，证据正文仍遵守独立审阅权限。

- [ ] **Step 5: Run backend/frontend quality tests and a local live-run dry boundary test.**

  Expected: Evaluation 页面反映真实批次和逐例结果，不再固定显示“未记录”或把真实业务验收自动算成人工通过。

---

### Task 7: 全量回归、正式发布和功能性验收

**Files:**
- Modify: `版本更新记录.md`
- Modify: `docs/superpowers/specs/2026-09-10-agent-remediation-after-production-acceptance.md` only for final status/evidence links if needed
- Test/record: `docs/superpowers/analysis/` acceptance evidence files as needed

- [ ] **Step 1: Run local full verification.**

  Run Agent V2 Python suite, full Python suite, Agent/frontend Node suites, syntax/diff checks, and migration verification. Existing unrelated failures必须保留并单独定位，不能用测试修改掩盖。

- [ ] **Step 2: Review release diff and latest Production rollback identity.**

  确认只包含 Agent 修复、质量后台、必要迁移和记录；不包含交易写入、订单融资接入、后台数据暴露或凭据。

- [ ] **Step 3: Deploy the candidate to the authorized Production main/Render target.**

  发布后核对实际版本、健康状态、静态资源版本和数据库迁移结果；更新版本记录只写完成后的真实证据。

- [ ] **Step 4: Execute at least the 12 new Production functional scenarios.**

  包含 367 天、五年覆盖/缺失、年度图表表格、独立公开研究、内部+外部、禁止联网同义句、多轮边界、订单融资拒答、后台拒答、混合部分完成、表改图追问、新问题不带旧拒答；另检查质量后台四视图、反馈回读、Evaluation 批次和真实数值与同源页面对账。

- [ ] **Step 5: Publish an evidence-based completion report.**

  按 R01–R14 和原需求逐项列出已满足、受阻和遗留项；只有 P0 关闭、必须功能实际验证、Evaluation 可执行且后台口径真实时，才声明整体完成，否则明确列为“已发布但未整体验收通过”。

