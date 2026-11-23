import frappe
from frappe import _
from frappe.model.document import Document


class RegionalExpenseAllocation(Document):
    def validate(self):
        if not frappe.flags.get("regional_expense_write"):
            frappe.throw(_("Expense allocations can only be changed through the workbench"), frappe.PermissionError)

    def on_trash(self):
        frappe.throw(_("Expense allocations cannot be deleted; update the allocation instead"), frappe.PermissionError)

    def before_rename(self, *args, **kwargs):
        self.on_trash()
