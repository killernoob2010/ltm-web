#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.iron_ore_basis_import import import_basis_workbook


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate or import the iron-ore basis workbook.")
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--apply", action="store_true", help="Write validated rows transactionally.")
    args = parser.parse_args()

    try:
        result = import_basis_workbook(
            args.workbook,
            apply=args.apply,
            expected_sha256=args.expected_sha256,
        )
    except (ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, separators=(",", ":")))
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
