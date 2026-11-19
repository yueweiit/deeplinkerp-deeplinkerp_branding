(function (root) {
    root.DeepLinkERPChinaExpensesPage = {
        register(route) {
            frappe.pages[route].on_page_load = function (wrapper) {
                // Use the same plain paths as MX so native asset caching is shared.
                frappe.require([
                    "/assets/deeplinkerp_branding/css/mexico_expenses.css",
                    "/assets/deeplinkerp_branding/css/china_expenses.css",
                    "/assets/deeplinkerp_branding/js/mexico_expense_date_range.js",
                    "/assets/deeplinkerp_branding/js/mexico_expense_insights.js",
                    "/assets/deeplinkerp_branding/js/mexico_expenses.js",
                    "/assets/deeplinkerp_branding/js/china_operating_expenses.js",
                ], () => frappe.require("/assets/deeplinkerp_branding/js/china_expenses.js", () => {
                    if (!wrapper.cn_expenses) wrapper.cn_expenses = new root.DeepLinkERPChinaExpenses.Workbench(wrapper);
                }));
            };
            frappe.pages[route].on_page_show = function (wrapper) { wrapper.cn_expenses?.show(); };
        },
    };
})(globalThis);
