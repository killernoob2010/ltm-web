# 智能贸易助手真实联网与混合分析修复：Luna Max 技术执行任务书

## 0. 执行合同

本文是对既有 `2026-09-14-agent-luna-execution-taskbook.md` 的增量修复任务书，落实用户已认可的完整修复方向。本文只形成执行方案；生成本文不等于已经派发执行、修复或发布。

- 执行模型：用户明确派发后，隔离的 `gpt-5.6-luna`，reasoning=`max`；不创建嵌套 Agent。
- 主 Agent：负责业务口径、架构分歧、未知根因、代码审阅、生产发布及最终真实验收。已有项目 Agent 发布授权继续有效，不重复请求同范围授权；生产操作不委托给 Luna。
- 授权结果：自然提问能按信息需求使用内外部来源；公开研究取得可引用正文；内部字段有准确覆盖说明；状态如实反映问题完成程度。
- Luna 工作环境：包含下述基线的隔离非生产工作树。不得直接操作生产环境变量、数据库、部署或生产管理员会话。
- 不增加依赖、表、迁移、搜索服务商或费用上限。确有必要时，提交具体差异和原因，由主 Agent 决定。
- 非目标：对话栏排序、订单融资修复、交易事实变更、任意 SQL、交易执行、界面重设计、全平台重构。
- 所有展示与 API 时间到秒；未知信息用 null/unknown，不能用零表示未记录。

## 1. 基线、证据与开始条件

编写时工作树：`/Users/wangjingze/.codex/worktrees/debb/轻量化交易管理系统WEB`。
分支：`codex/agent-answer-research-conversation-repair-20260914`。
实现基线：`4873f55 feat: add controlled mixed-source agent planning`。
上一方案：`6f814c2`；旧代码对照：`8afdbb8`。

开始时执行 `git status -sb`、`git rev-parse HEAD`、`git log -5 --oneline`，确认基线是否为祖先。遇到额外用户改动保留，不能 reset。HEAD 已前进时报告差异并在当前实现上补改，不能覆盖成旧版本。

主 Agent 应向 Luna 提供精简证据包，不传密码、Token、数据库地址或完整历史：

| 证据 | 已知结论 | 不得推导 |
|---|---|---|
| 线上 #117，问题“结合近期的天气，分析对矿石发运的影响” | `internal_lookup`；工具目录无 search_public/read_public；搜索计数 0 | 新分支也一定存在相同错误 |
| 线上 #118，显式要求联网查询澳大利亚天气事件 | 两次 search_public，零次 read_public；结果 partial；公开正文引用校验失败 | 服务商已成功返回、服务商一定故障、密钥一定缺失 |
| 旧策略规则复现 | 原句有逗号不命中外部规则；去掉逗号命中 | 通过增加“天气”一个词就完成修复 |
| 当前本地 Provider 检查 | 当时进程未配置 Tavily/Brave | 生产环境同样未配置 |

旧线上部署 SHA 尚未在上述证据中核实。正式结论必须将现场运行、旧代码复现、新实现测试分别归属，不能混称。

### 1.1 必读文件和增量核对

按项目规则阅读 AGENTS、README、开发流程、交接摘要；随后阅读：

- `docs/superpowers/plans/2026-09-14-agent-capability-planning-complete-design.md`
- `docs/superpowers/plans/2026-09-14-agent-luna-execution-taskbook.md`
- 本文相关代码与测试。

先列出“已实现/需修复/缺验证”清单。现有 TaskPlan、Requirement、CoverageReport、planner、coverage 都已存在，不重新搭建第二套规划系统。

## 2. 文件边界与责任

以下路径均相对仓库根目录。

| 文件 | 可修改内容 |
|---|---|
| `backend/app/trading_agent/planning_contracts.py` | 必要的可选时间范围字段；保持历史结构兼容 |
| `backend/app/trading_agent/planner.py` | 来源需求拆分、默认范围、计划校验；不得增加身份或权限决策 |
| `backend/app/trading_agent/research_policy.py` | TaskPlan 到服务器许可的投影、明确禁止联网规则 |
| `backend/app/trading_agent/capability_catalog.py`、`catalog.py`、`semantic_catalog.py` | 已有字段别名、可用性、范围和口径；不得虚构不存在的数据源 |
| `backend/app/trading_agent/research.py` | 搜索/正文标准结果、错误分类、脱敏诊断 |
| `backend/app/trading_agent/public_transport.py` | 仅在证据证实读取故障时定向修复；保留 SSRF/DNS/重定向限制 |
| `backend/app/trading_agent/harness.py` | 有界执行、搜索后取证、结果归属、最终覆盖检查 |
| `backend/app/trading_agent/prompts.py` | 结果状态投影与补证指令 |
| `backend/app/trading_agent/coverage.py`、`answer_v21.py` | 正文与需求匹配、最终答案覆盖、降级结果 |
| `backend/app/trading_agent/store.py`、`quality.py`、`quality_routes.py` | 复用现有事件/结果表、受限诊断输出 |
| `backend/app/trading_agent/worker.py` | 仅核对实际入口启用规划；若需修复仅定向接线 |
| `frontend/agent_quality.js`、`frontend/closing_review_agent.js` | 工具状态、失败原因与完成程度展示，必要时相关 CSS |
| `tests/agent_v2/`、相关 frontend tests、既有 Eval 定义 | 本文用例及兼容回归 |

超出清单且涉及业务服务、认证、基础设施或共享数据库结构的改动交回主 Agent。不得以修测试为由改订单融资等无关模块。

## 3. 技术决策：沿用合同，不创建平行状态系统

### 3.1 需求与权限

复用 `TaskPlan.requirements`，每项保留 `source_intent`、`targets`、`depends_on`、`needs_full_text`、`time_requirement`。

- 先判断问题需要的事实，再判断来源当前能否执行。外部服务未配置不能把 public 需求删除或改写成 knowledge。
- 自然语言外部需求由 planner 理解，服务器校验后经 `policy_from_task_plan` 批准。旧 `enforce_research_policy` 只保留兼容路径，不得二次覆盖新计划来源。
- `_NO_WEB`、当前身份权限、出网隐私检查和预算为服务器约束。模型不能通过计划绕过。
- 公开问题不强制查内部库；用户明确结合内部到港/库存时才要求对应内部数据。语义不明确但可合理继续时写清假设，避免无意义澄清。
- “发运”不是“到港”。可以说明到港侧观察，但必须保留两个目标的不同口径。

### 3.2 时间范围（本任务的固定默认）

用户未指定“近期”的范围时，使用任务启动时业务时区 Asia/Shanghai 的最近 14 个自然日，含当天，记录为 default 条件并在答案说明。明确日期覆盖默认值。

如现有字符串 time_requirement 不足以可靠比较，可仅新增可选 `time_window={start_date,end_date,timezone,origin}`，保持旧计划缺字段可读。范围由服务端核验，禁止模型将执行日期改成其他日期。

发布时间与事件发生日期分别处理。无发布时间不直接删除来源；若正文也无法确认事件处于请求范围，则不能作为“本期事件”完成证据。搜索 time_range 只是粗筛，不是日期核验。

### 3.3 工具返回合同

继续使用 ToolEnvelope 的现有 status，不新增公共顶层枚举。以下为 research payload 的增量约定，旧字段兼容：

```text
kind: research | public_read
provider: tavily | brave                 # 读取阶段可沿用来源 provider
code: string | null                     # 规范失败码
provider_status: available | unavailable | unknown
http_status: integer | null             # 不记录响应头/原始错误正文
search_status: results | no_results | failed  # research 使用
source_count: integer                   # research 使用
sources: [既有 title,url,description,published_at,published_label,
          fetch_status,source_ref]      # 保留既有引用格式
```

`provider_status=available` 仅表示本次服务响应可处理；凭据存在只叫 configured/readiness，不是本次调用成功。

| 条件 | ToolEnvelope.status | code/状态 |
|---|---|---|
| 未配置 | temporarily_unavailable | public_not_configured |
| 401/403 | temporarily_unavailable | public_auth_failed |
| 已支持的额度耗尽返回 | temporarily_unavailable | public_quota_exhausted |
| 429 | temporarily_unavailable | public_rate_limited |
| 请求超时 | temporarily_unavailable | public_timeout |
| 5xx/其他 HTTP 错误 | temporarily_unavailable | public_http_error，保留 http_status |
| JSON/结构无效 | temporarily_unavailable | public_invalid_response |
| 合法响应但没有候选来源 | partial | search_status=no_results |
| 有候选结果但均无法登记 | partial | public_no_usable_sources；与服务原生空结果区分 |
| 有可登记来源 | complete | search_status=results；仅代表搜索阶段完成 |
| 正文不可读/不支持/安全拒绝 | 保持现有适当状态 | public_read_failed/public_content_unsupported/public_source_unsafe |

不把 429 自动解释成额度耗尽；服务商错误识别仅依据已知响应规则，不输出服务原始敏感内容。

正文沿用 `read_public` 现有 text、excerpt、引用与 fetch_status 结构。不要把搜索摘要改标 full_text，也不要重建与 answer_v21 不兼容的引用格式。

## 4. 分批实施

### P0：取得真实失败分类，主 Agent 负责现场证据

1. `quality.get_run_detail` 已选择 events.status、error_code、duration_seconds，先读取这几个字段。当前 frontend 只显示部分字段，不能说数据库没有记录。
2. 对 #118 的两次搜索，核对 event.status、关联 result_ref 是否存在、结果 source_count/search_status、是否记录异常。缺失历史信息标 unknown，不能从调用次数反推。
3. 原始故障已被吞掉且无法恢复时，在既有预算内进行新真实调用，保留脱敏返回分类；新调用结论不得当成旧调用根因。
4. Luna 可完成下面独立部分；凡需要根据未确认 Provider 根因作出传输层/协议变更，暂停该子项交回主 Agent。

交付：一张含 run/task、版本、调用状态、返回数量、正文读取状态、证据可信度的简表。不得另做无关仪表盘。

### P1：规划与字段覆盖补齐

1. 核对 `worker.py -> RuntimeDeps.planning_enabled -> create_plan -> validate_plan -> policy_from_task_plan` 实际调用链；测试必须覆盖真实 worker 装配，不能只测手工开启的 Harness。
2. 对天气/停航/港口公告等近期公开事实，规划 public requirement，needs_full_text=true，保留请求日期与范围。
3. 如需内部比较，拆为独立内部 requirement。综合分析依赖相关事实；依赖失败时仍可输出有支持的部分分析，不伪造完整关联。
4. 验证计划修订不会撤销 no_web，历史会话限制不会因换说法丢失。
5. 查既有目录和字段别名；只添加确有来源的别名。无权/不支持/记录为空/别名未识别分别返回已有错误或覆盖码。
6. 字段覆盖以整个请求结果统计为准，不以分页 preview 判断。若现有工具没有全量覆盖信息，报告该缺口，由主 Agent 确认最小查询扩展；不要让 Harness 直接查业务表。

验收：标点和同义句不改变来源需求；内部查询无多余公开调用；不以不存在字段/数据源伪装覆盖。

### P2：搜索、正文结果与审计

1. 按 3.3 实现搜索结果分类；保留 Tavily/Brave 两个现有适配器和隐私检查。
2. 将失败 envelope 也保存到现有受控结果表，或使用等效既有持久化入口，生成 result_ref；不能只有临时 warning 导致事后无法诊断。
3. Harness 的工具事件保留 envelope.status、result_ref；error_code 写白名单 payload.code。不得把 exception 文本、整条问题或响应体塞进 error_code/tool_name。
4. `duration_seconds` 在 API/展示边界截断到整秒。失败写结果本身失败时，记录已有安全错误，禁止虚构审计成功。
5. 来源被过滤需记录候选数、登记数和有限原因计数；不记录内部 IP 或不安全 URL 中的秘密。
6. 不增加隐式 HTTP 重试：重试全部经 Harness 可计数工具调用，继承同一 grant、deadline 和预算。

验收：所有失败类别有稳定码；空结果不同于解析异常；没有密钥泄露；正文引用仍能通过现有读取校验。

### P3：有界取证和恢复流程

预算保持现有 RuntimeLimits：默认 max_search=2、max_tools=8、max_models=6；部署覆盖值更低时按更低值。所有规划、补证和重试都计入同一预算，不新增外层无限循环。

```text
public requirement pending
  -> 已批准且可用：search_public
  -> results：选择相关的已登记 source_ref -> read_public
  -> no_results：有剩余搜索预算时允许一次改写检索
  -> timeout/5xx：有剩余预算时最多一次重试
  -> rate_limited：仅当等待不超 deadline 且已有重试策略允许，否则停止
  -> auth/quota/not_configured：停止同 Provider 的本任务重试
read_public
  -> 正文可用：绑定对应 requirement，检查时间/地域/主题，继续答案
  -> 来源不可读：有工具预算则尝试另一个已登记来源
  -> 无证据或预算不足：报告缺口，继续可完成需求
final candidate
  -> 公开近期需求未取证：预算内补证一次，或明确 partial/failed
  -> 最终校验 -> assess_delivery -> 保存最终状态
```

实现约束：

- 复用 harness 当前 public_query_repair、预算循环和 coverage 检查，合并为一个恢复决策入口，不能在两处各自重试一次。
- 正文补证只能使用当前任务 search_public 已登记的 source_ref；不能由模型拼 URL 绕过注册和安全读取。
- 工具批次不可超过剩余预算。首批搜索有可用来源时优先读取，避免先用尽搜索次数却没取证。
- 首选最多两篇相关正文，仍受整体预算约束；不是固定“凑够两篇才完成”。事实完整、来源权威时一篇也可支持对应具体结论。
- 模型过早输出 final 时，若有已登记来源且缺正文，返回明确补证指令；补证失败必须结束为真实缺口，不能继续循环。
- 公共结果须关联到 requirement ID，可在 Harness 内维护归属映射并进入既有 coverage payload；不要只凭 kind=public_read 将任意网页判为所有公开需求已完成。

验收：模拟模型 search 后立即 final 的轨迹，Harness 能推进读取或正确降级；无重复计费循环；失败的公开子任务不抹掉有效内部结果。

### P4：证据与最终完成程度

复用 `coverage.assess_evidence`、`assess_delivery` 和 `answer_v21.validate_answer21`。

- 当前 `_has_full_text` 检查有文本还不够：需核对当前任务、需求归属、有效引用、来源相关性和时间覆盖。
- 日期、引用所有权、状态由服务端检查；语义相关性由模型给出可审阅摘录支撑并纳入真实 Eval，不宣称确定性代码能证明所有自然语言因果。
- 无正文不能以 knowledge 段完成 public requirement；有一般知识可交付，但公开需求仍缺失。
- 区分“已发生港口停航”与“风浪可能影响装船”。后者作为分析，不能冒充事实；实际损失数值必须有证据或确定性计算。
- 证据校验删减后重新评估 delivery_blocks，不使用删减前覆盖报告。
- CoverageReport.complete 仅在非空报告、所有计划 requirement 恰好出现一次且全部 answered 时为 true。补测空 items、漏项、重复项、未知 requirement ID。
- 两个公开 requirement 不能因为一篇无关正文而同时 answered；一个内部目标也不能借另一个目标结果完成。
- 原始校验失败后修复成功可判完成；历史事件存在 missing_reference 不应永久判失败，最终以最终有效答案及覆盖为准。

状态映射：保留数据库运行状态枚举，业务完成程度从最终 CoverageReport 和现有交付字段计算。全部必答项完成为已完成；存在有效交付且有缺口为部分完成；仅缺必要条件为需要补充信息；关键取证失败且没有有效交付为查询失败。不能把“没有数据”三个字自动当失败：若用户只问字段是否存在，目录回答本身可以完成。

### P5：可观测性与前端

1. `quality.get_run_detail` 输出白名单诊断摘要；按 task_id/result_ref 做有限查询，保留管理员权限验证。不要向普通用户返回其他任务来源或内部参数。
2. 前端首先展示已有 event.status/error_code/duration_seconds，解决“有记录却看不见”。
3. 复用结果表派生 `research_summary`：

```text
search_attempts             # 旧 search_calls 原含义保留，文案明确为尝试
search_responses_ok         # 合法成功响应，含零结果
searches_with_sources
registered_source_count
read_attempts
read_successes              # 正文非空且 fetch_status 合法
cited_source_count          # 最终有效答案中去重的来源
failure_codes
diagnostics_complete        # 历史数据缺失时 false，相应统计为 null
```

4. 不修改旧 search_calls 的含义来造成历史统计混乱；不得将 response_ok 等同业务完成。
5. 业务答案显示已完成/部分完成/需要补充信息/查询失败以及简短原因；代码、Provider 状态放可展开诊断，不塞入主要业务答案。
6. 不迁移表，不调整历史问答内容，不把已有失败标成人工通过。

## 5. 测试矩阵（每项必须有断言）

| ID | 输入/模拟条件 | 必须验证 |
|---|---|---|
| R01 | 原句天气＋发运 | public needs_full_text；允许搜索 |
| R02 | 去逗号、换标点、“最近天气会影响矿石装船吗” | 语义一致；禁止以关键词命中测试冒充真实规划验收 |
| R03 | “请联网查澳大利亚近期天气事件” | public only，不强制内部查询 |
| R04 | “结合系统本周到港和近期天气分析” | 分离内部/公开需求，最后交付同时覆盖或明确缺口 |
| R05 | “不要联网，按内部数据分析” | search/read 调用为零，即使模型提议也拒绝 |
| R06 | 服务未配置/401/403/额度不足 | 失败码准确；停止重复搜索；需求不被删除 |
| R07 | 429/5xx/timeout | 有界恢复；耗尽后状态准确；计数不突破预算 |
| R08 | 合法空结果/无效 JSON/无效 results 类型 | 三者分开；不能都当 no_results |
| R09 | 候选 URL 均不安全 | 无成功来源；不执行内网读取；提供安全原因 |
| R10 | 搜索返回来源，模型直接 final | 触发正文补证或正确降级 |
| R11 | 第一篇不可读，第二篇成功 | 只读已注册来源；成功证据可用；保留失败诊断 |
| R12 | 正文过期/日期未知/无关地域 | 不自动完成“近期指定地区事件” |
| R13 | 两个公开需求，只有一项有正文 | 另一项未完成；不得共享 kind 导致假覆盖 |
| R14 | 内部成功＋公开失败 | 保留内部答案，明确公开失败，整体 partial |
| R15 | 初稿有引用，最终校验删去主要结论 | 重新计算覆盖，不能标 complete |
| R16 | 初稿引用错误，修复后有效 | 最终可完成，不被旧失败事件永久阻止 |
| R17 | 字段别名/部分空值/无权限/不存在 | 口径与缺失类别准确；不绕过授权 |
| R18 | 搜索词包含私有客户/账户/内部编号 | 出网前拒绝；日志不暴露私有值 |
| R19 | 多轮 no_web、换话题、旧结果过期 | 限制和证据归属正确，不借历史授权出网 |
| R20 | 总预算不足、模型超时、正文超时 | 无无限循环；完成状态与原因准确 |
| R21 | 历史缺诊断、正文已读未引用 | unknown/null 与 0 区分；引用数不是读取数 |
| R22 | 普通内部库存/持仓/表格查询 | 原有结果不退化，不新增外部调用 |

单元/模拟测试验证契约与执行分支。真实模型用原句、同义句和未出现在提示示例中的问题验证规划；不得用固定假计划声称自然语言识别通过。

参考命令（开始时核对解释器路径，缺失使用项目既有环境）：

```sh
/tmp/agent-v2-python/bin/python -m pytest -q tests/agent_v2
/tmp/agent-v2-python/bin/python scripts/run_agent_v2_evals.py --mode offline-behavior
node --check frontend/agent_quality.js
node --check frontend/closing_review_agent.js
node --test tests/agent_quality_frontend.test.mjs tests/agent_answer_renderer.test.mjs tests/closing_review_agent_frontend.test.mjs
git diff --check
```

测试先做对应模块，再运行上述回归。前次报告的 401/16/17 通过不是当前验证结果，不能复制；前次全量订单融资两项失败需核对是否仍无关，不能顺手修复。

## 6. 真实调用与发布验收（主 Agent 执行）

Luna 提交后，主 Agent 先审阅和核对测试，再按既有授权选择性发布 Agent 差异。优先复用既有 Provider 和管理员会话；不因本地没密钥就宣布生产不可用，也不复制生产密钥给 Luna。

必须验证实际部署 commit；健康检查和静态页面只用于版本/服务确认，不代替业务验收。

最小真实验收集：R01、R02 的一条未见改写、R03、R04、R05、R22。每条保留：

```text
部署 SHA、环境、任务编号、问题、任务开始时间（秒）
计划中的各需求、批准来源、脱敏工具状态与失败码
搜索响应数/登记来源数/正文读取数/最终引用数
URL、正文摘录、发布时间或未知、事件日期证据
最终答案、未完成项、业务判定及理由
```

不固定断言“近期一定有台风/停航”。实时检索若没有相关事件，答案需说明检索范围与证据限制，不能将未搜到推断为绝对没有。可另用已知日期历史事件验证正文链路，但不得替代“近期”问题验收。

真实服务故障时记录 blocked/failed，保留独立通过项。模拟故障覆盖 R06—R21，禁止通过破坏生产密钥、耗尽额度或修改真实业务数据制造测试条件。

发布回退以本次前一个已确认部署为目标，遵循项目流程；不删除测试历史或改写交易事实，不由 Luna 执行回退。

## 7. 分批交付与停止条件

建议提交批次：A=P1；B=P2；C=P3+P4；D=P5+回归。P0 主 Agent 可并行取证；需要其结论的传输修复必须等待。每批输出：改动文件、行为变化、已运行测试、未测项、已知风险。

Luna 最终交付包必须包含：

1. commit 列表与相对基线的差异摘要；不混入无关工作。
2. 本文 P0—P5 对照：已完成/主 Agent 待办/证据不足。
3. 测试真实输出摘要；mock/offline/live 明确区分。
4. 待真实验收的问题清单与预期判定，不能自称生产已通过。
5. 搜索失败分类、补证路径、最终状态的关键代码位置。

以下情况停止相关子项并交回主 Agent，同时继续不依赖该决定的工作：

- 需要新增数据源、付费服务、扩大预算、迁移或修改生产配置。
- 不清楚指标业务含义、替代口径或别名存在歧义。
- 需要调整权限、出口白名单、安全读取策略或证据可信标准。
- 真实故障无法从现有证据分类，拟议修复只是猜测。
- 当前分支与基线差异包含其他人的重叠改动，无法安全保留。

主 Agent 最终验收标准：自然问题能触发正确来源并交付受证据支持的回答；字段缺口处理准确；失败原因可追踪；禁联网/隐私/只读限制生效；实际版本与证据一致。仅有调用记录或自动化绿色不能完成验收。

## 8. 可直接复制给 Luna Max 的执行提示

> 在主 Agent 提供的、包含 4873f55 基线的隔离非生产工作树中，执行 docs/superpowers/plans/2026-09-14-agent-live-research-repair-luna-max.md。先核对 HEAD、脏改动和已有实现，按 P1—P5 分批做增量修复。P0 真实现场证据及生产发布、最终验收由主 Agent 提供和执行。复用现有 TaskPlan、CoverageReport、ToolEnvelope、MCP、Harness 和事件/结果表，不新建平行框架。实现自然混合需求规划、搜索失败分类、有界正文补证、按需求验证最终覆盖、真实业务状态和脱敏诊断。保持当前权限、出网检查、只读约束、预算和历史协议兼容。按测试矩阵验证并提交每批证据。不得派发子 Agent、发布生产、调整生产配置、增加付费服务、迁移业务库、修改交易事实或放松引用校验。遇到本文停止条件交回主 Agent，只继续独立可做部分。交付代码差异、测试结果、未测项及主 Agent 的真实验收清单，不能用模拟测试声称真实联网通过。
