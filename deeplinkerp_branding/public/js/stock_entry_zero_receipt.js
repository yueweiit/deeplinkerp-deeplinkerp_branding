(function () {
	"use strict";

	const CONFIRMATION_DOCTYPE = "Zero Receipt Confirmation";
	const CONFIRMATION_ITEM_DOCTYPE = "Zero Receipt Confirmation Item";
	const ACTION_LABEL = __("记录零数量入库");

	function isExplicitZeroQuantity(value) {
		if (typeof value === "number") return Number.isFinite(value) && value === 0;
		if (typeof value !== "string" || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(value.trim())) {
			return false;
		}
		return Number(value) === 0;
	}

	function openZeroReceiptConfirmation(frm) {
		if (!frm.is_new() || frm.doc.purpose !== "Material Receipt") {
			frappe.msgprint(__("仅新建的入库单可记录零数量收货。"));
			return;
		}
		const rows = frm.doc.items || [];
		if (!frm.doc.company || !rows.length || rows.some((row) => !row.item_code)) {
			frappe.msgprint(__("请先选择公司并填写至少一行物料。"));
			return;
		}
		if (rows.some((row) => !isExplicitZeroQuantity(row.qty))) {
			frappe.msgprint(__("零数量入库确认只接受数量为 0 的明细；正数入库请使用原生库存单。"));
			return;
		}
		if (frm.doc.from_warehouse || rows.some((row) => row.s_warehouse || !(row.t_warehouse || frm.doc.to_warehouse))) {
			frappe.msgprint(__("零数量入库确认需要目标仓库，且不能填写来源仓库。"));
			return;
		}

		frappe.model.with_doctype(CONFIRMATION_DOCTYPE, () => {
			if (!frappe.model.can_create(CONFIRMATION_DOCTYPE)) {
				frappe.msgprint(__("没有创建零数量入库确认单的权限。"));
				return;
			}
			const confirmation = frappe.model.get_new_doc(CONFIRMATION_DOCTYPE);
			confirmation.company = frm.doc.company;
			confirmation.receipt_date = frm.doc.posting_date || confirmation.receipt_date;
			confirmation.receipt_time = frm.doc.posting_time || confirmation.receipt_time;
			confirmation.stock_entry_type = frm.doc.stock_entry_type || "Material Receipt";
			for (const row of rows) {
				const item = frappe.model.add_child(confirmation, CONFIRMATION_ITEM_DOCTYPE, "items");
				item.item_code = row.item_code;
				item.target_warehouse = row.t_warehouse || frm.doc.to_warehouse;
				item.received_qty = 0;
				item.stock_uom = row.stock_uom;
				if (row.material_request) {
					item.material_request = row.material_request;
					if (row.material_request_item) item.material_request_item = row.material_request_item;
				}
			}
			frappe.set_route("Form", CONFIRMATION_DOCTYPE, confirmation.name);
		});
	}

	function refreshZeroReceiptAction(frm) {
		frm.remove_custom_button(ACTION_LABEL);
		if (frm.is_new() && frm.doc.purpose === "Material Receipt" &&
			frappe.model.can_create("Stock Entry")) {
			frm.add_custom_button(ACTION_LABEL, () => openZeroReceiptConfirmation(frm));
		}
	}

	frappe.ui.form.on("Stock Entry", {
		refresh: refreshZeroReceiptAction,
		purpose: refreshZeroReceiptAction,
		stock_entry_type: refreshZeroReceiptAction,
	});
})();
