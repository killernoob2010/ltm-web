/* WH6 collector administration and provisional-fill read-only view. */
(function () {
  const state = { initialized: false, canManage: false, accountId: "" };
  const $ = (selector) => document.querySelector(selector);
  const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
  const statusLabel = (value) => ({ active: "正常", revoked: "已撤销", paused: "已暂停", provisional: "临时事实", duplicate_observation: "重复观察" }[value] || value || "—");

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
      <td>${esc(item.client_version || "—")}</td><td>${esc(statusLabel(item.status))}</td>
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

  function renderFills(items) {
    const body = $("#collectorFillsTable");
    $("#collectorFillCount").textContent = `共 ${items.length} 条`;
    body.innerHTML = items.map((item) => `<tr>
      <td>${esc(item.trade_date)}</td><td>${esc(item.trade_time || "—")}</td><td>${esc(item.exchange)}</td>
      <td>${esc(item.contract)}</td><td>${esc(item.side)}</td><td>${esc(item.open_close)}</td>
      <td>${esc(item.quantity)}</td><td>${esc(item.price)}</td><td>${esc(statusLabel(item.data_status))}</td>
    </tr>`).join("");
  }

  async function loadData() {
    if (!state.accountId) return;
    const [devices, fills] = await Promise.all([
      api(`/api/trading-collector/admin/devices?account_id=${encodeURIComponent(state.accountId)}`),
      api(`/api/trading-collector/fills?account_id=${encodeURIComponent(state.accountId)}&limit=100`),
    ]);
    renderDevices(devices.items || []);
    renderFills(fills.items || []);
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

  async function activate(options = {}) {
    state.canManage = Boolean(options.canManage);
    $("#collectorPairingBtn").classList.toggle("hidden", !state.canManage);
    if (!state.initialized) {
      $("#collectorPairingBtn").addEventListener("click", issuePairingCode);
      state.initialized = true;
    }
    try {
      await loadAccounts();
      await loadData();
    } catch (error) { setStatus(`加载失败：${error.message}`); }
  }

  window.TradingCollector = { activate };
})();
