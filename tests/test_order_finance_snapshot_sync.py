from __future__ import annotations

import json
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import db, order_finance
from app import order_finance_snapshot_sync as snapshot_sync


def use_temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "order_finance_snapshot.db")
    db.init_db()


def snapshot_record(item_no):
    return {
        "business_key": f"ITEM|{item_no}|1",
        "subsidiary": "北满",
        "source_file": "线上台账.xlsx",
        "source_sheet": "订单",
        "purchase_contract_no": f"C-{item_no}",
        "finance_amount_actual": 10_000_000,
        "finance_drawdown_date": "2026-06-01",
        "finance_due_date": "2026-08-01",
        "business_status": "存续",
        "source_json": json.dumps({"item_no": item_no}, ensure_ascii=False),
    }


def test_snapshot_requires_matching_server_secret(monkeypatch):
    monkeypatch.setenv(
        "ORDER_FINANCE_SNAPSHOT_SHARED_SECRET",
        "expected-secret-value",
    )

    with pytest.raises(HTTPException) as missing:
        snapshot_sync.get_order_finance_snapshot(None)
    assert missing.value.status_code == 404

    with pytest.raises(HTTPException) as wrong:
        snapshot_sync.get_order_finance_snapshot("Bearer wrong")
    assert wrong.value.status_code == 404


def test_snapshot_returns_only_versioned_fact_fields(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    order_finance.apply_order_finance_snapshot(
        [snapshot_record("A")],
        sync_success_at="2026-07-16T09:02:00+08:00",
        source_version="v20",
        attempt_slot="2026-07-16T09:00+08:00",
    )
    row_id = order_finance.list_order_finance_records()[0]["id"]
    order_finance.update_management_fields(
        row_id,
        {"manager_note": "production-only"},
        updated_by="pytest",
    )
    monkeypatch.setenv(
        "ORDER_FINANCE_SNAPSHOT_SHARED_SECRET",
        "expected-secret-value",
    )

    payload = snapshot_sync.get_order_finance_snapshot(
        "Bearer expected-secret-value"
    )

    assert payload["schema_version"] == 1
    assert payload["source_version"] == "v20"
    assert payload["source_success_at"] == "2026-07-16T09:02:00+08:00"
    assert payload["record_count"] == 1
    assert set(payload["records"][0]) == set(order_finance.FACT_FIELDS)
    assert "manager_note" not in payload["records"][0]
    assert payload["facts_hash"] == order_finance.order_finance_facts_hash(
        payload["records"]
    )


def test_snapshot_refuses_missing_successful_source(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    monkeypatch.setenv(
        "ORDER_FINANCE_SNAPSHOT_SHARED_SECRET",
        "expected-secret-value",
    )

    with pytest.raises(HTTPException) as captured:
        snapshot_sync.get_order_finance_snapshot(
            "Bearer expected-secret-value"
        )

    assert captured.value.status_code == 503
