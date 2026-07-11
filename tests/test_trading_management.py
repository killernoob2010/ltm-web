"""交易管理模块的数据结构与业务规则测试。"""
import importlib.util
import os
import sys

import pytest
from openpyxl import Workbook


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import db, trading_management


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


def build_trade_workbook(path):
    book = Workbook()
    sheet = book.active
    sheet.title = "成交记录"
    sheet.append([
        "交易所", "合约", "买卖", "开平", "手数", "成交价", "成交额",
        "平仓盈亏", "手续费", "投保", "权利金收支",
    ])
    sheet.append(["20260630"])
    sheet.append(["SHFE", "rb2610", "买", "开", 2, 3100, 62000, 0, 6, "保", 0])
    sheet.append(["SHFE", "rb2610", "卖", "平今", 2, 3120, 62400, 999, 6, "保", 0])
    sheet.append(["DCE", "i2609-C-700", "买", "开", 1, 4.2, 420, 0, 1.2, "投", -420])
    book.save(path)
    return path


def build_close_workbook(path):
    book = Workbook()
    sheet = book.active
    sheet.title = "平仓记录"
    sheet.append([
        "交易所", "合约", "开仓日期", "买入", "卖出", "手数", "价格",
        "逐笔平仓盈亏", "盯市平仓盈亏", "手续费", "权利金收支",
    ])
    sheet.append(["20260630"])
    sheet.append(["SHFE", "rb2610", "20260620", 2, None, None, 3100, None, None, None, None])
    sheet.append([None, None, None, None, 2, 2, 3120, 1200, 1100, None, 0])
    book.save(path)
    return path


def build_position_workbook(path):
    book = Workbook()
    sheet = book.active
    sheet.title = "期末持仓"
    sheet.append([
        "交易所", "开仓日期", "合约", "买卖", "手数", "价格", "浮动盈亏",
        "盯市盈亏", "占用保证金", "投保",
    ])
    sheet.append(["20260630"])
    sheet.append(["SHFE", "20260630", "rb2610", "买", 10, 3100, 2000, 1800, 50000, "保"])
    book.save(path)
    return path


def test_parser_normalizes_grouped_wenhua_workbooks(tmp_path):
    trades = trading_management.parse_trade_workbook(build_trade_workbook(tmp_path / "trades.xlsx"))
    closes = trading_management.parse_close_workbook(build_close_workbook(tmp_path / "closes.xlsx"))
    positions = trading_management.parse_position_workbook(build_position_workbook(tmp_path / "positions.xlsx"))

    assert trades[0]["open_close"] == "开仓"
    assert trades[1]["open_close"] == "平仓"
    assert trades[2]["asset_type"] == "option"
    assert closes[0]["fact_close_pnl"] == 1200
    assert closes[0]["open_side"] == "买"
    assert positions[0]["margin"] == 50000
    assert positions[0]["valuation_status"] == "pending_calculation"


def test_parser_rejects_unknown_trade_headers(tmp_path):
    book = Workbook()
    book.active.title = "成交记录"
    book.active.append(["合约", "未知数量"])
    book.save(tmp_path / "bad.xlsx")

    with pytest.raises(ValueError, match="缺少必要字段"):
        trading_management.parse_trade_workbook(tmp_path / "bad.xlsx")


def test_fact_signature_is_stable_after_normalization():
    first = {
        "date": "20260630",
        "exchange": " shfe ",
        "contract": "RB2610",
        "side": "买",
        "open_close_raw": "开",
        "quantity": 2,
        "price": 3100.0,
        "turnover": 62000,
        "fee": 6,
    }
    second = dict(first, exchange="SHFE", contract="rb2610", price="3100.000000")

    assert trading_management.build_fact_signature("trade", "A001", first) == trading_management.build_fact_signature(
        "trade", "A001", second
    )


def test_import_preview_requires_all_three_files(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO trading_accounts (account_code, display_name) VALUES (?, ?)",
            ("A001", "测试账户"),
        )

    with pytest.raises(ValueError, match="成交、平仓、持仓三表必须齐全"):
        trading_management.preview_trading_import(
            account_id=1,
            trade_path=build_trade_workbook(tmp_path / "trades.xlsx"),
            close_path=None,
            position_path=build_position_workbook(tmp_path / "positions.xlsx"),
            actor="tester",
        )


def test_import_preview_rejects_missing_account(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="交易账户不存在或已停用"):
        trading_management.preview_trading_import(
            account_id=999,
            trade_path=build_trade_workbook(tmp_path / "trades.xlsx"),
            close_path=build_close_workbook(tmp_path / "closes.xlsx"),
            position_path=build_position_workbook(tmp_path / "positions.xlsx"),
            actor="tester",
        )


def test_import_preview_rejects_partially_overlapping_active_batch(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO trading_accounts (account_code, display_name) VALUES (?, ?)",
            ("A001", "测试账户"),
        )
        conn.execute(
            """
            INSERT INTO trading_import_batches
                (account_id, range_start, range_end, status)
            VALUES (1, '20260601', '20260630', 'active')
            """
        )

    with pytest.raises(ValueError, match="日期范围部分重叠"):
        trading_management.preview_trading_import(
            account_id=1,
            trade_path=build_trade_workbook(tmp_path / "trades.xlsx"),
            close_path=build_close_workbook(tmp_path / "closes.xlsx"),
            position_path=build_position_workbook(tmp_path / "positions.xlsx"),
            actor="tester",
        )
