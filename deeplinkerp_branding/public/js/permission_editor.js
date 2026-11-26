/* Presentation for native RoleEditor/ModuleEditor. All writes remain in the native form. */
(() => {
	if (window.DeepLinkERPPermissionEditor) {
		window.DeepLinkERPPermissionEditor.register(window.cur_frm?.doctype);
		return;
	}
	const groups = {
		sales: "Sales and customer service", buying: "Purchasing", stock: "Inventory and production",
		finance: "Finance and tax", operations: "Projects and operations", integration: "Integrations",
		system: "System and platform", other: "Other / needs explanation",
	};
	// Exact internal identities only. These are responsibility guides, not effective permission assertions.
	const roles = {};
	function role(names, group, description, audience, sensitive = false) {
		for (const name of names) roles[name] = { group, description, audience, sensitive };
	}
	role(["Sales User"], "sales", "Daily sales documents and customer follow-up.", "Sales staff");
	role(["Sales Manager"], "sales", "Sales supervision and management of sales documents.", "Sales supervisors", true);
	role(["Sales Master Manager"], "sales", "Maintenance of shared sales master data.", "Sales master data maintainers");
	role(["Sales Production Release Operator"], "sales", "Check the actual binding to Sales Production Release Permission before assigning this role.", "Authorized production release staff", true);
	role(["Support Team", "Support User"], "sales", "Customer issues, service requests and support follow-up.", "Customer service staff");
	role(["Support Manager"], "sales", "Customer service supervision and service configuration.", "Customer service supervisors", true);
	role(["Purchase User"], "buying", "Daily purchasing documents and supplier follow-up.", "Purchasing staff");
	role(["Purchase Manager"], "buying", "Purchasing supervision and management of purchasing documents.", "Purchasing supervisors", true);
	role(["Stock User"], "stock", "Warehouse documents, receipts, issues and stock movements.", "Warehouse staff");
	role(["Stock Manager"], "stock", "Inventory supervision and management of stock documents.", "Inventory supervisors", true);
	role(["Item Manager"], "stock", "Maintenance of items and related master data.", "Item master data maintainers");
	role(["Manufacturing User"], "stock", "Production orders and daily manufacturing operations.", "Production staff");
	role(["Manufacturing Manager"], "stock", "Manufacturing supervision and production configuration.", "Production supervisors", true);
	role(["Quality Manager"], "stock", "Quality inspections, goals and quality management records.", "Quality staff");
	role(["Subcontracting User"], "stock", "Subcontracting documents and material coordination.", "Subcontracting staff");
	role(["Subcontracting Manager"], "stock", "Subcontracting supervision and document management.", "Subcontracting supervisors", true);
	role(["Accounts User"], "finance", "Daily accounting documents and financial processing.", "Accounting staff");
	role(["Accounts Manager"], "finance", "Accounting supervision and financial configuration.", "Finance supervisors", true);
	role(["Auditor", "China Finance Auditor"], "finance", "Financial review and audit enquiries; check actual write and export permissions.", "Auditors");
	role(["China Finance User"], "finance", "Daily work in the China finance extension.", "Accounting staff");
	role(["China Voucher Maker"], "finance", "Preparation of accounting vouchers in the China finance extension.", "Accounting staff");
	role(["China Finance Manager"], "finance", "China finance configuration, closing and financial controls.", "Finance supervisors", true);
	role(["China Tax User"], "finance", "Tax invoice processing and input tax deduction work.", "Tax staff");
	role(["China Voucher Reviewer"], "finance", "Review of accounting vouchers in the China finance extension.", "Voucher reviewers", true);
	role(["China Voucher Poster"], "finance", "Posting of accounting vouchers in the China finance extension.", "Voucher posting staff", true);
	role(["China Archive User"], "finance", "Accounting archive preparation and enquiries.", "Accounting archive staff");
	role(["China Cashier", "China Cashier User"], "finance", "Cashier processing in the China finance extension.", "Cashiers");
	role(["海外成本核算用户"], "finance", "Overseas cost workbench access; related document permissions still apply.", "Overseas costing staff");
	role(["Projects User"], "operations", "Projects, tasks and daily project follow-up.", "Project staff");
	role(["Projects Manager"], "operations", "Project supervision and project configuration.", "Project supervisors", true);
	role(["Maintenance User"], "operations", "Equipment maintenance and service records.", "Maintenance staff");
	role(["Maintenance Manager"], "operations", "Maintenance supervision and service configuration.", "Maintenance supervisors", true);
	role(["Delivery User"], "operations", "Delivery trips and delivery coordination.", "Delivery staff");
	role(["Delivery Manager"], "operations", "Delivery supervision and delivery configuration.", "Delivery supervisors", true);
	role(["Employee", "Employee Self Service"], "operations", "Employee self-service and related personal records.", "Employees");
	role(["HR User"], "operations", "Daily human resources records and processing.", "Human resources staff");
	role(["HR Manager"], "operations", "Human resources supervision and configuration.", "Human resources supervisors", true);
	role(["System Manager"], "system", "System configuration, users and permission administration.", "System administrators", true);
	role(["Website Manager"], "system", "Website pages, content and website configuration.", "Website administrators", true);
	role(["SMS Manager"], "system", "SMS sending and related configuration.", "SMS administrators", true);
	role(["Newsletter Manager"], "system", "Mailing lists and newsletter publishing.", "Mailing list administrators");
	role(["Script Manager"], "system", "Script maintenance that can affect system behavior.", "Authorized technical administrators", true);
	role(["Report Manager", "Dashboard Manager"], "system", "Shared report or dashboard configuration.", "Report administrators", true);
	role(["Workspace Manager"], "system", "Shared workspace layout and navigation configuration.", "Workspace administrators", true);
	role(["Translator"], "system", "Maintenance of interface translations.", "Translation maintainers");
	role(["Blogger"], "system", "Blog posts and related website content.", "Content editors");
	role(["Knowledge Base Contributor", "Knowledge Base Editor"], "system", "Knowledge base articles and content maintenance.", "Content editors");
	const modules = {};
	function module(names, group, label, description) {
		for (const name of names) modules[name] = { group, label, description };
	}
	module(["Selling"], "sales", "Selling", "Sales documents, customers and sales analysis.");
	module(["CRM"], "sales", "CRM", "Leads, opportunities and customer relationship work.");
	module(["Support"], "sales", "Support", "Customer issues, support and service follow-up.");
	module(["Buying"], "buying", "Buying", "Supplier enquiries, purchasing and purchase analysis.");
	module(["OA Purchase Request"], "buying", "OA purchase collaboration", "OA purchase requests and related purchasing collaboration.");
	module(["Stock"], "stock", "Stock", "Items, warehouses, stock movements and inventory enquiries.");
	module(["Manufacturing"], "stock", "Manufacturing", "Bills of materials, work orders and production processing.");
	module(["Subcontracting"], "stock", "Subcontracting", "Subcontracted orders and supplied materials.");
	module(["Quality Management"], "stock", "Quality Management", "Quality goals, procedures and review records.");
	module(["Quantity In Stock"], "stock", "Stock quantity enquiries", "Stock quantity enquiry features supplied by the installed app.");
	module(["Mobile Operations"], "stock", "Mobile operations", "Mobile warehouse and on-site operations.");
	module(["Accounts"], "finance", "Accounting", "Accounting documents, receivables, payables and financial reports.");
	module(["China Finance"], "finance", "China Finance", "China accounting vouchers, reconciliation, tax and closing.");
	module(["Budget"], "finance", "Budget", "Budget and expense management features supplied by the installed app.");
	module(["Overseas Costing"], "finance", "Overseas costing", "Overseas cost batches, allocation and related procurement.");
	module(["Projects"], "operations", "Projects", "Projects, tasks and project follow-up.");
	module(["Assets"], "operations", "Assets", "Asset records, depreciation and asset movements.");
	module(["Maintenance"], "operations", "Maintenance", "Equipment maintenance plans, visits and service records.");
	module(["Utilities"], "operations", "Utilities", "General tools and utility records.");
	module(["Contacts"], "operations", "Contacts", "Contacts and addresses shared by business documents.");
	module(["Communication"], "operations", "Communication", "Communication records linked to business documents.");
	module(["CRM Integration", "Crm Integration"], "integration", "CRM integration", "CRM and ERP data exchange, settings and integration logs.");
	module(["MES Integration", "Mes Integration"], "integration", "MES integration", "ERP and MES production and inventory integration.");
	module(["Integrations"], "integration", "Integrations", "External service connections and integration settings.");
	module(["ERPNext Integrations"], "integration", "ERPNext integrations", "Integration features provided by ERPNext.");
	module(["EDI"], "integration", "Electronic data interchange", "Electronic business data exchange features.");
	module(["Telephony"], "integration", "Telephony", "Telephony connections and call-related features.");
	module(["Core"], "system", "System core", "Users, roles and framework management features.");
	module(["Setup"], "system", "Setup", "Shared business settings and basic configuration.");
	module(["Customization"], "system", "Customization", "Custom fields, forms and system customization.");
	module(["Custom Filters"], "system", "Filters and login extensions", "Custom filters, Desk enhancements and login integrations.");
	module(["Deeplinkerp Branding"], "system", "DeepLinkERP business extensions", "Shared navigation, sales, purchasing, inventory and expense extensions.");
	module(["Website"], "system", "Website", "Website pages, portal content and website settings.");
	module(["Portal"], "system", "Portal", "Customer or supplier portal features.");
	module(["Workflow"], "system", "Workflow", "Document approval workflows and state transitions.");
	module(["Email"], "system", "Email", "Email accounts, messages and related settings.");
	module(["Printing"], "system", "Printing and formats", "Print formats, print settings and output configuration.");
	module(["Automation"], "system", "Automation", "Automatic assignment and other automation rules.");
	module(["Bulk Transaction"], "system", "Bulk transactions", "Batch processing of supported business documents.");
	module(["Geo"], "system", "Geographical data", "Country, region and geographical reference data.");
	module(["Regional"], "system", "Regional", "Regional business features and localization.");
	module(["Desktop Notifications"], "system", "Desktop notifications", "Desktop message and notification features.");
	module(["Draft Notifications"], "system", "Draft notifications", "Rule-based business document notifications.");
	module(["AI Assistant"], "system", "AI Assistant", "AI assistant settings and business query features.");

	const translate = (text, args) => __(text, args, "Permission assignment");
	const escape = (value) => frappe.utils.escape_html(String(value ?? ""));
	const unique = (values) => [...new Set(values.filter(Boolean))];
	const profile_names = (frm) => unique((frm.doc.role_profiles || []).map((row) => row.role_profile));
	const selected_roles = (frm) => unique((frm.doc.roles || []).map((row) => row.role));
	function describe(kind, value) {
		const catalog = kind === "roles" ? roles : modules;
		const known = Object.hasOwn(catalog, value) ? catalog[value] : null;
		return known || {
			group: "other", description: "Purpose not documented yet. Check actual permissions and related business settings.",
		};
	}
	function profile_diff(current_roles, profiles) {
		const next = unique(profiles.flatMap((profile) => (profile.roles || []).map((row) => row.role)));
		return {
			added: next.filter((role) => !current_roles.includes(role)),
			removed: current_roles.filter((role) => !next.includes(role)),
			retained: next.filter((role) => current_roles.includes(role)),
		};
	}
	async function read_profiles(names) {
		if (!names.length) return [];
		await frappe.model.with_doctype("Role Profile");
		const field = frappe.meta.get_docfield("Role Profile", "roles");
		// client.get serializes a hidden child table as []; distinguish that from a truly empty template.
		if (!field || (frappe.session.user !== "Administrator" &&
			!frappe.perm.has_perm("Role Profile", field.permlevel || 0, "read"))) {
			throw new Error("Role Profile roles are not readable");
		}
		// db.get_doc syncs into locals and can overwrite a template that is being edited in another Desk tab.
		const profiles = await Promise.all(names.map((name) => frappe.xcall(
			"frappe.client.get", { doctype: "Role Profile", name }, "GET"
		)));
		if (profiles.some((profile) => !Array.isArray(profile.roles) ||
			profile.roles.some((row) => typeof row.role !== "string" || !row.role))) {
			throw new Error("Incomplete Role Profile roles");
		}
		return profiles;
	}
	async function apply_profile(frm, name, expected_roles) {
		const doc = frm.doc;
		const snapshot = {
			roles: (doc.roles || []).map((row) => ({ ...row })),
			role_profiles: (doc.role_profiles || []).map((row) => ({ ...row })),
			__unsaved: doc.__unsaved,
		};
		frappe.dom.freeze(translate("Applying job template..."));
		try {
			frm.add_child("role_profiles", { role_profile: name });
			frm.dirty();
			frm.roles_editor.disable = true;
			frm.roles_editor.show();
			frm.refresh_field("role_profiles");
			// The native child event does not return this request's promise. Wait for the method itself.
			const response = await frm.call("populate_role_profile_roles");
			if (response?.exc || frm.doc !== doc ||
				JSON.stringify([...selected_roles(frm)].sort()) !== JSON.stringify([...expected_roles].sort())) {
				throw new Error("Role template synchronization did not match the confirmed preview");
			}
			frm.dirty();
		} catch (error) {
			// Restore only this operation's fields; native sync also restores child identities in locals.
			frappe.model.sync({ ...doc, ...snapshot });
			throw error;
		} finally {
			try {
				if (frm.doc === doc) {
					frm.roles_editor.disable = Boolean(doc.role_profiles?.length);
					frm.roles_editor.show();
					frm.refresh_field("role_profiles");
					frm.toolbar?.refresh();
					if (!doc.__unsaved && frm.beforeUnloadListener) {
						window.removeEventListener("beforeunload", frm.beforeUnloadListener, { capture: true });
					}
				}
			} finally {
				frappe.dom.unfreeze();
			}
		}
	}
	function matches(value, config, query, group, selected_only, checked) {
		return (!group || config.group === group) && (!selected_only || checked) &&
			[value, translate(config.label || value), translate(config.description), translate(groups[config.group]), translate(config.audience || "")]
				.join(" ").toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
	}
	function button(label, handler) {
		return $("<button type='button' class='btn btn-xs btn-default'>").text(label).on("click", handler);
	}
	let active_tooltip = null;
	function dismiss_tooltip() {
		const target = active_tooltip;
		active_tooltip = null;
		if (target?.data("bs.tooltip")) target.tooltip("hide");
	}
	function activate_tooltip(target) {
		if (active_tooltip?.[0] !== target[0]) dismiss_tooltip();
		active_tooltip = target;
	}
	function dispose_tooltip(target) {
		if (active_tooltip?.[0] === target[0]) active_tooltip = null;
		target.off(".dlpPermissionTooltip");
		if (target.data("bs.tooltip")) target.tooltip("dispose");
	}
	// Capture Escape before Desk blurs a focused control or closes its dialog.
	window.addEventListener("keydown", (event) => {
		if (event.key !== "Escape" || !active_tooltip) return;
		const element = active_tooltip[0];
		if (!element.isConnected) { active_tooltip = null; return; }
		dismiss_tooltip();
		if (document.activeElement === element) element.blur();
		event.preventDefault();
		event.stopPropagation();
	}, true);
	function set_tooltip(target, content) {
		if (target.data("dlpPermissionTip") === content && (!$.fn.tooltip || target.data("bs.tooltip"))) return;
		dispose_tooltip(target);
		target.attr("title", content).data("dlpPermissionTip", content);
		if ($.fn.tooltip) {
			// Track pending hover/focus timers too, so Escape can cancel a tip before it appears.
			target.on("mouseenter.dlpPermissionTooltip focusin.dlpPermissionTooltip show.bs.tooltip.dlpPermissionTooltip", () => activate_tooltip(target));
			target.on("hide.bs.tooltip.dlpPermissionTooltip", () => {
				if (active_tooltip?.[0] === target[0]) active_tooltip = null;
			});
			// A previous fade-out callback must not destroy a tip reopened immediately after Escape.
			target.tooltip({
				trigger: "hover focus", placement: "auto", container: "body", html: false, animation: false,
				delay: { show: 120, hide: 0 },
				template: '<div class="tooltip dlp-permission-tooltip" role="tooltip"><div class="arrow"></div><div class="tooltip-inner"></div></div>',
			});
		}
	}
	function hide_tooltips(frm) {
		for (const editor of [frm.roles_editor, frm.module_editor]) {
			editor?.dlp_guide?.each_tooltip(dispose_tooltip);
		}
	}
	class EditorGuide {
		constructor(editor, kind, changed) {
			this.editor = editor;
			this.kind = kind;
			this.changed = changed;
			this.sources = new Map();
			const control = editor.multicheck;
			this.control = control;
			this.toolbar = $("<div class='dlp-permission-filters'>").insertBefore(editor.wrapper);
			this.search = $("<input type='search' class='form-control input-sm'>")
				.attr({ placeholder: translate("Search by name or purpose"), "aria-label": translate("Search by name or purpose") })
				.appendTo(this.toolbar);
			this.category = $("<select class='form-control input-sm'>").attr("aria-label", translate("Business group"))
				.append($("<option>").val("").text(translate("All business groups"))).appendTo(this.toolbar);
			this.only = $("<input type='checkbox'>");
			$("<label>").append(this.only, document.createTextNode(translate("Selected only"))).appendTo(this.toolbar);
			this.toolbar.on("input change", "input,select", () => this.filter());
			this.empty = $("<div class='dlp-permission-empty text-muted'>").text(translate("No matching items"))
				.insertAfter(editor.wrapper).hide();
			// A bulk action on hidden rows is hard to review. Keep individual native checkboxes.
			control.df.select_all = false;
			control.$select_buttons.hide();
			const make = control.make_checkboxes;
			control.make_checkboxes = (...args) => {
				this.each_tooltip(dispose_tooltip);
				const result = make.apply(control, args);
				this.decorate();
				return result;
			};
			const select = control.select_options;
			control.select_options = (...args) => {
				const result = select.apply(control, args);
				this.filter();
				return result;
			};
			const change = control.df.on_change;
			control.df.on_change = (...args) => {
				const result = change?.apply(control.df, args);
				this.filter();
				this.changed?.();
				return result;
			};
			this.decorate();
		}
		each_tooltip(callback) {
			for (const item of this.rows || []) {
				for (const target of [item.label, item.help, item.detail]) {
					if (target?.data("bs.tooltip")) callback(target);
				}
			}
		}
		close_tooltips() {
			this.each_tooltip(dispose_tooltip);
			this.filter();
		}
		decorate() {
			const area = this.control.$checkbox_area.addClass("dlp-permission-options")
				.css({ columnCount: 1, columnWidth: "auto" });
			area.children(".dlp-permission-group").remove();
			this.rows = [];
			this.sections = new Map();
			this.category.find("option:not(:first)").remove();
			for (const option of this.control.options || []) {
				if (!option.$checkbox?.length) continue; // native options may still be loading
				const config = describe(this.kind, option.value);
				if (!this.sections.has(config.group)) {
					const section = $("<details class='dlp-permission-group' open>").appendTo(area);
					const summary = $("<summary>").appendTo(section);
					const grid = $("<div class='dlp-permission-grid'>").appendTo(section);
					this.sections.set(config.group, { section, summary, grid });
				}
				const row = option.$checkbox;
				const label = row.find(".label-area").text(translate(config.label || option.value));
				const help = button("i", (event) => {
					event.preventDefault(); event.stopPropagation();
					if (!$.fn.tooltip) frappe.msgprint(escape(help.data("dlpPermissionTip")));
					else help.trigger("focus");
				}).attr("aria-label", translate("About {0}", [label.text()]))
					.addClass("dlp-permission-help").toggleClass("is-key-responsibility", Boolean(config.sensitive)).appendTo(row);
				let detail;
				if (this.kind === "roles") {
					label.on("click.dlpPermission", () => this.close_tooltips());
					detail = button("⋯", () => {
						this.close_tooltips();
						return this.editor.show_permissions(option.value);
					}).attr("aria-label", translate("View permissions for {0}", [label.text()]))
						.addClass("dlp-permission-detail").appendTo(row);
					set_tooltip(detail, translate("View permissions"));
				}
				row.appendTo(this.sections.get(config.group).grid);
				this.rows.push({ value: option.value, config, row, label, help, detail, input: row.find('input[type="checkbox"]') });
			}
			// Keep a stable business order instead of the original alphabetical mixing.
			for (const group of Object.keys(groups)) {
				const section = this.sections.get(group);
				if (!section) continue;
				section.section.appendTo(area);
				$("<option>").val(group).text(translate(groups[group])).appendTo(this.category);
			}
			this.filter();
		}
		filter() {
			if (!this.rows) return;
			let visible = 0;
			const counts = new Map();
			for (const item of this.rows) {
				const checked = item.input.prop("checked");
				const show = matches(item.value, item.config, this.search.val() || "", this.category.val(), this.only.prop("checked"), checked);
				item.row.toggle(show);
				const sources = checked ? this.sources.get(item.value) : null;
				const explanation = [
					translate(item.config.description),
					item.config.audience ? translate("For: {0}", [translate(item.config.audience)]) : "",
					item.config.sensitive ? translate("Key responsibility") : "",
					sources?.length ? translate("From template: {0}", [sources.join(", ")]) : "",
				].filter(Boolean).join("\n");
				set_tooltip(item.label, explanation);
				set_tooltip(item.help, explanation);
				if (item.detail) set_tooltip(item.detail, translate("View permissions"));
				if (!show) {
					for (const target of [item.label, item.help, item.detail]) {
						if (target?.data("bs.tooltip")) target.tooltip("hide");
					}
				}
				const count = counts.get(item.config.group) || { total: 0, selected: 0 };
				count.total += Number(show); count.selected += Number(show && checked);
				counts.set(item.config.group, count);
				visible += Number(show);
			}
			for (const [group, section] of this.sections) {
				const count = counts.get(group) || { total: 0, selected: 0 };
				section.section.toggle(count.total > 0);
				section.summary.text(translate("{0} · {1} selected / {2}", [translate(groups[group]), count.selected, count.total]));
				if (this.search.val()?.trim() || this.only.prop("checked")) section.section.prop("open", true);
			}
			this.empty.toggle(!visible && this.rows.length > 0);
		}
	}
	class UserGuide {
		constructor(frm) {
			this.frm = frm;
			this.revision = 0;
			this.wrapper = $("<div class='dlp-permission-guide'>").insertBefore(frm.fields_dict.role_profiles.wrapper);
			const header = $("<div class='dlp-permission-guide-header'>").appendTo(this.wrapper);
			$("<strong>").text(translate("Assign permissions by job responsibility")).appendTo(header);
			this.metrics = $("<div class='dlp-permission-metrics'>").appendTo(header);
			const actions = $("<div class='dlp-permission-actions'>").appendTo(this.wrapper);
			this.preview = button(translate("Preview and add job template"), () => this.preview_profile()).appendTo(actions);
			button(translate("View saved user permissions"), () => this.route("query-report", "User Doctype Permissions"))
				.appendTo(actions);
			button(translate("Configure company / warehouse scope"), () => this.route("List", "User Permission"))
				.appendTo(actions);
			this.explanation = $("<details class='dlp-permission-guide-details'>").appendTo(this.wrapper);
			$("<summary>").text(translate("Configuration help")).appendTo(this.explanation);
			$("<p class='text-muted small'>").text(translate("Roles define operations. Module visibility controls navigation. User Permissions restrict records such as companies and warehouses."))
				.appendTo(this.explanation);
			$("<p class='text-muted small'>").text(translate("Use Preview and add to review role changes. Selected templates control the complete role list."))
				.appendTo(this.explanation);
			$("<p class='text-muted small'>").text(translate("Controls module navigation. Checking a module does not grant document operations or data access."))
				.appendTo(this.explanation);
			this.note = $("<p class='text-muted small'>").appendTo(this.explanation);
			this.sources_note = $("<p class='dlp-permission-profile-note small text-muted'>").appendTo(this.explanation);
		}
		route(type, name) {
			if (this.frm.is_dirty()) return frappe.msgprint(translate("Save the user first to inspect the saved permission configuration."));
			frappe.set_route(type, name, { user: this.frm.doc.name });
		}
		update() {
			const frm = this.frm;
			this.metrics.empty();
			const count = (text, value) => $("<span>").text(translate(text, [value])).appendTo(this.metrics);
			count("Selected roles: {0}", selected_roles(frm).length);
			count("Job templates: {0}", profile_names(frm).length);
			const all_modules = frm.doc.__onload?.all_modules || [];
			const blocked = new Set((frm.doc.block_modules || []).map((row) => row.module));
			if (frm.doc.user_type === "System User") count("Visible modules: {0}", all_modules.filter((name) => !blocked.has(name)).length);
			this.note.text(frm.doc.name === "Administrator"
				? translate("Administrator uses framework-wide privileges; the selected role list does not represent its full access.")
				: translate("This is the current form configuration. Saved permissions and business conditions determine actual access."));
			this.preview.toggle(!["Administrator", "Guest"].includes(frm.doc.name));
			if (frm.doc.name === "Administrator") this.explanation.prop("open", true);
		}
		async load_sources() {
			const revision = ++this.revision;
			const names = profile_names(this.frm);
			const guide = this.frm.roles_editor?.dlp_guide;
			if (guide) { guide.sources = new Map(); guide.filter(); }
			this.sources_note.text(names.length ? translate("Loading template sources...") : translate("No job template selected; roles are assigned individually."));
			try {
				const profiles = await read_profiles(names);
				if (revision !== this.revision || names.join("\n") !== profile_names(this.frm).join("\n")) return;
				const sources = new Map();
				for (const profile of profiles) {
					for (const row of profile.roles || []) {
						if (!sources.has(row.role)) sources.set(row.role, []);
						sources.get(row.role).push(profile.name);
					}
				}
				if (guide) { guide.sources = sources; guide.filter(); }
				this.sources_note.text(names.length ? translate("Roles follow the selected templates. Updating a shared template also affects its other users.")
					: translate("No job template selected; roles are assigned individually."));
			} catch {
				if (revision === this.revision) {
					this.sources_note.text(translate("Template sources could not be read. Check Role Profile read permission and retry by refreshing."));
					this.explanation.prop("open", true);
				}
			}
		}
		preview_profile() {
			const frm = this.frm;
			let generation = 0, previewed = null, closed = false;
			const dialog = new frappe.ui.Dialog({
				title: translate("Preview job template"),
				fields: [
					{ fieldname: "profile", fieldtype: "Link", options: "Role Profile", label: translate("Job template"), reqd: 1,
						onchange: () => render() },
					{ fieldname: "preview", fieldtype: "HTML" },
				],
				primary_action_label: translate("Add to current form"),
				primary_action: async () => {
					if (closed || !previewed || !frm.can_edit_roles || frm.doc.name !== previewed.user) return;
					const confirmed = previewed;
					primary.prop("disabled", true);
					try {
						const signature = () => JSON.stringify({ roles: selected_roles(frm), profiles: profile_names(frm) });
						if (signature() !== confirmed.signature) { await render(); return; }
						const name = confirmed.name;
						// Re-read template contents so the confirmed delta cannot silently go stale.
						const profiles = await read_profiles(unique([...profile_names(frm), name]));
						if (closed || previewed !== confirmed || frm.doc.name !== confirmed.user || dialog.get_value("profile") !== name) return;
						const diff = profile_diff(selected_roles(frm), profiles);
						if (signature() !== confirmed.signature || JSON.stringify(diff) !== JSON.stringify(confirmed.diff)) { await render(); return; }
						if (!profile_names(frm).includes(name)) {
							await apply_profile(frm, name, [...diff.added, ...diff.retained]);
							await refresh(frm);
						}
						dialog.hide();
					} catch {
						previewed = null;
						if (!closed) dialog.fields_dict.preview.$wrapper.text(translate("Unable to apply the template. Review the current form before saving or retrying."));
					} finally {
						if (!closed && previewed === confirmed) primary.prop("disabled", profile_names(frm).includes(confirmed.name));
					}
				},
				onhide: () => { closed = true; generation++; previewed = null; primary.prop("disabled", true); },
			});
			const primary = dialog.get_primary_btn();
			const render = async () => {
				if (closed) return;
				const token = ++generation;
				previewed = null;
				primary.prop("disabled", true);
				const name = dialog.get_value("profile");
				const area = dialog.fields_dict.preview.$wrapper;
				area.empty();
				if (!name) return;
				const signature = JSON.stringify({ roles: selected_roles(frm), profiles: profile_names(frm) });
				const user = frm.doc.name;
				area.text(translate("Loading"));
				try {
					const profiles = await read_profiles(unique([...profile_names(frm), name]));
					if (closed || token !== generation) return;
					const diff = profile_diff(selected_roles(frm), profiles);
					area.html(`<div class="dlp-permission-preview"><p>${escape(translate("The selected templates replace individually assigned roles. Review removals before applying. Changes take effect after saving."))}</p>${[
						["Roles to add", diff.added], ["Roles to remove", diff.removed], ["Roles retained", diff.retained],
					].map(([label, values]) => `<h6>${escape(translate(label))} (${values.length})</h6><ul>${values.length ? values.map((value) => `<li>${escape(translate(value))}</li>`).join("") : `<li>${escape(translate("None"))}</li>`}</ul>`).join("")}</div>`);
					previewed = { name, signature, diff, user };
					primary.prop("disabled", profile_names(frm).includes(name));
				} catch {
					if (!closed && token === generation) area.text(translate("Unable to preview this template. Check read permission and retry."));
				}
			};
			primary.prop("disabled", true);
			dialog.show();
		}
	}
	function refresh(frm) {
		if (frm.doctype === "User" && (!frm.can_edit_roles || frm.is_new())) return;
		const changed = () => frm.dlp_permission_guide?.update();
		for (const [editor, kind] of [[frm.roles_editor, "roles"], [frm.module_editor, "modules"]]) {
			if (!editor) continue;
			if (!editor.dlp_guide) editor.dlp_guide = new EditorGuide(editor, kind, changed);
			else editor.dlp_guide.filter();
		}
		if (frm.doctype === "User") {
			frm.set_df_property("role_profiles", "label", translate("Job permission templates"));
			frm.set_df_property("role_profiles", "description", "");
			frm.set_df_property("sb_allow_modules", "label", translate("Module visibility"));
			frm.set_df_property("module_profile", "label", translate("Module visibility template"));
			frm.set_df_property("module_profile", "description", "");
			if (!frm.dlp_permission_guide) frm.dlp_permission_guide = new UserGuide(frm);
			frm.dlp_permission_guide.update();
			return frm.dlp_permission_guide.load_sources();
		}
	}
	const registered = new Set();
	function register(doctype) {
		if (registered.has(doctype)) return;
		// This file follows each doctype's native script; do not register future forms before their editors.
		if (doctype === "User") {
			frappe.ui.form.on("User", { refresh, role_profiles: refresh, module_profile: refresh, on_hide: hide_tooltips });
			frappe.ui.form.on("User Role Profile", { role_profiles_add: refresh, role_profiles_remove: refresh });
		} else if (["Role Profile", "Module Profile"].includes(doctype)) {
			frappe.ui.form.on(doctype, { refresh, on_hide: hide_tooltips });
		} else {
			return;
		}
		registered.add(doctype);
	}
	window.DeepLinkERPPermissionEditor = { describe, profile_diff, read_profiles, apply_profile, matches, groups, roles, modules, refresh, register };
	register(window.cur_frm?.doctype);
})();
