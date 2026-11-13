"""OA source semantics: explicit organization, raw workflow and unknown payments."""
import copy
import unittest

from deeplinkerp_branding.services import operating_oa_source as oa
from deeplinkerp_branding.services.operating_oa_workflow import workflow
from tests.test_operating_oa_source import approval


class OAOnlyProjectionTests(unittest.TestCase):
    def test_form_organization_selects_mapping_key_without_inventing_legal_company(self):
        row = approval(originator_dept_name="Directory department")
        row["form_component_values"].append({"name": "申请部门/组织", "value": "Explicit organization"})
        item = oa.merge_application(row)
        self.assertEqual(item["source_company"], "Explicit organization")
        self.assertEqual(item["company_mapping_source"], "Explicit organization")
        self.assertNotIn("company", item)
        self.assertEqual(item["projection_sources"]["source_company"], "oa.form.source_company")

    def test_original_department_fallback_is_explicit_and_unmapped_stays_unknown(self):
        item = oa.merge_application(approval(originator_dept_name="Department", originator_dept_id="42"))
        self.assertEqual(item["company_mapping_source"], "Department")
        self.assertEqual(item["company_resolution"]["department_id"], "42")
        self.assertEqual(item["projection_sources"]["source_company"], "oa.originatorDeptName")
        unknown = oa.merge_application(approval())
        self.assertIsNone(unknown["company_mapping_source"])
        self.assertEqual(unknown["company_resolution"]["status"], "unavailable")

    def test_duplicate_or_invalid_organization_cannot_fall_back_to_department(self):
        for fields in (["A", "B"], [["A"]]):
            with self.subTest(fields=fields):
                row = approval(originator_dept_name="Fallback")
                row["form_component_values"] += [{"name": "部门/组织", "value": value} for value in fields]
                item = oa.merge_application(row)
                self.assertIsNone(item["company_mapping_source"])
                self.assertIn("source_company", item["projection_conflicts"])

    def test_display_conflicts_and_invalid_dates_do_not_create_payment_evidence(self):
        row = approval()
        row["form_component_values"] += [{"name": "账户性质", "value": "Maybe private"},
                                        {"name": "付款日期", "value": "2026-02-30"}]
        item = oa.merge_application(row)
        self.assertIsNone(item["account_nature"])
        self.assertIsNone(item["needed_payment_date"])
        self.assertIsNone(item["paid_amount"])
        self.assertEqual(item["payments"], [])

    def test_non_text_account_nature_is_flagged_without_aborting_projection(self):
        for value in (["公户"], {"value": "公户"}, [], {}, 1, False):
            with self.subTest(value=value):
                row = approval()
                row["form_component_values"].append({"name": "账户性质", "value": value})
                item = oa.merge_application(row)
                self.assertIsNone(item["account_nature"])
                self.assertIn("account_nature", item["projection_conflicts"])
                self.assertEqual(item["amount"], "100.00")

    def test_approved_oa_and_archived_receipt_do_not_prove_actual_payment(self):
        row = approval()
        item = oa.merge_application(row)
        item["attachments"] = oa.archive_attachments(row, [{"corp_id": "corp", "process_instance_id": "instance-1",
            "file_id": "receipt", "file_name": "payment-receipt.pdf", "archive_status": "archived",
            "actual_size": 100, "sha256": "a" * 64}])
        self.assertEqual(item["approvals"]["eligibility"], "eligible")
        self.assertEqual(len(item["attachments"]), 1)
        self.assertEqual(item["payment_evidence_status"], "unknown")
        self.assertFalse(item["payment_eligibility"]["can_register_payment"])
        self.assertIsNone(item["pending_amount"])

    def test_projection_is_stable_and_does_not_mutate_oa_payload(self):
        row = approval(raw_payload={"tasks": [], "operationRecords": []})
        original = copy.deepcopy(row)
        first = oa.merge_application(row)
        self.assertEqual(first, oa.merge_application(row))
        self.assertEqual(row, original)
        self.assertNotIn("raw_payload", first)
        self.assertNotIn("cashier_source_id", first)

    def test_unknown_currency_never_becomes_zero_or_a_payment(self):
        row = approval()
        row["form_component_values"][3]["value"] = "Unknown currency"
        item = oa.merge_application(row)
        self.assertIsNone(item["currency"])
        self.assertIsNone(item["paid_amount"])


class OAWorkflowTests(unittest.TestCase):
    def task(self, **values):
        return {"taskId": "task-1", "userId": "approver-1", "activityId": "node-1", "taskGroupName": "Review",
                "status": "RUNNING", "result": "NONE", "createTime": "2026-01-01T12:00:00Z", **values}

    def test_current_tasks_come_from_oa_and_do_not_replace_applicant(self):
        row = approval(raw_payload={"tasks": [self.task()], "originatorUserId": "u1"})
        row["status"] = "RUNNING"
        item = oa.merge_application(row)
        self.assertEqual(item["applicant"], "Alice")
        self.assertEqual(item["current_approver"], "approver-1")
        self.assertEqual(item["workflow_summary"]["current_tasks"][0]["stage"], "Review")
        self.assertFalse(item["payment_eligibility"]["can_register_payment"])

    def test_completed_approval_does_not_show_leftover_running_tasks_as_current(self):
        timeline = workflow(approval(raw_payload={"tasks": [self.task()]}))
        self.assertEqual(timeline["current_tasks"], [])
        self.assertFalse(timeline["events"][0]["current"])

    def test_timeline_uses_operation_records_and_omits_arbitrary_attachment_urls(self):
        timeline = workflow(approval(raw_payload={"tasks": [self.task(status="COMPLETED")],
            "operationRecords": [{"activityId": "node-1", "userId": "approver-1", "result": "AGREE",
                                  "remark": "Verified", "date": "2026-01-01T13:00:00Z",
                                  "attachments": [{"url": "http://untrusted/file"}]}]}))
        self.assertEqual(len(timeline["events"]), 1)
        event = timeline["events"][0]
        self.assertEqual(event["stage"], "Review")
        self.assertEqual(event["result"], "AGREE")
        self.assertEqual(event["comment"], "Verified")
        self.assertNotIn("attachments", event)

    def test_conflicting_identity_or_approval_state_is_rejected(self):
        for raw in ({"originatorUserId": "another"}, {"status": "RUNNING"}, {"result": "refuse"}):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "不一致"):
                workflow(approval(raw_payload=raw))

    def test_malformed_duplicate_and_unbounded_tasks_fail(self):
        for raw in ([], {"tasks": {}}, {"tasks": [None]}, {"tasks": [self.task(), self.task()]},
                    {"tasks": [{"userId": "u"}]}, {"tasks": [self.task()] * 5001}):
            with self.subTest(kind=type(raw)), self.assertRaises(ValueError):
                workflow(approval(raw_payload=raw))
