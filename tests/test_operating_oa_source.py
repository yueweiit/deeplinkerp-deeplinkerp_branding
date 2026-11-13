"""OA-only source facts, archive integrity and shared procurement compatibility."""
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
            raw = self.source.merge_application(row)
            raw.update(payments=[{"source_id": "cashier-evidence"}],
                       attachments=[{"source_id": "archive-evidence"}], remark="原始缓存备注")
            raw["version"] = self.source.digest({key: value for key, value in raw.items() if key != "version"})
            result.append((SimpleNamespace(source_version=raw["version"]), raw))
        return result

    def reconcile(self, cached, originals, exact=None):
        from deeplinkerp_branding.services import operating_oa_http as http
        pages = [originals[offset:offset + 100] for offset in range(0, len(originals), 100)] or [[]]
        reads = []
        exact = exact or {}
        def read_page(url, corps, limit, until, cursor=None, identity=None):
            if identity:
                reads.append((url, corps, limit, until, "exact", dict(identity)))
                found = exact.get((identity["corp_id"], identity["process_instance_id"]))
                return [found] if found else [], None
            index = int(cursor or 0)
            reads.append((url, corps, limit, until, cursor))
            return pages[index], str(index + 1) if index + 1 < len(pages) else None
        maps = {"来源组织": "采购公司"}
        with patch.object(self.service, "_oa_cached_identities", return_value=(cached, {})), \
                patch.object(http, "configuration", return_value=("http://oa:3000", tuple({
                    raw["oa_identity"]["corp_id"] for _, raw in cached}))), \
                patch.object(http, "read_page", side_effect=read_page), \
                patch.object(self.service, "_oa_connection") as legacy_reader, \
                patch.object(self.service, "_upsert") as upsert:
            changed = self.service._reconcile_oa_cache("2026-10-06T00:00:00Z", maps)
        legacy_reader.assert_not_called()
        return changed, reads, upsert, maps

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
        changed, reads, upsert, maps = self.reconcile(cached, originals)
        self.assertEqual(changed, 4)
        self.assertEqual(cached, before)
        expected = [(cached[index][1], next((row for row in originals
            if (row["corp_id"], row["process_instance_id"]) ==
               (cached[index][1]["oa_identity"]["corp_id"], cached[index][1]["oa_identity"]["process_instance_id"])), None))
            for index in (1, 2, 4, 5)]
        self.assertEqual(upsert.call_args_list,
                         [call(self.source.withdrawn_source(raw, row), maps) for raw, row in expected])
        self.assertEqual(len(reads), 2)
        self.assertEqual(reads[1][-1],
                         {"corp_id": rows[2]["corp_id"], "process_instance_id": rows[2]["process_instance_id"]})
        self.assertEqual(set(reads[0][1]), {"corp'one", "other-corp"})
        self.assertEqual(reads[0][2], 200)

    def test_missing_page_row_is_kept_when_exact_query_finds_it(self):
        row = approval(updated_at=datetime(2026, 10, 5, tzinfo=UTC))
        cached = self.cached([row])
        key = (row["corp_id"], row["process_instance_id"])
        changed, reads, upsert, _ = self.reconcile(cached, [], exact={key: row})
        self.assertEqual(changed, 0)
        upsert.assert_not_called()
        self.assertEqual(reads[-1][-1], {"corp_id": key[0], "process_instance_id": key[1]})

    def test_exact_deleted_source_is_blocked_after_identity_verification(self):
        row = approval(updated_at=datetime(2026, 10, 5, tzinfo=UTC))
        cached = self.cached([row])
        deleted = {**row, "deleted_at": datetime(2026, 10, 5, tzinfo=UTC)}
        key = (row["corp_id"], row["process_instance_id"])
        changed, _, upsert, maps = self.reconcile(cached, [], exact={key: deleted})
        self.assertEqual(changed, 1)
        upsert.assert_called_once_with(self.source.withdrawn_source(cached[0][1], deleted), maps)

    def test_failed_exact_lookup_cannot_withdraw_a_cached_source(self):
        from deeplinkerp_branding.services import operating_oa_http as http
        cached = self.cached([approval(updated_at=datetime(2026, 10, 5, tzinfo=UTC))])
        with patch.object(self.service, "_oa_cached_identities", return_value=(cached, {})), \
                patch.object(http, "configuration", return_value=("http://oa:3000", ("corp",))), \
                patch.object(http, "read_page", side_effect=[([], None), ValueError("OA unavailable")]), \
                patch.object(self.service, "_upsert") as upsert:
            with self.assertRaisesRegex(ValueError, "OA unavailable"):
                self.service._reconcile_oa_cache("2026-10-06T00:00:00Z", {})
        upsert.assert_not_called()

    def test_reconciliation_consumes_all_http_pages_before_withdrawal(self):
        rows = [approval(updated_at=datetime(2026, 10, 5, tzinfo=UTC)) for _ in range(201)]
        for index, row in enumerate(rows):
            row["process_instance_id"] = "instance-" + str(index)
        changed, reads, upsert, _ = self.reconcile(self.cached(rows), rows)
        self.assertEqual(changed, 0)
        upsert.assert_not_called()
        self.assertEqual([read[-1] for read in reads], [None, "1", "2"])


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
        item = self.source.merge_application(approval())
        self.assertEqual(item["source_system"], "dingtalk-oa")
        self.assertEqual(item["amount"], "100.00")
        self.assertEqual(item["source_status"], "付款待核对")
        self.assertIsNone(item["paid_amount"])
        self.assertIsNone(item["pending_amount"])
        self.assertEqual(item["payments"], [])


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


    def test_observed_bilingual_oa_currency_values_are_normalized_exactly(self):
        for value, currency in (("人民币RMB", "CNY"), ("美元Dólar", "USD"), ("比索Peso", "MXN")):
            with self.subTest(value=value):
                row = approval(); row["form_component_values"][3]["value"] = value
                item = self.source.merge_application(row)
                self.assertEqual(item["currency"], currency)
                self.assertEqual(item["original_source_currency"], currency)


    def test_rejected_running_cancelled_and_deleted_stay_blocked(self):
        for status, result, deleted in (("COMPLETED", "refuse", None), ("RUNNING", "agree", None),
                                       ("TERMINATED", "agree", None), ("COMPLETED", "agree", "2026-01-10")):
            row = approval(); row.update(status=status, result=result, deleted_at=deleted)
            item = self.source.merge_application(row)
            self.assertEqual(item["approvals"]["eligibility"], "blocked")
            self.assertIsNone(item["pending_amount"])


    def test_missing_or_float_money_not_fabricated_as_zero(self):
        for value in ("", None, 0.1, "NaN"):
            row = approval(); row["form_component_values"][2]["value"] = value
            self.assertIsNone(self.source.merge_application(row)["amount"])
        row = approval(); row["form_component_values"][2]["value"] = "0"
        self.assertEqual(self.source.merge_application(row)["amount"], "0")


    def test_cursor_is_bounded_and_binds_source_snapshot(self):
        value = self.source.encode_cursor("2026-10-06T00:00:00Z", "corp", "i")
        self.assertEqual(self.source.decode_cursor(value), ("2026-10-06T00:00:00Z", "corp", "i"))
        for invalid in ("garbage", "A" * 3000):
            with self.assertRaises(ValueError):
                self.source.decode_cursor(invalid)


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
        old_value = {key: field for key, field in items[0].items() if key not in {"content_quality", "version"}}
        self.assertEqual(items[0]["version"], self.source.digest({**old_value,
            "sha256": archived["sha256"], "actual_size": archived["actual_size"]}))
        quality = self.source.archive_attachments(row, [{**archived, "content_quality": "preview"}])[0]
        self.assertEqual(quality["version"], items[0]["version"])
        self.assertEqual(quality["content_quality"], "preview")


    def test_archive_integrity_requires_complete_exact_evidence(self):
        manifest = {"archive_status": "archived", "sha256": "a" * 64, "actual_size": 100}
        self.assertTrue(hasattr(self.source, "archive_integrity"), "Archive integrity guard missing")
        self.assertEqual(self.source.archive_integrity(manifest), ("a" * 64, 100))
        for mutation in ({"sha256": None}, {"sha256": "a"}, {"actual_size": None}, {"actual_size": -1}, {"actual_size": True}, {"actual_size": 1.5}, {"archive_status": "pending"}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.source.archive_integrity({**manifest, **mutation})


    def test_manual_applicant_identity_uses_current_resolver_not_old_oa_userid(self):
        cashier = {"source_id": "r", "corp_id":"corp", "process_instance_id": "instance-1", "applicant_identity": {
            "user_id": "", "employee_name": "Manual Alice", "status": "identified", "manual_applicant_override": True}}
        self.assertEqual(self.source.resolution_applicant(approval(), [cashier]), {"user_id": "", "employee_name": "Manual Alice"})
        cashier["applicant_identity"]["status"] = "ambiguous"
        self.assertEqual(self.source.resolution_applicant(approval(), [cashier]), {"user_id": "", "employee_name": ""})


    def test_withdrawn_cache_clears_payable_and_has_stable_fingerprint(self):
        item = self.source.merge_application(approval())
        removed = self.source.withdrawn_source(item)
        self.assertEqual(removed, self.source.withdrawn_source(removed))
        self.assertEqual(removed["approvals"]["eligibility"], "blocked")
        self.assertIsNone(removed["pending_amount"])
