"""Template configuration and shared, versioned schema bindings. No IO."""
import json
import re

from . import mexico_expense_contract as c

CONFIG_FIELDS = ('enabled', 'approval_type', 'template_name', 'corp_id', 'process_code',
                 'finance_node_names', 'finance_activity_ids', 'field_mapping', 'classification_fields')
STATE_FIELDS = ('cursor', 'sync_until', 'page_count', 'last_sync_at', 'last_error', 'sync_state')
DEFAULT_CODES = {
    'operating': ('PROC-D3ED660B-A5D4-4516-BC82-D83E52B5FEF8', 'PROC-75FEF975-C79F-44A3-A02C-21734C2DBC49', 'PROC-CCB314E9-1458-4D53-9EF2-F68A21EA018D'),
    'expense': (c.PROCESS_CODE,),
    'purchase': ('PROC-525D584D-ED10-4B8B-9C7A-9D67257BF6EE', 'PROC-98934E07-96ED-491C-8650-DFE8C3B707BF', 'PROC-CA023EF7-CE63-4A0B-B4BB-66482C0E9972'),
}
# Logical field -> known component IDs, exact historical names, allowed component kinds.
FIELDS = {
 'entity': (('CascadeField_G8SEQLCWG1S0','DDSelectField_1G5IFPI8IKQO0'), ('服务主体Cliente',), ('CascadeField','DDSelectField','DDMultiSelectField')),
 'amount': (('MoneyField_1FPZGEQOZ4M80',), ('金额importe',), ('MoneyField','CalculateField','NumberField')),
 'currency': (('DDSelectField_199XDJ3I2RMO0',), ('币种Moneda',), ('DDSelectField',)),
 'planned': (('DDDateField_1Q7ARFPRW8OW0',), ('付款日期Fecha de pago',), ('DDDateField',)),
 'description': (('TextareaField_11O6SVHWPA0W0','TextareaField_S0G37XGF2TC0'), ('事项说明Explicación de asuntos','规格明细需求说明Descripción de las necesidades de detalles'), ('TextareaField','TextField')),
 'payee': (('TextareaField_FS04VIPFB4W0',), ('收款人beneficiario',), ('TextareaField','TextField')),
 'payment_terms': (('DDSelectField_FGTWRBRZ2340',), ('付款条件Términos de pago',), ('DDSelectField',)),
 'production': (('DDSelectField_JTFRFTAFTTO0','DDSelectField_14ZWYSLFU1400'), ('生产/非生产Producción / No producción',), ('DDSelectField',)),
 'region': (('DDSelectField_1FEAISH1BZ4W0',), ('执行地区Región de ejecución',), ('DDSelectField',)),
 'request_type': (('DDSelectField_FXN62BAZA4G0',), ('申请类型Tipo de trámite',), ('DDSelectField',)),
 'salary_type': (('DDMultiSelectField_ZCB0DINS0400',), ('工资salario',), ('DDMultiSelectField','DDSelectField')),
 'expense_type': (('DDSelectField_RIQQC9Q99KW0',), ('支出类型',), ('DDSelectField',)),
 'operating_category': (('DDSelectField_1CHIGP4L45K00',), ('管理支出Gastos de operación',), ('DDSelectField',)),
 'selling_category': (('DDSelectField_1EZOBWHB6GSG0',), ('销售费用 Gastos de venta',), ('DDSelectField',)),
 'purchase_category': (('DDSelectField_187IN92KF6O00',), ('采购支出Gastos de Compra',), ('DDSelectField',)),
 'delivery': (('DDDateField_RP6F1EQVIIO0',), ('交付日期Fecha de entrega',), ('DDDateField',)),
 'budget': (('TextField_HQJISZX4Y480','TextField_5RP2TU3KHWW0'), ('本月预算金额Importe presupuestado del mes',), ('TextField','MoneyField','NumberField')),
 'budget_used': (('TextField_SKUJ6DR8EFK0','TextField_1TJT6NM8THCW0'), ('本月预算已用金额Importe utilizado del presupuesto mensual',), ('TextField','MoneyField','NumberField')),
 'salary_table': ((), ('薪酬税费总支出(分部门)Gasto total salarial-fiscal por depa',), ('TableField',)),
 'social_table': (('TableField_G2ELEALN0S80',), ('社保公积金seguridad social y fondo de vivienda',), ('TableField',)),
 'office_table': ((), ('办公场地总费用Gtos. tot. oficinas (alq., luz, internet)',), ('TableField',)),
 'items_table': (('TableField_119WFD19L8R40',), ('需求明细Desglose de los gastos',), ('TableField',)),
 'processors_table': (('TableField_19CDW7WYJFZ40',), ('加工商明细detalles de los procesadores',), ('TableField',)),
}

# Gastos has a different field layout; overrides retain the archived component identity.
EXPENSE_IDS = {
    'entity': 'DDMultiSelectField_RWULY2WAUCG0', 'amount': 'MoneyField_1F4PBZ6A5UOW',
    'currency': 'DDSelectField_F7MDR4TJ1JS0', 'planned': 'DDDateField_1URIJHJJMC680',
    'description': 'TextareaField_70ISZLEAO8G0', 'payee': 'TextField_D8GN6OUQBLS0',
    'payment_terms': 'DDSelectField_1KMJWCE24KBK0', 'expense_type': 'DDSelectField_1ATVGGUYKEZK0',
    'personnel_category': 'DDSelectField_E8MD2JTMHWO', 'tax_category': 'DDSelectField_3QB6VJM7BS80',
    'utilities_category': 'DDSelectField_J6QIEDKOACG0', 'logistics_category': 'DDSelectField_1GVCIZGB2H9C0',
    'services_category': 'DDSelectField_14SEL1HHI5Q80',
    'expense_table': 'TableField_1QM4FQVMBI0W0', 'salary_table': 'TableField_ENJNMIBMUAO0',
    'social_table': 'TableField_21SE4NJZE8W00',
}
EXPENSE_FIELDS = {key: ((component_id,), (),
    ('TableField',) if key.endswith('_table') else FIELDS[key][2] if key in FIELDS else ('DDSelectField',))
    for key, component_id in EXPENSE_IDS.items()}

def default_templates():
    return [dict(enabled=1, approval_type=kind, template_name=c.APPROVAL_FORMS[kind][0]+' '+str(index+1),
                 corp_id=c.CORP_ID, process_code=code, finance_node_names='MX财务Finanzas',
                 finance_activity_ids=c.FINANCE_ACTIVITY_ID if kind=='expense' else '', field_mapping='{}')
            for kind,codes in DEFAULT_CODES.items() for index,code in enumerate(codes)]

def tokens(value):
    return [v.strip() for v in re.split(r'[,\n，]+', value or '') if v.strip()]

def normalize(entry, *, require_finance=True):
    if not isinstance(entry,dict):raise ValueError('Invalid approval template')
    out={k:entry.get(k) for k in CONFIG_FIELDS}
    if out['enabled'] not in (0,1,'0','1',False,True):raise ValueError('Invalid approval template')
    out['enabled']=int(out['enabled'])
    for key in CONFIG_FIELDS[1:]:
        value=out[key] or ''
        if not isinstance(value,str) or len(value) > (10000 if key in ('field_mapping', 'classification_fields') else 2000):raise ValueError('Invalid approval template')
        out[key]=value.strip()
    if out['approval_type'] not in c.APPROVAL_FORMS or not re.fullmatch(r'PROC-[A-Fa-f0-9-]{36}',out['process_code']) or not re.fullmatch(r'[A-Za-z0-9_-]{1,140}',out['corp_id']):raise ValueError('Invalid approval template')
    mapping=json.loads(out['field_mapping'] or '{}')
    schema = EXPENSE_FIELDS if out['approval_type']=='expense' else FIELDS
    if not isinstance(mapping,dict) or set(mapping)-schema.keys():raise ValueError('Invalid template field mapping')
    for key,value in mapping.items():
        if not isinstance(value,list) or not value or len(value)>20 or any(not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,140}',v) for v in value):raise ValueError('Invalid template field mapping')
    out['field_mapping']=json.dumps(mapping,sort_keys=True)
    if require_finance and not tokens(out['finance_node_names']) and not tokens(out['finance_activity_ids']):raise ValueError('Finance nodes are required')
    from .mexico_expense_classification import validate_fields
    validate_fields(out['classification_fields'])
    return out

def finance_options(template):
    return dict(activity_ids=tokens(template.get('finance_activity_ids')),node_names=tokens(template.get('finance_node_names')))

def find(templates,corp,code):
    matches=[t for t in templates if t.get('corp_id')==corp and t.get('process_code')==code]
    if len(matches)!=1:raise ValueError('Approval template is not configured')
    return matches[0]

def resolve(components,template, schema=None):
    overrides=json.loads(template.get('field_mapping') or '{}'); result={}; issues=[]
    for key,(ids,names,kinds) in (schema or FIELDS).items():
        ids=overrides.get(key,ids)
        found=[f for f in components if f.get('id') in ids]
        if not found and key not in overrides:
            found=[f for f in components if c.normalized(f.get('name')) in {c.normalized(n) for n in names}]
        populated=[f for f in found if f.get('value') not in (None,'','[]')]
        if len(populated)>1 or any(f.get('componentType') not in kinds for f in found):issues.append('structure');continue
        result[key]=(populated or found or [{}])[0]
    return result,issues
