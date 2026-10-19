"""Original procurement evidence attached to the existing OA -> native PO flow.

Synchronization caches complete snapshots and creates reliable native drafts.
Order creation uses native permissions, never a receipt/payment/GL.
"""
from __future__ import annotations

from frappe import _ as _i18n


import copy
import json
import re
import uuid
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from datetime import UTC, datetime, timezone
from decimal import Decimal
from urllib.parse import urlencode

import frappe
from frappe.model import get_permitted_fields

from . import operating_oa_source as oa
from . import purchase_source_contract as contract
from .operating_expense_contract import digest
from .purchase_repost_boundary import initialize, procurement_entry
from . import purchase_source_rules as source_rules

DOCTYPE = "OA Purchase Request"
SOURCE_FIELD = "custom_purchase_source_json"
EVIDENCE_FIELD = "custom_cashier_payment_evidence"
BOUND_FIELD = "custom_purchase_bound_version"
SOURCE_ID_FIELD = "custom_purchase_source_id"
RECONCILIATION_FIELD = "custom_purchase_payment_reconciliation"
BENEFICIARY_FIELD = "custom_purchase_beneficiary_company"
PROPOSAL_FIELD = "custom_purchase_company_proposal"
PROJECT_FIELD = "custom_purchase_project"
CONFIRMED_FIELD = "custom_purchase_company_confirmed"
CONFIRMED_BY_FIELD = "custom_purchase_company_confirmed_by"
CONFIRMED_ON_FIELD = "custom_purchase_company_confirmed_on"
PENDING_FIELD = "custom_purchase_pending_reason"
SOURCE_PAGE_SIZE = 100  # Keep each upstream read below its fixed 10-second deadline.
MAX_SOURCE_ROWS = 20000
_managed = ContextVar("purchase_source_managed", default=False)


@contextmanager
def managed_write():
    token = _managed.set(True)
    try:
        yield
    finally:
        _managed.reset(token)


def _json(value):
    return json.loads(value or "{}") if isinstance(value, str) else dict(value or {})


def _version(source, evidence, adaptation=None):
    parts = [source.get("version"), evidence.get("version")]
    if adaptation is not None:
        parts.append(adaptation["preview_fingerprint"])
    return digest(parts)


def _native(doctype, name, permission="read"):
    doc = frappe.get_doc(doctype, name)
    doc.check_permission(permission)
    if doctype == "Company":
        from frappe.permissions import get_user_permissions
        allowed = get_user_permissions(frappe.session.user).get("Company", [])
        if allowed and name not in {p.doc for p in allowed}:
            frappe.throw(_i18n("无权访问此公司"), frappe.PermissionError)
    elif doc.get("company"):
        _native("Company", doc.company)
    return doc


def _write_fields(doctype, fields, parenttype=None):
    allowed = set(get_permitted_fields(doctype, parenttype=parenttype, permission_type="write"))
    meta = frappe.get_meta(doctype)
    levels = set(meta.get_permlevel_access(permission_type="write", parenttype=parenttype))
    allowed.update(f.fieldname for f in meta.fields if f.fieldtype in ("Table","Table MultiSelect") and f.permlevel in levels)
    if not set(fields) <= allowed:
        frappe.throw(_i18n("没有权限填写完整采购字段"), frappe.PermissionError)


def _confirmed_company(doc):
    # A legacy native PO link already identifies the legal purchasing company.
    # An old unbound target_company may only be an applicant-organization hint.
    return doc.get("target_company") if doc.get("purchase_order") or frappe.utils.cint(doc.get(CONFIRMED_FIELD)) else None


def _source(name, write=False):
    if write:
        initialize()
    doc = frappe.get_doc(DOCTYPE, name, for_update=write)
    doc.check_permission("write" if write else "read")
    _source_read_fields()
    if not doc.get(SOURCE_FIELD):
        frappe.throw(_i18n("此采购申请尚未接入原始来源，请先同步核对"))
    company = _confirmed_company(doc)
    if company:
        _native("Company", company)
    elif frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
        frappe.throw(_i18n("来源公司待确认，请由管理员核对"), frappe.PermissionError)
    return doc


def _source_read_fields():
    required = {"target_company", "purchase_order", "approval_status", "process_instance_id", "process_code",
                "oa_code", "apply_date", "creator", "execution_region", "department", "project", "order_no",
                "currency", "payment_amount", "detail_total_amount", "items_json", "processors_json", "payee",
                "description", "delivery_date", "payments_json", "payment_terms", "payment_date", "attachments_json",
                BENEFICIARY_FIELD, PROPOSAL_FIELD, PROJECT_FIELD, CONFIRMED_FIELD}
    if not required <= set(get_permitted_fields(DOCTYPE, permission_type="read")):
        frappe.throw(_i18n("没有权限查看完整采购来源"), frappe.PermissionError)
    from .purchase_payment_service import _require_fields
    _require_fields(DOCTYPE,{"items","processors","payments"})
    for child,fields in (("OA Purchase Request Item",{"item_code","item_name","specification","qty","uom","amount"}),
                         ("OA Purchase Request Processor",{"processor_name","processor_phone","odt","sales_order_no","processing_materials","qty","unit_price","amount"}),
                         ("OA Purchase Request Payment",{"payee","amount","payment_terms","currency","payment_date"})):
        _require_fields(child,fields,DOCTYPE)


def _normalize(row, *, occurrences=None):
    # Reuse the existing DingTalk table/text extractor, not a second table parser.
    from overseas_costing.scripts.import_oa_logistics import (
        _iter_form_components,
        extract_purchase_expense_rows,
    )
    from overseas_costing.utils.field_mapper import map_purchase_expense_row_to_item
    prepared = copy.deepcopy(row)
    if isinstance(prepared.get("form_component_values"), str):
        prepared["form_component_values"] = json.loads(prepared["form_component_values"])
    # DingTalk has used both rowValue objects and arrays of cells. Adapt only
    # their envelope; the existing shared extractor still parses every cell.
    for component in _iter_form_components(prepared):
        if (component.get("componentType") or component.get("component_type")) != "TableField":
            continue
        try:
            value=component.get("value")
            rows=json.loads(value) if isinstance(value,str) else value
        except (TypeError,ValueError):
            continue
        if isinstance(rows,list):
            component["value"]=[{"rowValue":r} if isinstance(r,list) else r for r in rows]
    details = [map_purchase_expense_row_to_item(r) for r in extract_purchase_expense_rows(prepared)]
    result = contract.normalize(row, detail_rows=details, occurrences=occurrences)
    # Original department IDs were omitted by the old projection. They enrich
    # adaptation scope without invalidating an unchanged, previously bound version.
    if row.get("originator_dept_id") or row.get("originator_dept_name"):
        result["adaptation_context"] = {key:row.get(key) for key in ("originator_dept_id","originator_dept_name")}
    return result


def _cashier_snapshot(until):
    from .operating_expenses import _request
    items, cursor = [], None
    for _ in range(40):
        result = _request("/api/integrations/erp/purchase-expenses", {"limit":500, "until":until, **({"cursor":cursor} if cursor else {})})
        try:
            same_instant = oa.timestamp(result.get("until")) == oa.timestamp(until)
        except (ValueError, TypeError):
            same_instant = False
        if not same_instant:
            frappe.throw(_i18n("采购出纳快照时间不符"))
        items.extend(result["items"])
        if result["end"]:
            if len({i["source_id"] for i in items}) != len(items):
                frappe.throw(_i18n("采购出纳根标识重复，请管理员核对"))
            return items
        if cursor == result["next_cursor"]:
            frappe.throw(_i18n("采购出纳分页未前进"))
        cursor = result["next_cursor"]
    frappe.throw(_i18n("采购出纳来源超过本次读取上限"))


def _fresh_source(doc):
    from .operating_expenses import _oa_connection
    old = _json(doc.get(SOURCE_FIELD))
    until = datetime.now(timezone.utc).isoformat()
    rows, _ = oa.read_page(_oa_connection()._connection, 1, until, identity=old["oa_identity"], process_codes=contract.PROCESS_CODES)
    occurrences = contract._field_occurrences(rows[0]) if len(rows) == 1 else None
    if len(rows) != 1 or not contract.in_scope(rows[0], occurrences=occurrences):
        frappe.throw(_i18n("采购来源已移出中国/墨西哥2026范围或不存在，请核对原单"))
    source = _normalize(rows[0], occurrences=occurrences)
    if source["oa_identity"] != old["oa_identity"]:
        frappe.throw(_i18n("采购来源身份不符"))
    return source, contract.payment_evidence(source, _cashier_snapshot(until))


def _assert_current(source, evidence, expected_version, adaptation=None):
    if _version(source, evidence, adaptation) != expected_version:
        frappe.throw(_i18n("来源或付款证据已变化，请刷新后重新核对"))
    if not source.get("eligible"):
        frappe.throw(_i18n("采购来源未审批通过、已拒绝或撤销，不能办理"))


def _validate_items(items):
    for row in items:
        item = _native("Item", row["item_code"])
        _native("UOM", row["uom"])
        if item.get("disabled") or not item.get("is_purchase_item"):
            frappe.throw(_i18n("物料必须为启用的采购物料"))
        units = {item.stock_uom} | {u.uom for u in item.get("uoms") or []}
        if row["uom"] not in units:
            frappe.throw(_i18n("采购单位未在物料中维护，请先核对单位换算"))


def _validate_buyer_company(doc, company, correction_reason):
    if not doc.get("target_company") or doc.target_company == company:
        return
    if doc.get("purchase_order") or frappe.utils.cint(doc.get(CONFIRMED_FIELD)) or not frappe.utils.cint(doc.get("backfill_imported")):
        frappe.throw(_i18n("来源公司与采购订单公司不一致"))
    if not str(correction_reason or "").strip():
        frappe.throw(_i18n("原申请组织仅为采购公司建议；更正采购公司请填写核对说明"))


def _project_company(project, company):
    meta = frappe.get_meta("Project")
    return bool(meta.has_field("company") and meta.has_field("is_active") and company
                and project.get("company") == company and project.get("is_active") == "Yes")


def _role_candidate(source, key, doctype, company=None):
    """Exact, permission-scoped native matches; duplicate labels stay ambiguous."""
    raw = source.get(key)
    status = source.get(key + "_status") or ("unique" if isinstance(raw, str) and raw.strip() else "missing")
    if status != "unique":
        return {"value":None, "status":status}
    display = {"Company":"company_name", "Project":"project_name", "Supplier":"supplier_name"}[doctype]
    filters = [[doctype,"name","=",raw]]
    if frappe.get_meta(doctype).has_field(display):
        filters.append([doctype,display,"=",raw])
    try:
        candidates = frappe.get_list(doctype, or_filters=filters, fields=["name"], limit_page_length=2)
        if len(candidates) != 1:
            return {"value":None, "status":"ambiguous" if candidates else "unresolved"}
        native = _native(doctype, candidates[0]["name"])
    except frappe.PermissionError:
        return {"value":None, "status":"unreadable"}
    if doctype == "Project" and not _project_company(native, company):
        return {"value":None, "status":"incompatible"}
    return {"value":native.name, "status":"unique"}


def _select_roles(doc, source, company, beneficiary_company=None, project=None):
    values = {}
    for key,doctype,field,explicit in (("beneficiary_company","Company",BENEFICIARY_FIELD,beneficiary_company),
                                       ("project","Project",PROJECT_FIELD,project)):
        if explicit is not None and not isinstance(explicit, str):
            frappe.throw("请明确选择已有受益公司" if key == "beneficiary_company" else "请明确选择已有项目或用空白字符串留空")
        explicit = explicit.strip() if explicit is not None else None
        # Existing verified values survive a newer raw source snapshot.
        chosen = explicit if explicit is not None else doc.get(field)
        if chosen:
            native = _native(doctype, str(chosen).strip())
            if doctype == "Project" and not _project_company(native, company):
                frappe.throw(_i18n("项目必须启用且明确属于采购公司，请选择有效项目或明确留空"))
            values[key] = native.name
            continue
        if explicit is not None and key == "project":
            values[key] = ""  # Explicitly leaving it empty also clears an older verified source link.
            continue
        candidate = _role_candidate(source,key,doctype,company)
        if candidate["status"] == "ambiguous" or (key == "beneficiary_company" and candidate["status"] not in ("unique","missing")):
            frappe.throw("请明确选择已有受益公司" if key == "beneficiary_company" else "项目来源不唯一，请明确选择已有项目或留空")
        values[key] = candidate["value"]
    return values["beneficiary_company"], values["project"]


def _role_projection(doc, source):
    warnings = []
    def visible(doctype, name, label):
        if not name:
            return None
        try:
            return _native(doctype, name).name
        except (frappe.PermissionError, frappe.DoesNotExistError):
            warnings.append(label + "不可访问，请核对已有记录和公司权限")
            return None

    company = _confirmed_company(doc)
    proposal = doc.get(PROPOSAL_FIELD)
    if not proposal and not company and frappe.utils.cint(doc.get("backfill_imported")):
        proposal = doc.get("target_company")  # Preserve the old hint without relabeling it as confirmed.
    proposal = visible("Company", proposal, "建议采购公司")
    beneficiary = _role_candidate(source,"beneficiary_company","Company")
    project = _role_candidate(source,"project","Project",company or proposal)
    if not company:
        warnings.append("采购公司待确认；申请人组织仅为采购公司建议")
    for label,candidate in (("最终使用/销售公司",beneficiary),("来源项目",project)):
        status = candidate["status"]
        if status == "ambiguous":
            warnings.append(label + "来源或候选不唯一，请明确选择已有记录")
        elif status in ("unresolved","unreadable"):
            warnings.append(label + "未能匹配可访问的已有记录，请人工核对")
        elif status == "incompatible":
            warnings.append("来源项目未满足启用及采购公司归属条件；原文保留，原生采购项目留空或选择有效项目")
    return {"purchasing_company":company, "company_confirmed":bool(company), "buyer_company_proposal":proposal,
            "beneficiary_company":visible("Company",doc.get(BENEFICIARY_FIELD),"已确认最终使用/销售公司"),
            "beneficiary_company_candidate":beneficiary["value"], "beneficiary_company_status":beneficiary["status"],
            "project":visible("Project",doc.get(PROJECT_FIELD),"已确认采购项目"),
            "project_candidate":project["value"], "project_status":project["status"], "role_warnings":warnings}


def _role_projections(records, sources, permitted, purchase_orders):
    """Request-local list roles, after the native OA name/field ACL query.

    Source hints are labelled pending evidence. Exact native candidates obey
    record, Company and field permissions, but never become confirmed roles.
    Linked native PO.company is authoritative; proposals cannot assign it.
    Company/Project discovery is batched and each candidate is checked once.
    This read-only projection does not change confirmation or synchronization.
    """
    from .purchase_fulfilment_service import _company
    from .purchase_order_progress import _FieldScope, _ProgressReader
    from .purchase_payment_service import _quiet_link_errors, _read, _record_reader
    records = list(records)
    reader = _record_reader.get() or _ProgressReader(get_permitted_fields); token = _record_reader.set(reader)
    try:
        native_fields = {dt: reader.fields.permitted(dt) if isinstance(reader.fields, _FieldScope) else
            set(get_permitted_fields(dt, permission_type="read")) for dt in ("Company", "Project")}
        readable = {dt: bool(frappe.has_permission(dt, "read")) for dt in native_fields}
        metadata = {dt: frappe.get_meta(dt) for dt in native_fields}
        tokens = {"Company": set(), "Project": set()}
        direct = {"Company": set(), "Project": set()}
        forward_companies = {}
        source_names = {doc["name"] for doc in records}
        for order in purchase_orders.values():
            source_name = order.get("custom_oa_purchase_expense")
            if source_name in source_names:
                companies = forward_companies.setdefault(source_name, set())
                if order.get("company"):
                    companies.add(order["company"])
        def purchasing_company(doc):
            linked = doc.get("purchase_order")
            if linked and linked in purchase_orders:
                return purchase_orders[linked].get("company")
            if doc["name"] in forward_companies:
                companies = forward_companies[doc["name"]]
                return next(iter(companies)) if len(companies) == 1 else None
            return doc.get("target_company") if not linked and doc.get(CONFIRMED_FIELD) in (True, 1, "1") else None
        for doc in records:
            source = sources.get(doc["name"], {})
            for key, dt, field in (("beneficiary_company", "Company", BENEFICIARY_FIELD), ("project", "Project", PROJECT_FIELD)):
                raw = source.get(key)
                if field in permitted and isinstance(raw, str) and raw.strip():
                    tokens[dt].add(raw)
                if doc.get(field):
                    direct[dt].add(doc[field])
            direct["Company"].update(value for value in (doc.get("target_company"), doc.get(PROPOSAL_FIELD),
                purchasing_company(doc)) if value)
        indexes = {dt: {} for dt in tokens}
        for dt, values in tokens.items():
            allowed = native_fields[dt]
            display = "company_name" if dt == "Company" else "project_name"
            if values and "name" in allowed and readable[dt]:
                conditions = [[dt, "name", "in", list(values)]]
                fields = ["name"]
                if display in allowed and metadata[dt].has_field(display):
                    conditions.append([dt, display, "in", list(values)]); fields.append(display)
                with _quiet_link_errors():
                    try:
                        found = frappe.get_list(dt, or_filters=conditions, fields=fields, limit_page_length=0)
                    except frappe.PermissionError:
                        found = []
                for row in found:
                    direct[dt].add(row["name"])
                    for key in fields:
                        value = row.get(key)
                        if value in values:
                            indexes[dt].setdefault(value, set()).add(row["name"])
            reader.preload(dt, direct[dt])
        reader.preload("Company", {doc.company for (dt, _), doc in reader.docs.items() if dt == "Project" and doc and doc.get("company")})
        visible_cache = {}
        def visible(dt, name):
            if not name or "name" not in native_fields[dt] or not readable[dt]:
                return None
            key = (dt, name)
            if key not in visible_cache:
                with _quiet_link_errors():
                    try:
                        doc = _company(name) if dt == "Company" else _read(dt, name)
                        if dt == "Project" and doc.get("company"):
                            _company(doc.company)
                        visible_cache[key] = doc
                    except (frappe.PermissionError, frappe.DoesNotExistError):
                        visible_cache[key] = None
            return visible_cache[key]
        def candidate(source, key, dt, company):
            raw = source.get(key)
            status = source.get(key + "_status") or ("unique" if isinstance(raw, str) and raw.strip() else "missing")
            if status != "unique":
                return {"value": None, "status": status}
            matches = indexes[dt].get(raw, set())
            if len(matches) != 1:
                return {"value": None, "status": "ambiguous" if matches else "unresolved"}
            name = next(iter(matches)); doc = visible(dt, name)
            if doc is None:
                return {"value": None, "status": "unreadable"}
            if dt == "Project" and (not {"company", "is_active"} <= native_fields[dt] or
                    not metadata[dt].has_field("company") or not metadata[dt].has_field("is_active") or
                    not company or doc.get("company") != company or doc.get("is_active") != "Yes"):
                return {"value": None, "status": "incompatible"}
            return {"value": name, "status": "unique"}
        result = {}
        for doc in records:
            source = sources.get(doc["name"], {}); warnings = []
            company = purchasing_company(doc)
            company_doc = visible("Company", company)
            company = company_doc.name if company_doc else None
            proposal = doc.get(PROPOSAL_FIELD)
            if not proposal and not company and doc.get("backfill_imported"):
                proposal = doc.get("target_company")
            proposal_doc = visible("Company", proposal)
            role = {"purchasing_company": company, "company_confirmed": bool(company),
                "buyer_company_proposal": proposal_doc.name if proposal_doc else None, "source_hint_label": "来源待核对"}
            if not company:
                warnings.append("采购公司待确认；申请人组织仅为采购公司建议")
            for key, dt, field in (("beneficiary_company", "Company", BENEFICIARY_FIELD), ("project", "Project", PROJECT_FIELD)):
                permitted_source = source if field in permitted else {}
                match = candidate(permitted_source, key, dt, company)
                confirmed = visible(dt, doc.get(field)) if field in permitted else None
                if dt == "Project" and not {"company", "is_active"} <= native_fields[dt]:
                    confirmed = None
                role.update({key: confirmed.name if confirmed else None, key + "_candidate": match["value"],
                    key + "_status": match["status"], "source_" + key + "_hint": permitted_source.get(key)})
                if match["status"] in ("ambiguous", "unresolved", "unreadable", "incompatible"):
                    warnings.append(("最终归属" if dt == "Company" else "来源项目") + "待核对已有记录、权限及项目归属")
                if doc.get(field) and not confirmed:
                    warnings.append("已确认角色不可访问，请核对已有记录和公司权限")
            role["beneficiary_companies"] = [role["beneficiary_company"]] if role["beneficiary_company"] else []
            role["role_warnings"] = warnings
            result[doc["name"]] = role
        return result
    finally:
        _record_reader.reset(token)


def _bind(doc, order, source, evidence, correction_reason="", *, beneficiary_company=None, project=None, adaptation=None):
    _write_fields("Purchase Order",{"custom_oa_purchase_expense"})
    role_values = {key:value for key,value in ((BENEFICIARY_FIELD,beneficiary_company),(PROJECT_FIELD,project)) if value is not None}
    _write_fields(DOCTYPE,{"purchase_order","target_company", *role_values})
    from .purchase_repost_boundary import document_boundary
    # Same-link rechecks do not save PO, but still mutate authoritative source
    # evidence. Borrow the SAME boundary before either OA evidence or Comment.
    with document_boundary(order):
        if order.get("custom_oa_purchase_expense") not in (None,"",doc.name):
            frappe.throw(_i18n("采购订单已关联另一条钉钉申请"))
        if not order.meta.has_field("custom_oa_purchase_expense"):
            frappe.throw(_i18n("采购关联字段未安装，请联系管理员"))
        # Rechecking the same link updates source evidence only, not the native PO.
        if order.get("custom_oa_purchase_expense") != doc.name:
            if order.docstatus == 1 and not order.meta.get_field("custom_oa_purchase_expense").allow_on_submit:
                frappe.throw(_i18n("该已提交订单不允许更新采购关联，请先核对元数据策略"))
            order.custom_oa_purchase_expense=doc.name
            with managed_write():
                order.save()  # Native permissions/update-after-submit still apply.
        values = {"purchase_order":order.name, "target_company":order.company, BOUND_FIELD:source["version"],
                  CONFIRMED_FIELD:1, CONFIRMED_BY_FIELD:frappe.session.user, CONFIRMED_ON_FIELD:frappe.utils.now_datetime(), **role_values,
                  SOURCE_FIELD:json.dumps(source,ensure_ascii=False,default=str), EVIDENCE_FIELD:json.dumps(evidence,ensure_ascii=False,default=str),
                  "source_stale":0, "source_invalid":0, "source_pending":0, PENDING_FIELD:"", "sync_status":"Purchase Order Created"}
        if adaptation is not None:
            values[source_rules.ADOPTION_FIELD] = json.dumps({"source_version":source["version"],"plan":adaptation},ensure_ascii=False,default=str)
        frappe.db.set_value(DOCTYPE,doc.name,values)
        if correction_reason:
            doc.add_comment("Comment", "采购来源人工核对：" + str(correction_reason)[:2000])


def _prepare_purchase_order(doc, source, company, supplier, currency, schedule_date, items, correction_reason="", beneficiary_company=None, project=None, *, automatic=False):
    _validate_buyer_company(doc,company,correction_reason)
    if not company or not supplier or not schedule_date or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(schedule_date)):
        frappe.throw(_i18n("请明确选择公司、供应商和需求日期"))
    _native("Company",company); party = _native("Supplier",supplier); _native("Currency",currency)
    if party.get("disabled"):
        frappe.throw(_i18n("供应商已停用"))
    if not frappe.has_permission("Purchase Order","create"):
        frappe.throw(_i18n("没有权限新建采购订单"),frappe.PermissionError)
    selected_beneficiary,selected_project = _select_roles(doc,source,company,beneficiary_company,project)
    try:
        selected = items if automatic else contract.validate_selection(source,currency,frappe.parse_json(items),correction_reason)
    except ValueError as exc:
        frappe.throw(str(exc))
    _validate_items(selected)
    for raw, row in zip(source.get("items") or [], selected, strict=False):
        if raw.get("item_code") == row.get("item_code") and raw.get("description"):
            row["description"] = raw["description"]
    fields = {"company","supplier","currency","schedule_date","items"}
    if selected_project:
        if not frappe.get_meta("Purchase Order").has_field("project"):
            frappe.throw(_i18n("采购订单项目字段未安装，请联系管理员"))
        fields.add("project")
    _write_fields("Purchase Order", fields)
    _write_fields("Purchase Order Item", {"item_code","qty","uom","rate","schedule_date"} | ({"description"} if any(r.get("description") for r in selected) else set()), "Purchase Order")
    order = frappe.new_doc("Purchase Order")
    order.company,order.supplier,order.currency,order.schedule_date = company,supplier,currency,schedule_date
    if selected_project:
        order.project = selected_project
    # Manual API retains its original today-based native behavior.
    order.transaction_date = source["apply_date"] if automatic else frappe.utils.nowdate()
    order.set("items",[])
    for row in selected:
        order.append("items",{**row,"schedule_date":schedule_date})
    return order, selected_beneficiary, selected_project


def _insert_purchase_order(doc, order, source, evidence, correction_reason="", *, beneficiary_company=None, project=None, automatic=False, adaptation=None):
    order.insert()  # Any insertion/hook error escapes and rolls back the batch.
    _bind(doc,order,source,evidence,correction_reason,beneficiary_company=beneficiary_company,project=project,adaptation=adaptation)
    if automatic:
        _check_automatic_amounts(frappe.get_doc("Purchase Order",order.name),_adapted_source(source,adaptation) if adaptation is not None else source)
    return {"name":order.name,"doctype":"Purchase Order"}


@frappe.whitelist(methods=["POST"])
@procurement_entry
def create_purchase_order_from_source(name, expected_version, company, supplier, currency, schedule_date, items, correction_reason="", beneficiary_company=None, project=None):
    doc = _source(name, write=True)
    if doc.get("purchase_order"):
        _native("Purchase Order",doc.purchase_order,"read")
        return {"name":doc.purchase_order,"doctype":"Purchase Order"}
    source, evidence = _fresh_source(doc)
    adaptation = _adaptation_preview(doc, source, evidence)
    _assert_current(source,evidence,expected_version,adaptation)
    from .purchase_repost_boundary import acquire
    with acquire(_adaptation_keys(adaptation)) if adaptation is not None else nullcontext():
        if adaptation is not None:
            current = _adaptation_preview(doc,source,evidence)
            _assert_current(source,evidence,expected_version,current)
            adapted = _adapted_source(source,current)
            try:
                chosen = contract.validate_selection(adapted,currency,frappe.parse_json(items),correction_reason)
            except ValueError as error:
                frappe.throw(str(error))
            if current.get("rule"):
                source_rules.ensure_items(_manual_adoption_plan(current,chosen,company))
            for raw, selected in zip(current["items"], chosen, strict=False):
                if raw.get("item_code") == selected.get("item_code"):
                    selected["description"] = raw.get("description")
            items, adaptation = chosen, current
        else:
            adapted = source
        order, beneficiary, selected_project = _prepare_purchase_order(doc,adapted,company,supplier,currency,schedule_date,
            items,correction_reason,beneficiary_company,project)
        if adaptation is not None:
            order.transaction_date = source["apply_date"]
            _check_automatic_price_writes(order)
        return _insert_purchase_order(doc,order,source,evidence,correction_reason,beneficiary_company=beneficiary,project=selected_project,adaptation=adaptation)


def _manual_adoption_plan(plan, selected, company):
    # Only source-defined Items still selected for their mapped buyer may be
    # auto-created. Replacements use existing native Items through manual checks.
    keys = {(row.get("item_code"),row.get("uom")) for row in selected}
    return {**plan,"items":[row for row in plan["items"]
        if company == plan.get("company") and (row.get("item_code"),row.get("uom")) in keys]}


def _adaptation_preview(doc, source, evidence):
    # Old linked snapshots keep their original evidence and confirmation semantics.
    if doc.get("purchase_order") or not isinstance(source.get("apply_date"),str) or source["apply_date"] < "2026-09-25":
        return None
    if not frappe.db.exists("DocType", source_rules.DOCTYPE):
        return None
    return source_rules.preview(source,evidence,doc=doc)


def _adapted_source(source, plan):
    return {**source,"items":plan["items"],"issues":list(plan.get("blocking_reasons") or []),
            "schedule_date":plan.get("schedule_date") or source.get("schedule_date"),
            "detail_total_amount":plan.get("total_amount"),"currency":plan.get("currency") or source.get("currency"),
            "purchasing_company":plan.get("company"),"purchasing_company_status":"unique",
            "supplier":plan.get("supplier"),"supplier_status":"unique"}


def _adaptation_keys(plan):
    if plan is None:
        return ()
    keys = ["dlp-source-item:" + digest(row.get("item_code"))[:40] for row in plan["items"] if row.get("item_code")]
    rule = plan.get("rule")
    if rule:
        keys.append("dlp-source-rule:" + rule["scope_key"][:40])
    return tuple(sorted(set(keys)))


def _create_adapted_order(doc, source, evidence, plan):
    """Reuse native draft preparation; expected failures undo only this source's new Items."""
    from .purchase_repost_boundary import acquire
    if plan["blocking_reasons"]:
        return None, "；".join(plan["blocking_reasons"])
    with acquire(_adaptation_keys(plan)):
        current = source_rules.preview(source,evidence,doc=doc)
        if current["preview_fingerprint"] != plan["preview_fingerprint"]:
            return None, "原单、规则或物料状态已变化，等待下轮重新核对"
        savepoint = "purchase_source_items_" + uuid.uuid4().hex
        frappe.db.savepoint(savepoint)
        try:
            new_items = source_rules.ensure_items(current)
            prepared, reason = _automatic_order(doc,_adapted_source(source,current))
        except (ValueError,frappe.ValidationError,frappe.PermissionError,frappe.DoesNotExistError) as error:
            prepared, reason = None, str(error)
        if prepared is None:
            frappe.db.rollback(save_point=savepoint)
            return None, reason
        order, beneficiary, project = prepared
        # Any insertion/binding/postcondition error escapes to the existing full-batch rollback.
        result = _insert_purchase_order(doc,order,source,evidence,beneficiary_company=beneficiary,
            project=project,automatic=True,adaptation=current)
        return {**result,"created_items":new_items}, ""


def _check_automatic_amounts(order, source):
    """Compare native rounding/defaults with every original source row."""
    def equal(doc, field, actual, expected):
        precision = doc.precision(field)
        return precision is not None and expected is not None and frappe.utils.flt(actual,precision) == frappe.utils.flt(expected,precision)
    original = source.get("items") or []
    if order.docstatus != 0 or order.currency != source.get("currency") or len(order.items) != len(original):
        frappe.throw(_i18n("原生采购币种或明细与来源不符，待人工核对"))
    for row, raw in zip(order.items,original,strict=False):
        if (row.item_code != raw.get("item_code") or row.uom != raw.get("uom")
                or not equal(row,"qty",row.qty,raw.get("qty")) or not equal(row,"amount",row.amount,raw.get("amount"))
                or (raw.get("rate") is not None and not equal(row,"rate",row.rate,raw["rate"]))):
            frappe.throw(_i18n("原生采购明细精度或金额与来源不符，待人工核对"))
    total = source.get("detail_total_amount")
    if any(not equal(order,field,order.get(field),total) for field in ("total","net_total","grand_total")):
        frappe.throw(_i18n("原生默认税费、折扣或采购金额与来源不符，待人工核对"))
    if order.get("rounded_total") and not equal(order,"rounded_total",order.rounded_total,total):
        frappe.throw(_i18n("原生采购舍入金额与来源不符，待人工核对"))


def _check_automatic_price_writes(order):
    """Prove native Item Price maintenance cannot write before defaults run."""
    settings = frappe.get_cached_doc("Stock Settings")
    if not settings.auto_insert_price_list_rate_if_missing or not frappe.has_permission("Item Price","write"):
        return
    from erpnext.accounts.party import _get_party_details
    from erpnext.stock.get_item_details import (
        ItemDetailsCtx,
        get_basic_details,
        get_price_list_details,
        get_price_list_rate_for,
    )
    # Use the same native supplier defaults as BuyingController, before its
    # set_missing_item_details calls get_price_list_rate -> insert_item_price.
    order.update_if_missing(_get_party_details(order.supplier,party_type="Supplier",doctype=order.doctype,
        company=order.company,ignore_permissions=order.flags.ignore_permissions))
    price_list = order.buying_price_list
    if (not price_list or order.is_internal_supplier
            or frappe.db.get_value("Price List",price_list,"currency",cache=True) != order.currency):
        return  # native insert_item_price returns without writing in these branches
    if settings.update_existing_price_list_rate:
        frappe.throw(_i18n("原生价格表启用自动更新，自动草稿需先人工核对；不会覆盖已有物料价格"))
    for row in order.items:
        item = frappe.get_cached_doc("Item",row.item_code)
        ctx = ItemDetailsCtx({**order.as_dict(),**row.as_dict(),"doctype":order.doctype,
            "price_list":price_list,"transaction_type":"buying",
            "price_list_uom_dependant":get_price_list_details(price_list).get("price_list_uom_dependant")})
        get_basic_details(ctx,item,overwrite_warehouse=False)
        rate = get_price_list_rate_for(ctx,item.name)
        if rate is None and item.variant_of:
            rate = get_price_list_rate_for(ctx,item.variant_of)
        if rate is None:
            frappe.throw(_i18n("原生价格表未匹配已有有效价格，自动草稿需先人工核对；不会新建物料价格"))


def _automatic_order(doc, source):
    """All pending decisions happen before native insert; no guessed master data."""
    try:
        if not source.get("eligible"):
            frappe.throw(_i18n("采购来源未审批通过、已拒绝或撤销"))
        if any(source.get(k,1) != 1 for k in ("instance_count","business_count")):
            frappe.throw(_i18n("采购来源身份或审批号不唯一，待人工核对"))
        if source.get("issues"):
            frappe.throw("；".join(source["issues"]))
        company = _confirmed_company(doc)
        if not company:
            company = _role_candidate(source,"purchasing_company","Company")["value"]
        if not company:
            frappe.throw(_i18n("采购公司待确认；申请人组织仅为建议，请核对原单明确采购公司"))
        candidates = [source.get("supplier"), *(r.get("supplier") for r in source.get("items") or [])]
        suppliers = {str(value).strip() for value in candidates if isinstance(value,str) and value.strip()}
        if source.get("supplier_status") == "ambiguous" or len(suppliers) != 1:
            frappe.throw(_i18n("供应商缺失或不唯一，请明确选择已有供应商"))
        supplier = _role_candidate({"supplier":suppliers.pop(),"supplier_status":"unique"},"supplier","Supplier")["value"]
        if not supplier:
            frappe.throw(_i18n("供应商未唯一匹配可访问的已有记录"))
        if not source.get("currency"):
            frappe.throw(_i18n("采购币种缺失或不唯一"))
        selected = []
        for index, raw in enumerate(source.get("items") or [],1):
            if raw.get("rate_invalid"):
                frappe.throw(_i18n("第{0}行原单单价无效，待人工核对").format(index))
            qty, amount, rate = (contract.oa.exact_amount(raw.get(key)) for key in ("qty","amount","rate"))
            if not raw.get("item_code") or not raw.get("uom") or qty is None or Decimal(qty) <= 0 or amount is None or Decimal(amount) < 0:
                frappe.throw(_i18n("第{0}行物料、单位、正数数量或明确金额待完善").format(index))
            if raw.get("currency") and contract.oa.CURRENCIES.get(contract.oa.normalized(raw["currency"])) != source["currency"]:
                frappe.throw(_i18n("第{0}行采购币种与来源不符").format(index))
            if rate is None:
                rate = str(Decimal(amount)/Decimal(qty))
            if Decimal(rate) < 0:
                frappe.throw(_i18n("第{0}行单价无效").format(index))
            selected.append({"item_code":raw["item_code"],"qty":qty,"uom":raw["uom"],"rate":rate})
        if not selected:
            frappe.throw(_i18n("采购明细待完善"))
        order, beneficiary, project = _prepare_purchase_order(doc,source,company,supplier,source["currency"],source.get("schedule_date"),selected,automatic=True)
        _check_automatic_price_writes(order)
        # Native defaults/calculation execute on the unsaved document. Insertion
        # and post-insert checks remain outside this pending-only catch.
        order.run_method("set_missing_values")
        order.run_method("set_taxes")
        order.run_method("validate")
        # Native insert assigns child.parent together with the real PO name.
        # Every other mandatory field must already be supplied by native defaults.
        missing = [message for row in [order,*order.get_all_children()]
                   for field,message in row._get_missing_mandatory_fields() if row is order or field != "parent"]
        if missing:
            frappe.throw("采购必要字段待完善：" + "；".join(missing))
        _check_automatic_amounts(order,source)
        return (order,beneficiary,project), ""
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        return None, str(error)


@frappe.whitelist(methods=["POST"])
@procurement_entry
def associate_purchase_order(name, purchase_order, expected_version, correction_reason="", beneficiary_company=None, project=None):
    doc = _source(name,write=True)
    if doc.get("purchase_order") and doc.purchase_order != purchase_order:
        frappe.throw(_i18n("来源已关联采购订单，请先核对现有关联"))
    source,evidence = _fresh_source(doc)
    _assert_current(source,evidence,expected_version,_recheck_plan(doc,source))
    order = frappe.get_doc("Purchase Order",purchase_order,for_update=True)
    order.check_permission("write"); _native("Company",order.company)
    if order.docstatus == 2 or order.status in ("Closed","Cancelled","On Hold"):
        frappe.throw(_i18n("不能关联已取消、关闭或暂停的订单"))
    _validate_buyer_company(doc,order.company,correction_reason)
    native_project = order.get("project") or ""
    if project is not None and str(project).strip() != native_project:
        frappe.throw(_i18n("请先在已有采购订单核对项目；关联入口不会改写订单项目"))
    selected_beneficiary,selected_project = _select_roles(doc,source,order.company,beneficiary_company,native_project)
    try:
        contract.validate_selection(source,order.currency,[{k:r.get(k) for k in ("item_code","qty","uom","rate")} for r in order.items],correction_reason)
    except ValueError as exc:
        frappe.throw(str(exc))
    adoption = _rechecked_adoption(doc,source,correction_reason)
    if adoption is not None and order.docstatus != 0:
        frappe.throw(_i18n("已提交订单的来源范围变化，请先按原生流程处理后再核对"))
    _bind(doc,order,source,evidence,correction_reason,beneficiary_company=selected_beneficiary,project=selected_project,adaptation=adoption)
    return {"name":order.name,"doctype":"Purchase Order"}


def _rechecked_adoption(doc, source, correction_reason):
    adoption = _json(doc.get(source_rules.ADOPTION_FIELD)).get("plan")
    if not adoption:
        return None
    from .purchase_source_adaptation import scope, scope_key
    current = scope(source)
    if current == adoption.get("scope"):
        return None
    if not str(correction_reason or "").strip():
        frappe.throw(_i18n("原申请组织或采购类型已变化，请填写核对说明"))
    try:
        scope_key(current)
    except ValueError as error:
        frappe.throw(str(error))
    return {**adoption,"scope":current,"previous_scope":adoption.get("scope"),
        "scope_rechecked_by":frappe.session.user,"scope_rechecked_on":str(frappe.utils.now_datetime())}


def _recheck_plan(doc, source):
    adoption = _json(doc.get(source_rules.ADOPTION_FIELD)).get("plan")
    if not adoption:
        return None
    from .purchase_source_adaptation import scope
    review = {"adopted_scope":adoption.get("scope"),"current_scope":scope(source)}
    return {**review,"preview_fingerprint":digest({"adoption":adoption,"scope":review})}


@frappe.whitelist()
def get_purchase_source_detail(name, fresh=0):
    doc = _source(name)
    source = _json(doc.get(SOURCE_FIELD)); evidence = _json(doc.get(EVIDENCE_FIELD))
    if frappe.utils.cint(fresh):
        # Explicit re-read only: no synchronization or native-document writes.
        source,evidence=_fresh_source(doc)
    version=_version(source,evidence)
    adaptation = _adaptation_preview(doc,source,evidence)
    creation_version = _version(source,evidence,adaptation)
    scope_review = _recheck_plan(doc,source)
    recheck_version = _version(source,evidence,scope_review)
    source["source_url"] = "https://aflow.dingtalk.com/dingtalk/mobile/homepage.htm?" + urlencode({"procInstId":source["oa_identity"]["process_instance_id"]})
    from .unified_purchase_service import cashier_evidence_readable
    readable = cashier_evidence_readable()
    finance = frappe.session.user == "Administrator" or bool({"Accounts User","Accounts Manager"} & set(frappe.get_roles()))
    if not readable:
        evidence = {"payment_evidence_status":"hidden", "paid_amount":None,"issues":["没有权限查看出纳付款证据"]}
    else:
        evidence = {**evidence,"attachments":[{**a,"attachment_id":a.get("source_id"),"downloadable":bool(finance and re.fullmatch(r"/api/integrations/erp/purchase-expenses/(attachments|payment-vouchers)/[1-9][0-9]{0,18}",a.get("url") or ""))} for a in evidence.get("attachments",[])]}
    reconciliation=_json(doc.get(RECONCILIATION_FIELD))
    reconciled=bool(readable and reconciliation.get("verified") and reconciliation.get("evidence_version")==evidence.get("version"))
    notice="已核对对应 ERP 付款记录；再次付款仍会复查有效状态" if reconciled else "历史付款尚未核对为 ERP 入账；不会自动补记付款"
    roles = _role_projection(doc,source)
    return {"name":doc.name,"version":version,"creation_version":creation_version,
            "adaptation":adaptation,"adopted_adaptation":_json(doc.get(source_rules.ADOPTION_FIELD)).get("plan") if doc.purchase_order else None,
            "can_manage_rules":bool(frappe.session.user == "Administrator" or "System Manager" in frappe.get_roles()),
            "scope_review":scope_review,"recheck_version":recheck_version,
            "source":source,"cashier":evidence,"purchase_order":doc.purchase_order,"pending_reason":doc.get(PENDING_FIELD),
            "target_company":roles["purchasing_company"] or roles["buyer_company_proposal"], **roles,
            "can_write":doc.has_permission("write"),"can_create":frappe.has_permission("Purchase Order","create"),
            "reconciliation_notice":notice if readable else None,
            "can_reconcile":bool(doc.purchase_order and doc.has_permission("write") and readable and finance)}


@frappe.whitelist()
def preview_purchase_source_adaptation(name=None, rule=None, from_date="2026-09-25", to_date=None):
    """Read current evidence and native master states without a receipt or cache write."""
    from .operating_expenses import _manager
    _manager()
    if name:
        doc = _source(name)
        source, evidence = _fresh_source(doc)
        try:
            return source_rules.preview(source,evidence,doc=doc,candidate=frappe.parse_json(rule) if rule else None)
        except ValueError as error:
            frappe.throw(str(error))
    if rule:
        frappe.throw(_i18n("请在一张明确的采购申请中预览同类规则"))
    start = frappe.utils.getdate(from_date)
    end = frappe.utils.getdate(to_date) if to_date else frappe.utils.getdate()
    if str(start) < "2026-09-25" or str(end) >= "2027-01-01" or end < start:
        frappe.throw(_i18n("本次补建范围仅限2026年9月25日起的采购申请"))
    snapshots, _ = _read_sync_snapshot(datetime.now(UTC).isoformat())
    rows = []
    for source, evidence, _ in snapshots:
        if not str(start) <= source["apply_date"] <= str(end):
            continue
        try:
            doc = _cached_doc(source)
            if doc:
                doc = _source(doc.name)
            else:
                if not frappe.has_permission(DOCTYPE,"read"):
                    frappe.throw(_i18n("没有权限查看采购来源"),frappe.PermissionError)
                _source_read_fields()
            bound = doc.get("purchase_order") if doc else None
            if bound:
                _native("Purchase Order",bound)
            plan = None if bound else source_rules.preview(source,evidence,doc=doc)
        except frappe.PermissionError:
            continue
        rows.append({"name":doc.name if doc else None,"source_id":source["source_id"],"business_id":source.get("business_id"),
                     "source_version":source["version"],"purchase_order":bound,
                     "adaptation":plan})
    return {"from_date":str(start),"to_date":str(end),"count":len(rows),"rows":rows,"preview_fingerprint":digest(rows)}


@frappe.whitelist(methods=["POST"])
@procurement_entry
def confirm_purchase_source_rule(name, rule, expected_preview_fingerprint):
    from .operating_expenses import _manager
    _manager()
    doc = _source(name,write=True)
    source, evidence = _fresh_source(doc)
    if not source.get("eligible") or doc.get("purchase_order"):
        frappe.throw(_i18n("仅可为已审批且未生成订单的采购申请确认同类规则"))
    try:
        return source_rules.confirm_rule(source,evidence,frappe.parse_json(rule),expected_preview_fingerprint,doc=doc)
    except ValueError as error:
        frappe.throw(str(error))


@frappe.whitelist()
def download_source_attachment(name, attachment_id, version):
    from .operating_expenses import _finance, _request
    _finance(); doc = _source(name)
    from .unified_purchase_service import cashier_evidence_readable
    if not cashier_evidence_readable():
        frappe.throw(_i18n("没有权限查看出纳付款证据"),frappe.PermissionError)
    source,evidence = _fresh_source(doc)
    candidates = [a for a in evidence.get("attachments",[]) if a.get("source_id") == attachment_id and a.get("version") == version]
    if len(candidates) != 1:
        frappe.throw(_i18n("附件不存在或已变化，请刷新核对"))
    file = candidates[0]; path = file.get("url") or ""
    if not re.fullmatch(r"/api/integrations/erp/purchase-expenses/(attachments|payment-vouchers)/[1-9][0-9]{0,18}",path):
        frappe.throw(_i18n("附件路径无效，请在钉钉原单核对"))
    frappe.response.update(type="download",filename=frappe.utils.strip_html(file.get("filename") or "附件"),filecontent=_request(path,download=True))


def _cached_doc(source):
    name = frappe.db.get_value(DOCTYPE,{SOURCE_ID_FIELD:source["source_id"]},"name")
    if name:
        return frappe.get_doc(DOCTYPE,name,for_update=True)
    candidates=frappe.get_all(DOCTYPE,filters={"process_instance_id":source["oa_identity"]["process_instance_id"]},fields=["name"],limit_page_length=2)
    if len(candidates)>1:
        frappe.throw(_i18n("本地采购来源实例重复，不能自动选取关联；请管理员核对"))
    name=candidates[0].name if candidates else None
    if name:
        if source.get("instance_count",1) != 1:
            frappe.throw(_i18n("旧采购来源企业身份不唯一，不能自动绑定"))
        doc = frappe.get_doc(DOCTYPE,name,for_update=True)
        old = _json(doc.get(SOURCE_FIELD))
        if old.get("oa_identity") and old["oa_identity"] != source["oa_identity"]:
            frappe.throw(_i18n("旧采购来源身份冲突"))
        return doc
    return None


def _native_header_text(meta, fieldname, value):
    """Project descriptive evidence only when the installed short field can hold it.

    The complete value stays in SOURCE_FIELD. Never truncate bank details, alter
    identifiers, or overwrite an existing manually reviewed native header.
    """
    if value is None:
        return None
    field = meta.get_field(fieldname)
    column_type, default_length = frappe.db.type_map.get(field.fieldtype, (None, None))
    limit = frappe.utils.cint(field.length) or frappe.utils.cint(default_length)
    if column_type == "varchar" and limit and len(frappe.utils.cstr(value)) > limit:
        return None
    return value


def _cache_source(source,evidence,company=None):
    doc = _cached_doc(source)
    values = {SOURCE_ID_FIELD:source["source_id"],SOURCE_FIELD:json.dumps(source,ensure_ascii=False,default=str),
              EVIDENCE_FIELD:json.dumps(evidence,ensure_ascii=False,default=str),"approval_status":source.get("status"),
              "source_invalid":int(not source.get("eligible")),"source_pending":int(source.get("status") != "COMPLETED")}
    if doc:
        values["source_stale"] = int(bool(doc.get(BOUND_FIELD) and doc.get(BOUND_FIELD) != source["version"]))
        frappe.db.set_value(DOCTYPE,doc.name,values,update_modified=False)
        return doc.name
    doc = frappe.new_doc(DOCTYPE)
    meta = doc.meta
    doc.update({**values,"process_instance_id":source["process_instance_id"],"process_code":source["process_code"],
                # Frappe field:oa_code keeps this equal to the internal document name.
                # The immutable original approval number remains in SOURCE_FIELD.
                "oa_code":"DT-PUR-"+digest(source["source_id"]),"apply_date":source["apply_date"],"creator":_native_header_text(meta,"creator",source.get("originator_user_name")),
                "execution_region":source.get("region"),"currency":source.get("currency"),"description":source.get("description"),
                "payee":_native_header_text(meta,"payee",source.get("payee")),"target_company":None,PROPOSAL_FIELD:company,CONFIRMED_FIELD:0,"sync_status":"Pending Purchase Order",
                "backfill_imported":1,"items_json":json.dumps(source["items"],ensure_ascii=False),"detail_total_amount":source.get("detail_total_amount"),
                "payment_amount":source.get("requested_amount")})
    with managed_write():
        doc.insert(ignore_permissions=True,set_name="DT-PUR-"+digest(source["source_id"]))  # Source cache only, exact identity name.
    return doc.name


def _invalidate_source(source,reason,row=None):
    source=copy.deepcopy(source)
    source.update(eligible=False,issues=list(dict.fromkeys([*(source.get("issues") or []),reason])))
    if row:
        source.update(status=row.get("status"),result=row.get("result"),updated_at=str(row.get("updated_at") or ""))
    source.pop("version",None)
    source["version"]=digest(source)
    return source


def _reconcile_cached_sources(connection,until,seen,cashier):
    # A source moved outside the year/template, or physically removed upstream,
    # must not remain actionable merely because it disappeared from the scan.
    # Preserve originals and all manual native fields; only invalidate the cache.
    cached=frappe.get_all(DOCTYPE,filters={SOURCE_ID_FIELD:["!=",""]},fields=["name",SOURCE_FIELD],limit_page_length=0)
    if len(cached)>MAX_SOURCE_ROWS:
        frappe.throw(_i18n("已接入采购来源超过核对上限，请管理员核对"))
    snapshots = []
    for record in cached:
        old=_json(record.get(SOURCE_FIELD))
        if not old or old["source_id"] in seen:
            continue
        rows,_=oa.read_page(connection,1,until,identity=old["oa_identity"],process_codes=contract.PROCESS_CODES)
        occurrences = contract._field_occurrences(rows[0]) if len(rows) == 1 else None
        if len(rows)==1 and contract.in_scope(rows[0],occurrences=occurrences):
            source=_normalize(rows[0],occurrences=occurrences)
            if source["oa_identity"] != old["oa_identity"]:
                frappe.throw(_i18n("采购来源身份不符"))
        else:
            source=_invalidate_source(old,"关联钉钉采购原单不存在或已移出中国/墨西哥2026范围，请先核对",rows[0] if len(rows)==1 else None)
        snapshots.append((source,contract.payment_evidence(source,cashier),None))
    return snapshots


def _read_sync_snapshot(until):
	from .operating_expenses import _oa_connection, _request

	snapshots = []
	count = 0
	cursor = None
	seen = set()
	cashier = _cashier_snapshot(until)
	connection = _oa_connection()._connection
	bridge_companies = set(
		frappe.get_all(
			"Company", filters={"name": ["in", sorted(set(oa.COMPANY_BRIDGES.values()))]}, pluck="name"
		)
	)
	for _ in range(MAX_SOURCE_ROWS // SOURCE_PAGE_SIZE):
		rows, cursor = oa.read_page(
			connection, SOURCE_PAGE_SIZE, until, cursor=cursor, process_codes=contract.PROCESS_CODES
		)
		prepared = [(row, contract._field_occurrences(row)) for row in rows]
		scoped = [
			(row, occurrences, contract.in_scope(row, occurrences=occurrences))
			for row, occurrences in prepared
		]
		selected = [(row, occurrences) for row, occurrences, in_scope in scoped if in_scope]
		applicants = [oa.resolution_applicant(row, cashier) for row, _ in selected]
		resolutions = (
			_request("/api/integrations/erp/resolve-applicant-companies", data={"applicants": applicants})[
				"items"
			]
			if applicants
			else []
		)
		if len(resolutions) != len(applicants):
			frappe.throw(_i18n("采购申请人归属数量不符"))
		for (row, occurrences), applicant, resolution in zip(selected, applicants, resolutions, strict=False):
			if any(resolution.get(k) != applicant[k] for k in ("user_id", "employee_name")):
				frappe.throw(_i18n("采购申请人归属身份不符"))
			source = _normalize(row, occurrences=occurrences)
			legal = (
				oa.COMPANY_BRIDGES.get(resolution.get("assigned_department"))
				if resolution.get("status") == "matched"
				else None
			)
			company = legal if legal in bridge_companies else None
			if source["source_id"] in seen:
				frappe.throw(_i18n("采购来源身份重复，请核对本次快照"))
			snapshots.append((source, contract.payment_evidence(source, cashier), company))
			count += 1
			seen.add(source["source_id"])
		# Retain previously imported but withdrawn/out-of-scope applications as audit evidence.
		for row, _, in_scope in scoped:
			if in_scope:
				continue
			doc = _cached_doc(
				{
					"source_id": oa.application_id(row),
					"oa_identity": {
						"corp_id": row["corp_id"],
						"process_instance_id": row["process_instance_id"],
					},
				}
			)
			if doc and doc.get(SOURCE_FIELD):
				old = _invalidate_source(
					_json(doc.get(SOURCE_FIELD)), "钉钉采购原单已移出中国/墨西哥2026范围，请先核对", row
				)
				snapshots.append((old, contract.payment_evidence(old, cashier), None))
				seen.add(old["source_id"])
		if cursor is None:
			snapshots.extend(_reconcile_cached_sources(connection, until, seen, cashier))
			if len(snapshots) > MAX_SOURCE_ROWS:
				frappe.throw(_i18n("采购来源及已接入来源超过本次同步上限"))
			return snapshots, count
	frappe.throw(_i18n("采购來源超过本次同步上限"))


def _sync_replay(receipt):
    from . import purchase_operation as operation
    operation.replay_artifacts(receipt)
    for acknowledged in receipt.get("acknowledgements",[]):
        doc = _source(acknowledged["name"])
        if _version(_json(doc.get(SOURCE_FIELD)),_json(doc.get(EVIDENCE_FIELD))) != acknowledged["version"] or doc.get("purchase_order") != acknowledged.get("purchase_order"):
            operation.reject("采购来源缓存或关联已变化，请重新同步核对", "replay_source_changed")
        if acknowledged.get("purchase_order"):
            _native("Purchase Order",acknowledged["purchase_order"])
    return {**receipt["result"], "documents":[{k:row[k] for k in ("doctype","name")} for row in receipt["documents"]], "reused":True}


def _approval_pending_reason(source):
    if source.get("deleted_at"):
        label = "原申请已删除或撤销"
    elif str(source.get("result") or "").lower() in {"refuse","reject","disagree"}:
        label = "审批已拒绝"
    else:
        label = {"RUNNING":"审批中","TERMINATED":"审批已终止","CANCELED":"审批已撤销"}.get(source.get("status"),"来源待核对")
    return label + "，不创建物料或采购订单"


@frappe.whitelist(methods=["POST"])
def sync_purchase_sources(request_id=None):
	from . import purchase_operation as operation
	from .operating_expenses import _manager

	_manager()
	if not frappe.db.exists("DocType", DOCTYPE) or not frappe.get_meta(DOCTYPE).has_field(PENDING_FIELD):
		frappe.throw(_i18n("采购来源元数据未安装"))

	def write():
		until = datetime.now(UTC).isoformat()
		with frappe.cache.lock("purchase-source-sync:" + frappe.local.site, timeout=300):
			snapshots, count = _read_sync_snapshot(until)
			result = {
				"count": count,
				"until": until,
				"created": 0,
				"already_linked": 0,
				"pending": 0,
				"invalid": 0,
				"new_items": 0,
				"outcomes": [],
			}
			documents = []
			acknowledgements = []
			for source, evidence, proposal in snapshots:
				existing = _cached_doc(source)
				adaptation = _adaptation_preview(existing or frappe._dict(),source,evidence)
				in_window = source.get("apply_date", "") >= "2026-09-25"
				plan, reason = (
					(None, "")
					if (existing and existing.get("purchase_order")) or adaptation is not None or not in_window
					else _automatic_order(existing or frappe._dict(), source)
				)
				if not in_window and not (existing and existing.get("purchase_order")):
					reason = "早于2026年9月25日，不在本次自动补建范围"
				name = _cache_source(source, evidence, proposal)
				doc = _source(name, write=True)
				if doc.get("purchase_order"):
					_native("Purchase Order", doc.purchase_order)
					result["already_linked"] += 1
				elif not source.get("eligible"):
					result["invalid"] += 1
					reason = _approval_pending_reason(source)
				elif adaptation is not None:
					created, reason = _create_adapted_order(doc,source,evidence,adaptation)
					if created:
						documents.append({key:created[key] for key in ("doctype","name")})
						result["created"] += 1
						result["new_items"] += len(created["created_items"])
					else:
						result["pending"] += 1
				elif plan is None:
					result["pending"] += 1
				else:
					order, beneficiary, project = plan
					documents.append(
						_insert_purchase_order(
							doc,
							order,
							source,
							evidence,
							beneficiary_company=beneficiary,
							project=project,
							automatic=True,
						)
					)
					result["created"] += 1
				# Preserve linked manual fields and their old bound_version. A
				# changed source stays stale until the existing human recheck.
				if not doc.get("purchase_order") and plan is None:
					frappe.db.set_value(DOCTYPE, name, PENDING_FIELD, reason, update_modified=False)
				bound = frappe.db.get_value(DOCTYPE, name, "purchase_order")
				if in_window:
					result["outcomes"].append({"name":name,"business_id":source.get("business_id"),"purchase_order":bound,
						"status":"linked" if bound else "pending" if source.get("eligible") else "ineligible","reason":reason if not bound else ""})
				acknowledgements.append(
					{
						"doctype": DOCTYPE,
						"name": name,
						"version": _version(source, evidence),
						"purchase_order": bound,
					}
				)
			return {**result, "result": result, "documents": documents, "acknowledgements": acknowledgements}

	return operation.run(
		request_id or str(uuid.uuid4()),
		{"operation": "sync_purchase_sources", "process_codes": contract.PROCESS_CODES},
		write,
		_sync_replay,
		acknowledge_validation=True,
	)


def cashier_payment_reason(evidence,reconciliation=None):
    if reconciliation and reconciliation.get("verified"):
        return ""
    paid=contract.oa.exact_amount(evidence.get("paid_amount"))
    if evidence.get("payment_evidence_status") != "recorded" or paid is None:
        return "出纳付款证据待核对，快捷付款已禁用；请先核对历史付款"
    if Decimal(paid)>0:
        return "存在出纳历史付款，请先核对并关联 ERP 付款记录，避免重复付款"
    return ""


def _verify_reconciliation(doc,evidence,names):
    from .purchase_payment_service import PI_FIELDS, _invoice_names, _invoice_row, _read
    if not isinstance(names,list) or not names or len(names)>100 or len(set(names))!=len(names) or any(not isinstance(n,str) for n in names):
        frappe.throw(_i18n("请明确选择不重复的原生付款单"))
    if evidence.get("payment_evidence_status") != "recorded" or contract.oa.exact_amount(evidence.get("paid_amount")) is None:
        frappe.throw(_i18n("出纳付款证据未确认，不能核对为已入账"))
    order=_native("Purchase Order",doc.purchase_order)
    invoices=set(_invoice_names("Purchase Order",order.name)); allocated=Decimal(0)
    for name in names:
        pe=_read("Payment Entry",name,{"company","party","party_type","payment_type","references","paid_from_account_currency","paid_to_account_currency","docstatus"})
        from .purchase_payment_service import _require_fields
        _require_fields("Payment Entry Reference",{"reference_doctype","reference_name","allocated_amount"},"Payment Entry")
        if (pe.docstatus!=1 or pe.payment_type!="Pay" or pe.party_type!="Supplier" or pe.party!=order.supplier
            or pe.company!=order.company or pe.paid_from_account_currency!=evidence.get("currency") or pe.paid_to_account_currency!=evidence.get("currency")):
            frappe.throw(_i18n("付款单必须为本公司、本供应商、同币种的已提交付款"))
        matched=Decimal(0)
        for ref in pe.references:
            if ref.reference_doctype=="Purchase Order" and ref.reference_name==order.name:
                matched+=Decimal(str(ref.allocated_amount))
            elif ref.reference_doctype=="Purchase Invoice" and ref.reference_name in invoices:
                invoice=_read("Purchase Invoice",ref.reference_name,PI_FIELDS)
                if invoice.docstatus!=1 or invoice.is_return or _invoice_row(invoice,"Purchase Order",order.name)["shared"]:
                    frappe.throw(_i18n("共享、退货或未提交应付不能核对为本订单历史付款"))
                matched+=Decimal(str(ref.allocated_amount))
        if matched<=0:
            frappe.throw(_i18n("付款单没有明确核销当前采购订单或对应应付"))
        allocated+=matched
    if allocated!=Decimal(evidence["paid_amount"]):
        frappe.throw(_i18n("所选付款核销金额与出纳实付不一致，请先核对，不自动补记或分摊"))
    return {"verified":True,"payment_entries":names,"evidence_version":evidence["version"],"amount":str(allocated),"currency":evidence["currency"]}


@frappe.whitelist(methods=["POST"])
def confirm_historical_payments(name,expected_version,payment_entries):
    from .operating_expenses import _finance
    _finance(); doc=_source(name,write=True)
    if not doc.purchase_order:
        frappe.throw(_i18n("请先明确关联采购订单"))
    source,evidence=_fresh_source(doc)
    _assert_current(source,evidence,expected_version)
    reconciliation=_verify_reconciliation(doc,evidence,frappe.parse_json(payment_entries))
    reconciliation.update(confirmed_by=frappe.session.user,confirmed_at=frappe.utils.now())
    frappe.db.set_value(DOCTYPE,doc.name,RECONCILIATION_FIELD,json.dumps(reconciliation,ensure_ascii=False))
    doc.add_comment("Comment","出纳历史付款已核对原生付款记录；本操作未生成、提交或修改付款单")
    return reconciliation


def payment_guard(order):
    name=order.get("custom_oa_purchase_expense")
    if not name or not frappe.get_meta(DOCTYPE).has_field(SOURCE_FIELD):
        return
    cached=frappe.db.get_value(DOCTYPE,name,SOURCE_FIELD)
    if not cached:
        return
    doc=_source(name); source,evidence=_fresh_source(doc)
    if not source.get("eligible") or doc.get(BOUND_FIELD) != source["version"]:
        frappe.throw(_i18n("采购来源已变化或未通过，请先复核关联"))
    reconciliation=_json(doc.get(RECONCILIATION_FIELD))
    if reconciliation.get("evidence_version")==evidence.get("version") and reconciliation.get("payment_entries"):
        reconciliation=_verify_reconciliation(doc,evidence,reconciliation["payment_entries"])
    else:
        reconciliation=None
    reason=cashier_payment_reason(evidence,reconciliation)
    if reason:
        frappe.throw(reason)


def validate_managed_source(doc,method=None):
    if _managed.get():
        return
    old=doc.get_doc_before_save()
    # This flag grants the narrow imported-company correction exception, even
    # if the raw source is attached later. Native defaults None/0 are equivalent.
    flag_values = (None,"",0,1,"0","1")
    if doc.get("backfill_imported") not in flag_values or frappe.utils.cint(doc.get("backfill_imported")) != frappe.utils.cint(old.get("backfill_imported") if old else 0):
        frappe.throw(_i18n("采购来源导入标记仅允许系统设置"),frappe.PermissionError)
    confirmation_changed = doc.get(CONFIRMED_FIELD) not in flag_values or frappe.utils.cint(doc.get(CONFIRMED_FIELD)) != frappe.utils.cint(old.get(CONFIRMED_FIELD) if old else 0)
    confirmation_changed = confirmation_changed or any(
        str(doc.get(field) or "") != str((old.get(field) if old else None) or "")
        for field in (CONFIRMED_BY_FIELD,CONFIRMED_ON_FIELD))
    if confirmation_changed:
        frappe.throw(_i18n("采购公司确认信息仅允许办理入口设置"),frappe.PermissionError)
    if not doc.get(SOURCE_FIELD) and not (old and old.get(SOURCE_FIELD)):
        return
    protected=(SOURCE_FIELD,EVIDENCE_FIELD,SOURCE_ID_FIELD,BOUND_FIELD,RECONCILIATION_FIELD,PENDING_FIELD,source_rules.ADOPTION_FIELD,"purchase_order","target_company",
               BENEFICIARY_FIELD, PROPOSAL_FIELD, PROJECT_FIELD,
               "approval_status","source_invalid","source_pending","source_stale","process_instance_id","process_code")
    if not old or any(old.get(k) != doc.get(k) for k in protected):
        frappe.throw(_i18n("来源及采购关联请使用采购订单列表的核对入口"),frappe.PermissionError)


def protect_managed_source_identity(doc,method=None,*args,**kwargs):
    if doc.get(SOURCE_ID_FIELD) or doc.get(SOURCE_FIELD):
        frappe.throw(_i18n("已接入的采购来源需保留审计身份，不能重命名或删除"),frappe.PermissionError)


def permission_query_conditions(user=None):
    from .operating_expenses import _companies
    user=user or frappe.session.user
    if user == "Administrator": return ""
    companies=_companies(user)
    assigned="(COALESCE(`tabOA Purchase Request`.purchase_order,'')<>'' OR COALESCE(`tabOA Purchase Request`."+CONFIRMED_FIELD+",0)=1)"
    parts=["("+assigned+" AND `tabOA Purchase Request`.target_company IN ("+",".join(frappe.db.escape(c) for c in companies)+"))"] if companies else []
    if "System Manager" in frappe.get_roles(user): parts.append("(NOT "+assigned+" OR COALESCE(`tabOA Purchase Request`.target_company,'')='')")
    restriction="("+" OR ".join(parts)+")" if parts else "1=0"
    return "(COALESCE(`tabOA Purchase Request`.custom_purchase_source_id,'')='' OR "+restriction+")"


def has_permission(doc,user=None,ptype=None):
    # Native role/document permissions are checked separately. A controller
    # hook's None is a denial in Frappe 16, not a neutral result.
    if not doc.get(SOURCE_ID_FIELD): return True
    from frappe.permissions import get_user_permissions
    user=user or frappe.session.user
    if ptype in ("delete","cancel","submit"): return False
    company=_confirmed_company(doc)
    if not company: return user == "Administrator" or "System Manager" in frappe.get_roles(user)
    permissions=get_user_permissions(user).get("Company",[])
    return bool(frappe.has_permission("Company","read",doc=company,user=user) and (not permissions or company in {p.doc for p in permissions}))


@frappe.whitelist(methods=["POST"])
def legacy_create_purchase_order(docname,supplier_name=None,**kwargs):
    # Preserve old public URL but close its unsafe guessed-company/qty1 bypass.
    frappe.throw(_i18n("请在采购订单列表点击“完善/关联”，明确公司、物料、单位和数量后生成草稿"))


def validate_managed_order(order,method=None):
    """Read-only UI metadata cannot protect provenance from native REST saves."""
    if _managed.get():
        return
    old=order.get_doc_before_save()
    before=old.get("custom_oa_purchase_expense") if old else None
    after=order.get("custom_oa_purchase_expense")
    if not (before or after) or not frappe.db.exists("DocType",DOCTYPE) or not frappe.get_meta(DOCTYPE).has_field(SOURCE_ID_FIELD):
        return
    managed=any(frappe.db.get_value(DOCTYPE,name,SOURCE_ID_FIELD) for name in {before,after} if name)
    if managed and (before != after or (old and old.company != order.company)):
        frappe.throw(_i18n("采购来源关联及公司请使用采购订单列表的核对入口"),frappe.PermissionError)


def validate_source_before_submit(order,method=None):
    if order.get("supplier") == source_rules.TEMP_SETTLEMENT_SUPPLIER:
        frappe.throw(_i18n("临时结算供应商待核对，请更换为实际结算供应商后再提交采购订单"))
    name=order.get("custom_oa_purchase_expense")
    if not name or not frappe.get_meta(DOCTYPE).has_field(SOURCE_FIELD) or not frappe.db.get_value(DOCTYPE,name,SOURCE_FIELD):
        return
    doc=_source(name); source,_=_fresh_source(doc)
    if not source.get("eligible") or doc.get(BOUND_FIELD)!=source["version"]:
        frappe.throw(_i18n("采购来源已变化或未审批通过，请先复核关联后再提交"))
    if doc.target_company != order.company:
        frappe.throw(_i18n("采购订单公司与已核对来源不符"))
    adoption = _json(doc.get(source_rules.ADOPTION_FIELD))
    if adoption.get("plan"):
        from .purchase_source_adaptation import scope
        if scope(source) != adoption["plan"].get("scope"):
            frappe.throw(_i18n("原申请组织或采购类型已变化，请先复核来源关联"))


def scheduled_sync():
    if not frappe.conf.get("purchase_source_sync_enabled"):
        return
    user=frappe.session.user
    try:
        import time
        deadline = time.monotonic() + 300  # Share the existing dedicated runner's time budget.
        frappe.set_user("Administrator")
        sync_purchase_sources()
        from .purchase_fulfilment_logistics import refresh_cached_logistics
        refresh_cached_logistics(deadline=deadline)
    finally:
        frappe.set_user(user)
