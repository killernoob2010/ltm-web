"""Small deterministic request and answer-coverage contracts.

The language model may suggest tool arguments, but this module keeps the
business scope check on the server side.  It intentionally covers only the
conditions that can be verified from the registered position result; it does
not pretend to replace a full natural-language evaluator.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .contracts import AnswerCoverage, ResolvedRequest


_MONTH = re.compile(r"(?<![A-Za-z0-9])(\d{3,4})(?![A-Za-z0-9])")
_POSITION = re.compile(
    r"持仓|期权|期货|合约|净卖|浮盈|浮亏|手数|多少手|账户|"
    r"只看卖|只看买|卖方|买方|卖的|买的|卖出|买入|Call|Put|看涨|看跌|认购|认沽",
    re.I,
)
_PUBLIC = re.compile(
    r"新闻|消息|财报|年报|公告|官网|公开资料|公开信息|供需信息|市场消息|研报|政策|规则|发运新闻",
    re.I,
)
_REF_METRIC = re.compile(r"#/metrics/([A-Za-z][A-Za-z0-9_]*)|#/payload/(?:groups|semantic_groups)/\d+/metrics/([A-Za-z][A-Za-z0-9_]*)")


def _text(value: Any) -> str:
    return str(value or "").strip()


def presentation_policy(text: Any) -> dict[str, Any]:
    """Return an explicit display mode and separately tracked prohibitions."""
    value = _text(text)
    no_table = bool(re.search(r"(?:不要|不用|不需要|无需|禁止)\s*表格", value, re.I))
    wants_chart = bool(re.search(r"图表|柱状图|折线图|曲线|画图|绘图", value, re.I))
    wants_text = bool(re.search(r"纯文字|纯文本|只要文字|只用文字|文字展示|用列表|列表说明|条目说明|简短文字|简短回答", value, re.I))
    wants_table = bool(re.search(r"表格|电子表格|数据表", value, re.I)) and not no_table
    if wants_chart:
        mode = "chart"
    elif wants_text:
        mode = "text"
    elif wants_table:
        mode = "table"
    else:
        mode = "auto"
    return {
        "mode": mode,
        "prohibited_presentations": ["table"] if no_table else [],
    }


def presentation_preference(text: Any) -> str:
    """Compatibility wrapper used by the existing prompt and validator."""
    return str(presentation_policy(text)["mode"])


def is_presentation_only_followup(text: Any) -> bool:
    """Recognize a format-only follow-up that may reuse the prior snapshot."""
    value = _text(text)
    wants_display = bool(re.search(r"改成|换成|切换|用|纯文字|表格|图表|画图|列表|简短", value, re.I))
    changes_data = bool(re.search(
        r"刷新|更新|另一个|其他|月份|期权|期货|合约|持仓|库存|数量|手数|浮盈|浮亏|净卖|Call|Put|卖出|买入|查询|查一下|搜索|新闻|财报",
        value, re.I,
    ))
    return wants_display and not changes_data


def _nearby_months(text: str) -> list[str]:
    values = []
    for match in _MONTH.finditer(text):
        value = match.group(1)
        if len(value) == 4 and value[:2] in {"19", "20"}:
            continue
        left = text[max(0, match.start() - 12):match.start()]
        right = text[match.end():match.end() + 12]
        if re.search(r"期权|期货|合约|月份|Call|Put|看涨|看跌|认购|认沽", left + right, re.I):
            values.append(value)
    return list(dict.fromkeys(values))


def _previous_request(inherited: Any) -> dict[str, Any]:
    if isinstance(inherited, ResolvedRequest):
        return inherited.model_dump(mode="json")
    if isinstance(inherited, dict):
        return dict(inherited)
    return {}


def inherited_request_from_history(history: Any) -> dict[str, Any]:
    """Read the last server-recorded request contract from bounded history."""
    for item in reversed(list(history or [])):
        content = item.get("content") if isinstance(item, dict) else ""
        match = re.search(r"resolved_request=(\{.*\})", str(content or ""))
        if not match:
            continue
        try:
            value = json.loads(match.group(1))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value
    return {}


def resolve_request(text: Any, inherited: Any = None) -> ResolvedRequest:
    """Resolve the bounded request facts needed by final coverage checks."""
    value = _text(text)
    previous = _previous_request(inherited)
    previous_filters = dict(previous.get("filters") or {})
    public = bool(_PUBLIC.search(value))
    inherited_position = previous.get("domain") == "positions" and (
        is_presentation_only_followup(value)
        or bool(
            re.search(
                r"刷新|更新快照|重新查询|重新读取|月份|期权|期货|合约|数量|手数|Call|Put|看涨|看跌|卖出|买入|全部期权|所有月份|(?:换成|改成|改为|切换到|切换为)\s*\d{3,4}",
                value,
                re.I,
            )
        )
    )
    position = bool(_POSITION.search(value)) or (inherited_position and not public)
    if position:
        domain = "positions"
        source_mode = "mixed" if public else "internal"
    elif public:
        domain = "public"
        source_mode = "public"
    else:
        domain = "general"
        source_mode = "general"

    filters = dict(previous_filters) if position else {}
    if re.search(r"铁矿石|\bI\b|\bi\d{3,4}", value, re.I):
        filters["products"] = ["铁矿石"]
    elif re.search(r"所有品种|全部品种|不限品种", value, re.I):
        filters.pop("products", None)
    months = _nearby_months(value)
    if not months and previous.get("domain") == "positions" and re.search(r"换成|改成|改为|切换到|切换为", value, re.I):
        months = [
            match.group(1) for match in _MONTH.finditer(value)
            if not (len(match.group(1)) == 4 and match.group(1)[:2] in {"19", "20"})
        ]
    if months:
        filters["contract_months"] = months
    elif re.search(r"所有月份|全部月份|不限月份", value, re.I):
        filters.pop("contract_months", None)

    if re.search(r"全部期权|所有期权|全部的?期权|所有的?期权", value, re.I):
        filters["asset_type"] = "option"
        filters["option_type"] = "all"
    elif re.search(r"期权", value, re.I):
        filters.setdefault("asset_type", "option")
    elif re.search(r"期货", value, re.I):
        filters["asset_type"] = "future"
    has_call = bool(re.search(r"Call|看涨|认购", value, re.I))
    has_put = bool(re.search(r"Put|看跌|认沽", value, re.I))
    if has_call or has_put:
        filters["asset_type"] = "option"
    if has_call and has_put:
        filters["option_type"] = "all"
    elif has_call:
        filters["option_type"] = "call"
    elif has_put:
        filters["option_type"] = "put"
    if re.search(r"其中(?:的)?卖|只看卖|卖出持仓|卖方持仓|卖的", value, re.I):
        filters["direction"] = "sell"
    elif re.search(r"其中(?:的)?买|只看买|买入持仓|买方持仓|买的", value, re.I):
        filters["direction"] = "buy"
    if re.search(r"全部期权|所有期权|全部的?期权|所有的?期权", value, re.I):
        filters.pop("direction", None)

    metric_names: list[str] = []
    if re.search(r"只(?:说|看|要|需)数量|只查(?:手数|数量)|(?:手数|数量)就行", value, re.I):
        metric_names = ["quantity"]
    else:
        if re.search(r"手数|数量|多少手", value, re.I):
            metric_names.append("quantity")
        if re.search(r"净卖|净额|净仓", value, re.I):
            metric_names.append("net_quantity")
        if re.search(r"浮盈|浮亏|盈亏", value, re.I):
            metric_names.append("floating_pnl")
        if re.search(r"万吨|净吨|吨数", value, re.I):
            metric_names.append("net_wan_tons" if "万吨" in value else "net_tons")
    if position and not metric_names:
        metric_names = list(previous.get("required_metrics") or ["quantity"])
    metric_names = list(dict.fromkeys(metric_names))

    group_by = list(previous.get("group_by") or []) if position else []
    if re.search(r"Call|Put|看涨|看跌|认购|认沽|各自|分别", value, re.I):
        if "option_type" not in group_by:
            group_by.append("option_type")
    if len(months) > 1 and "contract_month" not in group_by:
        group_by.append("contract_month")

    display = presentation_policy(value)
    if display["mode"] == "auto":
        inherited_mode = previous.get("presentation", "auto") or "auto"
        inherited_prohibited = list(previous.get("prohibited_presentations") or [])
        current_prohibited = list(display["prohibited_presentations"])
        display["prohibited_presentations"] = list(dict.fromkeys([*inherited_prohibited, *current_prohibited]))
        display["mode"] = "auto" if inherited_mode in display["prohibited_presentations"] else inherited_mode
    refs = previous.get("inherited_result_refs") or []
    refresh = bool(re.search(r"刷新|重新查询|重新读取|更新快照", value, re.I))
    return ResolvedRequest(
        domain=domain,
        source_mode=source_mode,
        filters=filters,
        required_metrics=metric_names,
        group_by=group_by,
        presentation=display["mode"],
        prohibited_presentations=display["prohibited_presentations"],
        refresh=refresh,
        inherited_result_refs=[str(item) for item in refs if item],
    )


def _payload(envelope: Any) -> dict[str, Any]:
    value = getattr(envelope, "payload", None)
    return value if isinstance(value, dict) else {}


def _metric_names(draft: Any, result: Any) -> set[str]:
    names: set[str] = set()
    for span in getattr(draft, "spans", []) or []:
        for ref in span.refs:
            match = _REF_METRIC.search(str(ref))
            while match:
                names.add(match.group(1) or match.group(2))
                break
    for view in getattr(result, "views", []) or []:
        for field in view.get("fields", []) if isinstance(view, dict) else []:
            names.add(str(field))
    for item in getattr(result, "evidence", []) or []:
        match = _REF_METRIC.search(str(getattr(item, "source_ref", "") or ""))
        if match:
            names.add(match.group(1) or match.group(2))
    body = _text(getattr(result, "plain_text", ""))
    labels = {
        "quantity": r"总手数|手数|数量",
        "net_quantity": r"净卖|净额|净仓",
        "floating_pnl": r"浮盈|浮亏|浮盈亏",
        "net_tons": r"净吨",
        "net_wan_tons": r"净万吨",
    }
    for name, pattern in labels.items():
        if re.search(pattern, body, re.I):
            names.add(name)
    return names


def assess_position_coverage(user_text: Any, envelopes: list[Any], result: Any, draft: Any = None, inherited: Any = None):
    """Check the user-requested position scope against returned evidence.

    The return value is `(request, coverage)`.  Keeping this pure makes it
    usable by focused tests and prevents the model from changing server-owned
    query scope after the fact.
    """
    request = resolve_request(user_text, inherited)
    if request.domain != "positions":
        return request, AnswerCoverage()
    position_envelopes = [
        item for item in envelopes
        if _payload(item).get("kind") == "positions"
        and getattr(item, "status", None) in {"complete", "partial"}
    ]
    explicit_position_scope = bool(re.search(
        r"期权|期货|合约|月份|手数|数量|净卖|浮盈|浮亏|Call|Put|吨|卖出|买入|铁矿石",
        _text(user_text), re.I,
    ))
    if not position_envelopes and not explicit_position_scope:
        return request, AnswerCoverage()
    missing: list[str] = []
    matched: list[str] = []
    expected = request.filters
    for envelope in position_envelopes:
        selection = _payload(envelope).get("selection") or {}
        actual_filters = selection.get("filters") or {}
        actual_asset = selection.get("asset_type", "all")
        if expected.get("asset_type") and actual_asset != expected["asset_type"]:
            continue
        expected_months = set(expected.get("contract_months") or [])
        actual_months = set(actual_filters.get("contract_months") or [])
        if expected_months and actual_months != expected_months:
            continue
        expected_products = set(expected.get("products") or [])
        actual_products = set(actual_filters.get("products") or [])
        if expected_products and actual_products != expected_products:
            continue
        expected_type = expected.get("option_type")
        actual_type = actual_filters.get("option_type", "all")
        if expected_type and expected_type != actual_type:
            continue
        expected_direction = expected.get("direction")
        if expected_direction and selection.get("direction", "all") != expected_direction:
            continue
        matched.append(str(getattr(envelope, "result_ref", "")))
    if not matched:
        if expected.get("contract_months"):
            missing.append("filter.contract_months")
        if expected.get("products"):
            missing.append("filter.products")
        if expected.get("option_type"):
            missing.append("filter.option_type")
        if expected.get("direction"):
            missing.append("filter.direction")
        if expected.get("asset_type"):
            missing.append("filter.asset_type")
        if not missing:
            missing.append("positions_result")

    covered_metrics = _metric_names(draft, result)
    metric_missing = [name for name in request.required_metrics if name not in covered_metrics]
    missing.extend(f"metric.{name}" for name in metric_missing)

    expected_groups = set()
    if "option_type" in request.group_by or expected.get("option_type") == "all":
        expected_groups.update({"call", "put"} if expected.get("option_type") in {None, "all"} else {expected["option_type"]})
    body = _text(getattr(result, "plain_text", ""))
    if expected_groups and not all(re.search(rf"\b{re.escape(group)}\b|{('看涨' if group == 'call' else '看跌')}", body, re.I) for group in expected_groups):
        missing.append("groups.option_type")

    status = "complete" if not missing and getattr(result, "delivery_status", "partial") == "complete" else "partial"
    coverage = AnswerCoverage(
        status=status,
        expected_filters=expected,
        matched_result_refs=list(dict.fromkeys(item for item in matched if item)),
        covered_metrics=sorted(covered_metrics),
        missing=list(dict.fromkeys(missing)),
    )
    return request, coverage


__all__ = [
    "assess_position_coverage", "inherited_request_from_history", "is_presentation_only_followup",
    "presentation_policy", "presentation_preference", "resolve_request",
]
