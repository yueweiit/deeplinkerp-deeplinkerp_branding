(function(root){
 'use strict';
 if(root.DeepLinkERPCrossborderProcurement)return;
 const SERVICE='deeplinkerp_branding.services.purchase_fulfilment_service.';
 const PROGRESS='deeplinkerp_branding.services.purchase_order_progress.get_order_progress';
 const LINK='Purchase Fulfilment Link',frappe=root.frappe,$=root.$;
 const t = (value, args = []) => String((root.__ || (text => text))(value)).replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");
 const esc=value=>String(value ?? '').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
 const flows={external_internal:'外部采购 / 内部结算',internal_local:'本地内部采购',trade_custody:'贸易品保管（无内部应付）'};
 const nodes={domestic_dispatch:'国内发运',international_dispatch:'国际发运',customs:'清关',reported_arrival:'报告到货（非 ERP 入库）',review:'待核对',evidence:'补充证据'};
 const associationFields=new Set(['external_item','beneficiary_company','flow_kind','internal_order','internal_item','allocated_qty','seller_order','seller_item','cost_batch','custody_company','custody_warehouse']);
 const noteField={native_doctype:'Purchase Order',native_fieldname:'terms',fieldtype:'Small Text'};
 const options=rows=>[{value:'',label:''},...rows.map(([value,label])=>({value,label:esc(t(label))}))];
 const itemLabel=item=>[item.name,item.item_code,item.item_name,item.uom].filter(Boolean).join(' · ');
 const warnings=rows=>[...new Set((rows || []).filter(Boolean))].map(row=>`<p class="text-warning">${esc(t(row))}</p>`).join('');
 const call=async(method,args)=>(await frappe.call({method:SERVICE+method,args,silent:true,type:'POST'})).message;
 const knownRefusal=error=>/ValidationError|LinkStateConflict|PermissionError|DoesNotExistError|MandatoryError|AuthenticationError/.test(error?.exc_type || error?.responseJSON?.exc_type || error?.exception || error?.responseJSON?.exception || '');
 const uncertain=new Set();let owned=null;
 function uncertaintyKey(order){return `dlp-crossborder-create:${frappe.boot?.sitename || root.location?.host}:${frappe.session?.user}:${order}`;}
 function markUncertain(order){uncertain.add(order);try{root.sessionStorage?.setItem(uncertaintyKey(order),'1');}catch(_){/* Session recovery is optional. */}}
 function clearUncertain(order){uncertain.delete(order);try{root.sessionStorage?.removeItem(uncertaintyKey(order));}catch(_){/* The local guard is still cleared for a confirmed outcome. */}}
 function isUncertain(order){try{if(root.sessionStorage?.getItem(uncertaintyKey(order)))uncertain.add(order);}catch(_){/* The in-memory guard remains active. */}return uncertain.has(order);}
 function cancelForList(c){
  if(owned?.c!==c)return;
  const drawer=owned.drawer;
  owned=null;
  // The shared close method cancels its operation gate. Never call it again
  // after a user close: a later payment drawer may already own that gate.
  if(drawer.alive())drawer.close(true);
 }
 async function open(order,tab='internal',c=root.cur_list?.dlpPurchaseOrderGrid){
  if(!order || !c || root.cur_list!==c.list)return;
  if(owned?.drawer.alive()){if(owned.order===order && !owned.drawer.busy)return owned.switchTab(tab);return;}
  const shell=root.DeepLinkERPPurchasePayments,drawer=shell.createDrawer(t("采购履行核对"),true);if(!drawer)return;
  let progress,selected=null,details=new Map(),externalItems=new Map(),nativeItems=new Map(),controls=new Map(),values={},session,roleGeneration=0,sellerGeneration=0,candidatesReady=false,candidateIndex=new Map(),warehouseIds=new Set(),seller=null,reconciliation=null;
  const candidateCache=new Map(),linkActions=new Map(),requestId=c.requestId;
  const isOrderList=()=>{const route=frappe.get_route?.() || [];return route[0]==='List' && route[1]==='Purchase Order' && (!route[2] || route[2]==='List');};
  // The list's existing cancelPending hook cancels refresh/filter/sort requests.
  // Request identity and drawer load identity are constant-time guards here.
  const current=generation=>drawer.alive() && generation===drawer.loadId && root.cur_list===c.list && c.requestId===requestId && isOrderList();
  const canWrite=()=>Boolean(frappe.model?.can_write?.('Purchase Order'));
  const detail=()=>details.get(selected);
  const tabs=()=>`<nav class="dlp-cross-tabs" role="tablist" aria-label="${esc(t('采购履行'))}">${[['internal','内部关联'],['logistics','物流证据']].map(([key,label])=>`<button type="button" class="btn btn-default dlp-cross-tab${tab===key?' active':''}" role="tab" aria-selected="${tab===key}" data-tab="${key}">${esc(t(label))}</button>`).join('')}</nav>`;
  const uncertaintyHTML=()=>isUncertain(order)?`<p class="text-warning" role="alert">${t("新建关联响应不确定，请刷新订单并核对现有关联。本次新建已锁定，请确认结果后再办理，勿重复建立关联。")}</p>`:'';
  function bindTabs(){drawer.panel.find('.dlp-cross-tab').off('.dlpCrossborder').on('click.dlpCrossborder',event=>{if(drawer.alive() && !drawer.busy)return switchTab(event.currentTarget.dataset.tab);});}
  function clearActions(){drawer.panel.find('footer .dlp-cross-action').remove();}
  function action(name,label,fn,generation,primary=false){
   return $(`<button type="button" class="btn ${primary?'btn-primary':'btn-default'} dlp-cross-action ${name}">${esc(t(label))}</button>`).appendTo(drawer.panel.find('footer')).on('click.dlpCrossborder',()=>{if(current(generation) && !drawer.busy)return fn();});
  }
  function syncBlocked(){
   drawer.panel.find('.dlp-cross-save').prop('disabled',drawer.busy || (!selected && isUncertain(order)));
   drawer.panel.find('.dlp-cross-confirm-price').prop('disabled',drawer.busy || Boolean(session?.dirty()));
   drawer.panel.find('.dlp-cross-reconcile').prop('hidden',!isUncertain(order));
  }
  async function add(field,label,initial,generation,config={}){
   if(!current(generation))return null;
   const {holder='.dlp-cross-fields',native_doctype=LINK,native_fieldname=field,numeric=false,...presentation}=config;
   values[field]=initial ?? (numeric?null:'');
   const touched=(value,raw)=>{
    if(!current(generation))return;
    values[field]=numeric && raw===''?null:value;
    if(session && associationFields.has(field)){
     session.touch(field,values[field]);
     updatePrice();
     syncBlocked();
    }
   };
   const nativeLink=presentation.fieldtype==='Link' && !numeric;
   // Link selection signals native df.change after validation, not a DOM
   // change. Do not attach shell raw-input listeners that capture partial IDs.
   const control=await shell.drawerInput(drawer,$('<div></div>').appendTo(drawer.panel.find(holder)),{native_doctype,native_fieldname,fieldname:field,label,read_only:false,only_select:1,...presentation},initial,nativeLink?undefined:touched);
   if(!current(generation)){shell.disposeControls([control]);return null;}
   if(initial==null || initial==='')control.$input?.val('');
   if(nativeLink){
    const nativeChanged=control.df.change || control.df.onchange;
    control.df.change=async function(...args){
     if(!current(generation) || drawer.busy || control.df.read_only)return;
     const value=control.value ?? '';
     if(nativeChanged)await nativeChanged.apply(this,args);
     if(!current(generation) || drawer.busy || (control.value ?? '')!==value || values[field]===value)return;
     touched(value);
     return linkActions.get(field)?.();
    };
   }
   if(control.df.read_only && control.df.fieldtype==='Select' && Array.isArray(control.df.options) && control.disp_area && control.set_disp_area){
    // Native readonly Select formats the raw value. Reuse the already escaped
    // option labels locally; never change native value/get_value or identity.
    const labels=new Map(control.df.options.map(option=>[String(option.value),option.label]));
    const renderDisplay=control.set_disp_area.bind(control);
    control.set_disp_area=value=>{
     if(!current(generation))return;
     renderDisplay(value);
     const label=labels.get(String(control.value ?? value ?? ''));
     if(label!==undefined)$(control.disp_area).html(label);
    };
    control.set_disp_area(control.value ?? initial);
   }
   controls.set(field,control);return control;
  }
  async function choose(field,choices,value,generation){
   const control=controls.get(field);if(!control || !current(generation))return;
   control.df.options=options(choices);control.set_options?.();await control.set_value(value || '');
   if(current(generation))values[field]=value || '';
  }
  function nativePrice(native,item,confirmation={}){
   if(!native)return `<p class="text-muted">${t("请明确选择原生内部订单及明细后核对实际价格。")}</p>`;
   const stale=Boolean(confirmation.version && (confirmation.currency!==native.currency || confirmation.native_item && confirmation.native_item!==item?.name || detail()?.source_versions?.internal_modified!==detail()?.internal_modified || (confirmation.seller_modified || null)!==(detail()?.seller_modified || null)));
   const pending=Boolean(session?.dirty());
   const status=pending?'关联修改未保存，原生价格确认待重新核对；请先保存并重新打开':stale?'原生价格 / 订单版本已变化，请重新确认':confirmation.version?'已记录原生价格确认':'原生价格尚未确认';
   return `<div class="dlp-cross-native-price"><p>${shell.nativeAction('Purchase Order',native.name,t("查看原生内部订单"))} · ${esc(native.company)} · ${native.docstatus===0?t("报价草稿不形成应付"):native.docstatus===1?t("原生订单已提交；应付以原生已提交发票为准"):t("原生订单已取消")}</p><p>${t("实际原生单价（只读）：{0}{1}", [item?shell.formatMoney(item.rate,native.currency):'明细待选择', item?` · ${esc(item.uom)} · <span title="${esc(item.rate)}">${t("原值")} ${esc(item.rate)}</span>`:''])}</p><p class="${confirmation.version && !stale && !pending?'text-success':'text-warning'}">${esc(t(status))}${confirmation.version?` · ${esc(confirmation.by)} · ${esc(confirmation.on)}`:''}</p></div>`;
  }
  function updatePrice(){const native=selected?detail()?.native_internal:candidateIndex.get(values.internal_order)?.doc,item=selected?nativeItems.get(selected)?.get(values.internal_item):candidateIndex.get(values.internal_order)?.items.get(values.internal_item);drawer.panel.find('.dlp-cross-price').html(nativePrice(native,item,selected?detail()?.price_confirmation:{}));}
  function updateUnit(){drawer.panel.find('.dlp-cross-unit').text(t("原生采购单位：{0}", [externalItems.get(values.external_item)?.uom || '待选择明细']));}
  async function candidates(generation){
   const role=++roleGeneration;candidateIndex=new Map();warehouseIds=new Set();candidatesReady=false;
   const {beneficiary_company,flow_kind,custody_company}=values;
   if(!beneficiary_company || (flow_kind==='trade_custody' && !custody_company))return;
   const key=JSON.stringify([beneficiary_company,flow_kind,custody_company || null]);
   if(!candidateCache.has(key))candidateCache.set(key,call('get_link_candidates',{external_order:order,beneficiary_company,flow_kind,custody_company:custody_company || null}));
   try{
    const result=await candidateCache.get(key);if(!current(generation) || role!==roleGeneration)return;
    candidateIndex=new Map((result.purchase_orders || []).map(doc=>[doc.name,{doc,items:new Map((doc.items || []).map(item=>[item.name,item]))}]));warehouseIds=new Set(result.warehouses || []);candidatesReady=true;
    const missing=flow_kind==='trade_custody'?!warehouseIds.size:!candidateIndex.size;
    drawer.panel.find('.dlp-cross-candidates-warning').html(warnings([...(result.warnings || []),...(missing?[flow_kind==='trade_custody'?'无匹配的原生保管仓库，请核对公司与权限。':'无匹配的原生内部订单，请先核对内部 Supplier、公司与权限。']:[])]));
    await choose('internal_order',[...candidateIndex.values()].map(({doc})=>[doc.name,`${doc.name} · ${doc.company} · ${doc.currency}`]),values.internal_order,generation);
    await choose('custody_warehouse',[...warehouseIds].map(name=>[name,name]),values.custody_warehouse,generation);
   }catch(error){if(current(generation) && role===roleGeneration)drawer.panel.find('.dlp-cross-candidates-warning').html(warnings(['候选读取受限或主数据尚未配置，请核对原生公司、内部 Supplier 和权限。']));}
  }
  async function readSeller(generation){
   const read=++sellerGeneration,name=values.seller_order;seller=null;await choose('seller_item',[],'',generation);if(!name)return;
   try{const result=(await frappe.call({method:'frappe.client.get',args:{doctype:'Sales Order',name},silent:true})).message;if(!current(generation) || read!==sellerGeneration || values.seller_order!==name)return;
    if(result?.name!==name || !result.modified)throw new Error(t("原生销售订单版本不可见"));
    seller={doc:result,items:new Map((result.items || []).map(item=>[item.name,item]))};await choose('seller_item',[...seller.items.values()].map(item=>[item.name,`${item.name} · ${item.item_code} · ${item.uom}`]),'',generation);
   }catch(error){if(current(generation) && read===sellerGeneration)drawer.error(new Error(t("原生销售订单或明细无权读取，请核对已有单据；不会自动生成销售单。")));}
  }
  function changed(control,fn,generation){
   if(control?.df.fieldtype==='Link'){linkActions.set(control.df.fieldname,fn);return;}
   control?.$input?.on('change.dlpCrossborder',async()=>{if(current(generation) && !drawer.busy)await fn();});
  }
  function versions(){const row=detail();return {expected_modified:progress.modified,expected_internal_modified:row?.internal_modified || candidateIndex.get(values.internal_order)?.doc.modified || null,expected_seller_modified:row?.seller_modified || seller?.doc.modified || null,...(row?{name:row.name,expected_link_modified:row.modified}:{})};}
  async function mutate(method,args,generation,creating=false){
   if(!current(generation) || drawer.busy || !canWrite() || (creating && isUncertain(order)))return;
   // Native Link validation is asynchronous. Never submit an older validated
   // selection while input intent or its post-validation synchronization differs.
   for(const [field,control] of controls){
    if(control.df.fieldtype!=='Link' || control.df.read_only)continue;
    const validated=control.value ?? '';
    if(control.inside_change_event || (control.get_input_value() ?? '')!==validated || (values[field] ?? '')!==validated){
     drawer.error(new Error(t("请等待字段验证完成，或重新选择有效记录后再次点击。")));return;
    }
   }
   if(['save_link','confirm_native_price'].includes(method) && detail()?.external_modified && detail().external_modified!==progress.modified){drawer.error(new Error(t("来源订单版本已变化，请刷新后重新核对明细及价格。")));return;}
   drawer.setBusy(true);let acknowledged=false;if(creating)markUncertain(order);
   try{
    const result=await call(method,args);if(!result?.name || !result.modified)throw new Error(t("操作响应未确认"));acknowledged=true;if(!current(generation))return;
    if(creating)clearUncertain(order);drawer.close(true);if(owned?.drawer===drawer)owned=null;c.invalidatePurchaseDetails?.([order]);
    frappe.show_alert?.({message:esc(t(method==='save_link'?'关联已保存，请重新打开并确认原生价格。':'核对记录已保存，请重新打开读取最新版本。'))+(result.warnings?.length?` ${esc(result.warnings.join('；'))}`:''),indicator:result.warnings?.length?'orange':'green'});
    await c.refresh();
   }catch(error){
    if(creating && current(generation) && knownRefusal(error))clearUncertain(order);
    if(acknowledged){if(root.cur_list===c.list && isOrderList())frappe.show_alert?.({message:t('记录已保存，列表刷新失败。请刷新订单核对。'),indicator:'orange'});}
    else if(current(generation)){if(creating && !knownRefusal(error))drawer.error(new Error(t("响应不确定，请刷新订单并核对现有关联；新建已锁定，勿重复建立关联。")));else drawer.error(new Error(knownRefusal(error)?error.message || t("操作被拒绝，请核对权限、版本或原生单据。"):t("操作未确认，请刷新后核对原生记录与版本。")));}
   }
   finally{if(current(generation)){drawer.setBusy(false);syncBlocked();}}
  }
  function save(generation){
   const row=detail(),external=externalItems.get(values.external_item),internal=row?.native_internal || candidateIndex.get(values.internal_order)?.doc;
   const changes=session.changes(),qty=Object.hasOwn(changes,'allocated_qty')?changes.allocated_qty:session.document.allocated_qty;
   if(row?.warnings?.some(warning=>warning.includes('海外成本物流能力尚未安装或不可用'))){drawer.error(new Error(t("物流成本批次当前不可见，不能改写关联；请恢复来源能力后核对，可保留独立手工节点。")));return;}
   if(qty==null || qty==='' || !Number.isFinite(Number(qty)) || Number(qty)<=0){drawer.error(new Error(t("请输入有限正数关联数量；空值不等于零。")));return;}
   if(!external || !external.uom || !values.beneficiary_company || !flows[values.flow_kind]){drawer.error(new Error(t("请明确选择最终公司和原生采购明细，核对数量及单位。")));return;}
   if(row && ['external_item','internal_order','internal_item','seller_order','seller_item'].some(field=>(row[field] || '')!==(values[field] || ''))){drawer.error(new Error(t("关联来源 / 明细身份不能改写，请填写原因停用后新建关联。")));return;}
   if(values.flow_kind==='trade_custody'){
    if(values.beneficiary_company!==progress.external.company || !values.custody_company || (!row && (!candidatesReady || !warehouseIds.has(values.custody_warehouse)))){drawer.error(new Error(t("贸易保管最终公司须为实际采购公司，并明确选择授权保管公司及仓库。")));return;}
   }else if(!internal || internal.company!==values.beneficiary_company || !(row?nativeItems.get(selected):candidateIndex.get(values.internal_order)?.items)?.has(values.internal_item) || (!row && !candidatesReady)){
    drawer.error(new Error(t("请选择服务器候选中的准确原生内部订单与明细；身份变更请停用后新建。")));return;
   }
   if(!row && values.seller_order && (!seller || seller.doc.name!==values.seller_order || !seller.items.has(values.seller_item))){drawer.error(new Error(t("请明确选择已有原生销售订单与准确明细 ID。")));return;}
   const optional=field=>values[field] || null;
   return mutate('save_link',{external_order:order,external_item:values.external_item,purchasing_company:progress.external.company,beneficiary_company:values.beneficiary_company,flow_kind:values.flow_kind,allocated_qty:qty,internal_order:values.flow_kind==='trade_custody'?null:optional('internal_order'),internal_item:values.flow_kind==='trade_custody'?null:optional('internal_item'),seller_order:optional('seller_order'),seller_item:optional('seller_item'),cost_batch:optional('cost_batch'),custody_company:optional('custody_company'),custody_warehouse:optional('custody_warehouse'),...versions()},generation,!row);
  }
  async function renderInternal(generation){
   const row=detail(),readonly=!canWrite();values={};session=shell.editSession({document:{allocated_qty:row?.allocated_qty ?? null}});candidateIndex=new Map();seller=null;candidatesReady=Boolean(row);
   const costAvailable=Boolean(frappe.model?.can_read?.('Overseas Cost Batch'));
   if(!costAvailable){
    drawer.panel.find('.dlp-cross-cost-warning').html(warnings(['国际物流成本批次未安装或无权读取，当前不可选择；不绑定成本仍可核对。'])+(row?.cost_batch?`<p class="text-muted">${t("保留已有批次：{0}", [esc(row.cost_batch)])}</p>`:''));
   }
   const internalItem=nativeItems.get(selected)?.get(row?.internal_item);
   const fields=[
    ['purchasing_company','实际采购付款公司',progress.external.company,{read_only:true,fieldtype:'Link',options:'Company'}],
    ['beneficiary_company','最终使用 / 销售公司',row?.beneficiary_company || '',{read_only:readonly || Boolean(row),fieldtype:'Link',options:'Company',reqd:1}],
    ['flow_kind','履行关系',row?.flow_kind || 'external_internal',{read_only:readonly || Boolean(row),fieldtype:'Select',options:options(Object.entries(flows))}],
    ['external_item','外部原生采购明细 ID',row?.external_item || '',{read_only:readonly || Boolean(row),fieldtype:'Select',options:options([...externalItems.values()].map(item=>[item.name,itemLabel(item)]))}],
    ['internal_order','内部原生采购订单',row?.internal_order || '',{read_only:readonly || Boolean(row),fieldtype:'Select',options:options(row?.internal_order?[[row.internal_order,row.internal_order]]:[])}],
    ['internal_item','内部原生采购明细 ID',row?.internal_item || '',{read_only:readonly || Boolean(row),fieldtype:'Select',options:options(row?.internal_item?[[row.internal_item,internalItem?itemLabel(internalItem):row.internal_item]]:[])}],
    ['allocated_qty','本次关联数量',row?.allocated_qty ?? null,{read_only:readonly,native_doctype:'Purchase Order Item',native_fieldname:'qty',numeric:true,reqd:1}],
    ['seller_order','已有内部销售订单（可选）',row?.seller_order || '',{read_only:readonly || Boolean(row),fieldtype:'Link',options:'Sales Order'}],
    ['seller_item','已有内部销售明细 ID（可选）',row?.seller_item || '',{read_only:readonly || Boolean(row),fieldtype:'Select',options:options(row?.seller_item?[[row.seller_item,row.seller_item]]:[])}],
    ['cost_batch','已有国际物流成本批次（可选）',row?.cost_batch || '',{read_only:readonly,fieldtype:'Link',options:'Overseas Cost Batch'}],
    ['custody_company','保管公司（贸易品保管）',row?.custody_company || '',{read_only:readonly,fieldtype:'Link',options:'Company'}],
    ['custody_warehouse','保管原生仓库（贸易品保管）',row?.custody_warehouse || '',{read_only:readonly,fieldtype:'Select',options:options(row?.custody_warehouse?[[row.custody_warehouse,row.custody_warehouse]]:[])}],
   ];
   for(const [field,label,value,config] of fields){
    // Without native read capability, do not construct/focus/query this Link.
    // Retain the authorized loaded value; the hidden-binding save guard stays.
    if(field==='cost_batch' && !costAvailable){values[field]=value;continue;}
    await add(field,label,value,generation,config);if(!current(generation))return;
   }
   for(const name of ['beneficiary_company','flow_kind','custody_company'])changed(controls.get(name),async()=>{if(row && name!=='custody_company')return;values.internal_order=row?.internal_order || '';values.internal_item=row?.internal_item || '';values.custody_warehouse='';await choose('internal_item',[],values.internal_item,generation);await candidates(generation);updatePrice();},generation);
   changed(controls.get('external_item'),()=>updateUnit(),generation);
   changed(controls.get('internal_order'),async()=>{if(row)return;await choose('internal_item',[...(candidateIndex.get(values.internal_order)?.items.values() || [])].map(item=>[item.name,`${item.name} · ${item.item_code} · ${item.uom}`]),'',generation);updatePrice();},generation);
   changed(controls.get('internal_item'),()=>updatePrice(),generation);changed(controls.get('seller_order'),()=>row?undefined:readSeller(generation),generation);
   updatePrice();updateUnit();if(!current(generation))return;
   if(!readonly){action('dlp-cross-save','保存关联',()=>save(generation),generation,true);if(row && row.native_internal && row.flow_kind!=='trade_custody')action('dlp-cross-confirm-price','确认实际原生价格',()=>{if(session.dirty()){drawer.error(new Error(t("请先保存关联并重新打开，再确认实际原生价格。")));return;}return mutate('confirm_native_price',{name:row.name,...versions(),expected_link_modified:row.modified},generation);},generation);
    if(row){await add('disable_reason','停用原因','',generation,{...noteField,holder:'.dlp-cross-disable'});if(!current(generation))return;action('dlp-cross-disable-link','停用关联',()=>{const reason=String(values.disable_reason || '').trim();if(!reason){drawer.error(new Error(t("请填写停用原因；保留审计记录，不删除关联。")));return;}return mutate('disable_link',{name:row.name,expected_link_modified:row.modified,reason},generation);},generation);}
   }
  }
  function logisticsHTML(row){
   if(!row)return `<p class="text-muted">${t("请先在内部关联中明确保存关联，再记录物流证据。")}</p>`;
   const snapshot=row.snapshot || {},context=snapshot.source_context || {};
   return `<section class="dlp-cross-logistics"><h4>${t("原始物流证据（只读）")}</h4><p class="text-warning">${t("物流评论及手工报告均不更新库存，也不确认付款或应付。预计、否定、含糊和冲突评论需人工核对。")}</p>${warnings([...(row.warnings || []),...(snapshot.warnings || [])])}<p>${t("来源：{0} · {1} · {2} · {3} · {4}", [esc(context.root_source_id || '尚未关联国际物流来源'), esc(context.corp_id), esc(context.instance_id), esc(context.source_snapshot), esc(snapshot.source_updated_at)])}</p>${(snapshot.timeline || []).map(entry=>{const raw=entry.raw || {};return `<article class="dlp-cross-proof"><p>${esc(entry.source_id)} · ${esc(raw.user_name || raw.user_id)} · ${esc(raw.operation_time)} · ${esc(entry.state==='reported'?'物流报告（非 ERP 入库）':'待核对')}</p><p class="dlp-source-text">${esc(raw.remark)}</p>${entry.quantity?`<p>${shell.formatQuantity(entry.quantity.qty)} ${esc(entry.quantity.uom)} · ${esc(entry.quantity.destination)}</p>`:''}</article>`;}).join('') || `<p class="text-muted">${t("暂无可核对的来源评论。")}</p>`}<h4>${t("手工核对节点（独立保留）")}</h4>${(row.manual_nodes || []).map(entry=>`<article class="dlp-cross-proof"><p>${esc(nodes[entry.node] || entry.node)} · ${esc(entry.by)} · ${esc(entry.on)}${entry.qty==null?'':` · <span title="${esc(entry.qty)}">${shell.formatQuantity(entry.qty)}</span> ${esc(entry.uom)}`}</p><p class="dlp-source-text">${esc(entry.note)}</p></article>`).join('') || `<p class="text-muted">${t("暂无手工节点。")}</p>`}</section>`;
  }
  async function renderLogistics(generation){
   const row=detail();if(!row || !canWrite())return;values={};session=null;
   for(const [field,label,value,config] of [['manual_node','手工节点','review',{native_fieldname:'flow_kind',fieldtype:'Select',options:options(Object.entries(nodes))}],['manual_note','核对说明 / 证据','',{...noteField,reqd:1}],['manual_qty','手工报告数量（可选）',null,{native_doctype:'Purchase Order Item',native_fieldname:'qty',numeric:true,reqd:0}],['manual_uom','数量单位（填写数量时必选）','',{native_fieldname:'allocated_uom',fieldtype:'Link',options:'UOM'}],['evidence_note','补充证据说明','',noteField],['evidence_file','已有原生 File ID（可选）','',{native_fieldname:'external_order',fieldtype:'Link',options:'File',reqd:0}]]){await add(field,label,value,generation,config);if(!current(generation))return;}
   const validNote=field=>{const note=String(values[field] || '');if(!note.trim() || note.length>4096){drawer.error(new Error(t("请填写 1 至 4096 字的核对证据。")));return null;}return note;};
   action('dlp-cross-manual','记录手工节点',()=>{
    const note=validNote('manual_note');if(note==null)return;
    if(!Object.hasOwn(nodes,values.manual_node)){drawer.error(new Error(t("请选择明确的手工核对节点；报告不替代原生入库。")));return;}
    const qty=values.manual_qty;
    if(qty!=null && (qty==='' || !Number.isFinite(Number(qty)) || Number(qty)<=0 || !values.manual_uom)){drawer.error(new Error(t("手工数量需明确单位及有限正数；空数量只记录证据。")));return;}
    // Frappe form encoding turns null into ''. Omit blank optional keys so
    // native server defaults remain None; entered values retain native parsing.
    const quantity=qty==null?{}:{qty,uom:values.manual_uom};
    return mutate('set_manual_node',{name:row.name,expected_link_modified:row.modified,node:values.manual_node,note,...quantity},generation);
   },generation,true);
   action('dlp-cross-evidence','保存补充证据',()=>{const note=validNote('evidence_note');if(note==null)return;const file=String(values.evidence_file || '');if(/[:/\\]/.test(file)){drawer.error(new Error(t("请选择已存在的原生 File ID，不能输入任意网址或上传新文件。")));return;}return mutate('add_evidence',{name:row.name,expected_link_modified:row.modified,note,file:file || null},generation);},generation);
   action('dlp-cross-refresh-logistics','明确刷新来源物流证据',()=>mutate('refresh_logistics',{name:row.name,expected_link_modified:row.modified},generation),generation);
  }
  async function renderReconciliation(generation){
   if(!isUncertain(order) || !canWrite() || !reconciliation)return;
   const holder=drawer.panel.find('.dlp-cross-reconciliation');
   if(reconciliation.generation!==generation || !reconciliation.complete){
    holder.html(warnings(['核对读取不完整，仍保持新建锁定。请恢复完整权限或刷新版本后再核对。']));
    return;
   }
   const records=[...details.values()].map(row=>
    `<p>${t("{0} · {1} · 外部明细 {2} · 内部 {3} / {4} · 数量 {5} {6} · 版本 {7}", [esc(row.name), esc(row.beneficiary_company), esc(row.external_item), esc(row.internal_order), esc(row.internal_item), esc(row.allocated_qty), esc(row.allocated_uom), esc(row.modified)])}</p>`
   ).join('');
   holder.html(`<h4>${t("本次新建结果核对")}</h4><p class="text-warning">${t("请先确认原请求已结束，再核对当前授权关联。未发现关联也不会自动解除锁定；解除后不会自动重试或新建。")}</p><p>${t("订单版本：{0}", [esc(progress.modified)])}</p>${records || `<p>${t("本次授权读取未发现有效关联，请人工核对原请求与原生记录。")}</p>`}<div class="dlp-cross-reconciliation-fields"></div>`);
   const holderSelector='.dlp-cross-reconciliation-fields';
   await add('reconcile_outcome','本次结果（必须人工选择）','',generation,{holder:holderSelector,native_fieldname:'flow_kind',fieldtype:'Select',options:options([['created','已创建：选择核对过的现有关联'],['not_created','未创建：已核对原请求与原生记录']])});
   if(!current(generation))return;
   await add('reconcile_link','已创建的准确关联 ID','',generation,{holder:holderSelector,native_fieldname:'external_item',fieldtype:'Select',options:options([...details.values()].map(row=>[row.name,`${row.name} · ${row.beneficiary_company} · ${row.external_item}`]))});
   if(!current(generation))return;
   await add('reconcile_ack','我确认原请求已结束，并已人工核对上述结果',0,generation,{holder:holderSelector,native_fieldname:'active',fieldtype:'Check'});
   if(!current(generation))return;
   action('dlp-cross-reconcile-confirm','确认核对结果，解除本次新建锁定',()=>acknowledgeReconciliation(generation),generation);
  }
  async function acknowledgeReconciliation(generation){
   if(!current(generation) || drawer.busy || !canWrite() || !isUncertain(order) || reconciliation?.generation!==generation || !reconciliation.complete)return;
   const outcome=values.reconcile_outcome,link=values.reconcile_link;
   if(!['created','not_created'].includes(outcome) || Number(values.reconcile_ack)!==1){
    drawer.error(new Error(t("请选择本次结果，并确认原请求已结束、已人工核对原生记录；不会自动重试。")));
    return;
   }
   if(outcome==='created' && !details.has(link)){
    drawer.error(new Error(t("请选择本次完整授权读取中实际存在的准确关联 ID，不能猜测新建结果。")));
    return;
   }
   // Only this explicit acknowledgement clears an uncertain outcome. Empty
   // lists, close/reopen and refresh never infer that creation did not happen.
   clearUncertain(order);
   selected=outcome==='created'?link:null;
   reconciliation=null;
   tab='internal';
   await render(++drawer.loadId);
  }
  async function render(generation){
   if(!current(generation))return;clearActions();controls=new Map();linkActions.clear();
   const role=c.providerRows?.find(row=>row.name===order && row.row_type==='purchase_order')?.role_context || {},row=detail();
   const hints=[role.buyer_company_proposal,role.source_beneficiary_company_hint,role.beneficiary_company_candidate,role.source_project_hint,role.project_candidate].filter(Boolean);
   shell.drawerBody(drawer,`${tabs()}<p>${t("{0} · 实际采购公司：{1} · {2}", [shell.nativeAction('Purchase Order',order,t("查看原生采购订单")), esc(progress.external.company), esc(progress.currency)])}</p>${warnings([...(progress.progress_warnings || []),...(role.role_warnings || [])])}${hints.length?`<p class="text-warning">${t("来源待核对（仅建议）：{0}", [hints.map(esc).join(' · ')])}</p>`:''}${uncertaintyHTML()}${!canWrite()?`<p class="text-warning">${t("当前没有采购订单写入权限，可查看核对证据。")}</p>`:''}<div class="dlp-cross-links">${[...details.values()].map(link=>`<button type="button" class="btn btn-default dlp-cross-link${link.name===selected?' active':''}" data-link="${esc(link.name)}">${esc(link.beneficiary_company)} · ${esc(link.name)}</button>`).join('')}<button type="button" class="btn btn-default dlp-cross-new">${t("明确新建关联")}</button></div>${tab==='logistics'?`${logisticsHTML(row)}<div class="dlp-cross-fields dlp-document-fields"></div>`:`${warnings(row?.warnings)}<p class="text-muted">${t("原生订单公司保持只读；公司或来源明细需变更时，请停用旧关联后明确新建。保存关联后必须重新确认原生价格。报价草稿不形成应付。")}</p><div class="dlp-cross-cost-warning"></div><div class="dlp-cross-fields dlp-document-fields"></div><p class="dlp-cross-unit"></p><div class="dlp-cross-candidates-warning"></div><div class="dlp-cross-price"></div><div class="dlp-cross-disable"></div>`}<section class="dlp-cross-reconciliation"></section>`);
   bindTabs();drawer.panel.find('.dlp-cross-link').off('.dlpCrossborder').on('click.dlpCrossborder',event=>{if(drawer.busy)return;selected=event.currentTarget.dataset.link;return render(++drawer.loadId);});
   drawer.panel.find('.dlp-cross-new').off('.dlpCrossborder').on('click.dlpCrossborder',()=>{if(drawer.busy)return;selected=null;tab='internal';return render(++drawer.loadId);});
   if(tab==='logistics')await renderLogistics(generation);else await renderInternal(generation);
   if(!current(generation))return;
   await renderReconciliation(generation);
   if(current(generation)){
    action('dlp-cross-reload','刷新核对内容',()=>load(),generation);
    if(canWrite())action('dlp-cross-reconcile','重新读取并核对不确定的新建结果',()=>isUncertain(order)?load({reconcile:true}):undefined,generation);
    drawer.setBusy(false);
    syncBlocked();
   }
  }
  async function load({reconcile=false}={}){
   const generation=++drawer.loadId;
   reconciliation=null;
   roleGeneration++;sellerGeneration++;
   clearActions();
   shell.drawerBody(drawer,`${tabs()}<p role="status">${t("正在读取授权订单与关联…")}</p>${uncertaintyHTML()}`);
   bindTabs();
   try{
    const result=(await frappe.call({method:PROGRESS,args:{purchase_orders:[order],include_items:1},silent:true})).message;if(!current(generation))return;
    progress=result?.[order];if(!progress || progress.state==='restricted' || !progress.external?.company || !progress.modified)throw new Error(t("订单或完整采购字段无权读取，请核对原生权限。"));
    externalItems=new Map((progress.items || []).filter(item=>item.name).map(item=>[item.name,item]));const ids=new Set(),readWarnings=[];
    let relatedReadable=true;
    for(const row of [...(progress.internal || []),...(progress.receipt_logistics || [])]){
     if(['restricted','setup_required'].includes(row.state)){
      relatedReadable=false;
      readWarnings.push(...(row.warnings?.length?row.warnings:['关联缺失或无权读取，请核对原生权限。']));
      continue;
     }
     if(row.name)ids.add(row.name);
     for(const allocation of row.allocations || [])if(allocation.name)ids.add(allocation.name);
    }
    const rows=await Promise.all([...ids].map(async name=>{try{const row=await call('get_link_detail',{name});if(row?.name!==name || row.external_order!==order)throw new Error(t("关联来源不符"));return row;}catch(error){readWarnings.push('部分关联缺失或无权读取，请在原生单据核对。');return null;}}));if(!current(generation))return;
    details=new Map(rows.filter(Boolean).map(row=>[row.name,row]));nativeItems=new Map(rows.filter(Boolean).map(row=>[row.name,new Map((row.native_internal?.items || []).map(item=>[item.name,item]))]));
    if(reconcile){
     const complete=relatedReadable && readWarnings.length===0 && rows.every(row=>row?.modified && row.external_modified===progress.modified);
     reconciliation={generation,complete};
    }
    progress.progress_warnings=[...(progress.progress_warnings || []),...readWarnings];if(!selected || !details.has(selected))selected=details.keys().next().value || null;
    await Promise.all([shell.loadMetadata(LINK),shell.loadMetadata('Purchase Order'),shell.loadMetadata('Purchase Order Item')]);if(!current(generation))return;
    candidateCache.clear();await render(generation);
   }catch(error){if(current(generation)){clearActions();shell.drawerBody(drawer,`${tabs()}${warnings([error.dlpMetadata?error.message:'无法读取采购履行信息，请核对原生公司、内部 Supplier、权限或关联元数据安装状态。',...(reconcile?['核对读取不完整，仍保持新建锁定。请恢复完整权限或刷新版本后再核对。']:[])])}`);bindTabs();action('dlp-cross-reload','刷新核对内容',()=>load(),generation);drawer.setBusy(false);}}
  }
  async function switchTab(next){if(!drawer.alive() || drawer.busy)return;tab=next==='logistics'?'logistics':'internal';return load();}
  owned={c,drawer,order,switchTab};await load();
 }
 root.DeepLinkERPCrossborderProcurement={open,cancelForList};
})(typeof globalThis!=='undefined'?globalThis:this);
