(function (root, factory) {
    if (typeof module === "object" && module.exports) module.exports = factory;
    root.DeepLinkERPChinaOperatingExpenses = factory(root);
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
    "use strict";
    const native = () => root.DeepLinkERPOperatingExpenses;
    const t = (text) => root.__ ? root.__(text) : text;
    const esc = (value) => native().esc(value);
    const API = "deeplinkerp_branding.services.operating_expenses.";
    const scope = "CN totals use application dates and original currencies. Verified payments are cumulative for the selected applications, not payments made during this period or posted expenses.";
    const cashierScope = "Counts application amounts once after all CN cashier tasks have completed and agreed, using the last completion date in China time. This is cashier processing, not verified bank payments. Missing completion evidence is excluded; synchronize data to refresh it.";
    const paymentScope = "OA approval tasks do not provide actual payment amounts. Verified payments and remaining balances stay pending review until payment evidence is available; pending review does not mean zero or unpaid.";
    const scopeFor = (basis) => basis === "cashier" ? cashierScope : scope;
    const syncNotice = (data) => data.cashier_pending_sync ? `<div class="mx-notice">${esc(t("Some cashier completion dates have not been synchronized. Use Synchronize Data to refresh them; current statistics include only applications with completion evidence."))}</div>` : "";
    const help = (label) => root.DeepLinkERPMexicoExpenseInsights?.help(t(label)) || "";
    const action = (label, id, extra = "") => `<button type="button" class="mx-button" data-cn-action="${id}" ${extra}>${esc(t(label))}</button>`;
    const connected = (filters, view) => ["", "operating"].includes(filters.approval_type) && view !== "payroll";
    function filters(values, view) {
        return {date_from:values.from_date, date_to:values.to_date, company:values.company,
            basis:values.basis === "cashier" ? "cashier" : "request",
            approval_state:values.status === "all" ? "" : values.status,
            quick_tab:view === "review" ? "reconciliation" : (values.quick_tab || "all"),
            keyword:values.keyword || "", company_review:values.company_review || "",
            application_type:values.application_type || ""};
    }
    function amount(bucket, field) {
        const value = native().money(bucket[field]);
        if (bucket[field] != null) return esc(value);
        const known = bucket.known_totals?.[field];
        return `${esc(t("Pending Review"))}${known == null ? "" : `<small>${esc(t("Known subtotal"))}: ${esc(native().money(known))}</small>`}`;
    }
    function moneyLines(totals, field) {
        return Object.entries(totals || {}).map(([currency, bucket]) =>
            `<div class="cn-money-line"><span>${esc(currency)}</span><strong>${amount(bucket, field)}</strong></div>`).join("") || "—";
    }
    function panel(title, body, rule = "") {
        return `<section class="mx-panel"><div class="mx-panel-head"><h3>${esc(t(title))} ${rule ? help(rule) : ""}</h3></div>${body}</section>`;
    }
    function table(headers, rows) {
        return `<div class="mx-scroll"><table><thead><tr>${headers.map((label) => `<th>${esc(t(label))}</th>`).join("")}</tr></thead><tbody>${rows || `<tr><td colspan="${headers.length}"><div class="mx-empty">${esc(t("No matching applications"))}</div></td></tr>`}</tbody></table></div>`;
    }
    function groupedTable(groups, label, dimension, cashier = false) {
        const rows = (groups || []).flatMap((group) => Object.entries(group.currencies || {}).map(([currency, bucket]) =>
            `<tr><td>${esc(dimension(group))}</td><td>${esc(currency)}</td><td>${esc(group.currency_counts?.[currency] ?? group.count)}</td>${(cashier ? ["amount"] : ["amount", "paid_amount", "pending_amount"]).map((field) => `<td>${amount(bucket, field)}</td>`).join("")}</tr>`)).join("");
        return table([label, "Currency", "Applications", ...(cashier ? ["Cashier Processed Amount"] : ["Application Amount", "Verified Payments", "Remaining to Pay"])], rows);
    }
    function dashboard(data, view, grouping) {
        const cashier = data.basis === "cashier";
        const cards = cashier ? `<div class="mx-cards"><div class="mx-card first"><span>${esc(t("Cashier Processed Amount"))} ${help(cashierScope)}</span><div class="cn-money-values">${moneyLines(data.currency_totals, "amount")}</div></div>${[
            ["Cashier Processed Applications", data.total_count],
            ["Included Subsidiaries", (data.company_overview || []).filter((group) => group.company).length],
            ["Attribution Pending Review", (data.review_counts?.unmapped || 0) + (data.review_counts?.stale || 0)],
        ].map(([label, value]) => `<div class="mx-card"><span>${esc(t(label))}</span><div class="mx-card-value">${esc(data.cashier_pending_sync && !data.total_count ? "—" : value)}</div></div>`).join("")}</div>` : `<div class="mx-cards">${[["Application Amount", "amount"], ["Verified Payments", "paid_amount"], ["Remaining to Pay", "pending_amount"]].map(([label, field], i) =>
            `<div class="mx-card ${i === 0 ? "first" : ""}"><span>${esc(t(label))} ${help(field === "amount" ? scope : paymentScope)}</span><div class="cn-money-values">${moneyLines(data.currency_totals, field)}</div></div>`).join("")}
            <div class="mx-card"><span>${esc(t("Applications Requiring Review"))}</span><div class="mx-card-value">${esc(data.tab_counts?.reconciliation ?? 0)}</div>${action("View Review Items", "review")}</div></div>`;
        const company = () => panel(cashier ? "Subsidiary Cashier Processing Distribution" : "Subsidiary Application and Payment Distribution", groupedTable(data.company_overview, "Subsidiary", (group) => group.company || t("Attribution Pending Review"), cashier),
            "Unconfirmed company amounts remain in a separate review group and are not assigned to a subsidiary.");
        const month = () => panel(cashier ? "Monthly Cashier Processing" : "Monthly Applications and Payments", groupedTable(data.months, cashier ? "Cashier Processing Month" : "Application Month", (group) => group.key || t("Unknown"), cashier), scopeFor(data.basis));
        if (view === "analysis") return syncNotice(data) + cards + `<div class="mx-analysis-group">${["company", "trend"].map((key) =>
            action(key === "company" ? "By Subsidiary" : "By Month", key, `aria-pressed="${grouping === key}"`)).join("")}</div>` + (grouping === "trend" ? month() : company());
        return syncNotice(data) + cards + month() + panel("Application Type Composition", groupedTable(data.application_types, "Application Type", (group) => native().typeLabel(group.key), cashier)) + company();
    }
    function detail(data, wb) {
        const review = wb.view === "review";
        const tabs = review ? "" : `<div class="mx-analysis-group">${native().quickTabs.map(([id, label]) =>
            action(label, "tab", `data-tab="${id}" aria-pressed="${(wb.filters.quick_tab || "all") === id}"`) + `<span class="text-muted">${esc(data.tab_counts?.[id] ?? 0)}</span>`).join("")}</div>`;
        const cashier = wb.filters.basis === "cashier";
        const columns = ["source_id", "company", "source_company", "applicant", "summary", "currency", "amount", "paid_amount", "pending_amount", "source_status", "actions", "request_date", "effective_application_type", "payee_name", "needed_payment_date", "current_approver", "finance_status"]
            .filter((id) => !cashier || !["paid_amount", "pending_amount", "source_status"].includes(id))
            .map((id) => ({...native().columns.find((column) => column.fieldname === id), ...(cashier && id === "amount" ? {label:"Cashier Processed Amount"} : {})}));
        if (cashier) columns.splice(7, 0, {fieldname:"cashier_date", label:"Cashier Processing Date"});
        const rows = data.rows.map((row) => `<tr>${columns.map((col) => `<td>${col.fieldname === "cashier_date" ? esc(row.cashier_date || "—") : native().renderValue(col.fieldname, row)}</td>`).join("")}</tr>`).join("");
        const total = Number(data.total_count), page = Math.floor(wb.start / 20) + 1;
        return syncNotice(data) + tabs + panel(review ? "Data Review" : "Expense Application Register",
            `<div class="cn-list-tools"><label>${esc(t("Search"))}<input class="form-control" type="search" data-filter="keyword" maxlength="200" value="${esc(wb.filters.keyword || "")}" placeholder="${esc(t("Search approval number, applicant or description"))}"></label>
                ${wb.filter("company_review", "Company Review", [["", t("All")], ["unmapped", t("Unmapped")], ["stale", t("Needs Reconfirmation")], ["confirmed", t("Confirmed")]])}
                ${wb.filter("application_type", "Application Type", [["", t("All")], ...["payment", "reimbursement", "unclassified"].map((id) => [id, native().typeLabel(id)])])}</div>
            <div class="cn-operating-table">${table(columns.map((col) => col.label), rows)}</div>
            <div class="cn-pagination"><span>${esc(t("Applications"))}: ${esc(total)} · ${page} / ${Math.max(1, Math.ceil(total / 20))}</span>${action("Previous", "previous", wb.start ? "" : "disabled")}${action("Next", "next", wb.start + 20 < total ? "" : "disabled")}</div>`, scopeFor(wb.filters.basis));
    }
    async function load(wb) {
        const list = ["details", "review"].includes(wb.view);
        const args = {filters:filters(wb.filters, wb.view)};
        if (list) Object.assign(args, {page_length:20, start:wb.start || 0});
        const response = await root.frappe.call({method:API + (list ? "get_operating_expenses" : "get_operating_expense_analysis"), args});
        return response.message;
    }
    async function exportCurrent(wb) {
        if (!root.DeepLinkERPPurchaseOrderExport?.downloadWorkbook) await root.frappe.require("/assets/deeplinkerp_branding/js/purchase_order_export.js");
        const exporter = root.DeepLinkERPPurchaseOrderExport;
        const args = {filters:JSON.stringify(filters(wb.filters, wb.view)), order_by:"request_date desc"};
        exporter.downloadWorkbook(root, await exporter.fetchNativeWorkbook(root, args, API + "export_operating_expenses"), t("Operating Expenses"));
    }
    function openSource(source, tab, onRefresh) {
        return root.DeepLinkERPOperatingExpenseDrawer.open(source, onRefresh, tab);
    }
    return {connected, filters, amount, moneyLines, dashboard, detail, load, exportCurrent, openSource, scope, scopeFor};
});
