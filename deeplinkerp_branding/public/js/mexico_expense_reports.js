// Included by the native report loader before it constructs filter controls.
frappe.provide("frappe.deeplinkerp");
frappe.deeplinkerp.mexico_expense_report = function (kind) {
	const payroll = kind === "payroll";
	return {
		formatter(value, row, column, data, defaultFormatter) {
			if (column.fieldtype === "Currency" && data?.currency) {
				return DeepLinkERPMexicoExpenseMoney.format(value, data.currency);
			}
			return defaultFormatter(value, row, column, data);
		},
		filters: [
            {fieldname: "classification_key", label: __("Source Classification Key"), fieldtype: "Data", hidden: 1},
            {fieldname: "classification_exact", label: __("Exact Source Classification"), fieldtype: "Data", hidden: 1},
			{
                fieldname: "from_date", label: __("Start Date"), fieldtype: "Date", reqd: 1,
            },
            {
                fieldname: "to_date", label: __("End Date"), fieldtype: "Date", reqd: 1,
            },
            {
                // Retain old report URLs; new navigation passes exact dates.
                fieldname: "month", label: __("Reporting Month"), fieldtype: "Data", hidden: 1,
			},
			{
				fieldname: "basis", label: __("Date Basis"), fieldtype: "Select",
				options: [
					{ value: "period", label: __("Expense Period") },
					{ value: "request", label: __("Application Date") },
					{ value: "planned", label: __("Planned Payment Date") },
				],
				default: "period", hidden: payroll,
			},
			{
				fieldname: "company", label: __("Subsidiary"), fieldtype: "Link",
				options: "Company",
				get_query: () => ({ filters: { country: "Mexico", is_group: 0 } }),
			},
			{
				fieldname: "approval_type", label: __("Approval Form"), fieldtype: "Autocomplete",
			},
			{
				fieldname: "category", label: __("Expense Category"), fieldtype: payroll ? "Data" : "Autocomplete",
				default: payroll ? "personnel" : "", hidden: payroll,
			},
			{
				fieldname: "subcategory", label: __("Expense Subcategory"), fieldtype: "Autocomplete",
			},
			{
				fieldname: "keyword", label: __("Search expenses"), fieldtype: "Data",
			},
			{
				// Review-only scope is ignored by confirmed reports, as in the workbench.
				fieldname: "issue", label: __("Review Issues"), fieldtype: "Data", hidden: 1,
			},
			{
				fieldname: "reporting_currency", label: __("Reporting Currency"), fieldtype: "Select",
				options: "\nMXN\nUSD\nCNY", reqd: 1,
			},
			{ fieldname: "currency", label: __("Original Currency"), fieldtype: "Data", hidden: 1 },
			{
				fieldname: "status", label: __("Approval Status"), fieldtype: "Select",
				options: [
					{ value: "finance_approved", label: __("Finance Approved") },
					{ value: "approved", label: __("Approved") },
					{ value: "pending", label: __("In Approval") },
					{ value: "active", label: __("Active Applications") },
					{ value: "rejected", label: __("Rejected") },
					{ value: "terminated", label: __("Terminated") },
					{ value: "withdrawn", label: __("Withdrawn") },
					{ value: "unknown", label: __("Unknown") },
					{ value: "all", label: __("All Statuses") },
				],
				default: "finance_approved", reqd: 1,
			},
		],
		async onload(report) {
			const context = await frappe.xcall("deeplinkerp_branding.services.mexico_expenses.get_context");
			const target = report.get_filter("reporting_currency");
			await target.set_value(target.get_value() || report.get_filter_value("currency") || "MXN");
			await report.get_filter("currency").set_value("");
			const from = report.get_filter("from_date"), to = report.get_filter("to_date");
            if (!from.get_value() && !to.get_value()) {
                const month = report.get_filter_value("month") || (context.today || frappe.datetime.get_today()).slice(0, 7);
                const [year, mm] = month.split("-").map(Number);
                await from.set_value(month + "-01");
                await to.set_value(new Date(Date.UTC(year, mm, 0)).toISOString().slice(0, 10));
            }
            await report.get_filter("month").set_value("");
            // Route values are applied before onload. Autocomplete accepts them before
			// its dictionary arrives; refreshing the label must preserve the stable code.
			for (const [field, entries] of [
				["approval_type", context.approval_forms], ["category", context.categories], ["subcategory", context.subcategories],
			]) {
				const control = report.get_filter(field);
				if (control.df.fieldtype !== "Autocomplete") continue;
				const value = control.get_value();
				control.set_data(entries.map((entry) => ({ value: entry.id, label: entry.label })));
				control.set_input(value);
			}
			if (payroll) {
				await report.get_filter("basis").set_value("period");
				await report.get_filter("category").set_value("personnel");
			}
		},
	};
};
