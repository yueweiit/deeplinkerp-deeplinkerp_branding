"""Managed associations between existing native procurement records.

The association holds identities, allocation, confirmation and evidence only.
Native PO write and every linked document/Company read authorize user changes;
the controller rejects ordinary REST/import writes, deletion and renaming.
"""
from __future__ import annotations

from frappe import _ as _i18n


import json
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

import frappe
from frappe.model import get_permitted_fields

from . import purchase_fulfilment_contract as contract
from .purchase_payment_service import _RecordReader, _quiet_link_errors, _read, _record_reader, _require_fields

DOCTYPE = "Purchase Fulfilment Link"
PO_FIELDS = {"company", "supplier", "currency", "grand_total", "items", "per_received", "status"}
ITEM_FIELDS = {"name", "item_code", "qty", "uom", "stock_uom", "conversion_factor", "rate", "amount"}
PUBLIC_FIELDS = {"external_order", "external_item", "purchasing_company", "beneficiary_company", "flow_kind",
                 "internal_order", "internal_item", "seller_order", "seller_item", "allocated_qty", "allocated_uom",
                 "allocated_stock_qty", "stock_uom", "conversion_factor", "custody_company", "custody_warehouse",
                 "cost_batch", "active", "price_confirmed_by", "price_confirmed_on"}
PRIVATE_FIELDS = {"source_versions_json", "price_confirmation_json", "snapshot_json", "manual_nodes_json", "audit_json"}
LINK_WARNING = "关联缺失或无权读取，请在原生单据核对"
MASTER_WARNING = "内部公司主数据或原生关联不完整，请先核对内部 Supplier/Customer 与代表公司"
SHARED_WARNING = "内部订单被多张外部订单共享或仅部分覆盖，整单应付余额不能归入本次关联"
PRICE_WARNING = "内部价格或币种尚未确认，请先核对原生订单；报价草稿不形成应付"
_managed = ContextVar("purchase_fulfilment_managed", default=False)


class LinkStateConflict(frappe.ValidationError):
    """Expected native allocation/CAS invalidation, raised before managed saves."""


@contextmanager
def _request_reader():
    """Share native ACL/source caches within an action, never across requests."""
    if _record_reader.get() is not None:
        yield
        return
    token = _record_reader.set(_RecordReader())
    try:
        yield
    finally:
        _record_reader.reset(token)


def _read_once(function):
    @wraps(function)
    def action(*args, **kwargs):
        with _request_reader():
            return function(*args, **kwargs)
    return action


@contextmanager
def managed_write():
    token = _managed.set(True)
    try:
        yield
    finally:
        _managed.reset(token)


def _json(value, default=None):
    if not value:
        return {} if default is None else default
    return json.loads(value) if isinstance(value, str) else value


def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _company(name, user=None):
    from frappe.permissions import get_user_permissions
    from .purchase_payment_service import _record_reader
    user = user or frappe.session.user
    reader = _record_reader.get()
    permissions = getattr(reader, "fulfilment_company_permissions", None) if reader else None
    if permissions is None:
        permissions = {row.doc for row in get_user_permissions(user).get("Company", [])}
        if reader:
            reader.fulfilment_company_permissions = permissions
    if permissions and name not in permissions:
        frappe.throw(_i18n("无权访问此公司"), frappe.PermissionError)
    return _read("Company", name)


def order_data(doc):
    return {"name": doc.name, "modified": str(doc.modified), "company": doc.company,
            "supplier": doc.supplier, "currency": doc.currency, "grand_total": doc.grand_total,
            "docstatus": doc.docstatus,
            "items": [{field: item.get(field) for field in ITEM_FIELDS} for item in doc.items]}


def _seller_price_data(doc):
    return {"name": doc.name, "currency": doc.currency, "grand_total": doc.grand_total,
            "items": [{field: item.get(field) for field in ITEM_FIELDS} for item in doc.items]}


def _write_permission(order):
    order.check_permission("write")
    # The explicit user action depends on these native fields even though no PO is changed.
    permitted = set(get_permitted_fields("Purchase Order", permission_type="write"))
    meta = frappe.get_meta("Purchase Order")
    levels = set(meta.get_permlevel_access(permission_type="write"))
    permitted.update(field.fieldname for field in meta.fields if field.fieldtype in ("Table", "Table MultiSelect")
                     and field.permlevel in levels)
    if not {"company", "supplier", "currency", "items"} <= permitted:
        frappe.throw(_i18n("无权核对完整采购字段"), frappe.PermissionError)


def _order(name, *, write=False, lock=False):
    if lock:
        doc = frappe.get_doc("Purchase Order", name, for_update=True)
        doc.check_permission("read")
        _require_fields("Purchase Order", PO_FIELDS)
    else:
        doc = _read("Purchase Order", name, PO_FIELDS)
    _company(doc.company)
    _require_fields("Purchase Order Item", ITEM_FIELDS, "Purchase Order")
    if write:
        _write_permission(doc)
    return doc


def _internal_supplier(order, seller_company=None):
    supplier = _read("Supplier", order.supplier, {"is_internal_supplier", "represents_company", "disabled"})
    if (not supplier.get("is_internal_supplier") or supplier.get("disabled") or not supplier.get("represents_company")
            or (seller_company and supplier.represents_company != seller_company)):
        frappe.throw(MASTER_WARNING + "（内部供应商）")
    _company(supplier.represents_company)
    return supplier


def authorize_link(doc, *, write=False, external=None, internal=None, require_cost=False):
    """All identities pass native permissions before projection of names or prices."""
    external = external or _order(doc.external_order, write=write)
    if write and external:
        _write_permission(external)
    for name in {doc.get("purchasing_company"), doc.get("beneficiary_company"), doc.get("custody_company")} - {None, ""}:
        _company(name)
    if doc.purchasing_company != external.company:
        frappe.throw(_i18n("采购关联公司与原生订单已变化"))
    flow = doc.flow_kind
    seller_company = external.company
    if flow in ("external_internal", "internal_local"):
        internal = internal or _order(doc.internal_order)
        if internal.company != doc.beneficiary_company:
            frappe.throw(_i18n("内部订单公司与最终受益公司不符"))
        supplier = _internal_supplier(internal, external.company if flow == "external_internal" else None)
        seller_company = supplier.represents_company
        if flow == "internal_local" and (external.name != internal.name or external.company != doc.beneficiary_company):
            frappe.throw(_i18n("本地内部采购必须使用当前工厂原生订单"))
    elif flow == "trade_custody":
        if doc.get("internal_order") or doc.get("internal_item") or external.company != doc.beneficiary_company:
            frappe.throw(_i18n("贸易保管不形成内部应付，所有公司应为采购公司"))
        warehouse = _read("Warehouse", doc.custody_warehouse, {"company", "is_group", "disabled"})
        _company(warehouse.company)
        if warehouse.company != doc.custody_company or warehouse.is_group or warehouse.disabled:
            frappe.throw(_i18n("保管仓库与保管公司不符或不可用"))
    else:
        frappe.throw(_i18n("采购关联类型无效"))
    seller = None
    if doc.get("seller_order"):
        seller = _read("Sales Order", doc.seller_order, {"company", "customer", "currency", "grand_total", "items"})
        _company(seller.company)
        _require_fields("Sales Order Item", ITEM_FIELDS, "Sales Order")
        customer = _read("Customer", seller.customer, {"is_internal_customer", "represents_company", "disabled"})
        if (seller.company != seller_company or not customer.is_internal_customer or customer.disabled
                or customer.represents_company != doc.beneficiary_company):
            frappe.throw(MASTER_WARNING + "（内部客户）")
        _company(customer.represents_company)
    if doc.get("cost_batch"):
        from .purchase_fulfilment_logistics import local_source
        local_source(doc.cost_batch, required=require_cost)
    return external, internal, seller


def _authorize(doc, **kwargs):
    denied = False
    with _quiet_link_errors():
        try:
            return authorize_link(doc, **kwargs)
        except (frappe.DoesNotExistError, frappe.PermissionError):
            denied = True
    if denied:
        frappe.throw(LINK_WARNING, frappe.PermissionError)


def validate_managed_link(doc, method=None):
    if not _managed.get():
        frappe.throw(_i18n("采购履行关联请使用订单进度的核对入口"), frappe.PermissionError)


def protect_link_identity(doc, method=None, *args, **kwargs):
    frappe.throw(_i18n("采购履行关联需保留审计身份，请停用关联，不能删除或重命名"), frappe.PermissionError)


def has_permission(doc, user=None, ptype=None):
    if ptype in ("delete", "cancel", "submit") or (ptype in ("write", "create") and not _managed.get()):
        return False
    with _quiet_link_errors():
        try:
            # Frappe calls the hook with session user on ordinary document reads.
            if user and user != frappe.session.user:
                return False
            authorize_link(doc, write=ptype in ("write", "create"))
            return True
        except (frappe.DoesNotExistError, frappe.PermissionError, frappe.ValidationError):
            return False


def permission_query_conditions(user=None):
    # The explicit source-authorized API performs linked PO/Company/field checks.
    # A native REST list cannot safely express all those linked document permissions.
    return "1=0"


def _check_version(doc, expected, label):
    if not expected or str(doc.modified) != str(expected):
        frappe.throw(label + "已变化，请刷新后重新核对", LinkStateConflict)


def _audit(doc, action, details=None):
    history = _json(doc.get("audit_json"), [])
    history.append({"action": action, "by": frappe.session.user, "on": str(frappe.utils.now_datetime()),
                    "details": details or {}})
    doc.audit_json = _dump(history)


def _save(doc, *, warning_cost_batch=None):
    with managed_write():
        doc.insert() if doc.is_new() else doc.save()
    result = {"name": doc.name, "modified": str(doc.modified), "active": int(doc.active)}
    batch = doc.get("cost_batch") or warning_cost_batch
    if batch:
        from .purchase_fulfilment_logistics import local_source
        warning = local_source(batch).get("warning")
        if warning:
            result["warnings"] = [warning]
    return result


def _locked_link(name, expected_modified, *, include_inactive=False):
    initial = frappe.get_doc(DOCTYPE, name)
    _authorize(initial, write=True)
    # Every mutation uses parents -> link/allocations. Taking the link first
    # would deadlock a concurrent new association holding the same PO parent.
    orders = {key: _order(key, lock=True, write=key == initial.external_order)
              for key in sorted({initial.external_order, initial.get("internal_order")} - {None, ""})}
    doc = frappe.get_doc(DOCTYPE, name, for_update=True)
    if _link_identity(doc) != _link_identity(initial):
        frappe.throw(_i18n("关联身份已变化，请刷新后重新核对"), LinkStateConflict)
    _check_version(doc, expected_modified, "关联记录")
    if not include_inactive and not doc.active:
        frappe.throw(_i18n("关联已停用，请重新建立关联"), LinkStateConflict)
    external, internal, seller = _authorize(doc, write=True, external=orders[doc.external_order],
                                           internal=orders.get(doc.get("internal_order")))
    existing = _current_allocations(doc.external_order, doc.get("internal_order"))
    if not include_inactive:
        try:
            contract.validate_allocation(order_data(external), order_data(internal) if internal else None,
                {"name": doc.name, **{field: doc.get(field) for field in PUBLIC_FIELDS}}, existing)
        except ValueError as error:
            frappe.throw(str(error), LinkStateConflict)
    # Soft disable still takes the current quota locks, but must release a link
    # even if a later native source quantity makes its former allocation invalid.
    return doc, external, internal, seller


def _link_identity(doc):
    return tuple(doc.get(field) for field in ("external_order", "external_item", "internal_order", "internal_item"))


def _current_allocations(external_order, internal_order):
    # Locking reads see the current committed allocation set even when earlier
    # permission/metadata reads have established a REPEATABLE READ snapshot.
    predicate = "external_order=%s"
    values = [external_order]
    if internal_order:
        predicate += " OR internal_order=%s"
        values.append(internal_order)
    return frappe.db.sql("SELECT name,active,external_order,external_item,internal_order,internal_item,"
        "allocated_qty,allocated_stock_qty FROM `tabPurchase Fulfilment Link` WHERE active=1 AND (" + predicate +
        ") ORDER BY name FOR UPDATE", values, as_dict=True)


@frappe.whitelist(methods=["POST"])
@_read_once
def save_link(external_order, external_item, purchasing_company, beneficiary_company, flow_kind, allocated_qty,
              expected_modified, internal_order=None, internal_item=None, expected_internal_modified=None,
              seller_order=None, seller_item=None, expected_seller_modified=None, cost_batch=None,
              custody_company=None, custody_warehouse=None, name=None, expected_link_modified=None):
    """Explicit native IDs only. No fuzzy matching or price/currency allocation."""
    required = {"external_order": external_order, "external_item": external_item,
                "purchasing_company": purchasing_company, "beneficiary_company": beneficiary_company}
    identities = {**required, "internal_order": internal_order, "internal_item": internal_item, "seller_order": seller_order,
                  "seller_item": seller_item, "cost_batch": cost_batch, "custody_company": custody_company,
                  "custody_warehouse": custody_warehouse, "name": name}
    if any(not isinstance(value, str) or not value.strip() or len(value) > 140 for value in required.values()) or any(
            value is not None and value != "" and (not isinstance(value, str) or len(value) > 140) for value in identities.values()):
        frappe.throw(_i18n("请提供明确的原生单据、公司及明细 ID"))
    if not frappe.db.exists("DocType", DOCTYPE):
        frappe.throw(_i18n("采购履行关联元数据尚未安装，请管理员更新应用"))
    initial = frappe.get_doc(DOCTYPE, name) if name else None
    if initial:
        if initial.external_order != external_order or initial.external_item != external_item:
            frappe.throw(_i18n("关联来源身份不能改写，请停用后重新建立关联"))
        _authorize(initial, write=True)
    order_names = {external_order, internal_order, initial.get("internal_order") if initial else None} - {None, ""}
    orders = {key: _order(key, lock=True, write=key == external_order) for key in sorted(order_names)}
    old = frappe.get_doc(DOCTYPE, name, for_update=True) if name else None
    if old:
        if _link_identity(old) != _link_identity(initial):
            frappe.throw(_i18n("关联身份已变化，请刷新后重新核对"))
        _check_version(old, expected_link_modified, "关联记录")
    old_batch = old.get("cost_batch") if old else None
    old_logistics = _json(old.get("source_versions_json")).get("logistics", {}) if old else {}
    old_snapshot = _json(old.get("snapshot_json")) if old else {}
    external = orders[external_order]
    _check_version(external, expected_modified, "外部采购订单")
    internal = orders.get(internal_order)
    if internal:
        _check_version(internal, expected_internal_modified, "内部采购订单")
    values = dict(external_order=external_order, external_item=external_item, purchasing_company=purchasing_company,
        beneficiary_company=beneficiary_company, flow_kind=flow_kind, internal_order=internal_order,
        internal_item=internal_item, seller_order=seller_order, seller_item=seller_item, allocated_qty=allocated_qty,
        cost_batch=cost_batch, custody_company=custody_company, custody_warehouse=custody_warehouse, active=1)
    if name:
        values["name"] = name
    doc = old or frappe.new_doc(DOCTYPE)
    doc.update(values)
    _, _, seller = _authorize(doc, write=True, external=external, internal=internal,
        require_cost=bool(cost_batch and cost_batch != old_batch))
    if seller:
        _check_version(seller, expected_seller_modified, "内部销售订单")
    # Parents are already locked/authorized and this action never changes them.
    # Reuse the same native projection for seller identity, quota and version.
    external_data = order_data(external)
    internal_data = external_data if internal is external else order_data(internal) if internal else None
    if seller:
        source_item = contract.native_item(external_data, external_item)
        target_item = contract.native_item({"items": [row.as_dict() if hasattr(row, "as_dict") else dict(row)
            for row in seller.items]}, seller_item)
        if source_item["item_code"] != target_item["item_code"] or source_item["stock_uom"] != target_item["stock_uom"]:
            frappe.throw(_i18n("内部销售明细物料或单位不符"))
    existing = _current_allocations(external_order, internal_order)
    try:
        checked = contract.validate_allocation(external_data, internal_data, values, existing)
    except ValueError as error:
        frappe.throw(str(error))
    doc.update({key: checked[key] for key in ("allocated_qty", "allocated_stock_qty", "allocated_uom", "stock_uom", "conversion_factor")})
    versions = {"external_modified": str(external.modified),
                "external_item_version": contract.item_version(external_data, contract.native_item(external_data, external_item)),
                "internal_modified": str(internal.modified) if internal else None,
                "seller_modified": str(seller.modified) if seller else None}
    logistics_changed = bool(old and old_batch != cost_batch)
    if cost_batch:
        from .purchase_fulfilment_logistics import local_source
        local = local_source(cost_batch, required=cost_batch != old_batch)
        versions["logistics"] = old_logistics if local["warning"] else local["context"]
        if old and not local["warning"]:
            logistics_changed = logistics_changed or contract.validate_snapshot_context(
                old_snapshot, local["context"], old_logistics)["state"] == "stale"
    doc.source_versions_json = _dump(versions)
    doc.price_confirmation_json = "{}"
    doc.price_confirmed_by = None; doc.price_confirmed_on = None
    # Identity, eligibility and version changes matter even when the batch name
    # is unchanged. Retain former evidence only as private audit, never current.
    if logistics_changed:
        if old_snapshot or old_logistics:
            _audit(doc, "change_logistics_binding", {"previous_cost_batch": old_batch,
                "previous_logistics": old_logistics, "previous_snapshot": old_snapshot})
        doc.snapshot_json = "{}"
    _audit(doc, "associate", {key: doc.get(key) for key in PUBLIC_FIELDS - {"price_confirmed_by", "price_confirmed_on"}})
    return _save(doc, warning_cost_batch=old_batch)


@frappe.whitelist(methods=["POST"])
@_read_once
def confirm_native_price(name, expected_modified, expected_internal_modified, expected_link_modified,
                         expected_seller_modified=None):
    doc, external, internal, seller = _locked_link(name, expected_link_modified)
    _check_version(external, expected_modified, "外部采购订单")
    if not internal or doc.flow_kind == "trade_custody":
        frappe.throw(_i18n("贸易保管不形成内部价格或应付"))
    _check_version(internal, expected_internal_modified, "内部采购订单")
    if seller:
        _check_version(seller, expected_seller_modified, "内部销售订单")
    data = order_data(internal)
    item = contract.native_item(data, doc.internal_item)
    try:
        contract.number(item.get("rate")); contract.number(item.get("amount"))
    except ValueError:
        frappe.throw(_i18n("内部原生价格尚未填写，请先核对价格及币种"))
    if not internal.currency:
        frappe.throw(_i18n("内部原生订单缺少币种"))
    doc.price_confirmed_by = frappe.session.user
    doc.price_confirmed_on = frappe.utils.now_datetime()
    version = contract.price_version(data)
    doc.price_confirmation_json = _dump({"version": version, "currency": internal.currency,
        "native_order": internal.name, "native_item": doc.internal_item, "rate": str(item["rate"]),
        "amount": str(item["amount"]), "seller_modified": str(seller.modified) if seller else None,
        "seller_price_version": contract.price_version(_seller_price_data(seller)) if seller else None,
        "by": doc.price_confirmed_by, "on": str(doc.price_confirmed_on)})
    _audit(doc, "confirm_native_price", {"version": version, "currency": internal.currency})
    return _save(doc)


@frappe.whitelist(methods=["POST"])
@_read_once
def disable_link(name, expected_link_modified, reason):
    doc, _, _, _ = _locked_link(name, expected_link_modified, include_inactive=True)
    if not str(reason or "").strip():
        frappe.throw(_i18n("请填写停用原因"))
    doc.active = 0
    _audit(doc, "disable", {"reason": str(reason).strip()[:4096]})
    return _save(doc)


@frappe.whitelist(methods=["POST"])
@_read_once
def set_manual_node(name, expected_link_modified, node, note, qty=None, uom=None):
    doc, _, _, _ = _locked_link(name, expected_link_modified)
    if node not in {"domestic_dispatch", "international_dispatch", "customs", "reported_arrival", "review", "evidence"}:
        frappe.throw(_i18n("手工物流节点无效"))
    if not str(note or "").strip() or len(str(note)) > 4096:
        frappe.throw(_i18n("请填写不超过 4096 字的核对证据"))
    event = {"node": node, "note": str(note).strip(), "by": frappe.session.user,
             "on": str(frappe.utils.now_datetime()), "origin": "manual", "erp_received": False}
    if qty is not None or uom:
        if not uom:
            frappe.throw(_i18n("手工数量需明确单位，不与其他单位混加"))
        try:
            event.update(qty=contract.decimal_text(contract.number(qty)), uom=str(uom))
        except ValueError as error:
            frappe.throw(str(error))
    manual = _json(doc.get("manual_nodes_json"), [])
    manual.append(event)
    doc.manual_nodes_json = _dump(manual)
    _audit(doc, "manual_node", event)
    return _save(doc)


@frappe.whitelist(methods=["POST"])
@_read_once
def add_evidence(name, expected_link_modified, note, file=None):
    # Attachments stay native File references and require native read, never raw URLs.
    if file:
        attachment = _read("File", file, {"attached_to_doctype", "attached_to_name"})
        doc = frappe.get_doc(DOCTYPE, name)
        if (attachment.attached_to_doctype, attachment.attached_to_name) not in {
                (DOCTYPE, name), ("Purchase Order", doc.external_order), ("Purchase Order", doc.get("internal_order"))}:
            frappe.throw(_i18n("附件不属于此原生采购关联"))
        note = str(note or "") + "\nFile: " + file
    return set_manual_node(name, expected_link_modified, "evidence", note)


@frappe.whitelist(methods=["POST"])
@_read_once
def refresh_logistics(name, expected_link_modified):
    doc, _, _, _ = _locked_link(name, expected_link_modified)
    from .purchase_fulfilment_logistics import refresh_source, unavailable_snapshot, CAPABILITY_WARNING
    if not doc.get("cost_batch"):
        snapshot = unavailable_snapshot("尚未关联可用的国际物流成本批次")
    else:
        snapshot = refresh_source(doc.cost_batch, _json(doc.get("source_versions_json")).get("logistics", {}))
    if CAPABILITY_WARNING not in snapshot.get("warnings", []):
        doc.snapshot_json = _dump(snapshot)
        doc.snapshot_refreshed_on = frappe.utils.now_datetime()
    _audit(doc, "refresh_logistics", {"source_context": snapshot.get("source_context"), "state": snapshot["state"]})
    result = _save(doc)
    return {**result, "warnings": snapshot.get("warnings", [])}


def _project_logistics(result, doc):
    """The same authorized cached-evidence projection serves detail and lists."""
    if not doc.get("cost_batch"):
        return
    from .purchase_fulfilment_logistics import local_source, unavailable_snapshot
    local = local_source(doc.cost_batch)
    if local["warning"]:
        result.update(cost_batch=None, snapshot=unavailable_snapshot(local["warning"]), warnings=[local["warning"]])
        result["source_versions"] = {key: value for key, value in result["source_versions"].items() if key != "logistics"}
    else:
        result["snapshot"] = contract.validate_snapshot_context(result["snapshot"], local["context"],
                                                                result["source_versions"].get("logistics", {}))


def load_associations(orders, reader):
    """One bounded-page discovery; each native target and context is read once."""
    result = {name: [] for name in orders}
    if not frappe.db.exists("DocType", DOCTYPE):
        return result, {}, {}
    raw = frappe.get_all(DOCTYPE, filters={"external_order": ["in", list(orders)], "active": 1},
                         fields=["*"], limit_page_length=0)
    if not raw:
        return result, {}, {}
    with _quiet_link_errors():
        try:
            _require_fields("Purchase Order Item", ITEM_FIELDS, "Purchase Order")
        except frappe.PermissionError:
            for row in raw:
                result[row.external_order].append({"state": "restricted", "warnings": [LINK_WARNING]})
            return result, {}, {}
    target_names = {row.internal_order for row in raw if row.get("internal_order")}
    reader.preload("Purchase Order", target_names)
    reader.preload("Company", {row.get(field) for row in raw for field in (
        "purchasing_company", "beneficiary_company", "custody_company") if row.get(field)})
    supplier_names = {reader.docs["Purchase Order", name].supplier for name in target_names
                      if reader.docs.get(("Purchase Order", name))}
    reader.preload("Supplier", supplier_names)
    reader.preload("Company", {doc.represents_company for (dt, _), doc in reader.docs.items()
                               if dt == "Supplier" and doc and doc.get("represents_company")})
    reader.preload("Sales Order", {row.seller_order for row in raw if row.get("seller_order")})
    sellers = [doc for (dt, _), doc in reader.docs.items() if dt == "Sales Order" and doc]
    reader.preload("Customer", {doc.customer for doc in sellers})
    reader.preload("Warehouse", {row.custody_warehouse for row in raw if row.get("custody_warehouse")})
    reader.preload("Company", {doc.get(field) for (dt, _), doc in reader.docs.items()
        if dt in ("Sales Order", "Customer", "Warehouse") and doc for field in ("company", "represents_company") if doc.get(field)})
    from .purchase_fulfilment_logistics import cost_schema_exists
    if cost_schema_exists():
        reader.preload("Overseas Cost Batch", {row.cost_batch for row in raw if row.get("cost_batch")})
    data = {}; targets = {}; price_versions = {}
    for name in target_names:
        with _quiet_link_errors():
            try:
                doc = _order(name)
                targets[name] = doc; data[name] = order_data(doc)
                price_versions[name] = contract.price_version(data[name])
            except (frappe.DoesNotExistError, frappe.PermissionError):
                pass
    all_links = frappe.get_all(DOCTYPE, filters={"internal_order": ["in", list(targets)], "active": 1},
        fields=["name", "external_order", "internal_order", "internal_item", "allocated_stock_qty", "active", "flow_kind"],
        limit_page_length=0) if targets else []
    try:
        coverage = contract.internal_coverage(all_links, data)
    except ValueError:
        coverage = {name: {"exact": False} for name in targets}
    source_data = {name: order_data(order) for name, order in orders.items()}
    source_items = {name: {item["name"]: item for item in order["items"]} for name, order in source_data.items()}
    source_versions = {(name, item["name"]): contract.item_version(order, item)
                       for name, order in source_data.items() for item in order["items"]}
    seller_versions = {seller.name: contract.price_version(_seller_price_data(seller)) for seller in sellers}
    target_items = {name: {item["name"]: item for item in order["items"]} for name, order in data.items()}
    source_allocated = {}
    invalid_sources = set()
    for row in raw:
        key = (row.external_order, row.external_item)
        try:
            source_allocated[key] = source_allocated.get(key, 0) + contract.number(row.allocated_qty)
        except ValueError:
            invalid_sources.add(key)
    for row in raw:
        doc = frappe.get_doc({"doctype": DOCTYPE, **row})
        with _quiet_link_errors():
            try:
                _require_fields(DOCTYPE, PUBLIC_FIELDS)
                external, internal, seller = authorize_link(doc, external=orders[doc.external_order],
                                                            internal=targets.get(doc.get("internal_order")))
            except (frappe.DoesNotExistError, frappe.PermissionError):
                result[row.external_order].append({"state": "restricted", "warnings": [LINK_WARNING]})
                continue
            except frappe.ValidationError as error:
                from .purchase_fulfilment_logistics import CAPABILITY_WARNING
                warning = CAPABILITY_WARNING if str(error) == CAPABILITY_WARNING else MASTER_WARNING
                result[row.external_order].append({"state": "setup_required", "warnings": [warning]})
                continue
        projected = {field: doc.get(field) for field in PUBLIC_FIELDS}
        key = (doc.external_order, doc.external_item)
        source_item = source_items[doc.external_order].get(doc.external_item)
        target_item = target_items.get(doc.get("internal_order"), {}).get(doc.get("internal_item"))
        source_stale = key in invalid_sources or not source_item
        if source_item:
            try:
                source_stale = source_stale or source_allocated[key] > contract.number(source_item["qty"])
                source_stale = source_stale or contract.number(doc.allocated_stock_qty) != (
                    contract.number(doc.allocated_qty) * contract.number(source_item["conversion_factor"]))
            except ValueError:
                source_stale = True
        if doc.flow_kind != "trade_custody":
            source_stale = source_stale or not target_item or bool(source_item and target_item and (
                source_item["item_code"] != target_item["item_code"] or source_item["stock_uom"] != target_item["stock_uom"]))
        projected.update(name=doc.name, modified=str(doc.modified),
            price_confirmation=_json(doc.get("price_confirmation_json")),
            source_versions=_json(doc.get("source_versions_json")), snapshot=_json(doc.get("snapshot_json")),
            manual_nodes=_json(doc.get("manual_nodes_json"), []), warnings=[],
            current_price_version=price_versions.get(doc.get("internal_order")),
            current_seller_price_version=seller_versions.get(seller.name) if seller else None,
            coverage=coverage.get(doc.get("internal_order"), {"exact": False}), source_stale=source_stale,
            internal_item_count=len(data.get(doc.get("internal_order"), {}).get("items", [])))
        _project_logistics(projected, doc)
        projected["source_stale"] = projected["source_stale"] or (
            projected["source_versions"].get("external_item_version") != source_versions.get(key))
        result[row.external_order].append(projected)
    return result, targets, coverage


@frappe.whitelist()
@_read_once
def get_link_detail(name):
    doc = frappe.get_doc(DOCTYPE, name)
    external, internal, seller = _authorize(doc)
    _require_fields(DOCTYPE, PUBLIC_FIELDS)
    result = {field: doc.get(field) for field in PUBLIC_FIELDS}
    result.update(name=doc.name, modified=str(doc.modified), external_modified=str(external.modified),
        internal_modified=str(internal.modified) if internal else None, seller_modified=str(seller.modified) if seller else None,
        native_internal=order_data(internal) if internal else None, source_versions=_json(doc.get("source_versions_json")),
        price_confirmation=_json(doc.get("price_confirmation_json")), snapshot=_json(doc.get("snapshot_json")),
        manual_nodes=_json(doc.get("manual_nodes_json"), []))
    _project_logistics(result, doc)
    return result


@frappe.whitelist()
@_read_once
def get_link_candidates(external_order, beneficiary_company, flow_kind="external_internal", custody_company=None):
    external = _order(external_order)
    _company(beneficiary_company)
    if flow_kind == "trade_custody":
        _company(custody_company)
        _require_fields("Warehouse", {"company", "is_group", "disabled"})
        rows = frappe.get_list("Warehouse", filters={"company": custody_company, "is_group": 0, "disabled": 0},
                               fields=["name"], limit_page_length=100)
        return {"purchase_orders": [], "warehouses": [row.name for row in rows], "warnings": []}
    _require_fields("Supplier", {"is_internal_supplier", "represents_company", "disabled"})
    filters = {"is_internal_supplier": 1, "disabled": 0}
    if flow_kind == "external_internal":
        filters["represents_company"] = external.company
    elif flow_kind != "internal_local":
        frappe.throw(_i18n("采购关联类型无效"))
    suppliers = frappe.get_list("Supplier", filters=filters, fields=["name"], limit_page_length=100)
    if not suppliers:
        return {"purchase_orders": [], "warnings": [MASTER_WARNING]}
    names = frappe.get_list("Purchase Order", filters={"company": beneficiary_company,
        "supplier": ["in", [row.name for row in suppliers]], "docstatus": ["!=", 2]}, fields=["name"], limit_page_length=100)
    rows = []
    for row in names:
        with _quiet_link_errors():
            try:
                target = _order(row.name)
                _internal_supplier(target, external.company if flow_kind == "external_internal" else None)
                if flow_kind == "internal_local" and target.name != external.name:
                    continue
                rows.append(order_data(target))
            except (frappe.DoesNotExistError, frappe.PermissionError, frappe.ValidationError):
                continue
    return {"purchase_orders": rows, "warnings": [] if rows else [MASTER_WARNING]}
