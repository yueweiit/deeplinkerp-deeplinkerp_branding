import importlib
import sys
import types
import unittest
from unittest.mock import patch


class FakePermissionError(Exception):
	pass


class FakeValidationError(Exception):
	pass


class FakeDefaults:
	def __init__(self):
		self.values = {}
		self.set_calls = []
		self.clear_calls = []

	def get_user_default(self, key, user=None):
		return self.values.get((user, key))

	def set_user_default(self, key, value, user=None):
		self.set_calls.append((key, value, user))
		self.values[(user, key)] = value

	def clear_user_default(self, key, user=None):
		self.clear_calls.append((key, user))
		self.values.pop((user, key), None)


class FakeDB:
	def __init__(self):
		self.doctype_exists = True
		self.company_default = "dl"

	def exists(self, doctype, name):
		return doctype == "DocType" and name == "DeepLinkERP Interface Settings" and self.doctype_exists

	def get_single_value(self, doctype, fieldname):
		if doctype != "DeepLinkERP Interface Settings" or fieldname != "default_navigation_mode":
			raise AssertionError("unexpected settings lookup")
		return self.company_default


FAKE_FRAPPE = types.ModuleType("frappe")
FAKE_FRAPPE._ = lambda message: message
FAKE_FRAPPE.PermissionError = FakePermissionError
FAKE_FRAPPE.ValidationError = FakeValidationError
FAKE_FRAPPE.session = types.SimpleNamespace(user="employee@example.com")
FAKE_FRAPPE.defaults = FakeDefaults()
FAKE_FRAPPE.db = FakeDB()
FAKE_FRAPPE.get_roles = lambda user=None: ["Employee"]


def throw(message, exception):
	raise exception(message)


FAKE_FRAPPE.throw = throw
FAKE_FRAPPE.whitelist = lambda function=None: function if function else (lambda method: method)

previous_frappe = sys.modules.get("frappe")
sys.modules["frappe"] = FAKE_FRAPPE
try:
	try:
		interface_mode = importlib.import_module(
			"deeplinkerp_branding.deeplinkerp_branding.interface_mode"
		)
	except ModuleNotFoundError:
		interface_mode = None
finally:
	if previous_frappe is None:
		del sys.modules["frappe"]
	else:
		sys.modules["frappe"] = previous_frappe


class InterfaceModeTest(unittest.TestCase):
	def setUp(self):
		self.assertIsNotNone(interface_mode, "interface_mode production module must exist")
		# Collection may reuse the module imported with another test's Frappe stub.
		# Bind and restore this test's dependency, rather than evicting shared modules.
		dependency = patch.object(interface_mode, "frappe", FAKE_FRAPPE)
		dependency.start()
		self.addCleanup(dependency.stop)
		FAKE_FRAPPE.session.user = "employee@example.com"
		FAKE_FRAPPE.defaults = FakeDefaults()
		FAKE_FRAPPE.db = FakeDB()
		FAKE_FRAPPE.get_roles = lambda user=None: ["Employee"]

	def test_constants_define_the_supported_storage_contract(self):
		self.assertEqual(interface_mode.MODE_SETTING_DOCTYPE, "DeepLinkERP Interface Settings")
		self.assertEqual(
			interface_mode.USER_OVERRIDE_KEY,
			"deeplinkerp_navigation_mode_override",
		)
		self.assertEqual(interface_mode.VALID_MODES, {"classic", "dl"})

	def test_missing_or_invalid_company_setting_falls_back_to_dl(self):
		FAKE_FRAPPE.db.doctype_exists = False
		self.assertEqual(interface_mode.get_company_default_mode(), "dl")

		FAKE_FRAPPE.db.doctype_exists = True
		FAKE_FRAPPE.db.company_default = "unknown"
		self.assertEqual(interface_mode.get_company_default_mode(), "dl")

	def test_company_default_is_effective_without_a_user_override(self):
		FAKE_FRAPPE.db.company_default = "classic"

		payload = interface_mode.get_interface_mode_payload()

		self.assertEqual(payload["company_default"], "classic")
		self.assertIsNone(payload["user_override"])
		self.assertEqual(payload["effective_mode"], "classic")

	def test_valid_user_override_wins_and_invalid_stored_value_is_ignored(self):
		FAKE_FRAPPE.db.company_default = "classic"
		FAKE_FRAPPE.defaults.values[
			("employee@example.com", interface_mode.USER_OVERRIDE_KEY)
		] = "dl"
		self.assertEqual(interface_mode.get_interface_mode_payload()["effective_mode"], "dl")

		FAKE_FRAPPE.defaults.values[
			("employee@example.com", interface_mode.USER_OVERRIDE_KEY)
		] = "legacy"
		payload = interface_mode.get_interface_mode_payload()
		self.assertIsNone(payload["user_override"])
		self.assertEqual(payload["effective_mode"], "classic")

	def test_follow_company_clears_only_the_current_user_override(self):
		FAKE_FRAPPE.defaults.values[
			("employee@example.com", interface_mode.USER_OVERRIDE_KEY)
		] = "classic"

		payload = interface_mode.set_user_navigation_mode("follow_company")

		self.assertEqual(
			FAKE_FRAPPE.defaults.clear_calls,
			[(interface_mode.USER_OVERRIDE_KEY, "employee@example.com")],
		)
		self.assertIsNone(payload["user_override"])

	def test_valid_override_is_saved_only_for_the_current_user(self):
		payload = interface_mode.set_user_navigation_mode("classic")

		self.assertEqual(
			FAKE_FRAPPE.defaults.set_calls,
			[(interface_mode.USER_OVERRIDE_KEY, "classic", "employee@example.com")],
		)
		self.assertEqual(payload["user_override"], "classic")
		self.assertEqual(payload["effective_mode"], "classic")

	def test_invalid_override_and_guest_write_are_rejected(self):
		with self.assertRaises(FakeValidationError):
			interface_mode.set_user_navigation_mode("automatic")

		FAKE_FRAPPE.session.user = "Guest"
		with self.assertRaises(FakePermissionError):
			interface_mode.set_user_navigation_mode("dl")

	def test_management_permission_is_reported_for_admin_roles_only(self):
		self.assertFalse(interface_mode.get_interface_mode_payload()["can_manage_company_default"])

		FAKE_FRAPPE.session.user = "manager@example.com"
		FAKE_FRAPPE.get_roles = lambda user=None: ["System Manager"]
		self.assertTrue(interface_mode.get_interface_mode_payload()["can_manage_company_default"])

		FAKE_FRAPPE.session.user = "Administrator"
		FAKE_FRAPPE.get_roles = lambda user=None: ["Administrator"]
		self.assertTrue(interface_mode.get_interface_mode_payload()["can_manage_company_default"])


if __name__ == "__main__":
	unittest.main()
