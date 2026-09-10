(function(root, factory) {
  "use strict";
  if (typeof module === "object" && module.exports) {
    const api = factory();
    module.exports = api;
    // Keep named CommonJS properties discoverable to Node's ESM bridge.
    module.exports.reduceProgress = api.reduceProgress;
    module.exports.formatBeijingSeconds = api.formatBeijingSeconds;
  } else {
    root.AgentProgress = factory();
  }
})(typeof self !== "undefined" ? self : this, function() {
  "use strict";

  function copyProgress(value) {
    if (!value || typeof value !== "object") return null;
    const result = { ...value };
    if (Array.isArray(value.history)) result.history = value.history.map((item) => ({ ...item }));
    return result;
  }

  function reduceProgress(current, incoming) {
    const next = copyProgress(incoming);
    if (!next || !Number.isFinite(Number(next.sequence))) return current || null;
    next.sequence = Number(next.sequence);
    next.terminal = Boolean(next.terminal || next.stage === "finished");
    if (!current) return next;
    const previous = copyProgress(current);
    if (previous.terminal && !next.terminal) return previous;
    if (previous.terminal && next.stage !== "finished") return previous;
    if (next.sequence < Number(previous.sequence)) return previous;
    return next.sequence === Number(previous.sequence)
      ? { ...previous, ...next, terminal: Boolean(previous.terminal || next.terminal) }
      : next;
  }

  function formatBeijingSeconds(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    const parts = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    }).formatToParts(date);
    const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second}`;
  }

  return { reduceProgress, formatBeijingSeconds };
});
