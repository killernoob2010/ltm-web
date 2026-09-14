"""Bounded task planning before the model is allowed to call business tools."""
from __future__ import annotations

import asyncio
from datetime import date
import json
import re
from typing import Any

from pydantic import TypeAdapter, ValidationError

from .contracts import FactQuery, PositionFilter
from .dv_contracts import DataFilters, DatasetQuery, validate_dataset_query
from .planning_contracts import TaskPlan


class PlannerError(ValueError):
    """A planning response cannot be safely used for execution."""


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
遇到“净买 Call 与净卖 Put”或类似并列对象时，必须为 Call、Put 建立独立分析目标，保留双边实际持仓，并同时请求 gross 数量、净额和实际浮盈亏；不得用一个 option_type=all 的目标替代对象拆分，也不得先按单边方向过滤原始持仓。
用户明确说不要联网时，生成 no_web 限制；不要自行取消。公开需求只写公开地区、品种、日期和主题，不写账户、持仓数量、盈亏、客户、订单或内部编码。
模型不能填写 user_id、account_id、权限、执行令牌、SQL、代码、预算或工具调用。只输出符合给定 JSON Schema 的 JSON，不要输出解释、Markdown 围栏或工具调用。
"""


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


def validate_plan(plan: TaskPlan, catalog: dict[str, Any], restrictions: list[dict[str, Any]] | None = None) -> TaskPlan:
    """Validate plan actions against descriptive capabilities and explicit limits."""
    restrictions = restrictions or []
    def walk_keys(value: Any):
        if isinstance(value, dict):
            for key, child in value.items():
                yield str(key)
                yield from walk_keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk_keys(child)

    for requirement in plan.requirements:
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
