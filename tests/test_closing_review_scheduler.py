"""Tests for the versioned trading calendar and deterministic scheduler."""

import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import closing_review_agent as agent  # noqa: E402
from app import closing_review_calendar as calendar  # noqa: E402
from app import closing_review_scheduler as scheduler  # noqa: E402
from app import db, main  # noqa: E402
from test_closing_review_agent import full_report  # noqa: E402


SHANGHAI = timezone(timedelta(hours=8))


def _use_temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("CLOSING_REVIEW_AGENT_ENABLED", "true")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "closing-review-scheduler.db")
    db.init_db()


def _insert_user(name, username, role="用户"):
    with db.connect() as conn:
        cur = conn.cursor()
        user_id = db._last_insert_id(
            cur,
            """
            INSERT INTO users (name, username, department, password_hash, role)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, username, "期货组", db.password_hash("password"), role),
        )
    return int(user_id)


def _grant(user_id, module_code):
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(
            cur,
            """
            INSERT OR REPLACE INTO module_permissions
                (user_id, module_code, can_view, can_edit, can_sensitive)
            VALUES (?, ?, 1, 0, 0)
            """,
            (user_id, module_code),
        )


def _user(user_id):
    with db.connect() as conn:
        return dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def _authorized_user(name):
    user_id = _insert_user(name, name.lower())
    _grant(user_id, "closing_review_agent")
    _grant(user_id, "trading_options")
    return _user(user_id)


def _report_for(source_ref="statement:a", status="complete"):
    report = full_report().model_copy(update={"trading_date": "20260618", "status": status})
    metadata = report.metadata.model_copy(
        update={
            "evidence_refs": [
                report.metadata.evidence_refs[0].model_copy(update={"ref": source_ref})
            ],
        }
    )
    return report.model_copy(update={"metadata": metadata})


def _report_builder(monkeypatch, report):
    monkeypatch.setattr(scheduler, "build_option_daily_review", lambda _date: report)


def test_calendar_resolves_weekend_holiday_and_year_boundary():
    assert calendar.resolve_previous_trading_day(date(2026, 6, 22)) == date(2026, 6, 18)
    assert calendar.resolve_previous_trading_day(date(2026, 6, 20)) == date(2026, 6, 18)
    assert calendar.resolve_previous_trading_day(date(2026, 1, 1)) == date(2025, 12, 31)
    assert calendar.is_trading_day(date(2026, 6, 18)) is True
    assert calendar.is_trading_day(date(2026, 6, 19)) is False
    assert calendar.is_trading_day(date(2026, 6, 21)) is False


def test_calendar_does_not_guess_outside_versioned_source():
    with pytest.raises(calendar.CalendarUnavailable):
        calendar.resolve_previous_trading_day(date(2024, 1, 2))


def test_due_before_1505_creates_no_task(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    _authorized_user("Before1505")
    _report_builder(monkeypatch, _report_for())

    result = scheduler.run_due_reviews(datetime(2026, 6, 22, 15, 4, tzinfo=SHANGHAI))

    assert result.tasks_created == 0
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM closing_review_tasks").fetchone()[0] == 0


def test_exact_1505_creates_previous_actual_day_and_is_idempotent(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    _authorized_user("At1505")
    _report_builder(monkeypatch, _report_for())
    monkeypatch.setattr(
        agent,
        "build_closing_review_provider",
        lambda: pytest.fail("automatic result must not call model"),
    )

    first = scheduler.run_due_reviews(datetime(2026, 6, 22, 15, 5, tzinfo=SHANGHAI))
    second = scheduler.run_due_reviews(datetime(2026, 6, 22, 15, 5, tzinfo=SHANGHAI))

    assert first.tasks_created >= 1
    assert second.tasks_created == 0
    with db.connect() as conn:
        task = conn.execute("SELECT target_date, task_kind, state FROM closing_review_tasks WHERE task_kind = 'automatic'").fetchone()
        message = conn.execute(
            "SELECT message_type, content FROM closing_review_messages WHERE message_type = 'automatic_result'"
        ).fetchone()
    assert task["target_date"] == "20260618"
    assert task["state"] == "succeeded"
    assert message["message_type"] == "automatic_result"


def test_wake_catch_up_and_weekend_holiday_skip(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    _authorized_user("Wake")
    _report_builder(monkeypatch, _report_for())

    before = scheduler.run_due_reviews(datetime(2026, 6, 22, 14, 0, tzinfo=SHANGHAI))
    wake = scheduler.run_due_reviews(datetime(2026, 6, 22, 18, 0, tzinfo=SHANGHAI))
    holiday = scheduler.run_due_reviews(datetime(2026, 6, 19, 15, 5, tzinfo=SHANGHAI))

    assert before.tasks_created == 0
    assert wake.tasks_created >= 1
    assert holiday.tasks_created == 0


def test_missing_data_is_saved_as_waiting_for_data(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    _authorized_user("Waiting")
    _report_builder(monkeypatch, _report_for(status="waiting_for_data"))

    result = scheduler.run_due_reviews(datetime(2026, 6, 22, 15, 5, tzinfo=SHANGHAI))

    assert result.tasks_created >= 1
    with db.connect() as conn:
        payload = conn.execute(
            "SELECT structured_payload FROM closing_review_messages WHERE message_type = 'automatic_result'"
        ).fetchone()[0]
    assert '"status":"waiting_for_data"' in payload


def test_source_change_supersedes_old_automatic_message_but_same_source_does_not_duplicate(
    tmp_path, monkeypatch
):
    _use_temp_db(tmp_path, monkeypatch)
    _authorized_user("Supersession")
    first_report = _report_for("statement:a")
    second_report = _report_for("statement:b")
    reports = iter([first_report, second_report, second_report])
    monkeypatch.setattr(scheduler, "build_option_daily_review", lambda _date: next(reports))

    first = scheduler.run_due_reviews(datetime(2026, 6, 22, 15, 5, tzinfo=SHANGHAI))
    changed = scheduler.run_due_reviews(datetime(2026, 6, 23, 15, 5, tzinfo=SHANGHAI))
    unchanged = scheduler.run_due_reviews(datetime(2026, 6, 24, 15, 5, tzinfo=SHANGHAI))

    assert first.tasks_created >= 1
    assert changed.tasks_created >= 1
    assert unchanged.tasks_created == 0
    with db.connect() as conn:
        messages = conn.execute(
            "SELECT supersedes_message_id FROM closing_review_messages WHERE message_type = 'automatic_result' ORDER BY id"
        ).fetchall()
    assert len(messages) >= 2
    assert messages[-1]["supersedes_message_id"] is not None


def test_only_users_with_both_permissions_receive_automatic_results(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    authorized = _authorized_user("Allowed")
    agent_only_id = _insert_user("AgentOnly", "agent-only")
    _grant(agent_only_id, "closing_review_agent")
    _report_builder(monkeypatch, _report_for())

    scheduler.run_due_reviews(datetime(2026, 6, 22, 15, 5, tzinfo=SHANGHAI))

    with db.connect() as conn:
        allowed_count = conn.execute(
            "SELECT COUNT(*) FROM closing_review_tasks WHERE user_id = ? AND task_kind = 'automatic'",
            (authorized["id"],),
        ).fetchone()[0]
        blocked_count = conn.execute(
            "SELECT COUNT(*) FROM closing_review_tasks WHERE user_id = ? AND task_kind = 'automatic'",
            (agent_only_id,),
        ).fetchone()[0]
    assert allowed_count == 1
    assert blocked_count == 0


def test_retention_cleanup_runs_once_per_calendar_day(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(scheduler.store, "redact_expired_content", lambda now: calls.append(now) or {})
    scheduler._retention_last_run_date = None

    scheduler.run_due_reviews(datetime(2026, 6, 22, 10, 0, tzinfo=SHANGHAI))
    scheduler.run_due_reviews(datetime(2026, 6, 22, 12, 0, tzinfo=SHANGHAI))
    scheduler.run_due_reviews(datetime(2026, 6, 23, 10, 0, tzinfo=SHANGHAI))

    assert len(calls) == 2


def test_replay_is_disabled_or_admin_only(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    admin = _user(1)
    ordinary = _authorized_user("ReplayUser")
    monkeypatch.delenv("CLOSING_REVIEW_AGENT_REPLAY_ENABLED", raising=False)

    disabled = scheduler.run_historical_replay(admin, date(2026, 6, 18))
    assert disabled.status == "disabled"
    monkeypatch.setenv("CLOSING_REVIEW_AGENT_REPLAY_ENABLED", "true")
    with pytest.raises(HTTPException) as caught:
        scheduler.run_historical_replay(ordinary, date(2026, 6, 18))
    assert caught.value.status_code == 403


def test_replay_uses_automatic_path_without_model_and_is_idempotent(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    admin = _user(1)
    authorized = _authorized_user("ReplayAllowed")
    monkeypatch.setenv("CLOSING_REVIEW_AGENT_REPLAY_ENABLED", "true")
    _report_builder(monkeypatch, _report_for())
    monkeypatch.setattr(
        agent,
        "build_closing_review_provider",
        lambda: pytest.fail("replay must not call model"),
    )

    first = scheduler.run_historical_replay(admin, date(2026, 6, 18))
    second = scheduler.run_historical_replay(admin, date(2026, 6, 18))

    assert first.tasks_created >= 1
    assert second.tasks_created == 0
    with db.connect() as conn:
        task_count = conn.execute(
            "SELECT COUNT(*) FROM closing_review_tasks WHERE user_id = ? AND task_kind = 'automatic'",
            (authorized["id"],),
        ).fetchone()[0]
        message_type = conn.execute(
            "SELECT message_type FROM closing_review_messages WHERE message_type = 'automatic_result'"
        ).fetchone()[0]
    assert task_count == 1
    assert message_type == "automatic_result"


def test_replay_api_returns_404_when_disabled_and_403_for_non_admin(tmp_path, monkeypatch):
    _use_temp_db(tmp_path, monkeypatch)
    admin = _user(1)
    ordinary = _authorized_user("ReplayAPIUser")
    admin_token = db.create_session(admin["id"])
    ordinary_token = db.create_session(ordinary["id"])

    with TestClient(main.app) as client:
        disabled = client.post(
            "/api/closing-review-agent/admin/replay",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"trading_date": "2026-06-18"},
        )
        monkeypatch.setenv("CLOSING_REVIEW_AGENT_REPLAY_ENABLED", "true")
        forbidden = client.post(
            "/api/closing-review-agent/admin/replay",
            headers={"Authorization": f"Bearer {ordinary_token}"},
            json={"trading_date": "2026-06-18"},
        )

    assert disabled.status_code == 404
    assert forbidden.status_code == 403


def test_master_switch_prevents_scheduler_start(monkeypatch):
    starts = []

    class FakeThread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            starts.append(True)

    monkeypatch.setenv("CLOSING_REVIEW_AGENT_ENABLED", "false")
    monkeypatch.setenv("CLOSING_REVIEW_AGENT_AUTO_ENABLED", "true")
    monkeypatch.setattr(scheduler, "_scheduler_started", False)
    monkeypatch.setattr(scheduler.threading, "Thread", FakeThread)

    assert scheduler.start_closing_review_scheduler() is False
    assert starts == []
