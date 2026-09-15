import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai import Agent

from app.trading_agent import pydantic_runtime, pydantic_tools, quality
from app.trading_agent.contracts import ToolEnvelope
from app.trading_agent.harness import RuntimeLimits
from app.trading_agent.runtime_budget import RuntimeBudget


class EnvelopeMCP:
    def __init__(self, envelope):
        self.envelope = envelope
        self.calls = []

    async def call_tool(self, name, arguments, grant):
        self.calls.append((name, arguments, grant))
        return self.envelope


class EventStore:
    def __init__(self):
        self.events = []

    def append_event(self, *args, **kwargs):
        self.events.append((args, kwargs))
        return True


def _tool_context(envelope, *, max_tools=2):
    store = EventStore()
    mcp = EnvelopeMCP(envelope)
    limits = RuntimeLimits(max_tools=max_tools, max_search=max_tools, deadline_seconds=10)
    context = pydantic_tools.AgentRunContext(
        task_id=1,
        principal=SimpleNamespace(user_id=7),
        plan=SimpleNamespace(),
        grant="task-grant",
        mcp=mcp,
        store=store,
        budget=RuntimeBudget(limits, clock=lambda: 1.0, started_at=1.0),
        sensitive_values=("INTERNAL-CUSTOMER-CANARY",),
        allowed_tool_names=frozenset({"query_positions"}),
    )
    return context, mcp, store


@pytest.mark.asyncio
async def test_transient_envelope_is_sanitized_before_retry_or_audit(monkeypatch):
    envelope = ToolEnvelope(
        status="temporarily_unavailable",
        captured_at=datetime.now(timezone.utc),
        calculation_version="test",
        payload={"kind": "service_error", "code": "INTERNAL-CUSTOMER-CANARY"},
    )
    context, mcp, store = _tool_context(envelope)
    monkeypatch.setattr(pydantic_tools, "_live_tool_allowed", lambda *_args, **_kwargs: True)

    summary = await pydantic_tools.invoke_registered_tool(
        "query_positions",
        {"asset_type": "future", "valuation_mode": "quantity_only"},
        ctx=context,
    )

    assert "INTERNAL-CUSTOMER-CANARY" not in repr(summary)
    assert "INTERNAL-CUSTOMER-CANARY" not in repr(store.events)
    assert context.budget.cancelled
    assert context.envelopes == []
    assert len(mcp.calls) == 1


@pytest.mark.asyncio
async def test_public_provider_unavailable_is_not_retried_or_task_cancelled(monkeypatch):
    envelope = ToolEnvelope(
        status="temporarily_unavailable",
        captured_at=datetime.now(timezone.utc),
        calculation_version="test",
        payload={"kind": "public_research_blocked", "code": "public_not_configured"},
    )
    context, mcp, store = _tool_context(envelope)
    context.allowed_tool_names = frozenset({"search_public"})
    monkeypatch.setattr(pydantic_tools, "_live_tool_allowed", lambda *_args, **_kwargs: True)

    summary = await pydantic_tools.invoke_registered_tool(
        "search_public", {"public_query": "公开天气"}, ctx=context,
    )

    assert summary == {"status": "temporarily_unavailable", "error_code": "temporarily_unavailable"}
    assert len(mcp.calls) == 1
    assert not context.budget.cancelled
    assert store.events[-1][1]["error_code"] == "temporarily_unavailable"


@pytest.mark.asyncio
async def test_unknown_transient_exception_code_is_fixed_and_not_retried(monkeypatch):
    from app.trading_agent.mcp_client import MCPToolError

    context, mcp, store = _tool_context(ToolEnvelope(
        status="complete",
        captured_at=datetime.now(timezone.utc),
        calculation_version="test",
        payload={"kind": "positions"},
    ))
    attempts = []

    async def fail(*_args):
        attempts.append(True)
        raise MCPToolError("UPSTREAM-UNCONFIRMED-timeout")

    mcp.call_tool = fail
    monkeypatch.setattr(pydantic_tools, "_live_tool_allowed", lambda *_args, **_kwargs: True)

    summary = await pydantic_tools.invoke_registered_tool(
        "query_positions", {"asset_type": "future", "valuation_mode": "quantity_only"}, ctx=context,
    )

    assert summary == {"status": "temporarily_unavailable", "error_code": "temporarily_unavailable"}
    assert len(attempts) == 1
    assert store.events[-1][1]["error_code"] == "temporarily_unavailable"


def test_failure_fallback_validation_is_not_run_even_with_body():
    answer = pydantic_runtime._failure_answer(
        SimpleNamespace(user_id=7),
        SimpleNamespace(store=SimpleNamespace()),
        [],
        "model_timeout",
        "auto",
    )

    assert answer.validation_summary is None
    assert quality.migration_status({
        "state": "failed",
        "structured_payload": answer.model_dump(mode="json"),
    })["validation"] == "not_run"


@pytest.mark.asyncio
async def test_sdk_stage_timeout_is_total_budget_not_single_request_limit():
    class SlowCompleteAgent:
        async def run(self, *args, **kwargs):
            await asyncio.sleep(0.06)
            return SimpleNamespace(usage=SimpleNamespace(requests=1))

    limits = RuntimeLimits(
        max_models=2,
        deadline_seconds=1.0,
        model_timeout_seconds=0.05,
    )
    budget = RuntimeBudget(limits)

    result = await pydantic_runtime._run_agent(
        SlowCompleteAgent(),
        "synthetic stage",
        deps=SimpleNamespace(limits=limits),
        budget=budget,
    )

    assert result.usage.requests == 1
    assert budget.model_calls == 1


@pytest.mark.asyncio
async def test_sdk_usage_is_charged_when_stage_raises_after_multiple_requests():
    class FailingAgent:
        async def run(self, *args, **kwargs):
            usage = kwargs.get("usage")
            if usage is not None:
                usage.requests = 2
            raise RuntimeError("synthetic stage failure")

    limits = RuntimeLimits(max_models=3, deadline_seconds=1.0)
    budget = RuntimeBudget(limits)

    with pytest.raises(RuntimeError, match="synthetic stage failure"):
        await pydantic_runtime._run_agent(
            FailingAgent(),
            "synthetic stage",
            deps=SimpleNamespace(limits=limits),
            budget=budget,
        )

    assert budget.model_calls == 2


def test_sdk_tools_are_constructed_as_sequential():
    envelope = ToolEnvelope(
        status="complete",
        captured_at=datetime.now(timezone.utc),
        calculation_version="test",
        payload={"kind": "positions"},
    )
    context, _mcp, _store = _tool_context(envelope)

    tool = pydantic_runtime._build_sdk_tool("query_positions", context)

    assert tool.sequential is True


@pytest.mark.asyncio
async def test_sdk_same_turn_tool_calls_are_actually_serial(monkeypatch):
    class TrackingMCP(EnvelopeMCP):
        def __init__(self, envelope):
            super().__init__(envelope)
            self.active = 0
            self.peak = 0

        async def call_tool(self, name, arguments, grant):
            self.calls.append((name, arguments, grant))
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await asyncio.sleep(0.02)
                return self.envelope
            finally:
                self.active -= 1

    envelope = ToolEnvelope(
        status="complete",
        captured_at=datetime.now(timezone.utc),
        calculation_version="test",
        payload={"kind": "positions"},
    )
    context, _mcp, _store = _tool_context(envelope, max_tools=3)
    mcp = TrackingMCP(envelope)
    context.mcp = mcp
    monkeypatch.setattr(pydantic_tools, "_live_tool_allowed", lambda *_args, **_kwargs: True)
    calls = 0
    args = {
        "as_of_mode": "latest", "as_of_date": None, "asset_type": "all",
        "contracts": [], "direction": "all", "classification": "all",
        "valuation_mode": "quantity_only", "required_metrics": ["quantity"],
        "filters": {}, "presentation": "auto",
    }

    def respond(_messages, _info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[
                ToolCallPart(tool_name="query_positions", args=args),
                ToolCallPart(tool_name="query_positions", args=args),
            ])
        return ModelResponse(parts=[TextPart(content="完成")])

    agent = Agent(
        FunctionModel(function=respond), output_type=str,
        deps_type=pydantic_tools.AgentRunContext,
        tools=[pydantic_runtime._build_sdk_tool("query_positions", context)],
        retries=0,
    )
    limits = RuntimeLimits(max_models=4, max_tools=3, deadline_seconds=1.0)
    budget = RuntimeBudget(limits)
    context.budget = budget

    result = await pydantic_runtime._run_agent(
        agent, "two calls", deps=SimpleNamespace(limits=limits), budget=budget,
        agent_deps=context,
    )

    assert result.output == "完成"
    assert mcp.peak == 1
    assert len(mcp.calls) == 2


@pytest.mark.asyncio
async def test_sdk_permission_failure_stops_the_remaining_same_turn_call(monkeypatch):
    class DeniedMCP(EnvelopeMCP):
        async def call_tool(self, name, arguments, grant):
            self.calls.append((name, arguments, grant))
            raise PermissionError("permission denied")

    envelope = ToolEnvelope(
        status="complete",
        captured_at=datetime.now(timezone.utc),
        calculation_version="test",
        payload={"kind": "positions"},
    )
    context, _mcp, _store = _tool_context(envelope, max_tools=3)
    mcp = DeniedMCP(envelope)
    context.mcp = mcp
    monkeypatch.setattr(pydantic_tools, "_live_tool_allowed", lambda *_args, **_kwargs: True)
    calls = 0
    args = {
        "as_of_mode": "latest", "as_of_date": None, "asset_type": "all",
        "contracts": [], "direction": "all", "classification": "all",
        "valuation_mode": "quantity_only", "required_metrics": ["quantity"],
        "filters": {}, "presentation": "auto",
    }

    def respond(_messages, _info):
        nonlocal calls
        calls += 1
        return ModelResponse(parts=[
            ToolCallPart(tool_name="query_positions", args=args),
            ToolCallPart(tool_name="query_positions", args=args),
        ])

    agent = Agent(
        FunctionModel(function=respond), output_type=str,
        deps_type=pydantic_tools.AgentRunContext,
        tools=[pydantic_runtime._build_sdk_tool("query_positions", context)],
        retries=0,
    )
    limits = RuntimeLimits(max_models=4, max_tools=3, deadline_seconds=1.0)
    budget = RuntimeBudget(limits)
    context.budget = budget

    with pytest.raises((pydantic_tools.ToolInvocationDenied, asyncio.CancelledError)):
        await pydantic_runtime._run_agent(
            agent, "permission failure", deps=SimpleNamespace(limits=limits), budget=budget,
            agent_deps=context,
        )

    assert len(mcp.calls) == 1
    assert calls == 1
    assert budget.cancelled
