# Trading Agent V2 C0 设计收口

日期：2026-09-09。状态：C0 只读设计完成，未执行 C1 真实模型或云端验收。

本记录只把现有实现能证明的语义和下一批最小差异固化下来。它不把本地合成测试、恢复库快照或旧的真实模型联调误写成当前业务数据或线上可用性。

## 1. 核对范围与结论

核对了 `backend/app/trading_effective_facts.py`、`backend/app/trading_agent/facts.py`、`backend/app/trading_agent/research.py`、`backend/app/trading_agent/answer.py`、`backend/app/trading_agent/store.py`，以及 `evals/trading_agent_v2/cases.jsonl` 和 `holdout.jsonl`。

结论有三项：

1. 有效持仓结果可能由结算快照、WH6 完整快照和结算基线加临时成交推导共同形成。系统有来源日期和采集新鲜度，但没有能代表混合结果的统一、经核验的 `data_as_of`。
2. 公开搜索已经生成带 `source_ref` 的不可变结果，正文读取也保留了父结果关系；最终答案仍会跳过 URL 校验，且结果加载当前按用户和会话范围，尚未强制绑定到本次任务。跨段落推论没有可以证明所有自然语言语义的确定性检查。
3. 44 个 regression 和 12 个 holdout 题目都有定义字段，但 `fixture` 只是标签，没有数据快照、哈希、预期数值或可回放工具输入。它们现在只能做定义检查，不能直接作为真实模型行为验收题。

## 2. 来源时点合同

### 2.1 现有字段的真实含义

| 字段 | 当前含义 | 可否直接称为“数据截至” |
|---|---|---|
| `captured_at` | Agent 读取并保存结果的系统时间，精度到秒 | 否 |
| `data_as_of` | `ToolEnvelope` 的可选来源水位；持仓路径当前保持 `null` | 只有来源适配器明确提供时才可以 |
| `trade_date` / `snapshot_date` / `position_snapshot_date` | 事实或持仓快照的业务观察日 | 只能称日期，不能补造当天零点 |
| WH6 `snapshot_timestamp` | 某个 WH6 完整快照的采集时间 | 仅适用于该快照覆盖的行 |
| `collector_last_seen_at` | 采集器或快照最后被观察到的时间 | 只能说明采集新鲜度，不证明每条事实完整 |
| `age_seconds` | 由采集器最后观察时间计算的新鲜度秒数 | 不是事实水位 |
| `as_of_time` | 当前 effective-facts 代码中由查询时钟填入的时间 | 不能当来源水位；C1 必须改名或明确标注为查询时间 |

`query_effective_positions()` 在最新和历史路径都把查询时钟放入 `as_of_time`；历史路径还只提供结算快照。`capture_positions()` 随后把这份结果保存为 `ToolEnvelope`，但没有把查询时钟写入 `data_as_of`。这正是应该保持保守的边界：查询时间和数据截至时间不能混用。

### 2.2 C1 采用的来源分类

后续来源适配器应为每一组行提供以下最小观察记录，不改持仓计算公式：

```text
source_kind: settlement | wh6 | derived | synthetic | unknown
source_label: 人类可读来源名
coverage_date_start / coverage_date_end: 已核验的业务日期范围
observed_at: 只有来源实际提供时间时才填，精度保留到秒
freshness_status: current | stale | conflict | unavailable
fact_status: settlement_confirmed | provisional | derived | unavailable
row_count: 该来源实际覆盖行数
environment: live | restored_backup | synthetic | unknown
```

`data_as_of` 只有在所有纳入结果的行具有同一来源语义、同一精度且没有冲突时才可填入。只要结果混有结算日期、WH6 时间和推导成交，整体 `data_as_of` 保持 `null`，在 payload 中返回按来源的覆盖信息。备份恢复时间、数据库写入时间和查询时间都不能替代事实时间；也不能用全库最大日期推断完整持仓截至时间。

具体判定：

- 无冲突的 WH6 完整快照可把其 `snapshot_timestamp` 作为该来源的观察时间，但多账户混合、结算基线混入或推导成交存在时，不提升为全局 `data_as_of`。
- 结算基线只提供 `position_snapshot_date` 或交易日期，精度是“日”，不能填入一个伪造的午夜时间；它应作为 `coverage_date` 返回。
- 结算基线加临时成交的推导结果没有单一观测时刻，应标为 `derived`，保留各输入来源的日期和新鲜度。
- 当前运行时没有可靠的“这是恢复备份”的标记。C1 不得从主机时钟或备份文件修改时间猜测 `restored_backup`；如要验收恢复数据，必须由测试/运行入口显式注入环境标记。

## 3. 跨段落证据与公开来源

### 3.1 现在能确定检查什么

- `fact` 和 `scenario` 段落中的业务数字必须有内部指标占位符；占位符、顶层 `fact_refs` 和段落 `evidence_refs` 已共用受限的指标路径校验。
- `inference` 和 `knowledge` 段落可以包含通用知识数字，当前没有规则声称能够判断所有自然语言数字是否来自内部事实。这项限制必须保留，不能用关键词黑名单包装成完整语义校验。
- `search_public()` 会在本任务中保存 `uuid#/sources/<index>`，`read_public()` 只接受该来源引用，并将正文结果以父结果关系保存；正文还带有 `untrusted_content=true`，外部指令不构成工具授权。
- `render_answer()` 对 `http://` 和 `https://` 引用直接跳过。因此当前“有 URL”不等于“URL 是本任务搜索到、已通过 SSRF 检查且被读取过的来源”。

### 3.2 C1 的来源引用协议

保持现有 `AnswerDraft` 顶层字段兼容，但新增严格的引用解析分支：

```text
public_search_ref = research_uuid#/sources/<index>
public_read_ref   = public_read_uuid#/payload/text
internal_ref      = result_uuid#/metrics/<name>
                  | result_uuid#/payload/groups/<index>/metrics/<name>
```

- 最终答案禁止直接使用 URL；`public_search_ref` 只能表示搜索摘要，`public_read_ref` 才能表示正文依据。
- 解析器必须核对结果类型、来源索引、父结果、状态、所有权、有效期和当前任务绑定；URL 仅作为存储在已登记结果中的来源属性，不能由模型直接提交。
- 当前 `store.load_result()` 的查询条件是用户和会话，未把 `task_id` 作为强制条件。C1 需要在结果解析边界补上“当前任务或明确父结果链”的约束；这可能触及 `store.py`，必须单独做最小变更和迁移/回归评估，不能在 A/B 范围内绕过。
- `fact`/`scenario` 的内部数字仍按现有严格规则；`inference` 若引用账户事实，必须带可解析的内部引用。跨多个结果的组合只有在用例明确允许的情况下通过；比较两个结果和同一持仓快照不能使用同一条泛化规则混为一谈。
- `knowledge` 不要求绑定账户事实；如依赖公开资料，引用必须是 `public_search_ref` 或 `public_read_ref`。不添加“所有数字都必须引用”的粗暴规则。

能够自动证明的是引用存在、权限正确、结果未过期、指标可用、公开来源已登记和来源类型匹配；推论是否完整、因果是否成立、是否遗漏假设仍由固定 Eval 判据和人工复核共同确认。

## 4. 44+12 题的真实 Eval 前置合同

### 4.1 当前题库不是可回放 fixture

`cases.jsonl` 有 44 行、37 个不同的 fixture 标签；`holdout.jsonl` 有 12 行、12 个不同标签。每行包含问题、允许工具、必需证据、禁止行为和 oracle，但仓库中没有与这些标签对应的输入数据、来源哈希、工具响应或数值预期。当前脚本只应报告 `definition_pass`，不能报告行为得分或 release 通过。

### 4.2 C1 可执行用例格式

每个真正运行的用例必须补齐：

```text
case_id / split / question
fixture_ref / fixture_sha256 / data_environment
account_scope / as_of_request / expected_source_semantics
allowed_tools / expected_tool_calls (set or ordered constraints)
required_evidence / forbidden_behaviors
oracle.status / exact_metrics_or_ranges / coverage_requirements
failure_classification: fixture | implementation | model | oracle
```

`fixture_ref` 必须能解析到脱敏合成数据或受保护 Staging/恢复快照的固定收据；真实账户标识、连接值和密钥不进入仓库、Prompt 或日志。工具调用必须由 Harness 记录并按白名单核对，不能只看模型最后一句话。数值、覆盖率、状态、引用可用性和时点精度可以做确定性判据；自然语言解释、推论假设和措辞质量需另列人工/规则判据。

### 4.3 留出集冻结规则

- holdout 的 fixture、哈希和 oracle 在运行前冻结；看到模型失败后不能直接改 oracle 迎合结果。
- 若发现数据与题意不一致，先把失败归为 `fixture` 或 `oracle`，建立新版本并记录理由，再重跑；不得静默改原题。
- hard gate 包括工具越权、私有数据外发、未引用业务数字、错误时点、跨标的相加、补零和错误状态；任何 hard gate 失败都不是“软分可抵消”。
- 真实模型通过只在固定数据、固定工具白名单和固定 oracle 下成立；没有完成 API 调用和证据留存时保持 `not_run`，不能从 44/44 定义通过推导。

## 5. 交给 C1/Luna 的最小实施批次

C0 本身先完成设计收口；随后在同一非生产工作树执行了前两项最小本地实现。剩余项目按以下顺序拆分，任何一项遇到新架构决定就停在该项：

1. **C1-PROVENANCE（已完成本地最小实现）**：在 effective facts 到 facts 的边界增加来源观察结构，保留 `data_as_of=null` 的保守行为；WH6 记录采集时间，结算记录业务观察日，推导结果标为 `derived`。没有改数量、去重、FIFO、估值或风险公式。
2. **C1-PUBLIC-REF（已完成本地最小实现）**：实现 `public_search_ref`/`public_read_ref` 解析，禁止最终答案直挂 URL；结果父链和当前任务绑定在答案证据边界校验。搜索摘要与正文引用不能互相冒充。
3. **C1-EVAL-FIXTURES**：只为第一批最小矩阵建立脱敏、可回放 fixture 和冻结 oracle。建议先覆盖：持仓数量三种问法、无行情浮盈、历史无同期行情、事实加推论、受控坏草稿修复；不把旧备份里的 20/4344 当长期常量。
4. **C1-REAL-SMOKE**：在固定 fixture 和 Staging 映射核对完成后，再决定是否申请新的付费 API 调用和真实网页验收；记录首次通过、修复通过和最终失败，真实模型未触发修复时不能声称纠错能力已验证。

C1 的停止条件：需要读取秘密、写 Production/正式数据、改变账户权限或交易能力、改变底层事实算法、添加未批准依赖、触发新的付费额度，或无法确定来源时点。此时保留已完成的设计和测试证据，交回主 Agent 决策。

本地实现提交：`0553559` 包含来源观察、公开引用解析、结果父引用和任务绑定回归；尚未推送或部署。具体测试结果记录在执行记录中。

## 6. C0 验收边界

- 已完成：三项 C0 设计收口、现有字段语义核对、公开来源引用差异、44+12 题库可执行性审计，以及 C1 最小实施顺序。
- 未执行：真实 DeepSeek、Brave、企微、网页登录后的 Agent 问答、实时行情/Greeks、持续运行和 Production。
- 不得宣称：当前结果有统一实时截至时间；URL 已经是可审计答案证据；44+12 题已经完成真实行为验收；A/B 本地 121 项通过等于业务可用。
