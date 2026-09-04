(function() {
  "use strict";

  const ENDPOINT = "/api/closing-review-agent";
  const state = {
    api: null,
    user: null,
    conversations: [],
    suggestions: [],
    conversationId: null,
    bound: false,
    loading: false,
    activation: 0,
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
    if (message.message_type === "automatic_result") return "自动收盘复盘";
    if (message.role === "user") return "我的问题";
    if (message.message_type === "error") return "Agent 状态";
    return "Agent 回答";
  }

  function renderHistory() {
    clear(history);
    if (!state.conversations.length) {
      addText(history, "p", "closing-review-agent-empty", "暂无对话，点击“新建对话”开始。");
      return;
    }
    state.conversations.forEach((conversation) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = `closing-review-agent-history-item${Number(conversation.id) === Number(state.conversationId) ? " active" : ""}`;
      item.setAttribute("aria-pressed", String(Number(conversation.id) === Number(state.conversationId)));
      addText(item, "strong", "closing-review-agent-history-title", conversation.title || "期权收盘复盘");
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
    items.forEach((message) => {
      const article = document.createElement("article");
      article.className = `closing-review-agent-message ${message.role === "user" ? "is-user" : "is-agent"}${message.message_type === "automatic_result" ? " is-automatic" : ""}`;
      const header = document.createElement("div");
      header.className = "closing-review-agent-message-header";
      addText(header, "strong", "closing-review-agent-message-label", messageLabel(message));
      addText(header, "time", "closing-review-agent-message-time", timestampSeconds(message.created_at));
      article.appendChild(header);
      addText(article, "p", "closing-review-agent-message-content", message.content || "该消息内容已按保留策略清理。");
      const payload = message.structured_payload;
      const projection = payload && typeof payload === "object" ? payload : null;
      if (projection && projection.status) {
        addText(article, "span", `closing-review-agent-status-chip status-${projection.status}`, `数据状态：${statusLabel(projection.status)}`);
      }
      const evidence = evidenceBlock(projection);
      if (evidence) article.appendChild(evidence);
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
    const data = await state.api(`${ENDPOINT}/conversations/${conversationId}/messages`);
    if (activation !== state.activation) return;
    renderMessages(data.items || []);
  }

  async function loadConversations(activation) {
    const data = await state.api(`${ENDPOINT}/conversations`);
    if (activation !== state.activation) return;
    state.conversations = data.items || [];
    if (!state.conversations.length) {
      const conversation = await state.api(`${ENDPOINT}/conversations`, {
        method: "POST",
        body: JSON.stringify({ title: "期权收盘复盘" }),
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
    const data = await state.api(`${ENDPOINT}/suggestions`);
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
      const conversation = await state.api(`${ENDPOINT}/conversations`, {
        method: "POST",
        body: JSON.stringify({ title: "期权收盘复盘" }),
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
    setStatus("正在读取确定性复盘结果…");
    const body = {
      content: suggestionId ? null : content,
      suggestion_id: suggestionId,
      client_request_id: requestId(),
    };
    try {
      await state.api(`${ENDPOINT}/conversations/${state.conversationId}/messages`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      input.value = "";
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
    bind();
    page.classList.remove("hidden");
    scopeNote.textContent = "宏源账户 · 铁矿石期权 · 仅收盘复盘事实查询";
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
