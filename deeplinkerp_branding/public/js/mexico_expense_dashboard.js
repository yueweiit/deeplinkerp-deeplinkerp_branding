(function (root, factory) {
    if (typeof module === "object" && module.exports) module.exports = factory;
    root.DeepLinkERPMexicoExpenseDashboard = factory(root);
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
    const t = (text, args = []) => root.__ ? root.__(text, args) : text.replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");
    const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    const link = (label, action, attrs = "", cls = "") => `<button type="button" class="mx-link ${cls}" data-dashboard="${action}" ${attrs}>${label}</button>`;
    const rowCompany = company => `data-company="${esc(company)}"`;
    const info = text => `<button type="button" class="mx-help" data-action="dashboard-help" data-help="${esc(text)}" aria-label="${esc(t("View explanation"))}" title="${esc(t("View explanation"))}">i</button>`;
    const historyUnavailable = () => t("Historical exchange rates are unavailable. Refresh to retry; selected-period totals remain available.");
    function comparisonReason(row) {
        return row?.unavailable_reason === "exchange_rate" ? historyUnavailable() : t({
            synchronization:"Some selected sources have not completed synchronization",
            history:"There are insufficient confirmed records in one of the comparison periods",
            review:"Review items in either period prevent a reliable comparison",
            period:"No comparable elapsed period",
        }[row?.unavailable_reason] || "Compare confirmed expenses only. Missing history, unfinished synchronization or review items in either period prevent a reliable comparison.");
    }
    function render(data, {currency, company, forms, categories, money, number}) {
        const review = data.review, comparison = data.comparison;
        const date = value => root.frappe?.datetime?.str_to_user ? root.frappe.datetime.str_to_user(value) : value;
        const compareDates = comparison ? `${t("Comparison Dates")}: ${date(comparison.from_date)} → ${date(comparison.to_date)} / ${date(comparison.previous_from)} → ${date(comparison.previous_to)}` : t("No comparable elapsed period");
        const compareLabel = comparison?.mode === "month_to_date" ? "Same period of previous month" : "Previous Period";
        const compareInfo = row => [!row?.comparable ? comparisonReason(row) : t("Only confirmed expense lines are included"), compareDates].join("\n");
        const change = row => !row?.comparable ? `<span class="mx-muted">—</span>` + info(compareInfo(row))
            : row.percent == null ? esc(t("No prior-period base")) : `${Number(row.percent) > 0 ? "+" : ""}${esc(row.percent)}%`;
        const countLink = (count, company, bucket, cls = "") => count ? link(esc(number(count)), "review", `${rowCompany(company)} data-bucket="${bucket}" aria-label="${esc(t(bucket === "attention" ? "Review {0} expense lines" : "View {0} lines awaiting finance",[number(count)]))}"`, cls) : `<span class="mx-muted">${esc(number(0))}</span>`;
        const card = (label, value, detail, explanation) => `<section class="mx-card"><div class="mx-card-label">${esc(t(label))}${explanation ? info(explanation) : ""}</div><div class="mx-card-value">${value}</div><div class="mx-card-detail">${detail}</div></section>`;
        const scopeInfo = [t("Confirmed expense lines only; financial approval is not proof of payment."), data.scope ? `${date(data.scope.from_date)} → ${date(data.scope.to_date)} · ${currency}` : currency].join("\n");
        const unassigned = review.unassigned_lines ? link(esc(t("Including {0} unassigned",[number(review.unassigned_lines)])), "review", 'data-unassigned="1" data-bucket="attention"', "mx-unassigned-chip") : "";
        const summaries = `<div class="mx-dashboard-cards">${card("Confirmed Expenditure", money(data.total,currency),
            esc(t("{0} applications · {1} expense lines",[number(data.included.applications),number(data.included.lines)])), scopeInfo)}${card(compareLabel,
            comparison?.comparable ? change(comparison) : "—",
            comparison?.comparable ? `${esc(t("Change Amount"))}: ${money(comparison.delta,currency)}` : `<span class="mx-status-neutral">${esc(t("Comparison unavailable"))}</span>`, compareInfo(comparison))}
            <section class="mx-card"><div class="mx-card-label">${esc(t("Expense Follow-up"))}${info(t("Unassigned expenses are included in review counts, but not in subsidiary expenditure totals."))}</div><div class="mx-follow-up"><div><span>${esc(t("Needs Review"))}</span><strong>${countLink(review.lines,company || "","attention")}</strong></div><div><span>${esc(t("Awaiting Finance"))}</span><strong>${countLink(review.awaiting_lines,company || "","awaiting_finance")}</strong></div></div>${unassigned ? `<div class="mx-card-detail">${unassigned}</div>` : ""}</section></div>`;
        const head = (title, explanation = "") => `<div class="mx-panel-head"><h3>${title}${explanation ? info(explanation) : ""}</h3><span class="mx-currency-label">${esc(currency)}</span></div>`;
        const table = (headers, rows, sorted = -1) => `<div class="mx-scroll"><table><thead><tr>${headers.map((h,i)=>`<th${i ? ' class="num"' : ""}${i === sorted ? ' aria-sort="descending"' : ""}>${esc(t(h))}${i === sorted ? ' <span aria-hidden="true">↓</span>' : ""}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>`;
        if (!company) {
            const rows = data.companies.map(row => `<tr><td>${link(`${esc(row.company)}<span aria-hidden="true">›</span>`,"company",rowCompany(row.company),"mx-company-link")}</td><td class="num mx-strong">${row.lines ? link(money(row.amount,currency),"details",rowCompany(row.company)) : `<span class="mx-muted">${esc(t("No Records"))}</span>`}</td><td class="num">${row.lines ? esc(row.share)+"%" : "—"}</td><td class="num">${change(row)}</td><td class="num">${countLink(row.review_lines,row.company,"attention")}</td><td class="num">${countLink(row.awaiting_lines,row.company,"awaiting_finance")}</td></tr>`).join("");
            return summaries + `<section class="mx-panel mx-dashboard-companies">${head(esc(t("Subsidiaries ({0})",[number(data.companies.length)])))}${table(["Subsidiary","Confirmed Expenditure","Expenditure Share",compareLabel,"Needs Review","Awaiting Finance"], rows || `<tr><td colspan="6">${esc(t("No authorized subsidiaries"))}</td></tr>`,1)}</section>`;
        }
        const breakdown = (title, definitions, amounts, field, action) => `<section class="mx-panel">${head(esc(t(title)))}${table([field === "approval_type" ? "Approval Form" : "Expense Category","Confirmed Expenditure"],definitions.map(definition=>{
            const row = amounts.find(row=>row[field]===definition.id);
            return `<tr><td>${esc(definition.label)}</td><td class="num">${row?.lines ? link(money(row.amount,currency),"details",`${rowCompany(company)} data-${action}="${esc(definition.id)}"`) : `<span class="mx-muted">${esc(t("No Records"))}</span>`}</td></tr>`;
        }).join("") || `<tr><td colspan="2" class="mx-muted">${esc(t("No Records"))}</td></tr>`)}</section>`;
        const trend = data.trend || [], max = Math.max(...trend.map(row=>Math.abs(Number(row.amount))),1);
        const trendBody = data.trend_unavailable_reason === "exchange_rate"
            ? `<div class="mx-notice mx-warning" role="status">${esc(t("Trend unavailable"))}${info(historyUnavailable())}</div>`
            : `<div class="mx-month-bars">${trend.map(row=>{
                const hasRecords = row.amount != null && row.lines !== 0;
                const tag = hasRecords ? "button" : "div";
                return `<${tag} ${hasRecords ? `type="button" data-dashboard="trend" data-from="${esc(row.from_date)}" data-to="${esc(row.to_date)}"` : ""} class="mx-month-bar${hasRecords ? "" : " mx-month-empty"}"><span>${esc(row.month)}</span><span class="mx-bar-track">${hasRecords ? `<i style="width:${Math.min(100,Math.abs(Number(row.amount))/max*100)}%"></i>` : ""}</span><strong>${hasRecords ? money(row.amount,currency) : esc(t("No Records"))}</strong></${tag}>`;
            }).join("")}</div>`;
        return summaries + `<div class="mx-insight-columns">${breakdown("Expenditure by Approval Form",forms,data.forms || [],"approval_type","form")}${breakdown("Expense Composition",categories.filter(c=>(data.categories || []).some(row=>row.category===c.id)),data.categories || [],"category","category")}</div><section class="mx-panel mx-insight-panel">${head(esc(t("Recent Six Months")),t("Through the selected month; select a month to view confirmed details"))}${trendBody}</section>`;
    }
    return {render};
});
