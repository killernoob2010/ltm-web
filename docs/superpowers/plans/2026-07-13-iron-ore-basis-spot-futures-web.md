# Iron Ore Basis Spot-Futures Web Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for every behavior change. Execute inline under the approved AI SDLC Gate A; do not dispatch subagents or enter production.

**Goal:** Import the confirmed 2024-to-date iron ore basis workbook into independent result/detail tables and expose read-only management and visualization pages on staging.

**Architecture:** Add isolated `iron_ore_basis` backend and importer modules, with idempotent schema creation from the existing database initializer. Add a focused frontend controller that reuses the current data-visualization pages, visual tokens and Canvas chart style while keeping the existing spot views intact.

**Tech Stack:** FastAPI, SQLite/PostgreSQL, psycopg2, openpyxl, plain JavaScript, Canvas2D, Node test runner, pytest.

## Global Constraints

- Testing branch/environment: `staging` / Render `ltm-web-staging` / Supabase `LTM WEB STAGING`.
- Production is excluded until a separate Gate B approval.
- Workbook: `outputs/019f58fd-4754-7ae2-8ebd-c3321fe0dd7f/铁矿石港口基差基础数据库_2024至今.xlsx`.
- Expected logical/physical counts: 60,424 basis records; 60,424 result rows plus 60,424 detail rows.
- No UI import/export/edit/delete and no automatic API sync in V1.
- Preserve unrelated dirty files and existing spot-data behavior.

---

### Task 1: Project and schema contract

**Files:**
- Create: `AI_SDLC_PROJECT.md`
- Create: `docs/2026-07-13_iron-ore-basis-spot-futures-business-requirements.md`
- Modify: `backend/app/db.py`
- Modify: `scripts/supabase_ddl.sql`
- Test: `tests/test_iron_ore_basis.py`

**Interfaces:**
- Produces tables `iron_ore_basis_results` and `iron_ore_basis_details`.
- Result business key is `business_date|port|product|rule_version|parameter_version`.

- [ ] Write a failing test that runs `db.init_db()` on SQLite and asserts both tables, the result business-key unique index, the detail `result_id` relation and required query indexes exist.
- [ ] Run `pytest tests/test_iron_ore_basis.py -q`; expect failure because the tables do not exist.
- [ ] Add `migrate_iron_ore_basis_schema(conn)` with PostgreSQL `DOUBLE PRECISION/SERIAL` and SQLite `REAL/AUTOINCREMENT`, then call it from `init_db()`.
- [ ] Add matching idempotent PostgreSQL DDL plus `ENABLE ROW LEVEL SECURITY`; do not grant `anon` or `authenticated` direct access because the app uses its server-side PostgreSQL connection.
- [ ] Run the focused test; expect pass.

### Task 2: Transactional workbook importer

**Files:**
- Create: `backend/app/iron_ore_basis_import.py`
- Create: `scripts/import_iron_ore_basis.py`
- Test: `tests/test_iron_ore_basis_import.py`

**Interfaces:**
- `validate_basis_workbook(path: Path, expected_sha256: str | None = None) -> BasisWorkbookData`
- `import_basis_workbook(path: Path, apply: bool, expected_sha256: str | None = None) -> dict`
- CLI defaults to validation; `--apply` is required for writes.

- [ ] Write failing tests for missing sheet/header, duplicate business key, result/detail mismatch, expected SHA mismatch, successful 2-row import and idempotent re-import.
- [ ] Run the focused tests; expect failures because importer functions are absent.
- [ ] Parse `期现数据` and `计算明细` read-only with `openpyxl`, normalize dates/booleans/numbers, build the exact business key and reconcile result fields against detail fields.
- [ ] Upsert results and details inside one `db.connect()` transaction using batches; resolve `result_id` after result upsert; return validated/inserted-or-updated counts and SHA.
- [ ] Add a CLI with `workbook`, `--expected-sha256`, `--apply`; print one compact JSON summary and return nonzero on validation/write failure.
- [ ] Run focused tests; expect pass. Run the real workbook without `--apply`; expect 60,424/60,424 and the confirmed SHA.

### Task 3: Read-only FastAPI queries

**Files:**
- Create: `backend/app/iron_ore_basis.py`
- Modify: `backend/app/main.py`
- Test: `tests/test_iron_ore_basis.py`

**Interfaces:**
- `GET /api/iron-ore-basis/management/filters`
- `GET /api/iron-ore-basis/management/rows?years=&products=&ports=&limit=50&offset=0`
- `GET /api/iron-ore-basis/display/filters`
- `GET /api/iron-ore-basis/display/chart?port=日照港&years=&products=`
- `GET /api/iron-ore-basis/display/optimal-warrant`

- [ ] Write failing tests for permission enforcement, default-all filters, pagination, fixed port order, active-port-only chart output, current-year latest-date minimum basis and deterministic tie-break.
- [ ] Run focused tests; expect missing router/functions.
- [ ] Implement parameterized SQL helpers and router endpoints. Management uses `data_visualization.data:view`; display uses `data_visualization.display:view`.
- [ ] Chart output groups daily points as `product -> year -> [{date,value}]`; never query or return another port.
- [ ] Optimal-warrant query independently selects current-year `MAX(business_date)` then orders by `basis, standardized_spot_price, wet_spot_price, port, product`.
- [ ] Register the router and run focused plus existing data-visualization/auth tests; expect pass.

### Task 4: Management and display UI

**Files:**
- Create: `frontend/iron_ore_basis.js`
- Create: `frontend/iron_ore_basis.css`
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Test: `tests/iron_ore_basis_frontend.test.mjs`
- Test: `tests/data_visualization_frontend.test.mjs`

**Interfaces:**
- `window.IronOreBasis.activateManagement()` and `window.IronOreBasis.activateDisplay()`.
- Existing `activateModule` delegates to these functions while defaulting both pages to their spot subview.

- [ ] Write failing source-contract tests for the four third-level labels, no basis upload/export controls, management fields, independent optimal-warrant block, year/product-only display filters, exact port-tab order and default `日照港`.
- [ ] Run `node --test tests/iron_ore_basis_frontend.test.mjs`; expect failure.
- [ ] Wrap current data and chart bodies as spot/basis subviews; render real nested third-level sidebar items under the existing data/chart parents without changing backend permission codes.
- [ ] Implement default-all checkbox filters, 50-row management pagination, current-port-only chart requests and independent optimal-warrant request.
- [ ] Render product small multiples in Canvas2D: daily points, month ticks, year colors, negative values, explicit zero axis, date/basis tooltip and responsive 3/2/1-column layout.
- [ ] Add focused CSS using existing colors, typography, density, radii and responsive breakpoints; no separate concept or invented navigation.
- [ ] Bump static asset versions and run focused plus existing frontend tests; expect pass.

### Task 5: Local quality gate and real-data verification

**Files:**
- Verify: all files above
- Verify: confirmed workbook

- [ ] Run `python -m compileall backend/app scripts` with the project venv.
- [ ] Run focused pytest and Node tests, then the complete existing pytest/Node suite.
- [ ] Initialize a temporary SQLite database; dry-run, apply and re-apply the real workbook. Assert 60,424 result rows, 60,424 detail rows and zero duplicate business keys.
- [ ] Query filters, first management page, 日照港 chart, and optimal-warrant endpoint against the imported SQLite data. Assert the 2026-07-10 昆巴粉/京唐港/-12.01028806584361 record.
- [ ] Review `git diff --check`, `git status`, and changed-file scope; exclude user-owned dirty files.

### Task 6: Staging database, deploy and T4 acceptance

**Files:**
- Modify after deployment: `版本更新记录.md`

- [ ] Commit only approved module/doc files and push `staging`.
- [ ] Before staging schema/data writes, run `scripts/backup_database.py` against the documented staging target and record backup path/counts without secrets.
- [ ] Apply the idempotent schema to `LTM WEB STAGING`, import the confirmed workbook with `--apply`, and verify exact table counts and duplicate count 0 using direct SQL.
- [ ] Confirm Render staging activates the pushed commit.
- [ ] Open a fresh in-app-browser tab at `https://ltm-web-staging.onrender.com/?codex=<commit>` and verify URL/title/static versions/console plus management pagination/filters, display default 日照港, port switching, chart filter independence, negative basis zero axis, and the exact optimal-warrant card.
- [ ] Verify existing spot management/display still work and inspect desktop plus narrow viewport screenshots against the approved existing design system.
- [ ] Update `版本更新记录.md` only after staging passes, commit/push the record, record AI SDLC tests/release evidence and stop at Gate B.

## Self-review

- Spec coverage: every BRD section maps to Tasks 1-6.
- Placeholder scan: no TBD/TODO or undefined implementation step remains.
- Type/name consistency: table, route, permission and frontend-controller names are identical across tasks.
- Rollback: code redeploys the previous staging commit; database restore uses the pre-migration staging backup. The two new isolated tables can be ignored by the previous application version during code rollback.
