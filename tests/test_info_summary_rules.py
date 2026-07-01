import unittest
from datetime import date
from unittest.mock import patch

from backend.app.main import (
    InfoCalculateIn,
    InfoCalculateAllIn,
    cache_month_key,
    calculate_info_summary_payload,
    calculate_info_summary_all,
    calculate_today_indicator,
    default_info_contracts,
    indicator_contracts_for_cache,
    info_summary_config,
    value_from_cached_prices,
)


class InfoSummaryRulesTest(unittest.TestCase):
    def test_default_month_diff_matches_source_for_june(self):
        defaults = default_info_contracts(date(2026, 6, 23))

        self.assertEqual(defaults["default_year"], 2026)
        self.assertEqual(defaults["default_month"], "09")
        self.assertEqual(
            defaults["yuecha_defaults"],
            {"year1": 2026, "month1": "09", "year2": 2027, "month2": "01"},
        )

    def test_config_adds_swap_month_diff_and_special_month_options(self):
        config = info_summary_config(user={"id": 1})

        self.assertEqual(
            config["info_types"][config["info_types"].index("月差") + 1],
            "掉期月差",
        )
        self.assertEqual(config["month_options_by_type"]["螺矿比"], ["01", "05", "09"])
        self.assertEqual(config["month_options_by_type"]["盘面钢厂利润"], ["01", "05", "09"])

    def test_swap_month_diff_uses_fe_contract_pair(self):
        payload = InfoCalculateIn(
            info_type="掉期月差",
            year=2026,
            calc_date="2026-06-23",
            year1=2026,
            month1="09",
            year2=2027,
            month2="01",
        )

        result = calculate_today_indicator(payload, mock=True)

        self.assertEqual(result["contracts"], {"FE1": "FE2609", "FE2": "FE2701"})
        self.assertEqual(result["today_value"], 4.5)
        self.assertEqual(cache_month_key(payload), "09_01")
        self.assertEqual(indicator_contracts_for_cache(payload), ["FE2609", "FE2701"])
        self.assertEqual(value_from_cached_prices("掉期月差", [103.5, 99.0]), 4.5)

    def test_regular_history_cache_uses_selected_contract_month(self):
        self.assertEqual(
            cache_month_key(InfoCalculateIn(info_type="螺矿比", year=2026, month="09", calc_date="2026-07-01")),
            "09",
        )
        self.assertEqual(
            indicator_contracts_for_cache(
                InfoCalculateIn(info_type="卷螺差", year=2026, month="09", calc_date="2026-07-01")
            ),
            ["HC2609", "RB2609"],
        )
        self.assertEqual(
            indicator_contracts_for_cache(
                InfoCalculateIn(info_type="螺矿比", year=2026, month="09", calc_date="2026-07-01")
            ),
            ["RB2610", "I2609"],
        )
        self.assertEqual(
            indicator_contracts_for_cache(
                InfoCalculateIn(info_type="盘面钢厂利润", year=2026, month="09", calc_date="2026-07-01")
            ),
            ["RB2610", "I2609", "J2609"],
        )

    def test_calculate_all_reuses_shared_realtime_quotes(self):
        calls = []
        fx_calls = []

        prices = {
            ("JM", "2609"): 1230.0,
            ("I", "2609"): 805.0,
            ("RB", "2610"): 3290.0,
            ("J", "2609"): 1760.0,
            ("FE", "2609"): 103.5,
        }

        def fake_fetch_sina_price(variety, contract="", mock=False):
            calls.append((variety, contract))
            return prices.get((variety, contract), 100.0)

        def fake_fetch_sgx_rate(contract, force_refresh=False):
            fx_calls.append((contract, force_refresh))
            return 7.18

        payload = InfoCalculateAllIn(items=[
            InfoCalculateIn(info_type="煤矿比", year=2026, month="09", calc_date="2026-06-24"),
            InfoCalculateIn(info_type="盘面钢厂利润", year=2026, month="09", calc_date="2026-06-24"),
            InfoCalculateIn(info_type="内外盘差2", year=2026, month="09", calc_date="2026-06-24"),
        ])

        with patch("backend.app.main.fetch_sina_price", side_effect=fake_fetch_sina_price), \
             patch("backend.app.main.fetch_sgx_usdcnh_rate", side_effect=fake_fetch_sgx_rate), \
             patch("backend.app.main.calculate_missing_cache_from_prices", return_value=None) as missing_cache:
            result = calculate_info_summary_all(payload, user={"id": 1, "role": "管理员"})

        self.assertEqual(len(result["cards"]), 3)
        self.assertEqual(calls.count(("I", "2609")), 1)
        self.assertEqual(calls.count(("JM", "2609")), 1)
        self.assertEqual(calls.count(("RB", "2610")), 1)
        self.assertEqual(calls.count(("J", "2609")), 1)
        self.assertEqual(calls.count(("FE", "2609")), 1)
        self.assertIn(("2609", True), fx_calls)
        self.assertEqual(missing_cache.call_count, 2)

    def test_calculate_all_does_not_show_stale_history_as_yesterday(self):
        payload = InfoCalculateAllIn(items=[
            InfoCalculateIn(
                info_type="月差",
                year=2026,
                calc_date="2026-07-01",
                year1=2026,
                month1="09",
                year2=2027,
                month2="01",
            ),
        ])
        latest = {
            "calc_date": "2026-06-10",
            "t_1_value": 15.0,
            "t_2_value": 16.0,
            "mean_value": 16.71,
            "min_value": 10.5,
            "max_value": 24.0,
            "std_value": 3.08,
        }

        with patch("backend.app.main.fetch_sina_price", side_effect=[744.5, 734.5]), \
             patch("backend.app.main.get_cached_data", return_value=None), \
             patch("backend.app.main.get_latest_cached_data", return_value=latest), \
             patch("backend.app.main.calculate_missing_cache_from_prices", return_value=None) as missing_cache, \
             patch("backend.app.main.db.log_operation"):
            result = calculate_info_summary_all(payload, user={"id": 1, "role": "管理员"})

        card = result["cards"][0]
        self.assertEqual(card["today_value"], 10.0)
        self.assertIsNone(card["t_1_value"])
        self.assertIsNone(card["t_2_value"])
        self.assertIsNone(card["std_value"])
        self.assertFalse(card["cache_hit"])
        self.assertTrue(card["history_stale"])
        self.assertEqual(card["history_calc_date"], "2026-06-10")
        missing_cache.assert_called_once()

    def test_inner_outer_does_not_mark_stale_cache_as_hit(self):
        payload = InfoCalculateIn(info_type="内外盘差", year=2026, month="09", calc_date="2026-07-01")
        latest = {
            "calc_date": "2026-06-11",
            "t_1_value": 81.41,
            "t_2_value": 80.0,
            "mean_value": 75.0,
            "min_value": 70.0,
            "max_value": 82.0,
            "std_value": 3.0,
        }
        realtime = {
            "month_values": {"05": None, "06": None, "07": None, "08": None, "09": 82.0},
            "contracts": {"05": {}, "06": {}, "07": {}, "08": {}, "09": {"I": "I2609"}},
        }

        with patch("backend.app.main.calculate_inner_outer_months", return_value=realtime), \
             patch("backend.app.main.get_cached_data", return_value=None), \
             patch("backend.app.main.get_latest_cached_data", return_value=latest):
            result = calculate_info_summary_payload(payload, fill_missing_history=False)

        self.assertFalse(result["cache_hit"])
        self.assertFalse(result["month_results"]["09"]["cache_hit"])
        self.assertTrue(result["month_results"]["09"]["history_stale"])
        self.assertEqual(result["month_results"]["09"]["history_calc_date"], "2026-06-11")


if __name__ == "__main__":
    unittest.main()
