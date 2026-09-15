from datetime import datetime, timezone
from uuid import uuid4

from app.trading_agent.answer_contracts import (
    EvidenceItem,
    ValidatedAnswer21,
    ValidationSummary,
    VALIDATION_CHECKS,
)
from app.trading_agent.contracts import MetricValue, ToolEnvelope
from app.trading_agent.coverage import assess_evidence
from app.trading_agent.delivery_gate import validate_delivery
from app.trading_agent.planning_contracts import TaskPlan


def _positions_plan():
    return TaskPlan.model_validate({
        "objective": "读取当前持仓数量",
        "topic_action": "new_topic",
        "requirements": [{
            "id": "positions",
            "question": "读取当前持仓数量",
            "targets": [{
                "id": "positions_target", "domain": "positions", "filters": {},
                "metrics": ["net_quantity"], "label": "当前持仓",
            }],
            "source_intent": "internal",
        }],
    })


def _position_envelope(*, data_as_of=None, payload=None):
    return ToolEnvelope(
        status="complete",
        result_ref=uuid4(),
        captured_at=datetime(2026, 9, 15, 11, 0, tzinfo=timezone.utc),
        data_as_of=data_as_of,
        calculation_version="source-time-repair-test-v1",
        payload={
            "kind": "positions",
            "selection": {"filters": {}},
            **(payload or {}),
        },
        metrics={
            "net_quantity": MetricValue(
                value="3", unit="手", status="complete", covered_rows=1, eligible_rows=1,
            ),
        },
    )


def test_missing_position_source_time_is_unknown_but_quantity_remains_answered():
    report = assess_evidence(_positions_plan(), [_position_envelope()])

    item = report.items[0]
    assert item.status == "answered"
    assert item.time_status == "unknown"
    assert item.result_refs


def test_position_data_as_of_makes_source_time_observed():
    report = assess_evidence(
        _positions_plan(),
        [_position_envelope(data_as_of=datetime(2026, 9, 15, 10, 30, tzinfo=timezone.utc))],
    )

    assert report.items[0].time_status == "observed"


def test_dataset_observation_coverage_makes_source_time_observed():
    plan = TaskPlan.model_validate({
        "objective": "读取发运数量",
        "topic_action": "new_topic",
        "requirements": [{
            "id": "shipping",
            "question": "读取发运数量",
            "targets": [{
                "id": "shipping_target", "domain": "dataset", "filters": {},
                "metrics": ["quantity"], "label": "发运",
            }],
            "source_intent": "internal",
        }],
    })
    envelope = ToolEnvelope(
        status="complete",
        result_ref=uuid4(),
        captured_at=datetime(2026, 9, 15, 11, 0, tzinfo=timezone.utc),
        calculation_version="source-time-repair-test-v1",
        payload={
            "kind": "dataset_rows",
            "coverage": {
                "first_observation": "2026-09-01",
                "last_observation": "2026-09-15",
            },
        },
        metrics={
            "quantity": MetricValue(
                value="80", unit="吨", status="complete", covered_rows=2, eligible_rows=2,
            ),
        },
    )

    assert assess_evidence(plan, [envelope]).items[0].time_status == "observed"


def test_unknown_source_time_blocks_complete_delivery_without_erasing_fact():
    envelope = _position_envelope()
    answer = ValidatedAnswer21(
        delivery_status="complete",
        body_markdown="当前持仓数量见已核验结果。",
        plain_text="当前持仓数量见已核验结果。",
        evidence=[EvidenceItem(
            id="e1", kind="internal", result_ref=str(envelope.result_ref),
            title="持仓结果", fetch_status="internal", captured_at=envelope.captured_at,
        )],
        validation_summary=ValidationSummary(
            checks={name: "passed" for name in VALIDATION_CHECKS},
        ),
    )

    validated = validate_delivery(
        _positions_plan(), answer, [envelope], principal=None, store_api=None,
    )

    assert validated.delivery_status == "partial"
    assert validated.validation_summary.checks["time"] == "failed"
    assert validated.evidence
