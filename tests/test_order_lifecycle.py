from pathlib import Path
import base64
from datetime import date, timedelta

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
    order_lifecycle_business_detail,
    order_lifecycle_override,
    order_lifecycle_child_override,
    order_lifecycle_child_record,
    set_manual_fcr,
    ManualOverrideRequest,
    LifecycleChildOverrideRequest,
    LifecycleChildRecordRequest,
)
from fastapi import HTTPException


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


def _wps_record(business_no, purchase_contract_no, system_contract_no, *, receipts=None, financings=None):
    return {
        "business_type": "融资",
        "business_no": business_no,
        "business_key": f"business:wps:{business_no}",
        "source_business_key": f"business:wps:{business_no}",
        "trade_entity": "YOLANDA",
        "supplier_steel_mill": "东钢",
        "terminal_customer": "客户A",
        "product_name": "钢材",
        "contract_quantity_mt": 100,
        "source_type": "wps",
        "source_snapshot_date": "2026-08-13",
        "source_version": "wps-v1",
        "source_record_key": f"wps:{business_no}",
        "contracts": [{
            "contract_no": system_contract_no,
            "purchase_contract_no": purchase_contract_no,
            "system_contract_no": system_contract_no,
            "source_key": f"contract:{purchase_contract_no}",
        }],
        "financings": financings or [{
            "bank": "中信唐山",
            "amount": 1000,
            "financing_date": "2026-07-01",
            "original_due_date": "2026-12-30",
            "source_key": f"finance:{purchase_contract_no}",
        }],
        "vessels": [],
        "documents": [],
        "customer_receipts": receipts or [],
        "bank_repayments": [],
        "raw": {"sheet": "YOLANDA", "row_no": 2},
    }


def _email_record(purchase_contract_no, *, business_type="融资", source_record_key=None, receipts=None):
    return {
        "business_type": business_type,
        "business_no": "" if business_type == "融资" else purchase_contract_no,
        "business_key": f"source:email:{purchase_contract_no}",
        "source_business_key": f"source:email:{purchase_contract_no}",
        "trade_entity": "YOLANDA",
        "supplier_steel_mill": "东钢",
        "terminal_customer": "客户A",
        "product_name": "钢材",
        "contract_quantity_mt": 100,
        "source_type": "email",
        "source_snapshot_date": "2026-08-13",
        "source_version": "mail-v1",
        "source_record_key": source_record_key or f"email:{purchase_contract_no}",
        "contracts": [{
            "contract_no": purchase_contract_no,
            "purchase_contract_no": purchase_contract_no,
            "system_contract_no": "SYS-1",
            "source_key": f"contract:{purchase_contract_no}",
        }],
        "financings": [],
        "vessels": [],
        "documents": [],
        "customer_receipts": receipts or [],
        "bank_repayments": [],
        "raw": {"file": "脱敏邮件台账.xls", "sheet": "Sheet1", "row_no": 3},
    }


def test_pass_through_all_receipts_complete_but_missing_document_is_anomaly():
    record = _record(receipts=[{"receipt_date": "2026-08-12", "fully_received": True, "source_key": "r1"}])
    status, risk, anomalies = calculate_business(record)
    assert status == "已回款"
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


def test_partial_bank_repayments_do_not_complete_financing_until_full_amount():
    record = _record(
        business_type="融资",
        financings=[{
            "bank": "中信唐山",
            "amount": 1000,
            "financing_date": "2026-07-01",
            "original_due_date": "2026-12-30",
            "source_key": "f1",
        }],
    )
    record["bank_repayments"] = [{
        "repayment_date": "2026-08-10",
        "amount": 400,
        "financing_source_key": "f1",
        "completion_explicit": False,
        "source_key": "r1",
    }]
    status, _, _ = calculate_business(record)
    assert status != "已完结"

    record["bank_repayments"].append({
        "repayment_date": "2026-08-12",
        "amount": 600,
        "financing_source_key": "f1",
        "completion_explicit": False,
        "source_key": "r2",
    })
    status, _, _ = calculate_business(record)
    assert status == "已完结"


def test_financing_partial_receipt_stays_waiting_for_receipt_and_repayment_is_separate():
    record = _record(
        business_type="融资",
        financings=[{
            "bank": "中信唐山",
            "amount": 1000,
            "financing_date": "2026-07-01",
            "original_due_date": "2026-12-30",
            "source_key": "f1",
        }],
        receipts=[{"receipt_date": "2026-08-12", "fully_received": False, "source_key": "r1"}],
    )
    record["documents"] = [{"document_type": "交单", "document_date": "2026-08-10", "source_key": "d1"}]
    status, _, _ = calculate_business(record)
    assert status == "待收汇"


def test_pass_through_full_receipt_is_paid_but_not_settled_without_settlement_fact():
    record = _record(receipts=[{"receipt_date": "2026-08-12", "fully_received": True, "source_key": "r1"}])
    status, _, _ = calculate_business(record)
    assert status == "已回款"
    record["settlement_status"] = "已结算"
    status, _, _ = calculate_business(record)
    assert status == "已结算"


def test_whole_card_deletion_requires_same_missing_key_set_twice(lifecycle_db):
    record = _record()
    assert apply_source_batch(_batch([record]))["deleted_businesses"] == 0
    assert apply_source_batch(_batch([], "v2"))["deleted_businesses"] == 0
    assert list_businesses({"page": 1, "page_size": 20})["total"] == 1
    assert apply_source_batch(_batch([], "v3"))["deleted_businesses"] == 1
    assert list_businesses({"page": 1, "page_size": 20})["total"] == 0
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM order_lifecycle_businesses").fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) AS c FROM order_lifecycle_contracts").fetchone()["c"] == 1


def test_exact_cross_source_match_keeps_one_parent_and_deduplicates_children(lifecycle_db):
    wps = _wps_record("Y-2026-14", "P-001", "SYS-1")
    mail = _email_record("P-001", receipts=[{"receipt_date": "2026-08-12", "amount": 100, "source_key": "receipt:mail"}])
    apply_source_batch({**_batch([wps]), "source_type": "wps"})
    result = apply_source_batch({**_batch([mail]), "source_type": "email"})

    assert result["matched_records"] == 1
    assert list_businesses({"page": 1, "page_size": 20})["total"] == 1
    with db.connect() as conn:
        parent = conn.execute("SELECT * FROM order_lifecycle_businesses").fetchone()
        assert parent["business_no"] == "Y-2026-14"
        assert parent["source_type"] == "mixed"
        assert conn.execute("SELECT COUNT(*) AS c FROM order_lifecycle_contracts WHERE business_id = ?", (parent["id"],)).fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) AS c FROM order_lifecycle_financings WHERE business_id = ?", (parent["id"],)).fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) AS c FROM order_lifecycle_customer_receipts WHERE business_id = ?", (parent["id"],)).fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) AS c FROM order_lifecycle_business_sources WHERE business_id = ?", (parent["id"],)).fetchone()["c"] == 2


def test_missing_source_removes_only_source_owned_children_and_keeps_shared_facts(lifecycle_db):
    wps = _wps_record("Y-2026-14", "P-001", "SYS-1")
    mail = _email_record("P-001", receipts=[{"receipt_date": "2026-08-12", "amount": 100, "source_key": "receipt:mail"}])
    apply_source_batch({**_batch([wps]), "source_type": "wps"})
    apply_source_batch({**_batch([mail]), "source_type": "email"})
    apply_source_batch({**_batch([], "mail-v2"), "source_type": "email"})
    result = apply_source_batch({**_batch([], "mail-v3"), "source_type": "email"})

    assert result["deleted_businesses"] == 0
    with db.connect() as conn:
        parent = conn.execute("SELECT id FROM order_lifecycle_businesses WHERE source_active = 1").fetchone()
        assert parent is not None
        assert conn.execute("SELECT COUNT(*) AS c FROM order_lifecycle_contracts WHERE business_id = ?", (parent["id"],)).fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) AS c FROM order_lifecycle_financings WHERE business_id = ?", (parent["id"],)).fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) AS c FROM order_lifecycle_customer_receipts WHERE business_id = ?", (parent["id"],)).fetchone()["c"] == 0


def test_wps_repayment_and_source_receipt_fields_are_persisted(lifecycle_db):
    wps = _wps_record("Y-2026-20", "P-020", "SYS-20", receipts=[{"receipt_date": "2026-08-12", "fully_received": False, "source_key": "receipt:20"}], financings=[{
        "bank": "中信唐山", "amount": 2000, "financing_date": "2026-07-01", "original_due_date": "2026-12-30", "repayment_date": "2026-08-10", "repayment_status": "已还款", "source_key": "finance:P-020",
    }])
    wps["bank_repayments"] = [{"repayment_date": "2026-08-10", "amount": 2000, "currency": "CNY", "financing_source_key": "finance:P-020", "completion_explicit": True, "source_key": "repay:P-020"}]
    apply_source_batch({**_batch([wps]), "source_type": "wps"})
    with db.connect() as conn:
        parent = conn.execute("SELECT id, status FROM order_lifecycle_businesses").fetchone()
        receipt = conn.execute("SELECT fully_received FROM order_lifecycle_customer_receipts").fetchone()
        repayment = conn.execute("SELECT completion_explicit, currency FROM order_lifecycle_bank_repayments").fetchone()
    assert parent["status"] == "已完结"
    assert receipt["fully_received"] == 0
    assert repayment["completion_explicit"] == 1
    assert repayment["currency"] == "CNY"
    card = list_businesses({"page": 1, "page_size": 20})["records"][0]
    assert card["outstanding_financing_amount"] == 0
    assert card["repayment_progress"]["repaid_financing_count"] == 1
    assert card["repayment_progress"]["total_financing_count"] == 1


def test_conflicting_exact_contracts_are_pending_without_auto_merge(lifecycle_db):
    apply_source_batch({**_batch([_wps_record("Y-2026-14", "P-001", "SYS-1"), _wps_record("Y-2026-15", "P-002", "SYS-2")]), "source_type": "wps"})
    conflict = _email_record("P-001", source_record_key="email:conflict")
    conflict["contracts"].append({"contract_no": "P-002", "purchase_contract_no": "P-002", "system_contract_no": "SYS-2", "source_key": "contract:P-002"})
    result = apply_source_batch({**_batch([conflict], "mail-v2"), "source_type": "email"})

    assert result["pending_match_candidates"] == 1
    assert list_businesses({"page": 1, "page_size": 20})["total"] == 2
    with db.connect() as conn:
        candidate = conn.execute("SELECT * FROM order_lifecycle_match_candidates WHERE status = 'open'").fetchone()
        assert candidate is not None
        assert "禁止自动合并" in candidate["reason"]


def test_unmatched_financing_mail_does_not_create_temporary_parent(lifecycle_db):
    mail = _email_record("P-404")
    result = apply_source_batch({**_batch([mail], "mail-v3"), "source_type": "email"})

    assert result["pending_match_candidates"] == 1
    assert list_businesses({"page": 1, "page_size": 20})["total"] == 0
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM order_lifecycle_businesses").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) AS c FROM order_lifecycle_match_candidates WHERE status = 'open'").fetchone()["c"] == 1


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


def test_manual_fcr_recalculates_risk_without_changing_business_status(lifecycle_db):
    record = _record(
        business_type="融资",
        financings=[{
            "bank": "中信唐山",
            "amount": 1000,
            "financing_date": date.today().isoformat(),
            "original_due_date": (date.today() + timedelta(days=60)).isoformat(),
            "source_key": "f1",
        }],
        receipts=[],
    )
    record["vessels"] = [{"vessel_name": "脱敏船", "latest_shipment_date": (date.today() + timedelta(days=2)).isoformat(), "source_key": "v1"}]
    apply_source_batch(_batch([record]))
    with db.connect() as conn:
        business_id = conn.execute("SELECT id FROM order_lifecycle_businesses").fetchone()["id"]
    node_result = order_lifecycle_node_confirmation(
        business_id,
        NodeConfirmationRequest(node="集港", confirmed=True, date=date.today().isoformat()),
        {"id": 1, "role": "管理员", "name": "测试管理员"},
    )
    assert node_result["status"] == "已集港"
    no_fcr = list_businesses({"page": 1, "page_size": 20})["records"][0]
    assert no_fcr["status"] == "已集港"
    assert no_fcr["risk_level"] == "高风险"

    result = set_manual_fcr(business_id, True, {"id": 1, "role": "管理员", "name": "测试管理员"}, "脱敏测试")
    assert result["status"] == "已集港"
    assert result["risk_level"] == "中风险"
    refreshed = list_businesses({"page": 1, "page_size": 20})["records"][0]
    assert refreshed["fcr"] is True


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
    assert result["summary"]["已完结"] == 1


def test_detail_edit_child_records_permissions_and_audit(lifecycle_db):
    record = _record(business_type="融资", financings=[{
        "bank": "中信唐山", "amount": 1000, "financing_date": "2026-07-01", "original_due_date": "2026-12-30", "source_key": "f1",
    }])
    apply_source_batch(_batch([record]))
    with db.connect() as conn:
        business_id = conn.execute("SELECT id FROM order_lifecycle_businesses").fetchone()["id"]
    admin = {"id": 1, "role": "管理员", "name": "测试管理员"}
    detail = order_lifecycle_business_detail(business_id, admin)
    assert len(detail["sections"]) == 8
    assert detail["can_sensitive"] is True

    order_lifecycle_override(business_id, ManualOverrideRequest(field_name="terminal_customer", value="人工客户", note="脱敏测试"), admin)
    contract_key = detail["contracts"][0]["source_key"]
    order_lifecycle_child_override(
        business_id,
        LifecycleChildOverrideRequest(collection="contracts", source_key=contract_key, field_name="buyer", value="人工买方", note="合同回读修正"),
        admin,
    )
    order_lifecycle_child_record(
        business_id,
        LifecycleChildRecordRequest(collection="vessels", source_key="manual:vessel:1", value={"vessel_name": "人工船舶", "latest_shipment_date": "2026-08-20"}, note="人工补录"),
        admin,
    )

    refreshed = order_lifecycle_business_detail(business_id, admin)
    assert refreshed["terminal_customer"] == "人工客户"
    assert refreshed["contracts"][0]["buyer"] == "人工买方"
    assert any(item.get("manual_record") and item["vessel_name"] == "人工船舶" for item in refreshed["vessels"])
    assert {item["path"] for item in refreshed["audit"]} >= {"terminal_customer", f"contracts.{contract_key}.buyer", "vessels.manual:vessel:1"}

    with pytest.raises(HTTPException) as denied:
        order_lifecycle_override(business_id, ManualOverrideRequest(field_name="product_name", value="不应写入"), {"id": 1, "role": "guest"})
    assert denied.value.status_code == 403


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
