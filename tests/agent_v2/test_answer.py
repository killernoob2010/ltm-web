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
