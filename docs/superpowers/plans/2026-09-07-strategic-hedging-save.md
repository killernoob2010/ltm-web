# 新增战略套保保存与录入规则修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Only one user-authorized Luna Max executor; no nested agents. Primary agent owns requirements and acceptance.

**Goal:** 用户可以按已确认口径新增战略套保，保存后回读所填内容，错误时不丢失输入。

**Architecture:** 修复既有 `/api/spot-ledger/strategic-hedging`，沿用现有表和权限。仅改战略套保弹窗、保存链路及其专用回读展示，不把战略字段强行映射成现货字段，不改数据库结构。

**Tech Stack:** FastAPI/Pydantic、PostgreSQL（Staging）、SQLite（本地测试）、原生 JavaScript/HTML、pytest、Node test。

**Spec:** 本文“已确认业务规则”是本任务冻结规格，来源为用户当前对话。用户已要求先出方案再由 Luna Max 执行。

## Global Constraints

- 工作区 `/Users/wangjingze/.codex/worktrees/trade-ledger-luna.hpuI` 已是隔离 worktree；当前分支 `codex/spot-ledger-source-display-fix-20260905`，fresh fetch 后 HEAD 与 origin/staging 均为 `8ad9b3f2392eebb6d0777626ec88f8cd523780be`。
- 仅 Staging：Render `ltm-web-staging` / `https://ltm-web-staging.onrender.com` / Supabase `hzpivfwtdiqnfxbcxgrm`。禁止 main、Production、生产数据、源贸易系统写入和真实交易操作。
- 不修改 CP块、销售类型映射、Excel、现货业务语义、账户身份匹配、权限体系、同步任务、全开全平规则或盈亏算法。不添加依赖或数据库结构。
- 已有脏文件 `版本更新记录.md` 属于此前销售类型工作，保留，不覆盖或混入执行者提交；由主 Agent 后续维护发布记录。
- 账户默认“宏源”，允许任意非空名称；合约／品种自由输入；数量单位固定“吨”。不得重新限制账户为宏源/财达枚举，不做手吨转换。
- 组别固定现有七组：大客户组、东北组、山东组、黄骅组、天津组、唐山组、南方组。
- 方向显示“买入开仓（多）”“卖出开仓（空）”，存储沿用“多”“空”。价格币种/单位维持自由输入，默认“元/吨”，不限制所有品种只能人民币。
- 时间展示精度到秒。真正空值存 NULL，显示“—”；不得把缺失价格变成 0。
- 一个执行者修改代码，主 Agent 独立复核。最多一次普通验收返修；出现新业务问题、权限/结构变化、同一问题二次失败，执行者停止并汇报，不自行扩范围。

## 已确认业务规则与验收映射

| ID | 业务要求 | 实现位置 | 自动化断言 | 真实验收 |
|---|---|---|---|---|
| SH-1 | 合法记录可以保存 | backend/app/spot_ledger.py | 所有不适用数值字段为 None；只开仓/全平成功且回读一致 | Staging 创建、刷新后回读 |
| SH-2 | 已确认的输入方式 | frontend/index.html、frontend/spot_ledger.js、StrategicHedgingIn | 七组、方向、吨校验；默认宏源可改；合约自由文本 | 弹窗选项与自定义账户保存 |
| SH-3 | 错误不丢输入、不重复提交 | frontend/spot_ledger.js | reportValidity、trim、空价格不为0、请求中只发一次、finally恢复、失败保留 | 缺项/不完整平仓/部分平仓反馈 |
| SH-4 | 保存结果可见且可核对 | frontend/spot_ledger.js 及最小战略专用详情 | 成功刷新列表/计数；失败不reset；回读展示战略字段而非空现货字段 | 打开所保存记录核对字段、页面刷新后仍在 |

## 评估与授权

- D2/T2/R2/C2，测试宽度 local_integration：同一模块内既有录入功能的保存、校验、回读。不涉及跨模块结果，故不升 D3/T3；无生产/结构/权限变化，不升 R3。用户明确授权 Luna Max 执行，主 Agent 审核，不另派评审 Agent。
- 已有根因证据：create_strategic_hedging 对全部 A:AY 初始化 `""`，包含 double precision 列 L/M/N 等。Staging 只读 `SELECT ''::double precision` 复现 22P02。本地 SQLite 原用例通过，不能代表 PostgreSQL 兼容。
- 回滚点为本轮前 `8ad9b3f`。代码回滚须新提交，仅 Staging。测试记录只能按本轮新增 ID 清理，绝不按模糊备注批量删除。

## Task 1: 一次性完成保存、输入校验与回读闭环

**Files:**
- Modify: `backend/app/spot_ledger.py`（StrategicHedgingIn、create_strategic_hedging；确有必要时添加最小战略列表投影，不更改现货投影语义）
- Modify: `frontend/index.html`（战略弹窗、spot_ledger.js 缓存版本）
- Modify: `frontend/spot_ledger.js`（战略表单、保存状态、最小战略详情分支）
- Modify only if needed: `frontend/styles.css`（仅战略专用样式，优先复用）
- Tests: `tests/test_spot_ledger_api.py`、`tests/spot_ledger_frontend.test.mjs`；允许新增一个同目录战略表单行为测试文件，不引入依赖。

**Interfaces:** 保持 POST 路径及 `{record: ...}` 响应；后续 GET records/{id} 回读同一记录。复用现有 `loadView`、`loadCounts`、`openRecord`。

- [ ] 1. 读项目规则、确认工作区及当前 diff；运行既有基线 `env -u DATABASE_URL python3 -m pytest tests/test_spot_ledger_api.py -q` 和 `node --test tests/spot_ledger_frontend.test.mjs`。失败若与本轮无关报告，不擅自修复。
- [ ] 2. 先加失败回归：通过 monkeypatch 捕获 `_execute_insert` 的参数，在原 SQLite 成功用例上明确断言数值空列都为 None，而不是依赖 SQLite 类型宽松行为。示例核心断言如下，完整测试沿用 ledger_context：

```python
original_insert = ledger._execute_insert
captured = {}
def recording_insert(cur, sql, params):
    captured.update(sql=sql, params=params)
    return original_insert(cur, sql, params)
monkeypatch.setattr(ledger, "_execute_insert", recording_insert)
# 调用既有合法 StrategicHedgingIn；从 INSERT 列名与 captured params 配对。
assert all(insert_values[code] is None for code in ledger.NUMERIC_FIELDS)
```

- [ ] 3. 为默认/自定义账户、空白字符串、非法组别、非法方向、非吨单位、缺失开仓价格、不完整平仓、部分平仓加行为测试。后端要保证 whitespace-only 不通过。保留原有完整平仓检查；不新增用户未确认的价格正负/合约枚举限制。
- [ ] 4. 最小修复存储初始化：

```python
fields = {code: None if code in NUMERIC_FIELDS else "" for code in FIELD_CODES}
fields.update({"A": "战略套保", "E": payload.group_name, "AP": payload.group_name})
```

  在 Pydantic 模型使用现有版本支持的字段验证方式 trim 文本，验证 group_name 属于 SHANGHAI_GROUPS，open_direction 属于多/空，quantity_unit 等于吨。账户与合约不做枚举。复用 `_text`，不改变公共解析函数。

- [ ] 5. 修改输入控件：组别 `<select required>` 带空白“请选择”；账户 `<input name="account" value="宏源" required>`；合约继续 input；方向 select 值多/空；数量单位 readonly input value=吨。价格币种/单位不变。
- [ ] 6. 保存函数执行次序：阻止默认动作 → 请求中则return → `form.reportValidity()` → trim与业务校验 → 组装 payload → 设置saving并禁用按钮 → POST → 成功提示与回读入口 → 刷新列表/计数 → finally恢复按钮。缺失开仓价格绝不能经过 `Number("")` 变0。失败保持表单值；分开处理“创建成功但列表刷新失败”，不得提示保存失败诱导重复创建。成功时 reset 恢复宏源和吨。

```javascript
if (strategySaving) return;
if (!form.reportValidity()) return;
if (!String(data.open_price ?? "").trim()) {
  status.textContent = "请填写开仓价格";
  return;
}
```

- [ ] 7. 回读展示必须能核对组别、账户、合约、方向、开仓时间/数量/吨/价格/币种、平仓字段、状态和备注。现有普通详情只渲染 A:AY，不能直接把它当作战略记录验收；增加最小 `record_source_type === "战略套保"` 展示分支，保留现货详情原分支原样。不添加战略编辑/删除功能，不把战略字段复制进现货价格字段。
- [ ] 8. Node 行为测试覆盖：默认值、trim和缺失价格、同一pending请求重复点击只提交一次、失败不清空、成功后可回读且refresh失败不误报创建失败。若仓库无 DOM harness，使用 Node vm 与最小 DOM/请求 stub，不只做源码字符串存在断言，不安装新库。
- [ ] 9. 运行定向 Python/Node 回归、`python3 -m compileall -q backend/app/spot_ledger.py`、`node --check frontend/spot_ledger.js`、`git diff --check`；自查无范围外修改。提交仅本轮实现、测试、计划文件；不要提交原有版本记录脏改动。
- [ ] 10. 提交后先向主 Agent 汇报 commit、测试命令/数量、变更文件、已知问题。主 Agent 审阅后才推进 Staging 部署与集中真实验收。执行者不得自行跳过此检查点。

## 主 Agent 负责的最终验收与发布边界

- 使用原有已保存登录会话/预填凭据，不读取输出密码，不创建新的凭据机制。若登录受阻，明确报告验收未完成，不能用 DB 成功替代。
- Staging 浏览器检查准确URL、版本资源、页面控制台；录入带唯一测试标记的2条记录：默认宏源只开仓、自定义账户财达全开全平。核对所有字段与吨口径，再刷新/重新打开核对持久化。
- 缺失必填、空白账户、缺失价格、不完整平仓、部分平仓必须失败且输入不丢；请求中重复点击仅创建一次。
- 回读 Staging PostgreSQL 记录确认非适用数值列为 NULL；清理仅本轮测试记录并回读验证数量，不改变历史记录。
- 页面冒烟检查现货台账筛选/分页和一条已有详情保持正常。CP块异常保持原样，不作本轮修复。
- 发布记录仅在真实部署后更新，区分自动化通过、真实验收完成与阻塞。正式版待用户另行确认。

## 执行记录

- 2026-09-07：主 Agent 已核对 origin/staging 基线与根因，用户已确认规则并明确要求 Luna Max 执行。
- 当前阶段：方案已锁定，待 Luna Max 执行 Task 1。
- Next action：由单个 Luna Max 执行者完成测试先行的本地实现并返回主 Agent 评审。
