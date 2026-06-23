import unittest
from datetime import date

from backend.app.main import (
    InfoCalculateIn,
    cache_month_key,
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


if __name__ == "__main__":
    unittest.main()
