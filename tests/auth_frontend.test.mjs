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
  assert.match(html, /id="loginStatus"/);
  assert.match(appJs, /setLoginLoading\(true, "正在登录，请稍候\.\.\."\)/);
  assert.match(appJs, /setLoginLoading\(true, "正在以访客身份进入\.\.\."\)/);
  assert.doesNotMatch(appJs, /localStorage\.getItem\("token"\)/);
  assert.doesNotMatch(appJs, /localStorage\.setItem\("token"/);
});
