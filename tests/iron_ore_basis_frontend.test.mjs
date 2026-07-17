import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";


const indexHtml = readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");
const basisJs = readFileSync(new URL("../frontend/iron_ore_basis.js", import.meta.url), "utf8");
const basisCss = readFileSync(new URL("../frontend/iron_ore_basis.css", import.meta.url), "utf8");
const sharedComponentsJs = readFileSync(new URL("../frontend/data_visualization_components.js", import.meta.url), "utf8");
const appJs = readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");
const stylesCss = readFileSync(new URL("../frontend/styles.css", import.meta.url), "utf8");


test("spot and spot-futures are real nested sidebar children, not page tabs", () => {
  assert.doesNotMatch(indexHtml, /id="dvDataViewTabs"|id="dvDisplayViewTabs"/);
  assert.doesNotMatch(indexHtml, /data-basis-management-view|data-basis-display-view/);
  assert.match(appJs, /const DATA_VISUALIZATION_SUBMENUS = \{/);
  assert.match(appJs, /data_visualization_data:\s*\[[\s\S]*?现货数据管理[\s\S]*?期现数据管理/);
  assert.match(appJs, /data_visualization_chart:\s*\[[\s\S]*?现货数据展示[\s\S]*?期现数据展示/);
  assert.match(appJs, /className = "menu-subitems"/);
  assert.match(appJs, /className = `menu-subitem/);
  assert.match(appJs, /activateModule\(item\.code, child\.name, child\.view\)/);
  assert.match(appJs, /pageSubtitle\.textContent = `\$\{label\.group\} \/ \$\{label\.name\} \/ \$\{subName\}`/);
});

test("basis management is read-only and exposes the confirmed fields", () => {
  const start = indexHtml.indexOf('id="ironOreBasisManagementView"');
  const end = indexHtml.indexOf('id="ironOreBasisDisplayView"');
  const section = indexHtml.slice(start, end);
  assert.ok(start > 0 && end > start);
  for (const label of [
    "日期", "周次", "年份", "港口", "品种", "湿吨现货价", "质量升贴水",
    "品牌升贴水", "主力连续收盘价", "基差", "数据状态",
  ]) assert.match(section, new RegExp(label));
  assert.doesNotMatch(section, /导入 Excel|导出|编辑|删除/);
  assert.match(section, /ironOreBasisManagementPagination/);
  assert.doesNotMatch(section, /ironOreBasisManagementLoadMore/);
});

test("basis pages refresh the latest stored data date on every activation", () => {
  assert.match(indexHtml, /id="ironOreBasisManagementLatestDate"[^>]*>最新数据日期：正在加载/);
  assert.match(indexHtml, /id="ironOreBasisDisplayLatestDate"[^>]*>最新数据日期：正在加载/);
  assert.match(basisJs, /async function loadManagementStatus\(\)/);
  assert.match(basisJs, /async function loadDisplayStatus\(\)/);
  assert.match(basisJs, /async function initManagement\(\) \{[\s\S]*await loadManagementStatus\(\)/);
  assert.match(basisJs, /async function initDisplay\(\) \{[\s\S]*await loadDisplayStatus\(\)/);
  assert.match(basisJs, /managementLatestDate\.textContent = "最新数据日期：" \+ \(filters\.latest_data_date \|\| "暂无数据"\)/);
  assert.match(basisJs, /displayLatestDate\.textContent = "最新数据日期：" \+ \(filters\.latest_data_date \|\| "暂无数据"\)/);
  assert.doesNotMatch(basisJs, /optimalDate\.textContent = "数据截至 "/);
  assert.doesNotMatch(indexHtml, /id="ironOreBasisOptimalDate"/);
  assert.doesNotMatch(indexHtml, /异常数据|同步异常|失败次数/);
});

test("basis management uses the shared 20 50 100 server pagination", () => {
  assert.match(basisJs, /managementPage:\s*1/);
  assert.match(basisJs, /managementPageSize:\s*20/);
  assert.match(basisJs, /\(basisState\.managementPage - 1\) \* basisState\.managementPageSize/);
  assert.match(basisJs, /DataVisualizationComponents\.renderPagination\(managementPagination/);
  assert.match(basisJs, /pageSizes:\s*\[20, 50, 100\]/);
  assert.doesNotMatch(basisJs, /managementHasMore|managementLoadMore/);
  assert.match(sharedComponentsJs, /function renderPagination\(container, options\)/);
});

test("basis display year filter aligns left and shares the taller chart viewport", () => {
  assert.match(basisCss, /#ironOreBasisDisplayView \.iron-ore-basis-filter-row \.dv-year-panel\s*\{[\s\S]*grid-column:\s*1/);
  assert.match(stylesCss, /\.chart-container \{[\s\S]*height:\s*max\(420px, calc\(100vh - 260px\)\)/);
});

test("spot and basis filters call the same shared checkbox component", () => {
  for (const id of [
    "ironOreBasisManagementYearAll", "ironOreBasisManagementYearNone",
    "ironOreBasisManagementProductAll", "ironOreBasisManagementProductNone",
    "ironOreBasisManagementPortAll", "ironOreBasisManagementPortNone",
    "ironOreBasisDisplayYearAll", "ironOreBasisDisplayYearNone",
    "ironOreBasisDisplayProductAll", "ironOreBasisDisplayProductNone",
  ]) {
    assert.match(indexHtml, new RegExp(`id="${id}"`));
  }
  assert.match(indexHtml, /class="dv-filter-btn dv-filter-all" id="ironOreBasisManagementYearAll"/);
  assert.match(indexHtml, /class="dv-filter-btn dv-filter-none" id="ironOreBasisDisplayProductNone"/);
  assert.match(indexHtml, /\/static\/data_visualization_components\.js/);
  assert.ok(indexHtml.indexOf("/static/data_visualization_components.js") < indexHtml.indexOf("/static/app.js"));
  assert.match(appJs, /DataVisualizationComponents\.renderCheckboxOptions/);
  assert.match(appJs, /DataVisualizationComponents\.bindCheckboxPanelActions/);
  assert.match(basisJs, /DataVisualizationComponents\.renderCheckboxOptions/);
  assert.match(basisJs, /DataVisualizationComponents\.bindCheckboxPanelActions/);
  assert.doesNotMatch(basisJs, /function buildFilter\(/);
  assert.doesNotMatch(basisJs, /function bindFilterActions\(/);
  assert.doesNotMatch(basisJs, /dv-checkbox-item/);
});

test("basis display keeps optimal warrant independent from chart controls", () => {
  assert.match(indexHtml, /id="ironOreBasisOptimalWarrant"/);
  assert.match(indexHtml, /最优仓单测算/);
  assert.match(basisJs, /async function loadOptimalWarrant/);
  assert.match(basisJs, /async function loadBasisChart/);
  const chartFunction = basisJs.slice(
    basisJs.indexOf("async function loadBasisChart"),
    basisJs.indexOf("function renderBasisChart"),
  );
  assert.doesNotMatch(chartFunction, /loadOptimalWarrant/);
});

test("basis display filters only year and product and defaults to Rizhao port", () => {
  const start = indexHtml.indexOf('id="ironOreBasisDisplayView"');
  const end = indexHtml.indexOf('id="alertDialog"');
  const section = indexHtml.slice(start, end);
  assert.match(section, /ironOreBasisDisplayYears/);
  assert.match(section, /ironOreBasisDisplayProducts/);
  assert.doesNotMatch(section, /ironOreBasisDisplayPorts/);
  const ports = Array.from(section.matchAll(/data-basis-port="([^"]+)"/g)).map((match) => match[1]);
  assert.deepEqual(ports, ["日照港", "青岛港", "岚山港", "连云港", "江阴港", "太仓港", "京唐港", "曹妃甸港"]);
  assert.match(section, /class="dv-tab active" data-basis-port="日照港"/);
  assert.match(basisJs, /activePort:\s*"日照港"/);
});

test("spot and basis charts call the same shared small-multiples component", () => {
  assert.match(indexHtml, /id="ironOreBasisYearLegend" class="dv-year-legend"/);
  assert.match(indexHtml, /id="ironOreBasisTooltip" class="iron-ore-basis-tooltip hidden"/);
  assert.match(appJs, /DataVisualizationComponents\.renderYearSmallMultiples/);
  assert.match(basisJs, /DataVisualizationComponents\.renderYearSmallMultiples/);
  assert.match(basisJs, /calendarMonthTicks/);
  assert.match(basisJs, /includeZero:\s*true/);
  assert.match(basisJs, /tooltipElement:\s*chartTooltip/);
  assert.doesNotMatch(basisJs, /var YEAR_COLORS/);
  assert.doesNotMatch(basisJs, /var BASIS_MONTHS/);
  assert.doesNotMatch(basisJs, /function updateBasisYearLegend/);
  assert.doesNotMatch(basisJs, /function findNearestBasisLine/);
  assert.doesNotMatch(basisJs, /function segmentDistance/);
  for (const field of ["日期", "年份", "品种", "港口", "基差"]) assert.match(basisJs, new RegExp(field));
  assert.match(basisCss, /\.iron-ore-basis-tooltip/);
});

test("basis chart requests only the active port and renders negative values with a zero axis", () => {
  assert.match(basisJs, /display\/chart\?port=" \+ encodeURIComponent\(basisState\.activePort\)/);
  assert.match(basisJs, /includeZero:\s*true/);
  assert.match(basisJs, /drawZeroAxis:\s*true/);
  assert.match(sharedComponentsJs, /yMin = Math\.min\(0, yMin\)/);
  assert.match(sharedComponentsJs, /yMax = Math\.max\(0, yMax\)/);
  assert.match(sharedComponentsJs, /if \(options\.drawZeroAxis/);
  assert.match(basisCss, /iron-ore-basis-chart-container/);
});

test("basis assets are loaded after the existing app controller", () => {
  assert.ok(indexHtml.indexOf("/static/app.js") < indexHtml.indexOf("/static/iron_ore_basis.js"));
  assert.match(indexHtml, /app\.js\?v=risk-alert-history-grouping-20260717/);
  assert.match(indexHtml, /iron_ore_basis\.css\?v=iron-ore-basis-auto-sync-20260714/);
  assert.match(indexHtml, /iron_ore_basis\.js\?v=iron-ore-basis-date-refresh-20260716/);
});

test("mobile app shell grows beyond the sidebar so the workspace remains reachable", () => {
  assert.match(stylesCss, /@media \(max-width: 900px\)[\s\S]*?\.app-shell \{[\s\S]*?height: auto;[\s\S]*?min-height: 100vh;/);
});
