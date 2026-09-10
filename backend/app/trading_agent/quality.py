"""Read-only runtime and evaluation reporting for the Agent admin page.

This module deliberately keeps runtime facts, human feedback, and evaluation
definitions separate.  A successful worker run is not treated as a passed
evaluation, and a checked-in case definition is not treated as a live-model
result.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from .. import db
from .tools import agent_module_catalog


QUALITY_VERSION = "agent-quality-v1"
FEEDBACK_OPERATION = "Agent质量反馈"
FEEDBACK_ENTITY = "agent_quality_feedback"
FEEDBACK_LABELS = {"correct", "incorrect", "needs_review"}
MAX_TEXT = 4000
MAX_NOTE = 1000
ROOT_DIR = Path(__file__).resolve().parents[3]
EVAL_DIR = ROOT_DIR / "evals" / "trading_agent_v2"

_MODULE_TOOLS = {
    "trading": {
        "query_trade_facts", "query_close_facts", "query_positions", "summarize_positions",
        "summarize_facts", "get_position_risk", "run_scenario",
    },
    "data_visualization": {
        "describe_dataset", "query_dataset", "summarize_dataset", "compare_dataset", "relate_datasets",
    },
    "information_warning": {"search_public", "read_public"},
}


def _seconds(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat(timespec="seconds")
    return str(value).split(".", 1)[0]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat(timespec="seconds")


def _table_exists(name: str) -> bool:
    with db.connect() as conn:
        if db._is_pg():
            row = db._exec(conn.cursor(), "SELECT to_regclass(?) AS name", (f"public.{name}",)).fetchone()
        else:
            row = db._exec(
                conn.cursor(),
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            ).fetchone()
    return bool(row and row["name"])


def _parse_date(value: str | None) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError("日期格式应为 YYYY-MM-DD") from exc


def _date_window(start_date: str | None, end_date: str | None) -> tuple[str | None, str | None, dict[str, str | None]]:
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    if start and end and start > end:
        raise ValueError("开始日期不能晚于结束日期")
    start_bound = datetime.combine(start, time.min, tzinfo=timezone.utc).isoformat(timespec="seconds") if start else None
    end_bound = datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc).isoformat(timespec="seconds") if end else None
    return start_bound, end_bound, {
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
    }


def _module_for_tool(tool_name: str | None) -> str | None:
    for module, names in _MODULE_TOOLS.items():
        if tool_name in names:
            return module
    return None


def _module_codes(task_id: int) -> list[str]:
    if not _table_exists("agent_v2_events"):
        return []
    with db.connect() as conn:
        rows = db._exec(
            conn.cursor(),
            "SELECT tool_name FROM agent_v2_events WHERE task_id=? AND tool_name IS NOT NULL",
            (task_id,),
        ).fetchall()
    return sorted({module for module in (_module_for_tool(row["tool_name"]) for row in rows) if module})


def _latest_feedback(task_ids: set[int] | None = None) -> dict[int, dict[str, Any]]:
    if not _table_exists("operation_logs"):
        return {}
    params: list[Any] = [FEEDBACK_ENTITY]
    suffix = ""
    if task_ids:
        placeholders = ",".join("?" for _ in task_ids)
        suffix = f" AND entity_id IN ({placeholders})"
        params.extend(sorted(task_ids))
    with db.connect() as conn:
        rows = db._exec(
            conn.cursor(),
            """SELECT id,user_id,entity_id,description,created_at
               FROM operation_logs
               WHERE entity_type=?""" + suffix + " ORDER BY id DESC",
            tuple(params),
        ).fetchall()
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        task_id = int(row["entity_id"])
        if task_id in result:
            continue
        try:
            payload = json.loads(row["description"] or "{}")
        except (TypeError, ValueError):
            payload = {"label": "needs_review", "note": str(row["description"] or "")}
        result[task_id] = {
            "label": payload.get("label", "needs_review"),
            "note": str(payload.get("note", ""))[:MAX_NOTE],
            "evaluator_version": str(payload.get("evaluator_version", "unknown"))[:80],
            "evaluator_id": row["user_id"],
            "created_at": _seconds(row["created_at"]),
        }
    return result


def _run_quality(state: str, feedback: dict[str, Any] | None) -> str:
    if feedback:
        return {
            "correct": "human_pass",
            "incorrect": "human_fail",
            "needs_review": "human_review",
        }.get(feedback.get("label"), "human_review")
    if state in {"succeeded", "partial", "failed", "cancelled"}:
        return "not_evaluated"
    return "pending"


def _run_item(row: dict[str, Any], feedback: dict[str, Any] | None = None) -> dict[str, Any]:
    task_id = int(row["task_id"])
    state = str(row.get("state") or row.get("task_state") or "unknown")
    return {
        "task_id": task_id,
        "channel": row.get("channel"),
        "state": state,
        "delivery_state": row.get("delivery_state"),
        "created_at": _seconds(row.get("created_at")),
        "finished_at": _seconds(row.get("finished_at")),
        "model_calls": int(row.get("model_calls") or 0),
        "tool_calls": int(row.get("tool_calls") or 0),
        "search_calls": int(row.get("search_calls") or 0),
        "last_error": str(row.get("last_error") or "")[:240] or None,
        "modules": _module_codes(task_id),
        "quality_status": _run_quality(state, feedback),
        "feedback": feedback,
    }


def _run_query(start_date: str | None = None, end_date: str | None = None, module: str | None = None):
    start_bound, end_bound, window = _date_window(start_date, end_date)
    if not _table_exists("agent_v2_runs"):
        return [], window
    clauses = ["1=1"]
    params: list[Any] = []
    if start_bound:
        clauses.append("r.created_at >= ?")
        params.append(start_bound)
    if end_bound:
        clauses.append("r.created_at < ?")
        params.append(end_bound)
    if module in _MODULE_TOOLS:
        if not _table_exists("agent_v2_events"):
            return [], window
        names = sorted(_MODULE_TOOLS[module])
        placeholders = ",".join("?" for _ in names)
        clauses.append(
            f"EXISTS (SELECT 1 FROM agent_v2_events me WHERE me.task_id=r.task_id "
            f"AND me.tool_name IN ({placeholders}))"
        )
        params.extend(names)
    sql = """SELECT r.*
             FROM agent_v2_runs r
             WHERE """ + " AND ".join(clauses) + " ORDER BY r.created_at DESC,r.task_id DESC"
    with db.connect() as conn:
        rows = db._exec(conn.cursor(), sql, tuple(params)).fetchall()
    return [dict(row) for row in rows], window


def list_runs(*, page: int = 1, page_size: int = 20, start_date: str | None = None,
              end_date: str | None = None, state: str | None = None, module: str | None = None) -> dict[str, Any]:
    safe_page = max(1, int(page or 1))
    safe_size = max(1, min(int(page_size or 20), 100))
    rows, window = _run_query(start_date, end_date, module)
    if state:
        rows = [row for row in rows if str(row.get("state")) == state]
    feedback = _latest_feedback({int(row["task_id"]) for row in rows})
    total = len(rows)
    offset = (safe_page - 1) * safe_size
    items = [_run_item(row, feedback.get(int(row["task_id"]))) for row in rows[offset:offset + safe_size]]
    return {
        "version": QUALITY_VERSION,
        "window": window,
        "items": items,
        "pagination": {"page": safe_page, "page_size": safe_size, "total": total},
    }


def _runtime_rows(start_date: str | None = None, end_date: str | None = None) -> tuple[list[dict[str, Any]], dict[str, str | None]]:
    return _run_query(start_date, end_date)[:2]


def build_summary(start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
    rows, window = _runtime_rows(start_date, end_date)
    task_ids = {int(row["task_id"]) for row in rows}
    feedback = _latest_feedback(task_ids)
    state_counts: dict[str, int] = {}
    for row in rows:
        state = str(row.get("state") or "unknown")
        state_counts[state] = state_counts.get(state, 0) + 1
    terminal_count = sum(state_counts.get(state, 0) for state in {"succeeded", "partial", "failed", "cancelled"})
    feedback_counts = {label: 0 for label in sorted(FEEDBACK_LABELS)}
    for item in feedback.values():
        if item.get("label") in feedback_counts:
            feedback_counts[item["label"]] += 1
    successful = state_counts.get("succeeded", 0)
    technical_rate = round(successful / terminal_count, 4) if terminal_count else None
    evaluation = evaluation_catalog()
    return {
        "version": QUALITY_VERSION,
        "generated_at": _now(),
        "window": window,
        "runtime": {
            "run_count": len(rows),
            "state_counts": state_counts,
            "terminal_count": terminal_count,
            "technical_success_rate": technical_rate,
            "model_calls": sum(int(row.get("model_calls") or 0) for row in rows),
            "tool_calls": sum(int(row.get("tool_calls") or 0) for row in rows),
            "search_calls": sum(int(row.get("search_calls") or 0) for row in rows),
        },
        "quality": {
            "evaluated_count": len(feedback),
            "passed_count": feedback_counts["correct"],
            "failed_count": feedback_counts["incorrect"],
            "needs_review_count": feedback_counts["needs_review"],
            "not_evaluated_count": max(0, terminal_count - len(feedback)),
        },
        "evaluation": evaluation,
        "modules": agent_module_catalog(),
        "coverage": {
            "runtime_source": "agent_v2_runs / agent_v2_events",
            "quality_source": "operation_logs human feedback",
            "evaluation_source": "checked-in regression, holdout, and offline definitions",
            "real_model_evaluated": bool(evaluation["live"].get("real_model_evaluated")),
        },
    }


def _read_json_lines(name: str) -> list[dict[str, Any]]:
    path = EVAL_DIR / name
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError, json.JSONDecodeError):
        return []


def evaluation_catalog() -> dict[str, Any]:
    regression = _read_json_lines("cases.jsonl")
    holdout = _read_json_lines("holdout.jsonl")
    try:
        manifest = json.loads((EVAL_DIR / "offline_behavior_manifest.json").read_text(encoding="utf-8"))
        offline_count = len(manifest.get("cases", []))
    except (OSError, ValueError, json.JSONDecodeError):
        offline_count = 0
    return {
        "real_model_evaluated": False,
        "release_readiness": "not_evaluated",
        "definition": {
            "regression": {"count": len(regression), "status": "available", "executed": False},
            "holdout": {"count": len(holdout), "status": "available", "executed": False},
        },
        "offline_behavior": {
            "count": offline_count,
            "status": "available" if offline_count else "unavailable",
            "executed": False,
            "real_model_evaluated": False,
            "release_readiness": "not_evaluated",
        },
        "live": {
            "status": "not_recorded",
            "executed": False,
            "real_model_evaluated": False,
            "release_readiness": "not_evaluated",
        },
    }


def evaluation_batch(batch_id: str) -> dict[str, Any]:
    batch_id = str(batch_id or "").strip()
    sources = {
        "definition-regression": ("definition", "cases.jsonl"),
        "definition-holdout": ("definition", "holdout.jsonl"),
        "offline-behavior": ("offline_behavior", "offline_behavior_manifest.json"),
    }
    if batch_id == "live":
        return {
            "id": "live",
            "kind": "live",
            "status": "not_recorded",
            "real_model_evaluated": False,
            "cases": [],
        }
    if batch_id not in sources:
        raise ValueError("评估批次不存在")
    kind, filename = sources[batch_id]
    if filename.endswith(".jsonl"):
        cases = _read_json_lines(filename)
    else:
        try:
            manifest = json.loads((EVAL_DIR / filename).read_text(encoding="utf-8"))
            cases = manifest.get("cases", [])
        except (OSError, ValueError, json.JSONDecodeError):
            cases = []
    return {
        "id": batch_id,
        "kind": kind,
        "status": "definition_available" if kind == "definition" else "offline_available",
        "executed": False,
        "real_model_evaluated": False,
        "cases": [
            {
                "id": item.get("id"),
                "question": str(item.get("question", ""))[:240],
                "capabilities": item.get("capabilities", []),
                "status": "not_run",
            }
            for item in cases
            if isinstance(item, dict)
        ],
    }


def record_feedback(*, evaluator_id: int, task_id: int, label: str, note: str = "",
                    evaluator_version: str = QUALITY_VERSION) -> dict[str, Any]:
    task_id = int(task_id)
    label = str(label or "").strip()
    note = str(note or "").strip()[:MAX_NOTE]
    evaluator_version = str(evaluator_version or QUALITY_VERSION).strip()[:80]
    if label not in FEEDBACK_LABELS:
        raise ValueError("反馈标签无效")
    if not _table_exists("agent_v2_runs"):
        raise ValueError("Agent运行记录尚未初始化")
    with db.connect() as conn:
        row = db._exec(conn.cursor(), "SELECT task_id FROM agent_v2_runs WHERE task_id=?", (task_id,)).fetchone()
    if not row:
        raise ValueError("Agent任务不存在")
    description = json.dumps(
        {"label": label, "note": note, "evaluator_version": evaluator_version},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    db.log_operation(
        evaluator_id,
        "agent_quality",
        FEEDBACK_OPERATION,
        description,
        FEEDBACK_ENTITY,
        task_id,
    )
    feedback = _latest_feedback({task_id}).get(task_id)
    return feedback or {
        "label": label,
        "note": note,
        "evaluator_version": evaluator_version,
        "evaluator_id": evaluator_id,
        "created_at": _now(),
    }


def _preview_payload(raw: Any) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    allowed = {"schema_version", "status", "delivery_status", "body_markdown", "plain_text", "limitations", "missing"}
    result = {key: payload[key] for key in allowed if key in payload}
    for key in ("body_markdown", "plain_text"):
        if key in result:
            result[key] = str(result[key])[:MAX_TEXT]
    for key in ("limitations", "missing"):
        if isinstance(result.get(key), list):
            result[key] = result[key][:20]
    return result


def get_run_detail(task_id: int) -> dict[str, Any] | None:
    task_id = int(task_id)
    if not _table_exists("agent_v2_runs"):
        return None
    with db.connect() as conn:
        row = db._exec(conn.cursor(), "SELECT * FROM agent_v2_runs WHERE task_id=?", (task_id,)).fetchone()
        messages = db._exec(
            conn.cursor(),
            "SELECT role,content,structured_payload,created_at FROM closing_review_messages WHERE task_id=? ORDER BY id ASC",
            (task_id,),
        ).fetchall() if _table_exists("closing_review_messages") else []
        events = db._exec(
            conn.cursor(),
            """SELECT seq,kind,tool_name,status,error_code,duration_seconds,created_at
               FROM agent_v2_events WHERE task_id=? ORDER BY seq ASC""",
            (task_id,),
        ).fetchall() if _table_exists("agent_v2_events") else []
    if not row:
        return None
    feedback = _latest_feedback({task_id}).get(task_id)
    item = _run_item(dict(row), feedback)
    question = None
    answer = None
    payload = None
    for message in messages:
        if message["role"] == "user" and question is None:
            question = str(message["content"] or "")[:MAX_TEXT]
        if message["role"] == "assistant":
            answer = str(message["content"] or "")[:MAX_TEXT]
            payload = _preview_payload(message["structured_payload"])
    item.update({
        "question": question,
        "answer": answer,
        "answer_payload": payload,
        "events": [
            {
                "seq": int(event["seq"]),
                "kind": event["kind"],
                "tool_name": event["tool_name"],
                "status": event["status"],
                "error_code": event["error_code"],
                "duration_seconds": event["duration_seconds"],
                "created_at": _seconds(event["created_at"]),
            }
            for event in events
        ],
    })
    return item


__all__ = [
    "QUALITY_VERSION", "FEEDBACK_LABELS", "build_summary", "evaluation_catalog", "evaluation_batch",
    "get_run_detail", "list_runs", "record_feedback",
]
