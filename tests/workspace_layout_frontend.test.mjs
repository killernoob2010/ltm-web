import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const html = fs.readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");
const styles = fs.readFileSync(new URL("../frontend/styles.css", import.meta.url), "utf8");

function cssBlock(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return styles.match(new RegExp(`${escaped}\\s*\\{([\\s\\S]*?)\\}`))?.[1] || "";
}

test("desktop app shell keeps the viewport row bounded and gives the sidebar its own scroll", () => {
  const shell = cssBlock(".app-shell");
  const sidebar = cssBlock(".sidebar");
  const workspace = cssBlock(".workspace");
  assert.match(shell, /height:\s*100vh;/);
  assert.match(shell, /height:\s*100dvh;/);
  assert.match(shell, /grid-template-rows:\s*minmax\(0,\s*1fr\);/);
  assert.match(sidebar, /min-height:\s*0;/);
  assert.match(sidebar, /overflow-y:\s*auto;/);
  assert.match(workspace, /min-height:\s*0;/);
  assert.match(workspace, /grid-template-rows:\s*auto\s+auto\s+minmax\(0,\s*1fr\);/);
});

test("workspace reserves a dedicated notice row and a stable content row", () => {
  assert.match(
    html,
    /id="workspaceNoticeRegion"[^>]*>[\s\S]*id="passwordChangeNotice"[\s\S]*<\/[^>]+>/,
  );
  assert.match(styles, /#globalTopbar\s*\{[\s\S]*?grid-row:\s*1;/);
  assert.match(styles, /\.workspace-notice-region\s*\{[\s\S]*?grid-row:\s*2;/);
  assert.match(styles, /\.workspace\s*>\s*\.page\s*\{[\s\S]*?grid-row:\s*3;/);
});

test("empty password errors do not reserve modal space", () => {
  assert.match(styles, /#changePasswordError:empty\s*\{[\s\S]*?display:\s*none;/);
});

test("mobile app shell keeps document flow while desktop shell is bounded", () => {
  const mobile = styles.match(/@media\s*\(max-width:\s*900px\)\s*\{([\s\S]*?)(?=\n\s*@media|\s*$)/)?.[1] || "";
  assert.match(mobile, /\.app-shell\s*\{[\s\S]*?height:\s*auto;/);
  assert.match(mobile, /\.app-shell\s*\{[\s\S]*?min-height:\s*100vh;/);
});

test("floating reminders stay outside the workspace flow", () => {
  assert.match(styles, /\.notification-panel\s*\{[\s\S]*?position:\s*fixed;/);
  assert.match(styles, /\.toast-host\s*\{[\s\S]*?position:\s*fixed;/);
  assert.match(html, /<aside id="notificationPanel"[\s\S]*?<\/aside>\s*\n\s*<div id="toastHost"/);
});
