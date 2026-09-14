(function() {
  "use strict";

  const ENDPOINT = "/api/closing-review-agent";
  const V2_ENDPOINT = "/api/trading-agent-v2";
  const DEFAULT_CONVERSATION_TITLE = "智能贸易助手对话";
  const LEGACY_DEFAULT_CONVERSATION_TITLE = "交易持仓助手对话";
  const legacyConversationEndpoint = `${ENDPOINT}/conversations`;
  const state = {
    api: null,
    user: null,
    conversations: [],
    conversationId: null,
    conversationStates: new Map(),
    bound: false,
    activation: 0,
    selectionSequence: 0,
    v2: false,
    creating: false,
    deleting: new Set(),
    undoRecords: [],
    answerRenders: [],
  };

  const $ = (id) => document.getElementById(id);
  const page = $("closingReviewAgentPage");
  const history = $("closingReviewHistory");
  const messages = $("closingReviewMessages");
  const composer = $("closingReviewComposer");
  const input = $("closingReviewInput");
  const sendButton = $("closingReviewSendBtn");
  const newButton = $("closingReviewNewBtn");
  const status = $("closingReviewStatus");
  const scopeNote = $("closingReviewScopeNote");

  function endpoint() {
    return state.v2 ? V2_ENDPOINT : ENDPOINT;
  }

  function timestampSeconds(value) {
    if (!value) return "";
    const raw = String(value);
    return raw.length >= 19 ? raw.slice(0, 19).replace("T", " ") : raw;
  }

  function requestId() {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }
    return `closing-review-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function conversationKey(conversationId) {
    return conversationId == null ? "blank" : String(conversationId);
  }

  function conversationState(conversationId) {
    const key = conversationKey(conversationId);
    if (!state.conversationStates.has(key)) {
      state.conversationStates.set(key, {
        conversationId,
        items: [],
        draft: "",
        scrollTop: 0,
        followOutput: true,
        activeTask: null,
        taskId: null,
        loading: false,
        pending: null,
        progress: null,
        announcedProgress: "",
        requestSequence: 0,
        pollingTaskId: null,
      });
    }
    return state.conversationStates.get(key);
  }

  function currentConversationState() {
    return state.conversationId == null ? conversationState(null) : conversationState(state.conversationId);
  }

  function isActive(conversationId, activation) {
    return activation === state.activation && Number(conversationId) === Number(state.conversationId);
  }

  function saveCurrentDraft() {
    if (!input) return;
    const current = currentConversationState();
    current.draft = input.value || "";
    if (messages) current.scrollTop = messages.scrollTop;
  }

  function restoreCurrentDraft() {
    if (!input) return;
    input.value = currentConversationState().draft || "";
  }

  function updateControls() {
    const current = state.conversationId == null ? null : currentConversationState();
    const otherRunning = [...state.conversationStates.values()].some((item) =>
      item.conversationId != null && Number(item.conversationId) !== Number(state.conversationId) && item.loading,
    );
    if (sendButton) {
      sendButton.disabled = !current || current.loading || otherRunning;
      sendButton.title = otherRunning ? "已有其他对话正在生成回答" : "发送问题";
    }
    if (newButton) newButton.disabled = state.creating;
  }

  function setStatus(message, kind = "") {
    if (!status) return;
    status.textContent = message || "";
    status.className = `closing-review-agent-status ${kind}`.trim();
    renderUndoRecords();
    updateControls();
  }

  function clear(element) {
    if (element) element.replaceChildren();
  }

  function addText(parent, tag, className, value) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    element.textContent = value == null ? "" : String(value);
    parent.appendChild(element);
    return element;
  }

  function statusLabel(value) {
    return {
      complete: "已回答 · 正确性未评估",
      partial: "部分结果",
      failed: "处理失败",
      waiting_for_data: "等待数据",
      data_anomaly: "数据异常",
      processing: "处理中",
      temporarily_unavailable: "暂时不可用",
      unsupported: "暂不支持",
      needs_clarification: "需要澄清",
    }[value] || value || "待处理";
  }

  function messageLabel(message) {
    if (message.message_type === "automatic_result") return "自动持仓结果";
    if (message.role === "user") return "我的问题";
    if (message.message_type === "error") return "Agent 状态";
    return "Agent 回答";
  }

  function displayConversationTitle(value) {
    const title = String(value || "").trim();
    if (!title || title === LEGACY_DEFAULT_CONVERSATION_TITLE) return DEFAULT_CONVERSATION_TITLE;
    return title;
  }

  function historyStatusLabel(conversation) {
    const current = conversationState(conversation.id);
    const taskState = current.activeTask ? String(current.activeTask.state || "") : "";
    if (current.loading || taskState === "queued" || taskState === "running") return "处理中";
    return conversation.status === "active" ? "可继续" : "已归档";
  }

  function moveTab(index, delta) {
    if (!state.conversations.length) return;
    const next = Math.max(0, Math.min(state.conversations.length - 1, index + delta));
    const conversation = state.conversations[next];
    if (conversation) selectConversation(conversation.id);
  }

  function focusTab(conversationId) {
    const tab = document.getElementById(`closingReviewTab-${conversationId}`);
    if (tab && typeof tab.focus === "function") tab.focus();
  }

  function renderHistory() {
    clear(history);
    if (!state.conversations.length) {
      addText(history, "p", "closing-review-agent-empty", "暂无对话，点击右侧“＋”开始使用智能贸易助手。");
      return;
    }
    state.conversations.forEach((conversation, index) => {
      const selected = Number(conversation.id) === Number(state.conversationId);
      const itemState = conversationState(conversation.id);
      const row = document.createElement("div");
      row.className = "closing-review-agent-history-row";
      const item = document.createElement("button");
      item.type = "button";
      item.className = `closing-review-agent-history-item${selected ? " active" : ""}`;
      item.id = `closingReviewTab-${conversation.id}`;
      item.setAttribute("role", "tab");
      item.setAttribute("aria-selected", String(selected));
      item.setAttribute("aria-controls", "closingReviewMessages");
      item.tabIndex = selected ? 0 : -1;
      item.title = displayConversationTitle(conversation.title);
      addText(item, "strong", "closing-review-agent-history-title", displayConversationTitle(conversation.title));
      addText(item, "span", "closing-review-agent-history-meta", `${historyStatusLabel(conversation)} · ${timestampSeconds(conversation.updated_at || conversation.last_message_at) || "刚刚"}`);
      item.addEventListener("click", () => selectConversation(conversation.id));
      item.addEventListener("keydown", (event) => {
        if (event.key === "ArrowRight" || event.key === "ArrowDown") {
          event.preventDefault();
          moveTab(index, 1);
        } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
          event.preventDefault();
          moveTab(index, -1);
        } else if (event.key === "Home") {
          event.preventDefault();
          moveTab(index, -index);
        } else if (event.key === "End") {
          event.preventDefault();
          moveTab(index, state.conversations.length - index - 1);
        } else if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectConversation(conversation.id);
        }
      });
      row.appendChild(item);
      if (state.v2) {
        const remove = addText(row, "button", "secondary closing-review-agent-delete", "×");
        remove.type = "button";
        remove.setAttribute("aria-label", `删除对话：${displayConversationTitle(conversation.title)}`);
        const canDelete = !itemState.loading && !["queued", "running"].includes(String(itemState.activeTask?.state || ""));
        remove.disabled = !canDelete || state.deleting.has(conversationKey(conversation.id));
        remove.title = canDelete ? `删除对话：${displayConversationTitle(conversation.title)}` : "回答生成中，结束后可删除";
        remove.addEventListener("click", () => deleteConversation(conversation));
      }
      history.appendChild(row);
    });
    if (messages) {
      const selectedTab = state.conversationId == null ? null : `closingReviewTab-${state.conversationId}`;
      if (selectedTab) messages.setAttribute("aria-labelledby", selectedTab);
      else messages.removeAttribute("aria-labelledby");
    }
  }

  function renderUndoRecords() {
    if (!status || !state.undoRecords.length) return;
    state.undoRecords.forEach((record) => {
      if (record.button && record.button.isConnected) return;
      const undo = addText(status, "button", "secondary closing-review-agent-delete", `撤销删除：${displayConversationTitle(record.conversation.title)}`);
      undo.type = "button";
      undo.setAttribute("aria-label", `撤销删除：${displayConversationTitle(record.conversation.title)}`);
      record.button = undo;
      undo.addEventListener("click", () => restoreConversation(record));
    });
  }

  function nextConversationId(conversation) {
    const index = state.conversations.findIndex((item) => Number(item.id) === Number(conversation.id));
    if (index < 0) return null;
    return state.conversations[index + 1]?.id ?? state.conversations[index - 1]?.id ?? null;
  }

  async function deleteConversation(conversation) {
    const id = conversation.id;
    const itemState = conversationState(id);
    if (state.deleting.has(conversationKey(id))) return;
    if (itemState.loading || ["queued", "running"].includes(String(itemState.activeTask?.state || ""))) {
      setStatus("回答生成中，结束后可删除。", "error");
      return;
    }
    const activation = state.activation;
    state.deleting.add(conversationKey(id));
    const wasCurrent = Number(state.conversationId) === Number(id);
    const replacement = wasCurrent ? nextConversationId(conversation) : state.conversationId;
    try {
      await state.api(`${V2_ENDPOINT}/conversations/${id}`, { method: "DELETE" });
      if (activation !== state.activation) return;
      if (Number(state.conversationId) === Number(id)) state.conversationId = replacement;
      await loadConversations(activation);
      if (wasCurrent) {
        if (state.conversationId == null) newButton.focus();
        else focusTab(state.conversationId);
      }
      state.undoRecords.push({ conversation, snapshot: itemState });
      setStatus("对话已删除，可撤销；业务数据未被修改。");
    } catch (error) {
      setStatus(error.message || "删除失败", "error");
    } finally {
      state.deleting.delete(conversationKey(id));
      renderHistory();
    }
  }

  async function restoreConversation(record) {
    if (!record || record.restoring) return;
    record.restoring = true;
    try {
      await state.api(`${V2_ENDPOINT}/conversations/${record.conversation.id}/restore`, { method: "POST" });
      state.conversationStates.set(conversationKey(record.conversation.id), record.snapshot);
      state.conversationId = record.conversation.id;
      state.undoRecords = state.undoRecords.filter((item) => item !== record);
      await loadConversations(state.activation);
      setStatus("对话已恢复");
    } catch (error) {
      record.restoring = false;
      setStatus(error.message || "恢复失败", "error");
    }
  }

  function evidenceBlock(payload) {
    if (!payload || typeof payload !== "object") return null;
    const evidence = Array.isArray(payload.evidence) ? payload.evidence : [];
    const limitations = Array.isArray(payload.limitations) ? payload.limitations : [];
    const metadata = payload.metadata && typeof payload.metadata === "object" ? payload.metadata : null;
    const refs = metadata && Array.isArray(metadata.evidence_refs) ? metadata.evidence_refs : [];
    if (!evidence.length && !limitations.length && !metadata && !refs.length) return null;
    const wrapper = document.createElement("details");
    wrapper.className = "closing-review-agent-evidence";
    addText(wrapper, "summary", "closing-review-agent-evidence-heading", "查看来源与校验详情");
    if (metadata && metadata.source) addText(wrapper, "span", "closing-review-agent-evidence-source", `最新来源：${metadata.source}`);
    const list = document.createElement("ul");
    list.className = "closing-review-agent-evidence-list";
    evidence.slice(0, 6).forEach((item) => {
      const label = [item.title, item.url, item.fetch_status].filter(Boolean).join(" · ");
      if (label) addText(list, "li", "", label);
    });
    refs.slice(0, 6).forEach((ref) => {
      const label = [ref.source, ref.locator].filter(Boolean).join(" · ");
      if (label) addText(list, "li", "", label);
    });
    limitations.slice(0, 6).forEach((item) => addText(list, "li", "", item.message || item.code || "部分内容未交付"));
    if (list.childNodes.length) wrapper.appendChild(list);
    return wrapper;
  }

  function renderMessages(items, conversationId, { preserveScroll = true } = {}) {
    const selected = Number(conversationId) === Number(state.conversationId);
    if (!selected) return;
    state.answerRenders.splice(0).forEach((render) => {
      if (render && typeof render.destroy === "function") render.destroy();
    });
    clear(messages);
    const itemState = conversationState(conversationId);
    const visibleItems = Array.isArray(items) ? items : [];
    if (!visibleItems.length && !itemState.pending) {
      addText(messages, "p", "closing-review-agent-empty", "这段对话还没有消息。");
    }
    const supersededIds = new Set(
      visibleItems.map((message) => Number(message.supersedes_message_id)).filter((id) => Number.isFinite(id) && id > 0),
    );
    visibleItems.forEach((message, index) => {
      const article = document.createElement("article");
      article.className = `closing-review-agent-message ${message.role === "user" ? "is-user" : "is-agent"}${message.message_type === "automatic_result" ? " is-automatic" : ""}`;
      const header = document.createElement("div");
      header.className = "closing-review-agent-message-header";
      addText(header, "strong", "closing-review-agent-message-label", messageLabel(message));
      addText(header, "time", "closing-review-agent-message-time", timestampSeconds(message.created_at));
      article.appendChild(header);
      const payload = message.structured_payload;
      const projection = payload && typeof payload === "object" ? payload : null;
      const isValidatedAnswer = message.role !== "user" && projection && projection.schema_version === "2.1";
      if (isValidatedAnswer && typeof window.AgentAnswerRenderer?.renderAnswer === "function") {
        const answer = document.createElement("div");
        answer.className = "closing-review-agent-message-content closing-review-agent-answer";
        article.appendChild(answer);
        try {
          const targetMessageId = message.id;
          const targetConversationId = message.conversation_id || conversationId;
          const rendered = window.AgentAnswerRenderer.renderAnswer(answer, projection, {
            messageId: targetMessageId,
            loadViewPage: ({ viewId, page, pageSize, facetPage, matrixColumnPage, signal }) => {
              if (!/^v[1-8]$/.test(String(viewId))) throw new Error("视图编号无效");
              const query = new URLSearchParams({
                page: String(page), page_size: String(pageSize),
                facet_page: String(facetPage || 1), facet_page_size: "6",
                matrix_column_page: String(matrixColumnPage || 1),
              });
              return state.api(`${V2_ENDPOINT}/conversations/${targetConversationId}/messages/${targetMessageId}/views/${encodeURIComponent(viewId)}?${query.toString()}`, { signal });
            },
            onError: () => {},
          });
          if (rendered && typeof rendered.destroy === "function") state.answerRenders.push(rendered);
        } catch (error) {
          addText(answer, "span", "", message.content || "该消息内容已按保留策略清理。");
        }
      } else {
        addText(article, "p", "closing-review-agent-message-content", message.content || "该消息内容已按保留策略清理。");
      }
      if (supersededIds.has(Number(message.id))) addText(article, "span", "closing-review-agent-status-chip", "已被更新");
      const dataStatus = projection && (projection.delivery_status || projection.status);
      if (dataStatus && dataStatus !== "complete") addText(article, "span", `closing-review-agent-status-chip status-${dataStatus}`, statusLabel(dataStatus));
      const limitations = Array.isArray(projection?.limitations) ? projection.limitations : [];
      if (limitations.length && dataStatus !== "complete") {
        const warning = limitations.find((item) => item.code === "request_scope_unverified" || item.code === "query_incomplete") || limitations[0];
        const technicalCodes = ["uncovered_claim", "unreferenced_number", "missing_reference", "invalid_reference", "reference_unavailable", "answer_validation_failed", "request_coverage_incomplete"];
        const warningText = technicalCodes.includes(warning.code) ? "部分解释未通过校验，已保留可核对的数据。" : warning.message;
        if (warningText && !(message.content || "").includes(warningText)) addText(article, "p", "closing-review-agent-limitation", warningText);
      }
      const evidence = evidenceBlock(projection);
      if (evidence) article.appendChild(evidence);
      if (message.role !== "user" && message.message_type === "error") {
        const previousQuestion = [...visibleItems.slice(0, index)].reverse().find((item) => item.role === "user");
        if (previousQuestion && previousQuestion.content) {
          const retry = document.createElement("button");
          retry.type = "button";
          retry.className = "secondary closing-review-agent-retry";
          retry.textContent = "重试原问题";
          retry.addEventListener("click", () => {
            input.value = previousQuestion.content;
            conversationState(conversationId).draft = previousQuestion.content;
            submitMessage();
          });
          article.appendChild(retry);
        }
      }
      messages.appendChild(article);
    });
    if (itemState.pending) {
      const pendingExists = visibleItems.some((message) => message.client_request_id === itemState.pending.clientRequestId);
      if (!pendingExists) {
        const pendingArticle = sendingBubble(itemState.pending.content, itemState.pending.clientRequestId, conversationId);
        pendingArticle.dataset.taskId = itemState.taskId || "";
      }
    }
    if (preserveScroll && itemState.followOutput) messages.scrollTop = messages.scrollHeight;
    else messages.scrollTop = itemState.scrollTop || 0;
    if (itemState.activeTask) renderProgress(itemState.activeTask, null, conversationId);
    updateControls();
  }

  function sendingBubble(content, clientRequestId, conversationId) {
    const article = document.createElement("article");
    article.className = "closing-review-agent-message is-user is-sending";
    article.dataset.clientRequestId = clientRequestId;
    article.dataset.conversationId = conversationId;
    const header = document.createElement("div");
    header.className = "closing-review-agent-message-header";
    addText(header, "strong", "closing-review-agent-message-label", "我的问题");
    addText(header, "time", "closing-review-agent-message-time", "刚刚");
    article.appendChild(header);
    addText(article, "p", "closing-review-agent-message-content", content);
    const progress = addText(article, "span", "closing-review-agent-inline-progress", "正在提交…");
    progress.setAttribute("aria-live", "off");
    messages.appendChild(article);
    if (Number(conversationId) === Number(state.conversationId)) messages.scrollTop = messages.scrollHeight;
    return article;
  }

  function renderProgress(task, article, conversationId) {
    const itemState = conversationState(conversationId);
    const incoming = task && task.progress;
    if (incoming) {
      const reducer = typeof window.AgentProgress?.reduceProgress === "function" ? window.AgentProgress.reduceProgress : null;
      itemState.progress = reducer ? reducer(itemState.progress, incoming) : incoming;
    }
    if (!itemState.progress) return;
    const progress = article && article.isConnected
      ? article.querySelector(".closing-review-agent-inline-progress")
      : Number(conversationId) === Number(state.conversationId)
        ? messages.querySelector(`.closing-review-agent-message[data-client-request-id="${itemState.pending?.clientRequestId || ""}"] .closing-review-agent-inline-progress`)
        : null;
    const elapsed = Number(itemState.progress.elapsed_seconds);
    if (progress) progress.textContent = `正在处理${Number.isFinite(elapsed) ? ` · ${Math.floor(elapsed)} 秒` : ""}`;
    const key = `${itemState.progress.sequence}:${itemState.progress.stage}:${itemState.progress.stage_status}`;
    if (key !== itemState.announcedProgress && Number(conversationId) === Number(state.conversationId)) {
      itemState.announcedProgress = key;
      setStatus(itemState.progress.terminal ? "" : "正在处理…");
    }
  }

  async function loadMessages(conversationId, activation, { preserveScroll = true } = {}) {
    const data = await state.api(`${endpoint()}/conversations/${conversationId}/messages`);
    if (activation !== state.activation) return;
    const itemState = conversationState(conversationId);
    itemState.items = data.items || [];
    itemState.activeTask = data.active_task || null;
    itemState.taskId = data.active_task?.task_id || data.active_task?.task_ref || itemState.taskId;
    itemState.loading = Boolean(data.active_task && ["queued", "running"].includes(String(data.active_task.state || "")));
    if (itemState.activeTask && itemState.taskId) startTaskPolling(itemState.taskId, conversationId, activation);
    if (isActive(conversationId, activation)) {
      renderMessages(itemState.items, conversationId, { preserveScroll });
      renderHistory();
      restoreCurrentDraft();
    }
    updateControls();
  }

  async function loadConversations(activation) {
    const data = await state.api(`${endpoint()}/conversations`);
    if (activation !== state.activation) return;
    state.conversations = data.items || [];
    if (!state.conversations.length) {
      state.conversationId = null;
      conversationState(null).items = [];
      restoreCurrentDraft();
      renderHistory();
      clear(messages);
      addText(messages, "p", "closing-review-agent-empty", "暂无对话，点击右侧“＋”开始使用智能贸易助手。");
      updateControls();
      return;
    }
    const selected = state.conversations.find((item) => Number(item.id) === Number(state.conversationId));
    state.conversationId = selected ? selected.id : state.conversations[0].id;
    renderHistory();
    restoreCurrentDraft();
    await loadMessages(state.conversationId, activation);
  }

  async function selectConversation(conversationId) {
    if (Number(state.conversationId) === Number(conversationId)) {
      input.focus();
      return;
    }
    saveCurrentDraft();
    const sequence = ++state.selectionSequence;
    const activation = state.activation;
    state.conversationId = conversationId;
    restoreCurrentDraft();
    renderHistory();
    setStatus("正在读取对话…");
    try {
      await loadMessages(conversationId, activation);
      focusTab(conversationId);
      if (sequence === state.selectionSequence && isActive(conversationId, activation)) setStatus("");
    } catch (error) {
      if (sequence === state.selectionSequence && isActive(conversationId, activation)) setStatus(error.message || "读取对话失败", "error");
    }
  }

  async function createConversation() {
    if (state.creating) return;
    saveCurrentDraft();
    const activation = state.activation;
    state.creating = true;
    updateControls();
    try {
      const conversation = await state.api(`${endpoint()}/conversations`, {
        method: "POST",
        body: JSON.stringify({ title: DEFAULT_CONVERSATION_TITLE }),
      });
      if (activation !== state.activation) return;
      state.conversations = [...state.conversations, conversation];
      state.conversationId = conversation.id;
      conversationState(conversation.id);
      renderHistory();
      renderMessages([], conversation.id, { preserveScroll: false });
      restoreCurrentDraft();
      input.focus();
      setStatus("");
    } catch (error) {
      if (activation === state.activation) setStatus(error.message || "新建对话失败", "error");
    } finally {
      state.creating = false;
      updateControls();
    }
  }

  async function waitForTask(taskId, activation, conversationId, sequence) {
    const itemState = conversationState(conversationId);
    const started = Date.now();
    const timeoutSeconds = endpoint().includes("trading-agent-v2") ? 225 : 90;
    let attempt = 0;
    let readFailures = 0;
    while (Date.now() - started < timeoutSeconds * 1000) {
      await new Promise((resolve) => setTimeout(resolve, Math.min(++attempt, 5) * 1000));
      if (activation !== state.activation || itemState.requestSequence !== sequence) return null;
      let task;
      try {
        task = await state.api(`${endpoint()}/tasks/${taskId}`);
        readFailures = 0;
      } catch (error) {
        if (++readFailures >= 3) throw error;
        if (isActive(conversationId, activation)) setStatus("暂时无法读取进度，正在重新连接…");
        continue;
      }
      if ((task.task_id != null && Number(task.task_id) !== Number(taskId))
          || (conversationId != null && task.conversation_id != null && Number(task.conversation_id) !== Number(conversationId))) {
        throw new Error("任务身份校验失败，请重新打开此对话。");
      }
      itemState.activeTask = task;
      if (task.progress) renderProgress(task, null, conversationId);
      renderHistory();
      if (["succeeded", "partial", "failed", "cancelled"].includes(task.state)) {
        itemState.loading = false;
        itemState.activeTask = null;
        itemState.pending = null;
        itemState.taskId = null;
        itemState.pollingTaskId = null;
        await loadMessages(conversationId, activation);
        if (isActive(conversationId, activation)) setStatus("");
        return task;
      }
      if (!task.progress && isActive(conversationId, activation)) setStatus(task.state === "queued" ? "正在排队，等待后台处理…" : "正在分析…");
    }
    throw new Error("暂未取得最终状态，请稍后打开此对话查看结果；不要重复提交相同问题。");
  }

  function startTaskPolling(taskId, conversationId, activation) {
    const itemState = conversationState(conversationId);
    if (!state.v2 || !taskId || itemState.pollingTaskId === String(taskId)) return;
    const sequence = itemState.requestSequence;
    itemState.pollingTaskId = String(taskId);
    waitForTask(taskId, activation, conversationId, sequence).catch((error) => {
      itemState.pollingTaskId = null;
      // The server task may still be running; keep submission disabled until a
      // later refresh observes a terminal state, preventing duplicate work.
      itemState.loading = true;
      if (isActive(conversationId, activation)) setStatus(error.message || "读取任务状态失败", "error");
      renderHistory();
      updateControls();
    });
  }

  async function submitMessage() {
    if (state.creating) return;
    if (state.conversationId == null) {
      await createConversation();
      if (state.conversationId == null) return;
    }
    const content = input.value.trim();
    if (!content) {
      setStatus("请输入要查询的问题。", "error");
      input.focus();
      return;
    }
    const itemState = conversationState(state.conversationId);
    const otherRunning = [...state.conversationStates.values()].some((item) =>
      item.conversationId != null && Number(item.conversationId) !== Number(state.conversationId) && item.loading,
    );
    if (itemState.loading || otherRunning) {
      setStatus(otherRunning ? "已有其他对话正在生成回答，请等待结束后再发送。" : "当前对话正在生成回答，请稍候。", "error");
      return;
    }
    saveCurrentDraft();
    itemState.loading = true;
    itemState.requestSequence += 1;
    const sequence = itemState.requestSequence;
    const activation = state.activation;
    const conversationId = state.conversationId;
    const pending = itemState.pending && itemState.pending.content === content
      ? itemState.pending
      : { conversationId, content, clientRequestId: requestId() };
    itemState.pending = pending;
    const article = sendingBubble(content, pending.clientRequestId, conversationId);
    setStatus("正在提交问题…");
    const body = { content, client_request_id: pending.clientRequestId };
    try {
      const queued = await state.api(`${endpoint()}/conversations/${conversationId}/messages`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (activation !== state.activation || itemState.requestSequence !== sequence) return;
      itemState.taskId = queued.task_id || queued.task_ref || null;
      itemState.activeTask = { state: queued.state || "queued", task_id: itemState.taskId };
      article.dataset.taskId = itemState.taskId || "";
      input.value = "";
      itemState.draft = "";
      renderHistory();
      if (state.v2 && itemState.taskId) {
        itemState.progress = null;
        itemState.announcedProgress = "";
        startTaskPolling(itemState.taskId, conversationId, activation);
      } else {
        itemState.loading = false;
        itemState.pending = null;
        await loadConversations(activation);
      }
    } catch (error) {
      itemState.loading = false;
      if (isActive(conversationId, activation)) setStatus(error.message || "复盘请求失败", "error");
      renderHistory();
    } finally {
      updateControls();
    }
  }

  function bind() {
    if (state.bound) return;
    state.bound = true;
    composer.addEventListener("submit", (event) => {
      event.preventDefault();
      submitMessage();
    });
    newButton.addEventListener("click", createConversation);
    input.addEventListener("input", saveCurrentDraft);
    input.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        submitMessage();
      }
    });
    messages.addEventListener("scroll", () => {
      const current = currentConversationState();
      current.scrollTop = messages.scrollTop;
      current.followOutput = messages.scrollTop + messages.clientHeight >= messages.scrollHeight - 24;
    });
  }

  async function activate(config) {
    if (!config || typeof config.api !== "function") return;
    state.api = config.api;
    state.user = config.user || null;
    state.activation += 1;
    const activation = state.activation;
    state.v2 = false;
    state.conversations = [];
    state.conversationStates = new Map();
    state.conversationId = null;
    state.undoRecords = [];
    try {
      const capabilities = await state.api(`${V2_ENDPOINT}/capabilities`);
      state.v2 = Boolean(capabilities && capabilities.enabled);
    } catch (error) {
      state.v2 = false;
    }
    bind();
    page.classList.remove("hidden");
    scopeNote.textContent = state.v2 ? "业务数据查询 · 市场研究 · 只读分析" : "交易复盘兼容模式 · 当前功能范围以可用能力为准";
    setStatus("正在加载 Agent…");
    try {
      await loadConversations(activation);
      focusTab(state.conversationId);
      if (activation === state.activation) setStatus("");
    } catch (error) {
      if (activation === state.activation) setStatus(error.message || "Agent 页面加载失败", "error");
    }
  }

  window.ClosingReviewAgent = { activate };
})();
