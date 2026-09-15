from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from app import db
from app.trading_agent import pydantic_runtime, store
from app.trading_agent.answer_contracts import ModelAnswer21
from app.trading_agent.contracts import MetricValue, ToolEnvelope
from app.trading_agent.harness import RuntimeDeps
from test_store import queued


class ExplodingLegacyModel:
    calls = 0

    def next_turn(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("Pydantic runtime must not call the legacy model protocol")


class TwoBusinessToolMCP:
    def __init__(self):
        self.calls = []
        self.last_ref = None

    async def list_tools(self, grant):
        return {"tools": [
            {"name": "describe_capabilities", "inputSchema": {"type": "object"}},
            {"name": "query_positions", "inputSchema": {"type": "object"}},
            {"name": "summarize_positions", "inputSchema": {"type": "object"}},
        ]}

    async def call_tool(self, name, args, grant):
        self.calls.append(name)
        principal = store.resolve_grant(grant)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        if name == "describe_capabilities":
            return ToolEnvelope(
                status="complete", captured_at=now, calculation_version="catalog-v1",
                payload={"kind": "capabilities", "business_capabilities": {
                    "version": "capability-catalog-v1",
                    "tools": [{"id": "query_positions"}, {"id": "summarize_positions"}],
                    "conditional_sources": [], "research": {},
                }},
            )
        if name == "query_positions":
            envelope = ToolEnvelope(
                status="complete", captured_at=now, calculation_version="positions-v1",
                payload={"kind": "positions", "selection": {"filters": {}}},
                metrics={"quantity": MetricValue(value="3", unit="手", status="complete", covered_rows=1, eligible_rows=1)},
            )
            self.last_ref = store.save_result(principal, envelope, [{"quantity": "3"}], kind="positions")
        elif name == "summarize_positions":
            envelope = ToolEnvelope(
                status="complete", captured_at=now, calculation_version="positions-summary-v1",
                payload={"kind": "positions", "selection": {"filters": {}}},
                metrics={"quantity": MetricValue(value="3", unit="手", status="complete", covered_rows=1, eligible_rows=1)},
            )
            self.last_ref = store.save_result(
                principal, envelope, [{"quantity": "3"}], kind="positions",
                parent_ref=self.last_ref, input_refs=[self.last_ref],
            )
        else:
            raise AssertionError(f"unexpected tool: {name}")
        return store.load_result(principal, self.last_ref).envelope


def _plan_args():
    return {
        "schema_version": "1.0",
        "objective": "读取当前持仓",
        "topic_action": "new_topic",
        "requirements": [{
            "id": "positions",
            "question": "读取当前持仓",
            "targets": [{
                "id": "positions_target", "domain": "positions", "filters": {},
                "metrics": ["quantity"], "group_by": [], "net_intent": "none", "label": "持仓",
            }],
            "source_intent": "internal", "depends_on": [], "needs_full_text": False,
            "time_requirement": "latest",
        }],
        "condition_origins": [], "restrictions": [], "presentation": "auto",
        "prohibited_presentations": [], "clarification": None,
    }


class ScriptedSDK:
    def __init__(self, mcp, *, repair_first=False):
        self.calls = 0
        self.mcp = mcp
        self.repair_first = repair_first
        self.repair_tool_names = None

    def __call__(self, messages, info):
        self.calls += 1
        if self.calls == 1:
            args = _plan_args()
        elif self.calls == 2:
            return ModelResponse(parts=[ToolCallPart(tool_name="query_positions", args={
                "as_of_mode": "latest", "as_of_date": None, "asset_type": "all",
                "contracts": [], "direction": "all", "classification": "all",
                "valuation_mode": "quantity_only", "required_metrics": ["quantity"],
                "filters": {}, "presentation": "auto",
            })])
        elif self.calls == 3:
            args = {
                "result_ref": str(self.mcp.last_ref), "group_by": [], "metrics": ["quantity"],
                "order_by": None, "descending": True,
            }
            return ModelResponse(parts=[ToolCallPart(tool_name="summarize_positions", args=args)])
        elif self.calls == 4 and self.repair_first:
            ref = str(self.mcp.last_ref)
            args = {
                "schema_version": "2.1",
                "blocks": [{"id": "s1", "kind": "fact", "text": "行。", "refs": [f"{ref}#/metrics/quantity"], "depends_on": []}],
                "views": [],
            }
        else:
            if self.repair_first and self.calls == 5:
                self.repair_tool_names = {
                    item.name for item in [*info.function_tools, *info.output_tools]
                }
            ref = str(self.mcp.last_ref)
            args = {
                "schema_version": "2.1",
                "blocks": [{
                    "id": "s1", "kind": "fact", "text": "当前持仓见已核验结果。",
                    "refs": [f"{ref}#/metrics/quantity"], "depends_on": [],
                }],
                "views": [],
            }
        return ModelResponse(parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=args)])


@pytest.mark.asyncio
async def test_pydantic_runtime_runs_sdk_plan_tool_loop_and_finishes_once(queued, monkeypatch):
    task_id = store.claim_next("pydantic-runtime-worker")
    mcp = TwoBusinessToolMCP()
    scripted = ScriptedSDK(mcp)
    sdk_model = FunctionModel(function=scripted)
    legacy = ExplodingLegacyModel()
    deps = RuntimeDeps(
        store=store, model=legacy, mcp=mcp, worker_id="pydantic-runtime-worker",
        sdk_model=sdk_model, planning_enabled=True,
    )
    finish_calls = []
    original_finish = store.finish

    def counting_finish(*args, **kwargs):
        finish_calls.append(args[0])
        return original_finish(*args, **kwargs)

    monkeypatch.setattr(store, "finish", counting_finish)
    result = await pydantic_runtime.run_task(task_id, deps)

    assert result.delivery_status == "complete"
    assert scripted.calls >= 4
    assert legacy.calls == 0
    assert mcp.calls == ["describe_capabilities", "query_positions", "summarize_positions"]
    assert finish_calls == [task_id]
    with db.connect() as conn:
        run = conn.execute("SELECT state FROM agent_v2_runs WHERE task_id=?", (task_id,)).fetchone()
        grant = conn.execute(
            "SELECT revoked_at FROM agent_v2_execution_grants WHERE task_id=? ORDER BY expires_at DESC LIMIT 1",
            (task_id,),
        ).fetchone()
    assert run["state"] == "succeeded"
    assert grant["revoked_at"] is not None


@pytest.mark.asyncio
async def test_pydantic_repair_does_not_expose_business_tools_again(queued):
    task_id = store.claim_next("pydantic-repair-worker")
    mcp = TwoBusinessToolMCP()
    scripted = ScriptedSDK(mcp, repair_first=True)
    deps = RuntimeDeps(
        store=store,
        model=ExplodingLegacyModel(),
        mcp=mcp,
        worker_id="pydantic-repair-worker",
        sdk_model=FunctionModel(function=scripted),
        planning_enabled=True,
    )

    result = await pydantic_runtime.run_task(task_id, deps)

    assert result.delivery_status == "complete"
    assert scripted.repair_tool_names is not None
    assert scripted.repair_tool_names.isdisjoint({"query_positions", "summarize_positions"})
    assert mcp.calls == ["describe_capabilities", "query_positions", "summarize_positions"]
