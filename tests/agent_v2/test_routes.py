import os
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db
from app.trading_agent import store
from app.trading_agent.contracts import MetricValue, ToolEnvelope
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


def test_capabilities_expose_market_dataset_only_with_display_permission(route_client):
    client, uid = route_client
    without_display = client.get("/trading-agent-v2/capabilities")
    assert without_display.status_code == 200
    assert "query_market_series" not in {item["name"] for item in without_display.json()["tools"]}
    assert without_display.json()["datasets"] == {}

    with db.connect() as conn:
        conn.execute(
            "INSERT INTO module_permissions(user_id,module_code,can_view,can_edit) VALUES (?, 'data_visualization_chart', 1, 0)",
            (uid,),
        )
    with_display = client.get("/trading-agent-v2/capabilities")
    assert "query_market_series" in {item["name"] for item in with_display.json()["tools"]}
    assert with_display.json()["datasets"]["iron_ore_basis"]["source"] == "iron_ore_basis_results"


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


def _finished_view_task(route_client):
    client, uid = route_client
    conversation = client.post("/trading-agent-v2/conversations", json={}).json()
    task_id = client.post(
        f"/trading-agent-v2/conversations/{conversation['id']}/messages",
        json={"client_request_id": str(uuid4()), "content": "查看冻结持仓"},
    ).json()["task_id"]
    assert store.claim_next("view-worker") == task_id
    principal = store.principal_for_task(task_id)
    ref = store.save_result(
        principal,
        ToolEnvelope(
            status="partial",
            captured_at=store.now(),
            calculation_version="view-test",
            payload={"kind": "positions"},
            metrics={"quantity": MetricValue(value="6", unit="手", status="complete", covered_rows=3, eligible_rows=3)},
        ),
        [
            {"row_ref": "r1", "contract": "i2609", "direction": "买", "quantity": "1", "valuation_price": None},
            {"row_ref": "r2", "contract": "i2609", "direction": "卖", "quantity": "2", "valuation_price": "700"},
            {"row_ref": "r3", "contract": "i2610", "direction": "买", "quantity": "3", "valuation_price": "701"},
        ],
    )
    validated = {
        "schema_version": "2.1",
        "delivery_status": "partial",
        "body_markdown": "已保留持仓表。",
        "plain_text": "已保留持仓表。",
        "evidence": [],
        "views": [{
            "id": "v1", "kind": "table", "result_ref": str(ref),
            "fields": ["contract", "quantity", "valuation_price"], "title": "持仓表",
        }],
        "limitations": [],
    }
    assert store.finish(task_id, "view-worker", "partial", "已保留持仓表。", structured_payload=validated)
    with db.connect() as conn:
        message_id = conn.execute(
            "SELECT id FROM closing_review_messages WHERE task_id=? AND role='assistant' ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()["id"]
    return client, uid, conversation["id"], message_id, ref


def test_completed_view_can_be_read_without_active_run(route_client):
    client, _, conversation_id, message_id, _ = _finished_view_task(route_client)

    response = client.get(
        f"/trading-agent-v2/conversations/{conversation_id}/messages/{message_id}/views/v1",
        params={"page": 1, "page_size": 20},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["view_id"] == "v1"
    assert payload["pagination"]["total_rows"] == 3
    assert payload["rows"][0]["valuation_price"] is None
    assert payload["coverage"]["eligible_quantity"] == "6"
    assert payload["coverage"]["covered_quantity"] == "5"


def test_history_and_task_do_not_expose_answer_after_permission_revocation(route_client):
    client, uid, conversation_id, message_id, _ = _finished_view_task(route_client)
    with db.connect() as conn:
        task_id = conn.execute("SELECT task_id FROM closing_review_messages WHERE id=?", (message_id,)).fetchone()[0]
    history_path = f"/trading-agent-v2/conversations/{conversation_id}/messages"
    assert "已保留持仓表" in client.get(history_path).text
    with db.connect() as conn:
        conn.execute("UPDATE module_permissions SET can_view=0 WHERE user_id=? AND module_code='trading_positions'", (uid,))
    history = client.get(history_path)
    assert history.status_code == 200
    answer = next(item for item in history.json()["items"] if item["id"] == message_id)
    assert answer["structured_payload"] is None
    assert "已保留持仓表" not in answer["content"]
    task = client.get(f"/trading-agent-v2/tasks/{task_id}").json()
    assert "已保留持仓表" not in task["answer"]
    assert task["result_refs"] == []


def test_view_rechecks_live_permission_and_expiry(route_client):
    client, uid, conversation_id, message_id, ref = _finished_view_task(route_client)
    path = f"/trading-agent-v2/conversations/{conversation_id}/messages/{message_id}/views/v1"

    with db.connect() as conn:
        conn.execute(
            "UPDATE module_permissions SET can_view=0 WHERE user_id=? AND module_code='trading_positions'",
            (uid,),
        )
    assert client.get(path).status_code == 403

    with db.connect() as conn:
        conn.execute(
            "UPDATE module_permissions SET can_view=1 WHERE user_id=? AND module_code='trading_positions'",
            (uid,),
        )
        conn.execute(
            "UPDATE agent_v2_results SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (str(ref),),
        )
    assert client.get(path).status_code == 410
