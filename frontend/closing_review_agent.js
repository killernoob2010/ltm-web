(function() {
  "use strict";

  const ENDPOINT = "/api/closing-review-agent";
  const V2_ENDPOINT = "/api/trading-agent-v2";
  const legacyConversationEndpoint = `${ENDPOINT}/conversations`;
  const state = {
    api: null,
    user: null,
    conversations: [],
    conversationId: null,
    bound: false,
    loading: false,
    activation: 0,
    v2: false,
    pending: null,
    progress: null,
    announcedProgress: "",
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

  function setStatus(message, kind = "") {
    if (!status) return;
    status.textContent = message || "";
    status.className = `closing-review-agent-status ${kind}`.trim();
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
      complete: "数据完整",
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

  function renderHistory() {
    clear(history);
    if (!state.conversations.length) {
      addText(history, "p", "closing-review-agent-empty", "暂无对话，点击“新建对话”开始使用交易持仓助手。");
      return;
    }
    state.conversations.forEach((conversation) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = `closing-review-agent-history-item${Number(conversation.id) === Number(state.conversationId) ? " active" : ""}`;
      item.setAttribute("aria-pressed", String(Number(conversation.id) === Number(state.conversationId)));
      addText(item, "strong", "closing-review-agent-history-title", conversation.title || "交易持仓助手对话");
      addText(item, "span", "closing-review-agent-history-meta", `${conversation.status === "active" ? "进行中" : "已归档"} · ${timestampSeconds(conversation.updated_at || conversation.last_message_at) || "刚刚"}`);
      item.addEventListener("click", () => selectConversation(conversation.id));
      history.appendChild(item);
    });
  }

  function evidenceBlock(payload) {
    if (!payload || typeof payload !== "object") return null;
    const evidence = Array.isArray(payload.evidence) ? payload.evidence : [];
    const limitations = Array.isArray(payload.limitations) ? payload.limitations : [];
    const metadata = payload.metadata && typeof payload.metadata === "object" ? payload.metadata : null;
    const refs = metadata && Array.isArray(metadata.evidence_refs) ? metadata.evidence_refs : [];
    if (!evidence.length && !limitations.length && !metadata && !refs.length) return null;
    const wrapper = document.createElement("div");
    wrapper.className = "closing-review-agent-evidence";
    addText(wrapper, "strong", "closing-review-agent-evidence-heading", "证据与限制");
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

  function renderMessages(items) {
    clear(messages);
    if (!items.length) {
      addText(messages, "p", "closing-review-agent-empty", "这段对话还没有消息。");
      return;
    }
    const supersededIds = new Set(
      items.map((message) => Number(message.supersedes_message_id)).filter((id) => Number.isFinite(id) && id > 0),
    );
    items.forEach((message, index) => {
      const article = document.createElement("article");
      article.className = `closing-review-agent-message ${message.role === "user" ? "is-user" : "is-agent"}${message.message_type === "automatic_result" ? " is-automatic" : ""}`;
      const header = document.createElement("div");
      header.className = "closing-review-agent-message-header";
      addText(header, "strong", "closing-review-agent-message-label", messageLabel(message));
      addText(header, "time", "closing-review-agent-message-time", timestampSeconds(message.created_at));
      article.appendChild(header);
      addText(article, "p", "closing-review-agent-message-content", message.content || "该消息内容已按保留策略清理。");
      if (supersededIds.has(Number(message.id))) addText(article, "span", "closing-review-agent-status-chip", "已被更新");
      const payload = message.structured_payload;
      const projection = payload && typeof payload === "object" ? payload : null;
      const dataStatus = projection && (projection.delivery_status || projection.status);
      if (dataStatus) addText(article, "span", `closing-review-agent-status-chip status-${dataStatus}`, `数据状态：${statusLabel(dataStatus)}`);
      const evidence = evidenceBlock(projection);
      if (evidence) article.appendChild(evidence);
      if (message.role !== "user" && message.message_type === "error") {
        const previousQuestion = [...items.slice(0, index)].reverse().find((item) => item.role === "user");
        if (previousQuestion && previousQuestion.content) {
          const retry = document.createElement("button");
          retry.type = "button";
          retry.className = "secondary closing-review-agent-retry";
          retry.textContent = "重试原问题";
          retry.addEventListener("click", () => {
            input.value = previousQuestion.content;
            submitMessage();
          });
          article.appendChild(retry);
        }
      }
      messages.appendChild(article);
    });
    messages.scrollTop = messages.scrollHeight;
  }

  function sendingBubble(content, clientRequestId) {
    const article = document.createElement("article");
    article.className = "closing-review-agent-message is-user is-sending";
    article.dataset.clientRequestId = clientRequestId;
    const header = document.createElement("div");
    header.className = "closing-review-agent-message-header";
    addText(header, "strong", "closing-review-agent-message-label", "我的问题");
    addText(header, "time", "closing-review-agent-message-time", "刚刚");
    article.appendChild(header);
    addText(article, "p", "closing-review-agent-message-content", content);
    const progress = addText(article, "span", "closing-review-agent-inline-progress", "正在提交…");
    progress.setAttribute("aria-live", "off");
    messages.appendChild(article);
    messages.scrollTop = messages.scrollHeight;
    return article;
  }

  function renderProgress(task, article) {
    const incoming = task && task.progress;
    if (!incoming) return;
    const reducer = typeof window.AgentProgress?.reduceProgress === "function" ? window.AgentProgress.reduceProgress : null;
    state.progress = reducer ? reducer(state.progress, incoming) : incoming;
    if (!state.progress) return;
    const progress = article && article.querySelector(".closing-review-agent-inline-progress");
    if (progress) {
      const elapsed = Number(state.progress.elapsed_seconds);
      progress.textContent = `${state.progress.summary || "处理中"}${Number.isFinite(elapsed) ? ` · ${elapsed} 秒` : ""}`;
    }
    const key = `${state.progress.sequence}:${state.progress.stage}:${state.progress.stage_status}`;
    if (key !== state.announcedProgress) {
      state.announcedProgress = key;
      setStatus(state.progress.summary || "正在处理…");
    }
  }

  async function loadMessages(conversationId, activation) {
    const data = await state.api(`${endpoint()}/conversations/${conversationId}/messages`);
    if (activation !== state.activation || Number(conversationId) !== Number(state.conversationId)) return;
    state.activeTask = data.active_task || null;
    renderMessages(data.items || []);
    if (state.activeTask) renderProgress(state.activeTask);
  }

  async function loadConversations(activation) {
    const data = await state.api(`${endpoint()}/conversations`);
    if (activation !== state.activation) return;
    state.conversations = data.items || [];
    if (!state.conversations.length) {
      const conversation = await state.api(`${endpoint()}/conversations`, {
        method: "POST",
        body: JSON.stringify({ title: "交易持仓助手对话" }),
      });
      if (activation !== state.activation) return;
      state.conversations = [conversation];
    }
    const selected = state.conversations.find((item) => Number(item.id) === Number(state.conversationId));
    state.conversationId = selected ? selected.id : state.conversations[0].id;
    renderHistory();
    await loadMessages(state.conversationId, activation);
  }

  async function selectConversation(conversationId) {
    if (state.loading || Number(state.conversationId) === Number(conversationId)) return;
    state.conversationId = conversationId;
    state.progress = null;
    state.announcedProgress = "";
    renderHistory();
    setStatus("正在读取对话…");
    try {
      await loadMessages(conversationId, state.activation);
      setStatus("");
    } catch (error) {
      setStatus(error.message || "读取对话失败", "error");
    }
  }

  async function createConversation() {
    if (state.loading) return;
    state.loading = true;
    newButton.disabled = true;
    try {
      const conversation = await state.api(`${endpoint()}/conversations`, {
        method: "POST",
        body: JSON.stringify({ title: "交易持仓助手对话" }),
      });
      state.conversations = [conversation, ...state.conversations];
      state.conversationId = conversation.id;
      state.progress = null;
      renderHistory();
      renderMessages([]);
      setStatus("");
    } catch (error) {
      setStatus(error.message || "新建对话失败", "error");
    } finally {
      state.loading = false;
      newButton.disabled = false;
    }
  }

  async function waitForTask(taskId, activation, conversationId = state.conversationId, article = null) {
    const started = Date.now();
    let timeoutSeconds = endpoint().includes("trading-agent-v2") ? 225 : 90;
    let attempt = 0;
    let readFailures = 0;
    while (Date.now() - started < timeoutSeconds * 1000) {
      await new Promise((resolve) => setTimeout(resolve, Math.min(++attempt, 5) * 1000));
      if (activation !== state.activation) return null;
      if (conversationId != null && state.conversationId != null && Number(conversationId) !== Number(state.conversationId)) return null;
      let task;
      try {
        task = await state.api(`${endpoint()}/tasks/${taskId}`);
        readFailures = 0;
      } catch (error) {
        if (++readFailures >= 3) throw error;
        setStatus("暂时无法读取进度，正在重新连接…");
        continue;
      }
      if ((task.task_id != null && Number(task.task_id) !== Number(taskId))
          || (conversationId != null && task.conversation_id != null && Number(task.conversation_id) !== Number(conversationId))) {
        throw new Error("任务身份校验失败，请重新打开此对话。");
      }
      if (task.progress) renderProgress(task, article);
      if (["succeeded", "partial", "failed", "cancelled"].includes(task.state)) return task;
      if (!task.progress) setStatus(task.state === "queued" ? "正在排队，等待后台处理…" : "正在分析…");
    }
    throw new Error("暂未取得最终状态，请稍后打开此对话查看结果；不要重复提交相同问题。");
  }

  async function submitMessage() {
    if (state.loading || !state.conversationId) return;
    const content = input.value.trim();
    if (!content) {
      setStatus("请输入要查询的问题。", "error");
      input.focus();
      return;
    }
    state.loading = true;
    sendButton.disabled = true;
    const conversationId = state.conversationId;
    const pending = state.pending && state.pending.conversationId === conversationId && state.pending.content === content
      ? state.pending
      : { conversationId, content, clientRequestId: requestId() };
    state.pending = pending;
    const article = sendingBubble(content, pending.clientRequestId);
    setStatus("正在提交问题…");
    const body = { content, client_request_id: pending.clientRequestId };
    try {
      const queued = await state.api(`${endpoint()}/conversations/${conversationId}/messages`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      state.pending = null;
      input.value = "";
      article.dataset.taskId = queued.task_id || queued.task_ref || "";
      if (state.v2 && queued.task_id) {
        state.progress = null;
        state.announcedProgress = "";
        await waitForTask(queued.task_id, state.activation, conversationId, article);
      }
      await loadConversations(state.activation);
      setStatus("");
    } catch (error) {
      // Before the server accepts the request, keep both the text and request
      // id so a retry is idempotent and does not duplicate the user bubble.
      setStatus(error.message || "复盘请求失败", "error");
    } finally {
      state.loading = false;
      sendButton.disabled = false;
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
    input.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        submitMessage();
      }
    });
  }

  async function activate(config) {
    if (!config || typeof config.api !== "function") return;
    state.api = config.api;
    state.user = config.user || null;
    state.activation += 1;
    const activation = state.activation;
    state.v2 = false;
    state.pending = null;
    state.progress = null;
    state.announcedProgress = "";
    try {
      const capabilities = await state.api(`${V2_ENDPOINT}/capabilities`);
      state.v2 = Boolean(capabilities && capabilities.enabled);
    } catch (error) {
      state.v2 = false;
    }
    bind();
    page.classList.remove("hidden");
    scopeNote.textContent = state.v2 ? "宏源期货 · 全部期货与期权 · 只读开放分析" : "宏源期货 · 期权收盘复盘（兼容模式）";
    setStatus("正在加载 Agent…");
    try {
      await loadConversations(activation);
      if (activation === state.activation) setStatus("");
    } catch (error) {
      if (activation === state.activation) setStatus(error.message || "Agent 页面加载失败", "error");
    }
  }

  window.ClosingReviewAgent = { activate };
})();
