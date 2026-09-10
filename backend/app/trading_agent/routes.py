"""Asynchronous Web boundary for Agent V2; model work is performed by worker."""
from datetime import datetime, timezone
import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import ConfigDict, Field

from .. import db
from ..trading_management import trading_management_current_user
from ..permissions import can, require_permission
from . import catalog, market_data, presentation, progress, store, tools
from .auth import pilot_allows_user
from .contracts import StrictModel

router = APIRouter(prefix="/trading-agent-v2")


class ConversationIn(StrictModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(default="智能贸易助手对话", min_length=1, max_length=80)


class MessageIn(StrictModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    client_request_id: UUID
    content: str = Field(min_length=1, max_length=2000)


def is_enabled() -> bool:
    return os.environ.get("AGENT_V2_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _require(user: dict, *, schema=False):
    if not is_enabled():
        raise HTTPException(404, "Trading Agent V2 未启用")
    if not pilot_allows_user(user):
        raise HTTPException(404, "Trading Agent V2 不可用")
    require_permission(user, "closing_review.agent", "view")
    if schema:
        with db.connect() as conn:
            exists = db._exec(conn.cursor(), "SELECT name FROM sqlite_master WHERE name='agent_v2_runs'").fetchone() if not db._is_pg() else db._exec(conn.cursor(), "SELECT to_regclass('public.agent_v2_runs') AS name").fetchone()
        if not exists or not exists["name"]:
            raise HTTPException(503, "Trading Agent V2 尚未完成数据库迁移")


def _seconds(value):
    if value is None:
        return None
    return str(value).split(".", 1)[0]


def _conversation(row):
    return {"id": row["id"], "channel": row["channel"], "kind": row["kind"], "title": row["title"],
            "status": row["status"], "created_at": _seconds(row.get("created_at")), "updated_at": _seconds(row.get("updated_at"))}


def _readable_answer(user_id, conversation_id, content, payload):
    if not isinstance(payload, dict) or payload.get("schema_version") != "2.1":
        return content, payload
    refs = {item["result_ref"] for field in ("views", "evidence")
            for item in payload.get(field, []) if isinstance(item, dict) and item.get("result_ref")}
    try:
        for ref in refs:
            store.load_result_for_view(user_id, conversation_id, ref)
    except (HTTPException, store.ResultExpired, ValueError):
        return "该答案的来源已过期或当前权限无法读取，请在权限恢复后重新查询。", None
    return content, payload


def _owned_conversation(user_id, conversation_id):
    with db.connect() as conn:
        row = db._exec(conn.cursor(), """SELECT * FROM closing_review_conversations
            WHERE id=? AND user_id=? AND channel='web' AND kind='v2_conversation' AND status='active'""", (conversation_id,user_id)).fetchone()
    return dict(row) if row else None


@router.get("/capabilities")
def capabilities(user: dict = Depends(trading_management_current_user)):
    _require(user)
    market_allowed = can(user, "trading.facts", "view") and can(user, "data_visualization.display", "view")
    return {"enabled": True, "version": "2.0", "engine": "agent-v2", "account_scope": "宏源期货",
            "tools": tools.tool_schemas(include_market=market_allowed), "dimensions": catalog.DIMENSIONS,
            "metrics": {kind: sorted(items) for kind, items in catalog.METRICS.items()},
            "datasets": {market_data.DATASET: market_data.dataset_catalog()} if market_allowed else {},
            "defaults": {"as_of": "latest", "timezone": "Asia/Shanghai"}}


@router.post("/conversations")
def create_conversation(payload: ConversationIn | None = None, user: dict = Depends(trading_management_current_user)):
    _require(user, schema=True)
    title = payload.title if payload else "智能贸易助手对话"
    with db.connect() as conn:
        row_id = db._last_insert_id(conn.cursor(), """INSERT INTO closing_review_conversations
            (user_id,channel,kind,title,status,created_at,updated_at) VALUES (?,'web','v2_conversation',?,'active',?,?)""",
            (user["id"],title,datetime.now(timezone.utc).replace(microsecond=0).isoformat(timespec="seconds"),datetime.now(timezone.utc).replace(microsecond=0).isoformat(timespec="seconds")))
        row = db._exec(conn.cursor(), "SELECT * FROM closing_review_conversations WHERE id=?", (row_id,)).fetchone()
    return _conversation(dict(row))


@router.get("/conversations")
def list_conversations(user: dict = Depends(trading_management_current_user)):
    _require(user, schema=True)
    with db.connect() as conn:
        rows = db._exec(conn.cursor(), """SELECT * FROM closing_review_conversations
            WHERE user_id=? AND channel='web' AND kind='v2_conversation' AND status='active' ORDER BY id DESC""", (user["id"],)).fetchall()
    return {"items": [_conversation(dict(row)) for row in rows]}


@router.get("/conversations/{conversation_id}/messages")
def list_messages(conversation_id: int, user: dict = Depends(trading_management_current_user)):
    _require(user, schema=True)
    conversation = _owned_conversation(user["id"], conversation_id)
    if not conversation:
        raise HTTPException(404, "对话不存在")
    with db.connect() as conn:
        rows = db._exec(conn.cursor(), """SELECT * FROM closing_review_messages
            WHERE conversation_id=? ORDER BY id ASC LIMIT 200""", (conversation_id,)).fetchall()
    items=[]
    for row in rows:
        value=dict(row)
        import json
        try: structured=json.loads(value.get("structured_payload")) if value.get("structured_payload") else None
        except (TypeError,ValueError): structured=None
        content = value.get("content")
        if value.get("role") == "assistant":
            content, structured = _readable_answer(user["id"], conversation_id, content, structured)
        items.append({"id":value["id"],"conversation_id":conversation_id,"task_id":value.get("task_id"),"role":value.get("role"),
                      "message_type":value.get("message_type"),"content":content,"structured_payload":structured,
                      "created_at":_seconds(value.get("created_at"))})
    with db.connect() as conn:
        active = db._exec(conn.cursor(), """SELECT t.*,r.delivery_state
            FROM closing_review_tasks t LEFT JOIN agent_v2_runs r ON r.task_id=t.id
            WHERE t.conversation_id=? AND t.user_id=? AND t.task_kind='v2_user_message'
              AND t.state NOT IN ('succeeded','partial','failed','cancelled')
            ORDER BY t.id DESC LIMIT 1""", (conversation_id, user["id"])).fetchone()
    active_task = None
    if active:
        active_task = dict(active)
        active_task["progress"] = progress.project_progress(
            active_task, store.stage_events(int(active_task["id"])), datetime.now(timezone.utc)
        )
        active_task = {
            "task_id": active_task["id"],
            "state": active_task["state"],
            "progress": active_task["progress"],
        }
    return {"conversation":_conversation(conversation),"items":items,"active_task":active_task}


@router.post("/conversations/{conversation_id}/messages")
def post_message(conversation_id: int, payload: MessageIn, response: Response, user: dict = Depends(trading_management_current_user)):
    _require(user, schema=True)
    conversation = _owned_conversation(user["id"], conversation_id)
    if not conversation:
        raise HTTPException(404, "对话不存在")
    request_id = payload.client_request_id
    content = payload.content
    try:
        task_id = store.enqueue(user, conversation_id, request_id, content.strip(), "web")
    except store.RequestConflict as exc:
        raise HTTPException(409, str(exc))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    with db.connect() as conn:
        task = db._exec(conn.cursor(), "SELECT state FROM closing_review_tasks WHERE id=?", (task_id,)).fetchone()
    response.status_code = 200 if task and task["state"] in {"succeeded","partial","failed","cancelled"} else 202
    return {"task_ref": str(task_id), "task_id": task_id, "state": task["state"] if task else "queued"}


@router.get("/tasks/{task_id}")
def get_task(task_id: int, user: dict = Depends(trading_management_current_user)):
    _require(user, schema=True)
    store.recover_interrupted()
    with db.connect() as conn:
        task = db._exec(conn.cursor(), """SELECT t.*,r.delivery_state FROM closing_review_tasks t
            LEFT JOIN agent_v2_runs r ON r.task_id=t.id
            WHERE t.id=? AND t.user_id=? AND t.task_kind='v2_user_message'""", (task_id,user["id"])).fetchone()
        if not task:
            raise HTTPException(404, "任务不存在")
        message = db._exec(conn.cursor(), "SELECT content,structured_payload FROM closing_review_messages WHERE task_id=? AND role='assistant' ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
    task = dict(task)
    result={"task_id":task_id,"conversation_id":task["conversation_id"],"state":task["state"],"delivery_state":task.get("delivery_state"),"finished_at":_seconds(task["finished_at"]),"answer":None,"result_refs":[]}
    result["progress"] = progress.project_progress(
        task, store.stage_events(task_id), datetime.now(timezone.utc)
    )
    result["poll_timeout_seconds"] = int(store._queue_timeout_seconds() + store.execution.TASK_SECONDS + 15)
    if message:
        import json
        try: result["answer"]=message["content"]; payload=json.loads(message["structured_payload"]) if message["structured_payload"] else {}
        except (TypeError,ValueError): payload={}
        result["answer"], payload = _readable_answer(user["id"], task["conversation_id"], result["answer"], payload)
        if isinstance(payload, dict):
            refs = list(payload.get("fact_refs", []))
            refs.extend(item.get("result_ref") for item in payload.get("evidence", []) if isinstance(item, dict) and item.get("result_ref"))
            refs.extend(item.get("result_ref") for item in payload.get("views", []) if isinstance(item, dict) and item.get("result_ref"))
            result["result_refs"] = list(dict.fromkeys(refs))
    return result


@router.get("/conversations/{conversation_id}/messages/{message_id}/views/{view_id}")
def get_message_view(
    conversation_id: int,
    message_id: int,
    view_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20),
    user: dict = Depends(trading_management_current_user),
):
    _require(user, schema=True)
    conversation = _owned_conversation(user["id"], conversation_id)
    if not conversation:
        raise HTTPException(404, "对话不存在")
    if page_size not in {20, 50, 100}:
        raise HTTPException(422, "分页大小无效")
    with db.connect() as conn:
        row = db._exec(conn.cursor(), """SELECT id, structured_payload
            FROM closing_review_messages
            WHERE id=? AND conversation_id=? AND role='assistant'""",
            (message_id, conversation_id)).fetchone()
    if not row:
        raise HTTPException(404, "消息不存在")
    import json
    try:
        payload = json.loads(row["structured_payload"] or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(404, "视图不存在") from None
    if not isinstance(payload, dict) or payload.get("schema_version") != "2.1":
        raise HTTPException(404, "视图不存在")
    raw_view = next(
        (item for item in payload.get("views", [])
         if isinstance(item, dict) and item.get("id") == view_id),
        None,
    )
    if raw_view is None:
        raise HTTPException(404, "视图不存在")
    try:
        view = presentation.ViewRequest.model_validate(raw_view)
        saved = store.load_result_for_view(user["id"], conversation_id, view.result_ref)
        return presentation.build_view(
            None, view, store, page=page, page_size=page_size, saved=saved
        )
    except store.ResultExpired:
        raise HTTPException(410, "结果已过期") from None
    except HTTPException:
        raise
    except (TypeError, ValueError):
        raise HTTPException(404, "视图不存在") from None


@router.post("/wecom/pair-code")
def pair_code(response: Response, user: dict = Depends(trading_management_current_user)):
    _require(user, schema=True)
    code = store.issue_pair_code(int(user["id"]))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {"code": code, "expires_in_seconds": 600}
