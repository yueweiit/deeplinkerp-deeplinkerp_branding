"""Configuration, cache scope and concurrent settings edits without external IO."""
import copy
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

import frappe
from deeplinkerp_branding.services import operating_expenses as service
from deeplinkerp_branding.services import operating_sync_config as config
from deeplinkerp_branding.services import operating_oa_http as http
from deeplinkerp_branding.services import operating_oa_source as oa
from tests.test_operating_oa_source import approval

NEW_CODE = "PROC-11111111-2222-3333-4444-555555555555"


class SyncSettingsTests(unittest.TestCase):
    def settings(self):
        settings = frappe._dict(enabled=1, company_mappings=[
            frappe._dict(source_company="Old name", company="Legal"),
            frappe._dict(source_company="source:application", company="Reviewed Legal")],
            approval_templates=config.templates({}), cursor="old-cursor", until="old-boundary", preview_fingerprint="old-preview")
        settings.set = lambda name, value: settings.update({name:[frappe._dict(row) for row in value]})
        return settings

    def test_defaults_preserve_original_scope_and_history_cannot_be_deleted(self):
        defaults = config.templates({})
        self.assertEqual(config.process_codes({}), oa.PROCESS_CODES)
        changed = copy.deepcopy(defaults)
        changed[0]["enabled"] = 0
        changed.append(dict(enabled=1, template_name="New", process_code=NEW_CODE))
        self.assertEqual(len(config.validate_templates(changed, defaults)),8)
        self.assertNotIn(defaults[0]["process_code"], config.process_codes({"approval_templates":changed},enabled_only=True))
        self.assertIn(defaults[0]["process_code"], config.process_codes({"approval_templates":changed}))
        for bad in ([], defaults[1:], [*defaults, defaults[0]], [*defaults, dict(enabled=1, template_name="X",process_code="unscoped")]):
            with self.subTest(bad=bad), self.assertRaises(ValueError): config.validate_templates(bad,defaults)

    def test_save_preserves_application_decisions_disables_sync_and_checks_revision(self):
        settings = self.settings()
        version = service._sync_config_version(settings)
        templates = copy.deepcopy(settings.approval_templates)
        templates[0]["enabled"] = 0
        with patch.object(service,"_manager"), patch.object(service,"_settings",return_value=settings), \
             patch.object(service,"_read"), patch.object(service,"_save") as save, \
             patch.object(service,"get_sync_settings",return_value={}):
            service.save_sync_settings({"Old name":"Legal","Older name":"Legal"}, approval_templates=templates, expected_config_version=version)
            self.assertEqual(service._maps(settings),{"Old name":"Legal","Older name":"Legal","source:application":"Reviewed Legal"})
            self.assertEqual(settings.enabled,0)
            self.assertIsNone(settings.cursor);self.assertIsNone(settings.until);self.assertIsNone(settings.preview_fingerprint)
            save.assert_called_once()
            with self.assertRaises(frappe.ValidationError):
                service.save_sync_settings({}, approval_templates=templates, expected_config_version=version)
            self.assertEqual(save.call_count,1)

    def test_configured_template_keeps_country_and_request_type_guards(self):
        row = approval();row["process_code"] = NEW_CODE
        self.assertFalse(oa.in_scope(row))
        self.assertTrue(oa.in_scope(row,process_codes=(NEW_CODE,)))
        self.assertEqual(oa.merge_application(row,process_codes=(NEW_CODE,))["approvals"]["eligibility"],"eligible")
        row["form_component_values"][1]["value"]="墨西哥"
        self.assertFalse(oa.in_scope(row,process_codes=(NEW_CODE,)))
        self.assertEqual(oa.merge_application(row,process_codes=(NEW_CODE,))["approvals"]["eligibility"],"blocked")

    def test_disabled_template_history_survives_reconciliation_and_exact_read(self):
        settings=self.settings();settings.approval_templates[0]["enabled"]=0
        row=approval(updated_at="2026-03-01T00:00:00Z")
        raw=oa.merge_application(row)
        doc=SimpleNamespace(name=raw["source_id"],source_version=raw["version"],company=None,issues="")
        cached=[(doc,raw)];roots={("corp","instance-1"):doc.name}
        with patch.object(service,"_settings",return_value=settings), patch.object(service,"_oa_cached_identities",return_value=(cached,roots)), \
             patch.object(http,"configuration",return_value=("http://oa:3005",("corp",))), \
             patch.object(http,"read_page",return_value=([row],None)) as read, \
             patch.object(http,"attachment_manifests",return_value=[]), patch.object(service,"_upsert") as upsert:
            service._source_page({"limit":100})
            self.assertNotIn(oa.PROCESS_CODES[0],read.call_args.kwargs["process_codes"])
            exact=service._source_page({"source_id":doc.name,"limit":1})
            self.assertIn(oa.PROCESS_CODES[0],read.call_args.kwargs["process_codes"])
            self.assertEqual(exact["items"][0]["source_id"],doc.name)
            self.assertEqual(service._reconcile_oa_cache("2026-03-03T00:00:00Z",{}),0)
            upsert.assert_not_called()

    def reconcile_disabled(self, original, current, *, extra=(), manifests=(), maps=None, doc_values=None):
        settings = self.settings()
        for template in settings.approval_templates:
            if template["process_code"] == original["process_code"]:
                template["enabled"] = 0
        raw = oa.merge_application(original)
        raw.update(source_id="legacy-root", retired_cashier_evidence={"payments": [{"source_id": "old-payment"}]})
        raw["version"] = oa.digest({key: value for key, value in raw.items() if key != "version"})
        doc = frappe._dict(name="legacy-root", source_version=raw["version"], company=None, issues="")
        doc.update(doc_values or {})
        roots = {(original["corp_id"], original["process_instance_id"]): doc.name}
        with patch.object(service, "_settings", return_value=settings), \
             patch.object(service, "_oa_cached_identities", return_value=([(doc, raw)], roots)), \
             patch.object(http, "configuration", return_value=("http://oa:3005", ("corp",))), \
             patch.object(http, "read_page", return_value=([current, *extra], None)), \
             patch.object(http, "attachment_manifests", return_value=list(manifests)) as attachments, \
             patch.object(service, "_upsert") as upsert:
            changed = service._reconcile_oa_cache("2026-03-03T00:00:00Z", maps or {})
        return changed, upsert, attachments, raw

    def test_disabled_history_refreshes_status_amount_and_archives_without_importing_new_sources(self):
        original = approval(updated_at="2026-03-01T00:00:00Z")
        original.update(status="RUNNING", result=None)
        current = copy.deepcopy(original)
        current.update(status="COMPLETED", result="agree", updated_at="2026-03-02T00:00:00Z")
        current["form_component_values"][2]["value"] = "250.00"
        new_source = {**current, "process_instance_id": "not-imported"}
        manifest = {"corp_id": "corp", "process_instance_id": "instance-1", "file_id": "file-1",
                    "file_name": "proof.pdf", "archive_status": "archived", "sha256": "a" * 64, "actual_size": 20}
        for status, result in (("COMPLETED", "agree"), ("COMPLETED", "refuse"), ("TERMINATED", None)):
            with self.subTest(status=status, result=result):
                current.update(status=status, result=result)
                changed, upsert, attachments, raw = self.reconcile_disabled(
                    original, current, extra=[new_source], manifests=[manifest])
                self.assertEqual(changed, 1)
                upsert.assert_called_once()
                item = upsert.call_args.args[0]
                self.assertEqual(item["source_id"], "legacy-root")
                self.assertEqual(item["amount"], "250.00")
                self.assertEqual(item["approvals"]["raw"]["status"], status)
                self.assertEqual(item["approvals"]["raw"]["result"], result)
                self.assertEqual(item["retired_cashier_evidence"], raw["retired_cashier_evidence"])
                self.assertEqual(item["attachments"], oa.archive_attachments(current, [manifest]))
                self.assertEqual(item["payments"], [])
                self.assertIsNone(item["paid_amount"])
                self.assertNotEqual(item["version"], raw["version"])
                attachments.assert_called_once_with("http://oa:3005", [current], exact=False)
                # The next completed scan must not write the same projection again.
                unchanged, writes, _, _ = self.reconcile_disabled(
                    original, current, manifests=[manifest], doc_values={"source_version": item["version"]})
                self.assertEqual(unchanged, 0)
                writes.assert_not_called()

    def test_disabled_history_keeps_snapshot_and_withdrawal_guards(self):
        original = approval(updated_at="2026-03-01T00:00:00Z")
        future = {**original, "updated_at": "2026-03-04T00:00:00Z", "status": "TERMINATED"}
        changed, upsert, attachments, _ = self.reconcile_disabled(original, future)
        self.assertEqual(changed, 0)
        upsert.assert_not_called()
        attachments.assert_not_called()
        deleted = {**original, "deleted_at": "2026-03-02T00:00:00Z"}
        outside = copy.deepcopy(original)
        outside["form_component_values"][1]["value"] = "墨西哥"
        for current in (deleted, outside):
            with self.subTest(deleted=bool(current.get("deleted_at"))):
                changed, upsert, attachments, raw = self.reconcile_disabled(original, current)
                self.assertEqual(changed, 1)
                upsert.assert_called_once_with(oa.withdrawn_source(raw, current), {})
                attachments.assert_not_called()

    def test_disabled_history_refreshes_company_mapping_even_when_source_version_is_unchanged(self):
        original = approval(updated_at="2026-03-01T00:00:00Z", originator_dept_name="Old name")
        changed, upsert, _, _ = self.reconcile_disabled(original, original, maps={"Old name": "Legal"})
        self.assertEqual(changed, 1)
        self.assertEqual(upsert.call_args.args[1], {"Old name": "Legal"})

    def test_disabled_history_attachment_failure_does_not_replace_cached_evidence(self):
        original = approval(updated_at="2026-03-01T00:00:00Z")
        settings = self.settings()
        for template in settings.approval_templates:
            template["enabled"] = 0
        raw = oa.merge_application(original)
        doc = frappe._dict(name=raw["source_id"], source_version=raw["version"])
        with patch.object(service, "_settings", return_value=settings), \
             patch.object(service, "_oa_cached_identities", return_value=([(doc, raw)], {("corp", "instance-1"):doc.name})), \
             patch.object(http, "configuration", return_value=("http://oa:3005", ("corp",))), \
             patch.object(http, "read_page", return_value=([original], None)), \
             patch.object(http, "attachment_manifests", side_effect=RuntimeError("archive unavailable")), \
             patch.object(service, "_upsert") as upsert:
            with self.assertRaisesRegex(RuntimeError, "archive unavailable"):
                service._reconcile_oa_cache("2026-03-03T00:00:00Z", {})
            upsert.assert_not_called()

    def test_empty_active_scope_cannot_enable_or_preview(self):
        settings=self.settings()
        for template in settings.approval_templates:template["enabled"]=0
        with patch.object(service,"_settings",return_value=settings), patch.object(service,"_manager"), patch.object(service,"_source_page") as read:
            for action in (service.preview_sync,lambda:service.enable_sync("old-preview")):
                with self.assertRaises(frappe.ValidationError):action()
            read.assert_not_called()

    def test_cursor_is_bound_to_template_order_and_legacy_defaults_remain_compatible(self):
        import base64,json
        until="2026-03-03T00:00:00Z"
        cursor=http._encode_cursor(until,0,"remote",("corp",),(NEW_CODE,))
        self.assertEqual(http._decode_cursor(cursor,until,("corp",),1,(NEW_CODE,)),(0,"remote"))
        with self.assertRaises(frappe.ValidationError):
            http._decode_cursor(cursor,until,("corp",),7,oa.PROCESS_CODES)
        old=base64.urlsafe_b64encode(json.dumps([until,0,"remote",["corp"]]).encode()).decode()
        self.assertEqual(http._decode_cursor(old,until,("corp",),7),(0,"remote"))
        with self.assertRaises(frappe.ValidationError):http._decode_cursor(old,until,("corp",),1,(NEW_CODE,))

    def test_new_template_changes_preview_fingerprint(self):
        settings=self.settings();result={"items":[],"end":True}
        with patch.object(http,"configuration",return_value=("http://oa:3005",("corp",))):
            before=service._sync_preview_fingerprint(settings,result)
            settings.approval_templates[0]["enabled"]=0
            self.assertNotEqual(before,service._sync_preview_fingerprint(settings,result))

    def test_network_preview_rejects_concurrent_configuration_changes_before_save(self):
        before=self.settings();after=self.settings();after.approval_templates[0]["enabled"]=0
        with patch.object(service,"_manager"), patch.object(service,"_settings",side_effect=[before,after]) as settings, \
             patch.object(service,"_source_page",return_value={"items":[],"end":True}), patch.object(service,"_save") as save:
            with self.assertRaises(frappe.ValidationError):service.preview_sync()
            self.assertEqual(settings.call_args_list[0].kwargs,{})
            self.assertEqual(settings.call_args_list[1].kwargs,{"for_update":True})
            save.assert_not_called()

    def test_settings_cannot_inject_a_new_per_application_company_decision(self):
        settings=self.settings()
        with patch.object(service,"_manager"), patch.object(service,"_settings",return_value=settings), patch.object(service,"_read"), patch.object(service,"_save") as save:
            with self.assertRaises(frappe.ValidationError):service.save_sync_settings({"source:another":"Legal"})
            save.assert_not_called()

    def test_metadata_seed_is_idempotent_and_does_not_change_sync_or_companies(self):
        from deeplinkerp_branding.operating_expense_install import ensure_sync_templates
        settings=self.settings();settings.approval_templates=[]
        old_maps=copy.deepcopy(settings.company_mappings)
        with patch.object(service,"_settings",return_value=settings), patch.object(service,"_save") as save:
            ensure_sync_templates();ensure_sync_templates()
            save.assert_called_once()
            self.assertEqual(len(settings.approval_templates),7)
            self.assertEqual(settings.company_mappings,old_maps)
            self.assertEqual(settings.enabled,1)
            self.assertEqual(settings.cursor,"old-cursor")
