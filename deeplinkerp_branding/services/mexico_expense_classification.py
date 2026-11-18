"""Source-field classification, independent of accounting/payroll category codes.

Each configured level contains alternative component IDs (one populated branch).
Labels are source data: slashes are never interpreted as hierarchy separators.
"""
import hashlib
import json
import re
import unicodedata
from decimal import Decimal

DEFAULT_FIELDS = {
    'operating': [
        ['DDSelectField_RIQQC9Q99KW0'],
        ['DDSelectField_1CHIGP4L45K00', 'DDSelectField_1EZOBWHB6GSG0'],
        ['DDSelectField_1S3EM45QN6AO0', 'DDSelectField_I4XXNWDVGTC0',
         'DDSelectField_RBAM4AXHZK00', 'DDSelectField_19Z73JWHJX9C',
         'DDSelectField_U0QE7GNDJU80', 'DDSelectField_Y4F21W7ZRZ40',
         'DDSelectField_NENOZ8ZDOIO0', 'DDSelectField_Y6E8VEROPTS0'],
        ['DDSelectField_5ATUMNKTXRS0'],
    ],
    'purchase': [
        ['DDSelectField_187IN92KF6O00'],
        ['DDSelectField_1GI2S2FRSMHS0', 'DDSelectField_1XAH98VL8H0G0',
         'DDSelectField_684GAXLLU0C0', 'DDSelectField_1NUKXRS0JV4W0', 'DDSelectField_B5T2H28CDOG0'],
        ['DDSelectField_19DW8WAO09Z40', 'DDSelectField_MK5IW5ZERI80', 'DDSelectField_83WHML87C8G0'],
        ['DDSelectField_12SPJP5NT6M80'],
    ],
    'expense': [
        ['DDSelectField_1ATVGGUYKEZK0'],
        ['DDSelectField_E8MD2JTMHWO', 'DDSelectField_3QB6VJM7BS80', 'DDSelectField_J6QIEDKOACG0',
         'DDSelectField_1GVCIZGB2H9C0', 'DDSelectField_14SEL1HHI5Q80'],
        ['DDSelectField_1FC6WHB8HPNK0', 'DDSelectField_1VZRHPMQQDGG0', 'DDSelectField_12I3FN5NG174'],
    ],
}
KEY_PATTERN = r'[a-f0-9]{32}(?:/[a-f0-9]{32}){0,5}'


def validate_fields(value):
    if not value:
        return None
    levels = json.loads(value)
    if not isinstance(levels, list) or not 1 <= len(levels) <= 6:
        raise ValueError('Invalid classification fields')
    seen = set()
    for level in levels:
        if not isinstance(level, list) or not 1 <= len(level) <= 20:
            raise ValueError('Invalid classification fields')
        for field in level:
            if not isinstance(field, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,140}', field) or field in seen:
                raise ValueError('Invalid classification fields')
            seen.add(field)
    return levels


def fields_for(template):
    configured = validate_fields(template.get('classification_fields'))
    if configured:
        return configured
    from . import mexico_expense_templates as templates
    schema = templates.EXPENSE_FIELDS if template.get('approval_type') == 'expense' else templates.FIELDS
    mapping = json.loads(template.get('field_mapping') or '{}')
    remap = {id: mapping[key] for key, (ids, _, _) in schema.items() if key in mapping for id in ids}
    return [[replacement for id in level for replacement in remap.get(id, [id])]
            for level in DEFAULT_FIELDS[template.get('approval_type')]]


def resolved_levels(fields, template):
    """Reuse parser bindings, including exact historical names, for default levels."""
    levels = fields_for(template)
    if template.get('classification_fields'):
        return levels, []
    from . import mexico_expense_templates as templates
    schema = templates.EXPENSE_FIELDS if template.get('approval_type') == 'expense' else templates.FIELDS
    defaults = DEFAULT_FIELDS[template.get('approval_type')]
    ids = {id for level in defaults for id in level}
    schema = {key: spec for key, spec in schema.items() if ids.intersection(spec[0])}
    resolved, errors = templates.resolve(fields, template, schema=schema)
    if errors:
        return [], ['classification']
    mapping = json.loads(template.get('field_mapping') or '{}')
    replacements = {id: [resolved[key]['id']] if resolved.get(key, {}).get('id') else mapping.get(key, list(spec[0]))
                    for key, spec in schema.items() for id in spec[0]}
    return [[replacement for id in level for replacement in replacements.get(id, [id])]
            for level in defaults], []


def keyed_path(family, nodes):
    path, identities = [], [family]
    for node in nodes:
        identities.append(normalized(node['label']))
        token = hashlib.sha256(json.dumps(identities, ensure_ascii=False).encode()).hexdigest()[:32]
        key = (path[-1]['key'] + '/' if path else '') + token
        path.append({**node, 'key': key})
    return path


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', value).split()).casefold()


def extract(row, template):
    raw = row.get('raw_payload') or {}
    fields = row.get('form_component_values') or raw.get('formComponentValues') or []
    if isinstance(fields, str):
        fields = json.loads(fields)
    levels, problems = resolved_levels(fields, template)
    if problems:
        return [], problems
    path = []
    for alternatives in levels:
        populated = [f for f in fields if f.get('id') in alternatives and f.get('value') not in (None, '', '[]')]
        if not populated:
            continue
        if len(populated) != 1:
            return [], ['classification']
        field = populated[0]
        label = field.get('value')
        if field.get('componentType') != 'DDSelectField' or not isinstance(label, str) or not label.strip() or len(label) > 500:
            return [], ['classification']
        label = ' '.join(label.split())
        identity = normalized(label)
        if path and identity == normalized(path[-1]['label']):
            continue
        path.append({'label': label, 'field': field['id']})
    return keyed_path(template.get('approval_type'), path), [] if path else ['classification']


def composition(rows):
    """Build a tree from SQL grouped, permission-scoped line totals (exact cents)."""
    groups = {}
    for row in rows:
        path = json.loads(row.get('classification_path') or '[]')
        amount = Decimal(str(row.get('amount') or 0))
        for index, node in enumerate(path):
            entry = groups.setdefault(node['key'], dict(id=node['key'],
                parent=path[index - 1]['key'] if index else '', label=node['label'], translate=node.get('translate', False),
                amount=Decimal(0), direct_amount=Decimal(0), has_children=False))
            entry['amount'] += amount
            entry['has_children'] |= index < len(path) - 1
            if index == len(path) - 1:
                entry['direct_amount'] += amount
    return [{**r, 'amount': format(r['amount'], '.2f'), 'direct_amount': format(r['direct_amount'], '.2f')}
            for r in sorted(groups.values(), key=lambda r: (-r['amount'], r['id']))]


def line_path(row, template, line, path, problems):
    """Bound payroll tables take precedence over shared header subcategories."""
    family = template.get('approval_type')
    if family not in ('expense', 'operating'):
        return path, problems
    from . import mexico_expense_templates as templates
    raw = row.get('raw_payload') or {}
    fields = row.get('form_component_values') or raw.get('formComponentValues') or []
    if isinstance(fields, str):
        fields = json.loads(fields)
    schema = templates.EXPENSE_FIELDS if family == 'expense' else templates.FIELDS
    resolved, errors = templates.resolve(fields, template, schema=schema)
    if errors:
        return path, problems
    for logical in ('salary_table', 'social_table'):
        field = resolved.get(logical, {})
        if not field.get('id') or field.get('id') != line.get('table_id'):
            continue
        # Missing header selections on legacy payroll forms are safe; ambiguous or
        # invalid populated classification fields must still require review.
        levels, resolution_errors = resolved_levels(fields, template)
        selected_ids = {id for level in levels for id in level}
        populated = any(f.get('id') in selected_ids and f.get('value') not in (None, '', '[]') for f in fields)
        if resolution_errors or (problems and (populated or template.get('classification_fields'))):
            return path, problems or resolution_errors
        subtype_ids = {resolved.get(key, {}).get('id') for key in
                       ('personnel_category', 'tax_category', 'utilities_category', 'logistics_category', 'services_category',
                        'operating_category', 'selling_category')}
        # Discard the shared subtype and its descendants, keeping any valid parents.
        prefix = []
        for node in path:
            if node['field'] in subtype_ids:
                break
            prefix.append(node)
        from . import mexico_expense_contract as contract
        fallback = contract.SUBCATEGORIES['salary' if logical == 'salary_table' else 'social'][1]
        label = ' '.join((field.get('name') or fallback).split())
        if not label or len(label) > 500 or len(prefix) >= 6:
            return [], ['classification']
        return keyed_path(family, [*prefix, {'label': label, 'field': field['id'], 'translate': not bool(field.get('name'))}]), []
    return path, problems


def expense_root(row, template, line, path):
    """Unify known Gastos root categories across its historical field layouts.

Only existing, unambiguous parser categories supply a missing root. A configured
custom hierarchy or a genuinely new root option remains entirely source-driven.
"""
    from . import mexico_expense_contract as contract
    from . import mexico_expense_templates as templates
    category = line.get('category')
    if not path or template.get('approval_type') != 'expense' or template.get('classification_fields') or category not in contract.MAIN_ALIASES:
        return path
    raw = row.get('raw_payload') or {}
    fields = row.get('form_component_values') or raw.get('formComponentValues') or []
    if isinstance(fields, str):
        fields = json.loads(fields)
    resolved, errors = templates.resolve(fields, template, schema=templates.EXPENSE_FIELDS)
    main = resolved.get('expense_type', {})
    value = main.get('value')
    if errors or value and contract._match(value, contract.MAIN_ALIASES) != category:
        return path
    label = next(label for code, label, _ in contract.CATEGORIES if code == category)
    root = dict(label=label, field=main.get('id') or 'legacy_category', translate=True)
    if value:
        root['source_label'] = value
        path = path[1:]
    path = [root, *[dict(node) for node in path]]
    return keyed_path('expense', path)
