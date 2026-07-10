#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.operation_log_archive import SupabaseArchiveStorage, archive_due_logs


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run or archive eligible operation-log months.")
    parser.add_argument("--apply", action="store_true", help="Upload, verify, and delete archived online rows.")
    parser.add_argument("--environment", choices=["staging", "production"], default="staging")
    parser.add_argument("--today", help="Override current date as YYYY-MM-DD for controlled testing.")
    args = parser.parse_args()

    run_date = date.fromisoformat(args.today) if args.today else date.today()
    storage = SupabaseArchiveStorage.from_env() if args.apply else None
    result = archive_due_logs(
        storage,
        apply=args.apply,
        today=run_date,
        environment=args.environment,
    )
    print(
        f"apply={str(args.apply).lower()} candidate_months={result['candidate_months']} "
        f"candidate_rows={result['candidate_rows']} archived_rows={result['archived_rows']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
