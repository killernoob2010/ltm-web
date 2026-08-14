# Order Lifecycle Fidelity and Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the non-Production order lifecycle test version back to the audited prototype interaction and information hierarchy, while removing the list-page N+1 query pattern and preventing non-authoritative steel-mill row numbers from becoming business identifiers.

**Architecture:** Keep the existing system shell, permissions, source reconciliation, and detail APIs. Replace list assembly with a bounded parent query plus batched child aggregation and page-only card serialization. Keep complete child/source/audit records behind the detail request. Rebuild the lifecycle view inside the existing page using prototype-aligned semantic sections, stable filter state, wide cards, and a full-page detail/edit mode.

**Tech Stack:** FastAPI, SQLite/PostgreSQL-compatible SQL, vanilla JavaScript, CSS, pytest, Node test runner, in-app Browser for Staging visual verification.

**Spec:** `docs/2026-08-14-order-lifecycle-fidelity-performance-business-requirements.md`

## Global Constraints

- Existing AIO-20260809-002 and branch `codex/aio-20260809-002-order-lifecycle-v1` only; do not create a new AIO.
- Local and Staging only. Do not read or write Production data, deploy Production, or change unrelated modules.
- Keep real source identifiers authoritative. A placeholder such as `XYZ-2026-1` is a display/example convention only; it must never be generated as a real identifier.
- Preserve existing permission checks, audit records, source reconciliation, and detail save behavior unless a test proves the current lifecycle page violates the approved requirements.
- Every user-visible time value remains truncated to seconds.

## Task 1: Establish the implementation baseline

- [ ] Confirm branch, clean worktree, approved BRD commit, current test commands, and prototype assets.
- [ ] Run the targeted lifecycle backend/frontend tests once and record the baseline failures.
- [ ] Do not edit generated assets, receipt/manifest management state, or Production configuration.

## Task 2: Add failing behavior contracts before implementation

- [ ] Add a backend test that creates a representative active/in-progress/completed data set and requires the three business-count summary fields plus the six operational metrics.
- [ ] Add a backend test with many business cards that requires page-sized output, correct all-result totals, and a bounded SQL statement count; it must fail against the current per-business child loading path.
- [ ] Add a backend test proving a WPS record whose identifier is only a steel-mill row number is held as a match candidate and cannot create a parent card.
- [ ] Replace the frontend source-presence-only assertions with a lifecycle structure contract covering the three summary cards, grouped filter controls, wide-card regions, detail section anchors, and absence of node-confirmation/temporary-cancel controls.
- [ ] Run the new tests and confirm they fail for the expected pre-change reasons.

## Task 3: Implement batched list aggregation and identifier protection

- [ ] Add indexes for all child tables, source records, and anomaly/filter access paths required by the new list query.
- [ ] Split list assembly into parent filtering, batched child aggregation, all-result summary/focus calculation, deterministic sort/page, and page-card serialization.
- [ ] Return `存续业务`, `其中进行中`, and `已完结业务` alongside the existing operational metrics, with summary counts calculated over the filtered result set rather than the current page.
- [ ] Ensure list cards contain only card-level aggregates and the current-page fields; keep full child/source/audit rows in the detail response.
- [ ] Add the legacy steel-mill-row identifier guard before parent creation and route rejected rows to the existing match-candidate/audit path with a clear reason.
- [ ] Run backend tests, including the original order-finance regression tests, and fix only failures caused by this scoped change.

## Task 4: Restore the prototype-aligned list interaction

- [ ] Rebuild the lifecycle page markup within the existing shell: three primary business counters, six operational metrics, search row, grouped filters, overview/focus tabs, wide cards, and stable pagination.
- [ ] Implement debounced keyword search, 200 ms loading feedback, session-scoped filter/view/page persistence, and a clear action that resets all of those values to page 1.
- [ ] Render financing, execution, risk, due/receipt, anomaly, and next-step fields in the prototype hierarchy; hide financing-only blocks on pass-through cards.
- [ ] Correct field width, wrapping, button sizing, contrast, and anomaly color treatment so labels cannot overflow or truncate in the approved desktop viewport.
- [ ] Keep the lifecycle page free of controls that confirm nodes, cancel orders, or invent replacement identifiers.

## Task 5: Restore full-page detail and unified edit interaction

- [ ] Replace the compact detail renderer with the approved return bar, hero, fixed 01–08 section navigation, table-based sections, complete child rows, anomaly/manual-record area, and sticky save bar.
- [ ] Keep detail data lazy-loaded on click and preserve the list query state when returning.
- [ ] Make edit mode a single page-level state: all allowed fields become editable together, with one save/cancel surface and existing audit/permission semantics.
- [ ] Add frontend behavior assertions for detail section order, full-row rendering, and the absence of per-node confirmation controls.

## Task 6: Local verification and Staging acceptance

- [ ] Run targeted backend, frontend, and security-sensitive import tests, then the full local test suite and frontend build.
- [ ] Run `git diff --check` and review the final diff for scope, SQL portability, and no secret/Production changes.
- [ ] Commit the implementation on the existing branch.
- [ ] Push/deploy the existing branch to Staging only, then update `版本更新记录.md` after deployment with the commit and user-visible scope.
- [ ] Use the in-app Browser on the Staging URL to verify version identity, page load feedback, all three summary cards, filter wrapping/clear, wide card layout, detail navigation, unified edit entry/exit, and list/detail performance. Treat terminal probes as auxiliary evidence only.
- [ ] If authentication or business data prevents real-page verification, stop at that boundary and report the exact user action needed; do not use guest data, mocks, or API 200 as a substitute.

## Acceptance Commands

```bash
pytest -q tests/test_order_lifecycle.py tests/test_order_finance.py
node --test tests/order_lifecycle_frontend.test.mjs
pytest -q
git diff --check
```

