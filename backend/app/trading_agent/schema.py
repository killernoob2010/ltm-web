"""Explicit adjunct schema migration; never called from a request or import."""
from .. import db

TABLES = {
    "agent_v2_runs": """
        task_id INTEGER PRIMARY KEY REFERENCES closing_review_tasks(id),
        execution_id TEXT NOT NULL UNIQUE, user_id INTEGER NOT NULL,
        channel TEXT NOT NULL, account_scope_json TEXT NOT NULL,
        request_hash TEXT NOT NULL, state TEXT NOT NULL,
        lease_owner TEXT, lease_expires_at TEXT, deadline_at TEXT,
        model_calls INTEGER NOT NULL DEFAULT 0, tool_calls INTEGER NOT NULL DEFAULT 0,
        search_calls INTEGER NOT NULL DEFAULT 0, last_error TEXT,
        delivery_state TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, finished_at TEXT
    """,
    "agent_v2_results": """
        id TEXT PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES closing_review_tasks(id),
        user_id INTEGER NOT NULL, conversation_id INTEGER NOT NULL,
        kind TEXT NOT NULL, parent_ref TEXT REFERENCES agent_v2_results(id), snapshot_ref TEXT,
        payload_json TEXT NOT NULL, source_hash TEXT NOT NULL,
        schema_version TEXT NOT NULL, calculation_version TEXT NOT NULL,
        created_at TEXT NOT NULL, expires_at TEXT NOT NULL
    """,
    "agent_v2_events": """
        id TEXT PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES closing_review_tasks(id),
        seq INTEGER NOT NULL, kind TEXT NOT NULL, tool_name TEXT, argument_hash TEXT,
        result_ref TEXT, duration_seconds REAL, status TEXT, error_code TEXT,
        created_at TEXT NOT NULL, UNIQUE(task_id, seq)
    """,
    "agent_v2_wecom_bindings": """
        bot_id TEXT NOT NULL, wecom_user_id TEXT NOT NULL, user_id INTEGER NOT NULL,
        status TEXT NOT NULL, created_at TEXT NOT NULL, revoked_at TEXT,
        PRIMARY KEY(bot_id, wecom_user_id), UNIQUE(bot_id, user_id)
    """,
    "agent_v2_pair_codes": """
        code_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, consumed_at TEXT
    """,
    "agent_v2_execution_grants": """
        token_hash TEXT PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES closing_review_tasks(id),
        user_id INTEGER NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT
    """,
    "agent_v2_evaluation_batches": """
        id TEXT PRIMARY KEY, suite TEXT NOT NULL, execution_source TEXT NOT NULL,
        environment TEXT NOT NULL, status TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
        total_count INTEGER NOT NULL DEFAULT 0, executed_count INTEGER NOT NULL DEFAULT 0,
        passed_count INTEGER NOT NULL DEFAULT 0, failed_count INTEGER NOT NULL DEFAULT 0,
        blocked_count INTEGER NOT NULL DEFAULT 0, not_run_count INTEGER NOT NULL DEFAULT 0,
        metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, finished_at TEXT
    """,
    "agent_v2_evaluation_cases": """
        batch_id TEXT NOT NULL REFERENCES agent_v2_evaluation_batches(id),
        case_id TEXT NOT NULL, question TEXT, capabilities_json TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL, score_json TEXT NOT NULL DEFAULT '{}',
        evidence_json TEXT NOT NULL DEFAULT '{}', task_id INTEGER, started_at TEXT, finished_at TEXT,
        PRIMARY KEY(batch_id, case_id)
    """,
    "agent_v2_evaluation_reviews": """
        id TEXT PRIMARY KEY, batch_id TEXT REFERENCES agent_v2_evaluation_batches(id),
        case_id TEXT, task_id INTEGER, reviewer_type TEXT NOT NULL,
        reviewer_id INTEGER, label TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
        evaluator_version TEXT NOT NULL, created_at TEXT NOT NULL
    """,
}


def statements(postgres: bool = False) -> list[str]:
    sql = [f"CREATE TABLE IF NOT EXISTS {name} ({columns})" for name, columns in TABLES.items()]
    for name, table, columns in (
        ("runs_queue", "runs", "state, created_at"),
        ("results_owner", "results", "user_id, conversation_id, created_at"),
        ("events_sequence", "events", "task_id, seq"),
        ("evaluation_cases_status", "evaluation_cases", "batch_id, status"),
        ("evaluation_batches_source", "evaluation_batches", "execution_source, created_at"),
        ("evaluation_reviews_task", "evaluation_reviews", "task_id, created_at"),
    ):
        sql.append(f"CREATE INDEX IF NOT EXISTS idx_agent_v2_{name} ON agent_v2_{table} ({columns})")
    if postgres:
        for name in TABLES:
            sql.extend([
                f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY",
                f"REVOKE ALL ON TABLE {name} FROM PUBLIC, anon, authenticated",
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {name} TO CURRENT_USER",
            ])
    return sql


def migrate_agent_v2_schema(conn) -> None:
    cur = conn.cursor()
    for sql in statements(db._is_pg()):
        db._exec(cur, sql)
