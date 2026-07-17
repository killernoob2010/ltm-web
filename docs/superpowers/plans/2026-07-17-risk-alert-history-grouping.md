# Risk Alert History Grouping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将风险预警改为普通用户仅管理本人数据、管理员管理全部数据的个人工作区，并把预警历史改为按规则汇总分页、展开后懒加载触发详情。

**Architecture:** 保留 `alert_settings` 与 `alert_history` 的现有关联，为已触发规则增加归档生命周期。后端新增规则汇总、单规则详情和整组历史删除接口，所有规则写操作统一经过归属校验；前端只加载当前规则页，展开时再请求详情。

**Tech Stack:** FastAPI、Pydantic、PostgreSQL/Supabase、SQLite、原生 JavaScript、HTML/CSS、pytest、Node.js `node:test`

## Global Constraints

- 普通用户只查看和管理自己创建的规则及历史。
- 管理员查看和管理全部规则及历史，可按设置人筛选。
- 管理员操作他人数据必须记录操作日志。
- 通知始终只发送给规则设置人，管理员不会收到他人通知。
- 预警历史只展示至少触发过一次的规则。
- 汇总层默认每页 10 条规则；详情展开后默认每次加载 20 条。
- 删除已触发规则时保留历史并显示“规则已删除”。
- 删除整组历史时，活跃规则保留，已归档规则同步物理清理。
- 时间继续显示到秒。
- 不改变预警计算公式、自动扫描频率或其他业务模块权限。
- 所有生产操作仍需单独 Gate B；本计划只自动执行到 Staging 验收。
- 不修改或提交当前工作区中与本任务无关的 `.gitignore`、备份文件、handoff 或同步脚本。

---

## File Structure

- Modify: `backend/app/db.py`
  - 增加 `alert_settings.archived_at` 的新建表定义和幂等迁移。
  - 增加单规则历史查询索引。
- Modify: `backend/app/main.py`
  - 增加风险预警管理员判断、规则归属校验和管理员审计辅助函数。
  - 调整规则列表、单条操作、批量操作和手动扫描的归属范围。
  - 实现规则归档、历史汇总、详情分页和整组删除接口。
- Modify: `frontend/index.html`
  - 增加管理员共享设置人筛选，把预警历史平铺表格替换为汇总列表和分页容器。
  - 更新风险预警静态资源版本。
- Modify: `frontend/app.js`
  - 增加历史分页、展开状态、详情缓存、加载更多、管理员筛选和删除整组历史交互。
  - 让拥有风险预警查看权限的非访客可以执行本模块日常操作。
- Modify: `frontend/styles.css`
  - 增加规则汇总行、展开详情、分页和移动端布局。
- Create: `tests/test_risk_alert_history_grouping.py`
  - 覆盖归属、管理员管理、归档、汇总、详情、删除、扫描和日志。
- Modify: `tests/test_risk_alert_owner_notifications.py`
  - 保持通知只属于设置人，补充管理员不会收到他人通知。
- Modify: `tests/risk_alert_frontend.test.mjs`
  - 覆盖新结构、分页、懒加载、管理员筛选和日常操作权限。
- Modify: `tests/auth_frontend.test.mjs`
  - 对齐风险预警按钮的权限语义。
- Modify: `版本更新记录.md`
  - Staging 实际部署和验收后补写记录。

---

### Task 1: Add Rule Archival and History Query Index

**Files:**
- Modify: `backend/app/db.py`
- Test: `tests/test_risk_alert_history_grouping.py`

**Interfaces:**
- Produces: nullable `alert_settings.archived_at: str | None`
- Produces: index `idx_alert_history_alert_time_id` on `(alert_id, alert_time DESC, id DESC)`
- Consumes: existing `db.migrate_alert_schema(conn)` initialization path

- [ ] **Step 1: Write failing SQLite migration tests**

Create `tests/test_risk_alert_history_grouping.py` with a temporary database helper and exact schema assertions:

```python
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
```

- [ ] **Step 2: Run the migration test and verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_risk_alert_history_grouping.py::test_alert_schema_has_archival_column_and_history_index
```

Expected: FAIL because `archived_at` and `idx_alert_history_alert_time_id` do not exist.

- [ ] **Step 3: Add fresh-schema columns and indexes**

In both PostgreSQL and SQLite `CREATE TABLE IF NOT EXISTS alert_settings` definitions, add:

```sql
archived_at TEXT,
```

After `alert_history` creation in both schema blocks, add:

```sql
CREATE INDEX IF NOT EXISTS idx_alert_history_alert_time_id
ON alert_history(alert_id, alert_time DESC, id DESC);
```

- [ ] **Step 4: Add idempotent old-schema migration**

Extend `migrate_alert_schema(conn)` so both database engines:

1. Add nullable `archived_at` only when missing.
2. Create `idx_alert_history_alert_time_id` with `IF NOT EXISTS`.
3. Preserve existing status, owner and history rows.

Use the existing PostgreSQL `information_schema.columns` and SQLite `PRAGMA table_info` patterns already present in this function.

- [ ] **Step 5: Add idempotency coverage**

Add:

```python
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
```

- [ ] **Step 6: Run migration tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_risk_alert_history_grouping.py -k schema
```

Expected: all schema tests PASS.

- [ ] **Step 7: Commit the migration**

```bash
git add backend/app/db.py tests/test_risk_alert_history_grouping.py
git commit -m "feat: add risk alert archival schema"
```

---

### Task 2: Enforce Owner Workspaces and Administrator Management

**Files:**
- Modify: `backend/app/main.py`
- Test: `tests/test_risk_alert_history_grouping.py`
- Modify: `tests/test_risk_alert_owner_notifications.py`

**Interfaces:**
- Produces: `is_risk_alert_admin(user: dict) -> bool`
- Produces: `load_risk_alert_for_action(cur, alert_id: int, user: dict, include_archived: bool = False)`
- Produces: `log_risk_alert_admin_action(user, setting, operation_type, description, deleted_count=None)`
- Changes: `scan_risk_alerts_once(mock: bool = False, creator_user_id: int | None = None) -> dict`
- Consumes: `require_view("risk_alert", user)` as the only module-entry permission gate

- [ ] **Step 1: Write failing owner/admin rule-list tests**

Add helpers that create one administrator and two ordinary users with risk-alert view access. Add:

```python
def test_users_only_list_their_own_rules_and_admin_lists_all(
    tmp_path, monkeypatch
):
    use_temp_db(tmp_path, monkeypatch)
    admin, first, second = create_risk_alert_users()
    first_id = create_rule(first, "第一人规则")
    second_id = create_rule(second, "第二人规则")

    assert [item["id"] for item in main.list_alert_settings(user=first)["items"]] == [first_id]
    assert [item["id"] for item in main.list_alert_settings(user=second)["items"]] == [second_id]
    assert {
        item["id"] for item in main.list_alert_settings(user=admin)["items"]
    } == {first_id, second_id}
```

Also assert users with `can_view=1, can_edit=0, can_sensitive=0` can create their own rules.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_risk_alert_history_grouping.py -k "own_rules or view_permission"
```

Expected: FAIL because lists are global and create still requires edit permission.

- [ ] **Step 3: Add shared risk-alert access helpers**

In `backend/app/main.py`, add narrowly scoped helpers near the risk-alert endpoints:

```python
def is_risk_alert_admin(user: dict) -> bool:
    return user.get("role") in {"管理员", "admin"}


def load_risk_alert_for_action(
    cur,
    alert_id: int,
    user: dict,
    include_archived: bool = False,
):
    sql = "SELECT * FROM alert_settings WHERE id = ?"
    params: list[object] = [alert_id]
    if not include_archived:
        sql += " AND archived_at IS NULL"
    if not is_risk_alert_admin(user):
        sql += " AND creator_user_id = ?"
        params.append(user["id"])
    row = db._exec(cur, sql, tuple(params)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="预警规则不存在")
    return row
```

Every risk-alert endpoint first calls `require_view("risk_alert", user)`. Do not call `require_edit` or sensitive delete permission for this module.

- [ ] **Step 4: Scope rule lists and single-rule writes**

Adjust:

- `create_alert_setting`
- `list_alert_settings`
- `update_alert_setting`
- `toggle_alert_setting`
- `simulate_alert_trigger`
- `delete_alert_setting`
- `mark_alert_history_read`

Rules:

- list excludes `archived_at IS NOT NULL`;
- ordinary user list adds `creator_user_id = current user`;
- administrator list supports optional `creator_user_id` filter;
- all single-rule writes call `load_risk_alert_for_action`;
- ordinary users receive 404 for another user’s rule.
- single-item acknowledgement joins `alert_history` to `alert_settings`; ordinary users can acknowledge only their own details, while administrators can acknowledge any detail and log an override when the owner differs.
- `mark_all_alert_history_read` remains scoped to the current account because it belongs to the personal notification panel, including for administrators.

- [ ] **Step 5: Add administrator audit helper and tests**

Implement:

```python
def log_risk_alert_admin_action(
    user: dict,
    setting,
    operation_type: str,
    description: str,
    deleted_count: int | None = None,
) -> None:
    if not is_risk_alert_admin(user) or setting["creator_user_id"] == user["id"]:
        return
    suffix = f"，删除历史 {deleted_count} 条" if deleted_count is not None else ""
    db.log_operation(
        user["id"],
        "risk_alert",
        operation_type,
        f"{description}；原设置人用户 ID {setting['creator_user_id']}{suffix}",
        "alert_settings",
        setting["id"],
    )
```

Add tests asserting administrator edit/toggle/simulate/delete of another user’s rule adds an operation log, while the owner editing their own rule does not add an administrator-override log.

- [ ] **Step 6: Scope manual scan without changing background scan**

Change:

```python
def scan_risk_alerts_once(
    mock: bool = False,
    creator_user_id: int | None = None,
) -> dict:
```

Build the settings query as:

```python
sql = """
SELECT *
FROM alert_settings
WHERE status = 'enabled' AND archived_at IS NULL
"""
params = []
if creator_user_id is not None:
    sql += " AND creator_user_id = ?"
    params.append(creator_user_id)
sql += " ORDER BY id"
```

The HTTP manual scan passes `None` for administrators and `user["id"]` for ordinary users. `alert_monitor_loop()` continues calling `scan_risk_alerts_once()` with no owner filter.

- [ ] **Step 7: Preserve owner-only notifications for administrators**

Add to `tests/test_risk_alert_owner_notifications.py`:

```python
def test_admin_does_not_receive_another_users_notifications(
    tmp_path, monkeypatch
):
    use_temp_db(tmp_path, monkeypatch)
    administrator = admin_user()
    ordinary_owner = create_futures_user(
        "普通设置人", "ordinary-risk-owner", administrator
    )
    trigger_for_owner(ordinary_owner)
    assert main.list_alert_notifications(user=administrator) == {
        "count": 0,
        "items": [],
    }
```

Do not broaden `/api/risk-alert/notifications`; it remains `creator_user_id = current user`.

- [ ] **Step 8: Run owner/admin tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_risk_alert_history_grouping.py \
  tests/test_risk_alert_owner_notifications.py
```

Expected: all tests PASS.

- [ ] **Step 9: Commit owner/admin isolation**

```bash
git add backend/app/main.py tests/test_risk_alert_history_grouping.py tests/test_risk_alert_owner_notifications.py
git commit -m "feat: scope risk alerts by owner"
```

---

### Task 3: Archive Triggered Rules and Delete History Groups

**Files:**
- Modify: `backend/app/main.py`
- Test: `tests/test_risk_alert_history_grouping.py`

**Interfaces:**
- Changes: `DELETE /api/risk-alert/settings/{alert_id}` returns `{"ok": True, "archived": bool}`
- Produces: `DELETE /api/risk-alert/history/rules/{alert_id}` returns `{"ok": True, "deleted": int, "rule_deleted": bool}`
- Consumes: `load_risk_alert_for_action(cur, alert_id, user, include_archived=True)`

- [ ] **Step 1: Write failing lifecycle tests**

Add four explicit tests:

```python
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
```

- [ ] **Step 2: Run lifecycle tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_risk_alert_history_grouping.py -k "delete_"
```

Expected: FAIL because deleting a rule currently deletes all history and no group-delete endpoint exists.

- [ ] **Step 3: Implement archive-on-delete**

Inside one transaction:

```python
history_count = db._exec(
    cur,
    "SELECT COUNT(*) AS c FROM alert_history WHERE alert_id = ?",
    (alert_id,),
).fetchone()["c"]
if history_count:
    db._exec(
        cur,
        """
        UPDATE alert_settings
        SET status = 'disabled',
            archived_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (alert_id,),
    )
    archived = True
else:
    db._exec(cur, "DELETE FROM alert_settings WHERE id = ?", (alert_id,))
    archived = False
```

Remove the existing unconditional `DELETE FROM alert_history` from rule deletion.

- [ ] **Step 4: Implement delete-history-group transaction**

Add:

```python
@app.delete("/api/risk-alert/history/rules/{alert_id}")
def delete_alert_history_group(alert_id: int, user=Depends(current_user)):
    require_view("risk_alert", user)
    with db.connect() as conn:
        cur = conn.cursor()
        setting = load_risk_alert_for_action(
            cur, alert_id, user, include_archived=True
        )
        count = db._exec(
            cur,
            "SELECT COUNT(*) AS c FROM alert_history WHERE alert_id = ?",
            (alert_id,),
        ).fetchone()["c"]
        if not count:
            raise HTTPException(status_code=404, detail="没有可删除的预警历史")
        db._exec(cur, "DELETE FROM alert_history WHERE alert_id = ?", (alert_id,))
        rule_deleted = bool(setting["archived_at"])
        if rule_deleted:
            db._exec(cur, "DELETE FROM alert_settings WHERE id = ?", (alert_id,))
    log_risk_alert_admin_action(
        user,
        setting,
        "删除他人预警历史",
        "管理员删除他人预警历史",
        deleted_count=count,
    )
    return {"ok": True, "deleted": int(count), "rule_deleted": rule_deleted}
```

Also call the normal operation logger for owner-initiated deletion, including the deleted count.

- [ ] **Step 5: Run lifecycle tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_risk_alert_history_grouping.py -k "delete_ or archive"
```

Expected: all lifecycle tests PASS.

- [ ] **Step 6: Commit lifecycle behavior**

```bash
git add backend/app/main.py tests/test_risk_alert_history_grouping.py
git commit -m "feat: preserve history for deleted alert rules"
```

---

### Task 4: Add Grouped Summary and Lazy Detail APIs

**Files:**
- Modify: `backend/app/main.py`
- Test: `tests/test_risk_alert_history_grouping.py`

**Interfaces:**
- Produces: `GET /api/risk-alert/history/summary?limit=10&offset=0&creator_user_id=`
- Produces: `GET /api/risk-alert/history/rules/{alert_id}?limit=20&offset=0`
- Summary item fields:
  - `alert_id`, `info_type`, `contract_year`, `contract_month`
  - `creator_user_id`, `creator`, `archived_at`, `rule_status`
  - `latest_current_value`, `latest_alert_value`, `latest_direction`
  - `latest_alert_time`, `alert_count`, `unread_count`
- Detail response: `items` plus standard `pagination`

- [ ] **Step 1: Write failing grouped-summary tests**

Create two rules with different numbers and timestamps of history. Assert:

```python
payload = main.list_alert_history_summary(limit=10, offset=0, user=owner)
assert payload["pagination"]["total"] == 2
assert payload["items"][0] == {
    "alert_id": latest_rule_id,
    "info_type": "最近规则",
    "contract_year": "2026",
    "contract_month": "09",
    "creator_user_id": owner["id"],
    "creator": owner["name"],
    "archived_at": None,
    "rule_status": "enabled",
    "latest_current_value": 12,
    "latest_alert_value": 10,
    "latest_direction": "向上突破",
    "latest_alert_time": latest_time,
    "alert_count": 3,
    "unread_count": 2,
}
```

Add pagination coverage with 12 triggered rules: page one contains 10 rules, page two contains 2, and `pagination.total == 12`.

- [ ] **Step 2: Run summary tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_risk_alert_history_grouping.py -k "summary or rule_pagination"
```

Expected: FAIL because the summary endpoint/function does not exist.

- [ ] **Step 3: Implement the database summary query**

Add the endpoint with an optional administrator-only owner filter:

```python
@app.get("/api/risk-alert/history/summary")
def list_alert_history_summary(
    limit: int = 10,
    offset: int = 0,
    creator_user_id: Optional[int] = None,
    user=Depends(current_user),
):
    require_view("risk_alert", user)
    limit = max(1, min(limit or 10, 50))
    offset = max(0, offset or 0)
    if not is_risk_alert_admin(user):
        creator_user_id = user["id"]
```

Use a window-function CTE supported by PostgreSQL and the project’s SQLite runtime:

```sql
WITH ranked_history AS (
    SELECT h.*,
           ROW_NUMBER() OVER (
               PARTITION BY h.alert_id
               ORDER BY h.alert_time DESC, h.id DESC
           ) AS row_num,
           COUNT(*) OVER (PARTITION BY h.alert_id) AS alert_count,
           SUM(
               CASE WHEN h.status = 'unread' THEN 1 ELSE 0 END
           ) OVER (PARTITION BY h.alert_id) AS unread_count
    FROM alert_history h
)
SELECT s.id AS alert_id, s.info_type, s.contract_year, s.contract_month,
       s.creator_user_id, s.creator, s.archived_at,
       s.status AS rule_status,
       h.current_value AS latest_current_value,
       h.alert_value AS latest_alert_value,
       h.direction AS latest_direction,
       h.alert_time AS latest_alert_time,
       h.alert_count, h.unread_count
FROM alert_settings s
JOIN ranked_history h ON h.alert_id = s.id AND h.row_num = 1
WHERE 1 = 1
```

Append:

- `AND s.creator_user_id = ?` for ordinary users;
- optional administrator `creator_user_id` filter;
- `ORDER BY h.alert_time DESC, h.id DESC LIMIT ? OFFSET ?`.

Use a separate `COUNT(*)` over `alert_settings JOIN (SELECT DISTINCT alert_id FROM alert_history)` with the same owner filter for `pagination.total`.

- [ ] **Step 4: Include administrator owner filters**

For administrators, return a top-level `owners` array containing distinct owners represented in the filtered history dataset:

```json
[
  {"id": 12, "name": "设置人甲"},
  {"id": 18, "name": "设置人乙"}
]
```

Ordinary-user responses return `owners: []`.

- [ ] **Step 5: Write failing detail pagination and access tests**

Add 25 history rows to one rule and assert:

```python
first = main.list_alert_history_details(
    alert_id, limit=20, offset=0, user=owner
)
second = main.list_alert_history_details(
    alert_id, limit=20, offset=20, user=owner
)
assert len(first["items"]) == 20
assert len(second["items"]) == 5
assert first["pagination"]["total"] == 25
assert first["items"][0]["alert_time"] > first["items"][-1]["alert_time"]
```

Assert another ordinary user receives 404 and administrator receives all 25 rows.

- [ ] **Step 6: Implement detail pagination**

Add:

```python
@app.get("/api/risk-alert/history/rules/{alert_id}")
def list_alert_history_details(
    alert_id: int,
    limit: int = 20,
    offset: int = 0,
    user=Depends(current_user),
):
```

Clamp `limit` to 1–100 and `offset` to zero or greater. Call `load_risk_alert_for_action(cur, alert_id, user, include_archived=True)`, count rows for the rule, then select:

```sql
SELECT id, alert_id, alert_time, current_value, alert_value,
       direction, status
FROM alert_history
WHERE alert_id = ?
ORDER BY alert_time DESC, id DESC
LIMIT ? OFFSET ?
```

- [ ] **Step 7: Keep compatibility history endpoint owner-scoped**

Retain the existing `GET /api/risk-alert/history` function for compatibility, but:

- ordinary users only receive events from their rules;
- administrators receive all events;
- no new frontend code calls this endpoint.

- [ ] **Step 8: Run grouped API tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_risk_alert_history_grouping.py
```

Expected: all grouping, pagination and access tests PASS.

- [ ] **Step 9: Commit grouped APIs**

```bash
git add backend/app/main.py tests/test_risk_alert_history_grouping.py
git commit -m "feat: add grouped alert history APIs"
```

---

### Task 5: Build Grouped History UI and Pagination

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Modify: `frontend/styles.css`
- Modify: `tests/risk_alert_frontend.test.mjs`
- Modify: `tests/auth_frontend.test.mjs`

**Interfaces:**
- Consumes: summary and detail API contracts from Task 4
- Produces: `loadAlertHistorySummary(resetPage = false)`
- Produces: `toggleAlertHistoryRule(alertId)`
- Produces: `loadMoreAlertHistory(alertId)`
- Produces: `deleteAlertHistoryGroup(alertId)`
- Produces state:
  - `alertHistoryPage`, `alertHistoryPageSize`, `alertHistoryTotal`
  - `riskAlertOwnerFilter`
  - `expandedAlertHistory: Map<number, {items, total, loading}>`

- [ ] **Step 1: Write failing frontend structure tests**

Update `tests/risk_alert_frontend.test.mjs` to assert:

```javascript
assert.match(html, /id="riskAlertOwnerFilter"/);
assert.match(html, /id="historySummaryList"/);
assert.match(html, /id="alertHistoryPagination"/);
assert.doesNotMatch(html, /<tbody id="historyTable"><\/tbody>/);
assert.match(appJs, /\/api\/risk-alert\/history\/summary/);
assert.match(appJs, /\/api\/risk-alert\/history\/rules\/\$\{alertId\}/);
assert.match(appJs, /function renderAlertHistoryPagination/);
assert.match(appJs, /function loadMoreAlertHistory/);
assert.match(appJs, /删除该规则的全部 .* 条预警历史/);
```

In `tests/auth_frontend.test.mjs`, assert risk-alert daily operation buttons depend on module view access and guest status rather than edit/sensitive levels.

- [ ] **Step 2: Run frontend tests and verify RED**

Run:

```bash
node --test tests/risk_alert_frontend.test.mjs tests/auth_frontend.test.mjs
```

Expected: FAIL because grouped history elements and functions do not exist.

- [ ] **Step 3: Replace the flat history table markup**

In `frontend/index.html`, add this shared administrator-only filter above the rules and history panels:

```html
<div id="riskAlertAdminFilters" class="alert-history-filters hidden">
  <label>
    设置人
    <select id="riskAlertOwnerFilter">
      <option value="">全部设置人</option>
    </select>
  </label>
</div>
```

Replace the flat history table with:

```html
<div id="historySummaryList" class="alert-history-summary-list"></div>
<div id="alertHistoryPagination" class="alert-history-pagination"></div>
```

Keep `historyCount` in the panel header.

- [ ] **Step 4: Add frontend state and separate rule/history loading**

Extend `state`:

```javascript
alertHistoryPage: 1,
alertHistoryPageSize: 10,
alertHistoryTotal: 0,
riskAlertOwnerFilter: "",
expandedAlertHistory: new Map(),
```

Change `loadRiskAlert()` so it passes the shared owner filter to the settings request, loads the filtered rules, and calls `loadAlertHistorySummary()` without requesting the old flat endpoint.

- [ ] **Step 5: Implement summary rendering**

`loadAlertHistorySummary(resetPage = false)` builds:

```javascript
const offset = (state.alertHistoryPage - 1) * state.alertHistoryPageSize;
const ownerQuery = state.riskAlertOwnerFilter
  ? `&creator_user_id=${encodeURIComponent(state.riskAlertOwnerFilter)}`
  : "";
const payload = await api(
  `/api/risk-alert/history/summary?limit=${state.alertHistoryPageSize}&offset=${offset}${ownerQuery}`,
);
```

Render each item as a summary row with:

- rule/contract and archived badge;
- setting owner;
- latest current value and alert value;
- count and unread count;
- latest direction and timestamp;
- “展开/收起” and “删除历史记录” buttons.

Use `formatAlertTime(item.latest_alert_time)` for all visible times.

- [ ] **Step 6: Implement lazy expansion and load-more**

On first expansion:

```javascript
const payload = await api(
  `/api/risk-alert/history/rules/${alertId}?limit=20&offset=0`,
);
state.expandedAlertHistory.set(alertId, {
  items: payload.items || [],
  total: payload.pagination?.total || 0,
  loading: false,
});
```

`loadMoreAlertHistory(alertId)` requests `offset = entry.items.length` and appends rows. Disable the button while loading and hide it when loaded length reaches total.

Single-item acknowledgement continues calling:

```javascript
await api(`/api/risk-alert/history/${historyId}/read`, {method: "POST"});
```

Then update the cached detail row and reload the summary so `unread_count` is accurate.

- [ ] **Step 7: Implement summary pagination and owner filter**

Render:

- total rule count;
- current page / total pages;
- previous and next buttons;
- explicit page-number buttons around the current page.

When changing pages:

```javascript
state.expandedAlertHistory.clear();
state.alertHistoryPage = nextPage;
await loadAlertHistorySummary();
```

Only show `riskAlertAdminFilters` when `state.user.role === "管理员"`. Populate `riskAlertOwnerFilter` from `payload.owners`. Changing owner reloads both the rule list and history summary, resets history to page 1, and clears expanded details.

- [ ] **Step 8: Implement delete-history confirmation**

Use:

```javascript
const confirmed = await confirmAction(
  "删除预警历史",
  `确认删除“${ruleLabel}”的全部 ${item.alert_count} 条预警历史？此操作不可撤销。`,
);
```

Call `DELETE /api/risk-alert/history/rules/${alertId}`. After success:

1. clear that rule’s detail cache;
2. reload summary;
3. if the current page becomes empty and page > 1, decrement page and reload;
4. reload settings because deleting an archived rule’s history may remove its tombstone.

- [ ] **Step 9: Change risk-alert controls to module-entry permission**

Define:

```javascript
function canUseRiskAlertWorkspace() {
  return !isGuest() && Boolean(modulePermission("risk_alert")?.can_view);
}
```

Use this for add, scan, batch enable/disable/delete, edit/toggle/simulate/delete, notification acknowledgement, history mark-read and delete-history controls. Backend ownership remains authoritative.

- [ ] **Step 10: Add focused styles**

Add classes without changing `.content-grid` or other pages:

```css
.alert-history-summary-list { display: grid; gap: 10px; }
.alert-history-summary { border: 1px solid var(--line); border-radius: 6px; background: #fff; }
.alert-history-summary-main { display: grid; grid-template-columns: minmax(220px, 1.4fr) repeat(5, minmax(110px, 1fr)) auto; gap: 12px; align-items: center; padding: 12px; }
.alert-history-details { border-top: 1px solid var(--line); padding: 0 12px 12px; }
.alert-history-pagination { display: flex; justify-content: flex-end; align-items: center; gap: 8px; margin-top: 12px; }
```

Add a mobile media rule that changes `.alert-history-summary-main` to one column and keeps action buttons reachable.

- [ ] **Step 11: Update static asset versions**

In `frontend/index.html`, update the risk-alert-affecting `app.js` and `styles.css` query versions to:

```text
risk-alert-history-grouping-20260717
```

Update all exact static-version assertions in Node tests.

- [ ] **Step 12: Run frontend tests and verify GREEN**

Run:

```bash
node --test tests/risk_alert_frontend.test.mjs tests/auth_frontend.test.mjs
node --check frontend/app.js
```

Expected: all focused Node tests PASS and syntax check exits 0.

- [ ] **Step 13: Commit grouped UI**

```bash
git add frontend/index.html frontend/app.js frontend/styles.css \
  tests/risk_alert_frontend.test.mjs tests/auth_frontend.test.mjs
git commit -m "feat: group alert history by rule"
```

---

### Task 6: Full Regression, Staging Deployment, and Acceptance

**Files:**
- Modify: `版本更新记录.md`
- Test: all Python and Node suites

**Interfaces:**
- Consumes: completed Tasks 1–5
- Produces: Staging deployment evidence and cleaned temporary data

- [ ] **Step 1: Run focused regression**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_risk_alert_history_grouping.py \
  tests/test_risk_alert_owner_notifications.py \
  tests/test_auth_permissions.py
node --test tests/risk_alert_frontend.test.mjs tests/auth_frontend.test.mjs
```

Expected: zero failures.

- [ ] **Step 2: Run full quality gate**

Run:

```bash
.venv/bin/python -m pytest -q
node --test tests/*.test.mjs
.venv/bin/python -m compileall -q backend/app
for file in frontend/*.js; do node --check "$file"; done
git diff --check
```

Expected: all tests pass, all syntax/compile checks exit 0, and `git diff --check` has no output.

- [ ] **Step 3: Review the release diff**

Verify:

```bash
git diff origin/main...HEAD -- \
  backend/app/db.py backend/app/main.py \
  frontend/index.html frontend/app.js frontend/styles.css \
  tests/test_risk_alert_history_grouping.py \
  tests/test_risk_alert_owner_notifications.py \
  tests/risk_alert_frontend.test.mjs tests/auth_frontend.test.mjs
```

Confirm no unrelated module behavior, production configuration, secrets or user-owned dirty files entered the commits.

- [ ] **Step 4: Push Staging**

```bash
git push origin staging
```

Expected: Render Staging auto-deploy starts from the new staging commit.

- [ ] **Step 5: Verify deployed identity before functional tests**

Open:

```text
https://ltm-web-staging.onrender.com/?codex=<staging-commit>
```

Verify:

- title is `轻量化交易管理系统 Web`;
- `app.js` and `styles.css` load `risk-alert-history-grouping-20260717`;
- no application error appears in the browser console;
- `/api/health` returns 200.

- [ ] **Step 6: Run three-account Staging acceptance**

Use one administrator and two ordinary accounts. Create uniquely named temporary rules:

```text
Codex历史分组验收-A-<timestamp>
Codex历史分组验收-B-<timestamp>
```

Generate at least:

- 12 triggered rules for account A, proving 10 + 2 rule pagination;
- 25 trigger rows on one A rule, proving 20 + 5 detail loading;
- 2 triggered rules for account B.

Verify:

1. A sees only A’s rules and history.
2. B sees only B’s rules and history.
3. Administrator sees both and owner filtering works.
4. Administrator changes one B rule and an audit log is created.
5. Administrator’s notification endpoint does not include A/B events.
6. Summary latest value, threshold, count, unread count, direction and time match generated events.
7. Detail expansion loads 20 then 5 without duplicates.
8. Deleting a triggered rule archives it and history shows “规则已删除”.
9. Deleting that history removes the archived rule tombstone.
10. Deleting active-rule history leaves the active rule.

- [ ] **Step 7: Clean temporary Staging data**

Delete the exact temporary history groups, rules and any temporary accounts created by the acceptance. Verify:

- no `Codex历史分组验收-%` rules remain;
- no history points to a missing rule;
- ordinary business rules and history counts return to their pre-test values;
- operation logs remain as audit evidence.

- [ ] **Step 8: Record Staging acceptance**

Add a top entry to `版本更新记录.md` containing:

- Staging-only environment boundary;
- feature behavior and permission model;
- migration and index;
- local test counts;
- deployed commit and static resource version;
- three-account acceptance results;
- temporary-data cleanup;
- rollback point;
- explicit statement that Production still requires Gate B.

- [ ] **Step 9: Commit and push the release record**

```bash
git add 版本更新记录.md
git commit -m "docs: record grouped alert history staging acceptance"
git push origin staging
```

- [ ] **Step 10: Record AI SDLC state**

Record:

- final local test counts;
- Staging T2 acceptance counts and evidence;
- Staging active version;
- transition to `release_close` with `waiting_gate_b`.

Do not approve Gate B, merge `main`, deploy Production, or write Production data.
