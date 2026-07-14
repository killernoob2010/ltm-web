#!/usr/bin/env python3
"""Fetch and optionally apply an iron-ore basis increment."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.iron_ore_basis_sources import BasisSourceError  # noqa: E402
from backend.app.iron_ore_basis_sync import sync_basis_range  # noqa: E402


def iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("日期必须使用 YYYY-MM-DD") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", type=iso_date, required=True)
    parser.add_argument("--end-date", type=iso_date, required=True)
    parser.add_argument("--slot-key")
    parser.add_argument("--apply", action="store_true", help="Write source points and complete results.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    slot_key = args.slot_key or (
        f"manual:{args.start_date.isoformat()}:{args.end_date.isoformat()}:"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    try:
        summary = sync_basis_range(
            args.start_date,
            args.end_date,
            trigger_type="manual",
            slot_key=slot_key,
            apply=args.apply,
        )
    except (BasisSourceError, ValueError, RuntimeError) as exc:
        code = exc.code if isinstance(exc, BasisSourceError) else "sync_failed"
        print(json.dumps({"ok": False, "code": code, "message": str(exc)}, ensure_ascii=False))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "applied": args.apply,
                "status": summary.status,
                "start_date": summary.target_start_date.isoformat(),
                "end_date": summary.target_end_date.isoformat(),
                "source_points_seen": summary.source_points_seen,
                "source_points_inserted": summary.source_points_inserted,
                "source_differences": summary.source_differences,
                "combinations_written": summary.combinations_written,
                "combinations_skipped": summary.combinations_skipped,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
