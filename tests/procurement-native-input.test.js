const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

// Only DOM/transport boundaries are simulated. Numeric parse, validation,
// get_value, native change binding and formatting run the pinned Frappe source.
class Surface {
 constructor(value='', parent=null) { this.value=value; this.parent=parent; this.children=[]; this.events=[]; this.capture=[]; this.textValue=''; this.node={addEventListener:(type,fn)=>this.capture.push({type,fn}),removeEventListener:(type,fn)=>this.capture=this.capture.filter(e=>e.type!==type||e.fn!==fn)};this[0]=this.node; }
 appendTo(parent){this.parent=parent;parent?.children?.push(this);return this;}
 find(selector){this.found??=new Map();if(!this.found.has(selector))this.found.set(selector,new Surface('',this));return this.found.get(selector);}
 html(value){if(value!==undefined)this.textValue=value;return this;}
 text(value){this.textValue=value;return this;}
 on(types,fn){for(const type of types.split(' '))this.events.push({type,fn});return this;}
 off(names){const tokens=names.split(' ');this.events=this.events.filter(e=>!tokens.some(n=>n.startsWith('.')?e.type.includes(n):e.type===n));return this;}
 get(){return this.node;}
 val(value){if(arguments.length)this.value=value;return this.value;}
 trigger(){return this;}toggleClass(){return this;}attr(){return this;}prop(key,value){this.props??={};this.props[key]=value;return this;}remove(){this.removed=true;this.removals=(this.removals||0)+1;return this;}
 append(value){this.textValue+=value;return this;}
 async emit(type,value){if(value!==undefined)this.value=value;const event={type,target:this.node,currentTarget:this.node,preventDefault(){},stopPropagation(){}};for(const e of this.capture.filter(e=>e.type===type))e.fn(event);for(const e of this.events.filter(e=>e.type.split('.')[0]===type && typeof e.fn==='function'))await e.fn(event);await new Promise(resolve=>setImmediate(resolve));}
}
function projection(doctype='Purchase Invoice') {return {document:{doctype,name:null,docstatus:0,modified:'v1',company:'C',supplier:'S',currency:'USD',payment_type:'Pay',amount:100.1234,items:doctype==='Payment Entry'?[]:[{key:'ITEM',item_code:'I',item_name:'Native',qty:1.234567,rate:9.87654,max_qty:10,uom:'个',warehouse:'W'}],references:[]},editable_fields:doctype==='Payment Entry'?['amount']:[],editable_item_fields:doctype==='Payment Entry'?[]:['qty','rate'],allowed_actions:[]};}
function harness({document=projection(), drafts=[], precision, floatPrecision=3, locale='#.###,##', metadata=()=>Promise.resolve(), fieldDictionary,missingField,submit=async()=>({...document,document:{...document.document,docstatus:1}}),readChain,storage,rpc,record=async()=>({document:{doctype:'Payment Entry',name:'PE',amount:100,currency:'USD',docstatus:1}}),chain={can_create:true,company:'C',supplier:'S',balances:[],invoices:[{name:'PI',can_pay:true,outstanding:100,currency:'USD'}]}}={}) {
 const surfaces=[],controls=[],requests=[],loaded=[],listeners={},confirms=[],uploaders=[],documentEvents=[],metadataReads=[];
 const $=value=>{if(value instanceof Surface)return value;const surface=new Surface(value);surfaces.push(surface);return surface;};
 const fields={qty:{fieldname:'qty',fieldtype:'Float',...(precision?{precision}:{})},rate:{fieldname:'rate',fieldtype:'Currency',options:'currency',...(precision?{precision}:{})},paid_amount:{fieldname:'paid_amount',fieldtype:'Currency',options:'paid_from_account_currency',...(precision?{precision}:{})},received_amount:{fieldname:'received_amount',fieldtype:'Currency',options:'paid_to_account_currency'}};
 const getDocfield=(doctype,field)=>{
  metadataReads.push([doctype,field]);
  const native=fieldDictionary?fieldDictionary[doctype]?.[field]:fields[field] || {fieldtype:'Data'};
  return field===missingField || !native?undefined:{...native,fieldname:field,parent:doctype};
 };
 const host={$,window:null,console,sessionStorage:storage,document:{body:new Surface(),addEventListener:(type,fn,capture)=>documentEvents.push({type,fn,capture}),removeEventListener:(type,fn)=>{const index=documentEvents.findIndex(entry=>entry.type===type && entry.fn===fn);if(index!==-1)documentEvents.splice(index,1);}},crypto:{randomUUID:()=> 'uuid'},locals:{},__:v=>v,cint:(value,fallback=0)=>Number.isNaN(parseInt(value,10))?fallback:parseInt(value,10),replace_all:(value,from,to)=>value.split(from).join(to),is_null:v=>v==null,
  frappe:{ui:{form:{}},provide(){},utils:{debounce:fn=>fn},boot:{sysdefaults:{float_precision:floatPrecision,currency_precision:2,number_format:locale}},defaults:{get_default:key=>key==='float_precision'?floatPrecision:undefined},meta:{get_docfield:getDocfield,get_field_currency:(df,doc)=>doc[df.options]},model:{with_doctype:async doctype=>{loaded.push(doctype);await metadata(doctype);},get_value:()=>undefined},router:{on:(type,fn)=>listeners[type]=fn},datetime:{get_today:()=> '2026-10-04'},confirm:(_,fn)=>confirms.push(fn),show_alert(){},run_serially:fns=>{let result;for(const fn of fns)result=fn();return Promise.resolve(result);},call:async request=>{requests.push(request);if(rpc && (/purchase_fulfilment_service\.|\.get_order_progress$/.test(request.method) || request.method==='frappe.client.get'))return {message:await rpc(request)};if(request.method==='frappe.client.get_list')return {message:drafts};if(request.method.endsWith('.get_purchase_chain'))return {message:readChain?await readChain():chain};if(request.method.endsWith('.record_payment'))return {message:await record(request.args)};if(/submit_document|complete_payment/.test(request.method))return {message:await submit(request.args)};return {message:document};}}
 };
 host.window=host;host.globalThis=host;
 host.frappe.require=async()=>{};
 const nativeCall=host.frappe.call;host.frappe.call=async request=>{if(request.method.endsWith('.get_progress')){requests.push(request);return {message:host.reversal || null};}if(request.method.endsWith('.list_attachments') || request.method.endsWith('.discard')){requests.push(request);return {message:[]};}return nativeCall(request);};
 host.frappe.ui.FileUploader=class {constructor(options){this.options=options;this.transfers=[];this.uploader={files:[],add_files:files=>this.uploader.files.push(...files.map(file=>({name:file.name,uploading:false,request_succeeded:false}))),upload_file:file=>{this.transfers.push(file.name);return Promise.resolve();}};uploaders.push(this);}ack(index,name){const doc={doctype:'File',name,file_name:this.uploader.files[index].name,file_url:'/private/files/'+name,is_private:1};Object.assign(this.uploader.files[index],{doc,request_succeeded:true});this.options.on_success(doc);}};
 const context=vm.createContext(host);
 for(const file of ['frappe-native-number-format.js','frappe-native-number-controls.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'fixtures',file),'utf8'),context);
 // Native Link does not validate on every input event. Its selection path
 // uses the pinned BaseControl validation/set_model_value/df.change sequence.
 host.frappe.ui.form.ControlLink=class extends host.frappe.ui.form.ControlData {
  static trigger_change_on_input_event=false;
  // Native Link maps a translated/display title back to its exact record ID.
  get_input_value(){const input=super.get_input_value();return this.title_value_map?.[input] || input;}
 };
 host.frappe.ui.form.make_control=({df})=>{
  const Type=host.frappe.ui.form[`Control${df.fieldtype}`]||host.frappe.ui.form.ControlData;
  // The pinned native readonly Select formatter uses Data(value), not the
  // option label. Keep that DOM boundary faithful instead of hiding raw IDs.
  const control=Object.create(Type.prototype);Object.assign(control,{df:{...df},doc:{},$input:new Surface(),disp_area:new Surface(),get_status:()=>df.read_only?'Read':'Write',set_disp_area(value){this.disp_area.html(this.value ?? value ?? '');},in_grid:()=>false});
  control.bind_change_event();controls.push(control);return control;
 };
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../deeplinkerp_branding/public/js/purchase_payments.js'),'utf8'),context);
 const crossborderPath=path.join(__dirname,'../deeplinkerp_branding/public/js/crossborder_procurement.js');
 if(fs.existsSync(crossborderPath))vm.runInContext(fs.readFileSync(crossborderPath,'utf8'),context);
 const click=async selector=>{const button=surfaces.findLast(s=>typeof s.value==='string'&&s.value.includes(` ${selector}`));assert.ok(button,`button ${selector}`);await button.emit('click');};
 const html=()=>{const visit=s=>s.textValue+[...(s.found?.values()||[])].map(visit).join('');return surfaces.map(visit).join('');};
 const delegatedClick=async(className,dataset)=>{const button={tagName:'BUTTON',dataset,classList:{contains:name=>name===className}};const event={target:{closest:selector=>selector.includes('.'+className)?button:null},preventDefault(){},stopPropagation(){}};for(const entry of documentEvents.filter(entry=>entry.type==='click'))entry.fn(event);await new Promise(resolve=>setImmediate(resolve));};
 return {api:host.DeepLinkERPPurchasePayments,host,controls,requests,loaded,surfaces,confirms,uploaders,documentEvents,metadataReads,delegatedClick,click,html,confirm:()=>confirms.shift()(),routeClose:()=>listeners.change()};
}

test('accepted reversal blocks existing batch, payment and saved-payment drawers before preview',async()=>{
 for(const open of [h=>h.api.batchDocumentDrawer('Purchase Order',[{name:'PO'}]),h=>h.api.batchPay('Purchase Receipt',[{name:'PR'}]),h=>h.api.pay('Purchase Receipt','PR'),h=>h.api.paymentDrawer('PE')]){
  const h=harness();h.host.reversal={operation_id:'IR',stage:'failed',safe_reason:'native_repost_failed',can_retry:false};
  await open(h);
  assert.match(h.html(),/失败待处理/);
  assert.equal(h.controls.length,0);
  assert.equal(h.requests.filter(row=>/preview_|get_purchase_chain|record_|complete_payment/.test(row.method)).length,0);
 }
});

test('batch receipt preview is read only and explicit draft preserves exact source row edits',async()=>{
 const p=projection('Purchase Receipt');p.editable_item_fields=['qty','warehouse'];p.document.sources=[{name:'PO-A',modified:'v1'}];
 const batch={documents:[p],sources:[{name:'PO-A',modified:'v1'},{name:'PO-B',modified:'v2'}]};
 const h=harness({document:batch});
 assert.equal(typeof h.api.batchDocumentDrawer,'function');
 await h.api.batchDocumentDrawer('Purchase Order',batch.sources,'Purchase Receipt');
 assert.ok(h.requests.some(row=>row.method.endsWith('.preview_document_batch')));
 assert.equal(h.requests.filter(row=>/record_document_batch|submit_document/.test(row.method)).length,0);
 await h.controls.find(c=>c.df.fieldname==='qty').$input.emit('input','2,345');
 await h.click('dlp-batch-save');
 const write=h.requests.find(row=>row.method.endsWith('.record_document_batch'));
 assert.equal(write.args.confirm,0);assert.equal(write.args.changes[0].items[0].key,'ITEM');assert.equal(write.args.changes[0].items[0].qty,2.345);
});

test('batch validation acknowledgment allows corrected input while unknown response keeps the original request',async()=>{
 for(const unknown of [false,true]){
  const values=new Map(),storage={getItem:key=>values.get(key) || null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
  const batch={documents:[projection('Purchase Receipt')],sources:[{name:'PO',modified:'v1'}]},h=harness({document:batch,storage});
  let sequence=0;h.host.crypto.randomUUID=()=>`operation-${++sequence}`;
  const nativeCall=h.host.frappe.call;
  h.host.frappe.call=async request=>{if(request.method.endsWith('.record_document_batch')){h.requests.push(request);if(unknown)throw new Error('network response unknown');return {message:{failed:true,error:'单据已改变，请重新预览'}};}return nativeCall(request);};
  await h.api.batchDocumentDrawer('Purchase Order',batch.sources);
  await h.click('dlp-batch-save');
  const original=h.requests.find(row=>row.method.endsWith('.record_document_batch'));
  if(unknown){
   assert.equal(values.size,1);h.routeClose();await h.api.batchDocumentDrawer('Purchase Order',batch.sources);
   await h.click('dlp-batch-retry');
   const retried=h.requests.filter(row=>row.method.endsWith('.record_document_batch')).at(-1);
   assert.equal(JSON.stringify(retried.args),JSON.stringify(original.args));assert.equal(values.size,1);
  }else{
   assert.match(h.html(),/单据已改变，请重新预览/);assert.equal(values.size,0);
   await h.controls.find(c=>c.df.fieldname==='qty').$input.emit('input','2,345');await h.click('dlp-batch-save');
   const corrected=h.requests.filter(row=>row.method.endsWith('.record_document_batch')).at(-1);
   assert.notEqual(corrected.args.request_id,original.args.request_id);assert.equal(corrected.args.changes[0].items[0].qty,2.345);
   h.routeClose();await h.api.batchDocumentDrawer('Purchase Order',batch.sources);
   assert.equal(h.requests.filter(row=>row.method.endsWith('.preview_document_batch')).length,2);
  }
 }
});

test('rejected merged receipt preview cannot submit stale individual rows and can explicitly re-preview individual mode',async()=>{
 const batch={documents:[projection('Purchase Receipt'),projection('Purchase Receipt')],sources:[{name:'PO-A',modified:'v1'},{name:'PO-B',modified:'v2'}]},h=harness({document:batch});
 const nativeCall=h.host.frappe.call;
 h.host.frappe.call=async request=>{if(request.method.endsWith('.preview_document_batch')){h.requests.push(request);if(request.args.merge)throw new Error('固定税费请逐单入库');return {message:batch};}return nativeCall(request);};
 await h.api.batchDocumentDrawer('Purchase Order',batch.sources);
 const mode=h.surfaces[0].find('.dlp-batch-mode');mode.node.value='1';await mode.emit('change');
 await h.click('dlp-batch-save');
 assert.equal(h.requests.filter(row=>row.method.endsWith('.record_document_batch')).length,0);
 assert.match(h.html(),/固定税费请逐单入库/);
 await h.click('dlp-batch-individual');await h.click('dlp-batch-save');
 assert.deepEqual(h.requests.filter(row=>row.method.endsWith('.preview_document_batch')).map(row=>row.args.merge),[0,1,0]);
 const write=h.requests.find(row=>row.method.endsWith('.record_document_batch'));
 assert.equal(write.args.merge,0);assert.equal(write.args.changes.length,2);
});

test('merged payment confirms each native invoice amount with fresh source versions',async()=>{
 const h=harness({record:async()=>({document:{doctype:'Payment Entry',name:'PE',docstatus:1,amount:30,currency:'USD'}})});
 const nativeCall=h.host.frappe.call;
 h.host.frappe.call=async request=>{if(request.method.endsWith('.preview_payment_batch')){h.requests.push(request);return {message:{company:'C',supplier:'S',currency:'USD',sources:[{name:'PR-A',modified:'fresh-a'},{name:'PR-B',modified:'fresh-b'}],invoices:[{name:'PI-SHARED',outstanding:30,currency:'USD'},{name:'PI-B',outstanding:10,currency:'USD'}]}};}return nativeCall(request);};
 assert.equal(typeof h.api.batchPay,'function');
 await h.api.batchPay('Purchase Receipt',[{name:'PR-A'},{name:'PR-B'}]);
 assert.equal(h.requests.filter(request=>request.method.endsWith('.record_payment')).length,0);
 await h.click('dlp-create');
 const request=h.requests.find(row=>row.method.endsWith('.record_payment'));
 assert.equal(request.args.confirm,0);assert.equal(request.args.sources[0].modified,'fresh-a');assert.equal(request.args.sources[1].modified,'fresh-b');
 assert.equal(request.args.allocations.length,2);assert.equal(request.args.allocations[0].name,'PI-SHARED');assert.equal(request.args.allocations[0].amount,30);
});

for(const invalid of ['-5','not-a-number','Infinity'])test(`merged payment rejects native allocation ${invalid} without dropping input or dispatching`,async()=>{
 const h=harness(),nativeCall=h.host.frappe.call,nativeDocfield=h.host.frappe.meta.get_docfield;
 h.host.frappe.meta.get_docfield=(doctype,field)=>({...nativeDocfield(doctype,field),...(field==='allocated_amount'?{fieldtype:'Currency',options:'currency',precision:4}:{})});
 h.host.frappe.call=async request=>{if(request.method.endsWith('.preview_payment_batch')){h.requests.push(request);return {message:{company:'C',supplier:'S',currency:'USD',sources:[{name:'PR',modified:'fresh'}],invoices:[{name:'PI-A',outstanding:30,currency:'USD'},{name:'PI-B',outstanding:10,currency:'USD'}]}};}return nativeCall(request);};
 await h.api.batchPay('Purchase Receipt',[{name:'PR'}]);
 const amount=h.controls.find(control=>control.df.fieldname==='allocated_amount');await amount.$input.emit('input',invalid);
 await h.click('dlp-create');
 assert.equal(h.requests.filter(request=>request.method.endsWith('.record_payment')).length,0);
 assert.equal(amount.$input.val(),invalid);assert.match(h.html(),/有效.*非负/);
 await amount.$input.emit('input','0');await h.click('dlp-create');
 const write=h.requests.find(request=>request.method.endsWith('.record_payment'));
 assert.equal(write.args.allocations.length,1);assert.equal(write.args.allocations[0].name,'PI-B');assert.equal(write.args.allocations[0].amount,10);
});

test('merged payment filters only real zero and retains native positive partial allocations',async()=>{
 const h=harness(),nativeCall=h.host.frappe.call,nativeDocfield=h.host.frappe.meta.get_docfield;
 h.host.frappe.meta.get_docfield=(doctype,field)=>({...nativeDocfield(doctype,field),...(field==='allocated_amount'?{fieldtype:'Currency',options:'currency',precision:4}:{})});
 h.host.frappe.call=async request=>{if(request.method.endsWith('.preview_payment_batch')){h.requests.push(request);return {message:{company:'C',supplier:'S',currency:'USD',sources:[{name:'PR',modified:'fresh'}],invoices:['PI-A','PI-B','PI-C'].map(name=>({name,outstanding:30,currency:'USD'}))}};}return nativeCall(request);};
 await h.api.batchPay('Purchase Receipt',[{name:'PR'}]);
 const amounts=h.controls.filter(control=>control.df.fieldname==='allocated_amount');
 for(const [index,value] of ['0','10','2,3456'].entries())await amounts[index].$input.emit('input',value);
 await h.click('dlp-create');
 const write=h.requests.find(request=>request.method.endsWith('.record_payment'));
 assert.equal(JSON.stringify(write.args.allocations),JSON.stringify([{name:'PI-B',amount:10},{name:'PI-C',amount:2.3456}]));
});

test('submitted receipt next step replaces the singleton drawer and previews AP without writing',async()=>{
 const p=projection('Purchase Receipt');p.document.name='PR';p.document.docstatus=1;p.editable_item_fields=[];
 const h=harness({document:p});
 const nativeCall=h.host.frappe.call;
 h.host.frappe.call=async request=>{if(request.method.endsWith('.preview_document_batch')){h.requests.push(request);return {message:{documents:[projection()],sources:[{name:'PR',modified:'v1'}]}};}return nativeCall(request);};
 await h.api.documentDrawer('Purchase Order','PO','Purchase Receipt','PR');
 await h.click('dlp-next-ap');
 await new Promise(resolve=>setImmediate(resolve));
 assert.ok(h.requests.some(row=>row.method.endsWith('.preview_document_batch')));
 assert.equal(h.requests.filter(row=>row.method.endsWith('.record_document_batch')).length,0);
});

test('automatic submitted AP is shown in the same workflow without creating a second invoice',async()=>{
 const h=harness({chain:{can_create_invoice:false,invoices:[{name:'PI-AUTO',docstatus:1}],draft_invoices:[]}});
 await h.api.batchDocumentDrawer('Purchase Receipt',[{name:'PR'}],'Purchase Invoice');
 assert.match(h.html(),/PI-AUTO/);
 assert.equal(h.requests.filter(row=>/preview_document_batch|record_document_batch/.test(row.method)).length,0);
 assert.ok(h.surfaces.some(row=>String(row.value).includes('dlp-next-pay')));
});

test('one native parent with multiple PO item joins resumes its unique receipt draft',async()=>{
 const h=harness({document:projection('Purchase Receipt'),drafts:[{name:'PR-ONE',modified:'v2'},{name:'PR-ONE',modified:'v2'}]});
 await h.api.documentDrawer('Purchase Order','PO','Purchase Receipt');
 const previews=h.requests.filter(r=>r.method.endsWith('.preview_document'));
 assert.equal(previews.length,1);assert.equal(previews[0].args.target_name,'PR-ONE');
 assert.equal(h.requests[0].args.distinct,undefined,'do not invent unsupported native get_list options');
 assert.doesNotMatch(h.html(),/2 张可见入库草稿/);
});
test('payable entry selects its clicked native invoice and ignores other invoice drafts on the source',async()=>{
 const h=harness({chain:{can_create:true,company:'C',supplier:'S',balances:[],payments:[{name:'OTHER-DRAFT',docstatus:0,payment_type:'Pay',references:[{doctype:'Purchase Invoice',name:'PI-FIRST'}]}],invoices:[{name:'PI-FIRST',can_pay:true,outstanding:100,currency:'USD'},{name:'PI-CLICKED',can_pay:true,outstanding:250,currency:'CNY'}]}});
 await h.api.pay('Purchase Receipt','PR','PI-CLICKED');
 assert.equal(h.controls.find(c=>c.df.fieldname==='invoice').value,'PI-CLICKED');
 assert.equal(h.controls.find(c=>c.df.fieldname==='amount').value,250);
 assert.equal(h.requests.filter(r=>r.method.endsWith('.get_payment_document')).length,0);
});
test('payable entry whose invoice became ineligible does not switch to another invoice',async()=>{
 const h=harness();await h.api.pay('Purchase Receipt','PR','MISSING');
 assert.match(h.html(),/当前应付单已不可快捷付款/);assert.equal(h.controls.length,0);
});
test('multiple unique native receipt drafts keep sorted unique choices without guessing or previewing',async()=>{
 const h=harness({drafts:[{name:'PR-NEW',modified:'v2'},{name:'PR-NEW',modified:'v2'},{name:'PR-OLD',modified:'v1'}]});
 await h.api.documentDrawer('Purchase Order','PO','Purchase Receipt');
 assert.equal(h.requests.length,1);
 const html=h.html();assert.match(html,/2 张可见入库草稿/);assert.equal((html.match(/data-target="PR-NEW"/g)||[]).length,1);assert.ok(html.indexOf('data-target="PR-NEW"')<html.indexOf('data-target="PR-OLD"'));
});
test('procurement PO payment and direct payable entries stop before opening or reading',async()=>{
 const h=harness();
 await h.api.pay('Purchase Order','PO');
 await h.api.batchPay('Purchase Order',[{name:'PO'}]);
 await h.api.documentDrawer('Purchase Order','PO','Purchase Invoice','PI-DRAFT');
 await h.api.batchDocumentDrawer('Purchase Order',[{name:'PO'}],'Purchase Invoice');
 assert.equal(h.requests.length,0);assert.equal(h.controls.length,0);assert.equal(h.surfaces.length,0);
});
test('native default Float precision3 accepts qty0.004 while drawer display is only2',async()=>{
 const h=harness();await h.api.documentDrawer('Purchase Receipt','PR','Purchase Invoice');
 const qty=h.controls.find(c=>c.df.fieldname==='qty');await qty.$input.emit('change','0,004');
 assert.equal(qty.value,0.004,'native change stores the native precision, not presentation precision');
 assert.equal(qty.$input.val(),'0,00');
 await h.click('dlp-save');
 assert.equal(h.requests.find(r=>r.method.endsWith('.save_document_draft')).args.changes.items[0].qty,0.004);
 assert.deepEqual(h.loaded,['Purchase Invoice']);
});
test('explicit native metadata precision6 preserves changed qty and rate before native change reformats the input',async()=>{
 const document=projection();document.document.items[0].rate=8.1234;
 const h=harness({document,precision:6});await h.api.documentDrawer('Purchase Receipt','PR','Purchase Invoice');
 const qty=h.controls.find(c=>c.df.fieldname==='qty'),rate=h.controls.find(c=>c.df.fieldname==='rate');
 await qty.$input.emit('input','1,234');await qty.$input.emit('change');await rate.$input.emit('change','9,87654');
 assert.equal(qty.value,1.234);assert.equal(rate.value,9.87654);assert.equal(qty.$input.val(),'1,23');assert.equal(rate.$input.val(),'9,88');
 await h.click('dlp-save');
 assert.deepEqual(JSON.parse(JSON.stringify(h.requests.find(r=>r.method.endsWith('.save_document_draft')).args.changes)),{items:[{key:'ITEM',qty:1.234,rate:9.87654}]});
});
test('native default Currency precision2 remains2 for actual payment edits, never upgraded to6',async()=>{
 const document=projection('Payment Entry');document.allowed_actions=['Submit'];const h=harness({document});await h.api.paymentDrawer('PE');
 const amount=h.controls.find(c=>c.df.fieldname==='amount');await amount.$input.emit('change','9,87654');await h.click('dlp-submit');
 assert.equal(h.requests.find(r=>r.method.endsWith('.complete_payment')).args.changes.amount,9.88);
});
test('metadata loading is awaited and close invalidates it before any control creation',async()=>{
 let resolve,started;const waiting=new Promise(r=>started=r);const h=harness({metadata:()=>{started();return new Promise(r=>resolve=r);}});
 const pending=h.api.documentDrawer('Purchase Receipt','PR','Purchase Invoice');await Promise.race([waiting,new Promise(r=>setImmediate(r))]);assert.equal(typeof resolve,'function','native metadata must load before creating controls');h.routeClose();resolve();await pending;
 assert.equal(h.controls.length,0);
});
test('missing native metadata is a visible safe failure, not invented numeric precision',async()=>{
 const h=harness({missingField:'qty'});await h.api.documentDrawer('Purchase Receipt','PR','Purchase Invoice');
 assert.ok(!h.controls.some(c=>c.df.fieldname==='qty'));
 assert.match(h.html(),/元数据/);
});
test('numeric capture listeners belong to the drawer instance and are removed on route close',async()=>{
 const h=harness();await h.api.documentDrawer('Purchase Receipt','PR','Purchase Invoice');const qty=h.controls.find(c=>c.df.fieldname==='qty');
 assert.equal(qty.$input.capture.length,2);h.routeClose();assert.equal(qty.$input.capture.length,0);
});
test('untouched controls never send their rounded native initialization values to document save',async()=>{
 const h=harness();await h.api.documentDrawer('Purchase Receipt','PR','Purchase Invoice');await h.click('dlp-save');
 assert.deepEqual(JSON.parse(JSON.stringify(h.requests.find(r=>r.method.endsWith('.save_document_draft')).args.changes)),{});
});
for(const [precision,expected] of [[undefined,9.88],[6,9.87654]])test(`payment creation keeps captured native amount precision ${precision||'default2'} after native change formatting`,async()=>{
 const h=harness({precision});await h.api.pay('Purchase Receipt','PR');
 const amount=h.controls.find(c=>c.df.fieldname==='amount'),bank=h.controls.find(c=>c.df.fieldname==='bank');
 await amount.$input.emit('change','9,87654');await bank.set_value('Bank');assert.equal(amount.$input.val(),'9,88');await h.click('dlp-create');
 assert.equal(h.requests.find(r=>r.method.endsWith('.record_payment')).args.amount_to_pay,expected);
 assert.deepEqual(h.loaded,['Payment Entry']);assert.equal(amount.$input.capture.length,0,'successful close releases own capture listeners');
});
for(const action of ['pay','paymentDrawer'])test(`${action} cannot create controls after awaited metadata completes on a closed route`,async()=>{
 let resolve;const h=harness({document:projection('Payment Entry'),metadata:()=>new Promise(r=>resolve=r)});
 const pending=h.api[action]('Purchase Receipt','PR');await new Promise(r=>setImmediate(r));assert.equal(typeof resolve,'function');h.routeClose();resolve();await pending;assert.equal(h.controls.length,0);
});
test('metadata load rejection remains visible and creates no controls',async()=>{
 const h=harness({metadata:async()=>{throw new Error('network unavailable');}});await h.api.documentDrawer('Purchase Receipt','PR','Purchase Invoice');assert.equal(h.controls.length,0);assert.match(h.html(),/元数据/);assert.doesNotMatch(h.html(),/network unavailable/);
});
for(const doctype of ['Purchase Invoice'])for(const dirty of [false,true])test(`${doctype} ${dirty?'confirmed dirty':'unchanged'} refresh metadata rejection is visible and restores buttons without losing the old session`,async()=>{
 let loads=0;const h=harness({document:projection(doctype),metadata:async()=>{if(++loads>1)throw new Error('private network message');}});
 if(doctype==='Payment Entry')await h.api.paymentDrawer('PE');else await h.api.documentDrawer('Purchase Receipt','PR','Purchase Invoice');
 const control=h.controls.find(c=>c.df.fieldname===(doctype==='Payment Entry'?'amount':'qty'));
 if(dirty)await control.$input.emit('change',doctype==='Payment Entry'?'19,87':'1,234');
 await assert.doesNotReject(()=>h.click('dlp-reload'));
 if(dirty)await assert.doesNotReject(()=>h.confirm());
 assert.match(h.html(),/元数据/);assert.doesNotMatch(h.html(),/private network message/);
 assert.equal(h.surfaces[0].find('button').props.disabled,false);
 assert.ok(!h.requests.some(r=>/save_document_draft|update_payment_draft|submit_document/.test(r.method)),'refresh must not write');
 await h.click('dlp-save');const changes=h.requests.find(r=>/save_document_draft|update_payment_draft/.test(r.method)).args.changes;
 assert.deepEqual(JSON.parse(JSON.stringify(changes)),!dirty?{}:doctype==='Payment Entry'?{amount:19.87}:{items:[{key:'ITEM',qty:1.234}]});
});
test('late refresh metadata rejection after close cannot re-enable or append to a removed drawer',async()=>{
 let loads=0,reject;const h=harness({metadata:()=>++loads===1?Promise.resolve():new Promise((_,r)=>reject=r)});
 await h.api.documentDrawer('Purchase Receipt','PR','Purchase Invoice');const before=h.controls.length;
 const pending=h.click('dlp-reload');await new Promise(r=>setImmediate(r));assert.equal(typeof reject,'function');h.routeClose();const closedHTML=h.html();reject(new Error('network'));
 await assert.doesNotReject(()=>pending);assert.equal(h.controls.length,before);assert.equal(h.surfaces[0].removed,true);assert.equal(h.html(),closedHTML);assert.ok(h.controls.every(c=>c.$input.capture.length===0));
});
test('invoice submit success followed by metadata refresh failure reports completed operation',async()=>{
 let loads=0;const document=projection();document.document.name='DRAFT';document.allowed_actions=['Submit'];
 const h=harness({document,metadata:async()=>{if(++loads>1)throw new Error('private network failure');}});
 await h.api.documentDrawer('Purchase Receipt','PR','Purchase Invoice','DRAFT');await h.click('dlp-submit');await h.confirm();
 assert.equal(h.requests.filter(r=>r.method.endsWith('.submit_document')).length,1);
 assert.match(h.html(),/原生操作已完成.*刷新.*失败/);assert.doesNotMatch(h.html(),/提交未完成|private network failure/);
});
test('confirmed edited payment submits input in one call and keeps durable payment link',async()=>{
 const document=projection('Payment Entry');document.document.name='PE';document.allowed_actions=['Submit'];const h=harness({document});
 await h.api.paymentDrawer('PE');await h.controls.find(c=>c.df.fieldname==='amount').$input.emit('change','19,87');
 await h.click('dlp-submit');await h.click('dlp-submit');
 assert.ok(!h.surfaces.some(s=>typeof s.value==='string' && /class="[^"]*dlp-save(?:\s|")/.test(s.value)),'normal payment has no draft action');
 const writes=h.requests.filter(r=>/complete_payment|update_payment_draft/.test(r.method));assert.equal(writes.length,1);
 assert.equal(writes[0].args.changes.amount,19.87);assert.equal(writes[0].args.workflow_action,'Submit');assert.match(h.html(),/付款已提交/);assert.match(h.html(),/payment-entry\/PE/);
 assert.equal(h.confirms.length,0,'confirmation is the explicit main button');
});
test('payment RPC failure is visibly unconfirmed, permits only identical retry and never auto retries',async()=>{
 const document=projection('Payment Entry');document.document.name='PE';document.allowed_actions=['Submit'];
 const h=harness({document,submit:async()=>{throw new Error('permission denied');}});await h.api.paymentDrawer('PE');await h.click('dlp-submit');
 assert.match(h.html(),/permission denied/);assert.doesNotMatch(h.html(),/付款已提交/);assert.equal(h.requests.filter(r=>r.method.endsWith('.complete_payment')).length,1);
 assert.equal(h.surfaces[0].find('button').props.disabled,false);await h.click('dlp-submit');
 const writes=h.requests.filter(r=>r.method.endsWith('.complete_payment'));assert.equal(writes.length,2);assert.equal(writes[0].args.request_id,writes[1].args.request_id);
});
test('successful payment with failed balance refresh still reports submitted and blocks stale writes',async()=>{
 const document=projection('Payment Entry');document.document.name='PE';document.allowed_actions=['Submit'];
 const h=harness({document,readChain:async()=>{throw new Error('private network');}});await h.api.paymentDrawer('PE',{sourceType:'Purchase Receipt',sourceName:'PR'});
 await h.click('dlp-submit');await h.click('dlp-submit');
 assert.match(h.html(),/付款已提交.*余额刷新失败/);assert.doesNotMatch(h.html(),/付款未确认|private network/);
 assert.equal(h.requests.filter(r=>r.method.endsWith('.complete_payment')).length,1);
});
test('late balance failure after route close cannot append a result to a removed drawer',async()=>{
 let reject;const document=projection('Payment Entry');document.document.name='PE';document.allowed_actions=['Submit'];
 const h=harness({document,readChain:()=>new Promise((_,r)=>reject=r)});await h.api.paymentDrawer('PE',{sourceType:'Purchase Receipt',sourceName:'PR'});
 const pending=h.click('dlp-submit');await new Promise(r=>setImmediate(r));h.routeClose();const closedHTML=h.html();reject(new Error('network'));await pending;
 assert.equal(h.html(),closedHTML);assert.equal(h.surfaces[0].removed,true);
});
test('source payment action resumes a visible existing draft without creating another',async()=>{
 const document=projection('Payment Entry');document.document.name='EXISTING';document.allowed_actions=['Submit'];
 const h=harness({document,chain:{payments:[{name:'EXISTING',payment_type:'Pay',docstatus:0}],invoices:[],balances:[]}});await h.api.pay('Purchase Receipt','PR');
 assert.equal(h.requests.filter(r=>r.method.endsWith('.preview_payment')).length,1);assert.ok(!h.requests.some(r=>r.method.endsWith('.record_payment')));
 assert.match(h.html(),/已有待确认付款/);assert.match(h.html(),/采购核销引用/);
});
test('workflow actions stay explicit and no-submit user sees clear next step',async()=>{
 for(const allowed_actions of [[],['Review']]){
  const document=projection('Payment Entry');document.document.name='PE';document.allowed_actions=allowed_actions;const h=harness({document});await h.api.paymentDrawer('PE');
  assert.match(h.html(),allowed_actions.length?/付款待审批/:/没有提交或审批权限/);assert.ok(!h.requests.some(r=>r.method.endsWith('.complete_payment')));
 }
});

test('consecutive payment clicks share one in-flight native request',async()=>{
 let resolve,started;const waiting=new Promise(r=>started=r);const h=harness({record:()=>{started();return new Promise(r=>resolve=r);}});await h.api.pay('Purchase Receipt','PR');
 await h.controls.find(c=>c.df.fieldname==='bank').set_value('Bank');const pending=h.click('dlp-create');await waiting;await h.click('dlp-create');
 assert.equal(h.requests.filter(r=>r.method.endsWith('.record_payment')).length,1);resolve({document:{name:'PE',docstatus:1,amount:100,currency:'USD'}});await pending;
});

async function attachmentFixture(restored=null){
 const h=harness();const drawer={panel:new Surface(),busy:false,alive:()=>true,setBusy(value){this.busy=value;},error(error){this.errorMessage=error.message;}};
 const manager=await h.api.mountAttachments(drawer,'Purchase Receipt','PR',restored);return {...h,drawer,manager};
}
test('attachment sessions and multi-file upload work without crypto.randomUUID',async()=>{
 const h=harness(),crypto=require('node:crypto').webcrypto;
 h.host.crypto={getRandomValues:bytes=>crypto.getRandomValues(bytes)};
 const drawer={panel:new Surface(),busy:false,alive:()=>true,setBusy(value){this.busy=value;},error(error){this.errorMessage=error.message;}};
 const manager=await h.api.mountAttachments(drawer,'Sales Order','SO');
 const sessionId=manager.snapshot().sessionId;
 assert.match(sessionId,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
 await manager.add([{name:'receipt.pdf'},{name:'transfer.pdf'}]);
 assert.equal(h.requests.find(request=>request.method.endsWith('.begin_upload')).args.session_id,sessionId);
 h.uploaders[0].ack(0,'FILE-A');h.uploaders[0].ack(1,'FILE-B');
 assert.equal(manager.ready(),true);assert.equal(manager.args().attachments.length,2);
 manager.stop();
});
test('file selection survives clearing the native input during asynchronous upload initialization',async()=>{
 const h=await attachmentFixture(),input=h.drawer.panel.find('.dlp-attachments').find('input[type=file]');
 const live={0:{name:'receipt.pdf'},length:1};input.node.files=live;
 Object.defineProperty(input.node,'value',{set(){delete live[0];live.length=0;}});
 await input.emit('change');
 assert.equal(live.length,0);assert.deepEqual(h.uploaders[0].transfers,['receipt.pdf']);
 assert.equal(h.manager.ready(),false,'selection is captured, but still awaits server acknowledgement');h.manager.stop();
});
test('native early upload promise cannot enable payment before every server File acknowledgement',async()=>{
 const h=await attachmentFixture();await h.manager.add([{name:'receipt.pdf'},{name:'voucher.jpg'}]);
 assert.equal(h.manager.ready(),false);assert.deepEqual(JSON.parse(JSON.stringify(h.manager.args())),{});
 h.uploaders[0].ack(0,'FILE-A');assert.equal(h.manager.ready(),false);
 h.uploaders[0].ack(1,'FILE-B');assert.equal(h.manager.ready(),true);
 assert.deepEqual(JSON.parse(JSON.stringify(h.manager.args())).attachments,['FILE-A','FILE-B']);
 assert.equal(h.uploaders[0].options.allow_toggle_private,false);assert.equal(h.uploaders[0].options.allow_web_link,false);
 h.manager.stop();
});
test('partly failed native upload retains the successful File and blocks confirmation',async()=>{
 const h=await attachmentFixture();await h.manager.add([{name:'receipt.pdf'},{name:'failed.pdf'}]);h.uploaders[0].ack(0,'FILE-A');h.uploaders[0].uploader.files[1].failed=true;
 assert.equal(h.manager.ready(),false);assert.deepEqual(JSON.parse(JSON.stringify(h.manager.args())).attachments,['FILE-A']);
 assert.equal(h.uploaders.length,1,'never retransmit successful File automatically');h.manager.stop();
});
test('only an explicit removal or cancellation calls scoped native File cleanup',async()=>{
 const h=await attachmentFixture();await h.manager.add([{name:'receipt.pdf'}]);h.uploaders[0].ack(0,'FILE-A');
 await h.manager.remove('FILE-A');assert.equal(h.requests.find(r=>r.method.endsWith('.discard')).args.attachments[0],'FILE-A');
 await h.drawer.beforeClose();assert.equal(h.requests.filter(r=>r.method.endsWith('.discard')).length,2);
 assert.equal(h.requests.at(-1).args.cancel,1);h.manager.stop();
});
test('explicit retry transfers only a failed file and never retransmits a server-confirmed file',async()=>{
 const h=await attachmentFixture();await h.manager.add([{name:'good.pdf'},{name:'bad.pdf'}]);h.uploaders[0].ack(0,'FILE-A');Object.assign(h.uploaders[0].uploader.files[1],{failed:true,error_message:'Size exceeds maximum'});
 h.manager.retry(0);assert.deepEqual(h.uploaders[0].transfers,['good.pdf','bad.pdf','bad.pdf']);
 Object.assign(h.uploaders[0].uploader.files[1],{failed:true,error_message:'XMLHttpRequest Error'});h.manager.retry(0);assert.equal(h.uploaders[0].transfers.length,3,'unknown outcome is not automatically retried');h.manager.stop();
});
test('an uncertain payment close preserves uploaded Files and the original request',async()=>{
 const h=await attachmentFixture();await h.manager.add([{name:'receipt.pdf'}]);h.uploaders[0].ack(0,'FILE-A');h.drawer.pendingPayment=()=>true;
 await h.manager.remove('FILE-A');await h.manager.add([{name:'another.pdf'}]);
 assert.deepEqual(JSON.parse(JSON.stringify(h.manager.args())).attachments,['FILE-A']);assert.deepEqual(h.uploaders[0].transfers,['receipt.pdf']);
 assert.equal(await h.drawer.beforeClose(),true);assert.equal(h.requests.filter(r=>r.method.endsWith('.discard')).length,0);h.manager.stop();
});
test('discovered draft handoff preserves staged File IDs without another upload',async()=>{
 const h=await attachmentFixture();await h.manager.add([{name:'receipt.pdf'}]);h.uploaders[0].ack(0,'FILE-A');const snapshot=h.manager.snapshot();h.manager.stop();
 const next=await attachmentFixture(snapshot);assert.deepEqual(JSON.parse(JSON.stringify(next.manager.args())).attachments,['FILE-A']);assert.equal(next.uploaders.length,0);next.manager.stop();
});
test('optional empty attachments retain legacy no-file payment payload and committed close never cleans up',async()=>{
 const h=await attachmentFixture();assert.deepEqual(JSON.parse(JSON.stringify(h.manager.args())),{});h.manager.committed();assert.equal(await h.drawer.beforeClose(),true);assert.equal(h.requests.length,0);
});
test('an uncertain payment survives close/reopen and retries the original payload and token',async()=>{
 const values=new Map(),storage={getItem:k=>values.get(k),setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};let attempts=0;
 const h=harness({storage,record:async()=>{if(++attempts===1)throw new Error('network lost');return {document:{name:'PE',docstatus:1,amount:100,currency:'USD'}};}});
 await h.api.pay('Purchase Receipt','PR');await h.controls.find(c=>c.df.fieldname==='bank').set_value('Bank');await h.click('dlp-create');h.routeClose();
 await h.api.pay('Purchase Receipt','PR');assert.match(h.html(),/上次付款响应未确认/);await h.click('dlp-retry-payment');
 const writes=h.requests.filter(r=>r.method.endsWith('.record_payment'));assert.equal(writes.length,2);assert.deepEqual(writes[0].args,writes[1].args);assert.equal(values.size,0);
});
test('acknowledged native rollback permits correcting inputs and does not claim payment success',async()=>{
 let attempts=0;const h=harness({record:async()=>++attempts===1?{failed:true,error:'Native bank validation'}:{document:{name:'PE',docstatus:1,amount:30,currency:'USD'}}});await h.api.pay('Purchase Receipt','PR');
 await h.controls.find(c=>c.df.fieldname==='bank').set_value('Bank');await h.click('dlp-create');assert.match(h.html(),/Native bank validation/);assert.doesNotMatch(h.html(),/付款已提交/);
 await h.controls.find(c=>c.df.fieldname==='amount').$input.emit('change','30');await h.click('dlp-create');
 assert.equal(h.requests.filter(r=>r.method.endsWith('.record_payment'))[1].args.amount_to_pay,30);assert.match(h.html(),/付款已提交/);
});

function crossFixture(){
 const native={name:'INTERNAL',company:'Factory',supplier:'Internal supplier',currency:'MXN',modified:'internal-current',docstatus:0,items:[{name:'INT-ITEM',item_code:'I',qty:10.123456,uom:'个',stock_uom:'个',conversion_factor:1,rate:8.987654,amount:90.98765}]};
 const detail={name:'LINK',modified:'link-current',external_order:'PO',external_item:'EXT-ITEM',purchasing_company:'Buyer',beneficiary_company:'Factory',flow_kind:'external_internal',internal_order:'INTERNAL',internal_item:'INT-ITEM',seller_order:null,seller_item:null,cost_batch:'BATCH',custody_company:null,custody_warehouse:null,active:1,allocated_qty:'1.234567',allocated_uom:'个',stock_uom:'个',external_modified:'po-current',internal_modified:'internal-current',seller_modified:null,native_internal:native,source_versions:{external_modified:'po-current',internal_modified:'internal-current'},price_confirmation:{},snapshot:{state:'review',warnings:['评论存在预计、否定、部分或数量/目的地含糊，需人工核对'],confirmed:false,erp_received:false,source_context:{root_source_id:'SOURCE',corp_id:'CORP',instance_id:'APPROVAL'},timeline:[{source_id:'COMMENT<&',state:'review',raw:{user_name:'Author<&',operation_time:'2026-10-07<&',remark:'预计到货 <img onerror=x>'}}]},manual_nodes:[{node:'reported_arrival',note:'人工报告 <script>',qty:'1.234567',uom:'个',by:'Reporter<&',on:'Time<&',erp_received:false}]};
 const progress={name:'PO',modified:'po-current',currency:'USD',docstatus:1,external:{company:'Buyer',supplier:'Vendor'},items:[{name:'EXT-ITEM',item_code:'I',item_name:'Native material',qty:10.123456,uom:'个'}],item_fields:['name','item_code','item_name','qty','uom'],internal:[{name:'LINK',internal_order:'INTERNAL',allocations:[{name:'LINK',external_item:'EXT-ITEM'}]}],receipt_logistics:[{name:'LINK'}]};
 return {native,detail,progress};
}
function crossHarness({fixture=crossFixture(),links=true,write=true,costCapability='available',rpc,fieldDictionary=crossFieldDictionary(),...options}={}){
 const calls=[],lifecycle=[];
 const h=harness({...options,fieldDictionary,rpc:async request=>{
  calls.push(request);if(rpc){const result=await rpc(request,fixture);if(result!==undefined)return result;}
  if(request.method.endsWith('.get_order_progress'))return {PO:{...fixture.progress,...(!links?{internal:[],receipt_logistics:[]}:{})}};
  if(request.method.endsWith('.get_link_detail'))return fixture.detail;
  if(request.method.endsWith('.get_link_candidates'))return {purchase_orders:[fixture.native],warehouses:['WAREHOUSE'],warnings:[]};
  if(request.method==='frappe.client.get')return {name:request.args.name,modified:'seller-current',company:'Buyer',items:[{name:'SELL-ITEM',item_code:'I',uom:'个'}]};
  return {name:'LINK',modified:'next',active:1};
 }});
 h.host.frappe.model.can_write=()=>write;h.host.frappe.get_route=()=>['List','Purchase Order','List'];
 if(costCapability!=='absent')h.host.frappe.boot.user={can_read:costCapability==='available'?['Overseas Cost Batch']:[]};
 h.host.frappe.model.can_read=doctype=>h.host.frappe.boot.user?.can_read.includes(doctype);
 const list={get_args:()=>({filters:[]})};const c={root:h.host,list,requestId:1,providerRows:[{name:'PO',row_type:'purchase_order',company:'Buyer',role_context:{buyer_company_proposal:'Suggested buyer',source_beneficiary_company_hint:'Raw factory',beneficiary_company_candidate:'Suggested factory'}}],invalidatePurchaseDetails:names=>lifecycle.push(['invalidate',...names]),refresh:async()=>lifecycle.push(['refresh'])};
 list.dlpPurchaseOrderGrid=c;h.host.cur_list=list;
 const open=async tab=>{assert.equal(typeof h.host.DeepLinkERPCrossborderProcurement?.open,'function','crossborder integration is a real renderer/controller');await h.host.DeepLinkERPCrossborderProcurement.open('PO',tab || 'internal',c);};
 const control=name=>h.controls.findLast(row=>row.df.fieldname===name);
 const event=async(selector,dataset={})=>{assert.match(h.html(),new RegExp(selector.slice(1)));const panel=h.surfaces.findLast(s=>s.value?.includes?.('dlp-payment-overlay'));const button=panel.find(selector);button.node.dataset=dataset;await button.emit('click');};
 const change=async(name,value)=>{const input=control(name);assert.ok(input,`native ${name} control`);await input.set_value(value);if(input.df.fieldtype!=='Link')await input.$input.emit('change');};
 return {...h,c,calls,lifecycle,open,control,event,change};
}
function crossFieldDictionary(){
 const fields=JSON.parse(fs.readFileSync(path.join(__dirname,'../deeplinkerp_branding/deeplinkerp_branding/doctype/purchase_fulfilment_link/purchase_fulfilment_link.json'),'utf8')).fields;
 return {
  'Purchase Fulfilment Link':Object.fromEntries(fields.map(field=>[field.fieldname,field])),
  // Verified against the pinned ERPNext Purchase Order JSON, not a fallback
  // for arbitrary missing names. Purchase Order has terms but no remarks.
  'Purchase Order':{terms:{fieldname:'terms',fieldtype:'Text Editor'},title:{fieldname:'title',fieldtype:'Data'}},
  'Purchase Order Item':{qty:{fieldname:'qty',fieldtype:'Float',reqd:1,non_negative:1}},
  'Payment Entry':{posting_date:{fieldtype:'Date'},paid_amount:{fieldtype:'Currency',options:'paid_from_account_currency'},received_amount:{fieldtype:'Currency',options:'paid_to_account_currency'},paid_from:{fieldtype:'Link',options:'Account'},paid_to:{fieldtype:'Link',options:'Account'},reference_no:{fieldtype:'Data'},remarks:{fieldtype:'Small Text'}},
 };
}
async function enterNewAssociation(h){
 await h.change('beneficiary_company','Factory');await h.change('external_item','EXT-ITEM');
 await h.change('internal_order','INTERNAL');await h.change('internal_item','INT-ITEM');
 await h.control('allocated_qty').$input.emit('change','1,234');
}
async function uncertainAssociation(h){await h.open();await enterNewAssociation(h);await h.click('dlp-cross-save');}
// Frappe leaves scalar nulls for jQuery's form encoding, where null becomes
// an empty string. Missing keys alone retain the server's None defaults.
const formFields=args=>new URLSearchParams(Object.entries(args).map(([key,value])=>[key,value ?? '']));

test('validated native beneficiary selection synchronizes exact Company before candidates without a manufactured DOM event',async()=>{
 const h=crossHarness({links:false});await h.open();const company=h.control('beneficiary_company');
 await company.$input.emit('input','Fact');assert.equal(h.calls.filter(request=>request.method.endsWith('.get_link_candidates')).length,0);
 await company.parse_validate_and_set_in_model('Factory',null,'Factory native label');
 const candidates=h.calls.filter(request=>request.method.endsWith('.get_link_candidates'));assert.equal(candidates.length,1);assert.equal(candidates[0].args.beneficiary_company,'Factory');
 assert.ok(h.control('internal_order').df.options.some(option=>option.value==='INTERNAL'));
 const before=h.control('internal_order').df.options;
 await company.set_value('Factory',true);await company.$input.emit('change');
 assert.equal(h.control('internal_order').df.options,before,'native callback and blur do not duplicate the dependent reaction');
 assert.ok(!company.$input.events.some(event=>event.type.endsWith('.dlpDrawer') || event.type==='change.dlpCrossborder'));
 await h.change('flow_kind','internal_local');await h.change('flow_kind','external_internal');
 assert.ok(h.calls.filter(request=>request.method.endsWith('.get_link_candidates')).every(request=>request.args.beneficiary_company==='Factory'));
 assert.equal(h.calls.filter(request=>request.method.endsWith('.get_link_candidates')).length,2,'one query per exact validated role context');
});
test('validated native custody Company selection supplies exact authorized warehouse context and save identity',async()=>{
 const h=crossHarness({links:false});await h.open();await h.change('flow_kind','trade_custody');
 await h.control('beneficiary_company').set_value('Buyer');assert.equal(h.calls.filter(request=>request.method.endsWith('.get_link_candidates')).length,0);
 await h.control('custody_company').parse_validate_and_set_in_model('Factory',null,'Factory native label');
 const request=h.calls.find(request=>request.method.endsWith('.get_link_candidates'));assert.ok(request);assert.equal(request.args.beneficiary_company,'Buyer');assert.equal(request.args.custody_company,'Factory');
 await h.change('external_item','EXT-ITEM');await h.change('custody_warehouse','WAREHOUSE');await h.control('allocated_qty').$input.emit('change','0,004');await h.click('dlp-cross-save');
 const args=h.calls.find(request=>request.method.endsWith('.save_link')).args;assert.equal(args.custody_company,'Factory');assert.equal(args.custody_warehouse,'WAREHOUSE');assert.equal(args.internal_order,null);
});
test('validated native seller SO selection reads its exact items and saves its current source version',async()=>{
 const h=crossHarness({links:false});await h.open();await enterNewAssociation(h);
 await h.control('seller_order').parse_validate_and_set_in_model('SELLER',null,'Seller native label');
 const request=h.calls.find(request=>request.method==='frappe.client.get');assert.ok(request);assert.equal(request.args.name,'SELLER');
 await h.control('seller_order').$input.emit('change');assert.equal(h.calls.filter(request=>request.method==='frappe.client.get').length,1);
 await h.change('seller_item','SELL-ITEM');await h.click('dlp-cross-save');
 const args=h.calls.find(request=>request.method.endsWith('.save_link')).args;assert.equal(args.seller_order,'SELLER');assert.equal(args.seller_item,'SELL-ITEM');assert.equal(args.expected_seller_modified,'seller-current');
});
test('validated native cost Link selection dirties confirmation and saves the exact batch without DOM change',async()=>{
 const fixture=crossFixture();fixture.detail.price_confirmation={version:'CONFIRMED',currency:'MXN'};
 const h=crossHarness({fixture});await h.open();await h.control('cost_batch').parse_validate_and_set_in_model('BATCH-VALIDATED',null,'Batch label');
 assert.match(h.surfaces[0].find('.dlp-cross-price').textValue,/关联修改未保存.*确认待重新核对/);
 await h.click('dlp-cross-save');assert.equal(h.calls.find(request=>request.method.endsWith('.save_link')).args.cost_batch,'BATCH-VALIDATED');
});
test('validated native UOM selection accompanies native precision quantity without DOM change',async()=>{
 const h=crossHarness();await h.open('logistics');await h.change('manual_note','本人核对数量');await h.control('manual_qty').$input.emit('change','0,004');
 await h.control('manual_uom').parse_validate_and_set_in_model('个',null,'单位');await h.click('dlp-cross-manual');
 const request=h.calls.find(request=>request.method.endsWith('.set_manual_node'));assert.ok(request);assert.equal(request.args.qty,0.004);assert.equal(request.args.uom,'个');
});
test('validated native File selection sends its exact existing ID without DOM change',async()=>{
 const h=crossHarness();await h.open('logistics');await h.change('evidence_note','本人核对已有文件');
 await h.control('evidence_file').parse_validate_and_set_in_model('FILE-VALIDATED',null,'File display label');await h.click('dlp-cross-evidence');
 assert.equal(h.calls.find(request=>request.method.endsWith('.add_evidence')).args.file,'FILE-VALIDATED');
});
test('deferred native Company validation runs no role read until its exact value resolves and then uses current flow',async()=>{
 const h=crossHarness({links:false});await h.open();const company=h.control('beneficiary_company');let resolve;
 company.validate=()=>new Promise(done=>resolve=done);await company.$input.emit('input','Fact');const pending=company.parse_validate_and_set_in_model('Factory',null);
 await h.change('flow_kind','internal_local');assert.equal(h.calls.filter(request=>request.method.endsWith('.get_link_candidates')).length,0);
 resolve('Factory');await pending;const request=h.calls.find(request=>request.method.endsWith('.get_link_candidates'));assert.ok(request);assert.equal(request.args.beneficiary_company,'Factory');assert.equal(request.args.flow_kind,'internal_local');
});
test('native invalid Company validation cannot forward the typed partial value into a later role read',async()=>{
 const h=crossHarness({links:false});await h.open();const company=h.control('beneficiary_company');
 await company.$input.emit('input','Fact');company.validate=async()=>undefined;await company.parse_validate_and_set_in_model('Fact',null);
 await h.change('flow_kind','internal_local');assert.equal(h.calls.filter(request=>request.method.endsWith('.get_link_candidates')).length,0);
 await h.click('dlp-cross-save');assert.equal(h.calls.filter(request=>request.method.endsWith('.save_link')).length,0);
});
test('out-of-order candidate responses from native Company selections cannot replace the latest exact role options',async()=>{
 let resolve,started;const wait=new Promise(done=>started=done);
 const h=crossHarness({links:false,rpc:async(request,fixture)=>{
  if(request.method.endsWith('.get_link_candidates')){
   if(request.args.beneficiary_company==='Factory'){started();return new Promise(done=>resolve=done);}
   return {purchase_orders:[{...fixture.native,name:'LATEST-PO',company:'Latest Factory'}],warnings:[]};
  }
 }});await h.open();const company=h.control('beneficiary_company'),first=company.set_value('Factory');
 await Promise.race([wait,new Promise(done=>setImmediate(done))]);assert.ok(resolve,'native selection actually dispatched its dependent candidate read');
 await company.set_value('Latest Factory');const options=h.control('internal_order').df.options;assert.ok(options.some(option=>option.value==='LATEST-PO'));
 resolve({purchase_orders:[crossFixture().native],warnings:[]});await first;assert.equal(h.control('internal_order').df.options,options);assert.equal(company.value,'Latest Factory');
});
test('out-of-order seller reads from native SO selections cannot restore an older source item or version',async()=>{
 let resolve,started;const wait=new Promise(done=>started=done);
 const h=crossHarness({links:false,rpc:async request=>{
  if(request.method==='frappe.client.get'){
   if(request.args.name==='SELLER-OLD'){started();return new Promise(done=>resolve=done);}
   return {name:'SELLER-LATEST',modified:'seller-latest',items:[{name:'LATEST-SELL-ITEM',uom:'个'}]};
  }
 }});await h.open();await enterNewAssociation(h);const seller=h.control('seller_order'),first=seller.set_value('SELLER-OLD');
 await Promise.race([wait,new Promise(done=>setImmediate(done))]);assert.ok(resolve,'native selection actually dispatched its dependent seller read');
 await seller.set_value('SELLER-LATEST');resolve({name:'SELLER-OLD',modified:'seller-old',items:[{name:'OLD-SELL-ITEM',uom:'个'}]});await first;
 assert.ok(h.control('seller_item').df.options.some(option=>option.value==='LATEST-SELL-ITEM'));assert.ok(!h.control('seller_item').df.options.some(option=>option.value==='OLD-SELL-ITEM'));
 await h.change('seller_item','LATEST-SELL-ITEM');await h.click('dlp-cross-save');assert.equal(h.calls.find(request=>request.method.endsWith('.save_link')).args.expected_seller_modified,'seller-latest');
});
const linkIntentValues={beneficiary_company:['Factory','Other Factory'],custody_company:['Factory','Other Custody'],seller_order:['SELLER-OLD','SELLER-NEXT'],cost_batch:['BATCH','BATCH-NEXT'],manual_uom:['Nos','Kg'],evidence_file:['FILE-OLD','FILE-NEXT']};
async function linkWriteFixture(field){
 const logistics=['manual_uom','evidence_file'].includes(field),[old,next]=linkIntentValues[field];
 const h=crossHarness({links:logistics || field==='cost_batch',rpc:async(request,fixture)=>{
  if(request.method.endsWith('.get_link_candidates') && request.args.beneficiary_company==='Other Factory')return {purchase_orders:[{...fixture.native,name:'OTHER-INTERNAL',company:'Other Factory',modified:'other-current',items:[{...fixture.native.items[0],name:'OTHER-INT-ITEM'}]}],warnings:[]};
  if(request.method.endsWith('.get_link_candidates') && request.args.custody_company==='Other Custody')return {warehouses:['OTHER-WAREHOUSE'],warnings:[]};
  if(request.method==='frappe.client.get')return {name:request.args.name,modified:request.args.name+'-current',items:[{name:request.args.name==='SELLER-NEXT'?'SELL-ITEM-NEXT':'SELL-ITEM-OLD',item_code:'I',uom:'个'}]};
 }});await h.open(logistics?'logistics':'internal');
 if(['beneficiary_company','seller_order'].includes(field))await enterNewAssociation(h);
 if(field==='custody_company'){
  await h.change('flow_kind','trade_custody');await h.control('beneficiary_company').set_value('Buyer');await h.control(field).set_value(old);
  await h.change('external_item','EXT-ITEM');await h.change('custody_warehouse','WAREHOUSE');await h.control('allocated_qty').$input.emit('change','0,004');
 }
 if(field==='seller_order'){await h.control(field).set_value(old);await h.change('seller_item','SELL-ITEM-OLD');}
 if(field==='manual_uom'){await h.change('manual_note','本人核对数量');await h.control('manual_qty').$input.emit('change','0,004');await h.control(field).set_value(old);}
 if(field==='evidence_file'){await h.change('evidence_note','本人核对已有文件');await h.control(field).set_value(old);}
 return {h,field,control:h.control(field),old,next,key:field==='manual_uom'?'uom':field==='evidence_file'?'file':field,action:field==='manual_uom'?'dlp-cross-manual':field==='evidence_file'?'dlp-cross-evidence':'dlp-cross-save',writes:()=>h.calls.filter(request=>/\.(save_link|set_manual_node|add_evidence)$/.test(request.method))};
}
async function selectChangedLinkDependencies({h,field}){
 if(field==='beneficiary_company'){await h.change('internal_order','OTHER-INTERNAL');await h.change('internal_item','OTHER-INT-ITEM');}
 if(field==='custody_company')await h.change('custody_warehouse','OTHER-WAREHOUSE');
 if(field==='seller_order')await h.change('seller_item','SELL-ITEM-NEXT');
}
for(const field of Object.keys(linkIntentValues))test(`pending native Link write ${field} never submits its old selection and requires another explicit click`,{timeout:5000},async()=>{
 const fixture=await linkWriteFixture(field),{h,control,old,next,action,writes,key}=fixture;let resolve;
 control.validate=()=>new Promise(done=>resolve=done);await control.$input.emit('input',next);const pending=control.parse_validate_and_set_in_model(next,null);
 try{
  assert.equal(control.inside_change_event,true);assert.equal(control.value,old);
  await h.click(action);assert.equal(writes().length,0);assert.match(h.html(),/等待.*验证.*重新选择.*再次点击/);
 }finally{resolve(next);await pending;}
 assert.equal(writes().length,0,'validation never retries a blocked write');await selectChangedLinkDependencies(fixture);await h.click(action);
 assert.equal(writes().length,1);assert.equal(writes()[0].args[key],next);
});
for(const field of Object.keys(linkIntentValues))test(`unvalidated native Link write ${field} cannot submit a no-blur typed partial over its valid old selection`,{timeout:5000},async()=>{
 const fixture=await linkWriteFixture(field),{h,control,old,next,action,writes,key}=fixture;
 await control.$input.emit('input','Partial unvalidated intent');assert.equal(control.value,old);assert.notEqual(control.get_input_value(),old);
 await h.click(action);assert.equal(writes().length,0);assert.match(h.html(),/等待.*验证.*重新选择.*再次点击/);
 await control.set_value(next);await selectChangedLinkDependencies(fixture);assert.equal(writes().length,0);await h.click(action);
 assert.equal(writes().length,1);assert.equal(writes()[0].args[key],next);
});
for(const field of Object.keys(linkIntentValues))test(`invalid native Link result ${field} never retries the blocked write or sends its previous validated value`,{timeout:5000},async()=>{
 const {h,control,next,action,writes}=await linkWriteFixture(field);let resolve;
 control.validate=()=>new Promise(done=>resolve=done);await control.$input.emit('input',next);const pending=control.parse_validate_and_set_in_model(next,null);
 try{await h.click(action);assert.equal(writes().length,0);}finally{resolve(undefined);await pending;}
 assert.equal(writes().length,0);assert.equal(control.value,undefined);
 if(['beneficiary_company','custody_company','manual_uom'].includes(field)){await h.click(action);assert.equal(writes().length,0,'required cleared native identity stays ineligible');}
});
for(const field of Object.keys(linkIntentValues))test(`translated native Link intent ${field} saves its mapped exact ID rather than rejecting its display label`,{timeout:5000},async()=>{
 const {h,control,old,action,writes,key}=await linkWriteFixture(field);control.title_value_map={'可读翻译标题':old};control.$input.val('可读翻译标题');
 assert.notEqual(control.$input.val(),control.value);assert.equal(control.get_input_value(),old);await h.click(action);
 assert.equal(writes().length,1);assert.equal(writes()[0].args[key],old);
});
test('validated Link callback gap blocks a synchronized-value mismatch without relying only on pending validation or input text',{timeout:5000},async()=>{
 const {h,control,next,action,writes}=await linkWriteFixture('cost_batch'),nativeChanged=control.df.change;let release;
 const wait=new Promise(done=>release=done);control.df.change=function(...args){return wait.then(()=>nativeChanged.apply(this,args));};
 const pending=control.parse_validate_and_set_in_model(next,null);
 try{
  assert.equal(control.inside_change_event,false);assert.equal(control.value,next);assert.equal(control.get_input_value(),next);
  await h.click(action);assert.equal(writes().length,0);assert.match(h.html(),/等待.*验证.*重新选择.*再次点击/);
 }finally{release();await pending;}
 assert.equal(writes().length,0);await h.click(action);assert.equal(writes()[0].args.cost_batch,next);
});
for(const field of Object.keys(linkIntentValues))for(const cancellation of ['route','filter'])test(`late native ${field} validation cannot read or update a ${cancellation}-cancelled drawer`,{timeout:5000},async()=>{
 const {h,control,next,action,writes}=await linkWriteFixture(field);let resolve;control.validate=()=>new Promise(done=>resolve=done);
 await control.$input.emit('input',next);const pending=control.parse_validate_and_set_in_model(next,null),before=h.calls.length;
 if(cancellation==='route')h.routeClose();else{h.c.requestId++;h.api.cancelCrossborderForList(h.c);}
 resolve(next);await pending;await h.click(action);assert.equal(h.calls.length,before);assert.equal(writes().length,0);assert.equal(h.surfaces[0].removed,true);
});

for(const costCapability of ['absent','denied'])test(`optional cost capability ${costCapability} has no native Link to focus or query and permits a no-cost association`,async()=>{
 const h=crossHarness({links:false,costCapability});await h.open();
 assert.equal(h.control('cost_batch'),undefined);
 assert.ok(!h.controls.some(control=>control.df.fieldtype==='Link' && control.df.options==='Overseas Cost Batch'));
 assert.match(h.html(),/成本批次.*未安装或无权读取.*不可选择/);
 assert.ok(!h.loaded.includes('Overseas Cost Batch'));
 assert.ok(!h.calls.some(request=>request.args?.doctype==='Overseas Cost Batch'));
 await enterNewAssociation(h);await h.click('dlp-cross-save');
 const save=h.calls.find(request=>request.method.endsWith('.save_link'));assert.ok(save);assert.equal(save.args.cost_batch,null);
});
test('available optional cost capability retains the native Link and explicit batch identity',async()=>{
 const h=crossHarness({links:false});await h.open();const cost=h.control('cost_batch');
 assert.equal(cost.df.fieldtype,'Link');assert.equal(cost.df.options,'Overseas Cost Batch');assert.equal(cost.df.read_only,false);
 await enterNewAssociation(h);await h.change('cost_batch','BATCH-EXPLICIT');await h.click('dlp-cross-save');
 assert.equal(h.calls.find(request=>request.method.endsWith('.save_link')).args.cost_batch,'BATCH-EXPLICIT');
});
test('unreadable optional cost retains an existing authorized batch without an editable Link or binding clearance',async()=>{
 const h=crossHarness({costCapability:'denied'});await h.open();
 assert.equal(h.control('cost_batch'),undefined);assert.match(h.html(),/保留已有批次：BATCH/);
 await h.click('dlp-cross-save');assert.equal(h.calls.find(request=>request.method.endsWith('.save_link')).args.cost_batch,'BATCH');
});
for(const [flow,label] of [['external_internal','外部采购 / 内部结算'],['internal_local','本地内部采购'],['trade_custody','贸易品保管（无内部应付）']])test(`readonly native flow ${flow} displays its human label without changing the stored value`,async()=>{
 const fixture=crossFixture();fixture.detail.flow_kind=flow;const h=crossHarness({fixture});await h.open();
 const control=h.control('flow_kind');assert.equal(control.df.read_only,true);assert.equal(control.disp_area.textValue,label);
 assert.equal(control.get_value(),flow);assert.equal(control.value,flow);
 await control.set_value(flow);assert.equal(control.disp_area.textValue,label,'later native formatting keeps the local label');
});
test('readonly native item displays escaped source labels while get_value and Save preserve exact item IDs',async()=>{
 const fixture=crossFixture();fixture.progress.items[0].item_name='Native material <&';fixture.native.items[0].item_code='Internal <&';
 const h=crossHarness({fixture});await h.open();const external=h.control('external_item'),internal=h.control('internal_item');
 assert.match(external.disp_area.textValue,/EXT-ITEM.*Native material &lt;&amp;.*个/);
 assert.match(internal.disp_area.textValue,/INT-ITEM.*Internal &lt;&amp;.*个/);
 assert.doesNotMatch(external.disp_area.textValue,/<&/);assert.doesNotMatch(internal.disp_area.textValue,/<&/);
 assert.equal(external.get_value(),'EXT-ITEM');assert.equal(internal.get_value(),'INT-ITEM');
 await h.click('dlp-cross-save');const args=h.calls.find(request=>request.method.endsWith('.save_link')).args;
 assert.equal(args.external_item,'EXT-ITEM');assert.equal(args.internal_item,'INT-ITEM');assert.equal(args.allocated_qty,'1.234567');
});
test('optional logistics native inputs clear inherited required markers without changing numeric metadata',async()=>{
 const h=crossHarness();await h.open('logistics');
 assert.equal(h.control('manual_qty').df.reqd,0);assert.equal(h.control('evidence_file').df.reqd,0);assert.equal(h.control('manual_note').df.reqd,1);
 assert.equal(h.control('manual_qty').df.fieldtype,'Float');assert.equal(h.control('manual_qty').df.non_negative,1);assert.equal(h.control('manual_qty').df.precision,undefined);
 assert.equal(h.control('evidence_file').df.fieldtype,'Link');assert.equal(h.control('evidence_file').df.options,'File');
});
test('crossborder clicked entry reads one fresh item projection and deduplicates authorized link detail IDs',async()=>{
 const h=crossHarness();await h.open();
 assert.equal(h.calls.filter(r=>r.method.endsWith('.get_order_progress')).length,1);assert.equal(h.calls[0].args.include_items,1);
 assert.deepEqual(JSON.parse(JSON.stringify(h.calls[0].args.purchase_orders)),['PO']);assert.equal(h.calls.filter(r=>r.method.endsWith('.get_link_detail')).length,1);
 assert.match(h.surfaces[0].value,/dlp-document-drawer/);assert.match(h.html(),/内部关联/);assert.match(h.html(),/物流证据/);
 assert.equal(h.control('purchasing_company').df.read_only,true);assert.equal(h.control('purchasing_company').value,'Buyer');
 assert.match(h.html(),/报价草稿不形成应付/);assert.match(h.html(),/MXN/);assert.match(h.html(),/8\.99/);
 assert.equal(h.calls.filter(r=>/save_link|confirm_native_price|refresh_logistics/.test(r.method)).length,0);
});
test('new association keeps raw company proposals as suggestions and requires exact native candidate IDs',async()=>{
 const h=crossHarness({links:false});await h.open();
 assert.equal(h.control('beneficiary_company').value,'');assert.equal(h.calls.filter(r=>r.method.endsWith('.get_link_candidates')).length,0);
 assert.match(h.html(),/Raw factory/);assert.match(h.html(),/Suggested buyer/);
 await h.change('beneficiary_company','Factory');await h.change('flow_kind','external_internal');
 assert.equal(h.calls.filter(r=>r.method.endsWith('.get_link_candidates')).length,1);
 await h.change('external_item','EXT-ITEM');await h.change('internal_order','INTERNAL');await h.change('internal_item','INT-ITEM');await h.control('allocated_qty').$input.emit('change','0,004');
 await h.click('dlp-cross-save');const args=h.calls.find(r=>r.method.endsWith('.save_link')).args;
 assert.equal(args.external_item,'EXT-ITEM');assert.equal(args.internal_item,'INT-ITEM');assert.equal(args.expected_internal_modified,'internal-current');assert.equal(args.allocated_qty,0.004);assert.equal(args.purchasing_company,'Buyer');assert.equal(args.expected_modified,'po-current');assert.equal(args.request_id,undefined);
 assert.deepEqual(h.lifecycle,[['invalidate','PO'],['refresh']]);assert.equal(h.surfaces[0].removed,true);
});
test('association update preserves untouched exact allocation and all current source versions then requires price reconfirmation',async()=>{
 const fixture=crossFixture();fixture.detail.seller_order='SELLER';fixture.detail.seller_item='SELL-ITEM';fixture.detail.seller_modified='seller-current';fixture.detail.price_confirmation={version:'OLD',currency:'MXN',rate:'8.987654',by:'Buyer',on:'Yesterday'};
 const h=crossHarness({fixture});await h.open();
 for(const name of ['external_item','internal_order','internal_item','seller_order','seller_item'])assert.equal(h.control(name).df.read_only,true,name);
 assert.match(h.html(),/保存关联后.*重新确认原生价格/);await h.click('dlp-cross-save');const args=h.calls.find(r=>r.method.endsWith('.save_link')).args;
 assert.equal(args.name,'LINK');assert.equal(args.allocated_qty,'1.234567');assert.equal(args.expected_modified,'po-current');assert.equal(args.expected_internal_modified,'internal-current');assert.equal(args.expected_seller_modified,'seller-current');assert.equal(args.expected_link_modified,'link-current');
});
test('price confirmation is explicit and uses current external internal seller and link versions',async()=>{
 const fixture=crossFixture();fixture.detail.seller_modified='seller-current';const h=crossHarness({fixture});await h.open();await h.click('dlp-cross-confirm-price');
 const args=h.calls.find(r=>r.method.endsWith('.confirm_native_price')).args;assert.equal(args.expected_modified,'po-current');assert.equal(args.expected_internal_modified,'internal-current');assert.equal(args.expected_seller_modified,'seller-current');assert.equal(args.expected_link_modified,'link-current');
 assert.equal(h.calls.filter(r=>r.method.endsWith('.save_link')).length,0);
});
test('no candidate and no write permission remain usable friendly in-drawer warnings',async()=>{
 const empty=crossHarness({links:false,rpc:async request=>request.method.endsWith('.get_link_candidates')?{purchase_orders:[],warnings:['内部 Supplier 未配置']}:undefined});await empty.open();await empty.change('beneficiary_company','Factory');
 assert.match(empty.html(),/无匹配的原生内部订单/);assert.match(empty.html(),/内部 Supplier 未配置/);await empty.click('dlp-cross-save');assert.equal(empty.calls.filter(r=>r.method.endsWith('.save_link')).length,0);
 const denied=crossHarness({write:false});await denied.open();assert.match(denied.html(),/没有采购订单写入权限/);assert.ok(!denied.surfaces.some(s=>s.value?.includes?.('dlp-cross-save')));
});
test('logistics separates escaped provenance and manual reports and warns that none of these change ERP inventory',async()=>{
 const h=crossHarness();await h.open('logistics');const html=h.html();
 for(const text of ['SOURCE','CORP','APPROVAL','COMMENT&amp;lt;'])if(text==='COMMENT&amp;lt;')assert.match(html,/COMMENT&lt;&amp;/);else assert.match(html,new RegExp(text));
 assert.match(html,/Author&lt;&amp;/);assert.match(html,/预计到货 &lt;img onerror=x&gt;/);assert.match(html,/手工核对节点/);assert.match(html,/报告到货（非 ERP 入库）/);assert.match(html,/人工报告 &lt;script&gt;/);assert.match(html,/不更新库存/);
 assert.equal(h.calls.filter(r=>r.method.endsWith('.refresh_logistics')).length,0);
});
test('manual arrival omits blank quantity from form transport, while entered quantity requires explicit UOM and native precision',async()=>{
 const blank=crossHarness();await blank.open('logistics');await blank.change('manual_node','reported_arrival');await blank.change('manual_note','本人核对到货，仅为报告');await blank.click('dlp-cross-manual');
 const args=blank.calls.find(r=>r.method.endsWith('.set_manual_node')).args,body=formFields(args);assert.equal(body.has('qty'),false);assert.equal(body.has('uom'),false);assert.equal(body.get('node'),'reported_arrival');
 const h=crossHarness();await h.open('logistics');await h.change('manual_note','实物报告');await h.control('manual_qty').$input.emit('change','0,004');await h.click('dlp-cross-manual');assert.equal(h.calls.filter(r=>r.method.endsWith('.set_manual_node')).length,0);assert.match(h.html(),/数量需明确单位/);
 await h.change('manual_uom','个');await h.click('dlp-cross-manual');const entered=h.calls.find(r=>r.method.endsWith('.set_manual_node')).args;assert.equal(entered.qty,0.004);assert.equal(formFields(entered).get('qty'),'0.004');assert.equal(formFields(entered).get('uom'),'个');
});
test('evidence uses an existing native File ID and refresh logistics is only explicit',async()=>{
 const h=crossHarness();await h.open('logistics');await h.change('evidence_note','已核对原生凭证');await h.change('evidence_file','https://untrusted.invalid/file');await h.click('dlp-cross-evidence');assert.equal(h.calls.filter(r=>r.method.endsWith('.add_evidence')).length,0);
 await h.change('evidence_file','FILE-EXISTING');await h.click('dlp-cross-evidence');assert.equal(h.calls.find(r=>r.method.endsWith('.add_evidence')).args.file,'FILE-EXISTING');
 const refresh=crossHarness();await refresh.open('logistics');await refresh.click('dlp-cross-refresh-logistics');assert.equal(refresh.calls.filter(r=>r.method.endsWith('.refresh_logistics')).length,1);assert.deepEqual(refresh.lifecycle,[['invalidate','PO'],['refresh']]);
});
test('missing optional cost capability preserves manual nodes with an actionable in-drawer warning',async()=>{
 const fixture=crossFixture();fixture.detail.cost_batch=null;fixture.detail.warnings=['海外成本物流能力尚未安装或不可用，可保留独立手工节点'];fixture.detail.snapshot={state:'unavailable',timeline:[],warnings:fixture.detail.warnings};
 const h=crossHarness({fixture});await h.open('logistics');assert.match(h.html(),/海外成本物流能力尚未安装或不可用/);assert.match(h.html(),/人工报告/);assert.ok(h.control('manual_note'));
});
test('uncertain create is never automatically retried and remains blocked after duplicate Save clicks',async()=>{
 const h=crossHarness({links:false,rpc:async request=>{if(request.method.endsWith('.save_link'))throw new Error('network timeout');}});await h.open();await h.change('beneficiary_company','Factory');await h.change('external_item','EXT-ITEM');await h.change('internal_order','INTERNAL');await h.change('internal_item','INT-ITEM');await h.control('allocated_qty').$input.emit('change','1,234');
 await h.click('dlp-cross-save');await h.click('dlp-cross-save');assert.equal(h.calls.filter(r=>r.method.endsWith('.save_link')).length,1);assert.match(h.html(),/响应不确定.*刷新.*核对.*关联/);assert.equal(h.surfaces[0].find('.dlp-cross-save').props.disabled,true);assert.deepEqual(h.lifecycle,[]);
});
test('known validation error is not success and allows corrected input without a duplicate implicit retry',async()=>{
 const h=crossHarness({rpc:async request=>{if(request.method.endsWith('.save_link'))throw Object.assign(new Error('超过原生可分配数量'),{exc_type:'ValidationError'});}});await h.open();await h.click('dlp-cross-save');
 assert.match(h.html(),/超过原生可分配数量/);assert.equal(h.calls.filter(r=>r.method.endsWith('.save_link')).length,1);assert.deepEqual(h.lifecycle,[]);assert.equal(h.surfaces[0].find('.dlp-cross-save').props.disabled,false);
});
test('one busy crossborder mutation blocks duplicate writes and closing invalidates a late acknowledged result',async()=>{
 let resolve,started;const wait=new Promise(r=>started=r);const h=crossHarness({rpc:async request=>{if(request.method.endsWith('.save_link')){started();return new Promise(r=>resolve=r);}}});await h.open();const pending=h.click('dlp-cross-save');await wait;await h.click('dlp-cross-save');assert.equal(h.calls.filter(r=>r.method.endsWith('.save_link')).length,1);
 h.routeClose();const closed=h.html();resolve({name:'LINK',modified:'next'});await pending;assert.equal(h.html(),closed);assert.deepEqual(h.lifecycle,[]);
});
for(const cancellation of ['route','list'])test(`crossborder late detail is discarded after ${cancellation} invalidation`,async()=>{
 let resolve,started;const wait=new Promise(r=>started=r);const h=crossHarness({rpc:async request=>{if(request.method.endsWith('.get_link_detail')){started();return new Promise(r=>resolve=r);}}});const pending=h.open();await wait;
 if(cancellation==='route')h.routeClose();else h.api.cancelCrossborderForList(h.c);const closed=h.html();resolve(crossFixture().detail);await pending;assert.equal(h.controls.length,0);assert.equal(h.html(),closed);assert.equal(h.surfaces[0].removed,true);
});
test('list cancellation only closes its owned crossborder drawer and keeps unrelated payment drawers alive',async()=>{
 const h=crossHarness();await h.open();h.api.cancelCrossborderForList({});assert.notEqual(h.surfaces[0].removed,true);h.api.cancelCrossborderForList(h.c);assert.equal(h.surfaces[0].removed,true);
 await h.api.paymentDrawer('PE');const payment=h.surfaces.findLast(s=>s.value?.includes?.('dlp-payment-overlay'));h.api.cancelCrossborderForList(h.c);assert.notEqual(payment.removed,true);
});
test('clearing an existing allocation is blank rather than silently reusing its original quantity',async()=>{
 const h=crossHarness();await h.open();await h.control('allocated_qty').$input.emit('change','');await h.click('dlp-cross-save');
 assert.equal(h.calls.filter(r=>r.method.endsWith('.save_link')).length,0);assert.match(h.html(),/正数关联数量/);
});
test('unsaved cost batch changes must be saved and reloaded before confirming native price',async()=>{
 const h=crossHarness();await h.open();await h.change('cost_batch','CHANGED-BATCH');await h.click('dlp-cross-confirm-price');
 assert.equal(h.calls.filter(r=>r.method.endsWith('.confirm_native_price')).length,0);assert.match(h.html(),/请先保存关联/);
});
test('hidden optional cost binding cannot be silently cleared by saving another association field',async()=>{
 const fixture=crossFixture();fixture.detail.cost_batch=null;fixture.detail.warnings=['海外成本物流能力尚未安装或不可用，请先核对原生物流来源；可保留独立手工节点'];
 const h=crossHarness({fixture,costCapability:'absent'});await h.open();assert.equal(h.control('cost_batch'),undefined);await h.control('allocated_qty').$input.emit('change','0,004');await h.click('dlp-cross-save');
 assert.equal(h.calls.filter(r=>r.method.endsWith('.save_link')).length,0);assert.match(h.html(),/成本批次当前不可见.*不能改写关联/);
 await h.event('.dlp-cross-tab',{tab:'logistics'});await h.change('manual_note','独立人工核对证据');await h.click('dlp-cross-manual');assert.equal(h.calls.filter(r=>r.method.endsWith('.set_manual_node')).length,1);
});
test('trade custody requires explicit beneficiary and authorized warehouse and never sends internal payable identities',async()=>{
 const h=crossHarness({links:false});await h.open();await h.change('flow_kind','trade_custody');assert.equal(h.control('beneficiary_company').value,'');
 await h.change('beneficiary_company','Buyer');await h.change('custody_company','Factory');await h.change('custody_warehouse','WAREHOUSE');await h.change('external_item','EXT-ITEM');await h.control('allocated_qty').$input.emit('change','0,004');await h.click('dlp-cross-save');
 const args=h.calls.find(r=>r.method.endsWith('.save_link')).args;assert.equal(args.flow_kind,'trade_custody');assert.equal(args.custody_warehouse,'WAREHOUSE');assert.equal(args.custody_company,'Factory');assert.equal(args.internal_order,null);assert.equal(args.internal_item,null);
});
test('internal local selection uses only the exact current native PO and its explicitly chosen item',async()=>{
 const fixture=crossFixture();fixture.native={...fixture.native,name:'PO',company:'Buyer',modified:'po-current',items:[{...fixture.native.items[0],name:'EXT-ITEM'}]};
 const h=crossHarness({fixture,links:false});await h.open();await h.change('flow_kind','internal_local');await h.change('beneficiary_company','Buyer');await h.change('external_item','EXT-ITEM');await h.change('internal_order','PO');await h.change('internal_item','EXT-ITEM');await h.control('allocated_qty').$input.emit('change','0,004');await h.click('dlp-cross-save');
 const args=h.calls.find(r=>r.method.endsWith('.save_link')).args;assert.equal(args.internal_order,'PO');assert.equal(args.internal_item,'EXT-ITEM');assert.equal(args.expected_internal_modified,'po-current');
});
test('optional seller order is explicitly read and needs an exact selected item and its current native version',async()=>{
 const h=crossHarness({links:false});await h.open();await h.change('beneficiary_company','Factory');await h.change('external_item','EXT-ITEM');await h.change('internal_order','INTERNAL');await h.change('internal_item','INT-ITEM');await h.control('allocated_qty').$input.emit('change','0,004');
 assert.equal(h.calls.filter(r=>r.method==='frappe.client.get').length,0);await h.change('seller_order','SELLER');await h.click('dlp-cross-save');assert.equal(h.calls.filter(r=>r.method.endsWith('.save_link')).length,0);
 await h.change('seller_item','SELL-ITEM');await h.click('dlp-cross-save');const args=h.calls.find(r=>r.method.endsWith('.save_link')).args;assert.equal(args.seller_order,'SELLER');assert.equal(args.seller_item,'SELL-ITEM');assert.equal(args.expected_seller_modified,'seller-current');
});
test('disable preserves the association identity and requires a reason rather than deleting it',async()=>{
 const h=crossHarness();await h.open();await h.click('dlp-cross-disable-link');assert.equal(h.calls.filter(r=>r.method.endsWith('.disable_link')).length,0);
 await h.change('disable_reason','来源身份已改变，停用后重新建立');await h.click('dlp-cross-disable-link');const request=h.calls.find(r=>r.method.endsWith('.disable_link'));assert.equal(request.args.name,'LINK');assert.equal(request.args.expected_link_modified,'link-current');assert.match(request.args.reason,/来源身份已改变/);assert.equal(h.calls.filter(r=>/delete/.test(r.method)).length,0);
});
test('manual note length and node choices are validated before the managed mutation',async()=>{
 const h=crossHarness();await h.open('logistics');await h.change('manual_note','x'.repeat(4097));await h.click('dlp-cross-manual');assert.equal(h.calls.filter(r=>r.method.endsWith('.set_manual_node')).length,0);
 await h.change('manual_note','有效证据');await h.change('manual_node','native_received');await h.click('dlp-cross-manual');assert.equal(h.calls.filter(r=>r.method.endsWith('.set_manual_node')).length,0);
});
test('switching tabs reuses the same drawer and invalidates a late detail read',async()=>{
 let firstResolve,started,reads=0;const wait=new Promise(r=>started=r);const h=crossHarness({rpc:async request=>{if(request.method.endsWith('.get_link_detail') && ++reads===1){started();return new Promise(r=>firstResolve=r);}}});
 const pending=h.open();await wait;await h.event('.dlp-cross-tab',{tab:'logistics'});const current=h.html();firstResolve({...crossFixture().detail,manual_nodes:[{node:'review',note:'STALE EVIDENCE'}]});await pending;
 assert.equal(h.surfaces.filter(s=>s.value?.includes?.('dlp-payment-overlay')).length,1);assert.equal(h.html(),current);assert.doesNotMatch(h.html(),/STALE EVIDENCE/);
});
test('new association uncertain outcome stays locked through close and fresh reopen',async()=>{
 const h=crossHarness({links:false,rpc:async request=>{if(request.method.endsWith('.save_link'))throw new Error('network');}});await h.open();await h.change('beneficiary_company','Factory');await h.change('external_item','EXT-ITEM');await h.change('internal_order','INTERNAL');await h.change('internal_item','INT-ITEM');await h.control('allocated_qty').$input.emit('change','0,004');await h.click('dlp-cross-save');h.routeClose();await h.open();await h.click('dlp-cross-save');
 assert.equal(h.calls.filter(r=>r.method.endsWith('.save_link')).length,1);assert.match(h.html(),/新建已锁定/);
});
test('fresh detail with a newer external PO version cannot confirm price using a stale item projection',async()=>{
 const fixture=crossFixture();fixture.detail.external_modified='po-newer';const h=crossHarness({fixture});await h.open();await h.click('dlp-cross-confirm-price');await h.click('dlp-cross-save');assert.equal(h.calls.filter(r=>/confirm_native_price|save_link/.test(r.method)).length,0);assert.match(h.html(),/来源订单版本已变化/);
});
test('closing an in-flight create preserves the uncertainty guard even if its later network response fails',async()=>{
 let reject,started;const wait=new Promise(r=>started=r);const h=crossHarness({links:false,rpc:async request=>{if(request.method.endsWith('.save_link')){started();return new Promise((_,r)=>reject=r);}}});await h.open();await h.change('beneficiary_company','Factory');await h.change('external_item','EXT-ITEM');await h.change('internal_order','INTERNAL');await h.change('internal_item','INT-ITEM');await h.control('allocated_qty').$input.emit('change','0,004');const pending=h.click('dlp-cross-save');await wait;h.routeClose();reject(new Error('network'));await pending;await h.open();
 assert.match(h.html(),/新建已锁定/);assert.equal(h.surfaces.findLast(s=>s.value?.includes?.('dlp-payment-overlay')).find('.dlp-cross-save').props.disabled,true);
});
test('acknowledged write with a list invalidation failure closes the stale creation projection',async()=>{
 const h=crossHarness();await h.open();h.c.invalidatePurchaseDetails=()=>{throw new Error('native render failed');};await h.click('dlp-cross-save');assert.equal(h.surfaces[0].removed,true);
});
test('unavailable native write permission cannot offer association writes',async()=>{
 const h=crossHarness();delete h.host.frappe.model.can_write;await h.open();assert.match(h.html(),/没有采购订单写入权限/);assert.ok(!h.surfaces.some(s=>s.value?.includes?.('dlp-cross-save')));
});
test('crossborder and receipt controls share capture listener while stale PO invoice entries stop',async()=>{
 const cross=crossHarness();assert.equal(cross.documentEvents.filter(entry=>entry.type==='click').length,1);assert.equal(cross.documentEvents.find(entry=>entry.type==='click').capture,true);
 await cross.delegatedClick('dlp-crossborder-open',{crossborderOrder:'PO',crossborderTab:'logistics'});assert.equal(cross.calls.filter(r=>r.method.endsWith('.get_order_progress')).length,1);assert.match(cross.html(),/手工核对节点/);
 const order=harness();await order.delegatedClick('dlp-receipt-invoice',{name:'PO',sourceDoctype:'Purchase Order',target:'PI-DRAFT'});assert.equal(order.requests.length,0);
 const row=harness();await row.delegatedClick('dlp-order-invoice',{name:'PO'});assert.equal(row.requests.length,0);
 const receipt=harness();await receipt.delegatedClick('dlp-receipt-invoice',{name:'PR'});assert.equal(receipt.requests[0].args.doctype,'Purchase Receipt');assert.equal(receipt.requests.find(r=>r.method.endsWith('.preview_document')).args.target_doctype,'Purchase Invoice');
});
test('actual crossborder close followed by a payment drawer leaves that payment live on list cancellation',async()=>{
 const document=projection('Payment Entry');document.document.name='PE';document.allowed_actions=['Submit'];
 const h=crossHarness({document});await h.open();
 await h.surfaces[0].find('.dlp-close,.dlp-cancel').emit('click');
 assert.equal(h.surfaces[0].removed,true);
 await h.api.paymentDrawer('PE');const payment=h.surfaces.findLast(s=>s.value?.includes?.('dlp-payment-overlay'));
 h.api.cancelCrossborderForList(h.c);assert.notEqual(payment.removed,true);
 await h.controls.findLast(c=>c.df.fieldname==='amount').$input.emit('change','12,34');await h.click('dlp-submit');
 const request=h.requests.find(r=>r.method.endsWith('.complete_payment'));assert.ok(request,'unrelated payment gate remains live');assert.equal(request.args.changes.amount,12.34);
 await payment.find('.dlp-close,.dlp-cancel').emit('click');const next=h.api.createDrawer('下一次核对');assert.ok(next);next.close(true);
});
for(const field of ['allocated_qty','cost_batch','custody_company','custody_warehouse'])test(`unsaved association ${field} visibly invalidates native price confirmation`,async()=>{
 const fixture=crossFixture();fixture.detail.price_confirmation={version:'CONFIRMED',currency:'MXN',by:'Buyer',on:'Yesterday'};
 const h=crossHarness({fixture});await h.open();const panel=h.surfaces[0];assert.match(panel.find('.dlp-cross-price').textValue,/已记录原生价格确认/);
 if(field==='allocated_qty')await h.control(field).$input.emit('input','0,004');else await h.change(field,'CHANGED');
 assert.match(panel.find('.dlp-cross-price').textValue,/关联修改未保存.*确认待重新核对/);
 assert.doesNotMatch(panel.find('.dlp-cross-price').textValue,/已记录原生价格确认/);
 assert.equal(panel.find('.dlp-cross-confirm-price').props.disabled,true);await h.click('dlp-cross-confirm-price');
 assert.equal(h.calls.filter(r=>r.method.endsWith('.confirm_native_price')).length,0);
 await h.click('dlp-cross-reload');assert.match(panel.find('.dlp-cross-price').textValue,/已记录原生价格确认/);assert.equal(panel.find('.dlp-cross-confirm-price').props.disabled,false);
});
for(const tab of ['internal','logistics'])test(`crossborder ${tab} notes use strict existing native metadata without Purchase Order remarks`,async()=>{
 const h=crossHarness({fieldDictionary:crossFieldDictionary()});await h.open(tab);
 assert.doesNotMatch(h.html(),/原生字段元数据不可用/);
 for(const name of tab==='internal'?['disable_reason']:['manual_note','evidence_note'])assert.equal(h.control(name)?.df.fieldtype,'Small Text',name);
 assert.ok(!h.metadataReads.some(([doctype,field])=>doctype==='Purchase Order' && field==='remarks'));
 assert.deepEqual([...new Set(h.loaded)].sort(),['Purchase Fulfilment Link','Purchase Order','Purchase Order Item'].sort());
});
test('uncertain creation with no created record needs fresh read and explicit human outcome before unlocking',async()=>{
 const values=new Map(),storage={getItem:key=>values.get(key) || null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
 const rpc=async request=>{if(request.method.endsWith('.save_link'))throw new Error('network');};
 let h=crossHarness({links:false,storage,rpc});await uncertainAssociation(h);const originalCalls=h.calls;h.routeClose();
 h=crossHarness({links:false,storage,rpc});await h.open();
 await h.click('dlp-cross-reconcile');assert.equal(h.calls.filter(r=>r.method.endsWith('.get_order_progress')).length,2);
 assert.match(h.html(),/未发现关联也不会自动解除锁定/);assert.equal(h.surfaces[0].find('.dlp-cross-save').props.disabled,true);
 await h.click('dlp-cross-reconcile-confirm');assert.equal(values.size,1,'an empty authorized result is not a human decision');
 await h.change('reconcile_outcome','not_created');await h.click('dlp-cross-reconcile-confirm');assert.equal(values.size,1,'acknowledgement remains mandatory');
 await h.change('reconcile_ack',1);await h.click('dlp-cross-reconcile-confirm');assert.equal(values.size,0);assert.equal(h.calls.filter(r=>r.method.endsWith('.save_link')).length,0,'reconciliation never retries a creation after a new frontend session');
 assert.equal(originalCalls.filter(r=>r.method.endsWith('.save_link')).length,1);
 assert.equal(h.surfaces[0].find('.dlp-cross-save').props.disabled,false);await enterNewAssociation(h);await h.click('dlp-cross-save');assert.equal(h.calls.filter(r=>r.method.endsWith('.save_link')).length,1,'only a new explicit Save dispatches again');
});
test('uncertain creation found existing requires explicit exact link selection and never creates again on acknowledgement',async()=>{
 let attempted=false;const values=new Map(),storage={getItem:key=>values.get(key) || null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
 const h=crossHarness({links:false,storage,rpc:async(request,fixture)=>{
  if(request.method.endsWith('.save_link')){attempted=true;throw new Error('network');}
  if(request.method.endsWith('.get_order_progress') && attempted)return {PO:fixture.progress};
 }});await uncertainAssociation(h);await h.click('dlp-cross-reconcile');assert.equal(h.calls.filter(r=>r.method.endsWith('.get_link_detail')).length,1);
 await h.change('reconcile_outcome','created');await h.change('reconcile_ack',1);await h.click('dlp-cross-reconcile-confirm');assert.equal(values.size,1);
 await h.change('reconcile_link','GUESSED');await h.click('dlp-cross-reconcile-confirm');assert.equal(values.size,1);
 await h.change('reconcile_link','LINK');await h.click('dlp-cross-reconcile-confirm');assert.equal(values.size,0);assert.equal(h.control('external_item').df.read_only,true);
 assert.equal(h.calls.filter(r=>r.method.endsWith('.save_link')).length,1);assert.match(h.html(),/LINK/);
});
for(const refusal of ['restricted','setup_required','detail_permission','version_changed'])test(`uncertainty reconciliation cannot unlock incomplete authorized records: ${refusal}`,async()=>{
 let attempted=false;const values=new Map(),storage={getItem:key=>values.get(key) || null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
 const h=crossHarness({links:false,storage,rpc:async(request,fixture)=>{
  if(request.method.endsWith('.save_link')){attempted=true;throw new Error('network');}
  if(request.method.endsWith('.get_order_progress') && attempted){
   if(['restricted','setup_required'].includes(refusal))return {PO:{...fixture.progress,internal:[{state:refusal,warnings:[]}],receipt_logistics:[]}};
   return {PO:{...fixture.progress,...(refusal==='version_changed'?{modified:'po-new'}:{})}};
  }
  if(request.method.endsWith('.get_link_detail') && refusal==='detail_permission')throw Object.assign(new Error('受限'),{exc_type:'PermissionError'});
 }});await uncertainAssociation(h);await h.click('dlp-cross-reconcile');assert.equal(values.size,1);assert.match(h.html(),/核对读取不完整.*仍保持新建锁定/);
 assert.ok(!h.control('reconcile_ack'));assert.equal(h.calls.filter(r=>r.method.endsWith('.save_link')).length,1);
});
for(const cancellation of ['route','filter'])test(`pending uncertainty reconciliation ignores a late read after ${cancellation}`,async()=>{
 let reads=0,resolve,started;const wait=new Promise(r=>started=r),values=new Map(),storage={getItem:key=>values.get(key) || null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
 const h=crossHarness({links:false,storage,rpc:async request=>{
  if(request.method.endsWith('.save_link'))throw new Error('network');
  if(request.method.endsWith('.get_order_progress') && ++reads===2){started();return new Promise(r=>resolve=r);}
 }});await uncertainAssociation(h);const pending=h.click('dlp-cross-reconcile');await wait;
 if(cancellation==='route')h.routeClose();else {h.c.requestId++;h.api.cancelCrossborderForList(h.c);}
 resolve({PO:{...crossFixture().progress,internal:[],receipt_logistics:[]}});await pending;assert.equal(values.size,1);assert.ok(!h.control('reconcile_ack'));
});
for(const cancellation of ['route','filter'])test(`explicit uncertainty acknowledgement cannot apply after ${cancellation} invalidates its fresh snapshot`,async()=>{
 const values=new Map(),storage={getItem:key=>values.get(key) || null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
 const h=crossHarness({links:false,storage,rpc:async request=>{if(request.method.endsWith('.save_link'))throw new Error('network');}});await uncertainAssociation(h);await h.click('dlp-cross-reconcile');
 await h.change('reconcile_outcome','not_created');await h.change('reconcile_ack',1);
 if(cancellation==='route')h.routeClose();else {h.c.requestId++;h.api.cancelCrossborderForList(h.c);}
 await h.click('dlp-cross-reconcile-confirm');assert.equal(values.size,1);assert.equal(h.calls.filter(r=>r.method.endsWith('.save_link')).length,1);
});
test('a creation refusal received after close cannot silently reset the persisted uncertainty lock',async()=>{
 let reject,started;const wait=new Promise(r=>started=r),values=new Map(),storage={getItem:key=>values.get(key) || null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
 const h=crossHarness({links:false,storage,rpc:async request=>{if(request.method.endsWith('.save_link')){started();return new Promise((_,r)=>reject=r);}}});
 await h.open();await enterNewAssociation(h);const pending=h.click('dlp-cross-save');await wait;h.routeClose();
 reject(Object.assign(new Error('来源已变化'),{exc_type:'ValidationError'}));await pending;assert.equal(values.size,1,'a departed generation cannot clear recovery state');
});
