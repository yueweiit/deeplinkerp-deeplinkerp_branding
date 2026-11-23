"""One allocation plan per OA approval, shared by both regional projections.

No payment or accounting writes. Amounts remain in the source currency. An
allocation is a partition of source lines, never an additional expense.
"""
from copy import deepcopy
from decimal import Decimal

from .operating_expense_contract import digest
from .mexico_expense_contract import exact_amount

REGIONS = ("cn", "mx", "other", "pending")
BLOCKING = {"amount", "total", "currency", "structure", "identity"}


def fingerprint(source, lines):
    # Workflow progress and finance dates change statistics, not cost ownership.
    return digest([source["corp_id"], source["process_code"], source["process_instance_id"],
        source.get("total_amount"), source.get("currency"), sorted(({
            key: line.get(key) for key in ("line_key", "source_entity", "amount", "currency",
                "description", "classification_path", "table_id", "source_row_id")
        } for line in lines), key=lambda line: line["line_key"]),
        sorted({issue for line in lines for issue in line["base_issues"] if issue in BLOCKING})])


def validate_allocations(entries, lines, companies, categories):
    if not isinstance(entries, list) or not 1 <= len(entries) <= 2000:
        raise ValueError("Invalid expense allocation")
    originals = {line["line_key"]: line for line in lines}
    if len(originals) != len(lines) or any(BLOCKING & set(line["base_issues"]) for line in lines):
        raise ValueError("Correct source amounts and line identities before allocating expenses")
    result, totals = [], {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid expense allocation")
        key, region, company = (entry.get(field) for field in ("line_key", "region", "company"))
        amount = exact_amount(entry.get("amount"))
        category = entry.get("management_category") or ""
        if key not in originals or region not in REGIONS or amount is None:
            raise ValueError("Invalid expense allocation")
        if region in ("cn", "mx"):
            if company not in companies or (region == "mx" and companies[company] != "Mexico"):
                raise ValueError("Select an authorized subsidiary for the allocation")
            if region == "cn" and category not in categories:
                raise ValueError("Select a management category for CN allocations")
        elif company or category:
            raise ValueError("Other and pending allocations must not specify a subsidiary or category")
        if region != "cn" and category:
            raise ValueError("Management categories apply only to CN allocations")
        totals[key] = totals.get(key, Decimal(0)) + Decimal(amount)
        result.append({"line_key": key, "region": region, "company": company or "", "amount": amount,
            "management_category": category})
    if set(totals) != set(originals) or any(line.get("amount") is None or totals[key] != Decimal(line["amount"])
            for key, line in originals.items()):
        raise ValueError("Allocated amounts must equal each original line amount")
    return result


def allocated_lines(source, lines, allocations, *, valid, note="", reviewer=None, reviewed_at=None, origin="mx"):
    originals = {line["line_key"]: line for line in lines}
    result = []
    for index, entry in enumerate(allocations):
        original = originals.get(entry["line_key"])
        # Removed/rekeyed rows retain their plan for review, but never an amount.
        line = deepcopy(original or (lines[0] if lines else {}))
        if not line:
            continue
        line.pop("_legacy_row_fingerprint", None)
        line.update(line_key=digest([source["source_key"], "regional", index]),
            amount=entry["amount"], amount_raw=entry["amount"],
            company=entry["company"] if entry["region"] == "mx" else "",
            regional_company=entry["company"],
            company_basis="manual", expense_scope="mx" if entry["region"] == "mx" or (entry["region"] == "pending" and origin == "mx") else "non_mx",
            scope_reason=note, review_note=note, reviewed_by=reviewer, reviewed_at=reviewed_at,
            reviewed_fingerprint=line.get("row_fingerprint"), mapping_version="")
        # Explicit, balanced amounts resolve the original multi-company question.
        problems = set(line.get("base_issues", [])) - {"allocation"}
        if not valid or original is None:
            problems.add("stale")
            problems.add("amount")
        if entry["region"] == "pending":
            problems.add("company")
        if entry["region"] == "mx" and not line.get("period_month"):
            problems.add("period")
        line["issues"] = sorted(problems)
        line["base_issues"] = sorted(problems)
        line["review_state"] = "excluded" if line["expense_scope"] == "non_mx" else "needs_review" if problems else "confirmed"
        result.append(line)
    return result
