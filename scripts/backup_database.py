#!/usr/bin/env python3
"""Basic Supabase/Postgres backup helper.

Reads DATABASE_URL, but never prints it. Requires pg_dump for full/schema dumps.
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import psycopg2


CORE_TABLES = [
    "users",
    "module_permissions",
    "operation_logs",
    "alert_settings",
    "alert_history",
    "calculated_data",
    "daily_prices",
    "dv_integration_batches",
    "dv_integrated_points",
    "iron_ore_basis_results",
    "iron_ore_basis_details",
    "iron_ore_basis_sync_runs",
    "iron_ore_basis_source_points",
    "order_finance_progress",
    "sh_junneng_positions",
    "sh_junneng_close_trades",
]


def pg_env(database_url: str) -> dict[str, str]:
    parsed = urlparse(database_url)
    env = os.environ.copy()
    env.update(
        {
            "PGHOST": parsed.hostname or "",
            "PGPORT": str(parsed.port or 5432),
            "PGUSER": unquote(parsed.username or ""),
            "PGPASSWORD": unquote(parsed.password or ""),
            "PGDATABASE": unquote((parsed.path or "").lstrip("/")),
        }
    )
    return env


def run_pg_dump(output_dir: Path, database_url: str, schema_only: bool) -> Path:
    suffix = "schema.sql" if schema_only else "full.dump"
    target = output_dir / suffix
    command = ["pg_dump", "--no-owner", "--file", str(target)]
    if schema_only:
        command.append("--schema-only")
    else:
        command.extend(["--format", "custom"])
    subprocess.run(command, check=True, env=pg_env(database_url))
    return target


def export_core_csv(output_dir: Path, database_url: str) -> list[Path]:
    written: list[Path] = []
    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            for table in CORE_TABLES:
                target = output_dir / f"{table}.csv"
                with target.open("w", newline="", encoding="utf-8") as fh:
                    cur.copy_expert(f"COPY (SELECT * FROM {table}) TO STDOUT WITH CSV HEADER", fh)
                written.append(target)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup Supabase Postgres without printing secrets.")
    parser.add_argument("--mode", choices=["full", "schema", "csv", "all"], default="all")
    parser.add_argument("--output-dir", default="backups")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    if not database_url.startswith("postgres"):
        raise SystemExit("This backup helper is intended for PostgreSQL DATABASE_URL")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = Path(args.output_dir) / stamp
    output_dir.mkdir(parents=True, exist_ok=True)

    outputs: list[Path] = []
    if args.mode in {"full", "all"}:
        outputs.append(run_pg_dump(output_dir, database_url, schema_only=False))
    if args.mode in {"schema", "all"}:
        outputs.append(run_pg_dump(output_dir, database_url, schema_only=True))
    if args.mode in {"csv", "all"}:
        outputs.extend(export_core_csv(output_dir, database_url))

    print("backup_complete")
    for path in outputs:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
