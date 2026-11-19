const test = require("node:test");
const assert = require("node:assert/strict");
const mxFactory = require("../deeplinkerp_branding/public/js/mexico_expenses.js");
const cnFactory = require("../deeplinkerp_branding/public/js/china_expenses.js");
function environment(extra = {}) {
    const root = {frappe:{session:{user:"manager"}}, ...extra};
    root.DeepLinkERPMexicoExpenses = mxFactory(root);
    return {root, api:cnFactory(root)};
}
test("CN routes and approval forms remain independent from MX", () => {
    const {api, root} = environment();
    assert.deepEqual(api.forms.map(([id]) => id), ["", "operating", "budget", "purchase"]);
    assert.equal(new Set(Object.values(api.routes)).size, 5);
    for (const route of Object.values(api.routes)) assert(!Object.values(root.DeepLinkERPMexicoExpenses.routes).includes(route));
    assert.equal(api.viewFor(["china-personnel-report"]), "payroll");
    assert.equal(api.viewFor(["mexico-expenses"]), undefined);
    const defaults = api.defaults("2024-02-15");
    assert.equal(defaults.currency, "CNY");
    assert.deepEqual([defaults.from_date, defaults.to_date], ["2024-02-01", "2024-02-29"]);
});
test("CN preferences restore last dates and form, honor URL, and do not touch MX preferences", () => {
    let key;
    const saved = {from_date:"2026-08-01", to_date:"2026-08-31", approval_type:"budget"};
    const {api} = environment({
        localStorage:{getItem(k) {key=k;return JSON.stringify(saved);}},
        location:{search:"?cn_filters="+encodeURIComponent(JSON.stringify({approval_type:"purchase"}))},
    });
    const wb = Object.create(api.Workbench.prototype);
    Object.assign(wb, {filters:api.defaults("2026-11-18"), companies:["CN A"]});
    wb.restore();
    assert.equal(key, "cn-expenses:preferences:v1:manager");
    assert.equal(wb.filters.from_date, saved.from_date);
    assert.equal(wb.filters.approval_type, "purchase");
    assert.deepEqual(api.validFilters({from_date:"2026-11-31", to_date:"2026-12-01", company:"MX A", approval_type:"expense", currency:"BAD"}, ["CN A"]), {});
});
test("CN company options retain the existing operating service's native permitted scope", async () => {
    const calls=[];
    const {api}=environment({frappe:{call:async (request)=>{calls.push(request);return {message:[{name:"CN A"}]};}}});
    const wb=Object.create(api.Workbench.prototype);
    assert.deepEqual(await wb.loadCompanies(),["CN A"]);
    assert.equal(calls.length,1);
    assert.equal(calls[0].method,"frappe.client.get_list");
    assert.equal(calls[0].args.filters,undefined, "do not silently hide existing legal assignments");
    assert(!("ignore_permissions" in calls[0].args));
});
test("unconnected CN views retain placeholders rather than fabricated amounts", () => {
    const {api}=environment();
    const wb=Object.create(api.Workbench.prototype);
    wb.filters=api.defaults("2026-11-18");wb.grouping="company";
    for (const view of Object.keys(api.routes)) {
        wb.view=view;
        const html=wb.content();
        assert(html.includes("Awaiting data connection"));
        assert(!html.includes("0.00"));
        assert(!html.includes("MX Finance"));
        assert(!html.includes("data-row="));
    }
    wb.view="overview";wb.filters.approval_type="budget";
    assert(wb.content().includes("Budget Amount"));
});
test("CN breadcrumbs keep parent links and make the current page non-clickable", () => {
    const links=[], items=[];
    let route=["china-personnel-report"], delegated=0;
    const breadcrumbs={set_custom_breadcrumbs(){delegated++;},append_breadcrumb_element:(url,label)=>links.push({url,label}),$breadcrumbs:{append:item=>items.push(item)}};
    const {api}=environment({frappe:{breadcrumbs,get_route:()=>route},document:{createElement:tag=>({tag,children:[],attrs:{},appendChild(child){this.children.push(child);},setAttribute(k,v){this.attrs[k]=v;}})}});
    api.installBreadcrumbs();api.installBreadcrumbs();breadcrumbs.set_custom_breadcrumbs({});
    assert.deepEqual(links.map(x=>x.url),["/desk/china-expenses","/desk/china-expense-reports"]);
    const current=items[0].children[0];
    assert.equal(current.attrs["aria-current"],"page");assert.equal(current.tag,"span");
    assert.equal(current.textContent,"Personnel Analysis");
    route=["mexico-expenses"];breadcrumbs.set_custom_breadcrumbs({});assert.equal(delegated,1);
});

test("late initialization cannot consume the new route filters and returning retries", async () => {
    let release, route="china-expenses";
    const frappe={get_route:()=>[route],route_options:{cn_filters:JSON.stringify({status:"pending"})}};
    const {api}=environment({frappe});
    const wb=Object.create(api.Workbench.prototype);
    Object.assign(wb,{wrapper:{page_name:route},filters:api.defaults("2026-11-19"),
        loadCompanies:()=>new Promise(r=>{release=r;}),refresh:async()=>{},loadError:()=>assert.fail("stale error")});
    const old=wb.initialize();route="china-expense-review";release(["A"]);await old;
    assert.equal(wb.initialized,undefined);assert.ok(frappe.route_options.cn_filters);
    route="china-expenses";wb.loadCompanies=async()=>["B"];wb.show();await wb.ready;
    assert.equal(wb.filters.status,"pending");assert.equal(frappe.route_options.cn_filters,undefined);
});
test("superseded company load cannot overwrite current data or paint a late error", async () => {
    let rejectOld;
    const {api}=environment({frappe:{get_route:()=>["china-expenses"]}});
    const wb=Object.create(api.Workbench.prototype);
    Object.assign(wb,{wrapper:{page_name:"china-expenses"},loadCompanies:()=>new Promise((_,r)=>{rejectOld=r;}),
        restore:()=>{},refresh:async()=>{},loadError:()=>assert.fail("stale error")});
    const old=wb.initialize();wb.loadCompanies=async()=>["New"];
    await wb.initialize();rejectOld(new Error("obsolete"));await old;assert.deepEqual(wb.companies,["New"]);
});
test("native double serialized route options restore directly from URL", () => {
    const {api}=environment({location:{search:"?cn_filters="+encodeURIComponent(JSON.stringify(JSON.stringify({status:"pending"})))}});
    const wb=Object.create(api.Workbench.prototype);
    Object.assign(wb,{filters:api.defaults("2026-11-19"),companies:[]});wb.restore();assert.equal(wb.filters.status,"pending");
});
