"""Durable identity for the installed MES Bin reconciliation, using native IR.

The original MES helper owns the queue, deduplication and inventory calculation.
This adapter borrows the existing physical session and publication fence. It is
prospective only: no historical task is adopted and no cancellation is enabled.
"""

from __future__ import annotations

from frappe import _ as _i18n


import hashlib
import json
import re
import sys
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from threading import RLock
from types import CodeType, FunctionType
from typing import TYPE_CHECKING

import frappe
from frappe.utils import caching as _request_caching

from . import purchase_repost_boundary as boundary

SERVICE = "DeepLinkERP native MES Bin intent"
PENDING = "Native MES Bin sync pending"
ACKNOWLEDGED = "Native MES Bin sync acknowledged"
PREFIX = "DLP-MES-BIN-"
METHOD = "mes_integration.mes_integration.material_request.sync_material_request_bins"
NATIVE_SHA256 = "73cd5eb8d391b53383fd6512b8665bf32bf4604754aa2c43f9d7ffd3d87f255a"
RETENTION_SHA256 = (
	"e3656896887da53c7472414470b6eb5cd067a98de6a95756f33abbfdd37ba856",
	"87ef9e5d7787809f6d827eb042d09ecb802448b5eb8b7106873c9ff339d0f4cb",
)
RQ_QUEUE_SHA256 = "36f3f197561466dfb95c598ed6ade0783b4a0734ee17b9600cc51e9b7f3a6d85"
REQUEST_CACHE_SHA256 = "95260b7ea1bda8133e1ac127a4bd90628f688450af4a7020e469bb57600329a5"
_request_cache_namespace = _request_caching.__dict__
_request_cache_source = Path(_request_caching.__file__)
CLAIM_META = "deeplinkerp_native_bin_intent"
MAX_RECORDS, MAX_PAIRS = 5000, 2500
MAX_BODY_BYTES, MAX_TOTAL_BYTES = 4 * 1024 * 1024, 16 * 1024 * 1024
ACTIVITY_COLUMN = "custom_purchase_native_intent_active"
ACTIVITY_INDEX = "purchase_native_intent_active"
ACTIVITY_EXPRESSION = (
	"CASE WHEN BINARY integration_request_service = BINARY '"
	+ SERVICE
	+ "' AND BINARY status = BINARY 'Completed' AND BINARY request_description = BINARY '"
	+ ACKNOWLEDGED
	+ "' THEN 0 ELSE 1 END"
)
_mutation = ContextVar("purchase_native_intent_mutation", default=frozenset())
_install_lock = RLock()
_claim = ContextVar("purchase_native_intent_fixed_claim", default=())
_stock_claim = ContextVar("purchase_native_intent_stock_claim", default=None)
_native_reads = ContextVar("purchase_native_intent_current_demand", default=False)
_native_lock_owner = ContextVar("purchase_native_intent_native_lock_owner", default=None)
_release_claim = ContextVar("purchase_native_intent_native_release_claim", default=None)


@dataclass(frozen=True)
class NativeIntent:
	name: str
	generation: str
	material_request_name: str
	user: str
	pairs: tuple
	body: str
	facts: dict


def _header(row, *, acknowledged=False):
	valid_phase = (
		(row.status == "Completed" and row.request_description == ACKNOWLEDGED)
		if acknowledged
		else (row.status in ("Queued", "Failed") and row.request_description == PENDING)
	)
	if (
		row.integration_request_service != SERVICE
		or not valid_phase
		or row.reference_doctype != "Material Request"
		or not isinstance(row.reference_docname, str)
		or not row.reference_docname
		or not re.fullmatch(PREFIX + r"[0-9a-f]{32}", row.name or "")
		or not re.fullmatch(r"[0-9a-f]{32}", row.request_id or "")
	):
		_reject("身份、引用或状态未经核查")
	if type(row.body_bytes) is not int or not 0 < row.body_bytes <= MAX_BODY_BYTES:
		_reject("正文缺失或超过安全上限")
	if row.output_bytes is not None and (
		type(row.output_bytes) is not int or not 0 <= row.output_bytes <= MAX_BODY_BYTES
	):
		_reject("输出正文缺失或超过安全上限")


def _manifest(row, body):
	try:
		facts = json.loads(body)

		def pairs(key):
			value = facts[key]
			if not isinstance(value, list) or any(
				not isinstance(pair, list)
				or len(pair) != 2
				or any(not isinstance(part, str) or not part or len(part) > 140 for part in pair)
				for pair in value
			):
				raise ValueError
			result = tuple(tuple(pair) for pair in value)
			if result != tuple(sorted(set(result))):
				raise ValueError
			return result

		if not isinstance(facts, dict) or type(facts.get("schema")) is not int or facts["schema"] != 1:
			raise ValueError
		old_pairs, new_pairs, fixed_pairs = pairs("old_pairs"), pairs("new_pairs"), pairs("pairs")
		pairs("source_pairs")
		scope = facts.get("source_scope")
		if scope is not None and (
			not isinstance(scope, list)
			or scope != sorted(set(scope))
			or any(not isinstance(name, str) or not name or len(name) > 140 for name in scope)
		):
			raise ValueError
		if (
			not fixed_pairs
			or len(fixed_pairs) > MAX_PAIRS
			or fixed_pairs != tuple(sorted(set(old_pairs) | set(new_pairs)))
			or facts.get("generation") != row.request_id
			or facts.get("material_request_name") != row.reference_docname
			or facts.get("site") != frappe.local.site
			or facts.get("database") != frappe.db.cur_db_name
			or facts.get("method") != METHOD
			or facts.get("native_source_sha256") != NATIVE_SHA256
			or facts.get("job_id") != "mes-material-request-bin-sync:" + row.reference_docname
			or not isinstance(facts.get("user"), str)
			or not facts["user"]
			or not isinstance(facts.get("source_modified"), str)
			or not facts["source_modified"]
			or not re.fullmatch(r"[0-9a-f]{64}", facts.get("source_persisted_version") or "")
			or not re.fullmatch(r"[0-9a-f]{64}", facts.get("source_version") or "")
		):
			raise ValueError
		companies = facts.get("warehouse_companies")
		if (
			not isinstance(companies, dict)
			or set(companies) != {pair[1] for pair in fixed_pairs}
			or any(not isinstance(company, str) or not company for company in companies.values())
		):
			raise ValueError
	except (TypeError, ValueError, KeyError):
		_reject("冻结正文格式或身份无效")
	return NativeIntent(
		row.name, row.request_id, row.reference_docname, facts["user"], fixed_pairs, body, facts
	)


def _headers(*, name=None):
	fields = "name,integration_request_service,request_id,status,request_description,reference_doctype,reference_docname,LENGTH(data) AS body_bytes,LENGTH(output) AS output_bytes"
	if name is not None:
		return frappe.db.sql(
			"SELECT " + fields + " FROM `tabIntegration Request` WHERE name=%s FOR UPDATE",
			(name,),
			as_dict=True,
		)
	return frappe.db.sql(
		"SELECT "
		+ fields
		+ " FROM `tabIntegration Request` FORCE INDEX (`"
		+ ACTIVITY_INDEX
		+ "`) WHERE integration_request_service=%s AND `"
		+ ACTIVITY_COLUMN
		+ "`=1 ORDER BY name LIMIT %s FOR UPDATE",
		(SERVICE, MAX_RECORDS + 1),
		as_dict=True,
	)


def _load(headers, *, acknowledged=False):
	if len(headers) > MAX_RECORDS:
		_reject("记录超过安全上限")
	for row in headers:
		_header(row, acknowledged=acknowledged)  # every header before loading any manifest body
	if sum(row.body_bytes + (row.output_bytes or 0) for row in headers) > MAX_TOTAL_BYTES:
		_reject("正文总字节超过安全上限")
	if not headers:
		return ()
	bodies = frappe.db.sql(
		"SELECT name,data,output FROM `tabIntegration Request` WHERE name IN %s ORDER BY name FOR UPDATE",
		(tuple(row.name for row in headers),),
		as_dict=True,
	)
	by_name = {row.name: row for row in bodies}
	if len(by_name) != len(headers):
		_reject("当前冻结集合已改变")
	result, union = [], set()
	for row in headers:
		actual = by_name[row.name]
		body, output = actual.data, actual.output
		if not isinstance(body, str) or len(body.encode("utf-8")) != row.body_bytes:
			_reject("实际冻结正文已改变")
		if (output is None and row.output_bytes is not None) or (
			output is not None
			and (not isinstance(output, str) or len(output.encode("utf-8")) != row.output_bytes)
		):
			_reject("实际输出正文已改变")  # resource evidence only; never an ACK authority
		record = _manifest(row, body)
		union.update(record.pairs)
		if len(union) > MAX_PAIRS:
			_reject("联合范围超过安全上限")
		result.append(record)
	return tuple(result)


def active_set():
	"""Indexed current read including zero-set fence, independent of reversals."""
	with boundary.execution(), boundary.acquire((boundary.fence_key(),)):
		return _load(_headers())


def recover():
	"""Internal SERVICE recovery entry; no scheduler/second queue is installed."""
	if frappe.db.transaction_writes:
		_reject("恢复必须从独立无写入事务开始")
	with boundary.execution(), boundary.acquire((boundary.fence_key(),)):
		records = _load(_headers())
		groups = {}
		for record in records:
			groups.setdefault(record.material_request_name, []).append(record)
		for name in sorted(groups):
			_dispatch(tuple(groups[name]))
		return tuple(record.name for record in records)


def reject_intersection(pairs):
	"""Future D admission hook, before cancellation/pointer publication."""
	if set(pairs).intersection(pair for record in active_set() for pair in record.pairs):
		_reject("仍待原生同步完成，请先完成同步后重试")


def adapt_query(query, state):
	"""Current native read points only while the existing lease owns writes.

	Native get_bin and its concurrent-creation get_last_doc fallback otherwise
	reload an RR snapshot before a full db_update. The native MES aggregate
	similarly needs a current read after its lock, not a new transaction/view.
	"""
	if not state.depth or not state.locks:
		return query
	sql = str(query).strip()
	if not re.match(r"SELECT\b", sql, re.I) or re.search(r"\bFOR\s+UPDATE\b|\bLOCK\s+IN\s+SHARE", sql, re.I):
		return query
	bin_read = re.search(r"\bFROM\s+`tabBin`", sql, re.I)
	native_read = _native_reads.get() and re.search(
		r"\bFROM\s+`tab(?:Item|Material Request|Material Request Item)`", sql, re.I
	)
	if bin_read or native_read:
		return sql.rstrip(";") + " FOR UPDATE"
	return query


def _native_callsite(owner, lines):
	"""A live audited generator frame, not a suspended yield's context flag."""
	expected, original = owner[3], owner[4]
	frame = sys._getframe(1)
	while frame is not None:
		if frame is expected:
			return (
				frame.f_code is original.__code__
				and frame.f_globals is original.__globals__
				and frame.f_lineno in lines
			)
		frame = frame.f_back
	return False


def protect_native_acquisition(query, values, state):
	owner = _native_lock_owner.get()
	if not re.search(r"\bGET_LOCK\b", str(query), re.I):
		return
	if owner is None or owner[0] is not state:
		if state.native_acquisitions and (
			str(query).strip() != "SELECT GET_LOCK(%s, %s)"
			or not isinstance(values, (tuple, list))
			or len(values) != 2
			or _tracked_native_name(values[0], state)
		):
			_reject("已跟踪原生租约的重新获取调用点未经核查")
		return  # genuinely unrelated/untracked native advisory lock behavior
	if (
		str(query).strip() != "SELECT GET_LOCK(%s, %s)"
		or not _native_callsite(owner, range(944, 948))
		or not isinstance(values, (tuple, list))
		or len(values) != 2
		or values[0] != owner[3].f_locals.get("lock_name")
		or not re.fullmatch(r"mes_mr_bin_[0-9a-f]{64}", str(values[0]))
		or values[1] != 180
		or owner[3].f_globals.get("MES_BIN_LOCK_TIMEOUT_SECONDS") != 180
	):
		_reject("原生租约调用点或参数未经核查")


def observe_native_lock(query, values, result, state):
	owner = _native_lock_owner.get()
	if owner is None or owner[0] is not state or str(query).strip() != "SELECT GET_LOCK(%s, %s)":
		return
	protect_native_acquisition(query, values, state)
	if result and result[0][0] == 1:
		owner[1].append((state.adopt_native(values[0]), values[0]))


def _tracked_native_name(value, state):
	# Verified MES names are ASCII; mysqlclient binds bytes as the same name.
	# Actual native upper-case names are distinct. No generic coercion/casefold.
	return any(
		value == name or (isinstance(value, bytes) and value == name.encode("ascii"))
		for name, _ in state.native_acquisitions.values()
	)


def protect_native_release(query, values, state):
	if not state.native_acquisitions:
		return
	# Only the two advisory-release function names. This is not a SQL rewriter;
	# native internal physical cleanup uses Session.raw, outside this precheck.
	sql = re.sub(r"/\*.*?\*/|--[^\n]*|#[^\n]*", " ", str(query), flags=re.S)
	if not re.search(r"\bRELEASE_(?:ALL_)?LOCKS?\s*\(", sql, re.I):
		return
	if (
		str(query).strip() != "SELECT RELEASE_LOCK(%s)"
		or not isinstance(values, (tuple, list))
		or len(values) != 1
		or _tracked_native_name(values[0], state)
	):
		_reject("未冻结的原生租约释放调用")


@contextmanager
def _native_lock_callbacks(state, expected_callback, native_context, original):
	"""Freeze actual original finally callbacks; resettable queues are not owner."""
	frame = native_context.gen.gi_frame
	owner = (state, [], expected_callback, frame, original, [])
	token = _native_lock_owner.set(owner)
	try:
		yield owner
	except Exception as error:
		# Native finally's callback registration can replace a first GET_LOCK/
		# body error. Restore only the exact earlier exception from this owned
		# generator's pre-finally traceback, preserving its original cause.
		if owner[5]:
			raise owner[5][0]
		candidate, seen = error, set()
		while candidate is not None and id(candidate) not in seen:
			seen.add(id(candidate))
			trace = candidate.__traceback__
			while trace is not None:
				if trace.tb_frame is frame and trace.tb_lineno <= 968:
					if candidate is not error:
						raise candidate
					raise
				trace = trace.tb_next
			candidate = candidate.__context__
		raise
	finally:
		_native_lock_owner.reset(token)


def freeze_native_callback(callback, state):
	owner = _native_lock_owner.get()
	if (
		owner is None
		or owner[0] is not state
		or not isinstance(callback, FunctionType)
		or callback.__code__ != owner[2]
	):
		return callback
	if (
		not _native_callsite(owner, (977, 978))
		or callback.__globals__ is not owner[3].f_globals
		or callback is not owner[3].f_locals.get("release_locks")
	):
		_reject("原生租约回调调用点未经核查")
	names = tuple(callback.__closure__[0].cell_contents)
	acquired = tuple(reversed(owner[1]))
	if names != tuple(name for _, name in acquired):
		_reject("原生租约回调范围已改变")
	held = tuple(token for token, _ in acquired)

	def frozen_release():
		release_token = _release_claim.set((state, held, names))
		try:
			return callback()  # actual original lambda/captured callable
		finally:
			_release_claim.reset(release_token)

	return frozen_release


def _release(lock_names):
	import mes_integration.mes_integration.material_request as mes

	lock_names = tuple(lock_names)
	claim = _release_claim.get()
	if claim is not None:
		state, tokens, names = claim
		if lock_names != names:
			_reject("原生租约释放认领范围已改变")
		return state.release_native(tokens)
	state = boundary.initialize()
	if any(name in state.locks for name in lock_names):
		_reject("未冻结的原生租约释放调用")
	return mes._dlp_native_intent_original_release(lock_names)


def _reject(reason):
	boundary._reject("原生库存同步意图" + reason)


def _namespace_identity(value, identity, *, prefix=False):
	# Use the installed column's exact equality namespace (including accent,
	# case and PAD SPACE equivalence); Python casefold is not that collation.
	if value == identity:
		return True
	if not isinstance(value, (str, bytes)):
		return False
	operator = "LIKE" if prefix else "="
	return (
		frappe.db.sql(
			f"SELECT CAST(%s AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci {operator} %s",
			(value, identity + "%" if prefix else identity),
		)[0][0]
		== 1
	)


def protected(doc):
	return bool(
		doc
		and (
			_namespace_identity(doc.get("integration_request_service"), SERVICE)
			or _namespace_identity(doc.get("name"), PREFIX, prefix=True)
		)
	)


@contextmanager
def _allow(name):
	token = _mutation.set(_mutation.get() | {name})
	try:
		yield
	finally:
		_mutation.reset(token)


def guard(doc, *, proposed=None):
	"""Both stored and proposed identities before any native write/commit."""
	from .purchase_reversal_progress import guard_audit

	guard_audit(doc, proposed)
	fields = {"name": doc.name, "integration_request_service": doc.get("integration_request_service")}
	if proposed:
		fields.update(proposed)
	old = None
	if doc.name:
		rows = frappe.db.get_values(
			"Integration Request",
			{"name": doc.name},
			["name", "integration_request_service"],
			as_dict=True,
			for_update=True,
		)
		old = rows[0] if rows else None
	if (protected(doc) or protected(fields) or protected(old)) and doc.name not in _mutation.get():
		frappe.throw(_i18n("原生库存同步审计不能由普通操作创建、改写或清理"), frappe.PermissionError)


def _pairs(mes, doc, rows=None):
	if not doc:
		return ()
	return tuple(mes.get_mes_material_request_item_warehouse_pairs(doc, rows))


def _source_version(doc):
	from .purchase_operation import encode

	return hashlib.sha256(encode(doc.as_dict()).encode()).hexdigest()


def _source_identity(doc):
	"""Producer identity, not native downstream fulfillment/audit progression.

	PO.status_updater writes child ordered_qty/modified/modified_by and parent
	per_ordered/status/modified/modified_by. PR.status_updater additionally writes
	child received_qty and parent per_received. StockEntry writes transfer_status.
	These exact derived fields are read again by native demand math; every
	other parent/child field, including requested qty, remains in the identity.
	Full persisted version is separately frozen and checked in ACK/replay.
	"""
	from erpnext.controllers.status_updater import status_map

	from .purchase_operation import encode

	facts = doc.as_dict()
	status = facts.get("status")
	known = {rule[0] for rule in status_map["Material Request"]}
	if status not in known:
		_reject("原生来源资格状态未经核查")
	facts["status"] = status if status in ("Stopped", "Cancelled") else "Eligible"
	for name in ("modified", "modified_by", "per_ordered", "per_received", "transfer_status"):
		facts.pop(name, None)
	facts["items"] = [
		{
			name: value
			for name, value in row.items()
			if name not in ("modified", "modified_by", "ordered_qty", "received_qty")
		}
		for row in facts.get("items", [])
	]
	return hashlib.sha256(encode(facts).encode()).hexdigest()


def _claim_identities(records):
	return sorted(
		[
			[record.name, record.generation, hashlib.sha256(record.body.encode()).hexdigest()]
			for record in records
		]
	)


def _source_witness(records):
	"""Exact persisted identity, with at least one frozen actual producer scope."""
	import mes_integration.mes_integration.material_request as mes

	from . import purchase_payment_service as service

	current = service._read("Material Request", records[0].material_request_name)
	version, identity, pairs = _source_version(current), _source_identity(current), _pairs(mes, current)
	matching = [
		record
		for record in records
		if record.facts["source_version"] == identity
		and tuple(tuple(pair) for pair in record.facts["source_pairs"]) == pairs
		and tuple(tuple(pair) for pair in record.facts["new_pairs"])
		== _pairs(mes, current, record.facts["source_scope"])
	]
	if not matching:
		_reject("当前来源版本或固定范围未由新生产者登记")
	return {
		"version": version,
		"identity": identity,
		"modified": str(current.modified),
		"pairs": pairs,
		"generation": matching[0].generation,
	}


def register(material_request, mr_item_rows=None):
	install()
	import mes_integration.mes_integration.material_request as mes

	from . import purchase_operation as audit
	from . import purchase_payment_service as service

	if not material_request.name:
		return
	new_pairs = _pairs(mes, material_request, mr_item_rows)
	# Incoming detail IDs cannot select removed/renamed persisted old children.
	old_pairs = _pairs(mes, material_request.get_doc_before_save())
	pairs = tuple(sorted(set(old_pairs) | set(new_pairs)))
	if not pairs:
		return
	if len(pairs) > MAX_PAIRS:
		_reject("范围超过安全上限")
	name, generation = PREFIX + uuid.uuid4().hex, uuid.uuid4().hex
	frozen_name = str(material_request.name)
	state = boundary.initialize()
	boundary.require_publication_order(state)
	epoch = state.epoch
	with boundary.execution(), boundary.acquire((boundary.fence_key(),)):
		headers = _headers()
		if len(headers) >= MAX_RECORDS:
			_reject("新增记录将超过安全上限")
		active = _load(headers)
		if len(set(pairs) | {pair for record in active for pair in record.pairs}) > MAX_PAIRS:
			_reject("新增联合范围将超过安全上限")
		keys = {boundary.lock_key("document", "Material Request", frozen_name)}
		companies = {}
		with service.current_reads():
			for item, warehouse in pairs:
				actual = service._read("Warehouse", warehouse, {"company", "is_group", "disabled"})
				if not actual.company or actual.is_group or actual.disabled:
					_reject("仓库所属公司或状态无效")
				companies[warehouse] = actual.company
				keys.add(boundary.lock_key("pair", actual.company, item, warehouse))
		with boundary.acquire(keys), service.current_reads():
			persisted = service._read("Material Request", frozen_name)
			source_scope = sorted(set(mr_item_rows)) if mr_item_rows else None
			if _pairs(mes, persisted, source_scope) != new_pairs:
				_reject("生产者内存范围与同事务持久来源不一致")
			facts = {
				"schema": 1,
				"generation": generation,
				"material_request_name": frozen_name,
				"method": METHOD,
				"job_id": "mes-material-request-bin-sync:" + frozen_name,
				"user": frappe.session.user,
				"site": frappe.local.site,
				"database": frappe.db.cur_db_name,
				"source_modified": str(persisted.modified),
				"source_version": _source_identity(persisted),
				"source_persisted_version": _source_version(persisted),
				"source_pairs": _pairs(mes, persisted),
				"source_scope": source_scope,
				"old_pairs": old_pairs,
				"new_pairs": new_pairs,
				"pairs": pairs,
				"warehouse_companies": companies,
				"native_source_sha256": NATIVE_SHA256,
			}
			body = audit.encode(facts)
			if len(body.encode("utf-8")) > MAX_BODY_BYTES:
				_reject("正文超过安全上限")
			if (
				len(body.encode("utf-8")) + sum(row.body_bytes + (row.output_bytes or 0) for row in headers)
				> MAX_TOTAL_BYTES
			):
				_reject("新增正文总字节将超过安全上限")
			with _allow(name):
				frappe.get_doc(
					{
						"doctype": "Integration Request",
						"name": name,
						"integration_request_service": SERVICE,
						"request_id": generation,
						"request_description": PENDING,
						"is_remote_request": 0,
						"status": "Queued",
						"data": body,
						"reference_doctype": "Material Request",
						"reference_docname": frozen_name,
					}
				).db_insert()

			# Immutable native identity, not a late read of the mutable MR object.
			def callback():
				return dispatch(name, parent=(state, epoch))

			request = getattr(frappe.local, "request", None)
			if request and hasattr(request, "after_response"):
				request.after_response.add(callback)
			else:
				frappe.db.after_commit.add(callback)


def dispatch(name, *, parent=None):
	"""Committed identity only; original helper remains the sole dispatcher."""
	if parent:
		state, epoch = parent
		if state.poisoned or state.epoch <= epoch:
			return  # callback before parent boundary is not a committed producer
		state.authority()
	with boundary.execution(), boundary.acquire((boundary.fence_key(),)):
		headers = _headers(name=name)
		if not headers:
			return  # rolled-back HTTP after_response callback survives natively
		if headers[0].status == "Completed" and headers[0].request_description == ACKNOWLEDGED:
			return _replay(headers)
		return _dispatch(_load(headers))


def _dispatch(records):
	install()
	import mes_integration.mes_integration.material_request as mes

	if len({record.user for record in records}) != 1:
		_reject("不同生产者执行人的固定集合尚未支持恢复")
	token, user = _claim.set(records), frappe.session.user
	try:
		frappe.set_user(records[0].user)
		return mes.enqueue_mes_material_request_bin_sync_job(
			material_request_name=records[0].material_request_name,
			item_warehouse_pairs=tuple(sorted({pair for record in records for pair in record.pairs})),
		)
	finally:
		_claim.reset(token)
		frappe.set_user(user)


def _replay(headers):
	"""Canonical terminal identity still needs receipt integrity and current ACLs."""
	import mes_integration.mes_integration.material_request as mes

	from . import purchase_payment_service as service
	from .purchase_operation import encode

	record = _load(headers, acknowledged=True)[0]
	if headers[0].output_bytes is None or not 0 < headers[0].output_bytes <= MAX_BODY_BYTES:
		_reject("已确认结果正文缺失或超过安全上限")
	output = frappe.db.sql(
		"SELECT output FROM `tabIntegration Request` WHERE name=%s FOR UPDATE", (record.name,)
	)[0][0]
	try:
		receipt = json.loads(output)
		if (
			not isinstance(receipt, dict)
			or type(receipt.get("schema")) is not int
			or receipt["schema"] != 1
			or receipt.get("generation") != record.generation
			or receipt.get("user") != record.user
			or receipt.get("body_sha256") != hashlib.sha256(record.body.encode()).hexdigest()
			or type(receipt.get("connection_id")) is not int
			or receipt["connection_id"] <= 0
			or type(receipt.get("epoch")) is not int
			or receipt["epoch"] < 0
			or not re.fullmatch(r"[0-9a-f-]{36}", receipt.get("execution_id") or "")
			or not isinstance(receipt.get("bins"), list)
			or not isinstance(receipt.get("nonstock_pairs"), list)
		):
			raise ValueError
		stock = {(row["item_code"], row["warehouse"]): row for row in receipt["bins"]}
		excluded = {(row["item_code"], row["warehouse"]): row for row in receipt["nonstock_pairs"]}
		if (
			len(stock) != len(receipt["bins"])
			or len(excluded) != len(receipt["nonstock_pairs"])
			or set(stock) & set(excluded)
			or set(stock) | set(excluded) != set(record.pairs)
		):
			raise ValueError
		claim, source = receipt["claim"], receipt["source"]
		if (
			not isinstance(claim, list)
			or not claim
			or len(claim) > MAX_RECORDS
			or any(
				not isinstance(entry, list)
				or len(entry) != 3
				or not re.fullmatch(PREFIX + r"[0-9a-f]{32}", entry[0] or "")
				or not re.fullmatch(r"[0-9a-f]{32}", entry[1] or "")
				or not re.fullmatch(r"[0-9a-f]{64}", entry[2] or "")
				for entry in claim
			)
			or claim != sorted(claim)
			or len({entry[0] for entry in claim}) != len(claim)
			or receipt["claim_names"] != [entry[0] for entry in claim]
			or receipt["claim_digest"] != hashlib.sha256(encode(claim).encode()).hexdigest()
			or [record.name, record.generation, hashlib.sha256(record.body.encode()).hexdigest()] not in claim
			or not isinstance(source, dict)
			or source.get("generation") not in {entry[1] for entry in claim}
		):
			raise ValueError
	except (TypeError, ValueError, KeyError):
		_reject("已确认结果与固定认领证据不一致")
	token = _native_reads.set(True)
	try:
		with boundary.acquire(_current_keys((record,), record.pairs)), service.current_reads():
			actual_source = service._read("Material Request", record.material_request_name)
			if (
				source.get("version") != _source_version(actual_source)
				or source.get("modified") != str(actual_source.modified)
				or source.get("pairs") != [list(pair) for pair in _pairs(mes, actual_source)]
			):
				_reject("已确认来源版本已改变")
			current = mes.get_mes_indented_qty_map(tuple(stock)) if stock else {}
			for item, warehouse in record.pairs:
				actual = service._read("Item", item, {"is_stock_item", "modified"})
				bins = frappe.db.get_values(
					"Bin",
					{"item_code": item, "warehouse": warehouse},
					["name", "indented_qty", boundary.POINTER],
					as_dict=True,
					limit=2,
					for_update=True,
				)
				if (item, warehouse) in stock:
					evidence = stock[item, warehouse]
					if (
						actual.is_stock_item != 1
						or len(bins) != 1
						or bins[0].name != evidence.get("name")
						or bins[0].get(boundary.POINTER)
						or Decimal(str(bins[0].indented_qty)) != Decimal(evidence["indented_qty"])
						or Decimal(str(current.get((item, warehouse), 0))).quantize(Decimal("0.000000001"))
						!= Decimal(evidence["indented_qty"]).quantize(Decimal("0.000000001"))
					):
						_reject("已确认库存证据已改变")
				else:
					evidence = excluded[item, warehouse]
					if (
						actual.is_stock_item != 0
						or bins
						or evidence.get("item_name") != actual.name
						or evidence.get("is_stock_item") != 0
						or evidence.get("native_write_eligible") is not False
						or evidence.get("item_modified") != str(actual.modified)
					):
						_reject("已确认非库存资格证据已改变")
		return {"acknowledged": True, "generation": record.generation}
	finally:
		_native_reads.reset(token)


def _current_keys(records, pairs):
	from . import purchase_payment_service as service

	keys = {
		boundary.lock_key("document", "Material Request", record.material_request_name) for record in records
	}
	with service.current_reads():
		for item, warehouse in pairs:
			actual = service._read("Warehouse", warehouse, {"company", "is_group", "disabled"})
			if (
				not actual.company
				or actual.is_group
				or actual.disabled
				or any(
					record.facts["warehouse_companies"].get(warehouse, actual.company) != actual.company
					for record in records
				)
			):
				_reject("旧或新仓库持久所属公司已改变")
			keys.add(boundary.lock_key("pair", actual.company, item, warehouse))
	return keys


def _ack(records, pairs, state, nonstock, source):
	import mes_integration.mes_integration.material_request as mes
	from frappe.utils import flt

	from .purchase_operation import encode

	current = mes.get_mes_indented_qty_map(pairs) if pairs else {}  # original nonempty calculation only
	bins = []
	for item, warehouse in pairs:
		rows = frappe.db.get_values(
			"Bin",
			{"item_code": item, "warehouse": warehouse},
			["name", "indented_qty", boundary.POINTER],
			as_dict=True,
			limit=2,
			for_update=True,
		)
		if len(rows) != 1 or rows[0].get(boundary.POINTER):
			_reject("实际库存结果缺失或仍受保护")
		actual, expected = (
			Decimal(str(rows[0].indented_qty)),
			Decimal(str(flt(current.get((item, warehouse), 0)))),
		)
		# Native MariaDB Float columns store decimal(21,9). This is a storage
		# postcheck only, not a second demand/projected/reservation calculation.
		if actual.quantize(Decimal("0.000000001")) != expected.quantize(Decimal("0.000000001")):
			_reject("当前需求与实际库存未一致提交")
		bins.append(
			{"name": rows[0].name, "item_code": item, "warehouse": warehouse, "indented_qty": str(actual)}
		)
	claim = _claim_identities(records)
	encoded, total = [], 0
	for record in records:
		fresh = _load(_headers(name=record.name))[0]
		if (fresh.generation, fresh.body, fresh.user, fresh.pairs) != (
			record.generation,
			record.body,
			record.user,
			record.pairs,
		):
			_reject("固定认领身份已改变")
		output = encode(
			{
				"schema": 1,
				"generation": record.generation,
				"user": record.user,
				"body_sha256": hashlib.sha256(record.body.encode()).hexdigest(),
				"bins": [row for row in bins if (row["item_code"], row["warehouse"]) in record.pairs],
				"nonstock_pairs": [
					row for row in nonstock if (row["item_code"], row["warehouse"]) in record.pairs
				],
				"claim": claim,
				"claim_names": [entry[0] for entry in claim],
				"claim_digest": hashlib.sha256(encode(claim).encode()).hexdigest(),
				"source": source,
				"connection_id": state.connection_id,
				"execution_id": state.execution_id,
				"epoch": state.epoch,
			}
		)
		size = len(output.encode("utf-8"))
		if size > MAX_BODY_BYTES:
			_reject("输出正文超过安全上限")
		total += size
		if total > MAX_TOTAL_BYTES:
			_reject("输出正文总字节超过安全上限")
		encoded.append((record.name, output))  # retained encodings never exceed the existing 16 MiB budget
	for name, output in encoded:  # all fixed receipts validated before the first canonical ACK write
		frappe.db.set_value(
			"Integration Request",
			name,
			{"status": "Completed", "request_description": ACKNOWLEDGED, "output": output},
			update_modified=False,
		)


@contextmanager
def _lock(material_request_data):
	"""Borrow native lock/math, add physical fence/current reads/fixed ACK."""
	install()
	import mes_integration.mes_integration.material_request as mes

	from . import purchase_payment_service as service

	pairs = tuple(mes.get_mes_material_request_item_warehouse_pairs(material_request_data))
	records = _claim.get()
	with boundary.execution() as state, boundary.acquire((boundary.fence_key(),)):
		if records and (
			set(pairs) != set(_stock_claim.get() or ())
			or any(record.user != frappe.session.user for record in records)
		):
			_reject("固定认领范围或执行人不一致")
		keys = _current_keys(records, pairs)
		with boundary.acquire(keys):
			for item, warehouse in pairs:
				boundary._check_bin_pending(item, warehouse)
			token = _native_reads.set(True)
			try:
				original = mes._dlp_native_intent_original_lock
				native_context = original(material_request_data)
				with (
					_native_lock_callbacks(
						state, mes._dlp_native_intent_lock_callback, native_context, original.__wrapped__
					) as owner,
					native_context,
				):
					try:
						yield
					except Exception as error:
						owner[5].append(
							error
						)  # before native finally can replace the yield body's first error
						raise
			except Exception:
				state.physical_rollback()  # native fallback swallows failure; remove every partial Bin write
				raise
			finally:
				_native_reads.reset(token)


def _sync(material_request_name, item_warehouse_pairs=None):
	"""Fixed private claim wraps, but never replaces, the installed executor."""
	install()
	import mes_integration.mes_integration.material_request as mes

	from . import purchase_operation as audit
	from . import purchase_payment_service as service

	records = _claim.get()
	if not records:
		return mes._dlp_native_intent_original_sync(material_request_name, item_warehouse_pairs)
	pairs = tuple(mes.normalize_mes_item_warehouse_pairs(item_warehouse_pairs))
	if pairs != tuple(sorted({pair for record in records for pair in record.pairs})) or any(
		record.material_request_name != material_request_name or record.user != frappe.session.user
		for record in records
	):
		_reject("固定认领范围、来源或执行人不一致")
	with boundary.execution() as state, boundary.acquire((boundary.fence_key(),)):
		token = _native_reads.set(True)
		stock_token = None
		try:
			keys = _current_keys(records, pairs)
			with boundary.acquire(keys), service.current_reads():
				source = _source_witness(records)
				current_items = {
					item: service._read("Item", item, {"is_stock_item", "modified"}) for item, _ in pairs
				}
				stock_pairs = tuple(pair for pair in pairs if current_items[pair[0]].is_stock_item == 1)
				nonstock = []
				for item, warehouse in pairs:
					if (item, warehouse) in stock_pairs:
						continue
					actual = current_items[item]
					if actual.is_stock_item != 0 or frappe.db.get_values(
						"Bin", {"item_code": item, "warehouse": warehouse}, "name", limit=1, for_update=True
					):
						_reject("非库存物料资格无效或仍有库存残留")
					nonstock.append(
						{
							"item_code": item,
							"warehouse": warehouse,
							"item_name": actual.name,
							"item_modified": str(actual.modified),
							"is_stock_item": 0,
							"native_write_eligible": False,
						}
					)
				stock_token = _stock_claim.set(stock_pairs)
				result = mes._dlp_native_intent_original_sync(material_request_name, item_warehouse_pairs)
				_ack(records, stock_pairs, state, nonstock, source)
				return result
		except Exception as error:
			# Include failures before native lock yields (including ER_CHECKREAD).
			# Use the existing physical protocol; preserve the first exception.
			state.physical_rollback()
			try:
				audit.runtime_log(
					{
						"operation_id": records[0].name,
						"user": records[0].user,
						"documents": [
							{
								"doctype": "Integration Request",
								"name": record.name,
								"generation": record.generation,
							}
							for record in records
						],
					},
					"native_bin_sync_failed",
					error,
				)
			except Exception:
				state.log("native_intent_diagnostic_failed")
			raise
		finally:
			if stock_token is not None:
				_stock_claim.reset(stock_token)
			_native_reads.reset(token)


def _lock_trampoline(material_request_data):
	with _dlp_native_intent_lock(material_request_data):
		yield


def _producer_trampoline(material_request, mr_item_rows=None):
	# Code is installed in the exact audited native function object so aliases
	# retain identity; the application-owned dispatcher is its one new global.
	return _dlp_native_intent_producer(material_request, mr_item_rows)


def _sync_trampoline(material_request_name, item_warehouse_pairs=None):
	return _dlp_native_intent_sync(material_request_name, item_warehouse_pairs)


def _release_trampoline(lock_names):
	return _dlp_native_intent_release(lock_names)


def _retention_trampoline(days=30):
	return _dlp_purchase_retention_delete(days=days)


def _compaction_trampoline(doctype, days=90):
	return _dlp_purchase_retention_compaction(doctype, days=days)


def _enqueue_trampoline(
	self,
	func,
	args=None,
	kwargs=None,
	timeout=None,
	result_ttl=None,
	ttl=None,
	failure_ttl=None,
	description=None,
	depends_on=None,
	job_id=None,
	at_front=False,
	meta=None,
	retry=None,
	repeat=None,
	on_success=None,
	on_failure=None,
	on_stopped=None,
	pipeline=None,
):
	return _dlp_native_intent_enqueue(
		self,
		func,
		args=args,
		kwargs=kwargs,
		timeout=timeout,
		result_ttl=result_ttl,
		ttl=ttl,
		failure_ttl=failure_ttl,
		description=description,
		depends_on=depends_on,
		job_id=job_id,
		at_front=at_front,
		meta=meta,
		retry=retry,
		repeat=repeat,
		on_success=on_success,
		on_failure=on_failure,
		on_stopped=on_stopped,
		pipeline=pipeline,
	)


def _enqueue(queue, func, **options):
	"""NEW RQ jobs only; native dedup returns before reaching this seam."""
	import rq.queue as rq_queue

	from .purchase_operation import encode

	records = _claim.get()
	if records:
		from frappe.utils.background_jobs import create_job_id, generate_qname

		payload = options.get("kwargs") or {}
		pairs = tuple(sorted({pair for record in records for pair in record.pairs}))
		if (
			func != "frappe.utils.background_jobs.execute_job"
			or options.get("args") is not None
			or payload.get("site") != frappe.local.site
			or payload.get("user") != records[0].user
			or payload.get("method") != METHOD
			or queue.name != generate_qname("short")
			or options.get("job_id") != create_job_id(records[0].facts["job_id"])
			or payload.get("kwargs")
			!= {"material_request_name": records[0].material_request_name, "item_warehouse_pairs": pairs}
		):
			_reject("原生队列固定认领参数已改变")
		meta = dict(options.get("meta") or {})
		if CLAIM_META in meta:
			_reject("新队列任务认领元数据已存在")
		claim = _claim_identities(records)
		meta[CLAIM_META] = {
			"schema": 1,
			"records": claim,
			"claim_digest": hashlib.sha256(encode(claim).encode()).hexdigest(),
			"pairs": [list(pair) for pair in pairs],
			"user": records[0].user,
			"site": frappe.local.site,
			"database": frappe.db.cur_db_name,
			"material_request_name": records[0].material_request_name,
			"method": METHOD,
			"job_id": records[0].facts["job_id"],
			"native_source_sha256": NATIVE_SHA256,
		}
		options["meta"] = meta  # never a Frappe method kwarg or an existing Job mutation
	return rq_queue._dlp_native_intent_original_enqueue(queue, func, **options)


def _clear_log_table(doctype, days=90):
	import frappe.core.doctype.log_settings.log_settings as log_settings

	if doctype == "Integration Request":
		# Before native try/sql_ddl (which commits before CREATE and also drops
		# a temporary table in except). Reject even an empty current namespace.
		frappe.throw(
			_i18n("原生库存同步审计不支持按创建时间重建日志表，请使用受保护的常规日志清理"), frappe.PermissionError
		)
	return log_settings._dlp_purchase_retention_original(doctype, days=days)


def _code_named(code, name):
	for child in code.co_consts:
		if isinstance(child, CodeType):
			if child.co_name == name:
				return child
			result = _code_named(child, name)
			if result is not None:
				return result


def _install_retention(*, strict):
	import frappe.core.doctype.log_settings.log_settings as logs
	import frappe.integrations.doctype.integration_request.integration_request as ir

	from .purchase_operation import ProcurementAuditRetention

	candidates = (
		(
			ir,
			ir.IntegrationRequest.clear_old_logs,
			"clear_old_logs",
			_retention_trampoline,
			ProcurementAuditRetention.clear_old_logs,
			"_dlp_purchase_retention_delete",
			RETENTION_SHA256[0],
		),
		(
			logs,
			logs.clear_log_table,
			"clear_log_table",
			_compaction_trampoline,
			_clear_log_table,
			"_dlp_purchase_retention_compaction",
			RETENTION_SHA256[1],
		),
	)
	prepared = []
	for module, native, name, trampoline, adapter, global_name, source_hash in candidates:
		source = Path(module.__file__).read_bytes()
		expected = _code_named(compile(source, module.__file__, "exec", dont_inherit=True), name)
		original = getattr(module, "_dlp_purchase_retention_original", None)
		installed = original is not None
		defaults = (30,) if name == "clear_old_logs" else (90,)
		if (
			hashlib.sha256(source).hexdigest() != source_hash
			or not _audited_function(
				native, trampoline.__code__ if installed else expected, module.__dict__, defaults
			)
			or (
				installed
				and (
					getattr(module, "_dlp_purchase_retention_callables", None) != (native, original)
					or not _audited_function(original, expected, module.__dict__, defaults)
					or getattr(module, global_name, None) is not adapter
				)
			)
		):
			if strict:
				_reject("原生日志保留调用身份未经核查")
			return False
		prepared.append((module, native, trampoline, adapter, global_name, installed))
	for module, native, trampoline, adapter, global_name, installed in prepared:
		if not installed:
			module._dlp_purchase_retention_original = FunctionType(
				native.__code__, native.__globals__, native.__name__, native.__defaults__, native.__closure__
			)
			setattr(module, global_name, adapter)
			native.__code__ = trampoline.__code__
			module._dlp_purchase_retention_callables = (native, module._dlp_purchase_retention_original)
	return True


def _install_queue(*, strict):
	import rq.queue as module

	native = module.Queue.enqueue_call
	source = Path(module.__file__).read_bytes()
	expected = _code_named(compile(source, module.__file__, "exec", dont_inherit=True), "enqueue_call")
	original = getattr(module, "_dlp_native_intent_original_enqueue", None)
	installed = original is not None
	defaults = (None,) * 9 + (False,) + (None,) * 7
	if (
		hashlib.sha256(source).hexdigest() != RQ_QUEUE_SHA256
		or not _audited_function(
			native, _enqueue_trampoline.__code__ if installed else expected, module.__dict__, defaults
		)
		or (
			installed
			and (
				getattr(module, "_dlp_native_intent_queue_callables", None) != (native, original)
				or not _audited_function(original, expected, module.__dict__, defaults)
				or getattr(module, "_dlp_native_intent_enqueue", None) is not _enqueue
			)
		)
	):
		if strict:
			_reject("原生队列调用身份未经核查")
		return False
	if not installed:
		module._dlp_native_intent_original_enqueue = FunctionType(
			native.__code__, native.__globals__, native.__name__, native.__defaults__, native.__closure__
		)
		module._dlp_native_intent_enqueue = _enqueue
		native.__code__ = _enqueue_trampoline.__code__
		module._dlp_native_intent_queue_callables = (native, module._dlp_native_intent_original_enqueue)
	return True


def _audited_function(function, code, namespace, defaults=None, *, request_cache_getter=None):
	if request_cache_getter is not None:
		# Only the fixed Frappe wrapper, with an independently frozen getter.
		# Candidate attributes cannot provide either expected namespace or code.
		if (
			not isinstance(function, FunctionType)
			or function.__globals__ is not _request_cache_namespace
			or function.__defaults__ is not None
			or function.__kwdefaults__ is not None
			or function.__code__.co_freevars != ("func",)
			or function.__closure__ is None
			or len(function.__closure__) != 1
			or getattr(function, "__wrapped__", None) is not request_cache_getter
		):
			return False
		try:
			if function.__closure__[0].cell_contents is not request_cache_getter:
				return False
			source = _request_cache_source.read_bytes()
		except (ValueError, OSError):  # empty cell or unavailable pinned source
			return False
		if hashlib.sha256(source).hexdigest() != REQUEST_CACHE_SHA256:
			return False
		compiled = compile(source, str(_request_cache_source), "exec", dont_inherit=True)
		parent = next(
			child
			for child in compiled.co_consts
			if isinstance(child, CodeType) and child.co_name == "request_cache"
		)
		expected_wrapper = next(
			child for child in parent.co_consts if isinstance(child, CodeType) and child.co_name == "wrapper"
		)
		return function.__code__ == expected_wrapper and _audited_function(
			request_cache_getter, code, namespace, defaults
		)
	return (
		isinstance(function, FunctionType)
		and function.__code__ == code
		and function.__globals__ is namespace
		and function.__closure__ is None
		and function.__defaults__ == defaults
		and (
			defaults is None
			or all(
				type(actual) is type(expected)
				for actual, expected in zip(function.__defaults__, defaults, strict=False)
			)
		)
		and function.__kwdefaults__ is None
	)


def _audited_context(wrapper, function):
	reference = contextmanager(_audited_function)
	return (
		isinstance(wrapper, FunctionType)
		and wrapper.__code__ is reference.__code__
		and wrapper.__globals__ is reference.__globals__
		and wrapper.__defaults__ is None
		and wrapper.__kwdefaults__ is None
		and getattr(wrapper, "__wrapped__", None) is function
		and wrapper.__closure__ is not None
		and len(wrapper.__closure__) == 1
		and wrapper.__closure__[0].cell_contents is function
	)


def install(*, strict=True):
	# One serialized installation, including retention and queue adaptation.
	with _install_lock:
		frappe.local.purchase_native_intent_capability = "unsupported"
		mes = sys.modules.get(METHOD.rsplit(".", 1)[0])
		if mes is not None:
			mes._dlp_native_intent_capability = False
		return _install(strict=strict)  # only complete audited installation publishes supported


def _install(*, strict=True):
	"""Fixed optional MES producer; preserve controller MRO/captured aliases."""
	with _install_lock:
		if not _install_retention(strict=strict):
			return False
	if (
		"mes_integration.mes_integration.material_request.MESMaterialRequestPerformanceMixin"
		not in frappe.get_hooks("extend_doctype_class", {}).get("Material Request", [])
	):
		frappe.local.purchase_native_intent_capability = "optional_absent"
		if strict:
			_reject("原生 MES 能力未安装")
		return False
	import mes_integration.mes_integration.material_request as mes

	def unsupported(reason):
		mes._dlp_native_intent_capability = False
		frappe.local.purchase_native_intent_capability = "unsupported"
		if strict:
			_reject(reason)
		return False

	with _install_lock:
		source = Path(mes.__file__).read_bytes()
		if hashlib.sha256(source).hexdigest() != NATIVE_SHA256:
			return unsupported("原生版本未经核查")
		if not _install_queue(strict=strict):
			return unsupported("原生队列调用身份未经核查")
		compiled = compile(source, mes.__file__, "exec", dont_inherit=True)
		from frappe.query_builder import Case
		from frappe.query_builder.functions import Sum
		from frappe.utils import flt

		dependencies = (
			"enqueue_mes_material_request_bin_sync_job",
			"get_mes_material_request_item_warehouse_pairs",
			"get_stock_item_warehouse_pairs",
			"normalize_mes_item_warehouse_pairs",
			"get_mes_indented_qty_map",
		)
		saved_dependencies = getattr(mes, "_dlp_native_intent_dependencies", {})
		for name in dependencies:
			function = getattr(mes, name, None)
			defaults = (None,) if name in dependencies[:2] else None
			if not _audited_function(function, _code_named(compiled, name), mes.__dict__, defaults) or (
				saved_dependencies and saved_dependencies.get(name) is not function
			):
				return unsupported("原生依赖调用身份未经核查")
		linked = {"frappe": frappe, "hashlib": hashlib, "Case": Case, "Sum": Sum, "flt": flt}
		if (
			any(getattr(mes, name, None) is not value for name, value in linked.items())
			or mes.MES_BIN_LOCK_TIMEOUT_SECONDS != 180
			or mes.MES_INWARD_MATERIAL_REQUEST_TYPES
			!= ("Purchase", "Manufacture", "Customer Provided", "Material Transfer")
		):
			return unsupported("原生依赖调用身份或关联常量已改变")
		expected = next(
			code
			for code in compiled.co_consts
			if isinstance(code, CodeType) and code.co_name == "enqueue_mes_material_request_bin_sync"
		)
		expected_lock = next(
			code
			for code in compiled.co_consts
			if isinstance(code, CodeType) and code.co_name == "lock_mes_material_request_bins"
		)
		expected_sync = next(
			code
			for code in compiled.co_consts
			if isinstance(code, CodeType) and code.co_name == "sync_material_request_bins"
		)
		expected_release = _code_named(compiled, "release_mes_material_request_locks")
		native = mes.enqueue_mes_material_request_bin_sync
		native_lock = getattr(mes.lock_mes_material_request_bins, "__wrapped__", None)
		native_sync = mes.sync_material_request_bins
		native_release = mes.release_mes_material_request_locks
		functions = (native, native_lock, native_sync, native_release)
		current_objects = (
			native,
			mes.lock_mes_material_request_bins,
			native_lock,
			native_sync,
			native_release,
		)
		expected_codes = (expected, expected_lock, expected_sync, expected_release)
		defaults = ((None,), None, (None,), None)
		if not _audited_context(mes.lock_mes_material_request_bins, native_lock):
			return unsupported("生产者或执行者调用身份未经核查")
		if getattr(mes, "_dlp_native_intent_installed", False):
			original_lock = getattr(mes, "_dlp_native_intent_original_lock", None)
			originals = (
				getattr(mes, "_dlp_native_intent_original_producer", None),
				getattr(original_lock, "__wrapped__", None),
				getattr(mes, "_dlp_native_intent_original_sync", None),
				getattr(mes, "_dlp_native_intent_original_release", None),
			)
			original_objects = (originals[0], original_lock, originals[1], originals[2], originals[3])
			frozen = getattr(mes, "_dlp_native_intent_callables", ())
			adapted_codes = tuple(
				function.__code__
				for function in (
					_producer_trampoline,
					_lock_trampoline,
					_sync_trampoline,
					_release_trampoline,
				)
			)
			if (
				len(frozen) != 10
				or any(
					actual is not saved
					for actual, saved in zip(current_objects + original_objects, frozen, strict=False)
				)
				or not _audited_context(original_lock, originals[1])
				or any(
					not _audited_function(function, code, mes.__dict__, default)
					for function, code, default in zip(functions, adapted_codes, defaults, strict=False)
				)
				or any(
					not _audited_function(function, code, mes.__dict__, default)
					for function, code, default in zip(originals, expected_codes, defaults, strict=False)
				)
				or any(
					getattr(mes, "_dlp_native_intent_" + name, None) is not adapter
					for name, adapter in (
						("producer", register),
						("lock", _lock),
						("sync", _sync),
						("release", _release),
					)
				)
			):
				return unsupported("生产者调用身份已改变")
			mes._dlp_native_intent_capability = True
			frappe.local.purchase_native_intent_capability = "supported"
			return True
		if any(
			not _audited_function(function, code, mes.__dict__, default)
			for function, code, default in zip(functions, expected_codes, defaults, strict=False)
		):
			return unsupported("生产者调用身份未经核查")
		mes._dlp_native_intent_original_producer = FunctionType(
			native.__code__, native.__globals__, native.__name__, native.__defaults__, native.__closure__
		)
		mes._dlp_native_intent_producer = register
		mes._dlp_native_intent_original_lock = contextmanager(
			FunctionType(
				native_lock.__code__,
				native_lock.__globals__,
				native_lock.__name__,
				native_lock.__defaults__,
				native_lock.__closure__,
			)
		)
		mes._dlp_native_intent_lock = _lock
		mes._dlp_native_intent_original_sync = FunctionType(
			native_sync.__code__,
			native_sync.__globals__,
			native_sync.__name__,
			native_sync.__defaults__,
			native_sync.__closure__,
		)
		mes._dlp_native_intent_sync = _sync
		mes._dlp_native_intent_original_release = FunctionType(
			native_release.__code__,
			native_release.__globals__,
			native_release.__name__,
			native_release.__defaults__,
			native_release.__closure__,
		)
		mes._dlp_native_intent_release = _release
		mes._dlp_native_intent_lock_callback = _code_named(expected_lock, "<lambda>")
		native_lock.__code__ = _lock_trampoline.__code__
		native.__code__ = _producer_trampoline.__code__
		native_sync.__code__ = _sync_trampoline.__code__
		native_release.__code__ = _release_trampoline.__code__
		mes._dlp_native_intent_dependencies = {name: getattr(mes, name) for name in dependencies}
		original_lock = mes._dlp_native_intent_original_lock
		mes._dlp_native_intent_callables = (
			*current_objects,
			mes._dlp_native_intent_original_producer,
			original_lock,
			original_lock.__wrapped__,
			mes._dlp_native_intent_original_sync,
			mes._dlp_native_intent_original_release,
		)
		mes._dlp_native_intent_installed = True  # publish only after the complete serialized adaptation
		mes._dlp_native_intent_capability = True
		frappe.local.purchase_native_intent_capability = "supported"
		return True


if TYPE_CHECKING:
	# Native modules receive these names during audited installation. No
	# application-runtime fallback is created for the trampolines.
	from .purchase_operation import ProcurementAuditRetention

	_dlp_native_intent_lock = _lock
	_dlp_native_intent_producer = register
	_dlp_native_intent_sync = _sync
	_dlp_native_intent_release = _release
	_dlp_purchase_retention_delete = ProcurementAuditRetention.clear_old_logs
	_dlp_purchase_retention_compaction = _clear_log_table
	_dlp_native_intent_enqueue = _enqueue
