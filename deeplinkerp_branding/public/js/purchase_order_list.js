(function (root) {
 const engine = typeof module === "object" && module.exports ? require("./compact_list.js") : root.DeepLinkERPCompactList;
 const unified = typeof module === "object" && module.exports ? require("./unified_purchase_list.js") : root.DeepLinkERPUnifiedPurchase;
 const COLUMNS = [
  ["transaction_date", "订单日期", 96], ["name", "采购订单号", 166], ["supplier_name", "供应商名称", 150], ["status", "订单状态", 104],
  ["schedule_date", "需求日期", 96], ["company", "公司", 90], ["currency", "币种", 56], ["grand_total", "订单金额", 140],
  ["advance_paid", "已预付", 140], ["advance_payment_status", "预付款状态", 94], ["order_settled", "已付 / 核销", 140], ["order_unpaid", "订单未付", 140],
  ["per_received", "已收货%", 70], ["per_billed", "已开票%", 70], ["project", "项目", 96], ["owner", "创建人", 86], ["receipt_action", "操作", 176],
  ['payment_progress','付款进度',128],['receipt_progress','收货进度',112],
 ].map(([fieldname, label, width]) => ({ fieldname, label, width }));
 const provider = unified.configure(COLUMNS);
 const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const t = (value, args = []) => String((root.__ || (text => text))(value)).replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");
 const numeric = value => value==null || value==='' || typeof value==='boolean' || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
 const itemFields = new Set(['warehouse','item_code','item_name','qty','uom','rate','amount','received_qty']);
 const payments = c => c?.root?.DeepLinkERPPurchasePayments || root.DeepLinkERPPurchasePayments;
 const isOA = c => c.providerScope==='oa';
 const canExpand = (c,doc) => !isOA(c) && doc.row_type==='purchase_order' && doc.order_progress?.state!=='restricted' && (doc.order_progress?.item_fields || []).some(field=>itemFields.has(field));
 function cancelPending(c) { payments(c)?.cancelCrossborderForList?.(c); }
 function fit(c,active) {
  if(!c)return;
  if(active!==undefined)c._setPurchaseSelectionActive?.(active);
  active ??= true;
  if(!active)cancelPending(c);
  return engine.fitViewport(c,{active,root:c.root,scrollElement:c.list.$result?.parent?.('.result-container')?.[0],layoutTailElement:c.list.$frappe_list?.[0],property:'--dlp-purchase-result-max-height',headerSelector:'.dlp-po-grid-header',rowSelector:'.dlp-po-grid-row',minimumRows:2,observeTargets:[c.$filters?.[0]?.parentElement,c.$toolbar?.[0],c.$summary?.[0],c.$paging?.[0]]});
 }
 function columns(c) {
  const selected=c.preferences.columns.map(field=>provider.columns.find(col=>col.fieldname===field)).filter(Boolean);
  const visible=selected.filter(col=>!itemFields.has(col.fieldname));
  const frozenThrough=visible.findIndex(col=>col.fieldname==='supplier_name');let left=76;
  return visible.map((col,index)=>{const width=engine.columnWidth?.(col,c.preferences.density,'Purchase Order') ?? col.width,value={...col,width,left,frozen:index<=frozenThrough};left+=width;return value;});
 }
 function cell(tag,field,value,span=1,width=null,extra='') {
  const col=typeof width==='object'?width:{width,frozen:['_select','_sequence'].includes(field),left:field==='_sequence'?36:0};
  const classes=[col.frozen?'dlp-purchase-frozen':'',field==='name'?'list-subject':'',field==='_select'?'select-like':''].filter(Boolean).join(' ');
  return `<${tag} data-fieldname="${field}"${span>1?` rowspan="${span}"`:''}${classes?` class="${classes}"`:''}${col.width?` style="width:${col.width}px;min-width:${col.width}px;max-width:${col.width}px;--dlp-purchase-left:${col.left}px"`:''}${extra}>${value}</${tag}>`;
 }
 function header(c) {
  const check=isOA(c)?'':`<input class="list-header-checkbox list-check-all" type="checkbox" title="${esc(c.translate('选择当前页全部订单'))}">`;
  return `<thead><tr class="list-header-subject dlp-po-grid-header">${cell('th','_select',check,1,36)}${cell('th','_sequence','#',1,40)}${columns(c).map(col=>cell('th',col.fieldname,esc(c.translate(col.label)),1,col,provider.sortFields.includes(col.fieldname)?` data-provider-sort="${col.fieldname}"`:'' )).join('')}</tr></thead>`;
 }
 function orderValue(c,doc,field) {
  if(field==='name') {
   if(doc.row_type==='oa_request')return `<button type="button" class="btn btn-link btn-xs" data-purchase-source="${esc(doc.oa_name || doc.name)}">${esc(t("待完善 · {0}", [doc.oa_number || doc.name]))}</button>`;
   const refs=(doc.oa_references || []).map(ref=>`<a class="dlp-po-provenance" title="${esc(ref.number || ref.name)}" href="/desk/oa-purchase-request/${encodeURIComponent(ref.name)}">${esc(ref.number || ref.name)}</a>`).join('');
   return `<a title="${esc(doc.name)}" href="${esc(unified.formLink(doc))}" data-name="${esc(doc.name)}">${esc(doc.name)}</a>${refs || (doc.source==='OA'?`<span class="dlp-po-provenance">${t("钉钉")}</span>`:'')}`;
  }
  if(field==='supplier_name') {
   const warning=[!doc.supplier?'供应商待确认':'',doc.oa_warning,...(doc.role_context?.role_warnings || []),...(doc.order_progress?.progress_warnings || [])].filter(Boolean);
   const label=doc.supplier?`<a title="${esc(doc.supplier_name)}" href="/desk/supplier/${encodeURIComponent(doc.supplier)}">${esc(doc.supplier_name || '—')}</a>`:esc(doc.supplier_name || '供应商待核对');
   const summary=warning[0]?.includes('内部关联')?'内部关联未确认':warning[0]?.length>18?`${warning[0].slice(0,18)}…`:warning[0];
   return label+(warning.length?`<span class="dlp-po-row-warning text-warning" title="${esc(warning.join('；'))}">${esc(summary)}</span>`:'');
  }
  if(field==='status')return doc.row_type==='purchase_order'?`${c.list.get_indicator_html?.(doc,Boolean(c.list.workflow_state_fieldname)) || esc(c.translate(doc.status || '—'))} ${payments(c)?.reversalHTML(doc.reversal) || ''}`:esc(doc.status || '来源待完善');
  if(field==='receipt_action') {
   if(doc.row_type==='oa_request')return provider.renderValue(field,doc,{},esc);
   if(payments(c)?.reversalBlocked(doc.reversal))return payments(c).reversalHTML(doc.reversal);
   const related=doc.order_progress?.state==='restricted'?`<span class="text-warning">${t("关联进度受限，请核对权限")}</span>`:['internal','logistics'].map((tab,i)=>`<button type="button" class="btn btn-xs btn-default dlp-crossborder-open" data-crossborder-order="${esc(doc.name)}" data-crossborder-tab="${tab}">${t(i?'物流证据':'内部关联')}</button>`).join('');
   return `<span class="dlp-po-row-actions">${payments(c)?.orderReceiptAction(doc) || ''}<details class="dlp-po-row-more"><summary class="btn btn-xs btn-default">${t('更多')}</summary><span class="dlp-po-row-more-menu">${related}</span></details></span>`;
  }
  return provider.renderValue(field,doc,{},esc) ?? grid.renderValue(field,doc,{allowed:c.allowed,translate:c.translate,number:(value,field)=>grid.formatNumber(c.root,value,field),date:value=>c.root.frappe.datetime?.str_to_user?.(value) || value});
 }
 function row(c,doc) {
  const cols=columns(c),expanded=c.expandedDetails?.has(doc.name),detail=doc.order_progress || {};
  const selectable=doc.row_type==='purchase_order' && !isOA(c);
  const check=selectable?`<input type="checkbox" class="list-row-checkbox" data-doctype="Purchase Order" data-name="${esc(doc.name)}">`:'';
  const arrow=canExpand(c,doc)?`<button type="button" class="dlp-purchase-expand" data-name="${esc(doc.name)}" aria-expanded="${Boolean(expanded)}" aria-label="${esc(c.translate(expanded?'收起物料明细':'展开物料明细'))}">${expanded?'⌄':'›'}</button>`:'';
  let extra='';
  if(expanded){
   const permitted=new Set(detail.item_fields || []),items=detail.state!=='restricted' && permitted.has('name')?(detail.items || []).filter(item=>item.name):[];
   const definitions=[['item_code','物料编码'],['item_name','物料名称'],['warehouse','仓库'],['qty','数量'],['uom','单位'],['rate','单价'],['amount','金额'],['received_qty','已收数量']].filter(([field])=>permitted.has(field));
   const contents=detail.state==='restricted'?`<span class="text-warning">${t("物料明细不可见，请核对权限")}</span>`:engine.detailTable({columns:definitions,items,escape:esc,translate:c.translate,wrapperClass:'dlp-purchase-detail-scroll',tableClass:'dlp-purchase-detail-table',columnClass:field=>['qty','rate','amount','received_qty'].includes(field)?'dlp-po-number':'',format:(field,item)=>['qty','rate','amount','received_qty'].includes(field)?numeric(item[field]):esc(item[field] ?? '—')});
   extra=`<tr class="dlp-purchase-expanded"><td colspan="${cols.length+2}">${contents || t("暂无可读物料明细")}</td></tr>`;
  }
  return `<tbody class="list-row-container dlp-purchase-order-body" data-dlp-detail-name="${esc(doc.name)}" data-order-name="${esc(doc.name)}" tabindex="0"><tr class="${selectable?'level list-row ':''}dlp-po-grid-row" data-order-name="${esc(doc.name)}">${cell('td','_select',check,1,36)}${cell('td','_sequence',arrow,1,40)}${cols.map(col=>cell('td',col.fieldname,orderValue(c,doc,col.fieldname),1,col,` title="${esc(typeof doc[col.fieldname]==='object'?'':doc[col.fieldname] ?? '')}"`)).join('')}</tr>${extra}</tbody>`;
 }
 function table(c,rows) { return `<div class="list-row-container dlp-purchase-table-wrap"><table class="dlp-purchase-table">${header(c)}${rows.map(doc=>row(c,doc)).join('')}</table><div class="checkbox-actions" style="display:none"></div></div>`; }
 function selectionChanged(c) {
  if(!c.getSelectedPurchaseOrders)return;
  const selected=c.getSelectedPurchaseOrders(),names=new Set(selected.map(row=>row.name));
  const blocked=c.providerRows.filter(row=>names.has(row.name) && !row.receipt_eligibility?.allowed);
  c.$purchaseActions?.find('.dlp-purchase-selected-count').text(c.translate(t("已选 {0} 张订单", [selected.length])));
  c.$purchaseActions?.find('[data-purchase-action="receipt"]').prop('disabled',!selected.length || blocked.length>0);
  const reasons=blocked.map(doc=>`${doc.name}：${doc.receipt_eligibility?.reason || t("收货资格未知，请刷新")}`);
  c.$purchaseActions?.find('.dlp-purchase-action-notice').text(blocked.length?`${reasons.slice(0,3).join('；')}${blocked.length>3?t("；另有 {0} 张不可入库", [blocked.length-3]):''}`:selected.length>1?c.translate('默认逐单入库，可选择合并。'):'').attr('title',reasons.join('；'));
 }
 function clearSelection(c) {
  c.list.clear_checked_items?.();
  c.list.$checkbox_cursor=null;
  selectionChanged(c);
 }
 function scopeChanged(c) {
  c.$purchaseTabs?.find('[data-purchase-scope]').removeClass('active').attr('aria-selected','false');
  c.$purchaseTabs?.find(`[data-purchase-scope="${c.providerScope}"]`).addClass('active').attr('aria-selected','true');
  c.$purchaseActions?.toggle(!isOA(c));
 }
 function mount(c) {
  const result=c.list.$result?.[0];let listening=false;
  // Set page rows before native forwarding/on_row_checked rewrites the sole header.
  const selectPage=event=>{
   if(isOA(c) || !event.target?.matches?.('.dlp-purchase-table .list-header-subject .list-check-all'))return;
   c.list.$result.find('.list-row-checkbox').prop('checked',event.target.checked);
  };
  c._setPurchaseSelectionActive=active=>{
   if(!result?.addEventListener || listening===active)return;
   result[active?'addEventListener':'removeEventListener']('change',selectPage,true);listening=active;
  };
  c._setPurchaseSelectionActive(true);
  // Stable whole-order selection projection for the later batch-operation task.
  c.getSelectedPurchaseOrders=()=> {
   if(isOA(c))return [];
   const names=new Set((c.list.get_checked_items?.(true) || []).map(doc=>typeof doc==='string'?doc:doc.name));
   return c.providerRows.filter(doc=>doc.row_type==='purchase_order' && names.has(doc.name)).map(doc=>({name:doc.name,modified:doc.modified}));
  };
  c.runPurchaseAction=async action=> {
   if(action!=='receipt')return;
   const selected=c.getSelectedPurchaseOrders();
   if(!selected.length)return;
   const blocked=selected.map(value=>c.providerRows.find(row=>row.name===value.name)).filter(doc=>!doc?.receipt_eligibility?.allowed || payments(c)?.reversalBlocked(doc.reversal));
   if(blocked.length){c.root.frappe.msgprint?.({title:c.translate('无法入库'),message:blocked.map(doc=>`${esc(doc?.name)}：${esc(doc?.receipt_eligibility?.reason || '收货资格已变化，请刷新')}`).join('<br>')});return;}
   if(selected.length>1){
    if(action==='receipt')return payments(c).batchDocumentDrawer('Purchase Order',selected,'Purchase Receipt');
    return;
   }
   return payments(c).documentDrawer('Purchase Order',selected[0].name,'Purchase Receipt');
  };
  // Crossborder drawer saves call this public hook before the shared page refresh.
  c.invalidatePurchaseDetails=names=> {
   c.requestId++;c.list.last_args=null;
   c.invalidateRowDetails(names);
   const targets=names==null?null:new Set(Array.isArray(names)?names:[names]);
   for(const doc of c.providerRows)if(doc.row_type==='purchase_order' && (!targets || targets.has(doc.name)) && doc.order_progress){delete doc.order_progress.items;delete doc.order_progress.item_fields;}
   c.list.render_list();c.list.set_rows_as_checked?.();
  };
  c.$purchaseTabs=c.root.$(`<nav class="dlp-purchase-scope-tabs" role="tablist" aria-label="${t('采购来源视图')}"><button type="button" class="btn btn-default btn-sm" role="tab" data-purchase-scope="orders">${t("采购订单")}</button><button type="button" class="btn btn-default btn-sm" role="tab" data-purchase-scope="oa">${t("钉钉待完善")}</button></nav>`).appendTo(c.$toolbar);
  c.$purchaseTabs.on('click.dlpPurchaseScope','[data-purchase-scope]',event=>c.setProviderScope(event.currentTarget.dataset.purchaseScope));
  c.$purchaseActions=c.root.$(`<div class="dlp-purchase-selection-actions"><span class="dlp-purchase-selected-count" aria-live="polite"></span><button type="button" class="btn btn-primary btn-sm" data-purchase-action="receipt" disabled>${t("入库")}</button><span class="text-muted dlp-purchase-action-notice" aria-live="polite"></span></div>`).prependTo(c.$toolbar);
  c.$purchaseActions.on('click.dlpPurchaseAction','[data-purchase-action]',event=>c.runPurchaseAction(event.currentTarget.dataset.purchaseAction));
  scopeChanged(c);selectionChanged(c);
 }
 const onPayload=provider.onPayload;
 provider.onPayload=c=>{onPayload?.(c);selectionChanged(c);};
 const grid = engine.create({doctype:'Purchase Order',purchaseChrome:true,backupMigratedPreferences:true,mainFilterFields:['search','company','status','from_date','to_date'],onMount:c=>engine.mountRowDetails(c,{rows:()=>c.providerRows,load:doc=>doc.order_progress,canExpand:doc=>canExpand(c,doc),active:()=>engineRoute(c)}),dismissInitialOnboarding:true,keepColumnHeader:true,columns:COLUMNS,defaultColumns:provider.defaultColumns,provider,providerSelectable:doc=>doc.row_type==='purchase_order',computedFields:['receipt_action','order_settled','order_unpaid'],moneyPrecision:2,
  onRequestStart:c=>{cancelPending(c);clearSelection(c);},onPageChange:clearSelection,onScopeChange:scopeChanged,onSelectionChange:selectionChanged,mountControls:mount,renderHeader:c=>`<table class="dlp-purchase-table">${header(c)}</table>`,renderRow:row,renderTable:table,
  afterRender:c=>{selectionChanged(c);fit(c);},onRouteChange:fit,controllerKey:'dlpPurchaseOrderGrid',routeClass:'dlp-purchase-order-grid-active',freezeUntil:'supplier_name',moneySummary:true,
  numbers:['grand_total','advance_paid','per_received','per_billed'],dates:['transaction_date','schedule_date'],quickFields:['company','status','advance_payment_status'],searchFields:['name','supplier_name','project'],extraFields:['supplier','party_account_currency','docstatus'],optionLabels:{progress_phase:{supplier_unpaid:'供应商未付',internal_unsettled:'内部待结算',factory_pending:'工厂待入库'}},controls:[
   {fieldname:'search',fieldtype:'Data',label:t("订单/审批/项目/供应商")},
   {fieldname:'from_date',fieldtype:'Date',label:t("订单开始日期"),permission_field:'transaction_date'},
   {fieldname:'to_date',fieldtype:'Date',label:t("订单结束日期"),permission_field:'transaction_date'},
   {fieldname:'company',fieldtype:'Link',options:'Company',label:t("采购付款公司")},
   {fieldname:'status',fieldtype:'Select',label:t("订单状态")},
   {fieldname:'beneficiary_company',fieldtype:'Link',options:'Company',label:t("最终归属公司"),permission_field:'company'},
   {fieldname:'progress_phase',fieldtype:'Select',label:t("进度阶段"),permission_field:'name',options:'\nsupplier_unpaid\ninternal_unsettled\nfactory_pending',emptyLabel:t("全部进度")},
   {fieldname:'review_only',fieldtype:'Check',label:t("待核对"),permission_field:'name'},
  ]});
 function engineRoute(c){const route=c.root.frappe.get_route?.() || [];return String(route[0]).toLowerCase()==='list' && route[1]==='Purchase Order';}
 if(typeof module==='object' && module.exports)module.exports=grid;
 root.DeepLinkERPPurchaseOrderGrid=grid;
 grid.install(root);
})(typeof globalThis!=='undefined'?globalThis:this);
