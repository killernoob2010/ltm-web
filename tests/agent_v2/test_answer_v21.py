from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest


def test_answer21_parser_is_exposed():
    from app.trading_agent import answer

    assert hasattr(answer, "parse_answer21")


def test_parse_rejects_duplicate_keys():
    from app.trading_agent.answer_v21 import parse_answer21

    with pytest.raises(ValueError):
        parse_answer21('{"schema_version":"2.1","schema_version":"2.0"}')


def test_parse_rejects_oversized_and_invalid_json_values():
    with pytest.raises(ValueError) as oversized:
        from app.trading_agent.answer_v21 import parse_answer21

        parse_answer21({"schema_version": "2.1", "body_markdown": "中" * 24000, "spans": [], "views": []})
    assert oversized.value.issues[0].code == "answer_too_large"

    from app.trading_agent.answer_v21 import parse_answer21

    with pytest.raises(ValueError) as invalid_unicode:
        parse_answer21('{"schema_version":"2.1","body_markdown":"\\ud800","spans":[],"views":[]}')
    assert invalid_unicode.value.issues[0].code == "invalid_unicode"

    with pytest.raises(ValueError) as invalid_number:
        parse_answer21('{"schema_version":"2.1","body_markdown":"说明","spans":[],"views":[],"x":NaN}')
    assert invalid_number.value.issues[0].code == "invalid_json"


def test_parse_keeps_free_markdown():
    from app.trading_agent.answer_v21 import parse_answer21

    text = "## 说明\n\n这是一般性解释。"
    draft = parse_answer21(
        {
            "schema_version": "2.1",
            "body_markdown": text,
            "spans": [
                {
                    "id": "s1",
                    "kind": "knowledge",
                    "start": 7,
                    "end": len(text),
                    "refs": [],
                    "depends_on": [],
                }
            ],
            "views": [],
        }
    )

    assert draft.body_markdown == text


def test_parse_rejects_overlapping_spans_and_dependency_cycles():
    from app.trading_agent.answer_v21 import parse_answer21

    base = {
        "schema_version": "2.1",
        "body_markdown": "abcdef",
        "views": [],
    }
    with pytest.raises(ValueError):
        parse_answer21(
            {
                **base,
                "spans": [
                    {"id": "s1", "kind": "knowledge", "start": 0, "end": 4, "refs": [], "depends_on": []},
                    {"id": "s2", "kind": "knowledge", "start": 3, "end": 6, "refs": [], "depends_on": []},
                ],
            }
        )
    with pytest.raises(ValueError):
        parse_answer21(
            {
                **base,
                "spans": [
                    {"id": "s1", "kind": "inference", "start": 0, "end": 2, "refs": [], "depends_on": ["s2"]},
                    {"id": "s2", "kind": "inference", "start": 2, "end": 4, "refs": [], "depends_on": ["s1"]},
                ],
            }
        )


def test_validate_drops_invalid_fact_and_dependent_inference_but_keeps_empty_table():
    from app.trading_agent.answer_v21 import validate_answer21

    valid_ref = uuid4()
    invalid_ref = uuid4()
    table_ref = uuid4()
    body = "事实失效。\n\n因此推断也失效。\n\n{{view:v1}}"
    store_api = SimpleNamespace(
        load_result=lambda principal, ref, **kwargs: {
            str(valid_ref): SimpleNamespace(
                ref=valid_ref,
                envelope=SimpleNamespace(
                    metrics={"quantity": {"value": "2", "unit": "手", "status": "complete", "covered_rows": 1, "eligible_rows": 1}},
                    payload={"kind": "positions"},
                    captured_at=datetime.now(timezone.utc),
                    data_as_of=None,
                    calculation_version="test",
                ),
                rows=[],
                parent_ref=None,
            ),
            str(table_ref): SimpleNamespace(
                ref=table_ref,
                envelope=SimpleNamespace(
                    metrics={},
                    payload={"kind": "positions"},
                    captured_at=datetime.now(timezone.utc),
                    data_as_of=None,
                    calculation_version="test",
                ),
                rows=[],
                parent_ref=None,
            ),
        }[str(UUID(str(ref)))],
    )
    spans = [
        {"id": "s1", "kind": "fact", "start": 0, "end": 5, "refs": [f"{invalid_ref}#/metrics/quantity"], "depends_on": []},
        {"id": "s2", "kind": "inference", "start": 7, "end": 17, "refs": [], "depends_on": ["s1"]},
        {"id": "s3", "kind": "knowledge", "start": 17, "end": 28, "refs": [], "depends_on": []},
    ]
    draft = {
        "schema_version": "2.1",
        "body_markdown": body,
        "spans": spans,
        "views": [
            {
                "id": "v1",
                "kind": "table",
                "result_ref": str(table_ref),
                "fields": [],
                "title": "空结果",
            }
        ],
    }

    result = validate_answer21(SimpleNamespace(), draft, store_api)

    assert result.delivery_status == "partial"
    assert "事实失效" not in result.body_markdown
    assert "因此推断" not in result.body_markdown
    assert "{{view:v1}}" in result.body_markdown
    assert result.views[0]["id"] == "v1"
    assert any(item.code == "reference_unavailable" for item in result.limitations)


def test_validate_does_not_deliver_uncovered_business_number():
    from app.trading_agent.answer_v21 import validate_answer21

    result = validate_answer21(
        SimpleNamespace(),
        {
            "schema_version": "2.1",
            "body_markdown": "当前持仓为 12 手。",
            "spans": [],
            "views": [],
        },
        SimpleNamespace(load_result=lambda *args, **kwargs: None),
    )

    assert result.delivery_status == "failed"
    assert "12 手" not in result.body_markdown
    assert any(item.code == "uncovered_claim" for item in result.limitations)


def test_task_state_is_separate_from_data_status():
    from app.trading_agent.answer_v21 import task_state21

    assert task_state21("complete") == "succeeded"
    assert task_state21("partial") == "partial"
    assert task_state21("failed") == "failed"
    with pytest.raises(ValueError):
        task_state21("waiting_for_data")
