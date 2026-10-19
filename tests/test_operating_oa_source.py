"""Original OA applications stay authoritative; cashier evidence is supplemental."""
import copy
import importlib
import importlib.util
import sys
import unittest
from datetime import UTC, datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, call, patch


def approval(**values):
    return dict(corp_id="corp", process_instance_id="instance-1", business_id="20260101001",
        process_code="PROC-0DC5DE17-A29A-497C-8A1F-1324298A04AA", status="COMPLETED", result="agree",
        create_time=datetime(2026, 1, 1, tzinfo=timezone.utc), originator_user_id="u1", originator_user_name="Alice",
        form_component_values=[{"name": n, "value": v} for n, v in (
            ("申请类型Tipo de trámite", "付款申请Solicitud de pago"), ("执行地区Región de ejecución", "中国"),
            ("金额importe", "100.00"), ("币种Moneda", "人民币"), ("事项说明Explicación de asuntos", "Office"),
            ("收款人beneficiario", "Vendor"), ("付款日期Fecha de pago", "2026-01-09"))], **values)


class RecordingConnection:
    """Capture the actual adapter query at the external database boundary."""
    def __init__(self, rows): self.rows = rows
    def __call__(self): return self
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def cursor(self): return self
    def execute(self, sql, params): self.sql, self.params = sql, params
    def fetchall(self): return self.rows


class OperatingCacheReconciliationTest(unittest.TestCase):
    def setUp(self):
        self.source = importlib.import_module("deeplinkerp_branding.services.operating_oa_source")
        # Exercise the real reconciliation service without a site, HTTP or a database.
        # These imports are unrelated to reconciliation and remain unused in these tests.
        frappe = ModuleType("frappe")
        frappe._ = lambda message: message
        frappe.whitelist = lambda **kwargs: lambda method: method
        modules = {"frappe": frappe, "requests": ModuleType("requests"),
            "frappe.utils": SimpleNamespace(now_datetime=None, getdate=None),
            "deeplinkerp_branding.services.purchase_payment_service": SimpleNamespace(_require_fields=None),
            "deeplinkerp_branding.services.unified_purchase_service": SimpleNamespace(_require_export_permission=None)}
        path = Path(__file__).parents[1] / "deeplinkerp_branding/services/operating_expenses.py"
        spec = importlib.util.spec_from_file_location("operating_reconciliation_test_target", path)
        self.service = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules):
            spec.loader.exec_module(self.service)

    def cached(self, rows):
        result = []
        for row in rows:
            raw = self.source.merge_application(row, [], {})
            raw.update(payments=[{"source_id": "cashier-evidence"}],
                       attachments=[{"source_id": "archive-evidence"}], remark="原始缓存备注")
            raw["version"] = self.source.digest({key: value for key, value in raw.items() if key != "version"})
            result.append((SimpleNamespace(source_version=raw["version"]), raw))
        return result

    def reconcile(self, cached, originals):
        class Connection(RecordingConnection):
            def __init__(self, rows):
                super().__init__(rows)
                self.queries = []

            def execute(self, sql, params):
                super().execute(sql, params)
                self.queries.append((sql, params))

            def fetchall(self):
                corps, instances = self.params
                return [row for row in self.rows if row["corp_id"] in corps
                        and row["process_instance_id"] in instances]

        query = Connection(originals)
        adapter = SimpleNamespace(_connection=Mock(return_value=query))
        maps = {"来源组织": "采购公司"}
        with patch.object(self.service, "_oa_cached_identities", return_value=(cached, {})), \
                patch.object(self.service, "_oa_connection", return_value=adapter) as factory, \
                patch.object(self.service, "_upsert") as upsert:
            changed = self.service._reconcile_oa_cache("2026-10-06T00:00:00Z", maps)
        factory.assert_called_once_with()
        adapter._connection.assert_called_once_with()
        return changed, query.queries, upsert, maps

    def test_reconciliation_projects_scope_fields_and_preserves_cached_audit_evidence(self):
        rows = [approval(updated_at=datetime(2026, 10, 5, tzinfo=UTC)) for _ in range(6)]
        for index, row in enumerate(rows):
            row.update(corp_id="corp'one", process_instance_id="instance-'" + str(index))
        rows[-1].update(corp_id="other-corp", process_instance_id=rows[0]["process_instance_id"])
        cached = self.cached(rows)
        before = copy.deepcopy(cached)
        originals = copy.deepcopy(rows)
        originals[1]["form_component_values"][1]["value"] = "墨西哥"
        originals[1].update(status="TERMINATED", result=None)
        originals[3]["form_component_values"][1]["value"] = "墨西哥"
        originals[3]["updated_at"] = datetime(2026, 10, 7, tzinfo=UTC)
        originals[4]["form_component_values"].append(copy.deepcopy(originals[4]["form_component_values"][0]))
        originals[-1]["form_component_values"][1]["value"] = "墨西哥"
        originals.pop(2)  # Missing source remains visible as withdrawn, with its original evidence.
        changed, queries, upsert, maps = self.reconcile(cached, originals)
        self.assertEqual(changed, 4)
        self.assertEqual(cached, before)
        expected = [(cached[index][1], next((row for row in originals
            if (row["corp_id"], row["process_instance_id"]) ==
               (cached[index][1]["oa_identity"]["corp_id"], cached[index][1]["oa_identity"]["process_instance_id"])), None))
            for index in (1, 2, 4, 5)]
        self.assertEqual(upsert.call_args_list,
                         [call(self.source.withdrawn_source(raw, row), maps) for raw, row in expected])
        for sql, params in queries:
            self.assertIn(self.source._OPERATING_FORM_VALUES + "::text AS form_component_values", sql)
            self.assertNotIn(",form_component_values::text AS form_component_values", sql)
            self.assertIn("corp_id=ANY(%s) AND process_instance_id=ANY(%s)", sql)
            self.assertNotIn("corp'one", sql)
            self.assertNotIn("instance-'", sql)
            self.assertEqual(set(params[0]), {"corp'one", "other-corp"})

    def test_reconciliation_limits_201_identities_to_three_queries_on_one_connection(self):
        rows = [approval(updated_at=datetime(2026, 10, 5, tzinfo=UTC)) for _ in range(201)]
        for index, row in enumerate(rows):
            row["process_instance_id"] = "instance-" + str(index)
        changed, queries, upsert, _ = self.reconcile(self.cached(rows), rows)
        self.assertEqual(changed, 0)
        upsert.assert_not_called()
        self.assertEqual([len(params[1]) for _, params in queries], [100, 100, 1])
        self.assertEqual([instance for _, params in queries for instance in params[1]],
                         [row["process_instance_id"] for row in rows])


class OriginalOperatingSourceTest(unittest.TestCase):
    def setUp(self):
        try:
            self.source = importlib.import_module("deeplinkerp_branding.services.operating_oa_source")
        except ImportError:
            self.source = None
        self.assertIsNotNone(self.source, "Original operating OA adapter is missing")

    def test_scope_requires_verified_template_region_year_and_request_type(self):
        self.assertTrue(self.source.in_scope(approval()))
        variants = [dict(process_code="procurement"), dict(create_time=datetime(2025, 12, 31, 15, 59, tzinfo=timezone.utc)),
                    dict(create_time=datetime(2026, 12, 31, 16, tzinfo=timezone.utc))]
        for values in variants:
            row = approval(); row.update(values)
            self.assertFalse(self.source.in_scope(row))
        for index, value in ((0, "备用金借款"), (1, "墨西哥"), (1, "")):
            row = approval(); row["form_component_values"][index]["value"] = value
            self.assertFalse(self.source.in_scope(row))
        row = approval(); row["form_component_values"][0]["value"] = "费用报销Reembolso de gastos"
        self.assertTrue(self.source.in_scope(row))

    def test_requested_payment_date_never_means_paid(self):
        item = self.source.merge_application(approval(), [], {})
        self.assertEqual(item["source_system"], "dingtalk-oa")
        self.assertEqual(item["amount"], "100.00")
        self.assertEqual(item["source_status"], "付款待核对")
        self.assertIsNone(item["paid_amount"])
        self.assertIsNone(item["pending_amount"])
        self.assertEqual(item["payments"], [])

    def test_display_projection_prefers_explicit_organization_and_keeps_legal_resolution_separate(self):
        row = approval()
        row['form_component_values'].extend([{'name': n, 'value': v} for n, v in (
            ('申请部门/组织 Departamento Solicitante', '来源组织'),
            ('项目归属Pertenencia del proyecto', '项目 A'), ('账户性质Tipo de cuenta', '公户'), ('备注', '来源备注'))])
        cashier = {'source_id': 'r', 'corp_id': 'corp', 'process_instance_id': 'instance-1',
            'amount': '100', 'currency': 'CNY', 'source_company': '记账法律公司',
            'general_manager_approval': '同意付款', 'projection_conflicts': [],
            'approvals': {'raw': {'general_manager_approval': '同意付款'}}}
        resolution = {'status': 'matched', 'assigned_department': '目录组织'}
        item = self.source.merge_application(row, [cashier], resolution)
        self.assertEqual({k: item[k] for k in ('source_company', 'account_nature', 'project',
                                             'needed_payment_date', 'general_manager_approval', 'remark')}, {
            'source_company': '来源组织', 'account_nature': '公户', 'project': '项目 A',
            'needed_payment_date': '2026-01-09', 'general_manager_approval': '同意付款', 'remark': '来源备注'})
        self.assertEqual(item['company_resolution'], resolution)
        self.assertEqual(item['company_mapping_source'], '目录组织')
        self.assertEqual(item['source_sheet'], '目录组织')
        self.assertEqual(item['projection_conflicts'], [])
        self.assertEqual(item['projection_sources']['source_company'], 'oa.form.source_company')
        self.assertEqual(item['projection_sources']['general_manager_approval'], 'cashier.general_manager_approval')
        self.assertFalse(item['payment_eligibility']['can_register_payment'])

        row['form_component_values'][-4]['value'] = '新展示组织'
        changed = self.source.merge_application(row, [cashier], resolution)
        self.assertEqual(changed['source_company'], '新展示组织')
        for key in ('company_resolution', 'company_mapping_source', 'source_sheet', 'approval_state', 'payment_eligibility'):
            self.assertEqual(changed[key], item[key])

        missing = self.source.merge_application(approval(), [], resolution)
        self.assertIsNone(missing['source_company'])
        self.assertNotIn('source_company', missing['projection_sources'])
        self.assertEqual(missing['company_mapping_source'], '目录组织')
        self.assertEqual(missing['company_resolution'], resolution)

    def test_purchase_read_includes_original_department_identity(self):
        from deeplinkerp_branding.services.purchase_source_contract import PROCESS_CODES
        class Query:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def cursor(self): return self
            def execute(self,sql,params): self.sql=sql
            def fetchall(self): return []
        query=Query()
        self.source.read_page(lambda:query,100,"2026-10-15T00:00:00+00:00",process_codes=PROCESS_CODES)
        self.assertIn("originator_dept_id,originator_dept_name",query.sql)

    def test_projection_conflicts_do_not_choose_a_value_or_infer_account_nature(self):
        row = approval()
        row['form_component_values'].extend([{'name': n, 'value': v} for n, v in (
            ('申请部门/组织', '组织 A'), ('项目归属', '项目 A'), ('项目归属Pertenencia', '项目 B'),
            ('账户性质', '某银行个人卡'), ('备注', ['not scalar']))])
        cashier = {'source_id': 'r', 'corp_id': 'corp', 'process_instance_id': 'instance-1',
            'amount': '100', 'currency': 'CNY', 'source_organization': '组织 B', 'project': '项目 A',
            'account_nature': '私户', 'remark': '人工备注'}
        item = self.source.merge_application(row, [cashier], {})
        self.assertEqual(set(item['projection_conflicts']), {'source_company', 'project', 'account_nature', 'remark'})
        for key in item['projection_conflicts']:
            self.assertIsNone(item[key])

    def test_matched_cashier_display_fields_are_used_only_with_exact_identity(self):
        cashier = {'source_id': 'r', 'corp_id': 'corp', 'process_instance_id': 'instance-1',
            'amount': '100', 'currency': 'CNY', 'source_organization': '原始归属组织',
            'source_company': '不用于列表的法人', 'project': '人工项目', 'account_nature': '私户',
            'needed_payment_date': '2026-01-09', 'remark': '人工备注',
            'general_manager_approval': '同意付款'}
        item = self.source.merge_application(approval(), [cashier], {'status': 'matched', 'assigned_department': '目录组织'})
        self.assertEqual(item['source_company'], '原始归属组织')
        self.assertEqual(item['project'], '人工项目')
        self.assertEqual(item['account_nature'], '私户')
        self.assertEqual(item['remark'], '人工备注')
        self.assertEqual(item['projection_sources']['project'], 'cashier.project')
        foreign = self.source.merge_application(approval(), [{**cashier, 'corp_id': 'foreign'}], {})
        for key in ('source_company', 'project', 'account_nature', 'remark', 'general_manager_approval'):
            self.assertIsNone(foreign[key])
        number_only = {**cashier, 'approval_no': approval()['business_id'],
                       'approval_identity_status': 'explicit'}
        number_only.pop('process_instance_id')
        legacy = self.source.merge_application(approval(), [number_only], {})
        for key in ('source_company', 'project', 'account_nature', 'remark', 'general_manager_approval'):
            self.assertIsNone(legacy[key])

    def test_workflow_corrects_originator_without_relabeling_current_approver(self):
        row = approval(); row.update(status="RUNNING", result="NONE", originator_user_id="supervisor")
        evidence = {"source_id": self.source.application_id(row), "corp_id": "corp", "process_instance_id": "instance-1",
            "lookup_status": "found", "approval_status": "RUNNING", "approval_result": "NONE",
            "originator": {"id": "actual-applicant", "name": "申请人"},
            "current_tasks": [{"id":"t", "stage":"主管", "assignees":[{"id":"supervisor", "name":"主管姓名"}]}],
            "payment_eligibility": {"can_register_payment":False, "reason":"主管审批尚未通过", "evidence_fingerprint":"proof"}}
        corrected = self.source.with_workflow(row, evidence)
        self.assertEqual(self.source.resolution_applicant(corrected, []), {"user_id":"actual-applicant", "employee_name":"申请人"})
        item = self.source.merge_application(corrected, [], {})
        self.assertEqual(item["applicant"], "申请人")
        self.assertEqual(item["approval_state"], "pending")
        self.assertEqual(item["current_approver"], "主管姓名")
        self.assertFalse(item["payment_eligibility"]["can_register_payment"])

        self.assertEqual(row["originator_user_id"], "supervisor")
        for lookup_status in ("found", "missing", "conflict"):
            unknown = self.source.with_workflow(row, {**evidence, "lookup_status":lookup_status, "originator":{"id":None,"name":None}})
            self.assertEqual(self.source.resolution_applicant(unknown, []), {"user_id":"", "employee_name":""})
            self.assertEqual(self.source.merge_application(unknown, [], {})["applicant"], "待核对")
        with self.assertRaises(ValueError):
            self.source.with_workflow(row, {**evidence, "corp_id":"other-corp"})

    def test_batch_cashier_index_does_not_merge_cross_company_or_duplicate_identity(self):
        rows = [{"source_id":"a", "corp_id":"corp", "process_instance_id":"instance-1"},
                {"source_id":"b", "corp_id":"other", "process_instance_id":"instance-1"},
                {"source_id":"c", "corp_id":"corp", "approval_no":"20260101001", "approval_identity_status":"explicit"}]
        index = self.source.cashier_index(rows)
        self.assertEqual([item["source_id"] for item in self.source.cashier_candidates(approval(), index)], ["a", "c"])
        self.assertEqual(len(self.source.cashier_candidates({**approval(), "business_count":2}, index)), 1)

    def test_cashier_without_proven_corporation_never_exposes_money_or_private_attachments(self):
        evidence = {"source_id":"foreign-root", "process_instance_id":"instance-1", "amount":"100", "currency":"CNY",
                    "paid_amount":"30", "pending_amount":"70", "payment_evidence_status":"recorded",
                    "payments":[{"source_id":"foreign-payment"}], "attachments":[{"source_id":"private-proof"}]}
        for row in (evidence, {**evidence, "process_instance_id":None, "approval_no":"20260101001", "approval_identity_status":"explicit"}):
            with self.subTest(identity=row):
                item = self.source.merge_application(approval(), [row], {})
                self.assertIsNone(item["cashier_source_id"])
                self.assertIsNone(item["paid_amount"])
                self.assertEqual(item["payments"], [])
                self.assertEqual(item["attachments"], [])
                self.assertEqual(self.source.cashier_candidates(approval(), self.source.cashier_index([row])), [])

    def test_approval_progress_does_not_erase_verified_payment_balance(self):
        row = approval(); row.update(status="RUNNING", result="NONE")
        cashier = {"source_id":"r", "corp_id":"corp", "process_instance_id":"instance-1", "amount":"100", "currency":"CNY",
                   "paid_amount":"30", "pending_amount":"70", "source_status":"部分付款", "payment_evidence_status":"recorded", "payments":[], "attachments":[]}
        item = self.source.merge_application(row, [cashier], {})
        self.assertEqual(item["pending_amount"], "70")
        self.assertFalse(item["payment_eligibility"]["can_register_payment"])
        for paid, pending in ((None,"100"),("0",None),("bad","100")):
            uncertain = self.source.merge_application(row, [{**cashier,"paid_amount":paid,"pending_amount":pending,"source_status":"未付款"}], {})
            self.assertEqual(uncertain["source_status"], "付款待核对")
            self.assertEqual(uncertain["cashier_reported_payment_status"], "未付款")

    def test_observed_bilingual_oa_currency_values_are_normalized_exactly(self):
        for value, currency in (("人民币RMB", "CNY"), ("美元Dólar", "USD"), ("比索Peso", "MXN")):
            with self.subTest(value=value):
                row = approval(); row["form_component_values"][3]["value"] = value
                item = self.source.merge_application(row, [], {})
                self.assertEqual(item["currency"], currency)
                self.assertEqual(item["original_source_currency"], currency)

    def test_unknown_oa_currency_never_borrows_cashier_currency(self):
        row = approval(); row["form_component_values"][3]["value"] = "人民币RMB待核对"
        cashier = {"source_id": "r1", "corp_id":"corp", "process_instance_id": "instance-1", "amount": "100", "currency": "CNY",
                   "paid_amount": "100", "pending_amount": "0", "payments": [], "attachments": [],
                   "payment_evidence_status": "recorded"}
        item = self.source.merge_application(row, [cashier], {})
        self.assertIsNone(item["currency"])
        self.assertIsNone(item["original_source_currency"])
        self.assertTrue(item["source_conflict"])
        self.assertIsNone(item["paid_amount"])
        self.assertIsNone(item["pending_amount"])

    def test_verified_unique_approval_joins_actual_payments_and_keeps_original_identity(self):
        raw = approval()
        cashier = {"source_id": "legacy-root", "corp_id":"corp", "approval_no": raw["business_id"], "approval_identity_status": "explicit",
                   "amount": "100", "currency": "CNY", "paid_amount": "20", "pending_amount": "80",
                   "source_status": "部分付款", "payments": [{"source_id": "p1", "amount": "20", "currency": "CNY", "evidence_status": "recorded"}],
                   "attachments": [], "payment_evidence_status": "recorded"}
        company = {"status": "matched", "assigned_department": "拉丁购", "match_source": "user_id"}
        item = self.source.merge_application(raw, [cashier], company)
        self.assertEqual(item["payments"], cashier["payments"])
        self.assertIsNone(item["source_company"])
        self.assertEqual(item["company_mapping_source"], "拉丁购")
        self.assertEqual(item["paid_amount"], "20")
        self.assertEqual(item["cashier_source_id"], "legacy-root")
        self.assertEqual(item["source_id"], self.source.application_id(raw))

    def test_ambiguous_identity_and_amount_conflict_never_become_unpaid(self):
        matching = {"source_id": "root", "corp_id":"corp", "process_instance_id": "instance-1", "amount": "999", "currency": "CNY",
                    "paid_amount": "0", "pending_amount": "999", "payments": [], "attachments": []}
        item = self.source.merge_application(approval(), [matching], {})
        self.assertTrue(item["source_conflict"])
        self.assertEqual(item["amount"], "100.00")
        self.assertIsNone(item["pending_amount"])
        item = self.source.merge_application(approval(), [matching, {**matching, "source_id": "root2"}], {})
        self.assertTrue(item["source_conflict"])
        self.assertEqual(item["payments"], [])

    def test_mutable_number_or_name_amount_never_authorizes_join(self):
        for extra in ({"dingding_id": "20260101001"}, {"approval_no": "20260101001", "approval_identity_status": "unverified"},
                      {"amount": "100", "applicant": "Alice"}):
            self.assertIsNone(self.source.merge_application(approval(), [extra], {})["cashier_source_id"])

    def test_rejected_running_cancelled_and_deleted_stay_blocked(self):
        for status, result, deleted in (("COMPLETED", "refuse", None), ("RUNNING", "agree", None),
                                       ("TERMINATED", "agree", None), ("COMPLETED", "agree", "2026-01-10")):
            row = approval(); row.update(status=status, result=result, deleted_at=deleted)
            item = self.source.merge_application(row, [], {})
            self.assertEqual(item["approvals"]["eligibility"], "blocked")
            self.assertIsNone(item["pending_amount"])

    def test_missing_or_float_money_not_fabricated_as_zero(self):
        for value in ("", None, 0.1, "NaN"):
            row = approval(); row["form_component_values"][2]["value"] = value
            self.assertIsNone(self.source.merge_application(row, [], {})["amount"])
        row = approval(); row["form_component_values"][2]["value"] = "0"
        self.assertEqual(self.source.merge_application(row, [], {})["amount"], "0")

    def test_paid_supplement_changes_version_without_overwriting_source_or_inputs(self):
        raw = approval(); previous = copy.deepcopy(raw)
        item = self.source.merge_application(raw, [], {})
        paid = self.source.merge_application(raw, [{"source_id": "r1", "corp_id":"corp", "process_instance_id": "instance-1",
           "amount": "100", "currency": "CNY", "paid_amount": "100", "pending_amount": "0", "source_status": "已付款",
           "payments": [], "attachments": [], "payment_evidence_status": "recorded"}], {})
        self.assertNotEqual(item["version"], paid["version"])
        self.assertEqual(item["source_id"], paid["source_id"])
        self.assertEqual(raw, previous)

    def test_cursor_is_bounded_and_binds_source_snapshot(self):
        value = self.source.encode_cursor("2026-10-06T00:00:00Z", "corp", "i")
        self.assertEqual(self.source.decode_cursor(value), ("2026-10-06T00:00:00Z", "corp", "i"))
        for invalid in ("garbage", "A" * 3000):
            with self.assertRaises(ValueError):
                self.source.decode_cursor(invalid)

    def test_explicit_identity_conflict_and_duplicate_business_ids_block_join(self):
        row = approval(); row["business_count"] = 2
        self.assertIsNone(self.source.merge_application(row, [{"source_id": "r1", "approval_no": row["business_id"], "approval_identity_status": "explicit"}], {})["cashier_source_id"])
        self.assertIsNone(self.source.merge_application(approval(), [{"source_id": "r1", "process_instance_id": "instance-1", "identity_conflict": True}], {})["cashier_source_id"])

    def test_blank_business_numbers_never_authorize_cashier_fallback(self):
        for business_id in (None, ""):
            with self.subTest(business_id=business_id):
                row = approval(); row.update(business_id=business_id, business_count=0)
                cashier = {"source_id": "r1", "approval_no": business_id, "approval_identity_status": "explicit"}
                self.assertIsNone(self.source.merge_application(row, [cashier], {})["cashier_source_id"])

    def test_read_page_projects_bounded_sql_in_existing_read_only_connection(self):
        query = RecordingConnection([approval(), {**approval(), "process_instance_id": "instance-2"}])
        rows, cursor = self.source.read_page(query, 1, "2026-10-06T00:00:00Z")
        self.assertEqual(len(rows), 1)
        self.assertTrue(cursor)
        self.assertEqual(self.source.decode_cursor(cursor), ("2026-10-06T00:00:00Z", "corp", "instance-1"))
        self.assertIn("CASE WHEN jsonb_typeof(form_component_values)='array' THEN", query.sql)
        self.assertIn("jsonb_agg(jsonb_build_object('name',component->'name','value',component->'value') ORDER BY ordinal),'[]'::jsonb)", query.sql)
        self.assertIn("jsonb_array_elements(form_component_values) WITH ORDINALITY AS form(component,ordinal)", query.sql)
        self.assertIn("WHERE jsonb_typeof(component)='object' AND component->>'name' ~ '申请类型|执行地区|金额|币种|事项说明|收款人|付款日期|归属项目|项目|申请部门/组织|部门/组织|部门Departamento|账户性质|备注'", query.sql)
        self.assertIn("ELSE form_component_values END)::text AS form_component_values", query.sql)
        self.assertNotIn("btrim", query.sql)
        self.assertNotIn("DISTINCT", query.sql)
        self.assertNotIn("SELECT *", query.sql)
        self.assertEqual(query.params[-1], 2)
        with self.assertRaises(ValueError):
            self.source.read_page(query, 1, "2026-10-07T00:00:00Z", cursor=cursor)

    def test_read_page_limits_materialized_page_before_global_business_count(self):
        query = RecordingConnection([])
        self.source.read_page(query, 500, "2026-10-06T00:00:00Z")
        sql = " ".join(query.sql.split())
        self.assertTrue(sql.startswith("WITH page AS MATERIALIZED (SELECT "), sql)
        self.assertNotIn(" OVER ", sql.upper())
        self.assertEqual(sql.count("), counts AS MATERIALIZED ("), 1, sql)
        page, counts = sql.split("), counts AS MATERIALIZED (", 1)
        self.assertIn("FROM costing_read.approval_instances_v2 WHERE process_code = ANY(%s)", page)
        self.assertIn("create_time >= %s AND create_time < %s AND updated_at <= %s", page)
        self.assertTrue(page.endswith("ORDER BY corp_id,process_instance_id LIMIT %s"), page)
        self.assertTrue(counts.startswith("SELECT business_id,count(*) AS business_count FROM costing_read.approval_instances_v2 WHERE business_id IN (SELECT business_id FROM page WHERE NULLIF(business_id,'') IS NOT NULL) GROUP BY business_id) "), counts)
        self.assertTrue(counts.endswith("SELECT page.*,COALESCE(counts.business_count,0) AS business_count FROM page LEFT JOIN counts USING(business_id) ORDER BY page.corp_id,page.process_instance_id"), counts)
        self.assertEqual(query.params, (list(self.source.PROCESS_CODES), self.source.START, self.source.END,
                                       datetime(2026, 10, 6, tzinfo=timezone.utc), 501))

    def test_read_page_cursor_and_identity_are_parameterized_in_bounded_page(self):
        from deeplinkerp_branding.services.purchase_source_contract import PROCESS_CODES as purchase_codes
        until = "2026-10-06T00:00:00Z"
        after_corp, after_instance = "corp-after", "instance-after"
        identity = {"corp_id": "corp'identity", "process_instance_id": "instance'identity"}
        cursor = self.source.encode_cursor(until, after_corp, after_instance)
        for process_codes in (self.source.PROCESS_CODES, purchase_codes):
            with self.subTest(process_codes=process_codes):
                query = RecordingConnection([])
                rows, next_cursor = self.source.read_page(query, 7, until, cursor=cursor, identity=identity,
                                                          process_codes=process_codes)
                self.assertEqual((rows, next_cursor), ([], None))
                self.assertTrue(query.sql.startswith("WITH page AS MATERIALIZED ("), query.sql)
                self.assertEqual(query.sql.count("), counts AS MATERIALIZED ("), 1, query.sql)
                page = query.sql.split("), counts AS MATERIALIZED (", 1)[0]
                if process_codes == self.source.PROCESS_CODES:
                    self.assertIn("jsonb_array_elements(form_component_values)", page)
                    self.assertNotIn("form_component_values::text AS form_component_values", page)
                else:
                    self.assertIn("form_component_values::text AS form_component_values", page)
                    self.assertNotIn("jsonb_array_elements", page)
                self.assertIn("(corp_id, process_instance_id) > (%s, %s)", page)
                self.assertIn("corp_id=%s AND process_instance_id=%s", page)
                for value in (after_corp, after_instance, *identity.values()):
                    self.assertNotIn(value, query.sql)
                self.assertEqual(query.params, (list(process_codes), self.source.START, self.source.END,
                    datetime(2026, 10, 6, tzinfo=timezone.utc), after_corp, after_instance,
                    identity["corp_id"], identity["process_instance_id"], 8))

    def test_attachment_manifest_is_scoped_and_proves_file_version(self):
        row = approval()
        archived = {"corp_id": "corp", "process_instance_id": "instance-1", "file_id": "f1", "file_name": "invoice.pdf",
                    "archive_status": "archived", "sha256": "a" * 64, "actual_size": 100}
        items = self.source.archive_attachments(row, [archived, {**archived, "corp_id": "other"}])
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["url"].startswith("/oa-archive/"))
        self.assertEqual(items[0]["archive_identity"]["file_id"], "f1")

    def test_archive_integrity_requires_complete_exact_evidence(self):
        manifest = {"archive_status": "archived", "sha256": "a" * 64, "actual_size": 100}
        self.assertTrue(hasattr(self.source, "archive_integrity"), "Archive integrity guard missing")
        self.assertEqual(self.source.archive_integrity(manifest), ("a" * 64, 100))
        for mutation in ({"sha256": None}, {"sha256": "a"}, {"actual_size": None}, {"actual_size": -1}, {"actual_size": True}, {"actual_size": 1.5}, {"archive_status": "pending"}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.source.archive_integrity({**manifest, **mutation})

    def test_ambiguous_manual_applicant_never_falls_back_to_old_user_company(self):
        cashier = {"source_id": "r", "corp_id":"corp", "process_instance_id": "instance-1", "amount": "100", "currency": "CNY",
                   "applicant_identity": {"status": "ambiguous", "manual_applicant_override": None}}
        item = self.source.merge_application(approval(), [cashier], {"status": "matched", "assigned_department": "拉丁购"})
        self.assertIsNone(item["source_company"])
        self.assertEqual(item["company_resolution"]["status"], "ambiguous")

    def test_manual_applicant_identity_uses_current_resolver_not_old_oa_userid(self):
        cashier = {"source_id": "r", "corp_id":"corp", "process_instance_id": "instance-1", "applicant_identity": {
            "user_id": "", "employee_name": "Manual Alice", "status": "identified", "manual_applicant_override": True}}
        self.assertEqual(self.source.resolution_applicant(approval(), [cashier]), {"user_id": "", "employee_name": "Manual Alice"})
        cashier["applicant_identity"]["status"] = "ambiguous"
        self.assertEqual(self.source.resolution_applicant(approval(), [cashier]), {"user_id": "", "employee_name": ""})

    def test_withdrawn_cache_clears_payable_and_has_stable_fingerprint(self):
        item = self.source.merge_application(approval(), [], {})
        removed = self.source.withdrawn_source(item)
        self.assertEqual(removed, self.source.withdrawn_source(removed))
        self.assertEqual(removed["approvals"]["eligibility"], "blocked")
        self.assertIsNone(removed["pending_amount"])

    def test_cashier_approval_and_reported_payment_fields_are_preserved_as_evidence(self):
        cashier = {"source_id": "r", "corp_id":"corp", "process_instance_id": "instance-1", "amount": "100", "currency": "CNY",
                   "source_status": "已付款", "approvals": {"raw": {"general_manager_approval": "同意付款", "finance_review": "已付款"}}}
        item = self.source.merge_application(approval(), [cashier], {})
        self.assertEqual(item["approvals"]["raw"]["cashier_general_manager_approval"], "同意付款")
        self.assertEqual(item["cashier_reported_payment_status"], "已付款")
        self.assertEqual(item["source_status"], "付款待核对")
        self.assertIsNone(item["paid_amount"])
