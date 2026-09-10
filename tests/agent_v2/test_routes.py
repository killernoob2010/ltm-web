import os
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db
from app.trading_agent import routes
from app.trading_agent.schema import migrate_agent_v2_schema


@pytest.fixture
def route_client(monkeypatch):
    db.init_db(); monkeypatch.setenv("AGENT_V2_ENABLED", "true"); monkeypatch.setenv("AGENT_V2_PILOT_USERNAME", "synthetic_pilot")
    with db.connect() as conn:
        uid=conn.execute("INSERT INTO users(name,username,department,password_hash,role) VALUES ('Pilot','synthetic_pilot','期货组','x','用户')").lastrowid
        for mod in ("closing_review_agent","trading_positions"):
            conn.execute("INSERT INTO module_permissions(user_id,module_code,can_view,can_edit) VALUES (?,?,1,0)",(uid,mod))
    with db.connect() as conn: migrate_agent_v2_schema(conn)
    app=FastAPI(); app.include_router(routes.router)
    app.dependency_overrides[routes.trading_management_current_user]=lambda: {"id":uid,"username":"synthetic_pilot","role":"用户"}
    return TestClient(app),uid


def test_v2_routes_are_async_and_idempotent(route_client):
    client,_=route_client
    conversation=client.post("/trading-agent-v2/conversations",json={"title":"开放分析"}).json()
    request_id=str(uuid4())
    first=client.post(f"/trading-agent-v2/conversations/{conversation['id']}/messages",json={"client_request_id":request_id,"content":"给我现在持仓"})
    second=client.post(f"/trading-agent-v2/conversations/{conversation['id']}/messages",json={"client_request_id":request_id,"content":"给我现在持仓"})
    assert first.status_code==202 and second.status_code==202
    assert first.json()["task_id"]==second.json()["task_id"]


def test_foreign_conversation_is_not_disclosed(route_client):
    client,_=route_client
    assert client.get("/trading-agent-v2/conversations/999999/messages").status_code==404


def test_message_contract_rejects_unknown_fields_and_pair_code_is_not_cached(route_client):
    client, _ = route_client
    conversation = client.post("/trading-agent-v2/conversations", json={}).json()
    bad = client.post(f"/trading-agent-v2/conversations/{conversation['id']}/messages", json={
        "client_request_id": str(uuid4()), "content": "查持仓", "account_id": 1,
    })
    assert bad.status_code == 422
    pair = client.post("/trading-agent-v2/wecom/pair-code")
    assert pair.status_code == 200
    assert pair.headers["cache-control"] == "no-store"


def test_web_and_pairing_use_system_permission_without_named_pilot(route_client, monkeypatch):
    client, uid = route_client
    monkeypatch.delenv("AGENT_V2_PILOT_USERNAME", raising=False)
    assert client.get("/trading-agent-v2/capabilities").status_code == 200
    assert client.post("/trading-agent-v2/wecom/pair-code").status_code == 200
    with db.connect() as conn:
        conn.execute("UPDATE module_permissions SET can_view=0 WHERE user_id=? AND module_code='closing_review_agent'", (uid,))
    assert client.get("/trading-agent-v2/capabilities").status_code == 403
    assert client.post("/trading-agent-v2/wecom/pair-code").status_code == 403


def test_poll_closes_expired_task_when_worker_is_unavailable(route_client):
    client, _ = route_client
    conversation = client.post('/trading-agent-v2/conversations', json={}).json()
    task = client.post(f"/trading-agent-v2/conversations/{conversation['id']}/messages", json={
        'client_request_id': str(uuid4()), 'content': '合成排队测试',
    }).json()['task_id']
    with db.connect() as conn:
        conn.execute("UPDATE agent_v2_runs SET created_at='2000-01-01T00:00:00+00:00' WHERE task_id=?", (task,))
    response = client.get(f'/trading-agent-v2/tasks/{task}').json()
    assert response['state'] == 'failed'
    assert '排队' in response['answer']
    assert response['poll_timeout_seconds'] >= 210
