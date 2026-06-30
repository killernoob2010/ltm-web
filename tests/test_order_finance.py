"""订单融资进度监控 — Excel parser and import tests."""
from pathlib import Path
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import db
from app.order_finance import (
    derive_business_status,
    import_order_finance_directory,
    list_order_finance_records,
    parse_order_finance_directory,
    update_management_fields,
)


LEDGER_DIR = Path("/Users/wangjingze/建龙/贸易处/订单融资合同汇总")


def test_parse_order_finance_directory_reads_current_ledgers():
    result = parse_order_finance_directory(LEDGER_DIR)

    assert result["summary"]["files_read"] == 6
    assert result["summary"]["record_count"] >= 25
    subsidiaries = {record["subsidiary"] for record in result["records"]}
    assert {"东钢", "北满", "承德", "抚顺", "西林", "阿城"}.issubset(subsidiaries)


def test_parse_order_finance_keeps_management_fields_empty_for_import():
    result = parse_order_finance_directory(LEDGER_DIR)
    sample = next(record for record in result["records"] if record["purchase_contract_no"])

    assert "planned_drawdown_date" not in sample
    assert "manager_note" not in sample
    assert sample["business_key"]
    assert sample["source_file"].endswith(".xls")


def test_derive_business_status_for_drawdown_without_bill_of_lading():
    status = derive_business_status(
        {
            "finance_drawdown_date": "2026-06-15",
            "finance_due_date": "2026-07-30",
            "bill_of_lading_date": "",
            "collection_date": "",
            "remark": "",
        }
    )

    assert status["business_status"] == "已放款待装船"
    assert status["next_action"] == "跟进船期和提单"


def test_import_order_finance_directory_preserves_management_fields(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "order_finance.db")
    db.init_db()
    first = import_order_finance_directory(LEDGER_DIR, imported_by="pytest")
    assert first["summary"]["record_count"] >= 25

    records = list_order_finance_records()
    target = records[0]
    update_management_fields(
        target["id"],
        {
            "planned_drawdown_date": "2026-07-01",
            "planned_finance_amount": 12345678,
            "repayment_requirement": "放款当日先回25%陈欠",
            "next_action": "等领导确认",
            "manager_note": "pytest manual note",
        },
    )

    second = import_order_finance_directory(LEDGER_DIR, imported_by="pytest")
    updated = next(item for item in list_order_finance_records() if item["id"] == target["id"])

    assert second["summary"]["record_count"] >= 25
    assert updated["planned_drawdown_date"] == "2026-07-01"
    assert updated["planned_finance_amount"] == 12345678
    assert updated["repayment_requirement"] == "放款当日先回25%陈欠"
    assert updated["next_action"] == "等领导确认"
    assert updated["manager_note"] == "pytest manual note"
