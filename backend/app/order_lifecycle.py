"""订单全流程管理（测试版）。

本模块与旧的 ``order_finance_progress`` 平行存在：旧表和旧页面继续服务原有
订单融资功能；本模块保存一张业务主卡及其融资、合同、船舶、单据、回款等子记录。
来源解析默认只读，只有显式调用导入函数时才写入测试环境数据库。
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from openpyxl import load_workbook
from pydantic import BaseModel, Field

from . import db
from .order_finance import (
    _get,
    _normalize_date,
    _normalize_text,
    _parse_date,
    _subsidiary_from_filename,
    _to_float,
    parse_order_finance_workbook,
)
from .permissions import can, require_permission


router = APIRouter()
ORDER_LIFECYCLE_MODULE = "order_lifecycle_progress"
PERMISSION_RESOURCE = "order_finance.records"
WPS_RAW_SHEETS = ("YOLANDA", "JLHK", "天津建龙")
MAIL_MILLS = ("阿城", "北满", "承德", "东钢", "抚顺", "西林")
TABLES = (
    "order_lifecycle_businesses",
    "order_lifecycle_contracts",
    "order_lifecycle_financings",
    "order_lifecycle_vessels",
    "order_lifecycle_documents",
    "order_lifecycle_customer_receipts",
    "order_lifecycle_bank_repayments",
    "order_lifecycle_source_batches",
    "order_lifecycle_source_records",
    "order_lifecycle_manual_overrides",
    "order_lifecycle_data_anomalies",
    "order_lifecycle_sync_state",
)

MANUAL_OVERRIDE_FIELDS = {
    "trade_entity",
    "supplier_steel_mill",
    "terminal_customer",
    "product_name",
    "contract_quantity_mt",
}


def _id_sql() -> str:
    return "SERIAL PRIMARY KEY" if db._is_pg() else "INTEGER PRIMARY KEY AUTOINCREMENT"


def initialize_schema(conn) -> None:
    """Create only the new lifecycle tables; never alters old order-finance facts."""
    ident = _id_sql()
    cur = conn.cursor()
    statements = [
        f"""
        CREATE TABLE IF NOT EXISTS order_lifecycle_businesses (
            id {ident},
            business_uid TEXT NOT NULL UNIQUE,
            business_key TEXT NOT NULL UNIQUE,
            business_no TEXT NOT NULL,
            business_type TEXT NOT NULL,
            trade_entity TEXT,
            supplier_steel_mill TEXT,
            terminal_customer TEXT,
            product_name TEXT,
            contract_quantity_mt DOUBLE PRECISION,
            status TEXT NOT NULL DEFAULT '待确认',
            port_status TEXT NOT NULL DEFAULT '待确认',
            port_confirmed_date TEXT,
            port_confirmed_by TEXT,
            shipment_status TEXT NOT NULL DEFAULT '待确认',
            shipment_confirmed_date TEXT,
            shipment_confirmed_by TEXT,
            risk_level TEXT NOT NULL DEFAULT '低风险',
            risk_reasons_json TEXT NOT NULL DEFAULT '[]',
            anomaly_count INTEGER NOT NULL DEFAULT 0,
            fcr INTEGER NOT NULL DEFAULT 0,
            is_cancelled INTEGER NOT NULL DEFAULT 0,
            cancelled_at TEXT,
            source_type TEXT NOT NULL,
            source_snapshot_date TEXT,
            source_version TEXT,
            source_record_key TEXT,
            source_presence_hash TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS order_lifecycle_contracts (
            id {ident},
            business_id INTEGER NOT NULL,
            contract_no TEXT,
            purchase_contract_no TEXT,
            system_contract_no TEXT,
            buyer TEXT,
            seller TEXT,
            quantity_mt DOUBLE PRECISION,
            source_key TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(business_id, source_key)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS order_lifecycle_financings (
            id {ident},
            business_id INTEGER NOT NULL,
            bank TEXT,
            amount DOUBLE PRECISION,
            financing_date TEXT,
            original_due_date TEXT,
            extended_due_date TEXT,
            repayment_date TEXT,
            repayment_status TEXT,
            source_key TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(business_id, source_key)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS order_lifecycle_vessels (
            id {ident},
            business_id INTEGER NOT NULL,
            vessel_name TEXT,
            imo TEXT,
            loading_port TEXT,
            discharge_port TEXT,
            eta TEXT,
            etb TEXT,
            estimated_discharge_date TEXT,
            source_key TEXT NOT NULL,
            source TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(business_id, source_key)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS order_lifecycle_documents (
            id {ident},
            business_id INTEGER NOT NULL,
            document_type TEXT NOT NULL,
            document_date TEXT,
            source_key TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(business_id, source_key)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS order_lifecycle_customer_receipts (
            id {ident},
            business_id INTEGER NOT NULL,
            receipt_date TEXT,
            amount DOUBLE PRECISION,
            currency TEXT,
            source_key TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(business_id, source_key)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS order_lifecycle_bank_repayments (
            id {ident},
            business_id INTEGER NOT NULL,
            financing_id INTEGER,
            repayment_date TEXT,
            amount DOUBLE PRECISION,
            source_key TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(business_id, source_key)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS order_lifecycle_source_batches (
            id {ident},
            source_type TEXT NOT NULL,
            source_locator TEXT,
            source_version TEXT,
            snapshot_date TEXT,
            source_hash TEXT,
            source_key_set_hash TEXT,
            deletion_candidate_hash TEXT,
            deletion_candidate_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            record_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            started_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS order_lifecycle_source_records (
            id {ident},
            batch_id INTEGER NOT NULL,
            source_type TEXT NOT NULL,
            source_key TEXT NOT NULL,
            business_key TEXT NOT NULL,
            source_file TEXT,
            source_sheet TEXT,
            source_row INTEGER,
            raw_json TEXT NOT NULL DEFAULT '{{}}',
            normalized_json TEXT NOT NULL DEFAULT '{{}}',
            raw_hash TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(batch_id, source_key)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS order_lifecycle_manual_overrides (
            id {ident},
            business_id INTEGER NOT NULL,
            field_name TEXT NOT NULL,
            value_json TEXT NOT NULL,
            note TEXT,
            modified_by TEXT NOT NULL,
            modified_at TEXT DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER NOT NULL DEFAULT 1,
            UNIQUE(business_id, field_name)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS order_lifecycle_data_anomalies (
            id {ident},
            business_id INTEGER NOT NULL,
            anomaly_key TEXT NOT NULL,
            anomaly_type TEXT NOT NULL,
            description TEXT NOT NULL,
            details_json TEXT NOT NULL DEFAULT '{{}}',
            status TEXT NOT NULL DEFAULT 'open',
            first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
            resolved_at TEXT,
            resolved_by TEXT,
            UNIQUE(business_id, anomaly_key)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS order_lifecycle_sync_state (
            id {ident},
            wps_last_success_at TEXT,
            wps_last_error TEXT,
            email_last_success_at TEXT,
            email_last_error TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            CHECK (id = 1)
        )
        """,
    ]
    if db._is_pg():
        for statement in statements:
            cur.execute(statement)
        for name, col_type in (("port_status", "TEXT NOT NULL DEFAULT '待确认'"), ("port_confirmed_date", "TEXT"), ("port_confirmed_by", "TEXT"), ("shipment_status", "TEXT NOT NULL DEFAULT '待确认'"), ("shipment_confirmed_date", "TEXT"), ("shipment_confirmed_by", "TEXT")):
            cur.execute(f"ALTER TABLE order_lifecycle_businesses ADD COLUMN IF NOT EXISTS {name} {col_type}")
    else:
        for statement in statements:
            conn.execute(statement)
        existing_columns = {row["name"] for row in conn.execute("PRAGMA table_info(order_lifecycle_businesses)").fetchall()}
        for name, col_type in (("port_status", "TEXT NOT NULL DEFAULT '待确认'"), ("port_confirmed_date", "TEXT"), ("port_confirmed_by", "TEXT"), ("shipment_status", "TEXT NOT NULL DEFAULT '待确认'"), ("shipment_confirmed_date", "TEXT"), ("shipment_confirmed_by", "TEXT")):
            if name not in existing_columns:
                conn.execute(f"ALTER TABLE order_lifecycle_businesses ADD COLUMN {name} {col_type}")
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_ol_business_type_status ON order_lifecycle_businesses(business_type, status)",
        "CREATE INDEX IF NOT EXISTS idx_ol_business_risk ON order_lifecycle_businesses(risk_level)",
        "CREATE INDEX IF NOT EXISTS idx_ol_business_source ON order_lifecycle_businesses(source_type, source_record_key)",
        "CREATE INDEX IF NOT EXISTS idx_ol_financing_business ON order_lifecycle_financings(business_id)",
        "CREATE INDEX IF NOT EXISTS idx_ol_receipt_business ON order_lifecycle_customer_receipts(business_id)",
        "CREATE INDEX IF NOT EXISTS idx_ol_anomaly_business_status ON order_lifecycle_data_anomalies(business_id, status)",
    ]
    for statement in indexes:
        cur.execute(statement) if db._is_pg() else conn.execute(statement)
    if db._is_pg():
        cur.execute(
            "INSERT INTO order_lifecycle_sync_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING"
        )
        db._secure_postgres_tables(cur, TABLES)
    else:
        conn.execute("INSERT OR IGNORE INTO order_lifecycle_sync_state (id) VALUES (1)")
    conn.commit()


def sync_order_lifecycle_permissions(cur) -> None:
    """Copy existing order-finance permissions for the new page only when missing."""
    users = db._exec(cur, "SELECT id FROM users").fetchall()
    for user in users:
        old = db._exec(
            cur,
            "SELECT can_view, can_edit, can_sensitive FROM module_permissions WHERE user_id = ? AND module_code = ?",
            (user["id"], "order_finance_progress"),
        ).fetchone()
        if not old:
            continue
        db._exec(
            cur,
            """
            INSERT OR IGNORE INTO module_permissions
                (user_id, module_code, can_view, can_edit, can_sensitive)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user["id"], ORDER_LIFECYCLE_MODULE, old["can_view"], old["can_edit"], old["can_sensitive"]),
        )


def _compact(value: Any) -> str:
    return re.sub(r"[\s\-_/—–]+", "", _normalize_text(value)).upper()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _first_nonempty(values: Iterable[Any]) -> Any:
    for value in values:
        if _normalize_text(value):
            return value
    return None


def _short_supplier(value: Any) -> str:
    text = _normalize_text(value)
    aliases = (
        ("北满", "北满"), ("BEIMAN", "北满"), ("东钢", "东钢"), ("DONG", "东钢"),
        ("承德", "承德"), ("CHENGDE", "承德"), ("抚顺", "抚顺"), ("FUSHUN", "抚顺"),
        ("西林", "西林"), ("XILIN", "西林"), ("阿城", "阿城"), ("ACHENG", "阿城"),
    )
    upper = text.upper()
    for needle, label in aliases:
        if needle in text or needle in upper:
            return label
    return text


def _header_text(value: Any) -> str:
    return re.sub(r"\s+", "", _normalize_text(value)).replace("\n", "")


def _header_index(headers: list[Any], aliases: tuple[str, ...], start: int = 0, end: Optional[int] = None) -> Optional[int]:
    end = len(headers) if end is None else min(end, len(headers))
    normalized = [_header_text(value) for value in headers]
    for alias in aliases:
        alias_text = _header_text(alias)
        for index in range(start, end):
            if alias_text and alias_text in normalized[index]:
                return index
    return None


def _row_value(row: list[Any], headers: list[Any], aliases: tuple[str, ...], start: int = 0, end: Optional[int] = None) -> Any:
    return _get(row, _header_index(headers, aliases, start, end))


def _business_key(source_type: str, sheet: str, item: str, purchase: Any, system: Any, row_no: int) -> str:
    identity = _compact(item) or _compact(purchase) or _compact(system) or str(row_no)
    return f"{source_type}:{_compact(sheet)}:{identity}"


def _source_record_key(source_type: str, sheet: str, row_no: int, item: str) -> str:
    return f"{source_type}:{_compact(sheet)}:{row_no}:{_compact(item)}"


def _empty_record() -> dict[str, Any]:
    return {
        "business_type": "融资",
        "business_no": "",
        "business_key": "",
        "trade_entity": "",
        "supplier_steel_mill": "",
        "terminal_customer": "",
        "product_name": "",
        "contract_quantity_mt": None,
        "source_type": "",
        "source_snapshot_date": date.today().isoformat(),
        "source_version": "",
        "source_record_key": "",
        "contracts": [],
        "financings": [],
        "vessels": [],
        "documents": [],
        "customer_receipts": [],
        "bank_repayments": [],
        "raw": {},
    }


def parse_wps_workbook(path: Path | str) -> dict[str, Any]:
    """Parse only the three authoritative WPS raw sheets into standard records."""
    path = Path(path)
    if not path.exists():
        raise ValueError(f"WPS文件不存在：{path}")
    workbook = load_workbook(path, read_only=True, data_only=True)
    snapshot_date = datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()
    version = _hash_file(path)
    records: list[dict[str, Any]] = []
    for sheet_name in WPS_RAW_SHEETS:
        if sheet_name not in workbook.sheetnames:
            continue
        sheet = workbook[sheet_name]
        header_row_no = None
        headers: list[Any] = []
        for row_no, values in enumerate(sheet.iter_rows(min_row=1, max_row=min(sheet.max_row, 20), values_only=True), 1):
            row_headers = list(values)
            if _header_index(row_headers, ("项次",)) is not None:
                header_row_no, headers = row_no, row_headers
                break
        if not header_row_no:
            continue
        groups: dict[str, dict[str, Any]] = {}
        current_item = ""
        for row_no, values in enumerate(sheet.iter_rows(min_row=header_row_no + 1, values_only=True), header_row_no + 1):
            row = list(values)
            item = _normalize_text(_row_value(row, headers, ("项次",))) or current_item
            if not item or _compact(item) in {"TOTAL", "合计"}:
                continue
            current_item = item
            purchase = _row_value(row, headers, ("合同号",), 0, 24)
            system = _row_value(row, headers, ("系统合同号",), 0, 24)
            key = _business_key("wps", sheet_name, item, purchase, system, row_no)
            record = groups.setdefault(key, _empty_record())
            record.update({
                "business_type": "融资",
                "business_no": item,
                "business_key": key,
                "trade_entity": {"YOLANDA": "YOLANDA", "JLHK": "香港建龙", "天津建龙": "天津建龙"}.get(sheet_name, sheet_name),
                "source_type": "wps",
                "source_snapshot_date": snapshot_date,
                "source_version": version,
                "source_record_key": _source_record_key("wps", sheet_name, row_no, item),
                "product_name": _first_nonempty((record.get("product_name"), _row_value(row, headers, ("品名", "合同品名")))),
                "supplier_steel_mill": _short_supplier(_first_nonempty((record.get("supplier_steel_mill"), _row_value(row, headers, ("供应商",))))),
                "terminal_customer": _first_nonempty((record.get("terminal_customer"), _row_value(row, headers, ("合同买方",), 24))),
                "contract_quantity_mt": _first_nonempty((record.get("contract_quantity_mt"), _to_float(_row_value(row, headers, ("合同数量",))))),
                "raw": {"sheet": sheet_name, "row": row, "headers": headers},
            })
            purchase = _normalize_text(purchase)
            system = _normalize_text(system)
            sales_system = _normalize_text(_row_value(row, headers, ("系统合同号",), 24))
            sales_contract = _normalize_text(_row_value(row, headers, ("双方合同号",), 24))
            buyer = _normalize_text(_row_value(row, headers, ("合同买方",), 24))
            seller = _normalize_text(_row_value(row, headers, ("供应商",), 0, 24))
            if any((purchase, system, sales_system, sales_contract, buyer, seller)):
                contract_key = ":".join((str(row_no), purchase, system, sales_system, sales_contract))
                if not any(item.get("source_key") == contract_key for item in record["contracts"]):
                    record["contracts"].append({
                        "contract_no": sales_contract or system,
                        "purchase_contract_no": purchase,
                        "system_contract_no": system or sales_system,
                        "buyer": buyer,
                        "seller": seller,
                        "quantity_mt": _to_float(_row_value(row, headers, ("合同数量",))),
                        "source_key": contract_key,
                    })
            bank = _normalize_text(_row_value(row, headers, ("贷款行",), 0, 24))
            amount = _to_float(_row_value(row, headers, ("贷款人民币金额",), 0, 24))
            financing_date = _normalize_date(_row_value(row, headers, ("借款日期",), 0, 24))
            original_due = _normalize_date(_row_value(row, headers, ("原到期日",), 0, 24))
            extended_due = _normalize_date(_row_value(row, headers, ("新到期日",), 0, 24))
            repayment_date = _normalize_date(_row_value(row, headers, ("还款日",), 0, 24))
            loan_status = _normalize_text(_row_value(row, headers, ("贷款状态",), 0, 24))
            if bank or amount is not None or financing_date or original_due or extended_due or repayment_date or loan_status:
                finance_key = f"{row_no}:{bank}:{amount}:{financing_date}"
                if not any(item.get("source_key") == finance_key for item in record["financings"]):
                    record["financings"].append({
                        "bank": bank,
                        "amount": amount,
                        "financing_date": financing_date,
                        "original_due_date": original_due,
                        "extended_due_date": extended_due,
                        "repayment_date": repayment_date,
                        "repayment_status": loan_status,
                        "source_key": finance_key,
                    })
            vessel = _normalize_text(_row_value(row, headers, ("船名",), 24))
            if vessel:
                record["vessels"].append({
                    "vessel_name": vessel,
                    "imo": "",
                    "loading_port": _normalize_text(_row_value(row, headers, ("起运港",))),
                    "discharge_port": _normalize_text(_row_value(row, headers, ("目的港",))),
                    "eta": "",
                    "etb": "",
                    "estimated_discharge_date": "",
                    "source_key": f"vessel:{row_no}:{vessel}",
                    "source": "wps",
                })
            document_date = _normalize_date(_row_value(row, headers, ("交单日期",), 24))
            if document_date:
                record["documents"].append({"document_type": "交单", "document_date": document_date, "source_key": f"doc:{row_no}:{document_date}"})
            receipt_date = _normalize_date(_row_value(row, headers, ("收汇日期",), 24))
            if receipt_date:
                record["customer_receipts"].append({
                    "receipt_date": receipt_date,
                    "amount": _to_float(_row_value(row, headers, ("交单金额",), 24)),
                    "currency": _normalize_text(_row_value(row, headers, ("合同币别",), 24)),
                    "source_key": f"receipt:{row_no}:{receipt_date}",
                })
        records.extend(groups.values())
    return {
        "source_type": "wps",
        "source_locator": str(path),
        "source_version": version,
        "snapshot_date": snapshot_date,
        "source_hash": version,
        "records": records,
        "summary": {"record_count": len(records), "sheet_count": len({r["raw"].get("sheet") for r in records})},
    }


def _mail_xlsx_records(path: Path) -> list[dict[str, Any]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook[workbook.sheetnames[0]]
    header_row_no = None
    headers: list[Any] = []
    for row_no, values in enumerate(sheet.iter_rows(min_row=1, max_row=min(sheet.max_row, 20), values_only=True), 1):
        row_headers = list(values)
        if _header_index(row_headers, ("序号", "货物品名", "货物")) is not None:
            header_row_no, headers = row_no, row_headers
            break
    if not header_row_no:
        return []
    records = []
    current = ""
    for row_no, values in enumerate(sheet.iter_rows(min_row=header_row_no + 1, values_only=True), header_row_no + 1):
        row = list(values)
        sequence = _normalize_text(_row_value(row, headers, ("序号",))) or current
        if not sequence:
            continue
        current = sequence
        records.append(_mail_row_record(path, sheet.title, headers, row, row_no, sequence))
    return records


def _mail_row_record(path: Path, sheet_name: str, headers: list[Any], row: list[Any], row_no: int, sequence: str) -> dict[str, Any]:
    subsidiary = _subsidiary_from_filename(path.name)
    product = _normalize_text(_row_value(row, headers, ("货物品名", "货物")))
    purchase = _normalize_text(_row_value(row, headers, ("采/销合同号", "合同号")))
    system = _normalize_text(_row_value(row, headers, ("YOLANDA合同号", "Yolanda采/销合同号", "Yolanda/Jianlong采/销合同号")))
    amount = _to_float(_row_value(row, headers, ("实际放款金额", "融资金额", "应放款金额", "放款金额")))
    financing_date = _normalize_date(_row_value(row, headers, ("放款日期",)))
    bank = _normalize_text(_row_value(row, headers, ("贷款行", "融资银行", "银行")))
    business_key = _business_key("email", subsidiary, sequence, purchase, system, row_no)
    receipt_date = _normalize_date(_row_value(row, headers, ("回款日期",)))
    doc_date = _normalize_date(_row_value(row, headers, ("交单日期",)))
    vessel = _normalize_text(_row_value(row, headers, ("船名航次", "船名")))
    record = _empty_record()
    record.update({
        "business_type": "融资" if any((amount is not None, financing_date, bank)) else "过单",
        "business_no": sequence,
        "business_key": business_key,
        "trade_entity": "YOLANDA",
        "supplier_steel_mill": subsidiary,
        "terminal_customer": _normalize_text(_row_value(row, headers, ("买方",))),
        "product_name": product,
        "contract_quantity_mt": _to_float(_row_value(row, headers, ("合同数量",))),
        "source_type": "email",
        "source_snapshot_date": datetime.fromtimestamp(path.stat().st_mtime).date().isoformat(),
        "source_record_key": _source_record_key("email", subsidiary, row_no, sequence),
        "raw": {"sheet": sheet_name, "row": row, "headers": headers, "file": path.name},
    })
    if purchase or system:
        record["contracts"].append({
            "contract_no": purchase or system,
            "purchase_contract_no": purchase,
            "system_contract_no": system,
            "buyer": _normalize_text(_row_value(row, headers, ("买方",))),
            "seller": _normalize_text(_row_value(row, headers, ("卖方",))),
            "quantity_mt": record["contract_quantity_mt"],
            "source_key": f"contract:{row_no}:{purchase}:{system}",
        })
    if record["business_type"] == "融资":
        record["financings"].append({
            "bank": bank,
            "amount": amount,
            "financing_date": financing_date,
            "original_due_date": _normalize_date(_row_value(row, headers, ("放款到期日期", "融资到期日"))),
            "extended_due_date": _normalize_date(_row_value(row, headers, ("新到期日",))),
            "repayment_date": _normalize_date(_row_value(row, headers, ("还款日期", "还款日"))),
            "repayment_status": _normalize_text(_row_value(row, headers, ("贷款状态", "状态"))),
            "source_key": f"finance:{row_no}:{bank}:{amount}:{financing_date}",
        })
    if vessel:
        record["vessels"].append({
            "vessel_name": vessel,
            "imo": "",
            "loading_port": _normalize_text(_row_value(row, headers, ("起运港",))),
            "discharge_port": _normalize_text(_row_value(row, headers, ("目的港", "卸港"))),
            "eta": "",
            "etb": "",
            "estimated_discharge_date": "",
            "source_key": f"vessel:{row_no}:{vessel}",
            "source": "email",
        })
    if doc_date:
        record["documents"].append({"document_type": "交单", "document_date": doc_date, "source_key": f"doc:{row_no}:{doc_date}"})
    if receipt_date:
        record["customer_receipts"].append({"receipt_date": receipt_date, "amount": None, "currency": "", "source_key": f"receipt:{row_no}:{receipt_date}"})
    return record


def parse_email_batch(directory: Path | str) -> dict[str, Any]:
    """Parse a complete six-mill email batch; missing attachment blocks the batch."""
    base = Path(directory)
    if not base.exists() or not base.is_dir():
        raise ValueError(f"邮件台账目录不存在：{base}")
    files = [p for p in sorted(base.iterdir()) if p.suffix.lower() in {".xls", ".xlsx"} and not p.name.startswith("~$")]
    found = {mill for mill in MAIL_MILLS if any(mill in path.name for path in files)}
    missing = [mill for mill in MAIL_MILLS if mill not in found]
    if missing:
        raise ValueError(f"邮件台账批次不完整，缺少：{'、'.join(missing)}")
    records: list[dict[str, Any]] = []
    file_results = []
    for path in files:
        if path.suffix.lower() == ".xls":
            result = parse_order_finance_workbook(path)
            parsed = [_standardize_legacy_mail_record(item, path) for item in result.get("records", [])]
        else:
            parsed = _mail_xlsx_records(path)
        records.extend(parsed)
        file_results.append({"file": path.name, "record_count": len(parsed)})
    version = hashlib.sha256("|".join(f"{p.name}:{_hash_file(p)}" for p in files).encode()).hexdigest()
    snapshot_date = max(datetime.fromtimestamp(p.stat().st_mtime).date().isoformat() for p in files)
    return {
        "source_type": "email",
        "source_locator": str(base),
        "source_version": version,
        "snapshot_date": snapshot_date,
        "source_hash": version,
        "records": records,
        "files": file_results,
        "summary": {"record_count": len(records), "files_read": len(files)},
    }


def _standardize_legacy_mail_record(item: dict[str, Any], path: Path) -> dict[str, Any]:
    sequence = _normalize_text(item.get("business_key")) or str(item.get("source_row_start") or "")
    purchase = _normalize_text(item.get("purchase_contract_no"))
    system = _normalize_text(item.get("system_contract_no"))
    item_no = f"{_subsidiary_from_filename(path.name)}-{item.get('source_row_start') or sequence}"
    key = _business_key("email", _subsidiary_from_filename(path.name), item_no, purchase, system, int(item.get("source_row_start") or 0))
    record = _empty_record()
    record.update({
        "business_type": "融资" if any((item.get("finance_amount_actual") is not None, item.get("finance_drawdown_date"), item.get("finance_bank"))) else "过单",
        "business_no": item_no,
        "business_key": key,
        "trade_entity": "YOLANDA",
        "supplier_steel_mill": _subsidiary_from_filename(path.name),
        "terminal_customer": _normalize_text(item.get("terminal_customer") or item.get("buyer")),
        "product_name": _normalize_text(item.get("product_name")),
        "contract_quantity_mt": item.get("contract_quantity_mt"),
        "source_type": "email",
        "source_snapshot_date": item.get("source_snapshot_date") or date.today().isoformat(),
        "source_record_key": _source_record_key("email", path.name, int(item.get("source_row_start") or 0), item_no),
        "raw": item,
    })
    if purchase or system:
        record["contracts"].append({
            "contract_no": purchase or system,
            "purchase_contract_no": purchase,
            "system_contract_no": system,
            "buyer": _normalize_text(item.get("buyer")),
            "seller": _normalize_text(item.get("seller")),
            "quantity_mt": item.get("contract_quantity_mt"),
            "source_key": f"contract:{item.get('source_row_start') or 0}:{purchase}:{system}",
        })
    if record["business_type"] == "融资":
        record["financings"].append({
            "bank": _normalize_text(item.get("finance_bank")),
            "amount": item.get("finance_amount_actual") if item.get("finance_amount_actual") is not None else item.get("finance_amount_expected"),
            "financing_date": _normalize_date(item.get("finance_drawdown_date")),
            "original_due_date": _normalize_date(item.get("finance_due_date")),
            "extended_due_date": _normalize_date(item.get("finance_due_date")),
            "repayment_date": _normalize_date(item.get("tail_payment_date")) if "还款" in _normalize_text(item.get("remark")) else "",
            "repayment_status": "已还款" if "还款" in _normalize_text(item.get("remark")) else "",
            "source_key": f"finance:{item.get('source_row_start') or 0}:{item.get('finance_bank')}:{item.get('finance_amount_actual')}",
        })
    for child_index, child in enumerate(_safe_json(item.get("sales_contracts_json"), []), 1):
        child_collection = _normalize_date(child.get("collection_date"))
        if child_collection:
            record["customer_receipts"].append({"receipt_date": child_collection, "amount": child.get("amount"), "currency": child.get("currency", ""), "source_key": f"receipt:{child_index}:{child_collection}"})
    collection = _normalize_date(item.get("collection_date"))
    if collection:
        record["customer_receipts"].append({"receipt_date": collection, "amount": None, "currency": "", "source_key": f"receipt:main:{collection}"})
    document = _normalize_date(item.get("document_submission_date"))
    if document:
        record["documents"].append({"document_type": "交单", "document_date": document, "source_key": f"doc:main:{document}"})
    vessel = _normalize_text(item.get("vessel_voyage"))
    if vessel:
        record["vessels"].append({
            "vessel_name": vessel,
            "imo": "",
            "loading_port": _normalize_text(item.get("origin_port")),
            "discharge_port": _normalize_text(item.get("destination_port")),
            "eta": "",
            "etb": "",
            "estimated_discharge_date": "",
            "source_key": f"vessel:main:{vessel}",
            "source": "email",
        })
    return record


def _safe_json(value: Any, default: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    try:
        parsed = json.loads(value or "")
        return parsed if parsed is not None else default
    except (TypeError, ValueError):
        return default


def _natural_sort_key(value: Any) -> str:
    text = _normalize_text(value).upper()
    return re.sub(r"\d+", lambda match: f"{int(match.group(0)):020d}", text)


def calculate_business(record: dict[str, Any], manual_fcr: bool = False) -> tuple[str, str, list[dict[str, Any]]]:
    """Return status, current risk and separate data anomalies."""
    anomalies: list[dict[str, Any]] = []
    risk_reasons: list[str] = []
    required = {
        "business_no": "业务编号",
        "business_type": "业务类型",
        "trade_entity": "贸易主体",
        "supplier_steel_mill": "供应钢厂",
        "terminal_customer": "终端客户",
        "product_name": "货物品名",
        "contract_quantity_mt": "业务整体合同数量",
    }
    for field, label in required.items():
        if record.get(field) in (None, ""):
            anomalies.append({"key": f"missing:{field}", "type": "数据缺失", "description": f"缺少{label}"})
    financings = [item for item in record.get("financings", []) if item.get("amount") is not None or item.get("financing_date") or item.get("bank")]
    receipts = [item for item in record.get("customer_receipts", []) if item.get("receipt_date")]
    documents = [item for item in record.get("documents", []) if item.get("document_date")]
    vessels = [item for item in record.get("vessels", []) if item.get("vessel_name")]
    if record.get("business_type") == "融资":
        for index, item in enumerate(financings):
            if not item.get("bank"):
                anomalies.append({"key": f"missing:finance_bank:{index}", "type": "数据缺失", "description": "融资明细缺少融资银行"})
            if item.get("amount") is None:
                anomalies.append({"key": f"missing:finance_amount:{index}", "type": "数据缺失", "description": "融资明细缺少融资金额"})
            if not item.get("financing_date"):
                anomalies.append({"key": f"missing:finance_date:{index}", "type": "数据缺失", "description": "融资明细缺少融资日期"})
            if "已还" in _normalize_text(item.get("repayment_status")) and not item.get("repayment_date"):
                anomalies.append({"key": f"missing:repayment_date:{index}", "type": "数据缺失", "description": "融资明细已明确还款但缺少银行实际还款日期"})
        if not financings:
            anomalies.append({"key": "missing:financing", "type": "数据缺失", "description": "融资业务暂无可确认的实际融资明细"})
    actual_date_fields = []
    for field, label in (("financing_date", "融资日期"), ("document_date", "交单日期"), ("receipt_date", "客户回款日期"), ("repayment_date", "银行还款日期")):
        values = []
        for collection in (record.get("financings", []) if field in {"financing_date", "repayment_date"} else record.get("documents", []) if field == "document_date" else record.get("customer_receipts", [])):
            value = collection.get(field)
            if value:
                values.append(value)
        for value in values:
            parsed = _parse_date(value)
            if parsed and parsed > date.today():
                anomalies.append({"key": f"future:{field}:{value}", "type": "日期异常", "description": f"{label}为未来日期：{value}"})
    has_document = bool(documents)
    has_vessel = bool(vessels)
    has_port = bool(record.get("_port_confirmed")) or has_vessel
    has_shipment = bool(record.get("_shipment_confirmed")) or has_vessel
    if receipts and not has_document:
        anomalies.append({"key": "sequence:receipt_without_document", "type": "节点矛盾", "description": "已有客户回款事实但交单事实缺失"})
    if has_document and not has_vessel:
        anomalies.append({"key": "sequence:document_without_vessel", "type": "节点矛盾", "description": "已有交单事实但装船/船舶事实缺失"})
    if record.get("business_type") == "融资":
        all_repaid = bool(financings) and all(item.get("repayment_date") or "已还" in _normalize_text(item.get("repayment_status")) for item in financings)
        if all_repaid:
            status = "已完结"
        elif receipts:
            status = "客户已回款"
        elif documents:
            status = "待收汇"
        elif has_shipment:
            status = "已装船"
        elif has_port:
            status = "已集港"
        elif financings:
            status = "已放款"
        else:
            status = "待确认"
        active_due_dates = [_parse_date(item.get("extended_due_date") or item.get("original_due_date")) for item in financings]
        active_due_dates = [item for item in active_due_dates if item]
        if status == "已完结":
            risk = "低风险"
        elif not active_due_dates:
            risk = "中风险"
            risk_reasons.append("融资到期日缺失或无法计算")
        else:
            days = min((item - date.today()).days for item in active_due_dates)
            risk = "高风险" if days <= 7 else "中风险" if days <= 30 else "低风险"
            if days <= 7:
                risk_reasons.append(f"最近融资到期日距今天 {days} 天")
            elif days <= 30:
                risk_reasons.append(f"最近融资到期日距今天 {days} 天")
    else:
        contracts = [item for item in record.get("contracts", []) if item.get("source_key")]
        if receipts and (not contracts or len(receipts) >= len(contracts)):
            status = "已完结"
        else:
            status = "订单执行中"
        risk = "中风险" if (has_document or has_vessel or has_port) and status != "已完结" else "低风险"
        if risk == "中风险":
            risk_reasons.append("过单业务存在待确认执行节点")
    record["_risk_reasons"] = risk_reasons
    return status, risk, anomalies


def _upsert_child(cur, table: str, columns: list[str], values: list[Any], conflict_columns: list[str]) -> int:
    placeholders = ", ".join("?" for _ in values)
    fields = ", ".join(columns)
    if db._is_pg():
        updates = ", ".join(f"{column}=EXCLUDED.{column}" for column in columns if column not in conflict_columns)
        sql = f"INSERT INTO {table} ({fields}) VALUES ({placeholders}) ON CONFLICT ({', '.join(conflict_columns)}) DO UPDATE SET {updates} RETURNING id"
        cur.execute(sql.replace("?", "%s"), values)
        row = cur.fetchone()
        return int(row["id"])
    cur.execute(
        f"INSERT INTO {table} ({fields}) VALUES ({placeholders}) ON CONFLICT ({', '.join(conflict_columns)}) DO UPDATE SET "
        + ", ".join(f"{column}=excluded.{column}" for column in columns if column not in conflict_columns),
        values,
    )
    row = cur.execute(f"SELECT id FROM {table} WHERE " + " AND ".join(f"{column} = ?" for column in conflict_columns), [values[columns.index(column)] for column in conflict_columns]).fetchone()
    return int(row["id"])


def _upsert_anomalies(cur, business_id: int, anomalies: list[dict[str, Any]]) -> None:
    active_keys = {item["key"] for item in anomalies}
    current = db._exec(cur, "SELECT anomaly_key FROM order_lifecycle_data_anomalies WHERE business_id = ? AND status = 'open'", (business_id,)).fetchall()
    for row in current:
        if row["anomaly_key"] not in active_keys:
            db._exec(cur, "UPDATE order_lifecycle_data_anomalies SET status = 'resolved', resolved_at = CURRENT_TIMESTAMP WHERE business_id = ? AND anomaly_key = ?", (business_id, row["anomaly_key"]))
    for item in anomalies:
        db._exec(
            cur,
            """
            INSERT INTO order_lifecycle_data_anomalies
                (business_id, anomaly_key, anomaly_type, description, details_json, status)
            VALUES (?, ?, ?, ?, ?, 'open')
            ON CONFLICT (business_id, anomaly_key) DO UPDATE SET
                anomaly_type = excluded.anomaly_type,
                description = excluded.description,
                details_json = excluded.details_json,
                status = 'open',
                last_seen_at = CURRENT_TIMESTAMP,
                resolved_at = NULL,
                resolved_by = NULL
            """,
            (business_id, item["key"], item["type"], item["description"], _json(item)),
        )


def _replace_children(cur, business_id: int, record: dict[str, Any]) -> None:
    for table in ("order_lifecycle_contracts", "order_lifecycle_financings", "order_lifecycle_vessels", "order_lifecycle_documents", "order_lifecycle_customer_receipts", "order_lifecycle_bank_repayments"):
        db._exec(cur, f"DELETE FROM {table} WHERE business_id = ?", (business_id,))
    finance_ids: dict[str, int] = {}
    for item in record.get("contracts", []):
        _upsert_child(cur, "order_lifecycle_contracts", ["business_id", "contract_no", "purchase_contract_no", "system_contract_no", "buyer", "seller", "quantity_mt", "source_key"], [business_id, item.get("contract_no"), item.get("purchase_contract_no"), item.get("system_contract_no"), item.get("buyer"), item.get("seller"), item.get("quantity_mt"), item.get("source_key") or uuid4().hex], ["business_id", "source_key"])
    for item in record.get("financings", []):
        finance_ids[item.get("source_key", "")] = _upsert_child(cur, "order_lifecycle_financings", ["business_id", "bank", "amount", "financing_date", "original_due_date", "extended_due_date", "repayment_date", "repayment_status", "source_key"], [business_id, item.get("bank"), item.get("amount"), item.get("financing_date"), item.get("original_due_date"), item.get("extended_due_date"), item.get("repayment_date"), item.get("repayment_status"), item.get("source_key") or uuid4().hex], ["business_id", "source_key"])
    for item in record.get("vessels", []):
        _upsert_child(cur, "order_lifecycle_vessels", ["business_id", "vessel_name", "imo", "loading_port", "discharge_port", "eta", "etb", "estimated_discharge_date", "source_key", "source"], [business_id, item.get("vessel_name"), item.get("imo"), item.get("loading_port"), item.get("discharge_port"), item.get("eta"), item.get("etb"), item.get("estimated_discharge_date"), item.get("source_key") or uuid4().hex, item.get("source")], ["business_id", "source_key"])
    for item in record.get("documents", []):
        _upsert_child(cur, "order_lifecycle_documents", ["business_id", "document_type", "document_date", "source_key"], [business_id, item.get("document_type", "交单"), item.get("document_date"), item.get("source_key") or uuid4().hex], ["business_id", "source_key"])
    for item in record.get("customer_receipts", []):
        _upsert_child(cur, "order_lifecycle_customer_receipts", ["business_id", "receipt_date", "amount", "currency", "source_key"], [business_id, item.get("receipt_date"), item.get("amount"), item.get("currency"), item.get("source_key") or uuid4().hex], ["business_id", "source_key"])
    for item in record.get("bank_repayments", []):
        _upsert_child(cur, "order_lifecycle_bank_repayments", ["business_id", finance_ids.get(item.get("financing_source_key", "")), item.get("repayment_date"), item.get("amount"), item.get("source_key") or uuid4().hex], [business_id, finance_ids.get(item.get("financing_source_key", "")), item.get("repayment_date"), item.get("amount"), item.get("source_key") or uuid4().hex], ["business_id", "source_key"])


def apply_source_batch(batch: dict[str, Any], imported_by: str = "system", complete_snapshot: bool = True) -> dict[str, Any]:
    """Apply a parsed complete batch atomically, with two-observation whole-card deletion."""
    initialize_schema_for_connection = initialize_schema
    with db.connect() as conn:
        initialize_schema_for_connection(conn)
        cur = conn.cursor()
        records = batch.get("records", [])
        source_type = batch.get("source_type", "")
        keys = sorted({item.get("business_key") for item in records if item.get("business_key")})
        key_hash = hashlib.sha256("|".join(keys).encode()).hexdigest()
        batch_id = db._last_insert_id(cur, "INSERT INTO order_lifecycle_source_batches (source_type, source_locator, source_version, snapshot_date, source_hash, source_key_set_hash, status, record_count, completed_at) VALUES (?, ?, ?, ?, ?, ?, 'success', ?, CURRENT_TIMESTAMP)", (source_type, batch.get("source_locator"), batch.get("source_version"), batch.get("snapshot_date"), batch.get("source_hash"), key_hash, len(records)))
        existing_rows = db._exec(cur, "SELECT id, business_key, port_status, shipment_status FROM order_lifecycle_businesses WHERE source_type = ? AND is_cancelled = 0", (source_type,)).fetchall()
        existing_by_key = {row["business_key"]: row for row in existing_rows}
        missing_keys = sorted(set(existing_by_key) - set(keys)) if complete_snapshot else []
        deletion_hash = hashlib.sha256("|".join(missing_keys).encode()).hexdigest() if missing_keys else ""
        previous = db._exec(cur, "SELECT deletion_candidate_hash FROM order_lifecycle_source_batches WHERE source_type = ? AND id <> ? ORDER BY id DESC LIMIT 1", (source_type, batch_id)).fetchone()
        confirmed_deletions = set(missing_keys) if missing_keys and previous and previous["deletion_candidate_hash"] == deletion_hash else set()
        if missing_keys:
            db._exec(cur, "UPDATE order_lifecycle_source_batches SET deletion_candidate_hash = ?, deletion_candidate_count = ? WHERE id = ?", (deletion_hash, len(missing_keys), batch_id))
        deleted = 0
        for record in records:
            effective_record = dict(record)
            existing = existing_by_key.get(record["business_key"])
            anomalies: list[dict[str, Any]] = []
            if existing:
                effective_record["_port_confirmed"] = existing["port_status"] == "已集港"
                effective_record["_shipment_confirmed"] = existing["shipment_status"] == "已装船"
                override_rows = db._exec(cur, "SELECT field_name, value_json FROM order_lifecycle_manual_overrides WHERE business_id = ? AND is_active = 1", (existing["id"],)).fetchall()
                for override in override_rows:
                    field_name = override["field_name"]
                    if field_name not in MANUAL_OVERRIDE_FIELDS and field_name != "fcr":
                        continue
                    manual_value = _safe_json(override["value_json"], None)
                    source_value = record.get(field_name)
                    if field_name != "fcr" and source_value != manual_value:
                        source_hash = hashlib.sha256(_json(source_value).encode()).hexdigest()[:16]
                        anomalies.append({"key": f"manual_conflict:{field_name}:{source_hash}", "type": "来源冲突", "description": f"人工值与来源值不同：{field_name}", "details": {"field": field_name, "manual_value": manual_value, "source_value": source_value}})
                    if field_name != "fcr":
                        effective_record[field_name] = manual_value
            if effective_record.get("vessels"):
                effective_record["_port_confirmed"] = True
                effective_record["_shipment_confirmed"] = True
            effective_record["_port_status"] = "已集港" if effective_record.get("_port_confirmed") else "待确认"
            effective_record["_shipment_status"] = "已装船" if effective_record.get("_shipment_confirmed") else "待确认"
            status, risk, calculated_anomalies = calculate_business(effective_record)
            anomalies.extend(calculated_anomalies)
            if existing:
                business_id = existing["id"]
                db._exec(cur, """UPDATE order_lifecycle_businesses SET business_no = ?, business_type = ?, trade_entity = ?, supplier_steel_mill = ?, terminal_customer = ?, product_name = ?, contract_quantity_mt = ?, status = ?, port_status = ?, shipment_status = ?, risk_level = ?, risk_reasons_json = ?, anomaly_count = ?, source_snapshot_date = ?, source_version = ?, source_record_key = ?, source_presence_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?""", (effective_record.get("business_no"), effective_record.get("business_type"), effective_record.get("trade_entity"), effective_record.get("supplier_steel_mill"), effective_record.get("terminal_customer"), effective_record.get("product_name"), effective_record.get("contract_quantity_mt"), status, effective_record.get("_port_status"), effective_record.get("_shipment_status"), risk, _json(effective_record.get("_risk_reasons", [])), len(anomalies), record.get("source_snapshot_date"), record.get("source_version"), record.get("source_record_key"), key_hash, business_id))
            else:
                business_uid = uuid4().hex
                business_id = db._last_insert_id(cur, """INSERT INTO order_lifecycle_businesses (business_uid, business_key, business_no, business_type, trade_entity, supplier_steel_mill, terminal_customer, product_name, contract_quantity_mt, status, port_status, shipment_status, risk_level, risk_reasons_json, anomaly_count, source_type, source_snapshot_date, source_version, source_record_key, source_presence_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (business_uid, effective_record.get("business_key"), effective_record.get("business_no"), effective_record.get("business_type"), effective_record.get("trade_entity"), effective_record.get("supplier_steel_mill"), effective_record.get("terminal_customer"), effective_record.get("product_name"), effective_record.get("contract_quantity_mt"), status, effective_record.get("_port_status"), effective_record.get("_shipment_status"), risk, _json(effective_record.get("_risk_reasons", [])), len(anomalies), source_type, record.get("source_snapshot_date"), record.get("source_version"), record.get("source_record_key"), key_hash))
            _replace_children(cur, business_id, record)
            _upsert_anomalies(cur, business_id, anomalies)
            db._exec(cur, "INSERT OR IGNORE INTO order_lifecycle_source_records (batch_id, source_type, source_key, business_key, source_file, source_sheet, source_row, raw_json, normalized_json, raw_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (batch_id, source_type, record.get("source_record_key"), record.get("business_key"), record.get("raw", {}).get("file"), record.get("raw", {}).get("sheet"), record.get("raw", {}).get("row_no"), _json(record.get("raw", {})), _json(record), hashlib.sha256(_json(record.get("raw", {})).encode()).hexdigest()))
        for key in confirmed_deletions:
            row = existing_by_key.get(key)
            if not row:
                continue
            business_id = row["id"]
            for table in ("order_lifecycle_data_anomalies", "order_lifecycle_manual_overrides", "order_lifecycle_contracts", "order_lifecycle_financings", "order_lifecycle_vessels", "order_lifecycle_documents", "order_lifecycle_customer_receipts", "order_lifecycle_bank_repayments", "order_lifecycle_source_records"):
                if table == "order_lifecycle_source_records":
                    db._exec(cur, "DELETE FROM order_lifecycle_source_records WHERE business_key = ?", (key,))
                else:
                    db._exec(cur, f"DELETE FROM {table} WHERE business_id = ?", (business_id,))
            db._exec(cur, "DELETE FROM order_lifecycle_businesses WHERE id = ?", (business_id,))
            deleted += 1
        now_field = "wps_last_success_at" if source_type == "wps" else "email_last_success_at"
        db._exec(cur, f"UPDATE order_lifecycle_sync_state SET {now_field} = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = 1")
        conn.commit()
    return {"batch_id": batch_id, "record_count": len(records), "deletion_candidates": len(missing_keys), "deleted_businesses": deleted, "source_type": source_type}


def _row_json(row: Any) -> dict[str, Any]:
    return dict(row) if row else {}


def _load_business_children(cur, business_id: int) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for key, table in (("contracts", "order_lifecycle_contracts"), ("financings", "order_lifecycle_financings"), ("vessels", "order_lifecycle_vessels"), ("documents", "order_lifecycle_documents"), ("customer_receipts", "order_lifecycle_customer_receipts"), ("bank_repayments", "order_lifecycle_bank_repayments")):
        result[key] = [_row_json(item) for item in db._exec(cur, f"SELECT * FROM {table} WHERE business_id = ? ORDER BY id", (business_id,)).fetchall()]
    return result


def _serialize_business(row: Any, children: dict[str, list[dict[str, Any]]], anomaly_rows: list[dict[str, Any]], can_sensitive: bool = False) -> dict[str, Any]:
    item = _row_json(row)
    item.update(children)
    item["risk_reasons"] = _safe_json(item.pop("risk_reasons_json", "[]"), [])
    item["anomalies"] = anomaly_rows
    item["anomaly_count"] = len(anomaly_rows)
    item["can_sensitive"] = can_sensitive
    item["fcr"] = bool(item.get("fcr"))
    if anomaly_rows:
        item["next_action"] = "人工判断并修正数据异常"
    else:
        item["next_action"] = {
            "已放款": "确认集港进度",
            "已集港": "确认装船进度",
            "已装船": "确认交单事实",
            "待收汇": "跟进客户回款",
            "客户已回款": "确认银行还款",
            "订单执行中": "跟进下一执行节点",
            "待确认": "补齐必要业务字段",
            "已完结": "无",
        }.get(item.get("status"), "人工确认下一步")
    return item


def list_businesses(filters: dict[str, Any]) -> dict[str, Any]:
    clauses = ["is_cancelled = 0"]
    params: list[Any] = []
    if filters.get("business_type"):
        clauses.append("business_type = ?")
        params.append(filters["business_type"])
    if filters.get("risk_level"):
        clauses.append("risk_level = ?")
        params.append(filters["risk_level"])
    if filters.get("status"):
        clauses.append("status = ?")
        params.append(filters["status"])
    if filters.get("fcr") in {"FCR", "非FCR"}:
        clauses.append("fcr = ?")
        params.append(1 if filters["fcr"] == "FCR" else 0)
    keyword = _normalize_text(filters.get("keyword"))
    if keyword:
        like = f"%{keyword.lower()}%"
        clauses.append("LOWER(REPLACE(REPLACE(COALESCE(business_no, '') || COALESCE(product_name, '') || COALESCE(terminal_customer, '') || COALESCE(supplier_steel_mill, ''), ' ', ''), '-', '')) LIKE ?")
        params.append(like.replace(" ", "").replace("-", ""))
    where = " AND ".join(clauses)
    page = max(int(filters.get("page") or 1), 1)
    page_size = min(max(int(filters.get("page_size") or 20), 1), 100)
    with db.connect() as conn:
        cur = conn.cursor()
        total = db._exec(cur, f"SELECT COUNT(*) AS c FROM order_lifecycle_businesses WHERE {where}", params).fetchone()["c"]
        all_rows = db._exec(cur, f"SELECT * FROM order_lifecycle_businesses WHERE {where}", params).fetchall()
        risk_order = {"高风险": 0, "中风险": 1, "低风险": 2}
        grouped: dict[tuple[int, int], list[Any]] = defaultdict(list)
        for row in all_rows:
            grouped[(1 if row["status"] == "已完结" else 0, risk_order.get(row["risk_level"], 3))].append(row)
        rows = []
        for group_key in sorted(grouped):
            rows.extend(sorted(grouped[group_key], key=lambda row: _natural_sort_key(row["business_no"]), reverse=True))
        rows = rows[(page - 1) * page_size: page * page_size]
        cards = []
        for row in rows:
            anomalies = [_row_json(item) for item in db._exec(cur, "SELECT * FROM order_lifecycle_data_anomalies WHERE business_id = ? AND status = 'open' ORDER BY id", (row["id"],)).fetchall()]
            cards.append(_serialize_business(row, _load_business_children(cur, row["id"]), anomalies, bool(filters.get("can_sensitive"))))
        summary_rows = db._exec(cur, f"SELECT business_type, risk_level, status, SUM(CASE WHEN business_type = '融资' AND status NOT IN ('已完结') THEN COALESCE((SELECT SUM(amount) FROM order_lifecycle_financings f WHERE f.business_id = b.id), 0) ELSE 0 END) AS active_finance, COUNT(*) AS count FROM order_lifecycle_businesses b WHERE {where} GROUP BY business_type, risk_level, status", params).fetchall()
        summary = {"存续融资金额": 0, "高风险业务数": 0, "中风险业务数": 0, "低风险业务数": 0, "已完结业务数": 0, "数据异常数": 0, "更新异常数": 0}
        for item in summary_rows:
            summary["存续融资金额"] += float(item["active_finance"] or 0)
            summary[{"高风险": "高风险业务数", "中风险": "中风险业务数", "低风险": "低风险业务数"}.get(item["risk_level"], "低风险业务数")] += int(item["count"] or 0)
            if item["status"] == "已完结":
                summary["已完结业务数"] += int(item["count"] or 0)
        sync = _row_json(db._exec(cur, "SELECT * FROM order_lifecycle_sync_state WHERE id = 1").fetchone())
        summary["数据异常数"] = db._exec(cur, f"SELECT COUNT(DISTINCT a.business_id) AS c FROM order_lifecycle_data_anomalies a JOIN order_lifecycle_businesses b ON b.id = a.business_id WHERE a.status = 'open' AND {where}", params).fetchone()["c"]
        summary["更新异常数"] = int(bool(sync.get("wps_last_error"))) + int(bool(sync.get("email_last_error")))
    return {"records": cards, "total": int(total), "page": page, "page_size": page_size, "summary": summary, "sync_status": sync}


def set_manual_fcr(business_id: int, enabled: bool, user: dict, note: str = "") -> dict[str, Any]:
    require_permission(user, PERMISSION_RESOURCE, "manage")
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(cur, "SELECT id FROM order_lifecycle_businesses WHERE id = ? AND is_cancelled = 0", (business_id,)).fetchone()
        if not row:
            raise KeyError(business_id)
        db._exec(cur, "UPDATE order_lifecycle_businesses SET fcr = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (1 if enabled else 0, business_id))
        db._exec(cur, "INSERT INTO order_lifecycle_manual_overrides (business_id, field_name, value_json, note, modified_by, modified_at, is_active) VALUES (?, 'fcr', ?, ?, ?, CURRENT_TIMESTAMP, 1) ON CONFLICT (business_id, field_name) DO UPDATE SET value_json = excluded.value_json, note = excluded.note, modified_by = excluded.modified_by, modified_at = CURRENT_TIMESTAMP, is_active = 1", (business_id, _json(bool(enabled)), note or None, user.get("name") or user.get("username") or "unknown"))
        conn.commit()
    db.log_operation(user["id"], ORDER_LIFECYCLE_MODULE, "人工修改", f"人工设置订单全流程FCR：{business_id}={bool(enabled)}")
    return {"business_id": business_id, "fcr": bool(enabled)}


def _lifecycle_user(authorization: Optional[str] = Header(default=None)):
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    require_permission(user, PERMISSION_RESOURCE, "view")
    return user


class ManualFcrRequest(BaseModel):
    enabled: bool
    note: str = Field(default="", max_length=500)


class ManualOverrideRequest(BaseModel):
    field_name: str = Field(min_length=1)
    value: Any
    note: str = Field(default="", max_length=500)


class NodeConfirmationRequest(BaseModel):
    node: str = Field(pattern="^(集港|装船)$")
    confirmed: bool
    date: str = Field(default="")
    note: str = Field(default="", max_length=500)


class LocalImportRequest(BaseModel):
    path: str = Field(min_length=1)
    source_type: str = Field(pattern="^(wps|email)$")


@router.get("/order-lifecycle/progress")
def order_lifecycle_progress(
    keyword: str = "",
    business_type: str = "",
    risk_level: str = "",
    status: str = "",
    fcr: str = "",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: dict = Depends(_lifecycle_user),
):
    return list_businesses({"keyword": keyword, "business_type": business_type, "risk_level": risk_level, "status": status, "fcr": fcr, "page": page, "page_size": page_size, "can_sensitive": can(user, PERMISSION_RESOURCE, "manage")})


@router.get("/order-lifecycle/sync-status")
def order_lifecycle_sync_status(user: dict = Depends(_lifecycle_user)):
    with db.connect() as conn:
        row = db._exec(conn.cursor(), "SELECT * FROM order_lifecycle_sync_state WHERE id = 1").fetchone()
    return _row_json(row)


@router.post("/order-lifecycle/businesses/{business_id}/fcr")
def order_lifecycle_fcr(business_id: int, request: ManualFcrRequest, user: dict = Depends(_lifecycle_user)):
    try:
        return set_manual_fcr(business_id, request.enabled, user, request.note)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="业务不存在") from exc


@router.patch("/order-lifecycle/businesses/{business_id}/node-confirmation")
def order_lifecycle_node_confirmation(business_id: int, request: NodeConfirmationRequest, user: dict = Depends(_lifecycle_user)):
    require_permission(user, PERMISSION_RESOURCE, "manage")
    if request.date and not _parse_date(request.date):
        raise HTTPException(status_code=400, detail="节点日期格式无法识别")
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(cur, "SELECT * FROM order_lifecycle_businesses WHERE id = ? AND is_cancelled = 0", (business_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="业务不存在")
        operator = user.get("name") or user.get("username") or "unknown"
        if request.node == "集港":
            db._exec(cur, "UPDATE order_lifecycle_businesses SET port_status = ?, port_confirmed_date = ?, port_confirmed_by = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", ("已集港" if request.confirmed else "待确认", request.date if request.confirmed else None, operator if request.confirmed else None, business_id))
        else:
            db._exec(cur, "UPDATE order_lifecycle_businesses SET shipment_status = ?, shipment_confirmed_date = ?, shipment_confirmed_by = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", ("已装船" if request.confirmed else "待确认", request.date if request.confirmed else None, operator if request.confirmed else None, business_id))
        refreshed = db._exec(cur, "SELECT * FROM order_lifecycle_businesses WHERE id = ?", (business_id,)).fetchone()
        children = _load_business_children(cur, business_id)
        record = {key: refreshed[key] for key in ("business_no", "business_type", "trade_entity", "supplier_steel_mill", "terminal_customer", "product_name", "contract_quantity_mt")}
        record.update(children)
        record["_port_confirmed"] = refreshed["port_status"] == "已集港"
        record["_shipment_confirmed"] = refreshed["shipment_status"] == "已装船"
        status, risk, anomalies = calculate_business(record)
        db._exec(cur, "UPDATE order_lifecycle_businesses SET status = ?, risk_level = ?, risk_reasons_json = ?, anomaly_count = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (status, risk, _json(record.get("_risk_reasons", [])), len(anomalies), business_id))
        _upsert_anomalies(cur, business_id, anomalies)
        conn.commit()
    db.log_operation(user["id"], ORDER_LIFECYCLE_MODULE, "人工确认", f"人工确认订单全流程节点：{business_id}/{request.node}/{request.confirmed}")
    return {"business_id": business_id, "node": request.node, "confirmed": request.confirmed, "date": request.date or None, "status": status, "risk_level": risk}


@router.patch("/order-lifecycle/businesses/{business_id}/override")
def order_lifecycle_override(business_id: int, request: ManualOverrideRequest, user: dict = Depends(_lifecycle_user)):
    require_permission(user, PERMISSION_RESOURCE, "manage")
    if request.field_name not in MANUAL_OVERRIDE_FIELDS:
        raise HTTPException(status_code=400, detail="该字段不支持人工覆盖")
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(cur, "SELECT id FROM order_lifecycle_businesses WHERE id = ? AND is_cancelled = 0", (business_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="业务不存在")
        db._exec(cur, "INSERT INTO order_lifecycle_manual_overrides (business_id, field_name, value_json, note, modified_by, modified_at, is_active) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 1) ON CONFLICT (business_id, field_name) DO UPDATE SET value_json = excluded.value_json, note = excluded.note, modified_by = excluded.modified_by, modified_at = CURRENT_TIMESTAMP, is_active = 1", (business_id, request.field_name, _json(request.value), request.note or None, user.get("name") or user.get("username") or "unknown"))
        conn.commit()
    db.log_operation(user["id"], ORDER_LIFECYCLE_MODULE, "人工修改", f"人工覆盖订单全流程字段：{business_id}/{request.field_name}")
    return {"business_id": business_id, "field_name": request.field_name, "value": request.value}


@router.post("/order-lifecycle/import-local")
def order_lifecycle_import_local(request: LocalImportRequest, user: dict = Depends(_lifecycle_user)):
    require_permission(user, PERMISSION_RESOURCE, "manage")
    path = Path(request.path)
    try:
        batch = parse_wps_workbook(path) if request.source_type == "wps" else parse_email_batch(path)
        return apply_source_batch(batch, imported_by=user.get("name") or "user")
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
