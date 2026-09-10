"""Bounded, auditable model/tool loop shared by Web and WeCom workers."""
import asyncio
from dataclasses import dataclass, field
import inspect
import json
import time
from typing import Any

from . import answer, prompts, tools, execution
from .contracts import AnswerDraft
from .model import ModelError, RetryableModelError


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


def _model_schemas(available=None):
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
            spec = registered[item["name"]]
            catalog.append({
                "name": item["name"],
                "description": spec["description"],
                "inputSchema": item.get("inputSchema") or item.get("input_schema") or spec["inputSchema"],
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
    grant = None
    started = deps.clock()
    budget = Budget(
        max_tools=deps.limits.max_tools,
        max_search=deps.limits.max_search,
        max_models=deps.limits.max_models,
        deadline_seconds=deps.limits.deadline_seconds,
    )
    final = None
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
                await asyncio.to_thread(deps.store.append_event, principal, "tool_error", error_code=type(exc).__name__)
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
                                    error_code=type(exc).__name__)
            final = _fallback("temporarily_unavailable", "当前工具目录暂时不可用，未执行任何业务查询；请稍后重试。")
            rendered = await asyncio.to_thread(answer.render_answer, principal, final, deps.store)
            await asyncio.to_thread(deps.store.finish, task_id, deps.worker_id, "failed", rendered,
                              structured_payload=_draft_payload(final))
            return final
        capability_payload = capability.payload if hasattr(capability, "payload") else capability
        history = await asyncio.to_thread(deps.store.task_history, task_id, principal.user_id)
        user_text = await asyncio.to_thread(deps.store.task_text, task_id, principal.user_id)
        messages = prompts.build_messages(history, capability_payload, user_text=user_text)
        schemas = _model_schemas(live_tools)
        repair_attempted = False
        for _ in range(budget.max_models):
            if deps.clock() - started >= budget.deadline_seconds:
                if repair_attempted:
                    final = _fallback("partial", _ANSWER_VALIDATION_FAILURE)
                break
            budget.model_calls += 1
            await asyncio.to_thread(deps.store.record_usage, task_id, model_calls=1)
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
                final = _fallback(
                    "partial",
                    _ANSWER_VALIDATION_FAILURE if repair_attempted
                    else "本次分析达到时间上限，已停止继续请求。",
                )
                break
            except RetryableModelError as exc:
                if repair_attempted:
                    final = _fallback("partial", _ANSWER_VALIDATION_FAILURE)
                    break
                # One bounded repair/retry is represented by the next model budget slot.
                messages.append({"role":"system","content":"工具或模型暂时不可用，请在现有证据基础上给出部分答案，并明确缺失。"})
                if budget.model_calls >= budget.max_models:
                    final = _fallback("temporarily_unavailable", "模型服务暂时不可用，已停止继续请求；请稍后重试。")
                continue
            except ModelError as exc:
                final = _fallback(
                    "partial" if repair_attempted else "temporarily_unavailable",
                    _ANSWER_VALIDATION_FAILURE if repair_attempted else str(exc),
                )
                break
            if turn.tool_calls:
                if repair_attempted:
                    final = _fallback("partial", _ANSWER_VALIDATION_FAILURE)
                    break
                planned_searches = sum(call.name == "search_public" for call in turn.tool_calls)
                if (budget.tool_calls + len(turn.tool_calls) > budget.max_tools
                        or budget.search_calls + planned_searches > budget.max_search):
                    final = _fallback("partial", "已达到本次工具调用上限，以上已取得的证据不足以继续完成全部分析。")
                    break
                assistant_call = {"role":"assistant","tool_calls": [{"id": call.id,"type":"function","function":{"name":call.name,"arguments":json.dumps(call.arguments,ensure_ascii=False)}} for call in turn.tool_calls]}
                messages.append(assistant_call)
                for call in turn.tool_calls:
                    budget.tool_calls += 1
                    if call.name == "search_public":
                        budget.search_calls += 1
                    await asyncio.to_thread(deps.store.record_usage, task_id, tool_calls=1, search_calls=1 if call.name == "search_public" else 0)
                    try:
                        envelope = await _bounded_call(
                            lambda: deps.mcp.call_tool(call.name, call.arguments, grant),
                            clock=deps.clock,
                            started=started,
                            total_seconds=budget.deadline_seconds,
                            call_seconds=deps.limits.tool_timeout_seconds,
                        )
                        await asyncio.to_thread(deps.store.append_event, principal, "tool", tool_name=call.name, arguments=call.arguments,
                                                result_ref=getattr(envelope, "result_ref", None), status=getattr(envelope, "status", None))
                        messages.append({"role":"tool","tool_call_id":call.id,"content":prompts.project_tool_result(envelope)})
                    except ExecutionDeadline:
                        final = _fallback("partial", "本次分析达到时间上限，已停止继续调用工具。")
                        messages.append({"role":"tool","tool_call_id":call.id,
                                         "content":json.dumps({"status":"partial","error":"任务时间已用尽"}, ensure_ascii=False)})
                        break
                    except Exception as exc:
                        await asyncio.to_thread(deps.store.append_event, principal, "tool_error", tool_name=call.name, arguments=call.arguments, error_code=type(exc).__name__)
                        messages.append({"role":"tool","tool_call_id":call.id,"content":json.dumps({"status":"temporarily_unavailable","error":"工具暂时不可用"},ensure_ascii=False)})
                    messages = _bound_messages(messages)
                if final:
                    break
                continue
            try:
                candidate = answer.parse_answer(turn.content or "")
                rendered = await asyncio.to_thread(answer.render_answer, principal, candidate, deps.store)
            except answer.AnswerValidationError as exc:
                await asyncio.to_thread(deps.store.append_event,
                    principal,
                    "answer_validation",
                    status="failed",
                    error_code=exc.issues[0].code if exc.issues else "invalid_value",
                )
                if not repair_attempted and budget.model_calls < budget.max_models:
                    repair_messages = prompts.build_answer_repair_messages(
                        turn.content or "", exc.issues
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
                await asyncio.to_thread(deps.store.finish, task_id, deps.worker_id, _task_state(final), rendered,
                                  structured_payload=_draft_payload(final))
                return final
        if final is None:
            final = _fallback("partial", "本次分析达到时间或调用预算上限，未能形成完整答案。")
        rendered = await asyncio.to_thread(answer.render_answer, principal, final, deps.store)
        await asyncio.to_thread(deps.store.finish, task_id, deps.worker_id, _task_state(final), rendered,
                          structured_payload=_draft_payload(final))
        return final
    finally:
        if grant:
            await asyncio.to_thread(deps.store.revoke_task_grants, task_id)
