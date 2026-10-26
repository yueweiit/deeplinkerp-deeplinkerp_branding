"""Add one operating-expense route to existing finance navigation only."""

import frappe
from urllib.parse import urlsplit

from deeplinkerp_branding.procurement_navigation import reconcile_links, reconcile_workspace

ENTRIES = (("运营支出", "Page", "operating-expenses", "receipt-text"),)
TARGETS = {"operating-expenses"}


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
	if not frappe.db.exists("Page", "operating-expenses"):
		return {"ok": False, "missing_page": "operating-expenses"}
	changed = []
	for name in ("Accounting", "China Finance"):
		for doctype in ("Workspace Sidebar", "Workspace"):
			if not frappe.db.exists(doctype, name):
				continue
			doc = frappe.get_doc(doctype, name)
			if doctype == "Workspace Sidebar":
				modified = reconcile_links(
					doc, "items", entries=ENTRIES, targets=TARGETS, anchor=_anchor(doc, "items"),
					child=int(_expense_group_anchor(doc, "items") is not None),
				)
			else:
				modified = reconcile_workspace(
					doc,
					entries=ENTRIES,
					targets=TARGETS,
					prefix="dlp-operating-",
					anchor=_anchor(doc, "links"),
				)
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
