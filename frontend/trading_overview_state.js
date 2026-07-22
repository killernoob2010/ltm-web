(function(root, factory) {
  const state = factory();
  if (typeof module === "object" && module.exports) module.exports = state;
  if (root) root.TradingOverviewState = state;
})(typeof globalThis !== "undefined" ? globalThis : this, function() {
  function compactDate(value) {
    return String(value || "").replaceAll("-", "");
  }

  function inputDate(value) {
    const text = compactDate(value);
    if (!/^\d{8}$/.test(text)) return "";
    return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`;
  }

  function lastDay(year, month) {
    return new Date(Number(year), Number(month), 0).getDate();
  }

  function defaultDraft(latestDate) {
    const text = compactDate(latestDate);
    if (!/^\d{8}$/.test(text)) {
      return {
        day: "", month: "", year: "", quarter: "1",
        customFrom: "", customTo: "",
      };
    }
    const year = text.slice(0, 4);
    const month = text.slice(4, 6);
    const quarter = String(Math.floor((Number(month) - 1) / 3) + 1);
    return {
      day: inputDate(text),
      month: `${year}-${month}`,
      year,
      quarter,
      customFrom: `${year}-${month}-01`,
      customTo: inputDate(text),
    };
  }

  function periodRange(mode, draft) {
    if (mode === "day") {
      const day = compactDate(draft.day);
      return { startDate: day, endDate: day };
    }
    if (mode === "month") {
      const match = /^(\d{4})-(\d{2})$/.exec(draft.month || "");
      if (!match) return { startDate: "", endDate: "" };
      const [, year, month] = match;
      const end = String(lastDay(year, month)).padStart(2, "0");
      return { startDate: `${year}${month}01`, endDate: `${year}${month}${end}` };
    }
    if (mode === "quarter") {
      const year = String(draft.year || "");
      const quarter = Number(draft.quarter);
      if (!/^\d{4}$/.test(year) || ![1, 2, 3, 4].includes(quarter)) {
        return { startDate: "", endDate: "" };
      }
      const startMonth = (quarter - 1) * 3 + 1;
      const endMonth = startMonth + 2;
      const start = String(startMonth).padStart(2, "0");
      const end = String(endMonth).padStart(2, "0");
      const endDay = String(lastDay(year, endMonth)).padStart(2, "0");
      return { startDate: `${year}${start}01`, endDate: `${year}${end}${endDay}` };
    }
    if (mode === "custom") {
      return {
        startDate: compactDate(draft.customFrom),
        endDate: compactDate(draft.customTo),
      };
    }
    throw new Error("未知时间维度");
  }

  function requestFilters({ accountId, scope, mode, draft }) {
    const range = periodRange(mode, draft);
    return {
      accountId: accountId ? Number(accountId) : null,
      scope: scope || "all",
      ...range,
    };
  }

  function validateFilters(filters) {
    if (!filters.startDate || !filters.endDate) throw new Error("请选择完整日期范围");
    if (filters.startDate > filters.endDate) throw new Error("开始日期不能晚于结束日期");
  }

  function queryString(filters) {
    validateFilters(filters);
    const params = new URLSearchParams();
    if (filters.accountId != null) params.set("account_id", filters.accountId);
    params.set("scope", filters.scope);
    params.set("start_date", filters.startDate);
    params.set("end_date", filters.endDate);
    return params.toString();
  }

  function responseMatches(response, filters) {
    if (!response) return false;
    const responseAccount = response.account_id == null ? null : Number(response.account_id);
    return responseAccount === filters.accountId
      && response.scope === filters.scope
      && response.start_date === filters.startDate
      && response.end_date === filters.endDate;
  }

  function createLoader({ api, onCommit = function() {}, onLoading = function() {}, onError = function() {} }) {
    let version = 0;
    const inFlight = new Map();

    function load(filters) {
      try {
        validateFilters(filters);
      } catch (error) {
        onError(error, filters);
        return Promise.reject(error);
      }
      const key = JSON.stringify(filters);
      const existing = inFlight.get(key);
      if (existing && existing.version === version) return existing.promise;
      const requestVersion = ++version;
      onLoading(true, filters);
      const request = (async function() {
        try {
          const data = await api(`/api/trading-management/overview?${queryString(filters)}`);
          if (requestVersion !== version) return { status: "stale" };
          if (!responseMatches(data.filters, filters)) {
            throw new Error("返回筛选范围与当前选择不一致");
          }
          onCommit(data, filters);
          return { status: "committed", data };
        } catch (error) {
          if (requestVersion === version) onError(error, filters);
          throw error;
        } finally {
          if (inFlight.get(key)?.version === requestVersion) inFlight.delete(key);
          if (requestVersion === version) onLoading(false, filters);
        }
      })();
      inFlight.set(key, { version: requestVersion, promise: request });
      return request;
    }

    return { load };
  }

  return {
    compactDate,
    inputDate,
    defaultDraft,
    periodRange,
    requestFilters,
    queryString,
    responseMatches,
    createLoader,
  };
});
