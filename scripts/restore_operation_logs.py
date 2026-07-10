#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.operation_log_archive import SupabaseArchiveStorage, restore_archive


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate or restore one operation-log archive.")
    parser.add_argument("archive_id", type=int)
    parser.add_argument("--apply", action="store_true", help="Restore rows after checksum and conflict checks.")
    args = parser.parse_args()

    storage = SupabaseArchiveStorage.from_env() if args.apply else None
    result = restore_archive(args.archive_id, storage, apply=args.apply)
    print(
        f"archive_id={result['archive_id']} "
        f"candidate_rows={result.get('candidate_rows', result.get('restored_rows', 0))} "
        f"restored_rows={result['restored_rows']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
