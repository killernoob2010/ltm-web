import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const qualitySource = readFileSync(new URL("../frontend/agent_quality.js", import.meta.url), "utf8");
const indexSource = readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");

test("quality page separates execution, validation, delivery and human review", () => {
  assert.match(qualitySource, /quality_dimensions/);
  assert.match(qualitySource, /五维质量状态/);
  assert.match(qualitySource, /新标准核验/);
  assert.match(qualitySource, /not_reviewed/);
  assert.match(qualitySource, /delivery_unknown/);
  assert.match(qualitySource, /succeeded: "执行成功"/);
  assert.doesNotMatch(qualitySource, /succeeded: "已交付"/);
  assert.match(indexSource, /agent_quality\.js\?v=agent-quality-migration-20260915/);
});
