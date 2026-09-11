import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from 'node:vm';

import renderer from "../frontend/agent_answer_renderer.js";

const source = readFileSync(new URL("../frontend/agent_answer_renderer.js", import.meta.url), "utf8");

test('inventory display is concise without rounding small nonzero values to zero', () => {
  const context = {asText: String, missingReason: () => '缺少数据'};
  vm.runInNewContext(source.slice(source.indexOf('  function displayValue'), source.indexOf('  function appendSummary')), context);
  assert.equal(context.displayValue('1604.06875819991', {}, 'current_value').text, '1,604.07');
  assert.equal(context.displayValue('-0.170058186498', {}, 'delta_pct').text, '-0.17%');
  assert.notEqual(context.displayValue('0.000001', {}, 'value').text, '0.00');
  assert.equal(context.displayValue('missing_period', {}, 'comparison_status').text, '未取到上周基准');
});
const browserFixture = readFileSync(new URL("./agent_answer_renderer_fixture.html", import.meta.url), "utf8");
const tableFixture = readFileSync(new URL("./agent_answer_renderer_table_fixture.html", import.meta.url), "utf8");
const chartFixture = readFileSync(new URL("./agent_answer_renderer_chart_fixture.html", import.meta.url), "utf8");
const datasetFixture = readFileSync(new URL("./agent_answer_renderer_dataset_fixture.html", import.meta.url), "utf8");

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

test("chart fixture covers SVG gaps, zero baseline and table toggle", () => {
  assert.match(source, /createElementNS/);
  assert.match(source, /查看数据表/);
  assert.match(chartFixture, /kind: "line"/);
  assert.match(chartFixture, /kind: "bar"/);
  assert.match(chartFixture, /values: \[null, "12\.50", "-3\.00"\]/);
  assert.match(chartFixture, /agent-answer-view-chart-zero/);
  assert.match(chartFixture, /requests\.length === beforeToggle/);
});

test("dataset fixture covers atlas series, facet paging and matrix accessibility", () => {
  assert.match(source, /chart\.version === 2/);
  assert.match(source, /facetPage/);
  assert.match(source, /matrixColumnPage/);
  assert.match(source, /observation_date/);
  assert.match(source, /agent-answer-dataset-chart/);
  assert.match(source, /agent-answer-matrix/);
  assert.match(source, /aria-label/);
  assert.match(datasetFixture, /PB粉\|2025/);
  assert.match(datasetFixture, /y: null/);
  assert.match(datasetFixture, /source_row_ref/);
});
