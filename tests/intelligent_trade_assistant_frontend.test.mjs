import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const html = fs.readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");
const source = fs.readFileSync(new URL("../frontend/closing_review_agent.js", import.meta.url), "utf8");

test("assistant page uses the intelligent trade assistant brand and business wording", () => {
  assert.match(html, /<h1>智能贸易助手<\/h1>/);
  assert.match(html, /aria-label="智能贸易助手对话"/);
  assert.match(html, /aria-label="输入业务分析问题"/);
  assert.match(html, /placeholder="例如：查询本周库存变化，或比较不同港口的基差。"/);
  assert.match(html, /仅分析授权数据；按问题需要使用公开资料，不执行交易或修改业务数据。⌘\/Ctrl \+ Enter 发送。/);
  assert.doesNotMatch(html, /交易持仓助手/);
  assert.match(source, /智能贸易助手对话/);
  assert.match(source, /业务数据查询 · 市场研究 · 只读分析/);
  assert.match(source, /交易复盘兼容模式 · 当前功能范围以可用能力为准/);
});

test("old default titles are aliased only at display time while custom titles stay unchanged", () => {
  const start = source.indexOf("  function displayConversationTitle");
  const end = source.indexOf("\n  function renderHistory", start);
  assert.ok(start >= 0, "displayConversationTitle must be a local presentation helper");
  const context = {
    value: "",
    DEFAULT_CONVERSATION_TITLE: "智能贸易助手对话",
    LEGACY_DEFAULT_CONVERSATION_TITLE: "交易持仓助手对话",
  };
  vm.runInNewContext(`${source.slice(start, end)}\nresult = displayConversationTitle(value);`, context);
  const display = (value) => {
    context.value = value;
    vm.runInNewContext("result = displayConversationTitle(value);", context);
    return context.result;
  };
  assert.equal(display("交易持仓助手对话"), "智能贸易助手对话");
  assert.equal(display("智能贸易助手对话"), "智能贸易助手对话");
  assert.equal(display("我自己命名的复盘"), "我自己命名的复盘");
});
