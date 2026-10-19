(function (root) {
	"use strict";
 const translateText = (text, args = []) => String((globalThis.__ || (value => value))(text)).replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");
	const route = "purchase-payables",
		t = root.__ || ((text) => text);
	const columns = [
		["posting_date", "应付日期", 120],
		["name", "应付单号", 180],
		["supplier", "供应商", 160],
		["company", "公司", 150],
		["grand_total", "应付原币金额", 160],
		["total", "应付金额", 150],
		["settled", "已付 / 核销", 150],
		["outstanding", "未付余额", 150],
		["status", "应付状态", 180],
		["orders", "采购订单", 200],
		["receipts", "采购入库", 200],
		["action", "办理", 240],
	].map(([fieldname, label, width]) => ({ fieldname, label, width }));
	const esc = root.DeepLinkERPCompactList.create({
		doctype: "Purchase Invoice",
		columns,
	}).escapeHTML;
	const money = (value, currency) =>
		value == null
			? "—"
			: `${esc(
					Number(value).toLocaleString(undefined, {
						minimumFractionDigits: 2,
						maximumFractionDigits: 2,
					})
			  )} ${esc(currency || "")}`;
	const api = (root.DeepLinkERPPurchasePayables = { controller: null });
	root.frappe.pages[route].on_page_load = (wrapper) => {
		const page = root.frappe.ui.make_app_page({
			parent: wrapper,
			title: t("采购应付"),
			single_column: true,
		});
		const actions = root.DeepLinkERPPurchasePayments;
		const provider = {
			columns,
			defaultColumns: ['name','supplier','posting_date','company','total','settled','outstanding','status','action'],
			preferenceVersion:4,
			migratePreferences:value=>value?.version===4?value:{...value,columns:provider.defaultColumns},
			freezeUntil: "supplier",
			sortFields: ['posting_date','name','supplier','company','grand_total','status'],
			summary: c => translateText("应付账户币种汇总：{0}", [(c.providerPayload?.totals || []).map(row=>translateText("应付 {0} · 已付 / 核销 {1} · 未付 {2}", [money(row.total,row.currency), money(row.settled,row.currency), money(row.outstanding,row.currency)])).join(' ｜ ') || '—']),
			request: (c) => ({
				method: "deeplinkerp_branding.services.purchase_payment_service.get_purchase_payables",
				args: { ...c.quick, order_by:c.providerOrderBy, start: c.page * c.pageSize, page_length: c.pageSize },
			}),
			exportCurrent: async c => {
				const args={...provider.request(c).args,export_format:'xlsx',columns:JSON.stringify(c.preferences.columns.filter(field=>field!=='action'))};
				delete args.start;delete args.page_length;
				if(!c.root.DeepLinkERPPurchaseOrderExport?.downloadWorkbook)await c.root.frappe.require('/assets/deeplinkerp_branding/js/purchase_order_export.js');
				const exporter=c.root.DeepLinkERPPurchaseOrderExport;
				if(!exporter)throw new Error(translateText("导出组件加载失败，请刷新。"));
				exporter.downloadWorkbook(c.root,await exporter.fetchNativeWorkbook(c.root,args,'deeplinkerp_branding.services.purchase_payment_service.get_purchase_payables'),'采购应付');
			},
			renderValue: (field, doc) => {
				if (field === "name")
					return actions.nativeAction("Purchase Invoice", doc.name, doc.name);
				if (field === "grand_total") return money(doc.grand_total, doc.invoice_currency);
				if (field === 'total') return money(doc.total,doc.currency);
				if (field === 'settled') return money(doc.settled,doc.currency);
				if (field === "outstanding") return money(doc.outstanding, doc.currency);
				if (field === "status")
					return `<div>${esc(
						t(
							doc.docstatus === 0
								? "草稿"
								: doc.docstatus === 2
								? "已取消"
								: doc.is_return
								? "退货应付"
								: doc.status
						)
					)}${
						doc.shared
							? `<div class="text-warning" title="${esc(
									t("共享 / 混合应付整单")
							  )}">${esc(t("共享 / 混合"))}</div>`
							: ""
					}</div>`;
				if (field === "orders" || field === "receipts")
					return doc[field]
						.map((name) =>
							actions.nativeAction(
								field === "orders" ? "Purchase Order" : "Purchase Receipt",
								name,
								name
							)
						)
						.join(" ");
				if (field === "action")
					return `${actions.nativeAction("Purchase Invoice", doc.name, translateText("查看应付单"))}${
						doc.can_pay
							? ` <button type="button" class="btn btn-default btn-xs dlp-payable-pay" data-name="${esc(
									doc.name
							  )}">${esc(t("付款 / 继续付款"))}</button>`
							: ""
					}`;
			},
			mountControls: (c) => {
				c.list.$result.on("click.dlpPayable", ".dlp-payable-pay", (event) => {
					const row = c.providerRows.find(
						(doc) => doc.name === event.currentTarget.dataset.name
					);
					if (row?.can_pay) actions.pay(row.source_type, row.source_name, row.name);
				});
			},
			onPayload: (c) => {
				c.$providerNotice?.remove();
				c.$providerNotice = root
					.$('<p class="text-muted dlp-po-provider-notice"></p>')
					.text(t(c.providerPayload.notice || ""))
					.appendTo(c.$advancedFilters);
			},
		};
		const grid = root.DeepLinkERPCompactList.create({
			doctype: "Purchase Invoice",
			purchaseChrome: true,
			backupMigratedPreferences: true,
			defaultColumns: provider.defaultColumns,
			preferenceVersion: provider.preferenceVersion,
			migratePreferences: provider.migratePreferences,
			mainFilterFields: ['search','company','from_date','to_date'],
			pageRoute: route,
			dismissInitialOnboarding: true,
			routeClass: "dlp-purchase-payable-grid-active",
			columns,
			provider,
			renderLink: (_c, _doc, value) => value,
			emptyLabel: translateText("没有符合条件的关联采购应付单"),
			pageFieldMap: {
				total: 'grand_total', settled: 'outstanding_amount', outstanding: 'outstanding_amount',
				orders: "items",
				receipts: "items",
				from_date: "posting_date",
				to_date: "posting_date",
			},
			dates: ["posting_date"],
			numbers: ['grand_total','total','settled','outstanding'],
			controls: [
				["search", "Data", null, "应付单号 / 供应商"],
				["company", "Link", "Company", "公司"],
				["supplier", "Link", "Supplier", "供应商"],
				["from_date", "Date", null, "应付开始日期"],
				["to_date", "Date", null, "应付结束日期"],
			].map(([fieldname, fieldtype, options, label]) => ({
				fieldname,
				fieldtype,
				options,
				label,
				permission_field: ['from_date','to_date'].includes(fieldname)?'posting_date':fieldname,
			})),
		});
		const ready = root.frappe.model
			.with_doctype("Purchase Invoice")
			.then(() => (api.controller = grid.mountPage(page, root)));
		wrapper.dlpLoad = async () => {
			const c = await ready;
			return c.refresh();
		};
		ready.catch(() =>
			page.main.append(
				`<p class="text-danger">${esc(t("应付元数据读取失败，请刷新或核对权限。"))}</p>`
			)
		);
		page.set_secondary_action(t("刷新"), () => wrapper.dlpLoad());
	};
	root.frappe.pages[route].on_page_show = (wrapper) => wrapper.dlpLoad?.();
})(globalThis);
