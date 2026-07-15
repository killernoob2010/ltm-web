"""订单融资进度监控。

Excel 台账解析、导入、查询和管理端计划字段维护。
"""
from __future__ import annotations

import json
import logging
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel
import xlrd
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from . import db
from .permissions import require_permission


router = APIRouter()
logger = logging.getLogger(__name__)

SUBSIDIARIES = ["东钢", "北满", "承德", "抚顺", "西林", "阿城"]
LOCAL_DEFAULT_LEDGER_DIR = Path("/Users/wangjingze/建龙/贸易处/订单融资合同汇总")
LOCAL_DEFAULT_LEDGER_WORKBOOK = Path("/Users/wangjingze/建龙/贸易处/YOLANDA和香港建龙出口钢材信用证台账.xlsx")
ORDER_FINANCE_MODULE = "order_finance_progress"
ORDER_FINANCE_CAPITAL_MODULE = "order_finance_capital"
TARGET_XLSX_SHEETS = ("订单", "额度", "预警")

DEFAULT_BANK_LIMITS = [
    {
        "bank": "中信唐山",
        "limit": 200000000,
        "note": "流贷-限非中信银行融资主体",
        "lc_requirement": "可接受FCR",
        "bill_requirement": "无限制",
        "finance_ratio": "",
        "term": "",
    },
    {
        "bank": "OCBC",
        "limit": 6000 * 7.2 * 10000,
        "note": "订单融资-东钢 / 打包贷款-集团内",
        "lc_requirement": "可接受FCR",
        "bill_requirement": "to order或客户的银行",
        "finance_ratio": "",
        "term": "",
    },
    {
        "bank": "UOB",
        "limit": 2000 * 7.2 * 10000,
        "note": "订单融资-集团内钢厂",
        "lc_requirement": "不能接受FCR",
        "bill_requirement": "to order或客户的银行",
        "finance_ratio": "",
        "term": "",
    },
    {
        "bank": "918ING银行（新加坡）",
        "limit": 1000 * 7.2 * 10000,
        "note": "订单融资-天津、集团内钢厂",
        "lc_requirement": "可接受FCR",
        "bill_requirement": "to order、客户银行、客户均可",
        "finance_ratio": "",
        "term": "",
    },
    {
        "bank": "918ING银行（香港）",
        "limit": 3000 * 7.2 * 10000,
        "note": "订单融资-天津、集团内钢厂",
        "lc_requirement": "可接受FCR",
        "bill_requirement": "to order、客户银行、客户均可",
        "finance_ratio": "",
        "term": "",
    },
]

FACT_FIELDS = [
    "business_key", "subsidiary", "source_file", "source_sheet", "source_row_start",
    "source_row_end", "source_snapshot_date", "product_name", "purchase_contract_no",
    "system_contract_no", "buyer", "seller", "overseas_entity", "terminal_customer",
    "contract_date", "trade_term", "origin_port", "destination_port",
    "contract_quantity_mt", "contract_currency", "contract_amount", "finance_bank",
    "finance_amount_expected", "finance_amount_actual", "repaid_amount",
    "remaining_credit_amount", "finance_drawdown_date", "finance_due_date",
    "finance_days", "finance_status", "latest_shipment_date", "lc_latest_shipment_date",
    "vessel_voyage", "bill_of_lading_date", "bill_of_lading_no",
    "document_submission_date", "collection_date", "actual_shipped_quantity_mt",
    "actual_goods_amount", "tail_amount", "tail_payment_date", "executor",
    "business_status", "risk_level", "remark", "sales_contracts_json",
    "settlement_json", "corrections_json", "import_warnings_json", "source_json",
]

MANAGEMENT_FIELDS = {
    "planned_drawdown_date",
    "planned_finance_amount",
    "amount_adjustment_note",
    "repayment_requirement",
    "repayment_requirement_status",
    "next_action",
    "next_follow_up_date",
    "manager_note",
    "shipment_confirmed_date",
    "shipment_confirmed_by",
    "shipment_confirmed_at",
}


class ImportLocalRequest(BaseModel):
    directory: str = str(LOCAL_DEFAULT_LEDGER_WORKBOOK)


class ManagementUpdateRequest(BaseModel):
    planned_drawdown_date: Optional[str] = None
    planned_finance_amount: Optional[float] = None
    amount_adjustment_note: Optional[str] = None
    repayment_requirement: Optional[str] = None
    repayment_requirement_status: Optional[str] = None
    next_action: Optional[str] = None
    next_follow_up_date: Optional[str] = None
    manager_note: Optional[str] = None


class ShipmentConfirmationRequest(BaseModel):
    confirmed: bool = True
    shipment_confirmed_date: Optional[str] = None


class ContractReminderRequest(BaseModel):
    manager_note: Optional[str] = None
    next_follow_up_date: Optional[str] = None


class ManualOrderFinanceRequest(BaseModel):
    subsidiary: str
    product_name: Optional[str] = None
    purchase_contract_no: Optional[str] = None
    system_contract_no: Optional[str] = None
    terminal_customer: Optional[str] = None
    contract_quantity_mt: Optional[float] = None
    contract_currency: Optional[str] = "CNY"
    contract_amount: Optional[float] = None
    finance_bank: Optional[str] = None
    finance_amount_expected: Optional[float] = None
    finance_amount_actual: Optional[float] = None
    finance_drawdown_date: Optional[str] = None
    finance_due_date: Optional[str] = None
    latest_shipment_date: Optional[str] = None
    bill_of_lading_date: Optional[str] = None
    collection_date: Optional[str] = None
    executor: Optional[str] = None
    planned_drawdown_date: Optional[str] = None
    planned_finance_amount: Optional[float] = None
    amount_adjustment_note: Optional[str] = None
    repayment_requirement: Optional[str] = None
    repayment_requirement_status: Optional[str] = None
    next_action: Optional[str] = None
    next_follow_up_date: Optional[str] = None
    manager_note: Optional[str] = None


class DuplicateOrderFinanceError(ValueError):
    def __init__(self, existing: Dict[str, Any]):
        super().__init__("已存在相同子公司和合同号的订单融资记录")
        self.existing = existing


async def order_finance_current_user(authorization: Optional[str] = Header(default=None)):
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def order_finance_require_edit(user: dict):
    require_permission(user, "order_finance.records", "edit")


def order_finance_require_import(user: dict):
    require_permission(user, "order_finance.records", "import")


def order_finance_require_view(user: dict):
    require_permission(user, "order_finance.records", "view")


def _cell_value(book, cell) -> Any:
    if cell.ctype == xlrd.XL_CELL_EMPTY:
        return None
    if cell.ctype == xlrd.XL_CELL_DATE:
        try:
            dt = xlrd.xldate_as_datetime(cell.value, book.datemode)
            return dt.date().isoformat() if dt.time() == datetime.min.time() else dt.isoformat(sep=" ")
        except Exception:
            return cell.value
    if cell.ctype == xlrd.XL_CELL_NUMBER:
        return int(cell.value) if float(cell.value).is_integer() else round(float(cell.value), 6)
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        return bool(cell.value)
    value = str(cell.value).strip()
    return value.replace("\n", " / ") if value else None


def _row_values(book, sheet, row_idx: int) -> List[Any]:
    return [_cell_value(book, sheet.cell(row_idx, col)) for col in range(sheet.ncols)]


def _normalize_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(value).strip()


def _to_float(value: Any) -> Optional[float]:
    if value in (None, "", "-"):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("CNY", "").replace("USD", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(value: Any) -> Optional[int]:
    number = _to_float(value)
    return int(number) if number is not None else None


def _normalize_date(value: Any) -> str:
    if value in (None, "", "-"):
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return ""
    for sep in ("/", "."):
        text = text.replace(sep, "-")
    if " " in text and len(text.split()) > 1:
        return text
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return text


def _normalize_xlsx_date(value: Any) -> str:
    if value in (None, "", "-", 0, "0"):
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)):
        try:
            return from_excel(value).date().isoformat()
        except Exception:
            return ""
    return _normalize_date(value)


def _parse_date(value: Any) -> Optional[date]:
    text = _normalize_date(value)
    if not text or len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _effective_finance_due(new_due: Any, original_due: Any, extension_days: int = 0) -> str:
    normalized_new = _normalize_xlsx_date(new_due)
    if normalized_new:
        return normalized_new
    normalized_original = _normalize_xlsx_date(original_due)
    parsed_original = _parse_date(normalized_original)
    if parsed_original and extension_days > 0:
        return (parsed_original + timedelta(days=extension_days)).isoformat()
    return normalized_original


def _find_col(headers: List[Any], *needles: str) -> Optional[int]:
    for needle in needles:
        for idx, header in enumerate(headers):
            if header and needle in str(header):
                return idx
    return None


def _get(row: List[Any], idx: Optional[int]) -> Any:
    return row[idx] if idx is not None and idx < len(row) else None


def _is_sequence(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return value.is_integer()
    return False


def _subsidiary_from_filename(filename: str) -> str:
    for name in SUBSIDIARIES:
        if name in filename:
            return name
    return Path(filename).stem


def _currency_amount(row: List[Any], amount_col: Optional[int]) -> tuple[str, Optional[float]]:
    value = _get(row, amount_col)
    next_value = _get(row, amount_col + 1 if amount_col is not None else None)
    text = _normalize_text(value).upper()
    if text in {"CNY", "USD", "AED", "MYR"}:
        return text, _to_float(next_value)
    return "", _to_float(value)


def _derive_bank(product: Any, finance_bank: Any, remark: Any) -> str:
    explicit = _normalize_text(finance_bank)
    if explicit and len(explicit) <= 20:
        return explicit
    text = f"{_normalize_text(product)} {_normalize_text(remark)}".upper()
    for bank in ["中信", "UOB", "OCBC", "ING", "邮储"]:
        if bank.upper() in text:
            return bank
    return explicit


def _terminal_customer(children: List[Dict[str, Any]], buyer: Any) -> str:
    for child in children:
        candidate = _normalize_text(child.get("buyer"))
        if candidate and candidate.upper() not in {"YOLANDA", "SINGAPORE YOLANDA PTE. LTD.", "建龙国贸", "天津建龙"}:
            return candidate
    main_buyer = _normalize_text(buyer)
    if main_buyer.upper() not in {"YOLANDA", "SINGAPORE YOLANDA PTE. LTD."}:
        return main_buyer
    return ""


def _overseas_entity(row: List[Any], children: List[Dict[str, Any]], buyer_col: Optional[int], seller_col: Optional[int]) -> str:
    candidates = [_get(row, buyer_col), _get(row, seller_col)]
    for child in children:
        candidates.extend([child.get("buyer"), child.get("seller")])
    for candidate in candidates:
        text = _normalize_text(candidate)
        upper = text.upper()
        if "YOLANDA" in upper or "HONG KONG" in upper or "SINGAPORE" in upper or "建龍" in text or "建龙" in text:
            return text
    return ""


def _business_key(subsidiary: str, purchase_contract: Any, system_contract: Any, source_file: str, row_no: int) -> str:
    purchase = _normalize_text(purchase_contract)
    system = _normalize_text(system_contract)
    if purchase or system:
        return "|".join([subsidiary, purchase, system])
    return "|".join([subsidiary, source_file, str(row_no)])


def _manual_business_key(record: Dict[str, Any]) -> str:
    subsidiary = _normalize_text(record.get("subsidiary"))
    purchase = _normalize_text(record.get("purchase_contract_no"))
    system = _normalize_text(record.get("system_contract_no"))
    if purchase or system:
        return _business_key(subsidiary, purchase, system, "手动新增", 0)
    return "|".join([subsidiary, "手动新增", uuid4().hex])


def _build_warnings(record: Dict[str, Any]) -> List[Dict[str, str]]:
    warnings: List[Dict[str, str]] = []
    for field in ("contract_date", "finance_drawdown_date", "finance_due_date", "latest_shipment_date", "bill_of_lading_date"):
        text = _normalize_text(record.get(field))
        parsed = _parse_date(text)
        if text and not parsed:
            warnings.append({"field": field, "level": "高", "message": f"日期无法识别：{text}"})
            continue
        if parsed and (parsed.year < 2024 or parsed.year > 2028):
            warnings.append({"field": field, "level": "高", "message": f"日期年份异常：{text}"})
    drawdown = _parse_date(record.get("finance_drawdown_date"))
    due = _parse_date(record.get("finance_due_date"))
    if drawdown and due and due < drawdown:
        warnings.append({"field": "finance_due_date", "level": "高", "message": "融资到期日早于放款日期"})
    return warnings


def _is_data_quality_warning(warning: Dict[str, Any]) -> bool:
    return _normalize_text(warning.get("field")) != "excel_alert"


def _data_quality_warning_count(records: List[Dict[str, Any]]) -> int:
    return sum(
        1
        for record in records
        for warning in _json_loads(record.get("import_warnings_json"), [])
        if _is_data_quality_warning(warning)
    )


def derive_business_status(record: Dict[str, Any]) -> Dict[str, str]:
    remark = _normalize_text(record.get("remark"))
    drawdown = _normalize_date(record.get("finance_drawdown_date"))
    due = _parse_date(record.get("finance_due_date"))
    bill = _normalize_date(record.get("bill_of_lading_date"))
    collection = _normalize_date(record.get("collection_date"))
    today = date.today()

    if "已结算" in remark:
        return {"business_status": "已结算", "risk_level": "低", "next_action": "无"}
    if not drawdown:
        return {"business_status": "待放款", "risk_level": "中", "next_action": "确认放款计划"}
    if not bill:
        if due and due <= today:
            return {"business_status": "需展期确认", "risk_level": "高", "next_action": "确认是否延期融资"}
        if due and (due - today).days <= 7:
            return {"business_status": "已放款待装船", "risk_level": "高", "next_action": "跟进船期和提单"}
        if due and (due - today).days <= 30:
            return {"business_status": "已放款待装船", "risk_level": "中", "next_action": "跟进船期和提单"}
        return {"business_status": "已放款待装船", "risk_level": "中", "next_action": "跟进船期和提单"}
    if not collection:
        return {"business_status": "已装船待回款", "risk_level": "中", "next_action": "跟进交单和回款"}
    return {"business_status": "已回款待结算", "risk_level": "低", "next_action": "确认结算"}


def _columns(headers: List[Any]) -> Dict[str, Optional[int]]:
    return {
        "product": _find_col(headers, "货物品名", "货物"),
        "purchase_contract": _find_col(headers, "东钢合同号", "北满采/销合同号", "承德采/销合同号", "抚顺采/销合同号", "西林采/销合同号", "阿城采/销合同号"),
        "system_contract": _find_col(headers, "YOLANDA合同号", "Yolanda/Jianlong采/销合同号", "Yolanda采/销合同号", "YOLANDA采/销合同号"),
        "lc_no": _find_col(headers, "LC NO"),
        "buyer": _find_col(headers, "买方"),
        "seller": _find_col(headers, "卖方"),
        "contract_date": _find_col(headers, "合同日期"),
        "trade_term": _find_col(headers, "价格条款"),
        "latest_ship": _find_col(headers, "最迟装船期"),
        "origin": _find_col(headers, "起运港"),
        "destination": _find_col(headers, "目的港", "卸港"),
        "quantity": _find_col(headers, "合同数量"),
        "amount": _find_col(headers, "合同总金额"),
        "loan_expected": _find_col(headers, "应放款金额", "融资金额", "放款金额"),
        "loan_actual": _find_col(headers, "实际放款金额"),
        "repaid": _find_col(headers, "已还款金额"),
        "remaining": _find_col(headers, "剩余额度"),
        "loan_date": _find_col(headers, "放款日期"),
        "loan_due": _find_col(headers, "放款到期日期", "放款到期日", "融资到期日"),
        "finance_days": _find_col(headers, "融资天数"),
        "payment_method": _find_col(headers, "付款方式"),
        "ship_qty": _find_col(headers, "实际出货数量", "装船数量"),
        "actual_amount": _find_col(headers, "实际出货金额", "实际货物金额"),
        "tail_amount": _find_col(headers, "尾款", "采购应退款金额"),
        "tail_date": _find_col(headers, "尾款付款日期"),
        "lc_date": _find_col(headers, "开证日"),
        "lc_bank": _find_col(headers, "开证行"),
        "lc_latest_ship": _find_col(headers, "LC-LSD船期", "LC 最迟装期"),
        "forwarder": _find_col(headers, "货代"),
        "bl_date": _find_col(headers, "提单日期"),
        "vessel": _find_col(headers, "船名航次", "船名", "船期"),
        "bl_no": _find_col(headers, "提单号"),
        "doc_date": _find_col(headers, "交单日期"),
        "collection_date": _find_col(headers, "回款日期"),
        "owner": _find_col(headers, "执行人员", "执行"),
        "status": _find_col(headers, "状态"),
        "remark": _find_col(headers, "备注"),
    }


def parse_order_finance_workbook(path: Path) -> Dict[str, Any]:
    book = xlrd.open_workbook(str(path), formatting_info=True)
    sheet = book.sheet_by_index(0)
    headers = _row_values(book, sheet, 1)
    rows = [_row_values(book, sheet, row_idx) for row_idx in range(sheet.nrows)]
    cols = _columns(headers)
    subsidiary = _subsidiary_from_filename(path.name)
    snapshot_date = date.today().isoformat()

    primary_indices = []
    for row_idx, row in enumerate(rows[2:], start=2):
        first = _get(row, 0)
        product = _normalize_text(_get(row, cols["product"]))
        if _is_sequence(first) and product and product.upper() not in {"TOTAL", "合计"}:
            primary_indices.append(row_idx)
    primary_indices.append(sheet.nrows)

    records = []
    for idx, row_idx in enumerate(primary_indices[:-1]):
        row = rows[row_idx]
        next_row_idx = primary_indices[idx + 1]
        children = []
        for child_idx in range(row_idx + 1, next_row_idx):
            child = rows[child_idx]
            contract = _normalize_text(_get(child, cols["purchase_contract"]))
            system_contract = _normalize_text(_get(child, cols["system_contract"]))
            buyer = _normalize_text(_get(child, cols["buyer"]))
            seller = _normalize_text(_get(child, cols["seller"]))
            lc_no = _normalize_text(_get(child, cols["lc_no"]))
            if not any([contract, system_contract, buyer, seller, lc_no]):
                continue
            child_currency, child_amount = _currency_amount(child, cols["amount"])
            children.append(
                {
                    "source_row": child_idx + 1,
                    "contract": contract,
                    "system_contract": system_contract,
                    "lc_no": lc_no,
                    "buyer": buyer,
                    "seller": seller,
                    "currency": child_currency,
                    "amount": child_amount,
                    "lc_date": _normalize_date(_get(child, cols["lc_date"])),
                    "lc_bank": _normalize_text(_get(child, cols["lc_bank"])),
                    "lc_latest_shipment_date": _normalize_date(_get(child, cols["lc_latest_ship"])),
                    "bill_of_lading_date": _normalize_date(_get(child, cols["bl_date"])),
                    "collection_date": _normalize_date(_get(child, cols["collection_date"])),
                    "remark": _normalize_text(_get(child, cols["remark"])),
                }
            )

        currency, amount = _currency_amount(row, cols["amount"])
        purchase_contract = _normalize_text(_get(row, cols["purchase_contract"]))
        system_contract = _normalize_text(_get(row, cols["system_contract"]))
        remark = _normalize_text(_get(row, cols["remark"])) or _normalize_text(_get(row, cols["status"]))
        record = {
            "business_key": _business_key(subsidiary, purchase_contract, system_contract, path.name, row_idx + 1),
            "subsidiary": subsidiary,
            "source_file": path.name,
            "source_sheet": sheet.name,
            "source_row_start": row_idx + 1,
            "source_row_end": next_row_idx,
            "source_snapshot_date": snapshot_date,
            "product_name": _normalize_text(_get(row, cols["product"])),
            "purchase_contract_no": purchase_contract,
            "system_contract_no": system_contract,
            "buyer": _normalize_text(_get(row, cols["buyer"])),
            "seller": _normalize_text(_get(row, cols["seller"])),
            "contract_date": _normalize_date(_get(row, cols["contract_date"])),
            "trade_term": _normalize_text(_get(row, cols["trade_term"])),
            "origin_port": _normalize_text(_get(row, cols["origin"])),
            "destination_port": _normalize_text(_get(row, cols["destination"])),
            "contract_quantity_mt": _to_float(_get(row, cols["quantity"])),
            "contract_currency": currency,
            "contract_amount": amount,
            "finance_amount_expected": _to_float(_get(row, cols["loan_expected"])),
            "finance_amount_actual": _to_float(_get(row, cols["loan_actual"])),
            "repaid_amount": _to_float(_get(row, cols["repaid"])),
            "remaining_credit_amount": _to_float(_get(row, cols["remaining"])),
            "finance_drawdown_date": _normalize_date(_get(row, cols["loan_date"])),
            "finance_due_date": _normalize_date(_get(row, cols["loan_due"])),
            "finance_days": _to_int(_get(row, cols["finance_days"])),
            "latest_shipment_date": _normalize_date(_get(row, cols["latest_ship"])),
            "lc_latest_shipment_date": _normalize_date(_get(row, cols["lc_latest_ship"])),
            "vessel_voyage": _normalize_text(_get(row, cols["vessel"])),
            "bill_of_lading_date": _normalize_date(_get(row, cols["bl_date"])),
            "bill_of_lading_no": _normalize_text(_get(row, cols["bl_no"])),
            "document_submission_date": _normalize_date(_get(row, cols["doc_date"])),
            "collection_date": _normalize_date(_get(row, cols["collection_date"])),
            "actual_shipped_quantity_mt": _to_float(_get(row, cols["ship_qty"])),
            "actual_goods_amount": _to_float(_get(row, cols["actual_amount"])),
            "tail_amount": _to_float(_get(row, cols["tail_amount"])),
            "tail_payment_date": _normalize_date(_get(row, cols["tail_date"])),
            "executor": _normalize_text(_get(row, cols["owner"])),
            "remark": remark,
            "sales_contracts_json": json.dumps(children, ensure_ascii=False),
            "settlement_json": "{}",
            "corrections_json": "[]",
            "source_json": json.dumps({"headers": headers, "row": row, "children": children}, ensure_ascii=False, default=str),
        }
        record["terminal_customer"] = _terminal_customer(children, record["buyer"])
        record["overseas_entity"] = _overseas_entity(row, children, cols["buyer"], cols["seller"])
        record["finance_bank"] = _derive_bank(record["product_name"], _get(row, cols["lc_bank"]), remark)
        derived = derive_business_status(record)
        record.update(derived)
        record["finance_status"] = record["business_status"]
        warnings = _build_warnings(record)
        record["import_warnings_json"] = json.dumps(warnings, ensure_ascii=False)
        records.append(record)

    return {
        "file": path.name,
        "sheet": sheet.name,
        "records": records,
        "summary": {"record_count": len(records), "warning_count": _data_quality_warning_count(records)},
    }


def _clean_xlsx_value(value: Any) -> Any:
    if value in (None, "", 0, "0"):
        return None
    if isinstance(value, str):
        text = value.strip()
        return text if text and text not in {"0", "-", "—"} else None
    return value


def _xlsx_text(value: Any) -> str:
    value = _clean_xlsx_value(value)
    return "" if value is None else str(value).strip()


def _xlsx_float(value: Any, scale: float = 1.0) -> Optional[float]:
    value = _clean_xlsx_value(value)
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value) / scale, 6)
    text = str(value).replace(",", "").strip()
    try:
        return round(float(text) / scale, 6)
    except ValueError:
        return None


def _xlsx_entity(item_no: str) -> str:
    return "香港建龙" if item_no.startswith("H-") else "YOLANDA"


def _xlsx_business_key_base(record: Dict[str, Any], source_file: str, source_row: int) -> str:
    parts = [
        _normalize_text(record.get("subsidiary")),
        _normalize_text(record.get("purchase_contract_no")),
        _normalize_text(record.get("system_contract_no")),
    ]
    if any(parts[1:]):
        return "|".join(parts)
    return "|".join([parts[0], source_file, str(source_row)])


def _xlsx_business_key_suffix(record: Dict[str, Any], source_row: int) -> str:
    return "|".join([
        _normalize_text(record.get("finance_bank")),
        _normalize_text(record.get("finance_drawdown_date")),
        str(record.get("finance_amount_actual") or ""),
        str(source_row),
    ])


def _xlsx_row_record(path: Path, sheet_name: str, headers: List[str], values: tuple[Any, ...], row_idx: int) -> Optional[Dict[str, Any]]:
    row = dict(zip(headers, values))
    item_no = _xlsx_text(row.get("项次"))
    if not item_no:
        return None
    subsidiary = _xlsx_text(row.get("供应商简称")) or _subsidiary_from_filename(_xlsx_text(row.get("供应商")))
    finance_due = _normalize_xlsx_date(row.get("新到期日")) or _normalize_xlsx_date(row.get("原到期日"))
    repay_date = _normalize_xlsx_date(row.get("还款日"))
    loan_status = _xlsx_text(row.get("贷款状态"))
    lc_contract = _xlsx_text(row.get("双方合同号"))
    source_date = ""
    if "-2025-" in item_no:
        source_date = "2025-01-01"
    elif "-2026-" in item_no:
        source_date = "2026-01-01"
    else:
        source_date = date.today().isoformat()
    record = {
        "subsidiary": subsidiary,
        "source_file": path.name,
        "source_sheet": sheet_name,
        "source_row_start": row_idx,
        "source_row_end": row_idx,
        "source_snapshot_date": source_date,
        "product_name": _xlsx_text(row.get("品名")),
        "purchase_contract_no": _xlsx_text(row.get("合同编号")),
        "system_contract_no": _xlsx_text(row.get("系统合同号")),
        "buyer": _xlsx_text(row.get("合同买方")),
        "seller": _xlsx_text(row.get("供应商")),
        "overseas_entity": _xlsx_entity(item_no),
        "terminal_customer": _xlsx_text(row.get("合同买方")),
        "contract_date": _normalize_xlsx_date(row.get("付款日期")),
        "trade_term": _xlsx_text(row.get("LC类型")),
        "origin_port": _xlsx_text(row.get("起运港")),
        "destination_port": _xlsx_text(row.get("目的港")),
        "contract_quantity_mt": _xlsx_float(row.get("合同数量(吨)")),
        "contract_currency": _xlsx_text(row.get("合同币别")) or "CNY",
        "contract_amount": _xlsx_float(row.get("付款金额")),
        "finance_bank": _xlsx_text(row.get("贷款行")),
        "finance_amount_expected": _xlsx_float(row.get("贷款人民币金额")),
        "finance_amount_actual": _xlsx_float(row.get("贷款人民币金额")),
        "repaid_amount": _xlsx_float(row.get("贷款人民币金额")) if repay_date or loan_status == "已还款" else None,
        "remaining_credit_amount": None,
        "finance_drawdown_date": _normalize_xlsx_date(row.get("借款日期")),
        "finance_due_date": finance_due,
        "finance_days": None,
        "finance_status": loan_status,
        "latest_shipment_date": _normalize_xlsx_date(row.get("最迟装船日")),
        "lc_latest_shipment_date": _normalize_xlsx_date(row.get("LC有效期")),
        "vessel_voyage": _xlsx_text(row.get("船名")),
        "bill_of_lading_date": "",
        "bill_of_lading_no": "",
        "document_submission_date": _normalize_xlsx_date(row.get("交单日期")),
        "collection_date": _normalize_xlsx_date(row.get("收汇日期")),
        "actual_shipped_quantity_mt": None,
        "actual_goods_amount": _xlsx_float(row.get("交单金额")),
        "tail_amount": None,
        "tail_payment_date": "",
        "executor": "",
        "remark": _xlsx_text(row.get("情况说明")) or loan_status,
        "sales_contracts_json": json.dumps([{
            "item_no": item_no,
            "contract": lc_contract,
            "lc_no": _xlsx_text(row.get("信用证编号")),
            "lc_bank": _xlsx_text(row.get("开证银行")),
            "lc_amount": _xlsx_float(row.get("信用证金额")),
            "lc_issue_date": _normalize_xlsx_date(row.get("开证日期")),
            "lc_expiry_date": _normalize_xlsx_date(row.get("LC有效期")),
            "lc_type": _xlsx_text(row.get("LC类型")),
            "transferable": _xlsx_text(row.get("是否可转让")),
            "receiving_bank": _xlsx_text(row.get("收证行")),
            "discount_date": _normalize_xlsx_date(row.get("贴现日期")),
        }], ensure_ascii=False),
        "settlement_json": "{}",
        "corrections_json": "[]",
        "source_json": json.dumps({"headers": headers, "row": list(values), "item_no": item_no}, ensure_ascii=False, default=str),
    }
    record["business_key"] = _xlsx_business_key_base(record, path.name, row_idx)
    derived = derive_business_status(record)
    if loan_status == "已还款" or repay_date:
        derived = {"business_status": "已结算", "risk_level": "低", "next_action": "无"}
    record.update(derived)
    record["finance_status"] = loan_status or record["business_status"]
    warnings = _build_warnings(record)
    record["import_warnings_json"] = json.dumps(warnings, ensure_ascii=False)
    return record


def _compact_header(value: Any) -> str:
    return _xlsx_text(value).replace("\n", "").replace(" ", "")


def _find_xlsx_header_row(sheet, aliases: tuple[str, ...] = ("项次",)) -> tuple[int, List[Any]]:
    normalized_aliases = {_compact_header(alias) for alias in aliases}
    for row_idx, values in enumerate(sheet.iter_rows(min_row=1, max_row=min(sheet.max_row, 20), values_only=True), start=1):
        headers = [_compact_header(value) for value in values]
        if any(alias in headers for alias in normalized_aliases):
            return row_idx, list(values)
    raise ValueError(f"{sheet.title}页签未找到项次表头")


def _row_alias(row: Dict[str, Any], *aliases: str) -> Any:
    compact = {_compact_header(key): value for key, value in row.items()}
    for alias in aliases:
        key = _compact_header(alias)
        if key in compact:
            return compact[key]
    return None


def _normalized_order_status(value: Any) -> str:
    text = _xlsx_text(value)
    if "结案" in text or text in {"已完成", "已结算"}:
        return "结案"
    if "存续" in text or text in {"进行中", "未结案"}:
        return "存续"
    return text


def _alerts_grouped_by_item(sheet) -> Dict[str, List[Dict[str, str]]]:
    alerts: Dict[str, List[Dict[str, str]]] = {}
    rows = [list(values) for values in sheet.iter_rows(values_only=True)]
    for index, values in enumerate(rows):
        compact = [_compact_header(value) for value in values]
        if "项次" not in compact:
            continue
        item_col = compact.index("项次")
        title = ""
        if index > 0:
            title = next((_xlsx_text(value) for value in rows[index - 1] if _xlsx_text(value)), "")
        row_index = index + 1
        while row_index < len(rows):
            next_values = rows[row_index]
            next_compact = [_compact_header(value) for value in next_values]
            if "项次" in next_compact:
                break
            item_no = _xlsx_text(next_values[item_col] if item_col < len(next_values) else None)
            if not item_no:
                break
            if item_no.startswith("#"):
                row_index += 1
                continue
            message_parts = [_xlsx_text(value) for value in next_values if _xlsx_text(value)]
            message = title or "Excel预警"
            if message_parts:
                message = f"{message}：{' / '.join(message_parts[1:])}" if len(message_parts) > 1 else message
            alerts.setdefault(item_no, []).append({
                "field": "excel_alert",
                "level": "高",
                "message": message,
                "source_sheet": sheet.title,
                "source_row": str(row_index + 1),
            })
            row_index += 1
    return alerts


def _quota_label_rows(sheet) -> Dict[str, List[int]]:
    labels: Dict[str, List[int]] = {}
    for row_idx in range(1, sheet.max_row + 1):
        label = _compact_header(sheet.cell(row_idx, 1).value)
        if label:
            labels.setdefault(label, []).append(row_idx)
    return labels


def _quota_numeric_value(sheet, row_indices: List[int], col_idx: int) -> Optional[float]:
    value = None
    for row_idx in row_indices:
        parsed = _xlsx_float(sheet.cell(row_idx, col_idx).value)
        if parsed is not None:
            value = parsed
    return value


def _parse_quota_sheet(book) -> Dict[str, Any]:
    if "额度" not in book.sheetnames:
        return {"banks": [], "total_credit": 0.0, "used_credit": 0.0, "available_credit": 0.0}
    sheet = book["额度"]
    labels = _quota_label_rows(sheet)
    condition_rows = labels.get("限定工厂", [])
    if not condition_rows:
        return {"banks": [], "total_credit": 0.0, "used_credit": 0.0, "available_credit": 0.0}
    bank_header_row = condition_rows[0] - 1
    unit_text = " ".join(
        _xlsx_text(sheet.cell(row, col).value)
        for row in range(1, min(sheet.max_row, 5) + 1)
        for col in range(1, min(sheet.max_column, 12) + 1)
    )
    multiplier = 10000.0 if "万元" in unit_text else 1.0
    banks = []
    for col_idx in range(2, sheet.max_column + 1):
        bank = _xlsx_text(sheet.cell(bank_header_row, col_idx).value)
        if not bank:
            continue
        limit = _quota_numeric_value(sheet, labels.get("授信额度", []), col_idx)
        used = _quota_numeric_value(sheet, labels.get("目前占用额度", []), col_idx)
        available = _quota_numeric_value(sheet, labels.get("目前可用额度", []), col_idx)
        if limit is None and used is None and available is None:
            continue
        banks.append({
            "bank": bank,
            "limit": (limit or 0.0) * multiplier,
            "used": used * multiplier if used is not None else None,
            "available": available * multiplier if available is not None else None,
            "note": _xlsx_text(sheet.cell(condition_rows[0], col_idx).value),
            "lc_requirement": _xlsx_text(sheet.cell(labels.get("信用证要求", [condition_rows[0]])[0], col_idx).value),
            "bill_requirement": _xlsx_text(sheet.cell(labels.get("提单要求", [condition_rows[0]])[0], col_idx).value),
            "finance_ratio": _xlsx_text(sheet.cell(labels.get("订单融资比例", [condition_rows[0]])[0], col_idx).value),
            "term": _xlsx_text(sheet.cell(labels.get("期限", [condition_rows[0]])[0], col_idx).value),
        })
    total_credit = sum(float(bank["limit"] or 0) for bank in banks)
    used_values = [bank["used"] for bank in banks if bank["used"] is not None]
    used_credit = sum(float(value) for value in used_values) if used_values else None
    available_values = [bank["available"] for bank in banks if bank["available"] is not None]
    return {
        "banks": banks,
        "total_credit": total_credit,
        "used_credit": used_credit,
        "available_credit": sum(available_values) if len(available_values) == len(banks) else (total_credit - used_credit if used_credit is not None else None),
        "unit": "元",
    }


def _order_sheet_record(
    path: Path,
    sheet_name: str,
    headers: List[Any],
    values: tuple[Any, ...],
    row_idx: int,
    alerts_by_item: Dict[str, List[Dict[str, str]]],
) -> Optional[Dict[str, Any]]:
    row = dict(zip(headers, values))
    item_no = _xlsx_text(_row_alias(row, "项次", "订单项次"))
    if not item_no or item_no.startswith("#") or item_no in {"合计", "TOTAL"}:
        return None
    supplier_short = _xlsx_text(_row_alias(row, "供应商简称", "钢厂", "发货方", "供应商"))
    supplier_full = _xlsx_text(_row_alias(row, "供应商", "发货方", "钢厂"))
    finance_amount = _xlsx_float(_row_alias(row, "贷款人民币金额", "融资金额", "放款金额"))
    status = _normalized_order_status(_row_alias(row, "状态", "订单状态", "存续/结案", "贷款状态"))
    repay_date = _normalize_xlsx_date(_row_alias(row, "还款日", "还款日期"))
    original_due = _normalize_xlsx_date(_row_alias(row, "原到期日"))
    extension_days = _to_int(_row_alias(row, "展期天数")) or 0
    new_due = _normalize_xlsx_date(_row_alias(row, "新到期日", "融资到期日", "到期日"))
    finance_due = _effective_finance_due(
        new_due,
        original_due,
        extension_days,
    )
    latest_shipment_date = _normalize_xlsx_date(_row_alias(row, "最迟装船日", "最晚装船日"))
    bill_date = _normalize_xlsx_date(_row_alias(row, "提单日", "提单日期"))
    document_date = _normalize_xlsx_date(_row_alias(row, "交单日", "交单日期", "银行交单日"))
    source_date = f"{item_no.split('-')[1]}-01-01" if len(item_no.split("-")) > 2 and item_no.split("-")[1].isdigit() else date.today().isoformat()
    source_meta = {
        "item_no": item_no,
        "headers": [str(header or "") for header in headers],
        "row": list(values),
        "finance_rate": _xlsx_float(_row_alias(row, "利率")),
        "original_due_date": original_due,
        "new_due_date": new_due,
        "extension_days": extension_days,
        "order_status": status,
        "alerts": alerts_by_item.get(item_no, []),
    }
    record = {
        "business_key": f"ITEM|{item_no}|1",
        "subsidiary": supplier_short or supplier_full or "未填供应商",
        "source_file": path.name,
        "source_sheet": sheet_name,
        "source_row_start": row_idx,
        "source_row_end": row_idx,
        "source_snapshot_date": source_date,
        "product_name": _xlsx_text(_row_alias(row, "品种材质", "品名", "品种", "材质")),
        "purchase_contract_no": _xlsx_text(_row_alias(row, "合同编号", "合同号", "合同")),
        "system_contract_no": _xlsx_text(_row_alias(row, "系统合同号")),
        "buyer": _xlsx_text(_row_alias(row, "合同买方", "买方")),
        "seller": supplier_full or supplier_short,
        "overseas_entity": _xlsx_entity(item_no),
        "terminal_customer": _xlsx_text(_row_alias(row, "合同买方", "终端客户", "客户")),
        "contract_date": _normalize_xlsx_date(_row_alias(row, "合同日期")),
        "trade_term": _xlsx_text(_row_alias(row, "贸易条款", "价格条款")),
        "origin_port": _xlsx_text(_row_alias(row, "起运港")),
        "destination_port": _xlsx_text(_row_alias(row, "目的港", "卸港")),
        "contract_quantity_mt": _xlsx_float(_row_alias(row, "合同数量(吨)", "吨数", "合同数量", "数量")),
        "contract_currency": _xlsx_text(_row_alias(row, "合同币别", "币种")) or "CNY",
        "contract_amount": _xlsx_float(_row_alias(row, "合同金额", "付款金额")),
        "finance_bank": _xlsx_text(_row_alias(row, "贷款行", "融资银行")),
        "finance_amount_expected": finance_amount,
        "finance_amount_actual": finance_amount,
        "repaid_amount": finance_amount if repay_date else None,
        "remaining_credit_amount": None,
        "finance_drawdown_date": _normalize_xlsx_date(_row_alias(row, "借款日期", "借款日", "放款日期")),
        "finance_due_date": finance_due,
        "finance_days": _to_int(_row_alias(row, "实际融资周期", "融资天数")),
        "finance_status": status,
        "latest_shipment_date": latest_shipment_date,
        "lc_latest_shipment_date": "",
        "vessel_voyage": "",
        "bill_of_lading_date": bill_date,
        "bill_of_lading_no": _xlsx_text(_row_alias(row, "提单号")),
        "document_submission_date": document_date,
        "collection_date": _normalize_xlsx_date(_row_alias(row, "收汇日期", "收汇日")),
        "actual_shipped_quantity_mt": None,
        "actual_goods_amount": None,
        "tail_amount": None,
        "tail_payment_date": repay_date,
        "executor": _xlsx_text(_row_alias(row, "执行人员", "负责人")),
        "business_status": status,
        "risk_level": "低" if status == "结案" else "中",
        "remark": _xlsx_text(_row_alias(row, "情况说明", "备注")),
        "sales_contracts_json": "[]",
        "settlement_json": "{}",
        "corrections_json": "[]",
        "source_json": json.dumps(source_meta, ensure_ascii=False, default=str),
    }
    warnings = _build_warnings(record) + list(alerts_by_item.get(item_no, []))
    if not status:
        warnings.append({"field": "business_status", "level": "高", "message": "存续/结案状态为空"})
    record["import_warnings_json"] = json.dumps(warnings, ensure_ascii=False)
    return record


def _parse_order_sheet(book, path: Path, alerts_by_item: Dict[str, List[Dict[str, str]]], capital: Dict[str, Any]) -> List[Dict[str, Any]]:
    sheet = book["订单"]
    header_row, headers = _find_xlsx_header_row(sheet)
    records: List[Dict[str, Any]] = []
    by_item: Dict[str, List[Dict[str, Any]]] = {}
    for row_idx, values in enumerate(sheet.iter_rows(min_row=header_row + 1, max_row=sheet.max_row, values_only=True), start=header_row + 1):
        record = _order_sheet_record(path, sheet.title, headers, values, row_idx, alerts_by_item)
        if not record:
            continue
        item_no = _item_no(record)
        siblings = by_item.setdefault(item_no, [])
        record["business_key"] = f"ITEM|{item_no}|{len(siblings) + 1}"
        siblings.append(record)
        records.append(record)
    active_amount = sum(
        float(record.get("finance_amount_actual") or record.get("finance_amount_expected") or 0)
        for record in records
        if record.get("business_status") == "存续"
    )
    quota_used = float(capital.get("used_credit") or 0)
    amount_multiplier = quota_used / active_amount if active_amount and quota_used else 1.0
    if 5000 <= amount_multiplier <= 15000:
        for record in records:
            for field in ("finance_amount_expected", "finance_amount_actual", "repaid_amount"):
                if record.get(field) is not None:
                    record[field] = float(record[field]) * 10000
            source = _json_loads(record.get("source_json"), {})
            source["finance_amount_unit"] = "万元"
            record["source_json"] = json.dumps(source, ensure_ascii=False, default=str)
    if records and capital.get("banks"):
        source = _json_loads(records[0].get("source_json"), {})
        source["workbook_capital"] = capital
        records[0]["source_json"] = json.dumps(source, ensure_ascii=False, default=str)
    return records


def parse_order_finance_xlsx_workbook(path: Path) -> Dict[str, Any]:
    book = load_workbook(path, data_only=True, read_only=False)
    if "订单" not in book.sheetnames:
        raise ValueError("Excel 缺少必需的订单页签")
    sheets = {name: name in book.sheetnames for name in TARGET_XLSX_SHEETS}
    alerts_by_item = _alerts_grouped_by_item(book["预警"]) if sheets["预警"] else {}
    capital = _parse_quota_sheet(book)
    records = _parse_order_sheet(book, path, alerts_by_item, capital)
    return {
        "file": path.name,
        "sheet": "订单",
        "sheets": sheets,
        "capital": capital,
        "records": records,
        "summary": {"record_count": len(records), "warning_count": _data_quality_warning_count(records)},
    }


def parse_order_finance_directory(directory: Path | str) -> Dict[str, Any]:
    base = Path(directory)
    if not base.exists():
        raise ValueError(f"目录不存在：{base}")
    if base.is_file():
        files = [base]
    else:
        files = sorted(
            path for path in base.iterdir()
            if path.suffix.lower() in {".xls", ".xlsx"} and not path.name.startswith("~$")
        )
    records: List[Dict[str, Any]] = []
    file_results = []
    for path in files:
        if path.suffix.lower() == ".xlsx":
            result = parse_order_finance_xlsx_workbook(path)
        else:
            result = parse_order_finance_workbook(path)
        records.extend(result["records"])
        file_results.append({
            "file": result["file"],
            "sheet": result["sheet"],
            "sheets": result.get("sheets"),
            **result["summary"],
        })
    return {
        "records": records,
        "files": file_results,
        "summary": {
            "files_read": len(files),
            "record_count": len(records),
            "warning_count": sum(item["warning_count"] for item in file_results),
        },
    }


def _json_or_empty(value: Any, empty: str = "{}") -> str:
    if value in (None, ""):
        return empty
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _row_to_dict(row: Any) -> Dict[str, Any]:
    return dict(row) if row else {}


def _serialize_record(record: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(record)
    for field, empty in (
        ("sales_contracts_json", "[]"),
        ("settlement_json", "{}"),
        ("management_plan_json", "{}"),
        ("manual_change_log_json", "[]"),
        ("corrections_json", "[]"),
        ("import_warnings_json", "[]"),
        ("source_json", "{}"),
    ):
        item[field] = _json_or_empty(item.get(field), empty)
    return item


def upsert_order_finance_records(records: List[Dict[str, Any]], imported_by: str = "") -> Dict[str, int]:
    inserted = 0
    updated = 0
    with db.connect() as conn:
        cur = conn.cursor()
        for raw in records:
            record = _serialize_record(raw)
            existing = db._exec(
                cur,
                "SELECT id FROM order_finance_progress WHERE business_key = ?",
                (record["business_key"],),
            ).fetchone()
            if existing:
                assignments = ", ".join(f"{field} = ?" for field in FACT_FIELDS)
                params = [record.get(field) for field in FACT_FIELDS]
                params.append(existing["id"])
                db._exec(
                    cur,
                    f"UPDATE order_finance_progress SET {assignments}, is_archived = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    tuple(params),
                )
                updated += 1
            else:
                insert_fields = FACT_FIELDS + [
                    "planned_drawdown_date", "planned_finance_amount", "amount_adjustment_note",
                    "repayment_requirement", "repayment_requirement_status", "next_action",
                    "next_follow_up_date", "manager_note", "manual_override_fields",
                    "shipment_confirmed_date", "shipment_confirmed_by", "shipment_confirmed_at",
                    "management_plan_json", "manual_change_log_json",
                ]
                values = [record.get(field) for field in FACT_FIELDS]
                values.extend([
                    None, None, "", "", "", record.get("next_action", ""), "", "",
                    "[]", None, None, None, "{}", "[]",
                ])
                placeholders = ", ".join("?" for _ in insert_fields)
                db._exec(
                    cur,
                    f"INSERT INTO order_finance_progress ({', '.join(insert_fields)}) VALUES ({placeholders})",
                    tuple(values),
                )
                inserted += 1
    return {"inserted": inserted, "updated": updated}


def _fact_values_equal(existing: Dict[str, Any], incoming: Dict[str, Any]) -> bool:
    return all(existing.get(field) == incoming.get(field) for field in FACT_FIELDS)


def apply_order_finance_snapshot(
    records: List[Dict[str, Any]],
    imported_by: str = "",
    sync_success_at: Optional[str] = None,
    source_version: Optional[str] = None,
    attempt_slot: Optional[str] = None,
) -> Dict[str, int]:
    del imported_by
    inserted = 0
    updated = 0
    archived = 0
    serialized = [_serialize_record(record) for record in records]
    incoming_keys = {record["business_key"] for record in serialized}

    with db.connect() as conn:
        cur = conn.cursor()
        existing_rows = db._exec(cur, "SELECT * FROM order_finance_progress").fetchall()
        existing_by_key = {
            row["business_key"]: _row_to_dict(row)
            for row in existing_rows
        }

        for record in serialized:
            existing = existing_by_key.get(record["business_key"])
            if existing:
                if not _fact_values_equal(existing, record) or existing.get("is_archived"):
                    for key_date_field in ("document_submission_date", "tail_payment_date"):
                        if existing.get(key_date_field) and not record.get(key_date_field):
                            logger.warning(
                                "order_finance_key_date_cleared",
                                extra={
                                    "business_key": record["business_key"],
                                    "key_date_field": key_date_field,
                                },
                            )
                    assignments = ", ".join(f"{field} = ?" for field in FACT_FIELDS)
                    params = [record.get(field) for field in FACT_FIELDS]
                    params.append(existing["id"])
                    db._exec(
                        cur,
                        f"UPDATE order_finance_progress SET {assignments}, "
                        "is_archived = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        tuple(params),
                    )
                    updated += 1
                continue

            insert_fields = FACT_FIELDS + [
                "planned_drawdown_date", "planned_finance_amount", "amount_adjustment_note",
                "repayment_requirement", "repayment_requirement_status", "next_action",
                "next_follow_up_date", "manager_note", "manual_override_fields",
                "shipment_confirmed_date", "shipment_confirmed_by", "shipment_confirmed_at",
                "management_plan_json", "manual_change_log_json",
            ]
            values = [record.get(field) for field in FACT_FIELDS]
            values.extend([
                None, None, "", "", "", record.get("next_action", ""), "", "",
                "[]", None, None, None, "{}", "[]",
            ])
            placeholders = ", ".join("?" for _ in insert_fields)
            db._exec(
                cur,
                f"INSERT INTO order_finance_progress ({', '.join(insert_fields)}) "
                f"VALUES ({placeholders})",
                tuple(values),
            )
            inserted += 1

        for existing in existing_by_key.values():
            if (
                not existing.get("is_archived")
                and existing.get("source_file") != "手动新增"
                and existing["business_key"] not in incoming_keys
            ):
                db._exec(
                    cur,
                    """UPDATE order_finance_progress
                       SET is_archived = 1, updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (existing["id"],),
                )
                archived += 1

        changed_count = inserted + updated + archived
        if sync_success_at is not None:
            db._exec(
                cur,
                """UPDATE order_finance_sync_status
                   SET last_success_at = ?, changed_count = ?, source_version = ?,
                       last_attempt_slot = COALESCE(?, last_attempt_slot),
                       updated_at = CURRENT_TIMESTAMP
                   WHERE id = 1""",
                (sync_success_at, changed_count, source_version, attempt_slot),
            )
        else:
            db._exec(
                cur,
                """UPDATE order_finance_sync_status
                   SET source_version = NULL, updated_at = CURRENT_TIMESTAMP
                   WHERE id = 1""",
            )

    return {
        "inserted": inserted,
        "updated": updated,
        "archived": archived,
        "changed_count": changed_count,
    }


def get_order_finance_sync_status() -> Dict[str, Any]:
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(
            cur,
            """SELECT last_success_at, changed_count, source_version, last_attempt_slot
               FROM order_finance_sync_status WHERE id = 1""",
        ).fetchone()
    status = _row_to_dict(row)
    return {
        "last_success_at": status.get("last_success_at"),
        "changed_count": int(status.get("changed_count") or 0),
        "source_version": status.get("source_version"),
        "last_attempt_slot": status.get("last_attempt_slot"),
    }


def claim_order_finance_sync_slot(slot_key: str) -> bool:
    with db.connect() as conn:
        cur = conn.cursor()
        result = db._exec(
            cur,
            """UPDATE order_finance_sync_status
               SET last_attempt_slot = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = 1 AND COALESCE(last_attempt_slot, '') != ?""",
            (slot_key, slot_key),
        )
        return int(getattr(result, "rowcount", 0) or 0) == 1


def record_unchanged_order_finance_sync(
    sync_success_at: str,
    source_version: str,
    attempt_slot: str,
) -> None:
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(
            cur,
            """UPDATE order_finance_sync_status
               SET last_success_at = ?, changed_count = 0, source_version = ?,
                   last_attempt_slot = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = 1""",
            (sync_success_at, source_version, attempt_slot),
        )


def archive_existing_excel_order_finance_records() -> int:
    with db.connect() as conn:
        cur = conn.cursor()
        result = db._exec(
            cur,
            """
            UPDATE order_finance_progress
            SET is_archived = 1, updated_at = CURRENT_TIMESTAMP
            WHERE is_archived = 0 AND source_file != '手动新增'
            """,
        )
    return int(getattr(result, "rowcount", 0) or 0)


def import_order_finance_directory(directory: Path | str, imported_by: str = "") -> Dict[str, Any]:
    parsed = parse_order_finance_directory(directory)
    changes = apply_order_finance_snapshot(parsed["records"], imported_by=imported_by)
    parsed["summary"].update(changes)
    return parsed


async def import_order_finance_upload(request: Request, file_name: str, imported_by: str = "") -> Dict[str, Any]:
    suffix = Path(file_name or "").suffix.lower()
    if suffix not in {".xlsx", ".xls"}:
        raise ValueError("请选择 .xlsx 或 .xls 格式的订单融资台账")
    file_bytes = await request.body()
    if not file_bytes:
        raise ValueError("上传文件为空")
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file_bytes)
            tmp_path = Path(tmp.name)
        parsed = parse_order_finance_directory(tmp_path)
        for record in parsed["records"]:
            record["source_file"] = file_name
            source = _json_loads(record.get("source_json"), {})
            if isinstance(source, dict):
                source["uploaded_file_name"] = file_name
                record["source_json"] = json.dumps(source, ensure_ascii=False, default=str)
        for item in parsed.get("files", []):
            item["file"] = file_name
        changes = apply_order_finance_snapshot(parsed["records"], imported_by=imported_by)
        parsed["summary"].update(changes)
        return parsed
    finally:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink()


ORDER_FINANCE_LIST_FIELDS = [
    "id", "business_key", "subsidiary", "source_file", "source_sheet", "source_row_start",
    "source_snapshot_date", "product_name", "purchase_contract_no", "system_contract_no",
    "overseas_entity", "terminal_customer", "contract_quantity_mt", "contract_currency", "contract_amount",
    "finance_bank", "finance_amount_expected", "finance_amount_actual", "finance_drawdown_date",
    "finance_due_date", "latest_shipment_date", "shipment_confirmed_date", "shipment_confirmed_by",
    "shipment_confirmed_at", "vessel_voyage", "bill_of_lading_date",
    "document_submission_date", "collection_date", "actual_shipped_quantity_mt", "executor", "business_status",
    "risk_level", "planned_drawdown_date", "planned_finance_amount", "amount_adjustment_note",
    "repayment_requirement", "repayment_requirement_status", "next_action",
    "next_follow_up_date", "manager_note", "tail_payment_date", "sales_contracts_json",
    "import_warnings_json", "source_json", "created_at", "updated_at",
]


def _clamp_limit(limit: int) -> int:
    return max(1, min(limit or 5000, 5000))


def list_order_finance_records_page(limit: int = 5000, offset: int = 0) -> Dict[str, Any]:
    limit = _clamp_limit(limit)
    offset = max(0, offset or 0)
    field_sql = ", ".join(ORDER_FINANCE_LIST_FIELDS)
    with db.connect() as conn:
        cur = conn.cursor()
        total_row = db._exec(
            cur,
            "SELECT COUNT(*) AS c FROM order_finance_progress WHERE is_archived = 0",
        ).fetchone()
        rows = db._exec(
            cur,
            f"""
            SELECT {field_sql}
            FROM order_finance_progress
            WHERE is_archived = 0
            ORDER BY
                CASE risk_level WHEN '高' THEN 1 WHEN '中' THEN 2 ELSE 3 END,
                COALESCE(finance_due_date, '9999-12-31'),
                id
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
    records = [_row_to_dict(row) for row in rows]
    total = int(total_row["c"] or 0)
    return {
        "records": records,
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(records) < total,
        },
    }


def list_order_finance_records() -> List[Dict[str, Any]]:
    return list_order_finance_records_page(limit=5000, offset=0)["records"]


def get_order_finance_record(record_id: int) -> Dict[str, Any]:
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(cur, "SELECT * FROM order_finance_progress WHERE id = ?", (record_id,)).fetchone()
    if not row:
        raise KeyError(record_id)
    return _row_to_dict(row)


def find_order_finance_duplicates(payload: Dict[str, Any], exclude_id: Optional[int] = None) -> Dict[str, Any]:
    candidate = dict(payload)
    candidate["subsidiary"] = _normalize_text(candidate.get("subsidiary"))
    candidate["purchase_contract_no"] = _normalize_text(candidate.get("purchase_contract_no"))
    candidate["system_contract_no"] = _normalize_text(candidate.get("system_contract_no"))
    exact = None
    if candidate["purchase_contract_no"] or candidate["system_contract_no"]:
        business_key = _manual_business_key(candidate)
        with db.connect() as conn:
            cur = conn.cursor()
            row = db._exec(
                cur,
                "SELECT * FROM order_finance_progress WHERE business_key = ? AND is_archived = 0",
                (business_key,),
            ).fetchone()
        exact = _row_to_dict(row)
        if exact and exclude_id and exact.get("id") == exclude_id:
            exact = None

    target_amount = (
        _to_float(candidate.get("planned_finance_amount"))
        or _to_float(candidate.get("finance_amount_actual"))
        or _to_float(candidate.get("finance_amount_expected"))
        or _to_float(candidate.get("contract_amount"))
    )
    target_customer = _normalize_text(candidate.get("terminal_customer")).upper()
    target_due = _normalize_date(candidate.get("finance_due_date"))
    similar = []
    if candidate["subsidiary"] and target_customer and target_due and target_amount is not None:
        for row in list_order_finance_records():
            if exclude_id and row.get("id") == exclude_id:
                continue
            if row.get("subsidiary") != candidate["subsidiary"]:
                continue
            row_customer = _normalize_text(row.get("terminal_customer")).upper()
            row_due = _normalize_date(row.get("finance_due_date"))
            row_amount = (
                _to_float(row.get("planned_finance_amount"))
                or _to_float(row.get("finance_amount_actual"))
                or _to_float(row.get("finance_amount_expected"))
                or _to_float(row.get("contract_amount"))
            )
            if row_customer == target_customer and row_due == target_due and row_amount == target_amount:
                similar.append(row)
    return {"exact": exact, "similar": similar[:5]}


def create_manual_order_finance_record(payload: Dict[str, Any], created_by: str = "") -> Dict[str, Any]:
    record = dict(payload)
    record["subsidiary"] = _normalize_text(record.get("subsidiary"))
    if not record["subsidiary"]:
        raise ValueError("子公司不能为空")
    duplicates = find_order_finance_duplicates(record)
    if duplicates["exact"]:
        raise DuplicateOrderFinanceError(duplicates["exact"])

    management_values = {field: record.get(field) for field in MANAGEMENT_FIELDS if record.get(field) not in (None, "")}
    manual_next_action = _normalize_text(record.get("next_action"))
    record.update(
        {
            "business_key": _manual_business_key(record),
            "source_file": "手动新增",
            "source_sheet": "",
            "source_row_start": None,
            "source_row_end": None,
            "source_snapshot_date": date.today().isoformat(),
            "purchase_contract_no": _normalize_text(record.get("purchase_contract_no")),
            "system_contract_no": _normalize_text(record.get("system_contract_no")),
            "terminal_customer": _normalize_text(record.get("terminal_customer")),
            "product_name": _normalize_text(record.get("product_name")),
            "contract_currency": _normalize_text(record.get("contract_currency")) or "CNY",
            "contract_quantity_mt": _to_float(record.get("contract_quantity_mt")),
            "contract_amount": _to_float(record.get("contract_amount")),
            "finance_bank": _normalize_text(record.get("finance_bank")),
            "finance_amount_expected": _to_float(record.get("finance_amount_expected")),
            "finance_amount_actual": _to_float(record.get("finance_amount_actual")),
            "finance_drawdown_date": _normalize_date(record.get("finance_drawdown_date")),
            "finance_due_date": _normalize_date(record.get("finance_due_date")),
            "latest_shipment_date": _normalize_date(record.get("latest_shipment_date")),
            "bill_of_lading_date": _normalize_date(record.get("bill_of_lading_date")),
            "collection_date": _normalize_date(record.get("collection_date")),
            "executor": _normalize_text(record.get("executor")),
            "remark": "手动新增",
            "sales_contracts_json": "[]",
            "settlement_json": "{}",
            "corrections_json": "[]",
            "source_json": json.dumps({"created_by": created_by, "source": "manual"}, ensure_ascii=False),
        }
    )
    derived = derive_business_status(record)
    record["business_status"] = derived["business_status"]
    record["risk_level"] = derived["risk_level"]
    record["finance_status"] = derived["business_status"]
    warnings = _build_warnings(record)
    record["import_warnings_json"] = json.dumps(warnings, ensure_ascii=False)

    insert_fields = FACT_FIELDS + [
        "planned_drawdown_date", "planned_finance_amount", "amount_adjustment_note",
        "repayment_requirement", "repayment_requirement_status", "next_action",
        "next_follow_up_date", "manager_note", "manual_override_fields",
        "shipment_confirmed_date", "shipment_confirmed_by", "shipment_confirmed_at",
        "management_plan_json", "manual_change_log_json",
    ]
    values = [record.get(field) for field in FACT_FIELDS]
    values.extend([
        _normalize_date(record.get("planned_drawdown_date")),
        _to_float(record.get("planned_finance_amount")),
        _normalize_text(record.get("amount_adjustment_note")),
        _normalize_text(record.get("repayment_requirement")),
        _normalize_text(record.get("repayment_requirement_status")),
        manual_next_action or derived["next_action"],
        _normalize_date(record.get("next_follow_up_date")),
        _normalize_text(record.get("manager_note")),
        json.dumps(sorted(management_values), ensure_ascii=False),
        _normalize_date(record.get("shipment_confirmed_date")),
        _normalize_text(record.get("shipment_confirmed_by")),
        _normalize_text(record.get("shipment_confirmed_at")),
        "{}",
        "[]",
    ])
    placeholders = ", ".join("?" for _ in insert_fields)
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(
            cur,
            f"INSERT INTO order_finance_progress ({', '.join(insert_fields)}) VALUES ({placeholders})",
            tuple(values),
        )
        record_id = db.last_insert_id(conn)
    return get_order_finance_record(record_id)


def update_management_fields(record_id: int, changes: Dict[str, Any], updated_by: str = "") -> Dict[str, Any]:
    allowed = {key: value for key, value in changes.items() if key in MANAGEMENT_FIELDS}
    if not allowed:
        return get_order_finance_record(record_id)
    before = get_order_finance_record(record_id)
    existing_log = json.loads(before.get("manual_change_log_json") or "[]")
    log_items = []
    for key, value in allowed.items():
        if before.get(key) != value:
            log_items.append({
                "field": key,
                "before": before.get(key),
                "after": value,
                "updated_by": updated_by,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            })
    override_fields = sorted(set(json.loads(before.get("manual_override_fields") or "[]")) | set(allowed))
    existing_log.extend(log_items)
    allowed["manual_override_fields"] = json.dumps(override_fields, ensure_ascii=False)
    allowed["manual_change_log_json"] = json.dumps(existing_log, ensure_ascii=False)

    assignments = ", ".join(f"{field} = ?" for field in allowed)
    params = list(allowed.values()) + [record_id]
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(
            cur,
            f"UPDATE order_finance_progress SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            tuple(params),
        )
    return get_order_finance_record(record_id)


def set_shipment_confirmation(
    item_no: str,
    confirmed: bool,
    shipment_confirmed_date: Optional[str] = None,
    updated_by: str = "",
) -> Dict[str, Any]:
    normalized_item = _normalize_text(item_no)
    matching = [row for row in list_order_finance_records() if _item_no(row) == normalized_item]
    if not matching:
        raise KeyError(normalized_item)
    if confirmed:
        normalized_date = _normalize_date(shipment_confirmed_date or date.today().isoformat())
        if not _parse_date(normalized_date):
            raise ValueError("实际装船日格式不正确")
        changes = {
            "shipment_confirmed_date": normalized_date,
            "shipment_confirmed_by": updated_by,
            "shipment_confirmed_at": datetime.now().isoformat(timespec="seconds"),
        }
    else:
        changes = {
            "shipment_confirmed_date": None,
            "shipment_confirmed_by": None,
            "shipment_confirmed_at": None,
        }
    for row in matching:
        update_management_fields(row["id"], changes, updated_by=updated_by)
    return {"item_no": normalized_item, "confirmed": confirmed, "updated": len(matching)}


def set_contract_reminder(
    item_no: str,
    manager_note: Optional[str] = None,
    next_follow_up_date: Optional[str] = None,
    updated_by: str = "",
) -> Dict[str, Any]:
    normalized_item = _normalize_text(item_no)
    matching = [row for row in list_order_finance_records() if _item_no(row) == normalized_item]
    if not matching:
        raise KeyError(normalized_item)
    normalized_note = _normalize_text(manager_note)
    normalized_date = _normalize_date(next_follow_up_date)
    if normalized_date and not _parse_date(normalized_date):
        raise ValueError("跟进日期格式不正确")
    stored_date = normalized_date or None
    changes = {"manager_note": normalized_note, "next_follow_up_date": stored_date}
    for row in matching:
        update_management_fields(row["id"], changes, updated_by=updated_by)
    return {
        "item_no": normalized_item,
        "manager_note": normalized_note,
        "next_follow_up_date": normalized_date,
        "updated": len(matching),
    }


def summarize_order_finance(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    active = [row for row in records if _normalize_text(row.get("business_status")) not in {"结案", "已完成", "已结算"}]
    due_soon = 0
    today = date.today()
    for row in active:
        due = _parse_date(row.get("finance_due_date"))
        if due and 0 <= (due - today).days <= 30:
            due_soon += 1
    finance_balance = sum(
        _to_float(row.get("planned_finance_amount")) or _to_float(row.get("finance_amount_actual")) or _to_float(row.get("finance_amount_expected")) or 0
        for row in active
    )
    return {
        "total_count": len(records),
        "active_count": len(active),
        "finance_balance": finance_balance,
        "due_30d_count": due_soon,
        "high_risk_count": len([row for row in records if row.get("risk_level") == "高"]),
    }


def _money_value(*values: Any) -> float:
    for value in values:
        parsed = _to_float(value)
        if parsed is not None:
            return parsed
    return 0.0


def _json_loads(value: Any, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _group_key(row: Dict[str, Any]) -> str:
    item_no = _item_no(row)
    if item_no and row.get("source_file") != "手动新增":
        return f"ITEM|{item_no}"
    parts = str(row.get("business_key") or "").split("|")
    if len(parts) >= 3:
        return "|".join(parts[:3])
    return "|".join([
        _normalize_text(row.get("subsidiary")),
        _normalize_text(row.get("purchase_contract_no")),
        _normalize_text(row.get("system_contract_no")),
    ])


def _item_no(row: Dict[str, Any]) -> str:
    source = _json_loads(row.get("source_json"), {})
    return _normalize_text(source.get("item_no")) or _normalize_text(row.get("purchase_contract_no")) or str(row.get("id"))


def _lc_info(row: Dict[str, Any]) -> Dict[str, Any]:
    items = _json_loads(row.get("sales_contracts_json"), [])
    if isinstance(items, list) and items:
        return items[0] or {}
    return {}


def _is_completed_group(rows: List[Dict[str, Any]]) -> bool:
    business_statuses = [_normalize_text(row.get("business_status")) for row in rows]
    if any(status == "存续" for status in business_statuses):
        return False
    explicit = [status for status in business_statuses if status]
    if explicit and all(status in {"结案", "已完成", "已结算"} for status in explicit):
        return True
    statuses = [_normalize_text(row.get("finance_status")) for row in rows]
    return bool(statuses) and all(status == "已还款" or row.get("repaid_amount") for status, row in zip(statuses, rows))


def _group_has_value(rows: List[Dict[str, Any]], field: str) -> bool:
    return any(bool(_normalize_text(row.get(field))) for row in rows)


def _group_shipment_completed(rows: List[Dict[str, Any]]) -> bool:
    return any(
        _group_has_value(rows, field)
        for field in ("shipment_confirmed_date", "bill_of_lading_date", "document_submission_date", "tail_payment_date")
    )


def _group_stage(rows: List[Dict[str, Any]]) -> str:
    if _is_completed_group(rows):
        return "已完成"
    has_loan = any(row.get("finance_drawdown_date") or _money_value(row.get("finance_amount_actual"), row.get("finance_amount_expected")) for row in rows)
    has_shipment = _group_shipment_completed(rows)
    has_document = _group_has_value(rows, "document_submission_date")
    has_repay = _group_has_value(rows, "tail_payment_date")
    if not has_loan:
        return "待放款"
    if has_repay:
        return "已还款待结案"
    if has_document:
        return "已交单待回款"
    if has_shipment:
        return "已装船待回款"
    return "已放款待装船"


def _days_to(value: Any) -> Optional[int]:
    parsed = _parse_date(value)
    if not parsed:
        return None
    return (parsed - date.today()).days


def _warning_indicator(warning: Dict[str, Any]) -> str:
    field = _normalize_text(warning.get("field"))
    message = _normalize_text(warning.get("message"))
    if field == "latest_shipment_date" or "最迟装船" in message:
        return "shipment"
    if field == "finance_due_date" or any(text in message for text in ("融资到期", "贷款到期", "还款到期")):
        return "finance_due"
    return "confirmation"


def _group_indicator_risks(rows: List[Dict[str, Any]], stage: str) -> Dict[str, str]:
    risks = {"shipment": "低", "finance_due": "低", "repayment": "低", "confirmation": "低", "reminder": "低"}
    if stage == "已完成":
        return risks
    shipment_completed = _group_shipment_completed(rows)
    warnings = [
        warning
        for row in rows
        for warning in _json_loads(row.get("import_warnings_json"), [])
    ]
    for warning in warnings:
        indicator = _warning_indicator(warning)
        if indicator == "shipment" and shipment_completed:
            continue
        risks[indicator] = "高"

    if not shipment_completed:
        shipment_days = [_days_to(row.get("latest_shipment_date")) for row in rows if row.get("latest_shipment_date")]
        min_shipment = min([item for item in shipment_days if item is not None], default=None)
        if min_shipment is None or min_shipment < 0:
            risks["shipment"] = "高"
        elif min_shipment <= 10 and risks["shipment"] != "高":
            risks["shipment"] = "中"

    follow_up_days = [_days_to(row.get("next_follow_up_date")) for row in rows if row.get("next_follow_up_date")]
    if any(item is not None and item <= 10 for item in follow_up_days):
        risks["reminder"] = "中"

    due_days = [_days_to(row.get("finance_due_date")) for row in rows if row.get("finance_due_date")]
    min_due = min([item for item in due_days if item is not None], default=None)
    missing_repay = not _group_has_value(rows, "tail_payment_date")
    if missing_repay:
        if min_due is None or min_due <= 7:
            risks["finance_due"] = "高"
        elif min_due <= 30 and risks["finance_due"] != "高":
            risks["finance_due"] = "中"
    else:
        risks["repayment"] = "中"
    return risks


def _group_risk(indicator_risks: Dict[str, str], stage: str) -> str:
    if stage == "已完成":
        return "已完成"
    if "高" in indicator_risks.values():
        return "高"
    if "中" in indicator_risks.values():
        return "中"
    return "低"


def _group_weekly_focus_reasons(rows: List[Dict[str, Any]], stage: str, risk: str) -> List[str]:
    if stage == "已完成":
        return []
    reasons = []
    if risk == "高":
        reasons.append("high_risk")
    if not _group_shipment_completed(rows):
        shipment_days = [_days_to(row.get("latest_shipment_date")) for row in rows if row.get("latest_shipment_date")]
        if any(item is not None and 0 <= item <= 10 for item in shipment_days):
            reasons.append("shipment_follow_up")
    follow_up_days = [_days_to(row.get("next_follow_up_date")) for row in rows if row.get("next_follow_up_date")]
    if any(item is not None and item <= 10 for item in follow_up_days):
        reasons.append("manual_follow_up")
    return reasons


def _group_repayment_timing(rows: List[Dict[str, Any]]) -> str:
    deltas = []
    for row in rows:
        due = _parse_date(row.get("finance_due_date"))
        repaid = _parse_date(row.get("tail_payment_date"))
        if due and repaid:
            deltas.append((repaid - due).days)
    if not deltas:
        return ""
    latest_delta = max(deltas)
    if latest_delta > 0:
        return f"逾期 {latest_delta} 天还款"
    if latest_delta < 0:
        return f"提前 {abs(latest_delta)} 天还款"
    return "按期还款"


def _group_next_action(rows: List[Dict[str, Any]], stage: str, risk: str) -> str:
    manual = next((_normalize_text(row.get("next_action")) for row in rows if _normalize_text(row.get("next_action"))), "")
    if manual and manual != "无":
        return manual
    if stage == "已完成":
        return "已闭环，保留历史查询"
    if len(rows) > 1:
        return "同一项次存在多行融资，请核对并分别跟进"
    if stage == "待放款":
        return "确认贷款行、金额和借款日期"
    if stage == "已放款待装船":
        return "优先联系工厂确认装船进度" if risk == "高" else "跟进工厂装船进度"
    if stage == "已装船待回款":
        return "确认银行交单安排"
    if stage == "已交单待回款":
        return "跟进交单后的回款和还款日"
    if stage == "已还款待结案":
        return "确认订单结案状态"
    return "确认当前订单状态"


def _build_progress_group(group_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows = sorted(group_rows, key=lambda row: (row.get("finance_due_date") or "9999-12-31", row.get("id") or 0))
    first = rows[0]
    lc = _lc_info(first)
    stage = _group_stage(rows)
    indicator_risks = _group_indicator_risks(rows, stage)
    risk = _group_risk(indicator_risks, stage)
    finance_total = sum(_money_value(row.get("finance_amount_actual"), row.get("finance_amount_expected"), row.get("planned_finance_amount")) for row in rows)
    due_dates = sorted([row.get("finance_due_date") for row in rows if row.get("finance_due_date")])
    latest_shipment_dates = sorted([row.get("latest_shipment_date") for row in rows if row.get("latest_shipment_date")])
    document_dates = sorted([row.get("document_submission_date") for row in rows if row.get("document_submission_date")])
    bill_dates = sorted([row.get("bill_of_lading_date") for row in rows if row.get("bill_of_lading_date")])
    repay_dates = sorted([row.get("tail_payment_date") for row in rows if row.get("tail_payment_date")])
    shipment_confirmed_dates = sorted([row.get("shipment_confirmed_date") for row in rows if row.get("shipment_confirmed_date")])
    shipment_confirmed_at = sorted([row.get("shipment_confirmed_at") for row in rows if row.get("shipment_confirmed_at")])
    follow_up_dates = sorted([row.get("next_follow_up_date") for row in rows if row.get("next_follow_up_date")])
    manager_note = next((_normalize_text(row.get("manager_note")) for row in rows if _normalize_text(row.get("manager_note"))), "")
    weekly_focus_reasons = _group_weekly_focus_reasons(rows, stage, risk)
    warnings = []
    for row in rows:
        warnings.extend(_json_loads(row.get("import_warnings_json"), []))
    return {
        "id": _group_key(first),
        "item_no": _item_no(first),
        "entity": first.get("overseas_entity") or "",
        "subsidiary": first.get("subsidiary") or "",
        "contract_no": first.get("purchase_contract_no") or "",
        "system_contract_no": first.get("system_contract_no") or "",
        "product": first.get("product_name") or "",
        "quantity": first.get("contract_quantity_mt"),
        "terminal_customer": first.get("terminal_customer") or "",
        "issuing_bank": lc.get("lc_bank") or "",
        "lc_no": lc.get("lc_no") or "",
        "lc_amount": lc.get("lc_amount"),
        "lc_expiry_date": lc.get("lc_expiry_date") or first.get("lc_latest_shipment_date") or "",
        "lc_type": lc.get("lc_type") or "",
        "transferable": lc.get("transferable") or "",
        "receiving_bank": lc.get("receiving_bank") or "",
        "latest_shipment_date": latest_shipment_dates[0] if latest_shipment_dates else "",
        "shipment_completed": _group_shipment_completed(rows),
        "shipment_confirmed_date": shipment_confirmed_dates[-1] if shipment_confirmed_dates else "",
        "shipment_confirmed_by": next((row.get("shipment_confirmed_by") for row in rows if row.get("shipment_confirmed_by")), ""),
        "shipment_confirmed_at": shipment_confirmed_at[-1] if shipment_confirmed_at else "",
        "vessel": next((row.get("vessel_voyage") for row in rows if row.get("vessel_voyage")), ""),
        "latest_due_date": due_dates[0] if due_dates else "",
        "bill_date": bill_dates[-1] if bill_dates else "",
        "document_date": document_dates[-1] if document_dates else "",
        "repay_date": repay_dates[-1] if repay_dates else "",
        "repayment_timing": _group_repayment_timing(rows),
        "stage": stage,
        "risk": risk,
        "indicator_risks": indicator_risks,
        "manager_note": manager_note,
        "next_follow_up_date": follow_up_dates[0] if follow_up_dates else "",
        "is_weekly_focus": bool(weekly_focus_reasons),
        "weekly_focus_reasons": weekly_focus_reasons,
        "next_action": _group_next_action(rows, stage, risk),
        "total_finance": finance_total,
        "financing_count": len(rows),
        "data_issue_count": len([warning for warning in warnings if _is_data_quality_warning(warning)]),
        "source_file": first.get("source_file") or "",
        "source_sheet": first.get("source_sheet") or "",
        "source_row_start": min((row.get("source_row_start") or 0 for row in rows), default=0),
        "source_row_end": max((row.get("source_row_end") or row.get("source_row_start") or 0 for row in rows), default=0),
        "financings": [
            {
                "id": row.get("id"),
                "bank": row.get("finance_bank") or "",
                "amount": _money_value(row.get("finance_amount_actual"), row.get("finance_amount_expected"), row.get("planned_finance_amount")),
                "borrow_date": row.get("finance_drawdown_date") or "",
                "original_due_date": _json_loads(row.get("source_json"), {}).get("original_due_date") or "",
                "new_due_date": _json_loads(row.get("source_json"), {}).get("new_due_date") or "",
                "extension_days": _json_loads(row.get("source_json"), {}).get("extension_days") or 0,
                "due_date": row.get("finance_due_date") or "",
                "rate": _json_loads(row.get("source_json"), {}).get("finance_rate"),
                "bill_date": row.get("bill_of_lading_date") or "",
                "document_date": row.get("document_submission_date") or "",
                "repay_date": row.get("tail_payment_date") or "",
                "status": row.get("finance_status") or row.get("business_status") or "",
                "next_action": row.get("next_action") or "",
                "source_file": row.get("source_file") or "",
                "source_sheet": row.get("source_sheet") or "",
                "source_row_start": row.get("source_row_start") or 0,
            }
            for row in rows
        ],
    }


def build_order_finance_progress_view(records: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    use_persisted_records = records is None
    records = records if records is not None else list_order_finance_records()
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in records:
        groups.setdefault(_group_key(row), []).append(row)
    contracts = [_build_progress_group(rows) for rows in groups.values()]
    contracts.sort(key=lambda item: (
        0 if item["risk"] == "高" else 1 if item["risk"] == "中" else 2 if item["risk"] == "低" else 3,
        item.get("latest_due_date") or "9999-12-31",
        item.get("item_no") or "",
    ))
    open_contracts = [item for item in contracts if item["stage"] != "已完成"]
    summary = {
        "open_contracts": len(open_contracts),
        "active_finance": sum(item["total_finance"] for item in open_contracts),
        "due_7d": len([item for item in open_contracts if (days := _days_to(item.get("latest_due_date"))) is not None and 0 <= days <= 7]),
        "due_30d": len([item for item in open_contracts if (days := _days_to(item.get("latest_due_date"))) is not None and 0 <= days <= 30]),
        "focus_risk": len([item for item in open_contracts if item["is_weekly_focus"]]),
        "financed_unshipped": len([item for item in open_contracts if item["stage"] == "已放款待装船"]),
        "documented_uncollected": len([item for item in open_contracts if item["stage"] == "已交单待回款"]),
        "collected_unrepaid": len([item for item in open_contracts if item["stage"] == "已还款待结案"]),
        "completed": len([item for item in contracts if item["stage"] == "已完成"]),
        "missing_milestones": len([item for item in open_contracts if not item.get("latest_shipment_date") or not item.get("document_date") or not item.get("repay_date")]),
        "data_issues": sum(item["data_issue_count"] for item in open_contracts),
        "total_contracts": len(contracts),
    }
    sync_status = get_order_finance_sync_status() if use_persisted_records else {}
    return {
        "summary": summary,
        "contracts": contracts,
        "sync_status": {
            "last_success_at": sync_status.get("last_success_at"),
            "changed_count": int(sync_status.get("changed_count") or 0),
        },
    }


def _sum_by(records: List[Dict[str, Any]], field: str) -> List[Dict[str, Any]]:
    totals: Dict[str, float] = {}
    for row in records:
        key = _normalize_text(row.get(field)) or "未填"
        totals[key] = totals.get(key, 0.0) + _money_value(row.get("finance_amount_actual"), row.get("finance_amount_expected"), row.get("planned_finance_amount"))
    return [{"name": key, "amount": value} for key, value in sorted(totals.items(), key=lambda item: item[1], reverse=True)]


def _workbook_capital_metadata(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    for row in records:
        source = _json_loads(row.get("source_json"), {})
        capital = source.get("workbook_capital") if isinstance(source, dict) else None
        if isinstance(capital, dict) and capital.get("banks"):
            return capital
    return {}


def build_order_finance_capital_view(records: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    records = records if records is not None else list_order_finance_records()
    open_rows = [row for row in records if _group_stage([row]) != "已完成"]
    bank_used: Dict[str, float] = {}
    for row in open_rows:
        bank = _normalize_text(row.get("finance_bank")) or "未填贷款行"
        bank_used[bank] = bank_used.get(bank, 0.0) + _money_value(row.get("finance_amount_actual"), row.get("finance_amount_expected"), row.get("planned_finance_amount"))
    capital_metadata = _workbook_capital_metadata(records)
    quota_banks = capital_metadata.get("banks") or DEFAULT_BANK_LIMITS
    bank_limit_map = {row["bank"]: row for row in quota_banks}
    all_banks = sorted(set(bank_limit_map) | set(bank_used))
    bank_usage = []
    for bank in all_banks:
        limit_row = bank_limit_map.get(bank, {"bank": bank, "limit": 0, "note": "", "lc_requirement": "", "bill_requirement": "", "finance_ratio": "", "term": ""})
        order_used = bank_used.get(bank, 0.0)
        limit = float(limit_row.get("limit") or 0)
        used = float(limit_row.get("used") if limit_row.get("used") is not None else order_used)
        available = limit_row.get("available")
        if available is None and limit:
            available = limit - used
        bank_usage.append({
            **limit_row,
            "used": used,
            "available": available,
            "usage_rate": used / limit if limit else None,
            "order_used": order_used,
            "difference": used - order_used,
        })
    bank_usage.sort(key=lambda item: item["used"], reverse=True)
    total_credit = float(capital_metadata.get("total_credit") or sum(float(row.get("limit") or 0) for row in quota_banks))
    order_used_credit = sum(bank_used.values())
    used_credit = float(capital_metadata.get("used_credit") if capital_metadata.get("used_credit") is not None else order_used_credit)
    available_credit = float(capital_metadata.get("available_credit") if capital_metadata.get("available_credit") is not None else total_credit - used_credit)
    buckets = [
        {"label": "7天内", "min": 0, "max": 7},
        {"label": "8-30天", "min": 8, "max": 30},
        {"label": "31-60天", "min": 31, "max": 60},
        {"label": "60天以上", "min": 61, "max": 99999},
        {"label": "已逾期", "min": -99999, "max": -1},
    ]
    due_buckets = []
    for bucket in buckets:
        rows = [row for row in open_rows if (days := _days_to(row.get("finance_due_date"))) is not None and bucket["min"] <= days <= bucket["max"]]
        due_buckets.append({
            "label": bucket["label"],
            "count": len(rows),
            "amount": sum(_money_value(row.get("finance_amount_actual"), row.get("finance_amount_expected"), row.get("planned_finance_amount")) for row in rows),
        })
    supplier_usage = _sum_by(open_rows, "subsidiary")
    entity_usage = _sum_by(open_rows, "overseas_entity")
    due_30_amount = sum(bucket["amount"] for bucket in due_buckets if bucket["label"] in {"7天内", "8-30天"})
    largest_bank = max((row["used"] for row in bank_usage), default=0)
    largest_supplier = max((row["amount"] for row in supplier_usage), default=0)
    return {
        "summary": {
            "total_credit": total_credit,
            "used_credit": used_credit,
            "available_credit": available_credit,
            "order_used_credit": order_used_credit,
            "usage_difference": used_credit - order_used_credit,
            "utilization_rate": used_credit / total_credit if total_credit else 0,
            "near_limit_banks": len([row for row in bank_usage if row.get("usage_rate") is not None and row["usage_rate"] >= 0.9]),
            "due_30_amount": due_30_amount,
            "largest_bank_share": largest_bank / used_credit if used_credit else 0,
            "largest_supplier_share": largest_supplier / used_credit if used_credit else 0,
        },
        "bank_usage": bank_usage,
        "entity_usage": entity_usage,
        "supplier_usage": supplier_usage,
        "due_buckets": due_buckets,
        "bank_details": [
            {
                "bank": row.get("finance_bank") or "未填贷款行",
                "item_no": _item_no(row),
                "contract_no": row.get("purchase_contract_no") or "",
                "subsidiary": row.get("subsidiary") or "",
                "amount": _money_value(row.get("finance_amount_actual"), row.get("finance_amount_expected"), row.get("planned_finance_amount")),
                "due_date": row.get("finance_due_date") or "",
                "status": row.get("finance_status") or row.get("business_status") or "",
            }
            for row in open_rows
        ],
    }


@router.post("/order-finance/import-local")
def import_order_finance_local(request: ImportLocalRequest, user: dict = Depends(order_finance_current_user)):
    order_finance_require_import(user)
    try:
        result = import_order_finance_directory(Path(request.directory), imported_by=user["name"])
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.post("/order-finance/import-file")
async def import_order_finance_file(
    request: Request,
    file_name: str,
    user: dict = Depends(order_finance_current_user),
):
    order_finance_require_import(user)
    try:
        return await import_order_finance_upload(request, file_name=file_name, imported_by=user["name"])
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/order-finance/records")
def order_finance_records(
    limit: int = 5000,
    offset: int = 0,
    user: dict = Depends(order_finance_current_user),
):
    order_finance_require_view(user)
    result = list_order_finance_records_page(limit=limit, offset=offset)
    return {"summary": summarize_order_finance(result["records"]), **result}


@router.get("/order-finance/progress")
def order_finance_progress(user: dict = Depends(order_finance_current_user)):
    order_finance_require_view(user)
    return build_order_finance_progress_view()


@router.get("/order-finance/capital")
def order_finance_capital(user: dict = Depends(order_finance_current_user)):
    require_permission(user, "order_finance.capital", "view")
    return build_order_finance_capital_view()


@router.get("/order-finance/records/{record_id}")
def order_finance_record(record_id: int, user: dict = Depends(order_finance_current_user)):
    order_finance_require_view(user)
    try:
        return get_order_finance_record(record_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="记录不存在") from exc


@router.post("/order-finance/records/manual")
def order_finance_create_manual(request: ManualOrderFinanceRequest, user: dict = Depends(order_finance_current_user)):
    order_finance_require_edit(user)
    changes = request.model_dump(exclude_unset=True) if hasattr(request, "model_dump") else request.dict(exclude_unset=True)
    try:
        duplicates = find_order_finance_duplicates(changes)
        record = create_manual_order_finance_record(changes, created_by=user["name"])
    except DuplicateOrderFinanceError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "existing": exc.existing}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"record": record, "duplicate_candidates": duplicates["similar"]}


@router.patch("/order-finance/records/{record_id}/management")
def order_finance_update_management(
    record_id: int,
    request: ManagementUpdateRequest,
    user: dict = Depends(order_finance_current_user),
):
    order_finance_require_edit(user)
    try:
        changes = request.model_dump(exclude_unset=True) if hasattr(request, "model_dump") else request.dict(exclude_unset=True)
        return update_management_fields(record_id, changes, updated_by=user["name"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="记录不存在") from exc


@router.patch("/order-finance/contracts/{item_no}/shipment-confirmation")
def order_finance_shipment_confirmation(
    item_no: str,
    request: ShipmentConfirmationRequest,
    user: dict = Depends(order_finance_current_user),
):
    order_finance_require_edit(user)
    try:
        return set_shipment_confirmation(
            item_no,
            confirmed=request.confirmed,
            shipment_confirmed_date=request.shipment_confirmed_date,
            updated_by=user["name"],
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="项次不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/order-finance/contracts/{item_no}/reminder")
def order_finance_contract_reminder(
    item_no: str,
    request: ContractReminderRequest,
    user: dict = Depends(order_finance_current_user),
):
    order_finance_require_edit(user)
    try:
        return set_contract_reminder(
            item_no,
            manager_note=request.manager_note,
            next_follow_up_date=request.next_follow_up_date,
            updated_by=user["name"],
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="项次不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
