"""订单融资进度监控。

Excel 台账解析、导入、查询和管理端计划字段维护。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import xlrd
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from . import db


router = APIRouter()

SUBSIDIARIES = ["东钢", "北满", "承德", "抚顺", "西林", "阿城"]
LOCAL_DEFAULT_LEDGER_DIR = Path("/Users/wangjingze/建龙/贸易处/订单融资合同汇总")
ORDER_FINANCE_MODULE = "order_finance_progress"

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
}


class ImportLocalRequest(BaseModel):
    directory: str = str(LOCAL_DEFAULT_LEDGER_DIR)


class ManagementUpdateRequest(BaseModel):
    planned_drawdown_date: Optional[str] = None
    planned_finance_amount: Optional[float] = None
    amount_adjustment_note: Optional[str] = None
    repayment_requirement: Optional[str] = None
    repayment_requirement_status: Optional[str] = None
    next_action: Optional[str] = None
    next_follow_up_date: Optional[str] = None
    manager_note: Optional[str] = None


async def order_finance_current_user(authorization: Optional[str] = Header(default=None)):
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def order_finance_require_edit(user: dict):
    if user["role"] == "管理员":
        return
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(
            cur,
            "SELECT can_edit FROM module_permissions WHERE user_id = ? AND module_code = ?",
            (user["id"], ORDER_FINANCE_MODULE),
        ).fetchone()
    if not row or not row["can_edit"]:
        raise HTTPException(status_code=403, detail="没有编辑权限")


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


def _parse_date(value: Any) -> Optional[date]:
    text = _normalize_date(value)
    if not text or len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


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
        "summary": {"record_count": len(records), "warning_count": sum(len(json.loads(r["import_warnings_json"])) for r in records)},
    }


def parse_order_finance_directory(directory: Path | str) -> Dict[str, Any]:
    base = Path(directory)
    if not base.exists() or not base.is_dir():
        raise ValueError(f"目录不存在：{base}")
    files = sorted(path for path in base.iterdir() if path.suffix.lower() == ".xls" and not path.name.startswith("~$"))
    records: List[Dict[str, Any]] = []
    file_results = []
    for path in files:
        result = parse_order_finance_workbook(path)
        records.extend(result["records"])
        file_results.append({"file": result["file"], "sheet": result["sheet"], **result["summary"]})
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
                    f"UPDATE order_finance_progress SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    tuple(params),
                )
                updated += 1
            else:
                insert_fields = FACT_FIELDS + [
                    "planned_drawdown_date", "planned_finance_amount", "amount_adjustment_note",
                    "repayment_requirement", "repayment_requirement_status", "next_action",
                    "next_follow_up_date", "manager_note", "manual_override_fields",
                    "management_plan_json", "manual_change_log_json",
                ]
                values = [record.get(field) for field in FACT_FIELDS]
                values.extend([
                    None, None, "", "", "", record.get("next_action", ""), "", "",
                    "[]", "{}", "[]",
                ])
                placeholders = ", ".join("?" for _ in insert_fields)
                db._exec(
                    cur,
                    f"INSERT INTO order_finance_progress ({', '.join(insert_fields)}) VALUES ({placeholders})",
                    tuple(values),
                )
                inserted += 1
    return {"inserted": inserted, "updated": updated}


def import_order_finance_directory(directory: Path | str, imported_by: str = "") -> Dict[str, Any]:
    parsed = parse_order_finance_directory(directory)
    changes = upsert_order_finance_records(parsed["records"], imported_by=imported_by)
    parsed["summary"].update(changes)
    return parsed


def list_order_finance_records() -> List[Dict[str, Any]]:
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(
            cur,
            """
            SELECT *
            FROM order_finance_progress
            WHERE is_archived = 0
            ORDER BY
                CASE risk_level WHEN '高' THEN 1 WHEN '中' THEN 2 ELSE 3 END,
                COALESCE(finance_due_date, '9999-12-31'),
                id
            """,
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_order_finance_record(record_id: int) -> Dict[str, Any]:
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(cur, "SELECT * FROM order_finance_progress WHERE id = ?", (record_id,)).fetchone()
    if not row:
        raise KeyError(record_id)
    return _row_to_dict(row)


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


def summarize_order_finance(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    active = [row for row in records if row.get("business_status") != "已结算"]
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


@router.post("/order-finance/import-local")
def import_order_finance_local(request: ImportLocalRequest, user: dict = Depends(order_finance_current_user)):
    order_finance_require_edit(user)
    try:
        result = import_order_finance_directory(Path(request.directory), imported_by=user["name"])
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.get("/order-finance/records")
def order_finance_records(user: dict = Depends(order_finance_current_user)):
    records = list_order_finance_records()
    return {"summary": summarize_order_finance(records), "records": records}


@router.get("/order-finance/records/{record_id}")
def order_finance_record(record_id: int, user: dict = Depends(order_finance_current_user)):
    try:
        return get_order_finance_record(record_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="记录不存在") from exc


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
