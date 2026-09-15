"""Task-scoped typed tool adapters for the Pydantic AI execution path."""
from __future__ import annotations

import asyncio
import inspect
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


_TRANSIENT_ERROR_MARKERS = (
    "timeout", "temporar", "unavailable", "connection", "connect", "reset",
    "rate_limit", "ratelimit", "overload", "service_unavailable",
)
_PERMISSION_ERROR_MARKERS = (
    "unauthor", "forbidden", "permission", "invalid_grant", "grant_expired",
    "grant_revoked", "authentication",
)


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
    safe_payload = sanitize_model_payload(
        payload,
        allowed_paths=allowed_paths,
        sensitive_values=sensitive_values,
    )
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
            allowed_paths={"*.value", "*.unit", "*.status"},
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
    text = f"{code} {type(exc).__name__}".casefold()
    return any(marker in text for marker in _TRANSIENT_ERROR_MARKERS)


def _is_permission_error(exc: BaseException, code: str) -> bool:
    if isinstance(exc, PermissionError):
        return True
    text = f"{code} {type(exc).__name__}".casefold()
    return any(marker in text for marker in _PERMISSION_ERROR_MARKERS)


def _transient_envelope_code(envelope: ToolEnvelope) -> str | None:
    if envelope.status != "temporarily_unavailable":
        return None
    payload = envelope.payload if isinstance(envelope.payload, dict) else {}
    for key in ("error_code", "code"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value[:64]
    return "temporarily_unavailable"


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
            error = _error_code(exc)
            try:
                sanitize_model_payload({"code": error}, allowed_paths={"code"},
                                       sensitive_values=(*ctx.sensitive_values, ctx.grant))
            except EgressDenied:
                ctx.budget.cancel()
                error = "private_content"
            if not _audit_tool(ctx, name, None, error):
                return _audit_failed(ctx)
            if _is_permission_error(exc, error):
                ctx.budget.cancel()
                raise ToolInvocationDenied("tool_not_authorized") from exc
            if _is_transient_error(exc, error):
                retry_error = _reserve_transient_retry(ctx, name)
                if retry_error is None:
                    continue
                return _error_summary(
                    "limit_exceeded" if retry_error != "retry_exhausted" else "temporarily_unavailable",
                    retry_error if retry_error != "retry_exhausted" else error,
                )
            return _error_summary("temporarily_unavailable", error)
        transient_code = _transient_envelope_code(envelope)
        if transient_code is not None:
            if not _audit_tool(ctx, name, envelope, transient_code):
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
