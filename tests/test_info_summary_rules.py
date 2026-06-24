import unittest
from datetime import date
from unittest.mock import patch

from backend.app.main import (
    InfoCalculateIn,
    InfoCalculateAllIn,
    cache_month_key,
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
             patch("backend.app.main.calculate_missing_cache_from_prices") as missing_cache:
            result = calculate_info_summary_all(payload, user={"id": 1, "role": "管理员"})

        self.assertEqual(len(result["cards"]), 3)
        self.assertEqual(calls.count(("I", "2609")), 1)
        self.assertEqual(calls.count(("JM", "2609")), 1)
        self.assertEqual(calls.count(("RB", "2610")), 1)
        self.assertEqual(calls.count(("J", "2609")), 1)
        self.assertEqual(calls.count(("FE", "2609")), 1)
        self.assertIn(("2609", True), fx_calls)
        missing_cache.assert_not_called()


if __name__ == "__main__":
    unittest.main()
