# Iron Ore Basis API Auto Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不依赖本机和历史 Excel 继续追加的前提下，让现有 Render Web Service 每天从 EBC 与新浪 I0 读取数据，按当前业务规则增量计算铁矿石期现基差，并在两个现有页面只显示数据库的最新数据日期。

**Architecture:** 将数据源适配、规则快照、纯计算、增量落库和调度拆成五个窄模块。历史 Excel 导入结果继续保留在现有结果表和明细表；API 原始点位写入独立来源表，只有 EBC 现货、I0 收盘价和规则参数同时完整的港口/品种/日期组合才追加到现有结果与明细表。调度线程仍运行在现有 Render Standard Web Service 内，默认关闭；Staging 只手动验证，Production 经单独确认后才启用。

**Tech Stack:** Python 3.11、FastAPI、PostgreSQL/Supabase、SQLite 测试库、`requests`、后台线程、原生 JavaScript、Node test runner、pytest、Render Standard Web Service。

## Global Constraints

- 实施依据：`docs/superpowers/specs/2026-07-14-iron-ore-basis-api-auto-sync-design.md`。若本计划与设计文档冲突，以设计文档和用户最新确认口径为准。
- 当前工作分支与默认环境：`staging` / Render `ltm-web-staging` / Supabase `LTM WEB STAGING`。Staging 开发、提交、推送、部署和测试数据验证可以自主执行。
- Production Gate B：合并或推送 `main`、生产部署、生产环境变量、生产数据库迁移或写入前必须再次取得用户确认，并先完成备份、迁移和回滚说明。
- 真实金融系统只读：EBC 与新浪接口只允许登录、查询和读取；不得操作任何交易、资金或订单控件/API。
- 不把账号、密码、Cookie、Token、数据库 URL 或 Deploy Hook 写入代码、测试、文档、日志、异常文本或提交记录。用户此前提供的 EBC 密码在生产启用前必须轮换，新密码只保存为 Render Secret。
- 历史边界：已有 Excel 历史截至 `2026-07-10`；API 增量从下一交易日 `2026-07-13` 开始。Excel 导入入口继续保留，历史文件本身不追加、不改写。
- 完整组合写入：同一日期中，只有某一港口/品种的 EBC 现货、I0 收盘价和规则参数都存在时才写该组合；不完整组合跳过，但不得阻塞其他完整组合。
- 去重与历史保护：结果业务键沿用 `date|port|product|rule_version|parameter_version`；API 同键永不覆盖已有结果/明细。来源点首次值作为 canonical value；后续值变化只记录差异元数据，不覆盖 canonical value，也不触发历史重算。
- 规则版本：当前规则无限期沿用。新规则必须由用户提供并显式激活，从激活后的下一个真实交易日生效；不得重算或覆盖旧日期。
- 初版界面不显示异常、不显示缺失组合数量、不发送提醒。两个页面顶部只显示 `最新数据日期：YYYY-MM-DD`，值为 `MAX(iron_ore_basis_results.business_date)`。
- 时间口径固定为 `Asia/Shanghai`：T 日 21:30、T+1 日 09:30、T+1 日 10:30；启动补漏回看最近 10 个自然日。调度必须依赖数据库幂等记录，不能只依赖进程内变量。
- 新表 `iron_ore_basis_source_points` 与 `iron_ore_basis_sync_runs` 在 PostgreSQL 中必须启用 RLS，不为 `anon`/`authenticated` 创建策略，并撤销表和序列权限。
- Staging 的 `IRON_ORE_BASIS_AUTO_SYNC_ENABLED` 保持关闭；只通过受控命令手动执行 dry-run 与 `--apply`。Production 只有 Gate B 后才设置为开启。
- 保持外科式修改：不重构其他行情、风险、交易或数据可视化模块；不改现有导航和权限模型；不处理工作区已有的无关未提交文件。
- AI SDLC 评估：D2（现有期现功能的数据接入方式优化）；T1（完整验证 API 自动更新这一项功能）；R2（增量、可关闭、可定位且可恢复的数据变更）；C2（单主 Agent 分阶段完成外部接口、计算、存储和调度验证，不启用子代理）。

---

### Task 1: 固化当前规则快照与历史回放基线

**Files:**
- Create: `backend/app/iron_ore_basis_rule_v1.json`
- Create: `backend/app/iron_ore_basis_rules.py`
- Create: `scripts/snapshot_iron_ore_basis_rule.py`
- Create: `scripts/verify_iron_ore_basis_history.py`
- Create: `tests/test_iron_ore_basis_rules.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class ProductRule:
    product: str
    parameter_year: int
    parameter_type: str
    fe: float
    sio2: float
    al2o3: float
    phosphorus: float
    sulfur: float
    h2o: float
    sulfur_defaulted: bool
    brand_adjustment: float
    parameter_source: str
    parameter_version: str

@dataclass(frozen=True)
class IndicatorMapping:
    indicator_code: str
    indicator_name: str
    port: str
    product: str
    ebc_price_fe: float | None
    price_proxy_indicator: str | None
    price_parameter_spec_diff: bool
    ebc_original_port: str | None

@dataclass(frozen=True)
class BasisRulePack:
    rule_version: str
    effective_from: date
    products: dict[str, ProductRule]
    indicators: dict[str, IndicatorMapping]

def load_active_rule_pack(business_date: date) -> BasisRulePack: ...
```

- `iron_ore_basis_rule_v1.json` 是运行时唯一的当前规则来源，不允许运行时从历史结果反推参数。
- 快照脚本只读查询已审计的 `iron_ore_basis_details`：指标映射取全历史唯一映射；每个品种参数取该品种最新有效的 `parameter_version`，允许 2027 年未提供新参数时继续沿用当前版本。
- JSON 固定记录 `I2312 / F-DCE I004-2021`、15 个品种参数和已验证的 104 个 EBC 指标映射；数组按稳定键排序，便于代码审查和哈希复现。

- [ ] **Step 1: 先写规则加载失败测试**

在 `tests/test_iron_ore_basis_rules.py` 覆盖：规则版本、15 个品种、104 个指标、重复指标拒绝、重复港口/品种映射拒绝、缺字段拒绝、未来日期仍返回当前规则。

Run:

```bash
env -u DATABASE_URL .venv/bin/pytest -q tests/test_iron_ore_basis_rules.py
```

Expected: FAIL，提示 `iron_ore_basis_rules` 尚不存在。

- [ ] **Step 2: 实现只读规则快照命令**

`scripts/snapshot_iron_ore_basis_rule.py` 默认只打印摘要与 SHA-256；只有显式 `--output <path>` 才生成 JSON 文件。查询必须只使用 `SELECT`，且断言：

```text
rule_version=I2312 / F-DCE I004-2021
products=15
indicator_mappings=104
mapping_conflicts=0
```

脚本不得打印 `DATABASE_URL`，不得写数据库。生成前若出现同一指标映射到多个港口/品种，立即失败，不静默选择。

- [ ] **Step 3: 生成并审查版本化规则 JSON**

在明确连接 Staging 数据库的受控环境运行：

```bash
.venv/bin/python scripts/snapshot_iron_ore_basis_rule.py --output backend/app/iron_ore_basis_rule_v1.json
```

Expected: JSON 包含 15 个品种、104 个映射；不包含任何凭据、Token、Cookie 或连接串。

- [ ] **Step 4: 实现严格规则加载器**

加载器将百分比继续使用现有数据库中的小数口径，不做二次猜测；对 NaN、空参数、未知规则版本和日期早于规则有效期明确抛出 `RuleConfigurationError`。

- [ ] **Step 5: 建立 60,424 行只读历史回放脚本**

`scripts/verify_iron_ore_basis_history.py` 从 `iron_ore_basis_details` 分页读取，并验证：业务键唯一、结果/明细关联完整、五项升贴水、质量合计、干吨价、标准化现货价、基差均与现存数据在 `1e-9` 容差内一致。脚本只读、退出码非零代表失败。

Expected current baseline:

```text
details=60424
orphans=0
formula_mismatches=0
rule_mismatches=0
```

- [ ] **Step 6: 运行规则测试并提交**

```bash
env -u DATABASE_URL .venv/bin/pytest -q tests/test_iron_ore_basis_rules.py
git add backend/app/iron_ore_basis_rule_v1.json backend/app/iron_ore_basis_rules.py scripts/snapshot_iron_ore_basis_rule.py scripts/verify_iron_ore_basis_history.py tests/test_iron_ore_basis_rules.py
git commit -m "feat: snapshot iron ore basis rules"
```

Expected: tests PASS；提交中无凭据和无关文件。

---

### Task 2: 实现可独立测试的基差计算引擎

**Files:**
- Create: `backend/app/iron_ore_basis_calculation.py`
- Create: `tests/test_iron_ore_basis_calculation.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class BasisCalculationInput:
    business_date: date
    mapping: IndicatorMapping
    product_rule: ProductRule
    wet_spot_price: float
    futures_close: float
    futures_series: str = "I0"

@dataclass(frozen=True)
class BasisCalculation:
    result: dict[str, object]
    detail: dict[str, object]

def calculate_basis_row(value: BasisCalculationInput, rule_pack: BasisRulePack) -> BasisCalculation: ...
```

公式必须逐项输出审计字段：

```text
dry_spot_price = wet_spot_price / (1 - h2o)
quality_adjustment = fe + sio2 + al2o3 + phosphorus + sulfur adjustments
standardized_spot_price = dry_spot_price - quality_adjustment - brand_adjustment
basis = standardized_spot_price - futures_close
```

- [ ] **Step 1: 写代表性规则测试并确认失败**

覆盖 Fe 三个区间、SiO2 三个区间、Al2O3、P、S、S 缺失默认值、品牌升贴水、含水换算、业务键和 ISO 周次。加入一条完整历史明细作为 golden case。

Run:

```bash
env -u DATABASE_URL .venv/bin/pytest -q tests/test_iron_ore_basis_calculation.py
```

Expected: FAIL，模块尚不存在。

- [ ] **Step 2: 实现最小纯函数**

计算模块不得联网、不得读数据库、不得读环境变量。输入缺失或 `h2o >= 1` 时抛出 `BasisCalculationError`；不在计算层补零、插值或沿用上日行情。

- [ ] **Step 3: 生成与 Excel 导入一致的结果/明细字典**

复用 `backend/app/iron_ore_basis_import.py` 的 `RESULT_COLUMNS`、`DETAIL_COLUMNS` 和业务键语义。API 行固定：

```text
source_workbook_name=API:EBC+Sina
source_workbook_sha256=SHA-256(canonical inputs + rule version + parameter version)
data_status=有效
futures_series=I0
```

canonical hash 使用排序后的 UTF-8 JSON，禁止包含访问令牌和原始响应头。

- [ ] **Step 4: 运行单元测试和历史回放**

```bash
env -u DATABASE_URL .venv/bin/pytest -q tests/test_iron_ore_basis_calculation.py tests/test_iron_ore_basis_rules.py
.venv/bin/python scripts/verify_iron_ore_basis_history.py
```

Expected: 单元测试 PASS；60,424 行回放 `formula_mismatches=0`。

- [ ] **Step 5: 提交计算引擎**

```bash
git add backend/app/iron_ore_basis_calculation.py tests/test_iron_ore_basis_calculation.py
git commit -m "feat: calculate iron ore basis rows"
```

---

### Task 3: 实现 EBC 与新浪 I0 只读数据源适配器

**Files:**
- Create: `backend/app/iron_ore_basis_sources.py`
- Create: `tests/test_iron_ore_basis_sources.py`
- Modify: `backend/app/info_summary_backfill.py`

**Interfaces:**

```python
class BasisSourceError(RuntimeError):
    code: str

class EbcBasisSource:
    def login(self) -> str: ...
    def fetch_points(
        self,
        indicator_codes: Sequence[str],
        start_date: date,
        end_date: date,
    ) -> list[SourcePoint]: ...

class SinaI0Source:
    def fetch_closes(self, start_date: date, end_date: date) -> dict[date, float]: ...
```

- EBC 账号与密码只从 `EBC_ACCOUNT`、`EBC_PASSWORD` 环境变量读取；缺失时返回结构化错误，不在 import/startup 阶段登录。
- 登录和查询请求使用明确超时、`raise_for_status()`、业务状态码校验；日志只允许输出错误代码和目标日期范围。
- 新浪适配器复用 `SinaHistoryProvider` 的解析逻辑，但将网络错误与“返回空数据”区分开，避免现有 `{}` 静默吞错妨碍调度判断。现有调用方行为保持兼容。

- [ ] **Step 1: 用伪造 HTTP 会话写失败测试**

覆盖：缺少环境变量、登录 401、返回非 JSON、业务 code 非 200、请求超时、104 指标批量查询、EBC null 点位保留为缺失、Sina JSONP 正常解析、I0 日期范围过滤。

Run:

```bash
env -u DATABASE_URL .venv/bin/pytest -q tests/test_iron_ore_basis_sources.py
```

Expected: FAIL，适配器尚不存在。

- [ ] **Step 2: 实现 EBC 会话和批量读取**

单次同步只登录一次，并在一个 `queryIndexData` 请求中发送当前规则包的全部 104 个指标；不得为每个指标重复登录。原始响应只转换为 `SourcePoint(source_name, indicator_key, business_date, value, payload_sha256)`，不保存 Token。

- [ ] **Step 3: 抽出可报告错误的新浪读取方法**

保留 `SinaHistoryProvider.history()` 对旧功能的兼容返回；新增内部严格方法供 `SinaI0Source` 使用，HTTP/解析失败抛 `BasisSourceError`，真实无数据返回空字典。

- [ ] **Step 4: 运行来源测试与现有回归**

```bash
env -u DATABASE_URL .venv/bin/pytest -q tests/test_iron_ore_basis_sources.py tests/test_info_summary_backfill.py
```

Expected: PASS；现有信息汇总缓存行为不变。

- [ ] **Step 5: 提交来源适配器**

```bash
git add backend/app/iron_ore_basis_sources.py backend/app/info_summary_backfill.py tests/test_iron_ore_basis_sources.py
git commit -m "feat: read EBC and Sina basis data"
```

---

### Task 4: 增加来源点与同步批次数据库结构

**Files:**
- Modify: `backend/app/db.py`
- Modify: `scripts/backup_database.py`
- Modify: `tests/test_iron_ore_basis.py`

**Schema:**

`iron_ore_basis_sync_runs`：

```text
id, slot_key UNIQUE, trigger_type, target_start_date, target_end_date,
status(running/success/partial/failed/skipped), source_points_seen,
source_points_inserted, source_differences, combinations_written,
combinations_skipped, error_code, error_summary, started_at, finished_at,
created_at, updated_at
```

`iron_ore_basis_source_points`：

```text
id, source_name, indicator_key, business_date, canonical_value,
canonical_payload_sha256, first_run_id, last_observed_value,
last_observed_payload_sha256, difference_detected, difference_count,
first_observed_at, last_observed_at,
UNIQUE(source_name, indicator_key, business_date)
```

`canonical_value` 首次插入后不可更新；同键同值只更新观察时间，同键不同值只更新 `last_observed_*`、差异标志与计数。

- [ ] **Step 1: 先写 SQLite 结构和约束测试**

验证两表存在、唯一键有效、结果表原有 60,424 行语义不变、source point canonical value 不被差异观察覆盖。

Run:

```bash
env -u DATABASE_URL .venv/bin/pytest -q tests/test_iron_ore_basis.py
```

Expected: FAIL，新表不存在。

- [ ] **Step 2: 同时实现 PostgreSQL 与 SQLite migration**

沿用 `migrate_iron_ore_basis_schema()`，只增加 `CREATE TABLE/INDEX IF NOT EXISTS`，不得改动或删除现有列。PostgreSQL 必须执行：

```sql
ALTER TABLE iron_ore_basis_sync_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE iron_ore_basis_source_points ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE iron_ore_basis_sync_runs, iron_ore_basis_source_points FROM anon, authenticated;
REVOKE ALL ON SEQUENCE iron_ore_basis_sync_runs_id_seq, iron_ore_basis_source_points_id_seq FROM anon, authenticated;
```

- [ ] **Step 3: 将新表纳入数据库备份清单**

在 `scripts/backup_database.py` 的核心表列表加入两表，保持备份脚本其余行为不变。

- [ ] **Step 4: 运行 DB 测试并检查 migration 幂等性**

```bash
env -u DATABASE_URL .venv/bin/pytest -q tests/test_iron_ore_basis.py
env -u DATABASE_URL .venv/bin/python -c 'from backend.app import db; db.init_db(); db.init_db(); print("migration_ok")'
```

Expected: PASS；连续初始化两次无错误。

- [ ] **Step 5: 提交数据库结构**

```bash
git add backend/app/db.py scripts/backup_database.py tests/test_iron_ore_basis.py
git commit -m "feat: store basis sync source points"
```

---

### Task 5: 实现幂等增量同步与手动命令

**Files:**
- Create: `backend/app/iron_ore_basis_sync.py`
- Create: `scripts/sync_iron_ore_basis.py`
- Create: `tests/test_iron_ore_basis_sync.py`
- Modify: `backend/app/iron_ore_basis_import.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class SyncSummary:
    status: str
    target_start_date: date
    target_end_date: date
    source_points_seen: int
    source_points_inserted: int
    source_differences: int
    combinations_written: int
    combinations_skipped: int

def sync_basis_range(
    start_date: date,
    end_date: date,
    *,
    trigger_type: str,
    slot_key: str,
    apply: bool = False,
    sources: BasisSources | None = None,
) -> SyncSummary: ...
```

- dry-run 可以登录和读取来源，但不得创建 sync run、source point、result 或 detail。
- apply 模式先获得 PostgreSQL advisory lock；同一 `slot_key` 或同一时间窗口并发时只允许一个执行者。SQLite 测试使用进程内锁。
- 先写 source points，再按日期/指标映射与 I0 聚合完整组合。结果和明细在单一事务中成对插入；任一行失败时该事务回滚。
- API 写入必须 `ON CONFLICT DO NOTHING`，不得复用 Excel 导入的覆盖式 upsert。Excel 导入逻辑仍保留原行为。
- 结果插入后再解析 `result_id` 写 detail；不存在完整结果的组合只计内部 skipped，不生成空行。

- [ ] **Step 1: 写同步失败测试**

覆盖：全量完整组合、部分 EBC null、I0 缺失、规则缺失、重复运行、并发 slot、历史同值来源、历史变值来源、结果已由 Excel 存在、事务中途失败回滚、dry-run 零写入。

Run:

```bash
env -u DATABASE_URL .venv/bin/pytest -q tests/test_iron_ore_basis_sync.py
```

Expected: FAIL，同步模块尚不存在。

- [ ] **Step 2: 将列定义和 SQL 小范围复用**

把 `RESULT_COLUMNS`、`DETAIL_COLUMNS` 和业务键函数作为明确的公共内部接口保留在 `iron_ore_basis_import.py`；不要重写 Excel 解析器，不把同步逻辑塞入导入模块。

- [ ] **Step 3: 实现来源点 first-write-wins**

在 SQL 层保证 canonical value 不被覆盖；不同值记录 `difference_detected=1` 和 `difference_count+1`。同步后计算继续使用 canonical value，确保历史稳定。

- [ ] **Step 4: 实现完整组合聚合和成对插入**

日期遍历只处理 `start_date <= date <= end_date`；对每个规则映射读取 EBC canonical point，同日读取 `Sina/I0` canonical point。缺一项即跳过该港口/品种，不向用户界面暴露 skipped 明细。

- [ ] **Step 5: 实现安全的手动 CLI**

```bash
.venv/bin/python scripts/sync_iron_ore_basis.py --start-date 2026-07-13 --end-date 2026-07-14
.venv/bin/python scripts/sync_iron_ore_basis.py --start-date 2026-07-13 --end-date 2026-07-14 --apply
```

默认 dry-run；`--apply` 才写库。标准输出只显示日期范围、状态、读取/新增/跳过数量，不显示账号、密码、Token、请求正文或连接串。

- [ ] **Step 6: 运行同步和回归测试**

```bash
env -u DATABASE_URL .venv/bin/pytest -q tests/test_iron_ore_basis_sync.py tests/test_iron_ore_basis_import.py tests/test_iron_ore_basis_calculation.py
```

Expected: PASS；Excel 导入测试保持通过。

- [ ] **Step 7: 提交同步实现**

```bash
git add backend/app/iron_ore_basis_sync.py backend/app/iron_ore_basis_import.py scripts/sync_iron_ore_basis.py tests/test_iron_ore_basis_sync.py
git commit -m "feat: sync incremental iron ore basis data"
```

---

### Task 6: 将三次日程和启动补漏接入现有 Web Service

**Files:**
- Modify: `backend/app/iron_ore_basis_sync.py`
- Modify: `backend/app/main.py`
- Modify: `tests/test_iron_ore_basis_sync.py`

**Interfaces:**

```python
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

def due_sync_slots(now: datetime) -> list[SyncSlot]: ...
def run_due_basis_syncs(now: datetime | None = None) -> list[SyncSummary]: ...
def start_iron_ore_basis_sync_scheduler(interval_seconds: int = 300) -> bool: ...
```

slot 规则：

```text
T 21:30       target=T
T+1 09:30     target=previous calendar dates still missing within 10-day lookback
T+1 10:30     target=previous calendar dates still missing within 10-day lookback
startup       target=max(2026-07-13, latest_result_date-10 days) through today
```

这里不自行维护交易日历：周末/节假日来源为空时自然写入 0 个组合；补漏窗口会在下一次运行继续覆盖最近 10 个自然日。每个时间 slot 使用数据库唯一 `slot_key` 保证多进程/重启幂等。

- [ ] **Step 1: 写确定性时钟测试**

覆盖 21:29/21:30、09:29/09:30、10:29/10:30、周末、时区、重复 tick、进程重启后 slot 已存在、自动开关默认关闭、启动回看不早于 2026-07-13。

- [ ] **Step 2: 实现 5 分钟 tick 和数据库幂等 slot**

线程内异常只记录失败 sync run，不得使 Web 服务退出。一个 slot 失败后，下一预定 slot 仍可重试相同目标日期。

- [ ] **Step 3: 在数据库初始化成功后条件启动**

仅当 `IRON_ORE_BASIS_AUTO_SYNC_ENABLED=true` 时，在 `db.init_db()` 与现有 seed 成功完成后启动 scheduler；默认值必须是 false。不得在 import 时联网，不得让 scheduler 与新表 migration 竞态，也不得阻塞 Web 启动。数据库初始化失败时不启动同步线程，保留现有 Web 启动容错并在后续进程重启时重试。

- [ ] **Step 4: 运行调度与启动回归**

```bash
env -u DATABASE_URL .venv/bin/pytest -q tests/test_iron_ore_basis_sync.py tests/test_iron_ore_basis.py
```

Expected: PASS；未设置开关时 0 次来源请求。

- [ ] **Step 5: 提交调度接入**

```bash
git add backend/app/iron_ore_basis_sync.py backend/app/main.py tests/test_iron_ore_basis_sync.py
git commit -m "feat: schedule iron ore basis sync"
```

---

### Task 7: 在两个现有页面显示统一最新数据日期

**Files:**
- Modify: `backend/app/iron_ore_basis.py`
- Modify: `frontend/index.html`
- Modify: `frontend/iron_ore_basis.js`
- Modify: `frontend/iron_ore_basis.css`
- Modify: `tests/test_iron_ore_basis.py`
- Modify: `tests/iron_ore_basis_frontend.test.mjs`

**API choice:** 不新增跨权限端点。最小修改是在现有两个 filters 响应中都增加：

```json
{"latest_data_date": "2026-07-14"}
```

值来自无 `data_status` 或组合完整率附加条件的：

```sql
SELECT MAX(business_date) AS latest_data_date FROM iron_ore_basis_results
```

因此只要新日期至少一个完整组合写入，页面日期就前进；若新日期一个完整组合都没有，日期保持上一天。

- [ ] **Step 1: 写 API 与前端失败测试**

后端覆盖：空表返回 null、部分组合也返回新日期、管理与展示 filters 同值、原权限不变。前端覆盖：两个视图都有固定文案容器、加载 filters 后更新文本、null 显示“暂无数据”。

Run:

```bash
env -u DATABASE_URL .venv/bin/pytest -q tests/test_iron_ore_basis.py
node --test tests/iron_ore_basis_frontend.test.mjs
```

Expected: FAIL，字段和 DOM 尚不存在。

- [ ] **Step 2: 扩展 filters 查询**

在 `_filters_for_permission()` 同一数据库连接中查询最新日期并返回；`management_filters` 与 `display_filters` 继续分别要求原有 data/display 查看权限。

- [ ] **Step 3: 在两个页面标题区增加状态文本**

只增加：

```text
最新数据日期：YYYY-MM-DD
```

不增加异常、缺失数、失败状态、按钮或编辑入口。样式使用现有面板的次级文字规范，移动端不溢出。

- [ ] **Step 4: 运行前后端测试**

```bash
env -u DATABASE_URL .venv/bin/pytest -q tests/test_iron_ore_basis.py
node --test tests/iron_ore_basis_frontend.test.mjs
```

Expected: PASS。

- [ ] **Step 5: 提交页面状态**

```bash
git add backend/app/iron_ore_basis.py frontend/index.html frontend/iron_ore_basis.js frontend/iron_ore_basis.css tests/test_iron_ore_basis.py tests/iron_ore_basis_frontend.test.mjs
git commit -m "feat: show latest iron ore basis date"
```

---

### Task 8: 全量本地验证与文档更新

**Files:**
- Modify: `README.md`
- Do not modify yet: `版本更新记录.md`（只有 Staging 实际部署成功后更新）
- Modify only if continuation state materially changes: `项目交接摘要.md` 或当前项目等价 handoff

- [ ] **Step 1: 更新运行说明**

README 只补充：两个环境变量名称、自动开关默认关闭、手动命令默认 dry-run、三个调度时间、历史保护和 Production Gate B。不得写任何变量值。

- [ ] **Step 2: 运行定向测试**

```bash
env -u DATABASE_URL .venv/bin/pytest -q \
  tests/test_iron_ore_basis_rules.py \
  tests/test_iron_ore_basis_calculation.py \
  tests/test_iron_ore_basis_sources.py \
  tests/test_iron_ore_basis_sync.py \
  tests/test_iron_ore_basis_import.py \
  tests/test_iron_ore_basis.py \
  tests/test_info_summary_backfill.py
node --test tests/iron_ore_basis_frontend.test.mjs
```

Expected: 全部 PASS。

- [ ] **Step 3: 运行完整后端与前端测试**

```bash
env -u DATABASE_URL .venv/bin/pytest -q
node --test tests/*.test.mjs
```

Expected: 全部 PASS；若存在与本改动无关的既有失败，必须保存失败命令和证据，不能称为全绿。

- [ ] **Step 4: 运行语法、敏感信息和差异检查**

```bash
.venv/bin/python -m compileall -q backend scripts
git diff --check
git status --short
git diff --name-only origin/staging...HEAD
```

并用 `rg` 检查新文件没有账号、密码、Token、Cookie、数据库 URL 实值。Expected: 只出现计划内文件和用户已有未提交文件；无敏感值。

- [ ] **Step 5: 提交 README**

```bash
git add README.md
git commit -m "docs: document iron ore basis sync"
```

---

### Task 9: Staging 数据库迁移、真实 API 手动同步与幂等验证

**Environment:** Render `ltm-web-staging` / Supabase `LTM WEB STAGING` only.

- [ ] **Step 1: 确认 Staging 映射和备份**

依据 `开发流程_备忘.md` 核对服务、分支和数据库；执行项目现有备份流程并记录备份结果，不输出连接串。确认 `IRON_ORE_BASIS_AUTO_SYNC_ENABLED` 未设置或为 false。

- [ ] **Step 2: 推送 staging 并等待自动部署**

```bash
git push origin staging
```

记录推送 commit。Render 自动部署完成前不执行数据库写入。

- [ ] **Step 3: 验证新表安全性**

在 Staging 数据库只读查询 `pg_tables.rowsecurity`、`pg_policies`、`information_schema.role_table_grants`，Expected:

```text
new_tables=2
rls_enabled=2
anon_policies=0
authenticated_policies=0
anon_or_authenticated_grants=0
```

- [ ] **Step 4: 先做真实来源 dry-run**

在 Render Staging Shell 使用 Secret 环境变量运行：

```bash
python scripts/sync_iron_ore_basis.py --start-date 2026-07-13 --end-date <当前日期>
```

Expected: EBC 登录成功、104 指标批量查询成功、新浪 I0 返回目标窗口；数据库四张相关表行数均不变化。

- [ ] **Step 5: 手动 apply Staging 增量**

```bash
python scripts/sync_iron_ore_basis.py --start-date 2026-07-13 --end-date <当前日期> --apply
```

Expected:

- 现有 `2024-01-02` 至 `2026-07-10` 的 60,424 条结果/明细未变化。
- 2026-07-13 起的完整组合被追加到同一结果/明细表。
- 杨迪粉已停止返回的 8 个 null 组合只跳过，不生成空行，不阻塞其余组合。
- `MAX(business_date)` 等于来源已具备完整组合的最新日期。

- [ ] **Step 6: 立即重复 apply 验证幂等**

同命令再执行一次。Expected:

```text
new_results=0
new_details=0
duplicate_business_keys=0
canonical_values_overwritten=0
```

- [ ] **Step 7: 验证历史差异保护**

通过测试夹具或受控 Staging 临时观测制造同 key 不同 source value，确认 canonical value、结果和明细不变，`difference_detected` 与内部计数增加；清理仅限明确的 Staging 测试数据。

- [ ] **Step 8: 运行数据库一致性查询**

验证：结果/明细一一对应、basis 一致、业务键无重复、API provenance 正确、Excel 历史 provenance 未改、最新日期语义正确。

---

### Task 10: 使用内置浏览器完成 Staging 页面验收

**URL:** `https://ltm-web-staging.onrender.com/?codex=<commit>`

- [ ] **Step 1: 关闭旧项目测试页签并打开全新验证页**

不得复用旧缓存页面。确认 URL、标题、登录态和静态资源版本对应本次 commit。

- [ ] **Step 2: 验证“期现数据管理”**

确认顶部只显示 `最新数据日期：YYYY-MM-DD`；历史与 API 新增数据在同一分页结果内；没有异常卡片、缺失计数或编辑入口。

- [ ] **Step 3: 验证“期现数据展示”**

确认最新日期与管理页相同；图表、港口页签、品种/年份过滤和最优仓单仍工作；新日期数据在对应图表可见。

- [ ] **Step 4: 检查浏览器控制台和网络请求**

Expected: 无应用 JavaScript 错误；filters 响应含 `latest_data_date`；浏览器没有直接请求 EBC 或新浪，所有第三方读取均发生在后端。

- [ ] **Step 5: 更新测试版发布记录并提交**

只有 Staging 部署和浏览器验收成功后，更新 `版本更新记录.md`，记录 commit、环境、验证项和仍关闭的自动开关；不记录密钥。

```bash
git add 版本更新记录.md
git commit -m "docs: record staging basis sync verification"
git push origin staging
```

---

### Task 11: Production Gate B（停止点，不自动执行）

- [ ] **Step 1: 向用户汇报 Staging 证据**

汇报：真实 EBC/Sina dry-run、Staging 写入日期、写入组合数、跳过组合数、重复运行 0 新增、历史 60,424 行未变、RLS/权限、两页面最新日期和浏览器控制台状态。

- [ ] **Step 2: 提交生产变更清单供确认**

包括：

1. 合并并推送 `main`。
2. 生产数据库备份与可回滚 migration。
3. 在 Render Production 设置轮换后的 `EBC_ACCOUNT`、`EBC_PASSWORD` Secret。
4. 首次手动 dry-run 与 apply。
5. 再次幂等运行。
6. 最后才设置 `IRON_ORE_BASIS_AUTO_SYNC_ENABLED=true`。
7. 验证三个 Asia/Shanghai slot 和页面最新日期。

- [ ] **Step 3: 等待用户明确批准**

没有 Production Gate B 明确批准，不合并 `main`、不推送 `main`、不改生产环境变量、不迁移/写生产数据库、不启用自动同步。

---

## Definition of Done

- 当前规则快照可审计、可复现，历史 60,424 行回放无差异。
- EBC 104 指标与新浪 I0 可从 Render 后端只读获取，凭据不落库、不进日志、不进仓库。
- API 增量从 2026-07-13 起按完整港口/品种组合写入现有结果和明细表。
- 同一业务键重复运行不新增、不覆盖；历史来源变值只记录内部差异。
- 三个计划时间和 10 日补漏由现有 Web Service 承载，且数据库幂等；Mac 关机不影响 Render 运行。
- 初版页面只显示统一最新数据日期，不展示异常信息。
- 新表 RLS/权限满足设计，备份清单覆盖新表。
- 本地定向与完整测试通过；Staging 真实同步、幂等、数据库一致性和内置浏览器验收通过。
- Production 仍保持未变更，直到用户通过 Gate B。
