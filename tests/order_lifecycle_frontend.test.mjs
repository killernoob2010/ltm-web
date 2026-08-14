import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const appJs = readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");
const indexHtml = readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");

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
    /src="\/static\/app\.js\?[^\"]*&cache=20260814-order-lifecycle-fidelity-v1"/,
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
