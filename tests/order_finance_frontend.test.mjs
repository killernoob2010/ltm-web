import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const appJs = readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");
const indexHtml = readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");
const stylesCss = readFileSync(new URL("../frontend/styles.css", import.meta.url), "utf8");

test("order finance page switch hides capital monitor before showing progress", () => {
  const showOnlyStart = appJs.indexOf("function showOnly(page)");
  assert.notEqual(showOnlyStart, -1);
  const showOnlyEnd = appJs.indexOf("async function activateModule", showOnlyStart);
  assert.notEqual(showOnlyEnd, -1);
  const showOnlyBody = appJs.slice(showOnlyStart, showOnlyEnd);

  assert.match(showOnlyBody, /orderFinancePage/);
  assert.match(showOnlyBody, /orderFinanceCapitalPage/);
});

test("order finance hides empty intermediate stage filters after data load", () => {
  assert.match(appJs, /ORDER_FINANCE_STAGE_FILTERS/);
  assert.match(appJs, /hideWhenEmpty:\s*true/);
  assert.match(appJs, /function syncOrderFinanceStageFilters\(/);
  assert.match(appJs, /button\.classList\.toggle\("hidden", shouldHide\)/);
  assert.match(appJs, /state\.orderFinanceFilter = "all"/);
});

test("order finance capital monitor follows the approved prototype structure", () => {
  const capitalStart = indexHtml.indexOf('id="orderFinanceCapitalPage"');
  assert.notEqual(capitalStart, -1);
  const capitalEnd = indexHtml.indexOf('id="orderFinanceManualDialog"', capitalStart);
  assert.notEqual(capitalEnd, -1);
  const capitalHtml = indexHtml.slice(capitalStart, capitalEnd);

  assert.match(capitalHtml, /class="capital-summary"/);
  assert.match(capitalHtml, /class="capital-grid"/);
  assert.match(capitalHtml, /monitor-panel bank-panel/);
  assert.match(capitalHtml, /section-label">银行额度/);
  assert.match(capitalHtml, /section-label">主体与工厂/);
  assert.match(capitalHtml, /section-label">资金日历/);
  assert.match(capitalHtml, /monitor-panel selected-bank/);
  assert.ok(capitalHtml.indexOf("orderFinanceBankList") < capitalHtml.indexOf("orderFinanceEntityList"));
  assert.ok(capitalHtml.indexOf("orderFinanceEntityList") < capitalHtml.indexOf("orderFinanceDueBuckets"));
  assert.ok(capitalHtml.indexOf("orderFinanceDueBuckets") < capitalHtml.indexOf("orderFinanceSelectedBankTable"));
  assert.match(capitalHtml, /class="split-stack"/);
  assert.match(appJs, /class="metric-card \${tone}"/);
  assert.match(appJs, /class="bank-row \${state\.selectedOrderFinanceBank === bank\.bank \? "selected" : ""}"/);
  assert.match(stylesCss, /\.capital-grid\s*\{[\s\S]*grid-template-columns:\s*minmax\(0, 1\.25fr\) minmax\(0, 0\.9fr\)/);
  assert.match(stylesCss, /\.bank-panel\s*\{[\s\S]*grid-row:\s*span 2/);
  assert.match(stylesCss, /\.bank-row:hover,\s*\.bank-row\.selected/);
});

test("order finance progress keeps approved layout while showing latest shipment and repayment dates", () => {
  const contractStart = appJs.indexOf("function renderOrderFinanceContract");
  assert.notEqual(contractStart, -1);
  const contractEnd = appJs.indexOf("function renderOrderFinanceContracts", contractStart);
  assert.notEqual(contractEnd, -1);
  const contractRenderer = appJs.slice(contractStart, contractEnd);

  assert.match(contractRenderer, /class="order-finance-field-strip"/);
  assert.match(contractRenderer, /orderFinanceField\("最迟装船日"/);
  assert.match(contractRenderer, /orderFinanceField\("展期状态"/);
  assert.match(contractRenderer, /orderFinanceField\("还款日"/);
  assert.doesNotMatch(contractRenderer, /orderFinanceField\("提单日"/);
  assert.doesNotMatch(contractRenderer, /orderFinanceField\("船名\/航次"/);
  assert.match(appJs, /function orderFinanceShipmentText\(/);
  assert.match(appJs, /function orderFinanceShipmentTone\(/);
  assert.match(appJs, /item\.repayment_timing/);
});

test("order finance detail omits unavailable bill date and keeps document and repayment", () => {
  assert.doesNotMatch(appJs, /<th>提单日<\/th>/);
  assert.match(appJs, /<th>交单日<\/th>/);
  assert.match(appJs, /<th>还款日<\/th>/);
  assert.doesNotMatch(appJs, /<th>收汇日<\/th>/);
  assert.match(indexHtml, />已交单待回款<\/button>/);
  assert.match(indexHtml, />已还款待结案<\/button>/);
  assert.match(appJs, /\["缺最迟装船\/交单\/还款"/);
  assert.doesNotMatch(appJs, /row\.bill_date \|\| "-"/);
  assert.match(appJs, /if \(item\.stage === "已完成"\) return "已结案"/);
  assert.match(appJs, /bank\.difference/);
  assert.match(appJs, /订单计算/);
});

test("order finance shipment deadline has warning and overdue visual tones", () => {
  assert.match(stylesCss, /\.order-finance-field\.danger/);
  assert.match(appJs, /if \(item\.stage === "已完成"\) return value \|\| "未提供"/);
  assert.match(appJs, /days <= 7/);
  assert.match(appJs, /需联系工厂/);
  assert.match(appJs, /已超过/);
});
