# Info Summary Cache Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a lightweight historical cache backfill mechanism for every `实时信息汇总` indicator, using `daily_prices` and `calculated_data` as the single future data source.

**Architecture:** Add a focused backend service for info-summary backfill, keep indicator-specific contract/formula rules in adapters, expose status/backfill APIs from `backend/app/main.py`, and add a small toolbar in the existing realtime info summary view. Tests use deterministic in-memory history providers; live data-source failures must return clear status and never fabricate historical values.

**Tech Stack:** FastAPI, existing SQLite/Postgres abstraction in `backend/app/db.py`, `backend/app/cache_service.py`, Python `unittest`, Node built-in test runner, optional AkShare historical source for domestic futures.

---

### Task 1: Backend Backfill Service

**Files:**
- Create: `backend/app/info_summary_backfill.py`
- Modify: `backend/app/cache_service.py`
- Test: `tests/test_info_summary_backfill.py`

- [ ] **Step 1: Write failing unit tests for adapter contracts and cache writes**

Create `tests/test_info_summary_backfill.py` with tests that:

```python
import unittest

from backend.app.info_summary_backfill import (
    BackfillRequest,
    StaticHistoryProvider,
    build_backfill_jobs,
    run_info_summary_backfill,
)
from backend.app.main import InfoCalculateIn


class InfoSummaryBackfillTest(unittest.TestCase):
    def test_build_backfill_jobs_covers_all_info_summary_types(self):
        request = BackfillRequest(calc_date="2026-06-23")
        jobs = build_backfill_jobs(request)
        self.assertEqual(
            [job.info_type for job in jobs],
            ["卷螺差", "螺矿比", "煤矿比", "盘面钢厂利润", "月差", "掉期月差", "内外盘差", "内外盘差2"],
        )

    def test_backfill_month_diff_writes_prices_and_calculated_values(self):
        provider = StaticHistoryProvider({
            "I2609": {"2026-06-18": 800.0, "2026-06-19": 805.0, "2026-06-22": 810.0, "2026-06-23": 812.0},
            "I2701": {"2026-06-18": 770.0, "2026-06-19": 772.0, "2026-06-22": 775.0, "2026-06-23": 777.0},
        })
        payload = InfoCalculateIn(
            info_type="月差",
            year=2026,
            calc_date="2026-06-23",
            year1=2026,
            month1="09",
            year2=2027,
            month2="01",
        )

        result = run_info_summary_backfill(payload, provider=provider)

        self.assertEqual(result.status, "success")
        self.assertEqual(result.info_type, "月差")
        self.assertGreaterEqual(result.price_rows_written, 6)
        self.assertGreaterEqual(result.calculated_rows_written, 1)
        self.assertEqual(result.latest_price_date, "2026-06-23")
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
.venv/bin/python -m unittest tests/test_info_summary_backfill.py
```

Expected: fails because `backend.app.info_summary_backfill` does not exist.

- [ ] **Step 3: Implement minimal backfill service**

Create `backend/app/info_summary_backfill.py` with:

- `BackfillRequest`
- `BackfillResult`
- `BackfillJob`
- `HistoryProvider`
- `StaticHistoryProvider`
- `AkshareHistoryProvider`
- `build_backfill_jobs(request)`
- `run_info_summary_backfill(payload, provider=None)`
- `run_all_info_summary_backfills(request, provider=None)`

Use existing helpers from `backend/app/main.py` for:

- `INFO_TYPES`
- `INNER_OUTER_MONTHS`
- `MONTH_DIFF_TYPES`
- `InfoCalculateIn`
- `cache_month_key`
- `indicator_contracts_for_cache`
- `value_from_cached_prices`

Use `save_daily_prices_batch`, `save_calculated_data`, and `get_all_prices_for_info_type` from `backend/app/cache_service.py`.

Minimum behavior:

- Determine required contracts per indicator.
- Pull provider history per contract.
- Upsert only non-null close prices.
- Build common dates where all required contracts have prices.
- Use rolling prior-window values to compute t-1, t-2, mean, min, max, std.
- Return `failed` when required history is unavailable.
- Do not write calculated rows when the values window is empty.

- [ ] **Step 4: Run unit test and verify GREEN**

Run:

```bash
.venv/bin/python -m unittest tests/test_info_summary_backfill.py
```

Expected: tests pass.

### Task 2: Backfill API and Status API

**Files:**
- Modify: `backend/app/main.py`
- Test: `tests/test_info_summary_backfill.py`

- [ ] **Step 1: Add failing API-shape tests**

Extend `tests/test_info_summary_backfill.py` to import and call:

```python
from backend.app.main import info_summary_cache_status
```

Add a test that asserts the status response contains:

```python
status = info_summary_cache_status(user={"id": 1})
self.assertIn("cache_counts", status)
self.assertIn("indicators", status)
self.assertIn("last_backfill", status)
```

- [ ] **Step 2: Run test and verify RED**

Run:

```bash
.venv/bin/python -m unittest tests/test_info_summary_backfill.py
```

Expected: fails because `info_summary_cache_status` does not exist.

- [ ] **Step 3: Add API models and routes**

Modify `backend/app/main.py`:

- Import backfill service functions.
- Add `InfoBackfillIn` Pydantic model with `info_type`, `calc_date`, `force`.
- Add `GET /api/info-summary/cache/status`.
- Add `POST /api/info-summary/cache/backfill`.

Route behavior:

- Require `info_summary` edit permission for backfill.
- Status route can use current-user access like config.
- Backfill route returns overall status plus per-indicator results.
- Log operation with module `info_summary`.

- [ ] **Step 4: Run backend tests**

Run:

```bash
.venv/bin/python -m unittest tests/test_info_summary_rules.py tests/test_info_summary_backfill.py
```

Expected: all tests pass.

### Task 3: Frontend Lightweight Entry

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Test: `tests/info_summary_frontend.test.mjs`

- [ ] **Step 1: Write failing frontend tests**

Extend `tests/info_summary_frontend.test.mjs` with assertions for:

```javascript
test("info summary exposes historical cache refresh entry", () => {
  assert.match(indexHtml, /refreshInfoCacheBtn/);
  assert.match(appJs, /\/api\/info-summary\/cache\/backfill/);
  assert.match(appJs, /\/api\/info-summary\/cache\/status/);
});
```

- [ ] **Step 2: Run test and verify RED**

Run:

```bash
node --test tests/info_summary_frontend.test.mjs
```

Expected: fails because the button and API calls do not exist.

- [ ] **Step 3: Add toolbar and JS handlers**

Modify existing realtime info summary toolbar/status area:

- Add `刷新历史缓存` button with id `refreshInfoCacheBtn`.
- Add a compact status text node for historical cache status.
- On `loadInfoSummary()`, call status API and render latest cache status.
- On button click, call backfill API, then reload status and recalculate cards.
- Surface partial/failed status in the same status area.

- [ ] **Step 4: Run frontend tests**

Run:

```bash
node --test tests/info_summary_frontend.test.mjs tests/pnl_colors.test.mjs
```

Expected: all tests pass.

### Task 4: Dependency, Verification, and Release Record

**Files:**
- Modify: `requirements.txt`
- Modify: `版本更新记录.md`
- Possibly modify: `frontend/index.html` cache-bust query for `app.js`

- [ ] **Step 1: Add historical source dependency only if required**

If the implemented domestic live history provider uses AkShare, add a pinned dependency to `requirements.txt`:

```text
akshare==1.16.98
```

If runtime verification shows a different current compatible version is required, pin that exact version and record the reason in the release note.

- [ ] **Step 2: Add versioned frontend script URL if `app.js` changed**

Update `frontend/index.html` script URL to a new cache-bust value:

```html
<script type="module" src="/static/app.js?v=info-cache-backfill-20260623"></script>
```

- [ ] **Step 3: Run full targeted verification**

Run:

```bash
.venv/bin/python -m unittest tests/test_info_summary_rules.py tests/test_info_summary_backfill.py
node --test tests/info_summary_frontend.test.mjs tests/pnl_colors.test.mjs
git diff --check
```

Expected: all commands pass.

- [ ] **Step 4: Update release record**

Add a Staging entry to `版本更新记录.md` after verification, noting:

- realtime info summary cache backfill mechanism
- whether database schema changed
- tests run
- no Production release yet

- [ ] **Step 5: Commit implementation**

Stage only files touched by this feature:

```bash
git add backend/app/info_summary_backfill.py backend/app/main.py backend/app/cache_service.py frontend/app.js frontend/index.html tests/test_info_summary_backfill.py tests/info_summary_frontend.test.mjs requirements.txt 版本更新记录.md
git commit -m "Add info summary cache backfill"
```

## Self-Review

- Spec coverage: plan covers full realtime info summary scope, single future cache source, legacy cache merge behavior, lightweight UI entry, FE status isolation, tests, and staging release notes.
- Placeholder scan: no implementation step depends on untracked placeholders; FE source can return explicit failure in first implementation if no stable source is verified.
- Type consistency: plan uses `InfoCalculateIn`, `cache_month_key`, `indicator_contracts_for_cache`, and `value_from_cached_prices` from existing backend code.
