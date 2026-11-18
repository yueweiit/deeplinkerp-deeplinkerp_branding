"""Alias recommendations stay separate from financial assignment and permission scope."""
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from pypika import Query, Table
from pypika.queries import QueryBuilder
from deeplinkerp_branding.services import mexico_expense_mapping as mapping
from deeplinkerp_branding.services import mexico_expenses as service
from deeplinkerp_branding.services import mexico_expense_contract as contract


class SuggestionTests(unittest.TestCase):
    def test_case_width_and_spacing_are_recommendations_only(self):
        self.assertEqual(mapping.suggest_companies("Ａｍｉｇｏｍａｒｔ ", "entity", ["AmigoMart"], []),
                         [{"company": "AmigoMart", "match": "normalized"}])
        self.assertNotEqual(contract.entity_key("entity", "Amigomart"), contract.entity_key("entity", "AmigoMart"))
        self.assertEqual(mapping.suggest_companies("UV IMPRESION MX", "entity", ["UV IMPRESION"], []),
                         [{"company": "UV IMPRESION", "match": "similar"}])

    def test_departments_only_use_confirmed_department_aliases(self):
        aliases = [{"source_entity": "Historical Name", "entity_kind": "entity", "company": "A"},
                   {"source_entity": "Office A", "entity_kind": "department", "company": "B"}]
        self.assertEqual(mapping.suggest_companies("A", "department", ["A", "B"], aliases), [])
        self.assertEqual(mapping.suggest_companies("Historical Name", "department", ["A", "B"], aliases), [])
        self.assertEqual(mapping.suggest_companies("Office A", "department", ["A", "B"], aliases),
                         [{"company": "B", "match": "normalized"}])
        self.assertEqual(mapping.suggest_companies("Office A", "department", ["A"], aliases), [])

    def test_empty_and_unrelated_names_have_no_suggestions(self):
        for name in ("", "   ", "Unknown unrelated subsidiary"):
            self.assertEqual(mapping.suggest_companies(name, "entity", ["UV IMPRESION"], []), [])

    def test_ambiguous_normalized_candidates_remain_visible_and_bounded(self):
        self.assertEqual(len(mapping.suggest_companies("Company A", "entity", ["Company A", "Company-A", "Company_A", "Company B"], [])), 3)

    def test_confirmed_aliases_keep_each_detail_amount_and_pending_finance(self):
        maps = {contract.entity_key("entity", name): company for name, company in
                [("UV IMPRESION MX", "UV IMPRESION"), ("UV IMPRESION MX彩印", "UV IMPRESION"), ("LEMOS MX", "LEMOS MX")]}
        amounts = ["11097.00", "6000.00", "32390.00"]
        for (key, company), amount in zip(maps.items(), amounts):
            line = dict(entity_key=key, row_fingerprint="unchanged", amount=amount, base_issues=[],
                        period_month=None, period_start=None, period_end=None)
            result = contract.apply_review(line, None, maps, "new")
            self.assertEqual((result["company"], result["amount"]), (company, amount))
            self.assertEqual(result["issues"], ["period"])
            self.assertEqual(result["review_state"], "needs_review")


class CandidateQueryTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE "tabMexico Expense Line" (name TEXT, source_entity TEXT, entity_kind TEXT, company TEXT, is_active INTEGER)')
        fixtures = [(str(i), f"Alias {i:02}", "entity", None, 1) for i in range(52)]
        fixtures += [("repeat", "Alias 00", "entity", "", 1), ("department", "Alias 00", "department", None, 1),
                     ("inactive", "Private", "entity", None, 0), ("forbidden", "Private", "entity", "B", 1),
                     ("assigned", "Assigned", "entity", "A", 1)]
        self.db.executemany('INSERT INTO "tabMexico Expense Line" VALUES (?,?,?,?,?)', fixtures)
        self.settings = frappe._dict(config_version="v1", company_mappings=[])
        def get_query(doctype, fields, filters, ignore_permissions):
            self.assertFalse(ignore_permissions)
            table = Table("tab" + doctype)
            query = Query.from_(table).select(*fields).where(table.company.isnull() | table.company.isin(["", "A"]))
            for field, operator, value in filters:
                self.assertEqual(operator, "=")
                query = query.where(getattr(table, field) == value)
            return query
        def run(query, as_dict=False):
            return [frappe._dict(dict(row)) for row in self.db.execute(query.get_sql(quote_char='"')).fetchall()]
        patches = [patch.object(frappe, "qb", SimpleNamespace(DocType=lambda name: Table("tab" + name), get_query=get_query)),
                   patch.object(QueryBuilder, "run", run, create=True), patch.object(service, "_settings", return_value=self.settings),
                   patch.object(service, "_require_settings_access"), patch.object(service, "_finance"),
                   patch.object(service, "_companies", return_value=["A"]), patch.object(service, "_", side_effect=lambda text: text),
                   patch.object(frappe, "throw", side_effect=lambda text, *args: (_ for _ in ()).throw(ValueError(text)))]
        for item in patches:
            item.start(); self.addCleanup(item.stop)

    def test_groups_pagination_and_permission_scope(self):
        first = service.get_unmapped_entities()
        self.assertTrue(first["has_more"])
        self.assertEqual(len(first["rows"]), 50)
        self.assertEqual(first["rows"][0]["line_count"], 2)
        second = service.get_unmapped_entities(50)
        self.assertFalse(second["has_more"])
        self.assertEqual(len(second["rows"]), 3)
        rows = first["rows"] + second["rows"]
        self.assertNotIn("Private", [row.source_entity for row in rows])
        self.assertNotIn("Assigned", [row.source_entity for row in rows])
        self.assertEqual(len({(row.source_entity, row.entity_kind) for row in rows}), 53)

    def test_unprivileged_configuration_access_stops_before_query(self):
        with patch.object(service, "_require_settings_access", side_effect=frappe.PermissionError), \
                patch.object(service, "_companies") as companies:
            with self.assertRaises(frappe.PermissionError): service.get_unmapped_entities()
            companies.assert_not_called()

    def test_invalid_page_rejected(self):
        for start in (-1, "bad", 1000001):
            with self.subTest(start=start), self.assertRaises(ValueError): service.get_unmapped_entities(start)
