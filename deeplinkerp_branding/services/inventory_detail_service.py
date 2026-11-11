"""四类库存明细查询、导出及未保存物料移动单准备服务。"""

from __future__ import annotations

import base64
import json
import math
import re
from collections import OrderedDict
from collections.abc import Iterable
from io import BytesIO
from itertools import chain
from tempfile import SpooledTemporaryFile
from typing import Any

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill

try:
	import frappe
except Exception:  # pragma: no cover - 本地单测环境不安装 Frappe
	frappe = None


SNAPSHOT_DOCTYPE = "Inventory Original Location Snapshot"
# The four reports share this app-owned export grant. Source read permissions
# and the permission-filtered company/item/warehouse scope are checked separately.
INVENTORY_EXPORT_PERMISSION_DOCTYPES = (SNAPSHOT_DOCTYPE,)
COUNT_UOM_TOKENS = (
	"个",
	"件",
	"套",
	"卷",
	"包",
	"张",
	"片",
	"支",
	"条",
	"台",
	"pieza",
	"conjunto",
	"rollo",
	"paquete",
	"hoja",
	"ramo",
	"barra",
	"unidad",
)
COMMON_EXPORT_COLUMNS = (1, 2, 3, 6, 7, 8, 9, 10, 11, 12)
EXPORT_HEADERS = (
	"正式物料编码",
	"物料名称（双语）",
	"仓库",
	"原始库位",
	"库位数量",
	"总库存",
	"BOM 用料状态",
	"库存单位",
	"物料组",
	"DPCI",
	"外部编码",
	"原始标识/别名",
)
CATEGORY_DEFINITIONS = {
	"material": {
		"title": "物料库存明细",
		"root_item_group": "",
		"route": "inventory-location-detail",
		"file_prefix": "material-inventory-detail",
	},
	"semi_finished": {
		"title": "半成品库存明细",
		"root_item_group": "半成品Semiterminado",
		"route": "semi-finished-inventory-detail",
		"file_prefix": "semi-finished-inventory-detail",
	},
	"finished_goods": {
		"title": "成品库存明细",
		"root_item_group": "成品Producto terminado",
		"route": "finished-goods-inventory-detail",
		"file_prefix": "finished-goods-inventory-detail",
	},
	"mold": {
		"title": "模具库存明细",
		"root_item_group": "模具Moldes",
		"route": "mold-inventory-detail",
		"file_prefix": "mold-inventory-detail",
	},
}
CATEGORY_EXPORT_HEADERS = (
	"正式物料编码",
	"物料名称（双语）",
	"仓库",
	"库位",
	"实时库存",
	"BOM 用料状态",
	"库存单位",
	"物料组",
)
COLOR_INVENTORY_CATEGORIES = frozenset({"semi_finished", "finished_goods"})
BOM_USAGE_LABELS = {
	"used": "已用于 BOM",
	"unused": "未用于 BOM",
}
BOM_USAGE_SCOPE = "all_companies"
BOM_USAGE_BATCH_SIZE = 500
ITEM_SEARCH_FIELDS = {
	"item_code": "name",
	"item_name": "item_name",
	"color": "custom_specifications",
	"dpci": "custom_dpci",
	"external_code": "custom_external_code",
	"original_identifier_alias": "custom_original_identifier_alias",
}
DEFAULT_PAGE_LENGTH = 200
MIN_PAGE_LENGTH = 1
MAX_PAGE_LENGTH = 2500
EXPORT_PAGE_LENGTH = 1000
EXPORT_STREAM_THRESHOLD = 5000
MAX_EXPORT_GROUPS = 100000
MAX_EXPORT_FILE_BYTES = 20 * 1024 * 1024
SIMPLE_STOCK_ENTRY_PURPOSES = {
	"Material Receipt",
	"Material Issue",
	"Material Transfer",
}


def _whitelist(function):
	if frappe is None:
		return function
	return frappe.whitelist()(function)


def _(message: str) -> str:
	translator = getattr(frappe, "_", None)
	return translator(message) if callable(translator) else message


def is_count_uom(stock_uom: str | None) -> bool:
	normalized = str(stock_uom or "").strip().lower()
	return any(token in normalized for token in COUNT_UOM_TOKENS)


def quantity_precision(value: Any, stock_uom: str | None) -> int:
	number = float(value or 0)
	if not is_count_uom(stock_uom):
		return 2
	return 0 if abs(number - round(number)) <= 1e-9 else 2


def format_quantity(value: Any, stock_uom: str | None) -> str:
	precision = quantity_precision(value, stock_uom)
	return f"{float(value or 0):,.{precision}f}"


def get_category_definition(category: Any) -> dict[str, str]:
	key = _text(category)
	definition = CATEGORY_DEFINITIONS.get(key)
	if not definition:
		raise ValueError(_("不支持的库存分类：{0}").format(key or _("空")))
	return dict(definition)


def _text(value: Any) -> str:
	return str(value or "").strip()


def parse_page_length(value: Any = None) -> int:
	"""Return a validated user-entered page length."""

	if value is None or (isinstance(value, str) and not value.strip()):
		return DEFAULT_PAGE_LENGTH
	if isinstance(value, bool):
		raise ValueError(_("每页行数必须是 1 到 2500 的整数。"))
	try:
		parsed = int(value)
	except (TypeError, ValueError) as error:
		raise ValueError(_("每页行数必须是 1 到 2500 的整数。")) from error
	if str(value).strip() not in {str(parsed), f"+{parsed}"}:
		raise ValueError(_("每页行数必须是 1 到 2500 的整数。"))
	if not MIN_PAGE_LENGTH <= parsed <= MAX_PAGE_LENGTH:
		raise ValueError(_("每页行数必须是 1 到 2500 的整数。"))
	return parsed


def _contains(value: Any, keyword: Any) -> bool:
	return _text(keyword).casefold() in _text(value).casefold()


def _matches_item_search(row: dict[str, Any], filters: dict[str, Any]) -> bool:
	keyword = _text(filters.get("keyword") or filters.get("item_code"))
	return (
		(not keyword or any(_contains(row.get(field), keyword) for field in ITEM_SEARCH_FIELDS))
		and _contains(row.get("item_name"), filters.get("item_name"))
		and _contains(row.get("color"), filters.get("specifications"))
	)


def _bom_usage_filter(filters: dict[str, Any]) -> str:
	status = _text(filters.get("bom_usage_status"))
	# Old browser tabs can still submit the retired four-state filter values.
	if status == "other_company":
		return "used"
	if status == "unknown":
		return status  # No BOM is unviewable under the current aggregate rule.
	if status and status not in BOM_USAGE_LABELS:
		raise ValueError(_("不支持的 BOM 用料状态。"))
	return status


def _matches_group_filters(row: dict[str, Any], filters: dict[str, Any]) -> bool:
	for fieldname in ("company", "warehouse", "item_group", "snapshot_key"):
		selected = _text(filters.get(fieldname))
		if selected and _text(row.get(fieldname)) != selected:
			return False

	snapshot_date = _text(filters.get("snapshot_date"))
	if snapshot_date and _text(row.get("snapshot_date")) != snapshot_date:
		return False

	return _matches_item_search(row, filters)


def _material_bom_candidate_codes(rows: Iterable[dict[str, Any]], filters: dict[str, Any]) -> set[str]:
	"""Limit BOM lookups to snapshot items surviving non-BOM filters."""

	if _bom_usage_filter(filters) == "unknown":
		return set()
	location = _text(filters.get("original_location"))
	return {
		_text(row.get("item_code"))
		for row in rows
		if _matches_group_filters(row, filters)
		and (not location or _contains(row.get("original_location"), location))
		and _text(row.get("item_code"))
	}


def build_inventory_location_payload(
	rows: Iterable[dict[str, Any]],
	filters: dict[str, Any] | None = None,
	*,
	bom_usage_statuses: dict[str, str] | None = None,
	start: int = 0,
	page_length: int | None = DEFAULT_PAGE_LENGTH,
) -> dict[str, Any]:
	"""按物料和仓库分组；库位筛选不改变完整仓库小计。"""

	filters = dict(filters or {})
	bom_usage_statuses = bom_usage_statuses or {}
	bom_usage_filter = _bom_usage_filter(filters)
	all_rows = [dict(row) for row in rows]
	group_rows = [row for row in all_rows if _matches_group_filters(row, filters)]
	groups: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()

	for row in sorted(
		group_rows,
		key=lambda value: (
			_text(value.get("warehouse")),
			_text(value.get("item_code")),
			_text(value.get("original_location")),
		),
	):
		key = (_text(row.get("item_code")), _text(row.get("warehouse")))
		group = groups.setdefault(
			key,
			{
				fieldname: row.get(fieldname) or ""
				for fieldname in (
					"item_code",
					"item_name",
					"warehouse",
					"stock_uom",
					"item_group",
					"dpci",
					"external_code",
					"original_identifier_alias",
				)
			}
			| {"total_qty": 0.0, "locations": []},
		)
		group["total_qty"] += float(row.get("location_qty") or 0)
		group["locations"].append(
			{
				"original_location": row.get("original_location") or "",
				"location_qty": float(row.get("location_qty") or 0),
			}
		)

	location_filter = _text(filters.get("original_location"))
	visible_groups: list[dict[str, Any]] = []
	for group in groups.values():
		bom_usage_status = bom_usage_statuses.get(group["item_code"], "unused")
		if bom_usage_filter and bom_usage_status != bom_usage_filter:
			continue
		if location_filter:
			visible_locations = [
				location
				for location in group["locations"]
				if _contains(location["original_location"], location_filter)
			]
			if not visible_locations:
				continue
			group["locations"] = visible_locations
		group["bom_usage_status"] = bom_usage_status
		visible_groups.append(group)

	total_count = len(visible_groups)
	start = _parse_start(start)
	page_groups = (
		visible_groups[start : start + page_length] if page_length is not None else visible_groups[start:]
	)
	snapshot_source = next(
		(row for row in group_rows if row.get("snapshot_key")),
		next((row for row in all_rows if row.get("snapshot_key")), {}),
	)
	return {
		"snapshot_key": snapshot_source.get("snapshot_key") or "",
		"snapshot_date": snapshot_source.get("snapshot_date") or "",
		"company": filters.get("company") or snapshot_source.get("company") or "",
		"groups": page_groups,
		"group_count": total_count,
		"total_count": total_count,
		"page_count": len(page_groups),
		"location_count": sum(len(group["locations"]) for group in page_groups),
		"start": start,
		"page_length": page_length,
		"has_previous": start > 0,
		"has_next": bool(page_length is not None and start + len(page_groups) < total_count),
	}


def _number_format(stock_uom: str | None, value: Any) -> str:
	return "#,##0" if quantity_precision(value, stock_uom) == 0 else "#,##0.00"


def build_inventory_location_xlsx(payload: dict[str, Any]) -> bytes:
	workbook = Workbook()
	sheet = workbook.active
	sheet.title = _("物料库存明细")[:31]
	sheet.freeze_panes = "A2"
	sheet.append([_(header) for header in EXPORT_HEADERS])

	header_fill = PatternFill("solid", fgColor="075985")
	for cell in sheet[1]:
		cell.fill = header_fill
		cell.font = Font(color="FFFFFF", bold=True)
		cell.alignment = Alignment(horizontal="center", vertical="center")

	current_row = 2
	for group in payload.get("groups") or []:
		locations = list(group.get("locations") or []) or [{"original_location": "", "location_qty": 0}]
		start_row = current_row
		for index, location in enumerate(locations):
			if index == 0:
				values = [
					group.get("item_code") or "",
					group.get("item_name") or "",
					group.get("warehouse") or "",
					location.get("original_location") or "",
					float(location.get("location_qty") or 0),
					float(group.get("total_qty") or 0),
					_(BOM_USAGE_LABELS[group.get("bom_usage_status", "unused")]),
					group.get("stock_uom") or "",
					group.get("item_group") or "",
					group.get("dpci") or "",
					group.get("external_code") or "",
					group.get("original_identifier_alias") or "",
				]
			else:
				values = [
					"",
					"",
					"",
					location.get("original_location") or "",
					float(location.get("location_qty") or 0),
					"",
					"",
					"",
					"",
					"",
					"",
					"",
				]
			sheet.append(values)
			sheet.cell(current_row, 5).number_format = _number_format(
				group.get("stock_uom"), location.get("location_qty")
			)
			if index == 0:
				sheet.cell(current_row, 6).number_format = _number_format(
					group.get("stock_uom"), group.get("total_qty")
				)
			current_row += 1

		end_row = current_row - 1
		if end_row > start_row:
			for column in COMMON_EXPORT_COLUMNS:
				sheet.merge_cells(
					start_row=start_row,
					start_column=column,
					end_row=end_row,
					end_column=column,
				)
		for row_number in range(start_row, end_row + 1):
			for column in range(1, len(EXPORT_HEADERS) + 1):
				sheet.cell(row_number, column).alignment = Alignment(vertical="center", wrap_text=True)

	widths = (18, 32, 24, 18, 15, 15, 17, 15, 28, 18, 20, 28)
	for index, width in enumerate(widths, start=1):
		sheet.column_dimensions[chr(64 + index)].width = width

	output = BytesIO()
	workbook.save(output)
	return output.getvalue()


def _warehouse_display_fields(row: dict[str, Any]) -> dict[str, str]:
	"""Only split leaf warehouse names that end with their actual parent warehouse label."""
	warehouse = _text(row.get("warehouse"))
	label = _text(row.get("warehouse_name"))
	parent = _text(row.get("parent_warehouse"))
	parent_label = _text(row.get("parent_warehouse_name"))
	suffix = f" {parent_label}" if parent_label else ""
	if parent and suffix and label.endswith(suffix):
		location = label[: -len(suffix)].strip()
		if re.fullmatch(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", location) and any(
			character.isdigit() for character in location
		):
			return {"display_warehouse": parent, "warehouse_location": location}
	return {"display_warehouse": warehouse, "warehouse_location": ""}


def _category_group_metadata(row: dict[str, Any]) -> dict[str, Any]:
	return {
		fieldname: row.get(fieldname) or ""
		for fieldname in (
			"item_code",
			"item_name",
			"color",
			"company",
			"warehouse",
			"stock_uom",
			"item_group",
			"dpci",
			"external_code",
			"original_identifier_alias",
		)
	} | _warehouse_display_fields(row) | {
		"actual_qty": float(row.get("actual_qty") or 0), "_snapshot_locations": []
	}


def _truthy(value: Any) -> bool:
	return _text(value).casefold() in {"1", "true", "yes", "on"}


def _parse_start(value: Any) -> int:
	try:
		return max(0, int(value or 0))
	except (TypeError, ValueError):
		return 0


def _matches_category_warehouse(group: dict[str, Any], filters: dict[str, Any]) -> bool:
	scope = filters.get("_warehouse_scope")
	if scope is not None:
		return _text(group.get("warehouse")) in scope
	warehouse = _text(filters.get("warehouse"))
	return not warehouse or _text(group.get("warehouse")) == warehouse


def _category_payload_filters(filters: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
	return {
		**filters,
		"_warehouse_scope": frozenset(context["warehouses"]) if context.get("warehouse_is_group") else None,
	}


def _matches_category_filters(group: dict[str, Any], filters: dict[str, Any]) -> bool:
	if not _matches_category_warehouse(group, filters):
		return False
	if _truthy(filters.get("only_with_stock")) and abs(float(group.get("actual_qty") or 0)) <= 1e-9:
		return False
	return _matches_item_search(group, filters)


def _category_bom_candidate_codes(
	stock_rows: list[dict[str, Any]], snapshot_rows: list[dict[str, Any]], filters: dict[str, Any]
) -> set[str]:
	"""Keep item codes from groups surviving warehouse, stock, keyword and location filters."""

	if _bom_usage_filter(filters) == "unknown":
		return set()
	item_rows: dict[str, dict[str, Any]] = {}
	codes: set[str] = set()
	for row in stock_rows:
		item_code = _text(row.get("item_code"))
		if not item_code:
			continue
		item_rows.setdefault(item_code, row)
		if _matches_category_filters(row, filters):
			codes.add(item_code)
	if not _truthy(filters.get("only_with_stock")):
		for row in snapshot_rows:
			item_code = _text(row.get("item_code"))
			base = item_rows.get(item_code)
			if base and _matches_category_filters(
				{**base, "warehouse": row.get("warehouse"), "actual_qty": 0}, filters
			):
				codes.add(item_code)
	location = _text(filters.get("reference_location") or filters.get("original_location"))
	if location:
		codes.intersection_update(
			_text(row.get("item_code"))
			for row in snapshot_rows
			if _matches_category_warehouse(row, filters)
			and _contains(row.get("original_location") or "库位待维护", location)
		)
	return codes


def build_categorized_inventory_payload(
	stock_rows: Iterable[dict[str, Any]],
	snapshot_rows: Iterable[dict[str, Any]],
	*,
	category: str,
	filters: dict[str, Any] | None = None,
	bom_usage_statuses: dict[str, str] | None = None,
	start: int = 0,
	page_length: int | None = DEFAULT_PAGE_LENGTH,
) -> dict[str, Any]:
	"""合并实时 Bin 与最新库位快照；快照只作参考，不覆盖实时库存。"""

	definition = get_category_definition(category)
	filters = dict(filters or {})
	bom_usage_statuses = bom_usage_statuses or {}
	bom_usage_filter = _bom_usage_filter(filters)
	groups: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
	item_metadata: dict[str, dict[str, Any]] = {}

	for source in stock_rows:
		row = dict(source)
		item_code = _text(row.get("item_code"))
		if not item_code:
			continue
		key = (item_code, _text(row.get("warehouse")))
		group = _category_group_metadata(row)
		groups[key] = group
		item_metadata.setdefault(item_code, group)

	snapshot_source: dict[str, Any] = {}
	for source in sorted(
		(dict(row) for row in snapshot_rows),
		key=lambda value: (
			_text(value.get("item_code")),
			_text(value.get("warehouse")),
			_text(value.get("original_location")),
		),
	):
		item_code = _text(source.get("item_code"))
		if not item_code or item_code not in item_metadata:
			continue
		warehouse = _text(source.get("warehouse"))
		key = (item_code, warehouse)
		if key not in groups:
			metadata = dict(item_metadata[item_code])
			metadata["warehouse"] = warehouse
			metadata["display_warehouse"] = warehouse
			metadata["warehouse_location"] = ""
			metadata["actual_qty"] = 0.0
			metadata["_snapshot_locations"] = []
			groups[key] = metadata
		groups[key]["_snapshot_locations"].append(
			{
				"reference_location": source.get("original_location") or "库位待维护",
				"snapshot_location_qty": float(source.get("location_qty") or 0),
				"snapshot_date": source.get("snapshot_date") or "",
			}
		)
		if source.get("snapshot_key"):
			snapshot_source = source

	# Item 的空仓库行只是“从未产生 Bin”的占位；一旦快照提供仓库就不再保留占位行。
	item_has_warehouse = {item_code for item_code, warehouse in groups if warehouse}
	for key in list(groups):
		if not key[1] and key[0] in item_has_warehouse:
			del groups[key]

	location_filter = _text(filters.get("reference_location") or filters.get("original_location"))
	visible: list[dict[str, Any]] = []
	for group in groups.values():
		if not _matches_category_filters(group, filters):
			continue
		bom_usage_status = bom_usage_statuses.get(group["item_code"], "unused")
		if bom_usage_filter and bom_usage_status != bom_usage_filter:
			continue
		group["bom_usage_status"] = bom_usage_status
		all_locations = list(group.pop("_snapshot_locations", []))
		snapshot_qty = (
			sum(float(location.get("snapshot_location_qty") or 0) for location in all_locations)
			if all_locations
			else None
		)
		if location_filter:
			locations = [
				location
				for location in all_locations
				if _contains(location.get("reference_location"), location_filter)
			]
			if not locations:
				continue
		else:
			locations = all_locations

		actual_qty = float(group.get("actual_qty") or 0)
		all_reference_locations_valid = all(
			_text(location.get("reference_location")) not in {"", "库位待维护"} for location in all_locations
		)
		difference_qty = actual_qty - snapshot_qty if snapshot_qty is not None else None
		if not all_locations:
			inventory_status = "无参考快照"
		elif not all_reference_locations_valid:
			inventory_status = "库位待维护"
		elif abs(float(difference_qty or 0)) <= 1e-9:
			inventory_status = "库存一致"
		else:
			inventory_status = "库存差异"
		group.update(
			{
				"snapshot_qty": snapshot_qty,
				"difference_qty": difference_qty,
				"inventory_status": inventory_status,
				"locations": locations
				or [
					{
						"reference_location": "无参考快照",
						"snapshot_location_qty": None,
						"snapshot_date": "",
					}
				],
			}
		)
		visible.append(group)

	visible.sort(
		key=lambda row: (
			abs(float(row.get("actual_qty") or 0)) <= 1e-9,
			_text(row.get("item_code")),
			_text(row.get("warehouse")),
		)
	)
	total_count = len(visible)
	start = _parse_start(start)
	page_groups = visible[start : start + page_length] if page_length is not None else visible[start:]
	return {
		"category": category,
		"title": definition["title"],
		"root_item_group": definition["root_item_group"],
		"snapshot_key": snapshot_source.get("snapshot_key") or "",
		"snapshot_date": snapshot_source.get("snapshot_date") or "",
		"groups": page_groups,
		"total_count": total_count,
		"page_count": len(page_groups),
		"location_count": sum(len(row["locations"]) for row in page_groups),
		"start": start,
		"page_length": page_length,
		"has_previous": start > 0,
		"has_next": bool(page_length is not None and start + len(page_groups) < total_count),
	}


def _attach_display_item_groups(payload: dict[str, Any], category: str) -> None:
	"""Display the product branch directly below the category root, retaining the actual group."""
	groups = payload.get("groups") or []
	for group in groups:
		group["display_item_group"] = _text(group.get("item_group"))
	root = get_category_definition(category)["root_item_group"]
	names = tuple(sorted({_text(group.get("item_group")) for group in groups} - {""}))
	if not root or not names or frappe is None:
		return
	source_groups = frappe.get_list(
		"Item Group", filters={"name": ["in", names]},
		fields=["name", "lft", "rgt"], limit_page_length=0,
	)
	if not source_groups:
		return
	products = frappe.get_list(
		"Item Group", filters={"parent_item_group": root},
		fields=["name", "lft", "rgt"], order_by="lft", limit_page_length=0,
	)
	display_names = {}
	for source in source_groups:
		for product in products:
			if product["lft"] <= source["lft"] and product["rgt"] >= source["rgt"]:
				display_names[source["name"]] = product["name"]
				break
	for group in groups:
		group["display_item_group"] = display_names.get(group.get("item_group"), group["display_item_group"])


def _category_export_headers(category: str = "", *, all_companies: bool = False) -> tuple[str, ...]:
	if category in COLOR_INVENTORY_CATEGORIES:
		headers = (*CATEGORY_EXPORT_HEADERS[:2], "颜色", CATEGORY_EXPORT_HEADERS[-1], *CATEGORY_EXPORT_HEADERS[2:-1])
	else:
		headers = CATEGORY_EXPORT_HEADERS
	if all_companies:
		position = 4 if category in COLOR_INVENTORY_CATEGORIES else 2
		headers = (*headers[:position], "公司", *headers[position:])
	return headers


def _category_export_values(
	group: dict[str, Any], *, include_color: bool = False, all_companies: bool = False
) -> list[Any]:
	values = [
		group.get("item_code") or "",
		group.get("item_name") or "",
		group.get("display_warehouse") or group.get("warehouse") or "",
		group.get("warehouse_location") or "",
		float(group.get("actual_qty") or 0),
		_(BOM_USAGE_LABELS[group.get("bom_usage_status", "unused")]),
		group.get("stock_uom") or "",
		group.get("display_item_group") or group.get("item_group") or "",
	]
	if include_color:
		item_group = values.pop()
		values[2:2] = [group.get("color") or "", item_group]
	if all_companies:
		values.insert(4 if include_color else 2, group.get("company") or "")
	return values


def build_categorized_inventory_xlsx(payload: dict[str, Any]) -> bytes:
	category = payload.get("category") or ""
	include_color = category in COLOR_INVENTORY_CATEGORIES
	all_companies = bool(payload.get("all_companies"))
	headers = _category_export_headers(category, all_companies=all_companies)
	workbook = Workbook()
	sheet = workbook.active
	sheet.title = _(_text(payload.get("title")) or "分类库存明细")[:31]
	sheet.freeze_panes = "A2"
	sheet.append([_(header) for header in headers])

	header_fill = PatternFill("solid", fgColor="075985")
	for cell in sheet[1]:
		cell.fill = header_fill
		cell.font = Font(color="FFFFFF", bold=True)
		cell.alignment = Alignment(horizontal="center", vertical="center")

	for row_number, group in enumerate(payload.get("groups") or [], start=2):
		sheet.append(_category_export_values(group, include_color=include_color, all_companies=all_companies))
		sheet.cell(row_number, (7 if include_color else 5) + int(all_companies)).number_format = _number_format(group.get("stock_uom"), group.get("actual_qty"))
		for column in range(1, len(headers) + 1):
			sheet.cell(row_number, column).alignment = Alignment(vertical="center", wrap_text=True)

	widths = (18, 32, 24, 16, 15, 17, 15, 28)
	if include_color:
		widths = (*widths[:2], 16, widths[-1], *widths[2:-1])
	if all_companies:
		position = 4 if include_color else 2
		widths = (*widths[:position], 24, *widths[position:])
	for index, width in enumerate(widths, start=1):
		sheet.column_dimensions[chr(64 + index)].width = width

	output = BytesIO()
	workbook.save(output)
	return output.getvalue()


def build_categorized_inventory_xlsx_streaming(
	title: str, groups: Iterable[dict[str, Any]], *, category: str = "", all_companies: bool = False
) -> bytes:
	"""Write large exports without retaining every worksheet cell in memory."""
	include_color = category in COLOR_INVENTORY_CATEGORIES
	workbook = Workbook(write_only=True)
	sheet = workbook.create_sheet(title=_(_text(title) or "分类库存明细")[:31])
	sheet.freeze_panes = "A2"
	widths = (18, 32, 24, 16, 15, 17, 15, 28)
	if include_color:
		widths = (*widths[:2], 16, widths[-1], *widths[2:-1])
	if all_companies:
		position = 4 if include_color else 2
		widths = (*widths[:position], 24, *widths[position:])
	for index, width in enumerate(widths, start=1):
		sheet.column_dimensions[chr(64 + index)].width = width
	header_fill = PatternFill("solid", fgColor="075985")
	header = []
	for label in _category_export_headers(category, all_companies=all_companies):
		cell = WriteOnlyCell(sheet, value=_(label))
		cell.fill = header_fill
		cell.font = Font(color="FFFFFF", bold=True)
		cell.alignment = Alignment(horizontal="center", vertical="center")
		header.append(cell)
	sheet.append(header)
	for group in groups:
		cells = [WriteOnlyCell(sheet, value=value) for value in _category_export_values(group, include_color=include_color, all_companies=all_companies)]
		for cell in cells:
			cell.alignment = Alignment(vertical="center", wrap_text=True)
		cells[(6 if include_color else 4) + int(all_companies)].number_format = _number_format(group.get("stock_uom"), group.get("actual_qty"))
		sheet.append(cells)
	with SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as output:
		workbook.save(output)
		if output.tell() > MAX_EXPORT_FILE_BYTES:
			raise ValueError(_("导出文件超过 {0} MB，请增加筛选条件。").format(MAX_EXPORT_FILE_BYTES // (1024 * 1024)))
		output.seek(0)
		return output.read()


def _parse_filters(filters: Any = None, **kwargs: Any) -> dict[str, Any]:
	if isinstance(filters, str):
		filters = json.loads(filters or "{}")
	parsed = dict(filters or {})
	parsed.update({key: value for key, value in kwargs.items() if value is not None})
	return parsed


def _require_read_permission() -> None:
	if frappe is None:
		return
	if not frappe.has_permission(SNAPSHOT_DOCTYPE, "read"):
		frappe.throw(_("没有权限查看库存库位快照。"), frappe.PermissionError)


def _require_categorized_inventory_read_permission() -> None:
	if frappe is None:
		return
	_require_read_permission()
	for doctype in ("Item", "Bin", "Warehouse"):
		if not frappe.has_permission(doctype, "read"):
			frappe.throw(_("没有权限查看{0}。").format(doctype), frappe.PermissionError)


def _can_export_inventory(doctypes: tuple[str, ...]) -> bool:
	if frappe is None:
		return True
	can_export = getattr(getattr(frappe, "permissions", None), "can_export", None)
	return callable(can_export) and all(can_export(doctype) for doctype in doctypes)


def _require_inventory_export_permission(doctypes: tuple[str, ...]) -> None:
	if not _can_export_inventory(doctypes):
		frappe.throw(_("没有导出库存明细的权限。"), frappe.PermissionError)


def _require_company_permission(company: str) -> None:
	"""Raw SQL 读取前仍须遵守用户对 Company 的文档权限。"""

	if frappe is None:
		return
	frappe.get_doc("Company", company).check_permission("read")


def _resolve_company(company: Any = None) -> str:
	"""Use an explicit company or the current user's Frappe default."""

	resolved = _text(company)
	if resolved:
		return resolved
	if frappe is None:
		raise ValueError(_("请先选择公司或设置默认公司。"))
	resolved = _text(frappe.defaults.get_user_default("Company"))
	if not resolved:
		frappe.throw(_("请先选择公司或设置默认公司。"), frappe.ValidationError)
	return resolved


def _accessible_items() -> tuple[str, ...]:
	rows = frappe.get_list(
		"Item",
		fields=["name"],
		limit_page_length=0,
	)
	return tuple(
		_text(row.get("name") if isinstance(row, dict) else getattr(row, "name", row))
		for row in rows
		if _text(row.get("name") if isinstance(row, dict) else getattr(row, "name", row))
	)


def _accessible_category_items(item_groups: list[str]) -> tuple[str, ...]:
	"""Ask Frappe to apply Item permissions within the selected category only."""
	if not item_groups:
		return ()
	rows = frappe.get_list(
		"Item",
		filters={"disabled": 0, "is_stock_item": 1, "item_group": ["in", item_groups]},
		fields=["name"],
		limit_page_length=0,
	)
	return tuple(_text(row.get("name")) for row in rows if _text(row.get("name")))


def _load_bom_usage_statuses(item_codes: Iterable[str]) -> dict[str, str]:
	"""Check direct components in active submitted BOMs for already authorized inventory items.

	Only item codes scoped by the inventory query enter this aggregate check. BOM
	identities, companies and child rows are never returned to the page.
	"""

	codes = tuple(sorted({_text(code) for code in item_codes if _text(code)}))
	if not codes:
		return {}
	if frappe is None:
		return dict.fromkeys(codes, "unused")

	used: set[str] = set()
	for offset in range(0, len(codes), BOM_USAGE_BATCH_SIZE):
		batch = codes[offset : offset + BOM_USAGE_BATCH_SIZE]
		rows = frappe.db.sql(
			"""
			SELECT DISTINCT component.item_code
			FROM `tabBOM Item` component
			INNER JOIN `tabBOM` bom ON bom.name = component.parent
			WHERE component.parenttype = 'BOM'
			  AND component.parentfield = 'items'
			  AND bom.docstatus = 1
			  AND bom.is_active = 1
			  AND component.item_code IN %(item_codes)s
			""",
			{"item_codes": batch},
			as_dict=True,
		)
		for row in rows:
			item_code = _text(row.get("item_code"))
			if item_code in batch:
				used.add(item_code)
	return {code: "used" if code in used else "unused" for code in codes}


def _attach_page_bom_usage(payload: dict[str, Any]) -> None:
	"""Resolve BOM usage only for displayed groups when no BOM status filter is active."""

	groups = payload.get("groups") or []
	statuses = _load_bom_usage_statuses(group["item_code"] for group in groups)
	for group in groups:
		group["bom_usage_status"] = statuses.get(group["item_code"], "unused")


def _latest_snapshot_key(company: str) -> str:
	rows = frappe.db.sql(
		f"""
        SELECT snapshot_key
        FROM `tab{SNAPSHOT_DOCTYPE}`
        WHERE company = %(company)s
        ORDER BY snapshot_date DESC, creation DESC
        LIMIT 1
        """,
		{"company": company},
		as_dict=True,
	)
	return rows[0]["snapshot_key"] if rows else ""


def _load_snapshot_rows(filters: dict[str, Any]) -> list[dict[str, Any]]:
	company = _resolve_company(filters.get("company"))
	snapshot_key = _text(filters.get("snapshot_key")) or _latest_snapshot_key(company)
	if not snapshot_key:
		return []
	items = _accessible_items()
	warehouses = _accessible_warehouses(company)
	selected_warehouse = _text(filters.get("warehouse"))
	if selected_warehouse and selected_warehouse not in warehouses:
		frappe.throw(_("没有权限查看所选仓库。"), frappe.PermissionError)
	if not items or not warehouses:
		return []
	filters["company"] = company
	filters["snapshot_key"] = snapshot_key
	color_value = "COALESCE(item.custom_specifications, '')" if _inventory_color_field("material") else "''"
	return frappe.db.sql(
		f"""
        SELECT
            snapshot.snapshot_key,
            snapshot.snapshot_date,
            snapshot.company,
            snapshot.item_code,
            item.item_name,
            {color_value} AS color,
            snapshot.warehouse,
            snapshot.original_location,
            snapshot.location_qty,
            snapshot.stock_uom,
            item.item_group,
            COALESCE(item.custom_dpci, '') AS dpci,
            COALESCE(item.custom_external_code, '') AS external_code,
            COALESCE(item.custom_original_identifier_alias, '') AS original_identifier_alias
        FROM `tab{SNAPSHOT_DOCTYPE}` snapshot
        INNER JOIN `tabItem` item ON item.name = snapshot.item_code
        WHERE snapshot.snapshot_key = %(snapshot_key)s
          AND snapshot.company = %(company)s
		  AND snapshot.item_code IN %(items)s
		  AND snapshot.warehouse IN %(warehouses)s
        ORDER BY snapshot.warehouse, snapshot.item_code, snapshot.original_location
        """,
		{
			"snapshot_key": snapshot_key,
			"company": company,
			"items": items,
			"warehouses": warehouses,
		},
		as_dict=True,
	)


def _list_snapshot_options(company: str) -> list[dict[str, Any]]:
	return frappe.db.sql(
		f"""
        SELECT snapshot_key, MAX(snapshot_date) AS snapshot_date
        FROM `tab{SNAPSHOT_DOCTYPE}`
        WHERE company = %(company)s
        GROUP BY snapshot_key
        ORDER BY snapshot_date DESC, snapshot_key DESC
        """,
		{"company": company},
		as_dict=True,
	)


def _category_group_bounds(category: str, selected_group: str = "") -> tuple[int, int] | None:
	definition = get_category_definition(category)
	root_name = definition["root_item_group"]
	if category == "material":
		if not frappe.has_permission("Item Group", "read"):
			frappe.throw(_("没有权限查看{0}。").format("Item Group"), frappe.PermissionError)
		if not selected_group:
			return 0, 2**31 - 1
		selected = frappe.db.get_value("Item Group", selected_group, ["lft", "rgt"], as_dict=True)
		if not selected:
			raise ValueError(_("物料组不存在：{0}").format(selected_group))
		frappe.get_doc("Item Group", selected_group).check_permission("read")
		return int(selected["lft"]), int(selected["rgt"])
	root = frappe.db.get_value("Item Group", root_name, ["lft", "rgt"], as_dict=True)
	if not root:
		return None
	frappe.get_doc("Item Group", root_name).check_permission("read")
	selected_group = _text(selected_group)
	if not selected_group:
		return int(root["lft"]), int(root["rgt"])

	selected = frappe.db.get_value("Item Group", selected_group, ["lft", "rgt"], as_dict=True)
	if not selected:
		raise ValueError(_("物料组不存在：{0}").format(selected_group))
	if int(selected["lft"]) < int(root["lft"]) or int(selected["rgt"]) > int(root["rgt"]):
		raise ValueError(_("物料组不属于{0}：{1}").format(root_name, selected_group))
	frappe.get_doc("Item Group", selected_group).check_permission("read")
	return int(selected["lft"]), int(selected["rgt"])


def _accessible_warehouses(company: str) -> tuple[str, ...]:
	rows = frappe.get_list(
		"Warehouse",
		filters={"company": company, "is_group": 0},
		fields=["name"],
		limit_page_length=0,
	)
	return tuple(
		_text(row.get("name") if isinstance(row, dict) else getattr(row, "name", row))
		for row in rows
		if _text(row.get("name") if isinstance(row, dict) else getattr(row, "name", row))
	)


def _accessible_inventory_companies_and_warehouses() -> tuple[tuple[str, ...], dict[str, str]]:
	companies = tuple(row["name"] for row in frappe.get_list("Company", fields=["name"], limit_page_length=0))
	if not companies:
		return (), {}
	rows = frappe.get_list(
		"Warehouse", filters={"company": ["in", companies], "is_group": 0},
		fields=["name", "company"], limit_page_length=0,
	)
	return companies, {row["name"]: row["company"] for row in rows if row["company"] in companies}


def _category_warehouse_scope(
	company: str, warehouses: tuple[str, ...], selected: str, *, allowed_companies: tuple[str, ...] | None = None
) -> tuple[tuple[str, ...], bool]:
	if not selected or selected in warehouses:
		return warehouses, False
	warehouse = frappe.get_doc("Warehouse", selected)
	warehouse.check_permission("read")
	company_allowed = warehouse.company in allowed_companies if allowed_companies is not None else warehouse.company == company
	if not company_allowed or not int(warehouse.is_group or 0):
		frappe.throw(_("没有权限查看所选仓库。"), frappe.PermissionError)
	descendants = frappe.get_list(
		"Warehouse",
		filters={
			"company": warehouse.company, "is_group": 0,
			"lft": [">", warehouse.lft], "rgt": ["<", warehouse.rgt],
		},
		fields=["name"], limit_page_length=0,
	)
	allowed = set(warehouses)
	return tuple(row["name"] for row in descendants if row["name"] in allowed), True


def _inventory_color_field(category: str) -> str:
	if category not in CATEGORY_DEFINITIONS or frappe is None:
		return ""
	meta = frappe.get_meta("Item")
	fieldname = "custom_specifications"
	if meta.has_field(fieldname) and fieldname in meta.get_permitted_fieldnames(permission_type="read"):
		return fieldname
	return ""


def _category_query_context(category: str, filters: dict[str, Any]) -> dict[str, Any]:
	company = _resolve_company(filters.get("company"))
	all_companies = _truthy(filters.get("all_companies"))
	bounds = _category_group_bounds(category, _text(filters.get("item_group")))
	if bounds is None:
		return {
			"company": company,
			"missing_item_group": get_category_definition(category)["root_item_group"],
			"warehouses": (),
			"snapshot_key": "",
			"all_companies": all_companies,
		}
	group_lft, group_rgt = bounds
	excluded_groups: tuple[str, ...] = ()
	if category == "material":
		roots = [
			frappe.db.get_value("Item Group", definition["root_item_group"], ["lft", "rgt"], as_dict=True)
			for key, definition in CATEGORY_DEFINITIONS.items() if key != "material"
		]
		if any(root and group_lft >= int(root["lft"]) and group_rgt <= int(root["rgt"]) for root in roots):
			raise ValueError(_("所选物料组不属于物料库存明细。"))
		active_roots = [(int(root["lft"]), int(root["rgt"])) for root in roots if root]
		if active_roots:
			clauses = [f"(child.lft >= %(ex_lft_{index})s AND child.rgt <= %(ex_rgt_{index})s)" for index in range(len(active_roots))]
			values = {
				field: bound
				for index, (lft, rgt) in enumerate(active_roots)
				for field, bound in ((f"ex_lft_{index}", lft), (f"ex_rgt_{index}", rgt))
			}
			excluded_groups = tuple(
				row["name"] for row in frappe.db.sql(
					"SELECT child.name FROM `tabItem Group` child WHERE " + " OR ".join(clauses),
					values, as_dict=True,
				)
			)
	companies = (company,)
	if all_companies:
		companies, warehouse_companies = _accessible_inventory_companies_and_warehouses()
		warehouses = tuple(warehouse_companies)
	else:
		warehouses = _accessible_warehouses(company)
	group_options = _list_category_item_groups({
		"group_lft": group_lft, "group_rgt": group_rgt, "excluded_groups": excluded_groups,
	})
	items = _accessible_category_items(group_options)
	selected_warehouse = _text(filters.get("warehouse"))
	if all_companies:
		warehouses, warehouse_is_group = _category_warehouse_scope(
			company, warehouses, selected_warehouse, allowed_companies=companies
		)
	else:
		warehouses, warehouse_is_group = _category_warehouse_scope(company, warehouses, selected_warehouse)
	return {
		"company": company,
		"companies": companies,
		"all_companies": all_companies,
		"group_lft": group_lft,
		"group_rgt": group_rgt,
		"warehouses": warehouses,
		"warehouse_is_group": warehouse_is_group,
		"items": items,
		"snapshot_key": "" if all_companies else _text(filters.get("snapshot_key")) or _latest_snapshot_key(company),
		"category": category,
		"color_field": _inventory_color_field(category),
		"excluded_groups": excluded_groups,
		"item_group_options": group_options,
	}


def _load_category_stock_rows(context: dict[str, Any]) -> list[dict[str, Any]]:
	warehouses = context["warehouses"]
	items = context["items"]
	excluded_clause = "AND item.item_group NOT IN %(excluded_groups)s" if context.get("excluded_groups") else ""
	color_value = "COALESCE(item.custom_specifications, '')" if context.get("color_field") == "custom_specifications" else "''"
	warehouse_company_clause = "warehouse.company IN %(companies)s" if context.get("all_companies") else "warehouse.company = %(company)s"
	if not items or (context.get("all_companies") and not warehouses):
		return []
	if warehouses:
		stock_join = f"""
        LEFT JOIN (
            SELECT bin.item_code, bin.warehouse, bin.actual_qty, warehouse.company
            FROM `tabBin` bin
            INNER JOIN `tabWarehouse` warehouse ON warehouse.name = bin.warehouse
            WHERE {warehouse_company_clause}
              AND warehouse.is_group = 0
              AND warehouse.name IN %(warehouses)s
        ) stock ON stock.item_code = item.name
        """
	else:
		stock_join = """
        LEFT JOIN (
            SELECT NULL AS item_code, NULL AS warehouse, 0 AS actual_qty, NULL AS company
            WHERE 1 = 0
        ) stock ON stock.item_code = item.name
        """
	return frappe.db.sql(
		f"""
        SELECT
            item.name AS item_code,
            item.item_name,
            {color_value} AS color,
            COALESCE(stock.company, '') AS company,
            COALESCE(stock.warehouse, '') AS warehouse,
            COALESCE(stock.actual_qty, 0) AS actual_qty,
            item.stock_uom,
            item.item_group,
            COALESCE(item.custom_dpci, '') AS dpci,
            COALESCE(item.custom_external_code, '') AS external_code,
            COALESCE(item.custom_original_identifier_alias, '') AS original_identifier_alias
        FROM `tabItem` item
        INNER JOIN `tabItem Group` item_group ON item_group.name = item.item_group
        {stock_join}
        WHERE item.disabled = 0
          AND item.is_stock_item = 1
		  AND item.name IN %(items)s
          AND item_group.lft >= %(group_lft)s
          AND item_group.rgt <= %(group_rgt)s
		  {excluded_clause}
        ORDER BY item.name, stock.warehouse
        """,
		{
			"company": context["company"],
			"companies": context.get("companies") or (context["company"],),
			"warehouses": warehouses or ("",),
			"items": items,
			"group_lft": context["group_lft"],
			"group_rgt": context["group_rgt"],
			"excluded_groups": context.get("excluded_groups") or ("",),
		},
		as_dict=True,
	)


def _load_category_snapshot_rows(context: dict[str, Any]) -> list[dict[str, Any]]:
	if not context["snapshot_key"] or not context["warehouses"] or not context["items"]:
		return []
	excluded_clause = "AND item.item_group NOT IN %(excluded_groups)s" if context.get("excluded_groups") else ""
	color_value = "COALESCE(item.custom_specifications, '')" if context.get("color_field") == "custom_specifications" else "''"
	return frappe.db.sql(
		f"""
        SELECT
            snapshot.snapshot_key,
            snapshot.snapshot_date,
            snapshot.item_code,
            item.item_name,
            {color_value} AS color,
            snapshot.warehouse,
            snapshot.original_location,
            snapshot.location_qty,
            snapshot.stock_uom,
            item.item_group,
            COALESCE(item.custom_dpci, '') AS dpci,
            COALESCE(item.custom_external_code, '') AS external_code,
            COALESCE(item.custom_original_identifier_alias, '') AS original_identifier_alias
        FROM `tab{SNAPSHOT_DOCTYPE}` snapshot
        INNER JOIN `tabItem` item ON item.name = snapshot.item_code
        INNER JOIN `tabItem Group` item_group ON item_group.name = item.item_group
        WHERE snapshot.snapshot_key = %(snapshot_key)s
          AND snapshot.company = %(company)s
          AND snapshot.warehouse IN %(warehouses)s
		  AND snapshot.item_code IN %(items)s
          AND item_group.lft >= %(group_lft)s
          AND item_group.rgt <= %(group_rgt)s
		  {excluded_clause}
        ORDER BY snapshot.item_code, snapshot.warehouse, snapshot.original_location
        """,
		{
			"snapshot_key": context["snapshot_key"],
			"company": context["company"],
			"warehouses": context["warehouses"],
			"items": context["items"],
			"group_lft": context["group_lft"],
			"group_rgt": context["group_rgt"],
			"excluded_groups": context.get("excluded_groups") or ("",),
		},
		as_dict=True,
	)


def _list_category_item_groups(context: dict[str, Any]) -> list[str]:
	filters = {
		"lft": [">=", context["group_lft"]],
		"rgt": ["<=", context["group_rgt"]],
	}
	if context.get("excluded_groups"):
		filters["name"] = ["not in", context["excluded_groups"]]
	rows = frappe.get_list(
		"Item Group", filters=filters, fields=["name"], order_by="lft", limit_page_length=0,
	)
	return [_text(row.get("name")) for row in rows if _text(row.get("name"))]


def _paged_category_candidates(
	context: dict[str, Any], filters: dict[str, Any], known_total: int | None = None
) -> tuple[list[dict[str, Any]], int]:
	"""Count and page authorized item/warehouse groups in SQL before loading their locations."""
	items = context.get("items") or ()
	if not items or _bom_usage_filter(filters) == "unknown":
		return [], 0
	if (context.get("warehouse_is_group") or context.get("all_companies")) and not context["warehouses"]:
		return [], 0
	if _text(filters.get("reference_location") or filters.get("original_location")) and not context["snapshot_key"]:
		return [], 0
	if _text(filters.get("specifications")) and not context.get("color_field"):
		return [], 0

	params = {
		"company": context["company"],
		"companies": context.get("companies") or (context["company"],),
		"snapshot_key": context["snapshot_key"],
		"items": items,
		"warehouses": context["warehouses"] or ("",),
		"group_lft": context["group_lft"],
		"group_rgt": context["group_rgt"],
		"excluded_groups": context.get("excluded_groups") or ("",),
		"warehouse": _text(filters.get("warehouse")),
		"keyword": _text(filters.get("keyword") or filters.get("item_code")),
		"item_name": _text(filters.get("item_name")),
		"specifications": _text(filters.get("specifications")),
		"reference_location": _text(filters.get("reference_location") or filters.get("original_location")),
	}
	excluded = "AND item.item_group NOT IN %(excluded_groups)s" if context.get("excluded_groups") else ""
	group_conditions = f"""
		item.disabled = 0 AND item.is_stock_item = 1 AND item.name IN %(items)s
		AND item_group.lft >= %(group_lft)s AND item_group.rgt <= %(group_rgt)s
		{excluded}
	"""
	bin_company_scope = """
		AND EXISTS (SELECT 1 FROM `tabWarehouse` stock_owner
			WHERE stock_owner.name = bin.warehouse AND stock_owner.is_group = 0
			AND stock_owner.company IN %(companies)s)
	""" if context.get("all_companies") else ""
	only_stock = _truthy(filters.get("only_with_stock"))
	if only_stock:
		base = f"""
			SELECT item.name AS item_code, bin.warehouse, bin.actual_qty
			FROM `tabItem` item
			INNER JOIN `tabItem Group` item_group ON item_group.name = item.item_group
			INNER JOIN `tabBin` bin ON bin.item_code = item.name AND bin.warehouse IN %(warehouses)s {bin_company_scope}
			WHERE {group_conditions} AND ABS(bin.actual_qty) > 0.000000001
		"""
	else:
		no_snapshot_pair = ""
		if context["snapshot_key"] and context["warehouses"]:
			no_snapshot_pair = """
				AND (bin.warehouse IS NOT NULL OR NOT EXISTS (
					SELECT 1 FROM `tabInventory Original Location Snapshot` existing
					WHERE existing.company = %(company)s AND existing.snapshot_key = %(snapshot_key)s
					AND existing.item_code = item.name AND existing.warehouse IN %(warehouses)s
				))
			"""
		base = f"""
			SELECT item.name AS item_code, COALESCE(bin.warehouse, '') AS warehouse,
				COALESCE(bin.actual_qty, 0) AS actual_qty
			FROM `tabItem` item
			INNER JOIN `tabItem Group` item_group ON item_group.name = item.item_group
			LEFT JOIN `tabBin` bin ON bin.item_code = item.name AND bin.warehouse IN %(warehouses)s {bin_company_scope}
			WHERE {group_conditions} {no_snapshot_pair}
		"""
		if context["snapshot_key"] and context["warehouses"]:
			base += f"""
				UNION ALL
				SELECT DISTINCT snapshot.item_code, snapshot.warehouse, 0 AS actual_qty
				FROM `tabInventory Original Location Snapshot` snapshot
				INNER JOIN `tabItem` item ON item.name = snapshot.item_code
				INNER JOIN `tabItem Group` item_group ON item_group.name = item.item_group
				WHERE {group_conditions}
				AND snapshot.company = %(company)s AND snapshot.snapshot_key = %(snapshot_key)s
				AND snapshot.warehouse IN %(warehouses)s
				AND NOT EXISTS (SELECT 1 FROM `tabBin` bin
					WHERE bin.item_code = snapshot.item_code AND bin.warehouse = snapshot.warehouse)
			"""

	conditions = []
	if context.get("warehouse_is_group"):
		conditions.append("candidate.warehouse IN %(warehouses)s")
	elif params["warehouse"]:
		conditions.append("candidate.warehouse = %(warehouse)s")
	if params["keyword"]:
		conditions.append("(" + " OR ".join(
			f"INSTR(LOWER(COALESCE(item.{field}, '')), LOWER(%(keyword)s)) > 0"
			for field in ITEM_SEARCH_FIELDS.values()
			if field != "custom_specifications" or context.get("color_field") == "custom_specifications"
		) + ")")
	if params["item_name"]:
		conditions.append("INSTR(LOWER(COALESCE(item.item_name, '')), LOWER(%(item_name)s)) > 0")
	if params["specifications"]:
		conditions.append("INSTR(LOWER(COALESCE(item.custom_specifications, '')), LOWER(%(specifications)s)) > 0")
	if params["reference_location"]:
		conditions.append("""
			EXISTS (SELECT 1 FROM `tabInventory Original Location Snapshot` location
				WHERE location.company = %(company)s AND location.snapshot_key = %(snapshot_key)s
				AND location.item_code = candidate.item_code AND location.warehouse = candidate.warehouse
				AND INSTR(LOWER(COALESCE(NULLIF(location.original_location, ''), '库位待维护')),
					LOWER(%(reference_location)s)) > 0)
		""")
	bom_filter = _bom_usage_filter(filters)
	if bom_filter:
		bom_exists = """
			EXISTS (SELECT 1 FROM `tabBOM Item` component
			INNER JOIN `tabBOM` bom ON bom.name = component.parent
			WHERE component.parenttype = 'BOM' AND component.parentfield = 'items'
			AND bom.docstatus = 1 AND bom.is_active = 1
			AND component.item_code = candidate.item_code)
		"""
		conditions.append(bom_exists if bom_filter == "used" else f"NOT {bom_exists}")
	where = "WHERE " + " AND ".join(conditions) if conditions else ""
	from_query = f"FROM ({base}) candidate INNER JOIN `tabItem` item ON item.name = candidate.item_code"
	total = (
		int(frappe.db.sql(f"SELECT COUNT(*) AS total {from_query} {where}", params, as_dict=True)[0]["total"])
		if known_total is None else known_total
	)
	if not total:
		return [], 0
	params["start"] = _parse_start(filters.get("_start"))
	params["page_length"] = _request_page_length(filters.get("_page_length"))
	color_value = "COALESCE(item.custom_specifications, '')" if context.get("color_field") == "custom_specifications" else "''"
	warehouse_company_clause = "warehouse.company IN %(companies)s" if context.get("all_companies") else "warehouse.company = %(company)s"
	rows = frappe.db.sql(
		f"""SELECT candidate.item_code, item.item_name, candidate.warehouse, candidate.actual_qty,
			{color_value} AS color,
			COALESCE(warehouse.company, '') AS company,
			warehouse.warehouse_name, parent.name AS parent_warehouse,
			parent.warehouse_name AS parent_warehouse_name,
			item.stock_uom, item.item_group, COALESCE(item.custom_dpci, '') AS dpci,
			COALESCE(item.custom_external_code, '') AS external_code,
			COALESCE(item.custom_original_identifier_alias, '') AS original_identifier_alias
			{from_query}
			LEFT JOIN `tabWarehouse` warehouse ON warehouse.name = candidate.warehouse
				AND {warehouse_company_clause} AND warehouse.is_group = 0
			LEFT JOIN `tabWarehouse` parent ON parent.name = warehouse.parent_warehouse
				AND parent.company = warehouse.company AND parent.is_group = 1
			{where}
			ORDER BY (ABS(candidate.actual_qty) <= 0.000000001), candidate.item_code, candidate.warehouse
			LIMIT %(page_length)s OFFSET %(start)s""",
		params, as_dict=True,
	)
	return rows, total


def _paged_category_snapshots(context: dict[str, Any], pairs: set[tuple[str, str]]) -> list[dict[str, Any]]:
	if not pairs or not context["snapshot_key"]:
		return []
	rows = frappe.db.sql(
		f"""SELECT snapshot.snapshot_key, snapshot.snapshot_date, snapshot.item_code,
			snapshot.warehouse, snapshot.original_location, snapshot.location_qty
			FROM `tab{SNAPSHOT_DOCTYPE}` snapshot
			WHERE snapshot.company = %(company)s AND snapshot.snapshot_key = %(snapshot_key)s
			AND (snapshot.item_code, snapshot.warehouse) IN %(pairs)s
			ORDER BY snapshot.item_code, snapshot.warehouse, snapshot.original_location""",
		{
			"company": context["company"], "snapshot_key": context["snapshot_key"],
			"pairs": tuple(sorted(pairs)),
		}, as_dict=True,
	)
	return rows


def _category_snapshot_summary(context: dict[str, Any]) -> dict[str, Any]:
	if not context["snapshot_key"] or not context.get("items") or not context["warehouses"]:
		return {}
	excluded = "AND item.item_group NOT IN %(excluded_groups)s" if context.get("excluded_groups") else ""
	rows = frappe.db.sql(
		f"""SELECT snapshot.snapshot_key, MAX(snapshot.snapshot_date) AS snapshot_date
			FROM `tab{SNAPSHOT_DOCTYPE}` snapshot
			INNER JOIN `tabItem` item ON item.name = snapshot.item_code
			INNER JOIN `tabItem Group` item_group ON item_group.name = item.item_group
			WHERE snapshot.company = %(company)s AND snapshot.snapshot_key = %(snapshot_key)s
			AND snapshot.warehouse IN %(warehouses)s AND snapshot.item_code IN %(items)s
			AND item_group.lft >= %(group_lft)s AND item_group.rgt <= %(group_rgt)s
			{excluded} GROUP BY snapshot.snapshot_key""",
		{
			"company": context["company"], "snapshot_key": context["snapshot_key"],
			"warehouses": context["warehouses"], "items": context["items"],
			"group_lft": context["group_lft"], "group_rgt": context["group_rgt"],
			"excluded_groups": context.get("excluded_groups") or ("",),
		}, as_dict=True,
	)
	return rows[0] if rows else {}


def _build_paged_category_payload(category: str, filters: dict[str, Any], start: Any, page_length: Any) -> dict[str, Any]:
	context = _category_query_context(category, filters)
	missing_group = _text(context.get("missing_item_group"))
	parsed_start = _parse_start(start)
	parsed_length = _request_page_length(page_length)
	page_filters = {**filters, "_start": parsed_start, "_page_length": parsed_length}
	rows, total = ([], 0) if missing_group else _paged_category_candidates(context, page_filters)
	pairs = {(_text(row.get("item_code")), _text(row.get("warehouse"))) for row in rows}
	snapshots = _paged_category_snapshots(context, pairs) if pairs else []
	statuses = _load_bom_usage_statuses(row["item_code"] for row in rows)
	payload = build_categorized_inventory_payload(
		rows, snapshots, category=category, filters=_category_payload_filters(filters, context),
		bom_usage_statuses=statuses, start=0, page_length=None,
	)
	_attach_display_item_groups(payload, category)
	summary = _category_snapshot_summary(context) if not missing_group else {}
	payload.update({
		"snapshot_key": summary.get("snapshot_key") or "",
		"snapshot_date": summary.get("snapshot_date") or "",
		"total_count": total,
		"page_count": len(payload["groups"]),
		"start": parsed_start,
		"page_length": parsed_length,
		"has_previous": parsed_start > 0,
		"has_next": parsed_start + len(rows) < total,
		"company": context["company"],
		"inventory_scope": "accessible_companies" if context.get("all_companies") else "company",
		"item_group_options": [] if missing_group else context["item_group_options"],
		"bom_usage_scope": BOM_USAGE_SCOPE,
	})
	if category == "material" and not context.get("all_companies"):
		payload["snapshot_options"] = _list_snapshot_options(context["company"])
	if missing_group:
		payload["warning"] = _("ERP 未维护分类物料组：{0}").format(missing_group)
	payload.update(_movement_permission_payload())
	return payload


def _iter_categorized_export_groups(
	category: str, filters: dict[str, Any], context: dict[str, Any]
) -> Iterable[dict[str, Any]]:
	if context.get("missing_item_group"):
		return
	start = 0
	total: int | None = None
	while True:
		rows, total = _paged_category_candidates(
			context, {**filters, "_start": start, "_page_length": EXPORT_PAGE_LENGTH}, known_total=total
		)
		if total > MAX_EXPORT_GROUPS:
			raise ValueError(_("导出结果超过 {0} 个物料仓库组，请增加筛选条件。").format(MAX_EXPORT_GROUPS))
		if not rows:
			return
		pairs = {(_text(row.get("item_code")), _text(row.get("warehouse"))) for row in rows}
		snapshots = _paged_category_snapshots(context, pairs)
		statuses = _load_bom_usage_statuses(row["item_code"] for row in rows)
		payload = build_categorized_inventory_payload(
			rows, snapshots, category=category, filters=_category_payload_filters(filters, context),
			bom_usage_statuses=statuses, start=0, page_length=None,
		)
		_attach_display_item_groups(payload, category)
		yield from payload["groups"]
		start += len(rows)
		if start >= total:
			return


def _build_categorized_export_content(
	category: str, filters: dict[str, Any], context: dict[str, Any]
) -> bytes:
	title = get_category_definition(category)["title"]
	all_companies = bool(context.get("all_companies"))
	groups = _iter_categorized_export_groups(category, filters, context)
	buffered: list[dict[str, Any]] = []
	for group in groups:
		buffered.append(group)
		if len(buffered) > EXPORT_STREAM_THRESHOLD:
			return build_categorized_inventory_xlsx_streaming(title, chain(buffered, groups), category=category, all_companies=all_companies)
	content = build_categorized_inventory_xlsx({"category": category, "title": title, "all_companies": all_companies, "groups": buffered})
	if len(content) > MAX_EXPORT_FILE_BYTES:
		raise ValueError(_("导出文件超过 {0} MB，请增加筛选条件。").format(MAX_EXPORT_FILE_BYTES // (1024 * 1024)))
	return content


def _movement_permission_payload() -> dict[str, Any]:
	if frappe is None:
		return {"can_create_stock_entry": True, "movement_disabled_reason": ""}
	allowed = bool(frappe.has_permission("Stock Entry", "create"))
	return {
		"can_create_stock_entry": allowed,
		"movement_disabled_reason": "" if allowed else _("没有创建物料移动单的权限。"),
	}


def _parse_list(value: Any, label: str) -> list[dict[str, Any]]:
	if isinstance(value, str):
		try:
			value = json.loads(value or "[]")
		except json.JSONDecodeError as error:
			raise ValueError(_("{0}格式不正确。").format(label)) from error
	if not isinstance(value, list) or not value:
		raise ValueError(_("{0}不能为空。").format(label))
	if len(value) > MAX_PAGE_LENGTH:
		raise ValueError(_("{0}一次不能超过 {1} 项。").format(label, MAX_PAGE_LENGTH))
	if not all(isinstance(row, dict) for row in value):
		raise ValueError(_("{0}格式不正确。").format(label))
	return [dict(row) for row in value]


def _require_stock_entry_create_permission() -> None:
	if frappe is None:
		return
	if not frappe.has_permission("Stock Entry", "create"):
		frappe.throw(_("没有权限创建物料移动单。"), frappe.PermissionError)
	for doctype in ("Item", "Bin", "Warehouse", "Stock Entry Type"):
		if not frappe.has_permission(doctype, "read"):
			frappe.throw(_("没有权限读取{0}。").format(doctype), frappe.PermissionError)


def _request_page_length(value: Any) -> int:
	try:
		return parse_page_length(value)
	except ValueError as error:
		if frappe is not None:
			frappe.throw(str(error), frappe.ValidationError)
		raise


def _validate_item(item_code: str):
	item_code = _text(item_code)
	if not item_code:
		raise ValueError(_("物料编码不能为空。"))
	item = frappe.get_doc("Item", item_code)
	item.check_permission("read")
	if getattr(item, "disabled", 0) or not getattr(item, "is_stock_item", 0):
		raise ValueError(_("物料不可用于库存移动：{0}").format(item_code))
	return item


def _validate_warehouse(company: str, warehouse: str):
	warehouse = _text(warehouse)
	if not warehouse:
		raise ValueError(_("仓库不能为空。"))
	doc = frappe.get_doc("Warehouse", warehouse)
	doc.check_permission("read")
	if _text(getattr(doc, "company", "")) != company:
		raise ValueError(_("仓库不属于当前公司：{0}").format(warehouse))
	if int(getattr(doc, "is_group", 0) or 0):
		raise ValueError(_("不能使用分组仓库：{0}").format(warehouse))
	return doc


def _actual_qty(item_code: str, warehouse: str) -> float:
	if frappe is None or not warehouse:
		return 0.0
	value = frappe.db.get_value(
		"Bin",
		{"item_code": item_code, "warehouse": warehouse},
		"actual_qty",
	)
	return float(value or 0)


def _attach_reversal_progress(payload, selections=None):
	if selections:
		selected = _parse_list(selections, _("已选物料"))
		if len(selected) > MAX_PAGE_LENGTH:
			raise ValueError(_("已选物料超过库存范围上限"))
		payload["selected_reversals"] = []
		for row in selected:
			code, warehouse = _text(row.get("item_code")), _text(row.get("source_warehouse"))
			_validate_item(code)
			_validate_warehouse(payload["company"], warehouse)
			payload["selected_reversals"].append({"item_code": code, "warehouse": warehouse})
	for row in payload.get("groups", []) + payload.get("selected_reversals", []):
		operation_id = (
			frappe.db.get_value(
				"Bin",
				{"item_code": row["item_code"], "warehouse": row["warehouse"]},
				"custom_purchase_reversal_operation",
			)
			if row.get("warehouse")
			else None
		)
		row["reversal"] = None
		if operation_id:
			from . import purchase_reversal_progress as progress

			_operation, context, output = progress._load(operation_id, lock=False)
			try:
				row["reversal"] = progress.public(context, output)
			except frappe.PermissionError:
				row["reversal"] = {
					"stage": "waiting_inventory",
					"safe_reason": "progress_unavailable",
					"can_retry": False,
				}
	return payload


def _require_pair_available(item_code, warehouse):
	# This endpoint only prepares an unsaved native document. The real writer
	# still acquires/rechecks the existing durable leases on save/submit.
	if frappe.db.get_value(
		"Bin", {"item_code": item_code, "warehouse": warehouse}, "custom_purchase_reversal_operation"
	):
		frappe.throw(_("采购冲销未完成，相关库存移动暂缓办理。"), frappe.ValidationError)


def build_movement_item_spec(
	*,
	purpose: str,
	item_code: str,
	source_warehouse: str,
	target_warehouse: str,
	quantity: Any,
	actual_qty: Any,
) -> dict[str, Any]:
	"""Build simple-purpose warehouses without writing an ERP document."""

	purpose = _text(purpose)
	item_code = _text(item_code)
	source_warehouse = _text(source_warehouse)
	target_warehouse = _text(target_warehouse)
	try:
		quantity = float(quantity)
	except (TypeError, ValueError) as error:
		raise ValueError(_("{0}的移动数量必须是数字。").format(item_code or _("物料"))) from error
	if not math.isfinite(quantity):
		raise ValueError(_("{0}的移动数量必须是有限数字。").format(item_code or _("物料")))
	if quantity <= 0:
		raise ValueError(_("{0}的移动数量必须大于零。").format(item_code or _("物料")))

	if purpose in {"Material Issue", "Material Transfer"}:
		if not source_warehouse:
			raise ValueError(_("{0}缺少来源仓库。").format(item_code))
		if quantity - float(actual_qty or 0) > 1e-9:
			raise ValueError(_("{0}的移动数量超过来源仓实时库存 {1:g}。").format(item_code, float(actual_qty or 0)))
	if purpose in {"Material Receipt", "Material Transfer"} and not target_warehouse:
		raise ValueError(_("请选择目标仓库。"))
	if purpose == "Material Transfer" and source_warehouse == target_warehouse:
		raise ValueError(_("{0}的来源仓库和目标仓库不能相同。").format(item_code))

	result = {
		"item_code": item_code,
		"qty": quantity,
		"s_warehouse": "",
		"t_warehouse": "",
	}
	if purpose == "Material Receipt":
		result["t_warehouse"] = target_warehouse
	elif purpose == "Material Issue":
		result["s_warehouse"] = source_warehouse
	elif purpose == "Material Transfer":
		result["s_warehouse"] = source_warehouse
		result["t_warehouse"] = target_warehouse
	return result


def _stock_entry_types() -> list[dict[str, str]]:
	rows = frappe.get_list(
		"Stock Entry Type",
		fields=["name", "purpose"],
		order_by="name asc",
		limit_page_length=0,
	)
	return [
		{
			"name": _text(row.get("name") if isinstance(row, dict) else row.name),
			"purpose": _text(row.get("purpose") if isinstance(row, dict) else row.purpose),
		}
		for row in rows
	]


@_whitelist
def get_inventory_movement_context(company: str, selections: Any) -> dict[str, Any]:
	"""Re-read selected item/warehouse groups from live Bin quantities."""

	_require_stock_entry_create_permission()
	company = _resolve_company(company)
	_require_company_permission(company)
	selected = _parse_list(selections, _("已选物料"))
	seen: set[tuple[str, str]] = set()
	items: list[dict[str, Any]] = []
	for selection in selected:
		item_code = _text(selection.get("item_code"))
		source_warehouse = _text(selection.get("source_warehouse"))
		key = (item_code, source_warehouse)
		if key in seen:
			raise ValueError(_("重复选择物料仓库组：{0} / {1}").format(item_code, source_warehouse))
		seen.add(key)
		item = _validate_item(item_code)
		_validate_warehouse(company, source_warehouse)
		_require_pair_available(item_code, source_warehouse)
		items.append(
			{
				"item_code": item_code,
				"item_name": _text(getattr(item, "item_name", "")),
				"source_warehouse": source_warehouse,
				"actual_qty": _actual_qty(item_code, source_warehouse),
				"stock_uom": _text(getattr(item, "stock_uom", "")),
			}
		)
	return {
		"company": company,
		"items": items,
		"stock_entry_types": _stock_entry_types(),
	}


@_whitelist
def prepare_inventory_stock_entry(
	company: str,
	stock_entry_type: str,
	target_warehouse: str = "",
	items: Any = None,
) -> dict[str, Any]:
	"""Return a complete local Stock Entry without inserting or saving it."""

	_require_stock_entry_create_permission()
	company = _resolve_company(company)
	_require_company_permission(company)
	stock_entry_type = _text(stock_entry_type)
	if not stock_entry_type:
		raise ValueError(_("请选择移动类型。"))
	type_doc = frappe.get_doc("Stock Entry Type", stock_entry_type)
	type_doc.check_permission("read")
	purpose = _text(getattr(type_doc, "purpose", ""))
	if not purpose:
		raise ValueError(_("移动类型未配置用途：{0}").format(stock_entry_type))

	target_warehouse = _text(target_warehouse)
	if purpose in {"Material Receipt", "Material Transfer"}:
		_validate_warehouse(company, target_warehouse)

	selected = _parse_list(items, _("移动物料"))
	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.company = company
	stock_entry.stock_entry_type = stock_entry_type
	stock_entry.purpose = purpose
	if purpose in {"Material Receipt", "Material Transfer"}:
		stock_entry.to_warehouse = target_warehouse

	seen: set[tuple[str, str]] = set()
	for selected_item in selected:
		item_code = _text(selected_item.get("item_code"))
		source_warehouse = _text(selected_item.get("source_warehouse"))
		key = (item_code, source_warehouse)
		if key in seen:
			raise ValueError(_("重复移动物料仓库组：{0} / {1}").format(item_code, source_warehouse))
		seen.add(key)
		item = _validate_item(item_code)
		for warehouse in {source_warehouse, target_warehouse} - {""}:
			_require_pair_available(item_code, warehouse)
		actual_qty = 0.0
		if purpose in {"Material Issue", "Material Transfer"}:
			_validate_warehouse(company, source_warehouse)
			actual_qty = _actual_qty(item_code, source_warehouse)
		spec = build_movement_item_spec(
			purpose=purpose,
			item_code=item_code,
			source_warehouse=source_warehouse,
			target_warehouse=target_warehouse,
			quantity=selected_item.get("qty"),
			actual_qty=actual_qty,
		)
		warehouse = spec.get("s_warehouse") or spec.get("t_warehouse") or ""
		details = stock_entry.get_item_details(
			frappe._dict(
				{
					"item_code": item_code,
					"company": company,
					"qty": spec["qty"],
					"warehouse": warehouse,
				}
			)
		)
		row = dict(details or {})
		row.update(spec)
		row.setdefault("uom", _text(getattr(item, "stock_uom", "")))
		row.setdefault("stock_uom", _text(getattr(item, "stock_uom", "")))
		row.setdefault("conversion_factor", 1)
		stock_entry.append("items", row)

	document = stock_entry.as_dict()
	document["__islocal"] = 1
	return {
		"stock_entry": document,
		"purpose": purpose,
		"requires_completion": purpose not in SIMPLE_STOCK_ENTRY_PURPOSES,
		"completion_message": (
			_("该移动类型还需要在标准物料移动表单中补充工单、BOM、批次、序列号或物料角色等字段。")
			if purpose not in SIMPLE_STOCK_ENTRY_PURPOSES
			else ""
		),
	}


@_whitelist
def get_inventory_location_detail(
	filters: Any = None,
	start: Any = 0,
	page_length: Any = DEFAULT_PAGE_LENGTH,
	progress_selections: Any = None,
	**kwargs: Any,
) -> dict[str, Any]:
	_require_read_permission()
	parsed = _parse_filters(filters, **kwargs)
	company = _resolve_company(parsed.get("company"))
	parsed["company"] = company
	_require_company_permission(company)
	bom_filter = _bom_usage_filter(parsed)
	snapshot_rows = _load_snapshot_rows(parsed)
	payload = build_inventory_location_payload(
		snapshot_rows,
		parsed,
		bom_usage_statuses=(
			_load_bom_usage_statuses(_material_bom_candidate_codes(snapshot_rows, parsed))
			if bom_filter
			else None
		),
		start=_parse_start(start),
		page_length=_request_page_length(page_length),
	)
	if not bom_filter:
		_attach_page_bom_usage(payload)
	payload["bom_usage_scope"] = BOM_USAGE_SCOPE
	payload["snapshot_options"] = _list_snapshot_options(company)
	payload.update(_movement_permission_payload())
	return _attach_reversal_progress(payload, progress_selections)


@_whitelist
def export_inventory_location_detail(filters: Any = None, **kwargs: Any) -> dict[str, Any]:
	_require_read_permission()
	_require_inventory_export_permission(INVENTORY_EXPORT_PERMISSION_DOCTYPES)
	parsed = _parse_filters(filters, **kwargs)
	company = _resolve_company(parsed.get("company"))
	parsed["company"] = company
	_require_company_permission(company)
	_bom_usage_filter(parsed)
	snapshot_rows = _load_snapshot_rows(parsed)
	payload = build_inventory_location_payload(
		snapshot_rows,
		parsed,
		bom_usage_statuses=_load_bom_usage_statuses(_material_bom_candidate_codes(snapshot_rows, parsed)),
		start=0,
		page_length=None,
	)
	content = build_inventory_location_xlsx(payload)
	snapshot = payload.get("snapshot_date") or "latest"
	return {
		"file_name": f"material-inventory-detail-{snapshot}.xlsx",
		"mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
		"content_base64": base64.b64encode(content).decode("ascii"),
		"bom_usage_scope": BOM_USAGE_SCOPE,
	}


@_whitelist
def get_inventory_detail_page(
	category: str,
	filters: Any = None,
	start: Any = 0,
	page_length: Any = DEFAULT_PAGE_LENGTH,
	progress_selections: Any = None,
	**kwargs: Any,
) -> dict[str, Any]:
	"""Live inventory page for all four mutually exclusive item categories."""
	_require_categorized_inventory_read_permission()
	get_category_definition(category)
	parsed = _parse_filters(filters, **kwargs)
	company = _resolve_company(parsed.get("company"))
	parsed["company"] = company
	_require_company_permission(company)
	_bom_usage_filter(parsed)
	payload = _build_paged_category_payload(category, parsed, start, page_length)
	payload["can_export_inventory"] = _can_export_inventory(INVENTORY_EXPORT_PERMISSION_DOCTYPES)
	return _attach_reversal_progress(payload, progress_selections)


@_whitelist
def get_categorized_inventory_detail(
	category: str,
	filters: Any = None,
	start: Any = 0,
	page_length: Any = DEFAULT_PAGE_LENGTH,
	progress_selections: Any = None,
	**kwargs: Any,
) -> dict[str, Any]:
	_require_categorized_inventory_read_permission()
	parsed = _parse_filters(filters, **kwargs)
	company = _resolve_company(parsed.get("company"))
	parsed["company"] = company
	_require_company_permission(company)
	bom_filter = _bom_usage_filter(parsed)
	get_category_definition(category)
	context = _category_query_context(category, parsed)
	parsed = _category_payload_filters(parsed, context)
	missing_item_group = _text(context.get("missing_item_group"))
	stock_rows = [] if missing_item_group else _load_category_stock_rows(context)
	snapshot_rows = [] if missing_item_group else _load_category_snapshot_rows(context)
	payload = build_categorized_inventory_payload(
		stock_rows,
		snapshot_rows,
		category=category,
		filters=parsed,
		bom_usage_statuses=(
			_load_bom_usage_statuses(_category_bom_candidate_codes(stock_rows, snapshot_rows, parsed))
			if bom_filter
			else None
		),
		start=_parse_start(start),
		page_length=_request_page_length(page_length),
	)
	if not bom_filter:
		_attach_page_bom_usage(payload)
	payload["bom_usage_scope"] = BOM_USAGE_SCOPE
	payload["company"] = company
	payload["inventory_scope"] = "accessible_companies" if context.get("all_companies") else "company"
	_attach_display_item_groups(payload, category)
	payload["item_group_options"] = [] if missing_item_group else _list_category_item_groups(context)
	if missing_item_group:
		payload["warning"] = _("ERP 未维护分类物料组：{0}").format(missing_item_group)
	payload.update(_movement_permission_payload())
	return _attach_reversal_progress(payload, progress_selections)


@_whitelist
def export_categorized_inventory_detail(category: str, filters: Any = None, **kwargs: Any) -> dict[str, Any]:
	_require_categorized_inventory_read_permission()
	_require_inventory_export_permission(INVENTORY_EXPORT_PERMISSION_DOCTYPES)
	parsed = _parse_filters(filters, **kwargs)
	company = _resolve_company(parsed.get("company"))
	parsed["company"] = company
	_require_company_permission(company)
	_bom_usage_filter(parsed)
	definition = get_category_definition(category)
	context = _category_query_context(category, parsed)
	content = _build_categorized_export_content(category, parsed, context)
	return {
		"file_name": f"{definition['file_prefix']}.xlsx",
		"mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
		"content_base64": base64.b64encode(content).decode("ascii"),
		"bom_usage_scope": BOM_USAGE_SCOPE,
		"inventory_scope": "accessible_companies" if context.get("all_companies") else "company",
	}
