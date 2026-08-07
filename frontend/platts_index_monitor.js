(function initPlattsIndexMonitor(global) {
  "use strict";

  const SERIES = [
    { key: "platts_lp", label: "LP", unit: "美元/干吨", decimals: 4, color: "#176b5d" },
    { key: "platts_61", label: "61%", unit: "美元/吨", decimals: 2, color: "#2962a9" },
    { key: "platts_58", label: "58%", unit: "美元/吨", decimals: 2, color: "#b05a2a" },
    { key: "platts_65", label: "65%", unit: "美元/吨", decimals: 2, color: "#7a4b9c" },
    { key: "spread_65_62", label: "65/62", unit: "美元/吨", decimals: 2, color: "#a23a52" },
    { key: "spread_65_61", label: "65/61", unit: "美元/吨", decimals: 2, color: "#4e6b35" },
  ];

  let runtime = { api: null, canSensitive: false };
  let draftToken = "";
  let draftRows = [];
  let bound = false;

  function currentMonth() {
    return new Date().toISOString().slice(0, 7);
  }

  function formatValue(value, series) {
    if (value === null || value === undefined || value === "") return "--";
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(series.decimals) : "--";
  }

  function setStatus(message, isError = false) {
    const status = document.querySelector("#plattsIndexImportStatus");
    if (status) {
      status.textContent = message;
      status.style.color = isError ? "var(--danger)" : "";
    }
  }

  function getMonth() {
    const input = document.querySelector("#plattsIndexMonth");
    return input?.value || currentMonth();
  }

  function renderReview(result) {
    const review = document.querySelector("#plattsIndexReview");
    const table = document.querySelector("#plattsIndexReviewTable");
    const message = document.querySelector("#plattsIndexReviewMessage");
    if (!review || !table) return;
    draftToken = result.draft_token || "";
    draftRows = (result.preview?.rows || []).map((row) => ({ ...row }));
    const issues = [
      ...(result.preview?.issues || []),
      ...(result.preview?.warnings || []),
      ...(result.preview?.conflicts || []).map((item) => ({ message: `${item.business_date} 已存在不同值` })),
    ];
    message.textContent = issues.length
      ? issues.map((item) => item.message).join("；")
      : "请核对 OCR 结果后填写复核说明。";
    table.replaceChildren();
    draftRows.forEach((row, rowIndex) => {
      const tr = document.createElement("tr");
      tr.dataset.rowIndex = String(rowIndex);
      const dateCell = document.createElement("td");
      dateCell.textContent = row.business_date || "";
      tr.appendChild(dateCell);
      for (const series of [
        { key: "platts_lp", decimals: 4 },
        { key: "platts_61", decimals: 2 },
        { key: "platts_58", decimals: 2 },
        { key: "platts_65", decimals: 2 },
        { key: "spread_61_62", decimals: 2 },
      ]) {
        const td = document.createElement("td");
        const input = document.createElement("input");
        input.type = "number";
        input.step = series.decimals === 4 ? "0.0001" : "0.01";
        input.value = row[series.key] ?? "";
        input.dataset.field = series.key;
        input.setAttribute("aria-label", `${row.business_date} ${series.key}`);
        td.appendChild(input);
        tr.appendChild(td);
      }
      table.appendChild(tr);
    });
    review.classList.remove("hidden");
  }

  function hideReview() {
    const review = document.querySelector("#plattsIndexReview");
    if (review) review.classList.add("hidden");
    draftToken = "";
    draftRows = [];
  }

  function canvasPoint(event, canvas) {
    const rect = canvas.getBoundingClientRect();
    const source = event.touches?.[0] || event;
    return { x: source.clientX - rect.left, y: source.clientY - rect.top };
  }

  function drawChart(canvas, tooltip, item) {
    const context = canvas.getContext("2d");
    if (!context) return;
    const width = Math.max(canvas.clientWidth, 260);
    const height = Math.max(canvas.clientHeight, 170);
    const ratio = global.devicePixelRatio || 1;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    const points = item.points || [];
    const padding = { left: 40, right: 12, top: 12, bottom: 28 };
    const chartWidth = width - padding.left - padding.right;
    const chartHeight = height - padding.top - padding.bottom;
    if (!points.length) {
      context.fillStyle = "#667486";
      context.font = "12px sans-serif";
      context.fillText("暂无已入库数据", padding.left, height / 2);
      return;
    }
    const numbers = points.map((point) => Number(point.value)).filter(Number.isFinite);
    if (Number.isFinite(Number(item.mtd))) numbers.push(Number(item.mtd));
    const minValue = Math.min(...numbers);
    const maxValue = Math.max(...numbers);
    const range = Math.max(maxValue - minValue, 0.01);
    const yMin = minValue - range * 0.12;
    const yMax = maxValue + range * 0.12;
    const xAt = (index) => padding.left + (points.length === 1 ? chartWidth / 2 : (chartWidth * index) / (points.length - 1));
    const yAt = (value) => padding.top + chartHeight * (1 - (value - yMin) / (yMax - yMin));
    context.strokeStyle = "#e7ebef";
    context.lineWidth = 1;
    for (let index = 0; index < 3; index += 1) {
      const y = padding.top + (chartHeight * index) / 2;
      context.beginPath();
      context.moveTo(padding.left, y);
      context.lineTo(width - padding.right, y);
      context.stroke();
    }
    context.fillStyle = "#667486";
    context.font = "10px sans-serif";
    context.fillText(formatValue(maxValue, SERIES.find((series) => series.key === item.key) || SERIES[0]), 2, padding.top + 4);
    context.fillText(formatValue(minValue, SERIES.find((series) => series.key === item.key) || SERIES[0]), 2, height - padding.bottom);
    if (Number.isFinite(Number(item.mtd))) {
      const mtdY = yAt(Number(item.mtd));
      context.setLineDash([4, 4]);
      context.strokeStyle = "#8a96a3";
      context.beginPath();
      context.moveTo(padding.left, mtdY);
      context.lineTo(width - padding.right, mtdY);
      context.stroke();
      context.setLineDash([]);
    }
    context.strokeStyle = item.color;
    context.lineWidth = 2;
    context.beginPath();
    points.forEach((point, index) => {
      const x = xAt(index);
      const y = yAt(Number(point.value));
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.stroke();
    context.fillStyle = item.color;
    points.forEach((point, index) => {
      context.beginPath();
      context.arc(xAt(index), yAt(Number(point.value)), 3, 0, Math.PI * 2);
      context.fill();
    });
    context.fillStyle = "#667486";
    context.fillText(String(points[0].date || "").slice(5), padding.left, height - 8);
    const lastDate = String(points[points.length - 1].date || "").slice(5);
    const lastWidth = context.measureText(lastDate).width;
    context.fillText(lastDate, width - padding.right - lastWidth, height - 8);
    const showTooltip = (event) => {
      const point = canvasPoint(event, canvas);
      const index = Math.max(0, Math.min(points.length - 1, Math.round(((point.x - padding.left) / chartWidth) * (points.length - 1))));
      const selected = points[index];
      tooltip.textContent = `${selected.date}：${formatValue(selected.value, item)}`;
      tooltip.style.left = `${Math.min(Math.max(point.x + 8, 4), width - 126)}px`;
      tooltip.style.top = `${Math.min(Math.max(point.y - 30, 4), height - 32)}px`;
      tooltip.classList.remove("hidden");
    };
    canvas.onclick = showTooltip;
    canvas.ontouchstart = showTooltip;
  }

  function renderCharts(summary) {
    const charts = document.querySelector("#plattsIndexCharts");
    if (!charts) return;
    charts.replaceChildren();
    for (const item of SERIES) {
      const series = summary.series?.[item.key] || { points: [], mtd: null };
      const card = document.createElement("article");
      card.className = "platts-chart-card";
      const header = document.createElement("header");
      const title = document.createElement("h3");
      title.textContent = item.label;
      const unit = document.createElement("span");
      unit.className = "platts-chart-unit";
      unit.textContent = series.unit || item.unit;
      header.append(title, unit);
      const meta = document.createElement("div");
      meta.className = "platts-chart-meta";
      const latest = series.points?.[series.points.length - 1];
      meta.textContent = `最新 ${latest?.date || "--"} ${latest ? formatValue(latest.value, item) : "--"}｜系统 MTD ${formatValue(series.mtd, item)}`;
      const wrap = document.createElement("div");
      wrap.className = "platts-chart-canvas-wrap";
      const canvas = document.createElement("canvas");
      canvas.className = "platts-chart-canvas";
      canvas.setAttribute("aria-label", `${item.label} MTD 折线图`);
      const tooltip = document.createElement("div");
      tooltip.className = "platts-chart-tooltip hidden";
      wrap.append(canvas, tooltip);
      card.append(header, meta, wrap);
      charts.appendChild(card);
      drawChart(canvas, tooltip, { ...item, ...series, key: item.key });
    }
  }

  function renderDaily(summary) {
    const table = document.querySelector("#plattsIndexDailyTable");
    const count = document.querySelector("#plattsIndexDailyCount");
    if (!table) return;
    table.replaceChildren();
    const rows = summary.rows || [];
    if (count) count.textContent = `${rows.length} 个交易日`;
    for (const row of rows) {
      const tr = document.createElement("tr");
      const values = [row.business_date, ...SERIES.map((item) => formatValue(row[item.key], item))];
      for (const value of values) {
        const td = document.createElement("td");
        td.textContent = value;
        tr.appendChild(td);
      }
      table.appendChild(tr);
    }
  }

  async function loadSummary() {
    if (!runtime.api) return;
    const month = getMonth();
    try {
      const summary = await runtime.api(`/api/platts-index/summary?month=${encodeURIComponent(month)}`);
      if (!summary.count && summary.latest_month && summary.latest_month !== month) {
        const monthInput = document.querySelector("#plattsIndexMonth");
        if (monthInput) monthInput.value = summary.latest_month;
        return loadSummary();
      }
      renderCharts(summary);
      renderDaily(summary);
      const mtdStatus = document.querySelector("#plattsIndexMtdStatus");
      if (mtdStatus) {
        const mtd = summary.mtd || {};
        const latestDate = summary.rows?.[summary.rows.length - 1]?.business_date || "--";
        mtdStatus.textContent = `系统 MTD（截至 ${latestDate}）：LP ${formatValue(mtd.platts_lp, SERIES[0])}｜61 ${formatValue(mtd.platts_61, SERIES[1])}｜58 ${formatValue(mtd.platts_58, SERIES[2])}｜65 ${formatValue(mtd.platts_65, SERIES[3])}`;
      }
      if (!document.querySelector("#plattsIndexReview")?.classList.contains("hidden")) return;
      const lastSuccess = summary.last_success_at ? `｜最近成功 ${summary.last_success_at}` : "";
      setStatus(`数据状态：已加载 ${summary.count || 0} 个交易日${lastSuccess}`);
    } catch (error) {
      setStatus(`数据状态：${error.message || "读取失败"}`, true);
    }
  }

  async function uploadSelectedFile(file) {
    if (!file || !runtime.api || !runtime.canSensitive) return;
    const uploadBtn = document.querySelector("#plattsIndexUploadBtn");
    uploadBtn.disabled = true;
    setStatus("数据状态：OCR 识别中");
    try {
      const fileData = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error("图片读取失败"));
        reader.readAsDataURL(file);
      });
      const result = await runtime.api("/api/platts-index/import/recognize", {
        method: "POST",
        body: JSON.stringify({ file_name: file.name, file_data: fileData }),
      });
      if (result.status === "review_required") {
        renderReview(result);
        setStatus("数据状态：待人工复核");
      } else if (result.status === "imported") {
        hideReview();
        setStatus(`数据状态：已入库 ${result.imported_count || 0} 行`);
        await loadSummary();
      } else {
        setStatus(`数据状态：${result.issues?.[0]?.message || "OCR 失败"}`, true);
      }
    } catch (error) {
      setStatus(`数据状态：${error.message || "OCR 失败"}`, true);
    } finally {
      uploadBtn.disabled = false;
    }
  }

  async function confirmReview() {
    if (!runtime.api || !draftToken) return;
    const confirmBtn = document.querySelector("#plattsIndexConfirmBtn");
    const reasonInput = document.querySelector("#plattsIndexReviewReason");
    const table = document.querySelector("#plattsIndexReviewTable");
    const rows = [...(table?.querySelectorAll("tr") || [])].map((tr) => {
      const row = { business_date: draftRows[Number(tr.dataset.rowIndex)]?.business_date };
      tr.querySelectorAll("input[data-field]").forEach((input) => { row[input.dataset.field] = input.value; });
      return row;
    });
    const reason = reasonInput?.value.trim() || "";
    if (!reason) {
      setStatus("数据状态：请填写复核说明", true);
      return;
    }
    confirmBtn.disabled = true;
    setStatus("数据状态：正在写入复核结果");
    try {
      const result = await runtime.api("/api/platts-index/import/confirm", {
        method: "POST",
        body: JSON.stringify({ draft_token: draftToken, rows, reason }),
      });
      hideReview();
      setStatus(`数据状态：复核入库 ${result.imported_count || 0} 行`);
      await loadSummary();
    } catch (error) {
      setStatus(`数据状态：${error.message || "复核失败"}`, true);
    } finally {
      confirmBtn.disabled = false;
    }
  }

  function bind() {
    if (bound) return;
    bound = true;
    document.querySelector("#plattsIndexUploadBtn")?.addEventListener("click", () => document.querySelector("#plattsIndexUploadFile")?.click());
    document.querySelector("#plattsIndexUploadFile")?.addEventListener("change", (event) => {
      const file = event.target.files?.[0];
      event.target.value = "";
      uploadSelectedFile(file);
    });
    document.querySelector("#plattsIndexMonth")?.addEventListener("change", loadSummary);
    document.querySelector("#plattsIndexConfirmBtn")?.addEventListener("click", confirmReview);
  }

  async function activate(options = {}) {
    if (typeof document === "undefined") return;
    runtime = { ...runtime, ...options };
    const month = document.querySelector("#plattsIndexMonth");
    if (month && !month.value) month.value = currentMonth();
    bind();
    const uploadBtn = document.querySelector("#plattsIndexUploadBtn");
    if (!runtime.canSensitive) {
      uploadBtn.disabled = true;
    } else {
      uploadBtn.disabled = false;
    }
    await loadSummary();
  }

  const api = { activate, calculateSeries: renderCharts };
  if (global) global.PlattsIndexMonitor = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
