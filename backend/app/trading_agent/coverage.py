"""Server-owned requirement and evidence coverage checks.

The language model may propose a plan and an answer, but it cannot declare a
requirement complete.  This module only reasons over registered result
envelopes and the validated answer contract; it never reads business tables or
derives new financial values.
"""
from __future__ import annotations

from typing import Any

from .planning_contracts import CoverageReport, RequirementCoverage, TaskPlan


_POSITION_KINDS = {"positions", "trades", "closes"}
_DATASET_KINDS = {"dataset_rows", "dataset_summary", "dataset_comparison", "dataset_relation", "market_series"}
_PUBLIC_KINDS = {"research", "public_search", "public_read", "public"}
_PUBLIC_SEARCH_KINDS = {"research", "public_search", "public"}


def _payload(envelope: Any) -> dict[str, Any]:
    value = getattr(envelope, "payload", {})
    return value if isinstance(value, dict) else {}


def _kind(envelope: Any) -> str:
    return str(_payload(envelope).get("kind") or "")


def _status(envelope: Any) -> str:
    return str(getattr(envelope, "status", "") or "")


def _result_ref(envelope: Any) -> str | None:
    value = getattr(envelope, "result_ref", None)
    return str(value) if value else None


def _source_ref(envelope: Any) -> str | None:
    value = _payload(envelope).get("source_ref")
    return str(value) if value else None


def _registered_public_source_refs(envelopes: list[Any]) -> set[str]:
    refs: set[str] = set()
    for envelope in envelopes:
        if _kind(envelope) not in _PUBLIC_SEARCH_KINDS:
            continue
        sources = _payload(envelope).get("sources")
        if not isinstance(sources, list):
            continue
        for source in sources:
            if isinstance(source, dict) and source.get("source_ref"):
                refs.add(str(source["source_ref"]))
    return refs


def _is_registered_public_read(envelope: Any, envelopes: list[Any]) -> bool:
    if _kind(envelope) != "public_read":
        return True
    source_ref = _source_ref(envelope)
    return bool(source_ref and source_ref in _registered_public_source_refs(envelopes))


def _requirement_ids(envelope: Any) -> set[str] | None:
    value = _payload(envelope).get("requirement_ids")
    if not isinstance(value, list):
        return None
    return {str(item) for item in value if str(item).strip()}


def _public_association_enabled(envelopes: list[Any]) -> bool:
    return any(_requirement_ids(item) is not None for item in envelopes)


def _source_requirement_ids(source_ref: str | None, envelopes: list[Any]) -> set[str]:
    if not source_ref:
        return set()
    result: set[str] = set()
    for envelope in envelopes:
        if _kind(envelope) not in _PUBLIC_SEARCH_KINDS:
            continue
        source_refs = {
            str(source.get("source_ref"))
            for source in (_payload(envelope).get("sources") or [])
            if isinstance(source, dict) and source.get("source_ref")
        }
        if source_ref in source_refs:
            result.update(_requirement_ids(envelope) or ())
    return result


def _public_item_matches(requirement: Any, envelope: Any, envelopes: list[Any]) -> bool:
    if not _public_association_enabled(envelopes):
        return True
    requirement_id = str(getattr(requirement, "id", ""))
    ids = _requirement_ids(envelope)
    if ids is None and _kind(envelope) == "public_read":
        ids = _source_requirement_ids(_source_ref(envelope), envelopes)
    return bool(ids and requirement_id in ids)


def _public_source_refs_for_requirement(requirement: Any, envelopes: list[Any]) -> set[str]:
    refs: set[str] = set()
    for envelope in envelopes:
        if _kind(envelope) not in _PUBLIC_SEARCH_KINDS:
            continue
        if not _public_item_matches(requirement, envelope, envelopes):
            continue
        for source in (_payload(envelope).get("sources") or []):
            if isinstance(source, dict) and source.get("source_ref"):
                refs.add(str(source["source_ref"]))
    return refs


def _is_full_text(envelope: Any) -> bool:
    fetch_status = _payload(envelope).get("fetch_status")
    return (
        _kind(envelope) == "public_read"
        and bool(str(_payload(envelope).get("text") or "").strip())
        and _status(envelope) in {"complete", "partial"}
        and fetch_status in {"full_text", "truncated"}
    )


def _metric_names(envelope: Any) -> set[str]:
    names: set[str] = set()
    metrics = getattr(envelope, "metrics", {})
    if isinstance(metrics, dict):
        names.update(str(name) for name in metrics)
    payload = _payload(envelope)
    for key in ("measure", "metric"):
        if payload.get(key):
            names.add(str(payload[key]))
    value_fields = payload.get("value_fields")
    if isinstance(value_fields, list):
        names.update(str(name) for name in value_fields)
    for group_key in ("groups", "semantic_groups"):
        groups = payload.get(group_key)
        if not isinstance(groups, list):
            continue
        for group in groups:
            if isinstance(group, dict) and isinstance(group.get("metrics"), dict):
                names.update(str(name) for name in group["metrics"])
    rows = getattr(envelope, "rows", None)
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                names.update(str(name) for name in row)
    preview = payload.get("preview")
    if isinstance(preview, list):
        for row in preview:
            if isinstance(row, dict):
                names.update(str(name) for name in row)
    return names


def _target_kinds(domain: str) -> set[str]:
    if domain == "positions":
        return _POSITION_KINDS
    if domain == "dataset":
        return _DATASET_KINDS
    if domain == "public":
        return _PUBLIC_KINDS
    return set()


def _candidate_envelopes(requirement: Any, envelopes: list[Any]) -> list[Any]:
    if requirement.source_intent in {"knowledge", "discover"}:
        return []
    domains = {target.domain for target in requirement.targets}
    allowed_kinds = set().union(*(_target_kinds(domain) for domain in domains))
    if requirement.source_intent == "public":
        allowed_kinds &= _PUBLIC_KINDS
    elif requirement.source_intent == "internal":
        allowed_kinds -= _PUBLIC_KINDS
    return [
        item for item in envelopes
        if _kind(item) in allowed_kinds
        and _is_registered_public_read(item, envelopes)
        and (_kind(item) not in _PUBLIC_KINDS or _public_item_matches(requirement, item, envelopes))
    ]


def _target_envelopes(target: Any, envelopes: list[Any], requirement: Any | None = None) -> list[Any]:
    return [
        item for item in _target_candidates(target, envelopes, requirement)
        if _scope_matches(target, item, requirement)
    ]


def _target_candidates(target: Any, envelopes: list[Any], requirement: Any | None = None) -> list[Any]:
    allowed_kinds = _target_kinds(str(getattr(target, "domain", "")))
    return [
        item for item in envelopes
        if _kind(item) in allowed_kinds
        and _is_registered_public_read(item, envelopes)
        and (
            str(getattr(target, "domain", "")) != "public"
            or requirement is None
            or _public_item_matches(requirement, item, envelopes)
        )
    ]


def _has_full_text(envelopes: list[Any]) -> bool:
    return any(_is_full_text(item) for item in envelopes)


def public_source_refs_needing_read(plan: TaskPlan, envelopes: list[Any]) -> list[str]:
    """Return registered public sources whose required正文 has not been read."""
    if not any(
        bool(getattr(requirement, "needs_full_text", False))
        and any(getattr(target, "domain", None) == "public" for target in (getattr(requirement, "targets", []) or []))
        for requirement in plan.requirements
    ):
        return []
    all_envelopes = list(envelopes or [])
    missing: list[str] = []
    for requirement in plan.requirements:
        if not (
            bool(getattr(requirement, "needs_full_text", False))
            and any(getattr(target, "domain", None) == "public" for target in (getattr(requirement, "targets", []) or []))
        ):
            continue
        registered = _public_source_refs_for_requirement(requirement, all_envelopes)
        read = {
            _source_ref(item)
            for item in all_envelopes
            if _is_registered_public_read(item, all_envelopes)
            and _public_item_matches(requirement, item, all_envelopes)
            and _is_full_text(item)
        }
        missing.extend(ref for ref in sorted(registered) if ref not in read)
    return list(dict.fromkeys(missing))


def _actual_scope(requirement: Any, candidates: list[Any]) -> dict[str, Any]:
    return {
        "source_intent": requirement.source_intent,
        "target_domains": [target.domain for target in requirement.targets],
        "result_kinds": sorted({_kind(item) for item in candidates}),
        "statuses": sorted({_status(item) for item in candidates}),
    }


def _target_scope(target: Any, candidates: list[Any]) -> dict[str, Any]:
    return {
        "domain": str(getattr(target, "domain", "")),
        "result_kinds": sorted({_kind(item) for item in candidates}),
        "statuses": sorted({_status(item) for item in candidates}),
    }


def _selection(envelope: Any) -> dict[str, Any]:
    payload = _payload(envelope)
    selection = payload.get("selection")
    if not isinstance(selection, dict):
        selection = {}
    flat = {
        key: value for key, value in selection.items()
        if key != "filters" and key != "as_of"
    }
    nested = selection.get("filters")
    if isinstance(nested, dict):
        flat.update(nested)
    as_of = selection.get("as_of") or _payload(envelope).get("as_of")
    if isinstance(as_of, dict):
        if "mode" in as_of:
            flat["as_of_mode"] = as_of["mode"]
        if "date" in as_of:
            flat["as_of_date"] = as_of["date"]
    return flat


def _expected_filters(target: Any) -> dict[str, Any]:
    filters = getattr(target, "filters", {})
    if not isinstance(filters, dict):
        return {}
    flat = {key: value for key, value in filters.items() if key != "filters"}
    nested = filters.get("filters")
    if isinstance(nested, dict):
        flat.update(nested)
    return flat


def _expected_scope(target: Any, requirement: Any | None = None) -> dict[str, Any]:
    expected = _expected_filters(target)
    window = getattr(requirement, "time_window", None)
    if window is not None:
        expected.setdefault("start_date", window.start_date.isoformat())
        expected.setdefault("end_date", window.end_date.isoformat())
    return expected


def _envelope_scope(envelope: Any) -> dict[str, Any]:
    payload = _payload(envelope)
    actual = _selection(envelope)
    for key in ("dataset", "measure", "metric", "operation", "mode"):
        if payload.get(key) is not None:
            actual.setdefault(key, payload[key])
    if payload.get("measure") is not None:
        actual.setdefault("metric", payload["measure"])
    periods = payload.get("periods")
    if isinstance(periods, dict):
        aliases = {
            "current": "current_date", "previous": "previous_date",
            "start": "start_date", "end": "end_date",
        }
        for source, target in aliases.items():
            if periods.get(source) is not None:
                actual.setdefault(target, periods[source])
    coverage = payload.get("coverage")
    if isinstance(coverage, dict):
        if coverage.get("first_observation") is not None:
            actual.setdefault("start_date", coverage["first_observation"])
        if coverage.get("last_observation") is not None:
            actual.setdefault("end_date", coverage["last_observation"])
    preview = payload.get("preview")
    if isinstance(preview, list):
        dates = [
            str(row.get("observation_date") or row.get("business_date") or row.get("period_start"))[:10]
            for row in preview
            if isinstance(row, dict)
            and (row.get("observation_date") or row.get("business_date") or row.get("period_start"))
        ]
        if dates:
            actual.setdefault("start_date", min(dates))
            actual.setdefault("end_date", max(dates))
        for key in ("port", "region", "product", "category", "grade"):
            values = list(dict.fromkeys(
                str(row[key]) for row in preview
                if isinstance(row, dict) and row.get(key) not in (None, "")
            ))
            if values:
                actual.setdefault(key, values)
    return actual


def _period_value_matches(expected: Any, actual: Any, *, start: bool) -> bool:
    if actual in (None, ""):
        return False
    expected_date = str(expected)[:10]
    actual_date = str(actual)[:10]
    return actual_date <= expected_date if start else actual_date >= expected_date


def _scope_matches(target: Any, envelope: Any, requirement: Any | None = None) -> bool:
    expected = _expected_scope(target, requirement)
    if not expected:
        return True
    actual = _envelope_scope(envelope)
    for key, value in expected.items():
        if key == "start_date":
            if not _period_value_matches(value, actual.get(key), start=True):
                return False
            continue
        if key == "end_date":
            if not _period_value_matches(value, actual.get(key), start=False):
                return False
            continue
        if not _value_matches(value, actual.get(key)):
            return False
    return True


def _scope_metadata_available(target: Any, envelope: Any, requirement: Any | None = None) -> bool:
    expected = _expected_scope(target, requirement)
    actual = _envelope_scope(envelope)
    return any(key in actual for key in expected)


def _value_matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, list):
        if not expected:
            return True
        if actual in (None, [], "") or actual == "all":
            return actual == "all"
        if not isinstance(actual, list):
            return False
        return set(str(value) for value in expected).issubset(set(str(value) for value in actual))
    if actual in (None, ""):
        return expected in (None, "")
    if actual == "all":
        return True
    if isinstance(actual, list):
        return str(expected) in {str(value) for value in actual}
    return str(expected) == str(actual)


def _scope_status(target: Any, candidates: list[Any], requirement: Any | None = None) -> str:
    expected = _expected_scope(target, requirement)
    if not expected:
        return "not_requested"
    for envelope in candidates:
        if _scope_matches(target, envelope, requirement):
            return "matched"
    return "mismatch" if any(
        _scope_metadata_available(target, item, requirement) for item in candidates
    ) else "unknown"


def assess_evidence(plan: TaskPlan, envelopes: list[Any]) -> CoverageReport:
    """Assess evidence coverage for every planned requirement."""
    items: list[RequirementCoverage] = []
    for requirement in plan.requirements:
        candidates = _candidate_envelopes(requirement, list(envelopes or []))
        result_refs = list(dict.fromkeys(ref for ref in (_result_ref(item) for item in candidates) if ref))
        missing: list[str] = []
        target_candidates: dict[str, list[Any]] = {}
        target_scope_candidates: dict[str, list[Any]] = {}

        if requirement.source_intent in {"knowledge", "discover"}:
            status = "answered"
        else:
            for target in requirement.targets:
                target_id = str(target.id)
                target_scope_candidates[target_id] = _target_candidates(
                    target, list(envelopes or []), requirement
                )
                target_candidates[target_id] = _target_envelopes(target, list(envelopes or []), requirement)
                if target.domain in {"positions", "dataset", "public"} and not target_candidates[target_id]:
                    scope_status = _scope_status(
                        target, target_scope_candidates[target_id], requirement
                    )
                    missing.append(
                        f"target.{target_id}.scope_mismatch"
                        if scope_status == "mismatch" else
                        f"target.{target_id}.result_missing"
                    )
                target_refs = {
                    ref for ref in (_result_ref(item) for item in target_candidates[target_id]) if ref
                }
                if target_candidates[target_id] and not target_refs:
                    missing.append(f"target.{target_id}.result_ref_missing")
            internal_targets = [target for target in requirement.targets if target.domain in {"positions", "dataset"}]
            public_targets = [target for target in requirement.targets if target.domain == "public"]
            if requirement.source_intent == "both":
                if not internal_targets:
                    missing.append("internal_source_required")
                if not public_targets:
                    missing.append("public_source_required")
            if not candidates:
                missing.append("result_missing")
            elif not result_refs:
                missing.append("result_ref_missing")
            for target in requirement.targets:
                if target.domain == "public":
                    continue
                target_evidence = target_candidates.get(str(target.id), [])
                available_metrics = set().union(*(_metric_names(item) for item in target_evidence)) if target_evidence else set()
                for metric in target.metrics:
                    if metric not in available_metrics:
                        missing.append(f"metric.{metric}")
            public_evidence = [item for target in public_targets for item in target_candidates.get(str(target.id), [])]
            if requirement.needs_full_text and not _has_full_text(public_evidence):
                missing.append("full_text_required")
            candidates = list({id(item): item for values in target_candidates.values() for item in values}.values())
            result_refs = list(dict.fromkeys(ref for ref in (_result_ref(item) for item in candidates) if ref))
            if any(_status(item) not in {"complete"} for item in candidates):
                missing.append("source_partial")
            status = "answered" if not missing else "partial"

        scope = _actual_scope(requirement, candidates)
        scope["targets"] = {
                str(target.id): {
                **_target_scope(target, target_candidates.get(str(target.id), [])),
                "result_refs": list(dict.fromkeys(
                    ref for ref in (_result_ref(item) for item in target_candidates.get(str(target.id), [])) if ref
                )),
                "filter_status": _scope_status(
                    target,
                    target_scope_candidates.get(str(target.id), []),
                    requirement,
                ),
            }
            for target in requirement.targets
        }
        items.append(RequirementCoverage(
            requirement_id=requirement.id,
            status=status,
            result_refs=result_refs,
            missing_codes=list(dict.fromkeys(missing)),
            actual_scope=scope,
            time_status="observed" if candidates else "unknown",
        ))
    return CoverageReport(items=items, complete=bool(items) and all(item.status == "answered" for item in items))


def validate_coverage_report(plan: TaskPlan, report: CoverageReport) -> CoverageReport:
    """Require a coverage item for exactly every requirement in the plan."""
    if not isinstance(report, CoverageReport):
        report = CoverageReport.model_validate(report)
    expected = [str(item.id) for item in plan.requirements]
    actual = [str(item.requirement_id) for item in report.items]
    if set(actual) != set(expected):
        raise ValueError("覆盖报告需求 id 必须与任务计划完全一致")
    return report


def _answer_result_refs(validated_answer: Any) -> set[str]:
    refs: set[str] = set()
    for evidence in getattr(validated_answer, "evidence", []) or []:
        value = getattr(evidence, "result_ref", None)
        if value:
            refs.add(str(value))
    for view in getattr(validated_answer, "views", []) or []:
        if isinstance(view, dict) and view.get("result_ref"):
            refs.add(str(view["result_ref"]))
    return refs


def assess_delivery(plan: TaskPlan, coverage: CoverageReport, validated_answer: Any) -> CoverageReport:
    """Add answer-reference coverage without removing valid independent items."""
    validate_coverage_report(plan, coverage)
    answer_refs = _answer_result_refs(validated_answer)
    answer_status = str(getattr(validated_answer, "delivery_status", "partial"))
    updated: list[RequirementCoverage] = []
    for requirement in plan.requirements:
        current = next((item for item in coverage.items if item.requirement_id == requirement.id), None)
        if current is None:
            current = RequirementCoverage(requirement_id=requirement.id, status="blocked", missing_codes=["coverage_missing"])
        blocks = list(current.delivery_blocks)
        status = current.status
        if current.status == "answered" and current.result_refs:
            if not answer_refs.intersection(current.result_refs):
                blocks.append("answer_reference_missing")
                status = "partial"
        target_scopes = current.actual_scope.get("targets", {})
        for target in requirement.targets:
            target_scope = target_scopes.get(str(target.id), {})
            if "result_refs" in target_scope and target.domain in {"positions", "dataset", "public"}:
                if not answer_refs.intersection(target_scope["result_refs"]):
                    blocks.append(f"target.{target.id}.answer_reference_missing")
                    status = "partial"
        if current.status == "answered" and answer_status != "complete":
            blocks.append("answer_delivery_partial")
            status = "partial"
        updated.append(current.model_copy(update={
            "status": status,
            "delivery_blocks": list(dict.fromkeys(blocks)),
        }))
    return CoverageReport(items=updated, complete=bool(updated) and all(item.status == "answered" for item in updated))


__all__ = [
    "assess_evidence", "assess_delivery", "validate_coverage_report",
    "public_source_refs_needing_read",
]
