"""Zero receipt confirmations are auditable records without stock effects."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from deeplinkerp_branding.deeplinkerp_branding.doctype.zero_receipt_confirmation import (
	zero_receipt_confirmation as controller,
)


PACKAGE = Path(__file__).resolve().parents[1]


def _record(**values):
	return SimpleNamespace(check_permission=lambda _action: None, **values)


@pytest.fixture
def valid_confirmation(monkeypatch):
	objects = {
		("Stock Entry Type", "Material Receipt"): _record(purpose="Material Receipt"),
		("Stock Entry Type", "Material Issue"): _record(purpose="Material Issue"),
		("Item", "ITEM-1"): _record(disabled=0, is_stock_item=1, stock_uom="Nos"),
		("Warehouse", "W1"): _record(company="C1", is_group=0, disabled=0),
		("Material Request", "MR-1"): _record(
			company="C1", docstatus=1, items=[SimpleNamespace(name="MRI-1", item_code="ITEM-1")]
		),
		("Material Request", "MR-2"): _record(
			company="C1", docstatus=1, items=[SimpleNamespace(name="MRI-2", item_code="ITEM-1")]
		),
	}
	monkeypatch.setattr(controller, "_", lambda message: message)
	monkeypatch.setattr(controller.frappe, "get_doc", lambda doctype, name: objects[doctype, name])
	monkeypatch.setattr(controller.frappe, "has_permission", lambda doctype, action: (doctype, action) in {
		("Stock Entry", "create"), ("Stock Entry", "submit")
	})
	monkeypatch.setattr(controller.frappe, "throw", lambda message, *_args: (_ for _ in ()).throw(ValueError(message)))
	row = SimpleNamespace(
		idx=1, item_code="ITEM-1", target_warehouse="W1", received_qty=0,
		stock_uom="", material_request=None, material_request_item=None,
	)
	doc = SimpleNamespace(
		docstatus=0, company="C1", stock_entry_type="Material Receipt",
		reason=" No delivery ", source_note=" Walk-in receipt ", items=[row],
	)
	return doc, row, objects


def test_valid_confirmation_records_zero_without_creating_stock_entry(valid_confirmation):
	doc, row, _objects = valid_confirmation
	controller.ZeroReceiptConfirmation.validate(doc)
	controller.ZeroReceiptConfirmation.before_submit(doc)
	assert doc.reason == "No delivery"
	assert doc.source_note == "Walk-in receipt"
	assert row.received_qty == 0
	assert row.stock_uom == "Nos"


@pytest.mark.parametrize("change, message", [
	(lambda doc, row, objects: setattr(row, "received_qty", 1), "必须为 0"),
	(lambda doc, row, objects: setattr(doc, "stock_entry_type", "Material Issue"), "仅支持入库类型"),
	(lambda doc, row, objects: setattr(objects["Warehouse", "W1"], "company", "C2"), "不属于所选公司"),
	(lambda doc, row, objects: setattr(objects["Warehouse", "W1"], "is_group", 1), "不可使用"),
	(lambda doc, row, objects: setattr(objects["Item", "ITEM-1"], "disabled", 1), "不可用于库存确认"),
	(lambda doc, row, objects: doc.items.append(SimpleNamespace(**{**vars(row), "idx": 2})), "重复"),
])
def test_invalid_confirmation_is_rejected(valid_confirmation, change, message):
	doc, row, objects = valid_confirmation
	change(doc, row, objects)
	with pytest.raises(ValueError, match=message):
		controller.ZeroReceiptConfirmation.validate(doc)


@pytest.mark.parametrize("quantity", [None, "", "  ", "not-a-number", False, "NaN", float("inf"), 0.0001])
def test_quantity_must_be_explicit_numeric_zero(valid_confirmation, quantity):
	doc, row, _objects = valid_confirmation
	row.received_qty = quantity
	with pytest.raises(ValueError, match="必须为 0"):
		controller.ZeroReceiptConfirmation.validate(doc)


def test_material_request_source_needs_no_manual_note(valid_confirmation):
	doc, row, _objects = valid_confirmation
	doc.source_note = ""
	row.material_request = "MR-1"
	controller.ZeroReceiptConfirmation.validate(doc)
	assert row.material_request_item == "MRI-1"


def test_separate_material_requests_can_confirm_same_item_and_warehouse(valid_confirmation):
	doc, row, _objects = valid_confirmation
	doc.source_note = ""
	row.material_request = "MR-1"
	row.material_request_item = "MRI-1"
	doc.items.append(SimpleNamespace(**{
		**vars(row), "idx": 2, "material_request": "MR-2", "material_request_item": "MRI-2"
	}))
	controller.ZeroReceiptConfirmation.validate(doc)


def test_inferred_material_request_row_cannot_duplicate_an_explicit_row(valid_confirmation):
	doc, row, _objects = valid_confirmation
	doc.source_note = ""
	row.material_request = "MR-1"
	row.material_request_item = "MRI-1"
	doc.items.append(SimpleNamespace(**{**vars(row), "idx": 2, "material_request_item": None}))
	with pytest.raises(ValueError, match="来源重复"):
		controller.ZeroReceiptConfirmation.validate(doc)


@pytest.mark.parametrize("change, message", [
	(lambda doc, row, objects: setattr(doc, "source_note", ""), "填写来源说明"),
	(lambda doc, row, objects: (setattr(row, "material_request", None), setattr(row, "material_request_item", "MRI-1")), "来源无效"),
	(lambda doc, row, objects: setattr(objects["Material Request", "MR-1"], "company", "C2"), "来源无效"),
	(lambda doc, row, objects: setattr(objects["Material Request", "MR-1"], "docstatus", 0), "来源无效"),
	(lambda doc, row, objects: setattr(row, "material_request_item", "MRI-2"), "不匹配"),
	(lambda doc, row, objects: setattr(objects["Material Request", "MR-1"].items[0], "item_code", "OTHER"), "不匹配"),
	(lambda doc, row, objects: objects["Material Request", "MR-1"].items.append(SimpleNamespace(name="MRI-3", item_code="ITEM-1")), "唯一确定"),
])
def test_invalid_material_request_source_is_rejected(valid_confirmation, change, message):
	doc, row, objects = valid_confirmation
	if message != "填写来源说明":
		row.material_request = "MR-1"
	change(doc, row, objects)
	with pytest.raises(ValueError, match=message):
		controller.ZeroReceiptConfirmation.validate(doc)


def test_confirmation_requires_stock_entry_permissions(valid_confirmation, monkeypatch):
	doc, _row, _objects = valid_confirmation
	monkeypatch.setattr(controller.frappe, "has_permission", lambda _doctype, action: action == "submit")
	with pytest.raises(ValueError, match="没有创建物料移动单的权限"):
		controller.ZeroReceiptConfirmation.validate(doc)
	monkeypatch.setattr(controller.frappe, "has_permission", lambda _doctype, action: action == "create")
	with pytest.raises(ValueError, match="没有提交物料移动单的权限"):
		controller.ZeroReceiptConfirmation.before_submit(doc)
	doc.docstatus = 1
	with pytest.raises(ValueError, match="没有提交物料移动单的权限"):
		controller.ZeroReceiptConfirmation.validate(doc)


def test_cancellation_does_not_depend_on_current_item_or_warehouse_state(valid_confirmation, monkeypatch):
	doc, _row, objects = valid_confirmation
	doc.docstatus = 2
	objects["Item", "ITEM-1"].disabled = 1
	monkeypatch.setattr(controller.frappe, "has_permission", lambda _doctype, _action: False)
	controller.ZeroReceiptConfirmation.validate(doc)


def test_stock_entry_action_copies_only_zero_receipt_rows():
	script = PACKAGE / "public/js/stock_entry_zero_receipt.js"
	javascript = f"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const handlers=[];const actions={{}};const messages=[];const routes=[];
let confirmation;
global.__=text=>text;
global.frappe={{ui:{{form:{{on:(doctype,events)=>handlers.push(events)}}}},
 model:{{can_create:()=>true,with_doctype:(_doctype,callback)=>callback(),
  get_new_doc:()=>confirmation={{name:'new-zero-receipt',items:[],receipt_date:'today',receipt_time:'now'}},
  add_child:(doc,doctype,field)=>{{assert.equal(doctype,'Zero Receipt Confirmation Item');
   const row={{}};doc[field].push(row);return row;}}}},
 msgprint:message=>messages.push(message),set_route:(...args)=>routes.push(args)}};
vm.runInThisContext(fs.readFileSync({json.dumps(str(script))},'utf8'));
const frm={{is_new:()=>true,doc:{{purpose:'Material Receipt',company:'C1',stock_entry_type:'Material Receipt',
 posting_date:'2026-11-12',posting_time:'10:00:00',items:[{{item_code:'I1',qty:0,t_warehouse:'W1',stock_uom:'Nos',
 material_request:'MR-1',material_request_item:'MRI-1'}}]}},
 add_custom_button:(label,action)=>actions[label]=action,
 remove_custom_button:label=>delete actions[label]}};
handlers[0].refresh(frm);
actions['记录零数量入库']();
assert.deepEqual(routes,[['Form','Zero Receipt Confirmation','new-zero-receipt']]);
assert.equal(confirmation.company,'C1');assert.equal(confirmation.receipt_date,'2026-11-12');
assert.equal(confirmation.receipt_time,'10:00:00');
assert.deepEqual(confirmation.items,[{{item_code:'I1',target_warehouse:'W1',received_qty:0,stock_uom:'Nos',
 material_request:'MR-1',material_request_item:'MRI-1'}}]);
for(const empty of [null,undefined,'','  ','not-a-number']){{
 frm.doc.items[0].qty=empty;actions['记录零数量入库']();
 assert.equal(routes.length,1);assert.match(messages.at(-1),/数量为 0/);
}}
frm.doc.items[0].qty=1;actions['记录零数量入库']();
assert.equal(routes.length,1);assert.match(messages.at(-1),/正数入库/);
frm.doc.items[0].qty=0;frm.doc.items[0].s_warehouse='SOURCE';actions['记录零数量入库']();
assert.equal(routes.length,1);assert.match(messages.at(-1),/来源仓库/);
frm.doc.purpose='Material Issue';handlers[0].purpose(frm);
assert.deepEqual(actions,{{}});
"""
	result = subprocess.run(["node", "-e", javascript], text=True, capture_output=True)
	assert result.returncode == 0, result.stderr


def test_confirmation_metadata_is_submittable_and_zero_quantity_is_read_only():
	doctypes = PACKAGE / "deeplinkerp_branding/doctype"
	parent = json.loads((doctypes / "zero_receipt_confirmation/zero_receipt_confirmation.json").read_text())
	child = json.loads((doctypes / "zero_receipt_confirmation_item/zero_receipt_confirmation_item.json").read_text())
	assert parent["is_submittable"] == 1 and parent["track_changes"] == 1
	assert child["istable"] == 1
	assert next(field for field in child["fields"] if field["fieldname"] == "received_qty")["read_only"] == 1
