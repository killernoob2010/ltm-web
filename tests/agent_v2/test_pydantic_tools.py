from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.trading_agent.contracts import ToolEnvelope
from app.trading_agent.pydantic_tools import (
    AgentRunContext,
    ToolInvocationDenied,
    invoke_registered_tool,
    model_tool_schemas,
)
from app.trading_agent.runtime_budget import BudgetExceeded, RuntimeBudget
from app.trading_agent.harness import RuntimeLimits
from app.trading_agent.planning_contracts import TaskPlan
from app.trading_agent.research_policy import RequestPlan, active_plan
from app.trading_agent import store
from test_store import queued


class FakeMCP:
    def __init__(self, envelope):
        self.envelope = envelope
        self.calls = []

    async def call_tool(self, name, arguments, grant):
        self.calls.append((name, arguments, grant))
        return self.envelope


class TransientMCP(FakeMCP):
    def __init__(self, envelope, *, failures=1):
        super().__init__(envelope)
        self.failures = failures

    async def call_tool(self, name, arguments, grant):
        self.calls.append((name, arguments, grant))
        if self.failures:
            self.failures -= 1
            from app.trading_agent.mcp_client import MCPToolError
            raise MCPToolError("upstream_timeout")
        return self.envelope


class FakeStore:
    def __init__(self):
        self.events = []

    def append_event(self, *args, **kwargs):
        self.events.append((args, kwargs))


def _context(envelope, *, allowed=("query_positions",), limits=None):
    return AgentRunContext(
        task_id=1,
        principal=SimpleNamespace(user_id=7),
        plan=SimpleNamespace(),
        grant="task-grant",
        sensitive_values=("INTERNAL-CUSTOMER-CANARY",),
        mcp=FakeMCP(envelope),
        store=FakeStore(),
        budget=RuntimeBudget(limits or RuntimeLimits(), clock=lambda: 10.0, started_at=10.0),
        allowed_tool_names=frozenset(allowed),
    )


@pytest.mark.asyncio
async def test_tool_arguments_are_revalidated_and_identity_is_not_forwarded(monkeypatch):
    envelope = ToolEnvelope(
        status="complete", result_ref=uuid4(), captured_at=datetime.now(timezone.utc),
        calculation_version="test", payload={"kind": "positions"},
    )
    ctx = _context(envelope)
    monkeypatch.setattr("app.trading_agent.pydantic_tools._live_tool_allowed", lambda *_a, **_k: True)

    with pytest.raises(ToolInvocationDenied):
        await invoke_registered_tool("query_positions", {"user_id": 7}, ctx=ctx)
    assert ctx.budget.counters["tool"] == 1
    assert ctx.mcp.calls == []


@pytest.mark.asyncio
async def test_tool_returns_minimal_summary_and_keeps_full_envelope_internal(monkeypatch):
    envelope = ToolEnvelope(
        status="complete", result_ref=uuid4(), captured_at=datetime.now(timezone.utc),
        calculation_version="test", payload={
            "kind": "positions", "canonical_value": "2", "unit": "手",
            "customer_name": "Alice", "source_sql": "select secret",
        },
    )
    ctx = _context(envelope)
    monkeypatch.setattr("app.trading_agent.pydantic_tools._live_tool_allowed", lambda *_a, **_k: True)

    summary = await invoke_registered_tool(
        "query_positions", {"asset_type": "future", "valuation_mode": "quantity_only"}, ctx=ctx
    )

    assert summary["result_ref"] == str(envelope.result_ref)
    assert summary["payload"] == {"kind": "positions", "canonical_value": "2", "unit": "手"}
    assert len(ctx.envelopes) == 1
    assert ctx.envelopes[0] is envelope
    assert ctx.budget.counters["tool"] == 1


@pytest.mark.asyncio
async def test_audit_write_failure_does_not_expose_tool_result(monkeypatch):
    envelope = ToolEnvelope(
        status="complete", result_ref=uuid4(), captured_at=datetime.now(timezone.utc),
        calculation_version="test", payload={"kind": "positions", "canonical_value": "2"},
    )
    ctx = _context(envelope)

    def fail_audit(*_args, **_kwargs):
        raise RuntimeError("synthetic_audit_failure")

    ctx.store.append_event = fail_audit
    monkeypatch.setattr("app.trading_agent.pydantic_tools._live_tool_allowed", lambda *_a, **_k: True)

    summary = await invoke_registered_tool(
        "query_positions", {"asset_type": "future", "valuation_mode": "quantity_only"}, ctx=ctx
    )

    assert summary == {"status": "temporarily_unavailable", "error_code": "audit_write_failed"}
    assert ctx.envelopes == []
    with pytest.raises(BudgetExceeded) as error:
        ctx.budget.reserve("tool")
    assert error.value.code == "cancelled"


@pytest.mark.asyncio
async def test_search_consumes_tool_and_search_budget_together(monkeypatch):
    envelope = ToolEnvelope(
        status="complete", result_ref=uuid4(), captured_at=datetime.now(timezone.utc),
        calculation_version="test", payload={"kind": "public_search"},
    )
    ctx = _context(
        envelope, allowed=("search_public",),
        limits=RuntimeLimits(max_tools=1, max_search=1),
    )
    monkeypatch.setattr("app.trading_agent.pydantic_tools._live_tool_allowed", lambda *_a, **_k: True)

    await invoke_registered_tool("search_public", {"public_query": "公开天气"}, ctx=ctx)

    assert ctx.budget.counters == {"model": 0, "tool": 1, "search": 1}


@pytest.mark.asyncio
async def test_transient_tool_failure_has_one_budgeted_retry(monkeypatch):
    envelope = ToolEnvelope(
        status="complete", result_ref=uuid4(), captured_at=datetime.now(timezone.utc),
        calculation_version="test", payload={"kind": "positions", "canonical_value": "2"},
    )
    ctx = _context(envelope, limits=RuntimeLimits(max_tools=2))
    ctx.mcp = TransientMCP(envelope, failures=1)
    monkeypatch.setattr("app.trading_agent.pydantic_tools._live_tool_allowed", lambda *_a, **_k: True)

    summary = await invoke_registered_tool(
        "query_positions", {"asset_type": "future", "valuation_mode": "quantity_only"}, ctx=ctx
    )

    assert summary["status"] == "complete"
    assert len(ctx.mcp.calls) == 2
    assert ctx.budget.counters["tool"] == 2
    assert len(ctx.envelopes) == 1


@pytest.mark.asyncio
async def test_transient_tool_failure_does_not_retry_more_than_once(monkeypatch):
    envelope = ToolEnvelope(
        status="complete", result_ref=uuid4(), captured_at=datetime.now(timezone.utc),
        calculation_version="test", payload={"kind": "positions"},
    )
    ctx = _context(envelope, limits=RuntimeLimits(max_tools=4))
    ctx.mcp = TransientMCP(envelope, failures=3)
    monkeypatch.setattr("app.trading_agent.pydantic_tools._live_tool_allowed", lambda *_a, **_k: True)

    summary = await invoke_registered_tool(
        "query_positions", {"asset_type": "future", "valuation_mode": "quantity_only"}, ctx=ctx
    )

    assert summary == {"status": "temporarily_unavailable", "error_code": "upstream_timeout"}
    assert len(ctx.mcp.calls) == 2
    assert ctx.budget.counters["tool"] == 2


@pytest.mark.asyncio
async def test_permission_denial_cancels_task_context(monkeypatch):
    envelope = ToolEnvelope(
        status="complete", result_ref=uuid4(), captured_at=datetime.now(timezone.utc),
        calculation_version="test", payload={"kind": "positions"},
    )
    ctx = _context(envelope, allowed=())
    monkeypatch.setattr("app.trading_agent.pydantic_tools._live_tool_allowed", lambda *_a, **_k: False)

    with pytest.raises(ToolInvocationDenied) as error:
        await invoke_registered_tool("query_positions", {}, ctx=ctx)

    assert error.value.code == "tool_not_authorized"
    assert ctx.budget.cancelled


def test_model_schemas_are_per_tool_and_never_expose_generic_executor(monkeypatch):
    ctx = _context(
        ToolEnvelope(
            status="complete", result_ref=uuid4(), captured_at=datetime.now(timezone.utc),
            calculation_version="test", payload={},
        ),
        allowed=("query_positions",),
    )
    monkeypatch.setattr("app.trading_agent.pydantic_tools._live_tool_allowed", lambda *_a, **_k: True)
    schemas = model_tool_schemas(ctx)
    assert [item["name"] for item in schemas] == ["query_positions"]
    assert all("grant" not in str(item) and "user_id" not in str(item) for item in schemas)


def test_public_policy_is_not_active_until_both_plan_audits_are_durable(queued):
    task = store.claim_next("plan-audit")
    principal = store.principal_for_task(task)
    plan = TaskPlan.model_validate({
        "objective": "查询公开天气",
        "topic_action": "new_topic",
        "requirements": [{
            "id": "weather", "question": "查询公开天气", "source_intent": "public",
            "needs_full_text": True,
            "targets": [{"id": "weather_target", "domain": "public", "label": "天气"}],
        }],
    })
    policy = RequestPlan(
        mode="research_allowed", reason="external_current_fact", domains=["public"]
    )
    store.append_event(
        principal, "research_policy", tool_name="public",
        status="research_allowed", error_code="external_current_fact",
    )
    assert active_plan(principal) is None
    store.append_event(
        principal, "task_plan", status="complete", error_code="external_current_fact"
    )
    assert active_plan(principal) is None

    assert store.record_plan_authorization(principal, plan, policy) is True
    current = active_plan(principal)
    assert current is not None
    assert current.mode == "research_allowed"

    store.append_event(
        principal, "research_policy", tool_name="public",
        status="research_allowed", error_code="external_current_fact",
    )
    assert active_plan(principal) is None


def test_plan_authorization_rolls_back_if_second_audit_write_fails(queued, monkeypatch):
    task = store.claim_next("plan-audit-rollback")
    principal = store.principal_for_task(task)
    plan = TaskPlan.model_validate({
        "objective": "查询公开天气",
        "topic_action": "new_topic",
        "requirements": [{
            "id": "weather", "question": "查询公开天气", "source_intent": "public",
            "targets": [{"id": "weather_target", "domain": "public", "label": "天气"}],
        }],
    })
    policy = RequestPlan(
        mode="research_allowed", reason="external_current_fact", domains=["public"]
    )
    original = store._append_event_row
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic_policy_audit_failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "_append_event_row", fail_second)
    with pytest.raises(RuntimeError, match="synthetic_policy_audit_failure"):
        store.record_plan_authorization(principal, plan, policy)

    with store.db.connect() as conn:
        rows = conn.execute(
            "SELECT kind FROM agent_v2_events WHERE task_id=? ORDER BY seq", (task,)
        ).fetchall()
    assert rows == []
    assert active_plan(principal) is None
