frappe.ui.form.on("Item", {
	refresh(frm) {
		const control = frm.fields_dict.end_of_life;
		const source = control?.df.description;
		if (!source || !control.df.show_description_on_click) return;
		// Frappe's InfoCard inserts the description without translating it.
		const translated = __(source, null, frm.doctype);
		control.$wrapper.find(".info-card .sidebar-card-description").each((_, element) => {
			for (const node of element.childNodes) {
				if (node.nodeType === 3 && node.textContent.trim() === source.trim()) {
					node.textContent = translated;
				}
			}
		});
	},
});
