"""Exercise model-visible data through the real SDK tool boundary, offline."""
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4
from types import SimpleNamespace

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from app.trading_agent import facts, pydantic_runtime, pydantic_tools
from app.trading_agent.contracts import ToolEnvelope
from test_pydantic_tools import _context
from test_store import queued


def example(kind):
    source_ref = f"{uuid4()}#/sources/0"
    if kind == "research":
        return "search_public", {"public_query": "Port Hedland weather"}, {
            "kind": kind, "provider": "tavily", "source_count": 1,
            "search_status": "results", "provider_status": "available",
            "sources": [{"source_ref": source_ref, "title": "Synthetic weather",
                         "url": "https://example.com/weather", "description": "Weather summary",
                         "fetch_status": "snippet_only", "published_label": "2026-09-15"}],
        }
    if kind == "public_read":
        return "read_public", {"source_ref": source_ref}, {
            "kind": kind, "source_ref": source_ref, "title": "Synthetic weather",
            "url": "https://example.com/weather", "text": "Synthetic weather body.",
            "fetch_status": "full_text", "untrusted_content": True,
            "truncated": False, "published_at": None, "published_label": "2026-09-15",
        }
    rows = [{"contract": "i2701-C-800", "option_type": "call", "direction": "sell",
             "asset_type": "option", "quantity": 2, "floating_pnl": None,
             "valuation_status": "unavailable", "row_ref": str(uuid4()),
             "account": "PRIVATE-ACCOUNT", "account_id": 123}]
    groups, count, truncated = facts._position_groups(rows)
    return "query_positions", {"asset_type": "option", "valuation_mode": "quantity_only"}, {
        "kind": "positions", "count": 1, "preview": [facts._project(row) for row in rows],
        "preview_count": 1, "preview_truncated": False,
        "groups": groups, "group_count": count, "groups_truncated": truncated,
        "semantic_groups": groups, "semantic_group_count": count,
        "semantic_groups_truncated": truncated, "valuation_basis": "not_requested",
    }


def assert_business_payload(kind, actual, expected):
    if kind == "research":
        assert actual["sources"] == expected["sources"]
        assert actual["source_count"] == 1
        assert actual["search_status"] == "results"
    elif kind == "public_read":
        for field in ("source_ref", "title", "url", "text", "fetch_status",
                      "untrusted_content", "truncated", "published_at", "published_label"):
            assert actual[field] == expected[field]
    else:
        row = actual["preview"][0]
        assert row["contract"] == "i2701-C-800"
        assert row["option_type"] == "call"
        assert row["quantity"] == 2
        assert row["floating_pnl"] is None
        assert row["row_ref"] == expected["preview"][0]["row_ref"]
        assert actual["count"] == 1
        assert actual["preview_truncated"] is False
        assert actual["valuation_basis"] == "not_requested"
        for key in ("groups", "semantic_groups"):
            group = actual[key][0]
            assert group["dimensions"]["contract"] == "i2701-C-800"
            assert group["metrics"]["quantity"]["value"] == "2"
            assert group["metrics"]["floating_pnl"]["status"] == "unavailable"
            assert group["metrics"]["floating_pnl"]["covered_rows"] == 0
            assert group["row_refs"] == expected[key][0]["row_refs"]
        assert "PRIVATE-ACCOUNT" not in repr(actual)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["research", "public_read", "positions"])
async def test_sdk_model_receives_required_tool_data(monkeypatch, kind):
    name, arguments, payload = example(kind)
    original = deepcopy(payload)
    envelope = ToolEnvelope(status="complete", result_ref=uuid4(),
                            captured_at=datetime.now(timezone.utc),
                            calculation_version="synthetic", payload=payload)
    ctx = _context(envelope, allowed=(name,))
    monkeypatch.setattr(pydantic_tools, "_live_tool_allowed", lambda *_a, **_k: True)
    observed = []

    async def model(messages, info):
        returns = [part for message in messages for part in message.parts
                   if isinstance(part, ToolReturnPart)]
        if not returns:
            return ModelResponse(parts=[ToolCallPart(name, arguments)])
        received = returns[-1].content
        assert_business_payload(kind, received["payload"], original)
        assert received["result_ref"] == str(envelope.result_ref)
        observed.append(received)
        return ModelResponse(parts=[TextPart("checked")])

    agent = Agent(FunctionModel(model), tools=[pydantic_runtime._build_sdk_tool(name, ctx)])
    result = await agent.run("Inspect synthetic tool evidence", deps=ctx)
    assert result.output == "checked"
    assert len(observed) == 1
    assert envelope.payload == original
    assert len(ctx.mcp.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["research", "public_read", "positions"])
@pytest.mark.parametrize("sensitive", [False, True])
async def test_projection_preserves_privacy_boundary(monkeypatch, kind, sensitive):
    name, arguments, payload = example(kind)
    target = (payload["sources"][0] if kind == "research" else
              payload["preview"][0] if kind == "positions" else payload)
    target["password"] = "SYNTHETIC-PASSWORD"
    target["customer_name"] = "SYNTHETIC-CUSTOMER"
    target["raw_payload"] = {"text": "DO-NOT-FORWARD"}
    if sensitive:
        target["title" if kind != "positions" else "product"] = "INTERNAL-CUSTOMER-CANARY"
    envelope = ToolEnvelope(status="complete", result_ref=uuid4(),
                            captured_at=datetime.now(timezone.utc),
                            calculation_version="synthetic", payload=payload)
    ctx = _context(envelope, allowed=(name,))
    monkeypatch.setattr(pydantic_tools, "_live_tool_allowed", lambda *_a, **_k: True)
    result = await pydantic_tools.invoke_registered_tool(name, arguments, ctx=ctx)
    if sensitive:
        assert result["error_code"] == "private_content"
        assert ctx.budget.cancelled
        assert not ctx.envelopes
        assert "INTERNAL-CUSTOMER-CANARY" not in repr(ctx.store.events)
    else:
        assert result["status"] == "complete"
        for value in ("SYNTHETIC-PASSWORD", "SYNTHETIC-CUSTOMER", "DO-NOT-FORWARD", "PRIVATE-ACCOUNT"):
            assert value not in repr(result)


@pytest.mark.asyncio
async def test_real_search_and_reader_feed_sdk_source_chain(queued, monkeypatch):
    from app.trading_agent import research, store
    from urllib.parse import urlparse

    principal = store.principal_for_task(store.claim_next("projection-test"))
    monkeypatch.setenv("TAVILY_API_KEY", "synthetic-search-key")
    monkeypatch.setattr(research, "_validate_url", urlparse)
    monkeypatch.setattr(research, "safe_read_public", lambda url, **_kw: SimpleNamespace(
        status_code=200, content_type="text/html", url=url,
        body_bytes=b"<p>Synthetic port weather body.</p>", truncated=False))

    class Session:
        def post(self, *args, **kwargs):
            return SimpleNamespace(status_code=200, json=lambda: {"results": [{
                "title": "Synthetic weather", "url": "https://example.com/weather",
                "content": "Synthetic snippet", "published_date": "2026-09-15",
            }]})

    class MCP:
        async def call_tool(self, name, arguments, grant):
            if name == "search_public":
                return research.search_public(principal, arguments["public_query"], session=Session())
            return research.read_public(principal, arguments["source_ref"])

    name, _, payload = example("research")
    ctx = _context(ToolEnvelope(status="complete", captured_at=datetime.now(timezone.utc),
                               calculation_version="synthetic", payload=payload),
                   allowed=("search_public", "read_public"))
    ctx.mcp = MCP()
    monkeypatch.setattr(pydantic_tools, "_live_tool_allowed", lambda *_a, **_k: True)
    calls = []

    async def model(messages, info):
        returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if not returns:
            return ModelResponse(parts=[ToolCallPart("search_public", {"public_query": "Port Hedland weather"})])
        data = returns[-1].content["payload"]
        if len(returns) == 1:
            source = data["sources"][0]
            assert source["fetch_status"] == "snippet_only"
            calls.append(source["source_ref"])
            return ModelResponse(parts=[ToolCallPart("read_public", {"source_ref": source["source_ref"]})])
        assert data["text"] == "Synthetic port weather body."
        assert data["source_ref"] == calls[0]
        assert data["untrusted_content"] is True
        assert data["fetch_status"] == "full_text"
        return ModelResponse(parts=[TextPart("verified offline source chain")])

    agent = Agent(FunctionModel(model), tools=[pydantic_runtime._build_sdk_tool(tool, ctx)
                                             for tool in ("search_public", "read_public")])
    assert (await agent.run("Synthetic research", deps=ctx)).output == "verified offline source chain"
    assert len(ctx.envelopes) == 2


def test_position_projection_preserves_empty_and_truncated_states():
    for count, truncated in [(0, False), (21, True)]:
        envelope = ToolEnvelope(status="complete", captured_at=datetime.now(timezone.utc),
                               calculation_version="synthetic", payload={
            "kind": "positions", "count": count, "preview": [], "preview_count": 0,
            "preview_truncated": truncated, "groups": [], "group_count": 0,
            "groups_truncated": False, "group_by": ["contract", "account"],
        })
        result = pydantic_tools._model_summary(envelope, sensitive_values=())["payload"]
        assert result["count"] == count
        assert result["preview"] == []
        assert result["preview_truncated"] is truncated
        assert result["groups"] == []
        assert result["group_by"] == ["contract"]
