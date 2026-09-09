from datetime import datetime, timezone
from uuid import uuid4
import pytest

from app.trading_agent import answer, store
from app.trading_agent.contracts import AnswerDraft, ToolEnvelope, MetricValue
from test_store import queued


def evidence(queued):
    task = store.claim_next("answer-worker")
    principal = store.principal_for_task(task)
    envelope = ToolEnvelope(status="complete", captured_at=datetime.now(timezone.utc), calculation_version="t",
        metrics={"floating_pnl": MetricValue(value="123.45", unit="CNY", status="complete", covered_rows=1, eligible_rows=1)}, payload={"kind":"positions"})
    return principal, store.save_result(principal, envelope, [])


def test_fact_placeholder_is_resolved_from_authorized_snapshot(queued):
    principal, ref = evidence(queued)
    draft = AnswerDraft(status="complete", paragraphs=[{"kind":"fact","text":f"浮盈为 {{{{fact:{ref}#/metrics/floating_pnl}}}}","evidence_refs":[f"{ref}#/metrics/floating_pnl"]}])
    assert "123.45 CNY" in answer.render_answer(principal, draft, store)


def test_answer_rejects_foreign_fact_reference(queued):
    principal, _ = evidence(queued)
    with pytest.raises(answer.InvalidEvidence):
        answer.render_answer(principal, {"status":"complete","paragraphs":[{"kind":"fact","text":f"浮盈 {{{{fact:{uuid4()}#/metrics/floating_pnl}}}}"}]}, store)


def test_answer_does_not_require_internal_evidence_for_knowledge(queued):
    principal, _ = evidence(queued)
    rendered = answer.render_answer(principal, {"status":"complete","paragraphs":[{"kind":"knowledge","text":"期权风险取决于价格、波动率和到期时间。"}]}, store)
    assert "期权风险" in rendered


def test_fact_numbers_must_use_registered_metric_placeholder(queued):
    principal, _ = evidence(queued)
    draft = AnswerDraft(status="complete", paragraphs=[{"kind":"fact", "text":"当前浮盈为 123.45 元"}])
    with pytest.raises(answer.InvalidEvidence):
        answer.render_answer(principal, draft, store)


def test_answer_resolves_group_risk_metric_path(queued):
    task = store.claim_next("answer-worker")
    principal = store.principal_for_task(task)
    envelope = ToolEnvelope(
        status="complete", captured_at=datetime.now(timezone.utc), calculation_version="risk",
        payload={"kind": "risk", "groups": [{"underlying_symbol": "DCE.i2609", "metrics": {
            "delta_exposure": {"value": "125", "unit": "CNY/标的价格单位", "status": "complete",
                                "covered_rows": 1, "eligible_rows": 1},
        }}]},
    )
    ref = store.save_result(principal, envelope, [])
    draft = {"status": "complete", "paragraphs": [{"kind": "fact",
        "text": "该标的 Delta 敞口为 {{fact:%s#/payload/groups/0/metrics/delta_exposure}}。" % ref,
        "evidence_refs": []}], "fact_refs": []}
    assert "125 CNY/标的价格单位" in answer.render_answer(principal, draft, store)


def test_dated_timestamps_are_not_mistaken_for_unreferenced_position_numbers():
    from app.trading_agent.answer import _has_unreferenced_number
    for timestamp in ('2026年9月9日17:52', '2026-09-09T17:52:00+08:00', '2026-09-09 17:52:00'):
        assert not _has_unreferenced_number(f'数据时点：{timestamp}。')
        assert _has_unreferenced_number(f'数据时点：{timestamp}，持仓合计2手。')


def test_extra_field_has_safe_exact_location():
    raw = {"status": "complete", "paragraphs": [
        {"kind": "knowledge", "text": "风险取决于数据和假设。"}
    ], "evidence_refs": []}
    with pytest.raises(answer.AnswerValidationError) as caught:
        answer.parse_answer(raw)
    issue = caught.value.issues[0]
    assert issue.code == "extra_field"
    assert issue.path == "/evidence_refs"
    assert "对应段落" in issue.message


@pytest.mark.parametrize(("raw", "code"), [
    ({"status": "complete"}, "missing_field"),
    ({"status": "complete", "paragraphs": "bad"}, "invalid_type"),
    ({"status": "not-a-status", "paragraphs": []}, "invalid_value"),
    ({"status": "complete", "paragraphs": []}, "answer_empty"),
])
def test_answer_validation_uses_safe_issue_categories(raw, code):
    with pytest.raises(answer.AnswerValidationError) as caught:
        answer.parse_answer(raw)
    assert caught.value.issues[0].code == code


@pytest.mark.parametrize("raw", [
    (chr(96) * 3) + 'json\n{"status":"complete","paragraphs":[]}\n' + (chr(96) * 3),
    '{"status":"complete","paragraphs":[],"x":NaN}',
    '{"status":"complete","status":"partial","paragraphs":[]}',
])
def test_answer_json_errors_do_not_expose_input(raw):
    with pytest.raises(answer.AnswerValidationError) as caught:
        answer.parse_answer(raw)
    assert caught.value.issues[0].code in {"invalid_json", "duplicate_field"}
    assert "SECRET_CANARY_123" not in str(caught.value)


def test_answer_validation_does_not_echo_unknown_sensitive_field():
    raw = {
        "status": "complete",
        "paragraphs": [{"kind": "knowledge", "text": "说明"}],
        "SECRET_CANARY_123": "should never be echoed",
    }
    with pytest.raises(answer.AnswerValidationError) as caught:
        answer.parse_answer(raw)
    assert "SECRET_CANARY_123" not in str(caught.value)
    assert "SECRET_CANARY_123" not in repr(caught.value.as_dicts())


def test_answer_rejects_oversized_raw_json():
    with pytest.raises(answer.AnswerValidationError) as caught:
        answer.parse_answer('{"status":"complete","paragraphs":[{"kind":"knowledge","text":"' + ("x" * 48000) + '"}]}')
    assert caught.value.issues[0].code == "answer_too_large"


def test_answer_rejects_missing_metric_and_malformed_placeholder(queued):
    principal, ref = evidence(queued)
    with pytest.raises(answer.InvalidEvidence) as missing:
        answer.render_answer(principal, {
            "status": "complete",
            "paragraphs": [{"kind": "fact", "text": f"{{{{fact:{ref}#/metrics/quantity}}}}"}],
        }, store)
    assert missing.value.issues[0].code == "metric_unavailable"
    with pytest.raises(answer.InvalidEvidence) as malformed:
        answer.render_answer(principal, {
            "status": "complete",
            "paragraphs": [{"kind": "knowledge", "text": "{{fact:not-a-uuid#/metrics/quantity}}"}],
        }, store)
    assert malformed.value.issues[0].code == "invalid_reference"
