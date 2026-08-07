import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const html = readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");
const appJs = readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");
const monitorJs = readFileSync(new URL("../frontend/platts_index_monitor.js", import.meta.url), "utf8");
const styles = readFileSync(new URL("../frontend/styles.css", import.meta.url), "utf8");

test("Platts module is placed after realtime information summary and has a dedicated page", () => {
  assert.match(html, /id="plattsIndexPage" class="page hidden"/);
  assert.match(html, /普氏指数监控/);
  assert.match(appJs, /code === "platts_index_monitor"/);
  assert.match(appJs, /window\.PlattsIndexMonitor\.activate/);
  assert.match(appJs, /canModuleSensitive\("platts_index_monitor"\)/);
});

test("Platts page exposes one-click upload, review state, month selection, six charts and a data table", () => {
  for (const id of [
    "plattsIndexUploadBtn", "plattsIndexUploadFile", "plattsIndexImportStatus",
    "plattsIndexImportCounts", "plattsIndexReview", "plattsIndexMonth", "plattsIndexPrevMonth",
    "plattsIndexNextMonth", "plattsIndexMonthLabel", "plattsIndexCharts", "plattsIndexDailyTable",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(monitorJs, /platts_lp/);
  assert.match(monitorJs, /platts_61/);
  assert.match(monitorJs, /platts_58/);
  assert.match(monitorJs, /platts_65/);
  assert.match(monitorJs, /spread_65_62/);
  assert.match(monitorJs, /spread_65_61/);
  assert.match(monitorJs, /MTD/);
  assert.match(monitorJs, /touchstart|click/);
});

test("Platts values use one display unit and preserve an explicitly selected empty month", () => {
  assert.doesNotMatch(monitorJs, /美元\/干吨/);
  assert.equal((monitorJs.match(/unit: "美元\/吨"/g) || []).length, 6);
  assert.match(monitorJs, /暂无已入库数据/);
  assert.match(monitorJs, /最近有数据月份/);
  assert.doesNotMatch(monitorJs, /if \(!summary\.count && summary\.latest_month/);
});

test("Platts V1.1 uses full names, stored month list, exact dates and a two-column desktop grid", () => {
  for (const label of [
    "Platts LP", "Platts 61%", "Platts 58%", "Platts 65%", "Platts 65/62", "Platts 65/61",
  ]) {
    assert.match(monitorJs, new RegExp(label.replace(/[/%]/g, "\\$&")));
    assert.match(html, new RegExp(label.replace(/[/%]/g, "\\$&")));
  }
  assert.match(monitorJs, /available_months/);
  assert.match(monitorJs, /上一月/);
  assert.match(monitorJs, /下一月/);
  assert.match(monitorJs, /mousemove/);
  assert.match(monitorJs, /selected\.date/);
  assert.match(styles, /grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/);
  assert.match(styles, /height:\s*220px/);
});

test("Platts upload controls require sensitive permission and disable duplicate submission", () => {
  assert.match(appJs, /plattsIndexUploadBtn/);
  assert.match(appJs, /platts_index_monitor/);
  assert.match(monitorJs, /uploadBtn\.disabled = true/);
  assert.match(monitorJs, /uploadBtn\.disabled = false/);
  assert.match(monitorJs, /review_required/);
  assert.match(monitorJs, /confirm/);
});
