import asyncio
import pytest

from app.trading_agent import harness, store, worker
from app.trading_agent.model import ModelTurn
from test_harness import KnowledgeModel, FakeMCP
from test_store import queued


@pytest.mark.asyncio
async def test_worker_once_claims_and_finishes_one_task(queued):
    deps = harness.RuntimeDeps(store=store, model=KnowledgeModel(), mcp=FakeMCP(), worker_id="worker-once")
    result = await worker.worker_once(deps)
    assert result.status == "complete"
    with __import__("app").db.connect() as conn:
        assert conn.execute("SELECT state FROM agent_v2_runs WHERE task_id=?", (queued[2],)).fetchone()[0] == "succeeded"


@pytest.mark.asyncio
async def test_worker_loop_can_stop_without_importing_main(queued):
    import sys

    was_loaded = "app.main" in sys.modules
    stop = asyncio.Event()
    stop.set()
    deps = harness.RuntimeDeps(store=store, model=KnowledgeModel(), mcp=FakeMCP(), worker_id="worker-loop")
    await worker.worker_loop(deps, stop_event=stop)
    assert ("app.main" in sys.modules) is was_loaded
