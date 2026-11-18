import unittest
from deeplinkerp_branding.services import mexico_expense_insights as i


class ManagementInsightsTests(unittest.TestCase):
    def test_complete_calendar_months_across_year(self):
        r = i.comparison_period("2026-01-01", "2026-03-31", "2026-11-18")
        self.assertEqual((r["previous_from"], r["previous_to"], r["mode"]), ("2025-10-01", "2025-12-31", "calendar"))

    def test_current_month_uses_elapsed_dates_and_previous_month_same_day(self):
        r = i.comparison_period("2026-11-01", "2026-11-30", "2026-11-18")
        self.assertEqual((r["to_date"], r["previous_from"], r["previous_to"]), ("2026-11-18", "2026-10-01", "2026-10-18"))
        self.assertEqual(r["mode"], "month_to_date")

    def test_previous_month_clamps_leap_day_and_custom_windows_are_equal(self):
        r = i.comparison_period("2024-03-01", "2024-03-31", "2024-03-30")
        self.assertEqual(r["previous_to"], "2024-02-29")
        r = i.comparison_period("2026-08-17", "2026-11-17", "2026-11-18")
        self.assertEqual((r["previous_from"], r["previous_to"]), ("2026-05-16", "2026-08-16"))

    def test_future_and_lower_date_boundary_do_not_invent_comparisons(self):
        self.assertIsNone(i.comparison_period("2026-12-01", "2026-12-31", "2026-11-18"))
        self.assertIsNone(i.comparison_period("1900-01-01", "1900-01-31", "2026-11-18"))

    def test_zero_base_and_exact_large_amount_changes(self):
        self.assertIsNone(i.change("100", "0")["percent"])
        self.assertEqual(i.change("9007199254740992.02", "9007199254740992.01")["delta"], "0.01")
        self.assertEqual(i.change("0", "100")["percent"], "-100.0")

    def test_rankings_include_disappeared_companies_and_order_by_amount(self):
        rows = i.ranking([{"company":"A","amount":"10"},{"company":"B","amount":"30"}],
                         [{"company":"A","amount":"20"},{"company":"C","amount":"5"}], "company")
        self.assertEqual([r["key"] for r in rows], ["B", "A", "C"])
        self.assertEqual(rows[0]["share"], "75.0")
        self.assertEqual(rows[-1]["delta"], "-5.00")
