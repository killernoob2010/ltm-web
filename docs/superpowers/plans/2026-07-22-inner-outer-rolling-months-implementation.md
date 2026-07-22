# Inner/Outer Rolling Months Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make both inner/outer spread cards display and calculate five consecutive contract months starting from the selected date month, including correct next-year contracts.

**Architecture:** A backend pure helper owns the authoritative year/month window used by quote dependencies, calculation, cache lookup, and API results. A matching frontend helper renders the same window immediately from existing date/year controls and keys values by `YYYY-MM` to keep cross-year results unambiguous.

**Tech Stack:** Python 3, FastAPI/Pydantic, browser JavaScript, Node test runner, Python unittest.

## Global Constraints

- Keep both existing inner/outer spread formulas unchanged.
- Do not change database schema or historical backfill behavior.
- Do not modify other information-summary indicators.
- Work only on the Staging/feature branch; Production remains out of scope.
- Preserve unrelated workspace changes.

---

### Task 1: Authoritative backend rolling window

**Files:**
- Modify: `backend/app/main.py`
- Test: `tests/test_info_summary_rules.py`

**Interfaces:**
- Produces: `inner_outer_contract_months(year: int, calc_date: str) -> list[dict[str, object]]`
- Produces: inner/outer `month_results` keyed by `YYYY-MM`, with `year` and `month` fields.

- [ ] **Step 1: Write failing tests**

Add assertions equivalent to:

```python
self.assertEqual(
    inner_outer_contract_months(2026, "2026-07-22"),
    [
        {"year": 2026, "month": "07"},
        {"year": 2026, "month": "08"},
        {"year": 2026, "month": "09"},
        {"year": 2026, "month": "10"},
        {"year": 2026, "month": "11"},
    ],
)
self.assertEqual(
    inner_outer_contract_months(2026, "2026-12-22")[-1],
    {"year": 2027, "month": "04"},
)
```

Also assert that December dependencies include `I2612` and `I2701`, and that calculation results use keys `2026-12` through `2027-04`.

- [ ] **Step 2: Verify RED**

Run:

```bash
/Users/wangjingze/Documents/轻量化交易管理系统WEB/.venv/bin/python -m unittest tests/test_info_summary_rules.py
```

Expected: failure because `inner_outer_contract_months` does not exist or fixed 05–09 behavior remains.

- [ ] **Step 3: Implement minimum backend change**

Add the pure five-month helper. Replace fixed-month loops in dependency collection, calculation, cache lookup, and response construction. For every window item, update both `year` and `month` on the monthly payload and key the response as `f"{year}-{month}"`.

- [ ] **Step 4: Verify GREEN**

Run the same unittest command. Expected: all tests pass.

### Task 2: Dynamic frontend labels and result mapping

**Files:**
- Modify: `frontend/app.js`
- Modify: `frontend/index.html`
- Test: `tests/info_summary_frontend.test.mjs`

**Interfaces:**
- Produces: `innerOuterContractMonths(year, calcDate)` returning five `{ year, month, key }` items.
- Produces: `renderInnerOuterMonthValues(card)` rebuilding the five visible month cells.

- [ ] **Step 1: Write failing frontend test**

Assert that the app contains a rolling-window helper, handles a December year rollover, keys rendered nodes with `YYYY-MM`, and registers `change` handlers for `.info-date` and `.info-year` on inner/outer cards.

- [ ] **Step 2: Verify RED**

Run:

```bash
node --test tests/info_summary_frontend.test.mjs
```

Expected: failure because the frontend still maps `state.infoConfig.inner_months`.

- [ ] **Step 3: Implement minimum frontend change**

Render five empty cells from the helper, update them after date/year changes, and map API results using `data-contract-month="YYYY-MM"`. Keep the existing card controls and styles. Update the `app.js` cache-busting query value in `frontend/index.html`.

- [ ] **Step 4: Verify GREEN**

Run the same Node command. Expected: all tests pass.

### Task 3: Regression, documentation, and Staging acceptance

**Files:**
- Modify after deploy: `版本更新记录.md`

**Interfaces:**
- Consumes: the backend and frontend behavior from Tasks 1 and 2.

- [ ] **Step 1: Run local regression**

```bash
/Users/wangjingze/Documents/轻量化交易管理系统WEB/.venv/bin/python -m unittest tests/test_info_summary_rules.py tests/test_info_summary_backfill.py
node --test tests/info_summary_frontend.test.mjs
node --check frontend/app.js
git diff --check
```

Expected: zero failures and zero syntax/diff errors.

- [ ] **Step 2: Commit and push feature branch, then integrate into Staging**

Stage only the spec, plan, target source files, tests, and post-deploy release record. Commit intentionally, fast-forward or merge into the current `staging` branch without touching unrelated files, and push `staging`.

- [ ] **Step 3: Verify real Staging surface**

Open `https://ltm-web-staging.onrender.com/?codex=<commit>`, log in if needed, enter `信息预警管理 → 实时信息汇总`, and confirm both cards show the current rolling five-month window. Exercise a date/year change that crosses December, confirm the five labels and returned values target the matching year/month keys, and check URL/title, meaningful DOM, no framework overlay, console health, and screenshot evidence.

- [ ] **Step 4: Record Staging result**

Append the real commit/deploy, test counts, browser evidence, database impact `无`, and rollback commit to `版本更新记录.md`. Do not claim or perform Production release.
