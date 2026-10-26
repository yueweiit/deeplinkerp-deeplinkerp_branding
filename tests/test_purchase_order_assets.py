import re
import unittest
from pathlib import Path

from deeplinkerp_branding import hooks


def selector_specificity(selector):
	"""Count the selectors used here, including native :not ID and pseudo-element rules."""
	return (
		len(re.findall(r"#[\w-]+", selector)),
		len(re.findall(r"\.[\w-]+|\[[^]]+\]", selector)) + selector.count(":first-child"),
		len(re.findall(r"(?:^|\s)body(?=[.\[:]|$)|::[\w-]+", selector)),
	)


def table_style_rules():
	css = (Path(__file__).resolve().parents[1] / "deeplinkerp_branding/public/css/purchase_order_list.css").read_text()
	css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
	css = re.sub(r":is\([^)]*\)", ".dlp-purchase-order-grid-active", css)
	return [(selector.strip(), rule) for selectors, rule in re.findall(r"([^{}]+)\{([^}]+)\}", css) for selector in selectors.split(",")]


class PurchaseOrderAssetsTest(unittest.TestCase):
	def test_ci_runs_navigation_and_purchase_order_regressions(self):
		workflow = (
			Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"
		).read_text()
		self.assertIn("node --test tests/*.test.js", workflow)
		for suite in (
			"tests.test_interface_mode",
			"tests.test_boot_branding",
			"tests.test_interface_mode_metadata",
			"tests.test_purchase_order_assets",
		):
			self.assertIn(suite, workflow)

	def test_shared_business_date_adapter_reuses_native_lists_without_replacing_compact_adapters(self):
		self.assertEqual(
			getattr(hooks, "doctype_list_js", {}),
			{"Purchase Order": "public/js/purchase_order_list.js", "Material Request": "public/js/material_request_list.js", "Purchase Receipt": "public/js/purchase_receipt_list.js", "Sales Order": "public/js/sales_order_list.js", **{doctype: "public/js/compact_list.js" for doctype in ("Request for Quotation", "Supplier Quotation", "Quotation", "Purchase Invoice", "Payment Entry", "Sales Invoice", "Delivery Note", "POS Invoice", "POS Opening Entry", "POS Closing Entry", "OA Purchase Request")}},
		)

	def test_unified_adapter_loads_after_cache_busted_shared_engine(self):
		scripts = hooks.app_include_js
		engine = "/assets/deeplinkerp_branding/js/compact_list.js?v=0.0.31"
		adapter = "/assets/deeplinkerp_branding/js/unified_purchase_list.js?v=0.0.13"
		self.assertIn(engine, scripts)
		self.assertIn(adapter, scripts)
		self.assertLess(scripts.index(engine), scripts.index(adapter))
		payments = next(path for path in scripts if path.startswith("/assets/deeplinkerp_branding/js/purchase_payments.js?v="))
		self.assertLess(scripts.index(engine), scripts.index(payments))
		source = "/assets/deeplinkerp_branding/js/purchase_source.js?v=0.0.3"
		self.assertLess(scripts.index(payments), scripts.index(source))
		self.assertLess(scripts.index(source), scripts.index(adapter))

	def test_crossborder_drawer_reuses_the_loaded_payment_shell_once(self):
		scripts = hooks.app_include_js
		payments = "/assets/deeplinkerp_branding/js/purchase_payments.js?v=0.0.31"
		crossborder = "/assets/deeplinkerp_branding/js/crossborder_procurement.js?v=0.0.5"
		self.assertIn(payments, scripts)
		self.assertIn(crossborder, scripts)
		self.assertEqual(sum("/crossborder_procurement.js" in path for path in scripts), 1)
		self.assertLess(scripts.index(payments), scripts.index(crossborder))
		self.assertLess(scripts.index(crossborder), scripts.index("/assets/deeplinkerp_branding/js/unified_purchase_list.js?v=0.0.13"))
		self.assertIn("/assets/deeplinkerp_branding/css/purchase_payments.css?v=0.0.10", hooks.app_include_css)
		self.assertNotIn("crossborder_procurement", str(getattr(hooks, "web_include_js", None)))

	def test_desk_loads_compact_styles_without_changing_website_assets(self):
		styles = hooks.app_include_css
		if isinstance(styles, str):
			styles = [styles]
		self.assertTrue(any("deeplinkerp_navigation.css" in style for style in styles))
		self.assertEqual(
			len([style for style in styles if "purchase_order_list.css" in style]), 1
		)
		self.assertNotIn("purchase_order_list", str(hooks.web_include_css))

	def test_procurement_keeps_readable_sidebar_and_refreshes_styles(self):
		css = (Path(__file__).resolve().parents[1] / "deeplinkerp_branding/public/css/purchase_order_list.css").read_text()
		self.assertNotRegex(css, r"body\.dlp-purchase-order-grid-active:not\(\.dlp-mes-navigation-enabled\) \.body-sidebar")
		self.assertIn("/assets/deeplinkerp_branding/css/purchase_order_list.css?v=0.0.27", hooks.app_include_css)

	def test_purchase_source_identity_stays_one_line_in_the_physical_table(self):
		css = (Path(__file__).resolve().parents[1] / "deeplinkerp_branding/public/css/purchase_order_list.css").read_text()
		selector = r"body\.dlp-purchase-order-grid-active \.dlp-purchase-table button\[data-purchase-source\]"
		rule = re.search(selector + r"\s*\{([^}]+)\}", css)
		self.assertIsNotNone(rule, "The physical OA identity overrides the native fixed button height")
		for property_name, value in (("display", "block"), ("height", "auto"), ("white-space", "nowrap"), ("overflow", "hidden"), ("text-overflow", "ellipsis"), ("text-align", "left"), ("max-width", "100%")):
			self.assertRegex(rule.group(1), re.escape(property_name) + r":\s*" + re.escape(value) + r"\s*;")

	def test_header_and_rows_do_not_distribute_extra_width_between_columns(self):
		css = (
			Path(__file__).resolve().parents[1]
			/ "deeplinkerp_branding/public/css/purchase_order_list.css"
		).read_text()
		css = re.sub(r"body:is\([^)]*\.dlp-purchase-order-grid-active[^)]*\)", "body.dlp-purchase-order-grid-active", css)
		shared_grid_rule = re.search(
			r"\.dlp-po-grid-header-columns,\s*"
			r"body\.dlp-purchase-order-grid-active \.dlp-po-grid-row\s*\{([^}]+)\}",
			css,
		)
		self.assertIsNotNone(shared_grid_rule)
		self.assertRegex(
			shared_grid_rule.group(1),
			r"justify-content:\s*start\s*;",
			"Native .level space-between must not offset data columns on wide screens",
		)
		self.assertRegex(shared_grid_rule.group(1), r"font-size:\s*inherit\s*;")
		header_rule = re.search(r"\.dlp-po-grid-header\s*\{([^}]+)\}", css)
		self.assertIsNotNone(header_rule)
		self.assertRegex(header_rule.group(1), r"font-size:\s*inherit\s*;")

	def test_business_compact_density_and_company_wrapping_preserve_standard_and_detail_clamps(self):
		css = (Path(__file__).resolve().parents[1] / "deeplinkerp_branding/public/css/purchase_order_list.css").read_text()
		compact = re.search(r"body:is\([^)]*\) \.dlp-po-grid:not\(\.dlp-po-standard\)\s*\{([^}]+)\}", css)
		self.assertIsNotNone(compact)
		self.assertRegex(compact.group(1), r"font-size:\s*12px;")
		self.assertRegex(compact.group(1), r"--dlp-po-cell-padding:\s*3px 5px;")
		standard = re.search(r"\.dlp-po-standard \.dlp-po-grid\s*\{([^}]+)\}", css)
		self.assertRegex(standard.group(1), r"font-size:\s*14px;")
		self.assertRegex(standard.group(1), r"--dlp-po-cell-padding:\s*7px 9px;")
		company = next((rule for selector, rule in table_style_rules() if selector.endswith('.dlp-po-grid-row [data-fieldname="company"]')), None)
		self.assertIsNotNone(company)
		for property_name, value in (("white-space", "normal"), ("overflow", "visible"), ("text-overflow", "clip"), ("overflow-wrap", "anywhere")):
			self.assertRegex(company, re.escape(property_name) + r":\s*" + value + r";")
		self.assertRegex(css, r"\.dlp-purchase-detail-text\s*\{[^}]*-webkit-line-clamp:\s*3;")

	def test_header_outer_stack_beats_native_first_row_and_both_body_sticky_columns(self):
		css = (Path(__file__).resolve().parents[1] / "deeplinkerp_branding/public/css/purchase_order_list.css").read_text()
		css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
		css = re.sub(r"body:is\([^)]*\)", "body.dlp-purchase-order-grid-active", css)
		selector, rule = next((selector.strip(), rule) for selector, rule in re.findall(r"([^{}]+)\{([^}]+)\}", css) if ":has(> .dlp-po-grid-header)" in selector)
		native = ".layout-main-section-wrapper:not(.disable-scrolling) .frappe-list .result-container .result .list-row-container:first-child"
		self.assertGreater(selector_specificity(selector), selector_specificity(native), "The outer header stacking context must override the deployed native selector")
		self.assertGreater(int(re.search(r"z-index:\s*(\d+)", rule).group(1)), 4, "The body action column uses z-index 4")
		self.assertRegex(rule, r"position:\s*sticky\s*;")
		self.assertRegex(rule, r"top:\s*0\s*;")
		self.assertRegex(css, r"\.dlp-po-grid \.result-container[^{}]*,\s*\.inventory-detail \.id-table-wrap\s*\{[^}]*isolation:\s*isolate;")

	def test_clear_button_and_sales_viewport_styles_remain_scoped_and_mobile_freezing_is_cancelled(self):
		root = Path(__file__).resolve().parents[1] / "deeplinkerp_branding/public/css"
		css = (root / "purchase_order_list.css").read_text()
		self.assertRegex(css, r"body\.dlp-purchase-order-grid-active-readonly \.filter-section,")
		self.assertRegex(css, r"\.dlp-po-filters \.dlp-po-clear-filters\s*\{[^}]*display:\s*inline-flex\s*!important;")
		self.assertRegex(css[css.index("@media (max-width: 767.98px)"):], r"\.dlp-po-frozen\s*\{\s*position:\s*static;")
		sales = (root / "sales_order_list.css").read_text()
		self.assertNotIn("--dlp-sales-result-max-height", sales)
		self.assertRegex(sales, r"body\.dlp-sales-order-grid-active \.dlp-po-grid \.result-container\s*\{[^}]*max-height:\s*none;[^}]*overflow-x:\s*auto;[^}]*overflow-y:\s*hidden;")
		viewbar = re.search(r"\.dlp-sales-viewbar\s*\{([^}]+)\}", sales).group(1)
		self.assertIn("display:contents", viewbar)
		mode_menu = re.search(r"body\.dlp-sales-order-grid-active:not\(\.dlp-mes-navigation-enabled\) \.dlp-interface-mode-menu\s*\{([^}]+)\}", sales)
		self.assertIsNotNone(mode_menu, "The narrow classic Sales sidebar must keep its mode menu inside the viewport")
		self.assertRegex(mode_menu.group(1), r"left:\s*0\s*;")
		self.assertRegex(mode_menu.group(1), r"right:\s*auto\s*;")
		inventory = (root / "inventory_detail.bundle.css").read_text()
		mobile_inventory = inventory[inventory.index("@media (max-width: 767px)"):]
		self.assertRegex(mobile_inventory, r"max-height:\s*var\(--dlp-inventory-result-max-height,\s*calc\(100vh - 230px\)\);")

	def test_compact_and_inventory_tables_offer_normal_width_mouse_scrollbar_tracks(self):
		css = (Path(__file__).resolve().parents[1] / "deeplinkerp_branding/public/css/purchase_order_list.css").read_text()
		shared = r"[^{}]*\.dlp-po-grid \.result-container[^{}]*,\s*\.inventory-detail \.id-table-wrap"
		base = re.search(shared + r"\s*\{([^}]+)\}", css)
		self.assertIsNotNone(base, "Existing compact and inventory scrollers should share visible scrollbar styles")
		self.assertRegex(base.group(1), r"scrollbar-width:\s*auto;")
		self.assertRegex(base.group(1), r"scrollbar-gutter:\s*stable;")
		guard = r"(?::not\(#page-form-builder \*\))?"
		bar = re.search(shared.replace(".result-container", ".result-container" + guard + "::-webkit-scrollbar").replace(".id-table-wrap", ".id-table-wrap" + guard + "::-webkit-scrollbar") + r"\s*\{([^}]+)\}", css)
		self.assertIsNotNone(bar)
		self.assertRegex(bar.group(1), r"width:\s*12px;")
		self.assertRegex(bar.group(1), r"height:\s*12px;")
		for part in ("track", "thumb"):
			rule = re.search(r"\.inventory-detail \.id-table-wrap" + guard + "::-webkit-scrollbar-" + part + r"\s*\{([^}]+)\}", css)
			self.assertIsNotNone(rule)
			self.assertRegex(rule.group(1), r"background:\s*var\(--dlp-table-scrollbar-" + part)
		self.assertNotRegex(css, r"(?:^|\})\s*\.result-container::-webkit-scrollbar", "No unrelated native list scrollbar overrides")

	def test_mouse_scrollbar_selectors_outrank_the_actual_native_form_builder_guard(self):
		rules = table_style_rules()
		for component in (".dlp-po-grid .result-container", ".inventory-detail .id-table-wrap"):
			for suffix in ("", "-track", "-thumb"):
				pseudo = "::-webkit-scrollbar" + suffix
				selector, rule = next((selector, rule) for selector, rule in rules if component in selector and selector.endswith(pseudo))
				native = ":not(#page-form-builder *)" + pseudo if not suffix else pseudo
				self.assertGreater(selector_specificity(selector), selector_specificity(native), f"{component} {pseudo} must win native dimensions/colours")
				if not suffix:
					self.assertRegex(rule, r"width:\s*12px;")
					self.assertRegex(rule, r"height:\s*12px;")

	def test_dark_table_scrollbar_colours_outrank_the_light_component_defaults(self):
		rules = table_style_rules()
		for component in (".dlp-po-grid .result-container", ".inventory-detail .id-table-wrap"):
			colour_rules = [(selector, rule) for selector, rule in rules if component in selector and "--dlp-table-scrollbar-track:" in rule]
			light = max(selector_specificity(selector) for selector, _ in colour_rules if "[data-theme=" not in selector)
			dark = [(selector, rule) for selector, rule in colour_rules if '[data-theme="dark"]' in selector]
			self.assertTrue(dark, "Each table component must retain its dark scrollbar colours")
			for selector, rule in dark:
				self.assertGreater(selector_specificity(selector), light, "Dark colours must win the body-scoped light defaults")
				self.assertIn("--dlp-table-scrollbar-track: #263346;", rule)
				self.assertIn("--dlp-table-scrollbar-thumb: #8ca0b8;", rule)


if __name__ == "__main__":
	unittest.main()
