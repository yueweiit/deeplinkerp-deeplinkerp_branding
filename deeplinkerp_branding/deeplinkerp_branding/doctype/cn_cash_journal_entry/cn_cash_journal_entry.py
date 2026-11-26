"""Manual CN cash book. It never creates GL entries or approval/payment facts."""
from decimal import Decimal

import frappe
from frappe import _
from frappe.model.document import Document

from deeplinkerp_branding.services.operating_expense_contract import money


class CNCashJournalEntry(Document):
    def before_validate(self):
        from deeplinkerp_branding.services.cn_cash_book_import import IMPORT_TOKEN
        if self.is_new() and self.flags.cash_book_import is IMPORT_TOKEN:
            return
        if self.is_new():
            if self.import_key or self.source_book or self.source_order:
                frappe.throw(_("Journal source fields cannot be edited."))
            self.source_order = 2147483647  # Manual entries follow imported rows on the same date.
        elif any(self.has_value_changed(k) for k in ("import_key", "source_book", "source_order")):
            frappe.throw(_("Journal source fields cannot be edited."))

    def validate(self):
        if not isinstance(self.direction, str) or self.direction not in {"income", "expense"}:
            frappe.throw(_("Choose whether this entry is a receipt or a payment."))
        if any(value is not None and not isinstance(value, str)
                for value in (self.summary, self.dingtalk_approval_number)):
            frappe.throw(_("Description and DingTalk approval number must be text."))
        self.summary = (self.summary or "").strip()
        self.dingtalk_approval_number = (self.dingtalk_approval_number or "").strip()
        try:
            amount = money(str(self.amount), precision=2, positive=True)
            # Native Currency fields serialize through float before persistence.
            # Reject values whose cents would change along that path.
            if Decimal(str(float(amount))) != amount:
                raise ValueError("Currency precision loss")
        except ValueError:
            frappe.throw(_("Amount must be greater than zero and have no more than two decimal places."))
        self.amount = format(amount, ".2f")

        company = frappe.get_doc("Company", self.company)
        company.check_permission("read")
        if company.is_group:
            frappe.throw(_("Choose a non-group company for the cash journal."))
        self.currency = self.currency or company.default_currency
        if not frappe.db.exists("Currency", self.currency):
            frappe.throw(_("Choose a valid currency for the cash journal."))
        if getattr(self, "account", None) and (self.is_new() or any(
            self.has_value_changed(key) for key in ("account", "company", "currency")
        )):
            from deeplinkerp_branding.services.operating_expenses import _account
            account = _account(self.account, self.company, {"Cash", "Bank"})
            if account.account_currency != self.currency:
                frappe.throw(_("Journal currency must match the account currency."))
