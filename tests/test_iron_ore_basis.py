import os
import sys

import asyncio
from fastapi import HTTPException
import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import db


def insert_basis_result(cur, **overrides):
    row = {
        "business_key": "2026-07-10|京唐港|昆巴粉|I2312 / F-DCE I004-2021|2026-典型值",
        "business_date": "2026-07-10",
        "business_week": 28,
        "week_label": "2026 W28",
        "business_year": 2026,
        "port": "京唐港",
        "product": "昆巴粉",
        "wet_spot_price": 746,
        "quality_adjustment": 28,
        "brand_adjustment": 0,
        "standardized_spot_price": 739.4897119341564,
        "futures_series": "I0",
        "futures_close": 751.5,
        "basis": -12.01028806584361,
        "data_status": "有效",
        "rule_version": "I2312 / F-DCE I004-2021",
        "parameter_version": "2026-典型值",
        "source_workbook_name": "basis.xlsx",
        "source_workbook_sha256": "a" * 64,
    }
    row.update(overrides)
    columns = list(row)
    db._exec(
        cur,
        f"INSERT INTO iron_ore_basis_results ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        tuple(row[column] for column in columns),
    )


def use_temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "iron_ore_basis.db")
    db.init_db()


def test_iron_ore_basis_schema_is_idempotent_and_indexed(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    db.init_db()

    with db.connect() as conn:
        cur = conn.cursor()
        tables = {
            row["name"]
            for row in cur.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        indexes = {
            row["name"]
            for row in cur.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        detail_foreign_keys = cur.execute(
            "PRAGMA foreign_key_list(iron_ore_basis_details)"
        ).fetchall()
        sync_run_columns = {
            row["name"]
            for row in cur.execute("PRAGMA table_info(iron_ore_basis_sync_runs)").fetchall()
        }

    assert "iron_ore_basis_results" in tables
    assert "iron_ore_basis_details" in tables
    assert "iron_ore_basis_sync_runs" in tables
    assert "iron_ore_basis_source_points" in tables
    assert "idx_iron_ore_basis_results_query" in indexes
    assert "idx_iron_ore_basis_results_optimal" in indexes
    assert "idx_iron_ore_basis_details_result" in indexes
    assert "idx_iron_ore_basis_sync_runs_window" in indexes
    assert "idx_iron_ore_basis_source_points_date" in indexes
    assert {"attempt_count", "error_stage", "http_status", "last_attempt_at"} <= sync_run_columns
    assert any(
        row["table"] == "iron_ore_basis_results" and row["from"] == "result_id"
        for row in detail_foreign_keys
    )


def test_iron_ore_basis_schema_adds_retry_columns_to_existing_sync_table(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "existing-basis.db")
    with db.connect() as conn:
        conn.execute(
            """CREATE TABLE iron_ore_basis_sync_runs (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   slot_key TEXT NOT NULL UNIQUE,
                   trigger_type TEXT NOT NULL,
                   target_start_date TEXT NOT NULL,
                   target_end_date TEXT NOT NULL,
                   status TEXT NOT NULL,
                   source_points_seen INTEGER NOT NULL DEFAULT 0,
                   source_points_inserted INTEGER NOT NULL DEFAULT 0,
                   source_differences INTEGER NOT NULL DEFAULT 0,
                   combinations_written INTEGER NOT NULL DEFAULT 0,
                   combinations_skipped INTEGER NOT NULL DEFAULT 0,
                   error_code TEXT,
                   error_summary TEXT,
                   started_at TEXT DEFAULT CURRENT_TIMESTAMP,
                   finished_at TEXT,
                   created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                   updated_at TEXT DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        conn.execute(
            """INSERT INTO iron_ore_basis_sync_runs
               (slot_key, trigger_type, target_start_date, target_end_date, status)
               VALUES ('scheduled:old', 'scheduled_0930', '2026-07-14', '2026-07-14', 'failed')"""
        )
    db.init_db()

    with db.connect() as conn:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(iron_ore_basis_sync_runs)").fetchall()
        }
        run = conn.execute(
            """SELECT attempt_count, error_stage, http_status, last_attempt_at
               FROM iron_ore_basis_sync_runs WHERE slot_key = 'scheduled:old'"""
        ).fetchone()

    assert {"attempt_count", "error_stage", "http_status", "last_attempt_at"} <= columns
    assert run["attempt_count"] == 1
    assert run["last_attempt_at"]


def test_postgres_retry_timestamp_backfill_keeps_coalesce_types_compatible(monkeypatch):
    statements = []

    class FakeCursor:
        def execute(self, sql):
            statements.append(sql)

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr(db, "_is_pg", lambda: True)

    db.migrate_iron_ore_basis_schema(FakeConnection())

    migration_sql = "\n".join(statements)
    assert "COALESCE(last_attempt_at, started_at, CURRENT_TIMESTAMP::text)" in migration_sql


def test_database_backup_includes_all_basis_tables():
    from scripts.backup_database import CORE_TABLES

    assert "iron_ore_basis_results" in CORE_TABLES
    assert "iron_ore_basis_details" in CORE_TABLES
    assert "iron_ore_basis_sync_runs" in CORE_TABLES
    assert "iron_ore_basis_source_points" in CORE_TABLES


def test_basis_source_points_have_stable_unique_business_key(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(
            cur,
            """INSERT INTO iron_ore_basis_sync_runs
               (slot_key, trigger_type, target_start_date, target_end_date, status)
               VALUES (?, ?, ?, ?, ?)""",
            ("manual:2026-07-13", "manual", "2026-07-13", "2026-07-13", "running"),
        )
        run_id = cur.lastrowid
        db._exec(
            cur,
            """INSERT INTO iron_ore_basis_source_points
               (source_name, indicator_key, business_date, canonical_value,
                canonical_payload_sha256, first_run_id, last_observed_value,
                last_observed_payload_sha256)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("EBC", "ID-PB", "2026-07-13", 700, "a" * 64, run_id, 700, "a" * 64),
        )

        with pytest.raises(Exception, match="UNIQUE"):
            db._exec(
                cur,
                """INSERT INTO iron_ore_basis_source_points
                   (source_name, indicator_key, business_date, canonical_value,
                    canonical_payload_sha256, first_run_id, last_observed_value,
                    last_observed_payload_sha256)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("EBC", "ID-PB", "2026-07-13", 701, "b" * 64, run_id, 701, "b" * 64),
            )


def test_management_filters_rows_and_pagination_default_to_all(tmp_path, monkeypatch):
    from app.iron_ore_basis import display_filters, management_filters, management_rows

    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        cur = conn.cursor()
        insert_basis_result(cur)
        insert_basis_result(
            cur,
            business_key="2025-07-10|日照港|PB粉|I2312 / F-DCE I004-2021|2025-典型值",
            business_date="2025-07-10",
            business_year=2025,
            port="日照港",
            product="PB粉",
            parameter_version="2025-典型值",
            basis=55,
        )
    admin = {"id": 1, "name": "admin", "role": "管理员"}

    filters = asyncio.run(management_filters(user=admin))
    display = asyncio.run(display_filters(user=admin))
    page = asyncio.run(management_rows(years="", products="", ports="", limit=1, offset=1, user=admin))

    assert filters["years"] == [2025, 2026]
    assert filters["latest_data_date"] == "2026-07-10"
    assert display["latest_data_date"] == "2026-07-10"
    assert filters["ports"][0] == "日照港"
    assert "昆巴粉" in filters["products"]
    assert page["pagination"] == {"total": 2, "limit": 1, "offset": 1, "has_more": False}
    assert page["data"][0]["business_date"] == "2025-07-10"


def test_basis_filters_report_no_latest_date_when_table_is_empty(tmp_path, monkeypatch):
    from app.iron_ore_basis import display_filters, management_filters

    use_temp_db(tmp_path, monkeypatch)
    admin = {"id": 1, "name": "admin", "role": "管理员"}

    assert asyncio.run(management_filters(user=admin))["latest_data_date"] is None
    assert asyncio.run(display_filters(user=admin))["latest_data_date"] is None


def test_management_requires_data_view_permission(tmp_path, monkeypatch):
    from app.iron_ore_basis import management_rows

    use_temp_db(tmp_path, monkeypatch)
    guest = db.ensure_guest_user()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(management_rows(years="", products="", ports="", user=guest))

    assert exc.value.status_code == 403


def test_display_chart_queries_only_active_port_and_groups_daily_years(tmp_path, monkeypatch):
    from app.iron_ore_basis import display_chart

    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        cur = conn.cursor()
        insert_basis_result(cur)
        insert_basis_result(
            cur,
            business_key="2026-07-10|日照港|昆巴粉|I2312 / F-DCE I004-2021|2026-典型值",
            port="日照港",
            basis=20,
        )
        insert_basis_result(
            cur,
            business_key="2025-07-10|日照港|昆巴粉|I2312 / F-DCE I004-2021|2025-典型值",
            business_date="2025-07-10",
            business_year=2025,
            port="日照港",
            parameter_version="2025-典型值",
            basis=30,
        )
    guest = db.ensure_guest_user()

    result = asyncio.run(display_chart(port="日照港", years="", products="", user=guest))

    assert result["port"] == "日照港"
    assert result["series"] == {
        "昆巴粉": {
            "2025": [{"date": "2025-07-10", "value": 30.0}],
            "2026": [{"date": "2026-07-10", "value": 20.0}],
        }
    }


def test_optimal_warrant_uses_latest_current_year_and_deterministic_tie_break(tmp_path, monkeypatch):
    from app.iron_ore_basis import _optimal_warrant_for_year

    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        cur = conn.cursor()
        insert_basis_result(cur)
        insert_basis_result(
            cur,
            business_key="2026-07-09|京唐港|昆巴粉|I2312 / F-DCE I004-2021|2026-典型值",
            business_date="2026-07-09",
            basis=-100,
        )
        insert_basis_result(
            cur,
            business_key="2026-07-10|曹妃甸港|超特粉|I2312 / F-DCE I004-2021|2026-典型值",
            port="曹妃甸港",
            product="超特粉",
            basis=-12.01028806584361,
            standardized_spot_price=750,
            wet_spot_price=740,
        )
        insert_basis_result(
            cur,
            business_key="2025-12-31|日照港|PB粉|I2312 / F-DCE I004-2021|2025-典型值",
            business_date="2025-12-31",
            business_year=2025,
            port="日照港",
            product="PB粉",
            parameter_version="2025-典型值",
            basis=-999,
        )
    admin = {"id": 1, "name": "admin", "role": "管理员"}

    result = _optimal_warrant_for_year(2026, user=admin)

    assert result["data_as_of"] == "2026-07-10"
    assert result["product"] == "昆巴粉"
    assert result["port"] == "京唐港"
    assert result["basis"] == pytest.approx(-12.01028806584361)
