"""交易总览专用汇总口径测试。"""

import pytest

from backend.app import db, trading_overview


def use_temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "trading-overview.db")
    db.init_db()


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"scope": "other"}, "未知总览统计范围"),
        ({"start_date": "20260701"}, "开始日期和结束日期必须同时提供"),
        ({"end_date": "20260731"}, "开始日期和结束日期必须同时提供"),
        (
            {"start_date": "2026-07-01", "end_date": "20260731"},
            "日期格式必须为 YYYYMMDD",
        ),
        (
            {"start_date": "20260731", "end_date": "20260701"},
            "开始日期不能晚于结束日期",
        ),
    ],
)
def test_overview_filters_reject_invalid_scope_and_date_ranges(kwargs, message):
    with pytest.raises(ValueError, match=message):
        trading_overview.OverviewFilters(**kwargs)


def test_latest_overview_date_respects_active_current_facts_and_account(
    tmp_path, monkeypatch
):
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        account_one = conn.execute(
            "SELECT id FROM trading_accounts WHERE account_code = 'hongyuan_futures'"
        ).fetchone()["id"]
        account_two = conn.execute(
            """
            INSERT INTO trading_accounts (account_code, display_name, masked_name)
            VALUES ('second', '第二账户', '第二账户')
            """
        ).lastrowid
        batch_one = conn.execute(
            """
            INSERT INTO trading_import_batches (account_id, status)
            VALUES (?, 'active')
            """,
            (account_one,),
        ).lastrowid
        batch_two = conn.execute(
            """
            INSERT INTO trading_import_batches (account_id, status)
            VALUES (?, 'active')
            """,
            (account_two,),
        ).lastrowid
        superseded_batch = conn.execute(
            """
            INSERT INTO trading_import_batches (account_id, status)
            VALUES (?, 'superseded')
            """,
            (account_one,),
        ).lastrowid
        source_one = conn.execute(
            """
            INSERT INTO trading_source_rows
                (batch_id, source_type, source_file, source_sheet, source_row_no,
                 raw_hash, raw_json)
            VALUES (?, 'trade', 'one.txt', 'trade', 1, 'one', '{}')
            """,
            (batch_one,),
        ).lastrowid
        source_two = conn.execute(
            """
            INSERT INTO trading_source_rows
                (batch_id, source_type, source_file, source_sheet, source_row_no,
                 raw_hash, raw_json)
            VALUES (?, 'position', 'two.txt', 'position', 1, 'two', '{}')
            """,
            (batch_two,),
        ).lastrowid
        source_old = conn.execute(
            """
            INSERT INTO trading_source_rows
                (batch_id, source_type, source_file, source_sheet, source_row_no,
                 raw_hash, raw_json)
            VALUES (?, 'trade', 'old.txt', 'trade', 1, 'old', '{}')
            """,
            (superseded_batch,),
        ).lastrowid
        identity_one = conn.execute(
            """
            INSERT INTO trading_fact_identities (account_id, fact_type, stable_key)
            VALUES (?, 'trade', 'one')
            """,
            (account_one,),
        ).lastrowid
        identity_two = conn.execute(
            """
            INSERT INTO trading_fact_identities (account_id, fact_type, stable_key)
            VALUES (?, 'position', 'two')
            """,
            (account_two,),
        ).lastrowid
        identity_old = conn.execute(
            """
            INSERT INTO trading_fact_identities (account_id, fact_type, stable_key)
            VALUES (?, 'trade', 'old')
            """,
            (account_one,),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO trading_trade_facts
                (identity_id, batch_id, source_row_id, trade_date, exchange,
                 contract, asset_type, side, open_close, quantity, price, is_current)
            VALUES (?, ?, ?, '20260718', '上期所', 'rb2610', 'future',
                    '买', '开仓', 1, 3500, 1)
            """,
            (identity_one, batch_one, source_one),
        )
        conn.execute(
            """
            INSERT INTO trading_position_snapshots
                (identity_id, batch_id, source_row_id, snapshot_date, exchange,
                 contract, asset_type, direction, quantity, average_price, is_current)
            VALUES (?, ?, ?, '20260721', '上期所', 'rb2610', 'future',
                    '买', 1, 3500, 1)
            """,
            (identity_two, batch_two, source_two),
        )
        conn.execute(
            """
            INSERT INTO trading_trade_facts
                (identity_id, batch_id, source_row_id, trade_date, exchange,
                 contract, asset_type, side, open_close, quantity, price, is_current)
            VALUES (?, ?, ?, '20260731', '上期所', 'rb2610', 'future',
                    '买', '开仓', 1, 3500, 0)
            """,
            (identity_old, superseded_batch, source_old),
        )

    assert trading_overview.latest_overview_date() == "20260721"
    assert trading_overview.latest_overview_date(account_one) == "20260718"
    assert trading_overview.latest_overview_date(account_two) == "20260721"


def _insert_identity(conn, account_id, fact_type, key):
    return conn.execute(
        """
        INSERT INTO trading_fact_identities (account_id, fact_type, stable_key)
        VALUES (?, ?, ?)
        """,
        (account_id, fact_type, key),
    ).lastrowid


def _seed_overview_sample(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        account_one = conn.execute(
            "SELECT id FROM trading_accounts WHERE account_code = 'hongyuan_futures'"
        ).fetchone()["id"]
        account_two = conn.execute(
            """
            INSERT INTO trading_accounts (account_code, display_name, masked_name)
            VALUES ('second-overview', '第二账户', '第二账户')
            """
        ).lastrowid
        batch_one = conn.execute(
            "INSERT INTO trading_import_batches (account_id, status) VALUES (?, 'active')",
            (account_one,),
        ).lastrowid
        batch_two = conn.execute(
            "INSERT INTO trading_import_batches (account_id, status) VALUES (?, 'active')",
            (account_two,),
        ).lastrowid
        old_batch = conn.execute(
            "INSERT INTO trading_import_batches (account_id, status) VALUES (?, 'superseded')",
            (account_one,),
        ).lastrowid

        def source(batch_id, key):
            return conn.execute(
                """
                INSERT INTO trading_source_rows
                    (batch_id, source_type, source_file, source_sheet, source_row_no,
                     raw_hash, raw_json)
                VALUES (?, 'statement', ?, 'statement', 1, ?, '{}')
                """,
                (batch_id, f"{key}.txt", key),
            ).lastrowid

        source_one = source(batch_one, "overview-one")
        source_two = source(batch_two, "overview-two")
        source_old = source(old_batch, "overview-old")

        def trade(account_id, batch_id, source_id, key, trade_date, contract,
                  side, open_close, quantity, fee, is_current=1):
            identity_id = _insert_identity(conn, account_id, "trade", key)
            conn.execute(
                """
                INSERT INTO trading_trade_facts
                    (identity_id, batch_id, source_row_id, trade_date, exchange,
                     contract, asset_type, side, open_close, quantity, price, fee,
                     is_current)
                VALUES (?, ?, ?, ?, '上期所', ?, 'future', ?, ?, ?, 3500, ?, ?)
                """,
                (
                    identity_id, batch_id, source_id, trade_date, contract, side,
                    open_close, quantity, fee, is_current,
                ),
            )
            return identity_id

        basic_open = trade(
            account_one, batch_one, source_one, "basic-open", "20260701",
            "rb2610", "买", "开仓", 10, 10,
        )
        basic_close_trade = trade(
            account_one, batch_one, source_one, "basic-close-trade", "20260710",
            "rb2610", "卖", "平仓", 4, 4,
        )
        strategic_open = trade(
            account_one, batch_one, source_one, "strategic-open", "20260702",
            "hc2610", "卖", "开仓", 5, 5,
        )
        unclassified_open = trade(
            account_one, batch_one, source_one, "unclassified-open", "20260703",
            "i2609", "买", "开仓", 2, 2,
        )
        second_basic_open = trade(
            account_two, batch_two, source_two, "second-basic-open", "20260705",
            "rb2610", "买", "开仓", 3, 3,
        )
        trade(
            account_one, old_batch, source_old, "superseded-trade", "20260731",
            "rb2610", "买", "开仓", 100, 100, is_current=0,
        )

        close_identity = _insert_identity(conn, account_one, "close", "basic-close")
        conn.execute(
            """
            INSERT INTO trading_close_facts
                (identity_id, batch_id, source_row_id, open_date, close_date,
                 exchange, contract, asset_type, open_side, close_side, quantity,
                 open_price, close_price, fact_close_pnl, matched_fee, is_current)
            VALUES (?, ?, ?, '20260701', '20260710', '上期所', 'rb2610',
                    'future', '买', '卖', 4, 3500, 3510, 400, 4, 1)
            """,
            (close_identity, batch_one, source_one),
        )
        conn.execute(
            """
            INSERT INTO trading_close_trade_links
                (close_identity_id, close_trade_identity_id, matched_quantity,
                 allocated_fee, rule_version)
            VALUES (?, ?, 4, 4, 'test-v1')
            """,
            (close_identity, basic_close_trade),
        )

        subject_id = conn.execute(
            "SELECT id FROM trading_business_subjects WHERE name = '上海钧能'"
        ).fetchone()["id"]
        for identity_id, business_type in (
            (basic_open, "basic_hedging"),
            (strategic_open, "strategic_hedging"),
            (second_basic_open, "basic_hedging"),
        ):
            conn.execute(
                """
                INSERT INTO trading_business_assignments
                    (trade_identity_id, business_subject_id, business_type,
                     assigned_by, updated_by)
                VALUES (?, ?, ?, 'tester', 'tester')
                """,
                (identity_id, subject_id, business_type),
            )
        conn.execute(
            """
            INSERT INTO trading_business_close_allocations
                (close_identity_id, open_trade_identity_id, matched_quantity,
                 source, business_pnl, rule_version)
            VALUES (?, ?, 4, 'manual_override', 300, 'business-manual-v1')
            """,
            (close_identity, basic_open),
        )

        def position(account_id, batch_id, source_id, key, snapshot_date,
                     contract, direction, quantity, margin):
            identity_id = _insert_identity(conn, account_id, "position", key)
            conn.execute(
                """
                INSERT INTO trading_position_snapshots
                    (identity_id, batch_id, source_row_id, snapshot_date, exchange,
                     contract, asset_type, direction, quantity, average_price,
                     margin, is_current)
                VALUES (?, ?, ?, ?, '上期所', ?, 'future', ?, ?, 3500, ?, 1)
                """,
                (
                    identity_id, batch_id, source_id, snapshot_date, contract,
                    direction, quantity, margin,
                ),
            )

        position(
            account_one, batch_one, source_one, "position-basic", "20260720",
            "rb2610", "买", 6, 600,
        )
        position(
            account_one, batch_one, source_one, "position-strategic", "20260720",
            "hc2610", "卖", 5, 1000,
        )
        position(
            account_one, batch_one, source_one, "position-unclassified", "20260720",
            "i2609", "买", 2, 200,
        )
        position(
            account_two, batch_two, source_two, "position-second", "20260721",
            "rb2610", "买", 3, 450,
        )

    return {
        "account_one": account_one,
        "account_two": account_two,
        "close_identity": close_identity,
        "unclassified_open": unclassified_open,
    }


def test_fact_overview_uses_current_facts_and_per_account_latest_snapshots(
    tmp_path, monkeypatch
):
    sample = _seed_overview_sample(tmp_path, monkeypatch)

    result = trading_overview.build_trading_overview(
        trading_overview.OverviewFilters(
            start_date="20260701", end_date="20260731"
        )
    )

    assert result["filters"] == {
        "account_id": None,
        "scope": "all",
        "scope_label": "全部",
        "pnl_metric": "fact_pnl",
        "start_date": "20260701",
        "end_date": "20260731",
    }
    assert result["trades"] == {
        "record_count": 5,
        "quantity": 24.0,
        "fee": 24.0,
    }
    assert result["pnl"] == {"value": 400.0, "metric": "fact_pnl"}
    assert result["daily_pnl"] == [{"date": "20260710", "value": 400.0}]
    assert result["positions"] == {
        "group_count": 4,
        "quantity": 16.0,
        "margin": 2250.0,
        "snapshot_status": "mixed",
        "snapshot_dates": ["20260720", "20260721"],
    }
    assert result["data_quality"]["unassigned_trade_count"] == 1
    assert result["data_quality"]["close_record_count"] == 1
    assert result["data_quality"]["unallocated_close_count"] == 0

    account_result = trading_overview.build_trading_overview(
        trading_overview.OverviewFilters(
            account_id=sample["account_one"],
            start_date="20260701",
            end_date="20260731",
        )
    )
    assert account_result["trades"]["record_count"] == 4
    assert account_result["trades"]["quantity"] == 21.0
    assert account_result["positions"]["snapshot_dates"] == ["20260720"]
    assert account_result["positions"]["margin"] == 1800.0


def test_fact_overview_distinguishes_missing_snapshot_from_real_zero(
    tmp_path, monkeypatch
):
    sample = _seed_overview_sample(tmp_path, monkeypatch)

    missing = trading_overview.build_trading_overview(
        trading_overview.OverviewFilters(
            account_id=sample["account_one"],
            start_date="20260701",
            end_date="20260710",
        )
    )

    assert missing["positions"]["snapshot_status"] == "missing"
    assert missing["positions"]["group_count"] is None
    assert missing["positions"]["quantity"] is None
    assert missing["positions"]["margin"] is None


def test_business_overview_uses_attributed_metrics_and_business_pnl(
    tmp_path, monkeypatch
):
    _seed_overview_sample(tmp_path, monkeypatch)

    basic = trading_overview.build_trading_overview(
        trading_overview.OverviewFilters(
            scope="basic_hedging",
            start_date="20260701",
            end_date="20260731",
        )
    )
    strategic = trading_overview.build_trading_overview(
        trading_overview.OverviewFilters(
            scope="strategic_hedging",
            start_date="20260701",
            end_date="20260731",
        )
    )

    assert basic["filters"]["pnl_metric"] == "business_pnl"
    assert basic["trades"] == {
        "record_count": 3,
        "quantity": 17.0,
        "fee": 17.0,
    }
    assert basic["pnl"] == {"value": 300.0, "metric": "business_pnl"}
    assert basic["daily_pnl"] == [{"date": "20260710", "value": 300.0}]
    assert basic["positions"]["group_count"] == 2
    assert basic["positions"]["quantity"] == 9.0
    assert basic["positions"]["margin"] == 1050.0

    assert strategic["trades"] == {
        "record_count": 1,
        "quantity": 5.0,
        "fee": 5.0,
    }
    assert strategic["pnl"] == {"value": 0.0, "metric": "business_pnl"}
    assert strategic["positions"]["group_count"] == 1
    assert strategic["positions"]["quantity"] == 5.0
    assert strategic["positions"]["margin"] == 1000.0


def test_business_snapshot_completeness_only_counts_accounts_in_selected_scope(
    tmp_path, monkeypatch
):
    _seed_overview_sample(tmp_path, monkeypatch)
    with db.connect() as conn:
        account_id = conn.execute(
            """
            INSERT INTO trading_accounts (account_code, display_name, masked_name)
            VALUES ('strategic-only', '仅战略账户', '仅战略账户')
            """
        ).lastrowid
        batch_id = conn.execute(
            "INSERT INTO trading_import_batches (account_id, status) VALUES (?, 'active')",
            (account_id,),
        ).lastrowid
        source_id = conn.execute(
            """
            INSERT INTO trading_source_rows
                (batch_id, source_type, source_file, source_sheet, source_row_no,
                 raw_hash, raw_json)
            VALUES (?, 'statement', 'strategic-only.txt', 'statement', 1,
                    'strategic-only', '{}')
            """,
            (batch_id,),
        ).lastrowid
        identity_id = _insert_identity(
            conn, account_id, "trade", "strategic-only-open"
        )
        conn.execute(
            """
            INSERT INTO trading_trade_facts
                (identity_id, batch_id, source_row_id, trade_date, exchange,
                 contract, asset_type, side, open_close, quantity, price, fee,
                 is_current)
            VALUES (?, ?, ?, '20260704', '上期所', 'ag2610', 'future',
                    '买', '开仓', 1, 8000, 1, 1)
            """,
            (identity_id, batch_id, source_id),
        )
        subject_id = conn.execute(
            "SELECT id FROM trading_business_subjects WHERE name = '上海钧能'"
        ).fetchone()["id"]
        conn.execute(
            """
            INSERT INTO trading_business_assignments
                (trade_identity_id, business_subject_id, business_type,
                 assigned_by, updated_by)
            VALUES (?, ?, 'strategic_hedging', 'tester', 'tester')
            """,
            (identity_id, subject_id),
        )

    basic = trading_overview.build_trading_overview(
        trading_overview.OverviewFilters(
            scope="basic_hedging", start_date="20260701", end_date="20260731"
        )
    )
    facts = trading_overview.build_trading_overview(
        trading_overview.OverviewFilters(
            start_date="20260701", end_date="20260731"
        )
    )

    assert basic["positions"]["snapshot_status"] == "mixed"
    assert basic["positions"]["quantity"] == 9.0
    assert basic["data_quality"]["missing_snapshot_account_count"] == 0
    assert facts["positions"]["snapshot_status"] == "partial"
    assert facts["data_quality"]["missing_snapshot_account_count"] == 1


def test_business_rematch_changes_business_pnl_without_changing_fact_pnl(
    tmp_path, monkeypatch
):
    sample = _seed_overview_sample(tmp_path, monkeypatch)
    filters = trading_overview.OverviewFilters(
        scope="basic_hedging", start_date="20260701", end_date="20260731"
    )
    fact_filters = trading_overview.OverviewFilters(
        start_date="20260701", end_date="20260731"
    )
    fact_before = trading_overview.build_trading_overview(fact_filters)

    with db.connect() as conn:
        conn.execute(
            """
            UPDATE trading_business_close_allocations
            SET business_pnl = 125
            WHERE close_identity_id = ?
            """,
            (sample["close_identity"],),
        )

    assert trading_overview.build_trading_overview(filters)["pnl"]["value"] == 125.0
    assert trading_overview.build_trading_overview(fact_filters)["pnl"] == fact_before["pnl"]


def test_fact_overview_reports_real_zero_from_an_existing_snapshot(
    tmp_path, monkeypatch
):
    sample = _seed_overview_sample(tmp_path, monkeypatch)
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE trading_position_snapshots
            SET quantity = 0, margin = 0
            WHERE batch_id IN (
                SELECT id FROM trading_import_batches WHERE account_id = ?
            )
            """,
            (sample["account_one"],),
        )

    result = trading_overview.build_trading_overview(
        trading_overview.OverviewFilters(
            account_id=sample["account_one"],
            start_date="20260701",
            end_date="20260731",
        )
    )

    assert result["positions"] == {
        "group_count": 0,
        "quantity": 0,
        "margin": 0.0,
        "snapshot_status": "ok",
        "snapshot_dates": ["20260720"],
    }


def test_business_trade_metrics_allocate_partial_close_quantity_and_fee(
    tmp_path, monkeypatch
):
    sample = _seed_overview_sample(tmp_path, monkeypatch)
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE trading_business_close_allocations
            SET matched_quantity = 2, business_pnl = 150
            WHERE close_identity_id = ?
            """,
            (sample["close_identity"],),
        )
        conn.execute(
            """
            UPDATE trading_position_snapshots
            SET quantity = 8, margin = 800
            WHERE contract = 'rb2610'
              AND batch_id IN (
                  SELECT id FROM trading_import_batches
                  WHERE account_id = ? AND status = 'active'
              )
            """,
            (sample["account_one"],),
        )

    result = trading_overview.build_trading_overview(
        trading_overview.OverviewFilters(
            scope="basic_hedging",
            start_date="20260701",
            end_date="20260731",
        )
    )

    assert result["trades"] == {
        "record_count": 3,
        "quantity": 15.0,
        "fee": 15.0,
    }
    assert result["pnl"]["value"] == 150.0
    assert result["positions"]["quantity"] == 11.0
    assert result["positions"]["margin"] == 1250.0


def test_overview_query_count_is_bounded(tmp_path, monkeypatch):
    _seed_overview_sample(tmp_path, monkeypatch)
    original_exec = db._exec
    calls = 0

    def counted_exec(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_exec(*args, **kwargs)

    monkeypatch.setattr(db, "_exec", counted_exec)
    result = trading_overview.build_trading_overview(
        trading_overview.OverviewFilters(
            scope="basic_hedging",
            start_date="20260701",
            end_date="20260731",
        )
    )

    assert calls <= 8
    assert "items" not in result
