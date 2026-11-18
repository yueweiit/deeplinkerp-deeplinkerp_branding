// Preserve monthly report bookmarks within the unified analysis page.
(() => {
    function redirect() {
        if (frappe.get_route()[0] !== "mexico-expense-trend") return;
        let filters;
        try { filters = JSON.parse(new URLSearchParams(location.search).get("mx_filters") || "null"); } catch (_) {}
        // Replace the obsolete entry so Back cannot re-enter the redirect.
        frappe.route_flags.replace_route = true;
        frappe.set_route("mexico-expense-reports", {
            mx_group: "trend",
            ...(filters && typeof filters === "object" && !Array.isArray(filters) ? {mx_filters:filters} : {}),
        });
    }
    frappe.pages["mexico-expense-trend"].on_page_load = redirect;
    frappe.pages["mexico-expense-trend"].on_page_show = redirect;
})();
