/* Shared CN/MX allocation editor. Raw approvals never reach this dialog. */
(function (root) {
    "use strict";
    const api = "deeplinkerp_branding.services.regional_expenses.";
    const call = async (method, args = {}) => (await frappe.call({method: api + method, args})).message;
    const regions = {cn: "CN Expenses", mx: "MX Expenses", other: "Other scope", pending: "Awaiting allocation"};
    const escape = (value) => frappe.utils.escape_html(String(value ?? ""));
    async function open(args, onSaved) {
        const data = await call("get_editor", args);
        const options = [{value: "", label: ""}, ...data.lines.map((line, i) => ({value: line.line_key,
            label: escape(`${i + 1}. ${line.source_entity || line.description || __("Detail row")} · ${line.amount ?? "—"} ${line.currency || ""}`)}))];
        const fields = [
            {fieldname: "line_key", label: __("Original expense line"), fieldtype: "Select", reqd: 1, in_list_view: 1,
                columns: 3, options, formatter: value => options.find(option => option.value === value)?.label || escape(value)},
            {fieldname: "region", label: __("Statistical scope"), fieldtype: "Select", reqd: 1, in_list_view: 1, columns: 2,
                options: [{value: "", label: ""}, ...Object.entries(regions).map(([value, label]) => ({value, label: escape(__(label))}))],
                formatter: value => escape(__(regions[value] || value || ""))},
            {fieldname: "company", label: __("Subsidiary"), fieldtype: "Select", in_list_view: 1, columns: 2,
                options: ["", ...Object.keys(data.companies)].map(value => ({value, label: escape(value)})), formatter: escape},
            // Data preserves exact decimal strings; never round with JS floats.
            {fieldname: "amount", label: __("Allocated original amount"), fieldtype: "Data", reqd: 1, in_list_view: 1, columns: 1, formatter: escape},
            {fieldname: "management_category", label: __("CN management category"), fieldtype: "Select", in_list_view: 1, columns: 2,
                options: [{value: "", label: ""}, ...Object.entries(data.categories).map(([value, label]) => ({value, label: escape(__(label))}))],
                formatter: value => escape(__(data.categories[value] || value || ""))},
        ];
        const dialog = new frappe.ui.Dialog({title: __("Cross-region expense allocation"), size: "extra-large", fields: [
            {fieldname: "instructions", fieldtype: "HTML"},
            {fieldname: "reset_lines", label: __("Rebuild from current source lines"), fieldtype: "Button", click() {
                dialog.fields_dict.allocations.df.data = data.lines.map((line) => ({line_key: line.line_key,
                    amount: line.amount, region: "pending", company: "", management_category: ""}));
                dialog.fields_dict.allocations.grid.refresh();
            }},
            {fieldname: "allocations", fieldtype: "Table", in_place_edit: true, data: data.allocations, fields},
            {fieldname: "finance_section", fieldtype: "Section Break", label: __("MX Finance approval"), collapsible: 1},
            {fieldname: "finance_activity_ids", fieldtype: "Small Text", label: __("MX finance activity IDs"),
                default: data.finance_activity_ids, read_only: data.origin !== "cn"},
            {fieldname: "finance_node_names", fieldtype: "Small Text", label: __("MX finance node names"),
                default: data.finance_node_names, read_only: data.origin !== "cn",
                description: __("Use exact node names or activity IDs from the approval. Leave empty to keep MX allocations awaiting finance confirmation.")},
            {fieldname: "review_section", fieldtype: "Section Break"},
            {fieldname: "note", label: __("Review Note"), fieldtype: "Small Text", reqd: 1, default: data.note},
        ], primary_action_label: __("Save allocation"), async primary_action() {
            const values = dialog.get_values();
            if (!values) return;
            dialog.disable_primary_action();
            try {
                await call("save_allocation", {origin: data.origin, key: data.key || "", allocation: data.name || "",
                    expected_revision: data.revision, expected_fingerprint: data.fingerprint,
                    finance_activity_ids: values.finance_activity_ids || "", finance_node_names: values.finance_node_names || "",
                    allocations: values.allocations.map(({line_key, region, company, amount, management_category}) =>
                        ({line_key, region, company, amount, management_category})), note: values.note});
                dialog.hide();
                if (onSaved) await onSaved();
            } finally { dialog.enable_primary_action(); }
        }});
        dialog.fields_dict.instructions.$wrapper.html(`<p><strong>${escape(data.number)}</strong></p><p>${escape(__(data.assignment_mode === "automatic"
            ? "Automatically assigned by company country. Saving changes creates a manual exception."
            : "This approval uses a saved manual allocation."))}</p><p>${escape(__(
            "Allocate every original line in its original currency. Split a line by adding rows; its allocated amounts must equal its original amount. CN uses the application month; MX requires its finance approval date. Approval does not confirm payment."
        ))}</p>${data.state === "stale" ? `<p class="text-warning">${escape(__("Cross-region allocation requires review"))}</p>` : ""}`);
        dialog.show();
    }
    async function list(onSaved) {
        const rows = await call("list_allocations");
        if (!rows.length) return frappe.msgprint(__("Open an expense approval to create its cross-region allocation."));
        const dialog = new frappe.ui.Dialog({title: __("Cross-region expense allocation"), fields: [
            {fieldname: "allocation", label: __("Approval Number"), fieldtype: "Select", reqd: 1,
                options: rows.map((row) => ({value: row.name, label: escape(`${row.number || row.name} · ${row.origin.toUpperCase()} · ${__({stale: "Needs review", pending: "Awaiting allocation", confirmed: "Confirmed"}[row.state])}`)}))},
        ], primary_action_label: __("Open"), primary_action(values) {
            dialog.hide();
            return open({allocation: values.allocation}, onSaved);
        }});
        dialog.show();
    }
    root.RegionalExpenses = {open, list};
})(window);
