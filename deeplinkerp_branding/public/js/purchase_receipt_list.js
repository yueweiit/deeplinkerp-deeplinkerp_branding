(function(root) {
 'use strict';
 const translateText = (text, args = []) => String((globalThis.__ || (value => value))(text)).replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");
 const native = [['posting_date','入库日期',94],['name','采购入库单号',158],['supplier_name','供应商名称',160],['company','公司',130],['currency','币种',60],['grand_total','入库金额',120],['status','入库状态',88]].map(([fieldname,label,width])=>({fieldname,label,width}));
 const columns = [...native, ...[['orders','采购订单',158],['payment_state','付款状态',130],['settled','已付 / 核销',128],['outstanding','未付',128],['payment_action','操作',230]].map(([fieldname,label,width])=>({fieldname,label,width}))];
 const service = 'deeplinkerp_branding.services.purchase_payment_service.get_receipt_list';
 const detailColumns=[['item_code','物料编码'],['item_name','物料名称'],['warehouse','仓库'],['qty','入库数量'],['uom','单位'],['rate','单价'],['amount','金额']];
 function receiptRows(c){return c.providerScope==='orders'?c.list.data:c.providerRows;}
 async function loadDetails(c,doc){
  if(!c.allowed.has('items'))throw new Error(translateText("没有物料明细读取权限。"));
  const frappe=c.root.frappe;
  c.receiptItemMetadata ||= frappe.model.with_doctype('Purchase Receipt Item').catch(error=>{c.receiptItemMetadata=null;throw error;});
  await c.receiptItemMetadata;
  // Native get checks document permission and applies field-level read filtering
  // server-side; metadata only narrows the returned projection further.
  const response=await frappe.call({method:'frappe.client.get',args:{doctype:'Purchase Receipt',name:doc.name},silent:true}),native=response.message;
  const meta=frappe.get_meta('Purchase Receipt Item'),fields=new Set((meta?.fields || []).filter(df=>!df.is_virtual && frappe.perm.has_perm('Purchase Receipt',df.permlevel || 0,'read')).map(df=>df.fieldname));
  const items=Array.isArray(native?.items)?native.items:[];
  const permitted=detailColumns.filter(([field])=>fields.has(field) && items.some(item=>Object.hasOwn(item,field)));
  return {header:{modified:native?.modified,currency:native?.currency},columns:permitted,items:items.map(item=>Object.fromEntries(permitted.map(([field])=>[field,item[field]])))};
 }
 function rowExtra(c,doc){
  if(!c.expandedDetails?.has(doc.name))return '';
  const escape=value=>String(value ?? '').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char])),detail=c.detailCache.get(doc.name);
  const contents=detail?.error?escape(detail.error):detail?root.DeepLinkERPCompactList.detailTable({columns:detail.columns,items:detail.items,escape,translate:c.translate,wrapperClass:'dlp-purchase-detail-scroll',tableClass:'dlp-purchase-detail-table',columnClass:field=>['qty','rate','amount'].includes(field)?'dlp-po-number':'',format:(field,item)=>['qty','rate','amount'].includes(field)?item[field]==null?'—':escape(Number(item[field]).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})):escape(item[field] ?? '—')}):c.detailPending.has(doc.name)?'正在读取物料明细…':'明细已更新，请刷新后重试。';
  return `<div class="dlp-purchase-expanded" role="region" aria-label="${translateText('入库物料明细')}">${contents}</div>`;
 }
 function request(c) {
  const args = c.originals.get_args.call(c.list);
  for (const filter of [...(args.filters || []), ...(args.or_filters || [])]) {
   if (Array.isArray(filter) && filter.length >= 4 && filter[0] !== 'Purchase Receipt') throw new Error(translateText("此高级关联筛选请切换“原生筛选 / 批量操作”；当前条件不会被丢弃。"));
  }
  return {method:service,args:{filters:JSON.stringify(c.quick),native_filters:JSON.stringify(args.filters || []),or_filters:JSON.stringify(args.or_filters || []),order_by:c.list.sort_selector?.get_sql_string() || c.providerOrderBy || args.order_by,start:c.page*c.pageSize,page_length:c.pageSize}};
 }
 function sortBy(c,field) {
  const selector=c.list.sort_selector;
  const [oldField,oldOrder]=(c.providerOrderBy || '').split(' ');
  const order=(selector?.sort_by || oldField)===field && (selector?.sort_order || oldOrder)==='asc'?'desc':'asc';
  c.setPage(0);
  if(selector){selector.set_value(field,order);c.list.on_sort_change(field,order);}
  else{c.providerOrderBy=`${field} ${order}`;c.refresh();}
 }
 function showPredicates(c){for(const field of ['supplier','status'])c.controls[field]?.$wrapper?.show();}
 function selectionChanged(c){const names=new Set(c.list.get_checked_items?.(true) || []),selected=c.providerRows.filter(row=>names.has(row.name));c.$receiptBatch?.find('button').prop('disabled',!selected.length || selected.some(row=>c.root.DeepLinkERPPurchasePayments?.reversalBlocked(row.reversal)));c.$receiptBatch?.find('span').text(translateText("已选 {0} 张入库", [selected.length]));}
 function clearSelection(c){c.list.clear_checked_items?.();c.$receiptBatch?.toggle(c.providerScope==='all');selectionChanged(c);}
 async function exportCurrent(c) {
  const { root } = c;
  const args = {...request(c).args,export_format:'xlsx',columns:JSON.stringify(c.preferences.columns.filter(field=>field!=='payment_action'))};
  delete args.start; delete args.page_length;
  if (!root.DeepLinkERPPurchaseOrderExport?.downloadWorkbook) await root.frappe.require('/assets/deeplinkerp_branding/js/purchase_order_export.js');
  const exporter = root.DeepLinkERPPurchaseOrderExport;
  if (!exporter) throw new Error(translateText("导出组件加载失败，请刷新重试。"));
  exporter.downloadWorkbook(root,await exporter.fetchNativeWorkbook(root,args,service),'采购入库明细');
 }
 const provider = {columns,defaultColumns:['name','supplier_name','posting_date','status','company','grand_total','orders','payment_state','payment_action'],preferenceVersion:4,migratePreferences:value=>value?.version===4?value:{...value,columns:provider.defaultColumns},virtualFields:['orders','payment_state','settled','outstanding','payment_action'],freezeUntil:'supplier_name',sortFields:native.map(c=>c.fieldname),sortBy,formLink:doc=>`/desk/purchase-receipt/${encodeURIComponent(doc.name)}`,request,exportCurrent,
  onQueryError:(c,error)=>{if(!String(error.message).includes('高级关联筛选'))return false;c.setProviderScope('orders',false);c.root.frappe.show_alert?.({message:error.message,indicator:'orange'});return true;},
  summary:(c,escape)=>escape(translateText("入库金额：{0} ｜ 余额按关联应付币种；共享整单余额不作入库合计", [(c.providerPayload?.totals || []).map(row=>`${Number(row.grand_total).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})} ${row.currency}`).join(' · ') || '—'])),
  renderValue:(field,doc,format,escape)=>{
   if(field==='grand_total') return doc.grand_total===null || doc.grand_total===undefined?'—':`${escape(Number(doc.grand_total).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}))} ${escape(doc.currency || '')}`.trim();
   if(field==='status') return `${escape(doc.docstatus===0?'草稿':doc.docstatus===2?'已取消':doc.is_return?'退货入库':'已入库')} ${root.DeepLinkERPPurchasePayments?.reversalHTML(doc.reversal) || ''}`;
   if(field==='orders') return(doc.orders || []).map(name=>`<a href="/desk/purchase-order/${encodeURIComponent(name)}">${escape(name)}</a>`).join(' · ') || '—';
   if(field==='payment_state') return `<span title="${escape((doc.warnings || []).join('；'))}">${escape(doc.settlement_state || doc.payment_state || '—')}${doc.shared_payable?translateText(" · 共享整单"):''}</span>`;
   if(['settled','outstanding'].includes(field)) return(doc.balances || []).map(b=>`<span title="${doc.shared_payable?translateText("共享应付整单余额"):translateText("关联应付余额")}">${escape(Number(b[field]).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}))} ${escape(b.currency)}</span>`).join(' · ') || '—';
   if(field==='payment_action') {
    if(root.DeepLinkERPPurchasePayments?.reversalBlocked(doc.reversal))return root.DeepLinkERPPurchasePayments.reversalHTML(doc.reversal);
    if(doc.docstatus===0){const actions=root.DeepLinkERPPurchasePayments;return (doc.draft_orders || []).length?(doc.draft_orders || []).map(name=>actions?.nativeAction('Purchase Order',name,'先处理订单草稿') || '').join(' '):actions?.nativeAction('Purchase Receipt',doc.name,'处理入库草稿') || '—';}
    const button=(cls,label,target='')=>`<button type="button" class="btn btn-xs btn-default ${cls}" data-name="${escape(doc.name)}"${target?` data-target="${escape(target)}"`:''}>${escape(translateText(label))}</button>`;
    return [(doc.draft_invoices || []).map(invoice=>button('dlp-receipt-invoice','继续应付草稿',typeof invoice==='string'?invoice:invoice.name)).join(' '), doc.can_create_invoice?button('dlp-receipt-invoice','确认应付'):'',doc.can_create?button('dlp-receipt-pay','付款 / 继续付款'):''].filter(Boolean).join(' ') || `<span title="${escape(doc.reason || '')}">—</span>`;
   }
  },
  mountControls:c=>{
   const $=c.root.$;
   const select=$(`<select class="form-control input-xs" aria-label="${translateText('入库视图')}"><option value="all">${translateText("关联付款视图")}</option><option value="orders">${translateText("原生筛选 / 批量操作")}</option></select>`).appendTo($(`<label>${translateText("视图")} </label>`).appendTo(c.$advancedFilters));
   c.$providerScope=select;
   select.on('change.dlpReceipt',()=>c.setProviderScope(select.val()));
   c.$receiptBatch=$(`<div class="dlp-purchase-selection-actions"><span></span><button type="button" class="btn btn-primary btn-sm" disabled>${translateText("合并应付付款")}</button></div>`).prependTo(c.$toolbar);
   c.$receiptBatch.find('button').on('click.dlpReceipt',()=>{const names=new Set(c.list.get_checked_items?.(true) || []);return root.DeepLinkERPPurchasePayments.batchPay('Purchase Receipt',c.providerRows.filter(row=>names.has(row.name)).map(row=>({name:row.name})));});
   c.list.$result.on('click.dlpReceipt','[data-provider-sort]',e=>sortBy(c,e.currentTarget.dataset.providerSort));
   c.setProviderScope('all',false);
  },
  onActivate:showPredicates,
  onPayload:c=>{ showPredicates(c); selectionChanged(c); c.$providerNotice?.remove(); c.$providerNotice=c.root.$('<p class="text-muted dlp-po-provider-notice"></p>').text(translateText("入库 → 确认应付 → 部分 / 合并付款。提交付款才计已付；高级关联筛选可切换原生视图。")).appendTo(c.$advancedFilters); }
 };
 const grid=root.DeepLinkERPCompactList.create({doctype:'Purchase Receipt',purchaseChrome:true,backupMigratedPreferences:true,preferenceVersion:4,defaultColumns:['name','supplier_name','posting_date','status','company','grand_total'],migratePreferences:value=>value?.version===4?value:{...value,columns:['name','supplier_name','posting_date','status','company','grand_total']},mainFilterFields:['search','company','status','from_date','to_date'],onMount:c=>root.DeepLinkERPCompactList.mountRowDetails(c,{rows:()=>receiptRows(c),load:doc=>loadDetails(c,doc),canExpand:()=>c.allowed.has('items'),active:()=>{const route=c.root.frappe.get_route?.() || [];return String(route[0]).toLowerCase()==='list' && route[1]==='Purchase Receipt';}}),rowExtra,renderSequence:(c,doc)=>c.allowed.has('items')?`<button type="button" class="dlp-purchase-expand" data-name="${grid.escapeHTML(doc.name)}" aria-expanded="${Boolean(c.expandedDetails?.has(doc.name))}" aria-label="${translateText('展开 / 收起入库物料明细')}">${c.expandedDetails?.has(doc.name)?'⌄':'›'}</button>`:'',moneyPrecision:2,dismissInitialOnboarding:true,columns:native,provider,providerSelectable:doc=>doc.docstatus===1 && !doc.is_return,keepColumnHeader:true,onSelectionChange:selectionChanged,onPageChange:clearSelection,onScopeChange:clearSelection,controllerKey:'dlpReceiptGrid',routeClass:'dlp-purchase-receipt-grid-active',freezeUntil:'supplier_name',dateField:'posting_date',numbers:['grand_total','settled','outstanding'],dates:['posting_date'],quickFields:['company','supplier','status'],searchFields:['name','supplier_name'],extraFields:['supplier','modified'],controls:[{fieldname:'search',fieldtype:'Data',label:translateText("入库单号 / 供应商名称")},{fieldname:'company',fieldtype:'Link',options:'Company',label:translateText("公司")},{fieldname:'supplier',fieldtype:'Link',options:'Supplier',label:translateText("供应商")},{fieldname:'status',fieldtype:'Select',label:translateText("入库状态")},{fieldname:'from_date',fieldtype:'Date',label:translateText("入库开始日期"),permission_field:'posting_date'},{fieldname:'to_date',fieldtype:'Date',label:translateText("入库结束日期"),permission_field:'posting_date'}]});
 root.DeepLinkERPReceiptGrid=grid; grid.install(root);
})(globalThis);
