(function (root, factory) {
	const adapter = factory();
	if (typeof module === "object" && module.exports) module.exports = adapter;
	root.DeepLinkERPUnifiedPurchase = adapter;
	if (root.document && root.frappe) adapter.installNavigation(root);
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
	"use strict";
 const translateText = (text, args = []) => String((globalThis.__ || (value => value))(text)).replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");

	const SERVICE = "deeplinkerp_branding.services.unified_purchase_service.";
	const groups = [
		["name", "订单/审批", 166], ["supplier_name", "供应商/采购付款公司", 190],
		["project_context", "项目/最终归属", 200], ["external_payment", "供应商付款", 190],
		["internal_settlement", "集团内部结算", 200], ["receipt_logistics", "收货物流", 200], ["receipt_action", "操作", 155],
	].map(([fieldname, label, width]) => ({ fieldname, label, width }));
	const virtualFields = ["row_type", "source", "oa_number", "approval_status", "oa_amount", "requested_amount", "cashier_paid_amount", ...groups.slice(2).map(c => c.fieldname), "order_settled", "order_unpaid", "payment_progress", "receipt_progress"];
	const groupFields = new Set(groups.slice(2).map(c => c.fieldname));
	const additions = [
		["row_type", "单据类型", 104], ["source", "来源", 108], ["oa_number", "OA 来源单", 180],
		["approval_status", "审批状态", 120], ["oa_amount", "来源明细金额", 180],
		["requested_amount", "来源申请金额", 180], ["cashier_paid_amount", "出纳实付（证据）", 180],
	].map(([fieldname, label, width]) => ({ fieldname, label, width }));
	const itemColumns = [["warehouse", "仓库", 112], ["item_code", "物料编码", 100], ["item_name", "物料名称", 150], ["qty", "数量", 72], ["uom", "单位", 54], ["rate", "单价", 100], ["amount", "金额", 112], ["received_qty", "已入库数量", 86]].map(([fieldname, label, width]) => ({ fieldname, label, width }));
	function filters(controller) {
		const quick = controller.quick || {}, result = { scope: controller.providerScope };
		for (const key of ["search", "from_date", "to_date", "company", "beneficiary_company", "progress_phase", "source", "approval_status", "status", "advance_payment_status"]) {
			if (quick[key] !== undefined && quick[key] !== null && quick[key] !== "") result[key] = quick[key];
		}
		if (quick.pending_company) result.company = "__unconfirmed__";
		if (quick.review_only) result.review_only = true;
		return result;
	}
	function nativeSort(orderBy) {
		const primary = String(orderBy || "").split(",")[0].trim();
		const match = /^(?:`tabPurchase Order`\.)?`?([a-z_][a-z0-9_]*)`?\s+(asc|desc)$/i.exec(primary);
		return match && !groupFields.has(match[1]) ? `${match[1]} ${match[2].toLowerCase()}` : null;
	}
	function getArgs(controller, nativeArgs = controller.originals?.get_args?.call(controller.list) || {}) {
		const order = nativeSort(nativeArgs.order_by);
		if (order) {
			const changed = controller.providerNativeOrder !== undefined && controller.providerNativeOrder !== order;
			if (controller.providerSortSource !== "header" || changed) {
				controller.providerOrderBy = order; controller.providerSortSource = "native";
				if (changed) controller.setPage?.(0);
			}
			controller.providerNativeOrder = order;
		}
		const shared = filters(controller), native = [], advanced = new Set((controller.list?.filter_area?.filter_list?.get_filters?.() || []).map(filter=>JSON.stringify(filter.slice(0,4))));
		for (const filter of nativeArgs.filters || []) {
			const [doctype,field,operator,value] = filter;
			if (doctype === 'Purchase Order' && ['company','status','advance_payment_status'].includes(field) && operator === '=' && !advanced.has(JSON.stringify(filter.slice(0,4))) && controller.nativeControlFields?.has(field) && controller.controls[field].get_value() === value) {
				if (!(field === 'company' && controller.quick?.pending_company)) shared[field] = value;
			} else native.push(filter);
		}
		return { filters: JSON.stringify(shared), native_filters: JSON.stringify(native), native_or_filters: JSON.stringify(nativeArgs.or_filters || []), start: controller.page * controller.pageSize, page_length: controller.pageSize, order_by: controller.providerOrderBy || "transaction_date desc" };
	}
	function request(controller, nativeArgs) { return { method: `${SERVICE}get_unified_purchase_list`, args: getArgs(controller, nativeArgs) }; }
	function formLink(doc) { return `/desk/${doc.row_type === "oa_request" ? "oa-purchase-request" : "purchase-order"}/${encodeURIComponent(doc.name)}`; }
	function exportColumns(columns) {
		const mapped = { name: "order_context", supplier_name: "supplier_context", receipt_action: "action_context", order_settled: "external_settled", order_unpaid: "external_order_unpaid" };
		const fields = columns.map(field => mapped[field] || field);
		for (const [amount, metadata] of [["grand_total", ["currency"]], ["oa_amount", ["oa_currency", "oa_amount_basis"]], ["requested_amount", ["oa_currency"]], ["cashier_paid_amount", ["cashier_currency"]], ["advance_paid", ["party_account_currency"]]]) {
			if (fields.includes(amount)) fields.splice(fields.indexOf(amount) + 1, 0, ...metadata.filter((field) => !fields.includes(field)));
		}
		const external = Math.max(fields.indexOf("external_settled"), fields.indexOf("external_order_unpaid"));
		if (external >= 0 && !fields.includes("external_currency")) fields.splice(external + 1, 0, "external_currency");
		for (const field of ["oa_warning", "oa_references"]) if (!fields.includes(field)) fields.push(field);
		return fields;
	}
	function numeric(value, quantity = false) {
		if (value === null || value === undefined || value === "" || typeof value === "boolean") return "—";
		const number = Number(value);
		return Number.isFinite(number) ? number.toLocaleString(undefined, { minimumFractionDigits: quantity ? 0 : 2, maximumFractionDigits: 2 }) : "—";
	}
	function monetary(value, currency) { return `${numeric(value)}${value == null ? "" : ` ${currency || translateText("币种待核对")}`}`; }
	function quantity(value, unit) { return `${numeric(value, true)}${unit ? ` ${unit}` : translateText(" 单位待核对")}`; }
	function group(lines) { return `<span class="dlp-po-group">${lines.filter(Boolean).join("")}</span>`; }
	function line(text, escape, kind = "", title = "") { return `<span class="dlp-po-group-line${kind ? ` ${kind}` : ""}"${title ? ` title="${escape(translateText(title))}"` : ""}>${escape(translateText(text))}</span>`; }
	function restricted(doc) { return doc.order_progress?.state === "restricted"; }
	function renderGroup(field, doc, escape) {
		const progress = doc.order_progress || {}, role = doc.role_context || {};
		const muted = (text, title) => line(text, escape, "text-muted", title);
		const warning = (text, title) => line(text, escape, "text-warning", title);
		const denied = () => group([warning(translateText("关联进度受限，请核对权限"))]);
		if (restricted(doc) && ["supplier_name", "project_context", "external_payment", "internal_settlement", "receipt_logistics"].includes(field)) return denied();
		if (field === "supplier_name") {
			const company = doc.row_type === "purchase_order" ? doc.company : role.company_confirmed ? role.purchasing_company : null;
			return group([line(doc.supplier_name || translateText("供应商待核对"), escape), muted(company ? translateText("采购付款：{0}", [company]) : doc.company_visibility === "hidden" ? translateText("公司不可见") : translateText("采购付款公司待确认")), role.buyer_company_proposal ? warning(translateText("来源待核对：建议 {0}", [role.buyer_company_proposal])) : ""]);
		}
		if (field === "project_context") {
			const hints = [...new Set([role.source_beneficiary_company_hint, role.beneficiary_company_candidate, role.source_project_hint, role.project_candidate].filter(Boolean))];
			return group([line(doc.project || role.project || translateText("项目待核对"), escape), muted(translateText("最终归属：{0}", [role.beneficiary_companies?.join("、") || "待核对"])), ...hints.map(value => warning(translateText("来源待核对：{0}", [value]))), role.role_warnings?.length ? warning(translateText("归属待核对"), role.role_warnings.join("；")) : ""]);
		}
		if (field === "external_payment") {
			const external = progress.external || {}, lines = [];
			if (external.state === "restricted") return denied();
			if (doc.row_type === "purchase_order") {
				lines.push(line(translateText("订单金额：{0}", [monetary(doc.grand_total, doc.currency)]), escape));
				if (external.state === "not_applicable") lines.push(muted(translateText("本地内部供货；外部付款不适用")));
				else if (external.state === "exact") lines.push(line(translateText("ERP 已付：{0}", [monetary(external.settled, external.currency)]), escape), muted(translateText("订单未付：{0}", [monetary(external.order_unpaid, external.currency)]), "按订单承诺计算，非应付余额"));
				else lines.push(warning(translateText("供应商付款待核对"), external.settlement_notice || progress.settlement_notice));
			}
			for (const reference of doc.oa_references || []) {
				lines.push(muted(translateText("来源 {0}", [reference.number || reference.name || "待核对"])), muted(`${reference.amount_basis || translateText("来源明细")}：${monetary(reference.amount, reference.currency)}`), muted(translateText("申请：{0}", [monetary(reference.requested_amount, reference.currency)])));
				if (reference.warning) lines.push(warning(translateText("来源待核对"), reference.warning));
				lines.push(muted(translateText("出纳证据：{0}", [monetary(reference.cashier_paid_amount, reference.cashier_currency)]), reference.cashier_evidence_status));
				lines.push(reference.cashier_reconciliation_verified === true ? muted(translateText("已核对 ERP 付款")) : warning(reference.cashier_reconciliation_verified === false ? translateText("未核对 ERP") : translateText("核对状态未知"), reference.cashier_reconciliation_warning));
			}
			return group(lines.length ? lines : [muted(translateText("来源待完善；ERP 付款未知"))]);
		}
		if (field === "internal_settlement") {
			if (doc.row_type === "oa_request") return group([muted(translateText("来源待完善；未形成内部应付"))]);
			const labels = { custody: "保管关系；不形成内部应付", draft_quote: "内部订单草稿；未形成应付", price_unconfirmed: "价格待确认；未形成应付", price_pending: "价格待确认；未形成应付", price_stale: "价格需重新核对；未形成应付", source_stale: "关联需重新核对", awaiting_invoice: "已确认报价；待开应付", shared: "共享应付待核对", review: "内部结算待核对" };
			return group((progress.internal || []).flatMap(entry => {
				if (["restricted", "setup_required"].includes(entry.state)) return [warning(translateText("关联进度受限，请核对权限"))];
				const identity = [entry.beneficiary_company, entry.internal_order].filter(Boolean).join(" · ");
				const result = [identity ? muted(identity) : ""];
				if (entry.state === "payable") result.push(line(translateText("内部应付：{0}", [monetary(entry.payable_total, entry.currency)]), escape), muted(translateText("ERP 已付：{0}", [monetary(entry.payable_settled, entry.currency)])), muted(translateText("应付未付：{0}", [monetary(entry.payable_outstanding, entry.currency)])));
				else result.push(warning(labels[entry.state] || translateText("内部结算待核对"), entry.warnings?.join("；")));
				return result;
			}).concat(!(progress.internal || []).length ? [warning(translateText("内部关联待核对；未认定应付"))] : []));
		}
		if (field === "receipt_logistics") {
			if (doc.row_type === "oa_request") return group([muted(translateText("来源待完善；收货待核对"))]);
			const domestic = progress.domestic_receipt || {}, factory = progress.factory_receipt || {}, lines = [];
			if (domestic.state === "native_received") lines.push(...(domestic.quantities || []).map(entry => muted(translateText("采购公司入库：{0}", [quantity(entry.qty, entry.uom)]))));
			else if (domestic.state === "restricted") lines.push(warning(translateText("采购公司收货受限")));
			else if (domestic.state === "review") lines.push(warning(translateText("采购公司收货待核对"), domestic.warnings?.join("；")));
			else lines.push(muted(translateText("采购公司尚无可核对入库")));
			if (factory.state === "exact") for (const entry of factory.quantities || []) {
				lines.push(muted(entry.beneficiary_company || translateText("最终公司")), line(translateText("已入库：{0}", [quantity(entry.received_stock_qty, entry.stock_uom)]), escape), muted(translateText("待入库：{0}", [quantity(entry.pending_stock_qty, entry.stock_uom)])));
			} else lines.push(warning(translateText("工厂入库待核对"), factory.warnings?.join("；")));
			for (const entry of progress.receipt_logistics || []) {
				if (["restricted", "setup_required"].includes(entry.state)) { lines.push(warning(translateText("物流关联受限，请核对权限"))); continue; }
				const title = [...(entry.warnings || []), ...(entry.provenance || []).map(proof => [proof.source_id, proof.author, proof.time].filter(Boolean).join(" · "))].join("；");
				lines.push(muted(`${entry.beneficiary_company || translateText("最终公司")} · ${entry.state === "reported" ? translateText("物流报告到货（非 ERP 入库）") : translateText("物流待核对")}`, title));
				if (entry.state === "reported") lines.push(...(entry.reported_quantities || []).map(value => muted(translateText("物流报告：{0} {1}", [quantity(value.qty, value.uom), value.destination || ""]))));
				const node = entry.manual_nodes?.at(-1);
				if (node) lines.push(muted(translateText("人工节点：{0}{1}", [({domestic_dispatch:'国内发运',international_dispatch:'国际发运',customs:'清关',reported_arrival:'报告到货（非 ERP 入库）',review:'待核对',evidence:'补充证据'})[node.node] || node.node || "待核对", node.qty == null ? "" : ` ${quantity(node.qty, node.uom)}`]), [node.note, node.by, node.on].filter(Boolean).join(" · ")));
			}
			return group(lines);
		}
	}
	function renderValue(field, doc, formatters = {}, escape = String) {
		if(field==='payment_progress') {
			const value=doc.order_progress?.external || {}, labels={restricted:'付款进度不可见',not_applicable:'外部付款不适用'};
			if(labels[value.state])return escape(labels[value.state]);
			if(value.state!=='exact' || value.settled==null || value.order_unpaid==null)return escape('付款口径待核对');
			return escape(Number(value.order_unpaid)<=0?'已付清':Number(value.settled)>0?'部分付款':'未付款');
		}
		if(field==='receipt_progress')return escape(doc.per_received==null?'收货进度不可见':Number(doc.per_received)>=100?'已收齐':Number(doc.per_received)>0?'部分收货':'未收货');
		const grouped = renderGroup(field, doc, escape);
		if (grouped !== undefined) return grouped;
		if (["order_settled", "order_unpaid"].includes(field)) return escape(monetary(doc.order_progress?.[field === "order_settled" ? "settled" : "order_unpaid"], doc.order_progress?.currency || doc.currency));
		if (field === "name" && doc.row_type === "oa_request") return escape(translateText("待完善 · {0}", [doc.oa_number || doc.name]));
		if (field === "receipt_action" && doc.row_type === "oa_request") return `<button type="button" class="btn btn-xs btn-default" data-purchase-source="${escape(doc.oa_name || doc.name)}">${translateText("完善/关联")}</button>`;
		if (field === "row_type") return escape(doc.row_type === "oa_request" ? "待完善来源" : "采购订单");
		if (field === "source") return escape(["OA", "oa"].includes(doc.source) ? "钉钉" : ["non_oa", "未关联 OA"].includes(doc.source) ? "其他来源" : doc.source || "来源待确认");
		if (field === "company" && !doc.company) return doc.company_visibility === "hidden" ? "公司不可见" : "公司待确认";
		if (field === "oa_number") {
			if (doc.oa_references?.length) return doc.oa_references.map((ref) => {
				const title = [ref.approval_status, ref.amount == null ? "金额不可见" : monetary(ref.amount, ref.currency), ref.amount_basis, ref.company, ref.warning, doc.oa_warning].filter(Boolean).join("；");
				return `<a href="/desk/oa-purchase-request/${encodeURIComponent(ref.name)}" title="${escape(translateText(title))}">${escape(ref.number || ref.name)}</a>`;
			}).join("；");
			if (!doc.oa_name) return escape(doc.oa_warning || "—");
			return `<a href="/desk/oa-purchase-request/${encodeURIComponent(doc.oa_name)}" title="${translateText('查看 OA 申请及钉钉原单')}">${escape(doc.oa_number || doc.oa_name)}</a>`;
		}
		if (["oa_amount", "requested_amount", "cashier_paid_amount"].includes(field)) {
			if (doc[field] === undefined || doc[field] === null || doc[field] === "") return escape(field === "oa_amount" ? doc.oa_warning || "—" : "—");
			const amount = Number(doc[field]);
			if (!Number.isFinite(amount)) return "—";
			const number = numeric(amount);
			const title = field === "cashier_paid_amount" ? "出纳实付证据；ERP 登记付款和核销另列" : [field === "oa_amount" ? doc.oa_amount_basis : "来源申请金额", doc.oa_warning].filter(Boolean).join("；");
			const currency = field === "cashier_paid_amount" ? doc.cashier_currency : doc.oa_currency;
			return `<span title="${escape(translateText(title))}">${escape(number)} ${escape(currency || "币种待确认")}${field !== "cashier_paid_amount" && doc.oa_warning ? " ⚠" : ""}</span>`;
		}
		if (field === "approval_status") return escape(doc.approval_status || [...new Set((doc.oa_references || []).map((ref) => ref.approval_status).filter(Boolean))].join("；") || "—");
	}
	function renderLink(controller, doc, value, escape = String) {
		const oa = doc.row_type === "oa_request", references = doc.oa_references || [];
		const identity = oa ? `<button type="button" class="btn btn-link btn-xs" data-purchase-source="${escape(doc.oa_name || doc.name)}" title="${escape(doc.oa_number || doc.name)}">${value}</button>` : `<a href="${escape(formLink(doc))}" data-name="${escape(doc.name)}">${value}</a>`;
		const status = oa ? escape(doc.status || "来源待完善") : controller.list.get_indicator_html?.(doc, Boolean(controller.list.workflow_state_fieldname)) || escape(controller.translate(doc.status || "—"));
		const date = controller.root.frappe.datetime?.str_to_user?.(doc.transaction_date) || doc.transaction_date || "—";
		return group([identity, line(date, escape, "text-muted"), `<span class="dlp-po-group-line dlp-po-status">${status}</span>`, ...references.map(ref => `<a class="dlp-po-group-line" href="/desk/oa-purchase-request/${encodeURIComponent(ref.name)}">${escape(ref.number || ref.name)}${ref.approval_status ? ` · ${escape(ref.approval_status)}` : ""}</a>`), references.length ? "" : line([doc.source === "OA" ? "钉钉" : doc.source === "未关联 OA" ? "其他来源" : "来源待确认", doc.approval_status].filter(Boolean).join(" · "), escape, "text-muted")]);
	}
	function summary(controller, escape = String) {
		const totals = controller.providerPayload?.totals;
		if (!totals) return "";
		const format = (rows) => (rows || []).map((r) => `${Number(r.amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${r.currency}`).join(" · ") || "—";
		return escape(translateText("订单金额：{0} ｜ OA 申请金额：{1}{2}", [format(totals.orders), format(totals.oa), totals.oa_unknown_currency_count ? translateText(" ｜ {0} 条 OA 币种待确认（未计合计）", [totals.oa_unknown_currency_count]) : ""]));
	}
	async function exportCurrent(controller) {
		const { root } = controller;
		if (controller.providerScope === "orders" && root.frappe.model?.can_export?.("Purchase Order") === false) throw new Error(translateText("当前用户没有导出权限。"));
		const args = { ...getArgs(controller), columns: JSON.stringify(exportColumns(controller.preferences.columns)) };
		delete args.start; delete args.page_length;
		if (!root.DeepLinkERPPurchaseOrderExport?.downloadWorkbook) await root.frappe.require("/assets/deeplinkerp_branding/js/purchase_order_export.js");
		const exporter = root.DeepLinkERPPurchaseOrderExport;
		if (!exporter?.downloadWorkbook) throw new Error(translateText("导出组件加载失败，请刷新重试。"));
		const bytes = await exporter.fetchNativeWorkbook(root, args, `${SERVICE}export_unified_purchase_list`);
		exporter.downloadWorkbook(root, bytes, "统一采购列表");
	}
	function mountControls(controller) {
		const { root, list } = controller, $ = root.$;
		const sorter = list.sort_selector;
		if (sorter) {
			const change = sorter.onchange || sorter.change;
			sorter.onchange = function (...args) {
				controller.providerSortSource = "native"; controller.setPage?.(0);
				return change?.apply(this, args);
			};
		}
		controller.$providerControls = $(`<div class='dlp-po-provider-controls'><label>${translateText("来源")} <select class='form-control input-xs' data-filter='source' aria-label='${translateText("来源")}'><option value=''>${translateText("全部来源")}</option><option value='oa'>${translateText("钉钉")}</option><option value='non_oa'>${translateText("其他来源")}</option></select></label><details class='dlp-po-source-advanced'><summary>${translateText("来源筛选")}</summary><label>${translateText("审批状态")} <input class='form-control input-xs' data-filter='approval_status' aria-label='${translateText("审批状态")}' placeholder='${translateText("审批状态")}'></label><label><input type='checkbox' data-filter='pending_company'> ${translateText("公司待确认")}</label></details></div>`);
		if(controller.$advancedFilters)controller.$providerControls.appendTo(controller.$advancedFilters);else controller.$providerControls.insertAfter(controller.$filters);
		if (root.frappe.session?.user === "Administrator" || root.frappe.user_roles?.includes("System Manager")) $(`<button type='button' class='btn btn-default btn-sm dlp-source-sync'>${translateText("同步钉钉")}</button>`).appendTo(controller.$providerControls).on("click.dlpUnified", async event => {
			const button = $(event.currentTarget).prop("disabled", true);
			try {
				if (!root.deeplinkerp?.purchaseSource?.sync) await root.frappe.require("/assets/deeplinkerp_branding/js/purchase_source.js?v=0.0.2");
				await root.deeplinkerp.purchaseSource.sync(); await controller.refresh();
			} catch (error) { root.frappe.msgprint({ message: error.message || translateText("采购来源同步失败，请核对系统提示。"), indicator: "red" }); }
			finally { button.prop("disabled", false); }
		});
		controller.$providerControls.on("input.dlpUnified change.dlpUnified", "[data-filter]", (e) => {
			const field = e.target.dataset.filter, value = e.target.type === "checkbox" ? e.target.checked : e.target.value;
			if (controller.quick[field] === value) return;
			controller.quick[field] = value;
			controller.setPage(0); controller.refresh();
		});
		list.$result.on("click.dlpUnified", "[data-provider-sort]", (e) => {
			const field = e.currentTarget.dataset.providerSort, previous = controller.providerOrderBy.split(" ");
			controller.providerOrderBy = `${field} ${previous[0] === field && previous[1] === "asc" ? "desc" : "asc"}`;
			controller.providerSortSource = "header";
			if (sorter && controller.allowed?.has(field)) {
				sorter.set_value(field, controller.providerOrderBy.split(" ")[1]);
				controller.providerNativeOrder = nativeSort(sorter.get_sql_string());
			}
			controller.setPage(0); controller.refresh();
		});
		controller.setProviderScope("orders", false);
		onActivate(controller);
	}
	function onActivate(controller) {
		const { root, list } = controller, realtime = root.frappe.realtime;
		if (!realtime?.on || !realtime?.off) return;
		controller.oaRealtimeListener ||= (data) => {
			if (data?.doctype === "OA Purchase Request" && root.cur_list === list && list.page.wrapper.is(":visible")) {
				if (controller.queueRealtimeRefresh) controller.queueRealtimeRefresh(data);
				else controller.refresh();
			}
		};
		// Native ListView initialization clears list_update listeners, including cached adapters.
		realtime.off("list_update", controller.oaRealtimeListener);
		realtime.on("list_update", controller.oaRealtimeListener);
	}
	function onPayload(controller) {
		onActivate(controller);
		if (controller.providerPayload?.capabilities?.oa_request && !controller.oaSubscribed) {
			controller.root.frappe.realtime?.doctype_subscribe?.("OA Purchase Request"); controller.oaSubscribed = true;
		}
		controller.$providerNotice?.remove();
		const warnings = controller.providerPayload?.warnings || [];
		if (warnings.length) controller.$providerNotice = controller.root.$("<div class='text-muted dlp-po-provider-notice'></div>").text(warnings.join("；")).insertAfter(controller.$providerControls);
	}
	function listPath(href) { try { return new URL(href, "https://local.invalid").pathname.replace(/\/$/, ""); } catch (_) { return ""; } }
	function shouldHideOANavigation(href, availableHrefs, canReadPO) {
		return Boolean(canReadPO && ["/desk/oa-purchase-request", "/app/oa-purchase-request"].includes(listPath(href)) && availableHrefs.some((value) => ["/desk/purchase-order", "/app/purchase-order"].includes(listPath(value))));
	}
	function installNavigation(root) {
		const sync = () => {
			const canRead = Boolean(root.frappe.model?.can_read?.("Purchase Order"));
			for (const container of root.document.querySelectorAll(".body-sidebar-container")) {
				const links = [...container.querySelectorAll("a[href]")], hrefs = links.map((link) => link.getAttribute("href"));
				for (const link of links) if (['/desk/purchase-payables','/desk/purchase-payment-records'].includes(listPath(link.getAttribute('href')))) {
					const disabled=!root.frappe.model?.can_read?.('Payment Entry');
					link.setAttribute('aria-disabled',String(disabled));link.classList.toggle('dlp-finance-disabled',disabled);
				}
				for (const link of links) if (shouldHideOANavigation(link.getAttribute("href"), hrefs, canRead)) {
					const item = link.closest(".standard-sidebar-item, .sidebar-item");
					if (item) { item.hidden = true; item.dataset.dlpOaCompatibility = "true"; }
				}
			}
		};
		const start = () => {
			root.document.addEventListener('click',event=>{const link=event.target.closest?.('a.dlp-finance-disabled');if(link){event.preventDefault();event.stopPropagation();}},true);
			sync(); new root.MutationObserver(sync).observe(root.document.body, { childList: true, subtree: true });
			root.frappe.router?.on("change", sync);
		};
		if (root.document.readyState === "loading") root.document.addEventListener("DOMContentLoaded", start, { once: true }); else start();
	}
	function configure(nativeColumns) {
		const columns = nativeColumns.map(col => ({ ...col }));
		const approved=nativeColumns.some(col=>col.fieldname==='transaction_date');
		const seen = new Set(columns.map(c => c.fieldname));
		// Material columns stay in expansion/export, never a no-op main-table choice.
		for (const col of [...groups, ...additions, ...(approved?[]:itemColumns)]) if (!seen.has(col.fieldname)) { columns.push(col); seen.add(col.fieldname); }
		const defaultColumns = approved ? ["name", "supplier_name", "transaction_date", "status", "company", "grand_total", "payment_progress", "receipt_progress", "receipt_action"] : nativeColumns.map(col=>col.fieldname);
		const migratePreferences = value => approved && value?.version!==4 ? {...value,columns:defaultColumns}:value;
		return { columns, defaultColumns, migratePreferences, preferenceVersion: 4, inheritPreferences: true, alwaysActive: true, freezeUntil: "supplier_name", virtualFields: [...virtualFields, ...itemColumns.map(c => c.fieldname)], useNativeIndicator: doc => doc.row_type === "purchase_order", getArgs, request, formLink, renderLink, renderValue, summary, exportCurrent, mountControls, onActivate, onPayload, sortFields: ["transaction_date", "name", "supplier_name", "company", "status", "grand_total", "oa_amount", "approval_status"] };
	}
	return { configure, getArgs, request, formLink, renderValue, summary, exportColumns, exportCurrent, shouldHideOANavigation, installNavigation };
});
