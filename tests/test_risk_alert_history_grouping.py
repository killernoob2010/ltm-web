import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import db
from app import main


def use_temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "risk-alert-history.db")
    db.init_db()


def create_risk_alert_users():
    with db.connect() as conn:
        cur = conn.cursor()
        admin = dict(
            db._exec(
                cur,
                "SELECT * FROM users WHERE username = ?",
                ("admin",),
            ).fetchone()
        )
        users = []
        for name, username in (
            ("第一设置人", "risk-owner-first"),
            ("第二设置人", "risk-owner-second"),
        ):
            db._exec(
                cur,
                """
                INSERT INTO users
                    (name, username, password_hash, department, role, status)
                VALUES (?, ?, ?, '期货组', '用户', '启用')
                """,
                (name, username, db.password_hash("temporary")),
            )
            user_id = db.last_insert_id(conn)
            db._exec(
                cur,
                """
                INSERT INTO module_permissions
                    (user_id, module_code, can_view, can_edit, can_sensitive)
                VALUES (?, 'risk_alert', 1, 0, 0)
                """,
                (user_id,),
            )
            users.append(
                dict(
                    db._exec(
                        cur,
                        "SELECT * FROM users WHERE id = ?",
                        (user_id,),
                    ).fetchone()
                )
            )
    return admin, users[0], users[1]


def create_rule(user, info_type):
    payload = main.AlertSettingIn(
        info_type=info_type,
        contract_year="2026",
        contract_month="09",
        alert_value=10,
        direction="above",
        status="enabled",
    )
    return main.create_alert_setting(payload, user=user)["id"]


def get_rule(alert_id):
    with db.connect() as conn:
        row = db._exec(
            conn.cursor(),
            "SELECT * FROM alert_settings WHERE id = ?",
            (alert_id,),
        ).fetchone()
    return dict(row) if row else None


def history_count(alert_id):
    with db.connect() as conn:
        return db._exec(
            conn.cursor(),
            "SELECT COUNT(*) AS c FROM alert_history WHERE alert_id = ?",
            (alert_id,),
        ).fetchone()["c"]


def test_alert_schema_has_archival_column_and_history_index(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        cur = conn.cursor()
        columns = {
            row["name"]
            for row in db._exec(cur, "PRAGMA table_info(alert_settings)").fetchall()
        }
        indexes = {
            row["name"]
            for row in db._exec(cur, "PRAGMA index_list(alert_history)").fetchall()
        }

    assert "archived_at" in columns
    assert "idx_alert_history_alert_time_id" in indexes


def test_alert_schema_migration_is_idempotent(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        db.migrate_alert_schema(conn)
        db.migrate_alert_schema(conn)
        row = db._exec(
            conn.cursor(),
            "SELECT archived_at FROM alert_settings LIMIT 1",
        ).fetchone()

    assert row is None


def test_users_only_list_their_own_rules_and_admin_lists_all(
    tmp_path, monkeypatch
):
    use_temp_db(tmp_path, monkeypatch)
    admin, first, second = create_risk_alert_users()
    first_id = create_rule(first, "第一人规则")
    second_id = create_rule(second, "第二人规则")

    assert [item["id"] for item in main.list_alert_settings(user=first)["items"]] == [
        first_id
    ]
    assert [
        item["id"] for item in main.list_alert_settings(user=second)["items"]
    ] == [second_id]
    assert {
        item["id"] for item in main.list_alert_settings(user=admin)["items"]
    } == {first_id, second_id}


def test_view_permission_allows_daily_risk_alert_operations(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    _, owner, _ = create_risk_alert_users()

    alert_id = create_rule(owner, "查看权限可设置")

    assert alert_id


def test_admin_editing_another_users_rule_is_logged(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    admin, owner, _ = create_risk_alert_users()
    alert_id = create_rule(owner, "管理员代管")
    payload = main.AlertSettingIn(
        info_type="管理员修改后",
        contract_year="2026",
        contract_month="09",
        alert_value=12,
        direction="below",
        status="enabled",
    )

    main.update_alert_setting(alert_id, payload, user=admin)

    with db.connect() as conn:
        rows = db._exec(
            conn.cursor(),
            """
            SELECT operation_type, description
            FROM operation_logs
            WHERE module_code = 'risk_alert' AND entity_id = ?
            ORDER BY id
            """,
            (alert_id,),
        ).fetchall()
    assert any(
        row["operation_type"] == "管理员编辑他人预警"
        and f"原设置人用户 ID {owner['id']}" in row["description"]
        for row in rows
    )


def test_manual_scan_is_owner_scoped_but_admin_scan_is_global(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    admin, first, second = create_risk_alert_users()
    first_id = create_rule(first, "第一人扫描")
    second_id = create_rule(second, "第二人扫描")
    seen = []

    def fake_current_value(row, mock=False):
        seen.append(row["id"])
        return None

    monkeypatch.setattr(main, "calculate_alert_current_value", fake_current_value)

    main.scan_risk_alerts(user=first)
    assert seen == [first_id]

    seen.clear()
    main.scan_risk_alerts(user=admin)
    assert seen == [first_id, second_id]


def test_delete_untriggered_rule_physically_removes_it(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    _, owner, _ = create_risk_alert_users()
    alert_id = create_rule(owner, "未触发规则")

    assert main.delete_alert_setting(alert_id, user=owner) == {
        "ok": True,
        "archived": False,
    }
    assert get_rule(alert_id) is None


def test_delete_triggered_rule_archives_it_and_preserves_history(
    tmp_path, monkeypatch
):
    use_temp_db(tmp_path, monkeypatch)
    _, owner, _ = create_risk_alert_users()
    alert_id = create_rule(owner, "已触发规则")
    main.simulate_alert_trigger(alert_id, current_value=11, user=owner)

    result = main.delete_alert_setting(alert_id, user=owner)

    assert result == {"ok": True, "archived": True}
    setting = get_rule(alert_id)
    assert setting["archived_at"] is not None
    assert setting["status"] == "disabled"
    assert history_count(alert_id) == 1


def test_delete_active_rule_history_keeps_rule(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    _, owner, _ = create_risk_alert_users()
    alert_id = create_rule(owner, "保留活跃规则")
    main.simulate_alert_trigger(alert_id, current_value=11, user=owner)
    main.simulate_alert_trigger(alert_id, current_value=12, user=owner)

    result = main.delete_alert_history_group(alert_id, user=owner)

    assert result == {"ok": True, "deleted": 2, "rule_deleted": False}
    assert get_rule(alert_id) is not None
    assert history_count(alert_id) == 0


def test_delete_archived_rule_history_removes_rule_tombstone(
    tmp_path, monkeypatch
):
    use_temp_db(tmp_path, monkeypatch)
    _, owner, _ = create_risk_alert_users()
    alert_id = create_rule(owner, "清理归档规则")
    main.simulate_alert_trigger(alert_id, current_value=11, user=owner)
    main.simulate_alert_trigger(alert_id, current_value=12, user=owner)
    main.delete_alert_setting(alert_id, user=owner)

    result = main.delete_alert_history_group(alert_id, user=owner)

    assert result == {"ok": True, "deleted": 2, "rule_deleted": True}
    assert get_rule(alert_id) is None
    assert history_count(alert_id) == 0
