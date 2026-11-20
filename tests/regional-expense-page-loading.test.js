const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const publicDir = path.join(__dirname, "../deeplinkerp_branding/public");
const nativeAssets = path.join(__dirname, "../../frappe/frappe/public/js/frappe/assets.js");
const loaders = ["mexico_expenses_page", "china_expenses_page"];

test("regional pages let Frappe version plain asset paths", () => {
    for (const name of loaders) {
        const source = fs.readFileSync(path.join(publicDir, "js", name + ".js"), "utf8");
        const assets = [...source.matchAll(/"(\/assets\/[^\"]+)"/g)].map((match) => match[1]);
        assert(assets.length > 0);
        for (const asset of assets) assert.match(asset, /\.(js|css)$/, asset);
    }
});

// Exercise the installed framework, rather than a frappe.require stub which
// bypasses extension detection, script evaluation and native asset caching.
test("native loader initializes MX and CN in either order and shares assets", {
    skip: !fs.existsSync(nativeAssets) && "Requires the sibling Frappe checkout",
}, async () => {
    for (const order of [["mexico", "china"], ["china", "mexico"]]) {
        const requests = [], pending = [];
        const root = {
            URL, console, location: {origin: "http://erp.test", hostname: "erp.test"},
            _version_number: "test-release", frappe: {
                boot: {}, pages: {}, dom: {freeze() {}, unfreeze() {}},
            },
        };
        root.window = root;
        const context = vm.createContext(root);
        root.document = {
            createElement: () => ({}),
            head: {appendChild(element) {
                requests.push(element.src || element.href);
                queueMicrotask(() => {
                    const url = new URL(element.src || element.href);
                    if (element.src) {
                        const file = url.pathname.replace("/assets/deeplinkerp_branding/", "");
                        vm.runInContext(fs.readFileSync(path.join(publicDir, file), "utf8"), context);
                        const api = file === "js/mexico_expenses.js" ? root.DeepLinkERPMexicoExpenses
                            : file === "js/china_expenses.js" ? root.DeepLinkERPChinaExpenses : null;
                        if (api) api.Workbench = class {
                            constructor(wrapper) { this.wrapper = wrapper; this.shown = 0; }
                            show() { this.shown++; }
                        };
                    }
                    element.onload();
                });
            }},
        };
        vm.runInContext(fs.readFileSync(nativeAssets, "utf8"), context);
        const requireAssets = root.frappe.require;
        root.frappe.require = (...args) => {
            const promise = requireAssets(...args);
            pending.push(promise);
            return promise;
        };
        for (const name of loaders) vm.runInContext(fs.readFileSync(path.join(publicDir, "js", name + ".js"), "utf8"), context);
        for (const region of order) {
            const route = region + "-expenses", wrapper = {};
            root.frappe.pages[route] = {};
            root[region === "mexico" ? "DeepLinkERPMexicoExpensesPage" : "DeepLinkERPChinaExpensesPage"].register(route);
            const page = root.frappe.pages[route];
            page.on_page_load(wrapper);
            while (pending.length) await pending.shift();
            const key = region === "mexico" ? "mx_expenses" : "cn_expenses";
            assert(wrapper[key], `${region} initialized`);
            const workbench = wrapper[key];
            page.on_page_load(wrapper);
            while (pending.length) await pending.shift();
            page.on_page_show(wrapper);
            assert.equal(wrapper[key], workbench, "page instance reused");
            assert.equal(workbench.shown, 1);
        }
        assert.equal(requests.filter((url) => new URL(url).pathname.endsWith("/mexico_expenses.js")).length, 1);
        for (const url of requests) assert.equal(new URL(url).searchParams.get("v"), "test-release");
        assert(root.DeepLinkERPMexicoExpenseMoney);
        assert(root.DeepLinkERPMexicoExpenseMappings);
        assert(root.DeepLinkERPChinaExpenses);
    }
});

test("CN approval workspace shares range controls and defers mounting after route leave",()=>{
 const pending=[],assets=[],instances=[];let route='china-expenses';
 const root={frappe:{boot:{cn_budget_workbench:true},pages:{'china-expenses':{}},get_route:()=>[route],require(paths,done){assets.push(...paths);pending.push(done)}},CNApprovalWorkbench:{Workbench:class{constructor(wrapper,name){this.shown=0;this.hidden=0;instances.push(name)}show(){this.shown++}hide(){this.hidden++}}}};
 const context=vm.createContext(root);
 vm.runInContext(fs.readFileSync(path.join(publicDir,'js/china_expenses_page.js'),'utf8'),context);
 root.DeepLinkERPChinaExpensesPage.register(route);
 const wrapper={},page=root.frappe.pages[route];page.on_page_load(wrapper);page.on_page_show(wrapper);
 assert.equal(instances.length,0);
 route='mexico-expenses';pending.shift()();assert.equal(instances.length,0);
 route='china-expenses';page.on_page_show(wrapper);assert.deepEqual(instances,['china-expenses']);
 page.on_page_hide(wrapper);assert.equal(wrapper.cn_expenses.hidden,1);
 page.on_page_show(wrapper);assert.equal(wrapper.cn_expenses.shown,1);
 assert.deepEqual(assets,['/assets/deeplinkerp_branding/css/mexico_expenses.css','/assets/deeplinkerp_branding/js/mexico_expense_date_range.js','/assets/deeplinkerp_branding/js/mexico_expense_money.js','/assets/budget/css/cn_approval_workbench.css','/assets/budget/js/cn_approval_workbench.js','/assets/budget/js/cn_approval_management.js']);
});
