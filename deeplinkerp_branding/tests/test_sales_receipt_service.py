"""Customer receipt scope, native draft writes, retries and attachment ownership."""

import unittest
from contextlib import ExitStack, nullcontext
from unittest.mock import Mock, patch

import frappe

from deeplinkerp_branding.services import sales_receipt_service as receipts
from deeplinkerp_branding.tests._purchase_test_support import install_native_throw, install_request_state

NATIVE_RUN = receipts.purchase_operation.run


class NativeDraft(frappe._dict):
	def is_new(self):
		return not self.name

	def precision(self, field):
		return 2


class SalesReceiptTests(unittest.TestCase):
	def setUp(self):
		install_native_throw(self)
		install_request_state(self)
		self.order = frappe._dict(doctype="Sales Order", name="SO-1", modified="v1", docstatus=1,
			company="C", customer="CUSTOMER", currency="MXN", grand_total=100, advance_paid=10, status="To Deliver and Bill")
		self.doc = NativeDraft(doctype="Payment Entry", name="PE-1", modified="p1", docstatus=0,
			company="C", company_currency="MXN", party="CUSTOMER", party_type="Customer", payment_type="Receive", posting_date="2026-10-26",
			paid_from="Debtors", paid_to="Bank", paid_from_account_currency="MXN", paid_to_account_currency="MXN",
			paid_amount=20, received_amount=20, references=[frappe._dict(doctype="Payment Entry Reference",
				reference_doctype="Sales Order", reference_name="SO-1", allocated_amount=20)],
			unallocated_amount=0, difference_amount=0, deductions=[], mode_of_payment=None,
			reference_no=None, reference_date=None, remarks=None, custom_remarks=0)
		self.doc.has_permission = Mock(return_value=True)
		self.doc.check_permission = Mock()
		self.doc.insert = Mock(side_effect=lambda: self.doc.update(name="NEW-PE", modified="p2"))
		self.doc.save = Mock(side_effect=lambda: self.doc.update(modified="p2"))
		self.doc.submit = Mock(side_effect=lambda: self.doc.update(docstatus=1, modified="p3"))
		self.doc.set_exchange_rate = Mock()
		self.doc.set_amounts = Mock()
		self.values = dict(amount="25.25", posting_date="2026-10-26", bank_account="Bank",
			mode_of_payment="Bank Transfer", reference_no="REF", reference_date="2026-10-25", remarks="Receipt")
		self.stack = self.enterContext(ExitStack())
		self.stack.enter_context(patch.object(receipts, "_", side_effect=lambda value: value))
		self.stack.enter_context(patch.object(frappe, "has_permission", return_value=True))
		self.stack.enter_context(patch.object(frappe, "logger", return_value=Mock()))
		self.stack.enter_context(patch.object(frappe, "get_system_settings", return_value="Banker's Rounding"))
		self.stack.enter_context(patch("frappe.translate.get_all_translations", return_value={}))
		self.stack.enter_context(patch("frappe.model.workflow.get_workflow_name", return_value=None))
		self.read = self.stack.enter_context(patch.object(receipts.service, "_read", side_effect=self.read_doc))
		self.stack.enter_context(patch.object(receipts.service, "_current", side_effect=self.read_doc))
		self.stack.enter_context(patch.object(receipts.service, "_require_fields"))
		self.bank = self.stack.enter_context(patch.object(receipts.service, "_bank_account",
			return_value=frappe._dict(name="Bank", account_currency="MXN", account_type="Bank")))
		self.fields = self.stack.enter_context(patch.object(receipts, "_fields"))
		self.stack.enter_context(patch.object(receipts, "_editable_fields", side_effect=lambda doc, fields, *a: list(fields)))
		self.drafts = self.stack.enter_context(patch.object(receipts, "_drafts", return_value=[]))
		self.mapper = self.stack.enter_context(patch.object(receipts, "_new_entry", return_value=self.doc))
		self.bind = self.stack.enter_context(patch.object(receipts.files, "bind"))
		self.stack.enter_context(patch.object(receipts.files, "list_attachments", return_value=[]))
		self.kernel = self.stack.enter_context(patch.object(receipts.purchase_operation, "run",
			side_effect=lambda request_id, payload, operation, replay, **kwargs: operation()))
		self.stack.enter_context(patch("deeplinkerp_branding.services.purchase_repost_boundary.execution", return_value=nullcontext()))

	def read_doc(self, doctype, name, *args, **kwargs):
		return self.order if doctype == "Sales Order" else self.doc if doctype == "Payment Entry" else frappe._dict(name=name)

	def save(self, **overrides):
		args = dict(sales_order="SO-1", values=self.values, request_id="12345678-1234-1234-1234-123456789abc",
			payment_entry="PE-1", expected_modified="p1", order_modified="v1")
		args.update(overrides)
		return receipts.save_receipt_draft(**args)

	def submit(self, **overrides):
		args = dict(sales_order="SO-1", payment_entry="PE-1", expected_modified="p1", order_modified="v1",
			request_id="12345678-1234-1234-1234-123456789abc")
		args.update(overrides)
		return receipts.submit_receipt(**args)

	def test_submit_permission_is_projected_only_for_saved_simple_drafts(self):
		self.assertTrue(receipts.get_receipt_context("SO-1", "PE-1")["can_submit"])
		self.doc.has_permission.side_effect = lambda permission: permission != "submit"
		self.assertFalse(receipts.get_receipt_context("SO-1", "PE-1")["can_submit"])
		self.doc.has_permission.side_effect = None
		self.doc.name = None
		self.assertFalse(receipts.get_receipt_context("SO-1", new_receipt=1)["can_submit"])

	def test_submission_uses_existing_native_document_and_never_creates_or_binds_files(self):
		result = self.submit()
		self.doc.submit.assert_called_once_with()
		self.doc.check_permission.assert_any_call("write")
		self.doc.check_permission.assert_any_call("submit")
		self.doc.insert.assert_not_called()
		self.doc.save.assert_not_called()
		self.bind.assert_not_called()
		self.assertEqual(result["document"]["docstatus"], 1)
		self.assertFalse(result["can_submit"])

	def test_current_order_balance_blocks_submission_even_when_modified_timestamp_is_unchanged(self):
		self.order.advance_paid = 95
		with self.assertRaises(frappe.ValidationError):
			self.submit()
		self.doc.submit.assert_not_called()

	def test_rounded_order_balance_matches_native_mapper_for_save_and_submit(self):
		from erpnext.accounts.doctype.payment_entry.payment_entry import set_grand_total_and_outstanding_amount

		for grand_total, rounded_total in ((99.60, 100), (100.40, 100), (99.60, 0)):
			with self.subTest(grand_total=grand_total, rounded_total=rounded_total):
				self.order.update(company_currency="MXN", grand_total=grand_total, base_grand_total=grand_total,
					rounded_total=rounded_total, base_rounded_total=rounded_total, advance_paid=10)
				_, remaining = set_grand_total_and_outstanding_amount(None, "Sales Order", "MXN", self.order)
				self.doc.update(docstatus=0, modified="p1")
				self.save(values={**self.values, "amount": str(remaining)})
				self.assertEqual(self.doc.references[0].allocated_amount, remaining)
				self.assertEqual(self.submit(expected_modified="p2")["document"]["docstatus"], 1)

	def test_rounding_down_does_not_allow_allocation_above_native_remaining(self):
		self.order.update(company_currency="MXN", grand_total=100.40, base_grand_total=100.40,
			rounded_total=100, base_rounded_total=100, advance_paid=10)
		with self.assertRaises(frappe.ValidationError):
			self.save(values={**self.values, "amount": "90.01"})
		self.doc.references[0].allocated_amount = 90.01
		with self.assertRaises(frappe.ValidationError):
			self.submit()
		self.doc.save.assert_not_called()
		self.doc.submit.assert_not_called()

	def test_actions_do_not_expose_or_interrupt_closed_and_restricted_orders(self):
		self.order.status = "Closed"
		self.assertEqual(receipts.get_receipt_actions("SO-1"), {})
		self.order.status = "To Deliver and Bill"
		self.read.side_effect = frappe.PermissionError
		self.assertEqual(receipts.get_receipt_actions("SO-1"), {})

	def test_submit_checks_versions_permissions_and_native_failure(self):
		for field in ("expected_modified", "order_modified"):
			with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
				self.submit(**{field: "old"})
		self.doc.submit.assert_not_called()
		self.doc.check_permission.side_effect = lambda permission: (_ for _ in ()).throw(frappe.PermissionError()) if permission == "submit" else None
		with self.assertRaises(frappe.PermissionError):
			self.submit()
		self.doc.submit.assert_not_called()
		self.doc.check_permission.side_effect = None
		self.doc.submit.side_effect = frappe.ValidationError("native posting refused")
		with self.assertRaises(frappe.ValidationError):
			self.submit()

	def test_advanced_closed_submitted_and_foreign_order_receipts_cannot_submit(self):
		for field, value in (("taxes", [frappe._dict()]), ("company_currency", "USD"), ("docstatus", 1),
			("company", "OTHER"), ("party", "OTHER"), ("payment_type", "Pay")):
			old = self.doc.get(field)
			self.doc[field] = value
			with self.subTest(field=field), self.assertRaises((frappe.ValidationError, frappe.PermissionError)):
				self.submit()
			self.doc[field] = old
		with patch("frappe.model.workflow.get_workflow_name", return_value="Approval"), self.assertRaises(frappe.ValidationError):
			self.submit()
		self.order.status = "On Hold"
		with self.assertRaises(frappe.ValidationError):
			self.submit()
		self.doc.submit.assert_not_called()

	def test_submission_has_durable_replay_and_changed_state_requires_review_without_resubmitting(self):
		from deeplinkerp_branding.tests.test_purchase_operation import AuditDatabase
		db = AuditDatabase()
		with (
			patch.object(receipts.purchase_operation, "run", side_effect=NATIVE_RUN),
			patch.object(frappe, "db", db), patch.object(frappe, "session", frappe._dict(user="QA")),
			patch.object(frappe, "get_doc", side_effect=db.audit),
			patch("deeplinkerp_branding.services.purchase_consistency.bind_operation"),
			patch("deeplinkerp_branding.services.purchase_consistency.check_registered"),
		):
			first = self.submit()
			second = self.submit()
			self.assertEqual(first["document"]["name"], second["document"]["name"])
			self.assertTrue(second["reused"])
			self.doc.submit.assert_called_once()
			self.doc.docstatus = 2
			third = self.submit()
			self.assertTrue(third["requires_review"])
			self.assertEqual(len(db.rows), 1)
			self.doc.submit.assert_called_once()

	def test_opening_new_form_never_inserts_saves_or_submits(self):
		self.doc.name = None
		result = receipts.get_receipt_context("SO-1")
		self.assertIsNone(result["document"]["name"])
		self.doc.insert.assert_not_called()
		self.doc.save.assert_not_called()
		self.doc.submit.assert_not_called()
		self.bind.assert_not_called()

	def test_opening_resumes_one_visible_draft_without_mapping_another(self):
		self.drafts.return_value = [{"name": "PE-1"}]
		self.assertEqual(receipts.get_receipt_context("SO-1")["document"]["name"], "PE-1")
		self.mapper.assert_not_called()

	def test_multiple_drafts_require_selection_and_explicit_new_can_map(self):
		self.drafts.return_value = [{"name": "PE-1"}, {"name": "PE-2"}]
		self.assertIsNone(receipts.get_receipt_context("SO-1")["document"])
		self.mapper.assert_not_called()
		receipts.get_receipt_context("SO-1", new_receipt=1)
		self.mapper.assert_called_once()

	def test_save_updates_same_native_draft_and_binds_attachments_without_submitting(self):
		result = self.save(attachment_session="SESSION", attachments=["F1", "F2"])
		self.doc.save.assert_called_once_with()
		self.doc.insert.assert_not_called()
		self.doc.submit.assert_not_called()
		self.assertEqual((self.doc.received_amount, self.doc.paid_amount, self.doc.references[0].allocated_amount), (25.25, 25.25, 25.25))
		self.assertEqual(self.doc.paid_from, "Debtors")
		self.assertEqual((self.doc.custom_remarks, self.doc.remarks), (1, "Receipt"))
		self.bank.assert_called_once_with("Bank", "C", "MXN", for_update=True)
		self.bind.assert_called_once_with(self.doc, "SESSION", ["F1", "F2"])
		self.assertEqual(result["document"]["modified"], "p2")

	def test_new_save_inserts_only_on_explicit_save(self):
		self.doc.name = None
		result = self.save(payment_entry=None, expected_modified=None)
		self.doc.insert.assert_called_once_with()
		self.doc.save.assert_not_called()
		self.assertEqual(result["document"]["name"], "NEW-PE")

	def test_concurrent_new_request_returns_existing_draft_for_review_without_overwriting(self):
		self.drafts.return_value = [{"name": "PE-1"}]
		result = self.save(payment_entry=None)
		self.assertTrue(result["needs_review"])
		self.assertEqual(self.doc.received_amount, 20)
		self.doc.save.assert_not_called()
		self.doc.insert.assert_not_called()
		self.bind.assert_not_called()

	def test_another_real_receipt_must_be_explicit(self):
		self.drafts.return_value = [{"name": "PE-1"}]
		self.doc.name = None
		self.save(payment_entry=None, new_receipt=1)
		self.doc.insert.assert_called_once()

	def test_stale_order_or_draft_and_submitted_draft_cannot_be_saved(self):
		for field, value in (("order_modified", "old"), ("expected_modified", "old")):
			with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
				self.save(**{field: value})
		self.doc.docstatus = 1
		with self.assertRaises(frappe.ValidationError):
			self.save()
		self.doc.save.assert_not_called()
		self.bind.assert_not_called()

	def test_no_create_permission_does_not_prevent_authorized_existing_draft_edit(self):
		with patch.object(frappe, "has_permission", return_value=False):
			with self.assertRaises(frappe.PermissionError):
				self.save(payment_entry=None)
			self.save()
		self.doc.check_permission.assert_called_with("write")

	def test_order_payment_company_customer_direction_and_reference_are_enforced(self):
		for field, value in (("company", "OTHER"), ("party", "OTHER"), ("party_type", "Supplier"), ("payment_type", "Pay")):
			old = self.doc[field]
			self.doc[field] = value
			with self.subTest(field=field), self.assertRaises(frappe.PermissionError):
				receipts.get_receipt_context("SO-1", "PE-1")
			self.doc[field] = old
		self.doc.references[0].reference_name = "OTHER-SO"
		with self.assertRaises(frappe.PermissionError):
			self.save()

	def test_foreign_currency_deductions_shared_allocations_and_workflow_use_advanced_form(self):
		for field, value in (("paid_to_account_currency", "USD"), ("deductions", [frappe._dict()]), ("unallocated_amount", 1)):
			old = self.doc[field]
			self.doc[field] = value
			with self.subTest(field=field):
				self.assertTrue(receipts.get_receipt_context("SO-1", "PE-1")["advanced_reason"])
				with self.assertRaises(frappe.ValidationError):
					self.save()
			self.doc[field] = old
		with patch("frappe.model.workflow.get_workflow_name", return_value="Approval"):
			self.assertTrue(receipts.get_receipt_context("SO-1", "PE-1")["advanced_reason"])
		self.doc.references.append(frappe._dict(self.doc.references[0], reference_name="SO-2"))
		self.assertTrue(receipts.get_receipt_context("SO-1", "PE-1")["advanced_reason"])

	def test_zero_order_and_excess_or_rounded_zero_amount_cannot_be_allocated(self):
		for value in ("90.01", "0.001"):
			with self.subTest(value=value), self.assertRaises(frappe.ValidationError):
				self.save(values={**self.values, "amount": value})
		self.order.grand_total = 0
		self.assertTrue(receipts.get_receipt_context("SO-1", "PE-1")["advanced_reason"])
		self.doc.save.assert_not_called()

	def test_bank_field_permissions_and_native_validation_are_not_bypassed(self):
		self.bank.side_effect = frappe.PermissionError("foreign account")
		with self.assertRaises(frappe.PermissionError):
			self.save()
		self.bank.side_effect = None
		self.fields.side_effect = frappe.PermissionError("field permission")
		with self.assertRaises(frappe.PermissionError):
			self.save()
		self.fields.side_effect = None
		self.doc.save.side_effect = frappe.ValidationError("native validation")
		with self.assertRaises(frappe.ValidationError):
			self.save()
		self.bind.assert_not_called()

	def test_invalid_payloads_and_nonfinite_amounts_are_rejected(self):
		for value in ("not json", [], {**self.values, "company": "OTHER"}, *({**self.values, "amount": value} for value in ("NaN", "Infinity", "-1", "0", "garbage"))):
			with self.subTest(value=value), self.assertRaises(frappe.ValidationError):
				receipts._values(value)

	def test_unreadable_order_and_write_denial_stop_before_native_save(self):
		self.read.side_effect = frappe.PermissionError("no read")
		with self.assertRaises(frappe.PermissionError):
			self.save()
		self.read.side_effect = self.read_doc
		self.doc.check_permission.side_effect = frappe.PermissionError("no write")
		with self.assertRaises(frappe.PermissionError):
			self.save()
		self.doc.save.assert_not_called()

	def test_durable_operation_replays_same_receipt_without_inserting_or_binding_again(self):
		from deeplinkerp_branding.tests.test_purchase_operation import AuditDatabase

		db = AuditDatabase()
		self.doc.name = None
		with (
			patch.object(receipts.purchase_operation, "run", side_effect=NATIVE_RUN),
			patch.object(frappe, "db", db),
			patch.object(frappe, "session", frappe._dict(user="QA")),
			patch.object(frappe, "get_doc", side_effect=db.audit),
			patch("deeplinkerp_branding.services.purchase_consistency.bind_operation"),
			patch("deeplinkerp_branding.services.purchase_consistency.check_registered"),
		):
			first = self.save(payment_entry=None)
			second = self.save(payment_entry=None)
		self.assertEqual(first["document"]["name"], second["document"]["name"])
		self.assertTrue(second["reused"])
		self.doc.insert.assert_called_once()
		self.bind.assert_called_once()

	def test_replay_after_permission_change_requires_review_instead_of_a_new_request(self):
		def run(request_id, payload, operation, replay, **kwargs):
			with patch.object(receipts, "_payment", side_effect=frappe.PermissionError):
				return replay({"name": "PE-1"})
		self.kernel.side_effect = run
		result = self.save()
		self.assertTrue(result["failed"])
		self.assertTrue(result["requires_review"])
		self.doc.save.assert_not_called()

	def test_generated_remarks_are_not_mistaken_for_custom_input(self):
		self.doc.remarks = "Native generated description"
		self.doc.custom_remarks = 0
		self.assertEqual(receipts.get_receipt_context("SO-1", "PE-1")["document"]["remarks"], "")
		self.save(values={**self.values, "remarks": ""})
		self.assertEqual(self.doc.custom_remarks, 0)

	def test_company_currency_conversion_is_advanced_even_when_both_accounts_match_order_currency(self):
		self.doc.company_currency = "USD"
		self.doc.source_exchange_rate = 2
		self.doc.target_exchange_rate = 2
		result = receipts.get_receipt_context("SO-1", "PE-1")
		self.assertTrue(result["advanced_reason"])
		self.assertEqual(result["editable_fields"], [])
		with self.assertRaises(frappe.ValidationError):
			self.save(values={**self.values, "posting_date": "2026-10-20"})
		self.doc.save.assert_not_called()
		self.doc.set_exchange_rate.assert_not_called()

	def test_tax_rows_templates_and_withholding_are_advanced_before_any_native_write(self):
		for field, value in (
			("taxes", [frappe._dict(tax_amount=1, included_in_paid_amount=0)]),
			("sales_taxes_and_charges_template", "TAX-TEMPLATE"),
			("purchase_taxes_and_charges_template", "TAX-TEMPLATE"),
			("apply_tds", 1), ("tax_withholding_category", "TDS"),
			("tax_withholding_group", "GROUP"), ("tax_withholding_entries", [frappe._dict()]),
			("override_tax_withholding_entries", 1),
		):
			self.doc[field] = value
			with self.subTest(field=field):
				self.assertTrue(receipts.get_receipt_context("SO-1", "PE-1")["advanced_reason"])
				with self.assertRaises(frappe.ValidationError):
					self.save()
			self.doc[field] = None
		self.doc.save.assert_not_called()
		self.doc.set_amounts.assert_not_called()

	def test_changed_account_clears_old_bank_identity_and_sets_actual_account_type(self):
		self.doc.update(bank_account="OLD-BANK", bank="OLD", bank_account_no="SYNTHETIC-NUMBER", paid_to_account_type="Bank")
		self.bank.return_value = frappe._dict(name="Cash", account_type="Cash", account_currency="MXN")
		self.save(values={**self.values, "bank_account": "Cash"})
		self.assertEqual((self.doc.paid_to, self.doc.paid_to_account_type, self.doc.paid_to_account_currency), ("Cash", "Cash", "MXN"))
		self.assertTrue(all(self.doc.get(field) is None for field in ("bank_account", "bank", "bank_account_no")))

	def test_new_mapping_clears_inherited_bank_identity_but_unchanged_existing_account_keeps_it(self):
		self.doc.update(bank_account="BANK", bank="BANK-NAME", bank_account_no="SYNTHETIC-NUMBER")
		self.save()
		self.assertEqual(self.doc.bank_account, "BANK")
		self.doc.name = None
		self.save(payment_entry=None, expected_modified=None)
		self.assertIsNone(self.doc.bank_account)
		self.assertIsNone(self.doc.bank_account_no)

	def test_read_only_optional_fields_are_preserved_and_cannot_be_changed(self):
		self.doc.update(reference_no="OLD-REF", remarks="Generated description", custom_remarks=0)
		denied = {"reference_no", "custom_remarks"}
		def require_fields(doctype, fields, **kwargs):
			if doctype == "Payment Entry" and set(fields) & denied:
				raise frappe.PermissionError("field permission")
		self.fields.side_effect = require_fields
		with patch.object(receipts, "_editable_fields", side_effect=lambda doc, fields, *a: list(set(fields) - denied)):
			projection = receipts.get_receipt_context("SO-1", "PE-1")
			self.assertTrue({"amount", "posting_date", "bank_account"} <= set(projection["editable_fields"]))
			self.assertNotIn("reference_no", projection["editable_fields"])
			self.assertNotIn("remarks", projection["editable_fields"])
			values = {**self.values, "reference_no": "OLD-REF", "remarks": ""}
			self.save(values=values)
			self.assertEqual(self.doc.reference_no, "OLD-REF")
			self.assertEqual((self.doc.remarks, self.doc.custom_remarks), ("Generated description", 0))
			self.doc.save.reset_mock()
			for key, value in (("reference_no", "OTHER"), ("remarks", "OTHER")):
				with self.subTest(key=key), self.assertRaises(frappe.PermissionError):
					self.save(values={**values, key: value}, expected_modified="p2")
			self.doc.save.assert_not_called()

	def test_bank_dependency_field_permission_is_reflected_in_projection_and_enforced_on_write(self):
		denied = {"bank_account_no"}
		with patch.object(receipts, "_editable_fields", side_effect=lambda doc, fields, *a: list(set(fields) - denied)):
			self.assertNotIn("bank_account", receipts.get_receipt_context("SO-1", "PE-1")["editable_fields"])
		def require_fields(doctype, fields, **kwargs):
			if set(fields) & denied:
				raise frappe.PermissionError("bank field permission")
		self.fields.side_effect = require_fields
		self.bank.return_value = frappe._dict(name="Cash", account_type="Cash", account_currency="MXN")
		with self.assertRaises(frappe.PermissionError):
			self.save(values={**self.values, "bank_account": "Cash"})
		self.doc.save.assert_not_called()

	def test_invalid_dates_and_attachments_return_definite_failure_without_entering_kernel(self):
		for args in (
			{"values": {**self.values, "reference_date": "2026-02-31"}},
			{"values": {**self.values, "posting_date": "2026-99-01"}},
			{"values": "not-json"}, {"attachments": "not-json"},
		):
			with self.subTest(args=args):
				result = self.save(**args)
				self.assertTrue(result["failed"])
				self.assertFalse(result.get("requires_review"))
		self.kernel.assert_not_called()
		self.doc.save.assert_not_called()

	def test_paused_order_is_acknowledged_but_completed_request_keeps_its_receipt(self):
		from deeplinkerp_branding.tests.test_purchase_operation import AuditDatabase

		db = AuditDatabase()
		with (
			patch.object(receipts.purchase_operation, "run", side_effect=NATIVE_RUN),
			patch.object(frappe, "db", db), patch.object(frappe, "session", frappe._dict(user="QA")),
			patch.object(frappe, "get_doc", side_effect=db.audit),
			patch("deeplinkerp_branding.services.purchase_consistency.bind_operation"),
			patch("deeplinkerp_branding.services.purchase_consistency.check_registered"),
		):
			self.order.status = "On Hold"
			result = self.save()
			self.assertTrue(result["failed"])
			self.assertFalse(result.get("requires_review"))
			self.assertEqual(db.rows, {})
			self.doc.save.assert_not_called()
			self.order.status = "To Deliver and Bill"
			self.assertFalse(self.save().get("failed"))
			self.order.status = "On Hold"
			replayed = self.save()
			self.assertTrue(replayed["requires_review"])
			self.assertEqual(len(db.rows), 1)
			self.doc.save.assert_called_once()
