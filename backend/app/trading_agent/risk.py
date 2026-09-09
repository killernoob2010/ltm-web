"""Full-snapshot Greeks and explicitly parameterized model scenarios."""
import math
from decimal import Decimal

from ..trading_valuation import calculate_option_position_valuation, calculate_option_display_greeks, _black76_price
from . import store
from .contracts import ToolEnvelope, MetricValue, Shock

UNITS = {"delta":"CNY/标的价格单位","gamma":"CNY/标的价格单位²",
    "theta":"CNY/年","vega":"CNY/波动率绝对值1","rho":"CNY/利率绝对值1"}
VERSION = "full-position-greeks-v2"


def _valid(value):
    return value is not None and isinstance(value,(float,int)) and math.isfinite(value)


def _snapshot(principal,ref):
    saved = store.load_result(principal,ref)
    if saved.envelope.payload.get("kind")!="positions" or saved.envelope.payload.get("aggregation"):
        raise ValueError("需要原始持仓快照")
    return saved


def _save(principal,saved,payload,rows,metrics,status,warnings):
    envelope = ToolEnvelope(status=status,captured_at=saved.envelope.captured_at,data_as_of=saved.envelope.data_as_of,
        snapshot_ref=saved.ref,calculation_version=VERSION,quote_times=saved.envelope.quote_times,
        payload=payload,metrics=metrics,warnings=warnings)
    ref = store.save_result(principal,envelope,rows,kind="risk",parent_ref=saved.ref)
    return store.load_result(principal,ref).envelope


def position_risk(principal,snapshot_ref,include_futures=False):
    saved = _snapshot(principal,snapshot_ref)
    groups={}
    rows=[]
    for original in saved.rows:
        asset=original.get("asset_type")
        if asset!="option" and not (include_futures and asset=="future"):
            continue
        row=dict(original)
        quote=row.get("_quote",{})
        underlying=row.get("underlying_symbol")
        if asset=="future":
            underlying=underlying or f"{row.get('exchange','')}.{row.get('contract','')}"
        key=underlying or f"unmapped:{row.get('contract','')}"
        unit={name:quote.get(name) if _valid(quote.get(name)) else None for name in UNITS}
        if asset=="future":
            unit={"delta":1.,"gamma":0.,"theta":0.,"vega":0.,"rho":0.}
        scale_ok=all(_valid(row.get(name)) for name in ("quantity","contract_multiplier")) and row["contract_multiplier"]>0 and row.get("direction") in {"买","卖"}
        if saved.envelope.payload.get("valuation_basis")=="historical_unavailable" or quote.get("expired"):
            unit={name:None for name in UNITS}
        if scale_ok:
            calculated=calculate_option_position_valuation(open_price=0,valuation_price=0,direction=row["direction"],
                remaining_quantity=row["quantity"],multiplier=row["contract_multiplier"],remaining_open_fee=0,unit_greeks=unit)
            exposures={f"{name}_exposure":calculated[f"{name}_exposure"] for name in UNITS}
        else:
            exposures={f"{name}_exposure":None for name in UNITS}
        row.update(unit_greeks=unit,display_greeks=calculate_option_display_greeks(direction=row.get("direction","买"),unit_greeks=unit),position_exposures=exposures)
        rows.append(row)
        groups.setdefault(key,[]).append(row)
    payload_groups=[]
    all_metrics={}
    for key,items in groups.items():
        metrics={}
        for name,unit in UNITS.items():
            field=f"{name}_exposure"
            values=[Decimal(str(row["position_exposures"][field])) for row in items if row["position_exposures"][field] is not None]
            metrics[field]=MetricValue(value=str(sum(values,Decimal(0))) if values else None,unit=unit,
                status="complete" if len(values)==len(items) else "partial" if values else "unavailable",covered_rows=len(values),eligible_rows=len(items))
        payload_groups.append({"underlying_symbol":key,"currency":"CNY","metrics":{name:value.model_dump() for name,value in metrics.items()}})
        if len(groups)==1:
            all_metrics=metrics
    warnings=list(saved.envelope.warnings)
    warnings.append("Greeks沿用现有计算口径；Black76为模型敏感度，未计提前行权价值，不构成风险评级。")
    status="partial" if not groups or any(metric["status"]!="complete" for group in payload_groups for metric in group["metrics"].values()) else "complete"
    return _save(principal,saved,{"kind":"risk","groups":payload_groups,"position_count":len(rows),"display_units":{"theta":"每日 /360","vega":"每波动率点 /100","rho":"每利率点 /100"}},rows,all_metrics,status,warnings)


def scenario(principal,snapshot_ref,shocks,method="black76_reprice_v1"):
    if method!="black76_reprice_v1" or not 1<=len(shocks)<=5:
        raise ValueError("不支持的情景方法或情景数量")
    shocks=[Shock.model_validate(shock) for shock in shocks]
    saved=_snapshot(principal,snapshot_ref)
    outcomes=[]
    for shock in shocks:
        values=[]
        missing=[]
        eligible=0
        for row in saved.rows:
            quote=row.get("_quote",{})
            underlying=row.get("underlying_symbol")
            if row.get("asset_type")=="future":
                underlying=underlying or f"{row.get('exchange','')}.{row.get('contract','')}"
            if underlying!=shock.underlying_symbol:
                continue
            eligible+=1
            multiplier=row.get("contract_multiplier")
            quantity=row.get("quantity")
            if not _valid(multiplier) or multiplier<=0 or not _valid(quantity) or row.get("direction") not in {"买","卖"}:
                missing.append(row.get("contract"));continue
            scale=quantity*multiplier*(1 if row["direction"]=="买" else -1)
            if row.get("asset_type")=="future":
                price=row.get("valuation_price")
                if not _valid(price) or price<=0 or 1+shock.price_change_pct/100<=0:
                    missing.append(row.get("contract"));continue
                values.append(price*shock.price_change_pct/100*scale);continue
            fields=("underlying_price","strike_price","iv","time_to_expiry","risk_free_rate")
            if saved.envelope.payload.get("valuation_basis") == "historical_unavailable" or quote.get("greeks_method")!="black76" or quote.get("expiry_source")!="provider_expire_datetime" or quote.get("option_class") not in {"CALL","PUT"} or any(not _valid(quote.get(field)) for field in fields):
                missing.append(row.get("contract"));continue
            f,k,iv,t,r=(quote[field] for field in fields)
            shocked_f=f*(1+shock.price_change_pct/100)
            shocked_iv=iv+shock.iv_change_points/100
            shocked_t=t-shock.days_forward/360
            if min(f,k,iv,t,shocked_f,shocked_iv,shocked_t)<=0:
                missing.append(row.get("contract"));continue
            base=_black76_price(f,k,r,iv,t,quote["option_class"])
            shocked=_black76_price(shocked_f,k,r,shocked_iv,shocked_t,quote["option_class"])
            values.append((shocked-base)*scale)
        metric=MetricValue(value=str(round(sum(values),8)) if values else None,unit="CNY",covered_rows=len(values),eligible_rows=eligible,
            status="complete" if values and len(values)==eligible else "partial" if values else "unavailable")
        outcomes.append({"shock":shock.model_dump(),"scenario_pnl_change":metric.model_dump(),"uncovered_contracts":missing})
    valid=any(item["scenario_pnl_change"]["value"] is not None for item in outcomes)
    status="unsupported" if not valid else "partial" if any(item["scenario_pnl_change"]["status"]!="complete" for item in outcomes) else "complete"
    return _save(principal,saved,{"kind":"scenario","method":method,"scenarios":outcomes},[],{},status,
        ["结果为同一Black76模型的情景损益变化，不是账面浮盈亏；未计提前行权价值，缺少有效到期和模型参数的合约不覆盖。"])
