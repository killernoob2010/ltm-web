(function() {
  "use strict";

  var PAGE_SIZE = 50;
  var YEAR_COLORS = ["#2563eb", "#f97316", "#16a34a", "#9333ea", "#0891b2"];
  var BASIS_MONTHS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];
  var basisState = {
    managementInitialized: false,
    displayInitialized: false,
    managementOffset: 0,
    managementHasMore: false,
    activePort: "日照港",
    lastChartSeries: {},
    chartHitPoints: [],
    chartHitSegments: [],
    highlightedYear: null,
  };

  var spotManagementView = document.querySelector("#dvSpotDataView");
  var basisManagementView = document.querySelector("#ironOreBasisManagementView");
  var spotDisplayView = document.querySelector("#dvSpotChartView");
  var basisDisplayView = document.querySelector("#ironOreBasisDisplayView");
  var managementYears = document.querySelector("#ironOreBasisManagementYears");
  var managementProducts = document.querySelector("#ironOreBasisManagementProducts");
  var managementPorts = document.querySelector("#ironOreBasisManagementPorts");
  var managementYearAll = document.querySelector("#ironOreBasisManagementYearAll");
  var managementYearNone = document.querySelector("#ironOreBasisManagementYearNone");
  var managementProductAll = document.querySelector("#ironOreBasisManagementProductAll");
  var managementProductNone = document.querySelector("#ironOreBasisManagementProductNone");
  var managementPortAll = document.querySelector("#ironOreBasisManagementPortAll");
  var managementPortNone = document.querySelector("#ironOreBasisManagementPortNone");
  var managementBody = document.querySelector("#ironOreBasisManagementBody");
  var managementLoadMore = document.querySelector("#ironOreBasisManagementLoadMore");
  var managementInfo = document.querySelector("#ironOreBasisManagementInfo");
  var displayYears = document.querySelector("#ironOreBasisDisplayYears");
  var displayProducts = document.querySelector("#ironOreBasisDisplayProducts");
  var displayYearAll = document.querySelector("#ironOreBasisDisplayYearAll");
  var displayYearNone = document.querySelector("#ironOreBasisDisplayYearNone");
  var displayProductAll = document.querySelector("#ironOreBasisDisplayProductAll");
  var displayProductNone = document.querySelector("#ironOreBasisDisplayProductNone");
  var portTabs = document.querySelector("#ironOreBasisPortTabs");
  var optimalDate = document.querySelector("#ironOreBasisOptimalDate");
  var optimalWarrant = document.querySelector("#ironOreBasisOptimalWarrant");
  var chartCanvas = document.querySelector("#ironOreBasisChartCanvas");
  var chartStatus = document.querySelector("#ironOreBasisChartStatus");
  var yearLegend = document.querySelector("#ironOreBasisYearLegend");
  var chartTooltip = document.querySelector("#ironOreBasisTooltip");

  function request(path, options) {
    return window.api(path, options);
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatNumber(value) {
    if (value === null || value === undefined || value === "") return "-";
    var number = Number(value);
    if (!Number.isFinite(number)) return "-";
    return number.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function checkedValues(container) {
    return Array.from(container.querySelectorAll('input[type="checkbox"]:checked')).map(function(input) {
      return input.value;
    });
  }

  function appendFilter(url, name, container) {
    var inputs = container.querySelectorAll('input[type="checkbox"]');
    var values = checkedValues(container);
    if (!inputs.length || values.length === inputs.length) return url;
    if (!values.length) return url + "&" + name + "=__EMPTY__";
    return url + "&" + name + "=" + encodeURIComponent(values.join(","));
  }

  function buildFilter(container, items, onChange) {
    container.innerHTML = items.map(function(item) {
      return '<label class="dv-checkbox-item"><input type="checkbox" value="' +
        escapeHtml(item) + '" checked><span>' + escapeHtml(item) + "</span></label>";
    }).join("");
    container.querySelectorAll('input[type="checkbox"]').forEach(function(input) {
      input.addEventListener("change", onChange);
    });
  }

  function bindFilterActions(container, allButton, noneButton, onChange) {
    allButton.addEventListener("click", function() {
      container.querySelectorAll('input[type="checkbox"]').forEach(function(input) { input.checked = true; });
      onChange();
    });
    noneButton.addEventListener("click", function() {
      container.querySelectorAll('input[type="checkbox"]').forEach(function(input) { input.checked = false; });
      onChange();
    });
  }

  async function loadManagementFilters() {
    var filters = await request("/api/iron-ore-basis/management/filters");
    buildFilter(managementYears, filters.years || [], function() { loadManagementRows(false); });
    buildFilter(managementProducts, filters.products || [], function() { loadManagementRows(false); });
    buildFilter(managementPorts, filters.ports || [], function() { loadManagementRows(false); });
    bindFilterActions(managementYears, managementYearAll, managementYearNone, function() { loadManagementRows(false); });
    bindFilterActions(managementProducts, managementProductAll, managementProductNone, function() { loadManagementRows(false); });
    bindFilterActions(managementPorts, managementPortAll, managementPortNone, function() { loadManagementRows(false); });
  }

  async function loadManagementRows(append) {
    if (!append) basisState.managementOffset = 0;
    var url = "/api/iron-ore-basis/management/rows?limit=" + PAGE_SIZE +
      "&offset=" + basisState.managementOffset;
    url = appendFilter(url, "years", managementYears);
    url = appendFilter(url, "products", managementProducts);
    url = appendFilter(url, "ports", managementPorts);
    if (!append) managementBody.innerHTML = '<tr><td colspan="11" class="empty-cell">正在加载</td></tr>';
    try {
      var result = await request(url);
      var rows = result.data || [];
      var html = rows.map(function(row) {
        return "<tr>" +
          "<td>" + escapeHtml(row.business_date) + "</td>" +
          "<td>" + escapeHtml(row.week_label) + "</td>" +
          "<td>" + escapeHtml(row.business_year) + "</td>" +
          "<td>" + escapeHtml(row.port) + "</td>" +
          "<td>" + escapeHtml(row.product) + "</td>" +
          "<td>" + formatNumber(row.wet_spot_price) + "</td>" +
          "<td>" + formatNumber(row.quality_adjustment) + "</td>" +
          "<td>" + formatNumber(row.brand_adjustment) + "</td>" +
          "<td>" + formatNumber(row.futures_close) + "</td>" +
          '<td class="iron-ore-basis-value">' + formatNumber(row.basis) + "</td>" +
          "<td>" + escapeHtml(row.data_status) + "</td></tr>";
      }).join("");
      if (append) managementBody.insertAdjacentHTML("beforeend", html);
      else managementBody.innerHTML = html || '<tr><td colspan="11" class="empty-cell">暂无符合条件的数据</td></tr>';
      var pagination = result.pagination || {};
      basisState.managementOffset = (pagination.offset || 0) + rows.length;
      basisState.managementHasMore = Boolean(pagination.has_more);
      managementLoadMore.classList.toggle("hidden", !basisState.managementHasMore);
      managementInfo.textContent = pagination.total
        ? "已显示 " + basisState.managementOffset + " / " + pagination.total + " 条"
        : "";
    } catch (error) {
      managementBody.innerHTML = '<tr><td colspan="11" class="error-cell">加载失败: ' + escapeHtml(error.message) + "</td></tr>";
      managementLoadMore.classList.add("hidden");
      managementInfo.textContent = "";
    }
  }

  async function initManagement() {
    if (!basisState.managementInitialized) {
      await loadManagementFilters();
      basisState.managementInitialized = true;
    }
    await loadManagementRows(false);
  }

  async function loadDisplayFilters() {
    var filters = await request("/api/iron-ore-basis/display/filters");
    buildFilter(displayYears, filters.years || [], loadBasisChart);
    buildFilter(displayProducts, filters.products || [], loadBasisChart);
    bindFilterActions(displayYears, displayYearAll, displayYearNone, loadBasisChart);
    bindFilterActions(displayProducts, displayProductAll, displayProductNone, loadBasisChart);
  }

  function optimalItem(label, value, emphasis) {
    return '<div class="iron-ore-basis-optimal-item' + (emphasis ? " emphasis" : "") + '">' +
      "<span>" + escapeHtml(label) + "</span><strong>" + escapeHtml(value) + "</strong></div>";
  }

  async function loadOptimalWarrant() {
    optimalDate.textContent = "";
    optimalWarrant.innerHTML = '<div class="toolbar-status">正在加载最新有效数据</div>';
    try {
      var row = await request("/api/iron-ore-basis/display/optimal-warrant");
      if (!row) {
        optimalWarrant.innerHTML = '<div class="empty-cell">本年度暂无有效基差数据</div>';
        return;
      }
      optimalDate.textContent = "数据截至 " + row.data_as_of;
      optimalWarrant.innerHTML = [
        optimalItem("品种", row.product),
        optimalItem("港口", row.port),
        optimalItem("湿吨现货价", formatNumber(row.wet_spot_price) + " 元/湿吨"),
        optimalItem("质量升贴水", formatNumber(row.quality_adjustment) + " 元/吨"),
        optimalItem("品牌升贴水", formatNumber(row.brand_adjustment) + " 元/吨"),
        optimalItem("标准化现货价", formatNumber(row.standardized_spot_price) + " 元/吨"),
        optimalItem("I0主力连续收盘价", formatNumber(row.futures_close) + " 元/吨"),
        optimalItem("基差", formatNumber(row.basis) + " 元/吨", true),
      ].join("");
    } catch (error) {
      optimalWarrant.innerHTML = '<div class="error-cell">最优仓单加载失败: ' + escapeHtml(error.message) + "</div>";
    }
  }

  async function loadBasisChart() {
    await new Promise(function(resolve) { requestAnimationFrame(resolve); });
    chartStatus.textContent = "正在加载";
    var url = "/api/iron-ore-basis/display/chart?port=" + encodeURIComponent(basisState.activePort);
    url = appendFilter(url, "years", displayYears);
    url = appendFilter(url, "products", displayProducts);
    try {
      var result = await request(url);
      basisState.lastChartSeries = result.series || {};
      chartStatus.textContent = "";
      renderBasisChart(basisState.lastChartSeries);
    } catch (error) {
      chartStatus.textContent = "图表加载失败: " + error.message;
      renderBasisChart({});
    }
  }

  function monthDayIndex(dateString) {
    var parsed = new Date(dateString + "T00:00:00Z");
    var reference = Date.UTC(2000, parsed.getUTCMonth(), parsed.getUTCDate());
    return Math.floor((reference - Date.UTC(2000, 0, 1)) / 86400000) + 1;
  }

  function drawBasisZeroAxis(ctx, x1, x2, y) {
    ctx.save();
    ctx.strokeStyle = "#64748b";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(x1, y);
    ctx.lineTo(x2, y);
    ctx.stroke();
    ctx.restore();
  }

  function collectYears(series) {
    var years = new Set();
    Object.keys(series).forEach(function(product) {
      Object.keys(series[product] || {}).forEach(function(year) { years.add(year); });
    });
    return Array.from(years).sort();
  }

  function updateBasisYearLegend(years) {
    yearLegend.innerHTML = years.map(function(year, index) {
      var stateClass = basisState.highlightedYear
        ? (basisState.highlightedYear === year ? " selected" : " dimmed")
        : "";
      return '<span class="dv-year-legend-item' + stateClass + '"><span class="dv-year-legend-swatch" style="background:' +
        YEAR_COLORS[index % YEAR_COLORS.length] + '"></span>' + escapeHtml(year) + "</span>";
    }).join("");
  }

  function hideBasisTooltip() {
    chartTooltip.classList.add("hidden");
  }

  function renderBasisChart(series) {
    var products = Object.keys(series);
    var yearsForLegend = collectYears(series);
    if (basisState.highlightedYear && !yearsForLegend.includes(basisState.highlightedYear)) {
      basisState.highlightedYear = null;
    }
    updateBasisYearLegend(yearsForLegend);
    hideBasisTooltip();

    var container = chartCanvas.parentElement;
    var width = Math.max(320, container.clientWidth);
    var columns = width >= 1100 ? 3 : (width >= 720 ? 2 : 1);
    var gap = 24;
    var panelHeight = 230;
    var rows = Math.max(1, Math.ceil(products.length / columns));
    var height = Math.max(420, rows * panelHeight + (rows - 1) * gap);
    var dpr = window.devicePixelRatio || 1;
    chartCanvas.width = width * dpr;
    chartCanvas.height = height * dpr;
    chartCanvas.style.width = width + "px";
    chartCanvas.style.height = height + "px";
    var ctx = chartCanvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    basisState.chartHitPoints = [];
    basisState.chartHitSegments = [];
    if (!products.length) {
      ctx.fillStyle = "#9ca3af";
      ctx.font = "14px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("当前港口或筛选条件下暂无基差数据", width / 2, 190);
      return;
    }

    var panelWidth = (width - gap * (columns - 1)) / columns;
    products.forEach(function(product, productIndex) {
      var col = productIndex % columns;
      var row = Math.floor(productIndex / columns);
      var originX = col * (panelWidth + gap);
      var originY = row * (panelHeight + gap);
      var pad = { top: 30, right: 18, bottom: 30, left: 48 };
      var chartWidth = panelWidth - pad.left - pad.right;
      var chartHeight = panelHeight - pad.top - pad.bottom;
      var yearsMap = series[product] || {};
      var years = Object.keys(yearsMap).sort();
      var values = [];
      years.forEach(function(year) {
        (yearsMap[year] || []).forEach(function(point) {
          if (Number.isFinite(Number(point.value))) values.push(Number(point.value));
        });
      });
      if (!values.length) return;
      var rawMin = Math.min.apply(null, values);
      var rawMax = Math.max.apply(null, values);
      var yMin = Math.min(0, rawMin);
      var yMax = Math.max(0, rawMax);
      var yPadding = (yMax - yMin) * 0.1 || 10;
      yMin -= yPadding;
      yMax += yPadding;
      function xScale(dateString) {
        return originX + pad.left + ((monthDayIndex(dateString) - 1) / 365) * chartWidth;
      }
      function yScale(value) {
        return originY + pad.top + chartHeight - ((value - yMin) / (yMax - yMin)) * chartHeight;
      }

      ctx.fillStyle = "#111827";
      ctx.font = "600 13px sans-serif";
      ctx.textAlign = "left";
      ctx.fillText(product, originX + pad.left, originY + 16);

      ctx.strokeStyle = "#e5e7eb";
      ctx.lineWidth = 1;
      for (var grid = 0; grid <= 4; grid += 1) {
        var gridY = originY + pad.top + (grid / 4) * chartHeight;
        ctx.beginPath();
        ctx.moveTo(originX + pad.left, gridY);
        ctx.lineTo(originX + pad.left + chartWidth, gridY);
        ctx.stroke();
      }
      drawBasisZeroAxis(ctx, originX + pad.left, originX + pad.left + chartWidth, yScale(0));
      ctx.fillStyle = "#64748b";
      ctx.font = "10px sans-serif";
      ctx.textAlign = "right";
      ctx.fillText(Math.round(yMax).toString(), originX + pad.left - 6, originY + pad.top + 4);
      ctx.fillText("0", originX + pad.left - 6, yScale(0) + 4);
      ctx.fillText(Math.round(yMin).toString(), originX + pad.left - 6, originY + pad.top + chartHeight);

      ctx.textAlign = "center";
      BASIS_MONTHS.forEach(function(month) {
        var tickDate = "2000-" + String(month).padStart(2, "0") + "-01";
        ctx.fillText(month + "月", xScale(tickDate), originY + pad.top + chartHeight + 18);
      });

      years.forEach(function(year) {
        var points = yearsMap[year] || [];
        var colorIndex = yearsForLegend.indexOf(year);
        var color = YEAR_COLORS[Math.max(0, colorIndex) % YEAR_COLORS.length];
        var dimmed = basisState.highlightedYear && basisState.highlightedYear !== year;
        var selected = basisState.highlightedYear === year;
        ctx.save();
        ctx.globalAlpha = dimmed ? 0.18 : 1;
        ctx.strokeStyle = color;
        ctx.lineWidth = selected ? 2.8 : 1.6;
        ctx.beginPath();
        var previous = null;
        points.forEach(function(point, pointIndex) {
          var value = Number(point.value);
          var x = xScale(point.date);
          var y = yScale(value);
          if (pointIndex === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
          basisState.chartHitPoints.push({ x: x, y: y, product: product, year: year, point: point });
          if (previous) {
            basisState.chartHitSegments.push({
              x1: previous.x, y1: previous.y, x2: x, y2: y, product: product, year: year,
            });
          }
          previous = { x: x, y: y };
        });
        ctx.stroke();
        ctx.fillStyle = color;
        points.forEach(function(point) {
          ctx.beginPath();
          ctx.arc(xScale(point.date), yScale(Number(point.value)), selected ? 1.8 : 1.25, 0, Math.PI * 2);
          ctx.fill();
        });
        ctx.restore();
      });
    });
  }

  function segmentDistance(x, y, segment) {
    var dx = segment.x2 - segment.x1;
    var dy = segment.y2 - segment.y1;
    var lengthSquared = dx * dx + dy * dy;
    if (!lengthSquared) return Math.hypot(x - segment.x1, y - segment.y1);
    var t = Math.max(0, Math.min(1, ((x - segment.x1) * dx + (y - segment.y1) * dy) / lengthSquared));
    return Math.hypot(x - (segment.x1 + t * dx), y - (segment.y1 + t * dy));
  }

  function findNearestBasisLine(x, y, maxDistance) {
    var closest = null;
    var closestDistance = maxDistance;
    basisState.chartHitSegments.forEach(function(segment) {
      var distance = segmentDistance(x, y, segment);
      if (distance < closestDistance) {
        closestDistance = distance;
        closest = segment;
      }
    });
    return closest;
  }

  function findNearestBasisPoint(x, y, maxDistance) {
    var closest = null;
    var closestDistance = maxDistance;
    basisState.chartHitPoints.forEach(function(hit) {
      var distance = Math.hypot(x - hit.x, y - hit.y);
      if (distance < closestDistance) {
        closestDistance = distance;
        closest = hit;
      }
    });
    return closest;
  }

  function canvasCoordinates(event) {
    var rect = chartCanvas.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left) * (chartCanvas.clientWidth / rect.width),
      y: (event.clientY - rect.top) * (chartCanvas.clientHeight / rect.height),
    };
  }

  function showBasisTooltip(hit, event) {
    chartTooltip.innerHTML = "<strong>" + escapeHtml(hit.product) + "</strong>" +
      "<span>日期：" + escapeHtml(hit.point.date) + "</span>" +
      "<span>年份：" + escapeHtml(hit.year) + "</span>" +
      "<span>品种：" + escapeHtml(hit.product) + "</span>" +
      "<span>港口：" + escapeHtml(basisState.activePort) + "</span>" +
      "<span>基差：" + formatNumber(hit.point.value) + " 元/吨</span>";
    chartTooltip.classList.remove("hidden");
    var containerRect = chartCanvas.parentElement.getBoundingClientRect();
    var left = event.clientX - containerRect.left + 14;
    var top = event.clientY - containerRect.top + 14;
    left = Math.min(left, containerRect.width - chartTooltip.offsetWidth - 8);
    top = Math.min(top, containerRect.height - chartTooltip.offsetHeight - 8);
    chartTooltip.style.left = Math.max(8, left) + "px";
    chartTooltip.style.top = Math.max(8, top) + "px";
  }

  async function initDisplay() {
    if (!basisState.displayInitialized) {
      await loadDisplayFilters();
      basisState.displayInitialized = true;
    }
    await Promise.all([loadOptimalWarrant(), loadBasisChart()]);
  }

  async function switchManagement(view) {
    spotManagementView.classList.toggle("hidden", view !== "spot");
    basisManagementView.classList.toggle("hidden", view !== "basis");
    if (view === "basis") await initManagement();
    else await window.activateDVSpotData();
  }

  async function switchDisplay(view) {
    spotDisplayView.classList.toggle("hidden", view !== "spot");
    basisDisplayView.classList.toggle("hidden", view !== "basis");
    if (view === "basis") await initDisplay();
    else {
      hideBasisTooltip();
      await window.activateDVSpotChart();
    }
  }

  managementLoadMore.addEventListener("click", function() { loadManagementRows(true); });
  portTabs.addEventListener("click", function(event) {
    var button = event.target.closest("[data-basis-port]");
    if (!button) return;
    basisState.activePort = button.dataset.basisPort;
    basisState.highlightedYear = null;
    portTabs.querySelectorAll("[data-basis-port]").forEach(function(item) {
      item.classList.toggle("active", item === button);
    });
    loadBasisChart();
  });
  chartCanvas.addEventListener("mousemove", function(event) {
    var point = canvasCoordinates(event);
    var hit = findNearestBasisPoint(point.x, point.y, 16);
    if (hit) showBasisTooltip(hit, event);
    else hideBasisTooltip();
  });
  chartCanvas.addEventListener("mouseleave", hideBasisTooltip);
  chartCanvas.addEventListener("click", function(event) {
    var point = canvasCoordinates(event);
    var hit = findNearestBasisLine(point.x, point.y, 8) || findNearestBasisPoint(point.x, point.y, 12);
    if (!hit) return;
    basisState.highlightedYear = basisState.highlightedYear === hit.year ? null : hit.year;
    renderBasisChart(basisState.lastChartSeries);
  });
  window.addEventListener("resize", function() {
    if (!basisDisplayView.classList.contains("hidden")) renderBasisChart(basisState.lastChartSeries);
  });

  window.IronOreBasis = {
    activateManagement: async function(view) {
      await switchManagement(view === "basis" ? "basis" : "spot");
    },
    activateDisplay: async function(view) {
      await switchDisplay(view === "basis" ? "basis" : "spot");
    },
  };
})();
