import asyncio
import json
from datetime import date, datetime, timezone

import pytest

from app.trading_agent.planner import (
    PlannerError,
    apply_time_windows,
    build_policy_fallback_plan,
    create_plan,
    validate_plan,
)
from app.trading_agent.model import ModelTurn
from app.trading_agent.planning_contracts import TaskPlan
from app.trading_agent.research_policy import RequestPlan


class PlanModel:
    def __init__(self, contents):
        self.contents = list(contents)
        self.calls = []

    def next_turn(self, messages, schemas, timeout):
        self.calls.append({"messages": messages, "schemas": schemas, "timeout": timeout})
        return ModelTurn(content=self.contents.pop(0))


class Budget:
    def __init__(self, max_models=3):
        self.model_calls = 0
        self.max_models = max_models


def _weather_plan():
    return {
        "schema_version": "1.0",
        "objective": "结合近期天气分析矿石发运影响",
        "topic_action": "new_topic",
        "requirements": [
            {
                "id": "shipping",
                "question": "确认相关期间的内部矿石发运变化",
                "targets": [{
                    "id": "internal_shipping",
                    "domain": "dataset",
                    "filters": {"products": ["铁矿石"]},
                    "metrics": ["value"],
                    "group_by": ["observation_date"],
                    "net_intent": "none",
                    "label": "内部发运",
                }],
                "source_intent": "internal",
                "depends_on": [],
                "needs_full_text": False,
                "time_requirement": "近期",
            },
            {
                "id": "weather",
                "question": "查询相关地区近期天气及运营影响",
                "targets": [{
                    "id": "public_weather",
                    "domain": "public",
                    "filters": {"topic": "天气"},
                    "metrics": [],
                    "group_by": [],
                    "net_intent": "none",
                    "label": "公开天气",
                }],
                "source_intent": "public",
                "depends_on": [],
                "needs_full_text": True,
                "time_requirement": "近期",
            },
        ],
        "condition_origins": [],
        "restrictions": [],
        "presentation": "auto",
        "prohibited_presentations": [],
        "clarification": None,
    }


def test_create_plan_splits_internal_and_public_requirements():
    model = PlanModel([json.dumps(_weather_plan(), ensure_ascii=False)])
    plan = asyncio.run(create_plan(
        "结合近期的天气，帮我分析一下对矿石发运的影响",
        {},
        {"tools": [], "conditional_sources": ["public_weather"]},
        model,
        Budget(),
    ))
    assert {item.source_intent for item in plan.requirements} == {"internal", "public"}
    assert model.calls[0]["schemas"] == []


def test_apply_time_windows_records_server_default_for_recent_requirements():
    plan = TaskPlan.model_validate(_weather_plan())
    resolved = apply_time_windows(
        plan,
        "结合近期的天气，分析矿石发运影响",
        now=datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc),
    )

    windows = {item.id: item.time_window for item in resolved.requirements}
    assert windows["shipping"].start_date == date(2026, 9, 1)
    assert windows["shipping"].end_date == date(2026, 9, 14)
    assert windows["shipping"].origin == "default"
    assert windows["weather"].origin == "default"


def test_apply_time_windows_preserves_explicit_user_dates():
    plan = TaskPlan.model_validate(_weather_plan())
    resolved = apply_time_windows(
        plan,
        "结合 2026-08-01 至 2026-08-14 的天气，分析矿石发运影响",
        now=datetime(2026, 9, 14, tzinfo=timezone.utc),
    )

    window = next(item.time_window for item in resolved.requirements if item.id == "weather")
    assert window.start_date == date(2026, 8, 1)
    assert window.end_date == date(2026, 8, 14)
    assert window.origin == "user"


def test_policy_fallback_plan_is_bounded_and_keeps_recent_public_evidence_required():
    candidate = RequestPlan(
        mode="research_allowed",
        reason="mixed_research",
        domains=["trading", "public"],
    )
    plan = build_policy_fallback_plan(
        "结合近期天气分析矿石发运影响",
        candidate,
        conversation_state={"topic_action": "new_topic"},
    )
    resolved = apply_time_windows(
        plan,
        "结合近期天气分析矿石发运影响",
        now=datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc),
    )
    validate_plan(resolved, {"tools": [], "conditional_sources": ["public_research"]})

    assert [item.source_intent for item in resolved.requirements] == ["internal", "public"]
    public = resolved.requirements[-1]
    assert public.needs_full_text is True
    assert public.targets[0].filters == {}
    assert public.time_window.start_date == date(2026, 9, 1)
    assert public.time_window.end_date == date(2026, 9, 14)
    assert public.time_window.origin == "default"


def test_policy_fallback_plan_cannot_open_public_access_for_non_public_candidate():
    candidate = RequestPlan(mode="internal_only", reason="internal_lookup", domains=["trading"])
    with pytest.raises(PlannerError, match="没有批准公开研究"):
        build_policy_fallback_plan("查询内部库存", candidate)


def test_create_plan_rejects_tool_calls_from_planning_turn():
    model = PlanModel([json.dumps(_weather_plan(), ensure_ascii=False)])
    model.next_turn = lambda messages, schemas, timeout: ModelTurn(
        tool_calls=[{"id": "x", "name": "query_positions", "arguments": {}}]
    )
    with pytest.raises(PlannerError, match="规划阶段不能调用工具"):
        asyncio.run(create_plan("查天气", {}, {}, model, Budget()))


def test_create_plan_repairs_one_invalid_json_response():
    model = PlanModel(["不是 JSON", json.dumps(_weather_plan(), ensure_ascii=False)])
    plan = asyncio.run(create_plan("结合天气看发运", {}, {}, model, Budget()))
    assert plan.objective.startswith("结合近期天气")
    assert len(model.calls) == 2
    assert model.calls[1]["messages"][-1]["role"] == "user"


def test_create_plan_stops_when_model_budget_is_exhausted():
    model = PlanModel(["不是 JSON", json.dumps(_weather_plan(), ensure_ascii=False)])
    with pytest.raises(PlannerError, match="模型预算"):
        asyncio.run(create_plan("结合天气看发运", {}, {}, model, Budget(max_models=1)))


def test_create_plan_reserves_one_model_call_for_answer_delivery():
    model = PlanModel([json.dumps(_weather_plan(), ensure_ascii=False)])
    with pytest.raises(PlannerError, match="答案整理"):
        asyncio.run(create_plan("结合天气看发运", {}, {}, model, Budget(max_models=1)))


def test_validate_plan_rejects_unregistered_filters_and_private_public_values():
    payload = _weather_plan()
    payload["requirements"][0]["targets"][0]["filters"]["not_registered"] = "x"
    plan = TaskPlan.model_validate(payload)
    with pytest.raises(PlannerError, match="筛选字段"):
        validate_plan(plan, {"tools": [], "conditional_sources": ["public_research"]})

    payload = _weather_plan()
    payload["requirements"][1]["targets"][0]["filters"]["account_id"] = 17
    plan = TaskPlan.model_validate(payload)
    with pytest.raises(PlannerError, match="公开目标"):
        validate_plan(plan, {"tools": [], "conditional_sources": ["public_research"]})


def test_validate_plan_uses_registered_filter_types_and_source_domains():
    payload = _weather_plan()
    payload["requirements"][0]["targets"][0]["filters"]["products"] = "铁矿石"
    plan = TaskPlan.model_validate(payload)
    with pytest.raises(PlannerError, match="筛选类型"):
        validate_plan(plan, {"tools": [], "conditional_sources": ["public_research"]})

    payload = _weather_plan()
    payload["requirements"][1]["source_intent"] = "internal"
    plan = TaskPlan.model_validate(payload)
    with pytest.raises(PlannerError, match="来源域"):
        validate_plan(plan, {"tools": [], "conditional_sources": ["public_research"]})


def test_validate_plan_accepts_registered_nested_dataset_filters():
    payload = _weather_plan()
    target = payload["requirements"][0]["targets"][0]
    target["filters"] = {
        "dataset": "inventory_summary",
        "mode": "range",
        "start_date": "2026-01-01",
        "end_date": "2026-01-31",
        "filters": {"ports": ["日照"], "data_states": ["observed"]},
    }
    plan = TaskPlan.model_validate(payload)
    assert validate_plan(plan, {"tools": [], "conditional_sources": ["public_research"]}) == plan
