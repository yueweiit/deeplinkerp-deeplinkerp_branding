"""Historical valuation tasks keep working after their warehouse is retired."""

import importlib
import unittest
from contextlib import nullcontext
from copy import deepcopy
from unittest.mock import Mock, patch

import frappe

from deeplinkerp_branding.tests._purchase_test_support import install_native_throw


class HistoricalRepostWarehouseTests(unittest.TestCase):
	def setUp(self):
		self.native = importlib.import_module("deeplinkerp_branding.services.purchase_native_repost")
		self.boundary = self.native.boundary
		self.service = importlib.import_module("deeplinkerp_branding.services.purchase_payment_service")
		self.scope = importlib.import_module("deeplinkerp_branding.services.purchase_reversal_scope")
		install_native_throw(self)
		self.old = frappe._dict(
			doctype="Repost Item Valuation", name="HISTORICAL", docstatus=1, status="Queued",
			based_on="Item and Warehouse", company="C", item_code="A", warehouse="W",
			posting_date="2026-10-02", posting_time="10:00:00",
		)
		self.warehouse = frappe._dict(company="C", is_group=0, disabled=1)
		self.writes = []
		writes = self.writes

		class NativeTask(frappe._dict):
			def db_set(self, field, value):
				writes.append((field, value))

		class Task(self.boundary.PointerBoundary, NativeTask):
			pass

		self.doc = Task(self.old)
		self.collect = Mock(return_value=object())
		self.pending = Mock()
		for guard in (
			patch.object(self.boundary, "execution", side_effect=lambda: nullcontext()),
			patch.object(self.boundary, "acquire", side_effect=lambda *a, **k: nullcontext()),
			patch.object(self.boundary, "fence_key", return_value="fence"),
			patch.object(self.boundary, "_persisted", side_effect=lambda *a, **k: self.old),
			patch.object(self.boundary, "_creates_identity", return_value=False),
			patch.object(self.boundary, "check_pointer"),
			patch.object(self.boundary, "require_publication_order"),
			patch.object(self.boundary, "_scope_keys", return_value={"pair"}),
			patch.object(self.boundary, "_check_pending", self.pending),
			patch.object(self.native.progress, "accepting", return_value=None),
			patch.object(self.native.progress, "owner", return_value=None),
			patch.object(self.service, "current_reads", side_effect=lambda: nullcontext()),
			patch.object(self.service, "_read", side_effect=self.read),
			patch.object(self.scope, "collect_repost_pair_scope", self.collect),
		):
			guard.start()
			self.addCleanup(guard.stop)

	def read(self, doctype, name, fields=()):
		if doctype == "Warehouse":
			return self.warehouse
		return frappe._dict(stock_uom="Nos", is_stock_item=1)

	def test_native_status_write_for_submitted_history_accepts_retired_warehouse(self):
		from erpnext.stock.doctype.repost_item_valuation.repost_item_valuation import RepostItemValuation

		RepostItemValuation.set_status(self.doc, "In Progress")
		self.assertEqual(self.writes, [("status", "In Progress")])
		self.assertEqual(self.warehouse.disabled, 1)
		self.pending.assert_called_once()
		self.assertTrue(all(call.kwargs.get("allow_disabled_warehouses") for call in self.collect.call_args_list))

	def test_existing_failed_task_can_enter_native_restart_boundary(self):
		self.old.status = self.doc.status = "Failed"
		with self.native.task_boundary(self.doc):
			self.writes.append("native restart")
		self.assertEqual(self.writes, ["native restart"])
		self.pending.assert_called_once()

	def test_db_set_cannot_change_repost_options_before_validation(self):
		for field, value in (("allow_negative_stock", 1), ("allow_zero_rate", 1),
			("reposting_reference", "OTHER")):
			for update in (field, {field: value, "status": "In Progress"}):
				with self.subTest(field=field, update=update):
					with self.assertRaises(frappe.PermissionError):
						self.doc.db_set(update, value)
		self.assertEqual(self.writes, [])
		self.collect.assert_not_called()

	def test_existing_task_with_other_pending_reversals_keeps_full_scope_check(self):
		callback = Mock()
		db = Mock()
		db.get_values.return_value = [("PROTECTED_BIN",)]
		with patch.object(frappe, "db", db), patch.object(frappe, "get_doc", return_value=self.old):
			self.native._unowned(self.doc, callback)
		self.collect.assert_called_once_with(self.old, allow_disabled_warehouses=True)
		self.pending.assert_called_once()
		callback.assert_called_once_with(self.old)

	def test_stale_task_with_pending_reversal_cannot_reach_native_executor(self):
		callback = Mock()
		db = Mock()
		db.get_values.return_value = [("PROTECTED_BIN",)]
		self.pending.side_effect = frappe.ValidationError("pending reversal")
		with patch.object(frappe, "db", db), patch.object(frappe, "get_doc", return_value=self.old):
			with self.assertRaises(frappe.ValidationError):
				self.native._unowned(self.doc, callback)
		callback.assert_not_called()

	def test_new_and_draft_tasks_still_reject_retired_warehouse(self):
		for creating, status in ((True, 1), (False, 0)):
			with self.subTest(creating=creating, status=status):
				self.old.docstatus = self.doc.docstatus = status
				with patch.object(self.boundary, "_creates_identity", return_value=creating):
					with self.assertRaises(frappe.ValidationError):
						self.doc.db_set("status", "In Progress")
		self.assertEqual(self.writes, [])

	def test_changing_a_historical_task_does_not_grant_retired_warehouse_access(self):
		for field, value in (("item_code", "B"), ("posting_date", "2026-10-01"),
			("recreate_stock_ledgers", 1), ("recalculate_valuation_rate", 1), ("warehouse", "OTHER"),
			("allow_negative_stock", 1), ("allow_zero_rate", 1), ("reposting_reference", "OTHER")):
			with self.subTest(field=field):
				self.doc.clear()
				self.doc.update(self.old)
				self.doc[field] = value
				with self.assertRaises(frappe.ValidationError):
					with self.native.task_boundary(self.doc):
						self.fail("Changed historical task reached writer")
		self.assertEqual(self.writes, [])

	def test_company_mismatch_and_group_warehouse_remain_rejected(self):
		for field, value in (("company", "OTHER"), ("is_group", 1)):
			with self.subTest(field=field):
				self.warehouse.update(company="C", is_group=0, disabled=1)
				self.warehouse[field] = value
				self.doc.update(self.old)
				with self.assertRaises(frappe.ValidationError):
					self.doc.db_set("status", "In Progress")
		self.assertEqual(self.writes, [])

	def test_identity_is_rechecked_after_acquiring_scope_locks(self):
		changed = deepcopy(self.old)
		changed.posting_date = "2026-09-01"
		with patch.object(self.boundary, "_persisted", side_effect=[self.old, changed]):
			with self.assertRaises(frappe.ValidationError):
				self.doc.db_set("status", "In Progress")
		self.assertEqual(self.writes, [])

	def test_pending_reversal_and_read_permissions_still_block_writes(self):
		with patch.object(self.pending, "side_effect", frappe.ValidationError("pending")):
			with self.assertRaises(frappe.ValidationError):
				self.doc.db_set("status", "In Progress")
		with patch.object(self.service, "_read", side_effect=frappe.PermissionError):
			with self.assertRaises(frappe.PermissionError):
				self.doc.db_set("status", "In Progress")
		self.assertEqual(self.writes, [])
