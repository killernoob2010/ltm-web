import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

const source = fs.readFileSync(new URL("../frontend/closing_review_agent.js", import.meta.url), "utf8");

test("agent page probes V2 capabilities and preserves V1 fallback", () => {
  assert.match(source, /const ENDPOINT = "\/api\/closing-review-agent"/);
  assert.match(source, /const V2_ENDPOINT = "\/api\/trading-agent-v2"/);
  assert.match(source, /state\.v2 = Boolean\(capabilities && capabilities\.enabled\)/);
  assert.match(source, /waitForTask/);
});
