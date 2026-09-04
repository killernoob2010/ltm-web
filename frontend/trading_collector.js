/* WH6 collector administration and provisional-fill read-only view. */
(function () {
  const state = { initialized: false, canManage: false, accountId: "", fillPage: 1, fillPageSize: 20 };
  const $ = (selector) => document.querySelector(selector);
  const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
  const statusLabel = (value) => ({
    active: "正常",
    revoked: "已撤销",
    paused: "已暂停",
    provisional: "临时事实",
    settlement_covered: "月结已覆盖",
    settlement_conflict: "月结待核对",
    duplicate_observation: "重复观察",
  }[value] || value || "—");
  const environmentLabel = (value) => ({ staging: "测试版", production: "正式版" }[value] || value || "未知环境");

  function renderCollectionPolicy(policy) {
    const environment = String(policy.environment || "").toLowerCase();
    $("#collectorPolicyEnvironment").textContent = environmentLabel(environment);
    $("#collectorPolicyStatus").textContent = `历史起点：${policy.history_start_date || "—"}｜当前交易日：${policy.current_trade_date || "—"}｜策略修订：${policy.policy_revision || "—"}`;
    const closed = (policy.closed_ranges || []).map((item) => `${esc(item.range_start)} 至 ${esc(item.range_end)}（月结已覆盖）`);
    const uploadable = (policy.upload_ranges || []).map((item) => `${esc(item.range_start)} 至 ${esc(item.range_end)}（允许上传）`);
    const items = [
      ["已关闭历史区间", closed.length ? closed : ["暂无已关闭月结区间"]],
      ["当前允许上传区间", uploadable.length ? uploadable : ["暂无可上传历史区间；当前交易日仍按策略处理"]],
    ];
    $("#collectorPolicyRanges").innerHTML = items.map(([title, values]) => `<div class="collector-summary-group"><strong>${esc(title)}</strong>${values.map((value) => `<span>${value}</span>`).join("")}</div>`).join("");
  }

  function setStatus(message) {
    const node = $("#collectorStatus");
    if (node) node.textContent = message;
  }

  async function loadAccounts() {
    const select = $("#collectorAccountId");
    if (!select) return;
    const config = await api("/api/trading-management/config");
    const accounts = (config.accounts || []).filter((item) => item.account_code === "hongyuan_futures");
    select.innerHTML = accounts.map((item) => `<option value="${esc(item.id)}">${esc(item.masked_name || item.display_name || item.account_code)}</option>`).join("");
    state.accountId = select.value || "";
    select.onchange = () => { state.accountId = select.value; loadData().catch((error) => setStatus(`加载失败：${error.message}`)); };
  }

  function renderDevices(items) {
    const body = $("#collectorDevicesTable");
    $("#collectorDeviceCount").textContent = `共 ${items.length} 台`;
    body.innerHTML = items.map((item) => `<tr>
      <td>${esc(item.device_name)}</td><td>${esc(item.account_masked_name || item.account_display_name)}</td>
      <td>${esc(item.client_version || "—")}</td><td>${esc(environmentLabel(item.environment))}</td><td>${esc(statusLabel(item.status))}</td>
      <td>${esc(item.last_seen_at || "—")}</td>
      <td>${state.canManage && item.status === "active" ? `<button type="button" class="secondary collector-revoke" data-id="${esc(item.device_id)}">撤销</button>` : "—"}</td>
    </tr>`).join("");
    body.querySelectorAll(".collector-revoke").forEach((button) => button.addEventListener("click", async () => {
      if (!window.confirm("确认撤销该采集设备？撤销后设备只能重新连接。")) return;
      try {
        await api(`/api/trading-collector/admin/devices/${button.dataset.id}/revoke`, { method: "POST" });
        await loadData();
      } catch (error) { setStatus(`撤销失败：${error.message}`); }
    }));
  }

  function renderFillPagination(data) {
    const node = $("#collectorFillPagination");
    if (!node) return;
    const totalItems = Number(data.total_items || 0);
    const totalPages = Number(data.total_pages || 0);
    const page = totalPages ? Number(data.page || 1) : 1;
    node.innerHTML = `<span>共 ${esc(totalItems)} 条</span>
      <label>每页 <select id="collectorFillPageSize" aria-label="成交每页条数">${[20, 50, 100].map((size) => `<option value="${size}"${size === state.fillPageSize ? " selected" : ""}>${size}</option>`).join("")}</select> 条</label>
      <button type="button" class="secondary" id="collectorFillPrev"${page <= 1 ? " disabled" : ""}>上一页</button>
      <span>第 ${esc(page)} / ${esc(totalPages)} 页</span>
      <button type="button" class="secondary" id="collectorFillNext"${!totalPages || page >= totalPages ? " disabled" : ""}>下一页</button>`;
    $("#collectorFillPageSize").addEventListener("change", (event) => {
      state.fillPageSize = Number(event.target.value);
      state.fillPage = 1;
      loadFills().catch((error) => setStatus(`加载失败：${error.message}`));
    });
    $("#collectorFillPrev").addEventListener("click", () => {
      if (state.fillPage <= 1) return;
      state.fillPage -= 1;
      loadFills().catch((error) => setStatus(`加载失败：${error.message}`));
    });
    $("#collectorFillNext").addEventListener("click", () => {
      if (!totalPages || state.fillPage >= totalPages) return;
      state.fillPage += 1;
      loadFills().catch((error) => setStatus(`加载失败：${error.message}`));
    });
  }

  function renderFills(items, data) {
    const body = $("#collectorFillsTable");
    $("#collectorFillCount").textContent = `共 ${Number(data.total_items || 0)} 条`;
    body.innerHTML = items.map((item) => `<tr>
      <td>${esc(item.trade_date)}</td><td>${esc(item.trade_time || "—")}</td><td>${esc(item.exchange)}</td>
      <td>${esc(item.contract)}</td><td>${esc(item.side)}</td><td>${esc(item.open_close)}</td>
      <td>${esc(item.quantity)}</td><td>${esc(item.price)}</td><td>${esc(statusLabel(item.data_status))}</td>
    </tr>`).join("");
    renderFillPagination(data);
  }

  function renderOptionVolume(data) {
    const total = Number(data.total_quantity || 0);
    $("#collectorOptionVolumeTotal").textContent = `${total} 手`;
    const groups = [
      ["按合约", data.by_contract],
      ["按买卖", data.by_side],
      ["按开平", data.by_open_close],
      ["按 Call/Put", data.by_option_kind],
    ];
    $("#collectorOptionVolumeSummary").innerHTML = groups.map(([title, values]) => {
      const entries = Object.entries(values || {});
      return `<div class="collector-summary-group"><strong>${esc(title)}</strong>${entries.length
        ? entries.map(([key, value]) => `<span>${esc(key)}：${esc(value)} 手</span>`).join("")
        : `<span>暂无</span>`}</div>`;
    }).join("");
  }

  function renderCurrentPositions(data) {
    const items = (data.items || []).filter((item) => item.asset_type === "option");
    $("#collectorPositionCount").textContent = `共 ${items.length} 条`;
    const statusMessages = {
      expired: "持仓数据可能已过期",
      multi_device_conflict: "多设备持仓不一致",
      unavailable: "当前没有可验证的完整持仓快照",
    };
    const statusNode = $("#collectorPositionStatus");
    const message = data.message || statusMessages[data.source_status] || `快照状态：${data.source_status || "正常"}`;
    statusNode.textContent = `${message}${data.snapshot_timestamp ? `｜快照：${data.snapshot_timestamp}` : ""}`;
    statusNode.className = `collector-data-status ${data.is_expired || data.source_status === "multi_device_conflict" ? "is-warning" : ""}`;
    $("#collectorPositionsTable").innerHTML = items.map((item) => `<tr>
      <td>${esc(item.contract)}</td><td>${esc(item.direction)}</td><td>${esc(item.quantity)}</td>
      <td>${esc(item.today_quantity ?? "—")}</td><td>${esc(item.yesterday_quantity ?? "—")}</td>
      <td>${esc(item.average_price ?? "—")}</td><td>${esc(item.exchange)}</td>
    </tr>`).join("");
  }

  async function loadFills() {
    if (!state.accountId) return;
    const fills = await api(`/api/trading-collector/fills?account_id=${encodeURIComponent(state.accountId)}&page=${state.fillPage}&page_size=${state.fillPageSize}`);
    renderFills(fills.items || [], fills);
  }

  async function loadSummaryData() {
    if (!state.accountId) return;
    const [devices, optionVolume, currentPositions, collectionPolicy] = await Promise.all([
      api(`/api/trading-collector/admin/devices?account_id=${encodeURIComponent(state.accountId)}`),
      api(`/api/trading-collector/option-volume?account_id=${encodeURIComponent(state.accountId)}`),
      api(`/api/trading-collector/positions/current?account_id=${encodeURIComponent(state.accountId)}`),
      api(`/api/trading-collector/admin/collection-policy?account_id=${encodeURIComponent(state.accountId)}`),
    ]);
    renderDevices(devices.items || []);
    renderOptionVolume(optionVolume);
    renderCurrentPositions(currentPositions);
    renderCollectionPolicy(collectionPolicy);
  }

  async function loadData() {
    if (!state.accountId) return;
    await Promise.all([loadFills(), loadSummaryData()]);
    setStatus(`已更新｜${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`);
  }

  async function issuePairingCode() {
    if (!state.canManage || !state.accountId) return;
    try {
      const result = await api("/api/trading-collector/admin/pairing-codes", {
        method: "POST", body: JSON.stringify({ account_id: Number(state.accountId), ttl_seconds: 900 }),
      });
      $("#collectorPairingCode").textContent = `连接码：${result.code}（${result.expires_at} 前有效，仅显示一次）`;
      setStatus("连接码已生成");
    } catch (error) { setStatus(`生成失败：${error.message}`); }
  }

  async function reconcileExistingFills() {
    if (!state.canManage || !state.accountId) return;
    if (!window.confirm("确认仅在测试版协调已存盘中成交？此操作会写入协调审计和必要的结算编号回填，不修改 WH6 或真实交易。")) return;
    const button = $("#collectorReconcileBtn");
    button.disabled = true;
    setStatus("正在协调已存历史成交…");
    try {
      const result = await api("/api/trading-collector/admin/reconcile", {
        method: "POST",
        body: JSON.stringify({ account_id: Number(state.accountId) }),
      });
      setStatus(`历史协调完成｜扫描 ${Number(result.scanned || 0)} 条｜月结覆盖 ${Number(result.covered || 0)} 条`);
      await loadData();
    } catch (error) {
      setStatus(`协调失败：${error.message}`);
    } finally {
      button.disabled = false;
    }
  }

  async function activate(options = {}) {
    state.canManage = Boolean(options.canManage);
    $("#collectorPairingBtn").classList.toggle("hidden", !state.canManage);
    $("#collectorReconcileBtn").classList.toggle("hidden", !state.canManage);
    if (!state.initialized) {
      $("#collectorPairingBtn").addEventListener("click", issuePairingCode);
      $("#collectorReconcileBtn").addEventListener("click", reconcileExistingFills);
      state.initialized = true;
    }
    try {
      await loadAccounts();
      await loadData();
    } catch (error) { setStatus(`加载失败：${error.message}`); }
  }

  window.TradingCollector = { activate };
})();
