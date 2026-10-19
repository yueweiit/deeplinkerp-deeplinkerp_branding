"""Procurement invariants after all native controller and finance hooks.

Native controllers own quantities, tax, stock valuation, GL and payment ledgers.
These checks compare their persisted results with native plans; they never post
replacement ledgers or manufacture documents in other business modules.
"""

from __future__ import annotations

from frappe import _ as _i18n


import hashlib
import json
import uuid
from contextvars import ContextVar
from copy import deepcopy
from decimal import Decimal

import frappe
from frappe.utils import flt, get_timedelta, getdate

from . import purchase_operation as operation
from . import purchase_payment_service as service

PI_HOLD_FIELDS = frozenset({"on_hold", "hold_comment", "release_date"})
_native_hold = ContextVar("purchase_invoice_native_hold", default=None)


def is_procurement(doc):
	if doc.doctype in ("Purchase Order", "Purchase Receipt"):
		return True
	if doc.doctype == "Purchase Invoice":
		return any(row.get("purchase_order") or row.get("purchase_receipt") for row in doc.get("items") or [])
	if doc.doctype != "Payment Entry" or doc.get("party_type") != "Supplier":
		return False
	for row in doc.get("references") or []:
		if row.reference_doctype == "Purchase Order":
			return True
		if row.reference_doctype == "Purchase Invoice" and frappe.db.exists(
			"Purchase Invoice Item", {"parent": row.reference_name, "purchase_order": ["is", "set"]}
		):
			return True
		if row.reference_doctype == "Purchase Invoice" and frappe.db.exists(
			"Purchase Invoice Item", {"parent": row.reference_name, "purchase_receipt": ["is", "set"]}
		):
			return True
	return False


def equal(doc, field, actual, expected, label):
	precision = doc.precision(field)
	if precision is None:
		operation.reject("原生业务精度不可用，不能确认采购操作", "native_precision_missing")
	if flt(actual, precision) != flt(expected, precision):
		operation.reject("采购一致性核对失败：" + label, "native_invariant_mismatch", invariant=label)


def snapshot(doc):
	return {
		"doctype": doc.doctype,
		"name": doc.name,
		"docstatus": doc.docstatus,
		"status": doc.get("status"),
		"modified": str(doc.get("modified") or ""),
		"amount": doc.get("paid_amount") if doc.doctype == "Payment Entry" else doc.get("grand_total"),
		"currency": doc.get("paid_from_account_currency")
		if doc.doctype == "Payment Entry"
		else doc.get("currency"),
		**(
			{"on_hold": doc.get("on_hold"), "release_date": str(doc.get("release_date") or "")}
			if doc.doctype == "Purchase Invoice"
			else {}
		),
		"items": [
			{
				"key": row.get("name"),
				"qty": row.get("qty"),
				"rate": row.get("rate"),
				"warehouse": row.get("warehouse"),
			}
			for row in doc.get("items") or []
		],
	}


def business_payload(value):
	"""Bind all supplied business fields; omit only Desk display bookkeeping.

	Local names, __islocal, modified, docstatus, flags and every child/account/
	reference/tax field remain bound. No anonymous-document content deduplication.
	"""
	if isinstance(value, dict):
		return {
			key: business_payload(item)
			for key, item in value.items()
			if key not in {"__onload", "__last_sync_on", "__unsaved"}
		}
	if isinstance(value, (list, tuple)):
		return [business_payload(item) for item in value]
	return value


def artifact_evidence(doc, *, ignored_fields=(), include_against_voucher=False):
	facts = {"document": business_payload(doc.as_dict())}
	for field in ignored_fields:
		facts["document"].pop(field, None)
	facts["ledgers"] = {
		doctype: sorted(_ledger(doc, doctype), key=lambda row: row.name)
		for doctype in (
			"GL Entry",
			"Stock Ledger Entry",
			"Payment Ledger Entry",
			"Advance Payment Ledger Entry",
		)
		if doc.doctype in ("Purchase Order", "Purchase Receipt", "Purchase Invoice", "Payment Entry")
	}
	if include_against_voucher and doc.doctype == "Purchase Invoice":
		for doctype in ("Payment Ledger Entry", "Advance Payment Ledger Entry"):
			# A PE owns these rows, but its allocation changes this PI's AP.
			# No PE actor permission or recalculation is part of a PI hold.
			rows = {row.name: row for row in facts["ledgers"][doctype]}
			rows.update(
				{
					row.name: row
					for row in frappe.db.get_values(
						doctype,
						{"against_voucher_type": doc.doctype, "against_voucher_no": doc.name},
						"*",
						as_dict=True,
						for_update=True,
					)
				}
			)
			facts["ledgers"][doctype] = sorted(rows.values(), key=lambda row: row.name)
	bin_keys = {(row.item_code, row.warehouse) for row in facts["ledgers"].get("Stock Ledger Entry", [])}
	if doc.doctype == "Purchase Order":
		# PO's native requested/ordered updater changes Bin without any SLE.
		bin_keys.update(
			(row.item_code, row.warehouse)
			for row in doc.items
			if row.get("warehouse")
			and not row.get("delivered_by_supplier")
			and frappe.get_cached_value("Item", row.item_code, "is_stock_item")
		)
		for row in doc.items:
			if row.get("material_request_item"):
				source = frappe.db.get_value(
					"Material Request Item",
					row.material_request_item,
					["item_code", "warehouse"],
					as_dict=True,
				)
				if (
					source
					and source.warehouse
					and frappe.get_cached_value("Item", source.item_code, "is_stock_item")
				):
					bin_keys.add((source.item_code, source.warehouse))
		if doc.get("is_old_subcontracting_flow"):
			bin_keys.update(
				(row.rm_item_code, row.reserve_warehouse)
				for row in doc.get("supplied_items") or []
				if row.get("rm_item_code") and row.get("reserve_warehouse")
			)
	if bin_keys:
		facts["bins"] = [
			frappe.db.get_values(
				"Bin", {"item_code": item, "warehouse": warehouse}, "*", as_dict=True, for_update=True
			)
			for item, warehouse in sorted(bin_keys)
		]
	return hashlib.sha256(operation.encode(facts).encode()).hexdigest()


def hold_evidence(doc):
	"""Only persisted facts: native hold must not alter money, sources or finance."""
	identities, _ = resolve_source_documents(doc, reader=service._current)
	identities.add((doc.doctype, doc.name))
	documents = {identity: service._current(*identity) for identity in sorted(identities)}
	for doctype in ("China Accounting Voucher", "China Voucher Sync Issue", "China Cash Flow Assignment"):
		if not frappe.db.exists("DocType", doctype):
			continue
		for source_type, name in sorted(identities):
			names = frappe.db.get_values(
				doctype, {"source_doctype": source_type, "source_name": name}, "name", for_update=True
			)
			for (name,) in names:
				documents[doctype, name] = frappe.get_doc(doctype, name, for_update=True)
	return {
		identity: artifact_evidence(
			native,
			ignored_fields=PI_HOLD_FIELDS | {"modified", "modified_by"}
			if identity == (doc.doctype, doc.name)
			else (),
			include_against_voucher=identity == (doc.doctype, doc.name),
		)
		for identity, native in documents.items()
	}


def check_hold(doc, before):
	evidence = hold_evidence(doc)
	if evidence != before:
		operation.reject(
			"原生冻结操作改变了金额、来源或财务事实，操作已回滚", "invoice_hold_business_facts_changed"
		)
	for identity in sorted(evidence):
		if identity != (doc.doctype, doc.name):
			acknowledge_effect(
				frappe.get_doc(*identity, for_update=True), system_effect=identity[0].startswith("China ")
			)
	return [{"module": "native invoice hold / unchanged procurement and finance", "result": "verified"}]


def acknowledge_effect(doc, *, system_effect=False):
	state = _state()
	context = operation.current() or (state["context"] if state else None)
	if context is None:
		return
	identity = {"doctype": doc.doctype, "name": doc.name}
	if system_effect:
		identity["system_effect"] = True
	else:
		identity["permission"] = "read"  # native updater effect, not a direct actor submit
	if not any((row["doctype"], row["name"]) == (doc.doctype, doc.name) for row in context["documents"]):
		context["documents"].append(identity)
	context["after"][doc.doctype + ":" + doc.name] = snapshot(doc)


def _form_replay(previous):
	operation.replay_artifacts(previous)
	doc = frappe.get_doc(previous["doctype"], previous["name"], for_update=True)
	doc.check_permission("read")
	doc.check_permission(previous["permission"])
	if previous.get("localname"):
		doc.localname = previous["localname"]
	from frappe.desk.form.save import send_updated_docs

	send_updated_docs(doc)
	return {"document": snapshot(doc)}


def _form_operation(request_id, payload, native):
	def write():
		old_request = frappe.flags.get("purchase_request_id")
		frappe.flags.purchase_request_id = request_id
		try:
			before = len(frappe.response.get("docs") or [])
			from .purchase_reversal_progress import acceptance

			with acceptance(payload):
				native()
			if len(frappe.response.get("docs") or []) <= before:
				operation.reject(
					"采购原生操作尚无同步确认结果，不能确认完成", "native_synchronous_result_missing"
				)
			doc = frappe.response.docs[-1]
			return {
				"document": snapshot(frappe.get_doc(doc["doctype"], doc["name"])),
				"localname": doc.get("localname"),
			}
		finally:
			frappe.flags.purchase_request_id = old_request

	result = operation.run(request_id, business_payload(payload), write, _form_replay)
	if result.get("reversal"):
		frappe.response["purchase_reversal"] = result["reversal"]
	return result


def _form_is_procurement(payload, *, doctype=None, name=None):
	"""Classify only; native actions still own unrelated document permissions."""
	doctype = doctype or (payload or {}).get("doctype")
	if doctype in ("Purchase Order", "Purchase Receipt"):
		return True
	if doctype not in ("Purchase Invoice", "Payment Entry"):
		return False
	if payload and is_procurement(frappe.get_doc(payload)):
		return True
	name = name or (payload or {}).get("name")
	# Neither cleared links nor __islocal can erase a persisted purchase identity.
	# This read is not source authorization and returns no content to the actor;
	# procurement controllers retain their existing source locks and ACL checks.
	return bool(name and frappe.db.exists(doctype, name) and is_procurement(frappe.get_doc(doctype, name)))


@frappe.whitelist(methods=["POST", "PUT"])
def savedocs(doc, action, request_id=None):
	from frappe.desk.form.save import savedocs as native_savedocs

	payload = json.loads(doc) if isinstance(doc, str) else doc
	if not _form_is_procurement(payload):
		return native_savedocs(doc, action)

	def native():
		from frappe.utils.scheduler import is_scheduler_inactive

		if (
			action == "Submit"
			and frappe.get_meta(payload["doctype"]).queue_in_background
			and not is_scheduler_inactive()
		):
			operation.reject(
				"采购提交需同步完成，原生后台提交暂不支持", "native_background_submission_unsupported"
			)
		return native_savedocs(doc, action)

	if not request_id:
		return native()
	_form_operation(request_id, {"doc": payload, "action": action}, native)


@frappe.whitelist(methods=["POST", "PUT"])
def cancel(
	doctype=None, name=None, workflow_state_fieldname=None, workflow_state=None, request_id=None, doc=None
):
	from frappe.desk.form.save import cancel as native_cancel

	def native():
		return native_cancel(doctype, name, workflow_state_fieldname, workflow_state)

	if (
		doctype not in ("Purchase Order", "Purchase Receipt", "Purchase Invoice", "Payment Entry")
		or not request_id
	):
		return native()
	payload = json.loads(doc) if isinstance(doc, str) else doc
	if not _form_is_procurement(payload, doctype=doctype, name=name):
		return native()
	if not payload or (payload.get("doctype"), payload.get("name")) != (doctype, name):
		operation.reject("原生采购取消缺少完整单据快照，请刷新", "native_cancel_payload_missing")
	_form_operation(
		request_id,
		{
			"doc": payload,
			"action": "Cancel",
			"workflow_state_fieldname": workflow_state_fieldname,
			"workflow_state": workflow_state,
		},
		native,
	)


def _state():
	return getattr(frappe.local, "purchase_consistency", None)


def _context():
	return operation.current() or {
		"operation_id": operation.identity(frappe.session.user, str(uuid.uuid4())),
		"user": frappe.session.user,
		"request_id": "native-form",
		"before": {},
		"after": {},
		"documents": [],
		"amount": None,
		"currency": None,
		"quantity": [],
		"modules": [],
	}


def register_document(doc, method=None):
	old = doc.get_doc_before_save()
	if not is_procurement(doc) and not (old and is_procurement(old)):
		return
	state = _state()
	if state is None:
		state = {
			"documents": {},
			"before_documents": {},
			"context": _context(),
			"explicit": operation.current() is not None,
		}
		frappe.local.purchase_consistency = state
		frappe.db.before_commit.add(lambda: _before_commit(state))
		frappe.db.after_rollback.add(lambda: setattr(frappe.local, "purchase_consistency", None))
	key = (doc.doctype, doc.name)
	state.get("hold_checks", {}).pop(key, None)  # a later real save requires the full business postcheck
	state["documents"][key] = doc
	context = state["context"]
	state["before_documents"].setdefault(key, old)
	context["before"].setdefault(doc.doctype + ":" + doc.name, snapshot(old) if old else {"exists": False})
	context["after"][doc.doctype + ":" + doc.name] = snapshot(doc)
	recorded = next(
		(row for row in context["documents"] if (row["doctype"], row["name"]) == (doc.doctype, doc.name)),
		None,
	)
	if recorded:
		recorded.pop("permission", None)  # this document actually passed its native controller
		recorded.pop("include_against_voucher", None)
	else:
		context["documents"].append({"doctype": doc.doctype, "name": doc.name})
	facts = snapshot(doc)
	context.update(
		amount=facts["amount"],
		currency=facts["currency"],
		quantity=[row.get("qty") for row in doc.get("items") or []],
	)


def bind_operation(context):
	state = _state()
	if state:
		for key in ("before", "after"):
			context[key].update(state["context"].get(key, {}))
		context.setdefault("gl_before", {}).update(state["context"].get("gl_before", {}))
		context.setdefault("payment_before", {}).update(state["context"].get("payment_before", {}))
		context.setdefault("billing_preserved", {}).update(state["context"].get("billing_preserved", {}))
		context["documents"] = list(state["context"].get("documents", []))
		state.update(context=context, explicit=True)


def _before_commit(state):
	try:
		check_registered(state)
		finish_native_audit(state)
	except Exception as error:
		operation.rollback_failure(state["context"], "rolled_back_before_commit", error)
		raise
	finally:
		frappe.local.purchase_consistency = None


def finish_native_audit(state):
	if state["explicit"]:
		frappe.db.set_value(
			"Integration Request",
			state["context"]["operation_id"],
			{"data": operation.encode(state["context"])},
			update_modified=False,
		)
		return
	context = state["context"]
	operation._reserve(context)
	doc = next(reversed(state["documents"].values()))
	operation.complete_audit(context, {"document": snapshot(doc)})
	operation.runtime_log(context, "success_pending_commit")


def check_registered(state=None):
	state = state or _state()
	if not state:
		return
	modules = []
	for key, doc in state["documents"].items():
		# Re-read after the complete hook chain, including auto PI creation.
		old = state["before_documents"][key]
		doc = frappe.get_doc(*key, for_update=True)
		doc._doc_before_save = old
		hold_before = state.get("hold_checks", {}).get(key)
		modules.extend(
			_stage(doc, "native invoice hold / unchanged facts", lambda: check_hold(doc, hold_before))
			if hold_before is not None
			else check_document(doc)
		)
		state["context"]["after"][doc.doctype + ":" + doc.name] = snapshot(doc)
	state["context"]["modules"].extend(modules)


def _stage(doc, name, action):
	state = _state()
	context = operation.current() or (state["context"] if state else _context())
	facts = snapshot(doc)
	context.update(
		amount=facts["amount"], currency=facts["currency"], quantity=[row["qty"] for row in facts["items"]]
	)
	return operation.trace(
		context,
		name,
		action,
		source={"doctype": doc.doctype, "name": doc.name},
		target={"doctype": doc.doctype, "name": doc.name},
	)


def check_draft(doc):
	for ledger, filters in (
		("Stock Ledger Entry", {"voucher_type": doc.doctype, "voucher_no": doc.name}),
		("GL Entry", {"voucher_type": doc.doctype, "voucher_no": doc.name}),
		("Payment Ledger Entry", {"voucher_type": doc.doctype, "voucher_no": doc.name}),
		("Advance Payment Ledger Entry", {"voucher_type": doc.doctype, "voucher_no": doc.name}),
	):
		if frappe.db.exists(ledger, filters):
			operation.reject("采购草稿不能形成库存或财务流水", "draft_has_ledger")
	if doc.doctype == "Purchase Order":
		for row in doc.items:
			equal(row, "received_qty", row.get("received_qty"), 0, "草稿订单实际收货数量")
		equal(doc, "advance_paid", doc.get("advance_paid"), 0, "草稿订单实际付款")


def finance_service(doc):
	if not frappe.db.exists("DocType", "China Finance Settings"):
		return None
	from china_finance.services import voucher

	settings = voucher.get_company_settings(doc.company)
	if not settings or getdate(voucher.get_posting_date(doc)) < getdate(settings.activation_date):
		return None
	return voucher


def _ledger(doc, doctype, **filters):
	return frappe.db.get_values(
		doctype,
		{"voucher_type": doc.doctype, "voucher_no": doc.name, **filters},
		"*",
		as_dict=True,
		for_update=True,
	)


def _balanced(doc, rows, field="base_grand_total"):
	equal(
		doc,
		field,
		sum((service.amount(row.get("debit")) for row in rows), Decimal(0)),
		sum((service.amount(row.get("credit")) for row in rows), Decimal(0)),
		"原生总账借贷",
	)


def _gl_map(rows):
	from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import get_accounting_dimensions
	from erpnext.accounts.general_ledger import toggle_debit_credit_if_negative

	dimension_fields = get_accounting_dimensions()
	result = {}
	for row in rows:
		# Native posting moves negative debit/credit to its opposite column.
		# Do not net persisted positive columns: equal additions are corruption.
		row = frappe._dict(
			deepcopy(row.as_dict() if callable(getattr(row, "as_dict", None)) else dict(row)),
			post_net_value=False,
		)
		toggle_debit_credit_if_negative([row])
		dimensions = {field: row.get(field) for field in dimension_fields if row.get(field) not in (None, "")}
		if row.get("dimensions_json"):
			try:
				stored_dimensions = json.loads(row.dimensions_json)
				if not isinstance(stored_dimensions, dict):
					raise ValueError
			except (ValueError, TypeError):
				operation.reject("中国会计凭证维度格式无效，采购操作已停止", "accounting_dimension_invalid")
			for field, value in stored_dimensions.items():
				if value not in (None, ""):
					if field in dimensions and dimensions[field] != value:
						operation.reject("中国会计凭证维度与原生字段不一致", "accounting_dimension_mismatch")
					dimensions[field] = value
		key = (
			*(
				row.get(field) or ""
				for field in (
					"account",
					"party_type",
					"party",
					"account_currency",
					"cost_center",
					"project",
					"finance_book",
					"against_voucher_type",
					"against_voucher",
				)
			),
			operation.encode(dimensions),
		)
		value = result.setdefault(key, [Decimal(0)] * 4)
		for index, field in enumerate(
			("debit", "credit", "debit_in_account_currency", "credit_in_account_currency")
		):
			value[index] += service.amount(row.get(field))
	return result


def _gl_precision(doc, field, currency=None):
	from frappe.model.meta import get_field_precision

	currency = currency or frappe.get_cached_value("Company", doc.company, "default_currency")
	return get_field_precision(frappe.get_meta("GL Entry").get_field(field), currency=currency)


def _compare_gl(doc, actual, expected, label, *, reverse=False):
	for key in actual.keys() | expected.keys():
		for index, field in enumerate(
			("debit", "credit", "debit_in_account_currency", "credit_in_account_currency")
		):
			precision = _gl_precision(doc, field, key[3] if index >= 2 else None)
			planned = service.amount(expected.get(key, [0] * 4)[index ^ 1 if reverse else index])
			if flt(actual.get(key, [0] * 4)[index], precision) != flt(planned, precision):
				operation.reject("采购一致性核对失败：" + label, "gl_allocation_mismatch", invariant=label)


def _check_finance_voucher(doc, voucher, event, status):
	from china_finance.services.voucher import get_posting_date

	currency = frappe.get_cached_value("Company", doc.company, "default_currency")
	posting_date = getdate(get_posting_date(doc))
	if (
		voucher.docstatus != 1
		or (
			voucher.company,
			voucher.source_doctype,
			voucher.source_name,
			voucher.source_event,
			voucher.get("source_key"),
			voucher.get("currency"),
			voucher.get("status"),
		)
		!= (doc.company, doc.doctype, doc.name, event, f"{event}|{doc.doctype}|{doc.name}", currency, status)
		or (event == "Posting" and voucher.get("reversal_of"))
		or (
			not voucher.get("posting_date")
			or getdate(voucher.get("posting_date")) != posting_date
			or voucher.get("fiscal_year") != str(posting_date.year)
			or voucher.get("accounting_period") != posting_date.strftime("%Y-%m")
			or (status == "Posted" and voucher.get("reversed_by"))
		)
	):
		operation.reject("中国会计凭证状态、公司、来源或币种不一致", "finance_voucher_header_mismatch")
	for side in ("debit", "credit"):
		field = "total_" + side
		equal(
			voucher,
			field,
			voucher.get(field),
			sum((service.amount(row.get(side)) for row in voucher.entries), Decimal(0)),
			"中国会计凭证表头与分录合计",
		)
	equal(voucher, "total_debit", voucher.total_debit, voucher.total_credit, "中国会计凭证借贷")


def check_finance(doc, rows):
	field = "base_paid_amount" if doc.doctype == "Payment Entry" else "base_grand_total"
	_balanced(doc, rows, field)
	if doc.doctype == "Purchase Receipt":
		import erpnext
		from erpnext.stock import get_warehouse_account_map

		expected = (
			doc.get_gl_entries(get_warehouse_account_map(doc.company))
			if erpnext.is_perpetual_inventory_enabled(doc.company)
			or frappe.db.get_value(
				"Company", doc.company, "enable_provisional_accounting_for_non_stock_items"
			)
			else []
		)
	elif doc.doctype == "Purchase Invoice":
		from erpnext.accounts.general_ledger import process_gl_map

		expected = process_gl_map(doc.get_gl_entries())
	elif doc.doctype == "Payment Entry":
		expected = _payment_gl_plan(doc)
	else:
		expected = []
	actual_map, expected_map = _gl_map(rows), _gl_map(expected)
	_compare_gl(doc, actual_map, expected_map, "总账与原生控制器分录")
	finance = finance_service(doc)
	if not rows:
		return {"module": "China Finance", "result": "N/A", "reason": "native controller produces no GL"}
	if not finance:
		return {
			"module": "China Finance",
			"result": "N/A",
			"reason": "company finance settings inactive for posting date",
		}
	name = finance.create_voucher_from_source(doc, "Posting")
	if not name:
		operation.reject("中国会计凭证未生成，采购操作不能提交", "finance_posting_missing")
	voucher = frappe.get_doc("China Accounting Voucher", name, for_update=True)
	_check_finance_voucher(doc, voucher, "Posting", "Posted")
	# Compare individual native GL allocations too; balanced totals alone are insufficient.
	_compare_gl(doc, _gl_map(voucher.entries), actual_map, "中国会计凭证与总账")
	acknowledge_effect(voucher, system_effect=True)
	_acknowledge_finance_assignments(doc, voucher=voucher.name)
	return {"module": "China Finance", "result": "verified", "voucher": voucher.name}


def check_cancellation(doc, cancelled_gl):
	finance = finance_service(doc)
	if (not finance or not cancelled_gl) and frappe.db.exists("DocType", "China Accounting Voucher"):
		posted = frappe.db.get_values(
			"China Accounting Voucher",
			{
				"source_doctype": doc.doctype,
				"source_name": doc.name,
				"source_event": "Posting",
				"docstatus": 1,
			},
			["name"],
			as_dict=True,
			for_update=True,
		)
		if posted:
			operation.reject(
				"已有正式中国凭证，当前会计设置或总账快照不可用，采购取消已回滚",
				"finance_cancellation_settings_inactive"
				if not finance
				else "finance_cancellation_gl_missing",
			)
	if not cancelled_gl:
		if finance and frappe.db.exists(
			"China Voucher Sync Issue", {"issue_key": f"Cancellation|{doc.doctype}|{doc.name}"}
		):
			operation.reject(
				"无财务流水的原生取消仍产生会计同步任务，严格模式不能确认完成",
				"finance_zero_movement_cancellation_unsupported",
			)
		return {"module": "China Finance cancellation", "result": "N/A", "reason": "source has no GL"}
	if not finance:
		return {
			"module": "China Finance cancellation",
			"result": "N/A",
			"reason": "company finance settings inactive",
		}
	assignments = _finance_assignments(doc)
	result = finance.process_cancellation_snapshot(doc.doctype, doc.name)
	if result.get("status") != "resolved" or not result.get("voucher"):
		operation.reject("中国会计凭证冲销尚未完成，采购取消已回滚", "finance_cancellation_incomplete")
	return verify_cancellation(doc, cancelled_gl, result=result, assignments=assignments, acknowledge=True)


def verify_cancellation(doc, cancelled_gl, *, result=None, assignments=None, acknowledge=False):
	"""Pure verification shared by synchronous cancellation and final recovery."""
	if result is None:
		if not cancelled_gl or not finance_service(doc):
			if not frappe.db.exists("DocType", "China Accounting Voucher"):
				return {
					"module": "China Finance cancellation",
					"result": "N/A",
					"reason": "China Accounting Voucher is not installed",
				}
			posted = frappe.db.exists(
				"China Accounting Voucher",
				{
					"source_doctype": doc.doctype,
					"source_name": doc.name,
					"source_event": "Posting",
					"docstatus": 1,
				},
			)
			if posted:
				operation.reject("中国会计取消证据不可用", "finance_cancellation_settings_inactive")
			return {
				"module": "China Finance cancellation",
				"result": "verified",
				"reason": "no applicable posting",
			}
		result = {
			"voucher": frappe.db.get_value(
				"China Accounting Voucher",
				{
					"source_doctype": doc.doctype,
					"source_name": doc.name,
					"source_event": "Cancellation",
					"docstatus": 1,
				},
				"name",
			),
			"issue": frappe.db.get_value(
				"China Voucher Sync Issue", {"issue_key": f"Cancellation|{doc.doctype}|{doc.name}"}, "name"
			),
		}
		if not result["voucher"]:
			operation.reject("中国会计冲销证据缺失", "finance_cancellation_incomplete")
	assignments = _finance_assignments(doc) if assignments is None else assignments
	reversal = frappe.get_doc("China Accounting Voucher", result["voucher"], for_update=True)
	if reversal.docstatus != 1 or not reversal.reversal_of:
		operation.reject("中国会计凭证冲销关联不完整", "finance_reversal_link_missing")
	original = frappe.get_doc("China Accounting Voucher", reversal.reversal_of, for_update=True)
	if original.reversed_by != reversal.name or original.status != "Reversed":
		operation.reject("中国会计凭证原凭证尚未完成冲销", "finance_original_not_reversed")
	for voucher, event in ((original, "Posting"), (reversal, "Cancellation")):
		_check_finance_voucher(doc, voucher, event, "Reversed" if event == "Posting" else "Posted")
	_compare_gl(doc, _gl_map(original.entries), _gl_map(cancelled_gl), "原凭证与取消前原生总账")
	_compare_gl(
		doc, _gl_map(reversal.entries), _gl_map(original.entries), "冲销凭证与原凭证反向金额", reverse=True
	)
	if acknowledge:
		for voucher in (original, reversal):
			acknowledge_effect(voucher, system_effect=True)
	if not result.get("issue"):
		operation.reject("中国会计冲销缺少同步记录身份", "finance_cancellation_issue_missing")
	issue = frappe.get_doc("China Voucher Sync Issue", result["issue"], for_update=True)
	if (
		issue.company,
		issue.source_doctype,
		issue.source_name,
		issue.issue_key,
		issue.status,
		issue.cancellation_voucher,
	) != (
		doc.company,
		doc.doctype,
		doc.name,
		f"Cancellation|{doc.doctype}|{doc.name}",
		"Resolved",
		reversal.name,
	):
		operation.reject("中国会计冲销同步记录未完成或来源不一致", "finance_cancellation_issue_mismatch")
	if acknowledge:
		acknowledge_effect(issue, system_effect=True)
	for name in assignments:
		assignment = frappe.get_doc("China Cash Flow Assignment", name, for_update=True)
		if assignment.status != "Cancelled" or assignment.docstatus not in (0, 2):
			operation.reject("中国会计现金流量指定尚未完成取消", "finance_cash_assignment_not_cancelled")
		if acknowledge:
			acknowledge_effect(assignment, system_effect=True)
	return {"module": "China Finance cancellation", "result": "verified", "voucher": reversal.name}


def _finance_assignments(doc, *, voucher=None):
	if not frappe.db.exists("DocType", "China Cash Flow Assignment"):
		return []
	filters = {"company": doc.company, "source_doctype": doc.doctype, "source_name": doc.name}
	if voucher:
		filters["china_accounting_voucher"] = voucher
	return [
		row.name
		for row in frappe.db.get_values(
			"China Cash Flow Assignment", filters, ["name"], as_dict=True, order_by="name", for_update=True
		)
	]


def _acknowledge_finance_assignments(doc, *, voucher):
	for name in _finance_assignments(doc, voucher=voucher):
		acknowledge_effect(
			frappe.get_doc("China Cash Flow Assignment", name, for_update=True), system_effect=True
		)


def check_sources(doc, *, reader=None, validate_execution=True, allow_draft_receipts=False):
	"""Validate every supplied native row identity, including native forms/multi-PO."""
	read = reader or service._current
	identities = set()
	for row in doc.get("items") or []:
		for doctype, link, detail in (
			(
				"Purchase Order",
				"purchase_order",
				"purchase_order_item" if doc.doctype == "Purchase Receipt" else "po_detail",
			),
			("Purchase Receipt", "purchase_receipt", "pr_detail"),
		):
			name, identity = row.get(link), row.get(detail)
			if not name and not identity:
				continue
			if not name or not identity:
				operation.reject("采购来源父单据与明细身份必须同时存在", "purchase_source_identity_missing")
			source = read(doctype, name)
			if reader:
				service._require_fields(doctype, {"company", "supplier", "items"})
				service._require_fields(doctype + " Item", {"item_code", "stock_uom"}, doctype)
			original = next((item for item in source.items if item.name == identity), None)
			if (
				not original
				or original.item_code != row.item_code
				or original.get("stock_uom") != row.get("stock_uom")
			):
				operation.reject("采购来源明细、物料或库存单位不一致", "purchase_source_row_mismatch")
			draft_receipt = (
				allow_draft_receipts
				and doc.doctype == "Purchase Invoice"
				and doc.docstatus == 0
				and not doc.get("update_stock")
				and doctype == "Purchase Receipt"
				and source.docstatus == 0
			)
			if (
				source.company != doc.company
				or source.supplier != doc.supplier
				or (source.docstatus != 1 and not draft_receipt)
			):
				operation.reject("采购来源状态、公司或供应商不一致", "purchase_source_header_mismatch")
			identities.add((source.doctype, source.name))
			if doctype == "Purchase Order" and validate_execution:
				from .purchase_source_service import validate_source_before_submit

				validate_source_before_submit(source)
				reason = service.order_execution_reason(source)
				if reason and source.status != "Completed":
					frappe.throw(reason)
	if (
		doc.doctype == "Purchase Invoice"
		and doc.get("update_stock")
		and any(row.get("purchase_receipt") for row in doc.items)
	):
		operation.reject("已入库来源应付不能重复更新库存", "purchase_stock_double_posting")
	return identities


def check_stock(doc):
	rows = _ledger(doc, "Stock Ledger Entry", is_cancelled=0)
	if doc.doctype == "Purchase Invoice" and not doc.get("update_stock"):
		if rows:
			operation.reject("非库存采购应付不能重复形成库存流水", "non_stock_invoice_has_sle")
		return
	expected = {}
	for row in doc.items:
		if not frappe.get_cached_value("Item", row.item_code, "is_stock_item"):
			continue
		for warehouse, qty in (
			(row.get("warehouse"), row.get("stock_qty")),
			(
				row.get("rejected_warehouse"),
				service.amount(row.get("rejected_qty")) * service.amount(row.get("conversion_factor")),
			),
		):
			if not warehouse or not service.amount(qty):
				continue
			key = (row.name, row.item_code, warehouse)
			expected[key] = (row, service.amount(qty) if doc.docstatus == 1 else Decimal(0))
			warehouse_doc = service._read("Warehouse", warehouse, {"company", "is_group", "disabled"})
			if warehouse_doc.company != doc.company or warehouse_doc.is_group or warehouse_doc.disabled:
				operation.reject("原生库存仓库公司或状态不一致", "purchase_warehouse_invalid")
	actual = {}
	for entry in rows:
		key = (entry.voucher_detail_no, entry.item_code, entry.warehouse)
		if key not in expected:
			operation.reject("采购库存流水没有明确对应原生物料明细和仓库", "purchase_sle_source_mismatch")
		actual[key] = actual.get(key, Decimal(0)) + service.amount(entry.actual_qty)
	for key, (row, qty) in expected.items():
		equal(row, "stock_qty", actual.get(key, 0), qty, "入库流水与原生库存数量")
	for item, warehouse in sorted({(key[1], key[2]) for key in expected}):
		entries = frappe.db.get_values(
			"Stock Ledger Entry",
			{"item_code": item, "warehouse": warehouse, "is_cancelled": 0},
			["actual_qty"],
			as_dict=True,
			for_update=True,
		)
		bins = frappe.db.get_values(
			"Bin", {"item_code": item, "warehouse": warehouse}, ["actual_qty"], as_dict=True, for_update=True
		)
		if len(bins) != 1:
			operation.reject("原生库存 Bin 缺失或重复", "purchase_bin_missing_or_duplicate")
		row = next(value[0] for key, value in expected.items() if key[1:] == (item, warehouse))
		equal(
			row,
			"stock_qty",
			bins[0].actual_qty,
			sum((service.amount(entry.actual_qty) for entry in entries), Decimal(0)),
			"Bin 与库存流水",
		)


def check_native_repost(doc, method=None):
	"""A real native repost created inside this procurement operation is an effect.

	Never run its executor: in production it commits and finishes asynchronously.
	The hook is inert for independent native stock jobs outside this boundary.
	"""
	from . import purchase_native_repost
	from . import purchase_reversal_progress as progress

	if progress.capture_root(doc) or purchase_native_repost.capture_child(doc):
		return
	context = operation.current()
	if context is None:
		return

	def verify():
		if doc.docstatus != 1 or doc.status != "Completed":
			operation.reject(
				"本次采购操作需要异步库存重估，严格同事务模式暂不支持，整笔已回滚",
				"native_stock_repost_incomplete",
			)
		acknowledge_effect(frappe.get_doc(doc.doctype, doc.name, for_update=True), system_effect=True)

	operation.trace(
		context,
		"Inventory / native stock repost",
		verify,
		source=context["documents"],
		target={"doctype": doc.doctype, "name": doc.name},
	)


def check_pending_reposts(doc, pairs=None):
	"""Only native pending reposts sharing this operation's actual stock scope.

	Transaction-based identities must join their real SLE voucher. Item-based
	identities must join real SLE item/warehouse pairs; no date-based attribution.
	We refuse, but never run, cancel, or change these existing native records.
	"""
	from pypika.terms import Criterion

	sources = [doc]
	sources.extend(
		service._current("Purchase Receipt", name)
		for name in sorted(
			{row.get("purchase_receipt") for row in (doc.get("items") or []) if row.get("purchase_receipt")}
		)
	)
	pairs = (
		pairs
		if pairs is not None
		else {
			(row.item_code, warehouse)
			for source in sources
			for row in (source.get("items") or [])
			for warehouse in (row.get("warehouse"), row.get("rejected_warehouse"))
			if warehouse and frappe.get_cached_value("Item", row.item_code, "is_stock_item")
		}
	)
	if not pairs:
		return
	repost = frappe.qb.DocType("Repost Item Valuation")
	sle = frappe.qb.DocType("Stock Ledger Entry")
	native_link = (
		(repost.based_on == "Transaction")
		& (repost.voucher_type == sle.voucher_type)
		& (repost.voucher_no == sle.voucher_no)
	) | (
		(repost.based_on == "Item and Warehouse")
		& (repost.item_code == sle.item_code)
		& (repost.warehouse == sle.warehouse)
	)
	affected = Criterion.any(
		(sle.item_code == item) & (sle.warehouse == warehouse) for item, warehouse in sorted(pairs)
	)
	pending = (
		frappe.qb.from_(repost)
		.inner_join(sle)
		.on(native_link)
		.select(repost.name)
		.distinct()
		.where(
			(repost.company == doc.company)
			& (sle.company == doc.company)
			& (repost.docstatus == 1)
			& repost.status.notin(["Completed", "Cancelled"])
			& affected
		)
		.orderby(repost.name)
		.for_update()
		.run(as_dict=True)
	)
	from .purchase_reversal_progress import owned_pending

	if any(not owned_pending(row.name) for row in pending):
		operation.reject(
			"已有待完成的原生库存重估影响本次采购，请先核对原生重估状态；本次未写入",
			"native_stock_repost_pending",
		)


def check_order_received(doc):
	names = {row.get("purchase_order") for row in doc.items if row.get("purchase_order")}
	for name in sorted(names):
		order = service._current("Purchase Order", name)
		receipts = frappe.db.get_values(
			"Purchase Receipt Item",
			{"purchase_order": name, "docstatus": 1},
			["purchase_order_item", "received_qty", "conversion_factor", "stock_uom"],
			as_dict=True,
			for_update=True,
		)
		invoices = frappe.db.get_values(
			"Purchase Invoice Item",
			{"purchase_order": name, "docstatus": 1},
			["po_detail", "received_qty", "conversion_factor", "stock_uom", "parent"],
			as_dict=True,
			for_update=True,
		)
		totals = {}
		for row in receipts:
			original = next((item for item in order.items if item.name == row.purchase_order_item), None)
			if not original or row.stock_uom != original.stock_uom:
				operation.reject("订单收货来源明细或库存单位不一致", "purchase_received_source_mismatch")
			totals[original.name] = totals.get(original.name, Decimal(0)) + service.amount(row.received_qty)
		for row in invoices:
			if not frappe.db.get_value("Purchase Invoice", row.parent, "update_stock"):
				continue
			original = next((item for item in order.items if item.name == row.po_detail), None)
			if not original or row.stock_uom != original.stock_uom:
				operation.reject("库存应付来源明细或库存单位不一致", "purchase_stock_invoice_source_mismatch")
			totals[original.name] = totals.get(original.name, Decimal(0)) + service.amount(row.received_qty)
		for row in order.items:
			equal(
				row, "received_qty", row.received_qty, totals.get(row.name, 0), "采购订单已收数量与原生来源"
			)
		acknowledge_effect(order)


def check_invoice_balance(doc, *, gl_plan=None):
	if gl_plan is not None:
		from erpnext.accounts.utils import get_payment_ledger_entries

		plan = get_payment_ledger_entries(gl_plan)
		for ledger in ("Payment Ledger Entry", "Advance Payment Ledger Entry"):
			_compare_payment_ledger(
				doc,
				ledger,
				_ledger(doc, ledger, delinked=0),
				[row for row in plan if row.doctype == ledger],
				"重算应付本单付款及预付账簿",
			)
	from erpnext.accounts.utils import QueryPaymentLedger

	ple = frappe.qb.DocType("Payment Ledger Entry")
	outstanding = QueryPaymentLedger().get_voucher_outstandings(
		[frappe._dict(voucher_type=doc.doctype, voucher_no=doc.name)],
		common_filter=[ple.party_type == "Supplier", ple.party == doc.supplier, ple.account == doc.credit_to],
	)
	if not outstanding:
		from erpnext.accounts.general_ledger import process_gl_map

		# A native zero-value invoice has no financial movement, hence no PLE.
		# Prove that native plan AND stored GL/PLE are empty; a missing ledger
		# on a real payable must still fail closed.
		if (
			process_gl_map(doc.get_gl_entries())
			or _ledger(doc, "GL Entry")
			or _ledger(doc, "Payment Ledger Entry")
		):
			operation.reject("采购应付缺少原生付款账簿", "purchase_invoice_ple_missing")
		equal(doc, "outstanding_amount", doc.outstanding_amount, 0, "无财务流水应付余额必须为零")
		return {
			"module": "AP / Payment Ledger",
			"result": "N/A",
			"reason": "native invoice produces no financial movement",
		}
	equal(
		doc,
		"outstanding_amount",
		doc.outstanding_amount,
		outstanding[0]["outstanding_in_account_currency"],
		"应付余额与原生付款账簿",
	)
	return {"module": "AP / Payment Ledger", "result": "verified"}


def _compare_payment_ledger(doc, ledger, actual, expected, label):
	from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import get_accounting_dimensions

	fields = (
		"company",
		"account",
		"account_currency",
		"currency",
		"party_type",
		"party",
		"project",
		"cost_center",
		"finance_book",
		"voucher_detail_no",
		"against_voucher_type",
		"against_voucher_no",
		"event",
		*get_accounting_dimensions(),
	)

	def allocations(rows):
		result = {}
		for row in rows:
			key = tuple(row.get(field) or "" for field in fields)
			values = result.setdefault(key, [Decimal(0)] * 4)
			for index, field in enumerate(("amount", "amount_in_account_currency")):
				amount = service.amount(row.get(field))
				values[index * 2 + int(amount < 0)] += abs(amount)
		return result

	planned, stored = allocations(expected), allocations(actual)
	for key in planned.keys() | stored.keys():
		for index in range(4):
			if ledger == "Advance Payment Ledger Entry" and index >= 2:
				continue
			from frappe.model.meta import get_field_precision

			currency = key[3] or key[2] or frappe.get_cached_value("Company", doc.company, "default_currency")
			if index < 2 and ledger == "Payment Ledger Entry":
				currency = frappe.get_cached_value("Company", doc.company, "default_currency")
			field = "amount" if index < 2 else "amount_in_account_currency"
			precision = get_field_precision(frappe.get_meta(ledger).get_field(field), currency=currency)
			if flt(stored.get(key, [0] * 4)[index], precision) != flt(
				planned.get(key, [0] * 4)[index], precision
			):
				operation.reject(
					"采购付款本单账簿与原生核销计划不一致", "payment_ledger_mismatch", invariant=label
				)


def _payment_gl_plan(doc):
	from erpnext.accounts.general_ledger import process_gl_map

	return process_gl_map(
		doc.build_gl_map(),
		merge_entries=frappe.get_single_value("Accounts Settings", "merge_similar_account_heads"),
	)


def check_payment_ledgers(doc):
	from erpnext.accounts.utils import get_payment_ledger_entries

	plan = get_payment_ledger_entries(_payment_gl_plan(doc))
	for ledger in ("Payment Ledger Entry", "Advance Payment Ledger Entry"):
		_compare_payment_ledger(
			doc,
			ledger,
			_ledger(doc, ledger, delinked=0),
			[row for row in plan if row.doctype == ledger],
			"本单付款核销",
		)
	for row in doc.references:
		if row.reference_doctype == "Purchase Order":
			order = service._current("Purchase Order", row.reference_name)
			advances = order.calculate_total_advance_from_ledger()
			equal(
				order,
				"advance_paid",
				order.advance_paid,
				advances[0].amount if advances else 0,
				"订单预付与原生预付账簿",
			)
			acknowledge_effect(order)


def check_billing(doc, *, context=None):
	"""Read native source rows and status calculators, without posting or repairing."""
	from erpnext.stock.doctype.purchase_receipt.purchase_receipt import (
		get_billed_amount_against_po,
		get_billed_amount_against_pr,
		get_purchase_receipts_against_po_details,
		update_billing_percentage,
	)

	orders = {row.get("purchase_order") for row in doc.items if row.get("purchase_order")}
	receipts = {row.get("purchase_receipt") for row in doc.items if row.get("purchase_receipt")}
	if doc.doctype == "Purchase Receipt":
		receipts.add(doc.name)
	state = _state()
	if context is None:
		context = operation.current() or (state["context"] if state else {})
	preserved = context.get("billing_preserved", {})

	def unchanged(source):
		previous = preserved.get(source.doctype + ":" + source.name)
		if previous is None:
			return False
		for row in source.items:
			equal(
				row,
				"billed_amt",
				row.billed_amt,
				previous["items"].get(row.name, 0),
				"不更新开票退货需保留原生来源金额",
			)
		equal(
			source,
			"per_billed",
			source.per_billed,
			previous["per_billed"],
			"不更新开票退货需保留原生开票比例",
		)
		acknowledge_effect(source)
		return True

	for name in sorted(orders):
		order = service._current("Purchase Order", name)
		po_rows = frappe.db.get_values(
			"Purchase Invoice Item",
			{"purchase_order": name, "docstatus": 1},
			["po_detail", "amount", "qty"],
			as_dict=True,
			for_update=True,
		)
		totals = {}
		for row in po_rows:
			totals[row.po_detail] = totals.get(row.po_detail, Decimal(0)) + service.amount(row.amount)
		if not unchanged(order):
			for row in order.items:
				equal(
					row, "billed_amt", row.billed_amt, totals.get(row.name, 0), "订单已开票金额与原生应付来源"
				)
			percent = order._calculate_target_parent_percentage(
				name, "Purchase Order", "Purchase Order Item", "amount", "billed_amt"
			)
			if not service.amount(order.base_net_total):
				quantities = sum(service.amount(row.qty) for row in po_rows)
				percent = min(
					100, quantities / (sum(service.amount(row.qty) for row in order.items) or 1) * 100
				)
			equal(order, "per_billed", order.per_billed, percent, "订单开票比例与原生状态更新")
			acknowledge_effect(order)
		# Reuse native PR selection/FIFO order and direct PO/PR billed queries.
		# This is a read-only comparison of the native updater's amount plan.
		details = [row.name for row in order.items]
		pr_rows = get_purchase_receipts_against_po_details(details)
		direct = get_billed_amount_against_pr([row.name for row in pr_rows])
		remaining = get_billed_amount_against_po(details)
		for row in pr_rows:
			amount = service.amount(direct.get(row.name))
			po = remaining.get(row.purchase_order_item, {})
			available, qty = service.amount(po.get("billed_amt")), service.amount(po.get("billed_qty"))
			if available and amount < service.amount(row.amount):
				if not amount and qty > service.amount(row.qty):
					amount = available * service.amount(row.qty) / qty
				else:
					used = min(service.amount(row.amount) - amount, available)
					amount += used
					po["billed_amt"] = available - used
			if "Purchase Receipt:" + row.parent not in preserved:
				receipt = service._current("Purchase Receipt", row.parent)
				item = next(item for item in receipt.items if item.name == row.name)
				equal(item, "billed_amt", item.billed_amt, amount, "入库已开票金额与原生应付来源")
			receipts.add(row.parent)
	for name in sorted(receipts):
		receipt = service._current("Purchase Receipt", name)
		if unchanged(receipt):
			continue
		if not any(row.get("purchase_order") for row in receipt.items):
			direct = get_billed_amount_against_pr([row.name for row in receipt.items])
			for row in receipt.items:
				equal(
					row, "billed_amt", row.billed_amt, direct.get(row.name, 0), "入库已开票金额与原生应付来源"
				)
		if receipt.docstatus != 1:
			continue
		# Execute the native calculator on a copy. Only this copy's DB and stock
		# callbacks are inert, including its optional invoice-rate cost adjustment.
		planned = frappe.get_doc(receipt.as_dict())
		values = {}
		planned.db_set = lambda field, value=None, **kwargs: values.update(
			{field: value} if isinstance(field, str) else field
		)
		adjust = frappe.db.get_single_value(
			"Buying Settings", "set_landed_cost_based_on_purchase_invoice_rate"
		)
		if adjust:
			for item in planned.items:
				item.db_set = lambda field, value, **kwargs: None
				item.db_update = lambda: None
			planned.update_valuation_rate = lambda **kwargs: None
			planned.enable_recalculate_rate_in_sles = lambda: None
			planned.repost_future_sle_and_gle = lambda **kwargs: None
		update_billing_percentage(planned, update_modified=False, adjust_incoming_rate=adjust)
		equal(receipt, "per_billed", receipt.per_billed, values["per_billed"], "入库开票比例与原生状态更新")
		acknowledge_effect(receipt)


def check_cancelled_ledgers(doc, *, context=None):
	"""ERPNext retains original plus swapped GL rows under either ledger policy."""
	from erpnext.accounts.utils import is_immutable_ledger_enabled

	state = _state()
	if context is None:
		context = operation.current() or (state["context"] if state else {})
	before = context.get("gl_before", {}).get(doc.doctype + ":" + doc.name)
	rows = _ledger(doc, "GL Entry")
	if before is None:
		operation.reject("采购取消缺少原生总账快照，不能确认冲销", "cancellation_gl_snapshot_missing")
	before = [frappe._dict(row) for row in before]  # durable JSON or same-tx native rows
	original_names = {row.name for row in before}
	original = [row for row in rows if row.name in original_names]
	reverse = [row for row in rows if row.name not in original_names]
	if len(original) != len(before) or any(
		row.is_cancelled != (0 if is_immutable_ledger_enabled() else 1) for row in rows
	):
		operation.reject("采购取消原生总账状态或分录不完整", "cancellation_gl_state_mismatch")

	# Native PI cancellation clears its own against-voucher link on BOTH sets.
	# Preserve every monetary/account/dimension key; allow only that exact unlink.
	def normalised(entries):
		result = []
		fields = (
			"account",
			"party_type",
			"party",
			"account_currency",
			"cost_center",
			"project",
			"finance_book",
		)
		for entry in entries:
			entry = frappe._dict(entry.copy())
			if (
				not entry.get("against_voucher_type")
				and not entry.get("against_voucher")
				and any(
					all(row.get(field) == entry.get(field) for field in fields)
					and (row.get("against_voucher_type"), row.get("against_voucher"))
					== (doc.doctype, doc.name)
					for row in before
				)
			):
				entry.update(against_voucher_type=doc.doctype, against_voucher=doc.name)
			result.append(entry)
		return result

	_compare_gl(doc, _gl_map(normalised(original)), _gl_map(before), "取消前原生总账不可改写")
	_compare_gl(doc, _gl_map(normalised(reverse)), _gl_map(before), "原生总账取消反向金额", reverse=True)
	immutable = is_immutable_ledger_enabled()
	from erpnext.accounts.utils import get_payment_ledger_entries

	payment_before = context.get("payment_before", {}).get(doc.doctype + ":" + doc.name)
	if payment_before is None:
		operation.reject("采购取消缺少原生付款账簿快照", "cancellation_payment_snapshot_missing")
	# Persisted GL omits PE's transient advance-voucher metadata. The same
	# native planner retains it and must still match all captured GL amounts.
	plan = _payment_gl_plan(doc) if doc.doctype == "Payment Entry" else before
	if doc.doctype == "Payment Entry":
		_compare_gl(doc, _gl_map(plan), _gl_map(before), "原生付款取消计划与原总账")
	reverse_plan = get_payment_ledger_entries(plan, cancel=1)
	for ledger in ("Payment Ledger Entry", "Advance Payment Ledger Entry"):
		rows = _ledger(doc, ledger)
		if any(row.delinked != (0 if immutable else 1) for row in rows):
			operation.reject("采购取消后付款或预付账簿状态不一致", "cancellation_payment_ledger_active")
		originals = [frappe._dict(row) for row in payment_before[ledger]]
		names = {row.name for row in originals}
		retained = [row for row in rows if row.name in names]
		if len(retained) != len(originals):
			operation.reject("采购取消需保留原付款账簿", "cancellation_payment_original_missing")
		if not immutable and ledger == "Advance Payment Ledger Entry":
			originals = [frappe._dict(row, event="Cancel") for row in originals]
		_compare_payment_ledger(doc, ledger, retained, originals, "取消付款原流水")
		_compare_payment_ledger(
			doc,
			ledger,
			[row for row in rows if row.name not in names],
			[row for row in reverse_plan if row.doctype == ledger],
			"取消付款反向流水",
		)
	return before


def _auto_invoice_enabled(doc):
	if (
		doc.doctype != "Purchase Receipt"
		or doc.get("is_return")
		or not frappe.db.exists("DocType", "China Finance Settings")
	):
		return False
	from china_finance.services.auto_invoice import _enabled

	return _enabled(doc.company, "auto_submit_purchase_invoice")


def _auto_invoices(doc):
	rows = frappe.db.get_values(
		"Purchase Invoice Item",
		{"purchase_receipt": doc.name},
		["parent", "docstatus"],
		as_dict=True,
		order_by="creation asc",
		for_update=True,
	)
	return [
		frappe.get_doc("Purchase Invoice", name, for_update=True)
		for name in dict.fromkeys(row.parent for row in rows if row.docstatus in (0, 1))
	]


def _permission(doc, ptype):
	# China Finance's existing hook sets ignore_permissions. Check the actual
	# actor with the native ACL API, which does not consult this bypass flag.
	if not frappe.has_permission(doc.doctype, ptype, doc=doc):
		operation.reject(
			"没有自动应付单所需权限，采购操作已停止", "auto_pi_permission_missing", frappe.PermissionError
		)


def prepare_auto_invoice(doc):
	"""Guard the existing company hook; it remains the sole invoice writer."""
	if (
		doc.doctype == "Purchase Invoice"
		and doc.flags.get("ignore_permissions")
		and any(row.get("purchase_receipt") for row in doc.items)
		and frappe.db.exists("DocType", "China Finance Settings")
	):
		from china_finance.services.auto_invoice import _enabled

		if _enabled(doc.company, "auto_submit_purchase_invoice"):
			# The hook's mapped invoice is now available. Its real document ACL
			# may be stricter than the earlier doctype-only role preflight.
			for ptype in ("read", "create" if doc.is_new() else "write"):
				_permission(doc, ptype)
			if doc.docstatus == 1:
				_permission(doc, "submit")
		return
	if not _auto_invoice_enabled(doc) or doc.docstatus != 1:
		return
	# _action is set by native _save AFTER this boundary starts. A reused draft
	# object can still carry "save" while submit() has already set docstatus=1.
	if doc.name and frappe.db.get_value("Purchase Receipt", doc.name, "docstatus") == 1:
		return
	invoices = _auto_invoices(doc)
	for invoice in invoices:
		_permission(invoice, "read")
	# Match the existing hook's first active invoice decision exactly.
	target = invoices[0] if invoices else None
	if target and target.docstatus == 1:
		return
	if target:
		for ptype in ("write", "submit"):
			_permission(target, ptype)
	elif not frappe.has_permission("Purchase Invoice", "create") or not frappe.has_permission(
		"Purchase Invoice", "submit"
	):
		operation.reject(
			"没有创建和提交自动应付单权限，采购操作已停止",
			"auto_pi_permission_missing",
			frappe.PermissionError,
		)
	from frappe.model.workflow import get_workflow_name

	if get_workflow_name("Purchase Invoice"):
		operation.reject("自动应付单需经过原生工作流，请先完成应付审批", "auto_pi_workflow_required")


def check_auto_invoice(doc):
	if not _auto_invoice_enabled(doc) or doc.docstatus != 1:
		return
	invoices = _auto_invoices(doc)
	totals = {}
	for invoice in invoices:
		_permission(invoice, "read")
		if invoice.company != doc.company or invoice.supplier != doc.supplier or invoice.get("is_return"):
			operation.reject("自动应付单公司、供应商或退货来源不一致", "auto_pi_header_mismatch")
		if invoice.docstatus != 1:
			operation.reject("自动应付尚有草稿，采购操作不能确认完成", "auto_pi_draft_incomplete")
		acknowledge_effect(invoice)
		for row in invoice.items:
			if row.get("purchase_receipt") != doc.name:
				continue
			original = next((item for item in doc.items if item.name == row.get("pr_detail")), None)
			if (
				not original
				or row.item_code != original.item_code
				or row.get("stock_uom") != original.get("stock_uom")
			):
				operation.reject("自动应付单来源明细不完整", "auto_pi_source_row_missing")
			totals[original.name] = totals.get(original.name, Decimal(0)) + service.amount(row.qty)
	bill_rejected = frappe.db.get_single_value(
		"Buying Settings", "bill_for_rejected_quantity_in_purchase_invoice"
	)
	from erpnext.stock.doctype.purchase_receipt.purchase_receipt import get_returned_qty_map

	returned = get_returned_qty_map(doc.name)
	for row in doc.items:
		qty = service.amount(row.received_qty if bill_rejected else row.qty)
		if not bill_rejected:
			returned_qty = max(
				Decimal(0), service.amount(returned.get(row.name)) - service.amount(row.get("rejected_qty"))
			)
			qty = max(Decimal(0), qty - returned_qty)
		equal(row, "qty", totals.get(row.name, 0), qty, "自动应付必须完整覆盖原生可开票数量")


def check_operating_dependencies(doc):
	"""Read-only existing fail-closed applicability check, also used by scope reads."""
	if doc.get("custom_operating_source") or doc.get("custom_operating_recognition"):
		operation.reject(
			"采购与经营费用关联尚无同步适配，操作已停止，请核对原生关联", "operating_dependency_unsupported"
		)


def check_document(doc):
	check_operating_dependencies(doc)
	if doc.docstatus == 0:
		_stage(doc, "native draft / stock / GL / AP", lambda: check_draft(doc))
		return [
			{"module": "native ledgers", "result": "verified", "reason": "draft has no real movement"},
			*_stage(
				doc, "Sales / procurement review / Operating applicability", lambda: invalidate_reviews(doc)
			),
		]
	_stage(doc, "procurement / native associated source effects", lambda: acknowledge_native_sources(doc))
	if doc.doctype == "Purchase Order":
		from .purchase_source_service import validate_source_before_submit

		if doc.docstatus == 1:
			_stage(doc, "procurement / source guards", lambda: validate_source_before_submit(doc))
		return [
			{"module": "procurement source", "result": "verified"},
			*_stage(
				doc, "Sales / procurement review / Operating applicability", lambda: invalidate_reviews(doc)
			),
		]
	if doc.doctype == "Payment Entry":
		doc.validate_duplicate_entry()
		if {row.reference_doctype for row in doc.references} - {"Purchase Invoice", "Purchase Order"}:
			operation.reject(
				"混合采购与其他模块付款引用尚无同步适配，操作已停止", "mixed_payment_sources_unsupported"
			)
		for row in doc.references:
			source = service._current(row.reference_doctype, row.reference_name)
			if source.company != doc.company or source.supplier != doc.party:
				operation.reject("付款引用公司或供应商不一致", "payment_source_header_mismatch")
			if row.reference_doctype == "Purchase Invoice" and source.docstatus == 1:
				_stage(source, "AP / Payment Ledger", lambda: check_invoice_balance(source))
			acknowledge_effect(source)
		if doc.docstatus == 1:
			_stage(doc, "Payment / own ledger allocations / PO advance", lambda: check_payment_ledgers(doc))
		else:
			for row in doc.references:
				if row.reference_doctype == "Purchase Order":
					source = service._current("Purchase Order", row.reference_name)
					advance = source.calculate_total_advance_from_ledger()
					equal(
						source,
						"advance_paid",
						source.advance_paid,
						advance[0].amount if advance else 0,
						"取消后订单预付与原生账簿",
					)
	else:
		if doc.docstatus == 1:
			_stage(doc, "procurement source / row identities", lambda: check_sources(doc))
		_stage(doc, "Inventory / SLE / Bin / warehouse", lambda: check_stock(doc))
		_stage(doc, "Inventory / final native stock reposts", lambda: check_pending_reposts(doc))
		_stage(doc, "procurement / authoritative received quantities", lambda: check_order_received(doc))
		_stage(doc, "AP / authoritative billed amounts and status", lambda: check_billing(doc))
		if doc.doctype == "Purchase Receipt":
			_stage(doc, "AP / company automatic invoice completeness", lambda: check_auto_invoice(doc))
	gl = _ledger(doc, "GL Entry", is_cancelled=0)
	if doc.docstatus == 2:
		original_gl = _stage(doc, "native ledger cancellation", lambda: check_cancelled_ledgers(doc))
		modules = [
			_stage(
				doc, "China Finance / synchronous cancellation", lambda: check_cancellation(doc, original_gl)
			)
		]
	else:
		modules = [_stage(doc, "GL / China Finance", lambda: check_finance(doc, gl))]
		if doc.doctype == "Purchase Invoice":
			modules.append(_stage(doc, "AP / Payment Ledger", lambda: check_invoice_balance(doc)))
	modules.extend(
		_stage(doc, "Sales / procurement review / Operating applicability", lambda: invalidate_reviews(doc))
	)
	return modules


def invalidate_reviews(doc):
	"""Invalidate existing managed review versions; never overwrite native prices."""
	from . import purchase_fulfilment_service as fulfilment

	check_sales_dependencies(doc)
	if doc.doctype != "Purchase Order":
		return [
			{
				"module": "Sales/procurement review",
				"result": "N/A",
				"reason": "receipt/payment does not change confirmed native order price or quantity",
			},
			{"module": "Operating", "result": "N/A", "reason": "no registered Operating dependency"},
		]
	old = doc.get_doc_before_save()
	fields = ("company", "supplier", "currency", "grand_total")
	changed = (
		not old
		or any(doc.get(field) != old.get(field) for field in fields)
		or [
			(row.name, row.item_code, row.qty, row.rate, row.get("uom"), row.get("conversion_factor"))
			for row in doc.items
		]
		!= [
			(row.name, row.item_code, row.qty, row.rate, row.get("uom"), row.get("conversion_factor"))
			for row in old.items
		]
	)
	if not changed and doc.docstatus != 2:
		return [
			{"module": "Sales/procurement review", "result": "N/A", "reason": "native order terms unchanged"}
		]
	order_names = {doc.name}
	links = []
	if order_names and frappe.db.exists("DocType", fulfilment.DOCTYPE):
		links = frappe.db.sql(
			"SELECT name FROM `tabPurchase Fulfilment Link` WHERE active=1 AND "
			"(external_order IN %(orders)s OR internal_order IN %(orders)s) ORDER BY name FOR UPDATE",
			{"orders": tuple(sorted(order_names))},
			as_dict=True,
		)
	for row in links:
		link = frappe.get_doc(fulfilment.DOCTYPE, row.name, for_update=True)
		versions = fulfilment._json(link.source_versions_json)
		marker = doc.doctype + ":" + doc.name + ":" + str(doc.modified)
		if versions.get("procurement_review_invalidated") == marker:
			continue
		fulfilment._audit(
			link,
			"native_procurement_change",
			{"source": marker, "previous_price_confirmation": fulfilment._json(link.price_confirmation_json)},
		)
		versions.update(procurement_review_invalidated=marker, external_item_version="invalidated:" + marker)
		frappe.db.set_value(
			fulfilment.DOCTYPE,
			link.name,
			{
				"source_versions_json": fulfilment._dump(versions),
				"price_confirmation_json": "{}",
				"price_confirmed_by": None,
				"price_confirmed_on": None,
				"audit_json": link.audit_json,
			},
		)
		acknowledge_effect(service._current(fulfilment.DOCTYPE, link.name))
	return [
		{
			"module": "Sales/procurement review",
			"result": "invalidated" if links else "N/A",
			"reason": "existing managed fulfilment links"
			if links
			else "no existing dependent fulfilment link",
		},
		{
			"module": "Operating",
			"result": "N/A",
			"reason": "native procurement has no registered Operating dependency",
		},
	]


def check_sales_dependencies(doc, *, reader=None):
	"""Native Sales row/reservation links require an adapter before we claim N/A."""
	read = reader or service._current
	intercompany_links = {
		"Purchase Order": ("inter_company_order_reference", None),
		"Purchase Receipt": ("inter_company_reference", "delivery_note_item"),
		"Purchase Invoice": ("inter_company_invoice_reference", "sales_invoice_item"),
	}
	documents = [doc]
	for row in doc.get("references") or []:
		if row.reference_doctype in ("Purchase Invoice", "Purchase Order"):
			documents.append(read(row.reference_doctype, row.reference_name))
	receipts = {
		row.get("purchase_receipt")
		for source in documents
		for row in source.get("items") or []
		if row.get("purchase_receipt")
	}
	documents.extend(read("Purchase Receipt", name) for name in sorted(receipts))
	orders = {
		row.get("purchase_order")
		for source in documents
		for row in source.get("items") or []
		if row.get("purchase_order")
	}
	documents.extend(read("Purchase Order", name) for name in sorted(orders))
	for source in documents:
		header, detail = intercompany_links.get(source.doctype, (None, None))
		if (header and source.get(header)) or (
			detail and any(row.get(detail) for row in source.get("items") or [])
		):
			operation.reject(
				"采购存在原生销售关联，尚无完整同步适配，操作已停止", "sales_dependency_unsupported"
			)
		for row in source.get("items") or []:
			if not any(
				row.get(field) for field in ("sales_order", "sales_order_item", "sales_order_packed_item")
			):
				continue
			detail = row.get("sales_order_item") or row.get("sales_order_packed_item")
			child_type = "Sales Order Item" if row.get("sales_order_item") else "Packed Item"
			if (
				not row.get("sales_order")
				or not detail
				or frappe.db.get_value(child_type, detail, "parent") != row.sales_order
			):
				operation.reject("采购明细的原生销售来源不完整，请核对单据", "sales_source_incomplete")
			operation.reject(
				"采购存在原生销售订单关联，尚无完整同步适配，操作已停止", "sales_dependency_unsupported"
			)
		if (
			source.doctype == "Purchase Receipt"
			and frappe.db.exists("DocType", "Stock Reservation Entry")
			and frappe.db.exists(
				"Stock Reservation Entry",
				{"from_voucher_type": source.doctype, "from_voucher_no": source.name, "docstatus": ["!=", 2]},
			)
		):
			operation.reject(
				"采购入库存在原生销售库存预留关联，尚无完整同步适配，操作已停止",
				"sales_reservation_unsupported",
			)


class ProcurementControllerBoundary:
	"""Also roll back native form/import controller failures, without touching other PE flows."""

	def _procurement_call(self, method, *args, _purchase_payload=None, **kwargs):
		persisted = None
		if self.doctype in ("Purchase Invoice", "Payment Entry") and self.name and not self.is_new():
			# Resource PUT merges incoming rows before save; incoming links must
			# not erase the already persisted procurement boundary. This read is
			# classification only, never exposed; scoped ACL/locks follow below.
			persisted = service._read(self.doctype, self.name)
		if not is_procurement(self) and not (persisted and is_procurement(persisted)):
			return method(*args, **kwargs)
		initial = deepcopy(self.as_dict())
		initial_flags, initial_action = dict(self.flags), getattr(self, "_action", None)
		result, attempts = {}, 0

		def write():
			nonlocal attempts
			if attempts:
				# A deadlock retries the complete native operation on a fresh
				# transaction, without keeping controller-mutated child rows.
				self.__init__(deepcopy(initial))
				self.flags.update(initial_flags)
				self._action = initial_action
			attempts += 1
			context = operation.current()
			old = None
			if self.name and not self.is_new() and method.__name__ != "insert":
				old = service._current(self.doctype, self.name)
				if self.docstatus == 2 and old.docstatus == 1:
					check_cancellation_facts(self, old)
					# All reliable old sources pass the same business ACL/locks.
					prepare_document(old)
				# Native cancellation unlinks intercompany headers with raw DB
				# updates; current post-hook state alone cannot prove Sales N/A.
				_stage(old, "Sales / persisted native associations", lambda: check_sales_dependencies(old))
			facts = snapshot(self)
			context.update(
				amount=facts["amount"],
				currency=facts["currency"],
				quantity=[row.get("qty") for row in self.get("items") or []],
			)
			prepare_document(self)
			_stage(self, "Sales / proposed native associations", lambda: check_sales_dependencies(self))
			prepare_auto_invoice(self)
			if self.name and not any(
				(row["doctype"], row["name"]) == (self.doctype, self.name) for row in context["documents"]
			):
				context["documents"].append({"doctype": self.doctype, "name": self.name})
			if self.name and not self.is_new() and method.__name__ != "insert":
				context["before"].setdefault(self.doctype + ":" + self.name, snapshot(old))
				if self.docstatus == 2 and old.docstatus == 1:
					context.setdefault("gl_before", {})[self.doctype + ":" + self.name] = _ledger(
						old, "GL Entry", is_cancelled=0
					)
					context.setdefault("payment_before", {})[self.doctype + ":" + self.name] = {
						ledger: _ledger(old, ledger, delinked=0)
						for ledger in ("Payment Ledger Entry", "Advance Payment Ledger Entry")
					}
			try:
				result["doc"] = method(*args, **kwargs)
				return {"document": snapshot(result["doc"])}
			finally:
				context["after"][self.doctype + ":" + str(self.name)] = snapshot(self)

		if operation.current() is not None:
			write()
		else:
			key = (
				self.flags.get("purchase_request_id")
				or frappe.flags.get("purchase_request_id")
				or str(uuid.uuid4())
			)
			# Unkeyed programmatic calls get a distinct invocation identity;
			# only a caller-provided key promises cross-request replay.
			outcome = operation.run(
				key,
				_purchase_payload
				or {"doc": business_payload(initial), "action": initial_action or method.__name__},
				write,
				_form_replay,
			)
			if "doc" not in result:
				result["doc"] = frappe.get_doc(outcome["document"]["doctype"], outcome["document"]["name"])
		return result["doc"]

	def insert(self, *args, **kwargs):
		from .purchase_repost_boundary import document_boundary

		with document_boundary(self, creating=True):
			return self._procurement_call(super().insert, *args, **kwargs)

	def _save(self, *args, **kwargs):
		from .purchase_repost_boundary import document_boundary

		with document_boundary(self):
			return self._procurement_call(super()._save, *args, **kwargs)

	def _procurement_hold_call(self, native, payload, fields=PI_HOLD_FIELDS):
		from .purchase_repost_boundary import _native_documents_action, execution

		with execution():
			persisted = service._read(self.doctype, self.name) if self.name and not self.is_new() else None
			if not is_procurement(self) and not (persisted and is_procurement(persisted)):
				return native()

			def preflight(actual):
				from .purchase_source_service import _write_fields

				actual.check_permission("write")
				_write_fields(actual.doctype, fields)

			def write():
				token = _native_hold.set(self)
				try:
					return native()
				finally:
					_native_hold.reset(token)

			return _native_documents_action((self,), write, payload, preflight=preflight, hold_only=True)

	def block_invoice(self, hold_comment=None, release_date=None):
		native = super().block_invoice
		return self._procurement_hold_call(
			lambda: native(hold_comment=hold_comment, release_date=release_date),
			{"action": "block_invoice", "hold_comment": hold_comment, "release_date": release_date},
		)

	def unblock_invoice(self):
		return self._procurement_hold_call(
			super().unblock_invoice, {"action": "unblock_invoice"}, {"on_hold", "release_date"}
		)

	def db_set(self, fieldname, *args, **kwargs):
		from .purchase_repost_boundary import POINTER, execution

		if fieldname == POINTER or (isinstance(fieldname, dict) and POINTER in fieldname):
			frappe.throw(_i18n("库存保护指针不能由普通单据写入、清除或替换"), frappe.PermissionError)
		fields = set(fieldname) if isinstance(fieldname, dict) else {fieldname}
		if self.doctype == "Purchase Invoice" and fields & PI_HOLD_FIELDS:
			early_commit = kwargs.get("commit") or (len(args) > 3 and args[3])
			if _native_hold.get() is self and fields <= PI_HOLD_FIELDS and not early_commit:
				return super().db_set(fieldname, *args, **kwargs)
			with execution():
				persisted = (
					service._read(self.doctype, self.name) if self.name and not self.is_new() else None
				)
				if is_procurement(self) or (persisted and is_procurement(persisted)):
					if not fields <= PI_HOLD_FIELDS:
						operation.reject(
							"冻结操作不能同时修改其他采购业务字段，请通过原生保存核对",
							"invoice_hold_mixed_fields",
						)
					if early_commit:
						operation.reject(
							"冻结操作不能在原生写入中提前提交，需完整核对后提交", "invoice_hold_early_commit"
						)
					native = super().db_set
					return self._procurement_hold_call(
						lambda: native(fieldname, *args, **kwargs),
						{
							"action": "change_release_date" if fields == {"release_date"} else "db_set_hold",
							"fields": fieldname,
							"values": args,
							"options": kwargs,
						},
						fields,
					)
		return super().db_set(fieldname, *args, **kwargs)


def check_cancellation_facts(doc, old):
	"""Native cancel skips validation: only cancellation state may be supplied."""
	from frappe.model.workflow import get_workflow_name

	ignored = {"docstatus", "modified", "modified_by"}
	workflow = get_workflow_name(doc.doctype)
	workflow_field = (
		frappe.get_cached_value("Workflow", workflow, "workflow_state_field") if workflow else None
	)

	def facts(source, *, root=False):
		values = source.as_dict(convert_dates_to_str=True, no_private_properties=True)
		for field in ignored | ({workflow_field} if root and workflow_field else set()):
			values.pop(field, None)
		for field in source.meta.get("fields") or []:
			if field.fieldtype == "Time" and field.fieldname in values:
				# Native Time reads are timedeltas; do not compare their
				# unpadded string representation with a padded client value.
				# Read raw values to retain durations/days and microseconds.
				raw = source.get(field.fieldname)
				if raw is None or raw == "":
					values[field.fieldname] = None
				else:
					duration = get_timedelta(raw)
					if duration is None:
						operation.reject(
							"采购取消包含非法原生时间事实", "cancellation_business_facts_changed"
						)
					values[field.fieldname] = duration
		for field in source.meta.get_table_fields():
			values[field.fieldname] = [facts(row) for row in source.get(field.fieldname) or []]
		return values

	if facts(doc, root=True) != facts(old, root=True):
		operation.reject(
			"采购取消不能同时改写来源、金额或核销事实，请刷新后单独取消",
			"cancellation_business_facts_changed",
		)


def resolve_source_documents(doc, *, reader=None, stock_entry_lifecycle=True):
	"""Read-only identity resolution shared with the cancellation scope collector.

	This does not preserve billing values, lock sources, register audit effects or
	run controller calculations. Callers still own those orchestration steps.
	"""
	read = reader or service._read
	orders, receipts, invoices = set(), set(), set()
	native_sources = native_source_identities(doc, reader=read, stock_entry_lifecycle=stock_entry_lifecycle)
	invoices.update(name for doctype, name in native_sources if doctype == "Purchase Invoice")
	for row in doc.get("items") or []:
		if row.get("purchase_order"):
			orders.add(row.purchase_order)
		if row.get("purchase_receipt"):
			receipts.add(row.purchase_receipt)
	for row in doc.get("references") or []:
		if row.reference_doctype == "Purchase Order":
			orders.add(row.reference_name)
		elif row.reference_doctype == "Purchase Invoice":
			invoices.add(row.reference_name)
			invoice = read("Purchase Invoice", row.reference_name)
			orders.update(item.purchase_order for item in invoice.items if item.get("purchase_order"))
			receipts.update(item.purchase_receipt for item in invoice.items if item.get("purchase_receipt"))
	for name in sorted(receipts):
		receipt = read("Purchase Receipt", name)
		orders.update(item.purchase_order for item in receipt.items if item.get("purchase_order"))
	identities = (
		native_sources
		| {("Purchase Order", name) for name in orders}
		| {("Purchase Receipt", name) for name in receipts}
		| {("Purchase Invoice", name) for name in invoices}
	)
	return identities, native_sources


def prepare_document(doc):
	"""Acquire MR -> PO -> PR -> PI source locks before native validation."""
	identities, native_sources = resolve_source_documents(doc)
	requests, orders, receipts, invoices = (
		{name for doctype, name in identities if doctype == target}
		for target in ("Material Request", "Purchase Order", "Purchase Receipt", "Purchase Invoice")
	)
	billing_transition = (
		doc.doctype == "Purchase Invoice"
		and doc.get("docstatus") in (1, 2)
		and (not doc.name or frappe.db.get_value(doc.doctype, doc.name, "docstatus") != doc.docstatus)
	)
	if billing_transition:
		from erpnext.stock.doctype.purchase_receipt.purchase_receipt import (
			get_purchase_receipts_against_po_details,
		)

		for name in sorted(orders):
			order = service._read("Purchase Order", name)
			receipts.update(
				row.parent
				for row in get_purchase_receipts_against_po_details([row.name for row in order.items])
			)
	for doctype, names in (
		("Material Request", requests),
		("Purchase Order", orders),
		("Purchase Receipt", receipts),
		("Purchase Invoice", invoices),
	):
		for name in sorted(names):
			source = service._current(doctype, name)
			if billing_transition and doctype in ("Purchase Order", "Purchase Receipt"):
				context = operation.current()
				key = doctype + ":" + name
				field = (
					"update_billed_amount_in_purchase_order"
					if doctype == "Purchase Order"
					else "update_billed_amount_in_purchase_receipt"
				)
				preserved = context.setdefault("billing_preserved", {})
				if doc.get("is_return") and not doc.get(field):
					preserved[key] = {
						"per_billed": source.per_billed,
						"items": {row.name: row.billed_amt for row in source.items},
					}
				else:
					preserved.pop(key, None)
	if native_sources:
		native_source_identities(doc, locked=True)  # revalidate current children under parent locks
	if doc.doctype in ("Purchase Receipt", "Purchase Invoice") and doc.get("docstatus") in (1, 2):
		_stage(doc, "Inventory / existing native stock reposts", lambda: check_pending_reposts(doc))


def _native_source_row(doc, row, source, detail, table, target_doctype, *, item_field="item_code", fields=()):
	"""Shared pure parent/detail proof for actual native updater identities."""
	service._require_fields(source.doctype, {"company", table})
	service._require_fields(target_doctype, {item_field, "stock_uom", *fields}, source.doctype)
	targets = [item for item in source.get(table) or [] if item.name == detail]
	target = targets[0] if len(targets) == 1 else None
	if (
		not target
		or target.doctype != target_doctype
		or target.get(item_field) != row.item_code
		or (target.get("stock_uom") != row.get("stock_uom"))
		or source.company != doc.company
		or source.docstatus != 1
		or (
			target.get("parent")
			and (
				target.parent != source.name
				or target.parenttype != source.doctype
				or target.parentfield != table
			)
		)
	):
		operation.reject("采购原生关联来源身份、公司或状态不一致", "native_source_identity_mismatch")
	return target


def _stock_entry_source_identities(doc, read, *, lifecycle):
	"""SELECT-only native MR, transit-SE and explicit old-PO supplied identities.

	StockEntry's updater relationships are not in its status_updater list. This
	follows actual pointers; no MR/transfer/supply/reservation calculations run.
	"""
	service._require_fields(
		"Stock Entry",
		{
			"company",
			"items",
			"purpose",
			"purchase_order",
			"supplier",
			"is_return",
			"outgoing_stock_entry",
			"add_to_transit",
			"subcontracting_inward_order",
		},
	)
	service._require_fields(
		"Stock Entry Detail",
		{
			"item_code",
			"stock_uom",
			"material_request",
			"material_request_item",
			"against_stock_entry",
			"ste_detail",
			"po_detail",
			"original_item",
			"subcontracted_item",
			"allow_alternative_item",
			"scio_detail",
		},
		"Stock Entry",
	)
	if doc.get("subcontracting_inward_order") or any(
		row.get("scio_detail") for row in doc.get("items") or []
	):
		operation.reject("委外入库来源尚无同步范围适配", "native_source_path_unsupported")
	identities = set()
	if lifecycle:
		service._require_fields(
			"Stock Entry",
			{
				"work_order",
				"job_card",
				"project",
				"asset_repair",
				"pick_list",
				"source_stock_entry",
				"inspection_required",
			},
		)
		service._require_fields("Stock Entry Detail", {"project", "quality_inspection"}, "Stock Entry")
		if any(
			doc.get(field)
			for field in (
				"work_order",
				"job_card",
				"project",
				"asset_repair",
				"pick_list",
				"source_stock_entry",
			)
		) or (
			any(
				row.get("project") or (doc.get("inspection_required") and row.get("quality_inspection"))
				for row in doc.get("items") or []
			)
			or doc.purpose in ("Send to Warehouse", "Receive at Warehouse")
		):
			operation.reject("库存单据存在尚未适配的原生外部写入或转换路径", "native_source_path_unsupported")

	def request(row, parent, detail):
		if not parent and not detail:
			return
		if not parent or not detail:
			operation.reject("原生物料请求父单据与明细身份不完整", "native_source_identity_missing")
		source = read("Material Request", parent)
		_native_source_row(doc, row, source, detail, "items", "Material Request Item")
		identities.add((source.doctype, source.name))

	outgoing = None
	if doc.get("outgoing_stock_entry"):
		if doc.purpose != "Material Transfer" or doc.outgoing_stock_entry == doc.name:
			operation.reject("未支持的库存中转来源路径", "native_source_path_unsupported")
		outgoing = read("Stock Entry", doc.outgoing_stock_entry)
		service._require_fields("Stock Entry", {"company", "items", "purpose", "add_to_transit"})
		if (
			outgoing.company != doc.company
			or outgoing.docstatus != 1
			or outgoing.purpose != "Material Transfer"
		):
			operation.reject("库存中转来源公司或状态不一致", "native_source_identity_mismatch")
		identities.add((outgoing.doctype, outgoing.name))
	for row in doc.get("items") or []:
		direct = (row.get("material_request"), row.get("material_request_item"))
		request(row, *direct)
		if outgoing:
			if row.get("against_stock_entry") != outgoing.name or not row.get("ste_detail"):
				operation.reject("库存中转来源父单据与明细错配", "native_source_identity_missing")
			target = _native_source_row(
				doc,
				row,
				outgoing,
				row.ste_detail,
				"items",
				"Stock Entry Detail",
				fields={"material_request", "material_request_item", "transferred_qty"},
			)
			inherited = (target.get("material_request"), target.get("material_request_item"))
			request(row, *inherited)
			if any(direct) and any(inherited) and direct != inherited:
				operation.reject("库存中转物料请求来源冲突", "native_source_identity_mismatch")
		elif row.get("against_stock_entry") or row.get("ste_detail"):
			operation.reject("库存中转来源缺少真实父库存单据", "native_source_identity_missing")
	if doc.get("purchase_order"):
		source = read("Purchase Order", doc.purchase_order)
		service._require_fields(
			"Purchase Order", {"company", "supplier", "items", "supplied_items", "is_old_subcontracting_flow"}
		)
		service._require_fields("Purchase Order Item", {"job_card"}, "Purchase Order")
		if (
			source.company != doc.company
			or source.docstatus != 1
			or not source.get("is_old_subcontracting_flow")
			or (doc.get("supplier") and doc.supplier != source.supplier)
		):
			operation.reject("原生旧委外采购来源公司、供应商或状态不一致", "native_source_identity_mismatch")
		if (
			lifecycle
			and doc.purpose == "Send to Subcontractor"
			and any(row.get("job_card") for row in source.items)
		) or not (doc.purpose in ("Send to Subcontractor", "Material Transfer") or doc.get("is_return")):
			operation.reject("旧委外工单或库存来源路径尚无同步范围适配", "native_source_path_unsupported")
		for row in doc.get("items") or []:
			if not row.get("po_detail") or row.get("original_item") or row.get("allow_alternative_item"):
				operation.reject(
					"旧委外供料需明确的已核查原生明细，不支持自动补链或替代物料",
					"native_source_path_unsupported",
				)
			target = _native_source_row(
				doc,
				row,
				source,
				row.po_detail,
				"supplied_items",
				"Purchase Order Item Supplied",
				item_field="rm_item_code",
				fields={"main_item_code", "reserve_warehouse"},
			)
			item = service._read("Item", target.rm_item_code, {"stock_uom"})
			if (
				item.stock_uom != row.get("stock_uom")
				or not target.get("main_item_code")
				or (row.get("subcontracted_item") != target.main_item_code)
				or not target.get("reserve_warehouse")
			):
				operation.reject("旧委外供料物料、单位或主物料身份不一致", "native_source_identity_mismatch")
		identities.add((source.doctype, source.name))
	return identities


def native_source_identities(doc, *, locked=False, reader=None, stock_entry_lifecycle=True):
	"""Resolve actual native updater targets; do not reproduce its update math."""
	identities = set()
	read = reader or (service._current if locked else service._read)
	if doc.doctype == "Stock Entry":
		return _stock_entry_source_identities(doc, read, lifecycle=stock_entry_lifecycle)
	for mapping in getattr(doc, "status_updater", []):
		if (doc.doctype, mapping.get("target_parent_dt")) not in (
			("Purchase Order", "Material Request"),
			("Purchase Receipt", "Material Request"),
			("Purchase Receipt", "Purchase Invoice"),
		):
			continue
		for row in doc.get("items") or []:
			parent, detail = row.get(mapping["percent_join_field"]), row.get(mapping["join_field"])
			if not parent and not detail:
				continue
			if not parent or not detail:
				operation.reject("采购原生关联来源父单据与明细身份不完整", "native_source_identity_missing")
			source = read(mapping["target_parent_dt"], parent)
			_native_source_row(
				doc, row, source, detail, "items", mapping["target_dt"], fields={mapping["target_field"]}
			)
			identities.add((source.doctype, source.name))
	return identities


def acknowledge_native_sources(doc):
	for identity in sorted(native_source_identities(doc, locked=True)):
		acknowledge_effect(service._current(*identity))
