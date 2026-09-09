"""Dry-run by default. Apply only to the documented Staging project with backup evidence."""
import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import db
from app.trading_agent.schema import migrate_agent_v2_schema, statements

STAGING_REF = "hzpivfwtdiqnfxbcxgrm"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--environment", choices=["staging"])
    parser.add_argument("--backup-receipt", type=Path)
    args = parser.parse_args()
    if not args.apply:
        print(";\n".join(statements(postgres=True)) + ";")
        return
    if args.environment != "staging" or not args.backup_receipt:
        parser.error("Apply requires --environment staging and verified --backup-receipt")
    parsed = urlparse(os.environ.get("DATABASE_URL", ""))
    mapped = parsed.hostname == f"db.{STAGING_REF}.supabase.co" or (
        (parsed.hostname or "").endswith(".pooler.supabase.com") and
        parsed.username == f"postgres.{STAGING_REF}")
    if not mapped:
        parser.error("Database does not match the documented Staging project")
    receipt = json.loads(args.backup_receipt.read_text())
    if receipt.get("project_ref") != STAGING_REF or receipt.get("backup_verified") is not True or receipt.get("restore_verified") is not True:
        parser.error("Backup and recovery evidence is incomplete")
    with db.connect() as conn:
        migrate_agent_v2_schema(conn)
    print("Agent V2 adjunct migration applied to Staging; business acceptance remains separate.")


if __name__ == "__main__":
    main()
