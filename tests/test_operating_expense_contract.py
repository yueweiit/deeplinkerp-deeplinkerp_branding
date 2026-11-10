"""Financial identity must survive archival copies without tolerating changed facts."""
import copy
import unittest

try:
    from deeplinkerp_branding.services import operating_expense_contract as contract
except ImportError:
    contract = None


def source():
    return {"source_system": "cashier-payment-archive", "source_id": "root-1", "version": "v1",
        "updated_at": "2026-10-01T00:00:00Z", "application_type": "payment", "source_company": "legal-1",
        "source_sheet": "weekly", "amount": "100.00", "currency": "CNY", "request_date": "2026-10-01",
        "payee_name": "Vendor", "applicant": "Person", "approvals": {"eligibility": "eligible", "raw": {"status": "COMPLETED", "result": "agree"}},
        "payments": [{"source_id": "p1", "amount": "20", "currency": "CNY", "payment_date": "2026-10-02", "evidence_status": "recorded"}],
        "attachments": [{"source_id": "a1", "version": "a-v1", "filename": "invoice.pdf", "url": "/api/integrations/erp/attachments/12"}]}


class OperatingExpenseContractTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(contract, "operating expense contract is missing")

    def test_payment_status_never_labels_unknown_balances_as_unpaid(self):
        for value in (None, "", "invalid", "-1"):
            for field in ("paid_amount", "pending_amount"):
                row = {"paid_amount":"0", "pending_amount":"100", "source_status":"未付款", field:value}
                self.assertEqual(contract.payment_status(row), "付款待核对")
        self.assertEqual(contract.payment_status({"paid_amount":"0", "pending_amount":"100", "source_status":"未付款"}), "未付款")
        self.assertEqual(contract.payment_status({"paid_amount":"100", "pending_amount":"0", "source_status":"已付款"}), "已付款")

    def test_technical_copy_and_new_payment_do_not_change_expense_identity(self):
        before = source()
        after = copy.deepcopy(before)
        after.update(version="v2", updated_at="2026-10-03T00:00:00Z", source_request_id="999", source_sheet="next-week")
        after["payments"].append({"source_id": "p2", "amount": "30", "currency": "CNY"})
        after["attachments"][0].update(url="/api/integrations/erp/attachments/999", provenance=[{"url": "x"}])
        self.assertEqual(contract.expense_facts(before), contract.expense_facts(after))
        after["amount"] = "101"
        self.assertNotEqual(contract.expense_facts(before), contract.expense_facts(after))

    def test_payment_intent_survives_cashier_completion_but_not_changed_financial_facts(self):
        before = source(); before.update(source_system="dingtalk-oa", oa_identity={"corp_id":"corp", "process_instance_id":"instance"})
        before["approvals"] = {"eligibility":"blocked", "raw":{"status":"RUNNING", "result":"NONE"}}
        after = copy.deepcopy(before)
        after["approvals"] = {"eligibility":"eligible", "raw":{"status":"COMPLETED", "result":"agree"}}
        self.assertEqual(contract.payment_intent_facts(before), contract.payment_intent_facts(after))
        self.assertNotEqual(contract.expense_facts(before), contract.expense_facts(after), "voucher approval binding stays strict")
        for change in ({"amount":"101"}, {"source_company":"other"}, {"payee_name":"other"}, {"oa_identity":{"corp_id":"other", "process_instance_id":"instance"}}):
            self.assertNotEqual(contract.payment_intent_facts(before), contract.payment_intent_facts({**after, **change}))

    def test_display_organization_does_not_change_existing_accounting_identity(self):
        before = {**source(), "source_system": "dingtalk-oa", "source_company": "legacy mapping organization"}
        after = {**before, "source_company": "original application organization",
                 "company_mapping_source": before["source_company"]}
        self.assertEqual(contract.expense_facts(before), contract.expense_facts(after))
        self.assertNotEqual(contract.expense_facts(before), contract.expense_facts({**after, "company_mapping_source": None}))

    def test_payment_decision_rejects_non_boolean_oa_authorization(self):
        item = {**source(), "source_system": "dingtalk-oa"}
        for value in (1, 0, "true", None):
            with self.subTest(value=value):
                decision = contract.payment_decision({**item, "payment_eligibility": {"can_register_payment": value}})
                self.assertFalse(decision["can_register_payment"])
        self.assertTrue(contract.payment_decision({**item, "payment_eligibility": {"can_register_payment": True}})["can_register_payment"])
        removed = {**item, "approvals":{"eligibility":"blocked", "raw":{"scope":"withdrawn"}}, "payment_eligibility":{"can_register_payment":True}}
        self.assertFalse(contract.payment_decision(removed)["can_register_payment"])

    def test_quick_tabs_separate_approval_payment_and_unknown_history(self):
        row = {"company":"Legal", "approval_state":"pending", "pending_amount":"500", "paid_amount":"300",
               "payment_eligibility":{"can_register_payment":True}}
        self.assertTrue(contract.quick_tab_matches(row, "pending_payment"))
        self.assertTrue(contract.quick_tab_matches(row, "approvals_running"))
        self.assertFalse(contract.quick_tab_matches(row, "paid"))
        self.assertFalse(contract.quick_tab_matches(row, "reconciliation"))
        self.assertTrue(contract.quick_tab_matches({**row,"paid_amount":None,"pending_amount":None}, "reconciliation"))
        self.assertFalse(contract.quick_tab_matches({**row,"paid_amount":None,"pending_amount":None}, "paid"))
        self.assertTrue(contract.quick_tab_matches({**row,"pending_amount":"0"}, "paid"))
        self.assertFalse(contract.quick_tab_matches({**row,"payment_eligibility":{"can_register_payment":False}}, "pending_payment"))
        self.assertTrue(contract.quick_tab_matches({**row, "issues": "原法律公司映射已移除，需要复核"}, "reconciliation"))

    def test_company_overview_keeps_disputed_attribution_out_of_subsidiary_totals(self):
        rows = [
            {"company": "A", "currency": "CNY", "amount": "100.00", "paid_amount": "20.00", "pending_amount": "80.00"},
            {"company": "A", "currency": "USD", "amount": "10", "paid_amount": None, "pending_amount": None},
            {"company": "A", "issues": "原法律公司映射已移除，需要复核", "currency": "CNY", "amount": "200", "paid_amount": "0", "pending_amount": "200", "approval_evidence_missing": True},
            {"company": "", "currency": "CNY", "amount": "300", "paid_amount": None, "pending_amount": None},
        ]
        overview, review = contract.company_overview(rows)
        self.assertEqual([entry["company"] for entry in overview], ["A", None])
        self.assertEqual(overview[0]["currencies"]["CNY"]["amount"], "100.00")
        self.assertEqual(overview[0]["currencies"]["USD"]["paid_amount"], None)
        self.assertEqual(overview[0]["currency_counts"], {"CNY": 1, "USD": 1})
        self.assertEqual(overview[1]["currencies"]["CNY"]["amount"], "500")
        self.assertEqual(review, {"unmapped": 1, "stale": 1, "payment_unknown": 2, "approval_evidence_missing": 1})

    def test_current_mapping_overrides_cached_review_warning(self):
        row = {"company": "A", "issues": "原法律公司映射已移除，需要复核"}
        self.assertEqual(contract.company_review_state(row, "A"), "confirmed")
        self.assertEqual(contract.company_review_state({"company": "A", "issues": ""}, "B"), "stale")
        self.assertEqual(contract.company_review_state({"company": "A", "issues": ""}, None), "stale")
        self.assertEqual(contract.company_review_state({"company": "", "issues": ""}, "A"), "unmapped")
        class SourceDoc:
            def get(self, key):
                return {"company": "A"}.get(key)

        self.assertEqual(contract.company_review_state(SourceDoc(), "B"), "stale")
        row.update(issues=contract.current_company_issues(row["issues"], "A", "A"), currency="CNY", amount="100", paid_amount="0", pending_amount="100")
        overview, review = contract.company_overview([row])
        self.assertEqual(overview[0]["company"], "A")
        self.assertEqual(review["stale"], 0)

    def test_current_mapping_warning_replaces_only_obsolete_cached_company_issue(self):
        issues = "付款日期待核对；原法律公司映射已移除，需要复核"
        self.assertEqual(contract.current_company_issues(issues, "A", "A"), "付款日期待核对")
        self.assertEqual(contract.current_company_issues(issues, "A", "B"),
                         "付款日期待核对；法律公司映射已变更，需要财务复核")
        self.assertEqual(contract.current_company_issues("", "A", None), "原法律公司映射已移除，需要复核")

    def test_sync_refreshes_changed_review_state_once_and_clears_restored_mapping(self):
        needed = contract.company_sync_needed
        self.assertTrue(needed("v1", "A", "", "v1", "B"))
        self.assertFalse(needed("v1", "A", "法律公司映射已变更，需要财务复核", "v1", "B"))
        self.assertTrue(needed("v1", "A", "法律公司映射已变更，需要财务复核", "v1", "A"))
        self.assertTrue(needed("v1", "A", "法律公司映射已变更，需要财务复核", "v1", None))
        self.assertFalse(needed("v1", "A", "原法律公司映射已移除，需要复核", "v1", None))
        self.assertTrue(needed("v1", "", "未映射法律公司", "v1", "A"))
        self.assertTrue(needed("v1", "A", "", "v2", "A"))

    def test_currency_totals_never_turn_unknown_history_into_zero(self):
        rows = [{"currency":"CNY","amount":"100","paid_amount":None,"pending_amount":None},
                {"currency":"MXN","amount":"60","paid_amount":"10","pending_amount":"50"}]
        totals = contract.currency_totals(rows)
        self.assertIsNone(totals["CNY"]["paid_amount"])
        self.assertIsNone(totals["CNY"]["pending_amount"])
        self.assertEqual(totals["CNY"]["amount"], "100")
        self.assertEqual(totals["MXN"]["paid_amount"], "10")
        mixed = contract.currency_totals(rows + [{"currency":"CNY","amount":"200","paid_amount":"30","pending_amount":"170"}])
        self.assertIsNone(mixed["CNY"]["paid_amount"])
        self.assertEqual(mixed["CNY"]["known_totals"]["paid_amount"], "30")

    def test_pending_work_includes_blocked_and_unknown_unfinished_requests(self):
        base = {"approval_state": "approved", "paid_amount": "0", "pending_amount": "100",
                "payment_eligibility": {"can_register_payment": False}}
        for amounts in ({}, {"paid_amount": "30", "pending_amount": "70"},
                        {"paid_amount": None, "pending_amount": None}):
            row = {**base, **amounts}
            self.assertTrue(contract.quick_tab_matches(row, "pending_work"))
        for state in ("rejected", "terminated", "withdrawn"):
            self.assertFalse(contract.quick_tab_matches({**base, "approval_state": state}, "pending_work"))
        self.assertFalse(contract.quick_tab_matches({**base, "paid_amount": "100", "pending_amount": "0"}, "pending_work"))
        self.assertFalse(contract.quick_tab_matches({**base, "pending_amount": "-5"}, "pending_work"))

    def test_worklist_statistics_share_one_classification_and_isolate_overpayment(self):
        rows = [
            {"approval_state": "pending", "currency": "CNY", "amount": "100", "paid_amount": None, "pending_amount": None},
            {"approval_state": "approved", "company": "Legal", "currency": "CNY", "amount": "100", "paid_amount": "30", "pending_amount": "70"},
            {"approval_state": "approved", "company": "Legal", "currency": "CNY", "amount": "100", "paid_amount": "105", "pending_amount": "-5"},
            {"approval_state": "rejected", "currency": "CNY", "amount": "100", "paid_amount": None, "pending_amount": None},
        ]
        selected, counts, totals = contract.worklist_summary(rows, "pending_work")
        self.assertEqual(selected, rows[:2])
        self.assertEqual(counts["all"], 4)
        self.assertEqual(counts["pending_work"], 2)
        self.assertEqual(counts["reconciliation"], 3)
        self.assertEqual(totals["CNY"]["known_totals"]["pending_amount"], "70")
        all_totals = contract.currency_totals(rows)
        self.assertIsNone(all_totals["CNY"]["pending_amount"])
        self.assertEqual(all_totals["CNY"]["anomaly_count"], 1)
        self.assertEqual(all_totals["CNY"]["anomaly_totals"]["pending_amount"], "-5")
        self.assertEqual(all_totals["CNY"]["known_totals"]["pending_amount"], "70")

    def test_blocker_uses_current_ledger_balances_not_cached_source_history(self):
        row = {"company": "Legal", "approval_state": "approved", "currency": "CNY", "amount": "100",
               "paid_amount": "30", "pending_amount": "70", "blocking_reason": "历史付款待核对",
               "payment_eligibility": {"can_register_payment": True}}
        contract.worklist_summary([row], "all")
        self.assertEqual(row["blocking_reason"], "")
        row.update(paid_amount=None, pending_amount=None)
        contract.worklist_summary([row], "all")
        self.assertEqual(row["blocking_reason"], "历史付款待核对")
        row.update(paid_amount="30", pending_amount="70", issues="原法律公司映射已移除，需要复核")
        contract.worklist_summary([row], "all")
        self.assertIn("法律公司归属待复核", row["blocking_reason"])

    def test_negative_paid_history_is_not_a_normal_payable_total(self):
        total = contract.currency_totals([{"currency": "CNY", "amount": "100", "paid_amount": "-10", "pending_amount": "110"}])["CNY"]
        self.assertTrue(total["incomplete"])
        self.assertIsNone(total["paid_amount"])
        self.assertIsNone(total["pending_amount"])
        self.assertEqual(total["anomaly_count"], 1)
        self.assertEqual(total["anomaly_totals"], {"paid_amount": "-10", "pending_amount": "110"})

    def test_payment_copy_identity_excludes_timestamps_but_retains_evidence(self):
        payment = source()["payments"][0]
        copied = {**payment, "version": "weekly", "updated_at": "later", "source_request_id": "888", "provenance": []}
        self.assertEqual(contract.payment_facts(payment), contract.payment_facts(copied))
        copied["evidence_status"] = "conflict"
        self.assertNotEqual(contract.payment_facts(payment), contract.payment_facts(copied))

    def test_oa_cashier_updates_preserve_expense_confirmation_and_event(self):
        before = source()
        before["source_system"] = "dingtalk-oa"
        before["approvals"]["raw"]["cashier_finance_review"] = "待付款"
        mapping = {"party": "Supplier", "payments": {"p1": {"bank_amount": "20"}, "p2": {"bank_amount": "30"}}}
        after = copy.deepcopy(before)
        after["approvals"]["raw"].update(cashier_finance_review="已付款", cashier_general_manager_approval="同意付款")
        after["payments"].append({"source_id": "p2", "amount": "30", "currency": "CNY", "payment_date": "2026-10-03", "evidence_status": "recorded"})
        self.assertEqual(contract.expense_facts(before), contract.expense_facts(after))
        self.assertEqual(contract.event_fingerprint(before, mapping), contract.event_fingerprint(after, mapping))
        self.assertEqual(contract.event_fingerprint(before, mapping, "p1"), contract.event_fingerprint(after, mapping, "p1"))
        after["payments"][0]["bank_reference"] = "new-bank-proof"
        self.assertEqual(contract.expense_facts(before), contract.expense_facts(after))
        self.assertNotEqual(contract.event_fingerprint(before, mapping, "p1"), contract.event_fingerprint(after, mapping, "p1"))
        self.assertEqual(before["approvals"]["raw"]["cashier_finance_review"], "待付款")
        self.assertEqual(after["approvals"]["raw"]["cashier_finance_review"], "已付款")

    def test_original_oa_changes_and_legacy_cashier_approvals_still_invalidate_expense(self):
        before = source()
        before["source_system"] = "dingtalk-oa"
        for field, value in (("amount", "101"), ("source_company", "legal-2"), ("approvals", {"eligibility": "blocked", "raw": {"status": "COMPLETED", "result": "refuse"}}),
                             ("approvals", {"eligibility": "eligible", "raw": {"status": "COMPLETED", "result": "agree", "finance_review": "changed-original"}})):
            with self.subTest(field=field, value=value):
                after = {**before, field: value}
                self.assertNotEqual(contract.expense_facts(before), contract.expense_facts(after))
                self.assertNotEqual(contract.event_fingerprint(before, {}), contract.event_fingerprint(after, {}))
        legacy = source()
        after = copy.deepcopy(legacy)
        after["approvals"]["raw"]["cashier_finance_review"] = "changed-legacy"
        self.assertNotEqual(contract.expense_facts(legacy), contract.expense_facts(after))
        self.assertNotEqual(contract.event_fingerprint(legacy, {}), contract.event_fingerprint(after, {}))

    def test_money_rejects_nonfinite_missing_float_and_excess_precision(self):
        for value in (None, "", "NaN", "Infinity", 0.1, "0.001", "1e100"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                contract.money(value, precision=2)
        self.assertEqual(str(contract.money("12.30", precision=2)), "12.30")

    def test_source_validation_rejects_conflicts_and_duplicate_payment_roots(self):
        for mutation in ({"amount": "NaN"}, {"currency": "unknown"}, {"source_id": ""}, {"application_type": "invented"},
                         {"approvals": ["bad"]}, {"approvals": {"eligibility": "eligible", "raw": ["bad"]}},
                         {"payments": ["bad"]}, {"attachments": ["bad"]}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                contract.validate_source({**source(), **mutation})
        duplicate = source()
        duplicate["payments"] *= 2
        with self.assertRaises(ValueError):
            contract.validate_source(duplicate)
        self.assertEqual(contract.validate_source(source())["source_id"], "root-1")

    def test_typed_attachment_paths_do_not_accept_arbitrary_urls(self):
        self.assertEqual(contract.attachment_path("/api/integrations/erp/payment-vouchers/12"), "/api/integrations/erp/payment-vouchers/12")
        for path in ("https://evil.test/a", "//127.0.0.1/a", "/api/integrations/erp/attachments/12?token=x", "/api/integrations/erp/attachments/../12"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                contract.attachment_path(path)

    def test_original_oa_contract_and_scoped_archive_token_are_supported(self):
        item = source(); item["source_system"] = "dingtalk-oa"
        self.assertEqual(contract.validate_source(item)["source_system"], "dingtalk-oa")
        self.assertEqual(contract.attachment_path("/oa-archive/" + "a" * 64), "/oa-archive/" + "a" * 64)

    def test_financial_fingerprint_normalizes_decimal_formatting(self):
        before = source()
        after = {**before, "amount": "100"}
        mapping = {"party": "Supplier", "expense_lines": [{"amount": "100", "source_amount": "100", "exchange_rate": "1"}]}
        formatted = {"party": "Supplier", "expense_lines": [{"amount": "100.00", "source_amount": "100.0", "exchange_rate": "1.0"}]}
        self.assertEqual(contract.event_fingerprint(before, mapping), contract.event_fingerprint(after, formatted))

    def test_malformed_cached_attachments_fail_closed_with_a_validation_error(self):
        for attachments in (None,"bad",["bad"]):
            with self.subTest(attachments=attachments),self.assertRaises(ValueError):
                contract.expense_facts({**source(),"attachments":attachments})


if __name__ == "__main__":
    unittest.main()
