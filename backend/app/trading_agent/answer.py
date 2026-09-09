"""Validate final model JSON and resolve only authorized evidence placeholders."""
import re
from typing import Any
from uuid import UUID

from .contracts import AnswerDraft


class InvalidEvidence(ValueError):
    pass


_FACT_REF = re.compile(
    r"\{\{fact:([0-9a-fA-F-]{36})#("
    r"/metrics/[A-Za-z][A-Za-z0-9_]*"
    r"|/payload/groups/[0-9]{1,3}/metrics/[A-Za-z][A-Za-z0-9_]*"
    r")\}\}"
)
_DATE_TOKEN = re.compile(r"(?<![A-Za-z0-9])(?:19|20)\d{2}(?:[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?)?(?![A-Za-z0-9])")
_BUSINESS_NUMBER = re.compile(
    r"(?<![A-Za-z])(?:[-+]?\d{2,}(?:\.\d+)?|[-+]?\d+\.\d+|[-+]?\d+(?=\s*(?:手|笔|张|合约|元|万元|CNY|%|点)))"
    r"\s*(?:手|笔|张|合约|元|万元|CNY|%|点)?(?![A-Za-z])",
    re.I,
)


def parse_answer(raw: str | dict[str, Any]) -> AnswerDraft:
    try:
        draft = AnswerDraft.model_validate_json(raw) if isinstance(raw, str) else AnswerDraft.model_validate(raw)
    except Exception as exc:
        raise ValueError("模型答案不是合法 AnswerDraft") from exc
    if len(draft.paragraphs) > 20:
        raise ValueError("答案段落过多")
    if any(len(paragraph.text) > 8000 for paragraph in draft.paragraphs) or sum(len(paragraph.text) for paragraph in draft.paragraphs) > 30000:
        raise ValueError("答案内容过长")
    if len(draft.fact_refs) > 100:
        raise ValueError("事实引用过多")
    return draft


def _resolve(text: str, principal, store):
    def replace(match):
        ref, metric_path = match.groups()
        try:
            saved = store.load_result(principal, UUID(ref))
        except Exception as exc:
            raise InvalidEvidence("事实引用不存在、越权或已过期") from exc
        if metric_path.startswith("/metrics/"):
            metric_name = metric_path.rsplit("/", 1)[-1]
            metric = saved.envelope.metrics.get(metric_name)
        else:
            parts = metric_path.strip("/").split("/")
            groups = saved.envelope.payload.get("groups")
            try:
                group_index = int(parts[2])
                metric_name = parts[4]
                metric = groups[group_index]["metrics"].get(metric_name)
            except (AttributeError, IndexError, KeyError, TypeError, ValueError):
                metric = None
            if isinstance(metric, dict):
                try:
                    from .contracts import MetricValue

                    metric = MetricValue.model_validate(metric)
                except Exception:
                    metric = None
        if metric is None or metric.value is None or metric.status not in {"complete", "partial"}:
            raise InvalidEvidence("事实引用指标不可用")
        return f"{metric.value} {metric.unit}"
    return _FACT_REF.sub(replace, text)


def _has_unreferenced_number(text: str) -> bool:
    without_refs = _FACT_REF.sub(" ", text)
    without_dates = _DATE_TOKEN.sub(" ", without_refs)
    return bool(_BUSINESS_NUMBER.search(without_dates))


def render_answer(principal, draft: AnswerDraft | str | dict[str, Any], store) -> str:
    draft = parse_answer(draft)
    rendered = []
    known_refs = set()
    for paragraph in draft.paragraphs:
        if paragraph.kind in {"fact", "scenario"} and _has_unreferenced_number(paragraph.text):
            raise InvalidEvidence("事实或情景段落中的业务数字必须引用已登记结果")
        text = _resolve(paragraph.text, principal, store)
        for ref in paragraph.evidence_refs + draft.fact_refs:
            if ref.startswith("http://") or ref.startswith("https://"):
                continue
            if "#/" not in ref:
                raise InvalidEvidence("证据引用格式无效")
            try:
                result_ref = UUID(ref.split("#/", 1)[0])
                store.load_result(principal, result_ref)
            except Exception as exc:
                raise InvalidEvidence("证据引用不存在、越权或已过期") from exc
            known_refs.add(ref)
        rendered.append(text)
    if draft.clarification:
        rendered.append(draft.clarification)
    if not rendered:
        raise ValueError("答案不能为空")
    return "\n\n".join(rendered)
