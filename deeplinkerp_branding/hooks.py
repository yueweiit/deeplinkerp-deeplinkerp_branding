app_name = "deeplinkerp_branding"
app_title = "Deeplinkerp Branding"
app_publisher = "yuewei"
app_description = "DeeplinkERP 品牌与全局业务定制"
app_email = "308642281@qq.com"
app_license = "mit"
required_apps = ["erpnext"]
app_logo_url = "/assets/deeplinkerp_branding/logo/deeplinkerp_logo_radius.png?v=0.0.6"
favicon = "/assets/deeplinkerp_branding/logo/tab_logo.svg?v=0.0.6"
splash_image = "/assets/deeplinkerp_branding/logo/deeplinkerp_logo_radius.png?v=0.0.6"

# Apps
# ------------------

add_to_apps_screen = [
	{
		"name": app_name,
		"logo": "/assets/deeplinkerp_branding/logo/deeplinkerp_logo_radius.png?v=0.0.6",
		"title": app_title,
		"route": "/app/deeplinkerp-settings",
	}
]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
app_include_css = [
	"/assets/deeplinkerp_branding/css/deeplinkerp_navigation.css?v=0.0.10",
	"/assets/deeplinkerp_branding/css/purchase_payments.css?v=0.0.10",
	"/assets/deeplinkerp_branding/css/purchase_order_list.css?v=0.0.27",
    "/assets/deeplinkerp_branding/css/operating_expenses.css?v=0.0.6",
	"/assets/deeplinkerp_branding/css/sales_order_list.css?v=0.0.12",
	"/assets/deeplinkerp_branding/css/inventory_detail.bundle.css?v=0.0.3",
]
app_include_js = [
	"/assets/deeplinkerp_branding/js/compact_list.js?v=0.0.31",
	"/assets/deeplinkerp_branding/js/purchase_payments.js?v=0.0.29",
	"/assets/deeplinkerp_branding/js/crossborder_procurement.js?v=0.0.5",
	"/assets/deeplinkerp_branding/js/purchase_source.js?v=0.0.3",
	"/assets/deeplinkerp_branding/js/oa_purchase_request.js?v=0.0.1",
    "/assets/deeplinkerp_branding/js/operating_payment_panel.js?v=0.0.8",
	"/assets/deeplinkerp_branding/js/operating_expense_drawer.js?v=0.0.17",
	"/assets/deeplinkerp_branding/js/operating_expenses.js?v=0.0.8",
	"/assets/deeplinkerp_branding/js/unified_purchase_list.js?v=0.0.13",
	"/assets/deeplinkerp_branding/js/deeplinkerp_navigation.js?v=0.0.20",
	"/assets/deeplinkerp_branding/js/deeplinkerp_interface_mode.js?v=0.0.3",
	"/assets/deeplinkerp_branding/js/deeplinkerp_branding.js?v=0.0.28",
	"/assets/deeplinkerp_branding/js/inventory_detail.bundle.js?v=0.0.4",
	"/assets/deeplinkerp_branding/js/selection_dropdown.bundle.js",
]

# include js, css files in header of web template
web_include_css = "/assets/deeplinkerp_branding/css/deeplinkerp_branding.css"
# web_include_js = "/assets/deeplinkerp_branding/js/deeplinkerp_branding.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "deeplinkerp_branding/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
doctype_list_js = {
	**{doctype: "public/js/compact_list.js" for doctype in (
		"Request for Quotation", "Supplier Quotation", "Quotation", "Purchase Invoice", "Payment Entry",
		"Sales Invoice", "Delivery Note", "POS Invoice", "POS Opening Entry", "POS Closing Entry", "OA Purchase Request",
	)},
	"Purchase Receipt": "public/js/purchase_receipt_list.js",
	"Sales Order": "public/js/sales_order_list.js",
	"Purchase Order": "public/js/purchase_order_list.js",
	"Material Request": "public/js/material_request_list.js",
}
doctype_js = {"Material Request": "public/js/material_request_form.js", **{doctype: "public/js/purchase_payment_form.js" for doctype in ("Purchase Order", "Purchase Receipt", "Purchase Invoice", "Payment Entry")}}
doctype_js["OA Purchase Request"] = "public/js/oa_purchase_request.js"
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "deeplinkerp_branding/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

boot_session = "deeplinkerp_branding.deeplinkerp_branding.branding.apply_boot_branding"

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "deeplinkerp_branding.utils.jinja_methods",
# 	"filters": "deeplinkerp_branding.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "deeplinkerp_branding.install.before_install"
after_install = ["deeplinkerp_branding.inventory_install.after_install", "deeplinkerp_branding.procurement_navigation.ensure_procurement_navigation", "deeplinkerp_branding.operating_navigation.ensure_operating_navigation"]

# Uninstallation
# ------------

# before_uninstall = "deeplinkerp_branding.uninstall.before_uninstall"
# after_uninstall = "deeplinkerp_branding.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "deeplinkerp_branding.utils.before_app_install"
# after_app_install = "deeplinkerp_branding.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "deeplinkerp_branding.utils.before_app_uninstall"
# after_app_uninstall = "deeplinkerp_branding.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "deeplinkerp_branding.build.after_build"
after_migrate = [
	"deeplinkerp_branding.deeplinkerp_branding.branding.apply_deeplinkerp_settings_branding",
	"deeplinkerp_branding.inventory_install.after_migrate",
	"deeplinkerp_branding.procurement_navigation.ensure_procurement_navigation",
	"deeplinkerp_branding.operating_expense_install.after_migrate",
	"deeplinkerp_branding.purchase_source_install.after_migrate",
	"deeplinkerp_branding.purchase_fulfilment_install.after_migrate",
	"deeplinkerp_branding.purchase_reversal_install.after_migrate",
	"deeplinkerp_branding.operating_navigation.ensure_operating_navigation",
]

permission_query_conditions = {
    dt: "deeplinkerp_branding.services.operating_expenses.permission_query_conditions"
    for dt in ("Operating Expense Source", "Operating Expense Mapping", "Operating Expense Event", "Operating Expense Takeover", "Operating Expense Payment")
}
has_permission = {
    dt: "deeplinkerp_branding.services.operating_expenses.has_permission"
    for dt in ("Operating Expense Source", "Operating Expense Mapping", "Operating Expense Event", "Operating Expense Takeover", "Operating Expense Payment")
}
permission_query_conditions["OA Purchase Request"] = "deeplinkerp_branding.services.purchase_source_service.permission_query_conditions"
has_permission["OA Purchase Request"] = "deeplinkerp_branding.services.purchase_source_service.has_permission"
permission_query_conditions["Purchase Fulfilment Link"] = "deeplinkerp_branding.services.purchase_fulfilment_service.permission_query_conditions"
has_permission["Purchase Fulfilment Link"] = "deeplinkerp_branding.services.purchase_fulfilment_service.has_permission"
doc_events = {
    "Journal Entry": {"validate": "deeplinkerp_branding.services.operating_expenses.validate_operating_journal"},
    "File": {"validate": "deeplinkerp_branding.services.operating_payment_service.validate_payment_file", "on_trash": "deeplinkerp_branding.services.operating_payment_service.validate_payment_file"},
}
doc_events["OA Purchase Request"] = {"validate":"deeplinkerp_branding.services.purchase_source_service.validate_managed_source","before_rename":"deeplinkerp_branding.services.purchase_source_service.protect_managed_source_identity","on_trash":"deeplinkerp_branding.services.purchase_source_service.protect_managed_source_identity"}
doc_events["Purchase Order"] = {"validate":"deeplinkerp_branding.services.purchase_source_service.validate_managed_order","before_submit":"deeplinkerp_branding.services.purchase_source_service.validate_source_before_submit"}
for procurement_doctype in ("Purchase Order", "Purchase Receipt", "Purchase Invoice", "Payment Entry"):
    events = doc_events.setdefault(procurement_doctype, {})
    for procurement_event in ("on_update", "on_submit", "on_cancel", "on_update_after_submit"):
        events[procurement_event] = "deeplinkerp_branding.services.purchase_consistency.register_document"
doc_events["Integration Request"] = {
    "on_trash": "deeplinkerp_branding.services.purchase_operation.protect_audit",
    "before_rename": "deeplinkerp_branding.services.purchase_operation.protect_audit",
}
doc_events["Repost Item Valuation"] = {"on_submit": "deeplinkerp_branding.services.purchase_consistency.check_native_repost",
    "on_trash": "deeplinkerp_branding.services.purchase_native_repost.protect_task",
    "before_rename": "deeplinkerp_branding.services.purchase_native_repost.protect_task"}
extend_doctype_class = {
    "File": ["deeplinkerp_branding.services.purchase_native_repost.CheckpointFileBoundary"],
    **{doctype: ["deeplinkerp_branding.services.purchase_consistency.ProcurementControllerBoundary"]
       for doctype in ("Purchase Order", "Purchase Receipt", "Purchase Invoice", "Payment Entry")},
    "Integration Request": ["deeplinkerp_branding.services.purchase_operation.ProcurementAuditRetention"],
    **{doctype: ["deeplinkerp_branding.services.purchase_repost_boundary.NativeRangeBoundary"]
       for doctype in ("Stock Entry", "Delivery Note", "Sales Invoice", "Stock Reconciliation", "Landed Cost Voucher", "Material Request")},
    **{doctype: ["deeplinkerp_branding.services.purchase_repost_boundary.PointerBoundary"]
       for doctype in ("Bin", "Repost Item Valuation")},
}
before_request = ["deeplinkerp_branding.services.purchase_repost_boundary.before_execution"]
for _status_doctype in ("Purchase Order", "Purchase Receipt", "Material Request"):
    extend_doctype_class[_status_doctype].append("deeplinkerp_branding.services.purchase_repost_boundary.NativeStatusBoundary")
before_job = ["deeplinkerp_branding.services.purchase_repost_boundary.before_execution"]
override_whitelisted_methods = {"oa_purchase_request.oa_purchase_request.oa_purchase_request.create_purchase_order":"deeplinkerp_branding.services.purchase_source_service.legacy_create_purchase_order"}
override_whitelisted_methods.update({"frappe.desk.form.save.savedocs": "deeplinkerp_branding.services.purchase_consistency.savedocs",
    "frappe.desk.form.save.cancel": "deeplinkerp_branding.services.purchase_consistency.cancel",
    "erpnext.buying.doctype.purchase_order.purchase_order.update_status": "deeplinkerp_branding.services.purchase_repost_boundary.update_purchase_order_status",
    "erpnext.buying.doctype.purchase_order.purchase_order.close_or_unclose_purchase_orders": "deeplinkerp_branding.services.purchase_repost_boundary.close_or_unclose_purchase_orders",
    "erpnext.controllers.accounts_controller.update_child_qty_rate": "deeplinkerp_branding.services.purchase_repost_boundary.update_child_qty_rate"})
scheduler_events = {"cron": {"*/15 * * * *": ["deeplinkerp_branding.services.operating_expenses.scheduled_sync", "deeplinkerp_branding.services.purchase_source_service.scheduled_sync", "deeplinkerp_branding.services.purchase_reversal_progress.recover"]}}
website_context = {
	"favicon": "/assets/deeplinkerp_branding/logo/tab_logo.svg?v=0.0.6",
	"splash_image": "/assets/deeplinkerp_branding/logo/deeplinkerp_logo_radius.png?v=0.0.6",
}

website_redirects = [
	{
		"source": "/favicon.ico",
		"target": "/assets/deeplinkerp_branding/logo/tab_logo.svg?v=0.0.6",
		"redirect_http_status": 302,
	},
	{
		"source": "/apple-touch-icon.png",
		"target": "/assets/deeplinkerp_branding/logo/deeplinkerp_logo_radius.png?v=0.0.6",
		"redirect_http_status": 302,
	},
	{
		"source": "/apple-touch-icon-precomposed.png",
		"target": "/assets/deeplinkerp_branding/logo/deeplinkerp_logo_radius.png?v=0.0.6",
		"redirect_http_status": 302,
	},
	{
		"source": "/site.webmanifest",
		"target": "/assets/deeplinkerp_branding/manifest.webmanifest",
		"redirect_http_status": 302,
	},
	{
		"source": "/manifest.json",
		"target": "/assets/deeplinkerp_branding/manifest.webmanifest",
		"redirect_http_status": 302,
	},
]

update_website_context = "deeplinkerp_branding.web_context.update_context"
# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "deeplinkerp_branding.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"deeplinkerp_branding.tasks.all"
# 	],
# 	"daily": [
# 		"deeplinkerp_branding.tasks.daily"
# 	],
# 	"hourly": [
# 		"deeplinkerp_branding.tasks.hourly"
# 	],
# 	"weekly": [
# 		"deeplinkerp_branding.tasks.weekly"
# 	],
# 	"monthly": [
# 		"deeplinkerp_branding.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "deeplinkerp_branding.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "deeplinkerp_branding.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "deeplinkerp_branding.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "deeplinkerp_branding.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["deeplinkerp_branding.utils.before_request"]
# after_request = ["deeplinkerp_branding.utils.after_request"]

# Job Events
# ----------
# before_job = ["deeplinkerp_branding.utils.before_job"]
# after_job = ["deeplinkerp_branding.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"deeplinkerp_branding.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []
