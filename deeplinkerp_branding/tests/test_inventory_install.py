"""Inventory capability ownership and migration installation tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from deeplinkerp_branding import inventory_install

ROOT = Path(__file__).resolve().parents[1]


class FakeSidebar:
	def __init__(self) -> None:
		self.items = [
			SimpleNamespace(
				label="可用数量", link_type="Report", link_to="Stock Projected Qty", icon="package", idx=1
			),
			SimpleNamespace(label="储位", link_type="DocType", link_to="Bin", icon="warehouse", idx=2),
		]

	def get(self, fieldname: str):
		assert fieldname == "items"
		return self.items

	def append(self, fieldname: str, values: dict):
		assert fieldname == "items"
		row = SimpleNamespace(**values)
		self.items.append(row)
		return row


def test_sidebar_keeps_standard_entries_and_adds_the_four_inventory_pages() -> None:
	sidebar = FakeSidebar()
	assert inventory_install.upsert_stock_sidebar_inventory_pages(sidebar) is True
	assert [(row.label, row.link_to) for row in sidebar.items] == [
		("可用数量", "Stock Projected Qty"),
		("物料库存明细", "inventory-location-detail"),
		("半成品库存明细", "semi-finished-inventory-detail"),
		("成品库存明细", "finished-goods-inventory-detail"),
		("模具库存明细", "mold-inventory-detail"),
		("储位", "Bin"),
	]
	assert inventory_install.upsert_stock_sidebar_inventory_pages(sidebar) is False


def test_snapshot_doctype_keeps_its_name_and_data_contract_but_changes_module_owner() -> None:
	path = (
		ROOT
		/ "deeplinkerp_branding/doctype/inventory_original_location_snapshot/inventory_original_location_snapshot.json"
	)
	definition = json.loads(path.read_text(encoding="utf-8"))
	assert definition["name"] == "Inventory Original Location Snapshot"
	assert definition["module"] == "Deeplinkerp Branding"
	assert definition["autoname"] == "field:idempotency_key"
	assert definition["allow_import"] == 1
	assert {row["fieldname"] for row in definition["fields"]} >= {
		"snapshot_key",
		"company",
		"item_code",
		"warehouse",
		"location_qty",
		"idempotency_key",
	}


def test_inventory_trace_fields_are_owned_by_branding_installation() -> None:
	fields = inventory_install.get_inventory_trace_custom_fields()
	assert set(fields) == {"Item"}
	assert {row["fieldname"] for row in fields["Item"]} == {
		"custom_dpci",
		"custom_external_code",
		"custom_original_identifier_alias",
	}


def test_branding_declares_and_ci_installs_erpnext_dependency() -> None:
	hooks = (ROOT / "hooks.py").read_text(encoding="utf-8")
	workflow = (ROOT.parent / ".github/workflows/ci.yml").read_text(encoding="utf-8")

	assert 'required_apps = ["erpnext"]' in hooks
	assert "FRAPPE_RELEASE: v16.23.0" in workflow
	assert "ERPNEXT_RELEASE: v16.23.0" in workflow
	assert '--frappe-branch "$FRAPPE_RELEASE"' in workflow
	assert 'bench get-app --branch "$ERPNEXT_RELEASE" erpnext' in workflow
	assert "OVERSEAS_COSTING_COMMIT: a8070a92fb963f7fad97ef40bc2cdac90e4b10bc" in workflow
	assert (
		'git clone --depth 1 --no-checkout --branch main https://github.com/yueweiit/deeplinkerp-overseas_costing.git "$costing_source"'
		in workflow
	)
	assert 'git -C "$costing_source" fetch --depth 1 origin "$OVERSEAS_COSTING_COMMIT"' in workflow
	checkout = 'git -C "$costing_source" checkout --detach "$OVERSEAS_COSTING_COMMIT"'
	install = 'bench get-app --skip-assets overseas_costing "$costing_source"'
	assert checkout in workflow and install in workflow
	assert workflow.index(checkout) < workflow.index(install)
	assert 'test "$(git -C apps/overseas_costing rev-parse HEAD)" = "$OVERSEAS_COSTING_COMMIT"' in workflow
	assert "from deeplinkerp_branding.services.purchase_native_repost import install" in workflow
	assert "install();" in workflow
	assert workflow.index("bench --site test_site install-app erpnext") < workflow.index(
		"bench --site test_site install-app deeplinkerp_branding"
	)
