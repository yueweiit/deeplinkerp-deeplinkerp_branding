"""Unified inventory detail client rendering and safety tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHARED_JS = ROOT / "deeplinkerp_branding/public/js/inventory_detail.bundle.js"
SHARED_CSS = ROOT / "deeplinkerp_branding/public/css/inventory_detail.bundle.css"
MODULE_ROOT = ROOT / "deeplinkerp_branding/deeplinkerp_branding"


def run_js(body: str) -> dict:
	source = f"const inventory=require({json.dumps(str(SHARED_JS))});\n{body}"
	result = subprocess.run(["node", "-e", source], text=True, capture_output=True)
	assert result.returncode == 0, result.stderr
	return json.loads(result.stdout)


def test_reversal_stage_blocks_existing_inventory_movement_without_losing_selection():
	result = run_js("""
const group={item_code:'I',warehouse:'W',reversal:{stage:'failed'},locations:[{}]};
const key=inventory.selectionKey(group),selected=new Map([[key,{item_code:'I',source_warehouse:'W'}]]);
const next=inventory.updateCurrentPageSelection([group],selected,true);
const html=inventory.renderMaterialRows([group],new Set([key]));
const button={length:1,text(){},prop(k,v){this[k]=v;},attr(){}};
inventory.InventoryDetailPage.prototype.updateMovementButton.call({$movementButton:button,selected:next,canCreateStockEntry:true});
console.log(JSON.stringify({disabled:button.disabled,selected:next.size,html}));
""")
	assert result["disabled"] is True
	assert result["selected"] == 1
	assert "失败待处理" in result["html"]


def test_material_rows_use_one_selectable_checkbox_per_item_warehouse_group() -> None:
	result = run_js(
		"""
const html=inventory.renderMaterialRows([{
 item_code:'A',item_name:'物料A',warehouse:'W1',total_qty:5,stock_uom:'个：pieza',
 item_group:'物料',locations:[{original_location:'L1',location_qty:2},{original_location:'L2',location_qty:3}]
}]);
console.log(JSON.stringify({html}));
"""
	)
	html = result["html"]
	assert html.count('type="checkbox"') == 1
	assert html.count('rowspan="2"') == 10
	assert "data-selection-key" in html


def test_placeholder_without_a_warehouse_cannot_be_selected() -> None:
	result = run_js(
		"""
const html=inventory.renderCategorizedRows([{
 item_code:'A',item_name:'半成品A',warehouse:'',actual_qty:0,stock_uom:'个：pieza',item_group:'半成品',
 inventory_status:'库位待维护',locations:[{reference_location:'库位待维护',snapshot_location_qty:null,snapshot_date:''}]
}]);
console.log(JSON.stringify({html}));
"""
	)
	assert 'type="checkbox"' in result["html"]
	assert "disabled" in result["html"]


def test_current_page_select_all_ignores_placeholder_rows_and_can_clear_again() -> None:
	result = run_js(
		"""
const groups=[
 {item_code:'A',warehouse:'W1'},
 {item_code:'B',warehouse:'W2'},
 {item_code:'C',warehouse:''}
];
const selected=inventory.updateCurrentPageSelection(groups,new Map(),true);
const cleared=inventory.updateCurrentPageSelection(groups,selected,false);
console.log(JSON.stringify({
 selected:[...selected.values()],
 cleared:cleared.size
}));
"""
	)
	assert result == {
		"selected": [
			{"item_code": "A", "source_warehouse": "W1"},
			{"item_code": "B", "source_warehouse": "W2"},
		],
		"cleared": 0,
	}


def test_shared_page_uses_integer_page_size_and_only_opens_an_unsaved_stock_entry() -> None:
	source = SHARED_JS.read_text(encoding="utf-8")
	assert 'fieldname: "page_length"' in source
	assert 'fieldtype: "Int"' in source
	assert "DEFAULT_PAGE_LENGTH = 200" in source
	assert "MIN_PAGE_LENGTH = 1" in source
	assert "MAX_PAGE_LENGTH = 2500" in source
	assert "get_inventory_movement_context" in source
	assert "prepare_inventory_stock_entry" in source
	assert 'frappe.set_route("Form", "Stock Entry"' in source
	assert "overseas_costing.services.inventory_location_service" not in source
	assert "this.resetSelection();" not in source.split("async refresh(", 1)[1].split("renderPager(payload)", 1)[0]
	assert '__("物料移动 ({0})", [this.selected.size])' in source
	assert "this.movementActionDataLabel" not in source
	assert ".menu-item-label" not in source
	assert 'aria-disabled' in source
	for forbidden in (".insert(", ".save(", ".submit(", "frappe.client.insert"):
		assert forbidden not in source


def test_reset_selection_clears_visible_row_and_header_checkboxes() -> None:
	source = SHARED_JS.read_text(encoding="utf-8")
	reset_selection = source.split("resetSelection() {", 1)[1].split(
		"updateSelectionUi() {", 1
	)[0]

	assert '"[data-selection-key], [data-select-current-page]"' in reset_selection
	assert '$selectionInputs.prop("checked", false);' in reset_selection
	assert '$selectionInputs.prop("indeterminate", false);' in reset_selection


LIFECYCLE_SETUP = """
const element={length:1,find(){return this},prop(){return this},attr(){return this},
 text(){return this},html(){return this},addClass(){return this},removeClass(){return this}};
function makePage(){
 const values={company:'C1',keyword:'',warehouse:'W1',page_length:1};
 const fields=Object.fromEntries(Object.keys(values).map(k=>[k,{get_value:()=>values[k],
  set_value:async v=>{values[k]=v}}]));
 const page=Object.assign(Object.create(inventory.InventoryDetailPage.prototype),{
  fields,config:{category:'finished_goods'},isMaterial:false,start:0,pageLength:1,requestId:0,
  selected:new Map(),currentGroups:[],company:'C1',effectiveCompany:'C1',
  selectionCompany:'C1',canCreateStockEntry:true,$root:element,$movementButton:element,
  setItemGroupOptions(){},renderPager(){}});
 return {page,values};
}
const group=(code)=>({item_code:code,item_name:'Name '+code,warehouse:'W1',locations:[]});
global.frappe={msgprint(){},call:async()=>({message:{company:'C1',groups:[group('A')],can_create_stock_entry:true}})};
"""


def test_pending_inventory_auto_refresh_updates_selection_and_disposes_stale_page():
	result = run_js(LIFECYCLE_SETUP + """
(async()=>{
 const handlers=new Map(),timers=new Map();let tick=0,calls=0;
 global.setTimeout=fn=>{timers.set(++tick,fn);return tick};global.clearTimeout=id=>timers.delete(id);
 frappe.realtime={on:(event,fn)=>handlers.set(event,fn),off:(event,fn)=>{if(handlers.get(event)===fn)handlers.delete(event)}};
 const {page}=makePage();const row={...group('A'),reversal:{operation_id:'OP',stage:'waiting_inventory'}};
 page.currentGroups=[row];page.selected=inventory.updateCurrentPageSelection([row],page.selected,true);
 frappe.call=async()=>{calls++;throw Error('poll failed')};
 page.fitViewport(true);const subscribed=handlers.has('purchase_reversal_progress'),scheduled=timers.size;
 const poll=timers.values().next().value;timers.clear();await poll();
 const failed={stage:page.selected.values().next().value.reversal.stage,blocked:!page.canCreateStockEntry,rows:page.currentGroups.length};
 frappe.call=async()=>{calls++;return {message:{company:'C1',groups:[group('B')],can_create_stock_entry:true,
  selected_reversals:[{item_code:'A',warehouse:'W1',reversal:null}]}}};
 await handlers.get('purchase_reversal_progress')({operation_id:'OP',stage:'completed'});
 const completed={size:page.selected.size,reversal:page.selected.values().next().value.reversal,canMove:page.canCreateStockEntry,timers:timers.size};
 let resolve;frappe.call=()=>new Promise(r=>{resolve=r});const pending=page.refresh();page.fitViewport(false);
 resolve({message:{company:'C1',groups:[group('STALE')],can_create_stock_entry:true}});await pending;
 console.log(JSON.stringify({subscribed,scheduled,failed,completed,disposed:{handlers:handlers.size,timers:timers.size,code:page.currentGroups[0].item_code},calls}));
})();
""")
	assert result == {"subscribed": True, "scheduled": 1,
		"failed": {"stage": "waiting_inventory", "blocked": True, "rows": 1},
		"completed": {"size": 1, "reversal": None, "canMove": True, "timers": 0},
		"disposed": {"handlers": 0, "timers": 0, "code": "B"}, "calls": 2}


@pytest.mark.parametrize("category", ["material", "semi_finished", "finished_goods", "mold"])
def test_actual_refresh_retains_selection_across_search_filter_paging_and_errors(category: str) -> None:
	result = run_js(LIFECYCLE_SETUP + f"const category={json.dumps(category)};\n" + """
(async()=>{
 const {page,values}=makePage();
 page.config.category=category;page.isMaterial=category==='material';page.setSnapshotOptions=()=>{};
 await page.refresh();
 page.selected=inventory.updateCurrentPageSelection(page.currentGroups,page.selected,true);
 values.keyword='B'; frappe.call=async()=>({message:{company:'C1',groups:[group('B')],can_create_stock_entry:true}});
 await page.refresh(true);
 page.selected=inventory.updateCurrentPageSelection(page.currentGroups,page.selected,true);
 values.warehouse=''; page.start=1; await page.refresh();
 const count=page.selected.size;
 page.selected=inventory.updateCurrentPageSelection(page.currentGroups,page.selected,false);
 const offpage=[...page.selected.values()].map(r=>r.item_code);
 frappe.call=async()=>{throw Error('permission denied')}; await page.refresh();
 const failure={count:page.selected.size,permission:page.canCreateStockEntry};
 page.resetSelection();
 console.log(JSON.stringify({count,offpage,failure,reset:page.selected.size}));
})();
""")
	assert result == {"count": 2, "offpage": ["A"], "failure": {"count": 1, "permission": False}, "reset": 0}


def test_company_switch_cancel_keeps_filters_and_selections_and_ignores_pending_response() -> None:
	result = run_js(LIFECYCLE_SETUP + """
(async()=>{
 const {page,values}=makePage();
 page.selected=inventory.updateCurrentPageSelection([group('A')],page.selected,true);
 let resolve; frappe.call=()=>new Promise(r=>resolve=r);
 const loading=page.refresh(); values.company='C2';
 let cancel; frappe.confirm=(message,yes,no)=>{cancel=no};
 const changing=page.changeCompany();
 const pending={company:values.company,warehouse:values.warehouse,loading:page.loading};
 let recoveryCompany,resolveRecovery,recoveryStarted;
 const recovery=new Promise(resolve=>recoveryStarted=resolve);
 frappe.call=({args})=>{recoveryCompany=args.filters.company;recoveryStarted();
  return new Promise(resolve=>resolveRecovery=resolve)};
 cancel(); await recovery;
 const recovering={loading:page.loading,movable:page.canMoveSelection()};
 resolveRecovery({message:{company:'C1',groups:[group('A')],can_create_stock_entry:true}});
 await changing;
 resolve({message:{company:'C2',groups:[group('B')],can_create_stock_entry:true}}); await loading;
 console.log(JSON.stringify({pending,company:values.company,warehouse:values.warehouse,
  count:page.selected.size,effective:page.effectiveCompany,groups:page.currentGroups.length,
  movable:page.canMoveSelection(),loading:page.loading,recoveryCompany,recovering}));
})();
""")
	assert result == {"pending": {"company": "C1", "warehouse": "W1", "loading": True}, "company": "C1", "warehouse": "W1", "count": 1, "effective": "C1", "groups": 1, "movable": True, "loading": False, "recoveryCompany": "C1", "recovering": {"loading": True, "movable": False}}


def test_movement_context_permission_failure_preserves_selection_and_requires_fresh_inventory() -> None:
	result = run_js(LIFECYCLE_SETUP + """
(async()=>{
 const {page}=makePage();page.selected=inventory.updateCurrentPageSelection([group('A')],page.selected,true);
 let calls=0,message;frappe.msgprint=value=>message=value;
 frappe.call=async()=>{calls++;throw Error('permission revoked')};
 await page.openMovementDialog();await page.openMovementDialog();
 const failure={calls,count:page.selected.size,movable:page.canMoveSelection(),message};
 frappe.call=async()=>({message:{company:'C1',groups:[group('A')],can_create_stock_entry:true}});
 await page.refresh();
 console.log(JSON.stringify({failure,recovered:page.canMoveSelection(),count:page.selected.size}));
})();
""")
	assert result == {"failure": {"calls": 1, "count": 1, "movable": False, "message": "permission revoked"}, "recovered": True, "count": 1}


@pytest.mark.parametrize("category", ["material", "semi_finished", "finished_goods", "mold"])
def test_persistent_header_reenables_after_initial_and_repeated_refresh(category: str) -> None:
	result = run_js(LIFECYCLE_SETUP + f"const category={json.dumps(category)};\n" + """
(async()=>{
 const {page}=makePage();page.config.category=category;page.isMaterial=category==='material';
 page.setSnapshotOptions=()=>{};
 const header={length:1,state:{},prop(key,value){this.state[key]=value;return this}};
 let row={length:1,state:{},prop:header.prop};
 const body={html(value){row={length:1,state:{},prop:header.prop};return this}};
 const events={};
 page.$root={addClass(){return this},removeClass(){return this},
  on(event,selector,callback){events[selector]=callback;return this},
  find(selector){
   if(selector==='tbody') return body;
   if(selector==='[data-select-current-page]') return header;
   if(selector==='[data-selection-key], [data-select-current-page]')
    return {length:2,prop(key,value){header.prop(key,value);row.prop(key,value);return this}};
   return element;
  }};
 page.bindEvents();
 const initialLoad=page.refresh();const during=header.state.disabled;await initialLoad;
 const initial=header.state.disabled;
 events['[data-select-current-page]']({currentTarget:{checked:true}});
 frappe.call=async()=>({message:{company:'C1',groups:[group('B')],can_create_stock_entry:false}});
 await page.refresh();const repeated={disabled:header.state.disabled,checked:header.state.checked};
 events['[data-select-current-page]']({currentTarget:{checked:true}});
 const count=page.selected.size;
 frappe.call=async()=>{throw Error('permission denied')};await page.refresh();const failed=header.state.disabled;
 frappe.call=async()=>({message:{company:'C1',groups:[group('B')],can_create_stock_entry:true}});
 await page.refresh();
 console.log(JSON.stringify({during,initial,repeated,count,failed,recovered:header.state.disabled,
  recoveredChecked:header.state.checked}));
})();
""")
	assert result == {"during": True, "initial": False, "repeated": {"disabled": False, "checked": False}, "count": 2, "failed": True, "recovered": False, "recoveredChecked": True}


def test_company_confirmation_accept_clears_once_and_cancel_restores_racing_field_edit() -> None:
	result = run_js(LIFECYCLE_SETUP + """
(async()=>{
 const {page,values}=makePage();
 page.selected=inventory.updateCurrentPageSelection([group('A')],page.selected,true);
 let yes,no,calls=0; frappe.confirm=(message,y,n)=>{yes=y;no=n};
 values.company='C2'; const cancelled=page.changeCompany();
 values.company='C3'; no(); await cancelled;
 const cancelCompany=values.company;
 values.company='C2'; const accepted=page.changeCompany();
 frappe.call=async()=>{calls++;return {message:{company:'C2',groups:[group('B')],can_create_stock_entry:true}}};
 yes(); await accepted;
 console.log(JSON.stringify({cancelCompany,company:values.company,warehouse:values.warehouse,
  count:page.selected.size,calls,movable:page.canMoveSelection()}));
})();
""")
	assert result == {"cancelCompany": "C1", "company": "C2", "warehouse": "", "count": 0, "calls": 1, "movable": False}


def test_company_confirmation_dismiss_releases_pending_state_and_reentrant_change_is_ignored() -> None:
	result = run_js(LIFECYCLE_SETUP + """
(async()=>{
 const {page,values}=makePage();let hidden;
 page.selected=inventory.updateCurrentPageSelection([group('A')],page.selected,true);
 page.fields.company.set_value=async value=>{values.company=value;await page.changeCompany()};
 frappe.confirm=()=>({$wrapper:{on(event,callback){hidden=callback}}});
 values.company='C2';const changing=page.changeCompany();
 if (!hidden){console.log(JSON.stringify({handler:false}));return;}
 hidden();await changing;
 console.log(JSON.stringify({handler:true,pending:page.companyChangePending,
  company:values.company,warehouse:values.warehouse,count:page.selected.size}));
})();
""")
	assert result == {"handler": True, "pending": False, "company": "C1", "warehouse": "W1", "count": 1}


def test_loading_unknown_company_and_stale_movement_context_cannot_open_dialog() -> None:
	result = run_js(LIFECYCLE_SETUP + """
(async()=>{
 const {page,values}=makePage();
 page.selected=inventory.updateCurrentPageSelection([group('A')],page.selected,true);
 let calls=0,resolve,dialogs=0;
 frappe.ui={Dialog:function(){dialogs++}};
 frappe.call=()=>{calls++;return new Promise(r=>resolve=r)};
 page.loading=true; await page.openMovementDialog(); page.loading=false;
 page.effectiveCompany=''; await page.openMovementDialog(); page.effectiveCompany='C1';
 const opening=page.openMovementDialog(); ++page.requestId;
 resolve({message:{items:[group('A')]}}); await opening;
 console.log(JSON.stringify({calls,dialogs}));
})();
""")
	assert result == {"calls": 1, "dialogs": 0}


def test_removing_selection_while_movement_context_loads_cannot_open_old_dialog() -> None:
	result = run_js(LIFECYCLE_SETUP + """
(async()=>{
 const {page}=makePage();
 page.selected=inventory.updateCurrentPageSelection([group('A'),group('B')],page.selected,true);
 let resolve,dialogs=0; frappe.ui={Dialog:function(){dialogs++;this.show=()=>{}}};
 frappe.call=()=>new Promise(r=>resolve=r);
 const opening=page.openMovementDialog();page.selected.delete(inventory.selectionKey(group('A')));
 resolve({message:{items:[group('A'),group('B')]}});await opening;
 console.log(JSON.stringify({dialogs,count:page.selected.size}));
})();
""")
	assert result == {"dialogs": 0, "count": 1}


def test_instances_have_independent_selection_and_stale_prepare_cannot_open_stock_entry() -> None:
	result = run_js(LIFECYCLE_SETUP + """
(async()=>{
 const {page}=makePage();const other=makePage().page;
 page.selected=inventory.updateCurrentPageSelection([group('A')],page.selected,true);
 let resolve,opened=0; page.openUnsavedStockEntry=()=>opened++;
 frappe.call=()=>new Promise(r=>resolve=r);
 const preparing=page.prepareMovement({hide(){}},{items:[{item_code:'A',source_warehouse:'W1',qty:1}]},'C1');
 ++page.requestId;resolve({message:{stock_entry:{name:'NEW'}}});await preparing;
 console.log(JSON.stringify({opened,other:other.selected.size,count:page.selected.size}));
})();
""")
	assert result == {"opened": 0, "other": 0, "count": 1}


def test_selected_dialog_removes_individual_offpage_items_and_clears_all() -> None:
	result = run_js(LIFECYCLE_SETUP + """
const {page}=makePage();
page.selected=inventory.updateCurrentPageSelection([group('A'),group('B')],page.selected,true);
let options,remove,html='';
const wrapper={html(v){html=v},on(event,selector,callback){remove=callback}};
frappe.ui={Dialog:function(o){options=o;this.fields_dict={selections:{$wrapper:wrapper}};this.show=()=>{}}};
global.$=target=>({attr:()=>target.key});
page.openSelectionDialog(); const initial=html;
remove({currentTarget:{key:inventory.selectionKey(group('A'))}});
const remaining=[...page.selected.values()].map(row=>row.item_code);
options.primary_action();
console.log(JSON.stringify({initial,remaining,count:page.selected.size,empty:html}));
""")
	assert "Name A" in result["initial"] and "W1" in result["initial"]
	assert result["remaining"] == ["B"]
	assert result["count"] == 0 and "暂无已选物料" in result["empty"]


def test_all_four_routes_are_thin_wrappers_over_the_shared_component() -> None:
	pages = {
		"inventory_location_detail": "material",
		"semi_finished_inventory_detail": "semi_finished",
		"finished_goods_inventory_detail": "finished_goods",
		"mold_inventory_detail": "mold",
	}
	for folder, category in pages.items():
		page = MODULE_ROOT / "page" / folder
		script = (page / f"{folder}.js").read_text(encoding="utf-8")
		definition = json.loads((page / f"{folder}.json").read_text(encoding="utf-8"))
		assert "InventoryDetail.bootstrap" in script
		assert f'category: "{category}"' in script
		assert "renderMaterialRows" not in script
		assert definition["module"] == "Deeplinkerp Branding"


def test_shared_assets_are_registered_once_in_branding_hooks() -> None:
	hooks = (ROOT / "deeplinkerp_branding/hooks.py").read_text(encoding="utf-8")
	assert "inventory_detail.bundle.js" in hooks
	assert "inventory_detail.bundle.css" in hooks
	assert SHARED_CSS.is_file()


@pytest.mark.parametrize("category", ["material", "semi_finished", "finished_goods", "mold"])
def test_movement_action_is_in_page_content_not_responsive_toolbar(category: str) -> None:
	result = run_js(
		f"""
let html='';
const button={{length:1,text(){{return this}},prop(){{return this}},attr(){{return this}}}};
const root={{find(){{return button}}}};
global.$=(value)=>typeof value==='string' ? (html=value,root) :
 {{children:()=>({{remove(){{}}}}),append(){{}}}};
const page=Object.assign(Object.create(inventory.InventoryDetailPage.prototype),{{
 config:{{title:'库存明细'}},isMaterial:{json.dumps(category == 'material')},
 page:{{body:{{}}}},selected:new Map(),canCreateStockEntry:false
}});
page.renderShell();
console.log(JSON.stringify({{html}}));
"""
	)
	assert 'data-inventory-action="movement"' in result["html"]
	assert 'class="id-actions"' in result["html"]
	source = SHARED_JS.read_text(encoding="utf-8")
	assert 'add_inner_button("物料移动' not in source
	assert "'[data-inventory-action=\"movement\"]'" in source


@pytest.mark.parametrize("count,can_create", [(0, True), (1, True), (200, True), (2, False)])
def test_content_movement_action_preserves_count_and_creation_permission(count: int, can_create: bool) -> None:
	result = run_js(
		f"""
const state={{}};
const button={{length:1,text(v){{state.label=v;return this}},
 prop(k,v){{state[k]=v;return this}},attr(k,v){{state[k]=v;return this}}}};
inventory.InventoryDetailPage.prototype.updateMovementButton.call({{
 $movementButton:button,selected:new Map(Array.from({{length:{count}}},(_,i)=>[i,{{}}])),
 canCreateStockEntry:{json.dumps(can_create)},movementDisabledReason:'权限说明'
}});
console.log(JSON.stringify(state));
"""
	)
	assert result == {
		"label": f"物料移动 ({count})",
		"disabled": not can_create or count == 0,
		"aria-disabled": "true" if not can_create or count == 0 else "false",
		"title": "权限说明",
	}
