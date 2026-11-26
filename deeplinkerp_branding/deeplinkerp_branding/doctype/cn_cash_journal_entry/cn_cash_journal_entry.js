frappe.ui.form.on("CN Cash Journal Entry", {
    setup(frm) {
        frm.set_query("company", () => ({filters:{is_group:0}}));
        frm.set_query("account", () => ({filters:{company:frm.doc.company,
            account_type:["in", ["Bank","Cash"]], is_group:0, disabled:0}}));
    },
});
