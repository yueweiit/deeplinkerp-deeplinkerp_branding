"""Operating and purchase adapters. Monetary ledger lines and item detail stay separate."""
import json
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from . import mexico_expense_contract as c
from . import mexico_expense_templates as templates


def text(value):
    if value is None:return ''
    if isinstance(value,str):
        try:
            parsed=json.loads(value)
            if isinstance(parsed,list):return ' / '.join(text(v) for v in parsed)
        except (ValueError,TypeError):pass
        return value.strip()
    if isinstance(value,(list,tuple)):return ' / '.join(text(v) for v in value)
    if isinstance(value,dict):return text(value.get('label') or value.get('name') or '')
    return str(value)


def _project(row, template, business_timezone, *, ignore_region=False):
    template = {key: template.get(key) for key in templates.CONFIG_FIELDS}
    if row.get('corp_id')!=template['corp_id'] or row.get('process_code')!=template['process_code'] or not row.get('process_instance_id'):
        raise ValueError('Unexpected Mexico approval identity')
    options=templates.finance_options(template)
    if template['approval_type']=='expense':
        source,lines=c.project(row,business_timezone,{**template,'finance_options':options})
        source.update(approval_type='expense',details_json='{}')
        for line in lines:line.update(approval_type='expense',business_category='',salary_type='')
        return source,lines
    raw=row.get('raw_payload') or {}
    if not isinstance(raw,dict):raise ValueError('Invalid raw approval')
    components=c._json(row.get('form_component_values') or raw.get('formComponentValues') or [])
    if not isinstance(components,list) or len(components)>500 or any(not isinstance(v,dict) for v in components):raise ValueError('Invalid form components')
    schema = {key: spec for key, spec in templates.FIELDS.items() if key != 'region'} if ignore_region else None
    fields,errors=templates.resolve(components,template,schema=schema)
    if len({f.get('id') for f in components})!=len(components):errors.append('structure')
    value=lambda key:fields.get(key,{}).get('value')
    status=c.approval_status(row.get('status'),row.get('result'),bool(row.get('deleted_at')))
    if any(raw.get(k) and row.get(k) and str(raw[k]).casefold()!=str(row[k]).casefold() for k in ('status','result')):status='unknown';errors.append('structure')
    currency=c._match(value('currency'),{'MXN':('比索','Peso','MXN'),'USD':('美元','Dólar','USD'),'CNY':('人民币','RMB','CNY')})
    if not currency:errors.append('currency')
    entity,multiple=c._entity(value('entity'))
    department=''
    if fields.get('entity',{}).get('componentType')=='CascadeField' and isinstance(value('entity'),str) and '/' in value('entity'):
        entity,department=value('entity').split('/',1);entity=entity.strip();multiple=False
    if multiple:errors.append('allocation')
    period,issues=c.finance_period(row,business_timezone,**options);errors+=issues
    created=datetime.fromisoformat(str(row['create_time']).replace('Z','+00:00'))
    if created.tzinfo is None:raise ValueError('Source timestamp requires timezone')
    planned=text(value('planned'))
    try:planned=date.fromisoformat(planned).isoformat() if planned else None
    except ValueError:planned=None;errors.append('structure')
    amount=c.exact_amount(value('amount'))
    if amount is None:errors.append('amount')
    if amount=='0.00':errors.append('amount_pending')
    family=template['approval_type'];sub=None
    path=[text(value(k)) for k in ('expense_type','operating_category','selling_category','purchase_category') if value(k)]
    if family=='purchase':
        sub=c._match(value('purchase_category'),{k:v[2] for k,v in c.SUBCATEGORIES.items() if k.startswith('purchase_')})
    else:
        main=text(value('operating_category'))
        sub=c._match(main,{'salary':('工资','Salario'),'social':('社保公积金','seguridad social'),'bonus':('奖金','Bonificaciones'),'welfare':('职工福利','Gastos de beneficios'),'tax_general':('税费','Impuestos'),'admin_other':('管理费用','车辆使用费','财务相关费用')})
        if not sub and c._match(value('expense_type'),{'selling':('销售费用','Gastos de venta')}):sub='selling_other'
    if not sub:errors.append('category')
    # Retain source-specific subcategories for drilldown without deriving them from prose.
    category_ids={'DDSelectField_1S3EM45QN6AO0','DDSelectField_5ATUMNKTXRS0','DDSelectField_I4XXNWDVGTC0','DDSelectField_RBAM4AXHZK00','DDSelectField_19Z73JWHJX9C','DDSelectField_U0QE7GNDJU80','DDSelectField_Y4F21W7ZRZ40','DDSelectField_NENOZ8ZDOIO0','DDSelectField_1GI2S2FRSMHS0','DDSelectField_19DW8WAO09Z40','DDSelectField_MK5IW5ZERI80','DDSelectField_1XAH98VL8H0G0','DDSelectField_684GAXLLU0C0','DDSelectField_1NUKXRS0JV4W0','DDSelectField_B5T2H28CDOG0','DDSelectField_83WHML87C8G0'}
    path += [text(f.get('value')) for f in components if f.get('id') in category_ids and f.get('value')]
    business_category=' / '.join(dict.fromkeys(path))
    source_key=c.digest([row['corp_id'],row['process_instance_id']]);lines=[];seen=set();details=[]
    def add(table_id,row_id,owner,raw_amount,description,kind='entity',forced_sub=None,owner_id=''):
        owner,multi=c._entity(owner);problems=[issue for issue in errors if issue != 'allocation' or table_id == 'header']
        if multi:problems.append('allocation')
        parsed=c.exact_amount(raw_amount)
        if parsed is None:problems.append('amount')
        if parsed=='0.00':problems.append('amount_pending')
        if not row_id or (table_id,str(row_id)) in seen:problems.append('identity');row_id='unresolved:'+str(len(lines))
        seen.add((table_id,str(row_id)))
        selected=forced_sub or sub
        facts=dict(source_entity=owner,entity_kind=kind,entity_key=c.entity_key(kind,owner),category=c.SUBCATEGORIES[selected][0] if selected else None,
            subcategory=selected,amount_raw=str(raw_amount if raw_amount is not None else ''),amount=parsed,currency=currency,description=text(description),
            table_id=table_id,source_row_id=str(row_id),approval_type=family,business_category=business_category,salary_type=text(value('salary_type')))
        lines.append(dict(facts,**period,line_key=c.digest([source_key,table_id,row_id]),row_fingerprint=c.digest(facts),base_issues=problems,source=source_key,_source_entity_id=owner_id))
    known_tables={f.get('id'):k for k,f in fields.items() if k.endswith('_table') and f}
    for f in components:
        if f.get('componentType')!='TableField' or not f.get('value'):continue
        table_id=f.get('id');kind=known_tables.get(table_id)
        try:
            entries=c._json(f['value'])
            if not isinstance(entries,list) or len(entries)>2000:raise ValueError()
            if not entries:continue
            if not kind:errors.append('structure')
            table_rows=[];totals=[];seen_rows=set()
            for entry in entries:
                if not isinstance(entry,dict) or not isinstance(entry.get('rowValue'),list):raise ValueError()
                cells=entry['rowValue']
                if any(not isinstance(v,dict) for v in cells) or len({v.get('key') for v in cells})!=len(cells):raise ValueError()
                row_id=entry.get('rowNumber')
                if not row_id or row_id in seen_rows:errors.append('identity')
                seen_rows.add(row_id)
                # Exact ID first; exact label aliases for historical copies only.
                def cell_field(ids=(),labels=()):
                    matches=[v for v in cells if v.get('key') in ids]
                    if not matches:matches=[v for v in cells if c.normalized(v.get('label')) in {c.normalized(x) for x in labels}]
                    return matches[0] if len(matches)==1 else {}
                def cell(ids=(),labels=()):
                    return cell_field(ids,labels).get('value')
                total=cell(('CalculateField_1D7YT3J5Z4YO0','CalculateField_FV6G9ZJPPEG0','MoneyField_X5KBWAODJ1S0'),('总金额Monto Total','总金额（元）Importe Total (CNY)','金额（元）Monto (yuan)'))
                parsed=c.exact_amount(total);totals.append(parsed)
                if family=='purchase' and kind in ('items_table','processors_table'):
                    qty=cell(('NumberField_6Q22XFQ51VW0','NumberField_1VD3WWVT2QCG0'),('数量Cantidad',))
                    price=cell(('NumberField_1P237DCILQKG0','NumberField_10YZCWTPVZR40'),('单价Precio','单价Precio Unitario'))
                    try:
                        quantity,unit_price=Decimal(str(qty)),Decimal(str(price))
                        if not quantity.is_finite() or not unit_price.is_finite() or quantity<0 or unit_price<0:raise ValueError()
                        if parsed is None or (quantity*unit_price).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)!=Decimal(parsed):errors.append('total')
                        if quantity>0 and unit_price==0:errors.append('amount_pending')
                    except (InvalidOperation,ValueError):errors.append('amount')
                table_rows.append({'row_id':str(row_id or ''),'cells':[{'label':str(v.get('label') or v.get('key') or ''),'value':text(v.get('value'))} for v in cells if v.get('value') not in (None,'') and not str(v.get('key','')).startswith(('DDPhoto','DDAttachment')) and '电话' not in str(v.get('label',''))]})
                if family=='operating' and kind in ('salary_table','social_table','office_table'):
                    owner_field=cell_field(labels=('部门Departamento',)) if kind!='office_table' else fields.get('entity',{})
                    owner=owner_field.get('value') if kind!='office_table' else entity
                    add(table_id,row_id,owner,total,cell(labels=('备注Nota','费用明细Detalle de gastos')), 'department' if kind!='office_table' else 'entity', 'social' if kind=='social_table' else 'salary' if kind=='salary_table' else None, c.organization_id(owner_field))
            details.append({'label':str(f.get('name') or table_id),'rows':table_rows})
            if family=='purchase' and kind in ('items_table','processors_table'):
                if any(v is None for v in totals) or amount is not None and sum((Decimal(v) for v in totals if v is not None),Decimal(0))!=Decimal(amount):errors.append('total')
        except (ValueError,TypeError,KeyError):errors.append('structure')
    if not lines:add('header','header',entity,value('amount'),value('description'),owner_id=c.organization_id(fields.get('entity',{})))
    if family=='operating' and amount is not None and (any(l['amount'] is None for l in lines) or sum((Decimal(l['amount']) for l in lines if l['amount'] is not None),Decimal(0))!=Decimal(amount)):errors.append('total')
    for line in lines:
        inherited=[issue for issue in errors if issue != 'allocation' or line['table_id'] == 'header']
        line['base_issues']=sorted(set(line['base_issues']+inherited));line['row_fingerprint']=c.digest([line['row_fingerprint'],line['base_issues'],amount])
    source=dict(source_key=source_key,corp_id=row['corp_id'],process_code=row['process_code'],process_instance_id=row['process_instance_id'],approval_no=row.get('business_id'),
        applicant=row.get('originator_user_name') or row.get('originator_user_id'),approval_status=status,
        applied_at=created.astimezone(timezone.utc).isoformat().replace('+00:00','Z'),request_date=created.astimezone(ZoneInfo(business_timezone)).date().isoformat(),
        planned_payment_date=planned,currency=currency,total_amount=amount,payee=text(value('payee')),payment_terms=text(value('payment_terms')),
        source_version=c.digest(json.loads(json.dumps(row,default=str))),raw_json=json.dumps(row,ensure_ascii=False,default=str),
        finance_approved_at=period['finance_approved_at'],finance_approved_date=period['finance_approved_date'],approval_type=family,
        details_json=json.dumps({'fields':{'Production Type':text(value('production')),'Execution Region':text(value('region')),'Application Type':text(value('request_type')),'Source Department':department,'Delivery Date':text(value('delivery')),'Budget Snapshot':text(value('budget')),'Used Budget Snapshot':text(value('budget_used'))},'tables':details},ensure_ascii=False))
    return source,lines


def project(row, template, business_timezone, *, ignore_region=False):
    from . import mexico_expense_classification as classification
    template = {key: template.get(key) for key in templates.CONFIG_FIELDS}
    source, lines = _project(row, template, business_timezone, ignore_region=ignore_region)
    path, problems = classification.extract(row, template)
    conflict = False
    if template['approval_type'] == 'expense':
        raw = row.get('raw_payload') or {}
        components = c._json(row.get('form_component_values') or raw.get('formComponentValues') or [])
        fields, _ = templates.resolve(components, template, schema=templates.EXPENSE_FIELDS)
        main = c._match(fields.get('expense_type', {}).get('value'), c.MAIN_ALIASES)
        candidates = {c._match(fields.get(key, {}).get('value'), {k: v[2] for k, v in c.SUBCATEGORIES.items()})
                      for key in ('personnel_category', 'tax_category', 'utilities_category', 'logistics_category', 'services_category')} - {None}
        conflict = len(candidates) > 1 or bool(main and any(c.SUBCATEGORIES[sub][0] != main for sub in candidates))
    source_path, source_problems = path, problems
    for line in lines:
        legacy_fingerprint = line['row_fingerprint']
        path, problems = classification.line_path(row, template, line, source_path, source_problems)
        # New source options are valid classifications without a hardcoded alias.
        # Keep them out of payroll assumptions; fixed accounting reports have an explicit fallback column.
        if path and not problems and not conflict and 'category' in line['base_issues']:
            line['category'], line['subcategory'] = 'source_other', None
            line['base_issues'] = [issue for issue in line['base_issues'] if issue != 'category']
        path = classification.expense_root(row, template, line, path)
        line.update(classification_path=json.dumps(path, ensure_ascii=False),
                    classification_key=path[-1]['key'] if path else '')
        line['base_issues'] = sorted(set(line['base_issues'] + problems))
        line['business_category'] = ' → '.join(node.get('source_label') or node['label'] for node in path)
        line['_legacy_row_fingerprint'] = legacy_fingerprint  # consumed before persistence
        line['row_fingerprint'] = 'mx-class-v1:' + c.digest([
            legacy_fingerprint, line['category'], line['subcategory'],
            line['classification_key'], line['base_issues'],
        ])
    return source, lines


def compatible_previous_review(previous, line, source_version, legacy_fingerprint):
    """Upgrade a pre-classification review only against the identical saved snapshot.

This is a one-way bridge: versioned fingerprints never enter this branch.
Changed paths, business facts, source snapshots and already-stale reviews must
follow the ordinary stale-review handling.
"""
    if not previous or not legacy_fingerprint:
        return previous
    old_fingerprint = previous.get('row_fingerprint') or ''
    if (old_fingerprint != legacy_fingerprint
            or previous.get('reviewed_fingerprint') != old_fingerprint
            or old_fingerprint.startswith('mx-class-v1:')
            or not source_version or previous.get('source_version') != source_version
            or 'stale' in c._json(previous.get('issues') or '[]')
            or previous.get('category') != line.get('category')
            or previous.get('subcategory') != line.get('subcategory')
            or previous.get('classification_key') and previous.get('classification_key') != line.get('classification_key')):
        return previous
    return {**previous, 'reviewed_fingerprint': line['row_fingerprint']}
