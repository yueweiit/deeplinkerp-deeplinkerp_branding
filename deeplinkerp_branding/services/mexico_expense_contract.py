"""Pure projection of Gastos approvals. No accounting or payment inference."""
from __future__ import annotations

import calendar
import json
import re
import unicodedata
from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from .operating_expense_contract import digest, money

CORP_ID = "ding144583309b2fb01c35c2f4657eb6378f"
PROCESS_CODE = "PROC-B5DDA7C9-FE83-4806-824B-4D56F1480C3E"
# Approval forms are a separate dimension from the accounting expense categories.
# Legacy binding retained for callers; live template bindings come from Settings.
APPROVAL_FORMS = {
    "operating": ("Operating Expenses", ()),
    "expense": ("Expense Requests", ((CORP_ID, PROCESS_CODE),)),
    "purchase": ("Purchase Expenses", ()),
}
FINANCE_ACTIVITY_ID = "f1e6_b1c7"
PERIOD_FIELDS = ("finance_approved_at", "finance_approved_date", "period_month", "period_start", "period_end", "period_kind")
CATEGORIES = (
    ("personnel", "Remunerations and Personnel", "#547ee0"),
    ("tax", "Taxes and Contributions", "#69b8ae"),
    ("utilities", "Utilities and Operations", "#93a8cf"),
    ("logistics", "Transport and Logistics", "#e2b67d"),
    ("services", "Professional Services", "#b6a4d8"),
    ("administration", "Administrative Expenses", "#88a4be"),
    ("selling", "Selling Expenses", "#df9d96"),
    ("procurement", "Purchase Expenses", "#c5a26b"),
    ("source_other", "Other Source Categories", "#8795a8"),
)
SUBCATEGORIES = {
    "welfare": ("personnel", "Employee Benefits", ("职工福利",)),
    "admin_other": ("administration", "Administrative Expenses", ()),
    "selling_other": ("selling", "Selling Expenses", ()),
    "purchase_mro": ("procurement", "MRO Purchases", ("MRO采购",)),
    "purchase_order": ("procurement", "Order Purchases", ("订单采购",)),
    "purchase_investment": ("procurement", "Investment Purchases", ("投资采购",)),
    "purchase_service": ("procurement", "Service Purchases", ("服务类采购",)),
    "purchase_project": ("procurement", "Project Purchases", ("项目开发采购",)),
    "salary": ("personnel", "Salary", ("Salario", "工资")),
    "social": ("personnel", "Social Contributions", ("Cotizaciones sociales", "Cotizaciones a la seguridad social", "社保", "社会保险缴费")),
    "fonacot": ("personnel", "Fonacot", ("Fonacot",)),
    "bonus_annual": ("personnel", "Annual Bonus", ("Aguinaldo", "年终奖")),
    "bonus": ("personnel", "Bonus", ("Bonificación", "奖金")),
    "tax_general": ("tax", "General Taxes", ("Tributos", "Impuestos y tasas", "税费")),
    "tax_federal": ("tax", "Federal Taxes", ("impuestos federales", "联邦税")),
    "electricity": ("utilities", "CFE (Electricity)", ("CFE", "联邦电力委员会")),
    "rent": ("utilities", "Rent", ("Rentas", "租金")),
    "water": ("utilities", "Water", ("Agua", "水费")),
    "ocean": ("logistics", "Ocean Freight", ("Marítimo", "海运")),
    "air": ("logistics", "Air Freight", ("Aéreo", "空运")),
    "courier": ("logistics", "Courier", ("Mensajería", "快递")),
    "legal_accounting": ("services", "Accounting and Legal Services", ("Servicios contables y legales", "servicios contables o servicios de abogados", "会计及法律服务", "会计服务或律师服务")),
}
MAIN_ALIASES = {
    "personnel": ("Remuneraciones y Personal", "薪酬人事"),
    "tax": ("Impuestos y Contribuciones", "税费"),
    "utilities": ("Servicios Públicos y Operativos", "公用运营"),
    "logistics": ("Transporte y Logística", "运输物流"),
    "services": ("Servicios Profesionales", "专业服务"),
}
ISSUES = {
    "classification": "Source classification requires review",
    "amount_pending": "Zero amount requires review",
    "company": "Subsidiary mapping required", "period": "Awaiting MX Finance approval",
    "finance": "Finance approval evidence requires review",
    "period_stale": "Expense period recalculation pending",
    "allocation": "Allocation between subsidiaries required", "amount": "Amount requires review",
    "currency": "Currency requires review", "category": "Expense category requires review",
    "structure": "Source form requires review", "identity": "Source row identity requires review",
    "total": "Header and detail totals do not match", "stale": "Source changed; review again",
}
TABLES = {
    "TableField_1QM4FQVMBI0W0": ("DDSelectField_RB6RW1978NWG", "MoneyField_1F4PBZ6A5UOW", "TextareaField_70ISZLEAO8G0", None, "entity"),
    "TableField_ENJNMIBMUAO0": ("DepartmentField_1TRH6GR60C0W0", "MoneyField_144WAB3DHHQ80", "TextareaField_15C85IEOADEK0", "salary", "department"),
    "TableField_21SE4NJZE8W00": ("DepartmentField_HO1FEKN323C0", "MoneyField_3T53N6RQFEA0", "TextareaField_1QVUKPQ0W4W00", "social", "department"),
}
SUB_FIELDS = ("DDSelectField_E8MD2JTMHWO", "DDSelectField_3QB6VJM7BS80", "DDSelectField_J6QIEDKOACG0", "DDSelectField_1GVCIZGB2H9C0", "DDSelectField_14SEL1HHI5Q80")


def normalized(value):
    return re.sub(r"[\s/()（）·_-]+", "", unicodedata.normalize("NFKC", str(value or "")).casefold())


def _match(value, choices):
    token = normalized(value)
    matches = {key for key, aliases in choices.items() if any(token.startswith(normalized(alias)) for alias in aliases)} if token else set()
    return next(iter(matches)) if len(matches) == 1 else None


def _json(value):
    return json.loads(value, parse_float=Decimal) if isinstance(value, str) else value


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def organization_id(field):
    """Read one explicit OA organization ID; names and paths are not IDs."""
    try:
        value = _json(field.get("extValue"))
    except (ValueError, TypeError):
        return ""
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    value = (value.get("code") or value.get("id")) if isinstance(value, dict) else None
    return str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ""


def _entity(value):
    """Preserve explicit entity/department labels; never infer an ERP company."""
    if isinstance(value, str):
        try:
            decoded = _json(value)
        except (ValueError, TypeError):
            return value.strip(), False
        if not isinstance(decoded, (list, dict)):
            return value.strip(), False
        value = decoded
    if isinstance(value, dict):
        label = value.get("name") or value.get("label") or value.get("deptName")
        return _text(label), False
    if isinstance(value, list):
        parts = [_entity(v)[0] for v in value]
        return " / ".join(p for p in parts if p), len(value) > 1
    return "", False


def ungrouped_number(value):
    # OA display exports may include grouped thousands; accept only the
    # unambiguous 1,234.56 form, not arbitrary commas or decimal-locale guesses.
    if isinstance(value, str) and re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", value.strip()):
        return value.strip().replace(",", "")
    return value


def exact_amount(value):
    try:
        amount = money(ungrouped_number(value), precision=2)
        # Frappe Currency is DECIMAL(21,9): at most twelve integer digits.
        return format(amount, ".2f") if 0 <= amount < Decimal("1e12") else None
    except ValueError:
        return None


def month_bounds(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}", value):
        raise ValueError("Invalid month")
    start = date.fromisoformat(value + "-01")
    if not 1900 <= start.year <= 2200:
        raise ValueError("Invalid reporting year")
    return start, start.replace(day=calendar.monthrange(start.year, start.month)[1])


def validate_period(month, start, end, kind):
    first, _ = month_bounds(month)
    start, end = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    if start > end or (end - start).days > 370 or kind not in {"month", "half_month", "week", "custom"}:
        raise ValueError("Invalid expense period")
    if not start <= first.replace(day=calendar.monthrange(first.year, first.month)[1]) or end < first:
        raise ValueError("Reporting month does not overlap expense period")
    return {"period_month": first.isoformat(), "period_start": start.isoformat(), "period_end": end.isoformat(), "period_kind": kind}


def entity_key(kind, label):
    return digest([kind, " ".join(str(label or "").split())])


def approval_status(status, result, deleted=False):
    if deleted:
        return "withdrawn"
    status, result = str(status or "").upper(), str(result or "").upper()
    if status == "RUNNING":
        return "pending"
    if status == "TERMINATED":
        return "terminated"
    if status == "COMPLETED":
        return {"AGREE": "approved", "REFUSE": "rejected"}.get(result, "unknown")
    return "unknown"


def finance_period(row, business_timezone="America/Mexico_City", activity_ids=None, node_names=None):
    """Use the verified finance activity, never application or overall completion time."""
    empty = dict.fromkeys(PERIOD_FIELDS)
    raw = row.get("raw_payload") or {}
    if not isinstance(raw, dict):
        return empty, ["finance"]
    status = approval_status(row.get("status"), row.get("result"), bool(row.get("deleted_at")))
    if status not in {"pending", "approved"}:
        return empty, []
    if any(raw.get(remote) and row.get(field) and str(raw[remote]).casefold() != str(row[field]).casefold()
           for remote, field in (("status", "status"), ("result", "result"))):
        return empty, ["finance"]
    tasks, operations = raw.get("tasks") or [], raw.get("operationRecords") or []
    if any(not isinstance(items, list) or len(items) > 5000 or any(not isinstance(item, dict) for item in items)
           for items in (tasks, operations)):
        return empty, ["finance"]
    allowed = set(activity_ids if activity_ids is not None else [FINANCE_ACTIVITY_ID])
    names = {normalized(name) for name in (node_names or [])}
    allowed.update(task.get("activityId") for task in tasks
                   if task.get("activityId") and normalized(task.get("taskGroupName")) in names)
    tasks = [task for task in tasks if task.get("activityId") in allowed]
    # Redirected tasks do not approve expenses; their replacement must complete.
    tasks = [task for task in tasks if not (task.get("status") == "CANCELED" and task.get("result") == "REDIRECTED")]
    if not tasks or any(task.get("status") in {"RUNNING", "WAITING"} for task in tasks):
        return empty, []
    if any(task.get("status") != "COMPLETED" or task.get("result") != "AGREE" for task in tasks):
        return empty, ["finance"]
    def timestamp(value):
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("Finance timestamp requires timezone")
        return stamp.astimezone(timezone.utc)
    try:
        ids = [task.get("taskId") for task in tasks]
        if any(not isinstance(key, str) or not key for key in ids) or len(set(ids)) != len(ids):
            raise ValueError("Invalid finance task identity")
        decisions = [operation for operation in operations if operation.get("activityId") in allowed
                     and operation.get("type") == "EXECUTE_TASK_NORMAL"]
        if any(operation.get("result") != "AGREE" for operation in decisions):
            raise ValueError("Finance task and operation disagree")
        approved_times, matched_operations = [], set()
        for task in tasks:
            finish = timestamp(task.get("finishTime"))
            created_value = task.get("createTime") or row.get("create_time")
            created = timestamp(created_value) if created_value else None
            if created and finish < created:
                raise ValueError("Finance completion precedes task creation")
            matches = []
            for index, operation in enumerate(decisions):
                if operation.get("activityId") != task.get("activityId"):
                    continue
                if operation.get("taskId") and operation["taskId"] != task["taskId"]:
                    continue
                if operation.get("userId") and task.get("userId") and operation["userId"] != task["userId"]:
                    continue
                approved_at = timestamp(operation.get("date"))
                if (created is None or created <= approved_at) and approved_at <= finish:
                    matches.append(approved_at)
                    matched_operations.add(index)
            # The action timestamp can precede task closure (observed by one minute).
            # Prefer the unambiguous approval action shown in the OA timeline.
            if decisions and len(set(matches)) != 1:
                raise ValueError("Finance task and operation disagree")
            approved_times.append(matches[0] if matches else finish)
        if len(matched_operations) != len(decisions):
            raise ValueError("Unmatched finance approval operation")
        approved = max(approved_times)
        local_date = approved.astimezone(ZoneInfo(business_timezone)).date()
        first, last = month_bounds(local_date.strftime("%Y-%m"))
    except (ValueError, TypeError, OverflowError):
        return empty, ["finance"]
    return {"finance_approved_at": approved.isoformat().replace("+00:00", "Z"),
            "finance_approved_date": local_date.isoformat(), "period_month": first.isoformat(),
            "period_start": first.isoformat(), "period_end": last.isoformat(), "period_kind": "month"}, []


def project(row, business_timezone="America/Mexico_City", template=None, *, include_finance=True):
    """Canonical archive row -> source + independent, stable expense lines."""
    corp, code = (template["corp_id"], template["process_code"]) if template else (CORP_ID, PROCESS_CODE)
    if row.get("corp_id") != corp or row.get("process_code") != code or not row.get("process_instance_id"):
        raise ValueError("Unexpected Mexico approval identity")
    raw = row.get("raw_payload") or {}
    if not isinstance(raw, dict):
        raise ValueError("Invalid raw approval")
    source_key = digest([corp, row["process_instance_id"]])
    components = _json(row.get("form_component_values") or raw.get("formComponentValues") or [])
    if not isinstance(components, list) or len(components) > 500:
        raise ValueError("Invalid form components")
    by_id, errors = {}, []
    for component in components:
        if not isinstance(component, dict):
            errors.append("structure")
            continue
        key = component.get("id")
        if key in by_id:
            errors.append("structure")
        by_id[key] = component
    approval_conflict = any(raw.get(remote) and row.get(field) and
        str(raw[remote]).casefold() != str(row[field]).casefold()
        for remote, field in (("status", "status"), ("result", "result")))
    if approval_conflict:
        errors.append("structure")
    overrides = json.loads(template.get("field_mapping") or "{}") if template else {}
    table_bindings = {key: key for key in TABLES}
    replaced_tables = set()
    if overrides:
        from . import mexico_expense_templates as bindings
        fields, mapping_issues = bindings.resolve(components, template, bindings.EXPENSE_FIELDS)
        errors.extend(mapping_issues)
        # Remove defaults first so an explicit binding is a replacement, not an
        # additional monetary table. Keep the actual component ID for line identity.
        for logical, component_ids in overrides.items():
            if logical.endswith("_table"):
                canonical = bindings.EXPENSE_IDS[logical]
                table_bindings.pop(canonical, None)
                replaced_tables.update([canonical, *component_ids])
        for logical in overrides.keys() & bindings.EXPENSE_IDS.keys():
            canonical = bindings.EXPENSE_IDS[logical]
            selected = fields.get(logical, {})
            by_id[canonical] = selected
            if logical.endswith("_table"):
                selected_id = selected.get("id")
                if not selected_id:
                    errors.append("structure")
                else:
                    if selected_id in table_bindings and table_bindings[selected_id] != canonical:
                        errors.append("structure")
                    table_bindings[selected_id] = canonical
    value = lambda key: by_id.get(key, {}).get("value")
    main = _match(value("DDSelectField_1ATVGGUYKEZK0"), MAIN_ALIASES)
    candidates = {_match(value(key), {k: v[2] for k, v in SUBCATEGORIES.items()}) for key in SUB_FIELDS if value(key)} - {None}
    sub = next(iter(candidates)) if len(candidates) == 1 else None
    if main and sub and SUBCATEGORIES[sub][0] != main:
        sub = None
        errors.append("category")
    currency = _match(value("DDSelectField_F7MDR4TJ1JS0"), {"MXN": ("Peso", "比索", "MXN"), "USD": ("Dólar", "美元", "USD"), "CNY": ("人民币", "RMB", "CNY")})
    stamp = datetime.fromisoformat(str(row["create_time"]).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Source timestamp requires timezone")
    created = stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    planned = _text(value("DDDateField_1URIJHJJMC680"))
    if planned:
        try:
            planned = date.fromisoformat(planned).isoformat()
        except ValueError:
            planned = None
    period, finance_issues = (finance_period(row, business_timezone, **(template.get("finance_options", {}) if template else {}))
        if include_finance else (dict.fromkeys(PERIOD_FIELDS), []))
    errors.extend(finance_issues)
    source = {"source_key": source_key, "corp_id": corp, "process_code": code,
        "process_instance_id": row["process_instance_id"], "approval_no": row.get("business_id"),
        "applicant": row.get("originator_user_name") or row.get("originator_user_id"),
        "approval_status": "unknown" if approval_conflict else approval_status(row.get("status"), row.get("result"), bool(row.get("deleted_at"))),
        "applied_at": created, "request_date": stamp.astimezone(ZoneInfo(business_timezone)).date().isoformat(),
        "planned_payment_date": planned or None, "currency": currency,
        "payee": _text(value("TextField_D8GN6OUQBLS0")), "payment_terms": _text(value("DDSelectField_1KMJWCE24KBK0")),
        "source_version": digest(json.loads(json.dumps(row, default=str))),
        "raw_json": json.dumps(row, ensure_ascii=False, default=str),
        "finance_approved_at": period["finance_approved_at"], "finance_approved_date": period["finance_approved_date"]}
    lines, seen = [], set()

    from . import mexico_expense_templates as templates
    service_fields, service_issues = templates.resolve(components, template or {}, schema={"entity": templates.FIELDS["entity"]})
    service = service_fields.get("entity", {}) if not service_issues else {}
    service_entity, service_multiple = _entity(service.get("value"))
    if service.get("componentType") == "CascadeField" and not service_multiple:
        service_entity = service_entity.split("/", 1)[0].strip()

    def add(table_id, row_number, entity, amount, description, forced_sub, kind, extra=(), owner_id="", owner_status="present"):
        from .expense_template_mapping import child_configured
        logical = next((key for key, identity in templates.EXPENSE_IDS.items()
            if identity == table_bindings.get(table_id)), None)
        blank_owner = owner_status in ("missing", "empty") and templates.empty_value(entity)
        explicit_owner = child_configured(template, logical, "entity")
        if owner_status in ("ambiguous", "invalid"):
            extra = (*extra, "structure", "allocation")
        if blank_owner and explicit_owner:
            extra = (*extra, "allocation")
        if blank_owner and not owner_id and (service_multiple or service_issues):
            extra = (*extra, "allocation" if service_multiple else "structure")
        if (blank_owner and not owner_id and service_entity and not service_multiple and not explicit_owner):
            entity, kind, owner_id = service_entity, "entity", organization_id(service)
        entity, multiple = _entity(entity)
        selected_sub = forced_sub or sub
        selected_category = SUBCATEGORIES[selected_sub][0] if selected_sub else main
        parsed = exact_amount(amount)
        problems = list(errors) + list(extra)
        if multiple:
            problems.append("allocation")
        if not row_number or (table_id, row_number) in seen:
            problems.append("identity")
            if row_number:
                errors.append("identity")
            # Preserve malformed rows independently, never auto-confirm them.
            row_number = "unresolved:" + str(len(lines))
        seen.add((table_id, row_number))
        if parsed is None:
            problems.append("amount")
        elif parsed == "0.00":
            problems.append("amount_pending")
        if not selected_sub or not selected_category:
            problems.append("category")
        if not currency:
            problems.append("currency")
        facts = {"source_entity": entity, "entity_kind": kind, "entity_key": entity_key(kind, entity),
            "category": selected_category, "subcategory": selected_sub, "amount_raw": str(amount if amount is not None else ""),
            "amount": parsed, "currency": currency, "description": _text(description),
            "table_id": table_id, "source_row_id": str(row_number)}
        lines.append({**facts, **period, "line_key": digest([source_key, table_id, row_number]),
            "row_fingerprint": digest(facts), "base_issues": sorted(set(problems)), "source": source_key,
            "_source_entity_id": owner_id})

    for component in components:
        if not isinstance(component, dict) or component.get("componentType") != "TableField" or not component.get("value"):
            continue
        key = component.get("id")
        canonical_key = table_bindings.get(key)
        if canonical_key is None:
            if key in replaced_tables:
                continue
            errors.append("structure")
            continue
        try:
            entries = _json(component["value"])
            if not isinstance(entries, list) or len(entries) > 2000:
                raise ValueError()
            entity_field, amount_field, text_field, fixed_sub, kind = TABLES[canonical_key]
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("rowValue"), list):
                    errors.append("structure")
                    continue
                cells, duplicates, owner_id = {}, False, ""
                for cell in entry["rowValue"]:
                    if not isinstance(cell, dict) or cell.get("key") in cells:
                        duplicates = True
                        continue
                    cells[cell.get("key")] = cell.get("value")
                    if cell.get("key") == entity_field:
                        owner_id = organization_id(cell)
                from . import expense_template_mapping as child_bindings
                from .mexico_expense_templates import EXPENSE_IDS
                logical = next(logical for logical, identity in EXPENSE_IDS.items() if identity == canonical_key)
                valid_cells = [cell for cell in entry["rowValue"] if isinstance(cell, dict)]
                if child_bindings.missing_children(template, logical, valid_cells):
                    errors.append("structure")
                owner, owner_status = child_bindings.child_with_status(template or {}, logical, "entity", valid_cells, (entity_field,))
                money = child_bindings.child(template or {}, logical, "amount", valid_cells, (amount_field,))
                note = child_bindings.child(template or {}, logical, "description", valid_cells, (text_field,))
                add(key, entry.get("rowNumber"), owner.get("value"), money.get("value"), note.get("value"), fixed_sub, kind,
                    ("structure",) if duplicates else (), organization_id(owner), owner_status)
        except (ValueError, TypeError):
            errors.append("structure")
    if "amount" in overrides:
        header_amount = value("MoneyField_1F4PBZ6A5UOW")
    else:
        header_amount = value("CalculateField_GZXGU5OSNWO0")
        if header_amount in (None, ""):
            header_amount = value("MoneyField_1F4PBZ6A5UOW")
    header_total = exact_amount(header_amount) if header_amount not in (None, "") else None
    if "amount" in overrides and header_total is None:
        errors.append("amount")
    if not lines:
        add("header", "header", value("DDMultiSelectField_RWULY2WAUCG0"), header_amount,
            value("TextareaField_70ISZLEAO8G0"), None, "entity", owner_id=organization_id(by_id.get("DDMultiSelectField_RWULY2WAUCG0", {})),
            owner_status="empty" if templates.empty_value(value("DDMultiSelectField_RWULY2WAUCG0")) else "present")
    if header_amount not in (None, "") and (header_total is None or any(l["amount"] is None for l in lines)
            or sum((Decimal(l["amount"]) for l in lines if l["amount"] is not None), Decimal(0)) != Decimal(header_total or 0)):
        errors.append("total")
    computed_total = exact_amount(format(sum((Decimal(l["amount"]) for l in lines if l["amount"] is not None), Decimal(0)), ".2f")) if all(l["amount"] is not None for l in lines) else None
    if computed_total is None and all(l["amount"] is not None for l in lines):
        errors.append("total")
    for line in lines:
        line["base_issues"] = sorted(set(line["base_issues"] + errors))
        line["row_fingerprint"] = digest([line["row_fingerprint"], line["base_issues"], header_total])
    source["total_amount"] = header_total if "amount" in overrides or header_total is not None else computed_total
    return source, lines


def apply_review(line, previous, mappings, mapping_version):
    """Never carry manual confirmation across changed economic source facts."""
    result = dict(line)
    problems = list(line["base_issues"])
    same = previous and previous.get("reviewed_fingerprint") == line["row_fingerprint"]
    mapped = mappings.get(line["entity_key"]) if "allocation" not in problems else None
    rule = mapped if isinstance(mapped, dict) else {"company": mapped}
    scope = rule.get("expense_scope") or "mx"
    mapped = rule.get("company") if scope == "mx" else None
    result.update(expense_scope=scope, scope_reason=rule.get("scope_reason") or "")
    result.update(company=mapped, company_basis="mapping" if mapped or scope == "non_mx" else "", mapping_version=mapping_version,
                  reviewed_fingerprint=None, reviewed_by=None, reviewed_at=None, review_note=None)
    if same:
        for field in ("reviewed_fingerprint", "reviewed_by", "reviewed_at", "review_note"):
            result[field] = previous.get(field)
        if previous.get("company_basis") == "manual":
            result.update(company=previous.get("company"), company_basis="manual",
                          expense_scope=previous.get("expense_scope") or "mx", scope_reason=previous.get("scope_reason") or "")
    elif previous and (previous.get("reviewed_fingerprint") or
                       "stale" in _json(previous.get("issues") or [])):
        problems.append("stale")
    # A changed manual decision must remain visible until reviewed again, even
    # if a standing source rule would otherwise exclude this row.
    if "stale" in problems:
        result.update(expense_scope="mx", scope_reason="")
    if result["expense_scope"] == "non_mx":
        result.update(company=None, issues=sorted(set(problems)), review_state="excluded")
        return result
    if not result["company"]:
        problems.append("company")
    if not result["period_month"] or not result["period_start"] or not result["period_end"]:
        problems.append("period")
    result["issues"] = sorted(set(problems))
    result["review_state"] = "confirmed" if not problems else "needs_review"
    return result
