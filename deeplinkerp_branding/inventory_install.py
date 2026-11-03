"""Install and migrate the shared DeeplinkERP inventory capability."""

from __future__ import annotations

INVENTORY_DETAIL_PAGES = (
	{
		"label": "物料库存明细",
		"link_to": "inventory-location-detail",
		"legacy_labels": ("库存库位明细",),
	},
	{
		"label": "半成品库存明细",
		"link_to": "semi-finished-inventory-detail",
		"legacy_labels": (),
	},
	{
		"label": "成品库存明细",
		"link_to": "finished-goods-inventory-detail",
		"legacy_labels": (),
	},
	{
		"label": "模具库存明细",
		"link_to": "mold-inventory-detail",
		"legacy_labels": (),
	},
)


def get_inventory_trace_custom_fields() -> dict[str, list[dict]]:
	return {
		"Item": [
			{
				"fieldname": "custom_dpci",
				"label": "DPCI",
				"fieldtype": "Data",
				"insert_after": "item_group",
			},
			{
				"fieldname": "custom_external_code",
				"label": "外部编码",
				"fieldtype": "Data",
				"insert_after": "custom_dpci",
			},
			{
				"fieldname": "custom_original_identifier_alias",
				"label": "原始标识/别名",
				"fieldtype": "Small Text",
				"insert_after": "custom_external_code",
			},
		]
	}


def ensure_inventory_trace_fields() -> dict:
	try:
		from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
	except Exception:
		return {"ok": False, "message": "当前未连接 Frappe。"}

	fields = get_inventory_trace_custom_fields()
	try:
		create_custom_fields(fields, ignore_validate=True)
	except TypeError:
		create_custom_fields(fields)
	return {"ok": True, "doctypes": list(fields)}


def ensure_stock_sidebar_inventory_pages() -> dict:
	try:
		import frappe
	except Exception:
		return {"ok": False, "message": "当前未连接 Frappe。"}

	if not frappe.db.exists("DocType", "Workspace Sidebar"):
		return {"ok": False, "message": "当前站点没有 Workspace Sidebar，已跳过。"}
	missing_pages = [
		page["link_to"] for page in INVENTORY_DETAIL_PAGES if not frappe.db.exists("Page", page["link_to"])
	]
	if missing_pages:
		return {"ok": False, "message": f"库存明细页面尚未安装：{', '.join(missing_pages)}"}

	sidebars = frappe.get_all(
		"Workspace Sidebar",
		fields=["name", "title", "module", "for_user"],
		limit_page_length=0,
	)
	sidebar_name = next(
		(
			row.name for row in sidebars
			if not row.for_user and row.name == "Stock"
		),
		None,
	) or next(
		(
			row.name for row in sidebars
			if not row.for_user and (row.module == "Stock" or row.title == "库存")
		),
		None,
	)
	if not sidebar_name:
		return {"ok": False, "message": "未找到库存 Workspace Sidebar。"}

	private_names = [
		row.name for row in sidebars
		if row.for_user and row.name == f"{sidebar_name}-{row.for_user}"
	]
	updated = []
	for name in [sidebar_name, *private_names]:
		sidebar = frappe.get_doc("Workspace Sidebar", name)
		if upsert_stock_sidebar_inventory_pages(sidebar):
			sidebar.save(ignore_permissions=True)
			updated.append(name)
	if updated:
		frappe.db.commit()
	return {
		"ok": True,
		"changed": bool(updated),
		"sidebar": sidebar_name,
		"updated_sidebars": updated,
		"pages": [page["link_to"] for page in INVENTORY_DETAIL_PAGES],
	}


def upsert_stock_sidebar_inventory_pages(sidebar) -> bool:
	items = list(sidebar.get("items") or [])
	changed = False
	targets = []
	for page in INVENTORY_DETAIL_PAGES:
		labels = {page["label"], *page["legacy_labels"]}
		matches = [
			row
			for row in items
			if getattr(row, "link_to", None) == page["link_to"] or getattr(row, "label", None) in labels
		]
		if matches:
			target = matches[0]
			for duplicate in matches[1:]:
				items.remove(duplicate)
				changed = True
		else:
			target = sidebar.append(
				"items",
				{
					"label": page["label"],
					"type": "Link",
					"link_type": "Page",
					"link_to": page["link_to"],
					"icon": "warehouse",
					"idx": len(items) + 1,
				},
			)
			items = list(sidebar.get("items") or [])
			changed = True

		desired = {
			"label": page["label"],
			"type": "Link",
			"link_type": "Page",
			"link_to": page["link_to"],
			"icon": "warehouse",
		}
		for fieldname, value in desired.items():
			if getattr(target, fieldname, None) != value:
				setattr(target, fieldname, value)
				changed = True
		targets.append(target)

	for target in targets:
		items.remove(target)
	bin = next(
		(
			row for row in items
			if getattr(row, "link_type", None) == "DocType" and getattr(row, "link_to", None) == "Bin"
		),
		None,
	)
	if bin:
		items.remove(bin)
	top_index = next(
		(
			index
			for index, row in enumerate(items)
			if getattr(row, "link_type", None) == "Dashboard" and getattr(row, "link_to", None) == "Stock"
		),
		-1,
	)
	if top_index < 0:
		top_index = next(
			(
				index for index, row in enumerate(items)
				if getattr(row, "link_type", None) == "Workspace" and getattr(row, "link_to", None) == "Stock"
			),
			-1,
		)
	original_order = list(sidebar.get("items") or [])
	for offset, target in enumerate(targets):
		items.insert(top_index + 1 + offset, target)
	if bin:
		report_index = next(
			(
				index for index, row in enumerate(items)
				if getattr(row, "link_to", None) == "Stock Projected Qty"
			),
			-1,
		)
		if report_index < 0:
			report_index = next(
				(
					index for index, row in enumerate(items)
					if getattr(row, "type", None) == "Section Break"
					and getattr(row, "label", None) in {"Reports", "报表"}
				),
				len(items) - 1,
			)
		items.insert(report_index + 1, bin)
	if items != original_order:
		changed = True

	sidebar.items[:] = items
	for index, row in enumerate(sidebar.items, start=1):
		if getattr(row, "idx", None) != index:
			row.idx = index
			changed = True
	return changed


def install_inventory_capability() -> None:
	ensure_inventory_trace_fields()
	ensure_stock_sidebar_inventory_pages()


def after_install() -> None:
	install_inventory_capability()


def after_migrate() -> None:
	install_inventory_capability()
