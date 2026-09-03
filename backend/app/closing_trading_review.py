"""Deterministic, read-only end-of-day option review service.

This module deliberately keeps business calculations independent from model
output and from the write-oriented settlement import workflow.  It reads the
already confirmed settlement facts and returns a typed, evidence-carrying
report for the fixed Phase 1 account and instrument.
"""

from __future__ import annotations

from datetime import datetime
import math
import re
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from . import db
from .permissions import require_permission
from .trading_management import trading_management_current_user


router = APIRouter()

CALCULATION_VERSION = "closing-option-review-v1"
RULE_VERSION = "option-review-rules-v1"
ACCOUNT_CODE = "hongyuan_futures"
ACCOUNT_NAME = "宏源账户"
INSTRUMENT_NAME = "铁矿石期权"
VALUATION_BASIS = "daily_settlement"
VALUATION_NOTE = "持仓浮盈浮亏按日结算单结算价计算；不代表15:00最后成交价。"

ReviewStatus = Literal["complete", "partial", "waiting_for_data", "data_anomaly"]
Freshness = Literal["target_trading_date", "derived_from_monthly", "unavailable"]
PositionAvailability = Literal["confirmed", "confirmed_zero", "derived_from_monthly", "unknown"]
OptionType = Literal["Call", "Put"]
Direction = Literal["买", "卖"]
NetDirection = Literal["净买", "净卖", "净平"]
RatioInterpretation = Literal["suitable", "not_suitable", "not_applicable"]

_OPTION_RE = re.compile(
    r"^(?P<product>[A-Za-z]+)(?P<month>\d{3,4})-(?P<kind>[cpCP])-(?P<strike>\d+(?:\.\d+)?)$"
)


class OptionReviewRequest(BaseModel):
    trading_date: str = Field(min_length=8, max_length=10)

    @field_validator("trading_date")
    @classmethod
    def normalize_trading_date(cls, value: str) -> str:
        compact = value.strip().replace("-", "")
        if not re.fullmatch(r"\d{8}", compact):
            raise ValueError("交易日期必须是YYYYMMDD或YYYY-MM-DD")
        try:
            datetime.strptime(compact, "%Y%m%d")
        except ValueError as exc:
            raise ValueError("交易日期无效") from exc
        return compact


class EvidenceRef(BaseModel):
    ref: str
    source: str
    locator: str


class EvidenceMetadata(BaseModel):
    data_as_of: Optional[str] = None
    source: str
    calculation_version: str = CALCULATION_VERSION
    rule_version: str = RULE_VERSION
    freshness: Freshness
    completeness: str
    warnings: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class NumericFact(BaseModel):
    value: Optional[float] = None
    metadata: EvidenceMetadata


class NetPositionFact(BaseModel):
    direction_label: NetDirection
    lots: NumericFact
    tons: NumericFact
    wan_tons: NumericFact


class OptionPositionDetail(BaseModel):
    contract: str
    strike_price: NumericFact
    quantity_lots: NumericFact
    floating_pnl: NumericFact


class OptionPositionGroup(BaseModel):
    expiry_month: str
    option_type: OptionType
    direction: Direction
    strike_min: NumericFact
    strike_max: NumericFact
    quantity_lots: NumericFact
    floating_pnl: NumericFact
    contract_count: NumericFact
    details: list[OptionPositionDetail]


class OptionPnlAttribution(BaseModel):
    expiry_month: str
    option_type: OptionType
    direction: Direction
    realized_close_pnl: NumericFact
    contribution_ratio: NumericFact
    ratio_interpretation: RatioInterpretation


class OptionDailyReviewResponse(BaseModel):
    status: ReviewStatus
    trading_date: str
    account_name: Literal["宏源账户"]
    instrument: Literal["铁矿石期权"]
    valuation_basis: Literal["daily_settlement"]
    valuation_note: str
    position_availability: PositionAvailability
    call_net: NetPositionFact
    put_net: NetPositionFact
    position_groups: list[OptionPositionGroup]
    realized_close_pnl: NumericFact
    unrealized_pnl: NumericFact
    pnl_attribution: list[OptionPnlAttribution]
    metadata: EvidenceMetadata
    warnings: list[str] = Field(default_factory=list)
    summary_text: str


def _dedupe_text(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result


def _dedupe_refs(values: list[EvidenceRef]) -> list[EvidenceRef]:
    result: list[EvidenceRef] = []
    seen: set[tuple[str, str, str]] = set()
    for value in values:
        key = (value.ref, value.source, value.locator)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _normalized_as_of(value: Any) -> Optional[str]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat(timespec="seconds")
    except ValueError:
        return raw.split(".", 1)[0]


def _metadata(
    *,
    source: str,
    freshness: Freshness,
    completeness: str,
    warnings: list[str],
    evidence_refs: list[EvidenceRef],
    data_as_of: Any = None,
) -> EvidenceMetadata:
    return EvidenceMetadata(
        data_as_of=_normalized_as_of(data_as_of),
        source=source,
        freshness=freshness,
        completeness=completeness,
        warnings=_dedupe_text(warnings),
        evidence_refs=_dedupe_refs(evidence_refs),
    )


def _numeric(
    value: Any,
    *,
    source: str,
    freshness: Freshness,
    completeness: str,
    warnings: list[str],
    evidence_refs: list[EvidenceRef],
    data_as_of: Any = None,
) -> NumericFact:
    number = None if value is None else round(float(value), 8)
    if number is not None and abs(number) < 0.000000005:
        number = 0.0
    return NumericFact(
        value=number,
        metadata=_metadata(
            source=source,
            freshness=freshness,
            completeness=completeness,
            warnings=warnings,
            evidence_refs=evidence_refs,
            data_as_of=data_as_of,
        ),
    )


def _date_label(compact: str) -> str:
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"


def _option_parts(contract: Any) -> Optional[dict[str, Any]]:
    match = _OPTION_RE.fullmatch(str(contract or "").strip())
    if not match or match.group("product").lower() != "i":
        return None
    kind = "Call" if match.group("kind").lower() == "c" else "Put"
    return {
        "expiry_month": match.group("month"),
        "option_type": kind,
        "strike_price": float(match.group("strike")),
    }


def _normalize_exchange(value: Any) -> str:
    return str(value or "").strip().lower().replace("交易所", "")


def _batch_ref(batch: dict[str, Any]) -> EvidenceRef:
    statement_type = str(batch.get("statement_type") or "statement")
    range_start = str(batch.get("range_start") or "")
    range_end = str(batch.get("range_end") or "")
    return EvidenceRef(
        ref=f"settlement_batch:{batch['id']}",
        source=f"trading_import_batches.{statement_type}",
        locator=f"{statement_type}:{range_start}-{range_end}",
    )


def _source_ref(row: dict[str, Any]) -> Optional[EvidenceRef]:
    source_row_id = row.get("source_row_id")
    if source_row_id is None or row.get("source_file") is None:
        return None
    return EvidenceRef(
        ref=f"source_row:{source_row_id}",
        source="trading_source_rows",
        locator=f"{row.get('source_type') or 'fact'}:{row.get('source_row_no')}",
    )


def _spec_ref(row: dict[str, Any]) -> Optional[EvidenceRef]:
    spec_id = (row.get("contract_spec") or {}).get("id")
    if spec_id is None:
        return None
    return EvidenceRef(
        ref=f"contract_spec:{spec_id}",
        source="trading_contract_specs",
        locator=f"{row.get('exchange')}:{row.get('contract')}:{row.get('asset_type')}",
    )


def _refs_for_rows(rows: list[dict[str, Any]], batches: list[dict[str, Any]]) -> list[EvidenceRef]:
    refs = [_batch_ref(batch) for batch in batches]
    for row in rows:
        source_ref = _source_ref(row)
        if source_ref:
            refs.append(source_ref)
        spec_ref = _spec_ref(row)
        if spec_ref:
            refs.append(spec_ref)
    return _dedupe_refs(refs)


def _select(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cursor = conn.cursor()
    return [dict(row) for row in db._exec(cursor, sql, params).fetchall()]


def _query_account_id(conn: Any) -> Optional[int]:
    rows = _select(
        conn,
        "SELECT id FROM trading_accounts WHERE account_code = ? AND is_active = 1",
        (ACCOUNT_CODE,),
    )
    return int(rows[0]["id"]) if rows else None


def _query_batches(conn: Any, account_id: int, trading_date: str) -> dict[str, list[dict[str, Any]]]:
    rows = _select(
        conn,
        """
        SELECT b.*
        FROM trading_import_batches b
        WHERE b.account_id = ?
          AND b.status = 'active'
          AND COALESCE(b.range_start, '') <= ?
          AND COALESCE(b.range_end, '') >= ?
          AND b.statement_type IN ('daily', 'monthly')
          AND (b.statement_type <> 'daily' OR b.position_snapshot_date = ?)
        ORDER BY COALESCE(b.source_priority, 0) DESC, b.id DESC
        """,
        (account_id, trading_date, trading_date, trading_date),
    )
    result = {"daily": [], "monthly": []}
    for row in rows:
        statement_type = str(row.get("statement_type") or "")
        if statement_type in result:
            result[statement_type].append(row)
    return result


def _query_positions(
    conn: Any,
    account_id: int,
    batch_id: int,
    trading_date: str,
) -> list[dict[str, Any]]:
    return _select(
        conn,
        """
        SELECT ps.id, ps.identity_id, ps.batch_id, ps.source_row_id,
               ps.snapshot_date, ps.snapshot_time, ps.exchange, ps.contract,
               ps.asset_type, ps.direction, ps.quantity, ps.average_price,
               ps.valuation_price, ps.floating_pnl, ps.valuation_status,
               ps.verification_status, b.created_at AS batch_created_at,
               b.confirmed_at AS batch_confirmed_at,
               sr.id AS joined_source_row_id, sr.source_type, sr.source_file,
               sr.source_row_no
        FROM trading_position_snapshots ps
        JOIN trading_fact_identities fi
          ON fi.id = ps.identity_id AND fi.account_id = ?
        JOIN trading_import_batches b
          ON b.id = ps.batch_id AND b.account_id = ? AND b.status = 'active'
        LEFT JOIN trading_source_rows sr
          ON sr.id = ps.source_row_id AND sr.batch_id = ps.batch_id
        WHERE ps.batch_id = ?
          AND ps.snapshot_date = ?
          AND ps.asset_type = 'option'
          AND ps.is_current = 1
        ORDER BY ps.contract, ps.direction, ps.id
        """,
        (account_id, account_id, batch_id, trading_date),
    )


def _query_closes(
    conn: Any,
    account_id: int,
    batch_id: int,
    trading_date: str,
) -> list[dict[str, Any]]:
    return _select(
        conn,
        """
        SELECT cf.id, cf.identity_id, cf.batch_id, cf.source_row_id,
               cf.open_date, cf.close_date, cf.exchange, cf.contract,
               cf.asset_type, cf.open_side, cf.close_side, cf.quantity,
               cf.open_price, cf.close_price, cf.fact_close_pnl,
               cf.matched_fee, cf.settlement_type, cf.is_current,
               cf.fee_status, cf.verification_status,
               b.created_at AS batch_created_at, b.confirmed_at AS batch_confirmed_at,
               sr.id AS joined_source_row_id, sr.source_type, sr.source_file,
               sr.source_row_no
        FROM trading_close_facts cf
        JOIN trading_fact_identities fi
          ON fi.id = cf.identity_id AND fi.account_id = ?
        JOIN trading_import_batches b
          ON b.id = cf.batch_id AND b.account_id = ? AND b.status = 'active'
        LEFT JOIN trading_source_rows sr
          ON sr.id = cf.source_row_id AND sr.batch_id = cf.batch_id
        WHERE cf.batch_id = ?
          AND cf.close_date = ?
          AND cf.asset_type = 'option'
          AND cf.settlement_type = 'trade_close'
          AND cf.is_current = 1
        ORDER BY cf.contract, cf.id
        """,
        (account_id, account_id, batch_id, trading_date),
    )


def _query_source_differences(
    conn: Any,
    account_id: int,
    batch_id: int,
) -> list[dict[str, Any]]:
    return _select(
        conn,
        """
        SELECT d.id, d.identity_id, d.fact_type, d.old_batch_id,
               d.new_batch_id, d.diff_json
        FROM trading_fact_source_differences d
        JOIN trading_fact_identities fi
          ON fi.id = d.identity_id AND fi.account_id = ?
        WHERE d.new_batch_id = ?
        ORDER BY d.id
        """,
        (account_id, batch_id),
    )


def _query_contract_specs(conn: Any) -> dict[str, dict[str, Any]]:
    rows = _select(
        conn,
        """
        SELECT id, exchange, product_code, asset_type,
               contract_multiplier, price_tick, source
        FROM trading_contract_specs
        WHERE is_active = 1 AND asset_type = 'option'
          AND LOWER(product_code) = 'i'
        ORDER BY id
        """,
    )
    specs: dict[str, dict[str, Any]] = {}
    for row in rows:
        specs[_normalize_exchange(row.get("exchange"))] = row
    return specs


def _difference_refs(rows: list[dict[str, Any]]) -> list[EvidenceRef]:
    return [
        EvidenceRef(
            ref=f"source_difference:{row['id']}",
            source="trading_fact_source_differences",
            locator=f"{row.get('fact_type')}:{row.get('identity_id')}",
        )
        for row in rows
    ]


def _batch_as_of(batch: dict[str, Any]) -> Any:
    return batch.get("confirmed_at") or batch.get("created_at")


def _validate_rows(
    rows: list[dict[str, Any]],
    specs: dict[str, dict[str, Any]],
    warnings: list[str],
    *,
    row_kind: Literal["position", "close"],
) -> tuple[list[dict[str, Any]], bool]:
    """Attach parsed contract/spec data and report whether any row is unsafe."""
    valid: list[dict[str, Any]] = []
    has_invalid = False
    side_key = "direction" if row_kind == "position" else "open_side"
    label = "持仓" if row_kind == "position" else "平仓"
    for source_row in rows:
        row = dict(source_row)
        parts = _option_parts(row.get("contract"))
        if parts is None:
            warnings.append(f"{label}合约无法解析为铁矿石期权：{row.get('contract')}")
            has_invalid = True
            continue
        direction = str(row.get(side_key) or "").strip()
        if direction not in {"买", "卖"}:
            warnings.append(f"{label}记录缺少有效买卖方向：{row.get('contract')}")
            has_invalid = True
            continue
        try:
            quantity = float(row.get("quantity"))
        except (TypeError, ValueError):
            quantity = 0.0
        if not math.isfinite(quantity) or quantity <= 0:
            warnings.append(f"{label}记录手数必须大于零：{row.get('contract')}")
            has_invalid = True
            continue
        if row_kind == "position":
            try:
                average_price = float(row.get("average_price"))
                valuation_price = row.get("valuation_price")
                if not math.isfinite(average_price):
                    raise ValueError
                if valuation_price is not None and not math.isfinite(float(valuation_price)):
                    raise ValueError
            except (TypeError, ValueError):
                warnings.append(f"持仓价格证据无效：{row.get('contract')}")
                has_invalid = True
                continue
        else:
            try:
                if not math.isfinite(float(row.get("fact_close_pnl"))):
                    raise ValueError
                if not math.isfinite(float(row.get("open_price"))) or not math.isfinite(float(row.get("close_price"))):
                    raise ValueError
            except (TypeError, ValueError):
                warnings.append(f"平仓价格或真实平仓盈亏证据无效：{row.get('contract')}")
                has_invalid = True
                continue
        spec = specs.get(_normalize_exchange(row.get("exchange")))
        try:
            multiplier = float(spec.get("contract_multiplier")) if spec else 0.0
        except (TypeError, ValueError):
            multiplier = 0.0
        if spec is None or not math.isfinite(multiplier) or multiplier <= 0:
            warnings.append(f"缺少铁矿石期权合约乘数配置：{row.get('exchange')}")
            has_invalid = True
            continue
        if row.get("source_file") is None or row.get("joined_source_row_id") is None:
            warnings.append(f"关键证据缺失：{label}记录未关联来源行：{row.get('contract')}")
            has_invalid = True
            continue
        verification_status = str(row.get("verification_status") or "")
        if verification_status not in {"matched", "file_imported"}:
            warnings.append(f"关键证据尚未完成核验：{label}记录：{row.get('contract')}")
            has_invalid = True
            continue
        row["parts"] = parts
        row["contract_spec"] = spec
        row["review_direction"] = direction
        valid.append(row)
    return valid, has_invalid


def _position_pnl(row: dict[str, Any]) -> Optional[float]:
    valuation_price = row.get("valuation_price")
    average_price = row.get("average_price")
    if valuation_price is None or average_price is None:
        return None
    multiplier = float(row["contract_spec"]["contract_multiplier"])
    quantity = float(row["quantity"])
    difference = float(valuation_price) - float(average_price)
    if row["review_direction"] == "卖":
        difference = -difference
    return difference * quantity * multiplier


def _empty_net(
    *,
    option_type: OptionType,
    can_confirm_zero: bool,
    freshness: Freshness,
    completeness: str,
    warnings: list[str],
    evidence_refs: list[EvidenceRef],
    data_as_of: Any,
) -> NetPositionFact:
    value = 0 if can_confirm_zero else None
    metadata_args = {
        "source": "trading_position_snapshots",
        "freshness": freshness,
        "completeness": completeness,
        "warnings": warnings,
        "evidence_refs": evidence_refs,
        "data_as_of": data_as_of,
    }
    return NetPositionFact(
        direction_label="净平",
        lots=_numeric(value, **metadata_args),
        tons=_numeric(value, **metadata_args),
        wan_tons=_numeric(value, **metadata_args),
    )


def _build_net(
    rows: list[dict[str, Any]],
    *,
    option_type: OptionType,
    can_confirm_zero: bool,
    freshness: Freshness,
    completeness: str,
    warnings: list[str],
    evidence_refs: list[EvidenceRef],
    data_as_of: Any,
) -> NetPositionFact:
    selected = [row for row in rows if row["parts"]["option_type"] == option_type]
    if not selected and not can_confirm_zero:
        return _empty_net(
            option_type=option_type,
            can_confirm_zero=False,
            freshness=freshness,
            completeness=completeness,
            warnings=warnings,
            evidence_refs=evidence_refs,
            data_as_of=data_as_of,
        )
    signed_lots = 0.0
    signed_tons = 0.0
    for row in selected:
        sign = 1 if row["review_direction"] == "卖" else -1
        quantity = float(row["quantity"])
        multiplier = float(row["contract_spec"]["contract_multiplier"])
        signed_lots += sign * quantity
        signed_tons += sign * quantity * multiplier
    direction_label: NetDirection
    if signed_lots > 0.00000001:
        direction_label = "净卖"
    elif signed_lots < -0.00000001:
        direction_label = "净买"
    else:
        direction_label = "净平"
    metric_args = {
        "source": "trading_position_snapshots",
        "freshness": freshness,
        "completeness": completeness,
        "warnings": warnings,
        "evidence_refs": evidence_refs,
        "data_as_of": data_as_of,
    }
    return NetPositionFact(
        direction_label=direction_label,
        lots=_numeric(abs(signed_lots), **metric_args),
        tons=_numeric(abs(signed_tons), **metric_args),
        wan_tons=_numeric(abs(signed_tons) / 10000, **metric_args),
    )


def _position_groups(
    rows: list[dict[str, Any]],
    *,
    freshness: Freshness,
    completeness: str,
    warnings: list[str],
    evidence_refs: list[EvidenceRef],
    data_as_of: Any,
) -> tuple[list[OptionPositionGroup], Optional[float]]:
    grouped: dict[tuple[str, OptionType, Direction], list[dict[str, Any]]] = {}
    for row in rows:
        key = (
            str(row["parts"]["expiry_month"]),
            row["parts"]["option_type"],
            row["review_direction"],
        )
        grouped.setdefault(key, []).append(row)

    groups: list[OptionPositionGroup] = []
    total_pnl = 0.0
    has_missing_valuation = False
    for key in sorted(grouped):
        expiry_month, option_type, direction = key
        group_rows = grouped[key]
        group_refs = _refs_for_rows(group_rows, [])
        group_warnings = list(warnings)
        details: list[OptionPositionDetail] = []
        floating_values: list[float] = []
        for row in group_rows:
            pnl = _position_pnl(row)
            if pnl is None:
                has_missing_valuation = True
                group_warnings.append(f"历史估值缺失：{row.get('contract')}")
            else:
                floating_values.append(pnl)
                total_pnl += pnl
            detail_args = {
                "source": "trading_position_snapshots",
                "freshness": freshness,
                "completeness": completeness,
                "warnings": group_warnings,
                "evidence_refs": group_refs,
                "data_as_of": data_as_of,
            }
            details.append(
                OptionPositionDetail(
                    contract=str(row["contract"]),
                    strike_price=_numeric(row["parts"]["strike_price"], **detail_args),
                    quantity_lots=_numeric(row["quantity"], **detail_args),
                    floating_pnl=_numeric(pnl, **detail_args),
                )
            )
        group_args = {
            "source": "trading_position_snapshots",
            "freshness": freshness,
            "completeness": completeness,
            "warnings": group_warnings,
            "evidence_refs": group_refs,
            "data_as_of": data_as_of,
        }
        group_pnl = sum(floating_values) if len(floating_values) == len(group_rows) else None
        quantities = [float(row["quantity"]) for row in group_rows]
        strikes = [float(row["parts"]["strike_price"]) for row in group_rows]
        groups.append(
            OptionPositionGroup(
                expiry_month=expiry_month,
                option_type=option_type,
                direction=direction,
                strike_min=_numeric(min(strikes), **group_args),
                strike_max=_numeric(max(strikes), **group_args),
                quantity_lots=_numeric(sum(quantities), **group_args),
                floating_pnl=_numeric(group_pnl, **group_args),
                contract_count=_numeric(len(group_rows), **group_args),
                details=details,
            )
        )
    return groups, None if has_missing_valuation else total_pnl


def _close_attribution(
    rows: list[dict[str, Any]],
    *,
    freshness: Freshness,
    completeness: str,
    warnings: list[str],
    evidence_refs: list[EvidenceRef],
    data_as_of: Any,
) -> tuple[list[OptionPnlAttribution], float]:
    grouped: dict[tuple[str, OptionType, Direction], list[dict[str, Any]]] = {}
    for row in rows:
        key = (
            str(row["parts"]["expiry_month"]),
            row["parts"]["option_type"],
            row["review_direction"],
        )
        grouped.setdefault(key, []).append(row)

    group_values = {
        key: sum(float(row["fact_close_pnl"]) for row in group_rows)
        for key, group_rows in grouped.items()
    }
    total_pnl = sum(group_values.values())
    denominator = sum(abs(value) for value in group_values.values())
    attributions: list[OptionPnlAttribution] = []
    for key in sorted(grouped):
        expiry_month, option_type, direction = key
        group_rows = grouped[key]
        group_value = group_values[key]
        group_refs = _refs_for_rows(group_rows, [])
        contribution_ratio = None
        ratio_interpretation: RatioInterpretation = "not_applicable"
        group_warnings = list(warnings)
        if abs(total_pnl) > 0.00000001:
            contribution_ratio = group_value / total_pnl
            ratio_interpretation = "suitable"
            if denominator > 0.00000001 and abs(total_pnl) / denominator < 0.5:
                ratio_interpretation = "not_suitable"
                group_warnings.append("平仓盈亏正负抵销，贡献比例不适合单独解读")
        elif denominator > 0.00000001:
            ratio_interpretation = "not_suitable"
            group_warnings.append("平仓盈亏正负抵销，贡献比例不适合单独解读")
        metric_args = {
            "source": "trading_close_facts",
            "freshness": freshness,
            "completeness": completeness,
            "warnings": group_warnings,
            "evidence_refs": group_refs,
            "data_as_of": data_as_of,
        }
        attributions.append(
            OptionPnlAttribution(
                expiry_month=expiry_month,
                option_type=option_type,
                direction=direction,
                realized_close_pnl=_numeric(group_value, **metric_args),
                contribution_ratio=_numeric(contribution_ratio, **metric_args),
                ratio_interpretation=ratio_interpretation,
            )
        )
    return attributions, total_pnl


def _format_number(value: Optional[float]) -> str:
    if value is None:
        return "待核"
    if abs(value - round(value)) < 0.00000001:
        return str(int(round(value)))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _net_summary(label: str, net: NetPositionFact) -> str:
    return f"{label}{net.direction_label}{_format_number(net.lots.value)}手"


def _summary_text(
    *,
    status: ReviewStatus,
    trading_date: str,
    position_availability: PositionAvailability,
    call_net: NetPositionFact,
    put_net: NetPositionFact,
    position_groups: list[OptionPositionGroup],
    realized_close_pnl: NumericFact,
    unrealized_pnl: NumericFact,
    warnings: list[str],
) -> str:
    date_label = _date_label(trading_date)
    if status == "waiting_for_data":
        return f"{date_label} 收盘交易复盘暂无法生成：等待日结算单，不能据此确认持仓为零。"
    if status == "data_anomaly":
        return f"{date_label} 收盘交易复盘数据异常：关键来源或证据存在问题，数值结论已暂停。"
    if position_availability == "confirmed_zero":
        position_text = "已确认无期权持仓"
    elif position_availability == "derived_from_monthly":
        position_text = "持仓来自月结单推导，不能视为目标日完整日结快照"
    else:
        position_text = "持仓已取得"
    text = (
        f"{date_label} {position_text}；"
        f"{_net_summary('Call', call_net)}，{_net_summary('Put', put_net)}；"
        f"真实平仓盈亏{_format_number(realized_close_pnl.value)}，"
        f"持仓浮盈浮亏{_format_number(unrealized_pnl.value)}。"
    )
    for group in position_groups:
        text += (
            f" {group.expiry_month} {group.direction}{group.option_type}："
            f"行权价{_format_number(group.strike_min.value)}—{_format_number(group.strike_max.value)}，"
            f"共{_format_number(group.quantity_lots.value)}手，"
            f"持仓浮盈浮亏{_format_number(group.floating_pnl.value)}元。"
        )
    if status == "partial":
        text += "报告为部分完成，仍有数据口径或历史估值限制。"
    if warnings:
        text += f"提示：{warnings[0]}。"
    return text


def _blocked_report(
    *,
    status: Literal["waiting_for_data", "data_anomaly"],
    trading_date: str,
    position_availability: PositionAvailability,
    source: str,
    freshness: Freshness,
    warnings: list[str],
    evidence_refs: list[EvidenceRef],
    data_as_of: Any = None,
) -> OptionDailyReviewResponse:
    clean_warnings = _dedupe_text(warnings)
    metric_args = {
        "freshness": freshness,
        "completeness": status,
        "warnings": clean_warnings,
        "evidence_refs": evidence_refs,
        "data_as_of": data_as_of,
    }
    call_net = _empty_net(
        option_type="Call",
        can_confirm_zero=False,
        **metric_args,
    )
    put_net = _empty_net(
        option_type="Put",
        can_confirm_zero=False,
        **metric_args,
    )
    realized = _numeric(None, source="trading_close_facts", **metric_args)
    unrealized = _numeric(None, source="trading_position_snapshots", **metric_args)
    metadata = _metadata(source=source, **metric_args)
    summary = _summary_text(
        status=status,
        trading_date=trading_date,
        position_availability=position_availability,
        call_net=call_net,
        put_net=put_net,
        position_groups=[],
        realized_close_pnl=realized,
        unrealized_pnl=unrealized,
        warnings=clean_warnings,
    )
    return OptionDailyReviewResponse(
        status=status,
        trading_date=trading_date,
        account_name=ACCOUNT_NAME,
        instrument=INSTRUMENT_NAME,
        valuation_basis=VALUATION_BASIS,
        valuation_note=VALUATION_NOTE,
        position_availability=position_availability,
        call_net=call_net,
        put_net=put_net,
        position_groups=[],
        realized_close_pnl=realized,
        unrealized_pnl=unrealized,
        pnl_attribution=[],
        metadata=metadata,
        warnings=clean_warnings,
        summary_text=summary,
    )


def build_option_daily_review(trading_date: str) -> OptionDailyReviewResponse:
    """Build the fixed Hongyuan iron-ore option report using read-only SQL."""
    normalized_date = OptionReviewRequest(trading_date=trading_date).trading_date
    with db.connect() as conn:
        account_id = _query_account_id(conn)
        if account_id is None:
            return _blocked_report(
                status="waiting_for_data",
                trading_date=normalized_date,
                position_availability="unknown",
                source="trading_accounts",
                freshness="unavailable",
                warnings=["未找到可用的宏源账户资料，等待数据准备。"],
                evidence_refs=[],
            )

        batches = _query_batches(conn, account_id, normalized_date)
        daily_batches = batches["daily"]
        monthly_batches = batches["monthly"]
        if not daily_batches and not monthly_batches:
            return _blocked_report(
                status="waiting_for_data",
                trading_date=normalized_date,
                position_availability="unknown",
                source="trading_import_batches",
                freshness="unavailable",
                warnings=[f"缺少{_date_label(normalized_date)}日结算单，等待数据。"],
                evidence_refs=[],
            )

        selected_batch = daily_batches[0] if daily_batches else monthly_batches[0]
        is_daily = bool(daily_batches)
        freshness: Freshness = "target_trading_date" if is_daily else "derived_from_monthly"
        position_completeness = "complete" if is_daily else "partial"
        position_rows = _query_positions(
            conn, account_id, int(selected_batch["id"]), normalized_date
        )
        close_rows = _query_closes(
            conn, account_id, int(selected_batch["id"]), normalized_date
        )
        differences = _query_source_differences(
            conn, account_id, int(selected_batch["id"])
        )
        specs = _query_contract_specs(conn)
        warnings: list[str] = []
        if not is_daily:
            warnings.append("目标日期缺少日结算单，本次持仓数据来自覆盖目标日期的月结单。")
        if differences:
            warnings.append("来源冲突：关键事实存在多个来源版本，已暂停数值结论。")

        valid_positions, invalid_positions = _validate_rows(
            position_rows, specs, warnings, row_kind="position"
        )
        valid_closes, invalid_closes = _validate_rows(
            close_rows, specs, warnings, row_kind="close"
        )

        if valid_positions and any(row.get("valuation_price") is None for row in valid_positions):
            warnings.append("历史估值缺失：无法计算全部持仓浮盈浮亏。")
        if valid_closes:
            warnings.append("真实平仓盈亏按已确认事实计算，不扣手续费。")

        position_availability: PositionAvailability
        if is_daily:
            position_availability = "confirmed_zero" if not position_rows else "confirmed"
        else:
            position_availability = "derived_from_monthly"
        all_refs = _refs_for_rows(position_rows + close_rows, [selected_batch])
        all_refs.extend(_difference_refs(differences))
        all_refs = _dedupe_refs(all_refs)
        data_as_of = _batch_as_of(selected_batch)
        if differences or invalid_positions or invalid_closes:
            return _blocked_report(
                status="data_anomaly",
                trading_date=normalized_date,
                position_availability=position_availability,
                source=f"trading_import_batches.{selected_batch.get('statement_type')}",
                freshness=freshness,
                warnings=warnings,
                evidence_refs=all_refs,
                data_as_of=data_as_of,
            )

        position_refs = _refs_for_rows(valid_positions, [selected_batch])
        close_refs = _refs_for_rows(valid_closes, [selected_batch])
        groups, derived_unrealized = _position_groups(
            valid_positions,
            freshness=freshness,
            completeness=position_completeness,
            warnings=warnings,
            evidence_refs=position_refs,
            data_as_of=data_as_of,
        )
        call_net = _build_net(
            valid_positions,
            option_type="Call",
            can_confirm_zero=is_daily and not valid_positions,
            freshness=freshness,
            completeness=position_completeness,
            warnings=warnings,
            evidence_refs=position_refs,
            data_as_of=data_as_of,
        )
        put_net = _build_net(
            valid_positions,
            option_type="Put",
            can_confirm_zero=is_daily and not valid_positions,
            freshness=freshness,
            completeness=position_completeness,
            warnings=warnings,
            evidence_refs=position_refs,
            data_as_of=data_as_of,
        )
        if not valid_positions and is_daily:
            derived_unrealized = 0.0
        unrealized = _numeric(
            derived_unrealized,
            source="trading_position_snapshots",
            freshness=freshness,
            completeness=position_completeness,
            warnings=warnings,
            evidence_refs=position_refs,
            data_as_of=data_as_of,
        )
        attributions, realized_value = _close_attribution(
            valid_closes,
            freshness=freshness,
            completeness="complete",
            warnings=warnings,
            evidence_refs=close_refs,
            data_as_of=data_as_of,
        )
        realized = _numeric(
            realized_value,
            source="trading_close_facts",
            freshness=freshness,
            completeness="complete",
            warnings=warnings,
            evidence_refs=close_refs,
            data_as_of=data_as_of,
        )
        status: ReviewStatus = "complete"
        if not is_daily or derived_unrealized is None:
            status = "partial"
        report_warnings = _dedupe_text(warnings)
        metadata = _metadata(
            source=f"trading_import_batches.{selected_batch.get('statement_type')}",
            freshness=freshness,
            completeness=status,
            warnings=report_warnings,
            evidence_refs=all_refs,
            data_as_of=data_as_of,
        )
        summary = _summary_text(
            status=status,
            trading_date=normalized_date,
            position_availability=position_availability,
            call_net=call_net,
            put_net=put_net,
            position_groups=groups,
            realized_close_pnl=realized,
            unrealized_pnl=unrealized,
            warnings=report_warnings,
        )
        return OptionDailyReviewResponse(
            status=status,
            trading_date=normalized_date,
            account_name=ACCOUNT_NAME,
            instrument=INSTRUMENT_NAME,
            valuation_basis=VALUATION_BASIS,
            valuation_note=VALUATION_NOTE,
            position_availability=position_availability,
            call_net=call_net,
            put_net=put_net,
            position_groups=groups,
            realized_close_pnl=realized,
            unrealized_pnl=unrealized,
            pnl_attribution=attributions,
            metadata=metadata,
            warnings=report_warnings,
            summary_text=summary,
        )


@router.get("/closing-trading-review/options/daily-summary", response_model=OptionDailyReviewResponse)
def get_option_daily_summary(
    trading_date: str = Query(..., min_length=8, max_length=10),
    user: dict = Depends(trading_management_current_user),
) -> OptionDailyReviewResponse:
    try:
        normalized_date = OptionReviewRequest(trading_date=trading_date).trading_date
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    require_permission(user, "trading.options", "view")
    return build_option_daily_review(normalized_date)
