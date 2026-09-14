"""Strict model-facing contracts; execution identity is never a tool argument."""
from dataclasses import dataclass
from datetime import date as CalendarDate, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


@dataclass(frozen=True)
class Principal:
    user_id: int
    channel: Literal["web", "wecom"]
    conversation_id: int
    account_ids: tuple[int, ...]
    execution_id: UUID


class AsOf(StrictModel):
    mode: Literal["latest", "settlement_date"] = "latest"
    date: CalendarDate | None = None

    @model_validator(mode="after")
    def coherent_date(self):
        if (self.mode == "settlement_date") != (self.date is not None):
            raise ValueError("Only settlement_date requires a date")
        return self


class PositionFilter(StrictModel):
    """Composable contract attributes; values are data, never expressions."""

    products: list[str] = Field(default_factory=list, max_length=20)
    contract_months: list[str] = Field(default_factory=list, max_length=20)
    option_type: Literal["all", "call", "put"] = "all"
    strike_min: Decimal | None = None
    strike_max: Decimal | None = None
    strike_min_inclusive: bool = True
    strike_max_inclusive: bool = True

    @field_validator("products", "contract_months")
    @classmethod
    def normalize_values(cls, values):
        result = []
        for value in values:
            text = str(value).strip().lower()
            if not text:
                raise ValueError("筛选值不能为空")
            result.append(text)
        return list(dict.fromkeys(result))

    @field_validator("contract_months")
    @classmethod
    def validate_months(cls, values):
        if any(not text.isdigit() or len(text) not in {3, 4} for text in values):
            raise ValueError("合约月份必须是三位或四位数字")
        return values

    @model_validator(mode="after")
    def coherent_strike_range(self):
        if self.strike_min is not None and self.strike_max is not None:
            if self.strike_min > self.strike_max:
                raise ValueError("行权价下限不能大于上限")
            if self.strike_min == self.strike_max and not (self.strike_min_inclusive and self.strike_max_inclusive):
                raise ValueError("相同行权价必须包含边界")
        return self


class FactQuery(StrictModel):
    as_of: AsOf = Field(default_factory=AsOf)
    asset_type: Literal["all", "future", "option"] = "all"
    contracts: list[str] = Field(default_factory=list, max_length=50)
    direction: Literal["all", "buy", "sell"] = "all"
    classification: Literal["all", "unclassified", "classified"] = "all"
    valuation_mode: Literal["auto", "quantity_only", "mark_to_market"] = "auto"
    required_metrics: list[Literal[
        "count", "quantity", "floating_pnl", "gross_quantity", "gross_buy_quantity",
        "gross_sell_quantity", "net_quantity", "net_sell_quantity", "net_tons",
        "net_signed_tons", "net_wan_tons", "strike_min", "strike_max",
        "covered_rows", "eligible_rows",
    ]] = Field(default_factory=list, max_length=8)
    filters: PositionFilter = Field(default_factory=PositionFilter)
    presentation: Literal["auto", "text", "table", "chart"] = "auto"


class MetricValue(StrictModel):
    value: str | None
    unit: str = Field(min_length=1)
    status: Literal["complete", "partial", "unavailable"]
    covered_rows: int = Field(ge=0)
    eligible_rows: int = Field(ge=0)

    @model_validator(mode="after")
    def valid_coverage(self):
        if self.covered_rows > self.eligible_rows:
            raise ValueError("Coverage exceeds eligible rows")
        if self.value is not None:
            try:
                finite = Decimal(self.value).is_finite()
            except InvalidOperation:
                finite = False
            if not finite:
                raise ValueError("Metric must be a finite decimal string")
        if self.status == "complete" and (self.value is None or self.covered_rows != self.eligible_rows):
            raise ValueError("Complete metrics require a value and full coverage")
        if self.status == "unavailable" and self.value is not None:
            raise ValueError("Unavailable metrics cannot carry a value")
        return self


ResultStatus = Literal["complete", "partial", "waiting_for_data", "data_anomaly",
                       "unsupported", "needs_clarification", "temporarily_unavailable",
                       "result_expired", "not_comparable", "limit_exceeded"]


class ToolEnvelope(StrictModel):
    schema_version: Literal["2.0"] = "2.0"
    status: ResultStatus
    result_ref: UUID | None = None
    snapshot_ref: UUID | None = None
    data_as_of: datetime | None = None
    captured_at: datetime
    quote_times: dict[str, str | None] = Field(default_factory=dict)
    calculation_version: str
    payload: dict = Field(default_factory=dict)
    metrics: dict[str, MetricValue] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    missing: list[dict] = Field(default_factory=list)


class Shock(StrictModel):
    underlying_symbol: str = Field(min_length=1, max_length=64)
    price_change_pct: float
    iv_change_points: float = 0
    days_forward: int = Field(default=0, ge=0)


class PublicQuery(StrictModel):
    text: str = Field(min_length=1, max_length=240)
    freshness: Literal["day", "week", "month", "year", "none"] = "none"
    approved: Literal[True] = True


class StoredResult(StrictModel):
    ref: UUID
    envelope: ToolEnvelope
    rows: list[dict]
    owner_user_id: int
    conversation_id: int
    parent_ref: UUID | None = None
    expires_at: datetime


class ResolvedRequest(StrictModel):
    """Server-owned summary of the request that the answer must cover.

    This is deliberately a small, optional contract.  It records the
    business conditions inferred from the user's words; identity, account
    scope, and authorization remain outside the model-facing contract.
    """

    schema_version: Literal["1.0"] = "1.0"
    domain: Literal["positions", "dataset", "public", "mixed", "general"] = "general"
    source_mode: Literal["internal", "public", "mixed", "general"] = "general"
    filters: dict[str, Any] = Field(default_factory=dict)
    required_metrics: list[str] = Field(default_factory=list, max_length=16)
    group_by: list[str] = Field(default_factory=list, max_length=8)
    presentation: Literal["auto", "text", "table", "chart"] = "auto"
    prohibited_presentations: list[Literal["table", "chart"]] = Field(default_factory=list, max_length=2)
    refresh: bool = False
    inherited_result_refs: list[str] = Field(default_factory=list, max_length=8)


class AnswerCoverage(StrictModel):
    """Server-side coverage record for a validated answer."""

    schema_version: Literal["1.0"] = "1.0"
    status: Literal["complete", "partial", "not_applicable"] = "not_applicable"
    expected_filters: dict[str, Any] = Field(default_factory=dict)
    matched_result_refs: list[str] = Field(default_factory=list, max_length=16)
    covered_metrics: list[str] = Field(default_factory=list, max_length=16)
    missing: list[str] = Field(default_factory=list, max_length=32)


class AnswerParagraph(StrictModel):
    kind: Literal["fact", "scenario", "inference", "knowledge"]
    text: str
    evidence_refs: list[str] = Field(default_factory=list)


class AnswerDraft(StrictModel):
    status: ResultStatus
    paragraphs: list[AnswerParagraph]
    fact_refs: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    clarification: str | None = None
