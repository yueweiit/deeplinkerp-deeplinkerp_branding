"""Consolidate operating expenses into CN when its navigation is installed."""

import json
from urllib.parse import urlsplit

import frappe

from deeplinkerp_branding.procurement_navigation import reconcile_links, reconcile_workspace

ENTRIES = (("运营支出", "Page", "operating-expenses", "receipt-text"),)
TARGETS = {"operating-expenses"}
CN_PAGES = ("china-expenses", "china-expense-details", "china-expense-reports", "china-personnel-report", "china-expense-review")


def ensure_cn_cash_journal_navigation():
	"""Attach the manual cash book without replacing regional/custom navigation."""
	target = "china-cash-journal"
	if not (frappe.db.exists("DocType", "CN Cash Journal Entry") and frappe.db.exists("Page", target)
			and frappe.db.exists("Workspace Sidebar", "China Operating Expenses")):
		return False
	doc = frappe.get_doc("Workspace Sidebar", "China Operating Expenses")
	if doc.get("for_user") or doc.get("public") == 0:
		return False
	# Budget's redesigned approval workspace is read-only. Its owned navigation
	# deliberately omits manual bookkeeping; standalone finance routes still exist.
	if any(row.get("link_to") == "china-expenses" and row.get("label") in ("Approval Overview", "Management Overview")
			for row in doc.get("items") or []):
		return False
	anchor = next((index + 1 for index, row in enumerate(doc.get("items") or [])
		if row.get("link_to") == "china-expense-details"), len(doc.get("items") or []))
	if not reconcile_links(doc, "items", entries=(("CN Cash Journal Entry", "Page", target, "book-open"),),
			targets={target, "CN Cash Journal Entry"}, anchor=anchor, child=0):
		return False
	previous, developer_mode = frappe.flags.in_import, frappe.conf.developer_mode
	frappe.flags.in_import, frappe.conf.developer_mode = True, False
	try:
		doc.save(ignore_permissions=True)
	finally:
		frappe.flags.in_import, frappe.conf.developer_mode = previous, developer_mode
	return True


def _remove_empty_expense_group(doc, table):
	"""Only prune the now-empty owned group, never custom sibling links."""
	group_type = "Section Break" if table == "items" else "Card Break"
	rows = list(doc.get(table) or [])
	result = [row for index, row in enumerate(rows) if not (
		row.get("type") == group_type and row.get("label") == "支出管理"
		and (index + 1 == len(rows) or rows[index + 1].get("type") == group_type)
	)]
	if result == rows:
		return False
	doc.set(table, result)
	if table == "links":
		content = json.loads(doc.content or "[]")
		doc.content = json.dumps([block for block in content if not (
			block.get("type") == "card" and block.get("data", {}).get("card_name") == "支出管理"
		)], ensure_ascii=False)
	for idx, row in enumerate(result, 1):
		row.idx = idx
	return True


def _expense_group_anchor(doc, table):
	if doc.get("name") != "China Finance":
		return None
	group_type = "Section Break" if table == "items" else "Card Break"
	return next((
		index + 1 for index, row in enumerate(doc.get(table) or [])
		if row.get("type") == group_type and row.get("label") == "支出管理"
	), None)


def _anchor(doc, table):
	group_anchor = _expense_group_anchor(doc, table)
	if group_anchor is not None:
		return group_anchor
	rows = list(doc.get(table) or [])
	for index, row in enumerate(rows):
		if row.get("link_to") == "Account" or urlsplit(row.get("url") or "").path.rstrip("/") in {"/desk/chart-of-accounts", "/app/chart-of-accounts", "/desk/account/view/tree", "/app/account/view/tree"}:
			return index + 1
	return next(
		(
			index
			for index, row in enumerate(rows)
			if row.get("link_to") in TARGETS
			and not any(row.get(field) for field in ("filters", "route_options", "url"))
		),
		len(rows),
	)


def ensure_operating_navigation():
	journal_changed = ensure_cn_cash_journal_navigation()
	if not frappe.db.exists("Page", "operating-expenses"):
		return {"ok": False, "missing_page": "operating-expenses"}
	changed = [{"doctype": "Workspace Sidebar", "name": "China Operating Expenses"}] if journal_changed else []
	cn_ready = frappe.db.exists("Workspace Sidebar", "China Operating Expenses") and all(
		frappe.db.exists("Page", page) for page in CN_PAGES)
	entries = () if cn_ready else ENTRIES
	for name in ("Accounting", "China Finance"):
		for doctype in ("Workspace Sidebar", "Workspace"):
			if not frappe.db.exists(doctype, name):
				continue
			doc = frappe.get_doc(doctype, name)
			if doc.get("for_user") or doc.get("public") == 0:
				continue
			if doctype == "Workspace Sidebar":
				modified = reconcile_links(
					doc, "items", entries=entries, targets=TARGETS, anchor=_anchor(doc, "items"),
					child=int(_expense_group_anchor(doc, "items") is not None),
				)
			else:
				modified = reconcile_workspace(
					doc,
					entries=entries,
					targets=TARGETS,
					prefix="dlp-operating-",
					anchor=_anchor(doc, "links"),
				)
			if cn_ready and modified:
				_remove_empty_expense_group(doc, "items" if doctype == "Workspace Sidebar" else "links")
			if not modified:
				continue
			previous, developer_mode = frappe.flags.in_import, frappe.conf.developer_mode
			frappe.flags.in_import, frappe.conf.developer_mode = True, False
			try:
				doc.flags.ignore_links = True
				doc.save(ignore_permissions=True)
			finally:
				frappe.flags.in_import, frappe.conf.developer_mode = previous, developer_mode
			changed.append({"doctype": doctype, "name": name})
	return {"ok": True, "changed": changed}
