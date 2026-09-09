"""Private WeCom single-chat adapter with one-way final delivery.

The SDK client is deliberately constructed lazily by the standalone worker. The
web process only imports the V2 routes, so importing this module never opens a
network connection or reads a secret. Raw SDK frames are reduced to the
request identity and reply headers before a pending delivery is retained in
memory; they are never written to the database.
"""
from dataclasses import dataclass
import hashlib
import inspect
import os
import re
import tempfile
from time import monotonic
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4, uuid5

from . import store

BOT_NAMESPACE = UUID("2f0d1a74-8ec6-4af9-b5d9-7f1e2b4d95d7")
NEW_CONVERSATION_COMMANDS = {"新建对话", "新对话", "开始新对话", "/new", "/reset"}
PAIR_CODE_RE = re.compile(r"[A-Za-z0-9_-]{20,128}\Z")
MAX_PENDING_DELIVERIES = 256


@dataclass(frozen=True)
class PendingDelivery:
    client: Any
    frame: dict[str, Any]
    stream_id: str | None
    created_at: float


_PENDING: dict[int, PendingDelivery] = {}


class BotLease:
    """Host-local advisory lease so one worker owns a bot connection at a time."""

    def __init__(self, bot_id: str, *, path: str | None = None):
        if not isinstance(bot_id, str) or not bot_id.strip():
            raise ValueError("企微机器人编号不能为空")
        digest = hashlib.sha256(bot_id.strip().encode("utf-8")).hexdigest()[:24]
        root = os.environ.get("AGENT_V2_LOCK_DIR", tempfile.gettempdir())
        self.path = path or os.path.join(root, f"hongyuan-agent-v2-wecom-{digest}.lock")
        self._handle = None
        self._locked = False

    def acquire(self):
        if self._locked:
            return self
        try:
            import fcntl
        except ImportError:  # pragma: no cover - the supported worker hosts are POSIX.
            fcntl = None
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, mode=0o700, exist_ok=True)
        handle = open(self.path, "a+", encoding="ascii")
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._handle = handle
            self._locked = True
            return self
        except (BlockingIOError, OSError) as exc:
            handle.close()
            raise RuntimeError("企微机器人已被其他 Agent worker 占用") from exc

    def release(self) -> None:
        if not self._handle:
            return
        try:
            import fcntl
        except ImportError:  # pragma: no cover
            fcntl = None
        try:
            if fcntl is not None:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None
            self._locked = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.release()


def _body(frame):
    return frame.get("body", {}) if isinstance(frame, dict) and isinstance(frame.get("body", {}), dict) else {}


def _reply_frame(frame) -> dict[str, Any]:
    headers = frame.get("headers", {}) if isinstance(frame, dict) else {}
    req_id = headers.get("req_id") or headers.get("reqid") or ""
    return {"headers": {"req_id": str(req_id)}}


def _stream_id(frame) -> str | None:
    body = _body(frame)
    value = body.get("stream_id") or body.get("streamid")
    return str(value) if value else None


def _is_single(body) -> bool:
    return (body.get("chattype") or body.get("chat_type")) == "single"


def parse_text_frame(frame, *, bot_id=None):
    body = _body(frame)
    sender = body.get("from") or {}
    text = body.get("text") or {}
    msgtype = body.get("msgtype")
    msgid = body.get("msgid") or body.get("id")
    user_id = sender.get("userid") or sender.get("user_id")
    content = text.get("content") if isinstance(text, dict) else None
    if msgtype and str(msgtype).lower() not in {"text", "1"}:
        return None
    if not msgid or not user_id or not _is_single(body) or not isinstance(content, str):
        return None
    content = content.strip()
    if not 1 <= len(content) <= 2000:
        return None
    bot_id = bot_id if bot_id is not None else os.environ.get("WECOM_BOT_ID", "")
    return {
        "msgid": str(msgid),
        "wecom_user_id": str(user_id),
        "content": content,
        "request_id": str(uuid5(BOT_NAMESPACE, f"{bot_id}:{msgid}")),
    }


async def _maybe_await(value):
    return await value if inspect.isawaitable(value) else value


async def _reply(client, frame, content: str, *, stream_id: str | None = None, finish: bool = True) -> bool:
    """Send exactly one response attempt; callers decide how to record failure."""
    try:
        if stream_id and hasattr(client, "reply_stream"):
            await _maybe_await(client.reply_stream(_reply_frame(frame), stream_id, content, finish))
        elif hasattr(client, "reply"):
            await _maybe_await(client.reply(_reply_frame(frame), {
                "msgtype": "text", "text": {"content": content},
            }))
        else:
            return False
    except Exception:
        return False
    return True


def _remember_delivery(task_id: int, client, frame, stream_id: str | None) -> None:
    if len(_PENDING) >= MAX_PENDING_DELIVERIES:
        oldest = min(_PENDING, key=lambda key: _PENDING[key].created_at)
        _PENDING.pop(oldest, None)
    _PENDING[task_id] = PendingDelivery(client, _reply_frame(frame), stream_id, monotonic())


def _binding(bot_id: str, wecom_user_id: str):
    with store.db.connect() as conn:
        row = store.db._exec(conn.cursor(), """SELECT user_id FROM agent_v2_wecom_bindings
            WHERE bot_id=? AND wecom_user_id=? AND status='active'""", (bot_id, wecom_user_id)).fetchone()
    return dict(row) if row else None


def _conversation(user_id: int, bot_id: str, wecom_user_id: str, *, new: bool = False) -> int:
    with store.db.connect() as conn:
        cur = conn.cursor()
        if not new:
            row = store.db._exec(cur, """SELECT id FROM closing_review_conversations
                WHERE user_id=? AND channel='wecom' AND kind='v2_conversation' AND status='active'
                ORDER BY id DESC LIMIT 1""", (user_id,)).fetchone()
            if row:
                return int(row["id"])
        title = "企业微信私聊"
        created = store.stamp()
        return store.db._last_insert_id(cur, """INSERT INTO closing_review_conversations
            (user_id,channel,kind,title,status,system_key,created_at,updated_at)
            VALUES (?,'wecom','v2_conversation',?,'active',?,?,?)""",
            (user_id, title, f"{bot_id}:{wecom_user_id}:{uuid4()}", created, created))


def _looks_like_pair_code(content: str) -> bool:
    return bool(PAIR_CODE_RE.fullmatch(content))


async def handle_text(frame, client, *, bot_id=None):
    """Validate one SDK text event, enqueue it, and acknowledge without blocking."""
    bot_id = bot_id or os.environ.get("WECOM_BOT_ID", "")
    parsed = parse_text_frame(frame, bot_id=bot_id)
    if parsed is None:
        body = _body(frame)
        if _is_single(body):
            await _reply(client, frame, "首版仅支持本人私聊文本消息，图片、文件和其他消息暂不支持。",
                         stream_id=_stream_id(frame), finish=True)
        return "unsupported"
    binding = _binding(bot_id, parsed["wecom_user_id"])
    if not binding:
        if _looks_like_pair_code(parsed["content"]):
            try:
                paired = store.consume_pair_code(bot_id, parsed["wecom_user_id"], parsed["content"])
            except Exception:
                paired = False
            reply = "已完成本人绑定，可以开始提问。" if paired else "配对码无效或已过期，请在网页 Agent 页面重新申请。"
            await _reply(client, frame, reply, stream_id=_stream_id(frame), finish=True)
            return "paired" if paired else "unpaired"
        await _reply(client, frame, "请先在网页 Agent 页面申请配对码，再私聊发送配对码完成本人绑定。",
                     stream_id=_stream_id(frame), finish=True)
        return "unpaired"

    user_id = int(binding["user_id"])
    if parsed["content"].casefold() in {item.casefold() for item in NEW_CONVERSATION_COMMANDS}:
        _conversation(user_id, bot_id, parsed["wecom_user_id"], new=True)
        await _reply(client, frame, "已新建对话，后续问题将从新的会话开始。",
                     stream_id=_stream_id(frame), finish=True)
        return "conversation_created"

    conversation_id = _conversation(user_id, bot_id, parsed["wecom_user_id"])
    task_id = store.enqueue({"id": user_id}, conversation_id, parsed["request_id"], parsed["content"], "wecom")
    existing = store.task_status(task_id)
    if existing and existing.get("state") in {"succeeded", "partial", "failed", "cancelled"}:
        # The idempotent request already reached a terminal state. A repeated
        # WeCom event must not replay the business answer or create a second
        # external side effect.
        return task_id
    stream_id = _stream_id(frame)
    _remember_delivery(task_id, client, frame, stream_id)
    acknowledged = await _reply(client, frame, "已收到，正在读取宏源交易事实。", stream_id=stream_id, finish=False)
    if not acknowledged:
        # The final answer still gets one attempt; the state is kept in DB when
        # that attempt succeeds or is unknown.
        store.mark_delivery(task_id, "delivery_unknown", "processing_ack_failed")
    return task_id


async def deliver_task(task_id: int) -> bool:
    """Deliver one completed answer once and record delivered/unknown explicitly."""
    pending = _PENDING.pop(int(task_id), None)
    if pending is None:
        return False
    answer = store.task_answer(int(task_id))
    if not answer:
        store.mark_delivery(int(task_id), "delivery_unknown", "answer_missing")
        return False
    sent = await _reply(pending.client, pending.frame, answer["content"], stream_id=pending.stream_id, finish=True)
    store.mark_delivery(int(task_id), "delivered" if sent else "delivery_unknown",
                        None if sent else "final_reply_failed")
    return sent


def is_enabled() -> bool:
    return os.environ.get("AGENT_V2_WECOM_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def build_client(handler: Callable[..., Awaitable[Any]] | None = None):
    """Construct and register the official SDK client without connecting it."""
    from aibot import WSClient, WSClientOptions

    bot_id = os.environ.get("WECOM_BOT_ID", "").strip()
    secret = os.environ.get("WECOM_BOT_SECRET", "").strip()
    if not bot_id or not secret:
        raise RuntimeError("企微机器人配置不完整")
    client = WSClient(WSClientOptions(bot_id=bot_id, secret=secret))
    callback = handler or handle_text

    async def on_text(frame):
        return await callback(frame, client, bot_id=bot_id)

    client.on("message.text", on_text)
    return client
