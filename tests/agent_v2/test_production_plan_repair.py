from datetime import datetime, timezone
from uuid import uuid4

from app.trading_agent.contracts import MetricValue, ToolEnvelope
from app.trading_agent.coverage import assess_evidence
from app.trading_agent.planner import PLANNER_SYSTEM, apply_time_windows
from app.trading_agent.planning_contracts import TaskPlan


def _position_plan(*, with_window=True, compare=False, pnl=True):
    requirements = [
        {
            "id": "call",
            "question": "当前期权 Call 净买手数",
            "targets": [{
                "id": "call_target",
                "domain": "positions",
                "filters": {"asset_type": "option", "option_type": "call"},
                "metrics": ["gross_quantity", "net_quantity", *(["floating_pnl"] if pnl else [])],
                "group_by": ["contract_month"],
                "net_intent": "net_buy",
                "label": "Call",
            }],
            "source_intent": "internal",
            "time_requirement": "当前持仓（最新快照）",
            **({"time_window": {"start_date": "2024-01-01", "end_date": "2024-12-31", "origin": "default"}} if with_window else {}),
        },
        {
            "id": "put",
            "question": "当前期权 Put 净卖手数",
            "targets": [{
                "id": "put_target",
                "domain": "positions",
                "filters": {"asset_type": "option", "option_type": "put"},
                "metrics": ["net_quantity", *(["floating_pnl"] if pnl else [])],
                "group_by": ["contract_month"],
                "net_intent": "net_sell",
                "label": "Put",
            }],
            "source_intent": "internal",
            "time_requirement": "当前持仓（最新快照）",
            **({"time_window": {"start_date": "2024-01-01", "end_date": "2024-12-31", "origin": "default"}} if with_window else {}),
        },
    ]
    if compare:
        requirements.append({
            "id": "distribution",
            "question": "Call 与 Put 的月份分布是否一致",
            "targets": [{
                "id": "distribution_target", "domain": "positions",
                "filters": {"asset_type": "option"}, "metrics": ["net_quantity"],
                "group_by": ["contract_month"], "net_intent": "net",
                "label": "月份比较",
            }],
            "source_intent": "internal", "depends_on": ["call", "put"],
            "time_requirement": "当前持仓（最新快照）",
            "analysis": {"operation": "compare", "metric": "net_quantity", "comparison_basis": "explicit", "conclusion_required": True},
        })
    return TaskPlan.model_validate({
        "objective": "期权持仓分析", "topic_action": "new_topic", "requirements": requirements,
        "condition_origins": [], "restrictions": [], "presentation": "text",
        "prohibited_presentations": ["table"],
    })


def test_current_positions_override_model_owned_historical_window():
    plan = _position_plan()

    resolved = apply_time_windows(
        plan,
        "只用内部数据，查询当前期权持仓，只要手数，不联网。",
        now=datetime(2026, 9, 15, 19, 0, tzinfo=timezone.utc),
    )

    assert all(item.time_window is None for item in resolved.requirements)


def test_current_positions_without_window_remain_latest_snapshot():
    plan = _position_plan(with_window=False)

    resolved = apply_time_windows(
        plan,
        "查询当前持仓的期权手数。",
        now=datetime(2026, 9, 15, 19, 0, tzinfo=timezone.utc),
    )

    assert all(item.time_window is None for item in resolved.requirements)


def test_explicit_historical_position_date_becomes_settlement_snapshot():
    resolved = apply_time_windows(
        _position_plan(),
        "只用内部数据，查询 2024-08-01 的期权持仓手数。",
        now=datetime(2026, 9, 15, 19, 0, tzinfo=timezone.utc),
    )

    assert all(item.time_window is None for item in resolved.requirements)
    assert all(
        target.filters["as_of_mode"] == "settlement_date"
        and target.filters["as_of_date"] == "2024-08-01"
        for item in resolved.requirements
        for target in item.targets
    )


def test_quantity_only_request_drops_unrequested_pnl_and_comparison():
    plan = _position_plan(compare=True, pnl=True)

    resolved = apply_time_windows(
        plan,
        "只用内部数据，汇总当前期权净买 Call 和净卖 Put 的手数，按合约月份列出，只要数量。",
        now=datetime(2026, 9, 15, 19, 0, tzinfo=timezone.utc),
    )

    assert [item.id for item in resolved.requirements] == ["call", "put"]
    assert all(
        metric not in {"floating_pnl", "gross_quantity", "gross_buy_quantity", "gross_sell_quantity"}
        for item in resolved.requirements
        for target in item.targets
        for metric in target.metrics
    )


def test_explicit_comparison_request_keeps_comparison_requirement():
    plan = _position_plan(compare=True, pnl=False)

    resolved = apply_time_windows(
        plan,
        "查询当前期权手数，并比较 Call 与 Put 的月份分布是否一致。",
        now=datetime(2026, 9, 15, 19, 0, tzinfo=timezone.utc),
    )

    assert [item.id for item in resolved.requirements] == ["call", "put", "distribution"]


def test_followup_only_put_drops_model_call_expansion_and_keeps_scope_filters():
    plan = _position_plan(compare=False, pnl=False)
    plan = plan.model_copy(update={
        "requirements": [item.model_copy(update={
            "targets": [target.model_copy(update={
                "filters": {**target.filters, "contract_months": ["2701"]},
            }) for target in item.targets],
        }) for item in plan.requirements],
    })

    resolved = apply_time_windows(
        plan,
        "连续追问：只看Put，只说数量。",
        now=datetime(2026, 9, 15, 19, 0, tzinfo=timezone.utc),
    )

    assert [item.id for item in resolved.requirements] == ["put"]
    target = resolved.requirements[0].targets[0]
    assert target.filters["option_type"] == "put"
    assert target.filters["contract_months"] == ["2701"]


def test_planner_prompt_does_not_force_unrequested_floating_pnl():
    assert "并同时请求 gross 数量、净额和实际浮盈亏" not in PLANNER_SYSTEM
    assert "用户明确要求的指标" in PLANNER_SYSTEM


def test_current_position_snapshot_ignores_model_window_in_evidence_scope():
    plan = apply_time_windows(
        _position_plan(),
        "只用内部数据，查询当前期权持仓，只要手数，不联网。",
        now=datetime(2026, 9, 15, 19, 0, tzinfo=timezone.utc),
    )
    envelopes = []
    for option_type in ("call", "put"):
        envelopes.append(ToolEnvelope(
            status="complete",
            result_ref=uuid4(),
            captured_at=datetime(2026, 9, 15, 11, 0, tzinfo=timezone.utc),
            calculation_version="coverage-repair-test-v1",
            payload={
                "kind": "positions",
                "selection": {
                    "filters": {
                        "asset_type": "option",
                        "option_type": option_type,
                        "as_of_mode": "latest",
                        "as_of_date": None,
                    },
                },
            },
            metrics={
                "net_quantity": MetricValue(
                    value="3", unit="手", status="complete", covered_rows=1, eligible_rows=1,
                ),
            },
        ))

    report = assess_evidence(plan, envelopes)

    assert report.complete is True
    assert all(item.actual_scope["targets"] for item in report.items)
    assert all(
        target_scope["filter_status"] == "matched"
        for item in report.items
        for target_scope in item.actual_scope["targets"].values()
    )


def test_unresolved_default_position_window_cannot_match_latest_snapshot():
    envelope = ToolEnvelope(
        status="complete",
        result_ref=uuid4(),
        captured_at=datetime(2026, 9, 15, 11, 0, tzinfo=timezone.utc),
        calculation_version="coverage-repair-test-v1",
        payload={
            "kind": "positions",
            "selection": {"filters": {"asset_type": "option", "option_type": "call"}},
            "as_of": {"mode": "latest", "date": None},
        },
        metrics={
            "net_quantity": MetricValue(
                value="3", unit="手", status="complete", covered_rows=1, eligible_rows=1,
            ),
        },
    )

    report = assess_evidence(_position_plan(), [envelope])

    call = next(item for item in report.items if item.requirement_id == "call")
    assert call.status == "partial"
    assert "target.call_target.scope_mismatch" in call.missing_codes
