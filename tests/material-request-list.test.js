const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const base = '../deeplinkerp_branding/public/js/';
const mr = fs.existsSync(require('node:path').join(__dirname, base, 'material_request_list.js')) ? require(base + 'material_request_list.js') : {};
test('MR columns start with ODT and category and keep native identifiers', () => {
 assert.deepEqual(mr.COLUMNS?.slice(0, 3).map(c => c.fieldname), ['custom_odt', 'custom_mes_issue_category', 'name']);
});
test('MR categories use only explicit source and otherwise translated original type', () => {
 assert.equal(typeof mr.renderValue, 'function');
 assert.equal(mr.renderValue('custom_mes_issue_category', {custom_mes_issue_category:'raw_material'}), '原料申请');
 assert.equal(mr.renderValue('custom_mes_issue_category', {custom_mes_issue_category:'unknown', material_request_type:'Material Issue', custom_odt:'ODT'} , {translate: x => x === 'Material Issue' ? '发料' : x}), '发料');
 assert.equal(mr.renderValue('custom_odt', {}), '');
});
test('MR query count and export share filters without quantity or money summary', () => {
 assert.equal(typeof mr.buildQuery, 'function');
 const allowed = new Set(['name','owner','custom_odt','custom_mes_issue_category','custom_material_request_no']);
 const q = mr.buildQuery({fields:['name','missing'], filters:[], or_filters:[]}, {custom_odt:'ODT-1', custom_mes_issue_category:'consumable', search:'MES-1'}, allowed);
 const requests = mr.buildRequests(q, ['name','owner'], allowed);
 assert.equal(requests.summary, null);
 assert.deepEqual(requests.export.filters, requests.count.filters);
 assert.deepEqual(requests.export.or_filters, requests.count.or_filters);
 assert.deepEqual(q.fields,['name']);
 assert.equal(q.or_filters.length,2);
 assert.equal(requests.export.doctype,'Material Request');
});
test('MR default hides creator while a user can add it back after preference migration', () => {
 const allowed=new Set(mr.COLUMNS.map(column=>column.fieldname));
 assert.equal(mr.normalizePreferences(null,allowed).columns.includes('owner'),false);
 assert.equal(mr.normalizePreferences({columns:['name','owner','status']},allowed).columns.includes('owner'),false);
 assert.equal(mr.normalizePreferences({version:1,columns:['name','owner','status']},allowed).columns.includes('owner'),true);
});
test('MES pending view includes historical prefix rows while keeping native and quick filters', () => {
 const nativeFilter=['Material Request','company','=','YUEWEI MX'];
 const env={frappe:{model:{std_fields_list:['name','owner','docstatus']},perm:{has_perm:()=>true},boot:{},get_route:()=>['List','Material Request','List']}};
 const list={doctype:'Material Request',view_name:'List',meta:{fields:[...mr.COLUMNS.map(c=>({fieldname:c.fieldname})),{fieldname:'material_request_type'},{fieldname:'custom_request_source'}]},fields:[['name','Material Request']],data:[],get_args(){return {fields:this.fields,filters:[nativeFilter],or_filters:[],order_by:'transaction_date desc'};},render_header(){},get_form_link(){return '/desk/material-request/MR';},get_indicator_html(){return '';}};
 const controller=mr.mount(list,env);
 assert.equal(controller.mrView,'mes_pending');
 controller.quick.custom_odt='ODT-1';
 controller.quick.search='MES-1';
 const pending=list.get_args();
 assert.deepEqual(pending.filters, [nativeFilter,
  ['Material Request','custom_odt','=','ODT-1'],
  ['Material Request','name','like','MAT-MR-MES-%'],
  ['Material Request','docstatus','=',1],
  ['Material Request','material_request_type','in',['Material Issue','Material Transfer for Manufacture','Injection Molding Issuance']],
  ['Material Request','status','not in',['Stopped','Cancelled']],
  ['Material Request','per_ordered','<',100]]);
 assert.deepEqual(pending.or_filters,[['Material Request','name','like','%MES-1%'],['Material Request','custom_material_request_no','like','%MES-1%']]);
 const requests=mr.buildRequests(pending,['name','status'],controller.allowed);
 assert.deepEqual(requests.count.filters,requests.export.filters);
 assert.deepEqual(requests.count.or_filters,requests.export.or_filters);
 controller.mrView='all';
 const all=list.get_args();
 assert.deepEqual(all.filters,[nativeFilter,['Material Request','custom_odt','=','ODT-1']]);
 assert.deepEqual(all.or_filters,pending.or_filters);
});
test('issued view means a submitted MES issue request with 100% issue progress', () => {
 const env={frappe:{model:{std_fields_list:['name','docstatus']},perm:{has_perm:()=>true},boot:{},get_route:()=>['List','Material Request','List']}};
 const list={doctype:'Material Request',view_name:'List',meta:{fields:mr.COLUMNS.map(c=>({fieldname:c.fieldname})).concat([{fieldname:'material_request_type'}])},fields:[['name','Material Request']],data:[],get_args(){return {fields:this.fields,filters:[],or_filters:[]};},render_header(){},get_form_link(){return '/desk/material-request/MR';},get_indicator_html(){return '';}};
 const controller=mr.mount(list,env);
 controller.mrView='mes_issued';
 assert.deepEqual(list.get_args().filters,[
  ['Material Request','name','like','MAT-MR-MES-%'],
  ['Material Request','docstatus','=',1],
  ['Material Request','material_request_type','in',['Material Issue','Material Transfer for Manufacture','Injection Molding Issuance']],
  ['Material Request','per_ordered','>=',100]]);
});
test('status and both date ranges filter the list, count and export in either view', () => {
 const env={frappe:{model:{std_fields_list:['name','docstatus']},perm:{has_perm:()=>true},boot:{},get_route:()=>['List','Material Request','List']}};
 const list={doctype:'Material Request',view_name:'List',meta:{fields:mr.COLUMNS.map(c=>({fieldname:c.fieldname})).concat([{fieldname:'material_request_type'},{fieldname:'custom_request_source'}])},fields:[['name','Material Request']],data:[],get_args(){return {fields:this.fields,filters:[],or_filters:[]};},render_header(){},get_form_link(){return '/desk/material-request/MR';},get_indicator_html(){return '';}};
 const controller=mr.mount(list,env);
 Object.assign(controller.quick,{status:'Pending',from_date:'2026-10-01',to_date:'2026-10-31',need_from_date:'2026-10-02',need_to_date:'2026-11-01'});
 for(const view of ['mes_pending','all']) {
  controller.mrView=view;
  const args=list.get_args();
  for(const filter of [['Material Request','transaction_date','>=','2026-10-01'],['Material Request','transaction_date','<=','2026-10-31'],['Material Request','schedule_date','>=','2026-10-02'],['Material Request','schedule_date','<=','2026-11-01'],['Material Request','status','=','Pending']]) assert.deepEqual(args.filters.find(row=>row[1]===filter[1] && row[2]===filter[2]),filter);
  const requests=mr.buildRequests(args,['name','status'],controller.allowed);
  assert.deepEqual(requests.count.filters,requests.export.filters);
 }
});
test('category filter finds labeled requests and historical rows through their displayed purpose', () => {
 const env={frappe:{model:{std_fields_list:['name','docstatus']},perm:{has_perm:()=>true},boot:{},get_route:()=>['List','Material Request','List']}};
 const list={doctype:'Material Request',view_name:'List',meta:{fields:mr.COLUMNS.map(c=>({fieldname:c.fieldname})).concat([{fieldname:'material_request_type'},{fieldname:'custom_request_source'}])},fields:[['name','Material Request']],data:[],get_args(){return {fields:this.fields,filters:[],or_filters:[]};},render_header(){},get_form_link(){return '/desk/material-request/MR';},get_indicator_html(){return '';}};
 const controller=mr.mount(list,env);
 controller.mrView='all';
 controller.quick.custom_mes_issue_category='raw_material';
 assert.deepEqual(list.get_args().filters,[['Material Request','custom_mes_issue_category','=','raw_material']]);
 controller.quick.custom_mes_issue_category='legacy:Material Issue';
 assert.deepEqual(list.get_args().filters,[['Material Request','custom_mes_issue_category','is','not set'],['Material Request','material_request_type','=','Material Issue']]);
});
test('MES pending view falls back to the established MES name prefix when source metadata is absent', () => {
 const env={frappe:{model:{std_fields_list:['name','docstatus']},perm:{has_perm:()=>true},boot:{},get_route:()=>['List','Material Request','List']}};
 const list={doctype:'Material Request',view_name:'List',meta:{fields:mr.COLUMNS.map(c=>({fieldname:c.fieldname})).concat([{fieldname:'material_request_type'}])},fields:[['name','Material Request']],data:[],get_args(){return {fields:this.fields,filters:[],or_filters:[]};},render_header(){},get_form_link(){return '/desk/material-request/MR';},get_indicator_html(){return '';}};
 const controller=mr.mount(list,env);
 assert.equal(controller.mrView,'mes_pending');
 assert.deepEqual(list.get_args().filters[0],['Material Request','name','like','MAT-MR-MES-%']);
});
test('view buttons switch scope, clear selection and return to the first page', () => {
 const vm=require('node:vm');
 const source=fs.readFileSync(require('node:path').join(__dirname,base,'material_request_list.js'),'utf8');
 let config,markup,click,cleared=0,page,refreshed=0;
 const button={dataset:{mrView:'all'}};
 const $=value=>value===button?{attr(){return this;},toggleClass(){return this;}}:{insertBefore(){markup=value;return this;},on(event,selector,handler){click=handler;return this;},find(selector){return {each(handler){if(selector==='[data-mr-view]')handler.call(button);}}}};
 const context={$,DeepLinkERPCompactList:{create(value){config=value;return {escapeHTML:value=>String(value),install(){}};}}};
 vm.runInNewContext(source,context);
 const controller={$filters:{},root:context,translate:value=>value,allowed:new Set(['name','docstatus','material_request_type','status','per_ordered']),mrView:'mes_pending',list:{clear_checked_items(){cleared++;}},setPage(value){page=value;},refresh(){refreshed++;}};
 config.mountControls(controller);
 assert.match(markup,/待发料/);
 assert.match(markup,/已发料/);
 assert.match(markup,/全部/);
 assert.match(markup,/dlp-mr-view-strip/);
 click({currentTarget:button});
 assert.equal(controller.mrView,'all');
 assert.equal(cleared,1);
 assert.equal(page,0);
 assert.equal(refreshed,1);
});
test('three view counts retain native and quick filters and ignore stale responses', async () => {
 const vm=require('node:vm');
 const source=fs.readFileSync(require('node:path').join(__dirname,base,'material_request_list.js'),'utf8');
 const shared=require(base+'compact_list.js');
 let config;
 const context={DeepLinkERPCompactList:{...shared,create(value){config=value;return {...shared.create(value),install(){}};}}};
 vm.runInNewContext(source,context);
 const calls=[],shown={};
 const spans=['mes_pending','mes_issued','all'].map(mrCount=>({dataset:{mrCount}}));
 const native=['Material Request','company','=','YUEWEI MX'];
 const c={mrView:'mes_pending',mrCountVersion:0,mrCounts:{mes_pending:null,mes_issued:null,all:null},quick:{search:'A'},allowed:new Set(['name','docstatus','material_request_type','status','per_ordered','custom_material_request_no']),list:{},originals:{get_args(){return {fields:['name'],filters:[native],or_filters:[]};}},root:{frappe:{call:request=>new Promise(resolve=>calls.push({...request,resolve}))},$:span=>({text:value=>shown[span.dataset.mrCount]=value})},$mrViews:{find:()=>({each:callback=>spans.forEach(span=>callback.call(span))})}};
 config.onRows(c);
 await Promise.resolve();
 assert.equal(calls.length,2);
 assert.deepEqual(calls[0].args.filters[0],native);
 assert.deepEqual(calls[0].args.or_filters,calls[1].args.or_filters);
 assert.ok(calls[0].args.filters.some(row=>row[1]==='per_ordered' && row[2]==='>='));
 assert.equal(calls[1].args.filters.some(row=>row[1]==='per_ordered'),false);
 c.total=7;config.onCount(c);assert.equal(shown.mes_pending,'7');
 c.quick.search='B';config.onRequestStart(c);config.onRows(c);
 await Promise.resolve();
 calls[0].resolve({message:90});calls[1].resolve({message:90});await new Promise(setImmediate);
 assert.equal(c.mrCounts.mes_issued,null);
 calls[2].resolve({message:5});calls[3].resolve({message:9});await new Promise(setImmediate);
 assert.equal(shown.mes_issued,'5');assert.equal(shown.all,'9');
 c.root.frappe.call=()=>{throw new Error('temporary count failure');};
 assert.doesNotThrow(()=>config.onRows(c));
 await new Promise(setImmediate);
 assert.equal(shown.mes_issued,'—');assert.equal(shown.all,'—');
});
test('material request column widths restore, resize, persist and keep keyboard focus', () => {
 const vm=require('node:vm');
 const source=fs.readFileSync(require('node:path').join(__dirname,base,'material_request_list.js'),'utf8');
 let config,renders=0,focuses=0;
 const events={},documentEvents={},storage=new Map([['mr-key:widths',JSON.stringify({custom_material_request_no:350})]]);
 const result={0:{querySelectorAll:()=>[]},on(name,selector,handler){events[name]=handler;return this;},find:()=>({0:{focus(){focuses++;}}})};
 const context={DeepLinkERPCompactList:{create(value){config=value;return {preferenceKey:()=> 'mr-key',install(){}};},mountRowDetails(){}}};
 vm.runInNewContext(source,context);
 const controller={root:{frappe:{},document:{addEventListener:(name,fn)=>documentEvents[name]=fn,removeEventListener:name=>delete documentEvents[name]},localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)}},list:{$result:result,data:[],render_list(){renders++;},set_rows_as_checked(){}},allowed:new Set(config.columns.map(col=>col.fieldname)),preferences:{columns:['name','custom_material_request_no']},translate:value=>value};
 config.onMount(controller);
 const column=config.columns.find(col=>col.fieldname==='custom_material_request_no');
 assert.equal(column.width,350);
 assert.match(config.headerCellExtra(controller,column),/role="separator"/);
 const stop=()=>{};
 events['pointerdown.dlpMRResize']({button:0,currentTarget:{dataset:{mrResize:column.fieldname}},clientX:100,pointerId:1,preventDefault:stop,stopPropagation:stop});
 documentEvents.pointermove({pointerId:1,clientX:180});documentEvents.pointerup({pointerId:1});
 assert.equal(column.width,430);assert.equal(renders,1);
 assert.equal(JSON.parse(storage.get('mr-key:widths')).custom_material_request_no,430);
 events['keydown.dlpMRResize']({key:'ArrowRight',shiftKey:true,currentTarget:{dataset:{mrResize:column.fieldname}},preventDefault:stop,stopPropagation:stop});
 assert.equal(column.width,450);assert.equal(focuses,1);
});
test('expanded details use native permission checked get and convert stock quantity to request units', async () => {
 const vm=require('node:vm');
 const source=fs.readFileSync(require('node:path').join(__dirname,base,'material_request_list.js'),'utf8');
 let config,detailOptions,request;
 const shared=require(base+'compact_list.js');
 const engine={...shared,create(value){config=value;return {escapeHTML:value=>String(value),install(){}};},mountRowDetails(controller,value){detailOptions=value;}};
 const context={DeepLinkERPCompactList:engine};
 vm.runInNewContext(source,context);
 const native={modified:'v1',material_request_type:'Material Issue',items:[{item_code:'ITEM-1',item_name:'Item <one>',qty:25,ordered_qty:20,conversion_factor:2,uom:'kg',schedule_date:'2026-10-08',from_warehouse:'WH-1'}],custom_item_details:[{item_code:'ITEM-1',model:'M1',color:'black',order_qty:25,issue_qty:25,uom:'kg'}]};
 const itemFields=['item_code','item_name','qty','ordered_qty','conversion_factor','uom','schedule_date','from_warehouse'];
 const detailFields=['item_code','model','color','order_qty','issue_qty','uom'];
 const frappe={get_route:()=>['List','Material Request','List'],model:{with_doctype:async()=>{}},perm:{has_perm:()=>true},get_meta:doctype=>({fields:(doctype==='Material Request Item'?itemFields:detailFields).map(fieldname=>({fieldname}))}),call:async args=>{request=args;return {message:native};}};
 const controller={root:{frappe},allowed:new Set(['items','custom_item_details']),translate:value=>value};
 config.onMount(controller);
 assert.equal(detailOptions.canExpand(),true);
 const detail=await detailOptions.load({name:'MR-1'});
 assert.equal(detailOptions.showExpandAll,false);
 assert.equal(request.method,'frappe.client.get');
 assert.equal(request.args.name,'MR-1');
 assert.equal(detail.items[0].issued_qty,10);
 assert.equal(detail.items[0].remaining_qty,15);
 assert.equal(detail.specific.items[0].model,'M1');
 controller.expandedDetails=new Set(['MR-1']);controller.detailCache=new Map([['MR-1',detail]]);
 const html=config.rowExtra(controller,{name:'MR-1'});
 assert.match(html,/物料具体明细/);
 assert.match(html,/Item &lt;one&gt;/);
 assert.doesNotMatch(html,/Item <one>/);
 detail.items[0].remaining_qty=0.0004;
 controller.root.format_number=(value,format,precision)=>value.toFixed(precision);
 assert.match(config.rowExtra(controller,{name:'MR-1'}),/>0\.0004<\/td>/);
 const restricted={...controller,allowed:new Set()};
 config.onMount(restricted);
 assert.equal(detailOptions.canExpand(),false);
 await assert.rejects(detailOptions.load({name:'MR-1'}),/没有物料明细读取权限/);
});
test('preferences are isolated by doctype while preserving the PO key', () => {
 assert.equal(typeof mr.preferenceKey,'function');
 const po = require(base + 'purchase_order_list.js');
 assert.notEqual(mr.preferenceKey('site','user'),po.preferenceKey('site','user'));
});
test('readonly form summary escapes values and respects field permissions', () => {
 const p = require('node:path').join(__dirname,base,'material_request_form.js');
 const form = fs.existsSync(p) ? require(p) : {};
 assert.equal(typeof form.summaryHTML,'function');
 const doc = {custom_odt:'<script>',custom_mes_issue_category:'raw_material',custom_material_request_no:'MES<&'};
 const html = form.summaryHTML(doc,new Set(Object.keys(doc)));
 assert.match(html,/&lt;script&gt;/);
 assert.match(html,/原料申请/);
 assert.match(html,/MES&lt;&amp;/);
 assert.doesNotMatch(form.summaryHTML(doc,new Set()),/script|MES|原料申请/);
});
test('shared engine installs MR once while chaining MES callbacks and preserving native actions', async () => {
 let loads=0, refreshes=0, count=0;
 const indicator=()=>['Draft','red'], bulk=()=>{};
 const settings={onload(){loads++;},refresh(){refreshes++;},get_indicator:indicator,bulk_handler:bulk};
 const classes=new Set(),listeners=[];
 let route=['List','Material Request','List'];
 const env={frappe:{listview_settings:{'Material Request':settings},get_route:()=>route,router:{on(e,cb){listeners.push(cb);}},model:{std_fields_list:['name','owner']},perm:{has_perm:()=>true},boot:{},call:async call=>{count++; assert.match(call.method,/get_count$/); return {message:10};}},document:{body:{classList:{toggle(c,on){on ? classes.add(c) : classes.delete(c);}}}}};
 const list={doctype:'Material Request',view_name:'List',meta:{fields:mr.COLUMNS.map(c=>({fieldname:c.fieldname})).concat([{fieldname:'material_request_type'}])},fields:[['name','Material Request']],data:[],get_args(){return {filters:[],or_filters:[],fields:this.fields,order_by:'name asc'};},render_header(){},refresh(){},get_form_link(doc){return '/desk/material-request/'+doc.name;},get_indicator_html(){return 'native';}};
 env.cur_list=list;
 mr.install(env); mr.install(env); settings.onload(list); settings.refresh(list);
 assert.equal(loads,1); assert.equal(refreshes,1); assert.equal(settings.get_indicator,indicator); assert.equal(settings.bulk_handler,bulk); assert.equal(listeners.length,1);
 const controller=list.dlpMaterialRequestGrid;
 controller.quick.custom_odt='ODT'; list.get_args(); controller.setPage(3); controller.quick.custom_odt='ODT-2'; assert.equal(list.get_args().start,0);
 await list.render_count(); assert.equal(count,1); assert.deepEqual(controller.summary,[]);
 route=['Form','Material Request','MR-1']; listeners[0](); assert.equal(classes.has('dlp-material-request-grid-active'),false);
});
test('form refresh mounts one readonly first-content summary and never dirties the document', () => {
 const vm=require('node:vm');
 const source=fs.readFileSync(require('node:path').join(__dirname,base,'material_request_form.js'),'utf8');
 let mounts=0, installs=0, html='', handler;
 const wrapper={prependTo(target){assert.equal(target,'first-content'); mounts++;return this;},html(value){html=value;return this;}};
 const context={ $:()=>wrapper,frappe:{ui:{form:{on(doctype,events){assert.equal(doctype,'Material Request');installs++;handler=events.refresh;}}},perm:{has_perm:(doctype,level)=>level===0}}};
 vm.runInNewContext(source,context);
 context.DeepLinkERPMaterialRequestSummary.install();
 const doc={custom_odt:'ODT<1',custom_mes_issue_category:'semi_finished',custom_material_request_no:'secret',__unsaved:0};
 const frm={doc,meta:{fields:[{fieldname:'custom_odt'},{fieldname:'custom_mes_issue_category'},{fieldname:'custom_material_request_no',permlevel:1}]},layout:{wrapper:'first-content'},set_value(){throw Error('summary must never write');},dirty(){throw Error('summary must never dirty');},save(){throw Error('summary must never save');}};
 handler(frm); handler(frm);
 assert.equal(mounts,1);assert.equal(installs,1);assert.equal(doc.__unsaved,0);
 assert.match(html,/ODT&lt;1/);assert.match(html,/半成品申请/);assert.doesNotMatch(html,/secret/);
});
test('missing optional metadata cannot become invalid MR query or export fields', () => {
 const allowed=mr.allowedFields({fields:[{fieldname:'status'}]},()=>true,['name','owner']);
 const q=mr.buildQuery({fields:['`tabMaterial Request`.`name`','custom_odt'],filters:[],or_filters:[]},{custom_odt:'absent',custom_mes_issue_category:'absent',search:'id'},allowed);
 assert.deepEqual(q.fields,['`tabMaterial Request`.`name`']);assert.deepEqual(q.filters,[]);
 assert.deepEqual(q.or_filters,[['Material Request','name','like','%id%']]);
 assert.deepEqual(mr.buildRequests(q,['custom_odt','name'],allowed).export.fields,['name']);
});
test('legacy list retains blank ODT and category fallback as display-only columns', () => {
 const notices=[];
 const env={frappe:{model:{std_fields_list:['name','owner']},perm:{has_perm:()=>true},boot:{},get_route:()=>['List','Material Request','List'],show_alert:notice=>notices.push(notice)}};
 const list={doctype:'Material Request',view_name:'List',meta:{fields:[{fieldname:'material_request_type'}]},fields:[['name','Material Request']],data:[],get_args(){return {fields:this.fields,filters:[],or_filters:[]};},render_header(){},get_form_link(){return '/desk/material-request/MR';},get_indicator_html(){return ''}};
 const controller=mr.mount(list,env);
 assert.deepEqual(controller.preferences.columns.slice(0,2),['custom_odt','custom_mes_issue_category']);
 const header=list.get_header_html();
 assert.doesNotMatch(header,/data-sort-by="custom_odt"|data-sort-by="custom_mes_issue_category"/);
 assert.match(header,/data-sort-by="name"/);
 const html=list.get_list_row_html({name:'MR',material_request_type:'Material Issue',custom_odt:'must not expose'});
 assert.match(html,/data-fieldname="custom_odt"/);assert.match(html,/Material Issue/);assert.doesNotMatch(html,/must not expose/);
 assert.deepEqual(list.get_args().fields,[['name','Material Request'],['owner','Material Request'],['material_request_type','Material Request']]);
 assert.deepEqual(mr.buildRequests(list.get_args(),controller.preferences.columns,controller.allowed).export.fields,['name']);
 mr.mount(list,env);assert.equal(notices.length,1);
});
test('legacy stored sorting cannot query missing display-only fields', () => {
 const allowed=new Set(['name','owner']);
 for(const order_by of ['custom_odt asc','`tabMaterial Request`.`custom_mes_issue_category` desc']) {
  const q=mr.buildQuery({fields:['name'],filters:[],or_filters:[],order_by},{},allowed);
  assert.equal(q.order_by,'name desc');
  assert.equal(mr.buildRequests(q,['name'],allowed).export.order_by,'name desc');
 }
 const q=mr.buildQuery({fields:['name'],filters:[],or_filters:[],order_by:'custom_odt asc, name asc'},{},allowed);
 assert.equal(q.order_by,'name asc');
 const installed=mr.buildQuery({fields:['name'],filters:[],or_filters:[],order_by:'custom_odt asc'},{},new Set(['name','custom_odt']));
 assert.equal(installed.order_by,'custom_odt asc');
});
test('inherited category keys fall back to the original type in list and form', () => {
 const form=require(base+'material_request_form.js');
 for(const custom_mes_issue_category of ['constructor','__proto__','toString']) {
  const doc={custom_mes_issue_category,material_request_type:'Material Issue'};
  const translate=x=>x === 'Material Issue' ? '发料' : x;
  assert.equal(mr.renderValue('custom_mes_issue_category',doc,{translate}),'发料');
  const html=form.summaryHTML(doc,new Set(Object.keys(doc)),translate);
  assert.match(html,/>发料<\/span>/);assert.doesNotMatch(html,/function|Object/);
 }
});
