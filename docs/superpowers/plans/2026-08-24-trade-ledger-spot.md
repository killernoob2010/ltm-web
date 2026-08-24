# 贸易台账管理—现货业务台账管理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有轻量化交易管理系统中交付本地可验收的现货业务台账模块，覆盖 A:AY 51 字段、fixture 同步链路、人工补录、待办/异常、历史迁移、战略套保和 Excel 导出。

**Architecture:** 保留现有 FastAPI、SQLite/PostgreSQL 兼容层、模块权限和单页 Web shell。新增专用 `spot_ledger` 业务模块负责字段、规则、数据库、API、迁移和导出；新增 `spot_ledger_sync` 负责标准化 source contract、profile-driven HTTP adapter、fixture、完整扫描门禁和小时调度；前端通过独立 `spot_ledger.js` 和页面区块接入现有导航。

**Tech Stack:** Python 3、FastAPI、SQLite/PostgreSQL-compatible SQL、openpyxl、requests、vanilla JavaScript、CSS、pytest、Node test runner。

**Spec:** `docs/superpowers/specs/2026-08-24-trade-ledger-spot-design.md`

## Global Constraints

- 只在当前 feature worktree 和本地 SQLite/明确 fixture 中操作；不改 main、Production、正式数据或正式环境变量。
- 现货只接收固定 7 个销售组、期现货为“现货”、合同状态为“生效”的销售合同商品明细。
- 销售日期取签订日期；结案且结算数量有效时采购量/销售量取结算数量，否则取合同数量；B05/B09 映射为“船货-落地”。
- E 为量归属组、AP 为业务毛利归属组；E 与 AP 不同只计算 AQ，不生成同步异常。
- 只有页数、总数、明细 ID 完整扫描成功后才允许软隐藏未出现记录；失败时保留旧可见记录。
- 系统字段随源刷新，人工字段保留；系统优先补录字段仅在源有有效值时覆盖人工值。
- 人工字段编辑要求敏感操作权限；不记录字段修改审计；同步 run/错误摘要必须保留。
- 每天 Asia/Shanghai 09:00—18:00 每小时调度；无实时同步和手动立即同步按钮。
- 真实报表 POST 不猜请求体、响应字段、认证或网页协议；无 profile/认证时明确 `auth_unavailable`，fixture 必须标注非真实源。
- 所有用户可见时间仅到秒。

## Task 1: 建立字段契约、数据库模型和纯规则服务

**Files:**

- Create: `backend/app/spot_ledger.py`
- Modify: `backend/app/db.py`
- Test: `tests/test_spot_ledger.py`

**Interfaces:**

- `FIELD_DEFINITIONS`: 51 个按 A:AY 顺序的字段定义，至少包含 `code`、`name`、`control`、`required_rule`、`source_rule`、`exportable`。
- `normalize_sales_contract_record(raw, mappings=None) -> dict`：返回标准化字段和错误摘要。
- `calculate_derived_fields(record) -> dict`：返回 A、B、AQ:AY 等派生字段。
- `missing_required_fields(record) -> list[str]`：按适用性返回字段名称，不把非必填空值计入。
- `initialize_schema(conn) -> None`：幂等创建 `spot_ledger_records` 和 `spot_ledger_sync_runs`。

- [ ] **Step 1: 写失败测试，锁定字段数量和关键规则。**

在 `tests/test_spot_ledger.py` 中先加入以下行为断言：

```python
def test_field_contract_contains_exactly_a_to_ay():
    from backend.app.spot_ledger import FIELD_DEFINITIONS
    assert [item["code"] for item in FIELD_DEFINITIONS] == [
        "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z", "AA", "AB", "AC", "AD", "AE", "AF", "AG", "AH", "AI", "AJ", "AK", "AL", "AM", "AN", "AO", "AP", "AQ", "AR", "AS", "AT", "AU", "AV", "AW", "AX", "AY",
    ]


def test_contract_mapping_uses_signed_date_and_settlement_after_close():
    from backend.app.spot_ledger import normalize_sales_contract_record
    record = normalize_sales_contract_record({
        "detail_id": "D-1", "spot_type": "现货", "contract_status": "生效",
        "quantity_group": "山东组", "profit_group": "唐山组",
        "contract_number": "C-1", "product_name": "铁矿石", "signed_date": "2026-08-24",
        "contract_quantity": 120, "settlement_quantity": 100, "is_closed": True,
        "business_category_code": "B09", "demander": "客户A",
    })
    assert record["U"] == "2026-08-24"
    assert record["L"] == record["X"] == 100
    assert record["D"] == "船货-落地"
    assert record["E"] == "山东组" and record["AP"] == "唐山组"
    assert record["AQ"] == "是"


def test_missing_required_fields_respects_land_contract_condition():
    from backend.app.spot_ledger import missing_required_fields
    record = {"D": "船货-落地", "C": "自主建仓", "K": "船A", "N": 0, "O": 0, "Y": 0, "P": "是", "long_contract_object": ""}
    assert "长协对象" in missing_required_fields(record)
    record["P"] = "否"
    assert "长协对象" not in missing_required_fields(record)
```

- [ ] **Step 2: 运行失败测试，确认失败来自缺少模块/契约。**

运行：

```bash
pytest -q tests/test_spot_ledger.py -k 'field_contract or signed_date or missing_required'
```

预期：FAIL，原因是 `backend.app.spot_ledger` 和字段契约尚未存在，而不是测试语法错误。

- [ ] **Step 3: 实现字段定义、标准化和派生计算。**

在 `spot_ledger.py` 中定义 A:AY 的单一字段契约、固定组和销售类型映射；将数字前缀从 AG 去除，将 K 占位符转为空，将 U/G 日期格式化到日，将 L/X 按结案/结算量规则计算。实现 P/长协对象、AQ 组别差异和 AS/AT 月份计算。对未知销售类型、公司/供应商映射缺失和商品分类缺失返回非静默异常摘要，但保留原始值。

- [ ] **Step 4: 为规则服务补齐测试并运行绿色测试。**

补充未知类型、0 值有效、负数校验、非必填空白、AG 前缀、K 占位符、B05/B09 和字段控制类型测试；运行：

```bash
pytest -q tests/test_spot_ledger.py -k 'field_contract or mapping or required or derived'
```

预期：所有定向测试通过。

- [ ] **Step 5: 实现 schema 和权限登记测试。**

在 `spot_ledger.py` 中使用现有 `db._exec` 和 SQLite/PostgreSQL 兼容 SQL 创建记录/run 表；在 `db.py` 注册 `("贸易台账管理", "spot_ledger", "现货业务台账管理")`，初始化时调用 schema 和缺失权限补齐。将 `spot_ledger` 加入 `permissions.py` 的资源、活跃模块和贸易处默认可见范围；人工接口仍用 `can_sensitive`。

增加 schema 幂等、模块权限和全部字段列存在测试，运行：

```bash
pytest -q tests/test_spot_ledger.py tests/test_auth_permissions.py -k 'spot_ledger or permission or schema'
```

- [ ] **Step 6: 提交本任务。**

运行 `git diff --check`，然后提交：

```bash
git add backend/app/spot_ledger.py backend/app/db.py backend/app/permissions.py tests/test_spot_ledger.py
git commit -m "feat: add spot ledger field contract and schema"
```

## Task 2: 实现 fixture/HTTP source、全量同步门禁、调度和历史迁移

**Files:**

- Create: `backend/app/spot_ledger_sync.py`
- Create: `tests/fixtures/spot_ledger_sales_contract_fixture.json`
- Create: `scripts/import_spot_ledger_history.py`
- Test: `tests/test_spot_ledger_sync.py`

**Interfaces:**

- `SalesContractSource.fetch_full_scan() -> FullScanResult`：返回标准记录、页数、总数和完整性结果。
- `FixtureSalesContractSource(path).fetch_full_scan() -> FullScanResult`：只读加载明确 fixture。
- `ProfiledSalesContractSource(profile, http).fetch_full_scan() -> FullScanResult`：仅在 profile/auth 完整时 POST，不猜协议。
- `apply_full_scan(scan, slot_key, now=None) -> dict`：事务化 upsert，完整成功后软隐藏。
- `due_spot_ledger_slots(now, attempted_slots=None) -> list[str]`：每天十个整点 slot，返回时间精度到分钟。
- `start_spot_ledger_sync_scheduler(interval_seconds=30) -> bool`：按环境变量启停。
- `migrate_history_workbook(path, apply=False) -> dict`：唯一匹配迁移，默认 dry-run。

- [ ] **Step 1: 写 source/sync 失败测试。**

先加入：fixture 7 组准入和多明细；重复 ID 幂等；未知类型保留异常；完整扫描隐藏缺失 ID；不完整分页不隐藏；调度十个 slot；缺少真实 profile/auth 返回 `auth_unavailable` 且不发 HTTP；历史唯一/无匹配/歧义处理。

示例断言：

```python
def test_incomplete_scan_does_not_hide_existing_record(ledger_db):
    first = load_fixture_scan()
    apply_full_scan(first, "2026-08-24T09:00+08:00")
    broken = FullScanResult(records=first.records[:1], page_count=1, expected_page_count=2, total_count=1, complete=False, errors=["page_missing"])
    result = apply_full_scan(broken, "2026-08-24T10:00+08:00")
    assert result["hidden"] == 0
    assert get_active_records()[0]["is_active"] == 1


def test_unattended_source_without_profile_is_explicitly_blocked(monkeypatch):
    source = ProfiledSalesContractSource.from_env()
    with pytest.raises(SalesContractSourceError, match="auth_unavailable"):
        source.fetch_full_scan()
```

- [ ] **Step 2: 运行测试确认红灯。**

运行：

```bash
pytest -q tests/test_spot_ledger_sync.py
```

预期：FAIL，原因是 source contract、扫描门禁和同步函数尚未实现。

- [ ] **Step 3: 添加明确标注的 fixture 和 source contract。**

fixture 至少覆盖 7 个销售组、同一合同 2 个明细、B07/B06/B05/B09、船名缺失、E/AP 不一致、非生效记录和未知映射错误。实现分页完整性检查，禁止按固定列序猜测外部响应；真实 profile 需要显式 records/total/page 路径、field map 和认证 provider。

- [ ] **Step 4: 实现事务化同步和软隐藏。**

按 `source_detail_id` upsert 系统字段、保留人工字段，记录 run 统计和脱敏错误；只有 `complete=True` 且分页/ID 检查通过才隐藏未出现现货 ID。每个时间戳用 `isoformat(timespec="seconds")`，任务错误不写凭据。

- [ ] **Step 5: 实现十个小时 slot 和启动保护。**

用 Asia/Shanghai 计算每天 09:00—18:00 slot；`SPOT_LEDGER_AUTO_SYNC_ENABLED` 非 true 时不启动；真实 source mode 缺 profile/auth 时更新异常状态而不是循环猜测或发起网页请求。不要添加手动同步 API。

- [ ] **Step 6: 实现历史迁移脚本和测试。**

使用 openpyxl 读取 `现货业务台账`，按合同号、商品、销售价格、合同/结算数量找唯一候选；只复制手工字段，忽略 `——` 占位符；拆分旧 P 的是否长协和对象。默认只输出 dry-run 摘要，`--apply` 才写入当前数据库；无真实 Excel 时不执行真实迁移。

- [ ] **Step 7: 运行同步/迁移测试并提交。**

```bash
pytest -q tests/test_spot_ledger_sync.py tests/test_spot_ledger.py
git diff --check
git add backend/app/spot_ledger_sync.py tests/fixtures/spot_ledger_sales_contract_fixture.json scripts/import_spot_ledger_history.py tests/test_spot_ledger_sync.py
git commit -m "feat: add spot ledger source sync and migration"
```

## Task 3: 实现 API、敏感编辑、待办/异常和 Excel 导出

**Files:**

- Modify: `backend/app/spot_ledger.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/permissions.py`
- Test: `tests/test_spot_ledger_api.py`

**Interfaces:**

- `GET /api/spot-ledger/field-definitions`
- `GET /api/spot-ledger/records`
- `GET /api/spot-ledger/records/{record_id}`
- `PATCH /api/spot-ledger/records/{record_id}`
- `GET /api/spot-ledger/pending`
- `GET /api/spot-ledger/sync-errors`
- `GET /api/spot-ledger/sync-status`
- `GET /api/spot-ledger/export`
- `POST /api/spot-ledger/strategic-hedging`

- [ ] **Step 1: 写 API 失败测试。**

使用 FastAPI TestClient 或模块路由函数测试：默认只返回 active；所有筛选可组合；缺失字段返回待补录；异常返回 run/record；普通编辑权限不能修改；敏感权限可修改手工字段但不能修改系统字段；战略套保创建接受完整开平字段且拒绝部分平仓；导出默认 A:AY、不含技术主键，`include_technical_key=true` 时包含明细 ID。

- [ ] **Step 2: 运行 API 测试确认红灯。**

```bash
pytest -q tests/test_spot_ledger_api.py
```

- [ ] **Step 3: 实现查询、详情和字段定义响应。**

列表支持日期范围、7 组、利润组、销售类型、商品、港口、操作抬头、供应商、客户、合同号、采购/销售/采购执行/销售执行、结案状态、补录状态和同步异常组合筛选；默认排除非 active。详情返回平铺 51 字段、模块扩展字段和字段定义。

- [ ] **Step 4: 实现敏感字段编辑和战略套保创建。**

手工字段白名单校验数字、日期、选项和条件字段；缺必填允许保存并重算待补录；系统字段更新请求返回 400；编辑不写操作日志。战略套保不套用现货必填，校验仅支持全开全平，状态输出未平仓/已平仓/数据异常。

- [ ] **Step 5: 实现待办、异常、状态和 xlsx 导出。**

导出沿用当前筛选，清空筛选导出所有 active；A:AY 顺序固定，模块新增字段追加；使用 openpyxl 生成 Excel-compatible `.xlsx`，不把凭据或内部 profile 写入文件。

- [ ] **Step 6: 运行 API/权限/导出测试并提交。**

```bash
pytest -q tests/test_spot_ledger_api.py tests/test_auth_permissions.py -k 'spot_ledger or permission or export'
git diff --check
git add backend/app/spot_ledger.py backend/app/main.py backend/app/permissions.py tests/test_spot_ledger_api.py
git commit -m "feat: expose spot ledger records and export APIs"
```

## Task 4: 接入导航并完成页面

**Files:**

- Create: `frontend/spot_ledger.js`
- Create: `tests/spot_ledger_frontend.test.mjs`
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Modify: `frontend/styles.css`

**Interfaces:**

- `window.SpotLedger.activate({ api, canSensitive })`：加载字段定义和列表。
- 页面保留现有 shell，新增列表、详情抽屉/区域、待补录/同步异常 tab、筛选区、导出按钮和战略套保手工录入对话框。

- [ ] **Step 1: 写前端失败契约。**

断言页面含 `spotLedgerPage`、导航路由 `spot_ledger`、A:AY 字段渲染入口、待补录/同步异常视图、导出和手工战略套保入口；断言不存在“立即同步”按钮；时间文案只显示到秒。

- [ ] **Step 2: 运行 Node 测试确认红灯。**

```bash
node --test tests/spot_ledger_frontend.test.mjs
```

- [ ] **Step 3: 接入 HTML、静态资源和 app 路由。**

在现有“贸易台账管理”分组中由后端模块返回“现货业务台账管理”；`showOnly` 增加页面；`activateModule` 只委托到 `window.SpotLedger`；权限只隐藏编辑/战略套保/导出按钮，不隐藏查看页面。

- [ ] **Step 4: 实现列表、筛选、详情和状态视图。**

列表显示合同号、销售组、利润组、销售类型、日期、商品、港口、客户、数量、价格、补录状态和同步状态；详情逐字段显示值、控制类型、来源规则和空白；待补录显示缺失字段；同步异常显示任务时间、类型、原因和明细定位；所有 HTML 值经过现有 escape helper 或模块内部安全转义。

- [ ] **Step 5: 实现敏感编辑、战略套保和导出交互。**

仅在 `canSensitive` 为真时显示编辑、战略套保和导出操作；保存后重新读取详情/列表；不提供同步按钮。导出保留筛选条件并支持技术主键显式勾选。

- [ ] **Step 6: 运行 Node/语法检查并提交。**

```bash
node --test tests/spot_ledger_frontend.test.mjs
node --check frontend/spot_ledger.js
node --check frontend/app.js
git diff --check
git add frontend/index.html frontend/app.js frontend/styles.css frontend/spot_ledger.js tests/spot_ledger_frontend.test.mjs
git commit -m "feat: add spot ledger navigation and page"
```

## Task 5: 本地 fixture 页面验收和最终质量门禁

**Files:**

- Modify: `README.md`（仅在运行方式/模块结构确有变化时）
- Modify: `版本更新记录.md`（仅记录本地候选，不写 Production 发布）
- Test: existing Python/Node suites and local page evidence

- [ ] **Step 1: 建立本地 SQLite fixture 数据。**

使用显式 fixture 同步函数写入当前 worktree 的本地 SQLite；确认 `.env` 不存在或 `DATABASE_URL` 未指向正式环境。不要写入 Supabase、Render 或用户正式数据。

- [ ] **Step 2: 运行完整自动化门禁。**

```bash
pytest -q
node --test tests/*.test.mjs
python3 -m compileall -q backend/app scripts
node --check frontend/app.js
node --check frontend/spot_ledger.js
git diff --check
```

必须读取完整输出并记录失败数；任何失败先修复并重新运行受影响测试，再做全量门禁。

- [ ] **Step 3: 启动本地服务并完成真实页面验收。**

启动：

```bash
env -u DATABASE_URL .venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8001
```

用管理员账号登录 `http://127.0.0.1:8001`，验证 URL/标题、菜单、列表、组合筛选、详情 51 字段、人工字段编辑、待补录、同步异常、战略套保全开全平、导出文件回读、控制台无应用错误。使用浏览器新鲜本地页签，不将 fixture 页面当成真实源同步证据。

- [ ] **Step 4: 做需求反向追踪和限制复核。**

逐项复核 TL-NAV-01 至 TL-TIME-01，确认每项都有自动化和页面证据；确认没有立即同步、审计留痕、部分套保、生产连接或真实交易操作。明确真实无人值守认证、source profile 和历史 Excel 仍是上线前阻塞。

- [ ] **Step 5: 更新项目文档并提交候选。**

仅在实际变更需要时更新 README；在版本记录中写明本地/feature 候选、测试证据、回滚点和未进入 Production。最终提交前运行 `git status --short --branch`、`git diff --stat` 和完整验证命令，不推送 main。
