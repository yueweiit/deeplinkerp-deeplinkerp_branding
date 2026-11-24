/* Shared native MX settings dialog; no page or route navigation. */
(function (root, factory) {
	const api = factory(root);
	if (typeof module === "object" && module.exports) module.exports = factory;
	root.DeepLinkERPMexicoExpenseSettings = api;
})(globalThis, function (root) {
	"use strict";
	const t = (text, args) => root.__(text, args);
	const esc = (value) => root.frappe.utils.escape_html(String(value ?? ""));
	const call = async (method, args = {}) =>
		(
			await root.frappe.call({
				method: "deeplinkerp_branding.services.mexico_expenses." + method,
				args,
			})
		).message;
	async function open(onSaved, options = {}) {
		const context = await call("get_context");
		if (!context.can_manage) {
			return root.frappe.msgprint(
				t("Access to all mapped subsidiaries is required to manage synchronization")
			);
		}
		const saved = context.settings;
		if (options.isCurrent && !options.isCurrent()) return;
		const dialog = new root.frappe.ui.Dialog({
			title: t("Mexico Expense Synchronization"),
			size: "large",
			fields: [
				{
					fieldname: "enabled",
					label: t("Enable Mexico Expense Sync"),
					fieldtype: "Check",
					default: context.enabled,
				},
				{
					fieldname: "business_timezone",
					label: t("Business Timezone"),
					fieldtype: "Data",
					default: saved.business_timezone,
					reqd: 1,
				},
				{
					fieldname: "notice",
					fieldtype: "HTML",
					options: `<p class="text-muted">${esc(
						t(
							"Mappings use the exact source entity label and kind; department names never imply a company"
						)
					)}</p>`,
				},
				{
					fieldname: "approval_templates",
					label: t("Approval Templates"),
					fieldtype: "Table",
					cannot_delete_rows: true,
					data: saved.templates || [],
					fields: [
						{
							fieldname: "enabled",
							label: t("Enabled"),
							fieldtype: "Check",
							default: 0,
							in_list_view: 1,
							columns: 1,
						},
						{
							fieldname: "approval_type",
							label: t("Approval Form"),
							fieldtype: "Select",
							options: context.approval_forms.map((f) => ({
								value: f.id,
								label: f.label,
							})),
							formatter: (value) =>
								esc(
									context.approval_forms.find((form) => form.id === value)
										?.label || value
								),
							reqd: 1,
							in_list_view: 1,
							columns: 2,
						},
						{
							fieldname: "template_name",
							label: t("Template Name"),
							fieldtype: "Data",
							in_list_view: 1,
							columns: 3,
						},
						{
							fieldname: "corp_id",
							label: t("Corporation ID"),
							fieldtype: "Data",
							reqd: 1,
						},
						{
							fieldname: "process_code",
							label: t("Process Code"),
							fieldtype: "Data",
							reqd: 1,
							in_list_view: 1,
							columns: 4,
						},
						{
							fieldname: "finance_node_names",
							label: t("Finance Node Names"),
							fieldtype: "Small Text",
							default: "MX财务Finanzas",
						},
						{
							fieldname: "finance_activity_ids",
							label: t("Finance Activity IDs"),
							fieldtype: "Small Text",
						},
						{
							fieldname: "classification_fields",
							label: t("Classification Fields JSON"),
							fieldtype: "Code",
							options: "JSON",
							description: t(
								"Ordered levels of component IDs; IDs in one level are alternative branches. Leave blank to use template defaults."
							),
						},
						{
							fieldname: "field_mapping",
							label: t("Field Mapping JSON"),
							fieldtype: "Code",
							options: "JSON",
							default: "{}",
						},
						{
							fieldname: "last_sync_at",
							label: t("Last Successful Sync"),
							fieldtype: "Datetime",
							read_only: 1,
						},
						{
							fieldname: "last_error",
							label: t("Last Sync Error"),
							fieldtype: "Small Text",
							read_only: 1,
						},
					],
				},
				{
					fieldname: "template_help",
					fieldtype: "HTML",
					options: `<p class="text-muted">${esc(
						t(
							"Select one template to preview. Disable historical templates instead of deleting them. Field mappings use logical names and component ID arrays."
						)
					)}</p>`,
				},
				{
					fieldname: "preview_template",
					label: t("Preview Selected Template"),
					fieldtype: "Button",
					click: async () => {
						const selected =
							dialog.fields_dict.approval_templates.grid.get_selected_children();
						if (selected.length !== 1)
							return root.frappe.msgprint(t("Select exactly one template"));
						const result = await call("preview_template", {
							template: selected[0],
							business_timezone: dialog.get_value("business_timezone"),
						});
						dialog.fields_dict.template_preview.$wrapper.html(
							`<div class="mx-notice">${esc(
								t("{0} applications · {1} expense lines", [
									result.applications,
									result.lines,
								])
							)}<br>${esc(result.finance_nodes.join(" / "))}<br>${esc(
								result.issues
									.map((issue) => context.issue_labels[issue] || issue)
									.join(" / ")
							)}</div>`
						);
					},
				},
				{ fieldname: "template_preview", fieldtype: "HTML" },
				{ fieldname: "company_mappings", fieldtype: "HTML" },
			],
			primary_action_label: t("Save Settings"),
			primary_action: async (values) => {
				dialog.disable_primary_action();
				try {
					await call("save_settings", {
						enabled: values.enabled || 0,
						business_timezone: values.business_timezone,
						approval_templates: values.approval_templates,
						company_mappings: mappingEditor.mappings,
						expected_config_version: saved.config_version,
					});
					dialog.hide();
					await onSaved?.();
					root.frappe.show_alert({
						message: t("Settings saved; cached records will be rechecked"),
						indicator: "green",
					});
				} finally {
					dialog.enable_primary_action();
				}
			},
		});
		const mappingEditor = new root.DeepLinkERPMexicoExpenseMappings.Editor({
			wrapper: dialog.fields_dict.company_mappings.$wrapper,
			companies: context.companies,
			mappings: saved.company_mappings,
			configVersion: saved.config_version,
			fetchPage: (start) => call("get_unmapped_entities", { start }),
		});
		dialog.show();
		mappingEditor.loadPage();
		return dialog;
	}
	return { open };
});
