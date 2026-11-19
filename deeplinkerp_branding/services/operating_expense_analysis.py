"""Read-only CN analysis of the existing, permission-filtered operating worklist.

Amounts retain their source currency and payment-evidence semantics. Months are
application months, not accounting periods or payment dates.
"""
from collections import Counter, defaultdict

from .operating_expense_contract import company_overview, currency_totals, worklist_summary


def summarize(rows):
    _, counts, totals = worklist_summary(rows, "all")
    companies, review = company_overview(rows)
    months, types = defaultdict(list), defaultdict(list)
    for row in rows:
        months[str(row.get("request_date") or "")[:7]].append(row)
        kind = row.get("effective_application_type")
        types[kind if kind in {"payment", "reimbursement"} else "unclassified"].append(row)

    def groups(grouped):
        return [{"key": key, "count": len(items),
                 "currency_counts": dict(Counter(row.get("currency") or "未知" for row in items)),
                 "currencies": currency_totals(items)}
                for key, items in sorted(grouped.items())]

    return {"total_count": len(rows), "tab_counts": counts, "currency_totals": totals,
            "company_overview": companies, "review_counts": review,
            "months": groups(months), "application_types": groups(types)}
