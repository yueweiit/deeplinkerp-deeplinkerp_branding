(function (root, factory) {
	if (typeof module === "object" && module.exports) module.exports = factory;
	root.DeepLinkERPOperatingExpenses = factory(root);
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
	"use strict";
	const API = "deeplinkerp_branding.services.operating_expenses.",
		route = "operating-expenses";
	const t = (value, args = []) => String((root.__ || (text => text))(value)).replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");
	const esc = (value) =>
		String(value ?? "").replace(
			/[&<>"']/g,
			(char) =>
				({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char])
		);
	const columns = [
		["source_id", "钉钉申请单号", 210],
		["source_status", "付款状态", 170],
		["source_company", "应付款公司", 180],
		["applicant", "申请人", 110],
		["account_nature", "账户性质", 100],
		["summary", "摘要", 310],
		["amount", "应付金额", 140],
		["paid_amount", "已支付金额", 140],
		["pending_amount", "待付款金额", 140],
		["currency", "货币类型", 85],
		["project", "项目归属", 160],
		["payee_name", "收款人", 160],
		["needed_payment_date", "需求付款日期", 125],
		["general_manager_approval", "总经理审批", 135],
		["remark", "备注", 200],
		["actions", "操作", 180],
		["request_date", "申请日期", 110],
		["effective_application_type", "申请类型", 115],
		["company", "法律公司", 170],
		["approval_state", "审批状态", 100],
		["current_approver", "当前审批人", 145],
		["source_system", "来源", 140],
		["finance_status", "凭证状态", 135],
	].map(([fieldname, label, width]) => ({ fieldname, label, width }));
	const defaultColumns = columns.slice(0, 16).map(col => col.fieldname);
	const preferenceVersion = 3;
	const quickTabs = [["pending_work","待办理"],["all","全部申请"],["approvals_running","审批中"],["paid","已付清"],["reconciliation","待核对"]];
	const approvalLabels = {pending:"审批中",approved:"已通过",rejected:"已拒绝",terminated:"已终止",withdrawn:"已撤回",unknown:"待核对",eligible:"来源审批通过",blocked:"需复核"};
	const sortFields = ["request_date", "amount", "applicant", "source_id", "company", "modified"];
	const filterFields = [
		"keyword",
		"company",
		"application_type",
		"applicant",
		"source_status",
		"approval_state",
		"date_from",
		"date_to",
		"quick_tab",
	];
	const exportFields = new Set(columns.filter(col => col.fieldname !== "actions").map(col => col.fieldname));
	function migratePreferences(value) {
		return value?.version === preferenceVersion ? value : { ...value, columns: defaultColumns };
	}
	function filters(values = {}) {
		const result = {};
		for (const field of filterFields)
			if (values[field] !== undefined && values[field] !== null && values[field] !== "")
				result[field] = String(values[field]);
		for (const [field, allowed, label] of [
			["application_type", ["payment", "reimbursement", "unclassified"], "申请类型"],
			["source_status", ["未付款", "部分付款", "已付款", "付款待核对"], "来源付款状态"],
			["approval_state", Object.keys(approvalLabels), "审批状态"],
			["quick_tab", [...quickTabs.map(row=>row[0]), "pending_payment"], "快捷筛选"],
		]) {
			if (result[field] && !allowed.includes(result[field]))
				throw new Error(t("{0}无效", [label]));
		}
		return result;
	}
	function orderBy(value = "request_date desc") {
		const match = /^([a-z_]+) (asc|desc)$/.exec(value);
		if (!match || !sortFields.includes(match[1])) throw new Error(t("排序字段无效"));
		return value;
	}
	function request(c) {
		return {
			method: API + "get_operating_expenses",
			args: {
				filters: JSON.stringify(currentFilters(c)),
				order_by: orderBy(c.providerOrderBy),
				start: c.page * c.pageSize,
				page_length: c.pageSize,
			},
		};
	}
	function currentFilters(c) {
		return filters({ quick_tab: "pending_work", ...c.quick });
	}
	// Display rounds decimal text without changing the exact text used by financial controls.
	function money(value) {
		if (value == null || value === "" || !["string", "number"].includes(typeof value))
			return "—";
		const match = /^([+-]?)(\d+)(?:\.(\d+))?$/.exec(String(value));
		if (!match) return "—";
		const fraction = (match[3] || "").padEnd(3, "0");
		let cents = BigInt(match[2]) * 100n + BigInt(fraction.slice(0, 2));
		if (fraction[2] >= "5") cents++;
		return `${
			match[1] === "-" && cents ? "-" : ""
		}${String(cents / 100n).replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${String(cents % 100n).padStart(2, "0")}`;
	}
	const typeLabel = (value) =>
		t(
			{ payment: "付款申请", reimbursement: "费用报销", unclassified: "待分类" }[value] ||
				"待分类"
		);
	const approvalLabel = (value) => t(Object.prototype.hasOwnProperty.call(approvalLabels,value)?approvalLabels[value]:"需复核");
	function aggregateMoney(row, field) {
		const total = money(row[field]);
		const known = money(row.known_totals?.[field]);
		return total + (total === "—" && known !== "—"
			? ` <small class="text-muted">（${esc(t("已知"))} ${known}）</small>` : "");
	}
	function summary(c) {
		const totals = c.providerPayload?.currency_totals || {};
		return Object.entries(totals)
			.map(
				([currency, row]) =>
					`${esc(currency || t("币种未明确"))} · ${esc(t("申请金额"))} ${aggregateMoney(
						row, "amount"
					)} · ${esc(t("累计已付"))} ${aggregateMoney(row, "paid_amount")} · ${esc(
						t("剩余待付")
					)} ${aggregateMoney(row, "pending_amount")}${
						row.incomplete
							? ` <span class="text-warning">${esc(t("金额不完整，需复核"))}</span>`
							: ""
					}${row.anomaly_count > 0 ? ` <span class="text-danger">${esc(t("异常付款"))} ${esc(row.anomaly_count)} · ${esc(t("未计入正常余额"))}</span>` : ""
					}`
			)
			.join("<br>");
	}
	function renderValue(field, doc) {
		if (doc.projection_conflicts?.includes(field)) return `<span class="dlp-operating-ellipsis text-warning" title="${esc(t("来源字段冲突，待核对"))}">${esc(t("待核对"))}</span>`;
		if (field === "source_id") {
			const original = root.DeepLinkERPOperatingExpenseDrawer?.dingTalkOriginalLink(doc.original_url);
			return original ? `<a class="dlp-operating-ellipsis" href="${esc(original.desktop_url)}" title="${esc(t("需安装并登录钉钉，原单权限由钉钉校验。"))}">${esc(doc.approval_no || "—")}</a>` : `<span class="dlp-operating-ellipsis" title="${esc(doc.approval_no || "")}">${esc(doc.approval_no || "—")}</span>`;
		}
		if (["amount", "paid_amount", "pending_amount"].includes(field)) return money(doc[field]);
		if (field === "effective_application_type") return esc(typeLabel(doc[field]));
		if (field === "approval_state")
			return `<span class="${
				["eligible","approved"].includes(doc[field]) ? "is-approved" : ["pending"].includes(doc[field]) ? "is-pending" : "is-review"
			} dlp-operating-badge">${esc(approvalLabel(doc[field]))}</span>`;
		if (field === "source_status") {
			const blocker = String(doc.blocking_reason || "").split("；").map(t).join("；");
			return `<div class="dlp-operating-work-status"><div><span class="dlp-operating-badge ${doc[field]==="已付款"?"is-approved":doc[field]==="部分付款"?"is-pending":"is-review"}">${esc(t(doc[field]||"待核对"))}</span><small class="text-muted">${esc(approvalLabel(doc.approval_state))}</small></div>${blocker ? `<small class="dlp-operating-ellipsis text-warning" title="${esc(blocker)}">${esc(blocker)}</small>` : ""}</div>`;
		}
		if (field === "finance_status") return esc(t(doc[field] || "未确认"));
		if (field === "actions")
			return `<div class="dlp-operating-row-actions">${[["payments","付款明细"],["approvals","查看审批"]].map(([tab,label])=>`<button type="button" class="btn btn-link btn-xs dlp-operating-open" data-source="${esc(doc.source_id||doc.name)}" data-tab="${tab}">${esc(t(label))}</button>`).join("")}</div>`;
		return `<span class="dlp-operating-ellipsis" title="${esc(
			doc[field] ?? ""
		)}">${esc(doc[field] === "" || doc[field] == null ? "—" : doc[field])}</span>`;
	}
	async function exportCurrent(c) {
		const args = {
			filters: JSON.stringify(currentFilters(c)),
			order_by: orderBy(c.providerOrderBy),
			columns: JSON.stringify(c.preferences.columns.filter(field => exportFields.has(field)).map(field => field === "source_id" ? "display_source_id" : field)),
		};
		if (!root.DeepLinkERPPurchaseOrderExport?.downloadWorkbook)
			await root.frappe.require("/assets/deeplinkerp_branding/js/purchase_order_export.js");
		const exporter = root.DeepLinkERPPurchaseOrderExport;
		if (!exporter) throw new Error(t("导出组件加载失败，请刷新。"));
		exporter.downloadWorkbook(
			root,
			await exporter.fetchNativeWorkbook(root, args, API + "export_operating_expenses"),
			t("运营支出")
		);
	}
	function fit(c, active = true) {
		return root.DeepLinkERPCompactList.fitViewport(c, {
			active,
			root,
			scrollElement: c.list.$result.parent(".result-container")[0],
			layoutTailElement: c.list.$frappe_list[0],
			property: "--dlp-operating-result-max-height",
			headerSelector: ".dlp-po-grid-header",
			rowSelector: ".dlp-po-grid-row",
			observeTargets: [
				c.$filters?.[0]?.parentElement,
				c.$toolbar?.[0],
				c.$summary?.[0],
				c.$paging?.[0],
			],
		});
	}
	function mountControls(c) {
		c.quick.quick_tab ||= "pending_work";
		c.$operatingTabs=root.$(`<nav class="dlp-operating-list-tabs" aria-label="${esc(t("运营支出筛选"))}">${quickTabs.map(([value,label])=>`<button type="button" class="btn btn-default btn-sm" data-quick-tab="${value}">${esc(t(label))}</button>`).join("")}</nav>`).insertBefore(c.$toolbar);
		c.updateOperatingTabs=()=>{
			const selected=c.quick.quick_tab||"pending_work", counts=c.providerPayload?.tab_counts;
			for(const [value,label] of quickTabs) c.$operatingTabs.find(`[data-quick-tab="${value}"]`)
				.text(t(label)+(Number.isInteger(counts?.[value]) ? ` (${counts[value]})` : ""))
				.toggleClass("active",value===selected).attr("aria-pressed",String(value===selected));
		};
		c.$operatingTabs.on("click.dlpOperating","[data-quick-tab]",async event=>{c.quick.quick_tab=event.currentTarget.dataset.quickTab;c.setPage(0);c.updateOperatingTabs();await c.refresh();});
		c.updateOperatingTabs();
		root.$(`<button type="button" class="btn btn-default btn-sm">${esc(t("恢复推荐列"))}</button>`).appendTo(c.$toolbar).on("click.dlpOperating",()=>c.setColumns(defaultColumns));
		root.$(
			`<button type="button" class="btn btn-default btn-sm">${esc(t("清空筛选"))}</button>`
		)
			.appendTo(c.$toolbar)
			.on("click.dlpOperating", () => c.clearQuickFilters());
		if (root.DeepLinkERPOperatingExpenseDrawer.isManager())
			root.$(
				`<button type="button" class="btn btn-default btn-sm">${esc(
					t("同步设置")
				)}</button>`
			)
				.appendTo(c.$toolbar)
				.on("click.dlpOperating", () =>
					root.DeepLinkERPOperatingExpenseDrawer.openSettings(() => c.refresh())
				);
		c.list.$result
			.off(".dlpOperating")
			.on("click.dlpOperating", ".dlp-operating-open", (event) =>
				root.DeepLinkERPOperatingExpenseDrawer.open(
					event.currentTarget.dataset.source,
					() => c.refresh(),
					event.currentTarget.dataset.tab
				)
			)
			.on("click.dlpOperating", "[data-provider-sort]", (event) => {
				const field = event.currentTarget.dataset.providerSort;
				if (!sortFields.includes(field)) return;
				const [previous, direction] = c.providerOrderBy.split(" ");
				c.providerOrderBy = `${field} ${
					previous === field && direction === "asc" ? "desc" : "asc"
				}`;
				c.setPage(0);
				c.refresh();
			});
		c.$operatingNotice = root
			.$('<p class="text-muted dlp-operating-notice"></p>')
			.text(t("仅登记已发生的付款，不向银行转账；凭证另行保存草稿。") + " " + t("本页不提供批量付款"))
			.insertAfter(c.$filters);
	}
	function grid() {
		const pageFieldMap = Object.fromEntries(
			columns.map((col) => [col.fieldname, col.fieldname])
		);
		Object.assign(pageFieldMap, {
			finance_status: "issues",
			actions: "source_id",
			keyword: "source_id",
			date_from: "request_date",
			date_to: "request_date",
			application_type: "effective_application_type",
			current_approver: "approval_state",
			project: "issues",
			account_nature: "issues",
			needed_payment_date: "issues",
			general_manager_approval: "issues",
			remark: "issues",
		});
		const controls = [
			["keyword", "Data", null, "申请编号 / 摘要 / 收款人"],
			["company", "Link", "Company", "法律公司"],
			["application_type", "Select", "\npayment\nreimbursement\nunclassified", "申请类型"],
			["applicant", "Data", null, "申请人"],
			["source_status", "Select", "\n未付款\n部分付款\n已付款\n付款待核对", "来源付款状态"],
			["approval_state", "Select", "\npending\napproved\nrejected\nterminated\nwithdrawn\nunknown\neligible\nblocked", "审批状态"],
			["date_from", "Date", null, "申请开始日期"],
			["date_to", "Date", null, "申请结束日期"],
		].map(([fieldname, fieldtype, options, label]) => ({
			fieldname,
			fieldtype,
			options,
			label,
			permission_field: pageFieldMap[fieldname] || fieldname,
		}));
		return root.DeepLinkERPCompactList.create({
			doctype: "Operating Expense Source",
			pageRoute: route,
			routeClass: "dlp-operating-expense-grid-active",
			columns,
			defaultColumns,
			preferenceVersion,
			migratePreferences,
			backupMigratedPreferences: true,
			hideSequence: true,
			freezeSelection: false,
			disabledSelectionLabel: "本页不提供批量付款",
			defaultSort: "request_date desc",
			pageFieldMap,
			controls,
			numbers: ["amount", "paid_amount", "pending_amount"],
			dates: ["request_date", "needed_payment_date"],
			dismissInitialOnboarding: true,
			optionLabels: {
				application_type: {
					payment: "付款申请",
					reimbursement: "费用报销",
					unclassified: "待分类",
				},
				approval_state: approvalLabels,
			},
			provider: {
				columns,
				defaultColumns,
				freezeFields: ["source_id"],
				sortFields,
				request,
				renderValue,
				summary,
				exportCurrent,
				mountControls,
				onPayload: (c) => {c.updateOperatingTabs?.();fit(c);},
			},
			afterRender: (c) => fit(c),
			emptyLabel: t("没有符合条件的运营支出来源"),
		});
	}
	const api = {
		API,
		route,
		columns,
		defaultColumns,
		quickTabs,
		sortFields,
		filters,
		orderBy,
		request,
		money,
		typeLabel,
		approvalLabel,
		summary,
		renderValue,
		exportCurrent,
		esc,
		grid,
		fit,
		controller: null,
	};
	api.onPageLoad = (wrapper) => {
		const page = root.frappe.ui.make_app_page({
			parent: wrapper,
			title: t("运营支出"),
			single_column: true,
		});
		const ready = root.frappe.model.with_doctype("Operating Expense Source").then(() => {
			const c = grid().mountPage(page, root);
			api.controller = c;
			wrapper.dlpOperatingController = c;
			root.frappe.router?.on("change", () =>
				fit(c, (root.frappe.get_route?.() || [])[0] === route)
			);
			return c;
		});
		wrapper.dlpLoad = async () => {
			const c = await ready;
			c.activate();
			fit(c, (root.frappe.get_route?.() || [])[0] === route);
			return c.refresh();
		};
		ready.catch(() =>
			page.main.append(
				`<p class="text-danger">${esc(t("来源元数据读取失败，请刷新或核对权限。"))}</p>`
			)
		);
		page.set_secondary_action(t("刷新"), () => wrapper.dlpLoad());
	};
	api.onPageShow = (wrapper) => wrapper.dlpLoad?.();
	return api;
});
