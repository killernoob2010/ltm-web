import json
from datetime import datetime, timezone

import pytest

from app.trading_agent import answer, harness, store
from app.trading_agent.contracts import ToolEnvelope
from app.trading_agent.model import ModelTurn
from test_harness import FakeMCP, ScriptedModel, GOOD_ANSWER21
from test_store import queued


class PositionsMCP(FakeMCP):
    async def call_tool(self, name, args, grant):
        if name != "query_positions":
            return await super().call_tool(name, args, grant)
        principal = store.resolve_grant(grant)
        envelope = ToolEnvelope(status="partial", captured_at=datetime.now(timezone.utc),
            calculation_version="test", payload={"kind": "positions", "selection": {"asset_type": "option"}},
            warnings=["缺少行情，不能计算浮盈亏"])
        ref = store.save_result(principal, envelope, [{"contract": "i2609-p-650", "asset_type": "option",
            "direction": "buy", "quantity": "2", "average_price": "10", "valuation_price": None,
            "floating_pnl": None}], kind="positions")
        return store.load_result(principal, ref).envelope


@pytest.mark.asyncio
async def test_invalid_json_keeps_current_protocol_and_verified_table(queued, caplog):
    task = store.claim_next("recovery")
    raw = '{"schema_version":"2.1","body_markdown":"PRIVATE_SENTINEL'
    model = ScriptedModel([
        ModelTurn(tool_calls=[{"id": "q1", "name": "query_positions", "arguments": {"asset_type": "option"}}]),
        ModelTurn(content=raw, finish_reason="length", usage={"completion_tokens": 2048}),
        ModelTurn(content=raw, finish_reason="length", usage={"completion_tokens": 2048}),
    ])
    with caplog.at_level("INFO"):
        result = await harness.run_task(task, harness.RuntimeDeps(store, model, PositionsMCP(), worker_id="recovery"))
    assert result.schema_version == "2.1"
    assert result.delivery_status == "partial"
    assert len(result.views) == 1
    assert len(model.calls) == 3
    assert "截断" in str(model.calls[-1])
    assert "PRIVATE_SENTINEL" not in caplog.text
    assert '"finish_reason": "length"' in caplog.text
    assert '"completion_tokens": 2048' in caplog.text
    with store.db.connect() as conn:
        message = conn.execute("SELECT structured_payload FROM closing_review_messages WHERE task_id=? AND role='assistant'", (task,)).fetchone()
    assert json.loads(message["structured_payload"])["views"] == result.views


@pytest.mark.asyncio
async def test_invalid_json_can_repair_without_changing_protocol(queued):
    task = store.claim_next("repair21")
    model = ScriptedModel([ModelTurn(content="not json"), ModelTurn(content=GOOD_ANSWER21)])
    result = await harness.run_task(task, harness.RuntimeDeps(store, model, FakeMCP(), worker_id="repair21"))
    assert result.delivery_status == "complete"
    assert len(model.calls) == 2


@pytest.mark.asyncio
async def test_length_finish_requires_repair_even_when_json_is_parseable(queued):
    task = store.claim_next("length-repair")
    model = ScriptedModel([ModelTurn(content=GOOD_ANSWER21, finish_reason="length"),
                           ModelTurn(content=GOOD_ANSWER21, finish_reason="stop")])
    result = await harness.run_task(task, harness.RuntimeDeps(store, model, FakeMCP(), worker_id="length-repair"))
    assert result.delivery_status == "complete"
    assert len(model.calls) == 2


def test_fallback_does_not_present_capabilities_as_business_data(queued):
    task = store.claim_next("filter")
    principal = store.principal_for_task(task)
    envelope = ToolEnvelope(status="complete", captured_at=datetime.now(timezone.utc),
        calculation_version="test", payload={"kind": "capabilities"})
    ref = store.save_result(principal, envelope, [], kind="capabilities")
    result = answer.build_fallback21(principal, [str(ref)], {}, "invalid_json", store)
    assert result.delivery_status == "failed"
    assert result.views == []


def test_parser_error_keeps_safe_position_without_raw_content():
    with pytest.raises(answer.Answer21ValidationError) as err:
        answer.parse_answer21('{"schema_version":"2.1","body_markdown":"PRIVATE_SENTINEL')
    assert "offset=" in err.value.issues[0].message
    assert "PRIVATE_SENTINEL" not in err.value.issues[0].message


@pytest.mark.parametrize("invalid", ["expired", "permission", "scope", "aggregation"])
def test_fallback_excludes_unreadable_or_unrelated_results(queued, invalid):
    task = store.claim_next("filter-bounds")
    principal = store.principal_for_task(task)
    envelope = ToolEnvelope(status="partial", captured_at=datetime.now(timezone.utc), calculation_version="test",
        payload={"kind": "positions", "selection": {"asset_type": "future"}, "aggregation": invalid == "aggregation"})
    ref = store.save_result(principal, envelope, [{"quantity": "2"}], kind="positions")
    with store.db.connect() as conn:
        if invalid == "expired":
            conn.execute("UPDATE agent_v2_results SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (str(ref),))
        if invalid == "permission":
            conn.execute("UPDATE module_permissions SET can_view=0 WHERE user_id=? AND module_code='trading_positions'", (principal.user_id,))
    scope = {"asset_type": "option"} if invalid == "scope" else {}
    result = answer.build_fallback21(principal, [str(ref)], scope, "invalid_json", store)
    assert result.delivery_status == "failed"
    assert result.views == []
