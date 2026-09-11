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
  const SVG_NS = "http://www.w3.org/2000/svg";
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
    const wrapper = doc.createElement("details");
    wrapper.className = "agent-answer-view-summary";
    appendText(doc, wrapper, "summary", "查看数据范围与统计口径", "agent-answer-view-summary-title");
    const list = doc.createElement("dl");
    keys.forEach((key) => {
      const labels = {
        row_count: "行数", eligible_rows: "纳入行数", covered_rows: "已覆盖行数",
        data_period: "数据期间", unit: "单位", units: "单位集合", matched_rows: "匹配行数",
        missing_previous: "缺少基准行数", unmatched_rows: "未匹配行数",
      };
      const term = appendText(doc, list, "dt", labels[key] || key);
      const definition = appendText(doc, list, "dd", displayValue(summary[key], summary, key).text);
      if (summary[key] === null || summary[key] === undefined) definition.title = `数据缺失：${missingReason(summary, key)}`;
      term.title = key;
    });
    Object.keys(coverage).forEach((key) => {
      const labels = {
        eligible_rows: "纳入行数", covered_rows: "已覆盖行数", eligible_quantity: "纳入手数",
        covered_quantity: "已覆盖手数", eligible_contracts: "纳入合约数", covered_contracts: "已覆盖合约数",
        data_period_start: "数据起始", data_period_end: "数据结束", matched_rows: "匹配行数",
        unmatched_rows: "未匹配行数", current_rows: "当前行数", previous_rows: "基准行数",
        missing_previous: "缺少基准行数",
      };
      appendText(doc, list, "dt", labels[key] || key);
      appendText(doc, list, "dd", displayValue(coverage[key], coverage, key).text);
    });
    wrapper.appendChild(list);
    parent.appendChild(wrapper);
  }

  function svgNode(doc, tag, attributes) {
    const node = doc.createElementNS(SVG_NS, tag);
    Object.entries(attributes || {}).forEach(([key, value]) => node.setAttribute(key, asText(value)));
    return node;
  }

  function chartNumber(value) {
    if (!Number.isFinite(value)) return "—";
    return Math.abs(value) >= 1000 ? value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })
      : String(Number(value.toFixed(2)));
  }

  function chartValue(value) {
    if (value === null || value === undefined || value === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function appendSvgText(doc, svg, x, y, value, className) {
    const node = svgNode(doc, "text", { x, y });
    if (className) node.setAttribute("class", className);
    node.textContent = asText(value);
    svg.appendChild(node);
    return node;
  }

  function chartSeriesData(pageData) {
    const chart = pageData && pageData.chart;
    if (!chart || chart.fallback === "table") return null;
    if (!Array.isArray(chart.labels) || !Array.isArray(chart.series)
        || chart.labels.length > 500 || chart.series.length < 1 || chart.series.length > 4) return null;
    const labels = chart.labels.map((label) => asText(label));
    if (labels.length !== new Set(labels).size) return null;
    const series = chart.series.map((item) => {
      if (!item || typeof item !== "object" || typeof item.key !== "string" || !Array.isArray(item.values)
          || item.values.length !== labels.length) return null;
      const values = item.values.map((value) => {
        if (value === null || value === undefined) return null;
        return chartValue(value) === null ? "invalid" : value;
      });
      return values.includes("invalid") ? null : { ...item, values };
    });
    if (series.some((item) => !item)) return null;
    return { ...chart, labels, series };
  }

  function appendChart(doc, parent, pageData) {
    const chart = chartSeriesData(pageData);
    if (!chart) return false;
    const width = 760;
    const height = 340;
    const left = 58;
    const right = 18;
    const top = 32;
    const bottom = 58;
    const plotWidth = width - left - right;
    const plotHeight = height - top - bottom;
    const labels = chart.labels;
    const values = chart.series.flatMap((item) => item.values.map(chartValue).filter((value) => value !== null));
    const wrapper = doc.createElement("div");
    wrapper.className = "agent-answer-view-chart";
    const unit = chart.unit ? `单位：${asText(chart.unit)}` : "单位：未标注";
    appendText(doc, wrapper, "p", unit, "agent-answer-view-chart-unit");
    const svg = svgNode(doc, "svg", {
      viewBox: `0 0 ${width} ${height}`,
      role: "img",
      "aria-label": `${viewTitle(pageData)}，${unit}`,
      preserveAspectRatio: "xMidYMid meet",
    });
    svg.classList.add("agent-answer-view-chart-svg");
    appendSvgText(doc, svg, left, 20, viewTitle(pageData), "agent-answer-view-chart-title");
    if (!values.length) {
      appendSvgText(doc, svg, width / 2, height / 2, "暂无可绘制数据", "agent-answer-view-chart-empty");
      wrapper.appendChild(svg);
      parent.appendChild(wrapper);
      return true;
    }

    let minimum = Math.min(...values);
    let maximum = Math.max(...values);
    if (pageData.kind === "bar") {
      minimum = Math.min(0, minimum);
      maximum = Math.max(0, maximum);
    }
    if (minimum === maximum) {
      if (minimum === 0) maximum = 1;
      else { minimum -= Math.abs(minimum) * 0.1 || 1; maximum += Math.abs(maximum) * 0.1 || 1; }
    }
    const range = maximum - minimum;
    const scaleY = (value) => top + plotHeight - ((value - minimum) / range) * plotHeight;
    const scaleX = (index) => labels.length === 1 ? left + plotWidth / 2 : left + (index / (labels.length - 1)) * plotWidth;
    const baseline = scaleY(0);
    const axis = svgNode(doc, "line", { x1: left, y1: top, x2: left, y2: top + plotHeight });
    axis.setAttribute("class", "agent-answer-view-chart-axis");
    svg.appendChild(axis);
    const bottomAxis = svgNode(doc, "line", { x1: left, y1: top + plotHeight, x2: left + plotWidth, y2: top + plotHeight });
    bottomAxis.setAttribute("class", "agent-answer-view-chart-axis");
    svg.appendChild(bottomAxis);
    for (let tick = 0; tick <= 4; tick += 1) {
      const value = maximum - ((maximum - minimum) * tick / 4);
      const y = scaleY(value);
      const grid = svgNode(doc, "line", { x1: left, y1: y, x2: left + plotWidth, y2: y });
      grid.setAttribute("class", "agent-answer-view-chart-grid");
      svg.appendChild(grid);
      appendSvgText(doc, svg, left - 8, y + 4, chartNumber(value), "agent-answer-view-chart-tick").setAttribute("text-anchor", "end");
    }
    if (pageData.kind === "bar") {
      const zero = svgNode(doc, "line", { x1: left, y1: baseline, x2: left + plotWidth, y2: baseline });
      zero.setAttribute("class", "agent-answer-view-chart-zero");
      svg.appendChild(zero);
    }
    const step = labels.length <= 10 ? 1 : Math.ceil(labels.length / 8);
    labels.forEach((label, index) => {
      if (index % step !== 0 && index !== labels.length - 1) return;
      const x = scaleX(index);
      const tick = svgNode(doc, "line", { x1: x, y1: top + plotHeight, x2: x, y2: top + plotHeight + 4 });
      tick.setAttribute("class", "agent-answer-view-chart-axis");
      svg.appendChild(tick);
      const text = appendSvgText(doc, svg, x, height - 22, label.length > 18 ? `${label.slice(0, 17)}…` : label, "agent-answer-view-chart-label");
      text.setAttribute("text-anchor", "middle");
    });

    const colors = ["#2359c4", "#087443", "#a66500", "#8b3fc5"];
    if (pageData.kind === "bar") {
      const groupWidth = plotWidth / Math.max(1, labels.length) * 0.76;
      const barWidth = groupWidth / chart.series.length;
      chart.series.forEach((item, seriesIndex) => {
        item.values.forEach((raw, index) => {
          const value = chartValue(raw);
          if (value === null) return;
          const x = left + ((index + 0.5) / labels.length) * plotWidth - groupWidth / 2 + seriesIndex * barWidth;
          const valueY = scaleY(value);
          const y = value >= 0 ? valueY : baseline;
          const bar = svgNode(doc, "rect", { x, y, width: Math.max(1, barWidth - 2), height: Math.max(1, Math.abs(valueY - baseline)), fill: colors[seriesIndex] });
          bar.setAttribute("aria-label", `${asText(item.label || item.key)} ${asText(raw)}`);
          svg.appendChild(bar);
        });
      });
    } else {
      chart.series.forEach((item, seriesIndex) => {
        let segment = [];
        const flush = () => {
          if (segment.length >= 2) {
            const line = svgNode(doc, "polyline", { points: segment.map((point) => point.join(",")).join(" "), fill: "none", stroke: colors[seriesIndex], "stroke-width": 2 });
            svg.appendChild(line);
          } else if (segment.length === 1) {
            const point = svgNode(doc, "circle", { cx: segment[0][0], cy: segment[0][1], r: 3, fill: colors[seriesIndex] });
            svg.appendChild(point);
          }
          segment = [];
        };
        item.values.forEach((raw, index) => {
          const value = chartValue(raw);
          if (value === null) { flush(); return; }
          segment.push([scaleX(index), scaleY(value)]);
        });
        flush();
      });
    }
    const legend = doc.createElement("div");
    legend.className = "agent-answer-view-chart-legend";
    chart.series.forEach((item, index) => {
      const entry = doc.createElement("span");
      entry.className = "agent-answer-view-chart-legend-item";
      const swatch = doc.createElement("i");
      swatch.className = "agent-answer-view-chart-swatch";
      swatch.style.backgroundColor = colors[index];
      entry.appendChild(swatch);
      appendText(doc, entry, "span", item.label || item.key);
      legend.appendChild(entry);
    });
    wrapper.appendChild(svg);
    wrapper.appendChild(legend);
    parent.appendChild(wrapper);
    return true;
  }

  function datasetChartData(pageData) {
    const chart = pageData && pageData.chart;
    if (!chart || chart.version !== 2 || chart.fallback === "table" || !Array.isArray(chart.facets)) return null;
    if (chart.facets.length > 6) return null;
    const facets = chart.facets.map((facet) => {
      if (!facet || typeof facet !== "object" || !Array.isArray(facet.series)) return null;
      const series = facet.series.map((item) => {
        if (!item || typeof item !== "object" || typeof item.key !== "string" || !Array.isArray(item.points)) return null;
        const points = item.points.map((point) => {
          if (!point || typeof point !== "object" || point.x === undefined) return null;
          if (point.y !== null && point.y !== undefined && chartValue(point.y) === null) return null;
          return { ...point, x: point.x, y: point.y === undefined ? null : point.y };
        });
        return points.some((point) => !point) ? null : { ...item, points };
      });
      return series.some((item) => !item) ? null : { ...facet, series };
    });
    return facets.some((facet) => !facet) ? null : { ...chart, facets };
  }

  function datasetColor(key) {
    const palette = ["#2359c4", "#087443", "#a66500", "#8b3fc5", "#ad2e4b", "#117a8b", "#5e6b2e"];
    const text = asText(key);
    let hash = 0;
    for (let index = 0; index < text.length; index += 1) hash = (hash * 31 + text.charCodeAt(index)) >>> 0;
    return palette[hash % palette.length];
  }

  function appendDatasetChartTable(doc, parent, chart) {
    const wrapper = doc.createElement("div");
    wrapper.className = "agent-answer-dataset-chart-table-scroll";
    const table = doc.createElement("table");
    table.className = "agent-answer-view-table agent-answer-dataset-chart-table";
    table.setAttribute("aria-label", "图谱数据表");
    const head = doc.createElement("thead");
    const headRow = doc.createElement("tr");
    ["横轴", "观察日期"].forEach((label) => appendText(doc, headRow, "th", label));
    const series = chart.facets.flatMap((facet) => facet.series.map((item) => ({ ...item, facet: facet.title || facet.key })));
    series.forEach((item) => appendText(doc, headRow, "th", `${asText(item.facet)} · ${asText(item.label || item.key)}`));
    head.appendChild(headRow);
    table.appendChild(head);
    const body = doc.createElement("tbody");
    chart.facets.forEach((facet) => {
      const xs = [...new Set(facet.series.flatMap((item) => item.points.map((point) => asText(point.x))))];
      xs.forEach((x) => {
        const tr = doc.createElement("tr");
        appendText(doc, tr, "td", x);
        const point = facet.series.flatMap((item) => item.points).find((item) => asText(item.x) === x);
        appendText(doc, tr, "td", point && point.observation_date ? point.observation_date : "—");
        series.forEach((item) => {
          const cell = doc.createElement("td");
          if (item.facet === (facet.title || facet.key)) {
            const value = item.points.find((candidate) => asText(candidate.x) === x);
            cell.textContent = value && value.y !== null && value.y !== undefined ? asText(value.y) : "—";
            if (value && value.observation_date) cell.title = `观察日期：${asText(value.observation_date)}`;
          } else {
            cell.textContent = "—";
          }
          tr.appendChild(cell);
        });
        body.appendChild(tr);
      });
    });
    table.appendChild(body);
    wrapper.appendChild(table);
    parent.appendChild(wrapper);
  }

  function appendDatasetChart(doc, parent, pageData) {
    const chart = datasetChartData(pageData);
    if (!chart) return false;
    const wrapper = doc.createElement("div");
    wrapper.className = "agent-answer-dataset-chart";
    appendText(doc, wrapper, "p", `横轴：${asText(chart.axis_mode || "chronological")}`, "agent-answer-dataset-chart-axis-mode");
    chart.facets.forEach((facet) => {
      const facetHost = doc.createElement("section");
      facetHost.className = "agent-answer-dataset-chart-facet";
      const unit = facet.unit ? `单位：${asText(facet.unit)}` : "单位：未标注";
      appendText(doc, facetHost, "h4", `${asText(facet.title || facet.key)}（${unit}）`);
      const width = 760;
      const height = 320;
      const left = 58;
      const right = 18;
      const top = 28;
      const bottom = 56;
      const plotWidth = width - left - right;
      const plotHeight = height - top - bottom;
      const xs = [...new Set(facet.series.flatMap((item) => item.points.map((point) => asText(point.x))))];
      if (chart.axis_mode === "business_week") xs.sort((a, b) => Number(a) - Number(b));
      else xs.sort();
      const values = facet.series.flatMap((item) => item.points.map((point) => chartValue(point.y)).filter((value) => value !== null));
      const svg = svgNode(doc, "svg", {
        viewBox: `0 0 ${width} ${height}`,
        role: "img",
        "aria-label": `${asText(facet.title || facet.key)}，${unit}`,
        preserveAspectRatio: "xMidYMid meet",
      });
      svg.classList.add("agent-answer-dataset-chart-svg");
      if (!values.length) {
        appendSvgText(doc, svg, width / 2, height / 2, "暂无可绘制数据", "agent-answer-view-chart-empty");
        facetHost.appendChild(svg);
        wrapper.appendChild(facetHost);
        return;
      }
      let minimum = Math.min(...values);
      let maximum = Math.max(...values);
      if (chart.kind === "bar") { minimum = Math.min(0, minimum); maximum = Math.max(0, maximum); }
      if (minimum === maximum) { minimum -= Math.abs(minimum) * 0.1 || 1; maximum += Math.abs(maximum) * 0.1 || 1; }
      const range = maximum - minimum;
      const scaleY = (value) => top + plotHeight - ((value - minimum) / range) * plotHeight;
      const scaleX = (index) => xs.length === 1 ? left + plotWidth / 2 : left + (index / (xs.length - 1)) * plotWidth;
      const axis = svgNode(doc, "line", { x1: left, y1: top, x2: left, y2: top + plotHeight });
      axis.setAttribute("class", "agent-answer-view-chart-axis");
      svg.appendChild(axis);
      const bottomAxis = svgNode(doc, "line", { x1: left, y1: top + plotHeight, x2: left + plotWidth, y2: top + plotHeight });
      bottomAxis.setAttribute("class", "agent-answer-view-chart-axis");
      svg.appendChild(bottomAxis);
      for (let tick = 0; tick <= 4; tick += 1) {
        const value = maximum - ((maximum - minimum) * tick / 4);
        const y = scaleY(value);
        const grid = svgNode(doc, "line", { x1: left, y1: y, x2: left + plotWidth, y2: y });
        grid.setAttribute("class", "agent-answer-view-chart-grid");
        svg.appendChild(grid);
        appendSvgText(doc, svg, left - 8, y + 4, chartNumber(value), "agent-answer-view-chart-tick").setAttribute("text-anchor", "end");
      }
      if (minimum <= 0 && maximum >= 0) {
        const zero = svgNode(doc, "line", { x1: left, y1: scaleY(0), x2: left + plotWidth, y2: scaleY(0) });
        zero.setAttribute("class", "agent-answer-view-chart-zero");
        svg.appendChild(zero);
      }
      const step = xs.length <= 10 ? 1 : Math.ceil(xs.length / 8);
      xs.forEach((label, index) => {
        if (index % step !== 0 && index !== xs.length - 1) return;
        const x = scaleX(index);
        const tick = svgNode(doc, "line", { x1: x, y1: top + plotHeight, x2: x, y2: top + plotHeight + 4 });
        tick.setAttribute("class", "agent-answer-view-chart-axis");
        svg.appendChild(tick);
        const text = appendSvgText(doc, svg, x, height - 20, label.length > 18 ? `${label.slice(0, 17)}…` : label, "agent-answer-view-chart-label");
        text.setAttribute("text-anchor", "middle");
      });
      const baseline = scaleY(0);
      facet.series.forEach((item, seriesIndex) => {
        const color = datasetColor(item.color_key || item.key);
        const byX = new Map(item.points.map((point) => [asText(point.x), point]));
        if (chart.kind === "bar") {
          const groupWidth = plotWidth / Math.max(1, xs.length) * 0.76;
          const barWidth = groupWidth / Math.max(1, facet.series.length);
          xs.forEach((xValue, index) => {
            const point = byX.get(xValue);
            const value = point ? chartValue(point.y) : null;
            if (value === null) return;
            const x = left + ((index + 0.5) / xs.length) * plotWidth - groupWidth / 2 + seriesIndex * barWidth;
            const valueY = scaleY(value);
            const bar = svgNode(doc, "rect", { x, y: value >= 0 ? valueY : baseline, width: Math.max(1, barWidth - 2), height: Math.max(1, Math.abs(valueY - baseline)), fill: color });
            bar.setAttribute("aria-label", `${asText(item.label || item.key)} ${asText(point.y)}，观察日期 ${asText(point.observation_date || "未知")}`);
            svg.appendChild(bar);
          });
        } else {
          let segment = [];
          const flush = () => {
            if (segment.length >= 2) {
              const line = svgNode(doc, "polyline", { points: segment.map((point) => point.join(",")).join(" "), fill: "none", stroke: color, "stroke-width": 2 });
              svg.appendChild(line);
            } else if (segment.length === 1) {
              const point = svgNode(doc, "circle", { cx: segment[0][0], cy: segment[0][1], r: 3, fill: color });
              svg.appendChild(point);
            }
            segment = [];
          };
          xs.forEach((xValue, index) => {
            const point = byX.get(xValue);
            const value = point ? chartValue(point.y) : null;
            if (value === null) { flush(); return; }
            const x = scaleX(index);
            const marker = svgNode(doc, "circle", { cx: x, cy: scaleY(value), r: 3, fill: color });
            marker.setAttribute("aria-label", `${asText(item.label || item.key)} ${asText(point.y)}，观察日期 ${asText(point.observation_date || "未知")}`);
            const title = doc.createElementNS(SVG_NS, "title");
            title.textContent = `观察日期：${asText(point.observation_date || "未知")}；值：${asText(point.y)}`;
            marker.appendChild(title);
            svg.appendChild(marker);
            segment.push([x, scaleY(value)]);
          });
          flush();
        }
      });
      const legend = doc.createElement("div");
      legend.className = "agent-answer-view-chart-legend";
      facet.series.forEach((item) => {
        const entry = doc.createElement("span");
        entry.className = "agent-answer-view-chart-legend-item";
        const swatch = doc.createElement("i");
        swatch.className = "agent-answer-view-chart-swatch";
        swatch.style.backgroundColor = datasetColor(item.color_key || item.key);
        entry.appendChild(swatch);
        appendText(doc, entry, "span", item.label || item.key);
        legend.appendChild(entry);
      });
      facetHost.appendChild(svg);
      facetHost.appendChild(legend);
      wrapper.appendChild(facetHost);
    });
    parent.appendChild(wrapper);
    return true;
  }

  function appendFacetPagination(doc, parent, pageData, loadPage, alive, listen, matrixPage) {
    const pagination = pageData && pageData.chart && pageData.chart.facet_pagination;
    if (!pagination || !Number.isInteger(pagination.total) || pagination.total <= 6) return;
    const page = Number.isInteger(pagination.page) && pagination.page > 0 ? pagination.page : 1;
    const controls = doc.createElement("div");
    controls.className = "agent-answer-view-pagination agent-answer-facet-pagination";
    appendText(doc, controls, "span", `图谱分组 · 第 ${page} 组 / ${Math.max(1, Math.ceil(pagination.total / 6))} 组`);
    const previous = doc.createElement("button");
    previous.type = "button";
    previous.textContent = "上一组图";
    previous.disabled = page <= 1;
    listen(previous, "click", () => { if (alive()) loadPage(1, 20, page - 1, matrixPage); });
    controls.appendChild(previous);
    const next = doc.createElement("button");
    next.type = "button";
    next.textContent = "下一组图";
    next.disabled = !pagination.has_more;
    listen(next, "click", () => { if (alive()) loadPage(1, 20, page + 1, matrixPage); });
    controls.appendChild(next);
    parent.appendChild(controls);
  }

  function appendMatrix(doc, parent, matrix) {
    if (!matrix || !Array.isArray(matrix.columns) || !Array.isArray(matrix.rows)) return;
    const scroll = doc.createElement("div");
    scroll.className = "agent-answer-view-table-scroll agent-answer-matrix-scroll";
    const table = doc.createElement("table");
    table.className = "agent-answer-view-table agent-answer-matrix";
    table.setAttribute("aria-label", "港口品种变化矩阵");
    const head = doc.createElement("thead");
    const headRow = doc.createElement("tr");
    (Array.isArray(matrix.row_dimensions) ? matrix.row_dimensions : []).forEach((field) => appendText(doc, headRow, "th", field));
    matrix.columns.forEach((column) => appendText(doc, headRow, "th", `${asText(column.label || column.key)}${column.unit ? `（${asText(column.unit)}）` : ""}`));
    head.appendChild(headRow);
    table.appendChild(head);
    const body = doc.createElement("tbody");
    matrix.rows.forEach((row) => {
      const tr = doc.createElement("tr");
      (Array.isArray(matrix.row_dimensions) ? matrix.row_dimensions : []).forEach((field) => appendText(doc, tr, "td", row.dimensions && row.dimensions[field]));
      matrix.columns.forEach((column) => {
        const cell = row.cells && row.cells[column.key];
        const td = appendText(doc, tr, "td", cell && cell.value !== null && cell.value !== undefined ? cell.value : "—");
        if (cell && cell.source_row_ref) td.title = `来源行：${asText(cell.source_row_ref)}`;
      });
      body.appendChild(tr);
    });
    table.appendChild(body);
    scroll.appendChild(table);
    parent.appendChild(scroll);
  }

  function appendMatrixPagination(doc, parent, matrix, loadPage, alive, listen, facetPage) {
    const pagination = matrix && matrix.column_pagination;
    if (!pagination || pagination.total <= 12) return;
    const page = Number.isInteger(pagination.page) && pagination.page > 0 ? pagination.page : 1;
    const controls = doc.createElement("div");
    controls.className = "agent-answer-view-pagination agent-answer-matrix-pagination";
    appendText(doc, controls, "span", `矩阵列 · 第 ${page} 组 / ${Math.max(1, Math.ceil(pagination.total / 12))} 组`);
    const previous = doc.createElement("button");
    previous.type = "button";
    previous.textContent = "上一组列";
    previous.disabled = page <= 1;
    listen(previous, "click", () => { if (alive()) loadPage(1, 20, facetPage, page - 1); });
    controls.appendChild(previous);
    const next = doc.createElement("button");
    next.type = "button";
    next.textContent = "下一组列";
    next.disabled = !pagination.has_more;
    listen(next, "click", () => { if (alive()) loadPage(1, 20, facetPage, page + 1); });
    controls.appendChild(next);
    parent.appendChild(controls);
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

  function appendPagination(doc, parent, pageData, loadPage, alive, listen, facetPage = 1, matrixColumnPage = 1) {
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
    listen(previous, "click", () => { if (alive()) loadPage(page - 1, pageSize, facetPage, matrixColumnPage); });
    controls.appendChild(previous);
    const next = doc.createElement("button");
    next.type = "button";
    next.textContent = "下一页";
    next.disabled = page >= totalPages;
    listen(next, "click", () => { if (alive()) loadPage(page + 1, pageSize, facetPage, matrixColumnPage); });
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
      if (alive()) loadPage(1, Number(select.value), facetPage, matrixColumnPage);
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
    const loadPage = async (page, pageSize, facetPage = 1, matrixColumnPage = 1) => {
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
      const request = { messageId, viewId: view.id, page, pageSize, facetPage, matrixColumnPage };
      if (currentController) Object.defineProperty(request, "signal", { value: currentController.signal, enumerable: false });
      try {
        const pageData = await loader(request);
        if (!alive() || sequence !== requestSequence) return;
        if (!pageData || typeof pageData !== "object") throw new Error("数据展示返回格式无效");
        host.body.replaceChildren();
        appendSummary(doc, host.body, pageData);
        const chartPage = { ...pageData, title: view.title };
        const facetPage = pageData.chart && pageData.chart.facet_pagination
          ? Number(pageData.chart.facet_pagination.page) || 1 : 1;
        const matrixColumnPage = pageData.matrix && pageData.matrix.column_pagination
          ? Number(pageData.matrix.column_pagination.page) || 1 : 1;
        if (pageData.matrix) {
          appendMatrix(doc, host.body, pageData.matrix);
          appendMatrixPagination(doc, host.body, pageData.matrix, loadPage, alive, listen, facetPage);
        }
        if (pageData.kind === "bar" || pageData.kind === "line") {
          const chartRendered = pageData.chart && pageData.chart.version === 2
            ? appendDatasetChart(doc, host.body, chartPage)
            : appendChart(doc, host.body, chartPage);
          if (chartRendered) {
            const toggle = doc.createElement("button");
            toggle.type = "button";
            toggle.className = "agent-answer-view-table-toggle";
            toggle.textContent = "查看数据表";
            toggle.setAttribute("aria-expanded", "false");
            const tableHost = doc.createElement("div");
            tableHost.className = "agent-answer-view-table-host";
            tableHost.hidden = true;
            if (pageData.chart && pageData.chart.version === 2) appendDatasetChartTable(doc, tableHost, pageData.chart);
            appendTable(doc, tableHost, pageData);
            appendPagination(doc, tableHost, pageData.pagination ? pageData : { ...pageData, pagination: {} }, loadPage, alive, listen, facetPage, matrixColumnPage);
            listen(toggle, "click", () => {
              tableHost.hidden = !tableHost.hidden;
              toggle.textContent = tableHost.hidden ? "查看数据表" : "收起数据表";
              toggle.setAttribute("aria-expanded", String(!tableHost.hidden));
            });
            host.body.appendChild(toggle);
            host.body.appendChild(tableHost);
            if (pageData.chart && pageData.chart.version === 2) appendFacetPagination(doc, host.body, pageData, loadPage, alive, listen, matrixColumnPage);
          } else {
            appendTable(doc, host.body, pageData);
            appendPagination(doc, host.body, pageData.pagination ? pageData : { ...pageData, pagination: {} }, loadPage, alive, listen, facetPage, matrixColumnPage);
            if (pageData.chart && pageData.chart.version === 2) appendFacetPagination(doc, host.body, pageData, loadPage, alive, listen, matrixColumnPage);
          }
        } else {
          appendTable(doc, host.body, pageData);
          appendPagination(doc, host.body, pageData.pagination ? pageData : { ...pageData, pagination: {} }, loadPage, alive, listen, facetPage, matrixColumnPage);
        }
        appendWarnings(doc, host.body, pageData.warnings);
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
