import subprocess
import sys
from pathlib import Path

from app import db
from app.trading_agent.schema import TABLES, migrate_agent_v2_schema


def test_schema_rolls_back_with_enclosing_transaction():
    db.init_db()
    try:
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            migrate_agent_v2_schema(conn)
            raise RuntimeError("synthetic failure")
    except RuntimeError:
        pass
    with db.connect() as conn:
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'agent_v2_%'").fetchall()


def test_migration_default_is_preview_without_database(tmp_path):
    script = Path(__file__).parents[2] / "scripts/migrate_agent_v2.py"
    result = subprocess.run([sys.executable,str(script)],capture_output=True,text=True,env={"DATABASE_URL":"invalid-synthetic-url"})
    assert result.returncode == 0
    assert "CREATE TABLE IF NOT EXISTS agent_v2_runs" in result.stdout
    result = subprocess.run([sys.executable,str(script),"--apply","--environment","staging"],capture_output=True,text=True,env={})
    assert result.returncode != 0
