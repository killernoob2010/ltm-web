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
  assert.match(indexHtml, /closing_review_agent\.js/);
  assert.match(appJs, /const closingReviewAgentPage = document\.querySelector\("#closingReviewAgentPage"\)/);
  assert.match(appJs, /code === "closing_review_agent"/);
  assert.match(appJs, /window\.ClosingReviewAgent\.activate\(\{\s*api,\s*user: state\.user/);
  assert.match(appJs, /closingReviewAgentPage/);
});

test("Agent workspace separates history, automatic results, suggestions and the single composer", () => {
  assert.match(indexHtml, /id="closingReviewHistory"/);
  assert.match(indexHtml, /id="closingReviewMessages"/);
  assert.match(indexHtml, /id="closingReviewSuggestions"/);
  assert.match(indexHtml, /id="closingReviewComposer"/);
  assert.match(indexHtml, /id="closingReviewInput"/);
  assert.match(indexHtml, /id="closingReviewSendBtn"/);
  assert.match(indexHtml, /自动收盘复盘/);
  assert.match(agentJs, /ENDPOINT = "\/api\/closing-review-agent"/);
  assert.match(agentJs, /ENDPOINT\}\/conversations/);
  assert.match(agentJs, /ENDPOINT\}\/suggestions/);
  assert.match(agentJs, /\/messages/);
  assert.match(agentJs, /message_type/);
});

test("Agent renders server content as text and does not create a client-side transcript", () => {
  assert.match(agentJs, /textContent/);
  assert.doesNotMatch(agentJs, /\.innerHTML/);
  assert.doesNotMatch(agentJs, /localStorage/);
  assert.match(agentJs, /crypto\.randomUUID/);
  assert.match(agentJs, /client_request_id/);
});

test("Agent exposes seconds-only timestamps and evidence/status labels", () => {
  assert.match(agentJs, /slice\(0, 19\)/);
  assert.match(agentJs, /数据状态/);
  assert.match(agentJs, /证据/);
  assert.match(agentJs, /最新来源|结果已更新/);
  assert.match(css, /\.closing-review-agent-page/);
  assert.match(css, /\.closing-review-agent-composer/);
  assert.match(css, /\.closing-review-agent-suggestion/);
});
