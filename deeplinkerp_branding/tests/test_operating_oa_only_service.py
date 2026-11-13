"""Site-free tests for isolated procurement cashier transport and credentials."""
import copy
import unittest
from unittest.mock import Mock, patch

import frappe

from deeplinkerp_branding.services import operating_expenses as service
from deeplinkerp_branding.services import purchase_cashier_http as procurement
from deeplinkerp_branding.tests._purchase_test_support import install_native_throw


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
