"""Exercise the public Sales summary against native-shaped, permission-filtered reads."""

import importlib.util
import sys
import types
import unittest
from contextvars import ContextVar
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch


class NativeRow(types.SimpleNamespace):
	def get(self, field):
		return getattr(self, field, None)


class SalesSummaryTests(unittest.TestCase):
	def setUp(self):
		self.fields = {"qty", "uom", "item_name", "rate"}
		self.docs = {}
		frappe = types.ModuleType("frappe")
		frappe.PermissionError = type("PermissionError", (Exception,), {})
		frappe.DoesNotExistError = type("DoesNotExistError", (Exception,), {})
		frappe.whitelist = lambda: lambda method: method
		frappe.has_permission = lambda doctype, permission, **kwargs: True
		frappe.throw = lambda message, error=None: (_ for _ in ()).throw((error or Exception)(message))
		frappe._ = lambda message: message
		self.frappe = frappe
		model = types.ModuleType("frappe.model")
		model.get_permitted_fields = lambda *args, **kwargs: self.fields
		utils = types.ModuleType("frappe.utils")
		utils.strip_html = lambda value: value
		reader = types.ModuleType("deeplinkerp_branding.services.purchase_payment_service")
		reader._record_reader = ContextVar("sales_summary_test_reader", default=None)
		docs = self.docs

		class RecordReader:
			def __init__(self):
				self.docs = {}

			def preload(self, doctype, names):
				if doctype == "Sales Order":
					self.docs.update({(doctype, name): docs.get(name) for name in names})

		def require_fields(doctype, fields, parenttype=None):
			if "sales_team" in fields:
				raise frappe.PermissionError

		def read(doctype, name, fields):
			if name not in docs:
				raise frappe.PermissionError
			return docs[name]

		reader._RecordReader = RecordReader
		reader._require_fields = require_fields
		reader._read = read
		path = Path(__file__).parents[1] / "deeplinkerp_branding/services/compact_sales_service.py"
		spec = importlib.util.spec_from_file_location("compact_sales_summary_test_target", path)
		self.sales = importlib.util.module_from_spec(spec)
		with patch.dict(
			sys.modules,
			{"frappe": frappe, "frappe.model": model, "frappe.utils": utils, reader.__name__: reader},
		):
			spec.loader.exec_module(self.sales)
		self.reader_context = reader._record_reader

	def summary(self, quantities, units, fields=None):
		if fields is not None:
			self.fields = fields
		items = [
			NativeRow(
				qty=qty,
				rate=10,
				item_name="Product",
				conversion_factor=100,
				**({"uom": units[index]} if units is not None else {}),
			)
			for index, qty in enumerate(quantities)
		]
		self.docs["SO-1"] = NativeRow(company="Company", items=items, sales_team=[])
		result = self.sales.get_sales_display_details(["SO-1", "DENIED"])
		self.assertNotIn("DENIED", result)
		self.assertIsNone(self.reader_context.get())
		return result["SO-1"]

	def test_quantity_sums_all_rows_without_unit_conversion_or_uom_permission(self):
		cases = [
			("same unit", [1, 2], ["件", "件"], 3),
			("mixed units", [1, 2], ["件", "Box"], 3),
			("no uom permission", [2.5, 1.25], None, 3.75),
			("zero", [0, 0], ["件", "Box"], 0),
			("decimals", [Decimal("1.25"), Decimal("2.50")], ["件", "Box"], Decimal("3.75")),
			("all 52 rows", list(range(1, 53)), ["件", "Box"] * 26, 1378),
		]
		for label, quantities, units, expected in cases:
			with self.subTest(label=label):
				fields = {"qty", "item_name", "rate"} | ({"uom"} if units is not None else set())
				row = self.summary(quantities, units, fields)
				self.assertEqual(row.get("dlp_quantity"), expected)
				self.assertEqual(row["dlp_item_count"], len(quantities))
				if units is None:
					self.assertNotIn("dlp_uom", row)
					self.assertNotIn("dlp_multiple_units", row)

	def test_quantity_is_absent_when_native_qty_permission_is_denied(self):
		for fields in ({"uom", "rate", "item_name"}, {"item_name"}, set()):
			with self.subTest(fields=fields):
				row = self.summary([100], ["件"], fields)
				self.assertNotIn("dlp_quantity", row)
				if "rate" in fields:
					self.assertEqual(row["dlp_rate"], 10, "legacy public response remains compatible")

	def test_payment_action_requires_submitted_order_and_payment_entry_create_permission(self):
		self.docs["SO-1"] = NativeRow(name="SO-1", company="Company", docstatus=0, items=[], sales_team=[])
		self.assertFalse(self.sales.get_sales_order_details("SO-1")["can_create_payment_entry"])
		self.docs["SO-1"].docstatus = 1
		with patch.object(self.frappe, "has_permission", side_effect=lambda doctype, permission, **kwargs: doctype != "Payment Entry"):
			self.assertFalse(self.sales.get_sales_order_details("SO-1")["can_create_payment_entry"])
			with self.assertRaises(self.frappe.PermissionError):
				self.sales.make_sales_order_payment_entry("SO-1")
		self.assertTrue(self.sales.get_sales_order_details("SO-1")["can_create_payment_entry"])

	def test_payment_action_uses_native_sales_order_mapping_without_saving(self):
		self.docs["SO-1"] = NativeRow(name="SO-1", company="Company", docstatus=1, items=[], sales_team=[])
		mapped = NativeRow(doctype="Payment Entry", name="new-payment-entry")
		mapper = Mock(return_value=mapped)
		modules = {}
		for name in ("erpnext", "erpnext.accounts", "erpnext.accounts.doctype", "erpnext.accounts.doctype.payment_entry"):
			modules[name] = types.ModuleType(name)
		modules["erpnext.accounts.doctype.payment_entry.payment_entry"] = types.ModuleType("payment_entry")
		modules["erpnext.accounts.doctype.payment_entry.payment_entry"].get_payment_entry = mapper
		with patch.dict(sys.modules, modules):
			self.assertIs(self.sales.make_sales_order_payment_entry("SO-1"), mapped)
		mapper.assert_called_once_with("Sales Order", "SO-1")


if __name__ == "__main__":
	unittest.main()
