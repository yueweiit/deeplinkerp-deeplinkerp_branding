(function (root, factory) {
	if (typeof module === "object" && module.exports) module.exports = factory;
	root.DeepLinkERPMexicoExpenseMappings = factory(root);
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
	"use strict";
	const t = (text, args = []) => root.__ ? root.__(text, args) : text.replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");
	const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
	const aliasKey = (row) => JSON.stringify([row.entity_kind, String(row.source_entity || "").trim().replace(/\s+/g, " ")]);
	function replaceAliases(mappings, company, aliases) {
		const result = mappings.filter((row) => row.company !== company).map((row) => ({...row}));
		const seen = new Set(result.map(aliasKey));
		for (const row of aliases) {
			const clean = {company, source_entity: String(row.source_entity || "").trim(), entity_kind: row.entity_kind};
			if (!clean.source_entity || clean.source_entity.length > 500 || !["entity", "department"].includes(clean.entity_kind)) throw new Error(t("Invalid subsidiary mappings"));
			if (seen.has(aliasKey(clean))) throw new Error(t("Duplicate source entity mapping"));
			seen.add(aliasKey(clean));
			result.push(clean);
		}
		if (result.length > 1000) throw new Error(t("Invalid subsidiary mappings"));
		return result;
	}
	class Editor {
		constructor({wrapper, companies, mappings, configVersion, fetchPage}) {
			this.wrapper = wrapper;
			this.companies = companies;
			this.mappings = mappings.map((row) => ({...row}));
			this.configVersion = configVersion;
			this.fetchPage = fetchPage;
			this.start = 0;
			this.rows = [];
			this.wrapper.addClass("mx-company-mappings").html('<div class="mx-alias-groups"></div><div class="mx-unmapped-entities"></div>');
			this.renderGroups();
			this.wrapper.on("click", "[data-alias-company]", (event) => this.editCompany(this.companies[Number(event.currentTarget.dataset.aliasCompany)]));
			this.wrapper.on("click", "[data-add-alias]", (event) => this.addAlias(Number(event.currentTarget.dataset.addAlias)));
			this.wrapper.on("click", "[data-alias-page]", (event) => this.loadPage(Number(event.currentTarget.dataset.aliasPage)));
		}
		renderGroups() {
			this.wrapper.find(".mx-alias-groups").html(`<h5>${esc(t("Subsidiary Aliases"))}</h5><p class="text-muted">${esc(t("Maintain historical names under each subsidiary. Entity and department aliases are separate. Changes take effect after Save Settings."))}</p>${this.companies.map((company, i) => {
				const rows = this.mappings.filter((row) => row.company === company);
				return `<details class="mx-alias-group"><summary>${esc(company)} <span class="text-muted">${esc(t("{0} aliases", [rows.length]))}</span></summary><div class="mx-alias-body">${["entity", "department"].map((kind) => `<div><strong>${esc(t(kind === "entity" ? "Expense Entity" : "Department"))}</strong><ul>${rows.filter((row) => row.entity_kind === kind).map((row) => `<li>${esc(row.source_entity)}</li>`).join("") || `<li class="text-muted">${esc(t("No aliases configured"))}</li>`}</ul></div>`).join("")}<button type="button" class="btn btn-default btn-sm" data-alias-company="${i}">${esc(t("Edit Aliases"))}</button></div></details>`;
			}).join("")}`);
		}
		editCompany(company) {
			const dialog = new root.frappe.ui.Dialog({
				title: t("Edit Aliases"), size: "large",
				fields: [
					{fieldname:"company", label:t("Subsidiary"), fieldtype:"Data", read_only:1, default:company},
					{fieldname:"aliases", label:t("Historical Source Names"), fieldtype:"Table",
						data:this.mappings.filter((row) => row.company === company).map((row) => ({...row})),
						fields:[
							{fieldname:"source_entity", label:t("Source Entity"), fieldtype:"Small Text", reqd:1, in_list_view:1, columns:7},
							{fieldname:"entity_kind", label:t("Entity Kind"), fieldtype:"Select", reqd:1, in_list_view:1, columns:3, default:"entity", options:[{value:"entity",label:t("Expense Entity")},{value:"department",label:t("Department")}]},
						]},
				],
				primary_action_label:t("Apply to Settings"),
				primary_action:(values) => {
					try {
						this.mappings = replaceAliases(this.mappings, company, values.aliases || []);
					} catch (error) { return root.frappe.msgprint(error.message); }
					dialog.hide();
					this.renderGroups();
					this.renderUnmapped();
				},
			});
			dialog.show();
		}
		async loadPage(start = 0) {
			const request = this.request = (this.request || 0) + 1;
			this.loading = true;
			this.error = "";
			this.renderUnmapped();
			try {
				const page = await this.fetchPage(start);
				if (request !== this.request) return;
				if (page.config_version !== this.configVersion) throw new Error(t("Settings changed; reload and try again"));
				this.rows = page.rows;
				this.start = page.start;
				this.hasMore = page.has_more;
			} catch (error) {
				if (request !== this.request) return;
				this.error = error.message || t("Unable to load unmapped names");
			} finally {
				if (request === this.request) { this.loading = false; this.renderUnmapped(); }
			}
		}
		renderUnmapped() {
			const header = `<h5>${esc(t("Unmapped Source Names"))}</h5><p class="text-muted">${esc(t("Suggestions do not assign a company. Select the subsidiary and add the alias, then Save Settings to recheck existing details."))}</p>`;
			if (this.loading || this.error) {
				this.wrapper.find(".mx-unmapped-entities").html(`${header}<p role="status">${esc(this.loading ? t("Loading...") : this.error)}</p>${this.error ? `<button type="button" class="btn btn-default btn-sm" data-alias-page="${this.start}">${esc(t("Retry"))}</button>` : ""}`);
				return;
			}
			const rows = this.rows.map((row, i) => {
				const mapped = this.mappings.find((mapping) => aliasKey(mapping) === aliasKey(row));
				const empty = !String(row.source_entity || "").trim();
				const suggestions = (row.suggestions || []).map((s) => s.company);
				return `<tr><td>${esc(row.source_entity || t("Missing Source Entity"))}<small>${esc(t(row.entity_kind === "department" ? "Department" : "Expense Entity"))}</small></td><td>${esc(row.line_count)}</td><td>${esc(suggestions.join(" / ") || "—")}</td><td>${mapped ? `<span>${esc(mapped.company)}</span><small>${esc(t("Included in Settings"))}</small>` : empty ? esc(t("Complete the source information first")) : `<select class="form-control input-sm" aria-label="${esc(t("Subsidiary"))}" data-alias-target="${i}"><option value="">${esc(t("Select Subsidiary"))}</option>${this.companies.map((company) => `<option value="${esc(company)}">${esc(company)}</option>`).join("")}</select><button type="button" class="btn btn-default btn-sm" data-add-alias="${i}">${esc(t("Add Alias"))}</button>`}</td></tr>`;
			}).join("");
			this.wrapper.find(".mx-unmapped-entities").html(`${header}<div class="mx-alias-scroll"><table class="table"><thead><tr><th>${esc(t("Source Entity"))}</th><th>${esc(t("Expense Lines"))}</th><th>${esc(t("Suggested Subsidiaries"))}</th><th>${esc(t("Subsidiary"))}</th></tr></thead><tbody>${rows || `<tr><td colspan="4">${esc(t("No unmapped names"))}</td></tr>`}</tbody></table></div><div class="mx-alias-pages"><button type="button" class="btn btn-default btn-sm" data-alias-page="${this.start - 50}" ${this.start === 0 ? "disabled" : ""}>${esc(t("Previous"))}</button><button type="button" class="btn btn-default btn-sm" data-alias-page="${this.start + 50}" ${this.hasMore ? "" : "disabled"}>${esc(t("Next"))}</button></div>`);
		}
		addAlias(index) {
			const row = this.rows[index];
			const company = this.wrapper.find(`[data-alias-target="${index}"]`).val();
			if (!this.companies.includes(company)) return root.frappe.msgprint(t("Select Subsidiary"));
			try {
				this.mappings = replaceAliases(this.mappings, company, [...this.mappings.filter((r) => r.company === company), row]);
			} catch (error) { return root.frappe.msgprint(error.message); }
			this.renderGroups();
			this.renderUnmapped();
		}
	}
	return {Editor, aliasKey, replaceAliases};
});
