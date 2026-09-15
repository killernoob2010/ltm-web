from types import SimpleNamespace

import pytest

from app import db
from app.trading_agent import runtime_dispatch, store
from app.trading_agent.schema import migrate_agent_v2_schema
from test_store import queued


def test_runtime_selector_is_fail_closed_and_sticky():
    config = {
        "backend": "pydantic",
        "pilot_user_ids": {42},
        "disabled": False,
    }

    assert runtime_dispatch.select_backend(42, 9, config) == "pydantic"
    assert runtime_dispatch.select_backend(7, 9, config) == "legacy"
    assert runtime_dispatch.select_backend(42, 9, {**config, "disabled": True}) == "legacy"
    assert runtime_dispatch.select_backend(42, 9, config, bound_backend="legacy") == "legacy"
    assert runtime_dispatch.select_backend(42, 9, config, bound_backend="pydantic") == "pydantic"

    with pytest.raises(ValueError, match="invalid_runtime_backend"):
        runtime_dispatch.select_backend(42, 9, {"backend": "unknown", "pilot_user_ids": set()})


def test_runtime_binding_is_persisted_once_for_a_conversation(queued):
    with db.connect() as conn:
        migrate_agent_v2_schema(conn)
    uid, _, task_id = queued
    task_id = store.claim_next("runtime-binding-worker")
    principal = store.principal_for_task(task_id)

    assert store.runtime_backend_for_task(task_id) is None
    assert store.bind_runtime(principal, "pydantic") == "pydantic"
    assert store.bind_runtime(principal, "legacy") == "pydantic"
    assert store.runtime_backend_for_task(task_id) == "pydantic"

    with db.connect() as conn:
        rows = conn.execute(
            "SELECT kind,status FROM agent_v2_events WHERE task_id=? AND kind='runtime_selected'",
            (task_id,),
        ).fetchall()
    assert [dict(row) for row in rows] == [{"kind": "runtime_selected", "status": "pydantic"}]
    assert uid == principal.user_id


@pytest.mark.asyncio
async def test_dispatch_routes_pilot_to_sdk_and_keeps_session_binding(monkeypatch):
    monkeypatch.setenv("AGENT_V2_RUNTIME_BACKEND", "pydantic")
    monkeypatch.setenv("AGENT_V2_RUNTIME_PILOT_USER_IDS", "42")
    calls = []

    class FakeStore:
        def principal_for_task(self, task_id):
            return SimpleNamespace(user_id=42, conversation_id=9)

        def runtime_backend_for_task(self, task_id):
            return None

        def bind_runtime(self, principal, backend):
            calls.append(("bind", backend))
            return backend

    async def fake_pydantic(task_id, deps):
        calls.append(("pydantic", task_id))
        return "sdk-result"

    async def fake_legacy(task_id, deps):
        calls.append(("legacy", task_id))
        return "legacy-result"

    monkeypatch.setattr(runtime_dispatch.pydantic_runtime, "run_task", fake_pydantic)
    monkeypatch.setattr(runtime_dispatch.harness, "run_task", fake_legacy)
    result = await runtime_dispatch.run_task(19, SimpleNamespace(store=FakeStore()))

    assert result == "sdk-result"
    assert calls == [("bind", "pydantic"), ("pydantic", 19)]


@pytest.mark.asyncio
async def test_runtime_disable_overrides_existing_sdk_binding(monkeypatch):
    monkeypatch.setenv("AGENT_V2_RUNTIME_BACKEND", "pydantic")
    monkeypatch.setenv("AGENT_V2_RUNTIME_PILOT_USER_IDS", "42")
    monkeypatch.setenv("AGENT_V2_PYDANTIC_DISABLED", "true")
    calls = []

    class FakeStore:
        def principal_for_task(self, task_id):
            return SimpleNamespace(user_id=42, conversation_id=9)

        def runtime_backend_for_task(self, task_id):
            return "pydantic"

        def bind_runtime(self, principal, backend):
            calls.append(("bind", backend))
            return "pydantic"

    async def fake_pydantic(task_id, deps):
        calls.append(("pydantic", task_id))
        return "sdk-result"

    async def fake_legacy(task_id, deps):
        calls.append(("legacy", task_id))
        return "legacy-result"

    monkeypatch.setattr(runtime_dispatch.pydantic_runtime, "run_task", fake_pydantic)
    monkeypatch.setattr(runtime_dispatch.harness, "run_task", fake_legacy)
    result = await runtime_dispatch.run_task(19, SimpleNamespace(store=FakeStore()))

    assert result == "legacy-result"
    assert calls == [("legacy", 19)]
