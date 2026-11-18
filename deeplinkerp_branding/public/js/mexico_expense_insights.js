(function (root, factory) {
	if (typeof module === "object" && module.exports) module.exports = factory;
	root.DeepLinkERPMexicoExpenseInsights = factory(root);
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
	const t = (text, args = []) => root.__ ? root.__(text, args) : text.replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");
	const esc = (v) => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
	function render(data, {currency, approval, company, basis, kind, categories, subcategories}) {
		const money = v => root.DeepLinkERPMexicoExpenseMoney.format(v, currency);
		const pct = v => v == null ? t("No prior-period base") : `${v}%`;
		const card = (label, value, detail = "") => `<div class="mx-card"><span>${esc(t(label))}</span><div class="mx-card-value">${value}</div><small>${detail}</small></div>`;
		const labels = kind === "payroll" ? subcategories : categories;
		const name = key => labels.find(c => c.id === key)?.label || key;
		const panel = (label, html) => `<section class="mx-panel mx-insight-panel"><h3>${esc(t(label))}</h3>${html}</section>`;
		const comparison = data.comparison;
		const changes = (rows, field) => `<div class="mx-scroll"><table><thead><tr><th>${esc(t(field === "company" ? "Subsidiary" : "Expense Category"))}</th><th class="num">${esc(t("Comparison Period"))}</th><th class="num">${esc(t("Previous Period"))}</th><th class="num">${esc(t("Change Amount"))}</th><th class="num">${esc(t("Change Rate"))}</th></tr></thead><tbody>${rows.map(r => `<tr><td><button class="mx-link" data-insight="${field}" data-key="${esc(r.key)}" data-from="${esc(comparison.from_date)}" data-to="${esc(comparison.to_date)}">${esc(field === "company" ? r.key : name(r.key))}</button></td><td class="num">${money(r.current)}</td><td class="num">${money(r.previous)}</td><td class="num">${money(r.delta)}</td><td class="num">${esc(pct(r.percent))}</td></tr>`).join("")}</tbody></table></div>`;
		return `<div class="mx-notice mx-report-scope"><strong>${esc(t("Report Scope"))}</strong> · ${esc(approval)} · ${esc(company)} · ${esc(basis)} · ${esc(currency)}<br>${esc(t("Confirmed expense lines only; financial approval is not proof of payment."))}</div>
		<div class="mx-payroll-cards mx-insight-cards">${card("Confirmed Expenditure", money(data.total), esc(t("{0} applications · {1} expense lines", [data.included.applications, data.included.lines])))}${card("Subsidiaries Included", esc(data.company_count))}${card("Applications Requiring Review", esc(data.pending.applications), `<button class="mx-link" data-insight="review">${esc(t("View Review Items"))}</button>`)}${card("Known Amount Requiring Review", money(data.pending.known_amount), esc(t("{0} lines have unconfirmed amounts", [data.pending.unknown_lines])))}</div>
		<div class="mx-notice">${esc(t("Review items cover active applications. Items without a finance period use application dates; they are not added to confirmed totals. Amounts before finance approval use estimated conversion."))}</div>
		${comparison ? panel("Period Comparison", `<p>${esc(t({calendar:"Previous complete calendar period",month_to_date:"Same period of previous month",equal_days:"Previous equal-length period"}[comparison.mode]))} · ${esc(comparison.from_date)} → ${esc(comparison.to_date)} / ${esc(comparison.previous_from)} → ${esc(comparison.previous_to)}</p><p>${esc(t("Current: {0} confirmed applications; previous: {1}. Unsynchronized or unconfirmed records are excluded from comparisons.",[comparison.current_applications,comparison.previous_applications]))}</p><div class="mx-payroll-cards">${card("Comparison Period",money(comparison.current))}${card("Previous Period",money(comparison.previous))}${card("Change Amount",money(comparison.delta))}${card("Change Rate",esc(pct(comparison.percent)))}</div>`) : `<div class="mx-notice">${esc(t("No comparable elapsed period"))}</div>`}
		<div class="mx-insight-columns">${panel("Subsidiary Ranking", `<div class="mx-scroll"><table><thead><tr><th>${esc(t("Subsidiary"))}</th><th class="num">${esc(t("Total"))}</th><th class="num">${esc(t("Share"))}</th></tr></thead><tbody>${data.companies.map((r, i) => `<tr><td><button class="mx-link" data-insight="company" data-key="${esc(r.key)}">${i + 1}. ${esc(r.key)}</button></td><td class="num">${money(r.current)}</td><td class="num">${esc(r.share)}%</td></tr>`).join("") || `<tr><td colspan="3">${esc(t("No confirmed expenses"))}</td></tr>`}</tbody></table></div>`)}${panel("Monthly Expenditure", `<div class="mx-month-bars">${data.trend.map(r => {
			const max = Math.max(...data.trend.map(v=>Math.abs(Number(v.amount))), 1);
			return `<button class="mx-month-bar" data-month="${esc(r.month)}"><span>${esc(r.month)}${r.partial ? `<small>${esc(t("Partial Month"))}</small>` : ""}</span><span class="mx-bar-track"><i style="width:${Math.min(100, Math.abs(Number(r.amount))/max*100)}%"></i></span><strong>${money(r.amount)}</strong></button>`;
		}).join("") || esc(t("No confirmed expenses"))}</div>`)}</div>
		${comparison ? `<div class="mx-insight-columns">${panel("Largest Subsidiary Changes",changes(data.company_changes.slice(0,5),"company"))}${panel("Largest Category Changes",changes(data.movers,"category"))}</div>` : ""}`;
	}
	return {render};
});
