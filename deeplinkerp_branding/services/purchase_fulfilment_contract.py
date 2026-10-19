"""Pure native-item allocations and conservative, auditable logistics reports.

No order, payable, inventory or master-data creation lives in this contract.
Quantities use the native stock factor; currency totals are never combined.
"""
from __future__ import annotations

try:
    from frappe import _ as _i18n
except ImportError:
    def _i18n(message):
        return message


import hashlib
import json
import re
from decimal import Decimal, DecimalException, InvalidOperation, Overflow, Underflow, localcontext

FLOW_KINDS = {"external_internal", "internal_local", "trade_custody"}
SOURCE_CONTEXT_FIELDS = ("policy_version", "batch", "cost_version", "root_kind", "root_source_id", "corp_id",
    "instance_id", "binding_id", "binding_revision", "source_snapshot", "available", "approved", "invalid", "fingerprint")
STALE_SOURCE_WARNING = "物流来源身份、资格或版本已变化，请重新核对关联"


def validate_snapshot_context(snapshot, current, bound=None):
    """One source rule for fresh parsing, cached projections and re-association.

None means evidence is being parsed before a binding is supplied. An explicit
empty binding is not proof. A new binding alone never revalidates old evidence.
No stored timeline, manual node or audit entry is rewritten by this projection.
"""
    result = {**(snapshot or {}), "state": (snapshot or {}).get("state", "unknown"),
              "quantities": (snapshot or {}).get("quantities", []),
              "warnings": list((snapshot or {}).get("warnings", []))}
    stale = (bound is not None and any(current.get(key) != bound.get(key) for key in SOURCE_CONTEXT_FIELDS))
    evidence_context = (snapshot or {}).get("source_context", {})
    stale = stale or bool(snapshot and any(current.get(key) != evidence_context.get(key) for key in SOURCE_CONTEXT_FIELDS))
    if stale:
        result.update(state="stale", quantities=[])
        if STALE_SOURCE_WARNING not in result["warnings"]:
            result["warnings"].append(STALE_SOURCE_WARNING)
    elif result["state"] == "reported" and (not current.get("available") or not current.get("approved")
                                            or current.get("invalid") or current.get("root_kind") != "logistics"):
        result.update(state="unavailable", quantities=[])
        result["warnings"].append("当前来源不是已批准且有效的国际物流审批，请核对来源")
    return result


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), default=str).encode()).hexdigest()


def number(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(_i18n("数量必须为有效数字")) from None
    if not result.is_finite() or result <= 0:
        raise ValueError(_i18n("数量及原生换算系数必须为有限正数"))
    return result


def decimal_text(value):
    try:
        with localcontext() as context:
            context.traps[Overflow] = True
            context.traps[Underflow] = True
            normalized = value.normalize()
            if not normalized.is_finite() or (value != 0 and normalized == 0):
                raise ValueError(_i18n("数值无法在当前计算精度保持有限值"))
            return format(normalized, "f")
    except DecimalException:
        raise ValueError(_i18n("数值超出当前计算精度，请核对数量或金额")) from None


def _allocation_text(value):
    """Positive Data140 values only; inspect length before fixed-point expansion."""
    value = value.normalize()
    if not value.is_finite() or value <= 0:
        raise ValueError(_i18n("关联数量及换算结果必须保持有限正数"))
    _, digits, exponent = value.as_tuple()
    length = max(len(digits) + exponent, 1) + (1 - exponent if exponent < 0 else 0)
    if length > 140:  # The three allocation numeric fields are native Data/varchar(140).
        raise ValueError(_i18n("关联数量或换算结果超出原生字段存储长度，请核对数量及单位"))
    return format(value, "f")


def native_item(order, name):
    matches = [item for item in order.get("items", []) if item.get("name") == name]
    if len(matches) != 1:
        raise ValueError(_i18n("请明确选择属于原生订单的唯一明细"))
    return matches[0]


def validate_allocation(external, internal, proposed, existing):
    """One O(items + related allocations) validation while native parents are locked."""
    try:
        with localcontext() as context:
            # Preserve the caller's precision/range and ordinary normalization,
            # but a positive quantity may never silently overflow/underflow.
            context.traps[Overflow] = True
            context.traps[Underflow] = True
            return _validate_allocation(external, internal, proposed, existing)
    except DecimalException:
        raise ValueError(_i18n("关联数量或换算结果超出当前计算精度，请核对原生数量及单位")) from None


def _validate_allocation(external, internal, proposed, existing):
    result = dict(proposed)
    flow = result.get("flow_kind")
    if flow not in FLOW_KINDS or external["name"] != result.get("external_order"):
        raise ValueError(_i18n("采购关联类型或原生订单不符"))
    if external["company"] != result.get("purchasing_company") or not result.get("beneficiary_company"):
        raise ValueError(_i18n("请明确采购公司及最终使用/销售公司"))
    item = native_item(external, result.get("external_item"))
    qty = number(result.get("allocated_qty"))
    item_qty = number(item["qty"])
    if qty > item_qty:
        raise ValueError(_i18n("累计关联数量超出外部采购明细数量"))
    factor = number(item.get("conversion_factor"))
    stock_qty = qty * factor
    stock_uom = item.get("stock_uom")
    if not item.get("uom") or not stock_uom:
        raise ValueError(_i18n("原生明细缺少单位或库存单位，请先核对"))
    external_allocated = Decimal(0)
    internal_allocated = Decimal(0)
    for row in existing:
        if not row.get("active") or (result.get("name") and row.get("name") == result["name"]):
            continue
        if (row.get("external_order"), row.get("external_item")) == (external["name"], item["name"]):
            external_allocated += number(row.get("allocated_qty"))
        if internal and (row.get("internal_order"), row.get("internal_item")) == (
                internal["name"], result.get("internal_item")):
            internal_allocated += number(row.get("allocated_stock_qty"))
    if qty + external_allocated > item_qty:
        raise ValueError(_i18n("累计关联数量超出外部采购明细数量"))
    if flow == "trade_custody":
        if internal or result.get("internal_order") or result.get("internal_item"):
            raise ValueError(_i18n("贸易保管关联不形成内部应付"))
        if (result["beneficiary_company"] != external["company"] or
                not result.get("custody_company") or not result.get("custody_warehouse")):
            raise ValueError(_i18n("贸易所有公司与保管公司/仓库需明确核对"))
    else:
        if not internal or internal["name"] != result.get("internal_order"):
            raise ValueError(_i18n("请选择原生内部采购订单"))
        if internal["company"] != result["beneficiary_company"]:
            raise ValueError(_i18n("内部采购订单公司必须为最终受益公司"))
        target = native_item(internal, result.get("internal_item"))
        if item.get("item_code") != target.get("item_code") or stock_uom != target.get("stock_uom"):
            raise ValueError(_i18n("原生物料或库存单位不一致，不能猜测跨明细关联"))
        if stock_qty + internal_allocated > number(target["qty"]) * number(target.get("conversion_factor")):
            raise ValueError(_i18n("累计关联数量超出内部采购明细数量"))
        if flow == "internal_local" and (internal["name"] != external["name"] or
                target["name"] != item["name"] or external["company"] != result["beneficiary_company"]):
            raise ValueError(_i18n("本地内部采购以当前工厂原生订单及明细为准"))
        if flow == "external_internal" and external["company"] == internal["company"]:
            raise ValueError(_i18n("跨公司采购与内部采购公司需分别明确"))
    result.update(allocated_qty=_allocation_text(qty), allocated_stock_qty=_allocation_text(stock_qty),
                  allocated_uom=item["uom"], stock_uom=stock_uom, conversion_factor=_allocation_text(factor))
    return result


def internal_coverage(links, orders):
    """Index all active links once, including sources outside the requested page.

A whole internal balance is attributable only to one external source and full
native item coverage. Partial/shared balances deliberately have no proration.
"""
    indexed = {}
    for row in links:
        if not row.get("active") or row.get("flow_kind") == "trade_custody" or not row.get("internal_order"):
            continue
        entry = indexed.setdefault(row["internal_order"], {"sources": set(), "items": {}, "valid": True})
        entry["sources"].add(row.get("external_order"))
        try:
            qty = number(row.get("allocated_stock_qty"))
        except ValueError:
            entry["valid"] = False
            continue
        key = row.get("internal_item")
        entry["items"][key] = entry["items"].get(key, Decimal(0)) + qty
    result = {}
    for name, order in orders.items():
        entry = indexed.get(name, {"sources": set(), "items": {}, "valid": False})
        native = {item["name"]: number(item["qty"]) * number(item["conversion_factor"])
                  for item in order.get("items", [])}
        exact = bool(entry["valid"] and len(entry["sources"]) == 1 and native and native == entry["items"])
        result[name] = {"exact": exact, "shared": len(entry["sources"]) > 1,
                        "source": next(iter(entry["sources"])) if len(entry["sources"]) == 1 else None}
    return result


def price_version(order):
    fields = ("name", "item_code", "qty", "uom", "stock_uom", "conversion_factor", "rate", "amount")
    numeric = {"qty", "conversion_factor", "rate", "amount"}
    # Native modified remains a CAS/audit version. Receipt/payment/remarks
    # updates do not change the confirmed prices, quantities, units or currency.
    return digest({"name": order["name"], "currency": order.get("currency"),
                   "grand_total": numeric_text(order.get("grand_total")),
                   "items": [{field: numeric_text(item.get(field)) if field in numeric else item.get(field)
                              for field in fields} for item in order.get("items", [])]})


def numeric_text(value):
    return decimal_text(Decimal(str(value or 0)))


def item_version(order, item):
    return digest({"company": order["company"], "supplier": order["supplier"], "name": item.get("name"),
        "item_code": item.get("item_code"), "qty": numeric_text(item.get("qty")), "uom": item.get("uom"),
        "stock_uom": item.get("stock_uom"), "conversion_factor": numeric_text(item.get("conversion_factor"))})


ARRIVAL = re.compile(r"已到(?:达|货)?|到达|已收货|arrived(?:\s+at)?|received\s+at|lleg[oó](?:\s+a)?", re.I)
# Treat normal English n't contractions as one conservative lexical family,
# regardless of auxiliary verb, capitalization or straight/curly apostrophe.
ENGLISH_NEGATION = r"\b(?:not|[a-z]+n['’‘]t)\b"
# The Spanish falt- stem includes common missing/shortage conjugations, not just
# present-tense falta/faltan. These are review signals, never ERP stock facts.
SHORTAGE = r"少了|缺件|缺少|数量不足|\bfalt[a-záéíóúüñ]*\b"
CAUTION = re.compile(ENGLISH_NEGATION + "|" + SHORTAGE + "|" +
    r"未到|没到|尚未|未收|没收|不到|不曾|未曾|从未|没有|并未|预计|计划|将|等待|部分|可能|大概|破损|损坏|短缺|缺货|异常|问题|[?？]|\b(?:never|no|without|expected|will|partial|damaged?|broken|missing|shortage|defect(?:ive)?|issues?|failed|lost|nunca|jam[aá]s|sin|da[ñn]ad[oa]s?|rot[oa]s?|problemas?)\b|previst|pendiente", re.I)
# Capture the whole quantity expression, including its sign and separators.
# Ignoring an invalid negative/thousands token would falsely promote a later
# positive token in the same comment to a confirmed-looking arrival report.
QUANTITY = re.compile(r"(?<![\w.,+\-])((?:[+\-−]\s*)?[0-9][0-9.,]*(?:\s+[0-9][0-9.,]*)*)")
QUANTITY_UNIT = re.compile(r"\s*(Nos|pcs|pieces|Kg|箱|件|个|套|吨|公斤|box(?:es)?|piezas)(?![A-Za-z])", re.I)


def _quantities(text):
    # A number/sign is mandatory at each scan start. Each maximal numerical
    # expression succeeds independently of its unit, so a missing unit cannot
    # restart/backtrack through every suffix of a grouped number. Units are
    # matched only at that expression's end: O(text), including whitespace.
    for match in QUANTITY.finditer(text):
        unit = QUANTITY_UNIT.match(text, match.end())
        if unit:
            yield match[1], unit[1], match.start()


def _comment(row, identity=None):
    text = str(row.get("remark") or "")
    arrival = ARRIVAL.search(text)
    caution = CAUTION.search(text)
    quantities = list(_quantities(text))
    fragment = text[arrival.end():].split("，", 1)[0].split(",", 1)[0].split("。", 1)[0] if arrival else ""
    destination = fragment.strip(" :：.;；")
    # A quantity following the arrival verb is not a destination.
    if arrival and any(arrival.end() <= row[2] < arrival.end() + len(fragment) for row in quantities):
        destination = ""
    quantity = None
    if len(quantities) == 1:
        token = quantities[0][0].strip()
        # Commas and exactly-three-digit dot groups are locale-ambiguous.
        # Never silently read 1.000 as one or skip the sign of -10.
        if not re.search(r"[,\s+−]|\.[0-9]{3}(?:\.|$)", token):
            try:
                quantity = number(token)
            except ValueError:
                pass
    reported = bool(arrival and not caution and quantity is not None and destination)
    result = {"id": identity if identity is not None else digest(row), "source_id": row.get("source_id") or digest([
        row.get("user_id"), row.get("operation_time"), text]), "raw": dict(row),
        "state": "reported" if reported else "review" if arrival or caution else "unclassified",
        "confirmed": False}
    if reported:
        try:
            result["quantity"] = {"qty": decimal_text(quantity),
                                  "uom": quantities[0][1], "destination": destination}
        except ValueError:
            result["state"] = "review"  # Raw evidence survives an unrepresentable quantity.
    return result


def logistics_snapshot(current, detail, bound):
    """O(C + J) text/JSON work, with hash-based dedup rather than row comparison.

Raw authors, times and identities stay attached. Every positive is only a
report; mixed units remain separate, and manual nodes live outside this cache.
"""
    seen = set()
    timeline = []
    for row in (detail.get("main_approval") or {}).get("timeline") or []:
        if not isinstance(row, dict):
            continue
        key = digest(row)
        if key not in seen:
            seen.add(key)
            timeline.append(_comment(row, key))
    eligible = bool(detail.get("ok") and current.get("available") and current.get("approved")
                    and not current.get("invalid") and current.get("root_kind") == "logistics")
    states = {row["state"] for row in timeline}
    claims = {}
    destinations = set()
    for row in timeline:
        if row.get("quantity"):
            claim = row["quantity"]
            claims.setdefault(claim["uom"].lower(), set()).add((claim["qty"], claim["destination"]))
            destinations.add(claim["destination"])
    if len(destinations) > 1 or any(len(values) > 1 for values in claims.values()):
        states.add("review")
    state = "unavailable" if not eligible else (
        "review" if "review" in states else "reported" if "reported" in states else "unknown")
    quantities = [row["quantity"] for row in timeline if row.get("quantity")] if state == "reported" else []
    warnings = []
    if not eligible:
        warnings.append("当前来源不是已批准且有效的国际物流审批，请核对来源")
    if state == "review":
        warnings.append("评论存在预计、否定、部分或数量/目的地含糊，需人工核对")
    snapshot = {"version": 1, "source_context": dict(current), "source_updated_at": detail.get("source_updated_at"),
            "timeline": timeline, "state": state, "quantities": quantities, "warnings": warnings,
            "confirmed": False, "erp_received": False}
    return validate_snapshot_context(snapshot, current, bound)
