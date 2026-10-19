"""Read-only cashier history; explicit finance mapping and native JE drafts.

ERP payment registration is isolated in operating_payment_service. Neither
service transfers money, submits vouchers, creates master data, or writes GL.
Cache, mapping and event documents can only be changed inside scoped server work.
"""
from __future__ import annotations

from frappe import _ as _i18n


import json
import hashlib
import os
import ipaddress
import socket
from datetime import date, datetime, timezone
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal, ROUND_HALF_UP

import frappe
import requests
from frappe.utils import now_datetime, getdate

from deeplinkerp_branding.services.operating_expense_contract import (
    SOURCE_SYSTEM, SOURCE_URL, MAX_JSON_BYTES, attachment_path, digest, identifier,
    validate_source, money, expense_facts, event_fingerprint, payment_decision, payment_status, worklist_summary, QUICK_TABS,
)
from deeplinkerp_branding.services.purchase_payment_service import _require_fields
from deeplinkerp_branding.services.unified_purchase_service import _require_export_permission

SOURCE = "Operating Expense Source"
MAPPING = "Operating Expense Mapping"
EVENT = "Operating Expense Event"
SETTINGS = "Operating Expense Sync Settings"
_managed = ContextVar("operating_expense_managed_write", default=False)
# The dedicated projection is not an escape hatch around native field scopes.
# Extra contract fields inherit the scope of their corresponding native scalar;
# opaque/unrecognized source JSON is deliberately never returned to clients.
RAW_SOURCE_PERMISSIONS = {
    **{field: field for field in ("source_id", "source_system", "source_company", "source_sheet", "application_type", "application_type_raw", "applicant", "payee_name", "summary", "request_date", "currency", "amount", "paid_amount", "pending_amount", "source_status", "updated_at")},
    "version": "source_version", "original_source_amount": "amount", "original_source_currency": "currency",
    "request_date_raw": "request_date", "needed_payment_date": "request_date", "original_url": "source_id",
    "approval_no": "source_id", "dingding_id": "source_id", "source_request_id": "source_id",
    "storage_precision_warning": "issues", "source_conflict": "issues", "currency_conflict": "issues",
    "approvals": "approval_state", "payments": "source_status", "attachments": "source_id",
    "cashier_source_id": "source_id", "company_resolution": "source_company", "payment_evidence_status": "source_status",
    "cashier_reported_payment_status": "source_status",
    "oa_identity": "source_id", "applicant_user_id": "applicant",
    "workflow_summary": "approval_state", "payment_eligibility": "approval_state",
    "current_approver": "approval_state", "approval_state": "approval_state",
    "project": "issues",
    "account_nature": "issues", "general_manager_approval": "approval_state", "remark": "summary",
    "projection_conflicts": "issues",
}
SOURCE_FIELDS = set(RAW_SOURCE_PERMISSIONS.values()) | {"company", "issues", "effective_application_type"}


@contextmanager
def managed_write():
    token = _managed.set(True)
    try:
        yield
    finally:
        _managed.reset(token)


def validate_managed_document(doc):
    if not _managed.get():
        frappe.throw(_i18n("运营费用来源及财务确认只能通过专用接口修改"), frappe.PermissionError)


def _manager():
    if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
        frappe.throw(_i18n("仅系统管理员可管理同步"), frappe.PermissionError)


def _finance():
    if frappe.session.user != "Administrator" and not ({"Accounts User", "Accounts Manager"} & set(frappe.get_roles())):
        frappe.throw(_i18n("需要财务权限"), frappe.PermissionError)


def _read(doctype, name, fields=(), for_update=False):
    doc = frappe.get_doc(doctype, name, for_update=for_update)
    doc.check_permission("read")
    _require_fields(doctype, set(fields))
    return doc


def _companies(user=None):
    user = user or frappe.session.user
    # get_list applies native roles, User Permissions and Company field permissions.
    if user != frappe.session.user:
        from frappe.permissions import get_user_permissions
        permissions = get_user_permissions(user).get("Company", [])
        if permissions:
            return [p.doc for p in permissions]
        return []
    _require_fields("Company", {"name"})
    return frappe.get_list("Company", pluck="name", limit_page_length=0)


def has_permission(doc, user=None, ptype=None):
    if ptype not in (None, "read", "export", "select", "print"):
        return False
    user = user or frappe.session.user
    if doc.doctype == SOURCE and user != "Administrator":
        from frappe.model import get_permitted_fields
        if not SOURCE_FIELDS <= set(get_permitted_fields(SOURCE, user=user, permission_type="read")):
            return False
    if not doc.get("company"):
        return user == "Administrator" or "System Manager" in frappe.get_roles(user)
    if not frappe.has_permission("Company", "read", doc=doc.company, user=user):
        return False
    from frappe.permissions import get_user_permissions
    company_permissions = get_user_permissions(user).get("Company", [])
    return not company_permissions or doc.company in {p.doc for p in company_permissions}


def permission_query_conditions(user=None):
    user = user or frappe.session.user
    if user == "Administrator":
        return ""
    companies = _companies(user)
    company_sql = ",".join(frappe.db.escape(c) for c in companies)
    clauses = ["company IN (" + company_sql + ")"] if companies else []
    if "System Manager" in frappe.get_roles(user):
        clauses.append("COALESCE(company, '') = ''")
    return "(" + " OR ".join(clauses) + ")" if clauses else "1=0"


def _source(name, write=False):
    identifier(name)
    doc = frappe.get_doc(SOURCE, name, for_update=write)
    doc.check_permission("read")
    _require_fields(SOURCE, SOURCE_FIELDS)
    if doc.company:
        _read("Company", doc.company, {"name", "default_currency"})
    else:
        _manager()
    return doc


def _settings(for_update=False):
    if for_update:
        # Native locking reads cover both Single values and company-map children.
        # Every settings writer must take this lock before any source row lock.
        return frappe.get_doc(SETTINGS, SETTINGS, for_update=True)
    return frappe.get_single(SETTINGS)


def _maps(settings):
    return {row.source_company: row.company for row in settings.company_mappings or []}


def _mapped_company(item, maps):
    # Displayed source organization does not silently change bookkeeping rules.
    legal = item.get("company_mapping_source", item.get("source_company"))
    return maps.get("source:" + item["source_id"]) or (maps.get(legal) if isinstance(legal, str) else None)


def _save(doc):
    with managed_write():
        return doc.save(ignore_permissions=True)


def _assert_financial_fields(record, expected, notice):
    for field, value in expected.items():
        actual = record.get(field)
        if isinstance(value, dict):
            actual = json.loads(actual or "{}")
            matches = actual == value
        else:
            matches = str("" if actual is None else actual) == str("" if value is None else value)
        if not matches:
            frappe.throw(notice)


def _audit_financial(operation, source, document, amount, result, *, error=None, stage=None,
                     company=None, error_code="financial_postcondition_failed"):
    # This records validation, not a claim that the RPC already committed.
    # Do not copy exception text or private party/bank/form data into this log.
    # The original exception is re-raised by the caller for native RPC handling.
    entry = {"operation": operation, "source": source, "document": document,
             "amount": str(amount or ""), "result": result}
    if company is not None:
        entry["company"] = company
    if error is not None:
        entry.update(error_type=type(error).__name__, error_code=error_code, stage=stage)
    frappe.logger("operating_finance", allow_site=True).info(entry)


@frappe.whitelist()
def get_sync_settings():
    _manager()
    settings = _settings()
    return {key: settings.get(key) for key in ("enabled", "changed_since", "until", "last_sync_at", "last_error", "preview_fingerprint")} | {
        "company_mappings": _maps(settings), "token_configured": bool(settings.get_password("api_token", raise_exception=False)), "source_url": SOURCE_URL,
        "source_mode": _source_mode(),
    }


@frappe.whitelist(methods=["POST"])
def save_sync_settings(company_mappings, api_token=None):
    _manager()
    values = frappe.parse_json(company_mappings)
    if not isinstance(values, dict) or len(values) > 200:
        frappe.throw(_i18n("公司映射格式无效"))
    rows = []
    for raw, company in values.items():
        if not isinstance(raw, str) or not raw.strip() or len(raw) > 200:
            frappe.throw(_i18n("必须明确来源法律公司"))
        _read("Company", company, {"name"})
        # Existing mapped/drafted documents cannot be reassigned by changing sync settings.
        rows.append({"source_company": raw, "company": company})
    if api_token is not None:
        if not isinstance(api_token, str) or not 8 <= len(api_token) <= 500:
            frappe.throw(_i18n("同步密钥格式无效"))
    settings = _settings(for_update=True)
    # The settings editor owns organization mappings, not per-application
    # decisions saved by another manager since that editor was opened.
    overrides = {raw: legal for raw, legal in _maps(settings).items() if raw.startswith("source:")}
    submitted = {row["source_company"]: row["company"] for row in rows}
    if any(submitted.get(raw, legal) != legal for raw, legal in overrides.items()):
        frappe.throw(_i18n("单据法律公司请在对应申请中核对，不能通过同步设置覆盖"))
    submitted.update(overrides)
    settings.set("company_mappings", [{"source_company": raw, "company": legal} for raw, legal in submitted.items()])
    if api_token is not None:
        settings.api_token = api_token
    settings.enabled = 0
    settings.preview_fingerprint = None
    _save(settings)
    return get_sync_settings()


def _base_url():
    override = frappe.conf.get("operating_expense_qa_url")
    if override:
        if frappe.local.site != "operating-expenses-qa.localhost" or frappe.conf.db_host != "db" or override != "http://host.docker.internal:64244":
            frappe.throw(_i18n("测试来源地址仅限专用隔离站点"))
        return override
    try:
        addresses = socket.getaddrinfo("payment.yueweiportal.com", 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(address[4][0]).is_global or ipaddress.ip_address(address[4][0]).is_multicast for address in addresses):
            frappe.throw(_i18n("来源主机必须解析为公开网络地址"))
    except OSError:
        frappe.throw(_i18n("出纳来源地址暂不可用，请稍后重试"))
    return SOURCE_URL


def _request(path, params=None, download=False, data=None):
    resolver = path == "/api/integrations/erp/resolve-applicant-companies"
    payment_write = path in {"/api/integrations/erp/operating-expenses/takeover-preview", "/api/integrations/erp/operating-expenses/takeover-claim"}
    workflow = path == "/api/integrations/erp/operating-expenses/workflow"
    if path not in ("/api/integrations/erp/operating-expenses", "/api/integrations/erp/purchase-expenses") and not (resolver or payment_write or workflow):
        if path.startswith("/api/integrations/erp/purchase-expenses/"):
            from .purchase_source_contract import attachment_path as purchase_attachment_path
            purchase_attachment_path(path)
        else:
            attachment_path(path)
        if path.startswith("/oa-archive/"):
            frappe.throw(_i18n("OA 归档只能通过授权附件标识读取"))
    settings = _settings()
    secret = settings.get_password("api_token", raise_exception=False)
    if not secret:
        frappe.throw(_i18n("同步密钥未配置"))
    limit = 20 * 1024 * 1024 if download else MAX_JSON_BYTES
    try:
        post = resolver or payment_write or (workflow and data is not None)
        transport = requests.post if post else requests.get
        kwargs = {"json": data} if post else {}
        with transport(_base_url() + path, params=params, headers={"Authorization": "Bearer " + secret}, **kwargs,
                          timeout=(5, 20), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise ValueError("source request failed")
            if int(response.headers.get("Content-Length", "0")) > limit:
                raise ValueError("source response too large")
            data = bytearray()
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                if len(data) > limit:
                    raise ValueError("source response too large")
            if download:
                return bytes(data)
            result = json.loads(data)
            versions = {1, 2} if workflow or payment_write or resolver else {1}
            if result.get("schema_version") not in versions or result.get("source_system") != SOURCE_SYSTEM or not isinstance(result.get("items"), list) or len(result["items"]) > 500:
                raise ValueError("invalid source schema")
            if not (resolver or payment_write or workflow) and (not isinstance(result.get("end"), bool) or (not result["end"] and not result.get("next_cursor")) or not result.get("until")):
                raise ValueError("invalid source pagination")
            return result
    except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError):
        # Do not echo remote body, response headers, URLs, credentials or exception text.
        frappe.throw(_i18n("出纳来源暂不可用或协议无效，请稍后重试"))


def _oa_connection():
    from overseas_costing.scripts.import_oa_logistics import _get_postgres_approval_source
    return _get_postgres_approval_source()


def _source_mode():
    mode = frappe.conf.get("operating_expense_source_mode") or "cashier"
    if mode not in {"cashier", "oa_cashier"}:
        frappe.throw(_i18n("运营来源模式无效"))
    return mode


def _cashier_snapshot(until):
    items, cursor = [], None
    for _ in range(40):
        result = _request("/api/integrations/erp/operating-expenses", {"limit": 500, "until": until, **({"cursor": cursor} if cursor else {})})
        items.extend(result["items"])
        if result["end"]:
            if len({i["source_id"] for i in items}) != len(items):
                frappe.throw(_i18n("出纳来源包含重复根标识"))
            return items
        if result["next_cursor"] == cursor:
            frappe.throw(_i18n("出纳来源分页未前进"))
        cursor = result["next_cursor"]
    frappe.throw(_i18n("出纳来源超过本次接入上限，请管理员核查"))


def _oa_cached_identities():
    """Use the persisted exact OA identity; cashier joins can change independently."""
    from deeplinkerp_branding.services import operating_oa_source as oa
    docs = frappe.get_all(SOURCE, filters={"source_system": oa.SOURCE_SYSTEM},
                          fields=["name", "source_json", "source_version"], limit_page_length=20001)
    if len(docs) > 20000:
        frappe.throw(_i18n("运营来源核对超过本次上限，请管理员核查"))
    cached, roots = [], {}
    for doc in docs:
        raw = json.loads(doc.source_json or "{}")
        identity = raw.get("oa_identity")
        if raw.get("source_system") != oa.SOURCE_SYSTEM or not identity:
            continue
        if not isinstance(identity, dict) or not all(isinstance(identity.get(k), str) and identity[k] for k in ("corp_id", "process_instance_id")):
            frappe.throw(_i18n("缓存 OA 身份无效，请管理员核查"))
        key = (identity["corp_id"], identity["process_instance_id"])
        if key in roots:
            frappe.throw(_i18n("缓存 OA 身份重复，请管理员核查"))
        cached.append((doc, raw))
        roots[key] = doc.name
    return cached, roots


def _source_page(params):
    if _source_mode() == "cashier":
        return _request("/api/integrations/erp/operating-expenses", params)
    from deeplinkerp_branding.services import operating_oa_source as oa
    until = params.get("until") or datetime.now(timezone.utc).isoformat()
    if oa.timestamp(until) > datetime.now(timezone.utc):
        frappe.throw(_i18n("来源快照时间无效"))
    cached, roots = _oa_cached_identities()
    cached_raw = {doc.name: raw for doc, raw in cached}
    identity = None
    if params.get("source_id"):
        identity = cached_raw.get(params["source_id"], {}).get("oa_identity")
        if not identity:
            return {"items": [], "end": True, "until": until, "next_cursor": None}
    source = _oa_connection()
    rows, cursor = oa.read_page(source._connection if source else None, params.get("limit", 500), until, cursor=params.get("cursor"), identity=identity)
    # Periodic bounded rescans also observe new cashier payments and employee mappings
    # when the original application itself has not changed. Only changed fingerprints
    # are written to the cache; the checkpoint is never advanced on a failed page.
    selected = [row for row in rows if oa.in_scope(row)]
    cashier = _cashier_snapshot(until)
    manifests = []
    if selected and source:
        with source._connection() as connection:
            with connection.cursor() as query:
                query.execute("SELECT corp_id,process_instance_id,file_id,file_name,archive_status,sha256,actual_size FROM costing_read.attachment_archives_v1 WHERE corp_id=ANY(%s) AND process_instance_id=ANY(%s)",
                              (list({r["corp_id"] for r in selected}), [r["process_instance_id"] for r in selected]))
                manifests = [dict(r) for r in query.fetchall()]
    if selected:
        identities = [{"corp_id": r["corp_id"], "process_instance_id": r["process_instance_id"], "source_id": oa.application_id(r)} for r in selected]
        workflow_items = _request("/api/integrations/erp/operating-expenses/workflow", data={"identities": identities})["items"]
        workflows = {(e.get("corp_id"), e.get("process_instance_id")): e for e in workflow_items}
        expected = {(i["corp_id"], i["process_instance_id"]) for i in identities}
        if len(workflows) != len(workflow_items) or set(workflows) != expected:
            frappe.throw(_i18n("审批来源返回身份或数量不符，请核对"))
        try:
            selected = [oa.with_workflow(r, workflows[(r["corp_id"], r["process_instance_id"])]) for r in selected]
        except ValueError as error:
            frappe.throw(str(error))
    cashier_index = oa.cashier_index(cashier)
    candidates = [oa.cashier_candidates(r, cashier_index) for r in selected]
    manifests_by_identity = {}
    for manifest in manifests:
        manifests_by_identity.setdefault((manifest["corp_id"], manifest["process_instance_id"]), []).append(manifest)
    applicants = [{"corp_id": r["corp_id"], **oa.resolution_applicant(r, cashier, candidates=c)} for r, c in zip(selected, candidates)]
    resolutions = _request("/api/integrations/erp/resolve-applicant-companies", data={"applicants": applicants})["items"] if applicants else []
    if len(resolutions) != len(applicants):
        frappe.throw(_i18n("申请人归属返回数量不符"))
    items = []
    for row, applicant, resolution, candidate in zip(selected, applicants, resolutions, candidates):
        if any(resolution.get(k) != applicant[k] for k in ("corp_id", "user_id", "employee_name")):
            frappe.throw(_i18n("申请人归属返回身份不符"))
        item = oa.merge_application(row, cashier, resolution, candidates=candidate)
        item["attachments"].extend(oa.archive_attachments(row, manifests_by_identity.get((row["corp_id"], row["process_instance_id"]), ())))
        key = (item["oa_identity"]["corp_id"], item["oa_identity"]["process_instance_id"])
        if identity and item["oa_identity"] != identity:
            frappe.throw(_i18n("OA 返回身份不符，请管理员核查"))
        if key in roots:
            item["source_id"] = roots[key]
        else:
            # A proven first join may migrate an existing cashier archive. Never
            # take a root already bound to another OA corporation or instance.
            old = item.get("cashier_source_id")
            if old and frappe.db.exists(SOURCE, old):
                legacy = json.loads(frappe.db.get_value(SOURCE, old, "source_json") or "{}")
                if legacy.get("oa_identity") == item["oa_identity"]:
                    item["source_id"] = old
                elif not legacy.get("oa_identity") and legacy.get("source_system") == SOURCE_SYSTEM:
                    if (legacy.get("identity_conflict") or legacy.get("approval_identity_status") == "conflict"
                            or (legacy.get("process_instance_id") and not oa.matches(row, legacy))
                            or (legacy.get("corp_id") and legacy["corp_id"] != row["corp_id"])
                            or (legacy.get("approval_no") and legacy["approval_no"] != row.get("business_id"))):
                        frappe.throw(_i18n("旧出纳来源身份冲突，请管理员核查"))
                    item["source_id"] = old
            if frappe.db.exists(SOURCE, item["source_id"]):
                existing = json.loads(frappe.db.get_value(SOURCE, item["source_id"], "source_json") or "{}")
                first_cashier_join = item["source_id"] == old and not existing.get("oa_identity") and existing.get("source_system") == SOURCE_SYSTEM
                if existing.get("oa_identity") != item["oa_identity"] and not first_cashier_join:
                    frappe.throw(_i18n("运营来源标识与 OA 身份不符，请管理员核查"))
        item["version"] = digest({k: v for k, v in item.items() if k != "version"})
        items.append(item)
    # Previously cached applications removed from the accepted scope remain audit-visible
    # but blocked; an exact fresh read also refuses them rather than trusting old data.
    if not identity:
        for row in rows:
            if oa.in_scope(row):
                continue
            name = roots.get((row.get("corp_id"), row.get("process_instance_id")), oa.application_id(row))
            raw = frappe.db.get_value(SOURCE, name, "source_json")
            if raw:
                items.append(oa.withdrawn_source(json.loads(raw), row))
    return {"schema_version": 1, "source_system": oa.SOURCE_SYSTEM, "items": items, "end": cursor is None, "until": until, "next_cursor": cursor}


def _reconcile_oa_cache(until, maps):
    """Retain removed/out-of-scope sources as blocked audit rows, never stale payables."""
    from deeplinkerp_branding.services import operating_oa_source as oa
    cached, _ = _oa_cached_identities()
    if not cached:
        return 0
    changed = 0
    with _oa_connection()._connection() as connection:
        for offset in range(0, len(cached), 100):
            batch = cached[offset:offset + 100]
            identities = [raw["oa_identity"] for _, raw in batch]
            with connection.cursor() as query:
                query.execute("SELECT corp_id,process_instance_id,process_code,create_time,updated_at,status,result,"
                              + oa._OPERATING_FORM_VALUES + "::text AS form_component_values FROM costing_read.approval_instances_v2 WHERE corp_id=ANY(%s) AND process_instance_id=ANY(%s)",
                              (list({i["corp_id"] for i in identities}), [i["process_instance_id"] for i in identities]))
                originals = {(r["corp_id"], r["process_instance_id"]): dict(r) for r in query.fetchall()}
            for doc, raw in batch:
                identity = raw["oa_identity"]
                row = originals.get((identity["corp_id"], identity["process_instance_id"]))
                if row and (oa.in_scope(row) or oa.timestamp(row["updated_at"]) > oa.timestamp(until)):
                    continue
                withdrawn = oa.withdrawn_source(raw, row)
                if withdrawn["version"] != doc.source_version:
                    _upsert(withdrawn, maps)
                    changed += 1
    return changed


@frappe.whitelist(methods=["POST"])
def preview_sync():
    _manager()
    result = _source_page({"limit": 100})
    settings = _settings(for_update=True)
    maps = _maps(settings)
    fingerprint = _sync_preview_fingerprint(settings, result)
    settings.preview_fingerprint = fingerprint
    _save(settings)
    return {"preview_fingerprint": fingerprint, "sources": [{"source_id": i.get("source_id"), "source_company": i.get("source_company"), "source_sheet": i.get("source_sheet"), "company": _mapped_company(i, maps), "application_type": i.get("application_type")} for i in result["items"]], "has_more": not result["end"], "enabled": bool(settings.enabled)}


def _sync_preview_fingerprint(settings, result):
    return digest({"company_mappings": _maps(settings), "mode": _source_mode(), "url": _base_url(), "token": digest(settings.get_password("api_token")), "sources": [[i.get("source_id"), i.get("version")] for i in result["items"]], "has_more": not result["end"]})


@frappe.whitelist(methods=["POST"])
def enable_sync(preview_fingerprint):
    _manager()
    result = _source_page({"limit": 100})
    settings = _settings(for_update=True)
    expected = _sync_preview_fingerprint(settings, result)
    if not _maps(settings) or not settings.preview_fingerprint or preview_fingerprint != settings.preview_fingerprint or expected != preview_fingerprint:
        frappe.throw(_i18n("请先预览并确认法律公司映射"))
    settings.enabled = 1
    _save(settings)
    return get_sync_settings()


def _issues(item, mapping=None, include_payment_dates=True):
    issues = []
    try:
        validate_source(item)
    except (ValueError, TypeError, AttributeError):
        issues.append("来源数据无效，请核查")
    decision = payment_decision(item)
    if not decision["can_register_payment"]:
        issues.append(decision.get("reason") or "审批证据待核对")
    if item.get("source_conflict") or item.get("currency_conflict"):
        issues.append("来源归属或币种冲突")
    if item.get("storage_precision_warning"):
        issues.append("原始金额存在精度警告")
    if item.get("original_source_amount") is not None:
        try:
            if money(item["original_source_amount"]) != money(item.get("amount")):
                issues.append("原始金额与归档金额不符")
        except ValueError:
            issues.append("原始金额无效，请核查")
    try:
        money(item.get("amount"), positive=True)
    except ValueError:
        issues.append("金额必须大于零")
    if item.get("application_type") == "unclassified" and (mapping or {}).get("application_type") not in {"payment", "reimbursement"}:
        issues.append("申请类型待分类")
    if include_payment_dates:
        for payment in item.get("payments", []) if isinstance(item.get("payments"), list) else []:
            if isinstance(payment, dict) and payment.get("evidence_status") == "recorded":
                try:
                    money(payment.get("amount"), positive=True)
                except ValueError:
                    issues.append("实际付款金额必须大于零，付款凭证已阻止")
            if isinstance(payment, dict) and not payment.get("payment_date"):
                issues.append("实际付款日期缺失，付款凭证已阻止")
    return issues


def _upsert(item, maps, *, doc=None):
    # A malformed identity cannot safely be cached under an invented key.
    identifier(item.get("source_id"))
    if len(json.dumps(item).encode()) > MAX_JSON_BYTES:
        frappe.throw(_i18n("来源数据过大"))
    name = item["source_id"]
    existing = bool(doc) or frappe.db.exists(SOURCE, name)
    if doc is not None and (doc.doctype != SOURCE or doc.name != name or not doc.flags.for_update):
        frappe.throw(_i18n("来源锁定身份不符"))
    if doc is None:
        doc = frappe.get_doc(SOURCE, name, for_update=True) if existing else frappe.new_doc(SOURCE)
    company = _mapped_company(item, maps)
    issues = _issues(item)
    effective_type = item.get("application_type")
    if existing and doc.company and company and company != doc.company:
        issues.append("法律公司映射已变更，需要财务复核")
        company = doc.company
    if existing and doc.company and not company:
        issues.append("原法律公司映射已移除，需要复核")
        company = doc.company
    if existing and frappe.db.exists(MAPPING, name):
        mapped = json.loads(frappe.db.get_value(MAPPING, name, "mapping_json") or "{}")
        if mapped.get("application_type") in {"payment", "reimbursement"}:
            issues = [issue for issue in issues if issue != "申请类型待分类"]
            if effective_type == "unclassified":
                effective_type = mapped["application_type"]
        try:
            if mapped.get("expense_fingerprint") != digest(expense_facts(item)):
                issues.append("财务确认后的来源事实已变化")
        except (ValueError, TypeError, AttributeError):
            issues.append("财务确认后的来源事实无效")
    doc.update({field: item.get(field) for field in ("source_id", "source_system", "source_company", "source_sheet", "application_type", "application_type_raw", "applicant", "payee_name", "summary", "request_date", "currency", "amount", "paid_amount", "pending_amount", "source_status", "updated_at")})
    doc.source_version = item.get("version")
    for field in ("source_system", "source_sheet", "application_type_raw", "applicant", "payee_name", "currency", "amount", "paid_amount", "pending_amount", "source_status", "updated_at", "source_version"):
        value = doc.get(field)
        if value is not None:
            if not isinstance(value, (str, int)) or len(str(value)) > 140:
                issues.append("来源显示字段无效或过长，原始值保留待核查")
            doc.set(field, str(value)[:140])
    for field in ("source_company", "summary"):
        if doc.get(field) is not None and not isinstance(doc.get(field), str):
            doc.set(field, str(doc.get(field)))
            issues.append("来源文本字段格式无效，原始值保留待核查")
    if doc.application_type not in {"payment", "reimbursement", "unclassified"}:
        doc.application_type = "unclassified"
    # Invalid dates stay in JSON and in visible issues rather than aborting a page.
    try:
        if doc.request_date:
            getdate(doc.request_date)
    except Exception:
        doc.request_date = None
    doc.company = company or ""
    doc.effective_application_type = effective_type if effective_type in {"payment", "reimbursement", "unclassified"} else "unclassified"
    approvals = item.get("approvals")
    doc.approval_state = "eligible" if isinstance(approvals, dict) and isinstance(approvals.get("raw", {}), dict) and approvals.get("eligibility") == "eligible" else "blocked"
    doc.source_json = json.dumps(item, ensure_ascii=False)
    doc.issues = "；".join(issues + ([] if company else ["未映射法律公司"]))
    _save(doc)
    return doc


def _sync_page():
    settings = _settings(for_update=True)
    if not settings.enabled:
        frappe.throw(_i18n("同步未启用"))
    maps = _maps(settings)
    params = {"limit": 500}
    if settings.changed_since:
        params["changed_since"] = settings.changed_since
    if settings.cursor:
        params.update(cursor=settings.cursor, until=settings.until)
    result = _source_page(params)
    seen = set()
    for item in result["items"]:
        root = identifier(item.get("source_id"))
        if root in seen:
            frappe.throw(_i18n("来源页包含重复标识"))
        seen.add(root)
        current_version, current_company = frappe.db.get_value(SOURCE, root, ["source_version", "company"]) or (None, None)
        if current_version != item.get("version") or (current_company or "") != (_mapped_company(item, maps) or ""):
            _upsert(item, maps)
    settings.cursor = result["next_cursor"] if not result["end"] else None
    settings.until = result["until"] if not result["end"] else None
    if result["end"]:
        if _source_mode() == "oa_cashier":
            _reconcile_oa_cache(result["until"], maps)
        settings.changed_since = result["until"]
    settings.last_sync_at = now_datetime()
    settings.last_error = None
    _save(settings)
    return {"count": len(result["items"]), "end": result["end"], "changed_since": settings.changed_since}


@frappe.whitelist(methods=["POST"])
def sync_operating_expenses():
    _manager()
    return _sync_page()


def scheduled_sync():
    if not frappe.db.exists("DocType", SETTINGS) or not _settings().enabled:
        return
    try:
        while _settings().enabled:
            if frappe.flags.get("dedicated_source_sync"):
                from .dedicated_source_sync import ensure_allowed
                ensure_allowed()
            result = _sync_page()
            # Keep the existing cursor/cache transaction together. A later page
            # failure must not discard an already completed checkpoint.
            frappe.db.commit()
            if result["end"]:
                return result
    except Exception:
        frappe.db.rollback()
        notice = "同步失败，请管理员重试"
        # Write only the error field; a concurrent manual page can advance its
        # cursor after our rollback without being overwritten by a stale doc.
        frappe.db.set_single_value(SETTINGS, "last_error", notice)
        frappe.db.commit()
        # Native ScheduledJobType.execute must record Failed, not Complete.
        raise RuntimeError(notice) from None


def _fresh(doc, include_payments=True):
    if not _settings().enabled:
        frappe.throw(_i18n("同步未启用，不能确认财务数据"))
    result = _source_page({"source_id": doc.source_id, "limit": 1})
    if not result["end"] or len(result["items"]) != 1 or result["items"][0].get("source_id") != doc.source_id:
        frappe.throw(_i18n("来源已撤回或已移出允许范围，请重新同步"))
    item = result["items"][0]
    validate_source(item)
    if _mapped_company(item, _maps(_settings())) != doc.company:
        frappe.throw(_i18n("来源法律公司映射已变化，请重新同步并复核"))
    from .operating_payment_service import merge_source
    return merge_source(doc, item) if include_payments else item


def _mapping(doc, for_update=False):
    if not frappe.db.exists(MAPPING, doc.name):
        frappe.throw(_i18n("请先完成财务分类和确认"))
    mapping = _read(MAPPING, doc.name, {"source", "company", "approved_by", "approved_at"}, for_update=for_update)
    if mapping.company != doc.company:
        frappe.throw(_i18n("财务映射公司不一致"))
    values = json.loads(mapping.mapping_json)
    _mapping_access(values)
    return values


def _mapping_access(values):
    """Opaque server JSON never bypasses permissions on its native financial refs."""
    party_type = values.get("party_type")
    if party_type not in {"Supplier", "Employee"}:
        frappe.throw(_i18n("财务往来方类型无效"))
    _read(party_type, values.get("party"), {"name", "company"} if party_type == "Employee" else {"name"})
    accounts = [values.get("payable_account")]
    for row in values.get("expense_lines", []):
        accounts.append(row.get("account"))
        if row.get("cost_center"):
            _read("Cost Center", row["cost_center"], {"company", "is_group"})
        if row.get("project"):
            _read("Project", row["project"], {"company"})
    for row in values.get("payments", {}).values():
        if row.get("bank_account"):
            accounts.append(row["bank_account"])
        if row.get("exchange_difference_account"):
            accounts.append(row["exchange_difference_account"])
        if row.get("cost_center"):
            _read("Cost Center", row["cost_center"], {"company", "is_group"})
    for account in set(accounts):
        _read("Account", account, {"company", "account_currency", "account_type", "root_type", "disabled", "is_group"})


def _account(name, company, types=None):
    account = _read("Account", name, {"company", "account_currency", "is_group", "disabled", "account_type", "root_type"})
    if account.company != company or account.is_group or account.disabled or (types and account.account_type not in types):
        frappe.throw(_i18n("科目公司、类型或状态无效"))
    if not account.account_currency:
        # Match native ERPNext's documented Company currency fallback; no master-data write.
        account.account_currency = _read("Company", company, {"default_currency"}).default_currency
    return account


def _party(item, mapping):
    kind = mapping.get("application_type") if item["application_type"] == "unclassified" else item["application_type"]
    expected = "Employee" if kind == "reimbursement" else "Supplier"
    if kind not in {"payment", "reimbursement"} or mapping.get("party_type") != expected:
        frappe.throw(_i18n("申请类型待分类或往来方类型不符"))
    party = _read(expected, mapping.get("party"), {"name", "company"} if expected == "Employee" else {"name"})
    if expected == "Employee" and party.company != mapping["company"]:
        frappe.throw(_i18n("报销员工公司不符"))
    return expected, party.name


def _rate(value):
    return money(value, precision=9, positive=True)


def _line(account, value, side, mapping, rate, party=None, cost_center=None, project=None):
    amount = money(value, precision=2, positive=True)
    rate = _rate(rate)
    base_currency = _read("Company", mapping["company"], {"default_currency"}).default_currency
    if account.account_currency == base_currency and rate != 1:
        frappe.throw(_i18n("本位币科目汇率必须为 1"))
    base = (amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    row = {"account": account.name, "account_currency": account.account_currency, "exchange_rate": str(rate), "cost_center": "", "project": "",
           side + "_in_account_currency": str(amount), side: str(base)}
    if party:
        row.update(party_type=party[0], party=party[1])
    if cost_center:
        center = _read("Cost Center", cost_center, {"company", "is_group"})
        if center.company != mapping["company"] or center.is_group:
            frappe.throw(_i18n("成本中心公司或状态无效"))
        row["cost_center"] = cost_center
    if project:
        project_doc = _read("Project", project, {"company"})
        if project_doc.company and project_doc.company != mapping["company"]:
            frappe.throw(_i18n("项目公司不符"))
        row["project"] = project
    return row


def _confirmed(value):
    return value is True or (type(value) is int and value == 1)


def _expense_lines(item, mapping):
    issues = _issues(item, mapping, include_payment_dates=False)
    if issues:
        frappe.throw("；".join(issues))
    coverage = mapping.get("existing_erp_coverage_confirmed") if mapping.get("recognition_mode") == "existing" else mapping.get("no_existing_erp_coverage")
    if not _confirmed(mapping.get("actual_incurred")) or not _confirmed(coverage):
        frappe.throw(_i18n("必须确认费用实际发生及现有 ERP 覆盖情况"))
    total = money(item["amount"], precision=2, positive=True)
    party = _party(item, mapping)
    payable = _account(mapping.get("payable_account"), mapping["company"], {"Payable"})
    if payable.account_currency != item["currency"]:
        frappe.throw(_i18n("应付科目币种必须匹配来源币种"))
    rows = []
    allocations = mapping.get("expense_lines")
    if not isinstance(allocations, list) or not 1 <= len(allocations) <= 100:
        frappe.throw(_i18n("必须明确费用/税额分摊行"))
    allocated = Decimal(0)
    for allocation in allocations:
        account = _account(allocation.get("account"), mapping["company"])
        if account.root_type != "Expense" and account.account_type != "Tax":
            frappe.throw(_i18n("费用行只能使用费用或税额科目"))
        amount = money(allocation.get("source_amount"), precision=2, positive=True)
        allocated += amount
        # No guessed tax, account currency or conversion: explicit amount/rate from finance.
        if account.account_currency == item["currency"] and money(allocation.get("amount")) != amount:
            frappe.throw(_i18n("同币种费用行金额必须等于来源分摊金额"))
        rows.append(_line(account, allocation.get("amount"), "debit", mapping, allocation.get("exchange_rate"), cost_center=allocation.get("cost_center"), project=allocation.get("project")))
    if allocated != total:
        frappe.throw(_i18n("费用分摊金额必须完整等于来源金额"))
    rows.append(_line(payable, str(total), "credit", mapping, mapping.get("payable_exchange_rate"), party=party))
    _balanced(rows)
    return rows


def _balanced(rows):
    if sum((money(r.get("debit", "0")) for r in rows), Decimal(0)) != sum((money(r.get("credit", "0")) for r in rows), Decimal(0)):
        frappe.throw(_i18n("凭证本位币借贷不平，请明确汇率和汇兑差额"))


@frappe.whitelist(methods=["POST"])
def save_mapping(source_id, mapping, expected_source_version):
    _finance()
    doc = _source(source_id, write=True)
    item = _fresh(doc)
    if item["version"] != expected_source_version:
        frappe.throw(_i18n("来源版本已变化，请刷新后复核"))
    values = frappe.parse_json(mapping)
    if not isinstance(values, dict) or len(json.dumps(values)) > 100000:
        frappe.throw(_i18n("财务映射格式无效"))
    allowed = {"company", "party_type", "party", "payable_account", "payable_exchange_rate", "expense_lines", "actual_incurred", "no_existing_erp_coverage", "posting_date", "payments", "classification", "application_type", "recognition_mode", "existing_erp_coverage_confirmed"}
    if set(values) - allowed or values.get("company") != doc.company:
        frappe.throw(_i18n("财务映射字段或法律公司无效"))
    if values.get("application_type") and (item["application_type"] != "unclassified" or values["application_type"] not in {"payment", "reimbursement"}):
        frappe.throw(_i18n("只能对待分类来源明确指定付款申请或费用报销"))
    if values.get("recognition_mode", "new") not in {"new", "existing"}:
        frappe.throw(_i18n("费用确认方式无效"))
    _expense_lines(item, values)
    getdate(values.get("posting_date"))
    if not values.get("posting_date"):
        frappe.throw(_i18n("必须明确费用记账日期"))
    old = json.loads(frappe.db.get_value(MAPPING, doc.name, "mapping_json") or "{}")
    if frappe.db.exists(EVENT, {"source": doc.name,"operation":"expense"}) and event_fingerprint(item, old) != event_fingerprint(item, values):
        frappe.throw(_i18n("已有凭证关联，费用确认不可覆盖；请人工复核调整"))
    values.update(expense_fingerprint=digest(expense_facts(item)), source_version=item["version"], approved_by=frappe.session.user, approved_at=str(now_datetime()))
    target = frappe.get_doc(MAPPING, doc.name) if frappe.db.exists(MAPPING, doc.name) else frappe.new_doc(MAPPING)
    target.update({"source": doc.name, "company": doc.company, "mapping_json": json.dumps(values, ensure_ascii=False), "approved_by": frappe.session.user, "approved_at": now_datetime()})
    _save(target)
    _upsert(item, _maps(_settings()))
    return {"source_id": doc.name, "mapping": values}


def _event_key(doc, payment_id=None):
    return digest([SOURCE_SYSTEM, doc.source_id, "payment", identifier(payment_id)]) if payment_id else digest([SOURCE_SYSTEM, doc.source_id, "expense"])


def _recognition(doc, mapping, item):
    event_key = _event_key(doc)
    if not frappe.db.get_value(EVENT, event_key, "journal_entry", for_update=True):
        frappe.throw(_i18n("请先生成或关联费用确认凭证"))
    preview = {"event_key": event_key, "fingerprint": event_fingerprint(item, mapping),
               "posting_date": mapping["posting_date"], "accounts": _expense_lines(item, mapping), "recognition": None}
    return _checked_voucher_result(doc, preview, operation="recognition")


def _match_journal(doc, rows):
    _require_fields("Journal Entry Account", {"account", "account_currency", "party_type", "party", "debit_in_account_currency", "credit_in_account_currency", "exchange_rate", "cost_center", "project"}, parenttype="Journal Entry")
    def facts(row):
        return tuple(str(row.get(k) or "") for k in ("account", "account_currency", "party_type", "party", "cost_center", "project")) + tuple(money(str(row.get(k) or "0")) for k in ("debit_in_account_currency", "credit_in_account_currency", "exchange_rate"))
    if sorted(facts(row) for row in doc.accounts) != sorted(facts(row) for row in rows):
        frappe.throw(_i18n("关联凭证科目、往来方、金额或汇率与确认不符"))


def _journal_association_scope():
    _require_fields("Journal Entry", {"company", "docstatus", "posting_date", "accounts"})
    _require_fields("Journal Entry Account", {"account", "account_currency", "party_type", "party", "debit_in_account_currency", "credit_in_account_currency", "exchange_rate", "cost_center", "project"}, parenttype="Journal Entry")


def _match_date(doc, posting_date):
    _require_fields("Journal Entry", {"posting_date"})
    if getdate(doc.posting_date) != getdate(posting_date):
        frappe.throw(_i18n("凭证记账日期与财务确认不符"))


def _payment_lines(doc, item, mapping, payment_id):
    recognition = _recognition(doc, mapping, item)
    payment = next((p for p in item["payments"] if p["source_id"] == payment_id), None)
    if not payment or payment.get("evidence_status") != "recorded" or payment.get("currency") != item["currency"]:
        frappe.throw(_i18n("实际付款证据不足、已撤回或币种冲突"))
    try:
        payment_date = date.fromisoformat(payment["payment_date"])
    except (KeyError, TypeError, ValueError):
        frappe.throw(_i18n("实际付款日期缺失或无效，不能生成付款凭证"))
    try:
        known_total = sum((money(p.get("amount"), positive=True) for p in item["payments"] if p.get("evidence_status") == "recorded"), Decimal(0))
    except ValueError:
        frappe.throw(_i18n("实际付款金额必须大于零，不能抵减其他付款或生成付款凭证"))
    if known_total > money(item["amount"]):
        frappe.throw(_i18n("实际付款超过费用确认金额，需要人工复核"))
    terms = mapping.get("payments", {}).get(payment_id)
    if not isinstance(terms, dict):
        frappe.throw(_i18n("请明确本次实际付款银行科目和汇率"))
    if _rate(terms.get("payable_exchange_rate")) != _rate(mapping["payable_exchange_rate"]):
        frappe.throw(_i18n("应付冲减必须沿用费用确认的账面汇率，差额应明确计入汇兑损益"))
    party = _party(item, mapping)
    payable = _account(mapping["payable_account"], doc.company, {"Payable"})
    bank = _account(terms.get("bank_account"), doc.company, {"Bank", "Cash"})
    amount = money(payment["amount"], precision=2, positive=True)
    bank_amount = money(terms.get("bank_amount"), precision=2, positive=True)
    if bank.account_currency == payment["currency"] and bank_amount != amount:
        frappe.throw(_i18n("同币种银行金额必须等于实际付款金额"))
    rows = [_line(payable, str(amount), "debit", mapping, terms.get("payable_exchange_rate"), party=party),
            _line(bank, str(bank_amount), "credit", mapping, terms.get("bank_exchange_rate"))]
    difference = money(rows[1]["credit"]) - money(rows[0]["debit"])
    if difference:
        fx = _account(terms.get("exchange_difference_account"), doc.company)
        base = _read("Company", doc.company, {"default_currency"}).default_currency
        if fx.account_currency != base or fx.root_type not in {"Income", "Expense"}:
            frappe.throw(_i18n("汇兑差额科目必须为本位币损益科目"))
        rows.append(_line(fx, str(abs(difference)), "debit" if difference > 0 else "credit", mapping, "1", cost_center=terms.get("cost_center")))
    _balanced(rows)
    return rows, recognition, payment_date


def _preview(doc, item, mapping, payment_id=None):
    if mapping.get("advance"):
        from .operating_payment_service import advance_preview
        return advance_preview(doc, item, payment_id)
    if _issues(item, mapping, include_payment_dates=False):
        frappe.throw("；".join(_issues(item, mapping, include_payment_dates=False)))
    if mapping.get("expense_fingerprint") != digest(expense_facts(item)):
        frappe.throw(_i18n("来源财务事实已变化，请重新确认；已有凭证不能覆盖"))
    rows, recognition, posting_date = _payment_lines(doc, item, mapping, payment_id) if payment_id else (_expense_lines(item, mapping), None, getdate(mapping["posting_date"]))
    fingerprint = event_fingerprint(item, mapping, payment_id)
    return {"event_key": _event_key(doc, payment_id), "fingerprint": fingerprint, "source_version": item["version"], "company": doc.company,
            "posting_date": str(posting_date), "accounts": rows, "recognition": recognition.name if recognition else None, "settlement_state": "尚未核销" if payment_id else None,
            "base_rounding": [{"account": r["account"], "exact_base": str(money(r.get("debit_in_account_currency") or r.get("credit_in_account_currency")) * money(r["exchange_rate"])), "rounded_base": r.get("debit") or r.get("credit")} for r in rows]}


@frappe.whitelist(methods=["POST"])
def preview_voucher(source_id, payment_source_id=None):
    _finance()
    doc = _source(source_id)
    item = _fresh(doc)
    from .operating_payment_service import voucher_mapping
    return _preview(doc, item, voucher_mapping(doc, payment_source_id), payment_source_id)


def _checked_voucher_result(doc, preview, payment_source_id=None, journal_name=None, *, created=False, operation="voucher"):
    """Current-read the persisted association and native parent/children together."""
    amount = sum((money(row.get("debit", "0")) for row in preview["accounts"]), Decimal(0))
    stage = "event_read"
    try:
        event = _read(EVENT, preview["event_key"], {"source", "company", "operation", "fingerprint"}, for_update=True)
        stage = "event_association"
        _assert_financial_fields(event, {"source": doc.name, "company": doc.company,
            "operation": "payment" if payment_source_id else "expense", "payment_source_id": payment_source_id,
            "fingerprint": preview["fingerprint"], "recognition": preview["recognition"]}, "凭证事件关联写后校验不一致，请人工复核")
        if journal_name and event.journal_entry != journal_name:
            frappe.throw(_i18n("凭证事件与原生凭证关联不一致，请人工复核"))
        stage = "journal_read"
        journal = _read("Journal Entry", event.journal_entry, {"company", "accounts", "docstatus", "posting_date"}, for_update=True)
        stage = "journal_state"
        if journal.company != doc.company or journal.docstatus not in {0, 1} or (created and journal.docstatus != 0):
            frappe.throw(_i18n("凭证公司或状态写后校验不一致，请人工复核"))
        stage = "journal_association"
        linked = json.loads(event.provenance_json or "{}").get("operation") == "link_existing"
        if created and linked:
            frappe.throw(_i18n("新建凭证事件不得变更为历史关联，请人工复核"))
        if linked:
            if event.operation != "expense" or journal.get("custom_operating_event_key"):
                frappe.throw(_i18n("现有凭证关联写后校验不一致，请人工复核"))
        else:
            _assert_financial_fields(journal, {"custom_operating_event_key": event.name,
                "custom_operating_source": doc.name, "custom_operating_fingerprint": preview["fingerprint"],
                "custom_operating_recognition": preview["recognition"]}, "原生凭证事件写后校验不一致，请人工复核")
        stage = "journal_accounts"
        _match_journal(journal, preview["accounts"])
        stage = "journal_date"
        _match_date(journal, preview["posting_date"])
    except Exception as error:
        _audit_financial(operation, doc.name, journal_name or preview["event_key"], amount, "rejected", error=error, stage=stage)
        raise
    _audit_financial(operation, doc.name, journal.name, amount, "validated")
    return journal


def _create_voucher_draft(source_id, expected_fingerprint, payment_source_id=None):
    _finance()
    doc = _source(source_id, write=True)
    from .operating_payment_service import voucher_mapping
    item, mapping = _fresh(doc), voucher_mapping(doc, payment_source_id, for_update=True)
    if not payment_source_id and mapping.get("recognition_mode") == "existing":
        frappe.throw(_i18n("已确认现有 ERP 覆盖，请关联现有凭证，不得再生成费用"))
    preview = _preview(doc, item, mapping, payment_source_id)
    if preview["fingerprint"] != expected_fingerprint:
        frappe.throw(_i18n("预览事实已变化，请重新预览"))
    existing = frappe.db.get_value(EVENT, preview["event_key"], ["journal_entry", "fingerprint"], as_dict=True, for_update=True)
    if existing:
        journal = _checked_voucher_result(doc, preview, payment_source_id, existing.journal_entry)
        return {"journal_entry": journal.name, "docstatus": journal.docstatus, "existing": True, "settlement_state": preview["settlement_state"]}
    journal = frappe.get_doc({"doctype": "Journal Entry", "voucher_type": "Journal Entry", "company": doc.company,
        "posting_date": preview["posting_date"], "multi_currency": 1, "user_remark": item.get("summary") or "运营费用",
        "accounts": preview["accounts"], "custom_operating_event_key": preview["event_key"], "custom_operating_source": doc.name,
        "custom_operating_recognition": preview["recognition"], "custom_operating_fingerprint": preview["fingerprint"]})
    with managed_write():
        journal.insert()  # Native permissions and ERPNext validation. Always docstatus 0.
        frappe.get_doc({"doctype": EVENT, "event_key": preview["event_key"], "source": doc.name, "company": doc.company,
            "operation": "payment" if payment_source_id else "expense", "payment_source_id": payment_source_id,
            "fingerprint": preview["fingerprint"], "source_version": item["version"], "journal_entry": journal.name,
            "recognition": preview["recognition"], "provenance_json": json.dumps({"source": item, "mapping": mapping, "actor": frappe.session.user}, ensure_ascii=False)}).insert(ignore_permissions=True)
    journal = _checked_voucher_result(doc, preview, payment_source_id, journal.name, created=True)
    return {"journal_entry": journal.name, "docstatus": journal.docstatus, "existing": False, "settlement_state": preview["settlement_state"]}


@frappe.whitelist(methods=["POST"])
def create_voucher_draft(source_id, expected_fingerprint, payment_source_id=None):
    # MariaDB snapshot isolation can reject a locking read after another request
    # inserts the event. Restart this isolated RPC's transaction, then repeat all
    # permission, source freshness, mapping and financial checks. Never commit.
    for attempt in range(3):
        try:
            return _create_voucher_draft(source_id, expected_fingerprint, payment_source_id)
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
            if attempt == 2:
                frappe.throw(_i18n("同期财务操作发生冲突，请刷新后重试"))


@frappe.whitelist(methods=["POST"])
def link_existing(source_id, journal_entry, expected_fingerprint):
    _finance()
    doc = _source(source_id, write=True)
    item, mapping = _fresh(doc), _mapping(doc)
    if mapping.get("recognition_mode") != "existing" or not mapping.get("existing_erp_coverage_confirmed"):
        frappe.throw(_i18n("请先明确现有 ERP 覆盖并确认关联"))
    preview = _preview(doc, item, mapping)
    if expected_fingerprint != preview["fingerprint"] or frappe.db.exists(EVENT, preview["event_key"]):
        frappe.throw(_i18n("费用确认已存在或预览变化，请刷新"))
    journal = _read("Journal Entry", journal_entry, {"company", "accounts", "docstatus"}, for_update=True)
    if journal.company != doc.company or journal.docstatus == 2 or journal.get("custom_operating_event_key"):
        frappe.throw(_i18n("凭证公司、状态或运营费用关联不符"))
    _match_journal(journal, preview["accounts"])
    _match_date(journal, preview["posting_date"])
    if frappe.db.exists(EVENT, {"journal_entry": journal.name}):
        frappe.throw(_i18n("该凭证已被关联，不能重复确认"))
    with managed_write():
        frappe.get_doc({"doctype": EVENT, "event_key": preview["event_key"], "source": doc.name, "company": doc.company,
            "operation": "expense", "fingerprint": preview["fingerprint"], "source_version": item["version"], "journal_entry": journal.name,
            "provenance_json": json.dumps({"source": item, "mapping": mapping, "actor": frappe.session.user, "operation": "link_existing"}, ensure_ascii=False)}).insert(ignore_permissions=True)
    journal = _checked_voucher_result(doc, preview, journal_name=journal.name)
    return {"journal_entry": journal.name, "docstatus": journal.docstatus}


def validate_operating_journal(doc, method=None):
    # Shared-image sites need not install Operating. Ordinary native journals
    # stay independent, but unauditable operating associations cannot be accepted.
    if not frappe.db.exists("DocType", EVENT):
        if any(doc.get(field) for field in ("custom_operating_event_key", "custom_operating_source", "custom_operating_fingerprint", "custom_operating_recognition")):
            frappe.throw(_i18n("当前账套未启用运营费用，不能设置运营费用凭证关联"))
        return
    key = doc.get("custom_operating_event_key")
    if not doc.is_new():
        old_key = frappe.db.get_value("Journal Entry", doc.name, "custom_operating_event_key")
        if old_key and old_key != key:
            frappe.throw(_i18n("运营费用凭证事件关联不可移除或修改"))
    if _managed.get():
        return
    # Existing recognition links are audited in Event without modifying historical
    # native JE fields. Resolve that association in reverse, including draft saves.
    event = frappe.db.get_value(EVENT, key if key else {"journal_entry": doc.name}, ["source", "fingerprint", "journal_entry", "operation", "payment_source_id", "provenance_json"], as_dict=True, for_update=True)
    if not key and not event:
        return  # Unrelated native journals are unaffected.
    if not event or event.journal_entry != doc.name:
        frappe.throw(_i18n("运营费用凭证关联不可伪造或更改"))
    if key:
        if event.source != doc.get("custom_operating_source") or event.fingerprint != doc.get("custom_operating_fingerprint"):
            frappe.throw(_i18n("运营费用凭证关联不可伪造或更改"))
    elif event.operation != "expense" or json.loads(event.provenance_json or "{}").get("operation") != "link_existing":
        frappe.throw(_i18n("运营费用凭证事件关联不可移除或修改"))
    source = _source(event.source)
    from .operating_payment_service import voucher_mapping
    item, mapping = _fresh(source), voucher_mapping(source, event.payment_source_id if event.operation == "payment" else None)
    preview = _preview(source, item, mapping, event.payment_source_id if event.operation == "payment" else None)
    if preview["fingerprint"] != event.fingerprint or doc.company != source.company:
        frappe.throw(_i18n("来源财务事实已变化，需要复核"))
    _match_journal(doc, preview["accounts"])
    _match_date(doc, preview["posting_date"])
    if key and doc.get("custom_operating_recognition") != preview["recognition"]:
        frappe.throw(_i18n("费用确认凭证关联不可修改"))
    if event.operation == "payment" and doc.docstatus == 0 and any(row.reference_name or row.reference_type for row in doc.accounts):
        frappe.throw(_i18n("付款草稿尚未核销，不能设置原生已核销引用"))
    if event.operation == "payment" and doc.docstatus == 1 and not mapping.get("advance"):
        recognition = _recognition(source, mapping, item)
        if recognition.docstatus != 1:
            frappe.throw(_i18n("费用确认凭证尚未提交，本付款凭证尚未核销"))
        if doc.get("custom_operating_recognition") != recognition.name:
            frappe.throw(_i18n("费用确认关联已变化"))
        for row in doc.accounts:
            if row.account == mapping["payable_account"] and row.party == mapping["party"]:
                row.reference_type, row.reference_name = "Journal Entry", recognition.name
        # Validate native against-JE rules after adding the explicit submitted reference.
        doc.validate_against_jv()


def _save_source_company(source_id, company, expected_source_version):
    _manager()
    before = _source(source_id)
    legal = _read("Company", company, {"name"})
    initial_settings = _settings()
    token_key = {"doctype": SETTINGS, "name": SETTINGS, "fieldname": "api_token", "encrypted": 1}
    token_before = frappe.db.get_value("__Auth", token_key, "password", order_by=None)
    result = _source_page({"source_id": before.source_id, "limit": 1})
    if not result["end"] or len(result["items"]) != 1:
        frappe.throw(_i18n("来源已撤回，请重新同步"))
    item = result["items"][0]
    if item.get("source_id") != before.source_id:
        frappe.throw(_i18n("来源标识不符，请重新同步"))
    if item["version"] != expected_source_version:
        frappe.throw(_i18n("来源版本已变化，请刷新后复核"))
    settings = _settings(for_update=True)
    if (settings.enabled != initial_settings.enabled
            or frappe.db.get_value("__Auth", token_key, "password", order_by=None, for_update=True) != token_before):
        frappe.throw(_i18n("同步设置已变化，请刷新后复核"))
    doc = _source(source_id, write=True)
    legal.check_permission("read")
    if doc.source_id != before.source_id:
        frappe.throw(_i18n("来源标识不符，请重新同步"))
    if doc.source_version != before.source_version and doc.source_version != expected_source_version:
        frappe.throw(_i18n("来源版本已变化，请刷新后复核"))
    from .operating_payment_service import PAYMENT, TAKEOVER
    # The source lock serializes financial writes. Use current reads here: a
    # consistent snapshot taken before remote IO can miss a newly committed row.
    for doctype in (MAPPING, EVENT, TAKEOVER, PAYMENT):
        if frappe.db.get_value(doctype, {"source": doc.name}, "name", for_update=True):
            frappe.throw(_i18n("已有财务确认，不可改变法律公司"))
    maps = _maps(settings)
    maps["source:" + doc.name] = company
    settings.set("company_mappings", [{"source_company": raw, "company": legal} for raw, legal in maps.items()])
    _save(settings)
    doc.company = company
    updated = _upsert(item, maps, doc=doc)
    _assert_financial_fields(frappe.db.get_value(SOURCE, doc.name, ["company", "source_version"], as_dict=True, for_update=True),
                            {"company": company, "source_version": expected_source_version}, "法律公司保存结果不一致，已取消本次操作")
    persisted = frappe.db.get_values("Operating Expense Company Map",
        {"parent": SETTINGS, "parenttype": SETTINGS, "parentfield": "company_mappings", "source_company": "source:" + doc.name},
        "company", for_update=True)
    if len(persisted) != 1 or persisted[0][0] != company:
        frappe.throw(_i18n("法律公司映射保存结果不一致，已取消本次操作"))
    _audit_financial("save_source_company", doc.name, doc.name, "0", "validated", company=company)
    return {"source_id": updated.name, "company": updated.company}


@frappe.whitelist(methods=["POST"])
def save_source_company(source_id, company, expected_source_version):
    """One RPC transaction: settings first, source second; no intermediate commit."""
    response_before = dict(frappe.response)
    messages_before = list(frappe.message_log)
    for attempt in range(3):
        try:
            return _save_source_company(source_id, company, expected_source_version)
        except frappe.QueryDeadlockError as error:
            frappe.db.rollback()
            frappe.response.clear()
            frappe.response.update(response_before)
            frappe.message_log[:] = messages_before
            _audit_financial("save_source_company", source_id, source_id, "0", "rolled_back",
                company=company, error=error, stage="retry" if attempt < 2 else "exhausted", error_code="transaction_conflict")
            if attempt == 2:
                frappe.throw(_i18n("操作冲突，法律公司未保存，请稍后重试。"))
        except Exception as error:
            frappe.db.rollback()
            _audit_financial("save_source_company", source_id, source_id, "0", "rolled_back",
                company=company, error=error, stage="validation_or_save", error_code="company_save_failed")
            raise


LIST_FIELDS = ["name", "source_id", "source_system", "company", "application_type", "effective_application_type", "application_type_raw", "applicant", "payee_name", "summary", "request_date", "currency", "amount", "paid_amount", "pending_amount", "source_status", "approval_state", "source_company", "source_sheet", "source_version", "issues"]
EXPORT_COLUMNS = {"company": "法律公司", "source_id": "申请编号", "effective_application_type": "财务申请类型", "application_type_raw": "原始申请类型", "applicant": "申请人", "payee_name": "收款人", "summary": "摘要", "request_date": "申请日期", "currency": "币种", "amount": "申请金额", "paid_amount": "累计已付", "pending_amount": "剩余待付", "source_status": "付款状态", "approval_state": "来源审批状态", "finance_status": "凭证状态", "issues": "待处理问题", "source_company": "来源公司", "source_sheet": "来源归档表"}
EXPORT_COLUMNS["approval_no"] = "原始审批编号"
EXPORT_COLUMNS["source_id"] = "ERP来源标识"
EXPORT_COLUMNS["display_source_id"] = "申请编号"
EXPORT_COLUMNS["current_approver"] = "当前办理人"
EXPORT_COLUMNS["source_system"] = "来源"
EXPORT_COLUMNS["project"] = "项目"
EXPORT_COLUMNS.update(source_status="付款状态", display_source_id="钉钉申请单号", source_company="应付款公司",
    amount="应付金额", paid_amount="已支付金额", pending_amount="待付款金额", currency="货币类型",
    project="项目归属", account_nature="账户性质", needed_payment_date="需求付款日期",
    general_manager_approval="总经理审批", remark="备注", blocking_reason="办理阻碍")


def _list_rows(filters=None, order_by="request_date desc", with_summary=False):
    _require_fields(SOURCE, set(LIST_FIELDS))
    filters = frappe.parse_json(filters) if isinstance(filters, str) else filters or {}
    allowed = {"company", "application_type", "applicant", "source_status", "approval_state", "date_from", "date_to", "keyword", "quick_tab"}
    if not isinstance(filters, dict) or set(filters) - allowed:
        frappe.throw(_i18n("列表筛选无效"))
    native = []
    for field in ("company", "application_type", "applicant"):
        if filters.get(field):
            if not isinstance(filters[field], str) or len(filters[field]) > 200:
                frappe.throw(_i18n("筛选值无效"))
            native.append(["effective_application_type" if field == "application_type" else field, "=", filters[field]])
    for field, operator in (("date_from", ">="), ("date_to", "<=")):
        if filters.get(field):
            native.append(["request_date", operator, str(getdate(filters[field]))])
    sort = str(order_by).split()
    if len(sort) != 2 or sort[0] not in {"request_date", "amount", "applicant", "source_id", "company", "modified"} or sort[1].lower() not in {"asc", "desc"}:
        frappe.throw(_i18n("排序字段无效"))
    or_filters = []
    keyword = None
    if filters.get("keyword"):
        keyword = filters["keyword"]
        if not isinstance(keyword, str) or len(keyword) > 200:
            frappe.throw(_i18n("关键词无效"))
        # The original OA number is a permission-aliased JSON projection. Match
        # once after the same native permission query, with literal semantics.
        keyword = keyword.casefold()
    order_sql = f"{sort[0]} {sort[1]}, name asc"
    if sort[0] == "amount":
        # Build native permissions/filters first, then add only fixed expression
        # terms. Original text is unchanged and malformed rows remain last.
        from pypika.terms import Case
        from pypika.functions import Cast
        from pypika.enums import Order
        table = frappe.qb.DocType(SOURCE)
        query = frappe.qb.get_query(SOURCE, fields=LIST_FIELDS, filters=native, or_filters=or_filters, ignore_permissions=False)
        invalid = Case().when(table.amount.regexp("^[+-]?[0-9]+([.][0-9]+)?$"), 0).else_(1)
        query = query.orderby(invalid, order=Order.asc).orderby(Cast(table.amount, "DECIMAL(65,30)"), order=Order.asc if sort[1].lower() == "asc" else Order.desc).orderby(table.name, order=Order.asc)
        rows = query.run(as_dict=True)
    else:
        rows = frappe.get_list(SOURCE, fields=LIST_FIELDS, filters=native, or_filters=or_filters, order_by=order_sql, limit_page_length=0)
    # Native query permissions + explicit Company query hook apply to this bounded
    # parent-field projection; no full source JSON is hydrated per result row.
    rows = [dict(row) for row in rows]
    _readable_source_numbers(rows)
    from .operating_payment_service import overlay_rows
    overlay_rows(rows)
    for row in rows:
        row["source_status"] = payment_status(row)
    status = filters.get("source_status")
    if status:
        if not isinstance(status, str) or len(status) > 200:
            frappe.throw(_i18n("付款状态筛选无效"))
        rows = [row for row in rows if row["source_status"] == status]
    state = filters.get("approval_state")
    if state:
        if state not in {"pending", "approved", "rejected", "terminated", "withdrawn", "unknown", "eligible", "blocked"}:
            frappe.throw(_i18n("审批状态筛选无效"))
        if state in {"eligible", "blocked"}:
            rows = [r for r in rows if bool(r["payment_eligibility"]["can_register_payment"]) == (state == "eligible")]
        else:
            rows = [r for r in rows if r["approval_state"] == state]
    if keyword:
        rows = [r for r in rows if any(keyword in str(r.get(field) or "").casefold() for field in ("source_id", "approval_no", "summary", "payee_name", "applicant"))]
    tab = filters.get("quick_tab") or "all"
    if tab not in QUICK_TABS:
        frappe.throw(_i18n("快捷筛选无效"))
    selected, counts, totals = worklist_summary(rows, tab)
    return (selected, counts, totals) if with_summary else selected


@frappe.whitelist()
def get_operating_expenses(filters=None, order_by="request_date desc", page_length=100, start=0):
    page_length, start = int(page_length), int(start)
    if page_length not in {20, 100, 500, 2500} or start < 0:
        frappe.throw(_i18n("分页参数无效"))
    rows, counts, totals = _list_rows(filters, order_by, with_summary=True)
    page = rows[start:start + page_length]
    _enrich_page(page)
    return {"rows": page, "total_count": len(rows), "currency_totals": totals, "tab_counts": counts}


def _enrich_page(rows):
    if not rows:
        return
    names = [row["name"] for row in rows]
    _require_fields(MAPPING, {"source", "company"})
    mappings = set(frappe.get_list(MAPPING, filters={"source": ["in", names]}, pluck="source", limit_page_length=0))
    events = frappe.get_all(EVENT, filters={"source": ["in", names]}, fields=["source", "journal_entry", "operation", "payment_source_id", "provenance_json"], limit_page_length=0)
    events = [event for event in events if not json.loads(event.provenance_json or "{}").get("voided_draft")]
    journals = {}
    if events and frappe.has_permission("Journal Entry", "read"):
        try:
            _journal_association_scope()
            journals = {doc.name: doc for doc in frappe.get_list("Journal Entry", filters={"name": ["in", list({e.journal_entry for e in events})]}, fields=["name", "company", "docstatus"], limit_page_length=0)}
        except frappe.PermissionError:
            pass
    by_source = {}
    for event in events:
        by_source.setdefault(event.source, []).append(event)
    for row in rows:
        row["finance_status"] = "需复核" if row.get("issues") else "已确认待生成" if row["name"] in mappings else "未确认"
        row["journal_entries"] = []
        for event in by_source.get(row["name"], []):
            journal = journals.get(event.journal_entry)
            if journal is None or journal.company != row["company"]:
                row["finance_status"] = "需复核"
                row["association_issue"] = "关联凭证缺失或无权读取"
                continue
            row["journal_entries"].append({"name": journal.name, "docstatus": journal.docstatus, "operation": event.operation, "payment_source_id": event.payment_source_id})
            row["finance_status"] = "需复核" if journal.docstatus == 2 or row.get("issues") else "关联已记账" if journal.docstatus == 1 else "草稿已生成"


@frappe.whitelist()
def get_operating_expense_detail(source_id):
    doc = _source(source_id)
    raw = json.loads(doc.source_json)
    from .operating_payment_service import merge_source, payment_context, payment_detail
    context = payment_context(doc, raw)
    raw = merge_source(doc, raw, context)
    # Presentation only; original source JSON and reported status stay auditable.
    raw["source_status"] = payment_status(raw)
    item = {key: raw[key] for key in RAW_SOURCE_PERMISSIONS if key in raw}
    # Malformed evidence remains untouched in server JSON and visible in issues;
    # do not invent payment identities/amounts or an eligible approval in the UI.
    item["payments"] = _detail_rows(raw.get("payments"), ("source_id", "amount", "currency", "payment_date", "payment_account", "payer", "remark", "bank_reference", "source_type", "evidence_status", "version", "created_at", "updated_at"))
    approvals = raw.get("approvals")
    approval_raw = approvals.get("raw", {}) if isinstance(approvals, dict) else None
    valid_approvals = isinstance(approvals, dict) and isinstance(approval_raw, dict)
    approval_fields = ("status", "result", "owner_confirmation", "finance_review", "finance_manager_approval", "general_manager_approval")
    approval_fields += tuple("cashier_" + field for field in approval_fields)
    item["approvals"] = {"eligibility": approvals.get("eligibility") if valid_approvals else None, "raw": {key: approval_raw.get(key) for key in approval_fields} if valid_approvals else {}}
    events = []
    for event in frappe.get_all(EVENT, filters={"source": doc.name}, fields=["name", "journal_entry", "operation", "payment_source_id", "fingerprint", "source_version", "provenance_json"], limit_page_length=0):
        provenance = json.loads(event.provenance_json or "{}")
        if provenance.get("voided_draft"):
            events.append({"operation": event.operation, "payment_source_id": event.payment_source_id, "settlement_state": "草稿已弃用；付款已撤销", "voided": True})
            continue
        try:
            _journal_association_scope()
            journal = _read("Journal Entry", event.journal_entry, {"company", "docstatus"})
            if journal.company != doc.company:
                raise frappe.PermissionError
            events.append({**{key: value for key,value in dict(event).items() if key!="provenance_json"}, "docstatus": journal.docstatus, "settlement_state": "尚未核销" if event.operation == "payment" and journal.docstatus == 0 else None})
        except (frappe.PermissionError, frappe.DoesNotExistError):
            events.append({"operation": event.operation, "payment_source_id": event.payment_source_id, "issue": "关联凭证缺失或无权读取"})
    mapping, mapping_issue = None, None
    if frappe.db.exists(MAPPING, doc.name):
        try:
            mapping = _mapping(doc)
        except (frappe.PermissionError, frappe.DoesNotExistError):
            mapping_issue = "财务映射存在缺失或无权读取的原生引用"
    # Client gets stable attachment identifiers; authenticated upstream paths remain server-side.
    item["attachments"] = _detail_rows(raw.get("attachments"), ("source_id", "filename", "version", "payment_source_id"))
    item.pop("provenance", None)
    item["effective_application_type"] = doc.effective_application_type
    return {"source": item, "company": doc.company or None, "issues": doc.issues, "mapping": mapping, "mapping_issue": mapping_issue, "events": events, "erp_payments": payment_detail(doc, context)}


def _detail_rows(rows, fields):
    return [{key: row.get(key) for key in fields} for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _readable_source_numbers(rows):
    """One bounded source projection for list, filters and export, never raw JSON."""
    from .operating_oa_source import approval_state
    _require_fields(SOURCE, {"source_id", "source_system", "approval_state", "issues"})
    # Names already passed get_list's native/company permissions. Select only
    # aliased facts, not payment histories or private attachments, in <=500 chunks.
    paths = ("approval_no", "approvals", "current_approver", "payment_eligibility", "source_conflict", "currency_conflict", "project",
             "source_company", "account_nature", "needed_payment_date", "general_manager_approval", "remark", "projection_conflicts", "original_url")
    expressions = ",".join("JSON_EXTRACT(CASE WHEN JSON_VALID(source_json) THEN source_json ELSE '{}' END,'$." + field + "') AS `" + field + "`" for field in paths)
    raw = {}
    for offset in range(0, len(rows), 500):
        names = tuple(row["name"] for row in rows[offset:offset + 500])
        for projected in frappe.db.sql("SELECT name," + expressions + " FROM `tabOperating Expense Source` WHERE name IN %(names)s", {"names": names}, as_dict=True):
            raw[projected.name] = {field: json.loads(projected[field]) if projected[field] is not None else None for field in paths}
    for row in rows:
        item = raw.get(row["name"], {})
        item["source_system"] = row["source_system"]
        value = item.get("approval_no")
        row["approval_no"] = value if isinstance(value, str) and len(value) <= 140 else None
        value = item.get("original_url")
        row["original_url"] = value if isinstance(value, str) else None
        approvals = item.get("approvals")
        original = approvals.get("raw") if isinstance(approvals, dict) else None
        original = original if isinstance(original, dict) else {}
        row["source_withdrawn"] = bool(original.get("scope") == "withdrawn" or original.get("deleted_at"))
        row["approval_state"] = "unknown" if original.get("scope") == "withdrawn" or original.get("deleted_at") else approval_state(original.get("status"), original.get("result"))
        row["current_approver"] = item.get("current_approver") or ""
        row["payment_eligibility"] = payment_decision(item)
        for field in ("project", "account_nature", "needed_payment_date", "general_manager_approval", "remark"):
            value = item.get(field)
            row[field] = value if isinstance(value, str) else None
        # Only an explicit canonical organization can replace the cached scalar.
        if isinstance(item.get("source_company"), str):
            row["source_company"] = item["source_company"]
        conflicts = item.get("projection_conflicts")
        row["projection_conflicts"] = conflicts if isinstance(conflicts, list) else []
        for field in ("source_conflict", "currency_conflict"):
            row[field] = bool(item.get(field))


@frappe.whitelist()
def export_operating_expenses(filters=None, order_by="request_date desc", columns=None):
    rows = _list_rows(filters, order_by)
    # Lazy exact-owner proof; authorized _source reads keep native scope checks.
    _require_export_permission(SOURCE, ({"owner": _source(row["name"]).get("owner")} for row in rows))
    selected = frappe.parse_json(columns) if isinstance(columns, str) else columns or list(EXPORT_COLUMNS)
    if not isinstance(selected, list) or not selected or len(selected) != len(set(selected)) or set(selected) - set(EXPORT_COLUMNS):
        frappe.throw(_i18n("导出列无效"))
    if "finance_status" in selected:
        _enrich_page(rows)
    from xlsxwriter import Workbook
    from io import BytesIO
    content = BytesIO()
    with Workbook(content, {"constant_memory": True, "strings_to_formulas": False, "strings_to_urls": False}) as workbook:
        sheet = workbook.add_worksheet("运营支出")
        numeric = workbook.add_format({"num_format": "0.00"})
        date_format = workbook.add_format({"num_format": "yyyy-mm-dd"})
        money_fields = {"amount", "paid_amount", "pending_amount"}
        for column_index, field in enumerate(selected):
            sheet.write(0, column_index, EXPORT_COLUMNS[field])
        for row_index, row in enumerate(rows, 1):
            for column_index, field in enumerate(selected):
                value = (row.get("approval_no") or "—") if field == "display_source_id" else row.get(field)
                monetary = field in money_fields
                if monetary:
                    try:
                        value = money(value)
                    except ValueError:
                        value = None
                cell_format = numeric if monetary else date_format if field == "request_date" else None
                sheet.write(row_index, column_index, value, cell_format)
    frappe.response["filename"] = "运营支出.xlsx"
    frappe.response["filecontent"] = content.getvalue()
    frappe.response["type"] = "binary"


@frappe.whitelist()
def download_operating_expense_attachment(source_id, attachment_source_id):
    doc = _source(source_id)
    cached = json.loads(doc.source_json)
    persisted = next((a for a in cached.get("attachments", []) if a.get("source_id") == attachment_source_id), None)
    if persisted is None:
        frappe.throw(_i18n("附件不存在或无权读取"), frappe.PermissionError)
    fresh = _fresh(doc)
    current = next((a for a in fresh.get("attachments", []) if a.get("source_id") == attachment_source_id), None)
    if not current or current.get("version") != persisted.get("version"):
        frappe.throw(_i18n("附件已变化或撤回，请重新同步"))
    # Exact persisted, typed URL. A fresh technical copy does not authorize arbitrary fetching.
    if attachment_path(current["url"]).startswith("/oa-archive/"):
        identity = current.get("archive_identity") or {}
        if identity.get("corp_id") != fresh.get("oa_identity", {}).get("corp_id") or identity.get("process_instance_id") != fresh.get("oa_identity", {}).get("process_instance_id"):
            frappe.throw(_i18n("归档附件来源身份不符"))
        manifest = _oa_connection().get_attachment_manifest(identity["process_instance_id"], identity["file_id"])
        if not manifest or manifest.get("corp_id") != identity["corp_id"] or manifest.get("archive_status") != "archived":
            frappe.throw(_i18n("附件尚未归档或无权读取，请在钉钉原单核对"))
        from deeplinkerp_branding.services.operating_oa_source import archive_attachments, archive_integrity
        verified = archive_attachments(identity, [manifest])
        if len(verified) != 1 or verified[0]["version"] != current["version"]:
            frappe.throw(_i18n("附件已变化或撤回，请重新同步"))
        try:
            expected_hash, expected_size = archive_integrity(manifest)
        except ValueError as error:
            frappe.throw(str(error))
        if expected_size > 20 * 1024 * 1024:
            frappe.throw(_i18n("附件超过本次下载上限"))
        from overseas_costing.services.import_service import _get_minio_archive_client
        try:
            content, _ = _get_minio_archive_client().download(manifest)
        except Exception:
            frappe.throw(_i18n("归档附件暂不可读取，请在钉钉原单核对"))
        if len(content) > 20 * 1024 * 1024:
            frappe.throw(_i18n("附件超过本次下载上限"))
        if len(content) != expected_size or hashlib.sha256(content).hexdigest() != expected_hash:
            frappe.throw(_i18n("归档附件校验不一致，请在钉钉原单核对"))
    else:
        content = _request(current["url"], download=True)
    filename = os.path.basename(str(persisted.get("filename") or "attachment").replace("\\", "/"))[:200]
    filename = "".join(c for c in filename if c.isprintable() and c not in '\r\n"') or "attachment"
    frappe.response["filename"] = filename
    frappe.response["filecontent"] = content
    frappe.response["type"] = "download"
