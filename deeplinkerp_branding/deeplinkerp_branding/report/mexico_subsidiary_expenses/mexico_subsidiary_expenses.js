{% include "deeplinkerp_branding/public/js/mexico_expense_money.js" %}
{% include "deeplinkerp_branding/public/js/mexico_expense_reports.js" %}

frappe.query_reports["Mexico Subsidiary Expenses"] = frappe.deeplinkerp.mexico_expense_report("company");
