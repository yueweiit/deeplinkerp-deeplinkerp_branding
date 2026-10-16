"""Permission-checked sales workspace; reuse native reads and CRM display mapping."""
import json

import frappe
from frappe.model import get_permitted_fields
from frappe.utils import strip_html

from deeplinkerp_branding.services.purchase_payment_service import _RecordReader, _record_reader, _read, _require_fields

HEADER_FIELDS = {"company", "customer", "customer_name", "custom_crm_order_no", "custom_odt", "title", "contact_display", "contact_mobile", "contact_email", "transaction_date", "delivery_date", "currency", "grand_total", "party_account_currency", "advance_paid", "custom_process_status", "status", "custom_remark", "project", "owner", "creation", "modified", "modified_by"}
ITEM_FIELDS = {"idx", "item_code", "item_name", "custom_specifications", "custom_version", "qty", "uom", "rate", "custom_item_tax_amount", "amount", "delivery_date", "description", "custom_product", "warehouse"}


def _names(value):
	value = json.loads(value) if isinstance(value, str) else value
	if not isinstance(value, list) or len(value) > 2500 or any(not isinstance(name, str) or not name for name in value):
		frappe.throw(frappe._("最多读取2500张订单"))
	return list(dict.fromkeys(value))


def _child_fields():
	try:
		_require_fields("Sales Order", {"items"})
		return set(get_permitted_fields("Sales Order Item", parenttype="Sales Order", permission_type="read"))
	except frappe.PermissionError:
		return set()


@frappe.whitelist()
def get_sales_display_details(sales_orders):
	names = _names(sales_orders)
	reader = _RecordReader()
	token = _record_reader.set(reader)
	try:
		reader.preload("Sales Order", names)
		reader.preload("Company", {doc.company for doc in reader.docs.values() if doc and doc.get("company")})
		result, docs = {}, {}
		for name in names:
			try:
				docs[name] = _read("Sales Order", name, {"company"})
			except (frappe.PermissionError, frappe.DoesNotExistError):
				continue
			result[name] = {}
		fields = _child_fields()
		can_sales_person = False
		try:
			_require_fields("Sales Order", {"sales_team"})
			_require_fields("Sales Team", {"sales_person"}, "Sales Order")
			can_sales_person = True
		except frappe.PermissionError:
			pass
		try:
			from crm_integration.crm_integration.sales_order import set_first_product, set_first_sales_person
			details = {name: {"product": "", "sales_person": ""} for name in docs}
			if docs and "custom_product" in fields:
				set_first_product(details, list(docs))
			if docs and can_sales_person:
				set_first_sales_person(details, list(docs))
		except ModuleNotFoundError as error:
			if error.name and not error.name.startswith("crm_integration"):
				raise
			details = {}
		for name, doc in docs.items():
			row = result[name]
			if fields:
				row["dlp_item_count"] = len(doc.items)
				row["dlp_product"] = details.get(name, {}).get("product") or (doc.items[0].item_name if doc.items and "item_name" in fields else "")
				if "qty" in fields:
					row["dlp_quantity"] = sum(item.qty for item in doc.items)
				if {"qty", "uom"} <= fields:
					units = {item.uom for item in doc.items}
					row["dlp_uom"] = next(iter(units)) if len(units) == 1 else ""
					row["dlp_multiple_units"] = len(units) > 1
				if "rate" in fields:
					row["dlp_rate"] = doc.items[0].rate if len(doc.items) == 1 else None
			if can_sales_person:
				row["dlp_sales_person"] = details.get(name, {}).get("sales_person") or (doc.sales_team[0].sales_person if doc.sales_team else "")
		return result
	finally:
		_record_reader.reset(token)


@frappe.whitelist()
def get_sales_order_details(sales_order):
	doc = _read("Sales Order", sales_order, {"company"})
	fields = set(get_permitted_fields("Sales Order", permission_type="read"))
	header = {field: doc.get(field) for field in HEADER_FIELDS & fields}
	header["name"] = doc.name
	item_fields = _child_fields()
	items = []
	for item in doc.items if item_fields else []:
		row = {field: item.get(field) for field in ITEM_FIELDS & item_fields}
		if "description" in row:
			row["description"] = strip_html(row["description"] or "")
		items.append(row)
	attachments = []
	file_fields = set(get_permitted_fields("File", permission_type="read"))
	if {"file_name", "file_url"} <= file_fields and frappe.has_permission("File", "read"):
		attachments = frappe.get_list("File", filters={"attached_to_doctype": "Sales Order", "attached_to_name": doc.name},
			fields=["file_name", "file_url"], limit_page_length=100)
	return {"header": header, "items": items, "item_fields": sorted(item_fields & ITEM_FIELDS), "attachments": attachments,
		"can_edit": bool(frappe.has_permission("Sales Order", "write", doc=doc)),
		"can_create_payment_entry": bool(doc.docstatus == 1 and frappe.has_permission("Payment Entry", "create"))}


@frappe.whitelist()
def make_sales_order_payment_entry(sales_order):
	doc = _read("Sales Order", sales_order, {"company"})
	if doc.docstatus != 1 or not frappe.has_permission("Payment Entry", "create"):
		frappe.throw(frappe._("订单未提交或没有创建收款凭证的权限。"), frappe.PermissionError)
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	return get_payment_entry("Sales Order", doc.name)
