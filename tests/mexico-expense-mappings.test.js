const test = require("node:test");
const assert = require("node:assert/strict");
const factory = require("../deeplinkerp_branding/public/js/mexico_expense_mappings.js");
const {replaceAliases, aliasKey} = factory({});
const row = (source_entity, company = "A", entity_kind = "entity") => ({source_entity, company, entity_kind});

test("multiple historical names share one company without mutating saved settings", () => {
	const saved = [row("Old A"), row("Old B", "B")];
	const result = replaceAliases(saved, "A", [row("Old A"), row("New A")]);
	assert.equal(saved.length, 2);
	assert.equal(result.length, 3);
	assert.deepEqual(result[0], row("Old B", "B"));
});
test("one source and kind cannot be silently reassigned to another subsidiary", () => {
	assert.throws(() => replaceAliases([row("Old A"), row("Old B", "B")], "A", [row("Old A"), row(" Old   B ")]), /Duplicate/);
});
test("departments stay distinct and normalized suggestions do not broaden formal matching", () => {
	assert.notEqual(aliasKey(row("Name")), aliasKey(row("name")));
	assert.equal(replaceAliases([row("Name", "B")], "A", [row("Name", "A", "department")]).length, 2);
});
test("removing aliases affects only selected company and empty aliases are rejected", () => {
	assert.deepEqual(replaceAliases([row("Old A"), row("Old B", "B")], "A", []), [row("Old B", "B")]);
	assert.throws(() => replaceAliases([], "A", [row(" ")]), /Invalid/);
	assert.throws(() => replaceAliases([], "A", [row("X", "A", "unknown")]), /Invalid/);
});

test("editing MX aliases preserves explicit non-MX rules and rejects conflicts", () => {
    const rule={source_entity:"Domestic",entity_kind:"department",company:"",expense_scope:"non_mx",scope_reason:"CN company"};
    const result=replaceAliases([rule,row("Old A")],"A",[row("New A")]);
    assert.deepEqual(result[0],rule);
    assert.throws(()=>replaceAliases([rule],"A",[row("Domestic","A","department")]), /Duplicate/);
});
