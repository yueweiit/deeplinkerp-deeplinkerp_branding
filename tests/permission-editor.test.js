const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "deeplinkerp_branding/public/js/permission_editor.js"), "utf8");

function load() {
	const handlers = {}, translations = {}, listeners = [];
	const context = vm.createContext({ window: { cur_frm: { doctype: "User" }, addEventListener: (...args) => listeners.push(args) }, frappe: { ui: { form: {
		on: (doctype, events) => {
			for (const [event, handler] of Object.entries(events)) {
				handlers[doctype] ||= {};
				(handlers[doctype][event] ||= []).push(handler);
			}
		},
	} } }, __: (key) => translations[key] || key });
	vm.runInContext(source, context);
	return { api: context.window.DeepLinkERPPermissionEditor, handlers, translations, context, listeners };
}
const plain = (value) => JSON.parse(JSON.stringify(value));

test("template preview mirrors the native union, including removal of individually assigned roles", () => {
	const { api } = load();
	const current = ["Sales User", "System Manager"];
	const profiles = [{ roles: [{ role: "Sales User" }, { role: "Purchase User" }] },
		{ roles: [{ role: "Purchase User" }, { role: "Stock User" }] }];
	const before = JSON.stringify({ current, profiles });
	assert.deepEqual(plain(api.profile_diff(current, profiles)), {
		added: ["Purchase User", "Stock User"], removed: ["System Manager"], retained: ["Sales User"],
	});
	assert.equal(JSON.stringify({ current, profiles }), before);
	assert.deepEqual(plain(api.profile_diff(current, [{ roles: [] }])), { added: [], removed: current, retained: [] });
});

test("unknown identities remain unclassified and do not inherit privileges by name", () => {
	const { api } = load();
	const unknown = api.describe("roles", "Custom Sales System Manager");
	assert.equal(unknown.group, "other");
	assert.equal(unknown.sensitive, undefined);
	assert.match(unknown.description, /not documented/);
	for (const name of ["constructor", "toString", "__proto__"]) assert.equal(api.describe("roles", name).group, "other");
	assert.match(api.describe("roles", "Sales Production Release Operator").description, /actual binding/);
	assert.equal(api.describe("modules", "Crm Integration").label, "CRM integration");
});

test("filters search both internal identities and localized purposes without changing selection", () => {
	const { api, translations } = load();
	translations[api.roles["Sales User"].description] = "日常销售单据与客户跟进";
	const config = api.describe("roles", "Sales User");
	assert.equal(api.matches("Sales User", config, " 跟进 ", "sales", true, true), true);
	assert.equal(api.matches("Sales User", config, "SALES", "", false, false), true);
	assert.equal(api.matches("Sales User", config, "", "finance", false, true), false);
	assert.equal(api.matches("Sales User", config, "", "", true, false), false);
});

test("loading the shared script for multiple doctypes registers each enhancement only once", () => {
	const { api, context, handlers, listeners } = load();
	assert.equal(handlers["Role Profile"], undefined);
	assert.equal(handlers["Module Profile"], undefined);
	vm.runInContext(source, context);
	assert.equal(context.window.DeepLinkERPPermissionEditor, api);
	assert.equal(handlers.User.refresh.length, 1);
	context.window.cur_frm.doctype = "Role Profile";
	const native = () => {};
	context.frappe.ui.form.on("Role Profile", { refresh: native });
	vm.runInContext(source, context);
	vm.runInContext(source, context);
	assert.equal(handlers["Role Profile"].refresh[0], native);
	assert.equal(handlers["Role Profile"].refresh.length, 2);
	assert.equal(listeners.length, 1);
	assert.equal(listeners[0][0], "keydown");
	assert.equal(listeners[0][2], true);
});

test("users without native role editing access receive no added editor or reads", () => {
	const { api } = load();
	assert.doesNotThrow(() => api.refresh({ doctype: "User", can_edit_roles: false }));
	assert.doesNotThrow(() => api.refresh({ doctype: "User", can_edit_roles: true, is_new: () => true }));
});

test("profile roles need field-level read permission; a masked table is never treated as an empty template", async () => {
	const { api, context } = load();
	let reads = 0;
	Object.assign(context.frappe, {
		model: { with_doctype: async () => {} }, session: { user: "manager@example.invalid" },
		meta: { get_docfield: () => ({ permlevel: 1 }) }, perm: { has_perm: () => false },
		xcall: async () => { reads++; return { roles: [] }; },
		db: { get_doc: () => { throw new Error("Template reads must not sync browser drafts"); } },
	});
	await assert.rejects(api.read_profiles(["Sales"]), /not readable/);
	assert.equal(reads, 0);
	context.frappe.perm.has_perm = () => true;
	assert.deepEqual(plain(await api.read_profiles(["Empty"])), [{ roles: [] }]);
	context.frappe.xcall = async () => ({ roles: [{}] });
	await assert.rejects(api.read_profiles(["Masked child"]), /Incomplete/);
});
