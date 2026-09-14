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
        return {}
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


def _value_matches(expected: Any, actual: Any) -> bool:
    if isinstance(expected, list):
        if actual in (None, [], "") or actual == "all":
            return True
        if not isinstance(actual, list):
            return False
        return set(str(value) for value in expected).issubset(set(str(value) for value in actual))
    if actual in (None, ""):
        return expected in (None, "")
    if actual == "all":
        return True
    return str(expected) == str(actual)


def _scope_status(target: Any, candidates: list[Any]) -> str:
    expected = _expected_filters(target)
    if not expected or str(getattr(target, "domain", "")) == "public":
        return "not_requested"
    for envelope in candidates:
        actual = _selection(envelope)
        if actual and all(_value_matches(value, actual.get(key)) for key, value in expected.items()):
            return "matched"
    return "unknown" if not any(_selection(item) for item in candidates) else "mismatch"


def assess_evidence(plan: TaskPlan, envelopes: list[Any]) -> CoverageReport:
    """Assess evidence coverage for every planned requirement."""
    items: list[RequirementCoverage] = []
    for requirement in plan.requirements:
        candidates = _candidate_envelopes(requirement, list(envelopes or []))
        result_refs = list(dict.fromkeys(ref for ref in (_result_ref(item) for item in candidates) if ref))
        missing: list[str] = []

        if requirement.source_intent in {"knowledge", "discover"}:
            status = "answered"
        else:
            target_candidates: dict[str, list[Any]] = {}
            for target in requirement.targets:
                target_id = str(target.id)
                target_candidates[target_id] = _target_envelopes(target, list(envelopes or []), requirement)
                if target.domain in {"positions", "dataset", "public"} and not target_candidates[target_id]:
                    missing.append(f"target.{target_id}.result_missing")
                if target_candidates[target_id] and _scope_status(target, target_candidates[target_id]) == "mismatch":
                    missing.append(f"target.{target_id}.scope_mismatch")
            internal_targets = [target for target in requirement.targets if target.domain in {"positions", "dataset"}]
            public_targets = [target for target in requirement.targets if target.domain == "public"]
            if requirement.source_intent == "both":
                if not internal_targets:
                    missing.append("internal_source_required")
                if not public_targets:
                    missing.append("public_source_required")
            if not candidates:
                missing.append("result_missing")
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
            if any(_status(item) not in {"complete"} for item in candidates):
                missing.append("source_partial")
            status = "answered" if not missing else "partial"

        scope = _actual_scope(requirement, candidates)
        scope["targets"] = {
                str(target.id): {
                **_target_scope(target, _target_envelopes(target, list(envelopes or []), requirement)),
                "filter_status": _scope_status(target, _target_envelopes(target, list(envelopes or []), requirement)),
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
        if current.status == "answered" and answer_status != "complete":
            blocks.append("answer_delivery_partial")
            status = "partial"
        updated.append(current.model_copy(update={
            "status": status,
            "delivery_blocks": list(dict.fromkeys(blocks)),
        }))
    return CoverageReport(items=updated, complete=bool(updated) and all(item.status == "answered" for item in updated))


__all__ = ["assess_evidence", "assess_delivery", "public_source_refs_needing_read"]
