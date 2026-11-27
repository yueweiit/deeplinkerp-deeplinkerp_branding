"""Automatic ownership and audited exceptions over local OA snapshots only.

The initiating feed owns refreshes. Its record overrides that approval in both
workbenches, including when both template lists contain the same OA approval.
All writes lock MX settings first; its existing cache and this registry publish
in the same transaction. No HTTP, GL, payment or OA writes occur here.
"""
import json
from contextlib import contextmanager

import frappe
from frappe import _
from frappe.utils import now_datetime

from . import regional_expense_contract as contract
from .operating_expense_contract import digest

DOCTYPE = "Regional Expense Allocation"


def installed():
    return bool(getattr(frappe.local, "site", None)) and frappe.db.table_exists(DOCTYPE)


def decode(value):
    return json.loads(value) if isinstance(value, str) else value


def encode(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def template_values(template):
    # Frappe child Documents are not dictionaries and must never be serialized
    # using their display string as the saved parsing configuration.
    from .mexico_expense_templates import CONFIG_FIELDS
    return {key: template.get(key) for key in CONFIG_FIELDS}


def connection_key():
    from .operating_oa_http import configuration
    url, _corps = configuration()
    return digest([url])


def require_connection(record):
    from .operating_oa_http import configuration
    _url, corps = configuration()
    if record.connection_fingerprint != connection_key() or record.corp_id not in corps:
        frappe.throw(_("Expense allocation belongs to another OA connection; restore or reconcile the source first"))


@contextmanager
def managed_write():
    old = frappe.flags.get("regional_expense_write")
    frappe.flags.regional_expense_write = True
    try:
        yield
    finally:
        frappe.flags.regional_expense_write = old


def save_record(doc):
    with managed_write():
        doc.insert(ignore_permissions=True) if doc.is_new() else doc.save(ignore_permissions=True)


def require_manager():
    frappe.only_for("System Manager")
    # Allocation editors show the complete source, not just one subsidiary.
    all_companies = frappe.get_all("Company", fields=["name", "country", "is_group"], limit_page_length=0)
    allowed = set(frappe.get_list("Company", pluck="name", limit_page_length=0))
    if any(company.name not in allowed for company in all_companies):
        frappe.throw(_("Cross-region allocations require access to every subsidiary"), frappe.PermissionError)
    if not installed():
        frappe.throw(_("Expense allocation storage is not installed. Run the application migration."))
    return {company.name: company.country for company in all_companies if not company.is_group}


def records():
    if not installed():
        return []
    rows = frappe.get_all(DOCTYPE, fields=["name", "corp_id", "process_code", "process_instance_id", "origin",
        "state", "facts_json", "allocations_json", "connection_fingerprint", "modified", "approval_no"],
        limit_page_length=10001, order_by="name")
    if len(rows) > 10000:
        frappe.throw(_("Expense allocation limit exceeded"))
    if rows:
        stamp = connection_key()
        from .operating_oa_http import configuration
        _url, corps = configuration()
        if any(row.connection_fingerprint != stamp or row.corp_id not in corps for row in rows):
            frappe.throw(_("Expense allocation belongs to another OA connection; restore or reconcile the source first"))
    return rows


def revision():
    if not installed():
        return ""
    values = frappe.get_all(DOCTYPE,
        fields=[{"MAX": "modified", "as": "latest"}, {"COUNT": "name", "as": "total"}], order_by=None)
    return digest([str(values[0].latest or ""), values[0].total]) if values else ""


def allowed_companies(user=None):
    return set(frappe.get_list("Company", pluck="name", limit_page_length=0, user=user or frappe.session.user))


def company_condition(user=None, alias=""):
    if not installed():
        return "1=1"
    column = f"{alias}.regional_company" if alias else "regional_company"
    companies = allowed_companies(user)
    values = ",".join(frappe.db.escape(company) for company in sorted(companies))
    return f"(COALESCE({column}, '') = ''" + (f" OR {column} IN ({values})" if values else "") + ")"


def membership_fields():
    return ["regional_company"] if installed() else []


def record_for(row, *, lock=False):
    if not installed():
        return None
    key = digest([row.get("corp_id"), row.get("process_instance_id")])
    if not frappe.db.exists(DOCTYPE, key):
        return None
    record = frappe.get_doc(DOCTYPE, key, for_update=lock)
    require_connection(record)
    if record.process_code != row.get("process_code"):
        frappe.throw(_("Approval instance identity conflicts with another template"))
    return record


def categories():
    if "budget" not in frappe.get_installed_apps():
        return {}
    from budget.services.cn_approval_management_rules import CATEGORIES
    return CATEGORIES


def application_date(raw):
    return application_date_info(raw)[0]


def application_date_info(raw, template=None):
    """Read an explicit form date without borrowing finance or payment dates."""
    from .mexico_expense_projection import text
    from datetime import date
    components = decode(raw.get("form_component_values") or (raw.get("raw_payload") or {}).get("formComponentValues") or [])
    if template:
        from . import expense_template_mapping as bindings, mexico_expense_templates as templates
        mapping = bindings.decode(template)
        if "application_date" in mapping:
            resolved, errors = templates.resolve(components, template, schema={"application_date": bindings.APPLICATION_DATE})
            value = text(resolved.get("application_date", {}).get("value"))
            try:
                return (date.fromisoformat(value).isoformat(), "form") if not errors else (None, "invalid")
            except (TypeError, ValueError):
                return None, "invalid"
    fields = [field for field in components if (
        field.get("id") in {"DDDateField_29I59O630HUS", "DDDateField_B0K470VSL0O0"}
        or str(field.get("name", "")).startswith(("申请日期", "Fecha de solicitud")))]
    if not fields:
        return None, "missing"
    dates = {text(field.get("value")) for field in fields if field.get("value")}
    try:
        applied = date.fromisoformat(next(iter(dates))).isoformat() if len(dates) == 1 else None
        return applied, "form" if applied else "invalid"
    except ValueError:
        return None, "invalid"


def management_date(applied, date_state, submitted, *, allow_submission=False):
    """Shared reporting/preview policy; absent fields alone permit fallback."""
    if not applied and date_state == "missing" and allow_submission:
        return str(submitted) if submitted else None, "submission"
    return applied, "application"


def facts(raw, template, timezone, ignore_region=False):
    from .mexico_expense_projection import project, text
    source, lines = project(raw, template, timezone, ignore_region=ignore_region)
    source["cn_application_date"], source["application_date_state"] = application_date_info(raw, template)
    components = decode(raw.get("form_component_values") or (raw.get("raw_payload") or {}).get("formComponentValues") or [])
    # Match the native CN project precedence: component ID first, then the
    # historical label. Never choose one project from conflicting values.
    project_fields = [field for field in components if field.get("id") == "TextField_97MDU018WPC0"]
    if not project_fields:
        project_fields = [field for field in components if text(field.get("name")).startswith(
            ("项目归属", "Pertenencia del proyecto"))]
    populated = [field for field in project_fields if field.get("value") not in (None, "", [], "[]")]
    source["cn_project"] = text(populated[0]["value"]) if len(populated) == 1 else ""
    for line in lines:
        line.pop("_legacy_row_fingerprint", None)
    return source, lines


def fact_payload(source, lines):
    # Queries never load raw workflow/attachments; these stay in source_json.
    return encode([{key: value for key, value in source.items() if key not in ("raw_json", "details_json")}, lines])


def current_candidate(origin, key, *, identity=None):
    from . import mexico_expenses as mx
    if origin == "cn":
        from budget.integrations.cn_approval_history import read_snapshot
        snapshot = read_snapshot()
        row = next((row for row in snapshot["rows"] if (
            (row["corp_id"], row["process_code"], row["process_instance_id"]) == identity
            if identity is not None else row["key"] == key)), None)
        if not row or row["kind"] == "budget":
            frappe.throw(_("Expense approval is unavailable"))
        payload = row.get("_routing_source")
        if not payload:
            frappe.throw(_("Refresh approvals before allocating this source"))
        template = dict(payload["template"])
        # Reuse explicitly saved MX finance bindings for a shared template.
        # Do not seed a template or guess a finance date when none is configured.
        match = next((item for item in (mx._settings().get("templates") or [])
            if item.get("corp_id") == row["corp_id"] and item.get("process_code") == row["process_code"]), None)
        if match:
            template.update({key: match.get(key) or "" for key in ("finance_activity_ids", "finance_node_names")})
        return payload["row"], template
    if origin == "mx":
        doc = frappe.get_doc(mx.SOURCE, key)
        raw = decode(doc.raw_json)
        if identity is not None and tuple(raw.get(field) for field in
                ("corp_id", "process_code", "process_instance_id")) != identity:
            frappe.throw(_("Expense approval is unavailable"))
        return raw, template_values(mx._template(raw))
    frappe.throw(_("Invalid expense allocation"))


@frappe.whitelist()
def list_allocations():
    require_manager()
    return [{"name": record.name, "number": record.approval_no, "origin": record.origin,
        "state": "pending" if record.state == "confirmed" and any(entry["region"] == "pending" for entry in decode(record.allocations_json)) else record.state,
        "modified": str(record.modified)} for record in records()]


@frappe.whitelist()
def get_editor(origin=None, key=None, allocation=None):
    companies = require_manager()
    from . import mexico_expenses as mx
    record = frappe.get_doc(DOCTYPE, allocation) if allocation else None
    if record:
        require_connection(record)
        raw, template = decode(record.source_json), decode(record.template_json)
        origin = record.origin
    else:
        raw, template = current_candidate(origin, key)
        record = record_for(raw)
    if record:
        source, lines = decode(record.facts_json)
        entries = decode(record.allocations_json)
        origin = record.origin
        template = decode(record.template_json)
    else:
        source, lines = facts(raw, template, mx._settings().business_timezone or "America/Mexico_City", origin == "cn")
        from . import regional_expense_automatic as automatic
        entries, _state = automatic.plan(source, lines, automatic.configuration(mx._settings()))
    from .regional_expense_automatic import is_automatic
    return {"name": record.name if record else None, "origin": origin, "key": key,
        "revision": str(record.modified) if record else "", "fingerprint": contract.fingerprint(source, lines),
        "number": source.get("approval_no"), "state": record.state if record else "new",
        "assignment_mode": "automatic" if is_automatic(entries) else "manual",
        "companies": companies, "categories": categories(), "allocations": entries,
        "finance_activity_ids": template.get("finance_activity_ids") or "", "finance_node_names": template.get("finance_node_names") or "",
        "note": record.review_note if record else "", "lines": [{field: line.get(field) for field in
            ("line_key", "source_entity", "description", "amount", "currency")} for line in lines]}


@frappe.whitelist(methods=["POST"])
def save_allocation(origin, key, allocations, note, expected_revision, expected_fingerprint, allocation=None,
        finance_activity_ids="", finance_node_names=""):
    companies = require_manager()
    from . import mexico_expenses as mx
    settings = mx._settings(lock=True)
    if not isinstance(note, str) or not note.strip() or len(note) > 1000:
        frappe.throw(_("A review note is required"))
    existing = frappe.get_doc(DOCTYPE, allocation, for_update=True) if allocation else None
    if existing:
        require_connection(existing)
    else:
        raw, template = current_candidate(origin, key)
        existing = record_for(raw, lock=True)
    if (str(existing.modified) if existing else "") != expected_revision:
        frappe.throw(_("Expense allocation changed; reload and review again"))
    if existing:
        # A missing source is deliberately retained for audit, not as evidence
        # that its old approved amount can be confirmed again. Resolve the
        # owner's latest verified local snapshot even when opened on the other
        # workbench, including requests that identify only the source key.
        origin = existing.origin
        raw, template = current_candidate(origin, existing.name,
            identity=(existing.corp_id, existing.process_code, existing.process_instance_id))
    for field, value in (("finance_activity_ids", finance_activity_ids), ("finance_node_names", finance_node_names)):
        if not isinstance(value, str) or len(value) > 2000:
            frappe.throw(_("Invalid finance node configuration"))
        # MX-owned templates keep their existing central finance settings.
        if origin == "cn":
            template[field] = value.strip()
    source, lines = facts(raw, template, settings.business_timezone or "America/Mexico_City", origin == "cn")
    stamp = contract.fingerprint(source, lines)
    if stamp != expected_fingerprint:
        frappe.throw(_("Expense source changed; reload and review again"))
    try:
        clean = contract.validate_allocations(decode(allocations), lines, companies, categories())
    except (ValueError, TypeError) as error:
        frappe.throw(_(str(error)))
    record = existing or frappe.new_doc(DOCTYPE)
    record.update({"source_key": source["source_key"], "corp_id": raw["corp_id"], "process_code": raw["process_code"],
        "process_instance_id": raw["process_instance_id"], "approval_no": source.get("approval_no"), "origin": origin,
        "source_json": encode(raw), "template_json": encode(template), "facts_json": fact_payload(source, lines),
        "allocations_json": encode(clean), "reviewed_fingerprint": stamp, "state": "confirmed", "review_note": note.strip(),
        "connection_fingerprint": connection_key(),
        "reviewed_by": frappe.session.user, "reviewed_at": now_datetime()})
    save_record(record)
    mx._upsert(raw, settings, regional_origin=origin)
    return {"ok": True, "name": record.name}


def template_for(row, configured=()):
    record = record_for(row)
    if record and record.origin == "mx":
        current = next((item for item in configured if item.get("corp_id") == row.get("corp_id")
            and item.get("process_code") == row.get("process_code")), None)
        if current:
            return current
    return decode(record.template_json) if record else None


def project_for_mx(row, template, settings, origin="mx", source_available=True):
    from . import regional_expense_automatic as automatic
    record = record_for(row, lock=True)
    if not record and not installed():
        return None
    template = template_values(template)
    # The opposite feed may be older or use a different parsing configuration.
    # It must not overwrite the allocation's authoritative source.
    entries = decode(record.allocations_json) if record else []
    auto = not record or automatic.is_automatic(entries)
    if record and record.origin != origin:
        row, template = decode(record.source_json), decode(record.template_json)
        source_available = all(entry.get("source_available", True) for entry in entries)
    elif record and record.origin == "cn":
        previous_template = decode(record.template_json)
        template = {**template, **{key: previous_template.get(key, "") for key in ("finance_activity_ids", "finance_node_names")}}
    owner = record.origin if record else origin
    if not record:
        # Existing per-line reviews are deliberate exceptions. Do not silently
        # discard them when first enabling automatic ownership.
        from . import mexico_expenses as mx
        source_key = digest([row.get("corp_id"), row.get("process_instance_id")])
        if frappe.get_all(mx.LINE, filters={"source": source_key, "is_active": 1,
                "company_basis": "manual"}, pluck="name", limit_page_length=1):
            return None
    if auto and owner == "cn":
        match = next((item for item in settings.get("templates") or [] if
            item.get("corp_id") == row.get("corp_id") and item.get("process_code") == row.get("process_code")), None)
        template.update({key: (match.get(key) if match else "") or ""
            for key in ("finance_activity_ids", "finance_node_names")})
    source, lines = facts(row, template, settings.business_timezone or "America/Mexico_City", owner == "cn")
    if auto and not lines:
        # Do not make existing allocations disappear on a malformed refresh.
        if not record:
            return None
        source_available = False
    stamp = contract.fingerprint(source, lines)
    if auto:
        context = automatic.configuration(settings)
        if lines:
            entries, state = automatic.plan(source, lines, context, available=source_available)
        else:
            state = "stale"
            entries = [{**entry, "source_available": False} for entry in entries]
        if not record:
            record = frappe.new_doc(DOCTYPE)
            record.update({"source_key": source["source_key"], "corp_id": row["corp_id"],
                "process_code": row["process_code"], "process_instance_id": row["process_instance_id"],
                "approval_no": source.get("approval_no"), "origin": owner, "connection_fingerprint": connection_key()})
    else:
        state = "confirmed" if record.state == "confirmed" and stamp == record.reviewed_fingerprint else "stale"
    values = {"source_json": encode(row), "template_json": encode(template), "facts_json": fact_payload(source, lines), "state": state}
    if auto:
        values.update(allocations_json=encode(entries), reviewed_fingerprint=stamp,
            review_note="", reviewed_by=None, reviewed_at=None)
    if any(record.get(key) != value for key, value in values.items()):
        record.update(values)
        save_record(record)
    routed = contract.allocated_lines(source, lines, decode(record.allocations_json), valid=state == "confirmed",
        note=record.review_note, reviewer=record.reviewed_by, reviewed_at=record.reviewed_at, origin=record.origin)
    if auto:
        for line in routed:
            line.update(company_basis="mapping", mapping_version=automatic.mapping_version(settings))
    # This is an internal source annotation, not a Mexico Expense Source field.
    source.pop("cn_application_date", None)
    source.pop("cn_project", None)
    return source, routed


def refresh_cn(rows, templates, *, incremental=False):
    if not installed():
        return
    from . import mexico_expenses as mx
    settings = mx._settings(lock=True)
    active = {(template["corp_id"], template["process_code"]) for template in templates}
    incoming = {(row["corp_id"], row["process_instance_id"]): row for row in rows}
    existing = records()
    processed = set()
    for record in existing:
        if record.origin != "cn" or (record.corp_id, record.process_code) not in active:
            continue
        row = incoming.get((record.corp_id, record.process_instance_id))
        if incremental and row is None:
            # Absence from a change feed does not mean an approval disappeared.
            continue
        if row and row.get("_routing_source"):
            mx._upsert(row["_routing_source"]["row"], settings, regional_origin="cn", regional_template=row["_routing_source"]["template"])
            processed.add((record.corp_id, record.process_instance_id))
        else:
            doc = frappe.get_doc(DOCTYPE, record.name, for_update=True)
            doc.state = "stale"
            save_record(doc)
            mx._upsert(decode(doc.source_json), settings, regional_origin="cn", regional_available=False)
    identities = {(record.corp_id, record.process_instance_id) for record in existing}
    for identity, row in incoming.items():
        if identity not in processed and identity not in identities and row.get("_routing_source") and row.get("kind") != "budget":
            mx._upsert(row["_routing_source"]["row"], settings, regional_origin="cn", regional_template=row["_routing_source"]["template"])


def cn_projection(context):
    return management_projection(context, regions=("cn",))


def management_projection(context, *, regions=("cn", "mx"), include_legacy=True, submission_fallback=False,
        management_rules=None, group_unassigned=False):
    """Read allocations once; permissions apply before returning any line."""
    from budget.services.cn_approval_management_rules import CATEGORIES, classify
    from . import regional_expense_automatic as automatic
    output, identities = [], automatic.legacy_manual_identities() if include_legacy else set()
    current_version = None
    saved = records()
    parsed = {record.name: decode(record.facts_json) for record in saved}
    fallback_records = {record.name for record in saved if submission_fallback and record.origin == "mx"}
    if submission_fallback and any(record.origin == "cn" and not parsed[record.name][0].get("cn_application_date")
            for record in saved):
        from .mexico_expenses import _settings
        # A shared template has the same date policy whichever feed published
        # first. Include disabled templates: their retained history still uses
        # the configured policy. Keep CN-only templates and legacy CN APIs strict.
        expense_templates = {(item.get("corp_id"), item.get("process_code"))
            for item in (_settings().get("templates") or [])}
        fallback_records.update(record.name for record in saved
            if (record.corp_id, record.process_code) in expense_templates)
    # Older facts do not carry field-presence metadata. Inspect only those
    # local source payloads, in one bounded read, without changing the snapshot.
    candidates = [record.name for record in saved if record.name in fallback_records
        and not parsed[record.name][0].get("cn_application_date")
        and not parsed[record.name][0].get("application_date_state")]
    date_states = {row.name: application_date_info(decode(row.source_json))[1]
        for row in frappe.get_all(DOCTYPE, filters={"name": ["in", candidates]},
            fields=["name", "source_json"], limit_page_length=10000)} if candidates else {}
    for record in saved:
        identities.add((record.corp_id, record.process_instance_id))
        source, lines = parsed[record.name]
        originals = {line["line_key"]: line for line in lines}
        entries = decode(record.allocations_json)
        auto = automatic.is_automatic(entries)
        if auto and current_version is None:
            from .mexico_expenses import _settings
            current_version = automatic.configuration(_settings())["version"]
        for index, entry in enumerate(entries):
            pending = entry["region"] == "pending" and record.origin in regions and context.get("manager")
            if pending and entry.get("matched_company") and entry["matched_company"] not in context["companies"]:
                continue
            if not pending and (entry["region"] not in regions or entry["company"] not in context["companies"]):
                continue
            original = originals.get(entry["line_key"], {})
            group = bool(group_unassigned and pending and auto and original
                and not entry.get("matched_company") and entry.get("assignment_issue") == "unmapped_company"
                and not str(original.get("source_entity") or "").strip())
            valid = record.state == "confirmed" and bool(original) and (not pending or group)
            if auto:
                valid = valid and entry.get("assignment_version") == current_version and entry.get("source_available", True)
            applied, date_basis = management_date(source.get("cn_application_date"),
                source.get("application_date_state") or date_states.get(record.name), source.get("request_date"),
                allow_submission=record.name in fallback_records)
            issues = [] if valid else ["regional_allocation"]
            if pending and not group:
                issues.append("organization")
            if not applied:
                issues.append("period")
            path = contract.management_path(original)
            kind = "purchase" if source.get("approval_type") == "purchase" else "operating"
            category = entry["management_category"]
            # MX and automatic Group allocations store no management category.
            # The unified report maps source paths at read time; saved CN
            # category decisions and legacy report behavior remain unchanged.
            if (entry["region"] == "mx" or group) and management_rules is not None:
                category = classify(kind, path, management_rules)
            if category not in CATEGORIES:
                issues.append("management_category")
            key_region = "cn" if regions == ("cn",) else entry["region"]
            output.append({"key": digest([record.name, "regional-" + key_region, index]), "source_key": record.name,
                "corp_id": record.corp_id, "process_code": record.process_code, "process_instance_id": record.process_instance_id,
                "kind": kind,
                "number": source.get("approval_no") or record.process_instance_id, "applicant": source.get("applicant") or "",
                "company": entry["company"], "entity": original.get("source_entity", ""), "department": "",
                "is_group_expense": int(group),
                "scope_company": entry.get("matched_company", "") if pending else "",
                "application_date": applied, "month": applied[:7] if applied else None, "status": source["approval_status"],
                "date_basis": date_basis,
                "amount": entry["amount"] if valid else None, "currency": original.get("currency") or source.get("currency"),
                "category": original.get("business_category", ""), "classification": path, "issues": issues,
                "management_category": category, "source_path": path,
                "description": original.get("description", ""), "line_label": original.get("description", ""),
                "project": source.get("cn_project") or "", "budget_state": "", "approved_at": None, "budget_categories_complete": False,
                "allocation_amounts": [part["amount"] for part in entries] if valid else [],
                "allocation_index": index, "header_amount": source.get("total_amount"),
                "table_index": -1, "detail_index": -1,
                "allocation_origin": record.origin, "regional_allocation": record.name,
                "source_family": source.get("approval_type"),
                "original_issues": original.get("base_issues") or [],
                "allocation_modified": str(record.modified)})
            if len(output) > 50000:
                frappe.throw(_("CN management analysis exceeds 50000 detail lines. Narrow the configured scope."))
    return identities, output


def cn_detail(line, context):
    from .mexico_expenses import _dingtalk_url
    record = frappe.get_doc(DOCTYPE, line["regional_allocation"])
    entries = decode(record.allocations_json)
    # Even a CN company reader must not obtain the other side's raw approval.
    full = context["manager"] and all((entry["company"] or entry.get("matched_company")) in context["companies"]
        for entry in entries if entry["company"] or entry.get("matched_company"))
    return {"facts": [{"label": "Approval source", "value": record.origin.upper()}], "tables": [],
        "attachments": [], "budget_snapshot": {}, "detail_total": None, "detail_currency": None,
        "approval_url": _dingtalk_url(record) if full else ""}
