import unittest
from unittest.mock import patch

from deeplinkerp_branding.services.operating_expense_analysis import summarize


def source(**values):
    return dict(company="Legal CN", request_date="2026-11-01", currency="CNY", amount="100.10",
                paid_amount="20.10", pending_amount="80", approval_state="approved",
                effective_application_type="payment", **values)


class AnalysisTests(unittest.TestCase):
    def test_month_company_and_type_keep_currencies_and_exact_decimals_separate(self):
        rows = [source(), {**source(), "currency": "USD", "amount": "0.20"},
                {**source(), "amount": "0.10", "request_date": "2026-10-31"}]
        result = summarize(rows)
        self.assertEqual(result["currency_totals"]["CNY"]["amount"], "100.20")
        self.assertEqual(result["currency_totals"]["USD"]["amount"], "0.20")
        november = result["months"][1]
        self.assertEqual(november["key"], "2026-11")
        self.assertEqual(november["currency_counts"], {"CNY": 1, "USD": 1})
        self.assertEqual(result["application_types"][0]["currency_counts"], {"CNY": 2, "USD": 1})

    def test_unknown_payments_remain_unknown_and_do_not_become_zero_or_paid(self):
        result = summarize([source(), {**source(), "paid_amount": None, "pending_amount": None}])
        bucket = result["currency_totals"]["CNY"]
        self.assertIsNone(bucket["paid_amount"])
        self.assertEqual(bucket["known_totals"]["paid_amount"], "20.10")
        self.assertEqual(result["tab_counts"]["paid"], 0)
        self.assertEqual(result["tab_counts"]["reconciliation"], 1)

    def test_unmapped_and_stale_attributions_do_not_enter_a_legal_company(self):
        rows = [source(), {**source(), "company": None},
                {**source(), "issues": "法律公司映射已变更"}]
        result = summarize(rows)
        self.assertEqual(result["company_overview"][0]["count"], 1)
        self.assertEqual(result["company_overview"][1]["company"], None)
        self.assertEqual(result["company_overview"][1]["count"], 2)

    def test_malformed_and_negative_amounts_do_not_create_false_aggregates(self):
        result = summarize([{**source(), "amount": "bad"}, {**source(), "paid_amount": "-10"}])
        bucket = result["currency_totals"]["CNY"]
        self.assertIsNone(bucket["amount"])
        self.assertIsNone(bucket["paid_amount"])
        self.assertEqual(bucket["anomaly_count"], 1)

    def test_empty_query_has_no_fabricated_currency_or_month(self):
        result = summarize([])
        self.assertEqual(result["currency_totals"], {})
        self.assertEqual(result["months"], [])
        self.assertEqual(result["total_count"], 0)


class ServiceBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Run these with Bench's Python so the real service and Frappe imports
        # are exercised; all reads are mocked and no site data is changed.
        from deeplinkerp_branding.services import operating_expenses
        cls.service = operating_expenses

    def test_analysis_reuses_permission_and_payment_projection_and_propagates_denial(self):
        filters = {"date_from": "2026-11-01", "company": "Legal CN"}
        with patch.object(self.service, "_list_rows", return_value=[source()]) as query, \
                patch.object(self.service, "_list_source_scope", return_value={"region": "CN"}):
            result = self.service.get_operating_expense_analysis(filters)
            query.assert_called_once_with(filters)
            self.assertEqual(result["total_count"], 1)
        with patch.object(self.service, "_list_rows", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                self.service.get_operating_expense_analysis(filters)

    def test_active_filter_keeps_unknown_pending_and_approved_but_excludes_closed(self):
        service = self.service
        states = ["approved", "pending", "unknown", "rejected", "terminated", "withdrawn"]
        rows = [{**source(), "approval_state": state} for state in states]
        rows.append({**source(), "source_withdrawn": True})
        with patch.object(service, "_require_fields") as permission, \
                patch.object(service.frappe, "get_list", return_value=rows), \
                patch.object(service, "_readable_source_numbers"), \
                patch("deeplinkerp_branding.services.operating_payment_service.overlay_rows"):
            result = service._list_rows({"approval_state": "active"})
            permission.assert_called_once_with(service.SOURCE, set(service.LIST_FIELDS))
        self.assertEqual([row["approval_state"] for row in result], states[:3])
