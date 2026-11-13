(function () {
	"use strict";

	async function syncCompanyCostCenters(frm) {
		const version = (frm._dlp_cost_center_sync_version || 0) + 1;
		frm._dlp_cost_center_sync_version = version;
		const doc = frm.doc;
		const company = doc.company;
		const isCurrent = () => frm.doc === doc && doc.docstatus === 0 &&
			doc.company === company && frm._dlp_cost_center_sync_version === version;
		if (!company || doc.docstatus !== 0) return;

		const targets = [{ row: doc, table: null },
			...(doc.items || []).map(row => ({ row, table: "items" })),
			...(doc.taxes || []).map(row => ({ row, table: "taxes" }))]
			.filter(target => target.row.cost_center)
			.map(target => ({ ...target, value: target.row.cost_center }));
		if (!targets.length) return;

		const response = await frappe.db.get_value("Company", company, "cost_center");
		if (!isCurrent()) return;
		const defaultName = response.message?.cost_center;
		const names = [...new Set([...targets.map(target => target.value), defaultName].filter(Boolean))];
		const centers = await frappe.db.get_list("Cost Center", {
			filters: { name: ["in", names] },
			fields: ["name", "company", "disabled", "is_group"],
			limit_page_length: names.length,
		});
		if (!isCurrent()) return;
		const byName = new Map(centers.map(center => [center.name, center]));
		const fallback = byName.get(defaultName);
		const isUsable = center => center?.company === company && !Number(center.disabled) &&
			!Number(center.is_group);

		for (const target of targets) {
			if (!isCurrent()) return;
			if (target.table && !(doc[target.table] || []).includes(target.row)) continue;
			if (target.row.cost_center !== target.value) continue;
			const center = byName.get(target.value);
			// Unknown or unreadable centers remain subject to native server validation.
			if (!center?.company || center.company === company) continue;
			const header = byName.get(doc.cost_center);
			const replacement = isUsable(header) ? header.name : isUsable(fallback) ? defaultName : "";
			if (target.table) {
				await frappe.model.set_value(target.row.doctype, target.row.name, "cost_center", replacement);
			} else {
				await frm.set_value("cost_center", replacement);
			}
		}
	}

	frappe.ui.form.on("Sales Order", {
		company: syncCompanyCostCenters,
		validate: syncCompanyCostCenters,
	});
})();
