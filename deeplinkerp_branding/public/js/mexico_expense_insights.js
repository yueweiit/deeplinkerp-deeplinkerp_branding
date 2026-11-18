(function (root, factory) {
	if (typeof module === "object" && module.exports) module.exports = factory;
	root.DeepLinkERPMexicoExpenseInsights = factory(root);
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
	const t = (text, args = []) => root.__ ? root.__(text, args) : text.replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");
	const esc = (v) => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
	function help(text) {
        return `<button type="button" class="mx-help" data-mx-help title="${esc(text)}" aria-label="${esc(text)}">?</button>`;
    }
    function mount(element) {
        if (!root.$?.fn?.tooltip) return;
        root.$(element).find("[data-mx-help]").each(function () {
            const target = root.$(this);
            if (target.data("bs.tooltip")) return;
            target.tooltip({
                trigger: "hover focus", placement: "auto", container: "body", html: false,
                delay: {show: 150, hide: 0},
                template: '<div class="tooltip mx-report-tooltip" role="tooltip"><div class="arrow"></div><div class="tooltip-inner"></div></div>',
            });
            target.on("mouseleave.mx-help", () => target.tooltip("hide"));
            target.on("keydown.mx-help", event => {
                if (event.key === "Escape") { target.tooltip("hide"); this.blur(); }
            });
        });
    }
    function dispose(element) {
        if (!root.$?.fn?.tooltip) return;
        root.$(element).find("[data-mx-help]").each(function () {
            const target = root.$(this);
            target.off(".mx-help");
            if (target.data("bs.tooltip")) target.tooltip("dispose");
        });
    }
    function render(data, {currency, approval, company, basis, kind, categories, subcategories, from, to}) {
		const money = v => root.DeepLinkERPMexicoExpenseMoney.format(v, currency);
		const pct = v => v == null ? t("No prior-period base") : `${v}%`;
		const card = (label, value, detail = "", description = "") => `<div class="mx-card"><span>${esc(t(label))}${description ? help(description) : ""}</span><div class="mx-card-value">${value}</div><small>${detail}</small></div>`;
		const labels = kind === "payroll" ? subcategories : categories;
		const name = key => labels.find(c => c.id === key)?.label || key;
		const panel = (label, html, description = "") => `<section class="mx-panel mx-insight-panel"><h3>${esc(t(label))}${description ? help(description) : ""}</h3>${html}</section>`;
		const comparison = data.comparison;
		const changes = (rows, field) => `<div class="mx-scroll"><table><thead><tr><th>${esc(t(field === "company" ? "Subsidiary" : "Expense Category"))}</th><th class="num">${esc(t("Comparison Period"))}</th><th class="num">${esc(t("Previous Period"))}</th><th class="num">${esc(t("Change Amount"))}</th><th class="num">${esc(t("Change Rate"))}</th></tr></thead><tbody>${rows.map(r => `<tr><td><button class="mx-link" data-insight="${field}" data-key="${esc(r.key)}" data-from="${esc(comparison.from_date)}" data-to="${esc(comparison.to_date)}">${esc(field === "company" ? r.key : name(r.key))}</button></td><td class="num">${money(r.current)}</td><td class="num">${money(r.previous)}</td><td class="num">${money(r.delta)}</td><td class="num">${esc(pct(r.percent))}</td></tr>`).join("")}</tbody></table></div>`;
        const reportScope = [
            `${t("Report Scope")}: ${approval} · ${company} · ${basis} · ${currency}`,
            from && to ? `${from} → ${to}` : "",
            t("Confirmed expense lines only; financial approval is not proof of payment."),
        ].filter(Boolean).join("\n");
        const reviewScope = t("Review items cover active applications. Items without a finance period use application dates; they are not added to confirmed totals. Amounts before finance approval use estimated conversion.");
        const comparisonScope = comparison ? [
            t({calendar:"Previous complete calendar period",month_to_date:"Same period of previous month",equal_days:"Previous equal-length period"}[comparison.mode]),
            t("Current: {0} confirmed applications; previous: {1}. Unsynchronized or unconfirmed records are excluded from comparisons.",[comparison.current_applications,comparison.previous_applications]),
        ].join("\n") : "";
        return `<div class="mx-payroll-cards mx-insight-cards">${card("Confirmed Expenditure", money(data.total), esc(t("{0} applications · {1} expense lines", [data.included.applications, data.included.lines])), reportScope)}${card("Subsidiaries Included", esc(data.company_count))}${card("Applications Requiring Review", esc(data.pending.applications), `<button class="mx-link" data-insight="review">${esc(t("View Review Items"))}</button>`, reviewScope)}${card("Known Amount Requiring Review", money(data.pending.known_amount), esc(t("{0} lines have unconfirmed amounts", [data.pending.unknown_lines])), reviewScope)}</div>
        ${comparison ? panel("Period Comparison", `<div class="mx-payroll-cards">${card("Comparison Period",money(comparison.current),`<span class="mx-comparison-dates">${esc(comparison.from_date)} → ${esc(comparison.to_date)}</span>`)}${card("Previous Period",money(comparison.previous),`<span class="mx-comparison-dates">${esc(comparison.previous_from)} → ${esc(comparison.previous_to)}</span>`)}${card("Change Amount",money(comparison.delta))}${card("Change Rate",esc(pct(comparison.percent)))}</div>`, comparisonScope) : `<div class="mx-notice">${esc(t("No comparable elapsed period"))}</div>`}
		<div class="mx-insight-columns">${panel("Subsidiary Ranking", `<div class="mx-scroll"><table><thead><tr><th>${esc(t("Subsidiary"))}</th><th class="num">${esc(t("Total"))}</th><th class="num">${esc(t("Share"))}</th></tr></thead><tbody>${data.companies.map((r, i) => `<tr><td><button class="mx-link" data-insight="company" data-key="${esc(r.key)}">${i + 1}. ${esc(r.key)}</button></td><td class="num">${money(r.current)}</td><td class="num">${esc(r.share)}%</td></tr>`).join("") || `<tr><td colspan="3">${esc(t("No confirmed expenses"))}</td></tr>`}</tbody></table></div>`)}${panel("Monthly Expenditure", `<div class="mx-month-bars">${data.trend.map(r => {
			const max = Math.max(...data.trend.map(v=>Math.abs(Number(v.amount))), 1);
			return `<button class="mx-month-bar" data-month="${esc(r.month)}"><span>${esc(r.month)}${r.partial ? `<small>${esc(t("Partial Month"))}</small>` : ""}</span><span class="mx-bar-track"><i style="width:${Math.min(100, Math.abs(Number(r.amount))/max*100)}%"></i></span><strong>${money(r.amount)}</strong></button>`;
		}).join("") || esc(t("No confirmed expenses"))}</div>`)}</div>
		${comparison ? `<div class="mx-insight-columns">${panel("Largest Subsidiary Changes",changes(data.company_changes.slice(0,5),"company"))}${panel("Largest Category Changes",changes(data.movers,"category"))}</div>` : ""}`;
	}
	return {render, help, mount, dispose};
});
