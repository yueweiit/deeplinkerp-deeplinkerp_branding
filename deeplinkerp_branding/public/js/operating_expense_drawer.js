(function (root, factory) {
	if (typeof module === "object" && module.exports) module.exports = factory;
	root.DeepLinkERPOperatingExpenseDrawer = factory(root);
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
	"use strict";
	const API = "deeplinkerp_branding.services.operating_expenses.";
	const t = (value, args = []) => String((root.__ || (text => text))(value)).replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");
	const clone = (value) => JSON.parse(JSON.stringify(value));
	const esc = (value) =>
		String(value ?? "").replace(
			/[&<>"']/g,
			(char) =>
				({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char])
		);
	const money = (value) => root.DeepLinkERPOperatingExpenses.money(value);
	const display = (value) =>
		esc(
			value == null || value === ""
				? "—"
				: typeof value === "object"
				? JSON.stringify(value)
				: value
		);
	const boolean = (value) => value === true || value === 1 || value === "1";
	const stateDisplay = (value) => display(typeof value === "string" && value ? t(value) : value);
	const recognitionEvents = (detail) =>
		(detail?.events || []).filter(
			(event) =>
				event.operation === "expense" &&
				event.journal_entry &&
				!event.issue &&
				[0, 1].includes(event.docstatus)
		);
	const hasPostedRecognition = (detail) =>
		recognitionEvents(detail).some((event) => event.docstatus === 1);
	const settlementBlocked = (detail, payment) =>
		(detail?.events || []).some(
			(event) =>
				event.payment_source_id === payment &&
				(event.docstatus === 1 || event.docstatus === 2 || event.issue)
		);
	const mappingFields = [
		"company",
		"party_type",
		"party",
		"payable_account",
		"payable_exchange_rate",
		"posting_date",
		"classification",
		"actual_incurred",
		"no_existing_erp_coverage",
		"recognition_mode",
		"existing_erp_coverage_confirmed",
		"application_type",
		"expense_lines",
		"payments",
	];
	const flags = [
		"actual_incurred",
		"no_existing_erp_coverage",
		"existing_erp_coverage_confirmed",
	];
	const roles = () =>
		root.frappe?.user_roles || root.frappe?.boot?.user?.roles || root.roles || [];
	const isManager = () =>
		root.frappe?.session?.user === "Administrator" ||
		roles().includes("System Manager") ||
		Boolean(root.frappe?.user?.has_role?.("System Manager"));
	function canFinance() {
		const f = root.frappe;
		return (
			Boolean(
				f?.session?.user === "Administrator" ||
					["Accounts User", "Accounts Manager"].some(
						(role) => roles().includes(role) || f?.user?.has_role?.(role)
					)
			) &&
			(typeof f?.model?.can_create !== "function" ||
				Boolean(f.model.can_create("Journal Entry"))) &&
			(typeof f?.model?.can_read !== "function" ||
				Boolean(f.model.can_read("Journal Entry")))
		);
	}
	function parseOriginalURL(value) {
		try {
			const url = new URL(value);
			return url.protocol === "https:" &&
				[
					"aflow.dingtalk.com",
					"oa.dingtalk.com",
					"www.dingtalk.com",
					"dingtalk.com",
				].includes(url.hostname) &&
				!url.username &&
				!url.password &&
				!url.port
				? url
				: null;
		} catch (_) {
			return null;
		}
	}
	const safeOriginalURL = (value) => parseOriginalURL(value)?.href || null;
	function dingTalkOriginalLink(value) {
		const url = parseOriginalURL(value);
		if (!url) return null;
		const ids = url.searchParams.getAll("procInstId"),
			queryStart = url.hash.indexOf("?");
		if (queryStart >= 0) {
			ids.push(...new URLSearchParams(url.hash.slice(queryStart + 1)).getAll("procInstId"));
		}
		if (ids.length !== 1 || !/^[A-Za-z0-9_-]{1,200}$/.test(ids[0])) return null;
		// Same client route as overseas_costing.utils.dingtalk; never use an
		// approval business number or an arbitrary supplied protocol URL.
		const mobile = `https://aflow.dingtalk.com/dingtalk/mobile/homepage.htm?showmenu=false&dd_progress=false#/approval?procInstId=${encodeURIComponent(ids[0])}`;
		return {
			official_url: url.href,
			mobile_url: mobile,
			desktop_url: `dingtalk://dingtalkclient/page/link?url=${encodeURIComponent(mobile)}&pc_slide=true`,
		};
	}
	function editSession(detail, onChange = () => {}) {
		let values, savedRevision;
		const sourceType = detail.source.application_type;
		const session = {
			revision: 0,
			reset(mapping = detail.mapping) {
				values = {
					company: detail.company || "",
					party_type:
						sourceType === "payment"
							? "Supplier"
							: sourceType === "reimbursement"
							? "Employee"
							: "",
					party: "",
					payable_account: "",
					payable_exchange_rate: "",
					posting_date: "",
					classification: "",
					actual_incurred: false,
					no_existing_erp_coverage: false,
					recognition_mode: "",
					existing_erp_coverage_confirmed: false,
					expense_lines: [],
					payments: {},
				};
				for (const field of mappingFields)
					if (mapping && field in mapping) values[field] = clone(mapping[field]);
				for (const field of flags) values[field] = boolean(values[field]);
				if (sourceType !== "unclassified") delete values.application_type;
				this.revision++;
				savedRevision = this.revision;
			},
			touch(field, value) {
				if (
					!mappingFields.includes(field) ||
					["payments", "expense_lines", "company", "party_type"].includes(field)
				)
					throw new Error(t("映射字段无效"));
				const next = flags.includes(field) ? boolean(value) : String(value ?? "");
				if (
					(flags.includes(field)
						? boolean(values[field])
						: String(values[field] ?? "")) === next
				)
					return;
				if (field === "application_type") {
					if (sourceType !== "unclassified")
						throw new Error(t("只能明确待分类来源的申请类型"));
					if (!["", "payment", "reimbursement"].includes(next))
						throw new Error(t("申请类型无效"));
					values.party_type =
						next === "payment"
							? "Supplier"
							: next === "reimbursement"
							? "Employee"
							: "";
					values.party = "";
				}
				values[field] = next;
				this.changed();
			},
			touchLine(index, field, value) {
				if (
					!values.expense_lines[index] ||
					![
						"account",
						"source_amount",
						"amount",
						"exchange_rate",
						"cost_center",
						"project",
					].includes(field)
				)
					throw new Error(t("费用分摊字段无效"));
				const next = String(value ?? "");
				if (String(values.expense_lines[index][field] ?? "") === next) return;
				values.expense_lines[index][field] = next;
				this.changed();
			},
			addLine() {
				if (values.expense_lines.length >= 100) throw new Error(t("最多 100 行费用分摊"));
				values.expense_lines.push({
					account: "",
					source_amount: "",
					amount: "",
					exchange_rate: "",
					cost_center: "",
					project: "",
				});
				this.changed();
			},
			removeLine(index) {
				values.expense_lines.splice(index, 1);
				this.changed();
			},
			touchPayment(id, field, value) {
				if (!canEditPayment(detail))
					throw new Error(
						t("请先保存费用映射并生成或关联费用确认凭证，再维护本笔结算。")
					);
				if (
					![
						"bank_account",
						"bank_amount",
						"bank_exchange_rate",
						"exchange_difference_account",
						"cost_center",
					].includes(field)
				)
					throw new Error(t("付款映射字段无效"));
				const next = String(value ?? "");
				if (String(values.payments[id]?.[field] ?? "") === next) return;
				values.payments[id] ||= {};
				values.payments[id][field] = next;
				// This is an existing recorded mapping fact, never an automatically chosen rate.
				if (
					!values.payments[id].payable_exchange_rate &&
					detail.mapping?.payable_exchange_rate
				)
					values.payments[id].payable_exchange_rate = String(
						detail.mapping.payable_exchange_rate
					);
				this.changed();
			},
			changed() {
				this.revision++;
				onChange(this);
			},
			mapping() {
				return clone(values);
			},
			dirty() {
				return this.revision !== savedRevision;
			},
			ready() {
				return (
					(sourceType !== "unclassified" ||
						["payment", "reimbursement"].includes(values.application_type)) &&
					values.actual_incurred &&
					(values.recognition_mode === "new"
						? values.no_existing_erp_coverage
						: values.recognition_mode === "existing" &&
						  values.existing_erp_coverage_confirmed)
				);
			},
		};
		session.reset();
		return session;
	}
	async function call(method, args = {}, base = API) {
		try {
			const response = await root.frappe.call({
				method: base + method,
				args,
				type: "POST",
				silent: true,
			});
			if (response.exc || response.exc_type) throw response;
			return response.message;
		} catch (error) {
			const payload = error?.responseJSON || error;
			let messages = [];
			try {
				const raw =
					typeof payload?._server_messages === "string"
						? JSON.parse(payload._server_messages)
						: payload?._server_messages;
				if (Array.isArray(raw))
					messages = raw
						.map((entry) => {
							if (typeof entry === "string") {
								try {
									entry = JSON.parse(entry);
								} catch (_) {
									return entry;
								}
							}
							return typeof entry?.message === "string" ? entry.message : "";
						})
						.filter(Boolean);
			} catch (_) {
				/* A malformed error envelope cannot expose a traceback or hide the fallback. */
			}
			const message =
				messages.join("\n") ||
				(typeof payload?.message === "string"
					? payload.message
					: typeof error?.message === "string"
					? error.message
					: t("操作失败，请核对系统提示。"));
			throw new Error(message.replace(/<[^>]*>/g, ""));
		}
	}
	function workflow(drawer, sourceId, hooks = {}) {
		let task = 0,
			detail = null;
		const previews = new Map();
		const key = (payment) => payment || "expense";
		const state = () => {
			if (drawer.alive()) hooks.onState?.(w);
		};
		const valid = (id, load) => drawer.alive() && id === task && load === drawer.loadId;
		async function run(action, operation = "write") {
			if (!drawer.alive()) return;
			if (drawer.busy) throw new Error(t("请等待当前操作完成"));
			const id = ++task,
				load = drawer.loadId;
			drawer.setBusy(true, operation);
			state();
			try {
				return await action(() => valid(id, load));
			} finally {
				if (valid(id, load)) {
					drawer.setBusy(false);
					state();
				}
			}
		}
		const currentPreview = (payment) => {
			const p = previews.get(key(payment));
			return p &&
				p.revision === w.session?.revision &&
				p.sourceVersion === detail?.source.version
				? p.result
				: null;
		};
		const sourceReady = () =>
			Boolean(
				detail?.company &&
					detail.source.approvals?.eligibility === "eligible" &&
					!detail.mapping_issue &&
					!(detail.events || []).some((event) => event.docstatus === 2 || event.issue)
			);
		const w = {
			session: null,
			get detail() {
				return detail;
			},
			invalidate() {
				previews.clear();
				if (drawer.alive()) hooks.onInvalidate?.();
				state();
			},
			async load() {
				drawer.loadId++;
				this.invalidate();
				return run(async (current) => {
					const result = await call("get_operating_expense_detail", {
						source_id: sourceId,
					});
					if (!current()) return;
					detail = result;
					this.session = editSession(detail, () => this.invalidate());
					await hooks.onDetail?.(detail, this);
					if (!current()) return;
					state();
					return result;
				}, "read");
			},
			async save() {
				if (!this.session?.ready()) throw new Error(t("请明确费用已发生及 ERP 覆盖情况"));
				const revision = this.session.revision,
					values = this.session.mapping();
				this.invalidate();
				return run(async (current) => {
					const result = await call("save_mapping", {
						source_id: sourceId,
						mapping: JSON.stringify(values),
						expected_source_version: detail.source.version,
					});
					if (!current() || this.session.revision !== revision) return;
					detail.mapping = result.mapping;
					this.session.reset(result.mapping);
					hooks.onSaved?.(result, this);
					state();
					return result;
				});
			},
			async saveCompany(company) {
				return run(async (current) => {
					const result = await call("save_source_company", {
						source_id: sourceId,
						company,
						expected_source_version: detail.source.version,
					});
					if (!current()) return;
					this.invalidate();
					return result;
				});
			},
			async preview(payment) {
				if (!detail?.mapping || !this.session || this.session.dirty())
					throw new Error(t("请先保存当前财务映射，再预览"));
				this.invalidate();
				if (payment && settlementBlocked(detail, payment))
					throw new Error(t("本笔结算已记账、已取消或存在问题，只能查看。"));
				if (payment && !canEditPayment(detail))
					throw new Error(
						t("请先保存费用映射并生成或关联费用确认凭证，再维护本笔结算。")
					);
				if (!payment && hasPostedRecognition(detail))
					throw new Error(t("费用确认凭证已记账，只能查看；后续实际付款仍可办理结算。"));
				const revision = this.session.revision,
					sourceVersion = detail.source.version;
				return run(async (current) => {
					const result = await call("preview_voucher", {
						source_id: sourceId,
						...(payment ? { payment_source_id: payment } : {}),
					});
					if (!current() || revision !== this.session.revision) return;
					if (!result?.fingerprint || !Array.isArray(result.accounts))
						throw new Error(t("凭证预览无效，请刷新后复核"));
					if (result.source_version !== sourceVersion)
						throw new Error(t("来源版本已变化，请刷新抽屉后重新预览。"));
					previews.set(key(payment), { revision, sourceVersion, result });
					hooks.onPreview?.(result, payment);
					state();
					return result;
				}, "read");
			},
			previewResult: currentPreview,
			canCreate(payment) {
				return Boolean(
					canFinance() &&
						sourceReady() &&
						(payment
							? canEditPayment(detail) && !settlementBlocked(detail, payment)
							: !hasPostedRecognition(detail)) &&
						currentPreview(payment) &&
						(payment || this.session.mapping().recognition_mode === "new")
				);
			},
			canLink() {
				return Boolean(
					canFinance() &&
						sourceReady() &&
						!hasPostedRecognition(detail) &&
						currentPreview() &&
						this.session.mapping().recognition_mode === "existing"
				);
			},
			async create(payment) {
				if (!this.canCreate(payment))
					throw new Error(t("请先取得当前映射的有效预览；已有 ERP 覆盖不能新建费用"));
				const fingerprint = currentPreview(payment).fingerprint;
				return run(async (current) => {
					const result = await call("create_voucher_draft", {
						source_id: sourceId,
						expected_fingerprint: fingerprint,
						...(payment ? { payment_source_id: payment } : {}),
					});
					if (!current()) return;
					this.invalidate();
					hooks.onResult?.(result, payment);
					state();
					return result;
				});
			},
			async link(journalEntry) {
				if (!this.canLink() || !journalEntry)
					throw new Error(t("请预览并明确选择已有原生凭证"));
				const fingerprint = currentPreview().fingerprint;
				return run(async (current) => {
					const result = await call("link_existing", {
						source_id: sourceId,
						journal_entry: journalEntry,
						expected_fingerprint: fingerprint,
					});
					if (!current()) return;
					this.invalidate();
					hooks.onResult?.(result);
					state();
					return result;
				});
			},
		};
		return w;
	}
	const nativeLink = (name, label = name) =>
		root.DeepLinkERPPurchasePayments?.nativeAction("Journal Entry", name, label) ||
		`<button type="button" class="btn btn-default btn-xs dlp-native-document" data-doctype="Journal Entry" data-name="${esc(
			name
		)}">${esc(t(label))}</button>`;
	const journalState = (value) =>
		t(value === 0 ? "草稿 · 未记账" : value === 1 ? "关联已记账" : "已取消 · 需复核");
	function sourceHTML(detail) {
		const s = detail.source,
			original = dingTalkOriginalLink(s.original_url);
		const fact = (label, value) =>
			`<div><span class="text-muted">${esc(t(label))}</span> ${display(value)}</div>`;
		const balance = detail.erp_payments?.managed ? detail.erp_payments.balance || {} : s;
		const progress = root.DeepLinkERPOperatingPaymentPanel?.balanceProgress({amount:s.amount,paid_amount:balance.paid_amount});
		const approvalState=s.approval_state||s.approvals?.state||s.approvals?.eligibility;
		const applicationNumber = original ? `<a href="${esc(original.desktop_url)}" title="${esc(t("需安装并登录钉钉，原单权限由钉钉校验。"))}">${display(s.approval_no || s.source_id)}</a>` : display(s.approval_no || s.source_id);
		const hero = `<header class="dlp-operating-hero"><div><h3>${display(s.summary || root.DeepLinkERPOperatingExpenses.typeLabel?.(s.effective_application_type||s.application_type)||t("运营支出详情"))}</h3><p>${esc(t("申请编号"))}：${applicationNumber}</p>${original ? "" : `<p class="text-muted">${esc(t("原单链接待核对"))}</p>`}<div class="dlp-operating-hero-badges"><span class="dlp-operating-badge ${["approved","eligible"].includes(approvalState)?"is-approved":approvalState==="pending"?"is-pending":"is-review"}">${esc(root.DeepLinkERPOperatingExpenses.approvalLabel(approvalState))}</span><span class="dlp-operating-badge ${s.source_status==="已付款"?"is-approved":"is-pending"}">${display(s.source_status||t("付款待核对"))}</span></div></div><div class="dlp-operating-money-strip">${[["申请金额",s.amount],["累计已付",balance.paid_amount],["剩余待付",balance.pending_amount]].map(([label,value])=>`<div><small>${esc(t(label))}</small><strong>${money(value)} <small>${display(s.currency)}</small></strong></div>`).join("")}</div><div class="dlp-operating-progress"><span>${esc(t("付款进度"))}</span>${progress?`<div role="progressbar" aria-label="${esc(t("付款进度"))}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress.percent}"><i style="width:${progress.percent}%"></i></div><span>${progress.label}</span>`:`<span class="text-muted">${esc(t("金额待核对"))}</span>`}</div></header>`;
		return `${hero}<section class="dlp-operating-section" data-operating-pane="request"><div class="dlp-operating-facts">${fact("法律公司",detail.company)}${fact("收款人",s.payee_name)}${fact("申请日期",s.request_date)}${fact("申请人",s.applicant)}</div><details class="dlp-operating-advanced"><summary>${esc(t("原始来源信息"))}</summary><div class="dlp-operating-facts">${fact(
			"申请编号",
			s.source_id
		)}${fact("原始审批编号", s.approval_no)}${fact("原始钉钉实例编号", s.dingding_id)}${fact("原始来源请求编号", s.source_request_id)}${fact("原始申请类型", s.application_type_raw)}${fact("来源版本", s.version)}${fact("来源公司", s.source_company)}${fact("来源归档表", s.source_sheet)}${fact("当前来源金额", `${money(s.amount)} ${s.currency || t("币种未明确")}`)}${fact("原始批准金额", s.original_source_amount == null ? "—" : `${money(s.original_source_amount)} ${s.original_source_currency || t("币种未明确")}`)}${fact("出纳已付", `${money(s.paid_amount)} ${s.currency || ""}`)}${fact("出纳待付", `${money(s.pending_amount)} ${s.currency || ""}`)}${fact("付款状态", s.source_status)}${fact("出纳原付款状态", s.cashier_reported_payment_status)}</div><p>${original ? `<a href="${esc(original.mobile_url)}" target="_blank" rel="noopener noreferrer">${esc(t("网页原单链接"))}</a> · ` : ""}<a href="https://payment.yueweiportal.com/" target="_blank" rel="noopener noreferrer">${esc(t("请款网站"))}</a></p></details></section>
		<section class="dlp-operating-section" data-operating-pane="approvals"><div class="dlp-operating-timeline-holder"></div><details class="dlp-operating-advanced"><summary>${esc(
			t("来源审批与待处理问题")
		)}</summary><p class="${s.approvals?.eligibility === "eligible" ? "text-success" : "text-warning"}">${esc(root.DeepLinkERPOperatingExpenses.approvalLabel(s.approvals?.eligibility))}</p><div class="dlp-operating-facts">${Object.entries(
			s.approvals?.raw || {}
		)
			.map(([key, value]) =>
				fact(
					{
						status: "审批状态原文",
						result: "审批结果原文",
						owner_confirmation: "负责人确认",
						finance_review: "财务复核",
						finance_manager_approval: "财务经理审批",
						general_manager_approval: "总经理审批",
						cashier_status: "出纳审批状态原文",
						cashier_result: "出纳审批结果原文",
						cashier_owner_confirmation: "出纳负责人确认",
						cashier_finance_review: "出纳财务审批",
						cashier_finance_manager_approval: "出纳财务主管审批",
						cashier_general_manager_approval: "出纳总经理审批",
					}[key] || key,
					value
				)
			)
			.join(
				""
				)}</div></details>${detail.issues ? `<p class="text-warning">${display(detail.issues)}</p>` : ""}${detail.mapping_issue ? `<p class="text-warning">${display(detail.mapping_issue)}</p>` : ""}</section>
		<section class="dlp-operating-section" data-operating-pane="request"><strong>${esc(
			t("来源附件")
		)}</strong>${(s.attachments || []).map((file) => `<p class="dlp-operating-proof"><button type="button" class="btn btn-link dlp-operating-download" data-attachment="${esc(file.source_id)}">${display(file.filename)}</button> · ${esc(t(file.payment_source_id ? "实际付款附件" : "申请附件"))} ${display(file.payment_source_id)} · ${esc(t("版本"))} ${display(file.version)}</p>`).join("") || `<p>${esc(t("暂无附件"))}</p>`}</section>
		<section class="dlp-operating-section" data-operating-pane="payments"><div class="dlp-operating-payment-heading"><strong>${esc(t("付款明细"))}</strong><div class="dlp-operating-register"></div></div><div class="dlp-operating-local-payments"></div><div class="dlp-operating-history-payments">${(s.payments || []).filter(p=>p.source_type!=="erp").map((p,index) => `<article class="dlp-operating-payment-card"><strong>${esc(t("第"))} ${index+1} ${esc(t("笔"))} · ${money(p.amount)} ${display(p.currency)}</strong><p>${display(p.payment_date)} · ${display(p.payer)}</p><p>${esc(t("来源：历史付款"))} · ${display(p.evidence_status)}</p><details class="dlp-operating-advanced"><summary>${esc(t("实际付款证据"))}</summary><p>${display(p.source_id)} · ${display(p.bank_reference)}</p><p>${display(p.remark)}</p></details></article>`).join("") || `<p class="text-muted">${esc(t("尚无历史付款证据"))}</p>`}</div></section>
		<section class="dlp-operating-section" data-operating-pane="vouchers"><strong>${esc(
			t("原生凭证关联与历史")
		)}</strong>${(detail.events || []).map((event) => `<p>${event.journal_entry ? nativeLink(event.journal_entry) : display(event.issue)} · ${esc(t(event.operation === "payment" ? "出纳付款结算" : "费用确认"))} ${display(event.payment_source_id)} · ${event.journal_entry ? esc(journalState(event.docstatus)) : ""} ${stateDisplay(event.settlement_state)} · ${esc(t("来源版本"))} ${display(event.source_version)}</p>`).join("") || `<p>${esc(t("尚无关联原生凭证"))}</p>`}</section>`;
	}
	function previewHTML(preview) {
		const fields = [
			"account",
			"account_currency",
			"exchange_rate",
			"debit_in_account_currency",
			"credit_in_account_currency",
			"debit",
			"credit",
			"party_type",
			"party",
			"cost_center",
			"project",
		];
		const amounts = new Set([
			"debit_in_account_currency",
			"credit_in_account_currency",
			"debit",
			"credit",
		]);
		return `<p>${display(
			preview.company
		)} · ${esc(t("凭证日期"))} ${display(preview.posting_date)}${preview.settlement_state ? ` · ${stateDisplay(preview.settlement_state)}` : ""}</p><div class="dlp-operating-table-wrap"><table class="table table-bordered dlp-operating-preview"><thead><tr>${["科目", "科目币种", "汇率", "科目币种借方", "科目币种贷方", "本位币借方", "本位币贷方", "往来方类型", "往来方", "成本中心", "项目"].map((label) => `<th>${esc(t(label))}</th>`).join("")}</tr></thead><tbody>${preview.accounts.map((row) => `<tr>${fields.map((field) => `<td>${amounts.has(field) ? money(row[field]) : display(row[field])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>${roundingHTML(preview.base_rounding)}<p>${esc(t("仅生成原生凭证草稿，未记账"))}</p>`;
	}
	// Presentation only: subtract returned exact/rounded decimal text without binary floats.
	function roundingDifference(rounded, exact) {
		const parts = (value) => {
			if (value == null || String(value).length > 200) return null;
			const match = /^([+-]?)(\d+)(?:\.(\d+))?(?:e([+-]?\d+))?$/i.exec(String(value));
			if (!match) return null;
			let scale = (match[3] || "").length - Number(match[4] || 0);
			if (Math.abs(scale) > 100) return null;
			let digits = BigInt(match[2] + (match[3] || "")) * (match[1] === "-" ? -1n : 1n);
			if (scale < 0) {
				digits *= 10n ** BigInt(-scale);
				scale = 0;
			}
			return { digits, scale };
		};
		const a = parts(rounded),
			b = parts(exact);
		if (!a || !b) return null;
		const scale = Math.max(a.scale, b.scale),
			difference =
				a.digits * 10n ** BigInt(scale - a.scale) -
				b.digits * 10n ** BigInt(scale - b.scale);
		if (!difference) return "0";
		const digits = String(difference < 0n ? -difference : difference).padStart(scale + 1, "0");
		const fraction = scale ? digits.slice(-scale).replace(/0+$/, "") : "";
		return `${
			difference < 0n ? "-" : ""
		}${scale ? digits.slice(0, -scale) : digits}${fraction ? "." + fraction : ""}`;
	}
	function roundingHTML(evidence) {
		const heading = esc(t("本位币舍入差额"));
		if (!Array.isArray(evidence) || !evidence.length)
			return `<p>${heading} ${money(evidence)} · ${esc(t("舍入依据不完整，需复核"))}</p>`;
		const rows = evidence.map((row) => ({
			...row,
			difference: roundingDifference(row?.rounded_base, row?.exact_base),
		}));
		const incomplete = rows.some((row) => row.difference === null),
			changes = rows.filter((row) => row.difference !== null && row.difference !== "0");
		const summary =
			changes
				.map(
					(row) =>
						`<span title="${esc(t("精确差额"))} ${esc(row.difference)}">${display(
							row.account
						)}：${money(row.difference)}${
							money(row.difference) === "0.00" ? ` (${esc(t("差额不足 0.01"))})` : ""
						}</span>`
				)
				.join(" · ") || (incomplete ? "—" : esc(t("无舍入差额")));
		return `<p>${heading} ${summary}${incomplete ? ` · ${esc(t("舍入依据不完整，需复核"))}` : ""}</p><details class="dlp-operating-rounding"><summary>${esc(t("精确舍入依据"))}</summary><div class="dlp-operating-table-wrap"><table class="table table-bordered"><thead><tr>${["科目", "精确本位币金额", "舍入后本位币金额", "差额（舍入后 - 精确）"].map((label) => `<th>${esc(t(label))}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr><td>${display(row.account)}</td><td>${display(row.exact_base)}</td><td>${display(row.rounded_base)}</td><td>${display(row.difference)}</td></tr>`).join("")}</tbody></table></div></details>`;
	}
	function controlDefinitions(detail) {
		const kind =
			detail.mapping?.party_type ||
			(detail.source.application_type === "payment"
				? "Supplier"
				: detail.source.application_type === "reimbursement"
				? "Employee"
				: "");
		const defs = [
			["classification", "Data", null, "费用分类"],
			["party", "Link", kind, "往来方"],
			["payable_account", "Link", "Account", "应付科目"],
			["payable_exchange_rate", "Data", null, "应付账面汇率"],
			["posting_date", "Date", null, "费用凭证日期"],
			["recognition_mode", "Select", "\nnew\nexisting", "费用确认方式"],
			["actual_incurred", "Check", null, "确认费用已实际发生"],
			["no_existing_erp_coverage", "Check", null, "确认 ERP 尚未覆盖本项费用"],
			["existing_erp_coverage_confirmed", "Check", null, "确认费用已有 ERP 覆盖"],
		].map(([fieldname, fieldtype, options, label]) => ({
			fieldname,
			fieldtype,
			options,
			label,
			reqd: [
				"party",
				"payable_account",
				"payable_exchange_rate",
				"posting_date",
				"recognition_mode",
			].includes(fieldname),
		}));
		if (detail.source.application_type === "unclassified")
			defs.unshift({
				fieldname: "application_type",
				fieldtype: "Select",
				options: [
					{ value: "", label: "" },
					{ value: "payment", label: t("付款申请") },
					{ value: "reimbursement", label: t("费用报销") },
				],
				label: t("明确申请类型"),
				reqd: 1,
			});
		defs.find((df) => df.fieldname === "recognition_mode").options = [
			{ value: "", label: "" },
			{ value: "new", label: t("新确认费用") },
			{ value: "existing", label: t("关联已有 ERP 凭证") },
		];
		return defs;
	}
	const canEditPayment = (detail) =>
		Boolean(detail?.mapping?.payable_exchange_rate && recognitionEvents(detail).length);
	function configureDrawer(drawer) {
		const original = drawer.setBusy.bind(drawer);
		drawer.actions = [];
		drawer.panel.addClass("dlp-operating-drawer");
		drawer.refreshEligibility = () => {
			if (!drawer.alive()) return;
			for (const [button, eligible] of drawer.actions)
				button.prop("disabled", drawer.busy || !eligible());
			for (const control of drawer.controls)
				control.$input?.prop("disabled", drawer.busy || Boolean(control.df?.read_only));
		};
		drawer.setBusy = (value, operation) => {
			original(value, operation);
			drawer.refreshEligibility();
		};
		return drawer;
	}
	async function makeControl(drawer, holder, df, value, touched, query) {
		const load = drawer.loadId,
			generation = drawer.operatingControlGeneration;
		const current = () =>
			drawer.alive() &&
			load === drawer.loadId &&
			generation === drawer.operatingControlGeneration;
		if (!current()) return null;
		let initialized = false;
		const control = root.frappe.ui.form.make_control({
			parent: holder,
			df: {
				...df,
				label: t(df.label),
				change: () => {
					if (initialized && !control.df?.read_only && current())
						touched?.(control.get_value());
				},
			},
			render_input: true,
		});
		control.df ||= df;
		drawer.controls.push(control);
		control.$input?.attr("aria-label", t(df.label));
		if (query) control.get_query = query;
		if (
			df.fieldtype === "Check" &&
			df.read_only &&
			control.disp_area &&
			control.set_disp_area
		) {
			const renderDisplay = control.set_disp_area.bind(control);
			control.set_disp_area = (displayValue) => {
				if (!current()) return;
				renderDisplay(displayValue);
				// Native readonly Check formatter supplies a CSS class but no checked attribute.
				root.$(control.disp_area)
					.find('input[type="checkbox"]')
					.prop("checked", boolean(control.value ?? displayValue))
					.prop("disabled", true)
					.attr("aria-label", t(df.label));
			};
		}
		await control.set_value(df.fieldtype === "Check" ? (boolean(value) ? 1 : 0) : value ?? "");
		if (!current()) {
			root.DeepLinkERPPurchasePayments.disposeControls([control]);
			return null;
		}
		if (
			df.read_only &&
			df.fieldtype === "Data" &&
			/(?:^|_)(?:source_amount|bank_amount|amount)$/.test(df.fieldname)
		) {
			control.$input?.val(money(value));
			if (control.disp_area)
				root.$(control.disp_area)
					.text(money(value))
					.attr("title", String(value ?? ""));
		}
		control.$input?.prop("disabled", drawer.busy || Boolean(control.df.read_only));
		initialized = true;
		control.$input?.on("input.dlpDrawer change.dlpDrawer", () => {
			if (initialized && !control.df?.read_only && current()) touched?.(control.get_value());
		});
		return control;
	}
	function action(drawer, holder, label, handler, eligible = () => true, primary = false) {
		const button = root
			.$(
				`<button type="button" class="btn ${
					primary ? "btn-primary" : "btn-default"
				}">${esc(t(label))}</button>`
			)
			.appendTo(holder);
		drawer.actions.push([button, eligible]);
		button.on("click.dlpDrawer", async () => {
			if (!drawer.alive() || drawer.busy || !eligible()) return;
			try {
				drawer.panel.find(".dlp-error").empty();
				await handler();
			} catch (error) {
				if (drawer.alive()) drawer.error(error);
			} finally {
				drawer.refreshEligibility();
			}
		});
		return button;
	}
	async function uiTask(drawer, fn, operation = "write") {
		if (!drawer.alive() || drawer.busy) return;
		const task = (drawer.uiTask = (drawer.uiTask || 0) + 1),
			load = drawer.loadId;
		const current = () => drawer.alive() && task === drawer.uiTask && load === drawer.loadId;
		drawer.setBusy(true, operation);
		try {
			return await fn(current);
		} finally {
			if (current()) drawer.setBusy(false);
		}
	}
	async function downloadAttachment(sourceId, file, alive) {
		const url = new URL(root.frappe.request.url || "/", root.location.href);
		if (url.origin !== root.location.origin || !root.frappe.csrf_token)
			throw new Error(t("附件下载需要同源请求和 CSRF 验证，请刷新。"));
		const body = new URLSearchParams({
			cmd: API + "download_operating_expense_attachment",
			source_id: sourceId,
			attachment_source_id: file.source_id,
		});
		const response = await root.fetch(url.href, {
			method: "POST",
			credentials: "same-origin",
			headers: {
				"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
				"X-Frappe-CSRF-Token": root.frappe.csrf_token,
			},
			body: body.toString(),
		});
		const contentType = response.headers.get("content-type") || "";
		if (!response.ok || /(?:json|html)/i.test(contentType))
			throw new Error(t("附件下载失败，请核对权限或附件版本。"));
		if (Number(response.headers.get("content-length") || 0) > 32 * 1024 * 1024)
			throw new Error(t("附件过大，请在请款网站核对。"));
		const bytes = await response.arrayBuffer();
		if (!alive()) return;
		if (bytes.byteLength > 32 * 1024 * 1024)
			throw new Error(t("附件过大，请在请款网站核对。"));
		const downloadURL = root.URL.createObjectURL(
				new root.Blob([bytes], { type: contentType })
			),
			link = root.document.createElement("a");
		link.href = downloadURL;
		link.download = String(file.filename || "附件")
			.split(/[\\/]/)
			.pop();
		root.document.body.appendChild(link);
		link.click();
		link.remove();
		root.setTimeout(() => root.URL.revokeObjectURL(downloadURL), 1000);
	}
	async function open(sourceId, onRefresh = () => {}, initialTab = "payments") {
		const shared = root.DeepLinkERPPurchasePayments,
			drawer = shared.createDrawer(t("运营支出详情"), true, {
				desktopNonModal: true,
				guardNavigation: true,
			});
		if (!drawer) return;
		configureDrawer(drawer);
		drawer.activeOperatingTab=["payments","approvals","request","vouchers"].includes(initialTab)?initialTab:"payments";
		let w,
			existingJE = "",
			lastResult = null;
		const eligible = () =>
			Boolean(
				canFinance() &&
					w?.detail?.company &&
					w.detail.source.approvals?.eligibility === "eligible" &&
					!w.detail.mapping_issue &&
					!(w.detail.events || []).some((event) => event.docstatus === 2 || event.issue)
			);
		const financialLocked = () =>
			Boolean(
				w?.detail?.events?.some(
					(event) => event.operation === "expense" || event.journal_entry || event.issue
				)
			);
		const editable = () => eligible() && !financialLocked();
		const accountQuery = (types) => () => ({
			filters: {
				company: w.detail.company,
				is_group: 0,
				disabled: 0,
				...(types ? { account_type: ["in", types] } : { root_type: "Expense" }),
			},
		});
		const centerQuery = () => ({ filters: { company: w.detail.company, is_group: 0 } });
		const projectQuery = () => ({ filters: { company: w.detail.company } });
		const refresh = () => {
			if (drawer.alive()) return onRefresh();
		};
		const resultHTML = (result) =>
			`${nativeLink(result.journal_entry)} · ${esc(journalState(result.docstatus))}${
				result.settlement_state ? ` · ${stateDisplay(result.settlement_state)}` : ""
			}`;
		const renderPreview = (preview, payment) =>
			(payment
				? drawer.panel
						.find("[data-payment-preview]")
						.filter((_index, node) => node.dataset.paymentPreview === payment)
				: drawer.panel.find(".dlp-operating-expense-preview")
			).html(previewHTML(preview));
		async function finish(operation) {
			const result = await operation;
			if (!result || !drawer.alive()) return;
			try {
				await w.load();
				await refresh();
			} catch (error) {
				if (drawer.alive())
					drawer.error(
						new Error(t("原生凭证已返回，刷新失败。请核对已显示凭证，勿重复办理。"))
					);
			}
			return result;
		}
		async function render(detail) {
			const load = drawer.loadId;
			drawer.paymentDirty = null;
			shared.disposeControls(drawer.controls);
			drawer.actions = [];
			existingJE = "";
			const content = `${sourceHTML(detail)}${
				!detail.company && isManager()
					? '<section class="dlp-operating-section"><div class="dlp-operating-company"></div><div class="dlp-operating-company-actions dlp-operating-actions"></div></section>'
					: ""
			}<details class="dlp-operating-section dlp-operating-advanced" data-operating-pane="vouchers"><summary>${esc(t("会计与高级设置"))}</summary>${
				!eligible()
					? `<p class="text-warning">${esc(
							t("当前需复核或缺少财务权限，来源仍可查看。")
					  )}</p>`
					: ""
			}${
				financialLocked()
					? `<p class="text-muted">${esc(t("已有原生凭证关联，费用确认不可覆盖。"))}</p>`
					: ""
			}<div class="dlp-operating-fields"></div><div class="dlp-operating-table-wrap"><table class="table table-bordered dlp-operating-allocations"><thead><tr>${[
				"费用 / 税费科目",
				"来源金额",
				"科目币种金额",
				"汇率",
				"成本中心",
				"项目",
				"操作",
			]
				.map((label) => `<th>${esc(t(label))}</th>`)
				.join(
					""
				)}</tr></thead><tbody class="dlp-operating-lines"></tbody></table></div><div class="dlp-operating-line-actions dlp-operating-actions"></div><div class="dlp-operating-expense-actions dlp-operating-actions"></div><div class="dlp-operating-existing"></div><div class="dlp-operating-existing-actions dlp-operating-actions"></div><div class="dlp-operating-expense-preview"></div></details><details class="dlp-operating-section dlp-operating-advanced" data-operating-pane="vouchers"><summary>${esc(
				t("按实际付款生成结算草稿")
			)}</summary><p class="text-muted">${esc(
				t(
					"先保存费用映射并生成或关联费用确认凭证，再逐笔维护银行科目与汇率。此处不登记或执行付款。"
				)
			)}</p><div class="dlp-operating-payments"></div></details><div class="dlp-operating-result" role="status"></div><div class="dlp-error text-danger" role="alert"></div>`;
			drawer.panel.find(".dlp-payment-body").html(content);
			drawer.panel.find("footer .dlp-operating-footer").remove();
			const footer = root
				.$('<div class="dlp-operating-footer"></div>')
				.appendTo(drawer.panel.find("footer"));
			action(drawer, footer, "刷新抽屉", async () => {
				if (await drawer.beforeClose()) return w.load();
			});
			if (!detail.company && isManager()) {
				let company = "";
				const control = await makeControl(
					drawer,
					drawer.panel.find(".dlp-operating-company"),
					{
						fieldname: "legal_company",
						fieldtype: "Link",
						options: "Company",
						label: t("明确法律公司"),
						reqd: 1,
					},
					"",
					(value) => {
						company = value;
						w.invalidate();
					}
				);
				if (!control) return;
				action(
					drawer,
					drawer.panel.find(".dlp-operating-company-actions"),
					"保存法律公司",
					async () => {
						const result = await w.saveCompany(company);
						if (result && drawer.alive()) {
							await w.load();
							await refresh();
						}
					},
					() => isManager() && Boolean(company)
				);
			}
			for (const df of controlDefinitions(detail)) {
				if (!drawer.alive() || load !== drawer.loadId) return;
				const values = w.session.mapping(),
					holder = root
						.$(`<div data-finance-field="${df.fieldname}"></div>`)
						.appendTo(drawer.panel.find(".dlp-operating-fields"));
				const query =
					df.fieldname === "payable_account"
						? accountQuery(["Payable"])
						: df.fieldname === "party" && values.party_type === "Employee"
						? () => ({ filters: { company: detail.company } })
						: null;
				const control = await makeControl(
					drawer,
					holder,
					{
						...df,
						...(df.fieldname === "party"
							? {
									options: values.party_type || "",
									read_only: !editable() || !values.party_type,
							  }
							: { read_only: !editable() }),
					},
					values[df.fieldname],
					(value) => {
						const previous = w.session.mapping()[df.fieldname] ?? "";
						if (
							df.fieldname === "application_type" &&
							String(value ?? "") !== String(previous)
						) {
							renderForm(() => w.session.touch(df.fieldname, value))
								.then(async (rebuilt) => {
									if (!rebuilt && drawer.alive() && load === drawer.loadId)
										await control.set_value(previous);
								})
								.catch((error) => {
									if (drawer.alive()) drawer.error(error);
								});
							return;
						}
						w.session.touch(df.fieldname, value);
						updateVisibility();
					},
					query
				);
				if (!control) return;
			}
			async function renderLines() {
				const holder = drawer.panel.find(".dlp-operating-lines");
				holder.empty();
				for (const [index, line] of w.session.mapping().expense_lines.entries()) {
					if (!drawer.alive() || load !== drawer.loadId) return;
					const row = root
						.$(
							`<tr data-allocation="${index}">${[
								"account",
								"source_amount",
								"amount",
								"exchange_rate",
								"cost_center",
								"project",
							]
								.map((field) => `<td data-allocation-field="${field}"></td>`)
								.join("")}<td class="dlp-operating-remove-line"></td></tr>`
						)
						.appendTo(holder);
					for (const [field, type, options, label] of [
						["account", "Link", "Account", "费用 / 税费科目"],
						["source_amount", "Data", null, "来源金额"],
						["amount", "Data", null, "科目币种金额"],
						["exchange_rate", "Data", null, "汇率"],
						["cost_center", "Link", "Cost Center", "成本中心"],
						["project", "Link", "Project", "项目"],
					]) {
						const control = await makeControl(
							drawer,
							row.find(`[data-allocation-field="${field}"]`),
							{
								fieldname: `line_${index}_${field}`,
								fieldtype: type,
								options,
								label,
								read_only: !editable(),
							},
							line[field],
							(value) => w.session.touchLine(index, field, value),
							field === "account"
								? () => ({
										filters: {
											company: detail.company,
											is_group: 0,
											disabled: 0,
										},
										or_filters: [
											["root_type", "=", "Expense"],
											["account_type", "=", "Tax"],
										],
								  })
								: field === "cost_center"
								? centerQuery
								: field === "project"
								? projectQuery
								: null
						);
						if (!control) return;
					}
					action(
						drawer,
						row.find(".dlp-operating-remove-line"),
						"移除",
						() => renderForm(() => w.session.removeLine(index)),
						editable
					);
				}
			}
			await renderLines();
			if (!drawer.alive() || load !== drawer.loadId) return;
			action(
				drawer,
				drawer.panel.find(".dlp-operating-line-actions"),
				"新增费用分摊",
				() => renderForm(() => w.session.addLine()),
				() => editable() && w.session.mapping().expense_lines.length < 100
			);
			action(
				drawer,
				drawer.panel.find(".dlp-operating-expense-actions"),
				"保存财务映射",
				async () => {
					await w.save();
					if (drawer.alive()) {
						await renderForm();
						await refresh();
					}
				},
				() => editable() && w.session.ready(),
				true
			);
			action(
				drawer,
				drawer.panel.find(".dlp-operating-expense-actions"),
				"预览费用凭证",
				() => w.preview(),
				() =>
					eligible() &&
					!hasPostedRecognition(detail) &&
					!w.session.dirty() &&
					w.session.ready()
			);
			action(
				drawer,
				drawer.panel.find(".dlp-operating-expense-actions"),
				"生成费用凭证草稿",
				() => finish(w.create()),
				() => eligible() && w.canCreate(),
				true
			);
			const existingControl = await makeControl(
				drawer,
				drawer.panel.find(".dlp-operating-existing"),
				{
					fieldname: "existing_journal",
					fieldtype: "Link",
					options: "Journal Entry",
					label: t("选择已有原生凭证"),
					read_only: !eligible() || hasPostedRecognition(detail),
				},
				"",
				(value) => {
					existingJE = value;
					drawer.refreshEligibility();
				},
				() => ({ filters: { company: detail.company, docstatus: ["in", [0, 1]] } })
			);
			if (!existingControl) return;
			action(
				drawer,
				drawer.panel.find(".dlp-operating-existing-actions"),
				"明确关联已有凭证",
				() => finish(w.link(existingJE)),
				() => eligible() && Boolean(existingJE) && w.canLink(),
				true
			);
			for (const payment of detail.source.payments || []) {
				if (!drawer.alive() || load !== drawer.loadId) return;
				const id = payment.source_id,
					terms = w.session.mapping().payments[id] || {},
					recorded = payment.evidence_status === "recorded";
				const section = root
					.$(
						`<section class="dlp-operating-section" data-payment="${esc(
							id
						)}"><p><strong>${display(id)}</strong> · ${display(
							payment.payment_date
						)} · ${money(payment.amount)} ${display(payment.currency)} · ${display(
							payment.evidence_status
						)}</p><div class="dlp-operating-fields"></div><div class="dlp-operating-actions"></div><div data-payment-preview="${esc(
							id
						)}"></div></section>`
					)
					.appendTo(drawer.panel.find(".dlp-operating-payments"));
				const paymentEditable = () =>
					eligible() &&
					recorded &&
					canEditPayment(detail) &&
					!detail.events?.some((event) => event.payment_source_id === id);
				for (const [field, type, options, label] of [
					["bank_account", "Link", "Account", "实际付款银行 / 现金科目"],
					["bank_amount", "Data", null, "银行科目币种金额"],
					["payable_exchange_rate", "Data", null, "已保存应付账面汇率"],
					["bank_exchange_rate", "Data", null, "银行汇率"],
					["exchange_difference_account", "Link", "Account", "汇兑损益科目"],
					["cost_center", "Link", "Cost Center", "汇兑损益成本中心"],
				]) {
					const control = await makeControl(
						drawer,
						root.$("<div></div>").appendTo(section.find(".dlp-operating-fields")),
						{
							fieldname: field,
							fieldtype: type,
							options,
							label,
							read_only: field === "payable_exchange_rate" || !paymentEditable(),
						},
						field === "payable_exchange_rate"
							? detail.mapping?.payable_exchange_rate || ""
							: terms[field],
						(value) => w.session.touchPayment(id, field, value),
						field === "bank_account"
							? accountQuery(["Bank", "Cash"])
							: field === "exchange_difference_account"
							? () => ({
									filters: { company: detail.company, is_group: 0, disabled: 0 },
									or_filters: [
										["root_type", "=", "Expense"],
										["root_type", "=", "Income"],
									],
							  })
							: field === "cost_center"
							? centerQuery
							: null
					);
					if (!control) return;
				}
				if (!canEditPayment(detail))
					section.append(
						`<p class="text-muted">${esc(
							t("请先保存费用映射并生成或关联费用确认凭证，再维护本笔结算。")
						)}</p>`
					);
				action(
					drawer,
					section.find(".dlp-operating-actions"),
					"保存付款映射",
					async () => {
						await w.save();
						if (drawer.alive()) {
							await renderForm();
							await refresh();
						}
					},
					() => paymentEditable() && w.session.ready()
				);
				action(
					drawer,
					section.find(".dlp-operating-actions"),
					"预览结算凭证",
					() => w.preview(id),
					() =>
						eligible() &&
						recorded &&
						!w.session.dirty() &&
						canEditPayment(detail) &&
						!settlementBlocked(detail, id)
				);
				action(
					drawer,
					section.find(".dlp-operating-actions"),
					"生成结算凭证草稿",
					() => finish(w.create(id)),
					() => eligible() && recorded && w.canCreate(id),
					true
				);
			}
			drawer.panel
				.find(".dlp-operating-download")
				.off(".dlpDrawer")
				.on("click.dlpDrawer", (event) => {
					const file = (detail.source.attachments || []).find(
						(row) => row.source_id === event.currentTarget.dataset.attachment
					);
					if (!file) return;
					uiTask(drawer, (current) => downloadAttachment(sourceId, file, current), "read").catch(
						(error) => {
							if (drawer.alive()) drawer.error(error);
						}
					);
				});
			if (lastResult)
				drawer.panel.find(".dlp-operating-result").html(resultHTML(lastResult));
			if(root.DeepLinkERPOperatingPaymentPanel) await root.DeepLinkERPOperatingPaymentPanel.mount(drawer,detail,{makeControl,action,uiTask,canFinance,sourceId,previewHTML,voucherRequest:call,request:(method,args)=>call(method,args,"deeplinkerp_branding.services.operating_payment_service."),reload:async()=>{await w.load();await refresh();}});
			updateVisibility();
			drawer.refreshEligibility();
		}
		function updateVisibility() {
			if (!w?.session || !drawer.alive()) return;
			const mode = w.session.mapping().recognition_mode;
			drawer.panel
				.find('[data-finance-field="no_existing_erp_coverage"]')
				.toggle(mode === "new");
			drawer.panel
				.find('[data-finance-field="existing_erp_coverage_confirmed"]')
				.toggle(mode === "existing");
			drawer.panel
				.find(".dlp-operating-existing,.dlp-operating-existing-actions")
				.toggle(mode === "existing");
			drawer.refreshEligibility();
		}
		async function renderForm(update) {
			if (!drawer.alive() || drawer.busy) return false;
			if (drawer.paymentDirty?.() && !(await drawer.beforeClose())) return false;
			if (!drawer.alive() || drawer.busy) return false;
			update?.();
			drawer.loadId++;
			await uiTask(drawer, () => render(w.detail), "read");
			return true;
		}
		w = workflow(drawer, sourceId, {
			onDetail: render,
			onPreview: renderPreview,
			onInvalidate: () =>
				drawer.panel.find(".dlp-operating-expense-preview,[data-payment-preview]").empty(),
			onState: () => {
				if (!drawer.alive()) return;
				if (!w?.previewResult())
					drawer.panel.find(".dlp-operating-expense-preview").empty();
				if (w?.session?.dirty()) drawer.panel.find("[data-payment-preview]").empty();
				drawer.refreshEligibility();
			},
			onResult: (result) => {
				lastResult = result;
				drawer.panel.find(".dlp-operating-result").html(resultHTML(result));
			},
		});
		drawer.beforeUnloadShouldBlock = () => Boolean(w.session?.dirty() || drawer.paymentDirty?.());
		drawer.beforeClose = async () =>
			!(w.session?.dirty() || drawer.paymentDirty?.()) ||
			(await new Promise((resolve) =>
				root.frappe.confirm(
					t("输入尚未保存，关闭会丢弃当前输入，继续？"),
					() => resolve(true),
					() => resolve(false)
				)
			));
		try {
			await w.load();
		} catch (error) {
			if (drawer.alive()) {
				drawer.panel
					.find(".dlp-payment-body")
					.html('<div class="dlp-error text-danger" role="alert"></div>');
				drawer.error(error);
			}
		}
		return drawer;
	}
	function settingsWorkflow(drawer, hooks = {}) {
		let revision = 0,
			savedRevision = 0,
			rows = [],
			confirmed = false,
			previewRevision = null;
		const state = () => {
			if (drawer.alive()) hooks.onState?.(w);
		};
		const reset = (settings) => {
			w.settings = Object.fromEntries(
				[
					"enabled",
					"token_configured",
					"company_mappings",
					"source_url",
					"last_sync_at",
					"last_error",
				].map((field) => [field, settings[field]])
			);
			rows = Object.entries(settings.company_mappings || {}).map(
				([source_company, company]) => ({ source_company, company })
			);
			w.token = "";
			revision++;
			savedRevision = revision;
			w.invalidate();
		};
		async function task(fn) {
			if (!isManager()) throw new Error(t("仅系统管理员可管理同步"));
			return uiTask(drawer, fn);
		}
		async function saveValues(mappings, token) {
			const start = revision;
			w.invalidate();
			return task(async (current) => {
				const result = await call("save_sync_settings", {
					company_mappings: JSON.stringify(mappings),
					...(token ? { api_token: token } : {}),
				});
				if (!current() || start !== revision) return;
				reset(result);
				await hooks.onSettings?.(w);
				state();
				return result;
			});
		}
		const w = {
			settings: null,
			token: "",
			previewResult: null,
			get rows() {
				return clone(rows);
			},
			dirty: () => revision !== savedRevision,
			invalidate() {
				this.previewResult = null;
				previewRevision = null;
				confirmed = false;
				state();
			},
			changed() {
				revision++;
				this.invalidate();
			},
			touchMap(index, field, value) {
				if (!rows[index] || !["source_company", "company"].includes(field))
					throw new Error(t("公司映射字段无效"));
				const next = String(value ?? "");
				if (rows[index][field] === next) return;
				rows[index][field] = next;
				this.changed();
			},
			addMap() {
				if (rows.length >= 200) throw new Error(t("最多 200 行公司映射"));
				rows.push({ source_company: "", company: "" });
				this.changed();
			},
			removeMap(index) {
				rows.splice(index, 1);
				this.changed();
			},
			touchToken(value) {
				const next = String(value ?? "");
				if (this.token === next) return;
				this.token = next;
				this.changed();
			},
			confirm(value) {
				confirmed = boolean(value);
				state();
			},
			async load() {
				drawer.loadId++;
				this.invalidate();
				return task(async (current) => {
					const result = await call("get_sync_settings");
					if (!current()) return;
					reset(result);
					await hooks.onSettings?.(this);
					state();
					return result;
				});
			},
			save() {
				const mappings = {};
				for (const row of rows) {
					if (
						!row.source_company.trim() ||
						!row.company ||
						Object.hasOwn(mappings, row.source_company)
					)
						return Promise.reject(
							new Error(t("请明确每行来源法律标识和 ERP 公司，来源标识不能重复"))
						);
					mappings[row.source_company] = row.company;
				}
				return saveValues(mappings, this.token);
			},
			disable() {
				return saveValues(this.settings.company_mappings || {}, "");
			},
			canPreview() {
				return Boolean(this.settings?.token_configured && !this.dirty());
			},
			async preview() {
				if (!this.canPreview()) throw new Error(t("请先保存公司映射和同步密钥，再预览"));
				const start = revision;
				this.invalidate();
				return task(async (current) => {
					const result = await call("preview_sync");
					if (!current() || start !== revision) return;
					if (!result?.preview_fingerprint || !Array.isArray(result.sources))
						throw new Error(t("同步预览无效，请重新预览"));
					this.previewResult = result;
					previewRevision = revision;
					confirmed = false;
					hooks.onPreview?.(result);
					state();
					return result;
				});
			},
			canEnable() {
				return Boolean(
					this.previewResult?.preview_fingerprint &&
						previewRevision === revision &&
						confirmed &&
						!this.dirty()
				);
			},
			async enable() {
				if (!this.canEnable())
					throw new Error(t("请先取得当前同步预览并明确确认法律公司映射"));
				const start = revision,
					fingerprint = this.previewResult.preview_fingerprint;
				return task(async (current) => {
					const result = await call("enable_sync", { preview_fingerprint: fingerprint });
					if (!current() || start !== revision) return;
					reset(result);
					await hooks.onSettings?.(this);
					state();
					return result;
				});
			},
			canSync() {
				return Boolean(this.settings?.enabled && !this.dirty());
			},
			async sync() {
				if (!this.canSync()) throw new Error(t("同步尚未启用，或设置存在未保存修改"));
				return task(async (current) => {
					const result = await call("sync_operating_expenses");
					if (current()) {
						hooks.onSynced?.(result);
						state();
						return result;
					}
				});
			},
		};
		return w;
	}
	async function openSettings(onRefresh = () => {}) {
		if (!isManager()) throw new Error(t("仅系统管理员可管理同步"));
		const drawer = root.DeepLinkERPPurchasePayments.createDrawer(t("运营支出同步设置"), true);
		if (!drawer) return;
		configureDrawer(drawer);
		let w,
			confirmControl,
			lastSync = "";
		function paintPreview(result) {
			const fields = [
				"source_id",
				"application_type",
				"source_company",
				"source_sheet",
				"company",
			];
			drawer.panel.find(".dlp-operating-sync-preview").html(
				`<p>${esc(t("请核对来源类型、来源法律标识与 ERP 法律公司。"))}</p>${
					result.has_more
						? `<p class="text-warning">${esc(
								t("仅预览前 100 条，来源还有更多记录。")
						  )}</p>`
						: ""
				}<div class="dlp-operating-table-wrap"><table class="table table-bordered"><thead><tr>${[
					"申请编号",
					"申请类型",
					"来源法律标识",
					"来源归档表",
					"法律公司",
				]
					.map((label) => `<th>${esc(t(label))}</th>`)
					.join("")}</tr></thead><tbody>${result.sources
					.slice(0, 100)
					.map(
						(source) =>
							`<tr>${fields
								.map(
									(field) =>
										`<td>${
											field === "application_type"
												? esc(
														root.DeepLinkERPOperatingExpenses.typeLabel(
															source[field]
														)
												  )
												: display(source[field])
										}</td>`
								)
								.join("")}</tr>`
					)
					.join("")}</tbody></table></div>`
			);
			confirmControl?.set_value(0);
			drawer.panel.find(".dlp-operating-sync-confirm").show();
		}
		async function renderSettings() {
			drawer.operatingControlGeneration = (drawer.operatingControlGeneration || 0) + 1;
			const load = drawer.loadId,
				settings = w.settings;
			root.DeepLinkERPPurchasePayments.disposeControls(drawer.controls);
			drawer.actions = [];
			confirmControl = null;
			drawer.panel
				.find(".dlp-payment-body")
				.html(
					`<section class="dlp-operating-section"><div class="dlp-operating-facts"><div>${esc(
						t("同步状态")
					)} · ${esc(t(settings.enabled ? "已启用" : "未启用"))}</div><div>${esc(
						t("同步密钥")
					)} · ${esc(
						t(settings.token_configured ? "已配置" : "未配置")
					)}</div><div>${esc(t("固定来源"))} · ${display(
						settings.source_url || "https://payment.yueweiportal.com"
					)}</div><div>${esc(t("上次同步"))} · ${display(
						settings.last_sync_at
					)}</div></div>${
						settings.last_error
							? `<p class="text-warning">${display(settings.last_error)}</p>`
							: ""
					}<p class="text-muted">${esc(
						t("保存设置会停用同步，并使已有同步预览失效。密钥留空则保留原密钥。")
					)}</p><div class="dlp-operating-token"></div></section><section class="dlp-operating-section"><strong>${esc(
						t("来源法律标识 → ERP 法律公司")
					)}</strong><p class="text-muted">${esc(
						t("业务单元和归档表不代表法律公司，请明确映射。")
					)}</p><div class="dlp-operating-table-wrap"><table class="table table-bordered"><thead><tr><th>${esc(
						t("来源法律标识")
					)}</th><th>${esc(t("法律公司"))}</th><th>${esc(
						t("操作")
					)}</th></tr></thead><tbody class="dlp-operating-company-maps"></tbody></table></div><div class="dlp-operating-map-actions dlp-operating-actions"></div></section><div class="dlp-operating-settings-actions dlp-operating-actions"></div><div class="dlp-operating-sync-preview"></div><div class="dlp-operating-sync-confirm"></div><div class="dlp-operating-sync-actions dlp-operating-actions"></div><p class="dlp-operating-sync-result" role="status">${esc(
						lastSync
					)}</p><div class="dlp-error text-danger" role="alert"></div>`
				);
			const tokenControl = await makeControl(
				drawer,
				drawer.panel.find(".dlp-operating-token"),
				{ fieldname: "api_token", fieldtype: "Password", label: t("新的同步密钥（可选）") },
				w.token,
				(value) => w.touchToken(value)
			);
			if (!tokenControl) return;
			for (const [index, row] of w.rows.entries()) {
				if (!drawer.alive() || load !== drawer.loadId) return;
				const holder = root
					.$(
						'<tr><td class="dlp-operating-map-source"></td><td class="dlp-operating-map-company"></td><td class="dlp-operating-map-remove"></td></tr>'
					)
					.appendTo(drawer.panel.find(".dlp-operating-company-maps"));
				for (const [field, type, options, label, selector] of [
					["source_company", "Data", null, "来源法律标识", ".dlp-operating-map-source"],
					["company", "Link", "Company", "法律公司", ".dlp-operating-map-company"],
				]) {
					const control = await makeControl(
						drawer,
						holder.find(selector),
						{
							fieldname: `company_map_${index}_${field}`,
							fieldtype: type,
							options,
							label,
							reqd: 1,
						},
						row[field],
						(value) => w.touchMap(index, field, value)
					);
					if (!control) return;
				}
				action(drawer, holder.find(".dlp-operating-map-remove"), "移除", async () => {
					w.removeMap(index);
					await uiTask(drawer, renderSettings);
				});
			}
			action(
				drawer,
				drawer.panel.find(".dlp-operating-map-actions"),
				"新增公司映射",
				async () => {
					w.addMap();
					await uiTask(drawer, renderSettings);
				},
				() => w.rows.length < 200
			);
			action(
				drawer,
				drawer.panel.find(".dlp-operating-settings-actions"),
				"保存设置并停用同步",
				() => w.save(),
				() => true,
				true
			);
			action(
				drawer,
				drawer.panel.find(".dlp-operating-settings-actions"),
				"停用同步",
				() => w.disable(),
				() => Boolean(w.settings.enabled)
			);
			action(
				drawer,
				drawer.panel.find(".dlp-operating-settings-actions"),
				"预览来源与公司映射",
				() => w.preview(),
				() => w.canPreview()
			);
			confirmControl = await makeControl(
				drawer,
				drawer.panel.find(".dlp-operating-sync-confirm"),
				{
					fieldname: "confirm_sync_preview",
					fieldtype: "Check",
					label: t("我已核对来源类型和法律公司映射，确认启用同步"),
				},
				0,
				(value) => w.confirm(value)
			);
			if (!confirmControl) return;
			drawer.panel.find(".dlp-operating-sync-confirm").hide();
			action(
				drawer,
				drawer.panel.find(".dlp-operating-sync-actions"),
				"确认启用同步",
				() => w.enable(),
				() => w.canEnable(),
				true
			);
			action(
				drawer,
				drawer.panel.find(".dlp-operating-sync-actions"),
				"手动同步并刷新",
				async () => {
					const result = await w.sync();
					if (result && drawer.alive()) {
						await w.load();
						await onRefresh();
					}
				},
				() => w.canSync()
			);
			drawer.panel.find("footer .dlp-operating-footer").remove();
			const footer = root
				.$('<div class="dlp-operating-footer"></div>')
				.appendTo(drawer.panel.find("footer"));
			action(drawer, footer, "刷新设置", () => w.load());
			drawer.refreshEligibility();
		}
		w = settingsWorkflow(drawer, {
			onSettings: renderSettings,
			onPreview: paintPreview,
			onState: () => {
				if (!drawer.alive()) return;
				if (!w?.previewResult) {
					drawer.panel.find(".dlp-operating-sync-preview").empty();
					drawer.panel.find(".dlp-operating-sync-confirm").hide();
				}
				drawer.refreshEligibility();
			},
			onSynced: (result) => {
				lastSync = `${t("本次同步记录")} ${result.count} · ${t(
					result.end ? "同步到当前边界" : "还有后续记录，请再次手动同步"
				)}`;
				drawer.panel.find(".dlp-operating-sync-result").text(lastSync);
			},
		});
		drawer.beforeClose = async () =>
			!w.dirty() ||
			(await new Promise((resolve) =>
				root.frappe.confirm(
					t("设置尚未保存，关闭会丢弃当前输入，继续？"),
					() => resolve(true),
					() => resolve(false)
				)
			));
		try {
			await w.load();
		} catch (error) {
			if (drawer.alive()) {
				drawer.panel
					.find(".dlp-payment-body")
					.html('<div class="dlp-error text-danger" role="alert"></div>');
				drawer.error(error);
			}
		}
		return drawer;
	}
	return {
		API,
		editSession,
		safeOriginalURL,
		dingTalkOriginalLink,
		canFinance,
		isManager,
		workflow,
		sourceHTML,
		previewHTML,
		controlDefinitions,
		canEditPayment,
		open,
		downloadAttachment,
		settingsWorkflow,
		openSettings,
	};
});
