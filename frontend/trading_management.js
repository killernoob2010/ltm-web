(function() {
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
    businessClassification: { junneng: "", options: "" },
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
    selected: new Set(),
    importPreviewId: null,
    permissions: { canEdit: false, canSensitive: false },
  };
  const factCache = new Map();
  const factRequests = new Map();
  let factCacheVersion = 0;

  const $ = (selector) => document.querySelector(selector);
  const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[char]);
  const fmt = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 });
  const num = (value) => value == null || value === "" ? "—" : fmt.format(Number(value));
  const money = (value) => `${Number(value || 0) > 0 ? "+" : Number(value || 0) < 0 ? "−" : ""}${fmt.format(Math.abs(Number(value || 0)))}`;
  const businessType = (value) => ({basic_hedging:"基础套保",strategic_hedging:"战略套保"})[value] || value || "未归类";
  const pending = () => '<span class="tm-tag amber">待计算</span>';

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
      ${metric("期间平仓盈亏", `${money(data.closes.fact_close_pnl)} 元`, "文华逐笔平仓盈亏", Number(data.closes.fact_close_pnl) >= 0 ? "tm-positive" : "tm-negative")}
      ${metric("期间手续费", `${num(data.trades.fee)} 元`, "文华成交记录")}
      ${metric("期末持仓", `${num(data.positions.record_count)} 条`, `${data.positions.snapshot_date || "—"} · 事实快照`)}
      ${metric("期末保证金", `${num(data.positions.margin)} 元`, "文华期末持仓文件")}
    </div>`;
    $("#tmOverviewView").innerHTML = `
      <div class="tm-period-bar"><div class="tm-tabs">${[["month","月"],["day","日"],["quarter","季"],["custom","自定义"]].map(([mode,label])=>`<button class="tm-tab-button ${tm.overviewMode===mode?"active":""}" data-overview-period="${mode}">${label}</button>`).join("")}</div><div class="tm-period-selection">${tm.overviewMode === "custom" ? `<input id="tmOverviewFrom" type="date"><span>至</span><input id="tmOverviewTo" type="date"><button id="tmOverviewApply">应用</button>` : ""}<span class="tm-tag blue">事实层 · 只读</span></div></div>
      ${summary}
      <section class="tm-panel tm-overview-chart"><div class="tm-panel-header"><div><h2>逐日平仓盈亏趋势</h2><p class="tm-section-copy">按文华逐笔平仓盈亏汇总</p></div><span class="tm-tag">事实口径</span></div><div class="tm-chart-wrap">${dailyPnlChart(data.daily_close_pnl || [])}</div></section>
      <div class="tm-overview-mini-grid">
        <section class="tm-panel tm-quality-panel"><div class="tm-panel-header"><h2>数据质量</h2><small>导入与核验状态</small></div><div class="tm-quality-list">${qualityRow("成交记录", `${num(data.trades.record_count)} 条已读取`, "已确认", "blue")}${qualityRow("平仓与手续费", `${num(data.closes.record_count)} 条`, "已匹配", "blue")}${qualityRow("持仓快照", data.positions.snapshot_date || "暂无快照", data.data_status.positions === "ok" ? "已确认" : "待导入", data.data_status.positions === "ok" ? "blue" : "amber")}${qualityRow("浮动盈亏", "计算口径待最终确认", "待计算", "amber")}</div></section>
        <section class="tm-panel"><div class="tm-panel-header"><div><h2>业务归属分布</h2><p class="tm-section-copy">事实交易归类进度</p></div><button class="tm-row-button" data-go-positions>前往归类 →</button></div><div class="tm-business-list">${qualityRow("上海钧能", "RB / HC 正式归属", "业务层")}${qualityRow("期权", "默认展示全部期权", "业务层")}${qualityRow("其它与待归属", "保留事实层完整记录", "待确认", "amber")}</div></section>
        <section class="tm-panel"><div class="tm-panel-header"><h2>活跃合约</h2><small>按当前事实范围</small></div><div class="tm-business-list">${qualityRow("成交手数", `${num(data.trades.quantity)} 手`, "全量")}${qualityRow("持仓手数", `${num(data.positions.quantity)} 手`, "期末")}${qualityRow("期权风险指标", "Delta / Gamma / Theta / Vega", "待计算", "amber")}</div></section>
      </div>`;
    $("[data-go-positions]")?.addEventListener("click", () => document.querySelector('.menu-item') && activateModule("trading_positions"));
    document.querySelectorAll("[data-overview-period]").forEach((button)=>button.addEventListener("click",()=>{tm.overviewMode=button.dataset.overviewPeriod;loadOverview().catch(showError);}));
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

  function prefetchFactTabs() {
    ["positions", "closes", "trades"].filter((tab) => tab !== tm.factsTab).forEach((tab) => {
      loadFactData(tab, { page: 1 }).catch(() => {});
    });
  }

  function filters(includeOpenClose = true) {
    return `<div class="tm-filters compact ${includeOpenClose ? "with-open-close" : "without-open-close"}"><input id="tmSearch" class="tm-filter-search" type="search" placeholder="搜索合约" value="${esc(tm.query)}"><button id="tmSearchApply" class="tm-secondary-button">搜索</button><select id="tmAssetType" class="tm-filter-select"><option value="">全部资产</option><option value="future">期货</option><option value="option">期权</option></select><select id="tmSide" class="tm-filter-select"><option value="">全部方向</option><option value="买">买</option><option value="卖">卖</option></select>${includeOpenClose ? '<select id="tmOpenClose" class="tm-filter-select"><option value="">全部开平</option><option value="开仓">开仓</option><option value="平仓">平仓</option></select>' : ""}<select id="tmClassification" class="tm-filter-select"><option value="">全部归类状态</option><option value="classified">已归类</option><option value="unclassified">未归类</option></select><input id="tmDateFrom" class="tm-filter-date" type="date"><input id="tmDateTo" class="tm-filter-date" type="date"></div>`;
  }

  function filterSummary(summary) {
    const items = [["记录数",summary.record_count],["手数",summary.quantity],["手续费",summary.fee],["平仓盈亏",summary.fact_close_pnl],["保证金",summary.margin],["浮动盈亏","待计算"]];
    return `<div class="tm-filter-summary">${items.map(([label,value]) => `<div><span>${label}</span><strong>${typeof value === "number" ? num(value) : esc(value ?? "—")}</strong></div>`).join("")}</div>`;
  }

  const FACT_COLUMNS = {
    positions: [["snapshot_date","快照日"],["contract","合约"],["asset_type","资产类型"],["direction","方向"],["quantity","手数"],["average_price","持仓均价"],["margin","保证金"],["assignment","业务类型 / 策略"],["source_record_count","聚合记录"],["pending","浮动盈亏"]],
    closes: [["close_date","平仓日"],["contract","合约"],["asset_type","资产类型"],["open_side","方向"],["quantity","手数"],["open_price","开仓价"],["close_price","平仓价"],["fact_close_pnl","平仓盈亏"],["matched_fee","手续费"],["assignment","业务类型 / 策略"]],
    trades: [["trade_date","成交日"],["contract","合约"],["asset_type","资产类型"],["side","方向"],["open_close","开平"],["quantity","手数"],["price","成交价"],["fee","手续费"],["fact_close_pnl","平仓盈亏"],["assignment","业务类型 / 策略"]],
  };

  function valueCell(row, key) {
    if (key === "pending") return pending();
    if (key === "assignment") return row.assignment_status === "classified" && row.business_type ? `<span class="tm-tag blue">${esc(businessType(row.business_type))}${row.strategy ? ` / ${esc(row.strategy)}` : ""}</span>` : '<span class="tm-tag amber">待确认</span>';
    if (["quantity","average_price","margin","open_price","close_price","fact_close_pnl","matched_fee","price","fee","business_pnl","matched_quantity"].includes(key)) return num(row[key]);
    if (key === "asset_type") return row[key] === "option" ? "期权" : "期货";
    if (key === "business_type") return row.assignment_status === "classified" && row[key] ? `<span class="tm-tag blue">${esc(businessType(row[key]))}</span>` : '<span class="tm-tag amber">待确认</span>';
    return esc(row[key] ?? "—");
  }

  function factTable(items) {
    const columns = FACT_COLUMNS[tm.factsTab];
    const selectable = tm.factsTab === "trades" && tm.permissions.canEdit;
    return `<div class="tm-table-wrap"><table><thead><tr>${selectable ? "<th></th>" : ""}${columns.map(([,label]) => `<th>${label}</th>`).join("")}<th></th></tr></thead><tbody>${items.length ? items.map((row) => `<tr>${selectable ? `<td><input type="checkbox" data-select-row="${row.identity_id}" ${tm.selected.has(row.identity_id) ? "checked" : ""}></td>` : ""}${columns.map(([key]) => `<td class="${["quantity","average_price","margin","open_price","close_price","fact_close_pnl","matched_fee","price","fee"].includes(key) ? "tm-numeric" : key === "contract" ? "tm-contract" : ""}">${valueCell(row,key)}</td>`).join("")}<td><button class="tm-row-button" data-detail='${esc(JSON.stringify(row))}'>详情 →</button></td></tr>`).join("") : `<tr><td colspan="${columns.length + 2}" class="tm-empty-state">暂无数据</td></tr>`}</tbody></table></div>`;
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
    const selection = tm.factsTab === "trades" && tm.permissions.canEdit ? `<div class="tm-selection-bar"><span>已选择 ${tm.selected.size} 条</span><button id="tmSelectPage">选择当前页</button><button id="tmSelectFiltered">选择全部筛选结果</button><button id="tmClearSelection">清空选择</button><button id="tmClassify" class="tm-primary-button" ${tm.selected.size ? "" : "disabled"}>业务归属</button></div>` : "";
    $("#tmPositionsView").innerHTML = `<section class="tm-panel"><div class="tm-section-header"><div>${factTabs()}</div><div class="tm-toolbar">${tm.permissions.canSensitive ? '<button id="tmImportButton" class="tm-secondary-button">导入三表</button>' : ""}<span class="tm-tag blue">统一事实层</span></div></div>${filters(tm.factsTab === "trades")}${filterSummary(data.summary)}${selection}${factTable(data.items)}${pagination(data)}</section>`;
    wireFactActions(data);
    prefetchFactTabs();
  }

  function wireFactActions(data) {
    document.querySelectorAll("[data-fact-tab]").forEach((button) => button.addEventListener("click", () => { tm.factsTab = button.dataset.factTab; tm.page = 1; renderPositionsView().catch(showError); }));
    $("#tmSearchApply")?.addEventListener("click", () => { tm.query = $("#tmSearch").value.trim(); tm.page = 1; renderPositionsView().catch(showError); });
    [["#tmAssetType","assetType"],["#tmSide","side"],["#tmOpenClose","openClose"],["#tmClassification","classification"]].forEach(([selector,key]) => $(selector)?.addEventListener("change", (event) => { tm[key] = event.target.value; tm.page = 1; renderPositionsView().catch(showError); }));
    $("#tmDateFrom")?.addEventListener("change", (event) => { tm.dateFrom = event.target.value.replaceAll("-",""); renderPositionsView().catch(showError); });
    $("#tmDateTo")?.addEventListener("change", (event) => { tm.dateTo = event.target.value.replaceAll("-",""); renderPositionsView().catch(showError); });
    document.querySelectorAll("[data-select-row]").forEach((box) => box.addEventListener("change", () => { const id = Number(box.dataset.selectRow); box.checked ? tm.selected.add(id) : tm.selected.delete(id); renderPositionsView().catch(showError); }));
    $("#tmSelectPage")?.addEventListener("click", () => { data.items.forEach((row) => tm.selected.add(row.identity_id)); renderPositionsView().catch(showError); });
    $("#tmSelectFiltered")?.addEventListener("click", async () => {
      const first = await loadFactData("trades",{page:1,pageSize:100});
      first.items.forEach((row)=>tm.selected.add(row.identity_id));
      for (let page=2; page<=first.total_pages; page+=1) {
        const result = await loadFactData("trades",{page,pageSize:100});
        result.items.forEach((row)=>tm.selected.add(row.identity_id));
      }
      renderPositionsView().catch(showError);
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
    openDrawer("业务归属", `${tm.selected.size} 条已选择`, `<p class="tm-section-copy">一笔成交按完整手数归属，不允许拆分。</p><div class="tm-upload-grid"><label class="tm-upload-box"><span>业务归属</span><select id="tmBusinessSubject">${tm.config.subjects.map((item) => `<option value="${item.id}">${esc(item.name)}</option>`).join("")}</select></label><label class="tm-upload-box"><span>业务类型</span><select id="tmBusinessType"><option value="basic_hedging">基础套保</option><option value="strategic_hedging">战略套保</option></select></label><label class="tm-upload-box"><span>策略</span><input id="tmStrategy" list="tmStrategyList"><datalist id="tmStrategyList">${tm.config.strategies.map((item) => `<option value="${esc(item.name)}"></option>`).join("")}</datalist></label><label class="tm-upload-box"><span>指令/备注</span><textarea id="tmInstruction"></textarea></label><button id="tmSaveClassification" class="tm-primary-button">确认整笔归属</button></div>`);
    $("#tmSaveClassification").addEventListener("click", async () => { try { await api("/api/trading-management/business-assignments/batch-confirm", {method:"POST",body:JSON.stringify({identity_ids:[...tm.selected],business_subject_id:Number($("#tmBusinessSubject").value),business_type:$("#tmBusinessType").value,strategy_name:$("#tmStrategy").value.trim(),instruction_text:$("#tmInstruction").value.trim()})}); invalidateFactCache(); tm.selected.clear(); closeDrawer(); showToast("业务归属已保存"); await renderPositionsView(); } catch (error) { showError(error); } });
  }

  async function fileBase64(file) {
    const bytes = new Uint8Array(await file.arrayBuffer()); let binary = "";
    for (let index = 0; index < bytes.length; index += 0x8000) binary += String.fromCharCode(...bytes.subarray(index,index + 0x8000));
    return btoa(binary);
  }

  function setImportBusy(busy, message = "") {
    ["#tmImportAccount","#tmTradeFile","#tmCloseFile","#tmPositionFile","#tmImportCancel","#tmImportPreview","#tmImportConfirm"].forEach((selector) => { const element = $(selector); if (element) element.disabled = busy; });
    const progress = $("#tmImportProgress");
    if (progress && busy) {
      progress.className = "tm-import-progress";
      progress.innerHTML = `<span class="spinner"></span><span>${esc(message)}</span>`;
    } else if (progress && !progress.classList.contains("tm-import-error")) {
      progress.textContent = tm.importPreviewId ? "预检已完成，请核对结果后确认覆盖导入。" : "请选择同一账户的完整三表后预检。";
    }
  }

  function invalidateImportPreview() {
    tm.importPreviewId = null;
    $("#tmImportConfirm")?.classList.add("hidden");
    if ($("#tmImportResult")) $("#tmImportResult").textContent = "文件已变化，请重新预检。";
  }

  async function openImportDrawer() {
    await ensureConfig(); tm.importPreviewId = null;
    openDrawer("导入与核验", "文华三表", `<p class="tm-section-copy">错误导入通过重新导入完整三表覆盖；三份文件齐全后才能预检。</p><div class="tm-upload-grid"><label class="tm-upload-box"><span>交易账户</span><select id="tmImportAccount">${tm.config.accounts.map((item) => `<option value="${item.id}">${esc(item.display_name)}</option>`).join("")}</select></label><label class="tm-upload-box"><span>成交记录</span><input id="tmTradeFile" type="file" accept=".xlsx,.xlsm"></label><label class="tm-upload-box"><span>平仓记录</span><input id="tmCloseFile" type="file" accept=".xlsx,.xlsm"></label><label class="tm-upload-box"><span>期末持仓</span><input id="tmPositionFile" type="file" accept=".xlsx,.xlsm"></label><div id="tmImportProgress" class="tm-import-progress">请选择同一账户的完整三表后预检。</div><div id="tmImportResult" class="tm-section-copy"></div><div class="tm-drawer-actions"><button id="tmImportCancel">取消</button><button id="tmImportPreview" class="tm-primary-button">预检</button><button id="tmImportConfirm" class="tm-primary-button hidden">确认覆盖导入</button></div></div>`);
    ["#tmImportAccount","#tmTradeFile","#tmCloseFile","#tmPositionFile"].forEach((selector) => $(selector).addEventListener("change", invalidateImportPreview));
    $("#tmImportCancel").addEventListener("click", closeDrawer);
    $("#tmImportPreview").addEventListener("click", previewImport);
    $("#tmImportConfirm").addEventListener("click", confirmImport);
  }

  async function previewImport() {
    const files = [$("#tmTradeFile").files[0],$("#tmCloseFile").files[0],$("#tmPositionFile").files[0]];
    if (files.some((file) => !file)) return showError(new Error("成交、平仓、持仓三表必须齐全"));
    setImportBusy(true,"正在预检三表，请稍候");
    try {
      const encoded = await Promise.all(files.map(async (file) => ({name:file.name,content_base64:await fileBase64(file)})));
      const result = await api("/api/trading-management/imports/preview", {method:"POST",body:JSON.stringify({account_id:Number($("#tmImportAccount").value),trade_file:encoded[0],close_file:encoded[1],position_file:encoded[2]})});
      tm.importPreviewId = result.preview_batch_id;
      $("#tmImportResult").textContent = `预检通过：成交 ${result.counts.trade} 条，平仓 ${result.counts.close} 条，持仓 ${result.counts.position} 条；${result.range_start || "—"} 至 ${result.range_end || "—"}。`;
      $("#tmImportConfirm").classList.remove("hidden");
    } catch (error) { showError(error); } finally { setImportBusy(false); }
  }

  async function confirmImport() {
    if (!tm.importPreviewId) return showError(new Error("请先完成预检"));
    setImportBusy(true,"正在覆盖导入并建立事实匹配，请勿关闭窗口");
    try {
      const result = await api(`/api/trading-management/imports/${tm.importPreviewId}/confirm`, {method:"POST"});
      tm.importPreviewId = null; invalidateFactCache(); closeDrawer(); showToast(`导入完成：成交 ${result.counts.trade}，平仓 ${result.counts.close}，持仓 ${result.counts.position}`); await renderPositionsView();
    } catch (error) { showError(error); } finally { setImportBusy(false); }
  }

  function businessTabs(active, prefix) {
    return `<div class="tm-tabs">${[["positions","当前持仓"],["closes","平仓记录"],["trades","全部交易"]].map(([key,label]) => `<button class="tm-tab-button ${active === key ? "active" : ""}" data-${prefix}-tab="${key}">${label}</button>`).join("")}</div>`;
  }

  const BUSINESS_COLUMNS = {
    positions: [["contract","合约"],["asset_type","资产类型"],["direction","方向"],["quantity","手数"],["average_price","持仓均价"],["business_subject","业务归属"],["business_type","业务类型"],["strategy","策略"],["source_record_count","聚合记录"],["pending","浮动盈亏"]],
    closes: [["close_date","平仓日"],["contract","合约"],["open_side","方向"],["matched_quantity","手数"],["open_price","开仓价"],["close_price","平仓价"],["fact_close_pnl","事实平仓盈亏"],["business_pnl","业务归属盈亏"],["strategy","策略"]],
    trades: [["trade_date","成交日"],["contract","合约"],["side","方向"],["open_close","开平"],["quantity","手数"],["price","成交价"],["business_subject","业务归属"],["business_type","业务类型"],["strategy","策略"]],
  };

  function businessTable(data, tab, allowRematch) {
    const columns = BUSINESS_COLUMNS[tab];
    return `<div class="tm-table-wrap"><table><thead><tr>${columns.map(([,label]) => `<th>${label}</th>`).join("")}${allowRematch && tab === "closes" ? "<th>操作</th>" : ""}</tr></thead><tbody>${data.items.length ? data.items.map((row) => `<tr>${columns.map(([key]) => `<td>${valueCell(row,key)}</td>`).join("")}${allowRematch && tab === "closes" ? `<td><button class="tm-row-button" data-rematch-id="${row.identity_id}" data-version="${row.allocation_version || 1}">调整开平 →</button></td>` : ""}</tr>`).join("") : `<tr><td colspan="${columns.length + 1}" class="tm-empty-state">暂无数据</td></tr>`}</tbody></table></div>`;
  }

  function optionAnatomy(contract) {
    const match = String(contract || "").match(/^(.+)-(C|P)-(\d+(?:\.\d+)?)$/i);
    return match ? { underlying: match[1], kind: match[2].toUpperCase() === "C" ? "看涨" : "看跌", strike: match[3] } : { underlying: contract || "—", kind: "期权", strike: "—" };
  }

  function optionPositionTable(data) {
    const body = data.items.map((row) => {
      const anatomy = optionAnatomy(row.contract);
      return `<tr><td>${esc(row.contract)}</td><td>${esc(row.direction)}</td><td>持仓</td><td>${num(row.quantity)}</td><td>${num(row.average_price)}</td><td>${num(row.margin)}</td><td>${esc(anatomy.underlying)}</td><td>${anatomy.kind}</td><td>${anatomy.strike}</td><td>${pending()}</td><td>${pending()}</td><td>${pending()}</td><td>${pending()}</td></tr>`;
    }).join("");
    return `<div class="tm-table-wrap"><table><thead><tr><th>合约</th><th>方向</th><th>开平</th><th>手数</th><th>持仓均价</th><th>保证金</th><th>标的</th><th>看涨/看跌</th><th>行权价</th><th>Delta</th><th>Gamma</th><th>Theta</th><th>Vega</th></tr></thead><tbody>${body || '<tr><td colspan="13" class="tm-empty-state">暂无数据</td></tr>'}</tbody></table></div>`;
  }

  function businessFilters(view) {
    const dateValue = (value) => value ? `${value.slice(0,4)}-${value.slice(4,6)}-${value.slice(6)}` : "";
    return `<div class="tm-filters compact without-open-close"><input id="${view}Search" class="tm-filter-search" type="search" placeholder="搜索合约" value="${esc(tm.businessQuery[view])}"><button id="${view}SearchApply" class="tm-secondary-button">搜索</button><select id="${view}Side" class="tm-filter-select"><option value="">全部方向</option><option value="买" ${tm.businessSide[view] === "买" ? "selected" : ""}>买</option><option value="卖" ${tm.businessSide[view] === "卖" ? "selected" : ""}>卖</option></select><select id="${view}Classification" class="tm-filter-select"><option value="">全部归类状态</option><option value="unclassified" ${tm.businessClassification[view] === "unclassified" ? "selected" : ""}>待确认</option><option value="classified" ${tm.businessClassification[view] === "classified" ? "selected" : ""}>已归属</option></select><input id="${view}DateFrom" class="tm-filter-date" type="date" value="${dateValue(tm.dateFrom)}"><input id="${view}DateTo" class="tm-filter-date" type="date" value="${dateValue(tm.dateTo)}"></div>`;
  }

  function businessFilterSummary(summary, tab) {
    const items = tab === "positions"
      ? [["记录数",summary.record_count],["手数",summary.quantity],["业务归属盈亏",summary.business_pnl],["浮动盈亏","待计算"]]
      : [["记录数",summary.record_count],[tab === "closes" ? "平仓手数" : "成交手数",summary.quantity],["业务归属盈亏",summary.business_pnl],["手续费",summary.fee]];
    return `<div class="tm-filter-summary compact">${items.map(([label,value]) => `<div><span>${label}</span><strong>${typeof value === "number" ? num(value) : esc(value ?? "—")}</strong></div>`).join("")}</div>`;
  }

  async function renderBusinessLedger(view) {
    const tabKey = view === "junneng" ? "junnengTab" : "optionsTab";
    const tab = tm[tabKey];
    const pageKey = view === "junneng" ? "junnengPage" : "optionsPage";
    const pageSizeKey = view === "junneng" ? "junnengPageSize" : "optionsPageSize";
    const params = new URLSearchParams({page:tm[pageKey],page_size:tm[pageSizeKey]});
    if (tm.businessQuery[view]) params.set("contract",tm.businessQuery[view]);
    if (tm.businessSide[view]) params.set("direction",tm.businessSide[view]);
    if (tm.businessClassification[view]) params.set("classification",tm.businessClassification[view]);
    if (tm.dateFrom) params.set("start_date",tm.dateFrom);
    if (tm.dateTo) params.set("end_date",tm.dateTo);
    const data = await api(`/api/trading-management/business/${view}/${tab}?${params}`);
    const title = view === "junneng" ? "上海钧能台账" : "期权台账";
    const notice = view === "junneng" ? `默认展示全部 RB/HC 候选；待确认 ${data.candidates?.record_count || 0} 笔，明确归属其他业务后移出。` : "默认展示全部期权；Delta、Gamma、Theta、Vega 暂显示待计算。";
    const primarySummary = [["记录数",data.summary.record_count],["手数",data.summary.quantity],["业务归属盈亏",data.summary.business_pnl],["浮动盈亏","待计算"]];
    const riskSummary = [["Delta","待计算"],["Gamma","待计算"],["Theta","待计算"],["Vega","待计算"]];
    const summaryRow = (items, className) => `<div class="tm-ledger-summary ${className}">${items.map(([label,value]) => `<div><span>${label}</span><strong>${typeof value === "number" ? num(value) : esc(value ?? "—")}</strong></div>`).join("")}</div>`;
    const ledgerTable = view === "options" && tab === "positions" ? optionPositionTable(data) : businessTable(data,tab,tm.permissions.canEdit);
    const html = `<div class="tm-ledger-hero"><section class="tm-panel"><div class="tm-panel-header"><div><h2>${title}</h2><p class="tm-section-copy">${notice}</p></div><span class="tm-tag blue">${view === "junneng" ? "钢材套保业务" : "统一事实层 · 期权业务视图"}</span></div>${summaryRow(primarySummary,"tm-ledger-summary-primary")}${view === "options" ? summaryRow(riskSummary,"tm-ledger-summary-risk") : ""}</section></div><section class="tm-section tm-panel"><div class="tm-section-header">${businessTabs(tab,view)}<span class="tm-tag blue">${notice}</span></div>${businessFilters(view)}${businessFilterSummary(data.summary,tab)}${view === "options" && tab === "positions" ? '<div class="tm-filter-summary compact"><div><span>标的</span><strong>按合约解析</strong></div><div><span>看涨/看跌</span><strong>按 C/P 解析</strong></div><div><span>行权价</span><strong>按合约解析</strong></div><div><span>风险指标</span><strong>待计算</strong></div></div>' : ""}${ledgerTable}${pagination(data,view)}</section>`;
    $(view === "junneng" ? "#tmJunnengView" : "#tmOptionsView").innerHTML = html;
    document.querySelectorAll(`[data-${view}-tab]`).forEach((button) => button.addEventListener("click", () => { tm[tabKey] = button.dataset[`${view}Tab`]; tm[pageKey] = 1; renderBusinessLedger(view).catch(showError); }));
    $(`#${view}SearchApply`).addEventListener("click",()=>{tm.businessQuery[view]=$(`#${view}Search`).value.trim();tm[pageKey]=1;renderBusinessLedger(view).catch(showError);});
    [["Side",tm.businessSide],["Classification",tm.businessClassification]].forEach(([suffix,state])=>$(`#${view}${suffix}`).addEventListener("change",(event)=>{state[view]=event.target.value;tm[pageKey]=1;renderBusinessLedger(view).catch(showError);}));
    [["DateFrom","dateFrom"],["DateTo","dateTo"]].forEach(([suffix,key])=>$(`#${view}${suffix}`).addEventListener("change",(event)=>{tm[key]=event.target.value.replaceAll("-","");tm[pageKey]=1;renderBusinessLedger(view).catch(showError);}));
    $(`#${view}PageSize`).addEventListener("change",(event)=>{tm[pageSizeKey]=Number(event.target.value);tm[pageKey]=1;renderBusinessLedger(view).catch(showError);});
    $(`#${view}Prev`).addEventListener("click",()=>{tm[pageKey]-=1;renderBusinessLedger(view).catch(showError);});
    $(`#${view}Next`).addEventListener("click",()=>{tm[pageKey]+=1;renderBusinessLedger(view).catch(showError);});
    document.querySelectorAll("[data-rematch-id]").forEach((button) => button.addEventListener("click", () => openRematch(Number(button.dataset.rematchId),Number(button.dataset.version))));
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
    $("#tmDataInfoButton").addEventListener("click", () => openDrawer("数据说明","当前系统口径",`<div class="tm-quality-list">${qualityRow("数据来源","文华成交、平仓、期末持仓三表","事实层")}${qualityRow("业务归属","独立业务层，不改事实","可调整")}${qualityRow("真实交易操作","系统严格禁止","只读")}</div>`));
    document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeDrawer(); });
  }

  window.TradingManagement = {
    async activate(moduleCode, permissions) {
      bind(); tm.moduleCode = moduleCode; tm.permissions = permissions;
      const [title,subtitle,view] = VIEW_COPY[moduleCode]; tm.view = view;
      $("#tmPageTitle").textContent = title; $("#tmPageSubtitle").textContent = subtitle;
      await ensureConfig(); await refresh();
    },
  };
})();
