const test=require('node:test');
const assert=require('node:assert/strict');
const engine=require('../deeplinkerp_branding/public/js/compact_list.js');

// Only jQuery/Frappe host boundaries are replaced. Renderer/controller run intact.
class Surface {
  constructor(){this.value='';this.length=1;}
  find(selector){this.children ||=new Map();if(!this.children.has(selector))this.children.set(selector,new Surface());return this.children.get(selector);} first(){return this;} html(value){if(value===undefined)return this.value;this.value=value;return this;}
  text(value){this.value=value;return this;} append(value){this.value+=value;return this;} appendTo(parent){this.parent=parent;return this;} prependTo(){return this;} insertBefore(){return this;} insertAfter(){return this;}
  addClass(){return this;} removeClass(){return this;} toggleClass(){return this;} toggle(){return this;} hide(){return this;} show(){return this;} remove(){return this;} off(){return this;} on(event,selector,callback){this.handlers ||=new Map();this.handlers.set(typeof selector==='function'?event:selector,callback || selector);return this;} attr(){return this;} prop(){return this;} val(){return this;} each(){return this;}
}
function setup(options={}){
  let route=['purchase-payment-records'];const calls=[],classes=new Map(),storage=new Map(),routes=[];
  const root={$:()=>new Surface(),document:{body:{classList:{toggle:(key,value)=>classes.set(key,value)}}},localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>storage.set(key,value)},frappe:{get_route:()=>route,router:{on:(_,callback)=>routes.push(callback)},session:{user:'buyer'},boot:{sitename:'qa'},get_meta:()=>options.meta,perm:{has_perm:(doctype,level)=>level!==1},model:{can_export:()=>false},ui:{form:{make_control:()=>({set_value:async()=>{},get_value:()=>''})}},call(request){return new Promise(resolve=>calls.push({request,resolve}));}}};
  const columns=options.columns || [{fieldname:'name',label:'付款单',width:150},{fieldname:'amount',label:'金额',width:130},{fieldname:'references',label:'引用',width:100}];
  const grid=engine.create({doctype:'Payment Entry',purchaseChrome:options.purchaseChrome,mainFilterFields:[],pageRoute:'purchase-payment-records',routeClass:'only-payment-page',columns,numbers:['amount'],pageFieldMap:options.fieldMap,controls:options.controls,visibleColumns:options.visibleColumns,provider:{columns,sortFields:options.sortFields,formLink:doc=>`/desk/payment-entry/${encodeURIComponent(doc.name)}`,request:c=>({method:'records',args:{search:c.quick.search,native_filters:JSON.stringify(c.nativeFilters || []),or_filters:JSON.stringify(c.orFilters || []),order_by:c.providerOrderBy,start:c.page*c.pageSize,page_length:c.pageSize}}),renderValue:field=>field==='references'?'引用':undefined,summary:()=>''}});
  let showForm=0;const form=new Surface();
  const controller=grid.mountPage({main:new Surface(),wrapper:new Surface(),...(options.pageForm?{page_form:form,show_form:()=>showForm++}:{})},root);
  return {controller,calls,classes,storage,shown:()=>showForm,leave:()=>route=['List','Item'],navigate:value=>{route=value;routes.forEach(callback=>callback());}};
}
test('real page renderer shares column/density preferences and fixed pagination without native ListView data',async()=>{
  const {controller:c,calls,storage}=setup();c.setPage(2);c.pageSize=500;
  const loading=c.refresh();assert.equal(calls[0].request.args.start,1000);
  calls[0].resolve({message:{rows:[{name:'PE<&',amount:1.2345,currency:'USD'}],total_count:1200}});await loading;
  assert.match(c.list.$result.value,/1\.23 USD/);assert.match(c.list.$result.value,/PE&lt;&amp;/);assert.match(c.list.$result.value,/1001/);
  assert.deepEqual(c.list.data,[]);c.preferences.density='standard';c.setColumns(['amount','name']);
  assert.match([...storage.values()][0],/standard/);assert.ok(c.list.$result.value.indexOf('data-fieldname="amount"')<c.list.$result.value.indexOf('data-fieldname="name"'));
});
test('view specific columns disappear from the grid while retaining saved selections',async()=>{
  const {controller:c,calls}=setup({visibleColumns:(owner,field)=>owner.view!=='receipt'||field!=='references'});
  c.view='receipt';
  const pending=c.refresh();calls[0].resolve({message:{rows:[{name:'PE-1',references:['PI-1']}],total_count:1}});await pending;
  assert.doesNotMatch(c.list.$result.value,/data-fieldname="references"/);
  assert.ok(c.preferences.columns.includes('references'));
  c.view='all';c.list.render_list();
  assert.match(c.list.$result.value,/data-fieldname="references"/);
});
test('page response becomes stale as soon as filters change, even before delayed new dispatch',async()=>{
  const {controller:c,calls}=setup();const loading=c.refresh();c.quick.search='new';
  calls[0].resolve({message:{rows:[{name:'old'}],total_count:1}});await loading;
  assert.deepEqual(c.providerRows,[]);
});
test('leaving page invalidates its pending load and no route class leaks onto other lists',async()=>{
  const {controller:c,calls,classes,leave}=setup();const loading=c.refresh();leave();c.activate();
  calls[0].resolve({message:{rows:[{name:'old'}],total_count:1}});await loading;
  assert.deepEqual(c.providerRows,[]);assert.equal(classes.get('only-payment-page'),false);
});
test('standalone native Page exposes its hidden page_form before mounting quick controls',()=>{
  const {shown}=setup({pageForm:true});assert.equal(shown(),1);
});
test('readonly provider collection cells never expose object coercion as hover labels',async()=>{
  const {controller:c,calls}=setup();const loading=c.refresh();calls[0].resolve({message:{rows:[{name:'PE',references:[{name:'PI'}]}],total_count:1}});await loading;
  assert.doesNotMatch(c.list.$result.value,/title="\[object Object\]"/);
});
test('shared onboarding helper closes only delayed automatic overlay and cleans up on leaving route',()=>{
 let observer,disconnected=0,clicks=0,wrapper,route=['List','Purchase Order'];
 const root={document:{body:{},querySelector:()=>wrapper},MutationObserver:class{constructor(callback){observer=callback;}observe(){}disconnect(){disconnected++;}},frappe:{get_route:()=>route}};
 const grid=engine.create({doctype:'Purchase Order',columns:[]});const c={};
 assert.equal(typeof grid.dismissAutomaticOnboarding,'function');grid.dismissAutomaticOnboarding(c,root);
 wrapper={querySelector:()=>({click:()=>clicks++})};observer();assert.equal(clicks,1);assert.equal(disconnected,1);assert.equal(c.stopInitialOnboarding,null);
 grid.dismissAutomaticOnboarding(c,root);assert.equal(clicks,1,'deliberate later reopening is preserved');
 const next={};wrapper=null;grid.dismissAutomaticOnboarding(next,root);route=['List','Item'];observer();assert.equal(disconnected,2);
});
test('page metadata and native read permissions govern alias fields and quick controls',()=>{
 const {controller:c}=setup({meta:{fields:[{fieldname:'paid_amount',permlevel:1},{fieldname:'company',permlevel:0}]},fieldMap:{amount:'paid_amount'},controls:[{fieldname:'company',fieldtype:'Link'},{fieldname:'amount',permission_field:'paid_amount',fieldtype:'Currency'}]});
 assert.equal(c.nativeAllowed.has('paid_amount'),false);assert.equal(c.allowed.has('amount'),false);assert.equal(c.controls.amount,undefined);assert.ok(c.controls.company);
});
test('AND or OR changes reset standalone page scope and reject pending responses before redispatch',async()=>{
 const {controller:c,calls}=setup();c.setPage(2);const pending=c.refresh();assert.equal(calls[0].request.args.start,200);
 c.orFilters=[['Payment Entry','name','like','NEW%']];calls[0].resolve({message:{rows:[{name:'old'}],total_count:1}});await pending;assert.deepEqual(c.providerRows,[]);
 const fresh=c.refresh();assert.equal(calls[1].request.args.start,0);assert.equal(c.page,0);calls[1].resolve({message:{rows:[{name:'new'}],total_count:1}});await fresh;assert.equal(c.providerRows[0].name,'new');
});
test('first manual onboarding launch is preserved and empty initial watching expires',()=>{
 let callback,timer,manual,wrapper,clicks=0,removed=0,disconnected=0;
 const root={setTimeout:fn=>{timer=fn;return 1;},clearTimeout(){},document:{body:{},querySelector:()=>wrapper,addEventListener:(type,fn)=>manual=fn,removeEventListener:()=>removed++},MutationObserver:class{constructor(fn){callback=fn;}observe(){}disconnect(){disconnected++;}},frappe:{get_route:()=>['List','Purchase Order']}};
 const grid=engine.create({doctype:'Purchase Order',columns:[]}),c={};grid.dismissAutomaticOnboarding(c,root);
 manual?.({target:{closest:()=>({})}});wrapper={querySelector:()=>({click:()=>clicks++})};callback();assert.equal(clicks,0);assert.equal(disconnected,1);assert.equal(removed,1);
 wrapper=null;const fresh={};grid.dismissAutomaticOnboarding(fresh,root);timer?.();wrapper={querySelector:()=>({click:()=>clicks++})};callback();assert.equal(clicks,0);assert.equal(fresh.stopInitialOnboarding,null);
});

test('expanded details invalidate using one row index rather than rescanning every row',()=>{
 const rows=Array.from({length:1000},(_,index)=>({name:`PO-${index}`,modified:'new'}));
 rows.find=()=>assert.fail('expanded rows must not rescan the full page');
 const expanded=new Map([['PO-2',{header:{modified:'new'}}],['PO-900',{header:{modified:'old'}}],['removed',{header:{modified:'new'}}]]);
 engine.invalidateExpandedDetails(expanded,rows);
 assert.deepEqual([...expanded.keys()],['PO-2']);
});

test('shared row details cache one version and discard late page, collapsed or changed-version reads',async()=>{
 const reads=[];let renders=0;const owner={providerRows:[{name:'PR',modified:'v1'}],requestId:1,list:{render_list:()=>renders++,set_rows_as_checked(){}},root:{frappe:{get_route:()=>['List','Purchase Receipt']}},detailActive:()=>true};
 assert.equal(typeof engine.mountRowDetails,'function');
 engine.mountRowDetails(owner,{rows:()=>owner.providerRows,load:doc=>new Promise(resolve=>reads.push({doc,resolve}))});
 const first=owner.toggleRowDetails('PR');assert.equal(reads.length,1);
 await owner.toggleRowDetails('PR');reads[0].resolve({header:{modified:'v1'},items:[{item_code:'old'}]});await first;
 assert.equal(owner.expandedDetails.has('PR'),false);assert.equal(owner.detailCache.has('PR'),false);
 const next=owner.toggleRowDetails('PR');reads[1].resolve({header:{modified:'v1'},items:[{item_code:'visible'}]});await next;
 await owner.toggleRowDetails('PR');await owner.toggleRowDetails('PR');assert.equal(reads.length,2);assert.equal(owner.detailCache.get('PR').items[0].item_code,'visible');
 owner.providerRows=[{name:'PR',modified:'v2'}];owner.reconcileRowDetails();assert.equal(owner.detailCache.size,0);assert.equal(owner.expandedDetails.size,0);
 const newer=owner.toggleRowDetails('PR');owner.requestId++;reads[2].resolve({header:{modified:'v2'},items:[{item_code:'late'}]});await newer;assert.equal(owner.detailCache.size,0);
 assert.ok(renders>0);
});
test('row details can omit the bulk expand action without removing manual expansion',async()=>{
 let markup;
 const control={appendTo(){return this;},on(){return this;},toggle(){return this;},find(){return {prop(){}}}};
 const owner={providerRows:[{name:'MR-1'}],requestId:1,$toolbar:{},root:{$:value=>{markup=value;return control;}},list:{render_list(){}}};
 engine.mountRowDetails(owner,{rows:()=>owner.providerRows,load:()=>({items:[]}),showExpandAll:false});
 owner.mountRowDetailControls();
 assert.doesNotMatch(markup,/data-row-details="expand"/);
 assert.match(markup,/data-row-details="collapse"/);
 await owner.toggleRowDetails('MR-1');
 assert.equal(owner.expandedDetails.has('MR-1'),true);
});
test('failed details can retry and newer server versions show an explicit refresh notice',async()=>{
 let attempts=0;const owner={providerRows:[{name:'PR',modified:'v1'}],requestId:1,list:{render_list(){}},root:{}};
 engine.mountRowDetails(owner,{rows:()=>owner.providerRows,load:async()=>{if(++attempts===1)throw new Error('读取失败');return {header:{modified:'v2'},items:[{item_code:'stale'}]};}});
 await owner.toggleRowDetails('PR');assert.match(owner.detailCache.get('PR').error,/读取失败/);
 await owner.toggleRowDetails('PR');await owner.toggleRowDetails('PR');assert.equal(attempts,2);
 assert.match(owner.detailCache.get('PR').error,/已更新|刷新/);assert.equal(owner.detailCache.get('PR').items,undefined);
});
test('detail caches survive a replacement row with the same version and invalidate an in-place version change',async()=>{
 const owner={providerRows:[{name:'PR',modified:'v1'}],requestId:1,list:{render_list(){}},root:{}};let reads=0;
 engine.mountRowDetails(owner,{rows:()=>owner.providerRows,load:async()=>{reads++;return {header:{modified:'v1'},items:[]};}});
 await owner.toggleRowDetails('PR');owner.providerRows=[{name:'PR',modified:'v1'}];owner.reconcileRowDetails();
 assert.equal(owner.detailCache.size,1);assert.equal(owner.expandedDetails.size,1);
 owner.providerRows[0].modified='v2';owner.reconcileRowDetails();assert.equal(owner.detailCache.size,0);assert.equal(owner.expandedDetails.size,0);assert.equal(reads,1);
});
test('page expansion uses every visible row independently of selection and reuses open and cached details',async()=>{
 let renders=0,reads=0;
 const owner={page:0,requestId:1,providerRows:[{name:'A'},{name:'B'},{name:'C'}],root:{},list:{render_list:()=>renders++,get_checked_items:()=>['A']}};
 engine.mountRowDetails(owner,{rows:()=>owner.providerRows,load:doc=>{reads++;return {items:[doc]};}});
 assert.equal(typeof owner.expandAllRowDetails,'function');
 await owner.toggleRowDetails('A');const before=renders;
 await owner.expandAllRowDetails();assert.deepEqual([...owner.expandedDetails],['A','B','C']);assert.equal(reads,3);assert.equal(renders,before+1);
 await owner.expandAllRowDetails();assert.equal(reads,3);
 owner.collapseAllRowDetails();assert.equal(owner.expandedDetails.size,0);
 await owner.expandAllRowDetails();assert.equal(reads,3);assert.equal(owner.expandedDetails.size,3);
});
test('four live detail reads remain bounded across collapse, new batches and manual clicks with local failures',async()=>{
 const reads=[];let live=0,peak=0;
 const owner={page:0,requestId:1,providerRows:Array.from({length:10},(_,i)=>({name:`R${i}`})),root:{},list:{render_list(){}}};
 engine.mountRowDetails(owner,{rows:()=>owner.providerRows,load:doc=>new Promise((resolve,reject)=>{live++;peak=Math.max(peak,live);reads.push({doc,resolve:value=>{live--;resolve(value);},reject:error=>{live--;reject(error);}});})});
 assert.equal(typeof owner.expandAllRowDetails,'function');
 const old=owner.expandAllRowDetails();assert.equal(reads.length,4);
 owner.collapseAllRowDetails();assert.equal(owner.expandedDetails.size,0);assert.equal(owner.detailPending.size,0);
 const fresh=owner.expandAllRowDetails();assert.equal(reads.length,4,'cancelled reads still occupy their live slots');
 for(let i=0;i<4;i++)reads[i].resolve({items:[{item_code:'OLD'}]});await new Promise(resolve=>setImmediate(resolve));
 assert.equal(reads.length,8);assert.equal(owner.detailCache.size,0);
 reads[4].reject(new Error('row failed'));for(let i=5;i<8;i++)reads[i].resolve({items:[]});await new Promise(resolve=>setImmediate(resolve));
 let next=8;while(owner.detailPending.size){for(;next<reads.length;next++)reads[next].resolve({items:[]});await new Promise(resolve=>setImmediate(resolve));}await Promise.all([old,fresh]);
 assert.equal(peak,4);assert.match(owner.detailCache.get('R0').error,/row failed/);assert.equal(owner.detailCache.get('R9').items.length,0);
 owner.collapseAllRowDetails();owner.detailCache.clear();const manual=owner.toggleRowDetails('R0');const batch=owner.expandAllRowDetails();assert.equal(live,4);
 owner.collapseAllRowDetails();for(const read of reads.slice(14))read.resolve({items:[]});await Promise.all([manual,batch]);assert.equal(peak,4);
});
test('page and company changes close details while same-page refresh preserves the version cache',async()=>{
 const owner={page:0,quick:{company:'A'},requestId:1,providerRows:[{name:'R',modified:'v1'}],root:{},list:{render_list(){}},setPage(page){this.page=page;}};
 engine.mountRowDetails(owner,{rows:()=>owner.providerRows,load:()=>({items:[]})});
 assert.equal(typeof owner.expandAllRowDetails,'function');
 await owner.expandAllRowDetails();owner.requestId++;owner.providerRows=[{name:'R',modified:'v1'}];owner.reconcileRowDetails();assert.equal(owner.expandedDetails.size,1);
 owner.setPage(1);assert.equal(owner.expandedDetails.size,0);assert.equal(owner.detailCache.size,1);
 await owner.expandAllRowDetails();owner.quick.company='B';owner.reconcileRowDetails();assert.equal(owner.expandedDetails.size,0);
});
test('detail sizing follows outer viewport pixels and procurement text clamping preserves complete content only when opted in',()=>{
 const frames=[],values=new Map(),observed=[];let resized;
 const viewport={clientWidth:1200},result={style:{setProperty:(key,value)=>values.set(key,value)}};
 const owner={providerRows:[],list:{$result:{0:result,parent:()=>({0:viewport})},render_list(){}},root:{getComputedStyle:()=>({paddingLeft:'10px',paddingRight:'10px'}),requestAnimationFrame:callback=>{frames.push(callback);return frames.length;},addEventListener:(_,callback)=>resized=callback,ResizeObserver:class{constructor(callback){this.callback=callback;}observe(node){observed.push(node);}}}};
 engine.mountRowDetails(owner,{rows:()=>owner.providerRows,load:()=>({})});assert.equal(typeof owner.afterRowDetailsRender,'function');owner.afterRowDetailsRender();frames.shift()();
 assert.equal(values.get('--dlp-purchase-detail-width'),'1152px');assert.deepEqual(observed,[viewport]);
 viewport.clientWidth=620;resized();frames.shift()();assert.equal(values.get('--dlp-purchase-detail-width'),'572px');
 const escape=engine.create({doctype:'Purchase Order',columns:[]}).escapeHTML,item={item_code:'CODE'.repeat(12),item_name:'完整<&物料名称'.repeat(20),warehouse:'完整仓库'.repeat(15),uom:'完整单位'.repeat(10),amount:'12,193,259,148.30'};
 const options={columns:Object.keys(item).map(field=>[field,field]),items:[item],escape};
 const html=engine.detailTable({...options,wrapperClass:'dlp-purchase-detail-scroll',tableClass:'dlp-purchase-detail-table'});
 for(const field of ['item_name','warehouse','uom'])assert.ok(html.includes(`<td data-fieldname="${field}" title="${escape(item[field])}" class=""><span class="dlp-purchase-detail-text">${escape(item[field])}</span></td>`),`${field} retains full escaped title and DOM text in its inner clamp`);
 for(const field of ['item_code','amount'])assert.ok(html.includes(`<td data-fieldname="${field}" title="${escape(item[field])}" class="">${escape(item[field])}</td>`),`${field} is never text-clamped`);
 assert.doesNotMatch(engine.detailTable({...options,tableClass:'table-bordered'}),/dlp-purchase-detail-text/,'other detailTable consumers preserve their existing rendering');
});
test('async detail completion replaces only the expanded region and preserves native selection nodes',async()=>{
 let resolve,renders=0,updates=0;const frames=[],checkbox={checked:true},region={set outerHTML(value){updates++;assert.match(value,/READY/);}};
 const row={getAttribute:()=> 'R',querySelector:()=>region},result={querySelectorAll:()=>[row]};
 const template={content:{querySelector:()=>({outerHTML:'<div class="dlp-purchase-expanded">READY</div>'})}};
 const owner={providerRows:[{name:'R'}],list:{$result:{0:result},render_list(){renders++;owner.afterRowDetailsRender();},get_list_row_html:()=>'<div>READY</div>',$checkbox_cursor:checkbox},root:{document:{createElement:()=>template},requestAnimationFrame:fn=>{frames.push(fn);return frames.length;}}};
 engine.mountRowDetails(owner,{rows:()=>owner.providerRows,load:()=>new Promise(done=>resolve=done)});
 assert.equal(typeof owner.afterRowDetailsRender,'function');
 const pending=owner.toggleRowDetails('R');assert.equal(renders,1);resolve({items:[]});await pending;frames.shift()();
 assert.equal(renders,1);assert.equal(updates,1);assert.equal(owner.list.$checkbox_cursor,checkbox);assert.equal(checkbox.checked,true);
});
test('standalone route departure invalidates a pending detail even when returning before it resolves',async()=>{
 const {controller:c,navigate}=setup();let resolve;
 c.providerRows=[{name:'PR',modified:'v1'}];
 engine.mountRowDetails(c,{rows:()=>c.providerRows,load:()=>new Promise(done=>resolve=done)});
 const pending=c.toggleRowDetails('PR');navigate(['List','Item']);navigate(['purchase-payment-records']);
 resolve({header:{modified:'v1'},items:[{item_code:'OLD'}]});await pending;
 assert.equal(c.detailCache.has('PR'),false);assert.equal(c.expandedDetails.has('PR'),false);
});
test('native applied filters remain visible beside quick filters on the existing render lifecycle',()=>{
 const {controller:c}=setup({purchaseChrome:true}),section=new Surface();
 c.list.page.wrapper.find=selector=>selector==='.page-form > .filter-section'?section:new Surface();
 c.list.render_list();assert.ok(c.$appliedFilters);assert.equal(section.parent,c.$appliedFilters);
 section.parent=c.list.page.wrapper;c.list.render_list();assert.equal(section.parent,c.$appliedFilters,'native reattachment stays visible in the same applied-filter container');
});

test('standalone page removes an exact OR tag without changing its AND group',async()=>{
 const {controller:c}=setup({purchaseChrome:true});
 const group=tuple=>({filters:[{field:{},get_value:()=>tuple,remove(){}}],get_filters(){return this.filters.map(f=>f.get_value());},update_filter_button(){}});
 c.nativeFilterGroup=group(['Payment Entry','company','=','A']);c.orFilterGroup=group(['Payment Entry','company','=','B']);
 let refreshes=0;c.refresh=async()=>refreshes++;
 const tag=c.getAppliedFilters().find(tag=>tag.kind==='native_or');assert.ok(tag);
 await c.removeAppliedFilter(tag);assert.deepEqual(c.orFilterGroup.get_filters(),[]);
 assert.deepEqual(c.nativeFilterGroup.get_filters(),[['Payment Entry','company','=','A']]);assert.equal(refreshes,1);
});

test('business document native sort resets on entry and preserves manual sort within an entry',()=>{
 let route=['List','Purchase Receipt','List'];const routes=[];
 const root={frappe:{get_route:()=>route,router:{on:(_,callback)=>routes.push(callback)},perm:{has_perm:()=>true},model:{std_fields_list:['name']}}};
 const list={doctype:'Purchase Receipt',view_name:'List',meta:{fields:[{fieldname:'posting_date'}]},get_args(){return {order_by:this.sort_selector.get_sql_string()};},sort_selector:{set_value(field,order){this.sort_by=field;this.sort_order=order;},get_sql_string(){return `${this.sort_by} ${this.sort_order}`+(this.sort_by==='name'?'':`, name ${this.sort_order}`);}}};
 assert.equal(typeof engine.bindBusinessDateSort,'function');engine.bindBusinessDateSort(list,root);engine.bindBusinessDateSort(list,root);
 assert.equal(list.get_args().order_by,'posting_date desc, name desc');assert.equal(routes.length,1);
 list.sort_selector.set_value('name','asc');assert.equal(list.get_args().order_by,'name asc');
 routes[0]();assert.equal(list.get_args().order_by,'name asc','same list route preserves manual choice');
 route=['Form','Purchase Receipt','PR'];routes[0]();route=['List','Purchase Receipt','List'];routes[0]();
 assert.equal(list.get_args().order_by,'posting_date desc, name desc');
});
test('shared business date map preserves native callbacks and uses readable name when date is denied',()=>{
 const dates={transaction_date:['Material Request','Request for Quotation','Supplier Quotation','Purchase Order','Quotation','Sales Order'],posting_date:['Purchase Receipt','Purchase Invoice','Payment Entry','Sales Invoice','Delivery Note','POS Invoice','POS Opening Entry','POS Closing Entry'],apply_date:['OA Purchase Request']};
 let loaded=0;const root={frappe:{listview_settings:{'Quotation':{onload(){loaded++;}}},get_route:()=>['List','Quotation','List'],router:{on(){}},perm:{has_perm:(_dt,level)=>level===0},model:{std_fields_list:['name']}}};
 assert.equal(typeof engine.installBusinessDateSort,'function');engine.installBusinessDateSort(root);engine.installBusinessDateSort(root);
 const list={doctype:'Quotation',view_name:'List',meta:{fields:[{fieldname:'transaction_date',permlevel:1}]},get_args(){return {order_by:this.sort_by+' '+this.sort_order};}};
 root.frappe.listview_settings.Quotation.onload(list);assert.equal(loaded,1);assert.equal(list.get_args().order_by,'name desc');
 for(const [field,doctypes] of Object.entries(dates))for(const doctype of doctypes)assert.equal(engine.businessDateFields[doctype],field);
 assert.equal(engine.businessDateFields.Item,undefined);
});
test('standalone finance page starts and reenters by readable business date while same-entry refresh keeps chosen sort',()=>{
 const {controller:c,navigate}=setup({meta:{fields:[{fieldname:'posting_date'}]}});
 assert.equal(c.providerOrderBy,'posting_date desc');c.providerOrderBy='name asc';c.activate();assert.equal(c.providerOrderBy,'name asc');
 navigate(['List','Item']);navigate(['purchase-payment-records']);assert.equal(c.providerOrderBy,'posting_date desc');
 const denied=setup({meta:{fields:[{fieldname:'posting_date',permlevel:1}]}}).controller;assert.equal(denied.providerOrderBy,'name desc');
});
test('shared standalone header sorting sends one refreshed query and keeps chosen order through paging',()=>{
 const {controller:c,calls}=setup({sortFields:['posting_date','name'],meta:{fields:[{fieldname:'posting_date'}]}});
 const click=c.list.$result.handlers?.get('[data-provider-sort]');assert.equal(typeof click,'function');
 c.setPage(2);click({currentTarget:{dataset:{providerSort:'name'}}});assert.equal(calls.length,1);assert.equal(calls[0].request.args.order_by,'name asc');assert.equal(c.page,0);
 c.setPage(1);c.refresh();assert.equal(calls[1].request.args.order_by,'name asc');assert.equal(calls[1].request.args.start,100);
});
test('shared business column widths follow field identity and density while full company cells stay unclamped',async()=>{
 assert.equal(typeof engine.columnWidth,'function');
 for(const doctype of ['Purchase Order','Purchase Receipt','Purchase Invoice','Payment Entry','Sales Order']){
  assert.equal(engine.columnWidth({fieldname:'company',width:90},'tight',doctype),180);
  assert.equal(engine.columnWidth({fieldname:'company',width:90},'standard',doctype),180);
  assert.equal(engine.columnWidth({fieldname:'company',width:220},'tight',doctype),220);
  assert.equal(engine.columnWidth({fieldname:'status',width:104},'tight',doctype),88);
  assert.equal(engine.columnWidth({fieldname:'status',width:104},'standard',doctype),104);
  for(const [field,width] of [['posting_date',96],['grand_total',160],['amount',140]])assert.equal(engine.columnWidth({fieldname:field,width},'tight',doctype),width);
 }
 assert.equal(engine.columnWidth({fieldname:'company',width:90},'tight','Material Request'),90);
 const columns=[{fieldname:'name',width:150},{fieldname:'company',width:140},{fieldname:'state',width:155},{fieldname:'amount',width:140}];
 const {controller:c,calls}=setup({columns}),company='拉丁购国际电子商务（东莞）有限公司<&',pending=c.refresh();
 calls[0].resolve({message:{rows:[{name:'PE',company,state:'草稿',amount:12}],total_count:1}});await pending;
 assert.match(c.list.$result.value,/data-fieldname="company"[^>]*width:180px/);
 assert.match(c.list.$result.value,/拉丁购国际电子商务（东莞）有限公司&lt;&amp;/);
 assert.doesNotMatch(c.list.$result.value,/class="[^"]*ellipsis[^"]*" data-fieldname="company"/);
 const change=c.$toolbar.find('.dlp-po-density').handlers.get('change.dlpPO');c.setPage(1);c.quick.search='PE';c.providerOrderBy='name asc';
 change({target:{value:'standard'}});assert.match(c.list.$result.value,/data-fieldname="state"[^>]*width:155px/);
 change({target:{value:'tight'}});assert.match(c.list.$result.value,/data-fieldname="state"[^>]*width:112px/);
 assert.equal(c.page,1);assert.deepEqual(c.quick,{search:'PE'});assert.equal(c.providerOrderBy,'name asc');assert.equal(calls.length,1);
});
