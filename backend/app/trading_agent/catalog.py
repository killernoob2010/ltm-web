"""Business vocabulary used to validate composable queries, never SQL expressions."""

DIMENSIONS = {
    "account": "账户", "contract": "合约", "product": "品种", "exchange": "交易所",
    "asset_type": "期货或期权", "direction": "买卖方向", "trade_date": "交易日",
    "fact_status": "事实确认状态", "assignment_status": "归类状态",
}
METRICS = {
    "positions": {"count": "条", "quantity": "手", "floating_pnl": "CNY"},
    "trades": {"count": "笔", "quantity": "手", "fee": "CNY"},
    "closes": {"count": "笔", "quantity": "手", "realized_close_pnl": "CNY"},
}


def validate_summary(kind, group_by, metrics, order_by=None):
    if kind not in METRICS or not metrics:
        raise ValueError("不支持的事实或指标")
    if any(field not in DIMENSIONS for field in group_by):
        raise ValueError("该属性尚无可查询的可靠来源")
    if any(metric not in METRICS[kind] for metric in metrics):
        raise ValueError("指标不适用于该事实类型")
    if order_by is not None and order_by not in set(group_by) | set(metrics):
        raise ValueError("排序字段必须属于本次汇总")
