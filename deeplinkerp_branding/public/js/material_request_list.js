(function (root) {
 const translateText = (text, args = []) => String((globalThis.__ || (value => value))(text)).replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");
 const engine = typeof module === "object" && module.exports ? require("./compact_list.js") : root.DeepLinkERPCompactList;
 const categories = Object.assign(Object.create(null), {consumable:"消耗品",raw_material:"原料申请",semi_finished:"半成品申请",unclassified:"未分类"});
 const pendingView = "mes_pending";
 const issuedView = "mes_issued";
 const viewIds = [pendingView, issuedView, "all"];
 const issueTypes = ["Material Issue", "Material Transfer for Manufacture", "Injection Molding Issuance"];
 const legacyTypes = ["Purchase", "Material Transfer", "Material Issue", "Manufacture", "Subcontracting", "Customer Provided", "Material Transfer for Manufacture", "Injection Molding Issuance"];
 const categoryLabels = Object.assign(Object.create(null), categories, Object.fromEntries(legacyTypes.map(type => [`legacy:${type}`, type])));
 const pendingFields = ["name", "docstatus", "material_request_type", "status", "per_ordered"];
 const canShowPending = controller => pendingFields.every(field => controller.allowed.has(field));
 const defaultColumns = ["custom_odt", "custom_mes_issue_category", "name", "status", "transaction_date", "schedule_date", "company", "set_from_warehouse", "per_ordered", "custom_material_request_no"];
 const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
 function migrateColumns(value) {
  if (!value || value.version === 1) return value;
  return {...value, columns:Array.isArray(value.columns) ? value.columns.filter(field => field !== "owner") : undefined};
 }
 function viewQuery(args, controller) {
  const filters = [...(args.filters || [])];
  const category = controller.quick.custom_mes_issue_category;
  if (category?.startsWith("legacy:") && legacyTypes.includes(category.slice(7)) && controller.allowed.has("custom_mes_issue_category") && controller.allowed.has("material_request_type")) {
   filters.push(["Material Request", "custom_mes_issue_category", "is", "not set"], ["Material Request", "material_request_type", "=", category.slice(7)]);
  } else if (category && controller.allowed.has("custom_mes_issue_category")) {
   filters.push(["Material Request", "custom_mes_issue_category", "=", category]);
  }
  if (controller.allowed.has("schedule_date")) {
   if (controller.quick.need_from_date) filters.push(["Material Request", "schedule_date", ">=", controller.quick.need_from_date]);
   if (controller.quick.need_to_date) filters.push(["Material Request", "schedule_date", "<=", controller.quick.need_to_date]);
  }
  if (controller.mrView === "all") return {...args, filters};
  // The MES create path assigns this prefix. It also covers older rows whose source field is blank.
  const issueFilters = [["Material Request", "name", "like", "MAT-MR-MES-%"],
   ["Material Request", "docstatus", "=", 1],
   ["Material Request", "material_request_type", "in", issueTypes]];
  return {...args, filters:[...filters, ...issueFilters, ...(controller.mrView === issuedView
   ? [["Material Request", "per_ordered", ">=", 100]]
   : [["Material Request", "status", "not in", ["Stopped", "Cancelled"]], ["Material Request", "per_ordered", "<", 100]])]};
 }
 function renderViewCounts(controller) {
  controller.$mrViews?.find("[data-mr-count]").each(function() {
   const count = controller.mrCounts?.[this.dataset.mrCount];
   controller.root.$(this).text(typeof count === "number" ? count.toLocaleString() : count ?? "…");
  });
 }
 function refreshViewCounts(controller) {
  const frappe = controller.root.frappe;
  if (!frappe.call || !controller.originals?.get_args) return;
  let base;
  try { base = grid.buildQuery(controller.originals.get_args.call(controller.list), controller.quick, controller.allowed); }
  catch (_) { return; }
  const version = ++controller.mrCountVersion;
  controller.mrCounts = {[pendingView]:null, [issuedView]:null, all:null};
  if (!canShowPending(controller)) controller.mrCounts[pendingView] = controller.mrCounts[issuedView] = "—";
  renderViewCounts(controller);
  for (const view of viewIds) {
   if (view === controller.mrView || view !== "all" && !canShowPending(controller)) continue;
   const query = viewQuery(base, {...controller, mrView:view});
   const args = grid.buildRequests(query, [], controller.allowed).count;
   Promise.resolve().then(() => frappe.call({method:"frappe.desk.reportview.get_count", args, silent:true})).then(response => {
    if (version !== controller.mrCountVersion) return;
    controller.mrCounts[view] = Number(response.message || 0);
    renderViewCounts(controller);
   }).catch(() => {
    if (version !== controller.mrCountVersion) return;
    controller.mrCounts[view] = "—";
    renderViewCounts(controller);
   });
  }
 }
 function acceptCurrentCount(controller) {
  if (!controller.mrCounts) return;
  controller.mrCounts[controller.mrView] = controller.total;
  renderViewCounts(controller);
 }
 function setView(controller, view) {
  if (!viewIds.includes(view) || view !== "all" && !canShowPending(controller) || view === controller.mrView) return;
  controller.list.clear_checked_items?.();
  controller.mrView = view;
  controller.$mrViews?.find("[data-mr-view]").each(function() {
   const selected = this.dataset.mrView === view;
   controller.root.$(this).attr("aria-pressed", String(selected)).toggleClass("btn-primary", selected).toggleClass("btn-default", !selected);
  });
  controller.setPage(0);
  return controller.refresh();
 }
 function mountViews(controller) {
  if (!controller.$filters) return;
  const $ = controller.root.$;
  const label = text => grid.escapeHTML(controller.translate(text));
  controller.$mrViews = $(`<div class="dlp-mr-view-strip"><div class="btn-group btn-group-sm dlp-mr-views" role="group" aria-label="${label("物料需求视图")}">
   ${[[pendingView,"待发料"],[issuedView,"已发料"],["all","全部"]].map(([view,text]) => `<button type="button" class="btn btn-sm ${controller.mrView === view ? "btn-primary" : "btn-default"}" data-mr-view="${view}" aria-pressed="${controller.mrView === view}" ${view !== "all" && !canShowPending(controller) ? "disabled" : ""}>${label(text)} <span class="dlp-mr-view-count" data-mr-count="${view}">…</span></button>`).join("")}
  </div></div>`).insertBefore(controller.$filters);
  controller.$mrViews.on("click.dlpMRViews", "[data-mr-view]", event => setView(controller, event.currentTarget.dataset.mrView));
  renderViewCounts(controller);
 }
 const clampWidth = value => Math.max(80, Math.min(800, Math.round(value)));
 function widthStorageKey(controller) {
  const frappe = controller.root.frappe;
  return `${grid.preferenceKey(frappe.boot?.sitename || controller.root.location?.host, frappe.session?.user)}:widths`;
 }
 function restoreColumnWidths(controller) {
  let saved;
  try { saved = JSON.parse(controller.root.localStorage?.getItem(widthStorageKey(controller)) || "null"); } catch (_) { return; }
  if (!saved || typeof saved !== "object" || Array.isArray(saved)) return;
  for (const col of columns) if (controller.allowed.has(col.fieldname) && Number.isFinite(saved[col.fieldname])) col.width = clampWidth(saved[col.fieldname]);
 }
 function saveColumnWidths(controller) {
  try { controller.root.localStorage?.setItem(widthStorageKey(controller), JSON.stringify(Object.fromEntries(columns.map(col => [col.fieldname, col.width])))); }
  catch (_) { /* Widths remain usable when browser storage is unavailable. */ }
 }
 function previewColumnWidth(controller, field) {
  const host = controller.list.$result?.[0];
  if (!host?.querySelectorAll) return;
  const visible = controller.preferences.columns.map(name => columns.find(col => col.fieldname === name)).filter(Boolean);
  const tracks = `36px 40px ${visible.map(col => `${col.width}px`).join(" ")}`;
  for (const node of host.querySelectorAll(".dlp-po-grid-header, .dlp-po-grid-row")) node.style.setProperty("--dlp-po-columns", tracks);
  const resized = columns.find(col => col.fieldname === field);
  for (const node of host.querySelectorAll(`.dlp-po-grid-cell[data-fieldname="${field}"]`)) node.style.width = `${resized.width}px`;
  for (const node of host.querySelectorAll(`[data-mr-resize="${field}"]`)) node.setAttribute("aria-valuenow", String(resized.width));
  const frozenEnd = visible.findIndex(col => col.fieldname === "custom_mes_issue_category");
  let left = 76;
  for (const col of visible.slice(0, frozenEnd + 1)) {
   for (const node of host.querySelectorAll(`.dlp-po-frozen[data-fieldname="${col.fieldname}"]`)) node.style.setProperty("--dlp-po-left", `${left}px`);
   left += col.width;
  }
 }
 function mountColumnResize(controller) {
  restoreColumnWidths(controller);
  const {root, list} = controller;
  if (!root.document?.addEventListener || !list.$result?.on) return;
  const change = (field, width) => {
   const col = columns.find(entry => entry.fieldname === field);
   if (!col || col.width === width) return false;
   col.width = width;
   previewColumnWidth(controller, field);
   return true;
  };
  const finish = focusField => {
   saveColumnWidths(controller); list.render_list(); list.set_rows_as_checked?.();
   if (focusField) list.$result.find(`[data-mr-resize="${focusField}"]`)[0]?.focus?.();
  };
  list.$result.on("click.dlpMRResize", "[data-mr-resize]", event => { event.preventDefault(); event.stopPropagation(); });
  list.$result.on("keydown.dlpMRResize", "[data-mr-resize]", event => {
   if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
   event.preventDefault(); event.stopPropagation();
   const field = event.currentTarget.dataset.mrResize;
   const col = columns.find(entry => entry.fieldname === field);
   if (col && change(field, clampWidth(col.width + (event.key === "ArrowRight" ? 1 : -1) * (event.shiftKey ? 20 : 10)))) finish(field);
  });
  list.$result.on("pointerdown.dlpMRResize", "[data-mr-resize]", event => {
   if (event.button !== 0) return;
   const field = event.currentTarget.dataset.mrResize;
   const col = columns.find(entry => entry.fieldname === field);
   if (!col) return;
   event.preventDefault(); event.stopPropagation();
   const startX = event.originalEvent?.clientX ?? event.clientX;
   const startWidth = col.width, pointerId = event.originalEvent?.pointerId ?? event.pointerId;
   let changed = false;
   const move = next => {
    if (next.pointerId !== pointerId) return;
    changed = change(field, clampWidth(startWidth + next.clientX - startX)) || changed;
   };
   const stop = next => {
    if (next.pointerId !== pointerId) return;
    root.document.removeEventListener("pointermove", move);
    root.document.removeEventListener("pointerup", stop);
    root.document.removeEventListener("pointercancel", stop);
    if (changed) finish();
   };
   root.document.addEventListener("pointermove", move);
   root.document.addEventListener("pointerup", stop);
   root.document.addEventListener("pointercancel", stop);
  });
 }
 function headerResizeHandle(controller, col) {
  const label = escape(`${controller.translate(col.label)}：${controller.translate("拖动或按方向键调整列宽")}`);
  return `<span class="dlp-mr-resize" data-mr-resize="${escape(col.fieldname)}" role="separator" tabindex="0" aria-orientation="vertical" aria-label="${label}" aria-valuemin="80" aria-valuemax="800" aria-valuenow="${col.width}" title="${label}"></span>`;
 }
 const itemColumns = [["item_code", "物料编码"], ["item_name", "物料名称"], ["qty", "需求数量"], ["issued_qty", "已处理数量"], ["remaining_qty", "剩余数量"], ["uom", "单位"], ["schedule_date", "需求日期"], ["warehouse", "仓库"]];
 const specificColumns = [["material_request_item_idx", "物料需求明细行号"], ["item_code", "物料编码"], ["model", "型号"], ["color", "颜色"], ["article_code", "成品货号"], ["batch_no", "生产批次号"], ["order_qty", "订单数量"], ["issue_qty", "需求量"], ["uom", "单位"]];
 const numberFields = new Set(["qty", "issued_qty", "remaining_qty", "order_qty", "issue_qty"]);
 function readableFields(frappe, doctype) {
  return new Set((frappe.get_meta(doctype)?.fields || []).filter(df => !df.is_virtual && frappe.perm.has_perm("Material Request", df.permlevel || 0, "read")).map(df => df.fieldname));
 }
 function quantity(value, root) {
  const number = Number(value);
  if (value === null || value === undefined || value === "" || !Number.isFinite(number)) return "—";
  const fractional = number.toFixed(8).split(".")[1] || "";
  const precision = fractional.replace(/0+$/, "").length;
  return escape(root.format_number ? root.format_number(number, null, precision) : number.toLocaleString(undefined, {maximumFractionDigits:8}));
 }
 async function loadDetails(controller, doc) {
  if (!controller.allowed.has("items")) throw new Error(translateText("没有物料明细读取权限。"));
  const frappe = controller.root.frappe;
  controller.mrItemMetadata ||= frappe.model.with_doctype("Material Request Item").catch(error => {controller.mrItemMetadata = null; throw error;});
  await controller.mrItemMetadata;
  const native = (await frappe.call({method:"frappe.client.get", args:{doctype:"Material Request", name:doc.name}, silent:true})).message;
  if (!native) throw new Error(translateText("物料需求明细读取失败，请重试。"));
  const fields = readableFields(frappe, "Material Request Item");
  const rows = Array.isArray(native?.items) ? native.items : [];
  const available = field => fields.has(field) && rows.some(row => Object.hasOwn(row, field));
  const canCalculate = ["qty", "ordered_qty", "conversion_factor"].every(available);
  const columns = itemColumns.filter(([field]) => field === "warehouse" ? available("from_warehouse") || available("warehouse") : ["issued_qty", "remaining_qty"].includes(field) ? canCalculate : available(field)).map(([field,label]) => [field, field === "issued_qty" && issueTypes.includes(native.material_request_type) ? "已发料数量" : label]);
  const items = rows.map(row => {
   const item = Object.fromEntries(columns.filter(([field]) => !["issued_qty", "remaining_qty", "warehouse"].includes(field)).map(([field]) => [field, row[field]]));
   if (columns.some(([field]) => field === "warehouse")) item.warehouse = fields.has("from_warehouse") && row.from_warehouse || fields.has("warehouse") && row.warehouse || "";
   if (canCalculate) {
    const factor = Number(row.conversion_factor), requested = Number(row.qty), processed = Number(row.ordered_qty);
    item.issued_qty = factor > 0 && Number.isFinite(processed) ? processed / factor : null;
    item.remaining_qty = Number.isFinite(requested) && item.issued_qty !== null ? Math.max(0, requested - item.issued_qty) : null;
   }
   return item;
  });
  let specific = null;
  if (controller.allowed.has("custom_item_details") && Array.isArray(native?.custom_item_details) && native.custom_item_details.length) {
   controller.mrSpecificMetadata ||= frappe.model.with_doctype("MES Material Request Item Detail").catch(error => {controller.mrSpecificMetadata = null; throw error;});
   await controller.mrSpecificMetadata;
   const detailFields = readableFields(frappe, "MES Material Request Item Detail");
   const details = native.custom_item_details;
   const detailColumns = specificColumns.filter(([field]) => detailFields.has(field) && details.some(row => Object.hasOwn(row, field)));
   specific = {columns:detailColumns, items:details.map(row => Object.fromEntries(detailColumns.map(([field]) => [field, row[field]])))};
  }
  return {header:{modified:native?.modified}, columns, items, specific};
 }
 function rowExtra(controller, doc) {
  if (!controller.expandedDetails?.has(doc.name)) return "";
  const detail = controller.detailCache.get(doc.name);
  let contents = detail?.error ? escape(detail.error) : escape(controller.translate("正在读取物料明细…"));
  if (detail && !detail.error) {
   contents = detail.items.length && detail.columns.length ? engine.detailTable({columns:detail.columns, items:detail.items, escape, translate:controller.translate, wrapperClass:"dlp-purchase-detail-scroll", tableClass:"dlp-purchase-detail-table", textFields:["item_name", "warehouse"], textClass:"dlp-purchase-detail-text", columnClass:field => numberFields.has(field) ? "dlp-po-number" : "", format:(field,item) => numberFields.has(field) ? quantity(item[field], controller.root) : field === "schedule_date" ? escape(controller.root.frappe.datetime?.str_to_user?.(item[field]) || item[field] || "—") : escape(item[field] || "—")}) : escape(controller.translate("暂无可读物料明细"));
   if (detail.specific?.items.length && detail.specific.columns.length) contents += `<details class="dlp-mr-specific"><summary>${escape(controller.translate("物料具体明细"))} (${detail.specific.items.length})</summary>${engine.detailTable({columns:detail.specific.columns, items:detail.specific.items, escape, translate:controller.translate, wrapperClass:"dlp-purchase-detail-scroll", tableClass:"dlp-purchase-detail-table", columnClass:field => numberFields.has(field) ? "dlp-po-number" : "", format:(field,item) => numberFields.has(field) ? quantity(item[field], controller.root) : escape(item[field] || "—")})}</details>`;
  }
  return `<div class="dlp-purchase-expanded" role="region" aria-label="${escape(controller.translate("物料需求明细"))}">${contents}</div>`;
 }
 const columns = [["custom_odt","ODT",130],["custom_mes_issue_category","申请类别",130],["name","ERP申请号",240],["status","状态",110],["transaction_date","申请日期",110],["schedule_date","需求日期",110],["company","公司",160],["set_from_warehouse","来源仓库",260],["per_ordered","已发料/履行%",130],["custom_material_request_no","MES申请号",300],["owner","创建人",120]].map(([fieldname,label,width])=>({fieldname,label,width}));
 const grid = engine.create({doctype:"Material Request",columns,defaultColumns,preferenceVersion:1,migratePreferences:migrateColumns,backupMigratedPreferences:true,controllerKey:"dlpMaterialRequestGrid",routeClass:"dlp-material-request-grid-active",freezeUntil:"custom_mes_issue_category",numbers:["per_ordered"],dates:["transaction_date","schedule_date"],quickFields:["custom_odt","status"],searchFields:["name","custom_material_request_no"],extraFields:["material_request_type","modified"],onMount:controller=>{controller.mrView=canShowPending(controller)?pendingView:"all";controller.mrCounts={[pendingView]:null,[issuedView]:null,all:null};controller.mrCountVersion=0;mountColumnResize(controller);engine.mountRowDetails(controller,{rows:()=>controller.list.data,load:doc=>loadDetails(controller,doc),canExpand:()=>controller.allowed.has("items"),showExpandAll:false,active:()=>{const route=controller.root.frappe.get_route?.() || [];return String(route[0]).toLowerCase()==="list" && route[1]==="Material Request";}});},onRequestStart:controller=>{controller.mrCountVersion++;controller.mrCounts={[pendingView]:null,[issuedView]:null,all:null};renderViewCounts(controller);},onRows:controller=>{controller.invalidateRowDetails?.();refreshViewCounts(controller);},onCount:acceptCurrentCount,mountControls:mountViews,transformQuery:viewQuery,headerCellExtra:headerResizeHandle,rowExtra,renderSequence:(controller,doc)=>controller.allowed.has("items")?`<button type="button" class="dlp-purchase-expand" data-name="${escape(doc.name)}" aria-expanded="${Boolean(controller.expandedDetails?.has(doc.name))}" aria-label="${escape(controller.translate("展开 / 收起物料需求明细"))}">${controller.expandedDetails?.has(doc.name)?"⌄":"›"}</button>`:"",controls:[
  {fieldname:"custom_odt",fieldtype:"Data",label:translateText("ODT")},
  {fieldname:"custom_mes_issue_category",fieldtype:"Select",label:translateText("申请类别"),options:`\nconsumable\nraw_material\nsemi_finished\nunclassified\n${legacyTypes.map(type => `legacy:${type}`).join("\n")}`},
  {fieldname:"status",fieldtype:"Select",label:translateText("状态")},
  {fieldname:"search",fieldtype:"Data",label:translateText("ERP / MES申请号")},
  {fieldname:"from_date",fieldtype:"Date",label:translateText("申请日期起"),permission_field:"transaction_date"},
  {fieldname:"to_date",fieldtype:"Date",label:translateText("申请日期止"),permission_field:"transaction_date"},
  {fieldname:"need_from_date",fieldtype:"Date",label:translateText("需求日期起"),permission_field:"schedule_date"},
  {fieldname:"need_to_date",fieldtype:"Date",label:translateText("需求日期止"),permission_field:"schedule_date"}
 ],optionalFields:["custom_odt","custom_mes_issue_category","custom_material_request_no"],legacyDisplayFields:["custom_odt","custom_mes_issue_category"],optionLabels:{custom_mes_issue_category:categoryLabels},renderValue(field,doc,formatters,escape) {
  if(field === "custom_odt") return escape(formatters.allowed && !formatters.allowed.has(field) ? "" : doc.custom_odt || "");
  if(field === "custom_mes_issue_category") return escape((!formatters.allowed || formatters.allowed.has(field) ? (formatters.translate || (x=>x))(categories[doc.custom_mes_issue_category] || "") : "") || (formatters.allowed && !formatters.allowed.has("material_request_type") ? "" : (formatters.translate || (x=>x))(doc.material_request_type || "")));
 }});
 grid.categories = categories;
 if(typeof module === "object" && module.exports) module.exports = grid;
 root.DeepLinkERPMaterialRequestGrid = grid;
 grid.install(root);
})(typeof globalThis !== "undefined" ? globalThis : this);
