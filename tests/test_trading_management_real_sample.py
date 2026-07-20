"""2026 年 6 月文华三表的只读验收基线；样本不在机器上时自动跳过。"""
from pathlib import Path
import os
import sys

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import db, trading_management


SAMPLE_DIR = Path("/Users/wangjingze/建龙/期货组/文化交易记录")
TRADE_FILE = SAMPLE_DIR / "期货账户-成交记录6月.xlsx"
CLOSE_FILE = SAMPLE_DIR / "期货账户-平仓记录.xlsx"
POSITION_FILE = SAMPLE_DIR / "期货账户-期末持仓6月.xlsx"
DAILY_STATEMENT = Path(
    "/Users/wangjingze/Desktop/902711111BILLS/902711111BILLS/D20260529o.txt"
)
MONTHLY_STATEMENT = Path(
    "/Users/wangjingze/Desktop/902711111BILLS/D202606o.txt"
)


@pytest.mark.skipif(
    not all(path.exists() for path in (TRADE_FILE, CLOSE_FILE, POSITION_FILE)),
    reason="本机未提供已确认的 2026 年 6 月文华三表",
)
def test_june_wenhua_sample_matches_confirmed_acceptance_baseline(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "real-sample.db")
    db.init_db()
    with db.connect() as conn:
        account_id = conn.execute(
            "SELECT id FROM trading_accounts WHERE account_code = 'hongyuan_futures'"
        ).fetchone()["id"]

    preview = trading_management.preview_trading_import(
        account_id, TRADE_FILE, CLOSE_FILE, POSITION_FILE, actor="acceptance-test"
    )
    confirmed = trading_management.confirm_trading_import(
        preview["preview_batch_id"], actor="acceptance-test"
    )
    matching = trading_management.match_imported_facts(confirmed["batch_id"])
    overview = trading_management.build_overview(
        trading_management.FactFilters(page=1, page_size=20)
    )
    option_closes = trading_management.query_business_rows(
        "options", "closes", trading_management.FactFilters(page=1, page_size=20)
    )
    with db.connect() as conn:
        raw_option_close_count = conn.execute(
            "SELECT COUNT(*) AS c FROM trading_close_facts WHERE asset_type = 'option'"
        ).fetchone()["c"]

    assert preview["counts"] == {"trade": 2753, "close": 2351, "position": 13017}
    assert overview["trades"]["record_count"] == 2753
    assert overview["closes"]["record_count"] == 2351
    assert overview["positions"]["record_count"] == 669
    assert overview["trades"]["fee"] == pytest.approx(35380.88)
    assert overview["closes"]["fact_close_pnl"] == pytest.approx(3497480)
    assert overview["closes"]["fee"] == pytest.approx(16885.34)
    assert overview["positions"]["margin"] == pytest.approx(26177056.50)
    assert matching["close_trade_links"] == 2351
    assert option_closes["total_items"] == raw_option_close_count
    assert overview["positions"]["floating_pnl_status"] == "pending_calculation"
    assert sum(row["fact_close_pnl"] for row in overview["daily_close_pnl"]) == pytest.approx(3497480)


@pytest.mark.skipif(
    not DAILY_STATEMENT.exists() or not MONTHLY_STATEMENT.exists(),
    reason="本机未提供已确认的日结/月结 TXT",
)
def test_real_statements_establish_zero_difference_opening_continuity(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "statement-sample.db")
    db.init_db()
    with db.connect() as conn:
        account_id = conn.execute(
            "SELECT id FROM trading_accounts WHERE account_code = 'hongyuan_futures'"
        ).fetchone()["id"]

    daily = trading_management.preview_settlement_import(
        account_id, "daily.txt", DAILY_STATEMENT.read_bytes(), "acceptance-test"
    )
    trading_management.confirm_settlement_import(
        daily["preview_batch_id"], "acceptance-test"
    )
    monthly = trading_management.preview_settlement_import(
        account_id, "monthly.txt", MONTHLY_STATEMENT.read_bytes(), "acceptance-test"
    )
    monthly_confirmed = trading_management.confirm_settlement_import(
        monthly["preview_batch_id"], "acceptance-test"
    )

    assert daily["counts"] == {
        "trade": 190,
        "close": 185,
        "exercise": 0,
        "position": 579,
    }
    assert monthly["counts"] == {
        "trade": 2753,
        "close": 2351,
        "exercise": 1,
        "position": 669,
    }
    assert monthly["continuity"]["status"] == "passed"
    assert monthly["continuity"]["previous_snapshot_date"] == "20260529"
    assert monthly["continuity"]["difference_lots"] == 0
    assert monthly_confirmed["counts"] == monthly["counts"]
    with db.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM trading_trade_facts WHERE is_current = 1"
        ).fetchone()["c"] == 2943
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM trading_close_facts WHERE is_current = 1"
        ).fetchone()["c"] == 2536
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM trading_position_snapshots WHERE is_current = 1"
        ).fetchone()["c"] == 1248
