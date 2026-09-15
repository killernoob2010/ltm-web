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

from .answer_contracts import AnswerDraft21, EvidenceItem, Limitation, ModelAnswer21, ValidatedAnswer21


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
    r"|/payload/semantic_groups/[0-9]{1,3}/metrics/[A-Za-z][A-Za-z0-9_]*"
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
_MARKDOWN_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")


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
    except json.JSONDecodeError as exc:
        raise _issue("invalid_json", "/", f"JSON 语法错误：offset={exc.pos}, line={exc.lineno}, column={exc.colno}。") from None
    except (_InvalidConstant, TypeError, UnicodeDecodeError):
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


def parse_answer21(raw: AnswerDraft21 | ModelAnswer21 | str | dict[str, Any]) -> AnswerDraft21:
    if isinstance(raw, AnswerDraft21):
        return raw
    if isinstance(raw, ModelAnswer21):
        return raw.compile()
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
        draft = ModelAnswer21.model_validate(raw).compile() if "blocks" in raw else AnswerDraft21.model_validate(raw)
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


def _result_public_fields(saved: Any) -> set[str]:
    """Resolve row references through the server-owned result registry."""
    from .presentation import allowed_fields_for_saved

    try:
        return allowed_fields_for_saved(saved)
    except (TypeError, ValueError):
        return set()


def _metric(saved: Any, path: str):
    envelope = _envelope(saved)
    if path.startswith("/metrics/"):
        name = path.rsplit("/", 1)[-1]
        metrics = _value(envelope, "metrics", {}) or {}
        return _value(metrics, name)
    parts = path.strip("/").split("/")
    if len(parts) == 5 and parts[3] == "metrics" and tuple(parts[:2]) in {
        ("payload", "groups"), ("payload", "semantic_groups")
    }:
        try:
            group = _payload(saved).get(parts[1], [])[int(parts[2])]
            return (group.get("metrics", {}) if isinstance(group, dict) else {}).get(parts[4])
        except (IndexError, KeyError, TypeError, ValueError):
            return None
    if len(parts) == 3 and parts[0] == "rows":
        if not parts[1].isascii() or not parts[1].isdecimal() or parts[2] not in _result_public_fields(saved):
            return None
        try:
            row = (_value(saved, "rows", []) or [])[int(parts[1])]
            value = row.get(parts[2]) if isinstance(row, dict) else None
            if isinstance(value, dict):
                return value
            return {"value": value, "status": "complete" if value is not None else "unavailable"}
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
    return _business_number_without_bound_tokens(text, ())


def _replace_bound_identifiers(text: str, identifiers) -> str:
    """Remove only identifiers proven by the referenced server result.

    Contract months are often written as bare four-digit tokens (for example
    ``2701``), so the generic alpha-numeric contract-token rule cannot safely
    recognize them.  The caller supplies only months found in a server-owned
    position result/request; arbitrary numbers are intentionally untouched.
    """
    tokens = sorted({str(value).strip() for value in identifiers or () if str(value).strip()}, key=len, reverse=True)
    if not tokens:
        return text
    pattern = r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(token) for token in tokens) + r")(?![A-Za-z0-9])"
    return re.sub(pattern, " ", text, flags=re.IGNORECASE)


def _business_number_without_bound_tokens(text: str, identifiers) -> bool:
    text = _FACT_TOKEN.sub(" ", text)
    text = _DATE_TOKEN.sub(" ", text)
    text = _CONTRACT_TOKEN.sub(" ", text)
    text = _replace_bound_identifiers(text, identifiers)
    return bool(_BUSINESS_NUMBER.search(text))


def _bound_contract_months(saved: Any) -> set[str]:
    """Return contract months carried by a server-owned position result."""
    payload = _payload(saved)
    if payload.get("kind") != "positions":
        return set()
    values: set[str] = set()
    selection = payload.get("selection") if isinstance(payload.get("selection"), dict) else {}
    filters = selection.get("filters") if isinstance(selection.get("filters"), dict) else {}
    for value in filters.get("contract_months") or ():
        text = str(value).strip()
        if text.isdigit() and len(text) in {3, 4}:
            values.add(text)
    for row in _value(saved, "rows", []) or []:
        value = row.get("contract_month") if isinstance(row, dict) else None
        text = str(value or "").strip()
        if text.isdigit() and len(text) in {3, 4}:
            values.add(text)
    for group in payload.get("semantic_groups") or ():
        dimensions = group.get("dimensions", {}) if isinstance(group, dict) else {}
        value = dimensions.get("contract_month") if isinstance(dimensions, dict) else None
        text = str(value or "").strip()
        if text.isdigit() and len(text) in {3, 4}:
            values.add(text)
    return values


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
    # Remove Markdown markers without destroying financial signs or the
    # hyphens inside ISO dates/ranges.  A list marker is only a hyphen followed
    # by whitespace; a negative number and a numeric range remain intact.
    text = re.sub(r"(?m)^(\s*)-\s+", r"\1", text)
    text = re.sub(r"(?<![A-Za-z0-9])-(?=\d)", "\uE000", text)
    text = re.sub(r"(?<=\d)-(?=\d)", "\uE001", text)
    text = re.sub(r"[`*_>#~]", "", text)
    text = re.sub(r"\[(.*?)\]\([^)]*\)", r"\1", text)
    text = text.replace("\uE000", "-").replace("\uE001", "-")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _strip_markdown_tables(text: str) -> tuple[str, bool]:
    """Remove Markdown table blocks when the requested mode forbids tables."""
    lines = text.splitlines()
    kept: list[str] = []
    removed = False
    index = 0
    while index < len(lines):
        if index + 1 < len(lines) and "|" in lines[index] and _MARKDOWN_TABLE_SEPARATOR.match(lines[index + 1]):
            removed = True
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                index += 1
            continue
        kept.append(lines[index])
        index += 1
    return "\n".join(kept), removed


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
    if match.group(2) == "/metadata" and saved is not None and kind != "public_fact":
        if _payload(saved).get("kind") in {
            "positions", "trades", "closes", "market_series", "dataset_rows", "dataset_summary",
            "dataset_comparison", "dataset_relation",
        }:
            return _internal_evidence(match.group(1), saved, None), None
    metric = _metric(saved, match.group(2)) if saved is not None else None
    value, unit = _metric_value(metric)
    if value is None:
        return None, ("reference_unavailable", "事实引用不存在、越权或当前不可用。")
    return _internal_evidence(match.group(1), saved, unit), None


def validate_answer21(
    principal: Any,
    draft: AnswerDraft21 | str | dict[str, Any],
    store_api: Any,
    *,
    presentation_preference: str = "auto",
    prohibited_presentations=(),
) -> ValidatedAnswer21:
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
        bound_identifiers: set[str] = set()
        for ref in span.refs:
            item, error = _reference_for_span(ref, span.kind, principal, store_api)
            if error:
                span_errors.append(error)
            elif item is not None:
                if item.kind == "internal":
                    reference_match = _REFERENCE.fullmatch(ref)
                    if reference_match:
                        saved = _load(store_api, principal, reference_match.group(1))
                        bound_identifiers.update(_bound_contract_months(saved))
                if item.id not in evidence_keys:
                    evidence_keys.add(item.id)
                    evidence.append(item)
        if span.kind in {"fact", "scenario"} and _business_number_without_bound_tokens(
            draft.body_markdown[span.start:span.end], bound_identifiers
        ):
            span_errors.append(("unreferenced_number", "事实或情景片段中的业务数字必须使用绑定引用。"))
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
    incomplete_views = False
    for view in draft.views:
        if view.kind in set(prohibited_presentations or ()):
            limitation_rows.append(
                _limitation("presentation_mismatch", f"当前展示要求禁止{view.kind}视图。", views=[view.id])
            )
            continue
        saved = _load(store_api, principal, str(view.result_ref))
        if saved is None:
            limitation_rows.append(
                _limitation("reference_unavailable", "数据展示引用不存在、越权或已过期。", views=[view.id])
            )
            continue
        from .presentation import _default_fields
        from .presentation import _dataset_fields, allowed_fields_for_saved
        payload = _payload(saved)
        kind = payload.get("kind")
        fields = view.fields or (_dataset_fields(saved, []) if kind in {
            "dataset_rows", "dataset_summary", "dataset_comparison", "dataset_relation",
        } else _default_fields(kind))
        allowed = allowed_fields_for_saved(saved)
        controls = [view.x_field, *view.series_by, *view.facet_by]
        if any(field not in allowed for field in fields + [field for field in controls if field is not None]):
            limitation_rows.append(_limitation("invalid_view", "数据展示字段或维度未在当前结果目录登记。", views=[view.id]))
            continue
        if view.layout == "matrix" and view.kind != "table":
            limitation_rows.append(_limitation("invalid_view", "矩阵展示必须使用表格视图。", views=[view.id]))
            continue
        if view.layout != "standard" and kind not in {
            "dataset_rows", "dataset_summary", "dataset_comparison", "dataset_relation",
        }:
            limitation_rows.append(_limitation("invalid_view", "该布局仅适用于已登记数据集结果。", views=[view.id]))
            continue
        valid_view_ids.add(view.id)
        valid_views.append(view.model_dump(mode="json"))
        rows = _value(saved, "rows", []) or []
        if rows and any(any(row.get(field) is None for field in fields) for row in rows):
            incomplete_views = True
            limitation_rows.append(_limitation("view_data_unavailable", "所展示字段存在缺失值；记录读取完整不代表所需字段完整。", views=[view.id]))

    if presentation_preference == "text" and draft.views:
        limitation_rows.append(
            _limitation("presentation_mismatch", "用户要求纯文字展示，答案不能包含表格或图表。", views=[view.id for view in draft.views])
        )

    uncovered_ranges = _uncovered_claims(draft.body_markdown, draft.spans)
    for _ in uncovered_ranges:
        limitation_rows.append(
            _limitation("uncovered_claim", "正文中的业务数字未被证据片段覆盖，已移除。")
        )
    ranges = [(spans_by_id[span_id].start, spans_by_id[span_id].end) for span_id in invalid]
    ranges.extend(uncovered_ranges)
    body = _remove_ranges(draft.body_markdown, ranges)
    if presentation_preference == "text" or "table" in set(prohibited_presentations or ()):
        body, removed_table = _strip_markdown_tables(body)
        if removed_table:
            limitation_rows.append(_limitation("presentation_mismatch", "当前展示要求不能在正文中嵌入 Markdown 表格。"))
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
    if presentation_preference == "text":
        # A view can be valid as data but still violate an explicit text-only
        # request.  Keep the data out of the delivered answer and let the
        # bounded repair/fallback path compose text from its fact references.
        valid_views = []
        body = _VIEW_TOKEN.sub("", body)
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
    has_failures = bool(invalid or uncovered_ranges or len(valid_views) != len(draft.views) or token_issues or incomplete_views
                        or (presentation_preference == "text" and draft.views))
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
        presentation_mode=presentation_preference,
    )


def _fallback_text_for_result(saved: Any) -> str:
    """Render a compact, evidence-preserving text result for a failed model turn."""
    payload = _payload(saved)
    kind = payload.get("kind")
    metrics = _value(_envelope(saved), "metrics", {}) or {}
    labels = {
        "quantity": "总手数", "floating_pnl": "浮盈亏", "net_quantity": "净卖手数",
        "net_sell_quantity": "净卖手数", "net_tons": "净吨数", "net_wan_tons": "净万吨",
    }

    def net_note(value: str | None) -> str:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return ""
        if number < 0:
            return "（净买）"
        if number == 0:
            return "（净平）"
        return "（净卖）"

    def coverage_note(metric: Any) -> str:
        """Keep partial quote/field coverage visible in a text fallback."""
        status = _value(metric, "status")
        if status not in {"partial", "unavailable"}:
            return ""
        covered = _value(metric, "covered_rows")
        eligible = _value(metric, "eligible_rows")
        try:
            covered_number = int(covered or 0)
            eligible_number = int(eligible or 0)
        except (TypeError, ValueError):
            return ""
        if eligible_number <= 0:
            return "（当前无可用数据）" if status == "unavailable" else ""
        if status == "partial":
            return f"（覆盖{covered_number}/{eligible_number}行）"
        return f"（0/{eligible_number}行可用）"

    parts = []
    net_name = None
    if isinstance(metrics, dict):
        for candidate in ("net_quantity", "net_sell_quantity"):
            candidate_value, _ = _metric_value(metrics.get(candidate))
            if candidate_value is not None:
                net_name = candidate
                break
    selection = payload.get("selection") if isinstance(payload.get("selection"), dict) else {}
    requested_metrics = payload.get("required_metrics") or selection.get("required_metrics") or []
    requested_metrics = [str(name) for name in requested_metrics if str(name) in labels]
    if not requested_metrics:
        requested_metrics = ["quantity"]
        if net_name:
            requested_metrics.append(net_name)
        requested_metrics.extend(("floating_pnl", "net_tons", "net_wan_tons"))
    elif "net_quantity" in requested_metrics and "net_quantity" not in metrics and net_name:
        requested_metrics = [net_name if name == "net_quantity" else name for name in requested_metrics]
    if kind == "positions":
        months = selection.get("filters", {}).get("contract_months") if isinstance(selection.get("filters"), dict) else []
        if months:
            parts.append(f"查询合约月份 {', '.join(str(month) for month in months)}")
    for name in requested_metrics:
        metric = metrics.get(name) if isinstance(metrics, dict) else None
        value, unit = _metric_value(metric)
        if value is not None:
            suffix = net_note(value) if name in {"net_quantity", "net_sell_quantity"} else ""
            parts.append(f"{labels[name]} {value}{unit or ''}{suffix}{coverage_note(metric)}")
    if kind == "positions":
        groups = payload.get("semantic_groups") or []
        group_parts = []
        group_metric_names = set(requested_metrics) & {"net_quantity", "net_sell_quantity", "floating_pnl", "net_tons", "net_wan_tons"}
        for group in groups[:20]:
            dimensions = group.get("dimensions", {}) if isinstance(group, dict) else {}
            option_type = dimensions.get("option_type")
            if option_type not in {"call", "put"}:
                continue
            if not group_metric_names:
                continue
            metric_map = group.get("metrics", {}) if isinstance(group, dict) else {}
            net = metric_map.get("net_quantity") if "net_quantity" in group_metric_names else None
            if _metric_value(net)[0] is None:
                net = metric_map.get("net_sell_quantity") if "net_sell_quantity" in group_metric_names else None
            pnl = metric_map.get("floating_pnl") if "floating_pnl" in group_metric_names else None
            tons = metric_map.get("net_tons") if "net_tons" in group_metric_names else None
            wan_tons = metric_map.get("net_wan_tons") if "net_wan_tons" in group_metric_names else None
            net_value, net_unit = _metric_value(net)
            pnl_value, pnl_unit = _metric_value(pnl)
            tons_value, tons_unit = _metric_value(tons or wan_tons)
            month_label = dimensions.get("contract_month")
            prefix = f"{month_label} " if month_label else ""
            detail = (
                f"{prefix}{str(option_type).title()} 净卖手数 {net_value}{net_unit or ''}{net_note(net_value)}{coverage_note(net)}"
                if net_value is not None
                else f"{prefix}{str(option_type).title()} 净额不可用"
            )
            if pnl_value is not None:
                detail += f"，对应实际持仓浮盈亏 {pnl_value}{pnl_unit or ''}{coverage_note(pnl)}"
            elif _value(pnl, "status") == "unavailable":
                detail += "，对应实际持仓浮盈亏当前不可用"
            if tons_value is not None:
                detail += f"，{labels.get('net_tons' if tons is not None else 'net_wan_tons')} {tons_value}{tons_unit or ''}{coverage_note(tons or wan_tons)}"
            group_parts.append(detail)
        if group_parts:
            parts.extend(group_parts)
        if payload.get("semantic_groups_truncated") or payload.get("groups_truncated"):
            parts.append("语义分组结果已截断，以上不是全部分组。")
    if parts:
        return "；".join(parts) + "。"
    if payload.get("count") == 0 and _value(_envelope(saved), "status") == "complete":
        return "在当前查询范围内没有有效持仓。"
    return "已保留已核验结果，但所需指标当前不可用。"


def _fallback_public_excerpt(saved: Any) -> tuple[EvidenceItem, str] | None:
    """Build a small, evidence-bound excerpt from an already-read public source."""
    payload = _payload(saved)
    if payload.get("kind") != "public_read":
        return None
    if str(_value(_envelope(saved), "status") or "") not in {"complete", "partial"}:
        return None
    if payload.get("fetch_status") not in {"full_text", "truncated"}:
        return None
    text = re.sub(r"\s+", " ", str(payload.get("text") or "")).strip()
    source_ref = str(payload.get("source_ref") or "").strip()
    if not text or not source_ref:
        return None
    ref = f"{saved.ref}#/payload/text"
    evidence = _public_evidence(ref, saved)
    if evidence is None:
        return None
    excerpt = text[:360].rstrip()
    if len(text) > len(excerpt):
        excerpt += "…"
    return evidence, excerpt


def reuse_presentation21(
    principal: Any,
    result_refs: list[str],
    store_api: Any,
    presentation_preference: str,
) -> ValidatedAnswer21:
    """Reformat existing immutable results without reading a new snapshot."""
    views: list[dict] = []
    evidence: list[EvidenceItem] = []
    text_parts: list[str] = []
    statuses: list[str] = []
    seen: set[str] = set()
    for value in result_refs[:8]:
        try:
            ref = str(UUID(str(value)))
        except (TypeError, ValueError):
            continue
        if ref in seen:
            continue
        seen.add(ref)
        saved = _load(store_api, principal, ref)
        if saved is None:
            continue
        payload = _payload(saved)
        kind = payload.get("kind")
        if kind not in {
            "positions", "trades", "closes", "market_series", "dataset_rows", "dataset_summary",
            "dataset_comparison", "dataset_relation",
        }:
            continue
        envelope_status = str(_value(_envelope(saved), "status") or "partial")
        if envelope_status not in {"complete", "partial"}:
            continue
        statuses.append(envelope_status)
        evidence.append(_internal_evidence(ref, saved, None))
        if presentation_preference == "text":
            text_parts.append(_fallback_text_for_result(saved))
            continue
        from .presentation import _default_fields, allowed_fields_for_saved

        allowed = allowed_fields_for_saved(saved)
        if presentation_preference == "chart":
            rows = _value(saved, "rows", []) or []
            metric = "floating_pnl" if "floating_pnl" in allowed and any(row.get("floating_pnl") is not None for row in rows) else "quantity"
            fields = [field for field in ("contract", metric) if field in allowed]
            if len(fields) < 2:
                fields = [field for field in _default_fields(kind) if field in allowed][:2]
            views.append({"id": f"v{len(views) + 1}", "kind": "bar", "result_ref": ref,
                          "fields": fields, "title": "基于同一快照的图形展示"})
        else:
            fields = [field for field in _default_fields(kind) if field in allowed]
            views.append({"id": f"v{len(views) + 1}", "kind": "table", "result_ref": ref,
                          "fields": fields, "title": "基于同一快照的表格展示"})
    if not statuses:
        return ValidatedAnswer21(
            delivery_status="failed", body_markdown="", plain_text="",
            limitations=[Limitation(code="reference_unavailable", message="原结果已过期或当前不可复用。")],
            presentation_mode=presentation_preference,
        )
    complete = all(value == "complete" for value in statuses)
    if presentation_preference == "text":
        body = "\n\n".join(dict.fromkeys(text_parts))
    elif views:
        body = "已切换展示方式，数据值仍来自同一已核验快照。\n\n" + "\n\n".join(
            f"{{{{view:{view['id']}}}}}" for view in views
        )
    else:
        body = "已保留同一已核验快照，但当前没有可用展示字段。"
    return ValidatedAnswer21(
        delivery_status="complete" if complete else "partial",
        body_markdown=body,
        plain_text=_plain_text(body, {view["id"]: view for view in views}),
        evidence=evidence,
        views=views,
        limitations=[] if complete else [Limitation(code="query_incomplete", message="原结果本身为部分结果，展示未扩大其数据覆盖。")],
        presentation_mode=presentation_preference,
    )


def build_fallback21(
    principal: Any,
    result_refs: list[str],
    query_scope: dict,
    failure_code: str,
    store_api: Any,
    *,
    presentation_preference: str = "auto",
    prohibited_presentations=(),
) -> ValidatedAnswer21:
    views = []
    evidence = []
    text_parts = []
    public_text_parts = []
    text_mode = presentation_preference in {"text", "chart"} or "table" in set(prohibited_presentations or ())
    for value in result_refs:
        try:
            ref = str(UUID(str(value)))
        except (TypeError, ValueError):
            continue
        saved = _load(store_api, principal, ref)
        if saved is None:
            continue
        metadata = _payload(saved)
        if metadata.get("kind") == "public_read":
            public_excerpt = _fallback_public_excerpt(saved)
            if public_excerpt is None:
                continue
            public_item, excerpt = public_excerpt
            evidence.append(public_item.model_dump(mode="json"))
            title = re.sub(r"\s+", " ", public_item.title).strip() or "公开来源"
            public_text_parts.append(f"来源：{title}\n已读取正文摘要：{excerpt}")
            continue
        if metadata.get("kind") not in {
            "positions", "trades", "closes", "market_series", "dataset_rows", "dataset_summary",
            "dataset_comparison", "dataset_relation",
        } or metadata.get("aggregation"):
            continue
        if _value(_envelope(saved), "status") not in {"complete", "partial"}:
            continue
        if query_scope and any(metadata.get("selection", {}).get(key) != value for key, value in query_scope.items()):
            continue
        if hasattr(store_api, "load_result_for_view"):
            try:
                store_api.load_result(principal, UUID(ref), require_current_task=True)
                store_api.load_result_for_view(principal.user_id, principal.conversation_id, ref)
            except Exception:
                continue
        if text_mode:
            text_parts.append(_fallback_text_for_result(saved))
        else:
            views.append({"id": f"v{len(views) + 1}", "kind": "table", "result_ref": ref, "fields": [], "title": "已核验数据"})
        evidence.append(_internal_evidence(ref, saved, None).model_dump(mode="json"))
        if len(views) == 8 or len(text_parts) == 8:
            break
    status = "partial" if views or text_parts or evidence else "failed"
    if public_text_parts:
        message_parts = list(text_parts)
        message_parts.append(
            "本次模型答案未通过校验；以下仅保留已读取的公开来源正文摘要，未核验内容未交付：\n\n"
            + "\n\n".join(dict.fromkeys(public_text_parts))
        )
        message = "\n\n".join(dict.fromkeys(message_parts))
    elif text_mode and text_parts:
        message = "\n\n".join(dict.fromkeys(text_parts))
    else:
        message = "已保留已核验数据，但本次回答未能完整交付。" if status == "partial" else "本次回答未通过格式或证据校验，系统未交付业务结论。"
    limitation = Limitation(code=failure_code, message="本次处理未完成，未核验内容未交付。")
    limitations = [limitation]
    if public_text_parts:
        limitations.append(
            Limitation(
                code="public_evidence_partial",
                message="已读取的公开正文已保留；未读取或未核验的公开内容未交付。",
            )
        )
    return ValidatedAnswer21(
        delivery_status=status,
        body_markdown=message,
        plain_text=message,
        evidence=evidence,
        views=views,
        limitations=limitations,
        presentation_mode=presentation_preference,
    )


_POLICY_MESSAGES = {
    "order_finance": (
        "订单融资管理模块当前尚未接入智能贸易助手，无法查询放款状态、未还款金额或融资到期日。",
        "module_not_connected",
    ),
    "backend_admin": (
        "后台管理信息不通过业务Agent提供，管理员也受此限制。",
        "backend_admin_forbidden",
    ),
}


def policy_answer21(module_codes: list[str]) -> ValidatedAnswer21:
    messages = [_POLICY_MESSAGES[code][0] for code in module_codes if code in _POLICY_MESSAGES]
    limitations = [Limitation(code=_POLICY_MESSAGES[code][1], message=_POLICY_MESSAGES[code][0])
                   for code in module_codes if code in _POLICY_MESSAGES]
    body = "\n\n".join(dict.fromkeys(messages)) or "当前请求不在智能贸易助手的授权业务范围内。"
    return ValidatedAnswer21(
        delivery_status="partial", body_markdown=body, plain_text=body,
        evidence=[], views=[], limitations=limitations,
    )


def apply_policy_limits(result: ValidatedAnswer21, module_codes: list[str]) -> ValidatedAnswer21:
    additions = [(_POLICY_MESSAGES[code][0], _POLICY_MESSAGES[code][1])
                 for code in module_codes if code in _POLICY_MESSAGES]
    if not additions:
        return result
    body = result.body_markdown
    limitations = list(result.limitations)
    existing = {(item.code, item.message) for item in limitations}
    for message, code in additions:
        if message not in body:
            body = f"{body.rstrip()}\n\n{message}" if body.strip() else message
        if (code, message) not in existing:
            limitations.append(Limitation(code=code, message=message))
            existing.add((code, message))
    return result.model_copy(update={
        "delivery_status": "partial",
        "body_markdown": body,
        "plain_text": body,
        "limitations": limitations,
    })


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
    "policy_answer21", "apply_policy_limits",
    "parse_answer21",
    "task_state21",
    "validate_answer21",
]
