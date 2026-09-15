"""Final, server-owned delivery checks for the controlled Agent path."""
from __future__ import annotations

import re
from typing import Any

from . import coverage as task_coverage
from .answer_contracts import (
    Limitation,
    ValidatedAnswer21,
    ValidationSummary,
    VALIDATION_CHECKS,
)
from .answer_v21 import Answer21ValidationError, validate_answer21
from .contracts import AnswerCoverage


_ACK_ONLY = {"行", "收到", "好的", "好", "可以", "明白", "了解", "ok", "好的收到"}
_RANK_CONCLUSION = re.compile(r"(?:排名|排序|第一|前\s*[一二三1-9]|最大|最小|最高|最低|领先|占比最高|降幅最大|增幅最大)")
_COMPARE_CONCLUSION = re.compile(r"(?:同比|环比|较上期|较前期|变化|增加|减少|上升|下降|差异|对比)")
_SUMMARY_LIMITATION_CODES = frozenset({
    "answer_reference_missing", "result_missing", "result_ref_missing",
    "full_text_required", "source_partial", "scope_mismatch", "metric_missing",
    "internal_source_required", "public_source_required", "answer_delivery_partial",
    "coverage_missing", "requirement_incomplete", "delivery_quality_check_failed",
    "ack_only_answer", "presentation_mismatch", "request_coverage_incomplete",
    "requirement_coverage_incomplete", "uncovered_claim", "unreferenced_number",
    "missing_reference", "invalid_reference", "reference_unavailable",
    "dependent_content_unavailable", "view_data_unavailable", "query_incomplete",
    "public_source_unavailable",
})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _summary_limitation_code(value: Any) -> str | None:
    code = _text(value)
    if code in _SUMMARY_LIMITATION_CODES:
        return code
    for suffix in ("answer_reference_missing", "result_missing", "result_ref_missing", "scope_mismatch"):
        if code.endswith("." + suffix):
            return suffix
    if code.startswith("metric."):
        return "metric_missing"
    return None


def _is_ack_only(value: str) -> bool:
    normalized = re.sub(r"[\s，。！？!,.；;：:、~～`*_#]+", "", value).lower()
    return normalized in _ACK_ONLY


def _has_repeated_conclusion(value: str) -> bool:
    lines = [re.sub(r"\s+", " ", item).strip() for item in value.splitlines()]
    lines = [item for item in lines if len(item) >= 8]
    return len(lines) != len(set(lines))


def _coverage_answer(report: Any, plan: Any | None = None) -> AnswerCoverage:
    missing: list[str] = []
    refs: list[str] = []
    for item in getattr(report, "items", []) or []:
        refs.extend(str(ref) for ref in (getattr(item, "result_refs", []) or []) if ref)
        missing.extend(str(code) for code in (getattr(item, "missing_codes", []) or []))
        missing.extend(str(code) for code in (getattr(item, "delivery_blocks", []) or []))
    refs = list(dict.fromkeys(refs))[:16]
    missing = list(dict.fromkeys(missing))[:32]
    metrics = []
    requirements = {
        str(item.id): item
        for item in (getattr(plan, "requirements", []) or [])
    }
    for item in getattr(report, "items", []) or []:
        if not getattr(item, "missing_codes", None):
            requirement = requirements.get(str(getattr(item, "requirement_id", "")))
            for target in (getattr(requirement, "targets", []) or []) if requirement is not None else []:
                metrics.extend(str(metric) for metric in (getattr(target, "metrics", []) or []))
    return AnswerCoverage(
        status="complete" if getattr(report, "complete", False) else "partial",
        matched_result_refs=list(dict.fromkeys(refs))[:16],
        covered_metrics=list(dict.fromkeys(metrics))[:16],
        missing=missing,
    )


def _check_scope(report: Any) -> str:
    items = list(getattr(report, "items", []) or [])
    if not items:
        return "not_applicable"
    if any(
        any("scope_mismatch" in str(code) for code in (getattr(item, "missing_codes", []) or []))
        for item in items
    ):
        return "failed"
    return "passed"


def _check_time(plan: Any, report: Any) -> str:
    requirements = list(getattr(plan, "requirements", []) or [])
    if not requirements or all(getattr(item, "source_intent", "") in {"knowledge", "discover"} for item in requirements):
        return "not_applicable"
    if any(getattr(item, "time_status", "unknown") != "observed" for item in (getattr(report, "items", []) or [])):
        return "failed"
    return "passed"


def _check_metrics(report: Any) -> str:
    items = list(getattr(report, "items", []) or [])
    if not items:
        return "not_applicable"
    return "failed" if any(
        any(str(code).startswith("metric.") for code in (getattr(item, "missing_codes", []) or []))
        for item in items
    ) else "passed"


def _check_evidence(report: Any) -> str:
    items = list(getattr(report, "items", []) or [])
    if not items:
        return "not_applicable"
    return "failed" if any(
        any(
            str(code).endswith(("result_missing", "result_ref_missing", "answer_reference_missing"))
            or str(code) in {"full_text_required", "result_ref_missing"}
            for code in [*(getattr(item, "missing_codes", []) or []), *(getattr(item, "delivery_blocks", []) or [])]
        )
        for item in items
    ) else "passed"


def _check_analysis(plan: Any, body: str) -> str:
    analyses = [
        getattr(requirement, "analysis", None)
        for requirement in (getattr(plan, "requirements", []) or [])
        if getattr(requirement, "analysis", None) is not None
    ]
    if not analyses:
        return "not_applicable"
    for analysis in analyses:
        operation = getattr(analysis, "operation", "")
        if operation == "rank" and getattr(analysis, "conclusion_required", False) and not _RANK_CONCLUSION.search(body):
            return "failed"
        if operation == "compare" and not _COMPARE_CONCLUSION.search(body):
            return "failed"
    return "failed" if _has_repeated_conclusion(body) else "passed"


def _check_presentation(validated: Any, preference: str, prohibited: set[str]) -> str:
    views = list(getattr(validated, "views", []) or [])
    if any(getattr(item, "code", None) == "presentation_mismatch" for item in (getattr(validated, "limitations", []) or [])):
        return "failed"
    if preference == "text" and views:
        return "failed"
    if any(str(view.get("kind")) in prohibited for view in views if isinstance(view, dict)):
        return "failed"
    body = _text(getattr(validated, "body_markdown", ""))
    if preference == "text" and re.search(r"^\s*\|.*\|\s*$", body, re.M):
        return "failed"
    return "passed"


def _check_rendered_content(validated: Any) -> str:
    body = _text(getattr(validated, "body_markdown", ""))
    if not body and not getattr(validated, "views", None):
        return "failed"
    if body and _is_ack_only(body):
        return "failed"
    if _has_repeated_conclusion(body):
        return "failed"
    return "passed"


def _failed_answer(preference: str, code: str, message: str) -> ValidatedAnswer21:
    summary = ValidationSummary(
        checks={name: ("failed" if name == "rendered_content" else "not_applicable") for name in VALIDATION_CHECKS},
        unresolved_codes=[code],
    )
    return ValidatedAnswer21(
        delivery_status="failed", body_markdown=message, plain_text=message,
        limitations=[Limitation(code=code, message=message)],
        presentation_mode=preference, validation_summary=summary,
    )


def validate_delivery(
    plan: Any,
    draft: Any,
    envelopes: list[Any],
    *,
    principal: Any,
    store_api: Any,
    presentation_preference: str | None = None,
    prohibited_presentations=(),
) -> ValidatedAnswer21:
    """Validate the answer, then attach deterministic seven-dimension checks."""
    preference = presentation_preference or getattr(plan, "presentation", "auto") or "auto"
    prohibited = set(prohibited_presentations or getattr(plan, "prohibited_presentations", []) or [])
    try:
        if isinstance(draft, ValidatedAnswer21):
            # Legacy 2.1 already performed reference resolution. Re-run only
            # the shared server-owned delivery checks at its final boundary.
            validated = draft
        else:
            validated = validate_answer21(
                principal, draft, store_api,
                presentation_preference=preference,
                prohibited_presentations=prohibited,
            )
    except (Answer21ValidationError, TypeError, ValueError) as exc:
        if isinstance(exc, Answer21ValidationError) and exc.issues:
            issue = exc.issues[0]
            return _failed_answer(preference, issue.code, issue.message)
        return _failed_answer(preference, "answer_contract_invalid", str(exc)[:240] or "答案未通过格式校验。")

    report = task_coverage.assess_evidence(plan, list(envelopes or []))
    report = task_coverage.assess_delivery(plan, report, validated)
    body = _text(validated.body_markdown)
    checks = {
        "scope": _check_scope(report),
        "time": _check_time(plan, report),
        "metrics": _check_metrics(report),
        "evidence": _check_evidence(report),
        "analysis": _check_analysis(plan, body),
        "presentation": _check_presentation(validated, preference, prohibited),
        "rendered_content": _check_rendered_content(validated),
    }
    unresolved = [
        str(code)
        for item in (getattr(report, "items", []) or [])
        for code in [*(getattr(item, "missing_codes", []) or []), *(getattr(item, "delivery_blocks", []) or [])]
    ]
    unresolved.extend(
        code
        for limitation in (getattr(validated, "limitations", []) or [])
        if (code := _summary_limitation_code(getattr(limitation, "code", None)))
    )
    if any(value == "failed" for value in checks.values()):
        unresolved.append("delivery_quality_check_failed")
    if body and _is_ack_only(body):
        unresolved.append("ack_only_answer")
    unresolved = list(dict.fromkeys(unresolved))[:40]
    limitations = list(validated.limitations)
    known_limitation_codes = {(item.code, item.message) for item in limitations}
    if not report.complete:
        message = "本次回答未完整覆盖已识别的业务需求，未核验部分未交付。"
        if ("requirement_coverage_incomplete", message) not in known_limitation_codes:
            limitations.append(Limitation(code="requirement_coverage_incomplete", message=message))
    if any(value == "failed" for value in checks.values()):
        message = "本次回答未通过最终交付检查，未核验内容未交付。"
        if ("delivery_quality_check_failed", message) not in known_limitation_codes:
            limitations.append(Limitation(code="delivery_quality_check_failed", message=message))
    if not body and not validated.views:
        delivery_status = "failed"
    elif any(value == "failed" for value in checks.values()) or not report.complete:
        delivery_status = "partial"
    else:
        delivery_status = "complete"
    return validated.model_copy(update={
        "delivery_status": delivery_status,
        "limitations": limitations[:80],
        "coverage": _coverage_answer(report, plan),
        "validation_summary": ValidationSummary(checks=checks, unresolved_codes=unresolved),
        "presentation_mode": preference,
    })


__all__ = ["validate_delivery"]
