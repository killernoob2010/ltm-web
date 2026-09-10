(function(root, factory) {
  "use strict";
  if (typeof module === "object" && module.exports) {
    let markedApi = null;
    let purifyApi = null;
    try { markedApi = require("./vendor/agent/marked.umd.js"); } catch (error) { markedApi = null; }
    try { purifyApi = require("./vendor/agent/purify.min.js"); } catch (error) { purifyApi = null; }
    if (typeof purifyApi === "function" && root && root.document) purifyApi = purifyApi(root);
    const api = factory(markedApi, purifyApi, root);
    module.exports = api;
    module.exports.renderAnswer = api.renderAnswer;
  } else {
    root.AgentAnswerRenderer = factory(root.marked, root.DOMPurify, root);
  }
}(typeof globalThis !== "undefined" ? globalThis : this, function(markedApi, purifyApi, root) {
  "use strict";

  const ALLOWED_TAGS = [
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote",
    "pre", "code", "strong", "em", "del", "hr", "br", "table", "thead", "tbody",
    "tr", "th", "td", "a",
  ];
  const ALLOWED_ATTR = ["href", "title", "colspan", "rowspan"];
  const FORBID_TAGS = [
    "img", "iframe", "script", "style", "svg", "math", "form", "object", "embed",
    "video", "audio", "source", "canvas", "input", "button", "textarea", "select",
  ];
  const FORBID_ATTR = [
    "id", "name", "style", "class", "data-*", "onerror", "onclick", "onload", "target",
  ];
  const VIEW_MARKER = /\{\{view:(v[1-8])\}\}/g;
  const PAGE_SIZES = [20, 50, 100];
  const MISSING_LABELS = {
    missing_quote: "缺少最新成交价",
    quote_missing: "缺少最新成交价",
    unavailable: "数据不可用",
    not_available: "数据不可用",
    invalid: "数据无效",
    stale: "行情已过期",
    permission_denied: "无权读取数据",
  };

  function documentFor(container) {
    return container && container.ownerDocument ? container.ownerDocument : root && root.document;
  }

  function escapeRegExp(value) {
    return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function asText(value) {
    if (value == null) return "";
    if (typeof value === "string") return value;
    if (typeof value === "object") {
      try { return JSON.stringify(value); } catch (error) { return String(value); }
    }
    return String(value);
  }

  function appendText(doc, parent, tag, value, className) {
    const node = doc.createElement(tag);
    if (className) node.className = className;
    node.textContent = asText(value);
    parent.appendChild(node);
    return node;
  }

  function report(onError, error, context) {
    if (typeof onError !== "function") return;
    try { onError(error instanceof Error ? error : new Error(asText(error)), context); } catch (ignored) {}
  }

  function markedParser() {
    if (markedApi && typeof markedApi.parse === "function") return markedApi;
    if (markedApi && markedApi.marked && typeof markedApi.marked.parse === "function") return markedApi.marked;
    if (typeof markedApi === "function") return markedApi;
    return null;
  }

  function sanitizer() {
    if (purifyApi && typeof purifyApi.sanitize === "function") return purifyApi;
    if (purifyApi && purifyApi.default && typeof purifyApi.default.sanitize === "function") return purifyApi.default;
    return null;
  }

  function canonicalUrl(value, doc) {
    if (!value) return null;
    try {
      const url = new URL(String(value), doc.baseURI);
      if (url.protocol !== "http:" && url.protocol !== "https:") return null;
      if (url.username || url.password) return null;
      return url.href;
    } catch (error) {
      return null;
    }
  }

  function sourceCatalog(payload, doc) {
    const urls = new Set();
    const evidence = payload && Array.isArray(payload.evidence) ? payload.evidence : [];
    evidence.forEach((item) => {
      const url = item && canonicalUrl(item.url, doc);
      if (url) urls.add(url);
    });
    return urls;
  }

  function filterLinks(fragment, payload, doc) {
    const catalog = sourceCatalog(payload, doc);
    fragment.querySelectorAll("a").forEach((link) => {
      const href = canonicalUrl(link.getAttribute("href"), doc);
      if (!href || !catalog.has(href)) {
        link.removeAttribute("href");
        link.removeAttribute("title");
        return;
      }
      link.setAttribute("href", href);
      link.setAttribute("rel", "noopener noreferrer");
    });
  }

  function viewMap(payload) {
    const result = new Map();
    const views = payload && Array.isArray(payload.views) ? payload.views : [];
    views.forEach((view) => {
      if (view && /^v[1-8]$/.test(String(view.id)) && !result.has(view.id)) result.set(view.id, view);
    });
    return result;
  }

  function replaceMarkersWithSentinels(markdown, views) {
    const markers = [];
    let sequence = 0;
    const replaced = String(markdown || "").replace(VIEW_MARKER, (whole, viewId) => {
      if (!views.has(viewId)) return whole;
      const sentinel = `\uE000agent-view-${sequence++}\uE001`;
      markers.push({ sentinel, viewId });
      return sentinel;
    });
    return { markdown: replaced, markers };
  }

  function markerParentIsLiteral(node) {
    let current = node.parentElement;
    while (current) {
      if (["CODE", "PRE", "A"].includes(current.tagName)) return true;
      current = current.parentElement;
    }
    return false;
  }

  function replaceSentinels(fragment, markers, doc, createHost) {
    if (!markers.length) return [];
    const bySentinel = new Map(markers.map((item) => [item.sentinel, item]));
    const pattern = new RegExp(markers.map((item) => escapeRegExp(item.sentinel)).join("|"), "g");
    const nodeFilter = (doc.defaultView && doc.defaultView.NodeFilter) || root.NodeFilter || { SHOW_TEXT: 4 };
    const walker = doc.createTreeWalker(fragment, nodeFilter.SHOW_TEXT);
    const textNodes = [];
    let node = walker.nextNode();
    while (node) {
      textNodes.push(node);
      node = walker.nextNode();
    }
    const mounted = [];
    textNodes.forEach((textNode) => {
      if (!textNode.parentNode || markerParentIsLiteral(textNode)) return;
      pattern.lastIndex = 0;
      if (!pattern.test(textNode.nodeValue || "")) return;
      pattern.lastIndex = 0;
      const parent = textNode.parentNode;
      const exactMarker = pattern.exec(textNode.nodeValue || "");
      const blockParent = parent.nodeType === 1 && [
        "P", "DIV", "LI", "BLOCKQUOTE", "H1", "H2", "H3", "H4", "H5", "H6",
      ].includes(parent.tagName);
      if (exactMarker && exactMarker[0] === textNode.nodeValue && parent.childNodes.length === 1 && blockParent) {
        const marker = bySentinel.get(exactMarker[0]);
        const host = marker ? createHost(marker.viewId) : null;
        if (host) {
          parent.replaceWith(host);
          mounted.push({ viewId: marker.viewId, host });
          return;
        }
      }
      pattern.lastIndex = 0;
      const replacement = doc.createDocumentFragment();
      let last = 0;
      let match = pattern.exec(textNode.nodeValue || "");
      while (match) {
        if (match.index > last) replacement.appendChild(doc.createTextNode(textNode.nodeValue.slice(last, match.index)));
        const marker = bySentinel.get(match[0]);
        const host = marker ? createHost(marker.viewId) : null;
        if (host) {
          replacement.appendChild(host);
          mounted.push({ viewId: marker.viewId, host });
        } else {
          replacement.appendChild(doc.createTextNode(match[0]));
        }
        last = match.index + match[0].length;
        match = pattern.exec(textNode.nodeValue || "");
      }
      if (last < textNode.nodeValue.length) replacement.appendChild(doc.createTextNode(textNode.nodeValue.slice(last)));
      textNode.replaceWith(replacement);
    });
    return mounted;
  }

  function viewTitle(view) {
    return view && view.title ? view.title : "已核验数据";
  }

  function missingReason(row, key) {
    const candidate = row && (row[`${key}_status`] || row.data_status || row.valuation_status);
    return MISSING_LABELS[candidate] || (candidate ? asText(candidate) : "缺少数据");
  }

  function displayValue(value, row, key) {
    if (value === null || value === undefined) return { text: `—（${missingReason(row, key)}）`, missing: true };
    return { text: asText(value), missing: false };
  }

  function appendSummary(doc, parent, pageData) {
    const summary = pageData && pageData.summary && typeof pageData.summary === "object" ? pageData.summary : {};
    const coverage = pageData && pageData.coverage && typeof pageData.coverage === "object" ? pageData.coverage : {};
    const keys = Object.keys(summary);
    if (!keys.length && !Object.keys(coverage).length) return;
    const wrapper = doc.createElement("div");
    wrapper.className = "agent-answer-view-summary";
    appendText(doc, wrapper, "strong", "全量汇总", "agent-answer-view-summary-title");
    const list = doc.createElement("dl");
    keys.forEach((key) => {
      const term = appendText(doc, list, "dt", key);
      const definition = appendText(doc, list, "dd", displayValue(summary[key], summary, key).text);
      if (summary[key] === null || summary[key] === undefined) definition.title = `数据缺失：${missingReason(summary, key)}`;
      term.title = key;
    });
    Object.keys(coverage).forEach((key) => {
      const labels = {
        eligible_rows: "纳入行数", covered_rows: "已覆盖行数", eligible_quantity: "纳入手数",
        covered_quantity: "已覆盖手数", eligible_contracts: "纳入合约数", covered_contracts: "已覆盖合约数",
      };
      appendText(doc, list, "dt", labels[key] || key);
      appendText(doc, list, "dd", displayValue(coverage[key], coverage, key).text);
    });
    wrapper.appendChild(list);
    parent.appendChild(wrapper);
  }

  function appendWarnings(doc, parent, warnings) {
    if (!Array.isArray(warnings) || !warnings.length) return;
    const list = doc.createElement("ul");
    list.className = "agent-answer-view-warnings";
    warnings.slice(0, 20).forEach((warning) => appendText(doc, list, "li", warning));
    parent.appendChild(list);
  }

  function appendTable(doc, parent, pageData) {
    const columns = Array.isArray(pageData && pageData.columns) ? pageData.columns : [];
    const rows = Array.isArray(pageData && pageData.rows) ? pageData.rows : [];
    if (!columns.length) {
      appendText(doc, parent, "p", rows.length ? "当前数据缺少可展示列。" : "当前结果没有可展示行。", "agent-answer-view-empty");
      return;
    }
    const scroll = doc.createElement("div");
    scroll.className = "agent-answer-view-table-scroll";
    const table = doc.createElement("table");
    table.className = "agent-answer-view-table";
    const head = doc.createElement("thead");
    const headRow = doc.createElement("tr");
    columns.forEach((column) => {
      const cell = doc.createElement("th");
      cell.scope = "col";
      const label = column && (column.label || column.key) ? (column.label || column.key) : "字段";
      cell.textContent = `${asText(label)}${column && column.unit ? `（${asText(column.unit)}）` : ""}`;
      headRow.appendChild(cell);
    });
    head.appendChild(headRow);
    table.appendChild(head);
    const body = doc.createElement("tbody");
    rows.forEach((row) => {
      const tr = doc.createElement("tr");
      columns.forEach((column) => {
        const key = column && column.key ? String(column.key) : "";
        const cell = doc.createElement("td");
        const value = displayValue(row && row[key], row, key);
        cell.textContent = value.text;
        if (value.missing) cell.title = `数据缺失：${missingReason(row, key)}`;
        tr.appendChild(cell);
      });
      body.appendChild(tr);
    });
    table.appendChild(body);
    scroll.appendChild(table);
    parent.appendChild(scroll);
    if (!rows.length) appendText(doc, parent, "p", "当前结果没有可展示行。", "agent-answer-view-empty");
  }

  function appendPagination(doc, parent, pageData, loadPage, alive, listen) {
    const pagination = pageData && pageData.pagination && typeof pageData.pagination === "object"
      ? pageData.pagination : {};
    const page = Number.isInteger(pagination.page) && pagination.page > 0 ? pagination.page : 1;
    const pageSize = PAGE_SIZES.includes(Number(pagination.page_size)) ? Number(pagination.page_size) : 20;
    const totalRows = Number.isInteger(pagination.total_rows) && pagination.total_rows >= 0 ? pagination.total_rows : 0;
    const totalPages = Number.isInteger(pagination.total_pages) && pagination.total_pages > 0 ? pagination.total_pages : 1;
    const controls = doc.createElement("div");
    controls.className = "agent-answer-view-pagination";
    const summary = appendText(doc, controls, "span", `共 ${totalRows} 行 · 第 ${page} / ${totalPages} 页`, "agent-answer-view-pagination-summary");
    summary.setAttribute("aria-live", "polite");
    const previous = doc.createElement("button");
    previous.type = "button";
    previous.textContent = "上一页";
    previous.disabled = page <= 1;
    listen(previous, "click", () => { if (alive()) loadPage(page - 1, pageSize); });
    controls.appendChild(previous);
    const next = doc.createElement("button");
    next.type = "button";
    next.textContent = "下一页";
    next.disabled = page >= totalPages;
    listen(next, "click", () => { if (alive()) loadPage(page + 1, pageSize); });
    controls.appendChild(next);
    const label = appendText(doc, controls, "label", "每页", "agent-answer-view-page-size-label");
    const select = doc.createElement("select");
    select.setAttribute("aria-label", "每页行数");
    PAGE_SIZES.forEach((size) => {
      const option = doc.createElement("option");
      option.value = String(size);
      option.textContent = String(size);
      option.selected = size === pageSize;
      select.appendChild(option);
    });
    listen(select, "change", () => {
      if (alive()) loadPage(1, Number(select.value));
    });
    label.appendChild(select);
    parent.appendChild(controls);
  }

  function createViewHost(doc, view) {
    const section = doc.createElement("section");
    section.className = "agent-answer-view";
    section.setAttribute("aria-label", viewTitle(view));
    const heading = appendText(doc, section, "h3", viewTitle(view), "agent-answer-view-title");
    heading.dataset.viewId = view.id;
    const body = doc.createElement("div");
    body.className = "agent-answer-view-body";
    section.appendChild(body);
    return { section, body };
  }

  function renderView(host, view, payload, options, runtime) {
    const doc = host.section.ownerDocument;
    let destroyed = false;
    let requestSequence = 0;
    let currentController = null;
    const listeners = [];
    const loader = typeof options.loadViewPage === "function" ? options.loadViewPage : null;
    const messageId = options.messageId != null ? options.messageId : payload.message_id;
    const alive = () => !destroyed && runtime.alive();
    const listen = (element, event, listener) => {
      element.addEventListener(event, listener);
      listeners.push({ element, event, listener });
    };
    const loadPage = async (page, pageSize) => {
      const sequence = ++requestSequence;
      if (currentController) currentController.abort();
      currentController = typeof AbortController === "function" ? new AbortController() : null;
      host.body.replaceChildren();
      appendText(doc, host.body, "p", `正在读取第 ${page} 页…`, "agent-answer-view-loading");
      if (!loader) {
        const error = new Error("数据展示读取器不可用");
        report(options.onError, error, { viewId: view.id });
        host.body.replaceChildren();
        appendText(doc, host.body, "p", "该数据展示暂时无法读取。", "agent-answer-view-error");
        return;
      }
      const request = { messageId, viewId: view.id, page, pageSize };
      if (currentController) Object.defineProperty(request, "signal", { value: currentController.signal, enumerable: false });
      try {
        const pageData = await loader(request);
        if (!alive() || sequence !== requestSequence) return;
        if (!pageData || typeof pageData !== "object") throw new Error("数据展示返回格式无效");
        host.body.replaceChildren();
        appendSummary(doc, host.body, pageData);
        appendTable(doc, host.body, pageData);
        appendWarnings(doc, host.body, pageData.warnings);
        appendPagination(doc, host.body, pageData.pagination ? pageData : { ...pageData, pagination: {} }, loadPage, alive, listen);
      } catch (error) {
        if (!alive() || sequence !== requestSequence || (error && error.name === "AbortError")) return;
        report(options.onError, error, { viewId: view.id });
        host.body.replaceChildren();
        appendText(doc, host.body, "p", "该数据展示暂时无法读取，已保留已核验正文。", "agent-answer-view-error");
      }
    };
    loadPage(1, 20);
    return {
      destroy() {
        destroyed = true;
        requestSequence += 1;
        if (currentController) currentController.abort();
        listeners.splice(0).forEach(({ element, event, listener }) => element.removeEventListener(event, listener));
      },
    };
  }

  function renderAnswer(container, payload, options) {
    options = options && typeof options === "object" ? options : {};
    if (!container || typeof container.replaceChildren !== "function") throw new TypeError("答案容器无效");
    const doc = documentFor(container);
    if (!doc) throw new Error("答案渲染需要浏览器 DOM");
    const body = payload && typeof payload.body_markdown === "string" ? payload.body_markdown : "";
    const views = viewMap(payload || {});
    const runtime = { active: true, alive: () => runtime.active };
    const cleanups = [];
    container.replaceChildren();
    container.classList.add("agent-answer-rendered");
    const bodyHost = doc.createElement("div");
    bodyHost.className = "agent-answer-markdown";
    container.appendChild(bodyHost);

    const marked = markedParser();
    const purifier = sanitizer();
    if (!marked || !purifier) {
      const error = new Error("安全 Markdown 依赖未加载");
      report(options.onError, error);
      bodyHost.textContent = body;
      return { destroy() { runtime.active = false; } };
    }

    try {
      const prepared = replaceMarkersWithSentinels(body, views);
      const html = marked.parse(prepared.markdown, { gfm: true, breaks: true, headerIds: false, mangle: false });
      const fragment = purifier.sanitize(html, {
        RETURN_DOM_FRAGMENT: true,
        ALLOWED_TAGS,
        ALLOWED_ATTR,
        FORBID_TAGS,
        FORBID_ATTR,
        ALLOW_DATA_ATTR: false,
        ALLOW_ARIA_ATTR: false,
        ALLOW_UNKNOWN_PROTOCOLS: false,
        SANITIZE_DOM: true,
        SAFE_FOR_TEMPLATES: true,
      });
      filterLinks(fragment, payload || {}, doc);
      const hosts = [];
      const createHost = (viewId) => {
        const view = views.get(viewId);
        if (!view) return null;
        const host = createViewHost(doc, view);
        const mount = renderView(host, view, payload || {}, options, runtime);
        cleanups.push(() => mount.destroy());
        hosts.push(viewId);
        return host.section;
      };
      replaceSentinels(fragment, prepared.markers, doc, createHost);
      bodyHost.appendChild(fragment);

      const unreferenced = [...views.values()].filter((view) => !hosts.includes(view.id));
      if (unreferenced.length) {
        const viewHost = doc.createElement("div");
        viewHost.className = "agent-answer-unreferenced-views";
        unreferenced.forEach((view) => viewHost.appendChild(createHost(view.id)));
        bodyHost.appendChild(viewHost);
      }
    } catch (error) {
      report(options.onError, error);
      bodyHost.replaceChildren(doc.createTextNode(body));
    }

    const destroy = () => {
      if (!runtime.active) return;
      runtime.active = false;
      cleanups.splice(0).forEach((cleanup) => cleanup());
    };
    return { destroy };
  }

  return { renderAnswer };
}));
