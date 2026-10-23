const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../deeplinkerp_branding/public/js/form_grid_insert.js"), "utf8");
const apps = path.resolve(__dirname, "../..");
// Exercise the actual native index renumbering and Journal Entry add handler.
const create = fs.readFileSync(path.join(apps, "frappe/frappe/public/js/frappe/model/create_new.js"), "utf8");
const journal = fs.readFileSync(path.join(apps, "erpnext/erpnext/accounts/doctype/journal_entry/journal_entry.js"), "utf8");

function harness(doctype = "Journal Entry") {
	const events = {}, messages = [], alerts = [], rows = new Map(), parents = new Map();
	const buttonHandlers = {};
	const button = { length: 1, removed: false, prependTo() { this.length = 1; this.removed = false; return this; },
		remove() { this.length = 0; this.removed = true; return this; },
		on(name, fn) { buttonHandlers[name] = fn; return this; }, attr() { return this; } };
	const $ = value => typeof value === "string" && value.startsWith("<button")
		? button : { on(name, fn) { events[name] = fn; } };
	$.each = (values, fn) => values.forEach((value, index) => fn(index, value));
	const translate = (s, args = []) => s.replace(/\{(\d+)\}/g, (_, n) => args[n]);
	const frm = { doc: { doctype, name: "draft", accounts: [], difference: 35 }, cscript: {},
		dirty() { this.changed = true; }, toolbar: {} };
	let count = 0, frozen = 0;
	const frappe = { ui: { form: { close_grid_form() {} } },
		perm: { get_field_display_status: () => "Write" }, utils: { escape_html: s => s },
		after_ajax: async () => {}, msgprint: value => messages.push(value), show_alert: value => alerts.push(value),
		dom: { freeze() { frozen++; }, unfreeze() { frozen--; } },
		get_doc: (_dt, name) => rows.get(name), model: {
			get_new_doc(dt, parent, field) {
				const row = { doctype: dt, name: `ROW-${++count}` };
				parent[field].push(row); rows.set(row.name, row); parents.set(row.name, { parent, field }); return row;
			},
			clear_doc(_dt, name) {
				const ref = parents.get(name);
				if (!ref) return;
				ref.parent[ref.field] = ref.parent[ref.field].filter(row => row.name !== name);
				ref.parent[ref.field].forEach((row, index) => { row.idx = index + 1; });
				rows.delete(name); parents.delete(name);
			},
		},
	};
	const grid = { frm, df: { fieldtype: "Table", fieldname: "accounts", options: "Journal Entry Account" },
		is_editable: () => true, get_data: () => frm.doc.accounts,
		filter: {}, grid_rows_by_docname: {}, allow_on_grid_editing: () => true,
		grid_pagination: { page_length: 2, page_index: 1, go_to_page(page) { this.page_index = page; } },
		refresh() {
			for (const row of frm.doc.accounts) this.grid_rows_by_docname[row.name] = {
				toggle_editable_row() { grid.focused = row.name; },
				wrapper: [{ scrollIntoView() {} }],
			};
		},
	};
	const context = vm.createContext({ window: {}, document: {}, $, frappe, __: translate, setTimeout,
		cur_frm: frm, cint: Math.trunc,
		erpnext: { accounts: { dimensions: { copy_dimension_from_first_row(_frm, _dt, name) {
			rows.get(name).cost_center = "Native default";
		} } } },
	});
	vm.runInContext(`frappe.model.add_child = ${create.split("add_child: ")[1].split("\n\tcopy_doc:")[0].trim().replace(/,$/, "")}`, context);
	const addStart = journal.indexOf("\taccounts_add(doc,");
	vm.runInContext(`window.native = {${journal.slice(addStart, journal.indexOf("\n};", addStart))}}`, context);
	frm.cscript.update_totals = doc => {
		doc.total_debit = doc.accounts.reduce((sum, row) => sum + (row.debit || 0), 0);
		doc.total_credit = doc.accounts.reduce((sum, row) => sum + (row.credit || 0), 0);
		doc.difference = doc.total_debit - doc.total_credit;
	};
	frm.script_manager = { async trigger(event, dt, name) {
		assert.equal(event, "accounts_add");
		await Promise.resolve();
		return context.window.native.accounts_add.call({ frm }, frm.doc, dt, name);
	} };
	for (let i = 0; i < 3; i++) Object.assign(frappe.model.add_child(frm.doc, grid.df.options, "accounts"), {
		account: `Original ${i}`, party_type: "Customer", party: "Customer A", exchange_rate: 7,
		user_remark: `Summary ${i}`, debit: i === 0 ? 35 : 0, credit: 0,
	});
	vm.runInContext(source, context);
	return { api: context.window.DeepLinkERPGridInsert, grid, frm, frappe, messages, alerts,
		frozen: () => frozen, events, button, buttonHandlers };
}

test("inserts after the stable source row; preserves original identity, values and order", async () => {
	const h = harness(), originals = h.frm.doc.accounts.map(row => ({ ...row }));
	const row = await h.api.insert_after(h.grid, h.frm.doc.accounts[0]);
	assert.deepEqual(h.frm.doc.accounts.map(item => item.name), [originals[0].name, row.name, originals[1].name, originals[2].name]);
	assert.deepEqual(h.frm.doc.accounts.map(item => item.idx), [1, 2, 3, 4]);
	assert.equal(h.frm.doc.accounts[0].debit, 35);
	assert.equal(h.frm.doc.accounts[2].account, originals[1].account);
	assert.equal(h.frm.changed, true);
	assert.equal(h.grid.focused, row.name);
});

test("journal insertion clears native party/difference defaults and retains summary and native dimensions", async () => {
	const h = harness(), row = await h.api.insert_after(h.grid, h.frm.doc.accounts[1]);
	for (const field of ["account", "account_type", "party", "party_type", "account_currency"]) assert.equal(row[field], "");
	for (const field of ["debit", "credit", "debit_in_account_currency", "credit_in_account_currency"]) assert.equal(row[field], 0);
	assert.equal(row.exchange_rate, 1);
	assert.equal(row.user_remark, "Summary 1");
	assert.equal(row.cost_center, "Native default");
	assert.equal(h.frm.doc.difference, 35);
});

test("other documents keep their native add defaults and calculations", async () => {
	const h = harness("Purchase Order");
	h.frm.script_manager.trigger = async (_event, _dt, name) => {
		Object.assign(h.frappe.get_doc(null, name), { qty: 1, rate: 42, warehouse: "Stores" });
	};
	const row = await h.api.insert_after(h.grid, h.frm.doc.accounts[0]);
	assert.equal(row.qty, 1); assert.equal(row.rate, 42); assert.equal(row.warehouse, "Stores");
	assert.equal(row.debit, undefined);
});

test("filtered insertion uses the real index, reveals its page and clears only local filters", async () => {
	const h = harness(); h.grid.filter = { account: { value: "Original 1" } };
	const row = await h.api.insert_after(h.grid, h.frm.doc.accounts[1]);
	assert.equal(row.idx, 3); assert.equal(h.grid.grid_pagination.page_index, 2);
	assert.equal(Object.keys(h.grid.filter).length, 0); assert.equal(h.alerts.length, 1);
});

test("active input is validated before summary inheritance and index lookup", async () => {
	const h = harness(), anchor = h.frm.doc.accounts[1];
	const row = await h.api.insert_after(h.grid, anchor, { get_status: () => "Write", get_input_value: () => "Edited summary",
		async parse_validate_and_set_in_model(value) { await Promise.resolve(); anchor.user_remark = value; }
	});
	assert.equal(row.user_remark, "Edited summary");
});

for (const reason of ["readonly", "cannot_add", "field_cannot_add", "save_disabled", "toolbar_readonly", "permission", "saving", "dialog", "non_table", "selected_rows"]) {
	test(`does not insert when ${reason}`, async () => {
		const h = harness(), grid = h.grid, anchor = h.frm.doc.accounts[0];
		if (reason === "readonly") grid.is_editable = () => false;
		if (reason === "cannot_add") grid.cannot_add_rows = true;
		if (reason === "field_cannot_add") grid.df.cannot_add_rows = true;
		if (reason === "save_disabled") h.frm.save_disabled = true;
		if (reason === "toolbar_readonly") h.frm.toolbar.read_only = true;
		if (reason === "permission") h.frappe.perm.get_field_display_status = () => "Read";
		if (reason === "saving") h.frappe.ui.form.is_saving = true;
		if (reason === "dialog") delete grid.frm;
		if (reason === "non_table") grid.df.fieldtype = "Table MultiSelect";
		if (reason === "selected_rows") grid.get_selected_children = () => [anchor];
		assert.equal(await h.api.insert_after(grid, anchor), undefined);
		assert.equal(h.frm.doc.accounts.length, 3);
	});
}

test("double click inserts only once while native hooks are pending", async () => {
	const h = harness(), anchor = h.frm.doc.accounts[0]; let release;
	h.frm.script_manager.trigger = () => new Promise(resolve => { release = resolve; });
	const first = h.api.insert_after(h.grid, anchor);
	await Promise.resolve(); await Promise.resolve();
	await h.api.insert_after(h.grid, anchor);
	assert.equal(h.frm.doc.accounts.length, 4);
	release(); await first; assert.equal(h.frozen(), 0);
});

test("deleted anchor, permission change or navigation during validation cannot insert elsewhere", async () => {
	for (const change of [h => h.frm.doc.accounts.shift(), h => h.grid.cannot_add_rows = true,
		h => h.frm.doc = { accounts: [], doctype: "Other" }]) {
		const h = harness(), parent = h.frm.doc, anchor = parent.accounts[0];
		await h.api.insert_after(h.grid, anchor, { get_status: () => "Write", get_input_value: () => "x",
			async parse_validate_and_set_in_model() { change(h); } });
		assert.ok(parent.accounts.length <= 3); assert.equal(h.frozen(), 0);
	}
});

test("validation errors preserve existing input and do not add a row", async () => {
	const h = harness();
	await h.api.insert_after(h.grid, h.frm.doc.accounts[0], { get_status: () => "Write", inside_change_event: true });
	assert.equal(h.frm.doc.accounts.length, 3); assert.equal(h.messages.length, 1); assert.equal(h.frozen(), 0);
});

test("failed native add hook rolls back an invisible filtered row and permits one clean retry", async () => {
	const h = harness(), anchor = h.frm.doc.accounts[0];
	h.grid.filter = { account: { value: "Original 0" } };
	h.frm.script_manager.trigger = async () => { throw new Error("Business rule"); };
	await h.api.insert_after(h.grid, anchor);
	assert.deepEqual(h.frm.doc.accounts.map(row => row.idx), [1, 2, 3]);
	assert.equal(h.frappe.get_doc(null, "ROW-4"), undefined);
	assert.match(h.messages[0].message, /Business rule.*新增行已撤回/s);
	assert.equal(h.frozen(), 0);
	h.frm.script_manager.trigger = async () => {};
	await h.api.insert_after(h.grid, anchor);
	assert.equal(h.frm.doc.accounts.length, 4);
	assert.deepEqual(h.frm.doc.accounts.map(row => row.idx), [1, 2, 3, 4]);
});

test("failed add hook cannot leave a new row behind when the grid refresh also fails", async () => {
	const h = harness();
	h.frm.script_manager.trigger = async () => { throw new Error("Business rule"); };
	h.grid.refresh = () => { throw new Error("Grid refresh failed"); };
	await h.api.insert_after(h.grid, h.frm.doc.accounts[0]);
	assert.deepEqual(h.frm.doc.accounts.map(row => row.idx), [1, 2, 3]);
	assert.match(h.messages[0].message, /新增行已撤回/);
	assert.equal(h.frozen(), 0);
});

test("keyboard click waits for the focused control's native asynchronous validation", async () => {
	const h = harness(), anchor = h.frm.doc.accounts[0], gridEvents = {};
	h.grid.wrapper = { on(name, _selector, fn) { gridEvents[name] = fn; }, find() { return { text() {} }; } };
	const row = { doc: anchor, grid: h.grid,
		row: { children() { return { length: 0, remove() {} }; }, addClass() {} } };
	h.api.decorate(row);
	let finishValidation;
	const control = { get_status: () => "Write", get_input_value: () => "Keyboard summary",
		validate_and_set_in_model(value) {
			if (anchor.user_remark === value) return Promise.resolve();
			return new Promise(resolve => { finishValidation = () => { anchor.user_remark = value; resolve(); }; });
		},
		parse_validate_and_set_in_model(value) { return this.validate_and_set_in_model(value); } };
	const input = { closest() { return { fieldobj: control }; } };
	gridEvents["focusin.dlp_grid_insert"]({ target: input });
	const nativeValidation = control.parse_validate_and_set_in_model("Keyboard summary");
	h.buttonHandlers.focusin({ relatedTarget: input });
	const insertion = h.buttonHandlers.click({ stopPropagation() {} });
	await Promise.resolve(); await Promise.resolve();
	assert.equal(h.frm.doc.accounts.length, 3);
	finishValidation(); await nativeValidation; await insertion;
	assert.equal(h.frm.doc.accounts.length, 4);
	assert.equal(h.frm.doc.accounts[1].user_remark, "Keyboard summary");
});

test("selection hides the row button and disables insertion until cleared", async () => {
	const h = harness(), anchor = h.frm.doc.accounts[0], gridEvents = {};
	h.grid.get_selected_children = () => h.frm.doc.accounts.filter(row => row.__checked);
	h.grid.wrapper = { on(name, _selector, fn) { gridEvents[name] = fn; }, find() { return { text() {} }; } };
	h.button.length = 0;
	const row = { doc: anchor, grid: h.grid,
		row: { children() { return h.button; }, addClass() {} } };
	h.grid.grid_rows = [row];
	h.api.decorate(row);
	assert.equal(h.button.length, 1);
	anchor.__checked = true;
	gridEvents["click.dlp_grid_insert"]();
	assert.equal(h.button.removed, true);
	assert.equal(h.api.editable(h.grid), false);
	assert.equal(await h.api.insert_after(h.grid, anchor), undefined);
	anchor.__checked = false;
	gridEvents["click.dlp_grid_insert"]();
	assert.equal(h.button.removed, false);
	assert.equal(h.api.editable(h.grid), true);
	assert.ok(await h.api.insert_after(h.grid, anchor));
});
