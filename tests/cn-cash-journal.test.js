const test = require("node:test");
const assert = require("node:assert/strict");
const factory = require("../deeplinkerp_branding/public/js/cn_cash_journal.js");
const moneyFactory = require("../deeplinkerp_branding/public/js/mexico_expense_money.js");
const {frappeSource, useDeskCurrency} = require("./fixtures/frappe-currency.cjs");

function element() {
    const children = new Map();
    return {
        find(key) { if (!children.has(key)) children.set(key, element()); return children.get(key); },
        html(value) { this.markup = value; return this; },
        text(value) { this.content = value; return this; },
        prop(key, value) { this[key] = value; return this; },
        on(key, handler) { this[key] = handler; return this; },
        empty() { children.clear(); return this; },
        end() { return this; }, append() {}, prepend() {}, remove() {},
        appendTo() { return this; }, removeClass() { return this; }, toggleClass() {},
    };
}

function pageFixture({list = () => ({rows:[], has_next:false}), save = doc => ({...doc, name:"CNCJ-2026-00001"})} = {}) {
    const alerts = [], requests = [], saves = [];
    const root = {__:v=>v, $:element, frappe:{
        call:async ({method, args}) => {
            if (method === "deeplinkerp_branding.services.cn_cash_book.list_page") {
                requests.push(args);
                return {message:await list(args)};
            }
            assert.equal(method, "frappe.client.save");
            saves.push(args.doc);
            return {message:await save(args.doc)};
        },
        ui:{form:{make_control:() => {
            let value;
            return {get_value:()=>value, get_input_value:()=>value, set_value:v=>{value=v;}, parse_validate_and_set_in_model() {}};
        }}},
        utils:{escape_html:s=>s.replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll('"',"&quot;"),
            get_form_link:(_, name)=>"/desk/cn-cash-journal-entry/"+encodeURIComponent(name)},
        datetime:{get_today:()=>"2026-11-19", str_to_user:v=>v},
        format:(v, df, opts, doc)=>doc.currency+" "+Number(v).toFixed(2),
        show_alert:msg=>alerts.push(msg),msgprint:msg=>alerts.push(msg),
    }};
    root.DeepLinkERPMexicoExpenseMoney = moneyFactory(root);
    const field = () => { const label = {}; return {value:"", closest:()=>label}; };
    const workbench = Object.assign(Object.create(factory(root).Workbench.prototype), {
        body:element(), el:element(), rows:[], pageSize:20, start:0, loaded:true, canCreate:true, canWrite:true,
        period:{mode:"all",from:"",to:""}, periodForm:{elements:{mode:field(),from:field(),to:field()}},
        filters:{company:"CN A",currency:"CNY",account:""},
    });
    return {root,workbench,alerts,requests,saves};
}

async function fixture(options) {
    const context = pageFixture(options), {workbench} = context;
    await workbench.addRow();
    const [row] = workbench.rows;
    row.controls.expense.set_value(12.34);
    return {...context,row};
}

test("compact rows show the original classification and three amounts; editing preserves native fields and revision",async()=>{
    const {workbench,saves}=pageFixture();
    workbench.filters.account="legacy";
    workbench.balances={entry:"163000000000000.09"};
    const doc={name:"entry",modified:"old",attachment:"/private/files/test.pdf",account:"legacy",remarks:"keep",
        posting_date:"2026-10-01", journal_category:"A", journal_subcategory:"", journal_detail:"C",
        summary:"<daily>", direction:"income", amount:"25.00", dingtalk_approval_number:"APPROVAL-999"};
    const row=await workbench.renderRow(doc);
    assert.deepEqual(row.controls,{});
    assert.match(row.element.markup,/<td>A<\/td><td><\/td><td>C<\/td>/);
    assert.match(row.element.markup,/&lt;daily>/);
    assert.match(row.element.markup,/CNY 25\.00/);
    assert.match(row.element.markup,/CNY 163,000,000,000,000\.09/);
    assert.match(row.element.markup,/href="\/desk\/cn-cash-journal-entry\/entry"/);
    assert.doesNotMatch(row.element.markup,/APPROVAL-999|legacy|<input|Discard Changes|>Save</);
    await row.element.find(".journal-edit").click();
    assert.deepEqual(Object.keys(row.controls),["posting_date","dingtalk_approval_number","journal_category",
        "journal_subcategory","journal_detail","summary","income","expense"]);
    row.controls.summary.set_value("updated");
    await workbench.saveRow(row);
    assert.equal(saves.length,1);
    for (const key of ["attachment","modified","account","remarks","dingtalk_approval_number"]) assert.equal(saves[0][key],doc[key]);
    assert.equal(saves[0].summary,"updated");
    assert.equal(row.editing,false);
});
test("new income or expense can save without account, approval number or description",async()=>{
    const {row,workbench}=await fixture();
    await workbench.saveRow(row);
    assert.equal(row.doc.name,'CNCJ-2026-00001');assert.equal(row.doc.amount,"12.34");
    assert.equal(row.doc.dingtalk_approval_number,'');assert.equal(row.saving,false);
});
test("double click cannot submit the same row twice during async validation",async()=>{
    const {row,workbench,saves}=await fixture();
    await Promise.all([workbench.saveRow(row),workbench.saveRow(row)]);
    assert.equal(saves.length,1);
});
test("failed saves retain values and loaded modification timestamp for a safe retry",async()=>{
    const {row,workbench,alerts}=await fixture({save:()=>{throw Error('TimestampMismatchError');}});
    row.doc={...row.doc,name:'existing',modified:'old',amount:10};
    await workbench.saveRow(row);
    assert.equal(row.doc.modified,'old');assert.equal(row.controls.expense.get_value(),12.34);
    assert.equal(row.saving,false);assert.equal(workbench.hasChanges(),true);
    assert.equal(alerts.at(-1).indicator,'red');
});
test("two positive columns cannot save or change the underlying direction", async()=>{
    const {row,workbench,alerts,saves}=await fixture();
    row.controls.income.get_value=()=>10;
    await workbench.saveRow(row);
    assert.equal(saves.length,0); assert.match(alerts.at(-1),/either income or expense/);
});
test("refresh, pagination and period changes cannot discard unsaved rows",async()=>{
    const {workbench,alerts,requests,row}=await fixture();
    workbench.hasNext=true;
    await workbench.reload();await workbench.turnPage(1);
    workbench.showPeriod({mode:"single",from:"2020-06",to:"2020-06"});
    await workbench.applyPeriod("single","2020-06","");
    assert.equal(requests.length,0);assert.equal(workbench.start,0);assert.equal(alerts.length,3);
    assert.equal(workbench.periodForm.elements.mode.value,"all");
    assert.equal(workbench.periodForm.elements.from.closest("label").hidden,true);
    assert.equal(workbench.rows[0],row);assert.equal(row.controls.expense.get_value(),12.34);
});

test("single, cross-year range, pagination and all send only applied inclusive month bounds",async()=>{
    const {workbench,requests}=pageFixture({list:()=>({rows:[],has_next:true})});
    const base={...workbench.filters,start:0};
    await workbench.load();
    await workbench.applyPeriod("single","2018-02","");
    await workbench.applyPeriod("range","2023-12","2024-02");
    await workbench.turnPage(1);
    await workbench.applyPeriod("all","2023-12","2024-02");
    const range={...base,from_month:"2023-12",to_month:"2024-02"};
    assert.deepEqual(requests,[base,{...base,from_month:"2018-02",to_month:"2018-02"},range,{...range,start:20},base]);
});

test("invalid periods keep the applied period and page intact",async()=>{
    const {workbench,requests,alerts}=pageFixture();
    workbench.start=20;
    for (const [from,to] of [["2024-01",""],["2024-13","2025-02"],["2025-01","2024-12"]])
        await workbench.applyPeriod("range",from,to);
    assert.equal(requests.length,0);assert.equal(alerts.length,3);
    assert.equal(workbench.start,20);assert.equal(workbench.period.mode,"all");
});

test("summaries and visible counts come from the latest response; null is not zero",async()=>{
    const pending=[];
    const {workbench}=pageFixture({list:()=>new Promise(resolve=>pending.push(resolve))});
    workbench.filters.account="CN Bank";
    const first=workbench.load(), latest=workbench.applyPeriod("single","2025-03","");
    const total=key=>workbench.el.find(`[data-total="${key}"]`).content;
    pending[1]({rows:[],summary:{opening:null,income:"0.00",expense:"10.25",closing:null,count:32}});
    await latest;
    pending[0]({rows:[],summary:{opening:"100",income:"999.99",expense:"8",closing:"456",count:99}});
    await first;
    assert.deepEqual(["opening","income","expense","closing"].map(total),["—","CNY 0.00","CNY 10.25","—"]);
    assert.equal(workbench.el.find(".journal-count").content," · Visible transactions: 32");
    assert.equal(workbench.el.find(".journal-scope").content,"2025-03");
    workbench.filters.company="";
    await workbench.load();
    assert.deepEqual(["opening","income","expense","closing"].map(total),["—","—","—","—"]);
    assert.equal(workbench.el.find(".journal-count").content,"");
});

test("multi-account ledger omits running balances; readers can open details but cannot edit or add",async()=>{
    const doc={name:"a",posting_date:"2025-01-03",direction:"expense",amount:"5.20"};
    const {workbench,saves}=pageFixture({list:()=>({rows:[doc], balances:{a:"123.45"},
        summary:{income:"0.00",expense:"5.20",opening:null,closing:null,count:1}})});
    workbench.canWrite=workbench.canCreate=false;
    await workbench.load();
    const [row]=workbench.rows, header=workbench.el.find("thead tr").markup;
    assert.match(header,/Journal Category<\/th><th scope="col">Journal Subcategory<\/th><th scope="col">Journal Detail Category/);
    assert.doesNotMatch(header,/Running Balance/);
    assert.doesNotMatch(row.element.markup,/journal-edit|journal-balance|123\.45/);
    assert.match(row.element.markup,/journal-detail/);
    await workbench.addRow();await workbench.editRow(row);await workbench.saveRow(row);
    assert.equal(workbench.rows.length,1);assert.deepEqual(row.controls,{});assert.equal(saves.length,0);
});

test("saving with another draft clears stale totals until that draft is discarded",async()=>{
    const summary={opening:null,income:"0.00",expense:"12.34",closing:null,count:1};
    const {workbench,row,requests}=await fixture({list:()=>({rows:[],summary})});
    await workbench.addRow();
    const other=workbench.rows[1];
    other.controls.summary.set_value("unsaved");
    await workbench.saveRow(row);
    assert.equal(requests.length,0);
    assert.equal(workbench.el.find('[data-total="expense"]').content,"—");
    assert.equal(other.controls.summary.get_value(),"unsaved");
    await workbench.discardRow(other);
    assert.equal(requests.length,1);
    assert.equal(workbench.el.find('[data-total="expense"]').content,"CNY 12.34");
});

test("unsafe amounts stay exact in display and cannot be posted by inline editing",async()=>{
    const {workbench,saves,alerts}=pageFixture();
    const row=await workbench.renderRow({name:"large",direction:"income",amount:"99999999999999.99",posting_date:"2025-01-01"});
    assert.match(row.element.markup,/99,999,999,999,999\.99/);
    await workbench.editRow(row);
    assert.equal(row.editing,false);
    await workbench.applyPeriod("single","2025-01","");
    await workbench.addRow();
    const [draft]=workbench.rows;
    assert.equal(draft.controls.posting_date.get_value(),"2025-01-01");
    draft.controls.expense.set_value(1e12);
    await workbench.saveRow(draft);
    assert.equal(saves.length,0);assert.match(alerts.at(-1),/cannot be edited safely/);
    assert.equal(draft.editing,true);
});

test("Desk comma-decimal Currency round trip preserves canonical cents on a summary-only edit",
    {skip:frappeSource ? false : "Set FRAPPE_JS_ROOT to run against an installed Desk checkout"}, async()=>{
    const {root,workbench,saves}=pageFixture();
    useDeskCurrency(root,"#.###,##");
    workbench.filters.account="Bank";
    const doc={name:"a",modified:"original",posting_date:"2026-11-19",direction:"expense",amount:"12.34"};
    workbench.balances={a:"1234.56"};
    workbench.renderSummary({opening:"1246.90",expense:"12.34",income:"0.00",closing:"1234.56",count:1});
    assert.equal(workbench.el.find('[data-total="expense"]').content,"CNY 12,34");
    assert.equal(workbench.el.find('[data-total="closing"]').content,"CNY 1.234,56");
    const row=await workbench.renderRow(doc);
    assert.match(row.element.markup,/CNY 12,34/);
    assert.match(row.element.markup,/CNY 1\.234,56/);
    await workbench.editRow(row);
    assert.equal(row.controls.expense.get_input_value(),"12,34");
    assert.equal(row.controls.expense.get_value(),12.34);
    assert.equal(workbench.hasChanges(),false);
    row.controls.summary.set_value("only the description changed");
    await workbench.saveRow(row);
    assert.deepEqual(saves.map(({direction,amount,modified})=>({direction,amount,modified})),
        [{direction:"expense",amount:"12.34",modified:"original"}]);
    assert.equal(saves[0].summary,"only the description changed");
    const changed=await workbench.renderRow(doc);
    await workbench.editRow(changed);
    changed.controls.expense.$input.val("1.234,56");
    await workbench.saveRow(changed);
    assert.equal(saves[1].amount,"1234.56");
    assert.equal(saves[1].direction,"expense");

    root.frappe.boot.sysdefaults.number_format="#.###";
    const lossy=await workbench.renderRow(doc);
    await workbench.editRow(lossy);
    assert.equal(lossy.editing,false);
    assert.deepEqual(lossy.controls,{});
});

test('unified journal breadcrumbs keep source ownership and standalone pages intact',()=>{
    let route=['china-cash-journal'];
    const entries=[], native=[];
    const root={__:value=>value,frappe:{boot:{expense_cash_journal:true},get_route:()=>route,
        utils:{escape_html:value=>value},breadcrumbs:{
            set_custom_breadcrumbs(options){native.push(options);},
            append_breadcrumb_element(...args){entries.push(args);}
        }}};
    const api=factory(root);
    api.installNavigation();
    const installed=root.frappe.breadcrumbs.set_custom_breadcrumbs;
    api.installNavigation();
    assert.equal(root.frappe.breadcrumbs.set_custom_breadcrumbs,installed);
    installed.call(root.frappe.breadcrumbs,{});
    assert.deepEqual(entries,[['/desk/expense-management?sidebar=Expense%20Management','Expense Management'],[null,'CN Cash Journal Entry']]);
    route=['expense-details'];installed.call(root.frappe.breadcrumbs,{other:true});
    assert.deepEqual(native,[{other:true}]);
    route=['china-cash-journal'];root.frappe.boot.expense_cash_journal=false;
    installed.call(root.frappe.breadcrumbs,{standalone:true});
    assert.deepEqual(native.at(-1),{standalone:true});
    assert.equal(entries.length,2);
});
