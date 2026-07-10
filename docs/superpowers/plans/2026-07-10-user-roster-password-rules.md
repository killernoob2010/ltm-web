# User Roster Password Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the company-leader department, centralize roster-specific initial-password rules, provision the approved 26-person roster with Long Yunfei's order-finance exception, and configure Wang Jingze's existing administrator account on Staging.

**Architecture:** Keep module authorization in `permissions.py`, move temporary-password selection into a focused `user_policy.py`, and make preview/create/default-reset consume the same policy result. Extend the existing dry-run-first roster script to accept the actual three-column workbook, attach controlled permission exceptions, and optionally configure Wang Jingze through a sanitized administrator-only password endpoint.

**Tech Stack:** FastAPI, Pydantic, SQLite/PostgreSQL compatibility layer, pypinyin, vanilla JavaScript/HTML, openpyxl roster reader, pytest, Node test runner, Render Staging, Supabase PostgreSQL.

## Global Constraints

- Work only on `staging`; do not merge or push `main`, deploy Production, or create Production accounts without a separate user confirmation.
- Preserve the existing dirty files and stage only files named in this plan.
- Add `公司领导` as a real department; do not map it to `管理部门`.
- Login accounts are lowercase full-name pinyin unless a non-Chinese or collision preview requires manual correction.
- Cao Xiang permanently uses `username`; other leaders use `username + 12345`; Trade and Futures ordinary users use `username`; Finance and Treasury ordinary users use `username + 123`.
- Wang Jingze remains an existing `管理部门 / 管理员`, receives the user-specified password through a one-time controlled operation, and has `password_change_recommended = 0`.
- Long Yunfei receives `operate` on both order-finance modules and no sensitive permission.
- Never write the operational plaintext password to application logs, operation logs, release records, Git commits, test fixtures, or reusable password constants; supply it only at execution time through a hidden prompt or non-printed environment value.
- Back up Staging authentication tables before account creation. Rollback scope is users, permissions, sessions, and logs only.

---

### Task 1: Add the Department and Central Password Policy

**Files:**
- Create: `backend/app/user_policy.py`
- Modify: `backend/app/permissions.py:35-67`
- Test: `tests/test_auth_permissions.py`

**Interfaces:**
- Consumes: `name`, resolved lowercase `username`, `department`, and `role` strings.
- Produces: `temporary_password_policy(name: str, username: str, department: str, role: str) -> dict[str, str]` with `temporary_password` and `password_rule` keys.

- [ ] **Step 1: Write failing password-policy and company-leader tests**

```python
from app.user_policy import temporary_password_policy


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
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_auth_permissions.py -q`

Expected: collection fails because `app.user_policy` does not exist or assertions fail because `公司领导` is absent.

- [ ] **Step 3: Implement the minimal focused policy module**

```python
# backend/app/user_policy.py
def temporary_password_policy(name: str, username: str, department: str, role: str) -> dict[str, str]:
    if name.strip() == "曹骧":
        return {"temporary_password": username, "password_rule": "cao_xiang_exception"}
    if role == "领导":
        return {"temporary_password": f"{username}12345", "password_rule": "leader_12345"}
    if role == "用户" and department in {"贸易处", "期货组"}:
        return {"temporary_password": username, "password_rule": "trade_or_futures_plain"}
    if role == "用户" and department in {"财企处", "资金处"}:
        return {"temporary_password": f"{username}123", "password_rule": "finance_or_treasury_123"}
    return {"temporary_password": f"{username}123", "password_rule": "compatibility_fallback_123"}
```

Change the department tuple exactly to:

```python
DEPARTMENTS = ("贸易处", "期货组", "财企处", "资金处", "管理部门", "公司领导")
```

Do not add `公司领导` to `DEPARTMENT_MODULES`; leaders already bypass that matrix through the existing leader branch.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_auth_permissions.py -q`

Expected: all tests in the file pass.

- [ ] **Step 5: Commit the policy unit**

```bash
git add backend/app/user_policy.py backend/app/permissions.py tests/test_auth_permissions.py
git commit -m "feat: add roster password policy"
```

---

### Task 2: Use One Policy for Preview, Create, Reset, and Self-Change Validation

**Files:**
- Modify: `backend/app/main.py:2680-2980`
- Test: `tests/test_auth_permissions.py`

**Interfaces:**
- Consumes: `temporary_password_policy(...)` from Task 1.
- Produces: preview responses containing `temporary_password` and `password_rule`; created/reset passwords matching preview; `POST /api/users/{id}/set-password` for a sanitized controlled administrator operation.

- [ ] **Step 1: Write failing integration tests for shared policy behavior**

```python
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
```

Add `import pytest` at the top of the test file for the exception assertion.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_auth_permissions.py -q`

Expected: preview still returns `username + 123`, lacks `password_rule`, or self-change accepts the new default.

- [ ] **Step 3: Replace the three duplicated password constructions**

Import the policy:

```python
from .user_policy import temporary_password_policy
```

In preview, create, reset, and self-change validation, compute:

```python
password_policy = temporary_password_policy(name, username, department, role)
temporary_password = password_policy["temporary_password"]
```

Return this additional preview field:

```python
"password_rule": password_policy["password_rule"],
```

For self-change, compute the logged-in user's actual generated default and reject it:

```python
default_password = temporary_password_policy(
    user["name"], user["username"], user["department"], user["role"]
)["temporary_password"]
if payload.new_password in {payload.current_password, default_password}:
    raise HTTPException(status_code=400, detail="新密码不能与当前密码或默认密码相同")
```

- [ ] **Step 4: Add the controlled administrator password-set endpoint**

Add the request type:

```python
class AdminSetPasswordIn(BaseModel):
    new_password: str = Field(min_length=1)
    password_change_recommended: bool = True
```

Add the endpoint next to default reset:

```python
@app.post("/api/users/{user_id}/set-password")
def set_user_password(user_id: int, payload: AdminSetPasswordIn, user=Depends(current_user)):
    _require_admin(user)
    with db.connect() as conn:
        cur = conn.cursor()
        target = db._exec(cur, "SELECT id, name, is_guest, cannot_change_password FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="用户不存在")
        if target["is_guest"] or target["cannot_change_password"]:
            raise HTTPException(status_code=400, detail="系统访客不允许设置密码")
        db._exec(
            cur,
            "UPDATE users SET password_hash = ?, password_change_recommended = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (db.password_hash(payload.new_password), int(payload.password_change_recommended), user_id),
        )
        db._exec(cur, "UPDATE user_sessions SET status = '已注销' WHERE user_id = ?", (user_id,))
    db.log_operation(user["id"], "user_management", "管理员设置密码", f"管理员设置用户密码: {target['name']}", "users", user_id)
    return {"ok": True}
```

Add this endpoint test using an unrelated fixture value rather than the operational password:

```python
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
```

- [ ] **Step 5: Run focused API tests**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_auth_permissions.py -q`

Expected: all tests pass; no operation log contains the supplied password.

- [ ] **Step 6: Commit the shared API policy**

```bash
git add backend/app/main.py tests/test_auth_permissions.py
git commit -m "feat: unify initial password handling"
```

---

### Task 3: Expose the Company-Leader Department in User Management

**Files:**
- Modify: `frontend/index.html:1024-1030`
- Test: `tests/auth_frontend.test.mjs`

**Interfaces:**
- Consumes: backend acceptance of `公司领导` from Task 1.
- Produces: an administrator-selectable `公司领导` department in create/edit dialogs.

- [ ] **Step 1: Extend the frontend assertion before changing HTML**

```javascript
for (const department of ["贸易处", "期货组", "财企处", "资金处", "管理部门", "公司领导"]) {
  assert.match(html, new RegExp(`<option value="${department}">${department}</option>`));
}
```

- [ ] **Step 2: Run the frontend test and verify it fails**

Run: `node --test tests/auth_frontend.test.mjs`

Expected: FAIL because the company-leader option is missing.

- [ ] **Step 3: Add the option without changing layout or styling**

```html
<option value="公司领导">公司领导</option>
```

Place it after `管理部门`; do not alter unrelated HTML.

- [ ] **Step 4: Run the frontend test and verify it passes**

Run: `node --test tests/auth_frontend.test.mjs`

Expected: both authentication frontend tests pass.

- [ ] **Step 5: Commit the department UI**

```bash
git add frontend/index.html tests/auth_frontend.test.mjs
git commit -m "feat: add company leader department option"
```

---

### Task 4: Make the Real Three-Column Roster Dry-Run Cleanly

**Files:**
- Modify: `scripts/provision_users.py`
- Modify: `tests/test_user_provisioning.py`

**Interfaces:**
- Consumes: `/api/users/preview`, `/api/users`, `/api/users/{id}/set-password`, and the workbook headers `姓名、部门、用户类型` with optional `登录账号（可空）`.
- Produces: normalized roster rows, Long Yunfei permission overrides, password-rule dry-run output, and an opt-in Wang Jingze configuration step.

- [ ] **Step 1: Write failing tests for the actual workbook shape and exceptions**

```python
def test_three_column_roster_normalizes_whitespace_and_adds_long_yunfei_override(tmp_path):
    path = tmp_path / "users.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.append(["姓名", "部门", "用户类型"])
    sheet.append(["龙云飞", "贸易处", "用户 "])
    sheet.append(["张胜根", "公司领导", "领导"])
    book.save(path)

    rows = provision_users.load_roster(path)
    assert rows == [
        {"name": "龙云飞", "department": "贸易处", "role": "用户", "username": ""},
        {"name": "张胜根", "department": "公司领导", "role": "领导", "username": ""},
    ]
    assert provision_users.permission_overrides(rows[0]) == [
        {"module_code": "order_finance_progress", "level": "operate"},
        {"module_code": "order_finance_capital", "level": "operate"},
    ]
    assert provision_users.permission_overrides(rows[1]) == []
```

Add this preflight preservation test:

```python
def test_preflight_preserves_password_rule_and_permissions_for_apply():
    row = {"name": "龙云飞", "department": "贸易处", "role": "用户", "username": ""}
    result = provision_users.preflight_roster(
        [row],
        lambda payload: {
            "username": "longyunfei",
            "temporary_password": "longyunfei",
            "password_rule": "trade_or_futures_plain",
            "username_available": True,
            "final_permissions": {
                "order_finance_progress": "operate",
                "order_finance_capital": "operate",
            },
        },
    )
    assert result[0]["password_rule"] == "trade_or_futures_plain"
    assert result[0]["permissions"] == [
        {"module_code": "order_finance_progress", "level": "operate"},
        {"module_code": "order_finance_capital", "level": "operate"},
    ]
```

- [ ] **Step 2: Run provisioning tests and verify they fail**

Run: `PYTHONPATH=. .venv/bin/pytest tests/test_user_provisioning.py -q`

Expected: FAIL because the login-account column is incorrectly required and no exception function exists.

- [ ] **Step 3: Split required and optional headers**

```python
REQUIRED_HEADERS = {"姓名": "name", "部门": "department", "用户类型": "role"}
OPTIONAL_HEADERS = {"登录账号（可空）": "username"}
```

Build indices for required columns, set `username` to an empty string when the optional column is absent, and continue stripping every string value.

- [ ] **Step 4: Add the controlled permission override function**

```python
def permission_overrides(row: dict[str, str]) -> list[dict[str, str]]:
    if row["name"] == "龙云飞":
        return [
            {"module_code": "order_finance_progress", "level": "operate"},
            {"module_code": "order_finance_capital", "level": "operate"},
        ]
    return []
```

Call preview with these overrides, include `password_rule` and `permissions` in each preflight result, and pass `preview["permissions"]` rather than `[]` to `/api/users` during apply.

- [ ] **Step 5: Add an opt-in existing-admin configuration path**

Extend `ApiClient.request` to accept `method="POST"` and `payload=None`, add `--configure-wangjingze`, and on `--apply --configure-wangjingze`:

```python
password = os.getenv("LTM_WANGJINGZE_PASSWORD") or getpass.getpass("王景泽新密码: ")
users = client.request("/api/users", method="GET")["users"]
target = next((item for item in users if item["name"] == "王景泽"), None)
if not target:
    raise ValueError("未找到现有用户王景泽")
if target["department"] != "管理部门" or target["role"] != "管理员":
    raise ValueError("王景泽必须先是管理部门管理员")
client.request(
    f"/api/users/{target['id']}/set-password",
    {"new_password": password, "password_change_recommended": False},
)
```

Do not print `password`, request payloads, or the endpoint response body.

- [ ] **Step 6: Run provisioning tests and a local dry-run**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest tests/test_user_provisioning.py -q
PYTHONPATH=. .venv/bin/python scripts/provision_users.py --help
```

Expected: all provisioning tests pass and help documents the opt-in flag.

- [ ] **Step 7: Commit the roster workflow**

```bash
git add scripts/provision_users.py tests/test_user_provisioning.py
git commit -m "feat: support approved personnel roster"
```

---

### Task 5: Verify Locally and Release to Staging

**Files:**
- Modify after deployment: `版本更新记录.md`

**Interfaces:**
- Consumes: all implementation tasks and the approved workbook `/Users/wangjingze/建龙/人员名单.xlsx`.
- Produces: deployed Staging code, a pre-account backup, dry-run evidence, 26 created Staging users, Long Yunfei's exception, and Wang Jingze's verified administrator account.

- [ ] **Step 1: Run focused and full local verification**

Run:

```bash
PYTHONPATH=. .venv/bin/pytest tests/test_auth_permissions.py tests/test_user_provisioning.py -q
PYTHONPATH=. .venv/bin/pytest -q
node --test tests/*.test.mjs
.venv/bin/python -m compileall -q backend scripts
node --check frontend/app.js
git diff --check
```

Expected: all new focused tests pass; Node tests pass; the only allowed full-suite failure is the already recorded unrelated realtime-quote cache mock count assertion.

- [ ] **Step 2: Push only the planned Staging commits**

Run:

```bash
git status --short
git push origin staging
```

Expected: user-owned dirty files remain unstaged and `origin/staging` reaches the implementation commit.

- [ ] **Step 3: Back up Staging authentication state before creating accounts**

Execute this DDL through the Staging Supabase migration tool:

```sql
CREATE SCHEMA IF NOT EXISTS codex_backups;
CREATE TABLE codex_backups.users_before_roster_20260710 AS TABLE public.users;
CREATE TABLE codex_backups.module_permissions_before_roster_20260710 AS TABLE public.module_permissions;
CREATE TABLE codex_backups.user_sessions_before_roster_20260710 AS TABLE public.user_sessions;
CREATE TABLE codex_backups.operation_logs_before_roster_20260710 AS TABLE public.operation_logs;
```

Record only table counts and backup table names; never record connection strings or password hashes in Git.

- [ ] **Step 4: Run the real roster dry-run against Staging**

Run:

```bash
PYTHONPATH=. .venv/bin/python scripts/provision_users.py \
  --base-url https://ltm-web-staging.onrender.com \
  --file /Users/wangjingze/建龙/人员名单.xlsx
```

Expected: 26 users, zero illegal departments, zero username conflicts, three `公司领导`, Cao Xiang rule `cao_xiang_exception`, and Long Yunfei's two `operate` overrides.

- [ ] **Step 5: Apply the roster and configure Wang Jingze on Staging**

Run:

```bash
PYTHONPATH=. .venv/bin/python scripts/provision_users.py \
  --base-url https://ltm-web-staging.onrender.com \
  --file /Users/wangjingze/建龙/人员名单.xlsx \
  --apply \
  --configure-wangjingze
```

Enter administrator credentials and the user-specified Wang Jingze password only through hidden prompts or process environment values that are not printed.

Expected: 26 new users created transactionally one at a time after whole-batch preflight; Wang Jingze remains `管理部门 / 管理员` with `password_change_recommended = 0`.

- [ ] **Step 6: Perform backend and browser acceptance**

Verify representative accounts:

```text
company leader: all 9 active business modules at view only, no backend
Cao Xiang: login succeeds with his exception password
Trade/Futures ordinary: generated plain-pinyin password succeeds
Finance/Treasury ordinary: generated +123 password succeeds
Long Yunfei: both order-finance modules at operate; sensitive import/export/delete/manage returns 403
Wang Jingze: administrator login succeeds and no password-change reminder appears
```

In the browser, inspect the company-leader department, Long Yunfei menu/buttons, Wang Jingze header/reminder state, and console logs.

- [ ] **Step 7: Update the release record after Staging verification**

Add a top entry to `版本更新记录.md` containing commits, backup counts, 26-account dry-run/apply results, representative browser/API checks, rollback scope, and the statement that Production was not changed. Do not include any plaintext password.

- [ ] **Step 8: Commit and push the verified release record**

```bash
git add 版本更新记录.md
git commit -m "docs: record roster staging rollout"
git push origin staging
```

Expected: release record is on `origin/staging`; `main` and Production remain untouched.

---

### Task 6: Handoff for Production Confirmation

**Files:**
- No code changes.

**Interfaces:**
- Consumes: verified Staging results and backup/rollback evidence from Task 5.
- Produces: a concise user decision point; no Production mutation.

- [ ] **Step 1: Report the exact Staging outcome**

Report created-user count, conflicts, department distribution, permission exceptions, Wang Jingze state, test results, Staging commit, and rollback backup identifier.

- [ ] **Step 2: Stop before Production**

Ask for explicit confirmation before merging/pushing `main`, deploying Production, taking a Production backup, or creating real Production accounts. Do not treat the approved roster or Staging success as Production authorization.
