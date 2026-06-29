import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const appJs = readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");
const indexHtml = readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");
const stylesCss = readFileSync(new URL("../frontend/styles.css", import.meta.url), "utf8");
const dbPy = readFileSync(new URL("../backend/app/db.py", import.meta.url), "utf8");
const integrationSection = indexHtml.slice(
  indexHtml.indexOf('id="dvIntegrationPage"'),
  indexHtml.indexOf('id="dvDataPage"'),
);

test("data visualization filters preserve empty selections", () => {
  assert.match(appJs, /function appendMultiSelectParam\(/);
  assert.match(appJs, /selectedValues\.length === 0/);
  assert.match(appJs, /__EMPTY__/);
});

test("integrated data cells without ids are not inline editable", () => {
  assert.match(appJs, /if \(!cell\.dataset\.id\) return;/);
});

test("data visualization chart page exposes arrival and chart modes", () => {
  assert.match(indexHtml, /data-metric="arrival"/);
  assert.match(indexHtml, /到港图表/);
  assert.match(indexHtml, /全品种图谱/);
  assert.match(indexHtml, /品种对比图/);
});

test("data visualization integration summary separates shipment and arrival", () => {
  assert.match(appJs, /\["发运", metrics\.shipment \|\| summary\.shipment_count \|\| 0\]/);
  assert.match(appJs, /\["到港", metrics\.arrival \|\| summary\.arrival_count \|\| 0\]/);
});

test("data visualization metric tabs use shipment arrival inventory demand order", () => {
  const metricOrder = function(containerId) {
    const start = indexHtml.indexOf(`id="${containerId}"`);
    assert.notEqual(start, -1);
    const end = indexHtml.indexOf("</div>", start);
    return Array.from(indexHtml.slice(start, end).matchAll(/data-metric="([^"]+)"/g)).map((match) => match[1]);
  };
  assert.deepEqual(metricOrder("dvDataTabs"), ["shipment", "arrival", "inventory", "apparent_demand"]);
  assert.deepEqual(metricOrder("dvChartTabs"), ["shipment", "arrival", "inventory", "apparent_demand"]);
  assert.match(appJs, /currentMetric: "shipment"/);
  assert.match(appJs, /chartMetric: "shipment"/);
  assert.match(appJs, /await initDVData\(\);/);
});

test("data visualization data page waits for filters before loading table", () => {
  assert.doesNotMatch(appJs, /initDVData\(\);\s*await loadDVTable\("shipment"\)/);
  assert.match(appJs, /await initDVData\(\);/);
  assert.match(appJs, /async function initDVData\(\)/);
  assert.match(appJs, /async function loadDVDataFilters\(\)/);
  assert.match(appJs, /await buildYearCheckboxes\(dvDataYearCheckboxes/);
  assert.match(appJs, /await loadDVTable\(dvState\.currentMetric\);/);
});

test("data integration page keeps only upload and download actions", () => {
  assert.match(integrationSection, /导入 Excel/);
  assert.match(integrationSection, /下载整合 Excel/);
  assert.doesNotMatch(integrationSection, /上传 Excel/);
  assert.doesNotMatch(integrationSection, /读取本地模板/);
  assert.doesNotMatch(integrationSection, /写入本地整合结果/);
  assert.doesNotMatch(integrationSection, /写入上传结果/);
  assert.doesNotMatch(integrationSection, /id="dvIntegrationSamples"/);
});

test("data integration upload automatically commits uploaded files", () => {
  assert.match(appJs, /正在上传并整合/);
  assert.match(appJs, /上传文件已整合，可下载 Excel/);
  assert.doesNotMatch(appJs, /dvUploadCommitBtn/);
  assert.doesNotMatch(appJs, /previewLocalIntegration/);
  assert.doesNotMatch(appJs, /commitLocalIntegration/);
});

test("sidebar groups put data visualization before admin", () => {
  assert.ok(dbPy.indexOf('("数据可视化管理", "data_visualization_integration"') < dbPy.indexOf('("后台管理", "user_management"'));
});

test("shanghai junneng ledger close dialog supports partial close quantity", () => {
  assert.match(indexHtml, /id="shJunnengCloseQuantity"/);
  assert.match(appJs, /close_quantity: Number\(document\.querySelector\("#shJunnengCloseQuantity"\)\.value\)/);
  assert.match(appJs, /item\?\.remaining_quantity \?\? item\?\.hold_quantity/);
});

test("sidebar groups are collapsible and visually emphasize group titles", () => {
  assert.match(appJs, /menu-group-toggle/);
  assert.match(appJs, /menu-group-items/);
  assert.match(appJs, /collapsedMenuGroups/);
  assert.match(stylesCss, /\.menu-group-title/);
  assert.match(stylesCss, /font-size: 15px/);
  assert.match(stylesCss, /background: rgba\(255, 255, 255, 0\.08\)/);
  assert.match(stylesCss, /\.menu-item \{\s+width: 100%;[\s\S]*font-size: 14px/);
  assert.match(stylesCss, /\.menu-group\.collapsed \.menu-group-items/);
});

test("data visualization chart filters use product pools instead of always-flat filters", () => {
  assert.match(indexHtml, /dvChartProductPool/);
  assert.match(appJs, /function applyDVChartProductPool/);
  assert.match(appJs, /整体对比/);
});

test("data visualization data page uses product pools and advanced filters", () => {
  assert.match(indexHtml, /dvDataProductPool/);
  assert.match(indexHtml, /dv-data-advanced-filters/);
  assert.match(appJs, /function applyDVDataProductPool/);
  assert.match(appJs, /product_pool=aggregate/);
});

test("non-mainstream product pools label country-total series clearly", () => {
  assert.match(indexHtml, /id="dvDataProductFilterLabel"/);
  assert.match(indexHtml, /id="dvChartProductFilterLabel"/);
  assert.match(appJs, /non_mainstream: "品种\/国家总量"/);
  assert.match(appJs, /updateDVProductFilterLabel\(dvDataProductFilterLabel, pool\)/);
  assert.match(appJs, /updateDVProductFilterLabel\(dvChartProductFilterLabel, pool\)/);
});

test("data visualization table headers support long product names", () => {
  assert.match(appJs, /function formatDVProductHeaderLabel\(label\)/);
  assert.match(appJs, /class="dv-product-header"/);
  assert.match(appJs, /title="/);
  assert.match(stylesCss, /#dvDataTable th:not\(:first-child\):not\(:nth-child\(2\)\)/);
  assert.match(stylesCss, /min-width: 132px/);
  assert.match(stylesCss, /max-width: 160px/);
  assert.match(stylesCss, /white-space: normal/);
  assert.match(stylesCss, /\.dv-value-cell \{[\s\S]*text-align: center;/);
});

test("aggregate product pool sends selected aggregate products", () => {
  const tableAggregateBranch = /if \(productPool === "aggregate"\) \{\s+url \+= "&product_pool=aggregate";\s+url = appendMultiSelectParam\(url, "products", productsArr, dvDataProductCheckboxes\.querySelectorAll\('input\[type="checkbox"\]'\)\.length\);/;
  const chartAggregateBranch = /if \(productPool === "aggregate"\) \{\s+url \+= "&product_pool=aggregate";\s+url = appendMultiSelectParam\(url, "products", productsArr, dvChartProductCheckboxes\.querySelectorAll\('input\[type="checkbox"\]'\)\.length\);/;
  assert.match(appJs, tableAggregateBranch);
  assert.match(appJs, chartAggregateBranch);
  assert.doesNotMatch(appJs, /else if \(productsArr\.length === 0\) \{\s+\} else if \(productsArr\.length === 0\)/);
});

test("mainstream advanced filter only applies to custom product pools", () => {
  assert.match(appJs, /function shouldApplyDVMainstreamFilter\(productPool\)/);
  assert.match(appJs, /if \(shouldApplyDVMainstreamFilter\(productPool\)\) \{\s+url = appendMultiSelectParam\(url, "mainstream_status", mainstreamArr, dvDataMainstreamCheckboxes/);
  assert.match(appJs, /if \(shouldApplyDVMainstreamFilter\(productPool\)\) \{\s+url = appendMultiSelectParam\(url, "mainstream_status", mainstreamArr, dvChartMainstreamCheckboxes/);
  assert.match(appJs, /syncDVMainstreamAdvancedFilter\(dvDataMainstreamCheckboxes, pool\)/);
  assert.match(appJs, /syncDVMainstreamAdvancedFilter\(dvChartMainstreamCheckboxes, pool\)/);
});

test("chart view mode controls rendering independently of selected product count", () => {
  assert.match(appJs, /if \(viewMode === "atlas"\) \{\s+renderDVChartAtlas\(ctx, W, H, series, products\);/);
  assert.doesNotMatch(appJs, /viewMode === "atlas" && products\.length > 1/);
  assert.match(appJs, /var useProductYearLegend = viewMode === "compare";/);
  assert.match(appJs, /var legendKey = useProductYearLegend \? \(lines\[liColor\]\.product \+ " " \+ lines\[liColor\]\.year\) : lines\[liColor\]\.year;/);
  assert.match(appJs, /if \(useProductYearLegend\) \{\s+drawDVChartLegend/);
});

test("data visualization tabs are below filter controls", () => {
  assert.ok(indexHtml.indexOf('id="dvDataTabs"') > indexHtml.indexOf('id="dvDataProductPool"'));
  assert.ok(indexHtml.indexOf('id="dvChartTabs"') > indexHtml.indexOf('id="dvChartProductPool"'));
  assert.match(indexHtml, /dv-tabs-after-filters/);
});

test("data visualization atlas shows a shared year legend beside chart tabs", () => {
  assert.match(indexHtml, /id="dvChartYearLegend"/);
  assert.ok(indexHtml.indexOf('id="dvChartYearLegend"') > indexHtml.indexOf('id="dvChartTabs"'));
  assert.match(appJs, /function updateDVChartYearLegend\(years, yearColorMap, visible\)/);
  assert.match(appJs, /dv-year-legend-item/);
});

test("data visualization atlas highlights all charts for the selected year", () => {
  assert.match(appJs, /highlightedYear/);
  assert.match(appJs, /closest = \{ lineKey: lineKey, year: year \}/);
  assert.match(appJs, /dist < closestDist && dist < 30/);
  assert.match(appJs, /dvState\.highlightedYear === closest\.year/);
  assert.match(appJs, /var isHighlightedYear = highlightedYear === year;/);
});

test("data visualization chart x axis uses month labels", () => {
  assert.match(appJs, /const DV_MONTH_AXIS_TICKS = \[/);
  assert.match(appJs, /label: "1月"/);
  assert.match(appJs, /label: "12月"/);
  assert.match(appJs, /function drawDVMonthAxis\(ctx, xScale, y\)/);
  assert.doesNotMatch(appJs, /fillText\("W" \+ String\(weekNo\)/);
});

test("data visualization dates are displayed without timestamps", () => {
  assert.match(appJs, /function formatDateOnly\(value\)/);
  assert.match(appJs, /formatDateOnly\(row\.date\)/);
  assert.match(appJs, /formatDateOnly\(point\.display_date\)/);
});

test("integrated import commit shows progress and times out instead of hanging forever", () => {
  assert.match(appJs, /导入中，请稍候/);
  assert.match(appJs, /AbortController/);
  assert.match(appJs, /导入超时/);
});

test("data visualization chart treats missing points as gaps", () => {
  assert.match(appJs, /function isMissingChartPoint\(point\)/);
  assert.match(appJs, /function formatDVChartTooltip\(point, product\)/);
  assert.match(appJs, /isMissingChartPoint\(point\) \? "无数据"/);
  assert.match(appJs, /firstPoint = true;\s+continue;/);
  assert.doesNotMatch(appJs, /ln2\.product \+ " \| " \+ ln2\.year \+ " \| " \+ formatChartNumber/);
});
