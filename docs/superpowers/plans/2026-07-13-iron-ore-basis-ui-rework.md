# Iron Ore Basis UI Rework Plan

**Assessment:** D2 / T2 / R2 / C1

**Goal:** Correct the staging UI so the iron-ore basis feature follows the approved navigation, filter and chart contracts without changing data, APIs, calculations or database tables.

## Scope

- Replace page-internal spot/basis tabs with true third-level sidebar children under the existing data-management and data-display menu items.
- Reuse the spot checkbox-panel pattern, including select-all and select-none controls, for all basis filters.
- Keep the existing Canvas2D small multiples and add one shared year legend, all twelve month labels, deterministic line selection and an HTML daily-value tooltip.
- Preserve the optimal-warrant independence, active-port-only requests, default Rizhao port and existing spot behavior.

## TDD sequence

1. Add focused failing frontend tests for nested-menu structure, absence of page tabs, matching filter controls, exact 12-month axis, shared legend, line selection and tooltip payload.
2. Implement the minimum navigation and basis-controller changes to pass those tests.
3. Run the focused basis frontend test and the existing data-visualization frontend regression test.
4. Run syntax checks, relevant iron-ore/data-visualization API tests and `git diff --check`.

## Staging T2 acceptance

- Verify the four child menu entries, three-level breadcrumb and page switching.
- Verify basis filters match the spot filter component and all/none behavior.
- Verify default Rizhao, one active-port request, independent optimal warrant, 12 month labels, one top-right legend, click highlight/cancel and daily tooltip.
- Run a focused spot management/display regression and check page identity, current static assets and console health.

## Exclusions

- No Excel reimport, basis recalculation, database/schema write, API redesign, full-row recount, export, calculation-detail page, automatic data sync, production merge or production deployment.

## Rollback

Redeploy staging commit `bc1d6f9`. No database rollback is required.
