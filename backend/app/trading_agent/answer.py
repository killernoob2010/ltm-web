"""Validate final model JSON and resolve only authorized evidence placeholders."""
import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError

from .contracts import AnswerDraft
from .store import ResultExpired


@dataclass(frozen=True)
class AnswerIssue:
    code: str
    path: str
    message: str


class AnswerValidationError(ValueError):
    def __init__(self, issues: list[AnswerIssue]):
        self.issues = tuple(issues[:5])
        super().__init__("答案格式或证据未通过校验")

    def as_dicts(self) -> list[dict[str, str]]:
        return [{"code": issue.code, "path": issue.path, "message": issue.message}
                for issue in self.issues]


class InvalidEvidence(AnswerValidationError):
    def __init__(self, message: str, *, code="invalid_evidence", path="/"):
        super().__init__([AnswerIssue(code, path, message)])


class _DuplicateJSONKey(ValueError):
    pass


class _InvalidJSONConstant(ValueError):
    pass


_FACT_REF = re.compile(
    r"\{\{fact:([0-9a-fA-F-]{36})#("
    r"/metrics/[A-Za-z][A-Za-z0-9_]*"
    r"|/payload/groups/[0-9]{1,3}/metrics/[A-Za-z][A-Za-z0-9_]*"
    r")\}\}"
)
_PUBLIC_SOURCE_REF = re.compile(r"^([0-9a-fA-F-]{36})#/sources/([0-9]{1,3})$")
_PUBLIC_READ_REF = re.compile(r"^([0-9a-fA-F-]{36})#/payload/text$")
_DATE_TOKEN = re.compile(r"(?<![A-Za-z0-9])(?:19|20)\d{2}(?:[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?)?(?![A-Za-z0-9])")
_DATETIME_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:19|20)\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?"
    r"[ T]?(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?"
    r"(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)?(?![A-Za-z0-9])"
)
_BUSINESS_NUMBER = re.compile(
    r"(?<![A-Za-z])(?:[-+]?\d{2,}(?:\.\d+)?|[-+]?\d+\.\d+|[-+]?\d+(?=\s*(?:手|笔|张|合约|元|万元|CNY|%|点)))"
    r"\s*(?:手|笔|张|合约|元|万元|CNY|%|点)?(?![A-Za-z])",
    re.I,
)
_SAFE_PATH_FIELDS = {
    "status", "paragraphs", "kind", "text", "evidence_refs", "fact_refs",
    "missing", "clarification", "schema_version",
}
_ERROR_MESSAGES = {
    "invalid_json": "答案必须是单个合法JSON对象。",
    "duplicate_field": "答案包含重复字段，重复字段不会被合并。",
    "extra_field": "该字段不允许出现在此位置。",
    "missing_field": "缺少必填字段。",
    "invalid_type": "字段类型不符合答案协议。",
    "invalid_value": "字段值不符合答案协议。",
    "answer_too_large": "答案超过长度限制。",
    "answer_empty": "答案不能为空。",
}


def _safe_path(loc: tuple[Any, ...]) -> str:
    parts = []
    for part in loc:
        if isinstance(part, int):
            parts.append(str(part))
        elif isinstance(part, str) and part in _SAFE_PATH_FIELDS:
            parts.append(part.replace("~", "~0").replace("/", "~1"))
        else:
            parts.append("[unknown]")
    return "/" + "/".join(parts) if parts else "/"


def _issue_from_error(error: dict[str, Any]) -> AnswerIssue:
    path = _safe_path(tuple(error.get("loc") or ()))
    error_type = str(error.get("type") or "")
    if error_type == "extra_forbidden":
        code = "extra_field"
        if path == "/evidence_refs":
            message = "该字段不允许出现在此位置；证据请放入对应段落的evidence_refs或顶层fact_refs。"
        else:
            message = _ERROR_MESSAGES[code]
    elif error_type == "missing":
        code, message = "missing_field", _ERROR_MESSAGES["missing_field"]
    elif error_type in {"string_type", "list_type", "dict_type", "too_short", "too_long"}:
        code, message = "invalid_type", _ERROR_MESSAGES["invalid_type"]
    elif error_type in {"literal_error", "enum", "assertion_error"}:
        code, message = "invalid_value", _ERROR_MESSAGES["invalid_value"]
    else:
        code, message = "invalid_value", _ERROR_MESSAGES["invalid_value"]
    return AnswerIssue(code, path, message)


def _raise_simple(code: str, *, path="/") -> None:
    raise AnswerValidationError([AnswerIssue(code, path, _ERROR_MESSAGES[code])])


def _parse_json_object(raw: str) -> Any:
    if len(raw) > 48000:
        _raise_simple("answer_too_large")

    def pairs(items):
        output = {}
        for key, value in items:
            if key in output:
                raise _DuplicateJSONKey()
            output[key] = value
        return output

    try:
        return json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(_InvalidJSONConstant()),
        )
    except (_DuplicateJSONKey, _InvalidJSONConstant, json.JSONDecodeError, TypeError, ValueError) as exc:
        code = "duplicate_field" if isinstance(exc, _DuplicateJSONKey) else "invalid_json"
        raise AnswerValidationError([AnswerIssue(code, "/", _ERROR_MESSAGES[code])]) from None


def parse_answer(raw: str | dict[str, Any]) -> AnswerDraft:
    if isinstance(raw, AnswerDraft):
        draft = raw
    else:
        if isinstance(raw, str):
            raw = _parse_json_object(raw)
        elif not isinstance(raw, dict):
            _raise_simple("invalid_type")
        try:
            draft = AnswerDraft.model_validate(raw)
        except ValidationError as exc:
            raise AnswerValidationError([_issue_from_error(error) for error in exc.errors(
                include_input=False, include_context=False, include_url=False
            )]) from None
    if len(draft.paragraphs) > 20:
        _raise_simple("answer_too_large", path="/paragraphs")
    if any(len(paragraph.text) > 8000 for paragraph in draft.paragraphs) or sum(len(paragraph.text) for paragraph in draft.paragraphs) > 30000:
        _raise_simple("answer_too_large", path="/paragraphs")
    if len(draft.fact_refs) > 100:
        _raise_simple("answer_too_large", path="/fact_refs")
    if not draft.paragraphs and not draft.clarification:
        _raise_simple("answer_empty", path="/paragraphs")
    return draft


def _load_metric(principal, store, ref: str, metric_path: str, *, issue_path="/fact_refs"):
    if not isinstance(metric_path, str) or not (
        re.fullmatch(r"/metrics/[A-Za-z][A-Za-z0-9_]*", metric_path)
        or re.fullmatch(r"/payload/groups/[0-9]{1,3}/metrics/[A-Za-z][A-Za-z0-9_]*", metric_path)
    ):
        raise InvalidEvidence("证据引用路径不在允许的业务指标目录中", code="invalid_reference", path=issue_path)
    try:
        result_ref = UUID(str(ref))
    except (TypeError, ValueError):
        raise InvalidEvidence("证据引用不可用", code="invalid_reference", path=issue_path) from None
    try:
        saved = store.load_result(principal, result_ref, require_current_task=True)
    except (HTTPException, ResultExpired):
        raise InvalidEvidence("证据引用不存在、越权或已过期", code="reference_unavailable", path=issue_path) from None
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
            from .contracts import MetricValue

            try:
                metric = MetricValue.model_validate(metric)
            except ValidationError:
                metric = None
    if metric is None:
        raise InvalidEvidence("证据引用的指标不存在", code="metric_unavailable", path=metric_path)
    if metric.value is None or metric.status not in {"complete", "partial"}:
        raise InvalidEvidence("证据引用的指标当前不可用", code="metric_unavailable", path=metric_path)
    return metric


def _load_public_reference(principal, store, ref: str, *, issue_path: str) -> None:
    source_match = _PUBLIC_SOURCE_REF.fullmatch(ref)
    read_match = _PUBLIC_READ_REF.fullmatch(ref)
    if not source_match and not read_match:
        raise InvalidEvidence("公开来源引用格式无效", code="invalid_reference", path=issue_path)
    try:
        result_ref = UUID(source_match.group(1) if source_match else read_match.group(1))
        saved = store.load_result(principal, result_ref, require_current_task=True)
    except (TypeError, ValueError):
        raise InvalidEvidence("公开来源引用格式无效", code="invalid_reference", path=issue_path) from None
    except (HTTPException, ResultExpired):
        raise InvalidEvidence("公开来源引用不存在、越权或已过期", code="reference_unavailable", path=issue_path) from None
    payload = saved.envelope.payload if isinstance(saved.envelope.payload, dict) else {}
    if source_match:
        if payload.get("kind") != "research":
            raise InvalidEvidence("公开来源引用类型不匹配", code="invalid_reference", path=issue_path)
        try:
            index = int(source_match.group(2))
            source = payload.get("sources", [])[index]
        except (IndexError, TypeError, ValueError):
            source = None
        if not isinstance(source, dict) or source.get("source_ref") != ref:
            raise InvalidEvidence("公开来源引用不存在", code="reference_unavailable", path=issue_path)
        return
    if payload.get("kind") != "public_read" or not str(payload.get("text") or "").strip():
        raise InvalidEvidence("公开正文引用不可用", code="reference_unavailable", path=issue_path)
    source_ref = payload.get("source_ref")
    if not isinstance(source_ref, str) or not _PUBLIC_SOURCE_REF.fullmatch(source_ref):
        raise InvalidEvidence("公开正文来源登记不完整", code="invalid_reference", path=issue_path)
    if saved.parent_ref is None:
        raise InvalidEvidence("公开正文缺少搜索结果父引用", code="reference_unavailable", path=issue_path)
    parent_ref = f"{saved.parent_ref}#/sources/{int(source_ref.rsplit('/', 1)[-1])}"
    if parent_ref != source_ref:
        raise InvalidEvidence("公开正文来源父引用不匹配", code="reference_unavailable", path=issue_path)
    _load_public_reference(principal, store, source_ref, issue_path=issue_path)


def _resolve(text: str, principal, store):
    def replace(match):
        ref, metric_path = match.groups()
        metric = _load_metric(principal, store, ref, metric_path, issue_path="/paragraphs")
        return f"{metric.value} {metric.unit}"

    rendered = _FACT_REF.sub(replace, text)
    if "{{fact:" in rendered:
        raise InvalidEvidence("事实引用格式无效", code="invalid_reference", path="/paragraphs")
    return rendered


def _has_unreferenced_number(text: str) -> bool:
    without_refs = _FACT_REF.sub(" ", text)
    without_dates = _DATE_TOKEN.sub(" ", _DATETIME_TOKEN.sub(" ", without_refs))
    return bool(_BUSINESS_NUMBER.search(without_dates))


def render_answer(principal, draft: AnswerDraft | str | dict[str, Any], store) -> str:
    draft = parse_answer(draft)
    rendered = []
    references = [(ref, "/fact_refs") for ref in draft.fact_refs]
    for paragraph in draft.paragraphs:
        if paragraph.kind in {"fact", "scenario"} and _has_unreferenced_number(paragraph.text):
            raise InvalidEvidence("事实或情景段落中的业务数字必须引用已登记结果", code="unreferenced_number", path="/paragraphs")
        text = _resolve(paragraph.text, principal, store)
        rendered.append(text)
        references.extend((ref, "/paragraphs/evidence_refs") for ref in paragraph.evidence_refs)
    for ref, path in references:
        if ref.startswith("http://") or ref.startswith("https://"):
            raise InvalidEvidence("公开来源必须使用本任务登记的来源引用", code="invalid_reference", path=path)
        if path != "/fact_refs" and (_PUBLIC_SOURCE_REF.fullmatch(ref) or _PUBLIC_READ_REF.fullmatch(ref)):
            _load_public_reference(principal, store, ref, issue_path=path)
            continue
        if "#/" not in ref:
            raise InvalidEvidence("证据引用格式无效", code="invalid_reference", path=path)
        result_ref, metric_path = ref.split("#/", 1)
        _load_metric(principal, store, result_ref, "/" + metric_path.lstrip("/"), issue_path=path)
    if draft.clarification:
        rendered.append(draft.clarification)
    if not rendered:
        _raise_simple("answer_empty")
    return "\n\n".join(rendered)
