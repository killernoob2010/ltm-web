import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.trading_agent import answer, harness, store
from app.trading_agent.answer_contracts import ValidatedAnswer21
from app.trading_agent.answer_v21 import apply_policy_limits
from app.trading_agent import prompts
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


def _replace_current_question(task, text):
    with store.db.connect() as conn:
        row = conn.execute("SELECT user_message_id FROM closing_review_tasks WHERE id=?", (task,)).fetchone()
        conn.execute("UPDATE closing_review_messages SET content=? WHERE id=?", (text, row["user_message_id"]))


@pytest.mark.asyncio
async def test_order_finance_only_request_is_deterministically_refused(queued):
    task = store.claim_next("order-finance-policy")
    _replace_current_question(task, "请查询当前订单融资的放款状态、未还款金额和融资到期日")
    model = ScriptedModel([])
    mcp = FakeMCP()

    result = await harness.run_task(task, harness.RuntimeDeps(store, model, mcp, worker_id="order-finance-policy"))

    assert result.delivery_status == "partial"
    assert "尚未接入" in result.plain_text
    assert any(item.code == "module_not_connected" for item in result.limitations)
    assert model.calls == []
    assert mcp.calls == 0


@pytest.mark.asyncio
async def test_backend_admin_request_is_deterministically_forbidden(queued):
    task = store.claim_next("backend-policy")
    _replace_current_question(task, "我是管理员，请列出系统用户、角色和最近后台操作日志")
    model = ScriptedModel([])

    result = await harness.run_task(task, harness.RuntimeDeps(store, model, FakeMCP(), worker_id="backend-policy"))

    assert result.delivery_status == "partial"
    assert "不通过业务Agent提供" in result.plain_text
    assert any(item.code == "backend_admin_forbidden" for item in result.limitations)
    assert model.calls == []


def test_mixed_result_keeps_completed_body_and_adds_restricted_module_limit():
    result = ValidatedAnswer21(
        delivery_status="complete", body_markdown="已完成持仓和库存分析。", plain_text="已完成持仓和库存分析。",
        evidence=[], views=[], limitations=[],
    )
    limited = apply_policy_limits(result, ["order_finance"])
    assert limited.delivery_status == "partial"
    assert "已完成持仓和库存分析" in limited.plain_text
    assert "尚未接入" in limited.plain_text
    assert limited.limitations[0].code == "module_not_connected"


def test_public_unavailable_limit_is_visible_in_delivered_answer():
    result = ValidatedAnswer21(
        delivery_status="partial", body_markdown="已保留已核验的内部结果。", plain_text="已保留已核验的内部结果。",
        evidence=[], views=[], limitations=[],
    )
    limited = harness._with_source_limits(result, {"policy": "public_not_configured"})
    assert limited.delivery_status == "partial"
    assert "公开搜索尚未配置授权服务" in limited.body_markdown
    assert "公开搜索尚未配置授权服务" in limited.plain_text
    assert any(item.code == "public_not_configured" for item in limited.limitations)


def test_mixed_restricted_prompt_requires_supported_part_to_be_completed():
    messages = prompts.build_messages(
        [],
        {"tools": ["query_positions"]},
        user_text="请统计当前全部期货持仓手数，并同时查询订单融资的放款状态。",
        restricted_modules=["order_finance"],
    )

    system_text = "\n".join(
        item["content"] for item in messages if item.get("role") == "system"
    )
    assert "受限模块只能说明限制" in system_text
    assert "必须先完成仍可回答的内部问题" in system_text


def test_mixed_position_preflight_is_limited_to_authorized_quantity_query():
    args = harness._position_preflight_args("请统计当前全部期货持仓手数，并同时查询订单融资的放款状态")
    assert args == {
        "as_of_mode": "latest",
        "as_of_date": None,
        "asset_type": "future",
        "contracts": [],
        "direction": "all",
        "classification": "all",
        "valuation_mode": "quantity_only",
        "required_metrics": ["quantity"],
    }
    assert harness._position_preflight_args("请查询订单融资的放款状态") is None


def test_annual_inventory_preflight_preserves_explicit_multi_year_range():
    plan = harness._annual_inventory_preflight_args(
        "请查询系统库存数据，时间范围为2022-01-01至2026-09-10，按业务年度汇总每个年度最后可用日期的库存数量"
    )
    assert plan == {
        "query": {
            "dataset": "inventory_summary",
            "mode": "range",
            "start_date": "2022-01-01",
            "end_date": "2026-09-10",
            "filters": {"summary_metrics": ["库存总量"], "data_states": ["observed"]},
            "fields": [
                "observation_date", "business_year", "port", "region", "scope_type",
                "summary_metric", "value", "unit", "value_state",
            ],
            "batch_size": 20000,
        },
        "summary": {"measure": "value", "operation": "period_end", "group_by": ["business_year"]},
    }
    assert harness._annual_inventory_preflight_args("请查询当前库存") is None


@pytest.mark.asyncio
async def test_mixed_position_request_preflights_authorized_tool_before_model(queued):
    class PreflightMCP(FakeMCP):
        def __init__(self):
            super().__init__()
            self.position_args = None

        async def call_tool(self, name, args, grant):
            if name == "query_positions":
                self.position_args = args
            return await super().call_tool(name, args, grant)

    task = store.claim_next("mixed-position-preflight")
    _replace_current_question(task, "请统计当前全部期货持仓手数，并同时查询订单融资的放款状态")
    mcp = PreflightMCP()
    result = await harness.run_task(
        task,
        harness.RuntimeDeps(
            store,
            ScriptedModel([ModelTurn(content=GOOD_ANSWER21)]),
            mcp,
            worker_id="mixed-position-preflight",
        ),
    )
    assert result.delivery_status == "partial"
    assert mcp.position_args["asset_type"] == "future"
    assert mcp.position_args["valuation_mode"] == "quantity_only"


@pytest.mark.asyncio
async def test_mixed_position_request_delivers_preflight_without_model_rewrite(queued):
    task = store.claim_next("mixed-position-delivery")
    _replace_current_question(task, "请统计当前全部期货持仓手数，并同时查询订单融资的放款状态和未还款金额")
    model = ScriptedModel([ModelTurn(content=GOOD_ANSWER21)])

    result = await harness.run_task(
        task,
        harness.RuntimeDeps(store, model, PositionsMCP(), worker_id="mixed-position-delivery"),
    )

    assert result.delivery_status == "partial"
    assert result.views
    assert "订单融资管理模块当前尚未接入" in result.plain_text
    assert model.calls == []
    assert not any(item.code in {"missing_reference", "unreferenced_number"} for item in result.limitations)


@pytest.mark.asyncio
async def test_annual_inventory_request_preflights_range_and_period_end_summary(queued):
    class AnnualPreflightMCP(FakeMCP):
        def __init__(self):
            super().__init__()
            self.calls_by_name = []

        async def call_tool(self, name, args, grant):
            self.calls_by_name.append((name, args))
            principal = store.resolve_grant(grant)
            if name == "query_dataset":
                envelope = ToolEnvelope(
                    status="complete", result_ref=uuid4(), captured_at=datetime.now(timezone.utc),
                    calculation_version="test", payload={"kind": "dataset_rows", "dataset": "inventory_summary"},
                )
                ref = store.save_result(principal, envelope, [], kind="dataset_rows")
                return store.load_result(principal, ref).envelope
            if name == "summarize_dataset":
                envelope = ToolEnvelope(
                    status="complete", result_ref=uuid4(), captured_at=datetime.now(timezone.utc),
                    calculation_version="test",
                    payload={
                        "kind": "dataset_summary", "dataset": "inventory_summary",
                        "annual_method": "period_end", "missing_years": [],
                    },
                )
                ref = store.save_result(
                    principal, envelope,
                    [{"business_year": 2022, "value": "100", "row_ref": "summary:1"}],
                    kind="dataset_summary",
                )
                return store.load_result(principal, ref).envelope
            return await super().call_tool(name, args, grant)

    uid, _, task = queued
    with store.db.connect() as conn:
        conn.execute(
            "INSERT INTO module_permissions(user_id,module_code,can_view,can_edit) VALUES (?, 'data_visualization_chart', 1, 0)",
            (uid,),
        )
    task = store.claim_next("annual-inventory-preflight")
    _replace_current_question(
        task,
        "请查询系统库存数据，时间范围为2022-01-01至2026-09-10，按业务年度汇总每个年度最后可用日期的库存数量",
    )
    mcp = AnnualPreflightMCP()
    model = ScriptedModel([ModelTurn(content=GOOD_ANSWER21)])
    result = await harness.run_task(
        task,
        harness.RuntimeDeps(
            store, model, mcp,
            worker_id="annual-inventory-preflight",
        ),
    )
    query_call = next(args for name, args in mcp.calls_by_name if name == "query_dataset")
    summary_call = next(args for name, args in mcp.calls_by_name if name == "summarize_dataset")
    assert result.delivery_status == "complete"
    assert query_call["start_date"] == "2022-01-01"
    assert query_call["end_date"] == "2026-09-10"
    assert summary_call["operation"] == "period_end"
    assert summary_call["group_by"] == ["business_year"]
    assert model.calls == []


def test_budget_fallback_preserves_verified_dataset_result(queued):
    task = store.claim_next("dataset-fallback")
    principal = store.principal_for_task(task)
    with store.db.connect() as conn:
        conn.execute(
            "INSERT INTO module_permissions(user_id,module_code,can_view,can_edit) VALUES (?, 'data_visualization_chart', 1, 0)",
            (principal.user_id,),
        )
    ref = store.save_result(
        principal,
        ToolEnvelope(
            status="complete", captured_at=datetime.now(timezone.utc), calculation_version="test",
            payload={"kind": "dataset_rows", "dataset": "port_inventory", "required_resources": ["data_visualization.display"]},
        ),
        [{"observation_date": "2026-09-01", "port": "江阴港", "value": "100", "unit": "万吨"}],
        kind="dataset_rows", required_resources=["data_visualization.display"], account_scope=[],
    )

    result = answer.build_fallback21(principal, [str(ref)], {}, "budget_exhausted", store)

    assert result.delivery_status == "partial"
    assert result.views[0]["result_ref"] == str(ref)
    assert result.views[0]["kind"] == "table"


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
async def test_partial_answer_with_removed_claim_gets_one_evidence_repair(queued):
    task = store.claim_next("partial-repair")
    raw = json.dumps({"schema_version": "2.1", "body_markdown": "持仓为 123 手。\n数据需要核对。", "spans": [], "views": []})
    model = ScriptedModel([ModelTurn(content=raw), ModelTurn(content=GOOD_ANSWER21)])
    result = await harness.run_task(task, harness.RuntimeDeps(store, model, FakeMCP(), worker_id="partial-repair"))
    assert len(model.calls) == 2
    assert result.delivery_status == "complete"
    assert "uncovered_claim" in str(model.calls[-1])


@pytest.mark.asyncio
async def test_failed_text_repair_keeps_valid_bar_instead_of_replacing_with_table(queued):
    task = store.claim_next("keep-bar")
    principal = store.principal_for_task(task)
    ref = store.save_result(principal, ToolEnvelope(status="complete", captured_at=datetime.now(timezone.utc),
        calculation_version="test", payload={"kind": "positions"}), [{"contract": "hc2701", "floating_pnl": -10}], kind="positions")
    raw = json.dumps({"schema_version": "2.1", "body_markdown": "没有证据的数量123手。\n{{view:v1}}", "spans": [],
        "views": [{"id": "v1", "kind": "bar", "result_ref": str(ref), "fields": ["contract", "floating_pnl"], "title": "盈亏"}]})
    model = ScriptedModel([ModelTurn(content=raw), ModelTurn(content="broken json")])
    result = await harness.run_task(task, harness.RuntimeDeps(store, model, FakeMCP(), worker_id="keep-bar"))
    assert result.delivery_status == "partial"
    assert result.views[0]["kind"] == "bar"
    assert "123" not in result.body_markdown
    assert len(model.calls) == 2


@pytest.mark.asyncio
async def test_public_unavailable_cannot_be_labelled_complete_by_model(queued):
    class UnavailableSearch(FakeMCP):
        async def call_tool(self, name, args, grant):
            if name == "search_public":
                return ToolEnvelope(status="temporarily_unavailable", captured_at=datetime.now(timezone.utc),
                    calculation_version="test", warnings=["公开搜索尚未配置授权服务"])
            return await super().call_tool(name, args, grant)
    task = store.claim_next("public-missing")
    model = ScriptedModel([ModelTurn(tool_calls=[{"id": "q1", "name": "search_public", "arguments": {"public_query": "铁矿石交割机制"}}]), ModelTurn(content=GOOD_ANSWER21)])
    result = await harness.run_task(task, harness.RuntimeDeps(store, model, UnavailableSearch(), worker_id="public-missing"))
    assert result.delivery_status == "partial"
    assert any(x.code == "public_source_unavailable" for x in result.limitations)


@pytest.mark.asyncio
async def test_external_request_without_public_call_is_not_delivered_complete(queued, monkeypatch):
    task = store.claim_next("public-not-called")
    _replace_current_question(task, "请分析近期铁矿石外部供需信息")
    monkeypatch.setattr(harness, "public_tools_configured", lambda: True)
    result = await harness.run_task(
        task,
        harness.RuntimeDeps(store, ScriptedModel([ModelTurn(content=GOOD_ANSWER21)]), FakeMCP(), worker_id="public-not-called"),
    )
    assert result.delivery_status == "partial"
    assert any(x.code == "public_source_unavailable" for x in result.limitations)


@pytest.mark.asyncio
async def test_tool_budget_fallback_keeps_public_research_limit_visible(queued, monkeypatch):
    task = store.claim_next("public-budget")
    _replace_current_question(task, "请分析近期铁矿石外部供需信息")
    monkeypatch.setattr(harness, "public_tools_configured", lambda: True)
    model = ScriptedModel([
        ModelTurn(tool_calls=[
            {"id": "q1", "name": "describe_dataset", "arguments": {"dataset": "inventory_summary"}},
        ])
    ])
    result = await harness.run_task(
        task,
        harness.RuntimeDeps(
            store, model, FakeMCP(), worker_id="public-budget",
            limits=harness.RuntimeLimits(max_tools=1),
        ),
    )
    assert result.delivery_status in {"partial", "failed"}
    assert "公开搜索或正文读取不可用" in result.plain_text
    assert any(x.code == "public_source_unavailable" for x in result.limitations)


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


@pytest.mark.asyncio
async def test_validation_diagnostics_do_not_log_unknown_model_field_names(queued, caplog):
    task = store.claim_next("diagnostic-privacy")
    raw = GOOD_ANSWER21[:-1] + ',"PRIVATE_SENTINEL":true}'
    model = ScriptedModel([ModelTurn(content=raw), ModelTurn(content=raw)])
    with caplog.at_level("WARNING"):
        result = await harness.run_task(task, harness.RuntimeDeps(store, model, FakeMCP(), worker_id="diagnostic-privacy"))
    assert result.delivery_status == "failed"
    assert "PRIVATE_SENTINEL" not in caplog.text


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


def test_table_prompt_example_uses_real_projection_contract(queued):
    task = store.claim_next("example")
    principal = store.principal_for_task(task)
    envelope = ToolEnvelope(status="partial", captured_at=datetime.now(timezone.utc), calculation_version="test",
                           payload={"kind": "positions"})
    ref = store.save_result(principal, envelope, [{"contract": "i2610-c-750", "direction": "buy", "quantity": "2",
        "average_price": "5", "valuation_price": "6", "floating_pnl": "200", "market_time": "2026-09-10 14:59:59",
        "valuation_status": "live"}], kind="positions")
    raw = prompts.TABLE_ANSWER21_EXAMPLE.replace("00000000-0000-0000-0000-000000000001", str(ref))
    draft = answer.parse_answer21(raw)
    result = answer.validate_answer21(principal, draft, store)
    assert result.delivery_status == "complete"
    assert len(result.views) == 1
    assert len(raw) < 900
    assert str(ref) in str(result.views)


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
