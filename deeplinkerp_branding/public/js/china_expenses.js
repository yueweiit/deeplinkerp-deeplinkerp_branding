(function (root, factory) {
    if (typeof module === "object" && module.exports) module.exports = factory;
    root.DeepLinkERPChinaExpenses = factory(root);
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
    "use strict";
    const shared = root.DeepLinkERPMexicoExpenses;
    const operating = root.DeepLinkERPChinaOperatingExpenses;
    const t = (text) => root.__ ? root.__(text) : text;
    const esc = shared?.esc || ((value) => String(value ?? "").replace(/[&<>"']/g,
        (c) => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c])));
    const routes = {
        overview: "china-expenses", details: "china-expense-details",
        budget_analysis: "budget-dashboard", analysis: "china-expense-reports", payroll: "china-personnel-report", review: "china-expense-review",
    };
    const titles = {budget_analysis:"Budget Execution Analysis", overview:"Expense Overview", details:"Expense Details", analysis:"Expenditure Analysis",
        payroll:"Personnel Analysis", review:"Data Review"};
    const forms = [["", "All Approval Forms"], ["operating", "Operating Expenses"], ["budget", "Budget"], ["purchase", "Purchase Expenses"]];
    const bases = [["period", "Expense Period"], ["request", "Application Date"], ["planned", "Planned Payment Date"], ["cashier", "Cashier Processing Date"]];
    const statuses = [["active", "Active Applications"], ["approved", "Approved"], ["pending", "In Approval"],
        ["all", "All Statuses"], ["rejected", "Rejected"], ["terminated", "Terminated"], ["withdrawn", "Withdrawn"], ["unknown", "Pending Review"]];
    const pending = "CN statistics are not connected yet";
    const viewFor = (route) => Object.keys(routes).find((view) => routes[view] === route?.[0]);
    function today() {
        const parts = new Intl.DateTimeFormat("en", {timeZone:"Asia/Shanghai", year:"numeric", month:"2-digit", day:"2-digit"})
            .formatToParts(new Date());
        const values = Object.fromEntries(parts.map(({type, value}) => [type, value]));
        return `${values.year}-${values.month}-${values.day}`;
    }
    function defaults(day = today()) {
        return {...shared.monthRange(day.slice(0, 7)), basis:"request", company:"", approval_type:"", currency:"CNY", status:"all",
            quick_tab:"all", keyword:"", company_review:"", application_type:"", budget_tab:"ledger", budget_type:"", department_id:""};
    }
    function validFilters(values, companies) {
        if (!values || typeof values !== "object" || Array.isArray(values)) return {};
        const clean = {};
        if (shared.validDateRange(values)) Object.assign(clean, {from_date:values.from_date, to_date:values.to_date});
        for (const [key, allowed] of Object.entries({basis:bases.map(([id]) => id), company:["", ...companies],
            approval_type:forms.map(([id]) => id), currency:["CNY", "USD", "MXN"], status:statuses.map(([id]) => id),
            budget_tab:["ledger", "quota"], budget_type:["", "production", "non_production"],
            quick_tab:["all", "pending_work", "approvals_running", "paid", "reconciliation"],
            company_review:["", "unmapped", "stale", "confirmed"], application_type:["", "payment", "reimbursement", "unclassified"]})) {
            if (allowed.includes(values[key])) clean[key] = values[key];
        }
        if (typeof values.department_id === "string" && values.department_id.length <= 140) clean.department_id = values.department_id;
        if (typeof values.keyword === "string" && values.keyword.length <= 200) clean.keyword = values.keyword;
        return clean;
    }
    function installBreadcrumbs() {
        const breadcrumbs = root.frappe.breadcrumbs;
        if (!breadcrumbs || breadcrumbs.cn_expenses_installed) return;
        const original = breadcrumbs.set_custom_breadcrumbs;
        breadcrumbs.set_custom_breadcrumbs = function (options) {
            const view = viewFor(root.frappe.get_route());
            if (!view) return original.call(this, options);
            this.append_breadcrumb_element("/desk/china-expenses", esc(t("China Operating Expenses")));
            if (["analysis", "payroll", "budget_analysis"].includes(view)) {
                this.append_breadcrumb_element("/desk/china-expense-reports", esc(t("Expense Reports")));
            }
            const item = root.document.createElement("li"), current = root.document.createElement("span");
            current.className = "mx-breadcrumb-current";
            current.setAttribute("aria-current", "page");
            current.textContent = t(options.label || titles[view]);
            item.appendChild(current);
            this.$breadcrumbs.append(item);
        };
        breadcrumbs.cn_expenses_installed = true;
    }
    const button = (label, action, extra = "") => `<button type="button" class="mx-button ${extra}" data-cn-action="${action}">${esc(t(label))}</button>`;
    const help = (label) => root.DeepLinkERPMexicoExpenseInsights?.help(t(label)) || "";
    class Workbench {
        constructor(wrapper) {
            this.wrapper = wrapper;
            this.view = viewFor([wrapper.page_name]) || "overview";
            this.filters = defaults();
            this.companies = [];
            this.grouping = "company";
            this.start = 0;
            this.generation = 0;
            this.page = root.frappe.ui.make_app_page({parent:wrapper, title:t(titles[this.view]), single_column:true});
            this.el = root.document.createElement("div");
            this.el.className = "mx-expenses cn-expenses";
            this.page.main.append(this.el);
            installBreadcrumbs();
            this.el.addEventListener("change", (event) => {
                if (!Object.hasOwn(this.filters, event.target.dataset.filter)) return;
                Object.assign(this.filters, validFilters({[event.target.dataset.filter]:event.target.value}, this.activeCompanies()));
                if (event.target.dataset.filter === "basis") this.filters.quick_tab = "all";
                this.refresh();
            });
            this.el.addEventListener("click", (event) => {
                const budgetControl = event.target.closest("[data-budget-action]");
                if (budgetControl) return root.CNBudget?.action(this, budgetControl);
                const source = event.target.closest(".dlp-operating-open");
                if (source) return operating.openSource(source.dataset.source, source.dataset.tab, () => this.refresh());
                const control = event.target.closest("[data-cn-action]");
                const action = control?.dataset.cnAction;
                if (action === "reset") this.filters = {...defaults(), approval_type:this.filters.approval_type, budget_tab:this.filters.budget_tab};
                else if (["company", "trend"].includes(action)) this.grouping = action;
                else if (action === "review") return root.frappe.set_route(routes.review, {cn_filters:JSON.stringify({...this.filters, quick_tab:"all"})});
                else if (action === "tab") this.filters.quick_tab = control.dataset.tab;
                else if (["previous", "next"].includes(action)) {
                    this.start = Math.max(0, this.start + (action === "next" ? 20 : -20));
                    return this.refresh(false);
                }
                else if (action === "retry") return this.initialize().catch(() => this.loadError());
                else if (!action) return;
                this.refresh();
            });
            root.$(wrapper).on("hide.cn-expenses", () => this.dispose());
            this.page.add_inner_button(t("Refresh"), () => this.initialize().catch(() => this.loadError()));
            this.exportButton = this.page.add_inner_button(t("Export Excel"), () => this.exportCurrent());
            if (root.DeepLinkERPOperatingExpenseDrawer?.isManager?.()) {
                this.syncSettingsItem = this.page.add_menu_item(t("Sync Settings"), () => this.openSync(true));
                this.syncButton = this.page.add_inner_button(t("Synchronize Data"), () => this.openSync(false));
            }
            this.ready = this.initialize().catch(() => this.loadError());
        }
        activeCompanies() { return this.filters.approval_type === "budget" ? (this.budgetCompanies || this.companies) : this.companies; }
        openSync(settings) {
            if (this.filters.approval_type === "budget" && root.CNBudget) return root.CNBudget.sync(this, settings);
            if (!["", "operating"].includes(this.filters.approval_type)) return;
            return root.DeepLinkERPOperatingExpenseDrawer[settings ? "openSettings" : "openSync"](() => this.refresh());
        }
        async loadCompanies() {
            // Same permitted legal-company scope as the existing operating service.
            const response = await root.frappe.call({method:"frappe.client.get_list", args:{
                doctype:"Company", fields:["name"], limit_page_length:0, order_by:"name asc",
            }});
            return response.message.map((row) => row.name);
        }
        async initialize() {
            const id = this.initializationId = (this.initializationId || 0) + 1;
            const current = () => id === this.initializationId && this.isCurrentRoute();
            this.initializing = true;
            try {
                const companies = await this.loadCompanies();
                if (!current()) return;
                this.companies = companies;
                this.restore();
                this.initialized = true;
                await this.refresh();
            } catch (error) {
                if (current()) this.loadError();
            } finally {
                if (id === this.initializationId) this.initializing = false;
            }
        }
        loadError() {
            this.generation++;
            this.data = null;
            this.loading = false;
            this.exportButton?.prop("disabled", true);
            this.disposeFilterControls();
            this.el.innerHTML = `<div class="mx-empty">${esc(t("CN workspace could not be loaded"))}${button("Retry", "retry")}</div>`;
        }
        isCurrentRoute() { return root.frappe.get_route()?.[0] === this.wrapper.page_name; }
        preferenceKey() {
            const user = root.frappe.session?.user;
            return user && user !== "Guest" ? `cn-expenses:preferences:v1:${user}` : null;
        }
        restore() {
            try {
                const key = this.preferenceKey(), saved = key && JSON.parse(root.localStorage?.getItem(key) || "null");
                if (saved) Object.assign(this.filters, validFilters(saved, [...this.companies, ...(typeof saved.company === "string" ? [saved.company] : [])]));
            } catch { /* Optional browser preferences. */ }
            const params = new URLSearchParams(root.location?.search || "");
            try {
                let filters = JSON.parse(params.get("cn_filters"));
                // Native routing can serialize the JSON route option once more.
                if (typeof filters === "string") filters = JSON.parse(filters);
                Object.assign(this.filters, validFilters(filters, [...this.companies, ...(typeof filters?.company === "string" ? [filters.company] : [])]));
            }
            catch { /* Invalid links retain valid defaults. */ }
            if (root.frappe.route_options?.cn_filters) {
                try { const values = JSON.parse(root.frappe.route_options.cn_filters); Object.assign(this.filters, validFilters(values, [...this.companies, ...(typeof values.company === "string" ? [values.company] : [])])); }
                catch { /* Invalid navigation options retain valid filters. */ }
                delete root.frappe.route_options.cn_filters;
            }
            this.grouping = params.get("cn_group") === "trend" ? "trend" : "company";
        }
        remember() {
            if (!this.isCurrentRoute()) return;
            try {
                const key = this.preferenceKey(), {from_date, to_date, approval_type, company, budget_tab, budget_type} = this.filters;
                if (key) root.localStorage?.setItem(key, JSON.stringify({from_date, to_date, approval_type, company, budget_tab, budget_type}));
            } catch { /* Disabled storage must not block navigation. */ }
            if (root.history?.replaceState && root.location?.href) {
                const url = new URL(root.location.href);
                url.searchParams.set("cn_filters", JSON.stringify(this.filters));
                if (this.view === "analysis") url.searchParams.set("cn_group", this.grouping);
                root.history.replaceState(root.history.state, "", url.pathname + url.search + url.hash);
            }
        }
        show() {
            if (this.initializing) return;
            if (this.initialized) { this.restore(); this.refresh(); }
            else this.ready = this.initialize();
        }
        dispose() {
            this.budgetReport?.dispose();
            this.budgetReport = null;
            this.initializationId = (this.initializationId || 0) + 1;
            this.initializing = false;
            this.generation++;
            this.disposeFilterControls();
            clearTimeout(this.sidebarTimer);
            if (this.sidebarHandler) root.$(root.document).off("sidebar_setup.cn-expenses", this.sidebarHandler);
            this.sidebarHandler = null;
            this.approvalSidebar?.remove();
            this.approvalSidebar = null;
        }
        mountSidebar() {
            if (!this.isCurrentRoute()) return;
            if (!this.sidebarHandler) {
                this.sidebarHandler = () => {
                    clearTimeout(this.sidebarTimer);
                    this.sidebarTimer = setTimeout(() => this.mountSidebar(), 0);
                };
                root.$(root.document).on("sidebar_setup.cn-expenses", this.sidebarHandler);
            }
            const top = root.document.querySelector(".body-sidebar-top");
            if (!top) return;
            if (!this.approvalSidebar?.isConnected) {
                top.querySelector(".mx-approval-sidebar")?.remove();
                this.approvalSidebar = root.document.createElement("div");
                this.approvalSidebar.className = "mx-approval-sidebar cn-approval-sidebar";
                this.approvalSidebar.setAttribute("role", "group");
                this.approvalSidebar.setAttribute("aria-label", t("Approval Form"));
                top.prepend(this.approvalSidebar);
                this.approvalSidebar.addEventListener("click", (event) => {
                    const choice = event.target.closest("[data-cn-form]");
                    if (!choice || !this.isCurrentRoute()) return;
                    if (this.filters.approval_type === choice.dataset.cnForm) return;
                    this.filters.approval_type = choice.dataset.cnForm;
                    this.filters.status = "all";
                    this.filters.company = "";
                    this.filters.keyword = "";
                    this.filters.department_id = "";
                    if (this.view === "budget_analysis" && this.filters.approval_type !== "budget") {
                        return root.frappe.set_route(routes.overview, {cn_filters:JSON.stringify(this.filters)});
                    }
                    this.refresh();
                    this.approvalSidebar.querySelector('[aria-pressed="true"]')?.focus();
                });
            }
            this.approvalSidebar.innerHTML = `<div class="mx-approval-heading">${esc(t("Approval Form"))}</div>` +
                forms.map(([id, label]) => `<button type="button" class="mx-approval-item" data-cn-form="${id}" aria-pressed="${this.filters.approval_type === id}"><span>${esc(t(label))}</span><span class="mx-approval-check" aria-hidden="true">✓</span></button>`).join("");
        }
        mountDateControls() {
            this.rangePicker = new root.DeepLinkERPMexicoExpenseDateRange.RangePicker({
                parent:this.el.querySelector(".mx-date-range-control"), value:this.filters, today:today(), getToday:async () => today(),
                onChange:(range) => {
                    if (!this.isCurrentRoute()) return;
                    Object.assign(this.filters, range);
                    this.refresh();
                    this.el.querySelector(".mx-range-trigger")?.focus();
                },
            });
        }
        async exportCurrent() {
            if (this.filters.approval_type === "budget" && root.CNBudget && this.data && !this.loading) {
                this.exportButton.prop("disabled", true);
                const generation = this.generation;
                try { await root.CNBudget.exportCurrent(this); }
                catch (error) { root.frappe.msgprint(t("Export failed. Please retry.")); }
                finally { if (generation === this.generation) this.exportButton.prop("disabled", !this.data || this.loading); }
                return;
            }
            if (!operating?.connected(this.filters, this.view) || !this.data || this.loading) return;
            this.exportButton.prop("disabled", true);
            try { await operating.exportCurrent(this); }
            catch (error) { root.frappe.msgprint(t("Export failed. Please retry.")); }
            finally { this.exportButton.prop("disabled", !this.data || this.loading || !operating.connected(this.filters, this.view)); }
        }
        async refresh(resetPage = true) {
            if (!this.isCurrentRoute()) return;
            const generation = ++this.generation;
            if (resetPage) this.start = 0;
            this.data = null;
            this.budgetReport?.dispose(); this.budgetReport = null;
            if (this.view === "budget_analysis") this.filters.approval_type = "budget";
            const isBudget = this.filters.approval_type === "budget";
            this.syncButton?.text(t(isBudget ? "Synchronize Budget Ledger" : "Synchronize Data"))
                .prop("disabled", !["", "operating", "budget"].includes(this.filters.approval_type));
            this.syncSettingsItem?.text(t(isBudget ? "Budget Sync Settings" : "Sync Settings"));
            if (this.filters.approval_type === "budget" && root.CNBudget) {
                if (["analysis", "payroll"].includes(this.view)) {
                    return root.frappe.set_route(this.view === "analysis" ? routes.budget_analysis : routes.overview, {cn_filters:JSON.stringify(this.filters)});
                }
                return root.CNBudget.render(this, generation);
            }
            this.page.set_title(t(titles[this.view]));
            this.filters = {...defaults(), ...validFilters(this.filters, this.companies)};
            this.disposeFilterControls();
            const f = this.filters;
            const live = operating?.connected(f, this.view);
            if (live && !["request", "cashier"].includes(f.basis)) f.basis = "request";
            if (!live && f.basis === "cashier") f.basis = "request";
            this.loading = !!live;
            this.exportButton?.prop("disabled", true).attr("title", live ? "" : t(pending));
            this.el.innerHTML = `<div class="mx-filters">${this.filter("basis", "Date Basis", (live ? bases.filter(([id]) => ["request", "cashier"].includes(id)) : bases.filter(([id]) => id !== "cashier")).map(([id, label]) => [id, t(label)]))}
                <div class="mx-filter mx-date-range-filter"><span>${esc(t("Date Range"))}</span><div class="mx-date-range-control"></div></div>
                ${this.filter("company", "Subsidiary", [["", t("All Subsidiaries")], ...this.companies.map((c) => [c, c])])}
                ${live ? "" : this.filter("currency", "Reporting Currency", ["CNY", "USD", "MXN"].map((c) => [c, c]))}
                ${this.filter("status", "Approval Status", statuses.map(([id, label]) => [id, t(label)]))}
                <div class="mx-filter-actions">${button("Reset Filters", "reset", "mx-reset")}</div></div>
                <div class="mx-scope"><span><i></i>${esc(f.from_date)} ~ ${esc(f.to_date)} · ${esc(t(bases.find(([id]) => id === f.basis)[1]))} · ${esc(t(forms.find(([id]) => id === f.approval_type)[1]))} · ${esc(f.company || t("All Subsidiaries"))} · ${esc(t(live ? "Original Currencies" : f.currency))}</span>${help(live ? operating.scopeFor(f.basis) : "CN filters are ready; statistics and currency conversion will be connected later")}</div>
                ${!live || !f.approval_type ? `<div class="mx-notice">${esc(t(live ? "This total contains operating expenses only. Select Budget to view plans and quotas separately; purchase statistics are not connected yet." : pending))}</div>` : ""}
                <div class="mx-body">${live ? `<div class="mx-empty">${esc(t("Loading..."))}</div>` : this.content()}</div>`;
            this.mountDateControls();
            this.mountSelectControls();
            root.DeepLinkERPMexicoExpenseInsights?.mount(this.el);
            this.mountSidebar();
            this.remember();
            root.frappe.breadcrumbs.add({type:"Custom", label:t(titles[this.view])});
            if (!live) return;
            try {
                const data = await operating.load(this);
                if (generation !== this.generation || !this.isCurrentRoute()) return;
                // A reconciliation can remove the last row on the current page.
                if (["details", "review"].includes(this.view) && this.start && this.start >= data.total_count) {
                    this.start = Math.max(0, Math.floor((data.total_count - 1) / 20) * 20);
                    return this.refresh(false);
                }
                this.data = data;
                this.el.querySelector(".mx-body").innerHTML = ["details", "review"].includes(this.view)
                    ? operating.detail(data, this) : operating.dashboard(data, this.view, this.grouping);
                shared.filterControls.mountSelectControls.call({el:this.el.querySelector(".mx-body")});
                root.DeepLinkERPMexicoExpenseInsights?.mount(this.el);
                this.exportButton.prop("disabled", false);
            } catch (error) {
                if (generation !== this.generation || !this.isCurrentRoute()) return;
                this.el.querySelector(".mx-body").innerHTML = `<div class="mx-empty">${esc(t("Operating expenses could not be loaded"))}${button("Retry", "retry")}</div>`;
            } finally {
                if (generation === this.generation) this.loading = false;
            }
        }
        cards(labels) {
            return `<div class="mx-cards">${labels.map((label, i) => `<div class="mx-card ${i === 0 ? "first" : ""}"><span>${esc(t(label))}</span><div class="mx-card-value">—</div><small>${esc(t("Awaiting data connection"))}</small></div>`).join("")}</div>`;
        }
        panel(label, body, helpText) {
            return `<section class="mx-panel"><div class="mx-panel-head"><h3>${esc(t(label))} ${helpText ? help(helpText) : ""}</h3></div>${body}</section>`;
        }
        empty() { return `<div class="mx-empty">${esc(t("Awaiting data connection"))}</div>`; }
        table(label, columns) {
            return this.panel(label, `<div class="mx-scroll"><table><thead><tr>${columns.map((c) => `<th>${esc(t(c))}</th>`).join("")}</tr></thead><tbody><tr><td colspan="${columns.length}">${this.empty()}</td></tr></tbody></table></div>`);
        }
        content() {
            if (this.view === "details" || this.view === "review") {
                return this.table(this.view === "review" ? "Data Review" : "Expense Application Register",
                    ["Approval Number", "Approval Form", "Subsidiary", "Expense Category", "Expense Period", "Expense Description", "Converted Amount", "Approval Status"]);
            }
            if (["analysis", "payroll"].includes(this.view)) {
                const salary = this.view === "payroll";
                return this.cards(["Confirmed Expenditure", "Subsidiaries Included", "Applications Requiring Review", "Known Amount Requiring Review"]) +
                    this.panel("Period Comparison", this.cards(["Comparison Period", "Previous Period", "Change Amount", "Change Rate"])) +
                    `<div class="mx-insight-columns">${this.panel("Subsidiary Ranking", this.empty())}${this.panel("Monthly Expenditure", this.empty())}</div>` +
                    (salary ? this.table("Personnel Analysis", ["Subsidiary", "Reporting Month", "Salary", "Social Insurance", "Bonus", "Total"]) :
                    `<div class="mx-analysis-group">${["company", "trend"].map((group) => `<button type="button" class="mx-button" data-cn-action="${group}" aria-pressed="${this.grouping === group}">${esc(t(group === "company" ? "By Subsidiary" : "By Month"))}</button>`).join("")}</div>` +
                    this.table(this.grouping === "trend" ? "Monthly Expenditure" : "Expenditure Analysis", [this.grouping === "trend" ? "Reporting Month" : "Subsidiary", "Operating Expenses", "Budget", "Purchase Expenses", "Total"]));
            }
            return this.cards([this.filters.approval_type === "budget" ? "Budget Amount" : "Application Amount", "Approved Application Amount", "Applications in Approval", "Applications Requiring Review"]) +
                `<div class="mx-chart-grid">${this.panel("Expense Trend", this.empty())}${this.panel("Expense Composition", this.empty())}</div>` +
                this.table("Subsidiary Expense Distribution", ["Subsidiary", "Applications", "Operating Expenses", "Budget", "Purchase Expenses", "Total"]);
        }
    }
    if (shared) Object.assign(Workbench.prototype, shared.filterControls);
    return {Workbench, routes, titles, forms, defaults, validFilters, today, viewFor, installBreadcrumbs};
});
