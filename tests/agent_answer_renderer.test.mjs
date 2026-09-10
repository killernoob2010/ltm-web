import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import renderer from "../frontend/agent_answer_renderer.js";

const source = readFileSync(new URL("../frontend/agent_answer_renderer.js", import.meta.url), "utf8");
const browserFixture = readFileSync(new URL("./agent_answer_renderer_fixture.html", import.meta.url), "utf8");
const tableFixture = readFileSync(new URL("./agent_answer_renderer_table_fixture.html", import.meta.url), "utf8");

test("answer renderer exposes the fixed browser API", () => {
  assert.equal(typeof renderer.renderAnswer, "function");
  assert.match(source, /RETURN_DOM_FRAGMENT/);
  assert.match(source, /DOMPurify/);
  assert.match(source, /loadViewPage/);
  assert.match(source, /removeEventListener/);
  assert.match(source, /AbortController/);
  assert.match(source, /ALLOWED_TAGS/);
  assert.match(source, /ALLOWED_ATTR/);
});

test("security acceptance uses a real DOM fixture instead of a fake DOM", () => {
  assert.match(browserFixture, /src="\.\.\/frontend\/agent_answer_renderer\.js"/);
  assert.match(browserFixture, /javascript:alert\(1\)/);
  assert.match(browserFixture, /querySelector\("img,script,iframe,form,object"\)/);
  assert.match(browserFixture, /unexpected page request/);
});

test("renderer source never writes untrusted answer HTML directly", () => {
  assert.doesNotMatch(source, /\.innerHTML\s*=/);
  assert.match(source, /textContent/);
  assert.match(source, /createElementNS|createElement/);
});

test("browser fixture covers trusted view tables, null reasons and page requests", () => {
  assert.match(tableFixture, /\{\{view:v1\}\}/);
  assert.match(tableFixture, /missing_quote/);
  assert.match(tableFixture, /messageId: 17/);
  assert.match(tableFixture, /pageSize/);
});
