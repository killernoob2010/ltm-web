import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const source = fs.readFileSync(new URL("../frontend/agent_quality.js", import.meta.url), "utf8");
const html = fs.readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");
const app = fs.readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");
const css = fs.readFileSync(new URL("../frontend/agent_quality.css", import.meta.url), "utf8");

test("admin quality page exposes runtime, trace, and evaluation views", () => {
  assert.match(html, /id="agentQualityPage"/);
  assert.match(html, /agent_quality\.css/);
  assert.match(html, /agent_quality\.js/);
  assert.match(app, /code === "agent_quality"/);
  assert.match(source, /\/api\/admin\/agent-quality\/summary/);
  assert.match(source, /\/api\/admin\/agent-quality\/runs/);
  assert.match(source, /\/api\/admin\/agent-quality\/evaluations/);
  assert.match(source, /real_model_evaluated/);
  assert.match(source, /data-feedback/);
  assert.match(css, /\.agent-quality-page/);
});

test("quality page does not render raw values without escaping and formats timestamps to seconds", () => {
  assert.match(source, /function escapeHtml/);
  assert.match(source, /function formatTimestamp/);
  assert.match(source, /slice\(0, 19\)/);
});
