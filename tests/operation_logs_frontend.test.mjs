import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const appJs = fs.readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");
const indexHtml = fs.readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");

const blockStart = appJs.indexOf("// 操作日志");
const blockEnd = appJs.indexOf("function orderFinanceDisplayAmount", blockStart);
const operationLogBlock = appJs.slice(blockStart, blockEnd);

test("operation log dialog exposes date filters and incremental controls", () => {
  assert.match(indexHtml, /id="logsStartDate"/);
  assert.match(indexHtml, /id="logsEndDate"/);
  assert.match(indexHtml, /id="logsLoadMoreBtn"/);
  assert.match(indexHtml, /id="logsPageInfo"/);
  assert.match(indexHtml, /导出当前已加载记录/);
});

test("operation log loader uses cursor pages without offset", () => {
  assert.match(operationLogBlock, /params\.set\("limit", "100"\)/);
  assert.match(operationLogBlock, /params\.set\("cursor", state\.operationLogCursor\)/);
  assert.match(operationLogBlock, /start_date/);
  assert.match(operationLogBlock, /end_date/);
  assert.match(operationLogBlock, /next_cursor/);
  assert.match(operationLogBlock, /append/);
  assert.doesNotMatch(operationLogBlock, /offset/);
});

test("operation log export is limited to loaded state rows", () => {
  assert.match(operationLogBlock, /state\.operationLogs/);
  assert.match(operationLogBlock, /当前已加载/);
});

