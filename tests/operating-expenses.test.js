const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const listPath = "../deeplinkerp_branding/public/js/operating_expenses.js";
const create = fs.existsSync(require("node:path").join(__dirname, listPath))
	? require(listPath)
	: () => ({});
const api = create({});
const expectedColumns = ["source_id","source_status","company","source_company","applicant","summary","amount","paid_amount","pending_amount","currency","project","payee_name","needed_payment_date","general_manager_approval","actions","request_date"];
test("default dates cover 180 inclusive days and leave explicit ranges unchanged", () => {
	for (const [today, start] of [["2026-11-13", "2026-05-18"], ["2026-01-01", "2025-07-06"], ["2024-03-01", "2023-09-04"]]) {
		const dates = create({frappe: {datetime: {
			get_today: () => today,
			add_days: (date, days) => {assert.equal(date, today); assert.equal(days, -179); return start;},
		}}}).defaultQuickFilters();
		assert.deepEqual(dates, {quick_tab: "pending_work", date_from: start, date_to: today});
		const c = {quick: {...dates, date_from: "2020-01-01"}, providerOrderBy: "request_date desc", page: 0, pageSize: 100};
		assert.equal(JSON.parse(api.request(c).args.filters).date_from, "2020-01-01");
	}
});
test("recommended columns show legal attribution alongside the OA organization", () => {
 assert.deepEqual(api.defaultColumns,expectedColumns);
 assert.equal(api.columns.find(col=>col.fieldname==="source_company").label,"来源组织");
 assert.equal(api.columns.find(col=>col.fieldname==="amount").label,"申请金额");
 const html=api.renderValue("source_id",{source_id:"x",approval_no:"100",summary:"rent"});
 assert.match(html,/100/); assert.doesNotMatch(html,/rent|<small/);
});
test("business approval states and quick tabs retain the same backend predicate for list and export", async () => {
 for(const state of ["pending","approved","rejected","terminated","withdrawn","unknown","eligible","blocked"]) assert.equal(api.filters({approval_state:state}).approval_state,state);
 assert.equal(api.approvalLabel("pending"),"审批中");
 assert.equal(api.approvalLabel("approved"),"已通过");
 assert.equal(api.approvalLabel("withdrawn"),"已撤回");
 assert.equal(api.approvalLabel("unknown"),"待核对");
 assert.throws(()=>api.filters({quick_tab:"injected"}));
 let exported;
 const root={DeepLinkERPPurchaseOrderExport:{downloadWorkbook(){},fetchNativeWorkbook:async (_r,args)=>{exported=args;return {};}}};
 const c={quick:{quick_tab:"approvals_running",company:"C"},providerOrderBy:"request_date desc",page:0,pageSize:100,preferences:{columns:["current_approver","source_status"]}};
 await create(root).exportCurrent(c);
 assert.deepEqual(JSON.parse(api.request(c).args.filters),JSON.parse(exported.filters));
 assert.deepEqual(api.quickTabs.map(row=>row[0]),["pending_work","all","approvals_running","paid","reconciliation"]);
 assert.equal(api.filters({quick_tab:"pending_payment"}).quick_tab,"pending_payment","old bookmarks remain supported");
 assert.match(api.renderValue("actions",{source_id:"safe"}),/data-tab="payments"[^>]*>付款明细/);
 assert.match(api.renderValue("actions",{source_id:"safe"}),/data-tab="approvals"[^>]*>查看审批/);
 assert.match(api.renderValue("current_approver",{current_approver:"<manager>"}),/&lt;manager&gt;/);
});
test("payment status exposes approval and the real work blocker without inventing source projections", () => {
 const html=api.renderValue("source_status",{source_status:"付款待核对",approval_state:"pending",blocking_reason:"历史付款待核对；<主管>审批中"});
 assert.match(html,/付款待核对/); assert.match(html,/审批中/); assert.match(html,/历史付款待核对/);
 assert.match(html,/&lt;主管&gt;/); assert.doesNotMatch(html,/<主管>/);
 assert.equal(api.renderValue("account_nature",{account_nature:null}),'<span class="dlp-operating-ellipsis" title="">—</span>');
 assert.match(api.renderValue("project",{project:"P",projection_conflicts:["project"]}),/待核对/);
 assert.doesNotMatch(api.renderValue("project",{project:"P",projection_conflicts:["remark"]}),/待核对/);
});
test("current columns export once in their visible order without hidden currency or technical IDs", async () => {
 let args;
 const root={DeepLinkERPPurchaseOrderExport:{downloadWorkbook(){},fetchNativeWorkbook:async (_root,input)=>{args=input;return {};}}};
 await create(root).exportCurrent({quick:{},providerOrderBy:"request_date desc",preferences:{columns:expectedColumns}});
 assert.deepEqual(JSON.parse(args.columns),expectedColumns.filter(field=>field!=="actions").map(field=>field==="source_id"?"display_source_id":field));
 assert.deepEqual(JSON.parse(args.filters),{quick_tab:"pending_work"});
});

test("original application number is readable and payment uncertainty is filterable", () => {
	assert.equal(api.filters({source_status: "付款待核对"}).source_status, "付款待核对");
	assert.match(api.renderValue("source_id", {source_id:"oa:technical", approval_no:"20260101001"}), /20260101001/);
	assert.equal(api.renderValue("source_id", {source_id:"oa:technical", approval_no:null}), '<span class="dlp-operating-ellipsis" title="">—</span>');
});
test("application number opens only a verified original DingTalk instance using the shared link builder", () => {
	const original = "https://aflow.dingtalk.com/dingtalk/pc/query/p.htm?procInstId=instance-1";
	const drawer = require("../deeplinkerp_branding/public/js/operating_expense_drawer.js")({});
	const list = create({DeepLinkERPOperatingExpenseDrawer: drawer});
	const html = list.renderValue("source_id", {approval_no:"NO<&", original_url:original});
	assert.match(html, /<a[^>]+href="dingtalk:\/\/dingtalkclient\/page\/link\?/);
	assert.match(html, /NO&lt;&amp;/);
	assert.ok(html.includes(drawer.dingTalkOriginalLink(original).desktop_url.replaceAll("&", "&amp;")));
	for (const invalid of ["javascript:alert(1)", "https://evil.example/?procInstId=instance-1", "https://aflow.dingtalk.com/?procInstId=one&procInstId=two", null])
		assert.doesNotMatch(list.renderValue("source_id", {approval_no:"NO", original_url:invalid}), /<a|href=/);
	assert.doesNotMatch(list.renderValue("actions", {source_id:"source:1", original_url:original}), /钉钉原单|>原单</);
});

test("operating filters retain only the exact backend predicates without type inference", () => {
	assert.equal(typeof api.filters, "function");
	assert.deepEqual(
		api.filters({
			keyword: "编号",
			company: "Legal",
			company_review: "stale",
			application_type: "payment",
			applicant: "A",
			source_status: "部分付款",
			approval_state: "eligible",
			date_from: "2026-10-01",
			date_to: "2026-10-06",
			injected: "x",
		}),
		{
			keyword: "编号",
			company: "Legal",
			company_review: "stale",
			application_type: "payment",
			applicant: "A",
			source_status: "部分付款",
			approval_state: "eligible",
			date_from: "2026-10-01",
			date_to: "2026-10-06",
		}
	);
	assert.throws(() => api.filters({ application_type: "报销付款混合" }), /类型/);
	assert.throws(() => api.filters({ company_review: "guess" }), /归属/);
	assert.throws(() => api.filters({ source_status: "partially_paid" }), /状态/);
	assert.throws(() => api.filters({ source_status: "partial" }), /状态/);
});
test("invalid company review filter translates its field label", () => {
	const localized = create({__: text => ({"{0}无效":"{0} is invalid", "公司归属状态":"Company attribution status"}[text] || text)});
	assert.throws(() => localized.filters({company_review:"guess"}), /Company attribution status is invalid/);
});
test("operating provider requests a stable whitelisted sort and the selected page only", () => {
	assert.equal(typeof api.request, "function");
	const req = api.request({
		quick: { company: "C" },
		providerOrderBy: "amount asc",
		page: 2,
		pageSize: 500,
	});
	assert.equal(
		req.method,
		"deeplinkerp_branding.services.operating_expenses.get_operating_expenses"
	);
	assert.deepEqual(JSON.parse(req.args.filters), { quick_tab: "pending_work", company: "C" });
	assert.equal(req.args.start, 1000);
	assert.equal(req.args.page_length, 500);
	assert.equal(req.args.order_by, "amount asc");
	assert.throws(
		() =>
			api.request({
				quick: {},
				providerOrderBy: "amount asc; delete",
				page: 0,
				pageSize: 100,
			}),
		/排序/
	);
});
test("operating amount rendering distinguishes missing, malformed, exact zero, and decimal rounding", () => {
	assert.equal(typeof api.money, "function");
	assert.equal(api.money(null), "—");
	assert.equal(api.money(""), "—");
	assert.equal(api.money("NaN"), "—");
	assert.equal(api.money(Infinity), "—");
	assert.equal(api.money("0"), "0.00");
	assert.equal(api.money("12.345"), "12.35");
	assert.equal(api.money("9007199254740993.01"), "9,007,199,254,740,993.01");
});
test("source classification and approval display never imply native posting", () => {
	assert.equal(typeof api.typeLabel, "function");
	assert.equal(api.typeLabel("unclassified"), "待分类");
	assert.equal(api.typeLabel("unknown reimbursement"), "待分类");
	assert.equal(api.typeLabel("payment"), "付款申请");
	assert.equal(api.typeLabel("reimbursement"), "费用报销");
	assert.equal(api.approvalLabel("eligible"), "来源审批通过");
	assert.equal(api.approvalLabel("agree"), "需复核");
});
test("summary renders every server currency separately and calls out unknown or incomplete values", () => {
	assert.equal(typeof api.summary, "function");
	const html = api.summary({
		providerPayload: {
			currency_totals: {
				CNY: { amount: "10", paid_amount: "2", pending_amount: "8", anomaly_count: 2 },
				USD: { amount: "3", paid_amount: "0", pending_amount: "3", incomplete: true },
				EUR: { amount: null, paid_amount: null, pending_amount: null, incomplete: true },
				JPY: { amount: "100", paid_amount: null, pending_amount: null, known_totals: { paid_amount: "20", pending_amount: "30" }, incomplete: true },
				"": { amount: "0", incomplete: true },
			},
		},
	});
	assert.match(html, /CNY.*10\.00.*累计已付.*2\.00.*剩余待付.*8\.00/);
	assert.match(html, /USD.*3\.00/);
	assert.match(html, /不完整/);
	assert.match(html, /币种未明确/);
	const unknown = html.split("<br>").find((row) => row.startsWith("EUR"));
	assert.match(unknown, /申请金额 — · 累计已付 — · 剩余待付 —/);
	assert.doesNotMatch(unknown, /0\.00/);
	assert.match(html, /USD.*累计已付.*0\.00/);
	assert.match(html, /JPY.*累计已付 —.*已知 20\.00.*剩余待付 —.*已知 30\.00/);
	assert.match(html, /异常付款 2.*未计入正常余额/);
});
test("company overview separates uncertain attribution and does not present applications as booked expenses", () => {
	const html = api.summary({quick:{}, providerPayload:{
		currency_totals:{}, source_scope:{mode:"oa_cashier",start_date:"2026-01-01",end_date:"2026-12-31",process_templates:7},
		review_counts:{unmapped:1,stale:1,payment_unknown:2,approval_evidence_missing:2},
		company_overview:[
			{company:"A<&",count:1,currency_counts:{CNY:1},currencies:{CNY:{amount:"100",paid_amount:"20"}}},
			{company:null,count:2,currency_counts:{CNY:2},currencies:{CNY:{amount:"500",paid_amount:null,known_totals:{paid_amount:"10"}}}},
		],
	}});
	assert.match(html,/A&lt;&amp;.*100\.00.*20\.00/);
	assert.doesNotMatch(html,/A<&/);
	assert.match(html,/归属待核对.*500\.00.*已知.*10\.00/);
	assert.match(html,/历史公司待复核 1/);
	assert.match(html,/页头当前公司不限制此页/);
	assert.match(html,/中国区域 2026-01-01 至 2026-12-31 的 7 类/);
	assert.match(html,/入账金额请以总账为准/);
	assert.match(api.renderValue("company",{company:"A",company_review_state:"stale"}),/A.*历史归属待复核/);
	assert.match(api.renderValue("company",{company:null,company_review_state:"unmapped"}),/公司待确认/);
	assert.match(create({DeepLinkERPOperatingExpenseDrawer:{isManager:()=>true}}).renderValue("actions",{source_id:"S",company_review_state:"stale"}),/data-tab="request"[^>]*>复核公司/);
	assert.doesNotMatch(create({DeepLinkERPOperatingExpenseDrawer:{isManager:()=>false}}).renderValue("actions",{source_id:"S",company_review_state:"stale"}),/复核公司/);
});
test("full-filter Excel snapshots filters, sort and ordered supported columns before lazy loading", async () => {
	let resume, args, method, downloaded;
	const root = { frappe: { require: () => new Promise((resolve) => (resume = resolve)) } };
	const exportAPI = create(root);
	assert.equal(typeof exportAPI.exportCurrent, "function");
	const c = {
		quick: { company: "C", keyword: "old" },
		providerOrderBy: "source_id asc",
		page: 4,
		pageSize: 20,
		preferences: { columns: ["summary", "actions", "finance_status", "amount"] },
	};
	const pending = exportAPI.exportCurrent(c);
	c.quick.keyword = "new";
	c.providerOrderBy = "amount desc";
	c.preferences.columns = ["payee_name"];
	root.DeepLinkERPPurchaseOrderExport = {
		fetchNativeWorkbook: async (_root, value, cmd) => {
			args = value;
			method = cmd;
			return new Uint8Array([1]);
		},
		downloadWorkbook: (_root, _bytes, title) => (downloaded = title),
	};
	resume();
	await pending;
	assert.deepEqual(JSON.parse(args.filters), { quick_tab: "pending_work", company: "C", keyword: "old" });
	assert.equal(args.order_by, "source_id asc");
	assert.deepEqual(JSON.parse(args.columns), ["summary", "finance_status", "amount"]);
	assert.equal(args.start, undefined);
	assert.equal(args.page_length, undefined);
	assert.equal(
		method,
		"deeplinkerp_branding.services.operating_expenses.export_operating_expenses"
	);
	assert.equal(downloaded, "运营支出");
});
test("list opens source drawer directly and preserves raw backend finance status", () => {
	assert.equal(typeof api.renderValue, "function");
	assert.match(api.renderValue("actions", { source_id: "A<&" }), /dlp-operating-open/);
	assert.doesNotMatch(api.renderValue("actions", { source_id: "A<&" }), /data-source="A<&"/);
	assert.match(
		api.renderValue("finance_status", { finance_status: "草稿已生成" }),
		/草稿已生成/
	);
	assert.equal(api.renderValue("amount", { amount: "0" }), "0.00");
	assert.equal(api.renderValue("amount", { amount: "1", currency: "MXN" }), "1.00");
});
test("operating Page reuses the shared readonly engine with scalar permission aliases and fixed source filters", () => {
	let config;
	const root = {
		DeepLinkERPCompactList: {
			create: (value) => {
				config = value;
				return { mountPage() {} };
			},
		},
	};
	const a = create(root);
	assert.equal(typeof a.grid, "function");
	a.grid();
	assert.equal(config.doctype, "Operating Expense Source");
	assert.equal(config.pageRoute, "operating-expenses");
	assert.equal(config.defaultSort, "request_date desc");
	assert.deepEqual(config.provider.freezeFields, ["source_id"]);
	assert.equal(config.freezeSelection, false);
	assert.equal(config.hideSequence, true);
	assert.equal(config.backupMigratedPreferences, true);
	assert.equal(config.disabledSelectionLabel,"本页不提供批量付款");
	assert.equal(config.pageFieldMap.finance_status, "issues");
	assert.equal(config.pageFieldMap.actions, "source_id");
	for(const field of ["account_nature","needed_payment_date","general_manager_approval","remark","project"]) assert.equal(config.pageFieldMap[field],"issues");
	assert.equal(
		config.controls.find((c) => c.fieldname === "source_status").options,
		"\n未付款\n部分付款\n已付款\n付款待核对"
	);
	assert.equal(config.controls.find((c) => c.fieldname === "company").fieldtype, "Link");
	assert.ok(config.controls.every((c) => c.permission_field));
	assert.ok(config.provider.exportCurrent);
});
test("shared column settings opens for a provider whose primary identifier is source_id and virtual columns are permission aliases", async () => {
	const shared = require("../deeplinkerp_branding/public/js/compact_list.js"),
		handlers = new Map();
	let dialogHTML = "", renderedHTML = "", summaryParent = "";
	const initialFilters = new Map();
	const tabText = new Map(), storage = new Map();
	const preferenceKey = shared.create({doctype:"Operating Expense Source",columns:[]}).preferenceKey("qa","finance");
	const oldLayout = {version:2,density:"standard",columns:["source_status","source_id","amount"]};
	const legacy = {density:"tight",columns:["request_date"]};
	storage.set(preferenceKey,JSON.stringify(oldLayout));
	storage.set(`${preferenceKey}:backup:legacy`,JSON.stringify(legacy));
	let
		shown = 0;
	class Surface {
		constructor(key = "") {
			this.key = key;
			this.length = 1;
		}
		find(key) {
			return new Surface(key);
		}
		first() {
			return this;
		}
		parent(selector) {
			return new Surface(selector);
		}
		html(value) {
			if (this.key === "dialog") dialogHTML = value;
			if(this.key.includes('class="result"')) renderedHTML = value;
			return this;
		}
		text(value) {
			tabText.set(this.key,value);
			return this;
		}
		append() {
			return this;
		}
		appendTo(target) {
			if (this.key.includes('class="dlp-po-summary"')) summaryParent = target.key;
			return this;
		}
		prependTo() {
			return this;
		}
		insertBefore() {
			return this;
		}
		insertAfter() {
			return this;
		}
		addClass() {
			return this;
		}
		removeClass() {
			return this;
		}
		toggleClass() {
			return this;
		}
		hide() {
			return this;
		}
		show() {
			return this;
		}
		remove() {
			return this;
		}
		off() {
			return this;
		}
		on(name, ...args) {
			handlers.set(this.key + ":" + name, args.at(-1));
			return this;
		}
		attr() {
			return this;
		}
		prop() {
			return this;
		}
		val() {
			return this;
		}
	}
	const root = {
		$: markup => new Surface(markup),
		localStorage: {getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value)},
		DeepLinkERPCompactList: shared,
		DeepLinkERPOperatingExpenseDrawer: { isManager: () => false },
		document: { body: { classList: { toggle() {} } } },
		frappe: {
			datetime: {get_today: () => "2026-11-13", add_days: () => "2026-05-18"},
			boot: {sitename:"qa"},
			get_route: () => ["operating-expenses"],
			session: { user: "finance" },
			router: { on() {} },
			get_meta: () => ({ fields: api.columns.map(col=>({fieldname:col.fieldname})).concat({fieldname:"issues"}) }),
			model: { can_export: () => false },
			ui: {
				Dialog: class {
					constructor() {
						this.fields_dict = { columns: { $wrapper: new Surface("dialog") } };
					}
					show() {
						shown++;
					}
					hide() {}
				},
				form: { make_control: ({df}) => {
					let value = df.initial_value ?? "";
					initialFilters.set(df.fieldname, value);
					return {get_value: () => value, set_value: async next => {value = next;}};
				}},
			},
		},
	};
	const c = create(root).grid().mountPage({ main: new Surface(), wrapper: new Surface() }, root);
	assert.doesNotThrow(() => handlers.get(".dlp-po-columns:click.dlpPO")());
	assert.equal(shown, 1);
	assert.match(dialogHTML, /钉钉申请单号/);
	assert.match(dialogHTML, /凭证状态/);
	assert.match(dialogHTML, /操作/);
	assert.doesNotMatch(dialogHTML, /data-field="name"/);
	assert.deepEqual(c.preferences.columns, expectedColumns);
	assert.equal(c.preferences.density,"standard");
	assert.equal(c.quick.quick_tab,"pending_work");
	assert.deepEqual(c.quick, {quick_tab: "pending_work", date_from: "2026-05-18", date_to: "2026-11-13"});
	assert.equal(initialFilters.get("date_from"), "2026-05-18");
	assert.equal(initialFilters.get("date_to"), "2026-11-13");
	assert.equal(summaryParent, ".result-container", "totals scroll below rows instead of reducing the viewport");
	assert.deepEqual(JSON.parse(storage.get(`${preferenceKey}:backup:2`)),oldLayout);
	assert.deepEqual(JSON.parse(storage.get(`${preferenceKey}:backup:legacy`)),legacy);
	assert.equal(JSON.parse(storage.get(preferenceKey)).version,4);
	assert.doesNotMatch(renderedHTML,/data-fieldname="_sequence"/);
	assert.match(renderedHTML,/<input type="checkbox" disabled[^>]*本页不提供批量付款/);
	const frozen = () => [...renderedHTML.matchAll(/class="([^"]*)" data-fieldname="([^"]*)" style="([^"]*)"/g)].filter(match=>match[1].split(" ").includes("dlp-po-frozen"));
	assert.deepEqual(frozen().map(match=>match[2]),["source_id"]);
	assert.match(frozen()[0][3],/--dlp-po-left:0px;width:210px/);
	c.providerPayload={tab_counts:{pending_work:270,all:372,approvals_running:14,paid:96,reconciliation:307}};
	c.updateOperatingTabs();
	assert.equal(tabText.get('[data-quick-tab="pending_work"]'),"待办理 (270)");
	c.setColumns(["summary","source_id"]);
	assert.deepEqual(c.preferences.columns,["summary","source_id"]);
	assert.deepEqual(frozen().map(match=>match[2]),["source_id"],"moving the ID never pins preceding custom columns");
	assert.match(frozen()[0][3],/--dlp-po-left:0px/);
	assert.deepEqual(JSON.parse(storage.get(`${preferenceKey}:backup:2`)),oldLayout,"later custom layouts never overwrite the backup");
	assert.deepEqual(JSON.parse(storage.get(`${preferenceKey}:backup:legacy`)),legacy);
	c.quick.keyword = "old";
	c.quick.date_from = "2020-01-01";
	c.refresh = async () => {};
	await c.clearQuickFilters();
	assert.deepEqual(c.quick, {quick_tab: "pending_work", date_from: "2026-05-18", date_to: "2026-11-13"});
	assert.equal(c.controls.date_from.get_value(), "2026-05-18");
});

test("layout migration replaces legacy order once while preserving subsequent density and custom columns",()=>{
	let config;
	create({DeepLinkERPCompactList:{create:value=>{config=value;return {};}}}).grid();
	const engine=require("../deeplinkerp_branding/public/js/compact_list.js").create(config);
	const allowed=new Set(api.columns.map(col=>col.fieldname));
	const upgraded=engine.normalizePreferences({density:"standard",columns:["request_date","source_id","amount"]},allowed);
	assert.deepEqual(upgraded,{density:"standard",columns:expectedColumns,version:4});
	assert.deepEqual(engine.normalizePreferences({version:2,density:"standard",columns:["source_status","source_id"]},allowed),upgraded);
	assert.deepEqual(engine.normalizePreferences({...upgraded,columns:["summary","source_id"]},allowed),{density:"standard",columns:["summary","source_id"],version:4});
	assert.deepEqual(engine.normalizePreferences({version:3,density:"standard",columns:["summary","source_id"]},allowed),{density:"standard",columns:["summary","source_id"],version:4});
});
