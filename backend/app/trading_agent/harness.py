"""Bounded, auditable model/tool loop shared by Web and WeCom workers."""
import asyncio
from dataclasses import dataclass, field
import inspect
import json
import logging
import re
import time
from typing import Any, Literal

from . import answer, prompts, progress, tools, execution, research_policy
from .answer_contracts import ValidatedAnswer21, Limitation
from .answer_v21 import Answer21Issue, apply_policy_limits, policy_answer21
from .contracts import AnswerDraft
from .model import ModelError, RetryableModelError
from .research_policy import (
    RequestPlan,
    enforce_research_policy,
    has_internal_request,
    public_tools_configured,
    public_tools_allowed,
    restricted_module_requests,
)


@dataclass(frozen=True)
class RuntimeLimits:
    """Per-task limits; values are configurable for controlled environments."""

    max_tools: int = 8
    max_search: int = 2
    max_models: int = 6
    deadline_seconds: float = execution.TASK_SECONDS
    model_timeout_seconds: float = 15.0
    tool_timeout_seconds: float = execution.TOOL_SECONDS


@dataclass(frozen=True)
class RuntimeDeps:
    store: Any
    model: Any
    mcp: Any
    clock: Any = time.monotonic
    worker_id: str = "agent-v2"
    limits: RuntimeLimits = field(default_factory=RuntimeLimits)
    answer_protocol: Literal["2.0", "2.1"] = "2.1"


@dataclass
class Budget:
    tool_calls: int = 0
    search_calls: int = 0
    model_calls: int = 0
    max_tools: int = 8
    max_search: int = 2
    max_models: int = 6
    deadline_seconds: float = execution.TASK_SECONDS

    def available(self, clock, started):
        return self.tool_calls < self.max_tools and self.model_calls < self.max_models and clock() - started < self.deadline_seconds


async def _await(value):
    return await value if inspect.isawaitable(value) else value


class ExecutionDeadline(TimeoutError):
    """The task's total wall-clock budget has expired."""


def _safe_error_code(exc: Exception) -> str:
    value = getattr(exc, "code", None)
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", value):
        return value
    return type(exc).__name__


def _position_preflight_args(user_text: str) -> dict[str, Any] | None:
    """Build the narrow, read-only position query needed by mixed requests."""
    text = str(user_text or "")
    if "持仓" not in text or not re.search(r"手数|持仓数量|持仓.*(?:数量|多少)", text):
        return None
    asset_type = "future" if "期货" in text and "期权" not in text else "all"
    return {
        "as_of_mode": "latest",
        "as_of_date": None,
        "asset_type": asset_type,
        "contracts": [],
        "direction": "all",
        "classification": "all",
        "valuation_mode": "quantity_only",
        "required_metrics": ["quantity"],
    }


def _annual_inventory_preflight_args(user_text: str) -> dict[str, Any] | None:
    """Build the registered range-plus-period-end query for explicit annual asks."""
    text = str(user_text or "")
    if "库存" not in text or not re.search(r"业务年度|每个年度|年度汇总", text):
        return None
    if not re.search(r"最后可用|年度末|期末|每年.*最后", text):
        return None
    dates = re.findall(r"20\d{2}-\d{2}-\d{2}", text)
    if len(dates) < 2:
        return None
    start_date, end_date = dates[0], dates[1]
    if start_date > end_date:
        return None
    return {
        "query": {
            "dataset": "inventory_summary",
            "mode": "range",
            "start_date": start_date,
            "end_date": end_date,
            "filters": {"summary_metrics": ["库存总量"], "data_states": ["observed"]},
            "fields": [
                "observation_date", "business_year", "port", "region", "scope_type",
                "summary_metric", "value", "unit", "value_state",
            ],
            "batch_size": 20000,
        },
        "summary": {"measure": "value", "operation": "period_end", "group_by": ["business_year"]},
    }


async def _bounded_call(factory, *, clock, started, total_seconds, call_seconds):
    """Run an async or sync-compatible call without exceeding task time."""
    remaining = float(total_seconds) - (clock() - started)
    if remaining <= 0:
        raise ExecutionDeadline()
    try:
        value = factory()
        return await asyncio.wait_for(
            _await(value), timeout=min(float(call_seconds), remaining)
        )
    except asyncio.TimeoutError as exc:
        raise ExecutionDeadline() from exc


async def _model_call(model, messages, schemas, timeout):
    with execution.phase("model"):
        if inspect.iscoroutinefunction(getattr(model, "next_turn", None)):
            return await asyncio.wait_for(model.next_turn(messages, schemas, timeout), timeout=max(.1, timeout))
        return await asyncio.wait_for(asyncio.to_thread(model.next_turn, messages, schemas, timeout), timeout=max(.1, timeout))


def _model_schemas(available=None, allowed_names=None):
    """Convert the live MCP catalog to the model schema after registry filtering."""
    if available is None:
        catalog = tools.tool_schemas()
    else:
        raw_tools = available.get("tools", available) if isinstance(available, dict) else getattr(available, "tools", available)
        if not isinstance(raw_tools, (list, tuple)):
            raise ValueError("MCP工具目录格式无效")
        registered = {item["name"]: item for item in tools.tool_schemas()}
        catalog = []
        for item in raw_tools:
            if hasattr(item, "model_dump"):
                item = item.model_dump(mode="json")
            if not isinstance(item, dict) or item.get("name") not in registered:
                continue
            if allowed_names is not None and item.get("name") not in allowed_names:
                continue
            spec = registered[item["name"]]
            catalog.append({
                "name": item["name"],
                "description": spec["description"],
                # The live catalog controls availability; the shared dispatch
                # registry owns argument constraints lost by MCP wrappers.
                "inputSchema": spec["inputSchema"],
            })
        if not catalog:
            raise ValueError("MCP工具目录为空")
    return [{"type": "function", "function": {"name": item["name"], "description": item["description"], "parameters": item["inputSchema"]}}
            for item in catalog]


def _fallback(status, text):
    return AnswerDraft(status=status, paragraphs=[{"kind":"knowledge","text":text}])


def _draft_payload(draft: AnswerDraft) -> dict[str, Any]:
    """Persist only the validated answer contract, never model protocol messages."""
    return {
        "schema_version": "2.0",
        "status": draft.status,
        "paragraphs": [paragraph.model_dump(mode="json") for paragraph in draft.paragraphs],
        "fact_refs": list(draft.fact_refs),
        "missing": list(draft.missing),
        "clarification": draft.clarification,
    }


def _task_state(draft: AnswerDraft) -> str:
    if draft.status == "complete":
        return "succeeded"
    if draft.status == "temporarily_unavailable":
        return "failed"
    return "partial"


def _looks_like_answer21(raw: Any) -> bool:
    if not isinstance(raw, str):
        return False
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return isinstance(value, dict) and value.get("schema_version") == "2.1"


def _validated21_issues(result: ValidatedAnswer21) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in result.limitations[:5]]


def _with_source_limits(result, unavailable):
    if not any(unavailable.values()):
        return result
    code = "public_not_configured" if unavailable.get("policy") == "public_not_configured" else "public_source_unavailable"
    message = "公开搜索尚未配置授权服务，联合研究尚未执行；已核验的内部结果仍可查看。" if code == "public_not_configured" else "公开搜索或正文读取不可用，联合研究尚未完成；已核验的内部结果仍可查看。"
    limitation = Limitation(code=code, message=message)
    body = result.body_markdown
    if message not in body:
        body = f"{body.rstrip()}\n\n{message}" if body.strip() else message
    return result.model_copy(update={"delivery_status": "partial" if result.delivery_status == "complete" else result.delivery_status,
        "body_markdown": body, "plain_text": body,
        "limitations": [*result.limitations, limitation]})


def _diagnostic_issue(item):
    allowed = {"schema_version", "body_markdown", "spans", "views", "id", "kind", "start", "end",
               "refs", "depends_on", "result_ref", "fields", "title", "sort_by", "descending", ""}
    parts = str(item.get("path", "/")).split("/")[:8]
    path = "/".join(part if part in allowed or (part.isascii() and part.isdecimal() and len(part) <= 5) else "?" for part in parts)
    return {"code": item.get("code"), "path": path}


def _log_model_diagnostic(task_id, turn, *, attempt, repair):
    data = {"task_id": task_id, "attempt": attempt, "repair": repair,
            "finish_reason": turn.finish_reason if turn.finish_reason in {"stop", "length", "tool_calls", "content_filter"} else "unknown",
            "content_chars": len(turn.content or ""), "tool_calls": len(turn.tool_calls)}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = turn.usage.get(key)
        if type(value) is int and value >= 0:
            data[key] = value
    if not turn.tool_calls:
        try:
            json.loads(turn.content or "")
        except json.JSONDecodeError as exc:
            data.update(json_error_offset=exc.pos, json_error_line=exc.lineno,
                        json_error_column=exc.colno, json_error_kind=exc.msg)
    logging.getLogger(__name__).warning("agent_model_diagnostic %s", json.dumps(data))


def _fallback21(store_api, principal, result_refs, code: str) -> ValidatedAnswer21:
    return answer.build_fallback21(principal, result_refs, {}, code, store_api)


async def _finish21(deps: RuntimeDeps, task_id: int, result: ValidatedAnswer21):
    await asyncio.to_thread(
        deps.store.finish,
        task_id,
        deps.worker_id,
        answer.task_state21(result.delivery_status),
        result.plain_text,
        structured_payload=result.model_dump(mode="json"),
    )


async def _record_stage(deps: RuntimeDeps, principal, stage: str, status: str):
    await asyncio.to_thread(
        progress.record_stage, principal, stage, status, store_api=deps.store
    )


def _bound_messages(messages, max_chars=48000):
    sizes = [len(str(item.get("content") or "")) + len(json.dumps(item.get("tool_calls") or [], ensure_ascii=False))
             for item in messages]
    if sum(sizes) <= max_chars:
        return messages
    # Keep the policy and capability system messages, then the newest context that fits.
    head = [item for item in messages[:2] if item.get("role") == "system"]
    budget = max_chars - sum(len(str(item.get("content") or "")) for item in head)
    tail = []
    for item, size in reversed(list(zip(messages[2:], sizes[2:]))):
        if size > budget:
            continue
        tail.append(item)
        budget -= size
    return head + list(reversed(tail))


def _messages_size(messages) -> int:
    return sum(
        len(str(item.get("content") or ""))
        + len(json.dumps(item.get("tool_calls") or [], ensure_ascii=False))
        for item in messages
    )


_ANSWER_VALIDATION_FAILURE = (
    "本次回答未通过格式或证据校验，系统未交付业务结论；这是系统处理问题，当前任务已结束。"
)
_PUBLIC_QUERY_REJECTED = (
    "公开检索子问题未通过隐私校验，系统没有发送该查询。请仅从用户的公开主题重新生成一个不含账户、金额、订单、客户、地点、编码或真实结果的公开子问题；"
    "本次最多再调用一次 search_public。若无法安全拆分，请保留已有内部答案，并明确说明公开研究受限。"
)


async def run_task(task_id: int, deps: RuntimeDeps) -> AnswerDraft:
    """Run one leased task. No model reasoning or private raw tool payload is persisted."""
    try:
        principal = await asyncio.to_thread(deps.store.principal_for_task, task_id)
    except Exception as exc:
        final = _fallback("temporarily_unavailable", "任务权限已失效，未执行数据查询；请重新提问。")
        try:
            rendered = await asyncio.to_thread(answer.render_answer, None, final, deps.store)
            await asyncio.to_thread(deps.store.finish, task_id, deps.worker_id, "failed", rendered,
                              error=type(exc).__name__, structured_payload=_draft_payload(final))
        except Exception:
            pass
        return final
    user_text = await asyncio.to_thread(deps.store.task_text, task_id, principal.user_id)
    plan = enforce_research_policy(
        user_text,
        RequestPlan(mode="clarification_required", reason="ambiguous", domains=["public"]),
    )
    configured = public_tools_configured()
    public_unavailable = {"policy": "public_not_configured"} if plan.mode == "research_allowed" and not configured else {}
    restricted_modules = restricted_module_requests(user_text)
    try:
        await asyncio.to_thread(
            deps.store.append_event,
            principal,
            "research_policy",
            tool_name=",".join(plan.domains),
            status=plan.mode,
            error_code=plan.reason,
        )
    except Exception:
        # The local decision remains fail-closed even if audit persistence is
        # temporarily unavailable.
        pass
    if restricted_modules and not has_internal_request(user_text):
        if deps.answer_protocol == "2.1":
            final = policy_answer21(restricted_modules)
            await _finish21(deps, task_id, final)
            return final
        final = _fallback("partial", policy_answer21(restricted_modules).plain_text)
        rendered = await asyncio.to_thread(answer.render_answer, principal, final, deps.store)
        await asyncio.to_thread(deps.store.finish, task_id, deps.worker_id, "partial", rendered,
                                structured_payload=_draft_payload(final))
        return final
    grant = None
    started = deps.clock()
    budget = Budget(
        max_tools=deps.limits.max_tools,
        max_search=deps.limits.max_search,
        max_models=deps.limits.max_models,
        deadline_seconds=deps.limits.deadline_seconds,
    )
    final = None
    protocol21_mode = deps.answer_protocol == "2.1"
    known_result_refs: list[str] = []
    public_query_repair_attempted = False

    def mark_public_source_gap():
        if (plan.mode == "research_allowed"
                and budget.search_calls == 0
                and not any(public_unavailable.values())):
            public_unavailable["policy"] = (
                "public_not_configured" if not configured else "public_source_unavailable"
            )

    def failure_fallback(status: str, text: str, code: str) -> Any:
        if protocol21_mode:
            return _fallback21(deps.store, principal, known_result_refs, code)
        return _fallback(status, text)

    try:
        try:
            grant = await asyncio.to_thread(deps.store.issue_grant, principal)
        except Exception as exc:
            final = _fallback("temporarily_unavailable", "任务权限在执行前已失效，未执行任何业务查询；请重新提问。")
            rendered = await asyncio.to_thread(answer.render_answer, principal, final, deps.store)
            await asyncio.to_thread(deps.store.finish, task_id, deps.worker_id, "failed", rendered,
                              error=type(exc).__name__, structured_payload=_draft_payload(final))
            return final
        live_tools = None
        if hasattr(deps.mcp, "list_tools"):
            budget.tool_calls += 1
            await asyncio.to_thread(deps.store.record_usage, task_id, tool_calls=1)
            try:
                live_tools = await _bounded_call(
                    lambda: deps.mcp.list_tools(grant),
                    clock=deps.clock,
                    started=started,
                    total_seconds=budget.deadline_seconds,
                    call_seconds=deps.limits.tool_timeout_seconds,
                )
                await asyncio.to_thread(deps.store.append_event, principal, "tool_catalog", status="complete")
            except ExecutionDeadline:
                final = _fallback("partial", "本次分析达到时间上限，未能读取完整工具目录。")
                rendered = await asyncio.to_thread(answer.render_answer, principal, final, deps.store)
                await asyncio.to_thread(deps.store.finish, task_id, deps.worker_id, "partial", rendered,
                                  structured_payload=_draft_payload(final))
                return final
            except Exception as exc:
                await asyncio.to_thread(deps.store.append_event, principal, "tool_error", error_code=_safe_error_code(exc))
                final = _fallback("temporarily_unavailable", "当前工具目录暂时不可用，未执行任何业务查询；请稍后重试。")
                rendered = await asyncio.to_thread(answer.render_answer, principal, final, deps.store)
                await asyncio.to_thread(deps.store.finish, task_id, deps.worker_id, "failed", rendered,
                                  structured_payload=_draft_payload(final))
                return final
        # Always discover actual server capabilities before interpreting the question.
        budget.tool_calls += 1
        await asyncio.to_thread(deps.store.record_usage, task_id, tool_calls=1)
        try:
            capability = await _bounded_call(
                lambda: deps.mcp.call_tool("describe_capabilities", {}, grant),
                clock=deps.clock,
                started=started,
                total_seconds=budget.deadline_seconds,
                call_seconds=deps.limits.tool_timeout_seconds,
            )
            await asyncio.to_thread(deps.store.append_event, principal, "tool", tool_name="describe_capabilities",
                                    result_ref=getattr(capability, "result_ref", None),
                                    status=getattr(capability, "status", None))
        except ExecutionDeadline:
            final = _fallback("partial", "本次分析达到时间上限，未能读取当前能力目录。")
            rendered = await asyncio.to_thread(answer.render_answer, principal, final, deps.store)
            await asyncio.to_thread(deps.store.finish, task_id, deps.worker_id, "partial", rendered,
                              structured_payload=_draft_payload(final))
            return final
        except Exception as exc:
            await asyncio.to_thread(deps.store.append_event, principal, "tool_error", tool_name="describe_capabilities",
                                    error_code=_safe_error_code(exc))
            final = _fallback("temporarily_unavailable", "当前工具目录暂时不可用，未执行任何业务查询；请稍后重试。")
            rendered = await asyncio.to_thread(answer.render_answer, principal, final, deps.store)
            await asyncio.to_thread(deps.store.finish, task_id, deps.worker_id, "failed", rendered,
                              structured_payload=_draft_payload(final))
            return final
        if getattr(capability, "result_ref", None):
            known_result_refs.append(str(capability.result_ref))
        capability_payload = capability.payload if hasattr(capability, "payload") else capability
        if isinstance(capability_payload, dict):
            capability_payload = dict(capability_payload)
            capability_payload["research_policy"] = {
                "mode": plan.mode, "reason": plan.reason, "domains": plan.domains,
                "clarification": plan.clarification, "configured": configured,
                "readiness": research_policy.public_provider_readiness(),
            }
        history = await asyncio.to_thread(deps.store.task_history, task_id, principal.user_id)
        preflight_context = []
        preflight_args = (
            _position_preflight_args(user_text)
            if restricted_modules and has_internal_request(user_text) else None
        )
        if preflight_args is not None and budget.tool_calls < budget.max_tools:
            budget.tool_calls += 1
            await asyncio.to_thread(deps.store.record_usage, task_id, tool_calls=1)
            try:
                preflight = await _bounded_call(
                    lambda: deps.mcp.call_tool("query_positions", preflight_args, grant),
                    clock=deps.clock,
                    started=started,
                    total_seconds=budget.deadline_seconds,
                    call_seconds=deps.limits.tool_timeout_seconds,
                )
                if getattr(preflight, "result_ref", None):
                    known_result_refs.append(str(preflight.result_ref))
                await asyncio.to_thread(
                    deps.store.append_event,
                    principal,
                    "tool",
                    tool_name="query_positions",
                    arguments=preflight_args,
                    result_ref=getattr(preflight, "result_ref", None),
                    status=getattr(preflight, "status", None),
                )
                if getattr(preflight, "status", None) in {"complete", "partial"}:
                    preflight_context.append(
                        "服务端已为当前混合请求预取仅限授权账户的持仓证据；请优先使用该结果回答持仓部分，"
                        "不要查询或推断受限模块：" + prompts.project_tool_result(preflight)
                    )
            except ExecutionDeadline:
                await asyncio.to_thread(
                    deps.store.append_event,
                    principal,
                    "tool_error",
                    tool_name="query_positions",
                    error_code="deadline_exceeded",
                )
            except Exception as exc:
                await asyncio.to_thread(
                    deps.store.append_event,
                    principal,
                    "tool_error",
                    tool_name="query_positions",
                    arguments=preflight_args,
                    error_code=_safe_error_code(exc),
                )
        annual_plan = _annual_inventory_preflight_args(user_text)
        if annual_plan is not None and budget.tool_calls + 2 <= budget.max_tools:
            annual_query = None
            budget.tool_calls += 1
            await asyncio.to_thread(deps.store.record_usage, task_id, tool_calls=1)
            try:
                annual_query = await _bounded_call(
                    lambda: deps.mcp.call_tool("query_dataset", annual_plan["query"], grant),
                    clock=deps.clock,
                    started=started,
                    total_seconds=budget.deadline_seconds,
                    call_seconds=deps.limits.tool_timeout_seconds,
                )
                if getattr(annual_query, "result_ref", None):
                    known_result_refs.append(str(annual_query.result_ref))
                await asyncio.to_thread(
                    deps.store.append_event,
                    principal,
                    "tool",
                    tool_name="query_dataset",
                    arguments=annual_plan["query"],
                    result_ref=getattr(annual_query, "result_ref", None),
                    status=getattr(annual_query, "status", None),
                )
                if getattr(annual_query, "status", None) in {"complete", "partial"}:
                    preflight_context.append(
                        "服务端已按用户明确的完整日期范围预取库存原始证据；不得把该范围改写成365天或缩短年份："
                        + prompts.project_tool_result(annual_query)
                    )
                    if getattr(annual_query, "result_ref", None):
                        budget.tool_calls += 1
                        await asyncio.to_thread(deps.store.record_usage, task_id, tool_calls=1)
                        annual_summary_args = {
                            **annual_plan["summary"],
                            "result_ref": str(annual_query.result_ref),
                        }
                        annual_summary = await _bounded_call(
                            lambda: deps.mcp.call_tool("summarize_dataset", annual_summary_args, grant),
                            clock=deps.clock,
                            started=started,
                            total_seconds=budget.deadline_seconds,
                            call_seconds=deps.limits.tool_timeout_seconds,
                        )
                        if getattr(annual_summary, "result_ref", None):
                            known_result_refs.append(str(annual_summary.result_ref))
                        await asyncio.to_thread(
                            deps.store.append_event,
                            principal,
                            "tool",
                            tool_name="summarize_dataset",
                            arguments=annual_summary_args,
                            result_ref=getattr(annual_summary, "result_ref", None),
                            status=getattr(annual_summary, "status", None),
                        )
                        if getattr(annual_summary, "status", None) in {"complete", "partial"}:
                            preflight_context.append(
                                "服务端已按每个业务年度的最后可用观察日完成确定性 period_end 汇总；"
                                "请优先使用该汇总创建表格，明确列出缺失年度，不要重新扩大或缩短查询范围："
                                + prompts.project_tool_result(annual_summary)
                            )
            except ExecutionDeadline:
                await asyncio.to_thread(
                    deps.store.append_event,
                    principal,
                    "tool_error",
                    tool_name="summarize_dataset" if annual_query is not None else "query_dataset",
                    error_code="deadline_exceeded",
                )
            except Exception as exc:
                await asyncio.to_thread(
                    deps.store.append_event,
                    principal,
                    "tool_error",
                    tool_name="summarize_dataset" if annual_query is not None else "query_dataset",
                    error_code=_safe_error_code(exc),
                )
        messages = prompts.build_messages(
            history,
            capability_payload,
            user_text=user_text,
            restricted_modules=restricted_modules,
            public_research_unavailable=bool(public_unavailable),
        )
        if preflight_context:
            messages.append({"role": "system", "content": "\n\n".join(preflight_context)})
        if live_tools is not None and hasattr(deps.mcp, "endpoint"):
            raw_live_tools = live_tools.get("tools", live_tools) if isinstance(live_tools, dict) else getattr(live_tools, "tools", live_tools)
            if isinstance(raw_live_tools, (list, tuple)) and not public_tools_allowed(plan, configured):
                live_tools = {"tools": [
                    item for item in raw_live_tools
                    if (item.get("name") if isinstance(item, dict) else getattr(item, "name", None))
                    not in {"search_public", "read_public"}
                ]}
        allowed_capability_tools = None
        if isinstance(capability_payload, dict) and isinstance(capability_payload.get("tools"), list):
            allowed_capability_tools = {
                item if isinstance(item, str) else item.get("name")
                for item in capability_payload["tools"]
                if isinstance(item, str) or isinstance(item, dict)
            }
            allowed_capability_tools.discard(None)
        schemas = _model_schemas(live_tools, allowed_names=allowed_capability_tools)
        allowed_tool_names = {
            item.get("function", {}).get("name")
            for item in schemas
            if isinstance(item, dict) and isinstance(item.get("function"), dict)
        }
        repair_attempted = False
        recoverable_draft21 = None
        for _ in range(budget.max_models):
            if deps.clock() - started >= budget.deadline_seconds:
                if repair_attempted:
                    final = failure_fallback("partial", _ANSWER_VALIDATION_FAILURE, "answer_validation_failed")
                break
            budget.model_calls += 1
            await asyncio.to_thread(deps.store.record_usage, task_id, model_calls=1)
            await _record_stage(deps, principal, "composing", "running")
            try:
                turn = await _bounded_call(
                    lambda: _model_call(
                        deps.model,
                        messages,
                        schemas,
                        min(deps.limits.model_timeout_seconds,
                            budget.deadline_seconds - (deps.clock() - started)),
                    ),
                    clock=deps.clock,
                    started=started,
                    total_seconds=budget.deadline_seconds,
                    call_seconds=deps.limits.model_timeout_seconds,
                )
            except ExecutionDeadline:
                final = failure_fallback(
                    "partial",
                    _ANSWER_VALIDATION_FAILURE if repair_attempted
                    else "本次分析达到时间上限，已停止继续请求。",
                    "answer_validation_failed" if repair_attempted else "deadline_exceeded",
                )
                break
            except RetryableModelError as exc:
                if repair_attempted:
                    final = failure_fallback("partial", _ANSWER_VALIDATION_FAILURE, "answer_validation_failed")
                    break
                # One bounded repair/retry is represented by the next model budget slot.
                messages.append({"role":"system","content":"工具或模型暂时不可用，请在现有证据基础上给出部分答案，并明确缺失。"})
                if budget.model_calls >= budget.max_models:
                    final = _fallback("temporarily_unavailable", "模型服务暂时不可用，已停止继续请求；请稍后重试。")
                continue
            except ModelError as exc:
                final = failure_fallback(
                    "partial" if repair_attempted else "temporarily_unavailable",
                    _ANSWER_VALIDATION_FAILURE if repair_attempted else str(exc),
                    "answer_validation_failed" if repair_attempted else "model_unavailable",
                )
                break
            _log_model_diagnostic(task_id, turn, attempt=budget.model_calls, repair=repair_attempted)
            if turn.tool_calls:
                if repair_attempted:
                    final = failure_fallback("partial", _ANSWER_VALIDATION_FAILURE, "answer_validation_failed")
                    break
                planned_searches = sum(call.name == "search_public" for call in turn.tool_calls)
                if (budget.tool_calls + len(turn.tool_calls) > budget.max_tools
                        or budget.search_calls + planned_searches > budget.max_search):
                    final = failure_fallback(
                        "partial",
                        "已达到本次工具调用上限，以上已取得的证据不足以继续完成全部分析。",
                        "budget_exhausted",
                    )
                    break
                assistant_call = {"role":"assistant","tool_calls": [{"id": call.id,"type":"function","function":{"name":call.name,"arguments":json.dumps(call.arguments,ensure_ascii=False)}} for call in turn.tool_calls]}
                messages.append(assistant_call)
                for call in turn.tool_calls:
                    budget.tool_calls += 1
                    if call.name == "search_public":
                        budget.search_calls += 1
                    await asyncio.to_thread(deps.store.record_usage, task_id, tool_calls=1, search_calls=1 if call.name == "search_public" else 0)
                    if call.name not in allowed_tool_names:
                        await asyncio.to_thread(
                            deps.store.append_event,
                            principal,
                            "tool_error",
                            tool_name="unknown",
                            status="rejected",
                            error_code="tool_not_allowed",
                        )
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": json.dumps(
                                {"status": "rejected", "error": "工具不在当前授权目录中，未执行任何操作"},
                                ensure_ascii=False,
                            ),
                        })
                        continue
                    try:
                        envelope = await _bounded_call(
                            lambda: deps.mcp.call_tool(call.name, call.arguments, grant),
                            clock=deps.clock,
                            started=started,
                            total_seconds=budget.deadline_seconds,
                            call_seconds=deps.limits.tool_timeout_seconds,
                        )
                        if getattr(envelope, "result_ref", None):
                            known_result_refs.append(str(envelope.result_ref))
                        await asyncio.to_thread(deps.store.append_event, principal, "tool", tool_name=call.name, arguments=call.arguments,
                                                result_ref=getattr(envelope, "result_ref", None), status=getattr(envelope, "status", None))
                        envelope_payload = getattr(envelope, "payload", None)
                        if call.name in {"search_public", "read_public"}:
                            public_unavailable[call.name] = getattr(envelope, "status", None) not in {"complete", "partial"}
                        if (call.name == "search_public" and isinstance(envelope_payload, dict)
                                and envelope_payload.get("kind") == "public_query_rejected"):
                            if (not public_query_repair_attempted
                                    and budget.search_calls < budget.max_search
                                    and budget.model_calls < budget.max_models):
                                public_query_repair_attempted = True
                                content = json.dumps({"status": "rejected", "error": _PUBLIC_QUERY_REJECTED}, ensure_ascii=False)
                            else:
                                content = json.dumps({"status": "rejected", "error": "公开检索子问题未发送；请保留已有答案并明确说明公开研究受限。"}, ensure_ascii=False)
                            messages.append({"role":"tool","tool_call_id":call.id,"content":content})
                        else:
                            messages.append({"role":"tool","tool_call_id":call.id,"content":prompts.project_tool_result(envelope)})
                    except ExecutionDeadline:
                        final = failure_fallback("partial", "本次分析达到时间上限，已停止继续调用工具。", "deadline_exceeded")
                        messages.append({"role":"tool","tool_call_id":call.id,
                                         "content":json.dumps({"status":"partial","error":"任务时间已用尽"}, ensure_ascii=False)})
                        break
                    except Exception as exc:
                        await asyncio.to_thread(deps.store.append_event, principal, "tool_error", tool_name=call.name, arguments=call.arguments, error_code=_safe_error_code(exc))
                        try:
                            from .research import QueryRejected
                            public_query_rejected = isinstance(exc, QueryRejected)
                        except ImportError:
                            public_query_rejected = False
                        if public_query_rejected and not public_query_repair_attempted and budget.search_calls < budget.max_search and budget.model_calls < budget.max_models:
                            public_query_repair_attempted = True
                            content = json.dumps({"status": "rejected", "error": _PUBLIC_QUERY_REJECTED}, ensure_ascii=False)
                        elif public_query_rejected:
                            content = json.dumps({"status": "rejected", "error": "公开检索子问题未发送；请保留已有答案并明确说明公开研究受限。"}, ensure_ascii=False)
                        else:
                            content = json.dumps({"status":"temporarily_unavailable","error":"工具暂时不可用"},ensure_ascii=False)
                        messages.append({"role":"tool","tool_call_id":call.id,"content":content})
                    messages = _bound_messages(messages)
                if final:
                    break
                continue
            raw_answer = turn.content or ""
            if protocol21_mode or _looks_like_answer21(raw_answer):
                protocol21_mode = True
                await _record_stage(deps, principal, "composing", "complete")
                await _record_stage(deps, principal, "validation", "running")
                v21_issues = []
                try:
                    if turn.finish_reason == "length":
                        raise answer.Answer21ValidationError([Answer21Issue("output_truncated", "/", "输出达到长度上限，请缩短解释并使用表格引用。")])
                    draft21 = answer.parse_answer21(raw_answer)
                    validated21 = await asyncio.to_thread(
                        answer.validate_answer21, principal, draft21, deps.store
                    )
                    if validated21.views:
                        recoverable_draft21 = draft21
                    evidence_errors = {"uncovered_claim", "unreferenced_number", "missing_reference", "invalid_reference", "reference_unavailable"}
                    if validated21.delivery_status == "failed" or any(item.code in evidence_errors for item in validated21.limitations):
                        v21_issues = _validated21_issues(validated21)
                except answer.Answer21ValidationError as exc:
                    v21_issues = exc.as_dicts()
                if v21_issues:
                    logging.getLogger(__name__).warning("agent_answer_validation task_id=%s attempt=%s codes=%s", task_id, budget.model_calls,
                        json.dumps([_diagnostic_issue(item) for item in v21_issues[:5]]))
                    await _record_stage(deps, principal, "validation", "failed")
                    await asyncio.to_thread(
                        deps.store.append_event,
                        principal,
                        "answer_validation",
                        status="failed",
                        error_code=v21_issues[0].get("code", "invalid_value"),
                    )
                    if not repair_attempted and budget.model_calls < budget.max_models:
                        repair_messages = prompts.build_answer_repair_messages(raw_answer, v21_issues, finish_reason=turn.finish_reason)
                        bounded = _bound_messages(messages)
                        if _messages_size(bounded + repair_messages) > 48000:
                            final = (await asyncio.to_thread(answer.validate_answer21, principal, recoverable_draft21, deps.store)
                                     if recoverable_draft21 is not None else _fallback21(deps.store, principal, known_result_refs, "answer_validation_failed"))
                            break
                        messages = bounded + repair_messages
                        repair_attempted = True
                        continue
                    final = (await asyncio.to_thread(answer.validate_answer21, principal, recoverable_draft21, deps.store)
                             if recoverable_draft21 is not None else _fallback21(deps.store, principal, known_result_refs, "answer_validation_failed"))
                    break
                mark_public_source_gap()
                final = apply_policy_limits(_with_source_limits(validated21, public_unavailable), restricted_modules)
                await _record_stage(deps, principal, "validation", "complete")
                await _finish21(deps, task_id, final)
                return final
            else:
                await _record_stage(deps, principal, "composing", "complete")
                await _record_stage(deps, principal, "validation", "running")
                try:
                    candidate = answer.parse_answer(raw_answer)
                    rendered = await asyncio.to_thread(answer.render_answer, principal, candidate, deps.store)
                except answer.AnswerValidationError as exc:
                    await _record_stage(deps, principal, "validation", "failed")
                    await asyncio.to_thread(deps.store.append_event,
                        principal,
                        "answer_validation",
                        status="failed",
                        error_code=exc.issues[0].code if exc.issues else "invalid_value",
                    )
                    if not repair_attempted and budget.model_calls < budget.max_models:
                        repair_messages = prompts.build_answer_repair_messages(
                            raw_answer, exc.issues
                        )
                        bounded = _bound_messages(messages)
                        if _messages_size(bounded + repair_messages) > 48000:
                            final = _fallback("partial", _ANSWER_VALIDATION_FAILURE)
                            break
                        messages = bounded + repair_messages
                        repair_attempted = True
                        continue
                    final = _fallback("partial", _ANSWER_VALIDATION_FAILURE)
                    break
                else:
                    final = candidate
                    await _record_stage(deps, principal, "validation", "complete")
                    await asyncio.to_thread(deps.store.finish, task_id, deps.worker_id, _task_state(final), rendered,
                                      structured_payload=_draft_payload(final))
                    return final
        mark_public_source_gap()
        if final is None:
            final = failure_fallback("partial", "本次分析达到时间或调用预算上限，未能形成完整答案。", "budget_exhausted")
        if isinstance(final, ValidatedAnswer21):
            final = apply_policy_limits(_with_source_limits(final, public_unavailable), restricted_modules)
            await _finish21(deps, task_id, final)
            return final
        rendered = await asyncio.to_thread(answer.render_answer, principal, final, deps.store)
        await asyncio.to_thread(deps.store.finish, task_id, deps.worker_id, _task_state(final), rendered,
                          structured_payload=_draft_payload(final))
        return final
    finally:
        if grant:
            await asyncio.to_thread(deps.store.revoke_task_grants, task_id)
