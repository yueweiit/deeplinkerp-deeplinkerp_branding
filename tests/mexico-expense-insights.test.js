const test=require('node:test');
const assert=require('node:assert/strict');
const workbench=require('../deeplinkerp_branding/public/js/mexico_expenses.js');
const renderer=require('../deeplinkerp_branding/public/js/mexico_expense_insights.js');
const api=workbench({});
test('monthly trend defaults to six complete months, including year boundary',()=>{
 assert.deepEqual(api.reportDefaultRange('2026-01-18','trend'),{from_date:'2025-07-01',to_date:'2025-12-31'});
 assert.deepEqual(api.reportDefaultRange('2026-11-18','payroll'),{from_date:'2026-11-01',to_date:'2026-11-30'});
});
test('personnel first entry inherits dates but not the shared approval form',()=>{
 const saved={from_date:'2026-09-01',to_date:'2026-09-30',approval_type:'expense'};
 const local=workbench({frappe:{session:{user:'boss'}},localStorage:{getItem:key=>key.endsWith(':payroll')?null:JSON.stringify(saved)}});
 const wb=Object.create(local.Workbench.prototype);wb.report='payroll';wb.filters={approval_type:''};wb.context={approval_forms:[]};
 wb.restorePreferences();assert.equal(wb.filters.approval_type,'');assert.equal(wb.filters.from_date,saved.from_date);
});
test('management drilldown preserves exact dates, company and confirmed scope',async()=>{
 let target;
 const wb={report:'payroll',effectiveFilters:()=>({approval_type:'operating',currency:'USD',category:'personnel'}),navigate:(view,filters)=>target={view,filters}};
 await api.Workbench.prototype.click.call(wb,{target:{closest:()=>({dataset:{insight:'company',key:'Company A',from:'2026-10-01',to:'2026-10-18'}})}});
 assert.equal(target.filters.company,'Company A');assert.equal(target.filters.to_date,'2026-10-18');assert.equal(target.filters.confirmed_only,'1');assert.equal(target.filters.approval_type,'operating');
 await api.Workbench.prototype.click.call(wb,{target:{closest:()=>({dataset:{insight:'review'}})}});
 assert.equal(target.view,'review');assert.equal(target.filters.status,'active');assert.equal(target.filters.confirmed_only,'');
});
test('report labels, missing comparison base and source names render safely',()=>{
 const render=renderer({DeepLinkERPMexicoExpenseMoney:{format:v=>String(v)}}).render;
 const data={total:'10',company_count:1,included:{applications:1,lines:1},pending:{applications:2,known_amount:'0',unknown_lines:1},companies:[{key:'<img>',current:'10',share:'100'}],trend:[{month:'2026-11',amount:'10',partial:true}],company_changes:[],movers:[],comparison:{from_date:'2026-11-01',to_date:'2026-11-18',previous_from:'2026-10-01',previous_to:'2026-10-18',mode:'month_to_date',current:'10',previous:'0',delta:'10',percent:null,current_applications:1,previous_applications:0}};
 const html=render(data,{currency:'MXN',approval:'All Approval Forms',company:'All',basis:'Period',kind:'company',categories:[],subcategories:[]});
 assert.ok(html.includes('No prior-period base'));assert.ok(html.includes('Partial Month'));assert.ok(html.includes('&lt;img&gt;'));assert.ok(!html.includes('<img>'));assert.ok(html.includes('1 lines have unconfirmed amounts'));
});
test('analysis failure or a changed snapshot leaves the original summary table available',async()=>{
 for(const mode of ['failure','changed','ok']) {
  let captured;
  const local=workbench({frappe:{call:async({method})=>{
   if(method.endsWith('get_report'))return {message:{rows:[{total:'10.00'}],columns:[]}};
   if(mode==='failure')throw new Error('FX unavailable');
   return {message:{total:mode==='changed'?'11.00':'10.00'}};
  }}});
  const wb=Object.create(local.Workbench.prototype);
  Object.assign(wb,{context:{},view:'reports',report:'company',epoch:0,filters:{from_date:'2026-11-01',to_date:'2026-11-18'},
   effectiveFilters(){return this.filters;},shell(){},mountApprovalSidebar(){},rememberFilters(){},reportTable(data){captured=data;return 'table';},el:{querySelector:()=>({innerHTML:''})}});
  await wb.refresh();
  assert.equal(captured.rows[0].total,'10.00');
  if(mode==='ok')assert.equal(captured.insights.total,'10.00');
  else {assert.equal(captured.insightsUnavailable,true);assert.equal(captured.insights,undefined);}
 }
});
test('legacy payroll bookmark redirects and keeps explicit filters',()=>{
 const vm=require('node:vm'),fs=require('node:fs');let destination;
 const context={URLSearchParams,location:{search:'?mx_filters='+encodeURIComponent(JSON.stringify({approval_type:'operating',from_date:'2026-09-01',to_date:'2026-09-30'}))},
  frappe:{pages:{'mexico-personnel-expenses':{}},get_route:()=>['mexico-personnel-expenses'],set_route:(...args)=>destination=args}};
 vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../deeplinkerp_branding/deeplinkerp_branding/page/mexico_personnel_expenses/mexico_personnel_expenses.js'),'utf8'),context);
 context.frappe.pages['mexico-personnel-expenses'].on_page_load();
 assert.equal(destination[0],'mexico-personnel-report');assert.equal(destination[1].mx_filters.approval_type,'operating');
});
