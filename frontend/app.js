const state = {
  token: localStorage.getItem("token") || "",
  user: null,
  modules: [],
  activeModule: "info_summary",
  selectedGroupId: null,
  selectedGroupName: "",
  groups: [],
  positions: [],
  allGroupsPnl: null,
  midEventTimer: null,
  infoSummaryRefreshInFlight: false,
  alertSettings: [],
  alertHistory: [],
  alertNotificationTimer: null,
  alertNotificationInFlight: false,
  lastNotificationIds: new Set(),
  midConfig: { varieties: [], contracts: [] },
  infoConfig: { info_types: [], default_year: 2026, default_month: "09", contract_months: [], month_options_by_type: {}, inner_months: [] },
  infoCacheStatus: null,
  shJunnengConfig: { contracts: [], default_contract: "", default_open_date: "" },
  shJunnengTrades: [],
  shJunnengSections: { today_trades: [], current_trades: [], settled_trades: [], totals: {} },
  selectedShJunnengId: null,
  settledOverview: { trades: [], totals: {}, contracts: [] },
};

const loginView = document.querySelector("#loginView");
const appView = document.querySelector("#appView");
const loginForm = document.querySelector("#loginForm");
const loginError = document.querySelector("#loginError");
const currentUser = document.querySelector("#currentUser");
const menu = document.querySelector("#menu");
const pageTitle = document.querySelector("#pageTitle");
const pageSubtitle = document.querySelector("#pageSubtitle");

const infoSummaryPage = document.querySelector("#infoSummaryPage");
const midEventPage = document.querySelector("#midEventPage");
const shJunnengPage = document.querySelector("#shJunnengPage");
const riskAlertPage = document.querySelector("#riskAlertPage");
const userManagementPage = document.querySelector("#userManagementPage");
const placeholderPage = document.querySelector("#placeholderPage");
const placeholderTitle = document.querySelector("#placeholderTitle");
const dvIntegrationPage = document.querySelector("#dvIntegrationPage");
const dvLocalPreviewBtn = document.querySelector("#dvLocalPreviewBtn");
const dvLocalCommitBtn = document.querySelector("#dvLocalCommitBtn");
const dvIntegrationFiles = document.querySelector("#dvIntegrationFiles");
const dvUploadCommitBtn = document.querySelector("#dvUploadCommitBtn");
const dvExportBtn = document.querySelector("#dvExportBtn");
const dvIntegrationStatus = document.querySelector("#dvIntegrationStatus");
const dvIntegrationBatchInfo = document.querySelector("#dvIntegrationBatchInfo");
const dvIntegrationSummary = document.querySelector("#dvIntegrationSummary");
const dvIntegrationSamples = document.querySelector("#dvIntegrationSamples");
const dvIntegrationSampleCount = document.querySelector("#dvIntegrationSampleCount");
const dvDataPage = document.querySelector("#dvDataPage");
const dvChartPage = document.querySelector("#dvChartPage");
const dvDataTabs = document.querySelector("#dvDataTabs");
const dvDataTbody = document.querySelector("#dvDataTbody");
const dvChartTabs = document.querySelector("#dvChartTabs");
const dvChartCanvas = document.querySelector("#dvChartCanvas");
const dvChartStatus = document.querySelector("#dvChartStatus");
const dvChartViewMode = document.querySelector("#dvChartViewMode");
const dvChartProductPool = document.querySelector("#dvChartProductPool");
const dvDataProductPool = document.querySelector("#dvDataProductPool");
const dvChartYearCheckboxes = document.querySelector("#dvChartYearCheckboxes");
const dvChartProductCheckboxes = document.querySelector("#dvChartProductCheckboxes");
const dvDataYearCheckboxes = document.querySelector("#dvDataYearCheckboxes");
const dvDataYearAll = document.querySelector("#dvDataYearAll");
const dvDataYearNone = document.querySelector("#dvDataYearNone");
const dvChartYearAll = document.querySelector("#dvChartYearAll");
const dvChartYearNone = document.querySelector("#dvChartYearNone");
const dvChartProductAll = document.querySelector("#dvChartProductAll");
const dvChartProductNone = document.querySelector("#dvChartProductNone");
const dvDataProductCheckboxes = document.querySelector("#dvDataProductCheckboxes");
const dvDataCategoryCheckboxes = document.querySelector("#dvDataCategoryCheckboxes");
const dvDataCountryCheckboxes = document.querySelector("#dvDataCountryCheckboxes");
const dvDataMainstreamCheckboxes = document.querySelector("#dvDataMainstreamCheckboxes");
const dvDataProductAll = document.querySelector("#dvDataProductAll");
const dvDataProductNone = document.querySelector("#dvDataProductNone");
const dvChartCategoryCheckboxes = document.querySelector("#dvChartCategoryCheckboxes");
const dvChartCountryCheckboxes = document.querySelector("#dvChartCountryCheckboxes");
const dvChartMainstreamCheckboxes = document.querySelector("#dvChartMainstreamCheckboxes");
const dvImportBtn = document.querySelector("#dvImportBtn");
const dvImportDialog = document.querySelector("#dvImportDialog");
const dvPreviewContent = document.querySelector("#dvPreviewContent");
const dvCancelImportBtn = document.querySelector("#dvCancelImportBtn");
const dvCommitImportBtn = document.querySelector("#dvCommitImportBtn");

const infoCards = document.querySelector("#infoCards");
const indicatorsTable = document.querySelector("#indicatorsTable");
const indicatorCount = document.querySelector("#indicatorCount");
const infoStatus = document.querySelector("#infoStatus");
const infoCacheStatus = document.querySelector("#infoCacheStatus");
const refreshInfoCacheBtn = document.querySelector("#refreshInfoCacheBtn");
const importCacheBtn = document.querySelector("#importCacheBtn");
const groupsTable = document.querySelector("#groupsTable");
const positionsTable = document.querySelector("#positionsTable");
const groupCount = document.querySelector("#groupCount");
const positionsTitle = document.querySelector("#positionsTitle");
const positionSummary = document.querySelector("#positionSummary");
const midEventStatus = document.querySelector("#midEventStatus");
const allGroupsPnl = document.querySelector("#allGroupsPnl");
const groupDialog = document.querySelector("#groupDialog");
const groupForm = document.querySelector("#groupForm");
const positionDialog = document.querySelector("#positionDialog");
const positionForm = document.querySelector("#positionForm");
const confirmDialog = document.querySelector("#confirmDialog");
const confirmTitle = document.querySelector("#confirmTitle");
const confirmMessage = document.querySelector("#confirmMessage");
const cancelConfirmBtn = document.querySelector("#cancelConfirmBtn");
const okConfirmBtn = document.querySelector("#okConfirmBtn");

const alertsTable = document.querySelector("#alertsTable");
const historyTable = document.querySelector("#historyTable");
const alertCount = document.querySelector("#alertCount");
const historyCount = document.querySelector("#historyCount");
const riskAlertStatus = document.querySelector("#riskAlertStatus");
const notificationBtn = document.querySelector("#notificationBtn");
const notificationBadge = document.querySelector("#notificationBadge");
const notificationPanel = document.querySelector("#notificationPanel");
const notificationList = document.querySelector("#notificationList");
const markAllNotificationsBtn = document.querySelector("#markAllNotificationsBtn");
const toastHost = document.querySelector("#toastHost");
const selectAllAlerts = document.querySelector("#selectAllAlerts");
const alertDialog = document.querySelector("#alertDialog");
const alertForm = document.querySelector("#alertForm");
const shJunnengTodayTable = document.querySelector("#shJunnengTodayTable");
const shJunnengCurrentTable = document.querySelector("#shJunnengCurrentTable");
const shJunnengSettledTable = document.querySelector("#shJunnengSettledTable");
const shJunnengTodayCount = document.querySelector("#shJunnengTodayCount");
const shJunnengCurrentCount = document.querySelector("#shJunnengCurrentCount");
const shJunnengSettledCount = document.querySelector("#shJunnengSettledCount");
const shJunnengStatus = document.querySelector("#shJunnengStatus");
const shJunnengDialog = document.querySelector("#shJunnengDialog");
const shJunnengForm = document.querySelector("#shJunnengForm");
const shJunnengCloseDialog = document.querySelector("#shJunnengCloseDialog");
const shJunnengCloseForm = document.querySelector("#shJunnengCloseForm");
const manualPriceDialog = document.querySelector("#manualPriceDialog");
const manualPriceForm = document.querySelector("#manualPriceForm");
const manualPriceFields = document.querySelector("#manualPriceFields");
const settledOverviewDialog = document.querySelector("#settledOverviewDialog");
const settledOverviewTable = document.querySelector("#settledOverviewTable");

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "请求失败");
  }
  return response.json();
}

function showLogin() {
  loginView.classList.remove("hidden");
  appView.classList.add("hidden");
}

function showApp() {
  loginView.classList.add("hidden");
  appView.classList.remove("hidden");
}

function today() {
  return new Date().toISOString().slice(0, 10);
}

function formatDateOnly(value) {
  if (!value) return "-";
  return String(value).slice(0, 10);
}

function money(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

function rate(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return Number(value).toLocaleString("zh-CN", { minimumFractionDigits: 4, maximumFractionDigits: 4 });
}

function midEventPrice(item, value) {
  return item.variety === "USD/CNY" ? rate(value) : money(value);
}

function dateTimeToSecond(value) {
  return value ? String(value).replace(/\.\d+/, "").replace(/([+-]\d{2})(:\d{2})?$/, "") : "";
}

function pnlClass(value) {
  const number = Number(value || 0);
  if (number > 0) return "numeric-up";
  if (number < 0) return "numeric-down";
  return "";
}

function confirmAction(title, message) {
  confirmTitle.textContent = title;
  confirmMessage.textContent = message;
  return new Promise((resolve) => {
    let settled = false;
    const cleanup = (value) => {
      if (settled) return;
      settled = true;
      cancelConfirmBtn.removeEventListener("click", onCancel);
      okConfirmBtn.removeEventListener("click", onOk);
      confirmDialog.removeEventListener("close", onClose);
      resolve(value);
    };
    const onCancel = () => {
      confirmDialog.close();
      cleanup(false);
    };
    const onOk = () => {
      confirmDialog.close();
      cleanup(true);
    };
    const onClose = () => cleanup(false);
    cancelConfirmBtn.addEventListener("click", onCancel, { once: true });
    okConfirmBtn.addEventListener("click", onOk, { once: true });
    confirmDialog.addEventListener("close", onClose, { once: true });
    confirmDialog.showModal();
  });
}

function moduleLabel(code) {
  for (const group of state.modules) {
    const item = group.items.find((entry) => entry.code === code);
    if (item) return { group: group.group, name: item.name };
  }
  return { group: "", name: code };
}

function renderMenu() {
  menu.innerHTML = "";
  for (const group of state.modules) {
    const wrapper = document.createElement("section");
    wrapper.className = "menu-group";
    wrapper.innerHTML = `<p class="menu-group-title">${group.group}</p>`;
    for (const item of group.items) {
      const button = document.createElement("button");
      button.className = `menu-item ${item.code === state.activeModule ? "active" : ""}`;
      button.textContent = item.name;
      button.addEventListener("click", () => activateModule(item.code, item.name));
      wrapper.appendChild(button);
    }
    menu.appendChild(wrapper);
  }
}

function showOnly(page) {
  [infoSummaryPage, midEventPage, shJunnengPage, riskAlertPage, userManagementPage, dvIntegrationPage, dvDataPage, dvChartPage, placeholderPage].forEach((item) => item.classList.add("hidden"));
  page.classList.remove("hidden");
}

async function activateModule(code, subName) {
  state.activeModule = code;
  stopMidEventAutoRefresh();
  stopInfoSummaryAutoRefresh();
  renderMenu();
  const label = moduleLabel(code);
  pageTitle.textContent = label.name;
  pageSubtitle.textContent = `${label.group} / ${label.name}`;

  if (code === "info_summary") {
    showOnly(infoSummaryPage);
    await loadInfoSummary();
    startInfoSummaryAutoRefresh();
    return;
  }
  if (code === "mid_event_monitor") {
    showOnly(midEventPage);
    await loadMidEvent();
    startMidEventAutoRefresh();
    return;
  }
  if (code === "sh_junneng") {
    showOnly(shJunnengPage);
    await loadShJunneng();
    refreshShJunnengPrices(false).catch((error) => updateShJunnengStatus(error.message));
    return;
  }
  if (code === "risk_alert") {
    showOnly(riskAlertPage);
    await loadRiskAlert();
    return;
  }
  if (code === "user_management") {
    showOnly(userManagementPage);
    await loadUserManagement();
    return;
  }
  if (code === "data_visualization_integration") {
    showOnly(dvIntegrationPage);
    await loadDVIntegrationLatest();
    return;
  }
  if (code === "data_visualization_data") {
    showOnly(dvDataPage);
    initDVData();
    await loadDVTable("inventory");
    return;
  }
  if (code === "data_visualization_chart") {
    showOnly(dvChartPage);
    if (!dvState.dvChartControlsInitialized) {
      await initDVChartControls();
      dvState.dvChartControlsInitialized = true;
    }
    await loadDVChart();
    return;
  }

  showOnly(placeholderPage);
  placeholderTitle.textContent = label.name;
}

async function bootstrap() {
  if (!state.token) {
    showLogin();
    return;
  }
  try {
    state.user = await api("/api/auth/me");
    state.modules = await api("/api/auth/modules");
  } catch {
    localStorage.removeItem("token");
    state.token = "";
    showLogin();
    return;
  }
  currentUser.textContent = `${state.user.name}｜${state.user.role}`;
  showApp();
  renderMenu();
  startAlertNotifications();
  activateModule("info_summary").catch(() => {});

}

async function loadInfoSummary() {
  state.infoConfig = await api("/api/info-summary/config");
  await loadInfoCacheStatus();
  renderInfoCards();
  updateInfoStatus("正在计算全部指标");
  await calculateAllInfo(false);
  updateInfoStatus("页面已加载并完成计算");
}

async function loadInfoCacheStatus() {
  try {
    state.infoCacheStatus = await api("/api/info-summary/cache/status");
    if (state.infoCacheStatus?.cache_counts) {
      state.infoConfig.cache_counts = state.infoCacheStatus.cache_counts;
    }
    updateInfoCacheStatus("已读取");
  } catch (error) {
    updateInfoCacheStatus(error.message);
  }
}

function renderInfoCards() {
  const years = Array.from({ length: 11 }, (_, index) => 2020 + index);
  const yearOptions = years.map((year) => `<option value="${year}">${year}</option>`).join("");
  const monthOptionsForType = (type) => (state.infoConfig.month_options_by_type?.[type] || state.infoConfig.contract_months)
    .map((month) => `<option value="${month}">${month}</option>`)
    .join("");
  const allMonthOptions = monthOptionsForType("");
  infoCards.innerHTML = state.infoConfig.info_types.map((type) => {
    const isMonthDiff = type === "月差" || type === "掉期月差";
    const isInnerOuter = type === "内外盘差" || type === "内外盘差2";
    const monthSelect = `<select class="info-month">${monthOptionsForType(type)}</select>`;
    const yearSelect = `<select class="info-year">${yearOptions}</select>`;
    const controls = isInnerOuter
      ? `
        <label>选择日期<input class="info-date" type="date" value="${today()}" /></label>
        <label>年${yearSelect}</label>
        <button class="calculate-info-btn">计算</button>
      `
      : isMonthDiff
        ? `
          <label>选择日期<input class="info-date" type="date" value="${today()}" /></label>
          <label>年1<select class="info-year1">${yearOptions}</select></label>
          <label>月1<select class="info-month1">${allMonthOptions}</select></label>
          <label>年2<select class="info-year2">${yearOptions}</select></label>
          <label>月2<select class="info-month2">${allMonthOptions}</select></label>
          <button class="calculate-info-btn">计算</button>
        `
        : `
          <label>选择日期<input class="info-date" type="date" value="${today()}" /></label>
          <label>年${yearSelect}</label>
          <label>月${monthSelect}</label>
          <button class="calculate-info-btn">计算</button>
        `;
    const values = isInnerOuter
      ? `
        <div class="inner-month-row">
          <span class="value-label">今日值</span>
          ${state.infoConfig.inner_months.map((month) => `
            <div class="inner-month-value">
              <span>${month}月</span>
              <strong class="today-value" data-month="${month}">--</strong>
            </div>
          `).join("")}
        </div>
      `
      : `
        <div class="legacy-values">
          <div><span>今日值</span><strong class="today-value">--</strong></div>
          <div><span>昨日值</span><strong class="t1-value">--</strong></div>
          <div><span>前日值</span><strong class="t2-value">--</strong></div>
          <div><span>均值</span><strong class="mean-value">--</strong></div>
          <div><span>最小值</span><strong class="min-value">--</strong></div>
          <div><span>最大值</span><strong class="max-value">--</strong></div>
          <div><span>标准差</span><strong class="std-value">--</strong></div>
        </div>
      `;
    return `
      <section class="info-section" data-info-type="${type}">
        <h2>${type}</h2>
        <div class="info-control-row">
          ${controls}
        </div>
        <div class="info-values legacy-layout">
          ${values}
        </div>
        <p class="info-card-status status-value">待计算</p>
      </section>
    `;
  }).join("");

  infoCards.querySelectorAll(".info-section").forEach((card) => {
    const year = card.querySelector(".info-year");
    const year1 = card.querySelector(".info-year1");
    const year2 = card.querySelector(".info-year2");
    const month = card.querySelector(".info-month");
    const month1 = card.querySelector(".info-month1");
    const month2 = card.querySelector(".info-month2");
    if (year) year.value = state.infoConfig.default_year;
    if (year1) year1.value = state.infoConfig.yuecha_defaults?.year1 || state.infoConfig.default_year;
    if (year2) year2.value = state.infoConfig.yuecha_defaults?.year2 || state.infoConfig.default_year;
    if (month) month.value = state.infoConfig.default_month;
    if (month1) month1.value = state.infoConfig.yuecha_defaults?.month1 || "09";
    if (month2) month2.value = state.infoConfig.yuecha_defaults?.month2 || "01";
    card.querySelector(".calculate-info-btn").addEventListener("click", () => calculateInfoCard(card));
  });
}

function buildInfoPayload(card) {
  const year = Number(card.querySelector(".info-year")?.value || card.querySelector(".info-year1")?.value || state.infoConfig.default_year);
  return {
    info_type: card.dataset.infoType,
    year,
    month: card.querySelector(".info-month")?.value || "09",
    calc_date: card.querySelector(".info-date").value,
    year1: Number(card.querySelector(".info-year1")?.value || year),
    month1: card.querySelector(".info-month1")?.value || undefined,
    year2: Number(card.querySelector(".info-year2")?.value || year),
    month2: card.querySelector(".info-month2")?.value || undefined,
  };
}

function applyInfoResult(card, result) {
  const status = card.querySelector(".status-value");
  fillInfoResult(card, result);
  status.textContent = result.cache_hit ? "已读取缓存并刷新今日值" : "缓存未命中，已刷新今日值";
}

async function calculateInfoCard(card, mock = false) {
  const infoType = card.dataset.infoType;
  const payload = buildInfoPayload(card);
  const status = card.querySelector(".status-value");
  status.textContent = "计算中";
  try {
    const result = await api(`/api/info-summary/calculate${mock ? "?mock=true" : ""}`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    applyInfoResult(card, result);
    updateInfoStatus(`${infoType} 已计算`);
  } catch (error) {
    status.textContent = error.message;
  }
}

function fillInfoResult(card, result) {
  if (result.month_results) {
    for (const [month, item] of Object.entries(result.month_results)) {
      const todayValue = card.querySelector(`.today-value[data-month="${month}"]`);
      if (todayValue) todayValue.textContent = money(item.today_value);
    }
    return;
  }

  card.querySelector(".today-value").textContent = money(result.today_value);
  card.querySelector(".t1-value").textContent = money(result.t_1_value);
  card.querySelector(".t2-value").textContent = money(result.t_2_value);
  card.querySelector(".mean-value").textContent = money(result.mean_value);
  card.querySelector(".min-value").textContent = money(result.min_value);
  card.querySelector(".max-value").textContent = money(result.max_value);
  card.querySelector(".std-value").textContent = money(result.std_value);
}

async function loadInfoHistory() {
  if (!indicatorsTable || !indicatorCount) return;
  const rows = await api("/api/info-summary/indicators");
  indicatorCount.textContent = `${rows.length} 条`;
  indicatorsTable.innerHTML = rows.map((item) => `
    <tr>
      <td>${item.info_type}</td>
      <td>${item.calc_date}</td>
      <td>${item.year}-${item.month}</td>
      <td>${money(item.t_1_value)}</td>
      <td>${money(item.t_2_value)}</td>
      <td>${money(item.mean_value)}</td>
      <td>${money(item.min_value)}</td>
      <td>${money(item.max_value)}</td>
      <td>${money(item.std_value)}</td>
    </tr>
  `).join("");
}

async function calculateAllInfo(mock = false) {
  const cards = [...infoCards.querySelectorAll(".info-section")];
  cards.forEach((card) => {
    card.querySelector(".status-value").textContent = "计算中";
  });
  const result = await api(`/api/info-summary/calculate-all${mock ? "?mock=true" : ""}`, {
    method: "POST",
    body: JSON.stringify({ items: cards.map(buildInfoPayload) }),
  });
  const resultsByType = new Map((result.cards || []).map((item) => [item.info_type, item]));
  for (const card of cards) {
    const item = resultsByType.get(card.dataset.infoType);
    if (item) {
      applyInfoResult(card, item);
    } else {
      card.querySelector(".status-value").textContent = "未返回结果";
    }
  }
  updateInfoStatus("全部指标已计算");
}

function updateInfoStatus(message) {
  if (!infoStatus) return;
  const counts = state.infoConfig.cache_counts || {};
  const countText = `缓存：计算 ${counts.calculated_data || 0}，价格 ${counts.daily_prices || 0}，交易日 ${counts.trading_days || 0}`;
  infoStatus.textContent = `实时更新：开启｜最后更新：${new Date().toLocaleTimeString("zh-CN")}｜${countText}｜${message}`;
}

function updateInfoCacheStatus(message) {
  if (!infoCacheStatus) return;
  const indicators = state.infoCacheStatus?.indicators || [];
  const dates = indicators.flatMap((item) => [item.latest_price_date, item.latest_calculated_date]).filter(Boolean);
  dates.sort();
  const latestDate = dates.length ? dates[dates.length - 1] : "--";
  infoCacheStatus.textContent = `历史缓存截至：${latestDate}｜${message}`;
}

async function refreshInfoCache() {
  refreshInfoCacheBtn.disabled = true;
  updateInfoCacheStatus("正在回填");
  try {
    const result = await api("/api/info-summary/cache/backfill", {
      method: "POST",
      body: JSON.stringify({ calc_date: today() }),
    });
    state.infoConfig.cache_counts = result.cache_counts || state.infoConfig.cache_counts;
    updateInfoCacheStatus(result.status === "started" ? "回填已启动" : "部分指标需检查");
    window.setTimeout(() => {
      loadInfoCacheStatus().catch(() => {});
    }, 5000);
  } catch (error) {
    updateInfoCacheStatus(error.message);
  } finally {
    refreshInfoCacheBtn.disabled = false;
  }
}

async function loadMidEvent(preferredGroupId = null) {
  state.midConfig = await api("/api/mid-event/config");
  const result = await api("/api/mid-event/groups");
  state.groups = result.groups || [];
  state.allGroupsPnl = result.all_total_pnl;
  if (preferredGroupId && state.groups.some((group) => group.id === preferredGroupId)) {
    state.selectedGroupId = preferredGroupId;
  }
  if (!state.selectedGroupId && state.groups.length) {
    state.selectedGroupId = state.groups[0].id;
    state.selectedGroupName = state.groups[0].group_name;
  }
  if (state.selectedGroupId && !state.groups.some((group) => group.id === state.selectedGroupId)) {
    state.selectedGroupId = state.groups[0]?.id || null;
    state.selectedGroupName = state.groups[0]?.group_name || "";
  }
  const selectedGroup = state.groups.find((group) => group.id === state.selectedGroupId);
  if (selectedGroup) state.selectedGroupName = selectedGroup.group_name;
  renderGroups();
  renderAllGroupsPnl();
  await loadPositions();
  updateMidEventStatus("页面已刷新");
}

function renderGroups() {
  groupCount.textContent = `${state.groups.length} 个`;
  groupsTable.innerHTML = state.groups.map((group) => `
    <tr class="${group.id === state.selectedGroupId ? "selected-row" : ""}">
      <td>${group.group_name}</td>
      <td class="${pnlClass(group.total_pnl)}">${money(group.total_pnl)}</td>
      <td>${dateTimeToSecond(group.updated_at || group.created_at)}</td>
      <td>
        <div class="row-actions">
          <button class="link" data-action="select" data-id="${group.id}">查看</button>
          <button class="link" data-action="edit" data-id="${group.id}">编辑</button>
          <button class="link" data-action="delete" data-id="${group.id}">删除</button>
        </div>
      </td>
    </tr>
  `).join("");
  groupsTable.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => handleGroupAction(button.dataset.action, Number(button.dataset.id)));
  });
}

function renderAllGroupsPnl() {
  if (!allGroupsPnl) return;
  allGroupsPnl.textContent = money(state.allGroupsPnl);
  allGroupsPnl.className = pnlClass(state.allGroupsPnl);
}

function updateMidEventStatus(message) {
  if (!midEventStatus) return;
  midEventStatus.textContent = `实时更新：开启｜最后更新：${new Date().toLocaleTimeString("zh-CN")}｜${message}`;
}

function stopMidEventAutoRefresh() {
  if (state.midEventTimer) {
    window.clearInterval(state.midEventTimer);
    state.midEventTimer = null;
  }
}
function stopInfoSummaryAutoRefresh() {
  if (state.infoSummaryTimer) {
    window.clearInterval(state.infoSummaryTimer);
    state.infoSummaryTimer = null;
  }
}

function startInfoSummaryAutoRefresh() {
  stopInfoSummaryAutoRefresh();
  state.infoSummaryTimer = window.setInterval(async () => {
    if (state.infoSummaryRefreshInFlight) return;
    if (state.activeModule === "info_summary") {
      state.infoSummaryRefreshInFlight = true;
      try {
        await calculateAllInfo(false);
        updateInfoStatus("自动刷新完成");
      } catch (error) {
        updateInfoStatus(error.message);
      } finally {
        state.infoSummaryRefreshInFlight = false;
      }
    }
  }, 60000);
}

function startMidEventAutoRefresh() {
  stopMidEventAutoRefresh();
  state.midEventTimer = window.setInterval(() => {
    if (state.activeModule === "mid_event_monitor") {
      refreshPrices(false).catch((error) => updateMidEventStatus(error.message));
    }
  }, 5000);
}

async function handleGroupAction(action, id) {
  const group = state.groups.find((item) => item.id === id);
  if (action === "select") {
    state.selectedGroupId = id;
    state.selectedGroupName = group.group_name;
    renderGroups();
    await loadPositions();
    return;
  }
  if (action === "edit") {
    document.querySelector("#groupDialogTitle").textContent = "编辑策略组";
    document.querySelector("#groupId").value = group.id;
    document.querySelector("#groupName").value = group.group_name;
    groupDialog.showModal();
    return;
  }
  if (action === "delete") {
    const confirmed = await confirmAction("删除策略组", "确认删除该策略组及其持仓？");
    if (!confirmed) return;
    await api(`/api/mid-event/groups/${id}`, { method: "DELETE" });
    if (state.selectedGroupId === id) {
      state.selectedGroupId = null;
      state.selectedGroupName = "";
    }
    await loadMidEvent();
  }
}

async function refreshPrices(mock = false) {
  const result = await api(`/api/mid-event/prices/refresh${mock ? "?mock=true" : ""}`, { method: "POST" });
  await loadMidEvent();
  const missing = result.missing_contracts || [];
  const reused = result.reused_contracts || [];
  if (missing.length) {
    updateMidEventStatus(`价格已刷新，${missing.length} 个合约未取到价格`);
  } else if (reused.length) {
    updateMidEventStatus(`价格已刷新，${reused.length} 个合约沿用已有价格`);
  } else {
    updateMidEventStatus("价格已刷新");
  }
}

async function loadPositions() {
  if (!state.selectedGroupId) {
    positionsTitle.textContent = "持仓";
    positionSummary.textContent = "请选择策略组";
    positionsTable.innerHTML = "";
    return;
  }
  const result = await api(`/api/mid-event/groups/${state.selectedGroupId}/positions`);
  state.positions = result.positions || [];
  state.allGroupsPnl = result.all_total_pnl;
  const total = result.total_pnl;
  positionsTitle.textContent = `${state.selectedGroupName || "策略组"} 持仓明细`;
  positionSummary.innerHTML = total === null || total === undefined
    ? "合计 --（价格获取中）"
    : `合计 <span class="${pnlClass(total)}">${money(total)}</span>`;
  renderAllGroupsPnl();
  const rows = state.positions.map((item) => `
    <tr>
      <td>${item.variety_name || item.variety}</td>
      <td>${item.contract || "-"}</td>
      <td>${item.direction === "long" ? "多" : "空"}</td>
      <td>${midEventPrice(item, item.open_price)}</td>
      <td>${midEventPrice(item, item.current_price)}</td>
      <td>${item.quantity}</td>
      <td>${item.multiplier}</td>
      <td class="${pnlClass(item.floating_pnl)}">${money(item.floating_pnl)}</td>
      <td>
        <div class="row-actions">
          <button class="link" data-action="edit" data-id="${item.id}">编辑</button>
          <button class="link" data-action="delete" data-id="${item.id}">删除</button>
        </div>
      </td>
    </tr>
  `);
  if (state.positions.length) {
    rows.push(`
      <tr class="summary-row">
        <td>【当前组汇总】</td>
        <td></td>
        <td></td>
        <td></td>
        <td></td>
        <td></td>
        <td></td>
        <td class="${pnlClass(total)}">${money(total)}</td>
        <td></td>
      </tr>
    `);
  }
  positionsTable.innerHTML = rows.join("");
  positionsTable.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => handlePositionAction(button.dataset.action, Number(button.dataset.id)));
  });
}

async function handlePositionAction(action, id) {
  const item = state.positions.find((entry) => entry.id === id);
  if (action === "edit") {
    openPositionDialog(item);
    return;
  }
  if (action === "delete") {
    const confirmed = await confirmAction("删除持仓", "确认删除该持仓？");
    if (!confirmed) return;
    await api(`/api/mid-event/positions/${id}`, { method: "DELETE" });
    await loadPositions();
  }
}

function openGroupDialog() {
  document.querySelector("#groupDialogTitle").textContent = "新增策略组";
  document.querySelector("#groupId").value = "";
  document.querySelector("#groupName").value = "";
  groupDialog.showModal();
}

function fillPositionOptions() {
  const varietySelect = document.querySelector("#positionVariety");
  const contractSelect = document.querySelector("#positionContract");
  varietySelect.innerHTML = state.midConfig.varieties.map((item) => `<option value="${item.code}">${item.name} (${item.code})</option>`).join("");
  contractSelect.innerHTML = state.midConfig.contracts.map((item) => `<option value="${item}">${item}</option>`).join("");
}

function syncPositionOpenPriceStep() {
  const priceInput = document.querySelector("#positionOpenPrice");
  priceInput.step = document.querySelector("#positionVariety").value === "USD/CNY" ? "0.0001" : "0.01";
}

function openPositionDialog(item = null) {
  if (!state.selectedGroupId) {
    window.alert("请先新增或选择一个策略组");
    return;
  }
  fillPositionOptions();
  document.querySelector("#positionDialogTitle").textContent = item ? "编辑持仓" : "新增持仓";
  document.querySelector("#positionId").value = item?.id || "";
  document.querySelector("#positionVariety").value = item?.variety || "I";
  document.querySelector("#positionContract").value = item?.contract || state.midConfig.contracts[0] || "";
  document.querySelector("#positionDirection").value = item?.direction || "long";
  document.querySelector("#positionOpenPrice").value = item?.open_price ?? "";
  document.querySelector("#positionQuantity").value = item?.quantity || 1;
  syncPositionOpenPriceStep();
  positionDialog.showModal();
}

function updateShJunnengStatus(message) {
  if (!shJunnengStatus) return;
  shJunnengStatus.textContent = `台账状态：${message}｜最后更新：${new Date().toLocaleTimeString("zh-CN")}`;
}

function shJunnengFilters() {
  const params = new URLSearchParams();
  const selectedDate = document.querySelector("#shJunnengSelectedDate").value || today();
  const keyword = document.querySelector("#shJunnengFilterKeyword").value.trim();
  params.set("selected_date", selectedDate);
  if (keyword) params.set("keyword", keyword);
  return params.toString();
}

async function loadShJunneng() {
  if (!state.shJunnengConfig.contracts.length) {
    state.shJunnengConfig = await api("/api/ledgers/sh-junneng/config");
    document.querySelector("#shJunnengSelectedDate").value = state.shJunnengConfig.default_open_date || today();
  }
  const query = shJunnengFilters();
  const result = await api(`/api/ledgers/sh-junneng/trades${query ? `?${query}` : ""}`);
  state.shJunnengTrades = result.trades || [];
  state.shJunnengSections = {
    today_trades: result.today_trades || [],
    current_trades: result.current_trades || [],
    settled_trades: result.settled_trades || [],
    totals: result.totals || {},
  };
  if (state.selectedShJunnengId && !state.shJunnengTrades.some((item) => item.id === state.selectedShJunnengId)) {
    state.selectedShJunnengId = null;
  }
  renderShJunneng();
  updateShJunnengStatus("页面已刷新");
}

function shJunnengRowClass(item) {
  return item.id === state.selectedShJunnengId ? "selected-row" : "";
}

function selectShJunnengTrade(id) {
  state.selectedShJunnengId = id;
  renderShJunneng();
}

function shJunnengTradeById(id) {
  return state.shJunnengTrades.find((item) => item.id === id);
}

function shJunnengSummaryRow(total, type = "today") {
  let cells = ["", "小计", "", "", money(total.trade_quantity), money(total.open_fee), money(total.close_fee), money(total.profit), "", "", ""];
  if (type === "current") {
    cells = ["", "小计", "", "", money(total.hold_quantity), money(total.open_fee), money(total.profit), "", ""];
  }
  if (type === "settled") {
    cells = ["", "小计", "", "", money(total.trade_quantity), money(total.open_fee), money(total.close_fee), money(total.profit), "", "", money(total.interest), money(total.profit_80), money(total.profit_20)];
  }
  return `<tr class="summary-row">${cells.map((cell) => `<td>${cell}</td>`).join("")}</tr>`;
}

function bindShJunnengRows(tbody) {
  tbody.querySelectorAll("tr[data-id]").forEach((row) => {
    row.addEventListener("click", () => selectShJunnengTrade(Number(row.dataset.id)));
    row.addEventListener("dblclick", () => {
      const item = shJunnengTradeById(Number(row.dataset.id));
      if (item) openShJunnengDialog(item);
    });
  });
}

function renderShJunneng() {
  const todayRows = state.shJunnengSections.today_trades || [];
  const currentRows = state.shJunnengSections.current_trades || [];
  const settledRows = state.shJunnengSections.settled_trades || [];
  const totals = state.shJunnengSections.totals || {};
  shJunnengTodayCount.textContent = `${todayRows.length} 条`;
  shJunnengCurrentCount.textContent = `${currentRows.length} 条`;
  shJunnengSettledCount.textContent = `${settledRows.length} 条`;

  shJunnengTodayTable.innerHTML = todayRows.map((item) => `
    <tr data-id="${item.id}" class="${shJunnengRowClass(item)}">
      <td>${item.contract_code || item.contract_month}</td>
      <td>${item.direction_label}</td>
      <td>${money(item.open_price)}</td>
      <td>${item.display_close_price}</td>
      <td>${item.trade_quantity}</td>
      <td>${money(item.open_fee)}</td>
      <td>${item.display_close_fee}</td>
      <td class="${pnlClass(item.profit)}">${money(item.profit)}</td>
      <td>${item.open_date}</td>
      <td>${item.display_close_date}</td>
      <td>${item.is_closed_label}</td>
    </tr>
  `).join("");
  if (todayRows.length) shJunnengTodayTable.innerHTML += shJunnengSummaryRow(totals.today || {}, "today");

  shJunnengCurrentTable.innerHTML = currentRows.map((item) => `
    <tr data-id="${item.id}" class="${shJunnengRowClass(item)}">
      <td>${item.contract_code || item.contract_month}</td>
      <td>${item.direction_label}</td>
      <td>${money(item.open_price)}</td>
      <td>${money(item.current_price)}</td>
      <td>${item.hold_quantity}</td>
      <td>${money(item.open_fee)}</td>
      <td class="${pnlClass(item.profit)}">${money(item.profit)}</td>
      <td>${item.open_date}</td>
      <td>${item.is_closed_label}</td>
    </tr>
  `).join("");
  if (currentRows.length) shJunnengCurrentTable.innerHTML += shJunnengSummaryRow(totals.current || {}, "current");

  shJunnengSettledTable.innerHTML = settledRows.map((item) => `
    <tr data-id="${item.id}" class="${shJunnengRowClass(item)}">
      <td>${item.contract_code || item.contract_month}</td>
      <td>${item.direction_label}</td>
      <td>${money(item.open_price)}</td>
      <td>${money(item.close_price)}</td>
      <td>${item.trade_quantity}</td>
      <td>${money(item.open_fee)}</td>
      <td>${money(item.close_fee)}</td>
      <td class="${pnlClass(item.profit)}">${money(item.profit)}</td>
      <td>${item.open_date}</td>
      <td>${item.close_date}</td>
      <td>${money(item.interest)}</td>
      <td>${money(item.profit_80)}</td>
      <td>${money(item.profit_20)}</td>
    </tr>
  `).join("");
  if (settledRows.length) shJunnengSettledTable.innerHTML += shJunnengSummaryRow(totals.settled || {}, "settled");

  [shJunnengTodayTable, shJunnengCurrentTable, shJunnengSettledTable].forEach(bindShJunnengRows);
}

function openShJunnengDialog(item = null) {
  document.querySelector("#shJunnengDialogTitle").textContent = item ? "编辑交易" : "新增交易";
  document.querySelector("#shJunnengTradeId").value = item?.id || "";
  document.querySelector("#shJunnengContractMonth").value = item?.contract_month || state.shJunnengConfig.default_contract || "";
  document.querySelector("#shJunnengDirection").value = item?.direction || "多头";
  document.querySelector("#shJunnengIsClosed").value = item?.is_closed_label || "未平仓";
  document.querySelector("#shJunnengOpenPrice").value = item?.open_price ?? "";
  document.querySelector("#shJunnengTradeQuantity").value = item?.trade_quantity || 1;
  document.querySelector("#shJunnengOpenFee").value = item?.open_fee ?? 0;
  document.querySelector("#shJunnengOpenDate").value = item?.open_date || state.shJunnengConfig.default_open_date || today();
  document.querySelector("#shJunnengCurrentPrice").value = item?.current_price ?? "";
  document.querySelector("#shJunnengFormClosePrice").value = item?.close_price ?? "";
  document.querySelector("#shJunnengFormCloseFee").value = item?.close_fee ?? "";
  document.querySelector("#shJunnengFormCloseDate").value = item?.close_date || "";
  toggleShJunnengCloseFields();
  shJunnengDialog.showModal();
}

function toggleShJunnengCloseFields() {
  const isClosed = document.querySelector("#shJunnengIsClosed").value === "已平仓";
  document.querySelectorAll(".sh-junneng-close-field").forEach((field) => {
    field.classList.toggle("hidden", !isClosed);
  });
}

function openShJunnengCloseDialog(item) {
  document.querySelector("#shJunnengCloseTradeId").value = item.id;
  document.querySelector("#shJunnengClosePrice").value = item.current_price ?? item.open_price ?? "";
  document.querySelector("#shJunnengCloseFee").value = item.close_fee ?? 0;
  document.querySelector("#shJunnengCloseDate").value = today();
  shJunnengCloseDialog.showModal();
}

function selectedShJunnengTrade() {
  const item = shJunnengTradeById(state.selectedShJunnengId);
  if (!item) showToast("请先选择一条交易记录");
  return item;
}

function openManualPriceDialog() {
  const currentRows = state.shJunnengSections.current_trades || [];
  const contracts = [...new Set(currentRows.map((item) => item.contract_month))];
  if (!contracts.length) {
    showToast("没有未平仓的合约需要更新价格");
    return;
  }
  manualPriceFields.innerHTML = contracts.map((contract) => `
    <label>
      ${contract} 价格
      <input class="manual-price-input" data-contract="${contract}" type="number" step="0.01" />
    </label>
  `).join("");
  manualPriceDialog.showModal();
}

async function loadSettledOverview() {
  const params = new URLSearchParams();
  const openStart = document.querySelector("#overviewOpenStart").value;
  const openEnd = document.querySelector("#overviewOpenEnd").value;
  const closeStart = document.querySelector("#overviewCloseStart").value;
  const closeEnd = document.querySelector("#overviewCloseEnd").value;
  const contracts = document.querySelector("#overviewContracts").value.trim();
  if (openStart) params.set("open_date_from", openStart);
  if (openEnd) params.set("open_date_to", openEnd);
  if (closeStart) params.set("close_date_from", closeStart);
  if (closeEnd) params.set("close_date_to", closeEnd);
  if (contracts) params.set("contracts", contracts);
  const result = await api(`/api/ledgers/sh-junneng/settled-overview${params.toString() ? `?${params}` : ""}`);
  state.settledOverview = result;
  renderSettledOverview();
}

function renderSettledOverview() {
  const rows = state.settledOverview.trades || [];
  settledOverviewTable.innerHTML = rows.map((item) => `
    <tr>
      <td>${item.contract_month}</td>
      <td>${item.direction_label}</td>
      <td>${money(item.open_price)}</td>
      <td>${money(item.close_price)}</td>
      <td>${item.trade_quantity}</td>
      <td>${money(item.open_fee)}</td>
      <td>${money(item.close_fee)}</td>
      <td class="${pnlClass(item.profit)}">${money(item.profit)}</td>
      <td>${item.open_date}</td>
      <td>${item.close_date}</td>
      <td>${money(item.interest)}</td>
      <td>${money(item.profit_80)}</td>
      <td>${money(item.profit_20)}</td>
    </tr>
  `).join("");
  if (rows.length) settledOverviewTable.innerHTML += shJunnengSummaryRow(state.settledOverview.totals || {}, "settled");
}

async function refreshShJunnengPrices(mock = false) {
  const result = await api(`/api/ledgers/sh-junneng/prices/refresh${mock ? "?mock=true" : ""}`, { method: "POST" });
  await loadShJunneng();
  updateShJunnengStatus(`价格已刷新，更新 ${result.refreshed_contracts || 0} 个合约`);
}

async function exportShJunneng() {
  const headers = {};
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const selectedDate = document.querySelector("#shJunnengSelectedDate").value || today();
  const response = await fetch(`/api/ledgers/sh-junneng/export?selected_date=${encodeURIComponent(selectedDate)}`, { headers });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "导出失败");
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `sh_junneng_trades_${today()}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function directionText(value) {
  return value === "below" ? "低于" : "高于";
}

function statusBadge(status) {
  const text = status === "enabled" ? "启用" : "停用";
  const extra = status === "enabled" ? "" : " disabled";
  return `<span class="badge${extra}">${text}</span>`;
}

function alertDirectionText(value) {
  if (value === "向上突破") return "向上突破";
  if (value === "向下突破") return "向下突破";
  return directionText(value);
}

function updateRiskAlertStatus(message) {
  if (!riskAlertStatus) return;
  riskAlertStatus.textContent = `后台扫描：开启｜最后更新：${new Date().toLocaleTimeString("zh-CN")}｜${message}`;
}

function showToast(message) {
  if (!toastHost) return;
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.textContent = message;
  toastHost.appendChild(toast);
  window.setTimeout(() => toast.remove(), 5000);
}

function alertMessage(item) {
  const contract = `${item.contract_year || ""}${item.contract_month || ""}`;
  return `${item.info_type || "预警"} ${contract} ${alertDirectionText(item.direction)}：当前 ${money(item.current_value)} / 阈值 ${money(item.alert_value)}`;
}

async function loadNotifications(showNewToast = true) {
  if (!state.token) return;
  if (state.alertNotificationInFlight) return;
  state.alertNotificationInFlight = true;
  try {
    const payload = await api("/api/risk-alert/notifications");
    const items = payload.items || [];
    notificationBadge.textContent = String(payload.count || 0);
    notificationBadge.classList.toggle("hidden", !payload.count);
    notificationList.innerHTML = items.length
      ? items.map((item) => `
        <button class="notification-item" data-id="${item.id}">
          <strong>${item.info_type || "-"}</strong>
          <span>${alertMessage(item)}</span>
          <small>${item.alert_time || ""}</small>
        </button>
      `).join("")
      : `<p class="empty-notification">暂无未读预警</p>`;
    notificationList.querySelectorAll(".notification-item").forEach((button) => {
      button.addEventListener("click", async () => {
        await api(`/api/risk-alert/history/${button.dataset.id}/read`, { method: "POST" });
        await loadNotifications(false);
        if (state.activeModule === "risk_alert") await loadRiskAlert();
      });
    });
    if (showNewToast) {
      for (const item of items) {
        if (!state.lastNotificationIds.has(item.id)) showToast(alertMessage(item));
      }
    }
    state.lastNotificationIds = new Set(items.map((item) => item.id));
  } finally {
    state.alertNotificationInFlight = false;
  }
}

function startAlertNotifications() {
  stopAlertNotifications();
  loadNotifications(false).catch(() => {});
  state.alertNotificationTimer = window.setInterval(() => {
    loadNotifications(true).catch(() => {});
  }, 30000);
}

function stopAlertNotifications() {
  if (state.alertNotificationTimer) {
    window.clearInterval(state.alertNotificationTimer);
    state.alertNotificationTimer = null;
  }
  state.alertNotificationInFlight = false;
}

async function loadRiskAlert() {
  const [settings, history] = await Promise.all([
    api("/api/risk-alert/settings"),
    api("/api/risk-alert/history"),
  ]);
  state.alertSettings = settings;
  state.alertHistory = history;
  alertCount.textContent = `${settings.length} 条`;
  historyCount.textContent = `${history.length} 条`;
  alertsTable.innerHTML = settings.map((item) => `
    <tr>
      <td><input class="alert-select" type="checkbox" value="${item.id}" /></td>
      <td>${item.info_type}</td>
      <td>${item.contract_year}-${item.contract_month}</td>
      <td>${item.alert_value}</td>
      <td>${directionText(item.direction)}</td>
      <td>${statusBadge(item.status)}</td>
      <td>${item.reminder_users || "全部"}</td>
      <td>
        <div class="row-actions">
          <button class="link" data-action="edit" data-id="${item.id}">编辑</button>
          <button class="link" data-action="toggle" data-id="${item.id}">${item.status === "enabled" ? "停用" : "启用"}</button>
          <button class="link" data-action="simulate" data-id="${item.id}">模拟</button>
          <button class="link" data-action="delete" data-id="${item.id}">删除</button>
        </div>
      </td>
    </tr>
  `).join("");
  alertsTable.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => handleAlertAction(button.dataset.action, Number(button.dataset.id)));
  });
  historyTable.innerHTML = history.map((item) => `
    <tr>
      <td>${item.alert_time || ""}</td>
      <td>${item.info_type || "-"}</td>
      <td>${money(item.current_value)}</td>
      <td>${money(item.alert_value)}</td>
      <td>${alertDirectionText(item.direction)}</td>
      <td>${item.status === "unread" ? "未读" : "已读"}</td>
      <td>${item.status === "unread" ? `<button class="link" data-id="${item.id}">标记已读</button>` : ""}</td>
    </tr>
  `).join("");
  historyTable.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", async () => {
      await api(`/api/risk-alert/history/${button.dataset.id}/read`, { method: "POST" });
      await loadRiskAlert();
      await loadNotifications(false);
    });
  });
  if (selectAllAlerts) selectAllAlerts.checked = false;
  updateRiskAlertStatus("页面已刷新");
}

function selectedAlertIds() {
  return [...alertsTable.querySelectorAll(".alert-select:checked")].map((input) => Number(input.value));
}

function openAlertDialog(item = null) {
  document.querySelector("#dialogTitle").textContent = item ? "编辑预警" : "新增预警";
  document.querySelector("#alertId").value = item?.id || "";
  document.querySelector("#infoType").value = item?.info_type || "卷螺差";
  document.querySelector("#contractYear").value = item?.contract_year || "2026";
  document.querySelector("#contractMonth").value = item?.contract_month || "09";
  document.querySelector("#alertValue").value = item?.alert_value ?? "";
  document.querySelector("#direction").value = item?.direction || "above";
  document.querySelector("#reminderUsers").value = item?.reminder_users || "";
  document.querySelector("#alertStatus").value = item?.status || "enabled";
  alertDialog.showModal();
}

async function handleAlertAction(action, id) {
  const item = state.alertSettings.find((entry) => entry.id === id);
  if (action === "edit") {
    openAlertDialog(item);
    return;
  }
  if (action === "toggle") {
    await api(`/api/risk-alert/settings/${id}/toggle`, { method: "POST" });
    await loadRiskAlert();
    return;
  }
  if (action === "simulate") {
    const currentValue = Number(item.alert_value) + (item.direction === "above" ? 1 : -1);
    await api(`/api/risk-alert/settings/${id}/simulate-trigger?current_value=${encodeURIComponent(currentValue)}`, { method: "POST" });
    await loadRiskAlert();
    await loadNotifications(true);
    return;
  }
  if (action === "delete") {
    const confirmed = await confirmAction("删除预警", "确认删除该预警规则？");
    if (!confirmed) return;
    await api(`/api/risk-alert/settings/${id}`, { method: "DELETE" });
    await loadRiskAlert();
  }
}

async function updateSelectedAlerts(status) {
  const ids = selectedAlertIds();
  if (!ids.length) {
    showToast("请先选择预警规则");
    return;
  }
  await Promise.all(ids.map((id) => {
    const item = state.alertSettings.find((entry) => entry.id === id);
    if (!item || item.status === status) return Promise.resolve();
    return api(`/api/risk-alert/settings/${id}/toggle`, { method: "POST" });
  }));
  await loadRiskAlert();
}

async function deleteSelectedAlerts() {
  const ids = selectedAlertIds();
  if (!ids.length) {
    showToast("请先选择预警规则");
    return;
  }
  const confirmed = await confirmAction("批量删除预警", `确认删除选中的 ${ids.length} 条预警规则？`);
  if (!confirmed) return;
  await Promise.all(ids.map((id) => api(`/api/risk-alert/settings/${id}`, { method: "DELETE" })));
  await loadRiskAlert();
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  loginError.textContent = "";
  try {
    const payload = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({
        username: document.querySelector("#username").value,
        password: document.querySelector("#password").value,
      }),
    });
    state.token = payload.token;
    localStorage.setItem("token", state.token);
    await bootstrap();
  } catch (error) {
    loginError.textContent = error.message;
  }
});

document.querySelector("#logoutBtn").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" }).catch(() => {});
  localStorage.removeItem("token");
  state.token = "";
  stopAlertNotifications();
  showLogin();
});

document.querySelector("#calculateAllInfoBtn").addEventListener("click", () => calculateAllInfo(false));
document.querySelector("#refreshIndicatorsBtn").addEventListener("click", loadInfoSummary);
refreshInfoCacheBtn.addEventListener("click", refreshInfoCache);
importCacheBtn.addEventListener("click", async () => {
  importCacheBtn.disabled = true;
  updateInfoStatus("正在导入历史缓存");
  try {
    const result = await api("/api/info-summary/cache/import", { method: "POST" });
    state.infoConfig.cache_counts = result.after;
    updateInfoStatus("历史缓存已导入");
  } catch (error) {
    updateInfoStatus(error.message);
  } finally {
    importCacheBtn.disabled = false;
  }
});
document.querySelector("#addGroupBtn").addEventListener("click", openGroupDialog);
document.querySelector("#refreshPricesBtn").addEventListener("click", () => refreshPrices(false));
document.querySelector("#addPositionBtn").addEventListener("click", () => openPositionDialog());
document.querySelector("#cancelGroupBtn").addEventListener("click", () => groupDialog.close());
document.querySelector("#cancelPositionBtn").addEventListener("click", () => positionDialog.close());
document.querySelector("#positionVariety").addEventListener("change", syncPositionOpenPriceStep);
document.querySelector("#addShJunnengBtn").addEventListener("click", () => openShJunnengDialog());
document.querySelector("#editShJunnengBtn").addEventListener("click", () => {
  const item = selectedShJunnengTrade();
  if (item) openShJunnengDialog(item);
});
document.querySelector("#deleteShJunnengBtn").addEventListener("click", async () => {
  const item = selectedShJunnengTrade();
  if (!item) return;
  const confirmed = await confirmAction("删除交易", "确认删除选中的交易记录？");
  if (!confirmed) return;
  await api(`/api/ledgers/sh-junneng/trades/${item.id}`, { method: "DELETE" });
  state.selectedShJunnengId = null;
  await loadShJunneng();
});
document.querySelector("#closeShJunnengBtn").addEventListener("click", () => {
  const item = selectedShJunnengTrade();
  if (!item) return;
  if (item.is_closed) {
    showToast("该交易已经平仓");
    return;
  }
  openShJunnengCloseDialog(item);
});
document.querySelector("#refreshShJunnengPricesBtn").addEventListener("click", () => {
  refreshShJunnengPrices(false).catch((error) => updateShJunnengStatus(error.message));
});
document.querySelector("#manualShJunnengPricesBtn").addEventListener("click", openManualPriceDialog);
document.querySelector("#refreshShJunnengBtn").addEventListener("click", loadShJunneng);
document.querySelector("#queryShJunnengBtn").addEventListener("click", loadShJunneng);
document.querySelector("#resetShJunnengFilterBtn").addEventListener("click", () => {
  document.querySelector("#shJunnengSelectedDate").value = today();
  document.querySelector("#shJunnengFilterKeyword").value = "";
  loadShJunneng();
});
document.querySelector("#settledOverviewBtn").addEventListener("click", async () => {
  await loadSettledOverview();
  settledOverviewDialog.showModal();
});
document.querySelector("#exportShJunnengBtn").addEventListener("click", async () => {
  try {
    await exportShJunneng();
    updateShJunnengStatus("导出完成");
  } catch (error) {
    updateShJunnengStatus(error.message);
  }
});
document.querySelector("#cancelShJunnengBtn").addEventListener("click", () => shJunnengDialog.close());
document.querySelector("#cancelShJunnengCloseBtn").addEventListener("click", () => shJunnengCloseDialog.close());
document.querySelector("#shJunnengIsClosed").addEventListener("change", toggleShJunnengCloseFields);
document.querySelector("#shJunnengSelectedDate").addEventListener("change", () => {
  if (state.activeModule === "sh_junneng") loadShJunneng().catch((error) => updateShJunnengStatus(error.message));
});
document.querySelector("#shJunnengFilterKeyword").addEventListener("input", () => {
  if (state.activeModule === "sh_junneng") loadShJunneng().catch((error) => updateShJunnengStatus(error.message));
});
document.querySelector("#cancelManualPriceBtn").addEventListener("click", () => manualPriceDialog.close());
document.querySelector("#filterSettledOverviewBtn").addEventListener("click", loadSettledOverview);
document.querySelector("#resetSettledOverviewBtn").addEventListener("click", async () => {
  ["#overviewOpenStart", "#overviewOpenEnd", "#overviewCloseStart", "#overviewCloseEnd", "#overviewContracts"].forEach((selector) => {
    document.querySelector(selector).value = "";
  });
  await loadSettledOverview();
});
document.querySelector("#closeSettledOverviewBtn").addEventListener("click", () => settledOverviewDialog.close());

groupForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const id = document.querySelector("#groupId").value;
  const payload = { group_name: document.querySelector("#groupName").value };
  try {
    const result = await api(id ? `/api/mid-event/groups/${id}` : "/api/mid-event/groups", {
      method: id ? "PUT" : "POST",
      body: JSON.stringify(payload),
    });
    const preferredGroupId = !id && result.id ? result.id : null;
    if (preferredGroupId) {
      state.selectedGroupId = preferredGroupId;
      state.selectedGroupName = payload.group_name;
    }
    groupDialog.close();
    await loadMidEvent(preferredGroupId);
  } catch (error) {
    updateMidEventStatus(`保存策略组失败：${error.message}`);
    window.alert(`保存策略组失败：${error.message}`);
  }
});

positionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const id = document.querySelector("#positionId").value;
  const payload = {
    variety: document.querySelector("#positionVariety").value,
    contract: document.querySelector("#positionContract").value,
    direction: document.querySelector("#positionDirection").value,
    open_price: Number(document.querySelector("#positionOpenPrice").value),
    quantity: Number(document.querySelector("#positionQuantity").value),
  };
  try {
    await api(id ? `/api/mid-event/positions/${id}` : `/api/mid-event/groups/${state.selectedGroupId}/positions`, {
      method: id ? "PUT" : "POST",
      body: JSON.stringify(payload),
    });
    positionDialog.close();
    await refreshPrices(false);
  } catch (error) {
    updateMidEventStatus(`保存持仓失败：${error.message}`);
    window.alert(`保存持仓失败：${error.message}`);
  }
});

shJunnengForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const id = document.querySelector("#shJunnengTradeId").value;
  const isClosed = document.querySelector("#shJunnengIsClosed").value;
  const payload = {
    contract_month: document.querySelector("#shJunnengContractMonth").value,
    direction: document.querySelector("#shJunnengDirection").value,
    open_price: Number(document.querySelector("#shJunnengOpenPrice").value),
    trade_quantity: Number(document.querySelector("#shJunnengTradeQuantity").value),
    open_fee: Number(document.querySelector("#shJunnengOpenFee").value || 0),
    open_date: document.querySelector("#shJunnengOpenDate").value,
    current_price: document.querySelector("#shJunnengCurrentPrice").value ? Number(document.querySelector("#shJunnengCurrentPrice").value) : null,
    close_price: isClosed === "已平仓" && document.querySelector("#shJunnengFormClosePrice").value ? Number(document.querySelector("#shJunnengFormClosePrice").value) : null,
    close_fee: isClosed === "已平仓" && document.querySelector("#shJunnengFormCloseFee").value ? Number(document.querySelector("#shJunnengFormCloseFee").value) : null,
    close_date: isClosed === "已平仓" ? document.querySelector("#shJunnengFormCloseDate").value : null,
    is_closed: isClosed,
  };
  await api(id ? `/api/ledgers/sh-junneng/trades/${id}` : "/api/ledgers/sh-junneng/trades", {
    method: id ? "PUT" : "POST",
    body: JSON.stringify(payload),
  });
  shJunnengDialog.close();
  await refreshShJunnengPrices(false);
});

shJunnengCloseForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const id = document.querySelector("#shJunnengCloseTradeId").value;
  const payload = {
    close_price: Number(document.querySelector("#shJunnengClosePrice").value),
    close_fee: Number(document.querySelector("#shJunnengCloseFee").value || 0),
    close_date: document.querySelector("#shJunnengCloseDate").value,
  };
  await api(`/api/ledgers/sh-junneng/trades/${id}/close`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  shJunnengCloseDialog.close();
  await loadShJunneng();
});

manualPriceForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const prices = {};
  manualPriceFields.querySelectorAll(".manual-price-input").forEach((input) => {
    if (input.value) prices[input.dataset.contract] = Number(input.value);
  });
  if (!Object.keys(prices).length) {
    showToast("请至少填写一个合约价格");
    return;
  }
  const result = await api("/api/ledgers/sh-junneng/prices/manual", {
    method: "POST",
    body: JSON.stringify({ prices }),
  });
  manualPriceDialog.close();
  await loadShJunneng();
  updateShJunnengStatus(`手动更新价格完成，更新 ${result.updated || 0} 条`);
});

notificationBtn.addEventListener("click", () => {
  notificationPanel.classList.toggle("hidden");
});
markAllNotificationsBtn.addEventListener("click", async () => {
  await api("/api/risk-alert/history/read-all", { method: "POST" });
  await loadNotifications(false);
  if (state.activeModule === "risk_alert") await loadRiskAlert();
});
document.querySelector("#addAlertBtn").addEventListener("click", () => openAlertDialog());
document.querySelector("#refreshAlertsBtn").addEventListener("click", loadRiskAlert);
document.querySelector("#scanAlertsBtn").addEventListener("click", async () => {
  const result = await api("/api/risk-alert/scan", { method: "POST" });
  await loadRiskAlert();
  await loadNotifications(true);
  updateRiskAlertStatus(`手动扫描完成：检查 ${result.checked} 条，触发 ${result.triggered} 条`);
});
document.querySelector("#batchEnableAlertsBtn").addEventListener("click", () => updateSelectedAlerts("enabled"));
document.querySelector("#batchDisableAlertsBtn").addEventListener("click", () => updateSelectedAlerts("disabled"));
document.querySelector("#batchDeleteAlertsBtn").addEventListener("click", deleteSelectedAlerts);
selectAllAlerts.addEventListener("change", () => {
  alertsTable.querySelectorAll(".alert-select").forEach((input) => {
    input.checked = selectAllAlerts.checked;
  });
});
document.querySelector("#cancelDialogBtn").addEventListener("click", () => alertDialog.close());
alertForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const id = document.querySelector("#alertId").value;
  const payload = {
    info_type: document.querySelector("#infoType").value,
    contract_year: document.querySelector("#contractYear").value,
    contract_month: document.querySelector("#contractMonth").value,
    alert_value: Number(document.querySelector("#alertValue").value),
    direction: document.querySelector("#direction").value,
    status: document.querySelector("#alertStatus").value,
    reminder_users: document.querySelector("#reminderUsers").value,
  };
  await api(id ? `/api/risk-alert/settings/${id}` : "/api/risk-alert/settings", {
    method: id ? "PUT" : "POST",
    body: JSON.stringify(payload),
  });
  alertDialog.close();
  await loadRiskAlert();
});

bootstrap();

// ── 用户管理 ────────────────────────────────────────────

let selectedUserId = null;

async function loadUserManagement() {
  try {
    const data = await api("/api/users");
    renderUsersTable(data.users);
    userMgmtStatus.textContent = `已加载 ${data.users.length} 个用户`;
  } catch (error) {
    userMgmtStatus.textContent = `加载失败: ${error.message}`;
  }
}

function renderUsersTable(users) {
  userCount.textContent = `共 ${users.length} 人`;
  usersTable.innerHTML = users.map((u) => `<tr data-user-id="${u.id}">
    <td>${u.id}</td>
    <td>${u.name}</td>
    <td>${u.department}</td>
    <td>${u.role}</td>
    <td>${u.status || "启用"}</td>
    <td>${u.created_at || ""}</td>
  </tr>`).join("");

  usersTable.querySelectorAll("tr").forEach((row) => {
    row.addEventListener("click", () => {
      usersTable.querySelectorAll("tr").forEach((r) => r.classList.remove("selected-row"));
      row.classList.add("selected-row");
      selectedUserId = parseInt(row.dataset.userId);
    });
  });
}

// 添加用户
function openUserDialog(editMode = false) {
  userId.value = "";
  userName.value = "";
  userDepartment.value = "";
  userPassword.value = "";
  userRole.value = "用户";
  userDialogTitle.textContent = editMode ? "编辑用户" : "添加用户";
  if (!editMode) {
    userPassword.required = true;
    userPassword.placeholder = "请输入密码（至少6位）";
  } else {
    userPassword.required = false;
    userPassword.placeholder = "留空则不修改密码";
  }
  userDialog.showModal();
}

async function openEditUserDialog() {
  if (!selectedUserId) {
    alert("请先选择要编辑的用户");
    return;
  }
  try {
    const data = await api("/api/users");
    const target = data.users.find((u) => u.id === selectedUserId);
    if (!target) {
      alert("用户不存在");
      return;
    }
    userId.value = target.id;
    userName.value = target.name;
    userDepartment.value = target.department;
    userPassword.value = "";
    userPassword.required = false;
    userPassword.placeholder = "留空则不修改密码";
    userRole.value = target.role;
    userDialogTitle.textContent = "编辑用户";
    userDialog.showModal();
  } catch (error) {
    alert(`获取用户信息失败: ${error.message}`);
  }
}

async function saveUser() {
  const id = userId.value;
  const payload = {
    name: userName.value.trim(),
    department: userDepartment.value.trim(),
    password: userPassword.value,
    role: userRole.value,
  };
  if (!payload.name || !payload.department) {
    alert("请填写所有必填字段");
    return;
  }
  if (!id && payload.password.length < 6) {
    alert("密码长度至少为6位");
    return;
  }
  try {
    if (id) {
      await api(`/api/users/${id}`, { method: "PUT", body: JSON.stringify(payload) });
    } else {
      await api("/api/users", { method: "POST", body: JSON.stringify(payload) });
    }
    userDialog.close();
    await loadUserManagement();
  } catch (error) {
    alert(`保存失败: ${error.message}`);
  }
}

async function deleteSelectedUser() {
  if (!selectedUserId) {
    alert("请先选择要删除的用户");
    return;
  }
  if (!confirm(`确定要删除该用户吗？`)) return;
  try {
    await api(`/api/users/${selectedUserId}`, { method: "DELETE" });
    selectedUserId = null;
    await loadUserManagement();
  } catch (error) {
    alert(`删除失败: ${error.message}`);
  }
}

// 权限设置
async function openPermissionDialog() {
  if (!selectedUserId) {
    alert("请先选择用户");
    return;
  }
  try {
    const data = await api(`/api/users/${selectedUserId}/permissions`);
    const perms = data.permissions;
    permissionTable.innerHTML = Object.entries(perms).map(([code, p]) => {
      const label = moduleLabel(code);
      return `<tr data-module="${code}">
        <td>${label ? label.name : code}</td>
        <td><input type="checkbox" class="perm-view" ${p.can_view ? "checked" : ""} /></td>
        <td><input type="checkbox" class="perm-edit" ${p.can_edit ? "checked" : ""} /></td>
      </tr>`;
    }).join("");
    permissionDialogTitle.textContent = "权限设置";
    permissionDialog.showModal();
  } catch (error) {
    alert(`获取权限失败: ${error.message}`);
  }
}

async function savePermissions() {
  const rows = permissionTable.querySelectorAll("tr");
  const permissions = [];
  rows.forEach((row) => {
    const code = row.dataset.module;
    const view = row.querySelector(".perm-view").checked ? 1 : 0;
    const edit = row.querySelector(".perm-edit").checked ? 1 : 0;
    permissions.push({ module_code: code, can_view: view, can_edit: edit });
  });
  try {
    await api(`/api/users/${selectedUserId}/permissions`, {
      method: "PUT",
      body: JSON.stringify({ permissions }),
    });
    permissionDialog.close();
    alert("权限保存成功");
  } catch (error) {
    alert(`保存失败: ${error.message}`);
  }
}

// 操作日志
async function openLogsDialog() {
  operationLogsDialog.showModal();
  await loadLogs();
}

async function loadLogs() {
  try {
    const params = new URLSearchParams();
    const opType = logsOpType.value;
    const userName = logsUserName.value.trim();
    if (opType) params.append("operation_type", opType);
    if (userName) params.append("user_name", userName);
    const data = await api(`/api/operation-logs?${params.toString()}`);
    logsTable.innerHTML = data.logs.map((l) => `<tr>
      <td>${l.id}</td>
      <td>${l.user_name || ""}</td>
      <td>${l.operation_type || ""}</td>
      <td>${l.description || ""}</td>
      <td>${l.module_code || ""}</td>
      <td>${l.created_at || ""}</td>
    </tr>`).join("");
  } catch (error) {
    logsTable.innerHTML = `<tr><td colspan="6" style="color:red">加载失败: ${error.message}</td></tr>`;
  }
}

function exportLogs() {
  const rows = logsTable.querySelectorAll("tr");
  let csv = "\uFEFFID,用户,操作类型,描述,模块,时间\n";
  rows.forEach((row) => {
    const cells = row.querySelectorAll("td");
    if (cells.length >= 6) {
      csv += Array.from(cells).map((c) => `"${c.textContent.replace(/"/g, '""')}"`).join(",") + "\n";
    }
  });
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `operation_logs_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

// 事件绑定
addUserBtn.addEventListener("click", () => openUserDialog(false));
editUserBtn.addEventListener("click", openEditUserDialog);
deleteUserBtn.addEventListener("click", deleteSelectedUser);
setPermissionBtn.addEventListener("click", openPermissionDialog);
viewLogsBtn.addEventListener("click", openLogsDialog);

userForm.addEventListener("submit", (e) => {
  e.preventDefault();
  saveUser();
});
cancelUserBtn.addEventListener("click", () => userDialog.close());

permissionForm.addEventListener("submit", (e) => {
  e.preventDefault();
  savePermissions();
});
cancelPermissionBtn.addEventListener("click", () => permissionDialog.close());

searchLogsBtn.addEventListener("click", loadLogs);
exportLogsBtn.addEventListener("click", exportLogs);
closeLogsBtn.addEventListener("click", () => operationLogsDialog.close());

// ═══════════════════════════════════════════════════════════════
// 数据可视化管理
// ═══════════════════════════════════════════════════════════════

let dvIntegrationUploadFiles = [];

let dvState = {
  currentMetric: "inventory",
  chartMetric: "inventory",
  selectedYears: [],
  uploadFile: null,
  uploadFileName: "",
  previewData: null,
  dvDataFilterInitialized: false,
  dvChartControlsInitialized: false,
  highlightedYear: null,
  lastChartData: null,
  chartFilters: null,
  dataFilters: null,
  highlightedLineKey: null,
};

const DV_PAGE_SIZE = 50;
const DV_CHART_PRODUCT_POOL_LABELS = {
  mainstream: "主流矿",
  non_mainstream: "非主流矿",
  aggregate: "整体对比",
  custom: "自定义",
};

async function loadDVIntegrationLatest() {
  try {
    const result = await api("/api/data-visualization/integration/latest");
    if (result.batch && result.batch.id) {
      dvIntegrationBatchInfo.textContent = "批次 " + result.batch.id + "｜" + (result.batch.created_at || "");
      dvIntegrationStatus.textContent = "已读取最近整合结果";
      const metrics = {};
      (result.metrics || []).forEach(function(row) { metrics[row.metric_type] = row.c; });
      renderDVIntegrationSummary({
        total_points: result.batch.point_count || 0,
        metrics: metrics,
        product_count: "-",
        week_count: "-",
        warnings: [],
        samples: [],
      }, []);
    } else {
      dvIntegrationBatchInfo.textContent = "";
      dvIntegrationSummary.innerHTML = '<div class="empty-cell">暂无整合结果</div>';
      dvIntegrationSamples.innerHTML = '<tr><td colspan="9" class="empty-cell">暂无样例数据</td></tr>';
      dvIntegrationSampleCount.textContent = "";
      dvIntegrationStatus.textContent = "待整合";
    }
  } catch (error) {
    dvIntegrationStatus.textContent = error.message;
  }
}

function renderDVIntegrationSummary(summary, files) {
  const metrics = summary.metrics || {};
  const items = [
    ["文件数", files.length || "-"],
    ["标准数据点", summary.total_points || 0],
    ["库存", metrics.inventory || 0],
    ["发运/到港", metrics.shipment || 0],
    ["表需", metrics.apparent_demand || 0],
    ["品种数", summary.product_count || "-"],
    ["周数", summary.week_count || "-"],
  ];
  dvIntegrationSummary.innerHTML = items.map(function(item) {
    return '<div class="dv-summary-item"><span>' + item[0] + '</span><strong>' + item[1] + '</strong></div>';
  }).join("");
  if (summary.warnings && summary.warnings.length) {
    dvIntegrationSummary.innerHTML += '<div class="error-cell">' + summary.warnings.join("；") + '</div>';
  }
  const samples = summary.samples || [];
  dvIntegrationSampleCount.textContent = samples.length ? "展示前 " + samples.length + " 条" : "";
  dvIntegrationSamples.innerHTML = samples.length ? samples.map(function(row) {
    return '<tr>' +
      '<td>' + row.week_start + '</td>' +
      '<td>' + (row.week_label || "") + '</td>' +
      '<td>' + metricLabel(row.metric_type) + '</td>' +
      '<td>' + row.source_country + '</td>' +
      '<td>' + row.product + '</td>' +
      '<td>' + row.category + '</td>' +
      '<td>' + row.mainstream_status + '</td>' +
      '<td>' + formatNumber(row.value) + '</td>' +
      '<td>' + row.source_sheet + ' / ' + row.source_section + '</td>' +
      '</tr>';
  }).join("") : '<tr><td colspan="9" class="empty-cell">暂无样例数据</td></tr>';
}

function metricLabel(metric) {
  if (metric === "inventory") return "库存";
  if (metric === "shipment") return "发运";
  if (metric === "arrival") return "到港";
  if (metric === "apparent_demand") return "表需";
  return metric;
}

async function fileToIntegrationPayload(file) {
  const base64 = await new Promise(function(resolve, reject) {
    const reader = new FileReader();
    reader.onload = function() {
      const result = reader.result || "";
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.substring(comma + 1) : result);
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
  return { file_name: file.name, file_data: base64 };
}

async function previewLocalIntegration() {
  dvIntegrationStatus.textContent = "正在读取本地模板...";
  const result = await api("/api/data-visualization/integration/local-preview");
  renderDVIntegrationSummary(result.summary, result.files || []);
  dvIntegrationStatus.textContent = "本地模板预览完成";
}

async function commitLocalIntegration() {
  dvIntegrationStatus.textContent = "正在写入本地整合结果...";
  const result = await api("/api/data-visualization/integration/local-commit", { method: "POST" });
  renderDVIntegrationSummary(result.summary, result.files || []);
  dvIntegrationBatchInfo.textContent = "批次 " + result.batch_id;
  dvIntegrationStatus.textContent = "本地整合结果已写入";
  dvState.dvDataFilterInitialized = false;
  dvState.dvChartControlsInitialized = false;
}

async function previewUploadedIntegration() {
  const files = Array.from(dvIntegrationFiles.files || []);
  if (!files.length) return;
  dvIntegrationStatus.textContent = "正在解析上传文件...";
  dvIntegrationUploadFiles = [];
  for (const file of files) {
    dvIntegrationUploadFiles.push(await fileToIntegrationPayload(file));
  }
  const result = await api("/api/data-visualization/integration/preview", {
    method: "POST",
    body: JSON.stringify({ files: dvIntegrationUploadFiles }),
  });
  renderDVIntegrationSummary(result.summary, result.files || []);
  dvUploadCommitBtn.disabled = false;
  dvIntegrationStatus.textContent = "上传文件预览完成";
}

async function commitUploadedIntegration() {
  if (!dvIntegrationUploadFiles.length) return;
  dvIntegrationStatus.textContent = "正在写入上传整合结果...";
  const result = await api("/api/data-visualization/integration/commit", {
    method: "POST",
    body: JSON.stringify({ files: dvIntegrationUploadFiles }),
  });
  renderDVIntegrationSummary(result.summary, result.files || []);
  dvIntegrationBatchInfo.textContent = "批次 " + result.batch_id;
  dvIntegrationStatus.textContent = "上传整合结果已写入";
  dvUploadCommitBtn.disabled = true;
  dvState.dvDataFilterInitialized = false;
  dvState.dvChartControlsInitialized = false;
}

async function exportIntegratedExcel() {
  dvIntegrationStatus.textContent = "正在生成整合 Excel...";
  const headers = {};
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch("/api/data-visualization/integration/export", { headers });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "下载失败");
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `iron_ore_integrated_${today()}.xlsx`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  dvIntegrationStatus.textContent = "整合 Excel 已下载";
}

dvLocalPreviewBtn.addEventListener("click", function() {
  previewLocalIntegration().catch(function(error) { dvIntegrationStatus.textContent = error.message; });
});

dvLocalCommitBtn.addEventListener("click", function() {
  commitLocalIntegration().catch(function(error) { dvIntegrationStatus.textContent = error.message; });
});

dvIntegrationFiles.addEventListener("change", function() {
  previewUploadedIntegration().catch(function(error) {
    dvUploadCommitBtn.disabled = true;
    dvIntegrationStatus.textContent = error.message;
  });
});

dvUploadCommitBtn.addEventListener("click", function() {
  commitUploadedIntegration().catch(function(error) { dvIntegrationStatus.textContent = error.message; });
});

dvExportBtn.addEventListener("click", function() {
  exportIntegratedExcel().catch(function(error) { dvIntegrationStatus.textContent = error.message; });
});

// ── Helpers ────────────────────────────────────────────────────────────
function getCheckedValues(container) {
  var checked = container.querySelectorAll('input[type="checkbox"]:checked');
  return Array.from(checked).map(function(cb) { return cb.value; });
}

function appendMultiSelectParam(url, paramName, selectedValues, totalCount) {
  if (totalCount === 0 || selectedValues.length === totalCount) return url;
  if (selectedValues.length === 0) {
    return url + "&" + paramName + "=__EMPTY__";
  }
  return url + "&" + paramName + "=" + encodeURIComponent(selectedValues.join(","));
}

function selectAllCheckboxes(container) {
  container.querySelectorAll('input[type="checkbox"]').forEach(function(cb) { cb.checked = true; });
}
function selectNoneCheckboxes(container) {
  container.querySelectorAll('input[type="checkbox"]').forEach(function(cb) { cb.checked = false; });
}

async function buildYearCheckboxes(container, onChange) {
  try {
    var result = await api("/api/data-visualization/years");
    var years = result.years || [];
    container.innerHTML = "";
    years.forEach(function(y) {
      var label = document.createElement("label");
      label.className = "dv-checkbox-label";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.value = String(y);
      cb.checked = true;
      cb.addEventListener("change", onChange);
      label.appendChild(cb);
      label.appendChild(document.createTextNode(String(y)));
      container.appendChild(label);
    });
  } catch (err) {
    console.error("加载年份列表失败:", err);
  }
}

function buildCheckboxes(container, items, onChange, checkedDefault) {
  container.innerHTML = "";
  items.forEach(function(item) {
    var label = document.createElement("label");
    label.className = "dv-checkbox-label";
    var cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = item;
    cb.checked = !!checkedDefault;
    cb.addEventListener("change", onChange);
    label.appendChild(cb);
    label.appendChild(document.createTextNode(item));
    container.appendChild(label);
  });
}

function initDVData() {
  if (dvState.currentMetric !== "inventory") {
    dvState.currentMetric = "inventory";
  }
  dvDataTabs.querySelectorAll(".dv-tab").forEach(function(t) { t.classList.remove("active"); });
  var inventoryTab = dvDataTabs.querySelector('[data-metric="inventory"]');
  if (inventoryTab) inventoryTab.classList.add("active");

  if (!dvState.dvDataFilterInitialized) {
    loadDVDataFilters();
  }
}

function loadDVDataFilters() {
  dvState.dvDataFilterInitialized = true;
  api("/api/data-visualization/filters").then(function(filters) {
    dvState.dataFilters = filters;
    buildYearCheckboxes(dvDataYearCheckboxes, function() { loadDVTable(dvState.currentMetric); });
    applyDVDataProductPool();
    buildCheckboxes(dvDataCategoryCheckboxes, filters.categories || [], function() { loadDVTable(dvState.currentMetric); }, true);
    buildCheckboxes(dvDataCountryCheckboxes, filters.source_countries || [], function() { loadDVTable(dvState.currentMetric); }, true);
    buildCheckboxes(dvDataMainstreamCheckboxes, filters.mainstream_statuses || [], function() { loadDVTable(dvState.currentMetric); }, true);

    if (dvDataProductPool) {
      dvDataProductPool.onchange = function() {
        applyDVDataProductPool();
        loadDVTable(dvState.currentMetric);
      };
    }
    dvDataProductAll.onclick = function() {
      selectAllCheckboxes(dvDataProductCheckboxes);
      loadDVTable(dvState.currentMetric);
    };
    dvDataProductNone.onclick = function() {
      selectNoneCheckboxes(dvDataProductCheckboxes);
      loadDVTable(dvState.currentMetric);
    };

    dvDataYearAll.onclick = function() {
      selectAllCheckboxes(dvDataYearCheckboxes);
      loadDVTable(dvState.currentMetric);
    };
    dvDataYearNone.onclick = function() {
      selectNoneCheckboxes(dvDataYearCheckboxes);
      loadDVTable(dvState.currentMetric);
    };
  }).catch(function(err) {
    console.error("加载筛选选项失败:", err);
  });
}

function applyDVDataProductPool() {
  var filters = dvState.dataFilters || {};
  var pools = filters.product_pools || {};
  var pool = dvDataProductPool ? dvDataProductPool.value : "mainstream";
  var items = [];
  if (pool === "mainstream") items = pools.mainstream || filters.products || [];
  else if (pool === "non_mainstream") items = pools.non_mainstream || [];
  else if (pool === "aggregate") items = pools.aggregate || ["主流矿合计", "非主流矿合计"];
  else items = pools.custom || filters.products || [];

  buildCheckboxes(dvDataProductCheckboxes, items, function() { loadDVTable(dvState.currentMetric); }, true);
  if (pool === "aggregate") {
    dvDataProductAll.disabled = true;
    dvDataProductNone.disabled = true;
  } else {
    dvDataProductAll.disabled = false;
    dvDataProductNone.disabled = false;
  }
}



// ── Tabs ──────────────────────────────────────────────────────────────
dvDataTabs.addEventListener("click", function(e) {
  var tab = e.target.closest(".dv-tab");
  if (!tab) return;
  dvDataTabs.querySelectorAll(".dv-tab").forEach(function(t) { t.classList.remove("active"); });
  tab.classList.add("active");
  dvState.currentMetric = tab.dataset.metric;
  loadDVTable(dvState.currentMetric);
});

dvChartTabs.addEventListener("click", function(e) {
  var tab = e.target.closest(".dv-tab");
  if (!tab) return;
  dvChartTabs.querySelectorAll(".dv-tab").forEach(function(t) { t.classList.remove("active"); });
  tab.classList.add("active");
  dvState.chartMetric = tab.dataset.metric;
  loadDVChart();
});

// ── Table loading ─────────────────────────────────────────────────────
async function loadDVTable(metric) {
  try {
    var yearsArr = getCheckedValues(dvDataYearCheckboxes);
    var productsArr = getCheckedValues(dvDataProductCheckboxes);
    var categoriesArr = getCheckedValues(dvDataCategoryCheckboxes);
    var countriesArr = getCheckedValues(dvDataCountryCheckboxes);
    var mainstreamArr = getCheckedValues(dvDataMainstreamCheckboxes);
    var productPool = dvDataProductPool ? dvDataProductPool.value : "mainstream";
    var url = "/api/data-visualization/table?metric=" + encodeURIComponent(metric);
    url = appendMultiSelectParam(url, "years", yearsArr, dvDataYearCheckboxes.querySelectorAll('input[type="checkbox"]').length);
    if (productPool === "aggregate") {
      url += "&product_pool=aggregate";
    } else {
      url += "&product_pool=" + encodeURIComponent(productPool);
      if (productPool === "custom") {
        url = appendMultiSelectParam(url, "products", productsArr, dvDataProductCheckboxes.querySelectorAll('input[type="checkbox"]').length);
      } else if (productsArr.length === 0) {
        url += "&products=__EMPTY__";
      } else {
        url += "&products=" + encodeURIComponent(productsArr.join(","));
      }
    }
    url = appendMultiSelectParam(url, "categories", categoriesArr, dvDataCategoryCheckboxes.querySelectorAll('input[type="checkbox"]').length);
    url = appendMultiSelectParam(url, "source_countries", countriesArr, dvDataCountryCheckboxes.querySelectorAll('input[type="checkbox"]').length);
    url = appendMultiSelectParam(url, "mainstream_status", mainstreamArr, dvDataMainstreamCheckboxes.querySelectorAll('input[type="checkbox"]').length);
    var result = await api(url);
    renderDVTable(result);
  } catch (err) {
    dvDataTbody.innerHTML = '<tr><td colspan="14" class="error-cell">加载失败: ' + err.message + '</td></tr>';
  }
}

function renderDVTable(result) {
  var data = result.data || [];
  var products = result.products || [];
  var productCount = products.length;

  if (!data.length) {
    dvDataTbody.innerHTML = '<tr><td colspan="' + (2 + productCount) + '" class="empty-cell">暂无数据，请先导入</td></tr>';
    return;
  }

  var thead = document.querySelector('#dvDataTable thead');
  if (thead) {
    thead.innerHTML = '<tr><th>日期</th><th>周次</th>' +
      products.map(function(p) { return '<th>' + p + '</th>'; }).join('') +
      '</tr>';
  }

  dvDataTbody.innerHTML = data
    .map(function(row) {
      var cells = '';
      for (var pi = 0; pi < products.length; pi++) {
        var p = products[pi];
        var pd = row[p] || {};
        var val = pd.value;
        var id = pd.id || '';
        var cls = 'dv-value-cell';
        if (pd.is_manual_override) cls += ' manual-override';
        if (pd.is_missing_filled) cls += ' missing-filled';
        var title = tooltipText(pd);
        cells += '<td class="' + cls + '" data-id="' + id + '" data-value="' + (val != null ? val : '') + '" title="' + title + '">' +
          (val != null ? formatNumber(val) : '-') + '</td>';
      }
      return '<tr><td>' + formatDateOnly(row.date) + '</td><td>' + row.week + '</td>' + cells + '</tr>';
    })
    .join('');

  attachInlineEdit();
}

function tooltipText(row) {
  var parts = [];
  if (row.is_manual_override) parts.push("人工修正");
  if (row.is_missing_filled) parts.push("缺失补0");
  if (row.source) parts.push("来源: " + row.source);
  if (row.updated_by) parts.push("修改人: " + row.updated_by);
  return parts.join(" | ");
}

function formatNumber(val) {
  if (val == null) return "-";
  if (Number.isInteger(val)) return val.toString();
  return val.toFixed(2);
}

function formatChartNumber(val) {
  if (val == null) return "-";
  if (Number.isInteger(val)) return val.toString();
  return Math.round(val).toString();
}

// ── Inline edit ────────────────────────────────────────────────────────
function attachInlineEdit() {
  dvDataTbody.querySelectorAll(".dv-value-cell").forEach(function(cell) {
    if (!cell.dataset.id) return;
    cell.addEventListener("dblclick", function() { startInlineEdit(cell); });
  });
}

function startInlineEdit(cell) {
  var tr = cell.closest("tr");
  var id = parseInt(cell.dataset.id);
  var current = cell.dataset.value;
  var input = document.createElement("input");
  input.type = "number";
  input.step = "any";
  input.value = current;
  input.className = "dv-inline-input";
  cell.textContent = "";
  cell.appendChild(input);
  input.focus();
  input.select();

  var save = async function() {
    var val = parseFloat(input.value);
    if (isNaN(val)) {
      cell.textContent = current || "-";
      return;
    }
    try {
      await api("/api/data-visualization/value", {
        method: "PUT",
        body: JSON.stringify({ data_point_id: id, new_value: val }),
      });
      loadDVTable(dvState.currentMetric);
    } catch (err) {
      cell.textContent = current || "-";
      alert("保存失败: " + err.message);
    }
  };

  input.addEventListener("blur", save);
  input.addEventListener("keydown", function(e) {
    if (e.key === "Enter") { input.blur(); }
    if (e.key === "Escape") { cell.textContent = current || "-"; }
  });
}

// ── Excel import ───────────────────────────────────────────────────────
dvImportBtn.addEventListener("click", function() { dvImportDialog.showModal(); });
dvCancelImportBtn.addEventListener("click", function() {
  dvImportFile.value = '';
  dvState.previewData = null;
  dvState.uploadFile = null;
  dvPreviewContent.innerHTML = '';
  dvCommitImportBtn.disabled = true;
  dvImportDialog.close();
});

dvImportDialog.addEventListener("close", function() {
  dvImportFile.value = '';
  dvState.previewData = null;
  dvState.uploadFile = null;
  dvPreviewContent.innerHTML = '';
  dvCommitImportBtn.disabled = true;
});

dvImportFile.addEventListener("change", async function() {
  var file = dvImportFile.files[0];
  if (!file) return;

  dvState.uploadFileName = file.name;
  dvPreviewContent.innerHTML = '<div class="dv-chart-status">正在解析文件...</div>';

  try {
    // Read file as base64 to match backend ImportRequest model
    var base64 = await new Promise(function(resolve, reject) {
      var reader = new FileReader();
      reader.onload = function() {
        var result = reader.result;
        var comma = result.indexOf(",");
        resolve(comma >= 0 ? result.substring(comma + 1) : result);
      };
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
    var headers = {};
    if (state.token) headers.Authorization = "Bearer " + state.token;
    headers["Content-Type"] = "application/json";

    var response = await fetch("/api/data-visualization/import/integrated/preview", {
      method: "POST",
      headers: headers,
      body: JSON.stringify({ file_data: base64, file_name: file.name }),
    });

    if (!response.ok) {
      var errPayload = await response.json().catch(function() { return {}; });
      throw new Error(errPayload.detail || "预览失败");
    }

    var preview = await response.json();
    dvState.previewData = preview;
    dvState.uploadFile = null;

    var summary = preview.summary || {};
    var errors = preview.errors || [];
    var html = '<div class="dv-preview-stats">';
    html += '<div>文件: ' + file.name + '</div>';
    html += '<div>数据点总数: ' + (summary.total_points || 0) + '</div>';
    html += '<div>库存: ' + (summary.inventory_count || 0) + ' | 发运: ' + (summary.shipment_count || 0) + ' | 到港: ' + (summary.arrival_count || 0) + ' | 表需: ' + (summary.apparent_demand_count || 0) + '</div>';
    html += '<div>品种数: ' + (summary.product_count || 0) + ' | 种类数: ' + (summary.category_count || 0) + ' | 来源/国家数: ' + (summary.country_count || 0) + '</div>';
    html += '<div>周数: ' + (summary.week_count || 0) + ' | 空值数: ' + (summary.null_count || 0) + '</div>';
    html += '<div>重复业务 key 数: ' + (summary.duplicate_key_count || 0) + '</div>';
    html += '<div>日期范围: ' + (summary.date_min || "-") + ' ~ ' + (summary.date_max || "-") + '</div>';
    if (errors.length) {
      html += '<div style="color:#dc2626;margin-top:6px;">错误详情 (' + errors.length + ' 条):</div>';
      errors.slice(0, 15).forEach(function(err) {
        html += '<div style="color:#dc2626;font-size:12px;">行' + err.row + ': ' + err.message + '</div>';
      });
      if (errors.length > 15) {
        html += '<div style="color:#dc2626;font-size:12px;">... 还有 ' + (errors.length - 15) + ' 条错误</div>';
      }
    }
    html += '</div>';
    dvPreviewContent.innerHTML = html;
    dvCommitImportBtn.disabled = (summary.total_points || 0) === 0;
  } catch (err) {
    dvPreviewContent.innerHTML = '<div class="error-cell">解析失败: ' + err.message + '</div>';
    dvCommitImportBtn.disabled = true;
  }
});

dvCommitImportBtn.addEventListener("click", async function() {
  if (!dvState.previewData) return;

  dvCommitImportBtn.disabled = true;
  dvCommitImportBtn.textContent = "导入中，请稍候...";
  dvPreviewContent.innerHTML = '<div class="dv-chart-status">导入中，请稍候，数据较多时可能需要 1-2 分钟...</div>';
  var controller = new AbortController();
  var timeoutId = setTimeout(function() { controller.abort(); }, 180000);

  try {
    await api("/api/data-visualization/import/integrated/commit", {
      method: "POST",
      signal: controller.signal,
      body: JSON.stringify({
        file_name: dvState.uploadFileName,
        preview_id: dvState.previewData.preview_id,
      }),
    });
    dvState.previewData = null;
    dvState.uploadFile = null;
    dvImportFile.value = '';
    dvState.dvDataFilterInitialized = false;
    dvState.dvChartControlsInitialized = false;
    loadDVTable(dvState.currentMetric);
    loadDVDataFilters();
    dvImportDialog.close();
  } catch (err) {
    if (err.name === "AbortError") {
      dvPreviewContent.innerHTML = '<div class="error-cell">导入超时，请稍后刷新数据管理页面确认是否已写入，或重新导入。</div>';
    } else {
      dvPreviewContent.innerHTML = '<div class="error-cell">导入失败: ' + err.message + '</div>';
    }
  } finally {
    clearTimeout(timeoutId);
    dvCommitImportBtn.disabled = false;
    dvCommitImportBtn.textContent = "确认导入";
  }
});

// ── Chart canvas ───────────────────────────────────────────────────────
function initDVCanvasSize() {
  var canvas = dvChartCanvas;
  var dpr = window.devicePixelRatio || 1;
  var container = canvas.parentElement;
  var W = container.clientWidth;
  var H = 400;
  canvas.width = W * dpr;
  canvas.height = H * dpr;
  canvas.style.width = W + "px";
  canvas.style.height = H + "px";
  var ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
}

// ── Chart loading ──────────────────────────────────────────────────────
async function loadDVChart() {
  var metric = dvState.chartMetric;
  var viewMode = dvChartViewMode ? dvChartViewMode.value : "atlas";
  var productPool = dvChartProductPool ? dvChartProductPool.value : "mainstream";

  await new Promise(function(resolve) { requestAnimationFrame(resolve); });
  initDVCanvasSize();
  dvChartStatus.textContent = "";
  dvChartStatus.className = "dv-chart-status";
  try {
    var yearsArr = getCheckedValues(dvChartYearCheckboxes);
    var productsArr = getCheckedValues(dvChartProductCheckboxes);
    var categoriesArr = getCheckedValues(dvChartCategoryCheckboxes);
    var countriesArr = getCheckedValues(dvChartCountryCheckboxes);
    var mainstreamArr = getCheckedValues(dvChartMainstreamCheckboxes);
    var url = "/api/data-visualization/chart?metric=" + encodeURIComponent(metric);
    url = appendMultiSelectParam(url, "years", yearsArr, dvChartYearCheckboxes.querySelectorAll('input[type="checkbox"]').length);
    if (productPool === "aggregate") {
      url += "&product_pool=aggregate";
    } else {
      url += "&product_pool=" + encodeURIComponent(productPool);
      if (productPool === "custom") {
        url = appendMultiSelectParam(url, "products", productsArr, dvChartProductCheckboxes.querySelectorAll('input[type="checkbox"]').length);
      } else if (productsArr.length === 0) {
        url += "&products=__EMPTY__";
      } else {
        url += "&products=" + encodeURIComponent(productsArr.join(","));
      }
    }
    url = appendMultiSelectParam(url, "categories", categoriesArr, dvChartCategoryCheckboxes.querySelectorAll('input[type="checkbox"]').length);
    url = appendMultiSelectParam(url, "source_countries", countriesArr, dvChartCountryCheckboxes.querySelectorAll('input[type="checkbox"]').length);
    url = appendMultiSelectParam(url, "mainstream_status", mainstreamArr, dvChartMainstreamCheckboxes.querySelectorAll('input[type="checkbox"]').length);
    var result = await api(url);
    renderDVChart(result.series, viewMode);
  } catch (err) {
    console.error("图表加载失败:", err);
    var canvas = dvChartCanvas;
    var ctx = canvas.getContext("2d");
    ctx.fillStyle = "#9ca3af";
    ctx.font = "14px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("暂无数据，请先导入数据", canvas.width / (window.devicePixelRatio || 1) / 2, 200);
  }
}

function renderDVChart(series, viewMode) {
  // series = { "卡粉": { "2023": [{week_no, display_date, value, is_missing_filled}, ...], ... }, ... }
  initDVCanvasSize();
  var W, H, ctx;
  W = dvChartCanvas.parentElement.clientWidth;
  H = 400;
  ctx = dvChartCanvas.getContext("2d");

  dvState.lastChartData = series;

  var products = Object.keys(series);
  if (!products.length) {
    ctx.fillStyle = "#9ca3af";
    ctx.font = "14px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("暂无数据，请先导入数据", W / 2, H / 2);
    return;
  }

  // Collect all (product, year) → data point arrays
  var lines = []; // [{product, year, points: [{week_no, value, display_date, is_missing_filled}]}]
  var allVals = [];

  for (var pi = 0; pi < products.length; pi++) {
    var prod = products[pi];
    var productSeries = series[prod] || {};
    var years = Object.keys(productSeries).sort();
    for (var yi = 0; yi < years.length; yi++) {
      var yr = years[yi];
      var pts = productSeries[yr];
      lines.push({
        product: prod,
        year: yr,
        points: pts.map(function(p) {
          return {
            week_no: p.week_no,
            value: p.value,
            display_date: p.display_date,
            is_missing_filled: !!p.is_missing_filled
          };
        })
      });
      for (var i = 0; i < pts.length; i++) {
        var numericValue = getChartPointNumericValue(pts[i]);
        if (numericValue !== null) allVals.push(numericValue);
      }
    }
  }

  if (!allVals.length) {
    ctx.fillStyle = "#9ca3af";
    ctx.font = "14px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("暂无数据", W / 2, H / 2);
    return;
  }

  if (viewMode === "atlas" && products.length > 1) {
    renderDVChartAtlas(ctx, W, H, series, products);
    return;
  }

  var dpad = { top: 30, right: products.length > 1 ? 180 : 110, bottom: 60, left: 60 };
  var chartW = W - dpad.left - dpad.right;
  var chartH = H - dpad.top - dpad.bottom;

  // X-axis: week numbers 1-52
  var maxWeek = 52;
  function xScale(wn) { return dpad.left + ((wn - 1) / (maxWeek - 1)) * chartW; }

  // Y-axis: auto-scale from data minimum, not forced from 0
  var yMin = Math.min.apply(null, allVals);
  var yMax = Math.max.apply(null, allVals);
  var yPad = (yMax - yMin) * 0.08 || 50;
  yMin = Math.max(0, yMin - yPad);
  yMax = yMax + yPad;
  function yScale(v) { return dpad.top + chartH - ((v - yMin) / (yMax - yMin)) * chartH; }

  var niceStep = calcNiceStep(yMax - yMin);
  var yTicks = [];
  for (var v = yMin; v <= yMax + niceStep * 0.5; v += niceStep) {
    yTicks.push(v);
  }

  var yearColors = ["#2563eb", "#dc2626", "#16a34a", "#ca8a04", "#7c3aed", "#0891b2", "#0d9488", "#d97706", "#be185d", "#475569", "#15803d", "#b45309"];
  var yearColorMap = {};
  var lineColorMap = {};
  var legendItems = [];
  var allYears = [];
  for (var li = 0; li < lines.length; li++) {
    var yr2 = lines[li].year;
    if (allYears.indexOf(yr2) < 0) allYears.push(yr2);
  }
  allYears.sort();
  for (var yi2 = 0; yi2 < allYears.length; yi2++) {
    yearColorMap[allYears[yi2]] = yearColors[yi2 % yearColors.length];
  }
  for (var liColor = 0; liColor < lines.length; liColor++) {
    var legendKey = products.length > 1 ? (lines[liColor].product + " " + lines[liColor].year) : lines[liColor].year;
    var legendColor = products.length > 1 ? yearColors[liColor % yearColors.length] : yearColorMap[lines[liColor].year];
    lineColorMap[legendKey] = legendColor;
    legendItems.push({ label: legendKey, color: legendColor });
  }

  // Grid lines + Y labels
  ctx.strokeStyle = "#e5e7eb";
  ctx.lineWidth = 1;
  for (var ti = 0; ti < yTicks.length; ti++) {
    var y = yScale(yTicks[ti]);
    ctx.beginPath();
    ctx.moveTo(dpad.left, y);
    ctx.lineTo(W - dpad.right, y);
    ctx.stroke();
    ctx.fillStyle = "#6b7280";
    ctx.font = "11px sans-serif";
    ctx.textAlign = "right";
    ctx.fillText(formatChartNumber(yTicks[ti]), dpad.left - 6, y + 4);
  }

  // X-axis labels: every 4 weeks
  ctx.fillStyle = "#6b7280";
  ctx.font = "10px sans-serif";
  ctx.textAlign = "center";
  for (var w = 1; w <= maxWeek; w += 4) {
    ctx.fillText("W" + String(w).padStart(2, "0"), xScale(w), dpad.top + chartH + 14);
  }

  // Draw lines
  var highlightedLineKey = dvState.highlightedLineKey || null;
  var availableLineKeys = lines.map(function(line) {
    return products.length > 1 ? (line.product + " " + line.year) : line.year;
  });
  if (highlightedLineKey && availableLineKeys.indexOf(highlightedLineKey) < 0) {
    highlightedLineKey = null;
    dvState.highlightedLineKey = null;
  }
  for (var li2 = 0; li2 < lines.length; li2++) {
    var line = lines[li2];
    var lineKey = products.length > 1 ? (line.product + " " + line.year) : line.year;
    var color = lineColorMap[lineKey] || yearColorMap[line.year];
    var alpha = highlightedLineKey && highlightedLineKey !== lineKey ? 0.12 : 1;
    var lineW = highlightedLineKey === lineKey ? 3 : 1.8;

    ctx.strokeStyle = color;
    ctx.globalAlpha = alpha;
    ctx.lineWidth = lineW;
    ctx.setLineDash([]);
    ctx.beginPath();

    var pts2 = line.points;
    var firstPoint = true;
    for (var pi3 = 0; pi3 < pts2.length; pi3++) {
      if (isMissingChartPoint(pts2[pi3])) {
        firstPoint = true;
        continue;
      }
      var x = xScale(pts2[pi3].week_no);
      var y2 = yScale(getChartPointNumericValue(pts2[pi3]));
      if (firstPoint) { ctx.moveTo(x, y2); firstPoint = false; }
      else ctx.lineTo(x, y2);
    }
    ctx.stroke();
    ctx.setLineDash([]);
    for (var missIndex = 0; missIndex < pts2.length; missIndex++) {
      if (isMissingChartPoint(pts2[missIndex])) {
        drawMissingChartMarker(ctx, xScale(pts2[missIndex].week_no), dpad.top + chartH - 8, color, highlightedLineKey === lineKey ? 4 : 3);
      }
    }
    ctx.globalAlpha = 1;

    // Single-product chart keeps a small line-end year label. Multi-product charts use a legend to avoid overlap.
    var lastP = null;
    for (var lastIndex = pts2.length - 1; lastIndex >= 0; lastIndex--) {
      if (!isMissingChartPoint(pts2[lastIndex])) {
        lastP = pts2[lastIndex];
        break;
      }
    }
    if (lastP && products.length === 1) {
      var lx = xScale(lastP.week_no);
      var ly = yScale(getChartPointNumericValue(lastP));
      ctx.fillStyle = color;
      ctx.font = "bold 11px sans-serif";
      ctx.textAlign = "left";
      ctx.fillText(line.year, lx + 4, ly - 4);
    }
  }

  if (products.length > 1) {
    drawDVChartLegend(ctx, legendItems, W - dpad.right + 18, dpad.top, dpad.right - 24, chartH);
  }

  // Draw data point nodes for highlighted line
  if (highlightedLineKey) {
    for (var li3 = 0; li3 < lines.length; li3++) {
      var nodeLineKey = products.length > 1 ? (lines[li3].product + " " + lines[li3].year) : lines[li3].year;
      if (nodeLineKey === highlightedLineKey) {
        var hpts = lines[li3].points;
        ctx.fillStyle = products.length > 1
          ? (lineColorMap[nodeLineKey] || yearColorMap[lines[li3].year])
          : yearColorMap[lines[li3].year];
        for (var pi4 = 0; pi4 < hpts.length; pi4++) {
          var px = xScale(hpts[pi4].week_no);
          if (isMissingChartPoint(hpts[pi4])) {
            drawMissingChartMarker(ctx, px, dpad.top + chartH - 8, ctx.fillStyle, 4);
            continue;
          }
          var py = yScale(getChartPointNumericValue(hpts[pi4]));
          ctx.beginPath();
          ctx.arc(px, py, 3, 0, Math.PI * 2);
          ctx.fill();
        }
        break;
      }
    }
  }

  // Click to highlight one product-year line.
  dvChartCanvas.onclick = function(e) {
    var rect = dvChartCanvas.getBoundingClientRect();
    var mx = e.clientX - rect.left;
    var my = e.clientY - rect.top;

    var closestLineKey = null;
    var closestDist = Infinity;
    for (var li3 = 0; li3 < lines.length; li3++) {
      var ln = lines[li3];
      var clickLineKey = products.length > 1 ? (ln.product + " " + ln.year) : ln.year;
      var prevValid = null;
      for (var pi4 = 0; pi4 < ln.points.length; pi4++) {
        if (isMissingChartPoint(ln.points[pi4])) {
          prevValid = null;
          continue;
        }
        var px = xScale(ln.points[pi4].week_no);
        var py = yScale(getChartPointNumericValue(ln.points[pi4]));
        var dist = Math.hypot(mx - px, my - py);
        if (prevValid) {
          dist = Math.min(dist, distanceToSegment(mx, my, xScale(prevValid.week_no), yScale(getChartPointNumericValue(prevValid)), px, py));
        }
        if (dist < closestDist && dist < 30) {
          closestDist = dist;
          closestLineKey = clickLineKey;
        }
        prevValid = ln.points[pi4];
      }
    }
    if (closestLineKey) {
      dvState.highlightedLineKey = dvState.highlightedLineKey === closestLineKey ? null : closestLineKey;
      dvState.highlightedYear = null;
      renderDVChart(dvState.lastChartData, dvChartViewMode ? dvChartViewMode.value : "atlas");
    }
  };

  // Hover tooltip
  dvChartCanvas.onmousemove = function(e) {
    var rect = dvChartCanvas.getBoundingClientRect();
    var mx = e.clientX - rect.left;
    var my = e.clientY - rect.top;
    var tooltip = "";

    for (var li4 = 0; li4 < lines.length; li4++) {
      var ln2 = lines[li4];
      for (var pi5 = 0; pi5 < ln2.points.length; pi5++) {
        var px2 = xScale(ln2.points[pi5].week_no);
        var py2 = isMissingChartPoint(ln2.points[pi5])
          ? dpad.top + chartH - 8
          : yScale(getChartPointNumericValue(ln2.points[pi5]));
        if (Math.hypot(mx - px2, my - py2) < 15) {
          tooltip = formatDVChartTooltip(ln2.points[pi5], ln2.product);
          break;
        }
      }
    }
    dvChartCanvas.title = tooltip;
  };
}

function isMissingChartPoint(point) {
  if (!point) return true;
  if (point.is_missing_filled) return true;
  if (point.value === null || point.value === undefined || point.value === "") return true;
  return !Number.isFinite(Number(point.value));
}

function getChartPointNumericValue(point) {
  if (isMissingChartPoint(point)) return null;
  return Number(point.value);
}

function formatDVChartTooltip(point, product) {
  var parts = [];
  if (point.display_date) parts.push(formatDateOnly(point.display_date));
  parts.push("Week " + (point.week_no || "--"));
  parts.push(product);
  parts.push(isMissingChartPoint(point) ? "无数据" : formatChartNumber(getChartPointNumericValue(point)));
  return parts.join(" | ");
}

function drawMissingChartMarker(ctx, x, y, color, radius) {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.fillStyle = "#ffffff";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(x - radius + 1, y + radius - 1);
  ctx.lineTo(x + radius - 1, y - radius + 1);
  ctx.stroke();
  ctx.restore();
}

function distanceToSegment(px, py, x1, y1, x2, y2) {
  var dx = x2 - x1;
  var dy = y2 - y1;
  if (dx === 0 && dy === 0) return Math.hypot(px - x1, py - y1);
  var t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy);
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

function renderDVChartAtlas(ctx, W, H, series, products) {
  var cols = W >= 1100 ? 3 : (W >= 760 ? 2 : 1);
  var panelGap = 28;
  var panelW = (W - panelGap * (cols - 1)) / cols;
  var panelH = 190;
  var rows = Math.ceil(products.length / cols);
  var dpr = window.devicePixelRatio || 1;
  dvChartCanvas.height = Math.max(420, rows * panelH + (rows - 1) * panelGap) * dpr;
  dvChartCanvas.style.height = Math.max(420, rows * panelH + (rows - 1) * panelGap) + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, dvChartCanvas.height / dpr);

  var years = [];
  products.forEach(function(product) {
    Object.keys(series[product] || {}).forEach(function(year) {
      if (years.indexOf(year) < 0) years.push(year);
    });
  });
  years.sort();
  var yearColors = ["#2563eb", "#dc2626", "#16a34a", "#ca8a04", "#7c3aed", "#0891b2", "#0d9488", "#d97706"];
  var yearColorMap = {};
  years.forEach(function(year, index) {
    yearColorMap[year] = yearColors[index % yearColors.length];
  });

  var hitPoints = [];
  var highlightedLineKey = dvState.highlightedLineKey || null;
  var availableLineKeys = [];
  products.forEach(function(product) {
    Object.keys(series[product] || {}).forEach(function(year) {
      availableLineKeys.push(product + " " + year);
    });
  });
  if (highlightedLineKey && availableLineKeys.indexOf(highlightedLineKey) < 0) {
    highlightedLineKey = null;
    dvState.highlightedLineKey = null;
  }

  products.forEach(function(product, index) {
    var col = index % cols;
    var row = Math.floor(index / cols);
    var x0 = col * (panelW + panelGap);
    var y0 = row * (panelH + panelGap);
    var pad = { top: 22, right: 34, bottom: 28, left: 40 };
    var chartW = panelW - pad.left - pad.right;
    var chartH = panelH - pad.top - pad.bottom;
    var productSeries = series[product] || {};
    var vals = [];
    Object.keys(productSeries).forEach(function(year) {
      productSeries[year].forEach(function(point) {
        var numericValue = getChartPointNumericValue(point);
        if (numericValue !== null) vals.push(numericValue);
      });
    });
    if (!vals.length) return;
    var yMin = Math.min.apply(null, vals);
    var yMax = Math.max.apply(null, vals);
    var yPad = (yMax - yMin) * 0.08 || 20;
    yMin = Math.max(0, yMin - yPad);
    yMax = yMax + yPad;
    function xScale(weekNo) { return x0 + pad.left + ((weekNo - 1) / 51) * chartW; }
    function yScale(value) { return y0 + pad.top + chartH - ((value - yMin) / (yMax - yMin)) * chartH; }

    ctx.fillStyle = "#111827";
    ctx.font = "bold 12px sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(product, x0 + pad.left, y0 + 14);
    ctx.strokeStyle = "#e5e7eb";
    ctx.lineWidth = 1;
    for (var grid = 0; grid < 4; grid++) {
      var gy = y0 + pad.top + (grid / 3) * chartH;
      ctx.beginPath();
      ctx.moveTo(x0 + pad.left, gy);
      ctx.lineTo(x0 + pad.left + chartW, gy);
      ctx.stroke();
    }
    ctx.fillStyle = "#6b7280";
    ctx.font = "10px sans-serif";
    ctx.textAlign = "center";
    [1, 13, 26, 39, 52].forEach(function(weekNo) {
      ctx.fillText("W" + String(weekNo).padStart(2, "0"), xScale(weekNo), y0 + pad.top + chartH + 18);
    });

    Object.keys(productSeries).sort().forEach(function(year) {
      var pts = productSeries[year];
      var color = yearColorMap[year];
      var lineKey = product + " " + year;
      var alpha = highlightedLineKey && highlightedLineKey !== lineKey ? 0.16 : 1;
      ctx.strokeStyle = color;
      ctx.globalAlpha = alpha;
      ctx.lineWidth = highlightedLineKey === lineKey ? 2.6 : 1.4;
      ctx.beginPath();
      var firstValidPoint = true;
      pts.forEach(function(point) {
        if (isMissingChartPoint(point)) {
          firstValidPoint = true;
          return;
        }
        var px = xScale(point.week_no);
        var py = yScale(getChartPointNumericValue(point));
        if (firstValidPoint) {
          ctx.moveTo(px, py);
          firstValidPoint = false;
        }
        else ctx.lineTo(px, py);
        if (!highlightedLineKey || highlightedLineKey === lineKey) {
          hitPoints.push({ x: px, y: py, year: year, product: product, lineKey: lineKey, point: point });
        }
      });
      ctx.stroke();
      pts.forEach(function(point) {
        if (!isMissingChartPoint(point)) return;
        var px = xScale(point.week_no);
        var py = y0 + pad.top + chartH - 7;
        drawMissingChartMarker(ctx, px, py, color, highlightedLineKey === lineKey ? 4 : 3);
        if (!highlightedLineKey || highlightedLineKey === lineKey) {
          hitPoints.push({ x: px, y: py, year: year, product: product, lineKey: lineKey, point: point });
        }
      });
      if (highlightedLineKey === lineKey) {
        ctx.fillStyle = color;
        pts.forEach(function(point) {
          if (isMissingChartPoint(point)) {
            drawMissingChartMarker(ctx, xScale(point.week_no), y0 + pad.top + chartH - 7, color, 4);
            return;
          }
          ctx.beginPath();
          ctx.arc(xScale(point.week_no), yScale(getChartPointNumericValue(point)), 2.5, 0, Math.PI * 2);
          ctx.fill();
        });
      }
      ctx.globalAlpha = 1;
    });
  });

  dvChartCanvas.onclick = function(e) {
    var rect = dvChartCanvas.getBoundingClientRect();
    var mx = e.clientX - rect.left;
    var my = e.clientY - rect.top;
    var closest = null;
    var closestDist = Infinity;
    products.forEach(function(product, index) {
      var col = index % cols;
      var row = Math.floor(index / cols);
      var x0 = col * (panelW + panelGap);
      var y0 = row * (panelH + panelGap);
      var pad = { top: 22, right: 34, bottom: 28, left: 40 };
      var chartW = panelW - pad.left - pad.right;
      var chartH = panelH - pad.top - pad.bottom;
      var productSeries = series[product] || {};
      var vals = [];
      Object.keys(productSeries).forEach(function(year) {
        productSeries[year].forEach(function(point) {
          var numericValue = getChartPointNumericValue(point);
          if (numericValue !== null) vals.push(numericValue);
        });
      });
      if (!vals.length) return;
      var yMin = Math.min.apply(null, vals);
      var yMax = Math.max.apply(null, vals);
      var yPad = (yMax - yMin) * 0.08 || 20;
      yMin = Math.max(0, yMin - yPad);
      yMax = yMax + yPad;
      function xScaleClick(weekNo) { return x0 + pad.left + ((weekNo - 1) / 51) * chartW; }
      function yScaleClick(value) { return y0 + pad.top + chartH - ((value - yMin) / (yMax - yMin)) * chartH; }

      Object.keys(productSeries).sort().forEach(function(year) {
        var pts = productSeries[year];
        var lineKey = product + " " + year;
        var prevValid = null;
        for (var pi = 0; pi < pts.length; pi++) {
          if (isMissingChartPoint(pts[pi])) {
            prevValid = null;
            continue;
          }
          var px = xScaleClick(pts[pi].week_no);
          var py = yScaleClick(getChartPointNumericValue(pts[pi]));
          var dist = Math.hypot(mx - px, my - py);
          if (prevValid) {
            dist = Math.min(dist, distanceToSegment(mx, my, xScaleClick(prevValid.week_no), yScaleClick(getChartPointNumericValue(prevValid)), px, py));
          }
          if (dist < closestDist && dist < 18) {
            closestDist = dist;
            closest = { lineKey: lineKey };
          }
          prevValid = pts[pi];
        }
      });
    });
    if (closest) {
      dvState.highlightedLineKey = dvState.highlightedLineKey === closest.lineKey ? null : closest.lineKey;
      dvState.highlightedYear = null;
      renderDVChart(dvState.lastChartData, dvChartViewMode ? dvChartViewMode.value : "atlas");
    }
  };

  dvChartCanvas.onmousemove = function(e) {
    var rect = dvChartCanvas.getBoundingClientRect();
    var mx = e.clientX - rect.left;
    var my = e.clientY - rect.top;
    var closest = hitPoints.find(function(item) {
      return Math.hypot(mx - item.x, my - item.y) < 12;
    });
    dvChartCanvas.title = closest
      ? formatDVChartTooltip(closest.point, closest.product)
      : "";
  };
}

function drawDVChartLegend(ctx, items, x, y, maxW, maxH) {
  ctx.save();
  ctx.font = "11px sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.fillStyle = "#374151";
  ctx.fillText("图例", x, y + 6);

  var rowH = 17;
  var startY = y + 26;
  var maxRows = Math.max(1, Math.floor((maxH - 26) / rowH));
  var visibleItems = items.slice(0, maxRows);
  visibleItems.forEach(function(item, index) {
    var yy = startY + index * rowH;
    ctx.strokeStyle = item.color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x, yy);
    ctx.lineTo(x + 16, yy);
    ctx.stroke();
    ctx.fillStyle = "#374151";
    ctx.fillText(truncateCanvasText(ctx, item.label, maxW - 24), x + 22, yy);
  });
  if (items.length > visibleItems.length) {
    ctx.fillStyle = "#6b7280";
    ctx.fillText("+" + (items.length - visibleItems.length) + " 项", x, startY + visibleItems.length * rowH);
  }
  ctx.restore();
}

function truncateCanvasText(ctx, text, maxW) {
  if (ctx.measureText(text).width <= maxW) return text;
  var output = text;
  while (output.length > 1 && ctx.measureText(output + "...").width > maxW) {
    output = output.slice(0, -1);
  }
  return output + "...";
}

function calcNiceStep(yMax) {
  if (yMax <= 200) return 50;
  if (yMax <= 500) return 100;
  if (yMax <= 1000) return 200;
  if (yMax <= 2000) return 400;
  if (yMax <= 5000) return 500;
  return Math.pow(10, Math.floor(Math.log10(yMax))) / 2;
}

function applyDVChartProductPool() {
  var filters = dvState.chartFilters || {};
  var pools = filters.product_pools || {};
  var pool = dvChartProductPool ? dvChartProductPool.value : "mainstream";
  var items = [];
  if (pool === "mainstream") items = pools.mainstream || filters.products || [];
  else if (pool === "non_mainstream") items = pools.non_mainstream || [];
  else if (pool === "aggregate") items = pools.aggregate || ["主流矿合计", "非主流矿合计"];
  else items = pools.custom || filters.products || [];

  buildCheckboxes(dvChartProductCheckboxes, items, loadDVChart, true);
  if (pool === "aggregate") {
    dvChartProductAll.disabled = true;
    dvChartProductNone.disabled = true;
  } else {
    dvChartProductAll.disabled = false;
    dvChartProductNone.disabled = false;
  }
}

// ── Chart controls init ────────────────────────────────────────────────
async function initDVChartControls() {
  try {
    var filters = await api("/api/data-visualization/filters");
    dvState.chartFilters = filters;
    await buildYearCheckboxes(dvChartYearCheckboxes, loadDVChart);
    applyDVChartProductPool();
    buildCheckboxes(dvChartCategoryCheckboxes, filters.categories || [], loadDVChart, true);
    buildCheckboxes(dvChartCountryCheckboxes, filters.source_countries || [], loadDVChart, true);
    buildCheckboxes(dvChartMainstreamCheckboxes, filters.mainstream_statuses || [], loadDVChart, true);

    if (dvChartViewMode) {
      dvChartViewMode.addEventListener("change", loadDVChart);
    }
    if (dvChartProductPool) {
      dvChartProductPool.addEventListener("change", function() {
        applyDVChartProductPool();
        loadDVChart();
      });
    }

    dvChartYearAll.addEventListener("click", function() {
      selectAllCheckboxes(dvChartYearCheckboxes);
      loadDVChart();
    });
    dvChartYearNone.addEventListener("click", function() {
      selectNoneCheckboxes(dvChartYearCheckboxes);
      loadDVChart();
    });
    dvChartProductAll.addEventListener("click", function() {
      selectAllCheckboxes(dvChartProductCheckboxes);
      loadDVChart();
    });
    dvChartProductNone.addEventListener("click", function() {
      selectNoneCheckboxes(dvChartProductCheckboxes);
      loadDVChart();
    });
  } catch (err) {
    console.error("加载图表筛选选项失败:", err);
  }
}

// DV controls initialized lazily on first chart page activation
