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
  assert.match(appJs, /currentUser\.textContent = isGuest\(\) \? "访客"/);
  assert.doesNotMatch(appJs, /localStorage\.getItem\("token"\)/);
  assert.doesNotMatch(appJs, /localStorage\.setItem\("token"/);
});

test("user management exposes account lifecycle permission levels and password self-service", () => {
  assert.match(html, /src="\/static\/app\.js\?v=order-finance-wps-sync-20260715"/);
  for (const id of [
    "resetUserPasswordBtn", "toggleUserStatusBtn", "changePasswordBtn",
    "passwordChangeNotice", "changePasswordDialog", "currentPassword",
    "newPassword", "confirmNewPassword", "userUsername", "permissionSummary",
    "previewUserBtn", "userPermissionEditor",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  for (const department of ["贸易处", "期货组", "财企处", "资金处", "管理部门", "公司领导"]) {
    assert.match(html, new RegExp(`<option value="${department}">${department}</option>`));
  }
  assert.match(html, /<option value="领导">领导<\/option>/);
  assert.doesNotMatch(html, /id="userPassword"/);
  assert.match(appJs, /\/api\/users\/preview/);
  assert.match(appJs, /\/api\/auth\/change-password/);
  assert.match(appJs, /\/reset-password/);
  assert.match(appJs, /\/status/);
  assert.match(appJs, /password_change_recommended/);
  assert.match(appJs, /temporary_password/);
  assert.match(appJs, /permission-level/);
  assert.match(appJs, /renderUserPermissionEditor/);
  assert.match(
    appJs,
    /\[\s*"#addShJunnengBtn", "#editShJunnengBtn", "#closeShJunnengBtn",\s*"#refreshShJunnengPricesBtn", "#manualShJunnengPricesBtn",\s*\]\.forEach\(\(selector\) => setHidden\(selector, guest \|\| !canModuleEdit\("sh_junneng"\)\)\)/,
  );
  assert.match(appJs, /setHidden\("#importCacheBtn", guest \|\| !canModuleSensitive\("info_summary"\)\)/);
  assert.match(appJs, /setHidden\("#batchDeleteAlertsBtn", guest \|\| !canModuleSensitive\("risk_alert"\)\)/);
});

test("view-only mid-event users only receive view actions", () => {
  assert.match(
    appJs,
    /\["#addGroupBtn", "#addPositionBtn", "#refreshPricesBtn"\]\.forEach\(\(selector\) =>\s*setHidden\(selector, guest \|\| !canModuleEdit\("mid_event_monitor"\)\)\)/,
  );
  assert.match(appJs, /const canEditMidEvent = canModuleEdit\("mid_event_monitor"\);/);
  assert.match(appJs, /const canSensitiveMidEvent = canModuleSensitive\("mid_event_monitor"\);/);
  assert.match(appJs, /canEditMidEvent \? `<button class="link" data-action="edit"/);
  assert.match(appJs, /canSensitiveMidEvent \? `<button class="link" data-action="delete"/);
  assert.match(appJs, /if \(canModuleEdit\("mid_event_monitor"\)\) startMidEventAutoRefresh\(\);/);
});

test("view-only risk-alert users do not receive mutation actions", () => {
  assert.match(
    appJs,
    /\["#addAlertBtn", "#scanAlertsBtn", "#batchEnableAlertsBtn", "#batchDisableAlertsBtn"\][\s\S]*?canModuleEdit\("risk_alert"\)/,
  );
  assert.match(appJs, /setHidden\("#batchDeleteAlertsBtn", guest \|\| !canModuleSensitive\("risk_alert"\)\)/);
  assert.match(appJs, /const canEditRiskAlert = canModuleEdit\("risk_alert"\);/);
  assert.match(appJs, /const canSensitiveRiskAlert = canModuleSensitive\("risk_alert"\);/);
  assert.match(appJs, /canEditRiskAlert \? `<button class="link" data-action="edit"/);
  assert.match(appJs, /canEditRiskAlert \? `<button class="link" data-action="toggle"/);
  assert.match(appJs, /canEditRiskAlert \? `<button class="link" data-action="simulate"/);
  assert.match(appJs, /canSensitiveRiskAlert \? `<button class="link" data-action="delete"/);
  assert.match(appJs, /setHidden\("#markAllNotificationsBtn", guest \|\| !canModuleEdit\("risk_alert"\)\)/);
  assert.match(appJs, /const canAcknowledgeRiskAlerts = canModuleEdit\("risk_alert"\);/);
  assert.match(appJs, /canAcknowledgeRiskAlerts \? "button" : "div"/);
  assert.match(appJs, /item\.status === "unread" && canEditRiskAlert/);
});

test("data-visualization import and export controls follow sensitive permission", () => {
  assert.match(html, /id="dvIntegrationImportLabel"/);
  assert.match(
    appJs,
    /\["#dvIntegrationImportLabel", "#dvExportBtn"\][\s\S]*?canModuleSensitive\("data_visualization_integration"\)/,
  );
  assert.match(
    appJs,
    /\["#dvImportBtn", "#dvCommitImportBtn"\][\s\S]*?canModuleSensitive\("data_visualization_data"\)/,
  );
});
