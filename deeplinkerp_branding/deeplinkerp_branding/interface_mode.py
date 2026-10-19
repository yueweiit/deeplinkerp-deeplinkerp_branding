import frappe


MODE_SETTING_DOCTYPE = "DeepLinkERP Interface Settings"
MODE_SETTING_FIELD = "default_navigation_mode"
USER_OVERRIDE_KEY = "deeplinkerp_navigation_mode_override"
FOLLOW_COMPANY = "follow_company"
DEFAULT_MODE = "dl"
VALID_MODES = {"classic", "dl"}
ADMIN_ROLE = "System Manager"


def normalize_mode(value):
	mode = str(value or "").strip().lower()
	return mode if mode in VALID_MODES else None


def get_company_default_mode():
	if not frappe.db.exists("DocType", MODE_SETTING_DOCTYPE):
		return DEFAULT_MODE
	return normalize_mode(frappe.db.get_single_value(MODE_SETTING_DOCTYPE, MODE_SETTING_FIELD)) or DEFAULT_MODE


def get_user_override(user=None):
	user = user or frappe.session.user
	if not user or user == "Guest":
		return None
	return normalize_mode(frappe.defaults.get_user_default(USER_OVERRIDE_KEY, user=user))


def resolve_effective_mode(company_default, user_override):
	return normalize_mode(user_override) or normalize_mode(company_default) or DEFAULT_MODE


def can_manage_company_default(user=None):
	user = user or frappe.session.user
	return bool(user == "Administrator" or ADMIN_ROLE in frappe.get_roles(user))


def get_interface_mode_payload(user=None):
	user = user or frappe.session.user
	company_default = get_company_default_mode()
	user_override = get_user_override(user)
	return {
		"company_default": company_default,
		"user_override": user_override,
		"effective_mode": resolve_effective_mode(company_default, user_override),
		"can_manage_company_default": can_manage_company_default(user),
	}


def apply_interface_mode_bootinfo(bootinfo):
	bootinfo["deeplinkerp_interface_mode"] = get_interface_mode_payload()


@frappe.whitelist()
def set_user_navigation_mode(mode):
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(frappe._("Login is required to change the interface mode."), frappe.PermissionError)

	requested_mode = str(mode or "").strip().lower()
	if requested_mode == FOLLOW_COMPANY:
		frappe.defaults.clear_user_default(USER_OVERRIDE_KEY, user=user)
	elif requested_mode in VALID_MODES:
		frappe.defaults.set_user_default(USER_OVERRIDE_KEY, requested_mode, user=user)
	else:
		frappe.throw(frappe._("Invalid interface mode."), frappe.ValidationError)

	return get_interface_mode_payload(user)
