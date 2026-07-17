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

test("order finance progress shows the decision fields without duplicate confirmation or extension cards", () => {
  const contractStart = appJs.indexOf("function renderOrderFinanceContract");
  assert.notEqual(contractStart, -1);
  const contractEnd = appJs.indexOf("function renderOrderFinanceContracts", contractStart);
  assert.notEqual(contractEnd, -1);
  const contractRenderer = appJs.slice(contractStart, contractEnd);

  assert.match(contractRenderer, /class="order-finance-field-strip"/);
  assert.match(contractRenderer, /orderFinanceField\("贷款行\/融资金额", orderFinanceBankAmountText\(item\), "", "single-line"\)/);
  assert.match(contractRenderer, /orderFinanceField\("装船状态"/);
  assert.match(contractRenderer, /orderFinanceField\("交单状态"/);
  assert.match(contractRenderer, /orderFinanceField\("融资到期日"[^\n]+"wide"\)/);
  assert.match(contractRenderer, /orderFinanceField\("回款状态"[^\n]+"wide"\)/);
  assert.doesNotMatch(contractRenderer, /orderFinanceField\("展期状态"/);
  assert.doesNotMatch(contractRenderer, /orderFinanceField\("确认状态"/);
  assert.doesNotMatch(contractRenderer, /orderFinanceField\("提单日"/);
  assert.doesNotMatch(contractRenderer, /orderFinanceField\("船名\/航次"/);
  assert.match(appJs, /function orderFinanceShipmentText\(/);
  assert.match(appJs, /item\.shipment_basis === "document"/);
  assert.match(appJs, /return "已根据交单日认定装船"/);
  assert.doesNotMatch(appJs, /已根据交单日认定装船：/);
  assert.match(appJs, /function orderFinanceDocumentText\(/);
  assert.match(appJs, /function orderFinancePaymentDueText\(/);
  assert.match(appJs, /function orderFinancePaymentText\(/);
  assert.match(appJs, /function orderFinanceShipmentTone\(/);
  assert.match(appJs, /item\.repayment_timing/);
  assert.match(appJs, /if \(item\.document_date\) return `已交单 \/ \$\{item\.document_date\}`/);
  assert.match(appJs, /return "待交单"/);
  assert.match(appJs, /`\$\{orderFinanceWan\(item\.total_finance, 1\)\}（\$\{item\.financing_count\}笔）`/);
  assert.doesNotMatch(appJs, /`\$\{item\.financing_count\}笔 \/ \$\{orderFinanceWan\(item\.total_finance, 1\)\}`/);
  assert.doesNotMatch(appJs, /截止日未提供|待交单 \/ 截止/);
  assert.match(stylesCss, /\.order-finance-field\.wide\s*\{[\s\S]*grid-column:\s*span 2/);
  assert.match(stylesCss, /\.order-finance-field\.wide strong\s*\{[\s\S]*white-space:\s*nowrap/);
  assert.match(stylesCss, /\.order-finance-field\.single-line strong\s*\{[\s\S]*white-space:\s*nowrap/);
  assert.match(stylesCss, /\.order-finance-field\s*\{[\s\S]*height:\s*58px/);
});

test("order finance detail is compact for one financing and complete per row for multiple financings", () => {
  assert.doesNotMatch(appJs, /<th>提单日<\/th>/);
  assert.doesNotMatch(appJs, /<th>交单日<\/th>/);
  assert.match(appJs, /item\.financing_count === 1/);
  assert.match(appJs, /<th>利率<\/th>/);
  assert.match(appJs, /<th>原到期日<\/th>/);
  assert.match(appJs, /<th>新到期日<\/th>/);
  assert.match(appJs, /<th>展期天数<\/th>/);
  assert.doesNotMatch(appJs, /<th>来源<\/th>/);
  assert.doesNotMatch(appJs, /row\.source_file|row\.source_sheet|row\.source_row_start/);
  assert.match(appJs, /<th>融资到期日<\/th>/);
  assert.match(appJs, /<th>回款日<\/th>/);
  assert.match(appJs, /<th>状态<\/th>/);
  assert.doesNotMatch(appJs, /<th>收汇日<\/th>/);
  assert.match(indexHtml, />待放款<\/button>/);
  assert.match(indexHtml, />已装船待交单<\/button>/);
  assert.match(indexHtml, />已交单待回款<\/button>/);
  assert.match(indexHtml, />已回款待结案<\/button>/);
  assert.doesNotMatch(appJs, /\["缺最迟装船\/交单\/还款"/);
  assert.doesNotMatch(appJs, /row\.bill_date \|\| "-"/);
  assert.match(appJs, /bank\.difference/);
  assert.match(appJs, /订单计算/);
});

test("order finance shipment deadline has warning and overdue visual tones", () => {
  assert.match(stylesCss, /\.order-finance-field\.danger/);
  assert.match(appJs, /if \(item\.stage === "已完成"\) return value \|\| "未提供"/);
  assert.match(appJs, /days <= 10/);
  assert.match(appJs, /需联系工厂/);
  assert.match(appJs, /已超过/);
});

test("order finance weekly focus uses ten-day actions and persistent reminders", () => {
  assert.doesNotMatch(indexHtml, />本周重点\/高风险</);
  assert.match(indexHtml, />本周重点</);
  assert.match(indexHtml, /id="orderFinanceReminderDialog"/);
  assert.match(indexHtml, /id="orderFinanceReminderNote"/);
  assert.match(indexHtml, /id="orderFinanceReminderDate"[^>]*type="date"/);
  assert.match(indexHtml, /id="clearOrderFinanceReminderBtn"/);
  assert.match(appJs, /filter === "focusRisk" && !item\.is_weekly_focus/);
  assert.match(appJs, /\["本周重点", summary\.focus_risk \|\| 0\]/);
  assert.match(appJs, /order-finance-reminder-btn/);
  assert.match(appJs, /order-finance-reminder-line/);
  assert.match(appJs, /\/reminder`/);
  assert.match(appJs, /manager_note/);
  assert.match(appJs, /next_follow_up_date/);
  assert.match(appJs, /confirmAction\("清除备注提醒"/);
  assert.match(stylesCss, /\.order-finance-reminder-line/);
});

test("order finance colors only the indicator fields that cause risk", () => {
  assert.match(appJs, /function indicatorRiskTone\(item, key\)/);
  assert.match(appJs, /item\.indicator_risks\?\.\[key\]/);
  assert.match(appJs, /level === "高" \? "danger" : level === "中" \? "warning" : ""/);
  assert.match(appJs, /indicatorRiskTone\(item, "shipment"\)/);
  assert.match(appJs, /indicatorRiskTone\(item, "document"\)/);
  assert.match(appJs, /indicatorRiskTone\(item, "payment"\)/);
  assert.match(appJs, /const riskClass = item\.risk === "高"/);
});

test("order finance supports shipment confirmation and removes import report", () => {
  assert.match(indexHtml, /id="orderFinanceShipmentDialog"/);
  assert.match(indexHtml, /id="orderFinanceShipmentDate"[^>]*type="date"/);
  assert.match(appJs, /order-finance-shipment-confirm-btn/);
  assert.match(appJs, /order-finance-shipment-undo-btn/);
  assert.match(appJs, /\/shipment-confirmation/);
  assert.match(appJs, /shipment_confirmed_date/);
  assert.match(appJs, /!item\.document_date && !item\.shipment_completed/);
  assert.match(appJs, /\/api\/order-finance\/progress\?ts=\$\{Date\.now\(\)\}/);
  assert.doesNotMatch(indexHtml, /id="orderFinanceImportReport"/);
  assert.doesNotMatch(indexHtml, />导入报告</);
  assert.doesNotMatch(appJs, /orderFinanceImportReport/);
  assert.match(appJs, /导入完成：\$\{summary\.record_count \|\| 0\} 条，异常 \$\{summary\.warning_count \|\| 0\} 条/);
});

test("order finance shows compact automatic sync status and new payment terminology", () => {
  assert.match(appJs, /function renderOrderFinanceSyncStatus\(/);
  assert.match(appJs, /上次同步：\$\{orderFinanceSyncTime\(syncStatus\.last_success_at\)\}/);
  assert.match(appJs, /更新 \$\{Number\(syncStatus\.changed_count \|\| 0\)\} 条/);
  assert.match(appJs, /renderOrderFinanceSyncStatus\(result\.sync_status\)/);
  assert.doesNotMatch(appJs, /已装船待回款/);
  assert.doesNotMatch(appJs, /已还款待结案/);
  assert.doesNotMatch(appJs, /回款到期日/);
  assert.doesNotMatch(indexHtml, /回款到期日/);
  assert.doesNotMatch(appJs, /7天内回款到期|30天内回款到期/);
  assert.match(appJs, /7天内融资到期|30天内融资到期/);
  assert.match(appJs, /融资到期日/);
  assert.match(indexHtml, /融资到期日/);
  assert.match(indexHtml, /app\.js\?v=risk-alert-history-grouping-20260717/);
  assert.match(indexHtml, /styles\.css\?v=risk-alert-summary-layout-20260717/);
});

test("documented unpaid orders show a missing financing due-date anomaly", () => {
  assert.match(
    appJs,
    /if \(item\.stage === "已交单待回款" && !dueDate\) return "融资到期日缺失"/,
  );
  assert.match(indexHtml, /app\.js\?v=risk-alert-history-grouping-20260717/);
});
