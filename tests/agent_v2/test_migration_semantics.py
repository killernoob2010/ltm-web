from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.trading_agent import store
from app.trading_agent.contracts import MetricValue, ToolEnvelope
from app.trading_agent.dv_analysis import rank_comparison_rows
from app.trading_agent.planning_contracts import AnalysisSpec, CoverageReport, TaskPlan
from app.trading_agent.planner import PlannerError, validate_plan
from test_store import queued


def _dataset_plan(*, metric="value", analysis=None):
    requirement = {
        "id": "inventory",
        "question": "查询库存",
        "targets": [{
            "id": "inventory_target", "domain": "dataset",
            "filters": {"dataset": "inventory_summary"},
            "metrics": [metric], "group_by": ["port"], "label": "库存",
        }],
        "source_intent": "internal",
    }
    if analysis is not None:
        requirement["analysis"] = analysis
    return TaskPlan.model_validate({
        "objective": "核对库存",
        "topic_action": "new_topic",
        "requirements": [requirement],
    })


def test_planner_rejects_incomplete_compare_and_rank_contracts():
    with pytest.raises(PlannerError, match="comparison_basis"):
        validate_plan(
            _dataset_plan(analysis={
                "operation": "compare", "metric": "value", "comparison_basis": "none",
            }),
            {"tools": [], "conditional_sources": []},
        )
    with pytest.raises(PlannerError, match="ranking_measure"):
        validate_plan(
            _dataset_plan(analysis={
                "operation": "rank", "metric": "value", "comparison_basis": "none",
            }),
            {"tools": [], "conditional_sources": []},
        )
    with pytest.raises(PlannerError, match="conclusion"):
        validate_plan(
            _dataset_plan(analysis={
                "operation": "rank", "metric": "value", "comparison_basis": "none",
                "ranking_measure": "delta", "conclusion_required": False,
            }),
            {"tools": [], "conditional_sources": []},
        )


def test_validate_plan_rejects_metric_not_registered_by_dataset():
    plan = _dataset_plan(metric="secret_metric")

    with pytest.raises(PlannerError, match="指标"):
        validate_plan(plan, {"tools": [], "conditional_sources": []})


def test_validate_plan_accepts_rank_with_explicit_conclusion_requirement():
    plan = _dataset_plan(analysis={
        "operation": "rank", "metric": "value", "comparison_basis": "none",
        "ranking_measure": "delta", "conclusion_required": True,
    })
    assert validate_plan(plan, {"tools": [], "conditional_sources": []}) == plan


def test_delta_ranking_uses_existing_field_contract_and_full_rows():
    rows = [
        {"port": "A", "delta": "-10", "delta_pct": "-10", "comparison_status": "complete", "row_ref": "2"},
        {"port": "B", "delta": "-40", "delta_pct": "-20", "comparison_status": "complete", "row_ref": "1"},
        {"port": "C", "delta": "5", "delta_pct": "10", "comparison_status": "complete", "row_ref": "3"},
        {"port": "D", "delta": None, "delta_pct": None, "comparison_status": "missing_period", "row_ref": "4"},
    ]

    ranked = rank_comparison_rows(rows, measure="pct_change", descending=False, top_k=2)

    assert [row["port"] for row in ranked] == ["B", "A"]
    assert all("pct_change" not in row for row in ranked)


def test_rank_ties_use_business_key_before_row_reference():
    rows = [
        {"port": "B", "delta": "-10", "comparison_status": "complete", "row_ref": "1"},
        {"port": "A", "delta": "-10", "comparison_status": "complete", "row_ref": "2"},
    ]

    ranked = rank_comparison_rows(rows, measure="delta", descending=False)

    assert [row["port"] for row in ranked] == ["A", "B"]


def test_coverage_report_must_bind_exactly_to_plan_requirements():
    plan = _dataset_plan()
    report = CoverageReport(items=[{"requirement_id": "other", "status": "partial"}])

    from app.trading_agent.coverage import validate_coverage_report

    with pytest.raises(ValueError, match="需求 id"):
        validate_coverage_report(plan, report)

    with pytest.raises(ValidationError):
        CoverageReport(items=[], complete=True)


def test_coverage_without_result_reference_is_not_answered():
    from app.trading_agent.coverage import assess_evidence

    envelope = ToolEnvelope(
        status="complete", captured_at=datetime.now(timezone.utc),
        calculation_version="coverage-test-v1",
        payload={"kind": "dataset_summary", "dataset": "inventory_summary", "measure": "value"},
        metrics={"value": MetricValue(value="1", unit="吨", status="complete", covered_rows=1, eligible_rows=1)},
    )

    item = assess_evidence(_dataset_plan(), [envelope]).items[0]

    assert item.status == "partial"
    assert "target.inventory_target.result_ref_missing" in item.missing_codes


def test_coverage_does_not_accept_nonempty_result_with_wrong_dataset_or_period():
    from app.trading_agent.coverage import assess_evidence

    plan = TaskPlan.model_validate({
        "objective": "核对本周库存",
        "topic_action": "new_topic",
        "requirements": [{
            "id": "inventory", "question": "核对本周库存", "source_intent": "internal",
            "time_window": {
                "start_date": "2026-09-01", "end_date": "2026-09-07", "origin": "user",
            },
            "targets": [{
                "id": "inventory_target", "domain": "dataset",
                "filters": {"dataset": "inventory_summary"}, "metrics": ["value"],
                "group_by": ["port"], "label": "库存",
            }],
        }],
    })
    envelope = ToolEnvelope(
        status="complete", result_ref=uuid4(), captured_at=datetime.now(timezone.utc),
        calculation_version="coverage-test-v1",
        payload={
            "kind": "dataset_summary", "dataset": "other_dataset", "measure": "value",
            "periods": {"start": "2026-08-01", "end": "2026-08-07"},
            "preview": [{"port": "A", "value": "1"}],
        },
    )

    item = assess_evidence(plan, [envelope]).items[0]

    assert item.status == "partial"
    assert "target.inventory_target.scope_mismatch" in item.missing_codes


def test_coverage_does_not_treat_missing_list_filter_metadata_as_all():
    from app.trading_agent.coverage import assess_evidence

    plan = TaskPlan.model_validate({
        "objective": "核对指定港口库存",
        "topic_action": "new_topic",
        "requirements": [{
            "id": "inventory", "question": "核对指定港口库存", "source_intent": "internal",
            "targets": [{
                "id": "inventory_target", "domain": "dataset",
                "filters": {"dataset": "inventory_summary", "ports": ["日照"]},
                "metrics": ["value"], "label": "指定港口库存",
            }],
        }],
    })
    envelope = ToolEnvelope(
        status="complete", result_ref=uuid4(), captured_at=datetime.now(timezone.utc),
        calculation_version="coverage-test-v1",
        payload={"kind": "dataset_summary", "dataset": "inventory_summary", "measure": "value"},
        metrics={"value": MetricValue(value="1", unit="吨", status="complete", covered_rows=1, eligible_rows=1)},
    )

    item = assess_evidence(plan, [envelope]).items[0]

    assert item.status == "partial"
    assert "target.inventory_target.scope_mismatch" in item.missing_codes


def test_complete_coverage_cannot_contain_delivery_blocks():
    with pytest.raises(ValidationError):
        CoverageReport.model_validate({
            "items": [{
                "requirement_id": "inventory", "status": "answered",
                "delivery_blocks": ["answer_reference_missing"],
            }],
            "complete": True,
        })


def test_followup_history_includes_actual_answer_only_while_evidence_is_reusable(queued):
    user_id, conversation_id, _ = queued
    task = store.claim_next("history-body")
    principal = store.principal_for_task(task)
    result_ref = store.save_result(
        principal,
        ToolEnvelope(
            status="complete", result_ref=uuid4(),
            captured_at=datetime.now(timezone.utc), calculation_version="test",
            payload={"kind": "positions"},
        ),
        [], kind="positions",
    )
    store.finish(
        task, "history-body", "succeeded", "上次实际交付：净买 Call 为 2 手。",
        structured_payload={
            "schema_version": "2.1", "delivery_status": "complete",
            "evidence": [{"kind": "internal", "result_ref": str(result_ref)}],
            "views": [],
        },
    )
    followup = store.enqueue({"id": user_id}, conversation_id, str(uuid4()), "继续解释", "web")

    assert "上次实际交付：净买 Call 为 2 手。" in store.task_history(followup, user_id)[-1]["content"]

    with __import__("app").db.connect() as conn:
        conn.execute(
            "UPDATE agent_v2_results SET expires_at=? WHERE id=?",
            ("2000-01-01T00:00:00+00:00", str(result_ref)),
        )
    assert "上次实际交付：净买 Call 为 2 手。" not in store.task_history(followup, user_id)[-1]["content"]


def test_followup_history_checks_view_only_evidence_before_reusing_body(queued):
    user_id, conversation_id, _ = queued
    task = store.claim_next("history-view-body")
    principal = store.principal_for_task(task)
    result_ref = store.save_result(
        principal,
        ToolEnvelope(
            status="complete", result_ref=uuid4(),
            captured_at=datetime.now(timezone.utc), calculation_version="test",
            payload={"kind": "positions"},
        ),
        [], kind="positions",
    )
    store.finish(
        task, "history-view-body", "succeeded", "仅由视图引用的实际交付正文。",
        structured_payload={
            "schema_version": "2.1", "delivery_status": "complete",
            "evidence": [], "views": [{"id": "v1", "result_ref": str(result_ref)}],
        },
    )
    followup = store.enqueue({"id": user_id}, conversation_id, str(uuid4()), "继续解释", "web")

    assert "仅由视图引用的实际交付正文。" in store.task_history(followup, user_id)[-1]["content"]

    with __import__("app").db.connect() as conn:
        conn.execute(
            "UPDATE agent_v2_results SET expires_at=? WHERE id=?",
            ("2000-01-01T00:00:00+00:00", str(result_ref)),
        )
    assert "仅由视图引用的实际交付正文。" not in store.task_history(followup, user_id)[-1]["content"]
