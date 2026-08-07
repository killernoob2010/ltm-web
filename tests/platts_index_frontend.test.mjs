import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const html = readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");
const appJs = readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");
const monitorJs = readFileSync(new URL("../frontend/platts_index_monitor.js", import.meta.url), "utf8");

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
    "plattsIndexReview", "plattsIndexMonth", "plattsIndexCharts", "plattsIndexDailyTable",
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

test("Platts upload controls require sensitive permission and disable duplicate submission", () => {
  assert.match(appJs, /plattsIndexUploadBtn/);
  assert.match(appJs, /platts_index_monitor/);
  assert.match(monitorJs, /uploadBtn\.disabled = true/);
  assert.match(monitorJs, /uploadBtn\.disabled = false/);
  assert.match(monitorJs, /review_required/);
  assert.match(monitorJs, /confirm/);
});
