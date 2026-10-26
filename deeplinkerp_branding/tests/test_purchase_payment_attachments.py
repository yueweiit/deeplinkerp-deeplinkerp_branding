"""Attachment scope and native transaction coordination; upload transport is native."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import frappe

from deeplinkerp_branding.services import purchase_payment_attachments as files

SESSION = "12345678-1234-1234-1234-123456789abc"


class PaymentAttachmentTests(unittest.TestCase):
	def test_sales_order_upload_context_uses_customer_receipt_permissions(self):
		with patch("deeplinkerp_branding.services.sales_receipt_service._order") as order:
			self.assertIs(files._context("Sales Order", "SO-1"), order.return_value)
			order.assert_called_once_with("SO-1", create=True)

	def test_sales_uploads_bind_only_to_same_customer_order_and_receive_direction(self):
		for overrides in ({}, {"party": "OTHER"}, {"payment_type": "Pay"}, {"party_type": "Supplier"},
			{"references": [frappe._dict(reference_doctype="Sales Order", reference_name="SO-2")]},
			{"references": [frappe._dict(reference_doctype="Sales Order", reference_name="SO-1"),
				frappe._dict(reference_doctype="Sales Order", reference_name="SO-2")]}):
			doc = frappe._dict(name="PE", company="C", party="CUSTOMER", party_type="Customer", payment_type="Receive",
				references=[frappe._dict(reference_doctype="Sales Order", reference_name="SO-1")])
			doc.update(overrides)
			doc.check_permission = Mock()
			source = frappe._dict(name="SO-1", doctype="Sales Order", company="C", customer="CUSTOMER")
			intent = {"context": ["Sales Order", "SO-1"], "files": ["FILE"]}
			with (
				self.subTest(overrides=overrides),
				patch.object(frappe, "cache", return_value=MagicMock()),
				patch.object(frappe, "db", MagicMock()), patch.object(files, "_put"),
				patch.object(files, "_intent", return_value=intent),
				patch.object(files, "_context", return_value=source),
				patch.object(files, "_temporary") as temporary,
				patch.object(files.service, "_read"),
			):
				if overrides:
					with self.assertRaises(frappe.PermissionError):
						files.bind(doc, SESSION, ["FILE"])
					temporary.assert_not_called()
				else:
					files.bind(doc, SESSION, ["FILE"])
					temporary.return_value.create_attachment_copy.assert_called_once_with("Payment Entry", "PE")

	def setUp(self):
		for name, value in (
			("session", frappe._dict(user="qa5-finance@example.invalid")),
			("flags", frappe._dict(mute_messages=True)),
		):
			patcher = patch.object(frappe, name, value)
			patcher.start()
			self.addCleanup(patcher.stop)

	def test_temporary_file_requires_owner_private_unattached_and_native_permissions(self):
		current = frappe.session.user
		for override in (
			{"owner": "other-user"},
			{"is_private": 0},
			{"is_folder": 1},
			{"attached_to_doctype": "Payment Entry"},
			{"attached_to_name": "PE"},
			{"attached_to_field": "field"},
		):
			doc = SimpleNamespace(
				owner=current,
				is_private=1,
				is_folder=0,
				attached_to_doctype=None,
				attached_to_name=None,
				attached_to_field=None,
				check_permission=Mock(),
			)
			for field, value in override.items():
				setattr(doc, field, value)
			with (
				self.subTest(override=override),
				patch.object(frappe, "get_doc", return_value=doc),
				self.assertRaises(frappe.PermissionError),
			):
				files._temporary("FILE")
			self.assertEqual(
				[call.args[0] for call in doc.check_permission.call_args_list], ["read", "write"]
			)

	def test_invalid_file_lists_and_unknown_session_are_rejected(self):
		for value in ({"name": "FILE"}, [1], [""], '"FILE"'):
			with self.subTest(value=value), self.assertRaises(frappe.ValidationError):
				files._ids(value)
		self.assertEqual(files._ids('["A","A","B"]'), ["A", "B"])

	def test_binding_reuses_native_attachment_helper_and_rollback_restores_session(self):
		intent = {"context": ["Payment Entry", "PE"], "files": ["FILE"]}
		doc = SimpleNamespace(name="PE", company="C", check_permission=Mock())
		source = SimpleNamespace(name="PE", doctype="Payment Entry", company="C")
		callbacks = []
		db = SimpleNamespace(after_rollback=SimpleNamespace(add=callbacks.append))
		cache = MagicMock()
		with (
			patch.object(frappe, "cache", return_value=cache),
			patch.object(frappe, "db", db),
			patch.object(files, "_intent", return_value=intent),
			patch.object(files, "_context", return_value=source),
			patch.object(files, "_temporary") as temporary,
			patch.object(files, "_put") as put,
			patch.object(files.service, "_read"),
		):
			files.bind(doc, SESSION, ["FILE"])
			temporary.return_value.create_attachment_copy.assert_called_once_with("Payment Entry", "PE")
			temporary.assert_called_once_with("FILE")
			self.assertEqual(put.call_args.args[1]["bound"], "PE")
			callbacks[0]()
			self.assertNotIn("bound", put.call_args.args[1])

	def test_another_company_session_file_or_payment_cannot_be_rebound(self):
		doc = SimpleNamespace(name="PE", company="C", check_permission=Mock())
		source = SimpleNamespace(name="PE", doctype="Payment Entry", company="C")
		for intent, company in (
			({"context": ["Payment Entry", "PE"], "files": ["OTHER"]}, "C"),
			({"context": ["Payment Entry", "PE"], "files": ["FILE"]}, "OTHER"),
			(
				{
					"context": ["Payment Entry", "PE"],
					"files": ["FILE"],
					"bound": "OTHER-PE",
					"used": ["FILE"],
				},
				"C",
			),
		):
			source.company = company
			with (
				self.subTest(intent=intent, company=company),
				patch.object(frappe, "cache", return_value=MagicMock()),
				patch.object(files, "_intent", return_value=intent),
				patch.object(files, "_context", return_value=source),
				patch.object(files.service, "_read"),
				patch.object(files, "_temporary") as temporary,
				self.assertRaises(frappe.PermissionError),
			):
				files.bind(doc, SESSION, ["FILE"])
			temporary.assert_not_called()

	def test_committed_file_session_is_never_deleted_by_cancel(self):
		with (
			patch.object(frappe, "cache", return_value=MagicMock()),
			patch.object(files, "_intent", return_value={"bound": "PE"}),
			patch.object(frappe, "delete_doc") as delete,
		):
			self.assertEqual(files.discard(SESSION, cancel=1), [])
			delete.assert_not_called()
