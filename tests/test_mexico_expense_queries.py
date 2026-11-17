"""Execute the shared query expressions in memory under a company permission scope."""
import json
import sqlite3
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from pypika import Query, Table
from pypika.queries import QueryBuilder

from deeplinkerp_branding.services import mexico_expenses as s


class MexicoQueryTests(unittest.TestCase):
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
