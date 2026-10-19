"""Small, permission checked projections and explicit native procurement actions."""
from __future__ import annotations

from frappe import _ as _i18n


import hashlib
import json
import re

import frappe
from frappe.model import get_permitted_fields
from frappe.utils import getdate

from deeplinkerp_branding.services import purchase_operation
from deeplinkerp_branding.services import purchase_payment_service as service

from .purchase_repost_boundary import procurement_entry

TARGETS = {("Purchase Receipt", "Purchase Invoice"), ("Purchase Order", "Purchase Receipt"),
           ("Purchase Order", "Purchase Invoice")}
HEADERS = {"Purchase Invoice": {"posting_date", "bill_no", "bill_date", "remarks"},
           "Purchase Receipt": {"posting_date", "remarks"}}
ITEM_EDITS = {"Purchase Invoice": {"qty", "rate"}, "Purchase Receipt": {"qty", "warehouse"}}
ITEM_FIELDS = {"item_code", "item_name", "qty", "uom", "rate", "warehouse"}
HEADER_FIELDS = {"company", "supplier", "currency", "posting_date", "remarks", "items", "taxes", "net_total", "grand_total"}
TAX_FIELDS = {"charge_type", "account_head", "description", "rate", "tax_amount", "total"}
ADVANCED = "此单据包含共享来源或高级原生设置，请打开原生单据处理"


def _fields(doctype, fields, permission="read", parenttype=None):
    fields = {field for field in fields if frappe.get_meta(doctype).has_field(field)}
    if permission == "read":
        service._require_fields(doctype, fields, parenttype)
    else:
        permitted = set(get_permitted_fields(doctype, parenttype=parenttype, permission_type=permission))
        levels = set(frappe.get_meta(doctype).get_permlevel_access(permission_type=permission, parenttype=parenttype))
        permitted.update(df.fieldname for df in frappe.get_meta(doctype).fields
                         if df.fieldtype == "Table" and df.permlevel in levels)
        if not fields <= permitted:
            frappe.throw(_i18n("没有编辑所选字段的权限"), frappe.PermissionError)
    return fields


def _version(doc, expected):
    if not expected or str(doc.modified) != str(expected):
        frappe.throw(_i18n("单据已改变，请刷新抽屉后重试"))


def _locked(doctype, name):
    return service._current(doctype, name)


def _workflow_actions(doc):
    from frappe.model.workflow import get_transitions, get_workflow, get_workflow_name, has_approval_access
    if doc.docstatus != 0:
        return []
    if doc.is_new():
        return ["Submit"] if doc.doctype in ("Purchase Receipt", "Purchase Invoice") and not get_workflow_name(doc.doctype) and doc.has_permission("submit") else []
    if get_workflow_name(doc.doctype):
        _fields(doc.doctype, {get_workflow(doc.doctype).workflow_state_field})
        return [row.action for row in get_transitions(doc)
                if has_approval_access(frappe.session.user, doc, row)]
    return ["Submit"] if doc.has_permission("submit") and doc.doctype in ("Purchase Receipt", "Purchase Invoice", "Payment Entry") else []


def _editable_fields(doc, fields, parenttype=None):
    permitted = []
    for field in sorted(fields):
        try:
            with service._quiet_link_errors():
                _fields(doc.doctype, {field}, "write", parenttype)
            permitted.append(field)
        except frappe.PermissionError:
            pass
    return permitted


def _link_fields(source_type, target):
    if target == "Purchase Receipt":
        return "purchase_order", "purchase_order_item"
    return ("purchase_order", "po_detail") if source_type == "Purchase Order" else ("purchase_receipt", "pr_detail")


def _document_source_link(doc):
    if doc.doctype == "Purchase Receipt":
        source_type = "Purchase Order"
    else:
        _fields(doc.doctype + " Item", {"purchase_receipt", "pr_detail", "purchase_order", "po_detail"}, parenttype=doc.doctype)
        source_type = "Purchase Receipt" if any(row.get("purchase_receipt") for row in doc.items) else "Purchase Order"
    link, identity = _link_fields(source_type, doc.doctype)
    _fields(doc.doctype + " Item", {link, identity, "qty", "rate"}, parenttype=doc.doctype)
    names = {row.get(link) for row in doc.items if row.get(link)}
    if not names or (doc.doctype == "Purchase Invoice" and source_type == "Purchase Order" and len(names) > 1) or any(not row.get(link) or not row.get(identity) for row in doc.items):
        frappe.throw(ADVANCED)
    return source_type, next(iter(names)) if len(names) == 1 else sorted(names)


def _sources(source):
    return source if isinstance(source, list) else [source]


def _key(row, target, source=None):
    return row.get(_link_fields(_sources(source)[0].doctype if source else "Purchase Receipt", target)[1])


def _mapping_fields(source, target):
    source.check_permission("read")
    _fields(source.doctype, HEADER_FIELDS | {"is_return", "is_subcontracted", "has_unit_price_items", "is_internal_supplier", "represents_company"})
    _fields("Purchase Taxes and Charges", TAX_FIELDS, parenttype=source.doctype)
    _fields(target, HEADER_FIELDS | HEADERS[target] | {"is_return", "is_subcontracted", "update_stock"})
    _fields(target + " Item", ITEM_FIELDS | {"conversion_factor", "pr_detail", "po_detail", "purchase_order_item", "purchase_order", "purchase_receipt"}, parenttype=target)
    _fields("Purchase Taxes and Charges", TAX_FIELDS, parenttype=target)
    source_fields = ITEM_FIELDS | {"conversion_factor", "received_qty", "purchase_order", "purchase_order_item"}
    source_fields |= {"delivered_by_supplier"} if target == "Purchase Receipt" else {"rejected_qty"}
    _fields(source.doctype + " Item", source_fields, parenttype=source.doctype)


def _invoice_source_reason(source, check_fields=True):
    """Source/settings capability only; do not run a native mapper to probe a button."""
    if check_fields:
        _mapping_fields(source, "Purchase Invoice")
    if source.docstatus != 1 or source.get("is_return"):
        return "来源必须已提交且非退货，请打开原生单据核对"
    if source.doctype == "Purchase Order":
        reason = service.order_execution_reason(source)
        if reason:
            return reason
    if source.get("is_subcontracted") or source.get("has_unit_price_items"):
        return ADVANCED
    if source.get("is_internal_supplier") and not source.get("represents_company"):
        return "内部供应商公司映射缺失，请打开原生单据核对主数据"
    if getattr(source, "is_internal_transfer", lambda: False)():
        return "此内部交易需从合法原生销售发票或交货单准备采购单据，请打开原生单据处理"
    if source.doctype == "Purchase Order":
        _fields("Buying Settings", {"pr_required"})
        if frappe.db.get_single_value("Buying Settings", "pr_required") == "Yes":
            supplier = service._read("Supplier", source.supplier, {"allow_purchase_invoice_creation_without_purchase_receipt"})
            if not supplier.allow_purchase_invoice_creation_without_purchase_receipt:
                for code in {row.item_code for row in source.items if row.item_code}:
                    item = service._read("Item", code, {"is_stock_item", "is_fixed_asset"})
                    if item.is_stock_item or item.is_fixed_asset:
                        return "原生设置要求库存或资产先采购入库；请在原生单据处理或由财务核对供应商许可"
    return ""


def _locked_source(source_type, name, target, locked_orders=None):
    # All invoice writers acquire PO -> PR -> PI locks. A later PR must share
    # the same PO lock with an earlier direct PO invoice, even with another token.
    names = sorted(name) if isinstance(name, list) else [name]
    originals = {}
    if source_type == "Purchase Receipt" and target == "Purchase Invoice":
        service._require_fields("Purchase Receipt Item", {"purchase_order", "purchase_order_item"}, "Purchase Receipt")
        warnings = []
        for source_name in names:
            original = service._source(source_type, source_name)
            originals[source_name] = set(service._source_links(original, "Purchase Order", "purchase_order", warnings))
        orders = set().union(*originals.values())
        if warnings:
            frappe.throw(service.LINK_WARNING)
        for order in sorted(orders):
            current_order = _locked("Purchase Order", order)
            if locked_orders is not None:
                locked_orders[order] = current_order
    current = [_locked(source_type, source_name) for source_name in names]
    for doc in current:
        if doc.name in originals and {row.get("purchase_order") for row in doc.items if row.get("purchase_order")} != originals[doc.name]:
            frappe.throw(_i18n("来源已改变，请刷新后重试"))
    return current if isinstance(name, list) else current[0]


def _native(source, target, target_doc=None):
    if (source.doctype, target) not in TARGETS:
        frappe.throw(_i18n("不支持此来源和目标单据"))
    if source.docstatus != 1 or source.get("is_return"):
        frappe.throw(_i18n("来源必须已提交且非退货"))
    if source.doctype == "Purchase Order":
        reason = service.order_execution_reason(source)
        if reason:
            frappe.throw(reason)
    _mapping_fields(source, target)
    if not frappe.has_permission(target, "create"):
        frappe.throw(_i18n("没有创建目标单据权限"), frappe.PermissionError)
    if target == "Purchase Invoice":
        reason = _invoice_source_reason(source, check_fields=False)
        if reason:
            frappe.throw(reason)
        if source.doctype == "Purchase Order":
            from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_invoice
        else:
            from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice
        doc = make_purchase_invoice(source.name)
        doc.update_stock = 0
        return doc
    from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
    return make_purchase_receipt(source.name, target_doc=target_doc) if target_doc else make_purchase_receipt(source.name)


def _current_maximum(source, target, for_update=True, locked_orders=None):
    """Current-read cap under the source lock; native maker still owns mapping/taxes.

    ERPNext PR's get_pending_qty is a local closure. Its rejected/returned quantity
    adjustment is mirrored here because its helper queries are snapshot reads.
    Quantities stay in each native source row's UOM, never converted independently.
    """
    if isinstance(source, list):
        result = {}
        for doc in source:
            result.update(_current_maximum(doc, target, for_update, locked_orders))
        return result
    if target == "Purchase Receipt":
        return {row.name: max(service.amount(0), service.amount(row.qty) - service.amount(row.received_qty))
                for row in source.items if not row.get("delivered_by_supplier")}
    if source.doctype == "Purchase Order":
        service._require_fields("Purchase Order Item", {"qty"}, "Purchase Order")
        service._require_fields("Purchase Invoice Item", {"purchase_order", "po_detail", "qty"}, "Purchase Invoice")
        billed = {}
        identities = {row.name for row in source.items}
        for row in frappe.db.get_values("Purchase Invoice Item", {"purchase_order": source.name, "docstatus": 1},
                                       ["po_detail", "qty"], as_dict=True, for_update=for_update):
            if row.po_detail not in identities:
                frappe.throw(service.LINK_WARNING)
            billed[row.po_detail] = billed.get(row.po_detail, service.amount(0)) + service.amount(row.qty)
        return {row.name: max(service.amount(0), service.amount(row.qty) - billed.get(row.name, service.amount(0)))
                for row in source.items}
    billed = {}
    service._require_fields("Purchase Invoice Item", {"pr_detail", "qty"}, "Purchase Invoice")
    service._require_fields("Purchase Receipt Item", {"purchase_receipt_item", "qty"}, "Purchase Receipt")
    identities = {row.name for row in source.items}
    for row in frappe.db.get_values("Purchase Invoice Item", {"purchase_receipt": source.name, "docstatus": 1},
                                   ["pr_detail", "qty"], as_dict=True, for_update=for_update):
        if row.pr_detail not in identities:
            frappe.throw(service.LINK_WARNING)
        billed[row.pr_detail] = billed.get(row.pr_detail, service.amount(0)) + service.amount(row.qty)
    parents = frappe.db.get_values("Purchase Receipt", {"return_against": source.name, "is_return": 1, "docstatus": 1},
                                   ["name"], as_dict=True, for_update=for_update)
    returned = {}
    if parents:
        for row in frappe.db.get_values("Purchase Receipt Item", {"parent": ["in", [row.name for row in parents]],
                "parenttype": "Purchase Receipt", "parentfield": "items"},
                ["purchase_receipt_item", "qty"], as_dict=True, for_update=for_update):
            returned[row.purchase_receipt_item] = returned.get(row.purchase_receipt_item, service.amount(0)) + abs(service.amount(row.qty))
    rejected = frappe.db.get_single_value("Buying Settings", "bill_for_rejected_quantity_in_purchase_invoice")
    result = {}
    for row in source.items:
        qty = service.amount(row.received_qty if rejected else row.qty) - billed.get(row.name, service.amount(0))
        if not rejected:
            returned_qty = returned.get(row.name, service.amount(0))
            if row.rejected_qty and returned_qty:
                returned_qty -= service.amount(row.rejected_qty)
            if returned_qty:
                qty = max(service.amount(0), qty - returned_qty)
        result[row.name] = max(service.amount(0), qty)
    # Native PR mapping only subtracts directly linked PR invoices. Direct PO
    # invoices already consume the same obligation; share each PO row's cap
    # once across this PR's rows, in native row order and source UOM.
    service._require_fields("Purchase Receipt Item", {"purchase_order", "purchase_order_item", "uom", "conversion_factor"}, "Purchase Receipt")
    order_names = sorted({row.get("purchase_order") for row in source.items if row.get("purchase_order")})
    remaining = {}
    order_items = {}
    service._require_fields("Purchase Order Item", {"uom", "conversion_factor"}, "Purchase Order")
    for name in order_names:
        order = (locked_orders or {}).get(name) if for_update else None
        if order is None:
            order = _locked("Purchase Order", name) if for_update else service._read("Purchase Order", name, service.SOURCE_FIELDS)
        if order.company != source.company or order.supplier != source.supplier:
            frappe.throw(service.LINK_WARNING)
        caps = _current_maximum(order, "Purchase Invoice", for_update=for_update)
        remaining.update({(name, key): value for key, value in caps.items()})
        order_items.update({(name, row.name): row for row in order.items})
    for row in source.items:
        if row.get("purchase_order"):
            key = (row.purchase_order, row.get("purchase_order_item"))
            if key not in remaining:
                frappe.throw(service.LINK_WARNING)
            original = order_items[key]
            if row.get("uom") != original.get("uom") or service.amount(row.get("conversion_factor")) != service.amount(original.get("conversion_factor")):
                frappe.throw(ADVANCED)
            result[row.name] = min(result[row.name], remaining[key])
            remaining[key] -= result[row.name]
    return result


def _invoice_capability(source):
    reason = _invoice_source_reason(source)
    if reason:
        return False, reason
    maximum = _current_maximum(source, "Purchase Invoice", for_update=False)
    return (True, "") if any(qty > 0 for qty in maximum.values()) else (False, "没有可开票的剩余数量，请打开原生应付核对")


def _current_drafts(source, target=None, return_documents=False):
    target = target or ("Purchase Receipt" if source.doctype == "Purchase Order" else "Purchase Invoice")
    link = _link_fields(source.doctype, target)[0]
    service._require_fields(target, {"items"})
    service._require_fields(target + " Item", {link}, target)
    parents = frappe.db.get_values(target + " Item", {link: source.name, "docstatus": 0},
                                  ["parent"], as_dict=True, distinct=True, for_update=True)
    if source.doctype == "Purchase Receipt" and target == "Purchase Invoice":
        service._require_fields("Purchase Receipt Item", {"purchase_order"}, "Purchase Receipt")
        service._require_fields("Purchase Invoice Item", {"purchase_order"}, "Purchase Invoice")
        orders = sorted({row.get("purchase_order") for row in source.items if row.get("purchase_order")})
        if orders:
            parents += frappe.db.get_values("Purchase Invoice Item", {"purchase_order": ["in", orders], "docstatus": 0},
                                           ["parent"], as_dict=True, distinct=True, for_update=True)
    documents = []
    for name in sorted({row.parent for row in parents}):
        with service._quiet_link_errors():
            try:
                doc = _locked(target, name)
            except (frappe.DoesNotExistError, frappe.PermissionError):
                frappe.throw(service.LINK_WARNING)
        if doc.docstatus == 0:
            documents.append(doc)
    return documents if return_documents else bool(documents)


def _source(source_doctype, source_name):
    if isinstance(source_name, list):
        rows = [_source(source_doctype, name) for name in sorted(source_name)]
        return [row[0] for row in rows], {"draft_invoices": [draft for _, chain in rows for draft in chain["draft_invoices"]]}
    source = service._source(source_doctype, source_name)
    chain = service.get_purchase_chain(source_doctype, source_name, include_payments=False)
    if chain["incomplete_links"]:
        frappe.throw(service.LINK_WARNING)
    return source, chain


def _advanced(doc, source):
    sources = _sources(source)
    source_type = sources[0].doctype
    field, key = _link_fields(source_type, doc.doctype)
    _fields(doc.doctype + " Item", {field, key, "uom", "conversion_factor", "purchase_order", "po_detail", "purchase_receipt", "pr_detail"}, parenttype=doc.doctype)
    by_name = {row.name: row for source in sources for row in source.items}
    owners = {row.name: source.name for source in sources for row in source.items}
    return bool(doc.get("is_return") or doc.get("update_stock") or doc.get("is_subcontracted")
                or any(row.get(field) != owners.get(row.get(key)) for row in doc.items)
                or not doc.items or len({_key(row, doc.doctype, source) for row in doc.items}) != len(doc.items)
                or any(row.get(key) not in by_name or row.get("item_code") != by_name[row.get(key)].get("item_code") for row in doc.items)
                or any(row.get("uom") != by_name[row.get(key)].get("uom")
                       or service.amount(row.get("conversion_factor")) != service.amount(by_name[row.get(key)].get("conversion_factor")) for row in doc.items)
                or (source_type == "Purchase Order" and doc.doctype == "Purchase Invoice"
                    and any(row.get("purchase_receipt") or row.get("pr_detail") for row in doc.items))
                or (source_type == "Purchase Receipt" and doc.doctype == "Purchase Invoice"
                    and any(row.get("purchase_order") != by_name[row.get(key)].get("purchase_order")
                            or row.get("po_detail") != by_name[row.get(key)].get("purchase_order_item") for row in doc.items)))


def _projection(doc, maximum=None, source=None):
    fields = _fields(doc.doctype, HEADER_FIELDS | HEADERS[doc.doctype])
    identity_fields = {"pr_detail", "po_detail", "purchase_order_item"}
    item_fields = _fields(doc.doctype + " Item", ITEM_FIELDS | identity_fields, parenttype=doc.doctype)
    tax_fields = _fields("Purchase Taxes and Charges", TAX_FIELDS, parenttype=doc.doctype)
    out = {field: doc.get(field) for field in fields - {"items", "taxes"}}
    out.update(doctype=doc.doctype, name=None if doc.is_new() else doc.name,
               modified=doc.get("modified"), docstatus=doc.docstatus,
               source_modified=source.get("modified") if source and not isinstance(source, list) else None,
               items=[{**{field: row.get(field) for field in item_fields - identity_fields},
                       "key": _key(row, doc.doctype, source), "max_qty": (maximum or {}).get(_key(row, doc.doctype, source), row.qty)} for row in doc.items],
               taxes=[{field: row.get(field) for field in tax_fields} for row in doc.get("taxes", [])],
               allowed_actions=_workflow_actions(doc))
    advanced = bool(source and _advanced(doc, source))
    if source:
        source_type = _sources(source)[0].doctype
        linkfield = _link_fields(source_type, doc.doctype)[0]
        _fields(doc.doctype + " Item", {linkfield}, parenttype=doc.doctype)
        out["sources"] = [{"name": row.name, "modified": str(row.modified)} for row in _sources(source)]
        for projected, native in zip(out["items"], doc.items, strict=False):
            projected["source_name"] = native.get(linkfield)
    if advanced:
        out["allowed_actions"] = []
    can_edit = bool(not advanced and doc.docstatus == 0 and doc.has_permission("create" if doc.is_new() else "write"))
    return {"document": out, "source_modified": out["source_modified"], "editable_fields": _editable_fields(doc, HEADERS[doc.doctype]) if can_edit else [],
            "editable_item_fields": _editable_fields(doc.items[0], ITEM_EDITS[doc.doctype], doc.doctype) if can_edit and doc.items else [],
            "allowed_actions": out["allowed_actions"], "advanced_reason": ADVANCED if advanced else ""}


def _limit_native(doc, maximum, source):
    """Apply a stricter current quantity cap; let native totals/schedules follow it."""
    items = []
    changed = False
    for row in doc.items:
        qty = min(service.amount(row.qty), service.amount(maximum.get(_key(row, doc.doctype, source), 0)))
        if qty <= 0:
            changed = True
            continue
        if service.amount(row.qty) != qty:
            row.qty = float(qty)
            changed = True
        items.append(row)
    if not items:
        frappe.throw(_i18n("没有可开票或入库的剩余数量，请打开原生单据核对"))
    if changed:
        doc.set("items", items)
        doc.run_method("calculate_taxes_and_totals")
        if doc.doctype == "Purchase Invoice":
            doc.set_payment_schedule()


@frappe.whitelist()
def preview_document(source_doctype, source_name, target_doctype, target_name=None):
    if (source_doctype, target_doctype) not in TARGETS:
        frappe.throw(_i18n("不支持此来源和目标单据"))
    source, chain = _source(source_doctype, source_name)
    locked_orders = {}
    source = _locked_source(source_doctype, source_name, target_doctype, locked_orders)
    if target_name:
        doc = service._read(target_doctype, target_name, HEADER_FIELDS)
        if doc.company != source.company or doc.supplier != source.supplier:
            frappe.throw(_i18n("来源公司或供应商不一致"))
        linkfield = _link_fields(source_doctype, target_doctype)[0]
        _fields(target_doctype + " Item", {linkfield}, parenttype=target_doctype)
        if not any(row.get(linkfield) == source_name for row in doc.items):
            frappe.throw(_i18n("目标单据与来源无关联"))
        if doc.docstatus != 0 or _advanced(doc, source):
            return _projection(doc, source=source)
    else:
        if target_doctype == "Purchase Invoice" and chain["draft_invoices"]:
            frappe.throw(_i18n("已有应付草稿，请选择继续编辑"))
        doc = None
    maximum = _current_maximum(source, target_doctype, locked_orders=locked_orders)
    native = _native(source, target_doctype)
    if _advanced(native, source):
        frappe.throw(ADVANCED)
    _limit_native(native, maximum, source)
    maximum = {_key(row, target_doctype, source): min(service.amount(row.qty), maximum.get(_key(row, target_doctype, source), service.amount(0)))
               for row in native.items}
    return _projection(doc or native, maximum, source)


def _changes(value):
    value = frappe.parse_json(value) if isinstance(value, str) else value
    if not isinstance(value, dict):
        frappe.throw(_i18n("修改内容必须为对象"))
    return value


def _edit_document(doc, changes, maximum, source=None):
    allowed = HEADERS[doc.doctype] | {"items"}
    if set(changes) - allowed:
        frappe.throw(_i18n("包含不支持的字段，请在原生单据处理"))
    _fields(doc.doctype, set(changes), "write")
    for field in HEADERS[doc.doctype] & changes.keys():
        value = changes[field]
        doc.set(field, getdate(value) if field.endswith("date") and value else value)
    edits = changes.get("items", [])
    if not isinstance(edits, list):
        frappe.throw(_i18n("明细修改必须为列表"))
    by_key = {_key(row, doc.doctype, source): row for row in doc.items}
    seen = set()
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) - (ITEM_EDITS[doc.doctype] | {"key"}):
            frappe.throw(_i18n("包含不支持的明细字段"))
        key = edit.get("key")
        if key not in by_key or key in seen:
            frappe.throw(_i18n("来源明细无效或重复"))
        seen.add(key)
        _fields(doc.doctype + " Item", set(edit) - {"key"}, "write", doc.doctype)
        row = by_key[key]
        for field in ITEM_EDITS[doc.doctype] & edit.keys():
            row.set(field, float(service.amount(edit[field])) if field in ("qty", "rate") else edit[field])
        if doc.doctype == "Purchase Receipt" and "qty" in edit:
            _fields("Purchase Receipt Item", {"received_qty"}, "write", doc.doctype)
            # Native validate_accepted_rejected_qty owns accepted + rejected totals.
            row.received_qty = 0
    for row in doc.items:
        if service.amount(row.qty) <= 0 or service.amount(row.qty) > service.amount(maximum.get(_key(row, doc.doctype, source), 0)):
            frappe.throw(_i18n("数量超过最新剩余数量或不是正数，请刷新"))
        if service.amount(row.rate) < 0:
            frappe.throw(_i18n("单价不能为负数"))
        if row.get("warehouse"):
            warehouse = service._read("Warehouse", row.warehouse, {"company", "is_group"})
            if warehouse.company != doc.company or warehouse.is_group:
                frappe.throw(_i18n("仓库必须为同公司明细仓库"))
    doc.run_method("calculate_taxes_and_totals")
    if doc.doctype == "Purchase Invoice":
        doc.set_payment_schedule()


@frappe.whitelist(methods=["POST"])
def save_document_draft(source_doctype, source_name, target_doctype, changes, request_id, target_name=None, expected_modified=None, allow_another_draft=0, expected_source_modified=None):
    changes = _changes(changes)
    if not re.fullmatch(r"[a-zA-Z0-9-]{16,80}", str(request_id or "")):
        frappe.throw(_i18n("缺少有效请求标识"))
    if (source_doctype, target_doctype) not in TARGETS:
        frappe.throw(_i18n("不支持此来源和目标单据"))
    allow_another_draft = allow_another_draft in (True, 1, "1", "true")
    payload = [source_doctype, source_name, target_doctype, target_name, expected_modified, changes]
    if allow_another_draft:
        payload.append(True)
    # Preserve the exact digest of every existing call with no source CAS token.
    if expected_source_modified is not None:
        payload.append({"expected_source_modified": expected_source_modified})
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    def operation():
        return _save_document_draft(source_doctype, source_name, target_doctype, changes, target_name,
            expected_modified, allow_another_draft, expected_source_modified)

    return purchase_operation.run(request_id, payload, operation, _replay_native, digest=digest)


def _save_document_draft(source_doctype, source_name, target_doctype, changes, target_name=None,
                         expected_modified=None, allow_another_draft=False, expected_source_modified=None):
    locked_orders = {}
    source = _locked_source(source_doctype, source_name, target_doctype, locked_orders)
    _source(source_doctype, source_name)
    if expected_source_modified is not None or (source_doctype, target_doctype) == ("Purchase Order", "Purchase Invoice"):
        _version(source, expected_source_modified)
    if not target_name and not allow_another_draft and _current_drafts(source, target_doctype):
        frappe.throw(_i18n("已有入库或应付草稿，请选择继续编辑；新建另一张需明确确认"))
    doc = _locked(target_doctype, target_name) if target_name else None
    if target_name:
        _version(doc, expected_modified)
        doc.check_permission("write")
        if doc.company != source.company or doc.supplier != source.supplier or _advanced(doc, source):
            frappe.throw(ADVANCED)
    if doc and doc.docstatus != 0:
        frappe.throw(_i18n("只能保存草稿"))
    native = _native(source, target_doctype)
    maximum = _current_maximum(source, target_doctype, locked_orders=locked_orders)
    if _advanced(native, source):
        frappe.throw(ADVANCED)
    _limit_native(native, maximum, source)
    maximum = {_key(row, target_doctype, source): min(service.amount(row.qty), maximum.get(_key(row, target_doctype, source), service.amount(0)))
               for row in native.items}
    doc = doc or native
    doc.check_permission("write" if target_name else "create")
    _edit_document(doc, changes, maximum, source)
    doc.save() if target_name else doc.insert()
    if doc.docstatus != 0 or (target_doctype == "Purchase Invoice" and doc.update_stock):
        frappe.throw(_i18n("保存必须保持草稿状态"))
    result = _projection(doc, maximum, source)
    result["reused"] = False
    return result


def _payment(doc):
    fields = {"company", "posting_date", "party", "party_type", "payment_type", "paid_from", "paid_to",
              "paid_from_account_currency", "paid_to_account_currency", "paid_amount", "received_amount", "references",
              "reference_no", "reference_date", "remarks", "deductions", "unallocated_amount"}
    service._require_fields("Payment Entry", fields)
    service._require_fields("Payment Entry Reference", {"reference_doctype", "reference_name", "allocated_amount", "outstanding_amount", "payment_term"}, "Payment Entry")
    warnings = []
    references = service._procurement_references(doc, warnings)
    kinds = {row.reference_doctype for row in doc.references}
    simple_references = kinds == {"Purchase Invoice"}
    advanced = bool(warnings or doc.payment_type != "Pay" or doc.party_type != "Supplier" or doc.get("deductions")
                    or doc.paid_from_account_currency != doc.paid_to_account_currency or doc.unallocated_amount
                    or not doc.references or not simple_references)
    receive = doc.payment_type == "Receive"
    service._bank_account(doc.paid_to if receive else doc.paid_from, doc.company,
                          doc.paid_to_account_currency if receive else doc.paid_from_account_currency)
    out = {"doctype": doc.doctype, "name": doc.name, "docstatus": doc.docstatus, "modified": doc.modified,
           "company": doc.company, "supplier": doc.party, "posting_date": doc.posting_date,
           "reference_no": doc.reference_no, "remarks": doc.remarks, "payment_type": doc.payment_type,
           "amount": doc.received_amount if receive else doc.paid_amount,
           "bank_account": doc.paid_to if receive else doc.paid_from,
           "currency": doc.paid_to_account_currency if receive else doc.paid_from_account_currency,
           "references": references, "payment_kind": "advance" if kinds == {"Purchase Order"} else "invoice",
           "allowed_actions": [] if advanced else _workflow_actions(doc)}
    return {"document": out, "allowed_actions": out["allowed_actions"],
            "editable_fields": _payment_editable(doc) if not advanced and doc.docstatus == 0 else [],
            "advanced_reason": ADVANCED if advanced else ""}


def _payment_editable(doc):
    if not doc.has_permission("write"):
        return []
    permitted = _editable_fields(doc, {"posting_date", "reference_no", "remarks", "paid_amount", "received_amount", "references", "paid_from"})
    fields = [field for field in ("posting_date", "reference_no", "remarks") if field in permitted]
    if {"paid_amount", "received_amount", "references"} <= set(permitted) and "allocated_amount" in _editable_fields(doc.references[0], {"allocated_amount"}, doc.doctype):
        fields.append("amount")
    if "paid_from" in permitted:
        fields.append("bank_account")
    return fields


@frappe.whitelist()
def preview_payment(name):
    return _payment(service._read("Payment Entry", name))


def _payment_balance(doc, validate_allocations=True):
    balances = {}
    for row in doc.references:
        if row.reference_doctype == "Purchase Order":
            frappe.throw(_i18n("采购订单预付款请在原生财务付款单处理；采购付款记录可继续查看"))
        if row.reference_doctype != "Purchase Invoice":
            frappe.throw(ADVANCED)
        invoice = _locked("Purchase Invoice", row.reference_name)
        service._require_fields("Purchase Invoice", service.PI_FIELDS)
        if invoice.company != doc.company or invoice.supplier != doc.party or not service.invoice_payment_eligible(invoice):
            frappe.throw(_i18n("应付来源状态、公司或供应商已改变"))
        for source_type, linkfield in (("Purchase Order", "purchase_order"), ("Purchase Receipt", "purchase_receipt")):
            service._require_fields("Purchase Invoice Item", {linkfield}, "Purchase Invoice")
            warnings = []
            service._source_links(invoice, source_type, linkfield, warnings)
            if warnings:
                frappe.throw(service.LINK_WARNING)
        balance = service.invoice_balance(invoice)
        if balance["currency"] != doc.paid_from_account_currency:
            frappe.throw(_i18n("跨币种付款请在原生单据处理"))
        balances[invoice.name] = balance
    allocated = {}
    for row in doc.references:
        allocated[row.reference_name] = allocated.get(row.reference_name, service.amount(0)) + service.amount(row.allocated_amount)
    if validate_allocations and any(value < 0 or value > service.amount(balances[name]["outstanding"]) for name, value in allocated.items()):
        frappe.throw(_i18n("核销超过最新原生余额，请刷新"))
    return balances


@frappe.whitelist(methods=["POST"])
@procurement_entry
def update_payment_draft(name, changes, expected_modified):
    changes = _changes(changes)
    allowed = {"posting_date", "reference_no", "remarks", "amount", "bank_account"}
    if set(changes) - allowed:
        frappe.throw(_i18n("包含不支持的付款字段"))
    doc = _locked("Payment Entry", name)
    _version(doc, expected_modified)
    doc.check_permission("write")
    if doc.docstatus != 0 or _payment(doc)["advanced_reason"]:
        frappe.throw(ADVANCED)
    _fields("Payment Entry", (set(changes) - {"amount", "bank_account"}) |
            ({"paid_amount", "received_amount", "references"} if "amount" in changes else set()) |
            ({"paid_from"} if "bank_account" in changes else set()), "write")
    balances = _payment_balance(doc, validate_allocations="amount" not in changes)
    account = service._bank_account(changes.get("bank_account") or doc.paid_from, doc.company, doc.paid_to_account_currency, for_update=True)
    if "amount" in changes:
        value = service.amount(changes["amount"])
        if value <= 0 or value > sum((service.amount(row["outstanding"]) for row in balances.values()), service.amount(0)):
            frappe.throw(_i18n("付款金额超过最新原生余额或不是正数"))
        # Reuse native term schedules; do not invent or remove reference rows.
        from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
        plans = {}
        for invoice, balance in balances.items():
            native = get_payment_entry(balance.get("reference_doctype", "Purchase Invoice"), invoice, bank_account=account.name)
            for row in native.references:
                plans[(row.reference_name, row.payment_term or "")] = service.amount(row.outstanding_amount)
        remaining = value
        service._require_fields("Payment Entry Reference", {"payment_term", "outstanding_amount"}, "Payment Entry")
        _fields("Payment Entry Reference", {"allocated_amount"}, "write", "Payment Entry")
        for row in doc.references:
            maximum = plans.get((row.reference_name, row.payment_term or ""), service.amount(0))
            allocated = min(remaining, max(service.amount(0), maximum))
            row.allocated_amount = float(allocated)
            remaining -= allocated
        if remaining:
            frappe.throw(_i18n("现有原生付款计划无法完整分配，请打开原生单据"))
        doc.paid_amount = doc.received_amount = float(value)
    doc.paid_from = account.name
    for field in ("posting_date", "reference_no", "remarks"):
        if field in changes:
            doc.set(field, getdate(changes[field]) if field == "posting_date" else changes[field])
    if "remarks" in changes:
        doc.custom_remarks = 1
    _payment_balance(doc)
    doc.save()
    return _payment(doc)


def _native_request(request_id, payload, operation):
    return purchase_operation.run(request_id, payload, operation, _replay_native, acknowledge_validation=True)


def _replay_native(previous):
    if previous.get("documents"):
        # Sources remain ahead of every target even when recovering several
        # acknowledged documents from one durable receipt.
        links = {}
        for row in previous["documents"]:
            if row.get("doctype") != "Payment Entry":
                doc = service._read(row["doctype"], row["name"], HEADER_FIELDS)
                source_type, names = _document_source_link(doc)
                links.setdefault((source_type, doc.doctype), set()).update(names if isinstance(names, list) else [names])
        for (source_type, target), names in sorted(links.items(), key=lambda pair: pair[0][0] != "Purchase Order"):
            _locked_source(source_type, sorted(names), target)
        purchase_operation.replay_artifacts(previous)
        return {"documents": [_replay_native(row) for row in previous["documents"]], "reused": True}
    purchase_operation.replay_artifacts(previous)
    doc = _locked(previous.get("doctype", "Payment Entry"), previous["name"])
    doc.check_permission(previous.get("permission", "read"))
    if doc.doctype == "Payment Entry":
        result = _payment(doc)
    else:
        source, _ = _source(*_document_source_link(doc))
        for original in _sources(source):
            _mapping_fields(original, doc.doctype)
        if any(original.company != doc.company or original.supplier != doc.supplier for original in _sources(source)) or _advanced(doc, source):
            frappe.throw(ADVANCED)
        result = _projection(doc, source=source)
    result.update(reused=True, needs_review=previous.get("needs_review", False))
    return result


def _batch_sources(sources):
    sources = frappe.parse_json(sources) if isinstance(sources, str) else sources
    if not isinstance(sources, list) or not sources or len(sources) > 100:
        frappe.throw(_i18n("请选择当前页的采购单据（最多100张）"))
    if any(not isinstance(row, dict) or not row.get("name") or set(row) - {"name", "modified"} for row in sources):
        frappe.throw(_i18n("来源单据无效"))
    if len({row["name"] for row in sources}) != len(sources):
        frappe.throw(_i18n("来源单据重复"))
    return sorted(sources, key=lambda row: row["name"])


def _merge_compatible(sources):
    fields = {"company", "supplier", "currency", "conversion_rate", "buying_price_list", "price_list_currency",
        "plc_conversion_rate", "taxes_and_charges", "apply_discount_on", "additional_discount_percentage", "discount_amount",
        "payment_terms_template", "supplier_address", "shipping_address", "billing_address", "incoterm", "named_place",
        "is_subcontracted", "is_internal_supplier", "represents_company", "tax_category", "shipping_rule",
        "disable_rounded_total", "ignore_pricing_rule", "letter_head", "tc_name", "terms", "contact_person", "contact_email",
        "contact_mobile", "supplier_warehouse", "set_warehouse", "project"}
    tax_fields = TAX_FIELDS | {"category", "add_deduct_tax", "included_in_print_rate", "included_in_paid_amount", "row_id", "cost_center", "account_currency"}
    facts = []
    for source in sources:
        readable = _fields(source.doctype, fields)
        tax_readable = _fields("Purchase Taxes and Charges", tax_fields, parenttype=source.doctype)
        if source.get("discount_amount") or source.get("additional_discount_percentage") or source.get("payment_terms_template") or any(row.charge_type == "Actual" for row in source.get("taxes", [])):
            frappe.throw(_i18n("固定税费、整单折扣或付款计划需在原生入库单核对；可选择逐单入库"))
        facts.append(({field: source.get(field) for field in readable},
            [{field: row.get(field) for field in tax_readable - {"tax_amount", "total"}} for row in source.get("taxes", [])]))
    if any(fact != facts[0] for fact in facts[1:]):
        frappe.throw(_i18n("合并入库要求公司、供应商、币种及原生税费/表头设置一致；请逐单或在原生单据处理"))


def _batch_context(sources, source_doctype, target_doctype, merge, check_versions=False):
    if (source_doctype, target_doctype) not in TARGETS or (merge and target_doctype != "Purchase Receipt"):
        frappe.throw(_i18n("此批量转换仅支持订单入库及入库确认应付"))
    locked_orders = {}
    originals = _locked_source(source_doctype, [row["name"] for row in sources], target_doctype, locked_orders)
    for original, selected in zip(originals, sources, strict=False):
        _source(source_doctype, original.name)
        if check_versions:
            _version(original, selected.get("modified"))
    if merge:
        _merge_compatible(originals)
    return originals, locked_orders


def _batch_groups(originals, target_doctype, merge):
    selected = {doc.name: doc for doc in originals}
    groups, seen = [], set()
    for original in originals:
        drafts = _current_drafts(original, target_doctype, return_documents=True)
        if len(drafts) > 1:
            frappe.throw(_i18n("所选来源有多张草稿，请从原生单据明确选择；整批未修改"))
        draft = drafts[0] if drafts else None
        if draft and draft.name in seen:
            continue
        if draft:
            source_type, names = _document_source_link(draft)
            names = names if isinstance(names, list) else [names]
            if source_type != original.doctype or not set(names) <= set(selected):
                frappe.throw(_i18n("已有草稿还包含未选择的来源，请打开原生草稿核对；整批未修改"))
            group = [selected[name] for name in sorted(names)]
            seen.add(draft.name)
        else:
            group = [original]
        groups.append((group, draft))
    if merge:
        if any(draft for _, draft in groups):
            if len(groups) != 1 or len(groups[0][0]) != len(originals):
                frappe.throw(_i18n("已有独立入库草稿包含人工值，请选择逐单继续；合并需在原生单据处理，整批未修改"))
        else:
            groups = [(originals, None)]
    return groups


def _batch_native(originals, target_doctype, locked_orders):
    native = None
    for original in originals:
        native = _native(original, target_doctype, native)
    maximum = _current_maximum(originals, target_doctype, locked_orders=locked_orders)
    if _advanced(native, originals):
        frappe.throw(ADVANCED)
    _limit_native(native, maximum, originals)
    return native, maximum


@frappe.whitelist()
@procurement_entry
def preview_document_batch(sources, source_doctype="Purchase Order", target_doctype="Purchase Receipt", merge=0):
    sources = _batch_sources(sources)
    merge = merge in (True, 1, "1", "true")
    originals, locked_orders = _batch_context(sources, source_doctype, target_doctype, merge)
    documents = []
    for group, draft in _batch_groups(originals, target_doctype, merge):
        if draft:
            if _advanced(draft, group):
                frappe.throw(ADVANCED)
            maximum = _current_maximum(group, target_doctype, locked_orders=locked_orders)
            documents.append(_projection(draft, maximum, group))
        else:
            native, maximum = _batch_native(group, target_doctype, locked_orders)
            documents.append(_projection(native, maximum, group))
    return {"documents": documents, "sources": [{"name": row.name, "modified": str(row.modified)} for row in originals], "merge": merge}


@frappe.whitelist(methods=["POST"])
@procurement_entry
def record_document_batch(sources, changes, request_id, source_doctype="Purchase Order", target_doctype="Purchase Receipt", merge=0, confirm=0, workflow_action=None, documents=None):
    sources = _batch_sources(sources)
    changes = frappe.parse_json(changes) if isinstance(changes, str) else changes
    merge, confirm = (value in (True, 1, "1", "true") for value in (merge, confirm))
    if not isinstance(changes, list) or not changes:
        frappe.throw(_i18n("请核对每张目标单据的明细修改"))
    changes = [_changes(row) for row in changes]
    documents = frappe.parse_json(documents) if isinstance(documents, str) else documents
    if documents is not None and (not isinstance(documents, list) or len(documents) != len(changes)):
        frappe.throw(_i18n("草稿确认范围已改变，请刷新"))
    def operation():
        originals, locked_orders = _batch_context(sources, source_doctype, target_doctype, merge, check_versions=True)
        groups = _batch_groups(originals, target_doctype, merge)
        if len(groups) != len(changes):
            frappe.throw(_i18n("草稿或来源范围已改变，请刷新"))
        results = []
        for index, ((group, draft), edits) in enumerate(zip(groups, changes, strict=False)):
            expected = documents[index] if documents is not None else {}
            if not isinstance(expected, dict) or expected.get("name") != (draft.name if draft else None):
                frappe.throw(_i18n("所选来源已有入库或应付草稿，请先预览并明确继续已有草稿；整批未修改"))
            if draft:
                _version(draft, expected.get("modified"))
                if _advanced(draft, group):
                    frappe.throw(ADVANCED)
                native, maximum = draft, _current_maximum(group, target_doctype, locked_orders=locked_orders)
                native.check_permission("write")
            else:
                native, maximum = _batch_native(group, target_doctype, locked_orders)
                native.check_permission("create")
            _edit_document(native, edits, maximum, group)
            native.save() if draft else native.insert()
            if native.docstatus != 0:
                frappe.throw(_i18n("原生保存必须保持草稿状态"))
            if confirm:
                from frappe.model.workflow import get_workflow_name
                if workflow_action or (not get_workflow_name(target_doctype) and "Submit" in _workflow_actions(native)):
                    results.append(_submit_document(target_doctype, native.name, native.modified, workflow_action))
                    continue
            results.append(_projection(native, maximum, group))
        return {"documents": results, "reused": False}
    return _native_request(request_id, [sources, changes, source_doctype, target_doctype, merge, confirm, workflow_action, documents], operation)


def _payment_request(request_id, payload, operation):
    return _native_request(request_id, payload, operation)


@frappe.whitelist(methods=["POST"])
def record_receipt(source_name, changes, request_id, target_name=None, expected_modified=None,
                   confirm=1, workflow_action=None, allow_another_draft=0):
    """One explicit PO receipt operation, reusing draft mapping and native submission."""
    confirm = confirm in (True, 1, "1", "true")
    args = dict(source_doctype="Purchase Order", source_name=source_name, target_doctype="Purchase Receipt",
                changes=_changes(changes), request_id=request_id, target_name=target_name,
                expected_modified=expected_modified, allow_another_draft=allow_another_draft)

    def operation():
        result = save_document_draft(**args)
        doc = _locked("Purchase Receipt", result["document"]["name"])
        from frappe.model.workflow import get_workflow_name
        if not confirm or (not workflow_action and (get_workflow_name(doc.doctype) or "Submit" not in _workflow_actions(doc))):
            return _projection(doc)
        return _submit_document(doc.doctype, doc.name, doc.modified, workflow_action)

    return _native_request(request_id, [args, confirm, workflow_action], operation)


def _confirm_payment(doc, workflow_action=None):
    from frappe.model.workflow import get_workflow_name
    # A configured workflow must be shown in the drawer, never guessed as Submit.
    if not workflow_action and (get_workflow_name("Payment Entry") or "Submit" not in _workflow_actions(doc)):
        return _payment(doc)
    return submit_document("Payment Entry", doc.name, doc.modified, workflow_action)


@frappe.whitelist(methods=["POST"])
def complete_payment(name, changes, expected_modified, request_id, workflow_action=None, attachment_session=None, attachments=None):
    changes = _changes(changes)

    def operation():
        doc = _locked("Payment Entry", name)
        _version(doc, expected_modified)
        if changes:
            update_payment_draft(name, changes, expected_modified)
            doc = _locked("Payment Entry", name)
        from deeplinkerp_branding.services.purchase_payment_attachments import bind
        bind(doc, attachment_session, attachments)
        return _confirm_payment(doc, workflow_action)

    payload = [name, changes, expected_modified, workflow_action]
    if attachments:
        payload += [attachment_session, attachments]
    return _payment_request(request_id, payload, operation)


@frappe.whitelist(methods=["POST"])
def record_payment(source_doctype, source_name, purchase_invoice=None, amount_to_pay=None, bank_account=None,
                   request_id=None, posting_date=None, remarks=None, reference_no=None, confirm=1, attachment_session=None, attachments=None, sources=None, allocations=None):
    """Save/submit the existing native PE flow; a discovered draft requires review first."""
    args = dict(source_doctype=source_doctype, source_name=source_name, purchase_invoice=purchase_invoice,
                amount_to_pay=amount_to_pay, bank_account=bank_account, posting_date=posting_date,
                remarks=remarks, reference_no=reference_no, request_id=request_id)
    if sources is not None:
        args.update(sources=_batch_sources(sources), allocations=service._payment_allocations(allocations))
    confirm = confirm in (True, 1, "1", "true")

    def operation():
        if not frappe.has_permission("Payment Entry", "create"):
            frappe.throw(_i18n("没有创建付款单权限"), frappe.PermissionError)
        if sources is None:
            source, target, _ = service.payment_target(source_doctype, source_name, purchase_invoice)
            target_type, target_names = target.doctype, [target.name]
        else:
            context = service._payment_batch_targets(source_doctype, args["sources"], check_versions=True)
            source = context["sources"][0]
            available = {doc.name for doc, balance in context["targets"]}
            target_names = [row["name"] for row in args["allocations"]]
            if not set(target_names) <= available:
                frappe.throw(_i18n("应付单不属于所选真实采购链或当前不可付款"))
            target_type = "Purchase Invoice"
        # Current DB lock serializes different request IDs and overlapping PO/PR sources.
        # Permission-checked discovery does not select or mutate an unseen business draft.
        candidates = frappe.get_list("Payment Entry", filters=[
            ["Payment Entry", "docstatus", "=", 0],
            ["Payment Entry", "payment_type", "=", "Pay"],
            ["Payment Entry", "company", "=", source.company],
            ["Payment Entry", "party_type", "=", "Supplier"],
            ["Payment Entry", "party", "=", source.supplier],
            ["Payment Entry Reference", "reference_doctype", "=", target_type],
            ["Payment Entry Reference", "reference_name", "in", target_names]],
            fields=["name"], order_by="modified desc", limit_page_length=0)
        if candidates:
            result = _payment(service._read("Payment Entry", candidates[0].name))
            result.update(needs_review=True, reused=True)
            return result
        saved = service.create_payment_draft(**args)
        doc = _locked("Payment Entry", saved["name"])
        from deeplinkerp_branding.services.purchase_payment_attachments import bind
        bind(doc, attachment_session, attachments)
        return _confirm_payment(doc) if confirm else _payment(doc)

    payload = [args, confirm]
    if attachments:
        payload += [attachment_session, attachments]
    return _payment_request(request_id, payload, operation)


@frappe.whitelist(methods=["POST"])
@procurement_entry
def submit_document(doctype, name, expected_modified, workflow_action=None, request_id=None):
    if request_id or doctype == "Purchase Receipt":
        return _native_request(request_id, [doctype, name, expected_modified, workflow_action],
                               lambda: _submit_document(doctype, name, expected_modified, workflow_action))
    return _submit_document(doctype, name, expected_modified, workflow_action)


def _submit_document(doctype, name, expected_modified, workflow_action=None):
    if doctype not in ("Purchase Receipt", "Purchase Invoice", "Payment Entry"):
        frappe.throw(_i18n("此抽屉仅支持采购入库、应付和付款明确提交"))
    source = None
    locked_orders = {}
    if doctype == "Payment Entry":
        doc = _locked(doctype, name)
    else:
        # Read and authorize first, then lock the source before the target to
        # agree with draft creation and every PO / later-PR invoice writer.
        candidate = service._read(doctype, name, HEADER_FIELDS)
        source_type, source_name = _document_source_link(candidate)
        source = _locked_source(source_type, source_name, doctype, locked_orders)
        doc = _locked(doctype, name)
    _version(doc, expected_modified)
    if doc.docstatus != 0:
        frappe.throw(_i18n("只能提交草稿"))
    if doctype == "Payment Entry":
        service._bank_account(doc.paid_from, doc.company, doc.paid_from_account_currency, for_update=True)
        if _payment(doc)["advanced_reason"]:
            frappe.throw(ADVANCED)
        _payment_balance(doc)
        if service.amount(doc.paid_amount) <= 0 or service.amount(doc.paid_amount) != sum((service.amount(row.allocated_amount) for row in doc.references), service.amount(0)):
            frappe.throw(_i18n("付款金额必须完整分配到原生引用"))
    else:
        for original in _sources(source):
            _source(original.doctype, original.name)
            _mapping_fields(original, doctype)
        if any(original.company != doc.company or original.supplier != doc.supplier for original in _sources(source)) or _advanced(doc, source):
            frappe.throw(ADVANCED)
        for original in _sources(source):
            reason = _invoice_source_reason(original, check_fields=False) if doctype == "Purchase Invoice" else service.order_execution_reason(original)
            if reason:
                frappe.throw(reason)
        if len(_sources(source)) > 1 and doctype == "Purchase Receipt":
            _merge_compatible(_sources(source))
        maximum = _current_maximum(source, doctype, locked_orders=locked_orders)
        totals = {}
        for row in doc.items:
            if service.amount(row.rate) < 0:
                frappe.throw(_i18n("单价不能为负数"))
            key = _key(row, doctype, source)
            totals[key] = totals.get(key, service.amount(0)) + service.amount(row.qty)
        if any(qty <= 0 or qty > maximum.get(key, service.amount(0)) for key, qty in totals.items()):
            frappe.throw(_i18n("数量超过最新剩余数量或不是正数，请刷新"))
    from frappe.model.workflow import apply_workflow, get_workflow_name
    actions = _workflow_actions(doc)
    action = workflow_action or "Submit"
    if action not in actions:
        frappe.throw(_i18n("没有当前提交或工作流操作权限"), frappe.PermissionError)
    if get_workflow_name(doctype):
        doc = apply_workflow(doc.as_dict(), action)
    else:
        doc.check_permission("submit")
        doc.submit()
    if doctype == "Purchase Invoice" and doc.update_stock:
        frappe.throw(ADVANCED)
    return _payment(doc) if doctype == "Payment Entry" else _projection(doc, source=source)


@frappe.whitelist()
def get_voucher_detail(name):
    fields = {"company", "posting_date", "status", "source_doctype", "source_name", "source_event", "statutory_number",
              "reversal_of", "reversed_by", "remarks", "currency", "total_debit", "total_credit", "entries"}
    doc = service._read("China Accounting Voucher", name, fields)
    row_fields = {"account", "account_name", "debit", "credit", "remarks"}
    row_fields = _fields("China Accounting Voucher Entry", row_fields, parenttype=doc.doctype)
    out = {field: doc.get(field) for field in fields - {"entries", "source_name", "reversal_of", "reversed_by"}}
    out.update(name=doc.name, docstatus=doc.docstatus, entries=[{field: row.get(field) for field in row_fields} for row in doc.entries])
    warnings = []
    for field in ("reversal_of", "reversed_by"):
        target = doc.get(field)
        if target and service._related(doc.doctype, target, warnings, {"company"}, doc.company):
            out[field] = target
    if doc.source_name and service._related(doc.source_doctype, doc.source_name, warnings, {"company"}, doc.company):
        out["source_name"] = doc.source_name
    out["warnings"] = warnings
    return out
