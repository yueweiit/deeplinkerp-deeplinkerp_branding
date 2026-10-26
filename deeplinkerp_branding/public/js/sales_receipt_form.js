(function (root, factory) {
	const api = factory(root);
	if (typeof module === "object" && module.exports) module.exports = api;
	root.DeepLinkERPSalesReceipts = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
	"use strict";
	const API = "deeplinkerp_branding.services.sales_receipt_service.";
	const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
	const t = (value, args = []) => String((root.__ || (text => text))(value)).replace(/\{(\d+)\}/g, (_, index) => args[index] ?? "");
	const fields = [
		["amount", "received_amount", "Currency", "收款金额"],
		["posting_date", "posting_date", "Date", "收款日期"],
		["bank_account", "paid_to", "Link", "收款账户"],
		["mode_of_payment", "mode_of_payment", "Link", "收付款方式"],
		["reference_no", "reference_no", "Data", "银行流水号"],
		["reference_date", "reference_date", "Date", "银行流水日期"],
		["remarks", "remarks", "Small Text", "收款备注"],
	];
	function saveArguments(order, projection, values, newReceipt, attachments = {}) {
		if (!Number.isFinite(Number(values.amount)) || Number(values.amount) <= 0) throw new Error(t("收款金额必须是大于 0 的有效数字。"));
		if (!values.posting_date || !values.bank_account) throw new Error(t("请填写收款日期和收款账户。"));
		return { sales_order: order, values, payment_entry: projection.document.name || null,
			expected_modified: projection.document.modified || null, order_modified: projection.order_modified,
			new_receipt: newReceipt ? 1 : 0, ...attachments };
	}
	function choicesHTML(drafts) {
		return `<p>${esc(t("发现已有收款草稿，请选择继续编辑；另一笔真实收款请另行新增。"))}</p>` + drafts.map(row =>
			`<div class="dlp-sales-draft-choice"><span>${esc(row.name)} · ${esc(row.posting_date || "")} · ${esc(row.amount)} ${esc(row.currency)}</span><button type="button" class="btn btn-default btn-sm dlp-receipt-choose" data-name="${esc(row.name)}">${esc(t("继续编辑草稿"))}</button></div>`).join("");
	}
	function mount({ dialog, salesOrder, onSaved, openAdvanced }) {
		const $ = root.$, shared = root.DeepLinkERPPurchasePayments;
		const panel = dialog.$wrapper.find(".dlp-sales-receipt-editor");
		let alive = true, generation = 0, projection = null, newReceipt = false, dirty = false, closing = false;
		let inputs = {}, initialValues = {}, retry = shared.retryToken(shared.requestId,
			`dlp-sales-receipt:${root.frappe.boot?.sitename || root.location?.host}:${root.frappe.session?.user}:${salesOrder}`);
		const submitRetry = shared.retryToken(shared.requestId,
			`dlp-sales-receipt-submit:${root.frappe.boot?.sitename || root.location?.host}:${root.frappe.session?.user}:${salesOrder}`);
		const pending = () => Boolean(retry.pending() || submitRetry.pending());
		const call = async (method, args) => (await root.frappe.call({ method: API + method, args, silent: true })).message;
		const ask = message => new Promise(resolve => root.frappe.confirm(message, () => resolve(true), () => resolve(false)));
		const drawer = {
			panel, controls: [], busy: false,
			alive: () => alive,
			pendingPayment: pending,
			error(error) { panel.find(".dlp-receipt-error").text(error.message || t("收款操作失败，请核对后重试。")); },
			setBusy(value, operation = "write") {
				this.busy = value; this.busyOperation = value ? operation : null;
				panel.attr("aria-busy", String(value));
				panel.find("button,input,select,textarea").prop("disabled", value);
				if (!value) for (const [key, control] of Object.entries(inputs)) {
					control.df.read_only = pending() || !(projection?.editable_fields || []).includes(key);
					control.refresh();
				}
				panel.find(".dlp-receipt-another,.dlp-receipt-advanced,.dlp-receipt-choose").prop("disabled", value || pending());
				panel.find(".dlp-create").text(t(retry.pending() ? "重试保存" : "保存收款草稿"));
				this.attachments?.refresh();
				if (submitRetry.pending()) panel.find(".dlp-create").prop("disabled", true);
					panel.find(".dlp-receipt-submit").prop("disabled", value || Boolean(retry.pending()) || this.attachments?.ready() === false)
					.text(t(submitRetry.pending() ? "重试提交" : "提交收款"));
			},
		};
		function hasChanges() {
			return dirty || Object.entries(inputs).some(([key, control]) => String(control.get_value() ?? "") !== initialValues[key])
				|| Boolean(drawer.attachments?.snapshot().files.length) || drawer.attachments?.ready() === false;
		}
		function dispose() {
			drawer.attachments?.stop(); drawer.attachments = null; drawer.beforeClose = null;
			shared.disposeControls(drawer.controls); inputs = {}; initialValues = {};
		}
		async function leave() {
			if (drawer.busy && drawer.busyOperation !== "read") return false;
			if ((hasChanges() || pending()) && !(await ask(t(pending()
				? "本次保存结果尚未确认。关闭后请先核对草稿，勿重复录入同一笔收款。是否关闭？"
				: "尚有未保存的收款资料或附件，是否放弃本次编辑？")))) return false;
			if (drawer.beforeClose && !(await drawer.beforeClose())) return false;
			dispose(); dirty = false; generation++; panel.prop("hidden", true); return true;
		}
		async function advanced() {
			if (pending()) return;
			const name = projection?.document?.name;
			if (!(await leave())) return;
			await openAdvanced(name);
		}
		async function render(result, restored = null) {
			dispose(); projection = result; dirty = false;
			const doc = result.document;
			panel.html(`<div class="dlp-sales-receipt-title"><strong>${esc(doc?.name ? t(doc.docstatus === 1 ? "已提交收款" : "编辑收款草稿") + " · " + doc.name : t("新增收款"))}</strong><button type="button" class="btn btn-default btn-xs dlp-receipt-cancel">${esc(t("收起"))}</button></div><p class="dlp-receipt-error text-danger" role="alert"></p>`);
			panel.find(".dlp-receipt-cancel").on("click", leave);
			if (!doc) {
				$(choicesHTML(result.drafts || [])).appendTo(panel);
				panel.find(".dlp-receipt-choose").on("click", event => open(event.currentTarget.dataset.name));
				$(`<button type="button" class="btn btn-default dlp-receipt-another">${esc(t("另记一笔收款"))}</button>`).appendTo(panel).on("click", () => open(null, true));
				if (retry.pending()) retryButton(panel);
				if (submitRetry.pending()) submitButton(panel);
				return;
			}
			$(`<p class="text-muted">${esc(doc.company)} · ${esc(doc.customer)} · ${esc(doc.currency)}<br>${esc(t(doc.docstatus === 1
				? "收款已提交，相关记账和订单预付款汇总以 ERP 记录为准。"
				: "草稿不代表已到账；保存后可由有权限的财务人员提交。"))}</p>`).appendTo(panel);
			const editable = doc.docstatus === 0 && !result.advanced_reason && ["amount", "posting_date", "bank_account"].every(key => result.editable_fields.includes(key));
			if (doc.docstatus === 0 && (result.advanced_reason || !editable)) $(`<p class="text-warning">${esc(result.advanced_reason || t("当前单据不可在订单内编辑，请打开完整付款单。"))}</p>`).appendTo(panel);
			if (editable) {
				const area = $('<div class="dlp-sales-receipt-fields"></div>').appendTo(panel);
				for (const [key, native, type, label] of fields) {
					inputs[key] = await shared.drawerInput(drawer, $('<div></div>').appendTo(area), {
						native_doctype: "Payment Entry", native_fieldname: native, fieldname: key, fieldtype: type,
						currency_context: { ...doc, paid_to_account_currency: doc.currency }, label: t(label),
						options: key === "bank_account" ? "Account" : key === "mode_of_payment" ? "Mode of Payment" : undefined,
						read_only: !result.editable_fields.includes(key),
					}, doc[key], () => { dirty = true; });
					if (!alive) return;
				}
				inputs.bank_account.get_query = () => ({ filters: { company: doc.company, is_group: 0, disabled: 0,
					account_type: ["in", ["Bank", "Cash"]], account_currency: doc.currency } });
				initialValues = Object.fromEntries(Object.entries(inputs).map(([key, control]) => [key, String(control.get_value() ?? "")]));
				$('<section class="dlp-attachments"></section>').appendTo(panel);
				await shared.mountAttachments(drawer, doc.name ? "Payment Entry" : "Sales Order", doc.name || salesOrder, restored);
				panel.find(".dlp-attachment-title strong").text(t("收款凭证"));
			}
			const existing = (result.attachments || []).filter(file => /^\/(private\/)?files\//.test(file.file_url));
			if (existing.length) $(`<div class="dlp-sales-receipt-existing"><strong>${esc(t("已保存的凭证"))}</strong>${existing.map(file => `<p><a href="${esc(file.file_url)}" target="_blank" rel="noopener">${esc(file.file_name)}</a></p>`).join("")}</div>`).appendTo(panel);
			const footer = $('<div class="dlp-sales-receipt-actions"></div>').appendTo(panel);
			if (editable && !submitRetry.pending() || retry.pending()) retryButton(footer);
			if (result.can_submit || submitRetry.pending()) submitButton(footer);
			$(`<button type="button" class="btn btn-default dlp-receipt-advanced">${esc(t("打开完整付款单"))}</button>`).appendTo(footer).on("click", advanced);
			if ((result.drafts || []).length || doc.name) $(`<button type="button" class="btn btn-default dlp-receipt-another">${esc(t("另记一笔收款"))}</button>`).appendTo(footer).on("click", () => open(null, true));
		}
		function retryButton(holder) {
			$(`<button type="button" class="btn btn-primary dlp-create">${esc(t(retry.pending() ? "重试保存" : "保存收款草稿"))}</button>`).appendTo(holder).on("click", save);
		}
		function submitButton(holder) {
			$(`<button type="button" class="btn btn-primary dlp-receipt-submit">${esc(t(submitRetry.pending() ? "重试提交" : "提交收款"))}</button>`).appendTo(holder).on("click", submit);
		}
		async function open(paymentEntry = null, forceNew = false) {
			if (!alive || drawer.busy) return;
			if (!panel.prop("hidden") && !(await leave())) return;
			if (submitRetry.pending()) { paymentEntry = submitRetry.pending().payment_entry; forceNew = false; }
			if (forceNew && !(await ask(t("确认这是另一笔真实收款，需要新增独立草稿？")))) return;
			newReceipt = forceNew;
			projection = null;
			const current = ++generation;
			panel.prop("hidden", false).html(`<p>${esc(t("正在读取收款资料…"))}</p>`);
			drawer.setBusy(true, "read");
			try {
				const result = await call("get_receipt_context", { sales_order: salesOrder, payment_entry: paymentEntry, new_receipt: forceNew ? 1 : 0 });
				await shared.loadMetadata("Payment Entry");
				if (!alive || current !== generation) return;
				await render(result);
				if (retry.pending()) drawer.error(new Error(t("上次保存结果未确认，请先重试原请求或核对已有草稿。")));
				if (submitRetry.pending()) drawer.error(new Error(t("上次提交结果未确认，请先重试原请求，勿重复提交。")));
				return true;
			} catch (error) {
				if (!alive || current !== generation) return;
				panel.html(`<p class="dlp-receipt-error text-danger" role="alert"></p><button type="button" class="btn btn-default dlp-receipt-advanced">${esc(t("打开完整付款单"))}</button>`);
				drawer.error(error); panel.find(".dlp-receipt-advanced").on("click", advanced);
				if (retry.pending()) retryButton(panel);
				if (submitRetry.pending()) submitButton(panel);
			} finally { if (alive && current === generation) drawer.setBusy(false); }
		}
		async function save() {
			if (!alive || drawer.busy || submitRetry.pending() || (!projection?.document && !retry.pending())) return;
			if (drawer.attachments && !drawer.attachments.ready()) { drawer.error(new Error(t("请等待附件上传完成，或移除失败文件。"))); return; }
			let args;
			try { args = retry.pending() || saveArguments(salesOrder, projection, Object.fromEntries(fields.map(([key]) => [key, inputs[key].get_value() || ""])), newReceipt, drawer.attachments?.args()); }
			catch (error) { drawer.error(error); return; }
			drawer.setBusy(true);
			let saved = false;
			try {
				const result = await call("save_receipt_draft", { ...args, request_id: retry.forPayload(args) });
				if (result.failed) { if (!result.requires_review) retry.succeeded(); throw new Error(result.error || t("收款草稿未保存，请核对后重试。")); }
				retry.succeeded(); saved = !result.needs_review;
				const restored = result.needs_review ? drawer.attachments?.snapshot() : null;
				if (saved) drawer.attachments?.committed();
				if (!alive) return;
				await render(result, restored);
				panel.find(".dlp-receipt-error").text("");
				dialog.$wrapper.find(".dlp-sales-receipt-notice").text(t(result.needs_review
					? "已有待编辑草稿，请核对后再保存；本次尚未修改该草稿。"
					: "收款草稿已保存，尚未计入已到账；可继续编辑或由有权限的财务人员提交。"));
				if (saved) await onSaved?.(result.document);
			} catch (error) { if (alive) drawer.error(new Error(saved ? t("草稿已保存，但页面刷新失败。请核对该草稿，勿重复新增。") : error.message || t("保存结果未确认，请重试原请求，勿重复新增。"))); }
			finally { if (alive) drawer.setBusy(false); }
		}
		async function submit() {
			if (!alive || drawer.busy) return;
			if (retry.pending() || hasChanges()) {
				drawer.error(new Error(t("请先保存或核对未确认的收款资料和附件，再提交草稿。"))); return;
			}
			let args = submitRetry.pending(), submitted = false;
			if (!args && !projection?.document?.name) return;
			drawer.setBusy(true);
			try {
				if (!args) {
					const current = await call("get_receipt_context", { sales_order: salesOrder, payment_entry: projection.document.name });
					if (!alive) return;
					await render(current);
					if (!current.can_submit) throw new Error(t("当前草稿不可在订单内提交，请打开完整付款单核对权限或审批设置。"));
					const doc = current.document;
					const amount = Number(doc.amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 9 });
					if (!(await ask(t("确认提交收款单 {0}，金额 {1} {2}？提交后将按原生规则记账，并更新订单预付款汇总。", [esc(doc.name), esc(amount), esc(doc.currency)])))) return;
					args = { sales_order: salesOrder, payment_entry: doc.name, expected_modified: doc.modified, order_modified: current.order_modified };
				}
				const result = await call("submit_receipt", { ...args, request_id: submitRetry.forPayload(args) });
				if (result.failed) {
					if (!result.requires_review) submitRetry.succeeded();
					throw new Error(result.error || t("收款未提交，请核对后重试。"));
				}
				if (result.document?.docstatus !== 1) throw new Error(t("提交结果未确认，请重试原请求，勿重复提交。"));
				submitRetry.succeeded(); submitted = true;
				if (!alive) return;
				await render(result);
				dialog.$wrapper.find(".dlp-sales-receipt-notice").text(t("收款已提交，已按原生规则记账并更新订单预付款汇总。"));
				await onSaved?.(result.document);
			} catch (error) {
				if (alive) drawer.error(new Error(submitted
					? t("收款已提交，但页面刷新失败。请核对该收款单，勿重复提交。")
					: error.message || t("提交结果未确认，请重试原请求，勿重复提交。")));
			} finally { if (alive) drawer.setBusy(false); }
		}
		const hide = async event => {
			if (closing || !alive) return;
			if (drawer.busy && drawer.busyOperation !== "read") { event.preventDefault(); return; }
			if (!panel.prop("hidden") && (hasChanges() || pending())) {
				event.preventDefault();
				if (await leave()) { closing = true; dialog.hide(); }
			}
		};
		const unload = event => { if (hasChanges() || drawer.busy && drawer.busyOperation === "write" || pending()) { event.preventDefault(); event.returnValue = ""; } };
		dialog.$wrapper.on("hide.bs.modal.dlpSalesReceipt", hide).on("hidden.bs.modal.dlpSalesReceipt", () => {
			alive = false; generation++; dispose(); root.removeEventListener?.("beforeunload", unload);
			dialog.$wrapper.off(".dlpSalesReceipt");
		});
		root.addEventListener?.("beforeunload", unload);
		return { open, save, submit, close: leave, drawer };
	}
	return { mount, saveArguments, choicesHTML };
});
