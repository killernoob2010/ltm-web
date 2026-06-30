import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from backend.app import db
from backend.app.main import (
    ShJunnengTradeCloseIn,
    ShJunnengTradeIn,
    close_sh_junneng_trade,
    create_sh_junneng_trade,
    list_sh_junneng_trades,
)


class ShJunnengLedgerTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_db_path = db.DB_PATH
        self._original_database_url = db.os.environ.pop("DATABASE_URL", None)
        db.DB_PATH = Path(self._tmpdir.name) / "test.db"
        db.init_db()
        self.user = {"id": 1, "name": "管理员", "role": "管理员"}

    def tearDown(self):
        db.DB_PATH = self._original_db_path
        if self._original_database_url is not None:
            db.os.environ["DATABASE_URL"] = self._original_database_url
        self._tmpdir.cleanup()

    def test_module_display_name_uses_correct_company_name(self):
        sh_module = [item for item in db.MODULES if item[1] == "sh_junneng"][0]

        self.assertEqual(sh_module[2], "上海钧能台账")

    @patch("backend.app.main.db.log_operation")
    def test_partial_closes_reduce_remaining_and_show_each_close_trade(self, log_operation):
        with patch("backend.app.main.fetch_sh_junneng_current_price", return_value=3020):
            created = create_sh_junneng_trade(
                ShJunnengTradeIn(
                    contract_month="RB2610",
                    direction="多头",
                    open_price=3000,
                    trade_quantity=10,
                    open_fee=100,
                    open_date="2026-06-29",
                    current_price=3020,
                ),
                user=self.user,
            )

        first_close = ShJunnengTradeCloseIn(
            close_quantity=4,
            close_price=3050,
            close_fee=20,
            close_date="2026-06-29",
        )
        close_sh_junneng_trade(created["id"], first_close, user=self.user)

        after_first = list_sh_junneng_trades(selected_date="2026-06-29", user=self.user)
        self.assertEqual(len(after_first["current_trades"]), 1)
        self.assertEqual(after_first["current_trades"][0]["remaining_quantity"], 6)
        self.assertEqual(after_first["current_trades"][0]["closed_quantity"], 4)
        self.assertEqual(len(after_first["settled_trades"]), 1)
        self.assertEqual(after_first["settled_trades"][0]["close_quantity"], 4)
        self.assertEqual(after_first["settled_trades"][0]["close_sequence"], 1)
        self.assertEqual(after_first["settled_trades"][0]["business_code"], after_first["current_trades"][0]["business_code"])

        second_close = ShJunnengTradeCloseIn(
            close_quantity=6,
            close_price=3060,
            close_fee=30,
            close_date="2026-06-29",
        )
        close_sh_junneng_trade(created["id"], second_close, user=self.user)

        after_second = list_sh_junneng_trades(selected_date="2026-06-29", user=self.user)
        self.assertEqual(after_second["current_trades"], [])
        self.assertEqual(len(after_second["settled_trades"]), 2)
        self.assertEqual([item["close_sequence"] for item in after_second["settled_trades"]], [2, 1])
        self.assertEqual([item["close_quantity"] for item in after_second["settled_trades"]], [6, 4])
        self.assertEqual(after_second["settled_trades"][0]["position_status"], "已全平")

        with self.assertRaises(HTTPException) as context:
            close_sh_junneng_trade(created["id"], first_close, user=self.user)
        self.assertEqual(context.exception.status_code, 400)
        log_operation.assert_called()


if __name__ == "__main__":
    unittest.main()
