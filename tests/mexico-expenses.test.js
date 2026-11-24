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
		assert.equal(current.textContent, view === "reports" ? "Expenditure Analysis" : api.views[view]);
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
		context: { companies: [], categories: [], approval_forms: [] }, el: {}, setSyncTime() {}, mountDateControls() {}, mountDashboardMonth() {}, mountSelectControls() {}, disposeFilterControls() {},
	});
	workbench.shell();
	assert(workbench.el.innerHTML.startsWith('<div class="mx-filters mx-dashboard-filters">'));
	assert(workbench.el.innerHTML.includes('mx-dashboard-month-picker'));
	assert(!workbench.el.innerHTML.includes('type="month"'));
	assert(!workbench.el.innerHTML.includes('data-filter="status"'));
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

test("review submits a scope override only after the displayed scope changes", async () => {
	let dialog;
	const calls = [];
	const local = factory({ frappe: {
		ui: { Dialog: class {
			constructor(options) { dialog = options; }
			show() {}
			hide() {}
			disable_primary_action() {}
			enable_primary_action() {}
		} },
		call: async (options) => { calls.push(options); return { message: { review_state: "needs_review" } }; },
		show_alert() {},
	} });
	const workbench = Object.create(local.Workbench.prototype);
	workbench.context = { can_assign_company: true, companies: ["A"] };
	workbench.refresh = async () => {};
	for (const [displayed, selected, expected, issues] of [
		[undefined, "mx", undefined, []],
		["mx", "mx", undefined, ["stale"]],
		["non_mx", "non_mx", undefined, []],
		["mx", "non_mx", "non_mx", []],
		["non_mx", "mx", "mx", []],
	]) {
		workbench.review({ name: "line", company: "A", expense_scope: displayed, issues,
			source_version: "v", modified: "m" }, { hide() {} });
		await dialog.primary_action({ company: "A", expense_scope: selected, note: "Checked" });
		assert.equal(calls.at(-1).args.expense_scope, expected);
		assert.equal(calls.at(-1).args.company, "A");
		assert.equal(calls.at(-1).args.expected_source_version, "v");
	}
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

function preferenceWorkbench(storage = new Map()) {
 const root = {
  location: { href: 'http://erp.test/desk/mexico-expenses?other=keep', search: '' },
  history: { state: { marker: 1 }, replaceState(state, title, url) { this.last = { state, url }; } },
  localStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value) },
  frappe: { session: { user: 'user-a' }, get_route: () => ['mexico-expenses'] },
 };
 const local = factory(root), page = Object.create(local.Workbench.prototype);
 Object.assign(page, { view: 'overview', context: { today: '2026-11-17', approval_forms: [{id:'operating'},{id:'expense'},{id:'purchase'}] },
  filters: { from_date:'2026-11-01', to_date:'2026-11-30', approval_type:'', company:'', category:'', currency:'MXN', status:'active', basis:'period' }, start:0 });
 return {root, page, storage};
}

test('module entry starts this month and all forms; analysis retains saved preferences', () => {
 const {page, storage} = preferenceWorkbench();
 page.restorePreferences();
 assert.equal(page.filters.from_date,'2026-11-01');
 Object.assign(page.filters,{from_date:'2026-09-12',to_date:'2026-10-04',approval_type:'purchase',company:'Private Company'});
 page.rememberFilters();
 const next=preferenceWorkbench(storage).page;
 next.restorePreferences();
 assert.equal(next.filters.from_date,'2026-11-01');
 assert.equal(next.filters.approval_type,'');
 next.view='reports';
 next.restorePreferences();
 assert.equal(next.filters.from_date,'2026-09-12');
 assert.equal(next.filters.to_date,'2026-10-04');
 assert.equal(next.filters.approval_type,'purchase');
 assert.equal(next.filters.company,'');
 assert(![...storage.values()][0].includes('Private Company'));
});

test('saved preferences are isolated by login user', () => {
 const {page,root}=preferenceWorkbench();
 Object.assign(page.filters,{from_date:'2026-09-01',to_date:'2026-09-30'});
 page.rememberFilters();
 root.frappe.session.user='user-b';
 Object.assign(page.filters,api.monthRange('2026-11'));
 page.restorePreferences();
 assert.equal(page.filters.from_date,'2026-11-01');
});

test('invalid or incomplete ranges never overwrite the previous valid preference', () => {
 const {page,storage}=preferenceWorkbench();page.rememberFilters();
 const saved=storage.get(page.preferenceKey());
 for(const range of [
  {from_date:'',to_date:'2026-11-30'}, {from_date:'2026-11-20',to_date:'2026-11-01'},
  {from_date:'2026-02-30',to_date:'2026-03-01'}, {from_date:'1899-01-01',to_date:'2026-11-30'},
 ]) { Object.assign(page.filters,range); page.rememberFilters(); assert.equal(storage.get(page.preferenceKey()),saved); }
 assert(api.validDateRange({from_date:'2024-02-29',to_date:'2024-02-29'}));
});

test('corrupt or disabled browser storage does not block defaults or URL updates', () => {
 const {page,root,storage}=preferenceWorkbench();
 storage.set(page.preferenceKey(),'{bad');
 assert.doesNotThrow(()=>page.restorePreferences());
 Object.defineProperty(root,'localStorage',{get(){throw new Error('denied');}});
 assert.doesNotThrow(()=>page.restorePreferences());
 assert.doesNotThrow(()=>page.rememberFilters());
 assert(root.history.last.url.includes('mx_filters='));
});

test('explicit legacy month URL overrides remembered dates; edits replace stale URL', () => {
 const {page,root}=preferenceWorkbench();
 page.rememberFilters();
 root.location.search='?mx_filters='+encodeURIComponent(JSON.stringify({month:'2026-09',approval_type:'expense'}));
 page.restorePreferences();page.applyRouteFilters();
 assert.equal(page.filters.from_date,'2026-09-01');
 assert.equal(page.filters.to_date,'2026-09-30');
 Object.assign(page.filters,{from_date:'2026-10-02',to_date:'2026-10-02'});
 page.rememberFilters();
 const url=new URL(root.history.last.url,'http://erp.test');
 assert.equal(url.searchParams.get('other'),'keep');
 assert.equal(JSON.parse(url.searchParams.get('mx_filters')).from_date,'2026-10-02');
 assert.deepEqual(root.history.last.state,{marker:1});
 root.location.search=url.search;
 page.applyRouteFilters();
 assert.equal(page.filters.from_date,'2026-10-02');
});

test('inactive page cannot overwrite preferences or the active page URL', () => {
 const {page,root,storage}=preferenceWorkbench();
 root.frappe.get_route=()=>['mexico-expense-trend'];
 page.rememberFilters();
 assert.equal(storage.size,0);assert.equal(root.history.last,undefined);
});

test('reset filters restores the current month, all forms and view defaults', async () => {
 const {page,root}=preferenceWorkbench();
 root.frappe.call=async()=>({message:page.context});
 root.frappe.get_route=()=>['mexico-expense-reports'];let refreshed=0;
 page.refresh=()=>{refreshed++;};page.view='reports';page.start=50;
 Object.assign(page.filters,{from_date:'2026-09-12',to_date:'2026-10-04',approval_type:'operating',company:'A',currency:'USD',status:'rejected',keyword:'x',issue:'amount_pending'});
 await page.click({target:{closest:()=>({dataset:{action:'reset'}})}});
 assert.equal(page.filters.from_date,'2026-11-01');assert.equal(page.filters.to_date,'2026-11-30');
 assert.equal(page.filters.approval_type,'');assert.equal(page.filters.company,'');
 assert.equal(page.filters.status,'finance_approved');assert.equal(page.filters.currency,'MXN');
 assert.equal(page.filters.keyword,'');assert.equal(page.filters.issue,'');assert.equal(page.start,0);assert.equal(refreshed,1);
});

test('reset fetches the latest business date even after a year boundary', async () => {
 const {page,root}=preferenceWorkbench();
 root.frappe.call=async()=>({message:{...page.context,today:'2027-01-01'}});
 page.refresh=()=>{};page.start=50;
 Object.assign(page.filters,{company:'A',approval_type:'expense',currency:'USD',status:'rejected'});
 await page.click({target:{closest:()=>({dataset:{action:'reset'}})}});
 assert.equal(page.filters.from_date,'2027-01-01');
 assert.equal(page.filters.to_date,'2027-01-31');
 assert.equal(page.filters.approval_type,'');
 assert.equal(page.filters.currency,'MXN');
 assert.equal(page.start,0);
});

test("all workbench views request explicit reporting currency without altering stored source facts", () => {
    for (const view of ['overview','details','payroll','reports','review']) {
        const workbench={filters:{currency:'CNY'},view,report:'company'};
        assert.equal(api.Workbench.prototype.effectiveFilters.call(workbench).reporting_currency,'CNY');
        assert.deepEqual(workbench.filters,{currency:'CNY'});
    }
});

test("converted line rendering retains original amounts and estimated rate evidence", () => {
    const html=api.Workbench.prototype.lineAmount({currency:'USD',amount:'10.00',reporting_currency:'CNY',converted_amount:'70.00',exchange_rate:'7.000000000000',exchange_rate_date:'2026-11-01',fx_estimated:true});
    for (const text of ['70.00','Original Amount','10.00','USD','7.000000000000','2026-11-01','Estimated Conversion']) assert(html.includes(text),text);
    const incomplete=api.Workbench.prototype.lineAmount({currency:null,amount:'10.00',reporting_currency:'CNY',converted_amount:null});
    assert(incomplete.startsWith('—'));
});

test("DingTalk entry uses the authorized source link without loading attachments", async () => {
    const url = 'dingtalk://dingtalkclient/page/link?url=https%3A%2F%2Faflow.dingtalk.com%2F&pc_slide=true';
    for (const [whole, link, expected] of [[true, url, true], [false, url, false], [true, '', false]]) {
        let html = ''; const requests = [];
        const content = {html(value) { html = value; return this; }, on() {return this;}};
        const data = {whole_source: whole, dingtalk_url: link, visible_amount: '10', lines: [],
            line: {issues: [], amount: '10', currency: 'MXN', approval_no: '123'}};
        const local = factory({frappe: {
            format: (value) => String(value),
            call: async ({method}) => {requests.push(method); return {message: data};},
            ui: {Dialog: class {
                constructor() {this.$wrapper = {addClass() {}}; this.fields_dict = {content: {$wrapper: content}};}
                show() {}
            }},
        }});
        const workbench = Object.create(local.Workbench.prototype);
        Object.assign(workbench, {filters: {currency: 'MXN'}, context: {can_review: false},
            isCurrentRoute: () => true, form: () => '', cat: () => null, sub: () => null});
        await workbench.detail('line');
        assert.equal(html.includes('Open Original in DingTalk'), expected);
        if (expected) {
            assert(html.includes('href="' + local.esc(url) + '"'));
            assert(html.indexOf('mx-detail-source') < html.indexOf('mx-detail-amount'));
        }
        assert.deepEqual(requests, ['deeplinkerp_branding.services.mexico_expenses.get_detail']);
    }
});

test("composition legend follows filtered amounts and retains form/category drilldown semantics", () => {
    const categories = [
        {id:'personnel', label:'Personnel', color:'#547ee0'},
        {id:'tax', label:'Taxes', color:'#69b8ae'},
        {id:'administration', label:'Administration', color:'#88a4be'},
        {id:'procurement', label:'Purchases', color:'#c5a26b'},
    ];
    const workbench = Object.create(api.Workbench.prototype);
    workbench.context = {categories};
    for (const [rows, expected] of [
        [[{category:'personnel',amount:'25.00'},{category:'administration',amount:'75.00'}], ['personnel','administration']],
        [[{category:'tax',amount:'10.00'},{category:'personnel',amount:'0.00'}], ['tax']],
        [[{category:'procurement',amount:'10.00'}], ['procurement']],
    ]) {
        const html=workbench.donut(rows);
        for (const c of categories) assert.equal(html.includes(`data-category="${c.id}"`), expected.includes(c.id));
        assert(!html.includes('NaN'));
    }
    assert.equal(categories.length,4);
    assert(workbench.donut([{category:'tax',amount:'0.00'}]).includes('mx-empty'));
    assert(!workbench.donut([]).includes('mx-donut'));
    const forms = ['operating','expense','purchase'].map(id=>({id,label:id,color:'#547ee0'}));
    const all=workbench.donut([{category:'expense',amount:'10.00'}],forms,true);
    for(const f of forms) assert(all.includes(`data-form="${f.id}"`));
    assert(!all.includes('data-category='));
});

function withSettingsAssets(root) {
    root.__ = (value) => value;
    root.frappe.utils = {escape_html: (value) => factory(root).esc(value)};
    root.frappe.require = (_paths, done) => {
        root.DeepLinkERPMexicoExpenseSettings = require("../deeplinkerp_branding/public/js/mexico_expense_settings.js")(root);
        done();
    };
    return root;
}

test("template grid uses approval labels in both editing and static cells without changing stored codes", async () => {
    for (const label of ['费用支出', 'Expense Requests', 'Solicitudes de gastos']) {
        let options;
        const local = factory(withSettingsAssets({DeepLinkERPMexicoExpenseMappings: {Editor: class {async loadPage() {}}}, frappe: {
            call: async () => ({message: {can_manage: true, approval_forms: [{id:'expense',label}],
                settings: {templates: [],company_mappings: []}}}),
            ui: {Dialog: class {constructor(value) {options=value;this.fields_dict={company_mappings:{$wrapper:{}}};} show() {}}},
        }}));
        const page = Object.create(local.Workbench.prototype);
        await page.settings();
        const field=options.fields.find(f=>f.fieldname==='approval_templates').fields.find(f=>f.fieldname==='approval_type');
        assert.deepEqual(field.options,[{value:'expense',label}]);
        assert.equal(field.formatter('expense'),label);
    }
});

test("settings save sends explicitly edited alias draft through the existing versioned API", async () => {
    let options, editor, submitted;
    const original = [{source_entity:"Old A", entity_kind:"entity", company:"A"}];
    const context = {can_manage:true, companies:["A"], approval_forms:[], settings:{
        config_version:"v1", company_mappings:original, templates:[]}};
    const local = factory(withSettingsAssets({
        DeepLinkERPMexicoExpenseMappings:{Editor:class {
            constructor(config) {this.mappings=config.mappings.map(r=>({...r}));editor=this;}
            async loadPage() {}
        }},
        frappe:{call:async ({method,args})=>{
            if (method.endsWith("save_settings")) {submitted=args;return {message:{ok:true}};}
            return {message:context};
        },show_alert:()=>{},ui:{Dialog:class {
            constructor(value) {options=value;this.fields_dict={company_mappings:{$wrapper:{}}};}
            show() {} hide() {} disable_primary_action() {} enable_primary_action() {}
        }}}
    }));
    const wb=Object.create(local.Workbench.prototype);wb.refresh=async()=>{};
    await wb.settings();
    assert.equal(submitted,undefined);
    editor.mappings.push({source_entity:"New A",entity_kind:"entity",company:"A"});
    await options.primary_action({enabled:0,business_timezone:"America/Mexico_City",approval_templates:[]});
    assert.equal(submitted.company_mappings.length,2);
    assert.equal(submitted.expected_config_version,"v1");
    assert.equal(original.length,1);
});

test("source composition expands actual field labels and keeps direct parent amounts", () => {
    const parent = "a".repeat(32), child = parent + "/" + "b".repeat(32);
    const wb = { filters: {approval_type:"operating",currency:"MXN"} };
    const nodes = [
        {id:parent,parent:"",label:"管理费用 / Admin",amount:"30.00",direct_amount:"10.00",has_children:true},
        {id:child,parent,label:'平台费 <script>',amount:"20.00",direct_amount:"20.00",has_children:false},
    ];
    let html = api.Workbench.prototype.sourceComposition.call(wb,nodes);
    assert.ok(html.includes(`data-classification-open="${parent}"`));
    assert.ok(html.includes('100.0%'));
    wb.compositionSelection = parent;
    html = api.Workbench.prototype.sourceComposition.call(wb,nodes);
    assert.ok(html.includes('平台费 &lt;script&gt;'));
    assert.ok(!html.includes('<script>'));
    assert.ok(html.includes('66.7%'));
    assert.ok(html.includes('33.3%'));
    assert.ok(html.includes('data-exact="1"'));
    assert.ok(html.includes('No Further Classification'));
    wb.filters.approval_type = "purchase";
    api.Workbench.prototype.sourceComposition.call(wb,nodes);
    assert.equal(wb.compositionSelection, "");
});

test("source classification detail navigation retains filters with an exact leaf scope", async () => {
    let actual;
    const key = "a".repeat(32);
    const wb = {filters:{approval_type:"purchase",company:"MX",category:"procurement",currency:"USD"},
        navigate: (view, filters) => {actual={view,filters};}};
    await api.Workbench.prototype.click.call(wb,{target:{closest:()=>({dataset:{classificationDetails:key,exact:"1"}})}});
    assert.equal(actual.view,"details");
    assert.equal(actual.filters.classification_key,key);
    assert.equal(actual.filters.classification_exact,"1");
    assert.equal(actual.filters.company,"MX");
    assert.equal(actual.filters.category,"");
    assert.equal(actual.filters.currency,"USD");
});
