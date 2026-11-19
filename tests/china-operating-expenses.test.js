const test = require("node:test");
const assert = require("node:assert/strict");
const nativeFactory = require("../deeplinkerp_branding/public/js/operating_expenses.js");
const factory = require("../deeplinkerp_branding/public/js/china_operating_expenses.js");
function environment(extra={}) {
    const root={...extra};root.DeepLinkERPOperatingExpenses=nativeFactory(root);
    return {root,api:factory(root)};
}
test("only operating/all forms connect to the existing service; payroll stays unconnected",()=>{
    const {api}=environment();
    for(const form of ["","operating"]) assert(api.connected({approval_type:form},"overview"));
    for(const form of ["budget","purchase"]) assert(!api.connected({approval_type:form},"overview"));
    assert(!api.connected({approval_type:"operating"},"payroll"));
});
test("CN list and review use application dates and exact native filter fields without currency conversion",async()=>{
    const calls=[];const {api}=environment({frappe:{call:async request=>{calls.push(request);return {message:{rows:[]}};}}});
    const wb={view:"details",start:20,filters:{from_date:"2026-11-01",to_date:"2026-11-30",company:"CN",status:"all",currency:"USD",quick_tab:"paid",keyword:"A"}};
    await api.load(wb);
    assert.match(calls[0].method,/operating_expenses.get_operating_expenses$/);
    assert.deepEqual(calls[0].args,{filters:{date_from:"2026-11-01",date_to:"2026-11-30",company:"CN",approval_state:"",quick_tab:"paid",keyword:"A",company_review:"",application_type:""},page_length:20,start:20});
    assert.equal(api.filters(wb.filters,"review").quick_tab,"reconciliation");
    await api.load({...wb,view:"overview"});
    assert.match(calls[1].method,/get_operating_expense_analysis$/);
    assert(!("page_length" in calls[1].args));
});
test("unknown payments render pending review and a known subtotal, never a false total",()=>{
    const {api}=environment();
    const html=api.amount({paid_amount:null,known_totals:{paid_amount:"12.35"}},"paid_amount");
    assert(html.includes("Pending Review"));assert(html.includes("Known subtotal"));assert(html.includes("12.35"));
    assert(!html.includes("0.00"));
    const lines=api.moneyLines({CNY:{amount:"100"},USD:{amount:"5"}},"amount");
    assert(lines.includes("CNY"));assert(lines.includes("USD"));assert(!lines.includes("105"));
});
test("existing drawer receives the same source identity, tab and post-save refresh",()=>{
    const calls=[];const {api}=environment({DeepLinkERPOperatingExpenseDrawer:{open:(...args)=>calls.push(args)}});
    const refresh=()=>{};api.openSource("source-1","payments",refresh);
    assert.deepEqual(calls,[["source-1",refresh,"payments"]]);
});
test("export preserves the same native filters including review and permission-controlled export method",async()=>{
    const calls=[];const {api}=environment({DeepLinkERPPurchaseOrderExport:{
        fetchNativeWorkbook:async (root,args,method)=>{calls.push({args,method});return "workbook";},
        downloadWorkbook:(root,book)=>{assert.equal(book,"workbook");},
    }});
    const wb={view:"review",filters:{status:"active",company:"CN",from_date:"2026-11-01",to_date:"2026-11-30"}};
    await api.exportCurrent(wb);
    assert.deepEqual(JSON.parse(calls[0].args.filters),api.filters(wb.filters,wb.view));
    assert.match(calls[0].method,/operating_expenses.export_operating_expenses$/);
});
