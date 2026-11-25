"""Add a server-owned checkpoint. Existing templates establish a full baseline."""
import frappe


def execute():
    frappe.reload_doc("deeplinkerp_branding", "doctype", "mexico_expense_template")
