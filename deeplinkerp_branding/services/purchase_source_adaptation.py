"""Pure, reviewable purchase plans; original OA evidence is never rewritten.

The runtime supplies the shared DingTalk row flattener for table envelopes.
This module selects confirmed facts and validates arithmetic; it does no writes.
"""

from __future__ import annotations

try:
	from frappe import _
except ImportError:
	def _(message):
		return message


import copy
import json
import re
from datetime import date
from decimal import Decimal, localcontext
from fractions import Fraction

from . import purchase_source_contract as contract
from .operating_expense_contract import digest

START_DATE = date(2026, 9, 25)
END_DATE = date(2027, 1, 1)
SERVICE_ITEM_CODE = "DLP-SVC-PROCESSING"
SERVICE_ITEM_NAME = "加工服务"
SCOPE_FIELDS = ("corp_id", "process_code", "execution_region", "source_organization", "purchase_type")
PROCESSOR_ALIASES = {
	"processor_name": ("processor_name", "processor", "加工商名称", "加工商名字", "加工商姓名", "加工商"),
	"processing_materials": (
		"processing_materials",
		"processing_material",
		"加工材料",
		"加工物料",
		"加工内容",
	),
	"odt": ("odt", "ODT工单", "ODT编号", "ODT号"),
	"sales_order_no": ("sales_order_no", "sales_order", "销售订单号码", "销售订单", "销售单号"),
	"qty": ("qty", "quantity", "加工数量", "数量", "cantidad"),
	"rate": ("rate", "unit_price", "加工单价", "单价", "precio"),
	"amount": ("amount", "加工金额", "金额", "总金额", "importe", "monto"),
	"uom": ("uom", "unit", "单位", "unidad"),
	"source_row_key": ("source_row_key", "row_key", "rowkey", "row_id", "rowid"),
}
_RECOMPUTED_ISSUES = frozenset({"采购明细待完善", "采购明细与申请金额不一致，请核对原单"})


def _text(value):
	return value.strip() if isinstance(value, str) and value.strip() else None


def _scope_text(value):
	# The organization is an explicit source fact, including its exact spelling.
	return value if isinstance(value, str) and value.strip() else ""


def _rows_value(value):
	if value is None or (isinstance(value, str) and not value.strip()):
		return []
	if isinstance(value, str):
		try:
			return json.loads(value, parse_float=Decimal)
		except (ValueError, TypeError):
			raise ValueError(_("加工商明细格式不支持，请核对原单结构")) from None
	return value


def _has_rows(value):
	try:
		value = _rows_value(value)
	except ValueError:
		return False
	return isinstance(value, list) and bool(value)


def scope(source):
	"""Return exact source facts, including the original department snapshot."""
	region = contract.oa.normalized(source.get("region"))
	canonical_region = (
		"中国"
		if region in {"中国", "中国china", "中国 china", "china"}
		else "墨西哥"
		if region in contract.EXECUTION_REGIONS
		else ""
	)
	goods, processing = _has_rows(source.get("items")), _has_rows(source.get("processors"))
	purchase_type = (
		"mixed" if goods and processing else "goods" if goods else "processing" if processing else "unknown"
	)
	department = source.get("department")
	organization = _scope_text(department)
	if not organization and (department is None or isinstance(department, str)):
		context = source.get("adaptation_context")
		context = context if isinstance(context, dict) else {}
		department_id = _scope_text(context.get("originator_dept_id"))
		department_name = _scope_text(context.get("originator_dept_name"))
		organization = (
			"dept:" + department_id
			if department_id
			else "dept-name:" + department_name
			if department_name
			else ""
		)
	return {
		"corp_id": _scope_text(source.get("corp_id")),
		"process_code": _scope_text(source.get("process_code")),
		"execution_region": canonical_region,
		"source_organization": organization,
		"purchase_type": purchase_type,
	}


def scope_key(value):
	"""Hash every populated scope component; no missing-component wildcards."""
	if not isinstance(value, dict) or any(not _scope_text(value.get(key)) for key in SCOPE_FIELDS):
		raise ValueError(_("适配规则范围缺失企业、模板、地区、申请组织或采购类型"))
	return digest({key: value[key] for key in SCOPE_FIELDS})


def _field(raw, aliases):
	matches = []
	for key, value in raw.items():
		name = re.sub(r"\s+", "", str(key)).casefold()
		if any(_label_matches(name, alias.casefold()) for alias in aliases):
			if value is not None and not (isinstance(value, str) and not value.strip()):
				matches.append(value)
	if len(matches) > 1:
		raise ValueError(_("加工商明细字段重复或不唯一，请核对原单"))
	return matches[0] if matches else None


def _label_matches(name, alias):
	if name == alias:
		return True
	# Chinese controls often append a Spanish translation. A different Chinese
	# suffix is another field: 加工商电话 must never be read as 加工商.
	return not alias.isascii() and name.startswith(alias) and not "\u3400" <= name[len(alias)] <= "\u9fff"


def processor_rows(value, *, flatten_row=None):
	"""Normalize flat rows; delegate rowValue/cell arrays to the shared parser.

	``flatten_row`` receives one dict envelope, wrapping raw cell arrays in
	``{"rowValue": cells}`` as required by the existing DingTalk flattener.
	Invalid explicit numeric values retain an ``*_invalid`` flag for planning.
	"""
	values = _rows_value(value)
	if not isinstance(values, list):
		raise ValueError(_("加工商明细格式不支持，请核对原单结构"))
	result = []
	for index, original in enumerate(values, 1):
		raw = copy.deepcopy(original)
		envelope_key = _field(raw, PROCESSOR_ALIASES["source_row_key"]) if isinstance(raw, dict) else None
		row_number = raw.get("_dingtalk_row_number", raw.get("rowNumber")) if isinstance(raw, dict) else None
		if isinstance(raw, list) or (isinstance(raw, dict) and "rowValue" in raw):
			if flatten_row is None:
				raise ValueError(_("加工商明细格式需要现有钉钉表格解析器"))
			raw = flatten_row({"rowValue": raw} if isinstance(raw, list) else raw)
		if not isinstance(raw, dict):
			raise ValueError(_("加工商明细格式不支持，请核对原单行"))
		row = {key: _field(raw, aliases) for key, aliases in PROCESSOR_ALIASES.items()}
		if envelope_key and row["source_row_key"] and envelope_key != row["source_row_key"]:
			raise ValueError(_("加工商明细来源行标识不一致，请核对原单"))
		row_number = raw.get("_dingtalk_row_number", raw.get("rowNumber", row_number))
		for key in ("qty", "rate", "amount"):
			original_value = row[key]
			row[key] = contract.oa.exact_amount(original_value)
			if raw.get(key + "_invalid") or (original_value is not None and row[key] is None):
				row[key + "_invalid"] = True
		for key in ("processor_name", "processing_materials", "odt", "sales_order_no"):
			row[key] = _text(row[key])
		if _text(row["uom"]):
			row["uom"] = _text(row["uom"])
		else:
			row.pop("uom")
		if row_number is not None and isinstance(row_number, (str, int)) and not isinstance(row_number, bool):
			row["_dingtalk_row_number"] = row_number
		row["source_row_key"] = (
			_text(envelope_key)
			or _text(row["source_row_key"])
			or f"processors:{row.get('_dingtalk_row_number', index)}"
		)
		result.append(row)
	return result


def _date(value):
	if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
		return None
	try:
		return date.fromisoformat(value)
	except ValueError:
		return None


def _exact_rate(amount, qty):
	ratio = Fraction(Decimal(amount)) / Fraction(Decimal(qty))
	denominator = ratio.denominator
	for factor in (2, 5):
		while denominator % factor == 0:
			denominator //= factor
	if denominator != 1:
		return None
	with localcontext() as context:
		context.prec = max(100, len(str(abs(ratio.numerator))) + len(str(ratio.denominator)) + 10)
		value = format(Decimal(ratio.numerator) / Decimal(ratio.denominator), "f")
	return value.rstrip("0").rstrip(".") if "." in value else value


def _numbers(raw, label, reasons):
	numbers = {key: contract.oa.exact_amount(raw.get(key)) for key in ("qty", "rate", "amount")}
	for key, caption in (("qty", "数量"), ("rate", "单价"), ("amount", "金额")):
		if raw.get(key + "_invalid") or (raw.get(key) is not None and numbers[key] is None):
			reasons.append(f"{label}原单{caption}无效，请核对")
	qty, rate, amount = (numbers[key] for key in ("qty", "rate", "amount"))
	if qty is None or Decimal(qty) <= 0:
		reasons.append(f"{label}数量必须明确且大于零")
		return numbers
	if (rate is not None and Decimal(rate) < 0) or (amount is not None and Decimal(amount) < 0):
		reasons.append(f"{label}单价和金额不得小于零")
		return numbers
	if amount is None and rate is not None and not raw.get("amount_invalid") and raw.get("amount") is None:
		with localcontext() as context:
			context.prec = len(Decimal(qty).as_tuple().digits) + len(Decimal(rate).as_tuple().digits) + 10
			amount = numbers["amount"] = str(Decimal(qty) * Decimal(rate))
	if rate is None and amount is not None and not raw.get("rate_invalid") and raw.get("rate") is None:
		rate = numbers["rate"] = _exact_rate(amount, qty)
		if rate is None:
			reasons.append(f"{label}单价无法由金额和数量精确表示，请核对")
	if amount is None:
		reasons.append(f"{label}金额缺失，且无法由明确数量和单价计算")
	if amount is not None and rate is not None:
		if Fraction(Decimal(qty)) * Fraction(Decimal(rate)) != Fraction(Decimal(amount)):
			reasons.append(f"{label}数量、单价与金额不一致，请核对原单")
	return numbers


def _role(source, key, caption, fallback, reasons):
	raw = source.get(key)
	status = source.get(key + "_status")
	if status == "ambiguous" or (raw is not None and not isinstance(raw, str)):
		reasons.append(caption + "缺失或不唯一，请核对原单")
		return None
	value = _text(raw)
	if value and fallback and value != fallback:
		reasons.append(caption + "与已确认适配规则冲突，请核对")
	if not value:
		value = fallback
	if not value:
		reasons.append(caption + "待确认，请明确原单角色或确认适配规则")
	return value


def _duplicate_billing(items, reasons):
	keys = set()
	signatures = set()
	for row in items:
		key = row["source_row_key"]
		if key in keys:
			reasons.append("采购来源行标识重复，请核对重复计费")
		keys.add(key)
		if not row["is_stock_item"]:
			signature = tuple(
				row.get(key) for key in ("processor_name", "processing_materials", "odt", "sales_order_no")
			)
			signature += tuple(
				Decimal(row[key]) if row.get(key) is not None else None for key in ("qty", "amount")
			)
			if signature in signatures:
				reasons.append("加工商明细存在重复行，请核对重复计费")
			signatures.add(signature)
	goods = [row for row in items if row["is_stock_item"]]
	processing = [row for row in items if not row["is_stock_item"]]
	for good in goods:
		materials = {
			contract.oa.normalized(good.get(key))
			for key in ("item_code", "item_name", "description")
			if _text(good.get(key))
		}
		for processor in processing:
			if contract.oa.normalized(processor.get("processing_materials")) in materials and all(
				good.get(key) is not None
				and processor.get(key) is not None
				and Decimal(good[key]) == Decimal(processor[key])
				for key in ("qty", "amount")
			):
				reasons.append("货物与加工商明细可能对同一材料重复计费，请核对原单")


def build_plan(source, rule=None, *, processors=None):
	"""Build a canonical plan with blockers, independently versioned from source.

	A rule is usable only when enabled and its ``scope_key`` exactly matches.
	Processing master data always requires that confirmed rule. Goods can use
	explicit source company/supplier without a rule; runtime checks master data.
	"""
	current_scope = scope(source)
	reasons = []
	if (
		not source.get("eligible")
		or source.get("status") != "COMPLETED"
		or source.get("result") != "agree"
		or source.get("deleted_at")
	):
		reasons.append("采购来源未审批通过、已拒绝或撤销，不能办理")
	apply_date = _date(source.get("apply_date"))
	if (
		not apply_date
		or not START_DATE <= apply_date < END_DATE
		or source.get("process_code") not in contract.PROCESS_CODES
		or not current_scope["execution_region"]
	):
		reasons.append("采购来源不在中国/墨西哥2026年且2026-09-25起的适配范围")
	if any(source.get(key, 1) != 1 for key in ("instance_count", "business_count")):
		reasons.append("采购来源身份或审批号不唯一，待核对")
	trusted_rule = {}
	if rule is not None:
		try:
			matching = isinstance(rule, dict) and rule.get("scope_key") == scope_key(current_scope)
		except ValueError:
			matching = False
			reasons.append("申请组织等适配范围缺失，不能匹配确认规则")
		if matching and rule.get("enabled") in (True, 1, "1"):
			trusted_rule = rule
		else:
			reasons.append("适配规则未启用或与来源精确范围不符")
	company = _role(source, "purchasing_company", "采购公司", _text(trusted_rule.get("company")), reasons)
	supplier = _role(source, "supplier", "供应商", _text(trusted_rule.get("supplier")), reasons)
	currency = contract.oa.CURRENCIES.get(contract.oa.normalized(source.get("currency")))
	if not currency:
		reasons.append("采购币种缺失或不唯一")
	schedule_date = source.get("schedule_date")
	date_adjustment = None
	if not _date(schedule_date):
		reasons.append("交付日期缺失或无效，请核对原单")
	elif apply_date and _date(schedule_date) < apply_date:
		date_adjustment = f"原需求日期 {schedule_date} 早于申请日期 {source['apply_date']}，草稿需求日期按申请日期 {source['apply_date']}"
		schedule_date = source["apply_date"]
	for issue in source.get("issues") or []:
		if isinstance(issue, str) and issue not in _RECOMPUTED_ISSUES:
			reasons.append(issue)
	raw_processing = source.get("processors") if processors is None else processors
	for key, caption in (("items", "需求明细"), ("processors", "加工商明细")):
		original = (source.get("original_fields") or {}).get(key)
		if not source.get(key) and isinstance(original, list) and len(contract._populated(original)) > 1:
			reasons.append(caption + "存在多个已填表格，不能自动选择")
	try:
		processing = processor_rows(raw_processing)
	except ValueError as error:
		processing = []
		reasons.append(str(error))
	goods = source.get("items") or []
	if not isinstance(goods, list) or any(not isinstance(row, dict) for row in goods):
		goods = []
		reasons.append("货物采购明细格式不支持，请核对原单")
	items = []
	for index, raw in enumerate(goods, 1):
		label = f"货物第{index}行"
		numbers = _numbers(raw, label, reasons)
		if not _text(raw.get("item_code")) or not _text(raw.get("uom")):
			reasons.append(label + "物料编码和单位待完善")
		if (
			raw.get("currency")
			and contract.oa.CURRENCIES.get(contract.oa.normalized(raw["currency"])) != currency
		):
			reasons.append(label + "币种与来源不符")
		if _text(raw.get("supplier")) and _text(raw["supplier"]) != supplier:
			reasons.append(label + "供应商与统一结算供应商不一致")
		items.append(
			{
				"item_code": _text(raw.get("item_code")),
				"item_name": _text(raw.get("item_name")),
				**numbers,
				"uom": _text(raw.get("uom")),
				"description": _text(raw.get("specification"))
				or _text(raw.get("description"))
				or _text(raw.get("item_name"))
				or "",
				"is_stock_item": 1,
				"item_group": _text(trusted_rule.get("item_group")),
				"source_row_key": _text(raw.get("source_row_key")) or f"items:{index}",
			}
		)
	if processing and (
		not trusted_rule
		or not _text(trusted_rule.get("item_group"))
		or not _text(trusted_rule.get("purchase_uom"))
	):
		reasons.append("加工服务的物料组和采购单位待确认，请确认精确范围的适配规则")
	for index, raw in enumerate(processing, 1):
		label = f"加工第{index}行"
		numbers = _numbers(raw, label, reasons)
		if not raw.get("processor_name") or not raw.get("processing_materials"):
			reasons.append(label + "加工商名称或加工材料待完善")
		uom = _text(trusted_rule.get("purchase_uom"))
		if raw.get("uom") and raw["uom"] != uom:
			reasons.append(label + "原单单位与确认规则不一致")
		refs = {
			key: raw.get(key) for key in ("processor_name", "processing_materials", "odt", "sales_order_no")
		}
		description = "\n".join(
			caption + "：" + refs[key]
			for key, caption in (
				("processor_name", "加工商"),
				("processing_materials", "加工材料"),
				("odt", "ODT"),
				("sales_order_no", "销售订单"),
			)
			if refs[key]
		)
		metadata = (
			{"_dingtalk_row_number": raw["_dingtalk_row_number"]} if "_dingtalk_row_number" in raw else {}
		)
		items.append(
			{
				"item_code": _text(trusted_rule.get("service_item_code")) or SERVICE_ITEM_CODE,
				"item_name": _text(trusted_rule.get("service_item_name")) or SERVICE_ITEM_NAME,
				**numbers,
				"uom": uom,
				"description": description,
				"is_stock_item": 0,
				"item_group": _text(trusted_rule.get("item_group")),
				"source_row_key": raw["source_row_key"],
				**refs,
				**metadata,
			}
		)
	if not items:
		reasons.append("没有可采购明细，请核对结构化货物或加工商明细")
	_duplicate_billing(items, reasons)
	with localcontext() as context:
		amounts = [Decimal(item["amount"]) for item in items if item["amount"] is not None]
		context.prec = (
			max(
				(len(value.as_tuple().digits) + abs(value.as_tuple().exponent) for value in amounts),
				default=1,
			)
			+ len(str(len(items)))
			+ 20
		)
		total = str(sum(amounts, Decimal(0))) if items and len(amounts) == len(items) else None
	requested = contract.oa.exact_amount(source.get("requested_amount"))
	if requested is None:
		reasons.append("原单申请金额缺失或无效，请核对")
	elif total is not None and Decimal(requested) != Decimal(total):
		reasons.append("采购明细与申请金额不一致，请核对原单")
	plan = {
		"scope": current_scope,
		"items": items,
		"company": company,
		"supplier": supplier,
		"currency": currency,
		"schedule_date": schedule_date,
		"total_amount": total,
		"blocking_reasons": list(dict.fromkeys(reasons)),
	}
	if date_adjustment:
		plan["date_adjustment"] = date_adjustment
		for item in items:
			item["description"] = "\n".join(
				value for value in (item["description"], date_adjustment) if value
			)
	plan["version"] = digest(plan)
	return plan
