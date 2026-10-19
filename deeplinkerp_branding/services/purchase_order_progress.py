"""Three source-authorized progress projections; no accounting documents returned.

Public input: <=100 requested POs; the private list/export core accepts the
already ACL-scoped filtered batch without dividing it into100-row rescans.
Related native child edges, fulfilment links and
cached comment JSON. The former per-PO PI/PE scans were O(N * E). Discovery and
indexes now visit each related PI item/reference once: O(N + E + C + J), with
O(N + E + C + J) related space. C is parsed text and J actual JSON bytes, not a
constant. All native parent hydration uses the existing request-local reader.
"""
from __future__ import annotations

from frappe import _ as _i18n


import json
from contextlib import contextmanager
from decimal import Decimal

import frappe
from frappe.model import get_permitted_fields

from .purchase_payment_service import _read, _record_reader, _RecordReader, amount, invoice_balance, _quiet_link_errors, _require_fields, PI_FIELDS
from . import purchase_fulfilment_service as fulfilment

ITEM_FIELDS = {"name", "idx", "item_code", "item_name", "description", "warehouse", "qty", "uom", "rate", "amount", "received_qty"}
NOTICE = "仅显示有权查看的订单进度；不包含银行账户、应付单或付款单明细。订单未付不是已到货应付余额。"
UNCERTAIN = "共享应付、退货或跨币种无法安全归入本订单，请由财务核对；不将整单金额冒充采购成本。"


class _FieldScope(set):
    """The existing reader's field-check set, backed by one native scope read.

    _require_fields still checks every requested set. This set-compatible
    cache also reuses failed checks and overlapping sets within this request;
    it preserves native table permlevels and never grants denied fields.
    """
    def __init__(self, getter):
        super().__init__(); self.getter = getter; self.allowed = {}; self.raw = {}

    def permitted(self, doctype, parenttype=None):
        key = (doctype, parenttype)
        if key not in self.allowed:
            fields = set(self.getter(doctype, parenttype=parenttype, permission_type="read", ignore_virtual=True))
            self.raw[key] = fields
            meta = frappe.get_meta(doctype)
            levels = set(meta.get_permlevel_access(permission_type="read", parenttype=parenttype))
            self.allowed[key] = fields | {df.fieldname for df in meta.fields
                if df.fieldtype in ("Table", "Table MultiSelect") and df.permlevel in levels}
        return self.raw[key]

    def __contains__(self, key):
        if super().__contains__(key):
            return True
        doctype, fields, parenttype = key
        self.permitted(doctype, parenttype)
        if not fields <= self.allowed[doctype, parenttype]:
            frappe.throw(_i18n("无权查看完整关联或金额字段"), frappe.PermissionError)
        self.add(key)
        return True


class _ProgressReader(_RecordReader):
    def __init__(self, getter=None):
        super().__init__()
        self.fields = _FieldScope(getter or get_permitted_fields)


@contextmanager
def _progress_read_scope(getter=None):
    if _record_reader.get() is not None:
        yield _record_reader.get()
        return
    reader = _ProgressReader(getter); token = _record_reader.set(reader)
    try:
        yield reader
    finally:
        _record_reader.reset(token)


def _sources(items, receipt_orders, shared_receipts=None):
    result = set()
    for item in items:
        if item.get("purchase_order"):
            result.add(item.get("purchase_order"))
        else:
            receipt = item.get("purchase_receipt")
            sources = receipt_orders.get(receipt, {None})
            if shared_receipts is not None and len(sources) > 1:
                # Mark a shared receipt once globally rather than expand all
                # its PO edges repeatedly for every invoice referencing it.
                shared_receipts.add(receipt)
                result.add(None)
            else:
                result.update(sources)
    return result


def _empty():
    return {"settled": Decimal(0), "payable_total": Decimal(0), "payable_outstanding": Decimal(0),
            "has_payable": False, "exact": True}


def _add_invoice(invoice, sources, orders, totals):
    relevant = sources & orders.keys()
    if not relevant or invoice.docstatus == 0:
        return
    if len(sources) != 1 or invoice.docstatus != 1 or invoice.get("is_return"):
        for name in relevant:
            totals[name]["exact"] = False
        return
    name = next(iter(relevant))
    order = orders[name]
    entry = totals[name]
    if (invoice.company != order.company or invoice.supplier != order.supplier or
            (invoice.party_account_currency or invoice.currency) != order.currency):
        entry["exact"] = False
        return
    try:
        balance = invoice_balance(invoice)
    except (ValueError, frappe.ValidationError):
        entry["exact"] = False
        return
    entry["has_payable"] = True
    entry["settled"] += amount(balance["settled"])
    entry["payable_total"] += amount(balance["total"])
    entry["payable_outstanding"] += amount(balance["outstanding"])


def _settlement_result(order, entry):
    exact = entry["exact"] and order.docstatus == 1
    return {"settled": float(entry["settled"]) if exact else None,
            "order_unpaid": float(amount(order.grand_total) - entry["settled"]) if exact else None,
            "settlement_exact": exact, "settlement_notice": "" if exact else UNCERTAIN}


def settlement(order, invoices, receipt_orders, advances):
    """Compatible pure wrapper; the list path instead uses one bulk index."""
    entry = _empty()
    for row in advances:
        if row["currency"] != order.currency:
            entry["exact"] = False
        else:
            entry["settled"] += amount(row["amount"])
    for invoice in invoices:
        _add_invoice(invoice, _sources(invoice.items, receipt_orders), {order.name: order}, {order.name: entry})
    return _settlement_result(order, entry)


def _native_indexes(orders, reader, *, internal_orders=()):
    names = list(orders)
    totals = {name: _empty() for name in names}
    internal_orders = set(internal_orders)
    external_orders = {name: order for name, order in orders.items() if name not in internal_orders}
    accounting_permission = {}
    accounting_fields = {}

    def authorized_accounting(doctype, name):
        # Only the new internal balance requires accounting-record access. Keep
        # the established fixed external PO-authorized summary compatible.
        key = (doctype, name)
        if key not in accounting_permission:
            with _quiet_link_errors():
                try:
                    if doctype not in accounting_fields:
                        try:
                            _require_fields(doctype, PI_FIELDS if doctype == "Purchase Invoice" else
                                {"company", "party", "party_type", "payment_type", "paid_to_account_currency", "references"})
                            _require_fields("Purchase Invoice Item" if doctype == "Purchase Invoice" else "Payment Entry Reference",
                                {"purchase_order", "purchase_receipt"} if doctype == "Purchase Invoice" else
                                {"reference_doctype", "reference_name", "allocated_amount"}, doctype)
                            accounting_fields[doctype] = True
                        except frappe.PermissionError:
                            accounting_fields[doctype] = False
                    if not accounting_fields[doctype]:
                        accounting_permission[key] = False
                        return False
                    if doctype == "Purchase Invoice":
                        doc = _read(doctype, name, PI_FIELDS)
                    else:
                        doc = _read(doctype, name, {"company", "party", "party_type", "payment_type",
                            "paid_to_account_currency", "references"})
                    fulfilment._company(doc.company)
                    accounting_permission[key] = True
                except (frappe.DoesNotExistError, frappe.PermissionError):
                    accounting_permission[key] = False
        return accounting_permission[key]

    def restricted(sources):
        for source in sources:
            totals[source].update(exact=False, restricted=True)
    receipt_items = frappe.get_all("Purchase Receipt Item", filters={"purchase_order": ["in", names]},
        fields=["parent", "purchase_order"], limit_page_length=0)
    receipt_names = {row.parent for row in receipt_items}
    all_receipt_items = frappe.get_all("Purchase Receipt Item", filters={"parent": ["in", list(receipt_names)]},
        fields=["parent", "purchase_order"], limit_page_length=0) if receipt_names else []
    receipt_orders = {}
    for item in all_receipt_items:
        receipt_orders.setdefault(item.parent, set()).add(item.purchase_order or None)
    invoice_items = frappe.get_all("Purchase Invoice Item", or_filters=[
        ["purchase_order", "in", names], ["purchase_receipt", "in", list(receipt_names)]],
        fields=["parent", "purchase_order", "purchase_receipt"], limit_page_length=0)
    invoice_names = {row.parent for row in invoice_items}
    reader.preload("Purchase Invoice", invoice_names)
    missing_sources = {}
    shared_receipts = set()
    for item in invoice_items:
        if not reader.docs.get(("Purchase Invoice", item.parent)):
            missing_sources.setdefault(item.parent, set()).update(_sources([item], receipt_orders, shared_receipts))
    for sources in missing_sources.values():
        for name in sources & orders.keys():
            totals[name]["exact"] = False
    for name in invoice_names:
        invoice = reader.docs.get(("Purchase Invoice", name))
        if invoice:
            sources = _sources(invoice.items, receipt_orders, shared_receipts)
            protected = sources & internal_orders
            if protected and not authorized_accounting("Purchase Invoice", name):
                restricted(protected)
                # No private fields are used to calculate denied internal AP.
                _add_invoice(invoice, sources, external_orders, totals)
            else:
                _add_invoice(invoice, sources, orders, totals)
    for receipt in shared_receipts:
        for name in receipt_orders[receipt] & orders.keys():
            totals[name]["exact"] = False
    references = frappe.get_all("Payment Entry Reference", filters={"reference_doctype": "Purchase Order",
        "reference_name": ["in", names]}, fields=["parent", "reference_name", "allocated_amount"], limit_page_length=0)
    reader.preload("Payment Entry", {row.parent for row in references})
    for ref in references:
        order = orders[ref.reference_name]
        pe = reader.docs.get(("Payment Entry", ref.parent))
        entry = totals[order.name]
        if order.name in internal_orders and not authorized_accounting("Payment Entry", ref.parent):
            restricted({order.name})
            continue
        if not pe or pe.docstatus == 2:
            entry["exact"] = False
        elif pe.docstatus == 1:
            if (pe.payment_type != "Pay" or pe.party_type != "Supplier" or pe.company != order.company
                    or pe.party != order.supplier or pe.paid_to_account_currency != order.currency):
                entry["exact"] = False
            else:
                entry["settled"] += amount(ref.allocated_amount)
    reader.preload("Purchase Receipt", receipt_names)
    receipts = {name: {"state": "none", "quantities": [], "warnings": [], "_by_item": {}} for name in names}
    receipt_fields = True
    if receipt_names:
        with _quiet_link_errors():
            try:
                _require_fields("Purchase Receipt", {"company", "supplier", "items", "is_return"})
                _require_fields("Purchase Receipt Item", {"purchase_order", "purchase_order_item", "qty", "uom", "stock_uom", "stock_qty"}, "Purchase Receipt")
            except frappe.PermissionError:
                receipt_fields = False
    for name in receipt_names:
        with _quiet_link_errors():
            try:
                if not receipt_fields:
                    raise frappe.PermissionError
                receipt = _read("Purchase Receipt", name, {"company", "supplier", "items", "is_return"})
                fulfilment._company(receipt.company)
            except (frappe.DoesNotExistError, frappe.PermissionError):
                for source in receipt_orders.get(name, set()) & orders.keys():
                    receipts[source] = {"state": "restricted", "quantities": [], "warnings": [fulfilment.LINK_WARNING]}
                continue
        if receipt.docstatus == 0:
            continue
        for item in receipt.items:
            source = item.get("purchase_order")
            if source not in orders:
                continue
            order = orders[source]
            entry = receipts[source]
            if (receipt.docstatus != 1 or receipt.is_return or receipt.company != order.company or receipt.supplier != order.supplier):
                entry.update(state="review", quantities=[], warnings=["原生入库存在取消、退货或公司/供应商不一致，请核对"])
            elif entry["state"] not in ("restricted", "review"):
                entry["state"] = "native_received"
                quantity = {"qty": item.qty, "uom": item.uom, "stock_qty": item.get("stock_qty"),
                            "stock_uom": item.get("stock_uom"), "native_item": item.get("purchase_order_item")}
                entry["quantities"].append(quantity)
                entry["_by_item"].setdefault(quantity["native_item"], []).append(quantity)
    # ERPNext also updates native PO receipt counters from stock-updating PI.
    # No accessible PR proof means unknown quantity, never an invented zero.
    item_fields = reader.fields.permitted("Purchase Order Item", "Purchase Order") if isinstance(reader.fields, _FieldScope) else set(
        get_permitted_fields("Purchase Order Item", parenttype="Purchase Order", permission_type="read"))
    reader.progress_item_fields = item_fields
    for name, order in orders.items():
        entry = receipts[name]
        if entry["state"] == "none" and (amount(order.per_received) > 0 or
                ("received_qty" in item_fields and any(amount(item.get("received_qty")) > 0 for item in order.items))):
            entry.update(state="review", warnings=["原生订单已有收货记录但缺少可核对的入库证明（可能为更新库存的应付）；数量待核对"])
    return totals, receipts


def _effective_order(order):
    return order.docstatus == 1 and order.status not in ("Cancelled", "Closed", "On Hold")


def _internal_row(link, targets, totals, group=None):
    if link.get("state") in ("restricted", "setup_required"):
        return dict(link)
    row = {key: link.get(key) for key in ("name", "modified", "beneficiary_company", "flow_kind", "internal_order",
        "internal_item", "allocated_qty", "allocated_uom", "stock_uom", "price_confirmed_by", "price_confirmed_on")}
    row.update(settled=None, order_unpaid=None, payable_total=None, payable_settled=None, payable_outstanding=None, currency=None, warnings=[])
    if link["flow_kind"] == "trade_custody":
        row.update(state="custody", warnings=["贸易品保管关系不形成内部应付"])
        return row
    target = targets[link["internal_order"]]
    row["currency"] = target.currency
    group = group or [link]
    row["allocations"] = [{key: entry.get(key) for key in ("name", "modified", "internal_item", "external_item",
        "allocated_qty", "allocated_uom", "allocated_stock_qty", "stock_uom")} for entry in group]
    if any(entry.get("source_stale") for entry in group):
        row.update(state="source_stale", warnings=["原生来源明细、数量或订单版本已变化，请重新核对关联"])
    elif any(not entry["price_confirmation"].get("version") for entry in group):
        row.update(state="price_unconfirmed", warnings=[fulfilment.PRICE_WARNING])
    elif any(entry["price_confirmation"]["version"] != entry["current_price_version"] or
             entry["price_confirmation"].get("seller_price_version") != entry["current_seller_price_version"] for entry in group):
        row.update(state="price_stale", warnings=["原生内部价格、币种或订单版本已变化，请重新确认"])
    elif not link["coverage"].get("exact"):
        row.update(state="shared", warnings=[fulfilment.SHARED_WARNING])
    elif not _effective_order(target):
        row.update(state="draft_quote" if target.docstatus == 0 else "review", warnings=["内部订单尚未生效或已暂停/取消，请核对原生单据"])
    else:
        entry = totals[target.name]
        if not entry["exact"]:
            row.update(state="review", warnings=[fulfilment.LINK_WARNING if entry.get("restricted") else UNCERTAIN])
        elif not entry["has_payable"]:
            row.update(state="awaiting_invoice", warnings=["已确认原生价格，尚无有效已提交内部应付；报价不形成应付"])
        else:
            row.update(state="payable", **_settlement_result(target, entry),
                       payable_total=float(entry["payable_total"]),
                       payable_settled=float(entry["payable_total"] - entry["payable_outstanding"]),
                       payable_outstanding=float(entry["payable_outstanding"]))
    return row


def _internal_rows(links, targets, totals):
    groups = {}; result = []
    for link in links:
        if not link.get("internal_order") or link.get("state"):
            result.append(_internal_row(link, targets, totals))
        else:
            groups.setdefault(link["internal_order"], []).append(link)
    result.extend(_internal_row(group[0], targets, totals, group) for group in groups.values())
    return result


def _receipt_projection(receipt):
    # Request-local native-item buckets are an index, never part of API JSON.
    return {field: receipt[field] for field in ("state", "quantities", "warnings")}


def _logistics_row(link, receipts):
    if link.get("state") in ("restricted", "setup_required"):
        return dict(link)
    snapshot = link["snapshot"]
    state = snapshot.get("state", "unknown")
    warnings = list(snapshot.get("warnings", []))
    # The association loader already applied the authoritative source-context
    # validator; this row only checks its destination and native receipt identity.
    if not link.get("cost_batch"):
        warnings.append("尚未明确关联国际物流来源")
    destinations = {str(row.get("destination") or "").strip().casefold() for row in snapshot.get("quantities", [])}
    allowed_destinations = {str(link["beneficiary_company"]).strip().casefold()}
    if link.get("custody_company"):
        allowed_destinations.add(str(link["custody_company"]).strip().casefold())
    if state == "reported" and (not destinations or not destinations <= allowed_destinations):
        state = "review"
        warnings.append("评论目的地未明确匹配本关联的原生公司，请人工核对")
    native_receipt = receipts.get(link.get("internal_order"), {"state": "none", "quantities": [], "warnings": []})
    if native_receipt["state"] == "native_received":
        if not link["coverage"].get("exact") or link.get("source_stale"):
            native_receipt = {"state": "review", "quantities": [], "warnings": [fulfilment.SHARED_WARNING]}
        elif link.get("internal_item_count", 0) > 1:
            item_quantities = native_receipt.get("_by_item", {}).get(link["internal_item"], [])
            native_receipt = {**_receipt_projection(native_receipt), "quantities": item_quantities}
            if not item_quantities:
                native_receipt = {"state": "review", "quantities": [], "warnings": ["原生入库明细缺少唯一订单明细身份，请核对"]}
    return {"name": link["name"], "beneficiary_company": link["beneficiary_company"], "state": state,
            "cost_batch": link.get("cost_batch"), "reported_quantities": snapshot.get("quantities", []) if state == "reported" else [],
            "native_receipt": _receipt_projection(native_receipt),
            "manual_nodes": link["manual_nodes"], "warnings": warnings, "erp_received_from_logistics": False,
            "provenance": [{"source_id": entry.get("source_id"),
                "author": entry.get("raw", {}).get("user_name") or entry.get("raw", {}).get("user_id"),
                "time": entry.get("raw", {}).get("operation_time"), "remark": entry.get("raw", {}).get("remark")}
                for entry in snapshot.get("timeline", [])]}


def _factory_receipt(links, targets, receipts, stock_items, source):
    """Group explicit stock allocations by native item; count each PR quantity once.

    Quantities remain independent per order/item/UOM. Unknown links, partial
    coverage or incompatible UOM cannot prove that a factory received zero.
    """
    inactive_notice = "原生订单尚未生效或已暂停/取消/关闭；工厂收货数量及待入库行动待核对"
    if not _effective_order(source):
        return {"state": "unknown", "quantities": [], "warnings": [inactive_notice]}
    groups = {}; unknown = not bool(links); warnings = []
    for link in links:
        if link.get("state") or link.get("source_stale") or not link.get("coverage", {}).get("exact"):
            unknown = True
            continue
        target = targets.get(link.get("internal_order"))
        if target and not _effective_order(target):
            unknown = True; warnings.append(inactive_notice)
            continue
        stock_item = stock_items.get((target.name, link.get("internal_item")), {}) if target else {}
        if not target or not link.get("internal_item") or not link.get("stock_uom") or stock_item.get("stock_uom") != link["stock_uom"]:
            unknown = True
            continue
        key = (target.name, link["internal_item"], link["stock_uom"])
        if key not in groups:
            groups[key] = {"internal_order": target.name, "native_item": link["internal_item"],
                "beneficiary_company": link["beneficiary_company"], "stock_uom": link["stock_uom"],
                "allocated_stock_qty": Decimal(0)}
        groups[key]["allocated_stock_qty"] += amount(link.get("allocated_stock_qty"))
    quantities = []
    for (name, item, uom), group in groups.items():
        receipt = receipts[name]
        if receipt["state"] not in ("none", "native_received"):
            unknown = True; warnings.extend(receipt["warnings"])
            continue
        native = receipt.get("_by_item", {}).get(item, [])
        if receipt["state"] == "native_received" and not native:
            unknown = True
            continue
        if any(row.get("stock_uom") != uom or row.get("stock_qty") is None for row in native):
            unknown = True
            continue
        received = sum((amount(row["stock_qty"]) for row in native), Decimal(0))
        counter = stock_items[name, item].get("received_stock_qty")
        if counter is None or counter != received:
            unknown = True
            warnings.append("原生明细收货计数与可核对入库证明不一致或权限不足；可能含更新库存应付，数量待核对")
            continue
        allocated = group["allocated_stock_qty"]
        quantities.append({**group, "allocated_stock_qty": float(allocated), "received_stock_qty": float(received),
                           "pending_stock_qty": float(max(Decimal(0), allocated - received))})
    if unknown:
        warnings.append("最终公司收货数量待核对；仅明确关联且单位可比的原生入库可确认收货")
    return {"state": "unknown" if unknown else "exact", "quantities": quantities, "warnings": list(dict.fromkeys(warnings))}


def _progress_flags(row):
    external = row["external"]; internal = row["internal"]; factory = row["factory_receipt"]
    phases = []
    if external["state"] == "exact" and amount(external.get("order_unpaid")) > 0:
        phases.append("supplier_unpaid")
    if any(entry.get("state") == "payable" and amount(entry.get("payable_outstanding")) > 0 for entry in internal):
        phases.append("internal_unsettled")
    if factory["state"] == "exact" and any(amount(entry.get("pending_stock_qty")) > 0 for entry in factory["quantities"]):
        phases.append("factory_pending")
    row["progress_phases"] = phases
    row["review_required"] = bool(row.get("progress_warnings") or external["state"] == "review" or
        any(entry.get("state") not in ("payable", "custody") for entry in internal) or factory["state"] != "exact" or
        row["domestic_receipt"]["state"] in ("restricted", "review") or
        any(entry.get("state") != "reported" or entry.get("warnings") for entry in row["receipt_logistics"]))


def _restricted_progress(name):
    return {"name": name, "state": "restricted", "settled": None, "order_unpaid": None, "received_percent": None,
            "external": {"state": "restricted"}, "internal": [], "domestic_receipt": {"state": "restricted", "quantities": [], "warnings": [fulfilment.LINK_WARNING]},
            "receipt_logistics": [], "factory_receipt": {"state": "unknown", "quantities": [], "warnings": [fulfilment.LINK_WARNING]},
            "progress_phases": [], "review_required": True, "progress_warnings": [fulfilment.LINK_WARNING], "notice": NOTICE}


def _require_source_order_fields():
    """Authority-only preflight shared by native lists and the strict progress core."""
    _require_fields("Purchase Order", fulfilment.PO_FIELDS)


def _get_order_progress_batch(names, include_items=False, *, tolerate_restricted=True):
    """Private shared list/export projection for already get_list-authorized names.

    One reader, association discovery and native edge index serve the entire
    batch. Permission-denied source rows return only a generic restricted DTO;
    database and validation failures propagate. The public wrapper stays strict.
    """
    names = list(dict.fromkeys(names))
    if not names:
        return {}
    reader = _record_reader.get() or _ProgressReader(); token = _record_reader.set(reader)
    try:
        result = {}
        with _quiet_link_errors():
            try:
                _require_source_order_fields()
            except frappe.PermissionError:
                if not tolerate_restricted:
                    raise
                return {name: _restricted_progress(name) for name in names}
        reader.preload("Purchase Order", names)
        reader.preload("Company", {doc.company for (dt, _), doc in reader.docs.items() if dt == "Purchase Order" and doc})
        orders = {}
        for name in names:
            with _quiet_link_errors():
                try:
                    doc = _read("Purchase Order", name, fulfilment.PO_FIELDS)
                    fulfilment._company(doc.company)
                    orders[name] = doc
                except (frappe.PermissionError, frappe.DoesNotExistError):
                    if not tolerate_restricted:
                        raise
                    result[name] = _restricted_progress(name)
        if not orders:
            return result
        links, targets, _ = fulfilment.load_associations(orders, reader)
        totals, receipts = _native_indexes({**orders, **targets}, reader, internal_orders=targets)
        item_fields = ITEM_FIELDS & reader.progress_item_fields
        # A stock UOM alone is not evidence that an Item updates stock. Hydrate
        # the native Item master once for all explicit factory allocations.
        reader.preload("Item", {item.item_code for target in targets.values() for item in target.items})
        stock_flags = {}
        stock_items = {}
        counter_fields = {"name", "received_qty", "qty", "uom", "stock_uom", "conversion_factor"} <= reader.progress_item_fields
        for target in targets.values():
            for item in target.items:
                code = item.item_code
                if code not in stock_flags:
                    with _quiet_link_errors():
                        try:
                            native = _read("Item", code, {"is_stock_item", "stock_uom"})
                            stock_flags[code] = native.stock_uom if native.is_stock_item else None
                        except (frappe.PermissionError, frappe.DoesNotExistError):
                            stock_flags[code] = None
                counter = None
                if counter_fields and item.get("received_qty") is not None and target.per_received is not None:
                    received_qty = amount(item.received_qty); factor = amount(item.get("conversion_factor"))
                    # PO.received_qty is in its native purchase UOM; compare PR
                    # proof only after the permitted native stock conversion.
                    if received_qty >= 0 and factor > 0 and not (len(target.items) == 1 and
                            amount(target.per_received) >= 100 and received_qty < amount(item.qty)):
                        counter = received_qty * factor
                stock_items[target.name, item.name] = {"stock_uom": stock_flags[code], "received_stock_qty": counter}
        for name, doc in orders.items():
            financial = _settlement_result(doc, totals[name])
            row = {"name": name, "modified": str(doc.modified), "currency": doc.currency, "grand_total": doc.grand_total,
                   "received_percent": doc.per_received, "docstatus": doc.docstatus, **financial, "notice": NOTICE}
            local = any(link.get("flow_kind") == "internal_local" for link in links[name])
            row["external"] = {"company": doc.company, "supplier": doc.supplier, "currency": doc.currency,
                "state": "not_applicable" if local else "exact" if financial["settlement_exact"] else "review",
                **({"settled": None, "order_unpaid": None} if local else financial)}
            row["internal"] = _internal_rows(links[name], targets, totals)
            row["domestic_receipt"] = _receipt_projection(receipts[name])
            row["receipt_logistics"] = [_logistics_row(link, receipts) for link in links[name]]
            row["factory_receipt"] = _factory_receipt(links[name], targets, receipts, stock_items, doc)
            if not links[name]:
                row["progress_warnings"] = ["最终公司/内部订单及物流来源尚未核对关联；不据来源提示认定内部应付"]
            _progress_flags(row)
            if include_items in (True, 1, "1", "true"):
                row["item_fields"] = sorted(item_fields)
                row["items"] = [{field: item.get(field) for field in item_fields} for item in doc.items]
            result[name] = row
        return result
    finally:
        _record_reader.reset(token)


@frappe.whitelist()
def get_order_progress(purchase_orders, include_items=False):
    names = json.loads(purchase_orders) if isinstance(purchase_orders, str) else purchase_orders
    if not isinstance(names, list) or not 1 <= len(names) <= 100 or any(not isinstance(name, str) or not name for name in names):
        frappe.throw(_i18n("每次仅支持 1 至 100 张采购订单"))
    return _get_order_progress_batch(names, include_items, tolerate_restricted=False)
