"""Fixed, read-only adapters for the registered trade data-visualization datasets."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import inspect
import json
import os
import time
from datetime import date, datetime, timezone
from typing import Any

from . import store
from .auth import authorize
from .contracts import ToolEnvelope
from .dv_contracts import DatasetQuery, OptimalWarrantArgs, validate_dataset_query
from .semantic_catalog import DATASET_SPECS, DatasetId, SENSITIVE_FIELDS, get_dataset_spec, public_allowed_fields
from .. import db


VERSION = "dv-query-v1"
MAX_ROWS = 20_000
PREVIEW_ROWS = 20
CURSOR_TTL_SECONDS = 15 * 60


class DataSourceUnavailable(RuntimeError):
    pass


# SQL here is deliberately fixed by dataset.  Model input can only select a
# registered predicate below; it can never supply a table, column, expression,
# join, or arbitrary statement.
_SOURCE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "spot_series": {
        "from": """dv_integrated_points s
            JOIN dv_integration_batches b ON b.id = s.batch_id
                AND b.status IN ('committed', 'completed', 'activated')""",
        "columns": {
            "source_row_id": "s.id", "observation_date": "s.display_date", "period_start": "s.week_start",
            "period_end": "s.week_end", "business_year": "s.business_year", "business_week": "s.business_week",
            "week_label": "s.week_label", "metric": "s.metric_type", "source_country": "s.source_country",
            "product": "s.product", "category": "s.category", "mainstream_status": "s.mainstream_status",
            "value": "s.value", "unit": "s.unit", "source_kind": "s.source_section",
            "value_state_source": "s.validation_status", "source_file": "s.source_file",
            "source_sheet": "s.source_sheet", "source_section": "s.source_section",
            "package_id": "s.batch_id", "row_ref": "s.id",
        },
        "date_field": "s.display_date",
    },
    "port_inventory": {
        "from": """dv_port_inventory_facts f
            JOIN dv_source_packages p ON p.package_id = f.package_id AND p.status = 'activated'""",
        "columns": {
            "source_row_id": "f.id", "observation_date": "f.observed_date", "period_start": "f.week_start",
            "week_start": "f.week_start", "port": "f.port_name", "region": "f.region",
            "scope_type": "f.scope_type", "source_country": "f.source_country", "product": "f.product",
            "category": "f.category", "mainstream_status": "f.mainstream_status", "raw_product": "f.raw_product",
            "value": "f.value", "unit": "f.unit", "value_state_source": "f.value_status",
            "source_file": "f.source_file", "source_sheet": "f.source_sheet", "source_row": "f.source_row",
            "source_column": "f.source_column", "source_cell": "f.source_cell", "mapping_version": "f.mapping_version",
            "package_id": "f.package_id", "row_ref": "f.id",
        },
        "date_field": "f.observed_date",
    },
    "inventory_summary": {
        "from": """dv_inventory_summary_facts f
            JOIN dv_source_packages p ON p.package_id = f.package_id AND p.status = 'activated'""",
        "columns": {
            "source_row_id": "f.id", "observation_date": "f.observed_date", "period_start": "f.week_start",
            "week_start": "f.week_start", "port": "f.port_name", "region": "f.region",
            "scope_type": "f.scope_type", "summary_metric": "f.metric", "value": "f.value", "unit": "f.unit",
            "value_state_source": "f.value_status", "source_file": "f.source_file", "source_sheet": "f.source_sheet",
            "source_row": "f.source_row", "source_column": "f.source_column", "source_cell": "f.source_cell",
            "mapping_version": "f.mapping_version", "package_id": "f.package_id", "row_ref": "f.id",
        },
        "date_field": "f.observed_date",
    },
    "inventory_grade": {
        "from": """dv_inventory_grade_facts f
            JOIN dv_source_packages p ON p.package_id = f.package_id AND p.status = 'activated'""",
        "columns": {
            "source_row_id": "f.id", "observation_date": "f.observed_date", "period_start": "f.week_start",
            "week_start": "f.week_start", "port": "f.port_name", "region": "f.region",
            "scope_type": "f.scope_type", "raw_grade": "f.raw_grade", "grade": "f.grade",
            "category": "f.category", "value": "f.value", "unit": "f.unit",
            "value_state_source": "f.value_status", "source_file": "f.source_file", "source_sheet": "f.source_sheet",
            "source_row": "f.source_row", "source_column": "f.source_column", "source_cell": "f.source_cell",
            "mapping_version": "f.mapping_version", "package_id": "f.package_id", "row_ref": "f.id",
        },
        "date_field": "f.observed_date",
    },
    "source_mainstream_inventory": {
        "from": """dv_inventory_mainstream_facts f
            JOIN dv_source_packages p ON p.package_id = f.package_id AND p.status = 'activated'""",
        "columns": {
            "source_row_id": "f.id", "observation_date": "f.observed_date", "period_start": "f.week_start",
            "week_start": "f.week_start", "port": "f.port_name", "region": "f.region",
            "scope_type": "f.scope_type", "raw_product": "f.raw_product", "product": "f.product",
            "category": "f.category", "source_country": "f.source_country", "value": "f.value", "unit": "f.unit",
            "value_state_source": "f.value_status", "source_file": "f.source_file", "source_sheet": "f.source_sheet",
            "source_row": "f.source_row", "source_column": "f.source_column", "source_cell": "f.source_cell",
            "mapping_version": "f.mapping_version", "package_id": "f.package_id", "row_ref": "f.id",
        },
        "date_field": "f.observed_date",
    },
    "arrival_detail": {
        "from": """dv_arrival_facts f
            JOIN dv_source_packages p ON p.package_id = f.package_id AND p.status = 'activated'""",
        "columns": {
            "source_row_id": "f.id", "observation_date": "f.observed_date", "period_start": "f.week_start",
            "week_start": "f.week_start", "arrival_kind": "f.arrival_kind", "method": "f.method",
            "port": "f.port_name", "scope_type": "f.scope_type", "slice_type": "f.slice_type",
            "dimension": "f.dimension", "product": "f.product", "category": "f.category",
            "grade": "f.grade", "source_country": "f.source_country", "mainstream_status": "f.mainstream_status",
            "value": "f.value", "unit": "f.unit", "value_state_source": "f.value_status",
            "source_file": "f.source_file", "source_sheet": "f.source_sheet", "source_row": "f.source_row",
            "source_column": "f.source_column", "source_cell": "f.source_cell", "mapping_version": "f.mapping_version",
            "package_id": "f.package_id", "row_ref": "f.id",
        },
        "date_field": "f.observed_date",
    },
    "iron_ore_basis": {
        "from": "iron_ore_basis_results r",
        "columns": {
            "source_row_id": "r.id", "observation_date": "r.business_date", "business_date": "r.business_date",
            "business_year": "r.business_year", "business_week": "r.business_week", "week_label": "r.week_label",
            "port": "r.port", "product": "r.product", "futures_series": "r.futures_series",
            "wet_spot_price": "r.wet_spot_price", "quality_adjustment": "r.quality_adjustment",
            "brand_adjustment": "r.brand_adjustment", "standardized_spot_price": "r.standardized_spot_price",
            "futures_close": "r.futures_close", "basis": "r.basis", "data_status": "r.data_status",
            "rule_version": "r.rule_version", "parameter_version": "r.parameter_version",
            "source_file": "r.source_workbook_name", "source_workbook_name": "r.source_workbook_name",
            "source_workbook_sha256": "r.source_workbook_sha256", "value_state_source": "r.data_status",
            "row_ref": "r.id",
        },
        "date_field": "r.business_date",
    },
}


_FILTER_COLUMNS = {
    "metric": "metric", "products": "product", "categories": "category", "source_countries": "source_country",
    "ports": "port", "regions": "region", "mainstream_status": "mainstream_status", "scope_type": "scope_type",
    "slice_type": "slice_type", "arrival_kind": "arrival_kind", "grades": "grade", "summary_metrics": "summary_metric",
}
_SENSITIVE_FIELDS = SENSITIVE_FIELDS


def _state(value, source_value=None):
    if value is None:
        return "missing"
    text = str(source_value or "").strip().lower()
    if text in {"invalid", "异常", "error", "invalid_value"}:
        return "invalid"
    if text in {"missing", "缺失", "", "unknown", "未知"}:
        return "observed"
    if text in {"ok", "valid", "有效", "confirmed", "complete", "完成"}:
        return "observed"
    return "observed"


def _definition(dataset: str) -> dict[str, Any]:
    try:
        return _SOURCE_DEFINITIONS[dataset]
    except KeyError:
        raise ValueError(f"unsupported_dataset:{dataset}") from None


def _fixed_select(definition: dict[str, Any]) -> str:
    return ", ".join(f"{expression} AS {field}" for field, expression in definition["columns"].items())


def _readonly_fetch(sql: str, params: tuple[Any, ...]) -> list[dict]:
    try:
        with db.connect() as conn:
            cur = conn.cursor()
            if db._is_pg():
                db._exec(cur, "SET TRANSACTION READ ONLY")
            else:
                conn.execute("PRAGMA query_only = ON")
            rows = db._exec(cur, sql, params).fetchall()
            return [dict(row) for row in rows]
    except Exception as exc:
        raise DataSourceUnavailable("registered_source_unavailable") from exc


def _empty_explicit(filters, field: str) -> bool:
    return field in filters.model_fields_set and getattr(filters, field) == []


def _where(args: DatasetQuery, definition: dict[str, Any]) -> tuple[str, list[Any]]:
    filters = args.filters
    clauses: list[str] = []
    params: list[Any] = []
    date_field = definition["date_field"]
    if args.mode == "range":
        clauses.append(f"{date_field} BETWEEN ? AND ?")
        params.extend([args.start_date.isoformat(), args.end_date.isoformat()])
    elif args.mode == "seasonal":
        clauses.append(f"CAST(SUBSTR({date_field}, 1, 4) AS INTEGER) IN ({','.join('?' for _ in filters.years)})")
        params.extend(filters.years)
    elif args.start_date or args.end_date:
        clauses.append(f"{date_field} BETWEEN ? AND ?")
        params.extend([args.start_date.isoformat() if args.start_date else "0001-01-01",
                       args.end_date.isoformat() if args.end_date else "9999-12-31"])
    if filters.years and args.mode != "seasonal":
        clauses.append(f"CAST(SUBSTR({date_field}, 1, 4) AS INTEGER) IN ({','.join('?' for _ in filters.years)})")
        params.extend(filters.years)

    for field, column in _FILTER_COLUMNS.items():
        values = getattr(filters, field, None)
        if values is None:
            continue
        expression = definition["columns"].get(column)
        if expression is None:
            # The semantic validator should have rejected this combination;
            # fail closed if a catalog and adapter ever drift apart.
            raise ValueError(f"unsupported_filter:{field}")
        if isinstance(values, list):
            if not values:
                clauses.append("1 = 0")
                continue
            clauses.append(f"{expression} IN ({','.join('?' for _ in values)})")
            params.extend(values)
        elif field == "metric":
            clauses.append(f"{expression} = ?")
            params.append(values)
    if filters.product_pool in {"mainstream", "non_mainstream"}:
        expression = definition["columns"].get("mainstream_status")
        if expression is None:
            raise ValueError("unsupported_filter:product_pool")
        clauses.append(f"{expression} = ?")
        params.append("主流" if filters.product_pool == "mainstream" else "非主流")
    if filters.data_states is not None:
        if not filters.data_states:
            clauses.append("1 = 0")
        # value_state is normalized after reading; do not guess source labels.
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def _date_value(row: dict) -> str | None:
    value = row.get("observation_date") or row.get("business_date") or row.get("period_start")
    return str(value)[:10] if value else None


def _business_week(row: dict) -> int | None:
    if row.get("business_week") is not None:
        try:
            return int(row["business_week"])
        except (TypeError, ValueError):
            return None
    try:
        return date.fromisoformat(_date_value(row)).isocalendar().week
    except (TypeError, ValueError):
        return None


def _normalize_row(row: dict, dataset: str) -> dict:
    output = {key: value for key, value in row.items() if value is not None}
    output["row_ref"] = str(row.get("row_ref") or row.get("source_row_id"))
    output["observation_date"] = _date_value(row)
    if output.get("business_year") is None and output.get("observation_date"):
        try:
            output["business_year"] = int(str(output["observation_date"])[:4])
        except (TypeError, ValueError):
            pass
    if row.get("period_start") is not None:
        output["period_start"] = str(row["period_start"])[:10]
    if row.get("business_week") is None:
        output["business_week"] = _business_week(row)
    output["value_state"] = _state(row.get("value"), row.get("value_state_source"))
    if dataset == "inventory_summary" and "summary_metric" in output:
        output.setdefault("metric", output["summary_metric"])
    return output


def _apply_python_filters(rows: list[dict], args: DatasetQuery) -> list[dict]:
    filters = args.filters
    normalized = [_normalize_row(row, args.dataset) for row in rows]
    if filters.years:
        normalized = [row for row in normalized if int(str(row.get("observation_date", "0000"))[:4]) in filters.years]
    if filters.business_weeks:
        normalized = [row for row in normalized if row.get("business_week") in filters.business_weeks]
    if filters.data_states:
        normalized = [row for row in normalized if row.get("value_state") in filters.data_states]
    if args.mode == "latest" and normalized:
        dates = sorted({row.get("observation_date") for row in normalized if row.get("observation_date")}, reverse=True)
        current = date.fromisoformat(dates[0]) if dates else None
        if args.dataset == "iron_ore_basis":
            keep = set(dates[:2])
        else:
            previous = (current.fromordinal(current.toordinal() - 7).isoformat() if current else None)
            keep = {dates[0]}
            if previous in dates:
                keep.add(previous)
        normalized = [row for row in normalized if row.get("observation_date") in keep]
    return normalized


def _logical_key(dataset: str, row: dict) -> tuple:
    spec = get_dataset_spec(dataset)
    dimensions = spec.dimension_keys
    return tuple(row.get(field) for field in dimensions)


def _duplicate_keys(dataset: str, rows: list[dict]) -> set[tuple]:
    seen: set[tuple] = set()
    duplicate: set[tuple] = set()
    for row in rows:
        key = _logical_key(dataset, row)
        if key in seen:
            duplicate.add(key)
        seen.add(key)
    return duplicate


def _cursor_secret() -> bytes:
    # Prefer a deployment secret.  DATABASE_URL is a stable deployment-bound
    # fallback for existing installations that predate the cursor setting; the
    # cursor is still re-authorized and bound to the user/conversation below.
    configured = (os.environ.get("AGENT_V2_CURSOR_SECRET") or os.environ.get("DATABASE_URL") or "agent-v2-local-cursor-key").strip()
    return hashlib.sha256(configured.encode("utf-8")).digest()


def _canonical_query(args: DatasetQuery) -> str:
    payload = args.model_dump(mode="json")
    payload.pop("cursor", None)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _encode_cursor(args: DatasetQuery, principal, row: dict) -> str:
    payload = {
        "v": 1,
        "dataset": args.dataset,
        "query": hashlib.sha256(_canonical_query(args).encode("utf-8")).hexdigest(),
        "user_id": int(principal.user_id),
        "conversation_id": int(principal.conversation_id),
        "last_date": _date_value(row),
        "last_row": str(row.get("row_ref") or ""),
        "expires_at": int(time.time()) + CURSOR_TTL_SECONDS,
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")
    signature = hmac.new(_cursor_secret(), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def _decode_cursor(args: DatasetQuery, principal) -> dict:
    try:
        encoded, supplied = args.cursor.split(".", 1)
        expected = hmac.new(_cursor_secret(), encoded.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, supplied):
            raise ValueError
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        if not isinstance(payload, dict):
            raise ValueError
        if payload.get("v") != 1 or payload.get("dataset") != args.dataset:
            raise ValueError
        if payload.get("user_id") != int(principal.user_id) or payload.get("conversation_id") != int(principal.conversation_id):
            raise ValueError
        query_hash = hashlib.sha256(_canonical_query(args).encode("utf-8")).hexdigest()
        if payload.get("query") != query_hash or int(payload.get("expires_at", 0)) < int(time.time()):
            raise ValueError
        if not payload.get("last_date") or not payload.get("last_row"):
            raise ValueError
        return payload
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError, UnicodeError, binascii.Error):
        raise ValueError("invalid_cursor") from None


def _query_rows(args: DatasetQuery, principal) -> list[dict]:
    definition = _definition(args.dataset)
    where, params = _where(args, definition)
    if args.cursor:
        cursor = _decode_cursor(args, principal)
        date_field = definition["date_field"]
        row_field = definition["columns"]["source_row_id"]
        cursor_clause = f"({date_field} < ? OR ({date_field} = ? AND {row_field} > ?))"
        if where:
            where = f"{where} AND {cursor_clause}"
        else:
            where = f" WHERE {cursor_clause}"
        params.extend([cursor["last_date"], cursor["last_date"], cursor["last_row"]])
    sql = f"SELECT {_fixed_select(definition)} FROM {definition['from']}{where} ORDER BY {definition['date_field']} DESC, {definition['columns']['source_row_id']} ASC LIMIT ?"
    raw = _readonly_fetch(sql, tuple(params + [args.batch_size + 1]))
    return _apply_python_filters(raw, args)


def _project_rows(dataset: str, rows: list[dict], fields: list[str]) -> list[dict]:
    selected = tuple(fields) if fields else public_allowed_fields(dataset)
    return [
        {field: row[field] for field in selected if field in row} | {"row_ref": row["row_ref"]}
        for row in rows
    ]


def _envelope(status: str, dataset: str, args: DatasetQuery, rows: list[dict], *, missing=None, extra=None) -> ToolEnvelope:
    spec = get_dataset_spec(dataset)
    payload = {
        "kind": "dataset_rows",
        "dataset": dataset,
        "semantic_version": spec.semantic_version,
        "selection": args.model_dump(mode="json"),
        "required_resources": ["data_visualization.display"],
        "account_scope": [],
        "row_count": len(rows),
        "preview": rows[:PREVIEW_ROWS],
        "preview_count": min(len(rows), PREVIEW_ROWS),
        "preview_truncated": len(rows) > PREVIEW_ROWS,
        "value_fields": list(spec.measure_fields),
        "unit": spec.unit,
        "coverage": {
            "first_observation": min((_date_value(row) for row in rows if _date_value(row)), default=None),
            "last_observation": max((_date_value(row) for row in rows if _date_value(row)), default=None),
        },
    }
    observed_years = sorted({
        int(str(_date_value(row))[:4])
        for row in rows
        if _date_value(row) and str(_date_value(row))[:4].isdigit()
    })
    if observed_years:
        payload["coverage"]["observed_years"] = observed_years
        requested_years = (args.filters.years or [])
        if not requested_years and args.start_date and args.end_date:
            requested_years = list(range(args.start_date.year, args.end_date.year + 1))
        if requested_years:
            payload["coverage"]["requested_years"] = sorted(set(requested_years))
            payload["coverage"]["missing_years"] = sorted(set(requested_years) - set(observed_years))
    if extra:
        payload.update(extra)
    return ToolEnvelope(
        status=status,
        captured_at=datetime.now(timezone.utc).replace(microsecond=0),
        calculation_version=VERSION,
        payload=payload,
        missing=missing or [],
    )


def query_dataset(principal, args: DatasetQuery) -> ToolEnvelope:
    if not isinstance(args, DatasetQuery):
        args = DatasetQuery.model_validate(args)
    args = validate_dataset_query(args)
    authorize(principal, "data_visualization.display")
    resources = ["data_visualization.display"]
    if set(args.fields) & _SENSITIVE_FIELDS:
        authorize(principal, "data_visualization.data")
        resources.append("data_visualization.data")
    try:
        # Keep the adapter seam compatible with narrow test/dry-run fakes that
        # predate the principal-bound continuation argument.
        if len(inspect.signature(_query_rows).parameters) == 1:
            rows = _query_rows(args)
        else:
            rows = _query_rows(args, principal)
    except DataSourceUnavailable:
        return _envelope(
            "temporarily_unavailable", args.dataset, args, [],
            missing=[{"reason": "registered_source_unavailable", "code": "source_unavailable"}],
        )
    duplicates = _duplicate_keys(args.dataset, rows)
    if duplicates:
        return _envelope(
            "data_anomaly", args.dataset, args, rows,
            missing=[{"reason": "同一登记逻辑身份存在多个已生效来源行", "code": "duplicate_identity"}],
        )
    has_more = len(rows) > args.batch_size
    rows = rows[:args.batch_size]
    rows = _project_rows(args.dataset, rows, args.fields)
    extra = {}
    missing = []
    if has_more and rows:
        extra["next_cursor"] = _encode_cursor(args, principal, rows[-1])
        extra["paged"] = True
        missing = [{"reason": "结果超过当前批次，可使用 next_cursor 继续读取", "code": "next_cursor"}]
    envelope = _envelope("partial" if has_more else "complete" if rows else "waiting_for_data",
                         args.dataset, args, rows, missing=missing, extra=extra)
    # Source connection is closed before this independent Agent snapshot write.
    ref = store.save_result(
        principal, envelope, rows, kind="dataset_rows", required_resources=resources, account_scope=[]
    )
    return store.load_result(principal, ref).envelope


def _coverage_query(dataset: str) -> tuple[str, tuple]:
    definition = _definition(dataset)
    sql = f"SELECT COUNT(*) AS row_count, MIN({definition['date_field']}) AS first_observation, MAX({definition['date_field']}) AS last_observation FROM {definition['from']}"
    return sql, ()


def describe_dataset(principal, dataset: DatasetId) -> ToolEnvelope:
    if dataset not in DATASET_SPECS:
        raise ValueError(f"unsupported_dataset:{dataset}")
    authorize(principal, "data_visualization.display")
    spec = get_dataset_spec(dataset)
    try:
        coverage = _readonly_fetch(*_coverage_query(dataset))[0]
    except DataSourceUnavailable:
        return ToolEnvelope(
            status="temporarily_unavailable",
            captured_at=datetime.now(timezone.utc).replace(microsecond=0),
            calculation_version=VERSION,
            payload={"kind": "dataset_description", "dataset": dataset, "semantic_version": spec.semantic_version},
            missing=[{"reason": "registered_source_unavailable", "code": "source_unavailable"}],
        )
    payload = {
        "kind": "dataset_description", "dataset": dataset, "semantic_version": spec.semantic_version,
        "required_resources": list(spec.required_resources), "fields": list(spec.allowed_fields),
        "filters": list(spec.filter_keys), "dimensions": list(spec.dimension_keys),
        "measures": list(spec.measure_fields), "unit": spec.unit, "grain": spec.grain,
        "missing_rule": spec.missing_rule, "coverage": coverage,
    }
    return ToolEnvelope(
        status="complete", captured_at=datetime.now(timezone.utc).replace(microsecond=0),
        calculation_version=VERSION, payload=payload,
    )


def get_optimal_warrant(principal, args: OptimalWarrantArgs | None = None) -> ToolEnvelope:
    args = args or OptimalWarrantArgs()
    if not isinstance(args, OptimalWarrantArgs):
        args = OptimalWarrantArgs.model_validate(args)
    authorize(principal, "data_visualization.display")
    current_year = date.today().year
    sql = """SELECT r.business_date AS observation_date, r.product, r.port,
                     r.wet_spot_price, r.quality_adjustment, r.brand_adjustment,
                     r.standardized_spot_price, r.futures_series, r.futures_close,
                     r.basis, r.data_status, r.rule_version, r.parameter_version,
                     r.source_workbook_name AS source_file, r.source_workbook_sha256,
                     r.id AS row_ref
              FROM iron_ore_basis_results r
              WHERE r.business_year = ? AND r.data_status = '有效'
                AND r.business_date = (
                    SELECT MAX(x.business_date) FROM iron_ore_basis_results x
                    WHERE x.business_year = ? AND x.data_status = '有效'
                )
              ORDER BY r.basis ASC, r.standardized_spot_price ASC,
                       r.wet_spot_price ASC, r.port ASC, r.product ASC LIMIT 1"""
    try:
        rows = _readonly_fetch(sql, (current_year, current_year))
    except DataSourceUnavailable:
        return ToolEnvelope(
            status="temporarily_unavailable",
            captured_at=datetime.now(timezone.utc).replace(microsecond=0),
            calculation_version=VERSION,
            payload={"kind": "optimal_warrant", "scope": args.scope},
            missing=[{"reason": "registered_source_unavailable", "code": "source_unavailable"}],
        )
    rows = [_normalize_row(row, "iron_ore_basis") for row in rows]
    envelope = _envelope(
        "complete" if rows else "waiting_for_data", "iron_ore_basis",
        DatasetQuery(dataset="iron_ore_basis", mode="latest", fields=["basis", "standardized_spot_price", "wet_spot_price", "port", "product"]),
        rows,
        extra={"kind": "optimal_warrant", "scope": args.scope, "current_year": current_year},
    )
    if not rows:
        return envelope
    ref = store.save_result(principal, envelope, rows, kind="dataset_rows", required_resources=["data_visualization.display"], account_scope=[])
    return store.load_result(principal, ref).envelope


__all__ = ["VERSION", "query_dataset", "describe_dataset", "get_optimal_warrant", "DataSourceUnavailable"]
