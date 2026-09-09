import asyncio
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.trading_agent import harness, store
from app.trading_agent.model import ModelTurn
from app.trading_agent.contracts import ToolEnvelope
from test_store import queued


class RepeatModel:
    def __init__(self): self.calls = 0
    def next_turn(self, messages, schemas, timeout):
        self.calls += 1
        return ModelTurn(tool_calls=[{"id":f"c{self.calls}","name":"describe_capabilities","arguments":{} }])


class KnowledgeModel:
    def next_turn(self, messages, schemas, timeout):
        return ModelTurn(content='{"status":"complete","paragraphs":[{"kind":"knowledge","text":"风险分析需要明确数据和假设。"}]}')


class UnsupportedModel:
    def next_turn(self, messages, schemas, timeout):
        return ModelTurn(content='{"status":"unsupported","paragraphs":[{"kind":"knowledge","text":"这个请求暂不支持。"}]}')


class FakeMCP:
    def __init__(self): self.calls = 0
    async def call_tool(self, name, args, grant):
        self.calls += 1
        return ToolEnvelope(status="complete",captured_at=datetime.now(timezone.utc),calculation_version="test",payload={"kind":"capabilities"})


class CatalogMCP(FakeMCP):
    async def list_tools(self, grant):
        return {"tools": [{"name": "describe_capabilities", "inputSchema": {"type": "object"}}]}


class SlowMCP(FakeMCP):
    async def call_tool(self, name, args, grant):
        await asyncio.sleep(0.2)
        return await super().call_tool(name, args, grant)


class ScriptedModel:
    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []

    def next_turn(self, messages, schemas, timeout):
        self.calls.append(deepcopy(messages))
        if not self.turns:
            raise AssertionError("模型被额外调用")
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn


BAD_ANSWER = '{"status":"complete","paragraphs":[{"kind":"knowledge","text":"说明"}],"evidence_refs":[]}'
GOOD_ANSWER = '{"status":"complete","paragraphs":[{"kind":"knowledge","text":"说明","evidence_refs":[]}],"fact_refs":[],"missing":[],"clarification":null}'


@pytest.mark.asyncio
async def test_budget_stops_repeated_tool_requests(queued):
    task = store.claim_next("harness-worker")
    model, mcp = RepeatModel(), FakeMCP()
    result = await harness.run_task(task, harness.RuntimeDeps(store, model, mcp, worker_id="harness-worker"))
    assert mcp.calls <= 8
    assert model.calls <= 6
    assert result.status in {"partial", "temporarily_unavailable"}
    with __import__("app").db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_v2_execution_grants WHERE task_id=? AND revoked_at IS NULL", (task,)).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_knowledge_answer_needs_no_fact_tool(queued):
    task = store.claim_next("harness-worker")
    result = await harness.run_task(task, harness.RuntimeDeps(store, KnowledgeModel(), FakeMCP(), worker_id="harness-worker"))
    assert result.status == "complete"


@pytest.mark.asyncio
async def test_non_complete_answer_maps_to_terminal_partial_task_state(queued):
    task = store.claim_next("harness-worker")
    result = await harness.run_task(task, harness.RuntimeDeps(store, UnsupportedModel(), FakeMCP(), worker_id="harness-worker"))
    assert result.status == "unsupported"
    with __import__("app").db.connect() as conn:
        assert conn.execute("SELECT state FROM agent_v2_runs WHERE task_id=?", (task,)).fetchone()[0] == "partial"


@pytest.mark.asyncio
async def test_harness_uses_live_mcp_catalog_when_available(queued):
    task = store.claim_next("harness-worker")
    result = await harness.run_task(task, harness.RuntimeDeps(store, KnowledgeModel(), CatalogMCP(), worker_id="harness-worker"))
    assert result.status == "complete"
    with __import__("app").db.connect() as conn:
        event = conn.execute("SELECT kind FROM agent_v2_events WHERE task_id=? AND kind='tool_catalog'", (task,)).fetchone()
    assert event is not None


@pytest.mark.asyncio
async def test_harness_stops_a_slow_tool_at_the_task_deadline(queued):
    task = store.claim_next("deadline-worker")
    deps = harness.RuntimeDeps(
        store,
        KnowledgeModel(),
        SlowMCP(),
        worker_id="deadline-worker",
        limits=harness.RuntimeLimits(deadline_seconds=0.02, model_timeout_seconds=0.01, tool_timeout_seconds=0.02),
    )

    result = await harness.run_task(task, deps)

    assert result.status in {"partial", "temporarily_unavailable"}


@pytest.mark.asyncio
async def test_answer_repair_success(queued):
    task = store.claim_next("repair-worker")
    model = ScriptedModel([ModelTurn(content=BAD_ANSWER), ModelTurn(content=GOOD_ANSWER)])

    result = await harness.run_task(task, harness.RuntimeDeps(store, model, FakeMCP(), worker_id="repair-worker"))

    assert result.status == "complete"
    assert len(model.calls) == 2
    repair_context = "\n".join(item.get("content", "") for item in model.calls[1])
    assert BAD_ANSWER in repair_context
    assert "extra_field" in repair_context
    with __import__("app").db.connect() as conn:
        event = conn.execute(
            "SELECT error_code FROM agent_v2_events WHERE task_id=? AND kind='answer_validation'",
            (task,),
        ).fetchone()
        row = conn.execute("SELECT state FROM closing_review_tasks WHERE id=?", (task,)).fetchone()
        message = conn.execute(
            "SELECT content FROM closing_review_messages WHERE task_id=? AND role='assistant' ORDER BY id DESC LIMIT 1",
            (task,),
        ).fetchone()
    assert event["error_code"] == "extra_field"
    assert row["state"] == "succeeded"
    assert message["content"] == "说明"


@pytest.mark.asyncio
async def test_answer_repair_stops_after_second_invalid(queued):
    task = store.claim_next("repair-stop-worker")
    model = ScriptedModel([ModelTurn(content=BAD_ANSWER), ModelTurn(content=BAD_ANSWER), ModelTurn(content=GOOD_ANSWER)])

    result = await harness.run_task(task, harness.RuntimeDeps(store, model, FakeMCP(), worker_id="repair-stop-worker"))

    assert result.status == "partial"
    assert len(model.calls) == 2
    with __import__("app").db.connect() as conn:
        grants = conn.execute(
            "SELECT COUNT(*) AS count FROM agent_v2_execution_grants WHERE task_id=? AND revoked_at IS NULL",
            (task,),
        ).fetchone()["count"]
    assert grants == 0


@pytest.mark.asyncio
async def test_answer_repair_respects_model_budget(queued):
    task = store.claim_next("repair-budget-worker")
    model = ScriptedModel([ModelTurn(content=BAD_ANSWER), ModelTurn(content=GOOD_ANSWER)])
    deps = harness.RuntimeDeps(
        store, model, FakeMCP(), worker_id="repair-budget-worker",
        limits=harness.RuntimeLimits(max_models=1),
    )

    result = await harness.run_task(task, deps)

    assert result.status == "partial"
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_answer_repair_respects_deadline(queued):
    class ExpiringClock:
        expired = False

        def __call__(self):
            return 1.0 if self.expired else 0.0

    clock = ExpiringClock()

    class ExpiringModel(ScriptedModel):
        def next_turn(self, messages, schemas, timeout):
            result = super().next_turn(messages, schemas, timeout)
            clock.expired = True
            return result

    task = store.claim_next("repair-deadline-worker")
    model = ExpiringModel([ModelTurn(content=BAD_ANSWER), ModelTurn(content=GOOD_ANSWER)])
    deps = harness.RuntimeDeps(
        store, model, FakeMCP(), clock=clock, worker_id="repair-deadline-worker",
        limits=harness.RuntimeLimits(deadline_seconds=0.5),
    )

    result = await harness.run_task(task, deps)

    assert result.status == "partial"
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_answer_repair_does_not_execute_new_tools(queued):
    task = store.claim_next("repair-tools-worker")
    model = ScriptedModel([
        ModelTurn(content=BAD_ANSWER),
        ModelTurn(tool_calls=[{"id": "repair-call", "name": "describe_capabilities", "arguments": {}}]),
    ])
    mcp = FakeMCP()

    result = await harness.run_task(task, harness.RuntimeDeps(store, model, mcp, worker_id="repair-tools-worker"))

    assert result.status == "partial"
    assert len(model.calls) == 2
    assert mcp.calls == 1


@pytest.mark.asyncio
async def test_answer_repair_stops_when_context_would_overflow(queued, monkeypatch):
    task = store.claim_next("repair-context-worker")
    model = ScriptedModel([ModelTurn(content=BAD_ANSWER), ModelTurn(content=GOOD_ANSWER)])
    monkeypatch.setattr(harness, "_messages_size", lambda messages: 48001)

    result = await harness.run_task(task, harness.RuntimeDeps(store, model, FakeMCP(), worker_id="repair-context-worker"))

    assert result.status == "partial"
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_unexpected_render_failure_is_not_answer_repair(queued, monkeypatch):
    task = store.claim_next("render-failure-worker")
    model = ScriptedModel([ModelTurn(content=GOOD_ANSWER)])

    def fail_render(*args, **kwargs):
        raise RuntimeError("SYNTHETIC_RENDER_FAILURE")

    monkeypatch.setattr(harness.answer, "render_answer", fail_render)
    with pytest.raises(RuntimeError, match="SYNTHETIC_RENDER_FAILURE"):
        await harness.run_task(task, harness.RuntimeDeps(store, model, FakeMCP(), worker_id="render-failure-worker"))
    assert len(model.calls) == 1
    with __import__("app").db.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS count FROM agent_v2_events WHERE task_id=? AND kind='answer_validation'",
            (task,),
        ).fetchone()["count"]
    assert count == 0


@pytest.mark.asyncio
async def test_storage_failure_is_not_answer_repair(queued, monkeypatch):
    task = store.claim_next("storage-failure-worker")
    model = ScriptedModel([ModelTurn(content=GOOD_ANSWER)])

    def fail_finish(*args, **kwargs):
        raise RuntimeError("SYNTHETIC_STORE_FAILURE")

    monkeypatch.setattr(store, "finish", fail_finish)
    with pytest.raises(RuntimeError, match="SYNTHETIC_STORE_FAILURE"):
        await harness.run_task(task, harness.RuntimeDeps(store, model, FakeMCP(), worker_id="storage-failure-worker"))
    assert len(model.calls) == 1
    with __import__("app").db.connect() as conn:
        event = conn.execute(
            "SELECT COUNT(*) AS count FROM agent_v2_events WHERE task_id=? AND kind='answer_validation'",
            (task,),
        ).fetchone()["count"]
        grants = conn.execute(
            "SELECT COUNT(*) AS count FROM agent_v2_execution_grants WHERE task_id=? AND revoked_at IS NULL",
            (task,),
        ).fetchone()["count"]
    assert event == 0
    assert grants == 0
