from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db
from app.trading_agent import quality, quality_routes, store
from app.trading_agent.schema import migrate_agent_v2_schema


def _admin_client(monkeypatch):
    db.init_db()
    monkeypatch.setenv("AGENT_V2_PILOT_USERNAME", "synthetic_pilot")
    with db.connect() as conn:
        pilot_id = conn.execute(
            "INSERT INTO users(name,username,department,password_hash,role) VALUES "
            "('Pilot','synthetic_pilot','期货组','x','用户')"
        ).lastrowid
        conn.execute(
            "INSERT INTO module_permissions(user_id,module_code,can_view,can_edit) VALUES (?, 'closing_review_agent', 1, 0)",
            (pilot_id,),
        )
        conversation_id = conn.execute(
            "INSERT INTO closing_review_conversations(user_id,channel,kind,title) "
            "VALUES (?,'web','v2_conversation','quality')",
            (pilot_id,),
        ).lastrowid
        admin_id = conn.execute(
            "INSERT INTO users(name,username,department,password_hash,role) VALUES "
            "('Quality Admin','quality_admin','管理部门','x','管理员')"
        ).lastrowid
        migrate_agent_v2_schema(conn)
    task_id = store.enqueue({"id": pilot_id}, conversation_id, str(uuid4()), "查持仓", "web")
    app = FastAPI()
    app.include_router(quality_routes.router, prefix="/api")
    app.dependency_overrides[quality_routes.trading_management_current_user] = lambda: {
        "id": admin_id,
        "username": "quality_admin",
        "role": "管理员",
        "department": "管理部门",
    }
    return TestClient(app), task_id, admin_id


def test_quality_routes_are_admin_only_and_separate_runtime_from_live_evaluation(monkeypatch):
    client, task_id, _ = _admin_client(monkeypatch)

    summary = client.get("/api/admin/agent-quality/summary")
    assert summary.status_code == 200
    assert summary.json()["runtime"]["run_count"] == 1
    assert summary.json()["evaluation"]["live"]["status"] == "not_recorded"

    runs = client.get("/api/admin/agent-quality/runs", params={"page_size": 10})
    assert runs.status_code == 200
    assert runs.json()["items"][0]["task_id"] == task_id
    assert "question" not in runs.json()["items"][0]

    detail = client.get(f"/api/admin/agent-quality/runs/{task_id}")
    assert detail.status_code == 200
    assert detail.json()["question"] == "查持仓"

    feedback = client.post(
        f"/api/admin/agent-quality/runs/{task_id}/feedback",
        json={"label": "needs_review", "note": "核对证据"},
    )
    assert feedback.status_code == 200
    assert feedback.json()["feedback"]["label"] == "needs_review"
    assert quality.build_summary()["quality"]["evaluated_count"] == 1


def test_quality_routes_reject_non_admin_even_if_module_permission_exists(monkeypatch):
    client, _, _ = _admin_client(monkeypatch)
    client.app.dependency_overrides[quality_routes.trading_management_current_user] = lambda: {
        "id": 1,
        "username": "ordinary",
        "role": "用户",
        "department": "期货组",
    }

    assert client.get("/api/admin/agent-quality/summary").status_code == 403
