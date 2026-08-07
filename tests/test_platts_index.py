import os
import sys
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import db, permissions
from app.platts_index import (
    SERIES,
    _summary,
    calculate_derived,
    calculate_mtd,
    confirm_platts_import,
    process_platts_import,
)
from app.platts_ocr import (
    AliyunRecognizeTableOcrProvider,
    MockTableOCRProvider,
    OCRProviderError,
    parse_table_payload,
)


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\x0dIHDR"
    b"\x00\x00\x01\x00\x00\x00\x01\x00\x08\x06\x00\x00\x00"
    b"\x00\x00\x00\x00"
)


def use_temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "platts.db")
    db.init_db()


def sample_payload():
    headers = [
        (0, "日期"),
        (1, "Platts LP"),
        (2, "Platts 61％"),
        (3, "Mysteel"),
        (4, "Platts 58%"),
        (5, "Platts 65%"),
        (6, "Platts 62/61"),
        (7, "MB"),
    ]
    rows = [
        ("2026/8/3", "0.2398", "94.10", "80.80", "111.00", "2.70"),
        ("2026-08-04", "0.2400", "94.20", "81.00", "111.30", "2.80"),
        ("2026-08-05", "0.2399", "94.15", "80.90", "111.15", "2.75"),
        ("2026-08-06", "0.2399", "94.15", "80.90", "111.15", "2.75"),
    ]
    cells = [
        {"row": 0, "col": col, "text": text}
        for col, text in headers
    ]
    for row_no, values in enumerate(rows, start=1):
        date_value, lp, sixty_one, fifty_eight, sixty_five, spread = values
        cells.extend(
            [
                {"row": row_no, "col": 0, "text": date_value},
                {"row": row_no, "col": 1, "text": lp},
                {"row": row_no, "col": 2, "text": sixty_one},
                {"row": row_no, "col": 3, "text": "无关列"},
                {"row": row_no, "col": 4, "text": fifty_eight},
                {"row": row_no, "col": 5, "text": sixty_five},
                {"row": row_no, "col": 6, "text": spread},
                {"row": row_no, "col": 7, "text": ""},
            ]
        )
    cells.append({"row": 5, "col": 0, "text": "2026-08-07"})
    cells.extend(
        {"row": 6, "col": col, "text": text}
        for col, text in {
            0: "MTD",
            1: "0.2399",
            2: "94.15",
            4: "80.90",
            5: "111.15",
            6: "2.75",
        }.items()
    )
    return {"request_id": "mock-request-1", "cells": cells}


def payload_for_month(month):
    payload = sample_payload()
    for cell in payload["cells"]:
        if cell["col"] == 0 and 1 <= cell["row"] <= 4:
            cell["text"] = f"{month}-{cell['row'] + 2:02d}"
    return payload


def test_parser_locates_target_headers_ignores_unrelated_columns_and_blank_future_rows():
    result = parse_table_payload(sample_payload())

    assert result["issues"] == []
    assert [row["business_date"] for row in result["rows"]] == [
        "2026-08-03",
        "2026-08-04",
        "2026-08-05",
        "2026-08-06",
    ]
    assert result["mtd"] == {
        "platts_lp": Decimal("0.2399"),
        "platts_61": Decimal("94.15"),
        "platts_58": Decimal("80.90"),
        "platts_65": Decimal("111.15"),
        "spread_61_62": Decimal("2.75"),
    }


def test_parser_normalizes_decimal_spaces_and_rejects_missing_columns():
    payload = sample_payload()
    for cell in payload["cells"]:
        if cell["text"] == "0.2398":
            cell["text"] = "0. 2398"
        if cell["text"] == "111.00":
            cell["text"] = "111. 00"
    result = parse_table_payload(payload)
    assert result["rows"][0]["platts_lp"] == Decimal("0.2398")
    assert result["rows"][0]["platts_65"] == Decimal("111.00")

    missing = sample_payload()
    missing["cells"] = [cell for cell in missing["cells"] if cell["text"] != "Platts 58%"]
    missing_result = parse_table_payload(missing)
    assert any(issue["code"] == "missing_header" for issue in missing_result["issues"])


def test_parser_requires_mtd_and_routes_mismatch_to_review():
    wrong = sample_payload()
    for cell in wrong["cells"]:
        if cell["row"] == 6 and cell["col"] == 2:
            cell["text"] = "94.16"
    result = parse_table_payload(wrong)
    assert any(issue["code"] == "mtd_mismatch" for issue in result["issues"])

    without_mtd = sample_payload()
    without_mtd["cells"] = [cell for cell in without_mtd["cells"] if cell["row"] != 6]
    result = parse_table_payload(without_mtd)
    assert any(issue["code"] == "missing_mtd" for issue in result["issues"])


def test_decimal_formulas_and_mtd_match_fixed_business_checksum():
    row = {
        "platts_lp": Decimal("0.2399"),
        "platts_61": Decimal("94.15"),
        "platts_58": Decimal("80.90"),
        "platts_65": Decimal("111.15"),
        "spread_61_62": Decimal("2.75"),
    }
    derived = calculate_derived(row)
    assert derived == {
        "platts_62_equivalent": Decimal("96.90"),
        "spread_65_62": Decimal("14.25"),
        "spread_65_61": Decimal("17.00"),
    }
    mtd = calculate_mtd([row])
    assert mtd["platts_lp"] == Decimal("0.2399")
    assert mtd["platts_61"] == Decimal("94.15")
    assert mtd["platts_58"] == Decimal("80.90")
    assert mtd["platts_65"] == Decimal("111.15")
    assert mtd["spread_61_62"] == Decimal("2.75")
    assert mtd["spread_65_62"] == Decimal("14.25")
    assert mtd["spread_65_61"] == Decimal("17.00")


def test_all_platts_series_use_usd_per_ton():
    assert [unit for _, _, unit in SERIES] == ["美元/吨"] * 6


def test_aliyun_provider_uses_supported_parameters_and_surfaces_vendor_error(monkeypatch):
    monkeypatch.setenv("PLATTS_OCR_ACCESS_KEY_ID", "example-id")
    monkeypatch.setenv("PLATTS_OCR_ACCESS_KEY_SECRET", "example-secret")
    captured = {}

    class ErrorResponse:
        status_code = 400
        headers = {}
        content = b'{"Code":"InvalidAccessKeyId.NotFound","Message":"The specified access key does not exist."}'

        def json(self):
            return {
                "Code": "InvalidAccessKeyId.NotFound",
                "Message": "The specified access key does not exist.",
            }

    def request(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return ErrorResponse()

    provider = AliyunRecognizeTableOcrProvider(request_fn=request, timeout=1)
    params = provider._signed_params()
    assert "IsHandWriting" not in params
    assert params["Action"] == "RecognizeTableOcr"

    with pytest.raises(OCRProviderError) as error:
        provider.recognize(PNG_BYTES)

    assert "InvalidAccessKeyId.NotFound" in str(error.value)
    assert "The specified access key does not exist." in str(error.value)
    assert "example-secret" not in str(error.value)
    assert error.value.status_code == 400
    assert captured["data"] == PNG_BYTES


def test_month_selection_is_exact_and_backfill_stays_in_its_month(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    user = {"id": 1, "name": "admin", "role": "管理员"}

    august = process_platts_import(
        PNG_BYTES,
        user=user,
        provider=MockTableOCRProvider(sample_payload()),
    )
    september = process_platts_import(
        PNG_BYTES + b"september",
        user=user,
        provider=MockTableOCRProvider(payload_for_month("2026-09")),
    )

    assert august["status"] == "imported"
    assert september["status"] == "imported"
    assert _summary("2026-08")["count"] == 4
    assert _summary("2026-09")["count"] == 4
    empty = _summary("2026-07")
    assert empty["month"] == "2026-07"
    assert empty["count"] == 0
    assert empty["latest_month"] == "2026-09"
    assert empty["mtd"] == {}

    with db.connect() as conn:
        dates = [
            row["business_date"]
            for row in conn.execute(
                "SELECT business_date FROM platts_index_daily ORDER BY business_date"
            ).fetchall()
        ]
    assert dates == [
        "2026-08-03",
        "2026-08-04",
        "2026-08-05",
        "2026-08-06",
        "2026-09-03",
        "2026-09-04",
        "2026-09-05",
        "2026-09-06",
    ]


def test_provider_error_and_review_never_write_daily_data(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    user = {"id": 1, "name": "admin", "role": "管理员"}

    class FailingProvider:
        name = "mock"

        def recognize(self, image_bytes):
            raise TimeoutError("ocr timeout")

    failed = process_platts_import(PNG_BYTES, user=user, provider=FailingProvider())
    assert failed["status"] == "failed"

    broken = sample_payload()
    for cell in broken["cells"]:
        if cell["row"] == 2 and cell["col"] == 5:
            cell["text"] = "111.xx"
    review = process_platts_import(
        PNG_BYTES + b"2",
        user=user,
        provider=MockTableOCRProvider(broken),
    )
    assert review["status"] == "review_required"
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM platts_index_daily").fetchone()["c"] == 0


def test_hash_reuse_and_same_day_revision_are_explicit(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    user = {"id": 1, "name": "admin", "role": "管理员"}

    class CountingProvider(MockTableOCRProvider):
        calls = 0

        def recognize(self, image_bytes):
            self.calls += 1
            return super().recognize(image_bytes)

    provider = CountingProvider(sample_payload())
    first = process_platts_import(PNG_BYTES, user=user, provider=provider)
    second = process_platts_import(PNG_BYTES, user=user, provider=provider)
    assert first["status"] == "imported"
    assert second["status"] == "imported"
    assert second["reused"] is True
    assert provider.calls == 1

    changed = sample_payload()
    for cell in changed["cells"]:
        if cell["row"] == 1 and cell["col"] == 5:
            cell["text"] = "111.10"
        if cell["row"] == 6 and cell["col"] == 5:
            cell["text"] = "111.18"
    conflict = process_platts_import(
        PNG_BYTES + b"3",
        user=user,
        provider=MockTableOCRProvider(changed),
    )
    assert conflict["status"] == "review_required"
    with db.connect() as conn:
        daily = conn.execute(
            "SELECT platts_65 FROM platts_index_daily WHERE business_date = '2026-08-03'"
        ).fetchone()
        assert Decimal(str(daily["platts_65"])) == Decimal("111.00")

    confirmed = confirm_platts_import(
        conflict["draft_token"],
        conflict["preview"]["rows"],
        user=user,
        reason="确认修订截图中的 2026-08-03 数值",
    )
    assert confirmed["status"] == "imported"
    with db.connect() as conn:
        daily = conn.execute(
            "SELECT platts_65 FROM platts_index_daily WHERE business_date = '2026-08-03'"
        ).fetchone()
        revision = conn.execute("SELECT reason FROM platts_index_revisions").fetchone()
    assert Decimal(str(daily["platts_65"])) == Decimal("111.10")
    assert revision["reason"] == "确认修订截图中的 2026-08-03 数值"


def test_schema_permissions_and_backup_contract(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        daily_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(platts_index_daily)").fetchall()
        }
    assert {
        "platts_index_daily",
        "platts_index_import_batches",
        "platts_index_revisions",
    } <= tables
    assert {"platts_62_equivalent", "spread_65_62", "spread_65_61"}.isdisjoint(daily_columns)
    assert db.MODULES.index(("信息预警管理", "platts_index_monitor", "普氏指数监控")) == db.MODULES.index(("信息预警管理", "info_summary", "实时信息汇总")) + 1
    assert permissions.default_permission_levels("贸易处", "用户")["platts_index_monitor"] == "operate"
    assert permissions.default_permission_levels("公司领导", "领导")["platts_index_monitor"] == "view"
    guest = db.ensure_guest_user()
    assert not permissions.can(guest, "platts_index.data", "view")
    assert not permissions.can(guest, "platts_index.imports", "import")

    from scripts.backup_database import CORE_TABLES

    assert {
        "platts_index_daily",
        "platts_index_import_batches",
        "platts_index_revisions",
    } <= set(CORE_TABLES)


def test_postgres_schema_uses_idempotent_create_statements(monkeypatch):
    statements = []

    class FakeCursor:
        def execute(self, sql, params=None):
            statements.append(sql)

        def fetchall(self):
            return []

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

        def commit(self):
            return None

    monkeypatch.setattr(db, "_is_pg", lambda: True)
    db.migrate_platts_index_schema(FakeConnection())
    sql = "\n".join(statements)
    assert "CREATE TABLE IF NOT EXISTS platts_index_daily" in sql
    assert "ADD CONSTRAINT IF NOT EXISTS" not in sql
    assert "NUMERIC" in sql
