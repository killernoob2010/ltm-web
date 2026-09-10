(function() {
  "use strict";

  const ENDPOINT = "/api/closing-review-agent";
  const V2_ENDPOINT = "/api/trading-agent-v2";
  const legacyConversationEndpoint = `${ENDPOINT}/conversations`;
  const legacySuggestionsEndpoint = `${ENDPOINT}/suggestions`;
  const state = {
    api: null,
    user: null,
    conversations: [],
    suggestions: [],
    conversationId: null,
    bound: false,
    loading: false,
    activation: 0,
    v2: false,
  };

  const $ = (id) => document.getElementById(id);
  const page = $("closingReviewAgentPage");
  const history = $("closingReviewHistory");
  const messages = $("closingReviewMessages");
  const suggestions = $("closingReviewSuggestions");
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
    const metadata = payload && typeof payload === "object" ? payload.metadata : null;
    const refs = metadata && Array.isArray(metadata.evidence_refs) ? metadata.evidence_refs : [];
    if (!metadata && !refs.length) return null;
    const wrapper = document.createElement("div");
    wrapper.className = "closing-review-agent-evidence";
    addText(wrapper, "strong", "closing-review-agent-evidence-heading", "证据");
    if (metadata && metadata.source) addText(wrapper, "span", "closing-review-agent-evidence-source", `最新来源：${metadata.source}`);
    if (metadata && metadata.freshness) addText(wrapper, "span", "closing-review-agent-evidence-freshness", `时效：${metadata.freshness}`);
    if (refs.length) {
      const list = document.createElement("ul");
      list.className = "closing-review-agent-evidence-list";
      refs.slice(0, 6).forEach((ref) => {
        const text = [ref.source, ref.locator].filter(Boolean).join(" · ");
        if (text) addText(list, "li", "", text);
      });
      wrapper.appendChild(list);
    }
    return wrapper;
  }

  function renderMessages(items) {
    clear(messages);
    if (!items.length) {
      addText(messages, "p", "closing-review-agent-empty", "这段对话还没有消息。可从下方推荐问题开始。");
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
      if (supersededIds.has(Number(message.id))) {
        addText(article, "span", "closing-review-agent-status-chip", "已被更新");
      }
      const payload = message.structured_payload;
      const projection = payload && typeof payload === "object" ? payload : null;
      if (projection && projection.status) {
        addText(article, "span", `closing-review-agent-status-chip status-${projection.status}`, `数据状态：${statusLabel(projection.status)}`);
      }
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

  function renderSuggestions() {
    clear(suggestions);
    if (!state.suggestions.length) {
      addText(suggestions, "p", "closing-review-agent-empty", "暂无推荐问题。");
      return;
    }
    state.suggestions.forEach((suggestion) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "closing-review-agent-suggestion";
      addText(button, "strong", "closing-review-agent-suggestion-label", suggestion.label);
      addText(button, "span", "closing-review-agent-suggestion-question", suggestion.question);
      button.addEventListener("click", () => submitMessage({ suggestionId: suggestion.id }));
      suggestions.appendChild(button);
    });
  }

  async function loadMessages(conversationId, activation) {
    const data = await state.api(`${endpoint()}/conversations/${conversationId}/messages`);
    if (activation !== state.activation) return;
    renderMessages(data.items || []);
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

  async function loadSuggestions(activation) {
    if (state.v2) {
      state.suggestions = [];
      renderSuggestions();
      return;
    }
    const data = await state.api(state.v2 ? `${V2_ENDPOINT}/suggestions` : legacySuggestionsEndpoint);
    if (activation !== state.activation) return;
    state.suggestions = data.items || [];
    renderSuggestions();
  }

  async function selectConversation(conversationId) {
    if (state.loading || Number(state.conversationId) === Number(conversationId)) return;
    state.conversationId = conversationId;
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

  async function waitForTask(taskId, activation) {
    const started = Date.now();
    let timeoutSeconds = endpoint().includes("trading-agent-v2") ? 225 : 90;
    let attempt = 0;
    let readFailures = 0;
    while (Date.now() - started < timeoutSeconds * 1000) {
      await new Promise((resolve) => setTimeout(resolve, Math.min(++attempt, 5) * 1000));
      if (activation !== state.activation) return;
      let task;
      try {
        task = await state.api(`${endpoint()}/tasks/${taskId}`);
        readFailures = 0;
      } catch (error) {
        if (++readFailures >= 3) throw error;
        setStatus("暂时无法读取进度，正在重新连接…");
        continue;
      }
      if (Number.isFinite(task.poll_timeout_seconds)) timeoutSeconds = task.poll_timeout_seconds;
      if (["succeeded", "partial", "failed", "cancelled"].includes(task.state)) return task;
      setStatus(task.state === "queued" ? "正在排队，等待后台处理…" : "正在分析…");
    }
    throw new Error("暂未取得最终状态，请稍后打开此对话查看结果；不要重复提交相同问题。");
  }

  async function submitMessage({ suggestionId = null } = {}) {
    if (state.loading || !state.conversationId) return;
    const content = input.value.trim();
    if (!suggestionId && !content) {
      setStatus("请输入要查询的问题。", "error");
      input.focus();
      return;
    }
    state.loading = true;
    sendButton.disabled = true;
    suggestions.querySelectorAll("button").forEach((button) => { button.disabled = true; });
    setStatus("正在读取确定性持仓事实…");
    const body = state.v2
      ? { content, client_request_id: requestId() }
      : {
        content: suggestionId ? null : content,
        suggestion_id: suggestionId,
        client_request_id: requestId(),
      };
    try {
      const queued = await state.api(`${endpoint()}/conversations/${state.conversationId}/messages`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      input.value = "";
      if (state.v2 && queued.task_id) {
        setStatus("已收到问题，正在读取宏源持仓事实…");
        await waitForTask(queued.task_id, state.activation);
      }
      await loadConversations(state.activation);
      setStatus("");
    } catch (error) {
      setStatus(error.message || "复盘请求失败", "error");
    } finally {
      state.loading = false;
      sendButton.disabled = false;
      suggestions.querySelectorAll("button").forEach((button) => { button.disabled = false; });
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
      await Promise.all([loadConversations(activation), loadSuggestions(activation)]);
      if (activation === state.activation) setStatus("");
    } catch (error) {
      if (activation === state.activation) setStatus(error.message || "Agent 页面加载失败", "error");
    }
  }

  window.ClosingReviewAgent = { activate };
})();
