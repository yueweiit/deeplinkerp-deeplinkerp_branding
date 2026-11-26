(function (root, factory) {
    if (typeof module === "object" && module.exports) module.exports = factory;
    root.DeepLinkERPCNCashJournal = factory(root);
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
    "use strict";
    const doctype = "CN Cash Journal Entry";
    const columns = [
        ["posting_date", "Date", "Transaction Date"],
        ["dingtalk_approval_number", "Data", "DingTalk Approval Number"],
        ["direction", "Select", "Receipt or Payment"],
        ["amount", "Currency", "Amount"],
        ["summary", "Small Text", "Journal Description"],
    ];
    const t = text => root.__(text);
    const esc = value => root.frappe.utils.escape_html(String(value ?? ""));
    const valuesOf = row => Object.fromEntries(columns.map(([field]) => [field, row.controls[field].get_value()]));
    function changed(row) {
        return !row.doc.name || columns.some(([field]) => {
            const value = row.controls[field].get_value();
            return field === "amount" ? Number(value) !== Number(row.doc[field])
                : String(value ?? "") !== String(row.doc[field] ?? "");
        });
    }
    function payload(row, values) {
        // Preserve hidden legacy fields and the loaded modified timestamp, so
        // native save retains attachments and detects concurrent changes.
        return {...row.doc, doctype, ...values};
    }
    function installNavigation() {
        const breadcrumbs = root.frappe.breadcrumbs;
        if (!root.frappe.boot?.expense_cash_journal || !breadcrumbs || breadcrumbs.cash_journal_installed) return;
        const original = breadcrumbs.set_custom_breadcrumbs;
        breadcrumbs.set_custom_breadcrumbs = function (options) {
            const route = root.frappe.get_route();
            if (!root.frappe.boot?.expense_cash_journal || route.length !== 1 || route[0] !== "china-cash-journal")
                return original.call(this, options);
            this.append_breadcrumb_element("/desk/expense-management?sidebar=Expense%20Management", esc(t("Expense Management")));
            this.append_breadcrumb_element(null, esc(t(doctype)));
        };
        breadcrumbs.cash_journal_installed = true;
    }
    class Workbench {
        constructor(wrapper) {
            installNavigation();
            this.wrapper = wrapper;
            this.rows = [];
            this.start = 0;
            this.pageSize = 20;
            this.page = root.frappe.ui.make_app_page({parent:wrapper, title:t(doctype), single_column:true});
            this.addButton = this.page.set_primary_action(t("Add Row"), () => this.addRow(), "add");
            this.page.add_inner_button(t("Refresh"), () => this.reload());
            this.company = this.page.add_field({fieldname:"company", label:t("Company"), fieldtype:"Link",
                options:"Company", reqd:1, get_query:() => ({filters:{is_group:0}}), change:() => this.changeFilters()});
            this.currency = this.page.add_field({fieldname:"currency", label:t("Currency"), fieldtype:"Link",
                options:"Currency", reqd:1, change:() => this.changeFilters()});
            this.el = root.$(`<div class="cn-cash-journal">
                <style>
                .cn-cash-journal {padding:20px 0}
                .cn-cash-journal .journal-table {overflow-x:auto;border:1px solid var(--border-color);border-radius:8px;background:var(--card-bg)}
                .cn-cash-journal table {min-width:1000px;table-layout:fixed;margin:0;width:100%}
                .cn-cash-journal th {background:var(--subtle-fg);font-weight:500;color:var(--text-muted)}
                .cn-cash-journal td {vertical-align:top;padding:10px 8px}
                .cn-cash-journal .form-group {margin:0}
                .cn-cash-journal textarea {height:34px!important;min-height:34px;resize:vertical}
                .cn-cash-journal .journal-actions {display:flex;gap:4px;flex-wrap:wrap}
                .cn-cash-journal tr.is-dirty {background:var(--control-bg)}
                .cn-cash-journal .journal-footer {display:flex;justify-content:space-between;align-items:center;margin-top:12px}
                .cn-cash-journal .journal-message {padding:36px;text-align:center;color:var(--text-muted)}
                </style>
                <div class="journal-table"><table class="table"><colgroup><col style="width:150px"><col style="width:220px"><col style="width:120px"><col style="width:150px"><col><col style="width:155px"></colgroup>
                <thead><tr>${columns.map(([, ,label]) => `<th>${esc(t(label))}</th>`).join("")}<th>${esc(t("Actions"))}</th></tr></thead>
                <tbody></tbody></table></div>
                <div class="journal-footer"><span class="text-muted journal-page"></span><div>
                <button class="btn btn-default btn-sm journal-previous">${esc(t("Previous"))}</button>
                <button class="btn btn-default btn-sm journal-next">${esc(t("Next"))}</button></div></div>
            </div>`).appendTo(this.page.main);
            this.body = this.el.find("tbody");
            this.el.find(".journal-previous").on("click", () => this.turnPage(-1));
            this.el.find(".journal-next").on("click", () => this.turnPage(1));
            root.addEventListener("beforeunload", event => {
                if (this.hasChanges()) { event.preventDefault(); event.returnValue = ""; }
            });
            this.ready = this.initialize();
        }
        async initialize() {
            this.syncing = true;
            await root.frappe.model.with_doctype(doctype);
            this.canCreate = root.frappe.model.can_create(doctype);
            this.canWrite = root.frappe.model.can_write(doctype);
            await this.company.set_value(root.frappe.defaults.get_user_default("Company")
                || root.frappe.boot.user.last_selected_values?.Company || "");
            await this.currency.set_value("CNY");
            this.filters = {company:this.company.get_value(), currency:this.currency.get_value()};
            this.syncing = false;
            await this.load();
        }
        hasChanges() { return this.rows.some(row => row.saving || changed(row)); }
        warnUnsaved() { root.frappe.msgprint(t("Save or discard the edited rows before changing the view.")); }
        async changeFilters() {
            if (this.syncing || !this.filters) return;
            if (this.hasChanges()) {
                this.syncing = true;
                await this.company.set_value(this.filters.company);
                await this.currency.set_value(this.filters.currency);
                this.syncing = false;
                this.warnUnsaved();
                return;
            }
            this.filters = {company:this.company.get_value(), currency:this.currency.get_value()};
            this.start = 0;
            await this.load();
        }
        reload() {
            if (this.hasChanges()) return this.warnUnsaved();
            return this.load();
        }
        turnPage(direction) {
            if (this.loading || (direction > 0 && !this.hasNext) || (direction < 0 && !this.start)) return;
            if (this.hasChanges()) return this.warnUnsaved();
            this.start = Math.max(0, this.start + direction * this.pageSize);
            return this.load();
        }
        message(text) {
            this.body.empty().append(root.$("<tr><td colspan='6' class='journal-message'></td></tr>").find("td").text(t(text)).end());
        }
        async load() {
            const generation = this.generation = (this.generation || 0) + 1;
            this.loading = true;
            this.rows = [];
            this.hasNext = false;
            this.updateButtons();
            if (!this.filters.company || !this.filters.currency) {
                this.message("Select a company and currency to record cash transactions.");
                this.loading = false;
                this.loaded = true;
                this.updateButtons();
                return;
            }
            this.message("Loading...");
            try {
                const result = await root.frappe.call({method:"frappe.client.get_list", args:{doctype,
                    fields:["*"], filters:this.filters, order_by:"posting_date desc, creation desc, name desc",
                    limit_start:this.start, limit_page_length:this.pageSize + 1}});
                if (generation !== this.generation) return;
                this.hasNext = result.message.length > this.pageSize;
                this.body.empty();
                for (const doc of result.message.slice(0, this.pageSize)) await this.renderRow(doc);
                if (!this.rows.length) this.message("No cash transactions yet. Click Add Row to begin.");
                this.loaded = true;
            } catch (error) {
                if (generation === this.generation) { this.loaded = false; this.message("Could not load cash transactions. Please refresh and try again."); }
            } finally {
                if (generation === this.generation) { this.loading = false; this.updateButtons(); }
            }
        }
        updateButtons() {
            const ready = !this.loading && this.filters?.company && this.filters?.currency && this.loaded;
            this.addButton?.prop("disabled", !ready || !this.canCreate);
            this.el.find(".journal-previous").prop("disabled", this.loading || !this.start);
            this.el.find(".journal-next").prop("disabled", this.loading || !this.hasNext);
            this.el.find(".journal-page").text(this.filters?.company ? t("Page {0}").replace("{0}", String(this.start / this.pageSize + 1)) : "");
        }
        async addRow() {
            if (this.loading || !this.loaded || !this.canCreate || !this.filters.company || !this.filters.currency) return;
            if (!this.rows.length) this.body.empty();
            const row = await this.renderRow({doctype, ...this.filters,
                posting_date:root.frappe.datetime.get_today(), direction:"expense", amount:0, summary:"", dingtalk_approval_number:""}, true);
            row.controls.dingtalk_approval_number.$input?.trigger("focus");
        }
        async renderRow(doc, prepend = false) {
            const row = {doc, controls:{}, element:root.$("<tr></tr>"), setting:true};
            prepend ? this.body.prepend(row.element) : this.body.append(row.element);
            this.rows.push(row);
            for (const [fieldname, fieldtype, label] of columns) {
                const cell = root.$("<td></td>").appendTo(row.element);
                const df = {fieldname, fieldtype, label:t(label), precision:2, read_only:doc.name && !this.canWrite,
                    change:() => { if (!row.setting) this.updateRow(row); }};
                if (fieldname === "direction") df.options = ["income", "expense"].map(value => ({value, label:root.__(value, null, doctype)}));
                row.controls[fieldname] = root.frappe.ui.form.make_control({parent:cell, df, render_input:true, only_input:true});
                await row.controls[fieldname].set_value(doc[fieldname] ?? "");
                row.controls[fieldname].$input?.attr("aria-label", t(label));
            }
            const actions = root.$("<td><div class='journal-actions'></div></td>").appendTo(row.element).find("div");
            row.save = root.$("<button class='btn btn-primary btn-xs'></button>").text(t("Save")).appendTo(actions).on("click", () => this.saveRow(row));
            row.discard = root.$("<button class='btn btn-default btn-xs'></button>").text(t("Discard Changes")).appendTo(actions).on("click", () => this.discardRow(row));
            row.open = root.$("<button class='btn btn-default btn-xs'></button>").text(t("Details")).appendTo(actions).on("click", () => {
                if (this.hasChanges()) return this.warnUnsaved();
                root.frappe.set_route("Form", doctype, row.doc.name);
            });
            row.setting = false;
            this.updateRow(row);
            return row;
        }
        updateRow(row) {
            const dirty = changed(row), editable = row.doc.name ? this.canWrite : this.canCreate;
            row.element.toggleClass("is-dirty", dirty);
            row.save.prop("disabled", row.saving || !dirty || !editable).toggle(!!editable);
            row.discard.prop("disabled", row.saving || !dirty).toggle(!!editable);
            row.open.toggle(!!row.doc.name).prop("disabled", row.saving);
        }
        async resetRow(row) {
            row.setting = true;
            for (const [field] of columns) await row.controls[field].set_value(row.doc[field] ?? "");
            row.setting = false;
        }
        async discardRow(row) {
            if (row.saving) return;
            if (!row.doc.name) {
                this.rows = this.rows.filter(item => item !== row);
                row.element.remove();
                if (!this.rows.length) this.message("No cash transactions yet. Click Add Row to begin.");
            } else { await this.resetRow(row); this.updateRow(row); }
        }
        async saveRow(row) {
            if (row.saving) return;
            row.saving = true;
            row.element.find("input, select, textarea").prop("disabled", true);
            this.updateRow(row);
            try {
                for (const control of Object.values(row.controls)) {
                    if (control.get_input_value) await control.parse_validate_and_set_in_model(control.get_input_value());
                }
                const values = valuesOf(row);
                if (!values.posting_date || !["income", "expense"].includes(values.direction)
                        || !Number.isFinite(Number(values.amount)) || Number(values.amount) <= 0) {
                    root.frappe.msgprint(t("Enter a date, receipt or payment, and an amount greater than zero."));
                    return;
                }
                const result = await root.frappe.call({method:"frappe.client.save", args:{doc:payload(row, values)}});
                row.doc = result.message;
                await this.resetRow(row);
                root.frappe.show_alert({message:t("Saved"), indicator:"green"});
            } catch (error) {
                root.frappe.show_alert({message:t("Row not saved. Your edits have been kept; check the error and retry."), indicator:"red"});
            } finally {
                row.saving = false;
                row.element.find("input, select, textarea").prop("disabled", false);
                this.updateRow(row);
            }
        }
    }
    return {Workbench, columns, changed, payload, installNavigation};
});
