"""Parse and validate the flexible, evidence-bound answer protocol 2.1."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from .answer_contracts import AnswerDraft21, EvidenceItem, Limitation, ValidatedAnswer21


@dataclass(frozen=True)
class Answer21Issue:
    code: str
    path: str
    message: str


class Answer21ValidationError(ValueError):
    def __init__(self, issues: list[Answer21Issue]):
        self.issues = tuple(issues[:8])
        super().__init__("答案格式或证据未通过校验")

    def as_dicts(self) -> list[dict[str, str]]:
        return [
            {"code": issue.code, "path": issue.path, "message": issue.message}
            for issue in self.issues
        ]


class _DuplicateKey(ValueError):
    pass


class _InvalidConstant(ValueError):
    pass


_FACT_TOKEN = re.compile(
    r"\{\{fact:([0-9a-fA-F-]{36})#("
    r"/metrics/[A-Za-z][A-Za-z0-9_]*"
    r"|/payload/groups/[0-9]{1,3}/metrics/[A-Za-z][A-Za-z0-9_]*"
    r"|/rows/[0-9]{1,5}/[A-Za-z][A-Za-z0-9_]*"
    r")\}\}"
)
_VIEW_TOKEN = re.compile(r"\{\{view:(v[1-8])\}\}")
_PUBLIC_SOURCE = re.compile(r"^([0-9a-fA-F-]{36})#/sources/([0-9]{1,3})$")
_PUBLIC_READ = re.compile(r"^([0-9a-fA-F-]{36})#/payload/text$")
_REFERENCE = re.compile(r"^([0-9a-fA-F-]{36})#(/.+)$")
_DATE_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:19|20)\d{2}(?:[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?)?(?![A-Za-z0-9])"
)
_CONTRACT_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?=[A-Za-z0-9.-]*[A-Za-z])(?=[A-Za-z0-9.-]*\d)"
    r"[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*(?![A-Za-z0-9])"
)
_BUSINESS_NUMBER = re.compile(
    r"(?<![A-Za-z0-9])(?:[-+]?\d{2,}(?:\.\d+)?|[-+]?\d+\.\d+|"
    r"[-+]?\d+(?=\s*(?:手|笔|张|合约|元|万元|CNY|%|点)))"
    r"\s*(?:手|笔|张|合约|元|万元|CNY|%|点)?(?![A-Za-z0-9])",
    re.I,
)


def _issue(code: str, path: str, message: str) -> Answer21ValidationError:
    return Answer21ValidationError([Answer21Issue(code, path, message)])


def _check_unicode(value: Any) -> None:
    if isinstance(value, str):
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise _issue("invalid_unicode", "/", "答案包含不合法的 Unicode 字符。")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _check_unicode(key)
            _check_unicode(item)
        return
    if isinstance(value, list):
        for item in value:
            _check_unicode(item)
        return
    if isinstance(value, float) and not Decimal(str(value)).is_finite():
        raise _issue("invalid_number", "/", "答案包含非有限数字。")


def _json_object(raw: str) -> Any:
    try:
        if len(raw.encode("utf-8")) > 64 * 1024:
            raise _issue("answer_too_large", "/", "答案超过长度限制。")
    except UnicodeEncodeError:
        raise _issue("invalid_unicode", "/", "答案包含不合法的 Unicode 字符。") from None

    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise _DuplicateKey()
            value[key] = item
        return value

    try:
        parsed = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(_InvalidConstant()),
        )
    except _DuplicateKey:
        raise _issue("duplicate_field", "/", "答案包含重复字段。") from None
    except (_InvalidConstant, json.JSONDecodeError, TypeError, UnicodeDecodeError):
        raise _issue("invalid_json", "/", "答案必须是单个合法 JSON 对象。") from None
    _check_unicode(parsed)
    return parsed


def _validation_error(exc: ValidationError) -> Answer21ValidationError:
    errors = []
    for item in exc.errors(include_input=False, include_context=False, include_url=False):
        location = "/" + "/".join(str(part) for part in item.get("loc") or ())
        errors.append(Answer21Issue("invalid_value", location or "/", "字段不符合答案协议。"))
    return Answer21ValidationError(errors or [Answer21Issue("invalid_value", "/", "字段不符合答案协议。")])


def _validate_spans(draft: AnswerDraft21) -> None:
    spans = draft.spans
    ids = [span.id for span in spans]
    if len(ids) != len(set(ids)):
        raise _issue("duplicate_field", "/spans", "证据片段编号必须唯一。")
    ordered = sorted(spans, key=lambda span: (span.start, span.end, span.id))
    previous = None
    for span in ordered:
        if span.end > len(draft.body_markdown):
            raise _issue("invalid_offset", f"/spans/{span.id}", "证据片段偏移超出正文范围。")
        if previous and previous.end > span.start:
            raise _issue("overlapping_span", f"/spans/{span.id}", "证据片段不能重叠。")
        previous = span
    span_ids = set(ids)
    for span in spans:
        if any(parent not in span_ids for parent in span.depends_on):
            raise _issue("invalid_dependency", f"/spans/{span.id}", "证据片段依赖不存在。")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(span_id: str):
        if span_id in visiting:
            raise _issue("dependency_cycle", "/spans", "证据片段依赖不能形成环。")
        if span_id in visited:
            return
        visiting.add(span_id)
        current = next(span for span in spans if span.id == span_id)
        for parent in current.depends_on:
            visit(parent)
        visiting.remove(span_id)
        visited.add(span_id)

    for span in spans:
        visit(span.id)


def parse_answer21(raw: AnswerDraft21 | str | dict[str, Any]) -> AnswerDraft21:
    if isinstance(raw, AnswerDraft21):
        return raw
    if isinstance(raw, str):
        raw = _json_object(raw)
    elif not isinstance(raw, dict):
        raise _issue("invalid_type", "/", "答案必须是 JSON 对象。")
    else:
        _check_unicode(raw)
        try:
            encoded = json.dumps(raw, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except UnicodeEncodeError:
            raise _issue("invalid_unicode", "/", "答案包含不合法的 Unicode 字符。") from None
        except ValueError:
            raise _issue("invalid_number", "/", "答案包含非有限数字。") from None
        if len(encoded) > 64 * 1024:
            raise _issue("answer_too_large", "/", "答案超过长度限制。")
    try:
        draft = AnswerDraft21.model_validate(raw)
    except ValidationError as exc:
        raise _validation_error(exc) from None
    _validate_spans(draft)
    view_ids = [view.id for view in draft.views]
    if len(view_ids) != len(set(view_ids)):
        raise _issue("duplicate_field", "/views", "展示编号必须唯一。")
    if not draft.body_markdown.strip() and not draft.views:
        raise _issue("answer_empty", "/body_markdown", "答案不能没有正文或数据展示。")
    return draft


def _value(obj: Any, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _envelope(saved: Any) -> Any:
    return _value(saved, "envelope", {}) or {}


def _payload(saved: Any) -> dict:
    value = _value(_envelope(saved), "payload", {})
    return value if isinstance(value, dict) else {}


def _metric(saved: Any, path: str):
    envelope = _envelope(saved)
    if path.startswith("/metrics/"):
        name = path.rsplit("/", 1)[-1]
        metrics = _value(envelope, "metrics", {}) or {}
        return _value(metrics, name)
    parts = path.strip("/").split("/")
    if len(parts) == 5 and parts[:2] == ["payload", "groups"] and parts[3] == "metrics":
        try:
            group = _payload(saved).get("groups", [])[int(parts[2])]
            return (group.get("metrics", {}) if isinstance(group, dict) else {}).get(parts[4])
        except (IndexError, KeyError, TypeError, ValueError):
            return None
    if len(parts) == 3 and parts[0] == "rows":
        try:
            row = (_value(saved, "rows", []) or [])[int(parts[1])]
            return row.get(parts[2]) if isinstance(row, dict) else None
        except (IndexError, KeyError, TypeError, ValueError):
            return None
    return None


def _metric_value(metric: Any) -> tuple[str | None, str | None]:
    value = _value(metric, "value")
    unit = _value(metric, "unit")
    state = _value(metric, "status")
    if isinstance(metric, dict) and "status" not in metric and "value" in metric:
        state = "complete" if value is not None else "unavailable"
    if value is None or state not in {"complete", "partial"}:
        return None, unit
    try:
        if not Decimal(str(value)).is_finite():
            return None, unit
    except (InvalidOperation, ValueError):
        return None, unit
    return str(value), unit


def _load(store_api: Any, principal: Any, ref: str):
    try:
        return store_api.load_result(principal, UUID(str(ref)))
    except Exception:
        return None


def _captured_at(saved: Any) -> datetime:
    value = _value(_envelope(saved), "captured_at")
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).replace(microsecond=0)


def _internal_evidence(ref: str, saved: Any, unit: str | None) -> EvidenceItem:
    kind = _payload(saved).get("kind") or "internal result"
    return EvidenceItem(
        id=f"internal:{ref}",
        kind="internal",
        result_ref=ref,
        source_ref=None,
        title=str(kind),
        url=None,
        excerpt=None,
        fetch_status="internal",
        published_at=None,
        captured_at=_captured_at(saved),
        unit=unit,
    )


def _public_evidence(ref: str, saved: Any) -> EvidenceItem | None:
    payload = _payload(saved)
    if _PUBLIC_READ.fullmatch(ref):
        source_ref = payload.get("source_ref")
        text = str(payload.get("text") or "").strip()
        if not source_ref or not text:
            return None
        return EvidenceItem(
            id=f"public:{ref}",
            kind="public",
            result_ref=ref.split("#", 1)[0],
            source_ref=source_ref,
            title=str(payload.get("title") or "公开正文"),
            url=payload.get("url"),
            excerpt=text[:500],
            fetch_status="truncated" if payload.get("truncated") else "full_text",
            published_at=payload.get("published_at"),
            captured_at=_captured_at(saved),
            unit=None,
        )
    match = _PUBLIC_SOURCE.fullmatch(ref)
    if not match:
        return None
    try:
        source = payload.get("sources", [])[int(match.group(2))]
    except (IndexError, TypeError, ValueError):
        return None
    if not isinstance(source, dict) or source.get("source_ref") != ref:
        return None
    return EvidenceItem(
        id=f"public:{ref}",
        kind="public",
        result_ref=match.group(1),
        source_ref=ref,
        title=str(source.get("title") or "公开来源"),
        url=source.get("url"),
        excerpt=str(source.get("description") or "")[:500] or None,
        fetch_status="snippet_only",
        published_at=source.get("published_at"),
        captured_at=_captured_at(saved),
        unit=None,
    )


def _business_number_without_tokens(text: str) -> bool:
    text = _FACT_TOKEN.sub(" ", text)
    text = _DATE_TOKEN.sub(" ", text)
    text = _CONTRACT_TOKEN.sub(" ", text)
    return bool(_BUSINESS_NUMBER.search(text))


def _limitation(code: str, message: str, *, spans=(), views=()) -> Limitation:
    return Limitation(
        code=code,
        message=message,
        affected_span_ids=list(dict.fromkeys(spans)),
        affected_view_ids=list(dict.fromkeys(views)),
    )


def _resolve_fact_tokens(text: str, principal: Any, store_api: Any):
    evidence: list[EvidenceItem] = []
    evidence_keys: set[str] = set()
    issues: list[tuple[str, str]] = []

    def replace(match):
        ref, path = match.groups()
        saved = _load(store_api, principal, ref)
        metric = _metric(saved, path) if saved is not None else None
        value, unit = _metric_value(metric)
        if value is None:
            issues.append(("reference_unavailable", "事实引用不存在、越权或当前不可用。"))
            return ""
        item = _internal_evidence(ref, saved, unit)
        if item.id not in evidence_keys:
            evidence_keys.add(item.id)
            evidence.append(item)
        return f"{value} {unit}".strip()

    rendered = _FACT_TOKEN.sub(replace, text)
    if "{{fact:" in rendered:
        issues.append(("invalid_reference", "事实引用格式无效。"))
    return rendered, evidence, issues


def _remove_ranges(text: str, ranges: list[tuple[int, int]]) -> str:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    for start, end in reversed(merged):
        text = text[:start] + text[end:]
    return text


def _business_number_matches(text: str):
    excluded = [
        match.span()
        for pattern in (_FACT_TOKEN, _DATE_TOKEN, _CONTRACT_TOKEN)
        for match in pattern.finditer(text)
    ]
    for match in _BUSINESS_NUMBER.finditer(text):
        if any(start < match.end() and match.start() < end for start, end in excluded):
            continue
        yield match


def _claim_range(text: str, start: int, end: int) -> tuple[int, int]:
    delimiters = "\n。！？!?；;"
    left_candidates = [text.rfind(delimiter, 0, start) for delimiter in delimiters]
    left = max(left_candidates, default=-1) + 1
    right_candidates = [text.find(delimiter, end) for delimiter in delimiters]
    right_candidates = [value for value in right_candidates if value >= 0]
    right = min(right_candidates, default=len(text) - 1)
    return left, min(len(text), right + 1)


def _uncovered_claims(text: str, spans):
    ranges = []
    for match in _business_number_matches(text):
        if any(span.start <= match.start() and match.end() <= span.end for span in spans):
            continue
        ranges.append(_claim_range(text, match.start(), match.end()))
    return list(dict.fromkeys(ranges))


def _plain_text(markdown: str, views_by_id: dict[str, dict]) -> str:
    def view_label(match):
        return f"[数据展示：{views_by_id.get(match.group(1), {}).get('title') or '表格'}]"

    text = _VIEW_TOKEN.sub(view_label, markdown)
    text = re.sub(r"```(?:[^\n]*)\n?", "", text)
    text = re.sub(r"[`*_>#~-]", "", text)
    text = re.sub(r"\[(.*?)\]\([^)]*\)", r"\1", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _reference_for_span(ref: str, kind: str, principal: Any, store_api: Any):
    source_match = _PUBLIC_SOURCE.fullmatch(ref)
    read_match = _PUBLIC_READ.fullmatch(ref)
    if kind == "public_fact":
        if not read_match:
            return None, ("public_excerpt_required", "公开事实必须引用已读取正文及摘录。")
        saved = _load(store_api, principal, read_match.group(1))
        item = _public_evidence(ref, saved) if saved is not None else None
        if item is None or item.fetch_status not in {"full_text", "truncated"}:
            return None, ("reference_unavailable", "公开正文引用不存在、越权或当前不可用。")
        return item, None
    if source_match or read_match:
        saved = _load(store_api, principal, (source_match or read_match).group(1))
        item = _public_evidence(ref, saved) if saved is not None else None
        if item is None:
            return None, ("reference_unavailable", "公开来源引用不存在、越权或当前不可用。")
        return item, None
    match = _REFERENCE.fullmatch(ref)
    if not match:
        return None, ("invalid_reference", "事实引用格式无效。")
    saved = _load(store_api, principal, match.group(1))
    metric = _metric(saved, match.group(2)) if saved is not None else None
    value, unit = _metric_value(metric)
    if value is None:
        return None, ("reference_unavailable", "事实引用不存在、越权或当前不可用。")
    return _internal_evidence(match.group(1), saved, unit), None


def validate_answer21(principal: Any, draft: AnswerDraft21 | str | dict[str, Any], store_api: Any) -> ValidatedAnswer21:
    draft = parse_answer21(draft)
    spans_by_id = {span.id: span for span in draft.spans}
    invalid: set[str] = set()
    limitation_rows: list[Limitation] = []
    evidence: list[EvidenceItem] = []
    evidence_keys: set[str] = set()

    for span in draft.spans:
        span_errors = []
        if span.kind in {"fact", "public_fact", "scenario"} and not span.refs:
            span_errors.append(("missing_reference", "事实或情景片段必须绑定证据。"))
        if span.kind == "public_fact" and not span.refs:
            span_errors.append(("public_excerpt_required", "公开事实必须绑定正文读取证据。"))
        if span.kind in {"fact", "scenario"} and _business_number_without_tokens(draft.body_markdown[span.start:span.end]):
            span_errors.append(("unreferenced_number", "事实或情景片段中的业务数字必须使用绑定引用。"))
        for ref in span.refs:
            item, error = _reference_for_span(ref, span.kind, principal, store_api)
            if error:
                span_errors.append(error)
            elif item is not None and item.id not in evidence_keys:
                evidence_keys.add(item.id)
                evidence.append(item)
        if span_errors:
            invalid.add(span.id)
            code, message = span_errors[0]
            limitation_rows.append(_limitation(code, message, spans=[span.id]))

    changed = True
    while changed:
        changed = False
        for span in draft.spans:
            if span.id not in invalid and any(parent in invalid for parent in span.depends_on):
                invalid.add(span.id)
                limitation_rows.append(
                    _limitation("dependent_content_unavailable", "该内容依赖未通过核验的证据，已移除。", spans=[span.id])
                )
                changed = True

    valid_views: list[dict] = []
    valid_view_ids: set[str] = set()
    for view in draft.views:
        saved = _load(store_api, principal, str(view.result_ref))
        if saved is None:
            limitation_rows.append(
                _limitation("reference_unavailable", "数据展示引用不存在、越权或已过期。", views=[view.id])
            )
            continue
        valid_view_ids.add(view.id)
        valid_views.append(view.model_dump(mode="json"))

    uncovered_ranges = _uncovered_claims(draft.body_markdown, draft.spans)
    for _ in uncovered_ranges:
        limitation_rows.append(
            _limitation("uncovered_claim", "正文中的业务数字未被证据片段覆盖，已移除。")
        )
    ranges = [(spans_by_id[span_id].start, spans_by_id[span_id].end) for span_id in invalid]
    ranges.extend(uncovered_ranges)
    body = _remove_ranges(draft.body_markdown, ranges)
    body, token_evidence, token_issues = _resolve_fact_tokens(body, principal, store_api)
    for item in token_evidence:
        if item.id not in evidence_keys:
            evidence_keys.add(item.id)
            evidence.append(item)
    for code, message in token_issues:
        limitation_rows.append(_limitation(code, message))
    if token_issues:
        body = _FACT_TOKEN.sub("", body)

    def drop_invalid_view(match):
        if match.group(1) in valid_view_ids:
            return match.group(0)
        return ""

    body = _VIEW_TOKEN.sub(drop_invalid_view, body)
    view_markers = set(_VIEW_TOKEN.findall(body))
    if any(view.id in valid_view_ids and f"{{{{view:{view.id}}}}}" not in body for view in draft.views):
        # A server-validated view is still an independently readable business result.
        pass

    unique_limitations: list[Limitation] = []
    limitation_keys = set()
    for row in limitation_rows:
        key = (row.code, row.message, tuple(row.affected_span_ids), tuple(row.affected_view_ids))
        if key not in limitation_keys:
            limitation_keys.add(key)
            unique_limitations.append(row)

    has_body = bool(body.strip())
    has_view = bool(valid_views)
    has_business_content = has_body or has_view or bool(evidence)
    has_failures = bool(invalid or uncovered_ranges or len(valid_views) != len(draft.views) or token_issues)
    if not has_business_content:
        delivery_status = "failed"
    elif has_failures:
        delivery_status = "partial"
    else:
        delivery_status = "complete"
    views_by_id = {view["id"]: view for view in valid_views}
    plain_text = _plain_text(body, views_by_id)
    if not plain_text and has_view:
        plain_text = "已生成可核验数据展示，请展开查看。"
    return ValidatedAnswer21(
        delivery_status=delivery_status,
        body_markdown=body,
        plain_text=plain_text,
        evidence=evidence,
        views=valid_views,
        limitations=unique_limitations,
    )


def build_fallback21(principal: Any, result_refs: list[str], query_scope: dict, failure_code: str, store_api: Any) -> ValidatedAnswer21:
    views = []
    evidence = []
    for value in result_refs:
        try:
            ref = str(UUID(str(value)))
        except (TypeError, ValueError):
            continue
        saved = _load(store_api, principal, ref)
        if saved is None:
            continue
        views.append({"id": f"v{len(views) + 1}", "kind": "table", "result_ref": ref, "fields": [], "title": "已核验数据"})
        evidence.append(_internal_evidence(ref, saved, None).model_dump(mode="json"))
        if len(views) == 8:
            break
    status = "partial" if views or evidence else "failed"
    message = "已保留已核验数据，但本次回答未能完整交付。" if status == "partial" else "本次回答未通过格式或证据校验，系统未交付业务结论。"
    limitation = Limitation(code=failure_code, message="本次处理未完成，未核验内容未交付。")
    return ValidatedAnswer21(
        delivery_status=status,
        body_markdown=message,
        plain_text=message,
        evidence=evidence,
        views=views,
        limitations=[limitation],
    )


def task_state21(delivery_status: str) -> str:
    mapping = {"complete": "succeeded", "partial": "partial", "failed": "failed"}
    if delivery_status not in mapping:
        raise ValueError("invalid delivery status")
    return mapping[delivery_status]


__all__ = [
    "Answer21Issue",
    "Answer21ValidationError",
    "AnswerDraft21",
    "ValidatedAnswer21",
    "build_fallback21",
    "parse_answer21",
    "task_state21",
    "validate_answer21",
]
