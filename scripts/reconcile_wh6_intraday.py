"""Dry-run-first WH6 intraday reconciliation command.

The command reuses the service reconciliation engine.  It never prints a
database URL, token, or credential; production apply is guarded separately.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Dict, Optional, Sequence
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app import db
from app import trading_collector_reconciliation as reconciliation


class CommandResult(dict):
    def __init__(self, payload: Dict[str, Any], *, exit_code: int = 0):
        super().__init__(payload)
        self.exit_code = exit_code


def _database_fingerprint() -> str:
    """Hash only the non-secret database location components."""

    raw_url = db.get_db_url()
    parsed = urlparse(raw_url)
    if parsed.scheme.startswith("postgres"):
        identity = "%s://%s:%s%s" % (
            parsed.scheme,
            parsed.hostname or "",
            parsed.port or "",
            parsed.path or "",
        )
    else:
        identity = "sqlite:%s" % Path(db.DB_PATH).expanduser().resolve()
    return "sha256:%s" % hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="协调 WH6 盘中成交与结算来源")
    parser.add_argument("--environment", choices=("staging", "production"), required=True)
    parser.add_argument("--account-code", required=True)
    parser.add_argument("--apply", action="store_true", help="提交协调结果；默认只读 dry-run")
    parser.add_argument(
        "--production-confirmation",
        default="",
        help="Production apply 的独立确认凭据；Staging 不需要",
    )
    return parser


def _account_row(cur, account_code: str):
    row = db._exec(
        cur,
        "SELECT id, account_code FROM trading_accounts WHERE account_code = ? AND is_active = 1",
        (account_code,),
    ).fetchone()
    if not row:
        raise ValueError("目标交易账户不存在或已停用")
    return row


def _validate_active_ranges(cur, account_id: int) -> list[dict[str, object]]:
    rows = db._exec(
        cur,
        """
        SELECT id, range_start, range_end, statement_type
        FROM trading_import_batches
        WHERE account_id = ? AND status = 'active' AND statement_type = 'monthly'
        ORDER BY range_start, range_end, id
        """,
        (account_id,),
    ).fetchall()
    for row in rows:
        if reconciliation._complete_month_range(row["range_start"], row["range_end"]) is None:
            raise ValueError("存在未覆盖完整自然月的 active 月结批次，已停止协调")
    return reconciliation.get_active_monthly_ranges(cur, account_id)


def _validate_current_audits(cur, account_id: int) -> None:
    row = db._exec(
        cur,
        """
        SELECT intraday_fill_id, COUNT(*) AS current_count
        FROM trading_intraday_fill_reconciliations
        WHERE account_id = ? AND is_current = 1
        GROUP BY intraday_fill_id
        HAVING COUNT(*) > 1
        LIMIT 1
        """,
        (account_id,),
    ).fetchone()
    if row:
        raise ValueError("one intraday fill has multiple current reconciliation rows; stopped")


def _rollback_anchor(cur) -> Dict[str, Any]:
    row = db._exec(
        cur,
        "SELECT COALESCE(MAX(id), 0) AS max_id FROM trading_intraday_fill_reconciliations",
    ).fetchone()
    return {
        "table": "trading_intraday_fill_reconciliations",
        "max_id_before": int(row["max_id"] or 0),
    }


def _run(args: argparse.Namespace) -> CommandResult:
    if args.environment == "production" and args.apply and not args.production_confirmation.strip():
        return CommandResult(
            {
                "state": "blocked",
                "environment": args.environment,
                "mode": "blocked",
                "message": "Production apply 必须提供独立确认凭据；本次未执行数据库操作",
            },
            exit_code=2,
        )

    with db.connect() as conn:
        cur = conn.cursor()
        account = _account_row(cur, args.account_code)
        account_id = int(account["id"])
        ranges = _validate_active_ranges(cur, account_id)
        _validate_current_audits(cur, account_id)
        anchor = _rollback_anchor(cur)
        bounds = db._exec(
            cur,
            """
            SELECT MIN(trade_date) AS start_date, MAX(trade_date) AS end_date,
                   COUNT(*) AS count
            FROM trading_intraday_fills
            WHERE account_id = ?
            """,
            (account_id,),
        ).fetchone()
        start = str(bounds["start_date"] or "")
        end = str(bounds["end_date"] or "")
        summary = reconciliation.reconcile_intraday_range(
            cur,
            account_id,
            start,
            end,
            "reconcile_wh6_intraday",
        )
        if summary.scanned != summary.changed + summary.unchanged:
            raise ValueError("协调结果无法通过变更/未变更守恒校验")
        if not args.apply:
            conn.rollback()
        else:
            after = _rollback_anchor(cur)
            anchor["max_id_after"] = after["max_id_before"]
        payload: Dict[str, Any] = {
            "mode": "apply" if args.apply else "dry-run",
            "environment": args.environment,
            "account_code": args.account_code,
            "database_fingerprint": _database_fingerprint(),
            "active_monthly_ranges": ranges,
            "range_start": start or None,
            "range_end": end or None,
            "rollback_anchor": anchor,
        }
        payload.update(summary.to_dict())
        payload["conflict"] = summary.conflicts
        return CommandResult(payload)


def run_command(argv: Optional[Sequence[str]] = None) -> CommandResult:
    args = _parser().parse_args(argv)
    try:
        return _run(args)
    except ValueError as exc:
        return CommandResult(
            {
                "state": "reconciliation_error",
                "environment": args.environment,
                "mode": "apply" if args.apply else "dry-run",
                "message": str(exc),
            },
            exit_code=2,
        )
    except Exception:
        return CommandResult(
            {
                "state": "reconciliation_error",
                "environment": args.environment,
                "mode": "apply" if args.apply else "dry-run",
                "message": "协调命令执行失败，未输出数据库连接细节",
            },
            exit_code=2,
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    result = run_command(argv)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
