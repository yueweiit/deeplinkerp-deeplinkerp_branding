"""Verify Stock Entry quantity labels with the branding app alone."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "public/js/stock_entry_quantity_labels.js"


def test_receipt_labels_restore_when_purpose_changes() -> None:
	javascript = f"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const handlers = [];
global.frappe = {{ui:{{form:{{on:(doctype, events)=>{{if(doctype==='Stock Entry') handlers.push(events)}}}}}}}};
global.__ = text => text;
vm.runInThisContext(fs.readFileSync({json.dumps(str(SCRIPT))}, 'utf8'));
const events = handlers[0];
const original = {{transfer_qty:'Issued Qty',actual_qty:'Actual Qty (at source/target)'}};
const grid = {{docfields:Object.entries(original).map(([fieldname,label])=>({{fieldname,label}})),
  update_docfield_property(fieldname,property,value) {{
    assert.equal(property,'label');
    this.docfields.find(field=>field.fieldname===fieldname).label=value;
  }}}};
const frm = {{doc:{{purpose:'Material Receipt',stock_entry_type:'Material Receipt',
    posting_date:'2024-01-01',posting_time:'09:00:00'}},fields_dict:{{items:{{grid}}}}}};
const labels = () => Object.fromEntries(grid.docfields.map(field=>[field.fieldname,field.label]));
events.onload(frm);
assert.deepEqual(labels(),{{transfer_qty:'入库数量（库存单位）',actual_qty:'目标仓过账时点库存'}});
frm.doc.purpose='Material Issue';frm.doc.stock_entry_type='Material Issue';events.purpose(frm);
assert.deepEqual(labels(),original);
frm.doc.purpose='';frm.doc.stock_entry_type='Material Receipt';events.stock_entry_type(frm);
assert.deepEqual(labels(),{{transfer_qty:'入库数量（库存单位）',actual_qty:'目标仓过账时点库存'}});
frm.doc.stock_entry_type='Material Issue';events.stock_entry_type(frm);
assert.deepEqual(labels(),original);
const shared = global.DeepLinkERPStockEntryQuantityLabels;
frm.doc.stock_entry_type='Finished Goods Receipt';shared.refresh(frm,true);
events.refresh(frm);
assert.deepEqual(labels(),{{transfer_qty:'入库数量（库存单位）',actual_qty:'目标仓过账时点库存'}});
frm.doc.stock_entry_type='Material Issue';events.stock_entry_type(frm);
assert.deepEqual(labels(),original);
"""
	result = subprocess.run(["node", "-e", javascript], text=True, capture_output=True)
	assert result.returncode == 0, result.stderr
