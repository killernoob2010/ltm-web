import pytest

from app import db
from app.trading_effective_facts import EffectiveFactFilters, query_effective_trades, query_effective_positions
from test_trading_collector_reconciliation import account_id, insert_batch, insert_settlement_trade, insert_wh6_fill


@pytest.fixture
def two_accounts():
    db.init_db()
    own = account_id()
    with db.connect() as conn:
        other = conn.execute("INSERT INTO trading_accounts(account_code, display_name) VALUES ('other', 'Synthetic other')").lastrowid
    for account, quantity in ((own, 2), (other, 30)):
        batch = insert_batch(account, "2026-05-10", "2026-05-10", "active", "daily")
        insert_settlement_trade(account, batch, identity_key=f"scope-{account}", quantity=quantity)
        insert_wh6_fill(account, trade_date="2026-05-11", quantity=quantity, event_key=f"wh-{account}")
    return own, other


@pytest.mark.parametrize("classification,expected", [("", 4), ("unclassified", 2)])
def test_foreign_account_excluded_from_rows_and_totals(two_accounts, classification, expected):
    own, _ = two_accounts
    with db.connect() as conn:
        result = query_effective_trades(conn.cursor(), EffectiveFactFilters(account_ids=(own,), classification=classification))
    assert result["summary"]["quantity"] == expected
    assert all(row["quantity"] == 2 for row in result["items"])
    assert result["total_items"] == (2 if not classification else 1)


def test_empty_scope_is_rejected_before_query():
    with pytest.raises(PermissionError):
        EffectiveFactFilters(account_ids=())


def test_current_positions_do_not_merge_foreign_contract(two_accounts):
    from test_trading_effective_facts import _insert_wh6_snapshot
    own, other = two_accounts
    for account, quantity in ((own, 2), (other, 30)):
        _insert_wh6_snapshot(account, rows=[dict(contract="i2609", asset_type="future", exchange="DCE", direction="买", quantity=quantity, average_price=700)])
    with db.connect() as conn:
        result = query_effective_positions(conn.cursor(), EffectiveFactFilters(account_ids=(own,)), include_all_items=True)
    assert sum(row["quantity"] for row in result["all_items"]) == 2


def test_historical_settlement_positions_are_scoped(two_accounts):
    from test_trading_effective_facts import _insert_settlement_position_batch, _baseline_row
    own, other = two_accounts
    for account, quantity in ((own, 2), (other, 30)):
        _insert_settlement_position_batch(account, "20260831", rows=[_baseline_row("i2609", "买", quantity, 700)])
    with db.connect() as conn:
        result = query_effective_positions(conn.cursor(), EffectiveFactFilters(account_ids=(own,), end_date="2026-08-31"), include_all_items=True)
    assert sum(row["quantity"] for row in result["all_items"]) == 2


def test_legacy_unscoped_call_retains_all_accounts(two_accounts):
    with db.connect() as conn:
        result = query_effective_trades(conn.cursor(), EffectiveFactFilters())
    assert result["summary"]["quantity"] == 64
