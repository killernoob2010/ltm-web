(function() {
  const tm = {
    initialized: false,
    moduleCode: "trading_overview",
    factsTab: "positions",
    businessTab: "positions",
    businessView: "junneng",
    page: 1,
    config: null,
    selectedTradeIds: new Set(),
    importPreviewId: null,
    rematch: null,
    permissions: { canEdit: false, canSensitive: false },
  };

  const $ = (selector) => document.querySelector(selector);
  const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[char]);
  const num = (value, digits = 2) => value == null || value === "" ? "-" : Number(value).toLocaleString("zh-CN", { maximumFractionDigits: digits });
  const businessType = (value) => ({ basic_hedging: "基础套保", strategic_hedging: "战略套保" })[value] || value || "-";
  const pending = () => '<span class="trading-pending">待计算</span>';

  function status(message, error = false) {
    const element = $("#tradingStatus");
    element.textContent = `交易数据：${message}`;
    element.style.color = error ? "var(--danger)" : "";
  }

  function setView(id) {
    ["#tradingOverviewView", "#tradingFactsView", "#tradingBusinessView", "#tradingExportView"]
      .forEach((selector) => $(selector).classList.toggle("hidden", selector !== id));
  }

  function syncTabs(kind, active) {
    document.querySelectorAll(`[data-trading-tabs="${kind}"] button`).forEach((button) => {
      button.classList.toggle("secondary", button.dataset.tab !== active);
    });
  }

  async function ensureConfig() {
    if (!tm.config) tm.config = await api("/api/trading-management/config");
    $("#tradingImportAccount").innerHTML = tm.config.accounts.map((item) =>
      `<option value="${item.id}">${esc(item.display_name || item.account_code)}</option>`).join("");
    $("#tradingBusinessSubject").innerHTML = tm.config.subjects.map((item) =>
      `<option value="${item.id}">${esc(item.name)}</option>`).join("");
    $("#tradingStrategyOptions").innerHTML = tm.config.strategies.map((item) =>
      `<option value="${esc(item.name)}"></option>`).join("");
  }

  async function loadOverview() {
    setView("#tradingOverviewView");
    const data = await api("/api/trading-management/overview");
    const cards = [
      ["成交记录", data.trades.record_count, `手续费 ${num(data.trades.fee)}`],
      ["平仓记录", data.closes.record_count, `事实盈亏 ${num(data.closes.fact_close_pnl)}`],
      ["当前持仓", data.positions.quantity, `保证金 ${num(data.positions.margin)}`],
      ["持仓浮动盈亏", "待计算", `快照日 ${data.positions.snapshot_date || "-"}`],
    ];
    $("#tradingOverviewCards").innerHTML = cards.map(([label, value, note]) =>
      `<article class="trading-metric-card"><span>${label}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></article>`).join("");
    $("#tradingDataStatus").innerHTML = [
      ["事实层", "文华三表真实数据，只读"],
      ["持仓口径", data.data_status.positions === "ok" ? "已取得最新持仓快照" : "暂无持仓快照"],
      ["浮盈与希腊字母", "初版统一显示待计算"],
    ].map(([label, value]) => `<div class="trading-status-item"><strong>${label}</strong><br>${value}</div>`).join("");
  }

  const FACT_COLUMNS = {
    positions: [["snapshot_date", "快照日"], ["contract", "合约"], ["direction", "方向"], ["quantity", "手数"], ["average_price", "均价"], ["margin", "保证金"], ["pending", "浮动盈亏"]],
    trades: [["trade_date", "成交日"], ["contract", "合约"], ["side", "买卖"], ["open_close", "开平"], ["quantity", "手数"], ["price", "成交价"], ["fee", "手续费"], ["fact_close_pnl", "事实盈亏"]],
    closes: [["close_date", "平仓日"], ["contract", "合约"], ["open_side", "开仓方向"], ["quantity", "手数"], ["open_price", "开仓价"], ["close_price", "平仓价"], ["fact_close_pnl", "事实盈亏"], ["matched_fee", "手续费"]],
  };

  function cell(item, key) {
    if (key === "pending") return pending();
    if (["quantity", "price", "average_price", "margin", "fee", "fact_close_pnl", "matched_fee", "open_price", "close_price", "business_pnl"].includes(key)) return num(item[key]);
    if (key === "business_type") return esc(businessType(item[key]));
    return esc(item[key] ?? "-");
  }

  function renderFacts(data) {
    const columns = FACT_COLUMNS[tm.factsTab];
    const selectable = tm.factsTab === "trades" && tm.permissions.canEdit;
    $("#tradingFactsHead").innerHTML = `<tr>${selectable ? '<th><input id="tradingSelectAll" type="checkbox"></th>' : ""}${columns.map(([, label]) => `<th>${label}</th>`).join("")}</tr>`;
    $("#tradingFactsBody").innerHTML = data.items.length ? data.items.map((item) => `<tr>${selectable ? `<td><input class="trading-trade-select" type="checkbox" value="${item.identity_id}" ${tm.selectedTradeIds.has(item.identity_id) ? "checked" : ""}></td>` : ""}${columns.map(([key]) => `<td>${cell(item, key)}</td>`).join("")}</tr>`).join("") : `<tr><td colspan="${columns.length + (selectable ? 1 : 0)}">暂无数据</td></tr>`;
    $("#tradingFactsSummary").textContent = `共 ${data.total_items} 条｜手数 ${num(data.summary.quantity)}｜手续费 ${num(data.summary.fee)}｜事实盈亏 ${num(data.summary.fact_close_pnl)}｜保证金 ${num(data.summary.margin)}`;
    $("#tradingFactsPagination").innerHTML = `<button type="button" class="secondary" data-page="${Math.max(1, data.page - 1)}" ${data.page <= 1 ? "disabled" : ""}>上一页</button><span>第 ${data.page} / ${data.total_pages} 页</span><button type="button" class="secondary" data-page="${Math.min(data.total_pages, data.page + 1)}" ${data.page >= data.total_pages ? "disabled" : ""}>下一页</button>`;
    document.querySelectorAll(".trading-trade-select").forEach((box) => box.addEventListener("change", () => {
      const id = Number(box.value);
      if (box.checked) tm.selectedTradeIds.add(id); else tm.selectedTradeIds.delete(id);
    }));
    $("#tradingSelectAll")?.addEventListener("change", (event) => document.querySelectorAll(".trading-trade-select").forEach((box) => {
      box.checked = event.target.checked;
      box.dispatchEvent(new Event("change"));
    }));
    document.querySelectorAll("#tradingFactsPagination button[data-page]").forEach((button) => button.addEventListener("click", () => {
      tm.page = Number(button.dataset.page); loadFacts().catch(showError);
    }));
  }

  async function loadFacts() {
    setView("#tradingFactsView");
    syncTabs("facts", tm.factsTab);
    $("#tradingClassifyBtn").classList.toggle("hidden", tm.factsTab !== "trades" || !tm.permissions.canEdit);
    const params = new URLSearchParams({ page: tm.page, page_size: 20 });
    const contract = $("#tradingContractFilter").value.trim();
    const direction = $("#tradingDirectionFilter").value;
    if (contract) params.set("contract", contract);
    if (direction) params.set("direction", direction);
    const data = await api(`/api/trading-management/facts/${tm.factsTab}?${params}`);
    renderFacts(data);
  }

  const BUSINESS_COLUMNS = {
    positions: [["contract", "合约"], ["direction", "方向"], ["quantity", "业务持仓"], ["average_price", "开仓均价"], ["business_subject", "业务归属"], ["business_type", "业务类型"], ["strategy", "策略"], ["pending", "浮动盈亏"]],
    trades: [["trade_date", "成交日"], ["contract", "合约"], ["side", "买卖"], ["open_close", "开平"], ["quantity", "手数"], ["price", "成交价"], ["business_subject", "业务归属"], ["business_type", "业务类型"], ["strategy", "策略"]],
    closes: [["close_date", "平仓日"], ["contract", "合约"], ["matched_quantity", "手数"], ["open_price", "事实开仓价"], ["close_price", "平仓价"], ["fact_close_pnl", "事实盈亏"], ["business_pnl", "业务盈亏"], ["strategy", "策略"], ["action", "操作"]],
  };

  async function loadBusiness() {
    setView("#tradingBusinessView");
    syncTabs("business", tm.businessTab);
    const data = await api(`/api/trading-management/business/${tm.businessView}/${tm.businessTab}?page_size=100`);
    const columns = BUSINESS_COLUMNS[tm.businessTab];
    $("#tradingBusinessHead").innerHTML = `<tr>${columns.map(([, label]) => `<th>${label}</th>`).join("")}</tr>`;
    $("#tradingBusinessBody").innerHTML = data.items.length ? data.items.map((item) => `<tr>${columns.map(([key]) => `<td>${key === "action" ? (tm.permissions.canEdit ? `<button type="button" class="link trading-rematch-btn" data-id="${item.identity_id}" data-version="${item.allocation_version || 1}">调整开平</button>` : "-") : cell(item, key)}</td>`).join("")}</tr>`).join("") : `<tr><td colspan="${columns.length}">暂无数据</td></tr>`;
    $("#tradingBusinessSummary").textContent = `共 ${data.total_items} 条｜手数 ${num(data.summary.quantity)}｜业务盈亏 ${num(data.summary.business_pnl)}｜事实盈亏 ${num(data.summary.fact_close_pnl)}｜浮动盈亏 待计算`;
    const candidateText = tm.businessView === "junneng" && data.candidates ? `｜RB/HC 待归属 ${data.candidates.record_count} 笔` : "";
    $("#tradingBusinessNotice").textContent = tm.businessView === "options" ? "默认展示全部期权；浮动盈亏及希腊字母暂显示待计算。" : `正式台账仅展示已归属上海钧能的数据${candidateText}。`;
    document.querySelectorAll(".trading-rematch-btn").forEach((button) => button.addEventListener("click", () => openRematch(Number(button.dataset.id), Number(button.dataset.version))));
  }

  function showError(error) {
    status(error.message || "加载失败", true);
  }

  async function fileBase64(file) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (let index = 0; index < bytes.length; index += 0x8000) {
      binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
    }
    return btoa(binary);
  }

  async function previewImport() {
    const files = [$("#tradingTradeFile").files[0], $("#tradingCloseFile").files[0], $("#tradingPositionFile").files[0]];
    if (files.some((file) => !file)) throw new Error("成交、平仓、持仓三表必须齐全");
    status("正在预检三表");
    const encoded = await Promise.all(files.map(async (file) => ({ name: file.name, content_base64: await fileBase64(file) })));
    const result = await api("/api/trading-management/imports/preview", {
      method: "POST",
      body: JSON.stringify({ account_id: Number($("#tradingImportAccount").value), trade_file: encoded[0], close_file: encoded[1], position_file: encoded[2] }),
    });
    tm.importPreviewId = result.preview_batch_id;
    $("#tradingImportPreview").innerHTML = `预检通过：成交 ${result.counts.trade} 条，平仓 ${result.counts.close} 条，持仓 ${result.counts.position} 条；数据范围 ${result.range_start || "-"} 至 ${result.range_end || "-"}。`;
    $("#tradingImportConfirmBtn").classList.remove("hidden");
    status("三表预检通过，等待确认");
  }

  async function confirmImport() {
    if (!tm.importPreviewId) throw new Error("请先完成预检");
    status("正在确认并建立事实匹配");
    const result = await api(`/api/trading-management/imports/${tm.importPreviewId}/confirm`, { method: "POST" });
    $("#tradingImportDialog").close();
    tm.importPreviewId = null;
    status(`导入完成：成交 ${result.counts.trade}，平仓 ${result.counts.close}，持仓 ${result.counts.position}`);
    await refresh();
  }

  async function confirmClassification() {
    const ids = [...tm.selectedTradeIds];
    if (!ids.length) throw new Error("请先选择需要归属的完整成交");
    await api("/api/trading-management/business-assignments/batch-confirm", {
      method: "POST",
      body: JSON.stringify({ identity_ids: ids, business_subject_id: Number($("#tradingBusinessSubject").value), business_type: $("#tradingBusinessType").value, strategy_name: $("#tradingStrategy").value.trim(), instruction_text: $("#tradingInstruction").value.trim() }),
    });
    tm.selectedTradeIds.clear();
    $("#tradingClassifyDialog").close();
    status(`已完成 ${ids.length} 笔完整成交的业务归属`);
    await loadFacts();
  }

  async function openRematch(closeId, version) {
    const result = await api(`/api/trading-management/business-closes/${closeId}/candidates`);
    tm.rematch = { closeId, version, preview: null };
    $("#tradingRematchCandidates").innerHTML = `<table><thead><tr><th>选择手数</th><th>开仓日</th><th>合约</th><th>方向</th><th>可平手数</th><th>开仓价</th><th>策略</th></tr></thead><tbody>${result.items.map((item) => `<tr><td><input class="trading-rematch-choice" type="number" min="0" max="${item.available_quantity}" step="1" value="0" data-id="${item.identity_id}"></td><td>${esc(item.trade_date)}</td><td>${esc(item.contract)}</td><td>${esc(item.side)}</td><td>${num(item.available_quantity)}</td><td>${num(item.price)}</td><td>${esc(item.strategy)}</td></tr>`).join("")}</tbody></table>`;
    $("#tradingRematchImpact").textContent = "选择开仓记录和手数后预览业务盈亏变化。";
    $("#tradingRematchConfirmBtn").classList.add("hidden");
    $("#tradingRematchReason").value = "";
    $("#tradingRematchDialog").showModal();
  }

  function rematchSelections() {
    return [...document.querySelectorAll(".trading-rematch-choice")].map((input) => ({ open_trade_identity_id: Number(input.dataset.id), quantity: Number(input.value) })).filter((item) => item.quantity > 0);
  }

  async function previewRematch() {
    const result = await api(`/api/trading-management/business-closes/${tm.rematch.closeId}/preview`, { method: "POST", body: JSON.stringify({ allocation_version: tm.rematch.version, selections: rematchSelections() }) });
    tm.rematch.preview = result;
    $("#tradingRematchImpact").textContent = `业务盈亏将从 ${num(result.before_business_pnl)} 调整为 ${num(result.after_business_pnl)}；事实层盈亏与开平关系不变。`;
    $("#tradingRematchConfirmBtn").classList.remove("hidden");
  }

  async function confirmRematch() {
    if (!tm.rematch?.preview) throw new Error("请先预览影响");
    const result = await api(`/api/trading-management/business-closes/${tm.rematch.closeId}/confirm`, { method: "POST", body: JSON.stringify({ preview_token: tm.rematch.preview.preview_token, allocation_version: tm.rematch.version, reason: $("#tradingRematchReason").value.trim() }) });
    $("#tradingRematchDialog").close();
    const reconciliation = result.reconciliation?.status === "business_pnl_reconciliation_failed"
      ? `；闭环核验差额 ${num(result.reconciliation.difference)}，待核验`
      : "";
    status(`业务开平关系已调整，相关持仓和平仓归属已同步更新${reconciliation}`);
    await loadBusiness();
  }

  async function restoreDefault() {
    await api(`/api/trading-management/business-closes/${tm.rematch.closeId}/restore-default`, { method: "POST", body: JSON.stringify({ allocation_version: tm.rematch.version, reason: "恢复事实层默认开平关系" }) });
    $("#tradingRematchDialog").close();
    status("已恢复事实层默认开平关系并同步业务持仓");
    await loadBusiness();
  }

  async function refresh() {
    status("正在加载");
    if (tm.moduleCode === "trading_overview") await loadOverview();
    else if (tm.moduleCode === "trading_positions") await loadFacts();
    else if (["trading_sh_junneng", "trading_options"].includes(tm.moduleCode)) await loadBusiness();
    else setView("#tradingExportView");
    status(`已更新 ${new Date().toLocaleTimeString("zh-CN")}`);
  }

  function bind() {
    if (tm.initialized) return;
    tm.initialized = true;
    $("#tradingRefreshBtn").addEventListener("click", () => refresh().catch(showError));
    $("#tradingImportBtn").addEventListener("click", () => ensureConfig().then(() => $("#tradingImportDialog").showModal()).catch(showError));
    $("#tradingImportCancelBtn").addEventListener("click", () => $("#tradingImportDialog").close());
    $("#tradingImportPreviewBtn").addEventListener("click", () => previewImport().catch(showError));
    $("#tradingImportConfirmBtn").addEventListener("click", () => confirmImport().catch(showError));
    $("#tradingFilterBtn").addEventListener("click", () => { tm.page = 1; loadFacts().catch(showError); });
    document.querySelectorAll('[data-trading-tabs="facts"] button').forEach((button) => button.addEventListener("click", () => { tm.factsTab = button.dataset.tab; tm.page = 1; tm.selectedTradeIds.clear(); loadFacts().catch(showError); }));
    document.querySelectorAll('[data-trading-tabs="business"] button').forEach((button) => button.addEventListener("click", () => { tm.businessTab = button.dataset.tab; loadBusiness().catch(showError); }));
    $("#tradingClassifyBtn").addEventListener("click", () => {
      if (!tm.selectedTradeIds.size) return showError(new Error("请先选择需要归属的完整成交"));
      $("#tradingClassifyCount").textContent = `已选择 ${tm.selectedTradeIds.size} 笔成交。系统按整笔归属，不允许按手数拆分。`;
      ensureConfig().then(() => $("#tradingClassifyDialog").showModal()).catch(showError);
    });
    $("#tradingClassifyCancelBtn").addEventListener("click", () => $("#tradingClassifyDialog").close());
    $("#tradingClassifyConfirmBtn").addEventListener("click", () => confirmClassification().catch(showError));
    $("#tradingRematchCancelBtn").addEventListener("click", () => $("#tradingRematchDialog").close());
    $("#tradingRematchPreviewBtn").addEventListener("click", () => previewRematch().catch(showError));
    $("#tradingRematchConfirmBtn").addEventListener("click", () => confirmRematch().catch(showError));
    $("#tradingRestoreDefaultBtn").addEventListener("click", () => restoreDefault().catch(showError));
  }

  window.TradingManagement = {
    async activate(moduleCode, permissions) {
      bind();
      tm.moduleCode = moduleCode;
      tm.permissions = permissions;
      tm.businessView = moduleCode === "trading_options" ? "options" : "junneng";
      $("#tradingImportBtn").classList.toggle("hidden", !permissions.canSensitive);
      await refresh();
    },
  };
})();
