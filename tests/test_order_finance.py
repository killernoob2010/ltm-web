"""订单融资进度监控 — Excel parser and import tests."""
from pathlib import Path
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import db
from app import order_finance
from app.order_finance import (
    DuplicateOrderFinanceError,
    create_manual_order_finance_record,
    derive_business_status,
    find_order_finance_duplicates,
    import_order_finance_directory,
    list_order_finance_records,
    parse_order_finance_directory,
    update_management_fields,
)


LEDGER_DIR = Path("/Users/wangjingze/建龙/贸易处/订单融资合同汇总")
NEW_LEDGER_WORKBOOK = Path("/Users/wangjingze/建龙/贸易处/YOLANDA和香港建龙出口钢材信用证台账(副本).xlsx")


def use_temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "order_finance.db")
    db.init_db()


def test_parse_order_finance_directory_reads_new_workbook_all_years():
    result = parse_order_finance_directory(NEW_LEDGER_WORKBOOK)

    assert result["summary"]["files_read"] == 1
    assert result["summary"]["record_count"] == 70
    years = {record["source_snapshot_date"][:4] for record in result["records"]}
    assert years == {"2025", "2026"}
    subsidiaries = {record["subsidiary"] for record in result["records"]}
    assert {"东钢", "北满", "抚顺", "西林", "阿城"}.issubset(subsidiaries)


def test_parse_order_finance_keeps_management_fields_empty_for_import():
    result = parse_order_finance_directory(NEW_LEDGER_WORKBOOK)
    sample = next(record for record in result["records"] if record["purchase_contract_no"])

    assert "planned_drawdown_date" not in sample
    assert "manager_note" not in sample
    assert sample["business_key"]
    assert sample["source_file"].endswith(".xlsx")


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
    use_temp_db(tmp_path, monkeypatch)
    first = import_order_finance_directory(NEW_LEDGER_WORKBOOK, imported_by="pytest")
    assert first["summary"]["record_count"] == 70

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

    second = import_order_finance_directory(NEW_LEDGER_WORKBOOK, imported_by="pytest")
    updated = next(item for item in list_order_finance_records() if item["id"] == target["id"])

    assert second["summary"]["record_count"] == 70
    assert updated["planned_drawdown_date"] == "2026-07-01"
    assert updated["planned_finance_amount"] == 12345678
    assert updated["repayment_requirement"] == "放款当日先回25%陈欠"
    assert updated["next_action"] == "等领导确认"
    assert updated["manager_note"] == "pytest manual note"


def test_import_new_workbook_archives_previous_excel_source_records(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    old_import = import_order_finance_directory(LEDGER_DIR, imported_by="pytest")
    assert old_import["summary"]["record_count"] >= 25
    assert len(list_order_finance_records()) == old_import["summary"]["record_count"]

    new_import = import_order_finance_directory(NEW_LEDGER_WORKBOOK, imported_by="pytest")
    records = list_order_finance_records()

    assert new_import["summary"]["record_count"] == 70
    assert len(records) == 70
    assert {record["source_file"] for record in records} == {NEW_LEDGER_WORKBOOK.name}


def test_parse_order_finance_default_path_falls_back_to_seed_when_file_is_missing(tmp_path, monkeypatch):
    missing_workbook = tmp_path / "YOLANDA和香港建龙出口钢材信用证台账(副本).xlsx"
    monkeypatch.setattr(order_finance, "LOCAL_DEFAULT_LEDGER_WORKBOOK", missing_workbook)

    result = parse_order_finance_directory(missing_workbook)

    assert result["summary"]["files_read"] == 1
    assert result["summary"]["record_count"] == 70


def test_manual_create_blocks_exact_contract_duplicate(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    payload = {
        "subsidiary": "北满",
        "purchase_contract_no": "MANUAL-PC-001",
        "system_contract_no": "MANUAL-SC-001",
        "terminal_customer": "MOLYCOP",
        "product_name": "Hot Rolled Alloy Steel Round Bars",
        "contract_quantity_mt": 1000,
        "finance_bank": "UOB",
        "finance_due_date": "2026-08-30",
        "planned_finance_amount": 12000000,
        "next_action": "确认放款安排",
    }

    created = create_manual_order_finance_record(payload, created_by="pytest")
    assert created["source_file"] == "手动新增"
    assert created["business_key"] == "北满|MANUAL-PC-001|MANUAL-SC-001"
    assert created["product_name"] == "Hot Rolled Alloy Steel Round Bars"
    assert created["contract_quantity_mt"] == 1000
    assert created["finance_bank"] == "UOB"

    duplicates = find_order_finance_duplicates(payload)
    assert duplicates["exact"]
    assert duplicates["exact"]["id"] == created["id"]

    try:
        create_manual_order_finance_record(payload, created_by="pytest")
    except DuplicateOrderFinanceError as exc:
        assert exc.existing["id"] == created["id"]
    else:
        raise AssertionError("expected exact duplicate to be blocked")


def test_manual_create_reports_similar_duplicates_without_blocking(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    first = create_manual_order_finance_record(
        {
            "subsidiary": "承德",
            "terminal_customer": "SAMSUNG",
            "product_name": "钢材",
            "finance_due_date": "2026-09-15",
            "planned_finance_amount": 8880000,
        },
        created_by="pytest",
    )
    second_payload = {
        "subsidiary": "承德",
        "terminal_customer": "SAMSUNG",
        "product_name": "钢材",
        "finance_due_date": "2026-09-15",
        "planned_finance_amount": 8880000,
    }

    duplicates = find_order_finance_duplicates(second_payload)
    assert duplicates["exact"] is None
    assert [item["id"] for item in duplicates["similar"]] == [first["id"]]

    second = create_manual_order_finance_record(second_payload, created_by="pytest")
    assert second["id"] != first["id"]
    assert second["business_key"].startswith("承德|手动新增|")


def test_import_merges_manual_record_with_same_contract_key_and_preserves_management(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    excel_sample = next(record for record in parse_order_finance_directory(NEW_LEDGER_WORKBOOK)["records"] if record["purchase_contract_no"])
    manual = create_manual_order_finance_record(
        {
            "subsidiary": excel_sample["subsidiary"],
            "purchase_contract_no": excel_sample["purchase_contract_no"],
            "system_contract_no": excel_sample["system_contract_no"],
            "terminal_customer": "手工临时客户",
            "product_name": "手工临时货物",
            "planned_finance_amount": 7654321,
            "repayment_requirement": "先回陈欠",
            "manager_note": "导入前手工备注",
        },
        created_by="pytest",
    )

    import_order_finance_directory(NEW_LEDGER_WORKBOOK, imported_by="pytest")
    merged = next(item for item in list_order_finance_records() if item["id"] == manual["id"])

    assert merged["source_file"].endswith(".xlsx")
    assert merged["terminal_customer"] == excel_sample["terminal_customer"]
    assert merged["planned_finance_amount"] == 7654321
    assert merged["repayment_requirement"] == "先回陈欠"
    assert merged["manager_note"] == "导入前手工备注"
