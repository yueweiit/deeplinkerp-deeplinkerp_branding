"""Operator-only import of a reconciled, zero-opening domestic bank journal CSV."""
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import date
from decimal import Decimal


DOCTYPE = "CN Cash Journal Entry"
IMPORT_TOKEN = object()
FACTS = ("posting_date", "direction", "amount", "dingtalk_approval_number", "summary",
         "journal_category", "journal_subcategory", "journal_detail")
HEADER = ("年", "月", "日", "钉钉申请单号", "类别", "二级明细", "三级明细", "所属项目",
          "摘要", "收入", "支出", "余额", "发票寄送信息", "归属部门")


def read_journal(stream, title):
    reader = iter(csv.reader(stream))
    heading, header, opening = (next(reader) for _ in range(3))
    if heading[0].strip() != title or tuple(c.strip().splitlines()[-1] if c.strip() else ""
                                            for c in header[:14]) != HEADER:
        raise ValueError("Journal title or column headers do not match")
    if "期末余额" not in opening[8] or _yuan(opening[11]) != 0:
        raise ValueError("Expected a documented zero opening balance")
    balance = Decimal(0)
    occurrences = Counter()
    result = []
    ended = False
    for index, raw in enumerate(reader, 4):
        row = [cell.strip() for cell in raw]
        populated = {i for i, cell in enumerate(row) if cell}
        if not populated:
            continue
        if populated == {11} and not ended and _yuan(row[11]) == balance:
            ended = True
            continue
        if (ended or len(row) < 12 or populated - {0,1,2,3,4,5,6,8,9,10,11}
                or bool(row[9]) == bool(row[10])):
            raise ValueError(f"Unmapped or invalid transaction at CSV row {index}")
        day = date(*map(int, row[:3])).isoformat()
        income = bool(row[9])
        amount = _yuan(row[9] if income else row[10])
        balance += amount if income else -amount
        if amount <= 0 or balance != _yuan(row[11]) or (result and day < result[-1]["posting_date"]):
            raise ValueError(f"Date, amount or running balance mismatch at CSV row {index}")
        facts = (day, "income" if income else "expense", format(amount, ".2f"), row[3],
                 row[8], row[4], row[5], row[6])
        occurrences[facts] += 1
        identity = json.dumps([title, "CNY", *facts, occurrences[facts]], ensure_ascii=False)
        result.append(dict(zip(FACTS, facts), source_order=len(result) + 1, source_book=title,
                           import_key=hashlib.sha256(identity.encode()).hexdigest(), balance=balance))
    if not ended or not result:
        raise ValueError("Missing closing balance or transactions")
    return result


def _yuan(text):
    value = text.lstrip("￥¥").strip()
    if not re.fullmatch(r"-?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?", value):
        raise ValueError("Invalid yuan amount")
    return Decimal(value.replace(",", ""))


def import_journal(path, company, account, title, expected_rows, expected_closing, apply=False):
    """Run via bench execute, never via HTTP. Any conflict aborts the whole transaction."""
    import frappe
    from deeplinkerp_branding.services.cn_cash_book import ORDER
    from deeplinkerp_branding.services.operating_expenses import _account

    if frappe.session.user != "Administrator" or type(apply) is not bool:
        raise ValueError("Requires the site administrator and a boolean apply flag")
    if _account(account, company, {"Bank"}).account_currency != "CNY":
        raise ValueError("Expected an active CNY bank account of the specified company")
    with open(path, encoding="utf-8-sig", newline="") as stream:
        rows = read_journal(stream, title)
    if len(rows) != int(expected_rows) or rows[-1]["balance"] != Decimal(str(expected_closing)):
        raise ValueError("Source count or closing balance does not match")
    if apply:
        # An ordinary Desk edit does not lock the Account. Lock journal ranges,
        # including gaps, through verification and the caller's transaction commit.
        if frappe.db.db_type != "mariadb":
            raise ValueError("This importer requires MariaDB transactional range locks")
        isolation, autocommit = frappe.db.sql("SELECT @@session.tx_isolation, @@session.autocommit")[0]
        if autocommit or isolation not in ("REPEATABLE-READ", "SERIALIZABLE"):
            raise ValueError("Import requires an InnoDB transaction with repeatable reads")
        frappe.get_doc("Account", account, for_update=True)
    def journal_rows(filters, fields, order_by=None):
        return frappe.qb.get_query(DOCTYPE, fields=fields, filters=filters, order_by=order_by,
                                   for_update=apply, ignore_permissions=True).run(as_dict=True)
    fields = ["name", "account", "import_key", "source_book", "source_order", *FACTS]
    prior = journal_rows({"source_book": title}, ["company","account","currency","posting_date","import_key"])
    check_manifest(prior, rows, company, account)
    scope = {"company": company, "currency": "CNY", "posting_date": ["<=", rows[-1]["posting_date"]]}
    def existing():
        return journal_rows(scope, fields, ORDER)
    ledger = existing()
    pending = plan_import(rows, ledger, account)
    if apply:
        frappe.db.savepoint("before_cash_book_import")
        try:
            for row in pending:
                doc = frappe.get_doc({"doctype": DOCTYPE, "company": company, "account": account, "currency": "CNY",
                                      **{k: v for k, v in row.items() if k != "balance"}})
                doc.flags.cash_book_import = IMPORT_TOKEN
                doc.insert()
            saved = existing()
            if plan_import(rows, saved, account):
                raise ValueError("Post-import row verification failed")
            verify_balances(rows, saved, account)
        except Exception:
            frappe.db.rollback(save_point="before_cash_book_import")
            raise
    return {"rows": len(rows), "existing": len(rows)-len(pending), "new": len(pending),
            "written": len(pending) if apply else 0, "closing": str(rows[-1]["balance"])}


def check_manifest(prior, rows, company, account):
    dates = {row["import_key"]: row["posting_date"] for row in rows}
    for doc in prior:
        if (doc["company"], doc["account"], doc["currency"]) != (company, account, "CNY"):
            raise ValueError("This source book is already registered to another company, currency or account")
        if doc["import_key"] not in dates:
            raise ValueError("A previously imported row is absent from this full source book")
        if str(doc["posting_date"]) != dates[doc["import_key"]]:
            raise ValueError("Previously imported transaction date has changed; nothing overwritten")


def plan_import(rows, ledger, account):
    keyed = {doc["import_key"]: doc for doc in ledger if doc["import_key"]}
    keys = {row["import_key"] for row in rows}
    unlinked = [doc for doc in ledger if doc["account"] in (None, "", account) and doc["import_key"] not in keys]
    candidates = {(str(doc["posting_date"]), doc["direction"], Decimal(str(doc["amount"]))) for doc in unlinked}
    approvals = {doc["dingtalk_approval_number"].strip() for doc in unlinked if doc["dingtalk_approval_number"]}
    pending = []
    for row in rows:
        n = row["source_order"]
        if ((row["posting_date"], row["direction"], Decimal(row["amount"])) in candidates
                or row["dingtalk_approval_number"] in approvals):
            raise ValueError(f"Possible existing transaction for source transaction {n}; nothing overwritten")
        previous = keyed.get(row["import_key"])
        if not previous:
            pending.append(row)
            continue
        if previous["account"] != account or previous["source_order"] != n or any(
            (Decimal(str(previous[k])) != Decimal(row[k]) if k == "amount" else
             str(previous[k] or "") != row[k]) for k in FACTS
        ):
            raise ValueError(f"Previously imported CSV row {n} has changed; nothing overwritten")
    # An existing movement in this book may affect the balance even if not a duplicate.
    simulated = [*ledger, *({**row, "account": account} for row in pending)]
    simulated.sort(key=lambda r: (str(r["posting_date"]), r.get("source_order") or 0))
    verify_balances(rows, simulated, account)
    return pending


def verify_balances(rows, ledger, account):
    wanted = {row["import_key"]: row["balance"] for row in rows}
    balance = Decimal(0)
    for doc in ledger:
        if doc["account"] != account:
            continue
        amount = Decimal(str(doc["amount"]))
        balance += amount if doc["direction"] == "income" else -amount
        if doc["import_key"] in wanted and balance != wanted[doc["import_key"]]:
            raise ValueError("Account running balance differs from the source; review existing entries")
    if balance != rows[-1]["balance"]:
        raise ValueError("Account closing balance differs from the source; review existing entries")
