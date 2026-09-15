"""Regression cases for the four batch B review findings."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.trading_agent import coverage, dv_analysis
from app.trading_agent.contracts import ToolEnvelope
from app.trading_agent.dv_contracts import DatasetCompare
from app.trading_agent.planning_contracts import TaskPlan
from app.trading_agent.pydantic_tools import invoke_registered_tool
from test_pydantic_tools import _context
from test_dataset_analysis import _saved


def envelope(dataset="inventory_summary", **payload):
    return ToolEnvelope(
        status="complete", result_ref=uuid4(), captured_at=datetime.now(timezone.utc),
        calculation_version="synthetic-review",
        payload={"kind": "dataset_summary", "dataset": dataset, "measure": "value", **payload},
    )


def plan(*datasets):
    return TaskPlan.model_validate({
        "objective": "核对数据", "topic_action": "new_topic",
        "requirements": [{
            "id": "r", "question": "核对数据", "source_intent": "internal",
            "targets": [
                {"id": f"t{i}", "domain": "dataset", "label": name,
                 "filters": {"dataset": name}, "metrics": ["value"]}
                for i, name in enumerate(datasets)
            ],
        }],
    })


@pytest.mark.parametrize("multiple_targets", [False, True])
def test_delivery_requires_reference_for_each_matching_target(multiple_targets):
    p = plan("inventory_summary", "arrival_detail") if multiple_targets else plan("inventory_summary")
    inventory, arrival = envelope(), envelope("arrival_detail")
    report = coverage.assess_evidence(p, [inventory, arrival])
    assert report.complete
    if not multiple_targets:
        assert report.items[0].result_refs == [str(inventory.result_ref)]
    answer = SimpleNamespace(delivery_status="complete", evidence=[SimpleNamespace(result_ref=arrival.result_ref)], views=[])
    assert not coverage.assess_delivery(p, report, answer).complete
    answer.evidence.append(SimpleNamespace(result_ref=inventory.result_ref))
    assert coverage.assess_delivery(p, report, answer).complete


@pytest.mark.asyncio
@pytest.mark.parametrize("placement", ["port", "metadata", "grant"])
async def test_actual_tool_output_blocks_task_sensitive_values(monkeypatch, placement):
    result = envelope(port="INTERNAL-CUSTOMER-CANARY" if placement == "port" else "公开港口")
    if placement == "metadata":
        result.calculation_version = "INTERNAL-CUSTOMER-CANARY"
    if placement == "grant":
        result.payload["port"] = "task-grant"
    ctx = _context(result, allowed=("summarize_dataset",))
    monkeypatch.setattr("app.trading_agent.pydantic_tools._live_tool_allowed", lambda *_a, **_k: True)
    summary = await invoke_registered_tool("summarize_dataset", {
        "result_ref": str(uuid4()), "measure": "value", "operation": "sum",
    }, ctx=ctx)
    assert summary == {"status": "temporarily_unavailable", "error_code": "private_content"}
    assert ctx.envelopes == []
    assert ctx.budget.cancelled
    assert "INTERNAL-CUSTOMER-CANARY" not in str(ctx.store.events)


def comparison(monkeypatch, *, ranking_measure=None, top_k=None):
    rows = []
    for index in range(25):
        rows.extend([
            {"port": f"P{index:02}", "observation_date": "2026-09-01", "value": 100},
            {"port": f"P{index:02}", "observation_date": "2026-09-08", "value": 99-index},
        ])
    rows.append({"port": "Missing", "observation_date": "2026-09-08", "value": 1})
    saved = _saved(rows)
    monkeypatch.setattr(dv_analysis.store, "load_result", lambda *_a, **_k: saved)
    monkeypatch.setattr(dv_analysis, "authorize", lambda *_a, **_k: None)
    persisted = []
    def save(_principal, result, values, **_kwargs):
        persisted.extend(values)
        return result.model_copy(update={"result_ref": uuid4()})
    monkeypatch.setattr(dv_analysis, "_save_derived", save)
    result = dv_analysis.compare_dataset(object(), DatasetCompare(
        result_ref=uuid4(), method="previous_week", measure="value", group_by=["port"],
        ranking_measure=ranking_measure, top_k=top_k,
    ))
    return result, persisted, rows


def test_registered_comparison_ranks_full_snapshot_and_preserves_missingness(monkeypatch):
    old, old_rows, _ = comparison(monkeypatch)
    ranked, ranked_rows, source = comparison(monkeypatch, ranking_measure="delta", top_k=2)
    assert "ranking" not in old.payload
    assert len(old_rows) == 26
    assert [row["port"] for row in ranked_rows] == ["P24", "P23"]
    assert [row["delta"] for row in ranked_rows] == ["-25", "-24"]
    assert ranked.status == "partial"
    assert ranked.payload["ranking"] == {
        "measure": "delta", "descending": False, "top_k": 2, "total_rows": 26,
        "participating_rows": 25, "excluded_rows": 1, "returned_rows": 2,
    }
    assert ranked.payload["periods"] == {"current": "2026-09-08", "previous": "2026-09-01"}
    refs = {row["port"]: row["row_ref"] for row in old_rows}
    assert all(row["row_ref"] == refs[row["port"]] for row in ranked_rows)
    assert len(source) == 51
    assert not any("delta" in row for row in source)


@pytest.mark.asyncio
async def test_real_comparison_survives_typed_summary_projection(monkeypatch):
    result, rows, _ = comparison(monkeypatch, ranking_measure="delta", top_k=2)
    result.payload["preview"][0]["customer_name"] = "PRIVATE-NAME"
    ctx = _context(result, allowed=("compare_dataset",))
    monkeypatch.setattr("app.trading_agent.pydantic_tools._live_tool_allowed", lambda *_a, **_k: True)
    summary = await invoke_registered_tool("compare_dataset", {
        "result_ref": str(uuid4()), "method": "previous_week", "measure": "value",
        "ranking_measure": "delta", "top_k": 2,
    }, ctx=ctx)
    payload = summary["payload"]
    assert payload["periods"] == result.payload["periods"]
    assert payload["coverage"]["matched_rows"] == 25
    assert payload["value_fields"] == ["current_value", "previous_value", "delta", "delta_pct"]
    assert payload["ranking"]["excluded_rows"] == 1
    assert [row["delta"] for row in payload["preview"]] == ["-25", "-24"]
    assert "PRIVATE-NAME" not in str(summary)
    assert ctx.mcp.calls[0][1]["top_k"] == 2


@pytest.mark.asyncio
async def test_mcp_server_accepts_ranking_arguments(monkeypatch):
    from app.trading_agent import mcp_server
    captured = []
    result = envelope()
    def response(name, args):
        captured.append((name, args))
        return {"status": result.status, "data": result.model_dump(mode="json")}
    monkeypatch.setattr(mcp_server, "_response", response)
    server = mcp_server.build_mcp_server()
    await server.call_tool("compare_dataset", {
        "result_ref": str(uuid4()), "method": "previous_week", "measure": "value",
        "ranking_measure": "delta", "top_k": 2,
    })
    assert captured[0][1]["ranking_measure"] == "delta"
    assert captured[0][1]["top_k"] == 2


@pytest.mark.asyncio
async def test_sensitive_error_code_is_not_returned_or_audited(monkeypatch):
    from app.trading_agent.mcp_client import MCPToolError
    ctx = _context(envelope())
    async def fail(*_args):
        raise MCPToolError("INTERNAL-CUSTOMER-CANARY")
    ctx.mcp.call_tool = fail
    monkeypatch.setattr("app.trading_agent.pydantic_tools._live_tool_allowed", lambda *_a, **_k: True)
    summary = await invoke_registered_tool("query_positions", {}, ctx=ctx)
    assert "INTERNAL-CUSTOMER-CANARY" not in str(summary)
    assert "INTERNAL-CUSTOMER-CANARY" not in str(ctx.store.events)
    assert ctx.budget.cancelled


@pytest.mark.asyncio
async def test_query_summary_preserves_observation_range_and_truncation(monkeypatch):
    from app.trading_agent.dv_queries import _envelope
    from app.trading_agent.dv_contracts import DatasetQuery
    result = _envelope("complete", "port_inventory", DatasetQuery(dataset="port_inventory"), [
        {"port": f"P{i}", "observation_date": "2026-09-08", "value": i} for i in range(25)
    ])
    ctx = _context(result, allowed=("query_dataset",))
    monkeypatch.setattr("app.trading_agent.pydantic_tools._live_tool_allowed", lambda *_a, **_k: True)
    summary = await invoke_registered_tool("query_dataset", {"dataset": "port_inventory"}, ctx=ctx)
    assert summary["payload"]["coverage"]["first_observation"] == "2026-09-08"
    assert summary["payload"]["coverage"]["last_observation"] == "2026-09-08"
    assert summary["payload"]["preview_truncated"] is True
    assert len(summary["payload"]["preview"]) == 20
    assert summary["payload"]["row_count"] == 25


def test_rank_without_top_k_keeps_all_comparable_rows(monkeypatch):
    result, rows, _ = comparison(monkeypatch, ranking_measure="delta")
    assert len(rows) == 25
    assert rows[0]["port"] == "P24"
    assert rows[-1]["port"] == "P00"
    assert result.payload["preview_truncated"]
    assert result.payload["ranking"]["returned_rows"] == 25
