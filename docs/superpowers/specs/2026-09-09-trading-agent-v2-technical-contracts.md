# 交易 Agent V2 技术合同

日期：2026-09-09。状态：开发输入文档，未实现或线上验证。配套 [业务设计](2026-09-09-trading-agent-v2-upgrade-design.md) 与 [实施计划](../plans/2026-09-09-trading-agent-v2-implementation.md)。本文件细化已确认需求，不扩大账户、群、业务模块或交易权限。

## 1. 技术选择与运行拓扑

- 保留既有 FastAPI 网页服务，增加轻量 V2 入队/查询路由；不在 HTTP 请求内等待模型完成。
- 一个 Python Agent 运行进程：数据库任务轮询、DeepSeek 调用、企微长连接、单任务编排；初期串行执行任务，不引入 Redis/Celery、多 Agent 或多模型路由。
- Agent 进程内运行独立 Starlette MCP ASGI app，监听 `127.0.0.1:8766/mcp`，生命周期内启动/关闭 SDK session manager；Harness 通过真实 SDK client 调用。该 app 不挂载到现有公网 main.py。
- 使用 Streamable HTTP 的结构化工具结果；服务端无跨用户 session 状态，授权依据每次请求的执行令牌，绝不把 MCP session id 当身份。Host 仅允许 loopback，拒绝非允许 Origin；不启用浏览器 CORS。
- 首版是第一方内部 MCP，采用下面定义的短期执行令牌；不声称实现第三方 OAuth 自动接入。未来远程开放需要单独实现 MCP HTTP 授权、TLS、受众校验和客户端验收。
- 全量有效持仓的取数、行情、Greeks 与聚合在业务服务中完成，MCP 仅适配；不改变期权台账页面及归类流程。
- Agent 运行环境独立 requirements，不升级现有 Web 运行依赖。候选 Python 3.12、`mcp==2.2.0`、`wecom-aibot-python-sdk==1.0.2`；原 requirements 依赖一并解析锁定。元数据已核对，兼容安装与 import 仍是 T0 实际门槛。
- DeepSeek 沿用 `deepseek-v4-flash` 单模型，Chat Completions，自定义工具调用；首版显式关闭 thinking，避免隐藏推理跨进程持久化要求。不得自动切换模型。未通过真实 smoke 则不能交付。
- 公开检索适配首选 Brave Web Search；只是技术默认，不代表用户已购买或有额度。无已授权 key 时相关链路保持未验收，不购买、不改用抓取搜索结果页冒充 API。

## 2. 身份、账户与执行授权

`Principal` 是服务端创建的只读对象：`user_id:int, channel:web|wecom, conversation_id:int, account_ids:tuple[int,...], execution_id:UUID`；这些字段不出现在模型工具参数中。

每个业务工具执行以下顺序：验证执行令牌 → 查 V2 任务归属及有效期 → 重新读取启用用户与 module_permissions → 要求 `closing_review.agent:view` → 对事实工具要求 `trading.facts:view` → 求宏源 canonical account 与系统有效范围的交集 → 验证引用/筛选 → 取数。知识回答仍要求 Agent 入口权限。不能伪造管理员，也不能通过取消期权权限意外扩大其他权限。

当前 `permissions.get_data_scope_filter` 返回 all。V2 必须在 SQL/事实输入层加账户限制：为 `EffectiveFactFilters` 增加可选 `account_ids`（旧调用默认 None 保持语义），其筛选必须覆盖所有结算/WH 子查询及 count/summary；空 tuple 表示无权且拒绝，不是全部。账户数值不由前端或模型指定。账户唯一映射不存在/重复就停止事实查询并报 scope_unavailable。

Harness 生成 256-bit 随机令牌，DB 仅存 SHA-256、execution_id、expires_at（5 分钟）、revoked_at。HTTP Bearer 只传给 loopback MCP，不进模型、日志或消息。每任务结束撤销；各工具重新检查任务状态、权限及令牌。旧令牌不得读取另一个任务/会话的结果。

首轮只允许 AGENT_V2_PILOT_USERNAME 对应本人使用V2网页/企微；其他用户即使有旧Agent权限也不得自动进入V2。

网页使用现有登录 session；企微使用 bot_id + from.userid + chattype 绑定本地用户，绝不按显示名匹配。绑定采用一次性流程：已登录且有权限的网页用户请求配对码（10 分钟、最多 5 次失败、DB 仅存 hash），在机器人私聊发送该码完成绑定；绑定操作不调用模型。首轮仅 `AGENT_V2_PILOT_USERNAME` 对应的唯一启用用户可配对。群事件首版拒绝，不注册 group grants；未来明确授权群后才实现群共享范围。

## 3. 公共类型（backend/app/trading_agent/contracts.py）

所有模型参数用 Pydantic `extra='forbid'`；拒绝 NaN/Infinity、重复键及超长 JSON。价格与金额输出为 decimal 字符串，内部沿用已有计算函数并在边界规范化；不能偷偷替换已确认浮盈亏公式。

```python
class AsOf(BaseModel):
    mode: Literal['latest', 'settlement_date'] = 'latest'
    date: date | None = None  # settlement_date 必填，latest 禁止

class FactQuery(BaseModel):
    as_of: AsOf = Field(default_factory=AsOf)
    asset_type: Literal['all', 'future', 'option'] = 'all'
    contracts: list[str] = Field(default_factory=list, max_length=50)
    direction: Literal['all', 'buy', 'sell'] = 'all'
    classification: Literal['all', 'unclassified', 'classified'] = 'all'

class MetricValue(BaseModel):
    value: str | None
    unit: str
    status: Literal['complete', 'partial', 'unavailable']
    covered_rows: int
    eligible_rows: int

class ToolEnvelope(BaseModel):
    schema_version: Literal['2.0'] = '2.0'
    status: Literal['complete','partial','waiting_for_data','data_anomaly',
                    'unsupported','needs_clarification','temporarily_unavailable',
                    'result_expired','not_comparable','limit_exceeded']
    result_ref: UUID | None
    snapshot_ref: UUID | None
    data_as_of: datetime | None
    captured_at: datetime
    quote_times: dict[str, str | None]
    calculation_version: str
    payload: dict
    metrics: dict[str, MetricValue]
    evidence_refs: list[str]
    warnings: list[str]
    missing: list[dict]
```

参数形状错误用 MCP invalid params；身份失败为 HTTP 401/403；未知或非本人结果使用相同 not-found 响应。ToolEnvelope 是已处理业务状态，不把缺数当意外 500。证据引用格式 `result_uuid#/metrics/floating_pnl` 或记录路径；服务端解析并验证，不接受任意 URL/文件路径。

`PositionRow` 白名单：row_ref、contract、exchange、product、asset_type、direction、quantity、average_price、fact_status、formation_method、assignment_status、group_key/null、valuation_price、valuation_status、market_time、contract_multiplier、underlying_symbol、underlying_price、expiry_date、iv、各 unit_greeks、display_greeks、position_exposures。字段是否可用、空值含义及单位由 `catalog.py` 提供；当前 group_key 必须来自已确认属性，不能把现有 strategy 名称自动等同用户未来组别。没有历史有效期的归属属性不对历史结果公开为历史事实。

工具只返回必要字段和最多 20 行明细预览；全量行保存在服务端快照。模型通过引用聚合、排序和取下一页，绝不能因预览截断而漏算。

## 4. 工具输入与输出

名称与业务设计一致；新增少量事实汇总工具补齐原八类平仓盈亏能力，避免只有持仓、没有成交/平仓统计。

| 工具 | 严格参数 | payload / 实施说明 |
|---|---|---|
| describe_capabilities | 无业务参数 | 实际支持属性、日期、方法；仅授权范围 |
| query_trade_facts | start_date,end_date,FactQuery 的筛选（无 as_of） | 全量成交引用、count、预览；按事实交易日，不按自然日猜夜盘 |
| query_close_facts | start_date,end_date,contracts | 正式了结/平仓事实及毛盈亏引用；临时未核验盈亏不生成 |
| query_positions | FactQuery | 全量有效持仓 snapshot_ref、count、预览 |
| summarize_positions | result_ref,group_by:list[Field],metrics:list[Metric],order_by?,descending=true | 持仓/盈亏聚合；数据不足返回覆盖率 |
| summarize_facts | result_ref,group_by,metrics,order_by?,descending=true | 成交笔数/数量/手续费、已核验平仓盈亏；不跨事实类型混加 |
| read_result_page | result_ref,page>=1,page_size:20|50|100,fields:list[Field] | 读取同一不可变结果分页，重新核权 |
| compare_results | left_ref,right_ref,metrics | 同账户、单位、估值方式、粒度、归属口径可比才返回差；不同日期允许 |
| get_position_risk | snapshot_ref,include_futures:bool=false | 按标的、币种分组的指标/覆盖率，不跨标的强加 |
| run_scenario | snapshot_ref,method='black76_reprice_v1',shocks:list[Shock]（最多5） | Shock: underlying_symbol,price_change_pct,iv_change_points=0,days_forward=0；明确用户假设或显式默认值，禁止模型自造阈值 |
| explain_evidence | result_ref,metric_path | 最小来源/组成/公式/版本，旧结果不重新取当前行情 |
| search_public | public_query:str<=240,freshness:day|week|month|year|none | 通过出口检查后搜索，最多5个结果与 source_ref |
| read_public | source_ref | 只读取本任务搜索返回并校验的公共链接，最多12000字符正文及实际抓取状态 |

Field 与 Metric 采用 catalog 实际枚举，不接受任意表达式。允许维度：contract/product/exchange/asset_type/direction/trade_date/fact_status/assignment_status，以及未来有依据的 group_key。指标按事实种类列出：count、quantity、fee、realized_close_pnl、floating_pnl；风险必须先调用风险工具。order_by 必须来自本次允许的维度或指标，缺失值固定排最后，禁止任意表达式。聚合最多返回100组，超过时保留全量结果引用，说明截断，按页读；绝不将 top-N 当总量。

## 5. 全量快照与计算

`capture_positions(principal, query, quote_provider) -> ToolEnvelope`：
1. 在 DB 一致性读取事务中取受账户限制的有效持仓和必要来源行版本。Postgres REPEATABLE READ 只读事务；SQLite 同一读事务。不跨网络调用持有 DB 事务。
2. 关闭读事务后，按去重合约一次请求行情，记录每个合约 quote 时间和参数；保存深拷贝，不让后续行情更新改写本轮对象。
3. 复用 `calculate_live_position_floating_pnl` 计算全量浮盈亏。风险复用 `calculate_option_position_valuation` 的敞口计算，传 remaining_open_fee=0 与全量事实口径一致；显式区分展示 Greeks。
4. 保存不可变规范化行、报价、版本和 hash。captured_at 与 data_as_of 分开；data_as_of 取来源/采集真实时间或 null，不拿 now 伪造。
5. 后续 get_position_risk/run_scenario/summarize 只读该 snapshot，不再次刷新行情；新“现在”创建新快照。

限定单快照最多20000行和10MB，超出返回 limit_exceeded 并建议缩小时间/范围；不静默截断。成交/平仓分页在同一读事务内分批物化直到完整或触限，保存服务端结果。纯统计可在固定事务使用正确业务聚合；不能为统计重新拼错结算覆盖规则。

期权指数与期货对冲采用同标的映射；无映射不强行合并。`position_exposures` 延用底层方向×quantity×multiplier：Delta 为货币/标的价格单位，Gamma 为货币/价格单位平方，Vega 是波动率绝对值1的货币变化，Theta 是每年货币变化；展示每日 Theta /360、每波动率点 Vega /100。每项覆盖率独立。

Black76 情景重定价只对该方法和输入确实支持的合约启用：冻结 base F、IV、到期时间、利率、乘数；F'=F*(1+pct/100)，IV'=IV+points/100，T'=T-days/360。比较同模型 base 与 shocked 理论价，乘方向和数量乘数；期货同标的按价格差×方向×数量×乘数。零冲击必须为0。若 IV'<=0 或 T'<=0 或 F'<=0，返回 unsupported，不臆造到期行权处理。结果为模型情景损益变化，不与当前账面浮盈混成一个总盈亏。历史行情不存在时不计算历史 Greeks。实际品种不适用 Black76 时保留持仓并报告未覆盖，不扩大模型方法。

## 6. 存储合同与幂等

复用 closing_review_conversations/messages/tasks 的归属及消息结构，V2 task_kind='v2_user_message'；不修改旧调度和八类入口语义。新增迁移由 `trading_agent/schema.py:migrate_agent_v2_schema(conn)` 显式执行，禁止在每次请求/工具调用 init_db。

新增表（SQLite TEXT JSON；Postgres 同字段 TEXT，便于沿用 db._exec）：
- `agent_v2_runs`：task_id PK/FK closing_review_tasks、user_id、channel、request_hash、state、lease_owner、lease_expires_at、deadline_at、model_calls、tool_calls、search_calls、last_error、delivery_state、created_at、finished_at。
- `agent_v2_results`：id UUID text PK、task_id FK、user_id、conversation_id、kind、parent_ref、snapshot_ref、payload_json、source_hash、schema_version、calculation_version、created_at、expires_at。外键 parent_ref 允许 null；owner 查询必须有 user_id 和 conversation_id。
- `agent_v2_events`：id UUID PK、task_id FK、seq、kind、tool_name、argument_hash、result_ref、duration_seconds、status、error_code、created_at；UNIQUE(task_id,seq)。不存原始推理或全量参数。
- `agent_v2_wecom_bindings`：bot_id、wecom_user_id、user_id、status、created_at、revoked_at；UNIQUE(bot_id,wecom_user_id)，首轮每 bot 仅一个允许试点用户。
- `agent_v2_pair_codes`：code_hash PK、user_id、expires_at、attempts、consumed_at；10分钟TTL，过期不自动绑定。
- `agent_v2_execution_grants`：token_hash PK、task_id、user_id、expires_at、revoked_at。

为 runs(state,created_at)、results(user_id,conversation_id,created_at)、events(task_id,seq) 建索引。Postgres 六张表全部启用 RLS，撤销 PUBLIC/anon/authenticated 的表与序列权限，后端运行角色使用明确授权；不改全库默认授权。沿用服务端 owner 条件，不假设网站用户 id 等于 Supabase auth.uid()。

内容保留90天，任务审计365天沿用旧政策；快照保留90天且超容量拒绝写入，不无限复制。清理只在明确的维护任务执行，删除结果同时让引用返回 result_expired；审计保留脱敏摘要。原对话证据过期后不能声称能精确复现。

入队通过原 claim_user_task 的唯一键；同 request_id 不同正文 hash 返回409。原 closing_review_tasks 的started_at/finished_at、state与V2 runs终态在同一事务更新；V2 message_type沿用answer/error等已有展示种类，structured_payload带schema_version=2.0。V1上下文构建器不读取V2会话，V2 routes只返回其所属引擎会话；用V2 runs或新增会话kind=v2_conversation辨别，不复用原daily_review会话。

同一个企微 msgid 重送对应相同 UUID5(bot_id,msgid)，req_id 是回复关联而非业务幂等键。runs 状态 queued→running→succeeded|partial|failed|cancelled。租约每5秒续期、30秒失效；进程崩溃后 running 标 failed/interrupted，不自动重跑模型或悄悄改用新数据。queued 可恢复。未知发送结果标 delivery_unknown，不能宣称企微已收到或盲目重发。

## 7. Agent 接口与执行循环

`/api/trading-agent-v2` 新路由：

所有业务路由检查AGENT_V2_ENABLED及本人权限；关闭返回404，非本人结果404。现有main.py菜单过滤目前依赖旧开关，需改为旧版或V2任一启用才显示Agent模块，但仍要求原模块权限；不能让V2必须依赖旧版总开关。网页JS以服务端capabilities选择API，不由前端指定模型、账户或工具。
- GET /capabilities：当前用户可用能力、配置状态、版本；无密钥值。
- POST /conversations：创建 web V2 会话；GET /conversations、GET /conversations/{id}/messages 仅本人 web。
- POST /conversations/{id}/messages：`{client_request_id:UUID,content:1..2000 chars}`，返回202和task_ref；重复已完成返回原结果。
- GET /tasks/{id}：状态、最小 answer、result refs；非本人404。
- POST /wecom/pair-code：已登录试点用户申请码；只向该用户显示，响应禁缓存、日志不记录。

企微会话 key=bot_id+from.userid+single；网页不读取其短期历史。“新建对话”确切命令在企微创建新会话，旧消息保留政策不变。

`async run_task(task_id:int, deps:RuntimeDeps) -> AnswerDraft`；`RuntimeDeps` 包含 store、model（next_turn）、mcp（call_tool）、clock。Runtime 对工具循环不硬编码 Greeks/天气关键词。首先 describe_capabilities，模型返回 function tool_calls 或最终 JSON；参数后端验证，再按顺序执行。每任务至多8次工具调用（包括 search/read/分页）、2次search、6次模型请求（含重试/修复），总90秒；单次模型最长15秒。预算检查在每次网络请求前后，超限已完成的证据可形成部分答案，不强行启动额外模型总结。

工具批量超过剩余预算时整批不启动，回送明确工具错误或用固定模板结束。模型无调用但涉及账户事实时必须有相应授权证据引用，否则允许一次修复（计入模型预算），仍不合格降级为确定性事实或明确未完成。429/5xx/连接超时最多额外一次请求；认证错误不重试。公开检索5秒、正文读取8秒、行情复用原超时均受总期限约束；取消不能令后台结果覆盖结束消息。

最终 `AnswerDraft`：`status, paragraphs:[{kind:fact|scenario|inference|knowledge,text,evidence_refs}], fact_refs:[], missing:[], clarification:null|string`。业务数字用 `{{fact:result_uuid#/metrics/x}}` 占位引用，renderer 再填充；知识数字可引用公共来源。禁止模型自算未注册业务数值。程序检查引用存在、权限、时点、单位与数值占位；推论有效性由规则与 Eval 检查，不宣称自动证明所有自然语言。

上下文最多最近6轮，工具结果每次最多16000字符，整次发送上下文最多48000字符；先按请求字段投影再取预览，超限以引用代替，不把裁剪后数据用于“全量”结论。原始 reasoning_content 不存储、不回显；首版 thinking disabled。网页与企微最终只显示必要口径，细节可追问。

## 8. 搜索数据出口与公共证据

`research.py` 使用独立 PublicQuery 对象和 validate_public_query(query, private_context)；禁止直接把完整用户问题、工具结果、模型分析草稿送搜索。先做公开子问题抽取，再检查账户/客户/订单/地址/内部标识/私有数值及编码变体；不确定时拒绝搜索并澄清公开检索词，不静默放行。该控制不保证数学上的零泄露，须红队案例与运行审计共同验收。

允许公开日期和外部指标方法词；不能把仅有关键词白名单做成固定问题目录。read_public 只接 source_ref，不接受模型任意 URL；实际读取仅 HTTP(S)、禁止用户信息及内网/loopback/link-local地址、每次DNS与重定向验证（最多3跳），不携带 cookies/授权头。防止DNS重绑定必须连接已校验地址并维持TLS主机名校验，不靠一次DNS预检查；无法安全实现时只返回搜索摘要并标 snippet_only。正文最多1MB、12000字符，非HTML/text首版不下载，状态明确，不假装读过PDF。

结果记录标题、URL、抓取时间、发布/事件日期（未知为null）、正文短摘录及 source_ref。资料中的指令一律作不可信文本。最终引用只允许已登记来源。知识回答对时效性事实需搜索；无搜索配置则明确无法核实最新情况。

## 9. 企业微信与部署配置

首选企业微信团队 Python SDK 的 WSClient/WSClientOptions，监听 message.text，用 reply_stream(frame,stream_id,text,finish)；不使用仅推送的群 webhook。只处理文本，文件/图片首版提示不支持且不下载。

SDK 回调读取 body.msgid、body.from.userid、body.chattype、body.chatid（有时缺省），缺必需身份或非 single 则不进业务队列；在真实沙盒首条事件核验形状，只记录字段名不留原始事件。认证连接失败只记录错误类别。收件后先回复无业务数据的处理中消息，最后一次发送经校验答案；不直播模型草稿、思维链或工具结果。

主 worker 获得 bot_id 对应数据库租约后才连接（复用 runs 之外的 Postgres advisory lock，不锁业务表；SQLite 本地进程锁）；失锁立即断连。关闭时停止接单并有限等待任务；重连遵循 SDK，不能无限重发业务答案。首版群 disabled；向用户已发起问题的私聊回复不扩大为主动订阅。

环境名：AGENT_V2_ENABLED=false、AGENT_V2_WECOM_ENABLED=false、AGENT_V2_PILOT_USERNAME、AGENT_V2_ENV=staging、AGENT_V2_MCP_PORT=8766、DEEPSEEK_API_BASE、DEEPSEEK_MODEL、DEEPSEEK_API_KEY、WECOM_BOT_ID、WECOM_BOT_SECRET、BRAVE_SEARCH_API_KEY。配置值从受控环境加载，不读取输出密钥。

Web 只需 V2 路由 flag，worker 命令 `python -m backend.app.trading_agent.worker`。默认在已有获准常驻主机运行并只连 Staging；Render 新 worker 若产生新增付费，须用户批准，计划不擅自新增付费服务。不修改现有 render.yaml 生产服务定义。休眠或临时本机运行不能验收为持续可用服务。

## 10. 外部合同证据（2026-09-09 查阅）

以下为已核对文档，不代表账号/环境可用。实施 T0 记录实际解析的依赖锁和协议协商结果；本次仅读取元数据，未安装SDK或请求业务API。

- [DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)：使用 Chat Completions 的工具声明和结果回传；首版不依赖 beta strict 模式。
- [DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)：显式关闭 thinking；若未来开启，须处理官方要求的 reasoning_content 回传，另做评估。
- [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/) 与 [ASGI lifecycle](https://py.sdk.modelcontextprotocol.io/run/asgi/)：采用官方2.x接口，避免混用1.x FastMCP示例。
- [MCP tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools) 与 [HTTP 授权](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)：工具Schema与授权边界；锁定SDK实际支持的协商版本，不假定不同协议版本字段一致。
- [企业微信团队 Python SDK](https://github.com/WecomTeam/wecom-aibot-python-sdk) 与 [官方示例](https://github.com/WecomTeam/wecom-aibot-python-sdk/blob/master/examples/basic.py)：长连接文本收发。PyPI候选1.0.2，仓库pyproject显示1.0.1，安装后核验包API，不能以仓库示例替代发布包兼容检查。
- [Brave Web Search](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started)：GET /res/v1/web/search，X-Subscription-Token；首版最多5结果，不使用其回答生成服务。
- [Supabase Data API 安全](https://supabase.com/docs/guides/api/securing-your-api)：RLS与grants分别检查。changelog.md在web读取时失败，本轮未做平台变更；迁移实施前重新核验相关变更。

## 11. 实现接口索引与部署前检查

下列为新增模块接口名，实施中保持一致；不是已经存在的实现。

| 类型/接口 | 定义位置 | 明确形状或用途 |
|---|---|---|
| Principal | contracts.py | 第2节只读身份对象 |
| StoredResult | contracts.py | ref:UUID、envelope:ToolEnvelope、rows:list[dict]、owner_user_id:int、conversation_id:int、expires_at:datetime |
| Shock | contracts.py | underlying_symbol:str、price_change_pct:finite float、iv_change_points:finite float=0、days_forward:int>=0 |
| PublicQuery | contracts.py | text:str、freshness枚举、approved:bool；仅出口检查器可构造approved=True |
| ModelTurn | model.py | tool_calls:list[{id,name,arguments}]、content:str、usage:dict、finish_reason:str |
| AnswerDraft | contracts.py | 第7节结构，status使用ToolEnvelope状态枚举；clarification仅一条 |
| RuntimeDeps | harness.py | store/model/mcp/clock依赖注入对象，真实运行与测试共用 |
| RequestConflict | store.py | 同请求编号正文不同，对应HTTP409 |
| InvalidEvidence | answer.py | 无效/越权引用，触发有限修复或降级 |
| QueryRejected | research.py | 公开查询可能含私有数据；抛出前无外部请求 |

部署检查必须确认worker的常驻主机和实际Staging DB映射；旧render.yaml只定义生产web，不能复制其环境配置创建worker。AGENT_V2_ENV仅是运行声明，不能单独证明DB目标正确。不同环境bot配置及执行令牌不得复用。

迁移验证覆盖表、索引、FK及幂等；Postgres使用下面形式逐表执行，表名只能取迁移内固定六表列表：

```sql
ALTER TABLE public.agent_v2_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.agent_v2_runs FROM PUBLIC, anon, authenticated;
```

其他五表及适用序列同一事务完成，后端DB角色权限显式核验；SQLite不执行Postgres权限语句。部署前备份/恢复演练不通过则不apply。回滚仅关闭服务入口和worker并撤令牌，不删除附表或业务事实。
