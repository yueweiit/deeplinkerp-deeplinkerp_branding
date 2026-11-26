"""Permission-aware view of the manual cash journal, independent of the GL."""
from decimal import Decimal

import frappe
from frappe import _
from frappe.model import get_permitted_fields
from frappe.permissions import get_role_permissions
from pypika import Case, analytics
from pypika.functions import Cast, Count, Sum

from .mexico_expense_contract import month_bounds

DOCTYPE = "CN Cash Journal Entry"
ORDER_FIELDS = ("posting_date", "source_order", "creation", "name")
ORDER = ", ".join(f"{field} asc" for field in ORDER_FIELDS)


@frappe.whitelist(methods=["GET", "POST"])
def list_page(company: str, currency: str, account: str = "", start: int = 0,
              from_month: str | None = None, to_month: str | None = None):
    first, last = _month_range(from_month, to_month)
    filters = {"company": company, "currency": currency}
    if account:
        filters["account"] = account
    if first:
        filters["posting_date"] = ["between", [first, last]]
    readable = set(get_permitted_fields(DOCTYPE, ignore_virtual=True))
    readable.difference_update(df.fieldname for df in frappe.get_meta(DOCTYPE).get_masked_fields())
    if not (filters.keys() | set(ORDER_FIELDS)) <= readable:
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    rows = frappe.get_list(DOCTYPE, filters=filters, fields=["*"], order_by=ORDER,
                           offset=max(0, start), limit=21)
    visible = rows[:20]
    for row in visible:
        if row.get("amount") is not None:
            row.amount = format(row.amount, ".2f") if "amount" in readable else None
    page = {"rows": visible, "has_next": len(rows) > 20, "balances": {}}
    if not {"amount", "direction"} <= readable:
        # Outer aggregates do not inherit the DocType's output masks.
        entries = _permitted_entries(filters, ["name"])
        count = frappe.qb.from_(entries).select(Count(entries.name)).run()[0][0]
        page["summary"] = {"opening": None, "income": None, "expense": None, "closing": None, "count": count}
        return page

    # Frappe sets read=1 for owner-only lists too; the nested key marks the restriction.
    permissions = get_role_permissions(DOCTYPE)
    can_balance = "account" in readable and permissions.get("read") and "read" not in permissions.get("if_owner", {})
    accounts = {row.get("account") for row in visible if row.get("account")}
    if can_balance and accounts:
        scope = {**filters, "account": ["in", list(accounts)], "posting_date": ["<=", visible[-1].posting_date]}
        page["balances"] = page_balances(scope, [row.name for row in visible])
    page["summary"] = _summary(filters, first, last, can_balance)
    return page


def _month_range(from_month, to_month):
    if from_month in (None, "") and to_month in (None, ""):
        return None, None
    try:
        first = month_bounds(from_month)[0]
        last = month_bounds(to_month)[1]
    except ValueError:
        frappe.throw(_("Enter both months in YYYY-MM format."))
    if first > last:
        frappe.throw(_("The start month must not be after the end month."))
    return first, last


def _summary(filters, first, last, can_balance):
    income, expense, count = _visible_totals(filters)
    summary = {"opening": None, "income": format(income, ".2f"), "expense": format(expense, ".2f"),
               "closing": None, "count": count}
    if not can_balance or not filters.get("account"):
        return summary

    opening, prior_count = Decimal(0), 0
    history = filters
    if first:
        prior_income, prior_expense, prior_count = _visible_totals({**filters, "posting_date": ["<", first]})
        opening = prior_income - prior_expense
        history = {**filters, "posting_date": ["<=", last]}
    # Only compare the unrestricted count; never read unrestricted amounts.
    # No visible history must look the same whether hidden entries exist or not.
    visible_count = prior_count + count
    if visible_count and visible_count == frappe.db.count(DOCTYPE, filters=history):
        summary.update(opening=format(opening, ".2f"), closing=format(opening + income - expense, ".2f"))
    return summary


def _visible_totals(filters):
    entries = _permitted_entries(filters, ["name", "direction", "amount"])
    # Transfer SQL decimal sums as text before Frappe's float decoder sees them.
    amounts = [Cast(Sum(Case().when(entries.direction == direction, entries.amount).else_(0)), "CHAR(65)")
               for direction in ("income", "expense")]
    income, expense, count = frappe.qb.from_(entries).select(*amounts, Count(entries.name)).run()[0]
    return Decimal(income or 0), Decimal(expense or 0), count


def _permitted_entries(filters, fields):
    return frappe.qb.get_query(DOCTYPE, fields=fields, filters=filters, ignore_permissions=False).as_("permitted")


def page_balances(scope, names):
    fields = ["name","account","amount","direction","posting_date","source_order","creation"]
    permitted = _permitted_entries(scope, fields)
    delta = Case().when(permitted.direction == "income", permitted.amount).else_(-permitted.amount)
    cumulative = analytics.Sum(delta).over(permitted.account).orderby(
        permitted.posting_date, permitted.source_order, permitted.creation, permitted.name)
    visible_count = analytics.Count(permitted.name).over(permitted.account)
    totals = frappe.qb.from_(permitted).select(permitted.name, permitted.account,
             Cast(cumulative, "CHAR(65)").as_("balance"), visible_count.as_("visible_count")).as_("totals")
    page = frappe.qb.from_(totals).select(totals.star).where(totals.name.isin(names)).run(as_dict=True)
    # Never present a subtotal of shared/owner-only rows as a bank balance.
    # Only compare counts internally; no hidden records or amounts are returned.
    counts = dict(frappe.get_all(DOCTYPE, fields=["account", {"COUNT": "name"}], filters=scope,
                                 group_by="account", order_by="account", as_list=True))
    return {r.name: format(Decimal(r.balance), ".2f") for r in page if r.visible_count == counts[r.account]}
