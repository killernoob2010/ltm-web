from datetime import datetime, timezone


def test_stage_events_query_survives_postgres_parameter_binding(monkeypatch):
    """Literal SQL percent signs must not collide with psycopg placeholders."""
    import sqlite3
    from contextlib import contextmanager
    from app.trading_agent import store

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE agent_v2_events (task_id, seq, kind, status, created_at)")
    conn.executemany("INSERT INTO agent_v2_events VALUES (?,?,?,?,?)", [
        (7, 1, "stage:internal_data", "complete", "2026-09-10T08:00:00Z"),
        (7, 2, "tool", "complete", "2026-09-10T08:00:01Z"),
        (8, 3, "stage:validation", "complete", "2026-09-10T08:00:02Z"),
        (7, 4, "stage:validation", "complete", "2026-09-10T08:00:03Z"),
    ])

    class Cursor:
        def execute(self, sql, params):
            # Exercise db._exec's PostgreSQL path and percent-style binding;
            # SQLite then evaluates the actual predicate, ordering and limit.
            encoded = tuple("'" + value.replace("'", "''") + "'" if isinstance(value, str)
                            else str(value) for value in params)
            self.result = conn.execute(sql % encoded)

        def fetchall(self):
            return self.result.fetchall()

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def connect():
        yield Connection()

    monkeypatch.setattr(store.db, "connect", connect)
    monkeypatch.setattr(store.db, "_is_pg", lambda: True)
    assert [row["seq"] for row in store.stage_events(7)] == [1, 4]
    assert [row["seq"] for row in store.stage_events(7, limit=1)] == [4]
    conn.close()


def test_progress_projects_queue_and_elapsed_seconds():
    from app.trading_agent.progress import project_progress

    created = "2026-09-10T15:00:00+00:00"
    projected = project_progress(
        {"state": "queued", "created_at": created, "finished_at": None},
        [],
        datetime(2026, 9, 10, 15, 0, 8, tzinfo=timezone.utc),
    )

    assert projected["sequence"] == 0
    assert projected["stage"] == "queued"
    assert projected["stage_status"] == "running"
    assert projected["elapsed_seconds"] == 8
    assert projected["history"] == []


def test_terminal_progress_does_not_regress_and_keeps_recent_safe_history():
    from app.trading_agent.progress import project_progress

    projected = project_progress(
        {
            "state": "succeeded",
            "created_at": "2026-09-10T15:00:00+00:00",
            "finished_at": "2026-09-10T15:00:12+00:00",
        },
        [
            {"seq": 2, "kind": "stage:internal_data", "status": "complete", "created_at": "2026-09-10T15:00:03+00:00"},
            {"seq": 4, "kind": "stage:validation", "status": "complete", "created_at": "2026-09-10T15:00:10+00:00", "argument_hash": "must-not-escape"},
        ],
        datetime(2026, 9, 10, 15, 1, tzinfo=timezone.utc),
    )

    assert projected["terminal"] is True
    assert projected["sequence"] == 5
    assert projected["stage"] == "finished"
    assert projected["stage_status"] == "complete"
    assert projected["elapsed_seconds"] == 12
    assert projected["history"][-1] == {
        "sequence": 4,
        "stage": "validation",
        "stage_status": "complete",
        "at": "2026-09-10T15:00:10+00:00",
    }
    assert "argument_hash" not in str(projected)


def test_record_stage_rejects_unknown_stage_or_status():
    from app.trading_agent.progress import record_stage

    class Principal:
        pass

    class Store:
        pass

    # Validation happens before the storage boundary, so malformed progress
    # cannot become an arbitrary event kind.
    for stage, status in (("model", "running"), ("quotes", "unknown")):
        try:
            record_stage(Principal(), stage, status, store_api=Store())
        except ValueError:
            continue
        raise AssertionError("invalid progress input was accepted")
