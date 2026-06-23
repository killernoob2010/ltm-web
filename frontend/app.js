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
  alertSettings: [],
  alertHistory: [],
  alertNotificationTimer: null,
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
      button.addEventListener("click", () => activateModule(item.code));
      wrapper.appendChild(button);
    }
    menu.appendChild(wrapper);
  }
}

function showOnly(page) {
  [infoSummaryPage, midEventPage, shJunnengPage, riskAlertPage, userManagementPage, placeholderPage].forEach((item) => item.classList.add("hidden"));
  page.classList.remove("hidden");
}

async function activateModule(code) {
  state.activeModule = code;
  stopMidEventAutoRefresh();
  renderMenu();
  const label = moduleLabel(code);
  pageTitle.textContent = label.name;
  pageSubtitle.textContent = `${label.group} / ${label.name}`;

  if (code === "info_summary") {
    showOnly(infoSummaryPage);
    await loadInfoSummary();
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

async function calculateInfoCard(card, mock = false) {
  const infoType = card.dataset.infoType;
  const year = Number(card.querySelector(".info-year")?.value || card.querySelector(".info-year1")?.value || state.infoConfig.default_year);
  const payload = {
    info_type: infoType,
    year,
    month: card.querySelector(".info-month")?.value || "09",
    calc_date: card.querySelector(".info-date").value,
    year1: Number(card.querySelector(".info-year1")?.value || year),
    month1: card.querySelector(".info-month1")?.value || undefined,
    year2: Number(card.querySelector(".info-year2")?.value || year),
    month2: card.querySelector(".info-month2")?.value || undefined,
  };
  const status = card.querySelector(".status-value");
  status.textContent = "计算中";
  try {
    const result = await api(`/api/info-summary/calculate${mock ? "?mock=true" : ""}`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    fillInfoResult(card, result);
    status.textContent = result.cache_hit ? "已读取缓存并刷新今日值" : "缓存未命中，已刷新今日值";
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
  for (const card of cards) {
    await calculateInfoCard(card, mock);
  }
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
    await loadInfoCacheStatus();
    await calculateAllInfo(false);
    updateInfoCacheStatus(result.status === "success" ? "回填完成" : "部分指标需检查");
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
}

function startAlertNotifications() {
  stopAlertNotifications();
  loadNotifications(false).catch(() => {});
  state.alertNotificationTimer = window.setInterval(() => {
    loadNotifications(true).catch(() => {});
  }, 5000);
}

function stopAlertNotifications() {
  if (state.alertNotificationTimer) {
    window.clearInterval(state.alertNotificationTimer);
    state.alertNotificationTimer = null;
  }
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
