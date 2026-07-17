import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import db


def use_temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "risk-alert-history.db")
    db.init_db()


def test_alert_schema_has_archival_column_and_history_index(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        cur = conn.cursor()
        columns = {
            row["name"]
            for row in db._exec(cur, "PRAGMA table_info(alert_settings)").fetchall()
        }
        indexes = {
            row["name"]
            for row in db._exec(cur, "PRAGMA index_list(alert_history)").fetchall()
        }

    assert "archived_at" in columns
    assert "idx_alert_history_alert_time_id" in indexes


def test_alert_schema_migration_is_idempotent(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        db.migrate_alert_schema(conn)
        db.migrate_alert_schema(conn)
        row = db._exec(
            conn.cursor(),
            "SELECT archived_at FROM alert_settings LIMIT 1",
        ).fetchone()

    assert row is None
