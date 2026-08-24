#!/usr/bin/env python3
"""Validate or explicitly apply local historical spot-ledger manual fields."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.spot_ledger_sync import migrate_history_workbook


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate or import a spot-ledger history workbook.")
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--apply", action="store_true", help="Write unique matches to the current non-production database.")
    args = parser.parse_args()
    try:
        result = migrate_history_workbook(args.workbook, apply=args.apply)
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, separators=(",", ":")))
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
