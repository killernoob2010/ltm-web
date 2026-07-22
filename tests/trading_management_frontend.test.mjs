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

test("settlement import uses one txt file with automatic daily or monthly detection", () => {
  assert.match(tradingJs, /id="tmStatementFile"/);
  assert.doesNotMatch(tradingJs, /id="tmTradeFile"/);
  assert.match(tradingJs, /accept="\.txt"/);
  assert.match(tradingJs, /系统自动识别日结单或月结单/);
  assert.match(tradingJs, /statement_file/);
  assert.match(tradingJs, /\/imports\/preview/);
  assert.match(tradingJs, /\/confirm/);
  assert.match(tradingJs, /正在解析并预检结算单，请稍候/);
  assert.match(tradingJs, /function pollImportJob/);
  assert.match(tradingJs, /\/imports\/jobs\//);
  assert.match(tradingJs, /写入并切换事实/);
  assert.match(tradingJs, /建立开平匹配/);
  assert.match(tradingJs, /重建业务分摊/);
});

test("whole trades can be classified and business close relationships can be rematched", () => {
  assert.match(tradingJs, /归属设置在完整开仓成交；平仓和到期了结按开平分摊自动继承/);
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
    "tm-filter-summary", "tm-drawer", "tm-toast",
  ]) assert.match(html + tradingJs + css, new RegExp(token));
});

test("all three ledgers use the approved prototype tab order", () => {
  assert.match(tradingJs, /当前持仓[\s\S]*平仓记录[\s\S]*全部交易/);
  assert.doesNotMatch(tradingJs, /业务持仓[\s\S]*业务成交[\s\S]*业务平仓/);
});

test("option lifecycle events reuse close records with one type column", () => {
  assert.match(tradingJs, /\["settlement_type","了结类型"\]/);
  assert.match(tradingJs, /trade_close:\s*"普通平仓"/);
  assert.match(tradingJs, /exercise:\s*"行权"/);
  assert.match(tradingJs, /assignment:\s*"履约"/);
  assert.match(tradingJs, /expiry_abandon:\s*"到期放弃"/);
  assert.match(tradingJs, /成交平仓手数/);
  assert.doesNotMatch(tradingJs, /行权与到期.*tm-tab-button/);
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
  for (const label of ["看涨\/看跌", "行权价", "Delta", "Gamma", "Theta", "Vega"]) {
    assert.match(tradingJs, new RegExp(label));
  }
  assert.doesNotMatch(tradingJs, /<th>标的<\/th>/);
  assert.doesNotMatch(tradingJs, /<th>标的价格<\/th>/);
  assert.doesNotMatch(tradingJs, /德尔塔|伽马|西塔|维伽/);
});

test("fact tabs cache by filters without prefetching sibling tabs", () => {
  assert.match(tradingJs, /function factCacheKey/);
  assert.match(tradingJs, /function loadFactData/);
  assert.doesNotMatch(tradingJs, /function prefetchFactTabs/);
  assert.match(tradingJs, /function invalidateFactCache/);
  assert.match(tradingJs + css, /tm-table-loading/);
});

test("fact filters preserve their visible values after rerender", () => {
  assert.match(tradingJs, /value="\$\{esc\(tm\.assetType\)\}"/);
  assert.match(tradingJs, /value="\$\{esc\(tm\.side\)\}"/);
  assert.match(tradingJs, /value="\$\{esc\(tm\.openClose\)\}"/);
  assert.match(tradingJs, /value="\$\{esc\(tm\.classification\)\}"/);
  assert.match(tradingJs, /value="\$\{factDateValue\(tm\.dateFrom\)\}"/);
  assert.match(tradingJs, /value="\$\{factDateValue\(tm\.dateTo\)\}"/);
});

test("trading filters use compact one-row desktop sizing", () => {
  assert.match(tradingJs, /tm-filters compact/);
  assert.match(tradingJs, /tm-filter-search/);
  assert.match(tradingJs, /tm-filter-select/);
  assert.match(tradingJs, /tm-filter-date/);
  assert.match(css, /grid-template-columns:140px 58px/);
  assert.match(css, /@media \(max-width:1209px\)/);
});

test("business ledgers keep only the summary rows beside tabs and filters", () => {
  assert.doesNotMatch(tradingJs, /<h2>口径说明<\/h2>/);
  assert.doesNotMatch(tradingJs, /tm-ledger-hero/);
  assert.doesNotMatch(tradingJs, /tm-ledger-summary-primary/);
  assert.doesNotMatch(tradingJs, /tm-ledger-summary-risk/);
  assert.match(tradingJs, /businessFilterSummary\(data\.summary,view,tab\)/);
  assert.match(tradingJs, /\["浮动盈亏",summary\.floating_pnl\]/);
});

test("overview chart renders real daily close pnl instead of a fixed placeholder", () => {
  assert.match(tradingJs, /function dailyPnlChart/);
  assert.match(tradingJs, /data\.daily_close_pnl/);
  assert.match(tradingJs, /row\.fact_close_pnl/);
  assert.doesNotMatch(tradingJs, /52,125 190,125 328,125/);
  assert.match(tradingJs, /暂无平仓盈亏数据/);
});

test("prototype assignment status and business pagination are functional contracts", () => {
  assert.match(tradingJs, /业务类型 \/ 策略/);
  assert.match(tradingJs, /待确认/);
  assert.match(tradingJs, /tmSelectFiltered/);
  assert.match(tradingJs, /selection-identities/);
  assert.match(tradingJs, /setClassificationBusy/);
  assert.match(tradingJs, /tmClassificationProgress/);
  assert.match(tradingJs, /classification/);
  assert.match(tradingJs, /junnengPageSize:\s*20/);
  assert.match(tradingJs, /optionsPageSize:\s*20/);
  assert.match(tradingJs, /pagination\(data,view\)/);
  assert.doesNotMatch(tradingJs, /business\/\$\{view\}\/\$\{tab\}\?page_size=100/);
  assert.match(tradingJs, /业务归属盈亏/);
});

test("overview uses one compact row for the three secondary cards and real periods", () => {
  assert.match(tradingJs + css, /tm-overview-mini-grid/);
  assert.match(css, /grid-template-columns:repeat\(3,minmax\(0,1fr\)\)/);
  assert.match(tradingJs, /data-overview-period/);
  assert.match(tradingJs, /data-overview-business/);
  for (const label of ["全部", "基础套保", "战略套保"]) {
    assert.match(tradingJs, new RegExp(label));
  }
  assert.match(tradingJs, /params\.set\("business_type",tm\.overviewBusinessType\)/);
  assert.match(tradingJs, /tmOverviewFrom/);
  assert.match(tradingJs, /tmOverviewTo/);
});

test("business ledgers are classified archives without candidate controls", () => {
  assert.doesNotMatch(tradingJs, /businessClassification/);
  assert.doesNotMatch(tradingJs, /默认展示全部 RB\/HC 候选/);
  assert.doesNotMatch(tradingJs, /id="\$\{view\}Classification"/);
  assert.match(tradingJs, /仅展示已完成业务归属的数据/);
});

test("Shanghai Junneng shows live positions and the five settlement metrics", () => {
  for (const label of ["最新价", "行情时间", "浮动盈亏", "估值状态"]) {
    assert.match(tradingJs, new RegExp(label));
  }
  for (const label of ["平仓盈亏（含手续费）", "资金利息", "80%结算金额", "20%结算金额", "手续费"]) {
    assert.match(tradingJs, new RegExp(label));
  }
  assert.doesNotMatch(tradingJs, /\["settlement_rule_version","规则版本"\]/);
  assert.doesNotMatch(tradingJs, /结算规则：\$\{/);
  assert.match(tradingJs, /latest_junneng_close_date/);
  assert.match(tradingJs, /monthRangeForDate/);
  assert.match(tradingJs, /businessDates:\s*\{\s*junneng:/);
});

test("option positions show valuation results without internal source or status columns", () => {
  for (const label of ["估值价", "IV", "浮动盈亏", "Delta", "Gamma", "Theta", "Vega"]) {
    assert.match(tradingJs, new RegExp(label));
  }
  for (const field of ["row.delta", "row.gamma", "row.theta", "row.vega"]) {
    assert.match(tradingJs, new RegExp(field));
  }
  assert.doesNotMatch(tradingJs, /<th>[^<]*敞口<\/th>/);
  for (const greek of ["Delta", "Gamma", "Theta", "Vega"]) {
    assert.match(tradingJs, new RegExp(`<th>${greek}<\\/th>`));
  }
  assert.doesNotMatch(tradingJs, /<th>估值来源<\/th>/);
  assert.doesNotMatch(tradingJs, /<th>估值状态<\/th>/);
  assert.doesNotMatch(tradingJs, /<th>到期日<\/th>/);
  assert.doesNotMatch(tradingJs, /<th>估值日<\/th>/);
  assert.doesNotMatch(tradingJs, /row\.underlying_symbol/);
  assert.doesNotMatch(tradingJs, /row\.underlying_price/);
  assert.doesNotMatch(tradingJs, /row\.expiry_date/);
  assert.doesNotMatch(tradingJs, /row\.valuation_date/);
  assert.match(tradingJs, /colspan="13"/);
  assert.match(tradingJs, /Number\(row\.iv\) \* 100/);
  assert.doesNotMatch(tradingJs, /风险指标<\/span><strong>待计算/);
  assert.match(tradingJs, /每15秒刷新/);
  assert.match(tradingJs, /IV 与 Greeks 不作为实时值/);
  assert.match(tradingJs, /明细 Greeks 为带方向的单手口径/);
});

test("option Greeks use four decimals without showing expiry metadata", () => {
  assert.match(
    tradingJs,
    /minimumFractionDigits:\s*4,\s*maximumFractionDigits:\s*4/,
  );
  assert.doesNotMatch(tradingJs, /（已到期）/);
});

test("option quote refresh shows last update time and whether values changed", () => {
  assert.match(tradingJs, /quoteRefreshState/);
  assert.match(tradingJs, /上次更新时间/);
  assert.match(tradingJs, /更新状态/);
  assert.match(tradingJs, /数据已更新/);
  assert.match(tradingJs, /已检查，行情无变化/);
  assert.match(tradingJs, /更新失败/);
  assert.match(tradingJs, /row\.valuation_status\s*\|\|\s*row\.market_data_status/);
  assert.match(tradingJs, /renderBusinessLedger\(tm\.view,\s*"timer"\)/);
});

test("visible business position pages refresh quotes every fifteen seconds", () => {
  assert.match(tradingJs, /BUSINESS_QUOTE_REFRESH_MS\s*=\s*15000/);
  assert.match(tradingJs, /document\.visibilityState\s*===\s*"visible"/);
  assert.match(tradingJs, /tm\[tabKey\]\s*!==\s*"positions"/);
  assert.match(tradingJs, /window\.setInterval/);
  assert.match(tradingJs, /stopBusinessQuoteRefresh/);
  assert.match(tradingJs, /businessQuoteRefreshInFlight/);
  assert.match(tradingJs, /tradingManagementPage"\)\.classList\.contains\("hidden"\)/);
  assert.match(tradingJs, /deactivate\(\)/);
  assert.match(tradingJs, /MutationObserver/);
  assert.match(tradingJs, /tradingManagementPage/);
});
