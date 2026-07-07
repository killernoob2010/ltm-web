import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import db
from app.permissions import can, get_user_permissions


def use_temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "auth.db")
    db.init_db()


def test_pbkdf2_hash_verifies_and_legacy_sha256_is_detected():
    encoded = db.password_hash("admin")

    assert encoded.startswith("pbkdf2_sha256$")
    assert db.verify_password("admin", encoded)
    assert not db.verify_password("wrong", encoded)

    legacy = db.legacy_password_hash("admin")
    assert db.verify_password("admin", legacy)
    assert db.needs_password_upgrade(legacy)


def test_session_expires_at_blocks_old_token(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        cur = conn.cursor()
        user = db._exec(cur, "SELECT * FROM users WHERE name = ?", ("admin",)).fetchone()

    token = db.create_session(user["id"], ttl_hours=1)
    assert db.get_user_by_token(token)["name"] == "admin"

    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(cur, "UPDATE user_sessions SET expires_at = ? WHERE token = ?", (expired, token))

    assert db.get_user_by_token(token) is None


def test_guest_user_has_only_allowed_view_permissions(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    guest = db.ensure_guest_user()

    assert guest["name"] == "guest"
    assert can(guest, "alert.realtime_summary", "view")
    assert can(guest, "data_visualization.display", "view")
    assert not can(guest, "data_visualization.display", "export")
    assert not can(guest, "order_finance.records", "view")

    permissions = get_user_permissions(guest)
    assert "alert.realtime_summary:view" in permissions
    assert "data_visualization.display:view" in permissions
