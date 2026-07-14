(function() {
  "use strict";

  var basisState = {
    managementInitialized: false,
    displayInitialized: false,
    managementPage: 1,
    managementPageSize: 20,
    activePort: "日照港",
    lastChartSeries: {},
    highlightedYear: null,
    highlightedLineKey: null,
  };

  var spotManagementView = document.querySelector("#dvSpotDataView");
  var basisManagementView = document.querySelector("#ironOreBasisManagementView");
  var spotDisplayView = document.querySelector("#dvSpotChartView");
  var basisDisplayView = document.querySelector("#ironOreBasisDisplayView");
  var managementLatestDate = document.querySelector("#ironOreBasisManagementLatestDate");
  var displayLatestDate = document.querySelector("#ironOreBasisDisplayLatestDate");
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
  var managementPagination = document.querySelector("#ironOreBasisManagementPagination");
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

  async function loadManagementFilters() {
    var filters = await request("/api/iron-ore-basis/management/filters");
    managementLatestDate.textContent = "最新数据日期：" + (filters.latest_data_date || "暂无数据");
    DataVisualizationComponents.renderCheckboxOptions(
      managementYears, filters.years || [], function() { loadManagementRows(false); }, true
    );
    DataVisualizationComponents.renderCheckboxOptions(
      managementProducts, filters.products || [], function() { loadManagementRows(false); }, true
    );
    DataVisualizationComponents.renderCheckboxOptions(
      managementPorts, filters.ports || [], function() { loadManagementRows(false); }, true
    );
    DataVisualizationComponents.bindCheckboxPanelActions(
      managementYears, managementYearAll, managementYearNone, function() { loadManagementRows(false); }
    );
    DataVisualizationComponents.bindCheckboxPanelActions(
      managementProducts, managementProductAll, managementProductNone, function() { loadManagementRows(false); }
    );
    DataVisualizationComponents.bindCheckboxPanelActions(
      managementPorts, managementPortAll, managementPortNone, function() { loadManagementRows(false); }
    );
  }

  async function loadManagementRows(preservePage) {
    if (!preservePage) basisState.managementPage = 1;
    var offset = (basisState.managementPage - 1) * basisState.managementPageSize;
    var url = "/api/iron-ore-basis/management/rows?limit=" + basisState.managementPageSize +
      "&offset=" + offset;
    url = appendFilter(url, "years", managementYears);
    url = appendFilter(url, "products", managementProducts);
    url = appendFilter(url, "ports", managementPorts);
    managementBody.innerHTML = '<tr><td colspan="11" class="empty-cell">正在加载</td></tr>';
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
      managementBody.innerHTML = html || '<tr><td colspan="11" class="empty-cell">暂无符合条件的数据</td></tr>';
      var pagination = result.pagination || {};
      DataVisualizationComponents.renderPagination(managementPagination, {
        page: basisState.managementPage,
        pageSize: basisState.managementPageSize,
        total: pagination.total || 0,
        pageSizes: [20, 50, 100],
        onPageChange: function(page) {
          basisState.managementPage = page;
          loadManagementRows(true);
        },
        onPageSizeChange: function(pageSize) {
          basisState.managementPageSize = pageSize;
          basisState.managementPage = 1;
          loadManagementRows(true);
        },
      });
    } catch (error) {
      managementBody.innerHTML = '<tr><td colspan="11" class="error-cell">加载失败: ' + escapeHtml(error.message) + "</td></tr>";
      managementPagination.innerHTML = "";
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
    displayLatestDate.textContent = "最新数据日期：" + (filters.latest_data_date || "暂无数据");
    DataVisualizationComponents.renderCheckboxOptions(displayYears, filters.years || [], loadBasisChart, true);
    DataVisualizationComponents.renderCheckboxOptions(displayProducts, filters.products || [], loadBasisChart, true);
    DataVisualizationComponents.bindCheckboxPanelActions(
      displayYears, displayYearAll, displayYearNone, loadBasisChart
    );
    DataVisualizationComponents.bindCheckboxPanelActions(
      displayProducts, displayProductAll, displayProductNone, loadBasisChart
    );
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

  function hideBasisTooltip() {
    chartTooltip.classList.add("hidden");
  }

  function renderBasisChart(series) {
    DataVisualizationComponents.renderYearSmallMultiples({
      canvas: chartCanvas,
      legendElement: yearLegend,
      tooltipElement: chartTooltip,
      series: series,
      state: basisState,
      pointX: function(point) { return monthDayIndex(point.date); },
      pointValue: function(point) { return Number(point.value); },
      isMissing: function(point) {
        return !point || point.value === null || point.value === undefined || point.value === "" ||
          !Number.isFinite(Number(point.value));
      },
      xMin: 1,
      xMax: 366,
      axisTicks: DataVisualizationComponents.calendarMonthTicks,
      includeZero: true,
      drawZeroAxis: true,
      emptyMessage: "当前港口或筛选条件下暂无基差数据",
      tooltipHtml: function(hit) {
        return "<strong>" + escapeHtml(hit.product) + "</strong>" +
          "<span>日期：" + escapeHtml(hit.point.date) + "</span>" +
          "<span>年份：" + escapeHtml(hit.year) + "</span>" +
          "<span>品种：" + escapeHtml(hit.product) + "</span>" +
          "<span>港口：" + escapeHtml(basisState.activePort) + "</span>" +
          "<span>基差：" + formatNumber(hit.point.value) + " 元/吨</span>";
      },
      onHighlight: function() { renderBasisChart(basisState.lastChartSeries); },
    });
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
