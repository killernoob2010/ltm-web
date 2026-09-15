"""Pydantic AI model factory for the controlled migration path."""
import asyncio
import inspect
import json
import re
import ssl
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from httpx2 import AsyncHTTPTransport, MockTransport, Timeout
from httpcore2 import AsyncConnectionPool
from openai import AsyncOpenAI
from pydantic_ai import Agent, RunContext, Tool, UsageLimits
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RunUsage

from . import capability_catalog, conversation_state, delivery_gate, planner, prompts, research_policy
from . import coverage as task_coverage
from . import tools as registered_tools
from . import pydantic_tools
from .answer_contracts import Limitation, ModelAnswer21, ValidatedAnswer21, VALIDATION_CHECKS
from .answer_v21 import build_fallback21
from .contracts import ToolEnvelope
from .runtime_budget import BudgetExceeded, RuntimeBudget


class _DeadlineChatModel(OpenAIChatModel):
    """Bound one non-streaming provider request, leaving the SDK loop intact."""

    async def request(self, messages, model_settings, model_request_parameters):
        async with asyncio.timeout(15):
            return await super().request(messages, model_settings, model_request_parameters)


def create_sdk_model(*, api_key: str, base_url: str, model_name: str, http_client):
    """Create an offline-constructible OpenAI-compatible Pydantic AI model."""
    if not isinstance(api_key, str) or not api_key:
        raise ValueError("api_key must be provided by protected configuration")
    if not isinstance(base_url, str) or not base_url.startswith("https://"):
        raise ValueError("base_url must use HTTPS")
    if not isinstance(model_name, str) or not model_name.strip():
        raise ValueError("model_name must be provided by protected configuration")
    if http_client is None:
        raise ValueError("http_client must be explicitly configured")
    if getattr(http_client, "trust_env", None) is not False:
        raise ValueError("http_client must disable environment proxies")
    if getattr(http_client, "follow_redirects", None) is not False:
        raise ValueError("http_client must disable redirects")
    # httpx2 2.12 has no public TLS accessor. Fail closed on unknown transports;
    # its exact MockTransport is permitted for the offline protocol tests.
    transports = [getattr(http_client, "_transport", None)]
    transports.extend(t for t in getattr(http_client, "_mounts", {}).values() if t is not None)
    for transport in transports:
        if type(transport) is MockTransport:
            continue
        if type(transport) is not AsyncHTTPTransport:
            raise ValueError("http_client transport certificate configuration is unknown")
        pool = getattr(transport, "_pool", None)
        context = getattr(pool, "_ssl_context", None)
        if (getattr(context, "verify_mode", None) != ssl.CERT_REQUIRED
                or getattr(context, "check_hostname", None) is not True):
            raise ValueError("http_client must enable certificate and hostname verification")
        if type(pool) is not AsyncConnectionPool or getattr(pool, "_proxy", None) is not None:
            raise ValueError("http_client must disable explicit proxies")

    openai_client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url,
        http_client=http_client,
        max_retries=0,
        timeout=Timeout(15.0),
    )
    provider = OpenAIProvider(openai_client=openai_client)
    return _DeadlineChatModel(
        model_name,
        provider=provider,
        settings={
            "thinking": False,
            "max_tokens": 4096,
            "extra_body": {"thinking": {"type": "disabled"}},
        },
    )


def _usage_requests(result: Any) -> int:
    usage = getattr(result, "usage", None)
    try:
        return max(1, int(getattr(usage, "requests", 1) or 1))
    except (TypeError, ValueError):
        return 1


def _record_agent_requests(budget: RuntimeBudget, result: Any, usage: RunUsage) -> None:
    observed = max(_usage_requests(result), int(getattr(usage, "requests", 0) or 0), 1)
    for _ in range(max(0, observed - 1)):
        try:
            budget.reserve("model")
        except BudgetExceeded:
            budget.record("model")


async def _run_agent(agent: Agent, prompt: str, *, deps, budget: RuntimeBudget,
                     agent_deps=None, message_history=None):
    """Run one SDK stage while charging its actual request count to the budget."""
    available = budget.remaining_models()
    if available <= 0:
        raise BudgetExceeded("budget_exhausted", "model budget exceeded")
    budget.reserve("model")
    stage_timeout = budget.remaining_seconds()
    if stage_timeout <= 0:
        raise BudgetExceeded("deadline_exceeded", "task deadline exceeded")
    usage = RunUsage()
    try:
        result = await asyncio.wait_for(
            agent.run(
                prompt,
                message_history=message_history,
                deps=agent_deps,
                usage_limits=UsageLimits(
                    request_limit=available,
                    tool_calls_limit=max(0, budget.max_tools - budget.tool_calls),
                ),
                usage=usage,
            ),
            timeout=stage_timeout,
        )
    except BaseException:
        _record_agent_requests(budget, None, usage)
        raise
    _record_agent_requests(budget, result, usage)
    return result


def _tool_items(raw: Any) -> list[dict[str, Any]]:
    items = raw.get("tools") if isinstance(raw, dict) else getattr(raw, "tools", None)
    if not isinstance(items, list):
        return []
    normalized = []
    for item in items:
        if isinstance(item, str):
            name, schema = item, {}
        elif isinstance(item, dict):
            name = item.get("name") or item.get("id")
            schema = item.get("inputSchema") or item.get("input_schema") or {}
        else:
            name = getattr(item, "name", None)
            schema = getattr(item, "inputSchema", None) or getattr(item, "input_schema", None) or {}
        if isinstance(name, str) and name in registered_tools.TOOL_SPECS:
            normalized.append({"name": name, "inputSchema": schema if isinstance(schema, dict) else {}})
    return normalized


def _catalog_from(raw_tools: Any, capability_envelope: ToolEnvelope) -> dict[str, Any]:
    payload = capability_envelope.payload if isinstance(capability_envelope.payload, dict) else {}
    business = payload.get("business_capabilities")
    if isinstance(business, dict) and isinstance(business.get("tools"), list):
        return business
    return capability_catalog.build_catalog({
        "tools": _tool_items(raw_tools),
        "conditional_sources": ["public_research"],
        "research": {},
    })


async def _record_event(deps, principal, kind: str, **kwargs) -> None:
    result = await asyncio.to_thread(deps.store.append_event, principal, kind, **kwargs)
    if result is False:
        raise RuntimeError("audit_write_failed")


async def _call_catalog_tool(deps, principal, grant: str, budget: RuntimeBudget) -> ToolEnvelope:
    budget.reserve("tool")
    timeout = min(float(deps.limits.tool_timeout_seconds), budget.remaining_seconds())
    if timeout <= 0:
        raise BudgetExceeded("deadline_exceeded", "task deadline exceeded")
    try:
        value = deps.mcp.call_tool("describe_capabilities", {}, grant)
        envelope = await asyncio.wait_for(
            value if inspect.isawaitable(value) else asyncio.to_thread(lambda: value),
            timeout=timeout,
        )
        if not isinstance(envelope, ToolEnvelope):
            envelope = ToolEnvelope.model_validate(envelope)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await _record_event(
            deps, principal, "tool", tool_name="describe_capabilities",
            status="temporarily_unavailable", error_code=type(exc).__name__.lower()[:64],
        )
        raise RuntimeError("capability_unavailable") from exc
    await _record_event(
        deps, principal, "tool", tool_name="describe_capabilities",
        result_ref=envelope.result_ref, status=envelope.status,
    )
    return envelope


def _tool_default(field_info):
    if field_info.is_required():
        return inspect.Parameter.empty
    factory = getattr(field_info, "default_factory", None)
    if factory is not None:
        try:
            return factory()
        except Exception:
            return inspect.Parameter.empty
    return field_info.default


def _build_sdk_tool(name: str, context: pydantic_tools.AgentRunContext) -> Tool:
    spec = registered_tools.TOOL_SPECS[name]
    model = spec["model"]
    parameters = [inspect.Parameter(
        "run_ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD,
        annotation=RunContext[pydantic_tools.AgentRunContext],
    )]
    annotations: dict[str, Any] = {"run_ctx": RunContext[pydantic_tools.AgentRunContext]}
    fields = list(model.model_fields.items())
    fields.sort(key=lambda item: not item[1].is_required())
    for field_name, field_info in fields:
        annotation = field_info.annotation or Any
        annotations[field_name] = annotation
        parameters.append(inspect.Parameter(
            field_name, inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=annotation, default=_tool_default(field_info),
        ))
    annotations["return"] = dict[str, Any]

    async def invoke(run_ctx, **kwargs):
        if run_ctx.deps.budget.cancelled:
            raise asyncio.CancelledError
        result = await pydantic_tools.invoke_registered_tool(name, kwargs, ctx=run_ctx.deps)
        if run_ctx.deps.budget.cancelled:
            raise asyncio.CancelledError
        return result

    invoke.__name__ = name
    invoke.__annotations__ = annotations
    invoke.__signature__ = inspect.Signature(parameters)
    return Tool(
        invoke,
        takes_ctx=True,
        name=name,
        description=str(spec["description"]),
        max_retries=0,
        timeout=context.tool_timeout_seconds,
        sequential=True,
    )


def _allowed_tool_names(principal: Any, plan: Any, *, research_allowed: bool) -> frozenset[str]:
    domains = {
        target.domain
        for requirement in (getattr(plan, "requirements", []) or [])
        for target in (getattr(requirement, "targets", []) or [])
    }
    names = {"describe_capabilities"}
    if "positions" in domains:
        names.update(registered_tools.TRADE_TOOL_NAMES)
    if "dataset" in domains:
        names.update(registered_tools.DATASET_TOOL_NAMES)
    if "public" in domains:
        names.update({"search_public", "read_public"})
    names.update({"explain_evidence"} if domains & {"positions", "dataset"} else set())
    authorized = set(registered_tools.tool_names_for_principal(
        principal, research_allowed=research_allowed,
    ))
    return frozenset(names & authorized)


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, BudgetExceeded):
        return exc.code
    if isinstance(exc, asyncio.TimeoutError):
        return "model_timeout"
    name = type(exc).__name__.lower()
    if "planner" in name or "validation" in name:
        return "plan_invalid"
    if "cancel" in name:
        return "cancelled"
    return {
        "runtimeerror": "runtime_unavailable",
        "unexpectedmodelbehavior": "model_output_invalid",
    }.get(name, "runtime_unavailable")


def _failure_answer(principal: Any, deps, refs: list[str], code: str, preference: str) -> ValidatedAnswer21:
    try:
        result = build_fallback21(
            principal, refs, {}, code, deps.store,
            presentation_preference=preference,
        )
    except Exception:
        message = "本次回答未通过格式或证据校验，系统未交付业务结论。"
        result = ValidatedAnswer21(
            delivery_status="failed", body_markdown=message, plain_text=message,
            limitations=[Limitation(code=code, message="本次处理未完成，未核验内容未交付。")],
            presentation_mode=preference,
        )
    return result.model_copy(update={"validation_summary": None})


_REPAIR_SAFE_CODES = frozenset({
    "answer_reference_missing", "result_missing", "result_ref_missing",
    "full_text_required", "source_partial", "scope_mismatch", "metric_missing",
    "internal_source_required", "public_source_required", "answer_delivery_partial",
    "coverage_missing", "requirement_incomplete",
    "delivery_quality_check_failed", "ack_only_answer", "presentation_mismatch",
    "request_coverage_incomplete", "requirement_coverage_incomplete",
    "uncovered_claim", "unreferenced_number", "missing_reference", "invalid_reference",
    "dependent_content_unavailable", "view_data_unavailable", "query_incomplete",
    "public_source_unavailable", "reference_unavailable",
})


def _safe_repair_code(value: Any) -> str | None:
    code = str(value or "").strip()
    if code in VALIDATION_CHECKS or code in _REPAIR_SAFE_CODES:
        return code
    for suffix in (
        "answer_reference_missing", "result_missing", "result_ref_missing", "scope_mismatch",
    ):
        if code.endswith("." + suffix):
            return suffix
    if code.startswith("metric."):
        return "metric_missing"
    return None


def _execution_system_prompt() -> str:
    return (
        "你是受控业务回答器。只能根据已批准计划和工具返回的证据回答；"
        "不得编造数字、来源、权限或身份信息。必须输出 ModelAnswer21；"
        "事实片段要绑定精确 result_ref 引用，不能把工具原始数据复制进答案。\n"
        + prompts.EVIDENCE_REFERENCE_GUIDANCE
    )


def _repair_system_prompt() -> str:
    return (
        "你是受控答案修正器。只能修正已有答案的格式、证据绑定和完整性；"
        "不得调用工具、请求新数据或编造事实。必须输出 ModelAnswer21。\n"
        + prompts.EVIDENCE_REFERENCE_GUIDANCE
    )


def _repair_feedback(plan: Any, validated: ValidatedAnswer21, envelopes: list[ToolEnvelope]) -> dict[str, Any]:
    report = task_coverage.assess_evidence(plan, envelopes)
    report = task_coverage.assess_delivery(plan, report, validated)
    summary = getattr(validated, "validation_summary", None)
    checks = getattr(summary, "checks", {}) if summary is not None else {}
    failed_checks = [name for name in VALIDATION_CHECKS if checks.get(name) == "failed"]
    unresolved: list[str] = []
    if summary is not None:
        unresolved.extend(
            code for code in (_safe_repair_code(item) for item in summary.unresolved_codes) if code
        )
    span_issues: list[dict[str, Any]] = []
    for limitation in (getattr(validated, "limitations", []) or []):
        code = _safe_repair_code(getattr(limitation, "code", None))
        if not code:
            continue
        unresolved.append(code)
        span_ids = [
            str(span_id)[:8]
            for span_id in (getattr(limitation, "affected_span_ids", []) or [])
            if re.fullmatch(r"s(?:[1-9]|[1-7][0-9]|80)", str(span_id))
        ]
        if span_ids:
            span_issues.append({"code": code, "span_ids": list(dict.fromkeys(span_ids))[:8]})
    requirements = []
    for item in report.items:
        missing = list(dict.fromkeys(
            code for code in (_safe_repair_code(value) for value in item.missing_codes) if code
        ))[:16]
        blocks = list(dict.fromkeys(
            code for code in (_safe_repair_code(value) for value in item.delivery_blocks) if code
        ))[:16]
        unresolved.extend([*missing, *blocks])
        if item.status != "answered" or missing or blocks:
            requirement = {
                "requirement_id": str(item.requirement_id)[:40],
                "status": item.status,
            }
            if missing:
                requirement["missing_codes"] = missing
            if blocks:
                requirement["delivery_blocks"] = blocks
            if not missing and not blocks:
                requirement["delivery_blocks"] = ["requirement_incomplete"]
            requirements.append(requirement)
    return {
        "failed_checks": failed_checks[:7],
        "unresolved_codes": list(dict.fromkeys(unresolved))[:40],
        "span_issues": span_issues[:16],
        "requirements": requirements[:8],
        "presentation": str(getattr(plan, "presentation", "auto"))[:20],
        "prohibited_presentations": [
            str(item)[:20]
            for item in (getattr(plan, "prohibited_presentations", []) or [])[:2]
        ],
    }


def _repair_prompt(plan: Any, validated: ValidatedAnswer21, envelopes: list[ToolEnvelope]) -> str:
    feedback = _repair_feedback(plan, validated, envelopes)
    return (
        "请只根据以下服务端交付诊断修正已有答案，只返回完整 ModelAnswer21。"
        "不得调用工具、请求新数据、重新规划或扩大范围；保留已有可用证据引用。\n"
        + json.dumps(feedback, ensure_ascii=False, separators=(",", ":"))
    )


def _answer_quality(answer: ValidatedAnswer21) -> int:
    return {"complete": 2, "partial": 1, "failed": 0}.get(answer.delivery_status, 0)


def _position_fallback_intents(plan: Any) -> dict[str, str]:
    intents: dict[str, str] = {}
    for requirement in getattr(plan, "requirements", []) or []:
        if getattr(requirement, "source_intent", None) != "internal":
            continue
        for target in getattr(requirement, "targets", []) or []:
            if getattr(target, "domain", None) != "positions":
                continue
            filters = getattr(target, "filters", {})
            if not isinstance(filters, dict):
                continue
            option_type = str(filters.get("option_type") or "").lower()
            intent = str(getattr(target, "net_intent", "none") or "none")
            if option_type in {"call", "put"} and intent in {"net_buy", "net_sell"}:
                intents[option_type] = intent
    return intents


def _recover_unreferenced_position_answer(
    plan: Any,
    envelopes: list[ToolEnvelope],
    validated: ValidatedAnswer21,
    *,
    principal: Any,
    store_api: Any,
    presentation_preference: str,
    prohibited_presentations,
) -> ValidatedAnswer21:
    """Recover verified text when a valid model envelope omitted all evidence refs."""
    if validated.evidence:
        return validated
    position_intents = _position_fallback_intents(plan)
    if not position_intents:
        return validated
    result_refs = [
        str(envelope.result_ref)
        for envelope in envelopes
        if getattr(envelope, "result_ref", None)
        and isinstance(getattr(envelope, "payload", None), dict)
        and envelope.payload.get("kind") == "positions"
        and not envelope.payload.get("aggregation")
    ]
    if not result_refs:
        return validated
    recovered = build_fallback21(
        principal,
        list(dict.fromkeys(result_refs)),
        {},
        "answer_reference_missing",
        store_api,
        presentation_preference=presentation_preference,
        prohibited_presentations=prohibited_presentations,
        position_intents=position_intents,
    )
    if not recovered.evidence:
        return validated
    recovered = delivery_gate.validate_delivery(
        plan,
        recovered,
        envelopes,
        principal=principal,
        store_api=store_api,
        presentation_preference=presentation_preference,
        prohibited_presentations=prohibited_presentations,
    )
    return recovered if _answer_quality(recovered) >= _answer_quality(validated) else validated


def _ensure_task_active(budget: RuntimeBudget) -> None:
    if budget.cancelled:
        raise BudgetExceeded("cancelled", "task is cancelled")


async def run_task(task_id: int, deps) -> ValidatedAnswer21 | None:
    """Execute one task through SDK planning, typed tools and final delivery gate."""
    principal = await asyncio.to_thread(deps.store.principal_for_task, task_id)
    budget = RuntimeBudget(
        deps.limits, clock=deps.clock, started_at=deps.clock(),
    )
    grant = None
    plan = None
    policy = None
    context = None
    envelopes: list[ToolEnvelope] = []
    preference = "auto"
    finished_attempted = False
    planning_status = "not_run"
    planning_error = None

    async def finish_once(result: ValidatedAnswer21, *, error: str | None = None,
                          coverage_report=None, conversation=None):
        nonlocal finished_attempted
        if finished_attempted:
            return False
        finished_attempted = True
        try:
            await asyncio.to_thread(
                deps.store.record_usage,
                task_id,
                model_calls=budget.model_calls,
                tool_calls=budget.tool_calls,
                search_calls=budget.search_calls,
            )
        except Exception:
            pass
        payload = result.model_dump(mode="json")
        agent_context: dict[str, Any] = {
            "runtime_backend": "pydantic",
            "planning": {
                "enabled": True,
                "status": planning_status,
                "policy_mode": getattr(policy, "mode", None),
                "policy_reason": getattr(policy, "reason", None),
                "catalog_version": "capability-catalog-v1",
            },
        }
        if planning_error:
            agent_context["planning"]["fallback_from"] = planning_error
        if plan is not None:
            agent_context["task_plan"] = plan.model_dump(mode="json")
        if coverage_report is not None:
            agent_context["coverage"] = coverage_report.model_dump(mode="json")
        if conversation is not None:
            agent_context["conversation_state"] = conversation.model_dump(mode="json")
        payload["agent_context"] = agent_context
        state = {
            "complete": "succeeded", "partial": "partial", "failed": "failed",
        }.get(result.delivery_status, "failed")
        persisted = await asyncio.to_thread(
            deps.store.finish, task_id, deps.worker_id, state, result.plain_text,
            error, payload,
        )
        return bool(persisted)

    try:
        if deps.sdk_model is None:
            raise RuntimeError("sdk_model_missing")
        grant = await asyncio.to_thread(deps.store.issue_grant, principal)
        budget.reserve("tool")
        raw_tools = await asyncio.wait_for(
            deps.mcp.list_tools(grant),
            timeout=min(float(deps.limits.tool_timeout_seconds), budget.remaining_seconds()),
        )
        await _record_event(deps, principal, "model_tool_catalog", status="complete")
        capability_envelope = await _call_catalog_tool(deps, principal, grant, budget)
        catalog = _catalog_from(raw_tools, capability_envelope)
        user_text = await asyncio.to_thread(deps.store.task_text, task_id, principal.user_id)
        history = await asyncio.to_thread(deps.store.task_history, task_id, principal.user_id)
        previous_state = conversation_state.extract_conversation_state(history)
        business_date = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        plan_prompt = json.dumps({
            "question": str(user_text or "")[:2000],
            "business_date": business_date,
            "conversation_state": previous_state,
            "history": history[-8:],
            "capability_catalog": catalog,
        }, ensure_ascii=False, separators=(",", ":"))
        planner_agent = Agent(
            deps.sdk_model,
            output_type=planner.TaskPlan,
            system_prompt=planner.PLANNER_SYSTEM,
            retries=0,
        )
        fallback_from = None
        try:
            plan_result = await _run_agent(
                planner_agent, plan_prompt, deps=deps, budget=budget,
            )
            _ensure_task_active(budget)
            plan = planner.apply_time_windows(plan_result.output, user_text)
            planner.validate_plan(plan, catalog)
        except Exception as exc:
            # A provider can return HTTP 200 while still failing the strict
            # TaskPlan output contract.  Keep that failure fail-closed unless
            # this is the narrow, deterministic current-position vocabulary.
            if isinstance(exc, BudgetExceeded) or budget.cancelled or budget.remaining_seconds() <= 0:
                raise
            fallback_plan = planner.build_internal_position_fallback_plan(
                user_text,
                conversation_state=previous_state,
            )
            fallback_plan = planner.apply_time_windows(
                fallback_plan, user_text,
            )
            planner.validate_plan(fallback_plan, catalog)
            plan = fallback_plan
            fallback_from = _failure_code(exc)
        preference = plan.presentation
        candidate_policy = research_policy.policy_from_task_plan(
            plan, configured=research_policy.public_tools_configured(), user_text=user_text,
        )
        policy = research_policy.enforce_research_policy(user_text, candidate_policy)
        if fallback_from and policy.mode != "internal_only":
            raise planner.PlannerError("持仓兜底计划不能扩大公开研究范围")
        if policy.mode == "clarification_required":
            plan = plan.model_copy(update={"clarification": policy.clarification})
        await asyncio.to_thread(deps.store.record_plan_authorization, principal, plan, policy)
        planning_status = "fallback_approved" if fallback_from else "approved"
        planning_error = fallback_from
        allowed_names = _allowed_tool_names(
            principal, plan,
            research_allowed=research_policy.public_tools_allowed(
                policy, research_policy.public_tools_configured(),
            ),
        )
        context = pydantic_tools.AgentRunContext(
            task_id=task_id,
            principal=principal,
            plan=plan,
            grant=grant,
            mcp=deps.mcp,
            store=deps.store,
            budget=budget,
            sensitive_values=(),
            envelopes=envelopes,
            allowed_tool_names=allowed_names,
            research_allowed=policy.mode == "research_allowed",
            tool_timeout_seconds=float(deps.limits.tool_timeout_seconds),
        )
        sdk_tools = [_build_sdk_tool(name, context) for name in sorted(allowed_names)]
        execution_agent = Agent(
            deps.sdk_model,
            output_type=ModelAnswer21,
            system_prompt=_execution_system_prompt(),
            deps_type=pydantic_tools.AgentRunContext,
            tools=sdk_tools,
            retries=0,
            tool_timeout=float(deps.limits.tool_timeout_seconds),
        )
        execution_prompt = json.dumps({
            "question": str(user_text or "")[:2000],
            "task_plan": plan.model_dump(mode="json"),
            "conversation_state": previous_state,
        }, ensure_ascii=False, separators=(",", ":"))
        answer_result = await _run_agent(
            execution_agent, execution_prompt, deps=deps, budget=budget,
            agent_deps=context,
        )
        _ensure_task_active(budget)
        answer_draft = ModelAnswer21.model_validate(answer_result.output)
        validated = delivery_gate.validate_delivery(
            plan, answer_draft, envelopes,
            principal=principal, store_api=deps.store,
            presentation_preference=preference,
            prohibited_presentations=plan.prohibited_presentations,
        )
        if validated.delivery_status != "complete" and budget.remaining_models() > 0:
            repair_prompt = _repair_prompt(plan, validated, envelopes)
            try:
                repair_agent = Agent(
                    deps.sdk_model,
                    output_type=ModelAnswer21,
                    system_prompt=_repair_system_prompt(),
                    retries=0,
                )
                repaired_result = await _run_agent(
                    repair_agent, repair_prompt, deps=deps, budget=budget,
                    message_history=answer_result.all_messages(),
                )
                _ensure_task_active(budget)
                repaired = delivery_gate.validate_delivery(
                    plan, ModelAnswer21.model_validate(repaired_result.output), envelopes,
                    principal=principal, store_api=deps.store,
                    presentation_preference=preference,
                    prohibited_presentations=plan.prohibited_presentations,
                )
                if _answer_quality(repaired) > _answer_quality(validated):
                    validated = repaired
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception:
                pass
        validated = _recover_unreferenced_position_answer(
            plan,
            envelopes,
            validated,
            principal=principal,
            store_api=deps.store,
            presentation_preference=preference,
            prohibited_presentations=plan.prohibited_presentations,
        )
        requirement_report = task_coverage.assess_evidence(plan, envelopes)
        requirement_report = task_coverage.assess_delivery(plan, requirement_report, validated)
        refs = [
            str(item.result_ref)
            for item in (validated.evidence or [])
            if getattr(item, "result_ref", None)
        ]
        conversation = conversation_state.update_conversation_state(
            previous_state, plan, requirement_report, refs, source_task_id=task_id,
        )
        persisted = await finish_once(
            validated, coverage_report=requirement_report, conversation=conversation,
        )
        return validated if persisted else None
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        failure = _failure_code(exc)
        refs = [str(item.result_ref) for item in envelopes if item.result_ref]
        result = _failure_answer(principal, deps, refs, failure, preference)
        persisted = await finish_once(result, error=failure)
        return result if persisted else None
    finally:
        try:
            await asyncio.shield(asyncio.to_thread(deps.store.revoke_task_grants, task_id))
        except BaseException:
            pass


__all__ = ["create_sdk_model", "run_task"]
