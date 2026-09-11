import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const source = fs.readFileSync(new URL("../frontend/closing_review_agent.js", import.meta.url), "utf8");
const progressSource = fs.readFileSync(new URL("../frontend/agent_progress.js", import.meta.url), "utf8");

test("agent page probes V2 capabilities and preserves V1 fallback", () => {
  assert.match(source, /const ENDPOINT = "\/api\/closing-review-agent"/);
  assert.match(source, /const V2_ENDPOINT = "\/api\/trading-agent-v2"/);
  assert.match(source, /state\.v2 = Boolean\(capabilities && capabilities\.enabled\)/);
  assert.match(source, /waitForTask/);
  assert.match(source, /facet_page_size/);
  assert.match(source, /matrix_column_page/);
});

test("conversation history distinguishes active tasks from reusable conversations", () => {
  assert.match(source, /function historyStatusLabel/);
  const start = source.indexOf("  function historyStatusLabel");
  const end = source.indexOf("  function renderHistory", start);
  const historyStatusFunction = source.slice(start, end);
  const run = (state, conversation) => {
    const context = { state, result: null };
    vm.runInNewContext(`${historyStatusFunction}\nresult = historyStatusLabel(${JSON.stringify(conversation)});`, context);
    return context.result;
  };
  assert.equal(run({conversationId: 7, activeTask: {state: "running"}}, {id: 7, status: "active"}), "处理中");
  assert.equal(run({conversationId: 7, activeTask: {state: "failed"}}, {id: 7, status: "active"}), "可继续");
  assert.equal(run({conversationId: 7, activeTask: null}, {id: 7, status: "active"}), "可继续");
  assert.equal(run({conversationId: 7, activeTask: null}, {id: 7, status: "archived"}), "已归档");
});

const { default: vm } = await import('node:vm');
const pollingFunction = source.slice(source.indexOf('  async function waitForTask'), source.indexOf('  async function submitMessage'));

function pollingHarness(api) {
  let now = 0;
  const context = {
    state: { activation: 1, requestSequence: 1, conversationId: 7, api: (url) => api(url, now) },
    endpoint: () => '/api/trading-agent-v2',
    setStatus: () => {},
    Date: { now: () => now },
    setTimeout: (resolve, delay) => { now += delay; resolve(); },
  };
  const guard = source.slice(source.indexOf('  function requestIsCurrent'), source.indexOf('  function setStatus'));
  vm.runInNewContext(guard + pollingFunction, context);
  return context.waitForTask;
}

test('polling includes queue time and returns the server terminal state', async () => {
  const wait = pollingHarness(async (url, elapsed) => ({
    state: elapsed < 120000 ? 'queued' : elapsed < 160000 ? 'running' : 'succeeded',
    poll_timeout_seconds: 225,
  }));
  assert.equal((await wait(42, 1)).state, 'succeeded');
});

test('transient status-read failure retries the same task without resubmitting', async () => {
  let reads = 0;
  const wait = pollingHarness(async (url) => {
    assert.equal(url, '/api/trading-agent-v2/tasks/42');
    if (++reads < 3) throw new Error('temporary network failure');
    return { state: 'succeeded' };
  });
  assert.equal((await wait(42, 1)).state, 'succeeded');
  assert.equal(reads, 3);
});

test('repeated status-read failure stops monitoring after three attempts', async () => {
  let reads = 0;
  const wait = pollingHarness(async () => { reads++; throw new Error('unavailable'); });
  await assert.rejects(wait(42, 1), /unavailable/);
  assert.equal(reads, 3);
});

test('progress reducer keeps terminal state and formats Beijing seconds', async () => {
  const progressModule = await import(new URL('../frontend/agent_progress.js', import.meta.url));
  const done = { sequence: 9, stage: 'finished', terminal: true, elapsed_seconds: 12 };
  assert.deepEqual(progressModule.reduceProgress(done, { sequence: 8, stage: 'search', terminal: false }), done);
  assert.equal(progressModule.formatBeijingSeconds('2026-09-10T18:01:02.999+00:00'), '2026-09-11 02:01:02');
  assert.match(progressSource, /Asia\/Shanghai/);
});
