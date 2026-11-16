"""Both entry points share one native Page authority and a localized label."""
import csv
import json
import unittest
from pathlib import Path

from frappe.desk.desk_views import DeskViews
from types import SimpleNamespace
from unittest.mock import patch
import frappe

PACKAGE=Path(__file__).parents[1]/"deeplinkerp_branding"


class MexicoNavigationTests(unittest.TestCase):
    def test_sidebar_and_home_icon_resolve_the_same_finance_page(self):
        icon=json.loads((PACKAGE/'desktop_icon/mexico_expenses.json').read_text())
        sidebar=json.loads((PACKAGE/'workspace_sidebar/mexico_expenses.json').read_text())
        page=json.loads((PACKAGE/'deeplinkerp_branding/page/mexico_expenses/mexico_expenses.json').read_text())
        self.assertEqual(icon['label'],sidebar['name'])
        self.assertEqual(icon['link_to'],sidebar['name'])
        self.assertEqual(icon['link_type'],'Workspace Sidebar')
        self.assertEqual(sidebar['items'][0]['link_type'],'Page')
        self.assertEqual(sidebar['items'][0]['link_to'],page['name'])
        self.assertEqual(icon['roles'],page['roles'])
        self.assertEqual(icon['hidden'],0)
        self.assertFalse(icon['parent_icon'])
        self.assertTrue((PACKAGE/'public/icons/desktop_icons/solid/mexico_expenses.svg').exists())

    def test_nested_sidebar_routes_have_real_pages_and_the_same_finance_authority(self):
        from frappe.model.utils import render_include
        sidebar = json.loads((PACKAGE / 'workspace_sidebar/mexico_expenses.json').read_text())
        icon = json.loads((PACKAGE / 'desktop_icon/mexico_expenses.json').read_text())
        self.assertEqual(len(sidebar['items']), 8)
        section = sidebar['items'][3]
        self.assertEqual(section['type'], 'Section Break')
        self.assertTrue(section['collapsible'])
        self.assertEqual([item['child'] for item in sidebar['items'][4:]], [1, 1, 1, 0])
        links = [item for item in sidebar['items'] if item['type'] == 'Link']
        self.assertEqual(len({item['link_to'] for item in links}), 7)
        for item in links:
            slug = item['link_to'].replace('-', '_')
            folder = PACKAGE / 'deeplinkerp_branding/page' / slug
            page = json.loads((folder / (slug + '.json')).read_text())
            self.assertEqual(item['link_type'], 'Page')
            self.assertEqual(page['name'], item['link_to'])
            self.assertEqual(page['title'], item['label'])
            self.assertEqual(page['roles'], icon['roles'])
            script = render_include((folder / (slug + '.js')).read_text())
            self.assertNotIn('{% include', script)
            self.assertIn('new root.DeepLinkERPMexicoExpenses.Workbench(wrapper)', script)
            authority = SimpleNamespace(allowed_pages={}, restricted_pages={page['name']})
            with patch.object(frappe, 'session', SimpleNamespace(user='finance')):
                self.assertFalse(DeskViews.is_item_allowed(authority, page['name'], 'Page'))
                authority.allowed_pages = {page['name']: {}}
                self.assertTrue(DeskViews.is_item_allowed(authority, page['name'], 'Page'))

    def test_native_sidebar_item_requires_allowed_page_authority(self):
        authority=SimpleNamespace(allowed_pages={},restricted_pages={'mexico-expenses'})
        with patch.object(frappe,'session',SimpleNamespace(user='finance')):
            self.assertFalse(DeskViews.is_item_allowed(authority,'mexico-expenses','Page'))
            authority.allowed_pages={'mexico-expenses':{}}
            self.assertTrue(DeskViews.is_item_allowed(authority,'mexico-expenses','Page'))
            authority.restricted_pages=set()
            self.assertFalse(DeskViews.is_item_allowed(authority,'mexico-expenses','Page'))

    def test_navigation_caption_is_localized_in_three_languages(self):
        for lang,expected in [('zh','费用支出（MX）'),('en','Expenses (MX)'),('es','Gastos (MX)')]:
            with (PACKAGE/'translations'/(lang+'.csv')).open() as stream:
                rows={row[0]:row[1] for row in csv.reader(stream) if len(row)>=2}
            self.assertEqual(rows['Mexico Expenses'],expected)
