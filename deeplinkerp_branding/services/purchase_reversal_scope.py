"""Pure, bounded identities for native stock/accounting cancellation effects.

No controller calculations, row locks, valuation, ledger writes, Bin creation or
executor calls belong here. Stock selectors follow stock_ledger's >= row anchors;
accounting selectors follow RIV's independent item/warehouse sets. The result is
evidence for a later transaction boundary, not authorization to cancel anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import frappe
from erpnext.stock.utils import get_combine_datetime
from frappe.utils import get_datetime

from . import purchase_consistency as consistency
from . import purchase_payment_service as service

MAX_PAIRS = 2500
MAX_VOUCHERS = 5000
MAX_SLES = 50000


@dataclass(frozen=True, order=True)
class StockPair:
	item_code: str
	warehouse: str


@dataclass(frozen=True, order=True)
class DocumentIdentity:
	doctype: str
	name: str


@dataclass(frozen=True)
class ReversalScope:
	company: str
	posting_datetime: str
	pairs: tuple[StockPair, ...]
	sources: tuple[DocumentIdentity, ...]
	vouchers: tuple[DocumentIdentity, ...]
	sles: tuple[str, ...] = ()
	anchors: tuple[tuple[StockPair, str], ...] = ()


_NATIVE = {
	"Purchase Order": "erpnext.buying.doctype.purchase_order.purchase_order.PurchaseOrder",
	"Purchase Receipt": "erpnext.stock.doctype.purchase_receipt.purchase_receipt.PurchaseReceipt",
	"Purchase Invoice": "erpnext.accounts.doctype.purchase_invoice.purchase_invoice.PurchaseInvoice",
	"Payment Entry": "erpnext.accounts.doctype.payment_entry.payment_entry.PaymentEntry",
	"Material Request": "erpnext.stock.doctype.material_request.material_request.MaterialRequest",
	"Stock Entry": "erpnext.stock.doctype.stock_entry.stock_entry.StockEntry",
	"Delivery Note": "erpnext.stock.doctype.delivery_note.delivery_note.DeliveryNote",
	"Sales Invoice": "erpnext.accounts.doctype.sales_invoice.sales_invoice.SalesInvoice",
	"Stock Reconciliation": "erpnext.stock.doctype.stock_reconciliation.stock_reconciliation.StockReconciliation",
	"Landed Cost Voucher": "erpnext.stock.doctype.landed_cost_voucher.landed_cost_voucher.LandedCostVoucher",
}
_BUYING = {"Purchase Order", "Purchase Receipt", "Purchase Invoice"}
_STOCK = {
	"Purchase Receipt",
	"Purchase Invoice",
	"Stock Entry",
	"Delivery Note",
	"Sales Invoice",
	"Stock Reconciliation",
}
_ROW_FIELDS = {
	"item_code",
	"stock_uom",
	"warehouse",
	"from_warehouse",
	"rejected_warehouse",
	"rejected_qty",
	"qty",
	"stock_qty",
	"s_warehouse",
	"t_warehouse",
	"target_warehouse",
	"purchase_order",
	"purchase_order_item",
	"po_detail",
	"purchase_receipt",
	"pr_detail",
	"material_request",
	"material_request_item",
	"purchase_invoice",
	"purchase_invoice_item",
	"delivered_by_supplier",
	"is_finished_item",
	"sales_order",
	"sales_order_item",
	"sales_order_packed_item",
	"delivery_note_item",
	"sales_invoice_item",
}
_ROW_FIELDS |= {
	"against_stock_entry",
	"ste_detail",
	"original_item",
	"subcontracted_item",
	"allow_alternative_item",
	"job_card",
	"scio_detail",
	"project",
	"quality_inspection",
}
_SLE_FIELDS = [
	"name",
	"company",
	"voucher_type",
	"voucher_no",
	"voucher_detail_no",
	"item_code",
	"warehouse",
	"posting_datetime",
	"posting_date",
	"posting_time",
	"creation",
	"is_cancelled",
	"actual_qty",
	"dependant_sle_voucher_detail_no",
	"recalculate_rate",
]
_SERIAL_BATCH_FIELDS = {
	"has_serial_no",
	"has_batch_no",
	"serial_no",
	"rejected_serial_no",
	"batch_no",
	"serial_and_batch_bundle",
	"rejected_serial_and_batch_bundle",
	"auto_created_serial_and_batch_bundle",
}


def _reject(reason):
	consistency.operation.reject("无法安全核查采购冲销范围：" + reason, "purchase_reversal_scope_invalid")


def _canonical(doc, *, opaque=False):
	"""Require the audited native controller and only the known extension MRO."""
	path = _NATIVE.get(doc.doctype)
	if not path:
		_reject("不支持的原生单据或来源路径")
	boundary = "deeplinkerp_branding.services.purchase_consistency.ProcurementControllerBoundary"
	mes = "mes_integration.mes_integration.material_request.MESMaterialRequestPerformanceMixin"
	range_boundary = "deeplinkerp_branding.services.purchase_repost_boundary.NativeRangeBoundary"
	status_boundary = "deeplinkerp_branding.services.purchase_repost_boundary.NativeStatusBoundary"
	allowed = (
		{boundary}
		if doc.doctype in (*_BUYING, "Payment Entry")
		else {mes, range_boundary}
		if doc.doctype == "Material Request"
		else {range_boundary}
	)
	if doc.doctype in ("Purchase Order", "Purchase Receipt", "Material Request"):
		allowed.add(status_boundary)
	if frappe.get_hooks("override_doctype_class", {}).get(doc.doctype):
		_reject("原生控制器已被替换")
	extension_paths = frappe.get_hooks("extend_doctype_class", {}).get(doc.doctype, [])
	extensions = set(extension_paths)
	if not extensions <= allowed:
		_reject("未核查的原生控制器扩展")
	native = frappe.get_attr(path)
	from frappe.model.base_document import get_controller

	controller = get_controller(doc.doctype)
	from frappe.model.document import LazyDocument, get_lazy_controller

	actual_type = type(doc)
	lazy_native = False
	if actual_type is not controller:
		lazy = get_lazy_controller(doc.doctype)
		lazy_native = (
			actual_type is lazy
			and lazy.__bases__ == (LazyDocument, controller)
			and (lazy.__mro__[:3] == (lazy, LazyDocument, controller))
		)
	if (actual_type is not controller and not lazy_native) or native not in controller.__mro__:
		_reject("原生控制器身份不一致")
	expected = (*(frappe.get_attr(path) for path in reversed(extension_paths)), native)
	if (not extension_paths and controller is not native) or (
		extension_paths
		and (
			controller.__bases__ != expected
			or controller.__mro__[: len(expected) + 1] != (controller, *expected)
		)
	):
		_reject("原生控制器实际继承路径未经核查")
	if doc.doctype == "Material Request" and mes in extensions:
		# The installed mixin delegates ordinary MR to native requested-qty math
		# but MES sources enqueue a different Bin path (material_request.py82-96).
		from mes_integration.mes_integration.material_request import is_mes_material_request

		_fields(doc.doctype, {"custom_request_source"})
		if is_mes_material_request(doc) and not opaque:
			_reject("MES 物料请求使用未支持的异步库存来源路径")


def _datetime(doc):
	date = doc.get("posting_date") or doc.get("transaction_date")
	if not date:
		_reject("原生单据日期缺失")
	return str(get_datetime(get_combine_datetime(date, doc.get("posting_time") or "00:00:00")))


def _fields(doctype, names, parenttype=None):
	# Optional native fields differ by controller; every field that exists and
	# can influence this resolver must pass the current actor's field permission.
	meta = frappe.get_meta(doctype)
	service._require_fields(doctype, {name for name in names if meta.has_field(name)}, parenttype)


def _child_parent(doc, row, table):
	if row.get("parent") and (
		row.parent != doc.name or row.parenttype != doc.doctype or row.parentfield != table
	):
		_reject("原生明细父单据身份不一致")


def _matches(row, filters):
	"""Match cached system evidence so repeated queries count unseen SLE only."""
	for fieldname, value in filters.items():
		actual = row.get(fieldname)
		if not isinstance(value, (tuple, list)):
			if actual != value:
				return False
			continue
		operator, expected = value
		if (operator == "in" and actual not in expected) or (operator == "not in" and actual in expected):
			return False
		if (operator == ">=" and str(actual) < str(expected)) or (operator == ">" and actual <= expected):
			return False
		if (operator == "!=" and actual == expected) or (
			operator == "is" and expected == "not set" and actual
		):
			return False
	return True


@dataclass
class _ScopeBudget:
	"""Private per-phase UNION proof, shared only by the existing collectors."""

	pairs: set = field(default_factory=set)
	identities: set = field(default_factory=set)
	sles: dict = field(default_factory=dict)
	documents: dict = field(default_factory=dict)

	def identity(self, identity):
		if not identity.name:
			return  # an unnamed incoming draft is not a fabricated identity
		if identity not in self.identities and len(self.identities) >= MAX_VOUCHERS:
			_reject("原生单据联合范围超过安全上限")
		self.identities.add(identity)

	def pair(self, pair):
		if pair not in self.pairs and len(self.pairs) >= MAX_PAIRS:
			_reject("物料仓库联合范围超过安全上限")
		self.pairs.add(pair)


class _Collector:
	def __init__(
		self,
		root,
		*,
		current=False,
		stock_entry_lifecycle=True,
		opaque=False,
		boundary_gate=False,
		budget=None,
		cancellation=False,
		allow_disabled_warehouses=False,
	):
		self.current = current
		self.stock_entry_lifecycle = stock_entry_lifecycle
		self.opaque = opaque
		self.boundary_gate = boundary_gate
		self.cancellation = cancellation
		# Only an existing RIV's historical closure uses this. New stock writes
		# and cancellation acceptance retain the enabled-warehouse requirement.
		self.allow_disabled_warehouses = allow_disabled_warehouses
		# Native ordinary non-stock PI drafts may preserve a manual draft PR
		# reference (existing auto-invoice completeness scenario). This is only
		# a protected read identity, never a submit/repost/cancel capability.
		self.allow_draft_receipts = (
			boundary_gate
			and root.doctype == "Purchase Invoice"
			and root.docstatus == 0
			and not root.get("update_stock")
		)
		self.budget = budget
		self.root = DocumentIdentity(root.doctype, root.name or "")
		self.company = root.get("company")
		self.posting_datetime = _datetime(root)
		if not self.company:
			_reject("公司缺失")
		service._read("Company", self.company)
		self.documents = {}
		self.sources = set()
		self.pairs = set()
		self.vouchers = set()
		self.sles = {}
		self.anchors = {}
		self.scanned = {}
		self.expanded = set()
		self.propagated = set()
		self.items = {}
		self.warehouses = {}
		self.potential_billing_receipts = set()
		self.add_document(root)

	def check_serial_batch(self, row):
		# Only the new accepted cancellation contract excludes this native
		# engine path. Ordinary stock writers retain their native support.
		if self.cancellation and any(row.get(field) for field in _SERIAL_BATCH_FIELDS):
			_reject("序列号或批次库存路径尚未支持采购异步冲销")

	def read_item(self, code):
		fields = {"is_stock_item", "stock_uom", "disabled"}
		if self.cancellation:
			fields |= {"has_serial_no", "has_batch_no"}
		item = self.items.get(code)
		if item is None:
			item = self.items[code] = service._read("Item", code, fields)
		self.check_serial_batch(item)
		return item

	def read(self, doctype, name, *, source=True):
		identity = DocumentIdentity(doctype, name)
		if identity not in self.documents:
			doc = service._read(doctype, name)
			self.add_document(doc, source=source)
		elif source and identity != self.root:
			self.add_source(identity)
		return self.documents[identity]

	def add_source(self, identity):
		if identity not in self.sources and len(self.sources) >= MAX_VOUCHERS:
			_reject("原生来源单据范围超过安全上限")
		self.sources.add(identity)

	def add_pair(self, item_code, warehouse, anchor=None):
		if not item_code or not warehouse:
			_reject("物料或仓库身份缺失")
		item = self.read_item(item_code)
		if not item.is_stock_item or item.get("disabled"):
			_reject("库存流水物料不是有效库存物料")
		location = self.warehouses.setdefault(warehouse, None)
		if location is None:
			location = self.warehouses[warehouse] = service._read(
				"Warehouse", warehouse, {"company", "is_group", "disabled"}
			)
		if location.company != self.company or location.is_group or (
			location.disabled and not self.allow_disabled_warehouses
		):
			_reject("仓库公司或状态不一致")
		pair = StockPair(item_code, warehouse)
		if self.budget is not None:
			self.budget.pair(pair)
		if pair not in self.pairs and len(self.pairs) >= MAX_PAIRS:
			_reject("物料仓库范围超过安全上限")
		self.pairs.add(pair)
		if anchor and (pair not in self.anchors or anchor < self.anchors[pair]):
			self.anchors[pair] = anchor

	def row_pairs(self, doc, row, table):
		"""Pairs from this exact native detail, never a same-item sibling row."""
		_child_parent(doc, row, table)
		if self.cancellation:
			_fields(row.doctype, _SERIAL_BATCH_FIELDS, doc.doctype)
			self.check_serial_batch(row)
		if table == "supplied_items":
			if not doc.get("is_old_subcontracting_flow"):
				return set()
			_fields(doc.doctype, {"supplied_items", "supplier_warehouse"})
			_fields(row.doctype, {"rm_item_code", "stock_uom", "reserve_warehouse"}, doc.doctype)
			warehouse = (
				row.get("reserve_warehouse")
				if doc.doctype == "Purchase Order"
				else doc.get("supplier_warehouse")
			)
			if not row.get("rm_item_code") or not warehouse:
				_reject("原生供料物料或仓库缺失")
			item = self.read_item(row.rm_item_code)
			if not item.is_stock_item or item.get("disabled") or row.get("stock_uom") != item.stock_uom:
				_reject("原生供料物料状态或库存单位不一致")
			return {StockPair(row.rm_item_code, warehouse)}
		_fields(row.doctype, _ROW_FIELDS, doc.doctype)
		code = row.get("item_code")
		if not code:
			_reject("原生明细物料缺失")
		native_item = self.read_item(code)
		if native_item.get("disabled") or (row.get("stock_uom") and row.stock_uom != native_item.stock_uom):
			_reject("原生明细物料状态或库存单位不一致")
		stock = doc.doctype in _STOCK and (
			doc.doctype not in ("Purchase Invoice", "Sales Invoice") or doc.get("update_stock")
		)
		quantity_bins = doc.doctype in ("Purchase Order", "Material Request")
		if (
			not native_item.is_stock_item
			or not (stock or quantity_bins)
			or (quantity_bins and row.get("delivered_by_supplier"))
		):
			return set()
		warehouse_fields = ("s_warehouse", "t_warehouse") if doc.doctype == "Stock Entry" else ("warehouse",)
		if doc.doctype in ("Purchase Receipt", "Purchase Invoice"):
			warehouse_fields += ("from_warehouse",)
			if row.get("rejected_qty"):
				warehouse_fields += ("rejected_warehouse",)
		if doc.doctype in ("Delivery Note", "Sales Invoice"):
			warehouse_fields += ("target_warehouse",)
		return {StockPair(code, row.get(field)) for field in warehouse_fields if row.get(field)}

	def document_pairs(self, doc):
		result = set()
		for table in ("items", "packed_items", "supplied_items"):
			for row in doc.get(table) or []:
				result.update(self.row_pairs(doc, row, table))
		return result

	def add_document(self, doc, *, source=False):
		identity = DocumentIdentity(doc.doctype, doc.name or "")
		if identity in self.documents:
			if source and identity != self.root:
				self.add_source(identity)
			return
		_canonical(
			doc,
			**(
				{"opaque": True}
				if self.opaque or (self.boundary_gate and doc.doctype == "Material Request")
				else {}
			),
		)
		doc.check_permission("read")
		_fields(
			doc.doctype,
			{
				"company",
				"supplier",
				"items",
				"packed_items",
				"supplied_items",
				"references",
				"posting_date",
				"posting_time",
				"transaction_date",
				"update_stock",
				"is_return",
				"return_against",
				"is_old_subcontracting_flow",
				"is_subcontracted",
				"subcontracting_order",
				"subcontracting_inward_order",
				"supplier_warehouse",
				"purchase_order",
				"outgoing_stock_entry",
				"add_to_transit",
				"work_order",
				"job_card",
				"project",
				"asset_repair",
				"pick_list",
				"source_stock_entry",
				"inspection_required",
				"update_billed_amount_in_purchase_receipt",
				"purchase_receipts",
				"vendor_invoices",
				"party_type",
				"party",
				"purpose",
				"status",
				"custom_operating_source",
				"custom_operating_recognition",
				"inter_company_order_reference",
				"inter_company_reference",
				"inter_company_invoice_reference",
			},
		)
		if doc.company != self.company or doc.get("docstatus") == 2:
			_reject("单据公司或状态不一致")
		if (
			source
			and doc.docstatus != 1
			and not (self.allow_draft_receipts and doc.doctype == "Purchase Receipt" and doc.docstatus == 0)
		):
			_reject("原生来源必须已提交")
		if (
			source
			and doc.doctype == "Purchase Order"
			and doc.get("status") in ("Closed", "Cancelled", "On Hold")
		):
			_reject("原生采购来源已关闭或暂停")
		if self.budget is not None:
			self.budget.identity(identity)
			if source or identity != self.root:
				self.budget.documents.setdefault(identity, doc)
		if not self.opaque and (
			doc.get("subcontracting_order")
			or (doc.get("is_subcontracted") and not doc.get("is_old_subcontracting_flow"))
		):
			_reject("不支持的新委外来源路径")
		self.documents[identity] = doc  # cycle guard; a failure never returns partial evidence
		consistency.check_operating_dependencies(doc)
		if source and identity != self.root:
			self.add_source(identity)
		for pair in self.document_pairs(doc):
			self.add_pair(pair.item_code, pair.warehouse)
		if doc.doctype in (*_BUYING, "Payment Entry"):
			consistency.check_sales_dependencies(doc, reader=self.read)
		if doc.doctype in _BUYING:
			consistency.check_sources(
				doc,
				reader=self.read,
				validate_execution=False,
				allow_draft_receipts=self.allow_draft_receipts,
			)
		if doc.doctype == "Payment Entry":
			_fields("Payment Entry Reference", {"reference_doctype", "reference_name"}, "Payment Entry")
			if doc.party_type != "Supplier":
				_reject("不支持的付款来源路径")
			for row in doc.get("references") or []:
				_child_parent(doc, row, "references")
				if (
					row.reference_doctype not in ("Purchase Order", "Purchase Invoice")
					or not row.reference_name
				):
					_reject("不支持的付款来源路径")
				target = self.read(row.reference_doctype, row.reference_name)
				if target.supplier != doc.party:
					_reject("付款来源供应商不一致")
		if self.opaque and doc.doctype not in (*_BUYING, "Payment Entry"):
			if doc.doctype != "Stock Entry":
				return  # Only site-wide absence authorizes opaque lifecycle.
			from .purchase_repost_boundary import _opaque

			if _opaque(doc, current=self.current):
				return
		identities, _ = consistency.resolve_source_documents(
			doc, reader=self.read, stock_entry_lifecycle=self.stock_entry_lifecycle and identity == self.root
		)
		for doctype, name in sorted(identities):
			self.read(doctype, name)
		if identity == self.root and doc.doctype == "Purchase Invoice":
			self.potential_billing_receipts.update(
				DocumentIdentity("Purchase Receipt", row.purchase_receipt)
				for row in doc.items
				if row.get("pr_detail")
			)
			# Native IF pr_detail ELSE po_detail: unrelated details on the same
			# PO and arbitrary source-only PRs must never become repost seeds.
			self.potential_billing_receipts.update(
				self.billing_receipts(
					{row.po_detail for row in doc.items if not row.get("pr_detail") and row.get("po_detail")}
				)
			)
		if doc.get("return_against"):
			target = self.read(doc.doctype, doc.return_against)
			if target.get("supplier") != doc.get("supplier") or target.get("is_return"):
				_reject("原生退货来源不一致")
		if doc.doctype == "Landed Cost Voucher":
			for row in doc.get("purchase_receipts") or []:
				_fields(row.doctype, {"receipt_document_type", "receipt_document"}, doc.doctype)
				_child_parent(doc, row, "purchase_receipts")
				if row.receipt_document_type not in ("Purchase Receipt", "Purchase Invoice", "Stock Entry"):
					_reject("不支持的到岸成本来源路径")
				target = self.read(row.receipt_document_type, row.receipt_document)
				if target.doctype == "Purchase Invoice" and not target.update_stock:
					_reject("到岸成本来源应付未更新库存")
			# Native LCV also writes claimed_landed_cost_amount on these actual
			# vendor PIs (LCV.py289-305); their identity is not an optional alias.
			for row in doc.get("vendor_invoices") or []:
				_fields(row.doctype, {"vendor_invoice"}, doc.doctype)
				_child_parent(doc, row, "vendor_invoices")
				if not row.get("vendor_invoice"):
					_reject("到岸成本供应商应付身份缺失")
				self.read("Purchase Invoice", row.vendor_invoice)

	def billing_receipts(self, details):
		# prepare_document's native billing transition also targets every active
		# non-return PR against the real PO details. Identity-only bounded SELECT
		# mirrors get_purchase_receipts_against_po_details; no billed math runs.
		details = sorted(details)
		if not details:
			return set()

		def candidate(doc):
			return (
				doc.docstatus == 1
				and not doc.get("is_return")
				and any(row.get("purchase_order_item") in details for row in doc.items)
			)

		candidates = {
			identity
			for identity, doc in self.documents.items()
			if identity.doctype == "Purchase Receipt" and candidate(doc)
		}
		identities = self.budget.identities if self.budget is not None else self.sources
		if self.budget is not None:
			for identity, doc in tuple(self.budget.documents.items()):
				if identity.doctype == "Purchase Receipt" and candidate(doc):
					self.read(identity.doctype, identity.name)
					candidates.add(identity)
		remaining = MAX_VOUCHERS - len(identities)
		known = tuple(
			sorted(identity.name for identity in identities if identity.doctype == "Purchase Receipt")
		) or ("",)
		rows = frappe.db.sql(
			"SELECT DISTINCT pri.parent FROM `tabPurchase Receipt Item` pri "
			"INNER JOIN `tabPurchase Receipt` pr ON pr.name=pri.parent "
			"WHERE pri.purchase_order_item IN %(details)s AND pr.docstatus=1 AND pr.is_return=0 "
			"AND pri.parent NOT IN %(known)s ORDER BY pri.parent LIMIT %(limit)s"
			+ (" FOR UPDATE" if self.current else ""),
			{"details": tuple(details), "known": known, "limit": remaining + 1},
			as_dict=True,
		)
		if len(rows) > remaining:
			_reject("原生账务来源范围超过安全上限")
		for row in rows:
			doc = self.read("Purchase Receipt", row.parent)
			if not candidate(doc):
				_reject("原生账务候选收货明细身份不一致")
			candidates.add(DocumentIdentity(doc.doctype, doc.name))
		return candidates

	def select(self, filters):
		# A repeated frontier must not spend the remaining budget on an already
		# consumed prefix and conceal later rows. Exclude unique names at SQL.
		query = dict(filters)
		seen = self.budget.sles if self.budget is not None else self.sles
		if seen:
			query["name"] = ["not in", sorted(seen)]
		remaining = MAX_SLES - len(seen)
		fields = _SLE_FIELDS
		if self.cancellation:
			meta = frappe.get_meta("Stock Ledger Entry")
			fields = fields + sorted(field for field in _SERIAL_BATCH_FIELDS if meta.has_field(field))
		rows = frappe.db.get_values(
			"Stock Ledger Entry",
			query,
			fields,
			as_dict=True,
			order_by="posting_datetime asc, creation asc, name asc",
			limit=remaining + 1,
			**({"for_update": True} if self.current else {}),
		)
		if len(rows) > remaining:
			_reject("库存流水范围超过安全上限")
		for row in rows:
			if not row.name or row.company != self.company:
				_reject("库存流水公司或身份不一致")
			seen[row.name] = row
		matched = [row for row in seen.values() if _matches(row, filters)]
		for row in matched:
			if row.company != self.company:
				_reject("库存流水公司或身份不一致")
			self.check_serial_batch(row)
			self.sles[row.name] = row
		return matched

	def add_voucher(self, doctype, name):
		identity = DocumentIdentity(doctype, name)
		if identity not in self.vouchers:
			if len(self.vouchers) >= MAX_VOUCHERS:
				_reject("受影响凭证范围超过安全上限")
			doc = self.read(doctype, name, source=False)
			if doc.docstatus != 1:
				_reject("受影响原生凭证必须已提交")
			self.vouchers.add(identity)

	def consume(self, rows, *, propagate=False):
		for row in rows:
			self.add_voucher(row.voucher_type, row.voucher_no)
			doc = self.documents[DocumentIdentity(row.voucher_type, row.voucher_no)]
			details = [
				(field, item)
				for field in ("items", "packed_items", "supplied_items")
				for item in doc.get(field) or []
				if item.name == row.voucher_detail_no
			]
			if (
				len(details) != 1
				or (details[0][1].get("item_code") or details[0][1].get("rm_item_code")) != row.item_code
			):
				_reject("库存流水明细身份或物料不一致")
			pair = StockPair(row.item_code, row.warehouse)
			table, detail = details[0]
			if pair not in self.row_pairs(doc, detail, table):
				_reject("库存流水没有实际原生仓库来源")
			if (
				propagate
				and (row.actual_qty < 0 or row.get("recalculate_rate"))
				and doc.doctype == "Stock Entry"
				and doc.purpose == "Manufacture"
				and doc.get("work_order")
				and any(
					item.get("is_finished_item") and item.get("t_warehouse") and not item.get("s_warehouse")
					for item in doc.items
				)
			):
				# Native SE1503-1558 may read costs from other consumption SEs.
				# No bounded identity/ACL planner for that rate path exists yet.
				# This is a stock-rate edge, including a recalculate_rate FG SLE,
				# not every GL-only child with a WO (SLE1193-1221, SE2107-2120).
				settings = frappe.get_meta("Manufacturing Settings")
				flags = ("material_consumption", "get_rm_cost_from_consumption_entry")
				if not all(settings.has_field(field) for field in flags):
					_reject("原生制造成本配置元数据缺失")
				filters = {
					"work_order": doc.work_order,
					"docstatus": 1,
					"purpose": "Material Consumption for Manufacture",
				}
				if all(self.setting("Manufacturing Settings", field) for field in flags) and (
					frappe.db.get_value("Stock Entry", filters, "name", for_update=True)
					if self.current
					else frappe.db.exists("Stock Entry", filters)
				):
					_reject("制造消耗成本来源尚无有界权限范围适配")
			anchor = str(
				get_datetime(row.posting_datetime or get_combine_datetime(row.posting_date, row.posting_time))
			)
			self.add_pair(row.item_code, row.warehouse, anchor if propagate else None)
			if not propagate or row.name in self.propagated:
				continue
			self.propagated.add(row.name)
			if row.dependant_sle_voucher_detail_no:
				target_detail = next(
					(
						item
						for item in doc.get("items") or []
						if item.name == row.dependant_sle_voucher_detail_no
					),
					None,
				)
				if not target_detail:
					_reject("原生依赖明细不属于实际父凭证")
				if doc.doctype == "Stock Entry" and doc.purpose == "Repack":
					# Native repack expands every positive output, including a
					# different item/detail, rather than only its FG pointer.
					targets = self.select(
						{
							"voucher_type": "Stock Entry",
							"voucher_no": doc.name,
							"actual_qty": [">", 0],
							"is_cancelled": 0,
						}
					)
					# Native get_all's != filter treats NULL as an empty value.
					# db.get_values uses SQL NULL != value; filter this already
					# required whole-voucher evidence without dropping outputs.
					self.consume(
						[
							target
							for target in targets
							if target.dependant_sle_voucher_detail_no != row.dependant_sle_voucher_detail_no
						],
						propagate=True,
					)
					continue
				targets = self.select(
					{
						"voucher_detail_no": row.dependant_sle_voucher_detail_no,
						"is_cancelled": 0,
						"dependant_sle_voucher_detail_no": ["is", "not set"],
					}
				)
				if not targets or any(
					(target.voucher_type, target.voucher_no) != (row.voucher_type, row.voucher_no)
					for target in targets
				):
					_reject("原生依赖库存明细缺失或错配")
				# Validate target child identities before adding their own anchor.
				for target in targets:
					self.consume([target], propagate=True)

	def expand_voucher(self, identity):
		active = self.select(
			{"voucher_type": identity.doctype, "voucher_no": identity.name, "is_cancelled": 0}
		)
		self.consume(active)
		# GL sorter selects active SLE by voucher number without type; a collision
		# would broaden native writes. Refuse explicitly rather than omit it.
		same_number = self.select({"voucher_no": identity.name, "company": self.company, "is_cancelled": 0})
		if any(row.voucher_type != identity.doctype for row in same_number):
			_reject("不同原生凭证类型使用相同凭证编号")

	def document_gl_candidates(self, doc):
		if doc.doctype not in _STOCK or (
			doc.doctype in ("Purchase Invoice", "Sales Invoice") and not doc.get("update_stock")
		):
			return
		items = {row.item_code for table in ("items", "packed_items") for row in doc.get(table) or []}
		warehouses = {
			row.get(field)
			for table in ("items", "packed_items")
			for row in doc.get(table) or []
			for field in (
				("warehouse", "s_warehouse", "t_warehouse")
				if doc.doctype == "Stock Entry"
				else ("warehouse",)
			)
			if row.get(field)
		}
		raw = self.select({"voucher_type": doc.doctype, "voucher_no": doc.name}) if doc.name else []
		items.update(row.item_code for row in raw)
		warehouses.update(row.warehouse for row in raw)
		filters = {"company": self.company, "is_cancelled": 0, "posting_datetime": [">=", _datetime(doc)]}
		# Native get_future_stock_vouchers omits each empty dimension, not both.
		if items:
			filters["item_code"] = ["in", sorted(items)]
		if warehouses:
			filters["warehouse"] = ["in", sorted(warehouses)]
		self.consume(self.select(filters))  # GL-only matches add footprint, never stock edges.

	def seed_document(self, doc):
		"""One verified native transaction context, reused for billing PRs."""
		if doc.name:
			self.add_voucher(doc.doctype, doc.name)
		self.posting_datetime = min(self.posting_datetime, _datetime(doc))
		if doc.name:
			self.consume(self.select({"voucher_type": doc.doctype, "voucher_no": doc.name}), propagate=True)
		self.document_gl_candidates(doc)

	def setting(self, doctype, field):
		return frappe.db.get_single_value(
			doctype, field, **({"cache": False, "for_update": True} if self.current else {})
		)

	def closure(self, *, require_bins=True, proposed_stock=False):
		root = self.documents[self.root]
		if proposed_stock:
			# Before a native stock write the root's SLE does not yet exist.
			# Its actual stock detail paths are the possible newly written raw
			# seed, not arbitrary source-only pairs or GL-child extra rows.
			for pair in self.document_pairs(root):
				self.add_pair(pair.item_code, pair.warehouse, _datetime(root))
		self.seed_document(root)
		if root.doctype == "Purchase Invoice":
			setting = "set_landed_cost_based_on_purchase_invoice_rate"
			if not frappe.get_meta("Buying Settings").has_field(setting):
				_reject("原生采购成本重贴配置元数据缺失")
			if self.setting("Buying Settings", setting) and not (
				root.get("is_return") and not root.get("update_billed_amount_in_purchase_receipt")
			):
				# FIFO may leave some candidates unchanged. This is a bounded
				# potential lease footprint, NOT a claim each PR created a RIV.
				# Later execution must capture only actual native RIV roots.
				for identity in sorted(self.potential_billing_receipts):
					self.seed_document(self.documents[identity])
		self.expand_frontier(require_bins=require_bins)

	def expand_frontier(self, *, require_bins=True):
		"""Same real SLE propagation for native transaction and pair roots."""
		while True:
			pending = sorted(self.vouchers - self.expanded)
			frontier = sorted(
				(pair, anchor)
				for pair, anchor in self.anchors.items()
				if pair not in self.scanned or anchor < self.scanned[pair]
			)
			if not pending and not frontier:
				break
			for identity in pending:
				self.expanded.add(identity)
				self.expand_voucher(identity)
			for pair, anchor in frontier:
				self.scanned[pair] = anchor
				self.consume(
					self.select(
						{
							"item_code": pair.item_code,
							"warehouse": pair.warehouse,
							"posting_datetime": [">=", anchor],
							"is_cancelled": 0,
						}
					),
					propagate=True,
				)
		for pair in sorted(self.pairs) if require_bins else ():
			rows = frappe.db.get_values(
				"Bin",
				{"item_code": pair.item_code, "warehouse": pair.warehouse},
				["name"],
				as_dict=True,
				limit=2,
				**({"for_update": True} if self.current else {}),
			)
			if len(rows) != 1:
				_reject("原生库存 Bin 缺失或重复")

	def result(self):
		return ReversalScope(
			self.company,
			self.posting_datetime,
			tuple(sorted(self.pairs)),
			tuple(sorted(self.sources)),
			tuple(sorted(self.vouchers)),
			tuple(sorted(self.sles)),
			tuple((pair, str(anchor)) for pair, anchor in sorted(self.anchors.items())),
		)


def collect_document_scope(doc) -> ReversalScope:
	"""Actual proposed sources/pairs only; first-stock drafts need no existing Bin."""
	return _Collector(doc).result()


def collect_cancellation_scope(persisted_doc) -> ReversalScope:
	"""Complete bounded native closure for the four supported procurement roots.

	Other canonical stock/LCV documents are evidence identities, not cancellation
	adapters. In particular an LCV root reposts its receipts, not its own SLE.
	"""
	if persisted_doc.doctype not in (*_BUYING, "Payment Entry"):
		_reject("不支持该原生单据的根取消路径")
	if not persisted_doc.name or persisted_doc.docstatus != 1:
		_reject("取消根单据必须是已保存的已提交单据")
	root = service._read(persisted_doc.doctype, persisted_doc.name)
	if root.docstatus != 1:
		_reject("取消根单据当前状态不是已提交")
	# PO/PE and a non-stock PI without receipt-rate adjustment cannot invoke
	# the stock repost engine. Preserve their normal synchronous semantics.
	stock_cancellation = root.doctype == "Purchase Receipt" or (
		root.doctype == "Purchase Invoice"
		and (
			root.get("update_stock")
			or frappe.db.get_single_value("Buying Settings", "set_landed_cost_based_on_purchase_invoice_rate")
		)
	)
	collector = _Collector(root, cancellation=bool(stock_cancellation))
	collector.closure()
	return collector.result()


def collect_boundary_scope(
	doc, *, current=False, stock_entry_lifecycle=True, opaque=False, budget=None,
	allow_disabled_warehouses=False,
):
	"""Internal ordinary native write footprint; NOT a new cancellation API.

	Submitted stock writes may reach real future selectors. LCV valuation uses
	its verified receipt contexts; no arbitrary source-only document is seeded.
	Drafts keep the first-stock/no-Bin behavior. The public B1 APIs stay pure.
	"""
	from contextlib import nullcontext

	with service.current_reads() if current else nullcontext():
		collector = _Collector(
			doc,
			current=current,
			stock_entry_lifecycle=stock_entry_lifecycle,
			opaque=opaque,
			boundary_gate=True,
			budget=budget,
			allow_disabled_warehouses=allow_disabled_warehouses,
		)
		if doc.docstatus == 1 and not opaque:
			if doc.doctype in _STOCK:
				collector.closure(require_bins=False, proposed_stock=True)
			elif doc.doctype == "Landed Cost Voucher":
				for row in doc.purchase_receipts:
					collector.seed_document(
						collector.documents[DocumentIdentity(row.receipt_document_type, row.receipt_document)]
					)
				# Existing stock frontier expansion is reused, not LCV-as-SLE.
				collector.closure(require_bins=False)
		return collector.result()


def collect_repost_pair_scope(task, *, allow_disabled_warehouses=False):
	"""Actual Item/Warehouse RIV footprint, without inventing a source document."""
	anchor = _datetime(task)
	rows = frappe.db.get_values(
		"Stock Ledger Entry",
		{
			"company": task.company,
			"item_code": task.item_code,
			"warehouse": task.warehouse,
			"posting_datetime": [">=", anchor],
			"is_cancelled": 0,
		},
		_SLE_FIELDS,
		as_dict=True,
		for_update=True,
		limit=MAX_SLES + 1,
	)
	if len(rows) > MAX_SLES:
		_reject("原生库存流水范围超过安全上限")
	if not rows:
		return ReversalScope(task.company, anchor, (StockPair(task.item_code, task.warehouse),), (), ())
	root = service._read(rows[0].voucher_type, rows[0].voucher_no)
	collector = _Collector(
		root, current=True, boundary_gate=True, allow_disabled_warehouses=allow_disabled_warehouses,
	)
	collector.add_pair(task.item_code, task.warehouse, anchor)
	collector.consume(rows, propagate=True)
	collector.expand_frontier(require_bins=False)
	return collector.result()
