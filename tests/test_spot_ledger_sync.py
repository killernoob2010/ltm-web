from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "spot_ledger_sales_contract_fixture.json"


@pytest.fixture
def ledger_db(tmp_path, monkeypatch):
    from app import db

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "spot-ledger.db")
    db.init_db()
    return db


def load_fixture_scan():
    from app.spot_ledger_sync import FixtureSalesContractSource

    return FixtureSalesContractSource(FIXTURE_PATH).fetch_full_scan()


def test_fixture_scan_accepts_only_seven_groups_spot_and_effective_records():
    from app.spot_ledger import SHANGHAI_GROUPS

    scan = load_fixture_scan()
    assert set(record["E"] for record in scan.records) == set(SHANGHAI_GROUPS)
    assert all(record["eligible"] for record in scan.records)
    assert len({record["source_detail_id"] for record in scan.records}) == len(scan.records)
    assert sum(record["AD"] == "C-100" for record in scan.records) == 2
    assert {record["D"] for record in scan.records} >= {"现货-市场加价", "现货-背对背", "船货-落地"}


def test_full_scan_upsert_is_idempotent_and_preserves_manual_fields(ledger_db):
    from app.spot_ledger_sync import apply_full_scan

    scan = load_fixture_scan()
    first = apply_full_scan(scan, "2026-08-24T09:00+08:00")
    second = apply_full_scan(scan, "2026-08-24T10:00+08:00")
    assert first["inserted"] == len(scan.records)
    assert second["inserted"] == 0
    assert second["updated"] == len(scan.records)
    with ledger_db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM spot_ledger_records WHERE record_source_type = '现货同步'").fetchone()["c"]
        detail = conn.execute("SELECT source_detail_id FROM spot_ledger_records WHERE \"AD\" = 'C-100' ORDER BY source_detail_id").fetchall()
    assert count == len(scan.records)
    assert len(detail) == 2


def test_incomplete_scan_does_not_hide_existing_record(ledger_db):
    from app.spot_ledger_sync import FullScanResult, apply_full_scan, get_active_records

    first = load_fixture_scan()
    apply_full_scan(first, "2026-08-24T09:00+08:00")
    broken = FullScanResult(
        records=first.records[:1], page_count=1, expected_page_count=2,
        total_count=1, complete=False, errors=["page_missing"], source_mode="fixture",
    )
    result = apply_full_scan(broken, "2026-08-24T10:00+08:00")
    assert result["hidden"] == 0
    assert len(get_active_records()) == len(first.records)


def test_complete_scan_soft_hides_missing_record_only_after_success(ledger_db):
    from app.spot_ledger_sync import FullScanResult, apply_full_scan, get_active_records

    first = load_fixture_scan()
    apply_full_scan(first, "2026-08-24T09:00+08:00")
    smaller = FullScanResult(
        records=first.records[:1], page_count=1, expected_page_count=1,
        total_count=1, complete=True, errors=[], source_mode="fixture",
    )
    result = apply_full_scan(smaller, "2026-08-24T10:00+08:00")
    assert result["hidden"] == len(first.records) - 1
    assert len(get_active_records()) == 1


def test_duplicate_detail_ids_make_scan_incomplete():
    from app.spot_ledger_sync import FullScanResult, validate_full_scan

    scan = load_fixture_scan()
    duplicate = FullScanResult(
        records=[scan.records[0], scan.records[0]], page_count=1,
        expected_page_count=1, total_count=2, complete=True, errors=[], source_mode="fixture",
    )
    result = validate_full_scan(duplicate)
    assert not result.complete
    assert any("duplicate_detail_id" in str(error) for error in result.errors)


def test_unattended_source_without_profile_is_explicitly_blocked(monkeypatch):
    from app.spot_ledger_sync import ProfiledSalesContractSource, SalesContractSourceError

    monkeypatch.delenv("SPOT_LEDGER_SOURCE_PROFILE", raising=False)
    source = ProfiledSalesContractSource.from_env()
    with pytest.raises(SalesContractSourceError, match="auth_unavailable"):
        source.fetch_full_scan()


def test_due_slots_are_daily_nine_to_eighteen_and_second_free():
    from app.spot_ledger_sync import due_spot_ledger_slots

    slots = due_spot_ledger_slots(datetime.fromisoformat("2026-08-24T18:15:42+08:00"))
    assert len(slots) == 10
    assert slots[0].endswith("09:00+08:00")
    assert slots[-1].endswith("18:00+08:00")
    assert all("." not in slot and ":42" not in slot for slot in slots)


def test_history_migration_only_updates_unique_match_and_defaults_to_dry_run(ledger_db, tmp_path):
    from openpyxl import Workbook, load_workbook
    from app.spot_ledger_sync import apply_full_scan, migrate_history_workbook

    scan = load_fixture_scan()
    apply_full_scan(scan, "2026-08-24T09:00+08:00")
    path = tmp_path / "history.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "现货业务台账"
    sheet.append(["销售合同号", "商品名称", "销售价格（元/吨）", "销售数量（吨）", "备注"])
    sheet.append(["C-100", "铁矿石", 800, 100, "历史人工备注"])
    sheet.append(["不存在", "铁矿石", 800, 100, "不应写入"])
    workbook.save(path)
    preview = migrate_history_workbook(path)
    assert preview["matched"] == 1
    assert preview["updated"] == 0
    applied = migrate_history_workbook(path, apply=True)
    assert applied["updated"] == 1
    with ledger_db.connect() as conn:
        row = conn.execute("SELECT \"AM\" FROM spot_ledger_records WHERE \"AD\" = 'C-100' ORDER BY source_detail_id LIMIT 1").fetchone()
    assert row["AM"] == "历史人工备注"
