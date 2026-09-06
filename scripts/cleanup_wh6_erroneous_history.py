#!/usr/bin/env python3
"""Guarded, one-time cleanup for the locked Staging WH6 history candidate."""

from __future__ import annotations

from datetime import datetime
import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict, Iterable, List, Mapping


sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))

from app import db


LOCKED_ID_MIN = 1
LOCKED_ID_MAX = 1000
LOCKED_START_DATE = "20260616"
LOCKED_END_DATE = "20260812"
LOCKED_DATA_STATUS = "settlement_conflict"
LOCKED_RECONCILIATION_STATUS = "monthly_unmatched"
LOCKED_PARSER_VERSION = "wh6-match-v1"
OPERATION_TYPE = "cleanup_wh6_erroneous_history"


class CleanupSafetyError(RuntimeError):
    """Raised when the exact cleanup contract is not proven."""


def _row_value(row: Mapping[str, Any], key: str) -> Any:
    return row[key] if row is not None else None


def _locked_fill_where() -> str:
    return """
        f.account_id = ?
        AND f.id BETWEEN ? AND ?
        AND f.data_status = ?
        AND f.reconciliation_status = ?
        AND REPLACE(f.trade_date, '-', '') BETWEEN ? AND ?
        AND f.parser_version = ?
    """


def _locked_fill_params(account_id: int) -> tuple[Any, ...]:
    return (
        account_id,
        LOCKED_ID_MIN,
        LOCKED_ID_MAX,
        LOCKED_DATA_STATUS,
        LOCKED_RECONCILIATION_STATUS,
        LOCKED_START_DATE,
        LOCKED_END_DATE,
        LOCKED_PARSER_VERSION,
    )


def _candidate_rows(cur, account_id: int) -> List[Dict[str, Any]]:
    rows = db._exec(
        cur,
        f"""
        SELECT f.id, f.account_id, f.source_event_key, f.trade_date
        FROM trading_intraday_fills f
        WHERE {_locked_fill_where()}
        ORDER BY f.id
        """,
        _locked_fill_params(account_id),
    ).fetchall()
    return [dict(row) for row in rows]


def _create_candidate_table(cur, rows: Iterable[Mapping[str, Any]]) -> None:
    db._exec(
        cur,
        """
        CREATE TEMP TABLE cleanup_wh6_candidate
            (id INTEGER PRIMARY KEY, source_event_key TEXT NOT NULL UNIQUE)
        """,
    )
    db._executemany(
        cur,
        "INSERT INTO cleanup_wh6_candidate (id, source_event_key) VALUES (?, ?)",
        [(int(row["id"]), str(row["source_event_key"])) for row in rows],
    )


def _count_dependencies(cur, account_id: int) -> Dict[str, Any]:
    row = db._exec(
        cur,
        """
        SELECT COUNT(*) AS observations,
               COUNT(DISTINCT o.source_event_key) AS observation_keys,
               MIN(o.device_id) AS min_device_id,
               MAX(o.device_id) AS max_device_id
        FROM trading_intraday_fill_observations o
        JOIN cleanup_wh6_candidate c ON c.source_event_key = o.source_event_key
        WHERE o.account_id = ?
        """,
        (account_id,),
    ).fetchone()
    reconciliation = db._exec(
        cur,
        """
        SELECT COUNT(*) AS reconciliations,
               COUNT(DISTINCT r.intraday_fill_id) AS reconciliation_fill_ids
        FROM trading_intraday_fill_reconciliations r
        JOIN cleanup_wh6_candidate c ON c.id = r.intraday_fill_id
        WHERE r.account_id = ?
        """,
        (account_id,),
    ).fetchone()
    issue = db._exec(
        cur,
        """
        SELECT COUNT(*) AS issues
        FROM trading_collector_issues i
        JOIN cleanup_wh6_candidate c ON c.source_event_key = i.source_event_key
        WHERE i.account_id = ?
        """,
        (account_id,),
    ).fetchone()
    device = db._exec(
        cur,
        """
        SELECT COUNT(*) AS device_count
        FROM trading_collector_devices
        WHERE id = 1 AND account_id = ?
        """,
        (account_id,),
    ).fetchone()
    return {
        "observations": int(_row_value(row, "observations") or 0),
        "observation_keys": int(_row_value(row, "observation_keys") or 0),
        "min_device_id": _row_value(row, "min_device_id"),
        "max_device_id": _row_value(row, "max_device_id"),
        "reconciliations": int(_row_value(reconciliation, "reconciliations") or 0),
        "reconciliation_fill_ids": int(
            _row_value(reconciliation, "reconciliation_fill_ids") or 0
        ),
        "issues": int(_row_value(issue, "issues") or 0),
        "device_count": int(_row_value(device, "device_count") or 0),
    }


def _matching_rows_after_boundary(cur, account_id: int) -> int:
    row = db._exec(
        cur,
        f"""
        SELECT COUNT(*) AS c
        FROM trading_intraday_fills f
        WHERE f.account_id = ?
          AND f.id >= ?
          AND f.data_status = ?
          AND f.reconciliation_status = ?
          AND REPLACE(f.trade_date, '-', '') BETWEEN ? AND ?
          AND f.parser_version = ?
        """,
        (
            account_id,
            LOCKED_ID_MAX + 1,
            LOCKED_DATA_STATUS,
            LOCKED_RECONCILIATION_STATUS,
            LOCKED_START_DATE,
            LOCKED_END_DATE,
            LOCKED_PARSER_VERSION,
        ),
    ).fetchone()
    return int(_row_value(row, "c") or 0)


def _validate_candidate(
    cur,
    account_id: int,
    expected_count: int,
    rows: List[Mapping[str, Any]],
) -> Dict[str, Any]:
    if expected_count != LOCKED_ID_MAX:
        raise CleanupSafetyError("expected-count 必须锁定为 1000")
    if len(rows) != expected_count:
        raise CleanupSafetyError(f"候选成交数量不符：{len(rows)}")
    ids = [int(row["id"]) for row in rows]
    keys = [str(row["source_event_key"] or "") for row in rows]
    if any(not key for key in keys) or len(set(keys)) != expected_count:
        raise CleanupSafetyError("候选规范事件键不是 1000 个且无重复")
    if min(ids) != LOCKED_ID_MIN or max(ids) != LOCKED_ID_MAX:
        raise CleanupSafetyError("候选 ID 范围不是 1—1000")
    if _matching_rows_after_boundary(cur, account_id):
        raise CleanupSafetyError("ID 1001 及以上仍存在相同错误候选条件")

    dependencies = _count_dependencies(cur, account_id)
    if dependencies["observations"] != expected_count or dependencies["observation_keys"] != expected_count:
        raise CleanupSafetyError("原始观察依赖数量不是 1000/1000")
    if dependencies["min_device_id"] != 1 or dependencies["max_device_id"] != 1 or dependencies["device_count"] != 1:
        raise CleanupSafetyError("候选原始观察不是全部来自账户绑定的设备 1")
    if dependencies["reconciliations"] != expected_count or dependencies["reconciliation_fill_ids"] != expected_count:
        raise CleanupSafetyError("协调记录依赖数量不是 1000/1000")
    if dependencies["issues"] != 0:
        raise CleanupSafetyError("候选存在关联问题记录，已停止")
    return {
        "fills": expected_count,
        "observations": dependencies["observations"],
        "reconciliations": dependencies["reconciliations"],
        "issues": dependencies["issues"],
        "min_id": min(ids),
        "max_id": max(ids),
    }


def _audit_exists(cur, account_code: str) -> bool:
    row = db._exec(
        cur,
        """
        SELECT 1 FROM operation_logs
        WHERE module_code = 'trading_collector'
          AND operation_type = ?
          AND description LIKE ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (OPERATION_TYPE, f'%' + f'"account_code": "{account_code}"' + '%'),
    ).fetchone()
    return row is not None


def _safe_backup_schema(value: str) -> str:
    schema = str(value or "").strip()
    if not schema:
        return "not_recorded"
    if not schema.startswith("codex_backup_"):
        raise CleanupSafetyError("backup-schema 必须使用 codex_backup_ 前缀")
    return schema


def _operation_description(
    account_code: str,
    account_id: int,
    expected_count: int,
    candidate: Mapping[str, Any],
    deleted: Mapping[str, Any],
    backup_schema: str,
) -> str:
    return json.dumps(
        {
            "environment": "staging",
            "executed_by": "codex-staging-cleanup",
            "account_code": account_code,
            "account_id": account_id,
            "expected_count": expected_count,
            "candidate_before": dict(candidate),
            "deleted": dict(deleted),
            "candidate_after": 0,
            "backup_schema": backup_schema,
            "executed_at": datetime.now().replace(microsecond=0).isoformat(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def run_cleanup(
    *,
    environment: str,
    account_code: str,
    expected_count: int,
    apply: bool = False,
    backup_schema: str = "",
) -> Dict[str, Any]:
    """Validate and optionally delete only the frozen Staging candidate."""
    if str(environment).strip().lower() != "staging":
        raise CleanupSafetyError("错误历史清理只允许在 staging 环境执行")
    if not account_code or account_code != "hongyuan_futures":
        raise CleanupSafetyError("账户必须锁定为 hongyuan_futures")
    if apply and not str(backup_schema or "").strip():
        raise CleanupSafetyError("apply 必须先提供已创建的 --backup-schema")
    backup_schema = _safe_backup_schema(backup_schema)

    with db.connect() as conn:
        cur = conn.cursor()
        account = db._exec(
            cur,
            "SELECT id FROM trading_accounts WHERE account_code = ?",
            (account_code,),
        ).fetchone()
        if account is None:
            raise CleanupSafetyError("找不到锁定账户")
        account_id = int(account["id"])
        rows = _candidate_rows(cur, account_id)
        if not rows:
            if _audit_exists(cur, account_code):
                return {"status": "already_clean", "candidate": {"fills": 0, "observations": 0, "reconciliations": 0, "issues": 0}}
            raise CleanupSafetyError("候选为空且没有本次清理审计，拒绝猜测性删除")
        _create_candidate_table(cur, rows)
        candidate = _validate_candidate(cur, account_id, expected_count, rows)
        if not apply:
            conn.rollback()
            return {"status": "dry_run", "candidate": candidate}

        rows_again = _candidate_rows(cur, account_id)
        if [(row["id"], row["source_event_key"]) for row in rows_again] != [
            (row["id"], row["source_event_key"]) for row in rows
        ]:
            raise CleanupSafetyError("应用前候选发生漂移，已停止")
        deleted = {
            "fills": candidate["fills"],
            "observations": candidate["observations"],
            "reconciliations": candidate["reconciliations"],
        }
        db._exec(
            cur,
            "DELETE FROM trading_intraday_fill_reconciliations WHERE intraday_fill_id IN (SELECT id FROM cleanup_wh6_candidate)",
        )
        db._exec(
            cur,
            """
            DELETE FROM trading_intraday_fill_observations
            WHERE account_id = ?
              AND source_event_key IN (SELECT source_event_key FROM cleanup_wh6_candidate)
            """,
            (account_id,),
        )
        remaining_issues = db._exec(
            cur,
            """
            SELECT COUNT(*) AS c
            FROM trading_collector_issues i
            JOIN cleanup_wh6_candidate c ON c.source_event_key = i.source_event_key
            WHERE i.account_id = ?
            """,
            (account_id,),
        ).fetchone()
        if int(_row_value(remaining_issues, "c") or 0):
            raise CleanupSafetyError("删除前仍发现关联问题记录，已回滚")
        db._exec(cur, "DELETE FROM trading_intraday_fills WHERE id IN (SELECT id FROM cleanup_wh6_candidate)")
        db._exec(
            cur,
            """
            INSERT INTO operation_logs
                (module_code, entity_type, entity_id, operation_type, description)
            VALUES ('trading_collector', 'intraday_fill_cleanup', ?, ?, ?)
            """,
            (
                account_id,
                OPERATION_TYPE,
                _operation_description(
                    account_code, account_id, expected_count, candidate, deleted, backup_schema
                ),
            ),
        )
        after = _candidate_rows(cur, account_id)
        if after:
            raise CleanupSafetyError("删除后锁定候选仍存在，已回滚")
        return {"status": "applied", "candidate": candidate, "deleted": deleted}


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清理 Staging 错误 WH6 历史成交")
    parser.add_argument("--environment", required=True)
    parser.add_argument("--account-code", required=True)
    parser.add_argument("--expected-count", required=True, type=int)
    parser.add_argument("--backup-schema", default="")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_cleanup(
            environment=args.environment,
            account_code=args.account_code,
            expected_count=args.expected_count,
            apply=args.apply,
            backup_schema=args.backup_schema,
        )
    except CleanupSafetyError as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
