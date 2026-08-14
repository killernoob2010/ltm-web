# Order Lifecycle Prototype Fidelity and Staging Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align the AIO-20260809-002 order-lifecycle Staging page with the latest approved prototype and business rules, while preserving authoritative WPS identifiers, making risk facts truthful, removing misleading over-order financing content, and proving the page is performant and usable on the real Staging surface.

**Architecture:** Keep the existing system shell, permissions, source reconciliation, database schema, and lazy detail endpoint. Separate three responsibilities: (1) backend source/risk/card facts, (2) frontend semantic rendering and layout, and (3) list-query aggregation/page bounds. The list response will contain only summary and current-page card aggregates; full child/source/audit records remain detail-only. No identifier generator or Production data path will be added.

**Tech Stack:** FastAPI, SQLite/PostgreSQL-compatible SQL, vanilla JavaScript, CSS, pytest, Node test runner, Computer Use/in-app Browser for real Staging verification.

**Spec:** `docs/2026-08-14-order-lifecycle-fidelity-performance-business-requirements.md`, with the latest user decision overriding the older left-side-detail-nav wording: the final detail navigation is a complete 01–08 horizontal row at the top.

## Global Constraints

- Existing AIO-20260809-002 only; do not create a new AIO number or successor work item.
- Existing branch `codex/aio-20260809-002-order-lifecycle-v1` only; do not create a feature branch.
- Local and Staging only. Do not read, write, migrate, or deploy Production data or configuration.
- WPS authoritative business numbers must be copied from source. Never generate `XYZ-年份-数字`, steel-mill-plus-row, filename-plus-row, random, or placeholder business numbers.
- A financing record without a real WPS identifier remains a pending match candidate; it must not become a temporary parent card.
- Over-order cards use the approved real purchase-contract identifier when that is the authoritative source; the UI must not fabricate another number.
- Current user copy rule: the main-card type badge uses the short label `过单`; the filter and detail classification use `过单类业务`. This must be documented and tested consistently.
- The latest user decision makes the 01–08 detail navigation horizontal. It must show all eight items without page-level horizontal clipping, with a solid background flush to the top bar.
- Preserve current permission checks, source reconciliation, manual-value precedence, audit records, and sensitive-operation boundaries.
- Do not delete or mass-rewrite Staging audit history. Any legacy-number data correction must be snapshot-backed, uniquely evidenced, transactional, and reversible.
- Every user-visible time remains truncated to seconds; no milliseconds or fractional seconds may be introduced.
- No unrelated module, import flow, trading operation, or Production release is in scope.

## Gate A assessment and traceability

- **D3:** one business module spanning its source parser, persistence, API, page renderer, and detail interaction. It is not D4 because no second independently owned business module or new system boundary is introduced.
- **T3:** integrated source/logic/persistence/API/real-UI acceptance for the order-lifecycle module. It is not T4 because the change does not create a cross-module business flow.
- **R3:** core business status/risk semantics, identifier governance, Staging data reconciliation, and database query behavior are business-impacting and require explicit rollback evidence even though Production is forbidden.
- **C1:** one top-level agent in the new conversation; no child-agent delegation.
- **Impact scope:** `whole_module` for order-lifecycle logic and UI, with focused regression of existing order-finance import/status tests. The unchanged surface is the system shell, auth, unrelated modules, and Production.

Traceability IDs used in every task:

| ID | Requirement | Automation | Real-surface evidence |
|---|---|---|---|
| `OL-ID-01` | Use source-authoritative numbers; never generate or silently rename | WPS/email negative tests and candidate-reason tests | Search default list and legacy-ID search in Staging |
| `OL-FILTER-01` | Four primary filter groups in one row, status in a second row, stable search/clear | DOM/behavior tests for group logic, wrapping, clear reset | Computer Use at desktop viewport |
| `OL-CARD-01` | Compact wide card, colored type badges, explicit identity and financing/execution facts | Card payload and frontend semantic assertions | Financing and over-order cards in Staging |
| `OL-RISK-01` | Risk color belongs to the affected fact, including shipment anomalies | Backend risk-fact tests and frontend tone assertions | High-risk financing card with missing/late shipment |
| `OL-PASS-01` | Over-order card has no financing placeholders; shows settlement/risk/next step | Backend serialization and DOM absence tests | Over-order-only filter in Staging |
| `OL-DETAIL-01` | Horizontal 01–08 nav, solid sticky top, no page clipping, full hero, scroll restoration | Detail structure/state tests | Open, scroll, return, and inspect real detail page |
| `OL-PERF-01` | Summary over all matches, page-only child reads, ≤2s warm actions, 200ms feedback | 200-row query/round-trip tests and timing capture | Staging warm entry/search/filter/page/detail timings |
| `OL-STAGE-01` | Staging-only release, recovery point, version identity, rollback evidence | Release/diff checks | Browser URL/build identity and release record |

## Task 1: Freeze the latest requirement contract and establish the baseline

**Files:**

- Modify: `docs/2026-08-14-order-lifecycle-fidelity-performance-business-requirements.md`
- Modify: `docs/superpowers/plans/2026-08-14-order-lifecycle-fidelity-performance.md`
- Test: `tests/order_lifecycle_frontend.test.mjs`, `tests/test_order_lifecycle.py`

**Interfaces:**

- Produces the final copy/layout contract used by all later tasks.
- Produces the baseline test result and baseline commit/version identity.

- [x] **Step 1: Record the actual baseline.**

Run:

```bash
git status --short --branch
git rev-parse HEAD
pytest -q tests/test_order_lifecycle.py tests/test_order_finance.py
node --test tests/order_lifecycle_frontend.test.mjs
```

Expected: the current branch is `codex/aio-20260809-002-order-lifecycle-v1`; record the exact commit and every baseline failure without treating a source-string test as visual acceptance.

Recorded 2026-08-14 baseline: the existing branch worktree was clean at plan commit `87ae1ad`; `origin/staging` and the remote lifecycle branch were at code commit `8ed4af9`. The targeted Python baseline passed `115` tests and the Node lifecycle contract passed `8` tests. The real Staging login page served lifecycle asset version `20260814-order-lifecycle-prototype-rebuild-v3`, matching `8ed4af9`.

- [x] **Step 2: Resolve documentation conflicts.**

Update the requirements document so it explicitly states:

```text
主卡显示短标签“过单”；筛选器和详情分类显示“过单类业务”。
融资业务编号只复制 WPS 真实业务项次；缺失或旧钢厂行号只能进入待匹配候选。
详情 01–08 导航按最新用户决定放在页面顶部横排；旧的左侧导航文字不再作为本轮实施基准。
```

Update the structural test contract in the same change. Do not alter the allowed desktop-only scope or the Production prohibition.

- [x] **Step 3: Commit only the contract/baseline documentation.**

```bash
git add docs/2026-08-14-order-lifecycle-fidelity-performance-business-requirements.md docs/superpowers/plans/2026-08-14-order-lifecycle-fidelity-performance.md
git commit -m "docs: freeze order lifecycle fidelity contract"
```

## Task 2: Enforce source-authoritative identifiers and guard legacy Staging data

**Files:**

- Modify: `backend/app/order_lifecycle.py:585-603, 941-969, 1790-1825`
- Test: `tests/test_order_lifecycle.py:419-432` and new identifier tests beside the existing source-ingestion tests

**Interfaces:**

- `apply_source_batch()` continues to return `pending_match_candidates` and never allocates a replacement identifier.
- `_is_legacy_mill_row_business_no(value)` remains the guard for legacy steel-mill/row labels.
- Any preview/mapping helper must return `{legacy_business_no, source_type, source_record_key, candidate_business_ids, authoritative_business_no, decision}` and must not write by default.

- [x] **Step 1: Add failing identifier tests.**

Add tests with these exact assertions:

```python
def test_financing_without_wps_business_no_never_creates_temporary_parent(lifecycle_db):
    result = apply_source_batch(_batch([_email_record("P-UNMATCHED", business_type="融资")]))
    assert result["created_businesses"] == 0
    assert result["pending_match_candidates"] == 1
    assert list_businesses({"page": 1, "page_size": 20})["total"] == 0


def test_legacy_identifier_reason_does_not_request_xyz_generation(lifecycle_db):
    result = apply_source_batch({**_batch([_wps_record("北满-17", "P-LEGACY-17", "SYS-LEGACY-17")]), "source_type": "wps"})
    assert result["created_businesses"] == 0
    with db.connect() as conn:
        reason = conn.execute("SELECT reason FROM order_lifecycle_match_candidates WHERE status = 'open'").fetchone()["reason"]
    assert "禁止生成" in reason
    assert "真实 WPS 业务编号" in reason
    assert "XYZ-年份-序号格式" not in reason
```

Run:

```bash
pytest -q tests/test_order_lifecycle.py -k 'temporary_parent or legacy_identifier'
```

Expected: the new reason assertion fails against the current XYZ wording before implementation.

- [x] **Step 2: Change only the source guard and error contract.**

Keep `_normalize_business_no()` as normalization only. Keep missing-financing-ID records in candidates. Change the WPS legacy reason to say that the real WPS business number must be read back and that no replacement number is generated. Do not use steel mill, filename, sheet, row, sequence, or random values for `business_no`.

- [x] **Step 3: Add a read-only legacy mapping preview.**

Implement a pure helper in `backend/app/order_lifecycle.py` that reads current legacy cards and their saved source/contracts, proposes only exact unique WPS matches, and returns `decision` values `unique`, `conflict`, or `no_evidence`. It must not update rows. Add tests for all three decisions. Do not add a broad string-replace migration.

- [x] **Step 4: Re-run identifier and import regressions.**

```bash
pytest -q tests/test_order_lifecycle.py -k 'identifier or candidate or import'
```

Expected: no new parent is created without a source-authoritative identifier; no old identifier is silently overwritten.

## Task 3: Make backend card facts semantically complete and risk-specific

**Files:**

- Modify: `backend/app/order_lifecycle.py:1330-1385, 2055-2191`
- Test: `tests/test_order_lifecycle.py`

**Interfaces:**

- `_serialize_business_card()` returns `risk_facts` with these keys: `shipment`, `document`, `due`, `bank_repayment`, `customer_receipt`, and `data_status`.
- Each risk fact is `{level: "high"|"medium"|"low"|"none", reason: str}`; no level is inferred from the overall card risk when the fact itself has no reason.
- Financing cards return `financing_banks`, `outstanding_financing_amount`, `financing_count`, `repayment_progress`, and a source-backed `drawdown_display` or `待来源回读`; never synthesize `1/1` from the existence of one row.
- Over-order cards return `settlement_status`, `risk_reasons`, `customer_receipt_progress`, and `next_action`, without financing aggregates.

- [x] **Step 1: Add failing risk-fact tests.**

Add tests for:

```python
def test_missing_latest_shipment_date_is_exposed_as_shipment_risk_fact(lifecycle_db):
    record = _wps_record("Y-2026-16", "P-016", "SYS-016")
    record["vessels"] = [{"vessel_name": "V1", "latest_shipment_date": "", "source_key": "v1"}]
    apply_source_batch({**_batch([record]), "source_type": "wps"})
    card = list_businesses({"page": 1, "page_size": 20})["records"][0]
    assert card["risk_facts"]["shipment"]["level"] == "high"
    assert "最迟装船日" in card["risk_facts"]["shipment"]["reason"]
    assert card["risk_facts"]["due"]["level"] == "none"


def test_over_order_card_has_settlement_and_no_financing_payload(lifecycle_db):
    apply_source_batch(_batch([_record(business_type="过单")]))
    card = list_businesses({"page": 1, "page_size": 20})["records"][0]
    assert card["business_type"] == "过单"
    assert "settlement_status" in card
    assert "financing_banks" not in card
    assert "outstanding_financing_amount" not in card
```

Run the tests and confirm the missing shipment fact and over-order payload fail before implementation.

- [x] **Step 2: Separate anomaly and risk derivation.**

Retain the anomaly row `missing:latest_shipment_date` for data-quality filtering and detail audit, but also derive a shipment fact risk when the current business node makes shipment risk decision-relevant. Preserve the overall `risk_reasons` list and do not duplicate the words “高风险” or “中风险” inside fact values.

- [x] **Step 3: Add type-specific card aggregates.**

Keep financing-only calculations behind `business_type == "融资"`. For `过单`, serialize settlement, customer receipts, risk reasons, data status, and next action only. Use `待来源回读` when a source fact is absent; never use an example value.

- [x] **Step 4: Run backend regression.**

```bash
pytest -q tests/test_order_lifecycle.py tests/test_order_finance.py
```

Expected: the existing status, repayment, FCR, source-conflict, and import behavior remains unchanged except for the explicitly corrected risk/card contract.

## Task 4: Bound list aggregation and prove performance behavior

**Files:**

- Modify: `backend/app/order_lifecycle.py:2130-2425`
- Test: `tests/test_order_lifecycle.py`

**Interfaces:**

- `list_businesses(filters)` returns the same public shape: `summary`, `records`, `total`, `page`, `page_size`, and `sync_status`.
- Summary is computed over all filtered matches; child rows are loaded only for the current page in overview mode.
- Focus mode uses a lightweight projection/aggregate and does not deserialize all six child collections for every matching business.

- [x] **Step 1: Add a 200-row query-bound test.**

Extend the existing bounded-query test with 200 parent records and page size 20. Assert:

```python
assert result["total"] == 200
assert len(result["records"]) == 20
assert result["summary"]["存续业务"] == 200
assert all(len(batch) <= 20 for batch in child_loads)
assert select_count <= 20
```

Add a focus-mode assertion that `_load_business_children_batch` is not called with all 200 IDs.

- [x] **Step 2: Implement summary aggregates without full child serialization.**

Keep parent filtering and deterministic ordering intact. Replace all-match full financing/repayment row loading used only for the summary with grouped aggregate queries keyed by `business_id`. Use the filtered ID set only for summary aggregates, then use the page ID set for card child facts. Do not return full source JSON, complete child arrays, or audit history from the list endpoint.

- [x] **Step 3: Add/verify supporting indexes.**

Use the existing schema migration pattern to verify indexes on `business_type/status/risk_level/business_no`, every child table `business_id`, open anomalies `(business_id,status,anomaly_type)`, and source business keys. Add only missing indexes and test migration idempotence for SQLite/PostgreSQL compatibility.

- [x] **Step 4: Verify summary and page-size invariants.**

```bash
pytest -q tests/test_order_lifecycle.py -k 'summary or bounded or page or focus'
```

Expected: 20, 50, and 100 page sizes return consistent totals and summaries, while details remain lazy.

## Task 5: Rebuild the main list rendering to the approved compact card contract

**Files:**

- Modify: `frontend/index.html:550-585`
- Modify: `frontend/app.js:3350-3630`
- Modify: `frontend/styles.css:1176-1260, 3228-3385`
- Test: `tests/order_lifecycle_frontend.test.mjs`

**Interfaces:**

- Existing IDs remain stable: `orderLifecycleSearchBtn`, `orderLifecycleKeyword`, `orderLifecycleFilters`, `orderLifecycleStatusRow`, `orderLifecycleCards`, and pagination controls.
- `renderOrderLifecycleCard(item)` renders one of two semantic templates: financing or over-order. It must not render hidden/empty financing placeholders for over-order.
- `risk_facts` controls fact-level classes; `risk_level` controls only the overall badge and card edge.

- [x] **Step 1: Add failing frontend contracts.**

Add Node assertions that the lifecycle block contains the four first-row filter groups, a distinct status row, type-specific card templates, settlement markup for over-order, and no `过单业务不适用` placeholder. Add assertions that the type badge has financing/pass CSS classes with backgrounds and that risk classes are attached to individual facts rather than the entire risk section.

Run:

```bash
node --test tests/order_lifecycle_frontend.test.mjs
```

Expected: the new semantic assertions fail against the current renderer/CSS.

- [x] **Step 2: Fix filter DOM and grid rules.**

Keep search on its own row. Put business type, risk, anomaly, and FCR in one explicit four-column grid. Put status in a separate full-width row. Give search, clear, mini-action, and checkbox controls one height/typography contract. Remove or override every legacy `grid-column: span 3/6` selector that still matches nested lifecycle fieldsets.

- [x] **Step 3: Render compact identity and type badges.**

Use labeled identity fields in the prototype order: contract, trade entity, supplier steel mill, product, quantity, terminal customer. Add background classes for financing and pass-through badges. Use `融资类业务` for financing and the approved short `过单` label on the main card, with an accessible full classification.

- [x] **Step 4: Render type-specific card bodies.**

Financing card sections must show source-backed bank, outstanding amount, financing count, drawdown display, port status, shipment/latest-shipment facts, document status, due/extension, customer receipt, bank repayment, next step. Over-order cards must show execution, customer receipts, settlement status, risk reason/data status, and next step only.

- [x] **Step 5: Apply field-level risk tones.**

Use `risk_facts.shipment`, `risk_facts.document`, `risk_facts.due`, `risk_facts.bank_repayment`, `risk_facts.customer_receipt`, and `risk_facts.data_status` to add `fact-danger`/`fact-warning`. Remove the whole-section `risk-danger`/`risk-warning` tint. Unaffected facts retain the neutral background.

- [x] **Step 6: Run frontend contracts and a page-start smoke.**

```bash
node --test tests/order_lifecycle_frontend.test.mjs
python3 -m compileall backend/app
```

Expected: all lifecycle structural/semantic tests pass and the page still boots without a JavaScript syntax error.

## Task 6: Make detail layout complete, horizontal, solid, and stateful

**Files:**

- Modify: `frontend/app.js:3432-3525, 3658-3710`
- Modify: `frontend/styles.css:287-302, 1248-1263, 3414-3770`
- Test: `tests/order_lifecycle_frontend.test.mjs`

**Interfaces:**

- `renderOrderLifecycleDetail(detail)` exposes hero fields for business number, type, status, risk, FCR, financing count, identity, next action, source summary, source update, last editor, and last modification time.
- The 01–08 nav remains a top horizontal `nav` with eight links and uses a solid background; it may internally scroll on narrow desktop widths, but the page itself must not clip.
- `saveOrderLifecycleViewState()` stores `scrollTop`; `restoreOrderLifecycleViewState()` restores it only after the list has rendered and the saved query state matches.

- [x] **Step 1: Add failing detail/state assertions.**

Require eight nav anchors, hero FCR/financing-count/next-step/source labels, a solid nav background declaration, and a saved/restored scroll value. Keep the existing assertion that no per-node confirmation controls are rendered.

- [x] **Step 2: Complete the detail hero.**

Move the required summary facts into the hero without duplicating contradictory status text. Keep the unified edit entry and existing sensitive-operation permission checks. Keep full child/source/audit rows lazy-loaded in sections.

- [x] **Step 3: Fix horizontal overflow boundaries.**

Allow the detail page to occupy the available content width. Give wide tables their own `overflow-x: auto` wrapper. Make long cell values wrap where appropriate. Do not hide the entire workspace overflow. Keep return control and the top nav visible.

- [x] **Step 4: Fix sticky nav.**

Set the nav top offset to the actual workspace topbar height, use an opaque surface background, a bottom border/shadow, and a higher z-index than detail content. Keep all eight items in one horizontal row at the approved desktop viewport; use internal horizontal scrolling only if a smaller desktop width requires it.

- [x] **Step 5: Restore list position.**

Capture `window.scrollY` before opening detail and write it into session state. After returning and rendering the list, restore the saved value on the next animation frame. Preserve keyword, filters, view, page, and page size as before.

- [x] **Step 6: Run detail contracts.**

```bash
node --test tests/order_lifecycle_frontend.test.mjs
```

Expected: all eight sections, hero fields, solid nav, no page-level clipping contract, and state persistence assertions pass.

## Task 7: Staging-only data review and controlled legacy-ID handling

**Files:**

- No Production files or databases.
- Use the read-only preview helper from Task 2 and the project-approved Staging backup/snapshot process.
- Update only: `版本更新记录.md` after successful Staging deployment.

**Interfaces:**

- Preview output is the only input to any Staging mapping decision.
- A mapping can be applied only when exactly one WPS authoritative number is proven and there is no target collision.
- Conflicts, missing evidence, and duplicate target numbers remain pending; no page-level placeholder or generated replacement is allowed.

- [x] **Step 1: Capture a recoverable Staging recovery point.**

Record the backup/snapshot identifier and the pre-change counts for parent cards, contracts, financings, vessels, documents, receipts, repayments, anomalies, and audit records. Do not access Production.

- [x] **Step 2: Run the legacy preview.**

Review every legacy `钢厂-行号` card, its source keys, contracts, WPS candidates, and conflicts. Do not apply a mapping merely because the steel mill or row number looks similar.

- [x] **Step 3: Apply only uniquely evidenced corrections, if any.**

Use one transaction per approved mapping, preserve the old/new number, source evidence, operator, timestamp, and result in the existing audit path, and re-run status/risk calculation. If no unique mapping exists, leave the record pending and report it; do not invent a value.

- [x] **Step 4: Verify data invariants.**

Confirm parent count, duplicate authoritative numbers, all child-row counts, and audit-row counts match the recovery-point comparison. If any invariant fails, restore the recovery point and stop.

## Task 8: Consolidated local quality gate, Staging deployment, and real-page acceptance

**Files:**

- Modify after deployment only: `版本更新记录.md`
- No Production files or settings.

**Interfaces:**

- Produces the tested commit, Staging URL/version identity, automated results, browser evidence, timing evidence, and rollback point required for AIO-20260809-002.

- [x] **Step 1: Run the targeted gate.**

```bash
pytest -q tests/test_order_lifecycle.py tests/test_order_finance.py
node --test tests/order_lifecycle_frontend.test.mjs
python3 -m compileall backend/app
git diff --check
```

- [x] **Step 2: Run the full local gate.**

```bash
pytest -q
```

Expected: no unrelated regression; the two previously identified order-finance failures remain resolved with fixture-safe behavior.

- [x] **Step 3: Review the final diff and commit the implementation.**

Verify only the approved files changed, no secrets/Production URLs entered source or records, and no generated business-number path exists. Commit on `codex/aio-20260809-002-order-lifecycle-v1`.

- [x] **Step 4: Push and deploy Staging only.**

Push the existing branch and wait for Render Staging to serve the immutable commit. Do not merge or push `main`; do not deploy Production.

- [x] **Step 5: Perform one consolidated Computer Use acceptance pass.**

On `https://ltm-web-staging.onrender.com/?codex=<commit>` using the admin account, verify:

1. version identity and `admin｜管理员` visible;
2. WPS/email success times and 3+6 summaries;
3. first-row filters, second-row status, uniform clear/search controls, and long-label wrapping;
4. financing card compact identity, type background, field-level risk, shipment anomaly highlighting, and explicit next step;
5. over-order-only view with no financing section, settlement/risk/receipt facts, and correct short type label;
6. search for a legacy identifier does not cause a generated replacement;
7. detail hero, all 01–08 horizontal links, solid sticky nav, table-local horizontal scroll, and no page-level clipping;
8. open detail, scroll, return, and verify the previous filter/page/view/scroll position;
9. unified edit entry/exit visibility for the authorized admin without saving a test mutation;
10. warm entry/search/filter/page/detail timings, with 200ms loading feedback and ≤2s warm result display.

Do not use an API 200, terminal `curl`, mock data, or logs as a replacement for this browser-visible business acceptance.

- [x] **Step 6: Update the release record after deployment.**

Record commit, Staging URL/version, focused/full test results, recovery point, data-mapping decision, browser evidence, measured timings, known limitations, and rollback commit in `版本更新记录.md`. Do not record passwords, tokens, cookies, database URLs, or deploy hooks.

## Acceptance commands

```bash
pytest -q tests/test_order_lifecycle.py tests/test_order_finance.py
node --test tests/order_lifecycle_frontend.test.mjs
python3 -m compileall backend/app
pytest -q
git diff --check
```

## Rollback and stop conditions

- Code rollback target: the pre-change Staging commit recorded in Task 1.
- Data rollback target: the recovery point recorded in Task 7, only if a uniquely evidenced Staging mapping was applied.
- Stop before applying data mappings when evidence is missing, conflicting, or non-unique.
- Stop ordinary rework after the same failure has failed twice; return to requirements or assessment instead of guessing.
- Any request to touch Production, merge/push `main`, change Production configuration, or delete business/audit data requires a separate explicit authorization and Gate B review.
