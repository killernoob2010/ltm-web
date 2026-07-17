# Risk Alert Summary Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the approved risk-alert summary prototype to the real page so checkboxes are compact and history action buttons remain horizontally readable.

**Architecture:** Keep the current HTML and JavaScript rendering structure. Add page-specific CSS contracts for risk-alert checkboxes and history action layout, then update the stylesheet cache key in the existing HTML entrypoint.

**Tech Stack:** Static HTML, CSS, vanilla JavaScript, Node.js built-in test runner.

## Global Constraints

- Work only on the `staging` branch and the staging Render service.
- Do not change risk-alert APIs, database models, permissions, notifications, pagination, or JavaScript behavior.
- Match the approved prototype: 12 × 12 checkboxes, compact summary grid, vertically stacked action buttons with horizontal single-line labels.
- Do not touch unrelated existing worktree changes.

---

### Task 1: Lock the layout contract with a failing frontend test

**Files:**
- Modify: `tests/risk_alert_frontend.test.mjs`

**Interfaces:**
- Consumes: `frontend/styles.css` and `frontend/index.html` as text fixtures.
- Produces: regression assertions for checkbox size, compact summary columns, stacked action buttons, and the stylesheet cache key.

- [ ] **Step 1: Write the failing test**

```javascript
test("risk alert summary keeps compact checkboxes and horizontally readable actions", () => {
  assert.match(css, /#selectAllAlerts,[\s\S]*\.alert-select[\s\S]*width:\s*12px/);
  assert.match(css, /\.alert-history-summary-main\s*\{[\s\S]*gap:\s*8px/);
  assert.match(css, /\.alert-history-summary-main\s*>\s*\.row-actions\s*\{[\s\S]*flex-direction:\s*column/);
  assert.match(css, /\.alert-history-summary-main\s*>\s*\.row-actions\s+button\s*\{[\s\S]*white-space:\s*nowrap/);
  assert.match(html, /styles\.css\?v=risk-alert-summary-layout-20260717/);
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `node --test tests/risk_alert_frontend.test.mjs`

Expected: FAIL because the new layout selectors and cache key are absent.

### Task 2: Apply the approved layout with the minimum CSS change

**Files:**
- Modify: `frontend/styles.css`
- Modify: `frontend/index.html`

**Interfaces:**
- Consumes: Existing `.alert-history-summary-main`, `.row-actions`, `#selectAllAlerts`, and `.alert-select` elements.
- Produces: Page-specific rendering rules only; no JavaScript or API change.

- [ ] **Step 1: Add the compact checkbox contract**

```css
#selectAllAlerts,
.alert-select {
  width: 12px;
  height: 12px;
  min-height: 0;
  margin: 0;
  accent-color: var(--primary);
}
```

- [ ] **Step 2: Tighten the history summary grid and stack actions**

```css
.alert-history-summary-main {
  grid-template-columns: minmax(180px, 1.25fr) repeat(4, minmax(72px, 0.65fr))
    minmax(86px, 0.72fr) minmax(134px, 0.9fr) minmax(112px, auto);
  gap: 8px;
  padding: 10px 10px 10px 8px;
}

.alert-history-summary-main > .row-actions {
  min-width: 112px;
  flex-direction: column;
  align-items: stretch;
}

.alert-history-summary-main > .row-actions button {
  width: 100%;
  white-space: nowrap;
  writing-mode: horizontal-tb;
}
```

- [ ] **Step 3: Update the stylesheet cache key**

Change the `frontend/index.html` stylesheet reference to:

```html
/static/styles.css?v=risk-alert-summary-layout-20260717
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `node --test tests/risk_alert_frontend.test.mjs`

Expected: all risk-alert frontend tests pass.

### Task 3: Regression, staging delivery, and real-surface acceptance

**Files:**
- Modify after deployment: `版本更新记录.md`

**Interfaces:**
- Consumes: Staging commit and Render auto-deployment.
- Produces: Browser-visible acceptance evidence and a release record.

- [ ] **Step 1: Run relevant local regression**

Run: `node --test tests/risk_alert_frontend.test.mjs tests/data_visualization_frontend.test.mjs`

Expected: all tests pass with zero failures.

- [ ] **Step 2: Commit and push only scoped files**

```bash
git add docs/superpowers/plans/2026-07-17-risk-alert-summary-layout.md \
  tests/risk_alert_frontend.test.mjs frontend/styles.css frontend/index.html
git commit -m "style: refine risk alert summary layout"
git push origin staging
```

- [ ] **Step 3: Verify the real staging surface**

Open `https://ltm-web-staging.onrender.com/?codex=<commit>` in a clean in-app browser tab. Confirm page identity, static asset cache key, no relevant console errors, 12 × 12 checkbox geometry, no horizontal overflow, vertical action stacking, horizontal labels, and expand/collapse interaction.

- [ ] **Step 4: Record the staging result**

After the verified deployment, append a short staging entry to `版本更新记录.md`, commit it separately, and push `staging`.
