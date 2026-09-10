"""Business vocabulary used to validate composable queries, never SQL expressions."""

from .semantic_catalog import DATASET_SPECS, dataset_registry, get_dataset_spec

DIMENSIONS = {
    "account": "账户", "contract": "合约", "product": "品种", "exchange": "交易所",
    "asset_type": "期货或期权", "direction": "买卖方向", "trade_date": "交易日",
    "fact_status": "事实确认状态", "assignment_status": "归类状态",
}

MARKET_DATASETS = {
    "iron_ore_basis": {
        "source": "iron_ore_basis_results",
        "dimensions": ["business_date", "port", "product", "data_status"],
        "metrics": ["basis", "futures_close", "wet_spot_price"],
        "fields": {
            "business_date": {
                "label": "业务日期",
                "unit": None,
                "description": "源结果的业务日期；不是统一行情观察时点。",
            },
            "port": {"label": "港口", "unit": None, "description": "来源目录中的港口。"},
            "product": {"label": "品种", "unit": None, "description": "来源目录中的铁矿石品种。"},
            "basis": {
                "label": "基差",
                "unit": "元/标准化吨",
                "description": "现有规则合同已保存的标准化现货价与期货收盘价差值；Agent 不重新计算。",
            },
            "futures_close": {
                "label": "期货收盘价",
                "unit": "元/吨",
                "description": "源结果中的 I0 主力连续期货收盘价；不与湿吨现货价混用。",
            },
            "wet_spot_price": {
                "label": "湿吨现货价",
                "unit": "元/湿吨",
                "description": "源结果中的湿吨现货价；不与标准化吨基差混用。",
            },
            "data_status": {
                "label": "数据状态",
                "unit": None,
                "description": "沿用源表状态；非有效行保留为异常或缺失，不当作有效点。",
            },
            "futures_series": {"label": "期货序列", "unit": None, "description": "源结果中的期货序列。"},
            "business_year": {"label": "业务年份", "unit": None, "description": "源结果中的业务年份。"},
            "business_week": {"label": "业务周次", "unit": None, "description": "源结果中的业务周次。"},
            "week_label": {"label": "周次标签", "unit": None, "description": "源结果中的周次标签。"},
            "rule_version": {"label": "规则版本", "unit": None, "description": "生成该行结果时记录的规则版本。"},
            "parameter_version": {"label": "参数版本", "unit": None, "description": "生成该行结果时记录的参数版本。"},
            "source_workbook_name": {"label": "来源文件", "unit": None, "description": "源结果记录的来源文件名。"},
            "source_workbook_sha256": {"label": "来源文件哈希", "unit": None, "description": "源结果记录的来源文件 SHA-256。"},
        },
    },
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
