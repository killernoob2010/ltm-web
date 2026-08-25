from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "import_spot_ledger_staging.py"
SPEC = importlib.util.spec_from_file_location("import_spot_ledger_staging", SCRIPT_PATH)
staging_import = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(staging_import)


def test_staging_import_accepts_only_the_exact_staging_host():
    assert staging_import.validate_staging_base_url("https://ltm-web-staging.onrender.com") == "https://ltm-web-staging.onrender.com"
    with pytest.raises(ValueError):
        staging_import.validate_staging_base_url("https://ltm-web-gt13.onrender.com")
    with pytest.raises(ValueError):
        staging_import.validate_staging_base_url("http://ltm-web-staging.onrender.com")
    with pytest.raises(ValueError):
        staging_import.validate_staging_base_url("https://ltm-web-staging.onrender.com/?apply=true")


def test_build_backfill_plan_is_2026_only_blank_only_and_conflict_safe():
    source_rows = [
        {"AD": "C-102", "H": "铁矿石", "Z": 820, "X": 100, "U": "2026-08-18", "K": "补录船", "AM": "来源备注"},
        {"AD": "C-101", "H": "铁矿石", "Z": 795, "X": 80, "U": "2026-08-19", "K": "Excel船"},
        {"AD": "C-103", "H": "铁矿石", "Z": 790, "X": 60, "U": "2025-08-17", "K": "历史船"},
    ]
    records = [
        {"record_id": "r-102", "AD": "C-102", "H": "铁矿石", "U": "2026-08-18", "L": 100, "X": 100},
        {"record_id": "r-101", "AD": "C-101", "H": "铁矿石", "U": "2026-08-19", "L": 80, "X": 80},
        {"record_id": "r-103", "AD": "C-103", "H": "铁矿石", "U": "2025-08-17", "L": 60, "X": 60},
    ]
    details = {
        "r-102": {**records[0], "Z": 820, "K": "", "AM": ""},
        "r-101": {**records[1], "Z": 795, "K": "已有船"},
        "r-103": {**records[2], "Z": 790, "K": ""},
    }

    result = staging_import.build_backfill_plan(source_rows, records, details)

    assert result["skipped_historical"] == 1
    assert result["unique_matches"] == 2
    assert result["candidate_updates"] == 1
    assert result["conflicts"] == 1
    assert result["field_updates"] == {"K": 1, "AM": 1}
    assert result["plans"][0]["record_id"] == "r-102"
    assert result["plans"][0]["values"] == {"K": "补录船", "AM": "来源备注"}


def test_build_backfill_plan_accepts_known_vat_price_and_settlement_quantity_differences():
    source_rows = [{"AD": "C-201", "H": "PB粉", "Z": 606.19469, "X": 7200, "U": "2026-08-05", "K": "瑞明"}]
    records = [{"record_id": "r-201", "AD": "C-201", "H": "PB粉", "U": "2026-08-05", "L": 7173.62, "X": 7173.62}]
    details = {"r-201": {**records[0], "Z": 685, "K": ""}}

    result = staging_import.build_backfill_plan(source_rows, records, details)

    assert result["unique_matches"] == 1
    assert result["unmatched"] == 0
    assert result["quantity_conflicts"] == 1
    assert result["price_vat_normalized"] == 1
    assert result["candidate_updates"] == 1
    assert result["plans"] == [{"record_id": "r-201", "values": {"K": "瑞明"}, "current_values": {"K": ""}}]


def test_cli_summary_does_not_include_credentials():
    summary = staging_import.safe_summary({"username": "admin", "password": "secret", "updated": 3})
    assert "password" not in summary
    assert "secret" not in summary


def test_apply_backfill_plan_uses_snapshot_and_expected_values(tmp_path):
    class FakeClient:
        def __init__(self):
            self.patches = []

        def get_detail(self, record_id):
            raise AssertionError("apply must not refetch every detail")

        def patch(self, record_id, values, expected_values=None):
            self.patches.append((record_id, values, expected_values))
            return {"ok": True}

    client = FakeClient()
    result = {
        "plans": [{"record_id": "r-1", "values": {"K": "船名A"}, "current_values": {"K": ""}}],
    }

    applied = staging_import.apply_backfill_plan(client, result, tmp_path / "changes.json")

    assert applied["applied"] == 1
    assert applied["failed"] == 0
    assert client.patches == [("r-1", {"K": "船名A"}, {"K": ""})]
