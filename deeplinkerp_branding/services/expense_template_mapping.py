"""Optional bindings inside existing field_mapping JSON; no IO or schema migration."""
import json
import re

ID_PATTERN = r"[A-Za-z0-9_-]{1,140}"
RESERVED = {"table_fields", "table_currency"}
CHILD_TYPES = {
    "entity": ("DepartmentField", "DDSelectField", "DDMultiSelectField", "CascadeField"),
    "amount": ("MoneyField", "CalculateField", "NumberField"),
    "quantity": ("NumberField", "CalculateField"),
    "unit_price": ("MoneyField", "NumberField", "CalculateField"),
    "description": ("TextField", "TextareaField"),
    "label": ("TextField", "TextareaField", "DDSelectField"),
    "original_amount": ("MoneyField", "NumberField", "CalculateField"),
    "currency": ("DDSelectField",), "exchange_rate": ("NumberField", "CalculateField"),
}
TABLE_ROLES = {
    "operating": {
        "allocation_table": ("entity", "amount", "description"),
        "salary_table": ("entity", "amount", "description"),
        "social_table": ("entity", "amount", "description"),
        "office_table": ("amount", "description"),
    },
    "purchase": {
        "items_table": ("amount", "quantity", "unit_price"),
        "processors_table": ("amount", "quantity", "unit_price"),
    },
    "expense": {key: ("entity", "amount", "description") for key in
        ("expense_table", "salary_table", "social_table")},
    "budget": {key: ("label", "amount", "original_amount", "currency", "exchange_rate") for key in
        ("hr_table", "budget_office_table", "operation_table")},
}
BUDGET_FIELDS = {
    "amount": (("MoneyField_1JA1U3NP3Q4G0", "MoneyField_NAO8CPC23000"), ("预算总金额", "Presupuesto Total"), ("MoneyField", "CalculateField", "NumberField")),
    "entity": (("DepartmentField-K0C3VCCH",), ("部门Departamento",), ("DepartmentField", "DDSelectField", "CascadeField")),
    "budget_month": (("DDDateField_RG3T9I5UX1S0",), ("预算月份", "Mes presupuestario"), ("DDDateField",)),
    "application_date": (("DDDateField_TFZ1WT5RP9C0",), ("申请日期", "Fecha de solicitud"), ("DDDateField",)),
    "expense_type": (("DDSelectField_1Y8RI4CCPWN40",), ("预算类型", "Tipo de presupuesto"), ("DDSelectField",)),
    "description": (("TextareaField_1JHTRP1E89TS0",), ("备注", "Observaciones"), ("TextareaField", "TextField")),
    "hr_table": (("TableField_1VIO6UO9ZJI80",), (), ("TableField",)),
    "budget_office_table": (("TableField_MM293T6SVHC0",), (), ("TableField",)),
    "operation_table": (("TableField_7MP8ZOFXR3S0",), (), ("TableField",)),
}
APPLICATION_DATE = (("DDDateField_29I59O630HUS", "DDDateField_B0K470VSL0O0"),
    ("申请日期", "Fecha de solicitud"), ("DDDateField",))


def decode(template):
    value = (template or {}).get("field_mapping") or {}
    return json.loads(value) if isinstance(value, str) else value


def schema(kind):
    from . import mexico_expense_templates as t
    return BUDGET_FIELDS if kind == "budget" else t.EXPENSE_FIELDS if kind == "expense" else t.FIELDS


def validate(mapping, kind):
    from . import mexico_expense_templates as t
    # Preserve legacy budget keys, which were accepted before budget bindings were applied.
    allowed = set(schema(kind)) | (set(t.FIELDS) if kind == "budget" else set())
    if not isinstance(mapping, dict) or set(mapping) - allowed - RESERVED:
        raise ValueError("Invalid template field mapping")

    def ids(value):
        if (not isinstance(value, list) or not 1 <= len(value) <= 20
                or any(not isinstance(v, str) or not re.fullmatch(ID_PATTERN, v) for v in value)
                or len(value) != len(set(value))):
            raise ValueError("Invalid template field mapping")

    for key, value in mapping.items():
        if key not in RESERVED:
            ids(value)
    tables = mapping.get("table_fields", {})
    roles = TABLE_ROLES.get(kind, {})
    if not isinstance(tables, dict) or set(tables) - set(roles):
        raise ValueError("Invalid template field mapping")
    for table, fields in tables.items():
        if not isinstance(fields, dict) or set(fields) - set(roles[table]):
            raise ValueError("Invalid template field mapping")
        seen = set()
        for values in fields.values():
            ids(values)
            if seen.intersection(values):
                raise ValueError("Invalid template field mapping")
            seen.update(values)
    currencies = mapping.get("table_currency", {})
    if (not isinstance(currencies, dict)
            or set(currencies) - ({"allocation_table"} if kind == "operating" else set())
            or any(value not in ("CNY", "header") for value in currencies.values())):
        raise ValueError("Invalid template field mapping")
    if len(json.dumps(mapping)) > 10000:
        raise ValueError("Invalid template field mapping")
    return mapping


def child(template, table, role, cells, ids=(), labels=()):
    from .mexico_expense_templates import resolve_table_cell
    explicit = decode(template).get("table_fields", {}).get(table, {}).get(role)
    result = resolve_table_cell(cells, explicit if explicit is not None else ids,
        () if explicit is not None else labels)
    if explicit is not None and result:
        kind = result.get("componentType") or re.split("[-_]", result["key"], maxsplit=1)[0]
        if kind not in CHILD_TYPES.get(role, ()):
            return {}
    return result


def child_configured(template, table, role):
    return role in decode(template).get("table_fields", {}).get(table, {})


def missing_children(template, table, cells):
    """An explicit binding must identify exactly one cell, including optional notes."""
    return [role for role in decode(template).get("table_fields", {}).get(table, {})
        if not child(template, table, role, cells)]


def allocation_currency(template, header):
    return header if decode(template).get("table_currency", {}).get("allocation_table") == "header" else "CNY"
