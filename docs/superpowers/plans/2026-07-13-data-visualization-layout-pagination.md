# Data Visualization Layout and Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. This task stays single-Agent under the approved C1 boundary.

**Goal:** Align the iron-ore basis year filter, expand both chart viewports on tall screens, and replace both data-management “load more” controls with 20/50/100 server-side pagination matching the Shanghai Junneng ledger.

**Architecture:** Keep the existing read-only `limit/offset/total` APIs unchanged. Add one shared data-visualization pagination renderer, use it from both spot and basis management controllers, and make only scoped CSS changes for filter alignment and chart viewport height.

**Tech Stack:** Vanilla JavaScript, HTML, CSS, Node test runner, FastAPI read-only APIs, in-app Browser staging acceptance.

## Global Constraints

- Work only on `staging`; do not touch `main` or Production.
- Do not change database schema, business data, basis calculations, filters, columns, or sorting.
- Pagination defaults to 20 rows and offers only 20, 50, and 100.
- Filters, metric tabs, or page-size changes reset to page 1.
- Browser acceptance must use the real Staging surface and include desktop plus 390×844 mobile regression.

---

### Task 1: Lock semantic layout and pagination contracts

**Files:**
- Modify: `tests/data_visualization_frontend.test.mjs`
- Modify: `tests/iron_ore_basis_frontend.test.mjs`

**Interfaces:**
- Consumes: current HTML/JS/CSS source files.
- Produces: failing assertions for shared pagination, left-aligned basis year filter, and viewport-responsive chart height.

- [ ] Add assertions that both management pages contain pagination containers instead of load-more buttons.
- [ ] Assert both controllers call `DataVisualizationComponents.renderPagination`, default to page size 20, calculate `offset = (page - 1) * pageSize`, and reset filters to page 1.
- [ ] Assert the shared renderer emits the Shanghai Junneng `tm-pagination` structure, only the 20/50/100 options, total count, current/total pages, and disabled boundary buttons.
- [ ] Assert basis display overrides the year panel to grid column 1 and chart containers use `max(420px, calc(100vh - 260px))`.
- [ ] Run `node --test tests/data_visualization_frontend.test.mjs tests/iron_ore_basis_frontend.test.mjs`; expect the new assertions to fail because the existing pages still use load-more and fixed-height chart CSS.

### Task 2: Implement shared server-side pagination

**Files:**
- Modify: `frontend/data_visualization_components.js`
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Modify: `frontend/iron_ore_basis.js`

**Interfaces:**
- Produces: `DataVisualizationComponents.renderPagination(container, options)`.
- `options`: `{ page, pageSize, total, pageSizes, onPageChange, onPageSizeChange }`.
- Both controllers continue to consume existing API pagination `{ total, limit, offset, has_more }`.

- [ ] Add the shared renderer with `data-dv-component="server-pagination"`, `tm-pagination` markup, page-size select, previous/next buttons, total count, and current/total page text.
- [ ] Replace `dvDataLoadMoreBtn` and `ironOreBasisManagementLoadMore` markup with dedicated pagination containers.
- [ ] Change spot state to `dataPage: 1` and `dataPageSize: 20`; request only the selected page and render replacement rows rather than appending.
- [ ] Change basis state to `managementPage: 1` and `managementPageSize: 20` with the same behavior.
- [ ] Ensure filters and metric changes reset to page 1, while previous/next preserve filters and load only the requested page.
- [ ] Rerun the focused Node tests; expect all assertions to pass.

### Task 3: Correct filter alignment and expand the chart viewport

**Files:**
- Modify: `frontend/iron_ore_basis.css`
- Modify: `frontend/styles.css`

**Interfaces:**
- Consumes: existing grid-based `.dv-chart-controls` layout.
- Produces: basis year filter starts in column 1; both chart containers grow with viewport height without shrinking below 420px.

- [ ] Add a display-scoped basis override placing `.dv-year-panel` in grid column 1, leaving the product row unchanged.
- [ ] Replace the chart container’s capped `min(62vh, 620px)` height with `max(420px, calc(100vh - 260px))`.
- [ ] Rerun the focused tests and `node --check` for all modified JavaScript files.

### Task 4: Regression, Staging delivery, and real-surface acceptance

**Files:**
- Modify after successful Staging verification: `版本更新记录.md`

**Interfaces:**
- Consumes: committed Staging build.
- Produces: recorded T2 evidence and an explicit Staging-only release result.

- [ ] Run all Node frontend tests, relevant Python read-only API tests, JavaScript syntax checks, and `git diff --check`.
- [ ] Commit only task files and push `staging`, preserving unrelated working-tree files.
- [ ] In the in-app Browser verify the deployed asset version, basis year/product left alignment, chart viewport growth at a tall desktop viewport, 390×844 regression, and both management pagination flows at 20/50/100 with previous/next and filter reset.
- [ ] Confirm console error/warn count is zero and no write request or database change occurred.
- [ ] Record tests/release in AI SDLC and update `版本更新记录.md` after Staging passes.

## Requirement Traceability

| Requirement | Implementation | Automation | Staging acceptance |
|---|---|---|---|
| Basis year filter aligns with product | Scoped CSS grid-column override | CSS semantic assertion | Compare rendered left edges |
| Spot and basis chart areas fill tall-screen blank space | Shared chart-container viewport height | Responsive-height assertion | Tall desktop shows more chart rows; mobile remains usable |
| Both management pages use 20/50/100 standard pagination | Shared renderer plus page/page-size state | Shared-component and request-offset assertions | Exercise page size, next/previous, totals, and filter reset |

## Self-Review

- All three confirmed requirements map to code, automation, and real-surface evidence.
- No placeholder, database migration, business-rule change, export, or unrelated refactor is included.
- Both management pages use the same pagination interface and the existing APIs already support the required `limit/offset/total` contract.
