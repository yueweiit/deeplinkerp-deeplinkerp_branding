const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const grid = require("../deeplinkerp_branding/public/js/sales_order_list.js");

test("sales search uses CRM and ERP identities without treating ODT as a contract", () => {
 const allowed = new Set(["name","custom_crm_order_no","customer_name","custom_odt"]);
 const query = grid.buildQuery({fields:["name"],filters:[],or_filters:[]}, {search:"CRM-001"}, allowed);
 assert.deepEqual(query.or_filters.map(f => f[1]), [...allowed]);
 assert.ok(!query.or_filters.some(f => f[1] === "po_no"));
});

test("mine and finance scopes remain inside native filters and never include cancelled workflow states", () => {
 global.frappe = {session:{user:"finance@example.test"}};
 const base={filters:[["Sales Order","company","=","MX"]]};
 const args=grid.transformQuery(base,{salesView:"finance",allowed:new Set(["custom_process_status"]),quick:{}});
 assert.equal(args.filters[0][3],"MX");
 assert.deepEqual(args.filters[1][3],["Pending Deposit Confirmation","Deposit Confirmation Processing"]);
 assert.deepEqual(args.filters.slice(2),[['Sales Order','docstatus','=',1],['Sales Order','status','not in',['Closed','Cancelled','Stopped','On Hold']]]);
});

test("detail output escapes source text and only renders permission-filtered item fields", () => {
 const html=grid.detailsHTML({header:{name:'SO-1',currency:'MXN',customer_name:'<img src=x onerror=bad()>'},item_fields:["item_code","qty","uom"],items:[{item_code:'<script>bad()</script>',qty:0,uom:'件',rate:98765}],attachments:[{file_url:'javascript:bad()',file_name:'bad'}]});
 assert.ok(html.includes('&lt;img'));
 assert.ok(html.includes('&lt;script&gt;'));
 assert.ok(!html.includes('98765'));
 assert.ok(!html.includes('javascript:bad'));
 assert.ok(html.includes('0 件'));
});

test("sales detail offers mapped payment creation only when permitted and labels zero-value orders", () => {
 const detail={header:{name:'SO-1',grand_total:0,currency:'MXN'},item_fields:[],items:[],can_create_payment_entry:true};
 const html=grid.detailsHTML(detail);
 assert.match(html,/dlp-sales-payment-new[^>]*>新增收款<\/button>/);
 assert.match(html,/订单金额为 0，请先核实销售价格/);
 assert.match(html,/dlp-sales-receipts-body/);
 assert.doesNotMatch(grid.detailsHTML({...detail,can_create_payment_entry:false}),/dlp-sales-payment-new/);
});

test("detail receipts escape identifiers and distinguish draft, submitted and hidden records", () => {
 const html=grid.receiptsHTML({receipts:[{name:'PE-<bad>',docstatus:0,posting_date:'2026-10-16',allocated_amount:20,currency:'MXN'},{name:'PE-2',docstatus:1,allocated_amount:10,currency:'MXN'}],receipts_complete:true});
 assert.match(html,/PE-&lt;bad&gt;.*草稿.*20\.00 MXN.*PE-2.*已提交/s);
 assert.doesNotMatch(html,/<bad>/);
 assert.match(grid.receiptsHTML({receipts:[],receipts_complete:false}),/暂无关联收款记录.*部分收款记录不可见/s);
 assert.match(grid.receiptsHTML({reason:'denied'}),/收款记录不可用/);
});

test("receipt submit action is present only for server-authorized drafts",()=>{
 const review={receipts:[{name:'PE-1',docstatus:0},{name:'PE-2',docstatus:1}]};
 assert.doesNotMatch(grid.receiptsHTML(review),/dlp-sales-receipt-submit/);
 const html=grid.receiptsHTML(review,{'PE-1':{can_submit:true},'PE-2':{can_submit:true}});
 assert.equal((html.match(/dlp-sales-receipt-submit/g)||[]).length,1);
 assert.match(html,/dlp-sales-receipt-submit[^>]*data-name="PE-1"/);
 assert.match(grid.detailsHTML({header:{name:'SO',advance_paid:0},items:[],item_fields:[]}),/data-sales-field="advance_paid"/);
});

test('late initial receipt reads and errors cannot replace the refreshed submitted state',async()=>{
 const previous=global.frappe;
 try {
  for(const failOld of [false,true]) {
   const reads=[];
   global.frappe={call:request=>new Promise((resolve,reject)=>reads.push({request,resolve,reject}))};
   const body={html(value){this.content=value;},text(value){this.content=value;}};
   const dialog={$wrapper:{find:()=>body}};
   const older=grid.loadDetailReceipts(dialog,'SO-1'),newer=grid.loadDetailReceipts(dialog,'SO-1');
   reads[2].resolve({message:{orders:[{receipts:[{name:'PE-1',docstatus:1,allocated_amount:100,currency:'MXN'}]}]}});
   reads[3].resolve({message:{}});await newer;
   const submitted=body.content;assert.match(submitted,/PE-1.*已提交.*100\.00 MXN/);
   if(failOld)reads[0].reject(new Error('late network failure'));
   else reads[0].resolve({message:{orders:[{receipts:[]}]}});
   reads[1].resolve({message:{}});await older;
   assert.equal(body.content,submitted);
  }
 } finally {global.frappe=previous;}
});

test("main quantity summary renders authorized totals without units and removes the price renderer", () => {
 const render=grid.renderValue;
 for(const [doc,expected] of [[{dlp_quantity:3,dlp_uom:'件'},'3'],[{dlp_quantity:3.75,dlp_multiple_units:true},'3.75'],[{dlp_multiple_units:true},'—'],[{dlp_quantity:0,dlp_uom:'件'},'0']])assert.equal(render("dlp_quantity",doc,{translate:x=>x}),expected);
 assert.equal(mountedReleaseFixture().config.renderValue("dlp_rate",{dlp_item_count:3,dlp_rate:999,currency:"MXN"},{translate:x=>x}),undefined);
 assert.ok(!grid.COLUMNS.some(column=>column.fieldname==='dlp_rate'));
});

test("sales presets retain an explicit details action and ERP account identity fields", () => {
 for(const columns of Object.values(grid.presets)) assert.ok(columns.includes("dlp_actions"));
 assert.ok(grid.presets.tail.includes("owner"));
 assert.ok(grid.presets.middle.includes("dlp_sales_person"));
 assert.ok(!grid.COLUMNS.some(c=>/deposit_required|deposit_difference/.test(c.fieldname)));
});

test('default finance columns show approval while delivery status stays optional',()=>{
 assert.ok(!grid.presets.main.includes('status'));
 assert.deepEqual(grid.presets.finance,['name','customer_name','advance_paid','custom_process_status','grand_total','dlp_receipts','dlp_last_confirmation','dlp_actions']);
 assert.ok(grid.presets.main.includes('custom_process_status'));
 const query=grid.buildQuery({filters:[['Sales Order','status','=','Closed'],['Sales Order','custom_process_status','=','Pending Production']],or_filters:[]},{},new Set(['status','custom_process_status']));
 assert.deepEqual(query.filters,[['Sales Order','status','=','Closed'],['Sales Order','custom_process_status','=','Pending Production']]);
});

test('saved sales columns prune obsolete price while keeping version-3 status, order and density',()=>{
 const allowed=new Set([...grid.COLUMNS.map(col=>col.fieldname),'party_account_currency']);
 const upgraded=grid.normalizePreferences({version:2,density:'standard',columns:['name','customer_name','custom_process_status','status','dlp_rate','grand_total']},allowed);
 assert.deepEqual(upgraded.columns,['name','customer_name','advance_paid','custom_process_status','grand_total']);
 assert.equal(upgraded.density,'standard');
 const hidden=grid.normalizePreferences({...upgraded,columns:['name','customer_name']},allowed);
 assert.deepEqual(hidden.columns,['name','customer_name'],'a deliberate hide after the upgrade remains respected');
 for(const columns of [['name','status','dlp_rate','customer_name','grand_total'],['name','grand_total','dlp_rate','customer_name']]){
  const saved=grid.normalizePreferences({version:3,density:'standard',columns},allowed);
  assert.deepEqual(saved.columns,columns.filter(field=>field!=='dlp_rate').flatMap(field=>field==='customer_name'?[field,'advance_paid']:[field]));
  assert.equal(saved.version,4);assert.equal(saved.density,'standard');
 }
});

test('financial grouping uses process state rather than release permission and unknown states show a dash',()=>{
 for(const raw of ['Pending Deposit Confirmation','Deposit Confirmation Processing']) assert.equal(grid.financialLabel(raw),'待财务审核');
 for(const raw of ['Pending Production','Pending Final Payment','Deliverable','Partially Delivered','Completed']) assert.equal(grid.financialLabel(raw),'已放行生产');
 assert.equal(grid.financialLabel('Rejected'),'已驳回');
 for(const raw of ['Pending Confirmation','Draft','Closed','Cancelled',undefined]) assert.equal(grid.financialLabel(raw),'—');
 assert.match(grid.renderValue('custom_process_status',{custom_process_status:'Pending Production',dlp_finance:{can_release:false}},{translate:x=>x}),/已放行生产/);
});

test('sync warnings stay supplemental to approval and permission denial is never a sync failure',()=>{
 const render=(field,doc)=>grid.renderValue(field,doc,{translate:x=>x});
 const processing={custom_process_status:'Deposit Confirmation Processing',dlp_finance:{can_release:false,process_status:'Deposit Confirmation Processing',last_confirmation:{user:'finance',creation:'today',status:'Processing'}}};
 assert.match(render('custom_process_status',processing),/待财务审核/);
 assert.match(render('dlp_last_confirmation',processing),/finance.*today.*CRM\/MES 同步中/s);
 const failed={custom_process_status:'Pending Deposit Confirmation',dlp_finance:{can_release:false,last_confirmation:{user:'finance',creation:'today',status:'Failed'}}};
 assert.match(render('dlp_last_confirmation',failed),/CRM\/MES 同步失败/);
 assert.match(render('dlp_sync',failed),/CRM\/MES 同步失败/);
 const financeOnly={custom_process_status:'Pending Deposit Confirmation',dlp_finance:{can_release:true,sync_status:'Failed',last_confirmation:null}};
 assert.match(render('dlp_sync',financeOnly),/CRM\/MES 同步失败/);
 const mesFailed={...failed,dlp_finance:{...failed.dlp_finance,sync_error:{code:'ERP_SALES_ORDER_IDENTITY_CONFLICT',message:'MES 订单映射冲突，请核对 ERP/CRM 单号与 MES 既有映射，处理冲突后重试。'}}};
 assert.match(render('dlp_last_confirmation',mesFailed),/MES 订单映射冲突/);
 assert.match(render('dlp_sync',mesFailed),/MES 订单映射冲突/);
 assert.doesNotMatch(render('dlp_sync',mesFailed),/CRM\/MES 同步失败/);
 const denied={custom_process_status:'Pending Production',dlp_finance:{can_release:false,reason:'no permission'}};
 assert.equal(render('dlp_last_confirmation',denied),'—');assert.equal(render('dlp_sync',denied),'—');
});

test('financial filters preserve raw native filters and missing source permission yields no rows',()=>{
 const native=['Sales Order','company','=','MX'];
 const controller={salesView:'finance',allowed:new Set(['custom_process_status']),quick:{financial_status:'approved'},list:{clear_checked_items(){}},salesExpanded:new Map()};
 const query=grid.transformQuery({filters:[native]},controller);
 assert.equal(controller.salesView,'all');
 assert.deepEqual(query.filters,[native,['Sales Order','custom_process_status','in',['Pending Production','Pending Final Payment','Deliverable','Partially Delivered','Completed']]]);
 const denied=grid.transformQuery({filters:[]},{salesView:'finance',allowed:new Set(),quick:{}});
 assert.deepEqual(denied.filters,[['Sales Order','name','in',[]]]);
});

test('mounted configuration defaults to finance without resetting saved columns',()=>{
 const f=mountedReleaseFixture();
 f.config.mountControls(f.controller);
 assert.equal(f.controller.salesView,'finance');
 assert.deepEqual(Array.from(f.config.defaultColumns),grid.presets.finance);
 assert.equal(f.config.preferenceVersion,4);
 assert.ok(!f.config.computedFields.includes('dlp_rate'));
 const control=f.config.controls.find(control=>control.fieldname==='financial_status');
 assert.equal(control.permission_field,'custom_process_status');
 assert.deepEqual(JSON.parse(JSON.stringify(f.grid.selectOptions(control,null,x=>x))),[{value:'',label:'全部审核状态'},{value:'pending',label:'待财务审核'},{value:'approved',label:'已放行生产'},{value:'rejected',label:'已驳回'}]);
 assert.ok(!f.config.controls.some(control=>['status','custom_process_status'].includes(control.fieldname)));
 assert.equal(f.config.purchaseChrome,true,'reuse the native advanced-filter disclosure');
 assert.deepEqual(Array.from(f.config.mainFilterFields),['search','customer','financial_status','from_date','to_date','company']);
 assert.ok(!f.config.mainFilterFields.includes('product'),'product stays reachable under more filters');
});

test('sales scope and release controls mount inside the existing personal-column toolbar',()=>{
 const f=mountedReleaseFixture(),nodes=[];
 class Surface {
  constructor(html=''){this.html=html;this.nodes=new Map();nodes.push(this);}
  find(selector){if(!this.nodes.has(selector))this.nodes.set(selector,new Surface());return this.nodes.get(selector);}
  prependTo(parent){this.parent=parent;return this;} appendTo(parent){this.parent=parent;return this;}
  insertBefore(target){this.before=target;return this;} insertAfter(target){this.after=target;return this;}
  on(){return this;} text(){return this;} prop(){return this;}
 }
 f.env.$=html=>new Surface(html);f.controller.$toolbar=new Surface('existing toolbar');
 const columns=f.controller.preferences.columns.slice();f.config.mountControls(f.controller);
 assert.equal(f.controller.$salesViewbar.parent,f.controller.$toolbar);
 assert.match(f.controller.$salesViewbar.html,/class="btn btn-default dlp-sales-reject" disabled>财务驳回<\/button>/);
 assert.equal(f.controller.$salesNotice.after,f.controller.$toolbar);
 assert.equal(nodes.find(node=>node.html.includes('dlp-sales-preset')).parent,f.controller.$toolbar);
 assert.match(nodes.find(node=>node.html.includes('dlp-sales-preset')).html,/<option value="finance">收款与预付<\/option>/);
 assert.deepEqual(f.controller.preferences.columns,columns);
 assert.equal(f.controller.salesView,'finance');
});

test('sales items prioritize seven permitted columns with sequence first, hide version and description, and retain all 52 rows with full hover text',()=>{
 const fields=['idx','item_code','item_name','custom_specifications','custom_version','qty','uom','rate','custom_item_tax_amount','amount','delivery_date','description','custom_product','warehouse'];
 const detail={header:{currency:'MXN'},item_fields:fields,items:Array.from({length:52},(_,i)=>({idx:i+1,item_code:`CODE-${i}`,item_name:'Long <product> name',custom_specifications:'Long specifications',custom_version:'Source version',description:'Complete <description>',warehouse:'Long warehouse',qty:2,uom:'件',rate:12345.6,amount:24691.2,delivery_date:'2026-12-31'}))};
 const html=grid.itemTable(detail);
 assert.equal((html.match(/<tbody>[\s\S]*?<\/tbody>/)?.[0].match(/<tr>/g)||[]).length,52);
 assert.deepEqual([...html.matchAll(/<th data-fieldname="([^"]+)"/g)].map(match=>match[1]),['idx','item_code','item_name','custom_specifications','qty','rate','amount','custom_item_tax_amount','delivery_date','custom_product','warehouse']);
 assert.deepEqual([...html.matchAll(/<th[^>]+>([^<]+)<\/th>/g)].slice(0,7).map(match=>match[1]),['序号','品目编码','品项名','颜色','数量','单价','金额']);
 for(const field of ['item_name','custom_specifications','warehouse'])assert.match(html,new RegExp(`<td data-fieldname="${field}" title="[^"]+"[^>]*><span class="dlp-sales-item-text">`));
 for(const field of ['qty','rate','custom_item_tax_amount','amount'])assert.match(html,new RegExp(`<td data-fieldname="${field}"[^>]+class="[^"]*dlp-sales-item-number`));
 assert.doesNotMatch(html,/data-fieldname="custom_version"|data-fieldname="description"|Complete &lt;description&gt;|Source version/);
 assert.equal(detail.items[0].description,'Complete <description>');assert.equal(detail.items[0].custom_version,'Source version');
 assert.match(html,/2 件/);assert.match(html,/12,345\.60 MXN/);
 assert.doesNotMatch(html,/dlp-sales-items-overview/);
 const denied=grid.itemTable({...detail,item_fields:['item_code']});assert.doesNotMatch(denied,/data-fieldname="amount"|dlp-sales-item-text/);
 const dialog=grid.detailsHTML(detail);assert.match(dialog,/dlp-sales-items-overview/);
 assert.deepEqual([...dialog.matchAll(/<th data-fieldname="([^"]+)"/g)].map(match=>match[1]),[...html.matchAll(/<th data-fieldname="([^"]+)"/g)].map(match=>match[1]));
 assert.deepEqual([...dialog.matchAll(/<th data-fieldname="([^"]+)" class="([^"]*)"/g)].filter(match=>!match[2].includes('dlp-sales-extra-item')).map(match=>match[1]),['idx','item_code','item_name','custom_specifications','qty','rate','amount']);
 assert.doesNotMatch(grid.itemTable({...detail,item_fields:fields.filter(field=>!['custom_version','description'].includes(field))}),/data-fieldname="custom_version"|data-fieldname="description"/);
});

test('overview extra columns are classified by field meaning when primary permissions are omitted',()=>{
 const html=grid.itemTable({header:{currency:'MXN'},item_fields:['idx','qty','custom_item_tax_amount','amount','delivery_date','warehouse'],items:[{idx:1,qty:2,custom_item_tax_amount:3,amount:19,warehouse:'Full warehouse'}]},false);
 for(const field of ['idx','qty','amount'])assert.match(html,new RegExp(`<th data-fieldname="${field}" class="(?![^"]*dlp-sales-extra-item)`));
 for(const field of ['custom_item_tax_amount','delivery_date','warehouse'])assert.match(html,new RegExp(`<th data-fieldname="${field}" class="[^"]*dlp-sales-extra-item`));
});

test('finance reviews supplement all orders when financial columns are visible and ignore stale reviews',async()=>{
 const f=mountedReleaseFixture();f.controller.salesView='all';f.controller.preferences.columns=['name','dlp_receipts'];
 const pending=f.config.onRows(f.controller);
 f.reads[0].resolve({message:{'SO-A':{dlp_product:'Current product'}}});
 await new Promise(setImmediate);
 assert.match(f.reads[1].request.method,/get_finance_release_review$/);
 f.controller.requestId++;
 f.reads[1].resolve({message:{orders:[{name:'SO-A',process_status:'Deposit Confirmation Processing',last_confirmation:{status:'Failed'},receipts:[{name:'stale'}]}]}});await pending;
 assert.equal(f.list.data[0].dlp_finance,undefined);
 assert.equal(f.grid.renderValue('dlp_last_confirmation',f.list.data[0],{translate:x=>x}),'—');
});

test('departed finance list ignores a late supplemental network error',async()=>{
 const f=mountedReleaseFixture();const notices=[];f.controller.$salesNotice={text:value=>notices.push(value)};
 const pending=f.config.onRows(f.controller);
 f.setRoute(['List','Purchase Order','List']);f.config.onRouteChange(f.controller,false);
 f.reads[0].reject(new Error('late offline'));await pending;
 assert.deepEqual(notices,[]);
});

test('real column changes load missing financial detail once and discard stale enrichment',async()=>{
 const f=mountedReleaseFixture();f.controller.salesView='all';f.list.get_checked_items=()=>[];
 f.controller.preferences.columns=['name'];
 const first=f.controller.setColumns(['name','dlp_receipts']);
 assert.match(f.reads[0].request.method,/get_sales_display_details$/);
 const duplicate=f.controller.setColumns(['name','dlp_receipts','dlp_last_confirmation']);
 assert.equal(f.reads.length,1);
 f.reads[0].resolve({message:{}});await new Promise(setImmediate);
 f.reads[1].resolve({message:{orders:[{name:'SO-A',receipts:[{name:'PAY-1'}],last_confirmation:{user:'finance',creation:'today',status:'Success'}}]}});
 await first;await duplicate;
 assert.equal(f.list.data[0].dlp_finance.receipts[0].name,'PAY-1');
 await f.controller.setColumns(['name','dlp_last_confirmation']);assert.equal(f.reads.length,2);
 delete f.list.data[0].dlp_finance;
 const stale=f.controller.setColumns(['name','dlp_receipts']);
 f.reads[2].resolve({message:{}});await new Promise(setImmediate);
 f.controller.requestId++;
 f.reads[3].resolve({message:{orders:[{name:'SO-A',last_confirmation:{status:'Failed'}}]}});await stale;
 assert.equal(f.list.data[0].dlp_finance,undefined);
});

test('missing source permission keeps selected release actions disabled without sending review requests',async()=>{
 const f=releaseSelectionFixture();f.controller.allowed=new Set(['name']);f.controller.list.data=[{name:'denied'}];
 await grid.updateReleaseSelection(f.controller);
 assert.equal(f.reads.length,0);assert.equal(f.button().disabled,true);assert.equal(f.rejectButton().disabled,true);assert.match(f.summary().label,/无权/);
});

test('Sales export transforms match the selected order and translate only financial process cells',()=>{
 const f=mountedReleaseFixture();
 const options=f.config.exportOptions({fields:['grand_total','custom_process_status','name']});
 assert.deepEqual(Object.keys(options.columnTransforms),['3']);
 assert.equal(options.columnTransforms[3].header,'财务审核状态');
 assert.equal(options.columnTransforms[3].value('Completed'),'已放行生产');
 assert.equal(options.columnTransforms[3].value('Cancelled'),'—');
 assert.deepEqual(Object.keys(f.config.exportOptions({fields:['name','grand_total']}).columnTransforms),[]);
});

test('native Select options have explicit empty labels and retain raw status values after refresh',()=>{
 assert.equal(typeof grid.selectOptions,'function');
 const options=grid.selectOptions({fieldname:'custom_process_status',emptyLabel:'全部业务状态'},{options:'\nPending Deposit Confirmation\nPending Production'},x=>x);
 assert.deepEqual(options,[{value:'',label:'全部业务状态'},{value:'Pending Deposit Confirmation',label:'待财务放行'},{value:'Pending Production',label:'生产已放行'}]);
 const status=grid.selectOptions({fieldname:'status',emptyLabel:'全部订单状态'},{options:'\nDraft\nTo Deliver and Bill\nClosed'},x=>x);
 assert.deepEqual(status,[{value:'',label:'全部订单状态'},{value:'Draft',label:'草稿'},{value:'To Deliver and Bill',label:'待交付及开票'},{value:'Closed',label:'已关闭'}]);
});

function releaseSelectionFixture() {
 const reads=[],nodes=new Map(),controller={requestId:1,list:{data:[],get_checked_items(){return this.data;}}};
 controller.root={frappe:{get_route:()=>['List','Sales Order','List'],call:request=>new Promise((resolve,reject)=>reads.push({request,resolve,reject}))}};
 controller.$salesViewbar={find(selector){if(!nodes.has(selector))nodes.set(selector,{text(value){this.label=value;return this;},prop(key,value){this[key]=value;return this;}});return nodes.get(selector);}};
 return {controller,reads,button:()=>nodes.get('.dlp-sales-release'),rejectButton:()=>nodes.get('.dlp-sales-reject'),summary:()=>nodes.get('.dlp-sales-selected')};
}

test('release action stays disabled until authoritative review and excludes released or unauthorized rows',async()=>{
 assert.equal(typeof grid.updateReleaseSelection,'function');
 const f=releaseSelectionFixture();f.controller.list.data=[{name:'released'},{name:'pending'},{name:'denied'}];
 const pending=grid.updateReleaseSelection(f.controller);
 assert.equal(f.button().disabled,true);
 assert.equal(f.reads[0].request.method,'crm_integration.crm_integration.finance_release.get_finance_release_review');
 f.reads[0].resolve({message:{orders:[{name:'released',can_release:false,process_status:'Pending Production'},{name:'pending',can_release:true},{name:'denied',can_release:false,reason:'no permission'},{name:'foreign',can_release:true}]}});
 await pending;
 assert.deepEqual(f.controller.salesReleaseNames,['pending']);
 assert.equal(f.button().disabled,false);assert.match(f.button().label,/1单/);assert.match(f.summary().label,/3.*1.*2/);
 await grid.updateReleaseSelection(f.controller);assert.equal(f.reads.length,1,'repeat rendering does not repeat the unchanged review');
});

test('all released and incomplete or failed review responses never enable production',async()=>{
 assert.equal(typeof grid.updateReleaseSelection,'function');
 for(const response of [{message:{orders:[{name:'A',can_release:false,process_status:'Pending Production'}]}},{message:{orders:[]}},new Error('offline')]){
  const f=releaseSelectionFixture();f.controller.list.data=[{name:'A'}];const pending=grid.updateReleaseSelection(f.controller);
  if(response instanceof Error)f.reads[0].reject(response);else f.reads[0].resolve(response);
  await pending;assert.equal(f.button().disabled,true);assert.deepEqual(f.controller.salesReleaseNames,[]);
 }
});

test('reject is visible for one eligible order, independent of production release eligibility',async()=>{
 const f=releaseSelectionFixture();f.controller.list.data=[{name:'SO-A'}];
 const review=grid.updateReleaseSelection(f.controller);
 assert.equal(f.rejectButton().disabled,true);
 f.reads[0].resolve({message:{orders:[{name:'SO-A',can_release:false,can_reject:true}]}});await review;
 assert.equal(f.button().disabled,true);assert.equal(f.rejectButton().disabled,false);
 assert.equal(f.controller.salesRejectName,'SO-A');
 f.controller.list.data=[{name:'SO-A'},{name:'SO-B'}];
 const multiple=grid.updateReleaseSelection(f.controller);
 f.reads[1].resolve({message:{orders:[{name:'SO-A',can_reject:true},{name:'SO-B',can_reject:true}]}});await multiple;
 assert.equal(f.rejectButton().disabled,true,'list action rejects only one explicitly selected order');
});

test('ineligible order keeps reject disabled and shows the rejection reason, not the release reason',async()=>{
 const f=releaseSelectionFixture();f.controller.list.data=[{name:'QA-ORDER'}];
 const review=grid.updateReleaseSelection(f.controller);
 f.reads[0].resolve({message:{orders:[{name:'QA-ORDER',can_release:false,can_reject:false,reason:'无生产放行权限',reject_reason:'没有取消权限'}]}});await review;
 assert.equal(f.rejectButton().disabled,true);
 assert.match(f.summary().label,/不可驳回：没有取消权限/);
 assert.doesNotMatch(f.summary().label,/无生产放行权限/);
});

test('single releasable order still explains why rejection is unavailable',async()=>{
 const f=releaseSelectionFixture();f.controller.list.data=[{name:'SO-A'}];
 const review=grid.updateReleaseSelection(f.controller);
 f.reads[0].resolve({message:{orders:[{name:'SO-A',can_release:true,can_reject:false,reject_reason:'缺少 CRM 订单编号'}]}});await review;
 assert.equal(f.button().disabled,false);assert.equal(f.rejectButton().disabled,true);
 assert.match(f.summary().label,/不可驳回：缺少 CRM 订单编号/);
});

test('an old review cannot enable a changed selection or a refreshed order',async()=>{
 assert.equal(typeof grid.updateReleaseSelection,'function');
 const f=releaseSelectionFixture();f.controller.list.data=[{name:'A',modified:'old'}];const older=grid.updateReleaseSelection(f.controller);
 f.controller.list.data=[{name:'A',modified:'new'}];f.controller.requestId++;const newer=grid.updateReleaseSelection(f.controller);
 f.reads[1].resolve({message:{orders:[{name:'A',can_release:false}]}});await newer;
 f.reads[0].resolve({message:{orders:[{name:'A',can_release:true}]}});await older;
 assert.equal(f.button().disabled,true);assert.deepEqual(f.controller.salesReleaseNames,[]);
 f.controller.list.data=[];await grid.updateReleaseSelection(f.controller);assert.match(f.summary().label,/未选择/);
});

function mountedReleaseFixture() {
 const engine=require('../deeplinkerp_branding/public/js/compact_list.js'),vm=require('node:vm');
 const f=releaseSelectionFixture();let config,route=['List','Sales Order','List'];
 const env={frappe:{...f.controller.root.frappe,get_route:()=>route,model:{std_fields_list:['name','owner','docstatus']},perm:{has_perm:()=>true},session:{user:'qa'},boot:{sitename:'qa'}},DeepLinkERPCompactList:{...engine,create(value){config=value;return engine.create(value);}}};
 vm.runInNewContext(fs.readFileSync(require.resolve('../deeplinkerp_branding/public/js/sales_order_list.js'),'utf8'),env);
 const list={doctype:'Sales Order',view_name:'List',view:'List',page:{},meta:{fields:grid.COLUMNS.map(col=>({fieldname:col.fieldname,permlevel:0}))},fields:[],data:[{name:'SO-A',modified:'old'}],settings:{},get_args:()=>({filters:[],or_filters:[]}),get_call_args(){return {args:this.get_args()};},no_change:()=>false,render_header(){},refresh(){},get_checked_items(){return this.data;},on_row_checked(){}};
 const controller=env.DeepLinkERPSalesWorkspace.mount(list,env);
 controller.$salesViewbar=f.controller.$salesViewbar;controller.salesExpanded=new Map();
 return {...f,controller,list,config,env,grid:env.DeepLinkERPSalesWorkspace,setRoute(value){route=value;}};
}

test('native dispatch invalidates release eligibility immediately and rejects an earlier response',async()=>{
 const f=mountedReleaseFixture(),older=f.grid.updateReleaseSelection(f.controller);
 f.list.no_change(f.list.get_call_args());
 f.reads[0].resolve({message:{orders:[{name:'SO-A',can_release:true}]}});await older;
 assert.equal(f.button().disabled,true);assert.equal(f.controller.salesReleaseNames.length,0);
 const current=f.grid.updateReleaseSelection(f.controller);
 f.reads[1].resolve({message:{orders:[{name:'SO-A',can_release:true}]}});await current;
 assert.equal(f.button().disabled,false);
 f.list.no_change(f.list.get_call_args());
 assert.equal(f.button().disabled,true,'already eligible selection is disabled at the real dispatch gate');
 assert.equal(f.controller.salesReleaseNames.length,0);
});

test('list rejection revalidates before confirmation and refreshes only after server success',async()=>{
 const f=mountedReleaseFixture();let confirm,refreshes=0,cleared=0;
 f.env.frappe.confirm=(message,callback)=>{assert.match(message,/SO-A/);confirm=callback;};
 f.env.frappe.msgprint=()=>assert.fail('successful rejection must not show an error');
 f.controller.refresh=()=>{refreshes++;};f.list.clear_checked_items=()=>{cleared++;};
 const action=f.grid.rejectSelectedOrder(f.controller);
 f.reads[0].resolve({message:{orders:[{name:'SO-A',can_reject:true}]}});await action;
 assert.equal(f.reads.length,1,'confirmation does not send E1');
 const result=confirm();
 assert.equal(f.reads[1].request.method,'crm_integration.crm_integration.sales_order.reject_sales_order');
 assert.equal(f.reads[1].request.args.sales_order_name,'SO-A');
 f.reads[1].resolve({message:{status:'success'}});await result;
 assert.equal(refreshes,1);assert.equal(cleared,1);assert.equal(f.rejectButton().disabled,true);
});

test('changed list selection cannot execute an old rejection confirmation',async()=>{
 const f=mountedReleaseFixture();let confirm;
 f.env.frappe.confirm=(_,callback)=>{confirm=callback;};
 const action=f.grid.rejectSelectedOrder(f.controller);
 f.reads[0].resolve({message:{orders:[{name:'SO-A',can_reject:true}]}});await action;
 f.list.data=[{name:'SO-B'}];await confirm();
 assert.equal(f.reads.length,1);
});

test('departed sales list ignores late display details without starting another release review',async()=>{
 const f=mountedReleaseFixture(),details=f.config.onRows(f.controller);
 assert.match(f.reads[0].request.method,/get_sales_display_details$/);
 f.setRoute(['List','Purchase Order','List']);f.config.onRouteChange(f.controller,false);
 f.reads[0].resolve({message:{'SO-A':{dlp_product:'Late product'}}});await details;
 assert.equal(f.reads.length,1,'late details must not render or re-start selection review');
 await f.grid.updateReleaseSelection(f.controller,true);
 assert.equal(f.reads.length,1);assert.equal(f.button().disabled,true);
 assert.equal(f.controller.salesReleaseNames.length,0);
});

test('review response verifies live selection even before the next selection callback runs',async()=>{
 const f=releaseSelectionFixture();f.controller.list.data=[{name:'A',modified:'old'}];
 const pending=grid.updateReleaseSelection(f.controller);f.controller.list.data=[{name:'B'}];
 f.reads[0].resolve({message:{orders:[{name:'A',can_release:true}]}});await pending;
 assert.equal(f.button().disabled,true);assert.deepEqual(f.controller.salesReleaseNames,[]);
});

test('empty and over-limit selections make no release-review request',async()=>{
 assert.equal(typeof grid.updateReleaseSelection,'function');
 const f=releaseSelectionFixture();await grid.updateReleaseSelection(f.controller);
 f.controller.list.data=Array.from({length:101},(_,i)=>({name:`SO-${i}`}));await grid.updateReleaseSelection(f.controller);
 assert.equal(f.reads.length,0);assert.equal(f.button().disabled,true);assert.deepEqual(f.controller.salesReleaseNames,[]);
});

test('native selection still updates bulk actions and select-all while sales field headers remain visible',()=>{
 const surface=()=>({visible:true,checked:false,indeterminate:false,toggle(value){this.visible=value;return this;},show(){this.visible=true;return this;},hide(){this.visible=false;return this;},find(){return this;},prop(key,value){this[key]=value;return this;}});
 const subject=surface(),actions=surface();let selected=0,nativeCalls=0;
 const list={doctype:'Sales Order',view_name:'List',view:'List',meta:{fields:grid.COLUMNS.map(col=>({fieldname:col.fieldname,permlevel:0}))},fields:[],data:[{name:'A'},{name:'B'}],settings:{},
  get_args:()=>({filters:[],or_filters:[]}),get_call_args(){return {args:this.get_args()};},render_header(){},refresh(){},
  on_row_checked(){nativeCalls++;this.$list_head_subject=subject;this.$checkbox_actions=actions;this.$checks={length:selected};subject.toggle(!selected);actions.toggle(Boolean(selected));this.bulkVisible=Boolean(selected);}};
 const root={frappe:{get_route:()=>['List','Sales Order','List'],model:{std_fields_list:['name','owner','docstatus']},perm:{has_perm:()=>true},session:{user:'test'},boot:{sitename:'qa'}}};
 grid.mount(list,root);
 for(selected of [1,2,0]){list.on_row_checked();assert.equal(subject.visible,true);assert.equal(actions.visible,false);assert.equal(list.bulkVisible,Boolean(selected));assert.equal(subject.checked,selected===2);assert.equal(subject.indeterminate,selected===1);}
 assert.equal(nativeCalls,3);
});

test("expanded detail cache expires when an order changes or disappears from the current result", () => {
 const controller={list:{data:[{name:"SO-1",modified:"new"}]},salesExpanded:new Map([["SO-1",{header:{modified:"old"}}],["SO-2",{header:{modified:"old"}}]])};
 grid.invalidateExpandedDetails(controller);
 assert.equal(controller.salesExpanded.size,0);
});

test("initial native onboarding closes once and later deliberate opening remains untouched", () => {
 let callback, button, clicks=0, disconnects=0;
 const previous={document:global.document,MutationObserver:global.MutationObserver,frappe:global.frappe};
 global.document={querySelector:()=>({querySelector:()=>button})};
 global.MutationObserver=class {constructor(fn){callback=fn;} observe(){} disconnect(){disconnects++;}};
 global.frappe={get_route:()=>["List","Sales Order","List"]};
 const controller={};
 try {
  grid.dismissAutomaticOnboarding(controller);
  assert.equal(clicks,0);
  button={click:()=>{clicks++;}}; callback();
  assert.equal(clicks,1); assert.equal(disconnects,1); assert.equal(controller.stopInitialOnboarding,null);
 } finally {Object.assign(global,previous);}
});

test("leaving sales before native onboarding arrives disconnects its scoped observer", () => {
 let callback, disconnects=0, route=["List","Sales Order","List"];
 const previous={document:global.document,MutationObserver:global.MutationObserver,frappe:global.frappe};
 global.document={querySelector:()=>({querySelector:()=>null})};
 global.MutationObserver=class {constructor(fn){callback=fn;} observe(){} disconnect(){disconnects++;}};
 global.frappe={get_route:()=>route};
 const controller={};
 try {
  grid.dismissAutomaticOnboarding(controller); route=["List","Purchase Order","List"]; callback();
  assert.equal(disconnects,1); assert.equal(controller.stopInitialOnboarding,null);
 } finally {Object.assign(global,previous);}
});

function viewportFixture() {
 const state={top:318.18,tail:75.5,header:32,height:720,scroll:0}, frames=new Map(), listeners=new Map(), observers=[];
 const values=new Map(), style={getPropertyValue:key=>values.get(key)||'',setProperty:(key,value)=>values.set(key,value),removeProperty:key=>values.delete(key)};
 const main={scrollTop:0,clientTop:0,get clientHeight(){return state.height;},getBoundingClientRect:()=>({top:0,bottom:state.height}),parentElement:null};
 const tableHeight=()=>Math.min(800,parseFloat(values.get('--dlp-test-result-max-height'))||400);
 const list={scrollTop:0,parentElement:main,getBoundingClientRect:()=>({top:state.top-state.scroll-43,bottom:state.top-state.scroll+tableHeight()+state.tail})};
 const header={getBoundingClientRect:()=>({height:state.header})}, row={};
 const result={style,scrollTop:0,parentElement:list,get offsetHeight(){return tableHeight();},get clientHeight(){return tableHeight()-17;},getBoundingClientRect:()=>({top:state.top-state.scroll,bottom:state.top-state.scroll+tableHeight()}),querySelector:selector=>selector==='.dlp-po-grid-header'?header:row};
 const root={get innerHeight(){return state.height;},getComputedStyle:node=>({overflowY:node===main?'auto':'visible',minHeight:node===row?'42px':'0px',paddingBottom:'0px',marginBottom:'0px',borderBottomWidth:'0px'}),
  requestAnimationFrame:fn=>{const id=frames.size+1;frames.set(id,fn);return id;},cancelAnimationFrame:id=>frames.delete(id),
  addEventListener:(event,fn)=>listeners.set(event,fn),removeEventListener:(event,fn)=>{if(listeners.get(event)===fn)listeners.delete(event);},
  ResizeObserver:class {constructor(callback){this.callback=callback;this.targets=[];observers.push(this);}observe(node){this.targets.push(node);}disconnect(){this.disconnected=true;}}};
 const controller={root,list:{$result:{parent:()=>[result]},$frappe_list:[list],page:{wrapper:{find:()=>[{}]}}},$toolbar:[{}],$filters:[{parentElement:{}}],$summary:[{}],$paging:[{}],$salesViewbar:[{}],$salesNotice:[{}]};
 return {controller,state,main,result,list,header,row,root,values,frames,listeners,observers,flush(){const callbacks=[...frames.values()];frames.clear();callbacks.forEach(fn=>fn());}};
}

function horizontalScrollFixture() {
 const f=mountedReleaseFixture(),frames=new Map(),observers=[],mutations=[];let frameId=0;
 function node(rect={left:240,right:1260,top:100,bottom:720},clientWidth=rect.right-rect.left,scrollWidth=clientWidth) {
  const listeners=new Map(),children=[];let scrollLeft=0;
  return {rect,clientWidth,scrollWidth,style:{},children,hidden:false,className:'',attributes:{},
   get scrollLeft(){return this.hidden?0:scrollLeft;},set scrollLeft(value){if(!this.hidden)scrollLeft=value;},
   getBoundingClientRect(){return this.rect;},get firstElementChild(){return children[0];},
   appendChild(child){children.push(child);return child;},setAttribute(key,value){this.attributes[key]=value;},
   addEventListener(type,fn){if(!listeners.has(type))listeners.set(type,new Set());listeners.get(type).add(fn);},
   removeEventListener(type,fn){listeners.get(type)?.delete(fn);},
   emit(type){for(const fn of listeners.get(type)||[])fn({target:this});},
   listenerCount(){return [...listeners.values()].reduce((sum,set)=>sum+set.size,0);}};
 }
 const viewport=node(undefined,1020,1700),page=node(),body=node(),details=[];let modal=false;
 page.scrollHeight=2500;page.clientHeight=720;page.scrollTop=0;
 viewport.querySelectorAll=()=>details;
 body.classList={contains:className=>className==='modal-open' && modal};
 f.controller.list.$result={parent:()=>[viewport]};f.controller.list.page.wrapper={0:{closest:()=>page}};
 f.env.innerWidth=1280;f.env.innerHeight=720;f.env.document={body,createElement:()=>node()};
 const host=node();f.env.addEventListener=host.addEventListener;f.env.removeEventListener=host.removeEventListener;
 f.env.requestAnimationFrame=fn=>{frames.set(++frameId,fn);return frameId;};f.env.cancelAnimationFrame=id=>frames.delete(id);
 f.env.ResizeObserver=class {constructor(callback){this.callback=callback;this.targets=new Set();observers.push(this);}observe(target){this.targets.add(target);}disconnect(){this.targets.clear();this.disconnected=true;}};
 f.env.MutationObserver=class {constructor(callback){this.callback=callback;mutations.push(this);}observe(){}disconnect(){this.disconnected=true;}};
 return {...f,viewport,page,body,details,frames,observers,mutations,host,node,
  bar(){const element=body.children.find(child=>child.className==='dlp-sales-horizontal-scroll');assert.ok(element,'Sales native bottom scrollbar is mounted');return element;},
  flush(){while(frames.size){const callbacks=[...frames.values()];frames.clear();callbacks.forEach(fn=>fn());}},
  render(){f.config.afterRender(f.controller);this.flush();},
  modal(value){modal=value;mutations.at(-1)?.callback();this.flush();}};
}

test('sales bottom scrollbar selects the detail with greatest visible area and keeps DOM order for ties',()=>{
 const f=horizontalScrollFixture(),a=f.node({left:250,right:1250,top:-200,bottom:300},1000,1600),b=f.node({left:250,right:1250,top:310,bottom:700},1000,1800);
 f.details.push(a,b);f.render();const bar=f.bar();
 bar.scrollLeft=140;bar.emit('scroll');assert.equal(b.scrollLeft,140);assert.equal(a.scrollLeft,0);
 a.rect={...b.rect};f.host.emit('scroll');f.flush();bar.scrollLeft=190;bar.emit('scroll');assert.equal(a.scrollLeft,190);assert.equal(b.scrollLeft,140);
});

test('sales bottom scrollbar falls back to the main result when every detail is outside the viewport',()=>{
 const f=horizontalScrollFixture();f.details.push(f.node({left:250,right:1250,top:730,bottom:900},1000,1800));f.render();
 const bar=f.bar();bar.scrollLeft=270;bar.emit('scroll');assert.equal(f.viewport.scrollLeft,270);assert.equal(f.details[0].scrollLeft,0);
 assert.equal(bar.style.left,'240px');assert.equal(bar.style.width,'1020px');assert.equal(bar.firstElementChild.style.width,'1700px');
});

test('sales native bottom scrollbar synchronizes both ways with the selected detail without changing the main grid',()=>{
 const f=horizontalScrollFixture(),detail=f.node({left:250,right:1250,top:120,bottom:2500},1000,1700);detail.scrollLeft=120;
 f.details.push(detail);f.render();const bar=f.bar();assert.equal(bar.scrollLeft,120);assert.equal(bar.firstElementChild.style.width,'1720px');
 detail.scrollLeft=380;detail.emit('scroll');assert.equal(bar.scrollLeft,380);
 bar.scrollLeft=620;bar.emit('scroll');assert.equal(detail.scrollLeft,620);assert.equal(f.viewport.scrollLeft,0);
 assert.equal(bar.attributes.tabindex,'0');assert.ok(bar.attributes['aria-label']);
});

test('sales scrollbar remeasures sidebar width, detail resize and replaced rows while releasing old targets',()=>{
 const f=horizontalScrollFixture(),old=f.node({left:250,right:1250,top:120,bottom:650},1000,1700);f.details.push(old);f.render();const bar=f.bar();
 f.viewport.rect={left:80,right:1260,top:100,bottom:720};f.viewport.clientWidth=1180;old.clientWidth=1160;old.scrollWidth=1900;
 f.observers[0].callback();f.flush();assert.equal(bar.style.left,'80px');assert.equal(bar.style.width,'1180px');assert.equal(bar.firstElementChild.style.width,'1920px');
 const replacement=f.node({left:90,right:1250,top:120,bottom:650},1160,2000);replacement.scrollLeft=230;f.details.splice(0,1,replacement);f.render();
 assert.equal(old.listenerCount(),0);assert.equal(bar.scrollLeft,230);assert.equal(f.body.children.filter(child=>child.className==='dlp-sales-horizontal-scroll').length,1);
 old.scrollLeft=500;old.emit('scroll');assert.equal(bar.scrollLeft,230);
 assert.ok(f.observers.at(-1).targets.has(replacement),'new detail widths are observed');
});

test('sales scrollbar hides for a visible detail without overflow rather than scrolling an unrelated main grid',()=>{
 const f=horizontalScrollFixture(),detail=f.node({left:250,right:1250,top:120,bottom:650},1000,1000);f.details.push(detail);f.render();assert.equal(f.bar().hidden,true);
 detail.scrollWidth=1700;f.observers[0].callback();f.flush();assert.equal(f.bar().hidden,false);
 f.details.length=0;f.viewport.scrollWidth=f.viewport.clientWidth;f.render();assert.equal(f.bar().hidden,true);
});

test('sales scrollbar route departure removes listeners and observers and cached return reuses one mirror',()=>{
 const f=horizontalScrollFixture(),detail=f.node({left:250,right:1250,top:120,bottom:650},1000,1700);f.details.push(detail);f.render();const bar=f.bar();
 f.setRoute(['List','Purchase Order','List']);f.config.onRouteChange(f.controller,false);assert.equal(bar.hidden,true);assert.equal(detail.listenerCount(),0);assert.equal(f.host.listenerCount(),0);assert.equal(f.page.listenerCount(),0);assert.equal(bar.listenerCount(),0);assert.equal(f.frames.size,0);
 assert.ok(f.observers.every(observer=>observer.disconnected));assert.ok(f.mutations.every(observer=>observer.disconnected));
 detail.scrollLeft=430;f.setRoute(['List','Sales Order','List']);f.config.onRouteChange(f.controller,true);f.flush();assert.equal(bar.scrollLeft,430,'cached return restores the retained detail position');f.config.onRouteChange(f.controller,true);f.flush();
 assert.equal(f.bar(),bar);assert.equal(f.body.children.filter(child=>child.className==='dlp-sales-horizontal-scroll').length,1);assert.equal(bar.listenerCount(),1);
});

test('sales bottom scrollbar restores changed or replaced target positions after modal hiding',()=>{
 for(const replaced of [false,true]){
  const f=horizontalScrollFixture();let detail=f.node({left:250,right:1250,top:120,bottom:650},1000,1700);
  detail.scrollLeft=300;f.details.push(detail);f.render();f.render();const bar=f.bar();assert.equal(bar.scrollLeft,300);
  f.modal(true);assert.equal(bar.hidden,true);assert.equal(bar.scrollLeft,0,'display:none has no scroll box');
  if(replaced){detail=f.node(detail.rect,1000,1700);f.details.splice(0,1,detail);f.render();}
  else {detail.scrollLeft=500;detail.emit('scroll');}
  const expected=detail.scrollLeft;f.modal(false);assert.equal(bar.hidden,false);assert.equal(bar.scrollLeft,expected,replaced?'rebuilt target starts at zero':'hidden target position changed');
  bar.scrollLeft=expected+30;bar.emit('scroll');assert.equal(detail.scrollLeft,expected+30,'subsequent scrolling starts from the restored position');
 }
});

test('sales detail widths and native scrollbar hover contract reserve a fixed slot and keep codes and money complete',()=>{
 const css=fs.readFileSync(require.resolve('../deeplinkerp_branding/public/css/sales_order_list.css'),'utf8');
 for(const [field,width] of [['item_code',160],['item_name',260],['custom_specifications',80],['qty',90],['rate',120],['amount',140]])assert.match(css,new RegExp(`\\[data-fieldname=${field}\\]\\s*\\{[^}]*min-width:\\s*${width}px`));
 assert.doesNotMatch(css,/\[data-fieldname=(?:custom_version|description)\]/);
 assert.match(css,/\.dlp-sales-horizontal-scroll\s*\{[^}]*position:\s*fixed;[^}]*bottom:\s*0;[^}]*height:\s*12px;[^}]*overflow-x:\s*auto/);
 assert.match(css,/::-webkit-scrollbar-thumb\s*\{[^}]*background:[^;]*var\(--dlp-table-scrollbar-thumb[^;]*;[^}]*background-clip:\s*content-box;[^}]*border:\s*3px solid transparent/);
 assert.match(css,/:hover[^}]*::-webkit-scrollbar-thumb[\s\S]*?\{[^}]*border-width:\s*0/);
 assert.match(css,/::-webkit-scrollbar-thumb:active[\s\S]*?\{[^}]*border-width:\s*0/);
 assert.match(css,/\.dlp-sales-horizontal-scroll\s*\{[^}]*scrollbar-color:\s*auto/,'Blink keeps the 6px-to-12px native thumb styling enabled');
 assert.match(css,/@supports not selector\(::-webkit-scrollbar\)\s*\{[^}]*\.dlp-sales-horizontal-scroll[^}]*scrollbar-color:\s*var\(--dlp-table-scrollbar-thumb/,'other engines retain themed native scrollbar colors');
 assert.match(css,/body\.modal-open[^}]*\.dlp-sales-horizontal-scroll[^}]*\{[^}]*display:\s*none/);
 assert.match(css,/\.dlp-sales-items-scroll[^{}]*::-webkit-scrollbar/);
 const code='LONGCODE'.repeat(5)+'XX',html=grid.itemTable({header:{currency:'MXN'},item_fields:['item_code','amount'],items:[{item_code:code,amount:987654321098.76}]});
 assert.ok(html.includes(code));assert.match(html,/987,654,321,098\.76 MXN/);assert.doesNotMatch(html,/dlp-sales-item-text/);
});

test('sales sequence button fills only its row cell and retains native button accessibility',()=>{
 const f=mountedReleaseFixture(),css=fs.readFileSync(require.resolve('../deeplinkerp_branding/public/css/sales_order_list.css'),'utf8');
 assert.match(css,/body\.dlp-sales-order-grid-active \.dlp-po-grid-row \.dlp-po-grid-cell\[data-fieldname=_sequence\]\s*\{[^}]*padding:\s*0[;}]/);
 assert.match(css,/body\.dlp-sales-order-grid-active \.dlp-po-grid-row \.dlp-sales-expand\s*\{[^}]*display:\s*flex;[^}]*align-items:\s*center;[^}]*justify-content:\s*center;[^}]*width:\s*100%;[^}]*flex:\s*1;[^}]*align-self:\s*stretch/);
 const closed=f.config.renderSequence(f.controller,{name:'SO-A'},1);
 assert.match(closed,/<button[^>]*type="button"[^>]*aria-expanded="false"[^>]*aria-label="展开产品明细"/);
 f.controller.salesExpanded.set('SO-A',{});
 assert.match(f.config.renderSequence(f.controller,{name:'SO-A'},1),/aria-expanded="true"/);
});

test('sales inline title and scoped tables keep a four-pixel gap despite late global table margins',()=>{
 const f=mountedReleaseFixture(),css=fs.readFileSync(require.resolve('../deeplinkerp_branding/public/css/sales_order_list.css'),'utf8');
 f.controller.salesExpanded.set('SO-A',{header:{},item_fields:['qty'],items:[{qty:1}]});
 const html=f.config.rowExtra(f.controller,{name:'SO-A'});
 assert.match(html,/<section class="dlp-sales-expanded"><strong>订单产品明细<\/strong><div class="dlp-sales-items-scroll"><table class="table dlp-sales-items/);
 assert.match(css,/\.dlp-sales-items-scroll>table\.table\.dlp-sales-items\s*\{[^}]*margin:\s*0[;}]/,'three Sales table classes outrank the late global .table rule in inline and dialog details');
 assert.match(css,/\.dlp-sales-expanded>strong\s*\{[^}]*display:\s*block;[^}]*line-height:\s*20px;[^}]*margin-bottom:\s*4px/);
 assert.match(css,/\.dlp-sales-expanded>\.dlp-sales-items-scroll\s*\{[^}]*margin:\s*0[;}]/);
 assert.match(css,/\.dlp-sales-expanded\s*\{[^}]*padding:\s*8px 10px/);
});

test('sales owns no viewport height adapter and its full-height grids scroll horizontally only',()=>{
 assert.equal(grid.fitViewport,undefined);
 const script=fs.readFileSync(require.resolve('../deeplinkerp_branding/public/js/sales_order_list.js'),'utf8');
 assert.doesNotMatch(script,/syncPageHeaders|--dlp-sales-header-offset/,'native headers scroll with the page');
 const css=fs.readFileSync(require.resolve('../deeplinkerp_branding/public/css/sales_order_list.css'),'utf8');
 assert.doesNotMatch(css,/--dlp-sales-result-max-height|100vw - 260px/);
 assert.match(css,/\.result-container\s*\{[^}]*max-height:\s*none;[^}]*overflow-x:\s*auto;[^}]*overflow-y:\s*hidden;/);
 assert.match(css,/\.dlp-sales-items-scroll\s*\{[^}]*overflow-x:\s*auto;[^}]*overflow-y:\s*hidden;/);
 assert.match(css,/\.dlp-sales-expanded\s*\{[^}]*width:\s*100cqw;/);
 assert.match(css,/\.dlp-sales-item-text\s*\{[^}]*-webkit-line-clamp:\s*3;/);
 assert.match(css,/\.dlp-sales-items\s*:is\(td,th\)\s*\{[^}]*border:\s*1px solid/);
});

test('shared viewport can reserve two usable purchase rows below sticky header and scrollbar on short screens',()=>{
 const engine=require('../deeplinkerp_branding/public/js/compact_list.js'),fixture=viewportFixture();
 fixture.state.height=360;fixture.state.top=300;
 engine.fitViewport(fixture.controller,{active:true,root:fixture.root,scrollElement:fixture.result,layoutTailElement:fixture.list,property:'--dlp-test-result-max-height',headerSelector:'.dlp-po-grid-header',rowSelector:'.dlp-po-grid-row',minimumRows:2});
 fixture.flush();assert.equal(fixture.values.get('--dlp-test-result-max-height'),'133px');
 fixture.state.scroll=100;fixture.main.scrollTop=100;fixture.observers[0].callback();fixture.flush();
 assert.equal(fixture.values.get('--dlp-test-result-max-height'),'133px','outer overflow preserves usable row space');
});

test('sales back-to-top follows page scrolling, reduced motion and route lifecycle with one reusable button',()=>{
 const f=mountedReleaseFixture(),listeners=new Map(),clicks=[],button={hidden:true,events:{},setAttribute(){},addEventListener(name,fn){this.events[name]=fn;}},documentScroller={scrollTop:0,scrollTo:options=>clicks.push(['document',options])};
 const scroller={scrollHeight:2000,clientHeight:700,scrollTop:0,scrollTo:options=>clicks.push(['page',options]),addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name)};
 let created=0,reduced=false;f.controller.list.page.wrapper={0:{closest:()=>scroller}};
 f.env.document={createElement:()=>button,body:{appendChild(){created++;}},scrollingElement:documentScroller};
 f.env.addEventListener=(name,fn)=>listeners.set(`root-${name}`,fn);f.env.removeEventListener=name=>listeners.delete(`root-${name}`);f.env.matchMedia=()=>({matches:reduced});
 f.config.onRouteChange(f.controller,true);assert.equal(created,1);assert.equal(button.hidden,true);
 scroller.scrollTop=301;listeners.get('scroll')();assert.equal(button.hidden,false);
 button.events.click();assert.equal(clicks[0][0],'page');assert.deepEqual({...clicks[0][1]},{top:0,behavior:'smooth'});
 reduced=true;button.events.click();assert.equal(clicks[1][1].behavior,'auto');
 f.config.onRouteChange(f.controller,false);assert.equal(button.hidden,true);assert.equal(listeners.size,0);
 button.events.click();assert.equal(clicks.length,2,'departed route cannot scroll another page');
 f.config.onRouteChange(f.controller,true);f.config.onRouteChange(f.controller,true);assert.equal(created,1);assert.equal(listeners.size,2);
 scroller.scrollTop=0;listeners.get('scroll')();assert.equal(button.hidden,true);
 scroller.scrollHeight=700;documentScroller.scrollTop=500;listeners.get('root-scroll')();assert.equal(button.hidden,false);
 button.events.click();assert.equal(clicks[2][0],'document','classic document scrolling is supported');
 documentScroller.scrollTop=0;listeners.get('root-scroll')();assert.equal(button.hidden,true);
 f.config.onRouteChange(f.controller,false);
});

test('all four cached inventory pages share viewport fitting without stale loads reactivating departed owners',async()=>{
 const engine=require('../deeplinkerp_branding/public/js/compact_list.js'),inventory=require('../deeplinkerp_branding/public/js/inventory_detail.bundle.js');
 assert.equal(typeof engine.fitViewport,'function');
 const previous={$:global.$,frappe:global.frappe}, callbacks=[], pending=[], owners=[];let route,current;
 // Replace only native UI/network boundaries; constructor, bootstrap, refresh and fitting remain real.
 class Surface {
  constructor(node){this[0]=node||{};this.length=1;}
  find(selector){return new Surface(selector==='.id-table-wrap'?current.result:undefined);}
  children(){return this;}remove(){return this;}append(){return this;}on(){return this;}html(){return this;}text(){return this;}prop(){return this;}attr(){return this;}addClass(){return this;}removeClass(){return this;}
 }
 global.$=value=>new Surface(typeof value==='string'?current.list:value);
 global.frappe={get_route:()=>[route],router:{on:(event,fn)=>callbacks.push(fn)},defaults:{get_default:()=> 'MX'},call:request=>new Promise(resolve=>pending.push({request,resolve})),
  ui:{make_app_page:({parent})=>({body:parent,wrapper:new Surface(),add_field:df=>({df,value:df.default??'',get_value(){return this.value;},async set_value(value){this.value=value;},refresh(){}}),set_primary_action(){},add_inner_button(){}})}};
 try {
  for(const [category,pageRoute] of [['material','inventory-location-detail'],['semi_finished','semi-finished-inventory-detail'],['finished','finished-goods-inventory-detail'],['mold','mold-inventory-detail']]) {
   route=pageRoute;callbacks.forEach(fn=>fn());current=viewportFixture();
   const fixture=current;fixture.state.top=272;fixture.state.tail=64;fixture.result.querySelector=selector=>selector==='thead'?fixture.header:fixture.row;
   const page=inventory.bootstrap({ownerDocument:{defaultView:fixture.root}},{category,title:category});
   // DOM lookup belongs to this cached owner after other inventories mount.
   page.$root.find=selector=>new Surface(selector==='.id-table-wrap'?fixture.result:undefined);
   await page.fields.keyword.set_value('saved');page.selected.set('A',{item_code:'A'});page.start=200;
   fixture.flush();assert.equal(fixture.values.get('--dlp-inventory-result-max-height'),'384px');assert.equal(fixture.observers.length,1);
   owners.push({page,fixture,pageRoute});
  }
  route='Item';callbacks.forEach(fn=>fn());
  for(const {fixture} of owners) {assert.equal(fixture.values.size,0);assert.equal(fixture.frames.size,0);assert.equal(fixture.listeners.size,0);assert.equal(fixture.observers[0].disconnected,true);}
  for(const {resolve} of pending)resolve({message:{company:'MX',groups:[],total_count:0,page_count:0,snapshot_options:[],item_group_options:[],can_create_stock_entry:false}});
  await new Promise(resolve=>setImmediate(resolve));
  for(const {fixture} of owners) {assert.equal(fixture.observers.length,1);assert.equal(fixture.frames.size,0,'late read/render must not reattach a departed owner');}
  for(const {page,fixture,pageRoute} of owners) {
   route=pageRoute;callbacks.forEach(fn=>fn());callbacks.forEach(fn=>fn());fixture.flush();
   assert.equal(fixture.observers.length,2);assert.equal(fixture.listeners.size,1);assert.equal(fixture.values.get('--dlp-inventory-result-max-height'),'384px');
   fixture.state.top=320;fixture.state.tail=80;fixture.observers[1].callback();fixture.flush();assert.equal(fixture.values.get('--dlp-inventory-result-max-height'),'320px');
   assert.equal(page.selected.size,1);assert.equal(page.fields.keyword.get_value(),'saved');assert.equal(page.start,200);
  }
  assert.equal(callbacks.length,4);assert.equal(pending.length,4,'returning to a cached page does not issue another read');
 } finally {route='Item';callbacks.forEach(fn=>fn());Object.assign(global,previous);}
});


test('prepayment is visible in both default groups and upgrades personal columns once without changing their order',()=>{
 for(const preset of [grid.presets.main,grid.presets.finance])assert.equal(preset[preset.indexOf('customer_name')+1],'advance_paid');
 const allowed=new Set([...grid.COLUMNS.map(c=>c.fieldname),'party_account_currency']);
 const before={version:3,density:'standard',columns:['name','status','grand_total','customer_name','dlp_actions']};
 const upgraded=grid.normalizePreferences(before,allowed);
 assert.deepEqual(upgraded.columns,['name','status','grand_total','customer_name','advance_paid','dlp_actions']);
 assert.equal(upgraded.density,'standard');assert.equal(upgraded.version,4);
 assert.deepEqual(before.columns,['name','status','grand_total','customer_name','dlp_actions']);
 const hidden={...upgraded,columns:upgraded.columns.filter(c=>c!=='advance_paid')};
 assert.deepEqual(grid.normalizePreferences(hidden,allowed),hidden);
 for(const denied of ['advance_paid','party_account_currency']){
  const fields=new Set(allowed);fields.delete(denied);
  assert.ok(!grid.normalizePreferences(before,fields).columns.includes('advance_paid'));
 }
});

test('prepayment shows the account currency, keeps explicit zero and never implies a missing receipt is unpaid',()=>{
 const render=doc=>grid.renderValue('advance_paid',doc,{});
 assert.equal(render({advance_paid:1200,currency:'USD',party_account_currency:'MXN'}),'1,200.00 MXN');
 assert.equal(render({advance_paid:0,party_account_currency:'MXN'}),'0.00 MXN');
 for(const doc of [{advance_paid:100,currency:'USD'},{party_account_currency:'MXN'},{advance_paid:'',party_account_currency:'MXN'}])assert.equal(render(doc),'—');
 const detail=header=>grid.detailsHTML({header:{name:'SO-1',...header},item_fields:[],items:[]});
 assert.match(detail({advance_paid:0,party_account_currency:'MXN'}),/0\.00 MXN/);
 assert.match(detail({advance_paid:1200,party_account_currency:'MXN'}),/1,200\.00 MXN/);
 assert.doesNotMatch(detail({advance_paid:98765,currency:'USD'}),/98,765/);
 assert.match(detail({advance_paid:0,party_account_currency:'MXN'}),/草稿收款.*未录入.*财务/s);
 assert.doesNotMatch(detail({}),/草稿收款/);
});


test('receipt visibility distinguishes loading, restricted records and association conflicts from an empty readable result',()=>{
 const render=finance=>grid.renderValue('dlp_receipts',{dlp_finance:finance},{});
 assert.match(render(undefined),/正在核验/);
 assert.match(render({receipts:[],receipts_complete:false}),/权限.*财务/);
 assert.match(render({receipts:[],receipts_complete:true}),/未找到可见的关联收款记录/);
 assert.match(render({association_error:true,receipts:[],receipts_complete:true}),/关联异常/);
 assert.match(render({reason:'order unavailable'}),/暂不可用/);
 const records={receipts:[{name:'PE-1',allocated_amount:0,currency:'MXN',docstatus:0}],receipts_complete:false};
 assert.match(render(records),/PE-1.*草稿.*0\.00 MXN.*权限/s);
 assert.doesNotMatch(render(records),/已提交|未付款/);
});

test('failed current receipt lookup stops loading and preserves selection while stale failures remain ignored',async()=>{
 const f=mountedReleaseFixture(),notices=[],paints=[];
 f.controller.salesView='all';f.controller.preferences.columns=['name','dlp_receipts'];
 f.controller.$salesNotice={text:value=>notices.push(value)};
 f.list.render_list=()=>paints.push('render');f.list.set_rows_as_checked=()=>paints.push('selection');
 const pending=f.config.onRows(f.controller);
 f.reads[0].resolve({message:{}});await new Promise(setImmediate);
 f.reads[1].reject(new Error('private upstream response'));await pending;
 assert.match(f.grid.renderValue('dlp_receipts',f.list.data[0],{}),/读取失败.*刷新/);
 assert.doesNotMatch(f.grid.renderValue('dlp_receipts',f.list.data[0],{}),/正在核验|private upstream/);
 assert.deepEqual(paints,['render','selection']);assert.match(notices[0],/读取失败/);
 const stale=f.config.onRows(f.controller);f.controller.requestId++;
 f.reads[2].reject(new Error('stale'));await stale;assert.equal(paints.length,2);
});
