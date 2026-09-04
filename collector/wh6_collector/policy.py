"""Strict, explicit collection policy received from the bound Staging device."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .version import CLIENT_VERSION, POLICY_SCHEMA_VERSION


MONTH_RE = re.compile(r"^(?P<year>\d{4})-(?P<month>0[1-9]|1[0-2])$")
DATE_RE = re.compile(r"^(?P<year>\d{4})-(?P<month>0[1-9]|1[0-2])-(?P<day>0[1-9]|[12]\d|3[01])$")


def _version_tuple(value: object) -> Tuple[int, ...]:
    text = str(value or "").strip()
    parts = text.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        raise ValueError("采集策略最低版本格式无效")
    return tuple(int(part) for part in parts)


def _parse_date(value: object, field_name: str) -> date:
    text = str(value or "").strip()
    match = DATE_RE.fullmatch(text)
    if not match:
        raise ValueError("采集策略 %s 必须是 ISO 日期" % field_name)
    try:
        return date(int(match.group("year")), int(match.group("month")), int(match.group("day")))
    except ValueError as exc:
        raise ValueError("采集策略 %s 不是有效日期" % field_name) from exc


def _month_bounds(month: str) -> Tuple[date, date]:
    match = MONTH_RE.fullmatch(month)
    if not match:
        raise ValueError("采集策略月份格式无效")
    year = int(match.group("year"))
    month_number = int(match.group("month"))
    start = date(year, month_number, 1)
    if month_number == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month_number + 1, 1)
    return start, date.fromordinal(next_month.toordinal() - 1)


@dataclass(frozen=True)
class ClosedRange:
    month: str
    range_start: str
    range_end: str
    source_batch_id: int

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "ClosedRange":
        if not isinstance(payload, Mapping):
            raise ValueError("采集策略关闭区间格式无效")
        month = str(payload.get("month") or "").strip()
        expected_start, expected_end = _month_bounds(month)
        range_start = _parse_date(payload.get("range_start"), "range_start")
        range_end = _parse_date(payload.get("range_end"), "range_end")
        if range_start != expected_start or range_end != expected_end:
            raise ValueError("采集策略关闭区间必须覆盖月份的完整自然月")
        statement_type = payload.get("statement_type")
        if statement_type is not None and str(statement_type).strip().lower() != "monthly":
            raise ValueError("日结来源不得生成关闭月份策略")
        batch_id = payload.get("source_batch_id")
        if isinstance(batch_id, bool):
            raise ValueError("采集策略来源批次无效")
        try:
            normalized_batch_id = int(batch_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("采集策略来源批次无效") from exc
        if normalized_batch_id <= 0:
            raise ValueError("采集策略来源批次无效")
        return cls(month, range_start.isoformat(), range_end.isoformat(), normalized_batch_id)

    def covers(self, trade_date: date) -> bool:
        return self.range_start <= trade_date.isoformat() <= self.range_end

    def to_payload(self) -> Dict[str, object]:
        return {
            "month": self.month,
            "range_start": self.range_start,
            "range_end": self.range_end,
            "source_batch_id": self.source_batch_id,
        }


@dataclass(frozen=True)
class CollectionPolicy:
    schema_version: int
    policy_revision: str
    minimum_client_version: str
    capabilities: Tuple[str, ...]
    closed_ranges: Tuple[ClosedRange, ...]
    generated_at: str

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "CollectionPolicy":
        if not isinstance(payload, Mapping):
            raise ValueError("采集策略响应格式无效")
        try:
            schema_version = int(payload.get("schema_version"))
        except (TypeError, ValueError) as exc:
            raise ValueError("采集策略 schema 版本无效") from exc
        if schema_version != POLICY_SCHEMA_VERSION:
            raise ValueError("采集策略 schema 版本不受当前客户端支持")

        revision = str(payload.get("policy_revision") or "").strip()
        if not revision or len(revision) > 256:
            raise ValueError("采集策略 revision 无效")
        minimum = str(payload.get("minimum_client_version") or "").strip()
        if _version_tuple(minimum) > _version_tuple(CLIENT_VERSION):
            raise ValueError("采集器版本低于服务端策略要求")

        capabilities = payload.get("capabilities")
        if not isinstance(capabilities, (list, tuple)) or any(
            not isinstance(item, str) or not item.strip() for item in capabilities
        ):
            raise ValueError("采集策略 capabilities 格式无效")
        if len(set(capabilities)) != len(capabilities):
            raise ValueError("采集策略 capabilities 不得重复")

        raw_ranges = payload.get("closed_ranges")
        if not isinstance(raw_ranges, (list, tuple)):
            raise ValueError("采集策略 closed_ranges 格式无效")
        ranges: List[ClosedRange] = []
        seen_months = set()
        for raw_range in raw_ranges:
            closed_range = ClosedRange.from_payload(raw_range)
            if closed_range.month in seen_months:
                raise ValueError("采集策略月份不得重复")
            seen_months.add(closed_range.month)
            ranges.append(closed_range)
        ranges.sort(key=lambda item: item.month)

        generated_at = str(payload.get("generated_at") or "").strip()
        if not generated_at:
            raise ValueError("采集策略 generated_at 缺失")
        try:
            datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("采集策略 generated_at 格式无效") from exc
        return cls(
            schema_version=schema_version,
            policy_revision=revision,
            minimum_client_version=minimum,
            capabilities=tuple(capabilities),
            closed_ranges=tuple(ranges),
            generated_at=generated_at,
        )

    def covers(self, trade_date: str) -> bool:
        text = str(trade_date or "").strip()
        try:
            parsed = _parse_date(text, "trade_date")
        except ValueError:
            return False
        return any(closed_range.covers(parsed) for closed_range in self.closed_ranges)

    def to_payload(self) -> Dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "policy_revision": self.policy_revision,
            "minimum_client_version": self.minimum_client_version,
            "capabilities": list(self.capabilities),
            "closed_ranges": [closed_range.to_payload() for closed_range in self.closed_ranges],
            "generated_at": self.generated_at,
        }
