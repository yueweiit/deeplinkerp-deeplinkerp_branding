"""Amount evidence and version-bound decisions for unified management reports.

Original OA facts and native financial reporting remain unchanged. Decisions
live in the protected allocation's facts_json and its existing Version history.
"""
from copy import deepcopy
from decimal import Decimal, DecimalException, ROUND_HALF_UP
import json

from . import mexico_expense_contract as amounts
from . import regional_expense_contract as allocations
from .operating_expense_contract import digest

VERSION = "approval-amount-review-v1"
EVIDENCE = "_amount_evidence"
REVIEW = "_amount_review"
MAX_OPERAND_LENGTH = 128


def bounded_operand(value):
    """Bound both input and fixed-point expansion before formatting OA numbers."""
    raw = str(value)
    if len(raw) > MAX_OPERAND_LENGTH:
        raise ValueError()
    number = Decimal(str(amounts.ungrouped_number(raw)))
    if not number.is_finite() or number < 0:
        raise ValueError()
    # adjusted() and the exponent inspect the compact Decimal representation;
    # they do not expand inputs such as 1e100000 or 1e-100000 into strings.
    digits = max(1, number.adjusted() + 1) + max(0, -number.as_tuple().exponent)
    if digits > MAX_OPERAND_LENGTH:
        raise ValueError()
    return number


def purchase_row(quantity, unit_price, total):
    result = {"quantity": None, "unit_price": None, "amount": amounts.exact_amount(total),
              "calculated_amount": None, "difference": None, "issues": []}
    try:
        qty, price = (bounded_operand(value) for value in (quantity, unit_price))
        result.update(quantity=format(qty, "f"), unit_price=format(price, "f"))
        calculated = (qty * price).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
        result["calculated_amount"] = format(calculated, ".2f")
        if result["amount"] is None:
            result["issues"].append("total")
        else:
            delta = Decimal(result["amount"]) - calculated
            result["difference"] = format(delta, ".2f")
            if delta:
                result["issues"].append("total")
        if qty > 0 and price == 0:
            result["issues"].append("amount_pending")
    except (DecimalException, ValueError):
        result["issues"].append("amount")
    return result


def evidence(source, components):
    """Retain only monetary columns from the parser's actual bound tables."""
    header = amounts.exact_amount(source.get("total_amount"))
    tables = []
    for table in json.loads(source.get("details_json") or "{}").get("tables", []):
        if table.get("kind") not in ("items_table", "processors_table"):
            continue
        rows = [{"row_number": row["row_id"], **row["amount_check"]}
                for row in table["rows"] if "amount_check" in row]
        if not rows:
            continue
        total = sum((Decimal(row["amount"]) for row in rows), Decimal(0)) if all(row["amount"] is not None for row in rows) else None
        calculated = sum((Decimal(row["calculated_amount"]) for row in rows), Decimal(0)) if all(row["calculated_amount"] is not None for row in rows) else None
        tables.append({"label": table.get("label") or "", "rows": rows,
            "total": format(total, ".2f") if total is not None else None,
            "calculated_total": format(calculated, ".2f") if calculated is not None else None,
            "difference": format(total - Decimal(header), ".2f") if total is not None and header is not None else None})
    return {"header_amount": header, "currency": source.get("currency"), "tables": tables,
            "form_fingerprint": digest(components)}


def fingerprint(source, lines):
    return digest([VERSION, allocations.fingerprint(source, lines), source.get(EVIDENCE)])


def supported(source, lines, entries):
    """Only a whole-approval monetary line, with no independent allocation."""
    if len(lines) != 1 or len(entries) != 1:
        return False
    line, entry = lines[0], entries[0]
    header = amounts.exact_amount(source.get("total_amount"))
    issues = set(line.get("base_issues") or [])
    return bool(header and Decimal(header) > 0 and source.get("approval_type") == "purchase"
        and line.get("table_id") == "header" and line.get("line_key") == entry.get("line_key")
        and entry.get("assignment_mode") == "automatic" and entry.get("source_available", True)
        and line.get("currency") == source.get("currency")
        and amounts.exact_amount(line.get("amount")) == header
        and amounts.exact_amount(entry.get("amount")) == header
        and "total" in issues and not issues.intersection((allocations.BLOCKING - {"total"}) | {"amount_pending", "allocation"})
        and source.get(EVIDENCE, {}).get("tables")
        and all(table["total"] is not None and table["calculated_total"] is not None
                for table in source[EVIDENCE]["tables"]))


def accepted(source, lines, entries):
    review = source.get(REVIEW) or {}
    return bool(review.get("state") == "confirmed" and review.get("fingerprint") == fingerprint(source, lines)
        and review.get("amount") == amounts.exact_amount(source.get("total_amount"))
        and review.get("reviewed_by") and review.get("reviewed_at") and review.get("note")
        and supported(source, lines, entries))


def preserve(source, lines, previous):
    """Once facts change a decision stays stale, even if they later revert."""
    review = deepcopy(previous.get(REVIEW)) if previous else None
    if review:
        if review.get("fingerprint") != fingerprint(source, lines):
            review["state"] = "stale"
        source[REVIEW] = review


def decision(source, lines, *, note, user, when):
    return {"state": "confirmed", "fingerprint": fingerprint(source, lines),
        "amount": amounts.exact_amount(source.get("total_amount")), "currency": source.get("currency"),
        "note": note, "reviewed_by": user, "reviewed_at": str(when),
        "evidence": deepcopy(source[EVIDENCE])}
