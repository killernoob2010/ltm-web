"""Server-owned progress events and their read-only task projection."""
from __future__ import annotations

from datetime import datetime, timezone
from math import floor
from typing import Any

from . import store


STAGES = ("queued", "internal_data", "quotes", "search", "source_read", "composing", "validation", "finished")
STAGE_SET = set(STAGES)
STAGE_STATUSES = {"running", "complete", "failed"}
TERMINAL_STATES = {"succeeded", "partial", "failed", "cancelled"}
STAGE_SUMMARIES = {
    "queued": "已排队等待处理",
    "internal_data": "已读取交易事实",
    "quotes": "正在核对行情",
    "search": "正在检索公开资料",
    "source_read": "正在读取公开来源",
    "composing": "正在整理回答",
    "validation": "正在校验回答",
    "finished": "处理完成",
}


def _datetime(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return fallback
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def _seconds(value: Any) -> str:
    parsed = _datetime(value, datetime.now(timezone.utc))
    return parsed.isoformat(timespec="seconds")


def record_stage(principal, stage: str, status: str, *, store_api=store) -> None:
    if stage not in STAGE_SET:
        raise ValueError("阶段无效")
    if status not in STAGE_STATUSES:
        raise ValueError("阶段状态无效")
    if stage == "queued":
        # Queue state is projected from task.created_at and does not need a
        # write before a worker owns the task.
        raise ValueError("排队阶段由任务状态推导")
    if stage == "finished":
        raise ValueError("完成阶段由终态推导")
    store_api.append_event(principal, f"stage:{stage}", status=status)


def project_progress(task: dict, events: list[dict], now: datetime) -> dict:
    created_at = _datetime(task.get("created_at"), now)
    finished_at = task.get("finished_at")
    end_at = _datetime(finished_at, now) if finished_at else now
    if end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=timezone.utc)
    elapsed_seconds = max(0, floor((end_at - created_at).total_seconds()))

    safe_events = []
    for raw in events or []:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "")
        stage = kind.removeprefix("stage:") if kind.startswith("stage:") else ""
        status = raw.get("status")
        try:
            sequence = int(raw.get("seq", raw.get("sequence")))
        except (TypeError, ValueError):
            continue
        if stage not in STAGE_SET or stage in {"queued", "finished"} or status not in STAGE_STATUSES:
            continue
        safe_events.append({"sequence": sequence, "stage": stage, "stage_status": status,
                            "at": _seconds(raw.get("created_at"))})
    safe_events.sort(key=lambda item: item["sequence"])
    history = safe_events[-30:]
    terminal = task.get("state") in TERMINAL_STATES
    if terminal:
        stage = "finished"
        stage_status = "failed" if task.get("state") == "failed" else "complete"
        sequence = (max((item["sequence"] for item in safe_events), default=0) + 1)
        summary = "处理未完成" if stage_status == "failed" else STAGE_SUMMARIES[stage]
        updated_at = _seconds(finished_at or task.get("created_at"))
    elif safe_events:
        latest = safe_events[-1]
        stage = latest["stage"]
        stage_status = latest["stage_status"]
        sequence = latest["sequence"]
        summary = STAGE_SUMMARIES[stage]
        updated_at = latest["at"]
    else:
        stage = "queued"
        stage_status = "running"
        sequence = 0
        summary = STAGE_SUMMARIES[stage]
        updated_at = _seconds(task.get("created_at"))
    return {
        "sequence": sequence,
        "stage": stage,
        "stage_status": stage_status,
        "updated_at": updated_at,
        "elapsed_seconds": elapsed_seconds,
        "summary": summary,
        "history": history,
        "terminal": terminal,
    }


__all__ = ["STAGES", "STAGE_STATUSES", "project_progress", "record_stage"]
