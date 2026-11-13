const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function fixture() {
	const handlers = {};
	const centers = [
		{ name: "OLD", company: "A" },
		{ name: "DEFAULT", company: "B", disabled: 0, is_group: 0 },
		{ name: "MANUAL", company: "B" },
	];
	const doc = { doctype: "Sales Order", name: "SO", docstatus: 0, company: "B",
		cost_center: "OLD", items: [], taxes: [], currency: "MXN", customer: "CUSTOMER" };
	const writes = [];
	const calls = [];
	const db = {
		async get_value(doctype, company, field) {
			calls.push([doctype, company, field]);
			return { message: { cost_center: "DEFAULT" } };
		},
		async get_list(doctype, options) {
			calls.push([doctype, options]);
			return centers.filter(center => options.filters.name[1].includes(center.name));
		},
	};
	const frm = { doc, async set_value(field, value) {
		writes.push([doc.name, field, value]);
		doc[field] = value;
		// The native header event only copies values into empty item rows.
		if (field === "cost_center" && value) {
			for (const row of doc.items) if (!row.cost_center) row.cost_center = value;
		}
	} };
	const frappe = {
		ui: { form: { on(doctype, events) { assert.equal(doctype, "Sales Order"); Object.assign(handlers, events); } } },
		db,
		model: { async set_value(doctype, name, field, value) {
			const row = [...doc.items, ...doc.taxes].find(row => row.name === name && row.doctype === doctype);
			assert.ok(row);
			writes.push([name, field, value]); row[field] = value;
		} },
	};
	vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../../public/js/sales_order_company.js"), "utf8"), { frappe });
	const add = (name, cost_center, table = "items") => {
		const row = { name, doctype: table === "items" ? "Sales Order Item" : "Sales Taxes and Charges", cost_center };
		doc[table].push(row);
		return row;
	};
	return { handlers, db, doc, frm, writes, calls, centers, add };
}

function deferred() {
	let resolve;
	const promise = new Promise(done => { resolve = done; });
	return { promise, resolve };
}

test("company change repairs foreign header, item and tax centers but preserves same-company choices", async () => {
	const h = fixture();
	const wrong = h.add("wrong", "OLD");
	const manual = h.add("manual", "MANUAL");
	const tax = h.add("tax", "OLD", "taxes");
	await h.handlers.company(h.frm);
	assert.equal(h.doc.cost_center, "DEFAULT");
	assert.equal(wrong.cost_center, "DEFAULT");
	assert.equal(tax.cost_center, "DEFAULT");
	assert.equal(manual.cost_center, "MANUAL");
	assert.equal(h.doc.currency, "MXN");
	assert.equal(h.doc.customer, "CUSTOMER");
	assert.equal(h.calls.length, 2);
});

test("validation also repairs an already open draft and returns the pending promise", async () => {
	const h = fixture(); h.doc.cost_center = "";
	const row = h.add("item", "OLD");
	const waiting = deferred(); h.db.get_value = () => waiting.promise;
	const pending = h.handlers.validate(h.frm);
	assert.equal(typeof pending.then, "function");
	assert.equal(row.cost_center, "OLD");
	waiting.resolve({ message: { cost_center: "DEFAULT" } });
	await pending;
	assert.equal(row.cost_center, "DEFAULT");
	assert.equal(h.doc.cost_center, "");
});

test("same-company centers, including explicit manual selections, remain unchanged", async () => {
	const h = fixture(); h.doc.cost_center = "MANUAL"; h.add("item", "MANUAL");
	await h.handlers.company(h.frm);
	assert.deepEqual(h.writes, []);
});

test("a valid manual header center takes precedence over the company default for foreign rows", async () => {
	const h = fixture(); h.doc.cost_center = "MANUAL";
	const row = h.add("wrong", "OLD"); const valid = h.add("valid", "DEFAULT");
	await h.handlers.company(h.frm);
	assert.equal(h.doc.cost_center, "MANUAL");
	assert.equal(row.cost_center, "MANUAL");
	assert.equal(valid.cost_center, "DEFAULT");
});

test("submitted or cancelled orders and missing companies are not changed or queried", async () => {
	for (const status of [1, 2]) {
		const h = fixture(); h.doc.docstatus = status;
		await h.handlers.company(h.frm); await h.handlers.validate(h.frm);
		assert.deepEqual(h.calls, []); assert.deepEqual(h.writes, []);
	}
	const h = fixture(); h.doc.company = "";
	await h.handlers.company(h.frm); assert.deepEqual(h.calls, []);
});

test("empty cost centers retain native defaults and do not need network calls", async () => {
	const h = fixture(); h.doc.cost_center = ""; h.add("item", "");
	await h.handlers.validate(h.frm);
	assert.deepEqual(h.calls, []); assert.deepEqual(h.writes, []);
});

test("missing, foreign, disabled or group defaults only clear confirmed foreign centers", async () => {
	for (const changes of [null, { company: "A" }, { disabled: 1 }, { is_group: 1 }]) {
		const h = fixture();
		if (changes === null) h.centers.splice(1, 1);
		else Object.assign(h.centers[1], changes);
		const row = h.add("item", "OLD"); const valid = h.add("manual", "MANUAL");
		await h.handlers.company(h.frm);
		assert.equal(h.doc.cost_center, ""); assert.equal(row.cost_center, "");
		assert.equal(valid.cost_center, "MANUAL");
	}
});

test("unreadable or unknown center ownership is preserved for native validation", async () => {
	const h = fixture(); h.doc.cost_center = "UNKNOWN"; h.add("item", "UNKNOWN");
	await h.handlers.company(h.frm);
	assert.deepEqual(h.writes, []);
});

test("late replies do not overwrite a later company even when switched back", async () => {
	const h = fixture(); const waiting = deferred();
	h.db.get_value = () => waiting.promise;
	const first = h.handlers.company(h.frm);
	h.doc.company = "A";
	const second = h.handlers.company(h.frm);
	h.doc.company = "B";
	const third = h.handlers.company(h.frm);
	waiting.resolve({ message: { cost_center: "DEFAULT" } });
	await Promise.all([first, second, third]);
	assert.deepEqual(h.writes, [["SO", "cost_center", "DEFAULT"]]);
});

test("user edits and removed rows made while querying are not overwritten", async () => {
	const h = fixture(); const removed = h.add("removed", "OLD");
	const edited = h.add("edited", "OLD");
	const waiting = deferred(); const read = h.db.get_list;
	h.db.get_list = async (...args) => { await waiting.promise; return read(...args); };
	const pending = h.handlers.company(h.frm);
	await Promise.resolve();
	h.doc.cost_center = "MANUAL"; edited.cost_center = "MANUAL"; h.doc.items.splice(0, 1);
	waiting.resolve(); await pending;
	assert.deepEqual(h.writes, []); assert.equal(removed.cost_center, "OLD");
});

test("document navigation or submission while querying invalidates the reply", async () => {
	for (const change of [h => { h.frm.doc = { ...h.doc }; }, h => { h.doc.docstatus = 1; }]) {
		const h = fixture(); const waiting = deferred(); h.db.get_value = () => waiting.promise;
		const pending = h.handlers.company(h.frm); change(h);
		waiting.resolve({ message: { cost_center: "DEFAULT" } }); await pending;
		assert.deepEqual(h.writes, []);
	}
});

test("lookup failures reject validation without changing financial dimensions", async () => {
	const h = fixture(); h.db.get_list = async () => { throw new Error("Permission denied"); };
	await assert.rejects(h.handlers.validate(h.frm), /Permission denied/);
	assert.deepEqual(h.writes, []);
});
