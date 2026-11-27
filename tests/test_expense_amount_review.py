"""Financial confirmation changes management eligibility, never OA monetary facts."""
from copy import deepcopy
import json

import frappe
import pytest

from deeplinkerp_branding.services import expense_amount_review as review
from deeplinkerp_branding.services import regional_expenses as regional
from deeplinkerp_branding.services import regional_expense_automatic as automatic
from deeplinkerp_branding.services import mexico_expenses as mx
from test_mexico_expense_templates import component, sample, table_row


def purchase():
    template, raw = sample('purchase')
    raw.update(status='COMPLETED', result='AGREE')
    raw['raw_payload'].update(status='COMPLETED', result='AGREE')
    fields = raw['raw_payload']['formComponentValues']
    fields[1]['value'] = '6,400.00'
    fields[2]['value'] = 'Peso比索'
    rows = [table_row(str(i), amount, qty, '0.64') for i, (amount, qty) in enumerate(
        [('3015', '4710'), ('768', '1200'), ('445', '695'), ('1853', '2895'), ('320', '500')], 1)]
    fields.append(component('TableField_119WFD19L8R40', json.dumps(rows), 'TableField', 'Purchase details'))
    source, lines = regional.facts(raw, template, 'America/Mexico_City')
    entries = [dict(line_key=lines[0]['line_key'], region='mx', company='MX', amount='6400.00',
        management_category='', assignment_mode='automatic', assignment_version='current', source_available=True)]
    return raw, template, source, lines, entries


def confirmed(source, lines):
    source[review.REVIEW] = review.decision(source, lines, note='Verified against approved amount', user='manager', when='2026-11-27 12:00:00')


@pytest.mark.parametrize('value', ['1e100000', '1e-100000', '0e100000', '0e-100000', '9' * 1000])
@pytest.mark.parametrize('operand', ['quantity', 'unit_price'])
def test_oversized_operands_are_rejected_without_expanding_evidence(value, operand):
    values = {'quantity': '1', 'unit_price': '1', 'total': '10'}
    values[operand] = value
    result = review.purchase_row(**values)
    assert 'amount' in result['issues']
    assert result['quantity'] is None and result['unit_price'] is None
    assert result['calculated_amount'] is None and result['difference'] is None
    assert len(json.dumps(result)) < 300


@pytest.mark.parametrize('quantity,price,total,expected', [
    ('4,710', '0.64', '3014.40', ('4710', '0.64')),
    ('2e3', '3.2e-1', '640.00', ('2000', '0.32')),
    ('1000000', '0.00123456', '1234.56', ('1000000', '0.00123456')),
])
def test_normal_operand_precision_and_grouped_numbers_are_preserved(quantity, price, total, expected):
    row = review.purchase_row(quantity, price, total)
    assert (row['quantity'], row['unit_price']) == expected
    assert row['calculated_amount'] == total and not row['issues']


@pytest.mark.parametrize('quantity', ['1e127', '1e-127'])
def test_operand_expansion_boundary_remains_bounded(quantity):
    row = review.purchase_row(quantity, '0', '0')
    assert 'amount' not in row['issues']
    assert len(row['quantity']) <= 129 and row['calculated_amount'] == '0.00'


def test_invalid_numeric_refresh_invalidates_old_confirmation_without_growing_facts():
    raw, template, source, lines, entries = purchase()
    confirmed(source, lines)
    rows = json.loads(raw['raw_payload']['formComponentValues'][-1]['value'])
    rows[0]['rowValue'][2]['value'] = '1e-100000000'
    raw['raw_payload']['formComponentValues'][-1]['value'] = json.dumps(rows)
    fresh, fresh_lines = regional.facts(raw, template, 'America/Mexico_City')
    review.preserve(fresh, fresh_lines, source)
    assert 'amount' in fresh_lines[0]['base_issues']
    assert fresh[review.EVIDENCE]['tables'][0]['rows'][0]['quantity'] is None
    assert len(regional.fact_payload(fresh, fresh_lines)) < 15000
    assert fresh[review.REVIEW]['state'] == 'stale'
    assert not review.accepted(fresh, fresh_lines, entries)


def test_exact_evidence_locates_rows_and_does_not_mutate_source():
    raw, template, source, lines, entries = purchase()
    before = deepcopy((raw, lines))
    proof = source[review.EVIDENCE]
    assert proof['header_amount'] == '6400.00'
    table = proof['tables'][0]
    assert (table['total'], table['calculated_total'], table['difference']) == ('6401.00', '6400.00', '1.00')
    assert [row['difference'] for row in table['rows']] == ['0.60', '0.00', '0.20', '0.20', '0.00']
    assert table['rows'][0]['calculated_amount'] == '3014.40'
    assert review.supported(source, lines, entries)
    confirmed(source, lines)
    assert review.accepted(source, lines, entries)
    assert (raw, lines) == before and 'total' in lines[0]['base_issues']
    assert source[review.REVIEW]['evidence'] == proof


@pytest.mark.parametrize('issue', ['amount', 'amount_pending', 'currency', 'structure', 'identity', 'allocation'])
def test_other_source_problems_cannot_be_confirmed_away(issue):
    _, _, source, lines, entries = purchase()
    lines[0]['base_issues'].append(issue)
    confirmed(source, lines)
    assert not review.supported(source, lines, entries)
    assert not review.accepted(source, lines, entries)


@pytest.mark.parametrize('change', ['split', 'multiple_lines', 'manual', 'unavailable', 'detail', 'wrong_amount'])
def test_only_one_original_header_and_one_automatic_allocation_are_supported(change):
    _, _, source, lines, entries = purchase()
    if change == 'split':
        entries.append({**entries[0], 'amount': '0.00'})
    elif change == 'multiple_lines':
        lines.append({**lines[0], 'line_key': 'other'})
    elif change == 'manual':
        entries[0]['assignment_mode'] = 'manual'
    elif change == 'unavailable':
        entries[0]['source_available'] = False
    elif change == 'detail':
        lines[0]['table_id'] = 'table'
    else:
        entries[0]['amount'] = '6401.00'
    assert not review.supported(source, lines, entries)


def test_detail_change_invalidates_review_even_with_unchanged_header_and_total():
    raw, template, source, lines, entries = purchase()
    confirmed(source, lines)
    changed = deepcopy(raw)
    table = changed['raw_payload']['formComponentValues'][-1]
    rows = json.loads(table['value'])
    rows[0]['rowValue'][2]['value'] = '4711'
    table['value'] = json.dumps(rows)
    fresh, fresh_lines = regional.facts(changed, template, 'America/Mexico_City')
    assert fresh['total_amount'] == source['total_amount']
    assert fresh[review.EVIDENCE]['tables'][0]['total'] == '6401.00'
    review.preserve(fresh, fresh_lines, source)
    assert fresh[review.REVIEW]['state'] == 'stale'
    assert not review.accepted(fresh, fresh_lines, entries)
    restored, restored_lines = regional.facts(raw, template, 'America/Mexico_City')
    review.preserve(restored, restored_lines, fresh)
    assert restored[review.REVIEW]['state'] == 'stale'
    assert not review.accepted(restored, restored_lines, entries)


def test_workflow_update_does_not_erase_valid_monetary_evidence():
    raw, template, source, lines, entries = purchase()
    confirmed(source, lines)
    raw.update(status='TERMINATED', result='')
    raw['raw_payload'].update(status='TERMINATED', result='')
    fresh, fresh_lines = regional.facts(raw, template, 'America/Mexico_City')
    review.preserve(fresh, fresh_lines, source)
    assert review.accepted(fresh, fresh_lines, entries)
    assert fresh['approval_status'] != 'approved'


@pytest.mark.parametrize('origin', ['cn', 'mx'])
def test_only_unified_reporting_adopts_amount_and_keeps_original_validation(monkeypatch, origin):
    from budget.services import expense_management
    raw, template, source, lines, entries = purchase()
    record = frappe._dict(name=source['source_key'], corp_id=source['corp_id'], process_code=source['process_code'],
        process_instance_id=source['process_instance_id'], origin=origin, state='stale', modified='revision',
        facts_json=regional.fact_payload(source, lines), allocations_json=json.dumps(entries))
    monkeypatch.setattr(regional, 'records', lambda: [record])
    monkeypatch.setattr(mx, '_settings', lambda: {})
    monkeypatch.setattr(automatic, 'configuration', lambda *_: {'version': 'current'})
    context = {'companies': {'MX'}, 'manager': True}
    def projected(enabled=True):
        return expense_management.normalize(regional.management_projection(context, include_legacy=False,
            submission_fallback=True, amount_reviews=enabled)[1][0])
    assert not projected()['eligible']
    confirmed(source, lines)
    record.facts_json = regional.fact_payload(source, lines)
    row = projected()
    assert row['eligible'] and row['amount'] == '6400.00' and row['amount_reviewed']
    assert 'total' not in row['issues']
    assert not projected(False)['eligible']
    assert 'total' in regional.decode(record.facts_json)[1][0]['base_issues']
    entries[0]['source_available'] = False
    record.allocations_json = json.dumps(entries)
    assert not projected()['eligible']


def test_sync_preserves_review_until_form_changes_and_never_writes_it_to_native_fields(monkeypatch):
    raw, template, source, lines, entries = purchase()
    confirmed(source, lines)
    record = frappe._dict(name=source['source_key'], origin='mx', state='stale',
        facts_json=regional.fact_payload(source, lines), allocations_json=json.dumps(entries))
    monkeypatch.setattr(regional, 'record_for', lambda *a, **k: record)
    monkeypatch.setattr(regional, 'save_record', lambda doc: None)
    monkeypatch.setattr(automatic, 'configuration', lambda *_: {'version': 'current',
        'companies': {'Company MX': 'Mexico'}, 'aliases': {}, 'rules': {}})
    monkeypatch.setattr(automatic, 'mapping_version', lambda *_: 'current')
    monkeypatch.setattr(frappe, 'get_installed_apps', lambda: [])
    settings = frappe._dict(business_timezone='America/Mexico_City', templates=[template])
    native, _ = regional.project_for_mx(raw, template, settings)
    saved_source, saved_lines = regional.decode(record.facts_json)
    assert review.accepted(saved_source, saved_lines, regional.decode(record.allocations_json))
    assert review.REVIEW not in native and review.EVIDENCE not in native
    fields = raw['raw_payload']['formComponentValues']
    fields.append(component('TextField_extra', 'Changed detail note', 'TextField'))
    regional.project_for_mx(raw, template, settings)
    saved_source, saved_lines = regional.decode(record.facts_json)
    assert saved_source[review.REVIEW]['state'] == 'stale'
    assert not review.accepted(saved_source, saved_lines, regional.decode(record.allocations_json))
