import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";


const indexHtml = readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");
const basisJs = readFileSync(new URL("../frontend/iron_ore_basis.js", import.meta.url), "utf8");
const basisCss = readFileSync(new URL("../frontend/iron_ore_basis.css", import.meta.url), "utf8");
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
  assert.match(section, /ironOreBasisManagementLoadMore/);
});

test("basis filters reuse the spot checkbox panel and all-none controls", () => {
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
  assert.match(basisJs, /function bindFilterActions\(container, allButton, noneButton, onChange\)/);
  assert.match(basisJs, /bindFilterActions\(managementYears, managementYearAll, managementYearNone/);
  assert.match(basisJs, /bindFilterActions\(displayProducts, displayProductAll, displayProductNone/);
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

test("basis chart has all months, one shared legend, selection and a real-point tooltip", () => {
  assert.match(indexHtml, /id="ironOreBasisYearLegend" class="dv-year-legend"/);
  assert.match(indexHtml, /id="ironOreBasisTooltip" class="iron-ore-basis-tooltip hidden"/);
  assert.match(basisJs, /var BASIS_MONTHS = \[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12\];/);
  assert.match(basisJs, /function updateBasisYearLegend\(years\)/);
  assert.doesNotMatch(basisJs, /var legendX =/);
  assert.match(basisJs, /highlightedYear:\s*null/);
  assert.match(basisJs, /chartHitSegments:\s*\[\]/);
  assert.match(basisJs, /function findNearestBasisLine\(x, y/);
  assert.match(basisJs, /chartCanvas\.addEventListener\("click"/);
  assert.match(basisJs, /basisState\.highlightedYear = basisState\.highlightedYear === hit\.year \? null : hit\.year/);
  for (const field of ["日期", "年份", "品种", "港口", "基差"]) assert.match(basisJs, new RegExp(field));
  assert.match(basisCss, /\.iron-ore-basis-tooltip/);
});

test("basis chart requests only the active port and renders negative values with a zero axis", () => {
  assert.match(basisJs, /display\/chart\?port=" \+ encodeURIComponent\(basisState\.activePort\)/);
  assert.match(basisJs, /Math\.min\(0,/);
  assert.match(basisJs, /Math\.max\(0,/);
  assert.match(basisJs, /drawBasisZeroAxis/);
  assert.match(basisCss, /iron-ore-basis-chart-container/);
});

test("basis assets are loaded after the existing app controller", () => {
  assert.ok(indexHtml.indexOf("/static/app.js") < indexHtml.indexOf("/static/iron_ore_basis.js"));
  assert.match(indexHtml, /app\.js\?v=iron-ore-basis-ui-20260713/);
  assert.match(indexHtml, /iron_ore_basis\.css\?v=iron-ore-basis-ui-20260713/);
});

test("mobile app shell grows beyond the sidebar so the workspace remains reachable", () => {
  assert.match(stylesCss, /@media \(max-width: 900px\)[\s\S]*?\.app-shell \{[\s\S]*?height: auto;[\s\S]*?min-height: 100vh;/);
});
