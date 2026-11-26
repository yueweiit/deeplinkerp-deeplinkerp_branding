// Requires a built Bench and an existing Playwright installation; uses no live site.
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
import { mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { execFileSync } from 'node:child_process';
import assert from 'node:assert/strict';
const apps=path.resolve(import.meta.dirname, '../..');
const framework=apps+'/frappe/frappe', app=apps+'/deeplinkerp_branding/deeplinkerp_branding';
const formSource=readFileSync(framework+'/public/js/frappe/form/form.js','utf8');
const nativeFormCall='('+formSource.slice(formSource.indexOf('\tcall(opts, args, callback) {'),formSource.indexOf('\n\tget_field(field)',formSource.indexOf('\tcall(opts, args, callback) {'))).trim().replace(/^call\(/,'function(')+')';
const keyboardSource=readFileSync(framework+'/public/js/frappe/ui/keyboard.js','utf8');
const nativeEscape=keyboardSource.slice(keyboardSource.indexOf('function handle_escape_key()'),keyboardSource.indexOf('frappe.ui.keys.add_shortcut',keyboardSource.indexOf('function handle_escape_key()')));
async function bounded(promise, label, timeout=10000){
 let timer;
 try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(label+' timed out after '+timeout+' ms')),timeout);})]);}
 finally{clearTimeout(timer);}
}
const out=process.env.PERMISSION_QA_OUTPUT || path.join(os.tmpdir(), 'deeplinkerp-permission-editor-qa');
mkdirSync(out,{recursive:true});
const translations=JSON.parse(execFileSync('python3',['-c',String.raw`
import csv,json,re,sys
from pathlib import Path
apps=Path(sys.argv[1]);result={}
keys=set(re.findall(r'"([^"\n]+)"',(apps/'deeplinkerp_branding/deeplinkerp_branding/public/js/permission_editor.js').read_text()))
keys.update(re.findall(r'(?:__|translate)\("([^"\n]+)"',(apps/'deeplinkerp_branding/deeplinkerp_branding/public/js/permission_editor.js').read_text()))
for lang in ['zh','es','en']:
 messages={}
 for base in [apps/'frappe/frappe', apps/'erpnext/erpnext']:
  po=base/'locale'/(lang+'.po')
  if po.exists():
   messages.update(re.findall(r'msgid "([^"\n]+)"\nmsgstr "([^"\n]*)"',po.read_text()))
 for base in [apps/'china_finance/china_finance',apps/'deeplinkerp_branding/deeplinkerp_branding']:
  messages.update({r[0]+(':'+r[2] if len(r)>2 and r[2] else ''):r[1] for r in csv.reader((base/'translations'/(lang+'.csv')).open()) if len(r)>1})
 result[lang]={k:v for k,v in messages.items() if k.removesuffix(':Permission assignment') in keys}
print(json.dumps(result))
`,apps],{encoding:'utf8'}));
const browser=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,headless:true,args:['--no-sandbox']});
const checks=[];
try {
 const page=await browser.newPage({viewport:{width:1280,height:1050},hasTouch:true}), errors=[];
 page.setDefaultTimeout(10000);page.setDefaultNavigationTimeout(10000);
 const evaluate=(fn,arg)=>bounded(page.evaluate(fn,arg),'Browser evaluation');
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/*',r=>r.fulfill({contentType:'text/html; charset=utf-8',body:'<meta charset="utf-8"><main><h3>用户 · 权限分配（合成数据验收）</h3><section id="role-section"><div id="profiles"></div><div id="role-field"></div></section><section><h4>模块入口显示</h4><div id="module-profile"></div><div id="module-field"></div></section></main>'}));
 await page.goto('http://permission-editor.test/');
 const css=readdirSync(framework+'/public/dist/css').find(name=>/^desk\.bundle\..+\.css$/.test(name));
 if(!css)throw Error('Build the Bench Desk assets before running the browser test.');
 await page.addStyleTag({path:framework+'/public/dist/css/'+css});
 await page.addStyleTag({path:app+'/public/css/permission_editor.css'});
 // Desk also loads these standard styles; their native columns caused the reported layout regression.
 await page.addStyleTag({path:framework+'/templates/styles/standard.css'});
 await page.addStyleTag({content:'body{background:var(--bg-color);display:block}main{width:100%;max-width:980px;margin:24px auto;background:var(--card-bg);padding:24px}h3{margin-bottom:24px}section{margin-top:24px}.test-dialog{position:fixed;inset:8vh 15vw;background:var(--card-bg);padding:24px;border:1px solid var(--border-color);z-index:2000;overflow:auto;box-shadow:0 10px 70px #0005}'});
 await page.addScriptTag({path:framework+'/public/js/lib/jquery/jquery.min.js'});
 await page.addScriptTag({path:path.resolve(framework,'../node_modules/bootstrap/dist/js/bootstrap.bundle.min.js')});
 await page.addScriptTag({content:nativeEscape});
 await evaluate(translations=>{
  window.lang='zh';
  window.__=(key,args=[],context) => (translations[lang][key+':'+context]||translations[lang][key]||key).replace(/\{(\d+)\}/g,(_,i)=>args[i]??'');
  window.cstr=v=>String(v??'');window.cint=v=>Math.trunc(Number(v)||0);
  window.cur_dialog=null;window.nativeEscapeCalls=0;
  window.addEventListener('keydown',event=>{if(event.key==='Escape'){nativeEscapeCalls++;handle_escape_key();}});
  window.handlers={};window.requests=[];window.dialogs=[];window.messages=[];
  const template=(name,roles)=>({doctype:'Role Profile',name,role_profile:name,roles:roles.map((role,index)=>({doctype:'Has Role',name:name+'-role-'+index,role,parent:name,parenttype:'Role Profile',parentfield:'roles'}))});
  window.profileDocs={Sales:template('Sales',['Sales User']),Warehouse:template('Warehouse',['Stock User']),Empty:template('Empty',[])};
  window.locals={};let idx=0;
  window.frappe={
   is_online:()=>true,
   ui:{form:{on(dt,events){for(const [event,fn] of Object.entries(events)){handlers[dt]??={};(handlers[dt][event]??=[]).push(fn);}}}},
   utils:{escape_html:s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))},
   meta:{get_docfield:()=>({options:'Has Role',permlevel:1}),docfield_copy:{},
    get_field:(_dt,field)=>['roles','role_profiles','block_modules'].includes(field)?{fieldtype:'Table'}:null},
   get_meta:dt=>({istable:['Has Role','User Role Profile','Block Module'].includes(dt),permissions:[{permlevel:1,write:1,role:'System Manager'}]}),
   get_doc:(dt,name)=>locals[dt]?.[name],
   provide:name=>name.split('.').reduce((parent,key)=>(parent[key]??={}),window),
   session:{user:'Administrator'},
   model:{with_doctype:async()=>{},table_fields:['Table','Table MultiSelect'],
    add_child(doc,dt,field){const row={doctype:dt,name:`ROW-${++idx}`,parent:doc.name,parenttype:doc.doctype,parentfield:field};doc[field]??=[];doc[field].push(row);(locals[dt]??={})[row.name]=row;return row;},
    clear_doc(dt,name){const row=locals[dt]?.[name];if(row){const parent=locals[row.parenttype]?.[row.parent];if(parent)parent[row.parentfield]=parent[row.parentfield].filter(r=>r.name!==name);delete locals[dt][name];}}},
   perm:{rights:['read','write','submit']},unscrub:s=>s,unscrubbed:s=>s,
   dom:{freeze(){window.frozen=(window.frozen||0)+1;},unfreeze(){window.frozen--; }},
   set_route:(...args)=>window.lastRoute=args,
   msgprint:v=>messages.push(v),
  };
  frappe.unscrub=s=>s;frappe.utils.icon=()=>'<i>✓</i>';
  window.readProfile=async name=>structuredClone(profileDocs[name]);
  frappe.ui.form.Control=class{
   constructor(args){Object.assign(this,args);this.wrapper=$('<div class="frappe-control">').attr('data-fieldtype',args.df.fieldtype).appendTo(args.parent);this.$wrapper=this.wrapper;this.selected_options=[];this.make();this.refresh();}
   make(){} refresh(){}
  };
  frappe.ui.form.make_control=args=>new frappe.ui.form.ControlMultiCheck(args);
  frappe.ui.Dialog=class{
   constructor(opts){this.opts=opts;this.values={};this.$wrapper=$('<div class="test-dialog">').appendTo('body');
    $('<h4>').text(opts.title).appendTo(this.$wrapper);this.body=$('<div>').appendTo(this.$wrapper)[0];this.fields_dict={};
    for(const df of opts.fields||[]){const $wrapper=$('<div>').appendTo(this.body);this.fields_dict[df.fieldname]={$wrapper};
     if(df.fieldtype==='Link'){const input=$('<input class="form-control">').appendTo($wrapper);input.attr('data-test-field',df.fieldname);
      input.on('change',()=>{this.values[df.fieldname]=input.val();this.change=Promise.resolve(df.onchange?.());});}}
    this.primary=$('<button type="button" class="btn btn-primary test-primary">').text(opts.primary_action_label||'OK').appendTo(this.$wrapper);
    this.primary.on('click',()=>{this.action=Promise.resolve(opts.primary_action?.());});dialogs.push(this);
   }
   get_value(key){return this.values[key];} get_primary_btn(){return this.primary;}
   show(){this.$wrapper.show();} hide(){this.opts.onhide?.();this.$wrapper.hide();}
   set_title(title){this.opts.title=title;}
  };
  window.roleNames=['Sales User','Sales Manager','Purchase User','Stock User','Accounts User','System Manager','China Tax User','Sales Production Release Operator','Unclassified role'];
  const fields={role_profiles:{wrapper:$('#profiles')[0]},roles_html:{wrapper:$('#role-field')[0]},modules_html:{wrapper:$('#module-field')[0]}};
  window.frm={doctype:'User',can_edit_roles:true,events:{},doc:{doctype:'User',name:'sample@example.invalid',roles:[],role_profiles:[],block_modules:[],__onload:{all_modules:['Selling','Buying','Stock','China Finance','Crm Integration','Core','Deeplinkerp Branding','Unclassified Module']},user_type:'System User'},fields_dict:fields,
   is_new:()=>false,get changed(){return !!this.doc.__unsaved;},set changed(value){this.doc.__unsaved=value?1:undefined;},is_dirty(){return this.changed;},dirty(){this.changed=true;},refresh_field(){},refresh_fields(){},set_df_property(field,prop,val){(this.properties??={})[field+'.'+prop]=val;},
   add_child(field,values){return Object.assign(frappe.model.add_child(this.doc,field==='roles'?'Has Role':'User Role Profile',field),values);},
  };
  window.cur_frm=frm;
  locals.User={[frm.doc.name]:frm.doc};frappe.ui.form.handlers=handlers;
  frappe.run_serially=tasks=>tasks.reduce((promise,fn)=>promise.then(fn),Promise.resolve());
  window.has_common=(left,right)=>left.some(value=>right.includes(value));frappe.user_roles=['System Manager'];
  for(const role of ['Sales User','Unclassified role'])frm.add_child('roles',{role});
  for(const name of ['Core','Unclassified Module'])Object.assign(frappe.model.add_child(frm.doc,'Block Module','block_modules'),{module:name});
 },translations);
 for(const file of ['public/js/frappe/model/sync.js','public/js/frappe/db.js','public/js/frappe/request.js','public/js/frappe/form/script_manager.js'])await page.addScriptTag({path:framework+'/'+file});
 await evaluate(nativeFormCall=>{
  frm.call=(new Function('return '+nativeFormCall))();
  frm.script_manager=new frappe.ui.form.ScriptManager({frm});
  // Only the transport is synthetic; use native xcall, db.get_doc, model.sync, Form.call and ScriptManager.
  frappe.request.call=opts=>{
   const request=$.Deferred(),method=opts.url.split('/').at(-1),args=structuredClone(opts.args);
   frappe.request.ajax_count++;
   const finish=()=>{frappe.request.ajax_count--;if(!frappe.request.ajax_count)frappe.request.waiting_for_ajax.splice(0).forEach(fn=>fn());};
   Promise.resolve().then(async()=>{
    let response;
    if(method==='frappe.client.get'){
     requests.push({dt:args.doctype,name:args.name});response={message:await readProfile(args.name)};
    }else if(method.endsWith('get_all_roles'))response={message:roleNames};
    else if(method.endsWith('get_perm_info')){window.permissionRequest=args.role;response={message:[{parent:'Sales Order',permlevel:0,read:1}]};}
    else if(method==='run_doc_method' && args.method==='populate_role_profile_roles'){
     window.syncCalls=(window.syncCalls||0)+1;
     if(window.holdSync)await new Promise(resolve=>window.releaseSync=resolve);
     if(window.failSync)throw Error('Simulated synchronization failure');
     const doc=args.docs,roles=[...new Set(doc.role_profiles.flatMap(row=>profileDocs[row.role_profile].roles.map(item=>item.role)))];
     doc.roles=roles.map((role,index)=>doc.roles.find(row=>row.role===role)||{doctype:'Has Role',name:'server-'+index,role,parent:doc.name,parentfield:'roles',parenttype:'User'});
     if(window.mismatchSync)doc.roles=[];
     delete doc.__unsaved;response={docs:[doc]};
    }else throw Error('Unexpected method '+method);
    if(response.docs)frappe.model.sync(response);
    opts.success?.(response);request.resolve(response);
   }).catch(error=>{opts.error?.({message:error.message});request.reject(error);}).finally(finish);
   return request.promise();
  };
 },nativeFormCall);
 for(const file of ['public/js/frappe/form/controls/multicheck.js','public/js/frappe/roles_editor.js','public/js/frappe/module_editor.js','core/doctype/user/user.js'])await page.addScriptTag({path:framework+'/'+file});
 await page.addScriptTag({path:app+'/public/js/permission_editor.js'});
 await evaluate(async()=>{
  frm.roles_editor=new frappe.RoleEditor($('<div>').appendTo('#role-field'),frm);
  frm.module_editor=new frappe.ModuleEditor(frm,$('<div>').appendTo('#module-field'),false);
  await new Promise(r=>setTimeout(r,20));await DeepLinkERPPermissionEditor.refresh(frm);
 });
 assert.equal(await page.locator('.dlp-permission-detail').count(),9);
 assert.equal(await evaluate(()=>frm.changed),false);
 assert.equal(await evaluate(()=>frm.doc.roles.length),2);
 checks.push('initial rendering preserves assignments');
 const columns=await page.locator('#role-field .checkbox-options').evaluate(element=>getComputedStyle(element).columnCount);
 assert.equal(columns,'1');
 const groups=await page.locator('#role-field .dlp-permission-group').evaluateAll(elements=>elements.map(element=>{const box=element.getBoundingClientRect();return {left:box.left,top:box.top,bottom:box.bottom,width:box.width};}));
 for(let i=1;i<groups.length;i++){assert(Math.abs(groups[i].left-groups[0].left)<1);assert(groups[i].top>=groups[i-1].bottom-1);}
 assert(groups[0].width>800);
 assert.equal(await page.locator('.dlp-permission-description,.dlp-permission-source,.dlp-permission-badge').count(),0);
 assert.equal(await page.locator('.dlp-permission-guide-details').evaluate(element=>element.open),false);
 checks.push('native standard CSS keeps full-width groups and descriptions out of the default layout');
 const salesRow=page.locator('#role-field .unit-checkbox').filter({has:page.locator('input[data-unit="Sales User"]')});
 await salesRow.locator('.label-area').hover();
 const tooltip=page.locator('.dlp-permission-tooltip.show');
 await tooltip.waitFor({state:'visible'});
 assert.match(await tooltip.innerText(),/日常销售单据/);
 const nativeEscapesBefore=await evaluate(()=>nativeEscapeCalls);
 await page.keyboard.press('Escape');await tooltip.waitFor({state:'hidden'});
 assert.equal(await evaluate(()=>nativeEscapeCalls),nativeEscapesBefore);
 await page.keyboard.press('Escape');assert.equal(await evaluate(()=>nativeEscapeCalls),nativeEscapesBefore+1);
 checks.push('Escape dismisses name hover first and then preserves native Desk Escape behavior');
 await page.mouse.move(0,0);await salesRow.locator('.label-area').hover();await tooltip.waitFor({state:'visible'});
 await salesRow.locator('.dlp-permission-help').focus();
 await page.waitForTimeout(200);
 assert.equal(await tooltip.count(),1);
 await salesRow.locator('.dlp-permission-help').press('Escape');await tooltip.waitFor({state:'hidden'});
 await evaluate(()=>{
  const label=$('#role-field input[data-unit="Sales User"]').closest('.unit-checkbox').find('.label-area');
  label.trigger('mouseenter');document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
 });
 await page.waitForTimeout(200);assert.equal(await tooltip.count(),0);
 checks.push('mixed hover and focus show one tip; Escape also cancels pending shows');
 await page.mouse.move(0,0);
 await salesRow.locator('.dlp-permission-help').focus();
 await tooltip.waitFor({state:'visible'});
 assert.match(await tooltip.innerText(),/销售人员/);
 await salesRow.locator('.dlp-permission-help').press('Escape');
 await tooltip.waitFor({state:'hidden'});
 assert.equal(await salesRow.locator('.dlp-permission-help').evaluate(element=>element===document.activeElement),false);
 const purchaseRow=page.locator('#role-field .unit-checkbox').filter({has:page.locator('input[data-unit="Purchase User"]')});
 await purchaseRow.locator('.dlp-permission-help').click();
 await tooltip.waitFor({state:'visible'});
 assert.equal(await purchaseRow.locator('input').isChecked(),false);
 assert.equal(await evaluate(()=>frm.is_dirty()),false);
 await purchaseRow.locator('.dlp-permission-help').press('Escape');
 checks.push('hover, focus and info-button click reveal descriptions without changing assignments');
 await salesRow.locator('.label-area').hover();await tooltip.waitFor({state:'visible'});
 await evaluate(()=>frm.roles_editor.multicheck.make_checkboxes());
 assert.equal(await page.locator('.dlp-permission-tooltip').count(),0);
 assert.equal(await page.locator('#role-field .dlp-permission-filters').count(),1);
 checks.push('native checkbox rebuild disposes old tooltips');
 await page.locator('#role-field input[type=search]').fill('purchas');
 assert.equal(await page.locator('#role-field .unit-checkbox:visible').count(),1);
 await page.locator('#role-field input[data-unit="Purchase User"]').check();
 assert.deepEqual(await evaluate(()=>frm.doc.roles.map(r=>r.role).sort()),['Purchase User','Sales User','Unclassified role']);
 assert.equal(await evaluate(()=>frm.doc.block_modules.length),2);
 checks.push('filtering keeps hidden assignments and native checkbox updates');
 await page.locator('#role-field input[type=search]').fill('');
 await page.locator('#role-field .dlp-permission-filters input[type=checkbox]').check();
 assert.equal(await page.locator('#role-field .unit-checkbox:visible').count(),3);
 await page.locator('#role-field input[data-unit="Purchase User"]').uncheck();
 assert.equal(await page.locator('#role-field .unit-checkbox:visible').count(),2);
 await page.locator('#role-field .dlp-permission-filters input[type=checkbox]').uncheck();
 checks.push('selected-only filter reacts after deselection');
 await evaluate(()=>{frm.roles_editor.disable=true;frm.roles_editor.show();});
 assert.equal(await page.locator('#role-field input[data-unit="Sales User"]').isDisabled(),true);
 assert.equal(await page.locator('#role-field .dlp-permission-filters input[type=checkbox]').isEnabled(),true);
 await salesRow.locator('.dlp-permission-detail').click();
 assert.equal(await evaluate(()=>permissionRequest),'Sales User');
 await evaluate(()=>{dialogs.at(-1).hide();frm.roles_editor.disable=false;frm.roles_editor.show();frm.changed=false;});
 checks.push('native lock preserved while search and permission detail remain available');
 const moduleInput=page.locator('#module-field input[data-unit="Core"]');
 await moduleInput.check();
 assert.deepEqual(await evaluate(()=>frm.doc.block_modules.map(r=>r.module)),['Unclassified Module']);
 assert.equal(await page.locator('#role-field input[data-unit="Sales User"]').isChecked(),true);
 await page.getByRole('button',{name:'查看已保存的用户权限',exact:true}).click();
 assert.equal(await evaluate(()=>window.lastRoute??null),null);
 assert.equal(await evaluate(()=>messages.length),1);
 checks.push('module visibility uses native block-list and saved-report guard');
 await evaluate(()=>{frm.changed=false;});
 await page.getByRole('button',{name:'配置公司／仓库数据范围',exact:true}).click();
 assert.deepEqual(await evaluate(()=>lastRoute),['List','User Permission',{user:'sample@example.invalid'}]);
 checks.push('record scope uses native user permission list');
 await page.getByRole('button',{name:'预览并添加岗位模板',exact:true}).click();
 await page.locator('.test-dialog:visible input').fill('Sales');await page.locator('.test-dialog:visible input').dispatchEvent('change');
 await evaluate(()=>dialogs.at(-1).change);
 assert.match(await page.locator('.dlp-permission-preview').innerText(),/移除角色[\s\S]*Unclassified role/);
 assert.equal(await evaluate(()=>frm.doc.role_profiles.length),0);
 const beforeFailure=await evaluate(()=>({roles:structuredClone(frm.doc.roles),profiles:structuredClone(frm.doc.role_profiles),dirty:frm.is_dirty()}));
 await evaluate(()=>{window.failSync=true;window.holdSync=true;});
 await page.locator('.test-dialog:visible .test-primary').click();
 await page.waitForFunction(()=>!!window.releaseSync);
 assert.equal(await page.locator('#role-field input[data-unit="Sales User"]').isDisabled(),true);
 assert.equal(await evaluate(()=>frozen),1);
 await evaluate(()=>releaseSync());await evaluate(()=>dialogs.at(-1).action);
 assert.equal(await page.locator('.test-dialog:visible').count(),1);
 assert.match(await page.locator('.test-dialog:visible').innerText(),/无法应用/);
 assert.deepEqual(await evaluate(()=>({roles:structuredClone(frm.doc.roles),profiles:structuredClone(frm.doc.role_profiles),dirty:frm.is_dirty()})),beforeFailure);
 assert.equal(await page.locator('#role-field input[data-unit="Sales User"]').isEnabled(),true);
 assert.equal(await evaluate(()=>frozen),0);
 checks.push('failed native synchronization restores pristine form and keeps dialog open');
 await evaluate(()=>{window.failSync=false;window.holdSync=false;});
 await page.locator('.test-dialog:visible input').dispatchEvent('change');await evaluate(()=>dialogs.at(-1).change);
 const callsBefore=await evaluate(()=>syncCalls);
 await page.locator('.test-dialog:visible .test-primary').click();await evaluate(()=>dialogs.at(-1).action);
 assert.equal(await evaluate(()=>syncCalls),callsBefore+1);
 assert.deepEqual(await evaluate(()=>frm.doc.roles.map(r=>r.role)),['Sales User']);
 assert.equal(await evaluate(()=>frm.doc.role_profiles[0].role_profile),'Sales');
 assert.equal(await page.locator('#role-field input[data-unit="Sales User"]').isDisabled(),true);
 assert.match(await salesRow.locator('.dlp-permission-help').getAttribute('data-original-title'),/来自岗位模板：Sales/);
 checks.push('template delta previews removals and waits for a single native synchronization request');
 // A template read must not overwrite a different Desk form's unsaved draft in locals.
 const draftBefore=await evaluate(()=>{
  const draft={doctype:'Role Profile',name:'Sales',role_profile:'Sales',__unsaved:1,roles:[
   {doctype:'Has Role',name:'draft-role-1',role:'Sales User',parent:'Sales',parentfield:'roles',parenttype:'Role Profile'},
   {doctype:'Has Role',name:'draft-role-2',role:'Accounts User',parent:'Sales',parentfield:'roles',parenttype:'Role Profile'},
  ]};frappe.model.sync(draft);return structuredClone(locals['Role Profile'].Sales);
 });
 await evaluate(()=>DeepLinkERPPermissionEditor.refresh(frm));
 assert.deepEqual(await evaluate(()=>structuredClone(locals['Role Profile'].Sales)),draftBefore);
 assert.equal(await evaluate(()=>locals['Has Role']['draft-role-2'].role),'Accounts User');
 checks.push('automatic source reads preserve cached template draft, dirty flag and child identities');
 // Rollback must also preserve an already dirty user, its existing template lock and unrelated edits.
 await evaluate(()=>{frm.doc.full_name='Unsaved name';frm.dirty();window.failSync=true;});
 await page.getByRole('button',{name:'预览并添加岗位模板',exact:true}).click();
 await page.locator('.test-dialog:visible input').fill('Warehouse');await page.locator('.test-dialog:visible input').dispatchEvent('change');await evaluate(()=>dialogs.at(-1).change);
 const dirtyBefore=await evaluate(()=>({roles:structuredClone(frm.doc.roles),profiles:structuredClone(frm.doc.role_profiles),dirty:frm.is_dirty(),full_name:frm.doc.full_name}));
 await page.locator('.test-dialog:visible .test-primary').click();await evaluate(()=>dialogs.at(-1).action);
 assert.deepEqual(await evaluate(()=>({roles:structuredClone(frm.doc.roles),profiles:structuredClone(frm.doc.role_profiles),dirty:frm.is_dirty(),full_name:frm.doc.full_name})),dirtyBefore);
 assert.equal(await page.locator('#role-field input[data-unit="Sales User"]').isDisabled(),true);
 checks.push('failed synchronization preserves earlier edits, selected templates and role lock');
 await evaluate(()=>{window.failSync=false;window.mismatchSync=true;});
 await page.locator('.test-dialog:visible input').dispatchEvent('change');await evaluate(()=>dialogs.at(-1).change);
 await page.locator('.test-dialog:visible .test-primary').click();await evaluate(()=>dialogs.at(-1).action);
 assert.match(await page.locator('.test-dialog:visible').innerText(),/无法应用/);
 assert.deepEqual(await evaluate(()=>({roles:structuredClone(frm.doc.roles),profiles:structuredClone(frm.doc.role_profiles),dirty:frm.is_dirty(),full_name:frm.doc.full_name})),dirtyBefore);
 await evaluate(()=>{window.mismatchSync=false;dialogs.at(-1).hide();});
 checks.push('unexpected native role result restores the confirmed form state');
 await page.getByRole('button',{name:'预览并添加岗位模板',exact:true}).click();
 await page.locator('.test-dialog:visible input').fill('Warehouse');await page.locator('.test-dialog:visible input').dispatchEvent('change');
 await evaluate(()=>dialogs.at(-1).change);
 await evaluate(()=>{profileDocs.Warehouse.roles=[{role:'Stock User'},{role:'System Manager'}];});
 await page.locator('.test-dialog:visible .test-primary').click();await evaluate(()=>dialogs.at(-1).action);
 assert.equal(await evaluate(()=>frm.doc.role_profiles.length),1);
 assert((await page.locator('.test-dialog:visible .dlp-permission-preview').innerText()).includes(translations.zh['System Manager'] || 'System Manager'));
 await evaluate(()=>dialogs.at(-1).hide());
 checks.push('changed template requires review again before any form mutation');
 // A dismissed preview must never apply an asynchronously completed read.
 await page.getByRole('button',{name:'预览并添加岗位模板',exact:true}).click();
 await page.locator('.test-dialog:visible input').fill('Warehouse');await page.locator('.test-dialog:visible input').dispatchEvent('change');
 await evaluate(()=>dialogs.at(-1).change);
 await evaluate(()=>{window.readResolvers=[];window.readProfile=name=>new Promise(resolve=>readResolvers.push(()=>resolve(structuredClone(profileDocs[name]))));dialogs.at(-1).primary.trigger('click');});
 try{
  await page.waitForFunction(()=>readResolvers.length===2);
  const beforeCancelRequests=await evaluate(()=>requests.length);
  await evaluate(()=>{dialogs.at(-1).hide();readResolvers.splice(0).forEach(resolve=>resolve());});
  await evaluate(()=>dialogs.at(-1).action);
  // Also force a late change after cancellation, independent of browser blur timing.
  await page.locator('.test-dialog').last().locator('input').dispatchEvent('change');
  await evaluate(()=>dialogs.at(-1).change);
  await page.waitForFunction(()=>frappe.request.ajax_count===0);
  assert.equal(await evaluate(()=>requests.length),beforeCancelRequests);
  assert.equal(await evaluate(()=>frm.doc.role_profiles.length),1);
  assert.equal(await evaluate(()=>dialogs.at(-1).primary.prop('disabled')),true);
 }finally{
  await evaluate(()=>{readProfile=async name=>structuredClone(profileDocs[name]);readResolvers.splice(0).forEach(resolve=>resolve());});
 }
 checks.push('cancelled preview blocks late change events and drains all held requests');
 // Unknown purpose and operator translation are explicit, without claiming release access.
 assert.match(await page.locator('#role-field').innerText(),/销售生产放行操作员/);
 assert.doesNotMatch(await page.locator('#role-field').innerText(),/实际绑定|用途说明待补充/);
 const releaseInfo=page.locator('#role-field .unit-checkbox').filter({has:page.locator('input[data-unit="Sales Production Release Operator"]')}).locator('.dlp-permission-help');
 await releaseInfo.hover();await tooltip.waitFor({state:'visible'});
 assert.match(await tooltip.innerText(),/实际绑定/);
 await page.screenshot({path:out+'/user-permissions-help-zh.png',fullPage:true});
 await page.locator('#role-field .unit-checkbox').filter({has:page.locator('input[data-unit="Unclassified role"]')}).locator('.dlp-permission-help').hover();
 await page.waitForFunction(()=>document.querySelector('.dlp-permission-tooltip.show')?.textContent.includes('用途说明待补充'));
 await evaluate(()=>frm.script_manager.trigger('on_hide','User',frm.doc.name));
 assert.equal(await page.locator('.dlp-permission-tooltip.show').count(),0);
 await page.screenshot({path:out+'/user-permissions-zh.png',fullPage:true});
 checks.push('unknown-purpose and release binding explanations appear on demand; route hide closes tooltips');
 // Simulate a second record and a denied template read; old sources must be cleared.
 await evaluate(async()=>{frm.doc.name='second@example.invalid';frm.doc.role_profiles=[{role_profile:'Denied'}];readProfile=async()=>{throw Error('denied');};await DeepLinkERPPermissionEditor.refresh(frm);});
 assert.match(await page.locator('.dlp-permission-profile-note').innerText(),/无法读取/);
 assert((await page.locator('#role-field .dlp-permission-help').evaluateAll(elements=>elements.map(element=>element.getAttribute('data-original-title')||''))).every(value=>!value.includes('来自岗位模板')));
 checks.push('read denial clears previous user template sources');
 // Three-language runtime construction, mobile width and dark theme.
 for(const language of ['en','es']){
  await evaluate(language=>{lang=language;document.querySelector('main').innerHTML='<h3>Permissions</h3><div id="profiles"></div><div id="role-field"></div><div id="module-field"></div>';frm.doc.role_profiles=[];frm.fields_dict.role_profiles.wrapper=$('#profiles')[0];frm.fields_dict.roles_html.wrapper=$('#role-field')[0];frm.fields_dict.modules_html.wrapper=$('#module-field')[0];delete frm.dlp_permission_guide;frm.roles_editor=new frappe.RoleEditor($('<div>').appendTo('#role-field'),frm);frm.module_editor=new frappe.ModuleEditor(frm,$('<div>').appendTo('#module-field'),false);readProfile=async name=>structuredClone(profileDocs[name]);},language);
  await page.waitForTimeout(30);await evaluate(()=>DeepLinkERPPermissionEditor.refresh(frm));
  assert.equal(await page.locator('.dlp-permission-guide').count(),1);
  assert.match(await page.locator('.dlp-permission-guide').innerText(),language==='es'?/Asignar permisos/:/Assign permissions/);
  await page.screenshot({path:out+`/user-permissions-${language}.png`,fullPage:true});
 }
 checks.push('English and Spanish rendering');
 await page.setViewportSize({width:390,height:844});
 const mobileHelp=page.locator('#role-field .unit-checkbox').filter({has:page.locator('input[data-unit="Purchase User"]')}).locator('.dlp-permission-help');
 const beforeTap=await evaluate(()=>frm.doc.roles.map(row=>row.role));
 await mobileHelp.tap();await tooltip.waitFor({state:'visible'});
 assert.match(await tooltip.innerText(),/Documentos diarios de compras/);
 assert.deepEqual(await evaluate(()=>frm.doc.roles.map(row=>row.role)),beforeTap);
 const tooltipBox=await tooltip.boundingBox();assert(tooltipBox.x>=0 && tooltipBox.x+tooltipBox.width<=390);
 await page.locator('h3').tap();await tooltip.waitFor({state:'hidden'});
 checks.push('touch opens a viewport-safe tooltip without changing roles');
 await page.screenshot({path:out+'/user-permissions-mobile.png',fullPage:true});
 assert(await evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 await evaluate(()=>document.documentElement.setAttribute('data-theme','dark'));
 await page.screenshot({path:out+'/user-permissions-dark.png',fullPage:true});
 checks.push('mobile layout and dark theme');
 // Evaluate each real doctype script only when that form first opens, as Desk does.
 const formScripts={User:readFileSync(framework+'/core/doctype/user/user.js','utf8'),
  'Role Profile':readFileSync(framework+'/core/doctype/role_profile/role_profile.js','utf8'),
  'Module Profile':readFileSync(framework+'/core/doctype/module_profile/module_profile.js','utf8')};
 const enhancement=readFileSync(app+'/public/js/permission_editor.js','utf8');
 const openingOrders=[['User','Role Profile','Module Profile'],['User','Module Profile','Role Profile'],
  ['Role Profile','User','Module Profile'],['Role Profile','Module Profile','User'],
  ['Module Profile','User','Role Profile'],['Module Profile','Role Profile','User']];
 for(const order of openingOrders){
  await evaluate(async({order,formScripts,enhancement})=>{
   delete window.DeepLinkERPPermissionEditor;
   frappe.ui.form.handlers={};window.handlers=frappe.ui.form.handlers;
   window.lifecycleForms={};
   for(const doctype of order){
    document.querySelector('main').innerHTML='<div id="profiles"></div><div id="role-field"></div><div id="module-field"></div>';
    const form={...frm,doctype,events:{},can_edit_roles:true,doc:{doctype,name:'lifecycle-'+doctype,user_type:'System User',roles:[],role_profiles:[],block_modules:[],__onload:{all_modules:['Stock','Core']}},
     roles_editor:null,module_editor:null,dlp_permission_guide:null,fields_dict:{role_profiles:{wrapper:$('#profiles')[0]},roles_html:{wrapper:$('#role-field')[0]},modules_html:{wrapper:$('#module-field')[0]},module_html:{wrapper:$('#module-field')[0]}}};
    (locals[doctype]??={})[form.doc.name]=form.doc;
    window.cur_frm=form;window.lifecycleForms[doctype]=form;
    (new Function(formScripts[doctype]))();
    const nativeRefresh=handlers[doctype].refresh.at(-1);
    (new Function(enhancement))();(new Function(enhancement))();
    if(handlers[doctype].refresh[0]!==nativeRefresh || handlers[doctype].refresh.length!==2)throw Error('Incorrect lifecycle registration order: '+doctype);
    form.script_manager=new frappe.ui.form.ScriptManager({frm:form});
    if(doctype==='User'){
     // Use native onload for roles; only the module-construction part of User.refresh is needed here.
     await form.script_manager.trigger('onload',doctype,form.doc.name);
     form.module_editor=new frappe.ModuleEditor(form,$('<div>').appendTo('#module-field'),false);
     await handlers.User.refresh.at(-1)(form);
    }else await form.script_manager.trigger('refresh',doctype,form.doc.name);
    await frappe.after_ajax();
    const editor=doctype==='Module Profile'?form.module_editor:form.roles_editor;
    if(!editor?.dlp_guide)throw Error('Missing first-refresh enhancement: '+doctype);
    const expected=doctype==='User'?2:1;
    if(document.querySelectorAll('.dlp-permission-filters').length!==expected)throw Error('Missing or duplicate filter bars: '+doctype);
   }
  },{order,formScripts,enhancement});
 }
 checks.push('all six first-opening orders register after native handlers and enhance the first refresh');
 // The final form is User; template forms must also retain native model updates.
 await evaluate(()=>{
  const form=lifecycleForms['Role Profile'];
  form.roles_editor.multicheck.selected_options=['Purchase User'];form.roles_editor.set_roles_in_table();
  const modules=lifecycleForms['Module Profile'];
  modules.module_editor.multicheck.selected_options=['Core'];modules.module_editor.set_modules_in_table();
 });
 assert.deepEqual(await evaluate(()=>lifecycleForms['Role Profile'].doc.roles.map(row=>row.role)),['Purchase User']);
 assert.deepEqual(await evaluate(()=>lifecycleForms['Module Profile'].doc.block_modules.map(row=>row.module)),['Stock']);
 checks.push('native role and module template editors retain model updates');
 assert.deepEqual(errors,[]);
 writeFileSync(out+'/browser-result.json',JSON.stringify({passed:true,mode:'Synthetic transport and forms; actual Frappe request helpers, model synchronization, Form.call, ScriptManager, native editors and doctype scripts; no site writes',checks,errors},null,2));
 console.log(JSON.stringify({passed:true,checks:checks.length},null,2));
} finally{await browser.close();}
