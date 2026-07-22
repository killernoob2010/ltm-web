# Trading Ledger Valuation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make business ledgers classified-only, add business-type overview filtering, and provide auditable Shanghai Junneng settlement plus read-only live valuation for futures and options.

**Architecture:** Keep imported settlement facts immutable. Apply business filters through assignment/allocation rows, put deterministic settlement and valuation formulas in a focused backend module, and inject a read-only quote provider whose volatile results are returned but never persisted into fact tables.

**Tech Stack:** FastAPI, SQLite/PostgreSQL-compatible SQL, vanilla JavaScript, pytest, Node test runner, optional TqSdk read-only market-data dependency.

## Global Constraints

- Work only on the staging feature branch; do not touch `main`, Production, Production data, or Production environment variables.
- Business ledgers show classified records only; the overview `全部` option still includes unclassified facts.
- The quote adapter may authenticate to market data only and must not construct or call trading-account or order APIs.
- Live values never overwrite imported facts; unavailable inputs return explicit status rather than fabricated zeroes.
- Shanghai Junneng rule version is exactly `sh_junneng_v1`; same-day interest counts as one day.
- Option display Greeks are position exposures using direction, remaining quantity, and contract multiplier.

---

### Task 1: Business filters and classified-only ledgers

**Files:**
- Modify: `backend/app/trading_management.py`
- Modify: `frontend/trading_management.js`
- Test: `tests/test_trading_management.py`
- Test: `tests/trading_management_frontend.test.mjs`

**Interfaces:**
- Extend `FactFilters` with `business_type: str = ""`.
- Accept only `""`, `basic_hedging`, and `strategic_hedging`.
- `query_business_rows()` returns only rows backed by `trading_business_assignments`.

- [ ] Write backend tests proving unclassified futures/options are absent from all three business tabs and mixed close allocations include only classified portions.
- [ ] Run the targeted tests and confirm they fail because current business views include candidates/unclassified options.
- [ ] Implement classified-only SQL/data filtering and business-type overview filtering at assignment/allocation grain.
- [ ] Write frontend tests for the three-option overview control and removal of classification controls/candidate notices from business ledgers.
- [ ] Run backend and frontend targeted tests to green.

### Task 2: Shanghai Junneng settlement and floating PnL

**Files:**
- Create: `backend/app/trading_valuation.py`
- Modify: `backend/app/trading_management.py`
- Test: `tests/test_trading_valuation.py`
- Test: `tests/test_trading_management.py`

**Interfaces:**
- Produce `calculate_sh_junneng_settlement(...) -> dict` with `net_close_pnl`, `fund_interest`, `settlement_80`, `settlement_20`, and `settlement_rule_version`.
- Produce `calculate_position_floating_pnl(...) -> float`.

- [ ] Write failing formula tests for long/short floating PnL, partial open-fee allocation, same-day interest, loss with interest, and positive 80/20 distribution.
- [ ] Implement the pure calculation functions with contract multiplier and rule version `sh_junneng_v1`.
- [ ] Write failing business-query tests for default current-month closes and classified-only settlement summaries.
- [ ] Join open-trade fees and proportionally allocated close fees at business allocation grain, then aggregate the new summary fields.
- [ ] Run valuation and trading-management targeted tests to green.

### Task 3: Read-only quote service and option exposures

**Files:**
- Modify: `backend/app/trading_valuation.py`
- Modify: `backend/app/trading_management.py`
- Modify: `requirements.txt`
- Modify: `.env.example`
- Test: `tests/test_trading_valuation.py`
- Test: `tests/test_trading_management.py`

**Interfaces:**
- Define a provider-independent `QuoteSnapshot` result containing market/valuation price, source, timestamp, status, multiplier, underlying, expiry, IV, and unit Greeks.
- TqSdk credentials come only from `TQSDK_USERNAME` and `TQSDK_PASSWORD`.
- Valuation price priority is last trade, then bid/ask midpoint, then latest imported settlement price.

- [ ] Write failing quote-selection tests covering last, midpoint, settlement reference, unavailable, and expired states.
- [ ] Write failing option tests for call/put, long/short, multiple lots, multiplier, floating PnL, and Delta/Gamma/Theta/Vega position exposures.
- [ ] Implement an injectable quote service with an in-memory TTL cache and a TqSdk adapter that imports lazily and exposes no trading operations.
- [ ] Enrich classified current positions on read without persisting quote or Greek results.
- [ ] Run targeted tests to green without requiring live credentials.

### Task 4: Frontend valuation display and refresh

**Files:**
- Modify: `frontend/trading_management.js`
- Modify: `frontend/trading_management.css`
- Modify: `frontend/index.html`
- Test: `tests/trading_management_frontend.test.mjs`

**Interfaces:**
- Business position rows consume valuation source/time/status fields.
- Option rows consume IV and position-exposure Greek fields.
- Poll only the visible Junneng/options positions tab every 15 seconds and stop on page/tab change.

- [ ] Write failing frontend contract tests for new columns, Junneng summaries, current-month default, and one guarded 15-second poller.
- [ ] Implement the minimal table/summary/status rendering and isolated date state per business ledger.
- [ ] Add cache-busting asset versions.
- [ ] Run frontend tests and JavaScript syntax checks to green.

### Task 5: Integrated verification and staging

**Files:**
- Modify after deploy: `README.md`
- Modify after deploy: `版本更新记录.md`

- [ ] Run full Python and Node suites, `compileall`, JavaScript syntax checks, and `git diff --check`.
- [ ] Run the real June statement regression and verify imported fact totals remain unchanged.
- [ ] Perform an independent whole-branch review and fix all blocking findings.
- [ ] Commit and push the feature result to `staging`, then verify Render Staging with the in-app browser and the deployed asset version.
- [ ] Record staging test evidence and update release documentation only after deployment succeeds.
