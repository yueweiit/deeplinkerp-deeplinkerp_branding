"""Procurement reads and native Payment Entry drafts; never submit or post GL here."""
from __future__ import annotations

from frappe import _ as _i18n


import hashlib
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal, InvalidOperation

import frappe
from frappe.model import get_permitted_fields
from frappe.utils import getdate, nowdate

from deeplinkerp_branding.services.unified_purchase_service import _require_export_permission

from .purchase_repost_boundary import procurement_entry

SOURCES = {"Purchase Receipt", "Purchase Order"}
PI_FIELDS = {"company", "supplier", "currency", "party_account_currency", "grand_total", "base_grand_total",
             "rounded_total", "base_rounded_total", "disable_rounded_total", "outstanding_amount", "items", "is_return", "on_hold"}

_record_reader = ContextVar("purchase_payment_record_reader", default=None)


class _RecordReader:
    """Request-local bulk hydration; every exposed document still passes normal permissions."""

    def __init__(self):
        self.docs = {}
        self.fields = set()
        self.checked = set()

    def preload(self, doctype, names):
        # Identity lookup needs no sorting or scan of unrelated cached documents.
        names = [name for name in dict.fromkeys(names) if (doctype, name) not in self.docs]
        for offset in range(0, len(names), 500):
            chunk = names[offset:offset + 500]
            # Same internal raw values as get_doc/load_from_db, never returned directly.
            parents = frappe.db.get_values(doctype, {"name": ["in", chunk]}, "*", as_dict=True)
            by_name = {row.name: row for row in parents}
            # Hydrate all child tables, including fields inspected by controller/user permissions.
            for field in frappe.get_meta(doctype).get_table_fields():
                for row in by_name.values():
                    row[field.fieldname] = []
                if by_name:
                    children = frappe.db.get_values(field.options, {"parent": ["in", list(by_name)],
                        "parenttype": doctype, "parentfield": field.fieldname}, "*", as_dict=True, order_by="idx asc")
                    for child in children:
                        by_name[child.parent][field.fieldname].append(child)
            for name in chunk:
                row = by_name.get(name)
                doc = frappe.get_doc({"doctype": doctype, **row}) if row else None
                if doc:
                    if hasattr(doc, "__setup__"):
                        doc.__setup__()
                    doc.mask_fields()
                self.docs[doctype, name] = doc

    def doc(self, doctype, name):
        key = (doctype, name)
        if key not in self.docs:
            self.docs[key] = frappe.get_doc(doctype, name)
        doc = self.docs[key]
        if doc is None:
            raise frappe.DoesNotExistError
        return doc

    def check(self, doc):
        key = (doc.doctype, doc.name)
        if key not in self.checked:
            doc.check_permission("read")
            self.checked.add(key)


def _read_doc(doctype, name):
    reader = _record_reader.get()
    return reader.doc(doctype, name) if reader else frappe.get_doc(doctype, name)


def _check_read(doc):
    reader = _record_reader.get()
    if reader:
        reader.check(doc)
    else:
        doc.check_permission("read")


def amount(value):
    try:
        value = Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        raise ValueError(_i18n("金额必须为有效数字"))
    if not value.is_finite():
        raise ValueError(_i18n("金额必须为有限数字"))
    return value


def _require_fields(doctype, fields, parenttype=None):
    reader = _record_reader.get()
    key = (doctype, frozenset(fields), parenttype)
    if reader and key in reader.fields:
        return
    permitted = set(get_permitted_fields(doctype, parenttype=parenttype, permission_type="read"))
    meta = frappe.get_meta(doctype)
    levels = set(meta.get_permlevel_access(permission_type="read", parenttype=parenttype))
    permitted.update(df.fieldname for df in meta.fields if df.fieldtype in ("Table", "Table MultiSelect") and df.permlevel in levels)
    if not set(fields) <= permitted:
        frappe.throw(_i18n("无权查看完整关联或金额字段"), frappe.PermissionError)
    if reader:
        reader.fields.add(key)


def _read(doctype, name, fields=()):
    doc = _read_doc(doctype, name)
    _check_read(doc)
    _require_fields(doctype, fields)
    if doc.get("company"):
        _check_read(_read_doc("Company", doc.company))
    return doc


def _current(doctype, name):
    from .purchase_repost_boundary import initialize
    initialize()
    doc = frappe.get_doc(doctype, name, for_update=True)
    doc.check_permission("read")
    if doc.get("company"):
        _read("Company", doc.company)
    return doc


class _CurrentReader(_RecordReader):
    """Fresh locking parents AND children; never the UI bulk-reader cache."""
    def doc(self, doctype, name):
        # A nested native hook can update a parent already read by the outer
        # call (PO submit -> automatic PR draft). The collector owns its own
        # immutable-phase cache; the orchestration reader must not retain it.
        return frappe.get_doc(doctype, name, for_update=True)

    def check(self, doc):
        doc.check_permission("read")


@contextmanager
def current_reads():
    token = _record_reader.set(_CurrentReader())
    try:
        yield
    finally:
        _record_reader.reset(token)


def order_execution_reason(order, *, allow_completed_invoice_payment=False):
    """PO-authorized source-state check; never expose the OA payload or financial settings."""
    if order.docstatus != 1:
        return "请先核对并提交采购订单，再办理付款或入库"
    if order.status in ("Closed", "Cancelled", "On Hold") or (
            order.status == "Completed" and not allow_completed_invoice_payment):
        return "采购订单已关闭、取消、暂停或完成，请先核对订单状态"
    name = order.get("custom_oa_purchase_expense")
    if not name:
        return ""
    meta = frappe.get_meta("OA Purchase Request")
    fields = [f for f in ("approval_status", "source_pending", "source_invalid", "source_stale",
                         "source_fingerprint", "latest_source_fingerprint", "target_company") if meta.has_field(f)]
    state = frappe.db.get_value("OA Purchase Request", name, fields, as_dict=True)
    if not state:
        return "采购来源记录缺失，请先核对订单"
    if state.get("target_company") and state.target_company != order.company:
        return "采购来源公司与订单不一致，请先核对"
    if state.get("source_invalid"):
        return "采购来源已拒绝或撤销，不能继续执行"
    if state.get("source_stale") or (state.get("source_fingerprint") and state.get("latest_source_fingerprint")
                                     and state.source_fingerprint != state.latest_source_fingerprint):
        return "采购来源已更新，请先复核订单"
    from overseas_costing.scripts.import_oa_logistics import (
        COMPLETED_APPROVAL_STATUSES,
        resolve_approval_decision,
    )
    decision = resolve_approval_decision(state.get("approval_status"))
    if decision["excluded"]:
        return "采购来源已拒绝或撤销，不能继续执行"
    approved = {str(value).strip().upper() for value in COMPLETED_APPROVAL_STATUSES} | {"通过"}
    if state.get("source_pending") or decision["effective_status"].strip().upper() not in approved:
        return "采购来源仍在审批中，请等待通过并确认订单"
    return ""


def invoice_payment_eligible(doc):
    """Match native PE's final on_hold refusal; release dates never auto-unhold."""
    return doc.docstatus == 1 and not doc.is_return and not doc.get("on_hold")


def receipt_eligibility(order, *, can_create, reversal_pending=False):
    """Read the already-authorized projection; never fetch an order per list row."""
    reason = ""
    try:
        progress = amount(order.get("per_received")) if order.get("per_received") is not None else None
    except (ValueError, TypeError):
        progress = None
    if reversal_pending:
        reason = "采购冲销未完成，暂不能入库"
    elif order.get("docstatus") != 1:
        reason = "订单未提交或已取消"
    elif order.get("status") in {"Closed", "Cancelled", "On Hold", "Completed"}:
        reason = "订单已关闭、取消、暂停或完成"
    elif progress is None or not progress.is_finite() or progress < 0:
        reason = "收货进度不可用，请核对权限或刷新"
    elif progress >= 100:
        reason = "订单已全部收货"
    elif not can_create:
        reason = "没有创建采购入库权限"
    return {"allowed": not bool(reason), "reason": reason}


def _receipt_payment_source(source_doctype):
    if source_doctype == "Purchase Order":
        frappe.throw(_i18n("采购订单仅支持入库；请从采购入库确认应付后付款，独立预付款请在原生财务付款单处理"))


def payment_target(source_doctype, source_name, purchase_invoice=None):
    _receipt_payment_source(source_doctype)
    source = _source(source_doctype, source_name)
    chain = get_purchase_chain(source_doctype, source_name, include_payments=False)
    if chain["incomplete_links"]:
        frappe.throw(LINK_WARNING)
    if source.docstatus != 1 or source.get("is_return"):
        frappe.throw(_i18n("来源必须为已提交且非退货的采购单据"))
    from .purchase_source_service import payment_guard
    for order_name in chain["orders"]:
        payment_guard(source if source_doctype == "Purchase Order" and source.name == order_name else _read("Purchase Order",order_name))
    if purchase_invoice:
        target = _current("Purchase Invoice", purchase_invoice)
        _require_fields("Purchase Invoice", PI_FIELDS)
        if purchase_invoice not in _invoice_names(source_doctype, source_name):
            frappe.throw(_i18n("应付单与当前采购单据没有明确关联"))
        if target.company != source.company or target.supplier != source.supplier:
            frappe.throw(_i18n("付款公司和供应商必须与来源一致"))
        if not invoice_payment_eligible(target):
            frappe.throw(_i18n("应付单未提交、为退货或已暂停付款"))
        return source, target, invoice_balance(target)
    frappe.throw(_i18n("尚无已提交应付，请先从采购入库确认应付"))


LINK_WARNING = "关联缺失或无权读取，请在原生单据核对；快捷付款/确认应付已禁用"
SOURCE_FIELDS = {"company", "supplier", "currency", "grand_total", "items", "status"}
RECEIPT_FIELDS = ["name", "supplier_name", "supplier", "posting_date", "status", "company", "currency", "grand_total", "docstatus", "is_return"]
RECEIPT_COLUMNS = {"name": "采购入库单号", "supplier_name": "供应商名称", "supplier": "供应商编码",
                   "posting_date": "入库日期", "status": "入库状态", "company": "公司", "currency": "入库币种",
                   "grand_total": "入库金额", "docstatus": "单据状态", "is_return": "是否退货",
                   "orders": "采购订单", "payment_state": "付款状态",
                   "shared_payable": "共享应付整单范围", "settlement_state": "已付/核销状态",
                   "settled": "已付/核销（各币种及整单范围）", "outstanding": "未付（各币种及整单范围）"}
PAYMENT_COLUMNS = {"name": "付款单", "posting_date": "付款日期", "supplier": "供应商", "company": "公司",
                   "payment_type": "付款类型", "amount": "金额", "currency": "付款币种", "bank_account": "记账账户",
                   "state": "付款状态", "remarks": "摘要", "references": "核销引用", "allocation_currency": "核销币种",
                   "vouchers": "会计凭证", "sync_issues": "凭证同步状态"}
PAYABLE_COLUMNS = {"name": "应付单号", "posting_date": "应付日期", "supplier": "供应商", "company": "公司",
                   "grand_total": "应付原币金额", "invoice_currency": "应付原币币种",
                   "total": "应付金额", "settled": "已付 / 核销", "outstanding": "未付余额", "currency": "应付账户币种",
                   "status": "应付状态", "orders": "采购订单", "receipts": "采购入库"}
BANK_WARNING = "银行或现金账户不可用或无权读取，请在原生单据核对"


def _query_fields(doctype):
    """Native stored parent fields; permission checks still apply to each requested field."""
    meta = frappe.get_meta(doctype)
    excluded = {"Table", "Table MultiSelect", "HTML", "Section Break", "Column Break", "Tab Break", "Button", "Heading", "Image", "Fold"}
    fields = set(meta.default_fields)
    fields.update(df.fieldname for df in meta.fields if df.fieldtype not in excluded and not df.get("is_virtual"))
    return fields & set(meta.get_valid_columns())


def _bank_account(name, company, currency, for_update=False):
    if for_update:
        from .purchase_repost_boundary import initialize
        initialize()
    """One bank guard for reads and drawer writes; framework account names stay private."""
    fields = {"company", "account_currency", "is_group", "account_type", "disabled"}
    denied = False
    with _quiet_link_errors():
        try:
            if for_update:
                doc = frappe.get_doc("Account", name, for_update=True)
                _check_read(doc)
                _require_fields("Account", fields)
                _read("Company", doc.company)
            else:
                doc = _read("Account", name, fields)
        except (frappe.DoesNotExistError, frappe.PermissionError):
            denied = True
    if denied:
        frappe.throw(BANK_WARNING, frappe.PermissionError)
    if doc.company != company or doc.is_group or doc.disabled or doc.account_type not in ("Bank", "Cash") or doc.account_currency != currency:
        frappe.throw(BANK_WARNING)
    return doc


def _normalize_filter(doctype, field, operator, value):
    from frappe.utils.data import get_filter
    normalized = get_filter(doctype, [doctype, field, operator, value])
    if normalized.doctype != doctype or normalized.fieldname != field:
        frappe.throw(_i18n("跨单据或明细筛选请在原生高级列表处理"))
    return [doctype, field, normalized.operator.lower(), normalized.value]


def _native_filters(doctype, value, allowed):
    value = json.loads(value) if isinstance(value, str) else value or []
    if isinstance(value, dict):
        value = [[field, *(condition if isinstance(condition, (list, tuple)) else ["=", condition])]
                 for field, condition in value.items()]
    if not isinstance(value, list):
        frappe.throw(_i18n("原生筛选条件必须为列表或对象"))
    result = []
    for entry in value:
        if not isinstance(entry, (list, tuple)) or len(entry) not in (3, 4):
            frappe.throw(_i18n("原生筛选条件格式无效"))
        if len(entry) == 4 and entry[0] != doctype:
            frappe.throw(_i18n("不支持跨单据筛选"))
        field, operator, operand = entry[-3:]
        if field not in allowed:
            frappe.throw(_i18n("不支持此父单据字段；明细筛选请在原生高级列表处理"))
        _require_fields(doctype, {field})
        result.append(_normalize_filter(doctype, field, str(operator).lower(), operand))
    return result


def _order_by(doctype, value, allowed):
    clauses = []
    for part in str(value or "posting_date desc").split(","):
        match = re.fullmatch(r"\s*(?:(?:`([^`]+)`|([a-z_][a-z0-9_ ]*))\s*\.\s*)?(?:`([a-z_][a-z0-9_]*)`|([a-z_][a-z0-9_]*))\s*(asc|desc)?\s*", part, re.I)
        if not match:
            frappe.throw(_i18n("排序字段无效"))
        table, field = match[1] or match[2], match[3] or match[4]
        if (table and table != "tab" + doctype) or field not in allowed:
            frappe.throw(_i18n("排序字段无效"))
        _require_fields(doctype, {field})
        clauses.append(field + " " + (match[5] or "asc").lower())
    if not any(clause.startswith("name ") for clause in clauses):
        clauses.append("name desc" if clauses[0] == "posting_date desc" else "name asc")
    return ", ".join(clauses)


def _pagination(start, page_length):
    try:
        start, page_length = int(start), int(page_length)
    except (TypeError, ValueError):
        frappe.throw(_i18n("分页参数无效"))
    if start < 0 or not 1 <= page_length <= 2500:
        frappe.throw(_i18n("分页参数无效"))
    return start, page_length


def _export(doctype, rows, columns, allowed):
    _require_export_permission(doctype, ({"name": row["name"], "owner": _read_doc(doctype, row["name"]).get("owner")}
                                        for row in rows))
    columns = json.loads(columns) if isinstance(columns, str) else columns or list(allowed)
    if not isinstance(columns, list) or not columns or len(set(columns)) != len(columns) or any(column not in allowed for column in columns):
        frappe.throw(_i18n("导出列无效"))
    columns = list(columns)
    currencies = ([("invoice_currency", {"grand_total"}), ("currency", {"total", "settled", "outstanding"})]
                  if doctype == "Purchase Invoice" else [("currency", {"grand_total", "amount"})])
    for currency, amounts in currencies:
        if amounts.intersection(columns) and currency not in columns:
            columns.append(currency)
    if doctype == "Payment Entry" and "allocation_currency" not in columns:
        columns.append("allocation_currency")
    for row in rows:
        _check_read(_read_doc(doctype, row["name"]))
    # Prevent spreadsheet formula interpretation of source text.
    def value(row, column):
        cell = row.get(column)
        if column == "posting_date":
            cell = getdate(cell) if cell else None
        elif column == "allocation_currency":
            cell = ", ".join(sorted({ref["currency"] for ref in row["references"] if ref.get("currency")}))
        elif doctype == "Purchase Receipt" and column in ("settled", "outstanding"):
            cell = [{"currency": balance["currency"], column: balance[column],
                     "scope_label": "共享应付整单余额" if row.get("shared_payable") else "关联应付余额"}
                    for balance in row.get("balances", [])]
        if isinstance(cell, (list, dict)):
            cell = json.dumps(cell, ensure_ascii=False, default=str)
        if isinstance(cell, str):
            from frappe.utils.xlsxutils import handle_html
            cell = handle_html(cell)
            if cell.startswith(("=", "+", "-", "@")):
                cell = "'" + cell
        return cell
    from frappe.utils.xlsxutils import make_xlsx
    column_styles = {index: (1,) if column == "posting_date" else (0,)
                     for index, column in enumerate(columns) if column in {"posting_date", "grand_total", "amount", "total", "settled", "outstanding"}}
    styles = {"styles": [{"num_format": "#,##0.00"}, {"num_format": "yyyy-mm-dd"}],
              "column_styles": column_styles} if column_styles else {}
    workbook = make_xlsx([[allowed[column] for column in columns]] + [[value(row, column) for column in columns] for row in rows], "采购记录", styles=styles)
    frappe.response["filename"] = doctype.replace(" ", "_") + ".xlsx"
    frappe.response["filecontent"] = workbook.getvalue()
    frappe.response["type"] = "binary"
    return None


@contextmanager
def _quiet_link_errors():
    """Caught Frappe throws must not enqueue private target names in _server_messages."""
    previous = frappe.flags.mute_messages
    frappe.flags.mute_messages = True
    try:
        yield
    finally:
        frappe.flags.mute_messages = previous


def _related(doctype, name, warnings, fields=(), company=None, supplier=None):
    """Resolve optional links through normal permissions; never disclose hidden target names."""
    with _quiet_link_errors():
        try:
            doc = _read(doctype, name, fields)
            if (company and doc.company != company) or (supplier and doc.supplier != supplier):
                raise frappe.PermissionError
            return doc
        except (frappe.DoesNotExistError, frappe.PermissionError):
            if LINK_WARNING not in warnings:
                warnings.append(LINK_WARNING)
            return None


def _source_links(doc, doctype, field, warnings):
    names = sorted({item.get(field) for item in doc.items if item.get(field)})
    return [name for name in names if _related(doctype, name, warnings, SOURCE_FIELDS,
                                              doc.company, doc.supplier)]


def _source(doctype, name):
    if doctype not in SOURCES:
        frappe.throw(_i18n("仅支持采购订单和采购入库"))
    return _read(doctype, name, SOURCE_FIELDS)


def _invoice_names(doctype, name, warnings=None, source=None):
    field = "purchase_receipt" if doctype == "Purchase Receipt" else "purchase_order"
    _require_fields("Purchase Invoice", {"items"})
    _require_fields("Purchase Invoice Item", {field}, "Purchase Invoice")
    # Discover parent candidates only through the readable source link; callers must
    # resolve each parent with _read/_related before exposing names or balances.
    # Joining to the parent would silently hide orphan children and permit duplicate PI creation.
    names = frappe.get_all("Purchase Invoice Item", filters={field: name},
                           distinct=True, limit_page_length=0, pluck="parent")
    if doctype == "Purchase Receipt":
        source = source or _source(doctype, name)
        warnings = warnings if warnings is not None else []
        _require_fields("Purchase Receipt Item", {"purchase_order"}, "Purchase Receipt")
        _require_fields("Purchase Invoice Item", {"purchase_order"}, "Purchase Invoice")
        orders = _source_links(source, "Purchase Order", "purchase_order", warnings)
        if orders:
            names += frappe.get_all("Purchase Invoice Item", filters={"purchase_order": ["in", orders]},
                                    distinct=True, limit_page_length=0, pluck="parent")
    return list(dict.fromkeys(names))


def invoice_balance(doc):
    """Outstanding is ERPNext's maintained party-ledger balance, not a sum of PE rows."""
    currency = doc.party_account_currency or doc.currency
    rounded = not doc.disable_rounded_total and bool(doc.rounded_total)
    total_field = "rounded_total" if rounded else "grand_total"
    if currency == doc.currency:
        total = amount(doc.get(total_field))
    elif currency == frappe.get_cached_value("Company", doc.company, "default_currency"):
        total = amount(doc.get("base_" + total_field))
    else:
        frappe.throw(_i18n("应付账户币种无法安全换算，请打开原生应付单核对"))
    outstanding = amount(doc.outstanding_amount)
    return {"currency": currency, "total": float(total), "settled": float(total - outstanding),
            "outstanding": float(outstanding)}


def _invoice_row(doc, source_type, source_name):
    _require_fields("Purchase Invoice Item", {"purchase_order", "purchase_receipt"}, "Purchase Invoice")
    field = "purchase_receipt" if source_type == "Purchase Receipt" else "purchase_order"
    shared = any(row.get(field) != source_name for row in doc.items)
    balance = invoice_balance(doc) if doc.docstatus == 1 else {}
    blocked = bool(doc.get("on_hold")) if doc.docstatus == 1 else False
    return {"name": doc.name, "docstatus": doc.docstatus, "is_return": bool(doc.is_return),
            "shared": shared, "scope_label": "共享应付整单余额" if shared else "关联应付余额",
            "can_pay": bool(invoice_payment_eligible(doc) and balance.get("outstanding", 0) > 0),
            "blocked": bool(blocked), **balance}


def summarize(invoices):
    groups = {}
    for row in invoices:
        if row["docstatus"] != 1 or row["is_return"]:
            continue
        currency = row["currency"]
        target = groups.setdefault(currency, {"currency": currency, "total": Decimal(0), "settled": Decimal(0), "outstanding": Decimal(0)})
        for key in ("total", "settled", "outstanding"):
            target[key] += amount(row[key])
    return [{key: float(value) if isinstance(value, Decimal) else value for key, value in row.items()} for row in groups.values()]


@frappe.whitelist()
def get_purchase_payables(company=None, supplier=None, search=None, from_date=None, to_date=None,
                          start=0, page_length=100, export_format=None, columns=None, order_by=None):
    if not frappe.has_permission("Payment Entry", "read"):
        frappe.throw(_i18n("采购人员请在采购订单查看订单进度；应付与付款办理由财务处理"), frappe.PermissionError)
    token = _record_reader.set(_RecordReader())
    try:
        return _get_purchase_payables(company, supplier, search, from_date, to_date, start, page_length, export_format, columns, order_by)
    finally:
        _record_reader.reset(token)


def _get_purchase_payables(company, supplier, search, from_date, to_date, start, page_length, export_format=None, columns=None, order_by=None):
    """Native PI documents with readable procurement sources, once per invoice.

    Totals/balances are whole native invoices, including mixed expense lines.
    No amount is reallocated or reclassified as a procurement cost.
    """
    fields = PI_FIELDS | {"name", "posting_date", "status"}
    _require_fields("Purchase Invoice", fields)
    _require_fields("Purchase Invoice Item", {"purchase_order", "purchase_receipt"}, "Purchase Invoice")
    start, page_length = _pagination(start, page_length)
    filters = {key: value for key, value in (("company", company), ("supplier", supplier)) if value}
    if from_date or to_date:
        filters["posting_date"] = ["between", [from_date or "1900-01-01", to_date or "2999-12-31"]]
    candidates = frappe.get_list("Purchase Invoice", filters=filters,
        or_filters=[["Purchase Invoice Item", field, "is", "set"]
                    for field in ("purchase_order", "purchase_receipt")],
        fields=["name"], order_by=_order_by("Purchase Invoice", order_by, fields), limit_page_length=0)
    reader = _record_reader.get()
    reader.preload("Purchase Invoice", [row.name for row in candidates])
    invoices = [doc for (dt, _), doc in reader.docs.items() if dt == "Purchase Invoice" and doc]
    reader.preload("Company", {doc.company for doc in invoices})
    for doctype, field in (("Purchase Order", "purchase_order"), ("Purchase Receipt", "purchase_receipt")):
        reader.preload(doctype, {item.get(field) for doc in invoices for item in doc.items if item.get(field)})
    rows, seen = [], set()
    for candidate in candidates:
        if candidate.name in seen:
            continue
        seen.add(candidate.name)
        warnings = []
        with _quiet_link_errors():
            try:
                doc = _read("Purchase Invoice", candidate.name, fields)
                if search and str(search).casefold() not in f"{doc.name} {doc.supplier}".casefold():
                    continue
                orders = _source_links(doc, "Purchase Order", "purchase_order", warnings)
                receipts = _source_links(doc, "Purchase Receipt", "purchase_receipt", warnings)
            except (frappe.PermissionError, frappe.DoesNotExistError):
                continue
        if not orders and not receipts:
            continue
        source_type, source_name = ("Purchase Receipt", receipts[0]) if receipts else ("Purchase Order", orders[0])
        invoice = _invoice_row(doc, source_type, source_name)
        rows.append({**invoice, "posting_date": doc.posting_date, "supplier": doc.supplier,
                     "company": doc.company, "invoice_currency": doc.currency, "grand_total": doc.grand_total,
                     "status": doc.status, "orders": orders, "receipts": receipts, "warnings": warnings,
                     "source_type": source_type, "source_name": source_name,
                     "can_pay": bool(invoice["can_pay"] and not warnings
                                     and frappe.has_permission("Payment Entry", "read")
                                     and frappe.has_permission("Payment Entry", "create"))})
    if export_format:
        if export_format != "xlsx":
            frappe.throw(_i18n("导出格式无效"))
        return _export("Purchase Invoice", rows, columns, PAYABLE_COLUMNS)
    return {"rows": rows[start:start + page_length], "total_count": len(rows), "totals": summarize(rows),
            "notice": "仅显示关联可读采购订单或采购入库的原生应付单。金额与余额均为整张应付单，可能包含其他费用；不是采购成本或某张入库的分摊金额。草稿、退货、取消分别显示，草稿和取消不计应付余额。"}


def _vouchers(payment_names):
    if not frappe.db.exists("DocType", "China Accounting Voucher") or not frappe.has_permission("China Accounting Voucher", "read"):
        return []
    fields = {"source_doctype", "source_name", "source_event", "status", "statutory_number", "company"}
    _require_fields("China Accounting Voucher", fields)
    result = []
    for row in frappe.get_list("China Accounting Voucher", filters={"source_doctype": "Payment Entry", "source_name": ["in", payment_names]},
            fields=["name", "source_name", "statutory_number", "source_event", "status", "docstatus", "company"], limit_page_length=0):
        if _related("China Accounting Voucher", row.name, [], fields):
            result.append(row)
    return result


def _decorate_payments(rows):
    from deeplinkerp_branding.services.purchase_document_actions import _payment_editable, _workflow_actions
    by_name = {row["name"]: row for row in rows}
    for row in rows:
        doc = _read_doc("Payment Entry", row["name"])
        row["sync_issues"] = []
        row["can_edit"] = False
        row["allowed_actions"] = []
        try:
            with _quiet_link_errors():
                _require_fields("Payment Entry", {"deductions", "unallocated_amount"})
                simple = (not row["warnings"] and doc.payment_type == "Pay" and doc.party_type == "Supplier"
                          and not doc.get("deductions") and not doc.unallocated_amount
                          and doc.paid_from_account_currency == doc.paid_to_account_currency
                          and bool(doc.references)
                          and ({ref.reference_doctype for ref in doc.references} == {"Purchase Invoice"}
                               or ({ref.reference_doctype for ref in doc.references} == {"Purchase Order"}
                                   and len({ref.reference_name for ref in doc.references}) == 1)))
                row["can_edit"] = bool(simple and doc.docstatus == 0 and _payment_editable(doc))
                row["allowed_actions"] = _workflow_actions(doc) if simple else []
        except frappe.PermissionError:
            pass
    doctype = "China Voucher Sync Issue"
    if not frappe.db.exists("DocType", doctype) or not frappe.has_permission(doctype, "read"):
        return
    fields = {"company", "source_doctype", "source_name", "status", "posting_date"}
    try:
        with _quiet_link_errors():
            _require_fields(doctype, fields)
            issues = frappe.get_list(doctype, filters={"source_doctype": "Payment Entry", "source_name": ["in", list(by_name)], "status": "Pending"},
                                     fields=["name", *sorted(fields)], limit_page_length=0)
            for issue in issues:
                payment = by_name[issue.source_name]
                if issue.company != payment["company"] or not _related(doctype, issue.name, [], fields, payment["company"]):
                    continue
                # Status only: do not leak last_error or unavailable cancellation-voucher names.
                payment["sync_issues"].append({"name": issue.name, "status": issue.status, "posting_date": issue.posting_date})
    except frappe.PermissionError:
        pass


def _procurement_references(payment, warnings):
    _require_fields("Payment Entry Reference", {"reference_doctype", "reference_name", "allocated_amount"}, "Payment Entry")
    refs = []
    for row in payment.references:
        if row.reference_doctype == "Purchase Order":
            order = _related("Purchase Order", row.reference_name, warnings, SOURCE_FIELDS, payment.company, payment.party)
            if order:
                refs.append({"doctype": "Purchase Order", "name": order.name, "allocated": row.allocated_amount, "currency": payment.paid_to_account_currency if payment.payment_type == "Pay" else payment.paid_from_account_currency, "orders": [order.name], "receipts": []})
        elif row.reference_doctype == "Purchase Invoice":
            invoice = _related("Purchase Invoice", row.reference_name, warnings, PI_FIELDS, payment.company, payment.party)
            if not invoice:
                continue
            _require_fields("Purchase Invoice Item", {"purchase_order", "purchase_receipt"}, "Purchase Invoice")
            orders = _source_links(invoice, "Purchase Order", "purchase_order", warnings)
            receipts = _source_links(invoice, "Purchase Receipt", "purchase_receipt", warnings)
            if any(item.purchase_order or item.purchase_receipt for item in invoice.items):
                refs.append({"doctype": "Purchase Invoice", "name": invoice.name, "allocated": row.allocated_amount, "currency": payment.paid_to_account_currency if payment.payment_type == "Pay" else payment.paid_from_account_currency, "orders": orders, "receipts": receipts})
    return refs


def _payment_row(doc):
    _require_fields("Payment Entry", {"company", "party", "party_type", "payment_type", "references", "posting_date", "paid_amount", "paid_from_account_currency", "paid_from", "received_amount", "paid_to_account_currency", "paid_to", "remarks"})
    _check_read(doc)
    _check_read(_read_doc("Company", doc.company))
    warnings = []
    refs = _procurement_references(doc, warnings)
    if not refs:
        return None
    bank_account = doc.paid_to if doc.payment_type == "Receive" else doc.paid_from
    currency = doc.paid_to_account_currency if doc.payment_type == "Receive" else doc.paid_from_account_currency
    try:
        with _quiet_link_errors():
            _bank_account(bank_account, doc.company, currency)
    except (frappe.PermissionError, frappe.ValidationError):
        bank_account = None
        warnings.append(BANK_WARNING)
    return {"name": doc.name, "company": doc.company, "supplier": doc.party, "posting_date": doc.posting_date,
            "docstatus": doc.docstatus, "payment_type": doc.payment_type, "amount": doc.received_amount if doc.payment_type == "Receive" else doc.paid_amount,
            "currency": currency, "bank_account": bank_account, "remarks": doc.remarks,
            "references": refs, "vouchers": [], "warnings": warnings,
            "state": {0: "草稿 · 未计已付", 1: "已提交", 2: "已取消 · 未计已付"}[doc.docstatus]}


@frappe.whitelist()
def get_payment_records(company=None, supplier=None, purchase_order=None, purchase_receipt=None, search=None, start=0, page_length=100,
                        from_date=None, to_date=None, status=None, native_filters=None, or_filters=None, order_by=None, export_format=None, columns=None):
    token = _record_reader.set(_RecordReader())
    try:
        return _payment_records(company, supplier, purchase_order, purchase_receipt, search, start, page_length,
                                from_date=from_date, to_date=to_date, status=status, native_filters=native_filters,
                                or_filters=or_filters, order_by=order_by, export_format=export_format, columns=columns)
    finally:
        _record_reader.reset(token)


def _payment_records(company, supplier, purchase_order, purchase_receipt, search, start, page_length,
                     from_date=None, to_date=None, status=None, native_filters=None, or_filters=None, order_by=None, export_format=None, columns=None):
    if purchase_order:
        _source("Purchase Order", purchase_order)
    receipt_orders = set()
    if purchase_receipt:
        receipt = _source("Purchase Receipt", purchase_receipt)
        _require_fields("Purchase Receipt Item", {"purchase_order"}, "Purchase Receipt")
        receipt_orders = set(_source_links(receipt, "Purchase Order", "purchase_order", []))
    start, page_length = _pagination(start, page_length)
    _require_fields("Payment Entry", {"party_type", "references"})
    filters = {"party_type": "Supplier", "payment_type": ["in", ["Pay", "Receive"]]}
    for field, value in (("company", company), ("party", supplier)):
        if value:
            filters[field] = value
    if from_date or to_date:
        filters["posting_date"] = ["between", [from_date or "1900-01-01", to_date or "2999-12-31"]]
    if status is not None and status != "":
        state = {"Draft": 0, "Submitted": 1, "Cancelled": 2}.get(str(status), status)
        try:
            state = int(state)
        except (TypeError, ValueError):
            frappe.throw(_i18n("付款状态无效"))
        if state not in (0, 1, 2):
            frappe.throw(_i18n("付款状态无效"))
        filters["docstatus"] = state
    allowed = _query_fields("Payment Entry")
    query = _native_filters("Payment Entry", filters, allowed | {"party_type"}) + _native_filters("Payment Entry", native_filters, allowed)
    ors = _native_filters("Payment Entry", or_filters, allowed)
    sorting = _order_by("Payment Entry", order_by, allowed)
    try:
        with _quiet_link_errors():
            _require_fields("Payment Entry", {"party"})
    except frappe.PermissionError:
        candidates = []
    else:
        candidates = frappe.get_list("Payment Entry", filters=query, or_filters=ors, fields=["name", "party"], order_by=sorting, limit_page_length=0)
    # Preserve Python casefold and literal substring semantics (SQL LIKE/collations differ).
    names = [row.name for row in candidates if not search or str(search).casefold() in (row.name + " " + (row.party or "")).casefold()]
    reader = _record_reader.get()
    reader.preload("Payment Entry", names)
    payments = [reader.doc("Payment Entry", name) for name in names if reader.docs["Payment Entry", name]]
    reader.preload("Company", {doc.company for doc in payments})
    for doctype in ("Purchase Order", "Purchase Invoice"):
        reader.preload(doctype, {ref.reference_name for doc in payments for ref in doc.references if ref.reference_doctype == doctype and ref.reference_name})
    invoices = [doc for (dt, name), doc in reader.docs.items() if dt == "Purchase Invoice" and doc]
    for doctype, field in (("Purchase Order", "purchase_order"), ("Purchase Receipt", "purchase_receipt")):
        reader.preload(doctype, {item.get(field) for doc in invoices for item in doc.items if item.get(field)})
    rows = []
    for name in names:
        try:
            with _quiet_link_errors():
                row = _payment_row(reader.doc("Payment Entry", name))
        except (frappe.DoesNotExistError, frappe.PermissionError):
            continue
        if not row:
            continue
        refs = row["references"]
        if purchase_order and not any(purchase_order in ref["orders"] for ref in refs):
            continue
        if purchase_receipt and not any(purchase_receipt in ref["receipts"] or receipt_orders.intersection(ref["orders"]) for ref in refs):
            continue
        rows.append(row)
    page = rows if export_format else rows[start:start + page_length]
    if page:
        try:
            with _quiet_link_errors():
                vouchers = _vouchers([row["name"] for row in page])
            by_name = {row["name"]: row for row in page}
            for voucher in vouchers:
                parent = voucher.pop("source_name")
                if voucher.pop("company") == by_name[parent]["company"]:
                    by_name[parent]["vouchers"].append(voucher)
        except frappe.PermissionError:
            pass
        _decorate_payments(page)
    if export_format:
        if export_format != "xlsx":
            frappe.throw(_i18n("仅支持 XLSX 导出"))
        return _export("Payment Entry", rows, columns, PAYMENT_COLUMNS)
    groups = {}
    for row in rows:
        group = (row["payment_type"], row["docstatus"], row["currency"])
        groups[group] = groups.get(group, Decimal(0)) + amount(row["amount"])
    totals = [{"payment_type": payment_type, "docstatus": status, "currency": currency, "amount": float(value)}
              for (payment_type, status, currency), value in sorted(groups.items())]
    # Whole bank-document amounts, never represented as procurement settlement.
    return {"rows": page, "total_count": len(rows),
            "totals": totals, "totals_label": "整张付款单银行币种金额（按付款类型、单据状态、币种分组）",
            "notice": "关联缺失或无权读取的单据不会作为可用链接；请在原生单据核对。仅显示有权查看的订单预付款或关联采购应付付款；草稿、取消不计已付。金额为整张付款单银行币种金额，核销见引用。关联订单预付款尚未核销时不计本入库已付。"}


def _order_progress(names, warnings):
    rows=[]
    for name in names:
        try:
            order=_related("Purchase Order", name, warnings, SOURCE_FIELDS | {"docstatus"})
            if not order:
                continue
            with _quiet_link_errors():
                _require_fields("Purchase Order Item", {"qty","received_qty","uom","rate"}, "Purchase Order")
            units={}
            pending=Decimal(0)
            for item in order.items:
                qty=max(Decimal(0),amount(item.qty)); received=max(Decimal(0),amount(item.received_qty))
                left=max(Decimal(0),qty-received);pending+=left*amount(item.rate)
                unit=units.setdefault(item.uom,{"uom":item.uom,"ordered":Decimal(0),"received":Decimal(0),"pending":Decimal(0)})
                unit["ordered"]+=qty;unit["received"]+=received;unit["pending"]+=left
            rows.append({"name":name,"docstatus":order.docstatus,"status":order.status,"currency":order.currency,"grand_total":order.grand_total,
                         "pending_net_amount":float(pending),"units":[{k:float(v) if isinstance(v,Decimal) else v for k,v in u.items()} for u in units.values()]})
        except frappe.PermissionError:
            if LINK_WARNING not in warnings:
                warnings.append(LINK_WARNING)
    return rows


@frappe.whitelist()
def get_purchase_chain(source_doctype, source_name, include_payments=True):
    doc = _source(source_doctype, source_name)
    _require_fields(source_doctype + " Item", {"purchase_order"} if source_doctype == "Purchase Receipt" else {"qty", "received_qty", "amount"}, source_doctype)
    invoices = []
    warnings = []
    if frappe.has_permission("Purchase Invoice", "read"):
        for name in _invoice_names(source_doctype, source_name, warnings=warnings, source=doc):
            invoice = _related("Purchase Invoice", name, warnings, PI_FIELDS, doc.company, doc.supplier)
            if not invoice:
                continue
            _require_fields("Purchase Invoice Item", {"purchase_order", "purchase_receipt"}, "Purchase Invoice")
            _source_links(invoice, "Purchase Order", "purchase_order", warnings)
            _source_links(invoice, "Purchase Receipt", "purchase_receipt", warnings)
            if invoice.company == doc.company and invoice.supplier == doc.supplier:
                invoices.append(_invoice_row(invoice, source_doctype, source_name))
    else:
        warnings.append("无采购应付读取权限，余额不可见")
    if any(row["shared"] for row in invoices):
        warnings.append("关联应付包含其他订单/入库；金额为共享应付整单余额，不分摊冒充本入库已付")
    payments = []
    if include_payments in (True, 1, "1", "true") and frappe.has_permission("Payment Entry", "read"):
        payments = get_payment_records(**{"purchase_receipt" if source_doctype == "Purchase Receipt" else "purchase_order": doc.name}, page_length=100)["rows"]
    can_create = bool(doc.docstatus == 1 and not doc.get("is_return") and frappe.has_permission("Payment Entry", "create"))
    reason = "" if can_create else "来源未提交、已退货，或没有创建付款权限"
    eligible = [row for row in invoices if row["can_pay"]]
    if not eligible:
        reason = reason or ("有关联应付草稿，请核对并提交；草稿不计应付" if any(i["docstatus"] == 0 for i in invoices) else "关联应付已结清、暂停或取消，请打开关联单据核对" if invoices else "尚无可付款的已提交应付单，请先确认应付")
    orders = []
    if source_doctype == "Purchase Receipt":
        orders = _source_links(doc, "Purchase Order", "purchase_order", warnings)
    else:
        orders = [doc.name]
    execution_reason = payment_execution_reason = ""
    for order_name in orders:
        order = doc if source_doctype == "Purchase Order" else _related(
            "Purchase Order", order_name, warnings, SOURCE_FIELDS, doc.company, doc.supplier)
        if not order:
            continue
        order_reason = order_execution_reason(order)
        execution_reason = execution_reason or order_reason
        payment_execution_reason = payment_execution_reason or (
            order_execution_reason(order, allow_completed_invoice_payment=True)
            if eligible and order.status == "Completed" else order_reason)
        if execution_reason and payment_execution_reason:
            break
    if payment_execution_reason:
        can_create = False
        reason = payment_execution_reason
    progress = _order_progress(orders, warnings)
    incomplete = LINK_WARNING in warnings
    if incomplete:
        reason = LINK_WARNING
        for row in invoices:
            row["can_pay"] = False
    drafts = [{"name": row["name"], "docstatus": 0} for row in invoices if row["docstatus"] == 0]
    can_invoice = bool(not incomplete and doc.docstatus == 1
                       and not doc.get("is_return") and not drafts
                       and frappe.has_permission("Purchase Invoice", "read")
                       and frappe.has_permission("Purchase Invoice", "create"))
    invoice_reason = (LINK_WARNING if incomplete else "已有应付草稿，请选择继续编辑" if drafts
                      else "来源未提交、已退货或没有创建应付权限" if not can_invoice else "")
    if execution_reason:
        can_invoice = False
        invoice_reason = execution_reason
    if can_invoice:
        from deeplinkerp_branding.services.purchase_document_actions import _invoice_capability
        try:
            with _quiet_link_errors():
                can_invoice, invoice_reason = _invoice_capability(doc)
        except (frappe.ValidationError, frappe.PermissionError):
            can_invoice = False
            invoice_reason = "原生开票条件或字段权限不足，请打开原生单据核对"
    advance = None
    advance_reason = ""
    can_prepay = False
    if source_doctype == "Purchase Order":
        can_create = can_invoice = False
    from .purchase_reversal_progress import projection
    reversal = projection(doc)
    if reversal and reversal["stage"] != "completed":
        can_create = can_invoice = can_prepay = False
        drafts = []
        reason = invoice_reason = advance_reason = "采购冲销未完成，相关操作暂缓办理"
    return {"source_doctype": source_doctype, "name": doc.name, "company": doc.company, "supplier": doc.supplier,
            "reversal": reversal,
            "receipt_eligibility": receipt_eligibility(doc, can_create=bool(frappe.has_permission("Purchase Receipt", "create")), reversal_pending=bool(reversal and reversal["stage"] != "completed")) if source_doctype == "Purchase Order" else None,
            "currency": doc.currency, "grand_total": doc.grand_total, "status": doc.status,
            "docstatus": doc.docstatus, "orders": orders, "order_progress": progress, "invoices": invoices, "balances": [] if incomplete else summarize(invoices), "incomplete_links": incomplete,
            "can_create_invoice": can_invoice, "draft_invoices": drafts,
            "invoice_reason": invoice_reason, "source_modified": doc.get("modified"),
            "draft_orders": [row["name"] for row in progress if row["docstatus"] == 0],
            "payments": payments, "warnings": warnings, "can_create": can_create and bool(eligible) and not incomplete, "reason": reason,
            "can_prepay": can_prepay, "advance_balance": advance, "advance_reason": advance_reason,
            "settlement_label": "已付/核销（含预付款抵扣、贷项等）"}


@frappe.whitelist()
def get_receipt_list(filters=None, start=0, page_length=100, native_filters=None, or_filters=None, order_by=None,
                     search=None, export_format=None, columns=None, **unused):
    token = _record_reader.set(_RecordReader())
    try:
        return _receipt_list(filters, start, page_length, native_filters, or_filters, order_by, search, export_format, columns)
    finally:
        _record_reader.reset(token)


def _receipt_list(filters, start, page_length, native_filters, or_filters, order_by, search, export_format, columns):
    filters = json.loads(filters) if isinstance(filters, str) else filters or {}
    if not isinstance(filters, dict):
        frappe.throw(_i18n("筛选条件必须为对象"))
    query = {}
    for field in ("company", "supplier", "status"):
        if filters.get(field):
            query[field] = filters[field]
    if filters.get("from_date") or filters.get("to_date"):
        query["posting_date"] = ["between", [filters.get("from_date") or "1900-01-01", filters.get("to_date") or "2999-12-31"]]
    fields = [*RECEIPT_FIELDS, "modified"]  # Version metadata is not an export column.
    _require_fields("Purchase Receipt", fields)
    start, page_length = _pagination(start, page_length)
    allowed = _query_fields("Purchase Receipt")
    query = _native_filters("Purchase Receipt", query, allowed) + _native_filters("Purchase Receipt", native_filters, allowed)
    ors = _native_filters("Purchase Receipt", or_filters, allowed)
    sorting = _order_by("Purchase Receipt", order_by, allowed)
    candidates = frappe.get_list("Purchase Receipt", filters=query, or_filters=ors, fields=fields, order_by=sorting, limit_page_length=0)
    keyword = str(search or filters.get("search") or "").casefold()
    candidates = [row for row in candidates if not keyword or keyword in " ".join(str(row.get(field) or "") for field in ("name", "supplier", "supplier_name")).casefold()]
    reader = _record_reader.get()
    reader.preload("Purchase Receipt", [row.name for row in candidates])
    reader.preload("Company", {row.company for row in candidates})
    visible = []
    for row in candidates:
        try:
            with _quiet_link_errors():
                _read("Purchase Receipt", row.name, fields)
            visible.append(row)
        except (frappe.PermissionError, frappe.DoesNotExistError):
            continue
    totals = {}
    for row in visible:
        totals[row.currency] = totals.get(row.currency, Decimal(0)) + amount(row.grand_total)
    rows = visible if export_format else visible[start:start + page_length]
    for row in rows:
        try:
            with _quiet_link_errors():
                chain = get_purchase_chain("Purchase Receipt", row.name, include_payments=False)
            row.update({key: chain[key] for key in ("orders", "balances", "can_create", "reason", "warnings", "incomplete_links")})
            row["can_create_invoice"] = chain["can_create_invoice"]
            row["draft_invoices"] = chain["draft_invoices"]
            row["draft_orders"] = chain["draft_orders"]
            row["reversal"] = chain.get("reversal")
            row["shared_payable"] = bool(not chain["incomplete_links"] and any(invoice["shared"] for invoice in chain["invoices"]))
            row["settlement_state"] = "余额不可见" if chain["incomplete_links"] or (chain["warnings"] and not chain["balances"]) else "未形成应付"
            row["payment_state"] = ("关联缺失或无权读取" if chain["incomplete_links"] else "共享应付" if any(i["shared"] for i in chain["invoices"]) else "余额不可见" if chain["warnings"] else "未形成应付")
            if chain["balances"] and not chain["incomplete_links"]:
                balances = chain["balances"]
                row["settlement_state"] = "已付清" if all(b["outstanding"] <= 0 for b in balances) else "部分付款/核销" if any(b["settled"] > 0 for b in balances) else "未付款"
                if not chain["warnings"]:
                    row["payment_state"] = row["settlement_state"]
        except (frappe.PermissionError, frappe.DoesNotExistError):
            row.update(balances=[], orders=[], can_create=False, can_create_invoice=False, draft_invoices=[], shared_payable=False, settlement_state="余额不可见", reason=LINK_WARNING, payment_state="关联缺失或无权读取", warnings=[LINK_WARNING], incomplete_links=True)
    if export_format:
        if export_format != "xlsx":
            frappe.throw(_i18n("仅支持 XLSX 导出"))
        return _export("Purchase Receipt", rows, columns, RECEIPT_COLUMNS)
    return {"rows": rows, "total_count": len(visible), "totals": [{"currency": currency, "grand_total": float(value)} for currency, value in sorted(totals.items())]}


@frappe.whitelist(methods=["POST"])
@procurement_entry
def create_payment_draft(source_doctype, source_name, purchase_invoice=None, amount_to_pay=None, bank_account=None, posting_date=None, remarks=None, request_id=None, reference_no=None, sources=None, allocations=None):
    """Save exactly a native draft. Never bypass create/read/write or approval permissions."""
    _receipt_payment_source(source_doctype)
    source = _source(source_doctype, source_name)
    if get_purchase_chain(source_doctype, source_name, include_payments=False)["incomplete_links"]:
        frappe.throw(LINK_WARNING)
    if source.docstatus != 1 or source.get("is_return"):
        frappe.throw(_i18n("来源必须为已提交且非退货的采购单据"))
    if not frappe.has_permission("Payment Entry", "create"):
        frappe.throw(_i18n("没有创建付款单权限"), frappe.PermissionError)
    if not re.fullmatch(r"[a-zA-Z0-9-]{16,80}", str(request_id or "")):
        frappe.throw(_i18n("缺少有效请求标识，请刷新付款抽屉"))
    allocation_rows = _payment_allocations(allocations) if sources is not None else None
    try:
        value = sum((amount(row["amount"]) for row in allocation_rows), Decimal(0)) if allocation_rows is not None else amount(amount_to_pay)
    except ValueError as exc:
        frappe.throw(str(exc))
    if value <= 0:
        frappe.throw(_i18n("本次金额必须大于0"))
    payload = [source_doctype, source_name, purchase_invoice, str(value), bank_account, str(posting_date or nowdate()), remarks or "", reference_no or ""]
    if sources is not None:
        from .purchase_document_actions import _batch_sources
        sources = _batch_sources(sources)
        payload += [sources, allocation_rows]
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()

    def operation():
        if sources is None:
            source, target, balance = payment_target(source_doctype, source_name, purchase_invoice)
            targets = [(target, balance, value)]
        else:
            context = _payment_batch_targets(source_doctype, sources, check_versions=True)
            source = context["sources"][0]
            by_name = {doc.name: (doc, balance) for doc, balance in context["targets"]}
            if any(row["name"] not in by_name for row in allocation_rows):
                frappe.throw(_i18n("应付单不属于所选真实采购链或当前不可付款"))
            targets = [(by_name[row["name"]][0], by_name[row["name"]][1], amount(row["amount"])) for row in allocation_rows]
        for _target, balance, allocated in targets:
            if allocated > amount(balance["outstanding"]):
                frappe.throw(_i18n("本次金额超过最新未付余额，请刷新"))
        _bank_account(bank_account, source.company, targets[0][1]["currency"], for_update=True)
        from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
        entry = None
        compatible = ("company", "party", "party_type", "payment_type", "paid_from", "paid_to", "paid_from_account_currency", "paid_to_account_currency", "source_exchange_rate", "target_exchange_rate", "book_advance_payments_in_separate_party_account")
        for target, _balance, allocated in targets:
            native = get_payment_entry(target.doctype, target.name, bank_account=bank_account, bank_amount=float(allocated))
            _require_fields("Payment Entry", set(compatible) | {"deductions", "references"})
            _require_fields("Payment Entry Reference", {"reference_doctype", "reference_name", "outstanding_amount", "allocated_amount", "payment_term"}, "Payment Entry")
            if native.get("deductions") or native.paid_from_account_currency != native.paid_to_account_currency:
                frappe.throw(_i18n("原生付款包含扣款或跨币种设置，请打开原生付款单处理"))
            if entry is not None and any(native.get(field) != entry.get(field) for field in compatible):
                frappe.throw(_i18n("合并付款的应付账户、币种或原生汇率设置不一致，请打开原生付款单处理"))
            remaining = allocated
            for ref in native.references:
                if ref.reference_doctype != target.doctype or ref.reference_name != target.name:
                    frappe.throw(_i18n("原生付款引用发生变化，请在原生单据处理"))
                ref.allocated_amount = float(min(remaining, max(Decimal(0), amount(ref.outstanding_amount))))
                remaining -= amount(ref.allocated_amount)
            if remaining:
                frappe.throw(_i18n("应付付款计划无法完整分配本次金额，请在原生付款单处理"))
            if entry is None:
                entry = native
            else:
                for ref in native.references:
                    entry.append("references", ref.as_dict())
        entry.check_permission("create")
        entry.posting_date = getdate(posting_date or nowdate())
        entry.reference_date = entry.posting_date
        entry.reference_no = str(reference_no or "")[:140] or None
        entry.paid_amount = entry.received_amount = float(value)
        entry.custom_remarks = bool(remarks)
        entry.remarks = str(remarks or "")[:1000] or f"采购付款：{source.name} / {target.name}"
        entry.insert()  # normal Frappe validation, workflow, field and document permissions
        if entry.docstatus != 0:
            frappe.throw(_i18n("付款草稿状态异常"))
        return {"doctype": "Payment Entry", "name": entry.name, "docstatus": 0, "reused": False}

    def replay(previous):
        # Read the complete native context and current ACLs; never trust audit output.
        from .purchase_document_actions import _payment
        from .purchase_operation import replay_artifacts
        replay_artifacts(previous)
        entry = _current("Payment Entry", previous["name"])
        entry.check_permission("write")
        _payment(entry)
        return {"name": entry.name, "docstatus": entry.docstatus, "reused": True}

    from .purchase_operation import run
    result = run(request_id, payload, operation, replay, digest=digest)
    return {key: result[key] for key in ("name", "docstatus", "reused")}


def _payment_allocations(allocations):
    rows = frappe.parse_json(allocations) if isinstance(allocations, str) else allocations
    if not isinstance(rows, list) or not rows or len(rows) > 100 or any(not isinstance(row, dict) or not row.get("name") or set(row) != {"name", "amount"} for row in rows):
        frappe.throw(_i18n("请明确每张应付单的本次付款金额"))
    if len({row["name"] for row in rows}) != len(rows) or any(amount(row["amount"]) <= 0 for row in rows):
        frappe.throw(_i18n("应付单不得重复且每笔付款金额必须大于0"))
    return sorted(rows, key=lambda row: row["name"])


def _payment_batch_targets(source_doctype, sources, check_versions=False):
    _receipt_payment_source(source_doctype)
    from .purchase_document_actions import _batch_sources, _locked_source, _version
    selected = _batch_sources(sources)
    originals = _locked_source(source_doctype, [row["name"] for row in selected], "Purchase Invoice")
    if check_versions:
        for original, chosen in zip(originals, selected, strict=False):
            _version(original, chosen.get("modified"))
    if any(doc.docstatus != 1 or doc.get("is_return") for doc in originals):
        frappe.throw(_i18n("所选来源必须已提交且非退货"))
    if any((doc.company, doc.supplier, doc.currency) != (originals[0].company, originals[0].supplier, originals[0].currency) for doc in originals):
        frappe.throw(_i18n("合并付款要求同公司、供应商和币种"))
    candidates = {}
    for original in originals:
        chain = get_purchase_chain(source_doctype, original.name, include_payments=False)
        if chain["incomplete_links"]:
            frappe.throw(LINK_WARNING)
        for invoice in chain["invoices"]:
            if invoice["docstatus"] == 1 and invoice["can_pay"]:
                candidates.setdefault(invoice["name"], original.name)
    if not candidates:
        frappe.throw(_i18n("所选采购链尚无已提交应付；多订单预付款请逐单或在原生付款单处理"))
    targets = []
    for name, original_name in sorted(candidates.items()):
        _, invoice, balance = payment_target(source_doctype, original_name, name)
        if invoice.currency != originals[0].currency or balance["currency"] != originals[0].currency:
            frappe.throw(_i18n("应付核销币种与所选来源不一致，请在原生付款单处理"))
        if amount(balance["outstanding"]) > 0:
            targets.append((invoice, balance))
    if not targets:
        frappe.throw(_i18n("所选应付已结清或不可付款，请刷新"))
    return {"sources": originals, "targets": targets}


@frappe.whitelist()
@procurement_entry
def preview_payment_batch(source_doctype, sources):
    context = _payment_batch_targets(source_doctype, sources)
    return {"source_doctype": source_doctype, "sources": [{"name": doc.name, "modified": str(doc.modified)} for doc in context["sources"]],
        "company": context["sources"][0].company, "supplier": context["sources"][0].supplier, "currency": context["sources"][0].currency,
        "invoices": [{"name": doc.name, **balance} for doc, balance in context["targets"]]}
