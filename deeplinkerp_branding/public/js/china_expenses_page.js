(function (root) {
    root.DeepLinkERPChinaExpensesPage = {
        register(route) {
            const redesigned = !!frappe.boot?.cn_budget_workbench;
            const mount = wrapper => {
                const current = frappe.get_route?.()?.[0];
                if (current && current !== route) return;
                if (!wrapper.cn_expenses) wrapper.cn_expenses = redesigned
                    ? new root.CNApprovalWorkbench.Workbench(wrapper, route)
                    : new root.DeepLinkERPChinaExpenses.Workbench(wrapper);
                else wrapper.cn_expenses.show();
            };
            frappe.pages[route].on_page_load = function (wrapper) {
                if (redesigned) {
                    frappe.require([
                        "/assets/deeplinkerp_branding/css/mexico_expenses.css",
                        "/assets/deeplinkerp_branding/js/mexico_expense_date_range.js",
                        "/assets/deeplinkerp_branding/js/mexico_expense_money.js",
                        "/assets/budget/css/cn_approval_workbench.css",
                        "/assets/budget/js/cn_approval_workbench.js",
                        "/assets/budget/js/cn_approval_management.js",
                    ], () => { wrapper.cn_assets_ready = true; if (!wrapper.cn_expenses) mount(wrapper); });
                    return;
                }
                // Use the same plain paths as MX so native asset caching is shared.
                frappe.require([
                    "/assets/deeplinkerp_branding/css/mexico_expenses.css",
                    "/assets/deeplinkerp_branding/css/china_expenses.css",
                    "/assets/deeplinkerp_branding/js/mexico_expense_date_range.js",
                    "/assets/deeplinkerp_branding/js/mexico_expense_insights.js",
                    "/assets/deeplinkerp_branding/js/mexico_expenses.js",
                    "/assets/deeplinkerp_branding/js/china_operating_expenses.js",
                ], () => frappe.require("/assets/deeplinkerp_branding/js/china_expenses.js", () => {
                    wrapper.cn_assets_ready = true; if (!wrapper.cn_expenses) mount(wrapper);
                }));
            };
            frappe.pages[route].on_page_show = function (wrapper) { if (wrapper.cn_assets_ready) mount(wrapper); };
            frappe.pages[route].on_page_hide = function (wrapper) { wrapper.cn_expenses?.hide?.(); };
        },
    };
})(globalThis);
