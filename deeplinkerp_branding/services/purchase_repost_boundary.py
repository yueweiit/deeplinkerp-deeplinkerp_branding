"""Same-business-session leases and earliest native purchase range boundary.

No acceptance, queue, executor or valuation algorithm lives here. Transaction
cleanup observes the *actual SQL boundary*, not Frappe's resettable callbacks.
Instance wrappers leave native database/controller implementations unchanged.
"""

from __future__ import annotations

from frappe import _ as _i18n


import hashlib
import inspect
import json
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field

import frappe

MAX_EPOCH_RECEIPT_BYTES = 4 * 1024 * 1024


def _reject(reason):
	frappe.throw("采购库存保护：" + reason, frappe.ValidationError)


def _known_database(db):
	# Detect without connecting or requiring an optional MySQLdb installation
	# on unrelated native sites. The protected entry still refuses any other
	# adapter; this is not a compatibility/async capability grant.
	if (type(db).__module__, type(db).__name__) != ("frappe.database.mariadb.mysqlclient", "MariaDBDatabase"):
		return False
	try:
		from frappe.database.mariadb.mysqlclient import MariaDBDatabase
	except ImportError:
		return False
	return type(db) is MariaDBDatabase


def _supported(db, connection):
	if not _known_database(db):
		return False
	from frappe.database.mariadb.mysqlclient import MariaDBDatabase
	from MySQLdb.connections import Connection

	return type(db) is MariaDBDatabase and isinstance(connection, Connection)


@dataclass
class Session:
	db: object
	connection: object
	cursor: object
	connection_id: int
	execution_id: str = field(default_factory=lambda: str(uuid.uuid4()))
	poisoned: bool = False
	depth: int = 0
	epoch: int = 0
	locks: dict[str, int] = field(default_factory=dict)
	references: dict[str, int] = field(default_factory=dict)
	native_acquisitions: dict[str, tuple[str, int]] = field(default_factory=dict)
	transaction_calls: list[dict] = field(default_factory=list)
	sql_sequence: int = 0
	begin_expected: bool = False
	# Dormant private transaction-participant protocol, not business authority:
	# prepare(identity) -> bounded JSON receipt; promote(detached receipt);
	# invalidate(); resolve(identity, immutable candidate bytes) -> exact bytes
	# or None. promote/invalidate are memory-only; neither can start SQL or a
	# transaction method. The later owned native adapter supplies identity/lease/input
	# authority; purchase_native_repost installs it only for an accepted task.
	receipt_participant: object = None
	receipt_phase: str | None = None
	receipt_resolution: dict | None = None

	def invalidate_receipts(self):
		if self.receipt_participant is not None:
			previous_phase = self.receipt_phase
			self.receipt_phase = "invalidate"
			try:
				self.receipt_participant.invalidate()
			except Exception:
				# Invalid evidence must never survive a failed cleanup. Keep
				# the original error and stop this physical execution.
				self.receipt_participant = None
				self.poison("receipt_invalidation_failed")
			finally:
				self.receipt_phase = previous_phase

	def log(self, event):
		# No document payload, credentials or native exception string in logs.
		try:
			frappe.logger("purchase_operation", allow_site=True).error(
				json.dumps(
					{
						"event": event,
						"execution_id": self.execution_id,
						"connection_id": self.connection_id,
						"epoch": self.epoch,
						"active": bool(self.depth),
						"lease_count": len(self.locks),
					}
				)
			)
		except Exception:
			pass  # diagnostics can never prevent physical cleanup/replace error

	def poison(self, event):
		if not self.poisoned:
			self.poisoned = True
			self.invalidate_receipts()
			self.log(event)
			try:
				self.connection.close()  # server releases all session locks
			except Exception:
				self.log("physical_close_failed")
			self.locks.clear()
			self.references.clear()
			self.native_acquisitions.clear()

	def verify(self):
		# BEFORE any SQL can trigger Frappe's lazy connect. Native unbuffered
		# cursors are permitted only when bound to this exact physical owner.
		cursor = self.db._cursor
		if self.poisoned:
			_reject("当前连接执行已失效，请重新发起请求")
		if (
			self.db._conn is not self.connection
			or cursor is None
			or getattr(cursor, "connection", None) is not self.connection
		):
			self.poison("physical_identity_changed")
			_reject("数据库连接身份已改变，请重新发起请求")
		try:
			if self.connection.thread_id() != self.connection_id:
				self.poison("server_identity_changed")
				_reject("数据库会话身份已改变，请重新发起请求")
		except frappe.ValidationError:
			raise
		except Exception:
			self.poison("physical_identity_unavailable")
			_reject("数据库连接已中断，请重新发起请求")

	def authority(self):
		# All normal protected entries (sql/initialize/execution/acquire)
		# meet here BEFORE raw lease SQL can bypass the sql wrapper.
		if self.receipt_phase in ("promote", "invalidate"):
			_reject("回执内存清理不能执行数据库语句")
		self.verify()
		keys = sorted(self.locks)
		for offset in range(0, len(keys), 128):
			batch = keys[offset : offset + 128]
			owners = self.raw("SELECT " + ", ".join("IS_USED_LOCK(%s)" for _ in batch), tuple(batch))[0]
			if len(owners) != len(batch) or any(owner != self.connection_id for owner in owners):
				self.poison("lease_authority_lost")
				_reject("库存保护租约已失效，请重新发起请求")

	def raw(self, query, values):
		"""All authority SQL shares the poison rule; never lazy reconnect."""
		self.verify()
		try:
			return self.native_sql(query, values)
		except Exception:
			self.poison("lease_authority_unavailable")
			raise

	def transaction_end(self):
		self.epoch += 1
		self.begin_expected = True
		# Active native execution can commit/rollback multiple chunks. A dormant
		# lease belongs to the previous SQL epoch, never a later after_commit
		# callback's newly acquired lease.
		if not self.depth:
			self.release(tuple(self.locks))
			self.receipt_participant = None

	def release(self, keys):
		self.verify()
		for key in sorted(keys, reverse=True):
			tokens = tuple(token for token, (name, _) in self.native_acquisitions.items() if name == key)
			if tokens:
				self.release_native(tokens)
				continue
			if self.raw("SELECT RELEASE_LOCK(%s)", (key,))[0][0] != 1:
				self.poison("lease_release_failed")
				_reject("库存保护租约释放失败，请重新发起请求")
			self.locks.pop(key, None)
			self.references.pop(key, None)

	def adopt_native(self, key):
		"""One actual original GET_LOCK success, in this same physical epoch."""
		self.verify()
		token = uuid.uuid4().hex
		self.native_acquisitions[token] = (key, self.epoch)
		self.locks.setdefault(key, self.epoch)
		return token

	def release_native(self, tokens):
		self.authority()
		for token in tokens:
			acquisition = self.native_acquisitions.get(token)
			if acquisition is None:
				continue  # consumed old callback cannot touch a newer same-name token
			key, epoch = acquisition
			if key not in self.locks or epoch > self.epoch:
				self.poison("native_lease_identity_changed")
				_reject("原生库存租约认领身份已改变")
			if self.raw("SELECT RELEASE_LOCK(%s)", (key,))[0][0] != 1:
				self.poison("native_lease_release_failed")
				_reject("原生库存租约释放失败")
			self.native_acquisitions.pop(token)
			remaining = [held_epoch for name, held_epoch in self.native_acquisitions.values() if name == key]
			if remaining:
				self.locks[key] = min(remaining)
			else:
				self.locks.pop(key, None)
				self.references.pop(key, None)

	def physical_rollback(self):
		try:
			self.verify()
			# Same queue reset responsibility as native full rollback. Never
			# carry a rolled-back operation's after_commit into a later native
			# status/error commit. Original managers still run remaining
			# rollback callbacks; any cleanup failure closes/poisons instead
			# of silently allowing follow-up writes on an uncertain session.
			self.db.before_commit.reset()
			self.db.after_commit.reset()
			self.db.before_rollback.run()
			self.verify()  # callbacks may close/poison this physical owner
			self.invalidate_receipts()
			self.verify()  # cleanup must never let native SQL lazily reconnect
			self.connection.rollback()
			self.db.transaction_writes = 0
			self.db.value_cache.clear()
			self.transaction_end()
			settled = (self.epoch, self.sql_sequence)
			self.db.after_rollback.run()
			return settled  # later callback SQL belongs to a new pending attempt
		except Exception:
			self.poison("physical_rollback_failed")

	def install_close(self):
		native_close = self.db.close

		def close():
			self.poisoned = True
			self.invalidate_receipts()
			try:
				if self.db._conn is self.connection and not getattr(self.connection, "open", True):
					self.db._cursor = self.db._conn = None
					return
				return native_close()
			finally:
				# Native close does not drain callbacks. A replaced db._conn
				# must also close this original physical lease owner.
				if self.db._conn is not self.connection and getattr(self.connection, "open", True):
					try:
						self.connection.close()
					except Exception:
						self.log("physical_close_failed")
				self.locks.clear()
				self.references.clear()
				self.native_acquisitions.clear()

		self.db.close = close

	def install(self):
		self.native_sql = self.db.sql
		native_sql_signature = inspect.signature(self.native_sql)
		native_commit, native_rollback = self.db.commit, self.db.rollback

		def observe_before(manager, kind):
			from frappe.utils import CallbackManager

			run = manager.run
			state = self

			class ObservedBefore(CallbackManager):
				def run(self, *args, **kwargs):
					call = state.transaction_calls[-1] if state.transaction_calls else None
					if call and call["kind"] == kind:
						call["before"] += 1
					try:
						return run(*args, **kwargs)  # original deque/reset/drain
					finally:
						if call and call["kind"] == kind:
							call["before"] -= 1

			observed = ObservedBefore()
			observed._functions = manager._functions  # preserve existing queue/aliases
			return observed

		self.db.before_commit = observe_before(self.db.before_commit, "commit")
		self.db.before_rollback = observe_before(self.db.before_rollback, "rollback")

		def observe_after(manager):
			from frappe.utils import CallbackManager

			state = self

			class ObservedAfter(CallbackManager):
				def add(self, callback):
					from .purchase_native_intent import freeze_native_callback

					return super().add(freeze_native_callback(callback, state))

			observed = ObservedAfter()
			observed._functions = manager._functions  # preserve native queue/reset/aliases
			return observed

		self.db.after_commit = observe_after(self.db.after_commit)
		self.db.after_rollback = observe_after(self.db.after_rollback)

		def native_sql(query, *args, **kwargs):
			try:
				return self.native_sql(query, *args, **kwargs)
			except Exception as error:
				if error.args and error.args[0] in (2006, 2013, 2055):
					self.poison("database_disconnected")
				raise

		def sql(query, *args, **kwargs):
			self.authority()
			try:
				arguments = native_sql_signature.bind(query, *args, **kwargs)
			except TypeError:
				return native_sql(query, *args, **kwargs)  # native signature/error, no SQL boundary
			arguments.apply_defaults()
			# Native run/explain return before transaction execution. Bind its
			# actual signature (query/values positional; options keyword-only)
			# so previews preserve authority without changing the SQL epoch.
			if not arguments.arguments["run"] or arguments.arguments["explain"]:
				return native_sql(query, *args, **kwargs)
			raw_command = str(query).strip()
			command = (
				re.sub(
					r"\A(?:(?:/\*(?!\!).*?\*/|--[ \t][^\n]*\n|#[^\n]*\n)\s*)*", "", raw_command, flags=re.S
				)
				.strip()
				.lower()
			)
			ending = re.fullmatch(
				r"(?:commit|rollback)(?:\s+work)?(?:\s+and\s+(?:no\s+)?chain)?\s*;?", command
			)
			beginning = re.match(r"(?:begin\b|start\s+transaction\b)", command)
			if self.receipt_phase == "prepare" and (ending or beginning):
				_reject("回执写入不能结束或替换物理事务")
			if self.receipt_participant is not None and arguments.arguments["auto_commit"]:
				_reject("部分回执执行不支持自动提交选项")
			# Implicit commits/driver autocommit would bypass the proven SQL
			# epoch. Ordinary unrelated DDL remains native before leases exist.
			if self.locks or self.receipt_participant is not None:
				if "/*!" in raw_command or re.match(
					r"(?:alter|create|drop|truncate|rename|grant|revoke|analyze|optimize|repair|flush|lock\s+tables|unlock\s+tables|set\s+(?:(?:session|local|global)\s+)?(?:@@(?:session\.)?)?autocommit)\b",
					command,
				):
					_reject("持有库存租约时不支持隐式提交")
				if beginning and not self.begin_expected:
					_reject("持有库存租约时不能隐式开始替换事务")
				if (
					re.match(r"(?:commit|rollback)\b", command)
					and not ending
					and not re.match(r"rollback\s+to\b", command)
				):
					_reject("未经核查的原生事务结束方式")
			if not ending:
				self.begin_expected = False
			if self.receipt_participant is not None and re.match(
				r"(?:savepoint\b|rollback\s+to\b|release\s+savepoint\b)", command
			):
				_reject("部分回执执行不支持保存点，请使用完整事务")
			from .purchase_native_intent import (
				adapt_query,
				observe_native_lock,
				protect_native_acquisition,
				protect_native_release,
			)

			protect_native_acquisition(query, arguments.arguments["values"], self)
			protect_native_release(query, arguments.arguments["values"], self)
			query = adapt_query(query, self)
			from .purchase_native_repost import current_query

			query = current_query(query)
			self.sql_sequence += 1
			participant, prepared, candidate = self.receipt_participant, None, None
			identity = {
				"execution_id": self.execution_id,
				"connection_id": self.connection_id,
				"epoch": self.epoch,
			}
			committing = bool(ending and command.startswith("commit"))
			if ending and not committing:
				self.invalidate_receipts()
			if committing and participant is not None:
				self.receipt_phase = "prepare"
				try:
					# Native before_commit has already drained. This write is
					# still on the SAME physical transaction, including raw
					# and nested commit calls from that callback queue.
					prepared = participant.prepare(dict(identity))
					if not isinstance(prepared, dict) or any(
						prepared.get(key) != value or type(prepared.get(key)) is not type(value)
						for key, value in identity.items()
					):
						_reject("部分回执的物理执行身份无效")
					candidate = json.dumps(prepared, ensure_ascii=False, sort_keys=True).encode("utf-8")
					if len(candidate) > MAX_EPOCH_RECEIPT_BYTES:
						_reject("部分回执超过字节上限")
					# Detached immutable candidate survives poison/invalidate;
					# matching only execution/epoch is never durable proof.
					prepared = json.loads(candidate)
				except Exception:
					settled = self.physical_rollback()
					if self.transaction_calls:
						self.transaction_calls[-1]["cleaned"] = settled
					raise
				finally:
					self.receipt_phase = None
			self.verify()  # participant cleanup/preparation cannot replace this owner
			try:
				result = native_sql(query, *args, **kwargs)
			except Exception:
				if committing and participant is not None:
					# SQL may have committed before the response was lost.
					# Close the old owner BEFORE fresh read-only resolution;
					# never retry or report this transaction uncommitted.
					self.poison("commit_response_unknown")
					self.receipt_resolution = {
						**identity,
						"candidate_sha256": hashlib.sha256(candidate).hexdigest(),
						"result": "unknown",
					}
					try:
						# poison preserves the first SQL error even if close
						# fails. Never resolve/replay with an unclosed owner.
						opened = getattr(self.connection, "open", None)
						if type(opened) in (bool, int) and opened == 0:
							durable = participant.resolve(dict(identity), candidate)
							self.receipt_resolution["result"] = (
								"durable"
								if type(durable) is bytes and durable == candidate
								else ("absent" if durable is None else "mismatch")
							)
						else:
							self.log("commit_owner_close_unconfirmed")
					except Exception:
						self.log("commit_receipt_resolution_failed")
				elif ending and participant is not None:
					# A failed raw rollback cannot authorize a later control
					# commit of business writes whose evidence was discarded.
					self.poison("rollback_response_unknown")
				raise
			observe_native_lock(query, arguments.arguments["values"], result, self)
			if ending:
				# A nested/raw boundary inside before_* is not this native
				# method's SQL boundary. It may then create new pending writes
				# and fail BEFORE the enclosing commit/rollback ever executes.
				call = self.transaction_calls[-1] if self.transaction_calls else None
				if call and not call["before"] and call["kind"] == command.split()[0].rstrip(";"):
					call["completed"] = True
				if committing and participant is not None:
					self.receipt_phase = "promote"
					try:
						participant.promote(prepared)
						self.verify()
					except Exception:
						self.poison("committed_receipt_promotion_failed")
						raise
					finally:
						self.receipt_phase = None
				self.transaction_end()
			return result

		def transaction(kind, method, *args, **kwargs):
			from .purchase_reversal_progress import accepting

			if kind == "commit" and accepting() is not None:
				_reject("原生取消受理不能提前提交")
			if self.receipt_phase is not None:
				_reject("回执处理不能重入事务方法")
			self.authority()
			call = {"kind": kind, "completed": False, "before": 0, "cleaned": None}
			self.transaction_calls.append(call)
			try:
				return method(*args, **kwargs)
			except Exception:
				if not call["completed"] and not self.poisoned:
					# Earlier before_* failures may have already deleted the
					# opposite callback queue. Roll back physical pending work
					# independently, retaining active execution's session locks.
					self.log("transaction_failed_before_sql_boundary")
					if call["cleaned"] != (self.epoch, self.sql_sequence):
						self.physical_rollback()
					else:
						self.invalidate_receipts()
				raise  # preserve the first error, including after_commit
			finally:
				self.transaction_calls.pop()

		self.db.sql = sql
		self.db.commit = lambda *a, **k: transaction("commit", native_commit, *a, **k)
		self.db.rollback = lambda *a, **k: transaction("rollback", native_rollback, *a, **k)


def initialize(db=None):
	"""Handshake exactly once, before procurement locks/audit/business writes."""
	business = db is None
	db = db if db is not None else frappe.local.db
	if (
		business
		and (reference := getattr(frappe.local, "purchase_session", None)) is not None
		and reference.db is not db
	):
		reference.poison("business_database_instance_changed")
		_reject("当前业务连接已被替换，请重新发起请求")
	previous = getattr(db, "_purchase_session", None)
	if previous is not None:
		previous.authority()
		if business:
			from .purchase_native_intent import install

			install(strict=False)
		return previous
	if db.transaction_writes:
		_reject("连接未在业务写入前初始化，请重试独立请求")
	if db._conn is None:
		db.connect()
	connection, cursor = db._conn, db._cursor
	if not _supported(db, connection):
		_reject("数据库连接适配器未经核查")
	state = Session(db, connection, cursor, 0)
	# Publish the execution BEFORE its first physical probe. Even that probe
	# can hit KILL/2006/2013; catching it must not permit another same-context
	# handshake, lazy reconnect or procurement continuation.
	db._purchase_session = state
	if business:
		frappe.local.purchase_session = state
	try:
		state.install_close()  # failed first ping must also close/destroy safely
		state.connection_id = connection.thread_id()
		connection.ping(False)  # mysqlclient C API: never repeat after row locks
		state.verify()  # BEFORE CONNECTION_ID SQL could lazily reconnect
		if db.sql("SELECT CONNECTION_ID()")[0][0] != state.connection_id:
			_reject("数据库会话身份不一致")
		state.install()
		if business:
			from .purchase_native_intent import install

			install(strict=False)
	except Exception:
		state.poison("session_initialization_failed")
		raise
	return state


@contextmanager
def execution(*, db=None):
	state = initialize(db)
	state.depth += 1
	try:
		yield state
	except Exception:
		state.depth -= 1
		if not state.depth and not state.poisoned:
			try:
				state.db.rollback()
			except Exception:
				state.log("execution_abort_cleanup_failed")
		raise
	else:
		state.depth -= 1


@contextmanager
def acquire(keys, *, db=None):
	"""Nonblocking sorted set; only this acquisition's new locks roll back."""
	state = initialize(db)
	if not state.depth:
		_reject("租约必须属于明确的外层执行")
	keys = tuple(sorted(set(keys)))
	acquired = []
	try:
		state.authority()
		for key in keys:
			if key not in state.locks:
				if state.raw("SELECT GET_LOCK(%s, 0)", (key,))[0][0] != 1:
					_reject("相关库存正在办理，请稍后重试")
				state.locks[key] = state.epoch
				acquired.append(key)
		for key in keys:
			state.references[key] = state.references.get(key, 0) + 1
	except Exception:
		if not state.poisoned:
			try:
				state.release(acquired)
			except Exception:
				state.log("partial_acquisition_cleanup_failed")
		raise
	try:
		yield state
	finally:
		for key in keys:
			if key in state.references:
				state.references[key] -= 1
		# Physical locks remain active until outer execution exits, then dormant
		# until an actual commit/full rollback. Never release at method return.


def lock_key(kind, *identity):
	payload = (frappe.local.site, frappe.db.cur_db_name, kind, *identity)
	return "dlpr:" + hashlib.sha256(json.dumps(payload, ensure_ascii=True).encode()).hexdigest()[:59]


POINTER = "custom_purchase_reversal_operation"
POINTER_TYPES = (
	"Bin",
	"Purchase Order",
	"Purchase Receipt",
	"Purchase Invoice",
	"Payment Entry",
	"Repost Item Valuation",
)
PENDING_TYPES = POINTER_TYPES[:-1]  # RIV generation ownership is permanent, not a pending source freeze.


def _creates_identity(doc, *, inserting=False):
	# Native insert ALWAYS names a new identity. Native _save routes to insert
	# for __islocal or a missing name (Document558); an incoming stored name is
	# not authority to carry that identity's owner through either path.
	return inserting or bool(doc.get("__islocal") or not doc.get("name"))


def check_pointer(doc, old, *, creating=False):
	if doc.doctype not in POINTER_TYPES:
		return

	def value(source):
		raw = source.get(POINTER) if source else None
		# Native Link serialization preserves 0/False as varchar "0". Only
		# actual null/empty-string values are empty, for new AND stored saves.
		return None if raw is None or raw == "" else raw

	incoming, previous = value(doc), value(old)
	if creating:
		if incoming is not None:
			frappe.throw(_i18n("新单据不能继承库存保护指针"), frappe.PermissionError)
		return
	if incoming != previous:
		frappe.throw(_i18n("库存保护指针不能由普通单据写入、清除或替换"), frappe.PermissionError)


def fence_key():
	# Opaque external lifecycle writers can cross company via native linked
	# records. A site fence/absence proof is honest; self.company alone is not.
	# Future pending registration AND final source/Bin unlock must borrow this
	# SAME fence before their sorted document/pair keys. B2 grants no publisher.
	return lock_key("pending-publication-fence")


def _pending_absent():
	for doctype in PENDING_TYPES:
		if frappe.db.get_values(doctype, {POINTER: ["is", "set"]}, ["name"], limit=1, for_update=True):
			_reject("存在待完成库存保护，未核查的原生关联路径暂缓办理")


def _scope_keys(scopes, roots):
	identities = {(doc.doctype, doc.name) for doc in roots if doc.name}
	result = set()
	for scope in scopes:
		identities.update((row.doctype, row.name) for row in (*scope.sources, *scope.vouchers))
		result.update(lock_key("pair", scope.company, pair.item_code, pair.warehouse) for pair in scope.pairs)
	for doctype, name in identities:
		result.add(lock_key("document", doctype, name))
	return result


def _check_pending(scopes, roots):
	from .purchase_reversal_scope import MAX_VOUCHERS

	identities = {(doc.doctype, doc.name) for doc in roots if doc.name}
	for scope in scopes:
		identities.update((row.doctype, row.name) for row in (*scope.sources, *scope.vouchers))
		for pair in scope.pairs:
			_check_bin_pending(pair.item_code, pair.warehouse)
	by_type = {}
	for doctype, name in sorted(identities):
		if doctype in PENDING_TYPES:
			by_type.setdefault(doctype, []).append(name)
	for doctype, names in by_type.items():
		rows = frappe.db.get_values(
			doctype,
			{"name": ["in", names], POINTER: ["is", "set"]},
			["name"],
			limit=MAX_VOUCHERS + 1,
			for_update=True,
		)
		if rows:
			_reject("相关采购来源待完成，请稍后重试")


def _check_bin_pending(item_code, warehouse):
	rows = frappe.db.get_values(
		"Bin",
		{"item_code": item_code, "warehouse": warehouse},
		["name", POINTER],
		as_dict=True,
		limit=2,
		for_update=True,
	)
	if len(rows) > 1:
		_reject("实际库存 Bin 重复")
	from .purchase_reversal_progress import allows

	if any(row.get(POINTER) and not allows(row.get(POINTER)) for row in rows):
		_reject("相关库存范围待完成，请稍后重试")


def _opaque(doc, *, current=False):
	"""Capability classification, not actor-controlled authorization flags."""
	from . import purchase_payment_service as service
	from .purchase_reversal_scope import _fields

	_fields(
		doc.doctype,
		{
			"apply_putaway_rule",
			"from_warehouse",
			"to_warehouse",
			"set_warehouse",
			"is_old_subcontracting_flow",
			"is_subcontracted",
			"subcontracting_order",
		},
	)
	# Native BuyingController.validate derives supplied rows late. Incoming
	# supplied_items cannot prove the final reservation footprint (BC64-66).
	if doc.doctype in ("Purchase Order", "Purchase Receipt", "Purchase Invoice") and (
		doc.get("is_old_subcontracting_flow")
		or doc.get("is_subcontracted")
		or doc.get("subcontracting_order")
	):
		return True
	if doc.get("apply_putaway_rule") and (
		doc.doctype == "Purchase Receipt"
		or (doc.doctype == "Stock Entry" and doc.get("purpose") in ("Material Receipt", "Material Transfer"))
	):
		return True  # SE220-232 / PR246-249: do not run/copy putaway math.
	if _stock_relevant(doc):
		for table in ("items", "packed_items"):
			for row in doc.get(table) or []:
				_fields(
					row.doctype,
					{"item_code", "warehouse", "s_warehouse", "t_warehouse", "delivered_by_supplier"},
					doc.doctype,
				)
				if not row.get("item_code") or row.get("delivered_by_supplier"):
					continue
				item = service._read("Item", row.item_code, {"is_stock_item"})
				if not item.is_stock_item:
					continue
				if doc.doctype == "Stock Entry":
					# Native validate_warehouse may fill missing mandatory ends
					# from headers. Classification only, not its calculations.
					source = doc.purpose in (
						"Material Issue",
						"Material Transfer",
						"Send to Subcontractor",
						"Material Transfer for Manufacture",
						"Material Consumption for Manufacture",
						"Return Raw Material to Customer",
						"Subcontracting Delivery",
					)
					target = doc.purpose in (
						"Material Receipt",
						"Material Transfer",
						"Send to Subcontractor",
						"Material Transfer for Manufacture",
						"Receive from Customer",
						"Subcontracting Return",
					)
					if (
						not (row.get("s_warehouse") or row.get("t_warehouse"))
						or (source and not row.get("s_warehouse"))
						or (target and not row.get("t_warehouse"))
					):
						return True
				elif doc.doctype in (
					"Purchase Order",
					"Purchase Receipt",
					"Purchase Invoice",
					"Material Request",
					"Delivery Note",
					"Sales Invoice",
					"Stock Reconciliation",
				) and not row.get("warehouse"):
					return True  # late native warehouse fill is not an empty scope
	if doc.doctype == "Material Request":
		if (
			"mes_integration.mes_integration.material_request.MESMaterialRequestPerformanceMixin"
			not in frappe.get_hooks("extend_doctype_class", {}).get("Material Request", [])
		):
			return False  # native ERPNext-only site has no MES updater branch
		from mes_integration.mes_integration.material_request import is_mes_material_request

		_fields(doc.doctype, {"custom_request_source"})
		return is_mes_material_request(doc)
	if doc.doctype == "Purchase Order":
		_fields("Purchase Order Item", {"blanket_order"}, doc.doctype)
		if any(row.get("blanket_order") for row in doc.get("items") or []):
			return True  # native status/bulk delegates an unaudited external writer
	if doc.doctype == "Stock Entry":
		return bool(
			any(
				doc.get(field)
				for field in (
					"work_order",
					"job_card",
					"project",
					"asset_repair",
					"pick_list",
					"source_stock_entry",
					"subcontracting_order",
					"subcontracting_inward_order",
				)
			)
			or any(
				row.get("project")
				or (doc.get("inspection_required") and row.get("quality_inspection"))
				or row.get("scio_detail")
				or row.get("original_item")
				or row.get("allow_alternative_item")
				for row in doc.get("items") or []
			)
			or doc.get("purpose") in ("Send to Warehouse", "Receive at Warehouse")
		)
	if doc.doctype in ("Delivery Note", "Sales Invoice"):
		service._require_fields("Product Bundle", {"new_item_code", "disabled"})
		for code in sorted({row.item_code for row in doc.get("items") or [] if row.get("item_code")}):
			bundles = frappe.db.get_values(
				"Product Bundle",
				{"new_item_code": code, "disabled": 0},
				["name"],
				as_dict=True,
				limit=2,
				for_update=current,
			)
			if len(bundles) > 1:
				_reject("原生组合物料身份重复")
			if bundles:
				service._read("Product Bundle", bundles[0].name, {"new_item_code", "disabled"})
				return True  # native packing_list can replace late stock rows
		return bool(
			doc.get("is_internal_customer")
			or doc.get("inter_company_reference")
			or any(
				row.get(field)
				for row in doc.get("items") or []
				for field in (
					"sales_order",
					"so_detail",
					"delivery_note",
					"dn_detail",
					"against_sales_order",
					"si_detail",
				)
			)
		)
	return False


def _opaque_sources(scopes):
	# The shared collector already proved parent/detail/item/UOM/company.
	# Classify its real MR identities, never flags on a PO/PR or a parallel
	# copied payment/source traversal. B1 public cancellation stays strict.
	from . import purchase_payment_service as service

	for identity in sorted(
		{row for scope in scopes for row in scope.sources if row.doctype == "Material Request"}
	):
		if _opaque(service._read(identity.doctype, identity.name)):
			return True
	return False


def _stock_relevant(doc):
	from .purchase_consistency import is_procurement

	return doc.doctype not in ("Payment Entry", "Purchase Invoice", "Sales Invoice") or (
		doc.get("update_stock")
		or is_procurement(doc)
		or (
			doc.doctype == "Payment Entry"
			and any(
				row.get("reference_doctype") in ("Purchase Order", "Purchase Invoice")
				for row in doc.get("references") or []
			)
		)
	)


def _mr_backlinks(doc, *, current, collector):
	"""Real native PO/PR child links; MR does not get a seventh pointer."""
	from . import purchase_payment_service as service
	from .purchase_consistency import _native_source_row
	from .purchase_reversal_scope import MAX_VOUCHERS, DocumentIdentity, _canonical, _child_parent, _fields

	result = set()
	if doc.doctype != "Material Request" or not doc.name:
		return result
	budget = collector.budget
	source = None
	for doctype in ("Purchase Order", "Purchase Receipt"):
		service._require_fields(doctype + " Item", {"material_request", "material_request_item"}, doctype)
		known = tuple(
			sorted(identity.name for identity in budget.identities if identity.doctype == doctype)
		) or ("",)
		remaining = MAX_VOUCHERS - len(budget.identities)
		rows = frappe.db.sql(
			f"SELECT DISTINCT parent FROM `tab{doctype} Item` WHERE material_request=%s "
			"AND material_request_item IS NOT NULL AND material_request_item!='' "
			"AND parent NOT IN %s ORDER BY parent LIMIT %s" + (" FOR UPDATE" if current else ""),
			(doc.name, known, remaining + 1),
			as_dict=True,
		)
		if len(rows) > remaining:
			_reject("物料请求采购关联超过安全上限")
		targets = {
			identity: target
			for identity, target in budget.documents.items()
			if identity.doctype == doctype
			and any(
				row.get("material_request") == doc.name and row.get("material_request_item")
				for row in target.get("items") or []
			)
		}
		for row in rows:
			identity = DocumentIdentity(doctype, row.parent)
			targets[identity] = service._read(doctype, row.parent, {"company", "items"})
		for identity, target in targets.items():
			_canonical(target, opaque=collector.opaque)
			if target.company != doc.company:
				_reject("物料请求采购关联公司不一致")
			budget.identity(identity)
			budget.documents.setdefault(identity, target)
			for row in target.get("items") or []:
				if row.get("material_request") == doc.name and row.get("material_request_item"):
					_child_parent(target, row, "items")
					_fields(row.doctype, {"material_request", "material_request_item"}, target.doctype)
					source = source or service._read("Material Request", doc.name)
					_native_source_row(
						target,
						row,
						source,
						row.get("material_request_item"),
						"items",
						"Material Request Item",
					)
			# Whole actual backlink rows enter evidence/lease footprint only;
			# they never seed valuation or a Cartesian GL infection graph.
			for pair in collector.document_pairs(target):
				collector.add_pair(pair.item_code, pair.warehouse)
			result.add(identity)
	return result


def _scopes(roots, *, current, opaque):
	from copy import deepcopy
	from dataclasses import replace

	from .purchase_reversal_scope import _Collector, _ScopeBudget, collect_boundary_scope

	budget = _ScopeBudget()  # one initial/current UNION, never one cap per root
	scopes = []
	for original in roots:
		doc = original
		if doc.docstatus == 2:
			# Incoming cancel state is not a new stock posting. The persisted
			# submitted root below supplies its real native cancellation range.
			doc = deepcopy(doc)
			doc.docstatus = 0
		if not _stock_relevant(doc):
			continue
		if doc.doctype == "Material Request":
			collector = _Collector(doc, current=current, opaque=opaque, boundary_gate=True, budget=budget)
			backlinks = _mr_backlinks(doc, current=current, collector=collector)
			scope = collector.result()
			scope = replace(scope, sources=tuple(sorted(set(scope.sources) | backlinks)))
		else:
			scope = collect_boundary_scope(doc, current=current, opaque=opaque, budget=budget)
		scopes.append(scope)
	return scopes


def _persisted(doc, *, current=False):
	from .purchase_payment_service import _read

	if doc.name and frappe.db.get_value(
		doc.doctype, doc.name, "name", **({"for_update": True} if current else {})
	):
		return _read(doc.doctype, doc.name)
	return None


def _proposed(doc, old):
	"""Native pure defaults/header and real Item stock-UOM truth, on a copy."""
	from copy import deepcopy

	from frappe.model.document import LazyDocument

	from . import purchase_payment_service as service

	if isinstance(doc, LazyDocument):
		from .purchase_reversal_scope import _canonical

		_canonical(doc, opaque=True)
		# Native extended-class pickle reducer reconstructs a LazyDocument as
		# another ExtendedExtended class. Materialize the vetted lazy object
		# through native get_doc(dict); never broaden the controller allowlist.
		proposed = frappe.get_doc(deepcopy(doc.as_dict()))
	else:
		proposed = deepcopy(doc)
	if not old:
		proposed.set("__islocal", True)  # native insert before _set_defaults
	proposed._set_defaults()
	if not old:
		# Native savedocs has already cleared a local parent's temporary name;
		# insert later normalizes its OWN child parents after naming (Doc471).
		# This pure native setter touches only the proposed copy, never any
		# actual source/old child identity checked by the shared resolver.
		proposed.set_parent_in_children()
	if proposed.doctype == "Stock Entry" and proposed.get("stock_entry_type"):
		# Same single real field consumed by set_purpose_for_stock_entry. Do not
		# call its cached reader in the decisive current phase.
		source = service._read("Stock Entry Type", proposed.stock_entry_type, {"purpose"})
		proposed.purpose = source.purpose
	for table in ("items", "packed_items"):
		for row, incoming in zip(proposed.get(table) or [], doc.get(table) or [], strict=False):
			# _set_defaults may supply the actor's generic stock_uom (Nos).
			# Native Stock Entry.validate_item unconditionally resets this to
			# the real Item value. Missing INPUT is distinct from an explicit
			# wrong UOM; do not freeze a user-default substituted identity.
			if (
				row.get("item_code")
				and not incoming.get("stock_uom")
				and frappe.get_meta(row.doctype).has_field("stock_uom")
			):
				source = service._read("Item", row.item_code, {"stock_uom"})
				if not source.stock_uom:
					_reject("实际物料库存单位缺失")
				row.stock_uom = source.stock_uom
	return proposed


def _native_default_proof(doc):
	"""Never prove current defaults then let native cached/RR values undo them."""
	if doc.doctype == "Stock Entry" and doc.get("stock_entry_type"):
		meta = frappe.get_meta(doc.doctype).get_field("purpose")
		if meta.fetch_from != "stock_entry_type.purpose" or meta.fetch_if_empty:
			_reject("原生库存用途默认路径未经核查")
		fetched = frappe.db.get_value(
			"Stock Entry Type",
			doc.stock_entry_type,
			("name", "purpose"),
			as_dict=True,
			cache=True,
			order_by=None,
		)
		if not fetched or fetched.purpose != doc.purpose:
			_reject("原生用途缓存或快照已过期，请重试独立请求")
		cached = frappe.get_cached_value("Stock Entry Type", doc.stock_entry_type, "purpose")
		if cached != doc.purpose:
			_reject("原生用途缓存已过期，请重试独立请求")
	for row in doc.get("items") or []:
		if (
			row.get("item_code")
			and row.get("stock_uom")
			and frappe.db.get_value("Item", row.item_code, "stock_uom", cache=False) != row.stock_uom
		):
			_reject("原生物料库存单位快照已过期，请重试独立请求")


def require_publication_order(state=None):
	"""Every deferred producer must take the existing site fence first."""
	state = state or initialize()
	if state.locks and fence_key() not in state.locks:
		_reject("原生库存同步发布隔离顺序无效，请重试独立请求")


def _repost_producer(roots, scopes):
	"""Potential native force/future/queue producers, not ordinary stock saves.

	The existing collector has already read the actual future frontier. A new
	unproved late producer is rejected by the RIV boundary, never fenced late.
	"""
	from .purchase_reversal_scope import _STOCK

	for doc in roots:
		if doc.docstatus not in (1, 2):
			continue
		if doc.doctype == "Landed Cost Voucher" or (doc.docstatus == 2 and doc.doctype in _STOCK):
			return True
		if doc.doctype == "Purchase Invoice" and frappe.db.get_single_value(
			"Buying Settings", "set_landed_cost_based_on_purchase_invoice_rate"
		):
			return True  # native receipt cost adjustment calls force=True
		if doc.doctype not in _STOCK or (
			doc.doctype in ("Purchase Invoice", "Sales Invoice") and not doc.get("update_stock")
		):
			continue
		if any(
			any((row.doctype, row.name) != (doc.doctype, doc.name) for row in scope.vouchers)
			for scope in scopes
		):
			return True
		consuming = [
			(row.item_code, row.get("s_warehouse") or row.get("from_warehouse") or row.get("warehouse"))
			for row in doc.get("items") or []
			if doc.doctype in ("Stock Entry", "Delivery Note", "Sales Invoice")
		]
		if len(consuming) != len(set(consuming)):
			return True  # native queue condition after actual consuming SLEs
	return False


@contextmanager
def _documents_boundary(documents, *, force_opaque=False, creating=False):
	"""One complete sorted lease and UNION budget for the actual native batch."""
	from . import purchase_payment_service as service

	with execution():
		for doc in documents:
			if doc.doctype == "Material Request" and doc.flags.get("mes_integration_request"):
				from .purchase_native_intent import install

				install()  # supported native asynchronous producer only
				require_publication_order()
		# Exactly native pure in-memory defaults: no naming, item calculation,
		# company/warehouse guessing or substitute source identities.
		roots = []
		for doc in documents:
			old = _persisted(doc)
			check_pointer(doc, old, creating=_creates_identity(doc, inserting=creating))
			proposed = _proposed(doc, old)
			if old and old.docstatus == 1 and doc.docstatus == 2:
				from .purchase_consistency import check_cancellation_facts, is_procurement

				if is_procurement(old):
					check_cancellation_facts(doc, old)
			roots.extend([proposed] + ([old] if old else []))
		opaque = force_opaque or any(_opaque(root) for root in roots)
		scopes = _scopes(roots, current=False, opaque=opaque)
		opaque = opaque or _opaque_sources(scopes)
		keys = _scope_keys(scopes, roots)
		producer = _repost_producer(roots, scopes)
		# Fence FIRST for actual potential producers; unrelated non-producers
		# keep their ordinary scoped concurrency.
		with acquire((fence_key(),) if opaque or producer else ()):
			with acquire(keys):
				with service.current_reads():
					current_roots, current_proposed_documents = [], []
					for doc in documents:
						current_old = _persisted(doc, current=True)
						check_pointer(doc, current_old, creating=_creates_identity(doc, inserting=creating))
						current_proposed = _proposed(doc, current_old)
						current_proposed_documents.append(current_proposed)
						current_roots.extend([current_proposed] + ([current_old] if current_old else []))
					current_opaque = force_opaque or any(
						_opaque(root, current=True) for root in current_roots
					)
					fresh = _scopes(current_roots, current=True, opaque=opaque)
					current_opaque = current_opaque or _opaque_sources(fresh)
					if current_opaque and not opaque:
						_reject("原生关联范围已改变，请重试独立请求")
					if _repost_producer(current_roots, fresh) and not (opaque or producer):
						_reject("原生重算发布范围已改变，请重试独立请求")
					if not _scope_keys(fresh, current_roots) <= keys:
						_reject("库存保护范围已扩大，请重试独立请求")
					if opaque:
						_pending_absent()
					_check_pending(fresh, current_roots)
					for proposed in current_proposed_documents:
						_native_default_proof(proposed)
					yield


@contextmanager
def document_boundary(doc, *, creating=False):
	"""Before native insert/_save, audit reservation, latest-check or SQL write."""
	with _documents_boundary((doc,), creating=creating):
		yield


def _status_documents(documents, native, *, status):
	payload = {"action": "update_status", "status": status}
	return _native_documents_action(documents, native, payload)


def _native_documents_action(
	documents, native, payload, *, force_opaque=False, preflight=None, hold_only=False
):
	"""Thin native action adapter; reuse source preparation/postchecks/audit."""
	from . import purchase_consistency as consistency
	from . import purchase_payment_service as service

	documents = tuple(documents)
	if not documents:
		return native()
	with _documents_boundary(documents, force_opaque=force_opaque), service.current_reads():
		old = {(doc.doctype, doc.name): service._read(doc.doctype, doc.name) for doc in documents}
		for doc in documents:
			# Status buttons operate on stored facts, not a replacement draft.
			# Native PO's timestamp subtraction is not a current-data proof.
			if consistency.business_payload(doc.as_dict()) != consistency.business_payload(
				old[(doc.doctype, doc.name)].as_dict()
			):
				_reject("原生状态单据事实已改变，请重试独立请求")
		if documents[0].doctype == "Material Request":
			return native()  # retain native/MES MRO; not a purchase/Finance adapter
		if preflight:
			for doc in documents:
				preflight(old[(doc.doctype, doc.name)])
		result = {}

		def write():
			for doc in documents:
				consistency.prepare_document(doc)
				consistency.check_sales_dependencies(doc)
				consistency.check_operating_dependencies(doc)
			state = consistency._state()
			prior = set(state["before_documents"]) if state else set()
			holds = state.get("hold_checks", {}) if state else {}
			if hold_only and any(
				(doc.doctype, doc.name) in prior and (doc.doctype, doc.name) not in holds for doc in documents
			):
				_reject("冻结操作需独立核对，不能替代同次采购业务保存")
			held_before = (
				{key: holds.get(key) or consistency.hold_evidence(old[key]) for key in old}
				if hold_only
				else {}
			)
			result["native"] = native()
			for doc in documents:
				actual = service._read(doc.doctype, doc.name)
				actual._doc_before_save = old[(doc.doctype, doc.name)]
				consistency.register_document(actual)
				if hold_only:
					consistency._state().setdefault("hold_checks", {})[doc.doctype, doc.name] = held_before[
						doc.doctype, doc.name
					]
					identity = next(
						row
						for row in consistency._state()["context"]["documents"]
						if (row["doctype"], row["name"]) == (doc.doctype, doc.name)
					)
					identity["permission"] = "write"
					identity["include_against_voucher"] = True
				if (doc.doctype, doc.name) not in prior:
					# update_child_qty_rate writes children before parent.save;
					# that later save's old snapshot is not this action's old
					# terms. Preserve a previously registered outer operation.
					consistency._state()["before_documents"][doc.doctype, doc.name] = old[
						(doc.doctype, doc.name)
					]
			return service._read(documents[0].doctype, documents[0].name)

		write.__name__ = payload["action"]
		documents[0]._procurement_call(
			write,
			_purchase_payload={
				**payload,
				"documents": [
					{"doctype": doc.doctype, "name": doc.name}
					if hold_only
					else consistency.business_payload(doc.as_dict())
					for doc in documents
				],
			},
		)
		return result.get("native")  # preserve native public None


class NativeStatusBoundary:
	"""Only MR/PO/PR native raw status methods; no generic status API."""

	def update_status(self, status):
		native = super().update_status
		return _status_documents((self,), lambda: native(status), status=status)


@frappe.whitelist()
def update_purchase_order_status(status, name):
	from erpnext.buying.doctype.purchase_order.purchase_order import update_status

	from .purchase_payment_service import _read

	with execution():
		doc = _read("Purchase Order", name)
		doc.check_permission("submit")
		return _status_documents((doc,), lambda: update_status(status, name), status=status)


@frappe.whitelist()
def close_or_unclose_purchase_orders(names, status):
	from erpnext.buying.doctype.purchase_order.purchase_order import (
		close_or_unclose_purchase_orders as native,
	)

	from .purchase_payment_service import _read

	with execution():
		# Parsing, permissions and algorithms remain native. Resolve the entire
		# bounded typed batch before any loop writes, including skipped statuses.
		from .purchase_reversal_scope import MAX_VOUCHERS

		parsed = json.loads(names)
		if (
			not isinstance(parsed, list)
			or not all(isinstance(name, str) for name in parsed)
			or len(set(parsed)) > MAX_VOUCHERS
		):
			_reject("采购订单批量状态范围无效或超过安全上限")
		documents = tuple(_read("Purchase Order", name) for name in sorted(set(parsed)))
		for doc in documents:
			doc.check_permission("write")
		return _status_documents(documents, lambda: native(names, status), status=status)


@frappe.whitelist()
def update_child_qty_rate(parent_doctype, trans_items, parent_doctype_name, child_docname="items"):
	from erpnext.controllers.accounts_controller import update_child_qty_rate as native

	if parent_doctype != "Purchase Order":
		return native(parent_doctype, trans_items, parent_doctype_name, child_docname)
	from . import purchase_payment_service as service
	from .purchase_reversal_scope import MAX_PAIRS, _child_parent, _fields

	with execution():
		doc = service._read("Purchase Order", parent_doctype_name)
		doc.check_permission("write")
		data = json.loads(trans_items)
		if (
			child_docname != "items"
			or not isinstance(data, list)
			or len(data) > MAX_PAIRS
			or not all(isinstance(row, dict) for row in data)
		):
			_reject("采购订单改量明细范围无效或超过安全上限")

		def preflight(actual):
			targets = {row.name: row for row in actual.items}
			for incoming in data:
				name = incoming.get("docname")
				if not name:
					if incoming.get("item_code"):
						service._read("Item", incoming["item_code"], {"stock_uom", "is_stock_item"})
					continue
				row = targets.get(name)
				if not row or incoming.get("item_code") != row.item_code:
					_reject("采购订单明细身份或所属单据不一致")
				_child_parent(actual, row, "items")
				_fields(row.doctype, {"item_code", "stock_uom", "uom", "warehouse"}, actual.doctype)

		preflight(doc)
		# Native new-item defaults choose warehouse/conversion/taxes late.
		# Do not run or copy those calculators to fabricate a proposed scope.
		return _native_documents_action(
			(doc,),
			lambda: native(parent_doctype, trans_items, parent_doctype_name, child_docname),
			{"action": "update_child_qty_rate", "trans_items": data, "child_docname": child_docname},
			force_opaque=any(row.get("item_code") and not row.get("docname") for row in data),
			preflight=preflight,
		)


class NativeRangeBoundary:
	"""Canonical native stock/MR insert and save, preserving other mixins."""

	def insert(self, *args, **kwargs):
		with document_boundary(self, creating=True):
			return super().insert(*args, **kwargs)

	def _save(self, *args, **kwargs):
		with document_boundary(self):
			return super()._save(*args, **kwargs)


class PointerBoundary:
	"""System pointer metadata is not ordinary controller write authority."""

	def _pointer_call(self, method, *args, creating=False, **kwargs):
		from . import purchase_payment_service as service

		with execution():
			if self.doctype == "Repost Item Valuation":
				from .purchase_native_repost import task_boundary

				with task_boundary(self, creating=creating):
					return method(*args, **kwargs)
			creating = _creates_identity(self, inserting=creating)
			if creating:
				check_pointer(self, None, creating=True)  # before lease/native naming
			old = _persisted(self)
			keys = {lock_key("document", self.doctype, self.name)} if self.name else set()
			if self.doctype == "Bin":
				for doc in (self, old) if old else (self,):
					service._read("Item", doc.item_code, {"stock_uom", "is_stock_item"})
					warehouse = service._read("Warehouse", doc.warehouse, {"company", "is_group", "disabled"})
					if warehouse.is_group or warehouse.disabled:
						_reject("库存仓库状态无效")
					keys.add(lock_key("pair", warehouse.company, doc.item_code, doc.warehouse))
			with acquire(keys), service.current_reads():
				current_old = _persisted(self, current=True)
				check_pointer(self, current_old, creating=creating)
				if self.doctype == "Bin":
					for doc in (self, current_old) if current_old else (self,):
						service._read("Item", doc.item_code, {"stock_uom", "is_stock_item"})
						warehouse = service._read(
							"Warehouse", doc.warehouse, {"company", "is_group", "disabled"}
						)
						if lock_key("pair", warehouse.company, doc.item_code, doc.warehouse) not in keys:
							_reject("实际库存范围已改变，请重试独立请求")
						_check_bin_pending(doc.item_code, doc.warehouse)
				return method(*args, **kwargs)

	def insert(self, *args, **kwargs):
		return self._pointer_call(super().insert, *args, creating=True, **kwargs)

	def _save(self, *args, **kwargs):
		return self._pointer_call(super()._save, *args, **kwargs)

	def db_set(self, fieldname, *args, **kwargs):
		if fieldname == POINTER or (isinstance(fieldname, dict) and POINTER in fieldname):
			frappe.throw(_i18n("库存保护指针不能由普通单据写入、清除或替换"), frappe.PermissionError)
		if self.doctype == "Repost Item Valuation":
			from .purchase_native_repost import _task_fields

			fields = {fieldname} if isinstance(fieldname, str) else set(fieldname)
			if fields & _task_fields(self).keys():
				frappe.throw(_i18n("原生重算范围必须通过单据保存核查，不能直接改写"), frappe.PermissionError)
			return self._pointer_call(super().db_set, fieldname, *args, **kwargs)
		return super().db_set(fieldname, *args, **kwargs)

	def db_update(self, *args, **kwargs):
		from .purchase_native_repost import task_boundary

		if self.doctype == "Repost Item Valuation":
			with task_boundary(self):
				return super().db_update(*args, **kwargs)
		return super().db_update(*args, **kwargs)

	def restart_reposting(self):
		from .purchase_native_repost import guard_task, task_boundary

		with task_boundary(self):
			guard_task(self, destructive=True)
			return super().restart_reposting()

	def deduplicate_similar_repost(self):
		if frappe.db.get_value(self.doctype, self.name, POINTER, for_update=True):
			return  # owned jobs require real coverage; no naked native Skipped
		if self.repost_only_accounting_ledgers:
			owned = frappe.db.get_values(
				self.doctype,
				{
					"based_on": "Transaction",
					"voucher_type": self.voucher_type,
					"voucher_no": self.voucher_no,
					"docstatus": 1,
					"repost_only_accounting_ledgers": 1,
					"status": "Queued",
					POINTER: ["is", "set"],
				},
				"name",
				limit=1,
				for_update=True,
			)
		elif self.based_on == "Item and Warehouse":
			owned = frappe.db.sql(
				f"""SELECT name FROM `tabRepost Item Valuation` WHERE item_code=%s AND warehouse=%s
                AND name!=%s AND TIMESTAMP(posting_date,posting_time)>TIMESTAMP(%s,%s)
                AND docstatus=1 AND status='Queued' AND based_on='Item and Warehouse'
                AND `{POINTER}` IS NOT NULL AND `{POINTER}`!='' LIMIT 1 FOR UPDATE""",
				(self.item_code, self.warehouse, self.name, self.posting_date, self.posting_time),
			)
		else:
			owned = []
		if owned:
			_reject("已受理采购冲销的原生任务不能被普通去重跳过")
		return super().deduplicate_similar_repost()

	@staticmethod
	def clear_old_logs(days=None):
		from .purchase_native_repost import clear_old_logs

		clear_old_logs(days)


def before_execution(*args, **kwargs):
	# HTTP auth/session housekeeping is outside procurement and runs earlier.
	# Early procurement hooks/services themselves still initialize first or
	# refuse late initialization; this is not a first-any-DB-write claim.
	db = getattr(frappe.local, "db", None)  # actual instance, not LocalProxy type
	if _known_database(db) and not db.transaction_writes:
		initialize()
		from .purchase_native_repost import install

		install()
		from .purchase_native_intent import METHOD, install

		if kwargs.get("method") == METHOD:
			install()  # captured native callable is resolved before before_job


def procurement_entry(method):
	from functools import wraps

	@wraps(method)
	def wrapped(*args, **kwargs):
		with execution():
			return method(*args, **kwargs)

	return wrapped
