"""Execute the cash-book queries against a disposable journal with row-level visibility."""
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.database.operator_map import OPERATOR_MAP
from frappe.database.query import Engine
from frappe.model import get_permitted_fields
from frappe.model.meta import Meta
from frappe.query_builder.utils import mask_fields
from pypika import Query, Table
from pypika.functions import Count
from pypika.queries import QueryBuilder

from deeplinkerp_branding.services import cn_cash_book as book


class CashBookTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE journal (
            name TEXT PRIMARY KEY, posting_date TEXT, source_order INTEGER, creation TEXT,
            direction TEXT, amount NUMERIC, account TEXT, company TEXT, currency TEXT, readable INTEGER, owner TEXT
        )""")
        table = Table("journal")
        # Use Frappe's actual permlevel, sharing and masking logic with in-memory metadata.
        self.meta = object.__new__(Meta)
        self.meta.name, self.meta.istable = book.DOCTYPE, False
        self.meta.default_fields = ["name", "creation"]
        self.columns = {name: frappe._dict(fieldname=name, fieldtype=kind, permlevel=0)
                        for name, kind in (("amount", "Currency"), ("direction", "Select"), ("account", "Link"),
                                           ("company", "Link"), ("currency", "Link"), ("posting_date", "Date"),
                                           ("source_order", "Int"))}
        self.meta.fields = list(self.columns.values())
        self.meta.permissions = [frappe._dict(role="Accounts User", read=1, permlevel=0)]
        self.roles = ["Accounts User"]
        self.enterContext(patch.multiple(frappe.local, create=True, role_permissions={},
                                         session=frappe._dict(user="reader@example.test")))
        self.enterContext(patch("frappe.permissions.get_doctype_ptype_map", return_value={}))
        self.enterContext(patch.object(frappe.share, "get_shared", return_value=["shared elsewhere"]))

        def get_query(doctype, fields, filters, ignore_permissions=True):
            self.assertEqual(doctype, book.DOCTYPE)
            query = Query.from_(table).select(*fields)
            if not ignore_permissions:
                query = query.where(table.readable == 1)
                if Engine().requires_owner_constraint(book.get_role_permissions(doctype)):
                    query = query.where(table.owner == frappe.session.user)
            for field, condition in filters.items():
                op, value = condition if isinstance(condition, list) else ("=", condition)
                query = query.where(OPERATOR_MAP[op](table[field], value))
            return query

        def get_list(doctype, fields, filters, order_by, offset, limit):
            if fields == ["*"]:
                fields = get_permitted_fields(doctype, ignore_virtual=True)
            query = get_query(doctype, fields, filters, ignore_permissions=False)
            for term in order_by.split(", "):
                field, direction = term.split()
                self.assertEqual(direction, "asc")
                query = query.orderby(table[field])
            return mask_fields(doctype, fields, query.limit(limit).offset(offset).run(as_dict=True))

        def get_all(doctype, fields, filters, group_by, order_by, as_list):
            self.assertEqual(fields, ["account", {"COUNT": "name"}])
            return get_query(doctype, [table.account, Count(table.name)], filters).groupby(table[group_by]).run()

        def count(doctype, filters):
            return get_query(doctype, [Count("*")], filters).run()[0][0]

        def run(query, as_dict=False):
            rows = self.db.execute(query.get_sql()).fetchall()
            return [frappe._dict(dict(row)) for row in rows] if as_dict else rows

        self.enterContext(patch.object(QueryBuilder, "run", run, create=True))
        self.enterContext(patch.multiple(frappe, qb=SimpleNamespace(from_=Query.from_, get_query=get_query),
                                         db=SimpleNamespace(count=count, table_exists=lambda dt: True),
                                         get_list=get_list, get_all=get_all, get_meta=lambda dt: self.meta,
                                         get_roles=lambda user=None: self.roles,
                                         cache=SimpleNamespace(get_value=lambda key: None, set_value=lambda *args: None)))

    def entry(self, name, day, direction, amount, *, account="Bank", company="CN", currency="CNY",
              order=1, readable=True, owner=None):
        self.db.execute("INSERT INTO journal VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (name, day, order, "12:00", direction, amount, account, company, currency, readable,
                         owner or frappe.session.user))

    def page(self, account="Bank", start=0):
        return book.list_page("CN", "CNY", account, start, from_month="2023-12", to_month="2024-02")

    def test_closed_month_range_keeps_carryover_and_totals_on_every_page(self):
        self.entry("prior receipt", "2023-10-01", "income", "100.01")
        self.entry("prior payment", "2023-11-30", "expense", "10.00")
        for i in reversed(range(20)):
            self.entry(f"receipt{i:02}", "2023-12-01", "income", "1.25")
        self.entry("payment", "2024-02-29", "expense", "2.19")
        self.entry("next month", "2024-03-01", "expense", 900, readable=False)
        self.entry("wrong company", "2023-12-01", "income", 800, company="Other")
        self.entry("wrong currency", "2023-12-01", "income", 700, currency="USD")
        self.entry("wrong account", "2024-02-01", "income", 600, account="Other")

        first, second, empty = (self.page(start=start) for start in (0, 20, 40))
        for result in (first, second, empty):
            self.assertEqual(result["summary"], {"opening": "90.01", "income": "25.00", "expense": "2.19",
                                                 "closing": "112.82", "count": 21})
        self.assertTrue(first["has_next"])
        self.assertEqual([row.name for row in first["rows"]], [f"receipt{i:02}" for i in range(20)])
        self.assertEqual(first["rows"][0].amount, "1.25")
        self.assertEqual(first["balances"]["receipt00"], "91.26")
        self.assertEqual(first["balances"]["receipt19"], "115.01")
        self.assertFalse(second["has_next"])
        self.assertEqual([row.name for row in second["rows"]], ["payment"])
        self.assertEqual(second["balances"], {"payment": "112.82"})
        self.assertEqual((empty["rows"], empty["balances"], empty["has_next"]), ([], {}, False))

    def test_empty_period_carries_the_last_balance(self):
        self.entry("overdraft", "2023-11-30", "expense", "5.20")
        self.entry("later", "2024-03-01", "income", 10)
        self.assertEqual(self.page()["summary"], {"opening": "-5.20", "income": "0.00", "expense": "0.00",
                                                  "closing": "-5.20", "count": 0})

    def test_accounts_reader_with_no_visible_history_does_not_probe_for_a_zero_balance(self):
        with patch.object(frappe.db, "count", side_effect=AssertionError("Unrestricted count")):
            empty = self.page()
            self.assertIsNone(empty["summary"]["opening"])
            self.assertIsNone(empty["summary"]["closing"])
            self.entry("outside reader scope", "2023-11-30", "income", 100, readable=False)
            self.assertEqual(self.page(), empty)

    def test_empty_and_fully_hidden_histories_are_indistinguishable_to_shared_reader(self):
        self.roles.clear()
        self.enterContext(patch.object(frappe.db, "count", side_effect=AssertionError("Unrestricted read")))
        months = {"from_month": "2024-02", "to_month": "2024-02"}
        result = book.list_page("CN", "CNY", "Bank", **months)
        self.assertEqual(result["summary"], {"opening": None, "income": "0.00", "expense": "0.00",
                                             "closing": None, "count": 0})
        self.entry("hidden prior", "2024-01-31", "expense", 100, readable=False)
        self.entry("private", "2024-02-29", "income", 500, readable=False)
        hidden = book.list_page("CN", "CNY", "Bank", **months)
        self.assertEqual(hidden, result)

    def test_masked_or_unreadable_monetary_inputs_return_only_visible_count(self):
        self.entry("receipt", "2024-02-01", "income", "12.34")
        self.entry("private", "2024-02-02", "expense", 500, readable=False)
        statements = []
        self.db.set_trace_callback(statements.append)
        for field in ("amount", "direction"):
            for restriction in ("mask", "permlevel"):
                with self.subTest(field=field, restriction=restriction), patch.dict(self.columns[field], {restriction: 1}):
                    result = self.page()
                    row, = result["rows"]
                    self.assertEqual(row.name, "receipt")
                    if restriction == "permlevel":
                        self.assertNotIn(field, row)
                    else:
                        self.assertEqual(row[field], None if field == "amount" else "XXXXXXXX")
                    self.assertEqual(result["summary"], {"opening": None, "income": None, "expense": None,
                                                         "closing": None, "count": 1})
                    self.assertEqual(result["balances"], {})
        self.assertFalse(any("SUM(" in sql.upper() for sql in statements))

    def test_share_only_reader_gets_visible_deltas_but_no_evidence_of_unshared_rows(self):
        self.roles.clear()
        self.entry("shared prior", "2023-11-30", "income", 100)
        self.entry("shared receipt", "2024-02-01", "income", "12.34")
        with (patch.object(frappe.db, "count", side_effect=AssertionError("Unrestricted count")),
              patch.object(frappe, "get_all", side_effect=AssertionError("Unrestricted account counts"))):
            shared = self.page()
            self.assertEqual(shared["rows"][0].amount, "12.34")
            self.assertEqual(shared["summary"], {"opening": None, "income": "12.34", "expense": "0.00",
                                                 "closing": None, "count": 1})
            self.assertEqual(shared["balances"], {})
            self.entry("private", "2024-01-01", "expense", 500, readable=False)
            self.assertEqual(self.page(), shared)

    def test_user_with_unmask_permission_keeps_amounts_and_running_balance(self):
        self.columns["amount"].mask = 1
        self.meta.permissions[0].mask = 1
        self.entry("receipt", "2024-02-01", "income", "12.34")
        result = self.page()
        self.assertEqual(result["rows"][0].amount, "12.34")
        self.assertEqual(result["balances"], {"receipt": "12.34"})
        self.assertEqual(result["summary"]["income"], "12.34")

    def test_owner_only_read_cannot_distinguish_another_owners_entry(self):
        self.meta.permissions[0].if_owner = 1
        self.entry("own receipt", "2024-02-01", "income", "12.34")
        with (patch.object(frappe.db, "count", side_effect=AssertionError("Unrestricted count")),
              patch.object(frappe, "get_all", side_effect=AssertionError("Unrestricted account counts"))):
            own = self.page()
            self.assertEqual([row.name for row in own["rows"]], ["own receipt"])
            self.assertEqual(own["balances"], {})
            self.assertEqual(own["summary"], {"opening": None, "income": "12.34", "expense": "0.00",
                                              "closing": None, "count": 1})
            self.entry("foreign payment", "2024-01-31", "expense", 500, owner="other@example.test")
            self.assertEqual(self.page(), own)

    def test_unrestricted_read_from_another_role_still_grants_balances(self):
        self.meta.permissions[0].if_owner = 1
        self.meta.permissions.append(frappe._dict(role="Journal Reader", read=1, permlevel=0))
        self.roles.append("Journal Reader")
        self.entry("foreign receipt", "2024-02-01", "income", "12.34", owner="other@example.test")
        page = self.page()
        self.assertEqual(page["balances"], {"foreign receipt": "12.34"})
        self.assertEqual(page["summary"]["closing"], "12.34")

    def test_masked_account_cannot_be_probed_by_filter_or_running_balance(self):
        self.columns["account"].mask = 1
        self.entry("receipt", "2024-02-01", "income", "12.34")
        result = self.page(account="")
        self.assertEqual(result["rows"][0].account, "XXXXXXXX")
        self.assertEqual(result["balances"], {})
        self.assertEqual(result["summary"]["income"], "12.34")
        with (patch.object(book, "_", side_effect=str),
              patch.object(frappe, "throw", side_effect=frappe.PermissionError),
              self.assertRaises(frappe.PermissionError)):
            self.page(account="Bank")

    def test_hidden_history_or_period_entry_suppresses_both_balances(self):
        self.entry("shared", "2024-02-15", "expense", "3.10")
        for day in ("2023-11-30", "2024-01-01", "2024-02-29"):
            with self.subTest(hidden_day=day):
                self.entry("private", day, "income", 500, readable=False)
                result = self.page()
                self.assertEqual(result["summary"], {"opening": None, "income": "0.00", "expense": "3.10",
                                                     "closing": None, "count": 1})
                self.assertEqual([row.name for row in result["rows"]], ["shared"])
                self.assertEqual(result["balances"], {"shared": "-3.10"} if day > "2024-02-15" else {})
                self.db.execute("DELETE FROM journal WHERE name='private'")

    def test_all_accounts_include_visible_unassigned_rows_without_combining_balances(self):
        self.entry("prior", "2023-11-01", "income", 10)
        self.entry("one", "2023-12-01", "income", "1.15")
        self.entry("two", "2023-12-01", "expense", "2.05", account="Wallet")
        self.entry("unassigned", "2024-02-29", "income", "0.15", account=None)
        self.entry("hidden", "2024-01-10", "income", 90, account="Wallet", readable=False)
        result = self.page(account="")
        self.assertEqual(result["summary"], {"opening": None, "income": "1.30", "expense": "2.05",
                                             "closing": None, "count": 3})
        self.assertEqual(result["balances"], {"one": "11.15"})

    def test_unfiltered_call_starts_at_zero_and_includes_all_history(self):
        self.entry("early", "2022-07-01", "income", "50.00")
        self.entry("late", "2026-01-01", "expense", "1.10")
        result = book.list_page("CN", "CNY", "Bank")
        self.assertEqual(result["summary"], {"opening": "0.00", "income": "50.00", "expense": "1.10",
                                             "closing": "48.90", "count": 2})
        self.assertEqual(result["balances"], {"early": "50.00", "late": "48.90"})

    def test_invalid_months_fail_before_reading_rows(self):
        with (patch.object(book, "_", side_effect=str),
              patch.object(frappe, "throw", side_effect=frappe.ValidationError),
              patch.object(frappe, "get_list", side_effect=AssertionError("Unexpected journal read"))):
            for first, last in ((None, "2024-01"), ("2024-01", ""), ("2024-02", "2024-01"),
                                ("2024-1", "2024-01"), ("2024-01", "2024-13"), ("2024-01-01", "2024-01")):
                with self.subTest(first=first, last=last), self.assertRaises(frappe.ValidationError):
                    book.list_page("CN", "CNY", from_month=first, to_month=last)
