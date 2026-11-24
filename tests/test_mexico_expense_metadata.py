"""Metadata security and language completeness without a database migration."""
import csv
import json
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe import permissions

from deeplinkerp_branding.services import mexico_expense_contract as contract

PACKAGE=Path(__file__).parents[1]/"deeplinkerp_branding"


def meta(slug):
    return json.loads((PACKAGE/"deeplinkerp_branding/doctype"/slug/(slug+".json")).read_text())


class MexicoMetadataTests(unittest.TestCase):
    def test_native_report_role_evaluation_grants_report_without_writes(self):
        doc = meta("mexico_expense_line")
        model = frappe._dict(name=doc["name"], permissions=[
            frappe._dict(**permission, permlevel=0) for permission in doc["permissions"]
        ])
        for role in ("Accounts User", "Accounts Manager", "System Manager"):
            with self.subTest(role=role), patch.object(frappe, "local", SimpleNamespace(role_permissions={})), \
                    patch.object(frappe, "get_roles", return_value=[role]), \
                    patch.object(permissions, "get_rights", return_value=["read", "report", "export", "write", "create", "delete"]):
                rights = permissions.get_role_permissions(model, user="finance-user")
                self.assertEqual([rights[key] for key in ("read", "report", "export")], [1, 1, 1])
                self.assertEqual([rights[key] for key in ("write", "create", "delete")], [0, 0, 0])

    def test_line_is_independent_and_scoped_by_real_company_link(self):
        doc=meta("mexico_expense_line")
        self.assertEqual(doc["istable"],0)
        fields={f['fieldname']:f for f in doc['fields']}
        self.assertEqual(fields['company']['options'],'Company')
        self.assertEqual(fields['amount']['fieldtype'],'Currency')
        self.assertEqual(fields['amount']['precision'],'2')
        self.assertEqual(fields['amount']['options'],'currency')
        self.assertEqual(fields['amount_raw']['fieldtype'],'Data')
        self.assertTrue(fields['line_key']['unique'])
        self.assertTrue(all(not permission.get('write') and not permission.get('create') for permission in doc['permissions']))

    def test_category_storage_accepts_every_parser_category(self):
        choices = next(f['options'] for f in meta('mexico_expense_line')['fields'] if f['fieldname']=='category').split('\n')
        self.assertTrue({c[0] for c in contract.CATEGORIES}.issubset(choices))

    def test_raw_source_is_not_exposed_to_accounts_roles(self):
        source=meta('mexico_expense_source')
        self.assertEqual([p['role'] for p in source['permissions']],['System Manager'])
        raw=next(f for f in source['fields'] if f['fieldname']=='raw_json')
        self.assertEqual(raw['permlevel'],1)
        self.assertTrue(next(f for f in source['fields'] if f['fieldname']=='source_key')['unique'])

    def test_background_sync_is_disabled_by_default(self):
        doc=meta('mexico_expense_settings')
        self.assertEqual(next(f for f in doc['fields'] if f['fieldname']=='enabled')['default'],'0')

    def test_native_report_loader_resolves_shared_filter_script(self):
        from frappe.model.utils import render_include
        for path in (PACKAGE / "deeplinkerp_branding/report").glob("mexico_*/*.js"):
            script = render_include(path.read_text())
            self.assertNotIn("{% include", script)
            self.assertIn('fieldname: "category"', script)
            self.assertIn('fieldname: "subcategory"', script)
            self.assertIn('fieldname: "keyword"', script)

    def test_all_new_field_and_enum_labels_exist_in_three_languages(self):
        keys={label for _,label,_ in contract.CATEGORIES}|{v[1] for v in contract.SUBCATEGORIES.values()}|set(contract.ISSUES.values())
        keys.update(label for label, _ in contract.APPROVAL_FORMS.values())
        keys.update(re.findall(r'_\("([^"\n]+)"\)', (PACKAGE / "services/mexico_expense_fx.py").read_text()))
        keys.update(re.findall(r'__\("([^"\n]+)"\)', (PACKAGE / "public/js/mexico_expense_reports.js").read_text()))
        for slug in ['mexico_expense_source','mexico_expense_line','mexico_expense_settings','mexico_expense_company_map','mexico_expense_template']:
            doc=meta(slug);keys.add(doc['name'])
            for f in doc['fields']:
                keys.add(f['label'])
                if f['fieldtype']=='Select':keys.update(v for v in f['options'].split('\n') if v)
        for lang in ['en','zh','es']:
            with (PACKAGE/'translations'/(lang+'.csv')).open() as stream:
                values={r[0]:r[1] for r in csv.reader(stream) if len(r)>=2}
            self.assertEqual(sorted(keys-values.keys()),[],lang)
            self.assertTrue(all(values[k] for k in keys),lang)

    def test_expense_translation_is_scoped_without_overwriting_generic_events(self):
        context = meta('mexico_expense_template')['name']
        for lang, generic, approval, journal in [('zh', '费用', '费用支出', '支出'),
                                        ('en', 'Expense', 'Expense Requests', 'Payment'),
                                        ('es', 'gasto', 'Solicitudes de gastos', 'Gasto')]:
            with self.subTest(lang=lang):
                with (PACKAGE/'translations'/(lang+'.csv')).open() as stream:
                    rows = [row for row in csv.reader(stream) if len(row) >= 2 and row[0] == 'expense']
                self.assertEqual(len(rows), 3)
                values = {(row[2] if len(row) > 2 else ''): row[1] for row in rows}
                self.assertEqual(values, {'': generic, context: approval, 'CN Cash Journal Entry': journal})
                # Frappe Select controls and formatters use the parent DocType context.
                def translate(text, context=None):
                    return values.get(context, values.get('', text))
                from frappe.utils.formatters import format_value
                with patch.object(frappe, '_', side_effect=translate):
                    self.assertEqual(format_value('expense', frappe._dict(fieldtype='Select', parent=context)), approval)
                    self.assertEqual(format_value('expense', frappe._dict(fieldtype='Select', parent='Operating Expense Event')), generic)
                    self.assertEqual(format_value('expense', frappe._dict(fieldtype='Select', parent='CN Cash Journal Entry')), journal)
