# Iron Ore Basis UI Rework Plan

**Assessment:** D2 / T2 / R2 / C1

**Goal:** Correct the staging UI so the iron-ore basis feature follows the approved navigation, filter and chart contracts without changing data, APIs, calculations or database tables.

## Scope

- Replace page-internal spot/basis tabs with true third-level sidebar children under the existing data-management and data-display menu items.
- Make spot and basis filters call the same shared checkbox renderer and select-all/select-none binder; matching class names alone do not satisfy this requirement.
- Make the spot atlas and basis chart call the same shared small-multiples renderer. Basis-specific daily X values, zero axis and tooltip fields are renderer options, not a second chart implementation.
- Preserve the optimal-warrant independence, active-port-only requests, default Rizhao port and existing spot behavior.

## TDD sequence

1. Add focused failing frontend tests for nested-menu structure, absence of page tabs, matching filter controls, exact 12-month axis, shared legend, line selection and tooltip payload.
2. Implement the minimum navigation and basis-controller changes to pass those tests.
3. Run the focused basis frontend test and the existing data-visualization frontend regression test.
4. Run syntax checks, relevant iron-ore/data-visualization API tests and `git diff --check`.

## Requirement traceability

| Locked requirement | Implementation contract | Automated assertion | Staging evidence |
|---|---|---|---|
| Filters use the same component | Spot and basis both call `DataVisualizationComponents.renderCheckboxOptions` and `bindCheckboxPanelActions` | Source-level shared-call assertions; no basis-local filter builder | Same DOM component marker, class, dimensions and all/none behavior on both pages |
| Charts use the same component | Spot atlas and basis both call `DataVisualizationComponents.renderYearSmallMultiples` | Source-level shared-call assertions plus spot regression | Same canvas component marker, panel geometry, colors, legend and highlight interaction in side-by-side screenshots |
| Basis keeps its confirmed semantics | Shared renderer receives daily axis, all 12 months, negative/zero-axis and real-point tooltip options | Basis option and shared-renderer assertions | Daily tooltip, negative value/zero axis, 12 months and highlight/cancel on Staging |

## Staging T2 acceptance

- Verify the four child menu entries, three-level breadcrumb and page switching.
- Verify basis filters and spot filters carry the same shared component marker, DOM class, rendered dimensions and all/none behavior.
- Verify default Rizhao, one active-port request, independent optimal warrant, 12 month labels, one top-right legend, click highlight/cancel and daily tooltip.
- Run a focused spot management/display regression, keep a spot-versus-basis mismatch ledger, and check page identity, current static assets and console health.

## Exclusions

- No Excel reimport, basis recalculation, database/schema write, API redesign, full-row recount, export, calculation-detail page, automatic data sync, production merge or production deployment.

## Rollback

Redeploy staging commit `bc1d6f9`. No database rollback is required.
