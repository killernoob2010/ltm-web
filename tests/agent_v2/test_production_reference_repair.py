from app.trading_agent import delivery_gate, pydantic_runtime, prompts
from app.trading_agent.answer_contracts import (
    Limitation,
    ModelAnswer21,
    ValidatedAnswer21,
    ValidationSummary,
    VALIDATION_CHECKS,
)
from app.trading_agent.planning_contracts import TaskPlan


def _knowledge_plan():
    return TaskPlan.model_validate({
        "objective": "解释期权规则",
        "topic_action": "new_topic",
        "requirements": [{
            "id": "knowledge",
            "question": "解释期权规则",
            "targets": [{
                "id": "knowledge_target", "domain": "knowledge", "label": "期权规则",
            }],
            "source_intent": "knowledge",
        }],
    })


def _invalid_reference_answer():
    checks = {name: "passed" for name in VALIDATION_CHECKS}
    return ValidatedAnswer21(
        delivery_status="partial",
        body_markdown="规则说明。",
        plain_text="规则说明。",
        limitations=[Limitation(
            code="invalid_reference",
            message="事实引用格式无效。",
            affected_span_ids=["s1"],
        )],
        validation_summary=ValidationSummary(checks=checks),
    )


def test_repair_feedback_carries_limitation_code_and_affected_spans():
    feedback = pydantic_runtime._repair_feedback(_knowledge_plan(), _invalid_reference_answer(), [])

    assert "invalid_reference" in feedback["unresolved_codes"]
    assert feedback["span_issues"] == [{"code": "invalid_reference", "span_ids": ["s1"]}]


def test_delivery_summary_keeps_invalid_reference_limitation_visible():
    validated = delivery_gate.validate_delivery(
        _knowledge_plan(),
        _invalid_reference_answer(),
        [],
        principal=None,
        store_api=None,
    )

    assert "invalid_reference" in validated.validation_summary.unresolved_codes


def test_pydantic_answer_and_repair_prompts_share_reference_grammar():
    execution_prompt = pydantic_runtime._execution_system_prompt()
    repair_prompt = pydantic_runtime._repair_system_prompt()

    assert prompts.EVIDENCE_REFERENCE_GUIDANCE in execution_prompt
    assert prompts.EVIDENCE_REFERENCE_GUIDANCE in repair_prompt
    assert "{{fact:result_uuid#/metrics/name}}" in prompts.EVIDENCE_REFERENCE_GUIDANCE


def test_repair_prompt_names_invalid_span_without_exposing_raw_payload():
    prompt = pydantic_runtime._repair_prompt(_knowledge_plan(), _invalid_reference_answer(), [])

    assert "invalid_reference" in prompt
    assert "span_issues" in prompt
    assert "s1" in prompt
    assert "raw payload" not in prompt.lower()
