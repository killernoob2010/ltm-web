# Order Finance Display Rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove ambiguous order-finance labels and make the two second-row status fields fill the card width without wrapping.

**Architecture:** Keep the existing order-finance card renderer and data contract. Change only presentation helpers, user-facing labels, detail columns, and narrowly scoped CSS classes; no backend or persistence changes.

**Tech Stack:** Vanilla JavaScript, HTML templates, CSS Grid, Node test runner.

## Global Constraints

- Work only on `staging` and Render `ltm-web-staging`.
- Do not change WPS sync, backend logic, database schema, API fields, risk rules, or other modules.
- Test only the display rework scope requested by the user.
- Do not submit Gate B or release Production.

---

### Task 1: Lock the revised display semantics with failing tests

**Files:**
- Modify: `tests/order_finance_frontend.test.mjs`
- Test: `tests/order_finance_frontend.test.mjs`

**Interfaces:**
- Consumes: `renderOrderFinanceContract`, `orderFinanceDocumentText`, `renderOrderFinanceFinancingRows`, and `.order-finance-field` CSS.
- Produces: assertions for status-only document text, financing terminology, removed source detail, two-column wide fields, no-wrap text, and fixed field height.

- [ ] **Step 1: Write the failing assertions**

```js
assert.match(appJs, /if \(item\.document_date\) return "已交单"/);
assert.match(appJs, /return "待交单"/);
assert.doesNotMatch(appJs, /截止日未提供|待交单 \/ 截止/);
assert.doesNotMatch(appJs, /<th>来源<\/th>/);
assert.match(contractRenderer, /orderFinanceField\("融资到期日"[^\n]+"wide"\)/);
assert.match(contractRenderer, /orderFinanceField\("回款状态"[^\n]+"wide"\)/);
assert.match(stylesCss, /\.order-finance-field\.wide\s*\{[\s\S]*grid-column:\s*span 2/);
assert.match(stylesCss, /\.order-finance-field\.wide strong\s*\{[\s\S]*white-space:\s*nowrap/);
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `node --test tests/order_finance_frontend.test.mjs`

Expected: FAIL because the current renderer still shows dates, source detail, old labels, and quarter-width second-row cards.

### Task 2: Implement the minimum renderer and CSS change

**Files:**
- Modify: `frontend/app.js`
- Modify: `frontend/styles.css`
- Modify: `frontend/index.html`
- Test: `tests/order_finance_frontend.test.mjs`

**Interfaces:**
- Consumes: existing `orderFinanceField(label, value, tone)` calls and order-finance view model.
- Produces: `orderFinanceField(label, value, tone, extraClass)` with optional presentation class; no data contract change.

- [ ] **Step 1: Implement status-only document text and optional field classes**

```js
function orderFinanceField(label, value, tone = "", extraClass = "") {
  return `<div class="order-finance-field ${tone} ${extraClass}"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value || "-")}</strong></div>`;
}

function orderFinanceDocumentText(item) {
  if (item.document_date) return "已交单";
  return "待交单";
}
```

- [ ] **Step 2: Remove source detail and rename user-facing financing fields**

Remove the single-financing source value, header, and cell. Rename all order-finance UI occurrences of “回款到期日” to “融资到期日”, and the card field “回款日” to “回款状态”. Apply `wide` to the final two card fields.

- [ ] **Step 3: Add the two-column, fixed-height, no-wrap layout**

```css
.order-finance-field {
  height: 58px;
  box-sizing: border-box;
}

.order-finance-field.wide {
  grid-column: span 2;
}

.order-finance-field.wide strong {
  overflow: hidden;
  overflow-wrap: normal;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 1100px) {
  .order-finance-field.wide {
    grid-column: auto;
  }
}
```

- [ ] **Step 4: Bump the front-end asset versions**

Change the `app.js` and `styles.css` query versions in `frontend/index.html` to `order-finance-display-rework-20260715`.

- [ ] **Step 5: Run the focused test and verify GREEN**

Run: `node --test tests/order_finance_frontend.test.mjs`

Expected: all tests in the focused file pass.

### Task 3: Verify only the approved rework on Staging

**Files:**
- Modify after successful deploy: `版本更新记录.md`

**Interfaces:**
- Consumes: deployed `staging` commit and current Staging order-finance data.
- Produces: browser-visible evidence and a read-only five-anomaly detail list.

- [ ] **Step 1: Run scoped local verification**

Run:

```bash
node --test tests/order_finance_frontend.test.mjs
git diff --check
```

Expected: focused test file passes and whitespace check exits 0.

- [ ] **Step 2: Commit and push Staging**

Stage only the design, plan, focused test, order-finance frontend files, and post-deploy release record. Preserve unrelated dirty files.

- [ ] **Step 3: Verify the real Staging surface**

Open `https://ltm-web-staging.onrender.com/?codex=<commit>`, log in, open “订单融资进度”, expand one financing detail, and verify desktop plus narrow viewport. Confirm no relevant console errors.

- [ ] **Step 4: Read the anomaly details without mutation**

Use the deployed order-finance view builder or authenticated read-only API response to list each affected item/contract and its `data_issues` reasons. Do not update any business row.

- [ ] **Step 5: Stop before Gate B**

Report the Staging version, scoped test evidence, visible change, and five anomaly details. Wait for user acceptance.
