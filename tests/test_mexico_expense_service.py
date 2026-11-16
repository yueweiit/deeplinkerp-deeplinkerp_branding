"""Authorization, aggregate arithmetic, optimistic review and transactional sync."""
from contextlib import nullcontext
from decimal import Decimal
from types import SimpleNamespace
import json
import unittest
from unittest.mock import MagicMock, patch
from pypika import Table

import frappe
from deeplinkerp_branding.services import mexico_expenses as s
from deeplinkerp_branding.services import operating_oa_http as http


class Doc(frappe._dict):
    def update(self, values=None, **kwargs):
        super().update(values or {}, **kwargs)
        return self


PERIOD_VERSION = s._period_version(Doc(business_timezone="America/Mexico_City"))


class MexicoServiceTests(unittest.TestCase):
    def setUp(self):
        def throw(message, exc=frappe.ValidationError, **kwargs):
            raise exc(message)
        for target, replacement in [("_",lambda text:text),("_settings",lambda lock=False:Doc(mapping_version="v1",business_timezone="America/Mexico_City"))]:
            patcher=patch.object(s,target,replacement);patcher.start();self.addCleanup(patcher.stop)
        patcher=patch.object(frappe,"throw",side_effect=throw);patcher.start();self.addCleanup(patcher.stop)
        for target in ("deeplinkerp_branding.services.operating_expense_contract._", "deeplinkerp_branding.services.operating_oa_http._"):
            patcher=patch(target,side_effect=lambda text:text);patcher.start();self.addCleanup(patcher.stop)

    def filters(self):
        return {"month":"2026-11","basis":"period","currency":"MXN","status":"approved"}

    def test_approval_form_filter_uses_stable_codes_and_preserves_category(self):
        for code in ("operating", "expense", "purchase", ""):
            self.assertEqual(s._filters({"approval_type":code,"category":"tax"})["approval_type"],code)
        with self.assertRaises(frappe.ValidationError):
            s._filters({"approval_type":"unknown"})

    def test_date_range_validation_and_legacy_month_conversion(self):
        self.assertEqual(s._filters({'month':'2024-02'})['to_date'], '2024-02-29')
        exact=s._filters({'from_date':'2026-09-30','to_date':'2026-10-01'})
        self.assertEqual((exact['from_date'],exact['to_date']),('2026-09-30','2026-10-01'))
        for values in ({'from_date':'2026-11-10'}, {'to_date':'2026-11-10'},
                       {'from_date':'2026-11-11','to_date':'2026-11-10'},
                       {'from_date':'2026-02-29','to_date':'2026-03-01'},
                       {'from_date':'20261101','to_date':'2026-11-02'}):
            with self.subTest(values=values), self.assertRaises(frappe.ValidationError):
                s._filters(values)

    def test_sync_toggle_does_not_cancel_queued_or_paginated_reprojection(self):
        settings = Doc(enabled=0, business_timezone="America/Mexico_City", mapping_version="old",
                       config_version="old", company_mappings=[])
        jobs = []
        def enqueue(method, **kwargs):
            jobs.append((method, kwargs))
        with patch.object(s, "_settings", return_value=settings), patch.object(s, "_require_manager"), \
                patch.object(s, "_require_settings_access"), patch.object(s, "_connection"), \
                patch.object(s, "_save"), patch.object(frappe, "enqueue", side_effect=enqueue), \
                patch.object(frappe, "db", SimpleNamespace(get_value=lambda *args: '{"source":1}')), \
                patch.object(s, "_upsert") as upsert:
            s.save_settings([], settings.business_timezone, 1, "old")
            version = jobs[0][1]["projection_version"]
            s.save_settings([], settings.business_timezone, 0, settings.config_version)
            self.assertEqual(len(jobs), 1)
            with patch.object(frappe, "get_all", return_value=[str(i) for i in range(50)]):
                s.reproject_cache(version)
            self.assertEqual(upsert.call_count, 50)
            self.assertEqual(jobs[1][1]["start"], 50)
            self.assertEqual(jobs[1][1]["projection_version"], version)
            self.assertTrue(jobs[1][1]["enqueue_after_commit"])
            s.save_settings([], settings.business_timezone, 1, settings.config_version)
            with patch.object(frappe, "get_all", return_value=["last"]):
                s.reproject_cache(version, start=50)
            self.assertEqual(upsert.call_count, 51)

    def test_new_mapping_or_timezone_supersedes_older_reprojection(self):
        for change in ({"mapping_version": "v2"}, {"business_timezone": "America/Tijuana"}):
            settings = Doc(mapping_version="v1", business_timezone="America/Mexico_City")
            version = s._projection_version(settings)
            settings.update(change)
            with self.subTest(change=change), patch.object(s, "_settings", return_value=settings), \
                    patch.object(frappe, "get_all") as query, patch.object(s, "_upsert") as upsert:
                s.reproject_cache(version)
                query.assert_not_called()
                upsert.assert_not_called()

    def test_public_batch_reads_one_fresh_mapping_version_per_call(self):
        rows = [{"amount": "1", "amount_raw": "1", "issues": "[]", "review_state": "confirmed",
                 "mapping_version": "v1", "period_version": PERIOD_VERSION, "company_basis": "mapping"}] * 100
        with patch.object(s, "_settings", side_effect=[Doc(mapping_version="v1"), Doc(mapping_version="v2")]) as settings:
            first = s._public_lines(rows)
            second = s._public_lines(rows)
        self.assertEqual(settings.call_count, 2)
        self.assertTrue(all(row["review_state"] == "confirmed" for row in first))
        self.assertTrue(all(row["review_state"] == "needs_review" and "stale" in row["issues"] for row in second))
        self.assertEqual(rows[0]["issues"], "[]")

    def test_list_serialization_does_not_read_settings_for_each_row(self):
        rows = [Doc(amount="1", amount_raw="1", issues="[]", mapping_version="v1")] * 100
        query = MagicMock()
        query.orderby.return_value = query
        query.limit.return_value = query
        query.offset.return_value = query
        query.run.return_value = rows
        with patch.object(s, "_finance"), patch.object(s, "_filters", return_value=self.filters()), \
                patch.object(s, "_aggregates", return_value=[{"lines":100,"applications":1}]), \
                patch.object(s, "_query", return_value=query), \
                patch.object(frappe, "qb", SimpleNamespace(DocType=lambda name: Table("tab" + name))), \
                patch.object(s, "_settings", return_value=Doc(mapping_version="v1")) as settings:
            result = s.get_lines(page_length=100)
        self.assertEqual(len(result["rows"]), 100)
        settings.assert_called_once()

    def test_detail_shares_mapping_version_between_selected_line_and_siblings(self):
        row = Doc(name="a" * 64, source="source", amount="1", amount_raw="1", issues="[]",
                  base_issues="[]", mapping_version="v1", company_basis="mapping")
        with patch.object(s, "_finance"), patch.object(s, "_line", return_value=row), \
                patch.object(frappe, "get_doc"), patch.object(s, "_whole_source_allowed", return_value=False), \
                patch.object(frappe, "get_list", return_value=[row] * 100), \
                patch.object(s, "_settings", return_value=Doc(mapping_version="v2")) as settings:
            result = s.get_detail(row.name)
        self.assertEqual(result["visible_amount"], "100")
        self.assertEqual(result["line"]["review_state"], "needs_review")
        self.assertTrue(all(line["review_state"] == "needs_review" for line in result["lines"]))
        settings.assert_called_once()

    def test_mutating_apis_require_post_and_never_allow_guest(self):
        for method in (s.save_settings,s.enqueue_sync,s.review_line):
            self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[method],["POST"])
            self.assertNotIn(method,frappe.guest_methods)

    def test_generic_document_writes_require_managed_context(self):
        with self.assertRaises(frappe.PermissionError):s.validate_managed_document(Doc())
        with s.managed_write():s.validate_managed_document(Doc())

    def test_detail_excel_exports_numeric_money_and_neutralizes_source_formulas(self):
        from io import BytesIO
        from openpyxl import load_workbook
        from frappe.utils import xlsxutils
        captured=[]
        row=Doc(name="a"*64,approval_no="=1+1",company="A",source_entity="A",category="personnel",subcategory="salary",amount="12.30",amount_raw="12.30",currency="MXN",approval_status="approved",issues="[]",mapping_version="v1")
        query=MagicMock();query.limit.return_value=query;query.run.return_value=[row] * 100
        with patch.object(s,"_finance"),patch.object(s,"_query",return_value=query),patch.object(frappe.permissions,"can_export",return_value=True),patch.object(xlsxutils,"build_xlsx_response",side_effect=lambda matrix,name:captured.append(matrix)),patch.object(xlsxutils.XLSXStyleBuilder,"get_datetime_format",return_value="yyyy-mm-dd"),patch.object(s,"_settings",return_value=Doc(mapping_version="v1",business_timezone="America/Mexico_City")) as settings:
            s.export_report({**self.filters(), "approval_type":"expense"},"details")
            workbook=load_workbook(BytesIO(xlsxutils.make_xlsx(captured[0],"QA").getvalue()))
        # One read for filter defaults and one for the entire output batch.
        self.assertEqual(settings.call_count, 2)
        sheet=workbook.active
        self.assertEqual(sheet['A8'].data_type,'s')
        self.assertEqual(sheet['A8'].value,"'=1+1")
        self.assertEqual(sheet['K8'].data_type,'n')
        self.assertEqual(sheet['K8'].value,12.3)
        self.assertEqual(sheet['B2'].value, '2026-11-01 ~ 2026-11-30')
        self.assertEqual(sheet['D4'].value, 'Expense Requests')

    def test_unchanged_projection_does_not_update_modified(self):
        doc=Doc(amount=Decimal("1.000000000"),period_month="2026-11-01",company=None)
        doc.is_new=lambda:False
        with patch.object(s,"_save") as save:
            s._store_projection(doc,{"amount":"1.00","period_month":"2026-11-01","company":""})
            save.assert_not_called()
            s._store_projection(doc,{"amount":"2.00"})
            save.assert_called_once_with(doc)

    def test_new_projection_keeps_empty_links_instead_of_user_defaults(self):
        doc=Doc(company='Default company',currency='USD')
        doc.is_new=lambda:True
        with patch.object(s,'_save'):
            s._store_projection(doc,{'company':None,'currency':None})
        self.assertEqual(doc.company,'')
        self.assertEqual(doc.currency,'')

    def test_full_source_access_reuses_one_company_scope_and_blocks_stale_mapping(self):
        docs=[Doc(company="A",is_active=1,company_basis="mapping",mapping_version="v1"),Doc(company="A",is_active=1,company_basis="mapping",mapping_version="old")]
        with patch.object(frappe,"get_all",return_value=docs),patch.object(s,"_companies",return_value=["A"]) as companies,patch.object(s,"_manager",return_value=True):
            self.assertFalse(s._whole_source_allowed("source"))
            companies.assert_called_once()

    def test_retiring_another_company_line_does_not_expose_shared_approval_attachments(self):
        docs=[Doc(company="A",is_active=1,company_basis="manual"),Doc(company="B",is_active=0,company_basis="mapping",mapping_version="old")]
        with patch.object(frappe,"get_all",return_value=docs),patch.object(s,"_companies",return_value=["A"]),patch.object(s,"_manager",return_value=True):
            self.assertFalse(s._whole_source_allowed("source"))
            self.assertFalse(s.source_has_permission(Doc(name="source"),user="manager"))

    def test_restricted_system_manager_cannot_read_or_replace_global_company_mappings(self):
        settings=Doc(company_mappings=[Doc(company="B",source_entity="Private B",entity_kind="entity")])
        with patch.object(s,"_manager",return_value=True),patch.object(s,"_companies",return_value=["A"]),patch.object(s,"_settings",return_value=settings),patch.object(s,"_finance"):
            result=s.get_context()
            self.assertFalse(result["can_manage"])
            self.assertTrue(result["can_assign_company"])
            self.assertNotIn("settings",result)
            self.assertFalse(s.settings_has_permission(settings,user="manager"))
            with self.assertRaises(frappe.PermissionError):s._require_settings_access(settings)

    def test_write_permission_is_denied_even_to_administrators(self):
        for kind in ("write","create","delete","submit","cancel"):
            self.assertFalse(s.has_permission(Doc(name="line",company="Company"),ptype=kind))

    def test_unmapped_and_stale_lines_do_not_bypass_company_permissions(self):
        with patch.object(s,"_manager",return_value=False),patch.object(s,"_companies",return_value=["A"]):
            for doc in [Doc(name="line",company=None),Doc(name="line",company="B"),Doc(name="line",company="A",company_basis="mapping",mapping_version="v0")]:
                self.assertFalse(s.has_permission(doc,user="finance",ptype="read"))
            self.assertTrue(s.has_permission(Doc(name="line",company="A",company_basis="mapping",mapping_version="v1"),user="finance",ptype="read"))

    def test_permission_query_includes_company_and_mapping_version(self):
        with patch.object(s,"_manager",return_value=False),patch.object(s,"_companies",return_value=["A"]),patch.object(frappe,"db",SimpleNamespace(escape=lambda value:"'"+value+"'")):
            condition=s.permission_query_conditions("finance")
            self.assertIn("company IN ('A')",condition)
            self.assertIn("mapping_version = 'v1'",condition)
        with patch.object(s,"_manager",return_value=False),patch.object(s,"_companies",return_value=[]):
            self.assertIn("1=0",s.permission_query_conditions("finance"))

    def test_source_permission_checks_all_subsidiaries(self):
        with patch.object(s,"_manager",return_value=True),patch.object(s,"_companies",return_value=["A"]),patch.object(frappe,"get_all",return_value=[Doc(company="A"),Doc(company="B")]):
            self.assertFalse(s.source_has_permission(Doc(name="source"),user="manager"))

    def test_malformed_and_unknown_filters_are_rejected(self):
        for values in ([1],{"sql":"x"},{"month":"2026-13"},{"status":[]},{"currency":"EUR"},{"issue":"unknown"},{"keyword":"x"*201}):
            with self.subTest(values=values),self.assertRaises(frappe.ValidationError):s._filters(values)

    def test_explicit_company_filter_is_authorized_before_query(self):
        with patch.object(s,"_company_access",side_effect=frappe.PermissionError),self.assertRaises(frappe.PermissionError):
            s._filters({**self.filters(),"company":"Forbidden"})

    def test_invalid_amount_does_not_display_as_zero(self):
        self.assertIsNone(s._public_line({"amount":0,"amount_raw":"","issues":"[]","mapping_version":"v1"}, "v1", PERIOD_VERSION)["amount"])

    def test_mapping_change_is_visible_as_pending_without_waiting_for_reprojection(self):
        result=s._public_line({"amount":1,"amount_raw":"1","review_state":"confirmed","issues":"[]","mapping_version":"old","company_basis":"mapping"}, "v1", PERIOD_VERSION)
        self.assertEqual(result["review_state"],"needs_review")
        self.assertIn("stale",result["issues"])

    def test_report_uses_exact_decimal_and_confirmed_scope(self):
        rows=[{"company":"A","category":"personnel","amount":Decimal("0.10")},{"company":"A","category":"tax","amount":Decimal("0.20")}]
        with patch.object(s,"_finance"),patch.object(s,"_aggregates",return_value=rows) as aggregates:
            columns,data=s.report_data({**self.filters(),"approval_type":"expense"},"company")
        self.assertEqual(data[0]["total"],Decimal("0.30"))
        aggregates.assert_called_once_with({**self.filters(),"approval_type":"expense","from_date":"2026-11-01","to_date":"2026-11-30"},("company","category"),confirmed=True)
        self.assertTrue(all(col.get("options")=="currency" for col in columns if col["fieldtype"]=="Currency"))

    def test_payroll_report_forces_expense_period_and_keeps_salary_separate(self):
        rows=[{"company":"A","period_month":"2026-11-01","subcategory":"salary","amount":"100"},{"company":"A","period_month":"2026-11-01","subcategory":"social","amount":"20"}]
        with patch.object(s,"_finance"),patch.object(s,"_aggregates",return_value=rows) as aggregates:
            _,data=s.report_data({**self.filters(),"basis":"request"},"payroll")
        self.assertEqual(data[0]["salary"],100)
        self.assertEqual(data[0]["total"],120)
        self.assertEqual(aggregates.call_args.args[0]["basis"],"period")
        self.assertEqual(aggregates.call_args.args[0]["category"],"personnel")

    def test_trend_report_groups_daily_query_results_by_month(self):
        rows=[{"request_date":"2026-11-01","category":"utilities","amount":"1.01"},{"request_date":"2026-11-15","category":"utilities","amount":"2.02"}]
        with patch.object(s,"_finance"),patch.object(s,"_aggregates",return_value=rows):
            _,data=s.report_data({**self.filters(),"basis":"request","approval_type":"expense"},"trend")
        self.assertEqual(len(data),1)
        self.assertEqual(data[0]["total"],Decimal("3.03"))

    def test_partial_source_detail_omits_full_workflow_and_header_values(self):
        line=Doc(name="a"*64,source="source",is_active=1,company="A",amount="10",amount_raw="10",issues="[]",mapping_version="v1")
        with patch.object(s,"_finance"),patch.object(s,"_line",return_value=line),patch.object(s,"_whole_source_allowed",return_value=False),patch.object(frappe,"get_doc",return_value=Doc(raw_json="DO NOT READ",payee="Other subsidiary")),patch.object(frappe,"get_list",return_value=[line]):
            result=s.get_detail("a"*64)
        self.assertFalse(result["whole_source"])
        self.assertNotIn("workflow",result)
        self.assertNotIn("payee",result)
        self.assertEqual(result["visible_amount"],"10")

    def test_attachment_access_fails_before_any_remote_read_for_partial_source(self):
        with patch.object(s,"_finance"),patch.object(s,"_line",return_value=Doc(source="source")),patch.object(s,"_whole_source_allowed",return_value=False),patch.object(http,"read_page") as read:
            with self.assertRaises(frappe.PermissionError):s._attachment_source("a"*64)
        read.assert_not_called()

    def test_review_rejects_source_and_document_version_conflicts(self):
        line=Doc(period_version=PERIOD_VERSION,source_version="new",modified="new",company="A")
        with patch.object(s,"_finance"),patch.object(s,"_manager",return_value=True),patch.object(s,"_line",return_value=line),patch.object(frappe,"db",SimpleNamespace(get_value=lambda *a:"source")),patch.object(frappe,"get_doc"),patch.object(s,"_save") as save:
            for source_version,modified in [("old","new"),("new","old")]:
                with self.assertRaises(frappe.ValidationError):s.review_line("a"*64,source_version,modified,"2026-11","2026-11-01","2026-11-30",note="Review")
            save.assert_not_called()

    def test_account_manager_cannot_reassign_company(self):
        line=Doc(period_version=PERIOD_VERSION,source_version="v",modified="v",company="A")
        with patch.object(s,"_finance"),patch.object(s,"_manager",return_value=False),patch.object(frappe,"get_roles",return_value=["Accounts Manager"]),patch.object(s,"_line",return_value=line),patch.object(frappe,"db",SimpleNamespace(get_value=lambda *a:"source")),patch.object(frappe,"get_doc"):
            with self.assertRaises(frappe.PermissionError):s.review_line("a"*64,"v","v","2026-11","2026-11-01","2026-11-30",company="B",note="Review")

    def test_review_does_not_clear_source_amount_or_allocation_issues(self):
        line=Doc(period_version=PERIOD_VERSION,source_version="v",modified="v",company="A",company_basis="manual",row_fingerprint="economic",base_issues='["amount"]')
        with patch.object(s,"_finance"),patch.object(s,"_manager",return_value=True),patch.object(s,"_line",return_value=line),patch.object(frappe,"db",SimpleNamespace(get_value=lambda *a:"source")),patch.object(frappe,"get_doc",return_value=Doc(corp_id=s.contract.CORP_ID,process_code=s.contract.PROCESS_CODE,raw_json=json.dumps({"status":"RUNNING","raw_payload":{"tasks":[{"activityId":s.contract.FINANCE_ACTIVITY_ID,"taskId":"finance-task","status":"COMPLETED","result":"AGREE","finishTime":"2026-11-16T12:00Z"}]}}))),patch.object(s,"_save"),patch.object(s,"now_datetime",return_value="now"),patch.object(frappe,"session",SimpleNamespace(user="manager")):
            result=s.review_line("a"*64,"v","v","2026-11","2026-11-01","2026-11-30",note="Reviewed")
        self.assertEqual(result["review_state"],"needs_review")
        self.assertIn("amount",json.loads(line.issues))

    def test_review_cannot_manually_supply_period_without_finance_approval(self):
        line = Doc(period_version=PERIOD_VERSION,source_version="v", modified="v", company="A", company_basis="manual",
                   row_fingerprint="facts", base_issues="[]")
        source = Doc(corp_id=s.contract.CORP_ID,process_code=s.contract.PROCESS_CODE,raw_json=json.dumps({"status":"RUNNING", "raw_payload":{}}))
        with patch.object(s, "_finance"), patch.object(s, "_manager", return_value=True), \
                patch.object(s, "_line", return_value=line), patch.object(frappe, "get_doc", return_value=source), \
                patch.object(frappe, "db", SimpleNamespace(get_value=lambda *a: "source")), \
                patch.object(s, "_save") as save:
            with self.assertRaisesRegex(frappe.ValidationError, "cannot be entered manually"):
                s.review_line("a" * 64, "v", "v", "2026-11", "2026-11-01", "2026-11-30", note="Manual attempt")
            save.assert_not_called()
            with patch.object(s, "now_datetime", return_value="now"), \
                    patch.object(frappe, "session", SimpleNamespace(user="manager")):
                result = s.review_line("a" * 64, "v", "v", note="Waiting for finance")
            self.assertEqual(result['review_state'], 'needs_review')
            self.assertIn('period', json.loads(line.issues))
            self.assertIsNone(line.period_month)

    def test_sync_snapshot_mismatch_rolls_back_without_upserting_or_advancing_cursor(self):
        settings=Doc(enabled=1,templates=[Doc(**s.templates.default_templates()[3],cursor="cursor",sync_until="snapshot",page_count=1)])
        database=MagicMock()
        cache=SimpleNamespace(lock=lambda *args,**kwargs:nullcontext())
        with patch.object(s,"_settings",return_value=settings),patch.object(frappe,"db",database),patch.object(frappe,"cache",cache),patch.object(frappe,"local",SimpleNamespace(site="isolated-test")),patch.object(http,"configuration",return_value=("http://test", [s.contract.CORP_ID])),patch.object(http,"read_template_page",return_value=([{}],{"syncUntil":"other","hasMore":True,"nextCursor":"next"})),patch.object(s,"_upsert") as upsert,patch.object(s,"_save"),patch.object(frappe,"log_error"):
            result=s.sync_page(template_key=s._template_key(settings.templates[0]))
        self.assertFalse(result["ok"])
        database.rollback.assert_called_once_with(save_point="mx_sync")
        upsert.assert_not_called()
        self.assertIsNone(settings.templates[0].cursor)

    def test_sync_persists_next_cursor_only_after_all_source_rows(self):
        settings=Doc(enabled=1,templates=[Doc(**s.templates.default_templates()[3],cursor=None,sync_until=None,page_count=0)])
        order=[]
        with patch.object(s,"_settings",return_value=settings),patch.object(frappe,"db",MagicMock()),patch.object(frappe,"cache",SimpleNamespace(lock=lambda *a,**kw:nullcontext())),patch.object(frappe,"local",SimpleNamespace(site="isolated-test")),patch.object(http,"configuration",return_value=("http://test", [s.contract.CORP_ID])),patch.object(http,"read_template_page",return_value=([{"id":1},{"id":2}],{"syncUntil":"snapshot","hasMore":True,"nextCursor":"next"})),patch.object(s,"_upsert",side_effect=lambda row,settings:order.append(row["id"])),patch.object(s,"_save",side_effect=lambda doc:order.append("settings")),patch.object(s,"now_datetime",return_value="now"),patch.object(frappe,"enqueue") as enqueue:
            result=s.sync_page(template_key=s._template_key(settings.templates[0]))
        self.assertTrue(result["ok"])
        self.assertEqual(order,[1,2,"settings"])
        self.assertEqual(settings.templates[0].cursor,"next")
        self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])

    def test_invalid_cursor_restarts_full_scan_but_other_errors_preserve_cursor(self):
        for error,expected in [(http.OAInvalidCursor(),None),(ValueError(),"cursor")]:
            settings=Doc(enabled=1,templates=[Doc(**s.templates.default_templates()[3],cursor="cursor",sync_until="snapshot",page_count=1)])
            with patch.object(s,"_settings",return_value=settings),patch.object(frappe,"db",MagicMock()),patch.object(frappe,"cache",SimpleNamespace(lock=lambda *a,**kw:nullcontext())),patch.object(frappe,"local",SimpleNamespace(site="isolated-test")),patch.object(http,"configuration",return_value=("http://test", [s.contract.CORP_ID])),patch.object(http,"read_template_page",side_effect=error),patch.object(s,"_save"),patch.object(frappe,"log_error"):
                self.assertFalse(s.sync_page(template_key=s._template_key(settings.templates[0]))["ok"])
            self.assertEqual(settings.templates[0].cursor,expected)

    def test_template_reader_rejects_cross_template_rows_and_nonadvancing_cursor(self):
        with patch.object(http,"_page",return_value=([{}],{"hasMore":False})),patch.object(http,"approval_row",return_value={"corp_id":"corp","process_code":"other"}):
            with self.assertRaises(frappe.ValidationError):http.read_template_page("http://test","corp","template")
        with patch.object(http,"_page",return_value=([],{"hasMore":True,"nextCursor":"same"})),patch.object(http,"_",side_effect=lambda text:text):
            with self.assertRaises(frappe.ValidationError):http.read_template_page("http://test","corp","template",cursor="same")


class MexicoPeriodCacheTests(unittest.TestCase):
    def test_public_lines_hide_old_dates_even_for_manual_company_assignment(self):
        row={'amount':1, 'amount_raw':'1', 'issues':'[]', 'mapping_version':'v1',
             'company_basis':'manual', 'review_state':'confirmed', 'period_version':'old',
             **dict.fromkeys(s.contract.PERIOD_FIELDS, '2026-11-01')}
        result=s._public_line(row, 'v1', PERIOD_VERSION)
        self.assertEqual(result['review_state'],'needs_review')
        self.assertIn('period_stale',result['issues'])
        self.assertTrue(all(result[k] is None for k in s.contract.PERIOD_FIELDS))
        self.assertEqual(row['period_month'],'2026-11-01')

    def test_review_cannot_mark_unrecalculated_row_current(self):
        line=Doc(source_version='v', modified='v', period_version='old')
        def throw(message, *args):
            raise frappe.ValidationError(message)
        with patch.object(s,'_finance'), patch.object(s,'_manager',return_value=True), \
                patch.object(s,'_',side_effect=lambda x:x), patch.object(frappe,'throw',side_effect=throw), \
                patch.object(s,'_settings',return_value=Doc(business_timezone='America/Mexico_City')), \
                patch.object(s,'_line',return_value=line), patch.object(frappe,'get_doc'), \
                patch.object(frappe,'db',SimpleNamespace(get_value=lambda *a:'source')), patch.object(s,'_save') as save:
            with self.assertRaisesRegex(frappe.ValidationError,'recalculated'):
                s.review_line('a'*64, 'v', 'v', note='Review')
            save.assert_not_called()

    def test_period_version_is_independent_of_mapping_and_sync_toggle(self):
        settings=Doc(business_timezone='America/Mexico_City',mapping_version='a',enabled=0)
        version=s._period_version(settings)
        settings.update(mapping_version='b',enabled=1)
        self.assertEqual(s._period_version(settings),version)
        settings.business_timezone='America/Tijuana'
        self.assertNotEqual(s._period_version(settings),version)
