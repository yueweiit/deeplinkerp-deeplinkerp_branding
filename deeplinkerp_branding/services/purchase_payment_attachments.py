"""Coordinate native private Files with the existing payment transaction."""

from __future__ import annotations

from frappe import _


import hashlib
import json
import re

import frappe

from deeplinkerp_branding.services import purchase_payment_service as service


def _key(session_id):
	if not re.fullmatch(r"[a-zA-Z0-9-]{16,80}", str(session_id or "")):
		frappe.throw(_("附件会话失效，请重新选择文件"))
	return (
		"dlp-payment-files:" + hashlib.sha256((frappe.session.user + ":" + session_id).encode()).hexdigest()
	)


def _context(doctype, name):
	if doctype == "Payment Entry":
		doc = service._read(doctype, name)
		service._read("Company", doc.company)
		doc.check_permission("write")
		if doc.docstatus != 0:
			frappe.throw(_("付款单已改变，请刷新"))
	elif doctype == "Sales Order":
		from .sales_receipt_service import _order

		doc = _order(name, create=True)
	else:
		doc = service._source(doctype, name)
		if doc.docstatus != 1 or doc.get("is_return"):
			frappe.throw(_("来源必须为已提交且非退货的采购单据"))
		if not frappe.has_permission("Payment Entry", "create"):
			frappe.throw(_("没有创建付款单权限"), frappe.PermissionError)
	return doc


def _intent(session_id):
	value = frappe.cache().get_value(_key(session_id))
	if not value or value.get("cancelled"):
		frappe.throw(_("附件会话失效，请重新选择文件"))
	return value


def _put(session_id, value):
	# Metadata expiry is not File deletion. Abandoned private Files are retained.
	frappe.cache().set_value(_key(session_id), value, expires_in_sec=86400)


def _ids(value):
	try:
		value = json.loads(value) if isinstance(value, str) else value
	except ValueError:
		frappe.throw(_("附件列表无效"))
	if value is None:
		return []
	if not isinstance(value, list) or not all(isinstance(name, str) and name for name in value):
		frappe.throw(_("附件列表无效"))
	return list(dict.fromkeys(value))


def _temporary(name):
	doc = frappe.get_doc("File", name, for_update=True)
	doc.check_permission("read")
	doc.check_permission("write")
	if (
		doc.owner != frappe.session.user
		or not doc.is_private
		or doc.is_folder
		or doc.attached_to_doctype
		or doc.attached_to_name
		or doc.attached_to_field
	):
		frappe.throw(_("附件不可用于本次付款"), frappe.PermissionError)
	return doc


@frappe.whitelist(methods=["POST"])
def begin_upload(doctype, name, session_id):
	_context(doctype, name)
	cache = frappe.cache()
	with cache.lock(_key(session_id) + ":lock", timeout=60, blocking_timeout=5):
		previous = cache.get_value(_key(session_id))
		if previous and (
			previous["context"] != [doctype, name] or previous.get("cancelled") or previous.get("bound")
		):
			frappe.throw(_("附件会话失效，请重新选择文件"))
		_put(session_id, previous or {"context": [doctype, name], "files": [], "uploads": {}})
	return {"session_id": session_id}


@frappe.whitelist(methods=["POST"])
def upload_file():
	"""Called by the native multipart upload_file handler; keep its File lifecycle."""
	session_id = frappe.form_dict.fieldname
	with frappe.cache().lock(_key(session_id) + ":lock", timeout=60, blocking_timeout=5):
		intent = _intent(session_id)
		_context(*intent["context"])
		if intent.get("bound") or not frappe.local.uploaded_file or not frappe.local.uploaded_filename:
			frappe.throw(_("附件上传未完成"))
		digest = hashlib.sha256(
			str(frappe.local.uploaded_filename).encode() + b"\0" + frappe.local.uploaded_file
		).hexdigest()
		if existing := intent.get("uploads", {}).get(digest):
			return _temporary(existing)
		doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": frappe.local.uploaded_filename,
				"content": frappe.local.uploaded_file,
				"is_private": 1,
			}
		).save()
		if doc.owner != frappe.session.user:
			frappe.throw(_("附件不可用于本次付款"), frappe.PermissionError)
		intent["files"] = list(dict.fromkeys([*intent["files"], doc.name]))
		intent.setdefault("uploads", {})[digest] = doc.name
		_put(session_id, intent)

		def rollback():
			current = frappe.cache().get_value(_key(session_id))
			if current:
				current["files"] = [name for name in current["files"] if name != doc.name]
				current["uploads"] = {
					key: name for key, name in current.get("uploads", {}).items() if name != doc.name
				}
				_put(session_id, current)

		frappe.db.after_rollback.add(rollback)
		return doc


def bind(doc, session_id=None, attachments=None):
	names = _ids(attachments)
	if not names:
		return
	doc.check_permission("write")
	service._read("Company", doc.company)
	with frappe.cache().lock(_key(session_id) + ":lock", timeout=60, blocking_timeout=5):
		intent = _intent(session_id)
		source = _context(*intent["context"])
		if intent.get("bound"):
			if intent["bound"] == doc.name and names == intent.get("used"):
				return
			frappe.throw(_("附件已用于其他付款"), frappe.PermissionError)
		if source.company != doc.company:
			frappe.throw(_("附件不可用于本次付款"), frappe.PermissionError)
		if source.doctype == "Payment Entry":
			valid_source = source.name == doc.name
		elif source.doctype == "Sales Order":
			valid_source = (doc.party_type == "Customer" and doc.payment_type == "Receive"
				and source.customer == doc.party and bool(doc.references) and all(
					ref.reference_doctype == "Sales Order" and ref.reference_name == source.name
					for ref in doc.references))
		else:
			invoices = set(service._invoice_names(source.doctype, source.name))
			valid_source = source.supplier == doc.party and any(
				ref.reference_doctype == "Purchase Invoice" and ref.reference_name in invoices
				for ref in doc.references
			)
		if not valid_source or not set(names) <= set(intent["files"]):
			frappe.throw(_("附件不可用于本次付款"), frappe.PermissionError)
		temporary = [_temporary(name) for name in names]
		for file in temporary:
			# Native copy retains the blob and its validations without decoding
			# binary PDF/image bytes or bypassing File create permissions.
			file.create_attachment_copy("Payment Entry", doc.name)
		previous = dict(intent)
		intent.update(bound=doc.name, used=names)
		_put(session_id, intent)
		frappe.db.after_rollback.add(lambda: _put(session_id, previous))


@frappe.whitelist(methods=["POST"])
def discard(session_id, attachments=None, cancel=0):
	with frappe.cache().lock(_key(session_id) + ":lock", timeout=60, blocking_timeout=5):
		intent = _intent(session_id)
		if intent.get("bound"):
			return []  # Never remove committed payment attachments or their source Files.
		names = list(intent["files"]) if cancel in (True, 1, "1") else _ids(attachments)
		if not set(names) <= set(intent["files"]):
			frappe.throw(_("附件不可用于本次付款"), frappe.PermissionError)
		docs = [_temporary(name) for name in names]
		for doc in docs:
			doc.check_permission("delete")
		for doc in docs:
			frappe.delete_doc("File", doc.name)
		intent["files"] = [name for name in intent["files"] if name not in names]
		intent["uploads"] = {
			key: name for key, name in intent.get("uploads", {}).items() if name not in names
		}
		intent["cancelled"] = cancel in (True, 1, "1")
		_put(session_id, intent)
		return names


@frappe.whitelist()
def list_attachments(name):
	doc = service._read("Payment Entry", name)
	service._read("Company", doc.company)
	return frappe.get_list(
		"File",
		filters={"attached_to_doctype": "Payment Entry", "attached_to_name": name},
		fields=["name", "file_name", "file_url", "is_private"],
		order_by="creation asc",
		limit_page_length=0,
	)
