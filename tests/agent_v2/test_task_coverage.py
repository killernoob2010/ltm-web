from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.trading_agent.contracts import MetricValue, ToolEnvelope
from app.trading_agent.planning_contracts import CoverageReport, TaskPlan


def _plan(*, needs_full_text=True):
    return TaskPlan.model_validate({
        "objective": "结合内部发运与公开天气",
        "topic_action": "new_topic",
        "requirements": [
            {
                "id": "shipping",
                "question": "核对内部发运",
                "targets": [{
                    "id": "shipping_target", "domain": "dataset", "filters": {},
                    "metrics": ["quantity"], "group_by": ["business_date"],
                    "label": "内部发运",
                }],
                "source_intent": "internal",
            },
            {
                "id": "weather",
                "question": "核对公开天气",
                "targets": [{
                    "id": "weather_target", "domain": "public", "filters": {},
                    "metrics": [], "group_by": [], "label": "公开天气",
                }],
                "source_intent": "public",
                "needs_full_text": needs_full_text,
            },
        ],
    })


def _envelope(kind, *, status="complete", payload=None, metrics=None):
    return ToolEnvelope(
        status=status,
        result_ref=uuid4(),
        captured_at=datetime.now(timezone.utc),
        calculation_version="coverage-test-v1",
        payload={"kind": kind, **(payload or {})},
        metrics=metrics or {},
    )


def test_assess_evidence_requires_public_full_text_and_tracks_each_requirement():
    from app.trading_agent.coverage import assess_evidence

    internal = _envelope(
        "dataset_rows",
        payload={"selection": {"dataset": "shipping"}},
        metrics={"quantity": MetricValue(value="80", unit="吨", status="complete", covered_rows=2, eligible_rows=2)},
    )
    search = _envelope("research", payload={"sources": [{"source_ref": "source-1"}]})
    read = _envelope("public_read", payload={"source_ref": "source-1", "text": "本周港口有暴雨。"})

    report = assess_evidence(_plan(), [internal, search, read])

    assert report.complete is True
    items = {item.requirement_id: item for item in report.items}
    assert items["shipping"].status == "answered"
    assert items["weather"].status == "answered"
    assert str(internal.result_ref) in items["shipping"].result_refs
    assert str(read.result_ref) in items["weather"].result_refs


def test_assess_evidence_distinguishes_snippet_only_from_full_text():
    from app.trading_agent.coverage import assess_evidence

    report = assess_evidence(
        _plan(needs_full_text=True),
        [
            _envelope("dataset_rows", metrics={"quantity": MetricValue(value="80", unit="吨", status="complete", covered_rows=2, eligible_rows=2)}),
            _envelope("research", payload={"sources": [{"source_ref": "source-1"}]}),
        ],
    )

    weather = next(item for item in report.items if item.requirement_id == "weather")
    assert weather.status == "partial"
    assert "full_text_required" in weather.missing_codes
    assert report.complete is False


def test_assess_delivery_marks_unreferenced_requirement_without_erasing_other_evidence():
    from app.trading_agent.coverage import assess_delivery

    plan = _plan()
    evidence = _envelope("dataset_rows", metrics={"quantity": MetricValue(value="80", unit="吨", status="complete", covered_rows=2, eligible_rows=2)})
    report = CoverageReport.model_validate({
        "items": [{
            "requirement_id": "shipping", "status": "answered",
            "result_refs": [str(evidence.result_ref)], "time_status": "observed",
        }, {
            "requirement_id": "weather", "status": "answered", "result_refs": [str(uuid4())],
            "time_status": "observed",
        }],
        "complete": True,
    })
    answer = SimpleNamespace(
        delivery_status="complete",
        evidence=[SimpleNamespace(result_ref=str(evidence.result_ref))],
        views=[],
    )

    delivered = assess_delivery(plan, report, answer)

    weather = next(item for item in delivered.items if item.requirement_id == "weather")
    assert delivered.complete is False
    assert weather.status == "partial"
    assert "answer_reference_missing" in weather.delivery_blocks
    shipping = next(item for item in delivered.items if item.requirement_id == "shipping")
    assert shipping.status == "answered"


def test_coverage_report_rejects_complete_when_a_requirement_is_partial():
    with pytest.raises(ValueError):
        CoverageReport.model_validate({
            "items": [{"requirement_id": "r1", "status": "partial"}],
            "complete": True,
        })


def test_mixed_requirement_requires_both_internal_and_public_evidence():
    from app.trading_agent.coverage import assess_evidence

    plan = TaskPlan.model_validate({
        "objective": "内部发运与公开天气联合分析",
        "topic_action": "new_topic",
        "requirements": [{
            "id": "mixed",
            "question": "结合内部发运和公开天气",
            "targets": [
                {"id": "shipping", "domain": "dataset", "metrics": ["quantity"], "label": "内部发运"},
                {"id": "weather", "domain": "public", "metrics": [], "label": "公开天气"},
            ],
            "source_intent": "both",
        }],
    })
    internal = _envelope(
        "dataset_rows",
        metrics={"quantity": MetricValue(value="80", unit="吨", status="complete", covered_rows=2, eligible_rows=2)},
    )

    report = assess_evidence(plan, [internal])

    item = report.items[0]
    assert item.status == "partial"
    assert "target.weather.result_missing" in item.missing_codes
    assert report.complete is False


def test_evidence_scope_mismatch_is_not_marked_as_answered():
    from app.trading_agent.coverage import assess_evidence

    plan = TaskPlan.model_validate({
        "objective": "核对日照库存",
        "topic_action": "new_topic",
        "requirements": [{
            "id": "inventory",
            "question": "核对日照港库存",
            "targets": [{
                "id": "inventory_target", "domain": "dataset",
                "filters": {"dataset": "inventory_summary", "ports": ["日照"]},
                "metrics": ["value"], "label": "日照库存",
            }],
            "source_intent": "internal",
        }],
    })
    envelope = _envelope(
        "dataset_rows",
        payload={"selection": {"dataset": "inventory_summary", "filters": {"ports": ["青岛"]}}},
        metrics={"value": MetricValue(value="80", unit="万吨", status="complete", covered_rows=2, eligible_rows=2)},
    )

    report = assess_evidence(plan, [envelope])

    item = report.items[0]
    assert item.status == "partial"
    assert "target.inventory_target.scope_mismatch" in item.missing_codes
    assert item.actual_scope["targets"]["inventory_target"]["filter_status"] == "mismatch"
