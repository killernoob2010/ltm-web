import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const html = fs.readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");
const appJs = fs.readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");

test("login page exposes real guest login without prefilled admin credentials", () => {
  assert.match(html, /以访客身份访问/);
  assert.doesNotMatch(html, /id="username"[^>]*value=/);
  assert.doesNotMatch(html, /id="password"[^>]*value=/);
  assert.match(appJs, /\/api\/auth\/guest-login/);
});
