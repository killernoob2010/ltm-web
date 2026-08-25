"""现货业务台账的字段契约、规则服务、持久化和 HTTP API。

真实销售合同来源不会在本模块里猜测请求或响应结构。同步适配器只负责把已确认的
标准字段交给 ``normalize_sales_contract_record``；本模块因此可以在本地 fixture 中
完整验证业务规则，同时保持真实无人值守认证仍为上线前阻塞。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import io
import json
import os
import re
from typing import Any, Iterable, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from pydantic import BaseModel, Field

from . import db
from .permissions import can, is_admin, require_permission


router = APIRouter()
SPOT_LEDGER_MODULE = "spot_ledger"
SPOT_LEDGER_RESOURCE = "spot_ledger.records"
SPOT_LEDGER_EXPORT_RESOURCE = "spot_ledger.export"
SHANGHAI_GROUPS = ("大客户组", "东北组", "山东组", "黄骅组", "天津组", "唐山组", "南方组")
LAND_SALES_TYPE = "船货-落地"
SALES_TYPE_MAP = {
    "B07": "现货-市场加价",
    "B06": "现货-背对背",
    "B09": LAND_SALES_TYPE,
    "B05": LAND_SALES_TYPE,
    "贸易-港口现货-市场加价-B07": "现货-市场加价",
    "贸易-港口现货-背对背-B06": "现货-背对背",
    "贸易-代理落地-B09": LAND_SALES_TYPE,
    "贸易-落地-固定价-B05": LAND_SALES_TYPE,
}
PLACEHOLDER_VALUES = {"--", "***", "---", "**", "****", "—", "——"}
NUMERIC_FIELDS = {"L", "M", "N", "O", "X", "Y", "Z", "AA", "AH", "AI", "AJ", "AK", "AL"}
MANUAL_FIELDS = {
    "C", "N", "O", "P", "R", "V", "W", "Y", "AA", "AC", "AE", "AH", "AI", "AJ", "AK", "AL", "AM", "AN", "AO",
    "long_contract_object",
}
SYSTEM_PRIORITY_FIELDS = {"K"}
TECHNICAL_FIELDS = (
    "source_detail_id", "source_closed_state", "record_source_type", "is_active", "supplement_status", "missing_fields",
    "sync_status", "last_synced_at", "sync_error_summary", "strategic_hedging_id",
)


def _field(code: str, name: str, control: str, source_rule: str, required_rule: str = "") -> dict[str, Any]:
    return {
        "code": code,
        "name": name,
        "control": control,
        "source_rule": source_rule,
        "required_rule": required_rule,
        "exportable": True,
    }


FIELD_DEFINITIONS = [
    _field("A", "数据来源", "计算", "业务台账-销售组别"),
    _field("B", "序列", "计算", "同销售组当前最大序列加一，不重排历史"),
    _field("C", "建仓", "手工/必填", "人工录入", "自主建仓或非自主建仓"),
    _field("D", "销售类型", "系统映射", "销售合同业务类别编码 B07/B06/B09/B05"),
    _field("E", "销售组别", "系统", "量归属组"),
    _field("F", "操作抬头", "系统转换", "源系统名称经显式字典转换"),
    _field("G", "采购日期", "系统", "资源日期"),
    _field("H", "商品名称", "系统", "销售合同商品明细"),
    _field("I", "港口", "系统", "销售合同商品明细"),
    _field("J", "模式", "系统", "销售合同商品明细"),
    _field("K", "船名", "系统优先补录/必填", "源船名；源无有效值时允许补录", "有效船名"),
    _field("L", "数量（吨）", "系统计算", "结案且结算数量有效取结算数量，否则取合同数量"),
    _field("M", "采购价格（元/吨）", "系统", "销售合同采购价格"),
    _field("N", "实物货物成本含税单价（锁汇）", "手工/必填", "人工录入", "非负数"),
    _field("O", "实物资金成本含税单价", "手工/必填", "人工录入", "数值，0和负数有效"),
    _field("P", "是否长协", "条件必填", "船货-落地人工录入", "船货-落地时必填是/否"),
    _field("Q", "供应商", "系统转换", "源供应商经显式字典转换"),
    _field("R", "付款条件", "手工", "人工录入"),
    _field("S", "采购业务", "系统", "销售合同采购业务"),
    _field("T", "采购执行", "系统", "销售合同采购执行"),
    _field("U", "销售日期", "系统", "销售合同签订日期"),
    _field("V", "客户性质", "手工", "人工录入：其他钢厂/贸易商/子公司"),
    _field("W", "第一次合作钢厂", "手工", "人工录入"),
    _field("X", "销售数量（吨）", "系统计算", "结案且结算数量有效取结算数量，否则取合同数量"),
    _field("Y", "含利息销售价格（元/吨）", "手工/必填", "人工录入", "非负数"),
    _field("Z", "销售价格（元/吨）", "系统", "销售合同销售价格"),
    _field("AA", "实物资金收入含税单价", "手工", "人工录入"),
    _field("AB", "合同签约客户名称", "系统", "销售合同签约客户"),
    _field("AC", "最终客户名称", "手工", "人工录入"),
    _field("AD", "销售合同号", "系统", "销售合同号"),
    _field("AE", "收款条件", "手工", "人工录入"),
    _field("AF", "销售业务", "系统", "销售合同销售业务"),
    _field("AG", "销售执行", "系统转换", "去除明确的数字前缀"),
    _field("AH", "实物含税盈亏（万元）", "手工", "人工录入"),
    _field("AI", "实物不含税盈亏（万元）", "手工", "人工录入"),
    _field("AJ", "期货量", "手工", "人工录入"),
    _field("AK", "期货不含税利润（万元）", "手工", "人工录入"),
    _field("AL", "合计不含税利润（万元）", "手工", "人工录入"),
    _field("AM", "备注", "手工", "人工录入"),
    _field("AN", "利润跨月因素", "手工", "人工录入"),
    _field("AO", "跨月利润确定日期", "手工", "人工录入"),
    _field("AP", "利润组别", "系统", "业务毛利归属组"),
    _field("AQ", "是否跨组业务", "计算", "E 与 AP 不一致时为是"),
    _field("AR", "上表项目", "计算", "源台账项目或校验计算"),
    _field("AS", "销售月份", "计算", "销售日期所在月首日"),
    _field("AT", "利润月份", "计算", "跨月利润确定日期所在月，否则销售月份"),
    _field("AU", "商品分类", "计算/字典", "商品分类字典，未命中保留原值并提示"),
    _field("AV", "利润合计校验", "计算校验", "人工利润字段的校验结果"),
    _field("AW", "利润日期校验", "计算校验", "利润月份与跨月日期校验"),
    _field("AX", "业务类型校验", "计算校验", "销售类型编码与标准类型校验"),
    _field("AY", "客户性质校验", "计算校验", "客户性质值域校验"),
]
FIELD_CODES = tuple(item["code"] for item in FIELD_DEFINITIONS)
FIELD_BY_CODE = {item["code"]: item for item in FIELD_DEFINITIONS}
FIELD_NAME_TO_CODE = {item["name"]: item["code"] for item in FIELD_DEFINITIONS}

# 这些是标准 source contract 中已经确认的名称映射。未知名称不丢失，保留原值并报错。
DEFAULT_NAME_MAPPINGS = {
    "operation_title": {"操作抬头A": "操作抬头A", "公司A": "公司A", "": ""},
    "supplier": {"供应商A": "供应商A", "供应商B": "供应商B", "": ""},
    "product_category": {"铁矿石": "铁矿石", "螺纹钢": "钢材", "焦煤": "煤炭", "": ""},
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _value(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in raw:
            return raw[key]
    return None


def _normalize_date(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = _text(value).replace("/", "-").replace("年", "-").replace("月", "-").replace("日", "")
    text = text.split("T", 1)[0].split(" ", 1)[0]
    match = re.match(r"^(\d{4}-\d{1,2})(?:-(\d{1,2}))?$", text)
    if not match:
        return text
    year_month, day_part = match.groups()
    if day_part is None:
        return f"{year_month}-01"
    year, month = year_month.split("-")
    return f"{year}-{int(month):02d}-{int(day_part):02d}"


def _month_start(value: Any) -> str:
    normalized = _normalize_date(value)
    return f"{normalized[:7]}-01" if re.match(r"^\d{4}-\d{2}-\d{2}$", normalized) else ""


def _number(value: Any) -> Optional[float | int]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    text = _text(value).replace(",", "")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def _is_true(value: Any) -> bool:
    return value is True or _text(value).lower() in {"1", "true", "yes", "是", "已结案", "结案", "closed"}


def _valid_source_value(value: Any) -> bool:
    return value is not None and _text(value) not in {""} and _text(value) not in PLACEHOLDER_VALUES


def _normalized_vessel(value: Any) -> str:
    text = _text(value)
    return "" if text in PLACEHOLDER_VALUES else text


def _mapped_value(
    raw_value: Any,
    mapping_name: str,
    mappings: dict[str, dict[str, str]],
    errors: list[dict[str, str]],
    field: str,
) -> str:
    text = _text(raw_value)
    if not text:
        return ""
    mapping = mappings.get(mapping_name, DEFAULT_NAME_MAPPINGS.get(mapping_name, {}))
    if text in mapping:
        return mapping[text]
    errors.append({"type": "conversion_mapping", "field": field, "message": f"{mapping_name} 未配置名称映射: {text}"})
    return text


def _business_type(raw_value: Any, errors: list[dict[str, str]]) -> str:
    code = _text(raw_value)
    if code in SALES_TYPE_MAP:
        return SALES_TYPE_MAP[code]
    if code in SALES_TYPE_MAP.values():
        return code
    if code:
        errors.append({"type": "conversion_mapping", "field": "D", "message": f"未知销售类型: {code}"})
    return code


def _quantity(raw: dict[str, Any]) -> Optional[float | int]:
    contract_quantity = _number(_value(raw, "contract_quantity", "quantity"))
    settlement_quantity = _number(_value(raw, "settlement_quantity"))
    if _is_true(_value(raw, "is_closed", "closed")) and settlement_quantity is not None:
        return settlement_quantity
    return contract_quantity


def calculate_derived_fields(record: dict[str, Any]) -> dict[str, Any]:
    """Recompute only deterministic fields, keeping manual values untouched."""
    result = dict(record)
    group = _text(result.get("E"))
    profit_group = _text(result.get("AP"))
    result["A"] = f"业务台账-{group}" if group else _text(result.get("A"))
    if group and profit_group:
        result["AQ"] = "是" if group != profit_group else "否"
    else:
        result["AQ"] = ""
    result["AS"] = _month_start(result.get("U"))
    result["AT"] = _month_start(result.get("AO")) or result["AS"]
    if result.get("D") == LAND_SALES_TYPE and not result.get("P"):
        result["P"] = ""
    return result


def normalize_sales_contract_record(raw: dict[str, Any], mappings: Optional[dict[str, dict[str, str]]] = None) -> dict[str, Any]:
    """Normalize one already-standardized sales-contract detail into A:AY.

    ``raw`` is the internal standard source contract, not an external response shape. The
    external adapter must supply explicit mappings before this function is called.
    """
    raw = dict(raw or {})
    mappings = mappings or {}
    errors: list[dict[str, str]] = []
    detail_id = _text(_value(raw, "detail_id", "source_detail_id"))
    if not detail_id:
        errors.append({"type": "missing_detail_id", "field": "source_detail_id", "message": "销售合同商品明细 ID 为空"})

    sales_type = _business_type(_value(raw, "business_category_code", "sales_type"), errors)
    quantity_group = _text(_value(raw, "quantity_group", "sales_group"))
    profit_group = _text(_value(raw, "profit_group", "profit_group_name"))
    if quantity_group and quantity_group not in SHANGHAI_GROUPS:
        errors.append({"type": "group_scope", "field": "E", "message": f"不在 7 个销售组范围内: {quantity_group}"})
    if profit_group and profit_group not in SHANGHAI_GROUPS:
        errors.append({"type": "group_scope", "field": "AP", "message": f"利润组不在标准组范围内: {profit_group}"})

    signed_date = _normalize_date(_value(raw, "signed_date", "sales_date"))
    purchase_date = _normalize_date(_value(raw, "resource_date", "purchase_date"))
    source_closed_state = "已结案" if _is_true(_value(raw, "is_closed", "closed")) else "未结案"
    quantity = _quantity(raw)
    category_raw = _text(_value(raw, "product_category", "product_name"))
    category_mapping = mappings.get("product_category", DEFAULT_NAME_MAPPINGS["product_category"])
    category = category_mapping.get(category_raw, category_raw)
    if category_raw and category_raw not in category_mapping:
        errors.append({"type": "category_mapping", "field": "AU", "message": f"商品分类未配置: {category_raw}"})

    record: dict[str, Any] = {code: "" for code in FIELD_CODES}
    record.update({
        "A": f"业务台账-{quantity_group}" if quantity_group else "",
        "B": _value(raw, "sequence", "B") or "",
        "C": _text(_value(raw, "building_position", "C")),
        "D": sales_type,
        "E": quantity_group,
        "F": _mapped_value(_value(raw, "operation_title", "F"), "operation_title", mappings, errors, "F"),
        "G": purchase_date,
        "H": _text(_value(raw, "product_name", "H")),
        "I": _text(_value(raw, "port", "I")),
        "J": _text(_value(raw, "mode", "J")),
        "K": _normalized_vessel(_value(raw, "vessel_name", "K")),
        "L": quantity,
        "M": _number(_value(raw, "purchase_price", "M")),
        "N": _number(_value(raw, "cargo_cost_price", "N")),
        "O": _number(_value(raw, "funding_cost_price", "O")),
        "P": _text(_value(raw, "is_long_contract", "P")) if sales_type == LAND_SALES_TYPE else "",
        "Q": _mapped_value(_value(raw, "supplier", "Q"), "supplier", mappings, errors, "Q"),
        "R": _text(_value(raw, "payment_condition", "R")),
        "S": _text(_value(raw, "purchase_business", "S")),
        "T": _text(_value(raw, "purchase_execution", "T")),
        "U": signed_date,
        "V": _text(_value(raw, "customer_nature", "V")),
        "W": _text(_value(raw, "first_cooperating_steel_mill", "W")),
        "X": quantity,
        "Y": _number(_value(raw, "sales_price_with_interest", "Y")),
        "Z": _number(_value(raw, "sales_price", "Z")),
        "AA": _number(_value(raw, "physical_fund_income_price", "AA")),
        "AB": _text(_value(raw, "demander", "contract_customer", "AB")),
        "AC": _text(_value(raw, "final_customer", "AC")),
        "AD": _text(_value(raw, "contract_number", "AD")),
        "AE": _text(_value(raw, "collection_condition", "AE")),
        "AF": _text(_value(raw, "sales_business", "AF")),
        "AG": re.sub(r"^\d+_", "", _text(_value(raw, "sales_execution", "AG"))),
        "AH": _number(_value(raw, "physical_tax_profit", "AH")),
        "AI": _number(_value(raw, "physical_non_tax_profit", "AI")),
        "AJ": _number(_value(raw, "futures_quantity", "AJ")),
        "AK": _number(_value(raw, "futures_non_tax_profit", "AK")),
        "AL": _number(_value(raw, "total_non_tax_profit", "AL")),
        "AM": _text(_value(raw, "remark", "AM")),
        "AN": _text(_value(raw, "profit_cross_month_factor", "AN")),
        "AO": _normalize_date(_value(raw, "profit_determination_date", "AO")),
        "AP": profit_group,
        "AR": _text(_value(raw, "upper_table_item", "AR")),
        "AU": category,
        "AV": _text(_value(raw, "profit_total_check", "AV")),
        "AW": _text(_value(raw, "profit_date_check", "AW")),
        "AX": _text(_value(raw, "business_type_check", "AX")),
        "AY": _text(_value(raw, "customer_nature_check", "AY")),
        "long_contract_object": _text(_value(raw, "long_contract_object")),
        "source_detail_id": detail_id,
        "source_closed_state": source_closed_state,
        "eligible": _text(_value(raw, "spot_type", "trade_type")) == "现货"
        and _text(_value(raw, "contract_status", "status")) == "生效"
        and quantity_group in SHANGHAI_GROUPS,
        "source_spot_type": _text(_value(raw, "spot_type", "trade_type")),
        "source_contract_status": _text(_value(raw, "contract_status", "status")),
        "sync_errors": errors,
    })
    return calculate_derived_fields(record)


def missing_required_fields(record: dict[str, Any]) -> list[str]:
    if _text(record.get("record_source_type")) == "战略套保":
        return []
    missing: list[str] = []
    required = (("C", "建仓"), ("K", "船名"), ("N", "实物货物成本含税单价（锁汇）"), ("O", "实物资金成本含税单价"), ("Y", "含利息销售价格（元/吨）"))
    for code, name in required:
        if record.get(code) is None or _text(record.get(code)) == "":
            missing.append(name)
    if _text(record.get("D")) == LAND_SALES_TYPE:
        if _text(record.get("P")) not in {"是", "否"}:
            missing.append("是否长协")
        elif _text(record.get("P")) == "是" and _text(record.get("long_contract_object")) == "":
            missing.append("长协对象")
    return missing


def validate_record_values(record: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if _text(record.get("record_source_type")) == "战略套保":
        return errors
    if _text(record.get("C")) and _text(record.get("C")) not in {"自主建仓", "非自主建仓"}:
        errors["C"] = "建仓只能选择自主建仓或非自主建仓"
    for code in ("N", "Y"):
        value = _number(record.get(code))
        if value is not None and value < 0:
            errors[code] = "该字段不能为负数"
    if _text(record.get("D")) == LAND_SALES_TYPE and _text(record.get("P")) not in {"", "是", "否"}:
        errors["P"] = "是否长协只能选择是或否"
    if _text(record.get("V")) and _text(record.get("V")) not in {"其他钢厂", "贸易商", "子公司"}:
        errors["V"] = "客户性质不在允许值域"
    if record.get("O") not in (None, "") and _number(record.get("O")) is None:
        errors["O"] = "实物资金成本含税单价必须是数字"
    return errors


def _quoted(code: str) -> str:
    return f'"{code}"'


def initialize_schema(conn) -> None:
    """Create the ledger tables idempotently for SQLite and PostgreSQL."""
    field_sql = ",\n".join(f"        {_quoted(code)} {'DOUBLE PRECISION' if code in NUMERIC_FIELDS else 'TEXT'}" for code in FIELD_CODES)
    cur = conn.cursor()
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS spot_ledger_records (
            record_id TEXT PRIMARY KEY,
            source_detail_id TEXT UNIQUE,
            source_closed_state TEXT NOT NULL DEFAULT '未结案',
            record_source_type TEXT NOT NULL DEFAULT '现货同步',
{field_sql},
            long_contract_object TEXT,
            eligible INTEGER NOT NULL DEFAULT 1,
            is_active INTEGER NOT NULL DEFAULT 1,
            supplement_status TEXT NOT NULL DEFAULT '待补录',
            missing_fields TEXT NOT NULL DEFAULT '[]',
            sync_status TEXT NOT NULL DEFAULT '正常',
            last_synced_at TEXT,
            sync_error_summary TEXT,
            source_payload_json TEXT,
            source_mode TEXT NOT NULL DEFAULT 'fixture',
            strategic_hedging_id TEXT,
            strategic_group TEXT,
            strategic_account TEXT,
            strategic_contract TEXT,
            strategic_open_direction TEXT,
            strategic_opened_at TEXT,
            strategic_open_quantity DOUBLE PRECISION,
            strategic_quantity_unit TEXT,
            strategic_open_price DOUBLE PRECISION,
            strategic_price_currency TEXT,
            strategic_closed_at TEXT,
            strategic_close_quantity DOUBLE PRECISION,
            strategic_close_price DOUBLE PRECISION,
            strategic_spot_record_id TEXT,
            strategic_remark TEXT,
            strategic_status TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    if db._is_pg():
        cur.execute("ALTER TABLE spot_ledger_records ADD COLUMN IF NOT EXISTS source_closed_state TEXT NOT NULL DEFAULT '未结案'")
    else:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(spot_ledger_records)").fetchall()}
        if "source_closed_state" not in columns:
            conn.execute("ALTER TABLE spot_ledger_records ADD COLUMN source_closed_state TEXT NOT NULL DEFAULT '未结案'")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS spot_ledger_sync_runs (
            id TEXT PRIMARY KEY,
            slot_key TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            source_mode TEXT NOT NULL DEFAULT 'fixture',
            page_count INTEGER NOT NULL DEFAULT 0,
            expected_page_count INTEGER,
            total_count INTEGER NOT NULL DEFAULT 0,
            inserted_count INTEGER NOT NULL DEFAULT 0,
            updated_count INTEGER NOT NULL DEFAULT 0,
            hidden_count INTEGER NOT NULL DEFAULT 0,
            error_count INTEGER NOT NULL DEFAULT 0,
            error_summary TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_spot_ledger_active ON spot_ledger_records(is_active, record_source_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_spot_ledger_contract ON spot_ledger_records(\"AD\")")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_spot_ledger_dates ON spot_ledger_records(\"U\", \"E\", \"AP\")")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_spot_ledger_sync_runs_slot ON spot_ledger_sync_runs(slot_key, started_at)")
    if db._is_pg():
        cur.execute("ALTER TABLE spot_ledger_records ENABLE ROW LEVEL SECURITY")
        cur.execute("ALTER TABLE spot_ledger_sync_runs ENABLE ROW LEVEL SECURITY")
        cur.execute("REVOKE ALL ON TABLE spot_ledger_records, spot_ledger_sync_runs FROM anon, authenticated")


def sync_spot_ledger_permissions(cur) -> None:
    """Add missing module permissions without overwriting administrator choices."""
    users = db._exec(cur, "SELECT id, department, role FROM users").fetchall()
    for user in users:
        role = user["role"]
        department = user["department"]
        if role in {"管理员", "admin"}:
            permission = (1, 1, 1)
        elif role == "领导":
            permission = (1, 0, 0)
        elif department in {"贸易处", "管理部门"}:
            permission = (1, 1, 0)
        else:
            continue
        db._exec(
            cur,
            "INSERT OR IGNORE INTO module_permissions (user_id, module_code, can_view, can_edit, can_sensitive) VALUES (?, ?, ?, ?, ?)",
            (user["id"], SPOT_LEDGER_MODULE, *permission),
        )


def record_to_public(record: dict[str, Any]) -> dict[str, Any]:
    result = dict(record)
    for field in ("missing_fields", "sync_error_summary", "source_payload_json"):
        value = result.get(field)
        if field == "missing_fields":
            result[field] = json.loads(value or "[]") if isinstance(value, str) else (value or [])
        elif field == "sync_error_summary":
            result[field] = json.loads(value or "[]") if isinstance(value, str) and value.startswith("[") else (value or "")
        elif field == "source_payload_json":
            result[field] = json.loads(value or "{}") if isinstance(value, str) else (value or {})
    return result


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row else {}


def _serialize_record(record: dict[str, Any]) -> dict[str, Any]:
    result = dict(record)
    result["missing_fields"] = missing_required_fields(result)
    result["supplement_status"] = "待补录" if result["missing_fields"] else "已完成"
    result["sync_status"] = "异常" if result.get("sync_errors") else "正常"
    return result


def _default_user_for_tests() -> dict[str, Any]:
    return {"id": 0, "role": "管理员", "name": "管理员", "department": "管理部门"}


def _get_user(user: Optional[dict[str, Any]]) -> dict[str, Any]:
    return user or _default_user_for_tests()


def _request_user(request: Request) -> dict[str, Any]:
    authorization = request.headers.get("authorization", "")
    token = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return dict(user)


def _record_query_conditions(params: dict[str, Any], include_inactive: bool = False) -> tuple[list[str], list[Any]]:
    conditions = ["record_source_type = '现货同步'"]
    values: list[Any] = []
    if not include_inactive:
        conditions.append("is_active = 1")
    mapping = {
        "sales_group": "\"E\"", "profit_group": "\"AP\"", "sales_type": "\"D\"",
        "product_name": "\"H\"", "port": "\"I\"", "operation_title": "\"F\"",
        "supplier": "\"Q\"", "customer": "\"AB\"", "contract_number": "\"AD\"",
        "purchase_execution": "\"T\"", "sales_execution": "\"AG\"",
    }
    for key, column in mapping.items():
        value = _text(params.get(key))
        if value:
            conditions.append(f"{column} LIKE ?")
            values.append(f"%{value}%")
    closed_state = _text(params.get("closed_state"))
    if closed_state:
        conditions.append("source_closed_state = ?")
        values.append("已结案" if closed_state in {"已结案", "结案", "是", "1", "true"} else "未结案")
    for key, column in (("from_date", '"U"'), ("to_date", '"U"')):
        value = _normalize_date(params.get(key))
        if value:
            conditions.append(f"{column} {'>=' if key == 'from_date' else '<='} ?")
            values.append(value)
    for key, column in (("purchase_quantity", '"L"'), ("sales_quantity", '"X"')):
        value = _text(params.get(key))
        if value:
            conditions.append(f"{column} = ?")
            values.append(_number(value))
    supplement = _text(params.get("supplement_status"))
    if supplement:
        conditions.append("supplement_status = ?")
        values.append(supplement)
    if params.get("sync_error") in (True, "true", "1", "是"):
        conditions.append("sync_status = '异常'")
    return conditions, values


def list_records(params: Optional[dict[str, Any]] = None, *, user: Optional[dict[str, Any]] = None, include_inactive: bool = False) -> list[dict[str, Any]]:
    params = params or {}
    require_permission(_get_user(user), SPOT_LEDGER_RESOURCE, "view")
    conditions, values = _record_query_conditions(params, include_inactive=include_inactive)
    limit_value = params.get("limit")
    offset_value = params.get("offset")
    limit = max(1, min(int(limit_value) if isinstance(limit_value, (int, str)) and str(limit_value).isdigit() else 500, 5000))
    offset = max(0, int(offset_value) if isinstance(offset_value, (int, str)) and str(offset_value).isdigit() else 0)
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(
            cur,
            f"SELECT * FROM spot_ledger_records WHERE {' AND '.join(conditions)} ORDER BY \"U\" DESC, record_id LIMIT ? OFFSET ?",
            (*values, limit, offset),
        ).fetchall()
    return [record_to_public(_row_dict(row)) for row in rows]


@router.get("/spot-ledger/field-definitions")
def field_definitions(user=Depends(_request_user)):
    require_permission(_get_user(user), SPOT_LEDGER_RESOURCE, "view")
    return {"fields": FIELD_DEFINITIONS, "technical_fields": list(TECHNICAL_FIELDS)}


@router.get("/spot-ledger/records")
def get_records(
    from_date: str = "", to_date: str = "", sales_group: str = "", profit_group: str = "", sales_type: str = "",
    product_name: str = "", port: str = "", operation_title: str = "", supplier: str = "", customer: str = "",
    contract_number: str = "", purchase_execution: str = "", sales_execution: str = "", purchase_quantity: str = "",
    sales_quantity: str = "", closed_state: str = "", supplement_status: str = "", sync_error: str = "",
    limit: int = Query(500, ge=1, le=5000), offset: int = Query(0, ge=0), user=Depends(_request_user),
):
    params = locals().copy()
    return {"records": list_records(params, user=user), "field_definitions": FIELD_DEFINITIONS}


@router.get("/spot-ledger/records/{record_id}")
def get_record(record_id: str, user=Depends(_request_user)):
    require_permission(_get_user(user), SPOT_LEDGER_RESOURCE, "view")
    with db.connect() as conn:
        row = db._exec(conn.cursor(), "SELECT * FROM spot_ledger_records WHERE record_id = ?", (record_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="台账记录不存在")
    return {"record": record_to_public(_row_dict(row)), "fields": FIELD_DEFINITIONS}


class SpotLedgerPatch(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


@router.patch("/spot-ledger/records/{record_id}")
def patch_record(record_id: str, payload: SpotLedgerPatch, user=Depends(_request_user)):
    active_user = _get_user(user)
    require_permission(active_user, SPOT_LEDGER_RESOURCE, "edit")
    require_permission(active_user, SPOT_LEDGER_RESOURCE, "manage")
    values = payload.values or {}
    allowed = MANUAL_FIELDS | SYSTEM_PRIORITY_FIELDS
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise HTTPException(status_code=400, detail={"message": "包含不可编辑的系统字段", "fields": unknown})
    if not values:
        raise HTTPException(status_code=400, detail="没有可保存的人工字段")
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(cur, "SELECT * FROM spot_ledger_records WHERE record_id = ?", (record_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="台账记录不存在")
        current = _row_dict(row)
        projected = dict(current)
        for field, value in values.items():
            if field in NUMERIC_FIELDS:
                converted = _number(value)
                if value not in (None, "") and converted is None:
                    raise HTTPException(status_code=400, detail={"message": f"字段 {field} 必须为数字", "field": field})
                projected[field] = converted
            elif field == "AO":
                projected[field] = _normalize_date(value)
            else:
                projected[field] = value if value is not None else ""
        value_errors = validate_record_values(projected)
        if value_errors:
            raise HTTPException(status_code=400, detail={"message": "人工字段格式不合法", "errors": value_errors})
        projected = calculate_derived_fields(projected)
        missing = missing_required_fields(projected)
        assignments = []
        params: list[Any] = []
        for field in values:
            column = f'"{field}"' if field in FIELD_CODES else field
            assignments.append(f"{column} = ?")
            params.append(projected[field])
        assignments.extend(["missing_fields = ?", "supplement_status = ?", "updated_at = ?"])
        params.extend([json.dumps(missing, ensure_ascii=False), "待补录" if missing else "已完成", datetime.now().isoformat(timespec="seconds")])
        params.append(record_id)
        db._exec(cur, f"UPDATE spot_ledger_records SET {', '.join(assignments)} WHERE record_id = ?", tuple(params))
        saved = db._exec(cur, "SELECT * FROM spot_ledger_records WHERE record_id = ?", (record_id,)).fetchone()
    return {"record": record_to_public(_row_dict(saved)), "missing_fields": missing}


def get_pending(*, user: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    records = list_records({"supplement_status": "待补录"}, user=user)
    return {"records": records, "count": len(records)}


def get_sync_errors(*, user: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    records = list_records({"sync_error": "true"}, user=user)
    try:
        from .spot_ledger_sync import get_sync_runs

        runs = get_sync_runs()
    except Exception:
        runs = []
    return {"records": records, "runs": runs, "count": len(records)}


@router.get("/spot-ledger/pending")
def pending_view(user=Depends(_request_user)):
    return get_pending(user=user)


@router.get("/spot-ledger/sync-errors")
def sync_errors_view(user=Depends(_request_user)):
    return get_sync_errors(user=user)


@router.get("/spot-ledger/sync-status")
def sync_status_view(user=Depends(_request_user)):
    require_permission(_get_user(user), SPOT_LEDGER_RESOURCE, "view")
    try:
        from .spot_ledger_sync import get_sync_runs

        runs = get_sync_runs()
    except Exception:
        runs = []
    return {
        "enabled": (os.getenv("SPOT_LEDGER_AUTO_SYNC_ENABLED") or "").strip().lower() == "true",
        "source_mode": (os.getenv("SPOT_LEDGER_SOURCE_MODE") or "profiled_http").strip(),
        "slots": [f"{hour:02d}:00" for hour in range(9, 19)],
        "runs": runs,
    }


@router.post("/spot-ledger/source-readiness")
def source_readiness_view(user=Depends(_request_user)):
    active_user = _get_user(user)
    if not is_admin(active_user):
        raise HTTPException(status_code=403, detail="仅管理员可检查真实源")

    from .spot_ledger_sync import ProfiledSalesContractSource, SalesContractSourceError

    try:
        scan = ProfiledSalesContractSource.from_env().fetch_full_scan()
    except SalesContractSourceError as exc:
        detail = {"code": exc.code, "stage": exc.stage}
        if exc.http_status is not None:
            detail["http_status"] = exc.http_status
        raise HTTPException(status_code=503, detail=detail) from None
    except Exception:
        raise HTTPException(status_code=503, detail={"code": "source_probe_failed"}) from None
    return {
        "ok": bool(scan.complete and not scan.errors),
        "source_mode": scan.source_mode,
        "complete": scan.complete,
        "page_count": scan.page_count,
        "expected_page_count": scan.expected_page_count,
        "total_count": scan.total_count,
        "eligible_count": len(scan.records),
        "error_count": len(scan.errors),
    }


class StrategicHedgingIn(BaseModel):
    group_name: str = Field(min_length=1)
    account: str = Field(min_length=1)
    contract: str = Field(min_length=1)
    open_direction: str = Field(min_length=1)
    opened_at: str = Field(min_length=1)
    open_quantity: float = Field(gt=0)
    quantity_unit: str = Field(min_length=1)
    open_price: float
    price_currency: str = Field(min_length=1)
    closed_at: Optional[str] = None
    close_quantity: Optional[float] = Field(default=None, gt=0)
    close_price: Optional[float] = None
    spot_record_id: Optional[str] = None
    remark: str = ""


def _execute_insert(cur, sql: str, params: tuple[Any, ...]):
    if db._is_pg():
        sql = sql.replace("?", "%s")
    cur.execute(sql, params)


@router.post("/spot-ledger/strategic-hedging")
def create_strategic_hedging(payload: StrategicHedgingIn, user=Depends(_request_user)):
    active_user = _get_user(user)
    require_permission(active_user, SPOT_LEDGER_RESOURCE, "edit")
    require_permission(active_user, SPOT_LEDGER_RESOURCE, "manage")
    has_close = any(value not in (None, "") for value in (payload.closed_at, payload.close_quantity, payload.close_price))
    if has_close and not all(value not in (None, "") for value in (payload.closed_at, payload.close_quantity, payload.close_price)):
        raise HTTPException(status_code=400, detail="平仓日期、平仓数量、平仓价格必须同时填写")
    if has_close and payload.close_quantity != payload.open_quantity:
        raise HTTPException(status_code=400, detail="当前仅支持全开全平，不支持部分平仓")
    status = "已平仓" if has_close else "未平仓"
    record_id = f"strategy:{uuid4().hex}"
    fields = {code: "" for code in FIELD_CODES}
    fields.update({"A": "战略套保", "E": payload.group_name, "AP": payload.group_name})
    timestamp = datetime.now().isoformat(timespec="seconds")
    columns = [
        "record_id", "source_detail_id", "record_source_type", *FIELD_CODES, "long_contract_object", "eligible", "is_active",
        "supplement_status", "missing_fields", "sync_status", "last_synced_at", "sync_error_summary", "source_payload_json",
        "source_mode", "strategic_hedging_id", "strategic_group", "strategic_account", "strategic_contract", "strategic_open_direction",
        "strategic_opened_at", "strategic_open_quantity", "strategic_quantity_unit", "strategic_open_price", "strategic_price_currency",
        "strategic_closed_at", "strategic_close_quantity", "strategic_close_price", "strategic_spot_record_id", "strategic_remark", "strategic_status",
    ]
    values = [
        record_id, None, "战略套保", *[fields[code] for code in FIELD_CODES], "", 1, 1, "已完成", "[]", "正常", "", "", "{}", "manual",
        record_id, payload.group_name, payload.account, payload.contract, payload.open_direction, payload.opened_at,
        payload.open_quantity, payload.quantity_unit, payload.open_price, payload.price_currency, payload.closed_at,
        payload.close_quantity, payload.close_price, payload.spot_record_id, payload.remark, status,
    ]
    quoted_columns = [f'"{column}"' if column in FIELD_CODES else column for column in columns]
    with db.connect() as conn:
        initialize_schema(conn)
        _execute_insert(conn.cursor(), f"INSERT INTO spot_ledger_records ({', '.join(quoted_columns)}) VALUES ({', '.join('?' for _ in columns)})", tuple(values))
        row = db._exec(conn.cursor(), "SELECT * FROM spot_ledger_records WHERE record_id = ?", (record_id,)).fetchone()
    return {"record": record_to_public(_row_dict(row))}


def _export_workbook(records: Iterable[dict[str, Any]], include_technical_key: bool = False) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    headers = [item["name"] for item in FIELD_DEFINITIONS]
    keys = list(FIELD_CODES)
    if include_technical_key:
        headers.append("销售合同商品明细 ID")
        keys.append("source_detail_id")
    sheet.append(headers)
    for record in records:
        sheet.append([record.get(key, "") for key in keys])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


@router.get("/spot-ledger/export")
def export_records(
    include_technical_key: bool = False, from_date: str = "", to_date: str = "", sales_group: str = "", profit_group: str = "",
    sales_type: str = "", product_name: str = "", port: str = "", operation_title: str = "", supplier: str = "", customer: str = "",
    contract_number: str = "", purchase_execution: str = "", sales_execution: str = "", purchase_quantity: str = "", sales_quantity: str = "",
    closed_state: str = "", supplement_status: str = "", sync_error: str = "", user=Depends(_request_user),
):
    require_permission(_get_user(user), SPOT_LEDGER_EXPORT_RESOURCE, "export")
    params = locals().copy()
    records = list_records(params, user=user)
    content = _export_workbook(records, include_technical_key=include_technical_key)
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=spot-ledger.xlsx"},
    )
