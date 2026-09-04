"""Bounded closing-review Agent workflow and HTTP boundary.

The model classifies intent only.  Dates, data access, projections, evidence,
and user-facing answers stay in deterministic code.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import re
import threading
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, UUID4, model_validator

from . import closing_review_calendar
from . import closing_review_agent_store as store
from . import closing_trading_review
from .closing_review_model_gateway import (
    ClosingReviewModelProvider,
    FakeClosingReviewProvider,
    IntentRequest,
    IntentResolution,
    ModelGatewayError,
    build_closing_review_provider,
)
from .permissions import require_permission
from .trading_management import trading_management_current_user


router = APIRouter(prefix="/closing-review-agent")

OptionDailyReviewResponse = closing_trading_review.OptionDailyReviewResponse
EvidenceMetadata = closing_trading_review.EvidenceMetadata
EvidenceRef = closing_trading_review.EvidenceRef
NumericFact = closing_trading_review.NumericFact
NetPositionFact = closing_trading_review.NetPositionFact
OptionPositionDetail = closing_trading_review.OptionPositionDetail
OptionPositionGroup = closing_trading_review.OptionPositionGroup
OptionPnlAttribution = closing_trading_review.OptionPnlAttribution
ACCOUNT_NAME = closing_trading_review.ACCOUNT_NAME
INSTRUMENT_NAME = closing_trading_review.INSTRUMENT_NAME

build_option_daily_review = closing_trading_review.build_option_daily_review

SUPPORTED_PROFILES = {
    "option_position_query",
    "option_previous_trading_day_position",
    "option_realized_pnl_query",
    "option_unrealized_pnl_query",
    "option_pnl_fact_attribution",
    "review_data_status_query",
    "effective_rule_query",
    "report_evidence_explanation",
}
CONTROLLED_STATUSES = {
    "needs_clarification",
    "unsupported",
    "temporarily_unavailable",
}
STATUS_LABELS = {
    "complete": "数据完整",
    "partial": "部分结果",
    "waiting_for_data": "等待数据",
    "data_anomaly": "数据异常",
    "needs_clarification": "需要澄清",
    "unsupported": "暂不支持",
    "temporarily_unavailable": "暂时不可用",
}
_FRACTIONAL_TIMESTAMP = re.compile(r"\d{2}:\d{2}:\d{2}\.\d+")
_ACTION_WORDS = (
    "下单",
    "撤单",
    "改单",
    "平仓建议",
    "行权",
    "资金划转",
    "交易建议",
    "submit order",
    "cancel order",
)


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_profile: str
    trading_date: Optional[str] = None
    reference_mode: str = "none"
    date_expression: Optional[str] = None
    anchor: Optional[dict[str, Any]] = None
    controlled_status: Optional[str] = None
    clarification_question: Optional[str] = None
    controlled_message: Optional[str] = None


class AnswerProjection(BaseModel):
    """Small, task-specific projection of the full deterministic report."""

    model_config = ConfigDict(extra="forbid")

    task_profile: str
    status: str
    trading_date: Optional[str] = None
    account_name: Optional[str] = None
    instrument: Optional[str] = None
    valuation_basis: Optional[str] = None
    valuation_note: Optional[str] = None
    call_net: Optional[NetPositionFact] = None
    put_net: Optional[NetPositionFact] = None
    position_groups: list[OptionPositionGroup] = Field(default_factory=list)
    realized_close_pnl: Optional[NumericFact] = None
    unrealized_pnl: Optional[NumericFact] = None
    pnl_attribution: list[OptionPnlAttribution] = Field(default_factory=list)
    metadata: Optional[EvidenceMetadata] = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    calculation_version: Optional[str] = None
    rule_version: Optional[str] = None
    clarification_question: Optional[str] = None
    controlled_message: Optional[str] = None


class ConversationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def normalize_title(self) -> "ConversationIn":
        if self.title is not None:
            self.title = self.title.strip()[:80] or None
        return self


class ReplayIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trading_date: str = Field(min_length=8, max_length=10)


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: Optional[str] = Field(default=None, max_length=1000)
    suggestion_id: Optional[str] = Field(default=None, max_length=80)
    client_request_id: UUID4

    @model_validator(mode="after")
    def validate_content_or_suggestion(self) -> "MessageIn":
        if self.content is not None:
            self.content = self.content.strip()
            if not self.content:
                self.content = None
        if self.suggestion_id is not None:
            self.suggestion_id = self.suggestion_id.strip() or None
        if not self.content and not self.suggestion_id:
            raise ValueError("content or suggestion_id is required")
        return self


class MessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation: dict[str, Any]
    task: dict[str, Any]
    message: Optional[dict[str, Any]] = None
    http_status: int = Field(default=200, exclude=True)


SUGGESTIONS = [
    {
        "id": "position_previous_trading_day",
        "label": "查看上一实际交易日持仓",
        "question": "昨天的期权持仓怎么样？",
        "intent": IntentResolution(
            task_profile="option_previous_trading_day_position",
            date_expression="昨天",
            reference_mode="explicit_date",
            needs_clarification=False,
        ),
    },
    {
        "id": "realized_pnl_previous_trading_day",
        "label": "查看上一实际交易日平仓盈亏",
        "question": "昨天实际平仓盈亏是多少？",
        "intent": IntentResolution(
            task_profile="option_realized_pnl_query",
            date_expression="昨天",
            reference_mode="explicit_date",
            needs_clarification=False,
        ),
    },
    {
        "id": "unrealized_pnl_previous_trading_day",
        "label": "查看上一实际交易日浮盈浮亏",
        "question": "昨天持仓浮盈浮亏是多少？",
        "intent": IntentResolution(
            task_profile="option_unrealized_pnl_query",
            date_expression="昨天",
            reference_mode="explicit_date",
            needs_clarification=False,
        ),
    },
    {
        "id": "data_status_previous_trading_day",
        "label": "查看上一实际交易日数据状态",
        "question": "昨天的复盘数据完整吗？",
        "intent": IntentResolution(
            task_profile="review_data_status_query",
            date_expression="昨天",
            reference_mode="explicit_date",
            needs_clarification=False,
        ),
    },
]
_SUGGESTION_MAP = {item["id"]: item for item in SUGGESTIONS}
_due_check_lock = threading.Lock()
_due_check_last_date: Optional[date] = None


def require_agent_and_option_permissions(user: dict) -> None:
    """Check both gates before constructing a provider or reading review data."""

    require_permission(user, "closing_review.agent", "view")
    require_permission(user, "trading.options", "view")


def _normalize_date(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    compact = value.strip().replace("-", "/")
    if re.fullmatch(r"\d{8}", compact):
        normalized = compact
    elif re.fullmatch(r"\d{4}/\d{2}/\d{2}", compact):
        normalized = compact.replace("/", "")
    else:
        return None
    try:
        datetime.strptime(normalized, "%Y%m%d")
    except ValueError:
        return None
    return normalized


def _previous_weekday(reference: date) -> date:
    return closing_review_calendar.resolve_previous_trading_day(reference)


def _today_from(value: Optional[datetime]) -> date:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone(timedelta(hours=8))).date()


def _anchor_from_context(context: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    anchor = context.get("anchor") if isinstance(context, dict) else None
    if not isinstance(anchor, dict):
        return None
    allowed = {"task_profile", "target_date", "displayed_metric_labels", "result_ref"}
    return {key: anchor[key] for key in allowed if key in anchor}


def resolve_task_request(
    intent: IntentResolution,
    context: Optional[dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> TaskRequest:
    """Resolve model classification into a deterministic, bounded task."""

    anchor = _anchor_from_context(context)
    if intent.needs_clarification:
        return TaskRequest(
            task_profile=intent.task_profile,
            reference_mode=intent.reference_mode,
            date_expression=intent.date_expression,
            anchor=anchor,
            controlled_status="needs_clarification",
            clarification_question=intent.clarification_question,
        )
    if intent.task_profile == "unsupported":
        return TaskRequest(
            task_profile="unsupported",
            reference_mode=intent.reference_mode,
            date_expression=intent.date_expression,
            anchor=anchor,
            controlled_status="unsupported",
            controlled_message="当前 Agent 只支持宏源账户铁矿石期权的八类收盘复盘问题。",
        )
    if intent.task_profile not in SUPPORTED_PROFILES:
        return TaskRequest(
            task_profile="unsupported",
            controlled_status="unsupported",
            controlled_message="当前问题不在收盘复盘 Agent 的支持范围内。",
        )

    expression = (intent.date_expression or "").strip()
    normalized: Optional[str] = None
    if intent.reference_mode == "latest_result":
        normalized = _normalize_date(anchor.get("target_date") if anchor else None)
        if normalized is None:
            return TaskRequest(
                task_profile=intent.task_profile,
                reference_mode=intent.reference_mode,
                date_expression=expression or None,
                anchor=anchor,
                controlled_status="needs_clarification",
                clarification_question="请明确要解释当前对话中的哪一个结果？",
            )
    elif expression in {"昨天", "昨日"}:
        normalized = _previous_weekday(_today_from(now)).strftime("%Y%m%d")
    elif expression in {"今天", "今日"}:
        normalized = _today_from(now).strftime("%Y%m%d")
    elif expression:
        normalized = _normalize_date(expression)

    if normalized is None:
        return TaskRequest(
            task_profile=intent.task_profile,
            reference_mode=intent.reference_mode,
            date_expression=expression or None,
            anchor=anchor,
            controlled_status="needs_clarification",
            clarification_question="请明确要查询的交易日期（YYYY-MM-DD）。",
        )
    try:
        if not closing_review_calendar.is_trading_day(
            datetime.strptime(normalized, "%Y%m%d").date()
        ):
            return TaskRequest(
                task_profile=intent.task_profile,
                reference_mode=intent.reference_mode,
                date_expression=expression or None,
                anchor=anchor,
                controlled_status="non_trading_day",
                trading_date=normalized,
                controlled_message=f"{_display_date(normalized)} 不是实际交易日，未替换为其他日期。",
            )
    except closing_review_calendar.CalendarUnavailable:
        return TaskRequest(
            task_profile=intent.task_profile,
            reference_mode=intent.reference_mode,
            date_expression=expression or None,
            anchor=anchor,
            controlled_status="calendar_unavailable",
            trading_date=normalized,
            controlled_message="当前版本的交易日历不覆盖该日期，未猜测或替换日期。",
        )
    return TaskRequest(
        task_profile=intent.task_profile,
        trading_date=normalized,
        reference_mode=intent.reference_mode,
        date_expression=expression or None,
        anchor=anchor,
    )


def _common_projection(profile: str, report: OptionDailyReviewResponse) -> dict[str, Any]:
    return {
        "task_profile": profile,
        "status": report.status,
        "trading_date": report.trading_date,
        "account_name": report.account_name,
        "instrument": report.instrument,
        "metadata": report.metadata,
        "evidence_refs": list(report.metadata.evidence_refs),
        "warnings": list(report.warnings),
        "calculation_version": report.metadata.calculation_version,
        "rule_version": report.metadata.rule_version,
    }


def _null_fact(fact: NumericFact) -> NumericFact:
    return fact.model_copy(update={"value": None})


def _position_group_without_pnl(group: OptionPositionGroup) -> OptionPositionGroup:
    return group.model_copy(
        update={
            "floating_pnl": _null_fact(group.floating_pnl),
            "details": [
                detail.model_copy(update={"floating_pnl": _null_fact(detail.floating_pnl)})
                for detail in group.details
            ],
        }
    )


def _controlled_projection(
    profile: str,
    status: str,
    *,
    question: Optional[str] = None,
    message: Optional[str] = None,
    trading_date: Optional[str] = None,
) -> AnswerProjection:
    return AnswerProjection(
        task_profile=profile,
        status=status,
        trading_date=trading_date,
        clarification_question=question,
        controlled_message=message,
    )


def project_task_result(
    task_profile: str,
    report: OptionDailyReviewResponse,
    anchor: Optional[dict[str, Any]],
) -> AnswerProjection:
    """Select only fields required by the requested task profile."""

    if task_profile == "report_evidence_explanation":
        if not isinstance(anchor, dict) or anchor.get("task_profile") not in SUPPORTED_PROFILES - {
            "report_evidence_explanation"
        }:
            return _controlled_projection(
                task_profile,
                "needs_clarification",
                question="请明确要解释当前对话中的哪一个结果？",
            )
        underlying_profile = str(anchor["task_profile"])
        projection = project_task_result(underlying_profile, report, anchor)
        return projection.model_copy(update={"task_profile": task_profile})

    base = _common_projection(task_profile, report)
    if task_profile in {"option_position_query", "option_previous_trading_day_position"}:
        base.update(
            {
                "call_net": report.call_net,
                "put_net": report.put_net,
                "position_groups": [_position_group_without_pnl(item) for item in report.position_groups],
            }
        )
    elif task_profile == "option_realized_pnl_query":
        base["realized_close_pnl"] = report.realized_close_pnl
    elif task_profile == "option_unrealized_pnl_query":
        base.update(
            {
                "valuation_basis": report.valuation_basis,
                "valuation_note": report.valuation_note,
                "unrealized_pnl": report.unrealized_pnl,
            }
        )
    elif task_profile == "option_pnl_fact_attribution":
        base["pnl_attribution"] = list(report.pnl_attribution)
    elif task_profile == "review_data_status_query":
        base["warnings"] = list(report.warnings)
    elif task_profile == "effective_rule_query":
        base.update(
            {
                "valuation_basis": report.valuation_basis,
                "valuation_note": report.valuation_note,
            }
        )
    else:
        return _controlled_projection(
            "unsupported",
            "unsupported",
            message="当前问题不在收盘复盘 Agent 的支持范围内。",
        )
    return AnswerProjection(**base)


def build_automatic_result(report: OptionDailyReviewResponse) -> AnswerProjection:
    """Project the full deterministic report for the automatic daily result."""

    return AnswerProjection(
        task_profile="automatic_daily_review",
        status=report.status,
        trading_date=report.trading_date,
        account_name=report.account_name,
        instrument=report.instrument,
        valuation_basis=report.valuation_basis,
        valuation_note=report.valuation_note,
        call_net=report.call_net,
        put_net=report.put_net,
        position_groups=list(report.position_groups),
        realized_close_pnl=report.realized_close_pnl,
        unrealized_pnl=report.unrealized_pnl,
        pnl_attribution=list(report.pnl_attribution),
        metadata=report.metadata,
        evidence_refs=list(report.metadata.evidence_refs),
        warnings=list(report.warnings),
        calculation_version=report.metadata.calculation_version,
        rule_version=report.metadata.rule_version,
    )


def _iter_numeric_facts(value: Any, path: str = ""):
    if isinstance(value, NumericFact):
        yield path, value
        return
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            yield from _iter_numeric_facts(getattr(value, name), f"{path}.{name}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_numeric_facts(item, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _iter_numeric_facts(item, f"{path}.{key}")


def _iter_strings(value: Any, path: str = ""):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, BaseModel):
        for name in type(value).model_fields:
            yield from _iter_strings(getattr(value, name), f"{path}.{name}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_strings(item, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _iter_strings(item, f"{path}.{key}")


def validate_completion(
    projection: AnswerProjection,
    report: OptionDailyReviewResponse,
) -> None:
    if projection.account_name not in {None, ACCOUNT_NAME}:
        raise ValueError("account is outside the fixed review scope")
    if report.account_name != ACCOUNT_NAME or report.instrument != INSTRUMENT_NAME:
        raise ValueError("account or instrument is outside the fixed review scope")
    for path, fact in _iter_numeric_facts(projection):
        if fact.value is not None and not fact.metadata.evidence_refs:
            raise ValueError(f"numeric field lacks evidence: {path}")
    if projection.task_profile == "option_realized_pnl_query" and projection.unrealized_pnl is not None:
        raise ValueError("realized projection cannot contain unrealized pnl")
    if projection.task_profile == "option_unrealized_pnl_query" and projection.realized_close_pnl is not None:
        raise ValueError("unrealized projection cannot contain realized pnl")
    if projection.status == "complete" and report.status == "data_anomaly":
        raise ValueError("complete projection cannot hide a data anomaly")
    for path, value in _iter_strings(projection):
        if _FRACTIONAL_TIMESTAMP.search(value):
            raise ValueError(f"用户可见时间必须只保留到秒: {path}")
        if any(word.lower() in value.lower() for word in _ACTION_WORDS):
            raise ValueError(f"交易动作或建议不允许出现在回答中: {path}")


def _display_date(value: Optional[str]) -> str:
    if not value or not re.fullmatch(r"\d{8}", value):
        return "未确定"
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"


def _display_number(fact: Optional[NumericFact]) -> str:
    if fact is None or fact.value is None:
        return "暂不可确认"
    return f"{fact.value:,.2f}"


def _display_net(label: str, value: Optional[NetPositionFact]) -> str:
    if value is None:
        return f"{label}：暂不可确认"
    return f"{label}：{value.direction_label} { _display_number(value.lots) } 手"


def render_answer(projection: AnswerProjection, *, updated: bool = False) -> str:
    if projection.clarification_question:
        return projection.clarification_question
    if projection.controlled_message:
        return projection.controlled_message
    status_label = STATUS_LABELS.get(projection.status, projection.status)
    lines = [f"数据状态：{status_label}", f"实际交易日：{_display_date(projection.trading_date)}"]
    if projection.task_profile in {
        "option_position_query",
        "option_previous_trading_day_position",
        "automatic_daily_review",
    }:
        lines.extend(
            [
                _display_net("Call", projection.call_net),
                _display_net("Put", projection.put_net),
                "持仓分组：" + ("、".join(
                    f"{item.expiry_month}{item.option_type}{item.direction} { _display_number(item.quantity_lots) } 手"
                    for item in projection.position_groups
                ) or "无可确认分组"),
            ]
        )
        if projection.task_profile == "automatic_daily_review":
            lines.append(f"真实平仓盈亏：{_display_number(projection.realized_close_pnl)}")
            lines.append(f"持仓浮盈浮亏：{_display_number(projection.unrealized_pnl)}")
            lines.append(f"估值口径：{projection.valuation_note or '未确定'}")
    elif projection.task_profile == "option_realized_pnl_query":
        lines.append(f"真实平仓盈亏：{_display_number(projection.realized_close_pnl)}")
    elif projection.task_profile == "option_unrealized_pnl_query":
        lines.append(f"持仓浮盈浮亏：{_display_number(projection.unrealized_pnl)}")
        if projection.valuation_note:
            lines.append(f"估值口径：{projection.valuation_note}")
    elif projection.task_profile == "option_pnl_fact_attribution":
        if projection.pnl_attribution:
            lines.append(
                "事实贡献：" + "、".join(
                    f"{item.expiry_month}{item.option_type}{item.direction} { _display_number(item.realized_close_pnl) }"
                    for item in projection.pnl_attribution
                )
            )
        else:
            lines.append("事实贡献：暂不可确认")
    elif projection.task_profile == "review_data_status_query":
        lines.append("数据说明：" + ("；".join(projection.warnings) or "未发现额外异常"))
    elif projection.task_profile == "effective_rule_query":
        lines.append(f"估值口径：{projection.valuation_basis or '未确定'}")
        if projection.valuation_note:
            lines.append(f"规则说明：{projection.valuation_note}")
        if projection.calculation_version:
            lines.append(f"计算版本：{projection.calculation_version}")
        if projection.rule_version:
            lines.append(f"规则版本：{projection.rule_version}")
    elif projection.task_profile == "report_evidence_explanation":
        if projection.realized_close_pnl is not None:
            lines.append(f"真实平仓盈亏组成：{_display_number(projection.realized_close_pnl)}")
        elif projection.unrealized_pnl is not None:
            lines.append(f"持仓浮盈浮亏组成：{_display_number(projection.unrealized_pnl)}")
        else:
            lines.append("当前锚点没有可解释的数值结果。")
    if projection.warnings and projection.task_profile not in {"review_data_status_query"}:
        lines.append("说明：" + "；".join(projection.warnings))
    if updated:
        lines.append("结果已更新：本次自动结果基于最新数据来源。")
    return "\n".join(lines)


def _suggestion_intent(suggestion_id: str) -> tuple[str, IntentResolution]:
    item = _SUGGESTION_MAP.get(suggestion_id)
    if not item:
        raise HTTPException(status_code=422, detail="推荐问题无效")
    return str(item["question"]), IntentResolution.model_validate(item["intent"])


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None
    return value


def _seconds(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return value.split(".", 1)[0]


def _public_conversation(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "channel": row.get("channel"),
        "kind": row.get("kind"),
        "title": row.get("title"),
        "status": row.get("status"),
        "last_message_at": _seconds(row.get("last_message_at")),
        "created_at": _seconds(row.get("created_at")),
        "updated_at": _seconds(row.get("updated_at")),
    }


def _public_message(row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not row:
        return None
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "task_id": row.get("task_id"),
        "role": row.get("role"),
        "message_type": row.get("message_type"),
        "content": row.get("content"),
        "structured_payload": _json_value(row.get("structured_payload")),
        "status": row.get("status"),
        "created_at": _seconds(row.get("created_at")),
        "redacted_at": _seconds(row.get("redacted_at")),
    }


def _task_business_status(task: dict[str, Any]) -> str:
    projection = _json_value(task.get("result_projection"))
    if isinstance(projection, dict) and projection.get("status"):
        return str(projection["status"])
    if task.get("state") == "processing":
        return "processing"
    if task.get("state") == "failed":
        return "temporarily_unavailable"
    return str(task.get("state") or "processing")


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": task["id"],
        "conversation_id": task.get("conversation_id"),
        "task_kind": task.get("task_kind"),
        "task_profile": task.get("task_profile"),
        "target_date": task.get("target_date"),
        "state": task.get("state"),
        "business_status": _task_business_status(task),
        "model_provider": task.get("model_provider"),
        "model_name": task.get("model_name"),
        "retry_count": task.get("retry_count") or 0,
        "error_category": task.get("error_category"),
        "started_at": _seconds(task.get("started_at")),
        "finished_at": _seconds(task.get("finished_at")),
    }


def _message_response(
    conversation: dict[str, Any],
    task: dict[str, Any],
    message: Optional[dict[str, Any]],
    http_status: int = 200,
) -> MessageResponse:
    return MessageResponse(
        conversation=_public_conversation(conversation),
        task=_public_task(task),
        message=_public_message(message) if message and "id" in message else message,
        http_status=http_status,
    )


def _existing_task_response(
    user_id: int,
    requested_conversation_id: int,
    task: dict[str, Any],
) -> MessageResponse:
    if int(task["conversation_id"]) != int(requested_conversation_id):
        raise HTTPException(status_code=409, detail="client_request_id 已用于其他对话")
    conversation = store.get_owned_conversation(user_id, int(task["conversation_id"]))
    if not conversation:
        raise HTTPException(status_code=404, detail="对话不存在")
    messages = store.list_messages(user_id, int(task["conversation_id"]), limit=200)
    related = [item for item in messages if item.get("task_id") == task.get("id")]
    latest = related[-1] if related else None
    code = 202 if task.get("state") == "processing" else 200
    return _message_response(conversation, task, latest, http_status=code)


def _provider_metadata(provider: Any) -> dict[str, Any]:
    metadata = getattr(provider, "last_metadata", {})
    return dict(metadata) if isinstance(metadata, dict) else {}


def _maybe_run_due_reviews(now: Optional[datetime] = None) -> None:
    """Wake the deterministic scheduler once per local calendar day on page open."""

    from . import closing_review_scheduler as scheduler

    if not scheduler._enabled("CLOSING_REVIEW_AGENT_AUTO_ENABLED"):
        return
    current = now or datetime.now(timezone.utc)
    local_date = _today_from(current)
    global _due_check_last_date
    with _due_check_lock:
        if _due_check_last_date == local_date:
            return
        _due_check_last_date = local_date
    try:
        scheduler.run_due_reviews(current)
    except Exception:
        # A wake check must never make the Agent page unavailable. The scheduler
        # records controlled failures on its own when it can reach the store.
        return


def _finish_task(
    task: dict[str, Any],
    projection: AnswerProjection,
    *,
    provider: Optional[Any] = None,
    intent: Optional[IntentResolution] = None,
    error_category: Optional[str] = None,
) -> dict[str, Any]:
    metadata = _provider_metadata(provider) if provider is not None else {}
    usage = metadata.get("usage")
    return store.finish_task(
        int(task["id"]),
        user_id=int(task["user_id"]),
        state="succeeded",
        task_profile=projection.task_profile,
        target_date=projection.trading_date,
        validated_intent=intent.model_dump(mode="json") if intent is not None else None,
        result_projection=projection.model_dump(mode="json"),
        model_provider=metadata.get("provider"),
        model_name=metadata.get("model"),
        model_usage=usage,
        model_finish_reason=metadata.get("finish_reason"),
        model_duration_seconds=metadata.get("duration_seconds"),
        retry_count=max(0, int(metadata.get("attempt_count", 1) or 1) - 1),
        error_category=error_category,
    )


def process_message(user: dict, conversation_id: int, payload: MessageIn) -> MessageResponse:
    """Process one bounded message and return a complete business state."""

    require_agent_and_option_permissions(user)
    conversation = store.get_owned_conversation(int(user["id"]), int(conversation_id))
    if not conversation:
        raise HTTPException(status_code=404, detail="对话不存在")
    if payload.suggestion_id:
        question, intent = _suggestion_intent(payload.suggestion_id)
    else:
        question = str(payload.content or "").strip()
        intent = None
    task, created = store.claim_user_task(
        int(user["id"]),
        int(conversation_id),
        str(payload.client_request_id),
        task_kind="user_message",
        state="processing",
    )
    if not created:
        return _existing_task_response(int(user["id"]), int(conversation_id), task)

    user_message = store.append_message(
        int(user["id"]),
        int(conversation_id),
        role="user",
        message_type="user",
        content=question,
        task_id=int(task["id"]),
    )
    task = store.attach_task_user_message(int(task["id"]), int(user["id"]), int(user_message["id"]))
    context = store.build_context(int(user["id"]), int(conversation_id))
    provider: Optional[ClosingReviewModelProvider] = None
    error_category: Optional[str] = None
    if intent is None:
        provider = build_closing_review_provider()
        try:
            intent = provider.resolve_intent(
                IntentRequest(
                    request_id=str(payload.client_request_id),
                    user_text=question,
                    context_messages=context.get("messages", []),
                    anchor=context.get("anchor"),
                    conversation_id=int(conversation_id),
                )
            )
        except ModelGatewayError as exc:
            error_category = exc.category
            projection = _controlled_projection(
                "unsupported",
                "temporarily_unavailable",
                message="当前问题识别暂时不可用，请稍后重试。",
            )
            task = _finish_task(task, projection, provider=provider, error_category=error_category)
            assistant = store.append_message(
                int(user["id"]),
                int(conversation_id),
                role="assistant",
                message_type="error",
                content=render_answer(projection),
                structured_payload=projection.model_dump(mode="json"),
                status="active",
                task_id=int(task["id"]),
            )
            refreshed = store.get_owned_conversation(int(user["id"]), int(conversation_id)) or conversation
            return _message_response(refreshed, task, assistant)

    request = resolve_task_request(intent, context=context)
    if request.controlled_status:
        projection = _controlled_projection(
            request.task_profile,
            request.controlled_status,
            question=request.clarification_question,
            message=request.controlled_message,
            trading_date=request.trading_date,
        )
    else:
        report = build_option_daily_review(str(request.trading_date))
        projection = project_task_result(request.task_profile, report, request.anchor)
        validate_completion(projection, report)
    task = _finish_task(task, projection, provider=provider, intent=intent, error_category=error_category)
    assistant = store.append_message(
        int(user["id"]),
        int(conversation_id),
        role="assistant",
        message_type="answer" if projection.status not in {"temporarily_unavailable", "unsupported"} else "error",
        content=render_answer(projection),
        structured_payload=projection.model_dump(mode="json"),
        status="active",
        task_id=int(task["id"]),
    )
    refreshed = store.get_owned_conversation(int(user["id"]), int(conversation_id)) or conversation
    return _message_response(refreshed, task, assistant)


@router.get("/conversations")
def list_agent_conversations(
    before_id: Optional[int] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    user: dict = Depends(trading_management_current_user),
):
    require_agent_and_option_permissions(user)
    _maybe_run_due_reviews()
    return {"items": [_public_conversation(item) for item in store.list_conversations(int(user["id"]), before_id, limit)]}


@router.post("/conversations")
def create_agent_conversation(
    payload: ConversationIn,
    user: dict = Depends(trading_management_current_user),
):
    require_agent_and_option_permissions(user)
    conversation = store.create_conversation(int(user["id"]), payload.title or store.DEFAULT_CONVERSATION_TITLE)
    return _public_conversation(conversation)


@router.get("/conversations/{conversation_id}/messages")
def list_agent_messages(
    conversation_id: int,
    before_id: Optional[int] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    user: dict = Depends(trading_management_current_user),
):
    require_agent_and_option_permissions(user)
    conversation = store.get_owned_conversation(int(user["id"]), conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="对话不存在")
    messages = store.list_messages(int(user["id"]), conversation_id, before_id, limit)
    return {
        "conversation": _public_conversation(conversation),
        "items": [_public_message(item) for item in messages],
    }


@router.get("/suggestions")
def list_agent_suggestions(user: dict = Depends(trading_management_current_user)):
    require_agent_and_option_permissions(user)
    return {
        "items": [
            {"id": item["id"], "label": item["label"], "question": item["question"]}
            for item in SUGGESTIONS
        ]
    }


@router.post("/admin/replay")
def replay_agent_result(
    payload: ReplayIn,
    user: dict = Depends(trading_management_current_user),
):
    from . import closing_review_scheduler as scheduler

    if not scheduler._enabled("CLOSING_REVIEW_AGENT_REPLAY_ENABLED"):
        raise HTTPException(status_code=404, detail="历史回放未启用")
    if user.get("role") not in {"管理员", "admin"}:
        raise HTTPException(status_code=403, detail="仅管理员可执行此操作")
    normalized = _normalize_date(payload.trading_date)
    if normalized is None:
        raise HTTPException(status_code=422, detail="交易日期必须是YYYYMMDD或YYYY-MM-DD")
    result = scheduler.run_historical_replay(
        user,
        datetime.strptime(normalized, "%Y%m%d").date(),
    )
    return result.model_dump(mode="json")


@router.post("/conversations/{conversation_id}/messages", response_model=MessageResponse)
def post_agent_message(
    conversation_id: int,
    payload: MessageIn,
    response: Response,
    user: dict = Depends(trading_management_current_user),
):
    result = process_message(user, conversation_id, payload)
    response.status_code = result.http_status
    return result
