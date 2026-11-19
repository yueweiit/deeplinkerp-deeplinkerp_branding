import importlib
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


class CashJournalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = importlib.import_module(
            "deeplinkerp_branding.deeplinkerp_branding.doctype.cn_cash_journal_entry.cn_cash_journal_entry")

    def setUp(self):
        self.company = SimpleNamespace(name="CN A", is_group=0, default_currency="CNY", check_permission=Mock())
        self.doc = SimpleNamespace(company="CN A", direction="expense", summary=" Rent ",
                                   dingtalk_approval_number=" 202611190001 ", currency="CNY", amount=12.34)

    def validate(self):
        with patch.object(self.module, "_", side_effect=lambda text:text), \
                patch.object(self.module.frappe, "get_doc", return_value=self.company) as read, \
                patch.object(self.module.frappe, "db", SimpleNamespace(exists=lambda dt, value: value in {"CNY", "USD"})), \
                patch.object(self.module.frappe, "throw", side_effect=lambda text: (_ for _ in ()).throw(ValueError(text))):
            self.module.CNCashJournalEntry.validate(self.doc)
        return read

    def test_income_and_expense_need_no_account_and_keep_selected_currency(self):
        for direction in ("income", "expense"):
            self.doc.direction = direction
            self.doc.currency = "USD"
            read = self.validate()
            read.assert_called_once_with("Company", "CN A")
            self.assertEqual(self.doc.amount, "12.34")
            self.assertEqual(self.doc.currency, "USD")
            self.assertEqual(self.doc.summary, "Rent")
            self.assertEqual(self.doc.dingtalk_approval_number, "202611190001")
        self.company.check_permission.assert_called_with("read")

    def test_optional_description_approval_number_and_currency_fallback(self):
        self.doc.summary = self.doc.dingtalk_approval_number = self.doc.currency = None
        self.validate()
        self.assertEqual(self.doc.summary, "")
        self.assertEqual(self.doc.dingtalk_approval_number, "")
        self.assertEqual(self.doc.currency, "CNY")

    def test_invalid_amounts_and_currency_serialization_loss_are_rejected(self):
        for value in (0, -1, "NaN", "Infinity", None, True, "12.345", "100000000000000", "99999999999999.99"):
            with self.subTest(value=value):
                self.doc.amount = value
                with self.assertRaisesRegex(ValueError, "Amount must"):
                    self.validate()

    def test_invalid_direction_currency_or_nontext_fields_are_rejected(self):
        for field, value in (("currency", "FAKE"), ("direction", "收入"), ("direction", ["income"]),
                             ("dingtalk_approval_number", 123), ("summary", {"value": "bad"})):
            before = getattr(self.doc, field)
            setattr(self.doc, field, value)
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate()
            setattr(self.doc, field, before)

    def test_unreadable_company_cannot_be_used(self):
        self.company.check_permission.side_effect = PermissionError("denied")
        with self.assertRaises(PermissionError):
            self.validate()

    def test_group_company_cannot_be_used(self):
        self.company.is_group = 1
        with self.assertRaisesRegex(ValueError, "non-group"):
            self.validate()

    def test_schema_keeps_company_permissions_and_legacy_data_without_account_requirement(self):
        schema = json.loads(Path(self.module.__file__).with_suffix(".json").read_text())
        self.assertFalse(schema["is_submittable"])
        self.assertEqual(schema["track_changes"], 1)
        fields = {field["fieldname"]:field for field in schema["fields"]}
        self.assertFalse(fields["company"].get("ignore_user_permissions"))
        self.assertFalse(fields["account"]["reqd"])
        self.assertTrue(fields["account"]["hidden"])
        self.assertFalse(fields["dingtalk_approval_number"].get("reqd"))
        self.assertFalse(fields["dingtalk_approval_number"].get("unique"))
        self.assertEqual(fields["attachment"]["fieldtype"], "Attach")
        self.assertTrue(all(not row.get("submit") and not row.get("cancel") for row in schema["permissions"]))
