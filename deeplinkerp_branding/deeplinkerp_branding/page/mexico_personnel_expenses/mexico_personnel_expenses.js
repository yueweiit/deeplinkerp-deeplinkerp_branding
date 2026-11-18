// Preserve bookmarks while keeping a single personnel analysis entry.
(() => {
    function redirect() {
        if (frappe.get_route()[0] !== "mexico-personnel-expenses") return;
        let filters;
        try { filters = JSON.parse(new URLSearchParams(location.search).get("mx_filters") || "null"); } catch (_) {}
        frappe.set_route("mexico-personnel-report", filters && typeof filters === "object" && !Array.isArray(filters) ? {mx_filters:filters} : {});
    }
    frappe.pages["mexico-personnel-expenses"].on_page_load = redirect;
    frappe.pages["mexico-personnel-expenses"].on_page_show = redirect;
})();
