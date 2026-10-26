(function(root) {
 'use strict';
 if(root.__dlpProcurementActionsInstalled && root.DeepLinkERPPurchasePayments)return;
 const frappe=root.frappe, $=root.$;
 const t = (value, args = []) => String((root.__ || (text => text))(value)).replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");
 const API='deeplinkerp_branding.services.purchase_payment_service.';
 const ACTIONS='deeplinkerp_branding.services.purchase_document_actions.';
 const FILES='deeplinkerp_branding.services.purchase_payment_attachments.';
 const esc=value=>String(value ?? '').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
 const slug=type=>({'Purchase Order':'purchase-order','Purchase Receipt':'purchase-receipt','Purchase Invoice':'purchase-invoice','Payment Entry':'payment-entry','China Accounting Voucher':'china-accounting-voucher','China Voucher Sync Issue':'china-voucher-sync-issue','Supplier':'supplier'}[type]);
 const link=(type,name,label=name)=>name && slug(type)?`<a href="/desk/${slug(type)}/${encodeURIComponent(name)}"${['Purchase Order','Purchase Receipt'].includes(type)?` class="dlp-native-document" data-doctype="${esc(type)}" data-name="${esc(name)}"`:""}>${esc(t(label))}</a>`:'—';
 const money=(value,currency)=>value==null?'—':`${Number(value).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}${currency?` ${esc(currency)}`:''}`;
 const quantity=value=>value==null?'—':Number(value).toLocaleString(undefined,{maximumFractionDigits:2});
 function formatNumericInput(control){const nativeFormat=control.format_for_input,display=Object.create(control);display.get_precision=()=>2;control.format_for_input=value=>nativeFormat.call(display,value);}
 const call=async(method,args,namespace=API)=>(await frappe.call({method:namespace+method,args,silent:true})).message;
 const state=value=>({Posted:'已记账',Reversed:'已冲销',Cancelled:'已取消',Draft:'草稿',Pending:'待同步'}[value] || value || '—');
 const balanceHTML=chain=>`${chain.shared_payable || (chain.invoices || []).some(invoice=>invoice.shared)?`<p class="text-warning">${t("共享应付整单余额，不是本张入库的已付金额")}</p>`:''}${(chain.balances || []).map(b=>`<span>${esc(t('应付'))} ${money(b.total,b.currency)}　${esc(t('已付 / 核销'))} ${money(b.settled,b.currency)}　<b>${esc(t('未付'))} ${money(b.outstanding,b.currency)}</b></span>`).join('<br>') || t('尚未形成应付 / 余额不可见')}`;
 const vouchersHTML=rows=>(rows || []).map(v=>`<button type="button" class="btn btn-link btn-xs dlp-voucher-preview" data-name="${esc(v.name)}">${esc(v.statutory_number || v.name)} · ${esc(t(state(v.status)))} · ${esc(t(v.source_event==='Posting'?'记账':v.source_event==='Cancellation'?'冲销':v.source_event))}</button>`).join(' · ') || '—';
 const referencesHTML=refs=>(refs || []).map(ref=>t("{0} · 分配 {1} {2} {3}", [link(ref.doctype,ref.name), money(ref.allocated,ref.currency), (ref.orders || []).map(name=>link('Purchase Order',name)).join(' · '), (ref.receipts || []).map(name=>link('Purchase Receipt',name)).join(' · ')])).join('；') || '—';
 const paymentSummary=payload=>t("{0}：{1}（不是采购核销合计）", [esc(t(payload.totals_label || '整张付款单银行币种金额')), (payload.totals || []).map(row=>`${t(row.payment_type==='Receive'?'退款':'付款')} / ${t({0:'草稿',1:'已提交',2:'已取消'}[row.docstatus])} ${money(row.amount,row.currency)}`).join(' · ') || '—']);
 const reversalLabels={waiting_inventory:'待库存重算',recalculating:'重算中',verifying:'校验中',completed:'冲销完成',failed:'失败待处理'};
 const reversalCache=new Map();
 const reversalBlocked=value=>Boolean(value && (value.stage!=='completed' || value.refresh_failed));
 const reversalHTML=value=>value?`<span class="dlp-reversal-stage ${value.stage==='completed' && !value.refresh_failed?'text-success':'text-warning'}" role="status">${esc(t(reversalLabels[value.stage] || '冲销进度待核对'))}${value.refresh_failed?t(" · 进度刷新失败"):''}</span>`:'';
 async function refreshReversal(doc){
  const key=`${doc.doctype}:${doc.name}`;
  try{
   const value=await call('get_progress',{doctype:doc.doctype,name:doc.name},'deeplinkerp_branding.services.purchase_reversal_progress.');
   if(value && !Object.hasOwn(reversalLabels,value.stage))throw new Error(t("unknown reversal stage"));
   reversalCache.set(key,value);return value;
  }catch(error){
   const prior=reversalCache.get(key);
   return {...(prior || {stage:'waiting_inventory',safe_reason:'progress_unavailable',can_retry:false}),refresh_failed:true};
  }
 }
 async function reversalDrawer(drawer,sources,reload){
  const generation=drawer.reversalLoadId=(drawer.reversalLoadId || 0)+1;
  const values=await Promise.all(sources.map(refreshReversal));
  if(!drawer.alive() || generation!==drawer.reversalLoadId)return true;
  if(!values.some(reversalBlocked))return false;
  drawer.panel.find('footer').find('.dlp-save,.dlp-submit,.dlp-create,.dlp-save-payment,.dlp-explicit-new,.dlp-batch-save,.dlp-batch-confirm,.dlp-next-ap,.dlp-next-pay,.dlp-batch-retry,.dlp-retry-payment,.dlp-retry-existing').remove();
  body(drawer,`${sources.map((source,index)=>`<p>${link(source.doctype,source.name)} · ${reversalHTML(values[index])}</p>`).join('')}<p>${t("采购冲销未完成，相关后续操作暂缓办理。")}</p>`);drawer.setBusy(false);
  if(root.setTimeout)root.setTimeout(()=>{if(drawer.alive() && generation===drawer.reversalLoadId && !drawer.busy)reload().catch(error=>drawer.error(error));},5000);
  return true;
 }
 async function reversalForm(frm){
  const identity={doctype:frm.doctype,name:frm.doc.name},generation=frm.dlpReversalGeneration=(frm.dlpReversalGeneration || 0)+1;
  root.clearTimeout?.(frm.dlpReversalTimer);frm.dlpReversalTimer=null;
  const previous=frm.dlpReversalIdentity;
  if(frm.is_new() || previous?.doctype!==identity.doctype || previous?.name!==identity.name){
   frm.dlpReversal=null;frm.$wrapper.find('.dlp-reversal-status').remove();
  }
  frm.dlpReversalIdentity=identity;
  if(frm.is_new())return;
  const value=await refreshReversal(identity);
  if(frm.doctype!==identity.doctype || frm.doc.name!==identity.name || frm.dlpReversalGeneration!==generation)return;
  frm.dlpReversal=value;
  frm.$wrapper.find('.dlp-reversal-status').remove();
  if(value){
   const box=$(`<section class="dlp-reversal-status">${reversalHTML(value)}${reversalBlocked(value)?`<p>${t("冲销未完成，相关库存与采购后续操作暂缓办理。")}</p>`:''}${value.can_retry && !value.refresh_failed?`<button type="button" class="btn btn-default btn-sm dlp-reversal-retry">${t("继续处理")}</button>`:''}</section>`).prependTo(frm.layout.wrapper);
   box.find('.dlp-reversal-retry').on('click.dlpReversal',async()=>{await call('retry',identity,'deeplinkerp_branding.services.purchase_reversal_progress.');await frm.reload_doc();});
  }
  if(reversalBlocked(value) && root.setTimeout)frm.dlpReversalTimer=root.setTimeout(()=>{if(root.cur_frm===frm && frm.doctype===identity.doctype && frm.doc.name===identity.name && frm.dlpReversalGeneration===generation)formRefresh(frm);},5000);
  return value;
 }

 // Raw projections stay immutable; only actual input events enter this edit set.
 function editSession(projection) {
  let header={},items=new Map();
  const session={document:projection.document,
   touch(field,value){header[field]=['amount','qty','rate'].includes(field)?Number(value):value;},
   touchItem(key,field,value){const row=items.get(key) || {key};row[field]=['qty','rate'].includes(field)?Number(value):value;items.set(key,row);},
   changes(){return {...header,...(items.size?{items:[...items.values()]}:{})};},
   reset(next){this.document=next.document;header={};items=new Map();},
   dirty(){return Object.keys(header).length>0 || items.size>0;},
  }; return session;
 }
 function requestId() {
  if(typeof root.crypto?.randomUUID==='function')return root.crypto.randomUUID();
  if(typeof root.crypto?.getRandomValues!=='function')throw new Error(t('当前浏览器不支持安全随机标识，请升级浏览器后重试。'));
  const bytes=new Uint8Array(16);root.crypto.getRandomValues(bytes);
  bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
  const hex=Array.from(bytes,byte=>byte.toString(16).padStart(2,'0')).join('');
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
 }
 function retryToken(uuid,storageKey=null) {
  let token=null,signature=null;
  const persist=()=>{try{if(storageKey){if(token)root.sessionStorage?.setItem(storageKey,JSON.stringify({token,signature}));else root.sessionStorage?.removeItem(storageKey);}}catch(error){/* optional browser recovery */}};
  try{const previous=storageKey && JSON.parse(root.sessionStorage?.getItem(storageKey) || 'null');if(previous?.token && typeof previous.signature==='string'){token=previous.token;signature=previous.signature;}}catch(error){/* optional browser recovery */}
  return {forPayload(payload){const next=JSON.stringify(payload);if(signature!==null && next!==signature)throw new Error(t("上次响应不确定，内容已改变。请先核对记录并刷新，不要重复建单。"));if(!token){token=uuid();signature=next;persist();}return token;},pending(){return signature?JSON.parse(signature):null;},succeeded(){token=null;signature=null;persist();}};
 }
 function paymentToken(sourceType,sourceName){return retryToken(requestId,`dlp-payment-retry:${frappe.boot?.sitename || root.location?.host}:${frappe.session?.user}:${sourceType}:${sourceName}`);}
 async function sendPayment(drawer,args,token,context){
  if(drawer.busy || drawer.paymentCompleted)return;drawer.setBusy(true);
  try{
   const result=await call('record_payment',{...args,request_id:token.forPayload(args)},ACTIONS);token.succeeded();if(!drawer.alive())return;
   if(result.failed){drawer.error(new Error(result.error || t('付款未提交，请核对后重试。')));return;}
   if(!result.needs_review)drawer.attachments?.committed();
   drawer.paymentCompleted=result.document.docstatus===1;
   if(drawer.paymentCompleted){await paymentResult(drawer,result,context);await refreshSurface();}
   else{const staged=result.needs_review?drawer.attachments?.snapshot():null;drawer.close(true);await paymentDrawer(result.document.name,{...context,staged,notice:result.needs_review?'已有待确认付款，请核对后继续。':args.confirm?'付款待审批，尚未计已付。':'付款草稿已保存，尚未计已付。'});}
  }catch(error){if(drawer.alive())drawer.error(new Error(drawer.paymentCompleted?t("付款已提交，页面刷新失败。请查看付款记录，勿重复提交。"):error.message || t("操作未确认：相同内容可重试；响应不确定时先核对付款记录。")));}
  finally{if(drawer.alive())drawer.setBusy(false);}
 }
 function operationGate(){let generation=0;return {begin:()=>++generation,cancel:()=>++generation,current:id=>id===generation};}
 const gate=operationGate();let activeDrawer=null,recordsController=null;
 function currentController(){const route=frappe.get_route?.() || [];return route[0]==='purchase-payables'?root.DeepLinkERPPurchasePayables?.controller:route[0]==='purchase-payment-records'?recordsController:root.cur_list?.dlpReceiptGrid || root.cur_list?.dlpPurchaseOrderGrid;}
 async function refreshSurface(){const route=frappe.get_route?.() || [],controller=currentController();if(controller)await controller.refresh();else if(route[0]==='Form' && ['Purchase Order','Purchase Receipt'].includes(root.cur_frm?.doctype))await root.cur_frm.reload_doc();else await root.cur_list?.refresh?.();}
 function recordsRoute(chain){frappe.route_options={[chain.source_doctype==='Purchase Receipt'?'purchase_receipt':'purchase_order']:chain.name};frappe.set_route('purchase-payment-records');}
 const returnKey='dlp-procurement-return';
 function nativeAction(type,name,label){return `<button type="button" class="btn btn-xs btn-default dlp-native-document" data-doctype="${esc(type)}" data-name="${esc(name)}" title="${esc(t(label))}" aria-label="${esc(t(label))}">${esc(t(label))}</button>`;}
 async function openNative(type,name){
  if(activeDrawer?.guardNavigation && !(await activeDrawer.close()))return;
  const route=frappe.get_route?.() || [],grid=currentController();
  let previous;try{previous=JSON.parse(root.sessionStorage?.getItem(returnKey) || 'null');}catch(error){/* optional browser storage */}
  const context={...(route[0]==='Form' && previous?previous:{route,quick:grid?.quick,page:grid?.page,scroll:grid?.list?.$result?.get?.(0)?.scrollTop || 0}),type,name};
  try{root.sessionStorage?.setItem(returnKey,JSON.stringify(context));}catch(error){/* The native form remains usable when storage is unavailable. */}
  activeDrawer?.close(true);frappe.set_route('Form',type,name);
 }
 function returnContext(frm){
  let context;try{context=JSON.parse(root.sessionStorage?.getItem(returnKey) || 'null');}catch(error){return;}
  if(!context || frm.doctype!==context.type || frm.doc.name!==context.name)return;
  frm.$wrapper.find('.dlp-procurement-return').remove();
  const box=$(`<section class="dlp-procurement-return"><p>${esc(t(frm.doc.docstatus===0?'请核对并处理当前草稿，完成后返回原列表。':'当前单据已处理，可返回原列表继续采购流程。'))}</p><button type="button" class="btn btn-primary">${esc(t('返回原采购列表'))}</button></section>`).prependTo(frm.layout.wrapper);
  box.find('button').on('click.dlpReturn',async()=>{await frappe.set_route(...context.route);const grid=currentController();if(grid && context.quick){grid.resetting=true;await Promise.all(Object.entries(grid.controls).map(([field,control])=>control.set_value(context.quick[field] || '')));grid.quick=context.quick;grid.resetting=false;grid.setPage(context.page || 0);await grid.refresh();const element=grid.list?.$result?.get?.(0);if(element)element.scrollTop=context.scroll;}root.sessionStorage?.removeItem(returnKey);});
 }
 function orderReceiptAction(doc){
  if(doc.row_type==='oa_request')return '';
  if(reversalBlocked(doc.reversal))return reversalHTML(doc.reversal);
  if(doc.docstatus===0)return nativeAction('Purchase Order',doc.name,t("确认订单"));
  if(doc.docstatus!==1)return '';
  if(!doc.receipt_eligibility?.allowed)return '';
  return `<span class="dlp-order-actions"><button type="button" class="btn btn-xs btn-default dlp-order-receipt" data-name="${esc(doc.name)}">${esc(t('入库'))}</button></span>`;
 }
 async function receiptDrafts(sourceName){
  const rows=(await frappe.call({method:'frappe.client.get_list',args:{doctype:'Purchase Receipt',fields:['name','modified'],filters:[['Purchase Receipt','docstatus','=',0],['Purchase Receipt Item','purchase_order','=',sourceName]],order_by:'modified desc',limit_page_length:0}})).message || [];
  // The permission-checked native child join may repeat a parent for each item.
  const seen=new Set();return rows.filter(row=>{if(seen.has(row.name))return false;seen.add(row.name);return true;});
 }
 function guardDrawerNavigation(drawer){
  const router=frappe.router;if(!router || typeof router.set_route!=='function' || typeof router.route!=='function')return false;
  const nativeSetRoute=router.set_route,nativeRoute=router.route,originURL=root.location.href,originIndex=root.navigation?.currentEntry?.index,originOptions=frappe.route_options;
  let restoring=false,deferredTraversal=false;
  const restoreOptions=()=>{frappe.route_options=originOptions;};
  async function navigate(next,rollback=restoreOptions){
   if(!drawer.alive())return next();
   if((drawer.busy && drawer.busyOperation!=='read') || drawer.navigationPending){rollback();return false;}
   drawer.navigationPending=true;
   try{if(!(await drawer.close())){rollback();return false;}deferredTraversal=false;return next();}
   finally{drawer.navigationPending=false;}
  }
  // Frappe's set_route pushes URL state before route parses/renders. Guard the
  // entry before that push; guard route itself for native browser Back/Forward.
  const setRoute=function(...args){
   if(frappe.open_in_new_tab)return nativeSetRoute.apply(this,args);
   const options=frappe.route_options,hash=frappe.route_hash;
   return navigate(()=>{frappe.route_options=options;frappe.route_hash=hash;return nativeSetRoute.apply(this,args);});
  };
  const route=function(...args){
   if(restoring && root.location.href===originURL){restoring=false;return false;}
   if(root.location.href===originURL)return nativeRoute.apply(this,args);
   const targetIndex=root.navigation?.currentEntry?.index;
   const rollback=()=>{
    restoreOptions();
    // Native Frappe history states are null. Navigation entry indices preserve
    // the real Back/Forward cursor without overwriting the traversed entry.
    if(Number.isInteger(originIndex) && originIndex>=0 && Number.isInteger(targetIndex) && targetIndex>=0 && originIndex!==targetIndex){restoring=true;root.history.go(originIndex-targetIndex);}
    else deferredTraversal=true;
   };
   return navigate(()=>nativeRoute.apply(this,args),rollback);
  };
  router.set_route=setRoute;router.route=route;
  drawer.releaseNavigation=()=>{
   if(router.set_route===setRoute)router.set_route=nativeSetRoute;if(router.route===route)router.route=nativeRoute;
   // Without entry indices retain both the history entry and guarded UI. An
   // explicitly approved manual close then renders the pending current URL.
   if(deferredTraversal && !drawer.navigationPending)nativeRoute.call(router);
  };
  return true;
 }
 function newDrawer(title,wide=false,options={}){
  if(activeDrawer)return null;
  const id=gate.begin(),opener=root.document.activeElement,media=options.desktopNonModal?root.matchMedia?.('(max-width: 767.98px)'):null,modal=()=>!options.desktopNonModal || !media || media.matches;
  const panel=$(`<div class="dlp-payment-overlay${options.desktopNonModal?' dlp-drawer-desktop-nonmodal':''}"><section class="dlp-payment-drawer${wide?' dlp-document-drawer':''}" role="dialog" aria-modal="${modal()}" aria-label="${esc(t(title))}"><header><h3>${esc(t(title))}</h3><button type="button" class="btn btn-default dlp-close" aria-label="${esc(t('关闭'))}">×</button></header><div class="dlp-payment-body"><p role="status">${esc(t('正在读取…'))}</p></div><footer><button type="button" class="btn btn-default dlp-cancel">${esc(t('取消'))}</button></footer></section></div>`).appendTo(root.document.body);
  const drawer={id,panel,controls:[],busy:false,loadId:0,guardNavigation:Boolean(options.guardNavigation),alive:()=>activeDrawer===drawer && gate.current(id),
   close(force=false,cleaned=false){
    if(activeDrawer!==this || (this.busy && !force && !(this.guardNavigation && this.busyOperation==='read')))return false;
    if(!force && !cleaned && this.beforeClose){this.closePending ||= Promise.resolve(this.beforeClose()).then(ok=>ok!==false?this.close(false,true):false).finally(()=>{this.closePending=null;});return this.closePending;}
    gate.cancel();this.releaseNavigation?.();media?.removeEventListener?.('change',resize);this.attachments?.stop();disposeControls(this.controls);panel.remove();root.document.removeEventListener('keydown',keys);root.removeEventListener?.('beforeunload',beforeUnload);activeDrawer=null;if(!force)opener?.focus?.();return true;
   },
   error(error){panel.find('.dlp-error').text(t(error?.message || '操作失败，请核对系统提示。'));},
   setBusy(value,operation='write'){this.busy=value;this.busyOperation=value?operation:null;panel.find('.dlp-payment-drawer').toggleClass('dlp-busy',value);panel.find('.dlp-payment-body').attr('aria-busy',String(value));panel.find('button').prop('disabled',value);if(this.guardNavigation && operation==='read')panel.find('.dlp-close,.dlp-cancel').prop('disabled',false);this.attachments?.refresh();},
  };
  const resize=()=>panel.find('.dlp-payment-drawer').attr('aria-modal',String(modal()));
  const beforeUnload=event=>{
   if(!drawer.alive() || !((drawer.busy && drawer.busyOperation!=='read') || drawer.beforeUnloadShouldBlock?.()))return;
   event.preventDefault();event.returnValue='';
  };
  const keys=e=>{if(e.target.closest?.('.modal'))return;if(e.key==='Escape'){e.preventDefault();drawer.close();}if(e.key==='Tab' && modal()){const elements=panel.find('button,input,select,textarea,summary,a[href]').filter(':visible:not(:disabled)').toArray();if(!elements.length)return;const first=elements[0],last=elements[elements.length-1];if(e.shiftKey && root.document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey && root.document.activeElement===last){e.preventDefault();first.focus();}}};
  activeDrawer=drawer;if(options.guardNavigation){drawer.guardNavigation=guardDrawerNavigation(drawer);root.addEventListener?.('beforeunload',beforeUnload);}media?.addEventListener?.('change',resize);root.document.addEventListener('keydown',keys);panel.find('.dlp-close,.dlp-cancel').on('click.dlpDrawer',()=>drawer.close());panel.find('.dlp-close').trigger('focus');return drawer;
 }
 const destroyedDatepickers=new WeakSet();
 function disposeControls(controls){for(const control of controls.splice(0)){control.dlpDisposeInput?.();delete control.dlpDisposeInput;control.$input?.off('.dlpDrawer .dlpInvoice');const picker=control.datepicker;if(picker?.destroy && !destroyedDatepickers.has(picker)){destroyedDatepickers.add(picker);picker.destroy();}}}
 function body(drawer,html){disposeControls(drawer.controls);drawer.panel.find('.dlp-payment-body').html(`${html}<div class="dlp-error text-danger" role="alert"></div>`);}
 async function mountAttachments(drawer,doctype,name,restored=null) {
  const holder=drawer.panel.find('.dlp-attachments'),docs=new Map((restored?.files || []).map(doc=>[doc.name,doc]));
  let uploader=null,timer=null,started=0,adding=0,removing=false,committed=false,stopped=false;
  const context=restored?.context || [doctype,name],sessionId=restored?.sessionId || requestId();
  holder.html(`<div class="dlp-attachment-title"><strong>${esc(t('付款凭证'))}</strong><span>${esc(t('可多选'))}</span></div><div class="dlp-attachment-drop"><span>${esc(t('拖放文件'))}</span><button type="button" class="btn btn-default dlp-choose-files">${esc(t('选择文件'))}</button><input type="file" multiple hidden></div><div class="dlp-attachment-list"></div><div class="dlp-native-uploader" hidden></div><p class="dlp-attachment-error text-danger" role="alert"></p>`);
  const queue=()=>uploader?.uploader.files || [];
  const pending=()=>queue().filter(file=>!docs.has(file.doc?.name));
  const canRetry=file=>file.failed && file.error_message && !/XMLHttpRequest|network|Network|^0\b/.test(file.error_message);
  const manager={
   add:files=>add(files),
   retry(index){const file=pending()[index];if(file && canRetry(file) && !drawer.busy && !drawer.pendingPayment?.()){file.failed=false;file.error_message=null;started=Date.now();monitor();uploader.uploader.upload_file(file,queue().indexOf(file)).catch(()=>refresh());}},
   refresh,
   ready:()=>!adding && !removing && !pending().length,
   args:()=>docs.size?{attachment_session:sessionId,attachments:[...docs.keys()]}:{},
   snapshot:()=>({sessionId,context,files:[...docs.values()]}),
   committed(){committed=true;docs.clear();this.stop();},
   stop(){stopped=true;if(timer)root.clearTimeout?.(timer);},
   async remove(id){if(drawer.busy || drawer.pendingPayment?.() || removing || !docs.has(id))return;removing=true;refresh();try{await call('discard',{session_id:sessionId,attachments:[id]},FILES);docs.delete(id);if(uploader)uploader.uploader.files=queue().filter(file=>file.doc?.name!==id);}catch(error){holder.find('.dlp-attachment-error').text(error.message);}finally{removing=false;refresh();}},
  };
  function refresh(){
   if(stopped || !drawer.alive())return;
   const waiting=pending();
   holder.find('.dlp-attachment-list').html([...docs.values()].map(doc=>`<div class="dlp-attachment-row"><a href="${esc(doc.file_url)}" target="_blank" rel="noopener" title="${esc(doc.file_name)}">${esc(doc.file_name)}</a><span class="text-success">${esc(t('已上传'))}</span><button type="button" class="btn btn-link dlp-remove-file" data-id="${esc(doc.name)}">${esc(t('移除'))}</button></div>`).join('')+waiting.map((file,index)=>`<div class="dlp-attachment-row"><span title="${esc(file.name)}">${esc(file.name)}</span><span class="${file.failed?'text-danger':''}">${esc(t(file.failed?(canRetry(file)?'上传失败':'上传未确认'):started && Date.now()-started>45000?'上传未确认':'正在上传…'))}</span>${canRetry(file)?`<button type="button" class="btn btn-link dlp-retry-file" data-index="${index}">${esc(t('重试'))}</button>`:''}<button type="button" class="btn btn-link dlp-remove-pending" data-index="${index}">${esc(t('移除'))}</button></div>`).join('')+(adding && !waiting.length?`<p>${esc(t('正在上传…'))}</p>`:''));
   drawer.panel.find('.dlp-create,.dlp-submit,.dlp-receipt-submit').prop('disabled',drawer.busy || !manager.ready());
   holder.find('.dlp-choose-files,.dlp-remove-file,.dlp-remove-pending,.dlp-retry-file').prop('disabled',drawer.busy || Boolean(drawer.pendingPayment?.()));
  }
  function monitor(){refresh();if(!stopped && drawer.alive() && pending().length && root.setTimeout)timer=root.setTimeout(monitor,250);}
  let initialization=null;
  async function ensure(){
   initialization ||= (async()=>{await call('begin_upload',{doctype:context[0],name:context[1],session_id:sessionId},FILES);await frappe.require('file_uploader.bundle.js');if(stopped || !drawer.alive())return;
    uploader=new frappe.ui.FileUploader({wrapper:holder.find('.dlp-native-uploader'),method:FILES+'upload_file',fieldname:sessionId,allow_multiple:true,make_attachments_public:false,allow_toggle_private:false,allow_toggle_optimize:false,disable_file_browser:true,allow_web_link:false,allow_take_photo:false,allow_google_drive:false,
     on_success:doc=>{if(doc?.doctype==='File' && doc.name && doc.is_private){docs.set(doc.name,doc);refresh();}else holder.find('.dlp-attachment-error').text(t('附件上传未完成'));}});
   })();return initialization;
  }
  async function add(files){
   if(drawer.busy || drawer.pendingPayment?.() || stopped || !files?.length)return;
   // File selection precedes the asynchronous session and bundle initialization.
   // Count every batch now so a save/submit cannot miss files awaiting ensure().
   adding++;refresh();
   try{await ensure();if(stopped || !drawer.alive() || drawer.busy || drawer.pendingPayment?.() || !uploader)return;const previous=new Set(queue());uploader.uploader.add_files(files);started=Date.now();monitor();for(const file of queue().filter(row=>!previous.has(row)))uploader.uploader.upload_file(file,queue().indexOf(file)).catch(()=>refresh());}
   catch(error){initialization=null;if(!stopped && drawer.alive())holder.find('.dlp-attachment-error').text(error.message || t('附件上传未完成'));}
   finally{adding--;refresh();}
  }
  holder.find('.dlp-choose-files').on('click.dlpDrawer',()=>holder.find('input[type=file]').trigger('click'));
  holder.find('input[type=file]').on('change.dlpDrawer',event=>{add(Array.from(event.target.files || []));event.target.value='';});
  holder.find('.dlp-attachment-drop').on('dragover.dlpDrawer',event=>event.preventDefault()).on('drop.dlpDrawer',event=>{event.preventDefault();add((event.originalEvent || event).dataTransfer?.files);});
  holder.on('click.dlpDrawer','.dlp-remove-file',event=>manager.remove(event.currentTarget.dataset.id));
  holder.on('click.dlpDrawer','.dlp-retry-file',event=>manager.retry(Number(event.currentTarget.dataset.index)));
  holder.on('click.dlpDrawer','.dlp-remove-pending',event=>{if(drawer.busy || drawer.pendingPayment?.())return;const file=pending()[Number(event.currentTarget.dataset.index)];if(!file || file.uploading)return;uploader.uploader.files=queue().filter(row=>row!==file);refresh();});
  drawer.attachments=manager;
  drawer.beforeClose=async()=>{if(committed || (!initialization && !docs.size && !queue().length))return true;if(drawer.pendingPayment?.())return true;drawer.setBusy(true);try{if(initialization)await initialization;await call('discard',{session_id:sessionId,cancel:1},FILES);return true;}catch(error){drawer.error(error);return false;}finally{drawer.setBusy(false);}};
  refresh();return manager;
 }
 async function showAttachments(drawer,name){const files=await call('list_attachments',{name},FILES);if(drawer.alive())drawer.panel.find('.dlp-existing-attachments').html((files || []).map(doc=>`<div class="dlp-attachment-row"><a href="${esc(doc.file_url)}" target="_blank" rel="noopener" title="${esc(doc.file_name)}">${esc(doc.file_name)}</a></div>`).join(''));}
 function advancedHTML(projection,type,name,sourceType,sourceName){return `<details${projection.advanced_reason?' open':''}><summary>${esc(t('高级处理 / 原生单据'))}</summary><p>${esc(projection.advanced_reason || '复杂税费、共享来源、跨币种、退款和付款计划沿用原生单据；此处不改变业务规则。')}</p>${name?link(type,name,'打开原生单据'):link(sourceType,sourceName,'打开来源单据')} ${sourceName?link(sourceType,sourceName,'查看来源'):''}</details>`;}
 function documentTable(doc){return `<div class="dlp-document-items"><table class="table table-bordered"><thead><tr><th>${t("来源 / 物料")}</th><th>${t("数量 / 当前剩余")}</th><th>${t("单位")}</th><th>${t("单价")}</th><th>${t("仓库")}</th></tr></thead><tbody>${(doc.items || []).map((row,index)=>`<tr data-row="${index}"><td>${esc(row.source_name || '')}<br>${esc(row.item_code)} · ${esc(row.item_name)}</td><td data-edit="qty"></td><td>${esc(row.uom)}</td><td data-edit="rate"></td><td data-edit="warehouse"></td></tr>`).join('')}</tbody></table></div>`;}
 async function mountDocumentItems(drawer,projection,session,scope=drawer.panel){
  const doc=projection.document,tasks=[];
  for(const [index,row] of (doc.items || []).entries())for(const field of ['qty','rate','warehouse']){
   const holder=scope.find(`[data-row="${index}"] [data-edit="${field}"]`),editable=projection.editable_item_fields.includes(field);
   if(!editable)holder.html(esc(field==='warehouse'?(row[field] || '—'):field==='rate'?money(row[field]):quantity(row[field])));
   else tasks.push(input(drawer,holder,{native_doctype:doc.doctype+' Item',currency_context:doc,fieldname:field,fieldtype:field==='warehouse'?'Link':field==='rate'?'Currency':'Float',options:field==='warehouse'?'Warehouse':undefined,label:field==='qty'?t("数量"):field==='rate'?t("单价"):t("仓库")},row[field],value=>session.touchItem(row.key,field,value)).then(control=>{if(field==='warehouse')control.get_query=()=>({filters:{company:doc.company,is_group:0}});}));
   if(field==='qty')$(`<small class="text-muted">${esc(t('最多'))} ${quantity(row.max_qty)}</small>`).appendTo(holder);
  }
  await Promise.all(tasks);
 }
 function nextSteps(drawer,documents,sourceType=null,sources=null){
  if(!documents.length || !documents.every(doc=>doc.docstatus===1))return;
  const type=documents[0].doctype,selected=documents.map(doc=>({name:doc.name,modified:doc.modified}));
  const footer=drawer.panel.find('footer');footer.find('.dlp-next-ap,.dlp-next-pay').remove();
  if(type==='Purchase Receipt')$(`<button type="button" class="btn btn-primary dlp-next-ap">${t("确认应付")}</button>`).appendTo(footer).on('click.dlpDrawer',()=>{drawer.close(true);return batchDocumentDrawer('Purchase Receipt',selected,'Purchase Invoice');});
  if(type==='Purchase Receipt' || sourceType)$(`<button type="button" class="btn btn-default dlp-next-pay">${t("部分 / 合并付款")}</button>`).appendTo(footer).on('click.dlpDrawer',()=>{drawer.close(true);return batchPay(type==='Purchase Receipt'?'Purchase Receipt':sourceType,type==='Purchase Receipt'?selected:sources);});
 }
 async function batchDocumentDrawer(sourceType,selected,targetType='Purchase Receipt'){
  if(sourceType==='Purchase Order' && targetType==='Purchase Invoice')return;
  const drawer=newDrawer(targetType==='Purchase Receipt'?'批量采购入库':'确认采购应付',true);if(!drawer)return;
  let sources=[...selected].sort((a,b)=>a.name.localeCompare(b.name)),merge=0,projections=[],sessions=[],saved=false;
  const token=retryToken(requestId,`dlp-document-batch:${frappe.session?.user}:${sourceType}:${sources.map(row=>row.name).join(',')}:${targetType}`);
  const context=()=>({source_doctype:sourceType,target_doctype:targetType,sources,merge});
  async function display(result){
   projections=result.documents;sessions=projections.map(editSession);saved=projections.every(row=>Boolean(row.document.name));if(result.sources)sources=result.sources;
   body(drawer,`${sources.map(row=>link(sourceType,row.name)).join(' · ')}<p>${targetType==='Purchase Receipt'?t("核对每行数量和仓库。保存草稿不改变库存；确认对本批全部单据执行原生提交。"):t("每张入库分别确认应付，不更新库存；付款需下一步明确确认。")}</p>${!saved && targetType==='Purchase Receipt'?`<label>${t("入库方式")} <select class="form-control dlp-batch-mode"><option value="0"${!merge?' selected':''}>${t("逐单入库")}</option><option value="1"${merge?' selected':''}>${t("合并入库")}</option></select></label>`:''}${projections.map((projection,index)=>{const doc=projection.document;return `<section data-batch-document="${index}"><p>${doc.name?link(targetType,doc.name):t("第 {0} 张", [index+1])} · ${esc(doc.company)} · ${esc(doc.supplier)} · ${money(doc.grand_total,doc.currency)} · ${doc.docstatus===1?t("已提交"):t("草稿")}</p>${documentTable(doc)}<div class="dlp-batch-headers"></div>${projection.advanced_reason?`<p class="text-warning">${esc(projection.advanced_reason)}</p>`:''}</section>`;}).join('')}`);
   drawer.setBusy(true);drawer.panel.find('footer').find('.dlp-batch-save,.dlp-batch-confirm,.dlp-next-ap,.dlp-next-pay,.dlp-batch-retry').remove();
   for(const [index,projection] of projections.entries()){
    const scope=drawer.panel.find(`[data-batch-document="${index}"]`);
    await mountDocumentItems(drawer,projection,sessions[index],scope);
    for(const field of projection.editable_fields || [])if(HEADERS_FOR_DRAWER.includes(field))await input(drawer,$('<div></div>').appendTo(scope.find('.dlp-batch-headers')),{native_doctype:targetType,fieldname:field,label:{posting_date:'单据日期',remarks:'备注',bill_no:'供应商票据号',bill_date:'票据日期'}[field]},projection.document[field],value=>sessions[index].touch(field,value));
   }
   drawer.panel.find('.dlp-batch-mode').on('change.dlpDrawer',async event=>{if(drawer.busy)return;const next=Number(event.target.value);const change=async()=>{merge=next;await load();};if(sessions.some(session=>session.dirty()))frappe.confirm(t("切换方式会丢弃未保存的输入，继续？"),change);else await change();});
   if(projections.every(row=>row.document.docstatus===0))$(`<button type="button" class="btn btn-default dlp-batch-save">${t("保存全部草稿")}</button>`).appendTo(drawer.panel.find('footer')).on('click.dlpDrawer',()=>write(0));
   const actions=projections.reduce((result,row)=>result.filter(action=>(row.allowed_actions || []).includes(action)),projections[0]?.allowed_actions || []);
   for(const action of actions)$(`<button type="button" class="btn btn-primary dlp-batch-confirm">${esc(action==='Submit'?(targetType==='Purchase Receipt'?'确认全部入库':'确认全部应付'):action)}</button>`).appendTo(drawer.panel.find('footer')).on('click.dlpDrawer',()=>frappe.confirm(t("确认执行本批全部原生单据操作？"),()=>write(1,action)));
   nextSteps(drawer,projections.map(row=>row.document),sourceType,sources);drawer.setBusy(false);
  }
  async function load(){drawer.setBusy(true);projections=[];sessions=[];drawer.panel.find('footer').find('.dlp-batch-save,.dlp-batch-confirm,.dlp-next-ap,.dlp-next-pay,.dlp-batch-retry,.dlp-batch-reload').remove();try{
   if(await reversalDrawer(drawer,sources.map(row=>({doctype:sourceType,name:row.name})),load))return;
   const pending=token.pending();
   if(pending){body(drawer,`<p class="text-warning">${t("上次响应未确认，请核对记录后重试同一批次内容。")}</p>`);$(`<button type="button" class="btn btn-primary dlp-batch-retry">${t("重试原操作")}</button>`).appendTo(drawer.panel.find('footer')).on('click.dlpDrawer',()=>write(0,null,pending));return;}
   if(targetType==='Purchase Invoice'){
    const chains=await Promise.all(sources.map(row=>call('get_purchase_chain',{source_doctype:sourceType,source_name:row.name,include_payments:false})));if(!drawer.alive())return;
    if(chains.every(chain=>chain.can_create_invoice===false && !(chain.draft_invoices || []).length && (chain.invoices || []).some(invoice=>invoice.docstatus===1))){
     const invoices=[...new Map(chains.flatMap(chain=>chain.invoices.filter(invoice=>invoice.docstatus===1)).map(invoice=>[invoice.name,invoice])).values()];
     body(drawer,`<p>${t("已有关联原生应付，请核对实际应付后继续付款。")}</p>${invoices.map(invoice=>link('Purchase Invoice',invoice.name)).join(' · ')}`);
     $(`<button type="button" class="btn btn-primary dlp-next-pay">${t("部分 / 合并付款")}</button>`).appendTo(drawer.panel.find('footer')).on('click.dlpDrawer',()=>{drawer.close(true);return batchPay(sourceType,sources);});return;
    }
   }
   const result=await call('preview_document_batch',context(),ACTIONS);if(!drawer.alive())return;await loadMetadata(targetType);if(drawer.alive())await display(result);
  }catch(error){if(drawer.alive()){body(drawer,`<p class="text-warning">${esc(error.message || '批量预览未完成，请核对原生单据。')}</p>${sources.map(row=>link(sourceType,row.name,'查看来源')).join(' · ')}`);if(targetType==='Purchase Receipt')$(`<button type="button" class="btn btn-primary dlp-batch-individual">${t("逐单重新预览")}</button>`).appendTo(drawer.panel.find('.dlp-payment-body')).on('click.dlpDrawer',()=>{if(drawer.busy)return;merge=0;return load();});}}finally{if(drawer.alive())drawer.setBusy(false);}}
  async function write(confirm,action=null,pending=null){if(drawer.busy || (!pending && !projections.length))return;drawer.setBusy(true);try{
   const payload=pending || {...context(),changes:sessions.map(session=>session.changes()),confirm,workflow_action:action==='Submit'?null:action,...(projections.some(row=>row.document.name)?{documents:projections.map(row=>({doctype:targetType,name:row.document.name,modified:row.document.modified}))}:{})};
   const result=await call('record_document_batch',{...payload,request_id:token.forPayload(payload)},ACTIONS);token.succeeded();if(!drawer.alive())return;if(result.failed){drawer.error(new Error(result.error || t("本批已回滚，请修改或重新预览。")));drawer.panel.find('footer').find('.dlp-batch-retry,.dlp-batch-reload').remove();$(`<button type="button" class="btn btn-default dlp-batch-reload">${t("重新预览")}</button>`).appendTo(drawer.panel.find('footer')).on('click.dlpDrawer',load);return;}await display(result);await refreshSurface();
  }catch(error){if(drawer.alive())drawer.error(error);}finally{if(drawer.alive())drawer.setBusy(false);}}
  await load();
 }
 const HEADERS_FOR_DRAWER=['posting_date','remarks','bill_no','bill_date'];
 async function batchPay(sourceType,selected){
  if(sourceType==='Purchase Order')return;
  const drawer=newDrawer('合并采购应付付款',true);if(!drawer)return;
  let sources=[...selected].sort((a,b)=>a.name.localeCompare(b.name));const token=paymentToken(sourceType,sources.map(row=>row.name).join(','));
  async function load(){try{
   if(await reversalDrawer(drawer,sources.map(row=>({doctype:sourceType,name:row.name})),load))return;
   const pending=token.pending();if(pending){body(drawer,`<p class="text-warning">${t("上次付款响应未确认，请核对后重试同一操作。")}</p>`);$(`<button type="button" class="btn btn-primary dlp-retry-payment">${t("重试付款")}</button>`).appendTo(drawer.panel.find('footer')).on('click.dlpDrawer',()=>sendPayment(drawer,pending,token,{sourceType,sourceName:sources[0].name}));return;}
   const chain=await call('preview_payment_batch',{source_doctype:sourceType,sources});if(!drawer.alive())return;sources=chain.sources;await loadMetadata('Payment Entry');await loadMetadata('Payment Entry Reference');
   body(drawer,`<p>${esc(chain.company)} · ${esc(chain.supplier)} · ${esc(chain.currency)}</p><p>${t("按真实应付整单余额核销，共享应付只列一次。金额为0的应付不参加本次付款。")}</p><div class="dlp-batch-allocations">${chain.invoices.map((invoice,index)=>`<section data-invoice="${index}"><p>${t("{0} · 最新未付 {1}", [link('Purchase Invoice',invoice.name), money(invoice.outstanding,invoice.currency)])}</p><div class="dlp-allocation-input"></div></section>`).join('')}</div><div class="dlp-fields"></div><section class="dlp-attachments"></section>`);drawer.setBusy(true);
   const amounts=new Map(chain.invoices.map(invoice=>[invoice.name,invoice.outstanding])),controls={};
   for(const [index,invoice] of chain.invoices.entries())await input(drawer,drawer.panel.find(`[data-invoice="${index}"] .dlp-allocation-input`),{native_doctype:'Payment Entry Reference',native_fieldname:'allocated_amount',fieldname:'allocated_amount',currency_context:{currency:invoice.currency},label:t("本次核销金额")},invoice.outstanding,value=>amounts.set(invoice.name,value));
   for(const [field,native,type,label,value] of [['bank','paid_from','Link','付款账户',''],['date','posting_date','Date','付款日期',frappe.datetime.get_today()],['reference','reference_no','Data','银行参考号',''],['remarks','remarks','Small Text','备注','']])controls[field]=await input(drawer,$('<div></div>').appendTo(drawer.panel.find('.dlp-fields')),{native_doctype:'Payment Entry',native_fieldname:native,fieldname:field,fieldtype:type,options:field==='bank'?'Account':undefined,label},value);
   controls.bank.get_query=()=>({filters:{company:chain.company,is_group:0,disabled:0,account_type:['in',['Bank','Cash']],account_currency:chain.currency}});
   await mountAttachments(drawer,sourceType,sources[0].name);drawer.pendingPayment=()=>Boolean(token.pending());
   const record=confirm=>{if(drawer.busy)return;if(!drawer.attachments.ready()){drawer.error(new Error(t("请等待附件上传完成，或移除失败文件。")));return;}if([...amounts.values()].some(amount=>!Number.isFinite(amount) || amount<0)){drawer.error(new Error(t("每笔核销金额必须是有效的非负数，不付款请明确填0。")));return;}const allocations=[...amounts].filter(([,amount])=>amount>0).map(([name,amount])=>({name,amount}));return sendPayment(drawer,{source_doctype:sourceType,source_name:sources[0].name,sources,allocations,bank_account:controls.bank.get_value(),posting_date:controls.date.get_value(),reference_no:controls.reference.get_value(),remarks:controls.remarks.get_value(),confirm,...drawer.attachments.args()},token,{sourceType,sourceName:sources[0].name});};
   $(`<button type="button" class="btn btn-default dlp-create">${t("保存付款草稿")}</button>`).appendTo(drawer.panel.find('footer')).on('click.dlpDrawer',()=>record(0));
   $(`<button type="button" class="btn btn-primary dlp-submit">${t("确认合并付款")}</button>`).appendTo(drawer.panel.find('footer')).on('click.dlpDrawer',()=>frappe.confirm(t("确认将本次金额核销到所列应付单？此处仅记账，不调用网银。"),()=>record(1)));drawer.setBusy(false);
  }catch(error){if(drawer.alive()){body(drawer,`<p class="text-warning">${esc(error.message || '请核对原生应付与付款单。')}</p>${sources.map(row=>link(sourceType,row.name)).join(' · ')}`);drawer.setBusy(false);}}}
  await load();
 }
 function metadataError(){const error=new Error(t("单据字段元数据读取失败，请刷新或在原生单据核对。"));error.dlpMetadata=true;return error;}
 async function loadMetadata(doctype){try{await frappe.model.with_doctype(doctype);}catch(error){throw metadataError();}}
 async function input(drawer,holder,df,value,onTouched){
  if(!drawer.alive())throw new Error(t("抽屉已关闭"));
  const {native_doctype,native_fieldname,currency_context,...presentation}=df;
  const native=frappe.meta.get_docfield(native_doctype,native_fieldname || df.fieldname);if(!native)throw metadataError();
  const numeric=['Currency','Float'].includes(native.fieldtype);
  // Native metadata/defaults remain the sole business parse precision. Labels,
  // aliases and display density belong only to this drawer control instance.
  const overrides=Object.fromEntries(Object.entries(presentation).filter(([key,val])=>key!=='precision' && val!==undefined));
  const control=frappe.ui.form.make_control({parent:holder,df:{...native,...overrides,label:t(overrides.label || native.label),...(numeric?{fieldtype:native.fieldtype}:{})},render_input:true});
  control.$input?.attr('aria-label',t(overrides.label || native.label));
  drawer.controls.push(control);
  if(!drawer.alive()){disposeControls(drawer.controls);throw new Error(t("抽屉已关闭"));}
  if(currency_context)control.get_doc=()=>currency_context;
  if(numeric)formatNumericInput(control);
  await control.set_value(value ?? '');
  if(!drawer.alive()){disposeControls(drawer.controls.includes(control)?drawer.controls:[control]);throw new Error(t("抽屉已关闭"));}
  if(onTouched && numeric){
   // Capture before the native change handler formats input back to two display
   // decimals. Never read that rounded presentation into the write projection.
   const node=control.$input?.get(0);if(!node?.addEventListener)throw new Error(t("输入控件不可用，请在原生单据核对。"));
   const touched=()=>{if(drawer.alive())onTouched(control.get_value(),control.$input.val());};
   for(const event of ['input','change'])node.addEventListener(event,touched,true);
   control.dlpDisposeInput=()=>{for(const event of ['input','change'])node.removeEventListener(event,touched,true);};
  }else if(onTouched)control.$input?.on('input.dlpDrawer change.dlpDrawer',()=>{if(drawer.alive())onTouched(control.get_value(),control.$input.val());});
  return control;
 }
 function actionButtons(drawer,projection,session,onSave,onReload,onPaymentSubmit=null,confirmLabel='确认付款'){
  const footer=drawer.panel.find('footer');let writeCompleted=false;footer.find('.dlp-save,.dlp-submit,.dlp-reload').remove();
  if((!onPaymentSubmit || projection.document.doctype==='Purchase Receipt') && !projection.advanced_reason && projection.document.docstatus===0 && (projection.editable_fields?.length || projection.editable_item_fields?.length))$(`<button type="button" class="btn ${onPaymentSubmit?'btn-default':'btn-primary'} dlp-save">${esc(t('保存草稿'))}</button>`).appendTo(footer).on('click.dlpDrawer',()=>{if(drawer.alive() && !writeCompleted)return onSave();});
  for(const action of projection.allowed_actions || []){
   if(!projection.document.name && !onPaymentSubmit)continue;
   const label=action==='Submit'?(onPaymentSubmit?confirmLabel:'提交并记账'):action;
   $(`<button type="button" class="btn ${onPaymentSubmit?'btn-primary':'btn-default'} dlp-submit">${esc(t(label))}</button>`).appendTo(footer).on('click.dlpDrawer',()=>{
    if(!drawer.alive() || writeCompleted)return;
    if(onPaymentSubmit)return onPaymentSubmit(action);
    if(session.dirty()){drawer.error(new Error(t("请先明确保存草稿，再提交。")));return;}
    frappe.confirm(t("确认对 {0} 执行“{1}”？此操作沿用原生权限及工作流，提交后会记账。", [esc(projection.document.name), esc(label)]),async()=>{
     if(!drawer.alive() || drawer.busy || writeCompleted)return;drawer.setBusy(true);
     try{
      await call('submit_document',{doctype:projection.document.doctype,name:projection.document.name,expected_modified:projection.document.modified,...(action==='Submit'?{}:{workflow_action:action})},ACTIONS);
      // Acknowledged native write is distinct from the subsequent display read.
      // This old projection must never offer another save/submit after success.
      writeCompleted=true;
      if(drawer.alive()){footer.find('.dlp-save,.dlp-submit').remove();for(const control of drawer.controls)control.$input?.prop('disabled',true);refreshSurface();await onReload();if(drawer.alive())frappe.show_alert({message:t("原生操作已完成"),indicator:'green'});}
     }
     catch(error){if(drawer.alive())drawer.error(new Error(writeCompleted?t("原生操作已完成，但页面刷新失败。请刷新抽屉或核对原生单据，不要重复提交。"):t("提交未完成：请核对系统提示；版本改变时先刷新抽屉。")));}
     finally{if(drawer.alive())drawer.setBusy(false);}
    });
   });
  }
  const reload=async()=>{
   if(!drawer.alive() || drawer.busy)return;drawer.setBusy(true);
   try{await onReload();}
   catch(error){if(drawer.alive())drawer.error(new Error(error.dlpMetadata?error.message:t("刷新未完成，请核对权限或原生单据后重试。")));}
   finally{if(drawer.alive())drawer.setBusy(false);}
  };
  if(!onPaymentSubmit)$(`<button type="button" class="btn btn-default dlp-reload">${esc(t('刷新抽屉'))}</button>`).appendTo(footer).on('click.dlpDrawer',async()=>{
   if(!drawer.alive() || drawer.busy)return;
   if(session.dirty()){frappe.confirm(t("刷新会丢弃尚未保存的输入，继续？"),reload);}else await reload();
  });
 }
 async function documentDrawer(sourceType,sourceName,targetType,targetName=null){
  if(sourceType==='Purchase Order' && targetType==='Purchase Invoice')return;
  const drawer=newDrawer(targetType==='Purchase Receipt'?'采购订单 → 入库':'采购入库 → 确认应付',true);if(!drawer)return;
  const token=retryToken(requestId);let projection,session,allowAnother=false;
  const context={source_doctype:sourceType,source_name:sourceName,target_doctype:targetType};
  async function load(){
   const generation=++drawer.loadId;
   if(await reversalDrawer(drawer,[{doctype:sourceType,name:sourceName}],load))return;
   if(!drawer.alive() || generation!==drawer.loadId)return;
   const result=await call('preview_document',{...context,...(targetName?{target_name:targetName}:{})},ACTIONS);if(!drawer.alive() || generation!==drawer.loadId)return;
   await loadMetadata(targetType);if(!drawer.alive() || generation!==drawer.loadId)return;
   targetName=result.document.name || null;
   projection=result;session=editSession(result);await render();
  }
  async function render(){
   if(!drawer.alive())return;const doc=projection.document;
   body(drawer,`<p>${link(sourceType,sourceName)} ${doc.name?`→ ${link(targetType,doc.name)}`:''}</p><div class="dlp-document-info"><span>${esc(t('供应商'))} ${esc(doc.supplier)}</span><span>${esc(t('公司'))} ${esc(doc.company)}</span><span>${esc(t('币种'))} ${esc(doc.currency)}</span><span>${esc(t(doc.docstatus===0?'草稿 · 未记账':'已提交'))}</span></div><p class="text-muted">${targetType==='Purchase Receipt'?esc(t('核对本次收货数量和仓库后确认入库；保存草稿不会改变库存。')):t("先核对数量与金额并保存草稿，再明确提交确认应付；不会自动付款。")}</p><div class="dlp-document-fields"></div><div class="dlp-document-items"><table class="table table-bordered"><thead><tr><th>${esc(t('物料'))}</th><th>${esc(t('数量 / 当前剩余'))}</th><th>${esc(t('单位'))}</th><th>${esc(t('单价'))}</th><th>${esc(t('仓库'))}</th></tr></thead><tbody>${(doc.items || []).map((row,index)=>`<tr data-row="${index}"><td title="${esc(row.item_name)}">${esc(row.item_code)}<br>${esc(row.item_name)}</td><td data-edit="qty"></td><td>${esc(row.uom)}</td><td data-edit="rate"></td><td data-edit="warehouse"></td></tr>`).join('')}</tbody></table></div><p>${esc(t('不含税'))} ${money(doc.net_total,doc.currency)}　<b>${esc(t('总金额'))} ${money(doc.grand_total,doc.currency)}</b>${esc(t('（保存后由原生规则重算）'))}</p><details><summary>${esc(t('税费（原生计算，只读）'))}</summary><table class="table table-bordered"><tbody>${(doc.taxes || []).map(row=>`<tr><td>${esc(row.description || row.account_head)}</td><td>${quantity(row.rate)}%</td><td>${money(row.tax_amount,doc.currency)}</td></tr>`).join('') || `<tr><td>${esc(t('无税费'))}</td></tr>`}</tbody></table></details>${advancedHTML(projection,targetType,doc.name,sourceType,sourceName)}`);
   drawer.setBusy(true);const tasks=[];
   for(const [field,type,label] of [['posting_date','Date','单据日期'],['bill_no','Data','供应商票据号'],['bill_date','Date','票据日期'],['remarks','Small Text','备注']]){
    if(!(field in doc))continue;
    const holder=$('<div></div>').appendTo(drawer.panel.find('.dlp-document-fields'));
    tasks.push(input(drawer,holder,{native_doctype:targetType,fieldname:field,fieldtype:type,label,read_only:!projection.editable_fields.includes(field)},doc[field],projection.editable_fields.includes(field)?value=>session.touch(field,value):null));
   }
   tasks.push(mountDocumentItems(drawer,projection,session));
   await Promise.all(tasks);if(!drawer.alive())return;
   if(targetType==='Purchase Receipt' && doc.name){
    $(`<button type="button" class="btn btn-default dlp-explicit-new">${esc(t('明确新建另一张剩余入库草稿'))}</button>`).appendTo(drawer.panel.find('.dlp-payment-body')).on('click.dlpDrawer',()=>frappe.confirm(t('已有草稿。仍要明确新建另一张入库草稿？请确认不是重复收货。'),async()=>{if(!drawer.alive() || drawer.busy)return;targetName=null;allowAnother=true;try{await load();}catch(error){if(drawer.alive())drawer.error(error);}}));
   }
   actionButtons(drawer,projection,session,()=>save(0),load,targetType==='Purchase Receipt'?action=>save(1,action):null,'确认入库');nextSteps(drawer,[doc],sourceType,[{name:sourceName}]);drawer.setBusy(false);
  }
  async function save(confirm=0,workflowAction=null){
   if(drawer.busy)return;drawer.setBusy(true);
   try{
    const args={...context,changes:session.changes(),...(targetName?{target_name:targetName,expected_modified:session.document.modified}:{}),...(allowAnother?{allow_another_draft:1}:{})};
    const {source_doctype,target_doctype,...receiptArgs}=args;
    const payload=targetType==='Purchase Receipt'?{...receiptArgs,confirm,...(workflowAction && workflowAction!=='Submit'?{workflow_action:workflowAction}:{})}:args;
    const result=await call(targetType==='Purchase Receipt'?'record_receipt':'save_document_draft',{...payload,request_id:token.forPayload(payload)},ACTIONS);
    if(result.failed){token.succeeded();throw new Error(result.error);}
    token.succeeded();if(!drawer.alive())return;
    targetName=result.document.name;allowAnother=false;projection=result;session.reset(result);await render();refreshSurface();frappe.show_alert({message:`${result.document.docstatus===1?t('入库已确认'):t('已保存草稿')} ${link(targetType,targetName)}`,indicator:'green'});
   }catch(error){if(drawer.alive())drawer.error(new Error(error.message || t("保存失败：相同内容可重试；若响应不确定请核对记录。版本改变时刷新抽屉。")));}
   finally{if(drawer.alive())drawer.setBusy(false);}
  }
  try{
   if(sourceType==='Purchase Order' && targetType==='Purchase Receipt' && !targetName){
    const drafts=await receiptDrafts(sourceName);if(!drawer.alive())return;
    if(drafts.length===1)targetName=drafts[0].name;
    if(drafts.length>1){
     body(drawer,`<p>${link(sourceType,sourceName)} · ${drafts.length} ${esc(t('张可见入库草稿。请明确选择继续编辑，避免重复建单。'))}</p><div class="dlp-draft-choices">${drafts.map(draft=>`<p><button type="button" class="btn btn-default dlp-draft-choice" data-target="${esc(draft.name)}">${esc(t('继续'))} ${esc(draft.name)}</button> ${link('Purchase Receipt',draft.name,'原生详情')}</p>`).join('')}</div><button type="button" class="btn btn-default dlp-explicit-new">${esc(t('明确新建另一张剩余入库草稿'))}</button>`);
     drawer.panel.find('.dlp-draft-choice').on('click.dlpDrawer',async e=>{targetName=e.currentTarget.dataset.target;try{await load();}catch(error){if(drawer.alive())drawer.error(error);}});
     drawer.panel.find('.dlp-explicit-new').on('click.dlpDrawer',()=>frappe.confirm(t('已有草稿未提交。仍要明确新建另一张入库草稿？请先确认不是重复收货。'),async()=>{if(!drawer.alive())return;allowAnother=true;try{await load();}catch(error){if(drawer.alive())drawer.error(error);}}));
     return;
    }
   }
   await load();
  }catch(error){if(drawer.alive()){body(drawer,`<p class="text-danger">${error.dlpMetadata?esc(error.message):t("无法预览，请核对权限、剩余数量或关联单据。已有草稿请在列表继续编辑。")}</p>${link(sourceType,sourceName,'打开来源单据')}`);drawer.setBusy(false);}}
 }
 async function pay(sourceType,sourceName,invoiceName=null){
  if(sourceType==='Purchase Order')return;
  const drawer=newDrawer('采购付款');if(!drawer)return;const token=paymentToken(sourceType,sourceName);
  async function load(){try{
   if(await reversalDrawer(drawer,[{doctype:sourceType,name:sourceName}],load))return;
   const chain=await call('get_purchase_chain',{source_doctype:sourceType,source_name:sourceName});if(!drawer.alive())return;
   const pending=token.pending();
   if(pending){body(drawer,`<p class="text-warning">${esc(t('上次付款响应未确认，请核对后重试同一操作。'))}</p><p>${pending.purchase_invoice?link('Purchase Invoice',pending.purchase_invoice):link(sourceType,sourceName)} · ${money(pending.amount_to_pay)} · ${esc(pending.bank_account)}</p><a href="/desk/purchase-payment-records">${esc(t('采购付款记录'))}</a>`);$(`<button type="button" class="btn btn-primary dlp-retry-payment">${esc(t(pending.confirm?'重试确认付款':'重试保存草稿'))}</button>`).appendTo(drawer.panel.find('footer')).on('click.dlpDrawer',()=>sendPayment(drawer,pending,token,{sourceType,sourceName}));return;}

   const drafts=(chain.payments || []).filter(row=>row.docstatus===0 && row.payment_type==='Pay' && (!invoiceName || (row.references || []).some(ref=>ref.doctype==='Purchase Invoice' && ref.name===invoiceName)));
   if(drafts.length){
    if(drafts.length===1){drawer.close(true);return paymentDrawer(drafts[0].name,{sourceType,sourceName,chain});}
    body(drawer,`<p>${t("已有付款草稿，请核对金额、账户与采购引用后选择继续处理。")}</p>${drafts.map(row=>`<p><button type="button" class="btn btn-default dlp-continue-payment" data-name="${esc(row.name)}">${t("继续 {0}", [esc(row.name)])}</button> · ${money(row.amount,row.currency)} · ${esc(row.bank_account || '账户不可见')}</p>`).join('')}`);
    drawer.panel.find('.dlp-continue-payment').on('click.dlpDrawer',event=>{drawer.close(true);paymentDrawer(event.currentTarget.dataset.name,{sourceType,sourceName,chain});});return;
   }
   if(!chain.can_create){body(drawer,`<p>${esc(chain.reason)}</p>${link(sourceType,sourceName,'打开原生来源')}`);return;}
   const eligible=chain.invoices.filter(invoice=>invoice.can_pay),controls={};let amountTouched=false,amountInput,invoiceGeneration=0;
   if(!eligible.length){body(drawer,`<p>${t("当前没有可快捷付款的应付余额，请打开原生应付单处理。")}</p>`);return;}
   const selected=invoiceName?eligible.find(invoice=>invoice.name===invoiceName):eligible[0];
   if(!selected){body(drawer,`<p>${esc(t('当前应付单已不可快捷付款，请刷新并核对来源。'))}</p>${link(sourceType,sourceName,'打开原生来源')}`);return;}
   await loadMetadata('Payment Entry');if(!drawer.alive())return;
   body(drawer,`<p><strong>${esc(chain.supplier)}</strong><br>${link(sourceType,sourceName)}</p><div class="dlp-payment-balances">${balanceHTML(chain)}</div>${(chain.warnings || []).map(w=>`<p class="text-warning">${esc(w)}</p>`).join('')}<div class="dlp-fields"></div><section class="dlp-attachments"></section><details class="dlp-remarks"><summary>${esc(t('备注'))}</summary><div class="dlp-remarks-field"></div></details>`);
   drawer.setBusy(true);
   const currencyContext={currency:selected.currency,paid_from_account_currency:selected.currency};
   const add=async(df,value,touched)=>controls[df.fieldname]=await input(drawer,$('<div></div>').appendTo(drawer.panel.find('.dlp-fields')),{native_doctype:'Payment Entry',...df},value,touched);
   await add({native_doctype:'Payment Entry Reference',native_fieldname:'reference_name',fieldname:'invoice',fieldtype:'Select',label:t("关联采购应付单"),options:eligible.map(i=>i.name).join('\n'),reqd:1},selected.name);
   await add({native_fieldname:'paid_amount',currency_context:currencyContext,fieldname:'amount',fieldtype:'Currency',label:t("本次付款金额"),reqd:1},selected.outstanding,value=>{amountTouched=true;amountInput=value;});
   await add({native_fieldname:'paid_from',fieldname:'bank',fieldtype:'Link',options:'Account',label:t("付款账户"),reqd:1},'');
   controls.bank.get_query=()=>({filters:{company:chain.company,is_group:0,disabled:0,account_type:['in',['Bank','Cash']],account_currency:eligible.find(i=>i.name===controls.invoice.get_value())?.currency}});
   await add({native_fieldname:'posting_date',fieldname:'date',fieldtype:'Date',label:t("付款日期"),reqd:1},frappe.datetime.get_today());
   await add({native_fieldname:'reference_no',fieldname:'reference',fieldtype:'Data',label:t("银行参考号")},'');
   controls.remarks=await input(drawer,drawer.panel.find('.dlp-remarks-field'),{native_doctype:'Payment Entry',fieldname:'remarks',fieldtype:'Small Text',label:t("备注")},'');
   await mountAttachments(drawer,sourceType,sourceName);drawer.pendingPayment=()=>Boolean(token.pending());
   controls.invoice.$input?.on('change.dlpInvoice',async()=>{const generation=++invoiceGeneration,chosen=eligible.find(i=>i.name===controls.invoice.get_value());if(chosen && drawer.alive()){currencyContext.currency=currencyContext.paid_from_account_currency=chosen.currency;await controls.amount.set_value(chosen.outstanding);if(drawer.alive() && generation===invoiceGeneration)amountTouched=false;}});
   if(!drawer.alive())return;
   const record=async confirm=>{
    if(drawer.busy || drawer.paymentCompleted)return;const chosen=eligible.find(i=>i.name===controls.invoice.get_value()),amount=amountTouched?amountInput:chosen?.outstanding;
    if(!drawer.attachments.ready()){drawer.error(new Error(t('请等待附件上传完成，或移除失败文件。')));return;}
    if(!chosen || !Number.isFinite(Number(amount)) || Number(amount)<=0 || Number(amount)>Number(chosen.outstanding) || !controls.bank.get_value() || !controls.date.get_value()){drawer.error(new Error(t("请填写账户、日期，以及不超过未付余额的正数金额。")));return;}
    const args={source_doctype:sourceType,source_name:sourceName,purchase_invoice:chosen.name,amount_to_pay:amount,bank_account:controls.bank.get_value(),posting_date:controls.date.get_value(),reference_no:controls.reference.get_value(),remarks:controls.remarks.get_value(),confirm,...drawer.attachments.args()};
    return sendPayment(drawer,args,token,{sourceType,sourceName,chain});
   };
   $(`<button type="button" class="btn btn-default dlp-save-payment">${esc(t('保存草稿'))}</button>`).appendTo(drawer.panel.find('footer')).on('click.dlpDrawer',()=>record(0));
   $(`<button type="button" class="btn btn-primary dlp-create">${esc(t('确认付款'))}</button>`).appendTo(drawer.panel.find('footer')).on('click.dlpDrawer',()=>record(1));drawer.setBusy(false);
  }catch(error){if(drawer.alive()){body(drawer,`<p class="text-danger">${error.dlpMetadata?esc(error.message):t("无法读取付款信息，请核对系统提示。")}</p>${link(sourceType,sourceName,'打开原生来源')}`);drawer.setBusy(false);}}}
  await load();
 }
 async function paymentResult(drawer,result,context={}){
  const doc=result.document;body(drawer,`<p class="text-success" role="status">${esc(t('付款已提交'))} · ${money(doc.amount,doc.currency)}</p><p>${esc(t('本次付款'))} ${link('Payment Entry',doc.name)}</p><div class="dlp-payment-balances" role="status">${esc(t('正在刷新余额…'))}</div>${context.sourceName?`<p>${link(context.sourceType,context.sourceName,'查看来源单据')}</p>`:''}<p><a href="/desk/purchase-payment-records">${esc(t('采购付款记录'))}</a></p>`);
  drawer.panel.find('footer').find('.dlp-save,.dlp-submit,.dlp-reload,.dlp-save-payment,.dlp-create').remove();drawer.panel.find('.dlp-cancel').text(t('完成'));
  if(context.sourceName){try{const chain=await call('get_purchase_chain',{source_doctype:context.sourceType,source_name:context.sourceName});if(drawer.alive())drawer.panel.find('.dlp-payment-balances').html(balanceHTML(chain));}catch(error){if(drawer.alive())drawer.error(new Error(t("付款已提交，余额刷新失败。请刷新来源或查看本次付款，勿重复提交。")));}}
  else drawer.panel.find('.dlp-payment-balances').remove();
  if(!drawer.alive())return;
  drawer.panel.find('.dlp-payment-body').append('<section class="dlp-existing-attachments"></section>');
  try{await showAttachments(drawer,doc.name);}catch(error){if(drawer.alive())drawer.error(new Error(t('付款已提交，附件读取失败。请打开付款单核对。')));}
 }
 async function paymentDrawer(name,context={}){
  const drawer=newDrawer('采购付款');if(!drawer)return;let projection,session,completed=false;const token=retryToken(requestId,`dlp-existing-payment:${frappe.boot?.sitename || root.location?.host}:${frappe.session?.user}:${name}`);drawer.pendingPayment=()=>Boolean(token.pending());
  async function load(){const generation=++drawer.loadId;if(await reversalDrawer(drawer,[{doctype:'Payment Entry',name}],load))return;const result=await call('preview_payment',{name},ACTIONS);if(!drawer.alive() || generation!==drawer.loadId)return;await loadMetadata('Payment Entry');if(!drawer.alive() || generation!==drawer.loadId)return;projection=result;session=editSession(result);await render();}
  async function render(){
   const doc=projection.document,notice=context.notice || (doc.docstatus===0 && !projection.allowed_actions?.includes('Submit')?(projection.allowed_actions?.length?'付款待审批，尚未计已付。':'当前没有提交或审批权限。'):'');
   const pending=token.pending();
   if(doc.docstatus===1 && pending)token.succeeded();
   if(doc.docstatus===0 && pending){body(drawer,`<p class="text-warning">${esc(t('上次付款响应未确认，请核对后重试同一操作。'))}</p><p>${link('Payment Entry',name)} · ${money(pending.changes?.amount ?? doc.amount,doc.currency)}</p>`);$(`<button type="button" class="btn btn-primary dlp-retry-existing">${esc(t('重试确认付款'))}</button>`).appendTo(drawer.panel.find('footer')).on('click.dlpDrawer',()=>confirm(pending.workflow_action,true));drawer.setBusy(false);return;}
   body(drawer,`<p><strong>${esc(doc.supplier)}</strong><br>${link('Payment Entry',name)} · ${esc(t({0:'已有待确认付款',1:'已提交',2:'已取消'}[doc.docstatus]))}</p>${notice?`<p class="text-warning" role="status">${esc(t(notice))}</p>`:''}${context.chain?`<div class="dlp-payment-balances">${balanceHTML(context.chain)}</div>`:''}<div class="dlp-fields"></div><section class="dlp-existing-attachments"></section>${doc.docstatus===0 && projection.editable_fields?.length && !projection.advanced_reason?'<section class="dlp-attachments"></section>':''}<details class="dlp-remarks"><summary>${esc(t('备注'))}</summary><div class="dlp-remarks-field"></div></details>${projection.advanced_reason?`<p class="text-warning">${esc(t(projection.advanced_reason))} ${link('Payment Entry',name,'打开原生付款单')}</p>`:''}<details><summary>${esc(t('采购核销引用'))}</summary>${referencesHTML(doc.references)}</details>`);
   drawer.setBusy(true);
   for(const [field,type,label] of [['posting_date','Date','付款日期'],['amount','Currency','本次付款金额'],['bank_account','Link','付款账户'],['reference_no','Data','银行参考号'],['remarks','Small Text','备注']]){
    const nativeField=field==='amount'?(doc.payment_type==='Receive'?'received_amount':'paid_amount'):field==='bank_account'?(doc.payment_type==='Receive'?'paid_to':'paid_from'):field;
    const editable=projection.editable_fields.includes(field),control=await input(drawer,field==='remarks'?drawer.panel.find('.dlp-remarks-field'):$('<div></div>').appendTo(drawer.panel.find('.dlp-fields')),{native_doctype:'Payment Entry',native_fieldname:nativeField,currency_context:{...doc,paid_from_account_currency:doc.currency,paid_to_account_currency:doc.currency},fieldname:field,fieldtype:type,options:field==='bank_account'?'Account':undefined,label,read_only:!editable},doc[field],editable?value=>session.touch(field,value):null);
    if(field==='bank_account')control.get_query=()=>({filters:{company:doc.company,is_group:0,disabled:0,account_type:['in',['Bank','Cash']],account_currency:doc.currency}});
   }
   if(!drawer.alive())return;
   if(doc.docstatus===0 && projection.editable_fields?.length && !projection.advanced_reason)await mountAttachments(drawer,'Payment Entry',name,context.staged);
   try{await showAttachments(drawer,name);}catch(error){drawer.error(error);}
   actionButtons(drawer,projection,session,null,load,confirm);drawer.setBusy(false);
  }
  async function confirm(action,original=false){
   if(drawer.busy || completed)return;
   if(drawer.attachments && !drawer.attachments.ready()){drawer.error(new Error(t('请等待附件上传完成，或移除失败文件。')));return;}drawer.setBusy(true);
   try{const args=original?token.pending():{name,changes:session.changes(),expected_modified:session.document.modified,workflow_action:action,...(drawer.attachments?.args() || {})};const result=await call('complete_payment',{...args,request_id:token.forPayload(args)},ACTIONS);token.succeeded();if(!drawer.alive())return;if(result.failed){drawer.error(new Error(result.error || t('付款未提交，请核对后重试。')));return;}drawer.attachments?.committed();completed=result.document.docstatus===1;if(completed){await paymentResult(drawer,result,context);await refreshSurface();}else{projection=result;session.reset(result);context.staged=null;context.notice='付款待审批，尚未计已付。';await render();}}
   catch(error){if(drawer.alive())drawer.error(new Error(completed?t("付款已提交，页面刷新失败。请查看本次付款，勿重复提交。"):error.message || t("付款未确认：请核对系统提示；相同内容可重试，响应不确定时先查看本次付款。")));}
   finally{if(drawer.alive())drawer.setBusy(false);}
  }
  try{await load();}catch(error){if(drawer.alive()){body(drawer,`<p class="text-danger">${error.dlpMetadata?esc(error.message):t("账户或单据不可用 / 无权读取，请在原生单据核对。")}</p>${link('Payment Entry',name,'打开原生付款单')}`);drawer.setBusy(false);}}
 }
 async function voucherDrawer(name){
  const drawer=newDrawer('财务凭证（只读）',true);if(!drawer)return;
  try{const doc=await call('get_voucher_detail',{name},ACTIONS);if(!drawer.alive())return;body(drawer,`<p>${link('China Accounting Voucher',name)} · ${esc(doc.statutory_number || '—')} · ${esc(state(doc.status))} · ${esc(doc.source_event==='Cancellation'?'冲销':'记账')}</p><p>${esc(doc.company)} · ${esc(doc.posting_date)}</p><p>${t("来源 {0} {1} {2}", [link(doc.source_doctype,doc.source_name), doc.reversal_of?t("原凭证 {0}", [link('China Accounting Voucher',doc.reversal_of)]):'', doc.reversed_by?t("冲销凭证 {0}", [link('China Accounting Voucher',doc.reversed_by)]):''])}</p><div class="dlp-document-items"><table class="table table-bordered"><thead><tr><th>${t("科目")}</th><th>${t("借方")}</th><th>${t("贷方")}</th><th>${t("摘要")}</th></tr></thead><tbody>${(doc.entries || []).map(row=>`<tr><td>${esc(row.account_name || row.account)}</td><td class="text-right">${money(row.debit,doc.currency)}</td><td class="text-right">${money(row.credit,doc.currency)}</td><td>${esc(row.remarks)}</td></tr>`).join('')}</tbody></table></div><p>${t("借方 {0}\\u3000贷方 {1}", [money(doc.total_debit,doc.currency), money(doc.total_credit,doc.currency)])}</p>${(doc.warnings || []).map(w=>`<p class="text-warning">${esc(w)}</p>`).join('')}`);}catch(error){if(drawer.alive())body(drawer,`<p class="text-danger">${t("无权读取或凭证不可用，请核对系统提示。")}</p>`);}
 }
 async function formRefresh(frm){
  returnContext(frm);
  const reversal=await reversalForm(frm);
  if(!['Purchase Order','Purchase Receipt'].includes(frm.doctype))return;
  frm.$wrapper.find('.dlp-purchase-chain').remove();if(frm.is_new())return;
  const current=frm.doc.name,generation=frm.dlpChainGeneration=(frm.dlpChainGeneration || 0)+1;
  try{const chain=await call('get_purchase_chain',{source_doctype:frm.doctype,source_name:current});if(frm.doc.name!==current || frm.dlpChainGeneration!==generation)return;
   const progress=(chain.order_progress || []).map(order=>`<p>${t("{0} · 订单 {1} · {2} · 待入货品金额 {3}（不含税）", [link('Purchase Order',order.name), money(order.grand_total,order.currency), (order.units || []).map(unit=>t("已入 {0} / 待入 {1} {2}", [quantity(unit.received), quantity(unit.pending), esc(unit.uom)])).join('；'), money(order.pending_net_amount,order.currency)])}</p>`).join('');
   const invoiceSource=frm.doctype==='Purchase Receipt' && frm.doctype===chain.source_doctype;
   if(reversalBlocked(reversal || chain.reversal)){chain.can_create=false;chain.can_prepay=false;chain.can_create_invoice=false;chain.draft_invoices=[];}
   const invoiceActions=invoiceSource?`${(chain.draft_invoices || []).map(i=>`<button class="btn btn-default btn-sm dlp-receipt-invoice" data-name="${esc(current)}" data-source-doctype="${esc(frm.doctype)}" data-target="${esc(i.name || i)}">${t("继续应付草稿")}</button>`).join(' ')} ${chain.can_create_invoice?`<button class="btn btn-default btn-sm dlp-receipt-invoice" data-name="${esc(current)}" data-source-doctype="${esc(frm.doctype)}">${t("确认应付")}</button>`:''}`:'';
   const box=$(`<section class="dlp-purchase-chain"><strong>${t("采购关联与付款")}</strong><p>${chain.orders.map(n=>link('Purchase Order',n)).join(' · ')}</p>${progress}<div>${balanceHTML(chain)}</div>${(chain.warnings || []).map(w=>`<p class="text-warning">${esc(w)}</p>`).join('')}<p>${chain.invoices.map(i=>`${link('Purchase Invoice',i.name)} ${i.docstatus===0?t("草稿"):i.docstatus===2?t("已取消"):''}`).join(' · ') || esc(chain.reason)}</p><div><button type="button" class="btn btn-default btn-sm dlp-records">${t("采购付款记录")}</button> ${invoiceActions} ${frm.doctype==='Purchase Receipt' && chain.can_create?`<button class="btn btn-primary btn-sm dlp-pay">${esc(t('付款 / 继续付款'))}</button>`:''} ${frm.doctype==='Purchase Order'?orderReceiptAction({...frm.doc,reversal:reversal || chain.reversal,receipt_eligibility:chain.receipt_eligibility}):''}</div>${chain.payments.length?`<details><summary>${t("关联付款与凭证（最多 100 条）")}</summary>${chain.payments.map(row=>`<p>${link('Payment Entry',row.name)} · ${esc(row.state)} · ${money(row.amount,row.currency)}<br>${referencesHTML(row.references)}<br>${vouchersHTML(row.vouchers)}</p>`).join('')}</details>`:''}</section>`).prependTo(frm.layout.wrapper);
   box.find('.dlp-pay').on('click.dlpForm',()=>pay(frm.doctype,current));
   box.find('.dlp-records').on('click.dlpForm',()=>recordsRoute(chain));
  }catch(error){/* Native form remains available without finance read permissions. */}
 }
 const recordColumns=[['posting_date','付款日期',96],['name','付款单号',165],['supplier','供应商',165],['company','公司',140],['payment_type','类型',65],['amount','整单金额',140],['bank_account','记账账户',165],['state','付款状态',155],['references','采购关联 / 分配币种',290],['vouchers','财务凭证 / 状态',240],['sync_issues','同步提示',145],['action','操作',100]].map(([fieldname,label,width])=>({fieldname,label,width}));
 function recordsRequest(c){const sort=c.providerOrderBy.replace(/^supplier\b/,'party').replace(/^state\b/,'docstatus');return {method:API+'get_payment_records',args:{...c.quick,native_filters:JSON.stringify(c.nativeFilterGroup?.get_filters() || []),or_filters:JSON.stringify(c.orFilterGroup?.get_filters() || []),start:c.page*c.pageSize,page_length:c.pageSize,order_by:sort}};}
 function mountRecordsFilters(c){
  const change=()=>{if(c.resetting)return;c.setPage(0);c.refresh();};
  for(const [key,label] of [['nativeFilterGroup','全部条件（AND）'],['orFilterGroup','任一条件（OR）']]){
   const button=$(`<button type="button" class="btn btn-default btn-sm filter-box" aria-expanded="false"><span class="filter-icon"></span>${esc(t(label))} <span class="button-label"></span></button>`).appendTo(c.$advancedFilters || c.$toolbar);
   const parent=$(`<section class="dlp-payment-native-filters" aria-label="${esc(t(label))}"></section>`).appendTo(c.$advancedFilters || c.$filters).hide();
   const group=new frappe.ui.FilterGroup({doctype:'Payment Entry',parent,on_change:change});
   // Native inline FilterGroup avoids its single global popover assumption and
   // document handlers. Only the reveal/close shell is page-scoped.
   group.filter_button=button;
   group.hide_popover=()=>{parent.hide();button.attr('aria-expanded','false');group.update_filters();};
   button.on('click.dlpRecords',()=>{if(parent.is(':visible'))group.hide_popover();else{parent.show();button.attr('aria-expanded','true');if(!group.filters.length)group.add_filter('Payment Entry','name');}});
   const validate=group.validate_args.bind(group),push=group._push_new_filter.bind(group);
   // Native metadata/permlevel selection stays authoritative. The service only
   // supports parent PE fields; do not offer child fields it would reject.
   group.validate_args=(doctype,field)=>{if(doctype!=='Payment Entry' || !c.nativeAllowed.has(field)){frappe.msgprint({message:t("此字段不在当前付款记录的可读筛选范围内。"),indicator:'orange'});return false;}return validate(doctype,field);};
   group._push_new_filter=(...args)=>{const filter=push(...args),select=filter?.fieldselect;if(select){select.options=select.options.filter(option=>option.doctype==='Payment Entry' && c.nativeAllowed.has(option.fieldname));select.awesomplete.list=select.options;}return filter;};
   c[key]=group;
  }
  c.resetAdvancedFilters=()=>{c.nativeFilterGroup.clear_filters();c.orFilterGroup.clear_filters();};
 }
 async function recordsExport(c){const args={...recordsRequest(c).args,export_format:'xlsx',columns:JSON.stringify(c.preferences.columns.filter(field=>field!=='action'))};delete args.start;delete args.page_length;if(!root.DeepLinkERPPurchaseOrderExport?.downloadWorkbook)await frappe.require('/assets/deeplinkerp_branding/js/purchase_order_export.js');const exporter=root.DeepLinkERPPurchaseOrderExport;if(!exporter)throw new Error(t("导出组件加载失败，请刷新。"));exporter.downloadWorkbook(root,await exporter.fetchNativeWorkbook(root,args,API+'get_payment_records'),'采购付款记录');}
 function recordsPage(wrapper){
  const page=frappe.ui.make_app_page({parent:wrapper,title:t("采购付款记录"),single_column:true});
  const provider={columns:recordColumns,defaultColumns:['name','supplier','posting_date','company','payment_type','amount','bank_account','state','action'],preferenceVersion:4,migratePreferences:value=>value?.version===4?value:{...value,columns:provider.defaultColumns},freezeUntil:'supplier',formLink:doc=>`/desk/payment-entry/${encodeURIComponent(doc.name)}`,request:recordsRequest,exportCurrent:recordsExport,summary:c=>paymentSummary(c.providerPayload || {}),sortFields:['posting_date','name','company','supplier','payment_type','state'],
   renderValue:(field,doc)=>{if(field==='supplier')return link('Supplier',doc.supplier);if(field==='amount')return money(doc.amount,doc.currency);if(field==='payment_type')return doc.payment_type==='Receive'?'退款':'付款';if(field==='references')return referencesHTML(doc.references);if(field==='vouchers')return vouchersHTML(doc.vouchers);if(field==='state')return `${esc(doc.state)}${(doc.warnings || []).length?` <span title="${esc(doc.warnings.join('；'))}">⚠</span>`:''}`;if(field==='sync_issues')return(doc.sync_issues || []).map(issue=>`${link('China Voucher Sync Issue',issue.name,'凭证待同步')} · ${esc(state(issue.status))}`).join('；') || '—';if(field==='action')return `<button type="button" class="btn btn-default btn-xs dlp-payment-preview" data-name="${esc(doc.name)}">${doc.can_edit?t("编辑草稿"):doc.allowed_actions?.length?t("预览 / 提交"):t("预览")}</button>`;},
   mountControls:mountRecordsFilters,
   onPayload:c=>{c.$providerNotice?.remove();c.$providerNotice=$('<p class="text-muted dlp-po-provider-notice"></p>').text(c.providerPayload.notice || '').appendTo(c.$advancedFilters);},
  };
  const fieldMap={supplier:'party',state:'docstatus',amount:'paid_amount',bank_account:'paid_from',references:'references',purchase_order:'references',purchase_receipt:'references',from_date:'posting_date',to_date:'posting_date',status:'docstatus'};
  const grid=root.DeepLinkERPCompactList.create({doctype:'Payment Entry',purchaseChrome:true,backupMigratedPreferences:true,defaultColumns:provider.defaultColumns,preferenceVersion:provider.preferenceVersion,migratePreferences:provider.migratePreferences,mainFilterFields:['search','company','status','from_date','to_date'],dismissInitialOnboarding:true,pageRoute:'purchase-payment-records',routeClass:'dlp-purchase-payment-grid-active',columns:recordColumns,provider,pageFieldMap:fieldMap,numbers:['amount'],dates:['posting_date'],controls:[['search','Data',null,'付款单号 / 供应商'],['company','Link','Company','公司'],['supplier','Link','Supplier','供应商'],['purchase_order','Link','Purchase Order','采购订单'],['purchase_receipt','Link','Purchase Receipt','采购入库'],['from_date','Date',null,'付款开始日期'],['to_date','Date',null,'付款结束日期'],['status','Select','\nDraft\nSubmitted\nCancelled','单据状态']].map(([fieldname,fieldtype,options,label])=>({fieldname,fieldtype,options,label,permission_field:fieldMap[fieldname]})),optionLabels:{status:{Draft:'草稿',Submitted:'已提交',Cancelled:'已取消'}}});
  const ready=frappe.model.with_doctype('Payment Entry').then(()=>{const c=grid.mountPage(page,root);recordsController=c;wrapper.dlpRecordsController=c;return c;});
  ready.catch(()=>page.main.append(`<p class="text-danger">${t("付款元数据读取失败，请刷新或核对权限。")}</p>`));
  wrapper.dlpLoad=async()=>{const c=await ready;c.activate();if((frappe.get_route?.() || [])[0]!=='purchase-payment-records')return;const options=frappe.route_options || {};frappe.route_options=null;if(Object.keys(options).length){c.resetting=true;await Promise.all(Object.entries(c.controls).map(([field,control])=>control.set_value(options[field] || '')));c.quick={...options};c.resetting=false;c.setPage(0);}return c.refresh();};
  page.set_secondary_action(t("刷新"),async()=>{const c=await ready;return c.refresh();});
 }
 // Exactly one delegated click handler per loaded resource; cached pages reuse it.
 if(root.document && !root.__dlpProcurementActionsInstalled){
  root.__dlpProcurementActionsInstalled=true;
  frappe.realtime?.on('purchase_reversal_progress',event=>{
   const frm=root.cur_frm,controller=currentController();
   if(frm?.dlpReversal?.operation_id===event.operation_id)Promise.resolve(frm.reload_doc()).catch(()=>{});
   else if(controller)Promise.resolve(controller.refresh()).catch(()=>{});
  });
  root.document.addEventListener('click',event=>{const button=event.target.closest?.('.dlp-receipt-pay,.dlp-receipt-invoice,.dlp-order-receipt,.dlp-payment-preview,.dlp-voucher-preview,.dlp-native-document,.dlp-crossborder-open');if(!button)return;if(button.tagName==='A' && (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button))return;event.preventDefault();event.stopPropagation();const name=button.dataset.name;if(button.classList.contains('dlp-crossborder-open'))root.DeepLinkERPCrossborderProcurement?.open(button.dataset.crossborderOrder,button.dataset.crossborderTab,root.cur_list?.dlpPurchaseOrderGrid);else if(button.classList.contains('dlp-native-document'))openNative(button.dataset.doctype,name);else if(button.classList.contains('dlp-receipt-pay'))pay('Purchase Receipt',name);else if(button.classList.contains('dlp-receipt-invoice'))documentDrawer(button.dataset.sourceDoctype || 'Purchase Receipt',name,'Purchase Invoice',button.dataset.target || null);else if(button.classList.contains('dlp-order-receipt'))documentDrawer('Purchase Order',name,'Purchase Receipt');else if(button.classList.contains('dlp-payment-preview'))paymentDrawer(name);else voucherDrawer(name);},true);
  frappe.router?.on('change',()=>{if(activeDrawer?.guardNavigation)return;gate.cancel();activeDrawer?.close(true);});
 }
 function mountProcurementTabs(c) {
  const route=c.root.frappe.get_route?.() || [], current=route[0]==='List'?route[1]:route[0];
  const entries=[['Purchase Order','purchase-order','采购订单'],['Purchase Receipt','purchase-receipt','采购入库'],['purchase-payables','purchase-payables','采购应付'],['purchase-payment-records','purchase-payment-records','采购付款记录']];
  const finance=Boolean(c.root.frappe.model?.can_read?.('Payment Entry'));
  c.$procurementTabs?.remove();
  c.$procurementTabs=c.root.$(`<nav class="dlp-procurement-tabs" aria-label="${esc(t('采购流程'))}">${entries.map(([key,path,label])=>!finance && key.startsWith('purchase-')?`<button type="button" disabled title="${esc(t('财务办理入口；请在订单查看进度'))}">${esc(t(label))}</button>`:`<a class="${key===current?'active':''}" href="/desk/${path}?sidebar=Buying" ${key===current?'aria-current="page"':''}>${esc(t(label))}</a>`).join('')}</nav>`).insertBefore(c.list.$result.parent('.result-container'));
 }
 root.DeepLinkERPPurchasePayments={pay,batchPay,batchDocumentDrawer,formRefresh,recordsPage,balanceHTML,documentDrawer,paymentDrawer,voucherDrawer,orderReceiptAction,nativeAction,openNative,vouchersHTML,paymentSummary,editSession,requestId,retryToken,operationGate,recordsRequest,recordsExport,receiptDrafts,mountRecordsFilters,disposeControls,mountProcurementTabs,mountAttachments,createDrawer:newDrawer,drawerBody:body,drawerInput:input,loadMetadata,reversalHTML,reversalBlocked,refreshReversal,cancelCrossborderForList:c=>root.DeepLinkERPCrossborderProcurement?.cancelForList(c),formatMoney:money,formatQuantity:quantity,formatNumericInput};
})(typeof globalThis!=='undefined'?globalThis:this);
