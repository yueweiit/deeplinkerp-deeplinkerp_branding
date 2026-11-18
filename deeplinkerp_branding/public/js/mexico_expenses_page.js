(function (root) {
	root.DeepLinkERPMexicoExpensesPage = {
		register(route) {
			frappe.pages[route].on_page_load = function (wrapper) {
				// Pass plain asset paths; Frappe adds its own cache-busting version.
				frappe.require([
					"/assets/deeplinkerp_branding/css/mexico_expenses.css",
					"/assets/deeplinkerp_branding/js/mexico_expense_date_range.js",
					"/assets/deeplinkerp_branding/js/mexico_expense_money.js",
					"/assets/deeplinkerp_branding/js/mexico_expense_mappings.js",
                    "/assets/deeplinkerp_branding/js/mexico_expense_insights.js",
					"/assets/deeplinkerp_branding/js/mexico_expenses.js",
				], () => {
					if (!wrapper.mx_expenses) {
						wrapper.mx_expenses = new root.DeepLinkERPMexicoExpenses.Workbench(wrapper);
					}
				});
			};
			frappe.pages[route].on_page_show = function (wrapper) {
				wrapper.mx_expenses?.show();
			};
		},
	};
})(globalThis);
