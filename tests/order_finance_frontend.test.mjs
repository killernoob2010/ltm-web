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

test("order finance capital monitor puts bank exposure and selected detail before breakdowns", () => {
  const capitalStart = indexHtml.indexOf('id="orderFinanceCapitalPage"');
  assert.notEqual(capitalStart, -1);
  const capitalEnd = indexHtml.indexOf('id="orderFinanceManualDialog"', capitalStart);
  assert.notEqual(capitalEnd, -1);
  const capitalHtml = indexHtml.slice(capitalStart, capitalEnd);

  assert.match(capitalHtml, /order-finance-capital-layout/);
  assert.match(capitalHtml, /order-finance-capital-primary/);
  assert.match(capitalHtml, /order-finance-capital-breakdowns/);
  assert.ok(capitalHtml.indexOf("orderFinanceBankList") < capitalHtml.indexOf("orderFinanceSelectedBankTable"));
  assert.ok(capitalHtml.indexOf("orderFinanceSelectedBankTable") < capitalHtml.indexOf("orderFinanceEntityList"));
  assert.match(stylesCss, /\.order-finance-capital-primary/);
  assert.match(stylesCss, /\.order-finance-capital-breakdowns/);
  assert.match(stylesCss, /\.capital-summary\s*\{[\s\S]*grid-template-columns:\s*repeat\(4, minmax\(0, 1fr\)\)/);
});
