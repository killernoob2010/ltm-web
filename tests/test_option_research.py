from __future__ import annotations

import sqlite3

from backend.app import db, option_research


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
                "credentials_configured": True,
                "professional_downloader_available": True,
                "raw": "must-not-leak",
            },
        }
    )

    assert safe["error_code"] == "tqsdk_probe_failed"
    assert "username" not in safe
    assert "raw" not in safe["checks"]


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
