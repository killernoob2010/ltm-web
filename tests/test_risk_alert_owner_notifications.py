import os
import sys

import pytest

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


def create_futures_user(name, username, admin):
    created = main.create_user(
        main.UserIn(name=name, username=username, department="期货组", role="用户"),
        user=admin,
    )
    with db.connect() as conn:
        return dict(
            db._exec(
                conn.cursor(),
                "SELECT * FROM users WHERE id = ?",
                (created["id"],),
            ).fetchone()
        )


def trigger_for_owner(owner):
    alert_id = main.create_alert_setting(alert_payload(), user=owner)["id"]
    main.simulate_alert_trigger(alert_id, current_value=11, user=owner)
    with db.connect() as conn:
        history_id = db._exec(
            conn.cursor(),
            "SELECT id FROM alert_history WHERE alert_id = ?",
            (alert_id,),
        ).fetchone()["id"]
    return alert_id, history_id


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


def test_notifications_are_visible_only_to_alert_owner(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    owner = admin_user()
    other = create_futures_user("其他用户", "other-user", owner)
    _, history_id = trigger_for_owner(owner)

    assert [
        item["id"] for item in main.list_alert_notifications(user=owner)["items"]
    ] == [history_id]
    assert main.list_alert_notifications(user=other) == {"count": 0, "items": []}


def test_shared_history_identifies_notification_owner(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    owner = admin_user()
    _, history_id = trigger_for_owner(owner)

    history = main.list_alert_history(user=owner)["items"]

    assert history[0]["id"] == history_id
    assert history[0]["creator_user_id"] == owner["id"]


def test_other_user_cannot_acknowledge_owner_notification(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    owner = admin_user()
    other = create_futures_user("其他用户", "other-user", owner)
    _, history_id = trigger_for_owner(owner)

    with pytest.raises(main.HTTPException) as exc:
        main.mark_alert_history_read(history_id, user=other)
    assert exc.value.status_code == 404

    main.mark_alert_history_read(history_id, user=owner)
    with db.connect() as conn:
        status = db._exec(
            conn.cursor(),
            "SELECT status FROM alert_history WHERE id = ?",
            (history_id,),
        ).fetchone()["status"]
    assert status == "read"


def test_mark_all_read_updates_only_current_owner(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    first = admin_user()
    second = create_futures_user("第二用户", "second-user", first)
    _, first_history = trigger_for_owner(first)
    _, second_history = trigger_for_owner(second)

    main.mark_all_alert_history_read(user=first)

    with db.connect() as conn:
        rows = db._exec(
            conn.cursor(),
            "SELECT id, status FROM alert_history WHERE id IN (?, ?) ORDER BY id",
            (first_history, second_history),
        ).fetchall()
    assert {row["id"]: row["status"] for row in rows} == {
        first_history: "read",
        second_history: "unread",
    }
