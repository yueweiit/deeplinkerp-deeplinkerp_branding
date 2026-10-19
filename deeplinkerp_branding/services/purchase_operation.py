"""One local database boundary for explicit native procurement operations.

The Integration Request primary key is the lock and durable receipt. Nothing in
this module commits: Frappe commits the business documents and receipt together.
Redis is deliberately absent from this authority. External writes cannot join it.
"""

from __future__ import annotations

from frappe import _


import hashlib
import json
import re
import time
from contextvars import ContextVar
from copy import deepcopy
from datetime import UTC, datetime, timezone

import frappe

SERVICE = "DeepLinkERP native procurement"
_active = ContextVar("purchase_operation", default=None)


def encode(value):
	return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def identity(actor, request_id):
	return "DLP-PURCHASE-" + hashlib.sha256((actor + ":" + request_id).encode()).hexdigest()


def current():
	return _active.get()


def error_identifier(error):
	if error is None:
		return None
	return getattr(error, "purchase_error_id", None) or (
		"native_permission_denied"
		if isinstance(error, frappe.PermissionError)
		else "native_validation_failed"
		if isinstance(error, frappe.ValidationError)
		else "native_runtime_failure"
	)


def runtime_log(context, result, error=None, rollback_error=None):
	# Do not record exception text, payloads, remarks, accounts, or attachments.
	# This JSON file log survives a database rollback; it is not a business write.
	event = {
		key: context.get(key)
		for key in ("operation_id", "user", "documents", "amount", "currency", "quantity", "before", "after")
	}
	event.update(
		time=datetime.now(UTC).isoformat(),
		elapsed_ms=round((time.monotonic() - context.get("started", time.monotonic())) * 1000),
		modules=context.get("modules", []),
		result=result,
		error=type(error).__name__ if error else None,
		error_code=error.args[0] if error and error.args and isinstance(error.args[0], int) else None,
		error_category="validation"
		if isinstance(error, frappe.ValidationError)
		else "permission"
		if isinstance(error, frappe.PermissionError)
		else "runtime"
		if error
		else None,
		rollback_error=type(rollback_error).__name__ if rollback_error else None,
		error_id=error_identifier(error),
		invariant=getattr(error, "purchase_invariant", None),
	)
	logger = frappe.logger("purchase_operation", allow_site=True)
	(logger.error if error else logger.info)(encode(event))


def rollback_failure(context, result, error):
	"""Keep the original business failure even when the connection cannot roll back."""
	rollback_error = None
	try:
		frappe.db.rollback()
	except Exception as caught:
		rollback_error = caught
	finally:
		runtime_log(context, result, error, rollback_error)
	return rollback_error


def trace(context, module, action, *, source=None, target=None):
	started = time.monotonic()
	event = {
		"module": module,
		"source": source or context.get("documents", []),
		"target": target or context.get("documents", []),
		"result": "verified",
	}
	error = None
	try:
		return action()
	except Exception as caught:
		error = caught
		event.update(
			result="failed",
			error=type(caught).__name__,
			error_id=error_identifier(caught),
			invariant=getattr(caught, "purchase_invariant", None),
			error_code=caught.args[0] if caught.args and isinstance(caught.args[0], int) else None,
		)
		raise
	finally:
		event.update(
			elapsed_ms=round((time.monotonic() - started) * 1000),
			amount=context.get("amount"),
			currency=context.get("currency"),
			quantity=context.get("quantity"),
			before=context.get("before", {}),
			after=context.get("after", {}),
		)
		context.setdefault("modules", []).append(event)
		logger = frappe.logger("purchase_operation", allow_site=True)
		(logger.error if error else logger.info)(
			encode(
				{
					"operation_id": context["operation_id"],
					"user": context["user"],
					"time": datetime.now(UTC).isoformat(),
					**event,
				}
			)
		)


def _existing(name):
	rows = frappe.db.get_values(
		"Integration Request",
		{"name": name},
		["name", "status", "data", "output"],
		as_dict=True,
		for_update=True,
	)
	return rows[0] if rows else None


def _reserve(context):
	# System audit metadata has no native create permission, and must never use
	# IntegrationRequest.update_status(), which commits. db_insert is limited to
	# this audit record; all business documents retain normal controller ACLs.
	audit = frappe.get_doc(
		{
			"doctype": "Integration Request",
			"name": context["operation_id"],
			"integration_request_service": SERVICE,
			"request_id": context["request_id"],
			"request_description": "Native procurement transaction",
			"is_remote_request": 0,
			"status": "Queued",
			"data": encode(context),
		}
	)
	audit.db_insert()


def complete_audit(context, output):
	if context.get("reversal", {}).get("roots"):
		from .purchase_reversal_progress import accept_audit

		accept_audit(context, output)
		return
	documents = output["documents"] if "documents" in output else [output.get("document", output)]
	receipts = []
	for document in documents:
		document = document.get("document", document)
		action_permission = next(
			(
				row.get("permission")
				for row in context["documents"]
				if (row["doctype"], row["name"])
				== (document.get("doctype", "Payment Entry"), document.get("name"))
			),
			None,
		)
		receipt = {
			"doctype": document.get("doctype", "Payment Entry"),
			"name": document.get("name"),
			"needs_review": bool(output.get("needs_review")),
			"permission": "read"
			if output.get("needs_review")
			else action_permission
			or (
				"submit"
				if document.get("docstatus") == 1
				else "cancel"
				if document.get("docstatus") == 2
				else "write"
			),
		}
		receipts.append(receipt)
		identity = {key: receipt[key] for key in ("doctype", "name")}
		if not any(
			(row["doctype"], row["name"]) == (identity["doctype"], identity["name"])
			for row in context["documents"]
		):
			context["documents"].append(identity)
	document = documents[0].get("document", documents[0]) if documents else {}
	artifacts = []
	from .purchase_consistency import artifact_evidence

	for identity in context["documents"]:
		key = identity["doctype"] + ":" + identity["name"]
		if key not in context["after"]:
			continue
		native = frappe.get_doc(identity["doctype"], identity["name"], for_update=True)
		permission = identity.get("permission") or (
			"submit" if native.docstatus == 1 else "cancel" if native.docstatus == 2 else "write"
		)
		artifacts.append(
			{
				**identity,
				"docstatus": native.docstatus,
				"modified": str(native.modified),
				"permission": permission,
				"evidence": artifact_evidence(native, include_against_voucher=True)
				if identity.get("include_against_voucher")
				else artifact_evidence(native),
			}
		)
	receipt = {"documents": receipts} if "documents" in output else receipts[0]
	receipt.update(artifacts=artifacts)
	for key in ("result", "acknowledgements"):
		if key in output:
			receipt[key] = output[key]
	if output.get("localname"):
		receipt["localname"] = output["localname"]
	context.update(
		amount=document.get("amount", document.get("grand_total")),
		currency=document.get("currency"),
		quantity=[{"key": row.get("key"), "qty": row.get("qty")} for row in document.get("items", [])],
	)
	frappe.db.set_value(
		"Integration Request",
		context["operation_id"],
		{
			"status": "Completed",
			"data": encode(context),
			"output": encode(receipt),
			"reference_doctype": document.get("doctype", "Payment Entry") if documents else None,
			"reference_docname": document.get("name"),
		},
		update_modified=False,
	)


def replay_artifacts(receipt):
	"""Current native ACLs and full persisted evidence for every acknowledged effect."""
	from .purchase_consistency import artifact_evidence

	documents = []
	for artifact in receipt.get("artifacts", []):
		doc = frappe.get_doc(artifact["doctype"], artifact["name"], for_update=True)
		documents.append((artifact, doc))
		if not artifact.get("system_effect"):
			for permission in ("read", artifact["permission"]):
				if not frappe.has_permission(doc.doctype, permission, doc=doc):
					reject(
						"采购操作关联单据权限已改变，请重新核对",
						"replay_permission_changed",
						frappe.PermissionError,
					)
	for artifact, doc in documents:
		evidence = (
			artifact_evidence(doc, include_against_voucher=True)
			if artifact.get("include_against_voucher")
			else artifact_evidence(doc)
		)
		if (
			doc.docstatus != artifact["docstatus"]
			or str(doc.modified) != artifact["modified"]
			or evidence != artifact["evidence"]
		):
			reject("采购操作关联单据或流水已改变，请重新核对", "replay_evidence_changed")


def reject(message, identifier, exception=frappe.ValidationError, *, invariant=None):
	"""Only call with fixed application messages/identifiers, never source payloads."""
	try:
		frappe.throw(message, exception)
	except exception as error:
		error.purchase_error_id = identifier
		error.purchase_invariant = invariant
		raise


from .purchase_repost_boundary import procurement_entry


@procurement_entry
def run(request_id, payload, operation, replay, *, digest=None, acknowledge_validation=False):
	"""Run once, or re-read the acknowledged native documents under current ACLs."""
	if current() is not None:
		return operation()
	if not re.fullmatch(r"[a-zA-Z0-9-]{16,80}", str(request_id or "")):
		reject("缺少有效请求标识，请刷新抽屉", "request_id_invalid")
	digest = digest or hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
	started, started_at = time.monotonic(), datetime.now(UTC).isoformat()
	for attempt in range(3):
		response_before = deepcopy(frappe.response)
		messages_before = list(frappe.message_log)
		# Only runtime logs retain failed attempts. A durable success may contain
		# no document, snapshot, policy or system effect from a rolled-back try.
		context = {
			"operation_id": identity(frappe.session.user, request_id),
			"request_id": request_id,
			"user": frappe.session.user,
			"digest": digest,
			"before": {},
			"after": {},
			"documents": [],
			"amount": None,
			"currency": None,
			"quantity": [],
			"started": started,
			"started_at": started_at,
			"modules": [],
		}
		previous = None
		reserved = False
		token = _active.set(context)
		try:
			previous = _existing(context["operation_id"])
			if previous:
				facts = json.loads(previous.data)
				if facts.get("digest") != digest or facts.get("user") != frappe.session.user:
					reject("同一请求内容已改变，请刷新后重新核对", "request_payload_changed")
				if facts.get("reversal"):
					from .purchase_reversal_progress import replay as replay_progress

					return replay_progress(previous)
				if previous.status != "Completed":
					reject("上次请求尚未完成，请稍后重试", "request_incomplete")
				return replay(json.loads(previous.output))
			_reserve(context)
			reserved = True
			from .purchase_consistency import bind_operation

			bind_operation(context)
			result = trace(context, "native procurement controllers", operation)
			from .purchase_consistency import check_registered

			check_registered()
			complete_audit(context, result)
			runtime_log(context, "success_pending_commit")
			return result
		except Exception as error:
			# A rolled-back native form must not sync phantom documents or
			# success messages into the client, including before a retry.
			frappe.response.clear()
			frappe.response.update(response_before)
			frappe.message_log[:] = messages_before
			rollback_error = rollback_failure(context, "rolled_back", error)
			if rollback_error:
				raise  # never retry or acknowledge an unknown transaction state
			# MariaDB 1205/1213 and PostgreSQL native equivalents only. Validation
			# errors are never retried; each retry rebuilds documents and callbacks.
			code = error.args[0] if error.args else None
			retry = (
				isinstance(error, (frappe.QueryDeadlockError, frappe.QueryTimeoutError))
				or code in (1205, 1213, "40P01", "55P03")
				or getattr(error, "pgcode", None) in ("40P01", "55P03")
			)
			audit_collision = (
				isinstance(error, frappe.DuplicateEntryError) and not reserved and previous is None
			)
			if audit_collision:
				# Concurrent primary-key winner is visible only in a new/current read.
				retry = True
			if (
				retry
				and attempt < 2
				and (
					audit_collision or not isinstance(error, (frappe.ValidationError, frappe.PermissionError))
				)
			):
				continue  # Frappe rollback starts a new transaction.
			if (
				(previous is None or previous.status == "Completed")
				and acknowledge_validation
				and isinstance(error, (frappe.ValidationError, frappe.PermissionError))
			):
				if isinstance(error, frappe.PermissionError):
					return {
						"failed": True,
						"error": "采购操作权限不足，请联系管理员核对",
						"error_id": getattr(error, "purchase_error_id", "native_permission_denied"),
					}
				return {"failed": True, "error": str(error)}
			raise
		finally:
			_active.reset(token)


def protect_audit(doc, method=None, *args, **kwargs):
	if doc.get("integration_request_service") == SERVICE:
		frappe.throw(_("采购操作审计需永久保留，不能删除或改写"), frappe.PermissionError)
	from .purchase_native_intent import guard

	guard(doc, proposed={"name": args[1]} if method == "before_rename" and len(args) >= 2 else None)


class ProcurementAuditRetention:
	def insert(self, *args, **kwargs):
		from .purchase_native_intent import guard

		# Fixed native Document.insert signature: set_name is its fifth argument.
		guard(self, proposed={"name": kwargs.get("set_name", args[4] if len(args) > 4 else self.name)})
		return super().insert(*args, **kwargs)

	def db_insert(self, *args, **kwargs):
		from .purchase_native_intent import guard

		guard(self)
		return super().db_insert(*args, **kwargs)

	def db_update(self, *args, **kwargs):
		from .purchase_native_intent import guard

		guard(self)
		return super().db_update(*args, **kwargs)

	def _save(self, *args, **kwargs):
		from .purchase_native_intent import guard

		guard(self)
		return super()._save(*args, **kwargs)

	def db_set(self, fieldname, *args, **kwargs):
		from .purchase_native_intent import guard

		proposed = (
			fieldname
			if isinstance(fieldname, dict)
			else {fieldname: args[0] if args else kwargs.get("value")}
		)
		guard(self, proposed=proposed)
		return super().db_set(fieldname, *args, **kwargs)

	def update_status(self, params, status):
		from .purchase_native_intent import guard

		guard(self, proposed={"status": status})
		return super().update_status(params, status)

	def handle_success(self, response):
		from .purchase_native_intent import guard

		guard(self, proposed={"status": "Completed"})
		return super().handle_success(response)

	def handle_failure(self, response):
		from .purchase_native_intent import guard

		guard(self, proposed={"status": "Failed"})
		return super().handle_failure(response)

	@staticmethod
	def clear_old_logs(days=30):
		# Native cleanup uses the resolved controller, so exclude this service
		# while retaining ordinary Integration Request cleanup behavior.
		from frappe.query_builder import Interval
		from frappe.query_builder.functions import Now

		from .purchase_native_intent import PREFIX
		from .purchase_native_intent import SERVICE as native_service

		table = frappe.qb.DocType("Integration Request")
		frappe.db.delete(
			table,
			filters=(table.creation < (Now() - Interval(days=days)))
			& (
				(table.integration_request_service.notin((SERVICE, native_service)))
				| table.integration_request_service.isnull()
			)
			& ~table.name.like(PREFIX + "%"),
		)
