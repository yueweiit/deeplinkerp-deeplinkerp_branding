import copy
import unittest
from unittest.mock import patch

from deeplinkerp_branding.services.operating_cashier_reporting import eligible_date, project
from deeplinkerp_branding.services.operating_oa_workflow import workflow
from deeplinkerp_branding.services.operating_expense_analysis import summarize


def task(**values):
    return {"taskId": "t1", "taskGroupName": "CN 出纳Cajero", "status": "COMPLETED", "result": "AGREE",
            "createTime": "2026-06-30T15:00Z", "finishTime": "2026-06-30T16:30Z", **values}


def application(**values):
    return {"process_instance_id": "instance", "status": "COMPLETED", "result": "agree", **values}


class CashierTests(unittest.TestCase):
    def test_completion_uses_china_date_and_last_successful_task_once(self):
        tasks = [task(), task(taskId="t2", taskGroupName="出纳", finishTime="2026-07-02T15:00Z")]
        before = copy.deepcopy(tasks)
        self.assertEqual(project(application(), tasks)["date"], "2026-07-02")
        self.assertEqual(tasks, before)
        self.assertEqual(project(application(), [task()])["date"], "2026-07-01")

    def test_completed_node_can_precede_final_approval_without_claiming_actual_payment(self):
        result = workflow(application(status="RUNNING", result="", raw_payload={"tasks": [task()]}))
        self.assertEqual(result["cashier"]["status"], "completed")
        self.assertFalse(result["payment_eligibility"]["can_register_payment"])

    def test_transfer_is_not_completion_and_pending_replacement_blocks(self):
        transfer = task(status="CANCELED", result="REDIRECTED")
        self.assertIsNone(project(application(), [transfer])["date"])
        self.assertEqual(project(application(), [transfer, task(taskId="replacement")])["date"], "2026-07-01")
        for status in ("RUNNING", "NEW", "CANCELED"):
            with self.subTest(status=status):
                self.assertIsNone(project(application(), [task(), task(taskId="later", status=status, result="")])["date"])

    def test_unrelated_mx_or_finance_node_is_not_cashier(self):
        for label in ("MX 出纳Cajero", "CN 会计Contador", "出纳主管审批", "", "cashier"):
            self.assertIsNone(project(application(), [task(taskGroupName=label)])["date"])

    def test_missing_invalid_negative_or_timezone_free_timing_is_excluded(self):
        for finish in (None, "", "bad", "2026-06-30T16:30", "2026-06-29T16:30Z"):
            self.assertIsNone(project(application(), [task(finishTime=finish)])["date"])
        self.assertIsNone(project(application(), [task(createTime=None)])["date"])

    def test_rejection_cancellation_withdrawal_and_duplicate_tasks_do_not_count(self):
        for parent in (application(status="TERMINATED"), application(result="refuse"),
                       application(status="RUNNING", result="refuse"), application(deleted_at="now")):
            self.assertIsNone(project(parent, [task()])["date"])
        self.assertIsNone(project(application(), [task(result="REFUSE")])["date"])
        with self.assertRaises(ValueError):
            workflow(application(raw_payload={"tasks": [task(), task()]}))

    def test_cached_projection_cannot_override_current_source_conflicts(self):
        row = {"source_system": "dingtalk-oa", "approval_state": "approved", "cashier_date": "2026-07-01"}
        self.assertEqual(eligible_date(row), "2026-07-01")
        for values in ({"source_withdrawn": True}, {"approval_state": "rejected"}, {"source_conflict": True},
                       {"currency_conflict": True}, {"projection_conflicts": ["conflict"]},
                       {"source_system": "legacy"}, {"cashier_date": "null"}, {"cashier_date": "2026-7-1"}):
            self.assertIsNone(eligible_date({**row, **values}))

    def test_cashier_grouping_uses_processing_month_but_keeps_payment_amounts_unknown(self):
        rows = [{"source_system": "dingtalk-oa", "approval_state": "approved", "company": "CN",
                 "request_date": "2026-06-01", "cashier_date": "2026-07-01", "amount": "10.10",
                 "paid_amount": None, "pending_amount": None, "currency": "CNY"}]
        result = summarize(rows, basis="cashier")
        self.assertEqual(result["basis"], "cashier")
        self.assertEqual(result["months"][0]["key"], "2026-07")
        self.assertEqual(result["currency_totals"]["CNY"]["amount"], "10.10")
        self.assertIsNone(result["currency_totals"]["CNY"]["paid_amount"])


class CashierServiceTests(unittest.TestCase):
    def test_invalid_date_basis_fails_before_querying_source_data(self):
        from deeplinkerp_branding.services import operating_expenses as service
        with patch.object(service, "_require_fields"), \
                patch.object(service.frappe, "throw", side_effect=ValueError("invalid basis")), \
                patch.object(service.frappe, "get_list") as native:
            for value in (["cashier"], "bank_paid", True):
                with self.assertRaisesRegex(ValueError, "invalid basis"):
                    service._list_rows({"basis": value})
        native.assert_not_called()

    def test_native_permission_query_does_not_exclude_prior_month_applications(self):
        from deeplinkerp_branding.services import operating_expenses as service
        base = {"source_system": "dingtalk-oa", "approval_state": "approved", "company": "CN",
                "request_date": "2026-05-01", "currency": "CNY", "amount": "100", "paid_amount": None,
                "pending_amount": None, "payment_eligibility": {"can_register_payment": False}}
        rows = [{**base, "name": str(i), "cashier_date": day} for i, day in enumerate(
            ["2026-06-30", "2026-07-01", "2026-07-31", "2026-08-01", None])]
        rows.append({**base, "name": "withdrawn", "cashier_date": "2026-07-01", "source_withdrawn": True})
        with patch.object(service, "_require_fields"), patch.object(service, "_readable_source_numbers"), \
                patch.object(service.frappe, "get_list", return_value=rows) as native, \
                patch("deeplinkerp_branding.services.operating_payment_service.overlay_rows"):
            result = service._list_rows({"basis": "cashier", "company": "CN", "date_from": "2026-07-01", "date_to": "2026-07-31"})
        self.assertEqual([row["name"] for row in result], ["1", "2"])
        self.assertEqual(native.call_args.kwargs["filters"], [["company", "=", "CN"]])
        self.assertNotIn("ignore_permissions", native.call_args.kwargs)

    def test_unsynchronized_data_is_reported_instead_of_exported_as_zero(self):
        from deeplinkerp_branding.services import operating_expenses as service
        meta = {}
        row = {"source_system": "dingtalk-oa", "approval_state": "approved", "cashier_date": None}
        with patch.object(service, "_require_fields"), patch.object(service, "_readable_source_numbers"), \
                patch.object(service.frappe, "get_list", return_value=[row]), \
                patch("deeplinkerp_branding.services.operating_payment_service.overlay_rows"):
            self.assertEqual(service._list_rows({"basis": "cashier"}, report_meta=meta), [])
        self.assertEqual(meta, {"cashier_pending_sync": 1})
        def incomplete(*args, report_meta, **kwargs):
            report_meta["cashier_pending_sync"] = 1
            return []
        with patch.object(service, "_list_rows", side_effect=incomplete), \
                patch.object(service.frappe, "throw", side_effect=ValueError("sync required")), \
                patch.object(service, "_require_export_permission") as export:
            with self.assertRaisesRegex(ValueError, "sync required"):
                service.export_operating_expenses({"basis": "cashier"})
        export.assert_not_called()

    def test_cashier_export_labels_application_amount_and_keeps_processing_date(self):
        from io import BytesIO
        from zipfile import ZipFile
        from deeplinkerp_branding.services import operating_expenses as service
        row = {"name": "source", "amount": "100.10", "cashier_date": "2026-07-01", "request_date": "2026-05-01"}
        response = {}
        with patch.object(service, "_list_rows", return_value=[row]), \
                patch.object(service, "_require_export_permission", side_effect=lambda dt, rows: list(rows)), \
                patch.object(service, "_source", return_value={"owner": "user"}), \
                patch.object(service, "_enrich_page"), patch.object(service.frappe, "response", response):
            service.export_operating_expenses({"basis": "cashier"})
        with ZipFile(BytesIO(response["filecontent"])) as archive:
            sheet = archive.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("Cashier Processed Amount", sheet)
        self.assertIn("2026-07-01", sheet)
        self.assertIn("2026-05-01", sheet)
        self.assertNotIn("已支付金额", sheet)
        self.assertNotIn("待付款金额", sheet)
