"""Bounded, read-only access to the registered iron-ore basis result set."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal

from pydantic import Field, model_validator

from .. import db
from ..iron_ore_basis import PORT_ORDER, PRODUCT_ORDER
from . import catalog, store
from .auth import authorize
from .contracts import StrictModel, ToolEnvelope


DATASET = "iron_ore_basis"
SOURCE_TABLE = "iron_ore_basis_results"
VERSION = "iron-ore-basis-read-v1"
# Historical queries are bounded by the row/payload limits below rather than
# by an arbitrary one-year business limit.
MAX_RANGE_DAYS = None
MAX_ROWS = 20000
MARKET_METRICS = ("basis", "futures_close", "wet_spot_price")
SOURCE_FIELDS = (
    "futures_series", "business_year", "business_week", "week_label", "rule_version",
    "parameter_version", "source_workbook_name", "source_workbook_sha256",
)


class MarketSeriesArgs(StrictModel):
    dataset: Literal["iron_ore_basis"]
    start_date: date
    end_date: date
    ports: list[str] = Field(default_factory=list, max_length=8)
    products: list[str] = Field(default_factory=list, max_length=8)
    metrics: list[Literal["basis", "futures_close", "wet_spot_price"]] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_scope(self):
        if self.start_date > self.end_date:
            raise ValueError("开始日期晚于结束日期")
        for field in ("ports", "products", "metrics"):
            values = [str(value).strip() for value in getattr(self, field)]
            if any(not value for value in values):
                raise ValueError("筛选项不能包含空值")
            if len(values) != len(set(values)):
                raise ValueError("筛选项不能重复")
            object.__setattr__(self, field, values)
        unknown_ports = set(self.ports) - set(PORT_ORDER)
        unknown_products = set(self.products) - set(PRODUCT_ORDER)
        if unknown_ports or unknown_products:
            raise ValueError("港口或品种不在已登记期现来源目录中")
        return self


def dataset_catalog() -> dict:
    dataset = catalog.MARKET_DATASETS[DATASET]
    return {
        "dataset": DATASET,
        "source": dataset["source"],
        "dimensions": list(dataset["dimensions"]),
        "metrics": list(dataset["metrics"]),
        "fields": {key: dict(value) for key, value in dataset["fields"].items()},
        "limits": {"date_span_days": MAX_RANGE_DAYS, "row_limit": MAX_ROWS, "ports": 8, "products": 8},
    }


def _read_rows(args: MarketSeriesArgs) -> list[dict]:
    columns = ["business_date", "port", "product", *args.metrics, "data_status", *SOURCE_FIELDS]
    clauses = ["business_date BETWEEN ? AND ?"]
    params: list[object] = [args.start_date.isoformat(), args.end_date.isoformat()]
    for field, values in (("port", args.ports), ("product", args.products)):
        if values:
            clauses.append(f"{field} IN ({','.join('?' for _ in values)})")
            params.extend(values)
    sql = (
        f"SELECT {', '.join(columns)} FROM {SOURCE_TABLE} "
        f"WHERE {' AND '.join(clauses)} ORDER BY business_date ASC, port ASC, product ASC LIMIT {MAX_ROWS + 1}"
    )
    with db.connect() as conn:
        rows = db._exec(conn.cursor(), sql, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def _duplicate_keys(rows: list[dict]) -> set[tuple[object, object, object]]:
    seen: set[tuple[object, object, object]] = set()
    duplicates: set[tuple[object, object, object]] = set()
    for row in rows:
        key = (row.get("business_date"), row.get("port"), row.get("product"))
        if key in seen:
            duplicates.add(key)
        seen.add(key)
    return duplicates


def _missing(rows: list[dict], metrics: list[str]) -> tuple[int, dict[str, int]]:
    by_metric = {metric: 0 for metric in metrics}
    missing_count = 0
    for row in rows:
        row_missing = row.get("data_status") != "有效"
        for metric in metrics:
            if row.get(metric) is None:
                by_metric[metric] += 1
                row_missing = True
        if row_missing:
            missing_count += 1
    return missing_count, by_metric


def _preview(rows: list[dict]) -> list[dict]:
    return [dict(row) for row in rows[:20]]


def capture_market_series(principal, args: MarketSeriesArgs) -> ToolEnvelope:
    """Read saved basis results without invoking imports, sync, or quote sources."""
    if not isinstance(args, MarketSeriesArgs):
        args = MarketSeriesArgs.model_validate(args)
    authorize(principal, "trading.facts")
    authorize(principal, "data_visualization.display")
    rows = _read_rows(args)
    duplicates = _duplicate_keys(rows)
    if duplicates:
        raise ValueError("同日同港同品种存在多个结果版本，当前选择语义不明确")
    missing_count, missing_by_metric = _missing(rows, list(args.metrics))
    latest = max((row.get("business_date") for row in rows if row.get("business_date")), default=None)
    status = "waiting_for_data" if not rows else "partial" if missing_count else "complete"
    payload = {
        "kind": "market_series",
        "dataset": DATASET,
        "source": SOURCE_TABLE,
        "selection": args.model_dump(mode="json"),
        "date_range": [args.start_date.isoformat(), args.end_date.isoformat()],
        "latest_observed_date": latest,
        "count": len(rows),
        "row_count": len(rows),
        "field_catalog": dataset_catalog()["fields"],
        "missing_count": missing_count,
        "missing_by_metric": missing_by_metric,
        "preview": _preview(rows),
        "preview_count": min(len(rows), 20),
        "preview_truncated": len(rows) > 20,
    }
    missing = []
    if missing_count:
        missing.append({"reason": "源表存在非有效状态或请求指标缺失", "count": missing_count})
    if len(rows) > MAX_ROWS:
        payload.update({"preview": [], "preview_count": 0, "preview_truncated": True, "row_count": len(rows)})
        envelope = ToolEnvelope(
            status="limit_exceeded",
            captured_at=datetime.now(timezone.utc).replace(microsecond=0),
            calculation_version=VERSION,
            payload=payload,
            missing=[{"reason": "查询结果超过20000行，请缩小日期或港口、品种范围"}],
        )
        return envelope
    envelope = ToolEnvelope(
        status=status,
        captured_at=datetime.now(timezone.utc).replace(microsecond=0),
        data_as_of=None,
        calculation_version=VERSION,
        payload=payload,
        missing=missing,
    )
    ref = store.save_result(principal, envelope, rows, kind="market_series")
    return store.load_result(principal, ref).envelope


__all__ = ["DATASET", "MARKET_METRICS", "MarketSeriesArgs", "capture_market_series", "dataset_catalog"]
