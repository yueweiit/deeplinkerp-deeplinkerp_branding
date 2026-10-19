(function (root, factory) {
	const api = factory(root, root.DeepLinkERPCompactList || (typeof require === "function" ? require("./compact_list.js") : null));
	if (typeof module === "object" && module.exports) module.exports = api;
	root.DeepLinkERPSalesWorkspace = api;
	if (root.frappe?.listview_settings) api.install(root);
})(typeof globalThis !== "undefined" ? globalThis : this, function (root, engine) {
	"use strict";
	const API = "deeplinkerp_branding.services.compact_sales_service.";
	const financialGroups = { pending: ["Pending Deposit Confirmation", "Deposit Confirmation Processing"], approved: ["Pending Production", "Pending Final Payment", "Deliverable", "Partially Delivered", "Completed"], rejected: ["Rejected"] };
	const financialLabels = { pending: "待财务审核", approved: "已放行生产", rejected: "已驳回" };
	const financialLabel = value => financialLabels[Object.keys(financialGroups).find(group => financialGroups[group].includes(value))] || "—";
	const statusLabels = { "Pending Confirmation": "待确认", "Pending Deposit Confirmation": "待财务放行", "Deposit Confirmation Processing": "CRM/MES 同步中", "Pending Production": "生产已放行", "Pending Final Payment": "待尾款确认", "Deliverable": "可发货", "Partially Delivered": "部分发货", "Completed": "已完成", "Rejected": "已驳回", "Cancelled": "已取消", "Closed": "已关闭" };
	const orderStatusLabels = { Draft: "草稿", "To Deliver and Bill": "待交付及开票", "To Bill": "待开票", "To Deliver": "待交付", Completed: "已完成", Cancelled: "已取消", Closed: "已关闭", "On Hold": "已暂停" };
	const COLUMNS = [
		["name", "CRM / ERP 订单号", 190], ["customer_name", "客户", 165], ["custom_process_status", "财务审核状态", 145], ["dlp_product", "产品", 170], ["dlp_quantity", "数量", 105], ["grand_total", "ERP 订单金额", 145], ["delivery_date", "预计交付日期", 110], ["transaction_date", "订单日期", 105],
		["title", "ERP 标题", 170], ["contact_display", "联系人", 160], ["contact_email", "联系人邮箱", 200], ["contact_mobile", "联系电话", 140], ["customer", "ERP 客户编码", 150], ["dlp_sales_person", "业务负责人", 155], ["custom_odt", "ODT", 130], ["custom_remark", "销售备注", 230], ["company", "公司", 180], ["currency", "币种", 70], ["status", "交付状态", 145], ["advance_paid", "ERP 预付款汇总", 150], ["owner", "ERP 创建人", 190], ["creation", "创建时间", 165], ["modified_by", "ERP 更新人", 190], ["modified", "更新时间", 165], ["per_delivered", "已交付", 85], ["per_billed", "已开票", 85], ["project", "项目", 130],
		["dlp_receipts", "关联收款记录", 290], ["dlp_sync", "财务确认 / 同步", 200], ["dlp_last_confirmation", "最近确认人 / 时间", 235], ["dlp_actions", "操作", 110],
	].map(([fieldname, label, width]) => ({ fieldname, label, width }));
	const presets = {
		main: ["name", "customer_name", "advance_paid", "custom_process_status", "dlp_product", "dlp_quantity", "grand_total", "delivery_date", "dlp_actions"],
		middle: ["name", "title", "contact_display", "dlp_sales_person", "customer", "transaction_date", "delivery_date", "custom_remark", "dlp_actions"],
		tail: ["name", "custom_odt", "status", "per_delivered", "owner", "creation", "modified_by", "modified", "dlp_actions"],
		finance: ["name", "customer_name", "advance_paid", "custom_process_status", "grand_total", "dlp_receipts", "dlp_last_confirmation", "dlp_actions"],
	};
	const itemColumns = [["idx", "序号"], ["item_code", "品目编码"], ["item_name", "品项名"], ["custom_specifications", "颜色"], ["qty", "数量"], ["rate", "单价"], ["amount", "金额"], ["custom_item_tax_amount", "IVA 增值税"], ["delivery_date", "交付日期"], ["custom_product", "产品系列"], ["warehouse", "仓库"]];
	const primaryItemFields = new Set(["idx", "item_code", "item_name", "custom_specifications", "qty", "rate", "amount"]);
	const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
	const t = (value, args = []) => String((root.__ || (text => text))(value)).replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");
	const state = value => t(statusLabels[value] || value || "—");
	const money = (value, currency) => value === undefined || value === null ? "—" : `${Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${esc(currency || "")}`;
	const prepayment = header => header.party_account_currency && header.advance_paid !== "" && Number.isFinite(Number(header.advance_paid)) ? money(header.advance_paid, header.party_account_currency) : "—";
	const call = async (method, args) => (await root.frappe.call({ method: API + method, args })).message;
	function itemTable(detail, full = true) {
		const permitted = new Set(detail.item_fields || []), currency = detail.header.currency;
		const columns = itemColumns.filter(([field]) => permitted.has(field));
		if (!columns.length) return `<p>${esc(t("无权查看产品明细"))}</p>`;
		return engine.detailTable({columns,items:detail.items,escape:esc,translate:t,wrapperClass:"dlp-sales-items-scroll",tableClass:`dlp-sales-items ${full ? "" : "dlp-sales-items-overview"}`,textFields:["item_name","custom_specifications","warehouse"],textClass:"dlp-sales-item-text",columnClass:field=>[primaryItemFields.has(field)?"":"dlp-sales-extra-item",["idx","qty","rate","custom_item_tax_amount","amount"].includes(field)?"dlp-sales-item-number":""].filter(Boolean).join(" "),format:(field,item)=>["rate","custom_item_tax_amount","amount"].includes(field)?money(item[field],currency):field==="qty"?esc(item[field] ?? "—")+(permitted.has("uom")?` ${esc(item.uom)}`:""):undefined});
	}
	function detailsHTML(detail) {
		const header = detail.header;
		const fields = [["custom_crm_order_no", "CRM 订单号"], ["name", "ERP 订单号"], ["customer_name", "客户"], ["customer", "ERP 客户编码"], ["title", "ERP 标题"], ["contact_display", "联系人"], ["contact_email", "联系人邮箱"], ["contact_mobile", "联系电话"], ["transaction_date", "订单日期"], ["delivery_date", "预计交付日期"], ["company", "公司"], ["custom_process_status", "ERP 业务状态"], ["grand_total", "ERP 订单金额"], ["advance_paid", "ERP 预付款汇总"], ["custom_odt", "ODT"], ["custom_remark", "销售备注"], ["owner", "ERP 创建人"], ["creation", "创建时间"], ["modified_by", "ERP 更新人"], ["modified", "更新时间"]].filter(([field]) => Object.hasOwn(header, field));
		const addIcon = root.frappe?.utils?.icon?.("add", "sm") || "";
		const paymentButton = detail.can_create_payment_entry ? `<button type="button" class="btn btn-primary btn-sm dlp-sales-payment-new">${addIcon}${esc(t("新增收款"))}</button>` : "";
		return `<div class="dlp-sales-detail-layout"><section><h4>${esc(t("订单基本信息"))}</h4><dl>${fields.map(([field, label]) => `<dt>${esc(t(label))}</dt><dd>${field === "custom_process_status" ? esc(state(header[field])) : field === "advance_paid" ? prepayment(header) : field === "grand_total" ? money(header[field], header.currency) : esc(header[field] || "—")}</dd>`).join("")}</dl>${Object.hasOwn(header, "advance_paid") ? `<p class="text-muted">${esc(t("预付款为 ERP 订单汇总；草稿收款不代表已到账，未录入的收款请向财务核对。"))}</p>` : ""}<p class="text-muted">${esc(t("显示 ERP 已同步字段；负责人来自销售团队，创建人是 ERP 账户。"))}</p><a class="btn btn-default btn-sm" href="/desk/sales-order/${encodeURIComponent(header.name)}">${esc(t(detail.can_edit ? "打开原生订单编辑" : "打开原生订单"))}</a></section><section><div class="dlp-sales-detail-heading"><h4>${esc(t("订单产品明细"))}</h4><button class="btn btn-default btn-sm dlp-sales-all-items" aria-expanded="false">${esc(t("显示全部明细字段"))}</button></div>${itemTable(detail, false)}<div class="dlp-sales-receipts"><div class="dlp-sales-detail-heading"><h4>${esc(t("收款与预付"))}</h4>${paymentButton}</div><div class="dlp-sales-receipts-body" role="status">${esc(t("正在读取关联收款记录…"))}</div>${header.grand_total === 0 ? `<p class="text-warning">${esc(t("订单金额为 0，请先核实销售价格后再分配预付款。"))}</p>` : ""}</div><h4>${esc(t("订单附件"))}</h4>${(detail.attachments || []).filter(file => /^\/(private\/)?files\//.test(file.file_url)).map(file => `<p><a target="_blank" rel="noopener" href="${esc(file.file_url)}">${esc(file.file_name)}</a></p>`).join("") || `<p class="text-muted">${esc(t("没有可见附件"))}</p>`}<p class="text-muted">${esc(t("预计交付日期沿用 ERP 交期；CRM 未同步的源字段不作推断。"))}</p></section></div>`;
	}
	function receiptsHTML(review) {
		if (!Array.isArray(review?.receipts)) return esc(t("收款记录不可用，请打开原生订单核对。"));
		const records = review.receipts.map(payment => `<div class="dlp-sales-receipt"><a href="/desk/payment-entry/${esc(encodeURIComponent(payment.name))}">${esc(payment.name)}</a><span>${esc(t(payment.docstatus === 1 ? "已提交" : payment.docstatus === 2 ? "已取消" : "草稿"))} · ${esc(payment.posting_date || "")}</span><strong>${money(payment.allocated_amount, payment.currency)}</strong></div>`).join("");
		const warning = review.association_error ? "收款与订单关联异常，请财务核对。" : review.receipts_complete === false ? "部分收款记录不可见，请财务核对。" : "";
		return (records || `<p class="text-muted">${esc(t("暂无关联收款记录"))}</p>`) + (warning ? `<p class="text-warning">${esc(t(warning))}</p>` : "");
	}
	async function loadDetailReceipts(dialog, name) {
		const body = dialog.$wrapper.find(".dlp-sales-receipts-body");
		try {
			const response = await root.frappe.call({ method: "crm_integration.crm_integration.finance_release.get_finance_release_review", args: { sales_orders: [name] } });
			body.html(receiptsHTML(response.message?.orders?.[0]));
		} catch (_) {
			body.text(t("收款记录读取失败，请打开原生订单核对。"));
		}
	}
	async function openSalesOrderPaymentEntry(dialog, name, button) {
		button.prop("disabled", true);
		try {
			const response = await root.frappe.call({ method: API + "make_sales_order_payment_entry", args: { sales_order: name } });
			const [payment] = root.frappe.model.sync(response.message);
			if (!payment) throw new Error(t("Missing mapped payment entry"));
			dialog.hide();
			root.frappe.set_route("Form", payment.doctype, payment.name);
		} catch (_) {
			dialog.$wrapper.find(".dlp-sales-receipts-body").text(t("无法新建收款，请检查订单状态和收款权限。"));
			button.prop("disabled", false);
		}
	}
	function renderValue(field, doc, formatters) {
		const tr = formatters.translate || t;
		if (field === "advance_paid") return prepayment(doc);
		if (field === "name") return `${esc(doc.custom_crm_order_no || doc.name)}${doc.custom_crm_order_no ? `<small class="dlp-sales-erp-number">ERP: ${esc(doc.name)}</small>` : ""}`;
		if (field === "custom_process_status") return `<span class="dlp-sales-state">${esc(tr(financialLabel(doc[field])))}</span>`;
		if (field === "status") return `<span class="dlp-sales-state">${esc(tr(orderStatusLabels[doc[field]] || doc[field] || "—"))}</span>`;
		if (field === "dlp_product") return esc(doc.dlp_product || "—") + (doc.dlp_item_count > 1 ? `<small> · ${doc.dlp_item_count} ${esc(tr("项"))}</small>` : "");
		if (field === "dlp_quantity") return doc.dlp_quantity == null ? "—" : esc(doc.dlp_quantity);
		if (field === "dlp_actions") return `<button type="button" class="btn btn-default btn-xs dlp-sales-detail" data-name="${esc(doc.name)}">${esc(tr("查看明细"))}</button>`;
		if (field === "dlp_receipts") {
			const finance = doc.dlp_finance;
			if (!finance) return esc(tr("正在核验收款记录…"));
			if (finance.receipts_read_failed) return esc(tr("收款记录读取失败，请刷新重试。"));
			if (!Array.isArray(finance.receipts)) return esc(tr("收款记录暂不可用，请打开原生订单核对。"));
			const records = finance.receipts.map(payment => `${esc(payment.name)} · ${esc(tr(payment.docstatus === 1 ? "已提交" : payment.docstatus === 2 ? "已取消" : "草稿"))}<br>${money(payment.allocated_amount, payment.currency)}`).join("<br>");
			const warning = finance.association_error ? "收款与订单关联异常，请联系财务核对。" : finance.receipts_complete === false ? "部分收款记录受权限限制，请联系财务核对。" : "";
			return records + (warning ? `${records ? "<br>" : ""}<small class="text-warning">${esc(tr(warning))}</small>` : "") || esc(tr("未找到可见的关联收款记录"));
		}
		if (field === "dlp_sync") return doc.dlp_finance ? esc(tr(syncWarning(doc) || "—")) : esc(tr("正在核验订单…"));
		if (field === "dlp_last_confirmation") { const audit = doc.dlp_finance?.last_confirmation, warning = syncWarning(doc); return `${audit ? `${esc(audit.user)}<br>${esc(audit.creation)}` : "—"}${warning ? `<br><small class="text-warning">${esc(tr(warning))}</small>` : ""}`; }
	}
	function syncWarning(doc) {
		if ((doc.dlp_finance?.process_status || doc.custom_process_status) === "Deposit Confirmation Processing") return "CRM/MES 同步中";
		return doc.dlp_finance?.last_confirmation?.status === "Failed" ? "CRM/MES 同步失败" : "";
	}
	function transformQuery(args, controller) {
		if (controller.salesView === "finance" && ["approved", "rejected"].includes(controller.quick.financial_status) && controller.allowed.has("custom_process_status")) switchView(controller, "all");
		if (controller.salesView === "mine" && controller.allowed.has("owner")) args.filters.push(["Sales Order", "owner", "=", root.frappe.session.user]);
		if (controller.salesView === "finance") {
			if (!controller.allowed.has("custom_process_status")) { args.filters.push(["Sales Order", "name", "in", []]); controller.$salesNotice?.text(t("无权查看财务审核状态，请联系管理员。")); }
			else args.filters.push(["Sales Order", "custom_process_status", "in", financialGroups.pending], ["Sales Order", "docstatus", "=", 1], ["Sales Order", "status", "not in", ["Closed", "Cancelled", "Stopped", "On Hold"]]);
		}
		if (controller.quick.financial_status && financialGroups[controller.quick.financial_status]) {
			args.filters.push(controller.allowed.has("custom_process_status") ? ["Sales Order", "custom_process_status", "in", financialGroups[controller.quick.financial_status]] : ["Sales Order", "name", "in", []]);
		}
		if (controller.quick.product && controller.allowed.has("items")) args.filters.push(["Sales Order Item", "item_code", "=", controller.quick.product]);
		return args;
	}
	function switchView(controller, view) {
		controller.salesView = view;
		controller.$salesViewbar?.find("[data-sales-view]").attr("aria-pressed", "false");
		controller.$salesViewbar?.find(`[data-sales-view="${view}"]`).attr("aria-pressed", "true");
		controller.list.clear_checked_items?.(); controller.salesExpanded?.clear(); invalidateReleaseSelection(controller);
		controller.setPage?.(0);
		controller.$salesNotice?.text(t(view === "finance" ? "收款记录未录入不代表未收款；定金由财务按线下约定判断。" : "展开产品行或查看明细；左右滚动查看全部字段。"));
	}
	function invalidateExpandedDetails(controller) {
		engine.invalidateExpandedDetails(controller.salesExpanded,controller.list.data);
	}
	async function onRows(controller) {
		if (!salesListActive(controller)) return;
		invalidateExpandedDetails(controller);
		const names = controller.list.data.map(row => row.name), generation = controller.requestId;
		try {
			const details = await call("get_sales_display_details", { sales_orders: names });
			if (generation !== controller.requestId || !salesListActive(controller)) return;
			controller.list.data.forEach(row => Object.assign(row, details[row.name] || {}));
			if (controller.salesView === "finance" || controller.preferences?.columns.some(field => ["dlp_receipts", "dlp_sync", "dlp_last_confirmation"].includes(field))) {
				const finance = [];
				for (let start = 0; start < names.length; start += 100) { const response = await root.frappe.call({ method: "crm_integration.crm_integration.finance_release.get_finance_release_review", args: { sales_orders: names.slice(start, start + 100) } }); if (generation !== controller.requestId || !salesListActive(controller)) return; finance.push(...response.message.orders); }
				controller.list.data.forEach(row => { row.dlp_finance = finance.find(item => item.name === row.name); });
			}
			controller.list.render_list(); controller.list.set_rows_as_checked?.(); controller.list.on_row_checked?.();
		} catch (_) {
			if (generation !== controller.requestId || !salesListActive(controller)) return;
			controller.list.data.forEach(row => { row.dlp_finance = { receipts_read_failed: true }; });
			controller.list.render_list?.(); controller.list.set_rows_as_checked?.();
			controller.$salesNotice?.text(t("补充信息读取失败；请刷新重试或打开原生订单核对。"));
		}
	}
	function migratePreferences(value, allowed) {
		if (!Array.isArray(value?.columns) || value.version >= 4) return value;
		const columns = value.version === 3 ? [...value.columns] : value.columns.filter(field => field !== "status");
		if (value.version !== 3 && allowed.has("custom_process_status") && !columns.includes("custom_process_status")) columns.splice(Math.max(columns.indexOf("customer_name") + 1, 1), 0, "custom_process_status");
		if (allowed.has("advance_paid") && allowed.has("party_account_currency") && !columns.includes("advance_paid")) {
			const anchor = columns.includes("customer_name") ? "customer_name" : "name";
			columns.splice(Math.max(columns.indexOf(anchor) + 1, 1), 0, "advance_paid");
		}
		return { ...value, columns };
	}
	function onColumnsChange(controller) {
		if (!salesListActive(controller) || !controller.preferences.columns.some(field => ["dlp_receipts", "dlp_sync", "dlp_last_confirmation"].includes(field)) || !controller.list.data.some(row => !row.dlp_finance)) return;
		const key = JSON.stringify([controller.requestId, controller.list.data.map(row => [row.name, row.modified])]);
		if (controller.salesColumnRead?.key === key) return controller.salesColumnRead.promise;
		const read = { key, promise: null };
		read.promise = onRows(controller).finally(() => { if (controller.salesColumnRead === read) controller.salesColumnRead = null; });
		controller.salesColumnRead = read;
		return read.promise;
	}
	function salesListActive(controller) {
		const route = controller.root.frappe.get_route?.() || [];
		return controller.salesRouteActive !== false && route[0] === "List" && route[1] === "Sales Order" && (!route[2] || route[2] === "List");
	}
	function releaseSelectionKey(controller) {
		return JSON.stringify([controller.requestId, (controller.list.get_checked_items?.() || []).map(row => typeof row === "string" ? row : [row.name, row.modified, row.status, row.custom_process_status])]);
	}
	function invalidateReleaseSelection(controller, label = t("正在刷新订单，请稍候…")) {
		controller.salesReleaseKey = null;
		controller.salesReleaseVersion = (controller.salesReleaseVersion || 0) + 1;
		controller.salesReleaseNames = [];
		controller.$salesViewbar?.find(".dlp-sales-selected").text(label);
		controller.$salesViewbar?.find(".dlp-sales-release").text(t("允许生产（{0}单）", [0])).prop("disabled", true);
	}
	function releaseSelectionCurrent(controller, version, key = controller.salesReleaseKey) {
		return salesListActive(controller) && controller.salesReleaseVersion === version && key === controller.salesReleaseKey && key === releaseSelectionKey(controller);
	}
	function updateReleaseSelection(controller, force = false) {
		if (!controller?.$salesViewbar) return Promise.resolve();
		if (controller.allowed && !controller.allowed.has("custom_process_status")) { invalidateReleaseSelection(controller, t("无权查看财务审核状态，请联系管理员。")); return Promise.resolve(); }
		if (!salesListActive(controller)) { invalidateReleaseSelection(controller, t("未选择订单")); return Promise.resolve(); }
		const selected = controller.list.get_checked_items?.() || [];
		const names = [...new Set(selected.map(row => typeof row === "string" ? row : row.name).filter(Boolean))];
		const key = releaseSelectionKey(controller);
		if (!force && controller.salesReleaseKey === key) return controller.salesReleasePromise || Promise.resolve();
		controller.salesReleaseKey = key;
		const version = controller.salesReleaseVersion = (controller.salesReleaseVersion || 0) + 1;
		controller.salesReleaseNames = [];
		const bar = controller.$salesViewbar;
		const paint = (label, eligible = []) => {
			controller.salesReleaseNames = eligible;
			bar.find(".dlp-sales-selected").text(label);
			bar.find(".dlp-sales-release").text(t("允许生产（{0}单）", [eligible.length])).prop("disabled", !eligible.length);
		};
		paint(!names.length ? t("未选择订单") : names.length > 100 ? t("已选择：{0}；每次最多核验 100 单", [names.length]) : t("已选择：{0}；正在核验…", [names.length]));
		if (!names.length || names.length > 100) return Promise.resolve();
		const current = () => releaseSelectionCurrent(controller, version, key);
		const promise = (async () => {
			try {
				const response = await controller.root.frappe.call({ method: "crm_integration.crm_integration.finance_release.get_finance_release_review", args: { sales_orders: names } });
				if (!current()) return;
				const permitted = new Set((response.message?.orders || []).filter(row => row.can_release === true).map(row => row.name));
				const eligible = names.filter(name => permitted.has(name));
				paint(t("已选择：{0} · 可放行：{1} · 不可放行：{2}", [names.length, eligible.length, names.length - eligible.length]), eligible);
			} catch (_) {
				if (current()) paint(t("放行资格核验失败，请重新选择或刷新后重试。"));
			}
		})();
		controller.salesReleasePromise = promise;
		return promise;
	}
	function mountControls(controller) {
		controller.salesView = "finance"; controller.salesExpanded = new Map();
		if (!root.$ || !controller.$toolbar) return;
		dismissAutomaticOnboarding(controller);
		const bar = root.$(`<div class="dlp-sales-viewbar"><div class="dlp-sales-tabs">${[["all", "全部订单"], ["mine", "我的订单"], ["finance", "待财务审核"]].map(([value, label]) => `<button class="btn btn-default btn-sm" type="button" data-sales-view="${value}" aria-pressed="${value === "finance"}">${esc(t(label))}</button>`).join("")}</div><div class="dlp-sales-release-action"><span class="dlp-sales-selected" role="status">${esc(t("未选择订单"))}</span><button type="button" class="btn btn-primary dlp-sales-release" disabled>${esc(t("允许生产（{0}单）", [0]))}</button></div></div>`).prependTo(controller.$toolbar);
		controller.$salesViewbar = bar;
		controller.$salesNotice = root.$(`<p class="dlp-sales-notice text-muted">${esc(t("收款记录未录入不代表未收款；定金由财务按线下约定判断。"))}</p>`).insertAfter(controller.$toolbar);
		const updateSelection = () => updateReleaseSelection(controller);
		controller.updateSalesSelection = updateSelection;
		bar.find(".dlp-sales-release").on("click", async () => {
			const pending = updateReleaseSelection(controller, true), version = controller.salesReleaseVersion;
			await pending;
			if (!releaseSelectionCurrent(controller, version)) return;
			const names = [...controller.salesReleaseNames];
			if (!names.length) return;
			if (!root.CRMFinanceRelease) await root.frappe.require("/assets/crm_integration/js/finance_release.js");
			if (!releaseSelectionCurrent(controller, version)) return;
			root.CRMFinanceRelease.open(names, () => { controller.list.clear_checked_items?.(); updateSelection(); controller.refresh(); });
		});
		bar.find("[data-sales-view]").on("click", async event => {
			switchView(controller, event.currentTarget.dataset.salesView);
			if (controller.salesView === "finance") { controller.resetting = true; controller.quick.financial_status = ""; await controller.controls.financial_status?.set_value(""); controller.resetting = false; }
			controller.refresh();
			controller.$salesNotice.text(t(controller.salesView === "finance" ? "收款记录未录入不代表未收款；定金由财务按线下约定判断。" : "展开产品行或查看明细；左右滚动查看全部字段。"));
		});
		const select = root.$(`<select class="form-control input-xs dlp-sales-preset" aria-label="${esc(t("字段分组"))}">${[["main", "主要字段"], ["middle", "订单资料"], ["tail", "来源与进度"], ["finance", "收款与预付"]].map(([value, label]) => `<option value="${value}">${esc(t(label))}</option>`).join("")}</select>`).appendTo(controller.$toolbar);
		select.on("change", event => controller.setColumns(presets[event.target.value]));
		controller.list.$result?.on("click.dlpSalesDetails", ".dlp-sales-detail,.dlp-sales-expand", async event => {
			event.preventDefault(); event.stopPropagation(); const button = root.$(event.currentTarget), name = button.attr("data-name");
			if (button.hasClass("dlp-sales-expand") && controller.salesExpanded.has(name)) { controller.salesExpanded.delete(name); controller.list.render_list(); controller.list.set_rows_as_checked?.(); return; }
			button.prop("disabled", true);
			try { const detail = await call("get_sales_order_details", { sales_order: name }); if (button.hasClass("dlp-sales-expand")) { controller.salesExpanded.set(name, detail); controller.list.render_list(); controller.list.set_rows_as_checked?.(); } else {
				const dialog = new root.frappe.ui.Dialog({ title: t("{0} · {1}", [t("订单详情"), name]), size: "extra-large", fields: [{ fieldname: "details", fieldtype: "HTML", options: detailsHTML(detail) }] }); dialog.$wrapper.addClass("dlp-sales-detail-dialog"); dialog.show(); dialog.$wrapper.find(".dlp-sales-all-items").on("click", e => { const expanded = e.currentTarget.getAttribute("aria-expanded") !== "true"; e.currentTarget.setAttribute("aria-expanded", String(expanded)); e.currentTarget.textContent = t(expanded ? "收起额外字段" : "显示全部明细字段"); dialog.$wrapper.find(".dlp-sales-items").toggleClass("dlp-sales-items-overview", !expanded); });
				loadDetailReceipts(dialog, name);
				dialog.$wrapper.find(".dlp-sales-payment-new").on("click", event => openSalesOrderPaymentEntry(dialog, name, root.$(event.currentTarget)));
			} } finally { button.prop("disabled", false); }
		});
		mountScrollTop(controller, true);
		mountHorizontalScroll(controller, true);
	}
	function mountHorizontalScroll(controller, active) {
		if (!controller) return;
		if (!active) return controller.stopSalesHorizontalScroll?.();
		if (controller.updateSalesHorizontalScroll) return controller.updateSalesHorizontalScroll();
		const host = controller.root, viewport = controller.list.$result?.parent?.(".result-container")?.[0];
		if (!viewport?.getBoundingClientRect || !viewport.querySelectorAll || !host.document?.createElement || !host.addEventListener) return;
		const scroller = controller.list.page.wrapper?.[0]?.closest?.(".main-section");
		let bar = controller.salesHorizontalScroll;
		if (!bar) {
			bar = controller.salesHorizontalScroll = host.document.createElement("div");
			bar.className = "dlp-sales-horizontal-scroll"; bar.hidden = true;
			bar.setAttribute("tabindex", "0"); bar.setAttribute("role", "region"); bar.setAttribute("aria-label", t("横向滚动订单表格"));
			const spacer = host.document.createElement("div"); spacer.setAttribute("aria-hidden", "true"); bar.appendChild(spacer);
			host.document.body.appendChild(bar);
		}
		let target, frame, observed = [];
		const fromTarget = () => { if (bar.scrollLeft !== target.scrollLeft) bar.scrollLeft = target.scrollLeft; };
		const fromBar = () => { if (target && target.scrollLeft !== bar.scrollLeft) target.scrollLeft = bar.scrollLeft; };
		const schedule = () => { if (frame === undefined) frame = host.requestAnimationFrame ? host.requestAnimationFrame(measure) : (measure(), undefined); };
		const observer = host.ResizeObserver ? new host.ResizeObserver(schedule) : null;
		function measure() {
			frame = undefined;
			const rect = viewport.getBoundingClientRect(), pageRect = scroller?.getBoundingClientRect();
			const left = Math.max(0, rect.left + (viewport.clientLeft || 0)), right = Math.min(host.innerWidth, left + viewport.clientWidth);
			const top = Math.max(0, rect.top, pageRect?.top || 0), bottom = Math.min(host.innerHeight, rect.bottom, pageRect?.bottom ?? host.innerHeight);
			const details = [...viewport.querySelectorAll(".dlp-sales-items-scroll")];
			let selected = viewport, greatestArea = 0;
			for (const detail of details) {
				const box = detail.getBoundingClientRect();
				const area = Math.max(0, Math.min(right, box.right) - Math.max(left, box.left)) * Math.max(0, Math.min(bottom, box.bottom) - Math.max(top, box.top));
				if (area > greatestArea) { selected = detail; greatestArea = area; }
			}
			if (target !== selected) { target?.removeEventListener("scroll", fromTarget); target = selected; target.addEventListener("scroll", fromTarget, { passive: true }); }
			const width = Math.max(0, right - left), overflow = Math.max(0, target.scrollWidth - target.clientWidth);
			bar.style.left = `${left}px`; bar.style.width = `${width}px`; bar.firstElementChild.style.width = `${width + overflow}px`;
			bar.hidden = !salesListActive(controller) || host.document.body.classList?.contains("modal-open") || width <= 0 || overflow <= 0;
			bar.scrollLeft = target.scrollLeft;
			const nodes = [...new Set([viewport, scroller, viewport.firstElementChild, ...details, ...details.map(detail => detail.firstElementChild)].filter(Boolean))];
			if (observer && (nodes.length !== observed.length || nodes.some((node, index) => node !== observed[index]))) {
				observer.disconnect(); nodes.forEach(node => observer.observe(node)); observed = nodes;
			}
		}
		const modalObserver = host.MutationObserver ? new host.MutationObserver(schedule) : null;
		modalObserver?.observe(host.document.body, { attributes: true, attributeFilter: ["class"] });
		bar.addEventListener("scroll", fromBar, { passive: true });
		host.addEventListener("scroll", schedule, { passive: true, capture: true }); host.addEventListener("resize", schedule);
		scroller?.addEventListener("scroll", schedule, { passive: true });
		controller.updateSalesHorizontalScroll = schedule;
		controller.stopSalesHorizontalScroll = () => {
			bar.hidden = true; target?.removeEventListener("scroll", fromTarget); bar.removeEventListener("scroll", fromBar);
			host.removeEventListener("scroll", schedule, true); host.removeEventListener("resize", schedule); scroller?.removeEventListener("scroll", schedule);
			observer?.disconnect(); modalObserver?.disconnect(); if (frame !== undefined) host.cancelAnimationFrame?.(frame);
			controller.updateSalesHorizontalScroll = null; controller.stopSalesHorizontalScroll = null;
		};
		schedule();
	}
	function mountScrollTop(controller, active) {
		if (!controller) return;
		if (!active) return controller.stopSalesScrollTop?.();
		if (controller.updateSalesScrollTop) return controller.updateSalesScrollTop();
		const host = controller.root, scroller = controller.list.page.wrapper?.[0]?.closest?.(".main-section");
		if (!host.document?.createElement) return;
		const scrollOwner = () => scroller && scroller.scrollHeight > scroller.clientHeight ? scroller : host.document.scrollingElement || host.document.documentElement;
		let button = controller.salesScrollTopButton;
		if (!button) {
			button = controller.salesScrollTopButton = host.document.createElement("button");
			button.type = "button"; button.className = "btn btn-default dlp-sales-scroll-top"; button.textContent = t("回到顶部"); button.setAttribute("aria-label", t("回到顶部"));
			host.document.body.appendChild(button);
			button.addEventListener("click", () => {
				if (!salesListActive(controller)) return;
				scrollOwner()?.scrollTo({ top: 0, behavior: host.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
			});
		}
		const update = () => { button.hidden = !salesListActive(controller) || (scrollOwner()?.scrollTop || 0) <= 300; };
		controller.updateSalesScrollTop = update;
		controller.stopSalesScrollTop = () => { button.hidden = true; scroller?.removeEventListener("scroll", update); host.removeEventListener("scroll", update); controller.updateSalesScrollTop = null; controller.stopSalesScrollTop = null; };
		scroller?.addEventListener("scroll", update, { passive: true }); host.addEventListener("scroll", update, { passive: true }); update();
	}
	function dismissAutomaticOnboarding(controller) {
		return grid.dismissAutomaticOnboarding(controller, root);
	}
	const grid = engine.create({ doctype: "Sales Order", controllerKey: "dlpSalesOrderGrid", routeClass: "dlp-sales-order-grid-active", columns: COLUMNS, freezeUntil: "name", defaultColumns: presets.finance, preferenceVersion: 4, migratePreferences, keepColumnHeader: true, onSelectionChange: updateReleaseSelection, purchaseChrome: true, mainFilterFields: ["search", "customer", "financial_status", "from_date", "to_date", "company"],
		computedFields: ["dlp_product", "dlp_quantity", "dlp_sales_person", "dlp_receipts", "dlp_sync", "dlp_last_confirmation", "dlp_actions"], extraFields: ["customer", "custom_crm_order_no", "party_account_currency"],
		numbers: ["grand_total", "advance_paid", "per_delivered", "per_billed"], dates: ["transaction_date", "delivery_date"], moneySummary: true, quickFields: ["company", "customer"], searchFields: ["name", "custom_crm_order_no", "customer_name", "custom_odt"], optionLabels: { financial_status: financialLabels, custom_process_status: statusLabels, status: orderStatusLabels },
		controls: [{ fieldname: "search", fieldtype: "Data", label: t("订单号 / 客户 / ODT") }, { fieldname: "customer", fieldtype: "Link", options: "Customer", label: t("客户") }, { fieldname: "financial_status", permission_field: "custom_process_status", fieldtype: "Select", label: t("财务审核状态"), emptyLabel: t("全部审核状态"), options: "\npending\napproved\nrejected" }, { fieldname: "from_date", permission_field: "transaction_date", fieldtype: "Date", label: t("开始日期") }, { fieldname: "to_date", permission_field: "transaction_date", fieldtype: "Date", label: t("结束日期") }, { fieldname: "product", permission_field: "items", fieldtype: "Link", options: "Item", label: t("品目编码") }, { fieldname: "company", fieldtype: "Link", options: "Company", label: t("公司") }],
		exportOptions: args => ({ columnTransforms: Object.fromEntries(args.fields.flatMap((field, index) => field === "custom_process_status" ? [[index + 2, { header: t("财务审核状态"), value: value => t(financialLabel(value)) }]] : [])) }),
		searchConflictMessage: "现有 OR 筛选已保留；清除后可使用订单 / 客户搜索。",
		transformQuery, onRows, onColumnsChange, mountControls, renderValue, onRequestStart: invalidateReleaseSelection,
		onRouteChange: (controller, active) => { if (controller) controller.salesRouteActive = active; mountScrollTop(controller, active); mountHorizontalScroll(controller, active); if (!active && controller) { invalidateReleaseSelection(controller, t("未选择订单")); controller.stopInitialOnboarding?.(); } else controller?.updateSalesSelection?.(); },
		renderLink: (controller, doc, value) => `<button type="button" class="dlp-sales-name dlp-sales-detail" data-name="${esc(doc.name)}">${value}</button>`,
		renderSequence: (controller, doc, sequence) => `<button class="dlp-sales-expand" type="button" data-name="${esc(doc.name)}" aria-expanded="${controller.salesExpanded?.has(doc.name) || false}" aria-label="${esc(t("展开产品明细"))}">${controller.salesExpanded?.has(doc.name) ? "⌄" : "›"} ${sequence}</button>`,
		rowExtra: (controller, doc) => controller.salesExpanded?.has(doc.name) ? `<section class="dlp-sales-expanded"><strong>${esc(t("订单产品明细"))}</strong>${itemTable(controller.salesExpanded.get(doc.name))}</section>` : "",
		afterRender: controller => { controller.updateSalesSelection?.(); mountScrollTop(controller, salesListActive(controller)); mountHorizontalScroll(controller, salesListActive(controller)); },
	});
	return { ...grid, COLUMNS, presets, itemTable, detailsHTML, receiptsHTML, transformQuery, financialLabel, statusLabels, orderStatusLabels, updateReleaseSelection, invalidateExpandedDetails, dismissAutomaticOnboarding };
});
