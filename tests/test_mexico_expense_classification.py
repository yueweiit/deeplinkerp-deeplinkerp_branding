import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from deeplinkerp_branding.services import mexico_expense_classification as c
from deeplinkerp_branding.services import mexico_expense_projection as p
from deeplinkerp_branding.services import mexico_expense_templates as t
from test_mexico_expense_templates import sample, component
from test_mexico_expense_contract import approval


class SourceClassificationTests(unittest.TestCase):
    def config(self, fields=None):
        return dict(approval_type='operating', classification_fields=json.dumps(fields or [['root'], ['child'], ['leaf']]))

    def row(self, *values):
        return {'form_component_values': [component(key, value) for key, value in zip(('root', 'child', 'leaf'), values)]}

    def test_source_labels_and_slashes_are_not_split_or_guessed(self):
        path, issues = c.extract(self.row('管理费用/Admin', '平台费', '新选项'), self.config())
        self.assertEqual(issues, [])
        self.assertEqual([n['label'] for n in path], ['管理费用/Admin', '平台费', '新选项'])
        self.assertEqual(path[2]['key'].split('/')[:2], path[1]['key'].split('/'))

    def test_keys_merge_template_copies_but_keep_parents_and_forms_separate(self):
        first = c.extract(self.row('Admin', 'Service'), self.config())[0][-1]['key']
        renamed = {'form_component_values': [component('a', ' Admin  '), component('b', 'Service')]}
        second = c.extract(renamed, self.config([['a'], ['b']]))[0][-1]['key']
        self.assertEqual(first, second)
        self.assertNotEqual(first, c.extract(self.row('Sales', 'Service'), self.config())[0][-1]['key'])
        self.assertNotEqual(first, c.extract(self.row('Admin', 'Service'), {**self.config(), 'approval_type':'purchase'})[0][-1]['key'])

    def test_bad_configuration_rejected_and_empty_uses_defaults(self):
        for value in ('{}', '[]', '[["a"],["a"]]', '[["x%"]]', '["a"]', json.dumps([['x'+str(i)] for i in range(7)])):
            with self.subTest(value=value), self.assertRaises(ValueError):c.validate_fields(value)
        self.assertIsNone(c.validate_fields(''))
        template, _ = sample()
        template['field_mapping'] = '{"expense_type":["future_root"]}'
        self.assertEqual(c.fields_for(template)[0], ['future_root'])

    def test_missing_ambiguous_multiselect_and_free_text_require_review(self):
        for fields in ([], [component('root','A'), component('root','B')],
                       [component('root','A','TextareaField')], [component('root','["A","B"]','DDMultiSelectField')]):
            path, issues = c.extract({'form_component_values':fields}, self.config())
            self.assertEqual(path, []);self.assertEqual(issues, ['classification'])

    def test_tree_conserves_money_with_partial_paths_without_double_counting(self):
        path = c.extract(self.row('Admin','Parking'), self.config())[0]
        rows = [{'classification_path':json.dumps(path), 'amount':'0.10'},
                {'classification_path':json.dumps(path[:1]), 'amount':'0.20'},
                {'classification_path':json.dumps(path), 'amount':'9999999999999.99'}]
        nodes = c.composition(rows)
        parent = next(n for n in nodes if not n['parent'])
        child = next(n for n in nodes if n['parent'])
        self.assertEqual(parent['amount'], '10000000000000.29')
        self.assertEqual(parent['direct_amount'], '0.20')
        self.assertEqual(child['amount'], '10000000000000.09')
        self.assertTrue(parent['has_children']);self.assertFalse(child['has_children'])

    def test_future_option_appears_without_alias_and_fixed_reports_retain_total(self):
        template, row = sample('purchase')
        next(f for f in row['raw_payload']['formComponentValues'] if f['id']=='DDSelectField_187IN92KF6O00')['value']='新采购选项'
        _, lines = p.project(row,template,'America/Mexico_City')
        self.assertEqual(lines[0]['category'],'source_other')
        self.assertNotIn('category',lines[0]['base_issues'])
        self.assertEqual(json.loads(lines[0]['classification_path'])[0]['label'],'新采购选项')
        self.assertEqual(lines[0]['amount'],'100.00')

    def test_frappe_document_template_and_expense_path_preserve_raw(self):
        template=next(v for v in t.default_templates() if v['approval_type']=='expense')
        row=approval(); original=copy.deepcopy(row)
        with patch('deeplinkerp_branding.services.operating_expense_contract._',side_effect=lambda m:m):
            source, lines=p.project(row,SimpleNamespace(get=lambda k:template.get(k)),'America/Mexico_City')
        self.assertEqual(row,original)
        self.assertEqual(json.loads(source['raw_json']),original)
        self.assertTrue(lines[0]['classification_key'])
        self.assertTrue(lines[0]['business_category'])

    def test_legacy_payroll_uses_bound_table_not_description(self):
        template=next(v for v in t.default_templates() if v['approval_type']=='expense')
        row={'form_component_values':[component('TableField_ENJNMIBMUAO0','[]','TableField','工资')]}
        path, issues=c.line_path(row,template,{'table_id':'TableField_ENJNMIBMUAO0'},[],['classification'])
        self.assertEqual(issues,[]);self.assertEqual(path[0]['label'],'工资')
        path, issues=c.line_path(row,template,{'table_id':'unknown'},[],['classification'])
        self.assertEqual(issues,['classification'])

    def test_known_expense_root_merges_legacy_and_modern_layouts(self):
        template=next(v for v in t.default_templates() if v['approval_type']=='expense')
        modern={'form_component_values':[component('DDSelectField_1ATVGGUYKEZK0','Remuneraciones y Personal/薪酬人事'),component('DDSelectField_E8MD2JTMHWO','Salario工资')]}
        legacy={'form_component_values':[component('DDSelectField_E8MD2JTMHWO','Salario工资')]}
        paths=[]
        for row in (modern,legacy):
            path,_=c.extract(row,template)
            paths.append(c.expense_root(row,template,{'category':'personnel'},path))
        self.assertEqual([n['key'] for n in paths[0]],[n['key'] for n in paths[1]])
        self.assertEqual(paths[0][0]['label'],'Remunerations and Personnel')
        self.assertTrue(paths[0][0]['translate'])
        self.assertEqual(paths[0][0]['source_label'],'Remuneraciones y Personal/薪酬人事')

    def test_known_conflicting_expense_categories_are_not_auto_confirmed(self):
        template=next(v for v in t.default_templates() if v['approval_type']=='expense')
        row=approval()
        fields=row['form_component_values']
        fields[:] = [f for f in fields if f['id'] not in ('DDSelectField_1ATVGGUYKEZK0','DDSelectField_E8MD2JTMHWO')]
        fields += [component('DDSelectField_1ATVGGUYKEZK0','Impuestos y Contribuciones/税费'),component('DDSelectField_E8MD2JTMHWO','Salario工资')]
        with patch('deeplinkerp_branding.services.operating_expense_contract._',side_effect=lambda m:m):
            _,lines=p.project(row,template,'America/Mexico_City')
        self.assertIn('category',lines[0]['base_issues'])


class ClassificationReviewRegressionTests(unittest.TestCase):
    def setUp(self):
        translator = patch('deeplinkerp_branding.services.operating_expense_contract._', side_effect=lambda message: message)
        translator.start()
        self.addCleanup(translator.stop)
        self.template = next(v for v in t.default_templates() if v['approval_type'] == 'expense')

    def expense(self, option='Nueva opción A'):
        from test_mexico_expense_contract import field, table, approve_finance
        return approve_finance(approval([
            field('DDSelectField_F7MDR4TJ1JS0', 'Peso比索'),
            field('DDSelectField_1ATVGGUYKEZK0', 'Servicios Públicos y Operativos/公用运营'),
            field('DDSelectField_J6QIEDKOACG0', option), table(amount='100'),
        ]))

    def project(self, row):
        source, lines = p.project(row, self.template, 'America/Mexico_City')
        return source, lines[0]

    def reviewed(self, source, line, legacy=False):
        fingerprint = line['_legacy_row_fingerprint'] if legacy else line['row_fingerprint']
        return {**line, 'source_version': source['source_version'], 'row_fingerprint': fingerprint,
                'reviewed_fingerprint': fingerprint, 'reviewed_by': 'reviewer', 'reviewed_at': '2026-11-16',
                'review_note': 'Checked', 'company_basis': 'manual', 'company': 'MX', 'issues': '[]'}

    def apply(self, source, line, previous):
        from deeplinkerp_branding.services import mexico_expense_contract as contract
        compatible = p.compatible_previous_review(previous, line, source['source_version'], line['_legacy_row_fingerprint'])
        return contract.apply_review(line, compatible, {}, 'mapping-v1')

    def test_changed_dynamic_category_invalidates_manual_review(self):
        first_source, first = self.project(self.expense())
        source, line = self.project(self.expense('Nueva opción B'))
        self.assertEqual(first['_legacy_row_fingerprint'], line['_legacy_row_fingerprint'])
        self.assertNotEqual(first['row_fingerprint'], line['row_fingerprint'])
        result = self.apply(source, line, self.reviewed(first_source, first))
        self.assertEqual(result['review_state'], 'needs_review')
        self.assertIn('stale', result['issues'])
        self.assertIsNone(result['reviewed_by'])
        self.assertFalse(result['company'])

    def test_unchanged_legacy_review_upgrades_once_and_retains_audit_fields(self):
        source, line = self.project(self.expense())
        previous = self.reviewed(source, line, legacy=True)
        original = copy.deepcopy(previous)
        result = self.apply(source, line, previous)
        self.assertEqual(previous, original)
        self.assertEqual(result['review_state'], 'confirmed')
        self.assertEqual(result['reviewed_fingerprint'], line['row_fingerprint'])
        for key in ('company', 'company_basis', 'reviewed_by', 'reviewed_at', 'review_note'):
            self.assertEqual(result[key], previous[key])
        repeated = self.apply(source, line, {**result, 'source_version': source['source_version']})
        self.assertEqual(repeated, result)

    def test_changed_source_before_first_upgrade_cannot_use_legacy_bridge(self):
        old_source, old_line = self.project(self.expense())
        source, line = self.project(self.expense('Nueva opción B'))
        result = self.apply(source, line, self.reviewed(old_source, old_line, legacy=True))
        self.assertEqual(result['review_state'], 'needs_review')
        self.assertIn('stale', result['issues'])

    def test_bridge_rejects_changed_path_business_facts_and_stale_reviews(self):
        source, line = self.project(self.expense())
        previous = self.reviewed(source, line, legacy=True)
        for change in ({'classification_key': 'a' * 32}, {'category': 'tax'},
                       {'subcategory': 'social'}, {'issues': '["stale"]'},
                       {'row_fingerprint': 'changed'}, {'reviewed_fingerprint': 'older'},
                       {'source_version': ''}):
            with self.subTest(change=change):
                old = {**previous, **change}
                self.assertEqual(p.compatible_previous_review(old, line, source['source_version'], line['_legacy_row_fingerprint']), old)

    def test_preclassification_review_without_path_can_upgrade_identical_known_facts(self):
        source, line = self.project(self.expense('CFE联邦电力委员会'))
        previous = self.reviewed(source, line, legacy=True)
        previous.pop('classification_key')
        previous.pop('classification_path')
        self.assertEqual(self.apply(source, line, previous)['review_state'], 'confirmed')

    def test_versioned_review_does_not_downgrade_to_legacy_comparison(self):
        source, line = self.project(self.expense())
        previous = self.reviewed(source, line)
        self.assertEqual(p.compatible_previous_review(previous, line, source['source_version'], line['_legacy_row_fingerprint']), previous)

    def test_mixed_salary_and_social_tables_override_header_subcategory(self):
        from test_mexico_expense_contract import field, table, approve_finance
        for subtype, custom, mapped, table_names in ((True, False, False, True), (False, False, False, True),
                                                    (True, True, True, True), (True, False, False, False)):
            with self.subTest(subtype=subtype, custom=custom, mapped=mapped, table_names=table_names):
                template = copy.deepcopy(self.template)
                wages = table('TableField_ENJNMIBMUAO0', amount='100.10')
                social = table('TableField_21SE4NJZE8W00', amount='20.20')
                if table_names:
                    wages['name'], social['name'] = '工资', '社会保险缴费（IMSS/Infonavit）'
                if mapped:
                    wages['id'], social['id'] = 'future_salary', 'future_social'
                    template['field_mapping'] = json.dumps({'salary_table': ['future_salary'], 'social_table': ['future_social']})
                fields = [field('DDSelectField_F7MDR4TJ1JS0', 'Peso比索'),
                          field('DDSelectField_1ATVGGUYKEZK0', 'Remuneraciones y Personal/薪酬人事'), wages, social]
                if subtype:
                    fields.append(field('DDSelectField_E8MD2JTMHWO', 'Salario工资'))
                if custom:
                    template['classification_fields'] = '[["DDSelectField_1ATVGGUYKEZK0"],["DDSelectField_E8MD2JTMHWO"]]'
                source, lines = p.project(approve_finance(approval(fields)), template, 'America/Mexico_City')
                self.assertEqual([l['subcategory'] for l in lines], ['salary', 'social'])
                self.assertEqual([l['base_issues'] for l in lines], [[], []])
                self.assertNotEqual(lines[0]['classification_key'], lines[1]['classification_key'])
                paths = [json.loads(l['classification_path']) for l in lines]
                self.assertEqual(paths[0][0]['key'], paths[1][0]['key'])
                self.assertEqual(paths[0][-1]['field'], wages['id'])
                self.assertEqual(paths[1][-1]['field'], social['id'])
                nodes = c.composition([{'classification_path': l['classification_path'], 'amount': l['amount']} for l in lines])
                self.assertEqual(sorted(n['amount'] for n in nodes if not n['has_children']), ['100.10', '20.20'])
                self.assertEqual(next(n['amount'] for n in nodes if not n['parent']), '120.30')

    def test_payroll_table_does_not_mask_ambiguous_header_fields(self):
        from test_mexico_expense_contract import field, table, approve_finance
        wages = table('TableField_ENJNMIBMUAO0', amount='100')
        wages['name'] = '工资'
        row = approve_finance(approval([field('DDSelectField_F7MDR4TJ1JS0', 'Peso比索'), wages,
            field('DDSelectField_E8MD2JTMHWO', 'Salario工资'), field('DDSelectField_3QB6VJM7BS80', 'Tributos税费')]))
        _, line = self.project(row)
        self.assertIn('classification', line['base_issues'])

    def test_historical_name_bindings_keep_operating_and_purchase_rows_classifiable(self):
        for kind, key, name in (('operating', 'DDSelectField_1CHIGP4L45K00', '管理支出Gastos de operación'),
                                ('purchase', 'DDSelectField_187IN92KF6O00', '采购支出Gastos de Compra')):
            with self.subTest(kind=kind):
                template, row = sample(kind)
                item = next(f for f in row['raw_payload']['formComponentValues'] if f['id'] == key)
                item.update(id='historical_category', name=name)
                _, lines = p.project(row, template, 'America/Mexico_City')
                self.assertEqual(lines[0]['base_issues'], [])
                self.assertEqual(json.loads(lines[0]['classification_path'])[0]['field'], 'historical_category')

    def test_explicit_id_override_does_not_fall_back_to_historical_label(self):
        template, row = sample('operating')
        item = next(f for f in row['raw_payload']['formComponentValues'] if f['id'] == 'DDSelectField_1CHIGP4L45K00')
        item.update(id='historical_category', name='管理支出Gastos de operación')
        template['field_mapping'] = '{"operating_category":["required_new_id"]}'
        self.assertEqual(c.extract(row, template), ([], ['classification']))
        template['classification_fields'] = '[["required_new_id"]]'
        self.assertEqual(c.extract(row, template), ([], ['classification']))

    def test_ambiguous_historical_labels_do_not_select_an_arbitrary_field(self):
        template, row = sample('operating')
        fields = row['raw_payload']['formComponentValues']
        item = next(f for f in fields if f['id'] == 'DDSelectField_1CHIGP4L45K00')
        item.update(id='old_a', name='管理支出Gastos de operación')
        fields.append({**item, 'id': 'old_b'})
        self.assertEqual(c.extract(row, template), ([], ['classification']))

    def test_operating_payroll_tables_also_take_precedence_over_shared_header(self):
        template, row = sample('operating')
        fields = row['raw_payload']['formComponentValues']
        next(f for f in fields if f['id'] == 'MoneyField_1FPZGEQOZ4M80')['value'] = '120.30'
        fields.append(component('DDSelectField_RIQQC9Q99KW0', '管理费用Gastos administrativos'))
        for id, label, amount in (
            ('operating_salary', '薪酬税费总支出(分部门)Gasto total salarial-fiscal por depa', '100.10'),
            ('TableField_G2ELEALN0S80', '社保公积金seguridad social y fondo de vivienda', '20.20'),
        ):
            rows = [{'rowNumber': id + '_row', 'rowValue': [
                {'key': 'department', 'label': '部门Departamento', 'value': 'MX HR'},
                {'key': 'MoneyField_X5KBWAODJ1S0', 'label': '金额（元）Monto (yuan)', 'value': amount},
            ]}]
            fields.append(component(id, json.dumps(rows), 'TableField', label))
        _, lines = p.project(row, template, 'America/Mexico_City')
        self.assertEqual([l['base_issues'] for l in lines], [[], []])
        self.assertEqual([l['subcategory'] for l in lines], ['salary', 'social'])
        self.assertNotEqual(lines[0]['classification_key'], lines[1]['classification_key'])
        self.assertEqual(json.loads(lines[1]['classification_path'])[-1]['field'], 'TableField_G2ELEALN0S80')

    def test_upsert_bridges_review_and_does_not_persist_legacy_helper(self):
        import frappe
        from deeplinkerp_branding.services import mexico_expenses as service
        row = self.expense()
        source, line = self.project(row)
        previous = frappe._dict(self.reviewed(source, line, legacy=True), name=line['line_key'])
        source_doc = SimpleNamespace(name=source['source_key'], corp_id=source['corp_id'], process_code=source['process_code'])
        line_doc = SimpleNamespace(name=line['line_key'])
        settings = SimpleNamespace(business_timezone='America/Mexico_City', mapping_version='mapping-v1')
        with patch.object(service, '_template', return_value=self.template), \
                patch.object(frappe, 'db', SimpleNamespace(exists=lambda *args: True)), \
                patch.object(frappe, 'get_doc', side_effect=[source_doc, line_doc]), \
                patch.object(frappe, 'get_all', return_value=[previous]), \
                patch.object(service, '_maps', return_value={}), \
                patch.object(service, '_period_version', return_value='current'), \
                patch.object(service, '_store_projection') as store:
            service._upsert(row, settings)
        saved = store.call_args_list[1].args[1]
        self.assertNotIn('_legacy_row_fingerprint', saved)
        self.assertEqual(saved['row_fingerprint'], line['row_fingerprint'])
        self.assertEqual(saved['reviewed_fingerprint'], line['row_fingerprint'])
        self.assertEqual(saved['company'], 'MX')
        self.assertEqual(saved['reviewed_by'], 'reviewer')
