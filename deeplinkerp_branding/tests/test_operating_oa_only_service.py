"""Site-free boundary tests for the retirement of operating cashier calls."""
import copy
import json
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from deeplinkerp_branding.services import operating_expenses as service
from deeplinkerp_branding.services import operating_oa_http as http
from deeplinkerp_branding.services import operating_oa_source as oa
from deeplinkerp_branding.services import operating_payment_service as payments
from deeplinkerp_branding.services import purchase_cashier_http as procurement
from deeplinkerp_branding.services.operating_expense_contract import digest, expense_facts, event_fingerprint, company_overview
from deeplinkerp_branding.tests._purchase_test_support import install_native_throw
from tests.test_operating_oa_source import approval


class OAOnlyServiceTests(unittest.TestCase):
    def setUp(self):
        install_native_throw(self)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("frappe.translate.get_all_translations", return_value={}))
        self.stack.enter_context(patch.object(frappe, "conf", {}))
        self.db = Mock()
        self.db.exists.return_value = False
        self.stack.enter_context(patch.object(frappe, "db", self.db))
        self.settings = frappe._dict(company_mappings=[], enabled=1)
        self.settings.get_password = Mock(side_effect=AssertionError("operating must not read credentials"))
        self.stack.enter_context(patch.object(service, "_settings", return_value=self.settings))
        self.stack.enter_context(patch.object(service, "_manager"))
        self.stack.enter_context(patch.object(http, "configuration", return_value=("http://oa:3005", ("corp",))))
        self.cashier = self.stack.enter_context(patch.object(procurement, "_request", side_effect=AssertionError("cashier IO")))

    def read(self, row, cached=()):
        roots = {(raw["oa_identity"]["corp_id"], raw["oa_identity"]["process_instance_id"]): doc.name for doc, raw in cached}
        with patch.object(service, "_oa_cached_identities", return_value=(list(cached), roots)), \
                patch.object(http, "read_page", return_value=([row], None)), \
                patch.object(http, "attachment_manifests", return_value=[]):
            return service._source_page({"limit": 100})["items"][0]

    def test_all_legacy_mode_flags_resolve_to_oa_without_reading_cashier_credentials(self):
        for mode in (None, "oa", "oa_cashier", "cashier"):
            with self.subTest(mode=mode), patch.object(frappe, "conf", {"operating_expense_source_mode": mode}):
                self.assertEqual(service._source_mode(), "oa")
                result = service.get_sync_settings()
                self.assertEqual(result["source_url"], "http://oa:3005")
                self.assertEqual(result["source_mode"], "oa")
                self.assertNotIn("token_configured", result)
                item = self.read(approval(originator_dept_name="Department"))
                self.assertEqual(service._mapped_company(item, {"Department": "Legal Company"}), "Legal Company")
                self.assertIsNone(service._mapped_company(item, {}))
                self.assertEqual(len(service._sync_preview_fingerprint(self.settings, {"items": [item], "end": True})), 64)
        self.settings.get_password.assert_not_called()
        self.cashier.assert_not_called()

    def test_cached_root_and_retired_evidence_survive_repeat_sync_without_current_payment_inference(self):
        row = approval()
        old = oa.merge_application(row)
        old.update(source_id="legacy-root", cashier_source_id="legacy-root", paid_amount="20", pending_amount="80",
                   payments=[{"source_id": "old-payment", "amount": "20", "currency": "CNY"}], payment_evidence_status="recorded")
        before = copy.deepcopy(old)
        doc = SimpleNamespace(name="legacy-root")
        item = self.read(row, [(doc, old)])
        self.assertEqual(item["source_id"], "legacy-root")
        self.assertEqual(item["retired_cashier_evidence"]["payments"], old["payments"])
        self.assertIsNone(item["paid_amount"])
        self.assertEqual(item["payments"], [])
        self.assertEqual(old, before)
        self.assertEqual(self.read(row, [(doc, item)]), item)
        self.cashier.assert_not_called()

    def test_legacy_browser_cannot_change_procurement_token_through_operating_settings(self):
        with self.assertRaisesRegex(frappe.ValidationError, "已改用 OA"), patch.object(service, "_save") as save:
            service.save_sync_settings({}, api_token="obsolete-client-token")
        save.assert_not_called()
        self.settings.get_password.assert_not_called()

    def test_timeline_uses_exact_oa_identity_and_preserves_read_permissions(self):
        row = approval(raw_payload={"tasks": [{"taskId": "t", "userId": "reviewer", "status": "COMPLETED", "result": "AGREE"}]})
        cached = oa.merge_application(row)
        doc = SimpleNamespace(source_id=cached["source_id"], source_json=json.dumps(cached))
        with patch.object(service, "_source", return_value=doc) as access, \
                patch.object(http, "read_page", return_value=([row], None)) as read:
            result = payments.get_approval_timeline("visible-source")
        access.assert_called_once_with("visible-source")
        self.assertEqual(read.call_args.kwargs["identity"], cached["oa_identity"])
        self.assertEqual(result["events"][0]["result"], "AGREE")
        self.cashier.assert_not_called()

    def test_new_takeover_cannot_call_retired_endpoint_or_claim_zero_history(self):
        with patch.object(service, "_finance"), patch.object(service, "_source", return_value=object()), \
                patch.object(payments, "_takeover", return_value=None), patch.object(frappe, "get_doc") as create:
            with self.assertRaisesRegex(frappe.ValidationError, "付款历史"):
                payments.preview_takeover("source", zero_history_confirmed=True)
            with self.assertRaisesRegex(frappe.ValidationError, "付款历史"):
                payments.claim_takeover("source", "v", "h", "request", zero_history_confirmed=True)
        create.assert_not_called()
        self.cashier.assert_not_called()

    def finance_fixture(self):
        item = oa.merge_application(approval(originator_dept_name="Department"))
        doc = frappe._dict(name=item["source_id"], source_id=item["source_id"], company="Company")
        mapping = {"company": "Company", "actual_incurred": True, "no_existing_erp_coverage": True,
                   "recognition_mode": "new", "posting_date": "2026-01-01", "party_type": "Supplier", "party": "Vendor",
                   "payable_account": "Payable", "payable_exchange_rate": "1",
                   "expense_lines": [{"account": "Expense", "source_amount": "100", "amount": "100", "exchange_rate": "1"}],
                   "expense_fingerprint": digest(expense_facts(item)),
                   "payments": {"old-payment": {"bank_account": "Bank", "bank_amount": "20", "bank_exchange_rate": "1", "payable_exchange_rate": "1"}}}
        def read(doctype, name, *args, **kwargs):
            if doctype == "Company":
                return frappe._dict(name=name, default_currency="CNY")
            if doctype == "Supplier":
                return frappe._dict(name=name)
            if doctype == "Account":
                return frappe._dict(name=name, company="Company", disabled=0, is_group=0, account_currency="CNY",
                                    account_type=name if name in {"Payable", "Bank"} else "Expense", root_type="Expense")
            if doctype == service.EVENT:
                return self.event
            raise AssertionError(doctype)
        self.stack.enter_context(patch.object(service, "_read", side_effect=read))
        return doc, item, mapping

    def test_approved_expense_preview_works_while_new_payment_stays_disabled(self):
        doc, item, mapping = self.finance_fixture()
        preview = service._preview(doc, item, mapping)
        self.assertEqual(preview["accounts"][0]["debit"], "100.00")
        self.assertEqual(preview["accounts"][1]["credit"], "100.00")
        self.assertIn(payments.PAYMENT_NOTICE, service._issues(item))
        self.assertFalse(item["payment_eligibility"]["can_register_payment"])

    def test_expense_preview_keeps_approval_conflict_and_fingerprint_guards(self):
        doc, item, mapping = self.finance_fixture()
        for changed in ({"approvals": {"eligibility": "blocked", "raw": {"status": "RUNNING"}}},
                        {"approvals": {"eligibility": "eligible", "raw": {"scope": "withdrawn"}}},
                        {"source_conflict": True}, {"currency_conflict": True}, {"amount": "101"}):
            with self.subTest(changed=changed), self.assertRaises(frappe.ValidationError):
                service._preview(doc, {**item, **changed}, mapping)

    def test_expense_preview_keeps_company_and_mapping_permissions(self):
        doc, item, mapping = self.finance_fixture()
        self.settings.company_mappings = [frappe._dict(source_company="Department", company="Other Company")]
        with patch.object(service, "_source_page", return_value={"items": [item], "end": True}):
            with self.assertRaisesRegex(frappe.ValidationError, "法律公司映射已变化"):
                service._fresh(doc)
            doc.company = ""
            with self.assertRaisesRegex(frappe.ValidationError, "请先确认法律公司"):
                service._fresh(doc)
        with patch.object(service, "_read", side_effect=frappe.PermissionError), self.assertRaises(frappe.PermissionError):
            service._preview(doc, item, mapping)

    def test_native_linked_expense_draft_validation_uses_expense_eligibility(self):
        source, item, mapping = self.finance_fixture()
        preview = service._preview(source, item, mapping)
        self.event = frappe._dict(source=source.name, journal_entry="JE", fingerprint=preview["fingerprint"], operation="expense")
        journal = frappe._dict(name="JE", company="Company", posting_date=preview["posting_date"], accounts=preview["accounts"],
                              custom_operating_event_key=preview["event_key"], custom_operating_source=source.name,
                              custom_operating_fingerprint=preview["fingerprint"], docstatus=0)
        journal.is_new = lambda: False
        self.db.exists.return_value = True
        self.db.get_value.side_effect = lambda doctype, *a, **kw: preview["event_key"] if doctype == "Journal Entry" else self.event
        with patch.object(service, "_source", return_value=source), patch.object(service, "_fresh", return_value=item), \
                patch.object(payments, "voucher_mapping", return_value=mapping), patch.object(service, "_require_fields"):
            service.validate_operating_journal(journal)

    def historical_fixture(self):
        doc, old, mapping = self.finance_fixture()
        old["payments"] = [{"source_id": "old-payment", "amount": "20", "currency": "CNY", "payment_date": "2026-01-02", "evidence_status": "recorded"}]
        old["attachments"] = [{"source_id": "receipt", "version": "v1", "filename": "receipt.pdf",
                               "url": "/api/integrations/erp/attachments/1", "payment_source_id": "old-payment"}]
        current = self.read(approval(originator_dept_name="Department"), [(doc, old)])
        self.event = frappe._dict(source=doc.name, company=doc.company, journal_entry="JE-PAY", operation="payment",
                                 payment_source_id="old-payment", fingerprint=event_fingerprint(old, mapping, "old-payment"),
                                 provenance_json=json.dumps({"source": old, "mapping": mapping}))
        self.db.get_value.return_value = "JE-PAY"
        self.stack.enter_context(patch.object(service, "_recognition", return_value=frappe._dict(name="JE-EXP", docstatus=1)))
        return doc, current, mapping

    def test_existing_external_payment_voucher_uses_event_bound_evidence_without_takeover(self):
        doc, item, mapping = self.historical_fixture()
        before = copy.deepcopy(item)
        self.assertIs(payments.merge_source(doc, item, context={"record": None}), item)
        preview = service._preview(doc, item, mapping, "old-payment")
        self.assertEqual(preview["fingerprint"], self.event.fingerprint)
        self.assertEqual(preview["accounts"][0]["debit"], "20.00")
        self.assertEqual(preview["posting_date"], "2026-01-02")
        self.assertEqual(item, before)
        self.assertEqual(item["payments"], [])
        self.assertIsNone(item["paid_amount"])

    def test_retired_evidence_cannot_create_a_new_payment_event(self):
        doc, item, mapping = self.historical_fixture()
        self.db.get_value.return_value = None
        with self.assertRaisesRegex(frappe.ValidationError, "实际付款证据不足"):
            service._preview(doc, item, mapping, "old-payment")

    def test_native_historical_payment_save_and_submit_keep_original_fingerprint(self):
        source, item, mapping = self.historical_fixture()
        preview = service._preview(source, item, mapping, "old-payment")
        journal = frappe._dict(name="JE-PAY", company="Company", posting_date=preview["posting_date"],
                              accounts=[frappe._dict(row) for row in preview["accounts"]],
                              custom_operating_event_key=preview["event_key"], custom_operating_source=source.name,
                              custom_operating_fingerprint=preview["fingerprint"], custom_operating_recognition="JE-EXP")
        journal.is_new = lambda: False
        journal.validate_against_jv = Mock()
        self.db.exists.return_value = True
        self.db.get_value.side_effect = lambda doctype, key, fields, **kwargs: (
            preview["event_key"] if doctype == "Journal Entry" else self.event if isinstance(fields, list) else "JE-PAY")
        with patch.object(service, "_source", return_value=source), patch.object(service, "_fresh", return_value=item), \
                patch.object(payments, "voucher_mapping", return_value=mapping), patch.object(service, "_require_fields"):
            for status in (0, 1):
                journal.docstatus = status
                service.validate_operating_journal(journal)
        journal.validate_against_jv.assert_called_once()
        self.assertEqual(journal.accounts[0].reference_name, "JE-EXP")

    def test_historical_payment_still_rejects_total_overpayment(self):
        doc, item, mapping = self.historical_fixture()
        item["retired_cashier_evidence"]["payments"].append({"source_id": "another-payment", "amount": "90", "currency": "CNY",
                                                         "payment_date": "2026-01-03", "evidence_status": "recorded"})
        with self.assertRaisesRegex(frappe.ValidationError, "超过费用确认金额"):
            service._preview(doc, item, mapping, "old-payment")

    def test_historical_payment_rejects_changed_evidence_facts_or_event_association(self):
        doc, item, mapping = self.historical_fixture()
        for field, value in (("amount", "21"), ("currency", "USD"), ("evidence_status", "withdrawn")):
            changed = copy.deepcopy(item)
            changed["retired_cashier_evidence"]["payments"][0][field] = value
            with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
                service._preview(doc, changed, mapping, "old-payment")
        for field, value in (("source", "Other"), ("company", "Other"), ("operation", "expense"), ("payment_source_id", "Other")):
            with self.subTest(field=field), patch.dict(self.event, {field: value}), self.assertRaises(frappe.ValidationError):
                service._preview(doc, item, mapping, "old-payment")
        with self.assertRaisesRegex(frappe.ValidationError, "实际付款证据不足"):
            service._preview(doc, item, mapping, "erp-payment:reversed")

    def test_legacy_exact_identity_reuses_root_and_does_not_double_totals(self):
        old = oa.merge_application(approval())
        old.update(source_system=service.SOURCE_SYSTEM, source_id="legacy-root", corp_id="corp", process_instance_id="instance-1")
        old.pop("oa_identity")
        docs = [frappe._dict(name="legacy-root", source_json=json.dumps(old), source_version="old")]
        with patch.object(frappe, "get_all", return_value=docs) as get_all, \
                patch.object(http, "read_page", return_value=([approval()], None)), patch.object(http, "attachment_manifests", return_value=[]):
            for _ in range(2):
                item = service._source_page({"limit": 100})["items"][0]
                self.assertEqual(item["source_id"], "legacy-root")
                self.assertIn(service.SOURCE_SYSTEM, get_all.call_args.kwargs["filters"]["source_system"][1])
                docs[0].update(source_json=json.dumps(item), source_version=item["version"])
        overview, _ = company_overview([{**item, "company": "Company"}])
        self.assertEqual(overview[0]["currencies"]["CNY"]["amount"], "100.00")

    def test_incomplete_conflicting_or_duplicate_legacy_identity_stops_before_oa_io(self):
        old = {"source_system": service.SOURCE_SYSTEM, "corp_id": "corp", "process_instance_id": "instance-1"}
        for raw in ({**old, "corp_id": None}, {**old, "identity_conflict": True}, {**old, "oa_identity": []},
                    {**old, "oa_identity": {"corp_id": "other", "process_instance_id": "instance-1"}}):
            with self.subTest(raw=raw), patch.object(frappe, "get_all", return_value=[frappe._dict(name="old", source_json=json.dumps(raw))]), \
                    patch.object(http, "read_page") as read, self.assertRaises(frappe.ValidationError):
                service._source_page({"limit": 100})
            read.assert_not_called()
        docs = [frappe._dict(name=name, source_json=json.dumps(old)) for name in ("old", "duplicate")]
        with patch.object(frappe, "get_all", return_value=docs), self.assertRaisesRegex(frappe.ValidationError, "身份重复"):
            service._oa_cached_identities()



class ProcurementTransportTests(unittest.TestCase):
    def setUp(self):
        install_native_throw(self)
        self.translations = patch("frappe.translate.get_all_translations", return_value={})
        self.translations.start()
        self.addCleanup(self.translations.stop)

    def test_credential_rotation_preserves_oa_settings_and_uses_managed_save(self):
        settings = frappe._dict(enabled=1, company_mappings=[{"source_company": "Department", "company": "Company"}],
                                cursor="cursor", until="until", preview_fingerprint="preview", changed_since="changed")
        before = copy.deepcopy(settings)
        settings.save = Mock(side_effect=lambda **kwargs: service.validate_managed_document(settings))
        settings.get_password = Mock(return_value="synthetic-secret")
        with patch.object(service, "_manager"), patch.object(service, "_settings", return_value=settings) as get_settings:
            self.assertEqual(procurement.save_settings("synthetic-secret"), {"token_configured": True})
            get_settings.assert_called_once_with(for_update=True)
            self.assertEqual(procurement.get_settings(), {"token_configured": True})
        settings.save.assert_called_once_with(ignore_permissions=True)
        for field, value in before.items():
            self.assertEqual(settings[field], value)

    def test_procurement_settings_reject_non_admin_before_credentials_or_writes(self):
        with patch.object(frappe, "session", frappe._dict(user="ordinary-user")), patch.object(frappe, "get_roles", return_value=["Purchase User"]), \
                patch.object(service, "_settings") as settings:
            with self.assertRaises(frappe.PermissionError):
                procurement.get_settings()
            with self.assertRaises(frappe.PermissionError):
                procurement.save_settings("synthetic-secret")
        settings.assert_not_called()

    def test_invalid_procurement_token_does_not_write_or_echo_secret(self):
        with patch.object(service, "_manager"), patch.object(service, "_settings") as settings:
            for value in (None, {}, "", "  ", " leading", "trailing ", "synthetic\nsecret", "a" * 4097):
                with self.subTest(kind=type(value)), self.assertRaisesRegex(frappe.ValidationError, "密钥格式无效"):
                    procurement.save_settings(value)
        settings.assert_not_called()

    def test_operating_routes_are_rejected_before_credentials_and_network(self):
        with patch("frappe.translate.get_all_translations", return_value={}), \
                patch.object(frappe, "get_single") as credentials, \
                patch.object(procurement.requests, "get") as get, patch.object(procurement.requests, "post") as post:
            for route in ("", "/workflow", "/takeover-preview", "/takeover-claim"):
                with self.subTest(route=route), self.assertRaises(ValueError):
                    procurement._request("/api/integrations/erp/operating-expenses" + route)
        credentials.assert_not_called()
        get.assert_not_called()
        post.assert_not_called()
