"""Materialize scoped business facts once, then operate on immutable evidence."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import re

from .. import db, trading_effective_facts as effective
from ..trading_valuation import QuoteRequest, QuoteSnapshot, select_live_trade_price, calculate_live_position_floating_pnl
from . import execution, progress, store
from .auth import authorize, require_account_scope
from .catalog import DIMENSIONS, METRICS, validate_summary
from .contracts import FactQuery, MetricValue, ToolEnvelope

VERSION = "effective-facts-live-pnl-v2"
PUBLIC_FIELDS = set(DIMENSIONS) | {"row_ref","quantity","average_price","fee","realized_close_pnl",
    "floating_pnl","group_key","valuation_price","valuation_status","market_time","contract_multiplier",
    "underlying_symbol","underlying_price","expiry_date","formation_method","settlement_type","count",
    "iv","delta","gamma","theta","vega","rho","unit_greeks","display_greeks","position_exposures"}


def _account_labels(cur, account_ids):
    account_ids = tuple(int(account_id) for account_id in account_ids)
    if not account_ids:
        return {}
    placeholders = ",".join("?" for _ in account_ids)
    rows = db._exec(
        cur,
        f"""SELECT id, COALESCE(NULLIF(masked_name, ''), NULLIF(display_name, ''), account_code) AS account_label
            FROM trading_accounts WHERE id IN ({placeholders}) AND is_active = 1""",
        account_ids,
    ).fetchall()
    return {int(row["id"]): str(row["account_label"]) for row in rows if row["account_label"]}


def _attach_account_labels(rows, labels):
    for row in rows:
        account_id = row.get("account_id")
        if account_id is None:
            continue
        try:
            label = labels.get(int(account_id))
        except (TypeError, ValueError):
            label = None
        if label:
            row["account"] = label


def _position_groups(rows, *, limit=100):
    group_by = ("account", "contract", "asset_type", "direction")
    grouped = {}
    for row in rows:
        key = tuple(row.get(field) for field in group_by)
        grouped.setdefault(key, []).append(row)
    ordered = sorted(grouped.items(), key=lambda item: tuple(str(value or "") for value in item[0]))
    groups = []
    for key, items in ordered[:limit]:
        groups.append({
            "dimensions": dict(zip(group_by, key)),
            "metrics": {
                name: _metric(items, name, unit).model_dump()
                for name, unit in METRICS["positions"].items()
            },
        })
    return groups, len(ordered), len(ordered) > limit


def _summary_groups(rows, group_by, metrics, *, limit=100):
    groups = []
    for row in rows[:limit]:
        coverage = row.get("_coverage") or {}
        groups.append({
            "dimensions": {field: row.get(field) for field in group_by},
            "metrics": {name: coverage[name] for name in metrics if name in coverage},
        })
    return groups, len(rows), len(rows) > limit


def _transaction(conn):
    db._exec(conn.cursor(), "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY" if db._is_pg() else "BEGIN")


def _filters(principal, query):
    if query.as_of.mode=="settlement_date" and query.classification!="all":
        raise ValueError("历史归属缺少版本证据，不能按当前归类筛选历史")
    filters = effective.EffectiveFactFilters(account_ids=principal.account_ids,
        asset_type="" if query.asset_type=="all" else query.asset_type,
        direction="" if query.direction=="all" else query.direction,
        classification="" if query.classification=="all" else query.classification,
        end_date=query.as_of.date.isoformat() if query.as_of.date else "",page_size=100)
    require_account_scope(filters)
    return filters


def _select_contracts(rows, contracts):
    selected = {s.strip().lower() for s in contracts}
    return [row for row in rows if not selected or str(row.get("contract","")).lower() in selected]


def _project(row, fields=None):
    fields = fields or PUBLIC_FIELDS
    if any(field not in PUBLIC_FIELDS for field in fields):
        raise ValueError("字段不在公开业务目录中")
    return {key:row.get(key) for key in sorted(fields) if key in row}


def _metric(rows, name, unit, *, available=True):
    eligible = len(rows)
    if not available or not rows:
        return MetricValue(value=None,unit=unit,covered_rows=0,eligible_rows=eligible,status="unavailable")
    values = []
    for row in rows:
        raw = 1 if name == "count" else row.get(name)
        if raw is None:
            continue
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, ValueError, TypeError):
            continue
        if value.is_finite():
            values.append(value)
    covered = len(values)
    value = str(sum(values,Decimal(0))) if values else None
    return MetricValue(value=value,unit=unit,covered_rows=covered,eligible_rows=eligible,
        status="unavailable" if value is None else "complete" if covered==eligible else "partial")


def _persist(principal, rows, kind, *, status="complete", warnings=None, metadata=None, metrics=None, data_as_of=None, parent_ref=None):
    if len(rows)>20000:
        return ToolEnvelope(status="limit_exceeded",captured_at=store.now(),calculation_version=VERSION,missing=[{"reason":"请缩小查询范围"}])
    envelope = ToolEnvelope(status=status,captured_at=store.now(),data_as_of=data_as_of,calculation_version=VERSION,
        payload={"kind":kind,"count":len(rows),"preview":[_project(row) for row in rows[:20]],
            "preview_count": min(len(rows), 20), "preview_truncated": len(rows) > 20, **(metadata or {})},
        warnings=warnings or [],metrics=metrics or {},quote_times=(metadata or {}).get("quote_times",{}))
    with execution.phase("evidence_save"):
        ref = store.save_result(principal,envelope,rows,kind=kind,parent_ref=parent_ref)
        return store.load_result(principal,ref).envelope


def capture_positions(principal, query: FactQuery, quote_provider):
    authorize(principal,"trading.facts")
    filters = _filters(principal,query)
    from ..trading_management import _active_contract_spec_multipliers
    progress.record_stage(principal, "internal_data", "running")
    try:
        with execution.phase("positions_database"), db.connect() as conn:
            _transaction(conn)
            if db._is_pg():
                db._exec(conn.cursor(), "SET LOCAL statement_timeout = '10000'")
            raw = effective.query_effective_positions(conn.cursor(),filters,include_all_items=True)
            specs = _active_contract_spec_multipliers(conn.cursor())
            account_labels = _account_labels(conn.cursor(), principal.account_ids)
    except Exception:
        progress.record_stage(principal, "internal_data", "failed")
        raise
    progress.record_stage(principal, "internal_data", "complete")
    rows = deepcopy(_select_contracts(raw.get("all_items",[]),query.contracts))
    _attach_account_labels(rows, account_labels)
    if len(rows)>20000:
        return _persist(principal,rows,"positions")
    requests = {str(row["contract"]):QuoteRequest(contract=str(row["contract"]),exchange=str(row.get("exchange") or ""),asset_type=str(row.get("asset_type") or "")) for row in rows}
    historical = query.as_of.mode=="settlement_date"
    warnings = list(raw.get("warnings",[]))
    quotes = {}
    if requests and not historical:
        progress.record_stage(principal, "quotes", "running")
        quote_status = "complete"
        try:
            with execution.phase("positions_quotes"):
                supplied_quotes = quote_provider(list(requests.values()))
            if isinstance(supplied_quotes, dict):
                quotes = deepcopy(supplied_quotes)
            else:
                warnings.append("行情提供方返回格式无效；浮盈亏及Greeks未覆盖")
        except Exception:
            quote_status = "failed"
            warnings.append("行情提供方暂时不可用；浮盈亏及Greeks未覆盖")
        finally:
            progress.record_stage(principal, "quotes", quote_status)
    if historical:
        warnings.append("历史持仓没有同期行情，不计算历史浮盈亏及Greeks；当前归属不作历史归属。")
    execution.checkpoint()
    quote_times = {}
    for index,row in enumerate(rows):
        contract = str(row["contract"])
        quote = quotes.get(contract,QuoteSnapshot())
        price,source,status = select_live_trade_price(quote)
        product = re.match(r"[a-zA-Z]+",contract)
        product = product.group().lower() if product else ""
        multiplier = quote.multiplier or specs.get((str(row.get("exchange","")).lower(),product,row.get("asset_type")))
        row.update(row_ref=str(index),product=product,group_key=None,valuation_price=price,valuation_status=status,
            market_time=quote.market_time,contract_multiplier=multiplier,underlying_symbol=quote.underlying_symbol,
            underlying_price=quote.underlying_price,expiry_date=quote.expiry_date,floating_pnl=None,
            _quote=asdict(quote))
        if historical:
            row["assignment_status"] = "unavailable"
        if status=="stale":
            warnings.append(f"{contract}沿用上次行情")
        quote_times[contract] = quote.market_time
        if price is not None and row.get("average_price") is not None and multiplier is not None and multiplier>0 and raw["data_status"]=="ok":
            row["floating_pnl"] = calculate_live_position_floating_pnl(open_price=float(row["average_price"]),market_price=price,
                direction=row["direction"],remaining_quantity=float(row["quantity"]),multiplier=multiplier)
    valid = raw["data_status"]=="ok"
    metrics = {name:_metric(rows,name,unit,available=valid and (name!="floating_pnl" or not historical)) for name,unit in METRICS["positions"].items()}
    status = "complete" if valid and all(m.status=="complete" for m in metrics.values()) and not warnings else "partial" if valid else "waiting_for_data"
    metadata = {"as_of":query.as_of.model_dump(mode="json"),"valuation_basis":"historical_unavailable" if historical else "latest_trade",
        "assignment_basis":"unavailable" if historical else "current","data_status":raw["data_status"],"quote_times":quote_times,
        "selection":query.model_dump(mode="json",exclude={"as_of"}),
        "provenance":raw.get("provenance") or {"data_as_of":None,"precision":None,"source_observations":[]}}
    position_groups, group_count, groups_truncated = _position_groups(rows)
    metadata.update({"groups": position_groups, "group_count": group_count, "groups_truncated": groups_truncated})
    if groups_truncated:
        warnings.append("持仓分组超过100组；总量仍按全量快照计算，逐组合约明细仅返回受限预览。")
    return _persist(principal,rows,"positions",status=status,warnings=warnings,metrics=metrics,metadata=metadata)


def capture_facts(principal, kind, start_date, end_date, filters=None):
    authorize(principal,"trading.facts")
    if kind not in {"trades","closes"}:
        raise ValueError("未知事实类型")
    start,end = date.fromisoformat(str(start_date)),date.fromisoformat(str(end_date))
    if start>end:
        raise ValueError("开始日期晚于结束日期")
    query = filters or FactQuery()
    scoped = replace(_filters(principal,query),start_date=start.isoformat(),end_date=end.isoformat())
    rows=[]
    progress.record_stage(principal, "internal_data", "running")
    try:
        with db.connect() as conn:
            _transaction(conn)
            for page in range(1,202):
                if kind=="trades":
                    result = effective.query_effective_trades(conn.cursor(),replace(scoped,page=page))
                else:
                    from ..trading_management import FactFilters, _query_close_rows_paged
                    result = _query_close_rows_paged(conn.cursor(),FactFilters(account_ids=principal.account_ids,
                        contract=query.contracts[0] if len(query.contracts) == 1 else "",
                        direction={"all": "", "buy": "买", "sell": "卖"}[query.direction],
                        asset_type="" if query.asset_type == "all" else query.asset_type,
                        classification="" if query.classification == "all" else query.classification,
                        start_date=start.isoformat(),end_date=end.isoformat(),page=page,page_size=100))
                rows.extend(result["items"])
                if len(rows)>20000 or page>=result["total_pages"]:
                    break
            account_labels = _account_labels(conn.cursor(), principal.account_ids)
    except Exception:
        progress.record_stage(principal, "internal_data", "failed")
        raise
    progress.record_stage(principal, "internal_data", "complete")
    if len(rows)>20000:
        return _persist(principal,rows,kind)
    rows = _select_contracts(rows,query.contracts)
    _attach_account_labels(rows, account_labels)
    for index,row in enumerate(rows):
        row["row_ref"] = str(index)
        row["direction"] = row.get("side",row.get("open_side"))
        row["trade_date"] = row.get("trade_date",row.get("close_date"))
        row["realized_close_pnl"] = row.get("fact_close_pnl")
        row["group_key"] = None
    metrics = {name:_metric(rows,name,unit) for name,unit in METRICS[kind].items()}
    return _persist(principal,rows,kind,metrics=metrics,metadata={"date_range":[start.isoformat(),end.isoformat()],"valuation_basis":"settlement_fact"},
        status="complete" if all(m.status=="complete" for m in metrics.values()) else "partial")


def read_page(principal,result_ref,page,page_size,fields):
    if type(page) is not int or page<1 or page_size not in {20,50,100}:
        raise ValueError("分页无效")
    saved = store.load_result(principal,result_ref)
    visible = saved.rows[(page-1)*page_size:page*page_size]
    return saved.envelope.model_copy(deep=True,update={"payload":{**saved.envelope.payload,
        "preview":[_project(row,fields) for row in visible],"page":page,"page_size":page_size}})


def summarize(principal,result_ref,group_by,metrics,order_by=None,descending=True):
    saved = store.load_result(principal,result_ref)
    kind = saved.envelope.payload["kind"]
    validate_summary(kind,group_by,metrics,order_by)
    if saved.envelope.payload.get("assignment_basis")=="unavailable" and "assignment_status" in group_by:
        raise ValueError("历史归属缺少版本证据")
    groups={}
    for row in saved.rows:
        groups.setdefault(tuple(row.get(field) for field in group_by),[]).append(row)
    rows=[]
    for key,items in groups.items():
        values={name:_metric(items,name,METRICS[kind][name]) for name in metrics}
        rows.append({**dict(zip(group_by,key)),**{name:value.value for name,value in values.items()},"_coverage":{name:value.model_dump() for name,value in values.items()}})
    if order_by:
        present=[row for row in rows if row.get(order_by) is not None]
        present.sort(key=lambda row:Decimal(row[order_by]) if order_by in metrics else str(row[order_by]),reverse=descending)
        rows=present+[row for row in rows if row.get(order_by) is None]
    available = saved.envelope.status not in {"waiting_for_data","data_anomaly"}
    totals = {name:_metric(saved.rows,name,METRICS[kind][name],available=available) for name in metrics}
    warnings = list(saved.envelope.warnings)
    if len(rows) > 100:
        warnings.append("分组超过100组；保留完整结果引用并只返回受限预览，可通过结果引用继续分页读取。")
    group_payload, group_count, groups_truncated = _summary_groups(rows, group_by, metrics)
    return _persist(principal,rows,kind,status=saved.envelope.status,warnings=warnings,metrics=totals,
        parent_ref=result_ref,metadata={**{key:value for key,value in saved.envelope.payload.items() if key not in {
            "preview", "preview_count", "preview_truncated", "count", "kind",
            "groups", "group_count", "groups_truncated",
        }},
            "group_by":group_by,"source_ref":str(result_ref),"aggregation":True,
            "groups":group_payload,"group_count":group_count,"groups_truncated":groups_truncated})


def compare(principal,left_ref,right_ref,metrics):
    left,right = (store.load_result(principal,ref) for ref in (left_ref,right_ref))
    keys=("kind","valuation_basis","assignment_basis","group_by","selection")
    comparable=all(left.envelope.payload.get(key)==right.envelope.payload.get(key) for key in keys)
    for name in metrics:
        l,r=left.envelope.metrics.get(name),right.envelope.metrics.get(name)
        comparable = comparable and l is not None and r is not None and l.unit==r.unit and l.status==r.status=="complete"
    if not comparable:
        return ToolEnvelope(status="not_comparable",captured_at=store.now(),calculation_version=VERSION,warnings=["单位、覆盖范围或估值口径不可比"])
    values={name:MetricValue(value=str(Decimal(right.envelope.metrics[name].value)-Decimal(left.envelope.metrics[name].value)),unit=left.envelope.metrics[name].unit,
        status="complete",covered_rows=2,eligible_rows=2) for name in metrics}
    return _persist(principal,[],"comparison",metrics=values,metadata={"left_ref":str(left_ref),"right_ref":str(right_ref)})
