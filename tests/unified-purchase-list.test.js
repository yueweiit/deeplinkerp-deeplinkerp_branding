const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const engine = require("../deeplinkerp_branding/public/js/compact_list.js");
const adapterPath = path.join(__dirname, "../deeplinkerp_branding/public/js/unified_purchase_list.js");
const adapter = fs.existsSync(adapterPath) ? require(adapterPath) : {};

test('unified purchase view preserves freezing through supplier like native purchase list',()=>{
 assert.equal(adapter.configure([{fieldname:'name'},{fieldname:'supplier_name'}]).freezeUntil,'supplier_name');
});

test('matching advanced and standard company conditions retain the advanced PO-only scope in list and export',async()=>{
 const filter=['Purchase Order','company','=','YUEWEI MX'];
 const c={quick:{},providerScope:'oa',page:0,pageSize:100,nativeControlFields:new Set(['company']),controls:{company:{get_value:()=>filter[3]}},list:{filter_area:{filter_list:{get_filters:()=>[filter]}}}};
 const provider=adapter.configure([]),native={filters:[filter],or_filters:[],order_by:'transaction_date desc'},args=provider.getArgs(c,native);
 assert.deepEqual(JSON.parse(args.native_filters),[filter]);assert.equal(JSON.parse(args.filters).company,undefined);
 let exported;c.originals={get_args:()=>native};c.preferences={columns:['name']};
 c.root={DeepLinkERPPurchaseOrderExport:{downloadWorkbook(){},async fetchNativeWorkbook(_,args){exported=args;return new Uint8Array();}}};
 await provider.exportCurrent(c);assert.equal(exported.native_filters,args.native_filters);assert.equal(exported.filters,args.filters);
});

function fixture(options={}) {
	const native = options.columns || [{ fieldname: "name", label: "采购订单号", width: 166 }];
	const provider = options.provider || {
		virtualFields: ["source"],
		columns: [...native, { fieldname: "source", label: "来源", width: 100 }],
		request: (c) => ({ method: "test.unified", args: { filters: JSON.stringify({ ...c.quick, scope: c.providerScope }), start: c.page * c.pageSize, page_length: c.pageSize, order_by: "transaction_date desc" } }),
		formLink: (doc) => `/desk/${doc.row_type === "oa_request" ? "oa-purchase-request" : "purchase-order"}/${encodeURIComponent(doc.name)}`,
		summary: () => "分别汇总",
		onActivate(c) { c.list.realtimeBindings ||= []; c.list.realtimeBindings.push("OA"); },
	};
	const grid = engine.create({ doctype: "Purchase Order", controllerKey: "grid", routeClass: "test", columns: native, provider, providerSelectable: options.providerSelectable, keepColumnHeader: options.keepColumnHeader });
	const list = {
		doctype: "Purchase Order", view_name: "List", meta: options.meta || { fields: [] }, fields: [["name", "Purchase Order"]], data: [],
		get_args() { return { filters: this.filters || [], or_filters: this.or_filters || [], fields: ["name"], order_by: this.order_by, start: this.start, page_length: this.page_length }; },
		get_call_args() { return { method: "native", args: this.get_args() }; },
		no_change() { return false; }, prepare_data(r) { this.data = r.message; }, reset_defaults() {},
		refresh() { return Promise.resolve(); }, get_header_html() {}, get_list_row_html() {}, render_header() {}, render_list() {}, render_count() {}, toggle_result_area() {},
		get_form_link(d) { return `/desk/purchase-order/${d.name}`; }, get_checked_items() { return ["OLD-PO"]; },
		setup_realtime_updates() { this.realtimeBindings = ["Purchase Order"]; },
	};
	const root = { frappe: { model: { std_fields_list: ["name"] }, perm: { has_perm: () => true }, get_route: () => ["List", "Purchase Order", "List"], session: {}, boot: {} } };
	if (options.realtime) {
		list.pending_document_refreshes = [];
		list.process_document_refreshes = () => assert.fail("native incremental append must not run on a bounded provider page");
		list.page = { wrapper: { is: () => true } };
		root.cur_list = list;
		root.frappe.utils = { debounce: callback => callback };
	}
	const controller = grid.mount(list, root);
	return { grid, list, controller };
}

test('real unified provider PO rows reuse native workflow indicator while OA rows keep their own status',()=>{
 const columns=[{fieldname:'name',label:'单据号',width:166},{fieldname:'supplier_name',label:'供应商',width:190},{fieldname:'status',label:'状态',width:120}];
 const {list,controller}=fixture({columns,provider:adapter.configure(columns),meta:{fields:[{fieldname:'status'},{fieldname:'supplier_name'}]}});
 const calls=[];list.workflow_state_fieldname='workflow_state';list.get_indicator_html=(doc,workflow)=>{calls.push([doc,workflow]);return '<span class="indicator-pill blue">待收货待开票</span>';};
 controller.setProviderScope('all',false);
 const po={name:'PO',row_type:'purchase_order',status:'To Receive and Bill',docstatus:1,supplier_name:'S'};
 const html=list.get_list_row_html(po);assert.match(html,/indicator-pill blue/);assert.match(html,/待收货待开票/);assert.equal(calls[0][0],po);assert.equal(calls[0][1],true);
 const count=calls.length,oa=list.get_list_row_html({name:'OA',row_type:'oa_request',status:'OA 待办',supplier_name:'S'});assert.match(oa,/OA 待办/);assert.doesNotMatch(oa,/indicator-pill/);assert.equal(calls.length,count);
 assert.equal(controller.providerScope,'all');assert.equal(controller.list.data.length,0);
});

test("optional provider keeps OA rows outside native PO data and selection", () => {
	const { list, controller } = fixture();
	assert.equal(typeof controller.setProviderScope, "function");
	controller.setProviderScope("all", false);
	const call = list.get_call_args();
	assert.equal(call.method, "test.unified");
	list.no_change(call);
	const response = { message: { rows: [{ name: "SAME", row_key: "OA Purchase Request::SAME", row_type: "oa_request", source: "OA" }], total_count: 1 } };
	call.callback(response);
	list.prepare_data(response);
	assert.deepEqual(list.data, []);
	assert.equal(controller.providerRows.length, 1);
	assert.deepEqual(list.get_checked_items(true), []);
	const html = list.get_list_row_html(controller.providerRows[0]);
	assert.ok(html.includes("/desk/oa-purchase-request/SAME"));
	assert.ok(!html.includes("list-row-checkbox"));
	assert.ok(!html.includes('class="level list-row '));
	assert.ok(!list.get_header_html().includes("list-check-all"));
});

test("selectable procurement provider retains only real orders in native selection and bulk hooks", () => {
	const { list, controller } = fixture({ providerSelectable: doc => doc.row_type === "purchase_order", keepColumnHeader: true });
	let nativeBefore = 0, nativeRestore = 0;
	controller.originals.before_render = () => nativeBefore++;
	controller.originals.set_rows_as_checked = () => nativeRestore++;
	controller.originals.get_checked_items = names => names ? ["PO", "OA"] : [{ name: "PO" }, { name: "OA" }];
	controller.setProviderScope("all", false);
	const call = list.get_call_args(); list.no_change(call);
	const po = { name: "PO", row_type: "purchase_order" }, oa = { name: "OA", row_type: "oa_request" };
	const response = { message: { rows: [oa, po], total_count: 2 } };
	call.callback(response); list.prepare_data(response);
	assert.deepEqual(list.data, [po]);
	assert.deepEqual(controller.providerRows, [oa, po]);
	assert.deepEqual(list.get_checked_items(true), ["PO"]);
	assert.deepEqual(list.get_checked_items(), [{ name: "PO" }]);
	assert.match(list.get_list_row_html(po), /list-row-checkbox/);
	assert.match(list.get_list_row_html(po), /class="level list-row /);
	assert.doesNotMatch(list.get_list_row_html(oa), /list-row-checkbox|class="level list-row /);
	assert.match(list.get_header_html(), /list-check-all/);
	list.before_render(); list.set_rows_as_checked();
	assert.equal(nativeBefore, 1); assert.equal(nativeRestore, 1);
});

test("selectable provider forwards changing native AND and OR filters and rejects an old response", () => {
	const { list, controller } = fixture({ provider: adapter.configure([{ fieldname: "name", label: "单据号", width: 166 }]), providerSelectable: doc => doc.row_type === "purchase_order" });
	controller.setProviderScope("all", false);
	list.filters = [["Purchase Order", "project", "=", "P1"]];
	list.or_filters = [["Purchase Order", "name", "like", "PO%"]];
	const call = list.get_call_args(); list.no_change(call);
	assert.equal(typeof call.args.native_filters, "string");
	assert.deepEqual(JSON.parse(call.args.native_filters), list.filters);
	assert.deepEqual(JSON.parse(call.args.native_or_filters), list.or_filters);
	controller.setPage(2);
	list.filters = [["Purchase Order", "project", "=", "P2"]];
	const response = { message: { rows: [{ name: "OLD", row_type: "purchase_order" }], total_count: 1 } };
	call.callback(response); list.prepare_data(response);
	assert.deepEqual(list.data, []); assert.equal(controller.page, 0);
});

test("returning to orders restores native query, links, preferences and selection", () => {
	const { list, controller } = fixture();
	assert.equal(typeof controller.setProviderScope, "function");
	controller.setProviderScope("all", false);
	assert.ok(controller.preferences.columns.includes("source"));
	controller.setProviderScope("orders", false);
	assert.equal(list.get_call_args().method, "native");
	assert.deepEqual(controller.preferences.columns, ["name"]);
	assert.deepEqual(list.get_checked_items(true), ["OLD-PO"]);
});

test("switching scope invalidates an old response before a new request is dispatched", () => {
	const { list, controller } = fixture();
	assert.equal(typeof controller.setProviderScope, "function");
	controller.setProviderScope("all", false);
	const old = list.get_call_args(); list.no_change(old);
	controller.setProviderScope("oa", false);
	const response = { message: { rows: [{ name: "old" }], total_count: 1 } };
	old.callback(response); list.prepare_data(response);
	assert.deepEqual(controller.providerRows, []);
});

test("OA realtime binding follows the native setup which clears event listeners", () => {
	const { list, controller } = fixture();
	controller.setProviderScope("all", false);
	list.setup_realtime_updates();
	assert.deepEqual(list.realtimeBindings, ["Purchase Order", "OA"]);
});

test("provider request preserves native advanced filters and every quick predicate", () => {
	assert.equal(typeof adapter.request, "function");
	const native = { filters: [["Purchase Order", "project", "=", "P1"]], or_filters: [["Purchase Order", "name", "like", "PO%"]] };
	const request = adapter.request({ providerScope: "all", quick: { search: "A", pending_company: 1, company: "Known", status: "Draft", advance_payment_status: "Initiated" }, page: 2, pageSize: 100, providerOrderBy: "oa_amount asc" }, native);
	assert.deepEqual(JSON.parse(request.args.filters), { scope: "all", search: "A", company: "__unconfirmed__", status: "Draft", advance_payment_status: "Initiated" });
	assert.deepEqual(JSON.parse(request.args.native_filters), native.filters);
	assert.deepEqual(JSON.parse(request.args.native_or_filters), native.or_filters);
	assert.equal(request.args.start, 200);
	assert.equal(request.args.order_by, "oa_amount asc");
});

test("native sort changes become canonical provider args and reset pagination", () => {
	const { list, controller } = fixture({ provider: adapter.configure([{ fieldname: "name", label: "单号", width: 166 }]), providerSelectable: doc => doc.row_type === "purchase_order" });
	controller.setProviderScope("all", false);
	list.order_by = "`tabPurchase Order`.`creation` desc";
	assert.equal(list.get_args().order_by, "creation desc");
	controller.setPage(2);
	list.order_by = "`tabPurchase Order`.`modified` asc";
	const call = list.get_call_args();
	assert.equal(call.args.order_by, "modified asc"); assert.equal(call.args.start, 0);
	controller.setPage(3);
	list.order_by = "`tabPurchase Order`.`custom_workflow_state` asc, `tabPurchase Order`.`name` asc";
	assert.equal(list.get_args().order_by, "custom_workflow_state asc"); assert.equal(controller.page, 0);
});

test("explicit header sorting wins until a native sort choice and both controls keep native labels in sync", () => {
	let headerClick, nativeChanges = 0;
	const surface = { insertAfter() { return this; }, on(event, selector, handler) { if (selector === "[data-provider-sort]") headerClick = handler; return this; } };
	const sorter = { sort_by: "creation", sort_order: "desc", args: { options: ["name", "creation", "modified"].map(fieldname => ({ fieldname })) },
		get_sql_string() { return "`tabPurchase Order`.`" + this.sort_by + "` " + this.sort_order; },
		set_value(field, order) { this.sort_by = field; this.sort_order = order; }, onchange() { nativeChanges++; } };
	const list = { $result: surface, sort_selector: sorter };
	const controller = { root: { $: () => surface, frappe: {} }, list, $filters: surface, allowed: new Set(["name", "creation", "modified"]), quick: {}, page: 0, pageSize: 100, providerOrderBy: "transaction_date desc",
		originals: { get_args: () => ({ order_by: sorter.get_sql_string() }) }, setProviderScope(scope) { this.providerScope = scope; }, setPage(page) { this.page = page; }, refresh() {} };
	adapter.configure([]).mountControls(controller);
	assert.equal(adapter.getArgs(controller).order_by, "creation desc");
	headerClick({ currentTarget: { dataset: { providerSort: "name" } } });
	assert.equal(adapter.getArgs(controller).order_by, "name asc"); assert.equal(sorter.sort_by, "name"); assert.equal(sorter.sort_order, "asc");
	headerClick({ currentTarget: { dataset: { providerSort: "oa_amount" } } });
	assert.equal(adapter.getArgs(controller).order_by, "oa_amount asc");
	controller.setPage(2); sorter.onchange("name", "asc");
	assert.equal(adapter.getArgs(controller).order_by, "name asc"); assert.equal(controller.page, 0); assert.equal(nativeChanges, 1);
});

test("unified procurement controls start with real orders and source filtering stays on the same provider", () => {
	const markup = [];
	const surface = { prependTo() { return this; }, insertAfter() { return this; }, on() { return this; } };
	let scope;
	adapter.configure([]).mountControls({ root: { $: html => { markup.push(html); return surface; }, frappe: {} }, list: { $result: surface }, $toolbar: surface, $filters: surface, setProviderScope: value => scope = value });
	assert.equal(scope, "orders");
	assert.doesNotMatch(markup.join(""), /dlp-po-scope|仅订单|仅 OA|仅查看/);
	assert.match(markup.join(""), /钉钉/); assert.match(markup.join(""), /其他来源/);
});

test("only administrators and system managers see the inline procurement source sync action", () => {
	for (const [user, roles, allowed] of [["buyer", ["Purchase User"], false], ["Administrator", [], true], ["manager", ["System Manager"], true]]) {
		const markup = [];
		const surface = { appendTo() { return this; }, insertAfter() { return this; }, on() { return this; } };
		adapter.configure([]).mountControls({ root: { $: html => { markup.push(html); return surface; }, frappe: { session: { user }, user_roles: roles } }, list: { $result: surface }, $filters: surface, setProviderScope() {} });
		assert.equal(markup.join("").includes("同步钉钉"), allowed, user);
		assert.equal(markup.join("").includes("采购出纳接口设置"), allowed, user);
	}
});

test("source row labels and actions use OA identity without pretending it is an order", () => {
	const escape = value => String(value).replaceAll("<", "&lt;").replaceAll('"', "&quot;");
	const doc = { name: "OA/1", row_type: "oa_request", oa_number: "2026-原单<1>" };
	assert.equal(adapter.renderValue("name", doc, {}, escape), "待完善 · 2026-原单&lt;1>");
	assert.match(adapter.renderValue("receipt_action", doc, {}, escape), /完善\/关联/);
	assert.match(adapter.renderValue("receipt_action", doc, {}, escape), /data-purchase-source="OA\/1"/);
	assert.doesNotMatch(adapter.renderValue("receipt_action", doc, {}, escape), /确认订单|入库|付款/);
	const controller = { list: {}, root: { frappe: {} }, translate: value => value };
	for (const number of ['202607201643000193490', '202607201643000193490"标识']) {
		const longSource = { ...doc, oa_number: number };
		const identity = adapter.configure([]).renderLink(controller, longSource, adapter.renderValue("name", longSource, {}, escape), escape);
		assert.ok(identity.includes(`title="${escape(number)}"`), "Truncated source identities must retain the complete escaped approval number on hover");
		assert.match(identity, /data-purchase-source="OA\/1"/);
		assert.doesNotMatch(identity, /href="\/desk\/purchase-order/);
	}
	for (const source of ["non_oa", "未关联 OA"]) assert.equal(adapter.renderValue("source", { source }), "其他来源");
});

test("cashier paid evidence is a separate amount with its own currency and unknown remains blank", () => {
	const amount = adapter.renderValue("cashier_paid_amount", { cashier_paid_amount: "25.12345", cashier_currency: "CNY", currency: "USD", advance_paid: 10 });
	assert.match(amount, /25\.12 CNY/);
	assert.equal(adapter.renderValue("cashier_paid_amount", { cashier_paid_amount: null }), "—");
	assert.match(adapter.renderValue("cashier_paid_amount", { cashier_paid_amount: "0", cashier_currency: "CNY" }), /0\.00 CNY/);
	assert.ok(adapter.configure([]).virtualFields.includes("cashier_paid_amount"));
});

test("hidden company is not presented as a pending company assignment", () => {
	assert.equal(adapter.renderValue("company", { company: null, company_visibility: "hidden" }), "公司不可见");
	assert.equal(adapter.renderValue("company", { company: null, company_visibility: "pending" }), "公司待确认");
	const grouped = adapter.renderValue("supplier_name", { row_type: "oa_request", supplier_name: "Supplier", company: null, company_visibility: "hidden", role_context: { company_confirmed: false } });
	assert.match(grouped, /公司不可见/); assert.doesNotMatch(grouped, /公司待确认/);
});

test("OA amount renders its own currency and basis without rounding the source", () => {
	assert.equal(typeof adapter.renderValue, "function");
	const escape = (v) => String(v).replaceAll("<", "&lt;");
	const doc = { oa_amount: 123.45678, currency: "USD", oa_currency: "MXN", oa_amount_basis: "付款申请金额", oa_warning: "<需核对>" };
	const value = adapter.renderValue("oa_amount", doc, { number: (v) => v.toFixed(2) }, escape);
	assert.ok(value.includes("123.46 MXN"));
	assert.ok(value.includes("付款申请金额"));
	assert.ok(value.includes("&lt;需核对>"));
	assert.equal(doc.oa_amount, 123.45678);
	assert.ok(adapter.renderValue("oa_amount", { oa_amount: 0 }, {}, escape).includes("币种待确认"));
});

test("navigation hides only an OA list duplicate when a PO entry exists", () => {
	assert.equal(typeof adapter.shouldHideOANavigation, "function");
	assert.equal(adapter.shouldHideOANavigation("/desk/oa-purchase-request", ["/desk/purchase-order"], true), true);
	assert.equal(adapter.shouldHideOANavigation("/desk/oa-purchase-request", [], true), false);
	assert.equal(adapter.shouldHideOANavigation("/desk/oa-purchase-request", ["/desk/purchase-order"], false), false);
	assert.equal(adapter.shouldHideOANavigation("/desk/oa-purchase-request/ABC", ["/desk/purchase-order"], true), false);
});

test("conflicting OA references keep individual source links and readable original evidence", () => {
	const escape = (v) => String(v).replaceAll("<", "&lt;").replaceAll('"', "&quot;");
	const doc = { oa_warning: "OA关联冲突", oa_references: [
		{ name: "OA/1", number: "审批<1>", approval_status: "通过", amount: 15.12345, currency: "CNY", amount_basis: "采购明细合计", company: "A" },
		{ name: "OA-2", number: "审批2", approval_status: "撤销", amount: 20, currency: "MXN", amount_basis: "付款申请金额", company: "B" },
	] };
	const value = adapter.renderValue("oa_number", doc, {}, escape);
	assert.ok(value.includes("/desk/oa-purchase-request/OA%2F1"));
	assert.ok(value.includes("/desk/oa-purchase-request/OA-2"));
	assert.ok(value.includes("审批&lt;1>"));
	assert.ok(value.includes("15.12 CNY"));
	assert.equal(doc.oa_references[0].amount, 15.12345, "two-decimal presentation does not change source evidence");
	assert.ok(value.includes("付款申请金额"));
	assert.equal(adapter.renderValue("approval_status", doc, {}, escape), "通过；撤销");
});

test("unified exports always retain selected amount currencies and provenance", () => {
	assert.deepEqual(adapter.exportColumns(["name", "oa_amount", "advance_paid"]),
		["order_context", "oa_amount", "oa_currency", "oa_amount_basis", "advance_paid", "party_account_currency", "oa_warning", "oa_references"]);
	assert.deepEqual(adapter.exportColumns(["name", "grand_total"]),
		["order_context", "grand_total", "currency", "oa_warning", "oa_references"]);
});
test('purchase compact progress exports use the same business labels instead of unrelated raw evidence states',()=>{
 assert.deepEqual(adapter.exportColumns(['payment_progress','receipt_progress']),['payment_progress','receipt_progress','oa_warning','oa_references']);
});

test("Excel transport reuses same-origin CSRF download for the unified endpoint", async () => {
	const exporter = require("../deeplinkerp_branding/public/js/purchase_order_export.js");
	assert.equal(typeof exporter.downloadWorkbook, "function");
	let body;
	const root = { frappe: { csrf_token: "test", request: { url: "/" } }, location: { href: "https://erp.test/desk/purchase-order", origin: "https://erp.test" }, fetch: async (_, options) => {
		body = new URLSearchParams(options.body);
		return new Response(Uint8Array.of(0x50, 0x4b, 0, 0), { headers: { "content-type": "application/octet-stream" } });
	} };
	await exporter.fetchNativeWorkbook(root, { filters: "{}", columns: '["name"]' }, "deeplinkerp_branding.services.unified_purchase_service.export_unified_purchase_list");
	assert.equal(body.get("cmd"), "deeplinkerp_branding.services.unified_purchase_service.export_unified_purchase_list");
	assert.equal(body.has("file_format_type"), false);
});

test("cached unified list reinstalls only its own OA realtime listener after native navigation", () => {
	const { EventEmitter } = require("node:events"), realtime = new EventEmitter();
	realtime.doctype_subscribe = () => {};
	const element = { prependTo() { return this; }, insertAfter() { return this; }, on() { return this; } };
	const list = { $result: element, page: { wrapper: { is: () => true } } };
	const root = { $: () => element, cur_list: list, frappe: { realtime } };
	let refreshed = 0;
	const controller = { root, list, $toolbar: element, $filters: element, providerPayload: { capabilities: { oa_request: true } }, setProviderScope(scope) { this.providerScope = scope; }, refresh() { refreshed++; } };
	const provider = adapter.configure([]);
	provider.mountControls(controller);
	realtime.emit("list_update", { doctype: "OA Purchase Request" });
	assert.equal(refreshed, 1);
	realtime.removeAllListeners("list_update");
	let nativeEvents = 0;
	realtime.on("list_update", () => nativeEvents++);
	provider.onActivate(controller);
	provider.onActivate(controller);
	realtime.emit("list_update", { doctype: "OA Purchase Request" });
	assert.equal(refreshed, 2);
	assert.equal(nativeEvents, 1);
});

test("OA realtime events enter the existing guarded native refresh queue and retain selected-page context", async () => {
	const { EventEmitter } = require("node:events");
	for (const guard of ["selection", "bulk", "editing", "avoid"]) {
		const { list, controller } = fixture({ provider: adapter.configure([{ fieldname: "name", label: "单号", width: 166 }]), providerSelectable: doc => doc.row_type === "purchase_order", realtime: true });
		let refreshed = 0; list.refresh = () => { refreshed++; };
		controller.root.frappe.realtime = new EventEmitter();
		adapter.configure([]).onActivate(controller);
		controller.setProviderScope("all", false); controller.setPage(2);
		list.data = [{ name: "SELECTED-PO", row_type: "purchase_order" }];
		if (guard === "selection") list.$checks = [{ name: "SELECTED-PO" }];
		if (guard === "bulk") list.disable_list_update = true;
		if (guard === "editing") list.filter_area = { is_being_edited: () => true };
		if (guard === "avoid") list.avoid_realtime_update = () => true;
		controller.root.frappe.realtime.emit("list_update", { doctype: "OA Purchase Request", name: "NEW-OA" });
		assert.equal(refreshed, 0, guard); assert.equal(list.pending_document_refreshes.length, 1, guard);
		assert.deepEqual(list.data.map(doc => doc.name), ["SELECTED-PO"]); assert.equal(controller.page, 2);
		list.$checks = []; list.disable_list_update = false; list.filter_area = { is_being_edited: () => false }; list.avoid_realtime_update = () => false;
		await list.process_document_refreshes();
		assert.equal(refreshed, 1, guard); assert.deepEqual(list.pending_document_refreshes, []); assert.equal(controller.page, 2);
	}
});

test("provider text filters react to typing without duplicate refresh on blur", () => {
	const handlers = new Map();
	const element = { prependTo() { return this; }, insertAfter() { return this; }, on(events, selector, handler) {
		if (selector === "[data-filter]") for (const event of events.split(" ")) handlers.set(event.split(".")[0], handler);
		return this;
	} };
	let refreshed = 0, pageResets = 0;
	const controller = { root: { $: () => element, frappe: {} }, list: { $result: element }, quick: {},
		$toolbar: element, $filters: element, setProviderScope() {}, setPage(page) { assert.equal(page, 0); pageResets++; }, refresh() { refreshed++; } };
	adapter.configure([]).mountControls(controller);
	assert.equal(typeof handlers.get("input"), "function");
	const event = { target: { dataset: { filter: "approval_status" }, type: "text", value: "审批中" } };
	handlers.get("input")(event); handlers.get("change")(event);
	assert.equal(controller.quick.approval_status, "审批中");
	assert.equal(refreshed, 1); assert.equal(pageResets, 1);
	event.target.value = ""; handlers.get("input")(event);
	assert.equal(refreshed, 2);
});

test("export captures clicked native filters and columns before lazy library loading", async () => {
	let resolve, captured;
	const root = { frappe: { require: () => new Promise((done) => { resolve = done; }) } };
	const native = { filters: [["Purchase Order", "project", "=", "P1"]], or_filters: [["Purchase Order", "owner", "=", "buyer"]], order_by: "`tabPurchase Order`.`modified` desc" };
	const controller = { root, originals: { get_args: () => native }, providerScope: "all", quick: { company: "A" }, preferences: { columns: ["name", "oa_amount", "cashier_paid_amount"] }, providerOrderBy: "name asc" };
	const downloading = adapter.exportCurrent(controller);
	controller.providerScope = "oa"; controller.quick.company = "B"; controller.preferences.columns = ["name", "grand_total"];
	native.filters = []; native.or_filters = []; native.order_by = "`tabPurchase Order`.`creation` asc";
	root.DeepLinkERPPurchaseOrderExport = { fetchNativeWorkbook: async (_, args) => { captured = args; return new Uint8Array(); }, downloadWorkbook() {} };
	resolve(); await downloading;
	assert.deepEqual(JSON.parse(captured.filters), { scope: "all", company: "A" });
	assert.ok(JSON.parse(captured.columns).includes("oa_amount"));
	assert.ok(!JSON.parse(captured.columns).includes("grand_total"));
	assert.deepEqual(JSON.parse(captured.native_filters), [["Purchase Order", "project", "=", "P1"]]);
	assert.deepEqual(JSON.parse(captured.native_or_filters), [["Purchase Order", "owner", "=", "buyer"]]);
	assert.ok(JSON.parse(captured.columns).includes("cashier_currency"));
	assert.equal(captured.order_by, "modified desc");
	assert.equal(captured.start, undefined); assert.equal(captured.page_length, undefined);
});

const groupedDefaults = ["name", "supplier_name", "project_context", "external_payment", "internal_settlement", "receipt_logistics", "receipt_action"];
const nativePOColumns = require("../deeplinkerp_branding/public/js/purchase_order_list.js").COLUMNS;

test("every previous purchase layout upgrades once to the approved order defaults and retains density", () => {
	const provider = adapter.configure(nativePOColumns);
	assert.ok(provider.defaultColumns.includes("payment_progress")); assert.ok(!provider.defaultColumns.includes('item_code'));
	const old8 = ["name", "supplier_name", "grand_total", "order_settled", "order_unpaid", "per_received", "status", "receipt_action"];
	const old10 = [...old8.slice(0, 3), "requested_amount", "cashier_paid_amount", ...old8.slice(3)];
	const old16 = nativePOColumns.map(c => c.fieldname).filter(field => field !== "receipt_action");
	const allowed = new Set([...provider.columns.map(c => c.fieldname), ...provider.virtualFields]);
	const grid = engine.create({ doctype: "Purchase Order", columns: nativePOColumns, provider });
	for (const density of ["standard", "tight"]) {
		const input = { density, version: 2, columns: groupedDefaults }, before = JSON.stringify(input);
		const prefs = grid.normalizePreferences(input, allowed, provider.columns);
		assert.deepEqual(prefs.columns, provider.defaultColumns); assert.equal(prefs.density, density); assert.equal(prefs.version, 4);
		assert.equal(JSON.stringify(input), before);
	}
	for (const columns of [old8, old10, old16, nativePOColumns.map(c => c.fieldname), ["supplier_name", "name", "grand_total"], [...groupedDefaults].reverse(), ["name", "status"]]) {
		assert.deepEqual(grid.normalizePreferences({ density: "standard", version:4, columns }, allowed, provider.columns).columns, columns);
	}
	assert.ok(provider.columns.some(c => c.fieldname === "advance_paid"));
	assert.ok(provider.columns.some(c => c.fieldname === "requested_amount"));
	assert.deepEqual(grid.normalizePreferences({ density: "standard", version: 3, columns: old8 }, allowed, provider.columns).columns, provider.defaultColumns);
});

test("provider migration never infers old defaults from a custom column definition or an empty selection", () => {
	const native = [{ fieldname: "name", label: "订单", width: 166 }], provider = adapter.configure(native);
	const allowed = new Set(provider.columns.map(c => c.fieldname)), grid = engine.create({ doctype: "Purchase Order", columns: native, provider });
	for (const columns of [["name"], []]) assert.deepEqual(grid.normalizePreferences({ columns }, allowed, provider.columns).columns, ["name"]);
});

test("crossborder quick predicates join the same native AND OR request before pagination", () => {
	const native = { filters: [["Purchase Order", "status", "=", "To Receive"]], or_filters: [["Purchase Order", "owner", "=", "buyer"]] };
	const request = adapter.request({ providerScope: "orders", page: 2, pageSize: 20, quick: { search: "项目或审批", company: "买方", beneficiary_company: "工厂", progress_phase: "internal_unsettled", review_only: true } }, native);
	assert.deepEqual(JSON.parse(request.args.filters), { scope: "orders", search: "项目或审批", company: "买方", beneficiary_company: "工厂", progress_phase: "internal_unsettled", review_only: true });
	assert.deepEqual(JSON.parse(request.args.native_filters), native.filters); assert.deepEqual(JSON.parse(request.args.native_or_filters), native.or_filters);
	assert.equal(request.args.start, 40); assert.equal(request.args.page_length, 20);
});

test("group exports map identities and actions in current order and retain legacy financial leaves", () => {
	assert.deepEqual(adapter.exportColumns(["internal_settlement", "supplier_name", "name", "receipt_logistics", "receipt_action", "order_settled", "order_unpaid", "advance_paid"]), ["internal_settlement", "supplier_context", "order_context", "receipt_logistics", "action_context", "external_settled", "external_order_unpaid", "external_currency", "advance_paid", "party_account_currency", "oa_warning", "oa_references"]);
});

test("role groups separate authoritative PO buyer and confirmed beneficiary from source proposals", () => {
	const doc = { row_type: "purchase_order", supplier_name: "供应商", company: "原生买方", project: "原生项目", role_context: { purchasing_company: "来源公司", company_confirmed: false, buyer_company_proposal: "建议公司", beneficiary_companies: ["已核对工厂"], beneficiary_company_candidate: "候选工厂", source_beneficiary_company_hint: "原始归属", project_candidate: "候选项目" } };
	const supplier = adapter.renderValue("supplier_name", doc);
	assert.match(supplier, /采购付款.*原生买方/); assert.doesNotMatch(supplier, /采购付款.*来源公司/);
	assert.match(supplier, /来源待核对.*建议公司/);
	const project = adapter.renderValue("project_context", doc);
	assert.match(project, /原生项目/); assert.match(project, /最终归属.*已核对工厂/);
	assert.match(project, /来源待核对.*候选工厂/); assert.match(project, /来源待核对.*候选项目/);
});

test("supplier payment preserves exact zero, unknown and every OA currency and proof independently", () => {
	const doc = { row_type: "purchase_order", grand_total: 0, currency: "CNY", order_progress: { external: { state: "exact", settled: 0, order_unpaid: null, currency: "CNY" } }, oa_references: [
		{ name: "OA-1", number: "完整审批一", requested_amount: 12.345, currency: "CNY", cashier_paid_amount: 0, cashier_currency: "CNY", cashier_reconciliation_verified: false },
		{ name: "OA-2", number: "完整审批二", requested_amount: 25, currency: "USD", cashier_paid_amount: null, cashier_currency: "USD", cashier_reconciliation_verified: null },
	] };
	const snapshot = JSON.stringify(doc), html = adapter.renderValue("external_payment", doc, { number: () => "bad global precision" });
	assert.match(html, /ERP 已付[^<]*0\.00 CNY/); assert.match(html, /订单未付[^<]*—/);
	assert.match(html, /完整审批一/); assert.match(html, /完整审批二/); assert.match(html, /12\.35 CNY/); assert.match(html, /25\.00 USD/);
	assert.match(html, /出纳证据[^<]*0\.00 CNY/); assert.match(html, /未核对 ERP/); assert.match(html, /核对状态未知/);
	assert.doesNotMatch(html, /37\.35|bad global precision/); assert.equal(JSON.stringify(doc), snapshot);
});

test("legacy and conflicting OA source amounts keep their individual basis and review evidence", () => {
	const doc = { row_type: "oa_request", oa_references: [
		{ name: "OA-CNY", number: "原申请-CNY", amount: 11.456, currency: "CNY", amount_basis: "采购明细合计", warning: "公司关联需核对" },
		{ name: "OA-USD", number: "原申请-USD", amount: 20, currency: "USD", amount_basis: "付款申请金额", requested_amount: 30, cashier_paid_amount: 5, cashier_currency: "USD" },
	] };
	const html = adapter.renderValue("external_payment", doc);
	assert.match(html, /采购明细合计[^<]*11\.46 CNY/); assert.match(html, /付款申请金额[^<]*20\.00 USD/);
	assert.match(html, /申请[^<]*30\.00 USD/); assert.match(html, /来源待核对/); assert.match(html, /公司关联需核对/);
	assert.doesNotMatch(html, /31\.46/);
});

test("internal groups never treat quote or custody as AP and keep payable orders and currencies separate", () => {
	for (const state of ["draft_quote", "price_unconfirmed", "price_stale", "awaiting_invoice", "custody"]) {
		const html = adapter.renderValue("internal_settlement", { row_type: "purchase_order", order_progress: { internal: [{ state, internal_order: "I-1", payable_total: 999, payable_outstanding: 888, currency: "USD" }] } });
		assert.match(html, /未形成应付|不形成内部应付|待开应付|待确认|重新核对/); assert.doesNotMatch(html, /999\.00|888\.00/);
	}
	const html = adapter.renderValue("internal_settlement", { order_progress: { internal: [
		{ state: "payable", internal_order: "I-CNY", beneficiary_company: "工厂一", payable_total: 10, payable_settled: 0, payable_outstanding: 10, currency: "CNY" },
		{ state: "payable", internal_order: "I-USD", beneficiary_company: "工厂二", payable_total: 20, payable_settled: 5, payable_outstanding: null, currency: "USD" },
	] } });
	assert.match(html, /I-CNY/); assert.match(html, /I-USD/); assert.match(html, /10\.00 CNY/); assert.match(html, /20\.00 USD/); assert.match(html, /应付未付[^<]*—/); assert.doesNotMatch(html, /30\.00/);
});

test("factory receipt uses confirmed stock quantities and unit with maximum two decimals; report is separate", () => {
	const doc = { order_progress: { domestic_receipt: { state: "none", quantities: [] }, factory_receipt: { state: "exact", quantities: [{ beneficiary_company: "工厂", received_stock_qty: 0, pending_stock_qty: 1.23456, stock_uom: "kg" }] }, receipt_logistics: [{ state: "reported", beneficiary_company: "工厂", reported_quantities: [{ qty: 99.12345, uom: "箱", destination: "工厂" }], native_receipt: { state: "none" }, manual_nodes: [{node:'reported_arrival',note:'人工报告，不改变库存',erp_received:false}] }] } };
	const html = adapter.renderValue("receipt_logistics", doc);
	assert.match(html, /工厂/); assert.match(html, /已入库[^<]*0 kg/); assert.match(html, /待入库[^<]*1\.23 kg/); assert.match(html, /物流报告[^<]*99\.12 箱/);
	assert.match(html,/人工节点：报告到货（非 ERP 入库）/);assert.doesNotMatch(html,/reported_arrival/);
	doc.order_progress.factory_receipt.state = "unknown";
	const unknown = adapter.renderValue("receipt_logistics", doc);
	assert.match(unknown, /工厂入库待核对/); assert.doesNotMatch(unknown, /待入库[^<]*1\.23|已入库[^<]*0 kg/);
});

test("restricted financial groups show a generic denial without any related identity or amount", () => {
	const doc = { order_progress: { state: "restricted", external: { state: "restricted", settled: 987 }, internal: [{ state: "restricted", internal_order: "HIDDEN-ORDER", beneficiary_company: "HIDDEN-COMPANY", payable_total: 123, currency: "USD" }], receipt_logistics: [{ state: "restricted", cost_batch: "HIDDEN-BATCH", reported_quantities: [{ qty: 12, uom: "kg" }] }] } };
	for (const field of ["external_payment", "internal_settlement", "receipt_logistics"]) {
		const html = adapter.renderValue(field, doc);
		assert.match(html, /受限/); assert.doesNotMatch(html, /HIDDEN|987|123|USD|kg/);
	}
	assert.match(adapter.renderValue("internal_settlement", { row_type: "oa_request", order_progress: null }), /待完善/);
});
