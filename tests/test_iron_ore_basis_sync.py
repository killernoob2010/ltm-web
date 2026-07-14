import os
import sys
from datetime import date

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import db  # noqa: E402
from app import iron_ore_basis_sync as sync_module  # noqa: E402
from app.iron_ore_basis_rules import BasisRulePack, IndicatorMapping, ProductRule  # noqa: E402
from app.iron_ore_basis_sources import BasisSourceError, SourcePoint  # noqa: E402
from app.iron_ore_basis_sync import BasisSources, sync_basis_range  # noqa: E402
from scripts.sync_iron_ore_basis import build_parser  # noqa: E402


class FakeEbc:
    def __init__(self, points):
        self.points = points
        self.calls = []

    def fetch_points(self, codes, start_date, end_date):
        self.calls.append((list(codes), start_date, end_date))
        return list(self.points)


class FakeSina:
    def __init__(self, closes):
        self.closes = closes

    def fetch_closes(self, start_date, end_date):
        return dict(self.closes)


class FailingEbc:
    def fetch_points(self, codes, start_date, end_date):
        raise BasisSourceError("http_error", "EBC 请求失败")


def use_temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "basis-sync.db")
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


def ebc_point(value=780):
    return SourcePoint("EBC", "ID-PB", date(2026, 7, 13), value, ("a" if value == 780 else "b") * 64)


def sources(*, value=780, closes=None):
    return BasisSources(
        ebc=FakeEbc([ebc_point(value)]),
        sina=FakeSina(closes if closes is not None else {date(2026, 7, 13): 744.5}),
    )


def table_count(table):
    with db.connect() as conn:
        return db._exec(conn.cursor(), f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]


def test_sync_dry_run_calculates_without_any_database_write(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)

    summary = sync_basis_range(
        date(2026, 7, 13),
        date(2026, 7, 13),
        trigger_type="manual",
        slot_key="manual:dry-run",
        apply=False,
        sources=sources(),
        rule_pack=rule_pack(),
    )

    assert summary.status == "success"
    assert summary.combinations_written == 1
    assert table_count("iron_ore_basis_sync_runs") == 0
    assert table_count("iron_ore_basis_source_points") == 0
    assert table_count("iron_ore_basis_results") == 0
    assert table_count("iron_ore_basis_details") == 0


def test_sync_writes_complete_result_detail_and_source_points(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)

    summary = sync_basis_range(
        date(2026, 7, 13),
        date(2026, 7, 13),
        trigger_type="manual",
        slot_key="manual:first",
        apply=True,
        sources=sources(),
        rule_pack=rule_pack(),
    )

    assert summary.status == "success"
    assert summary.combinations_written == 1
    assert summary.source_points_inserted == 2
    assert table_count("iron_ore_basis_results") == 1
    assert table_count("iron_ore_basis_details") == 1
    with db.connect() as conn:
        cur = conn.cursor()
        result = db._exec(cur, "SELECT * FROM iron_ore_basis_results").fetchone()
        detail = db._exec(cur, "SELECT * FROM iron_ore_basis_details").fetchone()
    assert result["source_workbook_name"] == "API:EBC+Sina"
    assert detail["result_id"] == result["id"]
    assert detail["basis"] == result["basis"]


def test_sync_skips_incomplete_combination_without_blocking_run(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)

    summary = sync_basis_range(
        date(2026, 7, 13),
        date(2026, 7, 13),
        trigger_type="manual",
        slot_key="manual:missing-ebc",
        apply=True,
        sources=sources(value=None),
        rule_pack=rule_pack(),
    )

    assert summary.combinations_written == 0
    assert summary.combinations_skipped == 1
    assert table_count("iron_ore_basis_results") == 0


def test_sync_is_idempotent_across_different_retry_slots(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    kwargs = {
        "start_date": date(2026, 7, 13),
        "end_date": date(2026, 7, 13),
        "trigger_type": "manual",
        "apply": True,
        "sources": sources(),
        "rule_pack": rule_pack(),
    }

    first = sync_basis_range(slot_key="manual:first", **kwargs)
    second = sync_basis_range(slot_key="manual:retry", **kwargs)

    assert first.combinations_written == 1
    assert second.combinations_written == 0
    assert table_count("iron_ore_basis_results") == 1
    assert table_count("iron_ore_basis_details") == 1


def test_changed_source_value_is_recorded_without_overwriting_canonical_or_result(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    first = sync_basis_range(
        date(2026, 7, 13), date(2026, 7, 13), trigger_type="manual",
        slot_key="manual:first", apply=True, sources=sources(value=780), rule_pack=rule_pack(),
    )
    with db.connect() as conn:
        original_basis = db._exec(conn.cursor(), "SELECT basis FROM iron_ore_basis_results").fetchone()["basis"]

    second = sync_basis_range(
        date(2026, 7, 13), date(2026, 7, 13), trigger_type="manual",
        slot_key="manual:changed", apply=True, sources=sources(value=790), rule_pack=rule_pack(),
    )

    with db.connect() as conn:
        cur = conn.cursor()
        point = db._exec(
            cur,
            """SELECT canonical_value, last_observed_value, difference_detected, difference_count
               FROM iron_ore_basis_source_points
               WHERE source_name='EBC' AND indicator_key='ID-PB'""",
        ).fetchone()
        current_basis = db._exec(cur, "SELECT basis FROM iron_ore_basis_results").fetchone()["basis"]
    assert first.source_differences == 0
    assert second.source_differences == 1
    assert point["canonical_value"] == 780
    assert point["last_observed_value"] == 790
    assert point["difference_detected"] == 1
    assert point["difference_count"] == 1
    assert current_basis == original_basis


def test_manual_cli_defaults_to_dry_run():
    args = build_parser().parse_args(["--start-date", "2026-07-13", "--end-date", "2026-07-14"])

    assert args.apply is False


def test_duplicate_slot_skips_without_refetching_sources(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    active_sources = sources()
    kwargs = {
        "start_date": date(2026, 7, 13),
        "end_date": date(2026, 7, 13),
        "trigger_type": "manual",
        "slot_key": "manual:same-slot",
        "apply": True,
        "sources": active_sources,
        "rule_pack": rule_pack(),
    }

    sync_basis_range(**kwargs)
    second = sync_basis_range(**kwargs)

    assert second.status == "skipped"
    assert len(active_sources.ebc.calls) == 1


def test_sync_rolls_back_run_sources_and_result_when_pair_write_fails(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    monkeypatch.setattr(
        sync_module,
        "_insert_calculation",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("pair write failed")),
    )

    with pytest.raises(RuntimeError, match="pair write failed"):
        sync_basis_range(
            date(2026, 7, 13),
            date(2026, 7, 13),
            trigger_type="manual",
            slot_key="manual:rollback",
            apply=True,
            sources=sources(),
            rule_pack=rule_pack(),
        )

    assert table_count("iron_ore_basis_sync_runs") == 1
    assert table_count("iron_ore_basis_source_points") == 0
    assert table_count("iron_ore_basis_results") == 0
    assert table_count("iron_ore_basis_details") == 0
    with db.connect() as conn:
        run = db._exec(conn.cursor(), "SELECT status, error_code FROM iron_ore_basis_sync_runs").fetchone()
    assert run["status"] == "failed"
    assert run["error_code"] == "RuntimeError"


def test_sync_records_source_failure_without_partial_data(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)

    with pytest.raises(BasisSourceError):
        sync_basis_range(
            date(2026, 7, 13),
            date(2026, 7, 13),
            trigger_type="manual",
            slot_key="manual:source-failed",
            apply=True,
            sources=BasisSources(ebc=FailingEbc(), sina=FakeSina({})),
            rule_pack=rule_pack(),
        )

    with db.connect() as conn:
        run = db._exec(conn.cursor(), "SELECT status, error_code FROM iron_ore_basis_sync_runs").fetchone()
    assert run["status"] == "failed"
    assert run["error_code"] == "http_error"
    assert table_count("iron_ore_basis_source_points") == 0
