"""FastAPI boundary tests for the collector administration and device paths."""

from pathlib import Path
import os
import sys

import pytest
from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))

from app import db
from app import main
from app import trading_collector_service as service


def use_temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "collector-api.db")
    db.init_db()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    with TestClient(main.app) as test_client:
        yield test_client


def auth_headers(role="admin"):
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (role,)).fetchone()
    return {"Authorization": "Bearer " + db.create_session(row["id"])}


def account_id():
    with db.connect() as conn:
        return conn.execute(
            "SELECT id FROM trading_accounts WHERE account_code = 'hongyuan_futures'"
        ).fetchone()["id"]


def sample_payload():
    return {
        "source_event_key": "tradeid:m-api-001",
        "trade_date": "2026-09-02",
        "trade_time": "09:05:03",
        "trade_timestamp": "2026-09-02T09:05:03+08:00",
        "exchange": "DCE",
        "contract": "i2607-c-750",
        "raw_contract": "i2607-C-750",
        "asset_type": "option",
        "side": "买",
        "open_close": "开",
        "quantity": 1,
        "price": "12.5",
        "source_record_sha256": "b" * 64,
        "parser_version": "wh6-match-v1",
    }


def test_admin_pairing_code_is_redacted_and_non_admin_is_denied(client):
    response = client.post(
        "/api/trading-collector/admin/pairing-codes",
        json={"account_id": account_id()},
        headers=auth_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["code"].startswith("LTM1-S-")
    assert "code_hash" not in body
    assert "token" not in body

    with db.connect() as conn:
        admin = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
        conn.execute(
            "INSERT INTO users (name, username, department, password_hash, role) VALUES (?, ?, ?, ?, ?)",
            ("普通用户", "normal", "贸易处", db.password_hash("pw"), "用户"),
        )
        normal = conn.execute("SELECT id FROM users WHERE username = 'normal'").fetchone()
    denied = client.post(
        "/api/trading-collector/admin/pairing-codes",
        json={"account_id": account_id()},
        headers={"Authorization": "Bearer " + db.create_session(normal["id"])},
    )
    assert denied.status_code == 403


def test_device_activation_heartbeat_revoke_and_ingest_without_browser_session(client):
    pairing = client.post(
        "/api/trading-collector/admin/pairing-codes",
        json={"account_id": account_id()},
        headers=auth_headers(),
    ).json()
    activated = client.post(
        "/api/trading-collector/device/activate",
        json={"pairing_code": pairing["code"], "device_name": "测试电脑", "client_version": "0.3.0", "fingerprint": "fp-api"},
    )
    assert activated.status_code == 200
    token = activated.json()["token"]
    headers = {"X-Collector-Token": token}
    heartbeat = client.post("/api/trading-collector/device/heartbeat", json={"client_version": "0.3.0"}, headers=headers)
    assert heartbeat.status_code == 200
    ingested = client.post("/api/trading-collector/device/ingest", json={"observations": [sample_payload()]}, headers=headers)
    assert ingested.status_code == 200
    assert ingested.json()["accepted"] == 1

    devices = client.get("/api/trading-collector/admin/devices", headers=auth_headers())
    device_id = devices.json()["items"][0]["device_id"]
    revoked = client.post(f"/api/trading-collector/admin/devices/{device_id}/revoke", headers=auth_headers())
    assert revoked.status_code == 200
    after = client.post("/api/trading-collector/device/heartbeat", json={}, headers=headers)
    assert after.status_code == 401


def test_read_only_fill_query_requires_permission_and_hides_full_paths(client):
    pairing = client.post(
        "/api/trading-collector/admin/pairing-codes",
        json={"account_id": account_id()},
        headers=auth_headers(),
    ).json()
    token = client.post(
        "/api/trading-collector/device/activate",
        json={"pairing_code": pairing["code"], "device_name": "查询电脑", "client_version": "0.3.0", "fingerprint": "fp-query"},
    ).json()["token"]
    payload = sample_payload()
    payload["source_path"] = r"C:\Users\Alice\WH6\Record\20260902match.dat"
    client.post("/api/trading-collector/device/ingest", json={"observations": [payload]}, headers={"X-Collector-Token": token})

    response = client.get("/api/trading-collector/fills", headers=auth_headers())
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["data_status"] == "provisional"
    assert "C:" not in item["source_path"]

    guest = client.post("/api/auth/guest-login")
    guest_response = client.get(
        "/api/trading-collector/fills",
        headers={"Authorization": "Bearer " + guest.json()["token"]},
    )
    assert guest_response.status_code == 403


def test_device_collection_policy_is_bound_to_device_account_and_requires_token(client):
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO trading_import_batches
                (account_id, range_start, range_end, status, statement_type,
                 source_priority)
            VALUES (?, '2026-08-01', '2026-08-31', 'active', 'monthly', 200)
            """,
            (account_id(),),
        )
    pairing = client.post(
        "/api/trading-collector/admin/pairing-codes",
        json={"account_id": account_id()},
        headers=auth_headers(),
    ).json()
    token = client.post(
        "/api/trading-collector/device/activate",
        json={
            "pairing_code": pairing["code"],
            "device_name": "策略电脑",
            "client_version": "0.3.0",
            "fingerprint": "fp-policy",
        },
    ).json()["token"]

    response = client.get(
        "/api/trading-collector/device/collection-policy?account_id=999",
        headers={"X-Collector-Token": token},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == 2
    assert body["environment"] == "staging"
    assert body["history_start_date"] == "2026-09-01"
    assert body["minimum_client_version"] == "0.3.0"
    assert [(item["month"], item["source_batch_id"]) for item in body["closed_ranges"]] == [
        ("2026-08", 1)
    ]
    assert "account_id" not in body

    unauthenticated = client.get("/api/trading-collector/device/collection-policy")
    assert unauthenticated.status_code == 401


def test_admin_collection_policy_readback_is_account_scoped(client):
    with db.connect() as conn:
        db._exec(
            conn.cursor(),
            """
            INSERT INTO trading_import_batches
                (account_id, range_start, range_end, status, statement_type,
                 source_priority)
            VALUES (?, '2026-08-01', '2026-08-31', 'active', 'monthly', 200)
            """,
            (account_id(),),
        )

    response = client.get(
        f"/api/trading-collector/admin/collection-policy?account_id={account_id()}",
        headers=auth_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == 2
    assert body["environment"] == "staging"
    assert body["history_start_date"] == "2026-09-01"
    assert body["closed_ranges"][0]["month"] == "2026-08"


def test_admin_reconcile_endpoint_is_staging_only_and_returns_audit_counts(client):
    response = client.post(
        "/api/trading-collector/admin/reconcile",
        json={"account_id": account_id()},
        headers=auth_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["scanned"] >= 0
    assert body["scanned"] == body["changed"] + body["unchanged"]
    assert body["transaction_numbers_backfilled"] >= 0


def test_fill_api_exposes_server_pagination_contract(client):
    pairing = client.post(
        "/api/trading-collector/admin/pairing-codes",
        json={"account_id": account_id()},
        headers=auth_headers(),
    ).json()
    client.post(
        "/api/trading-collector/device/activate",
        json={
            "pairing_code": pairing["code"],
            "device_name": "分页电脑",
            "client_version": "0.3.0",
            "fingerprint": "fp-page",
        },
    )
    response = client.get(
        "/api/trading-collector/fills?page=1&page_size=50",
        headers=auth_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 1
    assert body["page_size"] == 50
    assert body["total_items"] == 0
    assert body["total_pages"] == 0
