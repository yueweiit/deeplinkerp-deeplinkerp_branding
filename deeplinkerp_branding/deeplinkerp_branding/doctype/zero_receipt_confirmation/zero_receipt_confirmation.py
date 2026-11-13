"""A submitted zero receipt is an audit record, never a stock transaction."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.model.document import Document


def _is_explicit_zero(value: object) -> bool:
	if value is None or isinstance(value, bool):
		return False
	try:
		quantity = Decimal(str(value).strip())
	except (InvalidOperation, ValueError):
		return False
	return quantity.is_finite() and quantity == 0


class ZeroReceiptConfirmation(Document):
	def validate(self) -> None:
		if self.docstatus == 2:
			return
		if not frappe.has_permission("Stock Entry", "create"):
			frappe.throw(_("没有创建物料移动单的权限。"), frappe.PermissionError)
		if self.docstatus == 1 and not frappe.has_permission("Stock Entry", "submit"):
			frappe.throw(_("没有提交物料移动单的权限。"), frappe.PermissionError)

		self.reason = (self.reason or "").strip()
		self.source_note = (self.source_note or "").strip()
		if not self.reason:
			frappe.throw(_("请填写零收货原因。"))
		if not self.items or len(self.items) > 100:
			frappe.throw(_("零数量入库确认单需要 1 至 100 行物料。"))
		if any(not row.material_request for row in self.items) and not self.source_note:
			frappe.throw(_("无物料需求单的明细需要填写来源说明。"))

		# Frappe checks Company user permissions on this document's Link field.
		# A separate Company read check would reject native Stock Entry roles.
		stock_entry_type = frappe.get_doc("Stock Entry Type", self.stock_entry_type)
		if stock_entry_type.purpose != "Material Receipt":
			frappe.throw(_("零数量入库确认单仅支持入库类型。"))

		seen: set[tuple[str, str, str, str]] = set()
		material_requests: dict[str, Document] = {}
		for row in self.items:
			if not _is_explicit_zero(row.received_qty):
				frappe.throw(_("第 {0} 行实际收到数量必须为 0。").format(row.idx))
			if not row.item_code or not row.target_warehouse:
				frappe.throw(_("第 {0} 行需要物料和目标仓库。").format(row.idx))
			item = frappe.get_doc("Item", row.item_code)
			item.check_permission("read")
			if item.disabled or not item.is_stock_item:
				frappe.throw(_("第 {0} 行物料不可用于库存确认。").format(row.idx))
			warehouse = frappe.get_doc("Warehouse", row.target_warehouse)
			warehouse.check_permission("read")
			if warehouse.company != self.company or warehouse.is_group or warehouse.disabled:
				frappe.throw(_("第 {0} 行目标仓库不属于所选公司或不可使用。").format(row.idx))
			if row.material_request_item and not row.material_request:
				frappe.throw(_("第 {0} 行物料需求单来源无效。").format(row.idx))
			if row.material_request:
				request = material_requests.get(row.material_request)
				if request is None:
					request = frappe.get_doc("Material Request", row.material_request)
					request.check_permission("read")
					material_requests[row.material_request] = request
				if request.company != self.company or request.docstatus != 1:
					frappe.throw(_("第 {0} 行物料需求单来源无效。").format(row.idx))
				matching_items = [
					request_item for request_item in request.items
					if request_item.item_code == row.item_code
					and (not row.material_request_item or request_item.name == row.material_request_item)
				]
				if not matching_items:
					frappe.throw(_("第 {0} 行物料需求单明细与物料不匹配。").format(row.idx))
				if len(matching_items) != 1:
					frappe.throw(_("第 {0} 行物料需求单明细无法唯一确定。").format(row.idx))
				if not row.material_request_item:
					row.material_request_item = matching_items[0].name
			key = (row.item_code, row.target_warehouse, row.material_request or "", row.material_request_item or "")
			if key in seen:
				frappe.throw(_("第 {0} 行物料、目标仓库和来源重复。").format(row.idx))
			seen.add(key)
			row.stock_uom = item.stock_uom

	def before_submit(self) -> None:
		if not frappe.has_permission("Stock Entry", "submit"):
			frappe.throw(_("没有提交物料移动单的权限。"), frappe.PermissionError)
