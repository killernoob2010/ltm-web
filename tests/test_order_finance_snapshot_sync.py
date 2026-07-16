from __future__ import annotations

import json
import os
import sys
from copy import deepcopy

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


def valid_snapshot_payload(
    *item_nos,
    source_version="v20",
    source_success_at="2026-07-16T09:02:00+08:00",
):
    records = [
        {field: snapshot_record(item_no).get(field) for field in order_finance.FACT_FIELDS}
        for item_no in item_nos
    ]
    return {
        "schema_version": 1,
        "source_version": source_version,
        "source_success_at": source_success_at,
        "record_count": len(records),
        "facts_hash": order_finance.order_finance_facts_hash(records),
        "records": records,
    }


class StaticSnapshotClient:
    def __init__(self, payload):
        self.payload = payload

    def fetch_snapshot(self):
        return deepcopy(self.payload)


def active_business_keys():
    return {
        row["business_key"] for row in order_finance.list_order_finance_records()
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


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: {**payload, "records": [], "record_count": 0},
        lambda payload: {**payload, "record_count": payload["record_count"] + 1},
        lambda payload: {**payload, "facts_hash": "wrong"},
        lambda payload: {**payload, "schema_version": 2},
    ],
)
def test_invalid_follow_snapshot_preserves_staging_data(
    tmp_path, monkeypatch, mutate
):
    use_temp_db(tmp_path, monkeypatch)
    order_finance.apply_order_finance_snapshot(
        [snapshot_record("OLD")],
        sync_success_at="2026-07-15T17:02:00+08:00",
        source_version="v10",
        attempt_slot="2026-07-15T17:00+08:00",
    )
    payload = mutate(valid_snapshot_payload("NEW"))

    with pytest.raises(snapshot_sync.OrderFinanceSnapshotSyncError):
        snapshot_sync.run_order_finance_snapshot_follow(
            "2026-07-16T09:00+08:00",
            client=StaticSnapshotClient(payload),
        )

    assert active_business_keys() == {"ITEM|OLD|1"}
    status = order_finance.get_order_finance_sync_status()
    assert status["source_version"] == "v10"
    assert status["last_attempt_slot"] == "2026-07-15T17:00+08:00"


@pytest.mark.parametrize("invalid_key", ["", "ITEM|A|1"])
def test_follow_snapshot_rejects_empty_or_duplicate_business_keys(
    tmp_path, monkeypatch, invalid_key
):
    use_temp_db(tmp_path, monkeypatch)
    payload = valid_snapshot_payload("A", "B")
    payload["records"][1]["business_key"] = invalid_key
    payload["facts_hash"] = order_finance.order_finance_facts_hash(
        payload["records"]
    )

    with pytest.raises(snapshot_sync.OrderFinanceSnapshotSyncError):
        snapshot_sync.run_order_finance_snapshot_follow(
            "2026-07-16T09:00+08:00",
            client=StaticSnapshotClient(payload),
        )


def test_follower_defers_source_older_than_slot_without_claiming_it(
    tmp_path, monkeypatch
):
    use_temp_db(tmp_path, monkeypatch)
    payload = valid_snapshot_payload(
        "A",
        source_success_at="2026-07-16T08:59:59+08:00",
    )

    result = snapshot_sync.run_order_finance_snapshot_follow(
        "2026-07-16T09:00+08:00",
        client=StaticSnapshotClient(payload),
    )

    assert result == {"status": "deferred", "reason": "source_not_ready"}
    assert order_finance.get_order_finance_sync_status()["last_attempt_slot"] is None
    assert active_business_keys() == set()


def test_follower_applies_facts_and_preserves_staging_management_fields(
    tmp_path, monkeypatch
):
    use_temp_db(tmp_path, monkeypatch)
    existing = snapshot_record("A")
    existing["finance_amount_actual"] = 1
    order_finance.apply_order_finance_snapshot([existing])
    record_id = order_finance.list_order_finance_records()[0]["id"]
    order_finance.update_management_fields(
        record_id,
        {"manager_note": "staging-only"},
        updated_by="pytest",
    )
    payload = valid_snapshot_payload("A")

    result = snapshot_sync.run_order_finance_snapshot_follow(
        "2026-07-16T09:00+08:00",
        client=StaticSnapshotClient(payload),
    )

    assert result["status"] == "success"
    assert result["updated"] == 1
    updated = order_finance.list_order_finance_records()[0]
    assert updated["finance_amount_actual"] == 10_000_000
    assert updated["manager_note"] == "staging-only"
    assert order_finance.get_order_finance_sync_status()["source_version"] == "v20"


def test_follower_same_version_and_hash_reports_zero_changes(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    payload = valid_snapshot_payload("A")
    snapshot_sync.run_order_finance_snapshot_follow(
        "2026-07-16T09:00+08:00",
        client=StaticSnapshotClient(payload),
    )

    result = snapshot_sync.run_order_finance_snapshot_follow(
        "2026-07-16T17:00+08:00",
        client=StaticSnapshotClient(
            valid_snapshot_payload(
                "A", source_success_at="2026-07-16T17:02:00+08:00"
            )
        ),
    )

    assert result == {
        "status": "success",
        "inserted": 0,
        "updated": 0,
        "archived": 0,
        "changed_count": 0,
    }


def test_follower_source_shrink_requires_same_candidate_twice(
    tmp_path, monkeypatch
):
    use_temp_db(tmp_path, monkeypatch)
    order_finance.apply_order_finance_snapshot(
        [snapshot_record("A"), snapshot_record("B")],
        sync_success_at="2026-07-15T17:02:00+08:00",
        source_version="v10",
        attempt_slot="2026-07-15T17:00+08:00",
    )
    client = StaticSnapshotClient(valid_snapshot_payload("A", source_version="v21"))

    first = snapshot_sync.run_order_finance_snapshot_follow(
        "2026-07-16T09:00+08:00", client=client
    )
    second = snapshot_sync.run_order_finance_snapshot_follow(
        "2026-07-16T09:00+08:00", client=client
    )

    assert first == {
        "status": "deferred",
        "reason": "source_shrink_confirmation",
        "changed_count": 0,
    }
    assert second["status"] == "success"
    assert second["archived"] == 1
    assert active_business_keys() == {"ITEM|A|1"}
