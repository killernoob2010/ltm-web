import pytest

from app.trading_agent.answer_v21 import parse_answer21, validate_answer21


def test_blocks_compile_unicode_offsets_and_stable_dependencies():
    draft = parse_answer21({"schema_version": "2.1", "blocks": [
        {"id": "s1", "kind": "knowledge", "text": "中文😀", "refs": []},
        {"id": "s2", "kind": "inference", "text": "解释。", "depends_on": ["s1"]},
    ], "views": []})
    assert draft.body_markdown == "中文😀\n\n解释。"
    assert [(s.start, s.end) for s in draft.spans] == [(0, 3), (5, 8)]
    assert draft.spans[1].depends_on == ["s1"]


def test_blocks_do_not_bypass_evidence_validation():
    result = validate_answer21(None, {"schema_version": "2.1", "blocks": [
        {"id": "s1", "kind": "fact", "text": "持仓为123手。", "refs": []},
        {"id": "s2", "kind": "knowledge", "text": "缺失不等于零。"},
    ], "views": []}, object())
    assert result.delivery_status == "partial"
    assert "123" not in result.body_markdown
    assert "缺失不等于零" in result.body_markdown


@pytest.mark.parametrize("extra", [{"body_markdown": "偷偷覆盖"}, {"spans": []}])
def test_blocks_reject_mixed_protocol(extra):
    with pytest.raises(ValueError):
        parse_answer21({"schema_version": "2.1", "blocks": [{"id": "s1", "kind": "knowledge", "text": "解释"}], "views": [], **extra})


def test_trade_price_survives_public_projection_without_becoming_average():
    from app.trading_agent.facts import _project
    row = _project({"price": 721.5, "average_price": 700, "quantity": 2, "private_key": "hidden"})
    assert row["price"] == 721.5
    assert row["average_price"] == 700
    assert "private_key" not in row


def test_row_price_reference_resolves_but_private_numeric_field_does_not():
    from types import SimpleNamespace
    from app.trading_agent.answer_v21 import _metric, _metric_value
    saved = SimpleNamespace(rows=[{"price": 721.5, "private_key": 12345}])
    assert _metric_value(_metric(saved, "/rows/0/price"))[0] == "721.5"
    assert _metric(saved, "/rows/0/private_key") is None


def test_missing_requested_price_marks_answer_partial_without_dropping_view():
    from types import SimpleNamespace
    from uuid import uuid4
    ref = str(uuid4())
    saved = SimpleNamespace(rows=[{"contract": "i2701", "price": None}], envelope=SimpleNamespace(payload={"kind": "trades"}))
    api = SimpleNamespace(load_result=lambda *args: saved)
    result = validate_answer21(None, {"schema_version": "2.1", "blocks": [], "views": [
        {"id": "v1", "kind": "table", "result_ref": ref, "fields": ["contract", "price"], "title": "成交"}]}, api)
    assert result.delivery_status == "partial"
    assert result.views[0]["fields"] == ["contract", "price"]
    assert any(x.code == "view_data_unavailable" for x in result.limitations)
