import tempfile
import unittest
from pathlib import Path
from fastapi import HTTPException

from backend.app import db
from backend.app.main import (
    ShJunnengTradeCloseIn,
    ShJunnengTradeIn,
    ShJunnengManualPricesIn,
    close_sh_junneng_trade,
    create_sh_junneng_trade,
    delete_sh_junneng_trade,
    export_sh_junneng_trades,
    list_sh_junneng_trades,
    refresh_sh_junneng_prices,
    update_sh_junneng_prices_manually,
    update_sh_junneng_trade,
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

    def test_read_only_history_remains_available_after_retirement(self):
        with db.connect() as conn:
            cur = conn.cursor()
            db._exec(
                cur,
                """
                INSERT INTO sh_junneng_positions
                    (contract_month, direction, open_price, open_quantity,
                     remaining_quantity, open_fee, open_date, current_price,
                     business_code, status, created_by, updated_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("RB2610", "多头", 3000, 10, 10, 100, "2026-06-29", 3020,
                 "SHJN-ARCHIVE", "open", "管理员", "管理员"),
            )

        result = list_sh_junneng_trades(selected_date="2026-06-29", user=self.user)

        self.assertEqual(len(result["current_trades"]), 1)
        self.assertEqual(result["current_trades"][0]["business_code"], "SHJN-ARCHIVE")
        self.assertEqual(result["current_trades"][0]["remaining_quantity"], 10)

    def test_all_legacy_ledger_mutations_and_export_are_retired(self):
        trade = ShJunnengTradeIn(
            contract_month="RB2610",
            direction="多头",
            open_price=3000,
            trade_quantity=10,
            open_fee=100,
            open_date="2026-06-29",
            current_price=3020,
        )
        close = ShJunnengTradeCloseIn(
            close_quantity=4,
            close_price=3050,
            close_fee=20,
            close_date="2026-06-29",
        )
        operations = [
            lambda: create_sh_junneng_trade(trade, user=self.user),
            lambda: update_sh_junneng_trade(1, trade, user=self.user),
            lambda: delete_sh_junneng_trade(1, user=self.user),
            lambda: close_sh_junneng_trade(1, close, user=self.user),
            lambda: refresh_sh_junneng_prices(user=self.user),
            lambda: update_sh_junneng_prices_manually(
                ShJunnengManualPricesIn(prices={"RB2610": 3020}),
                user=self.user,
            ),
            lambda: export_sh_junneng_trades(user=self.user),
        ]

        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(HTTPException) as context:
                    operation()
                self.assertEqual(context.exception.status_code, 410)
                self.assertIn("已退役", context.exception.detail)


if __name__ == "__main__":
    unittest.main()
