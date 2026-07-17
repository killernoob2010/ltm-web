import copy
import os
import sys
from datetime import date

import pytest
from fastapi import HTTPException


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import db  # noqa: E402
from app.iron_ore_basis_import import DETAIL_COLUMNS, RESULT_COLUMNS  # noqa: E402
from app.iron_ore_basis_rules import (  # noqa: E402
    BasisRulePack,
    IndicatorMapping,
    ProductRule,
)
from app.iron_ore_basis_sources import SourcePoint  # noqa: E402
from app.iron_ore_basis_sync import BasisSources, sync_basis_range  # noqa: E402
from app import iron_ore_basis_snapshot_sync as snapshot_sync  # noqa: E402


class FakeEbc:
    def fetch_points(self, codes, start_date, end_date):
        del codes, start_date, end_date
        return [
            SourcePoint(
                "EBC",
                "ID-PB",
                date(2026, 7, 13),
                780,
                "a" * 64,
            )
        ]


class FakeSina:
    def fetch_closes(self, start_date, end_date):
        del start_date, end_date
        return {date(2026, 7, 13): 744.5}


class StaticSnapshotClient:
    def __init__(self, payload):
        self.payload = payload

    def fetch_snapshot(self):
        return copy.deepcopy(self.payload)


def use_temp_db(path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", path)
    monkeypatch.setattr(db, "DB_PATH", path / "basis-snapshot.db")
    db.init_db()


def rule_pack():
    product = ProductRule(
        product="PB粉",
        parameter_year=2026,
        parameter_type="典型值",
        fe=0.615,
        sio2=0.04,
        al2o3=0.023,
        phosphorus=0.0011,
        sulfur=0.0002,
        h2o=0.08,
        sulfur_defaulted=False,
        brand_adjustment=15,
        parameter_source="source-2026",
        parameter_version="2026-典型值",
    )
    mapping = IndicatorMapping(
        indicator_code="ID-PB",
        indicator_name="PB粉：61.5%Fe：日照港",
        port="日照港",
        product="PB粉",
        ebc_price_fe=0.615,
        price_proxy_indicator=None,
        price_parameter_spec_diff=False,
        ebc_original_port="日照港",
    )
    return BasisRulePack(
        rule_version="I2312 / F-DCE I004-2021",
        effective_from=date(2026, 7, 13),
        products={product.product: product},
        indicators={mapping.indicator_code: mapping},
    )


def seed_source_snapshot(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    summary = sync_basis_range(
        date(2026, 7, 13),
        date(2026, 7, 13),
        trigger_type="scheduled_2130",
        slot_key="scheduled:2026-07-13:2130",
        apply=True,
        sources=BasisSources(ebc=FakeEbc(), sina=FakeSina()),
        rule_pack=rule_pack(),
    )
    assert summary.status == "success"
    monkeypatch.setenv("IRON_ORE_BASIS_SYNC_MODE", "source")
    return snapshot_sync.build_iron_ore_basis_snapshot()


def table_count(table):
    with db.connect() as conn:
        return int(
            db._exec(conn.cursor(), f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
        )


def test_snapshot_secret_hides_endpoint_and_can_reuse_existing_cross_env_secret(
    monkeypatch,
):
    monkeypatch.delenv("IRON_ORE_BASIS_SNAPSHOT_SHARED_SECRET", raising=False)
    monkeypatch.setenv("ORDER_FINANCE_SNAPSHOT_SHARED_SECRET", "shared-secret")

    with pytest.raises(HTTPException) as missing:
        snapshot_sync._require_snapshot_secret(None)
    with pytest.raises(HTTPException) as wrong:
        snapshot_sync._require_snapshot_secret("Bearer wrong")

    assert missing.value.status_code == 404
    assert wrong.value.status_code == 404
    snapshot_sync._require_snapshot_secret("Bearer shared-secret")


def test_source_snapshot_requires_source_mode_and_completed_source_run(
    tmp_path, monkeypatch
):
    use_temp_db(tmp_path, monkeypatch)
    monkeypatch.setenv("IRON_ORE_BASIS_SYNC_MODE", "snapshot_follower")

    with pytest.raises(HTTPException) as wrong_mode:
        snapshot_sync.build_iron_ore_basis_snapshot()
    assert wrong_mode.value.status_code == 503

    monkeypatch.setenv("IRON_ORE_BASIS_SYNC_MODE", "source")
    with pytest.raises(HTTPException) as no_run:
        snapshot_sync.build_iron_ore_basis_snapshot()
    assert no_run.value.status_code == 503


def test_source_snapshot_is_canonical_hashed_and_contains_no_database_ids(
    tmp_path, monkeypatch
):
    payload = seed_source_snapshot(tmp_path, monkeypatch)

    assert payload["schema_version"] == 1
    assert payload["latest_business_date"] == "2026-07-13"
    assert payload["record_count"] == 1
    assert payload["snapshot_hash"] == snapshot_sync.basis_snapshot_hash(
        payload["records"]
    )
    assert payload["source_version"] == payload["snapshot_hash"]
    assert set(payload["records"][0]["result"]) == set(RESULT_COLUMNS)
    assert set(payload["records"][0]["detail"]) == set(DETAIL_COLUMNS) - {"result_id"}
    assert "id" not in repr(payload["records"][0])
    assert "result_id" not in repr(payload["records"][0])


def test_follower_appends_snapshot_and_repeat_is_idempotent(tmp_path, monkeypatch):
    payload = seed_source_snapshot(tmp_path / "source", monkeypatch)
    use_temp_db(tmp_path / "target", monkeypatch)

    first = snapshot_sync.run_iron_ore_basis_snapshot_follow(
        "startup:2026-07-17",
        client=StaticSnapshotClient(payload),
    )
    second = snapshot_sync.run_iron_ore_basis_snapshot_follow(
        "startup:2026-07-17",
        client=StaticSnapshotClient(payload),
    )

    assert first == {"status": "success", "inserted": 1}
    assert second == {"status": "success", "inserted": 0}
    assert table_count("iron_ore_basis_results") == 1
    assert table_count("iron_ore_basis_details") == 1


def test_follower_rejects_hash_count_and_duplicate_business_keys(
    tmp_path, monkeypatch
):
    payload = seed_source_snapshot(tmp_path, monkeypatch)

    bad_hash = copy.deepcopy(payload)
    bad_hash["snapshot_hash"] = "0" * 64
    with pytest.raises(snapshot_sync.IronOreBasisSnapshotSyncError) as hash_error:
        snapshot_sync._validate_snapshot_payload(bad_hash)
    assert hash_error.value.stage == "snapshot_hash"

    bad_count = copy.deepcopy(payload)
    bad_count["record_count"] = 2
    with pytest.raises(snapshot_sync.IronOreBasisSnapshotSyncError) as count_error:
        snapshot_sync._validate_snapshot_payload(bad_count)
    assert count_error.value.stage == "snapshot_validation"

    duplicate = copy.deepcopy(payload)
    duplicate["records"].append(copy.deepcopy(duplicate["records"][0]))
    duplicate["record_count"] = 2
    duplicate["snapshot_hash"] = snapshot_sync.basis_snapshot_hash(
        duplicate["records"]
    )
    duplicate["source_version"] = duplicate["snapshot_hash"]
    with pytest.raises(snapshot_sync.IronOreBasisSnapshotSyncError) as duplicate_error:
        snapshot_sync._validate_snapshot_payload(duplicate)
    assert duplicate_error.value.stage == "snapshot_validation"

    unapproved = copy.deepcopy(payload)
    unapproved["source_status"] = "failed"
    with pytest.raises(snapshot_sync.IronOreBasisSnapshotSyncError) as approval_error:
        snapshot_sync._validate_snapshot_payload(unapproved)
    assert approval_error.value.stage == "snapshot_validation"


def test_follower_rejects_existing_difference_before_inserting_any_new_row(
    tmp_path, monkeypatch
):
    payload = seed_source_snapshot(tmp_path / "source", monkeypatch)
    use_temp_db(tmp_path / "target", monkeypatch)
    snapshot_sync.run_iron_ore_basis_snapshot_follow(
        "startup:2026-07-17",
        client=StaticSnapshotClient(payload),
    )
    with db.connect() as conn:
        db._exec(
            conn.cursor(),
            "UPDATE iron_ore_basis_results SET basis = basis + 1",
        )

    expanded = copy.deepcopy(payload)
    new_record = copy.deepcopy(expanded["records"][0])
    for section in ("result", "detail"):
        new_record[section]["business_key"] = "new-business-key"
        new_record[section]["business_date"] = "2026-07-14"
    expanded["records"].append(new_record)
    expanded["record_count"] = 2
    expanded["latest_business_date"] = "2026-07-14"
    expanded["snapshot_hash"] = snapshot_sync.basis_snapshot_hash(expanded["records"])
    expanded["source_version"] = expanded["snapshot_hash"]

    with pytest.raises(snapshot_sync.IronOreBasisSnapshotSyncError) as error:
        snapshot_sync.run_iron_ore_basis_snapshot_follow(
            "poll:2026-07-17T15:30:00+08:00",
            client=StaticSnapshotClient(expanded),
        )

    assert error.value.stage == "snapshot_difference"
    assert table_count("iron_ore_basis_results") == 1
    assert table_count("iron_ore_basis_details") == 1


def test_follower_rolls_back_rows_when_sync_run_record_cannot_be_written(
    tmp_path, monkeypatch
):
    payload = seed_source_snapshot(tmp_path / "source", monkeypatch)
    use_temp_db(tmp_path / "target", monkeypatch)
    original_exec = db._exec

    def fail_sync_run(cur, sql, params=()):
        if "INSERT INTO iron_ore_basis_sync_runs" in sql:
            raise RuntimeError("sync run write failed")
        return original_exec(cur, sql, params)

    monkeypatch.setattr(db, "_exec", fail_sync_run)
    with pytest.raises(RuntimeError, match="sync run write failed"):
        snapshot_sync.run_iron_ore_basis_snapshot_follow(
            "startup:2026-07-17",
            client=StaticSnapshotClient(payload),
        )

    assert table_count("iron_ore_basis_results") == 0
    assert table_count("iron_ore_basis_details") == 0


def test_scheduler_routes_only_the_configured_mode(monkeypatch):
    calls = []
    monkeypatch.setenv("IRON_ORE_BASIS_AUTO_SYNC_ENABLED", "true")
    monkeypatch.setattr(
        snapshot_sync,
        "start_iron_ore_basis_source_scheduler",
        lambda interval: calls.append(("source", interval)) or True,
    )
    monkeypatch.setattr(
        snapshot_sync,
        "_start_snapshot_follower_scheduler",
        lambda interval: calls.append(("follower", interval)) or True,
    )

    monkeypatch.setenv("IRON_ORE_BASIS_SYNC_MODE", "source")
    assert snapshot_sync.start_iron_ore_basis_sync_scheduler(30) is True
    monkeypatch.setenv("IRON_ORE_BASIS_SYNC_MODE", "snapshot_follower")
    assert snapshot_sync.start_iron_ore_basis_sync_scheduler(45) is True
    monkeypatch.setenv("IRON_ORE_BASIS_SYNC_MODE", "invalid")
    assert snapshot_sync.start_iron_ore_basis_sync_scheduler(60) is False

    assert calls == [("source", 30), ("follower", 45)]
