import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app import db
from app.trading_agent import pydantic_runtime, store
from app.trading_agent.contracts import MetricValue, ToolEnvelope
from app.trading_agent.harness import RuntimeDeps
from test_store import queued


ORIGINAL_POSITION_QUESTION = (
    "只用内部数据，汇总当前期权净买 Call 和净卖 Put 的手数，按合约月份列出，只要数量。"
)


def _model_plan():
    return {
        "schema_version": "1.0",
        "objective": "汇总当前期权净买 Call 和净卖 Put 的手数",
        "topic_action": "new_topic",
        "requirements": [{
            "id": "call",
            "question": "当前期权 Call 净买手数",
            "targets": [{
                "id": "call_target", "domain": "positions",
                "filters": {"asset_type": "option", "option_type": "call"},
                "metrics": ["gross_quantity", "net_quantity", "floating_pnl"],
                "group_by": ["contract_month"], "net_intent": "net_buy", "label": "Call",
            }],
            "source_intent": "internal",
            "time_requirement": "当前持仓（最新快照）",
            "time_window": {"start_date": "2024-01-01", "end_date": "2024-12-31", "origin": "default"},
        }, {
            "id": "put",
            "question": "当前期权 Put 净卖手数",
            "targets": [{
                "id": "put_target", "domain": "positions",
                "filters": {"asset_type": "option", "option_type": "put"},
                "metrics": ["gross_quantity", "net_quantity", "floating_pnl"],
                "group_by": ["contract_month"], "net_intent": "net_sell", "label": "Put",
            }],
            "source_intent": "internal",
            "time_requirement": "当前持仓（最新快照）",
            "time_window": {"start_date": "2024-01-01", "end_date": "2024-12-31", "origin": "default"},
        }, {
            "id": "distribution",
            "question": "比较 Call 与 Put 的月份分布",
            "targets": [{
                "id": "distribution_target", "domain": "positions", "filters": {"asset_type": "option"},
                "metrics": ["net_quantity"], "group_by": ["contract_month"],
                "net_intent": "net", "label": "月份比较",
            }],
            "source_intent": "internal", "depends_on": ["call", "put"],
            "time_requirement": "当前持仓（最新快照）",
            "analysis": {
                "operation": "compare", "metric": "net_quantity", "comparison_basis": "explicit",
                "conclusion_required": True,
            },
        }],
        "condition_origins": [], "restrictions": [], "presentation": "text",
        "prohibited_presentations": ["table"], "clarification": None,
    }


class ProductionFailureMCP:
    def __init__(self, *, data_as_of=None):
        self.calls = []
        self.query_args = []
        self.refs = []
        self.data_as_of = data_as_of

    async def list_tools(self, grant):
        return {"tools": [
            {"name": "describe_capabilities", "inputSchema": {"type": "object"}},
            {"name": "query_positions", "inputSchema": {"type": "object"}},
        ]}

    async def call_tool(self, name, args, grant):
        self.calls.append(name)
        principal = store.resolve_grant(grant)
        now = datetime(2026, 9, 15, 11, 0, tzinfo=timezone.utc)
        if name == "describe_capabilities":
            return ToolEnvelope(
                status="complete", captured_at=now, calculation_version="catalog-offline-v1",
                payload={"kind": "capabilities", "business_capabilities": {
                    "version": "capability-catalog-v1",
                    "tools": [{"id": "query_positions"}],
                    "conditional_sources": [], "research": {},
                }},
            )
        if name != "query_positions":
            raise AssertionError(f"unexpected tool: {name}")
        self.query_args.append(args)
        option_type = (args.get("filters") or {}).get("option_type")
        ref = uuid4()
        envelope = ToolEnvelope(
            status="complete",
            result_ref=ref,
            captured_at=now,
            data_as_of=self.data_as_of,
            calculation_version="positions-offline-v1",
            payload={
                "kind": "positions",
                "as_of": {"mode": args.get("as_of_mode", "latest"), "date": args.get("as_of_date")},
                "selection": {"filters": {
                    "asset_type": (args.get("asset_type") or "all"),
                    "option_type": option_type or "all",
                }},
                "required_metrics": list(args.get("required_metrics") or []),
                "semantic_groups": [{
                    "dimensions": {"contract_month": "2701", "option_type": option_type},
                    "metrics": {"net_quantity": {
                        "value": "3" if option_type == "call" else "2",
                        "unit": "手", "status": "complete", "covered_rows": 1, "eligible_rows": 1,
                    }},
                }],
            },
            metrics={
                "net_quantity": MetricValue(
                    value="3" if option_type == "call" else "2",
                    unit="手", status="complete", covered_rows=1, eligible_rows=1,
                ),
            },
        )
        self.refs.append(store.save_result(principal, envelope, [{
            "contract_month": "2701", "option_type": option_type, "net_quantity": "3",
        }], kind="positions"))
        return store.load_result(principal, self.refs[-1]).envelope


class ProductionFailureSDK:
    def __init__(self, mcp):
        self.calls = 0
        self.messages = []
        self.mcp = mcp

    def __call__(self, messages, info):
        self.calls += 1
        self.messages.append(messages)
        if self.calls == 1:
            args = _model_plan()
            return ModelResponse(parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=args)])
        if self.calls in {2, 3}:
            option_type = "call" if self.calls == 2 else "put"
            return ModelResponse(parts=[ToolCallPart(tool_name="query_positions", args={
                "as_of_mode": "latest", "as_of_date": None,
                "asset_type": "option", "contracts": [], "direction": "all", "classification": "all",
                "valuation_mode": "quantity_only", "required_metrics": ["net_quantity"],
                "filters": {"option_type": option_type}, "presentation": "text",
            })])
        refs = list(self.mcp.refs)
        return ModelResponse(parts=[ToolCallPart(tool_name=info.output_tools[0].name, args={
            "schema_version": "2.1",
            "blocks": [
                {"id": "s1", "kind": "fact", "text": (
                    f"2701 Call 净买手数为 {{{{fact:{refs[0]}#/payload/semantic_groups/0/metrics/net_quantity}}}}。"
                ), "refs": [f"{refs[0]}#/payload/semantic_groups/0/metrics/net_quantity"], "depends_on": []},
                {"id": "s2", "kind": "fact", "text": (
                    f"2701 Put 净卖手数为 {{{{fact:{refs[1]}#/payload/semantic_groups/0/metrics/net_quantity}}}}。"
                ), "refs": [f"{refs[1]}#/payload/semantic_groups/0/metrics/net_quantity"], "depends_on": []},
            ],
            "views": [],
        })])


class FallbackExecutionSDK:
    """Model script used after the strict planner has failed closed."""

    def __init__(self, mcp):
        self.calls = 0
        self.mcp = mcp

    def __call__(self, messages, info):
        self.calls += 1
        if self.calls in {1, 2}:
            option_type = "call" if self.calls == 1 else "put"
            return ModelResponse(parts=[ToolCallPart(tool_name="query_positions", args={
                "as_of_mode": "latest", "as_of_date": None,
                "asset_type": "option", "contracts": [], "direction": "all", "classification": "all",
                "valuation_mode": "quantity_only", "required_metrics": ["net_quantity"],
                "filters": {"option_type": option_type}, "presentation": "text",
            })])
        refs = list(self.mcp.refs)
        return ModelResponse(parts=[ToolCallPart(tool_name=info.output_tools[0].name, args={
            "schema_version": "2.1",
            "blocks": [
                {"id": "s1", "kind": "fact", "text": (
                    f"2701 Call 净买手数为 {{{{fact:{refs[0]}#/payload/semantic_groups/0/metrics/net_quantity}}}}。"
                ), "refs": [f"{refs[0]}#/payload/semantic_groups/0/metrics/net_quantity"], "depends_on": []},
                {"id": "s2", "kind": "fact", "text": (
                    f"2701 Put 净卖手数为 {{{{fact:{refs[1]}#/payload/semantic_groups/0/metrics/net_quantity}}}}。"
                ), "refs": [f"{refs[1]}#/payload/semantic_groups/0/metrics/net_quantity"], "depends_on": []},
            ],
            "views": [],
        })])


@pytest.mark.asyncio
async def test_production_failure_query_is_repaired_end_to_end_without_real_model_or_search(queued):
    _, _, queued_task_id = queued
    task_id = store.claim_next("production-offline-chain-worker")
    assert task_id == queued_task_id
    with db.connect() as conn:
        conn.execute(
            "UPDATE closing_review_messages SET content=? WHERE id=(SELECT user_message_id FROM closing_review_tasks WHERE id=?)",
            (ORIGINAL_POSITION_QUESTION, task_id),
        )
    mcp = ProductionFailureMCP()
    scripted = ProductionFailureSDK(mcp)
    result = await pydantic_runtime.run_task(task_id, RuntimeDeps(
        store=store,
        model=object(),
        mcp=mcp,
        worker_id="production-offline-chain-worker",
        sdk_model=FunctionModel(function=scripted),
        planning_enabled=True,
    ))

    assert result.delivery_status == "partial"
    assert result.validation_summary.checks["time"] == "failed"
    assert len(result.evidence) == 2
    assert "3 手" in result.plain_text
    assert "2 手" in result.plain_text
    assert "2701" in result.plain_text
    assert mcp.calls == ["describe_capabilities", "query_positions", "query_positions"]
    assert scripted.calls >= 4
    assert all(args["as_of_mode"] == "latest" and args["as_of_date"] is None for args in mcp.query_args)
    assert [args["filters"]["option_type"] for args in mcp.query_args] == ["call", "put"]

    with db.connect() as conn:
        task_row = conn.execute(
            "SELECT state FROM closing_review_tasks WHERE id=?", (task_id,),
        ).fetchone()
        row = conn.execute(
            "SELECT structured_payload FROM closing_review_messages WHERE task_id=? AND role='assistant' ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
    payload = json.loads(row["structured_payload"])
    plan = payload["agent_context"]["task_plan"]
    assert task_row["state"] == "partial"
    assert [item["id"] for item in plan["requirements"]] == ["call", "put"]
    assert all(item["time_window"] is None for item in plan["requirements"])
    assert all(
        metric not in {"floating_pnl", "gross_quantity", "gross_buy_quantity", "gross_sell_quantity"}
        for item in plan["requirements"]
        for target in item["targets"]
        for metric in target["metrics"]
    )


@pytest.mark.asyncio
async def test_internal_position_fallback_continues_after_strict_planner_failure(queued, monkeypatch):
    _, _, queued_task_id = queued
    task_id = store.claim_next("production-planner-fallback-worker")
    with db.connect() as conn:
        conn.execute(
            "UPDATE closing_review_messages SET content=? WHERE id=(SELECT user_message_id FROM closing_review_tasks WHERE id=?)",
            (ORIGINAL_POSITION_QUESTION, task_id),
        )
    mcp = ProductionFailureMCP(
        data_as_of=datetime(2026, 9, 15, 11, 0, tzinfo=timezone.utc),
    )
    scripted = FallbackExecutionSDK(mcp)
    original_run_agent = pydantic_runtime._run_agent
    planner_attempt = []

    async def fail_planner_once(agent, prompt, **kwargs):
        if not planner_attempt:
            planner_attempt.append(True)
            kwargs["budget"].reserve("model")
            raise ValueError("strict task plan output is invalid")
        return await original_run_agent(agent, prompt, **kwargs)

    monkeypatch.setattr(pydantic_runtime, "_run_agent", fail_planner_once)
    result = await pydantic_runtime.run_task(task_id, RuntimeDeps(
        store=store,
        model=object(),
        mcp=mcp,
        worker_id="production-planner-fallback-worker",
        sdk_model=FunctionModel(function=scripted),
        planning_enabled=True,
    ))

    assert result.delivery_status == "complete"
    assert mcp.calls == ["describe_capabilities", "query_positions", "query_positions"]
    assert scripted.calls >= 3
    assert [args["filters"]["option_type"] for args in mcp.query_args] == ["call", "put"]

    with db.connect() as conn:
        row = conn.execute(
            "SELECT structured_payload FROM closing_review_messages WHERE task_id=? AND role='assistant' ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
    payload = json.loads(row["structured_payload"])
    planning = payload["agent_context"]["planning"]
    assert planning["status"] == "fallback_approved"
    assert planning["fallback_from"]
    plan = payload["agent_context"]["task_plan"]
    assert [item["id"] for item in plan["requirements"]] == ["call", "put"]
