import csv
from decimal import Decimal
from io import StringIO
import unittest

from deeplinkerp_branding.services.cn_cash_book_import import HEADER, check_manifest, plan_import, read_journal


class ImportTests(unittest.TestCase):
    def sample(self, last="7.00"):
        text = StringIO()
        writer = csv.writer(text)
        writer.writerow(["Book"])
        writer.writerow(HEADER)
        writer.writerow([""] * 8 + ["承上年期末余额", "", "", "0.00"])
        for income, expense, balance in [("10.00", "", "10.00"), ("", "1.50", "8.50"), ("", "1.50", last)]:
            writer.writerow(["2026","1","1","REF","A","B","C","","Test",income,expense,balance,"",""])
        writer.writerow([""] * 11 + ["7.00"])
        text.seek(0)
        return read_journal(text, "Book")

    def test_same_source_cannot_replace_rows_or_move_to_another_account(self):
        rows = self.sample()
        prior = [dict(r, company="C", account="BANK", currency="CNY") for r in rows]
        check_manifest(prior, rows, "C", "BANK")
        with self.assertRaisesRegex(ValueError,"another"):
            check_manifest(prior, rows, "C", "OTHER")
        with self.assertRaisesRegex(ValueError,"absent"):
            check_manifest(prior, rows[1:], "C", "BANK")
        with self.assertRaisesRegex(ValueError,"date has changed"):
            check_manifest([dict(prior[0], posting_date="2027-01-01")], rows, "C", "BANK")

    def test_distinct_legs_same_ref_and_even_identical_rows_survive_rerun(self):
        rows = self.sample()
        self.assertEqual((len(set(r["import_key"] for r in rows)), rows[-1]["balance"]), (3, Decimal(7)))
        self.assertEqual(plan_import(rows, [], "BANK"), rows)
        stored = [dict(r, account="BANK") for r in rows]
        self.assertEqual(plan_import(rows, stored, "BANK"), [])
        self.assertEqual(plan_import(rows, stored[:1], "BANK"), rows[1:])
        self.assertEqual(rows[2]["journal_detail"], "C")

    def test_changed_manual_data_blocks_instead_of_overwriting(self):
        rows = self.sample()
        with self.assertRaisesRegex(ValueError,"has changed"):
            plan_import(rows, [dict(rows[0], account="BANK", summary="manual")], "BANK")

    def test_possible_unassigned_legacy_duplicate_blocks(self):
        rows = self.sample()
        with self.assertRaisesRegex(ValueError,"Possible existing"):
            plan_import(rows, [dict(rows[0], account=None, import_key=None)], "BANK")
        with self.assertRaisesRegex(ValueError,"Possible existing"):
            plan_import(rows, [dict(rows[0], account="BANK", amount="0.01", import_key=None)], "BANK")
        self.assertEqual(plan_import(rows, [dict(rows[0], account="OTHER", import_key=None)], "BANK"), rows)

    def test_wrong_source_or_system_balance_blocks(self):
        with self.assertRaisesRegex(ValueError,"balance mismatch"):
            self.sample("8.00")
        rows = self.sample()
        extra = dict(rows[0], posting_date="2025-12-01", account="BANK", import_key=None, dingtalk_approval_number="")
        with self.assertRaisesRegex(ValueError,"balance differs"):
            plan_import(rows, [extra], "BANK")
