"""Persistent bounded tasks and immutable evidence with explicit ownership."""
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException

from .. import db
from .auth import authorize, resolve_principal, _live_user
from .contracts import Principal, StoredResult, ToolEnvelope
from . import execution


class RequestConflict(ValueError):
    pass


class QueueFull(RuntimeError):
    pass


class ResultExpired(LookupError):
    pass


QUEUE_LOCK_KEY = 641_219_587


def now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def stamp(value=None):
    return (value or now()).isoformat(timespec="seconds")


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _exec_no_return(cur, sql, params=()):
    """Execute adjunct inserts whose primary key is not named ``id``."""
    if db._is_pg():
        cur.execute(sql.replace("?", "%s"), params)
    else:
        cur.execute(sql, params)
    return cur


def _transaction(conn):
    if not db._is_pg():
        conn.execute("BEGIN IMMEDIATE")


def _env_number(name, default, cast):
    try:
        return max(0, cast(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _queue_limit():
    return _env_number("AGENT_V2_MAX_QUEUED_TASKS", 3, int)


def _queue_timeout_seconds():
    return _env_number("AGENT_V2_QUEUE_TIMEOUT_SECONDS", 120, float)


def _expire_queued(cur, timestamp=None):
    timestamp = timestamp or now()
    cutoff = stamp(timestamp - timedelta(seconds=_queue_timeout_seconds()))
    rows = db._exec(cur, "SELECT task_id FROM agent_v2_runs WHERE state='queued' AND created_at<=?", (cutoff,)).fetchall()
    for row in rows:
        task_id = int(row["task_id"])
        finished = stamp(timestamp)
        _terminal(cur, task_id, "failed", finished, "queue_timeout")
        task = db._exec(cur, "SELECT conversation_id FROM closing_review_tasks WHERE id=?", (task_id,)).fetchone()
        if task:
            db._exec(cur, """INSERT INTO closing_review_messages
                (conversation_id,task_id,role,message_type,content,status,created_at)
                VALUES (?,?,'assistant','error',?,'active',?)""",
                (task["conversation_id"], task_id,
                 f"排队等待超过 {_queue_timeout_seconds():g} 秒，本次任务已结束，请重新提问。", finished))
    return len(rows)


def enqueue(user, conversation_id, request_id, text, channel):
    request_id = str(UUID(str(request_id)))
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise ValueError("问题长度应为1至2000字")
    principal = resolve_principal(int(user["id"]), channel, conversation_id, uuid4())
    request_hash = digest(text)
    timestamp = stamp()
    with db.connect() as conn:
        _transaction(conn)
        cur = conn.cursor()
        if db._is_pg():
            # Serialize the small admission check across Web requests.
            db._exec(cur, "SELECT pg_advisory_xact_lock(?)", (QUEUE_LOCK_KEY,))
        # Same unique key as claim_user_task, but message + V2 run must commit together.
        db._exec(cur, """INSERT OR IGNORE INTO closing_review_tasks
            (user_id,conversation_id,client_request_id,task_kind,state,created_at)
            VALUES (?,?,?,'v2_user_message','queued',?)""",
            (principal.user_id, conversation_id, request_id, timestamp))
        task = db._exec(cur, "SELECT * FROM closing_review_tasks WHERE user_id=? AND client_request_id=?",
                        (principal.user_id, request_id)).fetchone()
        task_id = int(task["id"])
        if task["conversation_id"] != conversation_id or task["task_kind"] != "v2_user_message":
            raise RequestConflict("请求编号已被其他问题使用")
        if db._is_pg():
            db._exec(cur, "SELECT id FROM closing_review_tasks WHERE id=? FOR UPDATE", (task_id,))
        existing = db._exec(cur, "SELECT request_hash FROM agent_v2_runs WHERE task_id=?", (task_id,)).fetchone()
        if existing:
            if existing["request_hash"] != request_hash:
                raise RequestConflict("请求编号已被其他问题使用")
            return task_id
        _expire_queued(cur, datetime.fromisoformat(timestamp))
        queued = db._exec(cur, "SELECT COUNT(*) AS count FROM agent_v2_runs WHERE state='queued'").fetchone()
        if int(queued["count"]) >= _queue_limit():
            raise QueueFull("当前 Agent 排队已满，请稍后重试")
        _exec_no_return(cur, """INSERT INTO agent_v2_runs
            (task_id,execution_id,user_id,channel,account_scope_json,request_hash,state,created_at)
            VALUES (?,?,?,?,?,?,'queued',?)""",
            (task_id, str(principal.execution_id), principal.user_id, channel,
             json.dumps(principal.account_ids), request_hash, timestamp))
        mid = db._last_insert_id(cur, """INSERT INTO closing_review_messages
            (conversation_id,task_id,role,message_type,content,status,created_at)
            VALUES (?,?,'user','text',?,'active',?)""", (conversation_id, task_id, text, timestamp))
        db._exec(cur, "UPDATE closing_review_tasks SET user_message_id=? WHERE id=?", (mid, task_id))
        db._exec(cur, """UPDATE closing_review_conversations
            SET last_message_at=?,updated_at=? WHERE id=? AND user_id=? AND status='active'""",
            (timestamp,timestamp,conversation_id,principal.user_id))
    return task_id


def claim_next(worker_id, *, deadline_seconds=execution.TASK_SECONDS):
    timestamp = now()
    with db.connect() as conn:
        _transaction(conn)
        cur = conn.cursor()
        _expire_queued(cur, timestamp)
        suffix = " FOR UPDATE SKIP LOCKED" if db._is_pg() else ""
        row = db._exec(cur, "SELECT task_id FROM agent_v2_runs WHERE state='queued' ORDER BY created_at,task_id LIMIT 1" + suffix).fetchone()
        if not row:
            return None
        task_id = int(row["task_id"])
        db._exec(cur, """UPDATE agent_v2_runs SET state='running',lease_owner=?,lease_expires_at=?,deadline_at=?
            WHERE task_id=? AND state='queued'""",
            (worker_id, stamp(timestamp + timedelta(seconds=execution.LEASE_SECONDS)), stamp(timestamp + timedelta(seconds=deadline_seconds)), task_id))
        db._exec(cur, "UPDATE closing_review_tasks SET state='running',started_at=? WHERE id=?", (stamp(timestamp),task_id))
    return task_id


def heartbeat(task_id, worker_id):
    with db.connect() as conn:
        cur = db._exec(conn.cursor(), """UPDATE agent_v2_runs SET lease_expires_at=?
            WHERE task_id=? AND lease_owner=? AND state='running' AND lease_expires_at>? AND deadline_at>?""",
            (stamp(now()+timedelta(seconds=execution.LEASE_SECONDS)), task_id, worker_id, stamp(), stamp()))
        return cur.rowcount == 1


def principal_for_task(task_id):
    with db.connect() as conn:
        row = db._exec(conn.cursor(), """SELECT r.*,t.conversation_id FROM agent_v2_runs r
            JOIN closing_review_tasks t ON t.id=r.task_id WHERE r.task_id=?""", (task_id,)).fetchone()
    if not row:
        raise HTTPException(404, "任务不存在")
    principal = resolve_principal(int(row["user_id"]), row["channel"], int(row["conversation_id"]), UUID(row["execution_id"]))
    if list(principal.account_ids) != json.loads(row["account_scope_json"]):
        raise HTTPException(403, "账户范围已变化")
    return principal


def _active_run(cur, principal):
    execution.checkpoint()
    row = db._exec(cur, """SELECT r.task_id FROM agent_v2_runs r JOIN closing_review_tasks t ON t.id=r.task_id
        WHERE r.execution_id=? AND r.user_id=? AND t.conversation_id=? AND r.state='running'
        AND r.deadline_at>? AND r.lease_expires_at>?""",
        (str(principal.execution_id), principal.user_id, principal.conversation_id, stamp(), stamp())).fetchone()
    if not row:
        raise HTTPException(401, "任务已失效")
    return int(row["task_id"])


def issue_grant(principal):
    authorize(principal, "closing_review.agent")
    token = secrets.token_urlsafe(32)
    with db.connect() as conn:
        cur = conn.cursor()
        task_id = _active_run(cur, principal)
        _exec_no_return(cur, "INSERT INTO agent_v2_execution_grants(token_hash,task_id,user_id,expires_at) VALUES (?,?,?,?)",
            (digest(token), task_id, principal.user_id, stamp(now()+timedelta(minutes=5))))
    return token


def resolve_grant(token):
    if not isinstance(token, str) or not 20 <= len(token) <= 128:
        raise HTTPException(401, "执行凭证无效")
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(cur, "SELECT task_id FROM agent_v2_execution_grants WHERE token_hash=? AND revoked_at IS NULL AND expires_at>?",
            (digest(token), stamp())).fetchone()
        if not row:
            raise HTTPException(401, "执行凭证无效")
        principal = principal_for_task(int(row["task_id"]))
        _active_run(cur, principal)
    return principal


def save_result(
    principal,
    envelope: ToolEnvelope,
    rows,
    *,
    kind="positions",
    parent_ref=None,
    result_ref=None,
    required_resources=None,
    account_scope=None,
):
    authorize(principal, "trading.facts")
    if len(rows) > 20000:
        raise ValueError("limit_exceeded")
    if parent_ref:
        load_result(principal, parent_ref)
    ref = result_ref or uuid4()
    payload = dict(envelope.payload or {})
    existing_resources = payload.get("required_resources")
    if required_resources is None:
        required_resources = existing_resources or (
            ["trading.facts", "data_visualization.display"]
            if kind == "market_series" else ["trading.facts"]
        )
    if not isinstance(required_resources, (list, tuple)):
        raise ValueError("required_resources must be a list")
    if account_scope is None:
        account_scope = principal.account_ids
    try:
        account_scope = [int(value) for value in account_scope]
    except (TypeError, ValueError):
        raise ValueError("account_scope must be numeric") from None
    payload["required_resources"] = list(dict.fromkeys(str(value) for value in required_resources))
    payload["account_scope"] = account_scope
    frozen = envelope.model_copy(deep=True, update={"result_ref": ref,
        "snapshot_ref": ref if kind=="positions" and not parent_ref else envelope.snapshot_ref,
        "payload": payload})
    payload = json.dumps({"envelope": frozen.model_dump(mode="json"), "rows": rows}, ensure_ascii=False, allow_nan=False)
    if len(payload.encode("utf-8")) > 10 * 1024 * 1024:
        raise ValueError("limit_exceeded")
    timestamp = now()
    with db.connect() as conn:
        cur = conn.cursor()
        task_id = _active_run(cur, principal)
        db._exec(cur, """INSERT INTO agent_v2_results
            (id,task_id,user_id,conversation_id,kind,parent_ref,snapshot_ref,payload_json,source_hash,
             schema_version,calculation_version,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (str(ref),task_id,principal.user_id,principal.conversation_id,kind,str(parent_ref) if parent_ref else None,
             str(frozen.snapshot_ref) if frozen.snapshot_ref else None,payload,digest(payload),"2.0",
             frozen.calculation_version,stamp(timestamp),stamp(timestamp+timedelta(days=90))))
    return ref


def load_result(principal, ref, *, require_current_task=False):
    authorize(principal, "trading.facts")
    with db.connect() as conn:
        cur = conn.cursor()
        params = [str(UUID(str(ref))), principal.user_id, principal.conversation_id]
        task_clause = ""
        if require_current_task:
            task_clause = " AND task_id=?"
            params.append(_active_run(cur, principal))
        row = db._exec(cur, "SELECT * FROM agent_v2_results WHERE id=? AND user_id=? AND conversation_id=?" + task_clause,
            tuple(params)).fetchone()
    if not row:
        raise HTTPException(404, "结果不存在")
    if datetime.fromisoformat(row["expires_at"]) <= now():
        raise ResultExpired("结果已过期")
    return _stored_result_from_row(row, principal, ref)


def _stored_result_from_row(row, principal, ref):
    if datetime.fromisoformat(row["expires_at"]) <= now():
        raise ResultExpired("结果已过期")
    payload = json.loads(row["payload_json"])
    if digest(row["payload_json"]) != row["source_hash"]:
        raise ValueError("evidence_integrity_error")
    return StoredResult(ref=ref, envelope=payload["envelope"], rows=payload["rows"],
        owner_user_id=principal.user_id,conversation_id=principal.conversation_id,
        parent_ref=UUID(str(row["parent_ref"])) if row["parent_ref"] else None,
        expires_at=row["expires_at"])


def _view_context(user_id, conversation_id, ref):
    try:
        ref = UUID(str(ref))
    except (TypeError, ValueError):
        raise HTTPException(404, "结果不存在") from None
    with db.connect() as conn:
        row = db._exec(conn.cursor(), """SELECT r.*, ar.channel, ar.execution_id, ar.account_scope_json
            FROM agent_v2_results r
            JOIN agent_v2_runs ar ON ar.task_id=r.task_id
            WHERE r.id=? AND r.user_id=? AND r.conversation_id=?""",
            (str(ref), int(user_id), int(conversation_id))).fetchone()
    if not row:
        raise HTTPException(404, "结果不存在")
    try:
        principal = resolve_principal(
            int(user_id), row["channel"], int(conversation_id), UUID(str(row["execution_id"]))
        )
    except (TypeError, ValueError):
        raise HTTPException(403, "结果账户范围无法确认") from None
    return row, principal, ref


def load_result_for_view(user_id, conversation_id, result_ref):
    """Load a frozen result after rechecking live permissions, without a grant."""
    row, principal, ref = _view_context(user_id, conversation_id, result_ref)
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(403, "结果完整性无法确认") from None
    if digest(row["payload_json"]) != row["source_hash"]:
        raise HTTPException(403, "结果完整性无法确认")
    envelope = payload.get("envelope") if isinstance(payload, dict) else None
    metadata = envelope.get("payload") if isinstance(envelope, dict) else None
    metadata = metadata if isinstance(metadata, dict) else {}
    required = metadata.get("required_resources")
    if not isinstance(required, list) or not required:
        kind = metadata.get("kind")
        required = ["trading.facts", "data_visualization.display"] if kind == "market_series" else ["trading.facts"]
    try:
        for resource in required:
            authorize(principal, str(resource))
    except HTTPException:
        raise
    except (TypeError, ValueError):
        raise HTTPException(403, "结果权限范围无法确认") from None
    saved_scope = metadata.get("account_scope")
    if saved_scope is None:
        try:
            saved_scope = json.loads(row["account_scope_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            raise HTTPException(403, "结果账户范围无法确认") from None
    try:
        if tuple(int(value) for value in saved_scope) != tuple(principal.account_ids):
            raise HTTPException(403, "结果账户范围已变化")
    except (TypeError, ValueError):
        raise HTTPException(403, "结果账户范围无法确认") from None
    return _stored_result_from_row(row, principal, ref)


def _terminal(cur, task_id, state, timestamp, error=None):
    db._exec(cur, "UPDATE agent_v2_runs SET state=?,finished_at=?,last_error=?,lease_expires_at=NULL WHERE task_id=?", (state,timestamp,error,task_id))
    db._exec(cur, "UPDATE closing_review_tasks SET state=?,finished_at=?,error_category=? WHERE id=?", (state,timestamp,error,task_id))
    db._exec(cur, "UPDATE agent_v2_execution_grants SET revoked_at=? WHERE task_id=? AND revoked_at IS NULL", (timestamp,task_id))


def fail_owned(task_id, worker_id, reason):
    """Close only this worker's unfinished run, including an expired lease."""
    with db.connect() as conn:
        _transaction(conn)
        cur = conn.cursor()
        suffix = " FOR UPDATE" if db._is_pg() else ""
        row = db._exec(cur, """SELECT t.conversation_id FROM agent_v2_runs r
            JOIN closing_review_tasks t ON t.id=r.task_id
            WHERE r.task_id=? AND r.lease_owner=? AND r.state='running'""" + suffix,
            (task_id, worker_id)).fetchone()
        if not row:
            return False
        timestamp = stamp()
        _terminal(cur, task_id, "failed", timestamp, reason)
        message = "本次分析已停止，请重新提问。" if reason == "worker_error" else "本次分析执行超时或连接中断，已停止，请重新提问。"
        db._exec(cur, """INSERT INTO closing_review_messages
            (conversation_id,task_id,role,message_type,content,status,created_at)
            VALUES (?,?,'assistant','error',?,'active',?)""",
            (row["conversation_id"], task_id, message, timestamp))
        db._exec(cur, "UPDATE closing_review_conversations SET last_message_at=?,updated_at=? WHERE id=?",
            (timestamp, timestamp, row["conversation_id"]))
    return True


def finish(task_id, worker_id, state, answer, error=None, structured_payload=None):
    if state not in {"succeeded", "partial", "failed", "cancelled"}:
        raise ValueError("invalid terminal state")
    if structured_payload is not None:
        try:
            structured_payload = json.dumps(structured_payload, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("structured_payload must be JSON serializable") from exc
    with db.connect() as conn:
        _transaction(conn)
        cur = conn.cursor()
        suffix = " FOR UPDATE" if db._is_pg() else ""
        row = db._exec(cur, """SELECT t.conversation_id,r.channel FROM agent_v2_runs r JOIN closing_review_tasks t ON t.id=r.task_id
            WHERE r.task_id=? AND r.lease_owner=? AND r.state='running' AND r.lease_expires_at>?""" + suffix,
            (task_id,worker_id,stamp())).fetchone()
        if not row:
            return False
        timestamp = stamp()
        _terminal(cur,task_id,state,timestamp,error)
        if row["channel"] == "web":
            db._exec(cur, "UPDATE agent_v2_runs SET delivery_state='delivered' WHERE task_id=?", (task_id,))
        db._exec(cur, """INSERT INTO closing_review_messages
            (conversation_id,task_id,role,message_type,content,structured_payload,status,created_at)
            VALUES (?,?,'assistant',?,?,?,?,?)""", (row["conversation_id"],task_id,
            "error" if state=="failed" else "answer",answer,structured_payload,"active",timestamp))
        db._exec(cur, """UPDATE closing_review_conversations
            SET last_message_at=?,updated_at=? WHERE id=? AND status='active'""",
            (timestamp,timestamp,row["conversation_id"]))
    return True


def mark_delivery(task_id, state, error=None):
    """Record one final-channel delivery attempt without retrying it implicitly."""
    if state not in {"pending", "delivered", "delivery_unknown"}:
        raise ValueError("invalid delivery state")
    with db.connect() as conn:
        db._exec(conn.cursor(), """UPDATE agent_v2_runs SET delivery_state=?,last_error=COALESCE(?,last_error)
            WHERE task_id=? AND state IN ('succeeded','partial','failed','cancelled')""",
            (state,error,task_id))


def task_answer(task_id):
    """Return the latest final assistant text for a V2 task, without raw tool data."""
    with db.connect() as conn:
        row = db._exec(conn.cursor(), """SELECT content,structured_payload FROM closing_review_messages
            WHERE task_id=? AND role='assistant' ORDER BY id DESC LIMIT 1""", (task_id,)).fetchone()
    if not row:
        return None
    row = dict(row)
    payload = None
    if row.get("structured_payload"):
        try:
            payload = json.loads(row["structured_payload"])
        except (TypeError, ValueError):
            payload = None
    return {"content": row.get("content") or "", "structured_payload": payload}


def task_status(task_id):
    """Return terminal/delivery state for one task without exposing its content."""
    with db.connect() as conn:
        row = db._exec(conn.cursor(), """SELECT t.state,r.delivery_state
            FROM closing_review_tasks t LEFT JOIN agent_v2_runs r ON r.task_id=t.id
            WHERE t.id=?""", (task_id,)).fetchone()
    return dict(row) if row else None


def revoke_task_grants(task_id):
    with db.connect() as conn:
        db._exec(conn.cursor(), "UPDATE agent_v2_execution_grants SET revoked_at=? WHERE task_id=? AND revoked_at IS NULL", (stamp(), task_id))


def task_text(task_id, user_id=None):
    with db.connect() as conn:
        params = [task_id]
        owner = ""
        if user_id is not None:
            owner = " AND t.user_id=?"
            params.append(user_id)
        row = db._exec(conn.cursor(), """SELECT m.content FROM closing_review_tasks t
            JOIN closing_review_messages m ON m.id=t.user_message_id
            WHERE t.id=?""" + owner, tuple(params)).fetchone()
    return str(row["content"]) if row else ""


def task_history(task_id, user_id=None, limit=12):
    """Read only recent user/assistant text for the task's own conversation."""
    safe_limit = max(1, min(int(limit or 12), 12))
    params = [task_id]
    owner = ""
    if user_id is not None:
        owner = " AND t.user_id=?"
        params.append(user_id)
    with db.connect() as conn:
        sql = """SELECT m.role,m.content,m.structured_payload,m.id
            FROM closing_review_tasks t
            JOIN closing_review_messages current ON current.id=t.user_message_id
            JOIN closing_review_messages m ON m.conversation_id=t.conversation_id AND m.id < current.id
            WHERE t.id=?""" + owner + """ AND m.status='active' AND m.role IN ('user','assistant')
            ORDER BY m.id DESC LIMIT ?"""
        rows = db._exec(conn.cursor(), sql, tuple(params + [safe_limit])).fetchall()
    history = []
    for row in reversed(rows):
        if row["role"] == "assistant":
            try:
                payload = json.loads(row["structured_payload"] or "{}")
            except (TypeError, ValueError):
                payload = {}
            refs = payload.get("fact_refs", []) if isinstance(payload, dict) else []
            status = payload.get("status", "") if isinstance(payload, dict) else ""
            content = f"上次回答状态：{status}；可追问的证据引用：{json.dumps(refs, ensure_ascii=False)}"
        else:
            content = str(row["content"] or "")
        history.append({"role": row["role"], "content": content})
    return history


def record_usage(task_id, *, model_calls=0, tool_calls=0, search_calls=0):
    with db.connect() as conn:
        db._exec(conn.cursor(), """UPDATE agent_v2_runs SET model_calls=model_calls+?, tool_calls=tool_calls+?, search_calls=search_calls+?
            WHERE task_id=? AND state='running'""", (model_calls, tool_calls, search_calls, task_id))


def recover_interrupted():
    with db.connect() as conn:
        _transaction(conn)
        cur = conn.cursor()
        suffix = " FOR UPDATE" if db._is_pg() else ""
        _expire_queued(cur)
        rows = db._exec(cur, "SELECT task_id FROM agent_v2_runs WHERE state='running' AND (lease_expires_at<=? OR deadline_at<=?)" + suffix,
            (stamp(),stamp())).fetchall()
        for row in rows:
            task_id = int(row["task_id"])
            _terminal(cur,task_id,"failed",stamp(),"interrupted")
            task = db._exec(cur, "SELECT conversation_id FROM closing_review_tasks WHERE id=?", (task_id,)).fetchone()
            if task:
                timestamp = stamp()
                db._exec(cur, """INSERT INTO closing_review_messages
                    (conversation_id,task_id,role,message_type,content,status,created_at)
                    VALUES (?,?,'assistant','error',?,'active',?)""",
                    (task["conversation_id"], task_id, "任务因 Agent worker 中断而停止，请重新提问。", timestamp))
                db._exec(cur, """UPDATE closing_review_conversations SET last_message_at=?,updated_at=?
                    WHERE id=? AND status='active'""", (timestamp,timestamp,task["conversation_id"]))
    return len(rows)


def mark_orphaned_deliveries():
    """A restart cannot reconstruct a WeCom reply context, so pending answers become unknown."""
    with db.connect() as conn:
        cur = db._exec(conn.cursor(), """UPDATE agent_v2_runs SET delivery_state='delivery_unknown',
            last_error=COALESCE(last_error,'delivery_context_lost')
            WHERE channel='wecom' AND state IN ('succeeded','partial','failed','cancelled')
              AND delivery_state='pending'""", ())
        return cur.rowcount


def issue_pair_code(user_id):
    from .. import permissions
    user = _live_user(user_id)
    permissions.require_permission(user, "closing_review.agent", "view")
    code = secrets.token_urlsafe(18)
    with db.connect() as conn:
        _transaction(conn)
        cur = conn.cursor()
        db._exec(cur, "UPDATE agent_v2_pair_codes SET consumed_at=? WHERE user_id=? AND consumed_at IS NULL", (stamp(),user_id))
        _exec_no_return(cur, "INSERT INTO agent_v2_pair_codes(code_hash,user_id,expires_at) VALUES (?,?,?)",
            (digest(code),user_id,stamp(now()+timedelta(minutes=10))))
    return code


def consume_pair_code(bot_id, wecom_user_id, code):
    if not bot_id or not wecom_user_id or not isinstance(code,str) or len(code)>128:
        return False
    from .. import permissions
    with db.connect() as conn:
        _transaction(conn)
        cur = conn.cursor()
        suffix = " FOR UPDATE" if db._is_pg() else ""
        rows = db._exec(cur, "SELECT * FROM agent_v2_pair_codes WHERE consumed_at IS NULL AND expires_at>? AND attempts<5" + suffix, (stamp(),)).fetchall()
        selected = next((row for row in rows if secrets.compare_digest(row["code_hash"],digest(code))),None)
        if not selected:
            # One configured pilot; failed guesses count against its outstanding codes.
            for row in rows:
                db._exec(cur, "UPDATE agent_v2_pair_codes SET attempts=attempts+1 WHERE code_hash=?", (row["code_hash"],))
            return False
        user = _live_user(int(selected["user_id"]))
        permissions.require_permission(user, "closing_review.agent", "view")
        bound = db._exec(cur, "SELECT user_id,wecom_user_id FROM agent_v2_wecom_bindings WHERE bot_id=? AND status='active'", (bot_id,)).fetchall()
        if any(row["user_id"] != user["id"] or row["wecom_user_id"] != wecom_user_id for row in bound):
            return False
        _exec_no_return(cur, """INSERT INTO agent_v2_wecom_bindings(bot_id,wecom_user_id,user_id,status,created_at)
            VALUES (?,?,?,'active',?) ON CONFLICT(bot_id,wecom_user_id)
            DO UPDATE SET status='active',revoked_at=NULL""", (bot_id,wecom_user_id,user["id"],stamp()))
        db._exec(cur, "UPDATE agent_v2_pair_codes SET consumed_at=? WHERE code_hash=?", (stamp(),selected["code_hash"]))
    return True


def append_event(principal, kind, *, tool_name=None, arguments=None, result_ref=None,
                 duration_seconds=None, status=None, error_code=None):
    with db.connect() as conn:
        _transaction(conn)
        cur = conn.cursor()
        task_id = _active_run(cur,principal)
        if db._is_pg():
            db._exec(cur, "SELECT task_id FROM agent_v2_runs WHERE task_id=? FOR UPDATE", (task_id,))
        row = db._exec(cur, "SELECT COALESCE(MAX(seq),0)+1 AS next_seq FROM agent_v2_events WHERE task_id=?", (task_id,)).fetchone()
        db._exec(cur, """INSERT INTO agent_v2_events
            (id,task_id,seq,kind,tool_name,argument_hash,result_ref,duration_seconds,status,error_code,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (str(uuid4()),task_id,int(row["next_seq"]),kind,tool_name,
            digest(json.dumps(arguments,sort_keys=True,ensure_ascii=False)) if arguments is not None else None,
            str(result_ref) if result_ref else None,duration_seconds,status,error_code,stamp()))
