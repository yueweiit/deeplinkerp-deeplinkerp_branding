const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const code = fs.readFileSync(require.resolve("../deeplinkerp_branding/public/js/regional_expenses.js"), "utf8");
const escape = value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");

function harness(overrides = {}) {
    const dangerous = '<img src=x onerror="window.pwned=1">';
    const data = {name: "record", origin: "cn", key: "source", revision: "v1", fingerprint: "hash", number: dangerous,
        state: "confirmed", companies: {[dangerous]: "China", MX: "Mexico"}, categories: {service: "Services"},
        lines: [{line_key: "original", source_entity: dangerous, amount: "999999999999.99", currency: "USD"}],
        allocations: [{line_key: "original", region: "cn", company: dangerous, amount: "999999999999.99", management_category: "service"}],
        note: "Review", ...overrides};
    const dialogs = [], calls = [];
    let fail = false;
    class Dialog {
        constructor(config) {
            this.config = config; this.fields_dict = {}; this.values = {};
            for (const field of config.fields) {
                this.values[field.fieldname] = field.data || field.default || "";
                this.fields_dict[field.fieldname] = {df: field, grid: {refresh() {}}, $wrapper: {html: value => {this.html = value;}}};
            }
            dialogs.push(this);
        }
        show() {this.visible = true;}
        hide() {this.visible = false;}
        get_values() {return this.values;}
        disable_primary_action() {this.disabled = true;}
        enable_primary_action() {this.disabled = false;}
    }
    const context = {window: {}, __: text => text, frappe: {ui: {Dialog}, utils: {escape_html: escape}, msgprint() {},
        async call({method, args}) {
            calls.push({method, args});
            if (method.endsWith("get_editor")) return {message: data};
            if (method.endsWith("list_allocations")) return {message: [{name: "record", number: dangerous, origin: "cn", state: "stale"}]};
            if (fail) throw new Error("Save failed");
            return {message: {ok: true}};
        }}};
    vm.runInNewContext(code, context);
    return {api: context.window.RegionalExpenses, dialogs, calls, data, fail: () => {fail = true;}};
}

test("source labels, table cells and company options remain escaped", async () => {
    const h = harness(); await h.api.open({origin: "cn", key: "source"});
    const dialog = h.dialogs[0], fields = dialog.fields_dict.allocations.df.fields;
    assert.ok(!dialog.html.includes("<img"));
    for (const name of ["line_key", "company"]) {
        const field = fields.find(f => f.fieldname === name);
        assert.ok(field.options.every(option => !option.label.includes("<img")));
        assert.ok(!field.formatter(h.data.allocations[0][name]).includes("<img"));
    }
    assert.equal(h.calls.length, 1, "opening never saves configuration");
});

test("saving retains decimal strings, source identity and concurrency tokens", async () => {
    const h = harness(); let reloads = 0;
    await h.api.open({allocation: "record"}, () => reloads++);
    await h.dialogs[0].config.primary_action();
    const args = h.calls.at(-1).args;
    assert.equal(args.allocations[0].amount, "999999999999.99");
    assert.equal(args.expected_revision, "v1");
    assert.equal(args.expected_fingerprint, "hash");
    assert.equal(args.allocation, "record");
    assert.equal(reloads, 1);
    assert.equal(h.dialogs[0].visible, false);
});

test("automatic ownership is explained and saving submits a manual exception without trusted metadata", async () => {
    const h = harness({assignment_mode: "automatic"});
    h.data.allocations[0].assignment_mode = "automatic";
    await h.api.open({allocation: "record"});
    assert.match(h.dialogs[0].html, /Automatically assigned by company country/);
    await h.dialogs[0].config.primary_action();
    assert.equal(h.calls.at(-1).args.allocations[0].assignment_mode, undefined);
});

test("failed saving keeps the draft visible and allows retry", async () => {
    const h = harness(); await h.api.open({allocation: "record"}); h.fail();
    await assert.rejects(h.dialogs[0].config.primary_action());
    assert.equal(h.dialogs[0].visible, true);
    assert.equal(h.dialogs[0].disabled, false);
    assert.equal(h.dialogs[0].values.allocations[0].amount, "999999999999.99");
});

test("stale source can rebuild an unsaved draft without inventing assignments", async () => {
    const h = harness({state: "stale"}); await h.api.open({allocation: "record"});
    const dialog = h.dialogs[0]; dialog.fields_dict.reset_lines.df.click();
    const row = dialog.fields_dict.allocations.df.data[0];
    assert.equal(row.region, "pending"); assert.equal(row.company, ""); assert.equal(row.management_category, "");
    assert.equal(row.amount, "999999999999.99"); assert.equal(h.calls.length, 1);
});

test("allocation list can reopen an approval fully moved out of the current region", async () => {
    const h = harness(); await h.api.list();
    assert.ok(!h.dialogs[0].config.fields[0].options[0].label.includes("<img"));
    await h.dialogs[0].config.primary_action({allocation: "record"});
    assert.equal(h.calls.at(-1).args.allocation, "record");
    assert.equal(h.dialogs[1].visible, true);
});

test("MX-owned finance bindings remain read only in the allocation editor", async () => {
    const h = harness({origin: "mx"}); await h.api.open({allocation: "record"});
    assert.equal(h.dialogs[0].fields_dict.finance_node_names.df.read_only, true);
});
