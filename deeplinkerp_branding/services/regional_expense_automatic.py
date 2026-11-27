"""Derive ownership from exact source identities and Company.country.

Automatic plans live in the existing allocation JSON. A saved manual plan has
no automatic marker and is never overwritten. No external requests are made.
"""
from collections import defaultdict
import re
import unicodedata

import frappe

from .operating_expense_contract import digest
from . import regional_expense_contract as contract

VERSION = "company-country-v4-strict-subject-fallback"


def identity_key(value):
    # Formatting and accents are not company identity. Do not use edit-distance
    # or arbitrary substring matches for financial ownership.
    value = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(char for char in value if not unicodedata.combining(char)
        and not char.isspace() and char not in "()（）·_-")


def identity_keys(value, country=None):
    keys = {identity_key(value)} - {""}
    suffix = {"China": r"(?:cn|china|中国)", "Mexico": r"(?:mx|mexico|méxico|墨西哥)"}.get(country)
    if suffix:
        base = re.sub(r"[\s_(（-]+" + suffix + r"[)）]?\s*$", "", str(value or ""), flags=re.I)
        if len(identity_key(base)) >= 4:
            keys.add(identity_key(base))
    return keys


def recognized_companies(corp, kind, label, context):
    candidates = set()
    for company, country in context["companies"].items():
        if identity_keys(label, country) & identity_keys(company, country):
            candidates.add(company)
    for (alias_corp, alias_kind, alias), companies in context["aliases"].items():
        if alias_corp == corp and alias_kind in (None, kind) and identity_key(alias) == identity_key(label):
            candidates.update(companies)
    return candidates


def is_automatic(entries):
    return bool(entries) and all(row.get("assignment_mode") == "automatic" for row in entries)


def configuration(settings):
    """Server-only identity discovery; output permissions remain mandatory."""
    from .regional_expenses import installed
    if not getattr(frappe.local, "site", None) or not installed():
        return None
    cached = frappe.flags.get("regional_automatic_configuration")
    if cached is not None:
        return cached
    companies = {row.name: row.country for row in frappe.get_all("Company",
        filters={"is_group": 0}, fields=["name", "country"], limit_page_length=0)}
    cn_maps, rules, organizations = [], {"category_mappings": []}, {}
    if "budget" in frappe.get_installed_apps():
        from budget.services import cn_approval_settings as cn
        from budget.services.organization_mapping import approval_companies
        cn_maps = cn.company_mappings()
        rules = cn.management_rules()
        organizations = approval_companies()
    mx_maps = [{key: row.get(key) for key in ("entity_kind", "source_entity", "company", "expense_scope")}
        for row in settings.get("company_mappings") or []]
    context = {"companies": companies, "cn_maps": cn_maps, "mx_maps": mx_maps, "rules": rules,
        "organizations": organizations}
    aliases = defaultdict(set)
    for row in cn_maps:
        if row.get("company"):
            aliases[(row.get("corp_id"), None, row.get("source_name"))].add(row["company"])
    # MX aliases are currently global to its configured corporations. Do not
    # apply them to unrelated corporations configured only on the CN side.
    corps = {row.get("corp_id") for row in settings.get("templates") or []}
    organization_version = sorted((corp, department_id, company) for (corp, department_id), company in organizations.items())
    context["version"] = digest([VERSION, companies, cn_maps, mx_maps, rules, sorted(corps), organization_version])
    for row in mx_maps:
        if row.get("company") and row.get("expense_scope") != "non_mx":
            for corp in corps:
                aliases[(corp, row.get("entity_kind"), row.get("source_entity"))].add(row["company"])
    context["aliases"] = dict(aliases)
    frappe.flags.regional_automatic_configuration = context
    return context


def resolve_company(corp, line, context):
    if "allocation" in line.get("base_issues", []):
        return "", "multiple_companies"
    label, kind = line.get("source_entity") or "", line.get("entity_kind")
    candidates = set(context["aliases"].get((corp, None, label), ()))
    candidates.update(context["aliases"].get((corp, kind, label), ()))
    organization = context.get("organizations", {}).get((corp, line.get("_source_entity_id")))
    if organization:
        candidates.add(organization)
    if kind == "entity" and label in context["companies"]:
        candidates.add(label)
    # Explicit configuration and organization IDs remain authoritative. Only
    # unresolved names use deterministic, unique identity recognition.
    if not candidates and label.strip():
        candidates = recognized_companies(corp, kind, label, context)
    if len(candidates) > 1:
        return "", "conflicting_company_mapping"
    company = next(iter(candidates), "")
    if company not in context["companies"]:
        return "", "unmapped_company"
    return company, ""


def plan(source, lines, context, *, available=True):
    entries = []
    for line in lines:
        company, issue = resolve_company(source["corp_id"], line, context)
        region = {"China": "cn", "Mexico": "mx"}.get(context["companies"].get(company), "pending")
        if company and region == "pending":
            issue = "company_country"
        category = ""
        if region == "cn" and "budget" in frappe.get_installed_apps():
            from budget.services.cn_approval_management_rules import classify
            kind = "purchase" if source.get("approval_type") == "purchase" else "operating"
            category = classify(kind, contract.management_path(line), context["rules"])
        entries.append({"line_key": line["line_key"], "region": region,
            "company": company if region != "pending" else "", "matched_company": company,
            "amount": line.get("amount"), "management_category": category,
            "assignment_mode": "automatic", "assignment_issue": issue,
            "assignment_version": context["version"], "source_available": available})
    valid = available and bool(lines) and len({line["line_key"] for line in lines}) == len(lines)
    valid = valid and not any(contract.BLOCKING & set(line.get("base_issues", [])) for line in lines)
    return entries, "confirmed" if valid else "stale"


def mapping_version(settings):
    context = configuration(settings)
    return digest([settings.mapping_version or "", context["version"]]) if context else settings.mapping_version or ""


def legacy_manual_identities():
    """Keep native MX exceptions authoritative when both feeds carry the source."""
    from .regional_expenses import installed
    if not getattr(frappe.local, "site", None) or not installed():
        return set()
    from . import mexico_expenses as mx
    sources = frappe.get_all(mx.LINE, filters={"is_active": 1, "company_basis": "manual"},
        pluck="source", distinct=True, limit_page_length=10001)
    if len(sources) > 10000:
        frappe.throw(frappe._("Expense allocation limit exceeded"))
    if not sources:
        return set()
    return {(row.corp_id, row.process_instance_id) for row in frappe.get_all(mx.SOURCE,
        filters={"name": ["in", sources]}, fields=["corp_id", "process_instance_id"], limit_page_length=10000)}


def invalidate_configuration():
    flags = getattr(frappe.local, "flags", None)
    if flags is not None:
        flags.pop("regional_automatic_configuration", None)


def enqueue_rebuild():
    """Re-read local projections after ownership rules change, never OA."""
    from .regional_expenses import installed
    if not installed():
        return
    invalidate_configuration()
    # The same lease is used by configuration saves and initial page reads.
    # Never let a rolled-back company/mapping change publish a rebuild job.
    frappe.db.after_commit.add(ensure_rebuild)


def ensure_rebuild(settings=None):
    """Coalesce first-load local upgrades; source synchronization handles new rows."""
    from .regional_expenses import installed
    if not getattr(frappe.local, "site", None) or not installed():
        return False
    from . import mexico_expenses as mx
    settings = settings or mx._settings()
    version = mx._projection_version(settings)
    cache = frappe.cache()
    if cache.get_value("regional-ownership-complete") == version:
        return False
    pending = cache.make_key("regional-ownership-pending:" + version)
    if cache.set(pending, "1", nx=True, ex=600):
        try:
            # This is a read of already committed data, with no transaction to
            # publish. GET requests do not run after_commit callbacks.
            frappe.enqueue("deeplinkerp_branding.services.mexico_expenses.reproject_cache",
                projection_version=version, queue="long", timeout=600,
                job_id="regional-ownership-" + version, deduplicate=True)
        except Exception:
            cache.delete(pending)
            raise
    return True


def rebuild_completed(version):
    def publish():
        frappe.cache().set_value("regional-ownership-complete", version)
        frappe.cache().delete_value("regional-ownership-pending:" + version)
    frappe.db.after_commit.add(publish)


def company_changed(doc, method=None):
    if doc.is_new() or doc.has_value_changed("country") or doc.has_value_changed("is_group"):
        enqueue_rebuild()


def refresh_cn_local():
    if "budget" not in frappe.get_installed_apps():
        return
    from budget.integrations.cn_approval_history import read_snapshot
    from budget.integrations.cn_approval_source import configuration as cn_configuration
    from .regional_expenses import refresh_cn
    snapshot = read_snapshot()
    # Missing/incomplete current snapshots must not erase verified ownership.
    if snapshot.get("has_snapshot"):
        _url, templates = cn_configuration()
        refresh_cn(snapshot["rows"], templates)
