"""Bounded, auditable model/tool loop shared by Web and WeCom workers."""
import asyncio
from dataclasses import dataclass
import inspect
import json
import time
from typing import Any

from . import answer, prompts, tools
from .contracts import AnswerDraft
from .model import ModelError, RetryableModelError


@dataclass(frozen=True)
class RuntimeDeps:
    store: Any
    model: Any
    mcp: Any
    clock: Any = time.monotonic
    worker_id: str = "agent-v2"


@dataclass
class Budget:
    tool_calls: int = 0
    search_calls: int = 0
    model_calls: int = 0
    max_tools: int = 8
    max_search: int = 2
    max_models: int = 6
    deadline_seconds: float = 90

    def available(self, clock, started):
        return self.tool_calls < self.max_tools and self.model_calls < self.max_models and clock() - started < self.deadline_seconds


async def _await(value):
    return await value if inspect.isawaitable(value) else value


async def _model_call(model, messages, schemas, timeout):
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


async def run_task(task_id: int, deps: RuntimeDeps) -> AnswerDraft:
    """Run one leased task. No model reasoning or private raw tool payload is persisted."""
    try:
        principal = deps.store.principal_for_task(task_id)
    except Exception as exc:
        final = _fallback("temporarily_unavailable", "任务权限已失效，未执行数据查询；请重新提问。")
        try:
            rendered = answer.render_answer(None, final, deps.store)
            deps.store.finish(task_id, deps.worker_id, "failed", rendered,
                              error=type(exc).__name__, structured_payload=_draft_payload(final))
        except Exception:
            pass
        return final
    grant = None
    started = deps.clock()
    budget = Budget()
    final = None
    try:
        try:
            grant = deps.store.issue_grant(principal)
        except Exception as exc:
            final = _fallback("temporarily_unavailable", "任务权限在执行前已失效，未执行任何业务查询；请重新提问。")
            rendered = answer.render_answer(principal, final, deps.store)
            deps.store.finish(task_id, deps.worker_id, "failed", rendered,
                              error=type(exc).__name__, structured_payload=_draft_payload(final))
            return final
        live_tools = None
        if hasattr(deps.mcp, "list_tools"):
            budget.tool_calls += 1
            deps.store.record_usage(task_id, tool_calls=1)
            try:
                live_tools = await _await(deps.mcp.list_tools(grant))
                deps.store.append_event(principal, "tool_catalog", status="complete")
            except Exception as exc:
                deps.store.append_event(principal, "tool_error", error_code=type(exc).__name__)
                final = _fallback("temporarily_unavailable", "当前工具目录暂时不可用，未执行任何业务查询；请稍后重试。")
                rendered = answer.render_answer(principal, final, deps.store)
                deps.store.finish(task_id, deps.worker_id, "failed", rendered,
                                  structured_payload=_draft_payload(final))
                return final
        # Always discover actual server capabilities before interpreting the question.
        budget.tool_calls += 1
        deps.store.record_usage(task_id, tool_calls=1)
        try:
            capability = await _await(deps.mcp.call_tool("describe_capabilities", {}, grant))
            deps.store.append_event(principal, "tool", tool_name="describe_capabilities",
                                    result_ref=getattr(capability, "result_ref", None),
                                    status=getattr(capability, "status", None))
        except Exception as exc:
            deps.store.append_event(principal, "tool_error", tool_name="describe_capabilities",
                                    error_code=type(exc).__name__)
            final = _fallback("temporarily_unavailable", "当前工具目录暂时不可用，未执行任何业务查询；请稍后重试。")
            rendered = answer.render_answer(principal, final, deps.store)
            deps.store.finish(task_id, deps.worker_id, "failed", rendered,
                              structured_payload=_draft_payload(final))
            return final
        capability_payload = capability.payload if hasattr(capability, "payload") else capability
        history = deps.store.task_history(task_id, principal.user_id)
        user_text = deps.store.task_text(task_id, principal.user_id)
        messages = prompts.build_messages(history, capability_payload, user_text=user_text)
        schemas = _model_schemas(live_tools)
        for _ in range(budget.max_models):
            if deps.clock() - started >= budget.deadline_seconds:
                break
            budget.model_calls += 1
            deps.store.record_usage(task_id, model_calls=1)
            try:
                turn = await _model_call(deps.model, messages, schemas, min(15, budget.deadline_seconds - (deps.clock() - started)))
            except RetryableModelError as exc:
                # One bounded repair/retry is represented by the next model budget slot.
                messages.append({"role":"system","content":"工具或模型暂时不可用，请在现有证据基础上给出部分答案，并明确缺失。"})
                if budget.model_calls >= budget.max_models:
                    final = _fallback("temporarily_unavailable", "模型服务暂时不可用，已停止继续请求；请稍后重试。")
                continue
            except ModelError as exc:
                final = _fallback("temporarily_unavailable", str(exc))
                break
            if turn.tool_calls:
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
                    deps.store.record_usage(task_id, tool_calls=1, search_calls=1 if call.name == "search_public" else 0)
                    try:
                        envelope = await _await(deps.mcp.call_tool(call.name, call.arguments, grant))
                        deps.store.append_event(principal, "tool", tool_name=call.name, arguments=call.arguments,
                                                result_ref=getattr(envelope, "result_ref", None), status=getattr(envelope, "status", None))
                        messages.append({"role":"tool","tool_call_id":call.id,"content":prompts.project_tool_result(envelope)})
                    except Exception as exc:
                        deps.store.append_event(principal, "tool_error", tool_name=call.name, arguments=call.arguments, error_code=type(exc).__name__)
                        messages.append({"role":"tool","tool_call_id":call.id,"content":json.dumps({"status":"temporarily_unavailable","error":"工具暂时不可用"},ensure_ascii=False)})
                    messages = _bound_messages(messages)
                if final:
                    break
                continue
            try:
                final = answer.parse_answer(turn.content or "")
                rendered = answer.render_answer(principal, final, deps.store)
                deps.store.finish(task_id, deps.worker_id, _task_state(final), rendered,
                                  structured_payload=_draft_payload(final))
                return final
            except Exception as exc:
                # At most one repair prompt; it is still counted in model budget.
                if not any(item.get("content") == "最终答案格式或证据无效，请修复后只输出 AnswerDraft JSON。" for item in messages if item.get("role") == "system") and budget.model_calls < budget.max_models:
                    messages.append({"role":"system","content":"最终答案格式或证据无效，请修复后只输出 AnswerDraft JSON。"})
                    continue
                final = _fallback("partial", "已取得部分工具结果，但最终答案证据校验未通过；请重新提问。")
                break
        if final is None:
            final = _fallback("partial", "本次分析达到时间或调用预算上限，未能形成完整答案。")
        rendered = answer.render_answer(principal, final, deps.store)
        deps.store.finish(task_id, deps.worker_id, _task_state(final), rendered,
                          structured_payload=_draft_payload(final))
        return final
    finally:
        if grant:
            deps.store.revoke_task_grants(task_id)
