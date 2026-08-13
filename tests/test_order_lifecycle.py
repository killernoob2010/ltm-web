from pathlib import Path
import base64

import pytest

from backend.app import db
from backend.app.order_lifecycle import (
    apply_source_batch,
    calculate_business,
    initialize_schema,
    list_businesses,
    parse_email_batch,
    parse_wps_workbook,
    order_lifecycle_node_confirmation,
    NodeConfirmationRequest,
    LifecycleUploadRequest,
    LifecycleUploadFile,
    order_lifecycle_import_upload,
)


@pytest.fixture
def lifecycle_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "lifecycle.db")
    with db.connect() as conn:
        initialize_schema(conn)
        conn.execute("CREATE TABLE operation_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, module_code TEXT, entity_type TEXT, entity_id INTEGER, operation_type TEXT, description TEXT, before_data TEXT, after_data TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        conn.commit()
    return tmp_path


def _record(key="email:G1", business_type="过单", receipts=None, financings=None):
    return {
        "business_type": business_type,
        "business_no": "G-1",
        "business_key": key,
        "trade_entity": "YOLANDA",
        "supplier_steel_mill": "东钢",
        "terminal_customer": "客户A",
        "product_name": "钢材",
        "contract_quantity_mt": 100,
        "source_type": "email",
        "source_snapshot_date": "2026-08-13",
        "source_version": "v1",
        "source_record_key": "email:G-1",
        "contracts": [{"contract_no": "C1", "source_key": "c1"}],
        "financings": financings or [],
        "vessels": [],
        "documents": [],
        "customer_receipts": receipts or [],
        "bank_repayments": [],
        "raw": {},
    }


def _batch(records, version="v1"):
    return {
        "source_type": "email",
        "source_locator": "fixture",
        "source_version": version,
        "snapshot_date": "2026-08-13",
        "source_hash": version,
        "records": records,
    }


def test_pass_through_all_receipts_complete_but_missing_document_is_anomaly():
    record = _record(receipts=[{"receipt_date": "2026-08-12", "source_key": "r1"}])
    status, risk, anomalies = calculate_business(record)
    assert status == "已完结"
    assert risk == "低风险"
    assert any(item["key"] == "sequence:receipt_without_document" for item in anomalies)


def test_financing_all_repaid_without_date_is_not_silently_clean():
    record = _record(
        business_type="融资",
        financings=[{
            "bank": "中信唐山",
            "amount": 1000,
            "financing_date": "2026-07-01",
            "original_due_date": "2026-08-20",
            "repayment_status": "已还款",
            "source_key": "f1",
        }],
    )
    status, _, anomalies = calculate_business(record)
    assert status == "已完结"
    assert any(item["key"] == "missing:repayment_date:0" for item in anomalies)


def test_whole_card_deletion_requires_same_missing_key_set_twice(lifecycle_db):
    record = _record()
    assert apply_source_batch(_batch([record]))["deleted_businesses"] == 0
    assert apply_source_batch(_batch([], "v2"))["deleted_businesses"] == 0
    assert list_businesses({"page": 1, "page_size": 20})["total"] == 1
    assert apply_source_batch(_batch([], "v3"))["deleted_businesses"] == 1
    assert list_businesses({"page": 1, "page_size": 20})["total"] == 0


def test_manual_value_survives_source_refresh_and_reopens_only_for_new_source_value(lifecycle_db):
    record = _record()
    apply_source_batch(_batch([record]))
    with db.connect() as conn:
        business_id = conn.execute("SELECT id FROM order_lifecycle_businesses").fetchone()["id"]
        conn.execute("INSERT INTO order_lifecycle_manual_overrides (business_id, field_name, value_json, modified_by) VALUES (?, ?, ?, ?)", (business_id, "terminal_customer", '"人工客户"', "tester"))
        conn.commit()
    refreshed = _record()
    refreshed["terminal_customer"] = "来源客户A"
    apply_source_batch(_batch([refreshed], "v2"))
    result = list_businesses({"page": 1, "page_size": 20})["records"][0]
    assert result["terminal_customer"] == "人工客户"
    assert any(item["anomaly_type"] == "来源冲突" for item in result["anomalies"])


def test_sensitive_node_confirmation_advances_main_status(lifecycle_db):
    record = _record(business_type="融资", financings=[{"bank": "中信唐山", "amount": 1000, "financing_date": "2026-07-01", "original_due_date": "2026-12-30", "source_key": "f1"}])
    apply_source_batch(_batch([record]))
    with db.connect() as conn:
        business_id = conn.execute("SELECT id FROM order_lifecycle_businesses").fetchone()["id"]
    result = order_lifecycle_node_confirmation(business_id, NodeConfirmationRequest(node="集港", confirmed=True, date="2026-08-13"), {"id": 1, "role": "管理员", "name": "管理员"})
    assert result["status"] == "已集港"
    assert list_businesses({"page": 1, "page_size": 20})["records"][0]["port_status"] == "已集港"


def test_status_filter_qualifies_business_status_in_anomaly_summary(lifecycle_db):
    record = _record(
        business_type="融资",
        financings=[{
            "bank": "中信唐山",
            "amount": 1000,
            "financing_date": "2026-07-01",
            "original_due_date": "2026-08-20",
            "repayment_date": "2026-08-12",
            "repayment_status": "已还款",
            "source_key": "f1",
        }],
    )
    apply_source_batch(_batch([record]))
    result = list_businesses({"status": "已完结", "page": 1, "page_size": 20})
    assert result["total"] == 1
    assert result["summary"]["已完结业务数"] == 1


def test_real_wps_and_mail_fixtures_are_readable():
    wps = Path("/Users/wangjingze/Downloads/YOLANDA和香港建龙出口钢材信用证台账.xlsx")
    mail = Path("/Users/wangjingze/Documents/订单融资进度监控/data/raw/2026-07-05")
    if not wps.exists() or not mail.exists():
        pytest.skip("本机真实样例未挂载")
    wps_result = parse_wps_workbook(wps)
    mail_result = parse_email_batch(mail)
    assert wps_result["summary"]["record_count"] > 0
    assert mail_result["summary"]["files_read"] == 6
    assert mail_result["summary"]["record_count"] > 0


def test_upload_import_accepts_wps_snapshot_in_staging_db(lifecycle_db):
    wps = Path("/Users/wangjingze/Downloads/YOLANDA和香港建龙出口钢材信用证台账.xlsx")
    if not wps.exists():
        pytest.skip("本机真实样例未挂载")
    request = LifecycleUploadRequest(
        source_type="wps",
        files=[LifecycleUploadFile(file_name=wps.name, file_data=base64.b64encode(wps.read_bytes()).decode())],
    )
    result = order_lifecycle_import_upload(request, {"id": 1, "role": "管理员", "name": "管理员"})
    assert result["status"] == "success"
    assert result["source_type"] == "wps"
    assert list_businesses({"page": 1, "page_size": 20})["total"] > 0
