"""Bounded task planning before the model is allowed to call business tools."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
import json
import re
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter, ValidationError

from .contracts import FactQuery, PositionFilter
from .dv_contracts import DataFilters, DatasetQuery, validate_dataset_query
from .planning_contracts import AnalysisTarget, AnalysisSpec, Requirement, TaskPlan, TimeWindow
from .semantic_catalog import get_dataset_spec


class PlannerError(ValueError):
    """A planning response cannot be safely used for execution."""


class PlannerAuditError(PlannerError):
    """A validated plan could not be durably recorded as an authorization event."""


_POSITION_FILTER_FIELDS = {
    "asset_type", "contracts", "direction", "classification", "valuation_mode",
    "required_metrics", "as_of_mode", "as_of_date", "presentation",
    "products", "contract_months", "option_type", "strike_min", "strike_max",
    "strike_min_inclusive", "strike_max_inclusive",
}
_DATASET_FILTER_FIELDS = {
    "dataset", "mode", "start_date", "end_date", "fields", "metric", "years",
    "business_weeks", "products", "categories", "source_countries", "ports",
    "regions", "mainstream_status", "product_pool", "scope_type", "slice_type",
    "arrival_kind", "grades", "summary_metrics", "data_states", "filters",
}
_PUBLIC_FILTER_FIELDS = {
    "topic", "date", "start_date", "end_date", "region", "regions", "port", "ports",
    "product", "products", "company", "companies", "source", "freshness", "entities",
}
_PRIVATE_PLAN_KEYS = {
    "user_id", "account_id", "account_ids", "customer_id", "order_id", "order_no",
    "execution_id", "token", "grant", "permission", "permissions", "role", "sql",
    "code", "command", "budget", "max_tools", "max_models", "api_key",
}

_POSITION_METRICS = {
    "count", "quantity", "floating_pnl", "gross_quantity", "gross_buy_quantity",
    "gross_sell_quantity", "net_quantity", "net_sell_quantity", "net_tons",
    "net_signed_tons", "net_wan_tons", "strike_min", "strike_max",
    "covered_rows", "eligible_rows",
}
_DATASET_QUERY_FIELDS = {"dataset", "mode", "start_date", "end_date", "fields", "cursor", "batch_size"}


PLANNER_SYSTEM = """你是受控业务 Agent 的任务规划器。
把用户问题拆成 1 到 8 个可验证的业务需求，每个需求列出独立分析对象、所需来源、指标、范围和时间要求。
同一个问题可以同时需要内部数据和公开资料。不要因为出现发运、库存、港口或期权就关闭公开资料；先判断完成问题需要哪些证据。
遇到“净买 Call 与净卖 Put”或类似并列对象时，必须为 Call、Put 建立独立分析目标，保留双边实际持仓；只请求用户明确要求的指标，需要净买/净卖时不要先按单边方向过滤原始持仓。只有用户明确询问浮盈浮亏时才请求浮盈指标；只有用户明确要求比较或排名时才建立比较/排名需求。
用户明确说不要联网时，生成 no_web 限制；不要自行取消。公开需求只写公开地区、品种、日期和主题，不写账户、持仓数量、盈亏、客户、订单或内部编码。
模型不能填写 user_id、account_id、权限、执行令牌、SQL、代码、预算或工具调用。只输出符合给定 JSON Schema 的 JSON，不要输出解释、Markdown 围栏或工具调用。
"""

_RECENT_TIME = re.compile(r"近期|最近|近两周|近一个月|本期|截至目前|最新|目前|当前|今天|今日|昨日|latest|current", re.I)
_SNAPSHOT_TIME = re.compile(r"最新|当前|目前|有效持仓|持仓快照|latest|current", re.I)
_QUANTITY_ONLY = re.compile(r"只(?:要|说|看|询问|输出)(?:数量|手数)|仅(?:看|要|说|输出)(?:数量|手数)", re.I)
_COMPARISON_REQUEST = re.compile(r"比较|对比|是否一致|差异|变化|环比|同比|排名|排序|前[一二三123]|前三|最大|最小|降幅|增幅", re.I)
_ONLY_CALL = re.compile(r"(?:只|仅)(?:看|要|说|查)?\s*(?:call|看涨|认购)", re.I)
_ONLY_PUT = re.compile(r"(?:只|仅)(?:看|要|说|查)?\s*(?:put|看跌|认沽)", re.I)
_BUSINESS_TZ = ZoneInfo("Asia/Shanghai")


def _is_position_snapshot_requirement(requirement: Requirement, user_text: str) -> bool:
    """Return whether a positions requirement asks for the current snapshot."""
    targets = list(requirement.targets or [])
    if not targets or any(target.domain != "positions" for target in targets):
        return False
    return bool(
        _SNAPSHOT_TIME.search(str(requirement.time_requirement or ""))
        or _SNAPSHOT_TIME.search(str(user_text or ""))
    )


def _requested_option_type(user_text: str) -> str | None:
    text = str(user_text or "")
    has_call = bool(_ONLY_CALL.search(text))
    has_put = bool(_ONLY_PUT.search(text))
    if has_call == has_put:
        return None
    return "call" if has_call else "put"


def _normalize_user_constraints(task_plan: TaskPlan, user_text: str) -> TaskPlan:
    """Remove model-only expansions that contradict the user's request."""
    text = str(user_text or "")
    quantity_only = bool(_QUANTITY_ONLY.search(text))
    comparison_requested = bool(_COMPARISON_REQUEST.search(text))
    requested_option = _requested_option_type(text)
    pnl_requested = bool(re.search(r"浮盈|浮亏|盈亏|pnl", text, re.I))
    normalized: list[Requirement] = []

    for requirement in task_plan.requirements:
        analysis = requirement.analysis
        if not comparison_requested and analysis is not None and analysis.operation in {"compare", "rank"}:
            continue
        targets = []
        for target in requirement.targets:
            metrics = list(target.metrics)
            if quantity_only:
                if not pnl_requested:
                    metrics = [metric for metric in metrics if metric != "floating_pnl"]
                if not re.search(r"总手|gross", text, re.I):
                    metrics = [
                        metric for metric in metrics
                        if metric not in {"gross_quantity", "gross_buy_quantity", "gross_sell_quantity"}
                    ]
            filters = dict(target.filters or {})
            if quantity_only and target.domain == "positions":
                if not pnl_requested:
                    filter_metrics = filters.get("required_metrics")
                    if isinstance(filter_metrics, list):
                        filters["required_metrics"] = [
                            metric for metric in filter_metrics if metric != "floating_pnl"
                        ]
                    if filters.get("valuation_mode") == "mark_to_market":
                        filters["valuation_mode"] = "quantity_only"
                if not re.search(r"总手|gross", text, re.I):
                    filter_metrics = filters.get("required_metrics")
                    if isinstance(filter_metrics, list):
                        filters["required_metrics"] = [
                            metric for metric in filter_metrics
                            if metric not in {"gross_quantity", "gross_buy_quantity", "gross_sell_quantity"}
                        ]
            if requested_option and target.domain == "positions":
                target_option = str(filters.get("option_type") or "all").lower()
                if target_option not in {"all", requested_option}:
                    continue
                filters["option_type"] = requested_option
            if _is_position_snapshot_requirement(requirement, text) and not re.search(r"20\d{2}-\d{2}-\d{2}", text):
                filters["as_of_mode"] = "latest"
                filters["as_of_date"] = None
            if quantity_only and not metrics and target.domain == "positions":
                metrics = [
                    "net_quantity"
                    if target.net_intent in {"net_buy", "net_sell", "net"}
                    else "quantity"
                ]
            targets.append(target.model_copy(update={"metrics": metrics, "filters": filters}))
        if not targets:
            continue
        normalized_analysis = analysis
        if quantity_only and not pnl_requested and analysis is not None and analysis.metric == "floating_pnl":
            normalized_analysis = None
        normalized.append(requirement.model_copy(update={
            "targets": targets,
            "analysis": normalized_analysis,
        }))

    if not normalized:
        # The contract requires at least one requirement. Keep the first
        # bounded target if a model produced only unrequested comparisons.
        normalized = [task_plan.requirements[0].model_copy(update={"analysis": None})]
    known_ids = {str(item.id) for item in normalized}
    normalized = [item.model_copy(update={
        "depends_on": [dep for dep in item.depends_on if str(dep) in known_ids],
    }) for item in normalized]
    target_ids = {str(target.id) for item in normalized for target in item.targets}
    origins = [item for item in task_plan.condition_origins if str(item.target_id) in target_ids]
    return task_plan.model_copy(update={
        "requirements": normalized,
        "condition_origins": origins,
    })


def apply_time_windows(task_plan: TaskPlan, user_text: str, *, now: datetime | None = None) -> TaskPlan:
    """Fill server-owned windows and normalize model-owned scope claims."""
    text = str(user_text or "")
    explicit_dates: list[date] = []
    for value in re.findall(r"20\d{2}-\d{2}-\d{2}", text):
        try:
            explicit_dates.append(date.fromisoformat(value))
        except ValueError:
            continue
    business_now = now or datetime.now(_BUSINESS_TZ)
    if business_now.tzinfo is None:
        business_now = business_now.replace(tzinfo=_BUSINESS_TZ)
    business_date = business_now.astimezone(_BUSINESS_TZ).date()
    default_start = business_date - timedelta(days=13)
    normalized_plan = _normalize_user_constraints(task_plan, text)
    requirements = []
    for requirement in normalized_plan.requirements:
        position_snapshot = _is_position_snapshot_requirement(requirement, text) and not explicit_dates
        if position_snapshot:
            requirements.append(requirement.model_copy(update={"time_window": None}))
            continue
        if explicit_dates and len(explicit_dates) == 1 and all(
            target.domain == "positions" for target in requirement.targets
        ):
            as_of_date = explicit_dates[0].isoformat()
            targets = [target.model_copy(update={
                "filters": {
                    **dict(target.filters or {}),
                    "as_of_mode": "settlement_date",
                    "as_of_date": as_of_date,
                },
            }) for target in requirement.targets]
            requirements.append(requirement.model_copy(update={
                "targets": targets,
                "time_window": None,
            }))
            continue
        if explicit_dates:
            if len(explicit_dates) >= 2 and explicit_dates[0] <= explicit_dates[1]:
                start_date, end_date = explicit_dates[0], explicit_dates[1]
            else:
                start_date = end_date = explicit_dates[0]
            requirements.append(requirement.model_copy(update={
                "time_window": TimeWindow(
                    start_date=start_date,
                    end_date=end_date,
                    origin="user",
                ),
            }))
            continue
        if _RECENT_TIME.search(requirement.time_requirement or ""):
            requirements.append(requirement.model_copy(update={
                "time_window": TimeWindow(
                    start_date=default_start,
                    end_date=business_date,
                    origin="default",
                ),
            }))
            continue
        requirements.append(requirement)
    return normalized_plan.model_copy(update={"requirements": requirements})


def build_policy_fallback_plan(
    user_text: str,
    candidate,
    *,
    conversation_state: dict[str, Any] | None = None,
) -> TaskPlan:
    """Build a minimal server-owned plan when the model planner is unavailable.

    This is intentionally narrower than model planning: it can preserve only a
    deterministic public intent already approved by ``enforce_research_policy``
    and, for a mixed request, one generic internal context target.  It never
    copies the user's text into public filters or creates identity/permission
    fields.  The caller must still run ``apply_time_windows`` and
    ``validate_plan`` before opening the public tool grant.
    """
    if getattr(candidate, "mode", None) != "research_allowed":
        raise PlannerError("确定性策略没有批准公开研究，不能生成兜底计划")
    state = conversation_state if isinstance(conversation_state, dict) else {}
    restrictions = state.get("restrictions") or []
    if any(isinstance(item, dict) and item.get("kind") == "no_web" for item in restrictions):
        raise PlannerError("历史会话仍禁止联网，不能生成公开兜底计划")

    text = str(user_text or "")
    recent_request = bool(_RECENT_TIME.search(text))
    explicit_dates = re.findall(r"20\d{2}-\d{2}-\d{2}", text)
    time_requirement = "近期" if recent_request else "用户指定范围" if explicit_dates else "当前公开事实"
    requirements: list[Requirement] = []

    if getattr(candidate, "reason", None) == "mixed_research":
        internal_domain = "positions" if re.search(r"持仓|期权|期货|合约|盈亏|手数", text, re.I) else "dataset"
        internal_target = AnalysisTarget(
            id="internal_context_target",
            domain=internal_domain,
            filters={},
            metrics=[],
            group_by=[],
            label="内部业务数据",
        )
        requirements.append(Requirement(
            id="internal_context",
            question="完成用户明确要求的内部业务数据部分",
            targets=[internal_target],
            source_intent="internal",
            time_requirement=time_requirement,
        ))

    requirements.append(Requirement(
        id="public_research",
        question="查询与用户问题相关的公开资料并读取可引用正文",
        targets=[AnalysisTarget(
            id="public_research_target",
            domain="public",
            filters={},
            metrics=[],
            group_by=[],
            label="公开资料",
        )],
        source_intent="public",
        needs_full_text=True,
        time_requirement=time_requirement,
    ))
    topic_action = state.get("topic_action")
    if topic_action not in {"continue", "refine", "presentation_only", "refresh", "new_topic"}:
        topic_action = "new_topic"
    return TaskPlan(
        objective="按已识别的内部与公开证据完成本次请求",
        topic_action=topic_action,
        requirements=requirements,
        restrictions=[],
        presentation="auto",
        prohibited_presentations=[],
        clarification=None,
    )


def _json_schema() -> dict[str, Any]:
    return TaskPlan.model_json_schema()


def build_plan_messages(user_text: str, conversation_state: dict[str, Any], catalog: dict[str, Any]) -> list[dict[str, str]]:
    compact_catalog = {
        "tools": catalog.get("tools", []) if isinstance(catalog, dict) else [],
        "conditional_sources": catalog.get("conditional_sources", []) if isinstance(catalog, dict) else [],
        "research": catalog.get("research", {}) if isinstance(catalog, dict) else {},
    }
    return [
        {"role": "system", "content": PLANNER_SYSTEM + "\nJSON Schema:\n" + json.dumps(_json_schema(), ensure_ascii=False, separators=(",", ":"))},
        {"role": "user", "content": json.dumps({
            "question": str(user_text or "")[:2000],
            "conversation_state": conversation_state or {},
            "capability_catalog": compact_catalog,
        }, ensure_ascii=False, separators=(",", ":"))},
    ]


def _parse_object(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PlannerError("规划响应不是合法 JSON") from exc
    if not isinstance(value, dict):
        raise PlannerError("规划响应必须是 JSON 对象")
    return value


def _consume_model_budget(budget) -> None:
    model_calls = getattr(budget, "model_calls", 0)
    max_models = getattr(budget, "max_models", 0)
    if max_models - model_calls <= 1:
        raise PlannerError("模型预算不足，需保留一次答案整理调用")
    budget.model_calls += 1


async def _call_model(model, messages, timeout, budget):
    _consume_model_budget(budget)
    try:
        return await asyncio.to_thread(model.next_turn, messages, [], timeout)
    except Exception as exc:
        raise PlannerError("任务规划模型暂时不可用") from exc


async def create_plan(
    user_text: str,
    conversation_state: dict[str, Any] | None,
    catalog: dict[str, Any],
    model,
    budget,
    *,
    timeout: float = 15.0,
) -> TaskPlan:
    """Create one bounded plan; repair malformed JSON at most once."""
    messages = build_plan_messages(user_text, conversation_state or {}, catalog)
    turn = await _call_model(model, messages, timeout, budget)
    if getattr(turn, "tool_calls", None):
        raise PlannerError("规划阶段不能调用工具")
    try:
        return TaskPlan.model_validate(_parse_object(getattr(turn, "content", "")))
    except (PlannerError, ValueError) as first_error:
        if getattr(budget, "model_calls", 0) >= getattr(budget, "max_models", 0):
            raise PlannerError("模型预算不足，无法修复任务规划") from first_error
        repair_messages = messages + [{
            "role": "assistant", "content": str(getattr(turn, "content", ""))[:6000],
        }, {
            "role": "user",
            "content": "规划 JSON 无法校验。请只返回修正后的完整 JSON；不要调用工具，不要增加权限、预算或身份字段。",
        }]
        repaired = await _call_model(model, repair_messages, timeout, budget)
        if getattr(repaired, "tool_calls", None):
            raise PlannerError("规划修复阶段不能调用工具")
        try:
            return TaskPlan.model_validate(_parse_object(getattr(repaired, "content", "")))
        except (PlannerError, ValueError) as exc:
            raise PlannerError("任务规划两次均未通过结构校验") from exc


def _validate_position_target(filters: dict[str, Any], metrics: list[str]) -> None:
    nested_filters = filters.get("filters")
    if nested_filters is not None and not isinstance(nested_filters, dict):
        raise PlannerError("持仓目标的 filters 必须是对象")
    nested = {
        key: value for key, value in filters.items()
        if key in PositionFilter.model_fields
    }
    if isinstance(nested_filters, dict):
        nested.update(nested_filters)
    payload: dict[str, Any] = {"filters": nested}
    for field in (
        "asset_type", "contracts", "direction", "classification", "valuation_mode",
        "required_metrics", "presentation",
    ):
        if field in filters:
            payload[field] = filters[field]
    if "as_of_mode" in filters or "as_of_date" in filters:
        payload["as_of"] = {
            "mode": filters.get("as_of_mode", "latest"),
            "date": filters.get("as_of_date"),
        }
    try:
        FactQuery.model_validate(payload)
        unknown_metrics = set(metrics) - _POSITION_METRICS
        if unknown_metrics:
            raise ValueError(f"unknown metric: {sorted(unknown_metrics)[0]}")
    except (TypeError, ValueError, ValidationError) as exc:
        raise PlannerError(f"持仓目标的筛选类型或指标不合法: {exc}") from exc


def _validate_dataset_target(filters: dict[str, Any]) -> None:
    nested_filters = filters.get("filters")
    if nested_filters is not None and not isinstance(nested_filters, dict):
        raise PlannerError("数据目标的 filters 必须是对象")
    filter_payload = {
        key: value for key, value in filters.items()
        if key in DataFilters.model_fields
    }
    if isinstance(nested_filters, dict):
        filter_payload.update(nested_filters)
    try:
        DataFilters.model_validate(filter_payload)
        if "dataset" in filters:
            query_payload = {
                key: value for key, value in filters.items()
                if key in _DATASET_QUERY_FIELDS and key != "filters"
            }
            query_payload["filters"] = filter_payload
            validate_dataset_query(DatasetQuery.model_validate(query_payload))
        for field in _DATASET_QUERY_FIELDS - {"dataset", "mode", "start_date", "end_date"}:
            if field in filters:
                DatasetQuery.model_validate({"dataset": "spot_series", field: filters[field]})
        if "mode" in filters and filters["mode"] not in {"latest", "range", "seasonal"}:
            raise ValueError("invalid mode")
        date_adapter = TypeAdapter(date)
        for field in ("start_date", "end_date"):
            if field in filters and filters[field] is not None:
                date_adapter.validate_python(filters[field])
    except (TypeError, ValueError, ValidationError) as exc:
        raise PlannerError(f"数据目标的筛选类型或字段不合法: {exc}") from exc


def _dataset_name(filters: dict[str, Any]) -> str | None:
    value = filters.get("dataset")
    if isinstance(value, str):
        return value
    nested = filters.get("filters")
    if isinstance(nested, dict) and isinstance(nested.get("dataset"), str):
        return nested["dataset"]
    return None


def _validate_requirement_analysis(requirement: Requirement) -> None:
    analysis: AnalysisSpec | None = requirement.analysis
    if analysis is not None:
        if analysis.operation == "compare" and analysis.comparison_basis == "none":
            raise PlannerError("comparison_basis is required for compare")
        if analysis.operation != "compare" and analysis.comparison_basis != "none":
            raise PlannerError("comparison_basis is only valid for compare")
        if analysis.operation == "rank" and analysis.ranking_measure is None:
            raise PlannerError("ranking_measure is required for rank")
        if analysis.operation == "rank" and not analysis.conclusion_required:
            raise PlannerError("rank conclusion is required")
        if analysis.operation != "rank" and analysis.ranking_measure is not None:
            raise PlannerError("ranking_measure is only valid for rank")
        if analysis.operation != "rank" and analysis.top_k is not None:
            raise PlannerError("top_k is only valid for rank")
    for target in requirement.targets:
        if target.domain == "positions":
            allowed = _POSITION_METRICS
        elif target.domain == "dataset":
            dataset = _dataset_name(target.filters)
            if dataset:
                try:
                    allowed = set(get_dataset_spec(dataset).measure_fields) | {"count"}
                except (KeyError, TypeError, ValueError) as exc:
                    raise PlannerError(f"数据目标的数据集未登记: {dataset}") from exc
            else:
                # A legacy plan may defer the dataset selection to a server-owned
                # capability lookup; still reject obviously unregistered fields.
                allowed = {"value", "count"}
        else:
            continue
        unknown = sorted(set(target.metrics) - allowed)
        if unknown:
            raise PlannerError(f"数据目标的指标未登记: {unknown[0]}")
        if analysis is not None and analysis.metric not in allowed:
            raise PlannerError(f"分析指标未登记: {analysis.metric}")
    if analysis is not None and analysis.operation == "compare" and analysis.comparison_basis == "explicit":
        has_explicit_range = requirement.time_window is not None or any(
            isinstance(target.filters, dict)
            and any(key in target.filters for key in ("current_date", "previous_date", "start_date", "end_date"))
            for target in requirement.targets
        )
        if not has_explicit_range:
            raise PlannerError("比较基期不可解析")


def validate_plan(plan: TaskPlan, catalog: dict[str, Any], restrictions: list[dict[str, Any]] | None = None) -> TaskPlan:
    """Validate plan actions against descriptive capabilities and explicit limits."""
    restrictions = (
        [item.model_dump(mode="json") for item in plan.restrictions]
        if restrictions is None else restrictions
    )
    def walk_keys(value: Any):
        if isinstance(value, dict):
            for key, child in value.items():
                yield str(key)
                yield from walk_keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk_keys(child)

    for requirement in plan.requirements:
        _validate_requirement_analysis(requirement)
        for target in requirement.targets:
            filters = target.filters if isinstance(target.filters, dict) else {}
            private_keys = {key.lower() for key in walk_keys(filters)} & _PRIVATE_PLAN_KEYS
            if private_keys:
                if target.domain == "public":
                    raise PlannerError("公开目标不能携带私有业务字段")
                raise PlannerError("规划不能包含身份、权限、令牌或执行字段")
            allowed = {
                "positions": _POSITION_FILTER_FIELDS,
                "dataset": _DATASET_FILTER_FIELDS,
                "public": _PUBLIC_FILTER_FIELDS,
                "knowledge": set(),
            }.get(target.domain, set())
            unknown = sorted(set(filters) - allowed)
            if unknown:
                raise PlannerError(f"规划包含未登记的筛选字段: {unknown[0]}")
            if target.domain == "knowledge" and filters:
                raise PlannerError("知识目标不能携带数据筛选字段")
            if target.domain == "public" and requirement.source_intent == "internal":
                raise PlannerError("公开目标的来源域不能声明为内部")
            if target.domain in {"positions", "dataset"} and requirement.source_intent == "public":
                raise PlannerError("内部目标的来源域不能声明为公开")
            if target.domain == "positions":
                _validate_position_target(filters, target.metrics)
            elif target.domain == "dataset":
                _validate_dataset_target(filters)
    no_web = any(item.get("kind") == "no_web" for item in restrictions if isinstance(item, dict))
    if no_web and any(item.source_intent in {"public", "both"} for item in plan.requirements):
        raise PlannerError("当前任务明确禁止联网，规划不能包含公开来源")
    visible_tools = {
        item.get("id") for item in (catalog.get("tools", []) if isinstance(catalog, dict) else [])
        if isinstance(item, dict)
    }
    conditional_sources = set(catalog.get("conditional_sources", []) if isinstance(catalog, dict) else [])
    public_allowed = {"search_public", "read_public"}.issubset(visible_tools) or "public_research" in conditional_sources
    if any(item.source_intent in {"public", "both"} for item in plan.requirements) and not public_allowed:
        raise PlannerError("规划需要公开资料，但当前能力目录没有公开研究能力")
    return plan
