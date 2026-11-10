const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = "../deeplinkerp_branding/public/js/operating_expense_drawer.js";
const create = fs.existsSync(require("node:path").join(__dirname, path))
	? require(path)
	: () => ({});
const detail = () => ({
	source: {
		source_id: "source:1",
		version: "v1",
		application_type: "payment",
		currency: "CNY",
		amount: "100.00",
		approvals: { eligibility: "eligible" },
		payments: [
			{ source_id: "pay:1", amount: "25.00", currency: "CNY", evidence_status: "recorded" },
		],
	},
	company: "C",
	events: [],
	mapping: {
		company: "C",
		party_type: "Supplier",
		party: "S",
		payable_account: "Payable",
		payable_exchange_rate: "1.000000",
		posting_date: "2026-10-01",
		classification: "租金",
		actual_incurred: true,
		no_existing_erp_coverage: true,
		recognition_mode: "new",
		expense_lines: [
			{
				account: "Rent",
				source_amount: "100.00",
				amount: "100.00",
				exchange_rate: "1.000000",
				cost_center: "Main",
			},
		],
		payments: {
			hidden: {
				bank_account: "Original",
				bank_amount: "5.00",
				payable_exchange_rate: "1.000000",
				bank_exchange_rate: "1.000000",
			},
		},
		approved_by: "someone",
		expense_fingerprint: "private",
	},
});
const recognizedDetail = (docstatus = 0) => {
	const d = detail();
	d.events = [{ journal_entry: "JE-expense", docstatus, operation: "expense" }];
	return d;
};
const api = create({});
test("drawer presents four concise sections and preserves raw source facts in a foldout", () => {
 const a=create({DeepLinkERPOperatingExpenses:{money:String,approvalLabel:()=>"审批通过",typeLabel:()=>"付款申请"}});
 const html=a.sourceHTML(detail());
 for(const tab of ["request","approvals","payments","vouchers"]) assert.match(html,new RegExp(`data-operating-pane="${tab}"`));
 assert.match(html,/dlp-operating-hero/); assert.match(html,/<details/); assert.match(html,/累计已付/);
});
test("drawer business title and one money strip show real progress with readonly historical payment cards", () => {
 const a=create({DeepLinkERPOperatingExpenses:require("../deeplinkerp_branding/public/js/operating_expenses.js")({}),DeepLinkERPOperatingPaymentPanel:require("../deeplinkerp_branding/public/js/operating_payment_panel.js")({})});
 const d=detail();
 Object.assign(d.source,{summary:"办公室服务费报销",approval_no:"AP-1",approval_state:"pending",source_status:"部分付款",amount:"1000.00",paid_amount:"300.00",pending_amount:"700.00",current_approver:"王出纳"});
 const html=a.sourceHTML(d);
 assert.match(html,/<h3>办公室服务费报销<\/h3>/);
 assert.equal((html.match(/class="dlp-operating-money-strip"/g)||[]).length,1);
 assert.match(html,/aria-valuenow="30"/);
 assert.match(html,/审批中/);
 assert.match(html,/dlp-operating-payment-card[^]*来源：历史付款/);
 assert.doesNotMatch(html,/办理付款|data-payment-delete/);
});
test("drawer application number is the original DingTalk link without a duplicate hero button", () => {
	const a=create({DeepLinkERPOperatingExpenses:require("../deeplinkerp_branding/public/js/operating_expenses.js")({})});
	const d=detail();d.source.approval_no="NO<&";d.source.original_url="https://aflow.dingtalk.com/?procInstId=instance-1";
	const hero=a.sourceHTML(d).split('</header>',1)[0];
	assert.match(hero, /<a[^>]+href="dingtalk:\/\/[^>]+>NO&lt;&amp;<\/a>/);
	assert.doesNotMatch(hero, /class="btn|dlp-operating-hero-actions|>钉钉原单</);
});
test("cashier approval decisions and reported payment state remain separately labelled", () => {
	const d = detail();
	d.source.source_status = "付款待核对";
	d.source.cashier_reported_payment_status = "已付款";
	d.source.approvals.raw = { cashier_general_manager_approval: "同意付款", cashier_finance_review: "已付款" };
	const a = create({ DeepLinkERPOperatingExpenses: { money: String, approvalLabel: () => "需复核" } });
	const html = a.sourceHTML(d);
	assert.match(html, /出纳总经理审批.*同意付款/);
	assert.match(html, /出纳原付款状态.*已付款/);
	assert.match(html, /付款状态.*付款待核对/);
});
function host(call) {
	return {
		frappe: {
			session: { user: "finance" },
			user_roles: ["Accounts User"],
			model: { can_create: () => true, can_read: () => true },
			call,
		},
	};
}
function drawer() {
	let alive = true;
	return {
		loadId: 0,
		busy: false,
		alive: () => alive,
		close: () => (alive = false),
		setBusy(value) {
			this.busy = value;
		},
		error() {},
	};
}
test("operating workflow distinguishes cancellable reads from saved mapping and voucher writes",async()=>{
 const modes=[],d=drawer();d.setBusy=(value,operation)=>{d.busy=value;if(value)modes.push(operation);};
 const a=create(host(async req=>({message:req.method.endsWith("get_operating_expense_detail")?detail():req.method.endsWith("save_mapping")?{mapping:detail().mapping}:{fingerprint:"preview",source_version:"v1",accounts:[]}})));
 const w=a.workflow(d,"source:1");
 await w.load();await w.save();await w.preview();await w.create();
 assert.deepEqual(modes,["read","write","read","write"]);
});

test("legal company save uses the existing write gate and preserves detail on failure", async () => {
	const d = drawer(), calls = [];
	const original = { ...detail(), company: null };
	const a = create(host(async (req) => {
		calls.push(req);
		if (req.method.endsWith("get_operating_expense_detail")) return { message: original };
		throw { _server_messages: JSON.stringify([JSON.stringify({ message: "操作冲突，法律公司未保存，请稍后重试。" })]) };
	}));
	const w = a.workflow(d, "source:1");
	await w.load();
	await assert.rejects(() => w.saveCompany("C"), /法律公司未保存/);
	assert.equal(w.detail, original);
	assert.equal(d.busy, false);
	assert.equal(calls.at(-1).args.company, "C");
	assert.equal(calls.at(-1).args.expected_source_version, "v1");
	assert.equal(calls.length, 2);
});

test("pending legal company save cannot dispatch twice or refresh a closed drawer", async () => {
	let finish;
	const d = drawer(), calls = [], invalidations = [];
	const a = create(host(async (req) => {
		calls.push(req);
		if (req.method.endsWith("get_operating_expense_detail")) return { message: detail() };
		return new Promise((resolve) => { finish = () => resolve({ message: { company: "C" } }); });
	}));
	const w = a.workflow(d, "source:1", { onInvalidate: () => invalidations.push(true) });
	await w.load();
	const saving = w.saveCompany("C");
	await assert.rejects(() => w.saveCompany("C"), /等待/);
	const count = invalidations.length;
	d.close();
	finish();
	assert.equal(await saving, undefined);
	assert.equal(invalidations.length, count);
	assert.equal(calls.length, 2);
});

test("source mapping uses exact decimal strings and preserves hidden payment terms", () => {
	assert.equal(typeof api.editSession, "function");
	const s = api.editSession(recognizedDetail());
	s.touchLine(0, "amount", "99.123456789");
	s.touchPayment("pay:1", "bank_amount", "25.000");
	const payload = s.mapping();
	assert.equal(payload.expense_lines[0].amount, "99.123456789");
	assert.equal(payload.payments["pay:1"].bank_amount, "25.000");
	assert.deepEqual(payload.payments.hidden, detail().mapping.payments.hidden);
	assert.equal(payload.approved_by, undefined);
	assert.equal(payload.expense_fingerprint, undefined);
	assert.equal(payload.application_type, undefined);
	assert.equal(detail().mapping.expense_lines[0].amount, "100.00");
});
test("new mappings require explicit recognition choice and default actual and ERP coverage confirmations to false", () => {
	assert.equal(typeof api.editSession, "function");
	const d = detail();
	d.mapping = null;
	const s = api.editSession(d);
	assert.equal(s.mapping().actual_incurred, false);
	assert.equal(s.mapping().no_existing_erp_coverage, false);
	assert.equal(s.mapping().existing_erp_coverage_confirmed, false);
	assert.equal(s.mapping().posting_date, "");
	assert.equal(s.mapping().payable_exchange_rate, "");
	assert.equal(s.mapping().recognition_mode, "");
	assert.equal(s.ready(), false);
	s.touch("actual_incurred", 1);
	s.touch("recognition_mode", "existing");
	s.touch("existing_erp_coverage_confirmed", 1);
	assert.equal(s.ready(), true);
});
test("only unclassified sources can receive explicit audited application type", () => {
	assert.equal(typeof api.editSession, "function");
	const s = api.editSession(detail());
	assert.throws(() => s.touch("application_type", "reimbursement"), /待分类/);
	const d = detail();
	d.source.application_type = "unclassified";
	d.mapping = null;
	const unknown = api.editSession(d);
	unknown.touch("application_type", "reimbursement");
	assert.equal(unknown.mapping().application_type, "reimbursement");
	assert.equal(unknown.mapping().party_type, "Employee");
	assert.equal(unknown.mapping().party, "");
});
test("clearing an unclassified type choice clears party eligibility and never guesses a master type", () => {
	const d = detail();
	d.source.application_type = "unclassified";
	d.mapping = null;
	const s = api.editSession(d);
	s.touch("actual_incurred", true);
	s.touch("recognition_mode", "new");
	s.touch("no_existing_erp_coverage", true);
	assert.equal(s.ready(), false);
	s.touch("application_type", "payment");
	s.touch("party", "S");
	assert.equal(s.ready(), true);
	assert.doesNotThrow(() => s.touch("application_type", ""));
	assert.equal(s.mapping().party_type, "");
	assert.equal(s.mapping().party, "");
	assert.equal(s.ready(), false);
});
test("safe original evidence links accept canonical DingTalk HTTPS only", () => {
	assert.equal(typeof api.safeOriginalURL, "function");
	assert.equal(
		api.safeOriginalURL("https://aflow.dingtalk.com/dingtalk/mobile/homepage.htm?id=1"),
		"https://aflow.dingtalk.com/dingtalk/mobile/homepage.htm?id=1"
	);
	for (const url of [
		"javascript:alert(1)",
		"http://aflow.dingtalk.com/a",
		"https://evil.test/a",
		"https://dingtalk.com.evil.test",
		"https://user:secret@aflow.dingtalk.com/a",
	])
		assert.equal(api.safeOriginalURL(url), null);
});
test("original approval links use the established DingTalk client route and one verified instance", () => {
	const instance = "approval_ID-123";
	const official = `https://aflow.dingtalk.com/dingtalk/mobile/homepage.htm?procInstId=${instance}`;
	assert.equal(typeof api.dingTalkOriginalLink, "function");
	const link = api.dingTalkOriginalLink(official);
	assert.equal(link.official_url, official);
	assert.equal(link.mobile_url, `https://aflow.dingtalk.com/dingtalk/mobile/homepage.htm?showmenu=false&dd_progress=false#/approval?procInstId=${instance}`);
	assert.equal(link.desktop_url, `dingtalk://dingtalkclient/page/link?url=${encodeURIComponent(link.mobile_url)}&pc_slide=true`);
	assert.equal(api.dingTalkOriginalLink(link.mobile_url).desktop_url, link.desktop_url);
	for (const value of [
		"javascript:alert(1)",
		"https://evil.test/?procInstId=x",
		`${official}&procInstId=other`,
		`${official}#/approval?procInstId=other`,
		"https://aflow.dingtalk.com/?procInstId=%3Cscript%3E",
		"https://aflow.dingtalk.com/?id=1",
	]) assert.equal(api.dingTalkOriginalLink(value), null);
	const d = detail();
	d.source.original_url = official;
	const html = create({DeepLinkERPOperatingExpenses: {money: String, approvalLabel: () => "审批通过"}}).sourceHTML(d);
	assert.match(html, /href="dingtalk:\/\/dingtalkclient\/page\/link\?/);
	assert.match(html, /需安装并登录钉钉/);
	assert.match(html, /网页原单链接/);
});
test("finance eligibility combines finance role and native Journal Entry create/read permission", () => {
	assert.equal(typeof api.canFinance, "function");
	assert.equal(create(host()).canFinance(), true);
	assert.equal(
		create({
			...host(),
			frappe: { ...host().frappe, user_roles: ["System Manager"] },
		}).canFinance(),
		false
	);
	assert.equal(
		create({
			...host(),
			frappe: { ...host().frappe, model: { can_create: () => false, can_read: () => true } },
		}).canFinance(),
		false
	);
	assert.equal(
		create({
			...host(),
			frappe: { ...host().frappe, model: { can_create: () => 0, can_read: () => 1 } },
		}).canFinance(),
		false
	);
	assert.equal(
		create({
			...host(),
			frappe: { ...host().frappe, model: { can_create: () => 1, can_read: () => 0 } },
		}).canFinance(),
		false
	);
});
test("saved mapping then preview is the only path to a fingerprint-bound Journal Entry draft", async () => {
	const requests = [];
	const h = host(async (req) => {
		requests.push(req);
		return {
			message: req.method.endsWith("get_operating_expense_detail")
				? detail()
				: req.method.endsWith("save_mapping")
				? { mapping: detail().mapping }
				: req.method.endsWith("preview_voucher")
				? {
						fingerprint: "p1",
						source_version: "v1",
						accounts: [{ account: "Rent", debit: "100", credit: "0" }],
				  }
				: { journal_entry: "JE-1", docstatus: 0 },
		};
	});
	const a = create(h);
	assert.equal(typeof a.workflow, "function");
	const d = drawer(),
		w = a.workflow(d, "source:1");
	await w.load();
	await assert.rejects(() => w.create(), /预览/);
	await w.save();
	await w.preview();
	await w.create();
	assert.equal(
		requests.at(-1).method,
		"deeplinkerp_branding.services.operating_expenses.create_voucher_draft"
	);
	assert.deepEqual(requests.at(-1).args, { source_id: "source:1", expected_fingerprint: "p1" });
	assert.ok(requests.every((r) => r.type === "POST"));
	assert.ok(requests.every((r) => !/(submit|record_payment|GL Entry)/.test(r.method)));
});
test("changing any mapping input immediately invalidates a previous preview and create eligibility", async () => {
	const h = host(async (req) => ({
		message: req.method.endsWith("get_operating_expense_detail")
			? detail()
			: { fingerprint: "old", source_version: "v1", accounts: [] },
	}));
	const a = create(h);
	assert.equal(typeof a.workflow, "function");
	const w = a.workflow(drawer(), "source:1");
	await w.load();
	await w.preview();
	assert.equal(w.canCreate(), true);
	w.session.touch("classification", "changed");
	assert.equal(w.canCreate(), false);
	await assert.rejects(() => w.create(), /预览/);
});
test("a response from a closed or superseded drawer cannot paint, restore busy, or chain another write", async () => {
	let finish,
		paint = 0;
	const d = drawer(),
		h = host(() => new Promise((resolve) => (finish = resolve)));
	const a = create(h);
	assert.equal(typeof a.workflow, "function");
	const w = a.workflow(d, "source:1", { onDetail: () => paint++ });
	const pending = w.load();
	d.close();
	finish({ message: detail() });
	await pending;
	assert.equal(paint, 0);
	assert.equal(w.session, null);
	assert.equal(d.busy, true);
});
test("a preview resolving after mapping edits is discarded before it can enable creation", async () => {
	let finish;
	const h = host((req) =>
		req.method.endsWith("get_operating_expense_detail")
			? Promise.resolve({ message: detail() })
			: new Promise((resolve) => (finish = resolve))
	);
	const a = create(h);
	assert.equal(typeof a.workflow, "function");
	const w = a.workflow(drawer(), "source:1");
	await w.load();
	const pending = w.preview();
	w.session.touch("payable_exchange_rate", "2");
	finish({ message: { fingerprint: "old", source_version: "v1" } });
	await pending;
	assert.equal(w.canCreate(), false);
});
test("existing coverage mode permits explicit link after preview and blocks expense creation", async () => {
	const d = detail();
	d.mapping.recognition_mode = "existing";
	d.mapping.no_existing_erp_coverage = false;
	d.mapping.existing_erp_coverage_confirmed = true;
	const calls = [];
	const a = create(
		host(async (req) => {
			calls.push(req);
			return {
				message: req.method.endsWith("get_operating_expense_detail")
					? d
					: req.method.endsWith("preview_voucher")
					? { fingerprint: "existing", source_version: "v1", accounts: [] }
					: { journal_entry: "JE-old", docstatus: 1 },
			};
		})
	);
	assert.equal(typeof a.workflow, "function");
	const w = a.workflow(drawer(), "source:1");
	await w.load();
	await w.preview();
	assert.equal(w.canCreate(), false);
	await assert.rejects(() => w.create(), /已有|预览/);
	await w.link("JE-old");
	assert.deepEqual(calls.at(-1).args, {
		source_id: "source:1",
		journal_entry: "JE-old",
		expected_fingerprint: "existing",
	});
});
test("settlement uses the actual source payment identifier and keeps expense event separate", async () => {
	const calls = [];
	const a = create(
		host(async (req) => {
			calls.push(req);
			return {
				message: req.method.endsWith("get_operating_expense_detail")
					? recognizedDetail()
					: req.method.endsWith("preview_voucher")
					? {
							fingerprint: "payp",
							source_version: "v1",
							accounts: [],
							settlement_state: "尚未核销",
					  }
					: { journal_entry: "JE-pay", docstatus: 0, settlement_state: "尚未核销" },
			};
		})
	);
	assert.equal(typeof a.workflow, "function");
	const w = a.workflow(drawer(), "source:1");
	await w.load();
	await w.preview("pay:1");
	await w.create("pay:1");
	assert.deepEqual(calls.at(-1).args, {
		source_id: "source:1",
		payment_source_id: "pay:1",
		expected_fingerprint: "payp",
	});
});
test("preview accepts only the returned version of the loaded source for expense and settlement", async () => {
	for (const payment of [undefined, "pay:1"]) {
		let sourceVersion = "v1",
			painted = 0;
		const a = create(
			host(async (req) => ({
				message: req.method.endsWith("get_operating_expense_detail")
					? recognizedDetail()
					: { fingerprint: "p", source_version: sourceVersion, accounts: [] },
			}))
		);
		const w = a.workflow(drawer(), "source:1", { onPreview: () => painted++ });
		await w.load();
		await w.preview(payment);
		assert.equal(w.canCreate(payment), true);
		sourceVersion = "v2";
		await assert.rejects(() => w.preview(payment), /刷新/);
		assert.equal(w.previewResult(payment), null);
		assert.equal(w.canCreate(payment), false);
		assert.equal(painted, 1, "A stale response cannot paint a valid-looking preview");
		sourceVersion = "v1";
		await w.preview(payment);
		assert.equal(w.canCreate(payment), true);
	}
});
test("saved mapping without a valid expense recognition cannot edit or preview settlement terms", async () => {
	for (const events of [
		[],
		[{ operation: "expense", docstatus: 0, journal_entry: "" }],
		[{ operation: "payment", docstatus: 0, journal_entry: "JE-payment" }],
		[{ operation: "expense", docstatus: 0, journal_entry: "JE-expense", issue: "unreadable" }],
		[{ operation: "expense", docstatus: 2, journal_entry: "JE-expense" }],
	]) {
		const d = detail();
		d.events = events;
		const calls = [];
		const a = create(
			host(async (req) => {
				calls.push(req);
				return {
					message: req.method.endsWith("get_operating_expense_detail")
						? d
						: { fingerprint: "p", source_version: "v1", accounts: [] },
				};
			})
		);
		const w = a.workflow(drawer(), "source:1");
		await w.load();
		assert.equal(a.canEditPayment(d), false);
		assert.throws(() => w.session.touchPayment("pay:1", "bank_amount", "25"), /费用确认凭证/);
		await assert.rejects(() => w.preview("pay:1"), /费用确认凭证/);
		assert.equal(w.canCreate("pay:1"), false);
		assert.equal(
			calls.length,
			1,
			"No settlement preview is dispatched before recognition exists"
		);
	}
});
test("posted expense recognition blocks repeat expense preview but permits recorded settlement drafts", async () => {
	const d = recognizedDetail(1),
		calls = [];
	d.events.push({
		journal_entry: "JE-prior-payment",
		docstatus: 1,
		operation: "payment",
		payment_source_id: "pay:1",
	});
	d.source.payments.push({
		source_id: "pay:2",
		amount: "10.00",
		currency: "CNY",
		evidence_status: "recorded",
	});
	const a = create(
		host(async (req) => {
			calls.push(req);
			return {
				message: req.method.endsWith("get_operating_expense_detail")
					? d
					: req.method.endsWith("preview_voucher")
					? { fingerprint: "pay", source_version: "v1", accounts: [] }
					: { journal_entry: "JE-payment", docstatus: 0 },
			};
		})
	);
	const w = a.workflow(drawer(), "source:1");
	await w.load();
	assert.equal(a.canEditPayment(d), true);
	await assert.rejects(() => w.preview(), /已记账/);
	assert.equal(w.canCreate(), false);
	assert.equal(w.canLink(), false);
	assert.equal(calls.length, 1);
	await w.preview("pay:2");
	assert.equal(w.canCreate("pay:2"), true);
	const result = await w.create("pay:2");
	assert.equal(result.docstatus, 0);
	assert.ok(calls.at(-1).method.endsWith("create_voucher_draft"));
	assert.equal(calls.at(-1).args.payment_source_id, "pay:2");
	assert.ok(calls.every((req) => !/submit|record_payment/.test(req.method)));
});
test("known posted cancelled or problematic payment events reject settlement preview before dispatch", async () => {
	for (const state of [
		{ docstatus: 1 },
		{ docstatus: 2 },
		{ docstatus: 0, issue: "unreadable" },
	]) {
		const d = recognizedDetail(),
			calls = [];
		d.events.push({
			journal_entry: "JE-payment",
			operation: "payment",
			payment_source_id: "pay:1",
			...state,
		});
		const a = create(
			host(async (req) => {
				calls.push(req);
				return {
					message: req.method.endsWith("get_operating_expense_detail")
						? d
						: { fingerprint: "p", source_version: "v1", accounts: [] },
				};
			})
		);
		const w = a.workflow(drawer(), "source:1");
		await w.load();
		await assert.rejects(() => w.preview("pay:1"), /本笔结算/);
		assert.equal(w.canCreate("pay:1"), false);
		assert.equal(calls.length, 1);
	}
});
test("a posted event for this payment blocks creation even when a matching preview was cached", async () => {
	const d = recognizedDetail(),
		calls = [];
	const a = create(
		host(async (req) => {
			calls.push(req);
			return {
				message: req.method.endsWith("get_operating_expense_detail")
					? d
					: { fingerprint: "p", source_version: "v1", accounts: [] },
			};
		})
	);
	const w = a.workflow(drawer(), "source:1");
	await w.load();
	await w.preview("pay:1");
	assert.equal(w.canCreate("pay:1"), true);
	d.events.push({
		journal_entry: "JE-payment",
		docstatus: 1,
		operation: "payment",
		payment_source_id: "pay:1",
	});
	assert.equal(w.canCreate("pay:1"), false);
	await assert.rejects(() => w.create("pay:1"), /预览/);
	assert.equal(
		calls.length,
		2,
		"The cached fingerprint cannot dispatch a draft write for the posted payment"
	);
});
test("source evidence view separates original approved facts, cashier payment evidence, attachments and native JE history", () => {
	const a = create({
		DeepLinkERPOperatingExpenses:
			require("../deeplinkerp_branding/public/js/operating_expenses.js")({}),
	});
	assert.equal(typeof a.sourceHTML, "function");
	const d = detail();
	d.source.application_type_raw = "原文<&";
	d.source.approval_no = "AP<&-001";
	d.source.dingding_id = "DING<&-002";
	d.source.source_request_id = "REQ<&-003";
	d.source.original_source_amount = "98.765";
	d.source.original_source_currency = "USD";
	d.source.source_sheet = "BU归档";
	d.source.attachments = [
		{ source_id: "att:1", filename: "proof<&.pdf", payment_source_id: "pay:1" },
		{ source_id: "att:2", filename: "preview.pdf", content_quality: "preview" },
		{ source_id: "att:3", filename: "unknown.pdf", content_quality: "unknown" },
	];
	d.events = [
		{
			journal_entry: "JE-1",
			docstatus: 0,
			operation: "payment",
			payment_source_id: "pay:1",
			settlement_state: "尚未核销",
		},
	];
	const html = a.sourceHTML(d);
	assert.match(html, /preview\.pdf[^]*预览件/);
	assert.match(html, /unknown\.pdf[^]*文件质量待核对/);
	assert.match(html, /原始批准金额.*98\.77 USD/);
	assert.match(html, /原文&lt;&amp;/);
	assert.match(html, /原始审批编号.*AP&lt;&amp;-001/);
	assert.match(html, /原始钉钉实例编号.*DING&lt;&amp;-002/);
	assert.match(html, /原始来源请求编号.*REQ&lt;&amp;-003/);
	assert.equal(d.source.approval_no, "AP<&-001");
	assert.equal(d.source.dingding_id, "DING<&-002");
	assert.equal(d.source.source_request_id, "REQ<&-003");
	assert.match(html, /请款网站/);
	assert.match(html, /实际付款证据/);
	assert.match(html, /data-attachment="att:1"/);
	assert.match(html, /尚未核销/);
	assert.match(html, /草稿 · 未记账/);
	assert.doesNotMatch(html, /Bearer|api_token/);
});
test("derived settlement status is translated without altering raw source evidence", () => {
	const a = create({
		__: (text) => text === "尚未核销" ? "Pendiente de conciliación" : text,
		DeepLinkERPOperatingExpenses:
			require("../deeplinkerp_branding/public/js/operating_expenses.js")({}),
	});
	const d = recognizedDetail();
	d.source.application_type_raw = "尚未核销";
	d.events.push({ journal_entry: "JE-pay", docstatus: 0, operation: "payment",
		payment_source_id: "pay:1", settlement_state: "尚未核销" });
	const preview = { company: "C", posting_date: "2026-10-01",
		settlement_state: "尚未核销", accounts: [] };
	const original = JSON.stringify({ d, preview });
	assert.match(a.sourceHTML(d), /Pendiente de conciliación/);
	assert.match(a.sourceHTML(d), /原始申请类型.*尚未核销/);
	assert.match(a.previewHTML(preview), /Pendiente de conciliación/);
	assert.equal(JSON.stringify({ d, preview }), original);
});
test("native preview renders all account-currency and base-currency rows with explicit rounding and date", () => {
	const a = create({
		DeepLinkERPOperatingExpenses:
			require("../deeplinkerp_branding/public/js/operating_expenses.js")({}),
	});
	assert.equal(typeof a.previewHTML, "function");
	const html = a.previewHTML({
		company: "Legal",
		posting_date: "2026-10-01",
		base_rounding: "0.01",
		accounts: [
			{
				account: "A<&",
				account_currency: "USD",
				exchange_rate: "7.123456",
				debit_in_account_currency: "3.00",
				credit_in_account_currency: "0",
				debit: "21.37",
				credit: "0",
				cost_center: "Main",
				project: "P",
			},
		],
	});
	assert.match(html, /2026-10-01/);
	assert.match(html, /A&lt;&amp;/);
	assert.match(html, /USD/);
	assert.match(html, /7\.123456/);
	assert.match(html, /21\.37/);
	assert.match(html, /0\.01/);
});
test("native preview amounts show two decimals while missing opposite sides stay unknown and rates retain precision", () => {
	const a = create({
		DeepLinkERPOperatingExpenses:
			require("../deeplinkerp_branding/public/js/operating_expenses.js")({}),
	});
	const html = a.previewHTML({
		accounts: [
			{
				account: "Rent",
				account_currency: "CNY",
				exchange_rate: "1.000000001",
				debit_in_account_currency: "100",
				credit_in_account_currency: "0",
				debit: "100.000",
				credit: null,
			},
		],
		base_rounding: [{ account: "Rent", exact_base: "100", rounded_base: "100.00" }],
	});
	assert.match(html, /<td>100\.00<\/td><td>0\.00<\/td><td>100\.00<\/td><td>—<\/td>/);
	assert.match(html, /1\.000000001/);
	assert.match(html, /无舍入差额/);
	assert.doesNotMatch(html, /\[\{&quot;account&quot;/);
});
test("rounding presentation subtracts exact decimal text without float loss and preserves evidence in folded detail", () => {
	const a = create({
		DeepLinkERPOperatingExpenses:
			require("../deeplinkerp_branding/public/js/operating_expenses.js")({}),
	});
	const html = a.previewHTML({
		accounts: [],
		base_rounding: [
			{
				account: "A<&",
				exact_base: "9007199254740993.014",
				rounded_base: "9007199254740993.01",
			},
			{ account: "tiny", exact_base: "1.00E-11", rounded_base: "0.00" },
			{ account: "missing", exact_base: null, rounded_base: "0.00" },
		],
	});
	assert.match(html, /A&lt;&amp;.*0\.00/);
	assert.match(html, /title="[^"]*-0\.004/);
	assert.match(html, /差额不足 0\.01/);
	assert.match(html, /<details/);
	assert.match(html, /9007199254740993\.014/);
	assert.match(html, /-0\.00000000001/);
	assert.match(html, /舍入依据不完整，需复核/);
	assert.doesNotMatch(html, /无舍入差额/);
});
test("source and settlement controls use native Link/Date/Data/Check with explicit saved exchange-rate facts", () => {
	assert.equal(typeof api.controlDefinitions, "function");
	const definitions = api.controlDefinitions(detail());
	assert.equal(definitions.find((df) => df.fieldname === "posting_date").fieldtype, "Date");
	assert.equal(definitions.find((df) => df.fieldname === "party").options, "Supplier");
	assert.equal(
		definitions.find((df) => df.fieldname === "payable_exchange_rate").fieldtype,
		"Data"
	);
	assert.ok(
		definitions
			.filter((df) => /incurred|coverage/.test(df.fieldname))
			.every((df) => df.fieldtype === "Check")
	);
	const noMapping = detail();
	noMapping.mapping = null;
	assert.equal(api.canEditPayment(noMapping), false);
	assert.equal(api.canEditPayment(detail()), false);
	assert.equal(api.canEditPayment(recognizedDetail()), true);
});
test("drawer reuses the shared accessible shell factory and stops initializing controls after close", async () => {
	let made = 0,
		resolveControl,
		started,
		controlsMade = 0,
		sharedMade = 0;
	const begin = new Promise((resolve) => (started = resolve));
	const surface = {
		length: 1,
		addClass() {
			return this;
		},
		find() {
			return this;
		},
		html() {
			return this;
		},
		on() {
			return this;
		},
		off() {
			return this;
		},
		append() {
			return this;
		},
		appendTo() {
			return this;
		},
		empty() {
			return this;
		},
		remove() {
			return this;
		},
		attr() {
			return this;
		},
		prop() {
			return this;
		},
		toggle() {
			return this;
		},
		toggleClass() {
			return this;
		},
		text() {
			return this;
		},
	};
	const d = drawer();
	d.panel = surface;
	d.controls = [];
	const h = {
		$: () => surface,
		DeepLinkERPOperatingExpenses:
			require("../deeplinkerp_branding/public/js/operating_expenses.js")({}),
		DeepLinkERPPurchasePayments: {
			createDrawer(_title, _wide, options) {
				sharedMade++;
				assert.equal(options?.desktopNonModal, true);
				assert.equal(options?.guardNavigation, true);
				return d;
			},
			disposeControls: (controls) => {
				made += controls.length;
				controls.splice(0);
			},
		},
		frappe: {
			...host(async () => ({ message: detail() })).frappe,
			ui: {
				form: {
					make_control: () => {
						controlsMade++;
						return {
							$input: surface,
							get_value: () => "",
							set_value: () =>
								new Promise((resolve) => {
									resolveControl = resolve;
									started();
								}),
						};
					},
				},
			},
		},
	};
	const a = create(h);
	assert.equal(typeof a.open, "function");
	const pending = a.open("source:1");
	await Promise.race([
		begin,
		pending.then(() => assert.fail("Drawer did not reach control initialization")),
	]);
	d.close();
	resolveControl();
	await pending;
	assert.equal(sharedMade, 1);
	assert.equal(controlsMade, 1);
	assert.equal(made, 1);
});
test("settings save disables sync and passes a Password token only when deliberately supplied", async () => {
	const calls = [],
		settings = {
			enabled: 1,
			token_configured: true,
			company_mappings: { "Legal raw": "C" },
			source_url: "https://payment.yueweiportal.com",
		};
	const a = create({
		frappe: {
			...host().frappe,
			session: { user: "Administrator" },
			call: async (req) => {
				calls.push(req);
				return {
					message: req.method.endsWith("get_sync_settings")
						? settings
						: { ...settings, enabled: 0 },
				};
			},
		},
	});
	assert.equal(typeof a.settingsWorkflow, "function");
	const w = a.settingsWorkflow(drawer());
	await w.load();
	await w.save();
	assert.equal(
		calls.at(-1).method,
		"deeplinkerp_branding.services.operating_expenses.save_sync_settings"
	);
	assert.equal(calls.at(-1).args.api_token, undefined);
	assert.equal(w.settings.enabled, 0);
	w.touchToken("deliberate-secret");
	await w.save();
	assert.equal(calls.at(-1).args.api_token, "deliberate-secret");
	assert.equal(w.token, "");
});
test("settings cannot enable before a current complete preview and explicit confirmation, and edits invalidate it", async () => {
	let previewCount = 0;
	const a = create({
		frappe: {
			...host().frappe,
			session: { user: "Administrator" },
			call: async (req) => {
				const settings = {
					enabled: 0,
					token_configured: true,
					company_mappings: { Legal: "C" },
				};
				return {
					message: req.method.endsWith("preview_sync")
						? {
								preview_fingerprint: "setup" + ++previewCount,
								sources: [
									{
										source_id: "a",
										application_type: "unclassified",
										source_company: "Legal",
										company: "C",
									},
								],
								has_more: true,
						  }
						: settings,
				};
			},
		},
	});
	assert.equal(typeof a.settingsWorkflow, "function");
	const w = a.settingsWorkflow(drawer());
	await w.load();
	await assert.rejects(() => w.enable(), /预览|确认/);
	await w.preview();
	assert.equal(w.canEnable(), false);
	w.confirm(true);
	assert.equal(w.canEnable(), true);
	w.touchMap(0, "company", "Other");
	assert.equal(w.canEnable(), false);
	assert.equal(w.previewResult, null);
	await assert.rejects(() => w.preview(), /保存/);
});
test("settings manual sync requires explicit enabled state and never dispatches automatically on load or preview", async () => {
	const calls = [];
	const a = create({
		frappe: {
			...host().frappe,
			session: { user: "Administrator" },
			call: async (req) => {
				calls.push(req.method);
				return {
					message: req.method.endsWith("get_sync_settings")
						? { enabled: 0, token_configured: true, company_mappings: { Legal: "C" } }
						: req.method.endsWith("preview_sync")
						? { preview_fingerprint: "p", sources: [], has_more: false }
						: req.method.endsWith("enable_sync")
						? {
								enabled: true,
								token_configured: true,
								company_mappings: { Legal: "C" },
						  }
						: { count: 5, end: false },
				};
			},
		},
	});
	assert.equal(typeof a.settingsWorkflow, "function");
	const w = a.settingsWorkflow(drawer());
	await w.load();
	await w.preview();
	assert.ok(calls.every((cmd) => !cmd.endsWith("sync_operating_expenses")));
	await assert.rejects(() => w.sync(), /启用/);
	w.confirm(true);
	await w.enable();
	await w.sync();
	assert.equal(
		calls.at(-1),
		"deeplinkerp_branding.services.operating_expenses.sync_operating_expenses"
	);
});
test("OA source preview works without a cashier token and never starts sync implicitly", async () => {
	const calls = [];
	const a = create({
		frappe: {
			...host().frappe,
			session: { user: "Administrator" },
			call: async (req) => {
				calls.push(req.method);
				return { message: req.method.endsWith("get_sync_settings")
					? { enabled: 0, token_configured: false, source_mode: "oa_cashier", company_mappings: { Legal: "C" } }
					: { preview_fingerprint: "oa-preview", sources: [], has_more: false } };
			},
		},
	});
	const w = a.settingsWorkflow(drawer());
	await w.load();
	assert.equal(w.canPreview(), true);
	await w.preview();
	assert.ok(calls.some((method) => method.endsWith("preview_sync")));
	assert.ok(calls.every((method) => !method.endsWith("sync_operating_expenses")));
});
test("late settings preview after token or mapping edits cannot restore enable eligibility", async () => {
	let finish;
	const a = create({
		frappe: {
			...host().frappe,
			session: { user: "Administrator" },
			call: (req) =>
				req.method.endsWith("get_sync_settings")
					? Promise.resolve({
							message: {
								enabled: 0,
								token_configured: true,
								company_mappings: { Legal: "C" },
							},
					  })
					: new Promise((resolve) => (finish = resolve)),
		},
	});
	assert.equal(typeof a.settingsWorkflow, "function");
	const w = a.settingsWorkflow(drawer());
	await w.load();
	const pending = w.preview();
	w.touchToken("changed");
	finish({ message: { preview_fingerprint: "stale", sources: [] } });
	await pending;
	w.confirm(true);
	assert.equal(w.canEnable(), false);
});
test("a settings refresh invalidates confirmed preview immediately and failure cannot restore enable eligibility", async () => {
	let loads = 0,
		rejectRefresh;
	const requests = [];
	const settings = {
		enabled: 0,
		token_configured: true,
		company_mappings: { LegalB: "CompanyB" },
	};
	const a = create({
		frappe: {
			...host().frappe,
			session: { user: "Administrator" },
			call: (req) => {
				requests.push(req);
				if (req.method.endsWith("get_sync_settings")) {
					if (++loads === 1) return Promise.resolve({ message: settings });
					return new Promise((_resolve, reject) => {
						rejectRefresh = reject;
					});
				}
				return Promise.resolve({
					message: { preview_fingerprint: "current-preview", sources: [] },
				});
			},
		},
	});
	const d = drawer(),
		w = a.settingsWorkflow(d);
	await w.load();
	await w.preview();
	w.confirm(true);
	assert.equal(w.canEnable(), true);
	const refresh = w.load();
	assert.equal(
		w.canEnable(),
		false,
		"The old preview is unusable before the refresh RPC finishes"
	);
	assert.equal(w.previewResult, null);
	rejectRefresh(new Error("settings refresh failed"));
	await assert.rejects(() => refresh, /settings refresh failed/);
	assert.equal(d.busy, false);
	assert.deepEqual(w.rows, [{ source_company: "LegalB", company: "CompanyB" }]);
	w.confirm(true);
	assert.equal(w.canEnable(), false);
	await assert.rejects(() => w.enable(), /预览|确认/);
	assert.ok(requests.every((req) => !req.method.endsWith("enable_sync")));
	await w.preview();
	assert.equal(
		w.canEnable(),
		false,
		"A new successful preview still requires a new confirmation"
	);
	w.confirm(true);
	assert.equal(w.canEnable(), true);
});
test("every settings controls rebuild isolates deleted added and saved native Link callbacks without losing current mappings", async () => {
	const controls = [],
		buttons = new Map(),
		saved = [],
		errors = [];
	class Surface {
		constructor(html = "") {
			this.length = 1;
			this.handlers = {};
			if (html.includes("<button")) {
				const label = html.replace(/<[^>]+>/g, "");
				buttons.set(label, [...(buttons.get(label) || []), this]);
			}
		}
		addClass() {
			return this;
		}
		find() {
			return new Surface();
		}
		html() {
			return this;
		}
		on(name, fn) {
			this.handlers[name] = fn;
			return this;
		}
		off() {
			return this;
		}
		append() {
			return this;
		}
		appendTo() {
			return this;
		}
		empty() {
			return this;
		}
		remove() {
			return this;
		}
		attr() {
			return this;
		}
		prop(key, value) {
			this[key] = value;
			return this;
		}
		hide() {
			return this;
		}
		show() {
			return this;
		}
		toggleClass() {
			return this;
		}
		text() {
			return this;
		}
	}
	const d = drawer();
	d.panel = new Surface();
	d.controls = [];
	d.error = (error) => errors.push(error.message);
	const settings = {
		enabled: 0,
		token_configured: true,
		company_mappings: { LegalA: "CompanyA", LegalB: "CompanyB" },
	};
	const a = create({
		$: (value) => new Surface(value),
		DeepLinkERPOperatingExpenses:
			require("../deeplinkerp_branding/public/js/operating_expenses.js")({}),
		DeepLinkERPPurchasePayments: {
			createDrawer: () => d,
			disposeControls: (items) => items.splice(0),
		},
		frappe: {
			...host().frappe,
			session: { user: "Administrator" },
			call: async (req) => {
				if (req.method.endsWith("save_sync_settings")) {
					const mappings = JSON.parse(req.args.company_mappings);
					saved.push(mappings);
					return { message: { ...settings, company_mappings: mappings } };
				}
				return { message: settings };
			},
			ui: {
				form: {
					make_control: ({ df }) => {
						const control = {
							df,
							$input: new Surface(),
							value: "",
							async set_value(value) {
								this.value = value;
							},
							get_value() {
								return this.value;
							},
						};
						controls.push(control);
						return control;
					},
				},
			},
		},
	});
	const lastControl = (name) =>
		controls.filter((control) => control.df.fieldname === name).at(-1);
	const click = async (label) => buttons.get(label).at(-1).handlers["click.dlpDrawer"]();
	await a.openSettings();
	const deletedLink = lastControl("company_map_0_company");
	await buttons.get("移除")[0].handlers["click.dlpDrawer"]();
	assert.equal(d.busy, false);
	assert.equal(lastControl("company_map_0_company").get_value(), "CompanyB");
	deletedLink.df.change();
	await click("保存设置并停用同步");
	assert.equal(d.busy, false);
	assert.deepEqual(
		saved.at(-1),
		{ LegalB: "CompanyB" },
		"The deleted row callback cannot shift CompanyA onto LegalB"
	);
	const beforeAdd = lastControl("company_map_0_company");
	await click("新增公司映射");
	assert.equal(d.busy, false);
	const currentLink = lastControl("company_map_0_company");
	await currentLink.set_value("CompanyB2");
	currentLink.df.change();
	await beforeAdd.set_value("CompanyA");
	beforeAdd.df.change();
	const addedSource = lastControl("company_map_1_source_company"),
		addedLink = lastControl("company_map_1_company");
	await addedSource.set_value("LegalC");
	addedSource.df.change();
	await addedLink.set_value("CompanyC");
	addedLink.df.change();
	await click("保存设置并停用同步");
	assert.equal(d.busy, false);
	assert.deepEqual(saved.at(-1), { LegalB: "CompanyB2", LegalC: "CompanyC" });
	await addedLink.set_value("CompanyA");
	addedLink.df.change();
	await click("保存设置并停用同步");
	assert.equal(d.busy, false);
	assert.deepEqual(
		saved.at(-1),
		{ LegalB: "CompanyB2", LegalC: "CompanyC" },
		"A save rebuild also rejects old Link callbacks"
	);
	assert.deepEqual(errors, []);
});
test("settlement terms stay unavailable until the mapping has a saved explicit payable rate", () => {
	assert.equal(typeof api.canEditPayment, "function");
	const d = detail();
	d.mapping.payable_exchange_rate = "";
	assert.equal(api.canEditPayment(d), false);
	d.mapping = null;
	const s = api.editSession(d);
	s.touch("payable_exchange_rate", "7.123456");
	assert.throws(() => s.touchPayment("pay:1", "bank_amount", "10"), /保存/);
});
test("settings drawer uses a native Password input and an explicit preview confirmation Check", async () => {
	const defs = [],
		html = [],
		buttons = new Map();
	let sourceMode = "cashier",
		sourceUrl = "https://payment.yueweiportal.com";
	const surface = {
		length: 1,
		addClass() {
			return this;
		},
		find() {
			return this;
		},
		html(value) {
			if (value) html.push(value);
			return this;
		},
		on() {
			return this;
		},
		off() {
			return this;
		},
		append() {
			return this;
		},
		appendTo() {
			return this;
		},
		empty() {
			return this;
		},
		remove() {
			return this;
		},
		attr() {
			return this;
		},
		prop() {
			return this;
		},
		toggle() {
			return this;
		},
		hide() {
			return this;
		},
		show() {
			return this;
		},
		toggleClass() {
			return this;
		},
		text() {
			return this;
		},
	};
	const d = drawer();
	d.panel = surface;
	d.controls = [];
	const a = create({
		$: (markup) => {
			if (!String(markup).startsWith("<button")) return surface;
			const button = Object.create(surface);
			button.on = (event, handler) => {
				const label = String(markup).replace(/<[^>]*>/g, "");
				buttons.set(label, handler);
				return button;
			};
			return button;
		},
		DeepLinkERPOperatingExpenses: { typeLabel: () => "付款申请" },
		DeepLinkERPPurchasePayments: {
			createDrawer: () => d,
			disposeControls: (controls) => controls.splice(0),
		},
		frappe: {
			...host(async (req) => ({
				message: req.method.endsWith("preview_sync") ? {
					preview_fingerprint: "preview-1",
					sources: [
						{ source_id: "mapped", application_type: "payment", company: "C" },
						{ source_id: "pending", application_type: "payment", company: null },
					],
					has_more: true,
				} : {
					enabled: 0,
					token_configured: true,
					company_mappings: { Legal: "C" },
					source_mode: sourceMode,
					source_url: sourceUrl,
				},
			})).frappe,
			session: { user: "Administrator" },
			ui: {
				form: {
					make_control: ({ df }) => {
						defs.push(df);
						return {
							df,
							$input: surface,
							set_value: async () => {},
							get_value: () => "",
						};
					},
				},
			},
		},
	});
	assert.equal(typeof a.openSettings, "function");
	await a.openSettings();
	assert.equal(defs.find((df) => df.fieldname === "api_token").fieldtype, "Password");
	assert.equal(defs.find((df) => df.fieldname === "confirm_sync_preview").fieldtype, "Check");
	assert.match(defs.find((df) => df.fieldname === "confirm_sync_preview").label, /未映射来源仅以待核对状态导入/);
	assert.match(html.join(""), /当前来源模式.*旧出纳接口/);
	assert.match(html.join(""), /当前手动同步仍读取旧出纳接口/);
	assert.doesNotMatch(html.join(""), /name="source_url"/);
	sourceMode = "oa_cashier";
	sourceUrl = "http://8.135.70.130:3005";
	await a.openSettings();
	assert.match(html.at(-1), /当前来源模式.*OA 新接口/);
	assert.match(html.at(-1), /OA 审批接口地址.*8\.135\.70\.130:3005/);
	assert.doesNotMatch(html.at(-1), /payment\.yueweiportal\.com/);
	await buttons.get("预览来源与公司映射")();
	assert.match(html.at(-1), /当前预览 2 条，法律公司待核对 1 条/);
	assert.match(html.at(-1), /后续分页可能还有待核对记录/);
});
test("native recognition history separates immutable expense controls from available settlement controls", async () => {
	const requests = [],
		buttons = new Map(),
		errors = [];
	let created = false,
		expenseDocstatus = 0,
		paymentEvent = null;
	class Surface {
		constructor(html = "") {
			this.htmlValue = html;
			this.length = 1;
			this.handlers = {};
			this.disabled = false;
			if (html.includes("<button")) buttons.set(html.replace(/<[^>]+>/g, ""), this);
		}
		addClass() {
			return this;
		}
		find() {
			return new Surface();
		}
		html() {
			return this;
		}
		on(name, fn) {
			this.handlers[name] = fn;
			return this;
		}
		off() {
			return this;
		}
		append() {
			return this;
		}
		appendTo() {
			return this;
		}
		empty() {
			return this;
		}
		remove() {
			return this;
		}
		attr() {
			return this;
		}
		prop(key, value) {
			if (key === "disabled") this.disabled = value;
			return this;
		}
		toggle() {
			return this;
		}
		toggleClass() {
			return this;
		}
		text() {
			return this;
		}
		val() {
			return this;
		}
	}
	const d = drawer();
	d.panel = new Surface();
	d.controls = [];
	d.error = (error) => errors.push(error?.message || "操作失败，请核对系统提示。");
	const f = host(async (req) => {
		requests.push(req.method);
		const item = detail();
		if (created)
			item.events = [
				{ journal_entry: "JE-new", docstatus: expenseDocstatus, operation: "expense" },
			];
		if (paymentEvent) item.events.push(paymentEvent);
		return {
			message: req.method.endsWith("get_operating_expense_detail")
				? item
				: req.method.endsWith("preview_voucher")
				? {
						fingerprint: "p",
						source_version: "v1",
						accounts: [],
						company: "C",
						posting_date: "2026-10-01",
				  }
				: ((created = true), { journal_entry: "JE-new", docstatus: 0 }),
		};
	}).frappe;
	const a = create({
		$: (value) => new Surface(value),
		DeepLinkERPOperatingExpenses:
			require("../deeplinkerp_branding/public/js/operating_expenses.js")({}),
		DeepLinkERPPurchasePayments: {
			createDrawer: () => d,
			nativeAction: (_type, name) => `<button>${name}</button>`,
			disposeControls: (controls) => controls.splice(0),
		},
		frappe: {
			...f,
			ui: {
				form: {
					make_control: ({ df }) => ({
						df,
						$input: new Surface(),
						set_value: async () => {},
						get_value: () => "",
					}),
				},
			},
		},
	});
	await a.open("source:1");
	assert.equal(
		d.controls.find((control) => control.df.fieldname === "bank_account").df.read_only,
		true
	);
	assert.equal(buttons.get("预览结算凭证").disabled, true);
	await buttons.get("预览费用凭证").handlers["click.dlpDrawer"]();
	await buttons.get("生成费用凭证草稿").handlers["click.dlpDrawer"]();
	assert.equal(
		requests.at(-1),
		"deeplinkerp_branding.services.operating_expenses.get_operating_expense_detail"
	);
	assert.equal(buttons.get("保存财务映射").disabled, true);
	assert.equal(
		d.controls.find((control) => control.df.fieldname === "classification").df.read_only,
		true
	);
	assert.equal(
		d.controls.find((control) => control.df.fieldname === "bank_account").df.read_only,
		false
	);
	assert.equal(buttons.get("预览结算凭证").disabled, false);
	expenseDocstatus = 1;
	await buttons.get("刷新抽屉").handlers["click.dlpDrawer"]();
	assert.equal(buttons.get("预览费用凭证").disabled, true);
	assert.equal(buttons.get("生成费用凭证草稿").disabled, true);
	assert.equal(
		d.controls.find((control) => control.df.fieldname === "existing_journal").df.read_only,
		true
	);
	assert.equal(
		d.controls.find((control) => control.df.fieldname === "bank_account").df.read_only,
		false
	);
	assert.equal(buttons.get("预览结算凭证").disabled, false);
	expenseDocstatus = 0;
	for (const state of [
		{ docstatus: 1 },
		{ docstatus: 2 },
		{ docstatus: 0, issue: "unreadable" },
	]) {
		paymentEvent = {
			journal_entry: "JE-payment",
			operation: "payment",
			payment_source_id: "pay:1",
			...state,
		};
		await buttons.get("刷新抽屉").handlers["click.dlpDrawer"]();
		assert.equal(
			d.controls.find((control) => control.df.fieldname === "bank_account").df.read_only,
			true
		);
		assert.equal(buttons.get("预览结算凭证").disabled, true);
		assert.equal(buttons.get("生成结算凭证草稿").disabled, true);
		if (state.docstatus === 1)
			assert.equal(
				buttons.get("预览费用凭证").disabled,
				false,
				"A posted payment does not make the draft expense posted"
			);
	}
	assert.deepEqual(
		errors,
		[],
		"Successful actions clear the error node without calling the shared fallback error renderer"
	);
});
test("a failed re-preview cannot leave the previous fingerprint eligible for creation", async () => {
	let previews = 0;
	const a = create(
		host(async (req) => {
			if (req.method.endsWith("get_operating_expense_detail")) return { message: detail() };
			if (++previews === 1)
				return { message: { fingerprint: "old", source_version: "v1", accounts: [] } };
			throw new Error("changed source");
		})
	);
	const w = a.workflow(drawer(), "source:1");
	await w.load();
	await w.preview();
	assert.equal(w.canCreate(), true);
	await assert.rejects(() => w.preview(), /changed source/);
	assert.equal(w.canCreate(), false);
});
test("cancelled or unreadable native associations block further voucher creation", async () => {
	const d = detail();
	d.events = [{ journal_entry: "cancelled", docstatus: 2, operation: "expense" }];
	const a = create(
		host(async (req) => ({
			message: req.method.endsWith("get_operating_expense_detail")
				? d
				: { fingerprint: "p", source_version: "v1", accounts: [] },
		}))
	);
	const w = a.workflow(drawer(), "source:1");
	await w.load();
	await w.preview();
	assert.equal(w.canCreate(), false);
});
test("same-value native Link Date Data Check validation and empty payment controls never dirty a saved mapping", () => {
	const s = api.editSession(recognizedDetail());
	const revision = s.revision;
	s.touch("party", "S");
	s.touch("posting_date", "2026-10-01");
	s.touch("payable_exchange_rate", "1.000000");
	s.touch("actual_incurred", 1);
	s.touch("no_existing_erp_coverage", "1");
	s.touchLine(0, "account", "Rent");
	s.touchLine(0, "project", "");
	s.touchPayment("pay:1", "bank_account", "");
	s.touchPayment("pay:1", "bank_amount", "");
	assert.equal(s.revision, revision);
	assert.equal(s.dirty(), false);
	assert.equal(s.mapping().payments["pay:1"], undefined);
});
test("real drawer stays previewable after late native validation callbacks including readonly account rate", async () => {
	const buttons = new Map(),
		late = [],
		errors = [];
	class Surface {
		constructor(html = "") {
			this.length = 1;
			this.handlers = {};
			if (html.includes("<button")) buttons.set(html.replace(/<[^>]+>/g, ""), this);
		}
		addClass() {
			return this;
		}
		find() {
			return new Surface();
		}
		html() {
			return this;
		}
		on(name, fn) {
			this.handlers[name] = fn;
			return this;
		}
		off() {
			return this;
		}
		append() {
			return this;
		}
		appendTo() {
			return this;
		}
		empty() {
			return this;
		}
		remove() {
			return this;
		}
		attr() {
			return this;
		}
		prop(key, value) {
			if (key === "disabled") this.disabled = value;
			return this;
		}
		toggle() {
			return this;
		}
		toggleClass() {
			return this;
		}
		text() {
			return this;
		}
		val() {
			return this;
		}
	}
	const d = drawer();
	d.panel = new Surface();
	d.controls = [];
	d.error = (error) => errors.push(error.message);
	const a = create({
		$: (value) => new Surface(value),
		DeepLinkERPOperatingExpenses:
			require("../deeplinkerp_branding/public/js/operating_expenses.js")({}),
		DeepLinkERPPurchasePayments: {
			createDrawer: () => d,
			nativeAction: (_type, name) => name,
			disposeControls: (controls) => controls.splice(0),
		},
		frappe: {
			...host(async () => ({ message: detail() })).frappe,
			ui: {
				form: {
					make_control: ({ df }) => {
						let value;
						return {
							df,
							$input: new Surface(),
							set_value: async (next) => {
								value = df.fieldtype === "Check" ? (next ? 1 : 0) : next;
								late.push(() => df.change());
							},
							get_value: () => value,
						};
					},
				},
			},
		},
	});
	await a.open("source:1");
	for (const nativeChange of late) assert.doesNotThrow(nativeChange);
	assert.equal(buttons.get("预览费用凭证").disabled, false);
	assert.deepEqual(errors, []);
});
test("readonly native Check keeps persisted confirmation state in its display checkbox after native Read refreshes", async () => {
	const checks = new Map(),
		decimalDisplays = [];
	class Surface {
		constructor(role = "") {
			this.length = 1;
			this.role = role;
			this.attrs = {};
		}
		addClass() {
			return this;
		}
		find(selector) {
			if (this.role === "display" && selector === 'input[type="checkbox"]')
				return this.checkbox;
			return new Surface();
		}
		html(value) {
			if (this.role === "display") {
				this.checkbox = new Surface("checkbox");
				this.checkbox.checked = /\bchecked\b/.test(value);
				this.checkbox.disabled = /\bdisabled\b/.test(value);
			}
			return this;
		}
		on() {
			return this;
		}
		off() {
			return this;
		}
		append() {
			return this;
		}
		appendTo() {
			return this;
		}
		empty() {
			return this;
		}
		remove() {
			return this;
		}
		attr(key, value) {
			this.attrs[key] = value;
			return this;
		}
		prop(key, value) {
			this[key] = value;
			return this;
		}
		toggle() {
			return this;
		}
		toggleClass() {
			return this;
		}
		text() {
			return this;
		}
		val(value) {
			decimalDisplays.push(value);
			return this;
		}
	}
	const d = drawer();
	d.panel = new Surface();
	d.controls = [];
	const item = detail();
	item.events = [{ journal_entry: "JE-expense", docstatus: 0, operation: "expense" }];
	item.mapping.expense_lines[0].source_amount = "100";
	item.mapping.expense_lines[0].amount = "100";
	const a = create({
		$: (value) => (value instanceof Surface ? value : new Surface()),
		DeepLinkERPOperatingExpenses:
			require("../deeplinkerp_branding/public/js/operating_expenses.js")({}),
		DeepLinkERPPurchasePayments: {
			createDrawer: () => d,
			nativeAction: (_type, name) => name,
			disposeControls: (controls) => controls.splice(0),
		},
		frappe: {
			...host(async () => ({ message: item })).frappe,
			ui: {
				form: {
					make_control: ({ df }) => {
						const control = {
							df,
							disp_status: df.read_only ? "Read" : "Write",
							$input:
								df.fieldtype === "Check" && df.read_only
									? undefined
									: new Surface(),
							disp_area:
								df.fieldtype === "Check" ? new Surface("display") : undefined,
							set_value: async function (next) {
								this.value =
									df.fieldtype === "Check"
										? next === true
											? 1
											: parseInt(next, 10) || 0
										: next;
								this.set_disp_area(this.value);
								df.change();
							},
							set_disp_area: function (value) {
								// Frappe's Check formatter conveys state only by CSS class, not checked.
								this.disp_area?.html(
									`<input type="checkbox" disabled class="disabled-${
										this.value || value ? "selected" : "deselected"
									}">`
								);
							},
							refresh: function () {
								// Native refresh_input Read path renders disp_area without set_input.
								this.set_disp_area(this.value);
							},
							get_value: function () {
								return this.value;
							},
						};
						if (df.fieldtype === "Check") checks.set(df.fieldname, control);
						return control;
					},
				},
			},
		},
	});
	await a.open("source:1");
	for (const [field, expected] of [
		["actual_incurred", true],
		["no_existing_erp_coverage", true],
		["existing_erp_coverage_confirmed", false],
	]) {
		const control = checks.get(field);
		assert.equal(control.disp_status, "Read");
		assert.equal(control.$input, undefined);
		assert.equal(control.value, expected ? 1 : 0);
		for (let refresh = 0; refresh < 3; refresh++) {
			control.refresh();
			assert.equal(
				control.disp_area.checkbox.checked,
				expected,
				`${field} readonly display state`
			);
			assert.equal(control.disp_area.checkbox.disabled, true);
			assert.equal(control.disp_area.checkbox.attrs["aria-label"], control.df.label);
		}
	}
	assert.ok(decimalDisplays.includes("100.00"));
	assert.equal(item.mapping.actual_incurred, true);
	assert.equal(item.mapping.expense_lines[0].amount, "100");
	assert.equal(await d.beforeClose(), true);
});
test("opening an unmapped blocked source starts clean and cancellation asks only after a real edit", async () => {
	const item = detail();
	item.mapping = null;
	item.source.approvals.eligibility = "blocked";
	const session = api.editSession(item);
	assert.equal(session.dirty(), false);
	assert.equal(session.ready(), false);
	session.touch("classification", "edited");
	assert.equal(session.dirty(), true);
	let asked = 0;
	const surface = {
		length: 1,
		addClass() {
			return this;
		},
		find() {
			return this;
		},
		html() {
			return this;
		},
		on() {
			return this;
		},
		off() {
			return this;
		},
		append() {
			return this;
		},
		appendTo() {
			return this;
		},
		empty() {
			return this;
		},
		remove() {
			return this;
		},
		attr() {
			return this;
		},
		prop() {
			return this;
		},
		toggle() {
			return this;
		},
		toggleClass() {
			return this;
		},
		text() {
			return this;
		},
	};
	const d = drawer();
	d.panel = surface;
	d.controls = [];
	const a = create({
		$: () => surface,
		DeepLinkERPOperatingExpenses:
			require("../deeplinkerp_branding/public/js/operating_expenses.js")({}),
		DeepLinkERPPurchasePayments: {
			createDrawer: () => d,
			disposeControls: (controls) => controls.splice(0),
		},
		frappe: {
			...host(async () => ({ message: item })).frappe,
			confirm: (_message, yes) => {
				asked++;
				yes();
			},
			ui: {
				form: {
					make_control: ({ df }) => ({
						df,
						$input: surface,
						set_value: async () => {},
						get_value: () => "",
					}),
				},
			},
		},
	});
	await a.open("source:1");
	assert.equal(await d.beforeClose(), true);
	assert.equal(asked, 0);
	const calls = [];
	const w = create(
		host(async (req) => {
			calls.push(req.method);
			return { message: item };
		})
	).workflow(drawer(), "source:1");
	await w.load();
	await assert.rejects(() => w.preview(), /保存/);
	assert.equal(calls.length, 1);
});
test("server validation messages remain readable through silent RPC rejection and failed save keeps edits", async () => {
	let saved = 0;
	const a = create(
		host(async (req) => {
			if (req.method.endsWith("get_operating_expense_detail"))
				return { message: recognizedDetail() };
			throw {
				responseJSON: {
					exc_type: "ValidationError",
					_server_messages: JSON.stringify([
						JSON.stringify({
							message: "<b>科目公司不符</b>，请复核",
							indicator: "red",
						}),
					]),
				},
			};
		})
	);
	const w = a.workflow(drawer(), "source:1", { onSaved: () => saved++ });
	await w.load();
	w.session.touchPayment("pay:1", "bank_amount", "20");
	await assert.rejects(() => w.save(), /科目公司不符.*请复核/);
	assert.equal(w.session.dirty(), true);
	assert.equal(w.session.mapping().payments["pay:1"].bank_amount, "20");
	assert.equal(saved, 0);
	assert.equal(w.canCreate("pay:1"), false);
});
async function typeSwitchingDrawer(mapped = false, reviewCompany = false, companyReviewLocked = false) {
	const controls = [],
		disposed = [], requests = [], buttons = new Map(), htmlWrites = [];
	const item = detail();
	if (reviewCompany) {
		item.issues = "原法律公司映射已移除，需要复核";
		item.company_review_state = "stale";
		item.company_review_locked = companyReviewLocked;
	}
	if (!mapped) item.mapping = null;
	else item.mapping.application_type = "payment";
	item.source.application_type = "unclassified";
	item.source.application_type_raw = "raw unknown";
	class Surface {
		constructor(markup = "") {
			this.length = 1;
			this.attrs = {};
			this.handlers = {};
			if (markup.includes("<button")) buttons.set(markup.replace(/<[^>]+>/g, ""), this);
		}
		addClass() {
			return this;
		}
		find() {
			return new Surface();
		}
		html(value) {
			if (typeof value === "string") htmlWrites.push(value);
			return this;
		}
		on(name, handler) {
			this.handlers[name] = handler;
			return this;
		}
		off() {
			return this;
		}
		append() {
			return this;
		}
		appendTo() {
			return this;
		}
		empty() {
			return this;
		}
		remove() {
			return this;
		}
		attr(key, value) {
			this.attrs[key] = value;
			return this;
		}
		prop() {
			return this;
		}
		toggle() {
			return this;
		}
		toggleClass() {
			return this;
		}
		text() {
			return this;
		}
	}
	const d = drawer();
	d.panel = new Surface();
	d.controls = [];
	const root = {
		$: markup => new Surface(markup),
		DeepLinkERPOperatingExpenses:
			require("../deeplinkerp_branding/public/js/operating_expenses.js")({}),
		DeepLinkERPPurchasePayments: {
			createDrawer: () => d,
			disposeControls: (items) => {
				disposed.push(...items);
				items.splice(0);
			},
		},
		frappe: {
			...host(async req => {requests.push(req); return { message: req.method.endsWith("save_mapping") ? {mapping: JSON.parse(req.args.mapping)} : item };}).frappe,
			...(reviewCompany ? {session:{user:"Administrator"}} : {}),
			ui: {
				form: {
					make_control: ({ df }) => {
						const control = {
							df,
							$input:
								df.fieldtype === "Link" && df.read_only
									? undefined
									: new Surface(),
							set_value: async function (value) {
								this.value = value;
							},
							get_value() {
								return this.value;
							},
						};
						controls.push(control);
						return control;
					},
				},
			},
		},
	};
	const a = create(root);
	const current = (field) => controls.filter((control) => control.df.fieldname === field).at(-1);
	await a.open("source:1");
	return {a, d, root, current, controls, disposed, requests, buttons, htmlWrites, item};
}
test("historical company review offers an audited reconfirmation control", async () => {
	const {current, buttons} = await typeSwitchingDrawer(false, true);
	assert.equal(current("legal_company").get_value(), "C");
	assert.equal(current("legal_company").df.label, "重新确认法律公司");
	assert.ok(buttons.has("重新确认法律公司"));
});
test("historical company with financial confirmation offers no company save control", async () => {
	const {current, buttons, htmlWrites} = await typeSwitchingDrawer(true, true, true);
	assert.equal(current("legal_company"), undefined);
	assert.equal(buttons.has("重新确认法律公司"), false);
	assert.ok(htmlWrites.some(html => html.includes("已有财务确认，无法在此修改法律公司")));
});
test("unclassified type switching rebuilds searchable native party Links and isolates old control callbacks", async () => {
	const {current, disposed, item} = await typeSwitchingDrawer();
	assert.equal(current("party").$input, undefined);
	const initialType = current("application_type");
	await current("classification").set_value("retain this");
	current("classification").df.change();
	await initialType.set_value("payment");
	initialType.df.change();
	await new Promise((resolve) => setImmediate(resolve));
	const supplier = current("party");
	assert.ok(supplier.$input, "Readonly initial Link must become a real input");
	assert.equal(supplier.df.options, "Supplier");
	assert.equal(supplier.$input.attrs["aria-label"], "往来方");
	assert.equal(current("classification").value, "retain this");
	await supplier.set_value("S");
	supplier.df.change();
	initialType.df.change();
	assert.equal(current("party").value, "S");
	const paymentType = current("application_type");
	await paymentType.set_value("reimbursement");
	paymentType.df.change();
	await new Promise((resolve) => setImmediate(resolve));
	const employee = current("party");
	assert.ok(employee.$input);
	assert.equal(employee.df.options, "Employee");
	assert.deepEqual(employee.get_query(), { filters: { company: "C" } });
	assert.equal(employee.value, "");
	supplier.df.change();
	assert.equal(employee.value, "");
	assert.equal(current("classification").value, "retain this");
	assert.ok(disposed.includes(supplier));
	assert.equal(item.source.application_type, "unclassified");
	assert.equal(item.source.application_type_raw, "raw unknown");
});
test("advanced mapping rebuilds confirm dirty payment inputs before disposing their mounted controls", async () => {
	for (const cause of ["type", "allocation", "save"]) {
		const h = await typeSwitchingDrawer(true);
		let confirmation, checks = 0;
		h.d.paymentDirty = () => true;
		h.root.frappe.confirm = (_message, yes, no) => {checks++; confirmation = {yes, no};};
		const original = h.current("classification"), load = h.d.loadId;
		const rebuild = async () => {
			if (cause === "type") {
				const control = h.current("application_type");
				await control.set_value(control.value === "reimbursement" ? "payment" : "reimbursement");
				control.df.change();
			} else await h.buttons.get(cause === "allocation" ? "新增费用分摊" : "保存财务映射").handlers["click.dlpDrawer"]();
		};
		const declined = rebuild();
		await new Promise(resolve => setImmediate(resolve));
		assert.ok(confirmation, `${cause} must use the existing unsaved-input confirmation`);
		assert.equal(h.d.loadId, load);
		assert.equal(h.disposed.includes(original), false);
		confirmation.no(); await declined;
		await new Promise(resolve => setImmediate(resolve));
		assert.equal(h.current("classification"), original);
		assert.equal(h.d.paymentDirty(), true);
		const accepted = rebuild();
		await new Promise(resolve => setImmediate(resolve));
		assert.equal(checks, 2); confirmation.yes(); await accepted;
		await new Promise(resolve => setImmediate(resolve));
		assert.equal(h.disposed.includes(original), true);
		assert.equal(h.d.loadId, load + 1);
	}
});
test("leaving the drawer during payment-input discard confirmation cancels the pending rebuild", async () => {
	const h = await typeSwitchingDrawer(true);
	let accept;
	h.d.paymentDirty = () => true;
	h.root.frappe.confirm = (_message, yes) => {accept = yes;};
	const load = h.d.loadId;
	const pending = h.buttons.get("新增费用分摊").handlers["click.dlpDrawer"]();
	assert.equal(typeof accept, "function");
	h.d.close(); accept(); await pending;
	assert.equal(h.d.loadId, load);
	assert.equal(h.disposed.length, 0);
});
test("declined mapping rebuilds leave type party and allocations unchanged in the next save RPC", async () => {
	const outcomes = [];
	for (const cause of ["add", "remove", "type"]) {
		const h = await typeSwitchingDrawer(true);
		h.d.paymentDirty = () => true;
		h.root.frappe.confirm = (_message, _yes, no) => no();
		if (cause === "type") {
			await h.current("application_type").set_value("reimbursement");
			h.current("application_type").df.change();
			await new Promise(resolve => setImmediate(resolve));
		} else await h.buttons.get(cause === "add" ? "新增费用分摊" : "移除").handlers["click.dlpDrawer"]();
		h.d.paymentDirty = () => false;
		await h.buttons.get("保存财务映射").handlers["click.dlpDrawer"]();
		const saved = h.requests.find(req => req.method.endsWith("save_mapping"));
		const values = saved && JSON.parse(saved.args.mapping);
		outcomes.push({cause, typeControl: h.current("application_type").value, type: values?.application_type, partyType: values?.party_type, party: values?.party, lines: values?.expense_lines});
	}
	assert.deepEqual(outcomes, ["add", "remove", "type"].map(cause => ({cause, typeControl: "payment", type: "payment", partyType: "Supplier", party: "S", lines: detail().mapping.expense_lines})));
});
test("operating full-document warning predicate reuses live mapping and payment dirty state", async () => {
	const h = await typeSwitchingDrawer(true);
	assert.equal(typeof h.d.beforeUnloadShouldBlock, "function");
	assert.equal(h.d.beforeUnloadShouldBlock(), false);
	h.d.paymentDirty = () => true;assert.equal(h.d.beforeUnloadShouldBlock(), true);
	h.d.paymentDirty = () => false;
	await h.current("classification").set_value("changed");h.current("classification").df.change();
	assert.equal(h.d.beforeUnloadShouldBlock(), true);
	await h.buttons.get("保存财务映射").handlers["click.dlpDrawer"]();
	assert.equal(h.d.beforeUnloadShouldBlock(), false);
});
