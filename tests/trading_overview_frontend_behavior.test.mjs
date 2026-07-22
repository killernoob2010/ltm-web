import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const overviewState = require("../frontend/trading_overview_state.js");


test("period ranges use real day month quarter and custom calendar boundaries", () => {
  assert.deepEqual(
    overviewState.periodRange("day", { day: "2026-07-21" }),
    { startDate: "20260721", endDate: "20260721" },
  );
  assert.deepEqual(
    overviewState.periodRange("month", { month: "2026-02" }),
    { startDate: "20260201", endDate: "20260228" },
  );
  assert.deepEqual(
    overviewState.periodRange("quarter", { year: "2026", quarter: "3" }),
    { startDate: "20260701", endDate: "20260930" },
  );
  assert.deepEqual(
    overviewState.periodRange("custom", {
      customFrom: "2026-07-03",
      customTo: "2026-07-19",
    }),
    { startDate: "20260703", endDate: "20260719" },
  );
});


test("default overview state selects the latest fact month", () => {
  assert.deepEqual(overviewState.defaultDraft("20260721"), {
    day: "2026-07-21",
    month: "2026-07",
    year: "2026",
    quarter: "3",
    customFrom: "2026-07-01",
    customTo: "2026-07-21",
  });
});


test("request filters combine account scope and the selected period", () => {
  const filters = overviewState.requestFilters({
    accountId: "12",
    scope: "basic_hedging",
    mode: "month",
    draft: overviewState.defaultDraft("20260721"),
  });

  assert.deepEqual(filters, {
    accountId: 12,
    scope: "basic_hedging",
    startDate: "20260701",
    endDate: "20260731",
  });
  assert.equal(
    overviewState.queryString(filters),
    "account_id=12&scope=basic_hedging&start_date=20260701&end_date=20260731",
  );
});


test("reversed custom dates are rejected before an API call", async () => {
  let calls = 0;
  let message = "";
  const loader = overviewState.createLoader({
    api: async () => {
      calls += 1;
      return {};
    },
    onError: (error) => { message = error.message; },
  });

  await assert.rejects(
    loader.load({
      accountId: null,
      scope: "all",
      startDate: "20260731",
      endDate: "20260701",
    }),
    /开始日期不能晚于结束日期/,
  );
  assert.equal(calls, 0);
  assert.equal(message, "开始日期不能晚于结束日期");
});


test("only the latest response can commit rendered overview state", async () => {
  const pending = [];
  const commits = [];
  const loader = overviewState.createLoader({
    api: (url) => new Promise((resolve) => pending.push({ url, resolve })),
    onCommit: (data, filters) => commits.push({ data, filters }),
  });
  const allFilters = {
    accountId: null,
    scope: "all",
    startDate: "20260701",
    endDate: "20260731",
  };
  const businessFilters = { ...allFilters, scope: "basic_hedging" };

  const first = loader.load(allFilters);
  const second = loader.load(businessFilters);
  pending[1].resolve({
    filters: {
      account_id: null,
      scope: "basic_hedging",
      start_date: "20260701",
      end_date: "20260731",
    },
    pnl: { value: 300 },
  });
  await second;
  pending[0].resolve({
    filters: {
      account_id: null,
      scope: "all",
      start_date: "20260701",
      end_date: "20260731",
    },
    pnl: { value: 400 },
  });
  await first;

  assert.equal(commits.length, 1);
  assert.equal(commits[0].filters.scope, "basic_hedging");
  assert.equal(commits[0].data.pnl.value, 300);
});


test("returning to an older in-flight filter starts a new latest request", async () => {
  const pending = [];
  const commits = [];
  const loader = overviewState.createLoader({
    api: (url) => new Promise((resolve) => pending.push({ url, resolve })),
    onCommit: (data, filters) => commits.push(filters.scope),
  });
  const allFilters = {
    accountId: null,
    scope: "all",
    startDate: "20260701",
    endDate: "20260731",
  };
  const businessFilters = { ...allFilters, scope: "basic_hedging" };
  const response = (scope) => ({
    filters: {
      account_id: null,
      scope,
      start_date: "20260701",
      end_date: "20260731",
    },
  });

  const firstAll = loader.load(allFilters);
  const business = loader.load(businessFilters);
  const latestAll = loader.load(allFilters);

  assert.equal(pending.length, 3);
  pending[1].resolve(response("basic_hedging"));
  pending[0].resolve(response("all"));
  pending[2].resolve(response("all"));
  await Promise.all([firstAll, business, latestAll]);
  assert.deepEqual(commits, ["all"]);
});


test("failed refresh reports an error without clearing the last committed result", async () => {
  const commits = [];
  const errors = [];
  let shouldFail = false;
  const filters = {
    accountId: null,
    scope: "all",
    startDate: "20260701",
    endDate: "20260731",
  };
  const loader = overviewState.createLoader({
    api: async () => {
      if (shouldFail) throw new Error("network down");
      return {
        filters: {
          account_id: null,
          scope: "all",
          start_date: "20260701",
          end_date: "20260731",
        },
        pnl: { value: 400 },
      };
    },
    onCommit: (data) => commits.push(data),
    onError: (error) => errors.push(error.message),
  });

  await loader.load(filters);
  shouldFail = true;
  await assert.rejects(loader.load(filters), /network down/);

  assert.equal(commits.length, 1);
  assert.equal(commits[0].pnl.value, 400);
  assert.deepEqual(errors, ["network down"]);
});
