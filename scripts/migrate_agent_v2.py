"""Dry-run by default. Apply only to an explicitly mapped environment with backup evidence."""
import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import db
from app.trading_agent.schema import migrate_agent_v2_schema, statements

PROJECT_REFS = {
    "staging": "hzpivfwtdiqnfxbcxgrm",
    "production": "xuwxyvafomussargruxn",
}


def database_matches_environment(database_url: str, environment: str) -> bool:
    ref = PROJECT_REFS[environment]
    parsed = urlparse(database_url)
    return parsed.hostname == f"db.{ref}.supabase.co" or (
        (parsed.hostname or "").endswith(".pooler.supabase.com")
        and parsed.username == f"postgres.{ref}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--environment", choices=sorted(PROJECT_REFS))
    parser.add_argument("--backup-receipt", type=Path)
    args = parser.parse_args()
    if not args.apply:
        print(";\n".join(statements(postgres=True)) + ";")
        return
    if not args.environment or not args.backup_receipt:
        parser.error("Apply requires an explicit --environment and verified --backup-receipt")
    if not database_matches_environment(os.environ.get("DATABASE_URL", ""), args.environment):
        parser.error(f"Database does not match the documented {args.environment} project")
    receipt = json.loads(args.backup_receipt.read_text())
    if (
        receipt.get("environment") != args.environment
        or receipt.get("project_ref") != PROJECT_REFS[args.environment]
        or receipt.get("backup_verified") is not True
        or receipt.get("restore_verified") is not True
    ):
        parser.error("Backup and recovery evidence is incomplete")
    with db.connect() as conn:
        migrate_agent_v2_schema(conn)
    print(f"Agent V2 adjunct migration applied to {args.environment}; business acceptance remains separate.")


if __name__ == "__main__":
    main()
