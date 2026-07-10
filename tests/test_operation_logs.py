import os
import inspect
import sys
import gzip
import hashlib
import json
import asyncio
from datetime import date

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import db, main, operation_log_archive as archive


def use_temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "operation-logs.db")
    db.init_db()


def admin_user():
    with db.connect() as conn:
        row = db._exec(conn.cursor(), "SELECT * FROM users WHERE username = ?", ("admin",)).fetchone()
    return dict(row)


def test_init_db_creates_operation_log_archive_schema_and_indexes(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)

    db.init_db()

    with db.connect() as conn:
        indexes = {row["name"] for row in conn.execute("PRAGMA index_list('operation_logs')")}
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    assert {
        "idx_operation_logs_created_id",
        "idx_operation_logs_user_created_id",
        "idx_operation_logs_type_created_id",
    } <= indexes
    assert {"operation_log_archives", "operation_log_archive_users"} <= tables


def test_archived_operation_history_prevents_physical_user_deletion(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    admin = admin_user()
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(
            cur,
            """
            INSERT INTO users (name, username, department, password_hash, role)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("归档用户", "archive-user", "贸易处", db.password_hash("archive-user"), "用户"),
        )
        user_id = db.last_insert_id(conn)
        db._exec(
            cur,
            """
            INSERT INTO operation_log_archives
                (period_start, period_end, object_path, row_count, first_created_at,
                 last_created_at, sha256, compressed_bytes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2025-01-01",
                "2025-02-01",
                "staging/2025/01/operation-logs-2025-01.ndjson.gz",
                1,
                "2025-01-02 10:00:00",
                "2025-01-02 10:00:00",
                "a" * 64,
                100,
            ),
        )
        archive_id = db.last_insert_id(conn)
        db._exec(
            cur,
            "INSERT INTO operation_log_archive_users (archive_id, user_id) VALUES (?, ?)",
            (archive_id, user_id),
        )

    with pytest.raises(HTTPException) as exc:
        main.delete_user(user_id, user=admin)

    assert exc.value.status_code == 400
    assert "会话或操作历史" in exc.value.detail


def insert_log(user_id, operation_type, created_at, description="测试日志"):
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(
            cur,
            """
            INSERT INTO operation_logs
                (user_id, module_code, operation_type, description, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, "user_management", operation_type, description, created_at),
        )
        return db.last_insert_id(conn)


def test_operation_logs_use_stable_cursor_without_duplicates(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    admin = admin_user()
    for index in range(5):
        insert_log(admin["id"], "编辑用户", "2026-07-10 10:00:00", f"日志 {index}")

    first = main.list_operation_logs(limit=2, user=admin)
    second = main.list_operation_logs(limit=2, cursor=first["next_cursor"], user=admin)

    assert len(first["logs"]) == 2
    assert first["has_more"] is True
    assert first["next_cursor"]
    assert len(second["logs"]) == 2
    assert {row["id"] for row in first["logs"]}.isdisjoint(row["id"] for row in second["logs"])
    assert first["logs"][0]["id"] > first["logs"][1]["id"]
    assert "pagination" not in first


def test_operation_logs_default_to_100_and_cap_at_200(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    admin = admin_user()
    for index in range(205):
        insert_log(admin["id"], "登录", f"2026-07-10 10:{index // 60:02d}:{index % 60:02d}")

    default_page = main.list_operation_logs(user=admin)
    capped_page = main.list_operation_logs(limit=999, user=admin)

    assert len(default_page["logs"]) == 100
    assert len(capped_page["logs"]) == 200
    assert capped_page["has_more"] is True


def test_operation_logs_filter_by_user_type_and_inclusive_dates(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    admin = admin_user()
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(
            cur,
            """
            INSERT INTO users (name, username, department, password_hash, role)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("筛选用户", "filter-user", "贸易处", db.password_hash("filter-user"), "用户"),
        )
        target_id = db.last_insert_id(conn)
    insert_log(target_id, "添加用户", "2026-07-09 23:59:59", "范围外")
    expected_id = insert_log(target_id, "添加用户", "2026-07-10 23:59:59", "范围内")
    insert_log(target_id, "编辑用户", "2026-07-10 12:00:00", "类型不符")
    insert_log(admin["id"], "添加用户", "2026-07-10 12:00:00", "用户不符")
    insert_log(target_id, "添加用户", "2026-07-11 00:00:00", "结束日之后")

    result = main.list_operation_logs(
        operation_type="添加用户",
        user_name="筛选用户",
        start_date="2026-07-10",
        end_date="2026-07-10",
        user=admin,
    )

    assert [row["id"] for row in result["logs"]] == [expected_id]


def test_operation_logs_reject_invalid_cursor(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)

    with pytest.raises(HTTPException) as exc:
        main.list_operation_logs(cursor="not-a-valid-cursor", user=admin_user())

    assert exc.value.status_code == 400
    assert "游标" in exc.value.detail


def test_operation_log_route_avoids_count_and_offset():
    source = inspect.getsource(main.list_operation_logs).upper()

    assert "COUNT(*)" not in source
    assert "OFFSET" not in source


def test_automatic_batch_calculation_is_not_logged_but_manual_is(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    admin = admin_user()

    with db.connect() as conn:
        before = conn.execute("SELECT COUNT(*) AS c FROM operation_logs").fetchone()["c"]

    main.calculate_info_summary_all(
        main.InfoCalculateAllIn(items=[], audit_source="automatic"),
        user=admin,
    )
    with db.connect() as conn:
        after_automatic = conn.execute("SELECT COUNT(*) AS c FROM operation_logs").fetchone()["c"]

    main.calculate_info_summary_all(
        main.InfoCalculateAllIn(items=[], audit_source="manual"),
        user=admin,
    )
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT operation_type FROM operation_logs ORDER BY id"
        ).fetchall()

    assert after_automatic == before
    assert len(rows) == before + 1
    assert rows[-1]["operation_type"] == "批量计算指标"


def test_archive_selection_uses_time_and_soft_row_cap_without_current_month(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    admin = admin_user()
    insert_log(admin["id"], "登录", "2025-06-30 12:00:00")
    insert_log(admin["id"], "登录", "2026-05-01 12:00:00")
    insert_log(admin["id"], "登录", "2026-06-01 12:00:00")
    insert_log(admin["id"], "登录", "2026-07-01 12:00:00")
    with db.connect() as conn:
        periods = archive.select_archive_periods(
            conn,
            today=date(2026, 7, 10),
            retention_months=12,
            max_online_rows=2,
        )

    assert [(item.period_start.isoformat(), item.period_end.isoformat()) for item in periods] == [
        ("2025-06-01", "2025-07-01"),
        ("2026-05-01", "2026-06-01"),
    ]
    assert all(item.period_start.month != 7 or item.period_start.year != 2026 for item in periods)


def test_build_archive_payload_is_deterministic_gzip_ndjson(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    admin = admin_user()
    first_id = insert_log(admin["id"], "登录", "2025-06-01 09:00:00", "第一条")
    second_id = insert_log(admin["id"], "退出", "2025-06-30 18:00:00", "第二条")
    period = archive.ArchivePeriod(date(2025, 6, 1), date(2025, 7, 1), 2)

    with db.connect() as conn:
        payload = archive.build_archive_payload(conn, period)
        repeated = archive.build_archive_payload(conn, period)

    rows = [json.loads(line) for line in gzip.decompress(payload.content).decode("utf-8").splitlines()]
    assert [row["id"] for row in rows] == [first_id, second_id]
    assert rows[0]["user_name"] == "admin"
    assert payload.row_count == 2
    assert payload.user_ids == (admin["id"],)
    assert payload.sha256 == hashlib.sha256(payload.content).hexdigest()
    assert payload.content == repeated.content


class FakeArchiveStorage:
    def __init__(self, *, verify_ok=True):
        self.objects = {}
        self.verify_ok = verify_ok

    def upload_immutable(self, path, content):
        if path in self.objects:
            raise archive.ArchiveStorageError("对象已存在")
        self.objects[path] = content

    def verify(self, path, expected_sha256, expected_bytes):
        if not self.verify_ok:
            return False
        content = self.objects[path]
        return len(content) == expected_bytes and hashlib.sha256(content).hexdigest() == expected_sha256

    def download(self, path):
        return self.objects[path]


def test_archive_dry_run_has_zero_writes(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    admin = admin_user()
    old_id = insert_log(admin["id"], "登录", "2025-06-01 09:00:00")
    storage = FakeArchiveStorage()

    result = archive.archive_due_logs(storage, apply=False, today=date(2026, 7, 10))

    assert result["candidate_rows"] == 1
    assert result["archived_rows"] == 0
    assert storage.objects == {}
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM operation_log_archives").fetchone()["c"] == 0
        assert conn.execute("SELECT id FROM operation_logs WHERE id = ?", (old_id,)).fetchone()


def test_archive_verification_failure_keeps_online_logs(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    admin = admin_user()
    old_id = insert_log(admin["id"], "登录", "2025-06-01 09:00:00")
    storage = FakeArchiveStorage(verify_ok=False)

    with pytest.raises(archive.ArchiveStorageError):
        archive.archive_due_logs(storage, apply=True, today=date(2026, 7, 10))

    with db.connect() as conn:
        assert conn.execute("SELECT id FROM operation_logs WHERE id = ?", (old_id,)).fetchone()
        assert conn.execute("SELECT COUNT(*) AS c FROM operation_log_archives").fetchone()["c"] == 0


def test_archive_and_restore_round_trip_preserves_original_ids(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    admin = admin_user()
    old_id = insert_log(admin["id"], "登录", "2025-06-01 09:00:00", "待归档")
    storage = FakeArchiveStorage()

    archived = archive.archive_due_logs(storage, apply=True, today=date(2026, 7, 10))
    archive_id = archived["archives"][0]["id"]
    with db.connect() as conn:
        assert conn.execute("SELECT id FROM operation_logs WHERE id = ?", (old_id,)).fetchone() is None
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM operation_log_archive_users WHERE archive_id = ? AND user_id = ?",
            (archive_id, admin["id"]),
        ).fetchone()["c"] == 1

    restored = archive.restore_archive(archive_id, storage, apply=True)

    assert restored["restored_rows"] == 1
    with db.connect() as conn:
        row = conn.execute("SELECT id, description FROM operation_logs WHERE id = ?", (old_id,)).fetchone()
        metadata = conn.execute(
            "SELECT restored_at FROM operation_log_archives WHERE id = ?", (archive_id,)
        ).fetchone()
    assert dict(row) == {"id": old_id, "description": "待归档"}
    assert metadata["restored_at"]


def test_restore_id_conflict_rolls_back_all_rows(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    admin = admin_user()
    old_id = insert_log(admin["id"], "登录", "2025-06-01 09:00:00", "待归档")
    storage = FakeArchiveStorage()
    archived = archive.archive_due_logs(storage, apply=True, today=date(2026, 7, 10))
    archive_id = archived["archives"][0]["id"]
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO operation_logs (id, operation_type, description, created_at) VALUES (?, ?, ?, ?)",
            (old_id, "冲突", "冲突行", "2026-07-10 10:00:00"),
        )

    with pytest.raises(archive.ArchiveError):
        archive.restore_archive(archive_id, storage, apply=True)

    with db.connect() as conn:
        rows = conn.execute("SELECT description FROM operation_logs WHERE id = ?", (old_id,)).fetchall()
        restored_at = conn.execute(
            "SELECT restored_at FROM operation_log_archives WHERE id = ?", (archive_id,)
        ).fetchone()["restored_at"]
    assert [row["description"] for row in rows] == ["冲突行"]
    assert restored_at is None


def test_archive_metadata_list_is_admin_only_and_excludes_object_path(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    admin = admin_user()
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(
            cur,
            """
            INSERT INTO operation_log_archives
                (period_start, period_end, object_path, row_count, first_created_at,
                 last_created_at, sha256, compressed_bytes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("2025-06-01", "2025-07-01", "private/path.gz", 2, "a", "b", "c" * 64, 99),
        )

    result = main.list_operation_log_archives(user=admin)

    assert len(result["archives"]) == 1
    assert result["archives"][0]["period_start"] == "2025-06-01"
    assert "object_path" not in result["archives"][0]
    with pytest.raises(HTTPException) as exc:
        main.list_operation_log_archives(user={"id": 999, "role": "用户"})
    assert exc.value.status_code == 403


def test_archive_download_is_admin_only_and_streamed_on_demand(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    admin = admin_user()
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(
            cur,
            """
            INSERT INTO operation_log_archives
                (period_start, period_end, object_path, row_count, first_created_at,
                 last_created_at, sha256, compressed_bytes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("2025-06-01", "2025-07-01", "private/path.gz", 2, "a", "b", "c" * 64, 99),
        )
        archive_id = db.last_insert_id(conn)

    calls = []

    class FakeStreamingStorage:
        def iter_download(self, path):
            calls.append(path)
            yield b"one"
            yield b"two"

    monkeypatch.setattr(main, "get_operation_log_archive_storage", lambda: FakeStreamingStorage())
    response = main.download_operation_log_archive(archive_id, user=admin)

    async def collect_body():
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        return b"".join(chunks)

    assert calls == []
    assert asyncio.run(collect_body()) == b"onetwo"
    assert calls == ["private/path.gz"]
    assert response.media_type == "application/gzip"
    with pytest.raises(HTTPException) as exc:
        main.download_operation_log_archive(archive_id, user={"id": 999, "role": "用户"})
    assert exc.value.status_code == 403
