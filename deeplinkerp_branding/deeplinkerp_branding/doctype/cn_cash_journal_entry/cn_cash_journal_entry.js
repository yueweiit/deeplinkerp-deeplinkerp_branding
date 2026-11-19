frappe.ui.form.on("CN Cash Journal Entry", {
    setup(frm) {
        frm.set_query("company", () => ({filters:{is_group:0}}));
    },
});
