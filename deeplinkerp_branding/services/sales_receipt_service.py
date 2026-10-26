"""Order-scoped customer receipt drafts using native Payment Entry controllers."""

import json
from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.utils import flt, getdate

from . import purchase_operation, purchase_payment_attachments as files
from . import purchase_payment_service as service
from .purchase_document_actions import _editable_fields, _fields, _version
from .purchase_repost_boundary import procurement_entry

ORDER_FIELDS = {"company", "customer", "currency", "grand_total", "rounded_total", "base_grand_total", "base_rounded_total", "advance_paid", "status"}
PAYMENT_FIELDS = {
	"company", "party", "party_type", "payment_type", "posting_date", "paid_from", "paid_to",
	"paid_from_account_currency", "paid_to_account_currency", "paid_amount", "received_amount",
	"references", "reference_no", "reference_date", "remarks", "mode_of_payment", "deductions",
	"unallocated_amount", "difference_amount", "custom_remarks",
	"taxes", "sales_taxes_and_charges_template", "purchase_taxes_and_charges_template",
	"apply_tds", "tax_withholding_category", "tax_withholding_group", "tax_withholding_entries",
	"override_tax_withholding_entries", "is_opening",
}
REFERENCE_FIELDS = {"reference_doctype", "reference_name", "allocated_amount", "payment_term"}
INPUT_FIELDS = {"amount", "posting_date", "bank_account", "mode_of_payment", "reference_no", "reference_date", "remarks"}
WRITE_FIELDS = {
	"paid_amount", "received_amount", "references", "posting_date", "paid_to", "mode_of_payment",
	"reference_no", "reference_date", "remarks", "custom_remarks",
}
ACCOUNT_FIELDS = {"paid_to", "paid_to_account_currency", "paid_to_account_type", "bank_account", "bank", "bank_account_no"}
REQUIRED_FIELDS = {"paid_amount", "received_amount", "references", "posting_date", "paid_to"}
WRITE_FIELDS |= ACCOUNT_FIELDS
ADVANCED = "此收款涉及本位币换算、税费、工作流或复杂核销，请在完整付款单处理。"


def _order(name, *, create=False, for_update=False):
	doc = service._current("Sales Order", name) if for_update else service._read("Sales Order", name, ORDER_FIELDS)
	service._require_fields("Sales Order", ORDER_FIELDS)
	service._read("Customer", doc.customer)
	if doc.docstatus != 1 or doc.status in ("Closed", "Cancelled", "Stopped", "On Hold"):
		frappe.throw(_("订单未提交、已关闭或暂停，不能录入收款。"))
	if create and not frappe.has_permission("Payment Entry", "create"):
		frappe.throw(_("没有创建收款草稿的权限。"), frappe.PermissionError)
	return doc


def _payment(name, order, *, for_update=False):
	doc = service._current("Payment Entry", name) if for_update else service._read("Payment Entry", name, PAYMENT_FIELDS)
	service._require_fields("Payment Entry", PAYMENT_FIELDS)
	service._require_fields("Payment Entry Reference", REFERENCE_FIELDS, "Payment Entry")
	if (doc.company != order.company or doc.party_type != "Customer" or doc.payment_type != "Receive"
		or doc.party != order.customer or not any(
			row.reference_doctype == "Sales Order" and row.reference_name == order.name for row in doc.references
		)):
		frappe.throw(_("收款草稿与当前订单、客户或公司不一致。"), frappe.PermissionError)
	return doc


def _drafts(order, *, for_update=False):
	if not frappe.has_permission("Payment Entry", "read"):
		return []
	query = frappe.get_list("Payment Entry", filters=[
		["Payment Entry", "docstatus", "=", 0], ["Payment Entry", "company", "=", order.company],
		["Payment Entry", "party_type", "=", "Customer"], ["Payment Entry", "payment_type", "=", "Receive"],
		["Payment Entry", "party", "=", order.customer],
		["Payment Entry Reference", "reference_doctype", "=", "Sales Order"],
		["Payment Entry Reference", "reference_name", "=", order.name],
	], fields=["name"], distinct=True, order_by="modified desc, name asc", limit_page_length=50, run=False)
	rows = query.for_update().run(as_dict=True) if for_update else query.run(as_dict=True)
	result = []
	for row in rows:
		doc = _payment(row.name, order, for_update=for_update)
		result.append({"name": doc.name, "posting_date": doc.posting_date, "amount": doc.received_amount,
			"currency": doc.paid_to_account_currency, "can_edit": bool(doc.has_permission("write")),
			"can_submit": _can_submit(doc, order)})
	return result


def _can_submit(doc, order):
	return bool(not doc.is_new() and doc.docstatus == 0 and doc.has_permission("write")
		and doc.has_permission("submit") and not _simple_reason(doc, order))


@frappe.whitelist()
def get_receipt_actions(sales_order):
	# Capability reads should not interrupt viewing a closed or restricted order.
	with service._quiet_link_errors():
		try:
			return {row["name"]: {"can_submit": row["can_submit"]} for row in _drafts(_order(sales_order))}
		except (frappe.PermissionError, frappe.ValidationError, frappe.DoesNotExistError):
			return {}


def _simple_reason(doc, order):
	from frappe.model.workflow import get_workflow_name

	if flt(order.grand_total) <= 0:
		return _("订单金额为 0，请先核实价格；未分配预收款请在完整付款单处理。")
	service._require_fields("Company", {"default_currency"})
	if (get_workflow_name("Payment Entry") or doc.get("deductions") or order.currency != doc.company_currency
		or any(doc.get(key) for key in (
			"taxes", "sales_taxes_and_charges_template", "purchase_taxes_and_charges_template",
			"apply_tds", "tax_withholding_category", "tax_withholding_group", "tax_withholding_entries",
			"override_tax_withholding_entries"))
		or doc.paid_from_account_currency != order.currency or doc.paid_to_account_currency != order.currency
		or len(doc.references) != 1 or doc.references[0].reference_doctype != "Sales Order"
		or doc.references[0].reference_name != order.name or doc.references[0].get("payment_term")
		or doc.get("unallocated_amount") or doc.get("difference_amount") or doc.get("is_opening") == "Yes"):
		return _(ADVANCED)
	return ""


def _editable(doc):
	fields = set(_editable_fields(doc, WRITE_FIELDS))
	result = [key for key in ("posting_date", "reference_no", "reference_date", "mode_of_payment") if key in fields]
	if {"remarks", "custom_remarks"} <= fields:
		result.append("remarks")
	if ACCOUNT_FIELDS <= fields:
		result.append("bank_account")
	if ({"paid_amount", "received_amount", "references"} <= fields
		and "allocated_amount" in _editable_fields(doc.references[0], {"allocated_amount"}, "Payment Entry")):
		result.append("amount")
	return result


def _projection(doc, order):
	service._require_fields("Payment Entry", PAYMENT_FIELDS)
	service._require_fields("Payment Entry Reference", REFERENCE_FIELDS, "Payment Entry")
	reason = _simple_reason(doc, order)
	can_write = doc.has_permission("create" if doc.is_new() else "write")
	editable = _editable(doc) if not reason and doc.docstatus == 0 and can_write else []
	return {
		"document": {"doctype": "Payment Entry", "name": None if doc.is_new() else doc.name,
			"docstatus": doc.docstatus, "modified": str(doc.modified or ""), "company": doc.company,
			"customer": doc.party, "currency": order.currency, "amount": doc.received_amount,
			"posting_date": doc.posting_date, "bank_account": doc.paid_to, "mode_of_payment": doc.mode_of_payment,
			"reference_no": doc.reference_no, "reference_date": doc.reference_date,
			"remarks": doc.remarks if doc.custom_remarks else ""},
		"order_modified": str(order.modified), "editable_fields": editable, "advanced_reason": reason,
		"can_submit": bool(not doc.is_new() and doc.docstatus == 0 and can_write and not reason
			and doc.has_permission("submit")),
		"attachments": files.list_attachments(doc.name) if not doc.is_new() else [],
	}


def _new_entry(order, bank_account=None):
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	return get_payment_entry("Sales Order", order.name, bank_account=bank_account)


@frappe.whitelist()
def get_receipt_context(sales_order, payment_entry=None, new_receipt=0):
	order = _order(sales_order)
	drafts = _drafts(order)
	if payment_entry:
		doc = _payment(payment_entry, order)
	elif drafts and new_receipt not in (True, 1, "1"):
		if len(drafts) != 1:
			return {"drafts": drafts, "document": None}
		doc = _payment(drafts[0]["name"], order)
	else:
		_order(sales_order, create=True)
		doc = _new_entry(order)
	result = _projection(doc, order)
	result["drafts"] = drafts
	return result


def _values(value):
	try:
		value = json.loads(value) if isinstance(value, str) else value
	except ValueError:
		frappe.throw(_("收款字段无效，请刷新后重试。"))
	if not isinstance(value, dict) or set(value) - INPUT_FIELDS:
		frappe.throw(_("收款字段无效，请刷新后重试。"))
	try:
		amount = Decimal(str(value.get("amount")))
	except (InvalidOperation, ValueError):
		frappe.throw(_("收款金额必须是大于 0 的有效数字。"))
	if not amount.is_finite() or amount <= 0:
		frappe.throw(_("收款金额必须是大于 0 的有效数字。"))
	if not value.get("posting_date") or not value.get("bank_account"):
		frappe.throw(_("请填写收款日期和收款账户。"))
	if any(key != "amount" and not isinstance(item, (str, type(None))) for key, item in value.items()):
		frappe.throw(_("收款字段无效，请刷新后重试。"))
	result = {key: value.get(key) or "" for key in INPUT_FIELDS}
	result.update(amount=str(amount), posting_date=str(getdate(value["posting_date"])),
		reference_date=str(getdate(value["reference_date"])) if value.get("reference_date") else "")
	return result


def _allocation_amount(doc, order, value):
	from erpnext.accounts.doctype.payment_entry.payment_entry import set_grand_total_and_outstanding_amount

	precision = doc.precision("received_amount")
	amount = flt(value, precision)
	# Match the mapper and native allocation validation, including order rounding.
	_total, outstanding = set_grand_total_and_outstanding_amount(None, "Sales Order", doc.paid_from_account_currency, order)
	if amount <= 0 or amount > flt(outstanding, precision):
		frappe.throw(_("收款金额超过订单当前可分配余额，或按币种精度取整后为 0。"))
	return amount


def _apply(doc, order, values):
	if doc.docstatus != 0:
		frappe.throw(_("收款单已提交或取消，请刷新后核对。"))
	if reason := _simple_reason(doc, order):
		frappe.throw(reason)
	_fields("Payment Entry", REQUIRED_FIELDS, permission="write")
	_fields("Payment Entry Reference", {"allocated_amount"}, permission="write", parenttype="Payment Entry")
	amount = _allocation_amount(doc, order, values["amount"])
	account = service._bank_account(values["bank_account"], order.company, order.currency, for_update=True)
	for key in ("reference_no", "reference_date", "remarks", "mode_of_payment"):
		current = (doc.remarks if doc.custom_remarks else "") if key == "remarks" else doc.get(key)
		if str(current or "") == values[key]:
			continue  # Keep read-only optional fields and generated remarks unchanged.
		_fields("Payment Entry", {key, "custom_remarks"} if key == "remarks" else {key}, permission="write")
		if key == "mode_of_payment" and values[key]:
			service._read("Mode of Payment", values[key])
		doc.update({key: values[key] or None})
		if key == "remarks":
			doc.custom_remarks = int(bool(values[key]))
	if doc.is_new() or doc.paid_to != account.name:
		_fields("Payment Entry", ACCOUNT_FIELDS, permission="write")
		# A different ledger account must never retain another bank's identity.
		# Bank Account selection stays in the native financial form.
		doc.update({"paid_to": account.name, "paid_to_account_currency": account.account_currency,
			"paid_to_account_type": account.account_type, "bank_account": None, "bank": None, "bank_account_no": None})
	doc.posting_date = values["posting_date"]
	doc.paid_amount = doc.received_amount = amount
	doc.references[0].allocated_amount = amount
	doc.set_exchange_rate()
	doc.set_amounts()


@frappe.whitelist(methods=["POST"])
@procurement_entry
def save_receipt_draft(sales_order, values, request_id, payment_entry=None, expected_modified=None,
	order_modified=None, new_receipt=0, attachment_session=None, attachments=None):
	try:
		values = _values(values)
		names = files._ids(attachments)
	except frappe.ValidationError as error:
		# These checks cannot have written a business document. Give the client
		# a definitive failure, while transport failures retain the retry token.
		return {"failed": True, "error": str(error)}
	new_receipt = new_receipt in (True, 1, "1")
	payload = {"action": "sales_order_receipt_draft", "sales_order": sales_order, "values": values,
		"payment_entry": payment_entry, "expected_modified": expected_modified, "order_modified": order_modified,
		"new_receipt": new_receipt, "attachment_session": attachment_session, "attachments": names}

	def operation():
		order = _order(sales_order, create=not payment_entry, for_update=True)
		_version(order, order_modified)
		if payment_entry:
			doc = _payment(payment_entry, order, for_update=True)
			doc.check_permission("write")
			_version(doc, expected_modified)
		else:
			drafts = _drafts(order, for_update=True)
			if drafts and not new_receipt:
				if len(drafts) != 1:
					frappe.throw(_("已有多张收款草稿，请先选择要继续编辑的草稿。"))
				result = _projection(_payment(drafts[0]["name"], order, for_update=True), order)
				result["needs_review"] = True
				return result
			doc = _new_entry(order, values["bank_account"])
		_apply(doc, order, values)
		if doc.is_new():
			doc.insert()
		else:
			doc.save()
		files.bind(doc, attachment_session, names)
		return _projection(doc, order)

	def replay(previous):
		try:
			order = _order(sales_order)
			doc = _payment(previous["name"], order)
			doc.check_permission("read" if previous.get("needs_review") else "write")
			result = _projection(doc, order)
		except (frappe.PermissionError, frappe.ValidationError, frappe.DoesNotExistError):
			return {"failed": True, "requires_review": True,
				"error": _("原请求已有保存记录，但当前无法读取草稿。请联系财务核对，勿重复新增。")}
		result.update(reused=True, needs_review=bool(previous.get("needs_review")))
		return result

	return purchase_operation.run(request_id, payload, operation, replay, acknowledge_validation=True)


@frappe.whitelist(methods=["POST"])
@procurement_entry
def submit_receipt(sales_order, payment_entry, expected_modified, order_modified, request_id):
	"""Submit only an existing, reviewed draft through native financial controllers."""
	payload = {"action": "submit_sales_order_receipt", "sales_order": sales_order,
		"payment_entry": payment_entry, "expected_modified": expected_modified, "order_modified": order_modified}

	def operation():
		order = _order(sales_order, for_update=True)
		_version(order, order_modified)
		doc = _payment(payment_entry, order, for_update=True)
		doc.check_permission("write")
		doc.check_permission("submit")
		_version(doc, expected_modified)
		if doc.docstatus != 0:
			frappe.throw(_("收款单已提交或取消，请刷新后核对。"))
		if reason := _simple_reason(doc, order):
			frappe.throw(reason)
		# advance_paid may change without updating the order's modified timestamp.
		# Check the current locked balance even when both UI versions still match.
		_allocation_amount(doc, order, doc.references[0].allocated_amount)
		service._bank_account(doc.paid_to, order.company, order.currency, for_update=True)
		doc.submit()
		return _projection(doc, _order(sales_order, for_update=True))

	def replay(previous):
		try:
			order = _order(sales_order)
			doc = _payment(previous["name"], order)
			doc.check_permission("submit")
			if doc.docstatus != 1:
				raise frappe.ValidationError
			result = _projection(doc, order)
		except (frappe.PermissionError, frappe.ValidationError, frappe.DoesNotExistError):
			return {"failed": True, "requires_review": True,
				"error": _("原请求已有处理记录，但当前单据状态或权限已变化。请联系财务核对，勿重复提交。")}
		result["reused"] = True
		return result

	return purchase_operation.run(request_id, payload, operation, replay, acknowledge_validation=True)
