import asyncio
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
