"""Thin adapters on native RIV repost and its physical-commit witnesses.

The original functions, queue, recovery policy and stock/GL algorithms execute
unchanged. Captured aliases keep their identity through audited trampolines.
"""

from __future__ import annotations

from frappe import _ as _i18n


import hashlib
import json
import re
import sys
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from threading import RLock
from types import FunctionType
from typing import TYPE_CHECKING

import frappe
from frappe.utils import flt

from . import purchase_operation as operation
from . import purchase_repost_boundary as boundary
from . import purchase_reversal_progress as progress

_install_lock = RLock()
_compiled = {}
PINS = {
	"riv": "c4449547d07c76fd316a5b2185d4c9b60bd42e8747767fe28aea28cbc2ceafe1",
	"stock": "8e6f465d14b40647895137572f433ba214bc5dcb2df8471f77ba7f8da0ae99ee",
	"accounts": "bcb051040ed4e6d213aeafa78cf69eef3034b9a7157499a3047297cbab6cb4b8",
	# ERPNext b0d56f2f48: company-scoped reversal freeze check; adapter targets unchanged.
	"gl": "e66d7550cac63afab319823c53cbec8f9fc40d9d55097bf35489902a7ebbed52",
}


def _repost_trampoline(doc):
	return _dlp_reversal_repost(doc)


def _gl_trampoline(doc):
	return _dlp_reversal_gl(doc)


def _toggle_trampoline(gl_map):
	return _dlp_reversal_toggle(gl_map)


def _file_trampoline(data, doc, file_name=None):
	return _dlp_reversal_file(data, doc, file_name)


def _remove_trampoline(docname):
	return _dlp_reversal_remove(docname)


def _clear_trampoline(days=None):
	return _dlp_reversal_clear(days)


def _bulk_trampoline(names):
	return _dlp_reversal_bulk(names)


def install():
	from erpnext.accounts import general_ledger as gl
	from erpnext.accounts import utils as accounts
	from erpnext.stock import stock_ledger as stock
	from erpnext.stock.doctype.repost_item_valuation import repost_item_valuation as riv

	from .purchase_native_intent import _audited_function, _code_named

	modules = {"riv": riv, "stock": stock, "accounts": accounts, "gl": gl}
	with _install_lock:
		codes = {}
		for key, module in modules.items():
			source = Path(module.__file__).read_bytes()
			if hashlib.sha256(source).hexdigest() != PINS[key]:
				progress._reject("purchase_reversal_native_version_changed")
			if key not in _compiled:
				_compiled[key] = compile(source, module.__file__, "exec", dont_inherit=True)
			codes[key] = _compiled[key]
		entries = (
			(riv, riv, "repost", _repost_trampoline, repost, None),
			(riv, riv, "repost_gl_entries", _gl_trampoline, repost_gl, None),
			(gl, gl, "toggle_debit_credit_if_negative", _toggle_trampoline, toggle, None),
			(stock, stock, "create_json_gz_file", _file_trampoline, checkpoint, (None,)),
			(riv, riv, "remove_attached_file", _remove_trampoline, remove_file, None),
			(riv, riv.RepostItemValuation, "clear_old_logs", _clear_trampoline, clear_old_logs, (None,)),
			(riv, riv, "bulk_restart_reposting", _bulk_trampoline, bulk_restart, None),
		)
		for module, target, name, trampoline, adapter, defaults in entries:
			key = next(key for key, value in modules.items() if value is module)
			native = getattr(target, name)
			if name == "bulk_restart_reposting":
				from frappe.utils.typing_validations import validate_argument_types

				wrapped = getattr(native, "__wrapped__", None)
				reference = validate_argument_types(lambda names: None)
				if (
					not isinstance(native, FunctionType)
					or native.__code__ != reference.__code__
					or native.__globals__ is not reference.__globals__
					or native.__closure__ is None
					or not any(cell.cell_contents is wrapped for cell in native.__closure__)
				):
					progress._reject("purchase_reversal_native_callable_changed")
				native = wrapped
			attribute = "_dlp_reversal_original_" + name
			original = getattr(module, attribute, None)
			expected = _code_named(codes[key], name)
			if original is None:
				if not _audited_function(native, expected, module.__dict__, defaults):
					progress._reject("purchase_reversal_native_callable_changed")
				original = FunctionType(
					native.__code__, native.__globals__, native.__name__, native.__defaults__
				)
				setattr(module, attribute, original)
				dispatcher = trampoline.__code__.co_names[0]
				setattr(module, dispatcher, adapter)
				native.__code__ = trampoline.__code__
			elif (
				not _audited_function(native, trampoline.__code__, module.__dict__, defaults)
				or not _audited_function(original, expected, module.__dict__, defaults)
				or getattr(module, trampoline.__code__.co_names[0], None) is not adapter
			):
				progress._reject("purchase_reversal_native_callable_changed")
		return riv


def current_query(query):
	"""Only this accepted execution's actual native read projections become current."""
	if progress.owner() is None:
		return query
	sql = str(query).strip()
	if (
		re.match(r"SELECT\b", sql, re.I)
		and not re.search(r"\bFOR\s+UPDATE\b|\bLOCK\s+IN\s+SHARE", sql, re.I)
		and re.search(
			r"`tab(?:Stock Ledger Entry|GL Entry|Payment Ledger Entry|Advance Payment Ledger Entry|Purchase Receipt(?: Item)?|Purchase Invoice(?: Item)?|Stock Entry(?: Detail)?|Bin)`",
			sql,
		)
	):
		return sql.rstrip(";") + " FOR UPDATE"
	return query


def guard_task(doc, *, destructive=False):
	operation_id = (
		frappe.db.get_value("Repost Item Valuation", doc.name, boundary.POINTER, for_update=True)
		if doc.name
		else None
	)
	if operation_id and (destructive or not progress.allows(operation_id)):
		frappe.throw(_i18n("已受理采购冲销的原生任务不能由普通操作重置或改写"), frappe.PermissionError)


def protect_task(doc, method=None):
	guard_task(doc, destructive=True)


def protect_checkpoint(doc, method=None):
	fields = ["attached_to_doctype", "attached_to_name", "attached_to_field", "file_url"]
	old = frappe.db.get_value("File", doc.name, fields, as_dict=True) if doc.name else None
	references = [doc, old] if old else [doc]
	# The native adapter always creates a new immutable checkpoint file. Its
	# own old/new associations may safely deduplicate identical bytes or drop
	# its own alias; native File preserves other rows' shared physical data.
	if progress.owner() and all(
		row.get("attached_to_doctype") == "Repost Item Valuation"
		and row.get("attached_to_field") == "reposting_data_file"
		and progress.allows(
			frappe.db.get_value("Repost Item Valuation", row.get("attached_to_name"), boundary.POINTER)
		)
		for row in references
	):
		return
	urls = {row.get("file_url") for row in references if row.get("file_url")}
	if urls:
		references += frappe.db.get_values(
			"File",
			{
				"file_url": ["in", sorted(urls)],
				"attached_to_doctype": "Repost Item Valuation",
				"attached_to_field": "reposting_data_file",
			},
			fields,
			as_dict=True,
		)
	tasks = {
		row.get("attached_to_name")
		for row in references
		if row.get("attached_to_doctype") == "Repost Item Valuation"
		and row.get("attached_to_field") == "reposting_data_file"
		and row.get("attached_to_name")
	}
	for name in sorted(tasks):
		operation_id = frappe.db.get_value("Repost Item Valuation", name, boundary.POINTER)
		if operation_id and not progress.allows(operation_id):
			_, _, output = progress._load(operation_id, lock=False)
			if method != "on_trash" or output["stage"] != "completed":
				progress._reject("purchase_reversal_evidence_pending")


class CheckpointFileBoundary:
	"""Guard before native File moves/deletes bytes, not its post-native hook."""

	def validate(self):
		protect_checkpoint(self, "validate")
		return super().validate()

	def on_trash(self):
		protect_checkpoint(self, "on_trash")
		return super().on_trash()


def _task_fields(doc):
	return json.loads(
		operation.encode(
			{
				field: doc.get(field)
				for field in (
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
			}
		)
	)


def _owned_creation(doc):
	"""Private accepted call identities; no client flags or actor exception."""
	context = progress.accepting()
	if context is not None:
		manifest = context["reversal"]
		# Native new_doc can carry the user's default company; validate later
		# replaces it from the actual pair/source. Those accepted identities
		# prove company here, and capture_root checks the persisted result.
		valid = (
			doc.based_on == "Item and Warehouse"
			and (doc.item_code, doc.warehouse)
			in {(row["item_code"], row["warehouse"]) for row in manifest["scope"]["pairs"]}
		) or (
			doc.based_on == "Transaction"
			and (doc.voucher_type, doc.voucher_no)
			in {(row["doctype"], row["name"]) for row in manifest["scope"]["vouchers"]}
		)
		if not valid:
			progress._reject("purchase_reversal_root_outside_scope")
		return True
	owner = progress.owner()
	if owner is not None:
		if not (
			doc.repost_only_accounting_ledgers
			and doc.reposting_reference == owner["task"].name
			and (doc.voucher_type, doc.voucher_no) in {tuple(value) for value in owner["expected"]}
			and doc.company == owner["task"].company
		):
			progress._reject("purchase_reversal_child_outside_scope")
		return True
	return False


def _task_scopes(doc):
	from . import purchase_payment_service as service
	from .purchase_reversal_scope import collect_repost_pair_scope

	if doc.based_on == "Item and Warehouse":
		service._read("Item", doc.item_code, {"stock_uom", "is_stock_item"})
		warehouse = service._read("Warehouse", doc.warehouse, {"company", "is_group", "disabled"})
		if warehouse.company != doc.company or warehouse.is_group or warehouse.disabled:
			progress._reject("purchase_reversal_scope_invalid")
		return (collect_repost_pair_scope(doc),), ()
	if doc.based_on != "Transaction" or not doc.voucher_type or not doc.voucher_no:
		progress._reject("purchase_reversal_scope_invalid")
	source = service._read(doc.voucher_type, doc.voucher_no)
	return tuple(boundary._scopes((source,), current=True, opaque=False)), (source,)


@contextmanager
def task_boundary(doc, *, creating=False):
	"""RIV publication/save/restart shares native writers' current scope leases."""
	from . import purchase_payment_service as service

	with boundary.execution():
		old = boundary._persisted(doc)
		creating = boundary._creates_identity(doc, inserting=creating)
		boundary.check_pointer(doc, old, creating=creating)
		active = progress.accepting() or progress.owner()
		created = active.setdefault("_native_created_rivs", {}) if active is not None else {}
		if (creating or doc.name in created) and _owned_creation(doc):
			if not creating and _task_fields(doc) != created[doc.name]:
				progress._reject("purchase_reversal_task_changed")
			yield  # actual accepted roots/children already hold the complete leases
			if creating:
				created[doc.name] = _task_fields(doc)
			return
		operation_id = old.get(boundary.POINTER) if old else None
		if operation_id:
			guard_task(doc)
			if _task_fields(doc) != _task_fields(old):
				progress._reject("purchase_reversal_task_changed")
			yield  # native owned writer acquired/reread its generation earlier
			return
		boundary.require_publication_order()
		with boundary.acquire((boundary.fence_key(),)), service.current_reads():
			if doc.based_on == "Item and Warehouse":
				doc.company = service._read("Warehouse", doc.warehouse, {"company"}).company
			elif doc.based_on == "Transaction" and doc.voucher_type and doc.voucher_no:
				doc.company = service._read(doc.voucher_type, doc.voucher_no, {"company"}).company
			roots = (doc, old) if old else (doc,)
			scopes, sources = [], []
			for task in roots:
				ranges, documents = _task_scopes(task)
				scopes.extend(ranges)
				sources.extend(documents)
			keys = boundary._scope_keys(scopes, (*sources, *roots))
			with boundary.acquire(keys):
				current_old = boundary._persisted(doc, current=True)
				boundary.check_pointer(doc, current_old, creating=creating)
				fresh, current_sources = [], []
				for task in (doc, current_old) if current_old else (doc,):
					ranges, documents = _task_scopes(task)
					fresh.extend(ranges)
					current_sources.extend(documents)
				if not boundary._scope_keys(fresh, (*current_sources, *roots)) <= keys:
					progress._reject("purchase_reversal_scope_changed")
				boundary._check_pending(fresh, current_sources)
				yield


def capture_child(doc):
	owner = progress.owner()
	if owner is None:
		return False
	_owned_creation(doc)
	frappe.db.set_value(doc.doctype, doc.name, boundary.POINTER, owner["operation_id"], update_modified=False)
	owner["progress"]["tasks"].setdefault(
		doc.name,
		{"identity": _task_fields(doc), "coverage": {}, "expected": [[doc.voucher_type, doc.voucher_no]]},
	)
	return True


class Receipt:
	def __init__(self, owner):
		self.owner = owner
		self.durable = operation.encode(owner["progress"])

	def prepare(self, identity):
		owner = self.owner
		_, current, durable = progress._load(owner["operation_id"], lock=True)
		if current["reversal"]["generation"] != owner["context"]["reversal"]["generation"]:
			progress._reject("purchase_reversal_generation_changed")
		native = frappe.get_doc("Repost Item Valuation", owner["task"].name, for_update=True)
		if native.get(boundary.POINTER) != owner["operation_id"]:
			progress._reject("purchase_reversal_owner_changed")
		task = owner["progress"]["tasks"][native.name]
		witness = {
			**identity,
			"task": native.name,
			"generation": durable["generation"],
			"status": native.status,
			"current_index": native.current_index or 0,
			"gl_reposting_index": native.gl_reposting_index or 0,
			"checkpoint": native.reposting_data_file,
			"stock": stock_evidence(owner["context"]["reversal"]),
			"coverage_sha256": hashlib.sha256(operation.encode(task["coverage"]).encode()).hexdigest(),
		}
		task["last_receipt"] = witness
		task["commits"] = task.get("commits", 0) + 1
		owner["progress"]["receipts"] = [*owner["progress"].get("receipts", []), witness][-32:]
		progress._write(owner["context"], owner["progress"])
		return witness

	def promote(self, detached):
		self.durable = operation.encode(self.owner["progress"])

	def invalidate(self):
		self.owner["progress"].clear()
		self.owner["progress"].update(json.loads(self.durable))

	def resolve(self, identity, candidate):
		# The substrate closed the old owner before calling this method.
		from frappe.database import get_db

		original_db = frappe.local.db
		db = get_db(
			socket=frappe.conf.db_socket,
			user=frappe.conf.db_user,
			password=frappe.conf.db_password,
			host=frappe.conf.db_host,
			port=frappe.conf.db_port,
			cur_db_name=frappe.conf.db_name,
		)
		try:
			db.connect()
			# ORM query-builder execution can dispatch through frappe.db (the
			# poisoned old owner). This exact readonly query uses the fresh DB.
			rows = db.sql(
				"SELECT output FROM `tabIntegration Request` WHERE name=%s", (self.owner["operation_id"],)
			)
			if not rows:
				return None
			output = json.loads(rows[0][0])
			receipts = output.get("receipts", [])
			actual = next(
				(row for row in receipts if all(row.get(key) == value for key, value in identity.items())),
				None,
			)
			return json.dumps(actual, ensure_ascii=False, sort_keys=True).encode() if actual else None
		finally:
			db.close()
			frappe.local.db = original_db


def _unowned(doc, native):
	from .purchase_reversal_scope import collect_boundary_scope, collect_repost_pair_scope

	# Item/Warehouse RIVs have no voucher fields: their actual durable pair is
	# the identity. Transaction RIVs borrow the same existing closure collector.
	with boundary.execution():
		with boundary.acquire((boundary.fence_key(),)):
			current = frappe.get_doc(doc.doctype, doc.name, for_update=True)
			if current.based_on == "Item and Warehouse":
				pending = frappe.db.get_values(
					"Bin",
					{"warehouse": ["is", "set"], boundary.POINTER: ["is", "set"]},
					"name",
					limit=1,
					for_update=True,
				)
				footprint = collect_repost_pair_scope(current) if pending else None
				keys = (
					boundary._scope_keys((footprint,), ())
					if footprint
					else (boundary.lock_key("pair", current.company, current.item_code, current.warehouse),)
				)
				with boundary.acquire(keys):
					if footprint:
						boundary._check_pending((footprint,), ())
					else:
						boundary._check_bin_pending(current.item_code, current.warehouse)
					return native(current)
			if current.voucher_type and frappe.db.exists(current.voucher_type, current.voucher_no):
				source = frappe.get_doc(current.voucher_type, current.voucher_no)
				footprint = collect_boundary_scope(source)
				with boundary.acquire(boundary._scope_keys((footprint,), (source,))):
					boundary._check_pending((footprint,), (source,))
					return native(current)
			return native(current)


def repost(doc):
	riv = sys.modules["erpnext.stock.doctype.repost_item_valuation.repost_item_valuation"]
	native = riv._dlp_reversal_original_repost
	if progress.accepting() is not None:
		progress._reject("purchase_reversal_executor_during_acceptance")
	with boundary.execution(), boundary.acquire((boundary.fence_key(),)):
		operation_id = frappe.db.get_value(
			"Repost Item Valuation", doc.name, boundary.POINTER, for_update=True
		)
		if not operation_id:
			return _unowned(doc, native)
		_, context, output = progress._load(operation_id, lock=True)
		manifest = context["reversal"]
		with boundary.acquire(progress.keys(manifest)):
			task = frappe.get_doc("Repost Item Valuation", doc.name, for_update=True)
			# The preloaded native doc cannot authorize a write after waiting.
			_, context, output = progress._load(operation_id, lock=True)
			if output["stage"] in ("completed", "failed"):
				return
			identities = {root["name"]: root for root in manifest["roots"]}
			identities.update({name: value["identity"] for name, value in output["tasks"].items()})
			if task.get(boundary.POINTER) != operation_id or _task_fields(task) != identities.get(task.name):
				progress._reject("purchase_reversal_task_identity_changed")
			if task.docstatus != 1 or task.status not in ("Queued", "In Progress"):
				return
			if (
				task.repost_only_accounting_ledgers
				and task.reposting_reference
				and frappe.db.get_value(
					"Repost Item Valuation", task.reposting_reference, "status", for_update=True
				)
				not in ("Completed", "Skipped")
			):
				return
			task_progress = output["tasks"].setdefault(
				task.name, {"identity": _task_fields(task), "coverage": {}, "expected": []}
			)
			owner = {
				"operation_id": operation_id,
				"context": context,
				"progress": output,
				"task": task,
				"expected": task_progress["expected"],
			}
			token = progress._executing.set(owner)
			state = boundary.initialize()
			participant = Receipt(owner)
			state.receipt_participant = participant
			try:
				output.update(stage="recalculating", safe_reason=None)
				progress._write(context, output)
				native(task)
				task.reload()
				if task.status == "Failed":
					output.update(stage="failed", safe_reason="native_repost_failed")
					progress._write(context, output)
				elif task.status in ("Completed", "Skipped"):
					progress.finalize(context, output)
				frappe.db.commit()
				if output["stage"] == "completed":
					state.receipt_participant = None
					cleanup_files(context, output)
			except Exception as error:
				# Only current uncommitted work rolls back; native earlier
				# physical chunks and their durable receipts remain pending.
				if state.poisoned:
					operation.runtime_log(context, "reversal_execution_unknown", error)
					raise  # exact durable receipt is evidence, never a reconnect license
				frappe.db.rollback()
				if not state.poisoned:
					_, context, output = progress._load(operation_id, lock=True)
					owner.update(context=context, progress=output)
					output.update(stage="failed", safe_reason=operation.error_identifier(error))
					progress._write(context, output)
					operation.runtime_log(context, "reversal_failed", error)
					frappe.db.commit()
				raise
			finally:
				state.receipt_participant = None
				progress._executing.reset(token)


def repost_gl(doc):
	riv = sys.modules["erpnext.stock.doctype.repost_item_valuation.repost_item_valuation"]
	owner = progress.owner()
	if owner is not None:
		import erpnext

		expected = (
			(
				[(doc.voucher_type, doc.voucher_no)]
				if doc.repost_only_accounting_ledgers
				else sorted(
					set(riv._get_directly_dependent_vouchers(doc)) | set(riv.get_affected_transactions(doc))
				)
			)
			if erpnext.is_perpetual_inventory_enabled(doc.company)
			else []
		)
		allowed = {(row["doctype"], row["name"]) for row in owner["context"]["reversal"]["scope"]["vouchers"]}
		if not set(expected) <= allowed:
			progress._reject("purchase_reversal_native_scope_expanded")
		owner["expected"] = [list(value) for value in expected]
		owner["progress"]["tasks"][doc.name]["expected"] = owner["expected"]
	return riv._dlp_reversal_original_repost_gl_entries(doc)


def toggle(gl_map):
	gl = sys.modules["erpnext.accounts.general_ledger"]
	result = gl._dlp_reversal_original_toggle_debit_credit_if_negative(gl_map)
	owner = progress.owner()
	# Direct native per-voucher edge captures empty/unchanged expected output
	# before the GL commit that precedes gl_reposting_index persistence.
	caller = sys._getframe(2)
	accounts = sys.modules.get("erpnext.accounts.utils")
	if (
		owner is not None
		and accounts
		and caller.f_code is accounts.repost_gle_for_stock_vouchers.__code__
		and caller.f_globals is accounts.__dict__
	):
		identity = (caller.f_locals["voucher_type"], caller.f_locals["voucher_no"])
		if identity not in {tuple(value) for value in owner["expected"]}:
			progress._reject("purchase_reversal_gl_outside_scope")
		owner["progress"]["tasks"][owner["task"].name]["coverage"][operation.encode(identity)] = {
			"voucher": list(identity),
			"expected": deepcopy(result),
			"precision": caller.f_locals["precision"],
		}
	return result


def checkpoint(data, doc, file_name=None):
	stock = sys.modules["erpnext.stock.stock_ledger"]
	owner = progress.owner()
	# Native's new-file branch preserves the previous durable checkpoint. Old
	# files remain until final completion, so failed chunk writes can resume.
	url = stock._dlp_reversal_original_create_json_gz_file(data, doc, None if owner else file_name)
	if owner:
		files = owner["progress"]["tasks"][doc.name].setdefault("files", {})
		for (name,) in frappe.db.get_values(
			"File",
			{
				"file_url": url,
				"attached_to_doctype": doc.doctype,
				"attached_to_name": doc.name,
				"attached_to_field": "reposting_data_file",
				"is_private": 1,
			},
			"name",
		):
			files[name] = url
		if len(files) > 5000:
			progress._reject("purchase_reversal_evidence_oversize")
	return url


def remove_file(docname):
	riv = sys.modules["erpnext.stock.doctype.repost_item_valuation.repost_item_valuation"]
	if progress.owner() is not None:
		return  # unfinished evidence is retained; final summary survives cleanup
	operation_id = frappe.db.get_value("Repost Item Valuation", docname, boundary.POINTER)
	if operation_id:
		_, _, output = progress._load(operation_id, lock=True)
		if output["stage"] != "completed":
			progress._reject("purchase_reversal_evidence_pending")
	return riv._dlp_reversal_original_remove_attached_file(docname)


def cleanup_files(context, output):
	"""Exact native checkpoint identities only, after the completion tx commits."""
	if output["stage"] != "completed" or output.get("files_cleaned"):
		return
	try:
		for task_name, task in output["tasks"].items():
			for name, url in task.get("files", {}).items():
				if not frappe.db.exists("File", name):
					continue
				file = frappe.get_doc("File", name, for_update=True)
				if (
					file.file_url != url
					or file.attached_to_doctype != "Repost Item Valuation"
					or file.attached_to_name != task_name
					or file.attached_to_field != "reposting_data_file"
					or not file.is_private
				):
					progress._reject("purchase_reversal_checkpoint_identity_changed")
				file.delete(ignore_permissions=True)
		output["files_cleaned"] = True
		progress._write(context, output)
		frappe.db.commit()
	except Exception as error:
		frappe.db.rollback()
		operation.runtime_log(context, "reversal_checkpoint_cleanup_failed", error)


def stock_evidence(manifest):
	names = sorted({row["name"] for row in manifest["sles"]})
	rows = (
		frappe.db.get_values(
			"Stock Ledger Entry",
			{"name": ["in", names]},
			[
				"name",
				"actual_qty",
				"qty_after_transaction",
				"valuation_rate",
				"stock_value",
				"stock_value_difference",
				"stock_queue",
				"is_cancelled",
			],
			as_dict=True,
			for_update=True,
		)
		if names
		else []
	)
	return {
		"count": len(rows),
		"sha256": hashlib.sha256(
			operation.encode(sorted(rows, key=lambda row: row.name)).encode()
		).hexdigest(),
	}


def verify_stock_chain(manifest):
	inputs = []
	for pair, anchor in manifest["scope"]["anchors"]:
		inputs.extend(
			frappe.db.get_values(
				"Stock Ledger Entry",
				{**pair, "posting_datetime": [">=", anchor]},
				progress.SLE_INPUTS,
				as_dict=True,
				for_update=True,
				limit=50001,
			)
		)
	if len(inputs) > 50000 or operation.encode(
		sorted(inputs, key=lambda row: row["name"])
	) != operation.encode(sorted(manifest["sles"], key=lambda row: row["name"])):
		progress._reject("purchase_reversal_stock_inputs_changed")
	for pair in manifest["scope"]["pairs"]:
		rows = frappe.db.get_values(
			"Stock Ledger Entry",
			{**pair, "is_cancelled": 0},
			[
				"name",
				"actual_qty",
				"qty_after_transaction",
				"stock_value",
				"stock_value_difference",
				"valuation_rate",
			],
			as_dict=True,
			order_by="posting_datetime asc,creation asc,name asc",
			for_update=True,
			limit=50001,
		)
		if len(rows) > 50000:
			progress._reject("purchase_reversal_scope_oversize")
		quantity, value = 0.0, 0.0
		for row in rows:
			quantity += flt(row.actual_qty)
			value += flt(row.stock_value_difference)
			if flt(row.qty_after_transaction, 6) != flt(quantity, 6) or flt(row.stock_value, 6) != flt(
				value, 6
			):
				progress._reject("purchase_reversal_stock_chain_mismatch")
		bin = frappe.db.get_value(
			"Bin", pair, ["actual_qty", "stock_value", "valuation_rate"], as_dict=True, for_update=True
		)
		if not bin or flt(bin.actual_qty, 6) != flt(quantity, 6) or flt(bin.stock_value, 6) != flt(value, 6):
			progress._reject("purchase_reversal_bin_mismatch")
		if rows and flt(bin.valuation_rate, 6) != flt(rows[-1].valuation_rate, 6):
			progress._reject("purchase_reversal_bin_valuation_mismatch")


def verify_gl_coverage(context, output):
	from erpnext.accounts.general_ledger import process_gl_map

	from . import purchase_consistency as consistency

	expected, coverage = set(), {}
	if not output.get("receipts") or output["receipts"][-1]["stock"] != stock_evidence(context["reversal"]):
		progress._reject("purchase_reversal_stock_receipt_mismatch")
	for task in output["tasks"].values():
		expected.update(tuple(value) for value in task["expected"])
		coverage.update({tuple(value["voucher"]): value for value in task["coverage"].values()})
		if not task.get("last_receipt"):
			progress._reject("purchase_reversal_receipt_missing")
	if not expected <= set(coverage):
		progress._reject("purchase_reversal_gl_coverage_missing")
	invoice_plans = {}
	for identity in sorted(expected):
		doc = frappe.get_doc(*identity, for_update=True)
		plan = process_gl_map([frappe._dict(value) for value in deepcopy(coverage[identity]["expected"])])
		actual = consistency._ledger(doc, "GL Entry", is_cancelled=0)
		consistency._compare_gl(
			doc, consistency._gl_map(actual), consistency._gl_map(plan), "重算后的原生总账计划"
		)
		if doc.doctype == "Purchase Invoice":
			invoice_plans[identity] = plan
		if not frappe.db.exists("DocType", "China Accounting Voucher"):
			continue
		posted = frappe.db.get_values(
			"China Accounting Voucher",
			{
				"source_doctype": doc.doctype,
				"source_name": doc.name,
				"source_event": "Posting",
				"docstatus": 1,
			},
			"name",
			for_update=True,
		)
		for (name,) in posted:
			voucher = frappe.get_doc("China Accounting Voucher", name, for_update=True)
			consistency._check_finance_voucher(doc, voucher, "Posting", "Posted")
			consistency._compare_gl(
				doc,
				consistency._gl_map(voucher.entries),
				consistency._gl_map(actual),
				"历史中国凭证与重算后总账",
			)
	return invoice_plans


def reconcile(operation_id):
	with boundary.execution(), boundary.acquire((boundary.fence_key(),)):
		_, context, output = progress._load(operation_id, lock=True)
		if output["stage"] == "failed":
			return  # failure holds until current native handling authority retries
		with boundary.acquire(progress.keys(context["reversal"])):
			token = progress._executing.set({"operation_id": operation_id})
			try:
				if output["stage"] != "completed":
					progress.finalize(context, output)
					frappe.db.commit()
				cleanup_files(context, output)
			except Exception as error:
				frappe.db.rollback()
				_, context, output = progress._load(operation_id, lock=True)
				output.update(stage="failed", safe_reason=operation.error_identifier(error))
				progress._write(context, output)
				operation.runtime_log(context, "reversal_failed", error)
				frappe.db.commit()
			finally:
				progress._executing.reset(token)


def clear_old_logs(days=None):
	from frappe.query_builder import Interval
	from frappe.query_builder.functions import Now

	table = frappe.qb.DocType("Repost Item Valuation")
	frappe.db.delete(
		table,
		filters=(table.creation < (Now() - Interval(days=days or 90)))
		& table.status.isin(["Completed", "Skipped"])
		& (table[boundary.POINTER].isnull() | (table[boundary.POINTER] == "")),
	)


def bulk_restart(docnames):
	riv = sys.modules["erpnext.stock.doctype.repost_item_valuation.repost_item_valuation"]
	names = frappe.parse_json(docnames)
	for name in names:
		doc = frappe.get_doc("Repost Item Valuation", name)
		with task_boundary(doc):
			guard_task(doc, destructive=True)  # whole batch preflight, before its first restart writes
	return riv._dlp_reversal_original_bulk_restart_reposting(docnames)


if TYPE_CHECKING:
	# Installed in each audited native module's globals, not this module's
	# runtime namespace. These aliases describe that binding to static tools.
	_dlp_reversal_repost = repost
	_dlp_reversal_gl = repost_gl
	_dlp_reversal_toggle = toggle
	_dlp_reversal_file = checkpoint
	_dlp_reversal_remove = remove_file
	_dlp_reversal_clear = clear_old_logs
	_dlp_reversal_bulk = bulk_restart
