frappe.ui.form.on("Zero Receipt Confirmation", {
	refresh(frm) {
		frm.set_intro(__("本单仅记录实际收到 0，不生成库存流水或会计分录。"), "blue");
		frm.set_query("stock_entry_type", () => ({ filters: { purpose: "Material Receipt" } }));
		frm.set_query("item_code", "items", () => ({ filters: { is_stock_item: 1, disabled: 0 } }));
		frm.set_query("material_request", "items", () => ({
			filters: { company: frm.doc.company, docstatus: 1 },
		}));
		frm.set_query("target_warehouse", "items", () => ({
			filters: { company: frm.doc.company, is_group: 0, disabled: 0 },
		}));
	},
});
