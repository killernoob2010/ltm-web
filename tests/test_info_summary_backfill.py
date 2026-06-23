import tempfile
import unittest
from pathlib import Path

from backend.app import db
from backend.app.info_summary_backfill import (
    BackfillRequest,
    get_last_backfill_status,
    StaticHistoryProvider,
    build_backfill_jobs,
    run_info_summary_backfill,
    run_all_info_summary_backfills,
)
from backend.app.main import InfoCalculateIn


class TempDbTestCase(unittest.TestCase):
    def setUp(self):
        self._original_db_path = db.DB_PATH
        self._tmpdir = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self._tmpdir.name) / "test.db"
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self._original_db_path
        self._tmpdir.cleanup()


class InfoSummaryBackfillTest(TempDbTestCase):
    def test_build_backfill_jobs_covers_all_info_summary_types(self):
        request = BackfillRequest(calc_date="2026-06-23")

        jobs = build_backfill_jobs(request)

        self.assertEqual(
            [job.info_type for job in jobs],
            ["卷螺差", "螺矿比", "煤矿比", "盘面钢厂利润", "月差", "掉期月差", "内外盘差", "内外盘差2"],
        )

    def test_backfill_month_diff_writes_prices_and_calculated_values(self):
        provider = StaticHistoryProvider({
            "I2609": {
                "2026-06-18": 800.0,
                "2026-06-19": 805.0,
                "2026-06-22": 810.0,
                "2026-06-23": 812.0,
            },
            "I2701": {
                "2026-06-18": 770.0,
                "2026-06-19": 772.0,
                "2026-06-22": 775.0,
                "2026-06-23": 777.0,
            },
        })
        payload = InfoCalculateIn(
            info_type="月差",
            year=2026,
            calc_date="2026-06-23",
            year1=2026,
            month1="09",
            year2=2027,
            month2="01",
        )

        result = run_info_summary_backfill(payload, provider=provider)

        self.assertEqual(result.status, "success")
        self.assertEqual(result.info_type, "月差")
        self.assertEqual(result.price_rows_written, 8)
        self.assertEqual(result.calculated_rows_written, 3)
        self.assertEqual(result.latest_price_date, "2026-06-23")
        self.assertEqual(result.latest_calculated_date, "2026-06-23")

        with db.connect() as conn:
            cur = conn.cursor()
            prices = db._exec(cur, "SELECT COUNT(*) AS count FROM daily_prices").fetchone()["count"]
            calculated = db._exec(cur, "SELECT COUNT(*) AS count FROM calculated_data").fetchone()["count"]

        self.assertEqual(prices, 8)
        self.assertEqual(calculated, 3)

    def test_backfill_ignores_unrelated_contracts_for_same_info_type(self):
        provider = StaticHistoryProvider({
            "I2609": {
                "2026-06-22": 810.0,
                "2026-06-23": 812.0,
            },
            "I2701": {
                "2026-06-22": 775.0,
                "2026-06-23": 777.0,
            },
        })
        with db.connect() as conn:
            cur = conn.cursor()
            db._exec(
                cur,
                """
                INSERT INTO daily_prices (info_type, contract_code, calc_date, close_price)
                VALUES (?, ?, ?, ?)
                """,
                ("月差", "I2605", "2026-06-23", 700.0),
            )
        payload = InfoCalculateIn(
            info_type="月差",
            year=2026,
            calc_date="2026-06-23",
            year1=2026,
            month1="09",
            year2=2027,
            month2="01",
        )

        result = run_info_summary_backfill(payload, provider=provider)

        self.assertEqual(result.status, "success")
        self.assertEqual(result.calculated_rows_written, 1)

    def test_info_summary_cache_status_shape(self):
        from backend.app.main import info_summary_cache_status

        status = info_summary_cache_status(user={"id": 1})

        self.assertIn("cache_counts", status)
        self.assertIn("indicators", status)
        self.assertIn("last_backfill", status)

    def test_force_false_skips_when_recent_data_exists(self):
        provider = StaticHistoryProvider({
            "I2609": {"2026-06-18": 800.0, "2026-06-19": 805.0, "2026-06-22": 810.0, "2026-06-23": 812.0},
            "I2701": {"2026-06-18": 770.0, "2026-06-19": 772.0, "2026-06-22": 775.0, "2026-06-23": 777.0},
        })
        payload = InfoCalculateIn(
            info_type="月差", year=2026, calc_date="2026-06-23",
            year1=2026, month1="09", year2=2027, month2="01",
        )
        result1 = run_info_summary_backfill(payload, provider=provider, force=True)
        self.assertEqual(result1.status, "success")

        result2 = run_info_summary_backfill(payload, provider=provider, force=False)
        self.assertEqual(result2.status, "skipped")
        self.assertIn("无需回填", result2.message)

    def test_force_true_always_proceeds(self):
        provider = StaticHistoryProvider({
            "I2609": {"2026-06-22": 810.0, "2026-06-23": 812.0},
            "I2701": {"2026-06-22": 775.0, "2026-06-23": 777.0},
        })
        payload = InfoCalculateIn(
            info_type="月差", year=2026, calc_date="2026-06-23",
            year1=2026, month1="09", year2=2027, month2="01",
        )
        run_info_summary_backfill(payload, provider=provider, force=True)
        result = run_info_summary_backfill(payload, provider=provider, force=True)
        self.assertEqual(result.status, "success")

    def test_fe_fallback_returns_skipped(self):
        provider = StaticHistoryProvider({})
        payload = InfoCalculateIn(
            info_type="掉期月差", year=2026, calc_date="2026-06-23",
            year1=2026, month1="09", year2=2027, month2="01",
        )
        result = run_info_summary_backfill(payload, provider=provider, force=True)
        self.assertEqual(result.status, "skipped")
        self.assertIn("FE", result.message)

    def test_neiwaipancha_returns_skipped(self):
        provider = StaticHistoryProvider({})
        payload = InfoCalculateIn(
            info_type="内外盘差", year=2026, month="09", calc_date="2026-06-23",
        )
        result = run_info_summary_backfill(payload, provider=provider, force=True)
        self.assertEqual(result.status, "skipped")

    def test_get_last_backfill_status_tracks_results(self):
        provider = StaticHistoryProvider({
            "I2609": {"2026-06-22": 810.0, "2026-06-23": 812.0},
            "I2701": {"2026-06-22": 775.0, "2026-06-23": 777.0},
        })
        request = BackfillRequest(info_type="月差", calc_date="2026-06-23", force=True)
        results = run_all_info_summary_backfills(request, provider=provider)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "success")

        status = get_last_backfill_status()
        self.assertIsNotNone(status)
        self.assertIn("time", status)
        self.assertEqual(len(status["results"]), 1)
        self.assertEqual(status["results"][0]["info_type"], "月差")
        self.assertEqual(status["results"][0]["status"], "success")


if __name__ == "__main__":
    unittest.main()
