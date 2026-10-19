"""Accepted native procurement reversals, on the existing operation IR only.

Native RIV owns scheduling, partial commits and valuation. This service owns the
acceptance scope, protected progress and the final read-only business proof.
"""

from __future__ import annotations

from frappe import _ as _i18n


import json
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from datetime import UTC, datetime, timezone

import frappe

from . import purchase_operation as operation
from . import purchase_repost_boundary as boundary

STAGES = ("waiting_inventory", "recalculating", "verifying", "completed", "failed")
_accepting = ContextVar("purchase_reversal_acceptance", default=None)
_executing = ContextVar("purchase_reversal_execution", default=None)
_mutation = ContextVar("purchase_reversal_mutation", default=frozenset())
MAX_BYTES = 4 * 1024 * 1024
SLE_INPUTS = [
	"name",
	"company",
	"item_code",
	"warehouse",
	"voucher_type",
	"voucher_no",
	"voucher_detail_no",
	"actual_qty",
	"is_cancelled",
	"posting_datetime",
	"creation",
]


def _reject(reason="purchase_reversal_evidence_invalid"):
	operation.reject("采购冲销证据尚未完成，请核对处理进度", reason)


def _permission(doc, ptype):
	if not frappe.has_permission(doc.doctype, ptype, doc=doc):
		operation.reject(
			"采购冲销来源权限已改变，请核对权限", "native_permission_denied", frappe.PermissionError
		)


def accepting():
	return _accepting.get()


def owner():
	return _executing.get()


def allows(operation_id):
	active = owner()
	return bool(active and active["operation_id"] == operation_id)


@contextmanager
def mutation(name):
	token = _mutation.set(_mutation.get() | {name})
	try:
		yield
	finally:
		_mutation.reset(token)


def guard_audit(doc, proposed=None):
	values = dict(doc.as_dict() if callable(getattr(doc, "as_dict", None)) else doc)
	values.update(proposed or {})
	old_service = (
		frappe.db.get_value("Integration Request", doc.name, "integration_request_service")
		if doc.name
		else None
	)
	if operation.SERVICE in (
		old_service,
		doc.get("integration_request_service"),
		values.get("integration_request_service"),
	):
		current = operation.current()
		if doc.name not in _mutation.get() and not (current and current["operation_id"] == doc.name):
			frappe.throw(_i18n("采购操作审计不能由普通操作创建或改写"), frappe.PermissionError)


def _load(name, *, lock=True):
	"""Workflow execution locks current IR; UI projections use a plain read."""
	rows = frappe.db.get_values(
		"Integration Request",
		{"name": name},
		[
			"name",
			"status",
			"integration_request_service",
			"data",
			"output",
			"reference_doctype",
			"reference_docname",
		],
		as_dict=True,
		for_update=lock,
	)
	if len(rows) != 1 or rows[0].integration_request_service != operation.SERVICE:
		_reject()
	row = rows[0]
	if any(not isinstance(value, str) or len(value.encode()) > MAX_BYTES for value in (row.data, row.output)):
		_reject()
	context, progress = json.loads(row.data), json.loads(row.output)
	manifest = context.get("reversal")
	if (
		not isinstance(manifest, dict)
		or manifest.get("schema") != 1
		or manifest.get("site") != frappe.local.site
		or (
			manifest.get("database") != frappe.db.cur_db_name
			or progress.get("generation") != manifest.get("generation")
		)
	):
		_reject()
	if progress.get("stage") not in STAGES or (row.status == "Completed") != (
		progress["stage"] == "completed"
	):
		_reject()
	return row, context, progress


def keys(manifest):
	scope = manifest["scope"]
	identities = {(row["doctype"], row["name"]) for row in scope["sources"] + scope["vouchers"]}
	identities.add((manifest["source"]["doctype"], manifest["source"]["name"]))
	return {boundary.lock_key("document", *identity) for identity in identities} | {
		boundary.lock_key("pair", scope["company"], pair["item_code"], pair["warehouse"])
		for pair in scope["pairs"]
	}


@contextmanager
def acceptance(payload):
	"""Only the server-owned native submitted→cancelled form call enters here."""
	from . import purchase_consistency as consistency
	from . import purchase_native_intent, purchase_native_repost
	from . import purchase_payment_service as service
	from . import purchase_reversal_scope as scope

	context = operation.current()
	if not context or payload.get("action") != "Cancel":
		yield
		return
	supplied = payload["doc"]
	with boundary.acquire((boundary.fence_key(),)), service.current_reads():
		source = service._read(supplied["doctype"], supplied["name"])
		_permission(source, "read")
		_permission(source, "cancel")
		if source.docstatus != 1 or supplied.get("modified") != str(source.modified):
			_reject("purchase_reversal_source_changed")
		incoming = frappe.get_doc(supplied)
		consistency.check_cancellation_facts(incoming, source)
		footprint = scope.collect_cancellation_scope(source)
		manifest = {
			"schema": 1,
			"site": frappe.local.site,
			"database": frappe.db.cur_db_name,
			"generation": uuid.uuid4().hex,
			"source": {"doctype": source.doctype, "name": source.name},
			"scope": asdict(footprint),
			"roots": [],
			"source_modified": str(source.modified),
		}
		with boundary.acquire(keys(manifest)):
			current = service._current(source.doctype, source.name)
			_permission(current, "read")
			_permission(current, "cancel")
			if str(current.modified) != str(source.modified) or current.docstatus != 1:
				_reject("purchase_reversal_source_changed")
			fresh = scope.collect_cancellation_scope(current)
			if asdict(fresh) != asdict(footprint):
				_reject("purchase_reversal_scope_changed")
			boundary._check_pending((fresh,), (current,))
			consistency.check_pending_reposts(
				current, pairs={(pair.item_code, pair.warehouse) for pair in fresh.pairs}
			)
			purchase_native_intent.reject_intersection(
				tuple((pair.item_code, pair.warehouse) for pair in fresh.pairs)
			)
			purchase_native_repost.install()
			context["reversal"] = manifest
			token = _accepting.set(context)
			try:
				yield
			finally:
				_accepting.reset(token)
				context.pop("_native_created_rivs", None)
			if not manifest["roots"]:
				context.pop("reversal", None)  # no RIV: ordinary synchronous receipt


def capture_root(doc):
	context = accepting()
	if context is None:
		return False
	manifest = context["reversal"]
	pairs = {(pair["item_code"], pair["warehouse"]) for pair in manifest["scope"]["pairs"]}
	vouchers = {(row["doctype"], row["name"]) for row in manifest["scope"]["vouchers"]}
	if (
		doc.docstatus != 1
		or doc.status not in ("Queued", "In Progress", "Completed")
		or doc.company != manifest["scope"]["company"]
	):
		_reject("purchase_reversal_root_invalid")
	if doc.based_on == "Item and Warehouse":
		if (doc.item_code, doc.warehouse) not in pairs:
			_reject("purchase_reversal_root_outside_scope")
	elif doc.based_on != "Transaction" or (doc.voucher_type, doc.voucher_no) not in vouchers:
		_reject("purchase_reversal_root_outside_scope")
	fields = (
		"name",
		"based_on",
		"company",
		"item_code",
		"warehouse",
		"voucher_type",
		"voucher_no",
		"posting_date",
		"posting_time",
		"repost_only_accounting_ledgers",
		"via_landed_cost_voucher",
		"recreate_stock_ledgers",
		"recalculate_valuation_rate",
	)
	manifest["roots"].append({field: doc.get(field) for field in fields})
	return True


def owned_pending(name):
	context = operation.current()
	if context is None:
		from .purchase_consistency import _state

		state = _state()
		context = state["context"] if state else None
	return bool(
		context and any(root["name"] == name for root in context.get("reversal", {}).get("roots", []))
	)


def accept_audit(context, result):
	"""Called after synchronous native/finance checks, still the acceptance tx."""
	manifest = context["reversal"]
	source = frappe.get_doc(manifest["source"]["doctype"], manifest["source"]["name"], for_update=True)
	if source.docstatus != 2:
		_reject("purchase_reversal_source_not_cancelled")
	manifest["cancelled_modified"] = str(source.modified)
	scope = manifest["scope"]
	manifest["sles"] = []
	for pair, anchor in scope["anchors"]:
		rows = frappe.db.get_values(
			"Stock Ledger Entry",
			{
				"item_code": pair["item_code"],
				"warehouse": pair["warehouse"],
				"posting_datetime": [">=", anchor],
			},
			SLE_INPUTS,
			as_dict=True,
			for_update=True,
			limit=50001,
		)
		manifest["sles"].extend(dict(row) for row in rows)
	if len(manifest["sles"]) > 50000:
		_reject("purchase_reversal_scope_oversize")
	pointers = {
		(row["doctype"], row["name"])
		for row in scope["sources"] + scope["vouchers"]
		if row["doctype"] in boundary.PENDING_TYPES
	}
	pointers.add((source.doctype, source.name))
	for pair in scope["pairs"]:
		names = frappe.db.get_values("Bin", pair, "name", for_update=True)
		if len(names) != 1:
			_reject("purchase_reversal_bin_missing")
		pointers.add(("Bin", names[0][0]))
	manifest["pointers"] = [list(identity) for identity in sorted(pointers)]
	for doctype, name in manifest["pointers"] + [
		["Repost Item Valuation", root["name"]] for root in manifest["roots"]
	]:
		if frappe.db.get_value(doctype, name, boundary.POINTER, for_update=True):
			_reject("purchase_reversal_owner_conflict")
		frappe.db.set_value(doctype, name, boundary.POINTER, context["operation_id"], update_modified=False)
	progress = {
		"stage": "waiting_inventory",
		"generation": manifest["generation"],
		"safe_reason": None,
		"tasks": {},
		"receipts": [],
		"document": result["document"],
		"doctype": source.doctype,
		"name": source.name,
		"permission": "cancel",
	}
	_write(context, progress)
	result["reversal"] = public(context, progress)
	frappe.db.after_commit.add(lambda: notify(context["operation_id"]))


def _write(context, progress):
	events = progress.setdefault("stages", [])
	if not events or events[-1]["stage"] != progress["stage"]:
		events.append(
			{
				"stage": progress["stage"],
				"time": datetime.now(UTC).isoformat(),
				"safe_reason": progress.get("safe_reason"),
			}
		)
		progress["stages"] = events[-32:]
		operation.runtime_log(context, "reversal_" + progress["stage"] + "_pending_commit")
	data, output = operation.encode(context), operation.encode(progress)
	if max(len(data.encode()), len(output.encode())) > MAX_BYTES:
		_reject("purchase_reversal_evidence_oversize")
	with mutation(context["operation_id"]):
		frappe.db.set_value(
			"Integration Request",
			context["operation_id"],
			{
				"status": "Completed"
				if progress["stage"] == "completed"
				else "Failed"
				if progress["stage"] == "failed"
				else "Queued",
				"data": data,
				"output": output,
				"reference_doctype": context["reversal"]["source"]["doctype"],
				"reference_docname": context["reversal"]["source"]["name"],
			},
			update_modified=False,
		)


def notify(name):
	try:
		frappe.publish_realtime("purchase_reversal_progress", {"operation_id": name}, after_commit=False)
	except Exception:
		frappe.logger("purchase_operation", allow_site=True).error(
			operation.encode({"operation_id": name, "event": "progress_notification_failed"})
		)


def public(context, progress):
	source = frappe.get_doc(context["reversal"]["source"]["doctype"], context["reversal"]["source"]["name"])
	_permission(source, "read")
	return {
		"operation_id": context["operation_id"],
		"stage": progress["stage"],
		"safe_reason": progress.get("safe_reason"),
		"can_retry": progress["stage"] == "failed"
		and frappe.has_permission(source.doctype, "cancel", doc=source),
	}


@frappe.whitelist()
@boundary.procurement_entry
def get_progress(doctype, name):
	if doctype not in boundary.PENDING_TYPES:
		_reject("purchase_reversal_source_invalid")
	source = frappe.get_doc(doctype, name, for_update=False)
	_permission(source, "read")
	operation_id = source.get(boundary.POINTER)
	if not operation_id:
		rows = frappe.db.get_values(
			"Integration Request",
			{
				"integration_request_service": operation.SERVICE,
				"reference_doctype": doctype,
				"reference_docname": name,
			},
			["name", "output"],
			as_dict=True,
			order_by="creation desc",
			limit=1,
		)
		if not rows or not json.loads(rows[0].output or "{}").get("generation"):
			return None
		operation_id = rows[0].name
	_row, context, progress = _load(operation_id, lock=False)
	if (
		context["reversal"]["source"] != {"doctype": doctype, "name": name}
		and [doctype, name] not in context["reversal"]["pointers"]
	):
		_reject()
	return public(context, progress)


def projection(doc):
	"""Derived UI status; current source ACL owns visibility, never IR read ACL."""
	if doc.docstatus != 2 and not doc.get(boundary.POINTER):
		return None
	try:
		return get_progress(doc.doctype, doc.name)
	except (frappe.PermissionError, frappe.DoesNotExistError):
		return (
			{"stage": "waiting_inventory", "safe_reason": "progress_unavailable", "can_retry": False}
			if doc.get(boundary.POINTER)
			else None
		)


def replay(row):
	_, context, progress = _load(row.name, lock=True)
	source = frappe.get_doc(context["reversal"]["source"]["doctype"], context["reversal"]["source"]["name"])
	_permission(source, "read")
	_permission(source, "cancel")
	from frappe.desk.form.save import send_updated_docs

	send_updated_docs(source)
	from .purchase_consistency import snapshot

	return {"document": snapshot(source), "reversal": public(context, progress)}


def readonly_verify(context, progress):
	from . import purchase_consistency as consistency
	from . import purchase_payment_service as service

	manifest = context["reversal"]
	source = service._current(manifest["source"]["doctype"], manifest["source"]["name"])
	if source.docstatus != 2 or str(source.modified) != manifest["cancelled_modified"]:
		_reject("purchase_reversal_source_changed")
	# Reuse the existing pure stock/received/billing/AP checks. Finance creation
	# and review invalidation deliberately remain acceptance-only operations.
	consistency.check_stock(source)
	consistency.check_order_received(source)
	consistency.check_billing(source, context=context)
	consistency.check_cancelled_ledgers(source, context=context)
	consistency.verify_cancellation(source, context["gl_before"][source.doctype + ":" + source.name])
	from .purchase_native_repost import verify_gl_coverage, verify_stock_chain

	verify_stock_chain(manifest)
	invoice_plans = verify_gl_coverage(context, progress)
	from erpnext.accounts.general_ledger import process_gl_map

	for identity in sorted(
		{
			(row["doctype"], row["name"])
			for row in manifest["scope"]["sources"] + manifest["scope"]["vouchers"]
		}
	):
		doc = service._current(*identity)
		if doc.doctype == "Purchase Invoice" and doc.docstatus == 1:
			plan = invoice_plans.get(identity)
			if plan is None:
				plan = process_gl_map(doc.get_gl_entries())
			consistency.check_invoice_balance(doc, gl_plan=plan)
	return {
		"source": manifest["source"],
		"sles": len(manifest["sles"]),
		"pairs": len(manifest["scope"]["pairs"]),
		"tasks": sorted(progress["tasks"]),
		"checked_at": datetime.now(UTC).isoformat(),
	}


def finalize(context, progress):
	manifest = context["reversal"]
	tasks = frappe.db.get_values(
		"Repost Item Valuation",
		{boundary.POINTER: context["operation_id"]},
		["name", "status", "docstatus"],
		as_dict=True,
		for_update=True,
	)
	if not {root["name"] for root in manifest["roots"]} <= {row.name for row in tasks}:
		_reject("purchase_reversal_root_missing")
	if any(row.docstatus != 1 or row.status in ("Failed", "Cancelled") for row in tasks):
		_reject("purchase_reversal_task_failed")
	if any(row.status not in ("Completed", "Skipped") for row in tasks):
		return False
	if {row.name for row in tasks} != set(progress["tasks"]):
		_reject("purchase_reversal_receipt_missing")
	progress["stage"] = "verifying"
	_write(context, progress)
	proof = operation.trace(
		context, "Inventory / readonly final reversal proof", lambda: readonly_verify(context, progress)
	)
	for doctype, name in manifest["pointers"]:
		if frappe.db.get_value(doctype, name, boundary.POINTER, for_update=True) != context["operation_id"]:
			_reject("purchase_reversal_owner_changed")
		frappe.db.set_value(doctype, name, boundary.POINTER, None, update_modified=False)
	progress.update(stage="completed", safe_reason=None, proof=proof)
	_write(context, progress)
	frappe.db.after_commit.add(lambda: notify(context["operation_id"]))
	return True


@frappe.whitelist(methods=["POST"])
@boundary.procurement_entry
def retry(doctype, name):
	if doctype not in boundary.PENDING_TYPES:
		_reject("purchase_reversal_source_invalid")
	source = frappe.get_doc(doctype, name)
	_permission(source, "read")
	_permission(source, "cancel")
	operation_id = source.get(boundary.POINTER)
	if not operation_id:
		_reject("purchase_reversal_not_pending")
	with boundary.acquire((boundary.fence_key(),)):
		_row, context, progress = _load(operation_id, lock=True)
		with boundary.acquire(keys(context["reversal"])):
			root = context["reversal"]["source"]
			if (
				root != {"doctype": doctype, "name": name}
				and [doctype, name] not in context["reversal"]["pointers"]
			):
				_reject()
			current = frappe.get_doc(root["doctype"], root["name"], for_update=True)
			_permission(current, "read")
			_permission(current, "cancel")
			if progress["stage"] != "failed":
				return public(context, progress)
			for task in frappe.db.get_values(
				"Repost Item Valuation",
				{boundary.POINTER: operation_id},
				["name", "status"],
				as_dict=True,
				for_update=True,
			):
				task_evidence = progress["tasks"].get(task.name, {})
				covered = {tuple(value["voucher"]) for value in task_evidence.get("coverage", {}).values()}
				missing = {tuple(value) for value in task_evidence.get("expected", [])} - covered
				if task.status == "Failed" or (task.status in ("Completed", "Skipped") and missing):
					frappe.db.set_value(
						"Repost Item Valuation", task.name, "status", "Queued", update_modified=False
					)
			progress.update(stage="waiting_inventory", safe_reason=None)
			_write(context, progress)
			return public(context, progress)


def recover():
	"""Drive accepted generations only, including sites with scheduler disabled.

	Reuse audited native valuation and its timeslot/dependency rules. Do not
	invoke the global native selector or deduplication: they also touch legacy,
	unaccepted RIVs. The existing fixed timer runs this method on the main site.
	"""
	from .dedicated_source_sync import check_deadline
	from .purchase_native_repost import install, reconcile

	rows = frappe.db.get_values(
		"Integration Request",
		{"integration_request_service": operation.SERVICE, "status": ["in", ["Queued", "Failed"]]},
		["name", "output"],
		as_dict=True,
		limit=5001,
	)
	if len(rows) > 5000:
		_reject("purchase_reversal_scope_oversize")
	for row in rows:
		check_deadline()
		if not json.loads(row.output or "{}").get("generation"):
			continue
		_, _, current = _load(row.name, lock=False)
		if current["stage"] in ("failed", "completed"):
			continue
		riv = install()
		if riv.in_configured_timeslot():
			tasks = frappe.db.get_values(
				"Repost Item Valuation",
				{boundary.POINTER: row.name, "docstatus": 1, "status": ["in", ["Queued", "In Progress"]]},
				["name"],
				as_dict=True,
				order_by="posting_date asc, posting_time asc, creation asc",
				limit=5001,
			)
			if len(tasks) > 5000:
				_reject("purchase_reversal_scope_oversize")
			for task in tasks:
				check_deadline()
				doc = frappe.get_doc("Repost Item Valuation", task.name)
				riv.repost(doc)  # The locked adapter owns current task/dependency eligibility.
				check_deadline()
		reconcile(row.name)
