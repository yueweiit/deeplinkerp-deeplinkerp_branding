frappe.listview_settings["CN Cash Journal Entry"] = {
    add_fields:["direction", "currency", "amount", "dingtalk_approval_number"],
    formatters: {
        direction(value) {
            return value === "income" ? __("income", null, "CN Cash Journal Entry")
                : value === "expense" ? __("expense", null, "CN Cash Journal Entry") : "";
        },
    },
};
