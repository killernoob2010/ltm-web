import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const appJs = readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");
const indexHtml = readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");

test("data visualization filters preserve empty selections", () => {
  assert.match(appJs, /function appendMultiSelectParam\(/);
  assert.match(appJs, /selectedValues\.length === 0/);
  assert.match(appJs, /__EMPTY__/);
});

test("integrated data cells without ids are not inline editable", () => {
  assert.match(appJs, /if \(!cell\.dataset\.id\) return;/);
});

test("data visualization chart page exposes arrival and chart modes", () => {
  assert.match(indexHtml, /data-metric="arrival"/);
  assert.match(indexHtml, /到港图表/);
  assert.match(indexHtml, /全品种图谱/);
  assert.match(indexHtml, /品种对比图/);
});

test("data visualization integration labels separate shipment and arrival", () => {
  assert.match(appJs, /if \(metric === "shipment"\) return "发运";/);
  assert.match(appJs, /if \(metric === "arrival"\) return "到港";/);
});

test("data visualization chart filters use product pools instead of always-flat filters", () => {
  assert.match(indexHtml, /dvChartProductPool/);
  assert.match(appJs, /function applyDVChartProductPool/);
  assert.match(appJs, /整体对比/);
});
