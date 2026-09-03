"""Read-only V2 option volume/current-position API contract tests."""

from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))

from app import db
from app import main


def use_temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "collector-positions-api.db")
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


def sample_payload(*, event_key="tradeid:api-v2-option", contract="i2607-c-750", asset_type="option"):
    return {
        "source_event_key": event_key,
        "trade_date": "2026-09-03",
        "trade_time": "09:05:03",
        "trade_timestamp": "2026-09-03T09:05:03+08:00",
        "exchange": "DCE",
        "contract": contract,
        "raw_contract": contract,
        "asset_type": asset_type,
        "side": "买",
        "open_close": "开",
        "quantity": 1,
        "price": "12.5",
        "source_record_sha256": "b" * 64,
        "parser_version": "wh6-match-v1",
    }


def position_payload(*, snapshot_at="2026-09-03T09:05:00+08:00", source_hash="c" * 64):
    return {
        "source_snapshot_key": "snapshot:" + snapshot_at,
        "trade_date": "2026-09-03",
        "snapshot_time": "09:05:00",
        "snapshot_timestamp": snapshot_at,
        "complete": True,
        "rows": [
            {
                "contract": "i2607-c-750",
                "raw_contract": "i2607-C-750",
                "asset_type": "option",
                "exchange": "DCE",
                "direction": "long",
                "quantity": 2,
                "today_quantity": 1,
                "yesterday_quantity": 1,
                "average_price": "12.50",
            },
            {
                "contract": "i2607",
                "raw_contract": "i2607",
                "asset_type": "future",
                "exchange": "DCE",
                "direction": "long",
                "quantity": 9,
            },
        ],
        "source_snapshot_sha256": source_hash,
        "parser_version": "wh6-position-json-v1",
        "source_path": r"C:\Users\Alice\WH6\Record\20260903position.dat",
    }


def activate_device_via_api(client):
    pairing = client.post(
        "/api/trading-collector/admin/pairing-codes",
        json={"account_id": account_id()},
        headers=auth_headers(),
    ).json()
    return client.post(
        "/api/trading-collector/device/activate",
        json={
            "pairing_code": pairing["code"],
            "device_name": "V2 API 电脑",
            "client_version": "0.2.0",
            "fingerprint": "fp-v2-api",
        },
    ).json()["token"]


def test_option_volume_and_current_positions_are_option_only(client):
    token = activate_device_via_api(client)
    future = sample_payload(event_key="tradeid:api-v2-future", contract="i2607", asset_type="future")
    ingested = client.post(
        "/api/trading-collector/device/ingest",
        headers={"X-Collector-Token": token},
        json={
            "observations": [sample_payload(), future],
            "position_snapshots": [position_payload()],
        },
    )
    assert ingested.status_code == 200
    assert ingested.json()["accepted"] == 2
    assert ingested.json()["position_accepted"] == 1

    volume = client.get(
        "/api/trading-collector/option-volume?trade_date=2026-09-03",
        headers=auth_headers(),
    )
    positions = client.get("/api/trading-collector/positions/current", headers=auth_headers())
    assert volume.status_code == 200
    assert volume.json()["total_quantity"] == 1
    assert all(item["asset_type"] == "option" for item in volume.json()["items"])
    assert positions.status_code == 200
    assert positions.json()["items"][0]["quantity"] == 2
    assert all(item["asset_type"] == "option" for item in positions.json()["items"])
    assert all("source_path" not in item for item in positions.json()["items"])


def test_current_positions_expose_stale_message_and_guest_is_denied(client):
    token = activate_device_via_api(client)
    response = client.post(
        "/api/trading-collector/device/ingest",
        headers={"X-Collector-Token": token},
        json={"position_snapshots": [position_payload(snapshot_at="2026-09-03T00:00:00+08:00")]},
    )
    assert response.status_code == 200
    current = client.get("/api/trading-collector/positions/current", headers=auth_headers())
    assert current.json()["is_expired"] is True
    assert current.json()["message"] == "持仓数据可能已过期"

    guest = client.post("/api/auth/guest-login")
    denied = client.get(
        "/api/trading-collector/option-volume",
        headers={"Authorization": "Bearer " + guest.json()["token"]},
    )
    assert denied.status_code == 403


def test_position_request_size_and_revoked_device_are_rejected(client):
    token = activate_device_via_api(client)
    oversized = client.post(
        "/api/trading-collector/device/ingest",
        headers={"X-Collector-Token": token},
        json={"position_snapshots": [position_payload(source_hash=("a" * 63) + str(index % 10)) for index in range(101)]},
    )
    assert oversized.status_code == 400
    assert oversized.json()["detail"]["code"] == "batch_too_large"

    device_id = client.get("/api/trading-collector/admin/devices", headers=auth_headers()).json()["items"][0]["device_id"]
    assert client.post(f"/api/trading-collector/admin/devices/{device_id}/revoke", headers=auth_headers()).status_code == 200
    revoked = client.post(
        "/api/trading-collector/device/ingest",
        headers={"X-Collector-Token": token},
        json={"position_snapshots": []},
    )
    assert revoked.status_code == 401
