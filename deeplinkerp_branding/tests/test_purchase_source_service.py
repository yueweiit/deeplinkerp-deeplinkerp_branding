"""Native boundary tests: source evidence must not bypass purchase permissions."""
import json
import unittest
from contextlib import contextmanager, ExitStack, nullcontext
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import frappe
from deeplinkerp_branding.services import purchase_source_service as service


class PurchaseSourceServiceTests(unittest.TestCase):
	def setUp(self):
		# These boundary tests run both with an initialized native site and in
		# CI's site-free pytest process. Do not depend on thread-local DB/flags.
		self.db = Mock()
		# These existing source contract tests own an in-memory DB/order. Real
		# physical leases and native _bind writes are covered by NativeAtomic.
		for method in ("execution", "initialize", "document_boundary"):
			guard = patch(
				"deeplinkerp_branding.services.purchase_repost_boundary." + method,
				return_value=nullcontext() if method != "initialize" else None,
			)
			guard.start()
			self.addCleanup(guard.stop)
		self.db_patch = patch.object(frappe, "db", self.db)
		self.db_patch.start()
		self.addCleanup(self.db_patch.stop)

		def throw(message, exc=frappe.ValidationError, **kwargs):
			raise exc(message)

		self.throw_patch = patch.object(frappe, "throw", side_effect=throw)
		self.throw_patch.start()
		self.addCleanup(self.throw_patch.stop)
		date_patch = patch.object(frappe.utils, "nowdate", return_value="2026-10-06")
		date_patch.start()
		self.addCleanup(date_patch.stop)
		datetime_patch = patch.object(frappe.utils, "now_datetime", return_value="2026-10-07 10:00:00")
		datetime_patch.start()
		self.addCleanup(datetime_patch.stop)
		session_patch = patch.object(frappe, "session", frappe._dict(user="Administrator"))
		session_patch.start()
		self.addCleanup(session_patch.stop)
		self.source = dict(
			version="source-v1",
			eligible=True,
			currency="CNY",
			items=[dict(item_code="I", qty="2", uom="个", amount="100")],
			issues=[],
		)
		self.doc = frappe._dict(
			name="OA",
			target_company="China",
			purchase_order=None,
			custom_purchase_source_json=json.dumps(self.source),
			custom_cashier_payment_evidence="{}",
		)

	def test_unapproved_or_changed_source_never_creates_order(self):
		for raw, expected in (({**self.source, "eligible": False}, "source-v1"), (self.source, "old")):
			with (
				patch.object(service, "_source", return_value=self.doc),
				patch.object(service, "_fresh_source", return_value=(raw, {})),
				patch.object(service, "_native") as native,
			):
				with self.assertRaises(frappe.ValidationError):
					service.create_purchase_order_from_source(
						"OA",
						expected,
						"China",
						"Supplier",
						"CNY",
						"2026-10-08",
						[{"item_code": "I", "qty": 2, "uom": "个", "rate": 50}],
					)
				native.assert_not_called()

	def test_creation_uses_native_insert_draft_and_no_internal_commit(self):
		order = Mock(name="order")
		order.name = "PO-1"
		order.docstatus = 0
		with (
			patch.object(service, "_source", return_value=self.doc),
			patch.object(service, "_fresh_source", return_value=(self.source, {})),
			patch.object(service, "_version", return_value="v1"),
			patch.object(service, "_native", return_value=frappe._dict(disabled=0)),
			patch.object(service, "_validate_items"),
			patch.object(service, "_write_fields"),
			patch.object(service, "_bind") as bind,
			patch.object(frappe, "new_doc", return_value=order),
			patch.object(frappe, "has_permission", return_value=True),
			patch.object(frappe.db, "commit") as commit,
		):
			result = service.create_purchase_order_from_source(
				"OA",
				"v1",
				"China",
				"Supplier",
				"CNY",
				"2026-10-08",
				[{"item_code": "I", "qty": 2, "uom": "个", "rate": 50}],
			)
			self.assertEqual(result, {"name": "PO-1", "doctype": "Purchase Order"})
			order.insert.assert_called_once_with()
			order.submit.assert_not_called()
			commit.assert_not_called()
			bind.assert_called_once()

	def test_company_mismatch_cannot_be_overridden_by_reason(self):
		with (
			patch.object(service, "_source", return_value=self.doc),
			patch.object(service, "_fresh_source", return_value=(self.source, {})),
			patch.object(service, "_version", return_value="source-v1"),
		):
			with self.assertRaises(frappe.ValidationError):
				service.create_purchase_order_from_source(
					"OA",
					"source-v1",
					"Mexico",
					"Supplier",
					"CNY",
					"2026-10-08",
					[{"item_code": "I", "qty": 2, "uom": "个", "rate": 50}],
					"manual",
				)

	@contextmanager
	def creation_boundary(self, *, source=None, native=None, candidates=None):
		"""Only native storage/permissions are replaced; selection and role checks run."""
		order = Mock()
		order.name = "PO-roles"
		order.docstatus = 0
		with ExitStack() as stack:
			stack.enter_context(patch.object(service, "_source", return_value=self.doc))
			stack.enter_context(
				patch.object(service, "_fresh_source", return_value=(source or self.source, {}))
			)
			stack.enter_context(patch.object(service, "_version", return_value="v1"))
			native_read = stack.enter_context(
				patch.object(
					service,
					"_native",
					side_effect=native
					or (
						lambda dt, name, *args: frappe._dict(
							name=name,
							disabled=0,
							company="China" if dt == "Project" else None,
							is_active="Yes",
						)
					),
				)
			)
			stack.enter_context(patch.object(service, "_validate_items"))
			writes = stack.enter_context(patch.object(service, "_write_fields"))
			bind = stack.enter_context(patch.object(service, "_bind"))
			stack.enter_context(patch.object(frappe, "new_doc", return_value=order))
			stack.enter_context(patch.object(frappe, "has_permission", return_value=True))
			stack.enter_context(patch.object(frappe, "get_meta", return_value=Mock(has_field=lambda _: True)))
			stack.enter_context(
				patch.object(frappe, "get_list", side_effect=candidates or (lambda *args, **kwargs: []))
			)
			yield order, bind, native_read, writes

	def create_roles(self, company="China", **kwargs):
		return service.create_purchase_order_from_source(
			"OA",
			"v1",
			company,
			"Supplier",
			"CNY",
			"2026-10-08",
			[{"item_code": "I", "qty": 2, "uom": "个", "rate": 50}],
			**kwargs,
		)

	def test_imported_unconfirmed_buyer_proposal_can_be_corrected_only_with_reason(self):
		self.doc.backfill_imported = 1
		with self.creation_boundary() as (order, bind, _, _):
			with self.assertRaisesRegex(frappe.ValidationError, "说明"):
				self.create_roles("Mexico")
			order.insert.assert_not_called()
			result = self.create_roles(
				"Mexico", correction_reason="原组织建议不适用，采购主体经核对为 Mexico"
			)
		self.assertEqual(result["name"], "PO-roles")
		self.assertEqual(order.company, "Mexico")
		bind.assert_called_once()

	def test_confirmed_manual_buyer_company_is_never_overridden_by_proposal_correction(self):
		self.doc.backfill_imported = 1
		self.doc.custom_purchase_company_confirmed = 1
		with self.creation_boundary() as (order, _, _, _):
			with self.assertRaisesRegex(frappe.ValidationError, "公司"):
				self.create_roles("Mexico", correction_reason="申请组织变化")
		order.insert.assert_not_called()

	def test_unique_beneficiary_and_project_native_selection_do_not_replace_purchasing_company(self):
		source = {
			**self.source,
			"beneficiary_company": "Mexico Factory",
			"beneficiary_company_status": "unique",
			"project": "Factory tooling",
			"project_status": "unique",
		}

		def candidates(dt, **kwargs):
			return [frappe._dict(name="Factory" if dt == "Company" else "PROJECT-1")]

		def native(dt, name, *args):
			return frappe._dict(
				name=name, company="China" if dt == "Project" else None, is_active="Yes", disabled=0
			)

		with self.creation_boundary(source=source, native=native, candidates=candidates) as (
			order,
			bind,
			reads,
			writes,
		):
			self.create_roles()
		self.assertEqual(order.company, "China")
		self.assertEqual(order.project, "PROJECT-1")
		self.assertIn(unittest.mock.call("Company", "Factory"), reads.call_args_list)
		self.assertIn(unittest.mock.call("Project", "PROJECT-1"), reads.call_args_list)
		self.assertEqual(bind.call_args.kwargs["beneficiary_company"], "Factory")
		self.assertEqual(bind.call_args.kwargs["project"], "PROJECT-1")
		self.assertTrue(
			any(
				"project" in call.args[1]
				for call in writes.call_args_list
				if call.args[0] == "Purchase Order"
			)
		)

	def test_ambiguous_role_sources_require_server_verified_explicit_selection(self):
		for role in ("beneficiary_company", "project"):
			source = {**self.source, role: None, role + "_status": "ambiguous"}
			with self.creation_boundary(source=source) as (order, _, _, _):
				with self.subTest(role=role), self.assertRaisesRegex(frappe.ValidationError, "明确"):
					self.create_roles()
				order.insert.assert_not_called()
			with self.creation_boundary(source=source) as (order, bind, reads, _):
				self.create_roles(**{role: "EXPLICIT"})
				self.assertIn(
					unittest.mock.call("Company" if role == "beneficiary_company" else "Project", "EXPLICIT"),
					reads.call_args_list,
				)
				self.assertEqual(bind.call_args.kwargs[role], "EXPLICIT")
				order.insert.assert_called_once_with()

	def test_company_display_name_multiple_matches_never_select_arbitrary_beneficiary(self):
		source = {**self.source, "beneficiary_company": "Mexico", "beneficiary_company_status": "unique"}
		with self.creation_boundary(
			source=source, candidates=lambda *args, **kwargs: [frappe._dict(name="A"), frappe._dict(name="B")]
		) as (order, _, _, _):
			with self.assertRaisesRegex(frappe.ValidationError, "明确"):
				self.create_roles()
		order.insert.assert_not_called()

	def test_incompatible_source_project_stays_native_blank_and_explicit_mismatch_is_rejected(self):
		source = {**self.source, "project": "Factory tooling", "project_status": "unique"}

		def native(dt, name, *args):
			return frappe._dict(
				name=name, company="Mexico" if dt == "Project" else None, is_active="Yes", disabled=0
			)

		with self.creation_boundary(
			source=source, native=native, candidates=lambda *args, **kwargs: [frappe._dict(name="MX-PROJECT")]
		) as (order, bind, _, _):
			self.create_roles()
			self.assertNotIn("project", vars(order))
			self.assertIsNone(bind.call_args.kwargs["project"])
		with self.creation_boundary(source=source, native=native) as (order, _, _, _):
			with self.assertRaisesRegex(frappe.ValidationError, "项目.*公司"):
				self.create_roles(project="MX-PROJECT")
			order.insert.assert_not_called()

	def test_inactive_or_unassigned_project_candidates_stay_blank_and_explicit_choices_are_rejected(self):
		source = {**self.source, "project": "Source molds", "project_status": "unique"}
		for fields, company, active in (
			({"is_active"}, "China", "Yes"),
			({"company"}, "China", "Yes"),
			({"company", "is_active"}, None, "Yes"),
			({"company", "is_active"}, "", "Yes"),
			({"company", "is_active"}, "China", "No"),
			({"company", "is_active"}, "China", None),
		):

			def native(dt, name, *args):
				return frappe._dict(
					name=name, company=company if dt == "Project" else None, is_active=active, disabled=0
				)

			meta = Mock(has_field=lambda field: field in fields)
			native_meta = lambda dt: meta if dt == "Project" else Mock(has_field=lambda _: True)
			with (
				self.subTest(fields=fields, company=company, active=active),
				self.creation_boundary(
					source=source,
					native=native,
					candidates=lambda *args, **kwargs: [frappe._dict(name="PROJECT")],
				) as (order, bind, _, _),
				patch.object(frappe, "get_meta", side_effect=native_meta),
			):
				self.create_roles()
				self.assertNotIn("project", vars(order))
				self.assertIsNone(bind.call_args.kwargs["project"])
			with (
				self.subTest(explicit=True, fields=fields, company=company, active=active),
				self.creation_boundary(source=source, native=native) as (order, _, _, _),
				patch.object(frappe, "get_meta", side_effect=native_meta),
			):
				with self.assertRaisesRegex(frappe.ValidationError, "项目.*公司"):
					self.create_roles(project="PROJECT")
				order.insert.assert_not_called()

	def test_inactive_source_project_projection_warns_without_filling_native_project(self):
		self.doc.has_permission = lambda permission: True
		self.doc.purchase_order = "PO"
		raw = {
			**self.source,
			"oa_identity": {"process_instance_id": "instance"},
			"project": "Inactive molds",
			"project_status": "unique",
		}
		self.doc.custom_purchase_source_json = json.dumps(raw)
		from deeplinkerp_branding.services import unified_purchase_service

		def native(dt, name, *args):
			return frappe._dict(name=name, company="China" if dt == "Project" else None, is_active="No")

		with (
			patch.object(service, "_source", return_value=self.doc),
			patch.object(unified_purchase_service, "cashier_evidence_readable", return_value=False),
			patch.object(frappe, "has_permission", return_value=True),
			patch.object(frappe, "get_list", return_value=[frappe._dict(name="INACTIVE-PROJECT")]),
			patch.object(frappe, "get_meta", return_value=Mock(has_field=lambda _: True)),
			patch.object(service, "_native", side_effect=native),
		):
			payload = service.get_purchase_source_detail("OA")
		self.assertIsNone(payload.get("project_candidate"))
		self.assertEqual(payload.get("project_status"), "incompatible")
		self.assertIn("启用", "；".join(payload.get("role_warnings") or []))
		self.assertEqual(payload["source"]["project"], "Inactive molds")

	def test_explicit_role_selection_obeys_native_company_permission(self):
		def native(dt, name, *args):
			if name == "DENIED":
				raise frappe.PermissionError("Company denied")
			return frappe._dict(name=name, disabled=0)

		with self.creation_boundary(native=native) as (order, _, _, _):
			with self.assertRaises(frappe.PermissionError):
				self.create_roles(beneficiary_company="DENIED")
			order.insert.assert_not_called()

	def test_explicit_empty_project_clears_verified_source_project_without_native_guessing(self):
		self.doc.custom_purchase_project = "OLD-PROJECT"
		with self.creation_boundary() as (order, bind, reads, _):
			self.create_roles(project="")
		self.assertNotIn("project", vars(order))
		self.assertEqual(bind.call_args.kwargs["project"], "")
		self.assertFalse(any(call.args[0] == "Project" for call in reads.call_args_list))

	def test_explicit_role_controls_reject_non_string_values_before_order_insert(self):
		for role in ("beneficiary_company", "project"):
			for value in (0, [], {}):
				with self.creation_boundary() as (order, _, _, _):
					with (
						self.subTest(role=role, value=value),
						self.assertRaisesRegex(frappe.ValidationError, "公司|项目"),
					):
						self.create_roles(**{role: value})
					order.insert.assert_not_called()

	def test_association_verifies_beneficiary_and_retains_existing_native_project(self):
		order = SimpleNamespace(
			name="EXISTING-PO",
			company="China",
			currency="CNY",
			docstatus=0,
			status="Draft",
			project="NATIVE-PROJECT",
		)
		order.items = [frappe._dict(item_code="I", qty=2, uom="个", rate=50)]
		order.get = lambda key, default=None: getattr(order, key, default)
		order.check_permission = Mock()
		order.save = Mock()
		with (
			self.creation_boundary() as (_, bind, reads, _),
			patch.object(frappe, "get_doc", return_value=order),
		):
			result = service.associate_purchase_order(
				"OA", "EXISTING-PO", "v1", beneficiary_company="Factory", project="NATIVE-PROJECT"
			)
		self.assertEqual(result["name"], "EXISTING-PO")
		self.assertEqual(order.project, "NATIVE-PROJECT")
		order.save.assert_not_called()
		self.assertIn(unittest.mock.call("Company", "Factory"), reads.call_args_list)
		self.assertIn(unittest.mock.call("Project", "NATIVE-PROJECT"), reads.call_args_list)
		self.assertEqual(bind.call_args.kwargs["beneficiary_company"], "Factory")
		self.assertEqual(bind.call_args.kwargs["project"], "NATIVE-PROJECT")

	def test_association_rejects_explicit_project_change_before_any_binding(self):
		order = SimpleNamespace(
			name="EXISTING-PO",
			company="China",
			currency="CNY",
			docstatus=0,
			status="Draft",
			project="NATIVE-PROJECT",
		)
		order.get = lambda key, default=None: getattr(order, key, default)
		order.check_permission = Mock()
		order.save = Mock()
		with self.creation_boundary() as (_, bind, _, _), patch.object(frappe, "get_doc", return_value=order):
			with self.assertRaisesRegex(frappe.ValidationError, "已有采购订单.*项目"):
				service.associate_purchase_order("OA", "EXISTING-PO", "v1", project="DIFFERENT-PROJECT")
		bind.assert_not_called()
		order.save.assert_not_called()

	def test_existing_link_is_idempotent_not_second_order(self):
		self.doc.purchase_order = "PO-existing"
		with (
			patch.object(service, "_source", return_value=self.doc),
			patch.object(service, "_native") as native,
			patch.object(frappe, "new_doc") as new,
		):
			self.assertEqual(
				service.create_purchase_order_from_source(
					"OA", "v1", "China", "Supplier", "CNY", "2026-10-08", []
				)["name"],
				"PO-existing",
			)
			native.assert_called_once_with("Purchase Order", "PO-existing", "read")
			new.assert_not_called()

	def test_historical_paid_evidence_requires_explicit_reconciliation(self):
		self.assertTrue(
			service.cashier_payment_reason({"paid_amount": "20", "payment_evidence_status": "recorded"}, None)
		)
		self.assertTrue(
			service.cashier_payment_reason({"paid_amount": None, "payment_evidence_status": "unknown"}, None)
		)
		self.assertFalse(
			service.cashier_payment_reason({"paid_amount": "0", "payment_evidence_status": "recorded"}, None)
		)
		self.assertFalse(
			service.cashier_payment_reason(
				{"paid_amount": "20", "payment_evidence_status": "recorded"}, {"verified": True}
			)
		)

	def test_zero_document_sync_replay_rechecks_source_acl_and_current_version(self):
		receipt = {
			"documents": [],
			"result": {"pending": 1},
			"acknowledgements": [{"name": "OA", "version": "old", "purchase_order": None}],
		}
		with patch.object(service, "_source", side_effect=frappe.PermissionError("current source ACL")):
			with self.assertRaises(frappe.PermissionError):
				service._sync_replay(receipt)
		with patch.object(service, "_source", return_value=self.doc):
			with self.assertRaisesRegex(frappe.ValidationError, "已变化"):
				service._sync_replay(receipt)

	def test_cashier_snapshot_accepts_equivalent_timezone_serializations(self):
		from deeplinkerp_branding.services import operating_expenses

		until = "2026-10-06T09:00:00.123456+00:00"
		for returned in ("2026-10-06T09:00:00.123456Z", "2026-10-06T17:00:00.123456+08:00"):
			with (
				self.subTest(returned=returned),
				patch.object(
					operating_expenses,
					"_request",
					return_value={
						"until": returned,
						"items": [{"source_id": "purchase-1"}],
						"end": True,
					},
				) as request,
			):
				self.assertEqual(service._cashier_snapshot(until), [{"source_id": "purchase-1"}])
				self.assertEqual(request.call_args.args[1]["until"], until)
		self.db.commit.assert_not_called()

	def test_cashier_snapshot_rejects_different_or_unverifiable_instant(self):
		from deeplinkerp_branding.services import operating_expenses

		until = "2026-10-06T09:00:00.123456+00:00"
		for returned in ("2026-10-06T09:00:00.123457Z", "2026-10-06T09:00:00.123456", "invalid", None):
			with (
				self.subTest(returned=returned),
				patch.object(
					operating_expenses,
					"_request",
					return_value={
						"until": returned,
						"items": [],
						"end": True,
					},
				),
			):
				with self.assertRaisesRegex(frappe.ValidationError, "快照时间不符"):
					service._cashier_snapshot(until)
		self.db.commit.assert_not_called()

	def test_source_refresh_does_not_overwrite_manual_fields(self):
		incoming = {
			**self.source,
			"source_id": "oa:test",
			"oa_identity": {"corp_id": "corp", "process_instance_id": "instance"},
			"status": "COMPLETED",
			"result": "agree",
		}
		self.doc.items = [{"qty": 99}]
		self.doc.target_company = "Manual Company"
		with (
			patch.object(service, "_cached_doc", return_value=self.doc),
			patch.object(frappe.db, "set_value") as save,
		):
			service._cache_source(incoming, {}, "Suggested Company")
			values = save.call_args.args[2]
			self.assertNotIn("items", values)
			self.assertNotIn("target_company", values)
			self.assertNotIn("payment_amount", values)
			self.assertNotIn("purchase_order", values)
			for field in (
				"custom_purchase_beneficiary_company",
				"custom_purchase_project",
				"custom_purchase_company_confirmed",
				"custom_purchase_company_confirmed_by",
				"custom_purchase_company_confirmed_on",
			):
				self.assertNotIn(field, values)

	def test_new_cache_keeps_applicant_company_as_hint_without_order_or_buyer_confirmation(self):
		incoming = {
			**self.source,
			"source_id": "oa:test",
			"oa_identity": {"corp_id": "corp", "process_instance_id": "instance"},
			"process_instance_id": "instance",
			"process_code": "purchase",
			"apply_date": "2026-10-07",
			"status": "COMPLETED",
			"beneficiary_company": "Mexico Factory",
			"project": "Raw factory project",
		}
		doc = Mock()
		doc.name = "cache"
		doc.get.return_value = None
		with (
			patch.object(service, "_cached_doc", return_value=None),
			patch.object(frappe, "new_doc", return_value=doc) as new,
		):
			service._cache_source(incoming, {}, "China Applicant Proposal")
		values = doc.update.call_args.args[0]
		self.assertFalse(values.get("target_company"))
		self.assertEqual(values.get("custom_purchase_company_proposal"), "China Applicant Proposal")
		self.assertFalse(values.get("custom_purchase_company_confirmed"))
		self.assertFalse(values.get("purchase_order"))
		self.assertFalse(values.get("custom_purchase_beneficiary_company"))
		self.assertEqual(json.loads(values[service.SOURCE_FIELD])["beneficiary_company"], "Mexico Factory")
		new.assert_called_once_with(service.DOCTYPE)
		self.db.commit.assert_not_called()

	def test_source_sync_uses_small_pages_without_dropping_or_duplicating_sources(self):
		from deeplinkerp_branding.services import operating_expenses

		rows = [{"source_id": "source-" + str(index)} for index in range(201)]

		def read_page(connection, limit, until, cursor=None, **kwargs):
			start = int(cursor or 0)
			end = min(start + limit, len(rows))
			return rows[start:end], str(end) if end < len(rows) else None

		applicant = {"user_id": "u", "employee_name": "Synthetic"}

		def resolve(path, data):
			return {"items": [{**value, "status": "unknown"} for value in data["applicants"]]}

		with (
			patch.object(frappe, "cache", Mock(lock=Mock(return_value=MagicMock()))),
			patch.object(frappe, "local", SimpleNamespace(site="synthetic-source-test")),
			patch.object(frappe, "get_meta", return_value=SimpleNamespace(has_field=lambda _: True)),
			patch.object(frappe, "get_all", return_value=[]),
			patch.object(operating_expenses, "_manager"),
			patch.object(
				operating_expenses, "_oa_connection", return_value=SimpleNamespace(_connection=object())
			),
			patch.object(operating_expenses, "_request", side_effect=resolve),
			patch.object(service, "_cashier_snapshot", return_value=[]),
			patch.object(service.oa, "read_page", side_effect=read_page) as read,
			patch.object(service.oa, "resolution_applicant", return_value=applicant),
			patch.object(service.contract, "in_scope", return_value=True),
			patch.object(service, "_normalize", side_effect=lambda row, **kwargs: dict(row)),
			patch.object(service.contract, "payment_evidence", return_value={}),
			patch.object(service, "_cache_source") as save,
			patch.object(service, "_reconcile_cached_sources") as reconcile,
		):
			snapshots, count = service._read_sync_snapshot("2026-10-06T00:00:00Z")
		self.assertEqual(count, 201)
		self.assertEqual(
			[source["source_id"] for source, _, _ in snapshots], [row["source_id"] for row in rows]
		)
		save.assert_not_called()
		self.assertEqual([call.args[1] for call in read.call_args_list], [100, 100, 100])
		self.assertEqual([call.kwargs["cursor"] for call in read.call_args_list], [None, "100", "200"])
		self.assertEqual(reconcile.call_args.args[2], {row["source_id"] for row in rows})
		self.db.commit.assert_not_called()

	def test_last_upstream_page_failure_never_writes_partial_cache(self):
		from deeplinkerp_branding.services import operating_expenses

		row = {
			"corp_id": "corp",
			"process_instance_id": "instance",
			"process_code": service.contract.PROCESS_CODES[0],
			"create_time": "2026-01-01T00:00:00Z",
			"status": "COMPLETED",
			"result": "agree",
			"form_component_values": [{"name": "执行地区", "value": "中国China"}],
		}
		applicant = {"user_id": "u", "employee_name": "Synthetic"}
		with (
			patch.object(frappe, "cache", Mock(lock=Mock(return_value=MagicMock()))),
			patch.object(frappe, "local", SimpleNamespace(site="synthetic-source-test")),
			patch.object(frappe, "get_meta", return_value=SimpleNamespace(has_field=lambda _: True)),
			patch.object(frappe, "get_all", return_value=[]),
			patch.object(operating_expenses, "_manager"),
			patch.object(
				operating_expenses, "_oa_connection", return_value=SimpleNamespace(_connection=object())
			),
			patch.object(
				operating_expenses, "_request", return_value={"items": [{**applicant, "status": "unknown"}]}
			),
			patch.object(service, "_cashier_snapshot", return_value=[]),
			patch.object(
				service.oa, "read_page", side_effect=[([row], "next"), RuntimeError("late upstream failure")]
			),
			patch.object(service.oa, "resolution_applicant", return_value=applicant),
			patch.object(
				service, "_normalize", side_effect=lambda row, **kw: service.contract.normalize(row, **kw)
			),
			patch.object(service, "_cache_source") as save,
		):
			with self.assertRaisesRegex(RuntimeError, "late upstream failure"):
				service._read_sync_snapshot("2026-10-06T00:00:00Z")
		save.assert_not_called()

	def test_sync_normalizes_once_per_row_and_resolves_company_bridge_once_for_the_run(self):
		from deeplinkerp_branding.services import operating_expenses

		rows = [
			{
				"corp_id": "corp",
				"process_instance_id": str(index),
				"process_code": service.contract.PROCESS_CODES[0],
				"create_time": "2026-01-01T00:00:00Z",
				"status": "COMPLETED",
				"result": "agree",
				"form_component_values": [{"name": "执行地区", "value": "墨西哥Mexico"}],
			}
			for index in range(3)
		]
		applicant = {"user_id": "u", "employee_name": "Synthetic"}

		def resolution(path, data):
			return {
				"items": [
					{**value, "status": "matched", "assigned_department": "拉丁购"}
					for value in data["applicants"]
				]
			}

		with (
			patch.object(frappe, "cache", Mock(lock=Mock(return_value=MagicMock()))),
			patch.object(frappe, "local", SimpleNamespace(site="synthetic-source-test")),
			patch.object(frappe, "get_meta", return_value=SimpleNamespace(has_field=lambda _: True)),
			patch.object(frappe, "get_all", return_value=["拉丁购国际电子商务（东莞）有限公司"]) as companies,
			patch.object(operating_expenses, "_manager"),
			patch.object(
				operating_expenses, "_oa_connection", return_value=SimpleNamespace(_connection=object())
			),
			patch.object(operating_expenses, "_request", side_effect=resolution),
			patch.object(service, "_cashier_snapshot", return_value=[]),
			patch.object(service.oa, "read_page", return_value=(rows, None)),
			patch.object(service.oa, "resolution_applicant", return_value=applicant),
			patch.object(
				service,
				"_normalize",
				side_effect=lambda row, **kwargs: service.contract.normalize(row, **kwargs),
			),
			patch.object(
				service.contract, "_field_occurrences", wraps=service.contract._field_occurrences
			) as occurrences,
			patch.object(service, "_cache_source") as save,
			patch.object(service, "_reconcile_cached_sources"),
		):
			snapshots, count = service._read_sync_snapshot("2026-10-06T00:00:00Z")
		self.assertEqual(count, 3)
		self.assertEqual(occurrences.call_count, 3)
		companies.assert_called_once()
		self.assertEqual(companies.call_args.args[0], "Company")
		self.assertEqual({proposal for _, _, proposal in snapshots}, {"拉丁购国际电子商务（东莞）有限公司"})
		self.assertTrue(all(source["region"] == "墨西哥Mexico" for source, _, _ in snapshots))
		save.assert_not_called()

	def test_small_page_sync_keeps_original_twenty_thousand_source_bound(self):
		from deeplinkerp_branding.services import operating_expenses

		with (
			# This test mocks Redis locking; translation resources are covered
			# separately and must not be read from that lock-only cache mock.
			patch.object(service, "_i18n", side_effect=lambda text: text),
			patch.object(frappe, "cache", Mock(lock=Mock(return_value=MagicMock()))),
			patch.object(frappe, "local", SimpleNamespace(site="synthetic-source-test")),
			patch.object(frappe, "get_meta", return_value=SimpleNamespace(has_field=lambda _: True)),
			patch.object(frappe, "get_all", return_value=[]),
			patch.object(operating_expenses, "_manager"),
			patch.object(
				operating_expenses, "_oa_connection", return_value=SimpleNamespace(_connection=object())
			),
			patch.object(service, "_cashier_snapshot", return_value=[]),
			patch.object(
				service.oa,
				"read_page",
				side_effect=lambda *args, **kwargs: ([], str(int(kwargs.get("cursor") or 0) + args[1])),
			) as read,
			patch.object(service, "_reconcile_cached_sources") as reconcile,
		):
			with self.assertRaisesRegex(frappe.ValidationError, "超过本次同步上限"):
				service._read_sync_snapshot("2026-10-06T00:00:00Z")
		self.assertEqual(len(read.call_args_list), 200)
		self.assertEqual({call.args[1] for call in read.call_args_list}, {100})
		self.assertEqual(len(read.call_args_list) * read.call_args.args[1], 20000)
		reconcile.assert_not_called()
		self.db.commit.assert_not_called()

	def test_clearing_managed_payload_cannot_erase_provenance_guard(self):
		changed = frappe._dict(self.doc)
		changed.custom_purchase_source_json = ""
		changed.get_doc_before_save = lambda: self.doc
		with self.assertRaises(frappe.PermissionError):
			service.validate_managed_source(changed)

	def test_native_approval_and_identity_fields_remain_protected(self):
		for key in (
			"approval_status",
			"source_invalid",
			"source_pending",
			"source_stale",
			"process_instance_id",
		):
			changed = frappe._dict(self.doc)
			changed[key] = "changed"
			changed.get_doc_before_save = lambda: self.doc
			with self.subTest(field=key), self.assertRaises(frappe.PermissionError):
				service.validate_managed_source(changed)

	def test_managed_company_permissions_are_registered(self):
		from deeplinkerp_branding import hooks

		self.assertEqual(
			hooks.permission_query_conditions.get("OA Purchase Request"),
			"deeplinkerp_branding.services.purchase_source_service.permission_query_conditions",
		)
		self.assertEqual(
			hooks.has_permission.get("OA Purchase Request"),
			"deeplinkerp_branding.services.purchase_source_service.has_permission",
		)

	def test_unconfirmed_managed_buyer_remains_admin_only_even_if_beneficiary_is_allowed(self):
		doc = frappe._dict(
			custom_purchase_source_id="source-1",
			target_company="China",
			custom_purchase_beneficiary_company="Mexico",
			purchase_order=None,
			custom_purchase_company_confirmed=0,
		)
		with (
			patch.object(frappe, "get_roles", return_value=["Purchase User"]),
			patch.object(frappe, "has_permission", return_value=True),
			patch(
				"frappe.permissions.get_user_permissions",
				return_value={"Company": [frappe._dict(doc="China"), frappe._dict(doc="Mexico")]},
			),
		):
			self.assertFalse(service.has_permission(doc, user="buyer@example.test", ptype="read"))
		with patch.object(frappe, "get_roles", return_value=["System Manager"]):
			self.assertTrue(service.has_permission(doc, user="manager@example.test", ptype="read"))

	def test_source_query_scope_requires_confirmed_buyer_or_native_order_link(self):
		from deeplinkerp_branding.services import operating_expenses

		with (
			patch.object(operating_expenses, "_companies", return_value=["China"]),
			patch.object(frappe, "get_roles", return_value=["Purchase User"]),
		):
			self.db.escape.side_effect = lambda value: repr(value)
			condition = service.permission_query_conditions(user="buyer@example.test")
		self.assertIn("custom_purchase_company_confirmed", condition)
		self.assertIn("purchase_order", condition)
		self.assertNotIn("beneficiary_company", condition)

	def test_confirmation_and_verified_roles_cannot_be_written_through_native_source_save(self):
		for key in (
			"custom_purchase_beneficiary_company",
			"custom_purchase_project",
			"custom_purchase_company_proposal",
			"custom_purchase_company_confirmed",
			"custom_purchase_company_confirmed_by",
			"custom_purchase_company_confirmed_on",
		):
			changed = frappe._dict(self.doc)
			changed[key] = "changed"
			changed.get_doc_before_save = lambda: self.doc
			with self.subTest(field=key), self.assertRaises(frappe.PermissionError):
				service.validate_managed_source(changed)

	def test_source_import_marker_cannot_be_changed_to_gain_buyer_company_exception(self):
		for source_payload in (self.doc.custom_purchase_source_json, ""):
			for old_value, new_value in ((0, 1), (1, 0), (0, "changed")):
				old = frappe._dict(self.doc)
				old.custom_purchase_source_json = source_payload
				old.backfill_imported = old_value
				changed = frappe._dict(old)
				changed.backfill_imported = new_value
				changed.get_doc_before_save = lambda: old
				with (
					self.subTest(source=bool(source_payload), marker=new_value),
					self.assertRaises(frappe.PermissionError),
				):
					service.validate_managed_source(changed)

	def test_new_manual_source_cannot_preset_import_marker(self):
		doc = frappe._dict(name="manual", backfill_imported=1)
		doc.get_doc_before_save = lambda: None
		with self.assertRaises(frappe.PermissionError):
			service.validate_managed_source(doc)

	def test_unmanaged_native_source_keeps_regular_header_edits_and_blank_marker_default(self):
		old = frappe._dict(name="manual", target_company="China", description="old")
		changed = frappe._dict(old)
		changed.target_company = "Mexico"
		changed.description = "new"
		changed.backfill_imported = 0
		changed.get_doc_before_save = lambda: old
		service.validate_managed_source(changed)

	def test_unmanaged_source_confirmation_audit_cannot_be_planted_before_source_sync(self):
		for field, value in (
			(service.CONFIRMED_FIELD, 1),
			(service.CONFIRMED_BY_FIELD, "fake@example.test"),
			(service.CONFIRMED_ON_FIELD, "2026-10-07 10:00:00"),
		):
			for existing in (False, True):
				old = frappe._dict(name="manual", target_company="China") if existing else None
				changed = frappe._dict(old or {"name": "manual"})
				changed[field] = value
				changed.get_doc_before_save = lambda: old
				with self.subTest(field=field, existing=existing), self.assertRaises(frappe.PermissionError):
					service.validate_managed_source(changed)

	def test_unmanaged_source_confirmation_audit_cannot_be_cleared_outside_managed_write(self):
		for field, value, cleared in (
			(service.CONFIRMED_FIELD, 1, 0),
			(service.CONFIRMED_BY_FIELD, "server@example.test", ""),
			(service.CONFIRMED_ON_FIELD, "2026-10-07 10:00:00", None),
		):
			old = frappe._dict(name="manual")
			old[field] = value
			changed = frappe._dict(old)
			changed[field] = cleared
			changed.get_doc_before_save = lambda: old
			with self.subTest(field=field), self.assertRaises(frappe.PermissionError):
				service.validate_managed_source(changed)

	def test_native_audit_defaults_and_unchanged_serialized_audit_time_allow_header_edits(self):
		new = frappe._dict(
			name="manual",
			custom_purchase_company_confirmed=0,
			custom_purchase_company_confirmed_by="",
			custom_purchase_company_confirmed_on=None,
		)
		new.get_doc_before_save = lambda: None
		service.validate_managed_source(new)
		old = frappe._dict(self.doc)
		old.custom_purchase_company_confirmed = 1
		old.custom_purchase_company_confirmed_by = "server@example.test"
		old.custom_purchase_company_confirmed_on = datetime(2026, 10, 7, 10)
		changed = frappe._dict(old)
		changed.description = "manual edit"
		changed.custom_purchase_company_confirmed_on = "2026-10-07 10:00:00"
		changed.get_doc_before_save = lambda: old
		service.validate_managed_source(changed)

	def test_unmanaged_oa_retains_native_controller_permission(self):
		self.assertIs(service.has_permission(frappe._dict(name="legacy")), True)

	def test_managed_order_link_and_company_cannot_be_changed_via_native_save(self):
		old = frappe._dict(custom_oa_purchase_expense="OA", company="China")
		for changed in (
			frappe._dict(custom_oa_purchase_expense="", company="China"),
			frappe._dict(custom_oa_purchase_expense="Other", company="China"),
			frappe._dict(custom_oa_purchase_expense="OA", company="Mexico"),
		):
			changed.get_doc_before_save = lambda: old
			with (
				patch.object(frappe, "get_meta", return_value=Mock(has_field=lambda field: True)),
				patch.object(frappe.db, "get_value", return_value="source-1"),
				self.assertRaises(frappe.PermissionError),
			):
				service.validate_managed_order(changed)

	def test_managed_order_link_updates_use_narrow_authorized_context(self):
		order = frappe._dict(custom_oa_purchase_expense="OA", company="China")
		order.get_doc_before_save = lambda: None
		with service.managed_write():
			service.validate_managed_order(order)

	def test_orders_without_source_do_not_require_optional_oa_app(self):
		order = frappe._dict(company="China")
		order.get_doc_before_save = lambda: None
		with patch.object(frappe, "get_meta", side_effect=AssertionError("optional OA missing")):
			service.validate_managed_order(order)

	def test_managed_source_cannot_be_renamed_or_deleted_even_by_admin(self):
		for event in ("before_rename", "on_trash"):
			with self.subTest(event=event), self.assertRaises(frappe.PermissionError):
				service.protect_managed_source_identity(self.doc, event, "old", "new", False)

	def test_legacy_source_identity_operations_keep_native_behavior(self):
		service.protect_managed_source_identity(frappe._dict(name="legacy"), "before_rename")

	def test_binding_checks_native_field_write_permissions_before_any_mutation(self):
		order = frappe._dict(name="PO", company="China")
		order.get = lambda key: None
		with (
			patch.object(service, "_write_fields", side_effect=frappe.PermissionError),
			patch.object(frappe.db, "set_value") as save,
		):
			with self.assertRaises(frappe.PermissionError):
				service._bind(self.doc, order, self.source, {})
			save.assert_not_called()

	def test_same_submitted_order_recheck_does_not_rewrite_native_provenance_field(self):
		order = Mock()
		order.name = "PO"
		order.company = "China"
		order.docstatus = 1
		order.get.return_value = self.doc.name
		order.meta.get_field.return_value = frappe._dict(allow_on_submit=0)
		with patch.object(service, "_write_fields"):
			service._bind(self.doc, order, self.source, {})
		order.save.assert_not_called()
		self.assertEqual(self.db.set_value.call_args.args[2][service.BOUND_FIELD], "source-v1")

	def test_binding_records_server_buyer_confirmation_and_retains_previous_manual_roles(self):
		order = Mock()
		order.name = "PO"
		order.company = "China"
		order.docstatus = 1
		order.get.return_value = self.doc.name
		self.doc.custom_purchase_beneficiary_company = "Manual Mexico"
		self.doc.custom_purchase_project = "MANUAL-PROJECT"
		with (
			patch.object(service, "_write_fields"),
			patch.object(frappe, "session", frappe._dict(user="reviewer@example.test")),
			patch.object(frappe.utils, "now_datetime", return_value="2026-10-07 10:00:00"),
		):
			service._bind(self.doc, order, self.source, {})
		values = self.db.set_value.call_args.args[2]
		self.assertEqual(values.get("custom_purchase_company_confirmed"), 1)
		self.assertEqual(values.get("custom_purchase_company_confirmed_by"), "reviewer@example.test")
		self.assertEqual(values.get("custom_purchase_company_confirmed_on"), "2026-10-07 10:00:00")
		self.assertNotIn("custom_purchase_beneficiary_company", values)
		self.assertNotIn("custom_purchase_project", values)

	def test_original_payment_json_requires_native_payment_field_scope(self):
		self.doc.check_permission = Mock()
		fields = {
			"target_company",
			"purchase_order",
			"approval_status",
			"process_instance_id",
			"currency",
			"payment_amount",
			"detail_total_amount",
			"items_json",
			"processors_json",
			"payee",
			"description",
		}
		with (
			patch.object(frappe, "get_doc", return_value=self.doc),
			patch.object(service, "get_permitted_fields", return_value=fields),
			patch.object(service, "_native"),
		):
			with self.assertRaises(frappe.PermissionError):
				service.get_purchase_source_detail("OA")

	def test_attachment_denied_after_payment_permission_revoked_before_transport(self):
		from deeplinkerp_branding.services import operating_expenses

		with (
			patch.object(operating_expenses, "_finance"),
			patch.object(service, "_source", return_value=self.doc),
			patch.object(frappe, "has_permission", return_value=False),
			patch.object(service, "_fresh_source") as fresh,
			patch.object(operating_expenses, "_request") as transport,
		):
			with self.assertRaises(frappe.PermissionError):
				service.download_source_attachment("OA", "file-1", "v1")
			fresh.assert_not_called()
			transport.assert_not_called()

	def test_attachment_control_is_disabled_without_finance_role(self):
		source = {**self.source, "oa_identity": {"process_instance_id": "instance"}}
		self.doc.custom_purchase_source_json = json.dumps(source)
		self.doc.custom_cashier_payment_evidence = json.dumps(
			{
				"version": "v1",
				"attachments": [
					{"source_id": "file-1", "url": "/api/integrations/erp/purchase-expenses/attachments/1"}
				],
			}
		)
		self.doc.has_permission = lambda permission: True
		from deeplinkerp_branding.services import unified_purchase_service

		with (
			patch.object(service, "_source", return_value=self.doc),
			patch.object(unified_purchase_service, "cashier_evidence_readable", return_value=True),
			patch.object(frappe, "session", frappe._dict(user="purchase@example.test")),
			patch.object(frappe, "get_roles", return_value=["Purchase User"]),
			patch.object(frappe, "has_permission", return_value=True),
		):
			payload = service.get_purchase_source_detail("OA")
		self.assertFalse(payload["cashier"]["attachments"][0]["downloadable"])
		self.assertFalse(payload["can_reconcile"])

	def test_missing_source_is_invalidated_without_removing_manual_values(self):
		cached = {
			**self.source,
			"source_id": "source-1",
			"oa_identity": {"corp_id": "corp", "process_instance_id": "instance"},
			"status": "COMPLETED",
		}
		row = frappe._dict(name="OA", custom_purchase_source_json=json.dumps(cached))
		with (
			patch.object(frappe, "get_all", return_value=[row]),
			patch.object(service.oa, "read_page", return_value=([], None)),
			patch.object(service, "_cache_source") as save,
		):
			snapshots = service._reconcile_cached_sources(Mock(), "2026-10-06T00:00:00Z", set(), [])
			invalid = snapshots[0][0]
			self.assertFalse(invalid["eligible"])
			self.assertEqual(invalid["items"], cached["items"])
			self.assertIn("不存在", "；".join(invalid["issues"]))
			save.assert_not_called()

	def test_duplicate_local_legacy_instances_never_choose_arbitrary_binding(self):
		source = {
			**self.source,
			"source_id": "source-1",
			"oa_identity": {"corp_id": "corp", "process_instance_id": "instance"},
			"instance_count": 1,
		}
		with (
			patch.object(frappe.db, "get_value", side_effect=[None, "legacy-first"]),
			patch.object(
				frappe,
				"get_all",
				return_value=[frappe._dict(name="legacy-first"), frappe._dict(name="legacy-second")],
			),
			patch.object(frappe, "get_doc") as read,
		):
			with self.assertRaises(frappe.ValidationError):
				service._cached_doc(source)
			read.assert_not_called()

	def test_explicit_detail_refresh_returns_current_version_without_cache_write(self):
		self.doc.has_permission = lambda permission: True
		current = {**self.source, "oa_identity": {"process_instance_id": "instance"}, "version": "new"}
		from deeplinkerp_branding.services import unified_purchase_service

		with (
			patch.object(service, "_source", return_value=self.doc),
			patch.object(service, "_fresh_source", return_value=(current, {})) as fresh,
			patch.object(unified_purchase_service, "cashier_evidence_readable", return_value=False),
			patch.object(frappe, "session", frappe._dict(user="Administrator")),
			patch.object(frappe, "has_permission", return_value=True),
		):
			payload = service.get_purchase_source_detail("OA", fresh=1)
		fresh.assert_called_once_with(self.doc)
		self.assertEqual(payload["version"], service._version(current, {}))
		self.assertEqual(payload["source"]["version"], "new")
		self.db.set_value.assert_not_called()
		self.db.commit.assert_not_called()

	def test_detail_separates_raw_beneficiary_verified_roles_and_buyer_proposal(self):
		self.doc.has_permission = lambda permission: True
		self.doc.backfill_imported = 1
		self.doc.custom_purchase_company_proposal = "China"
		self.doc.custom_purchase_beneficiary_company = "Manual Factory"
		self.doc.custom_purchase_project = "MANUAL-PROJECT"
		raw = {
			**self.source,
			"oa_identity": {"process_instance_id": "instance"},
			"beneficiary_company": "New source Factory",
			"beneficiary_company_status": "unique",
			"project": "Factory molds",
			"project_status": "unique",
		}
		self.doc.custom_purchase_source_json = json.dumps(raw)
		from deeplinkerp_branding.services import unified_purchase_service

		def candidates(dt, **kwargs):
			return [frappe._dict(name="NEW-FACTORY" if dt == "Company" else "SOURCE-PROJECT")]

		def native(dt, name, *args):
			return frappe._dict(name=name, company="China" if dt == "Project" else None, is_active="Yes")

		with (
			patch.object(service, "_source", return_value=self.doc),
			patch.object(unified_purchase_service, "cashier_evidence_readable", return_value=False),
			patch.object(frappe, "has_permission", return_value=True),
			patch.object(frappe, "get_list", side_effect=candidates),
			patch.object(frappe, "get_meta", return_value=Mock(has_field=lambda _: True)),
			patch.object(service, "_native", side_effect=native),
		):
			payload = service.get_purchase_source_detail("OA")
		self.assertIsNone(payload.get("purchasing_company"))
		self.assertFalse(payload.get("company_confirmed"))
		self.assertEqual(payload.get("buyer_company_proposal"), "China")
		self.assertEqual(payload.get("beneficiary_company"), "Manual Factory")
		self.assertEqual(payload.get("project"), "MANUAL-PROJECT")
		self.assertEqual(payload.get("beneficiary_company_candidate"), "NEW-FACTORY")
		self.assertEqual(payload["source"]["beneficiary_company"], "New source Factory")
		self.assertEqual(payload["source"]["project"], "Factory molds")
		self.db.set_value.assert_not_called()

	def test_detail_warns_when_raw_project_is_incompatible_without_exposing_hidden_company_hint(self):
		self.doc.has_permission = lambda permission: True
		self.doc.purchase_order = "PO"
		self.doc.custom_purchase_company_proposal = "PRIVATE-COMPANY"
		raw = {
			**self.source,
			"oa_identity": {"process_instance_id": "instance"},
			"project": "Mexico molds",
			"project_status": "unique",
		}
		self.doc.custom_purchase_source_json = json.dumps(raw)
		from deeplinkerp_branding.services import unified_purchase_service

		def native(dt, name, *args):
			if name == "PRIVATE-COMPANY":
				raise frappe.PermissionError("Company denied")
			return frappe._dict(name=name, company="Mexico" if dt == "Project" else None)

		with (
			patch.object(service, "_source", return_value=self.doc),
			patch.object(unified_purchase_service, "cashier_evidence_readable", return_value=False),
			patch.object(frappe, "has_permission", return_value=True),
			patch.object(frappe, "get_list", return_value=[frappe._dict(name="MX-PROJECT")]),
			patch.object(frappe, "get_meta", return_value=Mock(has_field=lambda _: True)),
			patch.object(service, "_native", side_effect=native),
		):
			payload = service.get_purchase_source_detail("OA")
		self.assertEqual(payload.get("purchasing_company"), "China")
		self.assertTrue(payload.get("company_confirmed"))
		self.assertIsNone(payload.get("buyer_company_proposal"))
		self.assertIsNone(payload.get("project_candidate"))
		self.assertEqual(payload.get("project_status"), "incompatible")
		self.assertIn("公司", "；".join(payload.get("role_warnings") or []))
		self.assertEqual(payload["source"]["project"], "Mexico molds")
		self.assertNotIn("PRIVATE-COMPANY", json.dumps(payload))

	def test_detail_cannot_leak_legacy_target_company_when_its_visible_proposal_is_denied(self):
		self.doc.has_permission = lambda permission: True
		self.doc.backfill_imported = 1
		self.doc.target_company = "PRIVATE-LEGACY-COMPANY"
		self.doc.custom_purchase_source_json = json.dumps(
			{**self.source, "oa_identity": {"process_instance_id": "instance"}}
		)
		from deeplinkerp_branding.services import unified_purchase_service

		with (
			patch.object(service, "_source", return_value=self.doc),
			patch.object(unified_purchase_service, "cashier_evidence_readable", return_value=False),
			patch.object(frappe, "has_permission", return_value=True),
			patch.object(service, "_native", side_effect=frappe.PermissionError("Company denied")) as native,
			patch.object(service, "_role_projection", wraps=service._role_projection) as projection,
		):
			payload = service.get_purchase_source_detail("OA")
		self.assertIsNone(payload.get("target_company"))
		self.assertIsNone(payload.get("buyer_company_proposal"))
		self.assertNotIn("PRIVATE-LEGACY-COMPANY", json.dumps(payload))
		native.assert_called_once_with("Company", "PRIVATE-LEGACY-COMPANY")
		projection.assert_called_once()


if __name__ == "__main__": unittest.main()
