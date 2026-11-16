"""Reconcile the finance fallback link and indexes; root icons use native fixtures."""
import frappe

from deeplinkerp_branding.procurement_navigation import reconcile_links, reconcile_workspace


def after_migrate():
    if not frappe.db.exists("Page", "mexico-expenses"):
        return
    entries = (("Mexico Expenses", "Page", "mexico-expenses", "receipt-text"),)
    for doctype, table in (("Workspace Sidebar", "items"), ("Workspace", "links")):
        if not frappe.db.exists(doctype, "Accounting"):
            continue
        doc = frappe.get_doc(doctype, "Accounting")
        if doctype == "Workspace Sidebar":
            changed = reconcile_links(doc, table, entries=entries, targets={"mexico-expenses"}, anchor=len(doc.get(table) or []))
        else:
            changed = reconcile_workspace(doc, entries=entries, targets={"mexico-expenses"}, prefix="dlp-mexico-", anchor=len(doc.get(table) or []))
        if changed:
            previous, developer_mode = frappe.flags.in_import, frappe.conf.developer_mode
            frappe.flags.in_import, frappe.conf.developer_mode = True, False
            try:
                doc.flags.ignore_links = True
                doc.save(ignore_permissions=True)
            finally:
                frappe.flags.in_import, frappe.conf.developer_mode = previous, developer_mode
    if frappe.db.exists("DocType", "Mexico Expense Line"):
        for fields, name in ((["company", "currency", "period_month", "approval_status"], "mx_company_period"),
                             (["company", "currency", "request_date"], "mx_company_request"),
                             (["company", "currency", "planned_payment_date"], "mx_company_planned"),
                             (["source", "is_active"], "mx_source_active"),
                             (["mapping_version", "review_state"], "mx_mapping_review")):
            frappe.db.add_index("Mexico Expense Line", fields, name)
        from deeplinkerp_branding.services import mexico_expenses as service
        from deeplinkerp_branding.services import mexico_expense_templates as templates
        settings = service._settings(lock=True)
        if not settings.get("templates_initialized"):
            existing = {service._template_key(t) for t in (settings.get("templates") or [])}
            for template in templates.default_templates():
                if service._template_key(template) in existing:
                    continue
                if template["approval_type"] == "expense":
                    template.update({key: settings.get(key) for key in templates.STATE_FIELDS})
                settings.append("templates", template)
            settings.templates_initialized = 1
            settings.config_version = service.digest([settings.config_version, "multi-template-v1"])
            service._save(settings)
        # Rebuild cached approval facts after schema/policy changes; no external IO.
        if frappe.db.exists("Mexico Expense Source", {}):
            from deeplinkerp_branding.services.mexico_expenses import _projection_version, _settings
            frappe.enqueue("deeplinkerp_branding.services.mexico_expenses.reproject_cache",
                           projection_version=_projection_version(_settings()),
                           enqueue_after_commit=True, queue="long", timeout=180)
