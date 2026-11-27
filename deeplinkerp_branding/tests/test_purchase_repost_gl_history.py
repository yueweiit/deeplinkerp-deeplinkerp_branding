"""Native separate-GL dispatch against retired warehouses, without business writes."""

import unittest
from contextlib import nullcontext
from copy import deepcopy
from unittest.mock import Mock, patch

import erpnext
import frappe

from deeplinkerp_branding.services import purchase_native_repost as native
from deeplinkerp_branding.tests import test_purchase_reversal_scope as scope_fixtures
from erpnext.stock.doctype.repost_item_valuation import repost_item_valuation as riv


class HistoricalGLRepostTests(unittest.TestCase):
	def setUp(self):
		# Reuse the scope suite's permission/SLE fixtures, but retain the real
		# collector, task guards, native child dispatcher and parent status flow.
		self.fixture = scope_fixtures.ReversalScopeTests()
		self.fixture.setUp()
		self.addCleanup(self.fixture.doCleanups)
		self.source = self.fixture.add(scope_fixtures.document(
			"Purchase Receipt", "PR", items=[scope_fixtures.item("R")],
		))
		self.fixture.sle(self.source, self.source.items[0])
		self.fixture.docs["Warehouse", "W1"] = scope_fixtures.document(
			"Warehouse", "W1", disabled=1, is_group=0,
		)
		self.reposts = {}
		self.child_errors = []
		self.statuses = []
		self.fail_child = False
		self.fail_dispatch = False
		self.native_gl_calls = []
		self.pending = Mock()
		case = self

		class NativeTask(frappe._dict):
			def db_set(self, field, value):
				self[field] = value
				if field == "status":
					case.statuses.append((self.name, value))
				self.db_update()

			def db_update(self):
				case.reposts[self.name] = frappe._dict(deepcopy(dict(self)))

			def set_status(self, status=None, write=True):
				return riv.RepostItemValuation.set_status(self, status, write)

			def log_error(self, title):
				case.assertEqual(title, "Unable to repost item valuation")

			def submit(self):
				try:
					if case.fail_child:
						raise frappe.ValidationError("native child validation failed")
					self.docstatus = 1
					with native.task_boundary(self, creating=True):
						self.name = "GL-" + str(len(case.reposts))
						self.status = "Queued"
						self.db_update()
				except Exception as exc:
					case.child_errors.append(str(exc))
					raise

		class Task(native.boundary.PointerBoundary, NativeTask):
			pass

		self.Task = Task
		self.parent = Task(
			doctype="Repost Item Valuation", name="ROOT", company="C", docstatus=1, status="Queued",
			based_on="Item and Warehouse", item_code="A", warehouse="W1", posting_date="2026-01-10",
			posting_time="12:00:00", repost_only_accounting_ledgers=0,
		)
		self.reposts["ROOT"] = frappe._dict(deepcopy(dict(self.parent)))
		self.original_gl = riv.repost_gl_entries
		guards = (
			patch.object(native.boundary, "execution", side_effect=lambda: nullcontext()),
			patch.object(native.boundary, "acquire", side_effect=lambda *a, **k: nullcontext()),
			patch.object(native.boundary, "fence_key", return_value="fence"),
			patch.object(native.boundary, "_persisted", side_effect=self.persisted),
			patch.object(native.boundary, "require_publication_order"),
			patch.object(native.boundary, "_scope_keys", return_value={"scope"}),
			patch.object(native.boundary, "_check_pending", self.pending),
			patch.object(native.progress, "owner", return_value=None),
			patch.object(native.progress, "accepting", return_value=None),
			patch.object(self.fixture.scope.service, "current_reads", side_effect=lambda: nullcontext()),
			patch.object(self.fixture.scope.service, "_read", side_effect=self.read),
			patch.object(frappe, "get_doc", side_effect=lambda dt, name, **kw: self.read(dt, name)),
			patch.object(frappe, "new_doc", side_effect=self.new_task),
			patch.object(frappe, "flags", frappe._dict()),
			patch.object(frappe, "in_test", True),
			patch.object(frappe.db, "MAX_WRITES_PER_TRANSACTION", 200000),
			patch.object(frappe.db, "exists", side_effect=lambda dt, name=None, **kw:
				(dt == "Repost Item Valuation" and isinstance(name, str) and name in self.reposts)
				or (dt == "Purchase Receipt" and name == "PR")),
			patch.object(frappe.db, "get_values", side_effect=self.rows),
			patch.object(frappe.db, "get_single_value", side_effect=self.setting),
			patch.object(erpnext, "is_perpetual_inventory_enabled", return_value=True),
			patch.object(riv, "get_existing_reposting_only_gl_entries", return_value={}),
			patch.object(riv, "_get_directly_dependent_vouchers", return_value=[("Purchase Receipt", "PR")]),
			patch.object(riv, "get_affected_transactions", return_value=set()),
			patch.object(riv, "repost_sl_entries", Mock()),
			patch.object(riv, "remove_attached_file", Mock()),
			patch.object(riv, "_dlp_reversal_original_repost_gl_entries", self.dispatch, create=True),
			patch.object(riv, "repost_gl_entries", native.repost_gl),
		)
		for guard in guards:
			guard.start()
			self.addCleanup(guard.stop)
		self.addCleanup(lambda: self.assertIsNone(native._gl_creation.get()))

	def persisted(self, doc, **kwargs):
		return deepcopy(self.reposts.get(doc.name))

	def read(self, doctype, name, fields=()):
		if doctype == "Repost Item Valuation":
			return deepcopy(self.reposts[name])
		return self.fixture.read(doctype, name, fields)

	def rows(self, doctype, filters, fields, **kwargs):
		if doctype == "Repost Item Valuation":
			self.assertTrue(kwargs.get("for_update"))
			return [deepcopy(row) for row in self.reposts.values()
				if all(row.get(key) == value for key, value in filters.items())][:kwargs["limit"]]
		kwargs.pop("for_update", None)
		return self.fixture.rows(doctype, filters, fields, **kwargs)

	def setting(self, doctype, field, **kwargs):
		return int(field == "enable_separate_reposting_for_gl")

	def new_task(self, doctype):
		return self.Task(doctype=doctype, based_on="Transaction", docstatus=0,
			flags=frappe._dict(), allow_negative_stock=1, allow_zero_rate=0,
			posting_date="2026-01-10", posting_time="12:00:00")

	def dispatch(self, doc):
		self.native_gl_calls.append(doc.name)
		if self.fail_dispatch:
			raise RuntimeError("native GL failed")
		return self.original_gl(doc)

	def test_native_parent_completes_only_after_gl_child_exists_and_child_can_execute(self):
		riv.repost(self.parent)
		self.assertEqual(self.child_errors, [])
		self.assertEqual(self.parent.status, "Completed")
		child = self.Task(self.reposts["GL-1"])
		self.assertEqual(child.status, "Queued")
		self.assertEqual(child.reposting_reference, "ROOT")
		callback = Mock()
		native._unowned(child, callback)
		callback.assert_called_once()
		# Its subsequent status writes use the durable parent reference after
		# the creation context has gone away, retaining all pending guards.
		child.set_status("In Progress")
		child.set_status("Completed")
		self.assertTrue(self.pending.called)
		self.assertEqual(self.fixture.docs["Warehouse", "W1"].disabled, 1)

	def test_swallowed_native_child_failure_never_marks_parent_completed(self):
		self.fail_child = True
		with self.assertRaises(frappe.ValidationError) as error:
			riv.repost(self.parent)
		self.assertEqual(error.exception.purchase_error_id, "stock_repost_gl_children_incomplete")
		self.assertEqual(self.child_errors, ["native child validation failed"])
		self.assertNotIn(("ROOT", "Completed"), self.statuses)

	def test_native_production_error_path_records_failed_without_sending_mail(self):
		self.fail_child = True

		def write_failure(doctype, name, values):
			self.assertEqual(doctype, "Repost Item Valuation")
			self.reposts[name].update(values)

		with patch.object(frappe, "in_test", False), patch.object(frappe, "message_log", []), \
			patch.object(frappe, "get_traceback", return_value="GL child creation failed"), \
			patch.object(frappe, "get_cached_value", return_value=None), \
			patch.object(frappe.db, "set_value", side_effect=write_failure), \
			patch.object(frappe.db, "commit"), patch.object(frappe, "sendmail") as sendmail:
			riv.repost(self.parent)
			sendmail.assert_not_called()
		self.assertEqual(self.reposts["ROOT"].status, "Failed")
		self.assertNotIn(("ROOT", "Completed"), self.statuses)

	def test_partial_child_creation_cannot_complete_parent(self):
		second = self.fixture.add(scope_fixtures.document(
			"Purchase Receipt", "PR2", items=[scope_fixtures.item("R2")],
		))
		self.fixture.sle(second, second.items[0])
		original = self.new_task

		def fail_second(doctype):
			if len(self.reposts) > 1:
				self.fail_child = True
			return original(doctype)

		with patch.object(riv, "_get_directly_dependent_vouchers", return_value=[
			("Purchase Receipt", "PR"), ("Purchase Receipt", "PR2"),
		]), patch.object(frappe, "new_doc", side_effect=fail_second):
			with self.assertRaises(frappe.ValidationError) as error:
				riv.repost(self.parent)
		self.assertEqual(error.exception.purchase_error_id, "stock_repost_gl_children_incomplete")
		self.assertEqual(set(self.reposts), {"ROOT", "GL-1"})
		self.assertNotIn(("ROOT", "Completed"), self.statuses)

	def test_old_completed_or_wrong_company_children_cannot_cover_missing_work(self):
		for status, company in (("Completed", "C"), ("Skipped", "C"), ("Queued", "OTHER")):
			with self.subTest(status=status, company=company):
				self.reposts["STALE"] = frappe._dict(name="STALE", company=company, docstatus=1,
					based_on="Transaction", repost_only_accounting_ledgers=1, reposting_reference="ROOT",
					voucher_type="Purchase Receipt", voucher_no="PR", status=status)
				self.fail_child = True
				with self.assertRaises(frappe.ValidationError):
					riv.repost(self.parent)
		self.assertNotIn(("ROOT", "Completed"), self.statuses)

	def test_plain_new_gl_task_cannot_forge_historical_parent_authority(self):
		child = self.new_task("Repost Item Valuation")
		child.update(company="C", voucher_type="Purchase Receipt", voucher_no="PR",
			repost_only_accounting_ledgers=1, reposting_reference="ROOT")
		with self.assertRaises(frappe.ValidationError):
			child.submit()
		self.assertEqual(set(self.reposts), {"ROOT"})

	def test_dispatch_exception_clears_creation_authority(self):
		self.fail_dispatch = True
		with self.assertRaisesRegex(RuntimeError, "native GL failed"):
			riv.repost(self.parent)
		self.assertIsNone(native._gl_creation.get())
		self.test_plain_new_gl_task_cannot_forge_historical_parent_authority()

	def test_non_perpetual_and_inline_gl_keep_native_dispatch(self):
		with patch.object(erpnext, "is_perpetual_inventory_enabled", return_value=False):
			riv.repost(self.parent)
		self.assertEqual(set(self.reposts), {"ROOT"})
		with patch.object(frappe.db, "get_single_value", return_value=0), patch.object(
			riv, "repost_gle_for_stock_vouchers"
		) as inline:
			riv.repost(self.parent)
		inline.assert_called_once()
		self.assertEqual(self.native_gl_calls, ["ROOT", "ROOT"])

	def test_parent_identity_is_rechecked_during_native_child_creation(self):
		original = self.new_task

		def changed_parent(doctype):
			self.reposts["ROOT"].item_code = "CHANGED"
			return original(doctype)

		with patch.object(frappe, "new_doc", side_effect=changed_parent):
			with self.assertRaises(frappe.ValidationError):
				riv.repost(self.parent)
		self.assertEqual(set(self.reposts), {"ROOT"})
		self.assertNotIn(("ROOT", "Completed"), self.statuses)

	def test_native_scope_expansion_is_not_hidden_by_original_children_existing(self):
		self.fixture.add(scope_fixtures.document(
			"Purchase Receipt", "UNEXPECTED", items=[scope_fixtures.item("R2")],
		))
		with patch.object(riv, "_get_directly_dependent_vouchers", side_effect=[
			[("Purchase Receipt", "PR")],
			[("Purchase Receipt", "PR"), ("Purchase Receipt", "UNEXPECTED")],
		]):
			with self.assertRaises(frappe.ValidationError) as error:
				riv.repost(self.parent)
		self.assertEqual(error.exception.purchase_error_id, "stock_repost_gl_children_incomplete")
		self.assertEqual(self.reposts["GL-1"].voucher_no, "PR")
		self.assertEqual(set(self.reposts), {"ROOT", "GL-1"})
		self.assertNotIn(("ROOT", "Completed"), self.statuses)

	def test_child_company_parent_status_and_stock_options_remain_guarded(self):
		riv.repost(self.parent)
		original = deepcopy(self.reposts["GL-1"])
		for field, value in (("recreate_stock_ledgers", 1),
			("recalculate_valuation_rate", 1), ("via_landed_cost_voucher", 1),
			("reposting_reference", "MISSING")):
			with self.subTest(field=field):
				child = self.Task(original)
				child[field] = value
				with self.assertRaises((frappe.ValidationError, KeyError)):
					with native.task_boundary(child):
						self.fail("Changed child reached native writer")
		# As in the native controller, company is canonicalized from the voucher.
		child = self.Task(original, company="OTHER")
		with native.task_boundary(child):
			self.assertEqual(child.company, "C")
		self.reposts["ROOT"].company = "OTHER"
		with self.assertRaises(frappe.ValidationError):
			self.Task(original).set_status("In Progress")
		self.reposts["ROOT"].company = "C"
		self.reposts["ROOT"].docstatus = 2
		with self.assertRaises(frappe.ValidationError):
			self.Task(original).set_status("In Progress")

	def test_pending_reversal_blocks_child_executor(self):
		riv.repost(self.parent)
		self.pending.side_effect = frappe.ValidationError("pending reversal")
		callback = Mock()
		with self.assertRaises(frappe.ValidationError):
			native._unowned(self.Task(self.reposts["GL-1"]), callback)
		callback.assert_not_called()
