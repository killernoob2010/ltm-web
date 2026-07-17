import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import db, main


def use_temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "risk-alert.db")
    db.init_db()


def admin_user():
    with db.connect() as conn:
        return dict(
            db._exec(
                conn.cursor(),
                "SELECT * FROM users WHERE username = ?",
                ("admin",),
            ).fetchone()
        )


def alert_payload():
    return main.AlertSettingIn(
        info_type="卷螺差",
        contract_year="2026",
        contract_month="09",
        alert_value=10,
        direction="above",
        status="enabled",
    )


def test_create_alert_binds_authenticated_owner(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    owner = admin_user()

    alert_id = main.create_alert_setting(alert_payload(), user=owner)["id"]

    with db.connect() as conn:
        row = db._exec(
            conn.cursor(),
            "SELECT creator_user_id, creator, reminder_users FROM alert_settings WHERE id = ?",
            (alert_id,),
        ).fetchone()
    assert row["creator_user_id"] == owner["id"]
    assert row["creator"] == owner["name"]
    assert row["reminder_users"] == ""


def test_alert_migration_backfills_only_unique_creator_names(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(
            cur,
            "INSERT INTO users (name, username, password_hash, department, role) VALUES (?, ?, ?, ?, ?)",
            ("唯一设置人", "unique-owner", db.password_hash("x"), "期货组", "用户"),
        )
        unique_id = db.last_insert_id(conn)
        for username in ("duplicate-a", "duplicate-b"):
            db._exec(
                cur,
                "INSERT INTO users (name, username, password_hash, department, role) VALUES (?, ?, ?, ?, ?)",
                ("重名设置人", username, db.password_hash("x"), "期货组", "用户"),
            )
        db._exec(
            cur,
            "INSERT INTO alert_settings (info_type, contract_month, alert_value, creator) VALUES (?, ?, ?, ?)",
            ("卷螺差", "09", 10, "唯一设置人"),
        )
        unique_alert = db.last_insert_id(conn)
        db._exec(
            cur,
            "INSERT INTO alert_settings (info_type, contract_month, alert_value, creator) VALUES (?, ?, ?, ?)",
            ("卷螺差", "10", 11, "重名设置人"),
        )
        duplicate_alert = db.last_insert_id(conn)
        db._exec(
            cur,
            "UPDATE alert_settings SET creator_user_id = NULL WHERE id IN (?, ?)",
            (unique_alert, duplicate_alert),
        )

        db.migrate_alert_schema(conn)

        unique_row = db._exec(
            cur,
            "SELECT creator_user_id FROM alert_settings WHERE id = ?",
            (unique_alert,),
        ).fetchone()
        duplicate_row = db._exec(
            cur,
            "SELECT creator_user_id FROM alert_settings WHERE id = ?",
            (duplicate_alert,),
        ).fetchone()

    assert unique_row["creator_user_id"] == unique_id
    assert duplicate_row["creator_user_id"] is None
