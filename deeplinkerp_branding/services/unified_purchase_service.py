"""Permission-aware, read-only purchase orders and optional OA requests view.

List DTO: row.role_context carries confirmed native roles separately from
source candidates/proposals and hints labelled 来源待核对. row.order_progress
is the shared Task2 external/internal/domestic_receipt/receipt_logistics DTO,
with factory_receipt, progress_phases and review_required; OA-only rows have
no ERP progress. Historical cashier evidence never counts as ERP settled.
Permission discovery/role/batch indexes are O(P + E + J) time/space for authorized
rows, related edges and actual JSON/comment bytes. Existing deterministic
association/list sorting adds O(P log P); the complete pipeline is not linear.
"""

from __future__ import annotations

try:
	from frappe import _ as _i18n
except ImportError:
	def _i18n(message):
		return message


import json
import re
from collections import defaultdict
from functools import wraps
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any

try:
	import frappe
	import frappe.permissions
	from frappe.model import get_permitted_fields
	from frappe.model.workflow import get_workflow, get_workflow_name
except ImportError:  # Local pure tests do not require a Frappe installation.
	frappe = None
	get_permitted_fields = None
	get_workflow = get_workflow_name = None


PURCHASE_ORDER = "Purchase Order"
PURCHASE_ORDER_ITEM = "Purchase Order Item"
OA_REQUEST = "OA Purchase Request"
DEFAULT_PAGE_LENGTH = 100
MAX_PAGE_LENGTH = 2500
PO_FIELDS = (
	"name", "transaction_date", "supplier", "supplier_name", "status", "company", "currency",
	"grand_total", "advance_paid", "party_account_currency", "advance_payment_status",
	"per_received", "per_billed", "schedule_date", "project", "owner", "docstatus",
)
PO_QUERY_FIELDS = (*PO_FIELDS, "creation", "modified", "custom_oa_purchase_expense")
OA_QUERY_FIELDS = (
	"name", "oa_code", "apply_date", "creation", "modified", "target_company", "currency",
	"detail_total_amount", "processor_total_amount", "payment_amount", "purchase_order",
	"approval_status", "sync_status", "process_instance_id", "owner", "order_no",
	"custom_purchase_source_json", "custom_cashier_payment_evidence", "custom_purchase_source_id",
	"custom_purchase_beneficiary_company", "custom_purchase_company_proposal", "custom_purchase_project",
	"custom_purchase_company_confirmed", "backfill_imported", "project", "custom_purchase_pending_reason",
)
AMOUNT_FIELDS = (
	("detail_total_amount", "采购明细合计"),
	("processor_total_amount", "加工合计"),
	("payment_amount", "付款申请金额"),
)
CURRENCY_ALIASES = {"人民币RMB": "CNY", "比索Peso": "MXN"}
RECOGNIZED_CURRENCIES = frozenset({
	"CNY", "MXN", "USD", "EUR", "GBP", "HKD", "JPY", "CAD", "AUD", "NZD", "CHF",
	"SGD", "TWD", "KRW", "INR", "BRL", "ZAR", "AED", "SAR", "THB", "VND", "IDR",
	"MYR", "PHP", "RUB", "SEK", "NOK", "DKK", "PLN", "CZK", "TRY", "CLP", "COP", "PEN",
})
EXPORT_LABELS = {
	"name": "采购订单号 / 待完善来源", "transaction_date": "日期", "supplier": "供应商编码",
	"supplier_name": "供应商", "status": "订单状态", "company": "公司", "currency": "订单币种",
	"grand_total": "订单金额", "advance_paid": "已预付", "party_account_currency": "预付款币种",
	"advance_payment_status": "预付款状态", "per_received": "已收货%", "per_billed": "已开票%",
	"schedule_date": "需求日期", "project": "项目", "owner": "创建人", "docstatus": "单据状态",
	"creation": "创建时间", "modified": "修改时间",
	"source": "来源", "oa_name": "OA 记录", "oa_number": "OA 审批号", "approval_status": "审批状态",
	"oa_amount": "来源明细金额", "oa_currency": "OA 币种", "oa_amount_basis": "OA 金额口径",
	"oa_warning": "OA 提示", "oa_references": "OA 来源明细", "row_type": "类型",
	"requested_amount": "来源申请金额", "cashier_paid_amount": "出纳实付（未代表 ERP 入账）", "cashier_currency": "出纳实付币种",
	"cashier_evidence_status": "出纳证据状态", "source_eligible": "来源可办理", "source_version": "来源版本",
	"warehouse": "仓库", "item_code": "物料编码", "item_name": "物料名称", "qty": "数量", "uom": "单位",
	"rate": "单价", "amount": "明细金额", "received_qty": "已入库数量",
}
ITEM_EXPORT_FIELDS = frozenset({"warehouse", "item_code", "item_name", "qty", "uom", "rate", "amount", "received_qty"})
DEFAULT_EXPORT_COLUMNS = (
	"transaction_date", "name", "supplier_name", "status", "company", "source", "oa_number",
	"approval_status", "currency", "grand_total", "oa_currency", "oa_amount", "oa_amount_basis",
	"advance_paid", "party_account_currency", "advance_payment_status", "per_received", "per_billed",
	"schedule_date", "project", "owner", "oa_warning", "oa_references",
)
SORT_FIELDS = (frozenset(EXPORT_LABELS) - {"oa_references"}) | {"row_key"}
NUMERIC_FIELDS = frozenset({"grand_total", "oa_amount", "advance_paid", "per_received", "per_billed", "docstatus", "requested_amount", "cashier_paid_amount"})
NUMERIC_FIELD_TYPES = frozenset({"Currency", "Float", "Int", "Long Int", "Percent", "Check", "Rating", "Duration"})
CASHIER_READ_FIELDS = frozenset({"company", "party", "paid_amount", "paid_from", "paid_to",
	"paid_from_account_currency", "paid_to_account_currency", "reference_no", "reference_date", "remarks"})
EXPORT_LABELS.update({
	"payment_progress": "付款进度", "receipt_progress": "收货进度",
	"oa_source_details": "OA 来源核对明细",
	"purchasing_company": "采购付款公司", "buyer_company_proposal": "建议采购公司（待确认）",
	"beneficiary_companies": "最终归属公司（已核对）", "source_beneficiary_hint": "原始归属（来源待核对）",
	"source_project_hint": "原始项目（来源待核对）", "role_project": "已核对采购项目", "role_warnings": "归属/项目核对提示",
	"external_state": "供应商付款口径", "external_settled": "供应商已付（ERP）", "external_order_unpaid": "订单未付（不等于应付）",
	"external_currency": "供应商付款币种", "internal_orders": "内部订单", "internal_states": "内部结算状态",
	"internal_companies": "内部最终归属公司", "internal_payable_total": "内部应付总额（已提交）",
	"internal_settled": "内部已付（ERP）", "internal_outstanding": "内部应付未付", "internal_currencies": "内部结算币种",
	"internal_warnings": "内部结算核对提示", "domestic_receipt_state": "采购公司收货状态",
	"domestic_receipt_quantities": "采购公司原生收货数量/单位", "factory_receipt_state": "工厂收货核对状态",
	"factory_received_quantities": "工厂原生收货数量/单位", "factory_pending_quantities": "工厂原生未收数量/单位",
	"logistics_states": "物流报告状态", "logistics_reported_quantities": "物流报告数量/单位（不等于入库）",
	"logistics_manual_nodes": "人工物流节点", "logistics_provenance": "物流来源/作者/时间", "logistics_warnings": "物流快照/核对提示",
	"cashier_reconciliation_verified": "历史付款已核对 ERP", "cashier_reconciliation_warning": "历史付款核对提示",
	"progress_warnings": "进度核对提示", "review_required": "待核对", "action_notice": "操作提示",
})
EXPORT_GROUPS = {
	"order_context": ("name", "oa_number", "approval_status", "source", "transaction_date", "status", "oa_warning", "oa_source_details"),
	"supplier_context": ("supplier_name", "supplier", "purchasing_company", "company", "buyer_company_proposal"),
	"project_context": ("project", "role_project", "beneficiary_companies", "source_beneficiary_hint", "source_project_hint", "role_warnings"),
	"external_payment": ("external_state", "grand_total", "currency", "external_settled", "external_order_unpaid", "external_currency",
		"advance_paid", "party_account_currency", "advance_payment_status", "oa_amount", "oa_currency", "oa_amount_basis", "requested_amount", "cashier_paid_amount", "cashier_currency",
		"cashier_evidence_status", "cashier_reconciliation_verified", "cashier_reconciliation_warning"),
	"internal_settlement": ("internal_orders", "internal_companies", "internal_states", "internal_payable_total", "internal_settled",
		"internal_outstanding", "internal_currencies", "internal_warnings"),
	"receipt_logistics": ("per_received", "domestic_receipt_state", "domestic_receipt_quantities", "factory_receipt_state",
		"factory_received_quantities", "factory_pending_quantities", "logistics_states", "logistics_reported_quantities",
		"logistics_manual_nodes", "logistics_provenance", "logistics_warnings"),
	"action_context": ("action_notice", "review_required", "progress_warnings"),
}
EXPORT_MONEY_FIELDS = frozenset({"external_settled", "external_order_unpaid"})
SOURCE_PAYMENT_LEAVES = {
	"oa_amount": ("amount", "currency"), "oa_currency": ("currency", None), "oa_amount_basis": ("amount_basis", None),
	"requested_amount": ("requested_amount", "currency"), "cashier_paid_amount": ("cashier_paid_amount", "cashier_currency"),
	"cashier_currency": ("cashier_currency", None), "cashier_evidence_status": ("cashier_evidence_status", None),
	"cashier_reconciliation_verified": ("cashier_reconciliation_verified", None),
	"cashier_reconciliation_warning": ("cashier_reconciliation_warning", None),
}


def cashier_evidence_readable() -> bool:
	"""Derived external payment facts obey the native payment financial field scope."""
	return bool(frappe.has_permission("Payment Entry", "read") and CASHIER_READ_FIELDS <= _permitted_fields("Payment Entry"))


def _permitted_fields(doctype, **kwargs):
	from .purchase_payment_service import _record_reader
	reader = _record_reader.get()
	if reader and hasattr(reader.fields, "permitted"):
		return reader.fields.permitted(doctype, kwargs.get("parenttype"))
	return set(get_permitted_fields(doctype, permission_type="read", **kwargs))


def _request_reader():
	from .purchase_order_progress import _progress_read_scope
	return _progress_read_scope(get_permitted_fields)


def _read_request(function):
	@wraps(function)
	def wrapper(*args, **kwargs):
		with _request_reader():
			return function(*args, **kwargs)
	return wrapper


def _whitelist(function):
	return frappe.whitelist()(function) if frappe is not None else function


def _text(value: Any) -> str:
	return "" if value is None else str(value).strip()


def _date(value: Any) -> str:
	return _text(value.isoformat() if hasattr(value, "isoformat") else value)[:10]


def _integer(value: Any, *, minimum: int, maximum: int | None = None) -> int:
	if isinstance(value, bool) or not re.fullmatch(r"\+?\d+", str(value).strip()):
		raise ValueError(_i18n("分页参数必须是整数。"))
	parsed = int(value)
	if parsed < minimum or (maximum is not None and parsed > maximum):
		raise ValueError(_i18n("分页参数必须在 {0} 到 {1} 之间。").format(minimum, maximum or '无限制'))
	return parsed


def _filters(value: Any) -> dict[str, Any]:
	if isinstance(value, str):
		value = json.loads(value or "{}")
	if value is not None and not isinstance(value, dict):
		raise ValueError(_i18n("筛选条件必须是对象。"))
	parsed = dict(value or {})
	parsed["scope"] = _text(parsed.get("scope")) or "all"
	parsed["source"] = _text(parsed.get("source")).lower()
	if parsed["scope"] not in {"all", "orders", "oa"}:
		raise ValueError(_i18n("不支持的采购查看范围。"))
	if parsed["source"] not in {"", "oa", "non_oa"}:
		raise ValueError(_i18n("不支持的采购来源。"))
	parsed["progress_phase"] = _text(parsed.get("progress_phase"))
	if parsed["progress_phase"] not in {"", "supplier_unpaid", "internal_unsettled", "factory_pending"}:
		raise ValueError(_i18n("不支持的采购进度阶段。"))
	if not isinstance(parsed.get("review_only", False), bool):
		raise ValueError(_i18n("待核对筛选必须是布尔值。"))
	parsed["review_only"] = parsed.get("review_only", False)
	for key in ("company", "beneficiary_company", "status", "advance_payment_status", "approval_status", "search"):
		parsed[key] = _text(parsed.get(key))
	parsed["search"] = parsed["search"].casefold()
	for key in ("from_date", "to_date"):
		if parsed.get(key) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", _text(parsed[key])):
			raise ValueError(_i18n("采购日期必须使用 YYYY-MM-DD。"))
	return parsed


def _sort_spec(order_by: str, native_fields=()) -> tuple[str, bool]:
	parts = _text(order_by).split()
	if len(parts) != 2 or parts[0] not in SORT_FIELDS | set(native_fields) or parts[1].lower() not in {"asc", "desc"}:
		raise ValueError(_i18n("不支持的采购排序。"))
	return parts[0], parts[1].lower() == "desc"


def _native_projection(permitted: set[str], order_by: str) -> dict[str, str]:
	"""Carry the active workflow and chosen native scalar, never all custom fields."""
	parts = _text(order_by).split()
	if len(parts) != 2 or not re.fullmatch(r"[a-z_][a-z0-9_]*", parts[0]) or parts[1].lower() not in {"asc", "desc"}:
		raise ValueError(_i18n("不支持的采购排序。"))
	selected, fields = parts[0], {}
	workflow_field = None
	if get_workflow_name and get_workflow_name(PURCHASE_ORDER):
		workflow_field = get_workflow(PURCHASE_ORDER).workflow_state_field
	if selected not in SORT_FIELDS or selected in {"creation", "modified"} or workflow_field in permitted:
		from .purchase_payment_service import _query_fields, _require_fields
		allowed = _query_fields(PURCHASE_ORDER)
		meta = frappe.get_meta(PURCHASE_ORDER)
		if workflow_field in permitted and workflow_field in allowed:
			fields[workflow_field] = meta.get_field(workflow_field).fieldtype
		if selected not in SORT_FIELDS or selected in {"creation", "modified"} or selected == workflow_field:
			if selected not in allowed:
				raise ValueError(_i18n("不支持的采购排序。"))
			_require_fields(PURCHASE_ORDER, {selected})
			df = meta.get_field(selected)
			fields[selected] = df.fieldtype if df else "Datetime" if selected in {"creation", "modified"} else "Data"
	_sort_spec(order_by, fields)
	return fields


def _native_purchase_filters(value) -> list[list]:
	"""Only the existing PO items table may extend native parent filters."""
	from .purchase_payment_service import _native_filters, _normalize_filter, _query_fields, _require_fields
	value = json.loads(value) if isinstance(value, str) else value or []
	allowed = _query_fields(PURCHASE_ORDER)
	if not isinstance(value, list):
		return _native_filters(PURCHASE_ORDER, value, allowed)
	result = []
	for entry in value:
		if isinstance(entry, (list, tuple)) and len(entry) == 4 and entry[0] == PURCHASE_ORDER_ITEM:
			items = frappe.get_meta(PURCHASE_ORDER).get_field("items")
			if not items or items.fieldtype != "Table" or items.options != PURCHASE_ORDER_ITEM:
				frappe.throw(_i18n("采购订单明细字段不可用。"))
			field, operator, operand = entry[-3:]
			if field not in _query_fields(PURCHASE_ORDER_ITEM):
				frappe.throw(_i18n("不支持此采购订单明细字段。"))
			_require_fields(PURCHASE_ORDER, {"items"})
			_require_fields(PURCHASE_ORDER_ITEM, {field}, parenttype=PURCHASE_ORDER)
			result.append(_normalize_filter(PURCHASE_ORDER_ITEM, field, str(operator).lower(), operand))
		else:
			result.extend(_native_filters(PURCHASE_ORDER, [entry], allowed))
	return result


def _number(value: Any) -> Decimal | None:
	if value is None or value == "" or isinstance(value, bool):
		return None
	try:
		number = Decimal(str(value))
	except (InvalidOperation, ValueError):
		return None
	return number if number.is_finite() else None


def _oa_amount(request: dict) -> tuple[Any, str | None, str | None]:
	available = []
	for fieldname, basis in AMOUNT_FIELDS:
		if fieldname not in request:
			return None, None, "无权查看完整 OA 金额，待核实"
		value = request.get(fieldname)
		number = _number(value)
		if value is not None and value != "" and number is None:
			return None, None, "原始 OA 金额无效，待核实"
		if number is not None:
			available.append((value, basis))
			if number != 0:
				return value, basis, None
	if available:
		value, basis = available[-1]
		return value, basis, "原始金额为0，待核实"
	return None, None, None


def _currency(value: Any, currency_codes: set[str]) -> str | None:
	text = _text(value)
	alias = CURRENCY_ALIASES.get(text)
	if alias:
		return alias
	code = text.upper()
	return code if code in RECOGNIZED_CURRENCIES or code in currency_codes else None


def _oa_metadata(request: dict, currency_codes: set[str]) -> dict:
	if "_metadata" in request:
		return request["_metadata"]
	amount, basis, amount_warning = _oa_amount(request)
	managed = request["_source"]
	if managed:
		issues = managed.get("issues") or []
		linked = bool(request.get("_has_order_link") or request.get("purchase_order"))
		# These original parser hints are recomputed by the existing adaptation.
		# Current pending reasons and unrelated source evidence remain authoritative.
		if (not linked and _text(request.get("custom_purchase_pending_reason"))) or (linked and request.get("_adaptation_adopted")):
			issues = [issue for issue in issues if issue not in {"采购明细待完善", "采购明细与申请金额不一致，请核对原单"}]
		amount, basis, amount_warning = managed.get("detail_total_amount"), "钉钉采购明细合计", "；".join(issues)
	proof = request["_proof"]
	currency = _currency(managed.get("currency") if managed else request.get("currency"), currency_codes)
	warnings = [amount_warning] if amount_warning else []
	if request.get("custom_purchase_pending_reason"):
		warnings.append(request["custom_purchase_pending_reason"])
	if not currency:
		warnings.append("OA 币种未知，未计入合计")
	request["_metadata"] = {
		"oa_name": request["name"],
		"oa_number": managed.get("business_id") or request.get("oa_code") or request.get("order_no") or request["name"],
		"approval_status": request.get("approval_status"),
		"oa_amount": amount, "oa_currency": currency, "oa_amount_basis": basis,
		"oa_warning": "；".join(warnings) or None,
		"requested_amount":managed.get("requested_amount"), "cashier_paid_amount":proof.get("paid_amount"), "cashier_currency":proof.get("currency"),
		"cashier_evidence_status":proof.get("payment_evidence_status"), "source_eligible":managed.get("eligible"), "source_version":managed.get("version"),
		"cashier_reconciliation_verified": request.get("_reconciliation_verified"),
		"cashier_reconciliation_warning": request.get("_reconciliation_warning"),
	}
	return request["_metadata"]


def _prepare_request(record):
	"""Parse each private blob once; never attach its arbitrary keys to public rows."""
	request = dict(record)
	for field, key in (("custom_purchase_source_json", "_source"), ("custom_cashier_payment_evidence", "_proof")):
		if key not in request:
			value = request.get(field)
			request[key] = json.loads(value or "{}") if isinstance(value, str) else dict(value or {})
	return request


def _request_role(request, order=None):
	role = dict(request.get("_role_context") or {})
	source = request["_source"]
	managed = bool(request.get("custom_purchase_source_id") or source)
	company = request.get("target_company") if not managed or request.get("custom_purchase_company_confirmed") in (True, 1, "1") else None
	if order:
		company = order.get("company")
	if not role:
		role = {"purchasing_company": company, "company_confirmed": bool(company), "beneficiary_companies": [],
			"beneficiary_company": None, "source_hint_label": "来源待核对", "role_warnings": []}
		if managed:
			role.update(source_beneficiary_company_hint=source.get("beneficiary_company"), source_project_hint=source.get("project"))
			if not company:
				role["role_warnings"] = ["采购公司待确认；申请人组织仅为采购公司建议"]
	if order:
		role.update(purchasing_company=order.get("company"), company_confirmed=bool(order.get("company")))
	return role


def _combine_warnings(*values) -> str | None:
	parts = [part for value in values if value for part in value.split("；") if part]
	return "；".join(dict.fromkeys(parts)) or None


def _oa_reference(request: dict, currency_codes: set[str], *, order=None, association_warning=None) -> dict:
	metadata = _oa_metadata(request, currency_codes)
	company_warning = None
	managed = bool(request.get("custom_purchase_source_id") or request["_source"])
	confirmed = not managed or request.get("custom_purchase_company_confirmed") in (True, 1, "1")
	if confirmed and order and _text(order.get("company")) and _text(request.get("target_company")):
		if _text(order["company"]) != _text(request["target_company"]):
			company_warning = "OA 公司与订单公司不一致"
	return {
		"name": metadata["oa_name"], "number": metadata["oa_number"],
		"approval_status": metadata["approval_status"], "amount": metadata["oa_amount"],
		"currency": metadata["oa_currency"], "amount_basis": metadata["oa_amount_basis"],
		"company": request.get("target_company") or None if confirmed else None,
		"warning": _combine_warnings(metadata["oa_warning"], association_warning, company_warning),
		**({"cashier_reconciliation_verified": metadata["cashier_reconciliation_verified"],
			"cashier_reconciliation_warning": metadata["cashier_reconciliation_warning"],
			"cashier_paid_amount": metadata["cashier_paid_amount"], "cashier_currency": metadata["cashier_currency"],
			"cashier_evidence_status": metadata["cashier_evidence_status"], "requested_amount": metadata["requested_amount"]} if managed else {}),
	}


def _base_row(record: dict, doctype: str, native_fields=()) -> dict:
	return {
		**{field: record.get(field) if doctype == PURCHASE_ORDER else None for field in PO_FIELDS},
		**{field: record.get(field) if doctype == PURCHASE_ORDER else None for field in native_fields},
		**{field: record[field] for field in ("creation", "modified") if field in record},
		"name": record["name"], "row_key": f"{doctype}::{record['name']}",
		"row_type": "purchase_order" if doctype == PURCHASE_ORDER else "oa_request",
		"source": None, "oa_name": None, "oa_number": None, "approval_status": None,
		"oa_amount": None, "oa_currency": None, "oa_amount_basis": None, "oa_warning": None,
		"oa_references": [],
		"order_progress": record.get("order_progress") if doctype == PURCHASE_ORDER else None,
		"role_context": dict(record.get("_role_context") or {"purchasing_company": record.get("company") if doctype == PURCHASE_ORDER else None,
			"company_confirmed": bool(record.get("company")) if doctype == PURCHASE_ORDER else False, "beneficiary_companies": [], "role_warnings": []}),
	}


def _canonical_rows(purchase_orders, oa_requests, currency_codes: set[str], oa_reverse_link_readable: bool, native_fields=()) -> list[dict]:
	orders = {_text(row.get("name")): dict(row) for row in purchase_orders if _text(row.get("name"))}
	requests = {_text(row.get("name")): _prepare_request(row) for row in oa_requests if _text(row.get("name"))}
	associations, targets = defaultdict(set), defaultdict(set)
	for name, order in orders.items():
		linked = _text(order.get("custom_oa_purchase_expense"))
		if linked:
			targets[linked].add(name)
			if linked in requests:
				associations[name].add(linked)
	for name, request in requests.items():
		linked = _text(request.get("purchase_order"))
		if linked:
			targets[name].add(linked)
			if linked in orders:
				associations[linked].add(name)
		request["_has_order_link"] = bool(targets[name])
	rows, suppressed = [], set()
	for name, order in orders.items():
		row = _base_row(order, PURCHASE_ORDER, native_fields)
		row["_company_readable"] = "company" in order
		linked_names = sorted(associations[name])
		row["_search_values"] = []
		row["_approval_statuses"] = []
		if linked_names:
			suppressed.update(linked_names)
			row["source"] = "OA"
			row["_approval_statuses"] = [requests[key].get("approval_status") for key in linked_names]
			row["_search_values"] = [requests[key].get(field) for key in linked_names for field in ("name", "oa_code", "order_no")]
			conflict = any(len(targets[key]) > 1 for key in linked_names)
			if len(linked_names) == 1 and not conflict:
				row.update(_oa_metadata(requests[linked_names[0]], currency_codes))
			else:
				row["oa_number"] = "、".join(_text(_oa_metadata(requests[key],currency_codes)["oa_number"]) for key in linked_names)
				row["oa_warning"] = "OA 关联冲突，金额待核实" if conflict else "关联多个 OA，金额待核实"
			association_warning = row["oa_warning"] if conflict or len(linked_names) > 1 else None
			row["oa_references"] = [
				_oa_reference(requests[key], currency_codes, order=order, association_warning=association_warning)
				for key in linked_names
			]
			row["oa_warning"] = _combine_warnings(*(reference["warning"] for reference in row["oa_references"]))
			roles = [_request_role(requests[key], order) for key in linked_names]
			row["role_context"] = {**roles[0], "beneficiary_companies": list(dict.fromkeys(
				value for role in roles for value in role.get("beneficiary_companies", []))),
				"role_warnings": list(dict.fromkeys(value for role in roles for value in role.get("role_warnings", [])))}
			row["_search_values"].extend(value for role in roles for value in (role.get("project"), role.get("project_candidate"), role.get("source_project_hint")))
		elif "custom_oa_purchase_expense" in order:
			if order.get("custom_oa_purchase_expense"):
				row["source"] = "OA"
				row["oa_warning"] = "OA 关联记录不可见或不存在"
			elif oa_reverse_link_readable:
				row["source"] = "未关联 OA"
		rows.append(row)
	for name, request in requests.items():
		if name in suppressed:
			continue
		row = _base_row(request, OA_REQUEST, native_fields)
		row.update(_oa_metadata(request, currency_codes))
		role = _request_role(request)
		status = "已生成订单（无权查看）" if request.get("purchase_order") else "未生成订单"
		if "purchase_order" not in request:
			status = "订单关联不可见"
			row["oa_warning"] = _combine_warnings(row["oa_warning"], status)
		row.update({
			"source": "OA", "transaction_date": _date(request["_source"].get("apply_date") if request["_source"] else request.get("apply_date")) or None,
			"company": role.get("purchasing_company"), "owner": request.get("owner"), "role_context": role,
			"_company_readable": "target_company" in request,
			"status": status,
			"oa_references": [_oa_reference(request, currency_codes, association_warning="订单关联不可见" if "purchase_order" not in request else None)],
			"_approval_statuses": [request.get("approval_status")],
			"_search_values": [request.get("oa_code"), request.get("order_no"), role.get("project"), role.get("project_candidate"), role.get("source_project_hint")],
		})
		rows.append(row)
	return rows


def _matches(row: dict, filters: dict) -> bool:
	if filters["scope"] == "orders" and row["row_type"] != "purchase_order":
		return False
	if filters["scope"] == "oa" and row["row_type"] != "oa_request":
		return False
	source = {"oa": "OA", "non_oa": "未关联 OA"}.get(filters["source"])
	if source and row["source"] != source:
		return False
	for field in ("status","advance_payment_status"):
		if filters.get(field) and row.get(field) != filters[field]:
			return False
	company = filters["company"]
	if company == "__unconfirmed__":
		if not row.get("_company_readable") or _text(row.get("company")):
			return False
	elif company and _text(row.get("company")) != company:
		return False
	date = _date(row.get("transaction_date"))
	if filters.get("from_date") and (not date or date < filters["from_date"]):
		return False
	if filters.get("to_date") and (not date or date > filters["to_date"]):
		return False
	approval = filters["approval_status"]
	if approval and approval not in [_text(value) for value in row.get("_approval_statuses", [])]:
		return False
	search = filters["search"]
	values = [row.get(field) for field in ("name", "supplier", "supplier_name", "oa_name", "oa_number", "project", "owner")]
	return not search or any(search in _text(value).casefold() for value in values + row.get("_search_values", []))


def _load_order_progress(names, include_items=False):
	from .purchase_order_progress import _get_order_progress_batch
	return _get_order_progress_batch(names, include_items=include_items)


def _restricted_order_progress(name):
	from .purchase_order_progress import _restricted_progress
	return _restricted_progress(name)


def _project_source_roles(rows, sources, fields, orders):
	from .purchase_source_service import _role_projections
	return _role_projections(rows, sources, fields, {row["name"]: row for row in orders})


def _load_company_scope(names):
	"""One authority-only batch; never scan payment, receipt or logistics edges."""
	from .purchase_payment_service import _require_fields, _quiet_link_errors
	from .purchase_order_progress import _progress_read_scope
	from .purchase_fulfilment_service import _company
	names = list(dict.fromkeys(names))
	if not names:
		return set()
	with _progress_read_scope(get_permitted_fields) as reader:
		cache = getattr(reader, "list_company_scope", {})
		reader.list_company_scope = cache
		pending = [name for name in names if name not in cache]
		with _quiet_link_errors():
			try:
				_require_fields("Company", {"name"})
			except frappe.PermissionError:
				cache.update((name, False) for name in pending)
				pending = []
		reader.preload("Company", pending)
		for name in pending:
			with _quiet_link_errors():
				try:
					_company(name)
					cache[name] = True
				except (frappe.PermissionError, frappe.DoesNotExistError):
					cache[name] = False
		return {name for name in names if cache[name]}


def _load_order_fields_scope():
	from .purchase_order_progress import _require_source_order_fields
	from .purchase_payment_service import _quiet_link_errors
	with _quiet_link_errors():
		try:
			_require_source_order_fields()
			return True
		except frappe.PermissionError:
			return False


def _redact_restricted_row(row, native_fields=()):
	"""The same conservative row is used before filters/totals and at progress IO."""
	warning = "关联进度受限，请核对公司和字段权限"
	for field in (*native_fields, "company", "supplier", "supplier_name", "currency", "grand_total", "advance_paid",
		"party_account_currency", "advance_payment_status", "per_received", "per_billed", "project", "oa_amount", "oa_currency", "oa_amount_basis", "requested_amount",
		"cashier_paid_amount", "cashier_currency", "cashier_evidence_status", "cashier_reconciliation_verified"):
		if field not in {"name", "creation", "modified", "status", "docstatus", "transaction_date", "owner"}:
			row[field] = None
	row["_company_readable"] = False
	row["_search_values"] = []
	row["role_context"] = {"purchasing_company": None, "company_confirmed": False, "beneficiary_companies": [],
		"role_warnings": [warning]}
	row["order_progress"] = _restricted_order_progress(row["name"]) if row["row_type"] == "purchase_order" else None
	row["review_required"] = True
	row["oa_warning"] = warning if row["oa_references"] else None
	row["cashier_reconciliation_warning"] = warning if row["oa_references"] else None
	row["oa_references"] = [{**reference, **{key: None for key in ("amount", "currency", "amount_basis", "company", "requested_amount",
		"cashier_paid_amount", "cashier_currency", "cashier_evidence_status", "cashier_reconciliation_verified")},
		"warning": warning, "cashier_reconciliation_warning": warning}
		for reference in row["oa_references"]]


def _authorize_native_rows(rows, company_loader, order_fields_loader, native_fields):
	if company_loader is None and order_fields_loader is None:
		return
	order_fields_readable = order_fields_loader() if order_fields_loader and any(row["row_type"] == "purchase_order" for row in rows) else True
	readable = company_loader(row["company"] for row in rows if row.get("company")) if company_loader else None
	for row in rows:
		if ((readable is not None and row.get("company") and row["company"] not in readable) or
			(row["row_type"] == "purchase_order" and (not row.get("company") or not order_fields_readable))):
			_redact_restricted_row(row, native_fields)


def _attach_progress(rows, loader=None, *, include_items=False):
	orders = [row for row in rows if row["row_type"] == "purchase_order" and (row.get("order_progress") or {}).get("state") != "restricted"]
	names = [row["name"] for row in orders]
	progress = (loader(names, include_items=True) if include_items else loader(names)) if loader and orders else {}
	for row in rows:
		if row["name"] in progress and row["row_type"] == "purchase_order":
			row["order_progress"] = progress[row["name"]]
		value = row.get("order_progress")
		role = row["role_context"]
		if value and value.get("state") == "restricted":
			_redact_restricted_row(row)
			role = row["role_context"]
		elif value:
			role["beneficiary_companies"] = list(dict.fromkeys([*role.get("beneficiary_companies", []),
				*(entry["beneficiary_company"] for entry in [*value.get("internal", []), *value.get("receipt_logistics", [])]
					if entry.get("beneficiary_company") and entry.get("state") not in ("restricted", "setup_required"))]))
		row["review_required"] = bool(role.get("role_warnings") or row.get("oa_warning") or
			(value.get("review_required") if value else True))
		row["action_notice"] = "打开订单办理；操作时按原生权限重新核对" if row["row_type"] == "purchase_order" else "来源待完善，请核对后关联原生采购订单"


def _computed_matches(row, filters):
	if filters["beneficiary_company"] and filters["beneficiary_company"] not in row["role_context"].get("beneficiary_companies", []):
		return False
	if filters["progress_phase"] and filters["progress_phase"] not in (row.get("order_progress") or {}).get("progress_phases", []):
		return False
	return not filters["review_only"] or row.get("review_required", True)


def _needs_full_progress(filters):
	return bool(filters["beneficiary_company"] or filters["progress_phase"] or filters["review_only"])


def _pipeline(purchase_orders, oa_requests, *, filters=None, order_by="transaction_date desc", currency_codes=(),
	oa_reverse_link_readable=True, native_fields=None, progress_loader=None, full_progress=False, company_loader=None, order_fields_loader=None):
	parsed = _filters(filters)
	native_fields = native_fields or {}
	field, descending = _sort_spec(order_by, native_fields)
	rows = _canonical_rows(purchase_orders, oa_requests, set(currency_codes), oa_reverse_link_readable, native_fields)
	_authorize_native_rows(rows, company_loader, order_fields_loader, native_fields)
	rows = [row for row in rows if _matches(row, parsed)]
	if full_progress or _needs_full_progress(parsed):
		_attach_progress(rows, progress_loader)
	rows = [row for row in rows if _computed_matches(row, parsed)]
	chronological = field == "transaction_date" and descending
	rows.sort(key=lambda row: (_text(row.get("name")).casefold(), row["row_key"]) if chronological else (row["row_key"],), reverse=chronological)
	nonempty, empty = [], []
	for row in rows:
		(empty if row.get(field) is None else nonempty).append(row)
	numeric = field in NUMERIC_FIELDS or native_fields.get(field) in NUMERIC_FIELD_TYPES
	nonempty.sort(key=lambda row: (_number(row[field]) or Decimal(0)) if numeric else _text(row[field]).casefold(), reverse=descending)
	rows = nonempty + empty
	totals = {"orders": defaultdict(Decimal), "oa": defaultdict(Decimal)}
	unknown_names = set()
	for row in rows:
		for kind, currency_field, amount_field in (("orders", "currency", "grand_total"), ("oa", "oa_currency", "oa_amount")):
			amount, currency = _number(row.get(amount_field)), _text(row.get(currency_field))
			if currency and amount is not None:
				totals[kind][currency] += amount
		unknown_names.update(reference["name"] for reference in row["oa_references"] if not reference["currency"])
	totals = {kind: [{"currency": code, "amount": float(value)} for code, value in sorted(group.items())] for kind, group in totals.items()}
	totals["oa_unknown_currency_count"] = len(unknown_names)
	return rows, totals


def _public_rows(rows: list[dict]) -> list[dict]:
	return [{
		**{key: value for key, value in row.items() if not key.startswith("_")},
		"company_visibility": "hidden" if not row["_company_readable"]
			else ("known" if _text(row.get("company")) else "pending"),
	} for row in rows]


def build_unified_purchase_payload(purchase_orders, oa_requests, *, filters=None, start=0,
	page_length=DEFAULT_PAGE_LENGTH, order_by="transaction_date desc", currency_codes=(),
	capabilities=None, warnings=(), oa_reverse_link_readable=True, native_fields=None, progress_loader=None, company_loader=None, order_fields_loader=None, include_items=False) -> dict:
	start = _integer(start, minimum=0)
	page_length = _integer(page_length, minimum=1, maximum=MAX_PAGE_LENGTH)
	rows, totals = _pipeline(purchase_orders, oa_requests, filters=filters, order_by=order_by,
		currency_codes=currency_codes, oa_reverse_link_readable=oa_reverse_link_readable, native_fields=native_fields,
		progress_loader=progress_loader, company_loader=company_loader, order_fields_loader=order_fields_loader)
	page = rows[start:start + page_length]
	if include_items or not _needs_full_progress(_filters(filters)):
		_attach_progress(page, progress_loader, include_items=include_items)
	return {
		"rows": _public_rows(page), "total_count": len(rows),
		"start": start, "page_length": page_length, "has_previous": start > 0,
		"has_next": start + page_length < len(rows), "totals": totals,
		"capabilities": capabilities or {"purchase_order": True, "oa_request": True}, "warnings": list(warnings),
	}


def _columns(columns: Any) -> list[str]:
	if isinstance(columns, str):
		columns = json.loads(columns)
	if columns is None:
		return list(DEFAULT_EXPORT_COLUMNS)
	if not isinstance(columns, list) or not columns or any(not isinstance(key, str) or key not in EXPORT_LABELS.keys() | EXPORT_GROUPS.keys() for key in columns):
		raise ValueError(_i18n("不支持的采购导出列。"))
	if len(set(columns)) != len(columns):
		raise ValueError(_i18n("采购导出列不能重复。"))
	return list(dict.fromkeys(field for key in columns for field in EXPORT_GROUPS.get(key, (key,))))


def _export_value(field: str, row: dict) -> Any:
	"""Display-only cells; canonical identity, permissions and evidence stay intact."""
	if field == "payment_progress":
		external = (row.get("order_progress") or {}).get("external") or {}
		if external.get("state") == "restricted":
			return "付款进度不可见"
		if external.get("state") == "not_applicable":
			return "外部付款不适用"
		settled, unpaid = _number(external.get("settled")), _number(external.get("order_unpaid"))
		if external.get("state") != "exact" or settled is None or unpaid is None:
			return "付款口径待核对"
		return "已付清" if unpaid <= 0 else "部分付款" if settled > 0 else "未付款"
	if field == "receipt_progress":
		received = _number(row.get("per_received"))
		return "收货进度不可见" if received is None else "已收齐" if received >= 100 else "部分收货" if received > 0 else "未收货"
	if field in ITEM_EXPORT_FIELDS:
		progress = row.get("order_progress") or {}
		permitted = set(progress.get("item_fields") or [])
		items = [item for item in progress.get("items", []) if item.get("name")] if "name" in permitted and progress.get("state") != "restricted" else []
		return "\n".join((_display_number(item.get(field), quantity=field in {"qty", "received_qty"})
			if field in {"qty", "received_qty", "rate", "amount"} else _text(item.get(field)) or "—")
			if field in permitted else "—" for item in items) or "—"
	if field == "name" and row.get("row_type") == "oa_request":
		return f"待完善 · {row.get('oa_number') or row['name']}"
	if field == "source":
		return {"OA": "钉钉", "oa": "钉钉", "non_oa": "其他来源", "未关联 OA": "其他来源"}.get(
			row.get(field), row.get(field) or "来源待确认")
	if field == "oa_references":
		return json.dumps(row.get(field) or [], ensure_ascii=False, default=str)
	if len(row.get("oa_references", [])) > 1 and field in SOURCE_PAYMENT_LEAVES:
		if "_export_projection" not in row:
			row["_export_projection"] = _progress_export_projection(row)
		return row["_export_projection"].get(field)
	if field in EXPORT_LABELS and field not in row:
		if "_export_projection" not in row:
			row["_export_projection"] = _progress_export_projection(row)
		return row["_export_projection"].get(field)
	if field in NUMERIC_FIELDS and _number(row.get(field)) is not None:
		return float(_number(row[field]))
	return row.get(field)


def _display_number(value, *, quantity=False):
	number = _number(value)
	if number is None:
		return "—"
	text = f"{number:.2f}"
	return text.rstrip("0").rstrip(".") if quantity else text


def _quantity_text(row, *, key="qty", unit="uom"):
	return f"{_display_number(row.get(key), quantity=True)} {_text(row.get(unit)) or '单位待核对'}"


def _progress_export_projection(row):
	"""Fixed readable leaves, one projection per row; no JSON progress cell.

    Independent internal orders/currencies and receipt stock UOM remain on
    separate labelled lines. Formatting never changes native DTO precision.
    """
	progress = row.get("order_progress") or {}; role = row.get("role_context") or {}
	external = progress.get("external") or {}; internal = progress.get("internal") or []
	domestic = progress.get("domestic_receipt") or {}; factory = progress.get("factory_receipt") or {}
	logistics = progress.get("receipt_logistics") or []
	def lines(values):
		return "\n".join(_text(value) for value in values if value is not None and _text(value)) or None
	def internal_values(key, monetary=False):
		return lines(f"{entry.get('internal_order') or '关联待核对'}: " + (
			_display_number(entry.get(key)) + " " + (_text(entry.get("currency")) or "币种待核对") if monetary else
			_text(entry.get(key)) or "—") for entry in internal)
	def native_quantities(entries, key="qty", unit="uom"):
		return lines(((_text(entry.get("internal_order")) + ": ") if entry.get("internal_order") else "") +
			_quantity_text(entry, key=key, unit=unit) for entry in entries)
	source_details = []; source_leaves = {key: [] for key in SOURCE_PAYMENT_LEAVES}
	for reference in row.get("oa_references", []):
		identity = " / ".join(_text(reference.get(key)) for key in ("name", "number") if reference.get(key)) or "来源待核对"
		for field, (key, currency_key) in SOURCE_PAYMENT_LEAVES.items():
			value = reference.get(key)
			if currency_key:
				text = _display_number(value) + " " + (_text(reference.get(currency_key)) or "币种待核对")
			elif field == "cashier_reconciliation_verified":
				text = "已核对 ERP" if value is True else "尚未核对 ERP" if value is False else "未知（权限或证据待核对）"
			else:
				text = _text(value) or "—"
			source_leaves[field].append(identity + ": " + text)
		source_details.append(" · ".join(part for part in (
			_text(reference.get("name")), _text(reference.get("number")), _text(reference.get("approval_status")),
			_display_number(reference.get("amount")) + " " + (_text(reference.get("currency")) or "币种待核对"),
			_text(reference.get("amount_basis")), _text(reference.get("company")), _text(reference.get("warning")),
			("出纳证据 " + _display_number(reference.get("cashier_paid_amount")) + " " + (_text(reference.get("cashier_currency")) or "币种待核对"))
				if reference.get("cashier_paid_amount") is not None else "",
			"已核对 ERP 付款" if reference.get("cashier_reconciliation_verified") else _text(reference.get("cashier_reconciliation_warning"))) if part))
	return {
		**{key: lines(values) for key, values in source_leaves.items()}, "oa_source_details": lines(source_details),
		"purchasing_company": role.get("purchasing_company"), "buyer_company_proposal": role.get("buyer_company_proposal"),
		"beneficiary_companies": lines(role.get("beneficiary_companies", [])), "role_project": role.get("project"),
		"source_beneficiary_hint": role.get("source_beneficiary_company_hint"), "source_project_hint": role.get("source_project_hint"),
		"role_warnings": lines(role.get("role_warnings", [])), "external_state": external.get("state"),
		"external_settled": float(_number(external["settled"])) if _number(external.get("settled")) is not None else None,
		"external_order_unpaid": float(_number(external["order_unpaid"])) if _number(external.get("order_unpaid")) is not None else None,
		"external_currency": external.get("currency"), "internal_orders": lines(entry.get("internal_order") for entry in internal),
		"internal_states": internal_values("state"), "internal_companies": internal_values("beneficiary_company"),
		"internal_payable_total": internal_values("payable_total", True), "internal_settled": internal_values("payable_settled", True),
		"internal_outstanding": internal_values("payable_outstanding", True), "internal_currencies": internal_values("currency"),
		"internal_warnings": lines(f"{entry.get('internal_order') or '关联待核对'}: {warning}" for entry in internal for warning in entry.get("warnings", [])),
		"domestic_receipt_state": domestic.get("state"), "domestic_receipt_quantities": native_quantities(domestic.get("quantities", [])),
		"factory_receipt_state": factory.get("state"),
		"factory_received_quantities": native_quantities(factory.get("quantities", []), "received_stock_qty", "stock_uom"),
		"factory_pending_quantities": native_quantities(factory.get("quantities", []), "pending_stock_qty", "stock_uom"),
		"logistics_states": lines(f"{entry.get('cost_batch') or '来源待关联'}: {entry.get('state') or 'unknown'}" for entry in logistics),
		"logistics_reported_quantities": lines(f"{entry.get('cost_batch') or '来源待关联'}: {_quantity_text(qty)} {_text(qty.get('destination'))}"
			for entry in logistics for qty in entry.get("reported_quantities", [])),
		"logistics_manual_nodes": lines(f"{entry.get('name') or '关联待核对'}: " + " · ".join(_text(node.get(key)) for key in ("node", "note", "by", "on") if node.get(key)) +
			(" · " + _quantity_text(node) if node.get("qty") is not None else "") for entry in logistics for node in entry.get("manual_nodes", [])),
		"logistics_provenance": lines(f"{entry.get('cost_batch') or '来源待关联'}: " + " · ".join(_text(proof.get(key)) for key in ("source_id", "author", "time", "remark") if proof.get(key))
			for entry in logistics for proof in entry.get("provenance", [])),
		"logistics_warnings": lines(warning for entry in [domestic, factory, *logistics] for warning in entry.get("warnings", [])),
		"progress_warnings": lines(progress.get("progress_warnings", [])),
	}


def _export_data(rows: list[dict], columns: Any) -> list[list]:
	fields = _columns(columns)
	export_rows = [dict(row) for row in rows]
	return [
		[EXPORT_LABELS[field] for field in fields],
		*[[_export_value(field, row) for field in fields] for row in export_rows],
	]


def build_unified_purchase_export(purchase_orders, oa_requests, *, filters=None, columns=None,
	order_by="transaction_date desc", currency_codes=(), oa_reverse_link_readable=True, native_fields=None) -> list[list]:
	rows, _ = _pipeline(purchase_orders, oa_requests, filters=filters, order_by=order_by,
		currency_codes=currency_codes, oa_reverse_link_readable=oa_reverse_link_readable, native_fields=native_fields, full_progress=True)
	return _export_data(rows, columns)


def _require_export_permission(doctype: str, records) -> None:
	"""Match native report export authority, including its exact-owner fallback."""
	if frappe.permissions.can_export(doctype):
		return
	if not frappe.permissions.can_export(doctype, is_owner=True):
		frappe.throw(_i18n("没有权限导出{0}。").format(doctype), frappe.PermissionError)
	for record in records:
		if "owner" in record:
			owner = record.get("owner")
		else:
			# Authority-only read: never restore a denied owner field to the projection.
			doc = frappe.get_doc(doctype, record["name"])
			doc.check_permission("read")
			owner = doc.get("owner")
		if not owner or owner != frappe.session.user:
			frappe.throw(_i18n("没有权限导出{0}。").format(doctype), frappe.PermissionError)


def _read_records(native_filters=None, native_or_filters=None, order_by="transaction_date desc") -> tuple[list, list, dict, list, set, bool, dict]:
	if frappe is None:
		raise RuntimeError("统一采购查询需要 Frappe 站点。")
	native_filters = json.loads(native_filters or "[]") if isinstance(native_filters, str) else native_filters
	native_or_filters = json.loads(native_or_filters or "[]") if isinstance(native_or_filters, str) else native_or_filters
	if any(value is not None and not isinstance(value, (list, dict)) for value in (native_filters, native_or_filters)):
		frappe.throw(_i18n("原生筛选条件必须为列表或对象。"))
	capabilities, warnings, records, native_fields, permissions = {}, [], {}, {}, {}
	oa_reverse_link_readable = False
	for doctype, key, requested in ((PURCHASE_ORDER, "purchase_order", PO_QUERY_FIELDS), (OA_REQUEST, "oa_request", OA_QUERY_FIELDS)):
		exists = bool(frappe.db.exists("DocType", doctype))
		if doctype == OA_REQUEST and not exists:
			# No OA DocType means there can be no reverse links to hide.
			oa_reverse_link_readable = True
		readable = exists and bool(frappe.has_permission(doctype, "read"))
		capabilities[key] = readable
		if not readable:
			warnings.append(f"{doctype} 未安装。" if not exists else f"无权查看 {doctype}。")
			records[doctype] = []
			continue
		permitted = _permitted_fields(doctype, ignore_virtual=True)
		permissions[doctype] = permitted
		if doctype == OA_REQUEST:
			oa_reverse_link_readable = "purchase_order" in permitted
		if doctype == PURCHASE_ORDER:
			native_fields = _native_projection(permitted, order_by)
			requested = (*requested, *native_fields)
		fields = [field for field in requested if field in permitted and field not in {
			"custom_purchase_source_json", "custom_cashier_payment_evidence"}]
		fields = list(dict.fromkeys(fields))
		if "name" not in fields:
			frappe.throw(_i18n("没有权限查看 {0} 单号。").format(doctype), frappe.PermissionError)
		query = {}
		if doctype == PURCHASE_ORDER and (native_filters or native_or_filters):
			query = {"filters": _native_purchase_filters(native_filters), "or_filters": _native_purchase_filters(native_or_filters)}
		result = frappe.get_list(doctype, fields=fields, limit_page_length=0, **query)
		# Keep projection explicit even if a boundary returns surplus values.
		records[doctype] = [{field: row.get(field) for field in fields} for row in result]
	if native_filters or native_or_filters:
		# Advanced PO-only constraints never silently widen to unconverted sources.
		names={o["name"] for o in records[PURCHASE_ORDER]}
		linked={o.get("custom_oa_purchase_expense") for o in records[PURCHASE_ORDER]}
		records[OA_REQUEST]=[r for r in records[OA_REQUEST] if r.get("purchase_order") in names or r["name"] in linked]
	requests = records[OA_REQUEST]
	if requests:
		permitted = permissions[OA_REQUEST]
		meta = frappe.get_meta(OA_REQUEST)
		private_fields = ["name", *[field for field in ("custom_purchase_source_json", "custom_cashier_payment_evidence", "custom_purchase_payment_reconciliation", "custom_purchase_adaptation_json") if meta.has_field(field)]]
		# Authorized OA names, not a denied source-ID field, define this private
		# read scope. This also preserves unmanaged legacy rows and older schemas.
		private = {row["name"]: row for row in frappe.db.get_values(OA_REQUEST,
			{"name": ["in", [row["name"] for row in requests]]}, private_fields, as_dict=True)} if len(private_fields) > 1 else {}
		readable = cashier_evidence_readable()
		sources = {}
		for row in requests:
			data = private.get(row["name"], {})
			source = json.loads(data["custom_purchase_source_json"]) if data.get("custom_purchase_source_json") else {}
			try:
				adoption = json.loads(data["custom_purchase_adaptation_json"]) if data.get("custom_purchase_adaptation_json") else {}
			except (TypeError, ValueError):
				adoption = {}
			# Only a private boolean leaves this read boundary, never adopted rows.
			row["_adaptation_adopted"] = bool(isinstance(adoption, dict) and source.get("version")
				and adoption.get("source_version") == source["version"] and isinstance(adoption.get("plan"), dict) and adoption["plan"])
			proof = json.loads(data["custom_cashier_payment_evidence"]) if data.get("custom_cashier_payment_evidence") else {}
			reconciliation = json.loads(data["custom_purchase_payment_reconciliation"]) if data.get("custom_purchase_payment_reconciliation") else {}
			projected = {"version": source.get("version"), "issues": source.get("issues") if {"payment_amount", "detail_total_amount", "items_json"} <= permitted else []} if source else {}
			for original, key in (("apply_date", "apply_date"), ("approval_status", "eligible"), ("oa_code", "business_id"), ("payment_amount", "requested_amount"),
				("detail_total_amount", "detail_total_amount"), ("currency", "currency"), ("custom_purchase_beneficiary_company", "beneficiary_company"),
				("custom_purchase_project", "project")):
				if original in permitted and source:
					projected[key] = source.get(key)
					if key in ("beneficiary_company", "project"):
						projected[key + "_status"] = source.get(key + "_status")
			row["_source"] = projected
			row["_proof"] = {key: proof.get(key) for key in ("paid_amount", "currency", "payment_evidence_status")} if readable else {}
			if source and readable:
				verified = bool(reconciliation.get("verified") and reconciliation.get("evidence_version") == proof["version"]) if proof.get("version") else None
				row["_reconciliation_verified"] = verified
				row["_reconciliation_warning"] = None if verified else (
					"历史付款尚未核对为 ERP 入账；不会自动补记付款" if verified is False else "历史付款证据缺少可核对版本；ERP 入账状态未知")
			sources[row["name"]] = projected
		role_rows = [row for row in requests if row.get("custom_purchase_source_id") or row["_source"]]
		roles = _project_source_roles(role_rows, sources, permitted, records[PURCHASE_ORDER]) if role_rows else {}
		for row in requests:
			if row["name"] in roles:
				row["_role_context"] = roles[row["name"]]
	if not any(capabilities.values()):
		frappe.throw(_i18n("没有权限查看采购订单或 OA 采购申请。"), frappe.PermissionError)
	codes = {_text(row.get("currency")).upper() for row in records[OA_REQUEST] if _text(row.get("currency"))}
	currency_codes = {code for code in codes if code not in RECOGNIZED_CURRENCIES and frappe.db.exists("Currency", code)}
	return records[PURCHASE_ORDER], records[OA_REQUEST], capabilities, warnings, currency_codes, oa_reverse_link_readable, native_fields


@_whitelist
@_read_request
def get_unified_purchase_list(filters=None, start=0, page_length=DEFAULT_PAGE_LENGTH,
	order_by="transaction_date desc", native_filters=None, native_or_filters=None) -> dict:
	orders, requests, capabilities, warnings, currencies, reverse_readable, native_fields = _read_records(native_filters, native_or_filters, order_by)
	payload = build_unified_purchase_payload(orders, requests, filters=filters, start=start, page_length=page_length,
		order_by=order_by, currency_codes=currencies, capabilities=capabilities, warnings=warnings,
		oa_reverse_link_readable=reverse_readable, native_fields=native_fields, progress_loader=_load_order_progress,
		company_loader=_load_company_scope, order_fields_loader=_load_order_fields_scope, include_items=True)
	POINTER = "custom_purchase_reversal_operation"
	names = [row["name"] for row in payload["rows"] if row["row_type"] == "purchase_order"]
	pending = {name for name, in frappe.db.get_values(PURCHASE_ORDER, {"name": ["in", names], POINTER: ["is", "set"]}, "name")} if names else set()
	from .purchase_payment_service import receipt_eligibility
	can_receive = bool(frappe.has_permission("Purchase Receipt", "create"))
	for row in payload["rows"]:
		if row["row_type"] == "purchase_order":
			row["receipt_eligibility"] = receipt_eligibility(row, can_create=can_receive, reversal_pending=row["name"] in pending)
			if row["name"] in pending or row.get("docstatus") == 2:
				from .purchase_reversal_progress import projection
			row["reversal"] = projection(frappe.get_doc(PURCHASE_ORDER, row["name"], for_update=True)) if row["name"] in pending or row.get("docstatus") == 2 else None
	return payload


@_whitelist
@_read_request
def export_unified_purchase_list(filters=None, columns=None, order_by="transaction_date desc", native_filters=None, native_or_filters=None) -> None:
	fields = _columns(columns)
	orders, requests, capabilities, _, currencies, reverse_readable, native_fields = _read_records(native_filters, native_or_filters, order_by)
	rows, _ = _pipeline(orders, requests, filters=filters, order_by=order_by,
		currency_codes=currencies, oa_reverse_link_readable=reverse_readable, native_fields=native_fields,
		progress_loader=_load_order_progress, full_progress=True, company_loader=_load_company_scope, order_fields_loader=_load_order_fields_scope)
	if ITEM_EXPORT_FIELDS.intersection(fields):
		_attach_progress(rows, _load_order_progress, include_items=True)
	records = {PURCHASE_ORDER: {row["name"]: row for row in orders}, OA_REQUEST: {row["name"]: row for row in requests}}
	used = defaultdict(set)
	for row in rows:
		used[PURCHASE_ORDER if row["row_type"] == "purchase_order" else OA_REQUEST].add(row["name"])
		if row["oa_references"]:
			used[OA_REQUEST].update(reference["name"] for reference in row["oa_references"])
	if not rows:
		scope = _filters(filters)["scope"]
		for doctype, key in ((PURCHASE_ORDER, "purchase_order"), (OA_REQUEST, "oa_request")):
			if capabilities[key] and (scope != "orders" or doctype == PURCHASE_ORDER):
				used[doctype] = set()
	for doctype, names in sorted(used.items()):
		_require_export_permission(doctype, (records[doctype][name] for name in sorted(names)))
	from frappe.utils.xlsxutils import make_xlsx
	from xlsxwriter import Workbook
	output = BytesIO()
	# Source text remains unchanged; prevent ws.write from interpreting it as a formula or link.
	with Workbook(output, {
		"constant_memory": True, "strings_to_formulas": False, "strings_to_urls": False,
		"default_date_format": "yyyy-mm-dd hh:mm:ss",
	}) as workbook:
		# Native Data Export mode also preserves literal HTML-looking source text.
		make_xlsx(_export_data(rows, fields), "Data Export", wb=workbook)
		ws=workbook.get_worksheet_by_name("Data Export")
		number_format=workbook.add_format({"num_format":"0.00"})
		for index,key in enumerate(fields):
			if key in (NUMERIC_FIELDS - {"docstatus"}) | EXPORT_MONEY_FIELDS: ws.set_column(index,index,None,number_format)
	frappe.response["filename"] = "采购订单.xlsx"
	frappe.response["filecontent"] = output.getvalue()
	frappe.response["type"] = "binary"
