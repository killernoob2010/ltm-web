"""交易管理模块的数据结构与业务规则测试。"""
import importlib.util
import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import db


TRADING_TABLES = {
    "trading_accounts",
    "trading_import_batches",
    "trading_source_rows",
    "trading_fact_identities",
    "trading_trade_facts",
    "trading_close_facts",
    "trading_position_snapshots",
    "trading_contract_specs",
    "trading_fact_close_allocations",
    "trading_close_trade_links",
    "trading_business_subjects",
    "trading_strategies",
    "trading_business_assignments",
    "trading_business_close_allocations",
    "trading_business_allocation_audit",
}


def use_temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "trading-management.db")
    db.init_db()


def test_trading_management_schema_contains_isolated_tables(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)

    with db.connect() as conn:
        actual = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'trading_%'"
            ).fetchall()
        }

    assert TRADING_TABLES <= actual
    assert not any(name.startswith("sh_junneng") for name in TRADING_TABLES)


def test_trading_management_router_module_exists():
    assert importlib.util.find_spec("app.trading_management") is not None


def test_trading_management_modules_are_new_first_level_menu():
    modules = [item for item in db.MODULES if item[0] == "交易管理"]

    assert modules == [
        ("交易管理", "trading_overview", "总览"),
        ("交易管理", "trading_positions", "持仓与交易"),
        ("交易管理", "trading_sh_junneng", "上海钧能台账"),
        ("交易管理", "trading_options", "期权台账"),
        ("交易管理", "trading_export", "汇总与导出"),
    ]
