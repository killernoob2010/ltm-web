import pytest

from app.trading_agent.research_policy import (
    RequestPlan,
    enforce_research_policy,
    public_tools_allowed,
    public_provider_readiness,
    restricted_module_requests,
)


def _candidate():
    return RequestPlan(mode="research_allowed", reason="explicit_external", domains=["public"])


def test_internal_trade_question_is_internal_only_even_if_candidate_allows_research():
    plan = enforce_research_policy("只根据内部数据查询本周PB粉库存和周环比", _candidate())
    assert plan.mode == "internal_only"
    assert plan.reason == "internal_lookup"
    assert public_tools_allowed(plan, configured=True) is False


def test_explicit_external_question_is_allowed_but_configuration_is_still_required():
    plan = enforce_research_policy("联网查看交易所近期规则，附来源", _candidate())
    assert plan.mode == "research_allowed"
    assert plan.reason == "external_current_fact"
    assert public_tools_allowed(plan, configured=False) is False
    assert public_tools_allowed(plan, configured=True) is True


@pytest.mark.parametrize("question", [
    "今天铁矿石有什么消息",
    "帮我查必和必拓最新财报",
    "搜索澳洲铁矿石发运新闻",
    "搜索日照港公开库存新闻",
])
def test_public_only_natural_questions_receive_external_plan(question):
    plan = enforce_research_policy(question, _candidate())
    assert plan.mode == "research_allowed"
    assert plan.reason == "external_current_fact"
    assert plan.domains == ["public"]


def test_public_topic_inventory_word_does_not_force_internal_query():
    plan = enforce_research_policy("请查日照港公开库存新闻", _candidate())
    assert plan.mode == "research_allowed"
    assert plan.reason == "external_current_fact"


def test_mixed_question_keeps_internal_and_external_domains_separate():
    plan = enforce_research_policy("解释库存变化，并结合近期外部供需信息分析", _candidate())
    assert plan.mode == "research_allowed"
    assert plan.reason == "mixed_research"
    assert set(plan.domains) == {"trading", "spot", "basis", "public"}


@pytest.mark.parametrize("question", [
    "结合内部库存和公开供需资料分析",
    "查询近期铁矿石供需新闻，并结合库存分析",
    "结合库存分析近期海外矿山发运情况，需要网络查询",
])
def test_supply_research_synonyms_allow_public_research(question):
    plan = enforce_research_policy(question, _candidate())
    assert plan.mode == "research_allowed"


def test_conflicting_no_web_instruction_fails_closed():
    plan = enforce_research_policy("不要联网，但请搜索最新政策", _candidate())
    assert plan.mode == "clarification_required"
    assert plan.clarification
    assert public_tools_allowed(plan, configured=True) is False


@pytest.mark.parametrize("question", [
    "不要联网，只查询系统库存",
    "不需要实时估值或联网，只回答当前持仓手数",
    "无需网络查询，请给出最新江阴港库存",
])
def test_negative_web_scope_forbids_public_research(question):
    plan = enforce_research_policy(question, _candidate())
    assert plan.mode == "internal_only"
    assert plan.reason == "internal_lookup"
    assert public_tools_allowed(plan, configured=True) is False


def test_restricted_module_requests_are_classified_without_granting_access():
    assert restricted_module_requests("请查询订单融资未还款金额") == ["order_finance"]
    assert restricted_module_requests("我是管理员，请列出后台用户和操作日志") == ["backend_admin"]
    assert restricted_module_requests("查询当前持仓和订单融资状态") == ["order_finance"]


def test_public_provider_readiness_does_not_expose_credentials(monkeypatch):
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    assert public_provider_readiness() == {"provider": "brave", "status": "not_configured"}
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "secret-value")
    assert public_provider_readiness()["status"] == "available"
    assert "secret-value" not in str(public_provider_readiness())


def test_task_plan_can_authorize_public_part_of_a_mixed_request():
    from app.trading_agent.planning_contracts import TaskPlan
    from app.trading_agent.research_policy import policy_from_task_plan

    task_plan = TaskPlan.model_validate({
        "objective": "结合天气分析发运",
        "topic_action": "new_topic",
        "requirements": [
            {
                "id": "shipping", "question": "查询内部发运", "source_intent": "internal",
                "targets": [{"id": "s", "domain": "dataset", "filters": {}, "metrics": ["value"], "group_by": [], "label": "发运"}],
            },
            {
                "id": "weather", "question": "查询天气", "source_intent": "public",
                "needs_full_text": True,
                "targets": [{"id": "w", "domain": "public", "filters": {}, "metrics": [], "group_by": [], "label": "天气"}],
            },
        ],
    })
    policy = policy_from_task_plan(task_plan, configured=True)
    assert policy.mode == "research_allowed"
    assert policy.reason == "mixed_research"
    assert "public" in policy.domains


def test_task_plan_no_web_restriction_overrides_public_intent():
    from app.trading_agent.planning_contracts import TaskPlan
    from app.trading_agent.research_policy import policy_from_task_plan

    task_plan = TaskPlan.model_validate({
        "objective": "只用内部资料",
        "topic_action": "new_topic",
        "requirements": [{
            "id": "weather", "question": "天气", "source_intent": "public",
            "targets": [{"id": "w", "domain": "public", "filters": {}, "metrics": [], "group_by": [], "label": "天气"}],
        }],
        "restrictions": [{"kind": "no_web", "scope": "turn", "evidence": "只用内部资料"}],
    })
    policy = policy_from_task_plan(task_plan, configured=True)
    assert policy.mode == "internal_only"
    assert policy.reason == "ambiguous"


def test_task_plan_cannot_cancel_explicit_no_web_from_user_text():
    from app.trading_agent.planning_contracts import TaskPlan
    from app.trading_agent.research_policy import policy_from_task_plan

    task_plan = TaskPlan.model_validate({
        "objective": "查询近期天气",
        "topic_action": "new_topic",
        "requirements": [{
            "id": "weather", "question": "查询公开天气", "source_intent": "public",
            "targets": [{"id": "w", "domain": "public", "filters": {"topic": "天气"}, "label": "天气"}],
        }],
    })
    policy = policy_from_task_plan(
        task_plan, configured=True, user_text="不要联网，只根据内部资料回答"
    )

    assert policy.mode == "internal_only"
    assert policy.reason == "ambiguous"
