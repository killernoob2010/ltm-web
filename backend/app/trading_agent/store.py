"""Persistent bounded tasks and immutable evidence with explicit ownership."""
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException

from .. import db
from .auth import authorize, resolve_principal, _live_user
from .contracts import Principal, StoredResult, ToolEnvelope


class RequestConflict(ValueError):
    pass


class ResultExpired(LookupError):
    pass


def now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def stamp(value=None):
    return (value or now()).isoformat(timespec="seconds")


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _transaction(conn):
    if not db._is_pg():
        conn.execute("BEGIN IMMEDIATE")


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
        db._exec(cur, """INSERT INTO agent_v2_runs
            (task_id,execution_id,user_id,channel,account_scope_json,request_hash,state,created_at)
            VALUES (?,?,?,?,?,?,'queued',?)""",
            (task_id, str(principal.execution_id), principal.user_id, channel,
             json.dumps(principal.account_ids), request_hash, timestamp))
        mid = db._last_insert_id(cur, """INSERT INTO closing_review_messages
            (conversation_id,task_id,role,message_type,content,status,created_at)
            VALUES (?,?,'user','text',?,'active',?)""", (conversation_id, task_id, text, timestamp))
        db._exec(cur, "UPDATE closing_review_tasks SET user_message_id=? WHERE id=?", (mid, task_id))
    return task_id


def claim_next(worker_id):
    timestamp = now()
    with db.connect() as conn:
        _transaction(conn)
        cur = conn.cursor()
        suffix = " FOR UPDATE SKIP LOCKED" if db._is_pg() else ""
        row = db._exec(cur, "SELECT task_id FROM agent_v2_runs WHERE state='queued' ORDER BY created_at,task_id LIMIT 1" + suffix).fetchone()
        if not row:
            return None
        task_id = int(row["task_id"])
        db._exec(cur, """UPDATE agent_v2_runs SET state='running',lease_owner=?,lease_expires_at=?,deadline_at=?
            WHERE task_id=? AND state='queued'""",
            (worker_id, stamp(timestamp + timedelta(seconds=30)), stamp(timestamp + timedelta(seconds=90)), task_id))
        db._exec(cur, "UPDATE closing_review_tasks SET state='running',started_at=? WHERE id=?", (stamp(timestamp),task_id))
    return task_id


def heartbeat(task_id, worker_id):
    with db.connect() as conn:
        cur = db._exec(conn.cursor(), """UPDATE agent_v2_runs SET lease_expires_at=?
            WHERE task_id=? AND lease_owner=? AND state='running' AND lease_expires_at>? AND deadline_at>?""",
            (stamp(now()+timedelta(seconds=30)), task_id, worker_id, stamp(), stamp()))
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
        db._exec(cur, "INSERT INTO agent_v2_execution_grants(token_hash,task_id,user_id,expires_at) VALUES (?,?,?,?)",
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


def save_result(principal, envelope: ToolEnvelope, rows, *, kind="positions", parent_ref=None):
    authorize(principal, "trading.facts")
    if len(rows) > 20000:
        raise ValueError("limit_exceeded")
    if parent_ref:
        load_result(principal, parent_ref)
    ref = uuid4()
    frozen = envelope.model_copy(deep=True, update={"result_ref": ref})
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


def load_result(principal, ref):
    authorize(principal, "trading.facts")
    with db.connect() as conn:
        row = db._exec(conn.cursor(), "SELECT * FROM agent_v2_results WHERE id=? AND user_id=? AND conversation_id=?",
            (str(UUID(str(ref))),principal.user_id,principal.conversation_id)).fetchone()
    if not row:
        raise HTTPException(404, "结果不存在")
    if datetime.fromisoformat(row["expires_at"]) <= now():
        raise ResultExpired("结果已过期")
    payload = json.loads(row["payload_json"])
    if digest(row["payload_json"]) != row["source_hash"]:
        raise ValueError("evidence_integrity_error")
    return StoredResult(ref=ref,envelope=payload["envelope"],rows=payload["rows"],
        owner_user_id=principal.user_id,conversation_id=principal.conversation_id,expires_at=row["expires_at"])


def _terminal(cur, task_id, state, timestamp, error=None):
    db._exec(cur, "UPDATE agent_v2_runs SET state=?,finished_at=?,last_error=?,lease_expires_at=NULL WHERE task_id=?", (state,timestamp,error,task_id))
    db._exec(cur, "UPDATE closing_review_tasks SET state=?,finished_at=?,error_category=? WHERE id=?", (state,timestamp,error,task_id))
    db._exec(cur, "UPDATE agent_v2_execution_grants SET revoked_at=? WHERE task_id=? AND revoked_at IS NULL", (timestamp,task_id))


def finish(task_id, worker_id, state, answer, error=None):
    if state not in {"succeeded", "partial", "failed", "cancelled"}:
        raise ValueError("invalid terminal state")
    with db.connect() as conn:
        _transaction(conn)
        cur = conn.cursor()
        suffix = " FOR UPDATE" if db._is_pg() else ""
        row = db._exec(cur, """SELECT t.conversation_id FROM agent_v2_runs r JOIN closing_review_tasks t ON t.id=r.task_id
            WHERE r.task_id=? AND r.lease_owner=? AND r.state='running' AND r.lease_expires_at>?""" + suffix,
            (task_id,worker_id,stamp())).fetchone()
        if not row:
            return False
        timestamp = stamp()
        _terminal(cur,task_id,state,timestamp,error)
        db._exec(cur, """INSERT INTO closing_review_messages(conversation_id,task_id,role,message_type,content,status,created_at)
            VALUES (?,?,'assistant',?,?,'active',?)""", (row["conversation_id"],task_id,"error" if state=="failed" else "answer",answer,timestamp))
    return True


def recover_interrupted():
    with db.connect() as conn:
        _transaction(conn)
        cur = conn.cursor()
        suffix = " FOR UPDATE" if db._is_pg() else ""
        rows = db._exec(cur, "SELECT task_id FROM agent_v2_runs WHERE state='running' AND (lease_expires_at<=? OR deadline_at<=?)" + suffix,
            (stamp(),stamp())).fetchall()
        for row in rows:
            _terminal(cur,int(row["task_id"]),"failed",stamp(),"interrupted")
    return len(rows)


def issue_pair_code(user_id):
    from .. import permissions
    user = _live_user(user_id)
    permissions.require_permission(user, "closing_review.agent", "view")
    code = secrets.token_urlsafe(18)
    with db.connect() as conn:
        _transaction(conn)
        cur = conn.cursor()
        db._exec(cur, "UPDATE agent_v2_pair_codes SET consumed_at=? WHERE user_id=? AND consumed_at IS NULL", (stamp(),user_id))
        db._exec(cur, "INSERT INTO agent_v2_pair_codes(code_hash,user_id,expires_at) VALUES (?,?,?)",
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
        db._exec(cur, """INSERT INTO agent_v2_wecom_bindings(bot_id,wecom_user_id,user_id,status,created_at)
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
