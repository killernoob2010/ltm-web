from datetime import datetime, timedelta, timezone
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from app import db
from app.trading_agent import store
from app.trading_agent.schema import migrate_agent_v2_schema
from app.trading_agent.contracts import ToolEnvelope


@pytest.fixture
def queued(pilot):
    with db.connect() as conn:
        migrate_agent_v2_schema(conn)
        migrate_agent_v2_schema(conn)
    uid, cid = pilot
    task = store.enqueue({"id": uid}, cid, str(uuid4()), "查持仓", "web")
    return uid, cid, task


def test_request_id_conflict_and_duplicate_are_atomic(queued):
    uid, cid, _ = queued
    request = str(uuid4())
    def enqueue():
        return store.enqueue({"id": uid}, cid, request, "查期权", "web")
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: enqueue(), range(2)))
    assert ids[0] == ids[1]
    with pytest.raises(store.RequestConflict):
        store.enqueue({"id": uid}, cid, request, "查期货", "web")
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM closing_review_messages WHERE task_id=?", (ids[0],)).fetchone()[0] == 1


def test_competing_workers_claim_only_once(queued):
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(store.claim_next, ["a", "b"]))
    assert sum(value is not None for value in claims) == 1


def test_snapshot_is_immutable_and_unavailable_after_expiry(queued):
    task = store.claim_next("a")
    principal = store.principal_for_task(task)
    result = ToolEnvelope(status="complete", captured_at=datetime.now(timezone.utc), calculation_version="test")
    rows = [{"quantity": "2"}]
    ref = store.save_result(principal, result, rows)
    rows[0]["quantity"] = "999"
    assert store.load_result(principal, ref).rows == [{"quantity": "2"}]
    with db.connect() as conn:
        conn.execute("UPDATE agent_v2_results SET expires_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", str(ref)))
    with pytest.raises(store.ResultExpired):
        store.load_result(principal, ref)


def test_grant_revoked_at_terminal_task(queued):
    task = store.claim_next("a")
    principal = store.principal_for_task(task)
    token = store.issue_grant(principal)
    assert store.resolve_grant(token) == principal
    store.finish(task, "a", "succeeded", "合成测试完成")
    with pytest.raises(HTTPException) as error:
        store.resolve_grant(token)
    assert error.value.status_code == 401


def test_interrupted_task_not_requeued(queued):
    task = store.claim_next("a")
    with db.connect() as conn:
        conn.execute("UPDATE agent_v2_runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE task_id=?", (task,))
    assert store.recover_interrupted() == 1
    assert store.claim_next("b") is None
    with db.connect() as conn:
        assert conn.execute("SELECT state FROM closing_review_tasks WHERE id=?", (task,)).fetchone()[0] == "failed"


def test_pair_code_is_one_use_and_five_failures_lock_it(queued):
    uid, cid, _ = queued
    code = store.issue_pair_code(uid)
    for _ in range(5):
        assert not store.consume_pair_code("synthetic-bot", "external-user", "invalid")
    assert not store.consume_pair_code("synthetic-bot", "external-user", code)
    code = store.issue_pair_code(uid)
    assert store.consume_pair_code("synthetic-bot", "external-user", code)
    assert not store.consume_pair_code("synthetic-bot", "external-user", code)


def test_foreign_conversation_cannot_read_result(queued):
    from dataclasses import replace
    uid, cid, _ = queued
    task = store.claim_next("a")
    principal = store.principal_for_task(task)
    ref = store.save_result(principal, ToolEnvelope(status="complete", captured_at=datetime.now(timezone.utc), calculation_version="test"), [])
    with db.connect() as conn:
        other_cid = conn.execute("INSERT INTO closing_review_conversations(user_id,channel,kind,title) VALUES (?,'web','v2_conversation','other')", (uid,)).lastrowid
    with pytest.raises(HTTPException) as error:
        store.load_result(replace(principal, conversation_id=other_cid), ref)
    assert error.value.status_code == 404
