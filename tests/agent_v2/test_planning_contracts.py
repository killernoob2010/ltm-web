import pytest
from pydantic import ValidationError

from app.trading_agent.planning_contracts import (
    AnalysisTarget,
    Requirement,
    TaskPlan,
    UserRestriction,
)


def _target(target_id="call"):
    return {
        "id": target_id,
        "domain": "positions",
        "filters": {"asset_type": "option", "option_type": "call"},
        "metrics": ["quantity", "net_quantity", "floating_pnl"],
        "group_by": ["option_type"],
        "net_intent": "net_buy",
        "label": "Call净买目标",
    }


def _requirement(requirement_id="r1"):
    return {
        "id": requirement_id,
        "question": "统计净买 Call 的手数和浮盈浮亏",
        "targets": [_target()],
        "source_intent": "internal",
        "depends_on": [],
        "needs_full_text": False,
        "time_requirement": "当前有效持仓",
    }


def test_task_plan_accepts_two_independent_analysis_targets():
    plan = TaskPlan.model_validate({
        "objective": "分别回答净买 Call 和净卖 Put",
        "topic_action": "new_topic",
        "requirements": [
            _requirement("call_requirement"),
            {
                **_requirement("put_requirement"),
                "question": "统计净卖 Put 的手数和浮盈浮亏",
                "targets": [_target("put") | {
                    "filters": {"asset_type": "option", "option_type": "put"},
                    "net_intent": "net_sell",
                    "label": "Put净卖目标",
                }],
            },
        ],
        "condition_origins": [],
        "restrictions": [],
        "presentation": "text",
        "prohibited_presentations": ["table"],
        "clarification": None,
    })
    assert [item.targets[0].net_intent for item in plan.requirements] == ["net_buy", "net_sell"]


def test_task_plan_rejects_model_owned_identity_and_budget_fields():
    with pytest.raises(ValidationError):
        TaskPlan.model_validate({
            "objective": "查询持仓",
            "topic_action": "new_topic",
            "requirements": [_requirement()],
            "condition_origins": [],
            "restrictions": [],
            "presentation": "auto",
            "prohibited_presentations": [],
            "user_id": 999,
        })


def test_task_plan_rejects_empty_requirements_and_duplicate_target_ids():
    with pytest.raises(ValidationError):
        TaskPlan.model_validate({
            "objective": "空任务",
            "topic_action": "new_topic",
            "requirements": [],
            "condition_origins": [],
            "restrictions": [],
            "presentation": "auto",
            "prohibited_presentations": [],
        })
    with pytest.raises(ValidationError):
        TaskPlan.model_validate({
            "objective": "重复目标",
            "topic_action": "new_topic",
            "requirements": [
                _requirement("r1"),
                {**_requirement("r2"), "targets": [_target("call")]},
            ],
            "condition_origins": [],
            "restrictions": [],
            "presentation": "auto",
            "prohibited_presentations": [],
        })


def test_user_restriction_scope_is_explicit():
    restriction = UserRestriction.model_validate({
        "kind": "no_web",
        "scope": "turn",
        "evidence": "只用内部数据",
    })
    assert restriction.scope == "turn"
