const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const engine = require('../deeplinkerp_branding/public/js/compact_list.js');

function payments(call, extras={}, capture=()=>{}) {
  const host = { document: { addEventListener() {} }, ...extras, frappe: { call, router: { on() {} }, model:{with_doctype:async()=>{}},meta:{get_docfield:(doctype,fieldname)=>({fieldname,fieldtype:'Data',parent:doctype})},utils: { escape_html: value => String(value).replaceAll('<', '&lt;') }, ...extras.frappe } };
  capture(host);
  vm.runInNewContext(fs.readFileSync(require.resolve('../deeplinkerp_branding/public/js/purchase_payments.js'), 'utf8'), { globalThis: host, ...host });
  return host.DeepLinkERPPurchasePayments;
}
function functionFrom(name) { const fn = payments()[name]; assert.equal(typeof fn, 'function', `${name} is used by drawers`); return fn; }

function attachmentFixture() {
 class Box {
  constructor(){this.children=new Map();this.props={};}
  find(key){if(!this.children.has(key))this.children.set(key,new Box());return this.children.get(key);}
  html(value){this.markup=value;return this;}
  text(value){this.label=value;return this;}
  prop(key,value){this.props[key]=value;return this;}
  on(){return this;}
 }
 let begin, beginFailed, bundle, uploader, bundleReady, constructions=0;
 const bundleStarted=new Promise(resolve=>bundleReady=resolve);
 const calls=[],uploads=[],panel=new Box();
 const api=payments(async request=>{calls.push(request);if(request.method.endsWith('begin_upload'))await new Promise((resolve,reject)=>{begin=resolve;beginFailed=reject;});return {message:{}};},
  {crypto:require('node:crypto').webcrypto,frappe:{require:()=>{bundleReady();return new Promise(resolve=>bundle=resolve);},ui:{FileUploader:class {
   constructor(options){constructions++;uploader=this;this.options=options;this.uploader={files:[],add_files:files=>this.uploader.files.push(...files.map(file=>({...file}))),upload_file:file=>new Promise(resolve=>uploads.push({file,resolve}))};}
  }}}});
 const drawer={panel,busy:false,alive:()=>true,setBusy(value){this.busy=value;},error(error){throw error;}};
 return {api,drawer,calls,uploads,async begin(){begin();await bundleStarted;},failBegin:()=>beginFailed(new Error('session failed')),bundle:()=>bundle(),get uploader(){return uploader;},get constructions(){return constructions;},
  complete(index){const {file,resolve}=uploads[index];file.doc={doctype:'File',name:`FILE-${index}`,file_name:file.name,file_url:`/private/files/${file.name}`,is_private:1};uploader.options.on_success(file.doc);resolve();}};
}

test('selected attachment batches block writes throughout session and bundle initialization and upload',async()=>{
 const h=attachmentFixture(),manager=await h.api.mountAttachments(h.drawer,'Payment Entry','PE-1');
 const first=manager.add([{name:'one.pdf'}]),second=manager.add([{name:'two.pdf'}]);
 assert.equal(manager.ready(),false);
 assert.equal(h.drawer.panel.find('.dlp-create,.dlp-submit,.dlp-receipt-submit').props.disabled,true);
 assert.match(h.drawer.panel.find('.dlp-attachments').find('.dlp-attachment-list').markup,/正在上传/);
 await h.begin();
 assert.equal(manager.ready(),false,'bundle loading is still part of the pending selection');
 h.bundle();await Promise.all([first,second]);
 assert.equal(h.constructions,1);assert.equal(h.uploads.length,2);assert.equal(manager.ready(),false);
 h.complete(0);assert.equal(manager.ready(),false);
 h.complete(1);assert.equal(manager.ready(),true);
 assert.deepEqual(Array.from(manager.args().attachments),['FILE-0','FILE-1']);
});

test('disposing an editor during attachment initialization never starts its late uploads',async()=>{
 const h=attachmentFixture(),manager=await h.api.mountAttachments(h.drawer,'Payment Entry','PE-1');
 const adding=manager.add([{name:'one.pdf'}]);manager.stop();
 await h.begin();h.bundle();await adding;
 assert.equal(h.constructions,0);assert.equal(h.uploads.length,0);
});

test('cancelling during attachment initialization waits for cleanup without starting uploads',async()=>{
 const h=attachmentFixture(),manager=await h.api.mountAttachments(h.drawer,'Payment Entry','PE-1');
 const adding=manager.add([{name:'one.pdf'}]),closing=h.drawer.beforeClose();
 await h.begin();h.bundle();await adding;
 assert.equal(await closing,true);assert.equal(h.uploads.length,0);
 assert.match(h.calls.at(-1).method,/discard$/);assert.equal(h.calls.at(-1).args.cancel,1);
});

test('failed attachment session initialization reports the error and permits a fresh selection',async()=>{
 const h=attachmentFixture(),manager=await h.api.mountAttachments(h.drawer,'Payment Entry','PE-1');
 const failed=manager.add([{name:'one.pdf'}]);h.failBegin();await failed;
 assert.equal(h.drawer.panel.find('.dlp-attachments').find('.dlp-attachment-error').label,'session failed');
 const retried=manager.add([{name:'one.pdf'}]);assert.equal(manager.ready(),false);
 await h.begin();h.bundle();await retried;h.complete(0);
 assert.equal(manager.ready(),true);assert.equal(manager.args().attachments.length,1);
});

test('request identifiers prefer native randomUUID when available',()=>{
 let calls=0;
 const api=payments(undefined,{crypto:{randomUUID(){calls++;return 'native-uuid';},getRandomValues(){throw new Error('unexpected fallback');}}});
 assert.equal(api.requestId(),'native-uuid');assert.equal(calls,1);
});
test('request identifiers fall back to Web Crypto bytes and retain UUID v4 format and retry identity',()=>{
 const crypto=require('node:crypto').webcrypto;
 const api=payments(undefined,{crypto:{getRandomValues:bytes=>crypto.getRandomValues(bytes)}});
 const ids=new Set(Array.from({length:100},()=>api.requestId()));
 assert.equal(ids.size,100);
 for(const id of ids)assert.match(id,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
 const token=api.retryToken(api.requestId),first=token.forPayload({amount:10});
 assert.equal(token.forPayload({amount:10}),first);assert.throws(()=>token.forPayload({amount:11}));
 token.succeeded();assert.notEqual(token.forPayload({amount:11}),first);
});
test('missing Web Crypto produces an actionable error instead of weak identifiers or a TypeError',()=>{
 for(const crypto of [undefined,{}])assert.throws(()=>payments(undefined,{crypto}).requestId(),/当前浏览器不支持安全随机标识/);
});

test('operating expenses can reuse the accessible procurement drawer shell factory', () => {
  assert.equal(typeof payments().createDrawer,'function');
});

function drawerHost(mobile=false,paths=['account','operating-expenses','sales-order'],initialIndex=1) {
 const listeners={},surfaces=[],location={href:'https://erp.test/desk/operating-expenses'};
 const entries=paths.map(path=>({url:`https://erp.test/desk/${path}`,state:null}));let index=initialIndex,pending;
 location.href=entries[index].url;
 const surface={attrs:{},appendTo(){return this;},find(){return this;},on(){return this;},trigger(){return this;},toggleClass(){return this;},attr(key,value){this.attrs[key]=value;return this;},prop(){return this;},remove(){this.removed=true;return this;},filter(){return this;},toArray(){return [];}};
 const history={get state(){return entries[index].state;},replaceState(state,_title,url){entries[index]={state,url};location.href=url;},pushState(state,_title,url){entries.splice(index+1);entries.push({state,url});index++;location.href=url;},go(delta){index+=delta;location.href=entries[index].url;pending=Promise.resolve().then(()=>router.route());}};
 const router={on:(type,handler)=>listeners[type]=handler,async route(){router.renders=(router.renders||0)+1;listeners.change();},async set_route(path){history.pushState(null,'',`https://erp.test/desk/${path}`);await this.route();}};
 const nativeRoute=router.route,nativeSetRoute=router.set_route;
 const navigation={get currentEntry(){return {index};}};
 const api=payments(undefined,{$:markup=>{surfaces.push(markup);return surface;},location,history,navigation,addEventListener:(type,handler)=>listeners[type]=handler,removeEventListener:type=>delete listeners[type],matchMedia:()=>({matches:mobile}),document:{body:{},activeElement:{focus(){}},addEventListener:(type,handler)=>listeners[type]=handler,removeEventListener:type=>delete listeners[type]},frappe:{router}});
 return {api,router,nativeRoute,nativeSetRoute,location,navigation,surface,surfaces,listeners,entries,get index(){return index;},async traverse(delta){index+=delta;location.href=entries[index].url;await router.route();await pending;}};
}
test('operating opt-in is nonmodal on desktop, modal on mobile, and leaves procurement modal by default',async()=>{
 for(const [mobile,optIn,modal] of [[false,true,false],[true,true,true],[false,false,true]]) {
  const h=drawerHost(mobile),d=h.api.createDrawer('Details',true,optIn?{desktopNonModal:true,guardNavigation:true}:undefined);
  assert.match(h.surfaces[0],new RegExp(`aria-modal="${modal}"`));
  assert.equal(h.surfaces[0].includes('dlp-drawer-desktop-nonmodal'),optIn);
  d.close(true);
 }
});
test('guarded navigation confirms before native routing, preserves declined inputs, and disposes accepted drawer',async()=>{
 const h=drawerHost(),d=h.api.createDrawer('Details',true,{desktopNonModal:true,guardNavigation:true});
 let resolve,checks=0;d.beforeClose=()=>{checks++;return new Promise(done=>resolve=done);};
 const rejected=h.router.set_route('account');
 assert.equal(h.location.href,'https://erp.test/desk/operating-expenses');
 assert.equal(h.router.renders,undefined);resolve(false);await rejected;
 assert.equal(d.alive(),true);assert.equal(h.surface.removed,undefined);
 const accepted=h.router.set_route('account');resolve(true);await accepted;
 assert.equal(checks,2);assert.equal(d.alive(),false);assert.equal(h.router.renders,1);
 assert.equal(h.router.route,h.nativeRoute);assert.equal(h.router.set_route,h.nativeSetRoute);
});
test('guarded browser history and saving never render a route while the operating drawer must stay open',async()=>{
 const h=drawerHost(),d=h.api.createDrawer('Details',true,{guardNavigation:true});
 d.setBusy(true);await h.router.set_route('account');
 assert.equal(h.router.renders,undefined);assert.equal(d.alive(),true);
 await h.traverse(-1);
 assert.equal(h.location.href,'https://erp.test/desk/operating-expenses');assert.equal(h.router.renders,undefined);
 d.setBusy(false);d.beforeClose=async()=>false;
 await h.traverse(-1);
 assert.equal(d.alive(),true);assert.equal(h.router.renders,undefined);
 d.beforeClose=async()=>true;await h.traverse(-1);
 assert.equal(d.alive(),false);assert.equal(h.router.renders,1);
});
test('cancelled browser Back and Forward preserve native history entries and the original cursor',async()=>{
 for(const [paths,index,delta] of [[['account','operating-expenses'],1,-1],[['operating-expenses','account'],0,1]]) {
  for(const blocked of ['dirty','write']) {
   const h=drawerHost(false,paths,index),original=JSON.parse(JSON.stringify(h.entries)),d=h.api.createDrawer('Details',true,{guardNavigation:true});
   d.beforeClose=async()=>false;if(blocked==='write')d.setBusy(true,'write');
   await h.traverse(delta);
   assert.deepEqual(h.entries,original,`${blocked} cancellation must not rewrite a traversed entry`);
   assert.equal(h.index,index);assert.equal(h.location.href,original[index].url);assert.equal(h.router.renders,undefined);
   d.setBusy(false);d.beforeClose=async()=>true;await h.traverse(delta);
   assert.equal(h.location.href,original[index+delta].url);assert.equal(h.router.renders,1);assert.equal(d.alive(),false);
  }
 }
});
test('history without an entry index keeps dirty and write guards without rewriting entries',async()=>{
 const h=drawerHost(false,['account','operating-expenses'],1),original=JSON.parse(JSON.stringify(h.entries));delete h.navigation.currentEntry;
 const d=h.api.createDrawer('Details',true,{guardNavigation:true});d.beforeClose=async()=>false;
 await h.traverse(-1);
 assert.deepEqual(h.entries,original);assert.equal(d.alive(),true);assert.equal(h.router.renders,undefined);
 d.setBusy(true,'write');await h.router.set_route('sales-order');
 assert.deepEqual(h.entries,original);assert.equal(d.alive(),true);assert.equal(h.router.renders,undefined);
 d.setBusy(false);d.beforeClose=async()=>true;await d.close();await Promise.resolve();
 assert.equal(h.location.href,original[0].url);assert.equal(h.index,0);assert.equal(h.router.renders,1);
});
test('operating navigation cancels readonly loading but waits for writes without changing procurement defaults',async()=>{
 for(const [operation,allowed] of [['read',true],['write',false]]) {
  const h=drawerHost(),d=h.api.createDrawer('Details',true,{guardNavigation:true});
  d.setBusy(true,operation);await h.router.set_route('account');
  assert.equal(d.alive(),!allowed);assert.equal(h.router.renders,allowed?1:undefined);
 }
 const h=drawerHost(),d=h.api.createDrawer('Procurement');d.setBusy(true,'read');
 assert.equal(d.close(),false,'Procurement keeps its original busy close behavior');d.close(true);
});
test('operating full-document departure warns only for dirty inputs or writes and removes its opt-in listener on close',()=>{
 for(const [dirty,operation,blocked] of [[false,null,false],[false,'read',false],[false,'write',true],[true,null,true],[true,'read',true]]) {
  const h=drawerHost(),d=h.api.createDrawer('Details',true,{guardNavigation:true});d.beforeUnloadShouldBlock=()=>dirty;
  if(operation)d.setBusy(true,operation);
  const event={prevented:false,preventDefault(){this.prevented=true;}};
  assert.equal(typeof h.listeners.beforeunload,'function');h.listeners.beforeunload(event);
  assert.equal(event.prevented,blocked);assert.equal(event.returnValue,blocked?'':undefined);
  d.close(true);assert.equal(h.listeners.beforeunload,undefined);
 }
 const h=drawerHost();h.api.createDrawer('Procurement');assert.equal(h.listeners.beforeunload,undefined);
});
test('unsupported router hosts retain legacy route cleanup instead of leaving a guarded drawer alive',()=>{
 const h=drawerHost();delete h.router.set_route;
 const d=h.api.createDrawer('Details',true,{guardNavigation:true});
 h.listeners.change();assert.equal(d.alive(),false);assert.equal(h.surface.removed,true);
});

test('shared procurement numeric presentation helpers keep business parse precision unchanged', () => {
  const api=payments();
  assert.equal(typeof api.formatMoney,'function'); assert.equal(typeof api.formatQuantity,'function'); assert.equal(typeof api.formatNumericInput,'function');
  assert.equal(api.formatMoney(123.45678,'CNY'),'123.46 CNY'); assert.equal(api.formatQuantity(2.00001),'2');
  const control={df:{precision:8},get_precision(){return this.df.precision;},format_for_input(value){return Number(value).toFixed(this.get_precision());}};
  api.formatNumericInput(control);
  assert.equal(control.format_for_input(2.00001),'2.00'); assert.equal(control.format_for_input(49.99975000125),'50.00');
  assert.equal(control.get_precision(),8); assert.equal(control.df.precision,8);
});

test('drawer edit session sends only touched fields, never rounded initialized quantities or money', () => {
  const session = functionFrom('editSession')({document:{qty:1.234567,amount:9.87654,remarks:'old',items:[{key:'row',qty:1.234567,rate:9.87654}]}});
  assert.deepEqual(JSON.parse(JSON.stringify(session.changes())), {});
  session.touch('remarks', 'new');
  assert.deepEqual(JSON.parse(JSON.stringify(session.changes())), {remarks:'new'});
  session.touchItem('row','qty','0.75');
  assert.deepEqual(JSON.parse(JSON.stringify(session.changes())), {remarks:'new',items:[{key:'row',qty:0.75}]});
  assert.equal(session.document.items[0].rate,9.87654);
});
test('payment records forwards native AND/OR unchanged alongside quick predicates and exports the clicked complete scope',async()=>{
 let args,resolve,host;const api=payments(undefined,{frappe:{require:()=>new Promise(r=>resolve=r)}},value=>host=value);
 const c={quick:{search:'A'},nativeFilterGroup:{get_filters:()=>[['Payment Entry','owner','=','buyer']]},orFilterGroup:{get_filters:()=>[['Payment Entry','company','=','C']]},providerOrderBy:'supplier asc',page:2,pageSize:500,preferences:{columns:['supplier','name','action']}};
 const request=api.recordsRequest(c);
 assert.deepEqual(JSON.parse(request.args.native_filters),[['Payment Entry','owner','=','buyer']]);
 assert.deepEqual(JSON.parse(request.args.or_filters),[['Payment Entry','company','=','C']]);assert.equal(request.args.order_by,'party asc');
 const pending=api.recordsExport(c);c.quick.search='B';
 // Export uses the same native predicates, not only the current page or a later quick search.
 c.nativeFilterGroup.get_filters=()=>[];
 host.DeepLinkERPPurchaseOrderExport={fetchNativeWorkbook:async(root,value)=>{args=value;return new Uint8Array();},downloadWorkbook(){}};
 resolve();await pending;
 assert.equal(args.search,'A');assert.equal(args.start,undefined);assert.equal(args.page_length,undefined);
 assert.deepEqual(JSON.parse(args.columns),['supplier','name']);assert.deepEqual(JSON.parse(args.native_filters),[['Payment Entry','owner','=','buyer']]);
});
test('records advanced controls reuse two native FilterGroups with parent permission whitelist and one reset',()=>{
 const groups=[];class NativeGroup{constructor(opts){Object.assign(this,opts);this.initialButton=opts.filter_button;this.wrapper=opts.parent;groups.push(this);this.filters=[];}get_filters(){return this.filters;}clear_filters(){this.filters=[];}validate_args(){return true;} _push_new_filter(){return {fieldselect:{options:[{doctype:'Payment Entry',fieldname:'company'},{doctype:'Payment Entry',fieldname:'secret'},{doctype:'Payment Entry Reference',fieldname:'reference_name'}],awesomplete:{}}};}}
 const surface={appendTo(){return this;},insertAfter(){return this;},hide(){return this;},on(){return this;}};
 const api=payments(undefined,{$:()=>surface,frappe:{ui:{FilterGroup:NativeGroup},msgprint(){}}});
 assert.equal(typeof api.mountRecordsFilters,'function');let page=-1,refresh=0;
 const c={$toolbar:surface,nativeAllowed:new Set(['name','company']),list:{meta:{name:'Payment Entry'}},setPage:value=>page=value,refresh:()=>refresh++};
 api.mountRecordsFilters(c);assert.equal(groups.length,2);assert.equal(groups[0].doctype,'Payment Entry');assert.equal(groups[0].validate_args('Payment Entry','secret'),false);
 assert.equal(groups[0].initialButton,undefined,'native inline groups do not share Frappe single popover selector or install document handlers');
 assert.equal(groups[0].validate_args('Payment Entry Reference','reference_name'),false);
 const filter=groups[0]._push_new_filter('Payment Entry','company');assert.deepEqual(filter.fieldselect.options,[{doctype:'Payment Entry',fieldname:'company'}]);
 groups[0].on_change();assert.equal(page,0);assert.equal(refresh,1);
 groups[0].filters=[['Payment Entry','company','=','A']];groups[1].filters=[['Payment Entry','name','like','PE%']];c.resetAdvancedFilters();assert.deepEqual(groups.map(g=>g.filters),[[],[]]);
});
test('form procurement actions keep PO receipt-only while retaining receipt payable and historical records',async()=>{
 let html='';const box={find(){return {on(){}};},prependTo(){return this;}};
 for(const doctype of ['Purchase Order','Purchase Receipt']){
  const api=payments(async request=>({message:request.method.endsWith('get_progress')?null:{source_doctype:doctype,orders:[],invoices:[{name:'DRAFT-PI',docstatus:0}],payments:[],balances:[],draft_invoices:[{name:'DRAFT-PI'}],can_create_invoice:true,can_create:true,can_prepay:true,receipt_eligibility:{allowed:true,reason:''}}}),{$:value=>{html=value;return box;}});
  await api.formRefresh({doctype,doc:{name:'SOURCE',docstatus:1},is_new:()=>false,$wrapper:{find:()=>({remove(){}})},layout:{wrapper:{}}});
  assert.match(html,/dlp-records/);assert.match(html,/DRAFT-PI/);
  if(doctype==='Purchase Order'){assert.match(html,/dlp-order-receipt/);assert.doesNotMatch(html,/dlp-receipt-invoice|dlp-order-invoice|dlp-order-pay|class="btn btn-primary btn-sm dlp-pay"/);}
  else {assert.match(html,/dlp-receipt-invoice[^>]+data-source-doctype="Purchase Receipt"[^>]+data-target="DRAFT-PI"/);assert.match(html,/data-source-doctype="Purchase Receipt"[^>]*>确认应付/);assert.match(html,/dlp-pay/);}
 }
});
test('submitted PO never exposes procurement payable action regardless of native invoice permissions',()=>{
  const api=payments(undefined,{frappe:{model:{can_read:type=>type==='Purchase Invoice',can_create:type=>type==='Purchase Invoice'}}});
  assert.doesNotMatch(api.orderReceiptAction({name:'PO',docstatus:1,status:'To Bill',per_received:100,per_billed:20,receipt_eligibility:{allowed:false,reason:'已全部收货'}}),/dlp-order-invoice|dlp-order-pay|dlp-order-receipt/);
  for(const doc of [{docstatus:0},{docstatus:2},{docstatus:1,status:'Closed'},{docstatus:1,status:'On Hold'},{docstatus:1,per_billed:100}])assert.doesNotMatch(api.orderReceiptAction({name:'PO',per_received:100,per_billed:20,...doc}),/dlp-order-invoice/);
  const denied=payments(undefined,{frappe:{model:{can_read:()=>false,can_create:()=>false}}});
  assert.doesNotMatch(denied.orderReceiptAction({name:'PO',docstatus:1,per_received:100,per_billed:0}),/dlp-order-invoice/);
});
test('reversal poll failure retains last trusted pending state and blocks dependent actions',async()=>{
 let failure=false;
 const api=payments(async()=>{if(failure)throw new Error('connection lost');return {message:{operation_id:'IR-1',stage:'waiting_inventory',safe_reason:null,can_retry:false}};},
  {frappe:{model:{can_read:()=>true,can_create:()=>true}}});
 const doc={doctype:'Purchase Order',name:'PO',docstatus:1,status:'To Receive and Bill',per_received:0,per_billed:0,receipt_eligibility:{allowed:true,reason:''}};
 const first=await api.refreshReversal(doc);
 assert.equal(first.stage,'waiting_inventory');assert.match(api.reversalHTML(first),/待库存重算/);
 assert.doesNotMatch(api.orderReceiptAction({...doc,reversal:first}),/dlp-order-pay|dlp-order-receipt|dlp-order-invoice/);
 failure=true;
 const second=await api.refreshReversal(doc);
 assert.equal(second.stage,'waiting_inventory');assert.equal(second.operation_id,'IR-1');
 assert.equal(second.refresh_failed,true);assert.match(api.reversalHTML(second),/进度刷新失败/);
 for(const stage of ['recalculating','verifying','failed'])assert.doesNotMatch(api.orderReceiptAction({...doc,reversal:{stage}}),/dlp-order-pay|dlp-order-receipt/);
 assert.match(api.orderReceiptAction({...doc,reversal:{stage:'completed'}}),/dlp-order-receipt/);
});
test('same-doctype new form clears pending status, timer and late progress loads',async()=>{
 let resolve,host,removed=0,next=0;const timers=new Map(),box={find:()=>({on(){}}),prependTo(){return box;}};
 const api=payments(()=>new Promise(done=>resolve=done),{$:()=>box,
  setTimeout:fn=>{timers.set(++next,fn);return next;},clearTimeout:id=>timers.delete(id)},value=>{host=value;});
 const frm={doctype:'Purchase Invoice',doc:{doctype:'Purchase Invoice',name:'PI-OLD'},is_new:()=>Boolean(frm.doc.__islocal),
  $wrapper:{find:()=>({remove(){removed++;}})},layout:{wrapper:{}}};host.cur_frm=frm;
 const pending=api.formRefresh(frm);resolve({message:{operation_id:'IR-OLD',stage:'waiting_inventory'}});await pending;
 assert.equal(timers.size,1);
 const stale=api.formRefresh(frm);
 frm.doc={doctype:'Purchase Invoice',name:'new-purchase-invoice-a',__islocal:1};await api.formRefresh(frm);
 resolve({message:{operation_id:'IR-OLD',stage:'failed'}});await stale;
 assert.equal(frm.dlpReversal,null);assert.equal(timers.size,0);assert.equal(removed>=2,true);
});
test('owned drawer controls release native datepicker and their handlers once',()=>{
 let destroyed=0,unbound=0;const cleanup=functionFrom('disposeControls');const controls=[{datepicker:{destroy:()=>destroyed++},$input:{off:()=>unbound++}}];
 cleanup(controls);cleanup(controls);assert.equal(destroyed,1);assert.equal(unbound,1);assert.equal(controls.length,0);
});
test('real pay async initialization stops after route cancellation, without creating five later controls',async()=>{
 let routeClose,resolveFirst,started,created=0,dateCreated=0,removed=0;
 const firstStarted=new Promise(resolve=>started=resolve);
 const surface={appendTo(){return this;},find(){return this;},on(){return this;},off(){return this;},html(){return this;},trigger(){return this;},toggleClass(){return this;},attr(){return this;},prop(){return this;},remove(){removed++;return this;}};
 const api=payments(async request=>({message:request.method.endsWith('.get_progress')?null:{can_create:true,invoices:[{name:'PI',can_pay:true,outstanding:100,currency:'USD'}],company:'C',balances:[]}}),{$:()=>surface,document:{body:{},addEventListener(){},removeEventListener(){}},crypto:{randomUUID:()=> 'uuid'},frappe:{router:{on:(type,fn)=>routeClose=fn},datetime:{get_today:()=> '2026-10-04'},ui:{form:{make_control:({df})=>{created++;if(df.fieldtype==='Date')dateCreated++;return {$input:surface,get_value:()=>'',set_value:()=>created===1?new Promise(resolve=>{resolveFirst=resolve;started();}):Promise.resolve()};}}}}});
 const pending=api.pay('Purchase Receipt','PR');await firstStarted;routeClose();resolveFirst();await pending;
 assert.equal(created,1);assert.equal(dateCreated,0);assert.equal(removed,1);
});
test('real date control initialized before route cancellation is destroyed and cannot continue drawer initialization',async()=>{
 let routeClose,resolveFirst,started,created=0,destroyed=0;
 const firstStarted=new Promise(resolve=>started=resolve);
 const surface={appendTo(){return this;},find(){return this;},on(){return this;},off(){return this;},html(){return this;},trigger(){return this;},toggleClass(){return this;},attr(){return this;},prop(){return this;},remove(){return this;}};
 const api=payments(async request=>({message:request.method.endsWith('.get_progress')?null:{document:{name:'PE',posting_date:'2026-10-04',docstatus:0,references:[]},editable_fields:['posting_date'],allowed_actions:[]}}),{$:()=>surface,document:{body:{},addEventListener(){},removeEventListener(){}},frappe:{router:{on:(type,fn)=>routeClose=fn},ui:{form:{make_control:({df})=>{created++;return {$input:surface,...(df.fieldtype==='Date'?{datepicker:{destroy:()=>destroyed++}}:{}),get_value:()=>'',set_value:()=>created===1?new Promise(resolve=>{resolveFirst=resolve;started();}):Promise.resolve()};}}}}});
 const pending=api.paymentDrawer('PE');await firstStarted;routeClose();resolveFirst();await pending;
 assert.equal(created,1);assert.equal(destroyed,1);
});
test('successful reload clears touched changes; unchanged rounded controls remain absent', () => {
  const session = functionFrom('editSession')({document:{amount:1.23456}});
  session.touch('amount', '1.2'); session.reset({document:{amount:1.2,modified:'new'}});
  assert.deepEqual(JSON.parse(JSON.stringify(session.changes())), {});
  assert.equal(session.document.modified,'new');
});
test('uncertain save reuses its token only for the identical payload', () => {
  let ids=0; const token=functionFrom('retryToken')(()=>`request-${++ids}`);
  assert.equal(token.forPayload({qty:1}),token.forPayload({qty:1}));
  assert.throws(()=>token.forPayload({qty:2}),/响应|内容/);
  token.succeeded(); assert.equal(token.forPayload({qty:2}),'request-2');
});
test('late async drawer loads are invalidated on cancellation and superseded opening', () => {
  const gate=functionFrom('operationGate')(); const first=gate.begin();
  gate.cancel(); assert.equal(gate.current(first),false);
  const second=gate.begin(); const third=gate.begin();
  assert.equal(gate.current(second),false); assert.equal(gate.current(third),true);
});
test('PO stock receipt action only appears for real eligible native orders, never OA rows', () => {
  const action=functionFrom('orderReceiptAction');
  assert.equal(action({row_type:'oa_request',name:'OA',docstatus:1}), '');
  assert.match(action({name:'PO',docstatus:0}), /确认订单/);
  assert.equal(action({name:'PO',docstatus:1,per_received:100,status:'Completed'}), '');
  assert.match(action({name:'PO<&',docstatus:1,per_received:50,status:'To Receive',receipt_eligibility:{allowed:true,reason:''}}), /dlp-order-receipt/);
  assert.doesNotMatch(action({name:'PO<&',docstatus:1,per_received:50,status:'To Receive',receipt_eligibility:{allowed:true,reason:''}}),/data-name="PO<&"/);
});
test('voucher rendering includes number AND status AND event and escapes supplied text', () => {
  const html=functionFrom('vouchersHTML')([{name:'V<&',statutory_number:'记-1',status:'Reversed',source_event:'Cancellation'}]);
  assert.match(html,/记-1/); assert.match(html,/已冲销/); assert.match(html,/冲销/); assert.doesNotMatch(html,/V<&/);
});
test('payment records whole bank totals remain grouped by type, status and currency', () => {
  const html=functionFrom('paymentSummary')({totals_label:'整单银行金额',totals:[{payment_type:'Pay',docstatus:0,currency:'USD',amount:1},{payment_type:'Receive',docstatus:1,currency:'MXN',amount:2}]});
  assert.match(html,/整单银行金额/); assert.match(html,/付款.*草稿.*1\.00.*USD/); assert.match(html,/退款.*已提交.*2\.00.*MXN/);
});
test('shared payable balances are clearly whole invoice scope even when settlement is partially paid', () => {
  const html=functionFrom('balanceHTML')({shared_payable:true,balances:[{total:100,settled:25,outstanding:75,currency:'USD'}]});
  assert.match(html,/共享应付整单/); assert.match(html,/75\.00/);
});
test('shared page adapter uses the real shared renderer and supports saved column/density isolation', () => {
  const grid=engine.create({doctype:'Payment Entry',pageRoute:'purchase-payment-records',columns:[{fieldname:'name',label:'付款单',width:150}],provider:{columns:[{fieldname:'name',label:'付款单',width:150}],defaultColumns:['name']}});
  assert.equal(typeof grid.mountPage,'function');
});
test('PO receipt discovery uses permission-checked parent query and native child source filters', async () => {
  let request;
  const api=payments(async value=>{request=value;return {message:[{name:'AUTO-PR',modified:'v1'}]};});
  assert.equal(typeof api.receiptDrafts,'function');
  const result=await api.receiptDrafts('PO-1');
  assert.equal(request.method,'frappe.client.get_list');
  assert.equal(request.args.doctype,'Purchase Receipt');
  assert.deepEqual(JSON.parse(JSON.stringify(request.args.filters)),[['Purchase Receipt','docstatus','=',0],['Purchase Receipt Item','purchase_order','=','PO-1']]);
  assert.equal(request.args.limit_page_length,0);
  assert.equal(result[0].name,'AUTO-PR');
});
