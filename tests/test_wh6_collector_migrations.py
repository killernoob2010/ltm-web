"""V2.1 local SQLite backup, key rewrite and terminal-state migration tests."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

import pytest


sys.path.insert(0, str(Path(__file__).parents[1] / "collector"))
sys.path.insert(0, str(Path(__file__).parent))

from test_wh6_collector_core import _account
from wh6_collector.cli import CollectorConfig
from wh6_collector.migrations import canonical_fill_event_key, migrate_local_store


def seed_outbox(path, rows):
    connection = sqlite3.connect(str(path))
    connection.executescript(
        """
        CREATE TABLE outbox (
            event_key TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            item_type TEXT NOT NULL DEFAULT 'fill',
            priority TEXT NOT NULL DEFAULT 'history',
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            available_at TEXT NOT NULL,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE file_checkpoints (
            source_path TEXT PRIMARY KEY,
            checkpoint_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            message TEXT NOT NULL,
            path TEXT,
            record_index INTEGER,
            file_sha256 TEXT,
            severity TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    for row in rows:
        connection.execute(
            """
            INSERT INTO outbox
                (event_key, payload_json, item_type, priority, status, attempts,
                 available_at, last_error, created_at, updated_at)
            VALUES (?, ?, 'fill', 'history', ?, ?, ?, ?, ?, ?)
            """,
            row,
        )
    connection.commit()
    connection.close()


def fill_payload(trade_id="000123", trade_date="2026-09-04", exchange="DCE"):
    return {
        "source_event_key": "legacy",
        "trade_date": trade_date,
        "exchange": exchange,
        "trade_id": trade_id,
        "contract": "i2609-c-750",
        "asset_type": "option",
        "side": "买",
        "open_close": "开",
        "quantity": 1,
        "price": "785",
    }


def read_outbox(path):
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    rows = [dict(row) for row in connection.execute("SELECT * FROM outbox ORDER BY event_key")]
    connection.close()
    return rows


def test_loaded_config_uses_runtime_version_not_persisted_v1(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "staging_url": "http://127.0.0.1:8000",
                "source_path": "C:/WH6/Record",
                "account": _account().to_payload(),
                "device_token": "device-token",
                "data_dir": str(tmp_path),
                "client_version": "0.3.0",
            }
        ),
        encoding="utf-8",
    )

    config = CollectorConfig.load(path)

    assert config.client_version == "0.3.0"


def test_canonical_event_key_normalizes_date_exchange_and_leading_zero_id():
    assert canonical_fill_event_key(fill_payload()) == "tradeid:2026-09-04:dce:123"


def test_v1_and_v2_aliases_merge_without_losing_terminal_state(tmp_path):
    path = tmp_path / "collector.sqlite3"
    payload = json.dumps(fill_payload(), ensure_ascii=False)
    seed_outbox(
        path,
        [
            (
                "tradeid:123",
                payload,
                "acked",
                4,
                "2026-09-04T01:00:00+00:00",
                "old error",
                "2026-09-04T00:00:00+00:00",
                "2026-09-04T01:00:00+00:00",
            ),
            (
                "tradeid:2026-09-04:dce:000123",
                payload,
                "pending",
                2,
                "2026-09-04T01:00:00+00:00",
                None,
                "2026-09-04T00:30:00+00:00",
                "2026-09-04T00:30:00+00:00",
            ),
        ],
    )

    result = migrate_local_store(path, policy=None)
    rows = read_outbox(path)

    assert result.duplicates_merged == 1
    assert result.keys_rewritten >= 1
    assert len(rows) == 1
    assert rows[0]["event_key"] == "tradeid:2026-09-04:dce:123"
    assert rows[0]["status"] == "acked"
    assert rows[0]["attempts"] == 4
    assert rows[0]["last_error"] == "old error"
    assert Path(result.backup_path).is_file()
    with sqlite3.connect(str(result.backup_path)) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_migration_rewrites_six_aliases_and_releases_claims(tmp_path):
    path = tmp_path / "collector.sqlite3"
    payload = json.dumps(fill_payload(), ensure_ascii=False)
    aliases = [
        "tradeid:123",
        "tradeid:000123",
        "tradeid:20260904:DCE:000123",
        "tradeid:2026-09-04:dce:000123",
        "tradeid:2026-09-04:DCE:123",
        "legacy-event-key",
    ]
    rows = [
        (
            alias,
            payload,
            "claimed" if index == 5 else "pending",
            0,
            "2026-09-04T01:00:00+00:00",
            None,
            "2026-09-04T00:00:00+00:00",
            "2026-09-04T00:00:00+00:00",
        )
        for index, alias in enumerate(aliases)
    ]
    seed_outbox(path, rows)

    result = migrate_local_store(path, policy=None)

    assert result.duplicates_merged == 5
    assert result.claims_released == 1
    assert read_outbox(path)[0]["status"] == "pending"


def test_migration_is_idempotent_after_schema_version_three(tmp_path):
    path = tmp_path / "collector.sqlite3"
    seed_outbox(
        path,
        [
            (
                "tradeid:123",
                json.dumps(fill_payload()),
                "pending",
                0,
                "2026-09-04T01:00:00+00:00",
                None,
                "2026-09-04T00:00:00+00:00",
                "2026-09-04T00:00:00+00:00",
            )
        ],
    )

    first = migrate_local_store(path, policy=None)
    second = migrate_local_store(path, policy=None)

    assert first.new_version == 4
    assert second.old_version == 4
    assert second.keys_rewritten == 0
    assert second.duplicates_merged == 0
    assert second.claims_released == 0
    assert second.backup_path is None


def test_failed_migration_rolls_back_original_database(tmp_path):
    path = tmp_path / "collector.sqlite3"
    seed_outbox(
        path,
        [
            (
                "tradeid:123",
                json.dumps(fill_payload()),
                "pending",
                0,
                "2026-09-04T01:00:00+00:00",
                None,
                "2026-09-04T00:00:00+00:00",
                "2026-09-04T00:00:00+00:00",
            )
        ],
    )

    class BrokenPolicy:
        def covers(self, _trade_date):
            raise RuntimeError("policy failure")

    with pytest.raises(RuntimeError):
        migrate_local_store(path, policy=BrokenPolicy())

    assert read_outbox(path)[0]["event_key"] == "tradeid:123"
    assert read_outbox(path)[0]["status"] == "pending"
