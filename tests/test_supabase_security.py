"""Supabase public-schema security hardening tests."""

import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import db


EXPECTED_LEGACY_PUBLIC_TABLES = (
    "users",
    "user_sessions",
    "module_permissions",
    "operation_logs",
    "operation_log_archives",
    "operation_log_archive_users",
    "order_finance_progress",
    "sh_junneng_trades",
    "sh_junneng_positions",
    "sh_junneng_close_trades",
    "strategy_groups",
    "strategy_positions",
    "alert_settings",
    "alert_history",
    "calculated_data",
    "daily_prices",
    "trading_days",
    "dv_week_keys",
    "dv_data_points",
    "dv_import_batches",
    "dv_change_log",
    "dv_integration_batches",
    "dv_integrated_points",
)


class RecordingCursor:
    def __init__(self):
        self.statements = []

    def execute(self, statement, params=None):
        self.statements.append((statement, params))


def test_legacy_public_tables_are_explicitly_protected():
    assert db.LEGACY_PUBLIC_TABLES == EXPECTED_LEGACY_PUBLIC_TABLES


def test_default_public_privileges_are_revoked_for_future_objects():
    cur = RecordingCursor()

    db._secure_postgres_default_privileges(cur)

    statements = [" ".join(statement.split()) for statement, _ in cur.statements]
    assert any(
        statement.startswith("ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public")
        and "REVOKE ALL ON TABLES FROM anon, authenticated" in statement
        for statement in statements
    )
    assert any(
        statement.startswith("ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public")
        and "REVOKE ALL ON SEQUENCES FROM anon, authenticated" in statement
        for statement in statements
    )
    assert any(
        statement.startswith("ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public")
        and "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC, anon, authenticated" in statement
        for statement in statements
    )
    assert any(
        statement.startswith("ALTER DEFAULT PRIVILEGES FOR ROLE postgres ")
        and "IN SCHEMA public" not in statement
        and "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC, anon, authenticated" in statement
        for statement in statements
    )


def test_security_helper_allows_legacy_tables_without_identity_sequences():
    cur = RecordingCursor()

    db._secure_postgres_tables(
        cur,
        ("users", "trading_days"),
        sequence_tables=("users",),
    )

    statements = [" ".join(statement.split()) for statement, _ in cur.statements]
    assert "ALTER TABLE users ENABLE ROW LEVEL SECURITY" in statements
    assert "ALTER TABLE trading_days ENABLE ROW LEVEL SECURITY" in statements
    assert (
        "REVOKE ALL ON TABLE users, trading_days FROM anon, authenticated"
        in statements
    )
    assert (
        "REVOKE ALL ON SEQUENCE users_id_seq FROM anon, authenticated"
        in statements
    )


def test_closing_review_agent_tables_are_explicitly_protected():
    cur = RecordingCursor()

    db._secure_postgres_tables(cur, db.CLOSING_REVIEW_AGENT_TABLES)

    statements = [" ".join(statement.split()) for statement, _ in cur.statements]
    for table in db.CLOSING_REVIEW_AGENT_TABLES:
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in statements
    assert (
        "REVOKE ALL ON TABLE closing_review_conversations, closing_review_messages, closing_review_tasks FROM anon, authenticated"
        in statements
    )
    assert (
        "REVOKE ALL ON SEQUENCE closing_review_conversations_id_seq, closing_review_messages_id_seq, closing_review_tasks_id_seq FROM anon, authenticated"
        in statements
    )
