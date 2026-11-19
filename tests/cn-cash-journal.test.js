const test = require("node:test");
const assert = require("node:assert/strict");
const factory = require("../deeplinkerp_branding/public/js/cn_cash_journal.js");
function fixture(call=async ({args})=>({message:{...args.doc,name:"CNCJ-2026-00001",modified:"new"}})) {
    const alerts=[];
    const api=factory({__:value=>value, frappe:{call, show_alert:msg=>alerts.push(msg),msgprint:msg=>alerts.push(msg)}});
    const values={posting_date:"2026-11-19",dingtalk_approval_number:"",direction:"expense",amount:12.34,summary:""};
    const row={doc:{company:"CN A",currency:"CNY"},controls:Object.fromEntries(Object.entries(values).map(([key,value])=>[key,{
        get_value:()=>value,get_input_value:()=>value,parse_validate_and_set_in_model:async()=>{},set_value:async()=>{}
    }])),element:{find:()=>({prop:()=>{}})}};
    const workbench=Object.create(api.Workbench.prototype);
    workbench.rows=[row];workbench.updateRow=()=>{};
    return {api,row,workbench,alerts};
}
test("table uses the five requested columns and preserves non-table legacy fields and revision",()=>{
    const {api}=fixture();
    assert.deepEqual(api.columns.map(([field])=>field),['posting_date','dingtalk_approval_number','direction','amount','summary']);
    const row={doc:{name:'entry',modified:'old',attachment:'/private/files/test.pdf',account:'legacy',remarks:'keep'}};
    const saved=api.payload(row,{amount:20});
    assert.equal(saved.modified,'old');assert.equal(saved.attachment,row.doc.attachment);
    assert.equal(saved.account,'legacy');assert.equal(saved.remarks,'keep');assert.equal(saved.amount,20);
});
test("new income or expense can save without account, approval number or description",async()=>{
    const {row,workbench}=fixture();
    await workbench.saveRow(row);
    assert.equal(row.doc.name,'CNCJ-2026-00001');assert.equal(row.doc.amount,12.34);
    assert.equal(row.doc.dingtalk_approval_number,'');assert.equal(row.saving,false);
});
test("double click cannot submit the same row twice during async validation",async()=>{
    let calls=0;
    const {row,workbench}=fixture(async({args})=>{calls++;return {message:{...args.doc,name:'saved'}};});
    await Promise.all([workbench.saveRow(row),workbench.saveRow(row)]);
    assert.equal(calls,1);
});
test("failed saves retain values and loaded modification timestamp for a safe retry",async()=>{
    const {row,workbench,alerts}=fixture(async()=>{throw Error('TimestampMismatchError');});
    row.doc={...row.doc,name:'existing',modified:'old',amount:10};
    await workbench.saveRow(row);
    assert.equal(row.doc.modified,'old');assert.equal(row.controls.amount.get_value(),12.34);
    assert.equal(row.saving,false);assert.equal(workbench.hasChanges(),true);
    assert.equal(alerts.at(-1).indicator,'red');
});
test("refresh and pagination cannot discard edited rows",()=>{
    const {workbench,alerts}=fixture();let loaded=false;
    workbench.load=()=>{loaded=true;};workbench.start=0;workbench.hasNext=true;
    workbench.reload();workbench.turnPage(1);
    assert.equal(loaded,false);assert.equal(workbench.start,0);assert.equal(alerts.length,2);
});
