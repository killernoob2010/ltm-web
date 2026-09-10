(() => {
  const ENDPOINT = "/api/admin/agent-quality";
  const SUMMARY_ENDPOINT = "/api/admin/agent-quality/summary";
  const RUNS_ENDPOINT = "/api/admin/agent-quality/runs";
  const EVALUATIONS_ENDPOINT = "/api/admin/agent-quality/evaluations";
  const page = document.querySelector("#agentQualityPage");
  if (!page) return;

  const state = {
    api: null,
    bound: false,
    page: 1,
    pageSize: 20,
    summary: null,
    runs: null,
    evaluations: null,
  };

  const $ = (selector) => page.querySelector(selector);
  const status = $("#agentQualityStatus");
  const filterForm = $("#agentQualityFilterForm");
  const startDate = $("#agentQualityStartDate");
  const endDate = $("#agentQualityEndDate");
  const stateFilter = $("#agentQualityState");
  const moduleFilter = $("#agentQualityModule");
  const cards = $("#agentQualityCards");
  const coverage = $("#agentQualityCoverage");
  const runsTable = $("#agentQualityRunsTable");
  const runCount = $("#agentQualityRunCount");
  const pagination = $("#agentQualityRunPagination");
  const detail = $("#agentQualityDetail");
  const evaluationNotice = $("#agentQualityEvaluationNotice");
  const evaluationList = $("#agentQualityEvaluationList");

  const labels = {
    queued: "排队",
    running: "运行中",
    succeeded: "成功",
    partial: "部分完成",
    failed: "失败",
    cancelled: "已取消",
    pending: "待运行",
    not_evaluated: "未评估",
    human_pass: "人工通过",
    human_fail: "人工未通过",
    human_review: "待人工复核",
  };

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function formatTimestamp(value) {
    if (!value) return "--";
    const text = String(value).replace("T", " ");
    return text.slice(0, 19);
  }

  function formatNumber(value) {
    return Number(value || 0).toLocaleString("zh-CN");
  }

  function statusChip(value) {
    const label = labels[value] || value || "--";
    const tone = value === "succeeded" || value === "human_pass" ? "good"
      : value === "failed" || value === "human_fail" ? "bad"
        : value === "partial" || value === "needs_review" || value === "human_review" ? "warn" : "";
    return `<span class="agent-quality-status ${tone}">${escapeHtml(label)}</span>`;
  }

  function queryString() {
    const params = new URLSearchParams();
    if (startDate.value) params.set("start_date", startDate.value);
    if (endDate.value) params.set("end_date", endDate.value);
    if (stateFilter.value) params.set("state", stateFilter.value);
    if (moduleFilter.value) params.set("module", moduleFilter.value);
    return params;
  }

  function setStatus(message, isError = false) {
    status.textContent = message || "";
    status.classList.toggle("error-text", isError);
  }

  function renderCards() {
    const runtime = state.summary?.runtime || {};
    const quality = state.summary?.quality || {};
    const evaluation = state.summary?.evaluation || {};
    const rate = runtime.technical_success_rate == null ? "--" : `${(runtime.technical_success_rate * 100).toFixed(1)}%`;
    const items = [
      ["近期运行", formatNumber(runtime.run_count), "运行记录"],
      ["技术成功率", rate, "成功结束 / 终态运行"],
      ["人工已评估", formatNumber(quality.evaluated_count), "质量反馈记录"],
      ["人工通过", formatNumber(quality.passed_count), "不等同于技术成功"],
      ["待复核", formatNumber(quality.needs_review_count), "需要人工判断"],
      ["真实模型评估", evaluation.real_model_evaluated ? "已执行" : "未记录", evaluation.release_readiness || "not_evaluated"],
    ];
    cards.innerHTML = items.map(([title, value, note]) => `<div class="agent-quality-card"><small>${escapeHtml(title)}</small><strong>${escapeHtml(value)}</strong><span>${escapeHtml(note)}</span></div>`).join("");
  }

  function renderCoverage() {
    const data = state.summary || {};
    const items = [
      ["运行态", data.coverage?.runtime_source || "--", "记录 Agent 是否排队、运行、完成、失败及工具调用次数。"],
      ["质量态", data.coverage?.quality_source || "--", "人工反馈单独记录，不回写或修改 Agent 原始答案。"],
      ["评估态", data.coverage?.evaluation_source || "--", `真实模型评估：${data.evaluation?.real_model_evaluated ? "已记录" : "未记录"}。`],
    ];
    coverage.innerHTML = items.map(([title, value, note]) => `<div class="agent-quality-coverage-item"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(value)}<br>${escapeHtml(note)}</span></div>`).join("");
  }

  function moduleText(items) {
    const names = { trading: "交易管理", data_visualization: "数据可视化", information_warning: "信息预警" };
    return (items || []).map((item) => names[item] || item).join("、") || "未识别";
  }

  function renderRuns() {
    const items = state.runs?.items || [];
    runCount.textContent = `共 ${formatNumber(state.runs?.pagination?.total || 0)} 条`;
    runsTable.innerHTML = items.length ? items.map((item) => `
      <tr>
        <td>#${escapeHtml(item.task_id)}</td>
        <td>${escapeHtml(formatTimestamp(item.created_at))}</td>
        <td>${statusChip(item.state)}</td>
        <td>${escapeHtml(moduleText(item.modules))}</td>
        <td>${escapeHtml(item.model_calls)} / ${escapeHtml(item.tool_calls)} / ${escapeHtml(item.search_calls)}</td>
        <td>${statusChip(item.quality_status)}</td>
        <td><button type="button" class="secondary agent-quality-detail-btn" data-run-id="${escapeHtml(item.task_id)}">查看</button></td>
      </tr>`).join("") : `<tr><td colspan="7" class="empty-state">当前筛选范围没有运行记录。</td></tr>`;
    const total = state.runs?.pagination?.total || 0;
    const totalPages = Math.max(1, Math.ceil(total / state.pageSize));
    pagination.innerHTML = `<span>第 ${state.page} / ${totalPages} 页</span>
      <button type="button" class="secondary" data-run-page="prev" ${state.page <= 1 ? "disabled" : ""}>上一页</button>
      <button type="button" class="secondary" data-run-page="next" ${state.page >= totalPages ? "disabled" : ""}>下一页</button>`;
  }

  function renderDetail(item) {
    if (!item) {
      detail.classList.add("hidden");
      detail.textContent = "";
      return;
    }
    const events = (item.events || []).map((event) => `<li>${escapeHtml(formatTimestamp(event.created_at))}｜${escapeHtml(event.kind)}｜${escapeHtml(event.tool_name || "")}${event.error_code ? `｜${escapeHtml(event.error_code)}` : ""}</li>`).join("");
    const feedback = item.feedback ? `<p>当前反馈：${statusChip(item.quality_status)}｜${escapeHtml(item.feedback.note || "")}</p>` : `<p>当前反馈：${statusChip(item.quality_status)}</p>`;
    detail.innerHTML = `<h3>任务 #${escapeHtml(item.task_id)} 详情</h3>
      <div class="agent-quality-detail-grid">
        <div class="agent-quality-detail-block"><strong>用户问题</strong><p>${escapeHtml(item.question || "未记录")}</p></div>
        <div class="agent-quality-detail-block"><strong>Agent回答</strong><p>${escapeHtml(item.answer || "尚未生成回答")}</p></div>
        <div class="agent-quality-detail-block"><strong>执行轨迹</strong><p><ul>${events || "<li>暂无事件</li>"}</ul></p></div>
        <div class="agent-quality-detail-block"><strong>运行状态</strong><p>${statusChip(item.state)}｜投递：${escapeHtml(item.delivery_state || "--")}<br>结束时间：${escapeHtml(formatTimestamp(item.finished_at))}</p></div>
      </div>
      <div class="agent-quality-feedback">${feedback}
        <button type="button" class="secondary" data-feedback="correct" data-task-id="${escapeHtml(item.task_id)}">标记通过</button>
        <button type="button" class="secondary" data-feedback="incorrect" data-task-id="${escapeHtml(item.task_id)}">标记未通过</button>
        <button type="button" class="secondary" data-feedback="needs_review" data-task-id="${escapeHtml(item.task_id)}">标记待复核</button>
      </div>`;
    detail.classList.remove("hidden");
  }

  function renderEvaluations() {
    const data = state.evaluations || {};
    const definition = data.definition || {};
    const items = [
      ["回归题库", `definition-regression`, definition.regression],
      ["Holdout题库", `definition-holdout`, definition.holdout],
      ["离线行为", `offline-behavior`, data.offline_behavior],
      ["真实模型", "live", data.live],
    ];
    const live = data.live || {};
    evaluationNotice.textContent = live.real_model_evaluated
      ? "已记录真实模型评估；请结合批次版本、数据快照和逐例结果判断是否可发布。"
      : "当前页面明确显示：题库定义和离线行为检查不代表真实模型已通过；真实模型评估尚未记录。";
    evaluationList.innerHTML = items.map(([title, id, item]) => `<article class="agent-quality-evaluation">
      <h3>${escapeHtml(title)}</h3>
      <p>状态：${escapeHtml(item?.status || "--")}<br>用例数：${escapeHtml(item?.count ?? "--")}<br>真实模型：${item?.real_model_evaluated ? "已执行" : "未执行"}</p>
      <button type="button" class="secondary" data-evaluation-id="${escapeHtml(id)}">查看批次</button>
    </article>`).join("");
  }

  async function showEvaluation(batchId) {
    try {
      const batch = await state.api(`${EVALUATIONS_ENDPOINT}/${encodeURIComponent(batchId)}`);
      evaluationNotice.textContent = `${batch.id}：${batch.status}；真实模型评估：${batch.real_model_evaluated ? "已执行" : "未执行"}。`;
      evaluationList.innerHTML = (batch.cases || []).slice(0, 100).map((item) => `<article class="agent-quality-evaluation">
        <h3>${escapeHtml(item.id || "未命名用例")}</h3>
        <p>状态：${escapeHtml(item.status || "not_run")}<br>${escapeHtml(item.question || "")}</p>
      </article>`).join("") || `<div class="agent-quality-evaluation">当前批次没有逐例结果。</div>`;
    } catch (error) {
      setStatus(error.message || "评估批次读取失败", true);
    }
  }

  async function loadRuns() {
    const params = queryString();
    params.set("page", String(state.page));
    params.set("page_size", String(state.pageSize));
    state.runs = await state.api(`${RUNS_ENDPOINT}?${params.toString()}`);
    renderRuns();
  }

  async function refresh() {
    setStatus("正在读取…");
    try {
      const params = queryString();
      const [summary, evaluations] = await Promise.all([
        state.api(`${SUMMARY_ENDPOINT}?${params.toString()}`),
        state.api(EVALUATIONS_ENDPOINT),
      ]);
      state.summary = summary;
      state.evaluations = evaluations;
      state.page = 1;
      renderCards();
      renderCoverage();
      renderEvaluations();
      await loadRuns();
      setStatus(`已更新至 ${formatTimestamp(summary.generated_at)}`);
    } catch (error) {
      setStatus(error.message || "质量页面加载失败", true);
    }
  }

  async function showDetail(taskId) {
    detail.textContent = "正在读取任务详情…";
    detail.classList.remove("hidden");
    try {
      renderDetail(await state.api(`${RUNS_ENDPOINT}/${encodeURIComponent(taskId)}`));
    } catch (error) {
      detail.textContent = error.message || "任务详情读取失败";
    }
  }

  async function submitFeedback(taskId, label) {
    const note = window.prompt("请输入反馈说明（可选）", "") ?? "";
    try {
      await state.api(`${RUNS_ENDPOINT}/${encodeURIComponent(taskId)}/feedback`, {
        method: "POST",
        body: JSON.stringify({ label, note }),
      });
      await refresh();
      await showDetail(taskId);
    } catch (error) {
      setStatus(error.message || "反馈保存失败", true);
    }
  }

  function setTab(tab) {
    page.querySelectorAll("[data-quality-tab]").forEach((button) => button.classList.toggle("active", button.dataset.qualityTab === tab));
    page.querySelectorAll("[data-quality-panel]").forEach((panel) => panel.classList.toggle("hidden", panel.dataset.qualityPanel !== tab));
  }

  function bind() {
    if (state.bound) return;
    state.bound = true;
    filterForm.addEventListener("submit", (event) => {
      event.preventDefault();
      refresh();
    });
    page.addEventListener("click", (event) => {
      const tab = event.target.closest("[data-quality-tab]");
      if (tab) {
        setTab(tab.dataset.qualityTab);
        return;
      }
      const detailButton = event.target.closest("[data-run-id]");
      if (detailButton) {
        showDetail(detailButton.dataset.runId);
        return;
      }
      const pageButton = event.target.closest("[data-run-page]");
      if (pageButton && !pageButton.disabled) {
        state.page += pageButton.dataset.runPage === "next" ? 1 : -1;
        loadRuns().catch((error) => setStatus(error.message, true));
        return;
      }
      const feedbackButton = event.target.closest("[data-feedback]");
      if (feedbackButton) {
        submitFeedback(feedbackButton.dataset.taskId, feedbackButton.dataset.feedback);
        return;
      }
      const evaluationButton = event.target.closest("[data-evaluation-id]");
      if (evaluationButton) {
        showEvaluation(evaluationButton.dataset.evaluationId);
      }
    });
  }

  async function activate(config) {
    if (!config || typeof config.api !== "function") return;
    state.api = config.api;
    bind();
    page.classList.remove("hidden");
    await refresh();
  }

  window.AgentQuality = { activate };
})();
