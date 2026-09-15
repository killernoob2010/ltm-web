"""Task-scoped typed tool adapters for the Pydantic AI execution path."""
from __future__ import annotations

import asyncio
import inspect
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from . import tools
from .contracts import Principal, ToolEnvelope
from .egress_policy import EgressDenied, redact_exception, sanitize_model_payload
from .planning_contracts import TaskPlan
from .runtime_budget import BudgetExceeded, RuntimeBudget


class ToolInvocationDenied(PermissionError):
    """The model requested an unregistered, unauthorized or invalid operation."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


_PERMISSION_ERROR_MARKERS = (
    "unauthor", "forbidden", "permission", "invalid_grant", "grant_expired",
    "grant_revoked", "authentication",
)
_TRANSIENT_RETRY_CODES = frozenset({
    "temporarily_unavailable", "service_unavailable", "upstream_timeout",
    "tool_timeout", "connection_error", "connection_reset", "rate_limit",
    "rate_limited", "ratelimit", "overload", "public_source_unavailable",
})


@dataclass
class AgentRunContext:
    """Mutable execution state that belongs to exactly one leased task."""

    task_id: int
    principal: Principal
    plan: TaskPlan
    grant: str
    mcp: Any
    store: Any
    budget: RuntimeBudget
    sensitive_values: tuple[str, ...]
    envelopes: list[ToolEnvelope] = field(default_factory=list)
    allowed_tool_names: frozenset[str] | None = None
    research_allowed: bool = False
    tool_timeout_seconds: float = 25.0
    transient_retries_used: int = 0


_MODEL_PAYLOAD_PATHS = {
    "kind", "unit", "canonical_value", "data_as_of", "date_range", "measure", "metric",
    "periods", "coverage", "value_fields", "port", "product", "category", "grade",
    "observation_date", "business_date", "period_start", "period_end", "current_date",
    "previous_date", "current_value", "previous_value", "delta", "delta_pct", "value",
    "comparison_status", "relation_status", "row_ref", "status",
    "metrics.*.value", "metrics.*.unit", "metrics.*.status",
}

_DATASET_SUMMARY_PATHS = {
    "dataset", "row_count", "preview_count", "preview_truncated", "value_fields.*",
    *{f"periods.{key}" for key in ("current", "previous", "start", "end", "method")},
    *{f"coverage.{key}" for key in (
        "current_rows", "previous_rows", "matched_rows", "missing_previous", "unmatched_rows",
        "first_observation", "last_observation",
    )},
    "coverage.observed_years.*", "coverage.requested_years.*", "coverage.missing_years.*",
    *{f"ranking.{key}" for key in ("measure", "descending", "top_k", "total_rows", "participating_rows", "excluded_rows", "returned_rows")},
    *{f"preview.*.{key}" for key in (
        "port", "product", "category", "grade", "observation_date", "business_date",
        "business_year", "period_start", "period_end", "current_date", "previous_date",
        "current_value", "previous_value", "delta", "delta_pct", "value", "unit",
        "comparison_status", "status", "row_ref", "covered_rows", "eligible_rows",
    )},
}


_PUBLIC_SOURCE_FIELDS = {
    "source_ref", "title", "url", "description", "published_at",
    "published_label", "fetch_status",
}
_PUBLIC_SEARCH_PATHS = {
    "provider", "search_status", "provider_status", "source_count",
    "candidate_count", "registered_source_count",
    *{f"sources.*.{key}" for key in _PUBLIC_SOURCE_FIELDS},
}
_PUBLIC_READ_PATHS = _PUBLIC_SOURCE_FIELDS | {"text", "untrusted_content", "truncated"}
_POSITION_FIELDS = {
    "contract_symbol", "product", "exchange", "asset_type", "direction",
    "contract_month", "option_type", "strike_price", "fact_status", "assignment_status",
    "row_ref", "quantity", "average_price", "floating_pnl", "valuation_price",
    "valuation_status", "market_time", "contract_multiplier", "underlying_symbol",
    "underlying_price", "expiry_date", "gross_quantity", "gross_buy_quantity",
    "gross_sell_quantity", "net_quantity", "net_sell_quantity", "net_tons",
    "net_signed_tons", "net_wan_tons", "strike_min", "strike_max",
    "count", "covered_rows", "eligible_rows", "iv", "delta", "gamma", "theta", "vega", "rho",
}
_METRIC_FIELDS = {"value", "unit", "status", "covered_rows", "eligible_rows"}
_POSITION_PATHS = {
    "count", "preview_count", "preview_truncated", "page", "page_size",
    "group_count", "groups_truncated", "semantic_group_count", "semantic_groups_truncated",
    "aggregation", "source_ref", "valuation_basis", "data_status",
    "as_of.mode", "as_of.date", "required_metrics.*", "group_by.*",
    *{f"preview.*.{key}" for key in _POSITION_FIELDS},
    *{f"{group}.*.dimensions.{key}" for group in ("groups", "semantic_groups")
      for key in _POSITION_FIELDS},
    *{f"{group}.*.metrics.{metric}.{field}" for group in ("groups", "semantic_groups")
      for metric in _POSITION_FIELDS for field in _METRIC_FIELDS},
    "groups.*.row_refs.*", "semantic_groups.*.row_refs.*",
}


def _position_model_payload(payload: dict[str, Any], *, restore=False) -> dict[str, Any]:
    # The generic egress policy forbids commercial contract material. Rename
    # only the exchange instrument code at known trading-row boundaries while
    # filtering, then restore its registered name for evidence/view consumers.
    # Never relax the global prohibition or mutate the stored evidence.
    result = deepcopy(payload)
    rows = list(result.get("preview") or [])
    for key in ("groups", "semantic_groups"):
        rows.extend(group.get("dimensions", {}) for group in (result.get(key) or [])
                    if isinstance(group, dict))
    source, destination = ("contract_symbol", "contract") if restore else ("contract", "contract_symbol")
    seen = set()
    for row in rows:
        if isinstance(row, dict) and id(row) not in seen:
            seen.add(id(row))
            row.pop(destination, None)
            if source in row:
                row[destination] = row.pop(source)
    if isinstance(result.get("group_by"), list):
        result["group_by"] = [destination if key == source else key
                              for key in result["group_by"] if key != "account"]
    return result


def _candidate_tools(ctx: AgentRunContext) -> set[str]:
    if ctx.allowed_tool_names is not None:
        return set(ctx.allowed_tool_names)
    return set(tools.tool_names_for_principal(
        ctx.principal, research_allowed=ctx.research_allowed
    ))


def _live_tool_allowed(ctx: AgentRunContext, name: str) -> bool:
    if name not in tools.TOOL_SPECS or name not in _candidate_tools(ctx):
        return False
    return bool(tools._tool_authorized(
        ctx.principal, name, research_allowed=ctx.research_allowed
    ))


def _error_summary(status: str, code: str) -> dict[str, Any]:
    return {"status": status, "error_code": str(code)[:64]}


def _model_summary(envelope: ToolEnvelope, *, sensitive_values: tuple[str, ...]) -> dict[str, Any]:
    payload = envelope.payload if isinstance(envelope.payload, dict) else {}
    # Scan every part of the envelope, including metadata outside payload.
    sanitize_model_payload(envelope.model_dump(mode="json"), allowed_paths=(), sensitive_values=sensitive_values)
    allowed_paths = set(_MODEL_PAYLOAD_PATHS)
    if payload.get("kind") in {"dataset_rows", "dataset_summary", "dataset_comparison"}:
        allowed_paths.update(_DATASET_SUMMARY_PATHS)
    elif payload.get("kind") == "research":
        allowed_paths.update(_PUBLIC_SEARCH_PATHS)
    elif payload.get("kind") == "public_read":
        allowed_paths.update(_PUBLIC_READ_PATHS)
    elif payload.get("kind") == "positions":
        allowed_paths.update(_POSITION_PATHS)
        payload = _position_model_payload(payload)
    safe_payload = sanitize_model_payload(
        payload,
        allowed_paths=allowed_paths,
        sensitive_values=sensitive_values,
    )
    if payload.get("kind") == "positions":
        safe_payload = _position_model_payload(safe_payload, restore=True)
    summary: dict[str, Any] = {
        "status": envelope.status,
        "result_ref": str(envelope.result_ref) if envelope.result_ref else None,
        "snapshot_ref": str(envelope.snapshot_ref) if envelope.snapshot_ref else None,
        "data_as_of": envelope.data_as_of.isoformat(timespec="seconds") if envelope.data_as_of else None,
        "captured_at": envelope.captured_at.isoformat(timespec="seconds"),
        "calculation_version": envelope.calculation_version,
        "payload": safe_payload,
    }
    if envelope.metrics:
        metric_payload = {
            name: value.model_dump(mode="json")
            for name, value in envelope.metrics.items()
        }
        summary["metrics"] = sanitize_model_payload(
            metric_payload,
            allowed_paths={f"*.{field}" for field in _METRIC_FIELDS},
            sensitive_values=sensitive_values,
        )
    if envelope.missing:
        summary["missing_codes"] = [
            str(item["code"])[:64]
            for item in envelope.missing
            if isinstance(item, dict) and isinstance(item.get("code"), str)
        ][:16]
    return summary


async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _audit_tool(ctx: AgentRunContext, name: str, envelope: ToolEnvelope | None, error: str | None = None) -> bool:
    append_event = getattr(ctx.store, "append_event", None)
    if not callable(append_event):
        return False
    try:
        result = append_event(
            ctx.principal,
            "tool",
            tool_name=name,
            result_ref=getattr(envelope, "result_ref", None),
            status=getattr(envelope, "status", None) if envelope else "failed",
            error_code=error,
        )
    except Exception:
        return False
    return result is not False


def _audit_failed(ctx: AgentRunContext) -> dict[str, Any]:
    # Without a durable event, the result cannot be placed in the model's
    # evidence context.  Cancellation also prevents a caller from continuing
    # with an unaudited sequence of tools.
    ctx.budget.cancel()
    return _error_summary("temporarily_unavailable", "audit_write_failed")


def _error_code(exc: BaseException) -> str:
    return redact_exception(exc)["code"]


def _is_transient_error(exc: BaseException, code: str) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    if code.casefold() in _TRANSIENT_RETRY_CODES:
        return True
    return type(exc).__name__.casefold() in {
        "connecttimeout", "readtimeout", "connectionerror", "connectionreseterror",
    }


def _is_permission_error(exc: BaseException, code: str) -> bool:
    if isinstance(exc, PermissionError):
        return True
    text = f"{code} {type(exc).__name__}".casefold()
    return any(marker in text for marker in _PERMISSION_ERROR_MARKERS)


def _safe_exception_code(exc: BaseException, raw_code: str) -> str:
    normalized = raw_code.casefold()
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "tool_timeout"
    if normalized in _TRANSIENT_RETRY_CODES:
        return normalized
    if _is_permission_error(exc, raw_code):
        return "tool_not_authorized"
    return "temporarily_unavailable"


def _transient_envelope_code(envelope: ToolEnvelope) -> str | None:
    if envelope.status != "temporarily_unavailable":
        return None
    payload = envelope.payload if isinstance(envelope.payload, dict) else {}
    for key in ("error_code", "code"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value[:64] if value.casefold() in _TRANSIENT_RETRY_CODES else "temporarily_unavailable"
    return "temporarily_unavailable"


def _transient_envelope_retryable(envelope: ToolEnvelope) -> bool:
    payload = envelope.payload if isinstance(envelope.payload, dict) else {}
    if str(payload.get("kind") or "").casefold() in {"public_query_rejected", "public_research_blocked"}:
        return False
    for key in ("error_code", "code"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value.casefold() in _TRANSIENT_RETRY_CODES
    return True


def _transient_envelope_requires_stop(envelope: ToolEnvelope) -> bool:
    payload = envelope.payload if isinstance(envelope.payload, dict) else {}
    if str(payload.get("kind") or "").casefold() == "public_query_rejected":
        return True
    for key in ("error_code", "code"):
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            continue
        normalized = value.casefold()
        if any(marker in normalized for marker in _PERMISSION_ERROR_MARKERS):
            return True
        if any(marker in normalized for marker in ("private", "privacy", "query_rejected")):
            return True
    return False


def _sanitize_envelope_before_control(
    ctx: AgentRunContext, name: str, envelope: ToolEnvelope,
) -> dict[str, Any] | None:
    """Reject sensitive envelopes before any retry, audit or model projection."""
    try:
        sanitize_model_payload(
            envelope.model_dump(mode="json"),
            allowed_paths=(),
            sensitive_values=(*ctx.sensitive_values, ctx.grant),
        )
    except EgressDenied as exc:
        safe_code = exc.code if exc.code in {"private_content", "payload_depth_exceeded"} else "private_content"
        ctx.budget.cancel()
        if not _audit_tool(ctx, name, None, safe_code):
            return _audit_failed(ctx)
        return _error_summary("temporarily_unavailable", safe_code)
    return None


def _reserve_transient_retry(ctx: AgentRunContext, name: str) -> str | None:
    """Admit at most one task-wide service retry and return a blocking code."""
    if ctx.transient_retries_used >= 1:
        return "retry_exhausted"
    try:
        ctx.budget.reserve("search" if name == "search_public" else "tool")
    except BudgetExceeded as exc:
        return exc.code
    ctx.transient_retries_used += 1
    return None


def _deny(ctx: AgentRunContext, code: str) -> None:
    """Stop the task context before surfacing a permission boundary violation."""
    if code in {"unknown_tool", "tool_not_authorized", "missing_grant"}:
        ctx.budget.cancel()
    raise ToolInvocationDenied(code)


async def invoke_registered_tool(name: str, arguments: dict[str, Any], *, ctx: AgentRunContext) -> dict[str, Any]:
    """Invoke one pre-registered tool with task-local identity and budget."""
    if not isinstance(name, str) or name not in tools.TOOL_SPECS:
        _deny(ctx, "unknown_tool")
    try:
        ctx.budget.reserve("search" if name == "search_public" else "tool")
    except BudgetExceeded as exc:
        return _error_summary("limit_exceeded", exc.code)
    if not _live_tool_allowed(ctx, name):
        _deny(ctx, "tool_not_authorized")
    if not isinstance(arguments, dict):
        raise ToolInvocationDenied("invalid_arguments")
    try:
        validated = tools.TOOL_SPECS[name]["model"].model_validate(arguments)
    except Exception as exc:
        raise ToolInvocationDenied("invalid_arguments") from exc
    serialized = validated.model_dump(mode="json")
    if not isinstance(ctx.grant, str) or not ctx.grant:
        _deny(ctx, "missing_grant")
    while True:
        if ctx.budget.cancelled:
            raise asyncio.CancelledError
        if not _live_tool_allowed(ctx, name):
            _deny(ctx, "tool_not_authorized")
        try:
            call = ctx.mcp.call_tool(name, serialized, ctx.grant)
            envelope = await asyncio.wait_for(
                _await(call),
                timeout=min(ctx.tool_timeout_seconds, ctx.budget.remaining_seconds()),
            )
            if not isinstance(envelope, ToolEnvelope):
                envelope = ToolEnvelope.model_validate(envelope)
        except asyncio.TimeoutError:
            error = "tool_timeout"
            if not _audit_tool(ctx, name, None, error):
                return _audit_failed(ctx)
            retry_error = _reserve_transient_retry(ctx, name)
            if retry_error is None:
                continue
            return _error_summary(
                "limit_exceeded" if retry_error != "retry_exhausted" else "temporarily_unavailable",
                retry_error if retry_error != "retry_exhausted" else error,
            )
        except Exception as exc:
            raw_error = _error_code(exc)
            transient = _is_transient_error(exc, raw_error)
            permission = _is_permission_error(exc, raw_error)
            try:
                sanitize_model_payload({"code": raw_error}, allowed_paths={"code"},
                                       sensitive_values=(*ctx.sensitive_values, ctx.grant))
            except EgressDenied:
                ctx.budget.cancel()
                error = "private_content"
            else:
                error = _safe_exception_code(exc, raw_error)
            if not _audit_tool(ctx, name, None, error):
                return _audit_failed(ctx)
            if permission:
                ctx.budget.cancel()
                raise ToolInvocationDenied("tool_not_authorized") from exc
            if transient:
                retry_error = _reserve_transient_retry(ctx, name)
                if retry_error is None:
                    continue
                return _error_summary(
                    "limit_exceeded" if retry_error != "retry_exhausted" else "temporarily_unavailable",
                    retry_error if retry_error != "retry_exhausted" else error,
                )
            return _error_summary("temporarily_unavailable", error)
        envelope_error = _sanitize_envelope_before_control(ctx, name, envelope)
        if envelope_error is not None:
            return envelope_error
        transient_code = _transient_envelope_code(envelope)
        if transient_code is not None:
            if not _transient_envelope_retryable(envelope):
                payload = envelope.payload if isinstance(envelope.payload, dict) else {}
                stop_code = "public_query_rejected" if payload.get("kind") == "public_query_rejected" else "temporarily_unavailable"
                if _transient_envelope_requires_stop(envelope):
                    ctx.budget.cancel()
                if not _audit_tool(ctx, name, None, stop_code):
                    return _audit_failed(ctx)
                return _error_summary("temporarily_unavailable", stop_code)
            if not _audit_tool(ctx, name, None, transient_code):
                return _audit_failed(ctx)
            retry_error = _reserve_transient_retry(ctx, name)
            if retry_error is None:
                continue
            return _error_summary(
                "limit_exceeded" if retry_error != "retry_exhausted" else "temporarily_unavailable",
                retry_error if retry_error != "retry_exhausted" else transient_code,
            )
        break
    try:
        summary = _model_summary(envelope, sensitive_values=(*ctx.sensitive_values, ctx.grant))
    except EgressDenied as exc:
        ctx.budget.cancel()
        if not _audit_tool(ctx, name, None, exc.code):
            return _audit_failed(ctx)
        return _error_summary("temporarily_unavailable", exc.code)
    if not _audit_tool(ctx, name, envelope):
        return _audit_failed(ctx)
    ctx.envelopes.append(envelope)
    return summary


def model_tool_schemas(ctx: AgentRunContext) -> list[dict[str, Any]]:
    """Return individual registered schemas; the generic executor is never exposed."""
    names = sorted(_candidate_tools(ctx))
    return [
        {
            "name": name,
            "description": tools.TOOL_SPECS[name]["description"],
            "inputSchema": tools.TOOL_SPECS[name]["model"].model_json_schema(),
        }
        for name in names
        if name in tools.TOOL_SPECS and _live_tool_allowed(ctx, name)
    ]


__all__ = [
    "AgentRunContext", "ToolInvocationDenied", "invoke_registered_tool", "model_tool_schemas",
]
