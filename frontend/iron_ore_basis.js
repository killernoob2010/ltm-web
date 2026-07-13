(function() {
  "use strict";

  var PAGE_SIZE = 50;
  var YEAR_COLORS = ["#2563eb", "#f97316", "#16a34a", "#9333ea", "#0891b2"];
  var basisState = {
    managementInitialized: false,
    displayInitialized: false,
    managementOffset: 0,
    managementHasMore: false,
    activePort: "日照港",
    lastChartSeries: {},
    chartHitPoints: [],
  };

  var managementTabs = document.querySelector("#dvDataViewTabs");
  var displayTabs = document.querySelector("#dvDisplayViewTabs");
  var spotManagementView = document.querySelector("#dvSpotDataView");
  var basisManagementView = document.querySelector("#ironOreBasisManagementView");
  var spotDisplayView = document.querySelector("#dvSpotChartView");
  var basisDisplayView = document.querySelector("#ironOreBasisDisplayView");
  var managementYears = document.querySelector("#ironOreBasisManagementYears");
  var managementProducts = document.querySelector("#ironOreBasisManagementProducts");
  var managementPorts = document.querySelector("#ironOreBasisManagementPorts");
  var managementBody = document.querySelector("#ironOreBasisManagementBody");
  var managementLoadMore = document.querySelector("#ironOreBasisManagementLoadMore");
  var managementInfo = document.querySelector("#ironOreBasisManagementInfo");
  var displayYears = document.querySelector("#ironOreBasisDisplayYears");
  var displayProducts = document.querySelector("#ironOreBasisDisplayProducts");
  var portTabs = document.querySelector("#ironOreBasisPortTabs");
  var optimalDate = document.querySelector("#ironOreBasisOptimalDate");
  var optimalWarrant = document.querySelector("#ironOreBasisOptimalWarrant");
  var chartCanvas = document.querySelector("#ironOreBasisChartCanvas");
  var chartStatus = document.querySelector("#ironOreBasisChartStatus");

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

  async function loadManagementFilters() {
    var filters = await request("/api/iron-ore-basis/management/filters");
    buildFilter(managementYears, filters.years || [], function() { loadManagementRows(false); });
    buildFilter(managementProducts, filters.products || [], function() { loadManagementRows(false); });
    buildFilter(managementPorts, filters.ports || [], function() { loadManagementRows(false); });
  }

  async function loadManagementRows(append) {
    if (!append) basisState.managementOffset = 0;
    var url = "/api/iron-ore-basis/management/rows?limit=" + PAGE_SIZE +
      "&offset=" + basisState.managementOffset;
    url = appendFilter(url, "years", managementYears);
    url = appendFilter(url, "products", managementProducts);
    url = appendFilter(url, "ports", managementPorts);
    if (!append) {
      managementBody.innerHTML = '<tr><td colspan="11" class="empty-cell">正在加载</td></tr>';
    }
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

  function renderBasisChart(series) {
    var products = Object.keys(series);
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
      var pad = { top: 44, right: 18, bottom: 30, left: 48 };
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
      years.forEach(function(year, yearIndex) {
        var legendX = originX + pad.left + yearIndex * 62;
        ctx.strokeStyle = YEAR_COLORS[yearIndex % YEAR_COLORS.length];
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(legendX, originY + 32);
        ctx.lineTo(legendX + 14, originY + 32);
        ctx.stroke();
        ctx.fillStyle = "#475569";
        ctx.font = "10px sans-serif";
        ctx.fillText(year, legendX + 18, originY + 35);
      });

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

      var basisMonthLabel = [1, 4, 7, 10, 12];
      ctx.textAlign = "center";
      basisMonthLabel.forEach(function(month) {
        var tickDate = "2000-" + String(month).padStart(2, "0") + "-01";
        ctx.fillText(month + "月", xScale(tickDate), originY + pad.top + chartHeight + 18);
      });

      years.forEach(function(year, yearIndex) {
        var points = yearsMap[year] || [];
        var color = YEAR_COLORS[yearIndex % YEAR_COLORS.length];
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.6;
        ctx.beginPath();
        points.forEach(function(point, pointIndex) {
          var value = Number(point.value);
          var x = xScale(point.date);
          var y = yScale(value);
          if (pointIndex === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
          basisState.chartHitPoints.push({ x: x, y: y, product: product, year: year, point: point });
        });
        ctx.stroke();
        ctx.fillStyle = color;
        points.forEach(function(point) {
          var x = xScale(point.date);
          var y = yScale(Number(point.value));
          ctx.beginPath();
          ctx.arc(x, y, 1.25, 0, Math.PI * 2);
          ctx.fill();
        });
      });
    });

    chartCanvas.onmousemove = function(event) {
      var rect = chartCanvas.getBoundingClientRect();
      var x = event.clientX - rect.left;
      var y = event.clientY - rect.top;
      var closest = null;
      var closestDistance = 10;
      basisState.chartHitPoints.forEach(function(hit) {
        var distance = Math.hypot(x - hit.x, y - hit.y);
        if (distance < closestDistance) {
          closestDistance = distance;
          closest = hit;
        }
      });
      chartCanvas.title = closest
        ? closest.product + " | " + closest.year + " | " + closest.point.date + " | 基差 " + formatNumber(closest.point.value) + " 元/吨"
        : "";
    };
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
    managementTabs.querySelectorAll("[data-basis-management-view]").forEach(function(button) {
      button.classList.toggle("active", button.dataset.basisManagementView === view);
    });
    if (view === "basis") await initManagement();
    else await window.activateDVSpotData();
  }

  async function switchDisplay(view) {
    spotDisplayView.classList.toggle("hidden", view !== "spot");
    basisDisplayView.classList.toggle("hidden", view !== "basis");
    displayTabs.querySelectorAll("[data-basis-display-view]").forEach(function(button) {
      button.classList.toggle("active", button.dataset.basisDisplayView === view);
    });
    if (view === "basis") await initDisplay();
    else await window.activateDVSpotChart();
  }

  managementTabs.addEventListener("click", function(event) {
    var button = event.target.closest("[data-basis-management-view]");
    if (button) switchManagement(button.dataset.basisManagementView);
  });
  displayTabs.addEventListener("click", function(event) {
    var button = event.target.closest("[data-basis-display-view]");
    if (button) switchDisplay(button.dataset.basisDisplayView);
  });
  managementLoadMore.addEventListener("click", function() { loadManagementRows(true); });
  portTabs.addEventListener("click", function(event) {
    var button = event.target.closest("[data-basis-port]");
    if (!button) return;
    basisState.activePort = button.dataset.basisPort;
    portTabs.querySelectorAll("[data-basis-port]").forEach(function(item) {
      item.classList.toggle("active", item === button);
    });
    loadBasisChart();
  });
  window.addEventListener("resize", function() {
    if (!basisDisplayView.classList.contains("hidden")) renderBasisChart(basisState.lastChartSeries);
  });

  window.IronOreBasis = {
    activateManagement: async function() {
      var active = managementTabs.querySelector("[data-basis-management-view].active");
      await switchManagement(active ? active.dataset.basisManagementView : "spot");
    },
    activateDisplay: async function() {
      var active = displayTabs.querySelector("[data-basis-display-view].active");
      await switchDisplay(active ? active.dataset.basisDisplayView : "spot");
    },
  };
})();
