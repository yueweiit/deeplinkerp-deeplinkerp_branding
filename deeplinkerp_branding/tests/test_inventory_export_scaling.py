from io import BytesIO
from unittest.mock import patch

import pytest
from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from deeplinkerp_branding.services import inventory_detail_service as service


@pytest.mark.parametrize("categorized", [False, True])
def test_export_formats_fixed_columns_without_scanning_all_existing_cells(monkeypatch, categorized):
	monkeypatch.setattr(service, "frappe", None)
	stock = [{"item_code": "I", "warehouse": "W", "actual_qty": 5, "stock_uom": "kg"}]
	snapshots = [
		{"item_code": "I", "warehouse": "W", "original_location": location, "location_qty": qty}
		for location, qty in [("L1", 2), ("L2", 3)]
	]
	if categorized:
		payload = service.build_categorized_inventory_payload(stock, snapshots, category="semi_finished")
		build = service.build_categorized_inventory_xlsx
	else:
		payload = service.build_inventory_location_payload(snapshots, {}, page_length=None)
		build = service.build_inventory_location_xlsx
	reads = 0
	original = Worksheet.max_column.fget

	def max_column(sheet):
		nonlocal reads
		reads += 1
		return original(sheet)

	with patch.object(Worksheet, "max_column", property(max_column)):
		content = build(payload)
	# Serialization may inspect dimensions, but formatting must not do it per row.
	assert reads <= 1
	book = load_workbook(BytesIO(content))
	sheet = book.active
	assert sheet.max_row == (2 if categorized else 3)
	assert sheet.max_column == (9 if categorized else 12)
	quantity_cell = sheet["G2" if categorized else "F2"]
	assert quantity_cell.value == 5
	assert quantity_cell.number_format == "#,##0.00"
	if categorized:
		assert not sheet.merged_cells.ranges
	else:
		assert sheet["E2"].value + sheet["E3"].value == 5
		assert "A2:A3" in sheet.merged_cells
		assert sheet["E3"].alignment.vertical == "center"
	assert sheet["A2"].alignment.wrap_text
	assert quantity_cell.alignment.vertical == "center"


@pytest.mark.parametrize("category", list(service.CATEGORY_DEFINITIONS))
@pytest.mark.parametrize("all_companies", [False, True])
def test_large_category_export_uses_streaming_and_keeps_all_rows(monkeypatch, category, all_companies):
	monkeypatch.setattr(service, "frappe", None)

	def groups():
		for index in range(service.EXPORT_STREAM_THRESHOLD + 1):
			yield {
				"item_code": f"ITEM-{index}", "item_name": "Item", "warehouse": "W",
				"actual_qty": 1, "difference_qty": None, "stock_uom": "kg",
				"bom_usage_status": "unused", "inventory_status": "无参考快照",
				"item_group": "PROCESS-LEAF", "display_item_group": "PRODUCT-A",
				"color": "Negro",
				"company": "C1" if index % 2 == 0 else "C2",
				"locations": [
					{"reference_location": "OLD-01", "snapshot_location_qty": 1},
					{"reference_location": "OLD-02", "snapshot_location_qty": 2},
				],
			}

	monkeypatch.setattr(service, "_iter_categorized_export_groups", lambda *_args: groups())
	context = {"all_companies": all_companies}
	content = service._build_categorized_export_content(category, {}, context)
	sheet = load_workbook(BytesIO(content)).active
	assert sheet.max_row == service.EXPORT_STREAM_THRESHOLD + 2
	assert sheet.cell(sheet.max_row, 1).value == f"ITEM-{service.EXPORT_STREAM_THRESHOLD}"
	offset = int(category in service.COLOR_INVENTORY_CATEGORIES)
	assert sheet.max_column == 8 + offset + int(all_companies)
	quantity_column = 5 + 2 * offset + int(all_companies)
	assert sheet.cell(sheet.max_row, quantity_column).value == 1
	assert sheet.cell(sheet.max_row, quantity_column).number_format == "#,##0.00"
	assert sheet.cell(sheet.max_row, 4 if offset else 8 + int(all_companies)).value == "PRODUCT-A"
	if all_companies:
		assert sheet.cell(sheet.max_row, 5 if offset else 3).value == "C1"
	assert sheet.cell(sheet.max_row, 3).value == ("Negro" if offset else "C1" if all_companies else "W")

	monkeypatch.setattr(service, "MAX_EXPORT_FILE_BYTES", 100)
	with pytest.raises(ValueError, match="导出文件超过"):
		service._build_categorized_export_content(category, {}, context)


@pytest.mark.parametrize("category", list(service.CATEGORY_DEFINITIONS))
def test_cross_company_export_keeps_same_item_in_two_companies_as_separate_rows(monkeypatch, category):
	monkeypatch.setattr(service, "frappe", None)
	groups = [{"item_code": "I1", "warehouse": "WA", "company": "C1", "actual_qty": 2, "stock_uom": "kg"},
		{"item_code": "I1", "warehouse": "WB", "company": "C2", "actual_qty": 3, "stock_uom": "kg"}]
	content = service.build_categorized_inventory_xlsx({"category": category, "all_companies": True, "groups": groups})
	sheet = load_workbook(BytesIO(content)).active
	colored = category in service.COLOR_INVENTORY_CATEGORIES
	company_column = 5 if colored else 3
	quantity_column = 8 if colored else 6
	assert sheet.max_row == 3
	assert sheet.cell(1, company_column).value == "公司"
	assert [sheet.cell(index, company_column).value for index in (2, 3)] == ["C1", "C2"]
	assert [sheet.cell(index, quantity_column).value for index in (2, 3)] == [2, 3]
