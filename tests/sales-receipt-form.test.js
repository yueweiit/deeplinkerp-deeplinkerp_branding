const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const service = require("../deeplinkerp_branding/public/js/sales_receipt_form.js");

function fixture() {
	class Box {
		constructor() { this.nodes = new Map(); this.props = {}; this.events = {}; this.contents = []; }
		find(selector) { if (!this.nodes.has(selector)) this.nodes.set(selector, new Box()); return this.nodes.get(selector); }
		prop(key, value) { if (arguments.length === 1) return this.props[key]; this.props[key] = value; return this; }
		attr() { return this; }
		html(value) { this.markup = value; this.nodes.clear(); this.contents = []; return this; }
		text(value) { this.textValue = value; return this; }
		on(event, callback) { this.events[event] = callback; return this; }
		off() { return this; }
		appendTo(parent) { parent.contents.push(this); return this; }
	}
	const dialog = { $wrapper: new Box(), hide() { dialog.hidden = true; } };
	const panel = dialog.$wrapper.find(".dlp-sales-receipt-editor"); panel.props.hidden = true;
	const requests = [], controls = {}, callbacks = [], attachmentManagers = [], events = new Map();
	const shared = {
		async loadMetadata() {},
		requestId: () => "12345678-1234-1234-1234-123456789abc",
		retryToken: makeId => {
			let token = null, savedArgs = null;
			return { pending: () => savedArgs, succeeded() { token = savedArgs = null; },
				forPayload(args) { const signature = JSON.stringify(args); if (savedArgs) assert.equal(signature, JSON.stringify(savedArgs)); else { savedArgs = JSON.parse(signature); token = makeId(); } return token; } };
		},
		disposeControls(list) { for (const item of list.splice(0)) item.disposed = true; },
		async drawerInput(drawer, holder, df, value, touched) {
			const control = { df, value, get_value() { return this.value; }, refresh() {}, touch(value) { this.value = value; touched(); } };
			controls[df.fieldname] = control; drawer.controls.push(control); return control;
		},
		async mountAttachments(drawer) {
			const manager = { files: [], waiting: false, stopped: false, bound: false,
				ready() { return !this.waiting; }, snapshot() { return { files: this.files }; },
				args() { return this.files.length ? { attachment_session: "session", attachments: this.files.map(file => file.name) } : {}; },
				refresh() {}, stop() { this.stopped = true; }, committed() { this.bound = true; this.files = []; },
			};
			drawer.attachments = manager; drawer.beforeClose = async () => { manager.discarded = true; return true; }; attachmentManagers.push(manager);
		},
	};
	const root = { module: { exports: {} }, $, console,
		DeepLinkERPPurchasePayments: shared, addEventListener: (key, fn) => events.set(key, fn), removeEventListener: key => events.delete(key),
		frappe: { session: { user: "qa" }, boot: { sitename: "qa" },
			call: async request => { requests.push(request); return { message: await api.respond(request) }; },
			confirm: (message, yes, no) => { callbacks.push(message); (api.confirm === false ? no : yes)(); },
		},
	};
	function $(html) { const box = new Box(); box.html(html); return box; }
	vm.runInNewContext(fs.readFileSync(require.resolve("../deeplinkerp_branding/public/js/sales_receipt_form.js"), "utf8"), root);
	const projection = (name = null) => ({ document: { name, modified: name ? "p1" : "", docstatus: 0, company: "C", customer: "Customer", currency: "MXN", amount: 25,
		posting_date: "2026-10-26", bank_account: "Bank", mode_of_payment: "", reference_no: "", reference_date: "", remarks: "" },
		order_modified: "s1", can_submit: Boolean(name), editable_fields: ["amount", "posting_date", "bank_account", "mode_of_payment", "reference_no", "reference_date", "remarks"], drafts: [], attachments: [] });
	const api = { requests, controls, callbacks, panel, dialog, attachmentManagers, events, projection, respond: request => projection(request.args.payment_entry || null),
		saved: [], advanced: [], confirm: true };
	api.editor = root.module.exports.mount({ dialog, salesOrder: "SO-1", onSaved: async doc => { api.saved.push(doc.name); if (api.refreshError) throw api.refreshError; }, openAdvanced: async name => api.advanced.push(name) });
	return api;
}

test("request validation preserves receipt identity, native version and multiple attachment names", () => {
	const projection = { document: { name: "PE-1", modified: "p1" }, order_modified: "s1" };
	const args = service.saveArguments("SO-1", projection, { amount: "1.25", posting_date: "2026-10-26", bank_account: "Bank" }, false,
		{ attachment_session: "session", attachments: ["FILE-1", "FILE-2"] });
	assert.equal(args.payment_entry, "PE-1"); assert.equal(args.expected_modified, "p1"); assert.equal(args.order_modified, "s1");
	assert.deepEqual(args.attachments, ["FILE-1", "FILE-2"]);
	for (const amount of [0, -1, "NaN", "Infinity"]) assert.throws(() => service.saveArguments("SO", projection, { amount }, false));
});

test("draft choices escape content and do not silently select one of multiple receipts", () => {
	const html = service.choicesHTML([{ name: '<bad>', amount: 2, currency: "MXN" }]);
	assert.match(html, /继续编辑草稿/); assert.match(html, /&lt;bad&gt;/); assert.doesNotMatch(html, /<bad>/);
});

test("opening reads context only, keeps order detail open and does not save a payment", async () => {
	const h = fixture(); await h.editor.open();
	assert.equal(h.requests.length, 1); assert.match(h.requests[0].method, /get_receipt_context$/);
	assert.equal(h.dialog.hidden, undefined); assert.equal(h.controls.amount.value, 25); assert.equal(h.panel.props.hidden, false);
});

test("saved draft stays in order detail, accepts multiple files and updates the same native receipt next time", async () => {
	const h = fixture(); await h.editor.open(); const uploads = h.attachmentManagers.at(-1); uploads.files = [{ name: "F1" }, { name: "F2" }];
	h.respond = () => h.projection("PE-1"); await h.editor.save();
	assert.equal(h.dialog.hidden, undefined); assert.equal(h.saved[0], "PE-1"); assert.equal(uploads.bound, true);
	assert.equal(h.requests[1].args.attachments.length, 2); assert.equal(h.requests[1].args.payment_entry, null);
	h.controls.amount.touch(10); await h.editor.save();
	assert.equal(h.requests[2].args.payment_entry, "PE-1"); assert.equal(h.requests[2].args.expected_modified, "p1");
});

test("unfinished uploads prevent save and repeated clicks while request is pending never dispatch twice", async () => {
	const h = fixture(); await h.editor.open(); h.attachmentManagers.at(-1).waiting = true; await h.editor.save();
	assert.equal(h.requests.length, 1); h.attachmentManagers.at(-1).waiting = false;
	let finish; h.respond = () => new Promise(resolve => { finish = resolve; });
	const pending = h.editor.save(); await Promise.resolve(); await h.editor.save(); assert.equal(h.requests.length, 2);
	finish(h.projection("PE")); await pending;
});

test("uncertain response retries exact original fields, request ID and attachments", async () => {
	const h = fixture(); await h.editor.open(); h.attachmentManagers.at(-1).files = [{ name: "F1" }];
	h.respond = () => { throw new Error("network"); }; await h.editor.save(); const original = JSON.stringify(h.requests[1].args);
	h.controls.amount.value = 99; h.respond = () => h.projection("PE"); await h.editor.save();
	assert.equal(JSON.stringify(h.requests[2].args), original);
});

test("server validation failure permits correction", async () => {
	const h = fixture(); await h.editor.open(); h.respond = () => ({ failed: true, error: "Invalid account" }); await h.editor.save();
	h.controls.amount.touch(12); h.respond = () => h.projection("PE"); await h.editor.save(); assert.equal(h.requests[2].args.values.amount, 12);
});

test("definite date validation failure unlocks fields and sends corrected values next time", async () => {
	const h = fixture(); await h.editor.open(); h.controls.reference_date.touch("2026-02-31");
	h.respond = () => ({ failed: true, error: "Invalid date" }); await h.editor.save();
	assert.equal(h.controls.reference_date.df.read_only, false);
	assert.equal(h.controls.amount.df.read_only, false);
	h.controls.reference_date.touch("2026-10-26"); h.respond = () => h.projection("PE-1");
	await h.editor.save();
	assert.equal(h.requests.at(-1).args.values.reference_date, "2026-10-26");
	assert.equal(h.saved[0], "PE-1");
});

test("acknowledged receipt requiring review keeps the original retry and blocks changed values", async () => {
	const h = fixture(); await h.editor.open();
	h.respond = () => ({ failed: true, requires_review: true, error: "Review existing receipt" });
	await h.editor.save(); const original = JSON.stringify(h.requests.at(-1).args);
	assert.equal(h.controls.amount.df.read_only, true);
	h.controls.amount.value = 99; h.respond = () => h.projection("PE-1"); await h.editor.save();
	assert.equal(JSON.stringify(h.requests.at(-1).args), original);
});

test("pending save can be retried after reopening into a multiple-draft chooser or a read failure", async () => {
	for (const failedRead of [false, true]) {
		const h = fixture(); await h.editor.open();
		h.respond = () => { throw new Error("network"); }; await h.editor.save();
		const original = JSON.stringify(h.requests.at(-1).args);
		await h.editor.close();
		h.respond = () => { if (failedRead) throw new Error("read failed"); return { document: null, drafts: [{ name: "PE-1" }, { name: "PE-2" }] }; };
		await h.editor.open();
		h.respond = () => h.projection("PE-1"); await h.editor.save();
		assert.equal(JSON.stringify(h.requests.at(-1).args), original);
		assert.equal(h.saved[0], "PE-1");
	}
});

test("opening existing draft passes its ID; creating another requires explicit confirmation", async () => {
	const h = fixture(); await h.editor.open("PE-OLD"); assert.equal(h.requests[0].args.payment_entry, "PE-OLD");
	h.confirm = false; await h.editor.open(null, true); assert.equal(h.requests.length, 1);
	h.confirm = true; await h.editor.open(null, true); assert.equal(h.requests.at(-1).args.new_receipt, 1);
});

test("closing dirty form can be declined and confirmed close discards only staged uploads", async () => {
	const h = fixture(); await h.editor.open(); h.controls.amount.touch(11); const attachments = h.attachmentManagers.at(-1);
	h.confirm = false; assert.equal(await h.editor.close(), false); assert.equal(h.panel.props.hidden, false); assert.equal(attachments.discarded, undefined);
	h.confirm = true; assert.equal(await h.editor.close(), true); assert.equal(attachments.discarded, true); assert.equal(h.panel.props.hidden, true);
});

test("late context response cannot reopen a hidden dialog", async () => {
	const h = fixture(); let finish; h.respond = () => new Promise(resolve => { finish = resolve; });
	const opening = h.editor.open(); await Promise.resolve(); h.dialog.$wrapper.events["hidden.bs.modal.dlpSalesReceipt"]();
	finish(h.projection()); await opening; assert.equal(h.attachmentManagers.length, 0); assert.equal(h.events.size, 0);
});

test("native datepicker or Link changes are detected even without an input event", async () => {
	const h = fixture(); await h.editor.open(); h.controls.posting_date.value = "2026-10-27";
	h.confirm = false; assert.equal(await h.editor.close(), false);
	assert.equal(h.panel.props.hidden, false);
});

test("advanced settings never create a receipt without the explicit native action", async () => {
	const h = fixture(); h.respond = () => ({ ...h.projection("PE-1"), advanced_reason: "workflow", editable_fields: [] });
	await h.editor.open("PE-1"); assert.equal(h.attachmentManagers.length, 0);
	assert.ok(h.panel.contents.some(box => /打开完整付款单/.test(box.contents.map(item => item.markup).join(""))));
	assert.equal(h.requests.length, 1); assert.equal(h.advanced.length, 0);
});

test("submit reads the latest saved receipt, confirms its amount and only then posts the versioned request", async () => {
	const h = fixture(); await h.editor.open("PE-1");
	const latest = h.projection("PE-1"); latest.document.amount = 30; latest.document.modified = "p2"; latest.order_modified = "s2";
	h.respond = request => request.method.endsWith("get_receipt_context") ? latest
		: { ...latest, document: { ...latest.document, docstatus: 1 }, can_submit: false };
	await h.editor.submit();
	assert.match(h.callbacks[0], /PE-1.*30\.00.*MXN.*记账/);
	const posted = h.requests.filter(request => request.method.endsWith("submit_receipt"));
	assert.equal(posted.length, 1);
	assert.equal(posted[0].args.expected_modified, "p2"); assert.equal(posted[0].args.order_modified, "s2");
	assert.equal(h.saved[0], "PE-1"); assert.equal(h.dialog.hidden, undefined);
	assert.equal(h.requests.some(request => request.method.endsWith("save_receipt_draft")), false);
});

test("unsaved edits, uploads and uncertain saves block submission", async () => {
	for (const kind of ["dirty", "files", "upload", "save"]) {
		const h = fixture(); await h.editor.open("PE-1");
		if (kind === "dirty") h.controls.amount.touch(50);
		if (kind === "files") h.attachmentManagers.at(-1).files = [{ name: "F1" }];
		if (kind === "upload") h.attachmentManagers.at(-1).waiting = true;
		if (kind === "save") { h.respond = () => { throw new Error("network"); }; await h.editor.save(); }
		const before = h.requests.length; await h.editor.submit();
		assert.equal(h.requests.length, before); assert.equal(h.callbacks.length, 0);
	}
});

test("clearing form busy state keeps submit disabled while selected files are initializing", async () => {
	const h = fixture(); await h.editor.open("PE-1");
	h.attachmentManagers.at(-1).waiting = true;
	h.editor.drawer.setBusy(false);
	assert.equal(h.panel.find(".dlp-receipt-submit").props.disabled, true);
	h.attachmentManagers.at(-1).waiting = false;
	h.editor.drawer.setBusy(false);
	assert.equal(h.panel.find(".dlp-receipt-submit").props.disabled, false);
});

test("declined confirmation and revoked submission permission never post", async () => {
	for (const permitted of [true, false]) {
		const h = fixture(); h.respond = () => ({ ...h.projection("PE-1"), can_submit: permitted });
		await h.editor.open("PE-1"); h.confirm = false; await h.editor.submit();
		assert.equal(h.requests.some(request => request.method.endsWith("submit_receipt")), false);
	}
});

test("submission transport failure retains exact request and blocks creating or saving another receipt", async () => {
	const h = fixture(); await h.editor.open("PE-1");
	h.respond = request => { if (request.method.endsWith("get_receipt_context")) return h.projection("PE-1"); throw new Error("network"); };
	await h.editor.submit(); const original = JSON.stringify(h.requests.at(-1).args);
	assert.equal(h.controls.amount.df.read_only, true);
	const before = h.requests.length; await h.editor.save(); assert.equal(h.requests.length, before);
	h.respond = () => ({ ...h.projection("PE-1"), document: { ...h.projection("PE-1").document, docstatus: 1 }, can_submit: false });
	await h.editor.close(); await h.editor.open(); await h.editor.submit();
	assert.equal(JSON.stringify(h.requests.at(-1).args), original); assert.equal(h.callbacks.filter(text=>/确认提交收款单/.test(text)).length, 1);
});

test("double clicks during submission confirmation do not create duplicate posts", async () => {
	const h = fixture(); await h.editor.open("PE-1"); let finish;
	h.respond = request => request.method.endsWith("get_receipt_context") ? h.projection("PE-1") : new Promise(resolve => { finish = resolve; });
	const pending = h.editor.submit();
	for (let attempt = 0; !finish && attempt < 50; attempt++) await Promise.resolve();
	assert.equal(typeof finish, "function");
	await h.editor.submit(); assert.equal(h.requests.filter(request=>request.method.endsWith("submit_receipt")).length, 1);
	finish({ ...h.projection("PE-1"), document: { ...h.projection("PE-1").document, docstatus: 1 }, can_submit: false });
	await pending;
});

test("refresh failure after acknowledged submission cannot repeat the native post", async () => {
	const h = fixture(); await h.editor.open("PE-1"); h.refreshError = new Error("refresh");
	let submitted = false;
	h.respond = request => {
		if (request.method.endsWith("submit_receipt")) submitted = true;
		const result = h.projection("PE-1"); result.document.docstatus = submitted ? 1 : 0; result.can_submit = !submitted;
		return result;
	};
	await h.editor.submit();
	assert.match(h.panel.find(".dlp-receipt-error").textValue, /收款已提交.*刷新失败/);
	await h.editor.submit();
	assert.equal(h.requests.filter(request=>request.method.endsWith("submit_receipt")).length, 1);
});
