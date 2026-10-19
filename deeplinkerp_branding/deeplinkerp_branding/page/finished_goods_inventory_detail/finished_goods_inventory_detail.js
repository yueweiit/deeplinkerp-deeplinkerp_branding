(function () {
	"use strict";
 const translateText = (text, args = []) => String((globalThis.__ || (value => value))(text)).replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");
	const route = "finished-goods-inventory-detail";
	frappe.pages[route] = frappe.pages[route] || {};
	frappe.pages[route].on_page_load = function (wrapper) {
		globalThis.InventoryDetail.bootstrap(wrapper, {
			category: "finished_goods",
			title: translateText("成品库存明细"),
		});
	};
})();
