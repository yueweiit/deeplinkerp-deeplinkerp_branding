"""Purchase applications are source evidence, never orders or actual payments."""
from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import copy
import json
import re
from decimal import Decimal
from zoneinfo import ZoneInfo

from . import operating_oa_source as oa
from .operating_expense_contract import digest

PROCESS_CODES = ("PROC-BFDF6F09-4551-43B3-8C55-537AA74A241B", "PROC-6E11B527-2F82-439C-817D-C868DE086C97",
                 "PROC-E69FCD3E-E374-4C54-9D8F-6E1F55AD741F", "PROC-525D584D-ED10-4B8B-9C7A-9D67257BF6EE",
                 "PROC-98934E07-96ED-491C-8650-DFE8C3B707BF", "PROC-CA023EF7-CE63-4A0B-B4BB-66482C0E9972")
EXECUTION_REGIONS = frozenset({"中国", "中国china", "中国 china", "china", "墨西哥", "墨西哥mexico",
                              "墨西哥méxico", "墨西哥 mexico", "墨西哥 méxico", "mexico", "méxico"})
ALIASES = {
    "region": ("执行地区",), "currency": ("币种",), "payee": ("收款人",),
    "requested_amount": ("金额importe", "金额"), "items": ("需求明细",),
    "processors": ("加工商明细",), "detail_total": ("明细汇总金额",),
    "description": ("规格明细需求说明", "其他采购说明"),
    "schedule_date": ("交付日期",), "department": ("申请部门/组织", "申请部门", "所属BU"),
    "purchasing_company": ("采购公司", "采购主体", "付款公司", "Purchasing company"),
    "supplier": ("供应商", "Supplier"),
    "beneficiary_company": ("归属子公司", "所属子公司", "子公司", "受益公司", "最终用户公司", "业务主体",
                            "Empresa beneficiaria", "Empresa filial", "Subsidiaria", "Entidad comercial"),
    "project": ("项目归属", "归属项目", "项目Proyecto", "项目", "Proyecto", "Project"), "order_no": ("订单Pedido",),
    "payments": ("付款信息",), "payment_date": ("付款日期",),
    "payment_terms": ("付款条件",), "attachments": ("关键凭证",),
}
_FIELD_ALIASES = tuple((key, tuple(re.sub(r"\s+", "", alias).casefold() for alias in aliases))
                       for key, aliases in ALIASES.items())
ITEM_ALIASES = {
    "item_code": ("item_code", "material_code", "物品编码", "物料编码", "编码"),
    "item_name": ("item_name", "product_name", "物品名称", "物料名称", "名称"),
    "qty": ("qty", "quantity", "数量", "cantidad"), "uom": ("uom", "unit", "单位", "unidad"),
    "amount": ("amount", "goods_value", "金额", "总金额", "importe", "monto"),
    "rate": ("rate", "unit_price", "单价", "采购单价"),
    "currency": ("currency", "purchase_currency", "采购币种", "币种"),
    "supplier": ("supplier", "供应商"),
    "specification": ("specification", "spec_model", "规格", "description", "说明"),
}


def attachment_path(value):
    if not isinstance(value,str) or not re.fullmatch(r"/api/integrations/erp/purchase-expenses/(attachments|payment-vouchers)/[1-9][0-9]{0,18}",value):
        raise ValueError(_("采购附件路径无效"))
    return value


def _field_occurrences(row):
    components = row.get("form_component_values") or []
    if isinstance(components, str):
        components = json.loads(components, parse_float=Decimal)
    result = {}
    for component in components if isinstance(components, list) else []:
        if not isinstance(component, dict):
            continue
        name = re.sub(r"\s+", "", str(component.get("name") or component.get("label") or "")).casefold()
        for key, aliases in _FIELD_ALIASES:
            if any(name.startswith(alias) for alias in aliases):
                if key in ("purchasing_company","supplier") and not _populated([component.get("value")]):
                    break  # newly recognized blank controls are not new source facts
                result.setdefault(key, []).append(component.get("value"))
                break
    return result


def _populated(values):
    # Conditional form branches can repeat a label with an inactive null control.
    # Zero, false and malformed structures are not blank evidence.
    return [value for value in values if value is not None and not (isinstance(value, str) and not value.strip())]


def fields(row, *, occurrences=None):
    result = {}
    for key, values in (_field_occurrences(row) if occurrences is None else occurrences).items():
        if len(values) == 1:
            result[key] = values[0]
        elif key == "region":
            # Do not relax the execution-region scope, even for equal values.
            result[key] = None
        elif key == "currency":
            result[key] = values
        else:
            populated = _populated(values)
            # Multiple populated amounts may be installments; never sum or pick.
            result[key] = populated[0] if len(populated) == 1 else None
    return result


def _currency(row, *, occurrences=None):
    values = _populated((_field_occurrences(row) if occurrences is None else occurrences).get("currency", []))
    currencies = [oa.CURRENCIES.get(oa.normalized(item)) for item in values]
    return currencies[0] if currencies and all(item and item == currencies[0] for item in currencies) else None


def in_scope(row, *, occurrences=None):
    try:
        return (row.get("process_code") in PROCESS_CODES and oa.START <= oa.timestamp(row.get("create_time")) < oa.END
                and oa.normalized(fields(row, occurrences=occurrences).get("region")) in EXECUTION_REGIONS)
    except (ValueError, TypeError):
        return False


def _value(row, aliases):
    matches = [v for k, v in row.items() if any(
        str(k).casefold()==a.casefold() if a.isascii() else str(k).casefold().startswith(a.casefold())
        for a in aliases)]
    return matches[0] if len(matches) == 1 else None


def item_rows(value, parser=None):
    # Runtime supplies the existing OA table parser; pure normalized fixtures need no Frappe.
    if parser:
        values = parser(value)
    else:
        values = json.loads(value, parse_float=Decimal) if isinstance(value, str) and value else value
    rows = []
    for raw in values if isinstance(values, list) else []:
        if not isinstance(raw, dict):
            continue
        item = {k: _value(raw, aliases) for k, aliases in ITEM_ALIASES.items()}
        raw_rate = item.get("rate")
        for key in ("qty", "amount", "rate"):
            item[key] = oa.exact_amount(item[key])
        if raw_rate is not None and str(raw_rate).strip() and item["rate"] is None:
            item["rate_invalid"] = True
        # Do not change old bound fingerprints merely by introducing empty facts.
        for key in ("rate", "currency", "supplier"):
            if not _populated([item[key]]):
                item.pop(key)
        rows.append(item)
    return rows


def _role_value(occurrences, key):
    values = _populated(occurrences.get(key, []))
    if not values:
        return None, "missing"
    if len(values) != 1 or not isinstance(values[0], str):
        return None, "ambiguous"
    return values[0].strip(), "unique"


def normalize(row, parser=None, *, detail_rows=None, occurrences=None):
    occurrences = _field_occurrences(row) if occurrences is None else occurrences
    form = fields(row, occurrences=occurrences)
    beneficiary, beneficiary_status = _role_value(occurrences, "beneficiary_company")
    project, project_status = _role_value(occurrences, "project")
    buyer, buyer_status = _role_value(occurrences, "purchasing_company")
    supplier, supplier_status = _role_value(occurrences, "supplier")
    items = item_rows(form.get("items") if detail_rows is None else detail_rows, parser)
    total = (str(sum((Decimal(item["amount"]) for item in items), Decimal(0)))
             if items and all(item["amount"] is not None for item in items) else oa.exact_amount(form.get("detail_total")))
    requested = oa.exact_amount(form.get("requested_amount"))
    issues = []
    if total is not None and requested is not None and Decimal(total) != Decimal(requested):
        issues.append("采购明细与申请金额不一致，请核对原单")
    if not items:
        issues.append("采购明细待完善")
    identity = {k: row.get(k) for k in ("corp_id", "process_instance_id")}
    if not all(identity.values()):
        raise ValueError(_("采购来源身份缺失"))
    result = {"source_id": oa.application_id(row), "oa_identity": identity,
        "process_code": row.get("process_code"), "business_id": row.get("business_id"),
        "business_count": row.get("business_count", 1), "corp_id": row.get("corp_id"),
        "instance_count": row.get("instance_count", 1),
        "process_instance_id": row.get("process_instance_id"),
        "apply_date": oa.timestamp(row["create_time"]).astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat(),
        "originator_user_id": row.get("originator_user_id"), "originator_user_name": row.get("originator_user_name"),
        "eligible": bool(in_scope(row, occurrences=occurrences) and row.get("status") == "COMPLETED" and row.get("result") == "agree" and not row.get("deleted_at")),
        "status": row.get("status"), "result": row.get("result"), "deleted_at": str(row.get("deleted_at") or ""),
        "currency": _currency(row, occurrences=occurrences), "region": form.get("region"),
        "payee": form.get("payee"), "requested_amount": requested, "detail_total_amount": total,
        "items": items, "processors": form.get("processors"), "description": form.get("description"),
        "schedule_date": form.get("schedule_date"), "department": form.get("department"),
        "beneficiary_company": beneficiary, "beneficiary_company_status": beneficiary_status,
        "project": project, "project_status": project_status, "order_no": form.get("order_no"),
        "attachments": form.get("attachments"), "issues": issues,
        "updated_at": str(row.get("updated_at") or row["create_time"]),
        "original_fields": copy.deepcopy({key: values[0] if len(values) == 1 else values
                                          for key, values in occurrences.items()})}
    for key, value, status in (("purchasing_company", buyer, buyer_status), ("supplier", supplier, supplier_status)):
        if key in occurrences:
            result.update({key: value, key + "_status": status})
    # Approval evidence can change independently of product facts.
    result["version"] = digest(result)
    return result


def payment_evidence(item, cashier):
    candidates = [p for p in cashier if p.get("source_type") == "purchase" and oa.matches(item, p)
                  and (p.get("corp_id") or not p.get("process_instance_id") or item.get("instance_count",1)==1)]
    proof = candidates[0] if len(candidates) == 1 else None
    verified = bool(proof and proof.get("payment_evidence_status") == "recorded" and
                    not proof.get("source_conflict") and not proof.get("currency_conflict") and
                    item.get("currency") and proof.get("currency") == item["currency"])
    paid = oa.exact_amount(proof.get("paid_amount")) if verified else None
    issues = []
    if len(candidates) > 1:
        issues.append("出纳来源身份重复，付款待核对")
    if proof and not verified:
        issues.append("出纳付款证据或币种待核对")
    if paid is not None and Decimal(paid) < 0:
        paid = None; issues.append("出纳付款金额无效")
    result = {"source_type": "purchase", "oa_identity": item["oa_identity"],
        "cashier_source_id": proof.get("source_id") if proof else None, "currency": item.get("currency"),
        "paid_amount": paid, "payment_evidence_status": "recorded" if paid is not None else "unknown",
        "payments": copy.deepcopy(proof.get("payments") or []) if verified else [],
        "attachments": copy.deepcopy(proof.get("attachments") or []) if verified else [], "issues": issues}
    result["version"] = digest(result)
    return result


def validate_selection(source, currency, items, correction_reason=""):
    """Validate explicit human choices, without substituting missing quantity/prices."""
    if not isinstance(items, list) or not 1 <= len(items) <= 500:
        raise ValueError(_("请明确填写采购物料明细"))
    if not currency or (source.get("currency") != currency and not str(correction_reason or "").strip()):
        raise ValueError(_("币种与来源不符，请填写核对说明"))
    result = []
    for raw in items:
        if not isinstance(raw, dict) or not all(str(raw.get(k) or "").strip() for k in ("item_code", "uom")):
            raise ValueError(_("请填写物料编码和单位"))
        qty, rate = oa.exact_amount(raw.get("qty")), oa.exact_amount(raw.get("rate"))
        if qty is None or Decimal(qty) <= 0 or rate is None or Decimal(rate) < 0:
            raise ValueError(_("数量必须大于零，单价必须明确且不小于零"))
        result.append({"item_code":str(raw["item_code"]).strip(), "uom":str(raw["uom"]).strip(), "qty":qty, "rate":rate})
    original = source.get("items") or []
    unchanged = len(original) == len(result) and all(
        old.get("item_code") == new["item_code"] and old.get("uom") == new["uom"] and old.get("qty") is not None
        and Decimal(old["qty"]) == Decimal(new["qty"]) and old.get("amount") is not None
        and Decimal(old["amount"]) == Decimal(new["qty"]) * Decimal(new["rate"])
        for old, new in zip(original, result))
    if (not unchanged or source.get("issues")) and not str(correction_reason or "").strip():
        raise ValueError(_("采购明细待完善或与来源不符，请填写核对说明"))
    return result
