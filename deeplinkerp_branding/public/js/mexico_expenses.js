(function (root, factory) {
	if (typeof module === "object" && module.exports) module.exports = factory;
	root.DeepLinkERPMexicoExpenses = factory(root);
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
	"use strict";
	const API = "deeplinkerp_branding.services.mexico_expenses.";
	const t = (text, args = []) =>
		root.__ ? root.__(text, args) : text.replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");
	const esc = (value) =>
		String(value ?? "").replace(
			/[&<>"']/g,
			(c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
		);
	const views = {
		overview: "Expense Overview",
		details: "Expense Details",
		payroll: "Personnel Expenses",
		reports: "Expense Reports",
		review: "Data Review",
	};
	const routes = {
		overview: "mexico-expenses",
		details: "mexico-expense-details",
		payroll: "mexico-personnel-expenses",
		reports: "mexico-expense-reports",
		review: "mexico-expense-review",
	};
	const reportRoutes = {
		company: "mexico-expense-reports",
		payroll: "mexico-personnel-report",
		trend: "mexico-expense-trend",
	};
	function reportFromRoute(route) {
		return Object.keys(reportRoutes).find((kind) => reportRoutes[kind] === route?.[0]) || null;
	}
	function viewFromRoute(route) {
		if (reportFromRoute(route)) return "reports";
		return Object.keys(routes).find((view) => routes[view] === route?.[0]) || null;
	}
	function installBreadcrumbs() {
		const breadcrumbs = root.frappe.breadcrumbs;
		if (!breadcrumbs || breadcrumbs.mx_expenses_installed) return;
		const original = breadcrumbs.set_custom_breadcrumbs;
		breadcrumbs.set_custom_breadcrumbs = function (options) {
			const view = viewFromRoute(root.frappe.get_route());
			if (!view) return original.call(this, options);
			// Native clear() already renders the clickable Home icon.
			this.append_breadcrumb_element("/desk/" + routes.overview, esc(t("Mexico Expenses")));
			const report = reportFromRoute(root.frappe.get_route());
			if (report) this.append_breadcrumb_element("/desk/" + routes.reports, esc(t(views.reports)));
			const item = root.document.createElement("li");
			const current = root.document.createElement("span");
			current.className = "mx-breadcrumb-current";
			current.setAttribute("aria-current", "page");
			current.textContent = t(report ? reportNames[report] : views[view]);
			item.appendChild(current);
			this.$breadcrumbs.append(item);
		};
		breadcrumbs.mx_expenses_installed = true;
	}
	const statuses = {
		finance_approved: "Finance Approved",
		active: "Active Applications",
		approved: "Approved",
		pending: "In Approval",
		rejected: "Rejected",
		terminated: "Terminated",
		withdrawn: "Withdrawn",
		unknown: "Unknown",
		all: "All Statuses",
	};
	const reportNames = {
		company: "Mexico Subsidiary Expenses",
		payroll: "Mexico Personnel Expenses",
		trend: "Mexico Expense Trend",
	};
	const nodeMoneyFormatter = typeof module === "object" && module.exports
		? require("./mexico_expense_money.js")(root) : null;
	// frappe.require may fetch these scripts concurrently; resolve the browser
	// formatter when rendering, after the page loader has awaited all assets.
	const money = (value, currency) =>
		(nodeMoneyFormatter || root.DeepLinkERPMexicoExpenseMoney).format(value, currency);
	const number = (value) =>
		root.frappe.format(value || 0, { fieldtype: "Int" }, { inline: true, only_value: true });
	const date = (value) =>
		value ? root.frappe.datetime.str_to_user(String(value).slice(0, 10)) : t("Not Confirmed");
	function sourceTime(value) {
		if (!value) return "—";
		if (root.moment && root.frappe.boot?.time_zone?.system) {
			const parsed = root.moment.parseZone(value);
			if (parsed.isValid())
				return root.frappe.datetime.str_to_user(
					parsed
						.tz(root.frappe.boot.time_zone.system)
						.format(root.frappe.defaultDatetimeFormat)
				);
		}
		return String(value);
	}
	const pill = (status) =>
		`<span class="mx-pill mx-${esc(status)}">${esc(t(statuses[status] || "Unknown"))}</span>`;
	const button = (text, action, cls = "mx-button", extra = "") =>
		`<button type="button" class="${cls}" data-action="${action}" ${extra}>${esc(
			t(text)
		)}</button>`;
	const empty = () =>
		`<div class="mx-empty"><span>⌕</span><strong>${esc(
			t("No expenses match the current filters")
		)}</strong><p>${esc(
			t("Adjust the filters or check Data Review for incomplete records")
		)}</p></div>`;
	const call = async (method, args = {}) =>
		(await root.frappe.call({ method: API + method, args })).message;
	// Decimal strings are summed in minor units; no repeated binary float sums.
	function cents(value) {
		const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(String(value ?? "0"));
		if (!match) return 0n;
		return (
			(BigInt(match[2]) * 100n + BigInt((match[3] || "").padEnd(2, "0").slice(0, 2))) *
			(match[1] ? -1n : 1n)
		);
	}
	function decimal(value) {
		const sign = value < 0n ? "-" : "";
		const abs = value < 0n ? -value : value;
		return sign + (abs / 100n).toString() + "." + (abs % 100n).toString().padStart(2, "0");
	}
	function totals(rows, key) {
		const out = {};
		for (const row of rows || []) {
			const id = key(row);
			out[id] = (out[id] || 0n) + cents(row.amount);
		}
		return Object.fromEntries(
			Object.entries(out).map(([id, amount]) => [id, decimal(amount)])
		);
	}
	function monthsEnding(month) {
		const result = [],
			[year, mm] = month.split("-").map(Number);
		for (let i = 5; i >= 0; i--) {
			const d = new Date(Date.UTC(year, mm - 1 - i, 15));
			result.push(d.toISOString().slice(0, 7));
		}
		return result;
	}
	function monthRange(month) {
		const [year, mm] = month.split("-").map(Number);
		return { from_date: month + "-01", to_date: new Date(Date.UTC(year, mm, 0)).toISOString().slice(0, 10) };
	}
	function monthsInRange(from, to) {
		const result = [];
		if (!from || !to || from > to) return result;
		let [year, month] = from.slice(0, 7).split("-").map(Number);
		while (`${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}` <= to.slice(0, 7)) {
			result.push(`${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}`);
			if (++month > 12) { year++; month = 1; }
		}
		return result;
	}
	function validDateRange(filters) {
		const valid = (value) => {
			if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) return false;
			const parsed = new Date(value + "T00:00:00Z");
			return value >= "1900-01-01" && value <= "2200-12-31" &&
				!Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
		};
		return valid(filters.from_date) && valid(filters.to_date) && filters.from_date <= filters.to_date;
	}
	class Workbench {
		constructor(wrapper) {
			this.wrapper = wrapper;
			this.view = viewFromRoute([wrapper.page_name]) || "overview";
			this.report = reportFromRoute([wrapper.page_name]) || "company";
			installBreadcrumbs();
			this.page = root.frappe.ui.make_app_page({
				parent: wrapper,
				title: t(this.view === "reports" ? reportNames[this.report] : views[this.view]),
				single_column: true,
			});
			this.el = root.document.createElement("div");
			this.el.className = "mx-expenses";
			this.page.main.append(this.el);
			root.$(wrapper).on("hide.mx-expenses", () => {
				this.disposeFilterControls();
				this.disposeApprovalSidebar();
			});
			this.start = 0;
			this.epoch = 0;
			this.filters = {
				from_date: "",
				to_date: "",
				basis: "period",
				company: "",
				approval_type: "",
				category: "",
				subcategory: "",
				currency: "MXN",
				status: ["payroll", "reports"].includes(this.view) ? "finance_approved" : "active",
				keyword: "",
				issue: "",
			};
			this.el.addEventListener("click", (e) => this.click(e));
			this.el.addEventListener("change", (e) => {
				if (!e.target.dataset.filter) return;
				this.filters[e.target.dataset.filter] = e.target.value;
				this.filters.subcategory = "";
				this.start = 0;
				this.refresh();
			});
			this.el.addEventListener("keydown", (e) => {
				const row = e.target.closest("[data-row],[data-company],[data-month]");
				if (row && ["Enter", " "].includes(e.key) && e.target === row) {
					e.preventDefault();
					row.click();
				}
			});
			this.el.addEventListener("input", (e) => {
				if (e.target.id !== "mx-search") return;
				this.filters.keyword = e.target.value;
				clearTimeout(this.searchTimer);
				this.searchTimer = setTimeout(() => {
					this.start = 0;
					this.refresh(true);
				}, 350);
			});
			this.ready = this.initialize().catch(() => {
				this.el.innerHTML = `<div class="mx-empty"><strong>${esc(
					t("Mexico expenses could not be loaded")
				)}</strong>${button("Retry", "retry")}</div>`;
			});
		}
		async initialize() {
			this.el.innerHTML = `<div class="mx-empty">${esc(t("Loading Mexico expenses"))}</div>`;
			this.context = await call("get_context");
			Object.assign(this.filters, monthRange(this.context.today.slice(0, 7)));
			this.restorePreferences();
			this.page.add_inner_button(t("Refresh"), () => this.reload());
			this.page.add_inner_button(t("Export Excel"), () => this.export());
			this.page.add_menu_item(t("Calculation Basis"), () => this.showCriteria());
			if (this.context.can_manage) {
				this.page.add_menu_item(t("Synchronization Settings"), () => this.settings());
				this.page.add_menu_item(t("Synchronize Now"), () => this.synchronize());
			}
			this.applyRouteFilters();
			this.setBreadcrumbs();
			await this.refresh();
		}
		lineAmount(row) {
			if (!row.reporting_currency) return money(row.amount, row.currency);
			return `${money(row.converted_amount, row.reporting_currency)}<small>${esc(t("Original Amount"))}: ${money(row.amount, row.currency)} · ${esc(row.currency || "—")}</small><small>${esc(t("Exchange Rate"))}: ${esc(row.exchange_rate || "—")} · ${date(row.exchange_rate_date)}${row.fx_estimated ? " · " + esc(t("Estimated Conversion")) : ""}</small>`;
		}
		form(id) {
			return this.context.approval_forms.find((f) => f.id === id)?.label || t("Unclassified");
		}
		preferenceKey() {
			// localStorage is origin-scoped; additionally isolate users sharing a browser.
			const user = root.frappe.session?.user;
			return user && user !== "Guest" ? `mx-expenses:preferences:v1:${user}` : null;
		}
		restorePreferences() {
			try {
				const key = this.preferenceKey();
				const saved = key && JSON.parse(root.localStorage?.getItem(key) || "null");
				if (!saved || typeof saved !== "object") return;
				if (validDateRange(saved)) Object.assign(this.filters, { from_date: saved.from_date, to_date: saved.to_date });
				if (saved.approval_type === "" || this.context.approval_forms.some((f) => f.id === saved.approval_type)) {
					if (saved.approval_type !== this.filters.approval_type)
						Object.assign(this.filters, { category: "", subcategory: "", keyword: "", issue: "" });
					this.filters.approval_type = saved.approval_type;
				}
			} catch (_) {
				// Disabled browser storage or stale preferences must not block queries.
			}
		}
		rememberFilters() {
			if (!this.isCurrentRoute() || !validDateRange(this.filters)) return;
			try {
				const key = this.preferenceKey();
				const { from_date, to_date, approval_type } = this.filters;
				if (key) root.localStorage?.setItem(key, JSON.stringify({ from_date, to_date, approval_type }));
			} catch (_) { /* Browser storage is optional. */ }
			// Replace the current entry so an old drilldown URL cannot undo a new selection on refresh.
			if (root.history?.replaceState && root.location?.href) {
				const url = new URL(root.location.href);
				url.searchParams.set("mx_filters", JSON.stringify(this.filters));
				root.history.replaceState(root.history.state, "", url.pathname + url.search + url.hash);
			}
		}
		disposeApprovalSidebar() {
			clearTimeout(this.sidebarTimer);
			if (this.sidebarSetupHandler) root.$(root.document).off("sidebar_setup.mx-expenses", this.sidebarSetupHandler);
			this.sidebarSetupHandler = null;
			this.approvalSidebar?.remove();
			this.approvalSidebar = null;
		}
		mountApprovalSidebar() {
			if (!this.isCurrentRoute()) return;
			if (!this.sidebarSetupHandler) {
				this.sidebarSetupHandler = () => {
					clearTimeout(this.sidebarTimer);
					// Native sidebar_setup fires before its synchronous DOM rebuild.
					this.sidebarTimer = setTimeout(() => this.mountApprovalSidebar(), 0);
				};
				root.$(root.document).on("sidebar_setup.mx-expenses", this.sidebarSetupHandler);
			}
			// Keep outside the native item node, which the optional DL navigation reparents.
			const top = root.document.querySelector(".body-sidebar-top");
			if (!top) return;
			if (!this.approvalSidebar?.isConnected) {
				top.querySelector(".mx-approval-sidebar")?.remove();
				const group = root.document.createElement("div");
				group.className = "mx-approval-sidebar";
				group.setAttribute("role", "group");
				group.setAttribute("aria-label", t("Approval Form"));
				top.prepend(group);
				this.approvalSidebar = group;
				group.addEventListener("click", (event) => {
					const item = event.target.closest("[data-approval-type]");
					if (!item || !this.isCurrentRoute()) return;
					Object.assign(this.filters, { approval_type: item.dataset.approvalType, category: "", subcategory: "", issue: "", keyword: "" });
					this.start = 0;
					this.refresh();
					this.approvalSidebar?.querySelector('[aria-pressed="true"]')?.focus();
				});
			}
			this.approvalSidebar.innerHTML = `<div class="mx-approval-heading">${esc(t("Approval Form"))}</div>${[
				{ id: "", label: t("All Approval Forms") }, ...this.context.approval_forms,
			].map((form) => `<button type="button" class="mx-approval-item" data-approval-type="${esc(form.id)}" aria-pressed="${this.filters.approval_type === form.id}"><span>${esc(form.label)}</span><span class="mx-approval-check" aria-hidden="true">✓</span></button>`).join("")}`;
		}
		cat(id) {
			return this.context.categories.find((c) => c.id === id);
		}
		sub(id) {
			return this.context.subcategories.find((c) => c.id === id);
		}
		option(value, label, selected) {
			return `<option value="${esc(
				value
			)}" ${value === selected ? "selected" : ""}>${esc(label)}</option>`;
		}
		filter(id, label, options, disabled = false) {
			const selected = this.filters[id];
			const current = options.find(([value]) => value === selected);
			const key = `${this.wrapper?.page_name || routes[this.view]}-${id}`;
			return `<div class="mx-filter mx-select-filter"><span id="${key}-label">${esc(t(label))}</span>
				<select data-filter="${id}" hidden tabindex="-1" aria-hidden="true" ${disabled ? "disabled" : ""}>${options.map(([v, l]) => this.option(v, l, selected)).join("")}</select>
				<div class="dropdown mx-select">
					<button type="button" class="mx-select-toggle" data-toggle="dropdown" aria-haspopup="listbox" aria-expanded="false" aria-controls="${key}-menu" aria-labelledby="${key}-label ${key}-value" ${disabled ? "disabled" : ""}>
						<span id="${key}-value">${esc(current?.[1] || "")}</span><i class="mx-select-chevron" aria-hidden="true"></i>
					</button>
					<div class="dropdown-menu mx-select-menu" id="${key}-menu" role="listbox" aria-labelledby="${key}-label">
						${options.map(([value, text]) => `<button type="button" class="dropdown-item mx-select-option" role="option" aria-selected="${value === selected}" data-value="${esc(value)}" tabindex="-1"><span>${esc(text)}</span><i class="mx-select-check" aria-hidden="true">✓</i></button>`).join("")}
					</div>
				</div></div>`;
		}
		disposeFilterControls() {
			this.disposeDateControls();
			root.$(this.el).find(".mx-select-toggle").each(function () {
				// Dispose only mounted dropdowns; Bootstrap owns outside-click and keyboard events.
				if (root.$(this).data("bs.dropdown")) root.$(this).dropdown("dispose");
			});
		}
		mountSelectControls() {
			this.el.querySelectorAll(".mx-select-filter").forEach((holder) => {
				const select = holder.querySelector("select");
				const toggle = holder.querySelector(".mx-select-toggle");
				root.$(toggle).dropdown();
				root.$(holder.querySelector(".dropdown")).on("shown.bs.dropdown", () => {
					holder.querySelector('[aria-selected="true"]')?.focus();
				});
				holder.querySelectorAll(".mx-select-option").forEach((option) => {
					option.addEventListener("keydown", (event) => {
						if (event.key !== " ") return;
						event.preventDefault();
						event.stopPropagation();
						option.click();
					});
					option.addEventListener("click", () => {
						if (select.disabled) return;
						root.$(toggle).dropdown("hide");
						if (select.value !== option.dataset.value) {
							select.value = option.dataset.value;
							select.dispatchEvent(new root.Event("change", { bubbles: true }));
						}
						this.el.querySelector(`[data-filter="${select.dataset.filter}"]`)
							?.parentElement.querySelector(".mx-select-toggle").focus();
					});
				});
			});
		}
		disposeDateControls() {
			this.rangePicker?.destroy();
			this.rangePicker = null;
		}
		mountDateControls() {
			this.rangePicker = new root.DeepLinkERPMexicoExpenseDateRange.RangePicker({
				parent: this.el.querySelector(".mx-date-range-control"),
				value: validDateRange(this.filters)
					? { from_date: this.filters.from_date, to_date: this.filters.to_date }
					: monthRange(this.context.today.slice(0, 7)),
				today: this.context.today,
				getToday: async () => (await call("get_context")).today,
				onChange: (range) => {
					if (!this.isCurrentRoute()) return;
					Object.assign(this.filters, range);
					this.start = 0;
					this.refresh();
					this.el.querySelector(".mx-range-trigger")?.focus();
				},
			});
		}
		shell() {
			this.disposeFilterControls();
			const f = this.filters,
				payroll =
					this.view === "payroll" ||
					(this.view === "reports" && this.report === "payroll");
			if (payroll) Object.assign(f, { category: "personnel", basis: "period" });
			this.el.innerHTML = `<div class="mx-filters">${this.filter(
					"basis",
					"Date Basis",
					[
						["period", t("Expense Period")],
						["request", t("Application Date")],
						["planned", t("Planned Payment Date")],
					],
					payroll
				)}
                <div class="mx-filter mx-date-range-filter"><span>${esc(t("Date Range"))}</span><div class="mx-date-range-control"></div></div>
                ${this.filter("company", "Subsidiary", [
					["", t("All Subsidiaries")],
					...this.context.companies.map((c) => [c, c]),
				])}
                ${this.filter("currency", "Reporting Currency", [
					["MXN", "MXN"],
					["USD", "USD"],
					["CNY", "CNY"],
				])}
                ${this.filter(
					"status",
					"Approval Status",
					Object.entries(statuses).map(([v, l]) => [v, t(l)])
				)}
                <div class="mx-filter-actions">${button("Reset Filters", "reset", "mx-reset", `title="${esc(t("Restore this month and reset all filters"))}"`)}</div></div>
                <div class="mx-scope"><span><i></i>${esc(f.from_date)} ~ ${esc(f.to_date)} · ${esc(
				t(
					{
						period: "Expense Period",
						request: "Application Date",
						planned: "Planned Payment Date",
					}[f.basis]
				)
			)} · ${esc(f.approval_type ? this.form(f.approval_type) : t("All Approval Forms"))} · ${esc(t(statuses[f.status]))} · ${esc(f.currency)}</span><span>${esc(
				t("OA approvals do not prove actual payment")
			)}</span></div>
                ${this.context.approval_forms.find((form) => form.id === f.approval_type)?.connected === false
                    ? `<div class="mx-notice">${esc(t("This approval form is not connected yet; no data is available"))}</div>` : ""}
                ${f.category && !payroll
                    ? `<div class="mx-notice">${esc(t("Expense Category"))}: ${esc(this.cat(f.category)?.label || f.category)} ${button("Clear Expense Category", "clear-category", "mx-link")}</div>` : ""}
                ${
					f.subcategory
						? `<div class="mx-notice">${esc(this.sub(f.subcategory)?.label)} ${button(
								"Clear Subcategory",
								"clear-sub",
								"mx-link"
						  )}</div>`
						: ""
				}
                <div class="mx-body" aria-live="polite"><div class="mx-empty">${esc(
					t("Loading")
				)}…</div></div>
                <footer class="mx-footer"><span>${esc(
					t("Last Successful Sync")
				)}: <span class="mx-last-sync">—</span></span><span>${esc(
				t("Month-start rates by finance approval month; pending approvals use application month estimates")
			)}</span></footer>`;
			this.mountDateControls();
			this.mountSelectControls();
			this.setSyncTime();
		}
		setSyncTime() {
			const el = this.el.querySelector(".mx-last-sync");
			if (el)
				el.textContent = this.context.last_sync_at
					? root.frappe.datetime.str_to_user(String(this.context.last_sync_at))
					: t("Not Synchronized");
		}
		effectiveFilters() {
			const f = { ...this.filters };
			if (f.currency) f.reporting_currency = f.currency;
			if (this.view === "payroll" || (this.view === "reports" && this.report === "payroll"))
				Object.assign(f, { category: "personnel", basis: "period" });
			return f;
		}
		isCurrentRoute() {
			return root.frappe.get_route()?.[0] === (this.wrapper?.page_name || routes[this.view]);
		}
		setBreadcrumbs() {
			if (!this.isCurrentRoute()) return;
			root.frappe.breadcrumbs.add({ type: "Custom", label: t(views[this.view]) });
		}
		applyRouteFilters() {
			// Read this URL, not global route_options that another sidebar may consume.
			if (!this.isCurrentRoute()) return;
			const raw = new URLSearchParams(root.location?.search || "").get("mx_filters");
			if (!raw) return;
			try {
				const values = JSON.parse(raw);
				if (!values || Array.isArray(values) || typeof values !== "object") return;
				if (/^\d{4}-\d{2}$/.test(values.month || "") && !values.from_date && !values.to_date) Object.assign(values, monthRange(values.month));
				for (const key of Object.keys(this.filters)) {
					if (typeof values[key] === "string") this.filters[key] = values[key];
				}
				this.start = 0;
			} catch (_) {
				// Invalid navigation state leaves the normal defaults in place.
			}
		}
		show() {
			if (!this.context) return;
			this.restorePreferences();
			this.applyRouteFilters();
			this.setBreadcrumbs();
			return this.reload();
		}
		navigate(view, filters) {
			if (!routes[view]) return;
			clearTimeout(this.searchTimer);
			return root.frappe.set_route(routes[view], filters ? { mx_filters: { ...filters } } : {});
		}
		async reload() {
			if (!this.context) return;
			this.context = await call("get_context");
			return this.refresh();
		}
		async refresh(keepSearch = false) {
			if (!this.context) return;
			const epoch = ++this.epoch,
				filters = this.effectiveFilters();
			const searchFocus =
				keepSearch && this.el.querySelector("#mx-search") === root.document.activeElement;
			this.shell();
			this.mountApprovalSidebar();
			if (!validDateRange(filters)) {
				this.el.querySelector(".mx-body").innerHTML = `<div class="mx-notice mx-warning">${esc(t("Enter a valid start and end date; the start date must not be after the end date"))}</div>`;
				return;
			}
			this.rememberFilters();
			try {
				let html;
				if (this.view === "overview") {
					const data = await call("get_overview", { filters });
					if (epoch !== this.epoch) return;
					this.overviewData = data;
					html = this.overview(data);
				} else if (this.view === "details" || this.view === "review") {
					const data = await call("get_lines", {
						filters,
						start: this.start,
						page_length: 25,
						review: this.view === "review" ? 1 : 0,
					});
					if (epoch !== this.epoch) return;
					html = this.details(data, this.view === "review");
				} else {
					const data = await call("get_report", {
						filters,
						kind: this.view === "payroll" ? "payroll" : this.report,
					});
					if (epoch !== this.epoch) return;
					html = this.reportTable(data);
				}
				const sync = this.context.last_error
					? `<div class="mx-notice mx-warning">${esc(this.context.last_error)}</div>`
					: "";
				this.el.querySelector(".mx-body").innerHTML = sync + html;
				if (searchFocus) {
					const input = this.el.querySelector("#mx-search");
					input?.focus();
					input?.setSelectionRange(input.value.length, input.value.length);
				}
			} catch (error) {
				if (epoch !== this.epoch) return;
				this.el.querySelector(".mx-body").innerHTML = `<div class="mx-empty"><strong>${esc(
					t("Mexico expenses could not be loaded")
				)}</strong>${button("Retry", "retry")}</div>`;
			}
		}
		overview(data) {
			const f = this.filters,
				metrics = data.metrics || [];
			const incomplete = (status) =>
				metrics.some(
					(r) =>
						(!status || r.approval_status === status) &&
						Number(r.invalid_amounts || 0) > 0
				);
			const amount = (status) =>
				incomplete(status)
					? null
					: decimal(
							metrics
								.filter((r) => !status || r.approval_status === status)
								.reduce((sum, row) => sum + cents(row.amount), 0n)
					  );
			const count = (status) =>
				metrics
					.filter((r) => !status || r.approval_status === status)
					.reduce((s, r) => s + Number(r.applications || 0), 0);
			const cards = [
				["Application Amount", amount(), count(), ""],
				["Approved Application Amount", amount("approved"), count("approved"), "approved"],
				["Applications in Approval", amount("pending"), count("pending"), "pending"],
				["Applications to Review", null, data.review_count, "review"],
			];
			return `${metrics.some((r) => r.estimated_lines > 0) ? `<div class="mx-notice">${esc(t("Pending approvals are converted using application-month opening rates (estimated)"))}</div>` : ""}<div class="mx-cards">${cards
				.map(
					([title, value, n, kind], i) =>
						`<section class="mx-card ${i === 0 ? "first" : ""}"><span>${esc(
							t(title)
						)}</span><div class="mx-card-value">${
							kind === "review" ? number(n) : money(value, f.currency)
						}</div><small>${
							kind === "review"
								? button("Open Data Review", "review", "mx-link")
								: esc(t("{0} applications", [number(n)])) +
								  (value === null ? " · " + esc(t("Amount incomplete")) : "")
						}</small></section>`
				)
				.join("")}</div>
                ${
					data.review_count
						? `<div class="mx-notice mx-warning">${esc(
								t(
									"Expenses require MX Finance approval and valid subsidiary allocation before reporting"
								)
						  )} ${button("Open Data Review", "review", "mx-link")}</div>`
						: ""
				}
                <div class="mx-chart-grid"><section class="mx-panel"><div class="mx-panel-head"><div><h3>${esc(
					t("Expense Trend")
				)}</h3><p>${esc(t("Confirmed expenses within the selected date range by month"))}</p></div><span>${esc(f.currency)}</span></div>${this.trend(data.trend)}</section>
                <section class="mx-panel"><div class="mx-panel-head"><div><h3>${esc(
					t("Expense Composition")
				)}</h3><p>${esc(t("Select a category to view its details"))}</p></div><span>${esc(f.currency)}</span></div>${this.filters.approval_type ? this.donut(data.categories) : this.donut((data.forms || []).map((r) => ({...r, category: r.approval_type})), this.context.approval_forms.map((f, i) => ({...f, color: ["#547ee0", "#69b8ae", "#e2b67d"][i]})), true)}</section></div>${this.companyTable(data.company_forms || [], data.company_totals || [])}`;
		}
		trend(rows) {
			const months = monthsInRange(this.filters.from_date, this.filters.to_date),
				basis = {
					period: "period_month",
					request: "request_date",
					planned: "planned_payment_date",
				}[this.filters.basis];
			const grouped = totals(rows, (r) => String(r[basis]).slice(0, 7));
			const max = Math.max(...months.map((m) => Number(grouped[m] || 0)), 1);
			const lines = [0, 1, 2, 3]
				.map(
					(i) =>
						`<line x1="50" y1="${165 - i * 45}" x2="605" y2="${
							165 - i * 45
						}" stroke="var(--mx-line)" stroke-dasharray="3 5"/><text x="42" y="${
							168 - i * 45
						}" text-anchor="end">${esc(
							root.frappe.format(
								(max * i) / 3,
								{ fieldtype: "Float", precision: 0 },
								{ inline: true, only_value: true }
							)
						)}</text>`
				)
				.join("");
			return `<div class="mx-trend"><svg viewBox="0 0 630 205" aria-label="${esc(
				t("Expense Trend")
			)}" role="img">${lines}${months
				.map((m, i) => {
					const h = (Number(grouped[m] || 0) / max) * 135,
						x = 60 + i * (540 / Math.max(months.length, 1)),
						barWidth = Math.min(42, 360 / Math.max(months.length, 1));
					return `<g data-month="${m}" tabindex="0" role="button" aria-label="${m}"><rect x="${
						x - 8
					}" y="16" width="${barWidth + 16}" height="178" rx="5" fill="${
						m === this.filters.to_date?.slice(0, 7) ? "var(--mx-blue-light)" : "transparent"
					}"/><rect x="${x}" y="${
						165 - h
					}" width="${barWidth}" height="${h}" fill="#6387df" rx="4"/><text x="${
						x + barWidth / 2
					}" y="188" text-anchor="middle">${i % Math.max(1, Math.ceil(months.length / 8)) === 0 ? esc(m) : ""}</text><title>${m} · ${esc(
						grouped[m] || "0"
					)} ${esc(this.filters.currency)}</title></g>`;
				})
				.join("")}</svg></div>`;
		}
		donut(rows, categories = this.context.categories, byForm = false) {
			const byCategory = totals(rows, (r) => r.category),
				total = Object.values(byCategory).reduce((s, v) => s + cents(v), 0n);
			if (!total) return empty();
			// A single form uses only categories present in its filtered amounts.
			// Keep the three form choices for the all-forms comparison.
			if (!byForm) categories = categories.filter((c) => cents(byCategory[c.id]) > 0n);
			let offset = 0;
			const gradient = categories
				.map((c) => {
					const start = offset;
					offset += total ? (Number(cents(byCategory[c.id])) / Number(total)) * 100 : 0;
					return `${c.color} ${start}% ${offset}%`;
				})
				.join(",");
			return `<div class="mx-composition"><div class="mx-donut" style="background:${
				total ? `conic-gradient(${gradient})` : "var(--mx-line)"
			}"><div><b>${categories
				.filter((c) => cents(byCategory[c.id]) > 0n)
				.length.toString()
				.padStart(
					2,
					"0"
				)}</b><small>${esc(t("Categories"))}</small></div></div><div class="mx-legend">${categories.map((c) => `<button type="button" ${byForm ? "data-form" : "data-category"}="${c.id}"><span><i style="background:${c.color}"></i>${esc(c.label)}</span><strong>${total ? ((Number(cents(byCategory[c.id])) / Number(total)) * 100).toFixed(1) : "0.0"}%</strong></button>`).join("")}</div></div>`;
		}
		companyTable(rows, companyTotals) {
			const forms = this.context.approval_forms;
			const byCompany = totals(rows, (r) => r.company);
			return `<section class="mx-panel"><div class="mx-panel-head"><div><h3>${esc(t("Expenses by Subsidiary"))}</h3><p>${esc(t("Only confirmed expense lines are included"))}</p></div>${button("View Expense Details", "details", "mx-link")}</div>${rows.length ? `<div class="mx-scroll"><table><thead><tr><th>${esc(t("Subsidiary"))}</th><th>${esc(t("Applications"))}</th>${forms.map((f) => `<th class="num">${esc(f.label)}</th>`).join("")}<th class="num">${esc(t("Total"))} · ${esc(this.filters.currency)}</th></tr></thead><tbody>${Object.entries(byCompany).map(([company, total]) => {
				const amounts = totals(rows.filter((r) => r.company === company), (r) => r.approval_type);
				return `<tr><td><button class="mx-link" data-company="${esc(company)}">${esc(company)}</button></td><td>${number(companyTotals.find((r) => r.company === company)?.applications || 0)}</td>${forms.map((f) => `<td class="num"><button class="mx-link" data-company="${esc(company)}" data-form="${esc(f.id)}">${money(amounts[f.id] || "0", this.filters.currency)}</button></td>`).join("")}<td class="num mx-strong">${money(total, this.filters.currency)}</td></tr>`;
			}).join("")}</tbody></table></div>` : empty()}<div class="mx-panel-foot">${esc(t("Amounts are summed by expense line; application counts are distinct"))}</div></section>`;
		}
		details(data, review) {
			const f = this.filters;
			return `${
				review
					? `<div class="mx-notice mx-warning">${esc(
							t(
								"Expenses awaiting period assignment are listed by application date for review only"
							)
					  )}</div><div class="mx-issue-filters">${button(
							"All Issues",
							"all-issues",
							`mx-button ${!f.issue ? "active" : ""}`
					  )}${Object.entries(this.context.issue_labels)
							.map(
								([id, label]) =>
									`<button type="button" class="mx-button ${
										f.issue === id ? "active" : ""
									}" data-issue="${id}">${esc(label)}</button>`
							)
							.join("")}</div>`
					: ""
			}
                <section class="mx-panel"><div class="mx-panel-head"><div><h3>${esc(
					t(review ? "Data Review" : "Expense Application Register")
				)}</h3><p>${esc(t("{0} applications · {1} expense lines", [number(data.applications), number(data.total)]))}</p></div><input class="mx-search" type="search" id="mx-search" value="${esc(f.keyword)}" placeholder="${esc(t("Search approval number, entity or description"))}" aria-label="${esc(t("Search expenses"))}"></div>
                ${
					data.rows.length
						? `<div class="mx-scroll"><table><thead><tr>${[
								"Approval Number",
								"Approval Form",
								"Subsidiary",
								"Expense Category",
								"Expense Period",
								"Expense Description",
								"Converted Amount",
								"Approval Status",
								"Finance Status",
								review ? "Review Issues" : "Planned Payment Date",
						  ]
								.map(
									(h, i) =>
										`<th class="${i === 6 ? "num" : ""}">${esc(t(h))}${
											i === 6 ? " · " + esc(f.currency) : ""
										}</th>`
								)
								.join("")}<th></th></tr></thead><tbody>${data.rows
								.map(
									(r) =>
										`<tr tabindex="0" role="button" data-row="${esc(
											r.name
										)}"><td class="mx-source-number">${esc(
											r.approval_no || "—"
										)}</td><td>${esc(this.form(r.approval_type))}</td><td>${esc(
											r.company || t("Not Mapped")
										)}<small>${esc(r.source_entity)}</small></td><td>${esc(
											this.cat(r.category)?.label || t("Unclassified")
										)}<small>${esc(
											r.business_category || this.sub(r.subcategory)?.label || ""
										)}</small></td><td>${
											r.period_month
												? esc(String(r.period_month).slice(0, 7))
												: `<span class="mx-pill mx-warning">${esc(
														t("Not Confirmed")
												  )}</span>`
										}${
											r.period_start
												? `<small>${date(r.period_start)} – ${date(
														r.period_end
												  )}</small>`
												: ""
										}</td><td class="mx-description" title="${esc(
											r.description
										)}">${esc(
											r.description || "—"
										)}</td><td class="num mx-strong">${this.lineAmount(r)}</td><td>${pill(r.approval_status)}</td><td>${esc(t(r.finance_approved_date ? "Finance Approved" : "Awaiting MX Finance approval"))}<small>${date(r.finance_approved_date)}</small></td><td>${
											review
												? `<span class="mx-pill mx-warning">${esc(
														this.context.issue_labels[r.issues[0]] ||
															t("Requires Review")
												  )}</span>`
												: esc(date(r.planned_payment_date))
										}</td><td>›</td></tr>`
								)
								.join("")}</tbody></table></div>`
						: empty()
				}
                <div class="mx-panel-foot"><span>${number(
					data.total ? this.start + 1 : 0
				)}–${number(Math.min(this.start + 25, data.total))} / ${number(data.total)}</span><div>${button("Previous", "previous", "mx-button", this.start === 0 ? "disabled" : "")}${button("Next", "next", "mx-button", this.start + 25 >= data.total ? "disabled" : "")}</div></div></section>`;
		}
		reportTable(data) {
			const payroll = this.view === "payroll",
				kind = payroll ? "payroll" : this.report;
			const cards = payroll
				? `<div class="mx-payroll-cards">${this.context.subcategories
						.filter((c) => c.category === "personnel")
						.map((c) => {
							const total = data.rows.reduce((s, r) => s + cents(r[c.id]), 0n);
							return `<button type="button" class="mx-card" data-subcategory="${
								c.id
							}"><span>${esc(c.label)}</span><div class="mx-card-value">${money(
								decimal(total),
								this.filters.currency
							)}</div></button>`;
						})
						.join("")}</div>`
				: "";
			const numeric = (c) => c.fieldtype === "Currency";
			return (
				cards +
				`<div class="mx-notice">${esc(
					t("Reports include confirmed expense lines in the selected approval status")
				)} ${esc(
					t("OA approvals do not prove actual payment")
				)}</div><section class="mx-panel"><div class="mx-panel-head"><div><h3>${esc(
					t(reportNames[kind])
				)}</h3><p>${esc(
					t(
						kind === "payroll"
							? "Payroll is summarized by subsidiary, category and period; no employee details"
							: "Only confirmed expense lines are included"
					)
				)}</p></div>${button("Open Native Report", "native-report", "mx-link")}</div>${
					data.rows.length
						? `<div class="mx-scroll"><table><thead><tr>${data.columns
								.map(
									(c) =>
										`<th class="${numeric(c) ? "num" : ""}">${esc(
											c.label
										)}</th>`
								)
								.join("")}</tr></thead><tbody>${data.rows
								.map(
									(r) =>
										`<tr tabindex="0" role="button" ${
											kind === "trend"
												? `data-month="${esc(r.month)}"`
												: `data-company="${esc(r.company)}"`
										} ${
											kind === "payroll" ? `data-personnel="1" data-month="${esc(r.month)}"` : ""
										}>${data.columns
											.map(
												(c) =>
													`<td class="${numeric(c) ? "num" : ""} ${
														c.fieldname === "total" ? "mx-strong" : ""
													}">${
														numeric(c)
															? money(r[c.fieldname], r.currency)
															: esc(r[c.fieldname] || "—")
													}</td>`
											)
											.join("")}</tr>`
								)
								.join("")}</tbody><tfoot><tr>${data.columns
								.map(
									(c, i) =>
										`<td class="${numeric(c) ? "num" : ""}">${
											i === 0
												? esc(t("Total"))
												: numeric(c)
												? money(
														decimal(
															data.rows.reduce(
																(s, r) =>
																	s + cents(r[c.fieldname]),
																0n
															)
														),
														this.filters.currency
												  )
												: ""
										}</td>`
								)
								.join("")}</tr></tfoot></table></div>`
						: empty()
				}<div class="mx-panel-foot">${esc(
					t("Reporting months follow MX Finance approval in the business timezone")
				)}</div></section>`
			);
		}
		showCriteria() {
			return root.frappe.msgprint({
				title: t("Calculation Basis"),
				message: [
					"Expense entities are mapped explicitly to ERP subsidiaries",
					"Shared approvals are summed by line and counted once",
					"OA approvals do not prove actual payment",
					"Expenses require MX Finance approval and valid subsidiary allocation before reporting",
					"Reporting months follow MX Finance approval in the business timezone",
					"Month-start rates by finance approval month; pending approvals use application month estimates",
				]
					.map((s) => `<p>${esc(t(s))}</p>`)
					.join(""),
			});
		}
		async click(event) {
			const target = event.target.closest(
				"[data-action],[data-row],[data-company],[data-form],[data-category],[data-subcategory],[data-month],[data-issue]"
			);
			if (!target || target.disabled) return;
			const d = target.dataset;
			if (d.row) return this.detail(d.row);
			if (d.action) {
				if (d.action === "retry" && !this.context) return this.initialize();
				if (d.action === "criteria") return this.showCriteria();
				if (d.action === "reset") {
					const context = await call("get_context");
					if (!this.isCurrentRoute()) return;
					this.context = context;
					Object.assign(this.filters, {
						...monthRange(context.today.slice(0, 7)),
						approval_type: "",
						basis: "period",
						company: "",
						category: "",
						subcategory: "",
						currency: "MXN",
						status: ["payroll", "reports"].includes(this.view) ? "finance_approved" : "active",
						keyword: "",
						issue: "",
					});
					this.start = 0;
				}
				if (d.action === "review" || d.action === "details") {
					return this.navigate(d.action, {
						...this.filters, keyword: "", issue: "",
						status: d.action === "review" ? "active" : this.filters.status,
					});
				}
				if (d.action === "clear-category") Object.assign(this.filters, { category: "", subcategory: "" });
				if (d.action === "clear-sub") this.filters.subcategory = "";
				if (d.action === "previous") this.start = Math.max(0, this.start - 25);
				if (d.action === "next") this.start += 25;
				if (d.action === "all-issues") {
					this.filters.issue = "";
					this.start = 0;
				}
				if (d.action === "native-report") {
					root.frappe.route_options = this.effectiveFilters();
					return root.frappe.set_route(
						"query-report",
						reportNames[this.view === "payroll" ? "payroll" : this.report]
					);
				}
				return this.refresh();
			}

			if (d.issue) {
				this.filters.issue = d.issue;
				this.start = 0;
				return this.refresh();
			}
			if (d.company) this.filters.company = d.company;
			if (d.form) Object.assign(this.filters, { approval_type: d.form, category: "", subcategory: "" });
			if (d.category) this.filters.category = d.category;
			if (d.subcategory)
				Object.assign(this.filters, { category: "personnel", subcategory: d.subcategory });
			if (d.personnel) this.filters.category = "personnel";
			if (d.month) {
				const range = monthRange(d.month);
				this.filters.from_date = this.filters.from_date > range.from_date ? this.filters.from_date : range.from_date;
				this.filters.to_date = this.filters.to_date < range.to_date ? this.filters.to_date : range.to_date;
			}
			return this.navigate("details", { ...this.filters, keyword: "", issue: "" });
		}
		async detail(lineKey) {
			const request = (this.detailEpoch = (this.detailEpoch || 0) + 1);
			const data = await call("get_detail", { line_key: lineKey, reporting_currency: this.filters.currency });
			if (request !== this.detailEpoch || !this.isCurrentRoute()) return;
			const r = data.line,
				canReview = this.context.can_review;
			const dialog = new root.frappe.ui.Dialog({
				title: t("Expense Application Details"),
				size: "large",
				fields: [{ fieldname: "content", fieldtype: "HTML" }],
			});
			dialog.$wrapper.addClass("mx-detail-dialog");
			const detail = (label, value) =>
				`<div><dt>${esc(t(label))}</dt><dd>${value}</dd></div>`;
			const sourceLink = data.whole_source && data.dingtalk_url
				? `<div class="mx-detail-source"><a class="mx-button" href="${esc(data.dingtalk_url)}" rel="noopener noreferrer">${esc(t("Open Original in DingTalk"))}</a></div>`
				: "";
			dialog
				.fields_dict.content.$wrapper.html(`<div class="mx-expenses mx-drawer-content">${sourceLink}${r.issues.length ? `<div class="mx-notice mx-warning">${r.issues.map((i) => esc(this.context.issue_labels[i] || i)).join(" · ")}</div>` : ""}<div class="mx-detail-amount"><span>${esc(t("Visible Expense Amount"))} · ${esc(data.visible_currency || r.currency || "—")}</span><strong>${money(data.visible_amount, data.visible_currency || r.currency)}</strong></div><dl class="mx-detail-grid">${detail("Approval Number", esc(r.approval_no || "—"))}${detail("Approval Form", esc(this.form(r.approval_type)))}${detail("Business Category", esc(r.business_category || "—"))}${detail("Salary Type", esc(r.salary_type || "—"))}${detail("Approval Status", pill(r.approval_status))}${detail("Subsidiary", esc(r.company || t("Not Mapped")))}${detail("Source Entity", esc(r.source_entity))}${detail("Expense Category", esc(this.cat(r.category)?.label || t("Unclassified")))}${detail("Expense Subcategory", esc(this.sub(r.subcategory)?.label || t("Unclassified")))}${detail("Application Date", date(r.request_date))}${detail("Planned Payment Date", date(r.planned_payment_date))}${detail("MX Finance Approval Date", date(r.finance_approved_date))}${detail("Reporting Month", esc(String(r.period_month || t("Not Confirmed")).slice(0, r.period_month ? 7 : 200)))}${detail("Expense Period", r.period_start ? `${date(r.period_start)} – ${date(r.period_end)}` : esc(t("Not Confirmed")))}</dl>
                ${data.whole_source ? `<dl class="mx-detail-grid">${Object.entries(data.details?.fields || {}).filter(([, value]) => value).map(([label, value]) => detail(label, esc(value))).join("")}</dl>${(data.details?.tables || []).map((table) => {
                    const labels = [...new Set(table.rows.flatMap((row) => row.cells.map((cell) => cell.label)))];
                    return `<h3>${esc(table.label)}</h3><p class="mx-help">${esc(t("Supporting item details; amounts are not added again"))}</p><div class="mx-scroll"><table><thead><tr>${labels.map((label) => `<th>${esc(label)}</th>`).join("")}</tr></thead><tbody>${table.rows.map((row) => `<tr>${labels.map((label) => `<td>${esc(row.cells.find((cell) => cell.label === label)?.value || "—")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
                }).join("")}` : ""}
                <h3>${esc(
					t("Expense Details")
				)}</h3><div class="mx-scroll"><table><thead><tr><th>${esc(t("Subsidiary"))}</th><th>${esc(t("Expense Description"))}</th><th class="num">${esc(t("Converted Amount"))}</th></tr></thead><tbody>${data.lines.map((l) => `<tr><td>${esc(l.company || l.source_entity)}</td><td>${esc(l.description)}</td><td class="num">${this.lineAmount(l)}</td></tr>`).join("")}</tbody></table></div>
                ${
					!data.whole_source
						? `<div class="mx-notice mx-warning">${esc(
								t(
									"Only authorized subsidiary lines are shown; the full approval and attachments require access to all subsidiaries"
								)
						  )}</div>`
						: `<h3>${esc(t("Approval Timeline"))}</h3><div class="mx-timeline">${
								(data.workflow?.events || [])
									.map(
										(e) =>
											`<div><strong>${esc(
												e.stage || "—"
											)}</strong><small>${esc(sourceTime(e.time))} · ${esc(
												e.operator || "—"
											)}</small>${
												e.comment ? `<p>${esc(e.comment)}</p>` : ""
											}</div>`
									)
									.join("") || esc(t("No approval timeline available"))
						  }</div><h3>${esc(
								t("Key Attachments")
						  )}</h3><div class="mx-attachments">${button(
								"Load OA Attachments",
								"attachments"
						  )}</div><p class="mx-help">${esc(
								t("Attachment presence does not confirm payment")
						  )}</p>`
				}
                ${
					r.review_note
						? `<h3>${esc(t("Review Note"))}</h3><p>${esc(
								r.review_note
						  )}</p><p class="mx-help">${esc(r.reviewed_by || "")} · ${esc(
								String(r.reviewed_at || "")
						  )}</p>`
						: ""
				}
                ${
					canReview
						? `<div class="mx-detail-actions">${button(
								"Review Expense Line",
								"confirm",
								"mx-button mx-primary"
						  )}</div>`
						: ""
				}</div>`);
			dialog.fields_dict.content.$wrapper.on("click", "[data-action]", async (event) => {
				const action = event.currentTarget.dataset.action;
				if (action === "confirm") return this.review(data.line, dialog);
				if (action === "attachments") {
					const area = dialog.fields_dict.content.$wrapper.find(".mx-attachments");
					area.text(t("Loading"));
					try {
						const files = await call("get_attachments", { line_key: lineKey });
						area.html(
							files.length
								? files
										.map(
											(file) =>
												`<button type="button" class="mx-attachment" data-file="${esc(
													file.file_id
												)}" ${
													file.status !== "archived" ? "disabled" : ""
												}>${esc(file.filename)} <span>${esc(
													t(
														file.status === "archived"
															? "Download"
															: "Not Archived"
													)
												)}</span></button>`
										)
										.join("")
								: esc(t("No archived attachments"))
						);
					} catch {
						area.html(button("Retry", "attachments"));
					}
				}
			});
			dialog.fields_dict.content.$wrapper.on("click", "[data-file]", (event) =>
				this.download(lineKey, event.currentTarget.dataset.file)
			);
			dialog.show();
		}
		review(row, parent) {
			const fields = [
				{
					fieldname: "source",
					fieldtype: "HTML",
					options: `<p>${esc(row.source_entity)} · ${esc(row.approval_no || "")}</p>`,
				},
			];
			if (this.context.can_assign_company)
				fields.push({
					fieldname: "company",
					label: t("Subsidiary"),
					fieldtype: "Select",
					options: ["", ...this.context.companies],
					default: row.company || "",
				});
			fields.push(
				{
					fieldname: "automatic_period",
					fieldtype: "HTML",
					options: `<p>${esc(t("Reporting months follow MX Finance approval in the business timezone"))}</p><p>${esc(t("Reporting Month"))}: ${esc(row.period_month ? String(row.period_month).slice(0, 7) : t(row.issues?.includes("period_stale") ? "Expense period recalculation pending" : "Awaiting MX Finance approval"))}</p>`,
				},
				{
					fieldname: "note",
					label: t("Review Note"),
					fieldtype: "Small Text",
					reqd: 1,
					default: row.review_note || "",
				},
				{
					fieldname: "notice",
					fieldtype: "HTML",
					options: `<p class="text-muted">${esc(
						t(
							"Source amount, currency and allocation errors must be corrected in OA and synchronized again"
						)
					)}</p>`,
				}
			);
			const dialog = new root.frappe.ui.Dialog({
				title: t("Review Expense Line"),
				fields,
				primary_action_label: t("Save Review"),
				primary_action: async (values) => {
					dialog.disable_primary_action();
					try {
						const result = await call("review_line", {
							company: values.company,
							note: values.note,
							line_key: row.name,
							expected_source_version: row.source_version,
							expected_modified: row.modified,
						});
						dialog.hide();
						parent.hide();
						root.frappe.show_alert({
							message: t(
								result.review_state === "confirmed"
									? "Expense line confirmed"
									: "Review saved; source issues still require attention"
							),
							indicator: result.review_state === "confirmed" ? "green" : "orange",
						});
						await this.refresh();
					} finally {
						dialog.enable_primary_action();
					}
				},
			});
			dialog.show();
		}
		async settings() {
			this.context = await call("get_context");
			if (!this.context.can_manage) {
				return root.frappe.msgprint(
					t("Access to all mapped subsidiaries is required to manage synchronization")
				);
			}
			const saved = this.context.settings;
			const dialog = new root.frappe.ui.Dialog({
				title: t("Mexico Expense Synchronization"),
				size: "large",
				fields: [
					{
						fieldname: "enabled",
						label: t("Enable Mexico Expense Sync"),
						fieldtype: "Check",
						default: this.context.enabled,
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
						fieldname: "approval_templates", label: t("Approval Templates"), fieldtype: "Table",
						cannot_delete_rows: true, data: saved.templates || [],
						fields: [
							{fieldname: "enabled", label: t("Enabled"), fieldtype: "Check", default: 0, in_list_view: 1, columns: 1},
							{fieldname: "approval_type", label: t("Approval Form"), fieldtype: "Select", options: this.context.approval_forms.map((f) => ({value:f.id,label:f.label})), reqd:1, in_list_view:1, columns:2},
							{fieldname: "template_name", label: t("Template Name"), fieldtype:"Data", in_list_view:1, columns:3},
							{fieldname: "corp_id", label: t("Corporation ID"), fieldtype:"Data", reqd:1},
							{fieldname: "process_code", label: t("Process Code"), fieldtype:"Data", reqd:1, in_list_view:1, columns:4},
							{fieldname: "finance_node_names", label: t("Finance Node Names"), fieldtype:"Small Text", default:"MX财务Finanzas"},
							{fieldname: "finance_activity_ids", label: t("Finance Activity IDs"), fieldtype:"Small Text"},
							{fieldname: "field_mapping", label: t("Field Mapping JSON"), fieldtype:"Code", options:"JSON", default:"{}"},
							{fieldname: "last_sync_at", label: t("Last Successful Sync"), fieldtype:"Datetime", read_only:1},
							{fieldname: "last_error", label: t("Last Sync Error"), fieldtype:"Small Text", read_only:1},
						],
					},
					{fieldname:"template_help", fieldtype:"HTML", options:`<p class="text-muted">${esc(t("Select one template to preview. Disable historical templates instead of deleting them. Field mappings use logical names and component ID arrays."))}</p>`},
					{fieldname:"preview_template", label:t("Preview Selected Template"), fieldtype:"Button", click:async () => {
						const selected = dialog.fields_dict.approval_templates.grid.get_selected_children();
						if (selected.length !== 1) return root.frappe.msgprint(t("Select exactly one template"));
						const result = await call("preview_template", {template:selected[0], business_timezone:dialog.get_value("business_timezone")});
						dialog.fields_dict.template_preview.$wrapper.html(`<div class="mx-notice">${esc(t("{0} applications · {1} expense lines", [result.applications,result.lines]))}<br>${esc(result.finance_nodes.join(" / "))}<br>${esc(result.issues.map((issue) => this.context.issue_labels[issue] || issue).join(" / "))}</div>`);
					}},
					{fieldname:"template_preview", fieldtype:"HTML"},
					{
						fieldname: "company_mappings",
						label: t("Subsidiary Mappings"),
						fieldtype: "Table",
						cannot_add_rows: false,
						data: saved.company_mappings,
						fields: [
							{
								fieldname: "source_entity",
								label: t("Source Entity"),
								fieldtype: "Data",
								in_list_view: 1,
								reqd: 1,
							},
							{
								fieldname: "entity_kind",
								label: t("Entity Kind"),
								fieldtype: "Select",
								options: [
									{ value: "entity", label: t("Expense Entity") },
									{ value: "department", label: t("Department") },
								],
								default: "entity",
								in_list_view: 1,
								reqd: 1,
							},
							{
								fieldname: "company",
								label: t("Subsidiary"),
								fieldtype: "Select",
								options: this.context.companies,
								in_list_view: 1,
								reqd: 1,
							},
						],
					},
				],
				primary_action_label: t("Save Settings"),
				primary_action: async (values) => {
					dialog.disable_primary_action();
					try {
						await call("save_settings", {
							enabled: values.enabled || 0,
							business_timezone: values.business_timezone,
							approval_templates: values.approval_templates,
							company_mappings: values.company_mappings.map((r) => ({
								source_entity: r.source_entity,
								entity_kind: r.entity_kind,
								company: r.company,
							})),
							expected_config_version: saved.config_version,
						});
						dialog.hide();
						this.context = await call("get_context");
						await this.refresh();
						root.frappe.show_alert({
							message: t("Settings saved; cached records will be rechecked"),
							indicator: "green",
						});
					} finally {
						dialog.enable_primary_action();
					}
				},
			});
			dialog.show();
		}
		async synchronize() {
			await call("enqueue_sync");
			root.frappe.show_alert({
				message: t("Synchronization queued; refresh after the background job finishes"),
				indicator: "blue",
			});
			this.context = await call("get_context");
			this.setSyncTime();
		}
		async export() {
			if (!root.DeepLinkERPPurchaseOrderExport?.fetchNativeWorkbook)
				await root.frappe.require(
					"/assets/deeplinkerp_branding/js/purchase_order_export.js"
				);
			const exporter = root.DeepLinkERPPurchaseOrderExport;
			const kind =
				this.view === "overview"
					? "company"
					: this.view === "reports"
					? this.report
					: this.view;
			const bytes = await exporter.fetchNativeWorkbook(
				root,
				{ filters: JSON.stringify(this.effectiveFilters()), kind },
				API + "export_report"
			);
			exporter.downloadWorkbook(
				root,
				bytes,
				t("Mexico Expenses") + "-" + this.filters.from_date + "_" + this.filters.to_date + "-" + this.filters.currency
			);
		}
		async download(lineKey, fileId) {
			// Same-origin authenticated POST; no upstream URL reaches the browser.
			const response = await root.fetch(root.frappe.request.url || "/", {
				method: "POST",
				credentials: "same-origin",
				headers: {
					"Content-Type": "application/x-www-form-urlencoded",
					"X-Frappe-CSRF-Token": root.frappe.csrf_token,
				},
				body: new URLSearchParams({
					cmd: API + "download_attachment",
					line_key: lineKey,
					file_id: fileId,
				}),
			});
			const type = response.headers.get("content-type") || "";
			if (!response.ok || /json|html/.test(type))
				return root.frappe.msgprint(
					t("Attachment download failed; refresh or synchronize the source")
				);
			const blob = await response.blob(),
				url = root.URL.createObjectURL(blob),
				a = root.document.createElement("a");
			a.href = url;
			const disposition = response.headers.get("content-disposition") || "";
			const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
			a.download = encoded
				? decodeURIComponent(encoded)
				: disposition.match(/filename="([^"]+)"/i)?.[1] || "attachment";
			a.click();
			root.setTimeout(() => root.URL.revokeObjectURL(url), 1000);
		}
	}
	return { Workbench, cents, decimal, totals, monthsEnding, monthRange, monthsInRange, validDateRange, esc, views, routes, reportRoutes, reportFromRoute, viewFromRoute, installBreadcrumbs };
});
