"""Inventory capability ownership and migration installation tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from deeplinkerp_branding import inventory_install

ROOT = Path(__file__).resolve().parents[1]


class FakeSidebar:
	def __init__(self) -> None:
		self.items = [
			SimpleNamespace(label="Home", type="Link", link_type="Workspace", link_to="Stock", idx=1),
			SimpleNamespace(label="Dashboard", type="Link", link_type="Dashboard", link_to="Stock", idx=2),
			SimpleNamespace(label="Bin", type="Link", link_type="DocType", link_to="Bin", idx=3),
			SimpleNamespace(label="Stock Entry", type="Link", link_type="DocType", link_to="Stock Entry", idx=4),
			SimpleNamespace(label="Reports", type="Section Break", link_type="DocType", link_to=None, idx=5),
			SimpleNamespace(label="Stock Projected Qty", type="Link", link_type="Report", link_to="Stock Projected Qty", idx=6),
			SimpleNamespace(label="Stock Analytics", type="Link", link_type="Report", link_to="Stock Analytics", idx=7),
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
		("Home", "Stock"),
		("Dashboard", "Stock"),
		("物料库存明细", "inventory-location-detail"),
		("半成品库存明细", "semi-finished-inventory-detail"),
		("成品库存明细", "finished-goods-inventory-detail"),
		("模具库存明细", "mold-inventory-detail"),
		("Stock Entry", "Stock Entry"),
		("Reports", None),
		("Stock Projected Qty", "Stock Projected Qty"),
		("Bin", "Bin"),
		("Stock Analytics", "Stock Analytics"),
	]
	assert inventory_install.upsert_stock_sidebar_inventory_pages(sidebar) is False


def test_existing_inventory_pages_move_out_of_reports_without_duplication() -> None:
	sidebar = FakeSidebar()
	for page in inventory_install.INVENTORY_DETAIL_PAGES:
		sidebar.append("items", {
			"label": page["label"], "type": "Link", "link_type": "Page",
			"link_to": page["link_to"], "idx": len(sidebar.items) + 1,
		})
	sidebar.items.append(sidebar.items.pop(6))
	assert inventory_install.upsert_stock_sidebar_inventory_pages(sidebar) is True
	assert [row.link_to for row in sidebar.items[2:6]] == [
		page["link_to"] for page in inventory_install.INVENTORY_DETAIL_PAGES
	]
	links = [row.link_to for row in sidebar.items]
	assert links.index("Bin") == links.index("Stock Projected Qty") + 1
	assert [row.idx for row in sidebar.items] == list(range(1, len(sidebar.items) + 1))
	assert inventory_install.upsert_stock_sidebar_inventory_pages(sidebar) is False


def test_sidebar_without_dashboard_keeps_home_first() -> None:
	sidebar = FakeSidebar()
	sidebar.items.pop(1)
	assert inventory_install.upsert_stock_sidebar_inventory_pages(sidebar) is True
	assert sidebar.items[0].link_to == "Stock"
	assert sidebar.items[0].link_type == "Workspace"
	assert [row.link_to for row in sidebar.items[1:5]] == [
		page["link_to"] for page in inventory_install.INVENTORY_DETAIL_PAGES
	]
	assert inventory_install.upsert_stock_sidebar_inventory_pages(sidebar) is False


def test_navigation_sync_updates_public_and_exact_user_copies(monkeypatch) -> None:
	public = FakeSidebar()
	private = FakeSidebar()
	other = FakeSidebar()
	sidebars = {
		"Stock": public,
		"Stock-user@example.com": private,
		"Stock-Other": other,
	}
	saved = []
	for name, sidebar in sidebars.items():
		sidebar.save = lambda ignore_permissions, name=name: saved.append(name)
	rows = [
		SimpleNamespace(name="Stock-user@example.com", title="Stock-user@example.com", module="Stock", for_user="user@example.com"),
		SimpleNamespace(name="Stock-Other", title="Stock-Other", module="Stock", for_user="another@example.com"),
		SimpleNamespace(name="Stock", title="Stock", module="Stock", for_user=None),
	]
	commits = []
	fake_frappe = SimpleNamespace(
		db=SimpleNamespace(exists=lambda doctype, name: True, commit=lambda: commits.append(True)),
		get_all=lambda *args, **kwargs: rows,
		get_doc=lambda doctype, name: sidebars[name],
	)
	monkeypatch.setitem(sys.modules, "frappe", fake_frappe)
	result = inventory_install.ensure_stock_sidebar_inventory_pages()
	assert result["updated_sidebars"] == ["Stock", "Stock-user@example.com"]
	assert saved == result["updated_sidebars"]
	assert len(commits) == 1
	assert [row.link_to for row in other.items] == ["Stock", "Stock", "Bin", "Stock Entry", None, "Stock Projected Qty", "Stock Analytics"]
	assert inventory_install.ensure_stock_sidebar_inventory_pages()["changed"] is False
	assert len(commits) == 1


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
