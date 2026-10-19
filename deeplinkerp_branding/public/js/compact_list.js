(function (root, factory) {
 const translateText = (text, args = []) => String((globalThis.__ || (value => value))(text)).replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");
 const businessDateFields = Object.fromEntries([
  ...['Material Request','Request for Quotation','Supplier Quotation','Purchase Order','Quotation','Sales Order'].map(doctype=>[doctype,'transaction_date']),
  ...['Purchase Receipt','Purchase Invoice','Payment Entry','Sales Invoice','Delivery Note','POS Invoice','POS Opening Entry','POS Closing Entry'].map(doctype=>[doctype,'posting_date']),
  ['OA Purchase Request','apply_date'],
 ]);
 const compactWidths={status:88,advance_payment_status:84,payment_progress:108,receipt_progress:100,receipt_action:128,payment_action:176,payment_state:112,state:112,action:160,custom_process_status:124,dlp_actions:96};
 function columnWidth(column,density,doctype) {
  if(!['Purchase Order','Purchase Receipt','Purchase Invoice','Payment Entry','Sales Order'].includes(doctype))return column.width;
  if(column.fieldname==='company')return Math.max(180,column.width);
  return density==='standard'?column.width:Math.min(column.width,compactWidths[column.fieldname] || column.width);
 }
 const engine = { create: config=>factory(config,engine), fitViewport, detailTable, invalidateExpandedDetails, mountRowDetails, businessDateFields, bindBusinessDateSort, installBusinessDateSort, columnWidth };
 function businessRoute(root) {
  const route=root.frappe.get_route?.() || [];
  return String(route[0]).toLowerCase()==='list'?`List/${route[1]}/${route[2] || 'List'}`:String(route[0] || '');
 }
 function businessEntries(root, trackRoutes=false) {
  const frappe=root.frappe;
  if (!frappe.dlpBusinessDateEntries) {
   const state={route:businessRoute(root),revision:0,sync(){const route=businessRoute(root);if(route!==this.route){this.route=route;this.revision++;}}};
   frappe.dlpBusinessDateEntries=state;
  }
  const state=frappe.dlpBusinessDateEntries;
  if(trackRoutes && !state.watching){frappe.router?.on('change',()=>state.sync());state.watching=true;}
  state.sync();return state;
 }
 // A route entry is shared by cached ListViews and standalone Pages. Native
 // refresh/filter/paging never creates another entry or rewrites a manual sort.
 function bindBusinessDateSort(list, root, owner, pageRoute, trackRoutes=true) {
  const date=businessDateFields[list.doctype || owner?.doctype];
  if (!date || (!pageRoute && list.view_name!=='List')) return;
  let binding=list.dlpBusinessDateSort;
  if (!binding) {
   binding=list.dlpBusinessDateSort={entry:null,enter(){
    const state=businessEntries(root,trackRoutes),active=state.route===(pageRoute || `List/${list.doctype}/List`);
    if (!active || this.entry===state.revision)return;
    this.entry=state.revision;
    const allowed=this.owner?.nativeAllowed || this.owner?.allowed;
    const readable=field=>allowed?allowed.has(field):field==='name'?root.frappe.perm.has_perm(list.doctype,0,'read'):(list.meta?.fields || []).some(df=>df.fieldname===field && !df.is_virtual && root.frappe.perm.has_perm(list.doctype,df.permlevel || 0,'read'));
    const field=readable(date)?date:readable('name')?'name':null;if(!field)return;
    list.sort_by=field;list.sort_order='desc';list.order_by=`${field} desc`;list.sort_selector?.set_value(field,'desc');list.last_args=null;
    if(this.owner){this.owner.providerOrderBy=`${field} desc`;this.owner.providerNativeOrder=undefined;this.owner.providerSortSource='native';this.owner.setPage(0);}
   }};
   if(list.get_args){const getArgs=list.get_args;list.get_args=function(...args){binding.enter();return getArgs.apply(this,args);};}
  }
  if(owner && binding.owner!==owner){binding.owner=owner;binding.entry=null;}
  binding.enter();return binding;
 }
 function installBusinessDateSort(root) {
  const settings=root.frappe?.listview_settings;if(!settings)return;
  for(const doctype of Object.keys(businessDateFields)) {
   const config=settings[doctype] || (settings[doctype]={});
   if(config.onload?.dlpBusinessDateSort)continue;
   const onload=config.onload;
   config.onload=function(list){if(!config.dlpCompactList)bindBusinessDateSort(list,root);return onload?.apply(this,arguments);};
   config.onload.dlpBusinessDateSort=true;
  }
 }
 // One page index and a bounded version cache shared by procurement list adapters.
 function mountRowDetails(owner, {rows, load, active=()=>true, canExpand=()=>true}) {
  owner.expandedDetails=new Set(); owner.detailCache=new Map(); owner.detailPending=new Map(); owner.detailRows=new Map(); owner.detailVersions=new Map();
  const queue=[],dirty=new Set();let live=0,frame,detailElements=new Map(),previousScope,pumping=false,needsRender=false;
  const version=doc=>String(doc?.modified || doc?.order_progress?.modified || '');
  const scope=()=>JSON.stringify([owner.page,owner.pageSize,owner.providerScope,owner.quick,owner.querySignature]);
  const render=()=>{owner.list.render_list();owner.list.set_rows_as_checked?.();};
  const eligible=doc=>doc && active() && canExpand(doc);
  const current=token=>eligible(owner.detailRows.get(token.doc.name)) && owner.expandedDetails.has(token.doc.name) && owner.detailPending.get(token.doc.name)===token && owner.requestId===token.requestId && scope()===token.scope && version(owner.detailRows.get(token.doc.name))===token.version;
  function cancel(name) {const token=owner.detailPending.get(name);owner.detailPending.delete(name);token?.done();}
  function flush() {
   frame=undefined;const names=[...dirty];dirty.clear();if(!active())return;
   const template=owner.root.document?.createElement?.('template');
   if(!template || !owner.list.get_list_row_html){render();return;}
   for(const name of names){
    const doc=owner.detailRows.get(name),region=detailElements.get(name)?.querySelector('.dlp-purchase-expanded');
    if(!doc || !region || !owner.expandedDetails.has(name))continue;
    template.innerHTML=owner.list.get_list_row_html(doc);
    const replacement=template.content.querySelector('.dlp-purchase-expanded');
    // Keep the native row, checkbox, focus and selection cursor intact.
    if(replacement)region.outerHTML=replacement.outerHTML;
   }
   updateControls();
  }
  function schedule(name) {
   dirty.add(name);if(frame!==undefined)return;
   frame=owner.root.requestAnimationFrame?owner.root.requestAnimationFrame(flush):setTimeout(flush,0);
  }
  function finish(token,detail,error,asynchronous) {
   const accepted=current(token),name=token.doc.name;
   if(accepted)owner.detailCache.set(name,error?{error:error.message || '明细读取失败，请重试。'}:detail?.header?.modified && token.version && String(detail.header.modified)!==token.version?{error:'明细已更新，请刷新列表后查看。'}:detail);
   if(owner.detailPending.get(name)===token)owner.detailPending.delete(name);
   live--;token.done();pump();if(accepted && asynchronous)schedule(name);
  }
  function pump() {
   if(pumping)return;pumping=true;
   while(live<4 && queue.length){
    const token=queue.shift();if(!current(token)){if(owner.detailPending.get(token.doc.name)===token)cancel(token.doc.name);else token.done();continue;}
    live++;
    try {const value=load(token.doc);if(value?.then)value.then(detail=>finish(token,detail,null,true),error=>finish(token,null,error,true));else finish(token,value,null,false);}
    catch(error){finish(token,null,error,false);}
   }pumping=false;
  }
  function open(doc,retry=false) {
   const name=doc.name;if(owner.expandedDetails.has(name))return owner.detailPending.get(name)?.promise;
   owner.expandedDetails.add(name);if(retry && owner.detailCache.get(name)?.error)owner.detailCache.delete(name);
   if(owner.detailCache.has(name))return;
   const token={doc,version:version(doc),requestId:owner.requestId,scope:scope()};
   token.promise=new Promise(resolve=>token.done=resolve);owner.detailPending.set(name,token);queue.push(token);pump();return token.promise;
  }
  function updateControls() {
   const available=[...owner.detailRows.values()].filter(eligible);
   owner.$rowDetailControls?.toggle(Boolean(available.length));
   owner.$rowDetailControls?.find('[data-row-details="expand"]').prop('disabled',!available.some(doc=>!owner.expandedDetails.has(doc.name)));
   owner.$rowDetailControls?.find('[data-row-details="collapse"]').prop('disabled',!owner.expandedDetails.size);
  }
  owner.reconcileRowDetails=()=>{
   const signature=scope();if(previousScope!==undefined && signature!==previousScope)owner.cancelPendingRowDetails();previousScope=signature;
   const next=new Map(rows().map(doc=>[doc.name,doc]));
   for(const [name,previous] of owner.detailVersions)if(!next.has(name) || version(next.get(name))!==previous){owner.expandedDetails.delete(name);owner.detailCache.delete(name);cancel(name);}
   for(const [name,token] of owner.detailPending)if(token.requestId!==owner.requestId || !eligible(next.get(name))){owner.expandedDetails.delete(name);cancel(name);}
   for(const name of owner.expandedDetails)if(!eligible(next.get(name)))owner.expandedDetails.delete(name);
   owner.detailRows=next;owner.detailVersions=new Map([...next].map(([name,doc])=>[name,version(doc)]));
   updateControls();
  };
  owner.invalidateRowDetails=names=>{
   const targets=names==null?new Set(owner.detailRows.keys()):new Set(Array.isArray(names)?names:[names]);
   for(const name of targets){owner.expandedDetails.delete(name);owner.detailCache.delete(name);cancel(name);}updateControls();
  };
  owner.cancelPendingRowDetails=()=>{
   // Forget queued work immediately; live calls retain their slots until settled.
   needsRender ||= Boolean(owner.expandedDetails.size);
   owner.expandedDetails.clear();for(const name of owner.detailPending.keys())cancel(name);queue.length=0;dirty.clear();updateControls();
  };
  owner.restoreRowDetails=()=>{
   if(!active())return;
   if(needsRender){needsRender=false;render();}
   else {updateControls();owner.updateRowDetailViewport?.();}
  };
  owner.collapseAllRowDetails=()=>{owner.cancelPendingRowDetails();render();};
  owner.expandAllRowDetails=()=>{
   owner.reconcileRowDetails();const pending=[];let changed=false;
   for(const doc of owner.detailRows.values())if(eligible(doc) && !owner.expandedDetails.has(doc.name)){changed=true;pending.push(open(doc));}
   if(changed)render();return Promise.all(pending);
  };
  owner.toggleRowDetails=name=>{
   owner.reconcileRowDetails();const doc=owner.detailRows.get(name);if(!eligible(doc))return;
   if(owner.expandedDetails.has(name)){owner.expandedDetails.delete(name);cancel(name);render();return;}
   const pending=open(doc,true);render();return pending;
  };
  const setPage=owner.setPage;
  if(setPage)owner.setPage=function(...args){const expanded=owner.expandedDetails.size;owner.cancelPendingRowDetails();const value=setPage.apply(this,args);if(expanded && active())render();return value;};
  owner.mountRowDetailControls=()=>{
   if(!owner.$rowDetailControls && owner.$toolbar){
    owner.$rowDetailControls=owner.root.$(`<span class="dlp-purchase-detail-actions"><button type="button" class="btn btn-default btn-sm" data-row-details="expand">${translateText("展开")}</button><button type="button" class="btn btn-default btn-sm" data-row-details="collapse">${translateText("收起")}</button></span>`).appendTo(owner.$toolbar);
    owner.$rowDetailControls.on('click.dlpRowDetails','[data-row-details="expand"]',()=>owner.expandAllRowDetails()).on('click.dlpRowDetails','[data-row-details="collapse"]',()=>owner.collapseAllRowDetails());
   }updateControls();
  };
  owner.afterRowDetailsRender=()=>{
   needsRender=false;
   detailElements=new Map([...(owner.list.$result?.[0]?.querySelectorAll?.('[data-dlp-detail-name]') || [])].map(node=>[node.getAttribute('data-dlp-detail-name'),node]));
   owner.mountRowDetailControls();
   const result=owner.list.$result?.[0],host=owner.root;
   if(!result?.style || !host.requestAnimationFrame)return;
   const viewport=owner.list.$result.parent?.('.result-container')?.[0];if(!viewport)return;
   if(!owner.updateRowDetailViewport){
    let resizeFrame;const measure=()=>{resizeFrame=undefined;const style=host.getComputedStyle?.(viewport) || {},width=`${Math.max(0,Math.floor(viewport.clientWidth-(parseFloat(style.paddingLeft)||0)-(parseFloat(style.paddingRight)||0)-28))}px`;if(result.style.getPropertyValue?.('--dlp-purchase-detail-width')!==width)result.style.setProperty('--dlp-purchase-detail-width',width);};
    const schedule=()=>{if(resizeFrame===undefined)resizeFrame=host.requestAnimationFrame(measure);};
    const observer=host.ResizeObserver?new host.ResizeObserver(schedule):null;observer?.observe(viewport);host.addEventListener?.('resize',schedule);owner.updateRowDetailViewport=schedule;
   }owner.updateRowDetailViewport();
  };
  owner.list.$result?.on?.('click.dlpRowDetails','.dlp-purchase-expand',event=>{event.preventDefault();event.stopPropagation();return owner.toggleRowDetails(owner.root.$(event.currentTarget).attr('data-name'));});
  owner.reconcileRowDetails();
 }
 function invalidateExpandedDetails(expanded,rows,modified=detail=>detail.header?.modified) {
  const byName=new Map(rows.map(row=>[row.name,row]));
  for(const [name,detail] of expanded || []) {const row=byName.get(name);if(!row || (row.modified && row.modified!==modified(detail)))expanded.delete(name);}
 }
 function detailTable({columns, items=[], escape, translate=x=>x, format, wrapperClass='', tableClass='', columnClass=()=>'', textFields=[], textClass=''}) {
  const compactText=wrapperClass.split(/\s+/).includes('dlp-purchase-detail-scroll') && tableClass.split(/\s+/).includes('dlp-purchase-detail-table');
  const content=(field,item)=>{const value=format?.(field,item) ?? escape(item[field] ?? '—'),wrapClass=textFields.includes(field)?textClass:compactText && ['item_name','warehouse','uom'].includes(field)?'dlp-purchase-detail-text':'';return wrapClass?`<span class="${escape(wrapClass)}">${value}</span>`:value;};
  return `<div class="${escape(wrapperClass)}"><table class="table ${escape(tableClass)}"><thead><tr>${columns.map(([field,label],i)=>`<th data-fieldname="${escape(field)}" class="${escape(columnClass(field,i))}">${escape(translate(label))}</th>`).join('')}</tr></thead><tbody>${items.map(item=>`<tr>${columns.map(([field],i)=>`<td data-fieldname="${escape(field)}" title="${escape(item[field] ?? '—')}" class="${escape(columnClass(field,i))}">${content(field,item)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
 }
 // Opt-in sizing for surfaces that retain internal scrolling.
 function fitViewport(owner, options) {
  if (!owner) return;
  if (!options.active) return owner.stopTableViewport?.();
  if (owner.updateTableViewport) return owner.updateTableViewport();
  const {root:host,scrollElement:result,layoutTailElement:list,property,headerSelector,rowSelector,observeTargets=[],minimumRows=1}=options;
  if (!result?.getBoundingClientRect || !list?.getBoundingClientRect || !host.requestAnimationFrame || !host.getComputedStyle) return;
  let frame, stopped=false, observedHeader, observedRow;
  const schedule=()=>{if(!stopped && frame===undefined)frame=host.requestAnimationFrame(measure);};
  const observer=host.ResizeObserver ? new host.ResizeObserver(schedule) : null;
  const number=value=>parseFloat(value)||0;
  function measure() {
   frame=undefined;
   if(stopped)return;
   const ancestors=[];
   for(let node=result.parentElement;node;node=node.parentElement)ancestors.push(node);
   // Normalize geometry to the unscrolled layout so an outer scroll cannot grow the table.
   const top=result.getBoundingClientRect().top+ancestors.reduce((sum,node)=>sum+(node.scrollTop||0),0);
   let bottom=host.innerHeight,spacing=number(host.getComputedStyle(list).marginBottom),reachedScroller=false;
   ancestors.forEach((node,index)=>{
    const style=host.getComputedStyle(node),scrollable=/^(auto|scroll|overlay)$/.test(style.overflowY);
    if(scrollable){
     const above=ancestors.slice(index+1).reduce((sum,parent)=>sum+(parent.scrollTop||0),0);
     bottom=Math.min(bottom,node.getBoundingClientRect().top+(node.clientTop||0)+node.clientHeight+above);
    }
    if(node!==list && !reachedScroller)spacing+=number(style.paddingBottom)+(scrollable?0:number(style.marginBottom));
    reachedScroller ||= scrollable;
   });
   const header=result.querySelector(headerSelector),row=result.querySelector(rowSelector);
   for(const [previous,current] of [[observedHeader,header],[observedRow,row]]){
    if(previous!==current){if(previous)observer?.unobserve?.(previous);if(current)observer?.observe(current);}
   }
   observedHeader=header;observedRow=row;
   const rowHeight=row ? number(host.getComputedStyle(row).minHeight)||row.getBoundingClientRect?.().height||0 : 0;
   const minimum=(header?.getBoundingClientRect().height||0)+rowHeight*Math.max(1,minimumRows)+Math.max(0,result.offsetHeight-result.clientHeight);
   const tail=Math.max(0,list.getBoundingClientRect().bottom-result.getBoundingClientRect().bottom);
   const height=`${Math.floor(Math.max(minimum,bottom-top-tail-spacing))}px`;
   if(result.style.getPropertyValue(property)!==height)result.style.setProperty(property,height);
  }
  owner.updateTableViewport=schedule;
  owner.stopTableViewport=()=>{
   stopped=true;observer?.disconnect();
   if(frame!==undefined)host.cancelAnimationFrame(frame);
   host.removeEventListener('resize',schedule);result.style.removeProperty(property);
   owner.updateTableViewport=null;owner.stopTableViewport=null;
  };
  for(const node of new Set([list,result,...observeTargets]))if(node)observer?.observe(node);
  host.addEventListener('resize',schedule);
  schedule();
 }
 if (typeof module === "object" && module.exports) module.exports = engine;
 root.DeepLinkERPCompactList = engine;
 if(root.document && root.frappe)installBusinessDateSort(root);
})(typeof globalThis !== "undefined" ? globalThis : this, function (config, shared) {
 "use strict";
 const translateText = (text, args = []) => String((globalThis.__ || (value => value))(text)).replace(/\{(\d+)\}/g, (_, i) => args[i] ?? "");
 const DOCTYPE = config.doctype;
	const COLUMNS = config.columns;
	const NUMBERS = new Set(config.numbers || []);
	const DATES = new Set(config.dates || []);
	const callRequests = new WeakMap();
	const responseRequests = new WeakMap();
	const installed = new WeakSet();

	function escapeHTML(value) {
		return String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
	}

	function allowedFields(meta, canRead, standardFields = []) {
		const allowed = new Set(canRead(0) ? standardFields : []);
		for (const df of meta?.fields || []) {
			if (!df.is_virtual && canRead(df.permlevel || 0)) allowed.add(df.fieldname);
		}
		return allowed;
	}

	function preferenceKey(site, user) {
		return ["dlp-list", site, user, DOCTYPE].map((part) => encodeURIComponent(String(part || ""))).join(":");
	}

	function normalizePreferences(value, allowed, definitions = COLUMNS) {
		const migrate = definitions === COLUMNS ? config.migratePreferences : config.provider?.migratePreferences;
		if (migrate) value = migrate(value, allowed);
		const candidates = definitions.filter((col) => allowed.has(col.fieldname)).map((col) => col.fieldname);
		const defaults = definitions === COLUMNS ? config.defaultColumns : config.provider?.defaultColumns;
		const selected = Array.isArray(value?.columns) ? value.columns : (defaults || candidates);
		const columns = [...new Set(selected.filter((field) => candidates.includes(field)))];
		if (!columns.includes("name") && candidates.includes("name")) columns.unshift("name");
		const version = definitions === COLUMNS ? config.preferenceVersion : config.provider?.preferenceVersion;
		return { density: value?.density === "standard" ? "standard" : "tight", columns, ...(version ? { version } : {}) };
	}

	function selectOptions(control, field, translate) {
		const options = control.options || `\n${field?.options || ""}`;
		const labels = config.optionLabels?.[control.fieldname];
		if (!labels && !control.emptyLabel) return options;
		const values = typeof options === "string" ? options.split("\n") : options;
		return [...new Set(values)].map(value => typeof value === "object" ? value : ({ value, label: translate(value ? labels?.[value] || value : control.emptyLabel || "") }));
	}

	function fieldName(field) {
		if (Array.isArray(field)) return field[0];
		if (typeof field !== "string") return null;
		// Native ListView emits qualified columns. Linked aliases need their own metadata/permission scope.
		const clean = field.replace(/`/g, "");
		const prefix = clean.startsWith("tab") ? `tab${DOCTYPE}.` : `${DOCTYPE}.`;
		const name = clean.startsWith(prefix) ? clean.slice(prefix.length) : clean;
		return /^[\w]+$/.test(name) ? name : null;
	}

	function buildQuery(nativeArgs, quick, allowed) {
		const filters = (nativeArgs.filters || []).map((filter) => Array.isArray(filter) ? filter.slice(0, 4) : filter);
		const or_filters = (nativeArgs.or_filters || []).map((filter) => Array.isArray(filter) ? filter.slice(0, 4) : filter);
		const add = (field, operator, value) => {
			if (allowed.has(field) && value !== undefined && value !== null && value !== "") filters.push([DOCTYPE, field, operator, value]);
		};
		add(config.dateField || "transaction_date", ">=", quick.from_date);
		add(config.dateField || "transaction_date", "<=", quick.to_date);
		for (const field of config.quickFields || []) add(field, "=", quick[field]);
		const search = String(quick.search || "").trim();
		if (search && or_filters.length) throw new Error(translateText("Quick search cannot be combined with existing OR filters."));
		if (search) {
			for (const field of config.searchFields || ["name"]) {
				if (allowed.has(field)) or_filters.push([DOCTYPE, field, "like", `%${search}%`]);
			}
		}
		let order_by = nativeArgs.order_by;
		if (typeof order_by === "string") {
			const unavailable = new Set((config.legacyDisplayFields || []).filter((field) => !allowed.has(field)));
			const parts = order_by.split(",").map((part) => part.trim());
			const retained = parts.filter((part) => !unavailable.has(fieldName(part.replace(/\s+(?:asc|desc)\s*$/i, ""))));
			if (retained.length !== parts.length) order_by = retained.join(", ") || (allowed.has("name") ? "name desc" : undefined);
		}
		return { ...nativeArgs, order_by, fields: (nativeArgs.fields || []).filter((field) => allowed.has(fieldName(field))), filters, or_filters };
	}

	function buildRequests(args, columns, allowed) {
		const common = { doctype: DOCTYPE, filters: args.filters, or_filters: args.or_filters };
		const orderGrouping = fieldName(args.group_by) === "name";
		const exportFields = [];
		for (const field of columns) {
			if (!allowed.has(field)) continue;
			if (field === "advance_paid" && !allowed.has("party_account_currency")) continue;
			exportFields.push(field);
			if (field === "advance_paid") exportFields.push("party_account_currency");
		}
		const summary = config.moneySummary && allowed.has("currency") && allowed.has("grand_total") ? (orderGrouping ? {
			...common, fields: ["name", "currency", "grand_total", "party_account_currency", "advance_paid"].filter((field) => allowed.has(field)),
			group_by: args.group_by, order_by: args.order_by, start: 0, limit_page_length: 0,
		} : {
			...common, fields: ["currency"], group_by: "currency", aggregate_function: "sum",
			aggregate_on_doctype: DOCTYPE, aggregate_on_field: "grand_total", order_by: "currency asc", start: 0, limit_page_length: 0,
		}) : null;
		return {
			count: { ...common, distinct: true, limit: 0 },
			summary,
			export: { ...common, fields: exportFields, ...(args.group_by ? { group_by: args.group_by } : {}), order_by: args.order_by, start: 0, file_format_type: "Excel", title: DOCTYPE },
		};
	}

	function currencyTotals(rows) {
		const seen = new Set();
		const totals = new Map();
		for (const row of rows) {
			if (!row.name || seen.has(row.name)) continue;
			seen.add(row.name);
			const amount = Number(row.grand_total);
			if (!Number.isFinite(amount)) continue;
			const currency = row.currency || "";
			totals.set(currency, (totals.get(currency) || 0) + amount);
		}
		return [...totals].map(([currency, amount]) => ({ currency, _aggregate_column: amount }));
	}

	function formatNumber(root, value, field) {
		const configured = config.moneyPrecision ?? root.frappe?.boot?.sysdefaults?.currency_precision;
		const currencyPrecision = configured !== undefined && configured !== null && configured !== "" ? Number(configured) : 2;
		const precision = field.startsWith("per_") ? 2 : (Number.isInteger(currencyPrecision) && currencyPrecision >= 0 && currencyPrecision <= 9 ? currencyPrecision : 2);
		return root.format_number ? root.format_number(value, null, precision) : value.toLocaleString(undefined, { minimumFractionDigits: field.startsWith("per_") ? 0 : precision, maximumFractionDigits: precision });
	}

	function renderValue(field, doc, formatters = {}) {
		const custom = config.renderValue?.(field, doc, formatters, escapeHTML);
		if (custom !== undefined) return custom;
		const value = doc[field];
		if (value === undefined || value === null || value === "") return "—";
		if (NUMBERS.has(field)) {
			const number = Number(value);
			if (!Number.isFinite(number)) return "—";
			const percentage = field.startsWith("per_");
			const formatted = percentage && number === 0 ? "0" : escapeHTML(formatters.number ? formatters.number(number, field, doc) : number.toLocaleString(undefined, { minimumFractionDigits: percentage ? 0 : 2, maximumFractionDigits: 2 }));
			if (field.startsWith("per_")) return `${formatted}%`;
			const currency = doc[field === "advance_paid" ? "party_account_currency" : "currency"];
			return formatted + (currency ? ` ${escapeHTML(currency)}` : "");
		}
		if (DATES.has(field) && formatters.date) return escapeHTML(formatters.date(value));
		if (["status", "advance_payment_status", "payment_state", "state"].includes(field) && formatters.translate) return escapeHTML(formatters.translate(value));
		return escapeHTML(value);
	}

	function isNativeList(list) {
		return list?.doctype === DOCTYPE && list.view_name === "List" && (!list.view || list.view === "List");
	}

	function isListRoute(frappe) {
		const route = frappe.get_route?.() || [];
		return String(route[0]).toLowerCase() === "list" && route[1] === DOCTYPE && (!route[2] || String(route[2]).toLowerCase() === "list");
	}

	function requestKey(args) {
		return JSON.stringify(args);
	}

	function providerActive(controller) { return Boolean(config.provider && (config.provider.alwaysActive || controller.pageSurface || controller.providerScope !== "orders")); }
	function providerReadonly(controller) { return providerActive(controller) && (controller.pageSurface || typeof config.providerSelectable !== "function"); }
	function rowSelectable(controller, doc) { return !providerActive(controller) || (!providerReadonly(controller) && config.providerSelectable(doc)); }
	function displayColumns(controller) { return providerActive(controller) ? config.provider.columns : COLUMNS; }
	function displayPermissions(controller) { return providerActive(controller) ? controller.providerAllowed : controller.displayAllowed; }
	function currentRows(controller) { return providerActive(controller) ? controller.providerRows : controller.list.data; }
	function mountDisclosureRowGuard(controller) {
		const result=controller.list.$result?.[0];
		if (!config.purchaseChrome || !result?.addEventListener) return;
		let attached=false;
		const capture=event=>{
			const summary=event.target.closest?.('summary');
			if(summary?.closest('details') && summary.closest('.dlp-po-grid-row'))event.stopPropagation();
		};
		controller.syncDisclosureRowGuard=active=>{
			if(attached===active)return;
			result[active?'addEventListener':'removeEventListener']('click',capture,true);attached=active;
		};
	}

	function mount(list, root) {
		if (!isNativeList(list)) return null;
		if (list[config.controllerKey]) {
			list[config.controllerKey].activate();
			return list[config.controllerKey];
		}
		const frappe = root.frappe;
		shared.bindBusinessDateSort(list, root, undefined, undefined, false);
		const allowed = allowedFields(list.meta, (level) => frappe.perm.has_perm(DOCTYPE, level, "read"), frappe.model.std_fields_list);
		const missing = (config.optionalFields || []).filter((field) => !(list.meta?.fields || []).some((df) => df.fieldname === field));
		const displayAllowed = new Set(allowed);
		if (frappe.perm.has_perm(DOCTYPE, 0, "read")) for (const field of config.computedFields || []) displayAllowed.add(field);
		if (frappe.perm.has_perm(DOCTYPE, 0, "read")) {
			for (const field of config.legacyDisplayFields || []) if (missing.includes(field)) displayAllowed.add(field);
		}
		if (missing.length) frappe.show_alert?.({ message: (root.__ || ((x) => x))("申请来源字段尚未安装；当前显示可用的原生申请信息。"), indicator: "orange" });
		const key = preferenceKey(frappe.boot?.sitename || root.location?.host, frappe.session?.user);
		let saved;
		try { saved = JSON.parse(root.localStorage?.getItem(key) || "null"); } catch (_) { /* Browsers may disable storage. */ }
		const originals = {};
		for (const name of ["get_args", "get_call_args", "no_change", "prepare_data", "reset_defaults", "get_header_html", "get_list_row_html", "render_header", "render_list", "render_count", "toggle_result_area", "on_filter_change", "process_document_refreshes", "debounced_refresh", "get_checked_items", "set_rows_as_checked", "on_row_checked", "before_render", "after_render"]) originals[name] = list[name];
		const providerAllowed = new Set(displayAllowed);
		for (const field of config.provider?.virtualFields || []) providerAllowed.add(field);
		let providerSaved;
		try { providerSaved = JSON.parse(root.localStorage?.getItem(`${key}:unified`) || "null"); } catch (_) { /* Local preferences are optional. */ }
		if (!providerSaved && saved && config.provider) providerSaved = config.provider.inheritPreferences ? saved : { ...saved, columns: [...(saved.columns || []), ...(config.provider.newColumns || [])] };
		if(config.backupMigratedPreferences) {
			for(const [storageKey,value,definitions,permissions] of [[key,saved,COLUMNS,displayAllowed],[`${key}:unified`,providerSaved,config.provider?.columns,providerAllowed]]) {
				if(!value || !definitions || normalizePreferences(value,permissions,definitions).version===value.version)continue;
				try {const backupKey=`${storageKey}:backup:${value.version ?? 'legacy'}`;if(root.localStorage?.getItem(backupKey)==null)root.localStorage?.setItem(backupKey,JSON.stringify(value));}catch(_){/* Browser storage is optional. */}
			}
		}
		const controller = {
			list, root, allowed, displayAllowed, originals, quick: {}, controls: {}, resetting: false, preferences: config.provider?.alwaysActive ? normalizePreferences(providerSaved, providerAllowed, config.provider.columns) : normalizePreferences(saved, displayAllowed),
			providerAllowed, providerScope: "orders", providerRows: [], providerPayload: null, providerOrderBy: config.defaultSort,
			nativePreferences: normalizePreferences(saved, displayAllowed), providerPreferences: config.provider ? normalizePreferences(providerSaved, providerAllowed, config.provider.columns) : null,
			page: 0, pageSize: 100, requestId: 0, querySignature: null, total: null, summary: [], nativeControlFields: new Set(),
			translate: root.__ || ((label) => label),
			setPage(page) {
				config.onPageChange?.(this);
				this.page = Math.max(0, Number(page) || 0);
				list.start = this.page * this.pageSize;
				list.page_length = this.pageSize;
				list.last_args = null;
			},
			activate() {
				list.dlpBusinessDateSort?.enter();
				this.syncDisclosureRowGuard?.(isListRoute(frappe));
				root.document?.body?.classList.toggle(config.routeClass, isListRoute(frappe));
				root.document?.body?.classList.toggle(`${config.routeClass}-readonly`, isListRoute(frappe) && providerReadonly(this));
				if (isListRoute(frappe) && providerActive(this)) config.provider.onActivate?.(this);
				this.restoreRowDetails?.();
			},
			setProviderScope(scope, refresh = true) {
				if (!config.provider || !["all", "orders", "oa"].includes(scope)) return;
				if (providerActive(this)) this.providerPreferences = this.preferences; else this.nativePreferences = this.preferences;
				list.clear_checked_items?.();
				this.providerScope = scope;
				this.preferences = providerActive(this) ? this.providerPreferences : this.nativePreferences;
				this.providerRows = []; this.providerPayload = null; list.data = [];
				this.total = null; this.summary = []; this.querySignature = null; this.requestId++;
				this.setPage(0); this.savePreferences(); this.activate();
				this.$providerScope?.val(scope);
				config.onScopeChange?.(this);
				this.$providerControls?.toggle(providerActive(this));
				for (const field of config.quickFields || []) if (field !== "company") this.controls[field]?.$wrapper?.toggle(!providerReadonly(this));
				if (providerReadonly(this)) { list.page?.hide_actions_menu?.(); list.page?.clear_primary_action?.(); }
				// Do not leave readonly OA rows onscreen after native actions become available.
				list.render_list();
				if (refresh) return this.refresh();
			},
			savePreferences() {
				this.preferences = normalizePreferences(this.preferences, displayPermissions(this), displayColumns(this));
				try { root.localStorage?.setItem(providerActive(this) ? `${key}:unified` : key, JSON.stringify(this.preferences)); } catch (_) { /* Rendering must work without storage. */ }
				list.$frappe_list?.toggleClass("dlp-po-standard", this.preferences.density === "standard");
			},
			setColumns(columns, notify=true) {
				this.preferences.columns = columns;
				this.savePreferences();
				const hadSelection = Boolean(list.$checks?.length);
				list.render_list();
				list.set_rows_as_checked?.();
				// Native restoration calls on_row_checked itself only when there were checked elements.
				if (!hadSelection) list.on_row_checked?.();
				if(notify)return config.onColumnsChange?.(this);
			},
			...filterActions(),
			async exportCurrent() {
				if (providerActive(this)) return config.provider.exportCurrent(this);
				if (!frappe.model.can_export?.(DOCTYPE)) throw new Error(translateText("当前用户没有导出权限。"));
				const args = buildRequests(list.get_args(), this.preferences.columns, allowed).export;
				if (!root.DeepLinkERPPurchaseOrderExport?.exportExcel) await frappe.require("/assets/deeplinkerp_branding/js/purchase_order_export.js");
				if (!root.DeepLinkERPPurchaseOrderExport?.exportExcel) throw new Error(translateText("Excel 导出组件加载失败，请刷新页面后重试。"));
				await root.DeepLinkERPPurchaseOrderExport.exportExcel(root, args, config.exportOptions?.(args, this));
			},
			refresh() {
				this.activate();
				list.last_args = null;
				return list.refresh();
			},
			async refreshFilters() {
				if (!list.load_lib) return this.refresh();
				// Native throttle returns the previous promise for a trailing call.
				// Wait for the actual dispatch instead, without bypassing its throttle.
				let resolve, reject;
				const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
				const pending = this.pendingFilterRefresh = { resolve, reject };
				try {
					const refresh = this.refresh();
					const dispatched = pending.request;
					Promise.resolve(refresh).catch(error => { if (dispatched) reject(error); });
					return await promise;
				} finally {
					if (this.pendingFilterRefresh === pending) this.pendingFilterRefresh = null;
				}
			},
		};
		list[config.controllerKey] = controller;
		shared.bindBusinessDateSort(list, root, controller, undefined, false);
		mountDisclosureRowGuard(controller);
		config.onMount?.(controller);
		controller.setPage(0);
		list.selected_page_count = 100;
		for (const field of new Set([...COLUMNS.map((col) => col.fieldname), ...(config.extraFields || []), list.workflow_state_fieldname])) {
			if (allowed.has(field) && !list.fields.some((entry) => fieldName(entry) === field)) list.fields.push([field, DOCTYPE]);
		}

		list.get_args = function () {
			if (providerActive(controller)) {
				let args;
				try {
					const nativeArgs = originals.get_args.call(this);
					args = config.provider.getArgs?.(controller, nativeArgs) || config.provider.request(controller, nativeArgs).args;
				}
				catch (error) { if (config.provider.onQueryError?.(controller, error)) return this.get_args(); throw error; }
				const { start, page_length, ...scope } = args;
				const signature = JSON.stringify(scope);
				if (controller.querySignature !== null && signature !== controller.querySignature) {
					controller.setPage(0); controller.total = null; controller.providerPayload = null;
					args.start = 0;
				}
				controller.querySignature = signature;
				args.start = controller.page * controller.pageSize;
				args.page_length = controller.pageSize;
				return args;
			}
			const nativeArgs = originals.get_args.call(this);
			if (controller.quick.search && nativeArgs.or_filters?.length) {
				controller.quick.search = "";
				controller.searchControl?.$input?.val("");
				frappe.show_alert?.({ message: controller.translate(config.searchConflictMessage || "现有 OR 筛选已保留；请清除这些条件后使用订单/供应商搜索。"), indicator: "orange" });
			}
			const baseArgs = buildQuery(nativeArgs, controller.quick, allowed);
			const args = config.transformQuery?.(baseArgs, controller) || baseArgs;
			const signature = JSON.stringify([args.filters, args.or_filters, args.order_by]);
			if (controller.querySignature !== null && controller.querySignature !== signature) {
				controller.setPage(0);
				controller.total = null;
				controller.summary = [];
			}
			controller.querySignature = signature;
			args.start = controller.page * controller.pageSize;
			args.page_length = controller.pageSize;
			this.start = args.start;
			this.page_length = args.page_length;
			return args;
		};
		list.get_call_args = function () {
			const call = originals.get_call_args.call(this);
			if (providerActive(controller)) { call.method = config.provider.request(controller, originals.get_args.call(this)).method; call.args = this.get_args(); }
			const request = { id: null, key: requestKey(call.args) };
			callRequests.set(call, request);
			const callback = call.callback;
			call.callback = (response) => {
				responseRequests.set(response, request);
				try { callback?.(response); } catch (error) { finishFilterRequest(request, error); throw error; }
			};
			const error = call.error, always = call.always;
			call.error = (...args) => {
				finishFilterRequest(request, args[0] instanceof Error ? args[0] : new Error(controller.translate('筛选更新失败，请重试。')));
				return error?.(...args);
			};
			call.always = response => {
				if (!response || response.exc || response.session_expired) finishFilterRequest(request, new Error(controller.translate('筛选更新失败，请重试。')));
				return always?.(response);
			};
			return call;
		};
		list.no_change = function (call) {
			const unchanged = originals.no_change.call(this, call);
			const request = callRequests.get(call), pending = controller.pendingFilterRefresh;
			// Only the native dispatch gate can distinguish duplicate checks from forced refreshes.
			if (!unchanged && request) {
				request.id = ++controller.requestId;
				controller.lastDispatchedRequest = request;
				config.onRequestStart?.(controller);
			}
			if (pending && !pending.request && request) {
				const dispatched = unchanged ? controller.lastDispatchedRequest : request;
				if (dispatched?.key === request.key) {
					pending.request = dispatched; dispatched.filterRefresh = pending;
					if (dispatched.complete) finishFilterRequest(dispatched, dispatched.error);
				} else pending.reject(new Error(controller.translate('筛选更新失败，请重试。')));
			}
			return unchanged;
		};
		list.prepare_data = function (response) {
			const request = responseRequests.get(response);
			controller.renderingRequest = request;
			// Compare the current filters too: a pending throttled refresh may not have issued its new request yet.
			const current = this.get_args();
			if (request && (request.id !== controller.requestId || request.key !== requestKey(current))) return;
			if (providerActive(controller)) {
				controller.providerPayload = response.message || {};
				controller.providerRows = controller.providerPayload.rows || [];
				controller.total = Number(controller.providerPayload.total_count || 0);
				this.total_count = controller.total;
				// Only explicitly eligible provider documents enter native selection/actions.
				this.data = controller.providerRows.filter(doc => rowSelectable(controller, doc));
				controller.reconcileRowDetails?.();
				config.provider.onPayload?.(controller);
				return;
			}
			const start = this.start;
			this.start = 0;
			originals.prepare_data.call(this, response);
			this.start = start;
			controller.reconcileRowDetails?.();
			config.onRows?.(controller);
		};
		list.reset_defaults = function () {
			originals.reset_defaults.call(this);
			this.start = controller.page * controller.pageSize;
			this.page_length = controller.pageSize;
			this.selected_page_count = controller.pageSize;
			finishFilterRequest(controller.renderingRequest);
		};
		list.on_filter_change = function (...args) {
			if (controller.resetting) return;
			controller.setPage(0);
			return originals.on_filter_change?.apply(this, args);
		};
		list.get_checked_items = function (...args) {
			if (providerReadonly(controller)) return [];
			const checked = originals.get_checked_items?.apply(this, args) || [];
			if (!providerActive(controller)) return checked;
			const names = new Set(this.data.map(doc => doc.name));
			return checked.filter(doc => names.has(typeof doc === "string" ? doc : doc.name));
		};
		list.set_rows_as_checked = function (...args) { if (!providerReadonly(controller)) return originals.set_rows_as_checked?.apply(this, args); };
		if (config.keepColumnHeader) list.on_row_checked = function (...args) {
			const result = originals.on_row_checked?.apply(this, args);
			// Keep native checks, bulk permissions and actions; only replace its header presentation.
			this.$list_head_subject?.show();
			this.$checkbox_actions?.hide();
			const checked = this.$checks?.length || 0;
			this.$list_head_subject?.find(".list-check-all").prop("checked", checked > 0 && checked === this.data.length).prop("indeterminate", checked > 0 && checked < this.data.length);
			config.onSelectionChange?.(controller);
			return result;
		};
		for (const name of ["before_render", "after_render"]) list[name] = function (...args) { if (!providerReadonly(controller)) return originals[name]?.apply(this, args); };
		if (config.provider && list.setup_realtime_updates) {
			const nativeSetupRealtime = list.setup_realtime_updates;
			list.setup_realtime_updates = function (...args) {
				const result = nativeSetupRealtime.apply(this, args);
				if (providerActive(controller)) config.provider.onActivate?.(controller);
				return result;
			};
		}
		list.get_header_html = () => headerHTML(controller);
		list.get_list_row_html = (doc) => rowHTML(controller, doc);
		if (config.renderTable) list.render_header = function (...args) {
			// ListView.refresh calls this again after render_list. The physical table
			// already owns its header; native refresh_header must not append a second table.
			if (this.$result?.find(".dlp-custom-table-surface").length) {
				this.$list_head_subject = this.$result.find(".list-header-subject");
				this.$checkbox_actions = this.$result.find(".checkbox-actions");
				return;
			}
			this.$result?.find(".dlp-custom-list-header").remove();
			return originals.render_header?.apply(this, args);
		};
		list.render_list = function () {
			this.$result?.find(".list-row-container").remove();
			this.$list_head_subject = null;
			this.$checkbox_actions = null;
			if (config.renderTable) {
				currentRows(controller).forEach((doc, index) => { doc._idx = index; });
				this.$result?.append(`<div class="list-row-container dlp-custom-table-surface">${config.renderTable(controller, currentRows(controller))}</div>`);
				this.$list_head_subject = this.$result?.find(".list-header-subject");
				this.$checkbox_actions = this.$result?.find(".checkbox-actions");
			} else {
				this.render_header();
				currentRows(controller).forEach((doc, index) => {
					doc._idx = index;
					this.$result?.append(this.get_list_row_html(doc));
				});
			}
			movePurchaseNativeFilterSection(controller);
			config.afterRender?.(controller);controller.afterRowDetailsRender?.();
			if (providerActive(controller) && !currentRows(controller).length) this.$result?.append(`<div class="list-row-container dlp-provider-empty text-muted text-center" role="status">${translateText("没有符合条件的采购记录")}</div>`);
		};
		list.toggle_result_area = function () {
			if (!providerReadonly(controller)) originals.toggle_result_area.call(this);
			if (providerActive(controller)) this.$no_result?.hide();
			if (providerReadonly(controller)) { this.page?.hide_actions_menu?.(); this.page?.clear_primary_action?.(); }
			this.$result?.parent(".result-container").show();
			this.$result?.show();
			this.$paging_area?.hide();
			controller.$paging?.show();
		};
		list.render_count = () => updateSummary(controller);
		// A successful Ajax response can still fail while rendering. Keep the
		// native pipeline and propagate its error to the dispatched waiter too.
		for (const name of ['prepare_data', 'toggle_result_area', 'before_render', 'render', 'render_list', 'after_render', 'set_result_height', 'freeze', 'reset_defaults']) {
			const stage = list[name]; if (typeof stage !== 'function') continue;
			list[name] = function (...args) {
				try { return stage.apply(this, args); }
				catch (error) {
					finishFilterRequest(name === 'freeze' && args[0] ? controller.lastDispatchedRequest : controller.renderingRequest, error);
					throw error;
				}
			};
		}
		preserveRealtimePaging(controller);
		preserveSavedFilterGroup(controller);
		mountControls(controller);
		controller.activate();
		list.render_header(true);
		return controller;
	}

	function preserveRealtimePaging(controller) {
		const { list, root, originals } = controller;
		if (!originals.process_document_refreshes || !root.frappe.utils?.debounce) return;
		// Native incremental refresh appends matching documents and bypasses fixed-page preparation.
		list.process_document_refreshes = function () {
			if (!this.pending_document_refreshes?.length) return;
			if (!isListRoute(root.frappe) || root.cur_list !== this) {
				this.pending_document_refreshes = [];
				this.disable_realtime_updates?.();
				return;
			}
			if (this.$checks?.length || this.avoid_realtime_update?.() || this.disable_list_update || this.filter_area?.is_being_edited?.()) return;
			this.pending_document_refreshes = [];
			return controller.refresh();
		};
		// The constructor already bound the old method; cancel it before replacing that binding.
		originals.debounced_refresh?.cancel?.();
		list.debounced_refresh = root.frappe.utils.debounce(list.process_document_refreshes.bind(list), list.is_large_table ? 15000 : 2000);
		controller.queueRealtimeRefresh = data => {
			list.pending_document_refreshes ||= [];
			if (!list.pending_document_refreshes.some(pending => pending.doctype === data.doctype && pending.name === data.name)) list.pending_document_refreshes.push(data);
			return list.debounced_refresh();
		};
	}

	function preserveSavedFilterGroup(controller) {
		const page = controller.list.page;
		const canonical = "Saved Filters";
		const translated = controller.translate(canonical);
		if (translated === canonical || !page?.inner_toolbar?.find || !page.get_inner_group_button || !page.get_or_add_inner_group_button) return;
		// Native ListFilter creates a translated identity but its label updater looks up English.
		// Keep the same button/menu, and alias only this page's group lookup/creation methods.
		const normalize = () => page.inner_toolbar.find(`.inner-group-button[data-label="${encodeURIComponent(translated)}"]`).attr("data-label", encodeURIComponent(canonical));
		normalize();
		for (const name of ["get_inner_group_button", "get_or_add_inner_group_button"]) {
			const original = page[name];
			controller.originals[`page_${name}`] = original;
			page[name] = function (label, ...args) {
				if (label !== canonical && label !== translated) return original.call(this, label, ...args);
				normalize();
				const group = original.call(this, canonical, ...args);
				const text = group.find("button").contents().first()[0];
				if (text?.textContent.trim() === canonical) text.textContent = translated;
				return group;
			};
		}
		controller.restoreSavedFilterLabel = () => page.get_inner_group_button(canonical);
	}

	function layout(controller) {
		const cols = controller.preferences.columns.map((field) => displayColumns(controller).find((col) => col.fieldname === field)).filter(Boolean);
		const lastFrozen = cols.findIndex((col) => col.fieldname === (providerActive(controller) ? config.provider.freezeUntil : config.freezeUntil));
		const frozenFields = providerActive(controller) ? config.provider.freezeFields : config.freezeFields;
		let left = config.freezeSelection === false ? 0 : config.hideSequence ? 36 : 76;
		return cols.map((col, index) => {
			const frozen = frozenFields ? frozenFields.includes(col.fieldname) : lastFrozen >= 0 && index <= lastFrozen;
			const width=shared.columnWidth(col,controller.preferences.density,DOCTYPE);
			const result = { ...col, width, frozen, left };
			if (!frozenFields || frozen) left += width;
			return result;
		});
	}

	function cellHTML(col, value, title = "", header = false, readonly = false) {
		const classes = ["dlp-po-grid-cell", col.fieldname==='company'?"":"ellipsis", col.frozen ? "dlp-po-frozen" : "", NUMBERS.has(col.fieldname) ? "dlp-po-number" : "", DATES.has(col.fieldname) ? "dlp-po-date" : "", col.fieldname === "status" ? "dlp-po-status" : "", col.fieldname === "name" ? "list-subject" : ""].filter(Boolean).join(" ");
		return `<div class="${classes}" data-fieldname="${col.fieldname}" style="--dlp-po-left:${col.left}px;width:${col.width}px" title="${escapeHTML(title)}"${header ? ` data-${readonly ? "provider-sort" : "sort-by"}="${col.fieldname}"` : ""}>${value}</div>`;
	}

	function template(controller) {
		return `36px ${config.hideSequence ? "" : "40px "}${layout(controller).map((col) => `${col.width}px`).join(" ")}`;
	}

	function selectionCell(html, sequence) {
		const frozen = config.freezeSelection === false ? "" : " dlp-po-frozen";
		return `<div class="dlp-po-grid-cell${frozen} select-like" data-fieldname="_select" style="--dlp-po-left:0px;width:36px">${html}</div>` + (config.hideSequence ? "" : `<div class="dlp-po-grid-cell${frozen} dlp-po-number" data-fieldname="_sequence" style="--dlp-po-left:36px;width:40px">${sequence}</div>`);
	}
	function readonlyCheckbox(translate) {
		return config.disabledSelectionLabel ? `<input type="checkbox" disabled aria-label="${escapeHTML(translate(config.disabledSelectionLabel))}" title="${escapeHTML(translate(config.disabledSelectionLabel))}">` : "";
	}

	function headerHTML(controller) {
		if (config.renderHeader) return `<div class="list-row-container dlp-custom-list-header">${config.renderHeader(controller)}</div>`;
		const { translate: t, list } = controller;
		const provider = providerActive(controller), readonly = providerReadonly(controller);
		const checkbox = readonly ? readonlyCheckbox(t) : `<input class="list-header-checkbox list-check-all" type="checkbox" title="${escapeHTML(t("Select All"))}">`;
		const columns = layout(controller).map((col) => {
			const label = t(col.label);
			return cellHTML(col, escapeHTML(label), label, provider ? (config.provider.sortFields || []).includes(col.fieldname) : controller.allowed.has(col.fieldname), provider);
		}).join("");
		return `<div class="list-row-container"><header class="list-row-head dlp-po-grid-header" style="--dlp-po-columns:${template(controller)}"><div class="list-header-subject dlp-po-grid-header-columns">${selectionCell(checkbox, "#")}${columns}</div><div class="checkbox-actions" style="display:none"><span class="select-like">${checkbox}</span><span class="list-header-meta"></span></div></header></div>`;
	}

	function rowHTML(controller, doc) {
		if (config.renderRow) return config.renderRow(controller, doc);
		const { list, root } = controller;
		const provider = providerActive(controller), readonly = !rowSelectable(controller, doc);
		const formatters = {
			allowed: controller.allowed,
			translate: controller.translate,
			number: (value, field) => formatNumber(root, value, field),
			date: (value) => root.frappe.datetime?.str_to_user ? root.frappe.datetime.str_to_user(value) : value,
		};
		const checkbox = readonly ? readonlyCheckbox(controller.translate) : `<input type="checkbox" class="list-row-checkbox" data-doctype="${DOCTYPE}" data-name="${escapeHTML(doc.name)}">`;
		const cells = layout(controller).map((col) => {
			let value = (provider ? config.provider.renderValue?.(col.fieldname, doc, formatters, escapeHTML) : undefined) ?? renderValue(col.fieldname, doc, formatters);
			if (col.fieldname === "name") value = config.renderLink?.(controller, doc, value) ?? (provider ? config.provider.renderLink?.(controller, doc, value, escapeHTML) : undefined) ?? `<a href="${escapeHTML(provider ? config.provider.formLink(doc) : list.get_form_link(doc))}" data-name="${escapeHTML(doc.name)}">${value}</a>`;
			if (col.fieldname === "supplier_name" && doc.supplier) value = `<a href="/desk/supplier/${encodeURIComponent(doc.supplier)}">${value}</a>`;
			if (col.fieldname === "status" && (!provider || config.provider.useNativeIndicator?.(doc))) value = list.get_indicator_html?.(doc, Boolean(list.workflow_state_fieldname)) || value;
			const raw = doc[col.fieldname];
			return cellHTML(col, value, controller.allowed.has(col.fieldname) && (raw === null || typeof raw !== 'object') ? raw ?? "—" : "");
		}).join("");
		const sequence = controller.page * controller.pageSize + (doc._idx || 0) + 1;
		return `<div class="list-row-container"${config.rowExtra?` data-dlp-detail-name="${escapeHTML(doc.name)}"`:''} tabindex="0"><div class="level ${readonly ? "dlp-po-readonly-row" : "list-row"} dlp-po-grid-row" style="--dlp-po-columns:${template(controller)}">${selectionCell(checkbox, config.renderSequence?.(controller, doc, sequence) ?? sequence)}${cells}</div>${config.rowExtra?.(controller, doc) || ""}</div>`;
	}

	const filterKey = filter => JSON.stringify(filter.slice(0, 4));
	function finishFilterRequest(request, error) {
		if (!request) return;
		request.complete = true; request.error = error;
		if (error) request.filterRefresh?.reject(error); else request.filterRefresh?.resolve();
	}
	function appliedFilters(controller) {
		const t = controller.translate, definitions = controller.filterDefinitions ||= new Map([...controller.list.meta.fields, ...(config.controls || [])].map(df => [df.fieldname, df]));
		const result = [], seen = new Set(), native = controller.list.filter_area?.get?.() || controller.nativeFilterGroup?.get_filters() || controller.originals?.get_args?.call(controller.list)?.filters || [];
		const display = (field, value) => {
			const df = definitions.get(field) || controller.controls[field]?.df;
			const text = item => df?.fieldtype === 'Select' || controller.controls[field]?.df?.fieldtype === 'Select' ? t(config.optionLabels?.[field]?.[item] || item) : String(item);
			return Array.isArray(value) ? value.map(text).join(', ') : text(value);
		};
		for (const [kind,filters] of [['native',native],['native_or',controller.orFilterGroup?.get_filters() || []]]) for (const filter of filters) {
			const key = `${kind === 'native_or' ? 'or:' : ''}${filterKey(filter)}`; if (seen.has(key)) continue; seen.add(key);
			const [,field,operator,value] = filter;
			result.push({kind,key,filter:filter.slice(0,4),field,label:translateText("{0}{1}", [kind === 'native_or' ? `${t('任一条件')} · ` : '', t(definitions.get(field)?.label || field)]),operator,value:display(field,value)});
		}
		const providerLabels = {source:'来源', approval_status:'审批状态', pending_company:'公司待确认'};
		for (const [field,value] of Object.entries(controller.quick)) {
			if (value == null || value === '' || value === false) continue;
			const df = definitions.get(field), label = df?.label || providerLabels[field]; if (!label) continue;
			result.push({kind:'quick',key:`quick:${JSON.stringify([field,value])}`,field,label:t(label),operator:'',value:typeof value === 'boolean' ? t('是') : display(field,value)});
		}
		return result;
	}
	function filterActions() {
		return {
			getAppliedFilters() { return appliedFilters(this); },
			async mutateFilters(change) {
				if (this.filterMutationBusy) return;
				const area = this.list.filter_area, previous = area?.trigger_refresh;
				this.filterMutationBusy = true;
				this.resetting = true;
				area?.debounced_refresh_list_view?.cancel?.();
				if (area) area.trigger_refresh = false;
				this.$appliedFilters?.find('button').prop('disabled', true);
				this.$filters?.find('.dlp-po-clear-filters').prop('disabled', true);
				let failure;
				try {
					try { await change(); } catch (error) { failure = error; }
					area?.debounced_refresh_list_view?.cancel?.();
					if (area) area.trigger_refresh = previous;
					this.resetting = false;
					for (const group of new Set([area?.filter_list,this.nativeFilterGroup,this.orFilterGroup])) {
						group?.update_filter_button?.(); group?.toggle_empty_filters?.(!(group.filters || []).length);
					}
					this.setPage(0);
					try { await (this.refreshFilters ? this.refreshFilters() : this.refresh()); } catch (error) { failure ||= error; }
					if (failure) throw failure;
				}
				finally {
					if (this.resetting) area?.debounced_refresh_list_view?.cancel?.();
					if (area) area.trigger_refresh = previous;
					this.resetting = false;
					this.filterMutationBusy = false;
					movePurchaseNativeFilterSection(this);
					this.$filters?.find('.dlp-po-clear-filters').prop('disabled', false);
				}
			},
			async removeAppliedFilter(tag) {
				if (!tag || !this.getAppliedFilters().some(current => current.key === tag.key)) return;
				return this.mutateFilters(async () => {
					if (tag.kind === 'quick') {
						delete this.quick[tag.field];
						await this.controls[tag.field]?.set_value('');
						this.$providerControls?.find(`[data-filter="${tag.field}"]`).val('').prop('checked', false);
					} else {
						const area = this.list.filter_area, group = tag.kind === 'native_or' ? this.orFilterGroup : area?.filter_list || this.nativeFilterGroup, key = filterKey(tag.filter);
						const standard = tag.kind === 'native' && area?.get_standard_filters?.().some(filter => filterKey(filter) === key);
						const remaining = [];
						for (const filter of group?.filters || []) {
							if (filter.field && filterKey(filter.get_value()) === key) filter.remove(true);
							else remaining.push(filter);
						}
						if (group) { group.filters = remaining; group.update_filter_button?.(); }
						if (standard) await this.list.page.fields_dict[tag.field].set_value('');
					}
					this.restoreSavedFilterLabel?.();
				});
			},
			async clearQuickFilters() {
				return this.mutateFilters(async () => {
					this.quick = {};
					this.resetAdvancedFilters?.();
					await this.list.filter_area?.clear(false);
					await Promise.all(Object.entries(this.controls).filter(([field]) => !this.nativeControlFields.has(field)).map(([,control]) => control.set_value('')));
					this.$providerControls?.find('input, select').val('').prop('checked', false);
					this.restoreSavedFilterLabel?.();
				});
			},
		};
	}
	function movePurchaseNativeFilterSection(controller) {
		if (!config.purchaseChrome || !controller.$appliedFilters) return;
		// Native restrictions live under page-form, not the table surface. Move
		// the original section so its permission indicator and bindings survive.
		controller.list.page.wrapper.find('.page-form > .filter-section').appendTo(controller.$appliedFilters);
		controller.list.$frappe_list.find('> .filter-section').appendTo(controller.$appliedFilters);
		controller.appliedFilterTags = controller.getAppliedFilters();
		controller.$filterTags?.html(controller.appliedFilterTags.map((tag,index) => {
			const text = `${tag.label} ${tag.operator} ${tag.value}`.trim(), remove = `${controller.translate('移除筛选')}：${text}`;
			return `<span class="dlp-applied-filter"><span title="${escapeHTML(text)}">${escapeHTML(text)}</span><button type="button" data-applied-filter="${index}"${controller.filterMutationBusy ? ' disabled' : ''} aria-label="${escapeHTML(remove)}" title="${escapeHTML(remove)}">×</button></span>`;
		}).join('')).toggle(Boolean(controller.appliedFilterTags.length));
	}
	function mountControls(controller) {
		const { list, root, translate: t } = controller;
		if (!root.$ || !list.$frappe_list || !root.document) return;
		const $ = root.$;
		list.$frappe_list.addClass("dlp-po-grid");
		list.$paging_area.addClass("dlp-po-native-paging").hide();
		controller.$toolbar = $(`<div class="dlp-po-toolbar"><label>${escapeHTML(t("密度"))} <select class="form-control input-xs dlp-po-density"><option value="tight">${escapeHTML(t("紧凑"))}</option><option value="standard">${escapeHTML(t("标准"))}</option></select></label><button class="btn btn-default btn-sm dlp-po-columns" type="button">${escapeHTML(t("列设置"))}</button></div>`).prependTo(list.$frappe_list);
		controller.$toolbar.find(".dlp-po-density").val(controller.preferences.density).on("change.dlpPO", (event) => {
			controller.preferences.density = event.target.value;
			controller.setColumns(controller.preferences.columns, false);
		});
		controller.$toolbar.find(".dlp-po-columns").on("click.dlpPO", () => columnDialog(controller));
		if ((!config.pageRoute || config.provider.exportCurrent) && root.frappe.model.can_export?.(DOCTYPE)) {
			$(`<button class="btn btn-default btn-sm dlp-po-export" type="button">${escapeHTML(t("导出 Excel"))}</button>`).appendTo(controller.$toolbar).on("click.dlpPO", async (event) => {
				const button = $(event.currentTarget).prop("disabled", true);
				try { await controller.exportCurrent(); }
				catch (error) { root.frappe.msgprint({ title: t("导出失败"), message: escapeHTML(error.message || t("导出失败，请重试。")), indicator: "red" }); }
				finally { button.prop("disabled", false); }
			});
		}
		let filterHost = list.page.wrapper.find(".page-form").first();
		if (controller.pageSurface) {
			list.page.show_form?.();
			filterHost = list.page.page_form || filterHost;
			if (!filterHost.length) filterHost = $('<div class="page-form row"></div>').prependTo(list.page.main);
			filterHost.removeClass('hide').show();
		}
		controller.$filters = $('<div class="dlp-po-filters"></div>').prependTo(filterHost);
		if(config.purchaseChrome) {
			controller.$moreFilters=$(`<details class="dlp-purchase-more-filters"><summary class="btn btn-default btn-sm">${escapeHTML(t('更多筛选'))}</summary><div class="dlp-purchase-advanced-filters"></div></details>`).appendTo(controller.$filters);
			controller.$advancedFilters=controller.$moreFilters.find('.dlp-purchase-advanced-filters');
			controller.$appliedFilters=$(`<div class="dlp-purchase-applied-filters" aria-label="${translateText('当前筛选')}"></div>`).insertAfter(controller.$filters);
			controller.$filterTags=$(`<div class="dlp-applied-filter-tags" aria-label="${translateText('已应用的筛选条件')}"></div>`).appendTo(controller.$appliedFilters);
			controller.$filterTags.on('click.dlpPOFilters', '[data-applied-filter]', event => {
				const tag = controller.appliedFilterTags[Number(event.currentTarget.dataset.appliedFilter)];
				controller.removeAppliedFilter(tag).catch(error => root.frappe.msgprint({message:escapeHTML(error.message || t('筛选更新失败，请重试。')),indicator:'red'}));
			});
			const group = list.filter_area?.filter_list;
			if (group) { const change = group.on_change; group.on_change = function (...args) { if (!controller.resetting) return change?.apply(this, args); }; }
			movePurchaseNativeFilterSection(controller);
			list.page.inner_toolbar?.find('.inner-group-button').appendTo(controller.$advancedFilters);
		}
		const controls = (config.controls || []).map((control) => ({ ...control }));
		if(config.purchaseChrome) {const rank=new Map(config.mainFilterFields.map((field,index)=>[field,index]));controls.sort((a,b)=>(rank.get(a.fieldname) ?? 100)-(rank.get(b.fieldname) ?? 100));}
		for (const control of controls) {
			if (control.fieldname !== "search" && !controller.allowed.has(control.permission_field || control.fieldname)) continue;
			const field = list.meta.fields.find((df) => df.fieldname === control.fieldname);
			if (control.fieldtype === "Select") control.options = selectOptions(control, field, t);
			const holder = $(`<div class="dlp-po-filter" data-fieldname="${control.fieldname}"></div>`).appendTo(config.purchaseChrome && !(config.mainFilterFields || []).includes(control.fieldname) ? controller.$advancedFilters : controller.$filters);
			const native = config.purchaseChrome && list.page.fields_dict?.[control.fieldname];
			if (native && native.df.fieldtype === control.fieldtype && native.$wrapper) {
				native.$wrapper.removeClass('input-max-width col-md-2').appendTo(holder);
				native.$input?.attr('aria-label', t(control.label)).attr('placeholder', t(control.label));
				controller.controls[control.fieldname] = native;
				controller.nativeControlFields.add(control.fieldname);
				continue;
			}
			const input = root.frappe.ui.form.make_control({ parent: holder, df: { ...control, label: t(control.label), placeholder: t(control.label), change: () => {
				if (controller.resetting) return;
				controller.quick[control.fieldname] = input.get_value();
				controller.setPage(0);
				controller.refresh();
			} }, render_input: true });
			input.$input?.attr("aria-label", t(control.label));
			controller.controls[control.fieldname] = input;
			if (control.fieldname === "search") controller.searchControl = input;
		}
		// Move the native button itself so its advanced/saved-filter handlers survive.
		list.page.wrapper.find(".page-form .filter-x-button").off('click').on("click.dlpPOFilters", () => controller.clearQuickFilters().catch(error => root.frappe.msgprint({message:escapeHTML(error.message || t('筛选更新失败，请重试。')),indicator:'red'})))
			.text(t("清空筛选")).attr("title", t("清空筛选")).attr("aria-label", t("清空筛选")).addClass("dlp-po-clear-filters").appendTo(controller.$filters);
		controller.$summary = $('<div class="dlp-po-summary" aria-live="polite"></div>').insertBefore(list.$paging_area);
		controller.$paging = $(`<div class="dlp-po-paging"><label>${escapeHTML(t("每页"))} <select class="form-control input-xs dlp-po-page-size">${[20, 100, 500, 2500].map((size) => `<option value="${size}">${size}</option>`).join("")}</select></label><span class="dlp-po-page-info"></span><button class="btn btn-default btn-sm dlp-po-previous" type="button">${escapeHTML(t("上一页"))}</button><button class="btn btn-default btn-sm dlp-po-next" type="button">${escapeHTML(t("下一页"))}</button></div>`).appendTo(list.$frappe_list);
		controller.$paging.find(".dlp-po-page-size").val(100).on("change.dlpPO", (event) => {
			controller.pageSize = Number(event.target.value);
			controller.setPage(0);
			controller.refresh();
		});
		for (const [selector, delta] of [[".dlp-po-previous", -1], [".dlp-po-next", 1]]) {
			controller.$paging.find(selector).on("click.dlpPO", () => {
				controller.setPage(controller.page + delta);
				controller.refresh();
			});
		}
		controller.mountRowDetailControls?.();
		controller.savePreferences();
		config.provider?.mountControls?.(controller);
		config.mountControls?.(controller);
		if(config.purchaseChrome) {
			controller.$moreFilters.appendTo(controller.$filters);
			controller.$filters.find('.dlp-po-clear-filters').appendTo(controller.$filters);
			if(controller.pageSurface)$(`<button class="btn btn-default btn-sm dlp-po-clear-filters" type="button">${escapeHTML(t('清空'))}</button>`).appendTo(controller.$filters).on('click.dlpPO',()=>controller.clearQuickFilters());
		}
		if (config.dismissInitialOnboarding) dismissAutomaticOnboarding(controller, root);
		paintSummary(controller);
	}

	function dismissAutomaticOnboarding(controller, root) {
		if (controller.initialOnboardingDismissed || controller.stopInitialOnboarding || !root.document || !root.MutationObserver) return;
		let observer, timer, stopped = false;
		const cleanup = () => {
			if (stopped) return;
			stopped = true; observer?.disconnect();
			if (timer !== undefined) (root.clearTimeout || clearTimeout)(timer);
			root.document.removeEventListener?.('click', manualStart, true);
			controller.stopInitialOnboarding = null;
		};
		controller.stopInitialOnboarding = cleanup;
		const manualStart = event => {
			if (event.target.closest?.('.onboarding-sidebar')) { controller.initialOnboardingDismissed = true; cleanup(); }
		};
		const closeInitial = () => {
			if (stopped) return;
			const active = config.pageRoute ? (root.frappe.get_route?.() || [])[0] === config.pageRoute : isListRoute(root.frappe);
			if (!active) return cleanup();
			const close = root.document.querySelector('.user-onboarding')?.querySelector('.onb-header-actions button:has(use[href="#icon-x"])');
			if (close) { controller.initialOnboardingDismissed = true; close.click(); cleanup(); }
		};
		// Observe only a bounded initial load, including delayed native mount.
		// Capture the real Getting Started entry before native code opens it.
		observer = new root.MutationObserver(closeInitial);
		observer.observe(root.document.querySelector('.user-onboarding') || root.document.body, { childList: true, subtree: true });
		root.document.addEventListener?.('click', manualStart, true);
		timer = (root.setTimeout || setTimeout)(() => { controller.initialOnboardingDismissed = true; cleanup(); }, 5000);
		closeInitial();
	}

	function paintSummary(controller) {
		const { translate: t, list } = controller;
		const count = controller.total === null ? "…" : controller.total.toLocaleString();
		const amounts = providerActive(controller) ? config.provider.summary(controller, escapeHTML) : controller.summary.map((row) => renderValue("grand_total", { grand_total: row._aggregate_column, currency: row.currency }, { number: (value, field) => formatNumber(controller.root, value, field) })).join(" · ");
		controller.$summary?.html(`${escapeHTML(t("Total"))}: ${escapeHTML(count)}${amounts ? ` · ${amounts}` : ""}`);
		const rows = currentRows(controller);
		const from = rows.length ? controller.page * controller.pageSize + 1 : 0;
		const to = rows.length ? controller.page * controller.pageSize + rows.length : 0;
		controller.$paging?.find(".dlp-po-page-info").text(`${from}–${to} / ${count}`);
		controller.$paging?.find(".dlp-po-previous").prop("disabled", controller.page === 0);
		controller.$paging?.find(".dlp-po-next").prop("disabled", controller.total === null || to >= controller.total || rows.length === 0);
	}

	function updateSummary(controller) {
		const { list, root } = controller;
		if (providerActive(controller)) { paintSummary(controller); return Promise.resolve(); }
		if (!root.frappe.call) return Promise.resolve();
		const args = list.get_args();
		const signature = controller.querySignature;
		const requests = buildRequests(args, controller.preferences.columns, controller.allowed);
		const version = (controller.summaryVersion || 0) + 1;
		controller.summaryVersion = version;
		const current = () => version === controller.summaryVersion && signature === controller.querySignature;
		const count = root.frappe.call({ method: "frappe.desk.reportview.get_count", args: requests.count }).then((response) => {
			if (!current()) return;
			controller.total = Number(response.message || 0);
			list.total_count = controller.total;
			paintSummary(controller);
		});
		const summary = requests.summary ? root.frappe.call({ method: "frappe.desk.reportview.get_list", args: requests.summary }).then((response) => {
			if (!current()) return;
			controller.summary = requests.summary.aggregate_function ? response.message || [] : currencyTotals(response.message || []);
			paintSummary(controller);
		}) : Promise.resolve();
		return Promise.all([count, summary]).catch(() => {
			if (current()) controller.$summary?.text(controller.translate("Unable to load totals"));
		});
	}

	function columnDialog(controller) {
		const { root, list, translate: t } = controller;
		const visible = new Set(controller.preferences.columns);
		const columns = displayColumns(controller);
		const order = [...controller.preferences.columns, ...columns.map((col) => col.fieldname).filter((field) => displayPermissions(controller).has(field) && !visible.has(field))];
		const dialog = new root.frappe.ui.Dialog({ title: t("列设置"), fields: [{ fieldtype: "HTML", fieldname: "columns" }], primary_action_label: t("保存"), primary_action: () => {
			controller.setColumns(order.filter((field) => visible.has(field)));
			dialog.hide();
		} });
		const wrapper = dialog.fields_dict.columns.$wrapper;
		const draw = () => wrapper.html(`<div class="dlp-po-column-options">${order.map((field) => {
				const col = columns.find((col) => col.fieldname === field);
			return `<div class="dlp-po-column-option" data-field="${field}"><label><input type="checkbox"${visible.has(field) ? " checked" : ""}${field === "name" ? " disabled" : ""}> ${escapeHTML(t(col.label))}</label><button type="button" class="btn btn-xs btn-default" data-move="-1" aria-label="${escapeHTML(t("Move Up"))}">↑</button><button type="button" class="btn btn-xs btn-default" data-move="1" aria-label="${escapeHTML(t("Move Down"))}">↓</button></div>`;
		}).join("")}</div>`);
		wrapper.off(".dlpPO").on("change.dlpPO", "input", (event) => {
			const field = root.$(event.target).closest("[data-field]").attr("data-field");
			if (event.target.checked) visible.add(field); else visible.delete(field);
		}).on("click.dlpPO", "[data-move]", (event) => {
			const field = root.$(event.target).closest("[data-field]").attr("data-field");
			const index = order.indexOf(field);
			const target = index + Number(event.target.dataset.move);
			if (target >= 0 && target < order.length) {
				[order[index], order[target]] = [order[target], order[index]];
				draw();
			}
		});
		draw();
		dialog.show();
	}

	function install(root) {
		const frappe = root.frappe;
		if (!frappe?.listview_settings || installed.has(frappe)) return;
		installed.add(frappe);
		const settings = frappe.listview_settings[DOCTYPE] || (frappe.listview_settings[DOCTYPE] = {});
		settings.dlpCompactList=true;
		shared.installBusinessDateSort(root);
		const onload = settings.onload;
		const refresh = settings.refresh;
		settings.onload = function (list) {
			const result = onload?.apply(this, arguments);
			mount(list, root);
			return result;
		};
		settings.refresh = function (list) {
			const result = refresh?.apply(this, arguments);
			if (isNativeList(list)) mount(list, root)?.activate();
			return result;
		};
		frappe.router?.on("change", () => {
			root.document?.body?.classList.toggle(config.routeClass, isListRoute(frappe));
			// Native ListFactory caches by route; its doctype key may be an empty namespace.
			const list = [frappe.get_list_view?.(DOCTYPE), frappe.views?.list_view?.[`List/${DOCTYPE}/List`], frappe.views?.list_view?.[DOCTYPE], root.cur_list].find(isNativeList);
			list?.dlpBusinessDateSort?.enter();
			root.document?.body?.classList.toggle(`${config.routeClass}-readonly`, isListRoute(frappe) && Boolean(list?.[config.controllerKey] && providerReadonly(list[config.controllerKey])));
			list?.[config.controllerKey]?.syncDisclosureRowGuard?.(isListRoute(frappe));
			config.onRouteChange?.(list?.[config.controllerKey], isListRoute(frappe));
			if (!isListRoute(frappe)) { list?.[config.controllerKey]?.stopInitialOnboarding?.(); list?.[config.controllerKey]?.cancelPendingRowDetails?.(); }
			if (isListRoute(frappe)) list?.[config.controllerKey]?.activate();
		});
	}

	// Standalone Page surface uses the same renderer, preferences and controls. It
	// is not a native ListView and never participates in native business selection.
	function mountPage(page, root) {
		const $ = root.$, frappe = root.frappe;
		const meta = frappe.get_meta?.(DOCTYPE) || { fields: config.controls || [] };
		const surface = { page, meta, data: [], doctype: DOCTYPE,
			$frappe_list: $('<div class="frappe-list dlp-page-grid"></div>').appendTo(page.main) };
		surface.$result = $('<div class="result"></div>').appendTo($('<div class="result-container"></div>').appendTo(surface.$frappe_list));
		surface.$paging_area = $('<div></div>').appendTo(surface.$frappe_list);
		const nativeAllowed = allowedFields(meta, level => frappe.perm?.has_perm ? frappe.perm.has_perm(DOCTYPE, level, 'read') : true, frappe.model.std_fields_list || ['name','owner','docstatus','creation','modified','modified_by','idx']);
		const permitted = new Set(nativeAllowed);
		for (const col of COLUMNS) {
			const source = config.pageFieldMap?.[col.fieldname];
			if (!source || nativeAllowed.has(source)) permitted.add(col.fieldname);
		}
		const key = preferenceKey(frappe.boot?.sitename || root.location?.host, frappe.session?.user);
		let saved; try { saved = JSON.parse(root.localStorage?.getItem(key) || 'null'); } catch (_) { /* Optional preferences. */ }
		const preferences = normalizePreferences(saved, permitted, config.provider.columns);
		if (config.backupMigratedPreferences && saved && preferences.version !== saved.version) {
			try {
				const backupKey = `${key}:backup:${saved.version ?? "legacy"}`;
				if (root.localStorage?.getItem(backupKey) == null) root.localStorage?.setItem(backupKey, JSON.stringify(saved));
			} catch (_) { /* Migration must remain usable when optional browser storage is disabled. */ }
		}
		const c = { pageSurface: true, root, list: surface, nativeAllowed, allowed: permitted, displayAllowed: permitted, providerAllowed: permitted,
			preferences, controls: {}, quick: {}, resetting: false,
			providerRows: [], providerPayload: null, providerOrderBy: config.defaultSort, page: 0, pageSize: 100, total: null, requestId: 0, querySignature: null,
			translate: root.__ || (x => x),
			setPage(value) { this.page = Math.max(0, Number(value) || 0); },
			activate() { surface.dlpBusinessDateSort?.enter(); root.document.body.classList.toggle(config.routeClass, active()); this.restoreRowDetails?.(); },
			savePreferences() { this.preferences = normalizePreferences(this.preferences, permitted, config.provider.columns); try { root.localStorage?.setItem(key, JSON.stringify(this.preferences)); } catch (_) { /* Rendering must work without storage. */ } surface.$frappe_list.toggleClass('dlp-po-standard', this.preferences.density === 'standard'); },
			setColumns(columns) { this.preferences.columns = columns; this.savePreferences(); render(); },
			nativeControlFields: new Set(), ...filterActions(),
			exportCurrent() { return config.provider.exportCurrent(this); },
			async refresh() {
				this.activate(); if (!active()) return;
				const request = config.provider.request(this), { start, page_length, ...scope } = request.args;
				const signature = JSON.stringify(scope);
				if (this.querySignature !== null && signature !== this.querySignature) this.setPage(0);
				this.querySignature = signature; request.args.start = this.page * this.pageSize; request.args.page_length = this.pageSize;
				const generation = ++this.requestId;
				try {
					const response = await frappe.call(request);
					if (generation !== this.requestId || !active() || requestKey(config.provider.request(this).args) !== requestKey(request.args)) return;
					this.providerPayload = response.message || {}; this.providerRows = this.providerPayload.rows || []; this.total = Number(this.providerPayload.total_count || 0);
					render(); config.provider.onPayload?.(this); paintSummary(this);
				} catch (error) { if (generation === this.requestId && active()) this.$summary.text(translateText("无权读取或加载失败，请核对系统提示。")); }
			},
		};
		function active() { return (frappe.get_route?.() || [])[0] === config.pageRoute; }
		function render() { surface.$result.html(headerHTML(c) + c.providerRows.map((row, index) => { row._idx = index; return rowHTML(c, row); }).join('')); if (!c.providerRows.length) surface.$result.append(`<div class="dlp-provider-empty text-muted" role="status">${escapeHTML(c.translate(config.emptyLabel || '没有符合条件的采购付款记录'))}</div>`); movePurchaseNativeFilterSection(c); config.afterRender?.(c); c.afterRowDetailsRender?.(); paintSummary(c); }
		surface.render_list = render; surface.refresh = () => c.refresh();
		shared.bindBusinessDateSort(surface, root, c, config.pageRoute, false);
		if(shared.businessDateFields[DOCTYPE])surface.$result.on('click.dlpPageSort','[data-provider-sort]',event=>{
			const field=event.currentTarget.dataset.providerSort;
			if(!config.provider.sortFields?.includes(field) || !c.allowed.has(field))return;
			const [previous,order]=c.providerOrderBy.split(' ');
			c.providerOrderBy=`${field} ${previous===field && order==='asc'?'desc':'asc'}`;
			c.setPage(0);c.refresh();
		});
		config.onMount?.(c);
		mountControls(c); render(); c.activate();
		if (config.dismissInitialOnboarding) dismissAutomaticOnboarding(c, root);
		frappe.router?.on('change', () => { c.activate(); if (!active()) { c.requestId++; c.stopInitialOnboarding?.(); c.cancelPendingRowDetails?.(); } });
		return c;
	}
	return { COLUMNS, escapeHTML, allowedFields, preferenceKey, normalizePreferences, selectOptions, buildQuery, buildRequests, currencyTotals, formatNumber, renderValue, mount, mountPage, install, dismissAutomaticOnboarding };
});
