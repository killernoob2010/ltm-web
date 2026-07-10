import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import db, main, permissions
from app.permissions import can, get_user_permissions
from app.user_policy import temporary_password_policy
from app.order_finance import ContractReminderRequest, order_finance_contract_reminder
from app.main import (
    delete_strategy_position,
    get_user_permissions as get_managed_user_permissions,
    list_users,
    me,
    set_user_permissions,
    update_user,
    UserIn,
    PermissionsBatchIn,
)
from fastapi import HTTPException


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


def test_postgres_rewrite_handles_indented_insert_or_ignore(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/test")

    rewritten = db._pg_rewrite(
        """
        INSERT OR IGNORE INTO module_permissions
            (user_id, module_code, can_view)
        VALUES (?, ?, ?)
        """
    )

    assert "INSERT OR IGNORE" not in rewritten
    assert rewritten.startswith("INSERT INTO module_permissions")
    assert rewritten.endswith("ON CONFLICT DO NOTHING")


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


def test_order_finance_reminder_requires_edit_permission(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    guest = db.ensure_guest_user()

    try:
        order_finance_contract_reminder(
            "H-2026-3",
            ContractReminderRequest(manager_note="无权修改", next_follow_up_date="2026-07-20"),
            user=guest,
        )
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("order finance reminder should require edit permission")


def test_guest_is_system_identity_hidden_from_user_management(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    guest = db.ensure_guest_user()
    with db.connect() as conn:
        cur = conn.cursor()
        admin = db._exec(cur, "SELECT * FROM users WHERE name = ?", ("admin",)).fetchone()

    assert me(user=guest)["name"] == "访客"
    assert me(user=guest)["role"] == "访客"

    users = list_users(user=dict(admin))["users"]
    assert all(row["name"] != "guest" for row in users)

    try:
        update_user(
            guest["id"],
            UserIn(name="访客", department="访客", password="", role="guest"),
            user=dict(admin),
        )
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("guest user should not be editable")

    try:
        get_managed_user_permissions(guest["id"], user=dict(admin))
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("guest permissions should not be managed through user management")

    try:
        set_user_permissions(
            guest["id"],
            PermissionsBatchIn(permissions=[]),
            user=dict(admin),
        )
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("guest permissions should be fixed by backend")


def test_auth_migration_adds_identity_password_and_sensitive_columns(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)

    with db.connect() as conn:
        user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        permission_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(module_permissions)").fetchall()
        }
        admin = conn.execute(
            "SELECT name, username, password_change_recommended FROM users WHERE name = ?",
            ("admin",),
        ).fetchone()
        sensitive_count = conn.execute(
            "SELECT COUNT(*) AS c FROM module_permissions WHERE user_id = (SELECT id FROM users WHERE name = ?) AND can_sensitive = 1",
            ("admin",),
        ).fetchone()["c"]

    assert {"username", "password_change_recommended"} <= user_columns
    assert "can_sensitive" in permission_columns
    assert admin["username"] == "admin"
    assert admin["password_change_recommended"] == 0
    assert sensitive_count == len(db.MODULES)


def test_legacy_sqlite_auth_migration_is_idempotent_and_allows_duplicate_display_names(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "legacy.db")
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            department TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT '启用',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE module_permissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            module_code TEXT NOT NULL,
            can_view INTEGER NOT NULL DEFAULT 1,
            can_edit INTEGER NOT NULL DEFAULT 0,
            UNIQUE(user_id, module_code)
        );
        CREATE TABLE user_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT '活跃'
        );
        INSERT INTO users (name, department, password_hash, role) VALUES ('张三', '贸易处', 'hash', '用户');
        INSERT INTO users (name, department, password_hash, role) VALUES ('历史管理员', '期货组', 'hash', '管理员');
        INSERT INTO module_permissions (user_id, module_code, can_view, can_edit) VALUES (1, 'info_summary', 1, 1);
        """
    )

    db.migrate_auth_schema(conn)
    db.migrate_auth_schema(conn)
    conn.execute(
        "INSERT INTO users (name, username, department, password_hash, role) VALUES (?, ?, ?, ?, ?)",
        ("张三", "zhangsan2", "贸易处", "hash", "用户"),
    )
    migrated = conn.execute("SELECT * FROM users WHERE id = 1").fetchone()
    permission = conn.execute("SELECT * FROM module_permissions WHERE user_id = 1").fetchone()

    assert migrated["username"] == "张三"
    assert permission["can_sensitive"] == 1
    assert conn.execute("SELECT department FROM users WHERE id = 2").fetchone()["department"] == "管理部门"
    assert conn.execute("SELECT COUNT(*) AS c FROM users WHERE name = '张三'").fetchone()["c"] == 2
    conn.close()


def test_department_and_leader_default_permission_levels():
    trade = permissions.default_permission_levels("\u8d38\u6613\u5904", "\u7528\u6237")
    futures = permissions.default_permission_levels("\u671f\u8d27\u7ec4", "\u7528\u6237")
    finance = permissions.default_permission_levels("\u8d22\u4f01\u5904", "\u7528\u6237")
    treasury = permissions.default_permission_levels("\u8d44\u91d1\u5904", "\u7528\u6237")
    management = permissions.default_permission_levels("\u7ba1\u7406\u90e8\u95e8", "\u7528\u6237")
    leader = permissions.default_permission_levels("\u8d38\u6613\u5904", "\u9886\u5bfc")

    assert trade["info_summary"] == "operate"
    assert trade["data_visualization_chart"] == "operate"
    assert trade["order_finance_progress"] == "none"
    assert futures["sh_junneng"] == "operate"
    assert finance["order_finance_progress"] == "operate"
    assert treasury["order_finance_capital"] == "operate"
    assert management["sh_junneng"] == "operate"
    assert management["user_management"] == "none"
    assert all(leader[code] == "view" for code in permissions.ACTIVE_BUSINESS_MODULES)
    assert leader["user_management"] == "none"
    assert leader["steel_export"] == "none"


def test_company_leader_is_allowed_and_keeps_leader_view_defaults():
    assert "公司领导" in permissions.DEPARTMENTS
    levels = permissions.default_permission_levels("公司领导", "领导")
    assert {code for code, level in levels.items() if level == "view"} == permissions.ACTIVE_BUSINESS_MODULES
    assert all(level in {"none", "view"} for level in levels.values())


def test_temporary_password_policy_covers_roster_rules_and_cao_xiang_exception():
    cases = [
        (("曹骧", "caoxiang", "期货组", "领导"), "caoxiang", "cao_xiang_exception"),
        (("张胜根", "zhangshenggen", "公司领导", "领导"), "zhangshenggen12345", "leader_12345"),
        (("龙云飞", "longyunfei", "贸易处", "用户"), "longyunfei", "trade_or_futures_plain"),
        (("鲍歆禹", "baoxinyu", "期货组", "用户"), "baoxinyu", "trade_or_futures_plain"),
        (("张立", "zhangli", "财企处", "用户"), "zhangli123", "finance_or_treasury_123"),
        (("刘楠", "liunan", "资金处", "用户"), "liunan123", "finance_or_treasury_123"),
    ]
    for args, password, rule in cases:
        result = temporary_password_policy(*args)
        assert result == {"temporary_password": password, "password_rule": rule}


def test_sensitive_actions_are_separate_and_backend_is_admin_only(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (name, username, department, password_hash, role) VALUES (?, ?, ?, ?, ?)",
            ("\u6d4b\u8bd5\u7528\u6237", "testuser", "\u8d38\u6613\u5904", db.password_hash("test1234"), "\u7528\u6237"),
        )
        user_id = cur.lastrowid
        cur.execute(
            "INSERT INTO module_permissions (user_id, module_code, can_view, can_edit, can_sensitive) VALUES (?, ?, 1, 1, 0)",
            (user_id, "info_summary"),
        )
        cur.execute(
            "INSERT INTO module_permissions (user_id, module_code, can_view, can_edit, can_sensitive) VALUES (?, ?, 1, 1, 1)",
            (user_id, "user_management"),
        )
        cur.execute(
            "INSERT INTO module_permissions (user_id, module_code, can_view, can_edit, can_sensitive) VALUES (?, ?, 1, 1, 0)",
            (user_id, "mid_event_monitor"),
        )
        user = dict(cur.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())

    assert permissions.can(user, "alert.realtime_summary", "edit")
    assert not permissions.can(user, "alert.realtime_summary", "delete")
    assert not permissions.can(user, "alert.realtime_summary", "export")
    assert not permissions.can(user, "users", "manage")

    try:
        delete_strategy_position(999, user=user)
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("operate permission must not authorize a real delete endpoint")


def admin_user():
    with db.connect() as conn:
        return dict(conn.execute("SELECT * FROM users WHERE name = ?", ("admin",)).fetchone())


def test_user_preview_generates_pinyin_and_department_defaults(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "lazy_pinyin", lambda name: ["zhang", "san"])

    result = main.preview_user(
        main.UserPreviewIn(name="张三", username="", department="贸易处", role="用户", permissions=[]),
        user=admin_user(),
    )

    assert result["username"] == "zhangsan"
    assert result["temporary_password"] == "zhangsan"
    assert result["password_rule"] == "trade_or_futures_plain"
    assert result["username_available"] is True
    assert result["default_permissions"]["info_summary"] == "operate"
    assert result["default_permissions"]["order_finance_progress"] == "none"
    assert result["final_permissions"] == result["default_permissions"]


def test_create_user_uses_username_temporary_password_and_permission_snapshot(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    payload = main.UserIn(
        name="张三",
        username="zhangsan",
        department="贸易处",
        role="用户",
        permissions=[{"module_code": "order_finance_progress", "level": "view"}],
    )

    result = main.create_user(payload, user=admin_user())

    with db.connect() as conn:
        created = conn.execute("SELECT * FROM users WHERE id = ?", (result["id"],)).fetchone()
        order_permission = conn.execute(
            "SELECT can_view, can_edit, can_sensitive FROM module_permissions WHERE user_id = ? AND module_code = ?",
            (result["id"], "order_finance_progress"),
        ).fetchone()
    assert created["username"] == "zhangsan"
    assert created["password_change_recommended"] == 1
    assert db.verify_password("zhangsan", created["password_hash"])
    assert dict(order_permission) == {"can_view": 1, "can_edit": 0, "can_sensitive": 0}
    assert main.login(main.LoginRequest(username="zhangsan", password="zhangsan"))["user"]["name"] == "张三"
    listed = next(item for item in main.list_users(user=admin_user())["users"] if item["id"] == result["id"])
    assert listed["permission_summary"]["enabled"] > 0
    assert listed["permission_summary"]["sensitive"] == 0
    configurable = main.get_user_permissions(result["id"], user=admin_user())["permissions"]
    assert {item["module_code"] for item in configurable} == permissions.ACTIVE_BUSINESS_MODULES


def test_change_password_keeps_current_session_and_revokes_other_sessions(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    created = main.create_user(
        main.UserIn(name="李雷", username="lilei", department="期货组", role="用户", permissions=[]),
        user=admin_user(),
    )
    current_token = db.create_session(created["id"])
    other_token = db.create_session(created["id"])
    current = db.get_user_by_token(current_token)

    main.change_password(
        main.ChangePasswordIn(current_password="lilei", new_password="Secure8899"),
        user=current,
        authorization=f"Bearer {current_token}",
    )

    with db.connect() as conn:
        updated = conn.execute("SELECT * FROM users WHERE id = ?", (created["id"],)).fetchone()
    assert updated["password_change_recommended"] == 0
    assert db.verify_password("Secure8899", updated["password_hash"])
    assert db.get_user_by_token(current_token) is not None
    assert db.get_user_by_token(other_token) is None


def test_reset_and_disable_user_revoke_sessions_and_protect_last_admin(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    admin = admin_user()
    created = main.create_user(
        main.UserIn(name="王芳", username="wangfang", department="财企处", role="用户", permissions=[]),
        user=admin,
    )
    token = db.create_session(created["id"])

    reset = main.reset_user_password(created["id"], user=admin)
    assert reset["temporary_password"] == "wangfang123"
    assert db.get_user_by_token(token) is None

    token = db.create_session(created["id"])
    main.set_user_status(created["id"], main.UserStatusIn(status="停用"), user=admin)
    assert db.get_user_by_token(token) is None

    try:
        main.delete_user(created["id"], user=admin)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "历史" in exc.detail
    else:
        raise AssertionError("account with session history must not be physically deleted")

    with db.connect() as conn:
        conn.execute("UPDATE users SET status = '停用' WHERE role = '管理员' AND id != ?", (admin["id"],))
    try:
        main.set_user_status(admin["id"], main.UserStatusIn(status="停用"), user=admin)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "最后一名管理员" in exc.detail
    else:
        raise AssertionError("last enabled administrator must be protected")


def test_preview_create_and_reset_share_roster_password_policy(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        admin = dict(db._exec(conn.cursor(), "SELECT * FROM users WHERE name = ?", ("admin",)).fetchone())
    preview = main.preview_user(
        main.UserPreviewIn(name="张胜根", username="zhangshenggen", department="公司领导", role="领导"),
        user=admin,
    )
    assert preview["temporary_password"] == "zhangshenggen12345"
    assert preview["password_rule"] == "leader_12345"
    created = main.create_user(
        main.UserIn(name="张胜根", username="zhangshenggen", department="公司领导", role="领导"),
        user=admin,
    )
    with db.connect() as conn:
        row = db._exec(conn.cursor(), "SELECT * FROM users WHERE id = ?", (created["id"],)).fetchone()
    assert db.verify_password("zhangshenggen12345", row["password_hash"])
    reset = main.reset_user_password(created["id"], user=admin)
    assert reset["temporary_password"] == preview["temporary_password"]


def test_self_change_rejects_the_actual_generated_default_password(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(
            cur,
            "INSERT INTO users (name, username, department, password_hash, role) VALUES (?, ?, ?, ?, ?)",
            ("策略测试", "policyuser", "贸易处", db.password_hash("OldSecure8899"), "用户"),
        )
        user_id = db.last_insert_id(conn)
    token = db.create_session(user_id)
    user = db.get_user_by_token(token)
    with pytest.raises(HTTPException) as exc:
        main.change_password(
            main.ChangePasswordIn(current_password="OldSecure8899", new_password="policyuser"),
            user=user,
            authorization=f"Bearer {token}",
        )
    assert exc.value.status_code == 400
    assert "默认密码" in exc.value.detail


def test_admin_can_set_password_without_recommendation_or_plaintext_log(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        cur = conn.cursor()
        admin_user = dict(db._exec(cur, "SELECT * FROM users WHERE name = ?", ("admin",)).fetchone())
        db._exec(
            cur,
            "INSERT INTO users (name, username, department, password_hash, role) VALUES (?, ?, ?, ?, ?)",
            ("王景泽", "王景泽", "管理部门", db.password_hash("old-password"), "管理员"),
        )
        target_id = db.last_insert_id(conn)
    result = main.set_user_password(
        target_id,
        main.AdminSetPasswordIn(new_password="FixturePass5", password_change_recommended=False),
        user=admin_user,
    )
    assert result == {"ok": True}
    with db.connect() as conn:
        cur = conn.cursor()
        target = db._exec(cur, "SELECT password_hash, password_change_recommended FROM users WHERE id = ?", (target_id,)).fetchone()
        log = db._exec(cur, "SELECT description FROM operation_logs ORDER BY id DESC LIMIT 1").fetchone()
    assert db.verify_password("FixturePass5", target["password_hash"])
    assert target["password_change_recommended"] == 0
    assert "FixturePass5" not in log["description"]
