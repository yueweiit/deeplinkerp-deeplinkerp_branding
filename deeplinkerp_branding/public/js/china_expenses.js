(function (root, factory) {
    if (typeof module === "object" && module.exports) module.exports = factory;
    root.DeepLinkERPChinaExpenses = factory(root);
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
    "use strict";
    const shared = root.DeepLinkERPMexicoExpenses;
    const t = (text) => root.__ ? root.__(text) : text;
    const esc = shared?.esc || ((value) => String(value ?? "").replace(/[&<>"']/g,
        (c) => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c])));
    const routes = {
        overview: "china-expenses", details: "china-expense-details",
        analysis: "china-expense-reports", payroll: "china-personnel-report", review: "china-expense-review",
    };
    const titles = {overview:"Expense Overview", details:"Expense Details", analysis:"Expenditure Analysis",
        payroll:"Personnel Analysis", review:"Data Review"};
    const forms = [["", "All Approval Forms"], ["operating", "Operating Expenses"], ["budget", "Budget"], ["purchase", "Purchase Expenses"]];
    const bases = [["period", "Expense Period"], ["request", "Application Date"], ["planned", "Planned Payment Date"]];
    const statuses = [["active", "Active Applications"], ["approved", "Approved"], ["pending", "In Approval"],
        ["all", "All Statuses"], ["rejected", "Rejected"], ["terminated", "Terminated"]];
    const pending = "CN statistics are not connected yet";
    const viewFor = (route) => Object.keys(routes).find((view) => routes[view] === route?.[0]);
    function today() {
        const parts = new Intl.DateTimeFormat("en", {timeZone:"Asia/Shanghai", year:"numeric", month:"2-digit", day:"2-digit"})
            .formatToParts(new Date());
        const values = Object.fromEntries(parts.map(({type, value}) => [type, value]));
        return `${values.year}-${values.month}-${values.day}`;
    }
    function defaults(day = today()) {
        return {...shared.monthRange(day.slice(0, 7)), basis:"period", company:"", approval_type:"", currency:"CNY", status:"active"};
    }
    function validFilters(values, companies) {
        if (!values || typeof values !== "object" || Array.isArray(values)) return {};
        const clean = {};
        if (shared.validDateRange(values)) Object.assign(clean, {from_date:values.from_date, to_date:values.to_date});
        for (const [key, allowed] of Object.entries({basis:bases.map(([id]) => id), company:["", ...companies],
            approval_type:forms.map(([id]) => id), currency:["CNY", "USD", "MXN"], status:statuses.map(([id]) => id)})) {
            if (allowed.includes(values[key])) clean[key] = values[key];
        }
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
            if (["analysis", "payroll"].includes(view)) {
                this.append_breadcrumb_element("/desk/china-expense-reports", esc(t("Expense Reports")));
            }
            const item = root.document.createElement("li"), current = root.document.createElement("span");
            current.className = "mx-breadcrumb-current";
            current.setAttribute("aria-current", "page");
            current.textContent = t(titles[view]);
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
            this.page = root.frappe.ui.make_app_page({parent:wrapper, title:t(titles[this.view]), single_column:true});
            this.el = root.document.createElement("div");
            this.el.className = "mx-expenses cn-expenses";
            this.page.main.append(this.el);
            installBreadcrumbs();
            this.el.addEventListener("change", (event) => {
                if (!Object.hasOwn(this.filters, event.target.dataset.filter)) return;
                Object.assign(this.filters, validFilters({[event.target.dataset.filter]:event.target.value}, this.companies));
                this.refresh();
            });
            this.el.addEventListener("click", (event) => {
                const action = event.target.closest("[data-cn-action]")?.dataset.cnAction;
                if (action === "reset") this.filters = defaults();
                else if (["company", "trend"].includes(action)) this.grouping = action;
                else if (action === "retry") return this.initialize().catch(() => this.loadError());
                else if (!action) return;
                this.refresh();
            });
            root.$(wrapper).on("hide.cn-expenses", () => this.dispose());
            this.page.add_inner_button(t("Refresh"), () => this.initialize().catch(() => this.loadError()));
            this.page.add_inner_button(t("Export Excel"), () => {}).prop("disabled", true).attr("title", t(pending));
            this.page.add_menu_item(t("Open Operating Workbench"), () => root.frappe.set_route("operating-expenses"));
            this.ready = this.initialize().catch(() => this.loadError());
        }
        async loadCompanies() {
            // Native permission-filtered organization data only. No expense or budget statistics are queried.
            const response = await root.frappe.call({method:"frappe.client.get_list", args:{
                doctype:"Company", fields:["name"], filters:{country:"China", is_group:0}, limit_page_length:0, order_by:"name asc",
            }});
            this.companies = response.message.map((row) => row.name);
        }
        async initialize() {
            await this.loadCompanies();
            this.restore();
            this.initialized = true;
            this.refresh();
        }
        loadError() {
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
                if (saved) Object.assign(this.filters, validFilters(saved, this.companies));
            } catch { /* Optional browser preferences. */ }
            const params = new URLSearchParams(root.location?.search || "");
            try { Object.assign(this.filters, validFilters(JSON.parse(params.get("cn_filters")), this.companies)); }
            catch { /* Invalid links retain valid defaults. */ }
            this.grouping = params.get("cn_group") === "trend" ? "trend" : "company";
        }
        remember() {
            if (!this.isCurrentRoute()) return;
            try {
                const key = this.preferenceKey(), {from_date, to_date, approval_type} = this.filters;
                if (key) root.localStorage?.setItem(key, JSON.stringify({from_date, to_date, approval_type}));
            } catch { /* Disabled storage must not block navigation. */ }
            if (root.history?.replaceState && root.location?.href) {
                const url = new URL(root.location.href);
                url.searchParams.set("cn_filters", JSON.stringify(this.filters));
                if (this.view === "analysis") url.searchParams.set("cn_group", this.grouping);
                root.history.replaceState(root.history.state, "", url.pathname + url.search + url.hash);
            }
        }
        show() { if (this.initialized) { this.restore(); this.refresh(); } }
        dispose() {
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
                    this.filters.approval_type = choice.dataset.cnForm;
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
        refresh() {
            if (!this.isCurrentRoute()) return;
            this.filters = {...defaults(), ...validFilters(this.filters, this.companies)};
            this.disposeFilterControls();
            const f = this.filters;
            this.el.innerHTML = `<div class="mx-filters">${this.filter("basis", "Date Basis", bases.map(([id, label]) => [id, t(label)]))}
                <div class="mx-filter mx-date-range-filter"><span>${esc(t("Date Range"))}</span><div class="mx-date-range-control"></div></div>
                ${this.filter("company", "Subsidiary", [["", t("All Subsidiaries")], ...this.companies.map((c) => [c, c])])}
                ${this.filter("currency", "Reporting Currency", ["CNY", "USD", "MXN"].map((c) => [c, c]))}
                ${this.filter("status", "Approval Status", statuses.map(([id, label]) => [id, t(label)]))}
                <div class="mx-filter-actions">${button("Reset Filters", "reset", "mx-reset")}</div></div>
                <div class="mx-scope"><span><i></i>${esc(f.from_date)} ~ ${esc(f.to_date)} · ${esc(t(forms.find(([id]) => id === f.approval_type)[1]))} · ${esc(f.company || t("All Subsidiaries"))} · ${esc(f.currency)}</span>${help("CN filters are ready; statistics and currency conversion will be connected later")}</div>
                <div class="mx-notice">${esc(t(pending))}</div><div class="mx-body">${this.content()}</div>`;
            this.mountDateControls();
            this.mountSelectControls();
            root.DeepLinkERPMexicoExpenseInsights?.mount(this.el);
            this.mountSidebar();
            this.remember();
            root.frappe.breadcrumbs.add({type:"Custom", label:t(titles[this.view])});
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
