"""收盘交易复盘 Phase 1 的合成事实、证据和只读 API 合同测试。"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys

import pytest
from fastapi.testclient import TestClient


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import db


ACCOUNT_CODE = "hongyuan_futures"
OPTION_EXCHANGE = "大商所"


def _review_module():
    spec = importlib.util.find_spec("app.closing_trading_review")
    assert spec is not None, "closing_trading_review module has not been implemented"
    return importlib.import_module("app.closing_trading_review")


def use_temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "closing-review.db")
    db.init_db()


def _account_id(conn):
    return conn.execute(
        "SELECT id FROM trading_accounts WHERE account_code = ?",
        (ACCOUNT_CODE,),
    ).fetchone()["id"]


def _seed_batch(
    conn,
    trading_date,
    *,
    statement_type="daily",
    range_start=None,
    range_end=None,
    priority=100,
):
    range_start = range_start or trading_date
    range_end = range_end or trading_date
    return conn.execute(
        """
        INSERT INTO trading_import_batches
            (account_id, range_start, range_end, position_snapshot_date, status,
             statement_type, statement_file_name, statement_file_sha256,
             source_priority, parse_summary, created_by, confirmed_by, confirmed_at)
        VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, 'synthetic-test',
                'synthetic-test', ?)
        """,
        (
            _account_id(conn),
            range_start,
            range_end,
            range_end,
            statement_type,
            f"synthetic-{statement_type}-{range_end}.txt",
            f"sha-{statement_type}-{range_end}",
            priority,
            json.dumps({"synthetic": True}, ensure_ascii=False),
            f"{range_end[:4]}-{range_end[4:6]}-{range_end[6:]}T16:00:00+08:00",
        ),
    ).lastrowid


def _seed_source(conn, batch_id, source_type, row_no, *, present=True):
    if not present:
        return 999999
    raw_json = json.dumps(
        {"synthetic_source": source_type, "row": row_no},
        ensure_ascii=False,
        sort_keys=True,
    )
    return conn.execute(
        """
        INSERT INTO trading_source_rows
            (batch_id, source_type, source_file, source_sheet, source_row_no,
             raw_hash, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            batch_id,
            source_type,
            f"synthetic-{batch_id}.txt",
            f"synthetic-{source_type}",
            row_no,
            f"hash-{batch_id}-{source_type}-{row_no}",
            raw_json,
        ),
    ).lastrowid


def _seed_identity(conn, fact_type, stable_key):
    return conn.execute(
        """
        INSERT INTO trading_fact_identities (account_id, fact_type, stable_key)
        VALUES (?, ?, ?)
        """,
        (_account_id(conn), fact_type, stable_key),
    ).lastrowid


def seed_position(
    conn,
    batch_id,
    trading_date,
    contract,
    direction,
    quantity,
    average_price,
    valuation_price,
    *,
    row_no=1,
    source_present=True,
):
    source_id = _seed_source(
        conn, batch_id, "position", row_no, present=source_present
    )
    identity_id = _seed_identity(
        conn, "position", f"position-{batch_id}-{contract}-{direction}-{row_no}"
    )
    conn.execute(
        """
        INSERT INTO trading_position_snapshots
            (identity_id, batch_id, source_row_id, snapshot_date, snapshot_time,
             exchange, contract, asset_type, direction, open_date, quantity,
             average_price, margin, valuation_price, floating_pnl, is_current,
             valuation_status, verification_status)
        VALUES (?, ?, ?, ?, '15:00:00', ?, ?, 'option', ?, ?, ?, ?, 0, ?, NULL,
                1, ?, 'matched')
        """,
        (
            identity_id,
            batch_id,
            source_id,
            trading_date,
            OPTION_EXCHANGE,
            contract,
            direction,
            "20260520",
            quantity,
            average_price,
            valuation_price,
            "settlement_reference" if valuation_price is not None else "unavailable",
        ),
    )
    return identity_id


def seed_close(
    conn,
    batch_id,
    trading_date,
    contract,
    open_side,
    quantity,
    open_price,
    close_price,
    fact_close_pnl,
    *,
    row_no=1,
    source_present=True,
):
    source_id = _seed_source(
        conn, batch_id, "close", row_no, present=source_present
    )
    identity_id = _seed_identity(
        conn, "close", f"close-{batch_id}-{contract}-{open_side}-{row_no}"
    )
    close_side = "卖" if open_side == "买" else "买"
    conn.execute(
        """
        INSERT INTO trading_close_facts
            (identity_id, batch_id, source_row_id, open_date, close_date,
             exchange, contract, asset_type, open_side, close_side, quantity,
             open_price, close_price, fact_close_pnl, matched_fee, settlement_type,
             is_current, fee_status, verification_status)
        VALUES (?, ?, ?, '20260520', ?, ?, ?, 'option', ?, ?, ?, ?, ?, ?,
                12.5, 'trade_close', 1, 'matched', 'matched')
        """,
        (
            identity_id,
            batch_id,
            source_id,
            trading_date,
            OPTION_EXCHANGE,
            contract,
            open_side,
            close_side,
            quantity,
            open_price,
            close_price,
            fact_close_pnl,
        ),
    )
    return identity_id


def add_source_difference(conn, identity_id, batch_id):
    conn.execute(
        """
        INSERT INTO trading_fact_source_differences
            (identity_id, fact_type, old_batch_id, new_batch_id, diff_json)
        VALUES (?, 'close', NULL, ?, ?)
        """,
        (identity_id, batch_id, json.dumps({"synthetic": "conflict"})),
    )


def seed_user_without_option_permission(conn):
    user_id = conn.execute(
        """
        INSERT INTO users
            (name, username, department, password_hash, role, status)
        VALUES ('无期权权限用户', 'no-option-review-user', '贸易处', ?, '用户', '启用')
        """,
        (db.password_hash("synthetic-password"),),
    ).lastrowid
    conn.commit()
    return user_id


def seed_other_account_position(conn, trading_date):
    other_account_id = conn.execute(
        """
        INSERT INTO trading_accounts (account_code, display_name, masked_name)
        VALUES ('synthetic_other_account', '合成其他账户', '合成其他')
        """
    ).lastrowid
    batch_id = conn.execute(
        """
        INSERT INTO trading_import_batches
            (account_id, range_start, range_end, position_snapshot_date, status,
             statement_type, statement_file_name, statement_file_sha256,
             source_priority, parse_summary, created_by, confirmed_by, confirmed_at)
        VALUES (?, ?, ?, ?, 'active', 'daily', 'synthetic-other.txt',
                'sha-other', 100, '{}', 'synthetic-test', 'synthetic-test', ?)
        """,
        (
            other_account_id,
            trading_date,
            trading_date,
            trading_date,
            f"{trading_date[:4]}-{trading_date[4:6]}-{trading_date[6:]}T16:00:00+08:00",
        ),
    ).lastrowid
    source_id = conn.execute(
        """
        INSERT INTO trading_source_rows
            (batch_id, source_type, source_file, source_sheet, source_row_no,
             raw_hash, raw_json)
        VALUES (?, 'position', 'synthetic-other.txt', 'synthetic-position', 1,
                'hash-other', '{}')
        """,
        (batch_id,),
    ).lastrowid
    identity_id = conn.execute(
        """
        INSERT INTO trading_fact_identities (account_id, fact_type, stable_key)
        VALUES (?, 'position', 'synthetic-other-position')
        """,
        (other_account_id,),
    ).lastrowid
    conn.execute(
        """
        INSERT INTO trading_position_snapshots
            (identity_id, batch_id, source_row_id, snapshot_date, snapshot_time,
             exchange, contract, asset_type, direction, open_date, quantity,
             average_price, margin, valuation_price, floating_pnl, is_current,
             valuation_status, verification_status)
        VALUES (?, ?, ?, ?, '15:00:00', '大商所', 'i2609-c-700', 'option',
                '卖', '20260520', 10, 100, 0, 0, NULL, 1,
                'settlement_reference', 'matched')
        """,
        (identity_id, batch_id, source_id, trading_date),
    )


def admin_token(conn):
    user_id = conn.execute(
        "SELECT id FROM users WHERE username = '管理员'"
    ).fetchone()["id"]
    conn.commit()
    return db.create_session(user_id)


def test_complete_report_groups_dynamic_option_positions_and_separates_pnl(
    tmp_path, monkeypatch
):
    review = _review_module()
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        batch_id = _seed_batch(conn, "20260529")
        seed_position(conn, batch_id, "20260529", "i2609-c-700", "卖", 3, 8, 5)
        seed_position(conn, batch_id, "20260529", "i2610-p-650", "买", 1, 6, 7)
        seed_close(
            conn,
            batch_id,
            "20260529",
            "i2609-c-700",
            "卖",
            2,
            8,
            5,
            600,
        )

    report = review.build_option_daily_review("2026-05-29")

    assert report.status == "complete"
    assert report.trading_date == "20260529"
    assert report.valuation_basis == "daily_settlement"
    assert report.realized_close_pnl.value == 600
    assert report.unrealized_pnl.value == 1000
    assert report.metadata.data_as_of == "2026-05-29T16:00:00+08:00"
    assert len(report.pnl_attribution) == 1
    assert report.pnl_attribution[0].realized_close_pnl.value == 600
    assert report.pnl_attribution[0].contribution_ratio.value == 1
    assert {(item.expiry_month, item.option_type, item.direction)
            for item in report.position_groups} == {
        ("2609", "Call", "卖"), ("2610", "Put", "买")
    }
    assert report.call_net.direction_label == "净卖"
    assert report.call_net.lots.value == 3
    assert report.call_net.wan_tons.value == 0.03
    assert report.realized_close_pnl.metadata.evidence_refs
    assert report.unrealized_pnl.metadata.source == "trading_position_snapshots"
    assert report.position_groups[0].floating_pnl.metadata.evidence_refs
    assert report.position_groups[0].contract_count.value == 1
    assert report.position_groups[0].contract_count.metadata.evidence_refs
    assert all(
        metric.metadata.calculation_version == "closing-option-review-v1"
        for metric in (
            report.call_net.lots,
            report.call_net.wan_tons,
            report.realized_close_pnl,
            report.unrealized_pnl,
        )
    )
    assert "total_pnl" not in report.model_dump()


def test_net_position_uses_dynamic_months_and_shows_net_buy_when_buy_exceeds_sell(
    tmp_path, monkeypatch
):
    review = _review_module()
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        batch_id = _seed_batch(conn, "20260529")
        seed_position(conn, batch_id, "20260529", "i2609-c-700", "卖", 5, 10, 8)
        seed_position(conn, batch_id, "20260529", "i2609-c-740", "买", 2, 12, 13, row_no=2)
        seed_position(conn, batch_id, "20260529", "i2610-c-800", "买", 8, 10, 11, row_no=3)
        seed_position(conn, batch_id, "20260529", "i2610-p-650", "卖", 3, 8, 9, row_no=4)

    report = review.build_option_daily_review("20260529")

    assert report.status == "complete"
    assert report.call_net.direction_label == "净买"
    assert report.call_net.lots.value == 5
    assert report.call_net.tons.value == 500
    assert report.call_net.wan_tons.value == 0.05
    assert report.put_net.direction_label == "净卖"
    assert report.put_net.lots.value == 3
    assert {item.expiry_month for item in report.position_groups} == {"2609", "2610"}
    assert {item.strike_min.value for item in report.position_groups} == {
        650, 700, 740, 800
    }
    assert all(item.option_type in {"Call", "Put"} for item in report.position_groups)


def test_zero_position_daily_statement_is_confirmed_zero_not_missing_data(
    tmp_path, monkeypatch
):
    review = _review_module()
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        _seed_batch(conn, "20260529")

    report = review.build_option_daily_review("20260529")

    assert report.status == "complete"
    assert report.position_availability == "confirmed_zero"
    assert report.position_groups == []
    assert report.call_net.lots.value == 0
    assert report.put_net.wan_tons.value == 0
    assert report.realized_close_pnl.value == 0
    assert report.unrealized_pnl.value == 0
    assert "无期权持仓" in report.summary_text


def test_missing_daily_statement_waits_without_claiming_zero_position(
    tmp_path, monkeypatch
):
    review = _review_module()
    use_temp_db(tmp_path, monkeypatch)

    report = review.build_option_daily_review("20260529")

    assert report.status == "waiting_for_data"
    assert report.position_availability == "unknown"
    assert report.position_groups == []
    assert report.call_net.lots.value is None
    assert report.unrealized_pnl.value is None
    assert "日结算单" in " ".join(report.warnings)
    assert "无期权持仓" not in report.summary_text


def test_monthly_only_report_is_partial_when_historical_valuation_is_missing(
    tmp_path, monkeypatch
):
    review = _review_module()
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        batch_id = _seed_batch(
            conn,
            "20260529",
            statement_type="monthly",
            range_start="20260501",
            range_end="20260529",
            priority=200,
        )
        seed_position(
            conn,
            batch_id,
            "20260529",
            "i2609-c-700",
            "卖",
            2,
            8,
            None,
        )
        seed_close(
            conn,
            batch_id,
            "20260529",
            "i2609-c-700",
            "卖",
            1,
            8,
            5,
            300,
        )

    report = review.build_option_daily_review("20260529")

    assert report.status == "partial"
    assert report.position_availability == "derived_from_monthly"
    assert report.position_groups[0].quantity_lots.value == 2
    assert report.position_groups[0].floating_pnl.value is None
    assert report.realized_close_pnl.value == 300
    assert report.unrealized_pnl.value is None
    assert "历史估值" in " ".join(report.warnings)


def test_source_conflict_suppresses_deterministic_numeric_conclusions(
    tmp_path, monkeypatch
):
    review = _review_module()
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        batch_id = _seed_batch(conn, "20260529")
        seed_position(conn, batch_id, "20260529", "i2609-c-700", "卖", 2, 8, 5)
        close_id = seed_close(
            conn,
            batch_id,
            "20260529",
            "i2609-c-700",
            "卖",
            1,
            8,
            5,
            300,
        )
        add_source_difference(conn, close_id, batch_id)

    report = review.build_option_daily_review("20260529")

    assert report.status == "data_anomaly"
    assert report.realized_close_pnl.value is None
    assert report.unrealized_pnl.value is None
    assert report.position_groups == []
    assert "来源冲突" in " ".join(report.warnings)
    assert report.metadata.evidence_refs


def test_missing_key_source_evidence_is_data_anomaly(
    tmp_path, monkeypatch
):
    review = _review_module()
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        batch_id = _seed_batch(conn, "20260529")
        seed_position(
            conn,
            batch_id,
            "20260529",
            "i2609-c-700",
            "卖",
            2,
            8,
            5,
            source_present=False,
        )

    report = review.build_option_daily_review("20260529")

    assert report.status == "data_anomaly"
    assert report.call_net.lots.value is None
    assert report.unrealized_pnl.value is None
    assert "关键证据" in " ".join(report.warnings)


def test_realized_close_pnl_and_unrealized_pnl_are_not_combined(
    tmp_path, monkeypatch
):
    review = _review_module()
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        batch_id = _seed_batch(conn, "20260529")
        seed_position(conn, batch_id, "20260529", "i2609-p-700", "买", 1, 10, 8)
        seed_close(
            conn,
            batch_id,
            "20260529",
            "i2609-p-700",
            "卖",
            1,
            10,
            7,
            200,
        )

    report = review.build_option_daily_review("20260529")

    assert report.realized_close_pnl.value == 200
    assert report.unrealized_pnl.value == -200
    assert report.realized_close_pnl.value != report.unrealized_pnl.value
    assert "total_pnl" not in report.model_dump()
    assert "不扣手续费" in report.realized_close_pnl.metadata.warnings[0]


def test_report_scope_excludes_facts_from_another_account(tmp_path, monkeypatch):
    review = _review_module()
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        batch_id = _seed_batch(conn, "20260529")
        seed_position(conn, batch_id, "20260529", "i2609-c-700", "卖", 1, 8, 5)
        seed_other_account_position(conn, "20260529")

    report = review.build_option_daily_review("20260529")

    assert report.status == "complete"
    assert report.unrealized_pnl.value == 300
    assert {item.contract for group in report.position_groups for item in group.details} == {
        "i2609-c-700"
    }


def test_option_review_api_checks_permission_before_report_query(
    tmp_path, monkeypatch
):
    review = _review_module()
    from app import main

    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        user_id = seed_user_without_option_permission(conn)
        token = db.create_session(user_id)
    monkeypatch.setattr(
        review,
        "build_option_daily_review",
        lambda _date: pytest.fail("permission must be checked before data access"),
    )

    with TestClient(main.app) as client:
        response = client.get(
            "/api/closing-trading-review/options/daily-summary"
            "?trading_date=20260529",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403
    assert "statement_account_code" not in response.text
    assert "TEST001" not in response.text


def test_option_review_api_returns_typed_report_and_rejects_invalid_date(
    tmp_path, monkeypatch
):
    review = _review_module()
    from app import main

    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        batch_id = _seed_batch(conn, "20260529")
        seed_position(conn, batch_id, "20260529", "i2609-c-700", "卖", 1, 8, 5)
        token = admin_token(conn)

    with TestClient(main.app) as client:
        response = client.get(
            "/api/closing-trading-review/options/daily-summary"
            "?trading_date=2026-05-29",
            headers={"Authorization": f"Bearer {token}"},
        )
        invalid = client.get(
            "/api/closing-trading-review/options/daily-summary"
            "?trading_date=2026-02-30",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["trading_date"] == "20260529"
    assert payload["account_name"] == "宏源账户"
    assert payload["instrument"] == "铁矿石期权"
    assert payload["valuation_basis"] == "daily_settlement"
    assert payload["realized_close_pnl"]["metadata"]["evidence_refs"]
    assert "statement_account_code" not in response.text
    assert "synthetic-" not in response.text
    assert invalid.status_code == 422
