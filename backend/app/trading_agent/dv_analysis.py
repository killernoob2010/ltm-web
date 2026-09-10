"""Deterministic analysis over immutable, authorized dataset snapshots."""
from __future__ import annotations

from datetime import date, timedelta, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from . import store
from .auth import authorize
from .contracts import ToolEnvelope
from .dv_contracts import DatasetCompare, DatasetRelation, DatasetSummary
from .semantic_catalog import get_dataset_spec


VERSION = "dv-analysis-v1"
MICRO_BASE = Decimal("0.01")
_TEMPORAL_FIELDS = {
    "observation_date", "business_date", "period_start", "period_end", "week_start",
    "business_year", "business_week", "week_label",
}
_PRICE_MEASURES = {
    "basis", "wet_spot_price", "standardized_spot_price", "futures_close",
}
_INVENTORY_DATASETS = {"port_inventory", "inventory_summary", "inventory_grade", "source_mainstream_inventory"}
_FLOW_DATASETS = {"spot_series", "arrival_detail"}
_PORT_ALIASES = {
    "日照": "日照港", "日照港": "日照港", "青岛": "青岛港", "青岛港": "青岛港",
    "岚山": "岚山港", "岚山港": "岚山港", "连云港": "连云港", "连云港": "连云港",
    "江阴": "江阴港", "江阴港": "江阴港", "太仓": "太仓港", "太仓港": "太仓港",
    "京唐": "京唐港", "京唐港": "京唐港", "曹妃甸": "曹妃甸港", "曹妃甸港": "曹妃甸港",
}


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _wire_decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def compare_values(current: Decimal | None, previous: Decimal | None, *, allow_pct: bool,
                   micro_base: Decimal) -> dict[str, Any]:
    if current is None or previous is None:
        return {"delta": None, "delta_pct": None, "comparison_status": "missing_period"}
    delta = current - previous
    if not allow_pct:
        return {"delta": str(delta), "delta_pct": None, "comparison_status": "complete"}
    if current < 0 or previous < 0:
        return {"delta": str(delta), "delta_pct": None, "comparison_status": "invalid_input"}
    if previous <= micro_base:
        return {"delta": str(delta), "delta_pct": None, "comparison_status": "unstable_base"}
    return {
        "delta": str(delta),
        "delta_pct": str(delta / previous * Decimal("100")),
        "comparison_status": "complete",
    }


def _metadata(saved) -> dict:
    payload = saved.envelope.payload or {}
    return payload if isinstance(payload, dict) else {}


def _dataset(saved) -> str:
    dataset = _metadata(saved).get("dataset")
    if not isinstance(dataset, str):
        raise ValueError("result_missing_dataset")
    get_dataset_spec(dataset)
    return dataset


def _resources(saved) -> list[str]:
    resources = _metadata(saved).get("required_resources") or ["data_visualization.display"]
    if not isinstance(resources, list) or not resources:
        raise ValueError("result_missing_resources")
    return list(dict.fromkeys(str(value) for value in resources))


def _validate_group_by(dataset: str, group_by: list[str]) -> list[str]:
    spec = get_dataset_spec(dataset)
    fields = list(group_by) if group_by else [field for field in spec.dimension_keys if field not in _TEMPORAL_FIELDS]
    invalid = sorted(set(fields) - set(spec.dimension_keys))
    if invalid:
        raise ValueError(f"unsupported_group:{invalid[0]}")
    if len(fields) != len(set(fields)):
        raise ValueError("duplicate_group")
    return fields


def _rows_by_group(rows: list[dict], fields: list[str]) -> dict[tuple, list[dict]]:
    grouped: dict[tuple, list[dict]] = {}
    for row in rows:
        key = tuple(row.get(field) for field in fields)
        grouped.setdefault(key, []).append(row)
    return grouped


def _result_envelope(status: str, payload: dict, rows: list[dict], *, missing=None) -> ToolEnvelope:
    payload = dict(payload)
    payload.setdefault("row_count", len(rows))
    payload.setdefault("preview", rows[:20])
    payload.setdefault("preview_count", min(len(rows), 20))
    payload.setdefault("preview_truncated", len(rows) > 20)
    return ToolEnvelope(
        status=status,
        captured_at=datetime.now(timezone.utc).replace(microsecond=0),
        calculation_version=VERSION,
        payload=payload,
        missing=missing or [],
    )


def _save_derived(principal, envelope: ToolEnvelope, rows: list[dict], *, kind: str, input_refs: list[str], resources: list[str]):
    ref = store.save_result(
        principal, envelope, rows, kind=kind, input_refs=input_refs,
        required_resources=resources, account_scope=[],
    )
    return store.load_result(principal, ref).envelope


def summarize_dataset(principal, args: DatasetSummary) -> ToolEnvelope:
    if not isinstance(args, DatasetSummary):
        args = DatasetSummary.model_validate(args)
    saved = store.load_result(principal, args.result_ref)
    dataset = _dataset(saved)
    spec = get_dataset_spec(dataset)
    authorize(principal, "data_visualization.display")
    if args.measure not in spec.measure_fields and args.operation != "count":
        raise ValueError(f"unsupported_measure:{args.measure}")
    if args.operation == "sum" and args.measure in _PRICE_MEASURES:
        raise ValueError("unsupported_aggregation:price_sum")
    dates = {row.get("observation_date") for row in saved.rows if row.get("observation_date")}
    if args.operation == "sum" and dataset in _INVENTORY_DATASETS and len(dates) > 1:
        raise ValueError("unsupported_aggregation:inventory_across_periods")
    group_by = _validate_group_by(dataset, args.group_by)
    grouped = _rows_by_group(saved.rows, group_by)
    output: list[dict] = []
    for key, group in sorted(grouped.items(), key=lambda item: tuple(str(value) for value in item[0])):
        values = [_decimal(row.get(args.measure)) for row in group if row.get("value_state", "observed") not in {"missing", "invalid"}]
        values = [value for value in values if value is not None]
        if args.operation == "count":
            value = Decimal(len(values))
        elif not values:
            value = None
        elif args.operation == "sum":
            value = sum(values, Decimal("0"))
        elif args.operation == "mean":
            value = sum(values, Decimal("0")) / Decimal(len(values))
        elif args.operation == "min":
            value = min(values)
        else:
            value = max(values)
        row = {field: key[index] for index, field in enumerate(group_by)}
        row.update({
            "value": _wire_decimal(value), "covered_rows": len(values), "eligible_rows": len(group),
            "status": "complete" if value is not None and len(values) == len(group) else "partial" if values else "missing",
            "row_ref": f"summary:{len(output) + 1}",
        })
        output.append(row)
    payload = {
        "kind": "dataset_summary", "dataset": dataset, "semantic_version": spec.semantic_version,
        "input_refs": [str(args.result_ref)], "required_resources": _resources(saved), "account_scope": [],
        "measure": args.measure, "operation": args.operation, "group_by": group_by,
        "unit": spec.unit, "value_fields": ["value"],
    }
    status = "complete" if output and all(row["status"] == "complete" for row in output) else "partial" if output else "waiting_for_data"
    return _save_derived(principal, _result_envelope(status, payload, output), output,
                         kind="dataset_summary", input_refs=[str(args.result_ref)], resources=_resources(saved))


def _period_date(row: dict) -> str | None:
    value = row.get("observation_date") or row.get("business_date") or row.get("period_start")
    return str(value)[:10] if value else None


def _selected_periods(rows: list[dict], args: DatasetCompare) -> tuple[str | None, str | None]:
    dates = sorted({_period_date(row) for row in rows if _period_date(row)}, reverse=True)
    if not dates:
        return None, None
    current = args.current_date.isoformat() if args.current_date else dates[0]
    if args.method == "explicit_periods":
        if args.current_date is None or args.previous_date is None:
            raise ValueError("explicit_periods_requires_dates")
        return current, args.previous_date.isoformat()
    if current not in dates:
        raise ValueError("current_period_not_in_result")
    if args.method == "previous_week":
        current_date = date.fromisoformat(current)
        previous = (current_date - timedelta(days=7)).isoformat()
        return current, previous
    position = dates.index(current)
    return current, dates[position + 1] if position + 1 < len(dates) else None


def _compatible(current: dict, previous: dict) -> bool:
    for field in ("unit", "source_kind", "scope_type", "grade", "mapping_version"):
        left, right = current.get(field), previous.get(field)
        if left not in (None, "") and right not in (None, "") and left != right:
            return False
    return True


def compare_dataset(principal, args: DatasetCompare) -> ToolEnvelope:
    if not isinstance(args, DatasetCompare):
        args = DatasetCompare.model_validate(args)
    saved = store.load_result(principal, args.result_ref)
    dataset = _dataset(saved)
    spec = get_dataset_spec(dataset)
    authorize(principal, "data_visualization.display")
    if args.measure not in spec.measure_fields:
        raise ValueError(f"unsupported_measure:{args.measure}")
    group_by = _validate_group_by(dataset, args.group_by)
    current_date, previous_date = _selected_periods(saved.rows, args)
    current_rows = [row for row in saved.rows if _period_date(row) == current_date]
    previous_rows = [row for row in saved.rows if _period_date(row) == previous_date]
    current_groups = _rows_by_group(current_rows, group_by)
    previous_groups = _rows_by_group(previous_rows, group_by)
    keys = sorted(set(current_groups) | set(previous_groups), key=lambda key: tuple(str(value) for value in key))
    output: list[dict] = []
    allow_pct = args.measure == "value" and dataset in _INVENTORY_DATASETS | _FLOW_DATASETS
    for key in keys:
        current_group = current_groups.get(key, [])
        previous_group = previous_groups.get(key, [])
        row = {field: key[index] for index, field in enumerate(group_by)}
        row.update({"current_date": current_date, "previous_date": previous_date, "row_ref": f"comparison:{len(output) + 1}"})
        if len(current_group) != 1 or len(previous_group) > 1:
            row.update({"current_value": None, "previous_value": None, "delta": None, "delta_pct": None,
                        "comparison_status": "data_anomaly"})
        elif not current_group or not previous_group:
            result = compare_values(
                _decimal(current_group[0].get(args.measure)) if current_group else None,
                _decimal(previous_group[0].get(args.measure)) if previous_group else None,
                allow_pct=allow_pct, micro_base=MICRO_BASE,
            )
            row.update({"current_value": _wire_decimal(_decimal(current_group[0].get(args.measure))) if current_group else None,
                        "previous_value": _wire_decimal(_decimal(previous_group[0].get(args.measure))) if previous_group else None,
                        **result})
        elif not _compatible(current_group[0], previous_group[0]):
            row.update({"current_value": None, "previous_value": None, "delta": None, "delta_pct": None,
                        "comparison_status": "not_comparable"})
        else:
            current = _decimal(current_group[0].get(args.measure))
            previous = _decimal(previous_group[0].get(args.measure))
            result = compare_values(current, previous, allow_pct=allow_pct, micro_base=MICRO_BASE)
            row.update({"current_value": _wire_decimal(current), "previous_value": _wire_decimal(previous), **result})
        output.append(row)
    matched = sum(1 for row in output if row["comparison_status"] == "complete")
    payload = {
        "kind": "dataset_comparison", "dataset": dataset, "semantic_version": spec.semantic_version,
        "input_refs": [str(args.result_ref)], "required_resources": _resources(saved), "account_scope": [],
        "measure": args.measure, "group_by": group_by,
        "periods": {"current": current_date, "previous": previous_date},
        "coverage": {
            "current_rows": len(current_rows), "previous_rows": len(previous_rows),
            "matched_rows": matched,
            "missing_previous": sum(1 for row in output if row["comparison_status"] == "missing_period" and row.get("current_value") is not None),
        },
        "value_fields": ["current_value", "previous_value", "delta", "delta_pct"],
        "unit": spec.unit,
    }
    status = "complete" if output and all(row["comparison_status"] == "complete" for row in output) else "partial" if output else "waiting_for_data"
    return _save_derived(principal, _result_envelope(status, payload, output), output,
                         kind="dataset_comparison", input_refs=[str(args.result_ref)], resources=_resources(saved))


def _canonical_port(value: Any) -> str | None:
    return _PORT_ALIASES.get(str(value).strip()) if value not in (None, "") else None


def _week_key(row: dict) -> str | None:
    value = row.get("period_start") or row.get("week_start") or row.get("observation_date") or row.get("business_date")
    if not value:
        return None
    try:
        parsed = date.fromisoformat(str(value)[:10])
        return (parsed - timedelta(days=parsed.weekday())).isoformat()
    except ValueError:
        return None


def _relation_row(left: dict, right: dict, relation: str, index: int) -> dict:
    row = {
        "row_ref": f"relation:{index}", "left_date": _period_date(left), "right_date": _period_date(right),
        "left_port": left.get("port"), "right_port": right.get("port"),
        "product": left.get("product") or right.get("product"),
    }
    if relation == "inventory_basis_observation":
        row.update({"inventory_value": left.get("value"), "basis": right.get("basis"), "relation_status": "matched"})
    elif relation == "inventory_wet_price_observation":
        row.update({"inventory_value": left.get("value"), "wet_spot_price": right.get("wet_spot_price"), "relation_status": "matched"})
    else:
        current = _decimal(left.get("wet_spot_price"))
        baseline = _decimal(right.get("wet_spot_price"))
        row.update({"left_wet_spot_price": _wire_decimal(current), "right_wet_spot_price": _wire_decimal(baseline),
                    "delta": _wire_decimal(current - baseline) if current is not None and baseline is not None else None,
                    "relation_status": "matched" if current is not None and baseline is not None else "unmatched"})
    return row


def relate_datasets(principal, args: DatasetRelation) -> ToolEnvelope:
    if not isinstance(args, DatasetRelation):
        args = DatasetRelation.model_validate(args)
    left = store.load_result(principal, args.left_ref)
    right = store.load_result(principal, args.right_ref)
    left_dataset, right_dataset = _dataset(left), _dataset(right)
    resources = list(dict.fromkeys(_resources(left) + _resources(right)))
    authorize(principal, "data_visualization.display")
    matches: list[dict] = []
    unmatched = 0
    if args.relation in {"inventory_basis_observation", "inventory_wet_price_observation"}:
        inventory, basis, inventory_on_left = (left, right, True) if left_dataset in _INVENTORY_DATASETS and right_dataset == "iron_ore_basis" else (right, left, False)
        if _dataset(inventory) == "iron_ore_basis" or _dataset(basis) != "iron_ore_basis":
            raise ValueError("not_comparable:relation_dataset_pair")
        index = {}
        for row in basis.rows:
            key = (_week_key(row), row.get("product"), _canonical_port(row.get("port")))
            if key[0] and key[1] and key[2]:
                index.setdefault(key, []).append(row)
        for inventory_row in inventory.rows:
            key = (_week_key(inventory_row), inventory_row.get("product"), _canonical_port(inventory_row.get("port")))
            candidates = index.get(key, [])
            if len(candidates) == 1:
                matches.append(_relation_row(inventory_row, candidates[0], args.relation, len(matches) + 1))
            else:
                unmatched += 1
    else:
        if left_dataset != "iron_ore_basis" or right_dataset != "iron_ore_basis":
            raise ValueError("not_comparable:relation_dataset_pair")
        baseline = {}
        for row in right.rows:
            if _canonical_port(row.get("port")) == "日照港":
                baseline.setdefault((_period_date(row), row.get("product")), []).append(row)
        for row in left.rows:
            key = (_period_date(row), row.get("product"))
            candidates = baseline.get(key, [])
            if len(candidates) == 1 and _canonical_port(row.get("port")) != "日照港":
                matches.append(_relation_row(row, candidates[0], args.relation, len(matches) + 1))
            else:
                unmatched += 1
    if not matches and unmatched:
        status = "not_comparable"
    elif unmatched:
        status = "partial"
    else:
        status = "complete"
    payload = {
        "kind": "dataset_relation", "relation": args.relation,
        "input_refs": [str(args.left_ref), str(args.right_ref)], "required_resources": resources, "account_scope": [],
        "left_dataset": left_dataset, "right_dataset": right_dataset,
        "coverage": {"matched_rows": len(matches), "unmatched_rows": unmatched},
        "value_fields": sorted({field for row in matches for field in row if field not in {"row_ref", "relation_status"}}),
    }
    return _save_derived(principal, _result_envelope(status, payload, matches,
        missing=[{"reason": "未找到严格登记关系匹配", "code": "unmatched"}] if unmatched else []), matches,
        kind="dataset_relation", input_refs=[str(args.left_ref), str(args.right_ref)], resources=resources)


__all__ = ["VERSION", "MICRO_BASE", "compare_values", "summarize_dataset", "compare_dataset", "relate_datasets"]
