(function() {
  function monthRangeForDate(value) {
    if (!/^\d{8}$/.test(value || "")) return { from: "", to: "" };
    const year = Number(value.slice(0, 4));
    const month = Number(value.slice(4, 6));
    const lastDay = new Date(year, month, 0).getDate();
    return { from: `${value.slice(0, 6)}01`, to: `${value.slice(0, 6)}${String(lastDay).padStart(2, "0")}` };
  }

  const tm = {
    initialized: false,
    moduleCode: "trading_overview",
    view: "overview",
    factsTab: "positions",
    junnengTab: "positions",
    optionsTab: "positions",
    junnengPage: 1,
    junnengPageSize: 20,
    optionsPage: 1,
    optionsPageSize: 20,
    businessQuery: { junneng: "", options: "" },
    businessSide: { junneng: "", options: "" },
    businessDates: {
      junneng: { positions: { from: "", to: "" }, closes: { from: "", to: "" }, trades: { from: "", to: "" } },
      options: { positions: { from: "", to: "" }, closes: { from: "", to: "" }, trades: { from: "", to: "" } },
    },
    businessDatesInitialized: false,
    page: 1,
    pageSize: 20,
    query: "",
    assetType: "",
    side: "",
    openClose: "",
    classification: "",
    dateFrom: "",
    dateTo: "",
    config: null,
    overview: null,
    overviewMode: "month",
    overviewBusinessType: "",
    selected: new Set(),
    selectionBusy: false,
    importPreviewId: null,
    permissions: { canEdit: false, canSensitive: false },
    quoteRefreshState: {
      options: { fingerprint: "", updatedAt: "—", status: "等待更新" },
    },
  };
  const factCache = new Map();
  const factRequests = new Map();
  let factCacheVersion = 0;
  const BUSINESS_QUOTE_REFRESH_MS = 15000;
  let businessQuoteRefreshTimer = null;
  let businessQuoteRefreshInFlight = false;
  let businessVisibilityObserver = null;

  const $ = (selector) => document.querySelector(selector);
  const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[char]);
  const fmt = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });
  const greekFmt = new Intl.NumberFormat("zh-CN", { minimumFractionDigits: 4, maximumFractionDigits: 4 });
  const num = (value) => value == null || value === "" ? "—" : fmt.format(Number(value));
  const greekNum = (value) => value == null || value === "" ? "—" : greekFmt.format(Number(value));
  const money = (value) => `${Number(value || 0) > 0 ? "+" : Number(value || 0) < 0 ? "−" : ""}${fmt.format(Math.abs(Number(value || 0)))}`;
  const businessType = (value) => ({basic_hedging:"基础套保",strategic_hedging:"战略套保"})[value] || value || "未归类";
  const pending = () => '<span class="tm-tag amber">待计算</span>';
  const valuationSource = (value) => ({
    last_trade: "最新成交价",
    bid_ask_midpoint: "买一卖一中间价",
    settlement_reference: "结算价参考",
    unavailable: "不可用",
    expired: "已到期",
  })[value] || value || "—";
  const valuationStatus = (value) => ({
    live: "实时",
    settlement_reference: "参考",
    unavailable: "行情不可用",
    expired: "已到期",
  })[value] || value || "—";
  const SETTLEMENT_TYPE_LABELS = {
    trade_close: "普通平仓",
    exercise: "行权",
    assignment: "履约",
    expiry_abandon: "到期放弃",
  };

  const VIEW_COPY = {
    trading_overview: ["交易总览", "全量文华交易的领导驾驶舱", "overview"],
    trading_positions: ["持仓与交易", "查询、核验和归类全部真实交易事实", "positions"],
    trading_sh_junneng: ["上海钧能台账", "钢材套保业务的专用视图", "junneng"],
    trading_options: ["期权台账", "期权持仓、成交与风险视图", "options"],
    trading_export: ["汇总与导出", "统一预览并输出业务台账与交易模板", "export"],
  };

  function openDrawer(kicker, title, body) {
    $("#tmDrawerKicker").textContent = kicker;
    $("#tmDrawerTitle").textContent = title;
    $("#tmDrawerBody").innerHTML = body;
    $("#tmDrawerBackdrop").classList.remove("hidden");
    $("#tmDrawer").classList.add("open");
    $("#tmDrawer").setAttribute("aria-hidden", "false");
  }

  function closeDrawer() {
    $("#tmDrawerBackdrop").classList.add("hidden");
    $("#tmDrawer").classList.remove("open");
    $("#tmDrawer").setAttribute("aria-hidden", "true");
  }

  function showToast(message) {
    const toast = $("#tmToast");
    toast.textContent = message;
    toast.classList.remove("hidden");
    window.setTimeout(() => toast.classList.add("hidden"), 2600);
  }

  function showError(error) {
    showToast(error.message || "加载失败");
    const progress = $("#tmImportProgress");
    if (progress) {
      progress.className = "tm-import-progress tm-import-error";
      progress.textContent = `操作失败：${error.message || "未知错误"}`;
    }
  }

  async function ensureConfig() {
    if (!tm.config) tm.config = await api("/api/trading-management/config");
    if (!tm.businessDatesInitialized) {
      tm.businessDates.junneng.closes = monthRangeForDate(tm.config.latest_junneng_close_date);
      tm.businessDatesInitialized = true;
    }
    $("#tmAccountFilter").innerHTML = '<option value="">全部账户</option>' + tm.config.accounts.map((item) => `<option value="${item.id}">${esc(item.display_name || item.account_code)}</option>`).join("");
  }

  function switchInternalView(view) {
    tm.view = view;
    ["overview", "positions", "junneng", "options", "export"].forEach((name) => $("#tm" + name[0].toUpperCase() + name.slice(1) + "View").classList.toggle("hidden", name !== view));
  }

  function metric(label, value, note, tone = "") {
    return `<div class="tm-summary-item"><span class="tm-metric-label">${label}</span><strong class="tm-metric-value ${tone}">${value}</strong><small class="tm-metric-note">${note}</small></div>`;
  }

  function qualityRow(label, note, state, tone = "") {
    return `<div class="tm-quality-row"><div><strong>${label}</strong><span>${note}</span></div><span class="tm-tag ${tone}">${state}</span></div>`;
  }

  function dailyPnlChart(rows) {
    if (!rows.length) return '<div class="tm-chart-empty">暂无平仓盈亏数据</div>';
    const width = 760;
    const height = 250;
    const left = 52;
    const right = 18;
    const top = 18;
    const bottom = 34;
    const values = rows.map((row) => Number(row.fact_close_pnl || 0));
    const minimum = Math.min(0, ...values);
    const maximum = Math.max(0, ...values);
    const span = maximum - minimum || 1;
    const x = (index) => left + (rows.length === 1 ? (width - left - right) / 2 : index * (width - left - right) / (rows.length - 1));
    const y = (value) => top + (maximum - value) * (height - top - bottom) / span;
    const zeroY = y(0);
    const labelStep = Math.max(1, Math.ceil(rows.length / 6));
    const points = rows.map((row, index) => `${x(index)},${y(values[index])}`).join(" ");
    const dots = rows.map((row, index) => {
      const value = values[index];
      const tone = value >= 0 ? "positive" : "negative";
      const label = index % labelStep === 0 || index === rows.length - 1 ? `<text class="tm-chart-label" x="${x(index)}" y="238" text-anchor="middle">${esc(row.date || "")}</text>` : "";
      return `<line class="tm-chart-stem ${tone}" x1="${x(index)}" y1="${zeroY}" x2="${x(index)}" y2="${y(value)}"></line><circle class="tm-chart-point ${tone}" cx="${x(index)}" cy="${y(value)}" r="3.5"><title>${esc(row.date || "")}：${money(value)} 元</title></circle>${label}`;
    }).join("");
    return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="逐日平仓盈亏趋势"><line class="tm-chart-zero" x1="${left}" y1="${zeroY}" x2="${width - right}" y2="${zeroY}"></line><polyline class="tm-chart-line" points="${points}"></polyline>${dots}</svg>`;
  }

  function renderOverviewView(data) {
    const summary = `<div class="tm-summary-band">
      ${metric("期间成交", `${num(data.trades.record_count)} 笔`, `${num(data.trades.quantity)} 手 · 文华成交记录`)}
      ${metric("期间平仓盈亏", `${money(data.closes.fact_close_pnl)} 元`, "普通平仓及期权了结盈亏", Number(data.closes.fact_close_pnl) >= 0 ? "tm-positive" : "tm-negative")}
      ${metric("期间手续费", `${num(data.trades.fee)} 元`, "文华成交记录")}
      ${metric("期末持仓", `${num(data.positions.record_count)} 条`, `${data.positions.snapshot_date || "—"} · 事实快照`)}
      ${metric("期末保证金", `${num(data.positions.margin)} 元`, "文华期末持仓文件")}
    </div>`;
    $("#tmOverviewView").innerHTML = `
      <div class="tm-period-bar"><div class="tm-tabs">${[["month","月"],["day","日"],["quarter","季"],["custom","自定义"]].map(([mode,label])=>`<button class="tm-tab-button ${tm.overviewMode===mode?"active":""}" data-overview-period="${mode}">${label}</button>`).join("")}</div><div class="tm-tabs">${[["","全部"],["basic_hedging","基础套保"],["strategic_hedging","战略套保"]].map(([value,label])=>`<button class="tm-tab-button ${tm.overviewBusinessType===value?"active":""}" data-overview-business="${value}">${label}</button>`).join("")}</div><div class="tm-period-selection">${tm.overviewMode === "custom" ? `<input id="tmOverviewFrom" type="date"><span>至</span><input id="tmOverviewTo" type="date"><button id="tmOverviewApply">应用</button>` : ""}<span class="tm-tag blue">事实层 · 只读</span></div></div>
      ${summary}
      <section class="tm-panel tm-overview-chart"><div class="tm-panel-header"><div><h2>逐日平仓盈亏趋势</h2><p class="tm-section-copy">按普通平仓及期权了结盈亏汇总</p></div><span class="tm-tag">事实口径</span></div><div class="tm-chart-wrap">${dailyPnlChart(data.daily_close_pnl || [])}</div></section>
      <div class="tm-overview-mini-grid">
        <section class="tm-panel tm-quality-panel"><div class="tm-panel-header"><h2>数据质量</h2><small>导入与核验状态</small></div><div class="tm-quality-list">${qualityRow("成交记录", `${num(data.trades.record_count)} 条已读取`, "已确认", "blue")}${qualityRow("平仓与期权了结", `${num(data.closes.record_count)} 条`, "已匹配", "blue")}${qualityRow("持仓快照", data.positions.snapshot_date || "暂无快照", data.data_status.positions === "ok" ? "已确认" : "待导入", data.data_status.positions === "ok" ? "blue" : "amber")}${qualityRow("浮动盈亏", "计算口径待最终确认", "待计算", "amber")}</div></section>
        <section class="tm-panel"><div class="tm-panel-header"><div><h2>业务归属分布</h2><p class="tm-section-copy">事实交易归类进度</p></div><button class="tm-row-button" data-go-positions>前往归类 →</button></div><div class="tm-business-list">${qualityRow("上海钧能", "RB / HC 正式归属", "业务层")}${qualityRow("期权", "默认展示全部期权", "业务层")}${qualityRow("其它与待归属", "保留事实层完整记录", "待确认", "amber")}</div></section>
        <section class="tm-panel"><div class="tm-panel-header"><h2>活跃合约</h2><small>按当前事实范围</small></div><div class="tm-business-list">${qualityRow("成交手数", `${num(data.trades.quantity)} 手`, "全量")}${qualityRow("持仓手数", `${num(data.positions.quantity)} 手`, "期末")}${qualityRow("期权风险指标", "Delta / Gamma / Theta / Vega", "待计算", "amber")}</div></section>
      </div>`;
    $("[data-go-positions]")?.addEventListener("click", () => document.querySelector('.menu-item') && activateModule("trading_positions"));
    document.querySelectorAll("[data-overview-period]").forEach((button)=>button.addEventListener("click",()=>{tm.overviewMode=button.dataset.overviewPeriod;loadOverview().catch(showError);}));
    document.querySelectorAll("[data-overview-business]").forEach((button)=>button.addEventListener("click",()=>{tm.overviewBusinessType=button.dataset.overviewBusiness;loadOverview().catch(showError);}));
    $("#tmOverviewApply")?.addEventListener("click",()=>{tm.dateFrom=$("#tmOverviewFrom").value.replaceAll("-","");tm.dateTo=$("#tmOverviewTo").value.replaceAll("-","");loadOverview().catch(showError);});
  }

  async function loadOverview() {
    const params = new URLSearchParams();
    const dates = (tm.overview?.daily_close_pnl || []).map((row)=>row.date).filter(Boolean);
    const latest = dates.at(-1) || tm.overview?.positions?.snapshot_date || "";
    if (tm.overviewMode === "day" && latest) { params.set("start_date",latest); params.set("end_date",latest); }
    if (tm.overviewMode === "month" && latest) { params.set("start_date",`${latest.slice(0,6)}01`); params.set("end_date",`${latest.slice(0,6)}31`); }
    if (tm.overviewMode === "quarter" && latest) { const month=Number(latest.slice(4,6)); const start=String(Math.floor((month-1)/3)*3+1).padStart(2,"0"); const end=String(Number(start)+2).padStart(2,"0"); params.set("start_date",`${latest.slice(0,4)}${start}01`); params.set("end_date",`${latest.slice(0,4)}${end}31`); }
    if (tm.overviewMode === "custom") { if (tm.dateFrom) params.set("start_date",tm.dateFrom); if (tm.dateTo) params.set("end_date",tm.dateTo); }
    if (tm.overviewBusinessType) params.set("business_type",tm.overviewBusinessType);
    tm.overview = await api(`/api/trading-management/overview${params.size ? `?${params}` : ""}`);
    renderOverviewView(tm.overview);
  }

  function factTabs() {
    return `<div class="tm-tabs">${[["positions","当前持仓"],["closes","平仓记录"],["trades","全部交易"]].map(([key,label]) => `<button class="tm-tab-button ${tm.factsTab === key ? "active" : ""}" data-fact-tab="${key}">${label}</button>`).join("")}</div>`;
  }

  function factQuery(page = tm.page, pageSize = tm.pageSize) {
    const params = new URLSearchParams({ page, page_size: pageSize });
    if (tm.query) params.set("contract", tm.query);
    if (tm.assetType) params.set("asset_type", tm.assetType);
    if (tm.side) params.set("direction", tm.side);
    if (tm.openClose) params.set("open_close", tm.openClose);
    if (tm.classification) params.set("classification", tm.classification);
    if (tm.dateFrom) params.set("start_date", tm.dateFrom);
    if (tm.dateTo) params.set("end_date", tm.dateTo);
    return params.toString();
  }

  function factCacheKey(tab, page = tm.page) {
    return JSON.stringify([factCacheVersion, tab, tm.query, tm.assetType, tm.side, tm.openClose, tm.classification, tm.dateFrom, tm.dateTo, page, tm.pageSize]);
  }

  function invalidateFactCache() {
    factCacheVersion += 1;
    factCache.clear();
    factRequests.clear();
  }

  async function loadFactData(tab, { page = tm.page, pageSize = tm.pageSize, refresh = false } = {}) {
    const key = JSON.stringify([factCacheKey(tab, page), pageSize]);
    if (!refresh && factCache.has(key)) return factCache.get(key);
    if (factRequests.has(key)) return factRequests.get(key);
    const request = api(`/api/trading-management/facts/${tab}?${factQuery(page,pageSize)}`)
      .then((data) => { factCache.set(key, data); return data; })
      .finally(() => factRequests.delete(key));
    factRequests.set(key, request);
    return request;
  }

  function factDateValue(value) { return value ? `${value.slice(0,4)}-${value.slice(4,6)}-${value.slice(6,8)}` : ""; }

  function filters(includeOpenClose = true) {
    return `<div class="tm-filters compact ${includeOpenClose ? "with-open-close" : "without-open-close"}"><input id="tmSearch" class="tm-filter-search" type="search" placeholder="搜索合约" value="${esc(tm.query)}"><button id="tmSearchApply" class="tm-secondary-button">搜索</button><select id="tmAssetType" class="tm-filter-select" value="${esc(tm.assetType)}"><option value="" ${tm.assetType === "" ? "selected" : ""}>全部资产</option><option value="future" ${tm.assetType === "future" ? "selected" : ""}>期货</option><option value="option" ${tm.assetType === "option" ? "selected" : ""}>期权</option></select><select id="tmSide" class="tm-filter-select" value="${esc(tm.side)}"><option value="" ${tm.side === "" ? "selected" : ""}>全部方向</option><option value="买" ${tm.side === "买" ? "selected" : ""}>买</option><option value="卖" ${tm.side === "卖" ? "selected" : ""}>卖</option></select>${includeOpenClose ? `<select id="tmOpenClose" class="tm-filter-select" value="${esc(tm.openClose)}"><option value="" ${tm.openClose === "" ? "selected" : ""}>全部开平</option><option value="开仓" ${tm.openClose === "开仓" ? "selected" : ""}>开仓</option><option value="平仓" ${tm.openClose === "平仓" ? "selected" : ""}>平仓</option></select>` : ""}<select id="tmClassification" class="tm-filter-select" value="${esc(tm.classification)}"><option value="" ${tm.classification === "" ? "selected" : ""}>全部归类状态</option><option value="classified" ${tm.classification === "classified" ? "selected" : ""}>已归类</option><option value="unclassified" ${tm.classification === "unclassified" ? "selected" : ""}>未归类</option></select><input id="tmDateFrom" class="tm-filter-date" type="date" value="${factDateValue(tm.dateFrom)}"><input id="tmDateTo" class="tm-filter-date" type="date" value="${factDateValue(tm.dateTo)}"></div>`;
  }

  function filterSummary(summary) {
    const items = tm.factsTab === "closes"
      ? [["记录数",summary.record_count],["了结手数",summary.settlement_quantity],["成交平仓手数",summary.transaction_close_quantity],["手续费",summary.fee],["平仓盈亏",summary.fact_close_pnl]]
      : [["记录数",summary.record_count],["手数",summary.quantity],["手续费",summary.fee],["平仓盈亏",summary.fact_close_pnl],["保证金",summary.margin],["浮动盈亏","待计算"]];
    return `<div class="tm-filter-summary">${items.map(([label,value]) => `<div><span>${label}</span><strong>${typeof value === "number" ? num(value) : esc(value ?? "—")}</strong></div>`).join("")}</div>`;
  }

  const FACT_COLUMNS = {
    positions: [["snapshot_date","快照日"],["contract","合约"],["asset_type","资产类型"],["direction","方向"],["quantity","手数"],["average_price","持仓均价"],["margin","保证金"],["assignment","业务类型 / 策略"],["source_record_count","聚合记录"],["pending","浮动盈亏"]],
    closes: [["close_date","平仓日"],["settlement_type","了结类型"],["contract","合约"],["asset_type","资产类型"],["open_side","方向"],["quantity","手数"],["open_price","开仓价"],["close_price","平仓价"],["fact_close_pnl","平仓盈亏"],["matched_fee","手续费"],["assignment","业务类型 / 策略"]],
    trades: [["trade_date","成交日"],["contract","合约"],["asset_type","资产类型"],["side","方向"],["open_close","开平"],["quantity","手数"],["price","成交价"],["fee","手续费"],["fact_close_pnl","平仓盈亏"],["assignment","业务类型 / 策略"]],
  };

  function valueCell(row, key) {
    if (key === "pending") return pending();
    if (key === "settlement_type") return esc(SETTLEMENT_TYPE_LABELS[row[key]] || row[key] || "普通平仓");
    if (["open_price","fact_close_pnl"].includes(key) && row.settlement_type !== "trade_close" && row.verification_status !== "matched") return '<span class="tm-tag amber">待核验</span>';
    if (key === "assignment") return row.assignment_status === "classified" && row.business_type ? `<span class="tm-tag blue">${esc(businessType(row.business_type))}${row.strategy ? ` / ${esc(row.strategy)}` : ""}</span>` : '<span class="tm-tag amber">待确认</span>';
    if (key === "valuation_source") return esc(valuationSource(row[key]));
    if (key === "valuation_status" || key === "floating_pnl_status") return esc(valuationStatus(row[key]));
    if (["quantity","average_price","margin","open_price","close_price","fact_close_pnl","matched_fee","price","fee","business_pnl","matched_quantity","market_price","valuation_price","underlying_price","iv","floating_pnl","delta_exposure","gamma_exposure","theta_exposure","vega_exposure","net_close_pnl","fund_interest","settlement_80","settlement_20","allocated_open_fee","allocated_close_fee","settlement_open_price","settlement_fee"].includes(key)) return num(row[key]);
    if (key === "asset_type") return row[key] === "option" ? "期权" : "期货";
    if (key === "business_type") return row.assignment_status === "classified" && row[key] ? `<span class="tm-tag blue">${esc(businessType(row[key]))}</span>` : '<span class="tm-tag amber">待确认</span>';
    return esc(row[key] ?? "—");
  }

  function factTable(items) {
    const columns = FACT_COLUMNS[tm.factsTab];
    const selectable = tm.factsTab === "trades" && tm.permissions.canEdit;
    return `<div class="tm-table-wrap"><table><thead><tr>${selectable ? "<th></th>" : ""}${columns.map(([,label]) => `<th>${label}</th>`).join("")}<th></th></tr></thead><tbody>${items.length ? items.map((row) => `<tr>${selectable ? `<td>${row.open_close === "开仓" ? `<input type="checkbox" data-select-row="${row.identity_id}" ${tm.selected.has(row.identity_id) ? "checked" : ""}>` : "继承"}</td>` : ""}${columns.map(([key]) => `<td class="${["quantity","average_price","margin","open_price","close_price","fact_close_pnl","matched_fee","price","fee"].includes(key) ? "tm-numeric" : key === "contract" ? "tm-contract" : ""}">${valueCell(row,key)}</td>`).join("")}<td><button class="tm-row-button" data-detail='${esc(JSON.stringify(row))}'>详情 →</button></td></tr>`).join("") : `<tr><td colspan="${columns.length + 2}" class="tm-empty-state">暂无数据</td></tr>`}</tbody></table></div>`;
  }

  function pagination(data, prefix = "tm") {
    return `<div class="tm-pagination"><span>共 ${data.total_items} 条</span><label>每页<select id="${prefix}PageSize">${[20,50,100].map((size) => `<option value="${size}" ${Number(data.page_size) === size ? "selected" : ""}>${size}</option>`).join("")}</select>条</label><button id="${prefix}Prev" ${data.page <= 1 ? "disabled" : ""}>上一页</button><span>第 ${data.page} / ${data.total_pages} 页</span><button id="${prefix}Next" ${data.page >= data.total_pages ? "disabled" : ""}>下一页</button></div>`;
  }

  async function renderPositionsView() {
    const cached = factCache.get(factCacheKey(tm.factsTab));
    if (!cached) {
      $("#tmPositionsView").innerHTML = `<section class="tm-panel"><div class="tm-section-header"><div>${factTabs()}</div><span class="tm-tag blue">统一事实层</span></div>${filters(tm.factsTab === "trades")}<div class="tm-table-loading"><span class="spinner"></span><span>正在读取${tm.factsTab === "positions" ? "持仓" : tm.factsTab === "closes" ? "平仓" : "交易"}记录…</span></div></section>`;
    }
    const data = cached || await loadFactData(tm.factsTab);
    const selection = tm.factsTab === "trades" && tm.permissions.canEdit ? `<div class="tm-selection-bar"><span>${tm.selectionBusy ? "正在选择全部筛选结果…" : `已选择 ${tm.selected.size} 条开仓`}</span><button id="tmSelectPage" ${tm.selectionBusy ? "disabled" : ""}>选择当前页开仓</button><button id="tmSelectFiltered" ${tm.selectionBusy ? "disabled" : ""}>选择全部筛选开仓</button><button id="tmClearSelection" ${tm.selectionBusy ? "disabled" : ""}>清空选择</button><button id="tmClassify" class="tm-primary-button" ${tm.selected.size && !tm.selectionBusy ? "" : "disabled"}>业务归属</button></div>` : "";
    $("#tmPositionsView").innerHTML = `<section class="tm-panel"><div class="tm-section-header"><div>${factTabs()}</div><div class="tm-toolbar">${tm.permissions.canSensitive ? '<button id="tmImportButton" class="tm-secondary-button">导入结算单</button>' : ""}<span class="tm-tag blue">统一事实层</span></div></div>${filters(tm.factsTab === "trades")}${filterSummary(data.summary)}${selection}${factTable(data.items)}${pagination(data)}</section>`;
    wireFactActions(data);
  }

  function wireFactActions(data) {
    const resetSelectionForFilter = () => { tm.selected.clear(); tm.page = 1; };
    document.querySelectorAll("[data-fact-tab]").forEach((button) => button.addEventListener("click", () => { tm.factsTab = button.dataset.factTab; tm.page = 1; renderPositionsView().catch(showError); }));
    $("#tmSearchApply")?.addEventListener("click", () => { tm.query = $("#tmSearch").value.trim(); resetSelectionForFilter(); renderPositionsView().catch(showError); });
    [["#tmAssetType","assetType"],["#tmSide","side"],["#tmOpenClose","openClose"],["#tmClassification","classification"]].forEach(([selector,key]) => $(selector)?.addEventListener("change", (event) => { tm[key] = event.target.value; resetSelectionForFilter(); renderPositionsView().catch(showError); }));
    $("#tmDateFrom")?.addEventListener("change", (event) => { tm.dateFrom = event.target.value.replaceAll("-",""); resetSelectionForFilter(); renderPositionsView().catch(showError); });
    $("#tmDateTo")?.addEventListener("change", (event) => { tm.dateTo = event.target.value.replaceAll("-",""); resetSelectionForFilter(); renderPositionsView().catch(showError); });
    document.querySelectorAll("[data-select-row]").forEach((box) => box.addEventListener("change", () => { const id = Number(box.dataset.selectRow); box.checked ? tm.selected.add(id) : tm.selected.delete(id); renderPositionsView().catch(showError); }));
    $("#tmSelectPage")?.addEventListener("click", () => { tm.selected.clear(); data.items.filter((row) => row.open_close === "开仓").forEach((row) => tm.selected.add(row.identity_id)); renderPositionsView().catch(showError); });
    $("#tmSelectFiltered")?.addEventListener("click", async () => {
      tm.selectionBusy = true; await renderPositionsView();
      try {
        const result = await api(`/api/trading-management/facts/trades/selection-identities?${factQuery(1,100)}`);
        tm.selected = new Set(result.identity_ids);
      } catch (error) { showError(error); }
      finally { tm.selectionBusy = false; await renderPositionsView(); }
    });
    $("#tmClearSelection")?.addEventListener("click", () => { tm.selected.clear(); renderPositionsView().catch(showError); });
    $("#tmClassify")?.addEventListener("click", openClassificationDrawer);
    $("#tmImportButton")?.addEventListener("click", openImportDrawer);
    document.querySelectorAll("[data-detail]").forEach((button) => button.addEventListener("click", () => { const row = JSON.parse(button.dataset.detail); openDrawer("事实记录详情", row.contract, `<div class="tm-detail-list">${Object.entries(row).map(([key,value]) => `<div><span>${esc(key)}</span><strong>${esc(value ?? "—")}</strong></div>`).join("")}</div><p class="tm-section-copy">原始事实不可修改；业务归类仅写入独立业务层。</p>`); }));
    $("#tmPageSize")?.addEventListener("change", (event) => { tm.pageSize = Number(event.target.value); tm.page = 1; renderPositionsView().catch(showError); });
    $("#tmPrev")?.addEventListener("click", () => { tm.page -= 1; renderPositionsView().catch(showError); });
    $("#tmNext")?.addEventListener("click", () => { tm.page += 1; renderPositionsView().catch(showError); });
  }

  async function openClassificationDrawer() {
    await ensureConfig();
    openDrawer("业务归属", `${tm.selected.size} 条开仓已选择`, `<p class="tm-section-copy">归属设置在完整开仓成交；平仓和到期了结按开平分摊自动继承。</p><div class="tm-upload-grid"><label class="tm-upload-box"><span>业务归属</span><select id="tmBusinessSubject">${tm.config.subjects.map((item) => `<option value="${item.id}">${esc(item.name)}</option>`).join("")}</select></label><label class="tm-upload-box"><span>业务类型</span><select id="tmBusinessType"><option value="basic_hedging">基础套保</option><option value="strategic_hedging">战略套保</option></select></label><label class="tm-upload-box"><span>策略</span><input id="tmStrategy" list="tmStrategyList"><datalist id="tmStrategyList">${tm.config.strategies.map((item) => `<option value="${esc(item.name)}"></option>`).join("")}</datalist></label><label class="tm-upload-box"><span>指令/备注</span><textarea id="tmInstruction"></textarea></label><div id="tmClassificationProgress" class="tm-import-progress">已选择 ${tm.selected.size} 条开仓，确认后批量保存。</div><button id="tmSaveClassification" class="tm-primary-button">确认整笔归属</button></div>`);
    $("#tmSaveClassification").addEventListener("click", async () => {
      const count = tm.selected.size;
      setClassificationBusy(true, `正在保存 ${count} 条业务归属…`);
      try {
        const result = await api("/api/trading-management/business-assignments/batch-confirm", {method:"POST",body:JSON.stringify({identity_ids:[...tm.selected],business_subject_id:Number($("#tmBusinessSubject").value),business_type:$("#tmBusinessType").value,strategy_name:$("#tmStrategy").value.trim(),instruction_text:$("#tmInstruction").value.trim()})});
        invalidateFactCache(); tm.selected.clear(); closeDrawer(); showToast(`业务归属已保存 ${result.assigned_count} 条`); await renderPositionsView();
      } catch (error) { setClassificationBusy(false, error.message || "保存失败", true); }
    });
  }

  function setClassificationBusy(busy, message, isError = false) {
    ["#tmBusinessSubject","#tmBusinessType","#tmStrategy","#tmInstruction","#tmSaveClassification"].forEach((selector) => { const element = $(selector); if (element) element.disabled = busy; });
    const progress = $("#tmClassificationProgress");
    if (!progress) return;
    progress.className = `tm-import-progress${isError ? " tm-import-error" : ""}`;
    progress.innerHTML = busy ? `<span class="spinner"></span><span>${esc(message)}</span>` : esc(message);
  }

  async function fileBase64(file) {
    const bytes = new Uint8Array(await file.arrayBuffer()); let binary = "";
    for (let index = 0; index < bytes.length; index += 0x8000) binary += String.fromCharCode(...bytes.subarray(index,index + 0x8000));
    return btoa(binary);
  }

  function setImportBusy(busy, message = "") {
    ["#tmImportAccount","#tmStatementFile","#tmImportCancel","#tmImportPreview","#tmImportConfirm"].forEach((selector) => { const element = $(selector); if (element) element.disabled = busy; });
    const progress = $("#tmImportProgress");
    if (progress && busy) {
      progress.className = "tm-import-progress";
      progress.innerHTML = `<span class="spinner"></span><span>${esc(message)}</span>`;
    } else if (progress && !progress.classList.contains("tm-import-error")) {
      progress.textContent = tm.importPreviewId ? "预检已完成，请核对结果后确认导入。" : "请选择交易所 TXT 结算单后预检。";
    }
  }

  function invalidateImportPreview() {
    tm.importPreviewId = null;
    $("#tmImportConfirm")?.classList.add("hidden");
    if ($("#tmImportResult")) $("#tmImportResult").textContent = "文件已变化，请重新预检。";
  }

  async function openImportDrawer() {
    await ensureConfig(); tm.importPreviewId = null;
    openDrawer("导入与核验", "交易所结算单", `<p class="tm-section-copy">每次只需上传一个 TXT 文件，系统自动识别日结单或月结单。月结单作为主要来源；日结单用于期初建立、月中更新或补数，重叠数据自动去重且月结优先。</p><div class="tm-upload-grid"><label class="tm-upload-box"><span>交易账户</span><select id="tmImportAccount">${tm.config.accounts.map((item) => `<option value="${item.id}">${esc(item.display_name)}</option>`).join("")}</select></label><label class="tm-upload-box"><span>交易所结算单（TXT）</span><input id="tmStatementFile" type="file" accept=".txt"></label><div id="tmImportProgress" class="tm-import-progress">请选择交易所 TXT 结算单后预检。</div><div id="tmImportResult" class="tm-section-copy"></div><div class="tm-drawer-actions"><button id="tmImportCancel">取消</button><button id="tmImportPreview" class="tm-primary-button">预检</button><button id="tmImportConfirm" class="tm-primary-button hidden">确认导入</button></div></div>`);
    ["#tmImportAccount","#tmStatementFile"].forEach((selector) => $(selector).addEventListener("change", invalidateImportPreview));
    $("#tmImportCancel").addEventListener("click", closeDrawer);
    $("#tmImportPreview").addEventListener("click", previewImport);
    $("#tmImportConfirm").addEventListener("click", confirmImport);
  }

  async function previewImport() {
    const file = $("#tmStatementFile").files[0];
    if (!file) return showError(new Error("请选择交易所 TXT 结算单"));
    setImportBusy(true,"正在解析并预检结算单，请稍候");
    try {
      const encoded = {name:file.name,content_base64:await fileBase64(file)};
      const result = await api("/api/trading-management/imports/preview", {method:"POST",body:JSON.stringify({account_id:Number($("#tmImportAccount").value),statement_file:encoded})});
      if (result.duplicate_batch_id) {
        tm.importPreviewId = null;
        $("#tmImportResult").textContent = `该结算单已导入（批次 ${result.duplicate_batch_id}），无需重复确认。`;
        $("#tmImportConfirm").classList.add("hidden");
        return;
      }
      tm.importPreviewId = result.preview_batch_id;
      const typeLabel = result.statement_type === "monthly" ? "月结单" : "日结单";
      const continuity = result.continuity?.message || "连续性状态待确认";
      $("#tmImportResult").textContent = `预检通过（${typeLabel}）：成交 ${result.counts.trade} 条，平仓 ${result.counts.close} 条，行权/弃权 ${result.counts.exercise || 0} 条，持仓 ${result.counts.position} 条；${result.range_start || "—"} 至 ${result.range_end || "—"}。${continuity}`;
      $("#tmImportConfirm").classList.remove("hidden");
    } catch (error) { showError(error); } finally { setImportBusy(false); }
  }

  async function pollImportJob(jobId) {
    const stageLabels = {
      queued: "等待后台任务",
      facts: "写入并切换事实",
      matching: "建立开平匹配",
      business_allocations: "重建业务分摊",
      done: "导入完成",
    };
    for (let attempt = 0; attempt < 600; attempt += 1) {
      const job = await api(`/api/trading-management/imports/jobs/${encodeURIComponent(jobId)}`);
      const label = stageLabels[job.stage] || "处理导入任务";
      setImportBusy(true, `${label}：${job.message || "处理中"}`);
      if (job.status === "succeeded") return job.result;
      if (job.status === "failed") throw new Error(job.message || "后台导入失败");
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
    }
    throw new Error("后台导入仍在运行，请稍后重新打开交易管理查看结果");
  }

  async function confirmImport() {
    if (!tm.importPreviewId) return showError(new Error("请先完成预检"));
    setImportBusy(true,"正在创建后台导入任务");
    try {
      const job = await api(`/api/trading-management/imports/${tm.importPreviewId}/confirm`, {method:"POST"});
      const result = job.job_id ? await pollImportJob(job.job_id) : job;
      tm.importPreviewId = null; invalidateFactCache(); closeDrawer(); showToast(`导入完成：成交 ${result.counts.trade}，平仓 ${result.counts.close}，持仓 ${result.counts.position}`); await renderPositionsView();
    } catch (error) { showError(error); } finally { setImportBusy(false); }
  }

  function businessTabs(active, prefix) {
    return `<div class="tm-tabs">${[["positions","当前持仓"],["closes","平仓记录"],["trades","全部交易"]].map(([key,label]) => `<button class="tm-tab-button ${active === key ? "active" : ""}" data-${prefix}-tab="${key}">${label}</button>`).join("")}</div>`;
  }

  const BUSINESS_COLUMNS = {
    positions: [["contract","合约"],["asset_type","资产类型"],["direction","方向"],["quantity","手数"],["average_price","持仓均价"],["business_subject","业务归属"],["business_type","业务类型"],["strategy","策略"],["source_record_count","聚合记录"],["floating_pnl","浮动盈亏"]],
    closes: [["close_date","平仓日"],["settlement_type","了结类型"],["contract","合约"],["open_side","方向"],["matched_quantity","手数"],["open_price","开仓价"],["close_price","平仓价"],["fact_close_pnl","事实平仓盈亏"],["business_pnl","业务归属盈亏"],["strategy","策略"]],
    trades: [["trade_date","成交日"],["contract","合约"],["side","方向"],["open_close","开平"],["quantity","手数"],["price","成交价"],["business_subject","业务归属"],["business_type","业务类型"],["strategy","策略"]],
  };

  const JUNNENG_POSITION_COLUMNS = [
    ["contract","合约"],["direction","方向"],["quantity","手数"],["average_price","持仓均价"],
    ["valuation_price","最新价"],["market_time","行情时间"],["floating_pnl","浮动盈亏"],
    ["valuation_status","估值状态"],["business_type","业务类型"],["strategy","策略"],
  ];
  const JUNNENG_CLOSE_COLUMNS = [
    ["close_date","平仓日"],["contract","合约"],["open_side","方向"],["matched_quantity","手数"],
    ["settlement_open_price","分摊开仓价"],["close_price","平仓价"],["net_close_pnl","平仓盈亏（含手续费）"],
    ["fund_interest","资金利息"],["settlement_80","80%结算金额"],["settlement_20","20%结算金额"],
    ["settlement_fee","手续费"],["strategy","策略"],
  ];

  function businessColumns(view, tab) {
    if (view === "junneng" && tab === "positions") return JUNNENG_POSITION_COLUMNS;
    if (view === "junneng" && tab === "closes") return JUNNENG_CLOSE_COLUMNS;
    return BUSINESS_COLUMNS[tab];
  }

  function businessTable(data, view, tab, allowRematch) {
    const columns = businessColumns(view, tab);
    return `<div class="tm-table-wrap"><table><thead><tr>${columns.map(([,label]) => `<th>${label}</th>`).join("")}${allowRematch && tab === "closes" ? "<th>操作</th>" : ""}</tr></thead><tbody>${data.items.length ? data.items.map((row) => `<tr>${columns.map(([key]) => `<td>${valueCell(row,key)}</td>`).join("")}${allowRematch && tab === "closes" ? `<td><button class="tm-row-button" data-rematch-id="${row.identity_id}" data-version="${row.allocation_version || 1}">调整开平 →</button></td>` : ""}</tr>`).join("") : `<tr><td colspan="${columns.length + 1}" class="tm-empty-state">暂无数据</td></tr>`}</tbody></table></div>`;
  }

  function optionAnatomy(contract) {
    const match = String(contract || "").match(/^(.+)-(C|P)-(\d+(?:\.\d+)?)$/i);
    return match ? { underlying: match[1], kind: match[2].toUpperCase() === "C" ? "看涨" : "看跌", strike: match[3] } : { underlying: contract || "—", kind: "期权", strike: "—" };
  }

  function optionPositionTable(data) {
    const body = data.items.map((row) => {
      const anatomy = optionAnatomy(row.contract);
      const iv = row.iv == null ? "—" : `${num(Number(row.iv) * 100)}%`;
      return `<tr><td>${esc(row.contract)}</td><td>${esc(row.direction)}</td><td>${num(row.quantity)}</td><td>${num(row.average_price)}</td><td>${num(row.valuation_price)}</td><td>${anatomy.kind}</td><td>${anatomy.strike}</td><td>${iv}</td><td>${num(row.floating_pnl)}</td><td>${greekNum(row.delta)}</td><td>${greekNum(row.gamma)}</td><td>${greekNum(row.theta)}</td><td>${greekNum(row.vega)}</td></tr>`;
    }).join("");
    return `<div class="tm-table-wrap"><table><thead><tr><th>合约</th><th>方向</th><th>手数</th><th>持仓均价</th><th>估值价</th><th>看涨/看跌</th><th>行权价</th><th>IV</th><th>浮动盈亏</th><th>Delta</th><th>Gamma</th><th>Theta</th><th>Vega</th></tr></thead><tbody>${body || '<tr><td colspan="13" class="tm-empty-state">暂无数据</td></tr>'}</tbody></table></div>`;
  }

  function optionQuoteFingerprint(data) {
    return JSON.stringify(data.items.map((row) => [
      row.contract, row.direction, row.quantity, row.valuation_price,
      row.iv, row.floating_pnl, row.delta, row.gamma, row.theta, row.vega,
      row.market_time, row.market_data_status,
    ]));
  }

  function refreshTimestamp() {
    return new Date().toLocaleString("zh-CN", { hour12: false });
  }

  function updateOptionQuoteRefreshState(data, reason) {
    const state = tm.quoteRefreshState.options;
    const fingerprint = optionQuoteFingerprint(data);
    const statuses = data.items.map((row) => row.valuation_status || row.market_data_status);
    let status = "行情不可用";
    if (statuses.length && statuses.every((value) => value === "live")) {
      status = reason === "timer" && state.fingerprint === fingerprint
        ? "已检查，行情无变化"
        : "数据已更新";
    } else if (statuses.some((value) => value === "live")) {
      status = "部分行情已更新";
    } else if (statuses.some((value) => value === "settlement_reference")) {
      status = "参考结算行情";
    }
    state.fingerprint = fingerprint;
    state.updatedAt = refreshTimestamp();
    state.status = status;
  }

  function optionQuoteRefreshStatus() {
    const state = tm.quoteRefreshState.options;
    const tone = ["行情不可用", "更新失败", "参考结算行情"].includes(state.status) ? "amber" : "blue";
    return `<div class="tm-refresh-status"><span class="tm-tag">上次更新时间：<strong id="tmOptionsRefreshTime">${esc(state.updatedAt)}</strong></span><span class="tm-tag ${tone}">更新状态：<strong id="tmOptionsRefreshStatus">${esc(state.status)}</strong></span></div>`;
  }

  function markOptionQuoteRefreshFailure() {
    const state = tm.quoteRefreshState.options;
    state.updatedAt = refreshTimestamp();
    state.status = "更新失败";
    const time = $("#tmOptionsRefreshTime");
    const status = $("#tmOptionsRefreshStatus");
    if (time) time.textContent = state.updatedAt;
    if (status) status.textContent = state.status;
  }

  function businessFilters(view, tab) {
    const dateValue = (value) => value ? `${value.slice(0,4)}-${value.slice(4,6)}-${value.slice(6)}` : "";
    const dates = tm.businessDates[view][tab];
    return `<div class="tm-filters compact without-open-close"><input id="${view}Search" class="tm-filter-search" type="search" placeholder="搜索合约" value="${esc(tm.businessQuery[view])}"><button id="${view}SearchApply" class="tm-secondary-button">搜索</button><select id="${view}Side" class="tm-filter-select"><option value="">全部方向</option><option value="买" ${tm.businessSide[view] === "买" ? "selected" : ""}>买</option><option value="卖" ${tm.businessSide[view] === "卖" ? "selected" : ""}>卖</option></select><input id="${view}DateFrom" class="tm-filter-date" type="date" value="${dateValue(dates.from)}"><input id="${view}DateTo" class="tm-filter-date" type="date" value="${dateValue(dates.to)}"></div>`;
  }

  function businessFilterSummary(summary, view, tab) {
    const items = view === "junneng" && tab === "closes"
      ? [["平仓盈亏（含手续费）",summary.net_close_pnl],["资金利息",summary.fund_interest],["80%结算金额",summary.settlement_80],["20%结算金额",summary.settlement_20],["手续费",summary.fee]]
      : view === "options" && tab === "positions"
      ? [["组合浮盈",summary.floating_pnl],["Delta",summary.delta],["Gamma",summary.gamma],["Theta",summary.theta],["Vega",summary.vega]]
      : tab === "positions"
      ? [["记录数",summary.record_count],["手数",summary.quantity],["浮动盈亏",summary.floating_pnl],["估值状态",valuationStatus(summary.floating_pnl_status)]]
      : tab === "closes"
      ? [["记录数",summary.record_count],["了结手数",summary.settlement_quantity],["成交平仓手数",summary.transaction_close_quantity],["业务归属盈亏",summary.business_pnl],["手续费",summary.fee]]
      : [["记录数",summary.record_count],["成交手数",summary.quantity],["业务归属盈亏",summary.business_pnl],["手续费",summary.fee]];
    return `<div class="tm-filter-summary compact">${items.map(([label,value]) => `<div><span>${label}</span><strong>${typeof value === "number" ? (view === "options" && tab === "positions" && label !== "组合浮盈" ? greekNum(value) : num(value)) : esc(value ?? "—")}</strong></div>`).join("")}</div>`;
  }

  function stopBusinessQuoteRefresh() {
    if (businessQuoteRefreshTimer) window.clearInterval(businessQuoteRefreshTimer);
    businessQuoteRefreshTimer = null;
  }

  function startBusinessQuoteRefresh() {
    stopBusinessQuoteRefresh();
    const tabKey = tm.view === "junneng" ? "junnengTab" : "optionsTab";
    if (!["junneng","options"].includes(tm.view) || tm[tabKey] !== "positions" || document.visibilityState !== "visible" || $("#tradingManagementPage").classList.contains("hidden")) return;
    businessQuoteRefreshTimer = window.setInterval(async () => {
      if (document.visibilityState !== "visible" || tm[tabKey] !== "positions" || businessQuoteRefreshInFlight) return;
      businessQuoteRefreshInFlight = true;
      try {
        await renderBusinessLedger(tm.view, "timer");
      } catch (error) {
        if (tm.view === "options") markOptionQuoteRefreshFailure();
        showError(error);
      } finally {
        businessQuoteRefreshInFlight = false;
      }
    }, BUSINESS_QUOTE_REFRESH_MS);
  }

  async function renderBusinessLedger(view, reason = "load") {
    const tabKey = view === "junneng" ? "junnengTab" : "optionsTab";
    const tab = tm[tabKey];
    const pageKey = view === "junneng" ? "junnengPage" : "optionsPage";
    const pageSizeKey = view === "junneng" ? "junnengPageSize" : "optionsPageSize";
    const params = new URLSearchParams({page:tm[pageKey],page_size:tm[pageSizeKey]});
    const dates = tm.businessDates[view][tab];
    if (tm.businessQuery[view]) params.set("contract",tm.businessQuery[view]);
    if (tm.businessSide[view]) params.set("direction",tm.businessSide[view]);
    if (dates.from) params.set("start_date",dates.from);
    if (dates.to) params.set("end_date",dates.to);
    const data = await api(`/api/trading-management/business/${view}/${tab}?${params}`);
    if (view === "options" && tab === "positions") updateOptionQuoteRefreshState(data, reason);
    const notice = view === "junneng" ? "仅展示已完成业务归属的上海钧能数据。" : "仅展示已完成业务归属的数据；页面每15秒刷新，实时行情未接通时结算价仅供参考，IV 与 Greeks 不作为实时值；明细 Greeks 为带方向的单手口径。";
    const refreshStatus = view === "options" && tab === "positions" ? optionQuoteRefreshStatus() : "";
    const ledgerTable = view === "options" && tab === "positions" ? optionPositionTable(data) : businessTable(data,view,tab,tm.permissions.canEdit);
    const html = `<section class="tm-section tm-panel"><div class="tm-section-header">${businessTabs(tab,view)}<div class="tm-refresh-status"><span class="tm-tag blue">${notice}</span>${refreshStatus}</div></div>${businessFilters(view,tab)}${businessFilterSummary(data.summary,view,tab)}${ledgerTable}${pagination(data,view)}</section>`;
    $(view === "junneng" ? "#tmJunnengView" : "#tmOptionsView").innerHTML = html;
    document.querySelectorAll(`[data-${view}-tab]`).forEach((button) => button.addEventListener("click", () => { tm[tabKey] = button.dataset[`${view}Tab`]; tm[pageKey] = 1; stopBusinessQuoteRefresh(); renderBusinessLedger(view).catch(showError); }));
    $(`#${view}SearchApply`).addEventListener("click",()=>{tm.businessQuery[view]=$(`#${view}Search`).value.trim();tm[pageKey]=1;renderBusinessLedger(view).catch(showError);});
    $(`#${view}Side`).addEventListener("change",(event)=>{tm.businessSide[view]=event.target.value;tm[pageKey]=1;renderBusinessLedger(view).catch(showError);});
    [["DateFrom","from"],["DateTo","to"]].forEach(([suffix,key])=>$(`#${view}${suffix}`).addEventListener("change",(event)=>{tm.businessDates[view][tab][key]=event.target.value.replaceAll("-","");tm[pageKey]=1;renderBusinessLedger(view).catch(showError);}));
    $(`#${view}PageSize`).addEventListener("change",(event)=>{tm[pageSizeKey]=Number(event.target.value);tm[pageKey]=1;renderBusinessLedger(view).catch(showError);});
    $(`#${view}Prev`).addEventListener("click",()=>{tm[pageKey]-=1;renderBusinessLedger(view).catch(showError);});
    $(`#${view}Next`).addEventListener("click",()=>{tm[pageKey]+=1;renderBusinessLedger(view).catch(showError);});
    document.querySelectorAll("[data-rematch-id]").forEach((button) => button.addEventListener("click", () => openRematch(Number(button.dataset.rematchId),Number(button.dataset.version))));
    startBusinessQuoteRefresh();
  }

  async function openRematch(closeId, version) {
    const result = await api(`/api/trading-management/business-closes/${closeId}/candidates`);
    openDrawer("调整业务开平关系", `平仓事实 ${closeId}`, `<p class="tm-section-copy">事实层不变；只调整业务层对应关系。</p><div class="tm-upload-grid">${result.items.map((item) => `<label class="tm-upload-box"><span>${esc(item.contract)} · ${esc(item.trade_date)} · ${num(item.price)}</span><small>可平 ${num(item.available_quantity)} 手 · ${esc(item.strategy || "未配置策略")}</small><input class="tm-rematch-quantity" data-id="${item.identity_id}" type="number" min="0" max="${item.available_quantity}" value="0"></label>`).join("")}<div id="tmRematchImpact" class="tm-import-progress">选择开仓记录和手数后预览影响。</div><label class="tm-upload-box"><span>调整原因</span><textarea id="tmRematchReason"></textarea></label><div class="tm-drawer-actions"><button id="tmRestoreDefault">恢复默认</button><button id="tmPreviewRematch" class="tm-primary-button">预览影响</button><button id="tmConfirmRematch" class="tm-primary-button hidden">确认调整</button></div></div>`);
    let preview = null;
    const selections = () => [...document.querySelectorAll(".tm-rematch-quantity")].map((input) => ({open_trade_identity_id:Number(input.dataset.id),quantity:Number(input.value)})).filter((item) => item.quantity > 0);
    $("#tmPreviewRematch").addEventListener("click", async () => { try { preview = await api(`/api/trading-management/business-closes/${closeId}/preview`,{method:"POST",body:JSON.stringify({allocation_version:version,selections:selections()})}); $("#tmRematchImpact").textContent = `业务盈亏从 ${num(preview.before_business_pnl)} 调整为 ${num(preview.after_business_pnl)}；事实盈亏不变。`; $("#tmConfirmRematch").classList.remove("hidden"); } catch (error) { showError(error); } });
    $("#tmConfirmRematch").addEventListener("click", async () => { try { await api(`/api/trading-management/business-closes/${closeId}/confirm`,{method:"POST",body:JSON.stringify({preview_token:preview.preview_token,allocation_version:version,reason:$("#tmRematchReason").value.trim()})}); invalidateFactCache(); closeDrawer(); showToast("业务开平关系已更新"); await renderBusinessLedger(tm.view); } catch (error) { showError(error); } });
    $("#tmRestoreDefault").addEventListener("click", async () => { try { await api(`/api/trading-management/business-closes/${closeId}/restore-default`,{method:"POST",body:JSON.stringify({allocation_version:version,reason:"恢复事实层默认开平关系"})}); invalidateFactCache(); closeDrawer(); showToast("已恢复默认关系"); await renderBusinessLedger(tm.view); } catch (error) { showError(error); } });
  }

  function exportRow(title,note) {
    return `<div class="tm-export-row"><div><strong>${title}</strong><p>${note}</p></div><button disabled>功能暂未开放</button></div>`;
  }

  function renderExportView() {
    $("#tmExportView").innerHTML = `<div class="tm-content-grid"><section class="tm-panel"><div class="tm-panel-header"><div><h2>统一输出</h2><p class="tm-section-copy">基于同一事实层生成不同业务格式</p></div><span class="tm-tag amber">功能暂未开放</span></div><div class="tm-export-list">${exportRow("完整交易台账","包含基础套保交易明细与战略套保交易明细")}${exportRow("上海钧能台账","钢材期货业务视图")}${exportRow("期权台账","期权成交、持仓与风险指标")}${exportRow("自定义明细","按日期、品种、主体、业务类型或策略筛选")}</div></section><section class="tm-panel"><h2>输出前检查</h2><div class="tm-quality-list">${qualityRow("原始事实","文华三表导入后确认","待确认","amber")}${qualityRow("业务主体","需完成业务归属","待确认","amber")}${qualityRow("结算口径","尚未最终确认","待核验","amber")}${qualityRow("模板结构","基础套保 / 战略套保","已识别","blue")}</div></section></div><section class="tm-section tm-panel"><div class="tm-section-header"><div><h2>最近输出版本</h2><p class="tm-section-copy">正式系统将保存文件、来源范围、规则版本和核验状态</p></div></div><div class="tm-empty-state">本期不生成真实文件，功能位置按原型保留。</div></section>`;
  }

  async function refresh() {
    $("#tmLoadingState").classList.remove("hidden"); $("#tmContent").classList.add("hidden");
    try {
      if (tm.view === "overview") await loadOverview();
      if (tm.view === "positions") await renderPositionsView();
      if (tm.view === "junneng" || tm.view === "options") await renderBusinessLedger(tm.view);
      if (tm.view === "export") renderExportView();
      switchInternalView(tm.view); $("#tmLoadingState").classList.add("hidden"); $("#tmContent").classList.remove("hidden");
    } catch (error) { $("#tmLoadingState").classList.add("hidden"); $("#tmErrorState").classList.remove("hidden"); $("#tmErrorState").textContent = `数据读取失败：${error.message}`; throw error; }
  }

  function bind() {
    if (tm.initialized) return; tm.initialized = true;
    $("#tmCloseDrawer").addEventListener("click", closeDrawer);
    $("#tmDrawerBackdrop").addEventListener("click", closeDrawer);
    $("#tmDataInfoButton").addEventListener("click", () => openDrawer("数据说明","当前系统口径",`<div class="tm-quality-list">${qualityRow("数据来源","交易所日结单或月结单 TXT","事实层")}${qualityRow("重叠规则","月结优先，保留来源与差异审计","自动去重")}${qualityRow("业务归属","独立业务层，不改事实","可调整")}${qualityRow("真实交易操作","系统严格禁止","只读")}</div>`));
    document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeDrawer(); });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") startBusinessQuoteRefresh();
      else stopBusinessQuoteRefresh();
    });
    businessVisibilityObserver = new MutationObserver(() => {
      if ($("#tradingManagementPage").classList.contains("hidden")) stopBusinessQuoteRefresh();
    });
    businessVisibilityObserver.observe($("#tradingManagementPage"), { attributes: true, attributeFilter: ["class"] });
  }

  window.TradingManagement = {
    async activate(moduleCode, permissions) {
      bind(); stopBusinessQuoteRefresh(); tm.moduleCode = moduleCode; tm.permissions = permissions;
      const [title,subtitle,view] = VIEW_COPY[moduleCode]; tm.view = view;
      $("#tmPageTitle").textContent = title; $("#tmPageSubtitle").textContent = subtitle;
      await ensureConfig(); await refresh();
    },
    deactivate() {
      stopBusinessQuoteRefresh();
    },
  };
})();
