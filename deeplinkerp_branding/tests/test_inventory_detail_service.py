"""Unified inventory detail pagination and movement preparation tests."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from deeplinkerp_branding.services import inventory_detail_service as service

MATERIAL_ROWS = [
	{
		"snapshot_key": "YWFM-2026-09-29",
		"snapshot_date": "2026-09-29",
		"company": "YUEWEI MX",
		"item_code": item_code,
		"item_name": item_code,
		"warehouse": warehouse,
		"original_location": location,
		"location_qty": quantity,
		"stock_uom": "个：pieza",
		"item_group": "物料",
	}
	for item_code, warehouse, location, quantity in (
		("A", "W1", "L1", 2),
		("A", "W1", "L2", 3),
		("B", "W1", "L3", 4),
		("C", "W2", "L4", 5),
	)
]


def test_page_length_defaults_to_200_and_accepts_every_integer_in_range() -> None:
	assert service.DEFAULT_PAGE_LENGTH == 200
	assert service.parse_page_length(None) == 200
	assert service.parse_page_length(1) == 1
	assert service.parse_page_length("2500") == 2500

	for value in (0, 2501, "abc", 1.5):
		with pytest.raises(ValueError, match="1 到 2500"):
			service.parse_page_length(value)


def test_material_inventory_is_paginated_by_item_warehouse_group() -> None:
	first = service.build_inventory_location_payload(
		MATERIAL_ROWS,
		{},
		start=0,
		page_length=2,
	)
	assert first["total_count"] == 3
	assert first["page_count"] == 2
	assert first["location_count"] == 3
	assert first["has_previous"] is False
	assert first["has_next"] is True
	assert [row["item_code"] for row in first["groups"]] == ["A", "B"]
	assert first["groups"][0]["total_qty"] == 5

	last = service.build_inventory_location_payload(
		MATERIAL_ROWS,
		{},
		start=2,
		page_length=2,
	)
	assert last["page_count"] == 1
	assert last["has_previous"] is True
	assert last["has_next"] is False
	assert last["groups"][0]["item_code"] == "C"


def test_material_export_can_request_the_complete_filtered_result() -> None:
	payload = service.build_inventory_location_payload(
		MATERIAL_ROWS,
		{"warehouse": "W1"},
		start=0,
		page_length=None,
	)
	assert payload["total_count"] == 2
	assert payload["page_count"] == 2
	assert payload["has_next"] is False


def test_categorized_inventory_uses_the_same_group_pagination_contract() -> None:
	stock_rows = [
		{
			"item_code": row["item_code"],
			"item_name": row["item_name"],
			"warehouse": row["warehouse"],
			"actual_qty": row["location_qty"],
			"stock_uom": row["stock_uom"],
			"item_group": "半成品",
		}
		for row in (MATERIAL_ROWS[0], MATERIAL_ROWS[2], MATERIAL_ROWS[3])
	]
	payload = service.build_categorized_inventory_payload(
		stock_rows,
		[],
		category="semi_finished",
		filters={},
		start=1,
		page_length=1,
	)
	assert payload["total_count"] == 3
	assert payload["page_count"] == 1
	assert payload["start"] == 1
	assert payload["has_previous"] is True
	assert payload["has_next"] is True


@pytest.mark.parametrize(
	("purpose", "source", "target", "actual_qty", "quantity", "expected"),
	(
		("Material Receipt", "W1", "W2", 0, 3, {"s_warehouse": "", "t_warehouse": "W2"}),
		("Material Issue", "W1", "", 5, 3, {"s_warehouse": "W1", "t_warehouse": ""}),
		("Material Transfer", "W1", "W2", 5, 3, {"s_warehouse": "W1", "t_warehouse": "W2"}),
		("Manufacture", "W1", "W2", 0, 3, {"s_warehouse": "", "t_warehouse": ""}),
	),
)
def test_stock_entry_warehouse_prefill_follows_standard_purpose(
	purpose: str,
	source: str,
	target: str,
	actual_qty: float,
	quantity: float,
	expected: dict[str, str],
) -> None:
	assert service.build_movement_item_spec(
		purpose=purpose,
		item_code="ITEM-1",
		source_warehouse=source,
		target_warehouse=target,
		quantity=quantity,
		actual_qty=actual_qty,
	) | {"item_code": "ITEM-1", "qty": quantity} == expected | {
		"item_code": "ITEM-1",
		"qty": quantity,
	}


def test_issue_and_transfer_reject_zero_excess_and_same_warehouse() -> None:
	with pytest.raises(ValueError, match="大于零"):
		service.build_movement_item_spec(
			purpose="Material Issue",
			item_code="ITEM-1",
			source_warehouse="W1",
			target_warehouse="",
			quantity=0,
			actual_qty=5,
		)
	with pytest.raises(ValueError, match="实时库存"):
		service.build_movement_item_spec(
			purpose="Material Transfer",
			item_code="ITEM-1",
			source_warehouse="W1",
			target_warehouse="W2",
			quantity=6,
			actual_qty=5,
		)
	with pytest.raises(ValueError, match="不能相同"):
		service.build_movement_item_spec(
			purpose="Material Transfer",
			item_code="ITEM-1",
			source_warehouse="W1",
			target_warehouse="W1",
			quantity=1,
			actual_qty=5,
		)


@pytest.mark.parametrize("quantity", (float("nan"), float("inf"), float("-inf")))
def test_movement_quantity_rejects_non_finite_numbers(quantity: float) -> None:
	with pytest.raises(ValueError, match="有限数字"):
		service.build_movement_item_spec(
			purpose="Material Transfer",
			item_code="ITEM-1",
			source_warehouse="W1",
			target_warehouse="W2",
			quantity=quantity,
			actual_qty=5,
		)


def test_server_company_fallback_uses_the_current_user_default(monkeypatch) -> None:
	class FakeDefaults:
		@staticmethod
		def get_user_default(key: str) -> str:
			assert key == "Company"
			return "用户默认公司"

	class FakeFrappe:
		defaults = FakeDefaults()
		ValidationError = ValueError

		@staticmethod
		def throw(message: str, exception: type[Exception]) -> None:
			raise exception(message)

	monkeypatch.setattr(service, "frappe", FakeFrappe())
	assert service._resolve_company("") == "用户默认公司"
	assert service._resolve_company("明确公司") == "明确公司"


def test_server_company_fallback_requires_a_configured_default(monkeypatch) -> None:
	class FakeDefaults:
		@staticmethod
		def get_user_default(_key: str):
			return None

	class FakeFrappe:
		defaults = FakeDefaults()
		ValidationError = ValueError

		@staticmethod
		def throw(message: str, exception: type[Exception]) -> None:
			raise exception(message)

	monkeypatch.setattr(service, "frappe", FakeFrappe())
	with pytest.raises(ValueError, match="默认公司"):
		service._resolve_company("")


def test_prepare_endpoint_requires_create_permission_before_building_a_document(monkeypatch) -> None:
	class FakeFrappe:
		PermissionError = PermissionError

		@staticmethod
		def has_permission(doctype: str, permission_type: str) -> bool:
			assert (doctype, permission_type) == ("Stock Entry", "create")
			return False

		@staticmethod
		def throw(message: str, exception: type[Exception]) -> None:
			raise exception(message)

	monkeypatch.setattr(service, "frappe", FakeFrappe())
	with pytest.raises(PermissionError, match="创建物料移动单"):
		service.prepare_inventory_stock_entry(
			"YUEWEI MX",
			"Material Transfer",
			"W2",
			[{"item_code": "ITEM-1", "source_warehouse": "W1", "qty": 1}],
		)


def test_prepare_endpoint_contains_no_persistence_operation() -> None:
	source = inspect.getsource(service.prepare_inventory_stock_entry)
	for forbidden in (".insert(", ".save(", ".submit(", ".db.commit("):
		assert forbidden not in source


class FakeStockEntry:
	def __init__(self) -> None:
		self.doctype = "Stock Entry"
		self.name = "new-stock-entry-test"
		self.items: list[dict] = []

	def get_item_details(self, args):
		return {
			"item_name": args["item_code"],
			"stock_uom": "Nos",
			"uom": "Nos",
			"conversion_factor": 1,
		}

	def append(self, fieldname: str, row: dict) -> None:
		assert fieldname == "items"
		self.items.append(dict(row))

	def as_dict(self) -> dict:
		return {
			"doctype": self.doctype,
			"name": self.name,
			"company": self.company,
			"stock_entry_type": self.stock_entry_type,
			"purpose": self.purpose,
			"to_warehouse": getattr(self, "to_warehouse", ""),
			"items": self.items,
		}


def fake_frappe(
	*, purpose: str, actual_qty: float = 5, target_company: str = "YUEWEI MX", target_group: int = 0
):
	stock_entry = FakeStockEntry()

	class FakeDB:
		@staticmethod
		def get_value(doctype, filters, fieldname):
			if (doctype, fieldname) == ("Bin", "custom_purchase_reversal_operation"):
				return None
			assert (doctype, fieldname) == ("Bin", "actual_qty")
			return actual_qty

	class FakeFrappe:
		PermissionError = PermissionError
		db = FakeDB()

		@staticmethod
		def has_permission(doctype: str, permission_type: str) -> bool:
			return (doctype, permission_type) == ("Stock Entry", "create") or (
				permission_type == "read" and doctype in {"Item", "Bin", "Warehouse", "Stock Entry Type"}
			)

		@staticmethod
		def throw(message: str, exception: type[Exception]) -> None:
			raise exception(message)

		@staticmethod
		def get_doc(doctype: str, name: str):
			if doctype == "Company":
				return SimpleNamespace(check_permission=lambda _permission: None)
			if doctype == "Item":
				return SimpleNamespace(
					name=name,
					item_name=name,
					stock_uom="Nos",
					disabled=0,
					is_stock_item=1,
					check_permission=lambda _permission: None,
				)
			if doctype == "Warehouse":
				company = target_company if name == "W2" else "YUEWEI MX"
				is_group = target_group if name == "W2" else 0
				return SimpleNamespace(
					name=name,
					company=company,
					is_group=is_group,
					check_permission=lambda _permission: None,
				)
			if doctype == "Stock Entry Type":
				return SimpleNamespace(
					name=name,
					purpose=purpose,
					check_permission=lambda _permission: None,
				)
			raise AssertionError((doctype, name))

		@staticmethod
		def new_doc(doctype: str):
			assert doctype == "Stock Entry"
			return stock_entry

		@staticmethod
		def _dict(value: dict):
			return value

	return FakeFrappe(), stock_entry


@pytest.mark.parametrize(
	("purpose", "target", "expected_source", "expected_target", "complex_type"),
	(
		("Material Receipt", "W2", "", "W2", False),
		("Material Issue", "", "W1", "", False),
		("Material Transfer", "W2", "W1", "W2", False),
		("Manufacture", "W2", "", "", True),
	),
)
def test_prepare_returns_an_unsaved_standard_document_without_writing(
	monkeypatch,
	purpose: str,
	target: str,
	expected_source: str,
	expected_target: str,
	complex_type: bool,
) -> None:
	framework, stock_entry = fake_frappe(purpose=purpose)
	monkeypatch.setattr(service, "frappe", framework)
	result = service.prepare_inventory_stock_entry(
		"YUEWEI MX",
		purpose,
		target,
		[{"item_code": "ITEM-1", "source_warehouse": "W1", "qty": 2}],
	)

	assert result["stock_entry"]["__islocal"] == 1
	assert result["stock_entry"]["purpose"] == purpose
	assert result["requires_completion"] is complex_type
	assert stock_entry.items[0]["s_warehouse"] == expected_source
	assert stock_entry.items[0]["t_warehouse"] == expected_target
	assert not any(hasattr(stock_entry, method) for method in ("insert", "save", "submit"))


def test_prepare_rechecks_live_bin_and_rejects_inventory_that_changed(monkeypatch) -> None:
	framework, _stock_entry = fake_frappe(purpose="Material Transfer", actual_qty=1)
	monkeypatch.setattr(service, "frappe", framework)
	with pytest.raises(ValueError, match="实时库存 1"):
		service.prepare_inventory_stock_entry(
			"YUEWEI MX",
			"Material Transfer",
			"W2",
			[{"item_code": "ITEM-1", "source_warehouse": "W1", "qty": 2}],
		)


def test_movement_context_reloads_live_bin_instead_of_using_page_quantity(monkeypatch) -> None:
	framework, _stock_entry = fake_frappe(purpose="Material Transfer", actual_qty=7.5)
	monkeypatch.setattr(service, "frappe", framework)
	monkeypatch.setattr(
		service,
		"_stock_entry_types",
		lambda: [{"name": "Material Transfer", "purpose": "Material Transfer"}],
	)
	context = service.get_inventory_movement_context(
		"YUEWEI MX",
		[{"item_code": "ITEM-1", "source_warehouse": "W1", "actual_qty": 999}],
	)
	assert context["items"][0]["actual_qty"] == 7.5
	assert context["stock_entry_types"] == [{"name": "Material Transfer", "purpose": "Material Transfer"}]


@pytest.mark.parametrize(
	("target_company", "target_group", "message"),
	(
		("OTHER", 0, "不属于当前公司"),
		("YUEWEI MX", 1, "分组仓库"),
	),
)
def test_prepare_rejects_cross_company_and_group_target_warehouses(
	monkeypatch, target_company: str, target_group: int, message: str
) -> None:
	framework, _stock_entry = fake_frappe(
		purpose="Material Transfer",
		target_company=target_company,
		target_group=target_group,
	)
	monkeypatch.setattr(service, "frappe", framework)
	with pytest.raises(ValueError, match=message):
		service.prepare_inventory_stock_entry(
			"YUEWEI MX",
			"Material Transfer",
			"W2",
			[{"item_code": "ITEM-1", "source_warehouse": "W1", "qty": 1}],
		)


def test_bom_usage_queries_current_page_without_filter_and_all_candidates_with_filter(monkeypatch) -> None:
	snapshot_rows = [
		{
			"company": "YUEWEI MX", "snapshot_key": "SNAPSHOT", "item_code": code,
			"item_name": code, "warehouse": "W1", "original_location": "L1", "location_qty": 1,
		}
		for code in ("A", "B")
	]
	stock_rows = [
		{"item_code": code, "item_name": code, "warehouse": "W1", "actual_qty": 1}
		for code in ("A", "B")
	]
	for name in ("_require_read_permission", "_require_categorized_inventory_read_permission"):
		monkeypatch.setattr(service, name, lambda: None)
	monkeypatch.setattr(service, "_require_company_permission", lambda _company: None)
	monkeypatch.setattr(service, "_load_snapshot_rows", lambda _filters: snapshot_rows)
	monkeypatch.setattr(service, "_list_snapshot_options", lambda _company: [])
	monkeypatch.setattr(service, "_category_query_context", lambda _category, _filters: {})
	monkeypatch.setattr(service, "_load_category_stock_rows", lambda _context: stock_rows)
	monkeypatch.setattr(service, "_load_category_snapshot_rows", lambda _context: [])
	monkeypatch.setattr(service, "_list_category_item_groups", lambda _context: [])
	monkeypatch.setattr(service, "_movement_permission_payload", lambda: {})
	monkeypatch.setattr(service, "_attach_reversal_progress", lambda payload, _selections: payload)
	queried: list[tuple[str, ...]] = []

	def load_bom_usage(codes) -> dict[str, str]:
		unique = tuple(sorted(set(codes)))
		queried.append(unique)
		return {code: "used" if code == "A" else "unused" for code in unique}

	monkeypatch.setattr(service, "_load_bom_usage_statuses", load_bom_usage)
	for get_page in (
		lambda filters: service.get_inventory_location_detail(filters=filters, page_length=1),
		lambda filters: service.get_categorized_inventory_detail("semi_finished", filters=filters, page_length=1),
	):
		queried.clear()
		unfiltered = get_page({"company": "YUEWEI MX"})
		assert unfiltered["total_count"] == 2
		assert unfiltered["bom_usage_scope"] == service.BOM_USAGE_SCOPE
		assert unfiltered["groups"][0]["bom_usage_status"] == "used"
		assert queried == [("A",)]

		queried.clear()
		filtered = get_page({"company": "YUEWEI MX", "bom_usage_status": "used"})
		assert filtered["total_count"] == 1
		assert filtered["groups"][0]["bom_usage_status"] == "used"
		assert queried == [("A", "B")]

		queried.clear()
		empty = get_page({"company": "YUEWEI MX", "keyword": "no-match", "bom_usage_status": "used"})
		assert empty["total_count"] == 0
		assert queried == [()]

		queried.clear()
		legacy_unknown = get_page({"company": "YUEWEI MX", "bom_usage_status": "unknown"})
		assert legacy_unknown["total_count"] == 0
		assert queried == [()]


def test_live_inventory_query_pages_authorized_groups_in_database(monkeypatch) -> None:
	queries = []

	class FakeDB:
		@staticmethod
		def sql(query, values, as_dict=False):
			queries.append((query, dict(values)))
			if "COUNT(*) AS total" in query:
				return [{"total": 3}]
			return [{"item_code": "ITEM-2", "item_name": "Item 2", "warehouse": "W1", "actual_qty": 4,
				"stock_uom": "kg", "item_group": "半成品"}]

	monkeypatch.setattr(service, "frappe", SimpleNamespace(db=FakeDB()))
	rows, total = service._paged_category_candidates(
		{"company": "YUEWEI MX", "snapshot_key": "", "items": ("ITEM-1", "ITEM-2"),
			"warehouses": ("W1",), "group_lft": 2, "group_rgt": 5, "excluded_groups": ()},
		{"only_with_stock": 1, "warehouse": "W1", "_start": 1, "_page_length": 1},
	)
	assert total == 3
	assert [row["item_code"] for row in rows] == ["ITEM-2"]
	assert len(queries) == 2
	assert "LIMIT %(page_length)s OFFSET %(start)s" in queries[1][0]
	assert "INNER JOIN `tabBin`" in queries[1][0]
	assert queries[1][1]["items"] == ("ITEM-1", "ITEM-2")
	assert (queries[1][1]["start"], queries[1][1]["page_length"]) == (1, 1)


def test_missing_snapshot_and_blank_snapshot_location_have_different_statuses() -> None:
	stock = [
		{"item_code": "NO-SNAPSHOT", "warehouse": "W1", "actual_qty": 2},
		{"item_code": "BLANK-LOCATION", "warehouse": "W1", "actual_qty": 3},
	]
	snapshots = [{"item_code": "BLANK-LOCATION", "warehouse": "W1", "original_location": "", "location_qty": 3}]
	payload = service.build_categorized_inventory_payload(stock, snapshots, category="semi_finished")
	statuses = {row["item_code"]: row["inventory_status"] for row in payload["groups"]}
	assert statuses == {"NO-SNAPSHOT": "无参考快照", "BLANK-LOCATION": "库位待维护"}


def test_mixed_valid_and_blank_snapshot_locations_require_maintenance() -> None:
	stock = [{"item_code": "I", "warehouse": "W1", "actual_qty": 5}]
	snapshots = [
		{"item_code": "I", "warehouse": "W1", "original_location": "A1", "location_qty": 2},
		{"item_code": "I", "warehouse": "W1", "original_location": "", "location_qty": 3},
	]
	payload = service.build_categorized_inventory_payload(stock, snapshots, category="material")
	assert payload["groups"][0]["inventory_status"] == "库位待维护"
	assert payload["groups"][0]["difference_qty"] == 0


def test_export_endpoints_reject_missing_report_export_permission_before_loading_data(monkeypatch) -> None:
	checked = []

	def can_export(doctype):
		checked.append(doctype)
		return doctype != service.SNAPSHOT_DOCTYPE

	def deny(message, exception):
		raise exception(message)

	framework = SimpleNamespace(
		permissions=SimpleNamespace(can_export=can_export),
		PermissionError=PermissionError,
		throw=deny,
	)
	monkeypatch.setattr(service, "frappe", framework)
	monkeypatch.setattr(service, "_require_categorized_inventory_read_permission", lambda: None)
	monkeypatch.setattr(service, "_require_read_permission", lambda: None)
	monkeypatch.setattr(service, "_category_query_context", lambda *_args: pytest.fail("data query must not run"))
	monkeypatch.setattr(service, "_load_snapshot_rows", lambda *_args: pytest.fail("snapshot query must not run"))
	assert not service._can_export_inventory(service.INVENTORY_EXPORT_PERMISSION_DOCTYPES)
	with pytest.raises(PermissionError, match="没有导出库存明细的权限"):
		service.export_categorized_inventory_detail("material", {"company": "YUEWEI MX"})
	assert checked == [service.SNAPSHOT_DOCTYPE] * 2

	framework.permissions.can_export = lambda _doctype: False
	with pytest.raises(PermissionError, match="没有导出库存明细的权限"):
		service.export_inventory_location_detail({"company": "YUEWEI MX"})


@pytest.mark.parametrize("category", list(service.CATEGORY_DEFINITIONS))
def test_report_export_grant_enables_page_and_export_without_master_or_bin_export(monkeypatch, category):
	checked = []
	reads = []

	def can_export(doctype):
		checked.append(doctype)
		return doctype == service.SNAPSHOT_DOCTYPE

	def has_permission(doctype, permission_type):
		reads.append((doctype, permission_type))
		return permission_type == "read"

	monkeypatch.setattr(service, "frappe", SimpleNamespace(
		permissions=SimpleNamespace(can_export=can_export), has_permission=has_permission,
		get_doc=lambda *_args: SimpleNamespace(check_permission=lambda *_args: None),
	))
	monkeypatch.setattr(service, "_build_paged_category_payload", lambda *_args: {"company": "C1", "groups": []})
	monkeypatch.setattr(service, "_category_query_context", lambda *_args: {"company": "C1", "all_companies": True})
	monkeypatch.setattr(service, "_build_categorized_export_content", lambda *_args: b"xlsx")
	filters = {"company": "C1", "all_companies": 1}
	page = service.get_inventory_detail_page(category, filters)
	assert page["can_export_inventory"] is True
	exported = service.export_categorized_inventory_detail(category, filters)
	assert exported["content_base64"] == "eGxzeA=="
	assert checked == [service.SNAPSHOT_DOCTYPE] * 2
	assert reads == [(doctype, "read") for doctype in (service.SNAPSHOT_DOCTYPE, "Item", "Bin", "Warehouse")] * 2


@pytest.mark.parametrize("denied_doctype", ["Item", "Bin", "Warehouse"])
def test_report_export_grant_still_requires_each_source_read_permission(monkeypatch, denied_doctype):
	def throw(message, exception):
		raise exception(message)

	monkeypatch.setattr(service, "frappe", SimpleNamespace(
		has_permission=lambda doctype, _permission: doctype != denied_doctype,
		permissions=SimpleNamespace(can_export=lambda _doctype: True),
		PermissionError=PermissionError, throw=throw,
	))
	monkeypatch.setattr(service, "_category_query_context", lambda *_args: pytest.fail("must reject before querying data"))
	with pytest.raises(PermissionError, match=denied_doctype):
		service.export_categorized_inventory_detail("material", {"company": "C1", "all_companies": 1})


@pytest.mark.parametrize("excluded_groups", [(), ("OTHER-CATEGORY",)])
def test_group_options_use_native_permissions_with_category_bounds_and_exclusions(monkeypatch, excluded_groups):
	def get_list(doctype, filters, fields, order_by, limit_page_length):
		assert doctype == "Item Group" and fields == ["name"]
		assert order_by == "lft" and limit_page_length == 0
		expected = {"lft": [">=", 10], "rgt": ["<=", 30]}
		if excluded_groups:
			expected["name"] = ["not in", excluded_groups]
		assert filters == expected
		return [{"name": "VISIBLE-GROUP"}]

	monkeypatch.setattr(service, "frappe", SimpleNamespace(
		get_list=get_list,
		db=SimpleNamespace(sql=lambda *_args, **_kwargs: pytest.fail("group options must obey native permissions")),
	))
	assert service._list_category_item_groups({
		"group_lft": 10, "group_rgt": 30, "excluded_groups": excluded_groups,
	}) == ["VISIBLE-GROUP"]


def test_group_options_do_not_replace_permission_errors_with_an_unfiltered_query(monkeypatch):
	def denied(*_args, **_kwargs):
		raise PermissionError("group read denied")

	monkeypatch.setattr(service, "frappe", SimpleNamespace(get_list=denied))
	with pytest.raises(PermissionError, match="group read denied"):
		service._list_category_item_groups({"group_lft": 1, "group_rgt": 20})


def test_material_category_excludes_finished_semi_finished_and_mold_groups(monkeypatch) -> None:
	bounds = {
		"半成品Semiterminado": {"lft": 10, "rgt": 19},
		"成品Producto terminado": {"lft": 20, "rgt": 29},
		"模具Moldes": {"lft": 30, "rgt": 39},
	}

	class FakeDB:
		@staticmethod
		def get_value(_doctype, name, _fields, as_dict=False):
			return bounds.get(name)

		@staticmethod
		def sql(_query, _values, as_dict=False):
			return [{"name": name} for name in bounds]

	class FakeDoc:
		@staticmethod
		def check_permission(_permission):
			return None

	framework = SimpleNamespace(
		db=FakeDB(), has_permission=lambda *_args: True,
		get_doc=lambda *_args: FakeDoc(),
	)
	monkeypatch.setattr(service, "frappe", framework)
	monkeypatch.setattr(service, "_list_category_item_groups", lambda _context: ["Raw Group"])
	monkeypatch.setattr(service, "_accessible_category_items", lambda _groups: ("RAW-1",))
	monkeypatch.setattr(service, "_accessible_warehouses", lambda _company: ("W1",))
	monkeypatch.setattr(service, "_latest_snapshot_key", lambda _company: "LATEST")
	context = service._category_query_context("material", {"company": "YUEWEI MX", "snapshot_key": "OLDER"})
	assert set(context["excluded_groups"]) == set(bounds)
	assert context["items"] == ("RAW-1",)
	assert context["snapshot_key"] == "OLDER"
	with pytest.raises(ValueError, match="不属于物料库存明细"):
		service._category_query_context("material", {"company": "YUEWEI MX", "item_group": "模具Moldes"})


def test_parent_warehouse_scope_intersects_authorized_descendants(monkeypatch) -> None:
	checked = []
	parent = SimpleNamespace(company="C1", is_group=1, lft=10, rgt=20,
		check_permission=lambda permission: checked.append(permission))

	def get_list(doctype, filters, fields, limit_page_length):
		assert doctype == "Warehouse" and fields == ["name"] and limit_page_length == 0
		assert filters == {"company": "C1", "is_group": 0, "lft": [">", 10], "rgt": ["<", 20]}
		return [{"name": "W1"}, {"name": "HIDDEN-CHILD"}]

	monkeypatch.setattr(service, "frappe", SimpleNamespace(
		get_doc=lambda doctype, name: parent if (doctype, name) == ("Warehouse", "PARENT") else pytest.fail(name),
		get_list=get_list,
	))
	assert service._category_warehouse_scope("C1", ("W1", "OTHER-PARENT-CHILD"), "PARENT") == (("W1",), True)
	assert checked == ["read"]
	# Existing leaf selections retain their exact filter and need no group lookup.
	assert service._category_warehouse_scope("C1", ("W1",), "W1") == (("W1",), False)
	assert service._category_warehouse_scope("C1", ("W1",), "") == (("W1",), False)


@pytest.mark.parametrize("company,is_group,readable", [("C2", 1, True), ("C1", 0, True), ("C1", 1, False)])
def test_parent_warehouse_scope_rejects_cross_company_and_denied_warehouses(monkeypatch, company, is_group, readable):
	def check_permission(_permission):
		if not readable:
			raise PermissionError("denied")

	def throw(message, exception):
		raise exception(message)

	parent = SimpleNamespace(company=company, is_group=is_group, check_permission=check_permission)
	monkeypatch.setattr(service, "frappe", SimpleNamespace(
		get_doc=lambda *_args: parent, PermissionError=PermissionError, throw=throw,
		get_list=lambda **_kwargs: pytest.fail("must reject before loading descendants"),
	))
	with pytest.raises(PermissionError):
		service._category_warehouse_scope("C1", ("W1",), "PARENT")


@pytest.mark.parametrize("category", list(service.CATEGORY_DEFINITIONS))
def test_parent_filter_works_in_paged_results_and_export_without_losing_leaf_identity(monkeypatch, category):
	queries = []
	context = {"company": "C1", "items": ("ITEM",), "warehouses": ("W1", "W2"),
		"warehouse_is_group": True, "snapshot_key": "SNAP", "group_lft": 1, "group_rgt": 6,
		"item_group_options": [], "excluded_groups": ()}
	stock = [{"item_code": "ITEM", "warehouse": name, "actual_qty": qty}
		for name, qty in (("W1", 2), ("W2", 0))]
	snapshots = [{"item_code": "ITEM", "warehouse": name, "original_location": "OLD-01", "location_qty": 1}
		for name in ("W1", "W2")]

	def sql(query, values, as_dict):
		queries.append(query)
		assert values["warehouses"] == ("W1", "W2")
		assert "candidate.warehouse IN %(warehouses)s" in query
		assert "candidate.warehouse = %(warehouse)s" not in query
		return [{"total": 2}] if "COUNT(*) AS total" in query else stock

	monkeypatch.setattr(service, "frappe", SimpleNamespace(db=SimpleNamespace(sql=sql)))
	monkeypatch.setattr(service, "_category_query_context", lambda *_args: context)
	monkeypatch.setattr(service, "_paged_category_snapshots", lambda *_args: snapshots)
	monkeypatch.setattr(service, "_load_bom_usage_statuses", lambda _codes: {"ITEM": "used"})
	monkeypatch.setattr(service, "_category_snapshot_summary", lambda _context: {})
	monkeypatch.setattr(service, "_list_snapshot_options", lambda _company: [])
	monkeypatch.setattr(service, "_movement_permission_payload", lambda: {})
	filters = {"company": "C1", "warehouse": "PARENT", "only_with_stock": 0, "reference_location": "OLD-01"}
	payload = service._build_paged_category_payload(category, filters, 0, 200)
	assert payload["total_count"] == payload["page_count"] == 2
	assert [(row["warehouse"], row["actual_qty"]) for row in payload["groups"]] == [("W1", 2), ("W2", 0)]
	exported = list(service._iter_categorized_export_groups(category, filters, context))
	assert [row["warehouse"] for row in exported] == ["W1", "W2"]
	assert len(queries) == 4
	# The same scope is used by the older categorized endpoint's BOM/location filter.
	effective = service._category_payload_filters(filters, context)
	assert service._category_bom_candidate_codes(stock, snapshots, effective) == {"ITEM"}
	assert not service._matches_category_filters({"warehouse": "OUTSIDE"}, effective)
	assert not service._matches_category_filters({"warehouse": ""}, effective)


def test_empty_parent_scope_returns_no_inventory_or_placeholder_rows(monkeypatch):
	monkeypatch.setattr(service, "frappe", SimpleNamespace(
		db=SimpleNamespace(sql=lambda *_args, **_kwargs: pytest.fail("empty scope must not query stock"))))
	context = {"warehouses": (), "warehouse_is_group": True, "items": ("ITEM",)}
	filters = {"warehouse": "EMPTY-PARENT", "only_with_stock": 0}
	assert service._paged_category_candidates(context, filters) == ([], 0)
	effective = service._category_payload_filters(filters, context)
	payload = service.build_categorized_inventory_payload(
		[{"item_code": "ITEM", "warehouse": "", "actual_qty": 0}], [], category="semi_finished", filters=effective)
	assert payload["groups"] == []
	assert service._category_payload_filters({"_warehouse_scope": ["FORGED"]}, {})["_warehouse_scope"] is None


@pytest.mark.parametrize("category", ["semi_finished", "finished_goods", "mold"])
def test_product_group_display_uses_category_branch_above_filtered_process_in_page_and_export(monkeypatch, category):
	root = service.CATEGORY_DEFINITIONS[category]["root_item_group"]
	queries = []
	product = {"name": "PRODUCT-A", "lft": 2, "rgt": 20}
	process = {"name": "PROCESS-LEAF", "lft": 8, "rgt": 9}

	def get_list(doctype, filters, fields, limit_page_length, **_kwargs):
		assert doctype == "Item Group" and limit_page_length == 0
		queries.append(filters)
		if "name" in filters:
			assert filters == {"name": ["in", ("PROCESS-LEAF",)]}
			return [process]
		assert filters == {"parent_item_group": root}
		return [product]

	monkeypatch.setattr(service, "frappe", SimpleNamespace(get_list=get_list))
	context = {"company": "C1", "item_group_options": ["PROCESS-LEAF"]}
	stock = [{"item_code": "I1", "warehouse": "W1", "actual_qty": 3, "item_group": "PROCESS-LEAF"}]
	monkeypatch.setattr(service, "_category_query_context", lambda *_args: context)
	monkeypatch.setattr(service, "_paged_category_candidates", lambda *_args, **_kwargs: (stock, 1))
	monkeypatch.setattr(service, "_paged_category_snapshots", lambda *_args: [])
	monkeypatch.setattr(service, "_load_bom_usage_statuses", lambda _codes: {"I1": "unused"})
	monkeypatch.setattr(service, "_category_snapshot_summary", lambda _context: {})
	monkeypatch.setattr(service, "_movement_permission_payload", lambda: {})
	filters = {"company": "C1", "item_group": "PROCESS-LEAF", "only_with_stock": 1}
	page = service._build_paged_category_payload(category, filters, 0, 200)
	assert page["total_count"] == page["page_count"] == 1
	group = page["groups"][0]
	assert group["display_item_group"] == "PRODUCT-A"
	assert group["item_group"] == "PROCESS-LEAF" and group["actual_qty"] == 3
	assert filters["item_group"] == "PROCESS-LEAF"
	exported = list(service._iter_categorized_export_groups(category, filters, context))
	assert exported[0]["item_group"] == "PROCESS-LEAF"
	assert service._category_export_values(exported[0])[7] == "PRODUCT-A"
	assert len(queries) == 4  # Two batched lookups for each of the page and export.


def test_product_group_display_handles_direct_members_deep_descendants_and_unmatched_groups(monkeypatch):
	sources = [
		{"name": "PRODUCT-A", "lft": 2, "rgt": 20},
		{"name": "DEEP-PROCESS", "lft": 10, "rgt": 11},
		{"name": "CATEGORY-ROOT", "lft": 1, "rgt": 30},
		{"name": "UNMATCHED", "lft": 40, "rgt": 41},
	]
	product = sources[0]
	queries = []

	def get_list(_doctype, filters, **_kwargs):
		queries.append(filters)
		return sources if "name" in filters else [product]

	monkeypatch.setattr(service, "frappe", SimpleNamespace(get_list=get_list))
	payload = {"groups": [{"item_group": source["name"]} for source in sources] * 20}
	service._attach_display_item_groups(payload, "semi_finished")
	assert [group["display_item_group"] for group in payload["groups"][:4]] == [
		"PRODUCT-A", "PRODUCT-A", "CATEGORY-ROOT", "UNMATCHED"
	]
	assert payload["groups"][1]["item_group"] == "DEEP-PROCESS"
	assert len(queries) == 2
	assert len(queries[0]["name"][1]) == 4  # Deduplicate groups across warehouse rows.


@pytest.mark.parametrize("readable_source", [False, True])
def test_unreadable_product_ancestors_fall_back_to_actual_group(monkeypatch, readable_source):
	queries = []

	def get_list(_doctype, filters, **_kwargs):
		queries.append(filters)
		if "name" in filters and readable_source:
			return [{"name": "PROCESS", "lft": 4, "rgt": 5}]
		return []

	monkeypatch.setattr(service, "frappe", SimpleNamespace(get_list=get_list))
	payload = {"groups": [{"item_group": "PROCESS"}]}
	service._attach_display_item_groups(payload, "mold")
	assert payload["groups"][0] == {"item_group": "PROCESS", "display_item_group": "PROCESS"}
	assert len(queries) == (2 if readable_source else 1)


def test_material_group_display_keeps_its_actual_group_without_ancestor_queries(monkeypatch):
	monkeypatch.setattr(service, "frappe", SimpleNamespace(
		get_list=lambda *_args, **_kwargs: pytest.fail("material must not query product ancestors")))
	payload = {"groups": [{"item_group": "RAW-MATERIAL-GROUP"}]}
	service._attach_display_item_groups(payload, "material")
	assert payload["groups"][0] == {
		"item_group": "RAW-MATERIAL-GROUP", "display_item_group": "RAW-MATERIAL-GROUP"
	}


@pytest.mark.parametrize("category,present,readable,expected", [
	("semi_finished", True, True, "custom_specifications"),
	("finished_goods", True, True, "custom_specifications"),
	("mold", True, True, ""),
	("material", True, True, ""),
	("semi_finished", False, True, ""),
	("finished_goods", False, True, ""),
	("semi_finished", True, False, ""),
	("finished_goods", True, False, ""),
])
def test_inventory_color_uses_the_existing_readable_specifications_field_only_for_two_categories(
	monkeypatch, category, present, readable, expected
):
	def get_meta(doctype):
		assert doctype == "Item"
		assert category in service.COLOR_INVENTORY_CATEGORIES
		return SimpleNamespace(
			has_field=lambda name: name == "custom_specifications" and present,
			get_permitted_fieldnames=lambda permission_type: ["custom_specifications"] if readable else [],
		)

	monkeypatch.setattr(service, "frappe", SimpleNamespace(get_meta=get_meta))
	assert service._inventory_color_field(category) == expected


@pytest.mark.parametrize("paged", [False, True])
@pytest.mark.parametrize("readable_color", [False, True])
def test_inventory_queries_read_color_without_requiring_a_new_or_inaccessible_column(monkeypatch, paged, readable_color):
	queries = []
	color = "Negro" if readable_color else ""

	def sql(query, params, as_dict):
		queries.append(query)
		if "COUNT(*) AS total" in query:
			return [{"total": 1}]
		return [{"item_code": "I1", "item_name": "Item", "warehouse": "W1", "actual_qty": 3, "color": color}]

	monkeypatch.setattr(service, "frappe", SimpleNamespace(db=SimpleNamespace(sql=sql)))
	context = {"company": "C1", "warehouses": ("W1",), "items": ("I1",), "snapshot_key": "",
		"group_lft": 1, "group_rgt": 20, "color_field": "custom_specifications" if readable_color else ""}
	if paged:
		rows, _total = service._paged_category_candidates(context, {"only_with_stock": 1})
	else:
		rows = service._load_category_stock_rows(context)
	assert rows[0]["color"] == color
	assert ("item.custom_specifications" in queries[-1]) is readable_color
	payload = service.build_categorized_inventory_payload(rows, [], category="semi_finished")
	assert payload["groups"][0]["color"] == color
	assert payload["groups"][0]["actual_qty"] == 3


def test_color_is_preserved_on_snapshot_only_warehouse_groups_without_changing_quantity():
	stock = [{"item_code": "I1", "warehouse": "W1", "actual_qty": 3, "color": "Negro"}]
	snapshots = [{"item_code": "I1", "warehouse": "W2", "original_location": "OLD", "location_qty": 9}]
	payload = service.build_categorized_inventory_payload(stock, snapshots, category="semi_finished")
	assert [(row["warehouse"], row["actual_qty"], row["color"]) for row in payload["groups"]] == [
		("W1", 3, "Negro"), ("W2", 0, "Negro")
	]


def test_cross_company_inventory_intersects_native_company_and_warehouse_permissions(monkeypatch):
	queries = []

	def get_list(doctype, fields, limit_page_length, filters=None):
		queries.append(doctype)
		assert limit_page_length == 0
		if doctype == "Company":
			assert fields == ["name"]
			return [{"name": "C1"}, {"name": "C2"}]
		assert doctype == "Warehouse" and fields == ["name", "company"]
		assert filters == {"company": ["in", ("C1", "C2")], "is_group": 0}
		return [{"name": "WA", "company": "C1"}, {"name": "WB", "company": "C2"},
			{"name": "FORBIDDEN", "company": "C3"}]

	monkeypatch.setattr(service, "frappe", SimpleNamespace(get_list=get_list))
	assert service._accessible_inventory_companies_and_warehouses() == (
		("C1", "C2"), {"WA": "C1", "WB": "C2"}
	)
	assert queries == ["Company", "Warehouse"]


@pytest.mark.parametrize("category", list(service.CATEGORY_DEFINITIONS))
def test_cross_company_context_is_independent_of_operating_company_and_ignores_snapshots(monkeypatch, category):
	monkeypatch.setattr(service, "frappe", SimpleNamespace(
		db=SimpleNamespace(get_value=lambda *_args, **_kwargs: None)))
	monkeypatch.setattr(service, "_category_group_bounds", lambda *_args: (1, 20))
	monkeypatch.setattr(service, "_accessible_inventory_companies_and_warehouses",
		lambda: (("C1", "C2"), {"WA": "C1", "WB": "C2"}))
	monkeypatch.setattr(service, "_accessible_warehouses", lambda *_args: pytest.fail("must not restrict to operating company"))
	monkeypatch.setattr(service, "_list_category_item_groups", lambda *_args: ["G"])
	monkeypatch.setattr(service, "_accessible_category_items", lambda *_args: ("I1",))
	monkeypatch.setattr(service, "_inventory_color_field", lambda *_args: "")
	monkeypatch.setattr(service, "_latest_snapshot_key", lambda *_args: pytest.fail("cross company stock must not use one company's snapshot"))
	for company in ("C1", "C2"):
		context = service._category_query_context(category, {"company": company, "all_companies": 1, "snapshot_key": "OLD"})
		assert context["company"] == company
		assert context["warehouses"] == ("WA", "WB")
		assert context["companies"] == ("C1", "C2") and context["all_companies"]
		assert context["snapshot_key"] == ""


@pytest.mark.parametrize("only_stock", [0, 1])
def test_cross_company_sql_keeps_individual_warehouse_companies_and_quantities(monkeypatch, only_stock):
	queries = []
	stock = [{"item_code": "I1", "warehouse": "WA", "company": "C1", "actual_qty": 2},
		{"item_code": "I1", "warehouse": "WB", "company": "C2", "actual_qty": 3}]

	def sql(query, params, as_dict):
		queries.append(query)
		assert params["companies"] == ("C1", "C2") and params["warehouses"] == ("WA", "WB")
		assert "stock_owner.company IN %(companies)s" in query
		if "COUNT(*) AS total" in query:
			return [{"total": 2}]
		assert "warehouse.company IN %(companies)s" in query
		assert "warehouse.company = %(company)s" not in query
		assert "parent.company = warehouse.company" in query
		return stock

	monkeypatch.setattr(service, "frappe", SimpleNamespace(db=SimpleNamespace(sql=sql)))
	context = {"company": "C1", "companies": ("C1", "C2"), "all_companies": True,
		"warehouses": ("WA", "WB"), "items": ("I1",), "snapshot_key": "", "group_lft": 1, "group_rgt": 10}
	rows, total = service._paged_category_candidates(context, {"all_companies": 1, "only_with_stock": only_stock})
	assert total == 2 and len(queries) == 2
	groups = service.build_categorized_inventory_payload(rows, [], category="material")["groups"]
	assert [(row["company"], row["warehouse"], row["actual_qty"]) for row in groups] == [
		("C1", "WA", 2), ("C2", "WB", 3)
	]


def test_cross_company_parent_filter_uses_the_parent_owning_company(monkeypatch):
	parent = SimpleNamespace(company="C2", is_group=1, lft=10, rgt=20, check_permission=lambda *_args: None)

	def get_list(_doctype, filters, **_kwargs):
		assert filters == {"company": "C2", "is_group": 0, "lft": [">", 10], "rgt": ["<", 20]}
		return [{"name": "WB"}, {"name": "HIDDEN"}]

	def throw(message, exception):
		raise exception(message)

	monkeypatch.setattr(service, "frappe", SimpleNamespace(
		get_doc=lambda *_args: parent, get_list=get_list, throw=throw, PermissionError=PermissionError))
	assert service._category_warehouse_scope("C1", ("WA", "WB"), "PARENT-C2", allowed_companies=("C1", "C2")) == (("WB",), True)
	with pytest.raises(PermissionError):
		service._category_warehouse_scope("C1", ("WA", "WB"), "PARENT-C2", allowed_companies=("C1",))


def test_cross_company_empty_permission_scope_returns_no_stock_or_item_placeholders(monkeypatch):
	monkeypatch.setattr(service, "frappe", SimpleNamespace(
		db=SimpleNamespace(sql=lambda *_args, **_kwargs: pytest.fail("empty permission scope must not query"))))
	context = {"all_companies": True, "items": ("I1",), "warehouses": ()}
	assert service._paged_category_candidates(context, {"only_with_stock": 0}) == ([], 0)
	assert service._load_category_stock_rows(context) == []


def test_cross_company_view_does_not_allow_a_foreign_warehouse_in_stock_movements(monkeypatch):
	warehouse = SimpleNamespace(company="C2", is_group=0, check_permission=lambda *_args: None)
	monkeypatch.setattr(service, "frappe", SimpleNamespace(get_doc=lambda *_args: warehouse))
	monkeypatch.setattr(service, "_require_stock_entry_create_permission", lambda: None)
	monkeypatch.setattr(service, "_require_company_permission", lambda *_args: None)
	monkeypatch.setattr(service, "_validate_item", lambda *_args: SimpleNamespace(item_name="Item", stock_uom="kg"))
	monkeypatch.setattr(service, "_actual_qty", lambda *_args: pytest.fail("must reject before reading another company's stock"))
	with pytest.raises(ValueError, match="仓库不属于当前公司"):
		service.get_inventory_movement_context("C1", [{"item_code": "I1", "source_warehouse": "WB"}])
	with pytest.raises(ValueError, match="仓库不属于当前公司"):
		service._attach_reversal_progress({"company": "C1", "groups": []}, [{"item_code": "I1", "source_warehouse": "WB"}])
