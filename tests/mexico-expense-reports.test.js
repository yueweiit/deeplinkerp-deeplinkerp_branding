const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const app = path.join(__dirname, "../deeplinkerp_branding");
const nativePath = path.join(__dirname, "../../frappe/frappe/public/js/frappe/views/reports/query_report.js");
const reports = [
	["mexico_subsidiary_expenses", "Mexico Subsidiary Expenses"],
	["mexico_personnel_expenses", "Mexico Personnel Expenses"],
	["mexico_expense_trend", "Mexico Expense Trend"],
];

function harness() {
	const sandbox = { window: {}, DataTable: class {}, __: (text) => text, console };
	const frappe = sandbox.frappe = {
		standard_pages: {}, views: { BaseList: class {} },
		datetime: { get_today: () => "2026-11-16" },
		run_serially: async (tasks) => { for (const task of tasks) await task(); },
		xcall: async () => ({
			approval_forms: [{ id: "expense", label: "费用支出" }, { id: "purchase", label: "采购支出" }],
			categories: [{ id: "tax", label: "税费" }, { id: "personnel", label: "薪酬人事" }],
			subcategories: [{ id: "tax_general", label: "一般税费" }, { id: "salary", label: "工资" }],
		}),
		provide: (namespace) => {
			let current = sandbox;
			for (const part of namespace.split(".")) current = current[part] ||= {};
		},
	};
	vm.createContext(sandbox);
	vm.runInContext(fs.readFileSync(nativePath, "utf8").replace(/^import DataTable .*;$/m, ""), sandbox);
	for (const [slug] of reports) {
		const source = fs.readFileSync(path.join(app, "deeplinkerp_branding/report", slug, `${slug}.js`), "utf8")
			.replace(/\{% include "deeplinkerp_branding\/([^"\n]+)" %\}/g,
				(_, relative) => fs.readFileSync(path.join(app, relative), "utf8"));
		vm.runInContext(source, sandbox);
	}
	return frappe;
}

function control(df) {
	return {
		df, value: df.default || "", data: [], input: df.default || "",
		get_value() { return this.value; },
		async set_value(value) {
			if (df.fieldtype === "Select") {
				const choices = typeof df.options === "string" ? df.options.split("\n") : df.options.map((o) => o.value);
				assert(choices.includes(value), `Select ${df.fieldname} cannot accept ${value}`);
			}
			this.value = value;
			this.set_input(value);
		},
		set_data(data) { this.data = data; },
		set_input(value) { this.input = this.data.find((item) => item.value === value)?.label || value; },
	};
}

for (const [slug, name] of reports) {
	test(`${name} preserves workbench filters through native route handling`, { skip: !fs.existsSync(nativePath) }, async () => {
		const frappe = harness();
		const settings = frappe.query_reports[name];
		const report = Object.create(frappe.views.QueryReport.prototype);
		const payroll = slug === "mexico_personnel_expenses";
		for (const status of ["finance_approved", "approved", "pending", "active", "rejected", "terminated", "withdrawn", "unknown", "all"]) {
			report.filters = settings.filters.map(control);
			const route = {
				from_date: "2026-09-28", to_date: "2026-10-02", basis: payroll ? "period" : "planned", company: "Subsidiary A",
				category: payroll ? "personnel" : "tax", subcategory: payroll ? "salary" : "tax_general",
				approval_type: "expense", keyword: "reference 123", issue: "stale", reporting_currency: "USD", status,
			};
			frappe.route_options = route;
			await report.set_route_filters();
			report._no_refresh = true;
			await settings.onload(report);
			assert.deepEqual(JSON.parse(JSON.stringify(report.get_filter_values())), route);
			assert.equal(frappe.route_options, null);
			assert.equal(report.get_filter("approval_type").input, "费用支出");
			assert.equal(report.get_filter("category").input, payroll ? "personnel" : "税费");
			assert.equal(report.get_filter("subcategory").input, payroll ? "工资" : "一般税费");
			assert.equal(report._no_refresh, true);
		}
	});
}

test("native payroll report enforces its period and personnel scope", { skip: !fs.existsSync(nativePath) }, async () => {
	const frappe = harness();
	const settings = frappe.query_reports["Mexico Personnel Expenses"];
	const report = Object.create(frappe.views.QueryReport.prototype);
	report.filters = settings.filters.map(control);
	await report.set_route_filters({ basis: "request", category: "tax" });
	await settings.onload(report);
	assert.equal(report.get_filter_value("basis"), "period");
	assert.equal(report.get_filter_value("category"), "personnel");
});


test("legacy native month links become exact full-month date ranges", {skip: !fs.existsSync(nativePath)}, async () => {
    const frappe=harness(), settings=frappe.query_reports['Mexico Expense Trend'];
    const report=Object.create(frappe.views.QueryReport.prototype);
    report.filters=settings.filters.map(control);
    await report.set_route_filters({month:'2024-02'});
    await settings.onload(report);
    assert.equal(report.get_filter_value('from_date'),'2024-02-01');
    assert.equal(report.get_filter_value('to_date'),'2024-02-29');
    assert(!report.get_filter_values().month);
});

test("native reports migrate legacy currency links and prefer explicit reporting currency", {skip: !fs.existsSync(nativePath)}, async () => {
    for (const [route, expected] of [[{currency:'USD'},'USD'],[{currency:'USD',reporting_currency:'CNY'},'CNY'],[{},'MXN']]) {
        const frappe=harness(), settings=frappe.query_reports['Mexico Expense Trend'];
        const report=Object.create(frappe.views.QueryReport.prototype);
        report.filters=settings.filters.map(control);
        await report.set_route_filters(route);
        await settings.onload(report);
        assert.equal(report.get_filter_value('reporting_currency'),expected);
        assert(!report.get_filter_values().currency);
    }
});

test('all native reports format exact currency strings without coercing them to Number', {skip: !fs.existsSync(nativePath)}, () => {
    const frappe = harness();
    for (const [,name] of reports) {
        const settings = frappe.query_reports[name];
        const result = settings.formatter('76399999999999.24',0,{fieldtype:'Currency'},{currency:'MXN'},()=>{throw new Error('float formatter invoked');});
        assert.equal(result,'MXN 76,399,999,999,999.24');
        assert.equal(settings.formatter('A',0,{fieldtype:'Data'},{currency:'MXN'},value=>value),'A');
    }
});
