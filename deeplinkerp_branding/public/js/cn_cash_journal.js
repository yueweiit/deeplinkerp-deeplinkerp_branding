(function (root, factory) {
    if (typeof module === "object" && module.exports) module.exports = factory;
    root.DeepLinkERPCNCashJournal = factory(root);
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
    "use strict";
    const doctype = "CN Cash Journal Entry";
    const editFields = [
        ["posting_date", "Date", "Transaction Date"],
        ["dingtalk_approval_number", "Data", "DingTalk Approval Number"],
        ["journal_category", "Data", "Journal Category"],
        ["journal_subcategory", "Data", "Journal Subcategory"],
        ["journal_detail", "Data", "Journal Detail Category"],
        ["summary", "Small Text", "Journal Description"],
        ["income", "Currency", "Income"],
        ["expense", "Currency", "Expense"],
    ];
    const categories = ["journal_category", "journal_subcategory", "journal_detail"];
    const ledgerColumns = [["Transaction Date",11], ["Journal Category",9], ["Journal Subcategory",9], ["Journal Detail Category",10],
        ["Transaction Summary",18], ["Income",11], ["Expense",11], ["Running Balance",14], ["Actions",7]];
    const t = text => root.__(text);
    const esc = value => root.frappe.utils.escape_html(String(value ?? ""));
    const totals = {opening:"Opening Balance", income:"Total Income", expense:"Total Expense", closing:"Closing Balance"};
    const money = (value, currency) => root.DeepLinkERPMexicoExpenseMoney.format(value, currency);
    const valuesOf = row => Object.fromEntries(Object.entries(row.controls).map(([key, c]) => [key, c.get_value()]));
    function cellValue(doc, field) {
        if (field === "income" || field === "expense") return doc.direction === field ? doc.amount : null;
        return doc[field] ?? "";
    }
    function changed(row) {
        return !row.doc.name || Object.keys(row.controls).some(field => {
            const value = row.controls[field].get_value();
            const saved = cellValue(row.doc, field);
            return ["income","expense"].includes(field) ? Number(value ?? 0) !== Number(saved)
                : String(value ?? "") !== String(saved);
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
            this.period = {mode:"all", from:"", to:""};
            this.page = root.frappe.ui.make_app_page({parent:wrapper, title:t(doctype), single_column:true});
            this.addButton = this.page.set_primary_action(t("Add Row"), () => this.addRow(), "add");
            this.page.add_inner_button(t("Refresh"), () => this.reload());
            this.company = this.page.add_field({fieldname:"company", label:t("Company"), fieldtype:"Link",
                options:"Company", reqd:1, get_query:() => ({filters:{is_group:0}}), change:() => this.changeFilters()});
            this.currency = this.page.add_field({fieldname:"currency", label:t("Currency"), fieldtype:"Link",
                options:"Currency", reqd:1, change:() => this.changeFilters()});
            this.account = this.page.add_field({fieldname:"account", label:t("Cash or Bank Account"), fieldtype:"Link",
                options:"Account", get_query:() => ({filters:{company:this.company.get_value(), is_group:0,
                    disabled:0, account_type:["in",["Cash","Bank"]]}}), change:() => this.changeFilters()});
            this.el = root.$(`<div class="cn-cash-journal">
                <form class="journal-period">
                    <label>${esc(t("Journal Period"))}<select name="mode" class="form-control">
                        <option value="all">${esc(t("All Months"))}</option>
                        <option value="single">${esc(t("Single Month"))}</option>
                        <option value="range">${esc(t("Month Range"))}</option>
                    </select></label>
                    <label hidden><span class="journal-from-label"></span>
                        <input class="form-control" name="from" type="month" placeholder="YYYY-MM" disabled required></label>
                    <label hidden>${esc(t("End Month"))}
                        <input class="form-control" name="to" type="month" placeholder="YYYY-MM" disabled required></label>
                    <button class="btn btn-default btn-sm" type="submit">${esc(t("Apply Period"))}</button>
                    <span class="journal-status text-muted" role="status"><span class="journal-scope"></span><span class="journal-count"></span></span>
                </form>
                <dl class="journal-summary">${Object.entries(totals).map(([key, label]) =>
                    `<div><dt>${esc(t(label))}</dt><dd data-total="${key}">—</dd></div>`).join("")}</dl>
                <div class="journal-table" tabindex="0" role="region" aria-label="${esc(t(doctype))}">
                <table class="table"><colgroup></colgroup><thead><tr></tr></thead><tbody></tbody></table></div>
                <div class="journal-footer"><span class="text-muted journal-page"></span><div>
                <button class="btn btn-default btn-sm journal-previous">${esc(t("Previous"))}</button>
                <button class="btn btn-default btn-sm journal-next">${esc(t("Next"))}</button></div></div>
            </div>`).appendTo(this.page.main);
            this.body = this.el.find("tbody");
            this.periodForm = this.el.find(".journal-period")[0];
            const {mode, from, to} = this.periodForm.elements;
            mode.addEventListener("change", () => this.showPeriod({
                mode:mode.value, from:from.value, to:to.value >= from.value ? to.value : from.value
            }));
            this.periodForm.addEventListener("submit", event => {
                event.preventDefault();
                this.applyPeriod(mode.value, from.value, to.value);
            });
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
            this.filters = {company:this.company.get_value(), currency:this.currency.get_value(), account:""};
            this.syncing = false;
            await this.load();
        }
        hasChanges() { return this.rows.some(row => row.setting || row.saving || changed(row)); }
        warnUnsaved() { root.frappe.msgprint(t("Save or discard the edited rows before changing the view.")); }
        showPeriod(period) {
            const {mode, from, to} = this.periodForm.elements;
            mode.value = period.mode;
            from.disabled = mode.value === "all";
            to.disabled = mode.value !== "range";
            from.closest("label").hidden = from.disabled;
            to.closest("label").hidden = to.disabled;
            this.el.find(".journal-from-label").text(t(mode.value === "range" ? "Start Month" : "Transaction Month"));
            from.value = period.from || root.frappe.datetime.get_today().slice(0, 7);
            to.value = period.to || from.value;
        }
        applyPeriod(mode, from, to) {
            if (!this.filters) return;
            if (this.hasChanges()) {
                this.showPeriod(this.period);
                return this.warnUnsaved();
            }
            if (mode === "all") from = to = "";
            else {
                if (mode === "single") to = from;
                const month = /^(?!0000)\d{4}-(0[1-9]|1[0-2])$/;
                if (!month.test(from) || !month.test(to))
                    return root.frappe.msgprint(t("Enter both months in YYYY-MM format."));
                if (from > to) return root.frappe.msgprint(t("The start month must not be after the end month."));
            }
            this.period = {mode, from, to};
            this.start = 0;
            return this.load();
        }
        async changeFilters() {
            if (this.syncing || !this.filters) return;
            if (this.hasChanges()) {
                this.syncing = true;
                await this.company.set_value(this.filters.company);
                await this.currency.set_value(this.filters.currency);
                await this.account.set_value(this.filters.account);
                this.syncing = false;
                this.warnUnsaved();
                return;
            }
            if (this.company.get_value() !== this.filters.company || this.currency.get_value() !== this.filters.currency) {
                this.syncing = true;
                await this.account.set_value("");
                this.syncing = false;
            }
            this.filters = {company:this.company.get_value(), currency:this.currency.get_value(), account:this.account.get_value() || ""};
            this.start = 0;
            await this.load();
        }
        reload() {
            if (this.syncing || !this.filters) return;
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
            this.body.empty().append(root.$(`<tr><td colspan="${this.columns.length}" class="journal-message"></td></tr>`).find("td").text(t(text)).end());
        }
        get columns() {
            return this.filters.account ? ledgerColumns : ledgerColumns.filter(([label]) => label !== "Running Balance");
        }
        async load() {
            const generation = this.generation = (this.generation || 0) + 1;
            this.loading = true;
            this.rows = [];
            this.hasNext = false;
            this.needsRefresh = false;
            this.el.find("colgroup").html(this.columns.map(([,width]) => `<col style="width:${width}%">`).join(""));
            this.el.find("thead tr").html(this.columns.map(([label]) => `<th scope="col">${esc(t(label))}</th>`).join(""));
            this.renderSummary(null);
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
                const {mode, from, to} = this.period;
                const months = mode === "all" ? {} : {from_month:from, to_month:to};
                const result = await root.frappe.call({method:"deeplinkerp_branding.services.cn_cash_book.list_page",
                    args:{...this.filters, ...months, start:this.start}});
                if (generation !== this.generation) return;
                this.hasNext = result.message.has_next;
                this.balances = result.message.balances;
                this.body.empty();
                for (const doc of result.message.rows) {
                    await this.renderRow(doc);
                    if (generation !== this.generation) return;
                }
                this.renderSummary(result.message.summary);
                if (!this.rows.length) this.message("No transactions match these filters.");
                this.loaded = true;
            } catch (error) {
                if (generation === this.generation) { this.loaded = false; this.message("Could not load cash transactions. Please refresh and try again."); }
            } finally {
                if (generation === this.generation) { this.loading = false; this.updateButtons(); }
            }
        }
        renderSummary(summary) {
            for (const key of Object.keys(totals)) {
                const balance = key === "opening" || key === "closing";
                const value = money(balance && !this.filters.account ? null : summary?.[key], this.filters.currency);
                this.el.find(`[data-total="${key}"]`).text(value);
            }
            const {mode, from, to} = this.period;
            this.el.find(".journal-scope").text(mode === "all" ? t("All Months") : mode === "single" ? from : `${from} – ${to}`);
            this.el.find(".journal-count").text(summary?.count == null ? "" :
                " · " + t("Visible transactions: {0}").replace("{0}", String(summary.count)));
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
            const today = root.frappe.datetime.get_today(), month = today.slice(0, 7);
            const {mode, from, to} = this.period;
            const posting_date = mode === "all" || (from <= month && month <= to) ? today : from + "-01";
            const row = await this.renderRow({doctype, ...this.filters,
                posting_date, direction:"expense", amount:0, summary:"", dingtalk_approval_number:""}, true);
            row.controls.dingtalk_approval_number.$input?.trigger("focus");
        }
        async renderRow(doc, prepend = false) {
            const row = {doc, controls:{}, element:root.$("<tr></tr>")};
            prepend ? this.body.prepend(row.element) : this.body.append(row.element);
            this.rows.push(row);
            if (prepend) await this.editRow(row);
            else this.displayRow(row);
            return row;
        }
        displayRow(row) {
            row.controls = {};
            row.editing = false;
            const {doc} = row, currency = this.filters.currency;
            const href = root.frappe.utils.get_form_link(doctype, doc.name);
            row.element.removeClass("is-dirty").html(`
                <td><a class="journal-detail" href="${esc(href)}" title="${esc(t("Details"))}">${esc(root.frappe.datetime.str_to_user(doc.posting_date))}</a></td>
                ${categories.map(field => `<td>${esc(doc[field])}</td>`).join("")}
                <td class="journal-description">${esc(doc.summary || "—")}</td>
                ${["income", "expense"].map(key => `<td class="journal-number">${esc(money(cellValue(doc, key), currency))}</td>`).join("")}
                ${this.filters.account ? `<td class="journal-number journal-balance">${esc(money(this.balances?.[doc.name], currency))}</td>` : ""}
                <td>${this.canWrite ? `<button type="button" class="btn btn-default btn-xs journal-edit"
                    ${this.canEditRow(row) ? "" : `disabled title="${esc(t("This amount cannot be edited safely in the ledger."))}"`}>${esc(t("Edit"))}</button>` : ""}</td>`);
            row.element.find(".journal-detail").on("click", event => {
                if (!this.hasChanges()) return;
                event.preventDefault();
                event.stopPropagation();
                this.warnUnsaved();
            });
            row.element.find(".journal-edit").on("click", () => this.editRow(row));
        }
        canEditRow(row) {
            return row.doc.name ? this.canWrite && root.DeepLinkERPMexicoExpenseMoney.withinNativeRange(row.doc.amount) : this.canCreate;
        }
        async editRow(row) {
            if (row.editing || !this.canEditRow(row)) return;
            row.setting = row.editing = true;
            const panel = root.$(`<td colspan="${this.columns.length}"><div class="journal-editor"></div></td>`).appendTo(row.element.empty());
            const fields = panel.find(".journal-editor");
            for (const [fieldname, fieldtype, label] of editFields) {
                const parent = root.$("<div></div>").appendTo(fields);
                const df = {fieldname, fieldtype, label:t(label), precision:2,
                    change:() => { if (row.editing && !row.setting) this.updateRow(row); }};
                row.controls[fieldname] = root.frappe.ui.form.make_control({parent, df, render_input:true});
                const value = cellValue(row.doc, fieldname);
                await row.controls[fieldname].set_value(fieldtype === "Currency" && value != null ? Number(value) : value);
                if (fieldtype === "Currency" && value != null && Number(value) !== row.controls[fieldname].get_value()) {
                    row.setting = false;
                    this.displayRow(row);
                    return root.frappe.msgprint(t("This amount cannot be edited safely in the ledger."));
                }
                row.controls[fieldname].$input?.attr("aria-label", t(label));
            }
            const actions = root.$("<div class='journal-actions'></div>").appendTo(panel);
            row.save = root.$("<button class='btn btn-primary btn-xs'></button>").text(t("Save")).appendTo(actions).on("click", () => this.saveRow(row));
            row.discard = root.$("<button class='btn btn-default btn-xs'></button>").text(t("Discard Changes")).appendTo(actions).on("click", () => this.discardRow(row));
            row.setting = false;
            this.updateRow(row);
            if (row.doc.name) row.controls.posting_date.$input?.trigger("focus");
        }
        updateRow(row) {
            const dirty = changed(row);
            row.element.toggleClass("is-dirty", dirty);
            row.save.prop("disabled", row.saving || !dirty);
            row.discard.prop("disabled", !!row.saving);
        }
        async discardRow(row) {
            if (row.saving) return;
            if (!row.doc.name) {
                this.rows = this.rows.filter(item => item !== row);
                row.element.remove();
                if (!this.rows.length) this.message("No transactions match these filters.");
            } else this.displayRow(row);
            if (this.needsRefresh && !this.hasChanges()) await this.load();
        }
        async saveRow(row) {
            if (row.saving || !row.editing || !this.canEditRow(row)) return;
            row.saving = true;
            row.element.find("input, select, textarea").prop("disabled", true);
            this.updateRow(row);
            try {
                for (const control of Object.values(row.controls)) {
                    if (control.get_input_value) await control.parse_validate_and_set_in_model(control.get_input_value());
                }
                const values = valuesOf(row);
                const {income, expense, ...fields} = values;
                const amounts = [income,expense].map(Number);
                if (!fields.posting_date || amounts.some(n=>!Number.isFinite(n) || n<0) || amounts.filter(n=>n>0).length !== 1) {
                    root.frappe.msgprint(t("Enter a date and a positive amount in either income or expense."));
                    return;
                }
                const direction = amounts[0] > 0 ? "income" : "expense";
                const amount = Math.max(...amounts);
                if (!root.DeepLinkERPMexicoExpenseMoney.withinNativeRange(amount))
                    return root.frappe.msgprint(t("This amount cannot be edited safely in the ledger."));
                if (direction !== row.doc.direction || amount !== Number(row.doc.amount))
                    Object.assign(fields, {direction, amount:amount.toFixed(2)});
                const result = await root.frappe.call({method:"frappe.client.save", args:{doc:payload(row, fields)}});
                row.doc = result.message;
                this.balances = {};
                this.needsRefresh = true;
                this.renderSummary(null);
                this.el.find(".journal-balance").text("—");
                this.displayRow(row);
                root.frappe.show_alert({message:t("Saved"), indicator:"green"});
            } catch (error) {
                root.frappe.show_alert({message:t("Row not saved. Your edits have been kept; check the error and retry."), indicator:"red"});
            } finally {
                row.saving = false;
                if (row.editing) {
                    row.element.find("input, select, textarea").prop("disabled", false);
                    this.updateRow(row);
                }
            }
            if (this.needsRefresh && !this.hasChanges()) await this.load();
        }
    }
    return {Workbench, changed, payload, installNavigation};
});
