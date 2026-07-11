import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const appJs = readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");
const tradingJs = readFileSync(new URL("../frontend/trading_management.js", import.meta.url), "utf8");
const html = readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");
const css = readFileSync(new URL("../frontend/trading_management.css", import.meta.url), "utf8");

test("trading management is a separate first-level module with five page modes", () => {
  assert.match(appJs, /tradingManagementPage/);
  for (const code of ["trading_overview", "trading_positions", "trading_sh_junneng", "trading_options", "trading_export"]) {
    assert.match(appJs, new RegExp(code));
  }
  assert.match(html, /id="tmOverviewView"/);
  assert.match(html, /id="tmPositionsView"/);
  assert.match(html, /id="tmJunnengView"/);
  assert.match(html, /id="tmOptionsView"/);
  assert.match(html, /id="tmExportView"/);
});

test("three-table import requires trade close and position files with preview confirmation", () => {
  assert.match(tradingJs, /id="tmTradeFile"/);
  assert.match(tradingJs, /id="tmCloseFile"/);
  assert.match(tradingJs, /id="tmPositionFile"/);
  assert.match(tradingJs, /成交、平仓、持仓三表必须齐全/);
  assert.match(tradingJs, /\/imports\/preview/);
  assert.match(tradingJs, /\/confirm/);
  assert.match(tradingJs, /正在预检三表，请稍候/);
  assert.match(tradingJs, /正在覆盖导入并建立事实匹配，请勿关闭窗口/);
});

test("whole trades can be classified and business close relationships can be rematched", () => {
  assert.match(tradingJs, /一笔成交按完整手数归属，不允许拆分/);
  assert.match(tradingJs, /business-assignments\/batch-confirm/);
  assert.match(tradingJs, /business-closes\/\$\{closeId\}\/preview/);
  assert.match(tradingJs, /restore-default/);
  assert.match(tradingJs, /事实层不变/);
});

test("pending calculations and reserved export are visible in the first version", () => {
  assert.match(tradingJs, /待计算/);
  assert.match(tradingJs, /功能暂未开放/);
  assert.match(tradingJs, /本期不生成真实文件，功能位置按原型保留/);
  assert.match(css, /\.tm-pending/);
  assert.match(css, /\.tm-export-list/);
});

test("trading workspace ports the approved prototype components", () => {
  for (const token of [
    "tm-workspace-topbar", "tm-summary-band", "tm-content-grid", "tm-panel-header",
    "tm-filter-summary", "tm-ledger-hero", "tm-drawer", "tm-toast",
  ]) assert.match(html + tradingJs + css, new RegExp(token));
});

test("all three ledgers use the approved prototype tab order", () => {
  assert.match(tradingJs, /当前持仓[\s\S]*平仓记录[\s\S]*全部交易/);
  assert.doesNotMatch(tradingJs, /业务持仓[\s\S]*业务成交[\s\S]*业务平仓/);
});

test("prototype sections replace the simplified placeholder layout", () => {
  assert.match(tradingJs, /逐日平仓盈亏趋势/);
  assert.match(tradingJs, /数据质量/);
  assert.match(tradingJs, /业务归属分布/);
  assert.match(tradingJs, /统一输出/);
  assert.doesNotMatch(html, /class="trading-metric-grid"/);
});

test("option positions preserve the prototype anatomy and risk columns", () => {
  assert.match(tradingJs, /function optionAnatomy/);
  for (const label of ["标的", "看涨\/看跌", "行权价", "Delta", "Gamma", "Theta", "Vega"]) {
    assert.match(tradingJs, new RegExp(label));
  }
});

test("fact tabs cache by filters and prefetch sibling tabs", () => {
  assert.match(tradingJs, /function factCacheKey/);
  assert.match(tradingJs, /function loadFactData/);
  assert.match(tradingJs, /function prefetchFactTabs/);
  assert.match(tradingJs, /function invalidateFactCache/);
  assert.match(tradingJs + css, /tm-table-loading/);
});
