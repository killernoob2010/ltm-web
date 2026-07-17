# 期现页面最新日期刷新 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让期现管理和展示页每次激活时刷新左上角最新数据日期，并删除最优仓单区域的重复日期。

**Architecture:** 继续使用现有 management/display filters 接口作为最新日期权威来源。将“更新日期”与“首次构建筛选器”分离：前者每次激活执行，后者仍受 initialized 标志保护。

**Tech Stack:** Vanilla JavaScript, Node.js `node:test`, Render Staging.

## Global Constraints

- 仅修改期现前端日期刷新和展示。
- 不修改数据库、filters 接口返回结构、自动同步调度、EBC/新浪取数或基差计算。
- 只在现有 `staging` 工作区开发，保留用户未提交文件，不新建 worktree。
- 只发布 Render Staging；不操作 `main`、Production 或正式数据库。

---

### Task 1: 用回归测试锁定刷新和去重行为

**Files:**
- Modify: `tests/iron_ore_basis_frontend.test.mjs`
- Test: `tests/iron_ore_basis_frontend.test.mjs`

**Interfaces:**
- Consumes: `frontend/index.html` 中期现展示 DOM，`frontend/iron_ore_basis.js` 中 `initManagement()` / `initDisplay()` 激活逻辑。
- Produces: 静态语义回归断言，确保每次激活都刷新 filters，筛选器只初始化一次，最优仓单无重复日期。

- [ ] **Step 1: 改写现有日期状态测试并增加激活刷新断言**

```js
test("basis pages refresh the latest stored data date on every activation", () => {
  assert.match(basisJs, /async function loadManagementStatus\(\)/);
  assert.match(basisJs, /async function loadDisplayStatus\(\)/);
  assert.match(basisJs, /async function initManagement\(\) \{[\s\S]*await loadManagementStatus\(\)/);
  assert.match(basisJs, /async function initDisplay\(\) \{[\s\S]*await loadDisplayStatus\(\)/);
  assert.doesNotMatch(basisJs, /optimalDate\.textContent = "数据截至 "/);
  assert.doesNotMatch(indexHtml, /id="ironOreBasisOptimalDate"/);
});
```

- [ ] **Step 2: 运行专项测试，确认新断言先失败**

Run: `node --test tests/iron_ore_basis_frontend.test.mjs`

Expected: FAIL，原因为 `loadManagementStatus` / `loadDisplayStatus` 尚不存在，且 `ironOreBasisOptimalDate` 仍存在。

### Task 2: 实现每次激活刷新日期

**Files:**
- Modify: `frontend/iron_ore_basis.js`
- Modify: `frontend/index.html`
- Test: `tests/iron_ore_basis_frontend.test.mjs`

**Interfaces:**
- Consumes: `GET /api/iron-ore-basis/management/filters` 和 `GET /api/iron-ore-basis/display/filters` 返回的 `latest_data_date`。
- Produces: `loadManagementStatus(): Promise<object>` 和 `loadDisplayStatus(): Promise<object>`，两者在更新 DOM 后返回 filters 供首次初始化复用。

- [ ] **Step 1: 拆分管理页状态请求和筛选器初始化**

```js
async function loadManagementStatus() {
  var filters = await request("/api/iron-ore-basis/management/filters");
  managementLatestDate.textContent = "最新数据日期：" + (filters.latest_data_date || "暂无数据");
  return filters;
}

function initializeManagementFilters(filters) {
  // 保留现有三组 renderCheckboxOptions 和 bindCheckboxPanelActions。
}

async function initManagement() {
  var filters = await loadManagementStatus();
  if (!basisState.managementInitialized) {
    initializeManagementFilters(filters);
    basisState.managementInitialized = true;
  }
  await loadManagementRows(false);
}
```

- [ ] **Step 2: 以同样边界拆分展示页状态请求**

```js
async function loadDisplayStatus() {
  var filters = await request("/api/iron-ore-basis/display/filters");
  displayLatestDate.textContent = "最新数据日期：" + (filters.latest_data_date || "暂无数据");
  return filters;
}

function initializeDisplayFilters(filters) {
  // 保留现有两组 renderCheckboxOptions 和 bindCheckboxPanelActions。
}

async function initDisplay() {
  var filters = await loadDisplayStatus();
  if (!basisState.displayInitialized) {
    initializeDisplayFilters(filters);
    basisState.displayInitialized = true;
  }
  await Promise.all([loadOptimalWarrant(), loadBasisChart()]);
}
```

- [ ] **Step 3: 删除最优仓单的重复日期 DOM 和写入逻辑**

```html
<div class="panel-head">
  <h2>最优仓单测算</h2>
</div>
```

同时删除 `optimalDate` 元素查询、清空和 `"数据截至 " + row.data_as_of` 写入。

- [ ] **Step 4: 更新静态资源版本**

```html
<script src="/static/iron_ore_basis.js?v=iron-ore-basis-date-refresh-20260716"></script>
```

CSS 文件内容未变，不修改 CSS 版本。

- [ ] **Step 5: 运行专项和全量前端测试**

Run: `node --test tests/iron_ore_basis_frontend.test.mjs`

Expected: 期现前端专项全部 PASS。

Run: `node --test tests/*.test.mjs`

Expected: 无新增失败；如存在基线失败，必须先用修改前提交复现并记录。

- [ ] **Step 6: 运行项目级辅助检查**

Run: `env -u DATABASE_URL .venv/bin/python -m pytest -q`

Expected: Python 回归无新增失败。

Run: `git diff --check`

Expected: exit 0。

- [ ] **Step 7: 精确提交实现文件**

```bash
git add frontend/iron_ore_basis.js frontend/index.html tests/iron_ore_basis_frontend.test.mjs
git commit -m "fix: refresh basis latest date on activation"
```

### Task 3: 部署并验收 Render Staging

**Files:**
- Modify after successful deploy: `版本更新记录.md`

**Interfaces:**
- Consumes: `staging` 分支提交、Render `ltm-web-staging`、测试库 `iron_ore_basis_results`。
- Produces: 真实页面验收证据和发布记录。

- [ ] **Step 1: 推送 `staging` 并等待 Render 部署为 live**

```bash
git push origin staging
```

- [ ] **Step 2: 在测试库只读核对最大业务日期**

```sql
SELECT MAX(business_date) AS latest_data_date
FROM iron_ore_basis_results;
```

Expected: 返回当前真实最新日期，验收期间不写入数据。

- [ ] **Step 3: 在干净浏览器页签执行 T1 验收**

1. 打开 `https://ltm-web-staging.onrender.com/?codex=<commit>` 并以访客身份进入。
2. 进入“期现数据展示”，确认左上角日期与 SQL 一致，页面无“数据截至”。
3. 取消一个年份或品种勾选，切换到其他页面再返回，确认选择保留且日期重新请求。
4. 进入“期现数据管理”重复日期一致性检查。
5. 核对 URL、页面标题、`iron-ore-basis-date-refresh-20260716` 静态资源和控制台 error/warn。

- [ ] **Step 4: 部署成功后更新版本记录并提交**

```bash
git add 版本更新记录.md
git commit -m "docs: record basis date refresh staging verification"
git push origin staging
```

Expected: 文档提交部署为 live，运行时仍使用测试版环境，正式版无任何变更。
