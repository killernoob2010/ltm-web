import json
from uuid import uuid4

import pytest

from app import db
from app.trading_agent import quality, store
from app.trading_agent.schema import migrate_agent_v2_schema


@pytest.fixture
def queued(pilot):
    with db.connect() as conn:
        migrate_agent_v2_schema(conn)
    uid, conversation_id = pilot
    task_id = store.enqueue({"id": uid}, conversation_id, str(uuid4()), "查持仓", "web")
    return uid, conversation_id, task_id


def test_quality_summary_separates_runtime_health_from_evaluation_status(queued):
    _, _, _ = queued

    before = quality.build_summary()
    assert before["runtime"]["run_count"] == 1
    assert before["evaluation"]["real_model_evaluated"] is False
    assert before["evaluation"]["release_readiness"] == "not_evaluated"

    task_id = store.claim_next("quality-worker")
    principal = store.principal_for_task(task_id)
    store.append_event(
        principal,
        "research_policy",
        tool_name="public",
        status="research_allowed",
        error_code="mixed_research",
    )
    store.append_event(principal, "tool_call", tool_name="query_positions", status="ok")
    store.record_usage(task_id, model_calls=2, tool_calls=1, search_calls=1)
    assert store.finish(
        task_id,
        "quality-worker",
        "succeeded",
        "完成",
        structured_payload={"schema_version": "2.1", "delivery_status": "complete"},
    )

    summary = quality.build_summary()
    assert summary["runtime"]["state_counts"]["succeeded"] == 1
    assert summary["runtime"]["tool_calls"] == 1
    assert summary["runtime"]["search_calls"] == 1
    assert summary["quality"]["not_evaluated_count"] == 1
    assert summary["evaluation"]["real_model_evaluated"] is False


def test_quality_run_list_is_paged_and_does_not_include_raw_conversation_text(queued):
    _, _, task_id = queued
    result = quality.list_runs(page=1, page_size=10)

    assert result["pagination"]["total"] == 1
    assert result["items"][0]["task_id"] == task_id
    assert "question" not in result["items"][0]
    assert "查持仓" not in json.dumps(result["items"], ensure_ascii=False)


def test_quality_feedback_is_audited_without_mutating_agent_answer(queued):
    uid, _, task_id = queued
    answer_before = store.task_answer(task_id)

    saved = quality.record_feedback(
        evaluator_id=uid,
        task_id=task_id,
        label="needs_review",
        note="需要人工复核公开来源与内部数据的分离展示",
        evaluator_version="quality-ui-v1",
    )

    assert saved["label"] == "needs_review"
    assert saved["evaluator_version"] == "quality-ui-v1"
    assert store.task_answer(task_id) == answer_before
    with db.connect() as conn:
        row = conn.execute(
            "SELECT module_code,entity_type,entity_id,operation_type FROM operation_logs "
            "WHERE entity_type='agent_quality_feedback' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert dict(row) == {
        "module_code": "agent_quality",
        "entity_type": "agent_quality_feedback",
        "entity_id": task_id,
        "operation_type": "Agent质量反馈",
    }


def test_evaluation_catalog_marks_definition_and_offline_suites_without_claiming_live_pass():
    catalog = quality.evaluation_catalog()

    assert catalog["definition"]["regression"]["count"] >= 40
    assert catalog["definition"]["holdout"]["count"] >= 12
    assert catalog["offline_behavior"]["count"] >= 10
    assert catalog["offline_behavior"]["real_model_evaluated"] is False
    assert catalog["live"]["status"] == "not_recorded"
