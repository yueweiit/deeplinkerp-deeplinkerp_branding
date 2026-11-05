import hashlib
from functools import lru_cache
from pathlib import Path

import frappe

from deeplinkerp_branding.deeplinkerp_branding.interface_mode import apply_interface_mode_bootinfo


BRAND_NAME = "Deeplinkerp"
BRAND_LOGO_URL = "/assets/deeplinkerp_branding/logo/erp_logo_rounded_192.png?v=0.0.9"
ERP_NEXT_NAME = "ERPNext"
DEEPLINKERP_ICON_NAME = "Deeplinkerp"
FRAPPE_FRAMEWORK_NAME = "Frappe Framework"
DLP_FRAMEWORK_NAME = "DLP Framework"
FRAMEWORK_ICON_NAME = "Framework"
FRAPPE_FRAMEWORK_ICON_NAMES = (FRAMEWORK_ICON_NAME, FRAPPE_FRAMEWORK_NAME)
ADMIN_ONLY_ROLE = "System Manager"
DLP_ERP_IDX = 0
DLP_FRAMEWORK_IDX = -1
SETTINGS_DOCNAME = "Deeplinkerp Settings"
SETTINGS_LABEL = "Deeplinkerp Settings"
SETTINGS_ICON_URL = "/assets/erpnext/icons/desktop_icons/solid/erpnext_settings.svg"
PRODUCT_APP_NAMES = {"ai_assistant", "mes_integration"}
PRODUCT_APP_BY_TITLE = {
	"AI Assistant": "ai_assistant",
	"Mes Integration": "mes_integration",
	"MES Integration": "mes_integration",
	"MES Integration Log": "mes_integration",
}
PRODUCT_MODULE_APP_KEYS = {
	"ai assistant": "ai_assistant",
	"ai_assistant": "ai_assistant",
	"mes integration": "mes_integration",
	"mes_integration": "mes_integration",
	"mes integration log": "mes_integration",
	"mes_integration_log": "mes_integration",
}
DESKTOP_ICON_MODULE_BY_NAME = {
	"Accounting": "Accounts",
}
PURCHASE_PAGE_CODE_SOURCES = {
	"purchase-payables": ("deeplinkerp_branding/page/purchase_payables/purchase_payables.js",),
	"purchase-payment-records": (
		"deeplinkerp_branding/page/purchase_payment_records/purchase_payment_records.js",
		"public/js/purchase_payments.js",
	),
}


@lru_cache(maxsize=2)
def _purchase_page_code_version(name):
	"""Read fixed release sources once per worker; no database/cache writes."""
	base = Path(__file__).resolve().parents[1]
	digest = hashlib.sha256()
	try:
		for relative in PURCHASE_PAGE_CODE_SOURCES[name]:
			digest.update((base / relative).read_bytes())
	except OSError:
		return None  # Optional package files must not prevent Desk boot.
	return digest.hexdigest()


def _apply_purchase_page_code_versions(bootinfo):
	# Preserve native permission-filtered membership and metadata stamps. Desk's
	# existing sync_pages then evicts only these Page code caches, not preferences.
	page_info = bootinfo.get("page_info") or {}
	for name in PURCHASE_PAGE_CODE_SOURCES:
		if name not in page_info:
			continue
		version = _purchase_page_code_version(name)
		if version:
			page = page_info[name]
			modified = str(page.get("modified") or "").split("|dlp-purchase-code:", 1)[0]
			page["modified"] = f"{modified}|dlp-purchase-code:{version}"

SIDEBAR_ITEM_FIELDS = (
	"child",
	"collapsible",
	"display_depends_on",
	"filters",
	"icon",
	"indent",
	"keep_closed",
	"label",
	"link_to",
	"link_type",
	"navigate_to_tab",
	"route_options",
	"show_arrow",
	"type",
	"url",
)


def apply_deeplinkerp_settings_branding():
	"""Replace the ERPNext Settings desktop entry with Deeplinkerp Settings."""
	apply_logo_branding()

	if frappe.db.exists("Desktop Icon", "Deeplinkerp Branding"):
		frappe.db.set_value("Desktop Icon", "Deeplinkerp Branding", "hidden", 1, update_modified=False)

	apply_erpnext_desktop_icon_branding()
	apply_framework_desktop_icon_branding()
	apply_settings_desktop_icon_branding()

	copy_erpnext_settings_sidebar_items()

	# Keep the native sidebar as the source of truth, but remove its Workspace
	# after copying so Setup cannot show both the native and branded entries.
	# ERPNext may restore this standard Workspace during migrate; this
	# after_migrate hook removes it again without touching the native sidebar.
	if frappe.db.exists("Workspace", "ERPNext Settings"):
		frappe.delete_doc(
			"Workspace",
			"ERPNext Settings",
			force=True,
			ignore_permissions=True,
		)

	if frappe.db.exists("Workspace", SETTINGS_DOCNAME):
		frappe.db.set_value(
			"Workspace",
			SETTINGS_DOCNAME,
			{"app": "deeplinkerp_branding", "label": SETTINGS_LABEL, "title": SETTINGS_LABEL},
			update_modified=False,
		)
		set_doc_roles("Workspace", SETTINGS_DOCNAME, [ADMIN_ONLY_ROLE])

	if frappe.db.exists("Workspace Sidebar", SETTINGS_DOCNAME):
		frappe.db.set_value(
			"Workspace Sidebar",
			SETTINGS_DOCNAME,
			{"app": "deeplinkerp_branding", "standard": 1, "title": SETTINGS_LABEL},
			update_modified=False,
		)

	frappe.cache.delete_key("desktop_icons")
	frappe.cache.delete_key("bootinfo")
	frappe.clear_cache()


def apply_erpnext_desktop_icon_branding():
	if not frappe.db.exists("Desktop Icon", DEEPLINKERP_ICON_NAME):
		icon = frappe.new_doc("Desktop Icon")
		icon.update(
			{
				"doctype": "Desktop Icon",
				"name": DEEPLINKERP_ICON_NAME,
				"label": BRAND_NAME,
				"app": "deeplinkerp_branding",
				"hidden": 1,
				"icon_type": "App",
				"idx": DLP_ERP_IDX,
				"link": "/desk/home",
				"link_type": "External",
				"logo_url": BRAND_LOGO_URL,
				"standard": 1,
			}
		)
		icon.flags.ignore_permissions = True
		icon.insert(ignore_permissions=True, ignore_if_duplicate=True)

	frappe.db.set_value(
		"Desktop Icon",
		DEEPLINKERP_ICON_NAME,
		{
			"app": "deeplinkerp_branding",
			"hidden": 1,
			"icon_type": "App",
			"idx": DLP_ERP_IDX,
			"label": BRAND_NAME,
			"link": "/desk/home",
			"link_type": "External",
			"logo_url": BRAND_LOGO_URL,
			"standard": 1,
		},
		update_modified=False,
	)

	children = frappe.get_all(
		"Desktop Icon", filters={"parent_icon": ["in", [ERP_NEXT_NAME, DEEPLINKERP_ICON_NAME]]}, pluck="name"
	)
	for child in children:
		frappe.db.set_value("Desktop Icon", child, "parent_icon", "", update_modified=False)

	for icon_name in (ERP_NEXT_NAME, "ERPNext Settings"):
		if frappe.db.exists("Desktop Icon", icon_name):
			frappe.delete_doc("Desktop Icon", icon_name, force=1, ignore_permissions=True)



def apply_settings_desktop_icon_branding():
	if not frappe.db.exists("Desktop Icon", SETTINGS_DOCNAME):
		icon = frappe.new_doc("Desktop Icon")
		icon.update(
			{
				"doctype": "Desktop Icon",
				"name": SETTINGS_DOCNAME,
				"label": SETTINGS_LABEL,
				"app": "deeplinkerp_branding",
				"hidden": 0,
				"icon_type": "Link",
				"idx": 10,
				"link_to": SETTINGS_DOCNAME,
				"link_type": "Workspace Sidebar",
				"logo_url": SETTINGS_ICON_URL,
				"standard": 1,
				"roles": [{"role": ADMIN_ONLY_ROLE}],
			}
		)
		icon.flags.ignore_permissions = True
		icon.insert(ignore_permissions=True, ignore_if_duplicate=True)

	frappe.db.set_value(
		"Desktop Icon",
		SETTINGS_DOCNAME,
		{
			"app": "deeplinkerp_branding",
			"hidden": 0,
			"icon_type": "Link",
			"label": SETTINGS_LABEL,
			"idx": 10,
			"link_to": SETTINGS_DOCNAME,
			"link_type": "Workspace Sidebar",
			"logo_url": SETTINGS_ICON_URL,
			"standard": 1,
		},
		update_modified=False,
	)
	set_desktop_icon_roles(SETTINGS_DOCNAME, [ADMIN_ONLY_ROLE])


def apply_framework_desktop_icon_branding():
	if not frappe.db.exists("Desktop Icon", DLP_FRAMEWORK_NAME):
		icon = frappe.new_doc("Desktop Icon")
		icon.update(
			{
				"doctype": "Desktop Icon",
				"name": DLP_FRAMEWORK_NAME,
				"label": DLP_FRAMEWORK_NAME,
				"app": "deeplinkerp_branding",
				"hidden": 0,
				"icon_type": "App",
				"idx": DLP_FRAMEWORK_IDX,
				"link": "/desk/build",
				"link_type": "External",
				"logo_url": BRAND_LOGO_URL,
				"standard": 1,
				"roles": [{"role": ADMIN_ONLY_ROLE}],
			}
		)
		icon.flags.ignore_permissions = True
		icon.insert(ignore_permissions=True, ignore_if_duplicate=True)

	frappe.db.set_value(
		"Desktop Icon",
		DLP_FRAMEWORK_NAME,
		{
			"app": "deeplinkerp_branding",
			"hidden": 0,
			"icon_type": "App",
			"idx": DLP_FRAMEWORK_IDX,
			"label": DLP_FRAMEWORK_NAME,
			"link": "/desk/build",
			"link_type": "External",
			"logo_url": BRAND_LOGO_URL,
			"standard": 1,
		},
		update_modified=False,
	)
	set_desktop_icon_roles(DLP_FRAMEWORK_NAME, [ADMIN_ONLY_ROLE])

	children = frappe.get_all("Desktop Icon", filters={"parent_icon": ["in", FRAPPE_FRAMEWORK_ICON_NAMES]}, pluck="name")
	for child in children:
		frappe.db.set_value("Desktop Icon", child, "parent_icon", DLP_FRAMEWORK_NAME, update_modified=False)

	for icon_name in FRAPPE_FRAMEWORK_ICON_NAMES:
		if frappe.db.exists("Desktop Icon", icon_name):
			frappe.delete_doc("Desktop Icon", icon_name, force=1, ignore_permissions=True)

def apply_logo_branding():
	for doctype in ("System Settings", "Website Settings"):
		if frappe.db.exists("DocType", doctype):
			frappe.db.set_single_value(doctype, "app_name", BRAND_NAME, update_modified=False)

	for doctype in ("Navbar Settings", "Website Settings"):
		if frappe.db.exists("DocType", doctype):
			frappe.db.set_single_value(doctype, "app_logo", BRAND_LOGO_URL, update_modified=False)


def set_doc_roles(doctype, docname, roles):
	if not frappe.db.exists(doctype, docname):
		return

	doc = frappe.get_doc(doctype, docname)
	existing_roles = [row.role for row in doc.roles]
	if existing_roles == roles:
		return

	doc.set("roles", [])
	for role in roles:
		doc.append("roles", {"role": role})

	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)


def set_desktop_icon_roles(icon_name, roles):
	if not frappe.db.exists("Desktop Icon", icon_name):
		return

	icon = frappe.get_doc("Desktop Icon", icon_name)
	existing_roles = [row.role for row in icon.roles]
	if existing_roles == roles:
		return

	icon.set("roles", [])
	for role in roles:
		icon.append("roles", {"role": role})

	icon.flags.ignore_permissions = True
	icon.save(ignore_permissions=True)


def can_access_admin_only_icons():
	return frappe.session.user == "Administrator" or ADMIN_ONLY_ROLE in frappe.get_roles()


def bypass_module_filter():
	return frappe.session.user == "Administrator" or "Workspace Manager" in frappe.get_roles()


def get_blocked_modules_for_current_user():
	if bypass_module_filter():
		return set()

	blocked_modules = []
	for user in ("Administrator", frappe.session.user):
		blocked_modules.extend(frappe.get_cached_doc("User", user).get_blocked_modules())

	return set(blocked_modules)


def is_module_blocked(module, blocked_modules):
	return bool(module and module in blocked_modules)


def filter_workspace_sidebar_items(workspace_sidebar_item, blocked_modules):
	for key, sidebar in list(workspace_sidebar_item.items()):
		if is_module_blocked(sidebar.get("module"), blocked_modules):
			del workspace_sidebar_item[key]


def get_desktop_icon_sidebar_keys(icon):
	keys = []
	for sidebar in (icon.get("link_to"), icon.get("label"), icon.get("name")):
		if sidebar:
			keys.append(sidebar.lower())
	return keys


def get_desktop_icon_module(icon):
	return DESKTOP_ICON_MODULE_BY_NAME.get(icon.get("name")) or DESKTOP_ICON_MODULE_BY_NAME.get(icon.get("label"))


def is_workspace_sidebar_icon_visible(icon, workspace_sidebar_item, blocked_modules):
	if any(sidebar_key in workspace_sidebar_item for sidebar_key in get_desktop_icon_sidebar_keys(icon)):
		return True

	module = get_desktop_icon_module(icon)
	return bool(module and not is_module_blocked(module, blocked_modules))


def apply_boot_branding(bootinfo):
	"""Brand app labels in bootinfo before the desk sidebar is rendered."""
	_apply_purchase_page_code_versions(bootinfo)
	app_data = bootinfo.get("app_data", [])
	workspace_sidebar_item = bootinfo.get("workspace_sidebar_item", {})
	module_app = bootinfo.get("module_app", {})
	show_admin_only_icons = can_access_admin_only_icons()
	blocked_modules = get_blocked_modules_for_current_user()

	for app in app_data:
		app_name = app.get("app_name")
		if app_name == "erpnext" or app.get("app_title") == ERP_NEXT_NAME:
			app["app_title"] = BRAND_NAME
		elif app_name == "frappe" or app.get("app_title") == FRAPPE_FRAMEWORK_NAME:
			app["app_title"] = DLP_FRAMEWORK_NAME
		elif app_name in PRODUCT_APP_NAMES:
			app["app_title"] = BRAND_NAME

	for key, app_name in PRODUCT_MODULE_APP_KEYS.items():
		module_app[key] = app_name

	for key, sidebar in workspace_sidebar_item.items():
		label = sidebar.get("label")
		module = sidebar.get("module")
		if sidebar.get("app") in PRODUCT_APP_NAMES:
			continue
		if label in PRODUCT_APP_BY_TITLE:
			sidebar["app"] = PRODUCT_APP_BY_TITLE[label]
		elif module in PRODUCT_APP_BY_TITLE:
			sidebar["app"] = PRODUCT_APP_BY_TITLE[module]
		elif key in PRODUCT_MODULE_APP_KEYS:
			sidebar["app"] = PRODUCT_MODULE_APP_KEYS[key]

	filter_workspace_sidebar_items(workspace_sidebar_item, blocked_modules)

	filtered_icons = []
	for icon in bootinfo.get("desktop_icons", []):
		if icon.get("parent_icon") in {ERP_NEXT_NAME, DEEPLINKERP_ICON_NAME}:
			icon["parent_icon"] = ""
		elif icon.get("parent_icon") in {FRAPPE_FRAMEWORK_NAME, FRAMEWORK_ICON_NAME}:
			icon["parent_icon"] = DLP_FRAMEWORK_NAME
		elif icon.get("parent_icon") in PRODUCT_APP_BY_TITLE:
			icon["parent_icon"] = BRAND_NAME

		if icon.get("name") == ERP_NEXT_NAME or icon.get("label") == ERP_NEXT_NAME:
			icon["hidden"] = 1
		elif icon.get("name") == DEEPLINKERP_ICON_NAME or icon.get("label") == BRAND_NAME:
			icon["app"] = "deeplinkerp_branding"
			icon["hidden"] = 1
			icon["label"] = BRAND_NAME
			icon["logo_url"] = BRAND_LOGO_URL
			icon["link"] = "/desk/home"
			icon["link_type"] = "External"
		elif icon.get("name") == "ERPNext Settings" or icon.get("label") == "ERPNext Settings":
			icon["hidden"] = 1
		elif icon.get("name") == SETTINGS_DOCNAME or icon.get("label") == SETTINGS_LABEL:
			if not show_admin_only_icons:
				continue
			icon["app"] = "deeplinkerp_branding"
			icon["hidden"] = 0
			icon["label"] = SETTINGS_LABEL
			icon["link_to"] = SETTINGS_DOCNAME
			icon["link_type"] = "Workspace Sidebar"
		elif icon.get("name") == FRAMEWORK_ICON_NAME or icon.get("label") == FRAMEWORK_ICON_NAME:
			icon["hidden"] = 1
		elif icon.get("name") == DLP_FRAMEWORK_NAME or icon.get("label") == DLP_FRAMEWORK_NAME:
			if not show_admin_only_icons:
				continue
			icon["app"] = "deeplinkerp_branding"
			icon["hidden"] = 0
			icon["label"] = DLP_FRAMEWORK_NAME
			icon["logo_url"] = BRAND_LOGO_URL

		if icon.get("parent_icon") == DLP_FRAMEWORK_NAME and not show_admin_only_icons:
			continue

		if icon.get("link_type") == "Workspace Sidebar" and not is_workspace_sidebar_icon_visible(
			icon, workspace_sidebar_item, blocked_modules
		):
			continue

		filtered_icons.append(icon)

	visible_parent_labels = {
		icon.get("label")
		for icon in filtered_icons
		if icon.get("label") and not icon.get("parent_icon") and icon.get("hidden") != 1
	}
	bootinfo["desktop_icons"] = [
		icon
		for icon in filtered_icons
		if not icon.get("parent_icon") or icon.get("parent_icon") in visible_parent_labels
	]
	apply_interface_mode_bootinfo(bootinfo)


def append_unique(values, value):
	if value not in values:
		values.append(value)



def copy_erpnext_settings_sidebar_items():
	if not (
		frappe.db.exists("Workspace Sidebar", "ERPNext Settings")
		and frappe.db.exists("Workspace Sidebar", SETTINGS_DOCNAME)
	):
		return

	source = frappe.get_doc("Workspace Sidebar", "ERPNext Settings")
	target = frappe.get_doc("Workspace Sidebar", SETTINGS_DOCNAME)

	target.set("items", [])
	for source_item in source.items:
		target.append(
			"items",
			{field: source_item.get(field) for field in SIDEBAR_ITEM_FIELDS if source_item.get(field) is not None},
		)

	target.flags.ignore_permissions = True
	target.flags.ignore_links = True
	target.save(ignore_permissions=True)
