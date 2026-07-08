import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import db
from backend.app.main import StrategyGroupIn, create_strategy_group, list_strategy_groups


class MidEventMonitorSchemaTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_db_path = db.DB_PATH
        self._original_database_url = db.os.environ.pop("DATABASE_URL", None)
        db.DB_PATH = Path(self._tmpdir.name) / "test.db"
        self.user = {"id": 1, "name": "管理员", "role": "管理员"}

    def tearDown(self):
        db.DB_PATH = self._original_db_path
        if self._original_database_url is not None:
            db.os.environ["DATABASE_URL"] = self._original_database_url
        self._tmpdir.cleanup()

    def test_strategy_group_reader_migrates_legacy_creator_column(self):
        conn = sqlite3.connect(db.DB_PATH)
        conn.executescript(
            """
            CREATE TABLE strategy_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_name TEXT NOT NULL UNIQUE,
                creator TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE strategy_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                variety TEXT NOT NULL,
                variety_name TEXT,
                direction TEXT NOT NULL,
                open_price REAL NOT NULL,
                quantity INTEGER NOT NULL,
                multiplier INTEGER DEFAULT 100,
                contract TEXT DEFAULT '',
                current_price REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO strategy_groups (group_name, creator) VALUES ('历史策略组', '管理员');
            """
        )
        conn.commit()
        conn.close()

        db.init_db()

        with patch("backend.app.main.all_groups_total_pnl", return_value=0), patch(
            "backend.app.main.group_pnl", return_value=0
        ):
            result = list_strategy_groups(user=self.user)

        self.assertEqual(result["groups"][0]["group_name"], "历史策略组")
        self.assertEqual(result["groups"][0]["created_by"], "管理员")

    @patch("backend.app.main.db.log_operation")
    def test_create_strategy_group_uses_current_audit_column(self, log_operation):
        db.init_db()

        created = create_strategy_group(StrategyGroupIn(group_name="新策略组"), user=self.user)

        with db.connect() as conn:
            cur = conn.cursor()
            row = db._exec(
                cur,
                "SELECT id, group_name, created_by FROM strategy_groups WHERE id = ?",
                (created["id"],),
            ).fetchone()

        self.assertEqual(row["group_name"], "新策略组")
        self.assertEqual(row["created_by"], "管理员")
        log_operation.assert_called()


if __name__ == "__main__":
    unittest.main()
