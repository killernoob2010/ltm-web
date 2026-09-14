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

test("Agent conversations use accessible top tabs with isolated state and recoverable deletion", () => {
  assert.match(indexHtml, /id="closingReviewHistory"[^>]*role="tablist"/);
  assert.match(indexHtml, /id="closingReviewMessages"[^>]*role="tabpanel"/);
  assert.match(indexHtml, /id="closingReviewNewBtn"[^>]*aria-label="新建对话"/);
  assert.match(agentJs, /setAttribute\("role", "tab"\)/);
  assert.match(agentJs, /aria-selected/);
  assert.match(agentJs, /aria-labelledby/);
  assert.match(agentJs, /focusTab/);
  assert.match(agentJs, /ArrowRight|ArrowLeft/);
  assert.match(agentJs, /conversationStates: new Map/);
  assert.match(agentJs, /scrollTop/);
  assert.match(agentJs, /itemState\.requestSequence/);
  assert.match(agentJs, /restore/);
  assert.doesNotMatch(agentJs, /确认删除这段对话/);
});

test("Agent keeps the message area wide and lets only the tab rail scroll", () => {
  assert.match(css, /grid-template-rows: auto minmax\(0, 1fr\)/);
  assert.match(css, /closing-review-agent-history[^\{]*\{[^}]*overflow-x: auto/s);
  assert.match(css, /closing-review-agent-message \{[^}]*max-width: 100%/s);
  assert.match(css, /min-width: 0/);
  assert.match(css, /agent-answer-view-table-scroll[^\{]*\{[^}]*overflow-x: auto/s);
});

test("Agent changes asset versions when the conversation shell changes", () => {
  assert.match(indexHtml, /closing_review_agent\.css\?v=agent-answer-research-repair-20260914/);
  assert.match(indexHtml, /closing_review_agent\.js\?v=agent-answer-research-repair-20260914/);
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
