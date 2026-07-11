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
  assert.match(html, /id="tradingOverviewView"/);
  assert.match(html, /id="tradingFactsView"/);
  assert.match(html, /id="tradingBusinessView"/);
  assert.match(html, /id="tradingExportView"/);
});

test("three-table import requires trade close and position files with preview confirmation", () => {
  assert.match(html, /id="tradingTradeFile"/);
  assert.match(html, /id="tradingCloseFile"/);
  assert.match(html, /id="tradingPositionFile"/);
  assert.match(tradingJs, /成交、平仓、持仓三表必须齐全/);
  assert.match(tradingJs, /\/imports\/preview/);
  assert.match(tradingJs, /\/confirm/);
});

test("whole trades can be classified and business close relationships can be rematched", () => {
  assert.match(tradingJs, /系统按整笔归属，不允许按手数拆分/);
  assert.match(tradingJs, /business-assignments\/batch-confirm/);
  assert.match(tradingJs, /business-closes\/\$\{tm\.rematch\.closeId\}\/preview/);
  assert.match(tradingJs, /restore-default/);
  assert.match(html, /事实层不变/);
});

test("pending calculations and reserved export are visible in the first version", () => {
  assert.match(tradingJs, /待计算/);
  assert.match(html, /功能位置已预留/);
  assert.match(html, /持仓、即期持仓和结算口径确认后统一开发/);
  assert.match(css, /\.trading-pending/);
  assert.match(css, /\.trading-placeholder/);
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
