"""Safety contracts for the WH6 reconciliation command."""

import hashlib
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))

from app import db
from scripts.reconcile_wh6_intraday import run_command


def _configure_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "db")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "db" / "app.db")
    db.init_db()


def _account_id():
    with db.connect() as conn:
        return conn.execute(
            "SELECT id FROM trading_accounts WHERE account_code = 'hongyuan_futures'"
        ).fetchone()["id"]


def _database_digest():
    with db.connect() as conn:
        rows = []
        for table in (
            "trading_intraday_fills",
            "trading_intraday_fill_reconciliations",
            "trading_collector_issues",
        ):
            rows.append(
                (
                    table,
                    [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()],
                )
            )
    return hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()


def _seed_fill(account_id):
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO trading_intraday_fills
                (account_id, source_event_key, trade_date, trade_time, trade_timestamp,
                 exchange, contract, raw_contract, asset_type, side, open_close, quantity,
                 price, parser_version, source_record_sha256, canonical_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                "tradeid:2026-09-04:dce:123",
                "2026-09-04",
                "09:31:02",
                "2026-09-04T09:31:02+08:00",
                "DCE",
                "i2607-c-750",
                "i2607-C-750",
                "option",
                "买",
                "开",
                1,
                "12.5",
                "wh6-match-v1",
                "a" * 64,
                "b" * 64,
            ),
        )


def _seed_duplicate_current_audits(account_id):
    _seed_fill(account_id)
    with db.connect() as conn:
        fill_id = conn.execute(
            "SELECT id FROM trading_intraday_fills WHERE account_id = ?",
            (account_id,),
        ).fetchone()["id"]
        for status in ("unmatched", "matched_daily"):
            conn.execute(
                """
                INSERT INTO trading_intraday_fill_reconciliations
                    (intraday_fill_id, account_id, authority_type, source_priority,
                     result_status, resolved_fields_json, field_sources_json,
                     differences_json, is_current)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (fill_id, account_id, "wh6", 0, status, "{}", "{}", "[]"),
            )


def test_reconcile_command_defaults_to_dry_run_without_persistent_writes(tmp_path, monkeypatch):
    _configure_db(tmp_path, monkeypatch)
    _seed_fill(_account_id())
    before = _database_digest()
    result = run_command(["--environment", "staging", "--account-code", "hongyuan_futures"])
    assert result["mode"] == "dry-run"
    assert result["environment"] == "staging"
    assert result["rollback_anchor"]
    assert _database_digest() == before


def test_production_apply_requires_explicit_confirmation(tmp_path, monkeypatch):
    _configure_db(tmp_path, monkeypatch)
    result = run_command(
        ["--environment", "production", "--account-code", "hongyuan_futures", "--apply"]
    )
    assert result.exit_code == 2
    assert result["state"] == "blocked"


def test_staging_apply_is_idempotent_and_reports_reconciliation_counts(tmp_path, monkeypatch):
    _configure_db(tmp_path, monkeypatch)
    _seed_fill(_account_id())
    first = run_command(
        ["--environment", "staging", "--account-code", "hongyuan_futures", "--apply"]
    )
    with db.connect() as conn:
        first_audits = conn.execute(
            "SELECT COUNT(*) AS count FROM trading_intraday_fill_reconciliations"
        ).fetchone()["count"]
    second = run_command(
        ["--environment", "staging", "--account-code", "hongyuan_futures", "--apply"]
    )
    with db.connect() as conn:
        second_audits = conn.execute(
            "SELECT COUNT(*) AS count FROM trading_intraday_fill_reconciliations"
        ).fetchone()["count"]
    assert first["mode"] == "apply"
    assert first["scanned"] == 1
    assert second["scanned"] == 1
    assert first_audits == second_audits


def test_reconcile_rejects_multiple_current_audits_before_apply(tmp_path, monkeypatch):
    _configure_db(tmp_path, monkeypatch)
    account_id = _account_id()
    _seed_duplicate_current_audits(account_id)
    result = run_command(
        ["--environment", "staging", "--account-code", "hongyuan_futures", "--apply"]
    )
    assert result.exit_code == 2
    assert result["state"] == "reconciliation_error"
    assert "multiple current" in result["message"]
