import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from backend.app import db
from backend.app import info_summary_backfill
from backend.app.info_summary_backfill import (
    BackfillRequest,
    get_last_backfill_status,
    StaticHistoryProvider,
    build_backfill_jobs,
    maybe_run_daily_close_cache_update,
    run_info_summary_backfill,
    run_all_info_summary_backfills,
)
from backend.app.cache_service import save_daily_prices_batch
from backend.app.main import (
    InfoCalculateAllIn,
    InfoCalculateIn,
    calculate_info_summary_all,
    calculate_missing_cache_from_prices,
)


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
        self.assertIn("last_close_cache_update", status)

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

    def test_missing_cache_uses_previous_trading_days_for_request_date(self):
        save_daily_prices_batch([
            ("月差", "I2609", "2026-06-26", 748.0),
            ("月差", "I2701", "2026-06-26", 735.5),
            ("月差", "I2609", "2026-06-29", 746.0),
            ("月差", "I2701", "2026-06-29", 734.5),
            ("月差", "I2609", "2026-06-30", 747.0),
            ("月差", "I2701", "2026-06-30", 736.5),
        ])
        payload = InfoCalculateIn(
            info_type="月差",
            year=2026,
            calc_date="2026-07-01",
            year1=2026,
            month1="09",
            year2=2027,
            month2="01",
        )

        result = calculate_missing_cache_from_prices(payload)

        self.assertIsNotNone(result)
        self.assertEqual(result["t_1_value"], 10.5)
        self.assertEqual(result["t_2_value"], 11.5)
        with db.connect() as conn:
            cur = conn.cursor()
            row = db._exec(
                cur,
                """
                SELECT t_1_value, t_2_value
                FROM calculated_data
                WHERE info_type = ? AND year = ? AND month = ? AND calc_date = ?
                """,
                ("月差", 2026, "09_01", "2026-07-01"),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["t_1_value"], 10.5)

    def test_missing_cache_rejects_stale_price_history(self):
        save_daily_prices_batch([
            ("月差", "I2609", "2026-06-08", 759.0),
            ("月差", "I2701", "2026-06-08", 744.0),
            ("月差", "I2609", "2026-06-09", 760.0),
            ("月差", "I2701", "2026-06-09", 744.5),
        ])
        payload = InfoCalculateIn(
            info_type="月差",
            year=2026,
            calc_date="2026-07-01",
            year1=2026,
            month1="09",
            year2=2027,
            month2="01",
        )

        self.assertIsNone(calculate_missing_cache_from_prices(payload))

    def test_calculate_all_reads_fresh_history_from_cached_prices(self):
        save_daily_prices_batch([
            ("月差", "I2609", "2026-06-26", 748.0),
            ("月差", "I2701", "2026-06-26", 735.5),
            ("月差", "I2609", "2026-06-29", 746.0),
            ("月差", "I2701", "2026-06-29", 734.5),
            ("月差", "I2609", "2026-06-30", 747.0),
            ("月差", "I2701", "2026-06-30", 736.5),
        ])
        payload = InfoCalculateAllIn(items=[
            InfoCalculateIn(
                info_type="月差",
                year=2026,
                calc_date="2026-07-01",
                year1=2026,
                month1="09",
                year2=2027,
                month2="01",
            )
        ])

        from unittest.mock import patch
        with patch("backend.app.main.fetch_sina_price", side_effect=[747.0, 737.0]), \
             patch("backend.app.main.db.log_operation"):
            result = calculate_info_summary_all(payload, user={"id": 1, "role": "管理员"})

        card = result["cards"][0]
        self.assertEqual(card["today_value"], 10.0)
        self.assertEqual(card["t_1_value"], 10.5)
        self.assertEqual(card["t_2_value"], 11.5)
        self.assertTrue(card["cache_hit"])
        self.assertFalse(card["history_stale"])
        self.assertEqual(card["history_calc_date"], "2026-07-01")

    def test_daily_close_cache_update_waits_until_after_close_time(self):
        info_summary_backfill._last_close_cache_update_date = None

        with patch("backend.app.info_summary_backfill.run_all_info_summary_backfills") as runner:
            result = maybe_run_daily_close_cache_update(now=datetime(2026, 7, 1, 15, 30))

        self.assertEqual(result["status"], "waiting")
        runner.assert_not_called()

    def test_daily_close_cache_update_runs_once_per_day_after_close(self):
        info_summary_backfill._last_close_cache_update_date = None

        with patch("backend.app.info_summary_backfill.run_all_info_summary_backfills", return_value=[]) as runner:
            first = maybe_run_daily_close_cache_update(now=datetime(2026, 7, 1, 16, 30))
            second = maybe_run_daily_close_cache_update(now=datetime(2026, 7, 1, 17, 30))

        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "skipped")
        runner.assert_called_once()
        request = runner.call_args.args[0]
        self.assertEqual(request.calc_date, "2026-07-01")
        self.assertFalse(request.force)


if __name__ == "__main__":
    unittest.main()
