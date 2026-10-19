const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

let refresh;
const source = "Untranslated item help";
const translation = "Localized item help";
const context = {
	frappe: { ui: { form: { on(doctype, handlers) {
		assert.equal(doctype, "Item");
		refresh = handlers.refresh;
	} } } },
	__(text, replacements, doctype) {
		assert.equal(text, source);
		assert.equal(doctype, "Item");
		return translation;
	},
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,
	"../deeplinkerp_branding/public/js/item_form.js"), "utf8"), context);

const text = { nodeType: 3, textContent: source };
const link = { nodeType: 1, textContent: "Documentation", href: "/docs/item" };
const untouched = { nodeType: 3, textContent: "Other information" };
const df = { description: source, show_description_on_click: 1 };
const frm = { doctype: "Item", fields_dict: { end_of_life: {
	df, $wrapper: { find(selector) {
		assert.equal(selector, ".info-card .sidebar-card-description");
		return { each(fn) { fn(0, { childNodes: [text, link, untouched] }); } };
	} },
} } };
refresh(frm);
refresh(frm);
assert.equal(text.textContent, translation);
assert.equal(link.href, "/docs/item");
assert.equal(link.textContent, "Documentation");
assert.equal(untouched.textContent, "Other information");
assert.equal(df.description, source);
refresh({ fields_dict: {} });
df.show_description_on_click = 0;
refresh(frm);
console.log("PASS: Item help translation preserves metadata, links and unrelated text");
