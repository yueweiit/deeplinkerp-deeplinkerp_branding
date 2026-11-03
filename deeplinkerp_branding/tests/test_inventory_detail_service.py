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
