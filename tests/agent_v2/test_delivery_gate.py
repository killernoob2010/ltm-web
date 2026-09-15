from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.trading_agent import delivery_gate
from app.trading_agent.answer_contracts import AnswerDraft21, EvidenceSpan, ViewRequest
from app.trading_agent.contracts import MetricValue, ToolEnvelope
from app.trading_agent.planning_contracts import AnalysisSpec, AnalysisTarget, Requirement, TaskPlan


def _plan(*, analysis=None, presentation="auto"):
    return TaskPlan(
        objective="读取受控结果并完成回答",
        topic_action="new_topic",
        requirements=[Requirement(
            id="positions",
            question="读取持仓结果",
            targets=[AnalysisTarget(
                id="positions_target",
                domain="positions",
                filters={},
                metrics=["quantity"],
                group_by=[],
                label="持仓",
            )],
            source_intent="internal",
            time_requirement="latest",
            analysis=analysis,
        )],
        presentation=presentation,
    )


def _evidence_store():
    ref = uuid4()
    envelope = ToolEnvelope(
        status="complete",
        result_ref=ref,
        captured_at=datetime.now(timezone.utc),
        data_as_of=datetime.now(timezone.utc),
        calculation_version="test-v1",
        payload={"kind": "positions", "selection": {"filters": {}}},
        metrics={"quantity": MetricValue(value="3", unit="手", status="complete", covered_rows=1, eligible_rows=1)},
    )
    saved = SimpleNamespace(envelope=envelope, rows=[{"quantity": "3"}])
    return ref, envelope, SimpleNamespace(load_result=lambda principal, requested: saved)


def test_delivery_gate_attaches_all_quality_checks_to_a_validated_answer():
    ref, envelope, store_api = _evidence_store()
    token = f"{{{{fact:{ref}#/metrics/quantity}}}}"
    draft = AnswerDraft21(
        schema_version="2.1",
        body_markdown=f"当前持仓见核验结果 {token}。",
        spans=[EvidenceSpan(id="s1", kind="fact", start=0, end=len(f"当前持仓见核验结果 {token}。"), refs=[f"{ref}#/metrics/quantity"])],
    )

    result = delivery_gate.validate_delivery(
        _plan(), draft, [envelope], principal=SimpleNamespace(user_id=1, conversation_id=1), store_api=store_api,
    )

    assert result.delivery_status == "complete"
    assert result.validation_summary.version == "migration-v1"
    assert set(result.validation_summary.checks) == {
        "scope", "time", "metrics", "evidence", "analysis", "presentation", "rendered_content",
    }
    assert all(value in {"passed", "not_applicable"} for value in result.validation_summary.checks.values())


def test_delivery_gate_rejects_ack_only_body_and_missing_rank_conclusion():
    ref, envelope, store_api = _evidence_store()
    rank = AnalysisSpec(
        operation="rank", metric="quantity", comparison_basis="none", ranking_measure="value",
        descending=True, top_k=3, conclusion_required=True,
    )
    draft = AnswerDraft21(
        schema_version="2.1",
        body_markdown="行。",
        spans=[EvidenceSpan(id="s1", kind="fact", start=0, end=2, refs=[f"{ref}#/metrics/quantity"])],
    )

    result = delivery_gate.validate_delivery(
        _plan(analysis=rank), draft, [envelope], principal=SimpleNamespace(user_id=1, conversation_id=1), store_api=store_api,
    )

    assert result.delivery_status != "complete"
    assert result.validation_summary.checks["analysis"] == "failed"
    assert result.validation_summary.checks["rendered_content"] == "failed"


def test_delivery_gate_marks_text_preference_with_view_as_presentation_failure():
    ref, envelope, store_api = _evidence_store()
    view = ViewRequest(id="v1", kind="table", result_ref=ref, fields=["quantity"], title="持仓")
    draft = AnswerDraft21(
        schema_version="2.1",
        body_markdown="请看表格。{{view:v1}}",
        spans=[EvidenceSpan(id="s1", kind="knowledge", start=0, end=5, refs=[])],
        views=[view],
    )

    result = delivery_gate.validate_delivery(
        _plan(presentation="text"), draft, [envelope], presentation_preference="text",
        principal=SimpleNamespace(user_id=1, conversation_id=1), store_api=store_api,
    )

    assert result.validation_summary.checks["presentation"] == "failed"
    assert result.delivery_status == "partial"
