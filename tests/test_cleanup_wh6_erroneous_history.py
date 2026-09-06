"""Guard rails for the one-time Staging cleanup of invalid WH6 history."""

import json
from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).parent))

from app import db
from test_trading_collector_reconciliation import account_id, use_temp_db
from scripts import cleanup_wh6_erroneous_history as cleanup


def _seed_candidate(tmp_path, monkeypatch, *, observation_count=1000, issue_count=0):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(
            cur,
            """
            INSERT INTO trading_collector_devices
                (account_id, environment, device_name, client_version, fingerprint, token_hash, status)
            VALUES (?, 'staging', 'cleanup-test-device', '0.3.2', 'cleanup-fingerprint', 'cleanup-token', 'active')
            """,
            (account,),
        )
        device_id = db.last_insert_id(conn)
        fills = []
        observations = []
        reconciliations = []
        for index in range(1, 1001):
            event_key = f"tradeid:cleanup-{index:04d}"
            fills.append(
                (
                    account,
                    event_key,
                    "2026-06-16" if index % 2 else "20260812",
                    "09:31:02",
                    "2026-06-16T09:31:02+08:00",
                    "DCE",
                    "i2609",
                    "i2609",
                    "future",
                    "买",
                    "开",
                    1,
                    "785",
                    "tradeid-" + str(index),
                    "order-" + str(index),
                    "wh6-match-v1",
                    "settlement_conflict",
                    "monthly_unmatched",
                    "a" * 64,
                    "Record/match.dat",
                    index,
                    "b" * 64,
                )
            )
        db._executemany(
            cur,
            """
            INSERT INTO trading_intraday_fills
                (account_id, source_event_key, trade_date, trade_time, trade_timestamp,
                 exchange, contract, raw_contract, asset_type, side, open_close, quantity,
                 price, trade_id, order_id, parser_version, data_status,
                 reconciliation_status, source_record_sha256, source_path, source_record_index,
                 canonical_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            fills,
        )
        for index in range(1, observation_count + 1):
            event_key = f"tradeid:cleanup-{index:04d}"
            observations.append(
                (
                    device_id,
                    account,
                    event_key,
                    "obs-" + str(index),
                    "{}",
                    "accepted",
                    "2026-06-16T09:31:02+08:00",
                )
            )
        db._executemany(
            cur,
            """
            INSERT INTO trading_intraday_fill_observations
                (device_id, account_id, source_event_key, observation_hash,
                 payload_json, status, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            observations,
        )
        for index in range(1, 1001):
            reconciliations.append(
                (
                    index,
                    account,
                    "monthly",
                    200,
                    "monthly_unmatched",
                    "{}",
                    "{}",
                    "{}",
                )
            )
        db._executemany(
            cur,
            """
            INSERT INTO trading_intraday_fill_reconciliations
                (intraday_fill_id, account_id, authority_type, source_priority,
                 result_status, resolved_fields_json, field_sources_json, differences_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            reconciliations,
        )
        if issue_count:
            db._exec(
                cur,
                """
                INSERT INTO trading_collector_issues
                    (device_id, account_id, issue_code, source_event_key, message, payload_json)
                VALUES (?, ?, 'cleanup-test', ?, 'test issue', '{}')
                """,
                (device_id, account, "tradeid:cleanup-0001"),
            )
        db._exec(
            cur,
            """
            INSERT INTO trading_intraday_fills
                (account_id, source_event_key, trade_date, trade_time, trade_timestamp,
                 exchange, contract, raw_contract, asset_type, side, open_close, quantity,
                 price, trade_id, order_id, parser_version, data_status,
                 reconciliation_status, source_record_sha256, source_path, source_record_index,
                 canonical_hash)
            VALUES (?, 'tradeid:valid-1001', '2026-09-06', '09:31:02',
                    '2026-09-06T09:31:02+08:00', 'DCE', 'i2701', 'i2701', 'future',
                    '买', '开', 1, '800', 'valid-1001', 'order-valid-1001',
                    'wh6-match-v2', 'provisional', 'unmatched', ?, 'Record/match.dat',
                    1001, ?)
            """,
            (account, "d" * 64, "e" * 64),
        )
        conn.commit()
    return account


def _counts(account):
    with db.connect() as conn:
        return {
            "fills": conn.execute(
                "SELECT COUNT(*) AS c FROM trading_intraday_fills WHERE account_id = ?", (account,)
            ).fetchone()["c"],
            "observations": conn.execute(
                "SELECT COUNT(*) AS c FROM trading_intraday_fill_observations WHERE account_id = ?",
                (account,),
            ).fetchone()["c"],
            "reconciliations": conn.execute(
                "SELECT COUNT(*) AS c FROM trading_intraday_fill_reconciliations WHERE account_id = ?",
                (account,),
            ).fetchone()["c"],
            "issues": conn.execute(
                "SELECT COUNT(*) AS c FROM trading_collector_issues WHERE account_id = ?", (account,)
            ).fetchone()["c"],
        }


def test_dry_run_reports_exact_candidate_and_does_not_write(tmp_path, monkeypatch):
    account = _seed_candidate(tmp_path, monkeypatch)
    before = _counts(account)

    result = cleanup.run_cleanup(
        environment="staging",
        account_code="hongyuan_futures",
        expected_count=1000,
        apply=False,
    )

    assert result["status"] == "dry_run"
    assert result["candidate"] == {
        "fills": 1000,
        "observations": 1000,
        "reconciliations": 1000,
        "issues": 0,
        "min_id": 1,
        "max_id": 1000,
    }
    assert _counts(account) == before


@pytest.mark.parametrize(
    "seed_kwargs, mutation",
    [
        ({"observation_count": 999}, None),
        ({}, "parser"),
        ({}, "issue"),
    ],
)
def test_any_candidate_or_dependency_mismatch_stops_without_deleting(
    tmp_path, monkeypatch, seed_kwargs, mutation
):
    account = _seed_candidate(tmp_path, monkeypatch, **seed_kwargs)
    if mutation == "parser":
        with db.connect() as conn:
            conn.execute(
                "UPDATE trading_intraday_fills SET parser_version = 'wh6-match-v2' WHERE id = 1"
            )
            conn.commit()
    if mutation == "issue":
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO trading_collector_issues (account_id, issue_code, source_event_key, message) "
                "VALUES (?, 'cleanup-test', 'tradeid:cleanup-0001', 'test issue')",
                (account,),
            )
            conn.commit()

    before = _counts(account)
    with pytest.raises(cleanup.CleanupSafetyError):
        cleanup.run_cleanup(
            environment="staging",
            account_code="hongyuan_futures",
            expected_count=1000,
            apply=True,
            backup_schema="codex_backup_20260906_wh6_erroneous_history",
        )
    assert _counts(account) == before


def test_apply_deletes_only_locked_candidate_in_dependency_order_and_is_idempotent(tmp_path, monkeypatch):
    account = _seed_candidate(tmp_path, monkeypatch)

    result = cleanup.run_cleanup(
        environment="staging",
        account_code="hongyuan_futures",
        expected_count=1000,
        apply=True,
        backup_schema="codex_backup_20260906_wh6_erroneous_history",
    )

    assert result["status"] == "applied"
    assert result["deleted"] == {"fills": 1000, "observations": 1000, "reconciliations": 1000}
    assert _counts(account) == {"fills": 1, "observations": 0, "reconciliations": 0, "issues": 0}
    with db.connect() as conn:
        log = conn.execute(
            "SELECT operation_type, description FROM operation_logs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert log["operation_type"] == "cleanup_wh6_erroneous_history"
    assert "codex_backup_20260906_wh6_erroneous_history" in log["description"]
    assert "DATABASE_URL" not in log["description"]

    repeat = cleanup.run_cleanup(
        environment="staging",
        account_code="hongyuan_futures",
        expected_count=1000,
        apply=True,
        backup_schema="codex_backup_20260906_wh6_erroneous_history",
    )
    assert repeat["status"] == "already_clean"


def test_production_environment_is_rejected_before_database_access():
    with pytest.raises(cleanup.CleanupSafetyError, match="staging"):
        cleanup.run_cleanup(
            environment="production",
            account_code="hongyuan_futures",
            expected_count=1000,
            apply=True,
        )


def test_apply_requires_a_private_backup_schema_name(tmp_path, monkeypatch):
    _seed_candidate(tmp_path, monkeypatch)
    with pytest.raises(cleanup.CleanupSafetyError, match="backup-schema"):
        cleanup.run_cleanup(
            environment="staging",
            account_code="hongyuan_futures",
            expected_count=1000,
            apply=True,
        )
