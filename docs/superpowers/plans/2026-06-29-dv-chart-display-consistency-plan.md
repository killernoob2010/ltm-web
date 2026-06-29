# Data Visualization Chart Display Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep data visualization chart filtering and rendering dimensions consistent after product selections change.

**Architecture:** Frontend owns UI filter intent and chart rendering mode. Backend normalizes conflicting aggregate/mainstream filters so direct API calls follow the same rule as the UI.

**Tech Stack:** FastAPI, SQLite/Postgres-compatible data access, vanilla JavaScript canvas rendering, Node test runner, pytest.

---

### Task 1: Document The Rule

**Files:**
- Create: `docs/data_visualization_chart_display_consistency_requirements.md`

- [ ] **Step 1: Record the user-facing rules**

Document that non-custom product pools own mainstream scope, `全品种图谱` always renders as atlas, and `品种对比图` always uses `品种 + 年份` legend entries.

### Task 2: Add Regression Tests

**Files:**
- Modify: `tests/data_visualization_frontend.test.mjs`
- Modify: `tests/test_data_visualization.py`

- [ ] **Step 1: Add frontend static tests**

Check that chart rendering no longer falls back from atlas when one product remains, compare mode uses product-year legend keys, and mainstream advanced filters are only sent for the custom product pool.

- [ ] **Step 2: Add backend aggregate conflict test**

Check that direct aggregate chart/table API calls prefer selected aggregate products over conflicting `mainstream_status`.

- [ ] **Step 3: Run targeted tests and confirm failure**

Run `node --test tests/data_visualization_frontend.test.mjs` and the new pytest case. Expected: both fail before implementation.

### Task 3: Implement The Minimal Fix

**Files:**
- Modify: `frontend/app.js`
- Modify: `frontend/index.html`
- Modify: `backend/app/data_visualization.py`

- [ ] **Step 1: Frontend filter normalization**

Only append `mainstream_status` when `product_pool=custom`; disable the mainstream advanced checkboxes for other product pools.

- [ ] **Step 2: Frontend chart rendering normalization**

Always route `viewMode=atlas` to atlas rendering, and always use product-year legend keys in compare mode.

- [ ] **Step 3: Backend aggregate conflict normalization**

For `product_pool=aggregate`, ignore `mainstream_status` and use selected aggregate product labels as the source of truth.

- [ ] **Step 4: Cache bust static assets**

Update `frontend/index.html` asset query strings.

### Task 4: Verify And Deploy Staging

**Files:**
- Modify only if needed: none

- [ ] **Step 1: Run full tests**

Run `.venv/bin/python -m pytest -q`, `node --test tests/*.mjs`, and `git diff --check`.

- [ ] **Step 2: Commit and push staging**

Commit the implementation and push `staging`.

- [ ] **Step 3: Verify staging**

Open `https://ltm-web-staging.onrender.com/?codex=<commit>` and verify loaded asset versions, chart filter behavior, and console health.
