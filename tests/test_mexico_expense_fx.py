import unittest
from decimal import Decimal

from deeplinkerp_branding.services import mexico_expense_fx as fx


class MexicoExpenseFxTests(unittest.TestCase):
    def test_cross_rate_direction_and_identity(self):
        self.assertEqual(fx.cross_rate("7", ".35"), Decimal("20"))
        self.assertEqual(fx.cross_rate(".35", "7"), Decimal(".05"))
        self.assertEqual(fx.cross_rate("1", "1"), Decimal("1"))

    def test_line_rounding_and_large_original_amounts(self):
        self.assertEqual(fx.converted_amount("0.10", "1.05"), Decimal("0.11"))
        self.assertEqual(fx.converted_amount("999999999999.99", "20"), Decimal("19999999999999.80"))
        self.assertEqual(sum(fx.converted_amount("0.10", "1.05") for _ in range(3)), Decimal("0.33"))

    def test_missing_nonfinite_or_negative_values_are_not_zero(self):
        for value in (None, "", "bad", "NaN", "Infinity", "-1"):
            self.assertIsNone(fx.converted_amount(value, "1"))
        for rate in (None, "", "NaN", "Infinity", "0", "-1"):
            self.assertIsNone(fx.converted_amount("10", rate))
            with self.assertRaises(ValueError):
                fx.cross_rate(rate, "1")

    def test_finance_month_overrides_application_month_and_preserves_original(self):
        from unittest.mock import Mock
        loader = Mock(return_value=Decimal('7'))
        converter = fx.Conversion('CNY', loader)
        original = dict(amount='12.30', currency='USD', finance_approved_date='2026-11-30', request_date='2026-09-05')
        result = converter.line(original)
        self.assertEqual(result['converted_amount'], '86.10')
        self.assertEqual(result['exchange_rate_date'], '2026-11-01')
        self.assertEqual(result['fx_date_basis'], 'finance')
        self.assertFalse(result['fx_estimated'])
        self.assertEqual(original, {key: result[key] for key in original})
        converter.line(original)
        loader.assert_called_once_with('USD', '2026-11-01')

    def test_pending_uses_application_month_and_missing_currency_is_incomplete(self):
        from unittest.mock import Mock
        loader = Mock(return_value=Decimal('.35'))
        converter = fx.Conversion('CNY', loader)
        row = dict(amount='100', currency='MXN', request_date='2026-09-30')
        result = converter.line(row)
        self.assertEqual(result['converted_amount'], '35.00')
        self.assertTrue(result['fx_estimated'])
        self.assertEqual(result['fx_date_basis'], 'request')
        loader.assert_called_once_with('MXN', '2026-09-01')
        for currency in (None, '', 'EUR'):
            result = converter.line({**row, 'currency':currency})
            self.assertIsNone(result['converted_amount'])
            self.assertEqual(result['fx_status'], 'incomplete')
        result = converter.line({**row, 'currency':'CNY'})
        self.assertEqual(result['converted_amount'], '100.00')
        self.assertTrue(result['fx_estimated'])

    def test_cross_month_rates_are_independent_and_cny_legs_are_reused(self):
        from unittest.mock import Mock
        loader = Mock(side_effect=lambda code, month: {'USD': Decimal('7'), 'MXN': Decimal('.35') if month=='2026-09-01' else Decimal('.28')}[code])
        converter = fx.Conversion('USD', loader)
        for month, expected in [('2026-09-01','5.00'),('2026-10-01','4.00')]:
            self.assertEqual(converter.line(dict(amount='100',currency='MXN',finance_approved_date=month))['converted_amount'],expected)
            self.assertEqual(converter.line(dict(amount='7',currency='CNY',finance_approved_date=month))['converted_amount'],'1.00')
        self.assertEqual(loader.call_count, 4)

    def test_provider_only_caches_valid_exact_day_rates(self):
        import frappe
        from unittest.mock import Mock, patch
        from overseas_costing.services import fx_rate_service
        cache = Mock()
        cache.get_value.return_value = None
        valid = dict(ok=True, rate_date='2026-11-01', cny_per_unit='.35', raw={'rateDate':'2026-11-01'})
        with patch.object(frappe, 'cache', cache), patch.object(frappe, '_', side_effect=lambda x:x), \
                patch.object(frappe, 'throw', side_effect=ValueError('missing month-start rate')), \
                patch.object(fx_rate_service, 'fetch_cny_rate', return_value=valid) as fetch:
            self.assertEqual(fx.load_month_rate('MXN','2026-11-01'), Decimal('.35'))
            fetch.assert_called_once_with(currency='MXN',payment_date='2026-11-01')
            cache.set_value.assert_called_once()
            cache.reset_mock()
            cache.get_value.return_value=None
            for invalid in [dict(valid,rate_date='2026-10-31'),dict(valid,ok=False),dict(valid,cny_per_unit='NaN'),dict(valid,cny_per_unit=0),dict(valid,raw={})]:
                fetch.return_value=invalid
                with self.assertRaises(ValueError): fx.load_month_rate('MXN','2026-11-01')
            cache.set_value.assert_not_called()
            cache.get_value.return_value='.35'
            fetch.reset_mock()
            self.assertEqual(fx.load_month_rate('MXN','2026-11-01'), Decimal('.35'))
            fetch.assert_not_called()
