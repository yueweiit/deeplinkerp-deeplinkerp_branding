(function (globalThis) {
	"use strict";

	const MATERIAL_RECEIPT = "Material Receipt";

	function refreshStockEntryQuantityLabels(frm, mesReceipt) {
		const grid = frm?.fields_dict?.items?.grid;
		if (!grid || !frm.doc) return;

		if (!frm._dlp_stock_entry_quantity_labels) {
			const transferQty = grid.docfields.find((field) => field.fieldname === "transfer_qty");
			const actualQty = grid.docfields.find((field) => field.fieldname === "actual_qty");
			if (!transferQty || !actualQty) return;
			frm._dlp_stock_entry_quantity_labels = {
				transfer_qty: transferQty.label,
				actual_qty: actualQty.label,
				mes_receipt_type: "",
			};
		}

		const labels = frm._dlp_stock_entry_quantity_labels;
		if (mesReceipt === true) labels.mes_receipt_type = frm.doc.stock_entry_type || "";
		else if (mesReceipt === false) labels.mes_receipt_type = "";
		const isReceipt = frm.doc.purpose === MATERIAL_RECEIPT ||
			(!frm.doc.purpose && frm.doc.stock_entry_type === MATERIAL_RECEIPT) ||
			Boolean(labels.mes_receipt_type && labels.mes_receipt_type === frm.doc.stock_entry_type);

		grid.update_docfield_property("transfer_qty", "label",
			isReceipt ? __("入库数量（库存单位）") : labels.transfer_qty);
		grid.update_docfield_property("actual_qty", "label",
			isReceipt ? __("目标仓过账时点库存") : labels.actual_qty);
	}

	globalThis.DeepLinkERPStockEntryQuantityLabels = { refresh: refreshStockEntryQuantityLabels };
	frappe.ui.form.on("Stock Entry", {
		onload: refreshStockEntryQuantityLabels,
		refresh: refreshStockEntryQuantityLabels,
		stock_entry_type: refreshStockEntryQuantityLabels,
		purpose: refreshStockEntryQuantityLabels,
	});
})(typeof globalThis !== "undefined" ? globalThis : window);
