"""Execute the shared query expressions in memory under a company permission scope."""
import json
import sqlite3
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from pypika import Query, Table
from pypika.queries import QueryBuilder

from deeplinkerp_branding.services import mexico_expenses as s


class MexicoQueryTests(unittest.TestCase):
    def management(self, template_state=None, **filters):
        clock = SimpleNamespace(now=lambda zone: datetime(2026, 11, 24))
        templates = [{**row, "enabled": 1, "last_sync_at": "2026-11-24", **(template_state or {})} for row in s._templates()]
        with patch.object(s, "_finance"), patch.object(s, "_companies", return_value=["A", "Empty"]), \
                patch.object(s, "datetime", clock), patch.object(s, "_templates", return_value=templates):
            return s.get_management_overview({"from_date": "2026-11-01", "to_date": "2026-11-30", **filters})

    def test_monthly_dashboard_includes_empty_authorized_companies_and_exact_drilldown(self):
        result = self.management()
        self.assertEqual(result["total"], "90.00")
        self.assertEqual(result["scope"]["to_date"], "2026-11-24")
        self.assertEqual(result["scope"]["status"], "finance_approved")
        self.assertEqual([row["company"] for row in result["companies"]], ["A", "Empty"])
        self.assertEqual(result["companies"][1]["lines"], 0)
        self.assertNotIn("forbidden", str(result))
        detail = s._query(result["scope"], ["amount"]).run(as_dict=True)
        self.assertEqual(sum(row["amount"] for row in detail), 90)
        self.assertEqual(result["included"]["applications"], 2)
        self.assertEqual(result["review"], {"lines": 1, "awaiting_lines": 1, "unassigned_lines": 0})
        for bucket, expected in (("attention", {"stale_mapping"}), ("awaiting_finance", {"missing_period"})):
            scope = {**result["scope"], "status": "active", "confirmed_only": "", "review_bucket": bucket}
            self.assertEqual({r["name"] for r in s._query(scope, ["name"], review=True).run(as_dict=True)}, expected)

    def test_waiting_with_another_issue_or_stale_mapping_remains_attention(self):
        for changes in (("issues", '["period", "amount"]'), ("mapping_version", "old"), ("company_basis", None)):
            self.db.execute('UPDATE "tabMexico Expense Line" SET "'+changes[0]+'"=? WHERE name=?', (changes[1], "missing_period"))
            if changes[0] == "company_basis":
                self.db.execute('UPDATE "tabMexico Expense Line" SET mapping_version=? WHERE name=?', ("old", "missing_period"))
            result = self.management()
            self.assertEqual(result["review"]["lines"], 2)
            self.assertEqual(result["review"]["awaiting_lines"], 0)
            rows = s._query({**result["scope"], "status": "active", "confirmed_only": "", "review_bucket": "attention"}, ["name"], review=True).run(as_dict=True)
            self.assertEqual({r["name"] for r in rows}, {"missing_period", "stale_mapping"})

    def test_comparison_uses_same_elapsed_days_and_suppresses_incomplete_baseline(self):
        self.assertFalse(self.management()["comparison"]["comparable"])
        self.db.execute('UPDATE "tabMexico Expense Line" SET is_active=0 WHERE name=?', ("stale_mapping",))
        # This old-month approval is outside the comparable elapsed window.
        self.db.execute('UPDATE "tabMexico Expense Line" SET finance_approved_date=? WHERE name=?', ("2026-10-25", "october"))
        result = self.management()
        self.assertEqual(result["comparison"]["previous_to"], "2026-10-24")
        self.assertEqual(result["comparison"]["previous"], "0.00")
        self.assertIsNone(result["comparison"]["percent"])
        self.db.execute('UPDATE "tabMexico Expense Line" SET finance_approved_date=? WHERE name=?', ("2026-10-24", "october"))
        result = self.management()
        self.assertTrue(result["comparison"]["comparable"])
        self.assertEqual(result["comparison"]["previous"], "7.00")
        self.assertEqual(result["companies"][0]["delta"], "83.00")

    def test_company_dashboard_trend_expands_dates_without_changing_monthly_total(self):
        with patch.object(s, "_company_access"):
            result = self.management(company="A")
        self.assertEqual(result["total"], "90.00")
        self.assertEqual(sum(float(row["amount"]) for row in result["forms"]), 90)
        self.assertEqual(sum(float(row["amount"]) for row in result["categories"]), 90)
        self.assertEqual(len(result["trend"]), 6)
        october = next(row for row in result["trend"] if row["month"] == "2026-10")
        self.assertEqual((october["amount"], october["to_date"]), ("7.00", "2026-10-31"))
        self.assertEqual(result["trend"][-1]["to_date"], "2026-11-24")

    def test_converted_dashboard_amount_matches_public_detail_lines(self):
        self.db.execute('UPDATE "tabMexico Expense Line" SET amount_raw=CAST(amount AS TEXT)')
        with patch.object(s.fx, "load_month_rate", side_effect=lambda currency, day, **kwargs: Decimal("17.13") if currency == "USD" else Decimal(1)), patch.object(s, "_finance"):
            result = self.management(reporting_currency="MXN")
            details = s.get_lines(result["scope"])
        self.assertEqual(result["total"], "8655.00")
        self.assertEqual(sum(Decimal(row["converted_amount"]) for row in details["rows"]), Decimal(result["total"]))
        self.assertEqual({row["currency"] for row in details["rows"]}, {"MXN", "USD"})

    def test_missing_previous_rates_preserve_selected_totals_and_can_recover(self):
        self.db.execute('UPDATE "tabMexico Expense Line" SET currency=? WHERE name=?', ("USD", "october"))
        missing = {"2026-10-01"}
        def rate(currency, day, **options):
            if day in missing:
                if options.get("quiet"):
                    raise s.fx.RateUnavailable("Missing historical rate")
                raise ValueError("Missing required rate")
            return Decimal("17.13") if currency == "USD" else Decimal(1)
        with patch.object(s.fx, "load_month_rate", side_effect=rate), patch.object(s, "_company_access"):
            for company in ("", "A"):
                with self.subTest(company=company):
                    result = self.management(reporting_currency="MXN", company=company)
                    self.assertEqual(result["total"], "8655.00")
                    self.assertEqual(result["included"]["lines"], 4)
                    self.assertEqual(result["comparison"]["unavailable_reason"], "exchange_rate")
                    self.assertFalse(result["comparison"]["comparable"])
                    for key in ("previous", "delta", "percent"):
                        self.assertIsNone(result["comparison"][key])
                        self.assertIsNone(result["companies"][0][key])
                    if company:
                        self.assertEqual(sum(Decimal(row["amount"]) for row in result["forms"]), Decimal("8655.00"))
                        self.assertEqual(result["trend"], [])
                        self.assertEqual(result["trend_unavailable_reason"], "exchange_rate")
            missing.clear()
            recovered = self.management(reporting_currency="MXN", company="A")
            self.assertEqual(recovered["comparison"]["previous"], "119.91")
            self.assertEqual(recovered["comparison"]["unavailable_reason"], "review")
            self.assertNotIn("trend_unavailable_reason", recovered)
            self.assertEqual(len(recovered["trend"]), 6)

    def test_missing_older_trend_rate_preserves_current_and_comparison(self):
        self.db.execute('UPDATE "tabMexico Expense Line" SET finance_approved_date=?,period_month=? WHERE name=?',
                        ("2026-09-01", "2026-09-01", "usd"))
        self.db.execute('UPDATE "tabMexico Expense Line" SET is_active=0 WHERE name=?', ("stale_mapping",))
        def rate(currency, day, **options):
            if day == "2026-09-01" and options.get("quiet"):
                raise s.fx.RateUnavailable("Missing September rate")
            raise AssertionError("Only September needs conversion")
        with patch.object(s.fx, "load_month_rate", side_effect=rate), patch.object(s, "_company_access"):
            result = self.management(company="A", reporting_currency="MXN")
        self.assertEqual(result["total"], "90.00")
        self.assertTrue(result["comparison"]["comparable"])
        self.assertEqual(result["comparison"]["previous"], "7.00")
        self.assertEqual(result["trend"], [])
        self.assertEqual(result["trend_unavailable_reason"], "exchange_rate")

    def test_missing_selected_month_rate_still_fails(self):
        with patch.object(s.fx, "load_month_rate", side_effect=ValueError("Missing required rate")):
            with self.assertRaisesRegex(ValueError, "Missing required rate"):
                self.management(reporting_currency="MXN")

    def test_optional_history_does_not_hide_permission_or_query_errors(self):
        aggregate = s._aggregates
        for start in ("2026-10-01", "2026-06-01"):
            for error_type in (frappe.PermissionError, RuntimeError):
                with self.subTest(start=start, error=error_type):
                    def fail_history(values, *args, **kwargs):
                        if values["from_date"] == start:
                            raise error_type("History query failed")
                        return aggregate(values, *args, **kwargs)
                    with patch.object(s, "_aggregates", side_effect=fail_history), patch.object(s, "_company_access"):
                        with self.assertRaisesRegex(error_type, "History query failed"):
                            self.management(company="A")

    def test_month_to_date_does_not_include_future_finance_dates(self):
        self.db.execute('UPDATE "tabMexico Expense Line" SET finance_approved_date=? WHERE name=?', ("2026-11-25", "a1"))
        result = self.management()
        self.assertEqual(result["total"], "80.00")
        self.assertNotIn("a1", {row["name"] for row in s._query(result["scope"], ["name"]).run(as_dict=True)})

    def test_trend_distinguishes_absent_records_from_confirmed_zero(self):
        self.db.execute('UPDATE "tabMexico Expense Line" SET amount=0 WHERE name=?', ("october",))
        with patch.object(s, "_company_access"):
            result = self.management(company="A")
        september = next(row for row in result["trend"] if row["month"] == "2026-09")
        october = next(row for row in result["trend"] if row["month"] == "2026-10")
        self.assertEqual((september["amount"], september["lines"]), (None, 0))
        self.assertEqual((october["amount"], october["lines"]), ("0.00", 1))

    def test_comparison_returns_the_actual_unavailability_reason(self):
        self.assertEqual(self.management()["comparison"]["unavailable_reason"], "review")
        self.assertEqual(self.management(template_state={"last_sync_at":None})["comparison"]["unavailable_reason"], "synchronization")
        self.db.execute('UPDATE "tabMexico Expense Line" SET is_active=0 WHERE name IN (?,?)', ("october", "stale_mapping"))
        self.assertEqual(self.management()["comparison"]["unavailable_reason"], "history")

    def test_management_rejects_future_periods(self):
        with patch.object(s, "_", side_effect=lambda text: text), patch.object(frappe, "throw", side_effect=ValueError("future period")):
            with self.assertRaisesRegex(ValueError, "future period"):
                self.management(from_date="2026-12-01", to_date="2026-12-31")

    def test_missing_current_records_do_not_claim_a_complete_drop_in_spending(self):
        self.db.execute('UPDATE "tabMexico Expense Line" SET is_active=0 WHERE name!=?', ("october",))
        result = self.management()
        self.assertEqual(result["total"], "0.00")
        self.assertFalse(result["comparison"]["comparable"])
        self.assertIsNone(result["companies"][0]["percent"])

    def test_unknown_company_filter_never_returns_assigned_rows(self):
        rows = s._query({**self.filters, "unassigned_only": "1"}, ["name"], review=True).run(as_dict=True)
        self.assertEqual(rows, [])

    def test_incomplete_or_disabled_sync_never_claims_comparable_history(self):
        self.db.execute('UPDATE "tabMexico Expense Line" SET is_active=0 WHERE name=?', ("stale_mapping",))
        for state in ({"cursor": "next"}, {"last_error": "failure"}, {"last_sync_at": None}, {"enabled": 0}):
            with self.subTest(state=state):
                result = self.management(template_state=state)
                self.assertEqual(result["total"], "90.00")
                self.assertFalse(result["sync_complete"])
                self.assertFalse(result["comparison"]["comparable"])
                self.assertIsNone(result["comparison"]["percent"])

    def test_manager_unknown_company_bucket_is_separate_and_reconciles_to_review(self):
        self.db.execute('UPDATE "tabMexico Expense Line" SET company=NULL,review_state=?,issues=? WHERE name=?',
                        ("needs_review", '["company"]', "forbidden"))
        self.db.execute('UPDATE "tabMexico Expense Line" SET company=?,is_active=1,review_state=?,issues=? WHERE name=?',
                        ("", "needs_review", '["company"]', "inactive"))
        def manager_query(doctype, fields, filters, ignore_permissions):
            self.assertFalse(ignore_permissions)
            table = Table("tab" + doctype)
            query = Query.from_(table).select(*[getattr(table, f) if isinstance(f, str) else f for f in fields])
            query = query.where((table.company == "A") | table.company.isnull() | (table.company == ""))
            for field, operator, value in filters:
                column = getattr(table, field)
                query = query.where(column.isin(value) if operator == "in" else column == value)
            return query
        with patch.object(frappe.qb, "get_query", manager_query):
            result = self.management()
            self.assertEqual(result["total"], "90.00")
            self.assertEqual(result["review"]["unassigned_lines"], 2)
            self.assertEqual(result["review"]["lines"], 3)
            scope = {**result["scope"], "confirmed_only": "", "status": "active", "review_bucket": "attention", "unassigned_only": "1"}
            rows = s._query(scope, ["name"], review=True).run(as_dict=True)
            self.assertEqual({row["name"] for row in rows}, {"forbidden", "inactive"})

    def test_company_comparison_retains_visible_unassigned_uncertainty_in_either_period(self):
        self.db.execute('UPDATE "tabMexico Expense Line" SET is_active=0 WHERE name=?', ("stale_mapping",))
        self.db.execute('UPDATE "tabMexico Expense Line" SET company=NULL,review_state=?,issues=? WHERE name=?',
                        ("needs_review", '["company"]', "forbidden"))

        def manager_query(doctype, fields, filters, ignore_permissions):
            self.assertFalse(ignore_permissions)
            table = Table("tab" + doctype)
            query = Query.from_(table).select(*[getattr(table, f) if isinstance(f, str) else f for f in fields])
            query = query.where((table.company == "A") | table.company.isnull() | (table.company == ""))
            for field, operator, value in filters:
                column = getattr(table, field)
                query = query.where(column.isin(value) if operator == "in" else column == value)
            return query

        with patch.object(frappe.qb, "get_query", manager_query), patch.object(s, "_company_access"):
            for day, company in (("2026-11-05", None), ("2026-10-05", "")):
                with self.subTest(period=day):
                    self.db.execute('UPDATE "tabMexico Expense Line" SET finance_approved_date=?,company=? WHERE name=?',
                                    (day, company, "forbidden"))
                    overview = self.management()
                    selected = self.management(company="A")
                    company_row = next(row for row in overview["companies"] if row["company"] == "A")
                    for comparison in (company_row, selected["companies"][0], selected["comparison"]):
                        self.assertFalse(comparison["comparable"])
                        self.assertEqual(comparison["unavailable_reason"], "review")
                        self.assertIsNone(comparison["percent"])
                    self.assertEqual(selected["total"], "90.00")
                    self.assertEqual(selected["comparison"]["previous"], "7.00")
                    self.assertEqual(selected["review"], {"lines": 0, "awaiting_lines": 1, "unassigned_lines": 0})
                    self.assertNotIn("forbidden", str(selected))
            # Once the uncertain record is resolved, both views can compare again.
            self.db.execute('UPDATE "tabMexico Expense Line" SET is_active=0 WHERE name=?', ("forbidden",))
            self.assertTrue(self.management()["companies"][0]["comparable"])
            self.assertTrue(self.management(company="A")["comparison"]["comparable"])

    def test_company_comparison_does_not_read_unassigned_lines_outside_permissions(self):
        self.db.execute('UPDATE "tabMexico Expense Line" SET is_active=0 WHERE name=?', ("stale_mapping",))
        # The fixture's native permission scope allows only company A, never unknown owners.
        self.db.execute('UPDATE "tabMexico Expense Line" SET company=NULL,review_state=?,issues=? WHERE name=?',
                        ("needs_review", '["company"]', "forbidden"))
        with patch.object(s, "_company_access"):
            for result in (self.management(), self.management(company="A")):
                self.assertTrue(result["comparison"]["comparable"])
                self.assertEqual(result["comparison"]["percent"], "1185.7")
                self.assertEqual(result["review"]["unassigned_lines"], 0)

    def test_routed_approval_type_is_visible_without_an_mx_sync_template(self):
        from deeplinkerp_branding.services import regional_expenses as regional
        self.db.execute('CREATE TABLE "tabRegional Expense Allocation" (name TEXT)')
        self.db.execute('INSERT INTO "tabRegional Expense Allocation" VALUES (?)', ('shared',))
        self.db.execute('UPDATE "tabMexico Expense Line" SET approval_type=? WHERE source=?', ('operating', 'shared'))
        with patch.object(regional, "installed", return_value=True), patch.object(s, "_templates", return_value=[]):
            self.db.execute('UPDATE "tabMexico Expense Line" SET period_version=? WHERE source=?', (s._period_version(s._settings()), 'shared'))
            rows = s._query({**self.filters, "approval_type": "operating"}, ["name", "amount"]).run(as_dict=True)
        self.assertEqual({row.name for row in rows}, {"a1", "a2"})
        self.assertEqual(sum(row.amount for row in rows), 30)

    def setUp(self):
        self.db=sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.row_factory=sqlite3.Row
        meta=json.loads((Path(__file__).parents[1]/"deeplinkerp_branding/deeplinkerp_branding/doctype/mexico_expense_line/mexico_expense_line.json").read_text())
        columns=["name","modified",*[f["fieldname"] for f in meta["fields"]]]
        self.db.execute('CREATE TABLE "tabMexico Expense Line" ('+','.join('"'+c+'" '+('NUMERIC' if c=='amount' else 'TEXT') for c in columns)+')')
        self.db.execute('CREATE TABLE "tabMexico Expense Source" (name TEXT, corp_id TEXT, process_code TEXT)')
        self.queries=[]
        def native_get_query(doctype,fields,filters,ignore_permissions):
            self.assertFalse(ignore_permissions,"native company permissions must remain enabled")
            table=Table("tab"+doctype)
            query=Query.from_(table).select(*[getattr(table,f) if isinstance(f,str) else f for f in fields]).where(table.company=="A")
            for field,operator,value in filters:
                column=getattr(table,field)
                query=query.where(column.isin(value) if operator=='in' else column==value)
            return query
        def run(query,as_dict=False):
            sql=query.get_sql(quote_char='"');self.queries.append(sql)
            rows=self.db.execute(sql).fetchall()
            return [frappe._dict(dict(r)) for r in rows] if as_dict else rows
        facade=SimpleNamespace(from_=Query.from_,DocType=lambda name:Table("tab"+name),get_query=native_get_query)
        for target,value in [("qb",facade)]:
            p=patch.object(frappe,target,value);p.start();self.addCleanup(p.stop)
        p=patch.object(QueryBuilder,"run",run,create=True);p.start();self.addCleanup(p.stop)
        p=patch.object(s,"_settings",return_value=frappe._dict(mapping_version="v1"));p.start();self.addCleanup(p.stop)
        self.period_version = s._period_version(s._settings())
        self.filters={"month":"2026-11","basis":"period","currency":"MXN","status":"approved"}
        fixtures=[
            {"name":"a1","source":"shared","amount":10},
            {"name":"a2","source":"shared","amount":20,"category":"tax"},
            {"name":"forbidden","company":"B","amount":900},
            {"name":"usd","currency":"USD","amount":500},
            {"name":"rejected","approval_status":"rejected","amount":40},
            {"name":"running","approval_status":"pending","amount":60},
            {"name":"missing_period","period_month":None,"review_state":"needs_review","issues":'["period"]',"amount":200},
            {"name":"stale_mapping","mapping_version":"old","amount":99},
            {"name":"inactive","is_active":0,"amount":800},
            {"name":"october","period_month":"2026-10-01","request_date":"2026-10-03","amount":7},
        ]
        for fixture in fixtures:
            row={"name":fixture['name'],"source":fixture['name'],"company":"A","approval_type":"expense","is_active":1,"currency":"MXN","approval_status":"approved","period_month":"2026-11-01","request_date":"2026-11-05","planned_payment_date":"2026-12-01","review_state":"confirmed","issues":"[]","company_basis":"mapping","mapping_version":"v1","period_version":self.period_version,"category":"personnel","subcategory":"salary",**fixture}
            row.setdefault('finance_approved_date', row['period_month'])
            self.db.execute('INSERT INTO "tabMexico Expense Line" ('+','.join('"'+c+'"' for c in row)+') VALUES ('+','.join('?' for _ in row)+')',list(row.values()))

    def test_excluded_line_changes_only_its_share_of_a_mixed_approval(self):
        self.db.execute("UPDATE \"tabMexico Expense Line\" SET expense_scope='non_mx', review_state='excluded' WHERE name='a2'")
        values = {**self.filters, "basis":"request"}
        visible = {r["name"] for r in s._query(values, ["name"]).run(as_dict=True)}
        self.assertIn("a1", visible)
        self.assertNotIn("a2", visible)
        self.assertEqual(s._aggregates(values, confirmed=True)[0]["amount"], 10)
        excluded = s._query({**values,"expense_scope":"non_mx"}, ["name","amount"], review=True).run(as_dict=True)
        self.assertEqual([(r["name"],r["amount"]) for r in excluded], [("a2",20)])
        self.assertNotIn("a2", {r["name"] for r in s._query(values,["name"],review=True).run(as_dict=True)})
        self.assertEqual(self.db.execute('SELECT SUM(amount) FROM "tabMexico Expense Line" WHERE source=?', ('shared',)).fetchone()[0],30)

    def test_obsolete_exclusion_rule_returns_to_pending_until_reprojected(self):
        self.db.execute("UPDATE \"tabMexico Expense Line\" SET expense_scope='non_mx',review_state='excluded',mapping_version='old' WHERE name='a2'")
        pending = s._query(self.filters,["name"],review=True).run(as_dict=True)
        self.assertIn("a2", {r["name"] for r in pending})
        self.assertEqual(s._query({**self.filters,"expense_scope":"non_mx"},["name"],review=True).run(as_dict=True), [])

    def test_management_totals_match_report_and_preserve_company_permissions(self):
        values = {**self.filters, "status":"active"}
        with patch.object(s, "_finance"):
            result = s.get_report_insights(values, "company")
            columns, rows = s.report_data(values, "company")
        self.assertEqual(result["total"], format(sum(r["total"] for r in rows), ".2f"))
        self.assertEqual(result["company_count"], 1)
        self.assertEqual(result["companies"][0]["key"], "A")
        self.assertEqual(result["included"]["applications"], 2)
        self.assertEqual(result["pending"]["applications"], 2)

    def test_known_review_amounts_exclude_invalid_amounts_and_confirmed_drilldown(self):
        self.db.execute('UPDATE "tabMexico Expense Line" SET base_issues=? WHERE name=?', ('["amount"]','missing_period'))
        result = s._aggregates({**self.filters,"status":"active"}, review=True)[0]
        self.assertEqual(str(result["known_amount"]), "99")
        self.assertEqual(result["invalid_amounts"], 1)
        rows = s._aggregates({**self.filters,"status":"active","basis":"request","confirmed_only":"1"})
        self.assertEqual(rows[0]["amount"], 90)

    def test_review_known_amount_requires_an_unambiguous_currency_in_both_modes(self):
        for currency, issues in ((None, '[]'), ('', '[]'), (None, '["currency"]'), ('MXN', '["currency"]')):
            self.db.execute('UPDATE "tabMexico Expense Line" SET currency=?, base_issues=? WHERE name=?',
                            (currency, issues, 'missing_period'))
            for extra in ({}, {"reporting_currency": "MXN"}):
                with self.subTest(currency=currency, issues=issues, filters=extra):
                    row = s._aggregates({**self.filters, "status": "active", **extra}, review=True)[0]
                    self.assertEqual(row["lines"], 2)
                    self.assertEqual(float(row["known_amount"]), 99)
                    self.assertEqual(row["invalid_amounts"], 1)
        self.db.execute('UPDATE "tabMexico Expense Line" SET currency=?, base_issues=? WHERE name=?',
                        ('MXN', '[]', 'missing_period'))
        for extra in ({}, {"reporting_currency": "MXN"}):
            row = s._aggregates({**self.filters, "status": "active", **extra}, review=True)[0]
            self.assertEqual(float(row["known_amount"]), 299)
            self.assertEqual(row["invalid_amounts"], 0)

    def test_legacy_management_api_does_not_treat_missing_currency_as_mxn(self):
        self.db.execute('UPDATE "tabMexico Expense Line" SET currency=NULL, base_issues=? WHERE name=?',
                        ('["currency"]', 'missing_period'))
        with patch.object(s, "_finance"):
            result = s.get_report_insights({**self.filters, "status": "active"}, "company")
        self.assertEqual(result["pending"]["known_amount"], "99.00")
        self.assertEqual(result["pending"]["unknown_lines"], 1)
        self.assertEqual(result["pending"]["applications"], 2)

    def test_unsupported_currency_remains_unconvertible_in_review(self):
        self.db.execute('UPDATE "tabMexico Expense Line" SET currency=?, base_issues=? WHERE name=?',
                        ('EUR', '[]', 'missing_period'))
        row = s._aggregates({**self.filters, "status": "active", "reporting_currency": "MXN"}, review=True)[0]
        self.assertEqual(float(row["known_amount"]), 99)
        self.assertEqual(row["invalid_amounts"], 1)

    def test_source_classification_subtree_and_exact_scope_keep_company_permissions(self):
        parent = 'a' * 32
        child = parent + '/' + 'b' * 32
        self.db.execute('UPDATE "tabMexico Expense Line" SET classification_key=? WHERE name IN (?,?)', (parent,'a1','forbidden'))
        self.db.execute('UPDATE "tabMexico Expense Line" SET classification_key=? WHERE name=?', (child,'a2'))
        subtree = {**self.filters, 'classification_key':parent}
        self.db.execute('UPDATE "tabMexico Expense Line" SET classification_key=? WHERE name=?', (parent,'missing_period'))
        self.assertEqual(s._aggregates(subtree,confirmed=True)[0]['amount'],30)
        self.assertEqual(s._aggregates({**subtree,'basis':'request'})[0]['amount'],30)
        self.assertEqual(s._aggregates({**subtree,'classification_exact':'1'},confirmed=True)[0]['amount'],10)
        self.assertEqual(s._aggregates({**subtree,'classification_key':child},confirmed=True)[0]['amount'],20)

    def test_approval_form_scope_matches_corporation_and_template_not_expense_category(self):
        self.db.executemany('INSERT INTO "tabMexico Expense Source" VALUES (?,?,?)', [
            ("shared", s.contract.CORP_ID, s.contract.PROCESS_CODE),
            ("stale_mapping", "another-corp", s.contract.PROCESS_CODE),
            ("running", s.contract.CORP_ID, "another-template"),
            ("forbidden", s.contract.CORP_ID, s.contract.PROCESS_CODE),
        ])
        rows=s._aggregates({**self.filters,"approval_type":"expense","status":"active"})
        self.assertEqual(rows[0]["amount"],30)
        self.assertEqual(rows[0]["applications"],1)
        self.assertEqual(rows[0]["lines"],2)
        # Both personnel and tax lines belong to the same expense approval form.
        self.assertEqual(s._aggregates({**self.filters,"approval_type":"expense","category":"tax"})[0]["amount"],20)
        for kind in ("operating","purchase"):
            for flags in ({}, {"confirmed":True}, {"review":True}):
                self.assertEqual(s._aggregates({**self.filters,"approval_type":kind}, **flags)[0]["applications"],0)

    def test_report_excludes_other_companies_currencies_statuses_inactive_and_unconfirmed(self):
        rows=s._aggregates(self.filters,confirmed=True)
        self.assertEqual(rows[0]['amount'],30)
        self.assertEqual(rows[0]['applications'],1)
        self.assertEqual(rows[0]['lines'],2)

    def test_company_grouping_does_not_sum_per_category_application_counts(self):
        rows=s._aggregates(self.filters,("company",),confirmed=True)
        self.assertEqual(rows[0]['company'],'A')
        self.assertEqual(rows[0]['applications'],1)

    def test_review_includes_missing_period_by_application_date_and_stale_mapping(self):
        rows=s._query(self.filters,["name"],review=True).run(as_dict=True)
        self.assertEqual({r.name for r in rows},{'missing_period','stale_mapping'})

    def test_period_missing_does_not_appear_as_confirmed_november_expense(self):
        rows=s._query(self.filters,["name"],confirmed=True).run(as_dict=True)
        self.assertNotIn('missing_period',{r.name for r in rows})

    def test_request_and_planned_date_bases_are_separate(self):
        rows=s._aggregates({**self.filters,"month":"2026-12","basis":"planned"},confirmed=True)
        self.assertEqual(rows[0]['amount'],37)
        rows=s._aggregates({**self.filters,"month":"2026-12","basis":"request"},confirmed=True)
        self.assertEqual(rows[0]['lines'],0)

    def test_active_status_contains_pending_and_approved_only(self):
        rows=s._aggregates({**self.filters,"status":"active"},confirmed=True)
        self.assertEqual(rows[0]['amount'],90)
        self.assertEqual(rows[0]['applications'],2)

    def test_finance_approved_report_includes_running_flows_but_excludes_rejected_and_other_companies(self):
        self.db.execute("UPDATE \"tabMexico Expense Line\" SET finance_approved_date='2026-11-05' WHERE name IN ('a1','a2','running','rejected','forbidden','usd')")
        filters = {**self.filters, "status": "finance_approved"}
        rows = s._aggregates(filters, confirmed=True)
        self.assertEqual(rows[0]['amount'], 90)
        self.assertEqual(rows[0]['applications'], 2)
        with patch.object(s, '_finance'):
            _, data = s.report_data(filters, 'company')
        self.assertEqual(sum(row['total'] for row in data), 90)

    def test_cross_month_range_retains_company_scope(self):
        rows=s._aggregates({**self.filters,"from_date":"2026-10-01","to_date":"2026-11-30"},confirmed=True)
        self.assertEqual(rows[0]['amount'],37)

    def test_review_issue_filter_does_not_expand_scope(self):
        rows=s._query({**self.filters,"issue":"period"},["name"],review=True).run(as_dict=True)
        self.assertEqual([r.name for r in rows],['missing_period'])

    def test_category_and_subcategory_filters_remain_intersected(self):
        rows=s._aggregates({**self.filters,"category":"tax","subcategory":"salary"},confirmed=True)
        self.assertEqual(rows[0]['amount'],20)

    def test_aggregate_flags_unparseable_amounts_instead_of_claiming_a_complete_total(self):
        self.db.execute('''INSERT INTO "tabMexico Expense Line"
            (name,source,company,is_active,currency,approval_status,period_month,request_date,review_state,base_issues,amount,mapping_version,company_basis)
            VALUES ('bad','bad','A',1,'MXN','approved','2026-11-01','2026-11-05','needs_review','["amount"]',0,'v1','mapping')''')
        self.db.execute('UPDATE "tabMexico Expense Line" SET period_version=?, finance_approved_date=? WHERE name=?', (self.period_version, '2026-11-05', 'bad'))
        rows=s._aggregates(self.filters)
        self.assertEqual(rows[0]['invalid_amounts'],1)

    def test_timezone_change_blocks_old_and_new_month_reports_until_every_row_is_recalculated(self):
        settings = frappe._dict(mapping_version="v1", business_timezone="America/Tijuana")
        def throw(message):
            raise frappe.ValidationError(message)
        with patch.object(s, "_settings", return_value=settings), patch.object(s, "_", side_effect=lambda x:x), patch.object(frappe, "throw", side_effect=throw):
            for month in ("2026-10", "2026-11"):
                for basis in ("period", "request", "planned"):
                    with self.subTest(month=month, basis=basis), self.assertRaisesRegex(frappe.ValidationError, "recalculated"):
                        s._aggregates({**self.filters, "month":month, "basis":basis}, confirmed=True)
            new_version = s._period_version(settings)
            self.db.execute('UPDATE "tabMexico Expense Line" SET period_version=? WHERE name=?', (new_version, 'a1'))
            with self.assertRaises(frappe.ValidationError):
                s._aggregates(self.filters, confirmed=True)
            self.db.execute('UPDATE "tabMexico Expense Line" SET period_version=?', (new_version,))
            self.assertEqual(s._aggregates(self.filters, confirmed=True)[0]['amount'], 30)

    def test_stale_periods_use_request_month_in_review_and_have_their_own_issue_filter(self):
        self.db.execute('UPDATE "tabMexico Expense Line" SET period_version=NULL, period_month=?, company_basis=? WHERE name=?', ('2026-10-01', 'manual', 'a1'))
        rows=s._query({**self.filters, "issue":"period_stale"}, ["name"], review=True).run(as_dict=True)
        self.assertEqual([r.name for r in rows], ['a1'])

    def test_unrelated_stale_rows_do_not_block_authorized_currency_or_company_scope(self):
        self.db.execute('UPDATE "tabMexico Expense Line" SET period_version=NULL WHERE name IN (?,?,?)', ('forbidden','usd','inactive'))
        self.assertEqual(s._aggregates(self.filters, confirmed=True)[0]['amount'], 30)

    def test_single_day_uses_finance_approval_date_with_inclusive_boundaries(self):
        self.db.execute('UPDATE "tabMexico Expense Line" SET finance_approved_date=? WHERE name=?', ('2026-11-12','a1'))
        self.db.execute('UPDATE "tabMexico Expense Line" SET finance_approved_date=? WHERE name=?', ('2026-11-13','a2'))
        filters={**self.filters,'from_date':'2026-11-12','to_date':'2026-11-12'}
        rows=s._query(filters,['name'],confirmed=True).run(as_dict=True)
        self.assertEqual([r.name for r in rows],['a1'])
        # All rows still have November as period_month; day filtering must ignore it.
        self.assertEqual(s._aggregates(filters,confirmed=True)[0]['amount'],10)
        with patch.object(s,'_finance'):
            for kind in ('company','payroll','trend'):
                _, report=s.report_data(filters,kind)
                self.assertEqual(sum(r['total'] for r in report),10)

    def test_date_basis_and_review_fallback_use_exact_days(self):
        filters={**self.filters,'from_date':'2026-11-05','to_date':'2026-11-05'}
        self.assertEqual(s._aggregates({**filters,'basis':'request'},confirmed=True)[0]['amount'],30)
        self.assertEqual(s._aggregates(filters,confirmed=True)[0]['lines'],0)
        rows=s._query({**filters,'issue':'period'},['name'],review=True).run(as_dict=True)
        self.assertEqual([r.name for r in rows],['missing_period'])

    def test_cross_month_trend_does_not_include_days_outside_range(self):
        self.db.execute('UPDATE "tabMexico Expense Line" SET finance_approved_date=? WHERE name=?', ('2026-11-12','a1'))
        self.db.execute('UPDATE "tabMexico Expense Line" SET finance_approved_date=? WHERE name=?', ('2026-11-13','a2'))
        filters={**self.filters,'from_date':'2026-10-01','to_date':'2026-11-12'}
        with patch.object(s,'_finance'):
            _, rows=s.report_data(filters,'trend')
        self.assertEqual([(r['month'],r['total']) for r in rows],[('2026-10',7),('2026-11',10)])

    def test_conversion_includes_all_source_currencies_and_preserves_permissions(self):
        from decimal import Decimal
        filters={**self.filters,'reporting_currency':'CNY','currency':'CNY'}
        with patch.object(s.fx,'load_month_rate',side_effect=lambda code,day:Decimal('7') if code=='USD' else Decimal('.35')) as loader:
            result=s._aggregates(filters,confirmed=True)[0]
            self.assertEqual(Decimal(str(result['amount'])),Decimal('3510.50'))
            self.assertEqual(result['applications'],2)
            self.assertEqual(result['lines'],3)
            self.assertEqual(loader.call_count,2)
            with patch.object(s,'_finance'):
                _,rows=s.report_data(filters,'company')
                self.assertEqual(sum(r['total'] for r in rows),Decimal('3510.50'))
                self.assertTrue(all(r['currency']=='CNY' for r in rows))

    def test_cross_month_conversion_uses_finance_date_despite_request_filter(self):
        from decimal import Decimal
        self.db.execute('UPDATE "tabMexico Expense Line" SET finance_approved_date=? WHERE name=?',('2026-10-15','a1'))
        self.db.execute('UPDATE "tabMexico Expense Line" SET finance_approved_date=NULL WHERE name=?',('running',))
        filters={**self.filters,'basis':'request','status':'active','reporting_currency':'CNY','currency':'CNY'}
        def rate(code,day):
            return Decimal('7') if code=='USD' else Decimal('.3') if day=='2026-10-01' else Decimal('.35')
        with patch.object(s.fx,'load_month_rate',side_effect=rate):
            converter=s.fx.Conversion('CNY')
            result=s._aggregates(filters,conversion=converter,confirmed=True)[0]
            self.assertEqual(Decimal(str(result['amount'])),Decimal('3531'))
            self.assertEqual(result['estimated_lines'],1)
            raw=s._query(filters,['name','currency','amount','finance_approved_date','request_date'],confirmed=True).run(as_dict=True)
            lines=[converter.line(row) for row in raw]
            self.assertEqual(sum(Decimal(row['converted_amount']) for row in lines),Decimal(str(result['amount'])))
            pending=next(row for row in lines if row['name']=='running')
            self.assertEqual(pending['exchange_rate_date'],'2026-11-01')
            self.assertTrue(pending['fx_estimated'])

    def test_conversion_rounds_each_line_before_grouping_and_fails_on_rate_error(self):
        from decimal import Decimal
        self.db.execute('UPDATE "tabMexico Expense Line" SET amount=.10 WHERE name IN (\'a1\',\'a2\')')
        filters={**self.filters,'reporting_currency':'CNY','currency':'CNY','category':'personnel'}
        with patch.object(s.fx,'load_month_rate',return_value=Decimal('1.05')):
            # a1 and USD row; a2 is excluded by category.
            result=s._aggregates(filters,confirmed=True)[0]
            self.assertEqual(Decimal(str(result['amount'])),Decimal('525.11'))
        with patch.object(s.fx,'load_month_rate',side_effect=ValueError('rate unavailable')):
            with self.assertRaisesRegex(ValueError,'rate unavailable'):
                s._aggregates(filters,confirmed=True)
