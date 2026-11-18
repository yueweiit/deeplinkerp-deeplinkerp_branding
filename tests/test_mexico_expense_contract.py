"""Business facts, historical forms and confirmation invalidation; no site IO."""
import copy
import json
import unittest
from unittest.mock import patch

from deeplinkerp_branding.services import mexico_expense_contract as c


def field(key, value, kind="DDSelectField"):
    return {"id": key, "value": value, "componentType": kind}


def table(key="TableField_1QM4FQVMBI0W0", number="row-1", entity="LATINGO MX", amount="251739", description="Electricity"):
    entity_field, amount_field, text_field, _, _ = c.TABLES[key]
    return field(key, json.dumps([{"rowNumber": number, "rowValue": [
        {"key": entity_field, "value": entity}, {"key": amount_field, "value": amount},
        {"key": text_field, "value": description}]}]), "TableField")


def approval(components=None):
    return {"corp_id": c.CORP_ID, "process_code": c.PROCESS_CODE, "process_instance_id": "instance-1",
        "business_id": "approval-1", "status": "COMPLETED", "result": "agree", "create_time": "2026-11-06T01:00:00Z",
        "updated_at": "2026-11-06T01:05:00Z", "raw_payload": {}, "form_component_values": components or [
            field("DDSelectField_1ATVGGUYKEZK0", "Servicios Públicos y Operativos/公用运营"),
            field("DDSelectField_J6QIEDKOACG0", "CFE联邦电力委员会"),
            field("DDSelectField_F7MDR4TJ1JS0", "Peso比索"), table()]}


def approve_finance(row, finish="2026-11-16T12:00:00Z"):
    row["raw_payload"] = {"tasks": [{"activityId": c.FINANCE_ACTIVITY_ID, "taskId": "finance-task",
        "taskGroupName": "MX财务Finanzas", "status": "COMPLETED", "result": "AGREE", "finishTime": finish}]}
    return row


class MexicoContractTests(unittest.TestCase):
    def setUp(self):
        translations = patch("deeplinkerp_branding.services.operating_expense_contract._", side_effect=lambda text: text)
        translations.start()
        self.addCleanup(translations.stop)

    def test_sample_electricity_is_one_detail_not_paid_and_has_no_guessed_period(self):
        source, lines = c.project(approval())
        self.assertEqual(source["total_amount"], "251739.00")
        self.assertEqual(source["request_date"], "2026-11-05")
        self.assertEqual(lines[0]["category"], "utilities")
        self.assertEqual(lines[0]["subcategory"], "electricity")
        self.assertEqual(lines[0]["source_entity"], "LATINGO MX")
        result = c.apply_review(lines[0], None, {}, "v1")
        self.assertEqual(result["issues"], ["company", "period"])
        self.assertFalse(any(k in source for k in ("paid_amount", "pending_amount")))

    def test_category_dictionary_preserves_existing_groups_and_adds_operating_and_purchase(self):
        self.assertEqual({row[0] for row in c.CATEGORIES}, {"personnel","tax","utilities","logistics","services","administration","selling","procurement","source_other"})
        self.assertIn("salary", c.SUBCATEGORIES)
        self.assertEqual(c.SUBCATEGORIES["purchase_mro"][0], "procurement")

    def test_company_identity_uses_label_and_kind_not_reused_option_key(self):
        self.assertNotEqual(c.entity_key("entity", "LATINGO MX"), c.entity_key("entity", "YW Centro MX"))
        self.assertNotEqual(c.entity_key("entity", "MX"), c.entity_key("department", "MX"))

    def test_department_does_not_infer_legal_company(self):
        _, lines = c.project(approval([field("DDSelectField_F7MDR4TJ1JS0", "比索Peso"),
            table("TableField_ENJNMIBMUAO0", entity=[{"name": "YUEWEI MX", "id": "111"}])]))
        line = c.apply_review(lines[0], None, {}, "v1")
        self.assertEqual(line["source_entity"], "YUEWEI MX")
        self.assertEqual(line["entity_kind"], "department")
        self.assertIsNone(line["company"])
        self.assertEqual(line["subcategory"], "salary")

    def test_salary_and_social_tables_keep_separate_categories_and_do_not_add_calculated_total(self):
        source, lines = c.project(approval([field("DDSelectField_F7MDR4TJ1JS0", "Peso比索"),
            table("TableField_ENJNMIBMUAO0", amount="100.10"),
            table("TableField_21SE4NJZE8W00", amount="20.20"),
            field("CalculateField_GZXGU5OSNWO0", "120.30", "CalculateField")]))
        self.assertEqual(source["total_amount"], "120.30")
        self.assertEqual([l["subcategory"] for l in lines], ["salary", "social"])
        self.assertTrue(all(not l["base_issues"] for l in lines))

    def test_conflicting_total_blocks_all_lines(self):
        row = approval()
        row["form_component_values"].append(field("CalculateField_GZXGU5OSNWO0", "1", "CalculateField"))
        _, lines = c.project(row)
        self.assertIn("total", lines[0]["base_issues"])

    def test_multicompany_header_is_not_repeated_or_automatically_allocated(self):
        source, lines = c.project(approval([field("DDSelectField_E8MD2JTMHWO", "Salario工资"),
            field("DDSelectField_F7MDR4TJ1JS0", "Peso比索"), field("DDMultiSelectField_RWULY2WAUCG0", '["A", "B"]'),
            field("MoneyField_1F4PBZ6A5UOW", "100", "MoneyField")]))
        self.assertEqual(len(lines), 1)
        line = c.apply_review(lines[0], None, {lines[0]["entity_key"]: "Company A"}, "v1")
        self.assertIn("allocation", line["issues"])
        self.assertIsNone(line["company"])

    def test_row_numbers_survive_reordering(self):
        row = approval()
        entry = row["form_component_values"][-1]
        values = json.loads(entry["value"])
        other = copy.deepcopy(values[0]); other["rowNumber"] = "row-2"
        values.append(other); entry["value"] = json.dumps(values)
        _, before = c.project(row)
        entry["value"] = json.dumps(list(reversed(values)))
        _, after = c.project(row)
        self.assertEqual({r["line_key"] for r in before}, {r["line_key"] for r in after})

    def test_duplicate_row_numbers_block_every_line(self):
        row = approval()
        component = row["form_component_values"][-1]
        values = json.loads(component["value"])
        component["value"] = json.dumps(values * 2)
        _, lines = c.project(row)
        self.assertEqual(len({l["line_key"] for l in lines}), 2)
        self.assertTrue(all("identity" in line["base_issues"] for line in lines))

    def test_missing_row_identity_cannot_be_confirmed(self):
        _, lines = c.project(approval([table(number=None)]))
        self.assertIn("identity", lines[0]["base_issues"])

    def test_changed_amount_invalidates_manual_company_but_keeps_source_finance_period(self):
        _, lines = c.project(approve_finance(approval()))
        before = {**lines[0], "reviewed_fingerprint": lines[0]["row_fingerprint"], "company_basis": "manual", "company": "Company", "period_month": "2026-11-01", "period_start": "2026-11-01", "period_end": "2026-11-30"}
        unchanged = c.apply_review(lines[0], before, {}, "v2")
        self.assertEqual(unchanged["review_state"], "confirmed")
        changed = approve_finance(approval()); changed["form_component_values"][-1] = table(amount="2")
        _, lines = c.project(changed)
        after = c.apply_review(lines[0], before, {}, "v2")
        self.assertIsNone(after["company"])
        self.assertEqual(after["period_month"], "2026-11-01")
        self.assertIn("stale", after["issues"])

    def test_approval_status_change_preserves_economic_confirmation(self):
        row = approve_finance(approval()); _, lines = c.project(row)
        before = {**lines[0], "reviewed_fingerprint": lines[0]["row_fingerprint"], "period_month": "2026-11-01", "period_start": "2026-11-01", "period_end": "2026-11-30"}
        row.update(status="RUNNING", result="NONE", updated_at="2026-11-07T00:00:00Z")
        _, after = c.project(row)
        result = c.apply_review(after[0], before, {after[0]["entity_key"]: "Company"}, "v2")
        self.assertEqual(result["review_state"], "confirmed")

    def test_finance_completion_sets_local_calendar_month_even_while_flow_is_running(self):
        row = approve_finance(approval(), "2026-11-01T01:00:00Z")
        row.update(status="RUNNING", result="NONE", create_time="2026-10-20T12:00:00Z")
        source, lines = c.project(row)
        self.assertEqual(source["approval_status"], "pending")
        self.assertEqual(source["finance_approved_date"], "2026-10-31")
        result = c.apply_review(lines[0], None, {lines[0]["entity_key"]: "Company"}, "v1")
        self.assertEqual(result["period_month"], "2026-10-01")
        self.assertEqual(result["period_end"], "2026-10-31")
        self.assertEqual(result["review_state"], "confirmed")

    def test_only_verified_finance_activity_and_agree_completion_can_assign_period(self):
        for change in ({"activityId": "ceo"}, {"status": "RUNNING"}, {"result": "REFUSE"},
                       {"status": "CANCELED", "result": "REDIRECTED"}, {"finishTime": None},
                       {"finishTime": "2026-11-01T00:00:00"}, {"finishTime": "invalid"}):
            row = approve_finance(approval())
            row["raw_payload"]["tasks"][0].update(change)
            with self.subTest(change=change):
                period, _ = c.finance_period(row)
                self.assertIsNone(period["period_month"])

    def test_finance_operation_must_agree_with_completed_task(self):
        row = approve_finance(approval())
        row["raw_payload"]["operationRecords"] = [{"activityId": c.FINANCE_ACTIVITY_ID,
            "type": "EXECUTE_TASK_NORMAL", "result": "AGREE", "date": "2026-11-16T12:00:00Z"}]
        self.assertEqual(c.finance_period(row)[0]["period_month"], "2026-11-01")
        row["raw_payload"]["operationRecords"][0]["date"] = "2026-10-16T12:00:00Z"
        period, issues = c.finance_period(row)
        self.assertIsNone(period["period_month"])
        self.assertIn("finance", issues)

    def test_finance_action_time_wins_over_later_task_closure_at_month_boundary(self):
        row = approve_finance(approval(), "2026-11-01T06:00:00Z")
        row['create_time'] = '2026-10-20T12:00:00Z'
        row['raw_payload']['tasks'][0]['createTime'] = '2026-10-31T12:00:00Z'
        row['raw_payload']['operationRecords'] = [{"activityId":c.FINANCE_ACTIVITY_ID,
            "type":"EXECUTE_TASK_NORMAL", "result":"AGREE", "date":"2026-11-01T05:59:00Z"}]
        period, issues = c.finance_period(row)
        self.assertEqual(issues, [])
        self.assertEqual(period['finance_approved_at'], '2026-11-01T05:59:00Z')
        self.assertEqual(period['finance_approved_date'], '2026-10-31')
        self.assertEqual(period['period_month'], '2026-10-01')

    def test_redirected_task_requires_its_replacement_and_reopened_task_removes_period(self):
        row = approve_finance(approval())
        row["raw_payload"]["tasks"].append({"activityId": c.FINANCE_ACTIVITY_ID, "taskId": "redirected",
            "status": "CANCELED", "result": "REDIRECTED"})
        self.assertEqual(c.finance_period(row)[0]["period_month"], "2026-11-01")
        row["raw_payload"]["tasks"].append({"activityId": c.FINANCE_ACTIVITY_ID, "taskId": "reopened",
            "status": "RUNNING", "result": "NONE"})
        self.assertIsNone(c.finance_period(row)[0]["period_month"])

    def test_withdrawal_rejection_and_termination_remove_finance_period(self):
        for update in ({"deleted_at":"2026-11-17"}, {"status":"TERMINATED"}, {"result":"REFUSE"}):
            row = approve_finance(approval()); row.update(update)
            self.assertIsNone(c.finance_period(row)[0]["period_month"])

    def test_legacy_manual_period_never_overrides_current_finance_evidence(self):
        row = approve_finance(approval()); _, lines = c.project(row)
        old = {**lines[0], "period_month":"2026-09-01", "reviewed_fingerprint":lines[0]["row_fingerprint"],
               "company":"Company", "company_basis":"manual"}
        current = c.apply_review(lines[0], old, {}, "v1")
        self.assertEqual(current["period_month"], "2026-11-01")
        row["raw_payload"]["tasks"] = []
        _, lines = c.project(row)
        current = c.apply_review(lines[0], old, {}, "v1")
        self.assertEqual(current["company"], "Company")
        self.assertIsNone(current["period_month"])
        self.assertIn("period", current["issues"])

    def test_all_fourteen_subcategories_match_current_labels(self):
        for sub, (category, _, aliases) in c.SUBCATEGORIES.items():
            for alias in aliases:
                with self.subTest(sub=sub, alias=alias):
                    row = approval([field("DDSelectField_E8MD2JTMHWO", alias), field("DDSelectField_F7MDR4TJ1JS0", "Dólar美元"), table()])
                    _, lines = c.project(row)
                    self.assertEqual(lines[0]["subcategory"], sub)
                    self.assertEqual(lines[0]["category"], category)
                    self.assertEqual(lines[0]["currency"], "USD")

    def test_hidden_transport_subtype_does_not_change_ocean_category(self):
        row = approval([field("DDSelectField_1GVCIZGB2H9C0", "Marítimo海运"),
            field("DDSelectField_1FC6WHB8HPNK0", "Gastos locales/本地费用"), table()])
        _, lines = c.project(row)
        self.assertEqual(lines[0]["subcategory"], "ocean")

    def test_unknown_and_conflicting_subcategory_require_review(self):
        row = approval(); row["form_component_values"].append(field("DDSelectField_E8MD2JTMHWO", "Salario工资"))
        _, lines = c.project(row)
        self.assertIn("category", lines[0]["base_issues"])

    def test_exact_amount_rejects_rounding_nan_negative_and_float(self):
        for value in ("NaN", "1.001", "-1", 1.2, None, True, "100000000000000", "1000000000000"):
            self.assertIsNone(c.exact_amount(value))
        self.assertEqual(c.exact_amount("0"), "0.00")
        self.assertEqual(c.exact_amount("0.10"), "0.10")

    def test_application_total_cannot_overflow_frappe_currency_storage(self):
        row=approval()
        component=row['form_component_values'][-1]
        a=json.loads(table(amount='600000000000')['value'])[0]
        b=copy.deepcopy(a);b['rowNumber']='row-2'
        component['value']=json.dumps([a,b])
        source,lines=c.project(row)
        self.assertIsNone(source['total_amount'])
        self.assertTrue(all('total' in line['base_issues'] for line in lines))

    def test_zero_amount_is_not_mistaken_for_missing_amount(self):
        row=approval();row["form_component_values"][-1]=table(amount=0)
        _, lines=c.project(row)
        self.assertEqual(lines[0]["amount_raw"], "0")
        self.assertNotIn("amount",lines[0]["base_issues"])

    def test_raw_approval_conflict_never_counts_as_approved(self):
        row=approval();row["raw_payload"]={"status":"RUNNING"}
        source,lines=c.project(row)
        self.assertEqual(source["approval_status"],"unknown")
        self.assertIn("structure",lines[0]["base_issues"])

    def test_withdrawn_terminated_and_unknown_statuses_are_explicit(self):
        self.assertEqual(c.approval_status("COMPLETED","agree",True),"withdrawn")
        self.assertEqual(c.approval_status("TERMINATED",None),"terminated")
        self.assertEqual(c.approval_status("COMPLETED","OTHER"),"unknown")

    def test_cross_corporation_or_template_is_rejected(self):
        for fieldname in ("corp_id","process_code"):
            with self.assertRaises(ValueError):c.project({**approval(),fieldname:"other"})

    def test_expense_period_is_explicit_and_can_span_months(self):
        period=c.validate_period("2026-10","2026-09-30","2026-10-31","custom")
        self.assertEqual(period["period_month"],"2026-10-01")
        for args in [("2026-11","2026-10-01","2026-10-31","month"),("2026-13","2026-11-01","2026-11-30","month"),("2026-11","2026-11-30","2026-11-01","month"),("2026-11","2026-11-01","2026-11-30","inferred")]:
            with self.assertRaises(ValueError):c.validate_period(*args)

    def test_naive_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):c.project({**approval(),"create_time":"2026-11-01T00:00:00"})

    def test_unknown_table_and_malformed_cells_never_disappear_as_confirmed(self):
        row=approval();row["form_component_values"].append(field("TableField_UNKNOWN", '[{"rowValue":[]}]',"TableField"))
        _,lines=c.project(row)
        self.assertIn("structure",lines[0]["base_issues"])

    def test_does_not_mutate_original_source(self):
        row=approval(); before=copy.deepcopy(row);c.project(row)
        self.assertEqual(row,before)


class MexicoReviewPersistenceTests(unittest.TestCase):
    def test_changed_facts_require_explicit_review_across_repeated_sync_and_reprojection(self):
        row=approve_finance(approval())
        _, lines=c.project(row)
        mappings={lines[0]['entity_key']:'Company'}
        previous=c.apply_review(lines[0], None, mappings, 'v1')
        previous['reviewed_fingerprint']=lines[0]['row_fingerprint']
        row['form_component_values'][-1]=table(amount='2')
        _, lines=c.project(row)
        for version in ('v1','v1','v2','v2'):
            previous=c.apply_review(lines[0], previous, mappings, version)
            self.assertEqual(previous['review_state'],'needs_review')
            self.assertIn('stale',previous['issues'])
            previous['issues']=json.dumps(previous['issues'])
        previous['reviewed_fingerprint']=lines[0]['row_fingerprint']
        previous['issues']='[]'
        confirmed=c.apply_review(lines[0], previous, mappings, 'v2')
        self.assertEqual(confirmed['review_state'],'confirmed')
        self.assertNotIn('stale',confirmed['issues'])
