"""One allow-listed registry for model tools and their business handlers."""
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore
import time
from datetime import date, datetime, timezone
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from .contracts import FactQuery, Shock, StrictModel, ToolEnvelope
from . import catalog, facts, risk, store, execution


class ToolResponse(StrictModel):
    status: str
    data: dict[str, Any] = Field(default_factory=dict)


class EmptyArgs(StrictModel):
    pass


class FactArgs(StrictModel):
    start_date: date
    end_date: date
    asset_type: Literal["all", "future", "option"] = "all"
    contracts: list[str] = Field(default_factory=list, max_length=50)
    direction: Literal["all", "buy", "sell"] = "all"
    classification: Literal["all", "unclassified", "classified"] = "all"


class PositionArgs(StrictModel):
    as_of_mode: Literal["latest", "settlement_date"] = "latest"
    as_of_date: date | None = None
    asset_type: Literal["all", "future", "option"] = "all"
    contracts: list[str] = Field(default_factory=list, max_length=50)
    direction: Literal["all", "buy", "sell"] = "all"
    classification: Literal["all", "unclassified", "classified"] = "all"


class SummaryArgs(StrictModel):
    result_ref: UUID
    group_by: list[str] = Field(default_factory=list, max_length=8)
    metrics: list[str] = Field(min_length=1, max_length=8)
    order_by: str | None = None
    descending: bool = True


class PageArgs(StrictModel):
    result_ref: UUID
    page: int = Field(ge=1)
    page_size: Literal[20, 50, 100] = 20
    fields: list[str] = Field(default_factory=list, max_length=30)


class CompareArgs(StrictModel):
    left_ref: UUID
    right_ref: UUID
    metrics: list[str] = Field(min_length=1, max_length=8)


class RiskArgs(StrictModel):
    snapshot_ref: UUID
    include_futures: bool = False


class ScenarioArgs(StrictModel):
    snapshot_ref: UUID
    method: Literal["black76_reprice_v1"] = "black76_reprice_v1"
    shocks: list[Shock] = Field(min_length=1, max_length=5)


class EvidenceArgs(StrictModel):
    result_ref: UUID
    metric_path: str = Field(min_length=1, max_length=200)


class PublicSearchArgs(StrictModel):
    public_query: str = Field(min_length=1, max_length=240)
    freshness: Literal["day", "week", "month", "year", "none"] = "none"


class PublicReadArgs(StrictModel):
    source_ref: str = Field(min_length=1, max_length=500)


def _query_from_position(args: PositionArgs) -> FactQuery:
    return FactQuery(
        as_of={"mode": args.as_of_mode, "date": args.as_of_date},
        asset_type=args.asset_type,
        contracts=args.contracts,
        direction=args.direction,
        classification=args.classification,
    )


_quote_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-quotes")
_quote_slot = BoundedSemaphore(1)


def default_quote_provider(requests, *, timeout_seconds=5):
    from ..trading_valuation import get_quote_snapshots

    execution.checkpoint()
    scope = execution.current.get()
    wait = timeout_seconds if scope is None else min(timeout_seconds, scope.deadline - time.monotonic() - 3)
    if wait <= 0 or not _quote_slot.acquire(blocking=False):
        raise TimeoutError("quote_wait_unavailable")
    try:
        future = _quote_executor.submit(get_quote_snapshots, requests)
    except BaseException:
        _quote_slot.release()
        raise
    # A cold provider may finish later and warm its own cache. Never enqueue
    # more background fetches while it is still running, or persist late data.
    future.add_done_callback(lambda _: _quote_slot.release())
    return future.result(timeout=wait)


def _capability_envelope(principal) -> ToolEnvelope:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    data = {
        "account_scope": "宏源期货 canonical account（由服务端确定）",
        "defaults": {"as_of": "latest", "timezone": "Asia/Shanghai"},
        "dimensions": catalog.DIMENSIONS,
        "metrics": {kind: sorted(values) for kind, values in catalog.METRICS.items()},
        "tools": sorted(TOOL_SPECS),
        "limits": {"snapshot_rows": 20000, "preview_rows": 20, "page_size": [20, 50, 100]},
        "calculation_versions": {"facts": facts.VERSION, "risk": risk.VERSION},
    }
    return ToolEnvelope(status="complete", captured_at=now, calculation_version="catalog-v2", payload=data)


def dispatch(principal, name: str, arguments: dict[str, Any] | None = None, *, quote_provider=None) -> ToolEnvelope:
    """Validate and run one registered read-only tool under the current principal."""
    if name not in TOOL_SPECS:
        raise ValueError("未知工具")
    arguments = arguments or {}
    spec = TOOL_SPECS[name]
    args = spec["model"].model_validate(arguments)
    if name == "describe_capabilities":
        from .auth import authorize

        authorize(principal, "closing_review.agent")
        return _capability_envelope(principal)
    if name == "query_positions":
        return facts.capture_positions(principal, _query_from_position(args), quote_provider or default_quote_provider)
    if name == "query_trade_facts":
        return facts.capture_facts(principal, "trades", args.start_date, args.end_date,
                                   FactQuery(asset_type=args.asset_type, contracts=args.contracts,
                                             direction=args.direction, classification=args.classification))
    if name == "query_close_facts":
        return facts.capture_facts(principal, "closes", args.start_date, args.end_date,
                                   FactQuery(asset_type=args.asset_type, contracts=args.contracts,
                                             direction=args.direction, classification=args.classification))
    if name == "summarize_positions":
        return facts.summarize(principal, args.result_ref, args.group_by, args.metrics, args.order_by, args.descending)
    if name == "summarize_facts":
        return facts.summarize(principal, args.result_ref, args.group_by, args.metrics, args.order_by, args.descending)
    if name == "read_result_page":
        return facts.read_page(principal, args.result_ref, args.page, args.page_size, args.fields or None)
    if name == "compare_results":
        return facts.compare(principal, args.left_ref, args.right_ref, args.metrics)
    if name == "get_position_risk":
        return risk.position_risk(principal, args.snapshot_ref, args.include_futures)
    if name == "run_scenario":
        return risk.scenario(principal, args.snapshot_ref, args.shocks, args.method)
    if name == "explain_evidence":
        return explain_evidence(principal, args.result_ref, args.metric_path)
    if name == "search_public":
        from .research import search_public

        return search_public(principal, args.public_query, args.freshness)
    if name == "read_public":
        from .research import read_public

        return read_public(principal, args.source_ref)
    raise AssertionError(name)


def explain_evidence(principal, result_ref, metric_path):
    saved = store.load_result(principal, result_ref)
    if not metric_path.startswith("/metrics/") and not metric_path.startswith("metrics/"):
        raise ValueError("只能解释结果指标")
    metric_name = metric_path.rsplit("/", 1)[-1]
    metric = saved.envelope.metrics.get(metric_name)
    if metric is None:
        raise ValueError("指标不存在")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return ToolEnvelope(status="complete", captured_at=now, data_as_of=saved.envelope.data_as_of,
        calculation_version=saved.envelope.calculation_version,
        payload={"metric_path": metric_path, "metric": metric.model_dump(),
                 "kind": saved.envelope.payload.get("kind"),
                 "source_ref": str(result_ref),
                 "formula": "由已登记业务服务计算，结果快照不重新取行情"},
        evidence_refs=[f"{result_ref}#/metrics/{metric_name}"])


TOOL_SPECS: dict[str, dict[str, Any]] = {
    "describe_capabilities": {"model": EmptyArgs, "description": "返回当前授权范围内实际支持的属性、指标和限制。"},
    "query_trade_facts": {"model": FactArgs, "description": "按事实交易日读取宏源去重后的全量成交事实。"},
    "query_close_facts": {"model": FactArgs, "description": "读取已核验的平仓、行权、履约或放弃事实。"},
    "query_positions": {"model": PositionArgs, "description": "读取宏源全量有效持仓并冻结行情。as_of_mode=latest 时必须省略 as_of_date 或传 null；仅 settlement_date 模式需要 YYYY-MM-DD 日期，不支持精确历史时刻。返回 metrics.quantity 是全量持仓总手数；若只问总手数，可直接引用该指标，无需重复查询。partial 可能仅因行情缺失，应按每个指标自身的 status 判断可用性。"},
    "summarize_positions": {"model": SummaryArgs, "description": "基于完整持仓快照按白名单属性汇总。"},
    "summarize_facts": {"model": SummaryArgs, "description": "基于完整事实结果按白名单属性汇总。"},
    "read_result_page": {"model": PageArgs, "description": "读取已授权不可变结果的下一页。"},
    "compare_results": {"model": CompareArgs, "description": "比较单位和估值口径一致的两个结果。"},
    "get_position_risk": {"model": RiskArgs, "description": "基于冻结的全量期权持仓计算分标的Greeks敞口。"},
    "run_scenario": {"model": ScenarioArgs, "description": "按明确冲击运行受支持的Black76模型情景。"},
    "explain_evidence": {"model": EvidenceArgs, "description": "解释一个已登记指标的来源、口径和版本。"},
    "search_public": {"model": PublicSearchArgs, "description": "只搜索不含私有业务数据的公开资料。"},
    "read_public": {"model": PublicReadArgs, "description": "读取本任务已登记的公开来源。"},
}


def tool_schemas() -> list[dict[str, Any]]:
    return [{"name": name, "description": spec["description"], "inputSchema": spec["model"].model_json_schema()}
            for name, spec in TOOL_SPECS.items()]
