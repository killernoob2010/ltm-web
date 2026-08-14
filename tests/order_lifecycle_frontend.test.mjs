import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const appJs = readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");
const indexHtml = readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");
const stylesCss = readFileSync(new URL("../frontend/styles.css", import.meta.url), "utf8");

test("订单全流程页面使用独立模块、冻结原型筛选和详情 API", () => {
  assert.match(indexHtml, /id="orderLifecyclePage"/);
  assert.match(indexHtml, /id="orderLifecyclePageSize"/);
  assert.match(appJs, /code === "order_lifecycle_progress"/);
  assert.match(appJs, /api\/order-lifecycle\/progress/);
  assert.match(appJs, /page_size: String\(state\.orderLifecyclePageSize\)/);
  assert.match(appJs, /order-lifecycle-detail-btn/);
  assert.doesNotMatch(appJs, /order-lifecycle-node-btn/);
  assert.match(appJs, /orderLifecycleOverviewTab/);
  assert.match(appJs, /orderLifecycleFocusTab/);
  assert.match(appJs, /business_types:/);
  assert.match(appJs, /anomaly_types:/);
  assert.match(appJs, /order-lifecycle-edit-child/);
  assert.match(appJs, /child-record/);
  assert.match(appJs, /child-override/);
  assert.match(appJs, /来源版本/);
  assert.match(appJs, /内部记录ID/);
  assert.match(indexHtml, /id="orderLifecycleDetailView"/);
  assert.match(indexHtml, /data-filter-group="statuses"/);
  assert.match(indexHtml, /type="checkbox"/);
  assert.match(appJs, /api\/order-lifecycle\/businesses/);
  assert.doesNotMatch(appJs, /api\/order-lifecycle\/import-upload/);
  assert.doesNotMatch(indexHtml, /导入 WPS 测试快照|导入邮件六附件/);
  assert.doesNotMatch(indexHtml, /id="orderLifecycleWpsImportBtn"|id="orderLifecycleEmailImportFiles"/);
  const fcrFilter = indexHtml.match(/<fieldset[^>]*id="orderLifecycleFcrFilter"[\s\S]*?<\/fieldset>/)?.[0] || "";
  assert.match(fcrFilter, /data-filter-group="fcr"/);
  assert.doesNotMatch(fcrFilter, /data-filter-action=/);
});

test("订单全流程前端资源使用新的缓存一致性版本", () => {
  assert.match(
    indexHtml,
    /src="\/static\/app\.js\?[^\"]*&cache=20260814-order-lifecycle-fidelity-performance-v9"/,
  );
});

test("订单全流程页面符合已审核原型的列表结构和交互契约", () => {
  const lifecycleBlock = indexHtml.match(/<section id="orderLifecyclePage"[\s\S]*?<\/section>\s*<section id="orderFinanceCapitalPage"/)?.[0] || "";
  assert.match(lifecycleBlock, /order-lifecycle-primary-summary/);
  assert.match(lifecycleBlock, /order-lifecycle-operational-summary/);
  assert.match(lifecycleBlock, /order-lifecycle-search-panel/);
  assert.match(lifecycleBlock, /order-lifecycle-filter-grid/);
  assert.match(lifecycleBlock, /order-lifecycle-card-list/);
  assert.match(lifecycleBlock, /orderLifecycleDetailView/);
  assert.match(appJs, /order-lifecycle-primary-summary/);
  assert.match(appJs, /sessionStorage/);
  assert.match(appJs, /setTimeout\(/);
  assert.match(appJs, /orderLifecycleLoadingTimer/);
  assert.match(appJs, /children_loaded/);
});

test("订单全流程详情使用八段式整页结构和统一编辑入口", () => {
  assert.match(appJs, /detail-section-number/);
  assert.match(appJs, /order-lifecycle-detail-nav/);
  assert.match(appJs, /order-lifecycle-edit-all/);
  assert.match(appJs, /order-lifecycle-save-bar/);
  assert.match(appJs, /order-lifecycle-edit-child/);
  assert.doesNotMatch(appJs, /order-lifecycle-node-btn/);
  assert.doesNotMatch(indexHtml, /确认集港|确认装船|撤回业务/);
});

test("订单全流程详情长来源值必须在字段卡内换行", () => {
  assert.match(stylesCss, /order-lifecycle-detail-grid[^{}]*\{[\s\S]*?min-width:\s*0/);
  assert.match(stylesCss, /order-lifecycle-detail-grid[^{}]*strong[^{}]*\{[\s\S]*?overflow-wrap:\s*anywhere/);
});

test("订单全流程页面落实原型的搜索、筛选和来源状态布局", () => {
  const lifecycleBlock = indexHtml.match(/<section id="orderLifecyclePage"[\s\S]*?<section id="orderFinanceCapitalPage"/)?.[0] || "";
  assert.match(lifecycleBlock, /id="orderLifecycleSearchBtn"/);
  assert.match(lifecycleBlock, /id="orderLifecycleFilterGroups"/);
  assert.match(lifecycleBlock, /id="orderLifecycleStatusRow"/);
  assert.match(lifecycleBlock, /class="[^"]*order-lifecycle-filter-footer/);
  assert.match(lifecycleBlock, /WPS 最近获取成功/);
  assert.match(lifecycleBlock, /邮件台账最近获取成功/);
  assert.match(appJs, /function submitOrderLifecycleSearch\(/);
  assert.match(appJs, /orderLifecycleSearchBtn\.addEventListener/);
  const firstRowGroups = lifecycleBlock.match(/<div class="order-lifecycle-filter-groups"[\s\S]*?<\/div>\s*<div class="order-lifecycle-status-row"/)?.[0] || "";
  assert.equal((firstRowGroups.match(/<fieldset/g) || []).length, 4);
  assert.match(stylesCss, /\.order-lifecycle-filter-groups\s*\{[\s\S]*?grid-template-columns:\s*repeat\(4,/);
  assert.doesNotMatch(stylesCss, /#orderLifecycleTypeFilter[^{}]*grid-column:\s*span/);
});

test("订单全流程卡片和详情使用原型的信息层级与风险事实单元", () => {
  assert.match(appJs, /function lifecycleBusinessTypeLabel\(/);
  assert.match(appJs, /order-lifecycle-status-badge/);
  assert.match(appJs, /order-lifecycle-card-fact/);
  assert.match(appJs, /order-lifecycle-record-table/);
  assert.match(appJs, /<table class="order-lifecycle-record-table"/);
  assert.match(stylesCss, /order-lifecycle-card-body/);
  assert.match(stylesCss, /order-lifecycle-card-fact\.fact-danger/);
  assert.match(stylesCss, /order-lifecycle-card-fact\.fact-warning/);
  assert.match(appJs, /function renderOrderLifecycleFinancingCardBody\(/);
  assert.match(appJs, /function renderOrderLifecyclePassCardBody\(/);
  assert.doesNotMatch(appJs, /过单业务不适用/);
  assert.match(appJs, /order-lifecycle-business-type-badge type-financing/);
  assert.match(appJs, /order-lifecycle-business-type-badge type-pass/);
  assert.match(stylesCss, /\.order-lifecycle-business-type-badge\.type-financing\s*\{[^}]*background:/);
  assert.match(stylesCss, /\.order-lifecycle-business-type-badge\.type-pass\s*\{[^}]*background:/);
  assert.match(appJs, /lifecycleCardFact\([^\n]*item\.risk_facts\?\.shipment\)/);
  assert.doesNotMatch(appJs, /order-lifecycle-card-section order-lifecycle-card-risk \$\{riskTone\}/);
});

test("订单全流程详情将 01-08 导航放在内容顶部", () => {
  assert.match(stylesCss, /\.order-lifecycle-detail-layout\s*\{[\s\S]*?display:\s*block/);
  assert.match(stylesCss, /\.order-lifecycle-detail-nav\s*\{[\s\S]*?grid-template-columns:\s*repeat\(8/);
  const navRules = stylesCss.match(/\.order-lifecycle-detail-layout \.order-lifecycle-detail-nav\s*\{[^}]*\}/g) || [];
  assert.ok(navRules.length > 0);
  assert.ok(navRules.every((rule) => !/grid-template-columns:\s*1fr\s*;/.test(rule)));
});

test("订单全流程详情头、实心横向导航和滚动恢复符合冻结契约", () => {
  const detailRenderer = appJs.match(/function renderOrderLifecycleDetail\(detail\)[\s\S]*?async function loadOrderLifecycleDetail/)?.[0] || "";
  const sectionLabels = appJs.match(/const ORDER_LIFECYCLE_DETAIL_SECTIONS = \[([\s\S]*?)\];/)?.[1] || "";
  assert.equal((sectionLabels.match(/"[^"]+"/g) || []).length, 8);
  assert.match(detailRenderer, /ORDER_LIFECYCLE_DETAIL_SECTIONS\.map/);
  for (const label of ["FCR", "融资笔数", "下一步", "来源摘要", "来源更新", "最后修改人", "最后修改时间"]) {
    assert.match(detailRenderer, new RegExp(label));
  }
  const finalNavRules = stylesCss.match(/\.order-lifecycle-detail-layout \.order-lifecycle-detail-nav\s*\{[^}]*\}/g) || [];
  assert.ok(finalNavRules.some((rule) => /top:\s*0/.test(rule)));
  assert.ok(finalNavRules.some((rule) => /background:\s*(?:#fff|var\(--surface\))/.test(rule)));
  assert.ok(finalNavRules.some((rule) => /overflow-x:\s*auto/.test(rule)));
  assert.match(stylesCss, /\.order-lifecycle-detail-shell\s*\{[^}]*max-width:\s*100%/);
  assert.match(stylesCss, /#orderLifecycleDetailView\s*\{[^}]*min-width:\s*0[^}]*max-width:\s*100%/);
  assert.match(stylesCss, /\.order-lifecycle-detail-content\s*\{[^}]*min-width:\s*0[^}]*max-width:\s*100%/);
  assert.match(stylesCss, /\.order-lifecycle-detail-section\.detail-section\s*\{[^}]*min-width:\s*0[^}]*max-width:\s*100%/);
  assert.match(stylesCss, /\.order-lifecycle-record-table-wrap\s*\{[^}]*overflow-x:\s*auto/);
  assert.match(appJs, /scrollTop:\s*orderLifecycleCurrentScrollTop\(\)/);
  assert.match(appJs, /bindOrderLifecycleDetailNavigation[\s\S]*?event\.preventDefault\(\)[\s\S]*?pageScroller\.scrollTo/);
  assert.match(appJs, /pageScroller\.scrollTo\(\{ top: Math\.max\(targetTop, 0\), behavior: "auto" \}\)/);
  assert.match(appJs, /missingScrollSpace\s*=\s*targetTop\s*-\s*maxScrollTop/);
  assert.match(appJs, /detailRoot\.style\.paddingBottom\s*=\s*`\$\{currentScrollReserve \+ missingScrollSpace\}px`/);
  assert.match(appJs, /requestAnimationFrame\([\s\S]*?pageScroller\.scrollTop\s*=\s*scrollTop/);
  assert.match(appJs, /if \(pageScroller\) \{[\s\S]*?pageScroller\.scrollTop = scrollTop;[\s\S]*?\} else \{[\s\S]*?window\.scrollTo/);
  assert.match(appJs, /clearOrderLifecycleDetailHash\(\)/);
  assert.match(appJs, /pageScroller\.scrollTop = scrollTop;[\s\S]*?window\.scrollTo\(\{ top: 0/);
  assert.match(appJs, /orderLifecyclePendingScroll[^\n]*queryKey/);
});
