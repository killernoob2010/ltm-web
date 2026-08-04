from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from backend.app import db, option_backtest, option_research


def _use_temp_sqlite(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "app.db")


def test_schema_is_isolated_and_idempotent(monkeypatch, tmp_path):
    _use_temp_sqlite(monkeypatch, tmp_path)

    option_research.ensure_schema()
    option_research.ensure_schema()

    with sqlite3.connect(db.DB_PATH) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert set(option_research.OPTION_RESEARCH_TABLES) <= tables
    assert "trading_trade_facts" not in tables


def test_parse_dce_iron_ore_option_symbol():
    assert option_research.parse_dce_iron_ore_option_symbol("DCE.i2609-P-700") == {
        "symbol": "DCE.i2609-P-700",
        "exchange_id": "DCE",
        "product_id": "i",
        "underlying_symbol": "DCE.i2609",
        "option_class": "PUT",
        "strike_price": 700.0,
    }
    assert option_research.parse_dce_iron_ore_option_symbol("SHFE.rb2610C3000") is None


def test_bar_quality_issues_detects_bad_ohlc_and_order():
    rows = [
        {
            "symbol": "DCE.i2609-P-700",
            "duration_seconds": 300,
            "datetime_nano": 2,
            "open_price": 1.0,
            "high_price": 1.2,
            "low_price": 0.8,
            "close_price": 1.1,
            "volume": 20,
            "open_interest": 50,
        },
        {
            "symbol": "DCE.i2609-P-700",
            "duration_seconds": 300,
            "datetime_nano": 1,
            "open_price": 1.0,
            "high_price": 0.9,
            "low_price": 1.1,
            "close_price": 1.2,
            "volume": -1,
            "open_interest": 50,
        },
    ]

    codes = {issue["code"] for issue in option_research.bar_quality_issues(rows)}

    assert "non_increasing_timestamp" in codes
    assert "invalid_high" in codes
    assert "invalid_low" in codes
    assert "invalid_volume" in codes


def test_safe_probe_result_drops_unknown_fields_and_errors():
    safe = option_research._safe_probe_result(
        {
            "status": "ready",
            "error_code": "raw-secret-bearing-error",
            "futures_count": 3,
            "options_count": 8,
            "bars_count": 10,
            "username": "must-not-leak",
            "checks": {
                "probe_version": "v2",
                "credentials_configured": True,
                "professional_downloader_available": True,
                "raw": "must-not-leak",
            },
        }
    )

    assert safe["error_code"] == "tqsdk_probe_failed"
    assert "username" not in safe
    assert "raw" not in safe["checks"]
    assert safe["checks"]["probe_version"] == "v2"


def test_readiness_disabled_outside_staging(monkeypatch):
    monkeypatch.delenv("OPTION_RESEARCH_ENABLED", raising=False)
    monkeypatch.delenv("RENDER_SERVICE_NAME", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_HOSTNAME", raising=False)

    assert option_research.readiness_snapshot() == {
        "enabled": False,
        "status": "disabled",
        "message": "历史期权研究探针只在 Staging 环境启用。",
    }


def test_missing_credentials_are_reported_without_values(monkeypatch):
    from backend.app import option_research_probe

    monkeypatch.delenv("TQSDK_USERNAME", raising=False)
    monkeypatch.delenv("TQSDK_PASSWORD", raising=False)

    result = option_research_probe.run_probe()

    assert result["status"] == "blocked"
    assert result["error_code"] == "credentials_missing"
    assert result["checks"]["credentials_configured"] is False


def test_probe_selects_middle_strike_instead_of_lexicographic_middle():
    from backend.app import option_research_probe

    symbol = option_research_probe._representative_option(
        [
            "DCE.i2609-C-1000",
            "DCE.i2609-C-600",
            "DCE.i2609-P-800",
            "DCE.i2609-C-800",
        ]
    )

    assert symbol == "DCE.i2609-C-800"


def test_backtest_schema_is_idempotent_and_separate(monkeypatch, tmp_path):
    _use_temp_sqlite(monkeypatch, tmp_path)

    option_backtest.create_schema()
    option_backtest.create_schema()

    with sqlite3.connect(db.DB_PATH) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "option_research_results" in tables
    assert "trading_trade_facts" not in tables


def test_backtest_contract_quote_requires_static_metadata():
    quote = SimpleNamespace(
        underlying_symbol="DCE.i2609",
        expire_datetime="2026-08-12T07:00:00+00:00",
        volume_multiple=100,
        price_tick=0.1,
    )

    contract = option_backtest.parse_contract_quote("DCE.i2609-P-700", quote)

    assert contract is not None
    assert contract.option_class == "PUT"
    assert contract.strike_price == 700.0
    assert contract.volume_multiple == 100.0
    assert option_backtest._parse_date(contract.expire_datetime).isoformat() == "2026-08-12"


def test_discover_contracts_prefers_recent_expired_contracts():
    class FakeApi:
        def query_quotes(self, **kwargs):
            return ["DCE.i2509", "DCE.i2606"] if kwargs["expired"] else ["DCE.i2609"]

        def query_options(self, underlying):
            return []

    futures, contracts, capped = option_backtest.discover_contracts(FakeApi())

    assert futures == ["DCE.i2606", "DCE.i2509", "DCE.i2609"]
    assert contracts == []
    assert capped is False


def test_frame_rows_normalizes_valid_tqsdk_like_frame():
    frame = {
        "datetime": [1_700_000_000_000_000_000],
        "open": [2.0],
        "high": [2.2],
        "low": [1.8],
        "close": [2.1],
        "volume": [20],
        "open_oi": [50],
    }

    rows = option_backtest.frame_rows(
        frame,
        symbol="DCE.i2609-P-700",
        duration_seconds=86400,
        run_key="run-1",
    )

    assert len(rows) == 1
    assert rows[0]["close_price"] == 2.1
    assert rows[0]["duration_seconds"] == 86400
    assert rows[0]["trading_date"] == "2023-11-15"


def test_fetch_daily_rows_batch_reuses_one_update_loop():
    frame = {
        "datetime": [1_700_000_000_000_000_000],
        "open": [2.0],
        "high": [2.2],
        "low": [1.8],
        "close": [2.1],
        "volume": [20],
        "open_oi": [50],
    }

    class FakeApi:
        def get_kline_serial(self, symbol, duration_seconds, data_length):
            return frame

        def wait_update(self, deadline):
            return True

    rows, missing = option_backtest.fetch_daily_rows_batch(
        FakeApi(),
        ["DCE.i2609", "DCE.i2609-P-700"],
        "run-1",
    )

    assert len(rows) == 2
    assert missing == []


def test_daily_a0_screen_uses_real_option_rows_and_returns_monthly_metrics():
    contracts = [
        option_backtest.Contract(
            symbol="DCE.i2609-C-840",
            underlying_symbol="DCE.i2609",
            option_class="CALL",
            strike_price=840.0,
            expire_datetime="2026-02-12T07:00:00+00:00",
            volume_multiple=100.0,
            price_tick=0.1,
        ),
        option_backtest.Contract(
            symbol="DCE.i2609-P-760",
            underlying_symbol="DCE.i2609",
            option_class="PUT",
            strike_price=760.0,
            expire_datetime="2026-02-12T07:00:00+00:00",
            volume_multiple=100.0,
            price_tick=0.1,
        ),
    ]
    bars = []
    for day, underlying, call, put in [
        ("2026-01-05", 800.0, 2.0, 2.0),
        ("2026-01-06", 795.0, 0.4, 0.4),
        ("2026-01-07", 790.0, 0.3, 0.3),
    ]:
        bars.append(
            {
                "symbol": "DCE.i2609",
                "trading_date": day,
                "close_price": underlying,
            }
        )
        for symbol, price in (
            ("DCE.i2609-C-840", call),
            ("DCE.i2609-P-760", put),
        ):
            bars.append(
                {
                    "symbol": symbol,
                    "trading_date": day,
                    "open_price": price,
                    "close_price": price,
                }
            )

    result = option_backtest.simulate_daily_a0(contracts=contracts, bars=bars)

    assert result["granularity"] == "daily"
    assert result["final_eligible"] is False
    assert result["days"] == 3
    assert result["total_entries"] > 0
    assert isinstance(result["monthly"], list)
