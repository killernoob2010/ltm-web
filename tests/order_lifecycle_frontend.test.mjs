import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const appJs = readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");
const indexHtml = readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");

test("订单全流程测试版页面使用独立模块和分页 API", () => {
  assert.match(indexHtml, /id="orderLifecyclePage"/);
  assert.match(indexHtml, /id="orderLifecyclePageSize"/);
  assert.match(appJs, /code === "order_lifecycle_progress"/);
  assert.match(appJs, /api\/order-lifecycle\/progress/);
  assert.match(appJs, /page_size: String\(state\.orderLifecyclePageSize\)/);
  assert.match(appJs, /order-lifecycle-node-btn/);
  assert.match(appJs, /api\/order-lifecycle\/businesses/);
});
