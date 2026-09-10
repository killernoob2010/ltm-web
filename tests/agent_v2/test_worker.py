import asyncio
import pytest

from app.trading_agent import harness, store, worker
from app.trading_agent.model import ModelTurn
from app.trading_agent.resources import AdmissionDecision, ResourceSnapshot
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


@pytest.mark.asyncio
async def test_worker_loop_does_not_claim_when_resource_guard_blocks(queued):
    stop = asyncio.Event()

    class DenyGuard:
        def admit(self):
            stop.set()
            return AdmissionDecision(False, "resource_pressure", ResourceSnapshot(0.80, 0.10, "test"))

    deps = harness.RuntimeDeps(store, KnowledgeModel(), FakeMCP(), worker_id="guard-worker")
    await worker.worker_loop(deps, stop_event=stop, resource_guard=DenyGuard())

    with __import__("app").db.connect() as conn:
        assert conn.execute("SELECT state FROM agent_v2_runs WHERE task_id=?", (queued[2],)).fetchone()[0] == "queued"


@pytest.mark.asyncio
async def test_worker_cancels_analysis_when_lease_is_lost(queued, monkeypatch):
    from types import SimpleNamespace
    cancelled = asyncio.Event()
    async def slow_run(*args):
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.set()
    monkeypatch.setattr(harness, 'run_task', slow_run)
    proxy = SimpleNamespace(**{name: getattr(store, name) for name in dir(store) if not name.startswith('__')})
    proxy.heartbeat = lambda *args: False
    deps = harness.RuntimeDeps(proxy, KnowledgeModel(), FakeMCP(), worker_id='lost-lease')
    await worker.worker_once(deps, heartbeat_seconds=.01)
    assert cancelled.is_set()
    with __import__('app').db.connect() as conn:
        assert conn.execute('SELECT state FROM agent_v2_runs WHERE task_id=?', (queued[2],)).fetchone()[0] == 'failed'


@pytest.mark.asyncio
async def test_worker_failure_does_not_leave_running_task(queued, monkeypatch):
    async def broken(*args):
        raise RuntimeError('synthetic failure')
    monkeypatch.setattr(harness, 'run_task', broken)
    deps = harness.RuntimeDeps(store, KnowledgeModel(), FakeMCP(), worker_id='broken-worker')
    await worker.worker_once(deps)
    with __import__('app').db.connect() as conn:
        assert conn.execute('SELECT state FROM agent_v2_runs WHERE task_id=?', (queued[2],)).fetchone()[0] == 'failed'


@pytest.mark.asyncio
async def test_worker_heartbeat_does_not_block_event_loop(queued, monkeypatch):
    import time
    from types import SimpleNamespace
    progress = []
    async def short_run(*args):
        await asyncio.sleep(.04)
        progress.append(time.monotonic())
    proxy = SimpleNamespace(**{name: getattr(store, name) for name in dir(store) if not name.startswith('__')})
    def delayed_heartbeat(*args):
        time.sleep(.2)
        return True
    proxy.heartbeat = delayed_heartbeat
    monkeypatch.setattr(harness, 'run_task', short_run)
    deps = harness.RuntimeDeps(proxy, KnowledgeModel(), FakeMCP(), worker_id='slow-heartbeat')
    started = time.monotonic()
    await worker.worker_once(deps, heartbeat_seconds=.01)
    assert progress[0] - started < .15


@pytest.mark.asyncio
async def test_failed_task_does_not_block_next_question(queued, monkeypatch):
    from uuid import uuid4
    first = queued[2]
    second = store.enqueue({'id': queued[0]}, queued[1], str(uuid4()), '知识问题', 'web')
    original = harness.run_task
    async def fail_first(task_id, deps):
        if task_id == first:
            raise RuntimeError('synthetic failure')
        return await original(task_id, deps)
    monkeypatch.setattr(harness, 'run_task', fail_first)
    deps = harness.RuntimeDeps(store, KnowledgeModel(), FakeMCP(), worker_id='recovery-worker')
    await worker.worker_once(deps)
    result = await worker.worker_once(deps)
    assert result.status == 'complete'
    with __import__('app').db.connect() as conn:
        states = dict(conn.execute('SELECT task_id,state FROM agent_v2_runs').fetchall())
    assert states[first] == 'failed'
    assert states[second] == 'succeeded'
