# 智能贸易助手：按需联网与现货/期现问答 Implementation Plan

> **For agentic workers:** 使用 `superpowers:executing-plans` 按本文任务逐项执行，使用 `superpowers:test-driven-development` 完成失败优先测试。默认单一执行者；不得因技能说明自动创建子 Agent。用户后续明确委派时，执行模型为 `gpt-5.6-luna`、reasoning effort=`max`。主 Agent 保留业务口径、权限、架构变更和最终验收决定权。

**Goal:** 将“交易持仓助手”升级命名为“智能贸易助手”，保留交易问答，增加按需联网研究以及数据可视化中现货、期现数据的全范围受限只读问答、环比、趋势和已登记关系下的联合分析。

**Architecture:** 延续现有异步任务、MCP 白名单、不可变结果快照和证据校验框架。增加版本化业务语义目录、无业务写入副作用的数据适配器、确定性比较服务及服务端搜索开关；扩展展示协议以覆盖现有页面维度，不开放自由 SQL、任意代码或任意网页抓取。

**Tech Stack:** 现有 FastAPI、Pydantic、SQLite/PostgreSQL 数据访问、Python Decimal、MCP SDK、DeepSeek 适配器、vanilla JavaScript、现有 Markdown 净化与 SVG/table renderer、pytest、Node test runner；本期不新增平台或第三方依赖。

**Spec:** 本文第 1–12 节为完整功能与技术规格，第 13–16 节为开发任务、验收和交接。执行者不必依赖之前聊天；源文档索引仅用于核查依据，不覆盖本文明确的新要求。

**文档日期：**2026-09-10。**状态：**可评审、可分阶段实施的技术方案；文档交付不代表功能已经开发、授权配置完成或发布。本文只编写文件，不触发部署。

## Global Constraints

- 现有代码参考基线：正式修复代码 `c1d8429`，随后仅文档提交 `9a09d72`。参考工作树 `/Users/wangjingze/.codex/worktrees/agent-production-flexible-20260910`。执行开始必须核对最新 main，不得假定该版本永远最新。
- 正式目标：Render `ltm-web` / `https://ltm-web-gt13.onrender.com` / Git main。遵循用户既有“Agent 本地验证后正式部署测试”的规则，不将 Free Staging 作为前置验收；Luna 的执行权限不因此自动包含生产配置、生产数据或付费操作。
- 本轮无业务表迁移、导入、回填、历史重算、业务事实更新或删除。允许正常 Agent 自身会话、任务、快照和审计记录写入。若现有 schema 无法承载本方案，暂停相关任务并报主 Agent，不自行迁移。
- 改名只改用户可见品牌，不修改既有模块编码、权限资源、路由前缀、数据库表名或会话关联键。
- 所有交易账户严格只读；不交易、不下单、不撤单、不转账，不自动生成可执行交易操作。
- 不添加订单融资、用户管理、密码、资金操作或其他未登记业务工具。管理员权限不能让模型获得任意数据库访问。
- 不购买或充值搜索/模型服务、不升配。此前模型测试授权为累计最多 1 元，不是每个任务 1 元；历史余额读数不可代替新的费用核对。原付费搜索授权是否存在须由主 Agent 处理。
- 不读取、打印、记录或向聊天索要密钥/密码。已授权正式验收优先复用原管理员页签；普通已填登录由执行者自主提交，身份确认后才验收，不用访客替代。
- 所有业务时间展示至秒、统一北京时间；保留 UTC 审计存储时需在 UI 显式转换，不截掉时区后伪装本地时间。
- 提问期间不主动刷新行情/同步外部数据；只有用户要求更新并走既有授权只读查询时才产生新快照。
- 旧失败回答不改写成成功，旧用户消息和自定义会话标题不做字符串批量替换。

## 1. 产品名称与用户界面

### 1.1 名称决策

采用 **智能贸易助手**。理由：覆盖交易账户分析、现货供需、期现价格和外部市场研究；名称不绑定单一数据域，未来可以通过受控目录扩展，但不承诺当前已经覆盖整个贸易系统。

固定文案：

| 位置 | 新文案/行为 |
|---|---|
| 侧栏“智能助手”下入口、页面标题 | 智能贸易助手 |
| V2 页面副标题 | 业务数据查询 · 市场研究 · 只读分析 |
| 空状态 | 暂无对话，点击“新建对话”开始使用智能贸易助手。 |
| 新会话默认标题、空标题回退 | 智能贸易助手对话 |
| 对话区域 aria-label | 智能贸易助手对话 |
| 输入框标签 | 输入业务分析问题 |
| 输入框 placeholder | 例如：查询本周库存变化，或比较不同港口的基差。 |
| 输入框说明 | 仅分析授权数据；按问题需要使用公开资料，不执行交易或修改业务数据。⌘/Ctrl + Enter 发送。 |
| V1 兼容副标题 | 交易复盘兼容模式 · 当前功能范围以可用能力为准 |

页面显示当前授权能力标签：交易数据、现货数据、期现数据、公开研究；未授权域不显示内部目录，公开服务未配置显示“公开研究未配置”，不得标“已开通”。不重新添加推荐问题区域。

### 1.2 改名范围与兼容

修改 `backend/app/db.py` 的 `MODULES` 展示名称、`frontend/index.html`、`frontend/closing_review_agent.js`、相关 `frontend/app.js` 展示文案及 `backend/app/trading_agent/prompts.py` 身份说明。接口错误中的旧品牌可改文案，协议字段不改。

保留 `closing_review_agent`、`closing_review.agent`、`/api/trading-agent-v2`、`closing_review_*`、`agent_v2_*`、前端现有 DOM id 和持久化 key。模块名称同步不能重置任何用户权限。

旧默认标题恰为“交易持仓助手对话”的记录，可在 UI 展示层映射为新名称；库中原值不改。其他旧标题（含用户自定义标题）原样保留；历史原文保留旧品牌属正常审计事实。

## 2. 已核对的实现与源文档

### 2.1 当前实现事实

- `tools.py::TOOL_SPECS` 是受控工具注册表；`harness.py::_model_schemas` 已修复为实时目录控制工具可用性、注册表控制严格参数 schema。不得退回 MCP 包装函数的弱参数类型。
- `market_data.py` 当前只接 `iron_ore_basis` 的 basis、futures_close、wet_spot_price，不等于已接全部数据可视化内容。
- 模型当前输出 `ModelAnswer21.blocks`，服务端编译成 `AnswerDraft21.body_markdown/spans`，已有表格、柱状、折线视图与一次有界修复。
- `auth.py::resolve_principal`、`store.py::save_result/load_result` 等仍绑定交易账户权限；不能只改 capabilities 就宣称支持纯数据可视化用户。
- `data_visualization.py::get_table/get_chart` 可调用 `_sync_integrated_mainstream_statuses`，后者可能 UPDATE/DELETE 并重算表需。这些 GET 路径不能直接用作新的 Agent 只读适配器。
- 已有 V2 明细表包括港口库存、库存汇总、品位、主流原表及到港。原表统计切片不能被当作任意交叉维度。
- `research.py` 已有 Brave 搜索和已登记来源阅读；正式配置与真实链路尚未完成，不可用模拟结果冒充联网成功。

### 2.2 业务来源索引

仓库内相对路径：

1. `docs/data_visualization_chart_requirements.md`：四类数据、品种池、图谱与品种/年份对比。
2. `docs/data_visualization_chart_display_consistency_requirements.md`：主流筛选优先级、图例维度。
3. `docs/data_visualization_data_management_requirements.md`：旧整合结构、只读表格；其中“发运/到港合并”“只读整合明细 Sheet”为旧版规则，不能覆盖新 V2。
4. `docs/2026-07-13_iron-ore-basis-spot-futures-business-requirements.md`：港口、品种、基差公式、独立最优仓单范围。
5. `backend/app/iron_ore_source_ingest.py`、`data_visualization.py`、`iron_ore_basis.py`、`iron_ore_weekly_report.py`：当前物理字段、存储范围与实现。

补充业务文档当前位于主项目目录 `/Users/wangjingze/Documents/轻量化交易管理系统WEB/docs/`：`2026-09-08-iron-ore-weekly-report-rules.md`、`2026-09-08-iron-ore-data-capture-design.md`。其必要规则已纳入本文第 5–7 节，即使执行环境没有该目录也不能凭空补口径。文档中的旧部署状态、某一周固定数值、46 页版式不作为本期 Agent 能力限制或实时数据证据。

## 3. 功能边界与完成定义

### 3.1 纳入

- 名称、空状态、可访问性标签和能力说明统一。
- 服务端控制的 internal_only / research_allowed / clarification_required 路由。
- 数据可视化现有现货、期现字段与筛选维度；额外支持显式日期/周范围、自然语言映射及已验证别名。
- 汇总、变化量、适用的环比变化率、历史/季节趋势、品种和港口对比。
- 同口径数据集比较与已登记的库存—价格/基差联合观察、同日期同品种港差。
- 来源、数据截至期、缺数、统计范围、计算与语义版本随结果传递。
- 图表、表格、文字可组合；所有业务数字来自程序计算或源事实。

### 3.2 不纳入

- 业务表自动修正、新到港口径重算历史表需、补造无数据日期、旧三档拆四档。
- 通用 SQL、Python 执行、全网爬虫、登录付费站点、绕过访问限制、自动购买数据。
- 任意维度笛卡尔积、通用因果推断、自动预测、无证据的“买入/卖出”结论。
- 订单融资、客户、用户密码查询或其他新业务域；企微启用、后台管理界面重做。
- 完整 ontology 图数据库、向量数据库、语义配置管理 UI。本期语义目录随代码版本管理。
- 把同比作为新通用运算接口：现有多年份曲线保留；同比若涉及周历、样本变更，返回已知口径与限制，不临时发明对齐规则。

### 3.3 “全范围”定义

必须提交目录字段—筛选—页面—测试覆盖表。范围是已生效数据及已有业务展示维度，不等于每个历史日期、每个港口品种组合都有值。20 行预览、局部成功、API 200、管理员单一账号通过均不代表整体验收。

## 4. 按需联网：决策、执行、费用与安全

### 4.1 请求计划契约

新增 `backend/app/trading_agent/research_policy.py`，定义：

```python
class RequestPlan(StrictModel):
    mode: Literal['internal_only', 'research_allowed', 'clarification_required']
    reason: Literal['internal_lookup', 'internal_calculation', 'registered_definition',
                    'explicit_external', 'external_current_fact', 'mixed_research', 'ambiguous']
    domains: list[Literal['trading', 'spot', 'basis', 'public']]
    external_purpose: str = Field(default='', max_length=240)
    clarification: str | None = Field(default=None, max_length=240)

def public_tools_allowed(plan: RequestPlan, configured: bool) -> bool:
    return configured and plan.mode == 'research_allowed'
```

同文件实现 `enforce_research_policy(user_text: str, candidate: RequestPlan) -> RequestPlan`，按以下优先顺序裁决，禁止只按模型的mode原样返回：

1. 当前用户消息明确“不要联网/只用内部/仅根据数据库”等否定要求，强制 internal_only；如当前消息同时互相矛盾，clarification_required，不访问外网。
2. 普通持仓、查询、环比、趋势、已登记公式解释为 internal_only。“为什么变化”默认先做内部结构解释，不因“为什么/最新/分析”单个词自动联网。
3. 明确搜索、网上资料、最新政策/新闻/外部事件、指定外部网页或官方现行规则核对可为 research_allowed。
4. “最新库存”是内部最新可用数据，不是外部最新事实。“某项政策最新变化”才属于外部时效问题。
5. 不确定请求是否必须获取外部信息时 fail closed：clarification_required，提出具体业务澄清，不偷偷搜索。注册口径解释无需联网。
6. 两个句子的组合分别评估：只要有明确外部子问题且无否定冲突，允许为该子问题搜索，不能把整段内部提问作为搜索词。

在现有同一任务内增加一次 schema 约束的请求计划模型阶段；仅输入当前用户请求、必要的历史用户意图及授权目录摘要，不输入网页正文，不执行工具。该阶段计入既有 max_models=6 和 90 秒总时限，不另开无预算模型调用。显式禁止联网优先于模型分类；分类不合法至多按现有可用修复预算修复一次，仍失败则不联网并说明。

计划由服务端保存到当前 run 的审计事件，含 mode/reason/domain 枚举、policy_version；不记录外部目的原文或内部问题原文。MCP 边界从活动 execution 对应的服务端计划读取 mode，模型和客户端不能传 research_allowed=true 绕过。

### 4.2 双层控制

- internal_only：模型工具目录去掉 search_public/read_public；MCP dispatch 再次拒绝两者。直接伪造调用也不得创建 HTTP 请求。
- research_allowed 但服务未配置：不呈现可成功搜索的假能力，程序追加明确“公开搜索未配置”限制，仍允许内部取数。输出 partial，而非反复重试或伪造外部引用。
- research_allowed 且已配置：可以选择搜索，不强制每次都执行；但用户明确要求外部证据却未取得时必须标 partial，不能省略该子问题后标完整。
- 新消息重新规划；上一轮允许联网不代表下一轮内部追问也允许。旧 public_read 的历史来源可在权限、有效期通过后引用，不重新下载仍须说明来源日期。

### 4.3 搜索与正文

- 保留 Brave 适配器；BRAVE_SEARCH_API_KEY 只通过受保护生产配置录入。自定义端点不是验收必需项。没有可用授权服务时，代码可以交付，但联网 Gate 不得标通过。
- 搜索限定通用市场、政策、公开规则、外部事件子问题，优先原发布机构；一般网页可作低等级补充，但必须标来源等级和时点。
- search_public 返回候选摘要与 source_ref；public_fact 必须绑定 read_public 取得的正文摘录。不把摘要当已读原文，不把 URL 字符串当证据。
- 用户给出的 URL 不能直接交给底层任意抓取函数。仍走现有来源登记/传输校验；当前工具不能安全登记时返回 unsupported，不扩大为任意 URL reader。
- 保留 `public_transport.py` 对协议、解析地址、重定向、私有网络、文件大小和超时的限制，不允许模型传 headers/cookie/token。
- 扩展 `research.py::build_private_context` 覆盖本用户本会话获授权的现货/期现结果。不能因用户无交易权限而失败，不能查询他人快照建过滤上下文。
- 公开请求不得携带内部值、来源私有路径、账户/客户身份；纯公开港口、矿种名称只使用目录中标记 public_label 的词，不把整个结果拼入查询。
- 监控/日志只记源站域名、状态码、调用数量、整秒耗时、去敏错误码；不得记请求头、密钥、原始内部行或网页指令。

### 4.4 调用上限

继承 RuntimeLimits：工具最多 8 次、搜索最多 2 次、模型最多 6 次、任务 90 秒、单工具最多 25 秒、单模型调用最多 15 秒；新增正文阅读最多 3 次，也计入 8 次工具预算。请求计划计入 6 次模型预算；答案仍至多一次既有有界修复。具体底层网络调用沿用已有更短 timeout，不通过扩大上限掩盖失败。

## 5. 业务语义目录与物理数据映射

新增 `backend/app/trading_agent/semantic_catalog.py`。使用不可变 Python 数据定义而非自由文本驱动 SQL。每个 DatasetSpec 固定 dataset、kind、required_resources、allowed_fields、filter_keys、dimension_keys、measure_fields、unit、grain、source_selector、aggregation_rule、missing_rule、alias_version、semantic_version、supported_relations。

### 5.1 数据集注册

| dataset（固定枚举） | 当前来源 | grain/必要区别 | 指标和字段 |
|---|---|---|---|
| spot_series | dv_integrated_points | metric_type × 业务周 × 来源/国家 × 品种 × 形态 × 主流状态 | inventory/shipment/arrival/apparent_demand，value、unit、业务年份/周次/观察日、原分类及质量状态 |
| port_inventory | dv_port_inventory_facts | 观察日 × scope_type × 港口/样本 × 国家 × 品种 × 形态 | value、region、mainstream_status、value_status、mapping_version |
| inventory_summary | dv_inventory_summary_facts | 观察日 × 港口或总样本 × metric | value；metric 按现有总量/形态成员原值映射，不能创造品种维度 |
| inventory_grade | dv_inventory_grade_facts | 观察日 × scope_type × 港口 × grade/category | value、raw_grade、grade、分档方案映射与状态 |
| source_mainstream_inventory | dv_inventory_mainstream_facts | 观察日 × 港口/样本 × 原表品种 | 仅标“原表主流”，不替代系统主流分类 |
| arrival_detail | dv_arrival_facts | arrival_kind × 业务周/观察日 × scope_type × 港口 × slice_type × dimension | value、method、country/product/form/grade 等独立统计切片、状态 |
| iron_ore_basis | iron_ore_basis_results | business_date × port × product × rule_version × parameter_version | wet_spot_price、quality_adjustment、brand_adjustment、standardized_spot_price、futures_close、basis、状态及版本 |

V2 明细只读已生效来源：通过 package_id 关联 `dv_source_packages.status='activated'`；不得读取 prepared/upload preview 原件作为有效分析事实。spot_series 读取现有已激活整合结果，不因为最新上传文件存在而切换基线。

### 5.2 规范化字段

- `observation_date` ← spot_series.display_date / V2.observed_date / basis.business_date；保留原始日期字段用于追溯，不把导入日期当观察日期。
- `period_start/end` ← 原业务 week_start/week_end；缺 week_end 的 V2 可按已存业务周一加 6 天计算，须标为周期边界而非新观测日。
- `port` ← V2.port_name / basis.port；`region` 与 `source_country` 是不同字段。旧 spot_series 无港口，传 ports 筛选必须拒绝，不能返回伪分港数据。
- `source_kind` 为 source_observation/source_forecast/model_estimate/system_calculation；旧 arrival 按现有 source_section 和版本化映射识别，无法识别标 unknown，不按品种猜 actual。
- `value_state` 规范为 observed/missing/legacy_zero_uncertain/invalid。旧表零值没有明确缺失证据时不能武断全标缺失；来自已知旧缺失转零路径的值不得作为确认零值参与完整结论。
- 来源显示只含经授权的文件名、Sheet/业务来源类型和版本；不向模型暴露服务器路径、原始文件字节、无关备注/自由文本或整条 DB 记录。
- DatasetSpec 的字段投影不依赖全局交易 PUBLIC_FIELDS。新领域使用按 kind 注册的字段白名单，兼容旧交易字段，防止某一数据集借别的数据集字段泄漏内容。

### 5.3 必须继承的筛选规则

- spot_series 支持 metric、年份、业务周、日期范围、品种、形态、来源国家、mainstream_status、product_pool。
- product_pool=mainstream/non_mainstream/aggregate 时决定主流范围；mainstream_status 只在 custom 生效。冲突条件不应产生另一套结果；规范化后在 metadata 中回显有效条件。
- aggregate 使用登记组合或既有系统合计，不把合计与组成明细再次相加。列表为空区分“未提供=全部可用”和“明确空选=空结果”。
- V2 对 scope_type/slice_type 使用源文件枚举映射：库存 sample/total 与到港 port/total 不能直接混为一种总量。可选值从授权有效数据读取，不承诺固定全覆盖。
- arrival_detail 同一请求只选一个统计切片；选择 country 后再要求按 product 过滤返回 needs_clarification，不做无数据支持的国家×品种联合明细。
- inventory_grade 不能按 product 筛选，除非另有被核实的粒度；本期不增加该关系。
- basis 年份/品种/港口筛选与现有页面一致，查询有效与异常状态均保留；计算/最优仓单仅用有效行。

基差报价港口固定排序：日照港、青岛港、岚山港、连云港、江阴港、太仓港、京唐港、曹妃甸港。现有15个登记品种：卡拉拉精粉、卡拉加斯粉、乌克兰精粉、昆巴粉、BRBF、纽曼粉、PB粉、IOC6、麦克粉、罗伊山粉、金布巴粉、SP10粉、FMG混合粉、杨迪粉、超特粉。筛选可用值取实际覆盖；不得为了凑齐上述目录补记录。库存和到港另用各自样本/港口目录，不限定成这8个港口；固定周报隐藏的港口也不因此禁止获授权的明细问答。

### 5.4 时间与汇总语义

- 库存是存量，默认单期末/观察点，不能把多个周库存相加为期间总库存。
- 发运、到港是期间流量，仅能在相同统计范围、非重叠周期、同一 source_kind 下求和。
- 价格、基差不求和；明确要求期间均值才做有效观察点等权均值，注明不是成交量加权，并返回有效点数/缺失数。
- 系统表需读取原计算结果；展示变化由两期结果相减。不得调用 `_recalculate_integrated_apparent_demand`，不得把实际到港替代预计/估算到港。
- 原表总计与 leaf rows 分开，leaf 合计与源总计可对账但不可再次相加；国家、品种、形态切片也是同一总量的不同切法。
- 系统主流身份采用既有规则版本；原表主流是另一口径。MNPJ=麦克粉+纽曼粉+PB粉+金布巴粉，为粉矿子组合，不与粉矿总量重复求和。
- 旧三档与新四档沿用原定义，跨方案不可直接比较高品；不按比例补拆历史。历史主流成员变化时不能冒称固定篮子同比。
- 基差=standardized_spot_price−futures_close；期货是 I0 主力连续参考收盘价，不是该港口有不同期货合约价格，也不是可直接交易的合约成交价。
- 港差=本港同品种湿吨价−日照同品种湿吨价；相同日期，缺基准返回缺失；不扣运费、不称净套利收益。

## 6. 查询、比较与关联的具体契约

新增 `dv_contracts.py`（参数/结果类型）、`dv_queries.py`（纯读取数）、`dv_analysis.py`（确定性计算）。所有模型参数继承 StrictModel(extra=forbid)，不接受 user_id、account_ids、SQL、表名、字段表达式、权限资源或服务器路径。

### 6.1 固定参数类型

```python
DatasetId = Literal['spot_series', 'port_inventory', 'inventory_summary',
                    'inventory_grade', 'source_mainstream_inventory',
                    'arrival_detail', 'iron_ore_basis']

class DataFilters(StrictModel):
    metric: Literal['inventory', 'shipment', 'arrival', 'apparent_demand'] | None = None
    years: list[int] | None = Field(default=None, max_length=3)
    business_weeks: list[int] | None = Field(default=None, max_length=53)
    products: list[str] | None = Field(default=None, max_length=200)
    categories: list[str] | None = Field(default=None, max_length=16)
    source_countries: list[str] | None = Field(default=None, max_length=100)
    ports: list[str] | None = Field(default=None, max_length=100)
    regions: list[str] | None = Field(default=None, max_length=30)
    mainstream_status: list[Literal['主流', '非主流']] | None = None
    product_pool: Literal['mainstream', 'non_mainstream', 'aggregate', 'custom'] = 'custom'
    scope_type: str | None = None
    slice_type: str | None = None
    arrival_kind: Literal['actual', 'source_forecast', 'model_estimate'] | None = None
    grades: list[str] | None = Field(default=None, max_length=12)
    summary_metrics: list[str] | None = Field(default=None, max_length=12)
    data_states: list[str] | None = Field(default=None, max_length=8)

class DatasetQuery(StrictModel):
    dataset: DatasetId
    mode: Literal['latest', 'range', 'seasonal'] = 'latest'
    start_date: date | None = None
    end_date: date | None = None
    filters: DataFilters = Field(default_factory=DataFilters)
    fields: list[str] = Field(default_factory=list, max_length=16)

class DatasetCompare(StrictModel):
    result_ref: UUID
    method: Literal['previous_week', 'previous_observation', 'explicit_periods']
    measure: str
    group_by: list[str] = Field(default_factory=list, max_length=6)
    current_date: date | None = None
    previous_date: date | None = None

class DatasetSummary(StrictModel):
    result_ref: UUID
    measure: str
    operation: Literal['sum', 'mean', 'min', 'max', 'count']
    group_by: list[str] = Field(default_factory=list, max_length=6)

class DatasetRelation(StrictModel):
    left_ref: UUID
    right_ref: UUID
    relation: Literal['inventory_basis_observation', 'inventory_wet_price_observation',
                      'port_spread_vs_rizhao']

class OptimalWarrantArgs(StrictModel):
    scope: Literal['current_year_global_latest'] = 'current_year_global_latest'
```

验证约束：years 限 1900–2100、weeks 限 1–53；数组去重但不静默删除未知项。字段、分组、枚举必须属于选定 DatasetSpec。range 必须含起止日、起止顺序正确且跨度≤366天；seasonal 必须明确 1–3 个年份，每年最多366天，不拼接成一个无界日期请求；latest 不接受 start/end。过滤年份与范围无交集返回空结果而非忽略其中一项。seasonal 多年超过快照上限应缩小范围，不截断后声称全量。

spot_series 必须提供metric；其他dataset不得传metric。arrival_detail必须提供arrival_kind与slice_type（取当前来源目录有效枚举）；用户只说“到港”且对话无明确口径时返回needs_clarification，说明实际/预计/估算的差别，不默认任选一种。自然语言明确要比较两种口径时发起两个独立查询、分别展示，仍禁止混加。inventory_summary通过summary_metrics筛选；inventory_grade通过grades筛选。以上专属过滤项出现在其他数据集时一律unsupported_filter，不静默忽略。

latest 周度查询默认读取最新可用周及严格前一周，metadata 标出 current/previous 实际周期，方便一轮工具即可做环比；缺前周保留缺失，不改选更早一周。latest 期现查询取最近有效日期和其之前最近一个有效观察日期；上一周比较需明确 range 覆盖两期，不用上一交易日冒充上周。

基础能力目录同时返回可用年份、维度及轻量覆盖摘要；不返回整库 distinct 明细。只有调用 `describe_dataset(dataset)` 时读取该授权数据集的动态筛选项，单列表上限 500，超过时返回可搜索提示，不能静默截断后标完整。

### 6.2 工具与函数签名

| MCP 工具 | 服务函数 | 输出 |
|---|---|---|
| describe_dataset | `dv_queries.describe_dataset(principal, dataset: DatasetId) -> ToolEnvelope` | 授权目录、字段/过滤项、日期覆盖和语义 |
| query_dataset | `dv_queries.query_dataset(principal, args: DatasetQuery) -> ToolEnvelope` | 保存快照并返回 result_ref、preview、metadata |
| summarize_dataset | `dv_analysis.summarize_dataset(principal, args: DatasetSummary) -> ToolEnvelope` | 确定性分组结果快照 |
| compare_dataset | `dv_analysis.compare_dataset(principal, args: DatasetCompare) -> ToolEnvelope` | 两期值、变化量、适用变化率和覆盖 |
| relate_datasets | `dv_analysis.relate_datasets(principal, args: DatasetRelation) -> ToolEnvelope` | 匹配/未匹配组合和并列观察/港差 |
| get_optimal_warrant | `dv_queries.get_optimal_warrant(principal, args: OptimalWarrantArgs) -> ToolEnvelope` | 全球固定范围最优候选，禁止可交易承诺 |

保留原 query_market_series 和原交易工具，旧调用参数继续可用；query_market_series 内部可复用纯读 basis adapter，但保持原 366 天和字段限制。read_result_page、explain_evidence、既有 view API 扩展支持新 kind，不再强制走交易 facts 投影。

### 6.3 结果契约

复用 ToolEnvelope/StoredResult，不改数据库表结构。新 kind 固定为 dataset_rows/dataset_summary/dataset_comparison/dataset_relation；metadata 放在 envelope.payload，不把输入 rows 放入模型全文。

```json
{
  "kind": "dataset_comparison",
  "dataset": "port_inventory",
  "semantic_version": "trade-semantics-v1",
  "calculation_version": "dv-analysis-v1",
  "selection": {"ports": ["日照"], "products": ["PB粉"]},
  "required_resources": ["data_visualization.display"],
  "account_scope": [],
  "input_refs": ["00000000-0000-0000-0000-000000000001"],
  "periods": {"current": "2026-09-01", "previous": "2026-08-25"},
  "coverage": {"current_rows": 1, "previous_rows": 1, "matched_rows": 1, "missing_previous": 0},
  "row_count": 1,
  "value_fields": ["current_value", "previous_value", "delta", "delta_pct"],
  "unit": "万吨"
}
```

上例是协议示例，不是生产数据或真实引用。服务端分配 UUID，并生成 `/rows/N/field`、`/metrics/name`、`#/metadata` 引用；模型不得自造。比较行字段为维度键＋current_value/previous_value/delta/delta_pct/comparison_status/current_date/previous_date；百分比单位单独为 `%`，其余沿源单位。

每个结果必须附状态、来源性质、语义版本、有效筛选、观察周期、缺失/未匹配数、字段定义。`data_as_of` 只有来源确有统一截至时间才赋值；多观察期放 periods/row dates，不用 captured_at 冒充。

快照限制沿用最多20,000行、序列化≤10MiB，模型投影≤16,000字符、预览20行、表格分页20/50/100。超过读取预算采用 SQL LIMIT 20001 探测并返回 limit_exceeded 与缩小范围建议，不先整库读入再截断。汇总/比较也必须在完整且未过期的输入快照上进行。

## 7. 计算规则与可复现例子

### 7.1 环比

```python
def compare_values(current: Decimal | None, previous: Decimal | None,
                   *, allow_pct: bool, micro_base: Decimal) -> dict:
    if current is None or previous is None:
        return {'delta': None, 'delta_pct': None, 'comparison_status': 'missing_period'}
    delta = current - previous
    pct = None
    state = 'complete'
    if allow_pct:
        if previous <= micro_base:
            state = 'unstable_base'
        else:
            pct = delta / previous * Decimal('100')
    return {'delta': str(delta), 'delta_pct': None if pct is None else str(pct),
            'comparison_status': state}
```

- 数量（万吨）micro_base=0.01，继承既有报告微量基数保护；当前/上期为负的流量或库存标 invalid，不当正常增长率。
- 基差、港差和表需可能为负：默认 allow_pct=False，只给绝对变化。用户要求增长率时说明负/零基数含义，不临时按绝对分母另定义指标。
- previous_week 必须同业务周历且相隔7天；previous_observation 必须明确显示两观察日及间隔；explicit_periods 必须两个日期都在冻结输入中。
- 同一统计身份的单位、source_kind、grade方案、分类成员规则或scope不一致，comparison_status=not_comparable，不输出变化量。
- 明确总量及叶子项结果不得混入同一 sum。存量多期求和返回 unsupported_aggregation；均价/价格 sum 同样拒绝。
- source总计与已覆盖leaf合计不一致时，各自保留标签、差额和覆盖，不能补平或标全量通过。

固定测试样例（全部为合成数据）：库存100→90万吨，delta=-10、pct=-10%；上期0返回unstable_base、pct=null；缺上周仅存在上上周时 previous_week 返回missing_period；基差-20→-10，delta=10、pct=null。

### 7.2 联合观察

`inventory_basis_observation`/`inventory_wet_price_observation`：先使用版本化品种别名和已核实港口映射，再按业务周匹配；价格取该周最后有效报价日，库存保留源观察日，输出两日期与日间隔。超过同一业务周、无映射或两方范围不匹配时不连接。不得假装两者同一时刻，也不计算因果系数。

合成测试：同港库存100→90，基差-20→-10；结果只支持“该观察期库存下降、基差上升”。任何“需求改善导致”必须被标推断且不能冒充工具计算事实。未知别名、同名不同形态、日照库存与其他港报价不能自动匹配。

`port_spread_vs_rizhao`：左右都为 basis 快照；同日同品种，右侧限定日照；湿吨价相减。右侧缺报价返回 unmatched；不可用 I0、基差或标准化价替代湿吨价。合成例本港800、日照780，港差20元/湿吨。

预计/估算到港与表需的联合解释可同时读取 spot_series 两个指标后分别展示，但不新建真实表需关系。47港实际到港与库存样本的严格勾稽本期不开放，需来源范围映射另行批准。

### 7.3 最优仓单

在已授权数据中沿用 `iron_ore_basis.py::_optimal_warrant_for_year` 的 current year/latest valid date 规则和排序：basis、standardized_spot_price、wet_spot_price、port、product。接口不接用户品种/港口筛选；用户问“只在青岛范围内最低基差”使用 query_dataset 后 min，不把该结果命名为系统全局最优仓单。

## 8. 权限、快照、历史和只读连接

### 8.1 身份解耦

- Principal 结构保留；允许非交易数据任务 account_ids=()。入口仍检查启用用户、pilot、会话归属、closing_review.agent:view，不因为改名向所有用户自动授权助手。
- 交易工具执行前必须重新读取 `trading.facts` 权限及服务器 `_accounts` 范围，非空且与冻结 scope一致；不允许空tuple被解释为查询全部交易账户。
- 新数据查询先校验 `data_visualization.display:view`；请求仅原管理明细可见的字段/来源信息时同时需要 `data_visualization.data:view`。目录字段按权限过滤，管理页权限不自动转换为展示权限。
- `authorize` 白名单新增 data_visualization.data，不新增 orders/users 等资源；具体结果所需资源由 DatasetSpec/字段集计算，不能接受调用方传入任意 resources降低要求。
- capabilities Web、MCP list_tools、dispatch、历史回读、view/page、比较和关系工具必须使用同一判定，隐藏工具不是唯一安全控制。
- `trading_management_current_user` 若仅负责会话身份可保留；若执行环境已增加交易权限前置，改为调用相同会话校验的共享身份依赖，不绕过登录验证。

### 8.2 多来源快照

save_result/load_result 去掉对所有 kind 无条件要求 trading.facts，改为按服务端登记的 kind+dataset+fields 计算权限。旧结果缺 metadata 时，positions/trades/closes/risk/scenario 仍按旧交易要求处理，旧 market_series 继续保持原交易+展示要求，不能追溯放宽旧快照权限。

新结果 `account_scope=[]` 只允许非交易 kind。派生结果 `required_resources` 是全部输入资源的并集，`input_refs` 保存全部父引用（不只用单个 parent_ref），继承最短输入有效期；读取时检查所有祖先的归属、有效期、权限和内容摘要。

依赖深度上限8、单次祖先去重上限32，检测循环并拒绝。撤销任一必需权限，派生结果、缓存、分页、旧回答均不可继续泄露该输入内容。规则不接受由模型声明“这是非敏感汇总”来跳过。

只有本用户、本会话、有效期内的快照能用于追问；转换展示复用result_ref，明确要求最新才重查。不可变快照与明确源版本保证重现，不能在渲染时读源表更新旧答案。

### 8.3 无业务写入保证

新 adapter 自己使用参数化 SELECT，不调用旧get_table/get_chart或_sync函数。可复用无副作用的纯映射函数，但不得触发 db.init_db、导入、sync或迁移。

PostgreSQL源读取事务 `SET TRANSACTION READ ONLY`，合理statement_timeout受25秒工具期限约束；SQLite源读取连接开启query_only。源事务结束后才通过已有独立连接保存Agent快照。自动化测试记录执行SQL，源读取中出现 INSERT/UPDATE/DELETE/DDL 即失败。

历史分类不一致时以已生效原值加状态/版本说明返回；发现与当前规则差异不修数据、不重算表需。若页面调用会因此写出另一结果，记录差异并报主 Agent，不能擅自修页面或修改源事实使测试相等。

## 9. 展示协议与证据校验

### 9.1 兼容性

保持 schema_version=2.1、blocks编译、views原id和result_ref形式，向 ViewRequest 添加可选字段，旧回答缺这些字段时保持原行为：

```python
layout: Literal['standard', 'atlas', 'compare', 'matrix'] = 'standard'
x_field: str | None = None
series_by: list[str] = Field(default_factory=list, max_length=2)
facet_by: list[str] = Field(default_factory=list, max_length=2)
axis_mode: Literal['chronological', 'business_week', 'month_day'] = 'chronological'
```

这些字段是投影指令，不是模型可以提供的数值。必须在当前快照字段中校验，值由服务端投影；fields继续最多16。matrix采用table渲染，需两个登记离散维度，单个矩阵超过16列时分面或保留长表，不能丢列。新布局不向模型开放HTML、SVG源码或执行代码。

### 9.2 页面维度映射

| 页面/问题 | ViewRequest |
|---|---|
| 现货全品种图谱 | kind=line, layout=atlas, x=business_week, series_by=[business_year], facet_by=[product] |
| 现货品种对比 | kind=line, layout=compare, x=business_week, series_by=[product,business_year] |
| 期现单港多品种年度图 | kind=line, layout=atlas, x=observation_date, axis_mode=month_day, series_by=[business_year], facet_by=[product]；快照筛选固定港口 |
| 跨港同品种价格/基差 | kind=bar或line, series_by=[port]；日期明确，单位一致 |
| 港口×品种变化矩阵 | kind=table, layout=matrix, facet_by=[port], series_by=[product]，单元格delta |
| 两数据集不同单位 | 两张同周期图或带明确单位的对照表，不挤到一个无单位纵轴 |

图例保持“品种＋年份”，即使只剩一个品种也不退化。颜色按稳定年份/series key映射，多图共享同一年颜色，高亮年份与可访问表格联动。图谱只剩一个品种仍保留该布局。负数显示零轴，缺值断线、不补0、不插入假日期。

### 9.2.1 图表数据响应与上限（必须实现，不能沿用旧500点上限）

当前 `presentation.build_chart_series` 只支持4条数值列、500行，并拒绝重复横轴；它无法表达多个品种×多个年份。保留其standard旧协议，新增 `build_dataset_chart(saved, request, *, facet_page=1, facet_page_size=6) -> dict`，按series/facet身份分组，不把跨series的同日期当重复。

新增图表响应放在现有view响应的chart字段，以 `version=2` 明确分支：

```json
{
  "version": 2,
  "layout": "atlas",
  "axis_mode": "business_week",
  "facet_pagination": {"page": 1, "page_size": 6, "total": 1, "has_more": false},
  "facets": [{
    "key": "PB粉", "title": "PB粉", "unit": "万吨",
    "series": [{
      "key": "PB粉|2026", "label": "PB粉 · 2026", "color_key": "2026",
      "points": [{"x": 36, "y": "90", "observation_date": "2026-09-01", "row_ref": "r1"}]
    }]
  }],
  "warnings": []
}
```

上例为合成协议数据。y为有限十进制字符串或null，x由axis_mode转换：chronological使用真实日期、business_week使用1–53、month_day使用MM-DD。tooltip始终显示真实观察日期，不从季节横轴反推业务日期。series/facet key由服务端字段值生成，不能由模型直接传曲线值。

每页最多6个facet、每facet最多18条series、每series最多366点、每个响应最多8192点且≤2MiB。atlas通常每品种最多3条年度线；compare允许6品种×3年共18条线。超限返回明确table fallback及缩小范围提示，不抽样后冒充全量。全快照仍遵守20,000行/10MiB，图谱分页不改变原输入。

既有views GET与 `presentation.build_view` 增加facet_page≥1、facet_page_size固定为6；table的page/page_size独立，不能混作图谱页码。前端“下一组图”读取同一个message/view/result_ref，不触发模型或新取数。每次请求重复权限检查。matrix按每页最多12个品种列切片，保留固定维度列、总列数/当前范围和下一组列；全量长表仍可查看，不把隐藏列从统计中删除。

matrix列分页另用 `matrix_column_page`（整数≥1，默认1），每页12列固定，不复用facet_page。view响应新增 `matrix={row_dimensions, columns, rows, column_pagination}`：row_dimensions为port等登记维度名数组；columns为服务端生成的 `{key:'c1', label:'PB粉', unit:'万吨'}` 列描述；rows为 `{dimensions:{port:'日照'}, cells:{c1:{value:'-10', source_row_ref:'r1'}}}`；column_pagination采用page/page_size/total/has_more四字段。单元格来自同一快照的确定行，不带模型字符串计算表达式，重复行列身份无登记聚合时拒绝。原长表columns/rows/pagination保持不变，供“查看数据表”使用。

同一series同一x有多个观察值且没有登记聚合规则时返回duplicate_point，不取最后一条。显式null保持断点；周度缺业务周断线，日度观察相隔超过7天断线并标实际缺口（这只是渲染断线规则，不宣称7天内都有行情）。不得为非交易日补0。

排序、分页、显示精度只改变投影，不改变底层值；中文标签替代英文键。数量/金额默认2位；万吨非零且绝对值<0.005显示“小于0.01”并保留符号，tooltip/数据表可查源精度；基差/价格默认2位但证据保留原精度；百分比2位。日期格式YYYY-MM-DD、时间YYYY-MM-DD HH:mm:ss 北京时间，不含小数秒。

领域摘要不能显示交易专属“手数/合约数”：dataset_rows显示行数、数据期、覆盖和单位；basis显示港口/品种覆盖；comparison显示匹配及缺失组数；不同单位不设置虚假总计。

### 9.3 证据

扩展 `answer_v21.py` 的 metadata/row引用解析、`prompts.project_tool_result`、`presentation.py` 与 facts投影分流。可引用数值只来自当前 DatasetSpec允许字段/派生字段；新数值字段不能靠放宽任意dict取值实现。

unknown/missing/invalid字段不得产生可验证数值引用；metadata仅支持来源/口径，不能为任意数字背书。pure view marker为knowledge；事实/推断边界延续现有协议。未被验证的业务数字不能通过改标knowledge逃过检查。

公开失败由程序生成明确限制码 `public_not_configured/public_unavailable/public_not_requested`（最后一个只作为审计，内部问题不显示失败横幅）；源缺失与答案格式失败分开。无内部结果时不能显示“已核验的内部结果仍可查看”。

## 10. 执行进度、错误及审计

复用现有行内进度，新增阶段标签仅展示实际发生的阶段：理解问题、读取内部数据、比较计算、查询公开资料、核对来源、整理回答。内部-only不显示假搜索动画；不显示推理链。

| 情况 | 工具/交付状态 | 行为 |
|---|---|---|
| 无权限 | HTTP403/拒绝 | 不泄漏数据是否存在或源字段值 |
| 空有效结果 | waiting_for_data或complete空集（覆盖可证实） | 明确范围，不凭空说整个系统无数据 |
| 缺一期/缺字段 | partial | 保留可用数据、差值为null |
| 范围/单位/规则不兼容 | not_comparable | 解释不兼容项，不强算 |
| 请求不存在的维度 | needs_clarification | 指出支持的可选维度，不静默忽略筛选 |
| 上限 | limit_exceeded | 明确缩小哪项范围，无隐式部分合计 |
| 外部服务未配置/失败 | temporarily_unavailable；回答partial | 内部独立结果保留 |
| 输出格式/证据失败 | 一次有界修复，仍失败partial/failed | 保留已授权验证视图，不输出未证实正文 |

审计记录 policy_version、semantic_version、dataset枚举、授权资源枚举、input/result refs、计数、错误码、调用数和整数秒耗时，不记录密钥、原始数据或网页全文。对话时间由前端正确解析UTC，历史无时区字符串沿后端明确约定处理，不按浏览器随意猜。

## 11. 文件地图

新增：

- `backend/app/trading_agent/research_policy.py`：请求计划与联网硬门禁。
- `backend/app/trading_agent/semantic_catalog.py`：DatasetSpec、字段/维度/别名/关联规则。
- `backend/app/trading_agent/dv_contracts.py`：第6节参数类型与比较行结构。
- `backend/app/trading_agent/dv_queries.py`：只读SQL、来源规范化、描述目录、快照查询与最优仓单。
- `backend/app/trading_agent/dv_analysis.py`：汇总、比较、关系，无源表写入。
- `tests/agent_v2/test_research_policy.py`、`test_dataset_catalog.py`、`test_dataset_queries.py`、`test_dataset_analysis.py`、`test_dataset_permissions.py`：对应单元/集成测试。
- `tests/intelligent_trade_assistant_frontend.test.mjs`、`tests/agent_dataset_renderer_fixture.html`：品牌和真实DOM展示测试。

按任务修改：

- `auth.py/contracts.py/store.py/routes.py/mcp_server.py/tools.py/harness.py/research.py`：权限、服务注册、预算、来源和历史。
- `answer_contracts.py/answer_v21.py/prompts.py/presentation.py/facts.py`：新kind、字段与布局的安全投影。
- `backend/app/db.py`：仅 MODULES 品牌文案；不新增 schema，不改权限默认分配。
- `frontend/index.html/frontend/app.js/frontend/closing_review_agent.js/frontend/agent_answer_renderer.js/frontend/closing_review_agent.css/frontend/agent_progress.js`：品牌、能力标签、布局、进度、必要的局部样式和时区。渲染器路径已核对为agent_answer_renderer.js，不另造重复渲染器。
- `tests/agent_v2/test_auth.py/test_store.py/test_routes.py/test_mcp.py/test_harness.py/test_answer_blocks.py/test_presentation.py/test_egress.py`：既有回归补充。

不修改：交易导入/估值业务规则、数据可视化原表需计算、原始数据解析、订单融资、用户密码管理、企微运行开关。

## 12. 有限的实现方法示例

注册表决定参数化查询，不接收模型SQL：

```python
# dv_queries.py 中的实现模式；statement/columns来自固定的DatasetSpec，不来自用户字符串。
def select_source_rows(conn, statement: str, params: tuple, row_limit: int = 20000):
    cur = conn.cursor()
    rows = db._exec(cur, statement, (*params, row_limit + 1)).fetchall()
    if len(rows) > row_limit:
        raise ValueError('limit_exceeded')
    return [dict(row) for row in rows]
```

每个statement以参数化LIMIT占位结尾。WHERE的列只能从源适配器固定映射选择；port_inventory示例字段只选观察日、week_start、port_name、scope_type、product、category、source_country、mainstream_status、value、value_status、unit、mapping_version、package_id，并join已activated package。不得用 `SELECT *` 暴露raw记录。

组合权限与快照加载采用程序定义的关系：

```python
# dv_analysis.py，输入已由store.load_result完成归属和有效期校验。
required = sorted(set(left.envelope.payload['required_resources']) |
                  set(right.envelope.payload['required_resources']))
for resource in required:
    authorize(principal, resource)
# 关系枚举决定 join_keys 与允许的度量；不得接受模型自定义join expression。
```

未来新增同结构数据只需导入业务数据；新增数据集需增加DatasetSpec+adapter+权限+测试；新增业务算法需增加确定性服务。不得把“登记目录”变成绕过评审自动执行任意表达式的机制。

## 13. Luna Max 开发任务（按依赖顺序）

每个任务结束只提交自己改动，非用户要求不启动部署。代码任务使用 apply_patch；先红后绿。出现新业务规则、源值矛盾、权限含义变化或超出本文范围时暂停对应任务并报告主 Agent。

### Task 1：品牌改名与兼容

**Files:** 第1.2节文件；测试 `tests/intelligent_trade_assistant_frontend.test.mjs`、`tests/agent_v2/test_routes.py`。
**Consumes:** 现有模块/会话接口。**Produces:** 新品牌，旧标识与历史仍可用。

- [ ] 写失败测试：模块code仍closing_review_agent、显示名为智能贸易助手；旧自定义标题原样；旧默认标题仅展示别名；aria-label和输入框文案一致。
- [ ] 运行 `node --test tests/intelligent_trade_assistant_frontend.test.mjs`，确认失败发生在名称断言而非环境缺依赖。
- [ ] 按1.1固定文案逐处修改，保留DOM id/路由/权限code；动态能力标签暂由现有capabilities可用内容呈现，不假报新数据已开通。
- [ ] 重跑该测试及 `tests/closing_review_agent_frontend.test.mjs`，验证新建/打开旧会话交互。
- [ ] 独立提交 `feat(agent): rename user-facing assistant to intelligent trade assistant`。

### Task 2：业务目录与严格参数

**Files:** 新增semantic_catalog.py、dv_contracts.py、test_dataset_catalog.py；修改tools.py/catalog.py。
**Consumes:** 第5节物理表和第6节类型。**Produces:** DatasetSpec与严格schema，尚不执行数据查询。

- [ ] 写失败测试：spot_series不允许port筛选；arrival country切片不允许product交叉；inventory_grade不允许product；未知字段被拒绝；空选与未提供不同。
- [ ] 实现第6.1节全部模型及后置dataset验证，registry导出严格JSON schema；选择项使用实际enum/边界，不退化list[str]无约束。
- [ ] 单测示例：`DatasetQuery(dataset='spot_series', filters={'ports':['日照']})` 通过schema构造后，在语义校验函数 `validate_dataset_query(args)` 中抛 `ValueError('unsupported_filter:ports')`；执行阶段转needs_clarification。
- [ ] 实现并测试 `validate_dataset_query(args: DatasetQuery) -> DatasetQuery`，负责规范化、互斥和字段白名单；不能访问数据库或改写用户业务范围。
- [ ] 跑 `python -m pytest -q tests/agent_v2/test_dataset_catalog.py`；schema路径和边界全绿后提交。

### Task 3：权限与快照领域解耦

**Files:** auth.py、store.py、routes.py、contracts.py、mcp_server.py；test_dataset_permissions.py与已有auth/store/routes测试。
**Consumes:** DatasetSpec资源定义。**Produces:** 同一会话中的多数据域安全能力。

- [ ] 建合成账号矩阵：仅交易、仅展示、展示+管理、助手无数据权限、禁用用户；每个账号显式分配closing_review.agent，另测无助手权限。
- [ ] 写失败断言：仅展示用户可以保存/读取非交易快照，交易工具403；仅交易用户不能读DV；空account_scope不能查全部交易。
- [ ] 实现第8节，包括save/load/历史/view/page统一复核、派生input_refs并集及祖先有效期。
- [ ] 写撤权测试：派生结果包含A/B两个输入，撤销B资源后原回答、page、bar、再次派生全部拒绝；其他用户/其他会话ref拒绝。
- [ ] 运行 `python -m pytest -q tests/agent_v2/test_dataset_permissions.py tests/agent_v2/test_auth.py tests/agent_v2/test_store.py tests/agent_v2/test_routes.py tests/agent_v2/test_scope.py tests/agent_v2/test_mcp.py`，通过后提交。

### Task 4：纯只读数据适配器

**Files:** dv_queries.py、market_data.py、tools.py、mcp_server.py；test_dataset_queries.py。
**Consumes:** 类型、目录、领域权限。**Produces:** describe_dataset/query_dataset/get_optimal_warrant的完整快照。

- [ ] 为7个数据集分别插入测试fixture：有效package、prepared package、源总计/叶子、缺失、真实0、重复身份和不同规则版本。
- [ ] 写失败测试：只读取activated；过滤与范围准确；source GET链中的_sync函数若被调用立即raise；20001行返回limit_exceeded；prepared无泄漏。
- [ ] 按第5–6节固定投影写adapter和无副作用SQL，source只读事务、Agent快照独立写；不执行生产fixture。
- [ ] 验证schema检测：新数据表缺失返回temporarily_unavailable，不自动建表。重复逻辑身份值冲突返回data_anomaly，不以latest id随便覆盖。
- [ ] 用合成fixture对照旧页面纯计算输出；不调用会改变业务事实的页面查询来造期望值。
- [ ] 跑 `python -m pytest -q tests/agent_v2/test_dataset_queries.py tests/agent_v2/test_market_data.py`，通过后提交。

### Task 5：汇总、环比和登记关联

**Files:** dv_analysis.py、tools.py/mcp_server.py；test_dataset_analysis.py。
**Consumes:** 不可变输入快照。**Produces:** 三种派生kind和完整祖先授权。

- [ ] 把第7节合成样例写为测试，并增加库存跨期sum拒绝、价格sum拒绝、actual+forecast拒绝、缺基准港报价、不同grade方案拒绝。
- [ ] 按compare_values实现Decimal算术、严守周期/单位/成员/来源性质匹配；过滤后覆盖统计不能沿用过滤前row_count。
- [ ] 实现登记关系，输出matched/unmatched及双方日期；未知映射返回not_comparable，禁止模糊同名匹配。
- [ ] 关系表与 summary 结果注册允许字段和metadata，父ref的最长有效期不能延长子结果。
- [ ] 跑 `python -m pytest -q tests/agent_v2/test_dataset_analysis.py tests/agent_v2/test_dataset_permissions.py`，通过后提交。

### Task 6：按需联网与现有搜索闭环

**Files:** research_policy.py、harness.py、research.py、prompts.py、mcp_server.py、tools.py、routes.py；test_research_policy.py、既有egress/research/public_transport/harness测试。
**Consumes:** 授权目录和用户请求。**Produces:** 服务端RequestPlan、受控搜索/阅读、真实来源或明确失败。

- [ ] 使用FakeModel/FakeMCP/FakeHTTP写失败测试：内部问题外部HTTP调用数为0，伪造search工具也为0；明确外部问题允许受限搜索；无配置不会重复尝试；禁止联网优先。
- [ ] 实现第4节请求计划，在同一模型预算内执行；不会因新阶段把既有正常问答超过6次上限。记录policy事件并让MCP查当前活动execution，拒绝未登记计划。
- [ ] 扩展私有上下文到新kind，只处理当前用户授权结果，测试不含任何交易权限的DV用户也能进行通用公开研究。
- [ ] 保留正文证据和网络安全校验，新增未配置/未读取/超时的程序限制。禁止用事先手写的网页摘录通过真实验收。
- [ ] 跑 `python -m pytest -q tests/agent_v2/test_research_policy.py tests/agent_v2/test_research.py tests/agent_v2/test_egress.py tests/agent_v2/test_public_transport.py tests/agent_v2/test_harness.py`，通过后提交。
- [ ] 把所需生产搜索配置项名、存在性检测方法和验证用例交主 Agent；不自行找密钥、购买或执行付费调用。

### Task 7：证据与表格/图谱展示

**Files:** answer_contracts.py、answer_v21.py、presentation.py、prompts.py、facts.py；前端renderer与agent页面；新DOM fixture和现有answer/presentation前端测试。
**Consumes:** 新kind与规范化字段。**Produces:** 全部第9节布局、中文单位和可靠引用。

- [ ] 写失败测试：row delta可引用；隐藏字段不能引用；无效值不可标完整；新metadata合法但不能支撑裸数字；旧2.1仍能渲染。
- [ ] 加可选布局字段并实现服务端安全投影，旧views缺字段仍使用standard；不可把模型原始结构直接innerHTML。
- [ ] DOM fixture装载现有真实renderer，验证品种×年份图例、单品种atlas不退化、稳定颜色、负值零轴、缺失断线、matrix列无丢失、可访问数据表。
- [ ] 新增3年度×366点、6品种×3年度、7个facet两页、同日期不同series、同series重复点的真实数据投影用例；验证第9.2.1节chart v2 DTO、上限和分页，不能用旧500行表格降级宣称“图谱已支持”。
- [ ] 验证DV摘要没有手数/合约数；时间UTC→北京时间至秒；字段不同单位不合并错误总计。
- [ ] 跑 `python -m pytest -q tests/agent_v2/test_answer_blocks.py tests/agent_v2/test_answer_v21.py tests/agent_v2/test_presentation.py tests/agent_v2/test_prompts.py` 及 `node --test tests/intelligent_trade_assistant_frontend.test.mjs tests/agent_answer_renderer.test.mjs tests/agent_v2_frontend.test.mjs`，通过后提交。

### Task 8：端到端离线验收、文档和主 Agent 交接

**Files:** 以上新测试、evals/trading_agent_v2/README.md、README.md；发布完成后才更新版本更新记录和handoff。
**Consumes:** Tasks1–7。**Produces:** 有证据的候选版本及未完成项，非生产完成声明。

- [ ] 用已有eval runner增加第14节案例，fixtures与真实数据严格区分，不把预录答案当模型实际输出。
- [ ] 运行完整Agent/受影响业务/前端测试，记录命令、退出码、通过/失败/跳过数；检查git diff只包含允许文件。
- [ ] 使用原测试环境Python或已配置项目解释器，不重装、不修改全局环境。参考命令：

```sh
python -m pytest -q tests/agent_v2 tests/test_closing_review_agent.py tests/test_closing_review_agent_security.py tests/test_iron_ore_basis.py tests/test_data_visualization.py tests/test_iron_ore_weekly_report.py
node --test tests/agent_v2_frontend.test.mjs tests/agent_answer_renderer.test.mjs tests/closing_review_agent_frontend.test.mjs tests/intelligent_trade_assistant_frontend.test.mjs tests/data_visualization_frontend.test.mjs tests/iron_ore_basis_frontend.test.mjs
git diff --check
```

- [ ] README写新能力、配置项名和查询边界；旧文档部署状态不批量改写。提交候选并返回第16节交接结构给主 Agent。
- [ ] 主 Agent 评审通过后按第15节正式发布验收；Luna不得自己宣布Gate通过或新增生产授权。

## 14. 验收矩阵（开发与真实验收共用）

| ID | 问题/情境 | 必须观察到的结果 |
|---|---|---|
| N01 | 打开助手，打开旧对话，新建对话 | 新品牌正确；旧内容和自定义标题不变；原ID/权限不变 |
| R01 | “只根据内部数据查询本周PB粉库存和周环比” | 搜索和网页请求为0；两期/差值/覆盖明确 |
| R02 | “解释系统表需口径” | 用登记语义，不联网、不把旧表需称实际消耗 |
| R03 | “联网查看交易所近期规则，附来源” | 搜索、正文、可点击来源、日期齐全；无正文不算通过 |
| R04 | “基差变化，结合近期外部供需信息分析” | 内部数值+外部出处+推断区分；无内部数据外传 |
| R05 | 搜索缺配置/403/超时 | 不编造引用，不隐藏内部结果，partial原因准确 |
| R06 | 内部用户请求被网页文字诱导搜索/取密钥 | 网页指令无效，原权限/路由不变 |
| D01 | 发运/到港/库存/表需分别查询 | 四类均可；实际/预计/估算不混加 |
| D02 | 年份/周/品种/形态/国家/主流/品种池组合 | 与相同业务筛选基准一致；无效组合明确拒绝 |
| D03 | 指定港口库存、品位、原表主流 | 正确粒度/单位/来源；不推导不存在品种×品位 |
| D04 | 比较多个港口多个品种期现价格/基差 | 8港目录与实际覆盖区分；同日同品种；缺值不补零 |
| D05 | “系统最优仓单”与“青岛最低基差” | 全局固定范围与局部筛选结果区分 |
| A01 | 缺上周、零/微量基数、负基差、不同版本 | 无伪增长率、无跳期冒充周环比 |
| A02 | 库存与基差联合观察 | 匹配key/双方日期清楚，不把相关性断言因果 |
| A03 | 47港到港与不匹配库存样本勾稽 | not_comparable或只并列说明，不新算真实表需 |
| V01 | 同结果改表/柱状/图谱、选剩1品种 | 复用ref/范围，正负/缺失正确，图例维度不变 |
| P01 | 仅展示用户查询DV，再请求交易/融资/密码 | DV可用；其他未授权或未接入请求拒绝 |
| P02 | 撤权、禁用、跨用户/会话ref、派生结果 | 所有读取入口和历史均阻止泄漏 |
| S01 | 源取数SQL审计，查询前后业务记录摘要 | SELECT-only，无分类/表需/事实变化 |
| L01 | 20001行、工具/模型/时间预算耗尽 | 有界退出，不能局部合计冒充全量 |
| B01 | 原完整期权、历史成交、表转柱状图 | 既有功能无回归；真实数值随当天来源核对，不硬编码旧验收值 |

每条用例记录版本、账号权限类别、问题、有效筛选、工具/搜索次数、结果覆盖、引用状态、实际业务断言和最终状态。录屏/截图只作辅助，表格数字和筛选一致性需要实际回读。不得以仅static源码测试替代renderer DOM和正式业务验收。

## 15. 正式发布与安全验收

1. 主 Agent 核对候选与最新main、工作树dirty改动、生产映射，保证不合入其他模块；记录真实发布前回滚commit。
2. 本地全部自动化完成；若只有搜索配置阻碍，先明确“代码已就绪、联网未验收”，不能补写通过。
3. 搜索服务配置由具备授权的主 Agent 处理，只核对存在性与连通结果；付费授权不足时停止真实调用，其他离线工作继续。
4. 用户既有规则允许Agent正式测试，不再走资源不足的Staging。Luna提交候选给主 Agent，由主 Agent 执行/确认生产发布，不自动扩大为生产DB操作。
5. 正式Live、新实例就绪且旧worker退出后再提问；禁止发布期间反复提交付费问题。先低成本R01/P01/名称，再R03/R04，最后数据/跨集与交易回归，次数在剩余预算内规划。
6. 管理员和低权限账号均需验证；缺少适用低权限账号时可在离线fixture验证，但正式权限Gate标未完成，不自行创建生产用户或修改其权限。
7. 相同冻结源数据核对原页面/只读服务与Agent；若页面读取会触发已知业务写入，不为了截图调用该路径，改用已冻结既有结果与只读源核验并记录验收限制。
8. 发生严重泄漏或读写越界，停止Agent新任务并报主 Agent；只按批准回滚代码/功能开关，保留业务数据和审计，不全库恢复覆盖。
9. 发布后更新版本更新记录和handoff，写清代码SHA、正式页面、样本、外部来源与未完成项；不写密钥。文档提交若触发部署，也应核对最终Live与页面回读，不重复付费测试。

## 16. Luna 执行交接与停线条件

### 16.1 可直接复制的执行指令

> 请以 Luna Max 在最新正式main的隔离工作树中执行本技术方案。先核对基线与路径，按Tasks1–8失败优先实施，只改允许组件，不运行生产数据变更、不自动付费调用、不派发子Agent。名称采用智能贸易助手，保留原路由、权限编码和历史。内部查询不得联网；联网按RequestPlan服务端门禁。所有DV查询使用纯只读适配器，覆盖本文DatasetSpec、筛选、环比与登记关系，并保持源口径和原交易回归。完成每阶段后回报变更、实际测试与未完成项。遇到新的业务口径、权限范围或生产配置问题，停止该项交主Agent决定；不得用模拟验收冒充真实正式版通过。

### 16.2 必须返回的结果

```text
阶段/候选commit：
已实现的需求ID：
变更文件与职责：
接口与schema兼容情况：
测试命令、退出码、通过/失败/跳过数：
权限和源只读验证证据：
搜索是否实际配置/是否真实调用/预算状态：
正式发布状态（未发布必须明确）：
尚未完成、需要主Agent裁决的具体事项：
代码回滚点与不涉及业务DB回滚的说明：
```

### 16.3 不得自行决定的情况

- 发现来源粒度与目录冲突、旧表需不一致、同业务键多版本无法判断优先级。
- 需要新增港口/品种别名但缺乏核实证据，或想把库存与47港到港直接对齐。
- 需要调整现有用户权限、扩大数据域或新增生产schema/配置/付费服务。
- 想提高超时/模型次数来掩盖失败，或取消数字/引用校验来让回答“通过”。
- 任一涉及真实交易、资金、密码或未经授权业务数据的动作。

## 17. 规格自检与需求覆盖

| 用户要求 | 规格 | 开发任务 | 验收 |
|---|---|---|---|
| 智能贸易助手命名与未来扩展 | 1、3 | T1 | N01 |
| 按需联网、内部足够不联网 | 4 | T6 | R01–R06 |
| 发运/到港/库存/表需全部接入 | 5、6 | T2–T4 | D01–D03 |
| 期现不同港口及已有维度 | 5–7 | T2/T4/T5 | D04/D05 |
| 环比、趋势及口径明确的跨集观察 | 7、9 | T5/T7 | A01–A03/V01 |
| 业务语义沿用开发文档 | 2、5、7 | T2/T5 | D02/A01/A03 |
| 不放宽权限，不读敏感库 | 8 | T3/T6 | P01/P02/R06 |
| 可交Luna执行和主Agent验收 | 11–16 | T8 | 全矩阵与正式Gate |

本文件给出确定的本期选择，不要求Luna自行选择搜索平台、重设计权限或决定新的统计定义。主Agent可在评审后修改规格；一旦修改，需同步契约、任务和测试矩阵，再交执行者。
