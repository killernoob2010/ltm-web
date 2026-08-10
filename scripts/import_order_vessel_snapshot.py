#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import db
from app.order_finance import (
    ORDER_VESSEL_EXPECTED_SHA256,
    apply_order_vessel_email_due_checks,
    import_order_vessel_snapshot,
)


STAGING_SUPABASE_PROJECT_ID = "hzpivfwtdiqnfxbcxgrm"


def _validate_apply_target() -> None:
    database_url = db.get_db_url()
    if database_url.startswith("postgres") and STAGING_SUPABASE_PROJECT_ID not in database_url:
        raise ValueError("拒绝写入：当前 PostgreSQL 连接不是 LTM WEB STAGING")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate or import the finalized order-and-vessel snapshot."
    )
    parser.add_argument("workbook", type=Path)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the locked snapshot to the current local or Staging database.",
    )
    parser.add_argument("--imported-by", default="codex-staging-import")
    parser.add_argument(
        "--email-checks-json",
        type=Path,
        help="Optional exact business-number email due-date checks to apply after the R1 snapshot.",
    )
    args = parser.parse_args()

    try:
        if args.apply:
            _validate_apply_target()
        result = import_order_vessel_snapshot(
            args.workbook,
            apply=args.apply,
            imported_by=args.imported_by,
            expected_sha256=ORDER_VESSEL_EXPECTED_SHA256,
        )
        if args.email_checks_json:
            payload = json.loads(args.email_checks_json.read_text(encoding="utf-8"))
            checks = payload.get("checks") if isinstance(payload, dict) else None
            if not isinstance(checks, list):
                raise ValueError("邮件核对 JSON 顶层必须包含 checks 数组")
            if not args.apply:
                result["email_checks"] = {"validated": len(checks), "applied": False}
            else:
                result["email_checks"] = {
                    **apply_order_vessel_email_due_checks(checks),
                    "applied": True,
                }
    except (ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, separators=(",", ":")))
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
