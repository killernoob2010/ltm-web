(function() {
  "use strict";

  const moduleState = {
    api: null,
    token: "",
    canSensitive: false,
    fields: [],
    records: [],
    filters: {},
    view: "records",
    page: 1,
    pageSize: 20,
    total: 0,
    selectedRecord: null,
    bound: false,
  };

  const MANUAL_FIELDS = [
    "C", "K", "N", "O", "P", "R", "V", "W", "Y", "AA", "AC", "AE", "AH", "AI", "AJ", "AK", "AL", "AM", "AN", "AO", "long_contract_object",
  ];
  const REQUIRED_MANUAL_FIELDS = new Set(["C", "K", "N", "O", "Y"]);
  const DETAIL_HIDDEN_FIELDS = new Set(["A", "B", "AQ", "AR", "AS", "AT", "AV", "AW", "AX", "AY"]);
  const PLACEHOLDER_VALUES = new Set(["--", "***", "---", "**", "****", "—", "——"]);
  const NUMERIC_FIELDS = new Set(["N", "O", "Y", "AA", "AH", "AI", "AJ", "AK", "AL"]);
  const FIELD_LABELS = {
    long_contract_object: "长协对象",
  };
  const ADVANCED_FILTER_NAMES = [
    "from_date", "to_date", "product_name", "port", "operation_title", "supplier", "customer",
    "contract_number", "purchase_execution", "sales_execution", "purchase_quantity", "sales_quantity",
    "closed_state", "sync_error",
  ];
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
      const active = button.dataset.view === view;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
  }

  function updateAdvancedFilterCount() {
    const form = $("#spotLedgerFilterForm");
    const indicator = $("#spotLedgerAdvancedFilterCount");
    if (!form || !indicator) return;
    const count = ADVANCED_FILTER_NAMES.filter((name) => form.elements[name]?.value !== "").length;
    indicator.textContent = count ? `已启用 ${count} 项高级条件` : "";
    indicator.classList.toggle("hidden", count === 0);
  }

  function setAdvancedFiltersExpanded(expanded) {
    const advanced = $("#spotLedgerAdvancedFilters");
    const button = $("#spotLedgerToggleFiltersBtn");
    if (!advanced || !button) return;
    advanced.classList.toggle("hidden", !expanded);
    button.setAttribute("aria-expanded", String(expanded));
    $("#spotLedgerToggleFiltersText").textContent = expanded ? "收起更多" : "展开更多";
  }

  async function loadCounts() {
    const [pending, errors] = await Promise.all([
      moduleState.api("/api/spot-ledger/pending?limit=1"),
      moduleState.api("/api/spot-ledger/sync-errors?limit=1"),
    ]);
    $("#spotLedgerPendingCount").textContent = pending.count || 0;
    $("#spotLedgerErrorCount").textContent = errors.count || 0;
    const latest = errors.runs?.[0];
    if (latest) setSyncStatus(`${latest.status}｜${seconds(latest.finished_at || latest.started_at)}｜${latest.source_mode}`);
    else setSyncStatus("暂无任务记录；真实源认证仍待上线配置");
  }

  function renderPagination() {
    window.DataVisualizationComponents.renderPagination($("#spotLedgerPagination"), {
      page: moduleState.page,
      pageSize: moduleState.pageSize,
      total: moduleState.total,
      pageSizes: [20, 50, 100],
      onPageChange(page) {
        moduleState.page = page;
        loadView(moduleState.view);
      },
      onPageSizeChange(pageSize) {
        moduleState.pageSize = pageSize;
        moduleState.page = 1;
        loadView(moduleState.view);
      },
    });
  }

  function renderRows(records) {
    const body = $("#spotLedgerTableBody");
    if (!body) return;
    if (!records.length) {
      body.innerHTML = '<tr><td colspan="14" class="empty-cell">暂无符合条件的记录</td></tr>';
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
        <td>${escapeHtml(displayValue(record.AU))}</td>
        <td>${escapeHtml(supplierDisplayValue(record))}</td>
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
      const pageParams = { limit: moduleState.pageSize, offset: (moduleState.page - 1) * moduleState.pageSize };
      if (view === "pending") result = await moduleState.api(`/api/spot-ledger/pending${queryString(pageParams)}`);
      else if (view === "errors") result = await moduleState.api(`/api/spot-ledger/sync-errors${queryString(pageParams)}`);
      else result = await moduleState.api(`/api/spot-ledger/records${queryString({ ...moduleState.filters, ...pageParams })}`);
      moduleState.records = result.records || [];
      moduleState.total = Number(result.count ?? moduleState.records.length);
      if (result.field_definitions?.length) moduleState.fields = result.field_definitions;
      renderRows(moduleState.records);
      renderPagination();
      setStatus(`当前 ${moduleState.records.length} 条｜共 ${moduleState.total} 条`);
      if (view === "errors" && result.runs?.length) setSyncStatus(`${result.runs[0].status}｜${seconds(result.runs[0].finished_at || result.runs[0].started_at)}｜${result.runs[0].source_mode || "来源未标注"}`);
    } catch (error) {
      moduleState.records = [];
      moduleState.total = 0;
      renderRows([]);
      renderPagination();
      setStatus(error.message || "读取失败", true);
    }
  }

  function fieldDefinition(code) {
    return moduleState.fields.find((field) => field.code === code) || { code, name: FIELD_LABELS[code] || code, control: "手工", source_rule: "人工录入" };
  }

  function syncErrorText(value) {
    const errors = Array.isArray(value) ? value : value ? [value] : [];
    return errors.map((error) => {
      if (typeof error !== "object" || error === null) return String(error);
      return [error.field, error.message].filter(Boolean).join("：") || JSON.stringify(error);
    }).join("；");
  }

  function hasDisplayValue(value) {
    if (value === null || value === undefined || value === "") return false;
    return !(typeof value === "string" && PLACEHOLDER_VALUES.has(value.trim()));
  }

  function isRequiredField(field, record) {
    if (REQUIRED_MANUAL_FIELDS.has(field)) return true;
    if (field === "P") return record.D === "船货-落地";
    return field === "long_contract_object" && record.D === "船货-落地" && record.P === "是";
  }

  function requiredMarker(required) {
    return `<span class="spot-ledger-required-marker${required ? "" : " hidden"}" aria-hidden="true">*</span>`;
  }

  function detailFieldValue(record, field) {
    if (field.code === "Q") return supplierDisplayValue(record);
    return hasDisplayValue(record[field.code]) ? displayValue(record[field.code]) : "";
  }

  function supplierDisplayValue(record) {
    const legalName = hasDisplayValue(record.Q) ? displayValue(record.Q) : "";
    const alias = hasDisplayValue(record.supplier_display_name) ? displayValue(record.supplier_display_name) : "";
    if (alias && legalName && alias !== legalName) return `${alias}（法定全称：${legalName}）`;
    return alias || legalName || "";
  }

  function renderSystemField(field, record) {
    return `<div class="spot-ledger-system-row"><dt>${escapeHtml(field.name)}</dt><dd>${escapeHtml(detailFieldValue(record, field))}</dd></div>`;
  }

  function renderManualField(field, record) {
    const definition = fieldDefinition(field);
    const label = FIELD_LABELS[field] || definition.name;
    const required = isRequiredField(field, record);
    const value = hasDisplayValue(record[field]) ? displayValue(record[field]) : "";
    return `<div class="spot-ledger-manual-slot${value ? "" : " is-missing"}"><div class="spot-ledger-manual-label"><span>${escapeHtml(label)}</span>${requiredMarker(required)}</div><div class="spot-ledger-manual-value">${escapeHtml(value)}</div></div>`;
  }

  function renderDetail(record) {
    moduleState.selectedRecord = record;
    const detail = $("#spotLedgerDetail");
    detail.classList.remove("hidden");
    if (detail.showModal && !detail.open) detail.showModal();
    const errorText = syncErrorText(record.sync_error_summary);
    const systemFields = moduleState.fields.filter((field) => !MANUAL_FIELDS.includes(field.code) && !DETAIL_HIDDEN_FIELDS.has(field.code) && hasDisplayValue(record[field.code]));
    $("#spotLedgerDetailMeta").innerHTML = `<div><span>补录状态</span><strong>${escapeHtml(displayValue(record.supplement_status))}</strong></div><div><span>同步状态</span><strong>${escapeHtml(displayValue(record.sync_status))}</strong></div>${record.scope_status === "历史范围外" ? `<div><span>检查范围</span><strong>历史范围外：不纳入 2026 年补录与异常检查</strong></div>` : ""}${hasDisplayValue(record.last_synced_at) ? `<div><span>最近刷新</span><strong>${escapeHtml(seconds(record.last_synced_at))}</strong></div>` : ""}${errorText ? `<div class="spot-ledger-detail-alert"><span>同步异常</span><strong>${escapeHtml(errorText)}</strong></div>` : ""}`;
    $("#spotLedgerSystemCount").textContent = `${systemFields.length} 项`;
    $("#spotLedgerSystemFields").innerHTML = systemFields.length ? systemFields.map((field) => renderSystemField(field, record)).join("") : '<p class="spot-ledger-detail-empty">暂无已带出的系统字段</p>';
    $("#spotLedgerManualHint").textContent = record.scope_status === "历史范围外" ? "历史范围外，本轮不要求补录" : record.missing_fields?.length ? `待补录 ${record.missing_fields.length} 项` : "可按需修改";
    renderManualContent(record);
    $("#spotLedgerEditStatus").textContent = record.scope_status === "历史范围外" ? "历史范围外，本轮不要求补录" : record.missing_fields?.length ? `待补录：${record.missing_fields.join("、")}` : "必填字段已完成";
  }

  function closeDetail() {
    const detail = $("#spotLedgerDetail");
    if (!detail) return;
    if (detail.open && detail.close) detail.close();
    else detail.classList.add("hidden");
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
    const required = isRequiredField(field, record);
    const requiredAttribute = required ? " required" : "";
    const labelHtml = `<span class="spot-ledger-edit-label">${escapeHtml(label)}${requiredMarker(required)}</span>`;
    if (field === "C") return `<label>${labelHtml}<select name="${field}"${requiredAttribute}><option value="">空白</option><option value="自主建仓" ${current === "自主建仓" ? "selected" : ""}>自主建仓</option><option value="非自主建仓" ${current === "非自主建仓" ? "selected" : ""}>非自主建仓</option></select></label>`;
    if (field === "P") return `<label>${labelHtml}<select name="${field}"${requiredAttribute}><option value="">空白</option><option value="是" ${current === "是" ? "selected" : ""}>是</option><option value="否" ${current === "否" ? "selected" : ""}>否</option></select></label>`;
    if (field === "V") return `<label>${labelHtml}<select name="${field}"><option value="">空白</option><option value="其他钢厂" ${current === "其他钢厂" ? "selected" : ""}>其他钢厂</option><option value="贸易商" ${current === "贸易商" ? "selected" : ""}>贸易商</option><option value="子公司" ${current === "子公司" ? "selected" : ""}>子公司</option></select></label>`;
    const type = field === "AO" ? "date" : NUMERIC_FIELDS.has(field) ? "number" : "text";
    const step = NUMERIC_FIELDS.has(field) ? ' step="0.01"' : "";
    return `<label>${labelHtml}<input name="${field}" type="${type}"${step}${requiredAttribute} value="${escapeHtml(current)}"></label>`;
  }

  function syncRequiredInputs(form, record) {
    const draftRecord = { ...record, P: form.elements.P?.value || "" };
    MANUAL_FIELDS.forEach((field) => {
      const input = form.elements[field];
      if (!input) return;
      const required = isRequiredField(field, draftRecord);
      input.required = required;
      input.closest("label")?.querySelector(".spot-ledger-required-marker")?.classList.toggle("hidden", !required);
    });
  }

  function renderManualContent(record) {
    const form = $("#spotLedgerEditForm");
    const fields = $("#spotLedgerManualFields");
    const actions = $("#spotLedgerEditActions");
    if (!form || !fields || !actions) return;
    form.classList.remove("hidden");
    form.onsubmit = null;
    form.onchange = null;
    if (!moduleState.canSensitive) {
      form.classList.add("spot-ledger-edit-form-readonly");
      fields.className = "spot-ledger-manual-list";
      fields.innerHTML = MANUAL_FIELDS.map((field) => renderManualField(field, record)).join("");
      actions.classList.add("hidden");
      actions.innerHTML = "";
      return;
    }
    form.classList.remove("spot-ledger-edit-form-readonly");
    fields.className = "spot-ledger-edit-grid";
    fields.innerHTML = MANUAL_FIELDS.map((field) => editInput(field, record)).join("");
    actions.classList.remove("hidden");
    actions.innerHTML = '<button type="submit">保存人工字段</button><button id="spotLedgerResetEditBtn" type="button" class="secondary">重置本次修改</button>';
    form.onchange = () => syncRequiredInputs(form, record);
    syncRequiredInputs(form, record);
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
    $("#spotLedgerResetEditBtn").addEventListener("click", () => {
      form.reset();
      syncRequiredInputs(form, record);
    });
  }

  async function downloadExport() {
    const params = new URLSearchParams({ ...moduleState.filters, include_technical_key: "false" });
    const response = await fetch(`/api/spot-ledger/export?${params.toString()}`, { headers: { Authorization: `Bearer ${moduleState.token}` } });
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

  async function saveStrategy(event) {
    event?.preventDefault();
    const form = $("#spotLedgerStrategyForm");
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
  }

  function bind() {
    if (moduleState.bound) return;
    moduleState.bound = true;
    $("#spotLedgerFilterForm")?.addEventListener("submit", (event) => {
      event.preventDefault();
      moduleState.filters = filterValues();
      moduleState.page = 1;
      loadView("records");
    });
    $("#spotLedgerResetBtn")?.addEventListener("click", () => {
      $("#spotLedgerFilterForm")?.reset();
      moduleState.filters = {};
      moduleState.page = 1;
      setAdvancedFiltersExpanded(false);
      updateAdvancedFilterCount();
      loadView("records");
    });
    $("#spotLedgerToggleFiltersBtn")?.addEventListener("click", () => {
      const expanded = $("#spotLedgerToggleFiltersBtn").getAttribute("aria-expanded") === "true";
      setAdvancedFiltersExpanded(!expanded);
    });
    $("#spotLedgerFilterForm")?.addEventListener("input", updateAdvancedFilterCount);
    $("#spotLedgerFilterForm")?.addEventListener("change", updateAdvancedFilterCount);
    document.querySelectorAll(".spot-ledger-tab").forEach((button) => button.addEventListener("click", () => {
      moduleState.page = 1;
      loadView(button.dataset.view);
    }));
    $("#spotLedgerCloseDetailBtn")?.addEventListener("click", closeDetail);
    $("#spotLedgerDetail")?.addEventListener("close", () => {
      const form = $("#spotLedgerEditForm");
      if (form) {
        form.onsubmit = null;
        form.onchange = null;
      }
    });
    $("#spotLedgerExportBtn")?.addEventListener("click", () => downloadExport().catch((error) => setStatus(error.message, true)));
    $("#spotLedgerStrategyBtn")?.addEventListener("click", openStrategyDialog);
    $("#spotLedgerCancelStrategyBtn")?.addEventListener("click", () => $("#spotLedgerStrategyDialog")?.close());
    $("#spotLedgerStrategyForm")?.addEventListener("submit", saveStrategy);
    $("#spotLedgerSaveStrategyBtn")?.addEventListener("click", saveStrategy);
    setAdvancedFiltersExpanded(false);
    updateAdvancedFilterCount();
  }

  async function activate(config) {
    if (!config?.api) return;
    moduleState.api = config.api;
    moduleState.token = config.token || "";
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
