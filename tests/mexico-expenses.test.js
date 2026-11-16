const test = require("node:test");
const assert = require("node:assert/strict");
const factory = require("../deeplinkerp_branding/public/js/mexico_expenses.js");
const api = factory({
	frappe: { format: (value) => String(value), datetime: { str_to_user: (value) => value } },
});

test("minor-unit sums preserve decimal amounts", () => {
	assert.equal(api.decimal(api.cents("0.10") + api.cents("0.20")), "0.30");
	assert.equal(api.decimal(api.cents("-0.05")), "-0.05");
});
test("large monetary strings never accumulate binary rounding", () => {
	assert.equal(
		api.decimal(api.cents("9999999999999.99") + api.cents("0.01")),
		"10000000000000.00"
	);
});
test("grouped money totals remain strings and keep companies separate", () => {
	assert.deepEqual(
		api.totals(
			[
				{ company: "A", amount: "0.10" },
				{ company: "A", amount: "0.20" },
				{ company: "B", amount: "1" },
			],
			(r) => r.company
		),
		{ A: "0.30", B: "1.00" }
	);
});
test("trend spans year boundary without local timezone shifts", () => {
	assert.deepEqual(api.monthsEnding("2026-02"), [
		"2025-09",
		"2025-10",
		"2025-11",
		"2025-12",
		"2026-01",
		"2026-02",
	]);
});
test("source descriptions and approval numbers are HTML escaped", () => {
	assert.equal(
		api.esc('<img src=x onerror="alert(1)">&'),
		"&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&amp;"
	);
});
test("payroll requests always use explicit expense periods", () => {
	const workbench = {
		filters: { basis: "request", category: "tax", currency: "USD" },
		view: "payroll",
	};
	const result = api.Workbench.prototype.effectiveFilters.call(workbench);
	assert.equal(result.basis, "period");
	assert.equal(result.category, "personnel");
	assert.equal(result.currency, "USD");
	assert.equal(workbench.filters.basis, "request");
});
test("payroll report and payroll page use the same filter rules", () => {
	const workbench = {
		filters: { basis: "planned", status: "pending" },
		view: "reports",
		report: "payroll",
	};
	assert.deepEqual(api.Workbench.prototype.effectiveFilters.call(workbench), {
		basis: "period",
		status: "pending",
		category: "personnel",
	});
});
test("expense detail respects the selected basis and approval status", () => {
	const workbench = {
		filters: { basis: "planned", status: "rejected", company: "A" },
		view: "details",
	};
	assert.deepEqual(api.Workbench.prototype.effectiveFilters.call(workbench), workbench.filters);
});
test("empty monetary datasets show an exact zero", () => {
	assert.deepEqual(
		api.totals([], (r) => r.company),
		{}
	);
	assert.equal(api.decimal(api.cents(null)), "0.00");
});

test("currency formatting receives the row currency instead of a site default", () => {
	const calls = [];
	const local = factory({
		frappe: {
			format: (value, df, options, doc) => {
				calls.push({ df, options, doc });
				return String(value);
			},
		},
	});
	const workbench = Object.create(local.Workbench.prototype);
	Object.assign(workbench, {
		filters: { month: "2026-11", basis: "period", currency: "MXN" },
		context: { approval_forms: [], categories: [] },
		trend: () => "",
		donut: () => "",
		companyTable: () => "",
	});
	workbench.overview({
		metrics: [{ approval_status: "approved", amount: "1", applications: 1 }],
		review_count: 0,
	});
	const currencies = calls.filter((c) => c.df.fieldtype === "Currency");
	assert(currencies.length > 0);
	assert(currencies.every((c) => c.df.options === "currency" && c.doc.currency === "MXN"));
	assert(calls.every((c) => c.options.inline && c.options.only_value));
});

test("incomplete monetary sources do not display a false complete application total", () => {
	const workbench = Object.create(api.Workbench.prototype);
	Object.assign(workbench, {
		filters: { month: "2026-11", basis: "period", currency: "MXN" },
		context: { approval_forms: [], categories: [] },
		trend: () => "",
		donut: () => "",
		companyTable: () => "",
	});
	const html = workbench.overview({
		metrics: [
			{ approval_status: "approved", amount: "10", applications: 2, invalid_amounts: 1 },
		],
		review_count: 1,
	});
	assert(html.includes("Amount incomplete"));
	assert(html.includes('class="mx-card-value">—</div>'));
});

test("five views resolve from independent native Page routes", () => {
	assert.equal(new Set(Object.values(api.routes)).size, 5);
	for (const view of Object.keys(api.views)) {
		assert.equal(api.viewFromRoute([api.routes[view]]), view);
	}
	assert.equal(api.viewFromRoute(["sales-order"]), null);
});

test("route breadcrumbs keep parents clickable and the current page as text", () => {
	let route = [api.routes.review], delegated = 0;
	const children = [], links = [];
	const breadcrumbs = {
		set_custom_breadcrumbs() { delegated++; },
		append_breadcrumb_element(href, label) { links.push({ href, label }); },
		$breadcrumbs: { append: (item) => children.push(item) },
	};
	const local = factory({
		frappe: { breadcrumbs, get_route: () => route },
		document: { createElement: (tag) => ({
			tag, children: [], attributes: {},
			appendChild(node) { this.children.push(node); },
			setAttribute(name, value) { this.attributes[name] = value; },
		}) },
	});
	local.installBreadcrumbs();
	const installed = breadcrumbs.set_custom_breadcrumbs;
	local.installBreadcrumbs();
	assert.equal(breadcrumbs.set_custom_breadcrumbs, installed);
	for (const view of Object.keys(api.views)) {
		route = [api.routes[view]];
		breadcrumbs.set_custom_breadcrumbs({ label: "old page title" });
		assert.equal(links.at(-1).href, view === "reports" ? "/desk/mexico-expense-reports" : "/desk/mexico-expenses");
		const current = children.at(-1).children[0];
		assert.equal(current.tag, "span");
		assert.equal(current.attributes["aria-current"], "page");
		assert.equal(current.textContent, view === "reports" ? "Mexico Subsidiary Expenses" : api.views[view]);
		assert(!current.attributes.href);
	}
	route = ["other-page"];
	breadcrumbs.set_custom_breadcrumbs({ label: "Other" });
	assert.equal(delegated, 1);
});

test("drilldown filters travel in the URL and restore on direct load or back navigation", async () => {
	let target;
	let route = [api.routes.details];
	const root = { location: { search: "" }, frappe: { get_route: () => route, set_route: (...args) => { target = args; } } };
	const local = factory(root);
	const source = Object.create(local.Workbench.prototype);
	const filters = { from_date: "2026-09-28", to_date: "2026-10-02", company: "Subsidiary A", category: "tax", currency: "USD" };
	await source.navigate("details", filters);
	assert.equal(target[0], api.routes.details);
	assert.deepEqual(target[1].mx_filters, filters);
	root.location.search = "?mx_filters=" + encodeURIComponent(JSON.stringify(target[1].mx_filters));
	const destination = Object.create(local.Workbench.prototype);
	destination.view = "details";
	destination.filters = { from_date: "2026-11-01", to_date: "2026-11-30", company: "", category: "", currency: "MXN" };
	destination.start = 25;
	destination.applyRouteFilters();
	assert.deepEqual(destination.filters, filters);
	assert.equal(destination.start, 0);
	destination.filters.currency = "MXN";
	destination.applyRouteFilters();
	assert.equal(destination.filters.currency, "USD");
	route = [api.routes.review];
	destination.filters.currency = "MXN";
	destination.applyRouteFilters();
	assert.equal(destination.filters.currency, "MXN");
	root.location.search = "?mx_filters=invalid";
	assert.doesNotThrow(() => destination.applyRouteFilters());
});

test("content starts at filters and no longer duplicates the title or five tabs", () => {
	const workbench = Object.create(api.Workbench.prototype);
	Object.assign(workbench, {
		view: "overview", filters: { month: "2026-11", basis: "period", currency: "MXN", status: "active" },
		context: { companies: [], categories: [], approval_forms: [] }, el: {}, setSyncTime() {}, mountDateControls() {}, mountSelectControls() {}, disposeFilterControls() {},
	});
	workbench.shell();
	assert(workbench.el.innerHTML.startsWith('<div class="mx-filters">'));
	assert(!workbench.el.innerHTML.includes('mx-heading'));
	assert(!workbench.el.innerHTML.includes('mx-nav'));
});

test("expense review displays automatic finance period without editable month controls", () => {
	let dialog;
	const local = factory({ frappe: { ui: { Dialog: class {
		constructor(options) { dialog = options; }
		show() {}
	} } } });
	const workbench = Object.create(local.Workbench.prototype);
	workbench.context = { can_assign_company: true, companies: ["Company A"] };
	workbench.review({ period_month: "2026-10-01", source_entity: "A", approval_no: "1" }, {});
	assert(dialog.fields.some((field) => field.fieldname === "automatic_period" && field.options.includes("2026-10")));
	assert(!dialog.fields.some((field) => ["month", "period_start", "period_end", "period_kind"].includes(field.fieldname)));
});


test("report children have stable routes and the shared reports view", () => {
	for (const [kind, route] of Object.entries(api.reportRoutes)) {
		assert.equal(api.reportFromRoute([route]), kind);
		assert.equal(api.viewFromRoute([route]), "reports");
	}
	assert.equal(api.reportFromRoute(["mexico-personnel-expenses"]), null);
});


test("inactive sibling reports cannot consume the current route filters", () => {
	const local = factory({location: {search: '?mx_filters=%7B%22month%22%3A%222026-09%22%7D'}, frappe: {get_route: () => ['mexico-expense-trend']}});
	const report = Object.create(local.Workbench.prototype);
	Object.assign(report, {view:'reports', wrapper:{page_name:'mexico-personnel-report'}, filters:{month:'2026-11'}});
	report.applyRouteFilters();
	assert.equal(report.filters.month, '2026-11');
	assert.equal(report.isCurrentRoute(), false);
});


test("date range helpers cover single-day and cross-year selections", () => {
    assert.deepEqual(api.monthRange('2024-02'),{from_date:'2024-02-01',to_date:'2024-02-29'});
    assert.deepEqual(api.monthsInRange('2026-12-31','2027-01-01'),['2026-12','2027-01']);
    assert.deepEqual(api.monthsInRange('2026-11-12','2026-11-12'),['2026-11']);
});

test("monthly report drilldown keeps the intersection with the selected exact range", async () => {
    const page=Object.create(api.Workbench.prototype);let target;
    Object.assign(page,{filters:{from_date:'2026-09-28',to_date:'2026-10-02'},navigate:(view,filters)=>{target={view,filters};}});
    await page.click({target:{closest:()=>({dataset:{month:'2026-09',company:'A',personnel:'1'}})}});
    assert.equal(target.view,'details');
    assert.equal(target.filters.from_date,'2026-09-28');
    assert.equal(target.filters.to_date,'2026-09-30');
    assert.equal(target.filters.company,'A');
    assert.equal(target.filters.category,'personnel');
});
