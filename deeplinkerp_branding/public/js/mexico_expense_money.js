(function (root, factory) {
	if (typeof module === "object" && module.exports) module.exports = factory;
	root.DeepLinkERPMexicoExpenseMoney = factory(root);
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
	"use strict";
	const escape = (value) => String(value).replace(/[&<>"']/g,
		(char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));

	function format(value, currency) {
		if (value == null) return "—";
		const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(String(value));
		if (!match) return "—";
		// Keep the standard Desk formatter for normal amounts. Above a conservative
		// trillion-unit boundary, never coerce the amount to a JavaScript Number.
		if (BigInt(match[2]) < 1000000000000n) {
			return root.frappe.format(value,
				{ fieldtype: currency ? "Currency" : "Float", options: currency ? "currency" : undefined, precision: 2 },
				{ inline: true, only_value: true }, { currency });
		}
		const fraction = (match[3] || "").padEnd(3, "0");
		let minor = BigInt(match[2]) * 100n + BigInt(fraction.slice(0, 2));
		if (fraction[2] >= "5") minor += 1n;
		const integer = (minor / 100n).toString();
		const pattern = root.get_number_format?.(currency) || "#,###.##";
		const info = root.get_number_format_info?.(pattern) || { group_sep: ",", decimal_str: "." };
		const groups = [];
		let end = integer.length, size = 3;
		while (end > 0) {
			groups.unshift(integer.slice(Math.max(0, end - size), end));
			end -= size;
			if (pattern === "#,##,###.##") size = 2;
		}
		const amount = (match[1] && minor ? "-" : "") + groups.join(info.group_sep || "")
			+ (info.decimal_str || ".") + (minor % 100n).toString().padStart(2, "0");
		const symbol = currency ? (root.get_currency_symbol ? root.get_currency_symbol(currency) : currency) : null;
		if (!symbol) return escape(amount);
		const label = root.__ ? root.__(symbol) : symbol;
		const right = root.frappe.model?.get_value(":Currency", currency, "symbol_on_right");
		return escape(right ? `${amount} ${label}` : `${label} ${amount}`);
	}
	return { format };
});
