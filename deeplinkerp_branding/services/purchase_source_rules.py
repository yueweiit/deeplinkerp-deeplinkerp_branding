"""Confirmed purchase-source mappings and native Item adoption in the caller's transaction."""

from __future__ import annotations

from frappe import _


import copy
import json
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal

import frappe

from .operating_expense_contract import digest

DOCTYPE = "Purchase Source Mapping Rule"
ADOPTION_FIELD = "custom_purchase_adaptation_json"
TEMP_SETTLEMENT_SUPPLIER = "墨西哥加工费待核对"
SCOPE_FIELDS = ("corp_id", "process_code", "execution_region", "source_organization", "purchase_type")
CONFIG_FIELDS = (
	"company",
	"supplier",
	"item_group",
	"purchase_uom",
	"service_item_code",
	"service_item_name",
)
_managed = ContextVar("purchase_source_rule_write", default=False)


def _service():
	from . import purchase_source_service

	return purchase_source_service


@contextmanager
def managed_rule_write():
	token = _managed.set(True)
	try:
		yield
	finally:
		_managed.reset(token)


def validate_rule_write(doc):
	if not _managed.get():
		frappe.throw(_("采购映射规则请通过来源预览确认入口保存"), frappe.PermissionError)


def read_item(code):
	if not frappe.db.exists("Item", code):
		return None
	return _service()._native("Item", code).as_dict()


def validate_existing_item(spec, item):
	code = spec["item_code"]
	if item.get("disabled") or not item.get("is_purchase_item"):
		raise ValueError(_("物料 {0} 未启用采购").format(code))
	if int(bool(item.get("is_stock_item"))) != int(bool(spec.get("is_stock_item"))):
		raise ValueError(_("物料 {0} 的库存属性与采购用途冲突").format(code))
	owner = item.get("custom_overseas_business_entity")
	if owner and owner != spec.get("company"):
		raise ValueError(_("物料 {0} 的业务归属与采购公司冲突").format(code))
	if item.get("stock_uom") != spec.get("uom"):
		conversions = [
			row.get("conversion_factor")
			for row in item.get("uoms") or []
			if row.get("uom") == spec.get("uom")
		]
		if len(conversions) != 1 or conversions[0] is None or Decimal(str(conversions[0])) <= 0:
			raise ValueError(_("物料 {0} 的采购单位缺少明确的正数换算").format(code))


def item_specs(plan):
	result = {}
	for row in plan["items"]:
		spec = {key: row.get(key) for key in ("item_code", "item_name", "item_group", "uom", "is_stock_item")}
		spec["company"] = plan.get("company")
		if not spec["item_code"]:
			raise ValueError(_("原单缺少可靠物料编码，不能自动创建物料"))
		previous = result.get(spec["item_code"])
		if previous and previous != spec:
			raise ValueError(_("同一物料 {0} 的名称、单位或属性冲突").format(spec['item_code']))
		result[spec["item_code"]] = spec
	return list(result.values())


def check_item_creation(spec):
	code = "".join(str(spec.get("item_code") or "").casefold().split())
	if code in {"/", "-", "--", "n/a", "s/c", "sincodigo", "sincódigo", "无", "暂无", "未填写"} or (
		code and not code.strip("0")
	):
		raise ValueError(_("原单物料编码 {0} 是占位值，缺少可靠物料编码，不能自动创建物料").format(spec['item_code']))
	service = _service()
	if not all(spec.get(key) for key in ("item_code", "item_name", "item_group", "uom", "company")):
		raise ValueError(_("新物料 {0} 的名称、物料组、单位或公司待确认").format(spec.get('item_code') or '—'))
	group = service._native("Item Group", spec["item_group"])
	service._native("UOM", spec["uom"])
	if group.get("is_group"):
		raise ValueError(_("自动新增物料必须选择末级物料组"))
	if not frappe.has_permission("Item", "create"):
		frappe.throw(_("没有权限新建采购物料"), frappe.PermissionError)
	service._write_fields(
		"Item", {"item_code", "item_name", "item_group", "stock_uom", "is_stock_item", "is_purchase_item"}
	)


def ensure_items(plan):
	"""Caller owns the source/Item leases and rollback boundary; never commit or update existing Items."""
	specs = item_specs(plan)
	states = [(spec, read_item(spec["item_code"])) for spec in specs]
	for spec, item in states:
		if item:
			validate_existing_item(spec, item)
		else:
			check_item_creation(spec)
	created = []
	for spec, item in states:
		if item:
			continue
		doc = frappe.new_doc("Item")
		doc.item_code, doc.item_name = spec["item_code"], spec["item_name"]
		doc.item_group, doc.stock_uom = spec["item_group"], spec["uom"]
		doc.is_stock_item, doc.is_purchase_item = spec["is_stock_item"], 1
		if doc.meta.has_field("custom_overseas_business_entity"):
			_service()._write_fields("Item", {"custom_overseas_business_entity"})
			doc.custom_overseas_business_entity = spec["company"]
		doc.insert()
		created.append(doc.name)
	return created


def processors(source):
	from overseas_costing.scripts.import_oa_logistics import _flatten_dingtalk_table_row

	from . import purchase_source_adaptation as adaptation

	return adaptation.processor_rows(source.get("processors"), flatten_row=_flatten_dingtalk_table_row)


def rule_for(source):
	from . import purchase_source_adaptation as adaptation

	if not frappe.db.exists("DocType", DOCTYPE):
		return None
	try:
		key = adaptation.scope_key(adaptation.scope(source))
	except ValueError:
		return None
	if not frappe.db.exists(DOCTYPE, key):
		return None
	rule = frappe.get_doc(DOCTYPE, key)
	if not rule.enabled:
		return None
	return {
		field: rule.get(field)
		for field in (*SCOPE_FIELDS, *CONFIG_FIELDS, "scope_key", "enabled", "rule_version")
	}


def candidate_rule(source, values):
	from . import purchase_source_adaptation as adaptation

	if not isinstance(values, dict) or set(values) - set(CONFIG_FIELDS):
		raise ValueError(_("采购映射规则字段无效"))
	scope = adaptation.scope(source)
	key = adaptation.scope_key(scope)
	result = {field: str(values.get(field) or "").strip() for field in CONFIG_FIELDS}
	result["service_item_code"] = result["service_item_code"] or "DLP-SVC-PROCESSING"
	result["service_item_name"] = result["service_item_name"] or "加工服务"
	if any(not result[field] for field in ("company", "supplier", "item_group", "purchase_uom")):
		raise ValueError(_("请明确采购公司、统一结算供应商、物料组和计价单位"))
	for field in ("service_item_code", "service_item_name"):
		if len(result[field]) > 140:
			raise ValueError(_("服务物料编码或名称过长"))
	result.update(scope, scope_key=key, enabled=1)
	return result


def canonical_roles(source):
	"""Resolve exact native IDs/display labels through the existing ACL matcher."""
	result = copy.deepcopy(source)
	for row, key, doctype in [
		(result, "purchasing_company", "Company"),
		(result, "supplier", "Supplier"),
		*((row, "supplier", "Supplier") for row in result.get("items") or []),
	]:
		if not row.get(key):
			continue
		candidate = _service()._role_candidate(row, key, doctype)
		if candidate["status"] == "unique":
			row[key] = candidate["value"]
		elif candidate["status"] == "unreadable":
			frappe.throw(_("没有权限查看采购公司或供应商"), frappe.PermissionError)
		elif candidate["status"] == "ambiguous":
			row[key + "_status"] = "ambiguous"
	return result


def preview(source, evidence, *, doc=None, candidate=None):
	"""Pure adaptation + permission-scoped native reads. No cache, receipt, or native validate writes."""
	from . import purchase_source_adaptation as adaptation

	service = _service()
	confirmed_rule = rule_for(source)
	rule = candidate_rule(source, candidate) if candidate is not None else confirmed_rule
	adapted_source = canonical_roles(source)
	confirmed = service._confirmed_company(doc) if doc else None
	if confirmed:
		if adapted_source.get("purchasing_company") and adapted_source["purchasing_company"] != confirmed:
			frappe.throw(_("已确认采购公司与原单采购公司冲突"))
		adapted_source["purchasing_company"] = confirmed
	parse_error = None
	try:
		processing_rows = processors(source)
	except ValueError as error:
		processing_rows, parse_error = [], str(error)
	plan = adaptation.build_plan(adapted_source, rule, processors=processing_rows)
	states, new_items = [], []
	problems = list(plan["blocking_reasons"])
	if parse_error:
		problems.append(parse_error)
	for doctype, name in (
		("Company", plan.get("company")),
		("Supplier", plan.get("supplier")),
		("Currency", plan.get("currency")),
		("Item Group", rule.get("item_group") if rule else None),
		("UOM", rule.get("purchase_uom") if rule else None),
	):
		if not name:
			continue
		try:
			native = service._native(doctype, name)
			states.append(
				{
					"doctype": doctype,
					"name": native.name,
					"modified": str(native.get("modified") or ""),
					"disabled": native.get("disabled"),
					"is_group": native.get("is_group"),
				}
			)
			if native.get("disabled") or (doctype == "Item Group" and native.get("is_group")):
				problems.append(
					"供应商已停用" if doctype == "Supplier" else f"{doctype} 状态不适用于自动采购"
				)
			if (
				doctype == "Supplier"
				and native.get("is_internal_supplier")
				and native.get("represents_company") == plan.get("company")
			):
				problems.append("采购公司不能作为自己的内部结算供应商")
		except frappe.PermissionError:
			raise
		except (frappe.ValidationError, frappe.DoesNotExistError) as error:
			problems.append(str(error))
	try:
		for spec in item_specs(plan):
			try:
				item = read_item(spec["item_code"])
				if item:
					validate_existing_item(spec, item)
					states.append(
						{
							"doctype": "Item",
							**{
								key: item.get(key)
								for key in (
									"name",
									"disabled",
									"is_purchase_item",
									"is_stock_item",
									"stock_uom",
									"custom_overseas_business_entity",
								)
							},
							"modified": str(item.get("modified") or ""),
							"uoms": [
								{
									"uom": row.get("uom"),
									"conversion_factor": str(row.get("conversion_factor")),
								}
								for row in item.get("uoms") or []
							],
						}
					)
				else:
					check_item_creation(spec)
					new_items.append(spec)
					states.append({"doctype": "Item", "name": spec["item_code"], "missing": True})
			except (
				ValueError,
				frappe.ValidationError,
				frappe.PermissionError,
				frappe.DoesNotExistError,
			) as error:
				problems.append(str(error))
	except ValueError as error:
		problems.append(str(error))
	plan["blocking_reasons"] = list(dict.fromkeys(problems))
	plan["new_items"], plan["rule"] = new_items, rule
	plan["preview_fingerprint"] = digest(
		{
			"source": source.get("version"),
			"evidence": evidence.get("version"),
			"plan": plan,
			"masters": states,
			"confirmed_rule": confirmed_rule,
		}
	)
	return plan


def confirm_rule(source, evidence, values, expected_fingerprint, *, doc=None):
	from .purchase_repost_boundary import acquire

	candidate = candidate_rule(source, values)
	with acquire(("dlp-source-rule:" + candidate["scope_key"][:40],)):
		plan = preview(source, evidence, doc=doc, candidate=values)
		if not expected_fingerprint or plan["preview_fingerprint"] != expected_fingerprint:
			frappe.throw(_("原单、映射或物料状态已变化，请重新预览"))
		if plan["blocking_reasons"]:
			frappe.throw("；".join(plan["blocking_reasons"]))
		key = candidate["scope_key"]
		rule = (
			frappe.get_doc(DOCTYPE, key, for_update=True)
			if frappe.db.exists(DOCTYPE, key)
			else frappe.new_doc(DOCTYPE)
		)
		rule.update(candidate)
		rule.rule_version = digest(candidate)
		rule.confirmed_by, rule.confirmed_on = frappe.session.user, frappe.utils.now_datetime()
		with managed_rule_write():
			rule.save(ignore_permissions=True)
		return {
			"name": rule.name,
			"rule_version": rule.rule_version,
			"scope": {field: candidate[field] for field in SCOPE_FIELDS},
		}
