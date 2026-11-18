"""Mexico expense cache, company-scoped queries and review. No ledger writes."""
from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal
from pypika.enums import Order
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import frappe
from frappe import _
from frappe.utils import now_datetime

from . import mexico_expense_contract as contract
from . import operating_oa_http as http
from . import mexico_expense_templates as templates
from . import mexico_expense_projection as projection
from . import mexico_expense_fx as fx
from . import mexico_expense_classification as classification
from . import mexico_expense_mapping as company_mapping
from . import mexico_expense_insights as insights
from .operating_expense_contract import digest
from .purchase_payment_service import _require_fields

SOURCE = "Mexico Expense Source"
LINE = "Mexico Expense Line"
SETTINGS = "Mexico Expense Settings"
MAX_EXPORT_ROWS = 20000
FIELDS = ("name", "source", "source_version", "row_fingerprint", "modified", "approval_no", "applicant",
    "company", "source_entity", "entity_kind", "approval_type", "business_category", "classification_path", "classification_key", "salary_type", "category", "subcategory", "description", "amount", "amount_raw",
    "currency", "approval_status", "request_date", "planned_payment_date", "finance_approved_at", "finance_approved_date", "period_month", "period_start",
    "period_end", "period_kind", "period_version", "review_state", "issues", "base_issues", "review_note", "reviewed_by", "reviewed_at", "company_basis", "mapping_version")
_managed = ContextVar("mexico_expense_managed_write", default=False)


@contextmanager
def managed_write():
    token = _managed.set(True)
    try:
        yield
    finally:
        _managed.reset(token)


def validate_managed_document(doc):
    if not _managed.get():
        frappe.throw(_("Mexico expense records can only be changed through the workbench"), frappe.PermissionError)


def _manager(user=None):
    user = user or frappe.session.user
    return user == "Administrator" or "System Manager" in frappe.get_roles(user)


def _finance():
    if not _manager() and not {"Accounts User", "Accounts Manager"}.intersection(frappe.get_roles()):
        frappe.throw(_("Accounts permission is required"), frappe.PermissionError)
    if not frappe.has_permission(LINE, "read"):
        frappe.throw(_("Not permitted to read Mexico expenses"), frappe.PermissionError)
    _require_fields(LINE, set(FIELDS) - {"name", "modified"})


def _require_manager():
    if not _manager():
        frappe.throw(_("Only a System Manager can configure Mexico expense synchronization"), frappe.PermissionError)


def _can_manage_settings(settings, user=None):
    if not _manager(user):
        return False
    companies = set(_companies(user))
    return all(row.company in companies for row in settings.company_mappings or [])


def _require_settings_access(settings):
    _require_manager()
    if not _can_manage_settings(settings):
        frappe.throw(_("Access to all mapped subsidiaries is required to manage synchronization"), frappe.PermissionError)


def settings_has_permission(doc, user=None, ptype=None):
    return ptype in (None, "read", "export", "select", "print") and _can_manage_settings(_settings(), user)


def _settings(lock=False):
    return frappe.get_doc(SETTINGS, SETTINGS, for_update=True) if lock else frappe.get_single(SETTINGS)


def _templates(settings=None):
    settings = settings or _settings()
    configured = settings.get("templates")
    # Pre-migration compatibility; an explicitly empty table does not re-enable defaults.
    return configured if configured is not None else [t for t in templates.default_templates() if t["approval_type"] == "expense"]


def _template(row, settings=None):
    return templates.find(_templates(settings), row["corp_id"], row["process_code"])


def _template_key(template):
    return digest([template.get("corp_id"), template.get("process_code")])


def _template_facts(settings):
    return [{k: t.get(k) for k in ("corp_id", "process_code", "approval_type", "finance_node_names", "finance_activity_ids", "field_mapping", "classification_fields")}
            for t in sorted(_templates(settings), key=_template_key)]


def _companies(user=None):
    return frappe.get_list("Company", filters={"country": "Mexico", "is_group": 0}, pluck="name",
                           limit_page_length=1000, user=user or frappe.session.user)


def _company_access(company):
    if company not in _companies() or not frappe.has_permission("Company", "read", doc=company):
        frappe.throw(_("Not permitted to access this subsidiary"), frappe.PermissionError)


def permission_query_conditions(user=None):
    user = user or frappe.session.user
    companies = _companies(user)
    values = ",".join(frappe.db.escape(c) for c in companies)
    clauses = [f"company IN ({values})"] if companies else []
    if _manager(user):
        clauses.append("COALESCE(company, '') = ''")
    scope = "(" + " OR ".join(clauses) + ")" if clauses else "1=0"
    if not clauses:
        return scope
    if not _manager(user):
        version = frappe.db.escape(_settings().mapping_version or "")
        scope += f" AND (company_basis = 'manual' OR mapping_version = {version})"
    return scope


def has_permission(doc, user=None, ptype=None):
    if ptype not in (None, "read", "export", "select", "print", "report"):
        return False
    user = user or frappe.session.user
    if not doc or not doc.get("name"):
        return _manager(user) or bool({"Accounts User", "Accounts Manager"}.intersection(frappe.get_roles(user)))
    if not doc.get("company"):
        return _manager(user)
    if doc.company not in _companies(user):
        return False
    return _manager(user) or doc.company_basis == "manual" or doc.mapping_version == (_settings().mapping_version or "")


def source_permission_query_conditions(user=None):
    user = user or frappe.session.user
    if not _manager(user):
        return "1=0"
    companies = _companies(user)
    values = ",".join(frappe.db.escape(c) for c in companies)
    unauthorized = f"mx.company NOT IN ({values})" if companies else "1=1"
    quote = '"' if frappe.db.db_type == "postgres" else "`"
    version = frappe.db.escape(_settings().mapping_version or "")
    stale = f"mx.is_active = 1 AND COALESCE(mx.company_basis, '') <> 'manual' AND COALESCE(mx.mapping_version, '') <> {version}"
    return (f"NOT EXISTS (SELECT 1 FROM {quote}tabMexico Expense Line{quote} mx WHERE "
            f"mx.source = {quote}tabMexico Expense Source{quote}.name "
            f"AND ((COALESCE(mx.company, '') <> '' AND ({unauthorized})) OR ({stale})))")


def source_has_permission(doc, user=None, ptype=None):
    user = user or frappe.session.user
    if not _manager(user) or ptype not in (None, "read", "export", "select", "print"):
        return False
    if not doc or not doc.get("name"):
        return True
    allowed = set(_companies(user))
    version = _settings().mapping_version or ""
    rows = frappe.get_all(LINE, filters={"source": doc.name}, fields=["company", "is_active", "company_basis", "mapping_version"])
    return bool(rows) and all((not r.company or r.company in allowed) and
        (not r.is_active or r.company_basis == "manual" or (r.mapping_version or "") == version) for r in rows)


def _save(doc):
    with managed_write():
        return doc.insert(ignore_permissions=True) if doc.is_new() else doc.save(ignore_permissions=True)


def _store_projection(doc, values):
    """Stable rescans do not change modified or invalidate an open review form."""
    def canonical(field, value):
        if field in {"amount", "total_amount"}:
            return Decimal(str(value or 0))
        if field == "is_active":
            return int(value or 0)
        return str(value) if value not in (None, "") else ""
    # Empty links are intentional source facts. None would be replaced by the
    # inserting user's Company/Currency default in Document._set_defaults().
    values = {key: ("" if value is None and key in {"company", "currency"} else value)
              for key, value in values.items()}
    if doc.is_new() or any(canonical(field, doc.get(field)) != canonical(field, value) for field, value in values.items()):
        doc.update(values)
        _save(doc)


def _maps(settings):
    return {contract.entity_key(r.entity_kind, r.source_entity): r.company for r in settings.company_mappings or []}


def _upsert(row, settings):
    source, projected = projection.project(row, _template(row, settings), settings.business_timezone or "America/Mexico_City")
    existing = frappe.db.exists(SOURCE, source["source_key"])
    doc = frappe.get_doc(SOURCE, source["source_key"], for_update=True) if existing else frappe.new_doc(SOURCE)
    if existing and (doc.corp_id != source["corp_id"] or doc.process_code != source["process_code"]):
        frappe.throw(_("Approval instance identity conflicts with another template"))
    _store_projection(doc, source)
    previous = {r.name: r for r in frappe.get_all(LINE, filters={"source": doc.name}, fields=["*"])}
    active = set()
    mappings = _maps(settings)
    for raw in projected:
        old = previous.get(raw["line_key"])
        legacy_fingerprint = raw.pop("_legacy_row_fingerprint", None)
        prior_review = projection.compatible_previous_review(old, raw, source["source_version"], legacy_fingerprint)
        line = contract.apply_review(raw, prior_review, mappings, settings.mapping_version or "")
        line.update({k: source.get(k) for k in ("source_version", "approval_no", "applicant", "approval_status", "request_date", "planned_payment_date")})
        line.update(is_active=1, period_version=_period_version(settings), issues=json.dumps(line["issues"]), base_issues=json.dumps(raw["base_issues"]))
        item = frappe.get_doc(LINE, raw["line_key"], for_update=True) if old else frappe.new_doc(LINE)
        _store_projection(item, line)
        active.add(item.name)
    for name in set(previous) - active:
        item = frappe.get_doc(LINE, name, for_update=True)
        if item.is_active:
            item.is_active = 0
            _save(item)


def _connection():
    url, corps = http.configuration()
    if contract.CORP_ID not in corps:
        frappe.throw(_("The Mexico approval corporation is not enabled in the OA connection"))
    return url


@frappe.whitelist()
def get_context():
    _finance()
    settings = _settings()
    result = {"companies": _companies(), "can_review": _manager() or "Accounts Manager" in frappe.get_roles(),
        "can_manage": _can_manage_settings(settings), "can_assign_company": _manager(), "categories": [{"id": code, "label": _(label), "color": color}
            for code, label, color in contract.CATEGORIES],
        "approval_forms": [{"id": code, "label": _(label), "connected": any(t.get("approval_type") == code for t in _templates(settings))}
            for code, (label, bindings) in contract.APPROVAL_FORMS.items()],
        "subcategories": [{"id": code, "category": info[0], "label": _(info[1])} for code, info in contract.SUBCATEGORIES.items()],
        "issue_labels": {key: _(text) for key, text in contract.ISSUES.items()},
        "last_sync_at": settings.last_sync_at, "last_error": settings.last_error if _manager() else None,
        "enabled": bool(settings.enabled), "today": datetime.now(ZoneInfo(settings.business_timezone or "America/Mexico_City")).date().isoformat()}
    if result["can_manage"]:
        result["settings"] = {"config_version": settings.config_version or "", "business_timezone": settings.business_timezone or "America/Mexico_City",
            "templates": [{k: t.get(k) for k in (*templates.CONFIG_FIELDS, *templates.STATE_FIELDS)} for t in _templates(settings)],
            "company_mappings": [{"source_entity": r.source_entity, "entity_kind": r.entity_kind, "company": r.company} for r in settings.company_mappings or []]}
    return result


@frappe.whitelist()
def get_unmapped_entities(start=0):
    """Bounded, permission-scoped groups for the administrator's alias editor."""
    settings = _settings()
    _require_settings_access(settings)
    _finance()
    try:
        start = int(start)
        if start < 0 or start > 1000000:
            raise ValueError
    except (ValueError, TypeError):
        frappe.throw(_("Invalid mapping page"))
    from frappe.query_builder.functions import Count, Coalesce
    table = frappe.qb.DocType(LINE)
    query = frappe.qb.get_query(LINE, fields=[table.source_entity, table.entity_kind,
        Count(table.name).as_("line_count")], filters=[["is_active", "=", 1]], ignore_permissions=False)
    rows = (query.where(Coalesce(table.company, "") == "")
        .groupby(table.source_entity, table.entity_kind)
        .orderby(Count(table.name), order=Order.desc).orderby(table.source_entity).orderby(table.entity_kind)
        .offset(start).limit(51).run(as_dict=True))
    companies = _companies()
    mappings = [{"source_entity": r.source_entity, "entity_kind": r.entity_kind, "company": r.company}
                for r in settings.company_mappings or []]
    for row in rows[:50]:
        row["suggestions"] = company_mapping.suggest_companies(row.source_entity, row.entity_kind, companies, mappings)
    return {"rows": rows[:50], "has_more": len(rows) > 50, "start": start,
            "config_version": settings.config_version or ""}


@frappe.whitelist(methods=["POST"])
def save_settings(company_mappings, business_timezone, enabled, expected_config_version="", approval_templates=None):
    _require_manager()
    entries = frappe.parse_json(company_mappings)
    if not isinstance(entries, list) or len(entries) > 1000:
        frappe.throw(_("Invalid subsidiary mappings"))
    try:
        ZoneInfo(business_timezone)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        frappe.throw(_("Invalid business timezone"))
    clean, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("source_entity"), str) or not entry["source_entity"].strip() or len(entry["source_entity"]) > 500:
            frappe.throw(_("Invalid subsidiary mappings"))
        kind = entry.get("entity_kind", "entity")
        if kind not in {"entity", "department"}:
            frappe.throw(_("Invalid subsidiary mappings"))
        key = contract.entity_key(kind, entry["source_entity"])
        if key in seen:
            frappe.throw(_("Duplicate source entity mapping"))
        seen.add(key)
        _company_access(entry.get("company"))
        clean.append({"source_entity": entry["source_entity"].strip(), "entity_kind": kind, "company": entry["company"]})
    if enabled not in (0, 1, "0", "1", False, True):
        frappe.throw(_("Invalid synchronization setting"))
    settings = _settings(lock=True)
    _require_settings_access(settings)
    if (settings.config_version or "") != expected_config_version:
        frappe.throw(_("Settings changed; reload and try again"))
    old_templates = {_template_key(t): t for t in _templates(settings)}
    configured = None
    if approval_templates is not None:
        entries = frappe.parse_json(approval_templates)
        if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
            frappe.throw(_("Invalid approval template"))
        configured, identities = [], set()
        _base_url, allowed_corps = http.configuration()
        for entry in entries:
            try:
                item = templates.normalize(entry)
            except (ValueError, TypeError):
                frappe.throw(_("Invalid approval template"))
            identity = _template_key(item)
            if identity in identities or item["corp_id"] not in allowed_corps:
                frappe.throw(_("Duplicate or unauthorized approval template"))
            identities.add(identity)
            old = old_templates.get(identity)
            if old and item["approval_type"] != old.get("approval_type"):
                frappe.throw(_("An existing template cannot change approval form"))
            changed_rules = not old or any((item.get(k) or "") != (old.get(k) or "") for k in ("finance_node_names", "finance_activity_ids", "field_mapping", "classification_fields"))
            if item["enabled"] and (changed_rules or not old.get("enabled")):
                _preview_template(item, business_timezone)
            if old:
                item.update({k: old.get(k) for k in templates.STATE_FIELDS})
                if old.get("name"): item["name"] = old.get("name")
            configured.append(item)
        if set(old_templates) - identities:
            frappe.throw(_("Disable historical templates instead of removing them"))
    enabled = int(enabled)
    if enabled:
        _connection()
    mapping_version = digest(sorted(clean, key=lambda e: (e["entity_kind"], e["source_entity"])))
    old_facts = _template_facts(settings)
    if configured is not None: settings.set("templates", configured)
    changed = settings.mapping_version != mapping_version or settings.business_timezone != business_timezone or old_facts != _template_facts(settings)
    settings.update({"enabled": enabled, "business_timezone": business_timezone, "company_mappings": clean,
        "mapping_version": mapping_version, "config_version": digest([mapping_version, business_timezone, enabled, [{k: t.get(k) for k in templates.CONFIG_FIELDS} for t in _templates(settings)]])})
    _save(settings)
    if changed:
        frappe.enqueue("deeplinkerp_branding.services.mexico_expenses.reproject_cache", projection_version=_projection_version(settings),
            enqueue_after_commit=True, queue="long", timeout=180)
    return {"ok": True}


def _period_version(settings):
    # The shared cache guard also blocks facts projected before the mapping and
    # zero-amount fixes. Reprojection keeps the finance dates themselves unchanged.
    return digest(["mx-finance-period-v4.2-classification-review", settings.business_timezone or "America/Mexico_City", _template_facts(settings)])


def _projection_version(settings):
    # Sync scheduling does not change cached business facts.
    return digest(["mx-review-v2", settings.mapping_version or "", _period_version(settings)])


def reproject_cache(projection_version, start=0):
    settings = _settings(lock=True)
    if _projection_version(settings) != projection_version:
        return
    names = frappe.get_all(SOURCE, pluck="name", order_by="name asc", limit_start=int(start), limit_page_length=50)
    for name in names:
        raw = frappe.db.get_value(SOURCE, name, "raw_json")
        _upsert(json.loads(raw), settings)
    if len(names) == 50:
        frappe.enqueue("deeplinkerp_branding.services.mexico_expenses.reproject_cache", projection_version=projection_version,
            start=int(start) + 50, enqueue_after_commit=True, queue="long", timeout=180)


@frappe.whitelist(methods=["POST"])
def enqueue_sync():
    _require_settings_access(_settings())
    _connection()
    frappe.enqueue("deeplinkerp_branding.services.mexico_expenses.sync_page", force=True,
        enqueue_after_commit=True, queue="long", timeout=180)
    return {"queued": True}


def scheduled_sync():
    if frappe.db.exists("DocType", SETTINGS) and _settings().enabled:
        frappe.enqueue("deeplinkerp_branding.services.mexico_expenses.sync_page", enqueue_after_commit=True,
            queue="long", timeout=180)


def sync_page(force=False, template_key=None):
    """Each template owns its cursor. A failed job never advances another template."""
    from redis.exceptions import LockError
    if template_key is None:
        settings = _settings()
        if not force and not settings.enabled:
            return {"ok": True, "disabled": True}
        count = 0
        for template in _templates(settings):
            if template.get("enabled"):
                frappe.enqueue("deeplinkerp_branding.services.mexico_expenses.sync_page", force=force,
                    template_key=_template_key(template), enqueue_after_commit=True, queue="long", timeout=180)
                count += 1
        return {"ok": True, "queued": count}
    try:
        with frappe.cache.lock("mexico-expense-sync:" + frappe.local.site + ":" + template_key, timeout=170, blocking_timeout=0):
            frappe.db.savepoint("mx_sync")
            settings = _settings(lock=True)
            template = next((t for t in _templates(settings) if _template_key(t) == template_key), None)
            if not template or not template.get("enabled") or (not force and not settings.enabled):
                return {"ok": True, "disabled": True}
            try:
                url, corps = http.configuration()
                if template.get("corp_id") not in corps:
                    frappe.throw(_("Duplicate or unauthorized approval template"))
                rows, page = http.read_template_page(url, template.get("corp_id"), template.get("process_code"), cursor=template.get("cursor") or None)
                if template.get("cursor") and template.get("sync_until") != page["syncUntil"]:
                    raise http.OAInvalidCursor("Snapshot changed")
                if (template.get("page_count") or 0) >= http.MAX_PAGES:
                    frappe.throw(_("Mexico approval scan exceeds the configured page limit"))
                for row in rows:
                    _upsert(row, settings)
                template.update({"cursor": page["nextCursor"] if page["hasMore"] else None,
                    "sync_until": page["syncUntil"] if page["hasMore"] else None,
                    "page_count": (template.get("page_count") or 0) + 1 if page["hasMore"] else 0,
                    "last_sync_at": now_datetime(), "last_error": None})
                settings.last_sync_at = now_datetime()
                settings.last_error = next((t.get("last_error") for t in _templates(settings) if t.get("last_error")), None)
                _save(settings)
                if page["hasMore"]:
                    frappe.enqueue("deeplinkerp_branding.services.mexico_expenses.sync_page", force=force, template_key=template_key,
                        enqueue_after_commit=True, queue="long", timeout=180)
                return {"ok": True, "processed": len(rows), "has_more": page["hasMore"]}
            except Exception as exc:
                frappe.db.rollback(save_point="mx_sync")
                settings = _settings(lock=True)
                template = next(t for t in _templates(settings) if _template_key(t) == template_key)
                if isinstance(exc, http.OAInvalidCursor):
                    template.update({"cursor": None, "sync_until": None, "page_count": 0})
                template.last_error = _("Mexico expense synchronization failed; retry or contact an administrator")
                settings.last_error = template.get("last_error")
                _save(settings)
                frappe.log_error(title=_("Mexico Expense Sync Failed"), message=template.get("process_code") + ": " + type(exc).__name__)
                return {"ok": False}
    except LockError:
        return {"ok": False, "busy": True}


def _preview_template(template, business_timezone):
    url, corps = http.configuration()
    if template.get("corp_id") not in corps:
        frappe.throw(_("Duplicate or unauthorized approval template"))
    rows, _page = http.read_template_page(url, template.get("corp_id"), template.get("process_code"), limit=3)
    if not rows:
        frappe.throw(_("No sample approvals found; save this template disabled first"))
    issues, finance_nodes, count = set(), set(), 0
    for row in rows:
        source, lines = projection.project(row, template, business_timezone)
        count += len(lines)
        issues.update(issue for line in lines for issue in line["base_issues"])
        raw = row.get("raw_payload") or {}
        finance_nodes.update(t.get("taskGroupName", "") for t in raw.get("tasks", [])
                             if "财务" in t.get("taskGroupName", "") or "finanzas" in t.get("taskGroupName", "").lower())
    if "structure" in issues:
        frappe.throw(_("Template fields require mapping before synchronization can be enabled"))
    return {"applications": len(rows), "lines": count, "issues": sorted(issues), "finance_nodes": sorted(finance_nodes)}


@frappe.whitelist(methods=["POST"])
def preview_template(template, business_timezone="America/Mexico_City"):
    _require_settings_access(_settings())
    try:
        ZoneInfo(business_timezone)
        clean = templates.normalize(frappe.parse_json(template))
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        frappe.throw(_("Invalid approval template"))
    return _preview_template(clean, business_timezone)


def _filters(filters):
    values = frappe.parse_json(filters) if isinstance(filters, str) else filters or {}
    allowed = {"month", "from_date", "to_date", "basis", "company", "approval_type", "category", "subcategory", "currency", "reporting_currency", "status", "keyword", "issue", "classification_key", "classification_exact", "confirmed_only"}
    if not isinstance(values, dict) or set(values) - allowed:
        frappe.throw(_("Invalid Mexico expense filters"))
    if any(not isinstance(v, str) or len(v) > 200 for v in values.values() if v is not None):
        frappe.throw(_("Invalid Mexico expense filters"))
    values = {"month": datetime.now(ZoneInfo(_settings().business_timezone or "America/Mexico_City")).strftime("%Y-%m"), "basis": "period", "currency": "MXN", "status": "finance_approved", **values}
    if values.get("reporting_currency"):
        values["currency"] = values["reporting_currency"]
    try:
        if "from_date" in values or "to_date" in values:
            first, last = date.fromisoformat(values.get("from_date") or ""), date.fromisoformat(values.get("to_date") or "")
            if first.isoformat() != values["from_date"] or last.isoformat() != values["to_date"] or first > last or not (1900 <= first.year <= last.year <= 2200):
                raise ValueError()
        else:
            first, last = contract.month_bounds(values["month"])
        values.update(from_date=first.isoformat(), to_date=last.isoformat())
    except (ValueError, TypeError):
        frappe.throw(_("Enter a valid start and end date; the start date must not be after the end date"))
    if values["basis"] not in {"period", "request", "planned"} or values["currency"] not in {"MXN", "USD", "CNY"} or values["status"] not in {"all", "active", "finance_approved", "pending", "approved", "rejected", "terminated", "withdrawn", "unknown"}:
        frappe.throw(_("Invalid Mexico expense filters"))
    if values.get("company"):
        _company_access(values["company"])
    if values.get("approval_type") and values["approval_type"] not in contract.APPROVAL_FORMS:
        frappe.throw(_("Invalid approval form"))
    if values.get("category") and values["category"] not in {c[0] for c in contract.CATEGORIES}:
        frappe.throw(_("Invalid expense category"))
    if values.get("subcategory") and values["subcategory"] not in contract.SUBCATEGORIES:
        frappe.throw(_("Invalid expense category"))
    if values.get("classification_key") and (not values.get("approval_type") or not re.fullmatch(classification.KEY_PATTERN, values["classification_key"])):
        frappe.throw(_("Invalid expense category"))
    if values.get("confirmed_only") not in (None, "", "1"):
        frappe.throw(_("Invalid Mexico expense filters"))
    if values.get("classification_exact") not in (None, "", "1"):
        frappe.throw(_("Invalid expense category"))
    if values.get("issue") and values["issue"] not in contract.ISSUES:
        frappe.throw(_("Invalid review issue"))
    if values.get("keyword") and (not isinstance(values["keyword"], str) or len(values["keyword"]) > 200):
        frappe.throw(_("Invalid search text"))
    return values


def _query(values, fields, *, confirmed=False, review=False):
    """All lists, aggregates, native reports and exports share this scope."""
    from pypika.functions import Coalesce
    table = frappe.qb.DocType(LINE)
    # Chart drilldown must reconcile to the confirmed amounts displayed in the chart.
    confirmed = confirmed or bool((values.get("classification_key") or values.get("confirmed_only")) and not review)
    filters = [["is_active", "=", 1]]
    for field in ("company", "category", "subcategory"):
        if values.get(field):
            filters.append([field, "=", values[field]])
    approval_scope = None
    if values.get("approval_type"):
        bindings = [(t.get("corp_id"), t.get("process_code")) for t in _templates() if t.get("approval_type") == values["approval_type"]]
        if bindings:
            from pypika import Criterion
            source = frappe.qb.DocType(SOURCE)
            templates = Criterion.any((source.corp_id == corp) & (source.process_code == code) for corp, code in bindings)
            approval_scope = table.source.isin(frappe.qb.from_(source).select(source.name).where(templates))
        else:
            # Reserved forms have no data yet, never reuse Gastos rows under another label.
            approval_scope = table.name.isnull()
    status = values["status"]
    if status in {"active", "finance_approved"}:
        filters.append(["approval_status", "in", ["pending", "approved"]])
    elif status != "all":
        filters.append(["approval_status", "=", status])
    settings = _settings()
    period_current = Coalesce(table.period_version, "") == _period_version(settings)
    currency_scope = table.currency == values["currency"]
    if review:
        currency_scope |= table.currency.isnull() | (table.currency == "")
    def scoped_query(selected):
        query = frappe.qb.get_query(LINE, fields=selected, filters=filters, ignore_permissions=False)
        if not values.get("reporting_currency"):
            query = query.where(currency_scope)
        if approval_scope is not None:
            query = query.where(approval_scope)
        if values.get("classification_key"):
            key = values["classification_key"]
            match = table.classification_key == key
            if values.get("classification_exact") != "1":
                match |= table.classification_key.like(key + "/%")
            query = query.where(match)
        if values.get("keyword"):
            keyword = "%" + values["keyword"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            query = query.where(table.approval_no.like(keyword) | table.description.like(keyword) | table.source_entity.like(keyword))
        return query
    # An old period cannot determine whether a row belongs to the requested month.
    # Block partial totals until all visible candidates have current period facts.
    if not review and scoped_query(["name"]).where(~period_current).limit(1).run():
        frappe.throw(_("Expense periods are being recalculated; view Data Review and retry after recalculation"))
    query = scoped_query(fields)
    if status == "finance_approved":
        query = query.where(table.finance_approved_date.isnotnull())
    mapping_current = (table.company_basis == "manual") | (Coalesce(table.mapping_version, "") == (settings.mapping_version or ""))
    if not review:
        query = query.where(period_current)
    if confirmed:
        query = query.where((table.review_state == "confirmed") & mapping_current)
    if review:
        query = query.where((table.review_state != "confirmed") | ~mapping_current | ~period_current)
    basis = {"period": table.finance_approved_date, "request": table.request_date, "planned": table.planned_payment_date}[values["basis"]]
    if review and values["basis"] == "period":
        from pypika.terms import Case
        basis = Case().when(period_current, Coalesce(table.finance_approved_date, table.request_date)).else_(table.request_date)
    if values.get("from_date") and values.get("to_date"):
        first, last = date.fromisoformat(values["from_date"]), date.fromisoformat(values["to_date"])
    else:
        # Legacy month callers still select the complete calendar month.
        first, last = contract.month_bounds(values["month"])
    query = query.where((basis >= first) & (basis <= last))
    if review and values.get("issue"):
        issue = values["issue"]
        criterion = table.issues.like('%"' + issue + '"%')
        if issue == "stale":
            criterion |= ~mapping_current
        elif issue == "period_stale":
            criterion |= ~period_current
        query = query.where(criterion)
    return query


def _conversion_expression(values, conversion, **scope):
    from pypika.functions import Cast, Coalesce, Substring
    from pypika.terms import Case, Function
    table = frappe.qb.DocType(LINE)
    month = Substring(Cast(Coalesce(table.finance_approved_date, table.request_date), "CHAR(10)"), 1, 7)
    pairs = _query(values, [table.currency, month.as_("fx_month")], **scope).distinct().run(as_dict=True)
    factor = Case().when(table.currency == conversion.target, 1)
    for row in pairs:
        if row["currency"] == conversion.target or row["currency"] not in fx.CURRENCIES or not row["fx_month"]:
            continue
        rate_date = str(row["fx_month"]) + "-01"
        rate = conversion.rate(row["currency"], rate_date)
        factor = factor.when((table.currency == row["currency"]) & (month == row["fx_month"]), rate)
    # Round each line before summation, matching detail rows and Excel exactly.
    return Function("ROUND", table.amount * factor.else_(None), 2)


def _counts(values, **scope):
    from pypika.functions import Count
    table = frappe.qb.DocType(LINE)
    return _query(values, [Count(table.source).distinct().as_("applications"), Count(table.name).as_("lines")], **scope).run(as_dict=True)[0]


def _aggregates(values, groups=(), conversion=None, **kwargs):
    from pypika.functions import Cast, Count, Sum
    from pypika.terms import Case
    table = frappe.qb.DocType(LINE)
    amount = table.amount
    if values.get("reporting_currency"):
        conversion = conversion or fx.Conversion(values["reporting_currency"])
        amount = _conversion_expression(values, conversion, **kwargs)
    # Frappe MariaDB decoders return DECIMAL as float; transfer totals as text
    # so large cross-currency aggregates retain every cent.
    total_amount = Cast(Sum(amount), "CHAR(65)") if values.get("reporting_currency") else Sum(amount)
    invalid = (table.base_issues.like('%"amount"%') | table.base_issues.like('%"amount_pending"%') |
               table.base_issues.like('%"total"%') | table.base_issues.like('%"structure"%') |
               table.base_issues.like('%"identity"%') | table.base_issues.like('%"currency"%') |
               table.currency.isnull() | table.currency.notin(sorted(fx.CURRENCIES)) | amount.isnull())
    known_amount = Cast(Sum(Case().when(invalid, 0).else_(amount)), "CHAR(65)")
    fields = [getattr(table, field) for field in groups] + [total_amount.as_("amount"), Count(table.source).distinct().as_("applications"), Count(table.name).as_("lines"),
        Sum(Case().when(invalid, 1).else_(0)).as_("invalid_amounts"), known_amount.as_("known_amount")]
    if values.get("reporting_currency"):
        fields.append(Sum(Case().when(table.finance_approved_date.isnull(), 1).else_(0)).as_("estimated_lines"))
    query = _query(values, fields, **kwargs)
    if groups:
        query = query.groupby(*(getattr(table, field) for field in groups))
    return query.run(as_dict=True)


def _public_lines(rows):
    settings = _settings()
    period_version = _period_version(settings)
    return [_public_line(row, settings.mapping_version or "", period_version) for row in rows]


def _public_line(row, mapping_version, period_version):
    item = dict(row)
    if item.get("classification_path"):
        path = json.loads(item["classification_path"])
        item["business_category"] = " → ".join(_(node["label"]) if node.get("translate") else node["label"] for node in path)
    item["issues"] = json.loads(item.get("issues") or "[]")
    if item.get("company_basis") != "manual" and item.get("mapping_version") != mapping_version:
        item["issues"] = sorted(set(item["issues"] + ["stale"]))
        item["review_state"] = "needs_review"
    if item.get("period_version") != period_version:
        item["issues"] = sorted(set(item["issues"] + ["period_stale"]))
        item["review_state"] = "needs_review"
        item.update(dict.fromkeys(contract.PERIOD_FIELDS))
    if contract.exact_amount(item.get("amount_raw")) is None:
        item["amount"] = None
    elif item.get("amount") is not None:
        item["amount"] = str(item["amount"])
    return item


@frappe.whitelist()
def get_lines(filters=None, start=0, page_length=25, review=False):
    _finance()
    values = _filters(filters)
    try:
        start, page_length = int(start), int(page_length)
        review = review in (True, 1, "1", "true")
        if not 0 <= start <= 1000000 or not 1 <= page_length <= 100:
            raise ValueError()
    except (ValueError, TypeError):
        frappe.throw(_("Invalid pagination"))
    table = frappe.qb.DocType(LINE)
    total = _counts(values, review=review)
    data = _query(values, list(FIELDS), review=review).orderby(table.request_date, order=Order.desc).orderby(table.name).limit(page_length).offset(start).run(as_dict=True)
    rows = _public_lines(data)
    if values.get("reporting_currency"):
        conversion = fx.Conversion(values["reporting_currency"])
        rows = [conversion.line(row) for row in rows]
    return {"rows": rows, "total": int(total.get("lines") or 0), "applications": int(total.get("applications") or 0)}


@frappe.whitelist()
def get_overview(filters=None):
    _finance()
    values = _filters(filters)
    conversion = fx.Conversion(values["reporting_currency"]) if values.get("reporting_currency") else None
    def aggregate(groups=(), **scope):
        return _aggregates(values, groups, conversion=conversion, **scope)
    def clean(rows):
        return [{**r, "amount": str(r.get("amount") or 0)} for r in rows]
    nodes = classification.composition(aggregate(("classification_path",), confirmed=True)) if values.get("approval_type") else []
    nodes = [{**node, "label": _(node["label"]) if node.get("translate") else node["label"]} for node in nodes]
    return {"metrics": clean(aggregate(("approval_status",))),
        "forms": clean(aggregate(("approval_type",), confirmed=True)),
        "company_forms": clean(aggregate(("company", "approval_type"), confirmed=True)),
        "categories": clean(aggregate(("category",), confirmed=True)),
        "composition": nodes,
        "companies": clean(aggregate(("company", "category"), confirmed=True)),
        "company_totals": clean(aggregate(("company",), confirmed=True)),
        "trend": clean(aggregate(({"period": "period_month", "request": "request_date", "planned": "planned_payment_date"}[values["basis"]], "category"), confirmed=True)),
        "review_count": int((_counts(values, review=True).get("applications") or 0))}


def report_data(filters=None, kind="company", *, serialize=False):
    _finance()
    values = _filters(filters)
    if not isinstance(kind, str) or kind not in {"company", "payroll", "trend"}:
        frappe.throw(_("Invalid expense report"))
    if kind == "payroll":
        values.update(category="personnel", basis="period")
        groups = ("company", "period_month", "subcategory")
    else:
        group_field = "category" if values.get("approval_type") else "approval_type"
        groups = ("company", group_field) if kind == "company" else ({"period": "period_month", "request": "request_date", "planned": "planned_payment_date"}[values["basis"]], group_field)
    data = _aggregates(values, groups, confirmed=True)
    keys = [k for k, v in contract.SUBCATEGORIES.items() if v[0] == "personnel"] if kind == "payroll" else [c[0] for c in contract.CATEGORIES]
    labels = {k: v[1] for k, v in contract.SUBCATEGORIES.items()} if kind == "payroll" else {c[0]: c[1] for c in contract.CATEGORIES}
    if kind != "payroll" and group_field == "approval_type":
        keys = list(contract.APPROVAL_FORMS)
        labels = {code: info[0] for code, info in contract.APPROVAL_FORMS.items()}
    first = "month" if kind == "trend" else "company"
    columns = [{"fieldname": first, "label": "Reporting Month" if first == "month" else "Subsidiary", "fieldtype": "Data", "width": 190}]
    if kind == "payroll":
        columns.append({"fieldname": "month", "label": "Reporting Month", "fieldtype": "Data", "width": 100})
    columns += [{"fieldname": key, "label": labels[key], "fieldtype": "Currency", "options": "currency", "width": 140} for key in keys]
    columns += [{"fieldname": "total", "label": "Total", "fieldtype": "Currency", "options": "currency", "width": 155},
                {"fieldname": "currency", "label": "Reporting Currency" if values.get("reporting_currency") else "Currency", "fieldtype": "Data", "width": 100}]
    result = {}
    for row in data:
        month = str(row.get(groups[0] if kind == "trend" else "period_month") or "")[:7]
        key = month if kind == "trend" else (row["company"], month if kind == "payroll" else "")
        out = result.setdefault(key, {first: month if kind == "trend" else row["company"], "month": month, "currency": values["currency"], **{k: Decimal(0) for k in keys}, "total": Decimal(0)})
        category = row["subcategory" if kind == "payroll" else group_field]
        if category in keys:
            amount = Decimal(str(row["amount"] or 0))
            out[category] += amount
            out["total"] += amount
    rows = sorted(result.values(), key=lambda r: (str(r[first]), r.get("month", "")))
    if serialize and values.get("reporting_currency"):
        # Frappe serializes Decimal as float. Keep exact cents on both HTTP paths.
        rows = [{key: format(value, ".2f") if isinstance(value, Decimal) else value
                 for key, value in row.items()} for row in rows]
    return columns, rows


@frappe.whitelist()
def get_report(filters=None, kind="company"):
    columns, data = report_data(filters, kind, serialize=True)
    return {"columns": [{**c, "label": _(c["label"])} for c in columns], "rows": data}


@frappe.whitelist()
def get_report_insights(filters=None, kind="company"):
    _finance()
    if not isinstance(kind, str) or kind not in {"company", "payroll", "trend"}:
        frappe.throw(_("Invalid expense report"))
    values = _filters(filters)
    if kind == "payroll":
        values.update(category="personnel", basis="period")
    today = datetime.now(ZoneInfo(_settings().business_timezone or "America/Mexico_City")).date().isoformat()
    window = insights.comparison_period(values["from_date"], values["to_date"], today)
    conversion = fx.Conversion(values["reporting_currency"]) if values.get("reporting_currency") else None
    def aggregate(scope, groups=(), **flags):
        return _aggregates(scope, groups, conversion=conversion, **flags)
    companies = aggregate(values, ("company",), confirmed=True)
    included = _counts(values, confirmed=True)
    review_values = {**values, "status": "active", "issue": ""}
    pending = aggregate(review_values, review=True)
    known = sum((insights.amount(r["known_amount"]) for r in pending), Decimal(0))
    unknown = sum(int(r["invalid_amounts"] or 0) for r in pending)
    total = sum((insights.amount(r["amount"]) for r in companies), Decimal(0))
    basis_field = {"period": "period_month", "request": "request_date", "planned": "planned_payment_date"}[values["basis"]]
    monthly = {}
    for row in aggregate(values, (basis_field,), confirmed=True):
        month = str(row[basis_field])[:7]
        monthly[month] = monthly.get(month, Decimal(0)) + insights.amount(row["amount"])
    import calendar
    trend = []
    cursor = date.fromisoformat(values["from_date"]).replace(day=1)
    end_month = date.fromisoformat(values["to_date"]).replace(day=1)
    while cursor <= end_month:
        monthly.setdefault(cursor.strftime("%Y-%m"), Decimal(0))
        cursor = insights.shift_month(cursor, 1)
    for month, value in sorted(monthly.items()):
        first = date.fromisoformat(month + "-01")
        last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
        trend.append({"month": month, "amount": insights.money(value),
                      "partial": values["from_date"] > first.isoformat() or min(values["to_date"], today) < last.isoformat()})
    comparison, movers = None, []
    if window:
        current_values = {**values, "from_date": window["from_date"], "to_date": window["to_date"]}
        previous_values = {**values, "from_date": window["previous_from"], "to_date": window["previous_to"]}
        current = aggregate(current_values, ("company",), confirmed=True)
        previous = aggregate(previous_values, ("company",), confirmed=True)
        comparison = {**window, **insights.change(sum((insights.amount(r["amount"]) for r in current), Decimal(0)),
                                                sum((insights.amount(r["amount"]) for r in previous), Decimal(0)))}
        comparison["current_applications"] = int(_counts(current_values, confirmed=True)["applications"])
        comparison["previous_applications"] = int(_counts(previous_values, confirmed=True)["applications"])
        company_changes = insights.ranking(current, previous, "company")
        company_changes.sort(key=lambda row: -abs(insights.amount(row["delta"])))
        field = "subcategory" if kind == "payroll" else "category"
        movers = insights.ranking(aggregate(current_values, (field,), confirmed=True),
                                  aggregate(previous_values, (field,), confirmed=True), field)
        movers.sort(key=lambda row: -abs(insights.amount(row["delta"])))
    else:
        company_changes = []
    return {"total": insights.money(total), "company_count": len(companies), "included": included,
            "pending": {**_counts(review_values, review=True), "known_amount": insights.money(known), "unknown_lines": unknown},
            "companies": insights.ranking(companies, [], "company"), "company_changes": company_changes,
            "comparison": comparison, "movers": movers[:5], "trend": trend, "today": today}


@frappe.whitelist()
def export_report(filters=None, kind="company"):
    _finance()
    if not frappe.permissions.can_export(LINE):
        frappe.throw(_("Not permitted to export Mexico expenses"), frappe.PermissionError)
    values = _filters(filters)
    if not isinstance(kind, str) or kind not in {"company", "payroll", "trend", "details", "review"}:
        frappe.throw(_("Invalid expense report"))
    if kind == "payroll":
        values.update(category="personnel", basis="period")
    if kind in {"details", "review"}:
        data = _query(values, list(FIELDS), review=kind == "review").limit(MAX_EXPORT_ROWS + 1).run(as_dict=True)
        if len(data) > MAX_EXPORT_ROWS:
            frappe.throw(_("Export exceeds 20000 rows; narrow the filters"))
        columns = [{"fieldname": k, "label": label} for k, label in (("approval_no", "Approval Number"), ("company", "Subsidiary"), ("source_entity", "Source Entity"), ("category", "Expense Category"), ("subcategory", "Expense Subcategory"), ("description", "Expense Description"), ("finance_approved_date", "MX Finance Approval Date"), ("period_month", "Reporting Month"), ("period_start", "Expense Period Start"), ("period_end", "Expense Period End"), ("amount", "Amount"), ("currency", "Currency"), ("approval_status", "Approval Status"), ("issues", "Review Issues"), ("approval_type", "Approval Form"), ("business_category", "Business Category"), ("salary_type", "Salary Type"))]
        data = _public_lines(data)
        if values.get("reporting_currency"):
            conversion = fx.Conversion(values["reporting_currency"])
            data = [conversion.line(row) for row in data]
            for column in columns:
                if column["fieldname"] in {"amount", "currency"}:
                    column["label"] = "Original Amount" if column["fieldname"] == "amount" else "Original Currency"
            columns += [{"fieldname": key, "label": label} for key, label in (
                ("converted_amount", "Converted Amount"), ("reporting_currency", "Reporting Currency"),
                ("exchange_rate", "Exchange Rate"), ("exchange_rate_date", "Exchange Rate Date"),
                ("fx_date_basis", "Exchange Rate Basis"), ("fx_estimated", "Estimated Conversion"))]
        for row in data:
            if values.get("reporting_currency"):
                row["converted_amount"] = Decimal(row["converted_amount"]) if row["converted_amount"] is not None else None
                row["exchange_rate"] = Decimal(row["exchange_rate"]) if row["exchange_rate"] is not None else None
                row["fx_date_basis"] = _("Finance Approval Month") if row["fx_date_basis"] == "finance" else _("Application Month")
                row["fx_estimated"] = _("Yes") if row["fx_estimated"] else _("No")
            if row["amount"] is not None:
                row["amount"] = Decimal(row["amount"])
            row["approval_type"] = _(contract.APPROVAL_FORMS.get(row.get("approval_type"), ("Unclassified",))[0])
            row["category"] = _({c[0]: c[1] for c in contract.CATEGORIES}.get(row["category"], "Unclassified"))
            row["subcategory"] = _(contract.SUBCATEGORIES.get(row["subcategory"], (None, "Unclassified"))[1])
            row["approval_status"] = _(STATUS_LABELS.get(row["approval_status"], "Unknown"))
            row["issues"] = "; ".join(_(contract.ISSUES[k]) for k in row["issues"] if k in contract.ISSUES)
    else:
        columns, data = report_data(values, kind)
    from frappe.utils.xlsxutils import build_xlsx_response
    def cell(value):
        # Excel numeric cells retain only 15 significant digits. Preserve larger
        # exact amounts as text while leaving ordinary monetary cells numeric.
        if isinstance(value, Decimal) and len(value.normalize().as_tuple().digits) > 15:
            return format(value, "f")
        if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
            return "'" + value
        return value
    metadata = [[_("Mexico Expenses"), _("OA approvals do not prove actual payment")],
        [_("Date Range"), values["from_date"] + " ~ " + values["to_date"]], [_("Date Basis"), _(BASIS_LABELS[values["basis"]])],
        [_("Approval Status"), _(STATUS_LABELS.get(values["status"], values["status"])),
            _("Approval Form"), _(contract.APPROVAL_FORMS[values["approval_type"]][0]) if values.get("approval_type") else _("All Approval Forms")], [_("Reporting Currency") if values.get("reporting_currency") else _("Currency"), values["currency"]], []]
    if values.get("reporting_currency"):
        metadata.insert(-1, [_("Exchange Rate Basis"), _("Month-start rates by finance approval month; pending approvals use application month estimates")])
    matrix = metadata + [[_(c["label"]) for c in columns]] + [[cell(row.get(c["fieldname"])) for c in columns] for row in data]
    build_xlsx_response(matrix, "Mexico-Expenses-" + values["from_date"] + "_" + values["to_date"])


STATUS_LABELS = {"finance_approved": "Finance Approved", "all": "All Statuses", "active": "Active Applications", "pending": "In Approval", "approved": "Approved", "rejected": "Rejected", "terminated": "Terminated", "withdrawn": "Withdrawn", "unknown": "Unknown"}
BASIS_LABELS = {"period": "Expense Period", "request": "Application Date", "planned": "Planned Payment Date"}


def _validate_line_key(name):
    if not isinstance(name, str) or len(name) != 64 or any(c not in "0123456789abcdef" for c in name):
        frappe.throw(_("Invalid source line key"))


def _line(name, lock=False):
    _validate_line_key(name)
    doc = frappe.get_doc(LINE, name, for_update=lock)
    doc.check_permission("read")
    if not doc.is_active:
        frappe.throw(_("This expense line was removed from the source; refresh the page"))
    return doc


def _whole_source_allowed(source):
    # Historic memberships still protect comments/attachments on shared approvals.
    docs = frappe.get_all(LINE, filters={"source": source}, fields=["name", "company", "is_active", "company_basis", "mapping_version"])
    companies, manager = set(_companies()), _manager()
    version = _settings().mapping_version or ""
    return bool(docs) and all(
        ((not row.company and manager) or row.company in companies)
        and (not row.is_active or row.company_basis == "manual" or (row.mapping_version or "") == version)
        for row in docs
    )


def _dingtalk_url(source):
    """Reuse the costing link builder with a stored instance ID, never a source URL."""
    instance_id = str(source.get("process_instance_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", instance_id):
        return ""
    try:
        from overseas_costing.utils.dingtalk import build_desktop_approval_url
    except ModuleNotFoundError as exc:
        # Branding can be installed without the optional costing app.
        if exc.name not in {"overseas_costing", "overseas_costing.utils", "overseas_costing.utils.dingtalk"}:
            raise
        return ""
    return build_desktop_approval_url(instance_id)


@frappe.whitelist()
def get_detail(line_key, reporting_currency=None):
    _finance()
    if reporting_currency is not None and (not isinstance(reporting_currency, str) or reporting_currency not in fx.CURRENCIES):
        frappe.throw(_("Invalid reporting currency"))
    doc = _line(line_key)
    source = frappe.get_doc(SOURCE, doc.source)
    whole = _whole_source_allowed(doc.source)
    # Native company and field scope still applies to each visible sibling.
    siblings = frappe.get_list(LINE, filters={"source": doc.source, "is_active": 1}, fields=list(FIELDS), limit_page_length=2001)
    if len(siblings) > 2000:
        frappe.throw(_("Too many source lines; use the filtered expense list"))
    settings = _settings()
    mapping_version, period_version = settings.mapping_version or "", _period_version(settings)
    visible = [_public_line(row, mapping_version, period_version) for row in siblings]
    incomplete = any(row["amount"] is None or set(json.loads(row.get("base_issues") or "[]")) &
        {"amount", "amount_pending", "total", "structure", "identity"} for row in visible)
    result = {"line": _public_line({field: doc.get(field) for field in FIELDS}, mapping_version, period_version), "lines": visible,
        "whole_source": whole, "visible_amount": None if incomplete else str(sum((Decimal(row["amount"]) for row in visible), Decimal(0)))}
    if reporting_currency:
        conversion = fx.Conversion(reporting_currency)
        result["line"] = conversion.line(result["line"])
        result["lines"] = [conversion.line(row) for row in visible]
        result["visible_currency"] = reporting_currency
        result["visible_amount"] = None if incomplete or any(row["converted_amount"] is None for row in result["lines"]) else str(sum((Decimal(row["converted_amount"]) for row in result["lines"]), Decimal(0)))
    if whole:
        from .operating_oa_workflow import workflow
        result.update(workflow=workflow(json.loads(source.raw_json)), payee=source.payee, payment_terms=source.payment_terms, details=json.loads(source.get("details_json") or "{}"))
        result["dingtalk_url"] = _dingtalk_url(source)
    return result


@frappe.whitelist(methods=["POST"])
def review_line(line_key, expected_source_version, expected_modified, month=None, period_start=None, period_end=None,
                period_kind=None, company=None, note=None):
    _finance()
    if not _manager() and "Accounts Manager" not in frappe.get_roles():
        frappe.throw(_("Only an Accounts Manager can review expense lines"), frappe.PermissionError)
    if not isinstance(note, str) or not note.strip() or len(note) > 1000:
        frappe.throw(_("A review note is required"))
    _validate_line_key(line_key)
    settings = _settings(lock=True)
    # Keep lock order consistent with synchronization: settings, source, line.
    source_name = frappe.db.get_value(LINE, line_key, "source")
    if not source_name:
        frappe.throw(_("Expense line not found"))
    source = frappe.get_doc(SOURCE, source_name, for_update=True)
    doc = _line(line_key, lock=True)
    if doc.source_version != expected_source_version or str(doc.modified) != expected_modified:
        frappe.throw(_("Expense source changed; reload and review again"))
    if doc.period_version != _period_version(settings):
        frappe.throw(_("Expense periods are being recalculated; view Data Review and retry after recalculation"))
    if company and company != doc.company:
        _require_manager()
        _company_access(company)
        if "allocation" in json.loads(doc.base_issues or "[]"):
            frappe.throw(_("Correct the subsidiary allocation in OA before assigning a company"))
        doc.company, doc.company_basis = company, "manual"
    period, finance_issues = contract.finance_period(json.loads(source.raw_json), settings.business_timezone or "America/Mexico_City", **templates.finance_options(_template({"corp_id": source.corp_id, "process_code": source.process_code}, settings)))
    requested = {"period_month": month + "-01" if isinstance(month, str) and month else month,
                 "period_start": period_start, "period_end": period_end, "period_kind": period_kind}
    if any(value not in (None, "") and value != period[key] for key, value in requested.items()):
        frappe.throw(_("Expense periods follow MX Finance approval and cannot be entered manually"))
    doc.update(period)
    doc.period_version = _period_version(settings)
    doc.reviewed_fingerprint, doc.reviewed_by, doc.reviewed_at = doc.row_fingerprint, frappe.session.user, now_datetime()
    doc.review_note = note.strip()
    problems = json.loads(doc.base_issues or "[]") + finance_issues
    if not doc.period_month:
        problems.append("period")
    if not doc.company:
        problems.append("company")
    if doc.company_basis != "manual" and doc.mapping_version != (settings.mapping_version or ""):
        problems.append("stale")
    doc.issues = json.dumps(sorted(set(problems)))
    doc.review_state = "confirmed" if not problems else "needs_review"
    _save(doc)
    return {"ok": True, "review_state": doc.review_state}


def _attachment_source(line_key):
    _finance()
    line = _line(line_key)
    if not _whole_source_allowed(line.source):
        frappe.throw(_("Access to all subsidiaries in this approval is required to view its attachments"), frappe.PermissionError)
    source = frappe.get_doc(SOURCE, line.source)
    url, corps = http.configuration()
    if source.corp_id not in corps:
        frappe.throw(_("Duplicate or unauthorized approval template"))
    rows, next_cursor = http.read_page(url, (source.corp_id,), 2, "", identity={"corp_id": source.corp_id, "process_instance_id": source.process_instance_id})
    if len(rows) != 1 or rows[0].get("process_code") != source.process_code:
        frappe.throw(_("Approval source is unavailable; synchronize again"))
    current, projected_lines = projection.project(rows[0], _template(rows[0]), _settings().business_timezone or "America/Mexico_City")
    if current["source_version"] != source.source_version or current["approval_status"] == "withdrawn":
        frappe.throw(_("Approval source changed; synchronize before reading attachments"))
    return url, source


@frappe.whitelist()
def get_attachments(line_key):
    url, source = _attachment_source(line_key)
    manifests = http.attachment_manifests(url, [{"corp_id": source.corp_id, "process_code": source.process_code,
        "process_instance_id": source.process_instance_id}], exact=True)
    return [{"file_id": m["file_id"], "filename": m["file_name"], "status": m["archive_status"]} for m in manifests]


@frappe.whitelist()
def download_attachment(line_key, file_id):
    url, source = _attachment_source(line_key)
    if not isinstance(file_id, str) or len(file_id) > 200:
        frappe.throw(_("Invalid attachment identity"))
    manifests = http.attachment_manifests(url, [{"corp_id": source.corp_id, "process_code": source.process_code,
        "process_instance_id": source.process_instance_id}], exact=True)
    matches = [m for m in manifests if m["file_id"] == file_id and m["archive_status"] == "archived"]
    if len(matches) != 1:
        frappe.throw(_("Attachment is unavailable or not archived"))
    manifest = matches[0]
    from .operating_oa_source import archive_integrity
    expected_hash, expected_size = archive_integrity(manifest)
    content = http.download_attachment(url, {"corp_id": source.corp_id, "process_instance_id": source.process_instance_id, "file_id": file_id}, expected_size)
    if len(content) != expected_size or hashlib.sha256(content).hexdigest() != expected_hash:
        frappe.throw(_("Attachment integrity verification failed"))
    filename = os.path.basename(str(manifest["file_name"] or "attachment").replace("\\", "/"))[:200]
    filename = "".join(c for c in filename if c.isprintable() and c not in '\r\n"') or "attachment"
    frappe.response.update(filename=filename, filecontent=content, type="binary")
