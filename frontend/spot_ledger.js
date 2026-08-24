(function() {
  "use strict";

  const moduleState = {
    api: null,
    canSensitive: false,
    fields: [],
    records: [],
    filters: {},
    view: "records",
    selectedRecord: null,
    bound: false,
  };

  const MANUAL_FIELDS = [
    "C", "K", "N", "O", "P", "R", "V", "W", "Y", "AA", "AC", "AE", "AH", "AI", "AJ", "AK", "AL", "AM", "AN", "AO", "long_contract_object",
  ];
  const NUMERIC_FIELDS = new Set(["N", "O", "Y", "AA", "AH", "AI", "AJ", "AK", "AL"]);
  const FIELD_LABELS = {
    long_contract_object: "长协对象",
  };
  const VIEW_LABELS = { records: "台账列表", pending: "待补录", errors: "同步异常" };

  function $(selector) {
    return document.querySelector(selector);
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function displayValue(value) {
    if (value === null || value === undefined || value === "") return "空白";
    if (Array.isArray(value)) return value.map((item) => typeof item === "object" ? JSON.stringify(item) : String(item)).join("；");
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  function seconds(value) {
    if (!value) return "";
    return String(value).replace("T", " ").slice(0, 19);
  }

  function setStatus(message, error) {
    const status = $("#spotLedgerListStatus");
    if (status) {
      status.textContent = message || "";
      status.classList.toggle("error-text", Boolean(error));
    }
  }

  function setSyncStatus(message) {
    const status = $("#spotLedgerSyncStatus");
    if (status) status.textContent = `同步状态：${message}`;
  }

  function filterValues() {
    const form = $("#spotLedgerFilterForm");
    if (!form) return {};
    const values = {};
    new FormData(form).forEach((value, key) => {
      if (value !== "") values[key] = value;
    });
    return values;
  }

  function queryString(values) {
    const params = new URLSearchParams();
    Object.entries(values || {}).forEach(([key, value]) => {
      if (value !== "" && value !== null && value !== undefined) params.set(key, value);
    });
    const text = params.toString();
    return text ? `?${text}` : "";
  }

  function setActiveTab(view) {
    moduleState.view = view;
    document.querySelectorAll(".spot-ledger-tab").forEach((button) => {
      button.classList.toggle("active", button.dataset.view === view);
    });
  }

  async function loadCounts() {
    const [pending, errors] = await Promise.all([
      moduleState.api("/api/spot-ledger/pending"),
      moduleState.api("/api/spot-ledger/sync-errors"),
    ]);
    $("#spotLedgerPendingCount").textContent = pending.count || 0;
    $("#spotLedgerErrorCount").textContent = errors.count || 0;
    const latest = errors.runs?.[0];
    if (latest) setSyncStatus(`${latest.status}｜${seconds(latest.finished_at || latest.started_at)}｜${latest.source_mode}`);
    else setSyncStatus("暂无任务记录；真实源认证仍待上线配置");
  }

  function renderRows(records) {
    const body = $("#spotLedgerTableBody");
    if (!body) return;
    if (!records.length) {
      body.innerHTML = '<tr><td colspan="12" class="empty-cell">暂无符合条件的记录</td></tr>';
      return;
    }
    body.innerHTML = records.map((record) => {
      const error = record.sync_status === "异常";
      return `<tr class="spot-ledger-record-row" data-record-id="${escapeHtml(record.record_id)}">
        <td>${escapeHtml(displayValue(record.AD))}</td>
        <td>${escapeHtml(displayValue(record.E))}</td>
        <td>${escapeHtml(displayValue(record.AP))}</td>
        <td>${escapeHtml(displayValue(record.D))}</td>
        <td>${escapeHtml(seconds(record.U))}</td>
        <td>${escapeHtml(displayValue(record.H))}</td>
        <td>${escapeHtml(displayValue(record.I))}</td>
        <td>${escapeHtml(displayValue(record.AB))}</td>
        <td>${escapeHtml(displayValue(record.L))}</td>
        <td>${escapeHtml(displayValue(record.X))}</td>
        <td><span class="spot-ledger-badge ${record.supplement_status === "待补录" ? "warning" : "success"}">${escapeHtml(record.supplement_status || "待补录")}</span></td>
        <td><span class="spot-ledger-badge ${error ? "danger" : "success"}">${escapeHtml(record.sync_status || "正常")}</span></td>
      </tr>`;
    }).join("");
    body.querySelectorAll(".spot-ledger-record-row").forEach((row) => {
      row.addEventListener("click", () => openRecord(row.dataset.recordId));
    });
  }

  async function loadView(view) {
    setActiveTab(view);
    setStatus("读取中…");
    try {
      let result;
      if (view === "pending") result = await moduleState.api("/api/spot-ledger/pending");
      else if (view === "errors") result = await moduleState.api("/api/spot-ledger/sync-errors");
      else result = await moduleState.api(`/api/spot-ledger/records${queryString(moduleState.filters)}`);
      moduleState.records = result.records || [];
      if (result.field_definitions?.length) moduleState.fields = result.field_definitions;
      renderRows(moduleState.records);
      setStatus(`当前 ${moduleState.records.length} 条`);
      if (view === "errors" && result.runs?.length) setSyncStatus(`${result.runs[0].status}｜${seconds(result.runs[0].finished_at || result.runs[0].started_at)}`);
    } catch (error) {
      moduleState.records = [];
      renderRows([]);
      setStatus(error.message || "读取失败", true);
    }
  }

  function fieldDefinition(code) {
    return moduleState.fields.find((field) => field.code === code) || { code, name: FIELD_LABELS[code] || code, control: "手工", source_rule: "人工录入" };
  }

  function renderDetail(record) {
    moduleState.selectedRecord = record;
    const detail = $("#spotLedgerDetail");
    detail.classList.remove("hidden");
    $("#spotLedgerDetailMeta").innerHTML = `<div><span>明细 ID</span><strong>${escapeHtml(displayValue(record.source_detail_id))}</strong></div><div><span>来源类型</span><strong>${escapeHtml(displayValue(record.record_source_type))}</strong></div><div><span>补录状态</span><strong>${escapeHtml(displayValue(record.supplement_status))}</strong></div><div><span>同步状态</span><strong>${escapeHtml(displayValue(record.sync_status))}</strong></div><div><span>最近同步</span><strong>${escapeHtml(seconds(record.last_synced_at) || "空白")}</strong></div>`;
    $("#spotLedgerFieldDefinitions").innerHTML = moduleState.fields.map((field) => `<article class="spot-ledger-field-card"><div class="spot-ledger-field-card-head"><span>${escapeHtml(field.code)}</span><strong>${escapeHtml(field.name)}</strong></div><div class="spot-ledger-field-value">${escapeHtml(displayValue(record[field.code]))}</div><small>${escapeHtml(field.control)} · ${escapeHtml(field.source_rule)}</small></article>`).join("");
    const editButton = $("#spotLedgerEditBtn");
    editButton.classList.toggle("hidden", !moduleState.canSensitive);
    editButton.textContent = "编辑人工字段";
    $("#spotLedgerEditForm").classList.add("hidden");
    $("#spotLedgerEditStatus").textContent = record.missing_fields?.length ? `待补录：${record.missing_fields.join("、")}` : "必填字段已完成";
  }

  async function openRecord(recordId) {
    try {
      const result = await moduleState.api(`/api/spot-ledger/records/${encodeURIComponent(recordId)}`);
      moduleState.fields = result.fields || moduleState.fields;
      renderDetail(result.record);
    } catch (error) {
      setStatus(error.message || "详情读取失败", true);
    }
  }

  function editInput(field, record) {
    const definition = fieldDefinition(field);
    const label = FIELD_LABELS[field] || definition.name;
    const current = record[field] == null ? "" : record[field];
    if (field === "C") return `<label>${escapeHtml(label)}<select name="${field}"><option value="">空白</option><option value="自主建仓" ${current === "自主建仓" ? "selected" : ""}>自主建仓</option><option value="非自主建仓" ${current === "非自主建仓" ? "selected" : ""}>非自主建仓</option></select></label>`;
    if (field === "P") return `<label>${escapeHtml(label)}<select name="${field}"><option value="">空白</option><option value="是" ${current === "是" ? "selected" : ""}>是</option><option value="否" ${current === "否" ? "selected" : ""}>否</option></select></label>`;
    if (field === "V") return `<label>${escapeHtml(label)}<select name="${field}"><option value="">空白</option><option value="其他钢厂" ${current === "其他钢厂" ? "selected" : ""}>其他钢厂</option><option value="贸易商" ${current === "贸易商" ? "selected" : ""}>贸易商</option><option value="子公司" ${current === "子公司" ? "selected" : ""}>子公司</option></select></label>`;
    const type = field === "AO" ? "date" : NUMERIC_FIELDS.has(field) ? "number" : "text";
    const step = NUMERIC_FIELDS.has(field) ? ' step="0.01"' : "";
    return `<label>${escapeHtml(label)}<input name="${field}" type="${type}"${step} value="${escapeHtml(current)}"></label>`;
  }

  function showEditForm() {
    const record = moduleState.selectedRecord;
    if (!record || !moduleState.canSensitive) return;
    const form = $("#spotLedgerEditForm");
    form.innerHTML = `<div class="spot-ledger-edit-grid">${MANUAL_FIELDS.map((field) => editInput(field, record)).join("")}</div><div class="dialog-actions"><button type="submit">保存人工字段</button><button id="spotLedgerCancelEditBtn" type="button" class="secondary">取消</button></div>`;
    form.classList.remove("hidden");
    $("#spotLedgerEditBtn").textContent = "收起编辑";
    form.onsubmit = async (event) => {
      event.preventDefault();
      const values = {};
      new FormData(form).forEach((value, key) => {
        if (NUMERIC_FIELDS.has(key) && value !== "") values[key] = Number(value);
        else values[key] = value;
      });
      try {
        await moduleState.api(`/api/spot-ledger/records/${encodeURIComponent(record.record_id)}`, { method: "PATCH", body: JSON.stringify({ values }) });
        await openRecord(record.record_id);
        await loadView(moduleState.view);
        await loadCounts();
        $("#spotLedgerEditStatus").textContent = "人工字段已保存；未记录字段修改审计。";
      } catch (error) {
        $("#spotLedgerEditStatus").textContent = error.message || "保存失败";
      }
    };
    $("#spotLedgerCancelEditBtn").addEventListener("click", () => {
      form.classList.add("hidden");
      $("#spotLedgerEditBtn").textContent = "编辑人工字段";
    });
  }

  async function downloadExport() {
    const params = new URLSearchParams({ ...moduleState.filters, include_technical_key: "false" });
    const token = localStorage.getItem("token") || "";
    const response = await fetch(`/api/spot-ledger/export?${params.toString()}`, { headers: { Authorization: `Bearer ${token}` } });
    if (!response.ok) throw new Error("导出失败");
    const blob = await response.blob();
    const link = document.createElement("a");
    link.className = "spot-ledger-export";
    link.href = URL.createObjectURL(blob);
    link.download = "现货业务台账.xlsx";
    link.click();
    URL.revokeObjectURL(link.href);
    setStatus("Excel 已生成，列顺序为 A:AY");
  }

  function openStrategyDialog() {
    const dialog = $("#spotLedgerStrategyDialog");
    if (dialog?.showModal) dialog.showModal();
    else dialog?.classList.remove("hidden");
  }

  function bind() {
    if (moduleState.bound) return;
    moduleState.bound = true;
    $("#spotLedgerFilterForm")?.addEventListener("submit", (event) => {
      event.preventDefault();
      moduleState.filters = filterValues();
      loadView("records");
    });
    $("#spotLedgerResetBtn")?.addEventListener("click", () => {
      $("#spotLedgerFilterForm")?.reset();
      moduleState.filters = {};
      loadView("records");
    });
    document.querySelectorAll(".spot-ledger-tab").forEach((button) => button.addEventListener("click", () => loadView(button.dataset.view)));
    $("#spotLedgerCloseDetailBtn")?.addEventListener("click", () => $("#spotLedgerDetail")?.classList.add("hidden"));
    $("#spotLedgerEditBtn")?.addEventListener("click", () => {
      const form = $("#spotLedgerEditForm");
      if (form.classList.contains("hidden")) showEditForm();
      else {
        form.classList.add("hidden");
        $("#spotLedgerEditBtn").textContent = "编辑人工字段";
      }
    });
    $("#spotLedgerExportBtn")?.addEventListener("click", () => downloadExport().catch((error) => setStatus(error.message, true)));
    $("#spotLedgerStrategyBtn")?.addEventListener("click", openStrategyDialog);
    $("#spotLedgerCancelStrategyBtn")?.addEventListener("click", () => $("#spotLedgerStrategyDialog")?.close());
    $("#spotLedgerStrategyForm")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const data = Object.fromEntries(new FormData(form).entries());
      const payload = {
        ...data,
        open_quantity: Number(data.open_quantity),
        open_price: Number(data.open_price),
        close_quantity: data.close_quantity ? Number(data.close_quantity) : null,
        close_price: data.close_price ? Number(data.close_price) : null,
        closed_at: data.closed_at || null,
      };
      try {
        const result = await moduleState.api("/api/spot-ledger/strategic-hedging", { method: "POST", body: JSON.stringify(payload) });
        $("#spotLedgerStrategyStatus").textContent = `已保存：${result.record.strategic_status}｜${seconds(result.record.strategic_opened_at)}｜${displayValue(result.record.strategic_contract)}`;
        setStatus("战略套保记录已保存");
        form.reset();
      } catch (error) {
        $("#spotLedgerStrategyStatus").textContent = error.message || "保存失败";
      }
    });
  }

  async function activate(config) {
    if (!config?.api) return;
    moduleState.api = config.api;
    moduleState.canSensitive = Boolean(config.canSensitive);
    bind();
    $("#spotLedgerExportBtn")?.classList.toggle("hidden", !moduleState.canSensitive);
    $("#spotLedgerStrategyBtn")?.classList.toggle("hidden", !moduleState.canSensitive);
    try {
      const definitions = await moduleState.api("/api/spot-ledger/field-definitions");
      moduleState.fields = definitions.fields || [];
      await loadView(moduleState.view);
      await loadCounts();
    } catch (error) {
      setStatus(error.message || "现货台账加载失败", true);
    }
  }

  window.SpotLedger = { activate, seconds };
})();
