# Risk Alert Owner Notification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bind each risk alert to its creating account, deliver and acknowledge notifications only for that owner, stack the rules and history panels vertically, and display alert timestamps only to seconds.

**Architecture:** Add a nullable `creator_user_id` to `alert_settings`, populate it from the authenticated user on creation, and backfill legacy rows only when the stored creator name uniquely identifies a user. Scope notification reads and acknowledgement writes by that stable owner ID. Keep shared management lists and calculation behavior unchanged, while applying a risk-alert-only CSS stack and a frontend timestamp formatter.

**Tech Stack:** FastAPI, Pydantic, SQLite/PostgreSQL compatibility helpers, vanilla JavaScript/CSS/HTML, pytest, Node.js built-in test runner.

## Global Constraints

- Work only on branch `staging`, Render `ltm-web-staging`, and Supabase `LTM WEB STAGING`.
- Do not modify `main`, Production Render, Production Supabase, production data, or production environment variables.
- Preserve the existing risk calculation, threshold, scan interval, trigger, shared settings list, and shared history list behavior.
- Do not delete `reminder_users`; stop using it for new notification routing.
- Do not add delegation, multiple recipients, ownership transfer, or external notification channels.
- Preserve unrelated dirty-worktree files and do not include them in commits.
- Use one primary agent; do not create an extra worktree or dispatch subagents.

---

### Task 1: Persist stable alert ownership and backfill unambiguous legacy alerts

**Files:**
- Modify: `backend/app/db.py:334-357`
- Modify: `backend/app/db.py:745-767`
- Modify: `backend/app/db.py:1948-1958`
- Modify: `backend/app/main.py:139-147`
- Modify: `backend/app/main.py:1485-1571`
- Create: `tests/test_risk_alert_owner_notifications.py`

**Interfaces:**
- Consumes: authenticated user mappings with integer `id` and string `name`.
- Produces: `alert_settings.creator_user_id: int | null`; `create_alert_setting(payload, user)` always writes the current user's ID and name; legacy rows are backfilled only for a unique `users.name` match.

- [ ] **Step 1: Write failing ownership and migration tests**

Create `tests/test_risk_alert_owner_notifications.py` with isolated SQLite setup and these assertions:

```python
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
        return dict(db._exec(conn.cursor(), "SELECT * FROM users WHERE username = ?", ("admin",)).fetchone())


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
        db._exec(cur, "UPDATE alert_settings SET creator_user_id = NULL WHERE id IN (?, ?)", (unique_alert, duplicate_alert))
        db.migrate_alert_schema(conn)
        unique_row = db._exec(cur, "SELECT creator_user_id FROM alert_settings WHERE id = ?", (unique_alert,)).fetchone()
        duplicate_row = db._exec(cur, "SELECT creator_user_id FROM alert_settings WHERE id = ?", (duplicate_alert,)).fetchone()

    assert unique_row["creator_user_id"] == unique_id
    assert duplicate_row["creator_user_id"] is None
```

- [ ] **Step 2: Run the ownership tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_risk_alert_owner_notifications.py`

Expected: FAIL because `creator_user_id` does not exist and `AlertSettingIn`/create logic still uses `reminder_users`.

- [ ] **Step 3: Add the schema column and idempotent backfill**

Add `creator_user_id INTEGER` to both fresh `alert_settings` table definitions. Replace `migrate_alert_schema` with PostgreSQL and SQLite branches that add the column if missing and run these equivalent unique-name backfills:

```python
def migrate_alert_schema(conn) -> None:
    """Keep alert recipients bound to stable user IDs across SQLite and PostgreSQL."""
    if _is_pg():
        cur = conn.cursor()
        cur.execute("ALTER TABLE alert_settings ADD COLUMN IF NOT EXISTS reminder_users TEXT DEFAULT ''")
        cur.execute("ALTER TABLE alert_settings ADD COLUMN IF NOT EXISTS creator_user_id INTEGER")
        cur.execute(
            """
            UPDATE alert_settings AS alert
            SET creator_user_id = matched.id
            FROM users AS matched
            WHERE alert.creator_user_id IS NULL
              AND alert.creator = matched.name
              AND (SELECT COUNT(*) FROM users AS candidate WHERE candidate.name = alert.creator) = 1
            """
        )
        conn.commit()
        return

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(alert_settings)").fetchall()}
    if "reminder_users" not in columns:
        conn.execute("ALTER TABLE alert_settings ADD COLUMN reminder_users TEXT DEFAULT ''")
    if "creator_user_id" not in columns:
        conn.execute("ALTER TABLE alert_settings ADD COLUMN creator_user_id INTEGER")
    conn.execute(
        """
        UPDATE alert_settings
        SET creator_user_id = (
            SELECT MIN(users.id) FROM users WHERE users.name = alert_settings.creator
        )
        WHERE creator_user_id IS NULL
          AND creator IS NOT NULL
          AND (SELECT COUNT(*) FROM users WHERE users.name = alert_settings.creator) = 1
        """
    )
```

- [ ] **Step 4: Bind new alerts to the authenticated owner**

Remove `reminder_users` from `AlertSettingIn`. Change create SQL to write `creator_user_id` and `creator` from `user`; change update SQL so editing does not write either ownership field or `reminder_users`. Return `creator_user_id` from the settings list and keep `creator` for display.

```python
INSERT INTO alert_settings
    (info_type, contract_year, contract_month, alert_value, direction, status, creator_user_id, creator)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
```

- [ ] **Step 5: Run the focused tests and verify GREEN**

Run: `.venv/bin/python -m pytest -q tests/test_risk_alert_owner_notifications.py`

Expected: `2 passed`.

- [ ] **Step 6: Commit stable ownership**

```bash
git add backend/app/db.py backend/app/main.py tests/test_risk_alert_owner_notifications.py
git commit -m "feat: bind risk alerts to creator accounts"
```

---

### Task 2: Scope notification delivery and acknowledgement to the owner

**Files:**
- Modify: `backend/app/main.py:1641-1686`
- Modify: `tests/test_risk_alert_owner_notifications.py`
- Test: `tests/test_auth_permissions.py`

**Interfaces:**
- Consumes: `alert_settings.creator_user_id` from Task 1 and authenticated `user["id"]`.
- Produces: owner-filtered `list_alert_notifications(user)`; owner-guarded `mark_alert_history_read(history_id, user)`; owner-scoped `mark_all_alert_history_read(user)`.

- [ ] **Step 1: Add failing two-user delivery and acknowledgement tests**

Extend the focused test file with a helper that creates a second `期货组` operate user, then assert:

```python
def create_futures_user(name, username, admin):
    created = main.create_user(
        main.UserIn(name=name, username=username, department="期货组", role="用户"),
        user=admin,
    )
    with db.connect() as conn:
        return dict(db._exec(conn.cursor(), "SELECT * FROM users WHERE id = ?", (created["id"],)).fetchone())


def trigger_for_owner(owner):
    alert_id = main.create_alert_setting(alert_payload(), user=owner)["id"]
    main.simulate_alert_trigger(alert_id, current_value=11, user=owner)
    with db.connect() as conn:
        history_id = db._exec(
            conn.cursor(), "SELECT id FROM alert_history WHERE alert_id = ?", (alert_id,)
        ).fetchone()["id"]
    return alert_id, history_id


def test_notifications_are_visible_only_to_alert_owner(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    owner = admin_user()
    other = create_futures_user("其他用户", "other-user", owner)
    _, history_id = trigger_for_owner(owner)

    assert [item["id"] for item in main.list_alert_notifications(user=owner)["items"]] == [history_id]
    assert main.list_alert_notifications(user=other) == {"count": 0, "items": []}


def test_other_user_cannot_acknowledge_owner_notification(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    owner = admin_user()
    other = create_futures_user("其他用户", "other-user", owner)
    _, history_id = trigger_for_owner(owner)

    try:
        main.mark_alert_history_read(history_id, user=other)
    except main.HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("another user must not acknowledge the owner's notification")

    main.mark_alert_history_read(history_id, user=owner)
    with db.connect() as conn:
        status = db._exec(conn.cursor(), "SELECT status FROM alert_history WHERE id = ?", (history_id,)).fetchone()["status"]
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
            conn.cursor(), "SELECT id, status FROM alert_history WHERE id IN (?, ?) ORDER BY id", (first_history, second_history)
        ).fetchall()
    assert {row["id"]: row["status"] for row in rows} == {first_history: "read", second_history: "unread"}
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_risk_alert_owner_notifications.py`

Expected: the other user still sees the notification, can mark it read, or bulk acknowledgement changes both owners' records.

- [ ] **Step 3: Filter notification reads in SQL**

Replace post-query name parsing with a SQL owner predicate:

```sql
SELECT h.*, s.info_type, s.contract_year, s.contract_month, s.creator, s.creator_user_id
FROM alert_history h
JOIN alert_settings s ON s.id = h.alert_id
WHERE h.status = 'unread' AND s.creator_user_id = ?
ORDER BY h.alert_time DESC, h.id DESC
LIMIT 20
```

Pass `(user["id"],)` and return all selected rows directly.

- [ ] **Step 4: Guard single and bulk acknowledgement in SQL**

Use ownership predicates for both mutations:

```sql
UPDATE alert_history
SET status = 'read'
WHERE id = ?
  AND alert_id IN (SELECT id FROM alert_settings WHERE creator_user_id = ?)
```

```sql
UPDATE alert_history
SET status = 'read'
WHERE status = 'unread'
  AND alert_id IN (SELECT id FROM alert_settings WHERE creator_user_id = ?)
```

Keep the existing `require_edit("risk_alert", user)` permission gate. Return 404 for a single history row outside the current owner's scope.

- [ ] **Step 5: Run focused and permission regression tests**

Run: `.venv/bin/python -m pytest -q tests/test_risk_alert_owner_notifications.py tests/test_auth_permissions.py`

Expected: all tests pass, including the existing view-only 403 assertions.

- [ ] **Step 6: Commit notification isolation**

```bash
git add backend/app/main.py tests/test_risk_alert_owner_notifications.py
git commit -m "fix: isolate risk alert notifications by owner"
```

---

### Task 3: Stack the risk-alert panels and format timestamps to seconds

**Files:**
- Modify: `frontend/index.html:257-315`
- Modify: `frontend/index.html:872-893`
- Modify: `frontend/index.html:7,1307`
- Modify: `frontend/styles.css:421-428`
- Modify: `frontend/app.js:1377-1395`
- Modify: `frontend/app.js:1478-1510`
- Modify: `frontend/app.js:1526-1536`
- Modify: `frontend/app.js:1851-1864`
- Create: `tests/risk_alert_frontend.test.mjs`

**Interfaces:**
- Consumes: `creator` and `alert_time` returned by existing settings/history endpoints.
- Produces: `.risk-alert-stack`; `formatAlertTime(value: unknown): string`; no reminder-user form field; settings table displays `creator` under `设置人`.

- [ ] **Step 1: Write failing frontend structure tests**

Create `tests/risk_alert_frontend.test.mjs`:

```javascript
import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const html = fs.readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");
const css = fs.readFileSync(new URL("../frontend/styles.css", import.meta.url), "utf8");
const appJs = fs.readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");

test("risk alert management stacks rules above history without changing shared content grids", () => {
  assert.match(html, /id="riskAlertPage"[\s\S]*class="risk-alert-stack"/);
  assert.match(css, /\.risk-alert-stack\s*\{[\s\S]*grid-template-columns:\s*minmax\(0,\s*1fr\)/);
  assert.match(css, /\.content-grid\s*\{[\s\S]*minmax\(0,\s*1\.2fr\)/);
});

test("risk alert form binds recipients automatically to the creator", () => {
  assert.match(html, /<th>\u8bbe\u7f6e\u4eba<\/th>/);
  assert.doesNotMatch(html, /id="reminderUsers"/);
  assert.doesNotMatch(appJs, /reminder_users:/);
  assert.match(appJs, /item\.creator \|\| "-"/);
});

test("risk alert timestamps render only through seconds", () => {
  assert.match(appJs, /function formatAlertTime\(value\)/);
  assert.match(appJs, /\^\(\\d\{4\}-\\d\{2\}-\\d\{2\}\)\[ T\]\(\\d\{2\}:\\d\{2\}:\\d\{2\}\)/);
  assert.match(appJs, /formatAlertTime\(item\.alert_time\)/);
  assert.doesNotMatch(appJs, /<td>\$\{item\.alert_time \|\| ""\}<\/td>/);
});
```

- [ ] **Step 2: Run the frontend test and verify RED**

Run: `node --test tests/risk_alert_frontend.test.mjs`

Expected: all three tests fail against the current left/right layout, free-text recipient field, and raw timestamp output.

- [ ] **Step 3: Add risk-alert-only vertical layout**

Change the risk-alert wrapper from `class="content-grid"` to `class="risk-alert-stack"` and add:

```css
.risk-alert-stack {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 16px;
}
```

Do not change `.content-grid`.

- [ ] **Step 4: Replace recipient input and table display with creator identity**

Change the table header to `设置人`, render `${item.creator || "-"}`, remove the `reminderUsers` label/input, remove dialog population for that field, and omit `reminder_users` from the submit payload.

- [ ] **Step 5: Add and use a seconds-only timestamp formatter**

Add before `loadRiskAlert`:

```javascript
function formatAlertTime(value) {
  if (!value) return "";
  const text = String(value);
  const matched = text.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})/);
  return matched ? `${matched[1]} ${matched[2]}` : text;
}
```

Render history time with `${formatAlertTime(item.alert_time)}`. This truncates presentation precision without changing stored values or timezone semantics.

- [ ] **Step 6: Bump static resource versions**

Set both resource URLs to the same feature marker:

```html
<link rel="stylesheet" href="/static/styles.css?v=risk-alert-owner-notification-20260717" />
<script src="/static/app.js?v=risk-alert-owner-notification-20260717"></script>
```

Update the existing exact version assertion in `tests/auth_frontend.test.mjs` to the new app version.

- [ ] **Step 7: Run frontend tests and verify GREEN**

Run: `node --test tests/risk_alert_frontend.test.mjs tests/auth_frontend.test.mjs tests/info_summary_frontend.test.mjs`

Expected: all focused frontend and permission tests pass.

- [ ] **Step 8: Commit frontend behavior**

```bash
git add frontend/index.html frontend/styles.css frontend/app.js tests/risk_alert_frontend.test.mjs tests/auth_frontend.test.mjs
git commit -m "feat: refine risk alert ownership display"
```

---

### Task 4: Complete local quality gate and Staging acceptance

**Files:**
- Modify after successful deploy: `版本更新记录.md`
- Do not modify: unrelated dirty files listed by `git status --short`.

**Interfaces:**
- Consumes: Tasks 1-3 commits and project staging deployment workflow.
- Produces: local T2 evidence, deployed Staging commit, two-account real-surface acceptance, cleaned temporary data, release record, and an explicit Production boundary.

- [ ] **Step 1: Run the focused quality gate**

```bash
.venv/bin/python -m pytest -q tests/test_risk_alert_owner_notifications.py tests/test_auth_permissions.py
node --test tests/risk_alert_frontend.test.mjs tests/auth_frontend.test.mjs tests/info_summary_frontend.test.mjs
.venv/bin/python -m compileall -q backend/app
node --check frontend/app.js
git diff --check
```

Expected: all commands pass without syntax or whitespace errors.

- [ ] **Step 2: Run full regression suites**

```bash
.venv/bin/python -m pytest -q
node --test tests/*.mjs
```

Expected: new tests pass and no new regression is introduced. Any pre-existing failure must be identified with evidence rather than hidden.

- [ ] **Step 3: Inspect scope and push only the feature commits**

```bash
git status --short
git log --oneline origin/staging..HEAD
git diff --stat origin/staging..HEAD
git push origin staging
```

Expected: feature/spec/plan commits push to `origin/staging`; unrelated untracked files and `.gitignore` remain uncommitted.

- [ ] **Step 4: Verify the deployed commit and static assets in a clean in-app browser tab**

Open `https://ltm-web-staging.onrender.com/?codex=<commit>`. Verify the title and URL, no console errors/warnings, and both static assets use `risk-alert-owner-notification-20260717`.

- [ ] **Step 5: Run two-account functional acceptance on Staging**

Using two existing authorized test accounts or precisely created temporary Staging accounts:

1. Account A creates a uniquely named temporary alert and uses the existing simulate action.
2. Account A sees one corresponding notification; Account B's notification count and list do not include it.
3. Directly attempting Account B's single-read API against A's history ID returns 404 and leaves the row unread.
4. Account A marks it read successfully.
5. The page shows rules above history, the settings table heading `设置人`, no recipient input, and a timestamp matching `^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$`.
6. Delete the temporary alert through the authorized UI/API and verify its history is also removed. Remove temporary accounts if any were created.

- [ ] **Step 6: Update the Staging release record after acceptance**

Add a top entry to `版本更新记录.md` containing environment, behavior, nullable schema addition/backfill rule, commits/deploy, local and browser results, test-data cleanup, rollback point, and the statement that Production remains untouched and requires Gate B.

- [ ] **Step 7: Commit and push the release record**

```bash
git add 版本更新记录.md
git commit -m "docs: record risk alert staging acceptance"
git push origin staging
```

- [ ] **Step 8: Reconfirm the final Staging page after the documentation-only deploy**

Open `https://ltm-web-staging.onrender.com/?codex=<final-commit>` and verify the feature static asset versions remain active and the target page still loads without console errors.

- [ ] **Step 9: Stop at Gate B**

Report the tested Staging commit, database impact, acceptance evidence, cleanup state, known issues, rollback point, and Production release plan. Do not merge or push `main` and do not deploy Production without explicit user confirmation.
