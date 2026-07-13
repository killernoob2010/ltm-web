import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";


const indexHtml = readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");
const basisJs = readFileSync(new URL("../frontend/iron_ore_basis.js", import.meta.url), "utf8");
const basisCss = readFileSync(new URL("../frontend/iron_ore_basis.css", import.meta.url), "utf8");
const appJs = readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");
const stylesCss = readFileSync(new URL("../frontend/styles.css", import.meta.url), "utf8");


test("data pages expose spot and spot-futures third-level views", () => {
  assert.match(indexHtml, /现货数据管理/);
  assert.match(indexHtml, /期现数据管理/);
  assert.match(indexHtml, /现货数据展示/);
  assert.match(indexHtml, /期现数据展示/);
  assert.match(appJs, /IronOreBasis\.activateManagement/);
  assert.match(appJs, /IronOreBasis\.activateDisplay/);
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

test("basis chart requests only the active port and renders negative values with a zero axis", () => {
  assert.match(basisJs, /display\/chart\?port=" \+ encodeURIComponent\(basisState\.activePort\)/);
  assert.match(basisJs, /Math\.min\(0,/);
  assert.match(basisJs, /Math\.max\(0,/);
  assert.match(basisJs, /drawBasisZeroAxis/);
  assert.match(basisJs, /basisMonthLabel/);
  assert.match(basisCss, /iron-ore-basis-chart-container/);
});

test("basis assets are loaded after the existing app controller", () => {
  assert.ok(indexHtml.indexOf("/static/app.js") < indexHtml.indexOf("/static/iron_ore_basis.js"));
  assert.match(indexHtml, /iron_ore_basis\.css\?v=/);
});

test("mobile app shell grows beyond the sidebar so the workspace remains reachable", () => {
  assert.match(stylesCss, /@media \(max-width: 900px\)[\s\S]*?\.app-shell \{[\s\S]*?height: auto;[\s\S]*?min-height: 100vh;/);
});
