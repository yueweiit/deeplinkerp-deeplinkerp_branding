(function (root, factory) {
	if (typeof module === "object" && module.exports) module.exports = factory;
	else {
		root.deeplinkerp ||= {};
		root.deeplinkerp.purchaseSource ||= factory(root);
	}
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
	"use strict";
	const SERVICE = "deeplinkerp_branding.services.purchase_source_service.";
	const t = (value, args = []) => String((root.__ || (text => text))(value)).replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");
	const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
	const labels = { region: "执行地区", currency: "币种", payee: "收款人", requested_amount: "申请金额", items: "需求明细", processors: "加工商明细", detail_total: "明细汇总金额", description: t("采购说明"), schedule_date: "交付日期", department: "申请部门", project: "项目", order_no: "来源订单编号", payments: "原单付款信息", payment_date: "原单付款日期", payment_terms: "付款条件", attachments: "关键凭证" };
	const text = value => value === undefined || value === null || value === "" ? "—" : esc(value);
	const exactDisplay = (value, format) => value == null || value === "" ? "—" : `<span title="${esc(t("原值"))} · ${esc(value)}">${format(value)}</span>`;
	const money = (value, currency) => exactDisplay(value, amount => root.DeepLinkERPPurchasePayments.formatMoney(amount, currency || "币种待确认"));
	const quantity = value => exactDisplay(value, amount => root.DeepLinkERPPurchasePayments.formatQuantity(amount));
	const poLink = name => `<a href="/desk/purchase-order/${encodeURIComponent(name)}">${esc(name)}</a>`;
	const call = async (method, args, options = {}) => (await root.frappe.call({ method: SERVICE + method, args, silent: true, ...options })).message;
	let syncToken;
	async function sync() {
		if (root.frappe.session?.user !== "Administrator" && !root.frappe.user_roles?.includes("System Manager")) throw new Error(t("采购来源同步需要管理员权限。"));
		syncToken ||= root.DeepLinkERPPurchasePayments.retryToken(() => root.crypto.randomUUID(), `dlp-source-sync:${root.frappe.boot?.sitename || root.location?.host}:${root.frappe.session?.user}`);
		const result = await call("sync_purchase_sources", { request_id: syncToken.forPayload({}) }, { type: "POST" });
		syncToken.succeeded();
		if (result?.failed) throw new Error(result.error || t("采购来源同步未完成，请重新核对。"));
		root.frappe.show_alert({ message: [["新建草稿", "created"], ["已关联", "already_linked"], ["待完善", "pending"], ["失效", "invalid"]].map(([label, key]) => `${t(label)} ${result?.[key] ?? 0}`).join(" · "), indicator: "green" });
		return result;
	}
	function readable(value) {
		if (typeof value === "string" && /^[\[{]/.test(value.trim())) { try { return readable(JSON.parse(value)); } catch (_) { /* Original text remains evidence. */ } }
		if (Array.isArray(value)) return `<ol>${value.map(item => `<li>${readable(item)}</li>`).join("")}</ol>`;
		if (value && typeof value === "object") return `<dl>${Object.entries(value).map(([key, item]) => `<dt>${esc(labels[key] || key)}</dt><dd>${readable(item)}</dd>`).join("")}</dl>`;
		return `<span class="dlp-source-text">${text(value)}</span>`;
	}
	function originalLink(source) {
		try {
			const url = new URL(source.source_url);
			if (url.protocol === "https:" && url.hostname === "aflow.dingtalk.com") return `<a href="${esc(url.href)}" target="_blank" rel="noopener noreferrer">${esc(t("钉钉原单"))}</a>`;
		} catch (_) { /* Unavailable source URLs are not guessed. */ }
		return esc(t("钉钉原单链接暂不可用"));
	}
	const blockers = plan => [...new Set((plan?.blocking_reasons || []).filter(Boolean))];
	const newItemsHTML = items => (items || []).map(item => `<p>${text(item.item_code)} · ${text(item.item_name)} · ${text(item.item_group)} · ${text(item.uom)} · ${esc(t(({ 0: "非库存物料", 1: "库存物料" })[item.is_stock_item] || "库存属性待确认"))}</p>`).join("");
	function adaptationHTML(plan, historical = false) {
		if (!plan) return "";
		const columns = [["item_code", "物料编码"], ["item_name", "物料名称"], ["qty", "数量"], ["uom", "单位"], ["rate", "单价"], ["amount", "金额"], ["processor_name", "加工商"], ["processing_materials", "加工材料"], ["odt", "ODT"], ["sales_order_no", "销售订单"]];
		const table = root.DeepLinkERPCompactList.detailTable({ columns, items: plan.items || [], escape: esc, translate: t, format: (field, item) => field === "qty" ? quantity(item.qty) : ["rate", "amount"].includes(field) ? money(item[field], plan.currency) : undefined, wrapperClass: "dlp-document-items", tableClass: "table-bordered" });
		const proposed = newItemsHTML(plan.new_items);
		return `<section class="dlp-source-adaptation"><h5>${esc(t(historical ? "建单时适配明细（只读）" : "适配采购明细（只读）"))}</h5><p>${esc(t("适配明细合计"))}：${money(plan.total_amount, plan.currency)}</p>${plan.date_adjustment ? `<p class="text-muted">${esc(plan.date_adjustment)}</p>` : ""}${table}${proposed ? `<h5>${esc(t(historical ? "建单时拟新增物料" : "拟新增物料"))}</h5>${proposed}` : ""}</section>`;
	}
	function evidenceHTML(detail) {
		const source = detail.source, proof = detail.cashier || {};
		const cashier = proof.payment_evidence_status === "hidden" ? { payment_evidence_status: "hidden", issues: proof.issues } : proof;
		const facts = [["来源申请金额", money(source.requested_amount, source.currency)], ["来源明细金额", money(source.detail_total_amount, source.currency)], ["出纳实付（证据）", money(cashier.paid_amount, cashier.currency)], ["付款证据状态", text(cashier.payment_evidence_status === "hidden" ? "付款证据不可见" : cashier.payment_evidence_status === "recorded" ? "已记录" : "待核对")], ["申请日期", text(source.apply_date)], ["原交付日期", text(source.schedule_date)], ["原收款人", text(source.payee)]];
		const columns = [["item_code", "原物料编码"], ["item_name", "原物料名称"], ["qty", "原数量"], ["uom", "原单位"], ["amount", "原明细金额"]];
		const table = root.DeepLinkERPCompactList.detailTable({ columns, items: source.items || [], escape: esc, translate: t, format: (field, item) => field === "qty" ? quantity(item.qty) : field === "amount" ? money(item.amount, source.currency) : undefined, wrapperClass: "dlp-document-items", tableClass: "table-bordered" });
		const files = [...(source.attachment_manifest || []), ...(cashier.attachments || [])];
		const attachments = files.map(file => {
			const id = file.attachment_id, available = file.downloadable === true && id && file.version;
			const url = `/api/method/${SERVICE}download_source_attachment?name=${encodeURIComponent(detail.name)}&attachment_id=${encodeURIComponent(id)}&version=${encodeURIComponent(file.version)}`;
			return `<p>${available ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${text(file.filename || id)}</a>` : text(file.filename || id)} · ${esc(t(available ? "可下载证据" : "附件暂不可下载，请在原单核对"))}</p>`;
		}).join("") || `<p>${esc(t("暂无已归档附件，请在原单核对。"))}</p>`;
		const payments = (cashier.payments || []).map(payment => `<p>${text(payment.source_id)} · ${text(payment.payment_date)} · ${money(payment.amount, payment.currency)} · ${text(payment.payer)} · ${text(payment.evidence_status)}<br>${text(payment.remark)} · ${text(payment.bank_reference)}</p>`).join("") || `<p>${esc(t("尚无可核实的实际付款证据。"))}</p>`;
		const plan = detail.purchase_order ? detail.adopted_adaptation : detail.adaptation, reasons = detail.purchase_order ? [] : blockers(plan);
		const replacesLegacy = Array.isArray(plan?.items) && Array.isArray(plan?.blocking_reasons);
		const legacyIssues = [detail.pending_reason, ...(source.issues || [])].filter(issue => issue && !(replacesLegacy && ["采购明细待完善", "采购明细与申请金额不一致，请核对原单"].includes(issue)));
		const issues = [...new Set([...legacyIssues, ...reasons, ...(cashier.issues || [])].filter(Boolean))];
		return `<section class="dlp-source-evidence"><h4>${esc(t("采购来源"))} · ${text(source.business_id || detail.name)}</h4><p>${originalLink(source)} · <a href="/desk/oa-purchase-request/${encodeURIComponent(detail.name)}">${esc(t("OA 来源记录"))}</a></p><div class="dlp-source-facts">${facts.map(([label, value]) => `<div><strong>${esc(t(label))}</strong><span>${value}</span></div>`).join("")}</div><p class="text-muted">${esc(t("出纳实付是原付款证据；ERP 登记付款和核销在订单中另列。"))}</p>${issues.map(issue => `<p class="text-warning">${esc(t(issue))}</p>`).join("")}${adaptationHTML(plan, Boolean(detail.purchase_order))}${detail.scope_review ? `<h5>${esc(t("来源范围复核"))}</h5><p>${esc(t("原采用范围"))}：${readable(detail.scope_review.adopted_scope)}</p><p>${esc(t("本次核对范围"))}：${readable(detail.scope_review.current_scope)}</p>` : ""}<h5>${esc(t("原始采购明细（只读）"))}</h5>${table}<details><summary>${esc(t("原始申请内容"))}</summary>${readable(source.original_fields || {})}</details><details><summary>${esc(t("出纳付款证据"))}</summary>${payments}${attachments}</details></section>`;
	}
	function value(control) {
		const raw = control?.$input?.val?.();
		if (!control?.dlpNumeric) return control?.get_value?.() ?? "";
		if (raw === "") return "";
		if (control.dlpManualValue !== undefined) return control.dlpManualValue;
		if (raw === control.dlpInitialDisplay) return control.dlpOriginalValue;
		return /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(String(raw).trim()) ? String(raw).trim() : String(control.get_value());
	}
	async function open(name) {
		if (!name) return;
		const frappe = root.frappe, $ = root.$;
		const shellReady = () => ["createDrawer", "formatMoney", "formatQuantity", "formatNumericInput"].every(key => typeof root.DeepLinkERPPurchasePayments?.[key] === "function");
		if (!shellReady()) await frappe.require("/assets/deeplinkerp_branding/js/purchase_payments.js");
		const shell = root.DeepLinkERPPurchasePayments;
		if (!shellReady()) { frappe.msgprint(t("采购抽屉组件版本不完整，请整页刷新后重试。")); return; }
		const drawer = shell.createDrawer(t("完善/关联采购订单"), true); if (!drawer) return;
		drawer.panel.find(".dlp-payment-body").html(`<p class="dlp-source-loading" role="status">${esc(t("正在读取采购来源…"))}</p><div class="dlp-error text-danger" role="alert"></div>`);
		let detail, rows = [], paymentRows = [], controls = new Map(), saved = null, confirming = false, uncertain = false, freshRead = false, rulePreview = null, ruleConfirmButton;
		const eligible = () => Boolean(detail?.source?.eligible && detail.can_write && !detail.purchase_order);
		const canManageRules = () => eligible() && detail.can_manage_rules;
		const reviewable = () => Boolean(detail?.source?.eligible && detail.can_write && detail.purchase_order);
		const canReview = () => reviewable() && freshRead;
		const canReconcile = () => Boolean(detail?.source?.eligible && detail.can_reconcile && detail.purchase_order && detail.cashier?.payment_evidence_status === "recorded" && detail.cashier.paid_amount != null && Number(detail.cashier.paid_amount) > 0);
		const capture = () => ({ header: Object.fromEntries([...controls].filter(([field]) => !/_\d+$/.test(field)).map(([field, control]) => [field, value(control)])), items: rows.map((_, index) => Object.fromEntries(["item_code", "qty", "uom", "rate"].map(field => [field, value(controls.get(`${field}_${index}`))]))), paymentEntries: paymentRows.map((_, index) => value(controls.get(`payment_entry_${index}`))) });
		async function input(holder, fieldname, nativeType, nativeField, options, initial) {
			if (!drawer.alive()) return;
			// Dialog-only explanation is not a Purchase Order field or form write.
			const native = nativeType ? frappe.meta.get_docfield(nativeType, nativeField) : { fieldtype: options.fieldtype };
			if (!native) throw new Error(t("采购订单字段元数据不完整，请核对安装状态后刷新。"));
			const { label, fieldtype, ...presentation } = options;
			const overrides = Object.fromEntries(Object.entries(presentation).filter(([, option]) => option !== undefined));
			const numeric = ["Float", "Currency"].includes(native.fieldtype);
			const control = frappe.ui.form.make_control({ parent: holder, df: { ...native, ...overrides, fieldname, label: t(label), fieldtype: numeric ? native.fieldtype : fieldtype, only_select: 1, hidden: 0, read_only: 0, ignore_user_permissions: 0, depends_on: null, read_only_depends_on: null, mandatory_depends_on: null, fetch_from: null, default: null }, render_input: true });
			drawer.controls.push(control); controls.set(fieldname, control);
			control.$input?.attr("aria-label", t(label));
			control.get_doc = () => ({ currency: controls.get("currency")?.get_value() || "" });
			if (numeric) shell.formatNumericInput(control);
			await control.set_value(initial ?? "");
			if (!drawer.alive()) { shell.disposeControls(drawer.controls); return; }
			if (initial === undefined || initial === null || initial === "") control.$input?.val("");
			control.dlpNumeric = numeric; control.dlpOriginalValue = initial ?? ""; control.dlpInitialDisplay = control.$input?.val?.();
			if (numeric) {
				const node = control.$input?.get?.(0);
				const touched = () => { if (drawer.alive()) { const raw = control.$input.val(); control.dlpManualValue = raw === "" ? "" : /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(String(raw).trim()) ? String(raw).trim() : String(control.get_value()); } };
				if (node?.addEventListener) {
					for (const event of ["input", "change"]) node.addEventListener(event, touched, true);
					control.dlpDisposeInput = () => { for (const event of ["input", "change"]) node.removeEventListener(event, touched, true); };
				}
			}
			if (["company", "supplier", "rule_item_group", "rule_purchase_uom", "rule_service_item_code", "rule_service_item_name"].includes(fieldname)) {
				const node = control.$input?.get?.(0), changed = () => { rulePreview = null; ruleConfirmButton?.prop("disabled", true); };
				if (node?.addEventListener) {
					for (const event of ["input", "change"]) node.addEventListener(event, changed, true);
					control.dlpDisposeInput = () => { for (const event of ["input", "change"]) node.removeEventListener(event, changed, true); };
				}
			}
			return control;
		}
		async function render() {
			if (!drawer.alive()) return;
			shell.disposeControls(drawer.controls); controls = new Map();
			rulePreview = null; ruleConfirmButton = null;
			drawer.panel.find("footer .dlp-source-create,footer .dlp-source-associate,footer .dlp-source-reload,footer .dlp-source-reconcile,footer .dlp-source-review").remove();
			const notice = detail.purchase_order ? `${esc(t("已关联采购订单"))} ${poLink(detail.purchase_order)}` : !detail.source.eligible ? esc(t("来源未满足当前审批条件，只可查看证据。")) : !detail.can_write ? esc(t("当前没有办理权限，或公司归属待核对，请核对系统提示。")) : !detail.can_create ? esc(t("当前没有新建采购订单权限，可以核对并关联已有订单。")) : esc(t("请确认已有公司、供应商和物料，再创建采购订单草稿。"));
			const review = canReview() ? `<section class="dlp-source-review-fields"><h4>${esc(t("复核现有关联"))}</h4><p>${esc(t("当前采购订单"))}：${poLink(detail.purchase_order)} · ${esc(t("仅复核，不替换关联或创建订单。"))}</p></section>` : detail.purchase_order ? `<p class="text-muted">${esc(t(!detail.source.eligible ? "来源审批未满足办理条件，只可查看证据。" : !detail.can_write ? "当前没有办理权限，只可查看证据。" : "请先刷新来源，核对当前证据后再复核现有关联。"))}</p>` : "";
			drawer.panel.find(".dlp-payment-body").html(`${evidenceHTML(detail)}<p class="text-warning">${notice}</p>${detail.reconciliation_notice ? `<p class="text-muted">${esc(detail.reconciliation_notice)}</p>` : ""}${review}${canReconcile() ? `<section class="dlp-source-reconciliation"><h4>${t("核对历史付款")}</h4><p>${t("选择已提交、已分配到本订单的原生付款单。核对仅保存对应关系。")}</p><div class="dlp-source-payment-entries"></div></section>` : ""}${eligible() ? `<section class="dlp-source-mapping"><h4>${t("手工完善")}</h4><div class="dlp-document-fields"></div><div class="dlp-source-items"></div></section>` : ""}<div class="dlp-error text-danger" role="alert"></div>`);
			$(`<button type='button' class='btn btn-default dlp-source-reload'>${t("刷新来源")}</button>`).appendTo(drawer.panel.find("footer")).on("click.dlpSource", () => load(true));
			if (canReview()) {
				await input($("<div></div>").appendTo(drawer.panel.find(".dlp-source-review-fields")), "correction_reason", null, null, { fieldtype: "Small Text", label: t("完善/差异说明"), reqd: 0 }, saved?.header?.correction_reason);
				if (!drawer.alive()) return;
				$(`<button type='button' class='btn btn-primary dlp-source-review'>${t("复核现有关联")}</button>`).appendTo(drawer.panel.find("footer")).on("click.dlpSource", () => confirm("review"));
			}
			if (canReconcile()) await renderReconciliation();
			if (!eligible()) return;
			try { await frappe.model.with_doctype("Purchase Order"); if (!drawer.alive()) return; await frappe.model.with_doctype("Purchase Order Item"); }
			catch (_) { throw new Error(t("采购订单字段元数据读取失败，请核对安装状态和权限后刷新。")); }
			if (!drawer.alive()) return;
			const initial = saved?.header || { company: detail.adaptation?.company || detail.target_company || "", supplier: detail.adaptation?.supplier || "", currency: detail.adaptation?.currency || detail.source.currency || "", schedule_date: detail.adaptation?.schedule_date || (/^\d{4}-\d{2}-\d{2}$/.test(detail.source.schedule_date || "") ? detail.source.schedule_date : "") };
			for (const [field, fieldtype, options, label] of [["company", "Link", "Company", "公司"], ["supplier", "Link", "Supplier", "已有供应商"], ["currency", "Link", "Currency", "币种"], ["schedule_date", "Date", null, "需求日期"], ["correction_reason", "Small Text", null, "完善/差异说明"], ["purchase_order", "Link", "Purchase Order", "关联已有采购订单"]]) {
				if (!drawer.alive()) return;
				await input($("<div></div>").appendTo(drawer.panel.find(".dlp-document-fields")), field, field === "correction_reason" ? null : "Purchase Order", field === "purchase_order" ? "amended_from" : field, { fieldtype, options, label, reqd: ["company", "supplier", "currency", "schedule_date"].includes(field) ? 1 : 0 }, initial[field]);
			}
			if (!drawer.alive()) return;
			rows = saved?.items || (detail.adaptation?.items?.length ? detail.adaptation.items.map(item => ({ item_code: item.item_code, qty: item.qty, uom: item.uom, rate: item.rate })) : detail.source.items?.length ? detail.source.items.map(item => ({ item_code: item.item_code, qty: item.qty, uom: item.uom, rate: null })) : [{}]);
			await renderItems(); if (!drawer.alive()) return;
			if (canManageRules()) await renderRules(); if (!drawer.alive()) return;
			if (detail.can_create) $(`<button type='button' class='btn btn-primary dlp-source-create'>${t("创建采购订单草稿")}</button>`).appendTo(drawer.panel.find("footer")).on("click.dlpSource", () => confirm("create"));
			$(`<button type='button' class='btn btn-default dlp-source-associate'>${t("确认关联已有订单")}</button>`).appendTo(drawer.panel.find("footer")).on("click.dlpSource", () => confirm("associate"));
		}
		async function renderReconciliation() {
			try { await frappe.model.with_doctype("Payment Entry"); } catch (_) { throw new Error(t("付款单字段元数据读取失败，请核对安装状态和权限。")); }
			if (!drawer.alive()) return;
			paymentRows = saved?.paymentEntries?.length ? saved.paymentEntries : [""];
			const holder = drawer.panel.find(".dlp-source-payment-entries");
			for (const [index, name] of paymentRows.entries()) {
				if (!drawer.alive()) return;
				const control = await input($("<div></div>").appendTo(holder), `payment_entry_${index}`, "Payment Entry", "amended_from", { fieldtype: "Link", options: "Payment Entry", label: t("原生付款单") }, name);
				if (control) control.get_query = () => ({ filters: { docstatus: 1, payment_type: "Pay", ...(detail.target_company ? { company: detail.target_company } : {}) } });
			}
			$(`<button type='button' class='btn btn-default dlp-source-add-payment'>${t("添加付款单")}</button>`).appendTo(holder).on("click.dlpSource", async () => {
				if (drawer.busy || confirming || !drawer.alive()) return;
				saved = capture(); saved.paymentEntries.push(""); drawer.setBusy(true);
				try { await render(); } catch (error) { if (drawer.alive()) drawer.error(error); } finally { if (drawer.alive()) drawer.setBusy(false); }
			});
			$(`<button type='button' class='btn btn-primary dlp-source-reconcile'>${t("确认历史付款核对")}</button>`).appendTo(drawer.panel.find("footer")).on("click.dlpSource", () => confirm("reconcile"));
		}
		async function renderItems() {
			const columns = [["item_code", "已有物料", "Link", "Item"], ["qty", "数量", "Float"], ["uom", "单位", "Link", "UOM"], ["rate", "单价", "Currency"]];
			drawer.panel.find(".dlp-source-items").html(`<div class="dlp-document-items"><table class="table table-bordered"><thead><tr>${columns.map(([, label]) => `<th>${esc(t(label))}</th>`).join("")}<th></th></tr></thead><tbody>${rows.map((_, index) => `<tr>${columns.map(([field]) => `<td data-source-field="${field}_${index}"></td>`).join("")}<td><button type="button" class="btn btn-link dlp-source-remove-item" data-index="${index}">${esc(t("移除"))}</button></td></tr>`).join("")}</tbody></table></div><button type="button" class="btn btn-default dlp-source-add-item">${esc(t("添加物料"))}</button><p class="text-muted">${esc(t("数量和单价须明确填写；缺失值保持空白。"))}</p>`);
			for (const [index, row] of rows.entries()) for (const [field, label, fieldtype, options] of columns) {
				if (!drawer.alive()) return;
				const proposed = field === "item_code" && detail.adaptation?.new_items?.some(item => item.item_code === row.item_code);
				await input(drawer.panel.find(`[data-source-field="${field}_${index}"]`), `${field}_${index}`, proposed ? null : "Purchase Order Item", field, { fieldtype: proposed ? "Data" : fieldtype, options: proposed ? undefined : options, label: proposed ? t("拟新增物料编码") : label, reqd: 1 }, row[field]);
			}
			const redraw = async change => {
				if (drawer.busy || confirming || !drawer.alive()) return;
				saved = capture(); change(saved.items); drawer.setBusy(true);
				try { await render(); } catch (error) { if (drawer.alive()) drawer.error(error); } finally { if (drawer.alive()) drawer.setBusy(false); }
			};
			drawer.panel.find(".dlp-source-add-item").on("click.dlpSource", () => redraw(items => items.push({})));
			drawer.panel.find(".dlp-source-remove-item").on("click.dlpSource", event => redraw(items => items.splice(Number(event.currentTarget.dataset.index), 1)));
		}
		const ruleValues = () => Object.fromEntries(["company", "supplier", "item_group", "purchase_uom", "service_item_code", "service_item_name"].map(field => [field, String(value(controls.get(["company", "supplier"].includes(field) ? field : `rule_${field}`))).trim()]));
		const refreshRuleConfirmation = () => ruleConfirmButton?.prop("disabled", Boolean(drawer.busy || uncertain || !rulePreview || blockers(rulePreview.plan).length || rulePreview.serialized !== JSON.stringify(ruleValues())));
		async function renderRules() {
			$(`<section class='dlp-source-rules'><h4>${t("同类自动接入规则")}</h4><p>${t("确认精确来源范围和采购映射后，由现有定时任务接入采购订单草稿。")}</p><div class='dlp-source-rule-fields'></div><div class='dlp-source-rule-preview-result' aria-live='polite'></div></section>`).appendTo(drawer.panel.find(".dlp-source-mapping"));
			const rule = detail.adaptation?.rule || {};
			for (const [field, fieldtype, options, label, fallback] of [["item_group", "Link", "Item Group", "规则物料组", ""], ["purchase_uom", "Link", "UOM", "规则采购单位", ""], ["service_item_code", "Data", null, "加工服务物料编码", "DLP-SVC-PROCESSING"], ["service_item_name", "Data", null, "加工服务物料名称", "加工服务"]]) {
				if (!drawer.alive()) return;
				await input($("<div></div>").appendTo(drawer.panel.find(".dlp-source-rule-fields")), `rule_${field}`, null, null, { fieldtype, options, label, reqd: 1 }, saved?.header?.[`rule_${field}`] ?? rule[field] ?? fallback);
			}
			if (!drawer.alive()) return;
			$(`<button type='button' class='btn btn-default dlp-source-rule-preview'>${t("预览自动接入规则")}</button>`).appendTo(drawer.panel.find(".dlp-source-rules")).on("click.dlpSource", previewRule);
		}
		async function previewRule() {
			if (!drawer.alive() || drawer.busy || confirming || uncertain || !canManageRules()) return;
			rulePreview = null; ruleConfirmButton?.prop("disabled", true);
			const rule = ruleValues(), serialized = JSON.stringify(rule);
			if (Object.values(rule).some(item => !item)) { drawer.error(new Error(t("请填写公司、供应商、规则物料组、采购单位和加工服务物料编码、名称，再预览。"))); return; }
			const generation = drawer.loadId; drawer.setBusy(true);
			try {
				const plan = await call("preview_purchase_source_adaptation", { name: detail.name, rule: serialized }, { type: "GET" });
				if (!drawer.alive() || generation !== drawer.loadId || !canManageRules()) return;
				if (serialized !== JSON.stringify(ruleValues())) throw new Error(t("规则已变化，请重新预览。"));
				if (!plan?.preview_fingerprint || !Array.isArray(plan.items) || !plan.scope) throw new Error(t("规则预览不完整，请重新预览核对。"));
				drawer.panel.find(".dlp-source-rule-preview-result").html(`${adaptationHTML(plan)}${blockers(plan).map(reason => `<p class="text-warning">${esc(reason)}</p>`).join("")}`);
				ruleConfirmButton?.remove();
				rulePreview = { plan, serialized };
				ruleConfirmButton = $(`<button type='button' class='btn btn-primary dlp-source-rule-confirm'>${t("确认同类规则")}</button>`).appendTo(drawer.panel.find(".dlp-source-rules")).prop("disabled", Boolean(blockers(plan).length)).on("click.dlpSource", confirmRule);
			} catch (error) { if (drawer.alive() && generation === drawer.loadId) drawer.error(error); }
			finally { if (drawer.alive() && generation === drawer.loadId) { drawer.setBusy(false); refreshRuleConfirmation(); } }
		}
		function confirmRule() {
			if (!drawer.alive() || drawer.busy || confirming || uncertain || !canManageRules()) return;
			const selected = rulePreview;
			if (!selected || selected.serialized !== JSON.stringify(ruleValues())) { drawer.error(new Error(t("规则已变化，请重新预览。"))); return; }
			if (blockers(selected.plan).length) return;
			const rule = JSON.parse(selected.serialized), scope = selected.plan.scope;
			const scopeLabels = { corp_id: "钉钉企业", process_code: "审批流程", execution_region: "执行地区", source_organization: "来源组织", purchase_type: "采购类型" };
			const summary = Object.entries(scopeLabels).map(([field, label]) => `<p>${esc(t(label))}：${text(field === "purchase_type" ? ({ processing: "加工采购", goods: "实物采购", mixed: "实物及加工采购", unknown: "用途待确认" })[scope[field]] || scope[field] : scope[field])}</p>`).join("");
			const newItems = newItemsHTML(selected.plan.new_items) || `<p>${esc(t("无"))}</p>`;
			confirming = true;
			frappe.confirm(`<p>${esc(t("确认保存同类采购映射规则？"))}</p>${summary}<p>${esc(t("公司"))}：${esc(rule.company)} · ${esc(t("供应商"))}：${esc(rule.supplier)}</p><p>${esc(t("物料组"))}：${esc(rule.item_group)} · ${esc(t("采购单位"))}：${esc(rule.purchase_uom)}</p><p>${esc(t("加工服务物料"))}：${esc(rule.service_item_code)} · ${esc(rule.service_item_name)}</p><p>${esc(t("拟新增物料"))}：</p>${newItems}<p>${esc(t("规则仅限2026年9月25日至12月31日同一精确范围的同类来源，由现有定时任务自动生成采购订单草稿。"))}</p>`, async () => {
				confirming = false;
				if (!drawer.alive() || drawer.busy || uncertain || !canManageRules()) return;
				if (rulePreview !== selected || selected.serialized !== JSON.stringify(ruleValues())) { drawer.error(new Error(t("规则已变化，请重新预览。"))); return; }
				drawer.setBusy(true);
				try {
					const result = await call("confirm_purchase_source_rule", { name: detail.name, rule: selected.serialized, expected_preview_fingerprint: selected.plan.preview_fingerprint }, { type: "POST" });
					if (!drawer.alive()) return;
					if (!result?.name || !result.rule_version) throw new Error(t("规则保存结果未确认，请刷新来源核对。"));
					frappe.show_alert({ message: t("采购映射规则已确认"), indicator: "green" }); drawer.setBusy(false); await load(true);
				} catch (error) { if (drawer.alive()) { uncertain = true; drawer.error(error); } }
				finally { if (drawer.alive()) { drawer.setBusy(false); refreshRuleConfirmation(); } }
			}, () => { confirming = false; });
		}
		function confirm(action) {
			const permitted = () => action === "reconcile" ? canReconcile() : action === "review" ? canReview() : eligible();
			if (!drawer.alive() || drawer.busy || confirming || uncertain || !permitted()) return;
			const data = capture(), header = data.header, items = data.items;
			if (action === "create" && (!detail.can_create || !["company", "supplier", "currency", "schedule_date"].every(field => header[field]) || !items.length || items.some(item => !item.item_code || !item.uom || item.qty === "" || item.rate === "" || !Number.isFinite(Number(item.qty)) || Number(item.qty) <= 0 || !Number.isFinite(Number(item.rate)) || Number(item.rate) < 0))) { drawer.error(new Error(t("请填写公司、已有供应商、币种、需求日期，以及每行物料、正数数量、单位和明确单价。"))); return; }
			if (action === "associate" && !header.purchase_order) { drawer.error(new Error(t("请选择要关联的已有采购订单。"))); return; }
			const paymentEntries = data.paymentEntries.filter(Boolean);
			if (action === "reconcile" && !paymentEntries.length) { drawer.error(new Error(t("请明确选择历史原生付款单。"))); return; }
			const args = { name: detail.name, expected_version: action === "create" ? detail.creation_version || detail.version : action === "review" ? detail.recheck_version || detail.version : detail.version, ...(action === "reconcile" ? { payment_entries: JSON.stringify(paymentEntries) } : { correction_reason: header.correction_reason || "", ...(action === "create" ? { company: header.company, supplier: header.supplier, currency: header.currency, schedule_date: header.schedule_date, items: JSON.stringify(items) } : { purchase_order: action === "review" ? detail.purchase_order : header.purchase_order }) }) };
			const summary = action === "create" ? `<p>${esc(header.company)} · ${esc(header.supplier)} · ${esc(header.currency)} · ${esc(header.schedule_date)}</p>${items.map(item => `<p>${esc(item.item_code)} · ${quantity(item.qty)} ${esc(item.uom)} × ${money(item.rate, header.currency)}</p>`).join("")}` : ["associate", "review"].includes(action) ? `<p>${esc(args.purchase_order)}</p><p>${esc(header.correction_reason || "")}</p>${detail.scope_review ? `<p>${esc(t("原采用范围"))}：${readable(detail.scope_review.adopted_scope)}</p><p>${esc(t("本次核对范围"))}：${readable(detail.scope_review.current_scope)}</p>` : ""}` : `<p>${paymentEntries.map(esc).join(" · ")}</p>`;
			confirming = true;
			frappe.confirm(t("{0}{1}", [esc(t(action === "create" ? "确认创建采购订单草稿？创建后请在订单核对。" : action === "associate" ? "确认将此来源关联到所选采购订单？" : action === "review" ? "确认复核现有关联？不创建或替换采购订单。" : "确认核对历史付款并保存对应关系？")), summary]), async () => {
				confirming = false;
				if (!drawer.alive() || drawer.busy || uncertain || !permitted()) return;
				drawer.setBusy(true);
				try {
					const result = await call({ create: "create_purchase_order_from_source", associate: "associate_purchase_order", review: "associate_purchase_order", reconcile: "confirm_historical_payments" }[action], args, { type: "POST" });
					if (!drawer.alive()) return;
					if (action === "reconcile") { frappe.show_alert({ message: t("历史付款已核对"), indicator: "green" }); drawer.setBusy(false); await load(); return; }
					if (!result?.name || result.doctype !== "Purchase Order") throw new Error(t("操作结果未确认，请刷新来源核对。"));
					frappe.show_alert({ message: t(action === "create" ? "采购订单草稿已创建" : action === "review" ? "现有关联已复核" : "采购来源已关联"), indicator: "green" });
					drawer.close(true); shell.openNative("Purchase Order", result.name);
				} catch (error) { if (drawer.alive()) { uncertain = true; drawer.error(new Error(t("{0}；{1}", [error.message || t("操作未确认"), t("请先刷新来源或核对采购订单，再继续办理。")]))); } }
				finally { if (drawer.alive()) drawer.setBusy(false); }
			}, () => { confirming = false; });
		}
		async function load(fresh = false) {
			if (!drawer.alive() || drawer.busy || confirming) return;
			if (controls.size) saved = capture();
			const generation = ++drawer.loadId; uncertain = true; freshRead = false; rulePreview = null; ruleConfirmButton?.prop("disabled", true); drawer.setBusy(true);
			try {
				const result = await call("get_purchase_source_detail", { name, ...(fresh ? { fresh: 1 } : {}) });
				if (!drawer.alive() || generation !== drawer.loadId) return;
				if (!result?.source || !result.name || !result.version) throw new Error(t("采购来源详情不完整，请核对同步和权限后刷新。"));
				detail = result; freshRead = fresh; uncertain = false; await render();
			} catch (error) { if (drawer.alive()) { uncertain = true; drawer.panel.find(".dlp-source-loading").text(t("采购来源读取未完成，请核对同步状态或权限。")); drawer.error(new Error(error.message || t("采购来源不可用或无权查看，请核对系统提示。"))); } }
			finally { if (drawer.alive() && generation === drawer.loadId) { drawer.setBusy(false); refreshRuleConfirmation(); } }
		}
		await load(); return drawer;
	}
	if (root.document) root.document.addEventListener("click", event => {
		const button = event.target.closest?.("[data-purchase-source]"); if (!button) return;
		event.preventDefault(); event.stopPropagation();
		open(button.dataset.purchaseSource).catch(error => root.frappe.msgprint({ message: esc(error.message || t("采购来源读取失败，请刷新重试。")), indicator: "red" }));
	}, true);
	return { open, sync };
});
