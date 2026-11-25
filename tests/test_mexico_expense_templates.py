import copy
import json
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import frappe
from deeplinkerp_branding.services import mexico_expense_templates as t, mexico_expense_projection as p, mexico_expenses as s


def component(key,value,kind='DDSelectField',name=''):
    return dict(id=key,value=value,componentType=kind,name=name)


def sample(kind='operating'):
    config=next(copy.deepcopy(v) for v in t.default_templates() if v['approval_type']==kind)
    fields=[component('CascadeField_G8SEQLCWG1S0','Company MX/HR','CascadeField'),component('MoneyField_1FPZGEQOZ4M80','100','MoneyField'),component('DDSelectField_199XDJ3I2RMO0','人民币RMB'),component('DDSelectField_1CHIGP4L45K00','工资墨西哥Salario en México'),component('DDMultiSelectField_ZCB0DINS0400','["Semanal周薪"]','DDMultiSelectField'),component('DDSelectField_187IN92KF6O00','MRO采购Compras De MRO')]
    task=dict(activityId='new-node',taskGroupName='MX财务Finanzas',taskId='t1',userId='u1',status='COMPLETED',result='AGREE',createTime='2026-10-31T12:00Z',finishTime='2026-11-01T05:00Z')
    raw=dict(status='RUNNING',result='agree',formComponentValues=fields,tasks=[task],operationRecords=[])
    row=dict(corp_id=config['corp_id'],process_code=config['process_code'],process_instance_id='sample',create_time='2026-10-31T12:00Z',status='RUNNING',result='agree',raw_payload=raw)
    return config,row


def table_row(row_id, amount='100', quantity='2', price='50'):
    return dict(rowNumber=row_id,rowValue=[dict(key='TextField_1MXFGEI1WGO00',label='物品编码Código',value='ITEM-A'),dict(key='CalculateField_1D7YT3J5Z4YO0',label='总金额Monto Total',value=amount),dict(key='NumberField_6Q22XFQ51VW0',label='数量Cantidad',value=quantity),dict(key='NumberField_1P237DCILQKG0',label='单价Precio',value=price)])


class TemplateProjectionTests(unittest.TestCase):
    def setUp(self):
        # Pure projection tests do not initialize a site translation cache.
        translator = patch('deeplinkerp_branding.services.operating_expense_contract._', side_effect=lambda message: message)
        translator.start()
        self.addCleanup(translator.stop)

    def test_defaults_are_seven_unique_configurable_templates(self):
        configs=[t.normalize(v) for v in t.default_templates()]
        self.assertEqual(len(configs),7)
        self.assertEqual(len({(v['corp_id'],v['process_code']) for v in configs}),7)
        self.assertEqual([v['approval_type'] for v in configs].count('operating'),3)

    def test_invalid_mappings_and_missing_finance_rule_are_rejected(self):
        config,_=sample()
        for values in ({'approval_type':'other'},{'corp_id':'bad corp'},{'field_mapping':'{"amount":"bad"}'},{'field_mapping':'{"unknown":["id"]}'},{'finance_node_names':'','finance_activity_ids':''}):
            with self.subTest(values=values),self.assertRaises(ValueError):t.normalize({**config,**values})

    def test_management_accepts_no_finance_config_without_disabling_other_validation(self):
        config,_=sample()
        config.update(finance_node_names='',finance_activity_ids='')
        self.assertEqual(t.normalize(config,require_finance=False)['finance_node_names'],'')
        with self.assertRaises(ValueError):t.normalize(config)
        with self.assertRaises(ValueError):t.normalize({**config,'field_mapping':'{"unknown":["id"]}'},require_finance=False)

    def test_management_projection_uses_whole_approval_without_reading_finance_tasks(self):
        from test_mexico_expense_contract import approval
        for kind in ('operating','purchase','expense'):
            config,row=sample(kind) if kind!='expense' else (next(v for v in t.default_templates() if v['approval_type']=='expense'),approval())
            for status,result,expected in (('RUNNING','AGREE','pending'),('COMPLETED','AGREE','approved'),('COMPLETED','REFUSE','rejected'),('TERMINATED','','terminated')):
                with self.subTest(kind=kind,status=status,result=result):
                    row.update(status=status,result=result)
                    row['raw_payload'].update(status=status,result=result,tasks='malformed legacy task data')
                    with patch.object(p.c,'finance_period',side_effect=AssertionError('management must not inspect finance tasks')):
                        source,lines=p.project(row,config,'America/Mexico_City',include_finance=False)
                    self.assertEqual(source['approval_status'],expected)
                    self.assertIsNone(source['finance_approved_at'])
                    self.assertTrue(lines)
                    self.assertTrue(all(not line['finance_approved_date'] and 'finance' not in line['base_issues'] for line in lines))

    def test_finance_projection_is_unchanged_after_management_preview(self):
        config,row=sample()
        before=p.project(row,config,'America/Mexico_City')
        management=p.project(row,config,'America/Mexico_City',include_finance=False)
        self.assertIsNotNone(before[0]['finance_approved_date'])
        self.assertIsNone(management[0]['finance_approved_date'])
        self.assertEqual(before,p.project(row,config,'America/Mexico_City'))

    def test_operating_salary_finance_date_currency_and_cascade(self):
        config,row=sample();source,lines=p.project(row,config,'America/Mexico_City')
        self.assertEqual(source['approval_type'],'operating')
        self.assertEqual(lines[0]['source_entity'],'Company MX')
        self.assertEqual(lines[0]['period_month'],'2026-10-01')
        self.assertEqual(lines[0]['currency'],'CNY')
        self.assertEqual(lines[0]['subcategory'],'salary')
        self.assertEqual(lines[0]['salary_type'],'Semanal周薪')
        self.assertEqual(lines[0]['base_issues'],[])
        self.assertEqual(json.loads(source['details_json'])['fields']['Source Department'],'HR')

    def test_plain_multiselect_is_not_a_cascade_company_guess(self):
        config,row=sample();f=row['raw_payload']['formComponentValues'][0]
        f.update(id='DDSelectField_1G5IFPI8IKQO0',componentType='DDMultiSelectField',value='["A","B"]')
        self.assertIn('allocation',p.project(row,config,'America/Mexico_City')[1][0]['base_issues'])

    def test_future_template_and_component_ids_can_be_mapped_without_parser_edits(self):
        config,row=sample();config['process_code']=row['process_code']='PROC-00000000-0000-0000-0000-000000000001'
        row['raw_payload']['formComponentValues'][1]['id']='MoneyField_future'
        config['field_mapping']='{"amount":["MoneyField_future"]}'
        self.assertEqual(p.project(row,config,'America/Mexico_City')[1][0]['amount'],'100.00')

    def test_gastos_override_preserves_raw_archive_and_table_identity(self):
        from test_mexico_expense_contract import approval
        config=next(v for v in t.default_templates() if v['approval_type']=='expense')
        config['field_mapping']='{"currency":["new_currency"],"expense_table":["new_table"]}'
        row=approval()
        row['form_component_values'][2]['id']='new_currency'
        row['form_component_values'][3]['id']='new_table'
        source,lines=p.project(row,config,'America/Mexico_City')
        self.assertEqual(lines[0]['currency'],'MXN')
        self.assertEqual(lines[0]['table_id'],'new_table')
        self.assertNotIn('structure',lines[0]['base_issues'])
        self.assertEqual(json.loads(source['raw_json']),row)

    def test_ambiguous_fields_and_type_changes_do_not_confirm(self):
        config,row=sample();row['raw_payload']['formComponentValues'].append(component('DDSelectField_1G5IFPI8IKQO0','Another'))
        self.assertIn('structure',p.project(row,config,'America/Mexico_City')[1][0]['base_issues'])
        config,row=sample();row['raw_payload']['formComponentValues'][1]['componentType']='TextareaField'
        self.assertIn('structure',p.project(row,config,'America/Mexico_City')[1][0]['base_issues'])

    def test_purchase_items_are_not_added_to_header_or_counted_as_applications(self):
        config,row=sample('purchase');rows=[table_row('a','50','1','50'),table_row('b','50','1','50')]
        row['raw_payload']['formComponentValues'].append(component('TableField_119WFD19L8R40',json.dumps(rows),'TableField','需求明细Desglose de los gastos'))
        source,lines=p.project(row,config,'America/Mexico_City')
        self.assertEqual(len(lines),1);self.assertEqual(source['total_amount'],'100.00')
        self.assertEqual(lines[0]['amount'],'100.00');self.assertEqual(lines[0]['base_issues'],[])
        self.assertEqual(len(json.loads(source['details_json'])['tables'][0]['rows']),2)

    def test_zero_purchase_and_disagreeing_quantity_price_require_review(self):
        config,row=sample('purchase');row['raw_payload']['formComponentValues'][1]['value']='0'
        self.assertIn('amount_pending',p.project(row,config,'America/Mexico_City')[1][0]['base_issues'])
        config,row=sample('purchase');row['raw_payload']['formComponentValues'].append(component('TableField_119WFD19L8R40',json.dumps([table_row('a',price='60')]),'TableField'))
        self.assertIn('total',p.project(row,config,'America/Mexico_City')[1][0]['base_issues'])

    def test_duplicate_child_row_id_and_unknown_nonempty_table_are_flagged(self):
        for table,rows,issue in [('TableField_unknown',[table_row('a')],'structure'),('TableField_119WFD19L8R40',[table_row('a'),table_row('a')],'identity')]:
            config,row=sample('purchase');row['raw_payload']['formComponentValues'].append(component(table,json.dumps(rows),'TableField'))
            self.assertIn(issue,p.project(row,config,'America/Mexico_City')[1][0]['base_issues'])

    def test_all_matching_finance_tasks_must_finish_and_same_names_are_exact(self):
        config,row=sample();tasks=row['raw_payload']['tasks'];tasks.append({**tasks[0],'taskId':'t2','activityId':'branch2','status':'RUNNING','result':'NONE'})
        self.assertIsNone(p.project(row,config,'America/Mexico_City')[0]['finance_approved_date'])
        config,row=sample();row['raw_payload']['tasks'][0]['taskGroupName']='MX财务Finanzas assistant'
        self.assertIsNone(p.project(row,config,'America/Mexico_City')[0]['finance_approved_date'])

    def test_subject_changes_invalidate_prior_manual_review(self):
        config,row=sample();_,lines=p.project(row,config,'America/Mexico_City');old=dict(lines[0],reviewed_fingerprint=lines[0]['row_fingerprint'],company='A',company_basis='manual')
        row['raw_payload']['formComponentValues'][0]['value']='Another/HR';_,new=p.project(row,config,'America/Mexico_City')
        result=s.contract.apply_review(new[0],old,{},'v1')
        self.assertIn('stale',result['issues']);self.assertIsNone(result['company'])


class GastosMappingRegressionTests(unittest.TestCase):
    setUp = TemplateProjectionTests.setUp

    def config(self, **mapping):
        config = next(v for v in t.default_templates() if v['approval_type'] == 'expense')
        return t.normalize(dict(config, field_mapping=json.dumps(mapping)))

    def header(self):
        from test_mexico_expense_contract import approval, approve_finance, field
        return approve_finance(approval([
            field('DDSelectField_1ATVGGUYKEZK0', 'Servicios Públicos y Operativos/公用运营'),
            field('DDSelectField_J6QIEDKOACG0', 'CFE联邦电力委员会'),
            field('DDSelectField_F7MDR4TJ1JS0', 'Peso比索'),
            field('DDMultiSelectField_RWULY2WAUCG0', 'Company MX'),
            field('CalculateField_GZXGU5OSNWO0', '100', 'CalculateField'),
        ]))

    def test_explicit_amount_wins_over_legacy_calculation_and_checks_details(self):
        from test_mexico_expense_contract import field, table
        row = self.header()
        row['form_component_values'].append(field('new_amount', '200', 'MoneyField'))
        config = self.config(amount=['new_amount'])
        source, lines = p.project(row, config, 'America/Mexico_City')
        self.assertEqual(source['total_amount'], '200.00')
        self.assertEqual(lines[0]['amount'], '200.00')
        self.assertEqual(lines[0]['base_issues'], [])
        row['form_component_values'].append(table(amount='100'))
        source, lines = p.project(row, config, 'America/Mexico_City')
        self.assertEqual(source['total_amount'], '200.00')
        self.assertIn('total', lines[0]['base_issues'])

    def test_missing_empty_invalid_or_ambiguous_amount_never_falls_back(self):
        from test_mexico_expense_contract import field, table
        for candidates in ([], [field('new_amount', '', 'MoneyField')],
                           [field('new_amount', 'bad', 'MoneyField')],
                           [field('new_amount', '200', 'MoneyField'), field('alternative', '300', 'MoneyField')]):
            with self.subTest(candidates=candidates):
                row = self.header()
                row['form_component_values'].extend([table(amount='100'), *candidates])
                source, lines = p.project(row, self.config(amount=['new_amount', 'alternative']), 'America/Mexico_City')
                self.assertIsNone(source['total_amount'])
                self.assertIn('amount', lines[0]['base_issues'])

    def test_table_override_replaces_default_and_preserves_raw_and_other_tables(self):
        from test_mexico_expense_contract import approval, approve_finance, table
        row = approve_finance(approval())
        old = table(amount='100')
        replacement = copy.deepcopy(old)
        replacement['id'] = 'new_table'
        row['form_component_values'][-1] = old
        row['form_component_values'].extend([replacement, table('TableField_21SE4NJZE8W00', amount='20')])
        source, lines = p.project(row, self.config(expense_table=['new_table']), 'America/Mexico_City')
        self.assertEqual(source['total_amount'], '120.00')
        self.assertEqual({line['table_id'] for line in lines}, {'new_table', 'TableField_21SE4NJZE8W00'})
        self.assertTrue(all(not line['base_issues'] for line in lines))
        self.assertEqual(json.loads(source['raw_json']), row)
        row['form_component_values'].reverse()
        _, reordered = p.project(row, self.config(expense_table=['new_table']), 'America/Mexico_City')
        self.assertEqual({line['line_key'] for line in lines}, {line['line_key'] for line in reordered})

    def test_missing_or_ambiguous_table_never_uses_replaced_default(self):
        from test_mexico_expense_contract import table
        for candidates in ([], ['new_table', 'alternative']):
            with self.subTest(candidates=candidates):
                row = self.header()
                row['form_component_values'].append(table(amount='100'))
                for key in candidates:
                    candidate = table(amount='100')
                    candidate['id'] = key
                    row['form_component_values'].append(candidate)
                _, lines = p.project(row, self.config(expense_table=['new_table', 'alternative']), 'America/Mexico_City')
                self.assertEqual(len(lines), 1)
                self.assertEqual(lines[0]['table_id'], 'header')
                self.assertIn('structure', lines[0]['base_issues'])

    def test_unmapped_legacy_forms_keep_calculated_total_behavior(self):
        source, lines = p.project(self.header(), self.config(), 'America/Mexico_City')
        self.assertEqual(source['total_amount'], '100.00')
        self.assertEqual(lines[0]['base_issues'], [])

    def test_zero_in_all_families_requires_review_even_after_manual_confirmation(self):
        from test_mexico_expense_contract import approval, approve_finance, table
        for family in ('expense', 'operating', 'purchase'):
            if family == 'expense':
                config = self.config()
                row = approve_finance(approval())
                row['form_component_values'][-1] = table(amount='0')
            else:
                config, row = sample(family)
                row['raw_payload']['formComponentValues'][1]['value'] = '0'
            with self.subTest(family=family):
                _, lines = p.project(row, config, 'America/Mexico_City')
                line = lines[0]
                previous = dict(line, company='Company MX', company_basis='manual', reviewed_fingerprint=line['row_fingerprint'])
                reviewed = s.contract.apply_review(line, previous, {}, 'v1')
                self.assertEqual(reviewed['amount'], '0.00')
                self.assertEqual(reviewed['review_state'], 'needs_review')
                self.assertIn('amount_pending', reviewed['issues'])

    def test_zero_header_in_gastos_is_not_confirmable(self):
        row = self.header()
        row['form_component_values'][-1]['value'] = '0'
        source, lines = p.project(row, self.config(), 'America/Mexico_City')
        self.assertEqual(source['total_amount'], '0.00')
        self.assertIn('amount_pending', lines[0]['base_issues'])



class ChildDoc(SimpleNamespace):
    # Native Frappe children expose get/update, not dict subscripting.
    def get(self, key, default=None):
        return getattr(self, key, default)
    def update(self, values):
        self.__dict__.update(values)


class TemplateSyncTests(unittest.TestCase):
    def settings(self):
        entries=[ChildDoc(**t.default_templates()[0]),ChildDoc(**t.default_templates()[4])]
        for i,v in enumerate(entries):v.update(dict(cursor='cursor-'+str(i),sync_until='snap',page_count=1))
        return frappe._dict(enabled=1,templates=entries,business_timezone='America/Mexico_City')

    def test_dispatch_uses_enabled_templates_and_stopping_does_not_remove_history(self):
        settings=self.settings();settings.templates[0].enabled=0
        with patch.object(s,'_settings',return_value=settings),patch.object(frappe,'enqueue') as enqueue:
            self.assertEqual(s.sync_page()['queued'],1)
            self.assertEqual(enqueue.call_args.kwargs['template_key'],s._template_key(settings.templates[1]))
        self.assertEqual(len(s._templates(settings)),2)

    def test_page_jobs_deduplicate_the_same_cursor_and_continue_with_a_different_job(self):
        template=self.settings().templates[0]
        with patch.object(frappe,'enqueue') as enqueue:
            s._enqueue_page(template)
            first=enqueue.call_args.kwargs
            s._enqueue_page(template,force=True)
            self.assertEqual(first['job_id'],enqueue.call_args.kwargs['job_id'])
            self.assertTrue(first['deduplicate'])
            self.assertTrue(first['enqueue_after_commit'])
            self.assertEqual(first['expected_cursor'],'cursor-0')
            template.cursor='next-page'
            s._enqueue_page(template)
            self.assertNotEqual(first['job_id'],enqueue.call_args.kwargs['job_id'])

    def test_obsolete_queued_page_does_not_read_or_advance_current_source(self):
        settings=self.settings();template=settings.templates[0]
        with patch.object(s,'_settings',return_value=settings),patch.object(frappe,'db',MagicMock()),patch.object(frappe,'cache',SimpleNamespace(lock=lambda *a,**kw:nullcontext())),patch.object(frappe,'local',SimpleNamespace(site='isolated')),patch.object(s.http,'read_template_page') as read,patch.object(s,'_save') as save:
            result=s.sync_page(template_key=s._template_key(template),expected_cursor='obsolete')
        self.assertTrue(result['superseded'])
        read.assert_not_called();save.assert_not_called()
        self.assertEqual(template.cursor,'cursor-0')

    def test_failed_only_dispatch_does_not_rescan_successful_templates(self):
        settings=self.settings()
        settings.templates[0].last_error='failed'
        with patch.object(s,'_settings',return_value=settings),patch.object(s,'_enqueue_page') as enqueue,patch('frappe.utils.background_jobs.get_job_status',return_value=None):
            result=s.sync_page(force=True,retry_failed=True)
        self.assertEqual(result['queued'],1)
        self.assertEqual(enqueue.call_args.args[0],settings.templates[0])

    def test_one_failure_preserves_other_template_cursor_and_retry_is_independent(self):
        settings=self.settings();first=settings.templates[0];second=settings.templates[1]
        with patch.object(s,'_',side_effect=lambda text:text),patch.object(s,'_settings',return_value=settings),patch.object(frappe,'db',MagicMock()),patch.object(frappe,'cache',SimpleNamespace(lock=lambda *a,**kw:nullcontext())),patch.object(frappe,'local',SimpleNamespace(site='isolated')),patch.object(s.http,'configuration',return_value=('http://qa',[s.contract.CORP_ID])),patch.object(s.http,'read_template_page',side_effect=s.http.OAInvalidCursor()),patch.object(s,'_save'),patch.object(frappe,'log_error'):
            self.assertFalse(s.sync_page(template_key=s._template_key(first))['ok'])
        self.assertIsNone(first.cursor);self.assertEqual(second.cursor,'cursor-1')
        self.assertIsNone(second.get("last_error"))

    def test_sync_only_changes_selected_template_after_all_rows(self):
        settings=self.settings();first=settings.templates[0];second=settings.templates[1];order=[]
        with patch.object(s,'_settings',return_value=settings),patch.object(frappe,'db',MagicMock()),patch.object(frappe,'cache',SimpleNamespace(lock=lambda *a,**kw:nullcontext())),patch.object(frappe,'local',SimpleNamespace(site='isolated')),patch.object(s.http,'configuration',return_value=('http://qa',[s.contract.CORP_ID])),patch.object(s.http,'read_template_page',return_value=([{'id':1},{'id':2}],{'syncUntil':'snap','hasMore':True,'nextCursor':'next'})),patch.object(s,'_upsert',side_effect=lambda row,settings:order.append(row['id'])),patch.object(s,'_save',side_effect=lambda doc:order.append('saved')),patch.object(s,'now_datetime',return_value='now'),patch.object(frappe,'enqueue'):
            self.assertTrue(s.sync_page(template_key=s._template_key(first))['ok'])
        self.assertEqual(order,[1,2,'saved']);self.assertEqual(first.cursor,'next');self.assertEqual(second.cursor,'cursor-1')

    def test_disabled_switch_does_not_invalidate_periods_but_finance_mapping_does(self):
        settings=self.settings();before=s._period_version(settings)
        settings.templates[0].enabled=0
        self.assertEqual(s._period_version(settings),before)
        settings.templates[0].finance_node_names='Different Finance'
        self.assertNotEqual(s._period_version(settings),before)


class TemplateSettingsTests(unittest.TestCase):
    def test_enabled_management_template_is_previewed_without_finance_nodes(self):
        from test_mexico_expense_service import Doc
        original=next(v for v in t.default_templates() if v['approval_type']=='expense')
        config,row=sample()
        config.update(finance_node_names='',finance_activity_ids='')
        row['raw_payload']['tasks']='malformed historical task data'
        settings=Doc(enabled=0,business_timezone='America/Mexico_City',mapping_version=s.digest([]),
            config_version='v1',company_mappings=[],templates=[ChildDoc(**original)])
        settings.set=lambda key,value:settings.update({key:value})
        with patch.object(s,'_require_manager'),patch.object(s,'_require_settings_access'),patch.object(s,'_settings',return_value=settings),patch.object(s.http,'configuration',return_value=('http://qa',[s.contract.CORP_ID])),patch.object(s.http,'read_template_page',return_value=([row],{})) as read,patch.object(s,'_save'),patch.object(frappe,'enqueue'):
            s.save_settings([],settings.business_timezone,0,'v1',[original,config],management=True)
        read.assert_called_once()
        self.assertEqual(read.call_args.kwargs,{'limit':3})
        self.assertEqual(settings.templates[0]['finance_node_names'],original['finance_node_names'])
        self.assertEqual(settings.templates[1]['finance_node_names'],'')
        self.assertEqual(settings.templates[1]['enabled'],1)

    def test_save_preserves_cursor_and_previews_new_enabled_template(self):
        from test_mexico_expense_service import Doc
        settings=Doc(enabled=0,business_timezone='America/Mexico_City',mapping_version=s.digest([]),config_version='v1',company_mappings=[],templates=[ChildDoc(**t.default_templates()[0],cursor='saved',sync_until='snapshot')])
        settings.set=lambda key,value:settings.update({key:value})
        entries=[dict(t.default_templates()[0],cursor='forged'),t.default_templates()[4]]
        with patch.object(s,'_require_manager'),patch.object(s,'_require_settings_access'),patch.object(s,'_settings',return_value=settings),patch.object(s.http,'configuration',return_value=('http://qa',[s.contract.CORP_ID])),patch.object(s,'_preview_template') as preview,patch.object(s,'_save'),patch.object(frappe,'enqueue'):
            s.save_settings([],settings.business_timezone,0,'v1',entries)
        self.assertEqual(settings.templates[0]['cursor'],'saved')
        self.assertEqual(settings.templates[0]['sync_until'],'snapshot')
        self.assertEqual(preview.call_count,1)
        self.assertEqual(preview.call_args.args[0]['approval_type'],'purchase')

    def test_historical_template_removal_is_rejected(self):
        from test_mexico_expense_service import Doc
        settings=Doc(config_version='v1',templates=t.default_templates())
        def throw(message,*args,**kwargs):raise frappe.ValidationError(message)
        with patch.object(s,'_',side_effect=lambda message:message),patch.object(s,'_require_manager'),patch.object(s,'_require_settings_access'),patch.object(s,'_settings',return_value=settings),patch.object(s.http,'configuration',return_value=('http://qa',[s.contract.CORP_ID])),patch.object(frappe,'throw',side_effect=throw),patch.object(s,'_save') as save:
            with self.assertRaisesRegex(frappe.ValidationError,'Disable historical'):
                s.save_settings([],'America/Mexico_City',0,'v1',[t.default_templates()[0]])
        save.assert_not_called()
