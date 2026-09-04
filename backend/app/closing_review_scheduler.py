"""Deterministic 15:05 closing-review results and Staging replay."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import os
import threading
import time as time_module
from typing import Any, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from . import closing_review_agent as agent
from . import closing_review_agent_store as store
from . import closing_review_calendar as calendar


SHANGHAI = timezone(timedelta(hours=8))
AUTO_TIME = time(15, 5)
WORKFLOW_VERSION = "closing-review-agent-v1"
build_option_daily_review = agent.build_option_daily_review


class RunSummary(BaseModel):
    status: str = "ok"
    planned_at: Optional[str] = None
    actual_started_at: Optional[str] = None
    target_date: Optional[str] = None
    calendar_version: str = calendar.CALENDAR_VERSION
    users_considered: int = 0
    tasks_created: int = 0
    messages_created: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)


_scheduler_lock = threading.Lock()
_scheduler_started = False
_run_lock = threading.Lock()
_retention_lock = threading.Lock()
_retention_last_run_date: Optional[date] = None


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _local_now(value: Optional[datetime] = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(SHANGHAI)


def _seconds(value: datetime) -> str:
    return _local_now(value).isoformat(timespec="seconds")


def _authorized_users() -> list[dict[str, Any]]:
    with store.db.connect() as conn:
        rows = store.db._exec(
            conn.cursor(),
            """
            SELECT DISTINCT u.*
            FROM users u
            WHERE u.status = '启用'
              AND u.role NOT IN ('访客', 'guest')
              AND (
                    u.role IN ('管理员', 'admin')
                    OR (
                        EXISTS (
                            SELECT 1 FROM module_permissions p1
                            WHERE p1.user_id = u.id
                              AND p1.module_code = 'closing_review_agent'
                              AND p1.can_view = 1
                        )
                        AND EXISTS (
                            SELECT 1 FROM module_permissions p2
                            WHERE p2.user_id = u.id
                              AND p2.module_code = 'trading_options'
                              AND p2.can_view = 1
                        )
                    )
              )
            ORDER BY u.id
            """,
        ).fetchall()
    return [dict(row) for row in rows]


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None
    return value


def _source_signature(report: agent.OptionDailyReviewResponse) -> str:
    refs = sorted(
        (item.ref, item.source, item.locator)
        for item in report.metadata.evidence_refs
    )
    material = {
        "trading_date": report.trading_date,
        "status": report.status,
        "source": report.metadata.source,
        "freshness": report.metadata.freshness,
        "calculation_version": report.metadata.calculation_version,
        "rule_version": report.metadata.rule_version,
        "evidence_refs": refs,
    }
    digest = hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"review-source:{digest[:32]}"


def _client_request_id(target_date: str, source_signature: str, calculation_version: str) -> str:
    return f"auto:{target_date}:{calculation_version}:{source_signature}"


def _previous_automatic_message(
    user_id: int,
    conversation_id: int,
    target_date: str,
) -> Optional[dict[str, Any]]:
    messages = store.list_messages(user_id, conversation_id, limit=200)
    matches: list[dict[str, Any]] = []
    for message in messages:
        if message.get("message_type") != "automatic_result":
            continue
        payload = _json_value(message.get("structured_payload"))
        if isinstance(payload, dict) and payload.get("trading_date") == target_date:
            matches.append(message)
    return matches[-1] if matches else None


def _create_automatic_for_user(
    user: dict[str, Any],
    report: agent.OptionDailyReviewResponse,
    *,
    planned_at: str,
) -> tuple[int, int]:
    user_id = int(user["id"])
    target_date = report.trading_date
    source_signature = _source_signature(report)
    calculation_version = report.metadata.calculation_version
    client_request_id = _client_request_id(target_date, source_signature, calculation_version)
    conversation = store.get_or_create_daily_conversation(user_id)
    task, created = store.claim_user_task(
        user_id,
        int(conversation["id"]),
        client_request_id,
        task_kind="automatic",
        task_profile="automatic_daily_review",
        target_date=target_date,
        state="processing",
        source_signature=source_signature,
    )
    if not created:
        return 0, 0

    try:
        projection = agent.build_automatic_result(report)
        agent.validate_completion(projection, report)
        previous = _previous_automatic_message(user_id, int(conversation["id"]), target_date)
        payload = projection.model_dump(mode="json")
        payload["scheduled_at"] = planned_at
        payload["generated_at"] = _seconds(datetime.now(timezone.utc))
        content = agent.render_answer(projection, updated=previous is not None)
        message = store.append_message(
            user_id,
            int(conversation["id"]),
            role="assistant",
            message_type="automatic_result",
            content=content,
            structured_payload=payload,
            status="active",
            task_id=int(task["id"]),
            supersedes_message_id=int(previous["id"]) if previous else None,
        )
        task = store.finish_task(
            int(task["id"]),
            user_id=user_id,
            state="succeeded",
            task_profile="automatic_daily_review",
            target_date=target_date,
            validated_intent={
                "task_profile": "automatic_daily_review",
                "target_date": target_date,
            },
            result_projection=payload,
            workflow_version=WORKFLOW_VERSION,
            calculation_version=report.metadata.calculation_version,
            rule_version=report.metadata.rule_version,
            source_signature=source_signature,
        )
        return 1, 1
    except Exception:
        store.finish_task(
            int(task["id"]),
            user_id=user_id,
            state="failed",
            error_category="automatic_result_failed",
            workflow_version=WORKFLOW_VERSION,
            source_signature=source_signature,
        )
        raise


def _run_retention_once(now: datetime) -> None:
    global _retention_last_run_date
    local_date = _local_now(now).date()
    with _retention_lock:
        if _retention_last_run_date == local_date:
            return
        _retention_last_run_date = local_date
    store.redact_expired_content(now)


def _summary_for(now: datetime, target_date: Optional[date] = None) -> RunSummary:
    local = _local_now(now)
    planned = datetime.combine(local.date(), AUTO_TIME, tzinfo=SHANGHAI)
    return RunSummary(
        planned_at=_seconds(planned),
        actual_started_at=_seconds(local),
        target_date=target_date.strftime("%Y%m%d") if target_date else None,
    )


def run_due_reviews(now: datetime) -> RunSummary:
    """Run the due task after 15:05 for the previous actual trading day."""

    local = _local_now(now)
    _run_retention_once(local)
    try:
        if not calendar.is_trading_day(local.date()):
            return RunSummary(status="non_trading_day", actual_started_at=_seconds(local))
        if local.time().replace(tzinfo=None) < AUTO_TIME:
            return RunSummary(status="not_due", actual_started_at=_seconds(local))
        target = calendar.resolve_previous_trading_day(local.date())
    except calendar.CalendarUnavailable:
        return RunSummary(status="calendar_unavailable", actual_started_at=_seconds(local))

    with _run_lock:
        summary = _summary_for(local, target)
        try:
            report = build_option_daily_review(target.strftime("%Y%m%d"))
        except Exception as exc:
            summary.status = "failed"
            summary.errors.append(type(exc).__name__)
            return summary
        users = _authorized_users()
        summary.users_considered = len(users)
        for user in users:
            try:
                tasks, messages = _create_automatic_for_user(user, report, planned_at=summary.planned_at or _seconds(local))
                summary.tasks_created += tasks
                summary.messages_created += messages
                if not tasks:
                    summary.skipped += 1
            except Exception as exc:
                summary.errors.append(f"user:{user.get('id')}:{type(exc).__name__}")
        if summary.errors:
            summary.status = "partial_failure"
        return summary


def run_historical_replay(user: dict[str, Any], trading_date: date) -> RunSummary:
    """Replay the same deterministic automatic flow for one historical date."""

    if not _enabled("CLOSING_REVIEW_AGENT_REPLAY_ENABLED"):
        return RunSummary(status="disabled")
    if user.get("role") not in {"管理员", "admin"}:
        raise HTTPException(status_code=403, detail="仅管理员可执行此操作")
    try:
        if not calendar.is_trading_day(trading_date):
            return RunSummary(
                status="non_trading_day",
                target_date=trading_date.strftime("%Y%m%d"),
            )
    except calendar.CalendarUnavailable:
        return RunSummary(
            status="calendar_unavailable",
            target_date=trading_date.strftime("%Y%m%d"),
        )

    now = _local_now()
    with _run_lock:
        summary = _summary_for(now, trading_date)
        try:
            report = build_option_daily_review(trading_date.strftime("%Y%m%d"))
        except Exception as exc:
            summary.status = "failed"
            summary.errors.append(type(exc).__name__)
            return summary
        users = _authorized_users()
        summary.users_considered = len(users)
        for target_user in users:
            try:
                tasks, messages = _create_automatic_for_user(
                    target_user,
                    report,
                    planned_at=summary.planned_at or _seconds(now),
                )
                summary.tasks_created += tasks
                summary.messages_created += messages
                if not tasks:
                    summary.skipped += 1
            except Exception as exc:
                summary.errors.append(f"user:{target_user.get('id')}:{type(exc).__name__}")
        if summary.errors:
            summary.status = "partial_failure"
        return summary


def _scheduler_loop(interval_seconds: int) -> None:
    while True:
        try:
            run_due_reviews(datetime.now(timezone.utc))
        except Exception:
            pass
        time_module.sleep(interval_seconds)


def start_closing_review_scheduler(interval_seconds: int = 60) -> bool:
    global _scheduler_started
    if not agent.is_enabled() or not _enabled("CLOSING_REVIEW_AGENT_AUTO_ENABLED"):
        return False
    with _scheduler_lock:
        if _scheduler_started:
            return False
        thread = threading.Thread(
            target=_scheduler_loop,
            args=(max(30, int(interval_seconds)),),
            daemon=True,
            name="closing-review-agent",
        )
        thread.start()
        _scheduler_started = True
    return True
