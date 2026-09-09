"""Strict model-facing contracts; execution identity is never a tool argument."""
from dataclasses import dataclass
from datetime import date as CalendarDate, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class FactQuery(StrictModel):
    as_of: AsOf = Field(default_factory=AsOf)
    asset_type: Literal["all", "future", "option"] = "all"
    contracts: list[str] = Field(default_factory=list, max_length=50)
    direction: Literal["all", "buy", "sell"] = "all"
    classification: Literal["all", "unclassified", "classified"] = "all"


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
