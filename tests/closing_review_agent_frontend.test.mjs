import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const agentJs = readFileSync(new URL("../frontend/closing_review_agent.js", import.meta.url), "utf8");
const appJs = readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");
const indexHtml = readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");
const css = readFileSync(new URL("../frontend/closing_review_agent.css", import.meta.url), "utf8");

test("closing review Agent has one guarded workspace entry point", () => {
  assert.match(indexHtml, /id="closingReviewAgentPage" class="page hidden closing-review-agent-page"/);
  assert.match(indexHtml, /closing_review_agent\.css/);
  assert.match(indexHtml, /vendor\/agent\/marked\.umd\.js/);
  assert.match(indexHtml, /vendor\/agent\/purify\.min\.js/);
  assert.match(indexHtml, /agent_answer_renderer\.js/);
  assert.match(indexHtml, /closing_review_agent\.js\?v=[^"\s]+/);
  assert.match(indexHtml, /closing_review_agent\.js/);
  assert.match(appJs, /const closingReviewAgentPage = document\.querySelector\("#closingReviewAgentPage"\)/);
  assert.match(appJs, /code === "closing_review_agent"/);
  assert.match(appJs, /window\.ClosingReviewAgent\.activate\(\{\s*api,\s*user: state\.user/);
  assert.match(appJs, /closingReviewAgentPage/);
});

test("Agent workspace separates history, results and the single composer without suggestions", () => {
  assert.match(indexHtml, /id="closingReviewHistory"/);
  assert.match(indexHtml, /id="closingReviewMessages"/);
  assert.doesNotMatch(indexHtml, /closingReviewSuggestions/);
  assert.match(indexHtml, /id="closingReviewComposer"/);
  assert.match(indexHtml, /id="closingReviewInput"/);
  assert.match(indexHtml, /id="closingReviewSendBtn"/);
  assert.match(indexHtml, /自动持仓结果/);
  assert.match(agentJs, /ENDPOINT = "\/api\/closing-review-agent"/);
  assert.match(agentJs, /ENDPOINT\}\/conversations/);
  assert.doesNotMatch(agentJs, /suggestion/i);
  assert.match(agentJs, /\/messages/);
  assert.match(agentJs, /message_type/);
  assert.match(agentJs, /重试原问题/);
  assert.match(agentJs, /supersedes_message_id/);
});

test("workspace is presented as the Intelligent Trade Assistant", () => {
  assert.match(indexHtml, /<h1>智能贸易助手<\/h1>/);
  assert.match(indexHtml, /业务数据查询 · 市场研究 · 只读分析/);
  assert.match(agentJs, /业务数据查询 · 市场研究 · 只读分析/);
  assert.match(agentJs, /交易复盘兼容模式 · 当前功能范围以可用能力为准/);
  assert.match(agentJs, /智能贸易助手/);
  assert.doesNotMatch(indexHtml, /<h1>期权收盘复盘 Agent<\/h1>/);
});

test("Agent renders server content as text and does not create a client-side transcript", () => {
  assert.match(agentJs, /textContent/);
  assert.doesNotMatch(agentJs, /\.innerHTML/);
  assert.match(agentJs, /AgentAnswerRenderer\.renderAnswer/);
  assert.match(agentJs, /messageId/);
  assert.doesNotMatch(agentJs, /localStorage/);
  assert.match(agentJs, /crypto\.randomUUID/);
  assert.match(agentJs, /client_request_id/);
});

test("Agent exposes seconds-only timestamps and evidence/status labels", () => {
  assert.match(agentJs, /slice\(0, 19\)/);
  assert.match(agentJs, /statusLabel\(dataStatus\)/);
  assert.match(agentJs, /evidenceBlock\(projection\)/);
  assert.match(agentJs, /最新来源|结果已更新/);
  assert.match(css, /\.closing-review-agent-page/);
  assert.match(css, /\.closing-review-agent-composer/);
  assert.match(css, /\.agent-answer-view-table/);
  assert.match(css, /overflow-x: auto/);
  assert.doesNotMatch(css, /\.closing-review-agent-suggestion/);
});
