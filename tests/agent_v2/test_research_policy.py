import pytest

from app.trading_agent.research_policy import (
    RequestPlan,
    enforce_research_policy,
    public_tools_allowed,
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
