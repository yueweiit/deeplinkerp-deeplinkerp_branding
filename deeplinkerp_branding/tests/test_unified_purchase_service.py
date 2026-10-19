"""Real merge/filter behavior with only the Frappe read boundary substituted."""

from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime
from io import BytesIO
from types import SimpleNamespace
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest


def backend():
	assert importlib.util.find_spec("deeplinkerp_branding.services.unified_purchase_service"), (
		"Unified purchase read backend is not implemented"
	)
	return importlib.import_module("deeplinkerp_branding.services.unified_purchase_service")


def po(name="PO-1", **values):
	return {
		"name": name, "transaction_date": "2026-10-01", "supplier": "SUP-1",
		"supplier_name": "供应商", "status": "Closed", "company": "Yuewei",
		"currency": "USD", "grand_total": 0, "advance_paid": 0,
		"party_account_currency": "CNY", "advance_payment_status": "Not Paid",
		"per_received": 0, "per_billed": 0, "schedule_date": None,
		"project": None, "owner": "buyer@example.test", "docstatus": 1,
		"custom_oa_purchase_expense": None, **values,
	}


def oa(name="OA-1", **values):
	return {
		"name": name, "oa_code": "审批-1", "apply_date": "2026-09-30",
		"creation": datetime(2026, 9, 29, 8), "target_company": "Yuewei",
		"currency": "人民币RMB", "detail_total_amount": 10,
		"processor_total_amount": 20, "payment_amount": 30,
		"purchase_order": None, "approval_status": "APPROVED", "sync_status": "DONE",
		"process_instance_id": "PROCESS-1", "owner": "requester@example.test",
		"order_no": "采购编号-1", **values,
	}


def test_closed_zero_orders_remain_and_explicit_links_alone_deduplicate():
	s = backend()
	result = s.build_unified_purchase_payload(
		[po(custom_oa_purchase_expense="OA-1"), po("采购编号-1")],
		[oa(), oa("OA-2", purchase_order="PO-1"), oa("OA-3")],
	)
	assert result["total_count"] == 3
	assert {row["row_key"] for row in result["rows"]} == {
		"Purchase Order::PO-1", "Purchase Order::采购编号-1", "OA Purchase Request::OA-3",
	}
	linked = next(row for row in result["rows"] if row["name"] == "PO-1")
	assert linked["grand_total"] == 0
	assert linked["source"] == "OA"
	assert linked["oa_amount"] is None
	assert "多个" in linked["oa_warning"]


def test_linked_metadata_is_merged_before_date_company_search_and_approval_filters():
	s = backend()
	result = s.build_unified_purchase_payload(
		[po(custom_oa_purchase_expense="OA-1")], [oa(target_company="Other")],
		filters={"search": "审批-1", "from_date": "2026-10-01", "to_date": "2026-10-01",
			"company": "Yuewei", "approval_status": "APPROVED", "source": "oa"},
	)
	assert result["total_count"] == 1
	row = result["rows"][0]
	assert row["oa_name"] == "OA-1"
	assert row["oa_number"] == "审批-1"
	assert row["oa_amount"] == 10
	assert row["oa_currency"] == "CNY"
	assert row["oa_amount_basis"] == "采购明细合计"
	assert row["company"] == "Yuewei"


def test_inaccessible_linked_order_keeps_oa_without_leaking_order_name():
	s = backend()
	result = s.build_unified_purchase_payload([], [oa(purchase_order="SECRET-PO")])
	row = result["rows"][0]
	assert row["name"] == "OA-1"
	assert row["status"] == "已生成订单（无权查看）"
	assert "SECRET-PO" not in str(result)
	assert row["row_type"] == "oa_request"
	assert row["grand_total"] is None
	assert row["supplier"] is None
	assert row["docstatus"] is None


def test_managed_source_keeps_zero_and_separates_request_cashier_and_erp():
	request=oa(currency="USD",custom_purchase_pending_reason="原生默认税费待核对",custom_purchase_source_json=json.dumps({"eligible":True,"version":"v1","currency":"CNY","requested_amount":"999","detail_total_amount":"0","issues":[]}),
		custom_cashier_payment_evidence=json.dumps({"paid_amount":"20","currency":"CNY","payment_evidence_status":"recorded"}))
	row=backend().build_unified_purchase_payload([po(custom_oa_purchase_expense="OA-1",grand_total=100,advance_paid=5)],[request])["rows"][0]
	assert row["oa_amount"] == "0"
	assert row["oa_currency"] == "CNY"
	assert row["requested_amount"] == "999"
	assert row["cashier_paid_amount"] == "20"
	assert row["grand_total"] == 100 and row["advance_paid"] == 5
	assert row["source_version"] == "v1" and row["source_eligible"] is True
	assert "原生默认税费待核对" in row["oa_warning"]


def test_quick_order_status_and_advance_status_are_not_ignored():
	s=backend()
	assert s.build_unified_purchase_payload([po()],[],filters={"status":"Draft"})["total_count"] == 0
	assert s.build_unified_purchase_payload([po()],[],filters={"advance_payment_status":"Paid"})["total_count"] == 0


def test_conflicting_order_links_do_not_assign_the_same_oa_amount_twice():
	s = backend()
	result = s.build_unified_purchase_payload(
		[po(custom_oa_purchase_expense="OA-1"), po("PO-2")],
		[oa(purchase_order="PO-2")],
	)
	assert result["total_count"] == 2
	assert all(row["oa_amount"] is None for row in result["rows"])
	assert all("冲突" in row["oa_warning"] for row in result["rows"])
	assert result["totals"]["oa"] == []


def test_conflicting_links_keep_readable_per_oa_provenance_on_both_orders():
	s = backend()
	result = s.build_unified_purchase_payload(
		[po(custom_oa_purchase_expense="OA-1"), po("PO-2")],
		[oa(purchase_order="PO-2")],
	)
	for row in result["rows"]:
		assert row["oa_amount"] is None
		assert len(row["oa_references"]) == 1
		reference = row["oa_references"][0]
		assert {key: reference[key] for key in (
			"name", "number", "approval_status", "amount", "currency", "amount_basis", "company",
		)} == {
			"name": "OA-1", "number": "审批-1", "approval_status": "APPROVED", "amount": 10,
			"currency": "CNY", "amount_basis": "采购明细合计", "company": "Yuewei",
		}
		assert "冲突" in reference["warning"]


def test_multiple_oa_references_keep_each_amount_and_approval_without_aggregate_choice():
	s = backend()
	row = s.build_unified_purchase_payload([po(custom_oa_purchase_expense="OA-1")], [
		oa(), oa("OA-2", purchase_order="PO-1", approval_status="RUNNING", currency="USD", detail_total_amount=99),
	])["rows"][0]
	assert row["oa_amount"] is None
	assert [(ref["name"], ref["amount"], ref["approval_status"]) for ref in row["oa_references"]] == [
		("OA-1", 10, "APPROVED"), ("OA-2", 99, "RUNNING"),
	]


def test_company_mismatch_keeps_both_native_companies_and_warns():
	s = backend()
	row = s.build_unified_purchase_payload([po(custom_oa_purchase_expense="OA-1")],
		[oa(target_company="Other")])["rows"][0]
	assert row["company"] == "Yuewei"
	assert row["oa_references"][0]["company"] == "Other"
	assert "公司" in row["oa_warning"]
	assert "公司" in row["oa_references"][0]["warning"]


def test_unknown_currency_in_conflicting_references_is_counted_once_per_oa():
	result = backend().build_unified_purchase_payload(
		[po(custom_oa_purchase_expense="OA-1"), po("PO-2")],
		[oa(purchase_order="PO-2", currency="神秘币")],
	)
	assert result["totals"]["oa_unknown_currency_count"] == 1
	assert result["totals"]["oa"] == []


@pytest.mark.parametrize(
	("detail", "processor", "payment", "amount", "basis", "zero_warning"),
	[(12.345678, 20, 30, 12.345678, "采购明细合计", False),
		(0, -4, 30, -4, "加工合计", False),
		(None, 0, -7, -7, "付款申请金额", False),
		(0, 0, 0, 0, "付款申请金额", True),
		(None, None, None, None, None, False)],
)
def test_oa_amount_precedence_preserves_precision_negative_zero_and_null(
	detail, processor, payment, amount, basis, zero_warning,
):
	row = backend().build_unified_purchase_payload([], [oa(
		detail_total_amount=detail, processor_total_amount=processor, payment_amount=payment,
	)])["rows"][0]
	assert row["oa_amount"] == amount
	assert row["oa_amount_basis"] == basis
	assert ("原始金额为0，待核实" in (row["oa_warning"] or "")) is zero_warning


def test_unknown_currency_is_visible_but_excluded_from_currency_totals():
	s = backend()
	result = s.build_unified_purchase_payload(
		[po(grand_total=40)],
		[oa(), oa("OA-2", currency="比索Peso", detail_total_amount=2),
			oa("OA-3", currency="神秘币", detail_total_amount=100)],
	)
	assert result["totals"] == {
		"orders": [{"currency": "USD", "amount": 40}],
		"oa": [{"currency": "CNY", "amount": 10}, {"currency": "MXN", "amount": 2}],
		"oa_unknown_currency_count": 1,
	}
	unknown = next(row for row in result["rows"] if row["name"] == "OA-3")
	assert unknown["oa_currency"] is None
	assert "币种" in unknown["oa_warning"]


def test_currency_codes_require_recognition_or_currency_master_evidence():
	s = backend()
	result = s.build_unified_purchase_payload([], [oa(currency="XYZ")])
	assert result["rows"][0]["oa_currency"] is None
	known = s.build_unified_purchase_payload([], [oa(currency="XYZ")], currency_codes={"XYZ"})
	assert known["rows"][0]["oa_currency"] == "XYZ"


def test_unconfirmed_company_uses_original_cached_date_without_creation_fallback():
	s = backend()
	request = oa(apply_date=None, target_company=None, _source={"apply_date": "2026-09-29"})
	result = s.build_unified_purchase_payload([], [request],
		filters={"company": "__unconfirmed__", "from_date": "2026-09-29", "to_date": "2026-09-29"})
	assert result["rows"][0]["transaction_date"] == "2026-09-29"
	assert result["rows"][0]["company"] is None
	assert result["rows"][0]["status"] == "未生成订单"
	row = s.build_unified_purchase_payload([], [oa(apply_date=None)])["rows"][0]
	assert row["transaction_date"] is None
	assert s.build_unified_purchase_payload([], [oa(apply_date=None)], filters={"from_date": "2026-09-29"})["total_count"] == 0


@pytest.mark.parametrize(("scope", "names"), [
	("all", {"PO-1", "PO-2", "OA-2"}), ("orders", {"PO-1", "PO-2"}),
	("oa", {"OA-2"}),
])
def test_scope_tabs_partition_real_orders_and_unconverted_oa_sources(scope, names):
	result = backend().build_unified_purchase_payload(
		[po(custom_oa_purchase_expense="OA-1"), po("PO-2")], [oa(), oa("OA-2")],
		filters={"scope": scope},
	)
	assert {row["name"] for row in result["rows"]} == names
	if scope == "oa":
		assert result["total_count"] == 1 and result["totals"]["orders"] == []


def test_pagination_is_stable_and_totals_cover_all_filtered_rows():
	s = backend()
	orders = [po("PO-3", grand_total=3), po("PO-1", grand_total=1), po("PO-2", grand_total=2), po("PO-MISSING", grand_total=0, transaction_date=None)]
	first = s.build_unified_purchase_payload(orders, [], page_length=1)
	second = s.build_unified_purchase_payload(orders, [], start=1, page_length=1)
	assert first["rows"][0]["name"] == "PO-3"
	assert second["rows"][0]["name"] == "PO-2"
	assert first["has_previous"] is False and first["has_next"] is True
	assert second["has_previous"] is True and second["has_next"] is True
	assert first["totals"] == second["totals"]
	assert first["totals"]["orders"] == [{"currency": "USD", "amount": 6}]
	assert [row["name"] for row in s.build_unified_purchase_payload(orders, [], start=1)["rows"]] == ["PO-2", "PO-1", "PO-MISSING"]
	assert [row[0] for row in s.build_unified_purchase_export(orders, [], columns=["name"])[1:]] == ["PO-3", "PO-2", "PO-1", "PO-MISSING"]


@pytest.mark.parametrize("kwargs", [
	{"page_length": 0}, {"page_length": 2501}, {"page_length": 1.2}, {"page_length": True},
	{"start": -1}, {"start": 1.2}, {"start": True},
	{"order_by": "transaction_date desc; DROP TABLE `tabPurchase Order`"},
	{"order_by": "unknown desc"}, {"order_by": "name sideways"},
	{"filters": {"scope": "secret"}}, {"filters": {"source": "secret"}},
])
def test_invalid_pagination_sort_and_scope_are_rejected(kwargs):
	with pytest.raises(ValueError):
		backend().build_unified_purchase_payload([], [], **kwargs)


def test_unreadable_association_and_amount_fields_are_not_inferred():
	s = backend()
	order = po()
	del order["custom_oa_purchase_expense"]
	request = oa(detail_total_amount=0, payment_amount=88)
	del request["processor_total_amount"]
	del request["currency"]
	del request["target_company"]
	result = s.build_unified_purchase_payload([order], [request])
	assert next(row for row in result["rows"] if row["name"] == "PO-1")["source"] is None
	row = next(row for row in result["rows"] if row["name"] == "OA-1")
	assert row["oa_amount"] is None
	assert row["company"] is None
	assert row["oa_currency"] is None
	assert s.build_unified_purchase_payload([order], [], filters={"source": "non_oa"})["total_count"] == 0


def test_unconfirmed_company_filter_does_not_infer_unreadable_company_is_empty():
	s = backend()
	order, request = po(), oa()
	del order["company"]
	del request["target_company"]
	result = s.build_unified_purchase_payload([order], [request], filters={"company": "__unconfirmed__"})
	assert result["total_count"] == 0


@pytest.mark.parametrize("row_type", ["purchase_order", "oa_request"])
@pytest.mark.parametrize("visibility", ["hidden", "pending", "known"])
def test_company_visibility_distinguishes_field_permissions_from_business_pending(row_type, visibility):
	s = backend()
	record = po() if row_type == "purchase_order" else oa()
	company_field = "company" if row_type == "purchase_order" else "target_company"
	if visibility == "hidden":
		del record[company_field]
	elif visibility == "pending":
		record[company_field] = None
	orders, requests = ([record], []) if row_type == "purchase_order" else ([], [record])
	result = s.build_unified_purchase_payload(orders, requests)
	row = result["rows"][0]
	assert row["company_visibility"] == visibility
	assert not any(key.startswith("_") for key in row)
	pending = s.build_unified_purchase_payload(orders, requests, filters={"company": "__unconfirmed__"})
	assert pending["total_count"] == (1 if visibility == "pending" else 0)


class ReadBoundary:
	PermissionError = PermissionError

	def __init__(self, orders=(), requests=(), *, oa_installed=True, read=None, export=None,
		native_export=None, owner_export=(), fields=None, document_denied=()):
		self.records = {"Purchase Order": list(orders), "OA Purchase Request": list(requests)}
		self.installed = {"Purchase Order"} | ({"OA Purchase Request"} if oa_installed else set())
		self.read = self.installed if read is None else set(read)
		self.export = self.installed if export is None else set(export)
		self.native_export = self.export if native_export is None else set(native_export)
		self.owner_export = set(owner_export)
		self.fields = fields or {key: set().union(*(row.keys() for row in value)) for key, value in self.records.items()}
		self.calls = []
		self.field_calls = []
		self.response = {}
		self.private_reads = []
		self.db = SimpleNamespace(exists=self.exists, get_values=self.get_values)
		self.permissions = SimpleNamespace(can_export=self.can_export)
		self.session = SimpleNamespace(user="buyer@example.test")
		self.document_reads = []
		self.document_denied = set(document_denied)

	def exists(self, doctype, name):
		if doctype == "DocType":
			return name in self.installed
		return doctype == "Currency" and name in {"CNY", "MXN", "USD", "XYZ"}

	def get_values(self, doctype, filters, fields, **kwargs):
		if filters.get("custom_purchase_reversal_operation") == ["is", "set"]:
			return [(row["name"],) for row in self.records[doctype] if row["name"] in filters["name"][1] and row.get("custom_purchase_reversal_operation")]
		self.private_reads.append((doctype, filters, fields))
		return [{field: row.get(field) for field in fields} for row in self.records[doctype]
			if row["name"] in filters["name"][1]]

	def has_permission(self, doctype, permission_type="read"):
		return doctype in (self.read if permission_type == "read" else self.export)

	def can_export(self, doctype, is_owner=False):
		return doctype in (self.owner_export if is_owner else self.native_export)

	def get_meta(self, doctype):
		installed = set().union(*(row.keys() for row in self.records.get(doctype, []))) | self.fields.get(doctype, set())
		return SimpleNamespace(has_field=lambda field: field in installed)

	def get_doc(self, doctype, name):
		record = next(row for row in self.records[doctype] if row["name"] == name)
		def check_permission(permission):
			self.document_reads.append((doctype, name, permission))
			if not self.has_permission(doctype, permission) or (doctype, name) in self.document_denied:
				raise PermissionError
		return SimpleNamespace(get=record.get, check_permission=check_permission)

	def permitted_fields(self, doctype, **kwargs):
		self.field_calls.append((doctype, kwargs))
		return self.fields.get(doctype, {"name"})

	def get_list(self, doctype, **kwargs):
		self.calls.append((doctype, kwargs))
		assert self.has_permission(doctype, "read")
		assert set(kwargs["fields"]) <= self.fields[doctype]
		return [{field: row.get(field) for field in kwargs["fields"]} for row in self.records[doctype]]

	def throw(self, message, exception=ValueError):
		raise exception(message)


def connect(monkeypatch, boundary):
	s = backend()
	monkeypatch.setattr(s, "frappe", boundary)
	monkeypatch.setattr(s, "get_permitted_fields", boundary.permitted_fields)
	monkeypatch.setattr(s, "get_workflow_name", lambda doctype: "")
	from contextlib import nullcontext
	monkeypatch.setattr(s, "_request_reader", nullcontext)
	monkeypatch.setattr(s, "_permitted_fields", lambda dt, **kwargs: set(boundary.permitted_fields(dt, permission_type="read", **kwargs)))
	# Native record hydration is exercised in test_crossborder_progress. This
	# boundary covers unified query/canonical/export behavior without a site.
	monkeypatch.setattr(s, "_load_order_progress", lambda names, include_items=False: {}, raising=False)
	monkeypatch.setattr(s, "_restricted_order_progress", lambda name: {"name": name, "state": "restricted", "review_required": True})
	monkeypatch.setattr(s, "_project_source_roles", lambda rows, sources, fields, orders: {}, raising=False)
	monkeypatch.setattr(s, "_load_company_scope", lambda names: set(names), raising=False)
	monkeypatch.setattr(s, "_load_order_fields_scope", lambda: True)
	return s


def connect_native_queries(monkeypatch, boundary, *, workflow_field=None, fieldtypes=None):
	"""Use the existing query guards; substitute only Frappe metadata/IO boundaries."""
	s = connect(monkeypatch, boundary)
	fieldtypes = fieldtypes or {}
	metadata = {dt: set(fields) | set().union(*(row.keys() for row in boundary.records.get(dt, [])))
		for dt, fields in boundary.fields.items()}
	metadata.setdefault("Purchase Order Item", set()).update({"name", "item_code", "qty"})
	metadata["Purchase Order"].add("items")
	def get_meta(doctype):
		fields = []
		for name in metadata[doctype]:
			values = {"fieldname": name, "fieldtype": fieldtypes.get(name, "Data"), "permlevel": 0}
			if name == "items":
				values.update(fieldtype="Table", options="Purchase Order Item",
					permlevel=0 if name in boundary.fields[doctype] else 1)
			fields.append(SimpleNamespace(**values, get=values.get))
		return SimpleNamespace(fields=fields, default_fields={"name", "creation", "modified"},
			get_valid_columns=lambda: metadata[doctype],
			get_permlevel_access=lambda **kwargs: {0},
			has_field=lambda field: field in metadata[doctype],
			get_field=lambda name: next((df for df in fields if df.fieldname == name), None))
	monkeypatch.setattr(boundary, "get_meta", get_meta, raising=False)
	monkeypatch.setattr(boundary, "whitelist", lambda **kwargs: lambda fn: fn, raising=False)
	monkeypatch.setattr(boundary, "_", lambda text: text, raising=False)
	monkeypatch.setitem(sys.modules, "frappe", boundary)
	monkeypatch.setitem(sys.modules, "frappe.model", SimpleNamespace(get_permitted_fields=boundary.permitted_fields))
	monkeypatch.setitem(sys.modules, "frappe.utils", SimpleNamespace(getdate=lambda value: value, nowdate=lambda: "2026-10-06"))
	name = "deeplinkerp_branding.services.purchase_payment_service"
	spec = importlib.util.spec_from_file_location(name, __import__("pathlib").Path(s.__file__).with_name("purchase_payment_service.py"))
	queries = importlib.util.module_from_spec(spec)
	monkeypatch.setitem(sys.modules, name, queries)
	spec.loader.exec_module(queries)
	monkeypatch.setattr(queries, "_normalize_filter", lambda dt, field, op, value: [dt, field, op, value])
	monkeypatch.setattr(s, "get_workflow_name", lambda dt: "Active Purchase Workflow" if workflow_field else "", raising=False)
	monkeypatch.setattr(s, "get_workflow", lambda dt: SimpleNamespace(workflow_state_field=workflow_field), raising=False)
	return s


@pytest.mark.parametrize(("readable", "source_date"), [(True, "2026-03-05"), (False, "2026-03-05"), (True, None)])
def test_cached_original_source_date_obeys_apply_date_projection_permission(monkeypatch, readable, source_date):
	request = oa(apply_date="2026-10-12", custom_purchase_source_json=json.dumps({"apply_date": source_date, "private": "PRIVATE-SOURCE"}))
	fields = set(request) - ({"apply_date"} if not readable else set())
	boundary = ReadBoundary([], [request], fields={"Purchase Order": {"name"}, "OA Purchase Request": fields})
	s = connect(monkeypatch, boundary)
	row = s.get_unified_purchase_list(order_by="name desc")["rows"][0]
	assert row["transaction_date"] == (source_date if readable else None)
	assert "PRIVATE-SOURCE" not in str(row)


@pytest.mark.parametrize("field", ["creation", "modified"])
def test_native_timestamp_sort_uses_each_records_own_timestamp_and_export_order(monkeypatch, capture_xlsx, field):
	orders = [po("PO-1", **{field: datetime(2026, 10, 1, 8)}), po("PO-2", **{field: datetime(2026, 10, 3, 8)})]
	request = oa(**{field: datetime(2026, 10, 2, 8)}, apply_date="2026-09-01")
	b = ReadBoundary(orders, [request])
	s = connect_native_queries(monkeypatch, b)
	result = s.get_unified_purchase_list(order_by=f"{field} desc", page_length=1)
	assert result["total_count"] == 3 and result["has_next"] is True
	assert result["rows"][0]["name"] == "PO-2"
	assert result["rows"][0][field] == orders[1][field]
	assert [row["name"] for row in s.get_unified_purchase_list(order_by=f"{field} desc")["rows"]] == ["PO-2", "OA-1", "PO-1"]
	s.export_unified_purchase_list(order_by=f"{field} desc", columns=["name"])
	content = b.response["filecontent"]
	assert content.index(b"PO-2") < content.index("待完善 · 审批-1".encode()) < content.index(b"PO-1")


def test_active_native_workflow_state_is_projected_and_sortable_without_exposing_other_fields(monkeypatch):
	orders = [po("PO-1", custom_workflow_state="Submitted", private_notes="private"), po("PO-2", custom_workflow_state="Approved")]
	b = ReadBoundary(orders, [oa()])
	s = connect_native_queries(monkeypatch, b, workflow_field="custom_workflow_state")
	result = s.get_unified_purchase_list()
	assert next(row for row in result["rows"] if row["name"] == "PO-1")["custom_workflow_state"] == "Submitted"
	assert all("private_notes" not in row for row in result["rows"])
	result = s.get_unified_purchase_list(order_by="custom_workflow_state asc")
	assert [row["name"] for row in result["rows"]] == ["PO-2", "PO-1", "OA-1"]
	assert result["rows"][-1].get("custom_workflow_state") is None


def test_permitted_native_scalar_sort_is_projected_and_numeric(monkeypatch):
	orders = [po("PO-1", total_qty=20), po("PO-2", total_qty=3)]
	b = ReadBoundary(orders, [oa()])
	s = connect_native_queries(monkeypatch, b, fieldtypes={"total_qty": "Float"})
	result = s.get_unified_purchase_list(order_by="total_qty asc")
	assert [row["name"] for row in result["rows"]] == ["PO-2", "PO-1", "OA-1"]
	assert result["rows"][0]["total_qty"] == 3


@pytest.mark.parametrize("field", ["creation", "modified", "custom_workflow_state", "total_qty"])
def test_denied_native_sort_field_is_not_inferred_and_fails_closed(monkeypatch, field):
	order = po(**{field: "private"})
	b = ReadBoundary([order], fields={"Purchase Order": set(order) - {field}, "OA Purchase Request": {"name"}})
	s = connect_native_queries(monkeypatch, b, workflow_field="custom_workflow_state")
	assert field not in s.get_unified_purchase_list()["rows"][0]
	with pytest.raises(PermissionError):
		s.get_unified_purchase_list(order_by=f"{field} asc")


@pytest.mark.parametrize("argument", ["native_filters", "native_or_filters"])
def test_native_item_filters_keep_list_count_totals_and_export_identical(monkeypatch, capture_xlsx, argument):
	order = po(custom_oa_purchase_expense="OA-1", grand_total=12)
	b = ReadBoundary([order, dict(order)], [oa(), oa("OA-UNCONVERTED", detail_total_amount=999)])
	b.fields["Purchase Order"].add("items")
	b.fields["Purchase Order Item"] = {"name", "item_code", "qty"}
	s = connect_native_queries(monkeypatch, b)
	conditions = [["Purchase Order Item", "item_code", "=", "ITEM-1"], ["Purchase Order", "company", "=", "Yuewei"]]
	args = {argument: json.dumps(conditions)}
	result = s.get_unified_purchase_list(**args, page_length=1)
	assert result["total_count"] == 1 and result["has_next"] is False
	assert result["totals"] == {"orders": [{"currency": "USD", "amount": 12.0}], "oa": [{"currency": "CNY", "amount": 10.0}], "oa_unknown_currency_count": 0}
	assert result["rows"][0]["oa_name"] == "OA-1"
	s.export_unified_purchase_list(**args, columns=["name", "grand_total", "oa_amount"])
	assert b"PO-1" in b.response["filecontent"] and b"OA-UNCONVERTED" not in b.response["filecontent"]
	po_calls = [kwargs for dt, kwargs in b.calls if dt == "Purchase Order"]
	assert len(po_calls) == 2 and po_calls[0] == po_calls[1]
	assert po_calls[0][argument.removeprefix("native_")] == conditions
	assert ("Purchase Order Item", {"parenttype": "Purchase Order", "permission_type": "read"}) in b.field_calls


@pytest.mark.parametrize("denied", ["items", "qty"])
def test_native_item_filter_requires_parent_table_and_child_field_permissions(monkeypatch, denied):
	b = ReadBoundary([po()])
	b.fields["Purchase Order"].add("items")
	b.fields["Purchase Order Item"] = {"name", "item_code", "qty"}
	b.fields["Purchase Order" if denied == "items" else "Purchase Order Item"].discard(denied)
	s = connect_native_queries(monkeypatch, b)
	with pytest.raises(PermissionError):
		s.get_unified_purchase_list(native_filters=[["Purchase Order Item", "qty", ">", 1]])
	assert not b.calls


@pytest.mark.parametrize("doctype, field", [("Payment Entry", "paid_amount"), ("Purchase Invoice Item", "item_code"), ("Purchase Order Item", "not_a_field")])
def test_native_item_filter_rejects_foreign_or_unknown_fields_before_query(monkeypatch, doctype, field):
	b = ReadBoundary([po()])
	b.fields["Purchase Order"].add("items")
	b.fields["Purchase Order Item"] = {"name", "item_code", "qty"}
	s = connect_native_queries(monkeypatch, b)
	with pytest.raises(ValueError):
		s.get_unified_purchase_list(native_filters=[[doctype, field, "=", "private"]])
	assert not b.calls


@pytest.mark.parametrize("empty", ["[]", "{}", "null"])
def test_serialized_empty_native_filters_do_not_hide_unconverted_sources(monkeypatch, capture_xlsx, empty):
	b = ReadBoundary([po()], [oa()])
	s = connect_native_queries(monkeypatch, b)
	args = {"native_filters": empty, "native_or_filters": empty}
	result = s.get_unified_purchase_list(**args)
	assert result["total_count"] == 2
	assert {row["name"] for row in result["rows"]} == {"PO-1", "OA-1"}
	assert all("filters" not in kwargs for dt, kwargs in b.calls)
	s.export_unified_purchase_list(**args, columns=["name"])
	assert "待完善 · 审批-1".encode() in b.response["filecontent"]


@pytest.mark.parametrize("invalid", [0, False, "0", "false", '""'])
def test_malformed_native_filter_shape_is_not_silently_treated_as_no_filter(monkeypatch, invalid):
	b = ReadBoundary([po()], [oa()])
	s = connect_native_queries(monkeypatch, b)
	with pytest.raises(ValueError):
		s.get_unified_purchase_list(native_filters=invalid)
	assert not b.calls


def test_native_sort_rejects_unknown_table_and_sql_fragments_before_query(monkeypatch):
	b = ReadBoundary([po()])
	s = connect_native_queries(monkeypatch, b)
	for invalid in ("items asc", "not_a_field asc", "total_qty desc; select 1", "`tabPayment Entry`.`paid_amount` asc"):
		with pytest.raises(ValueError):
			s.get_unified_purchase_list(order_by=invalid)
	assert not b.calls


@pytest.fixture
def capture_xlsx(monkeypatch):
	class WorkbookBoundary:
		def __init__(self, output, options):
			self.output = output
			assert options["strings_to_formulas"] is False
			assert options["strings_to_urls"] is False

		def __enter__(self):
			return self

		def get_worksheet_by_name(self, name):
			return SimpleNamespace(set_column=lambda *args: None)

		def add_format(self, values):
			assert values == {"num_format":"0.00"}
			return values

		def __exit__(self, *_args):
			if hasattr(self, "data"):
				self.output.write(repr(self.data).encode())

	module = SimpleNamespace(make_xlsx=lambda data, sheet_name, wb: setattr(wb, "data", (data, sheet_name)))
	monkeypatch.setitem(__import__("sys").modules, "frappe.utils.xlsxutils", module)
	monkeypatch.setitem(__import__("sys").modules, "xlsxwriter", SimpleNamespace(Workbook=WorkbookBoundary))


def test_api_missing_oa_app_keeps_orders_and_reports_capability(monkeypatch):
	b = ReadBoundary([po()], oa_installed=False)
	result = connect(monkeypatch, b).get_unified_purchase_list()
	assert result["total_count"] == 1
	assert result["capabilities"] == {"purchase_order": True, "oa_request": False}
	assert result["warnings"]
	assert [call[0] for call in b.calls] == ["Purchase Order"]


def test_api_uses_permission_aware_unbounded_queries_and_sanitizes_fields(monkeypatch):
	b = ReadBoundary([po(custom_oa_purchase_expense="OA-1")], [oa()], fields={
		"Purchase Order": {"name", "transaction_date", "custom_oa_purchase_expense"},
		"OA Purchase Request": {"name", "oa_code", "purchase_order", "apply_date"},
	})
	result = connect(monkeypatch, b).get_unified_purchase_list(filters='{"search":"审批-1"}')
	assert result["total_count"] == 1
	assert result["rows"][0]["grand_total"] is None
	assert result["rows"][0]["oa_amount"] is None
	assert result["rows"][0]["company"] is None
	assert "custom_oa_purchase_expense" not in result["rows"][0]
	assert len(b.calls) == 2
	assert all(call[1]["limit_page_length"] == 0 for call in b.calls)
	assert all("filters" not in call[1] and "ignore_permissions" not in call[1] for call in b.calls)
	assert all(call[1] == {"permission_type": "read", "ignore_virtual": True} for call in b.field_calls)


def test_api_oa_read_denied_exposes_source_without_oa_links_or_metadata(monkeypatch):
	b = ReadBoundary([po(custom_oa_purchase_expense="SECRET-OA")], [oa("SECRET-OA")], read={"Purchase Order"})
	result = connect(monkeypatch, b).get_unified_purchase_list()
	assert result["rows"][0]["source"] == "OA"
	assert result["rows"][0]["oa_name"] is None
	assert "SECRET-OA" not in str(result)
	assert [call[0] for call in b.calls] == ["Purchase Order"]


@pytest.mark.parametrize("visibility", ["hidden_field", "empty_hidden_field", "no_oa_read"])
def test_api_blank_forward_link_does_not_claim_non_oa_when_reverse_link_is_unreadable(monkeypatch, visibility):
	order = po(custom_oa_purchase_expense=None)
	requests = [oa(purchase_order="PO-1")] if visibility == "hidden_field" else []
	b = ReadBoundary([order], requests, read={"Purchase Order"} if visibility == "no_oa_read" else None,
		fields={"Purchase Order": set(order), "OA Purchase Request": {"name", "oa_code"}})
	s = connect(monkeypatch, b)
	result = s.get_unified_purchase_list()
	row = next(row for row in result["rows"] if row["row_type"] == "purchase_order")
	assert row["source"] is None
	assert s.get_unified_purchase_list(filters={"source": "non_oa"})["total_count"] == 0


def test_api_empty_reverse_query_uses_permitted_field_evidence(monkeypatch):
	order = po(custom_oa_purchase_expense=None)
	b = ReadBoundary([order], [], fields={
		"Purchase Order": set(order), "OA Purchase Request": {"name", "purchase_order"},
	})
	result = connect(monkeypatch, b).get_unified_purchase_list(filters={"source": "non_oa"})
	assert result["total_count"] == 1
	assert result["rows"][0]["source"] == "未关联 OA"


def test_hidden_oa_reverse_link_does_not_infer_order_was_not_generated(monkeypatch):
	order, request = po(), oa(purchase_order="SECRET-PO")
	b = ReadBoundary([order], [request], fields={
		"Purchase Order": set(order), "OA Purchase Request": set(request) - {"purchase_order"},
	})
	result = connect(monkeypatch, b).get_unified_purchase_list()
	row = next(row for row in result["rows"] if row["row_type"] == "oa_request")
	assert row["status"] == "订单关联不可见"
	assert "订单关联不可见" in row["oa_warning"]
	assert "SECRET-PO" not in str(result)


def test_no_readable_doctype_is_explicit_permission_error(monkeypatch):
	b = ReadBoundary(read=set())
	with pytest.raises(PermissionError):
		connect(monkeypatch, b).get_unified_purchase_list()


@pytest.mark.parametrize("export_permissions", [{"Purchase Order"}, {"OA Purchase Request"}, set()])
def test_export_requires_permissions_for_both_linked_metadata_and_order(monkeypatch, capture_xlsx, export_permissions):
	b = ReadBoundary([po(custom_oa_purchase_expense="OA-1")], [oa()], native_export=export_permissions)
	with pytest.raises(PermissionError):
		connect(monkeypatch, b).export_unified_purchase_list()
	assert b.response == {}


@pytest.mark.parametrize("sources", ["order", "oa", "linked"])
def test_export_uses_native_capability_when_generic_export_is_false(monkeypatch, capture_xlsx, sources):
	orders = [po(custom_oa_purchase_expense="OA-1" if sources == "linked" else None)] if sources != "oa" else []
	requests = [oa()] if sources != "order" else []
	b = ReadBoundary(orders, requests, export=set(), native_export={"Purchase Order", "OA Purchase Request"},
		fields={"Purchase Order": set(po()), "OA Purchase Request": set(oa())})
	s = connect(monkeypatch, b)
	s.export_unified_purchase_list(columns=["name", "oa_references"])
	assert b.response["type"] == "binary"
	assert (b"OA-1" in b.response["filecontent"]) is (sources != "order")


@pytest.mark.parametrize("sources", ["order", "oa", "linked"])
def test_owner_only_export_allows_exact_user_for_every_selected_source(monkeypatch, capture_xlsx, sources):
	orders = [po(custom_oa_purchase_expense="OA-1" if sources == "linked" else None)] if sources != "oa" else []
	requests = [oa(owner="buyer@example.test")] if sources != "order" else []
	b = ReadBoundary(orders, requests, native_export=set(), owner_export={"Purchase Order", "OA Purchase Request"},
		fields={"Purchase Order": set(po()), "OA Purchase Request": set(oa())})
	connect(monkeypatch, b).export_unified_purchase_list(columns=["name"])
	assert b.response["type"] == "binary"


@pytest.mark.parametrize("doctype", ["Purchase Order", "OA Purchase Request"])
@pytest.mark.parametrize("owner", ["other@example.test", None])
def test_owner_only_export_rejects_other_or_missing_owner(monkeypatch, capture_xlsx, doctype, owner):
	orders, requests = ([po(owner=owner)], []) if doctype == "Purchase Order" else ([], [oa(owner=owner)])
	b = ReadBoundary(orders, requests, native_export=set(), owner_export={doctype},
		fields={"Purchase Order": set(po()), "OA Purchase Request": set(oa())})
	with pytest.raises(PermissionError):
		connect(monkeypatch, b).export_unified_purchase_list(columns=["name"])
	assert b.response == {}


@pytest.mark.parametrize("association", ["single", "multiple", "conflict"])
@pytest.mark.parametrize("owner", ["other@example.test", None])
def test_linked_oa_owner_boundary_covers_all_sources(monkeypatch, capture_xlsx, association, owner):
	orders = [po(custom_oa_purchase_expense="OA-1")]
	requests = [oa(owner=owner)]
	if association == "multiple":
		requests = [oa(owner="buyer@example.test"), oa("OA-2", purchase_order="PO-1", owner=owner)]
	elif association == "conflict":
		orders.append(po("PO-2"))
		requests[0]["purchase_order"] = "PO-2"
	b = ReadBoundary(orders, requests, native_export={"Purchase Order"}, owner_export={"OA Purchase Request"})
	with pytest.raises(PermissionError):
		connect(monkeypatch, b).export_unified_purchase_list(filters={"scope": "orders"}, columns=["name", "oa_references"])
	assert b.response == {}


def test_owner_only_export_checks_selected_rows_without_filtering_mixed_owners(monkeypatch, capture_xlsx):
	b = ReadBoundary([po(), po("PO-2", owner="other@example.test", company="Other")], oa_installed=False,
		native_export=set(), owner_export={"Purchase Order"})
	s = connect(monkeypatch, b)
	s.export_unified_purchase_list(filters={"company": "Yuewei"}, columns=["name"])
	assert b"PO-1" in b.response["filecontent"] and b"PO-2" not in b.response["filecontent"]
	b.response.clear()
	with pytest.raises(PermissionError):
		s.export_unified_purchase_list(columns=["name"])
	assert b.response == {}


@pytest.mark.parametrize("doctype", ["Purchase Order", "OA Purchase Request"])
def test_owner_authority_read_never_restores_denied_display_fields(monkeypatch, capture_xlsx, doctype):
	order, request = po(grand_total=99), oa(owner="buyer@example.test", detail_total_amount=99)
	orders, requests = ([order], []) if doctype == "Purchase Order" else ([], [request])
	b = ReadBoundary(orders, requests, native_export=set(), owner_export={doctype}, fields={
		"Purchase Order": set(order) - {"owner", "grand_total"},
		"OA Purchase Request": set(request) - {"owner", "detail_total_amount"},
	})
	s = connect(monkeypatch, b)
	s.export_unified_purchase_list(columns=["name", "owner", "grand_total", "oa_amount"])
	assert b.document_reads == [(doctype, "PO-1" if orders else "OA-1", "read")]
	assert b"buyer@example.test" not in b.response["filecontent"] and b"99" not in b.response["filecontent"]
	assert s.get_unified_purchase_list()["rows"][0]["owner"] is None


def test_hidden_owner_authority_still_requires_document_read(monkeypatch, capture_xlsx):
	order = po()
	b = ReadBoundary([order], oa_installed=False, native_export=set(), owner_export={"Purchase Order"},
		fields={"Purchase Order": set(order) - {"owner"}}, document_denied={("Purchase Order", "PO-1")})
	with pytest.raises(PermissionError):
		connect(monkeypatch, b).export_unified_purchase_list(columns=["name"])
	assert b.response == {}


@pytest.mark.parametrize("scope, denied_source", [
	("orders", "Purchase Order"), ("oa", "OA Purchase Request"), ("all", "Purchase Order"),
])
def test_empty_export_still_requires_requested_native_authority(monkeypatch, capture_xlsx, scope, denied_source):
	b = ReadBoundary(export={"Purchase Order", "OA Purchase Request"},
		native_export={"Purchase Order", "OA Purchase Request"} - {denied_source},
		fields={"Purchase Order": {"name"}, "OA Purchase Request": {"name"}})
	with pytest.raises(PermissionError):
		connect(monkeypatch, b).export_unified_purchase_list(filters={"scope": scope}, columns=["name"])
	assert b.response == {}


def test_export_builder_uses_complete_same_filtered_result_and_selected_columns():
	s = backend()
	rows = [po("PO-2", grand_total=2), po("PO-1", grand_total=1), po("PO-3", company="Other")]
	result = s.build_unified_purchase_payload(rows, [], filters={"company": "Yuewei"}, page_length=1)
	data = s.build_unified_purchase_export(
		rows, [], filters={"company": "Yuewei"}, columns=["name", "grand_total", "party_account_currency"],
	)
	assert result["total_count"] == 2
	assert data == [["采购订单号 / 待完善来源", "订单金额", "预付款币种"], ["PO-2", 2, "CNY"], ["PO-1", 1, "CNY"]]
	with pytest.raises(ValueError):
		s.build_unified_purchase_export(rows, [], columns=["process_instance_id"])


def test_export_builder_preserves_source_text_and_numeric_precision():
	s = backend()
	order = po(supplier_name="=1+1", project="https://example.test/source", grand_total=42.123456,
		custom_oa_purchase_expense="OA-1")
	request = oa(oa_code='=HYPERLINK("https://example.test","OA")', detail_total_amount=-7.890123)
	data = s.build_unified_purchase_export([order], [request],
		columns=["supplier_name", "oa_number", "grand_total", "oa_amount", "project"])
	assert data[1] == [order["supplier_name"], request["oa_code"], 42.123456, -7.890123, order["project"]]
	assert s.build_unified_purchase_payload([order], [request])["rows"][0]["supplier_name"] == "=1+1"


def test_export_structured_oa_references_keep_original_evidence_as_text():
	s = backend()
	order = po(custom_oa_purchase_expense="OA-1")
	request = oa(oa_code="=1+1", detail_total_amount=-7.890123)
	data = s.build_unified_purchase_export([order], [request], columns=["name", "oa_references"])
	assert data[0] == ["采购订单号 / 待完善来源", "OA 来源明细"]
	assert isinstance(data[1][1], str)
	assert json.loads(data[1][1]) == [{
		"name": "OA-1", "number": "=1+1", "approval_status": "APPROVED", "amount": -7.890123,
		"currency": "CNY", "amount_basis": "采购明细合计", "company": "Yuewei", "warning": None,
	}]
	assert "采购明细合计" in data[1][1]


def test_export_source_display_keeps_cache_identity_and_exact_evidence_immutable():
	s = backend()
	request = oa("DT-PUR-internal-identity", oa_code="DT-ORIGINAL",
		custom_purchase_source_json=json.dumps({"business_id": "DT-ORIGINAL", "currency": "CNY",
			"detail_total_amount": "0.00000", "requested_amount": "100.123456789"}))
	rows = s.build_unified_purchase_payload([po()], [request])["rows"]
	original = json.dumps(rows, ensure_ascii=False, default=str)
	columns = ["name", "source", "oa_amount", "requested_amount", "oa_currency", "oa_references"]
	data = s._export_data(rows, columns)
	assert data[2][:5] == ["待完善 · DT-ORIGINAL", "钉钉", 0.0, 100.123456789, "CNY"]
	assert data[1][:2] == ["PO-1", "其他来源"]
	assert data[0] == ["采购订单号 / 待完善来源", "来源", "来源明细金额", "来源申请金额", "OA 币种", "OA 来源明细"]
	assert isinstance(data[2][2], float) and isinstance(data[2][3], float)
	assert json.loads(data[2][5]) == rows[1]["oa_references"]
	assert json.loads(data[2][5])[0]["amount"] == "0.00000"
	assert json.dumps(rows, ensure_ascii=False, default=str) == original
	assert rows[1]["name"] == "DT-PUR-internal-identity" and rows[1]["source"] == "OA"
	assert s.build_unified_purchase_export([po()], [request], columns=columns) == data


@pytest.mark.parametrize("source, label", [
	("OA", "钉钉"), ("oa", "钉钉"), ("non_oa", "其他来源"), ("未关联 OA", "其他来源"),
	(None, "来源待确认"), ("自有来源", "自有来源"),
])
def test_export_source_labels_match_visible_list_without_changing_codes(source, label):
	row = {"name": "PO-1", "row_type": "purchase_order", "source": source}
	assert backend()._export_data([row], ["name", "source"])[1] == ["PO-1", label]
	assert row["source"] == source


def test_export_xlsx_preserves_literal_formula_and_url_strings(monkeypatch):
	# Exercise the real installed Frappe XLSX implementation; pure environments skip only this integration.
	xlsxutils = pytest.importorskip("frappe.utils.xlsxutils")
	openpyxl = pytest.importorskip("openpyxl")
	monkeypatch.setattr(xlsxutils.XLSXStyleBuilder, "get_datetime_format", lambda: "yyyy-mm-dd hh:mm:ss")
	order = po(supplier_name='=IF(1<2,"<b>literal</b>","")', project="<b>https://example.test/source</b>", grand_total=42.123456,
		custom_oa_purchase_expense="OA-1")
	request = oa(oa_code='=HYPERLINK("https://example.test","OA")', detail_total_amount=-7.890123)
	b = ReadBoundary([order], [request])
	s = connect(monkeypatch, b)
	s.export_unified_purchase_list(columns=["supplier_name", "oa_number", "grand_total", "oa_amount", "project", "oa_references"])
	with ZipFile(BytesIO(b.response["filecontent"])) as archive:
		root = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
		ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
		assert root.findall(".//x:f", ns) == []
		assert root.findall(".//x:hyperlink", ns) == []
	workbook = openpyxl.load_workbook(BytesIO(b.response["filecontent"]), data_only=False)
	sheet = workbook.active
	assert [sheet.cell(2, index).value for index in range(1, 6)] == [
		order["supplier_name"], request["oa_code"], 42.123456, -7.890123, order["project"],
	]
	assert [sheet.cell(2, index).data_type for index in range(1, 6)] == ["s", "s", "n", "n", "s"]
	assert sheet.cell(2, 3).number_format == sheet.cell(2, 4).number_format == "0.00"
	assert sheet.cell(2, 6).data_type == "s"
	assert json.loads(sheet.cell(2, 6).value)[0]["amount"] == -7.890123
	assert json.loads(sheet.cell(2, 6).value)[0]["number"] == request["oa_code"]


def test_export_download_calls_frappe_xlsx_without_pagination_or_business_writes(monkeypatch, capture_xlsx):
	b = ReadBoundary([po("PO-2"), po("PO-1")], [], oa_installed=False)
	s = connect(monkeypatch, b)
	s.export_unified_purchase_list(columns='["name"]')
	assert b.response["filename"].endswith(".xlsx")
	assert b.response["type"] == "binary"
	assert b"PO-1" in b.response["filecontent"] and b"PO-2" in b.response["filecontent"]


def test_private_projection_does_not_reveal_denied_approval_or_financial_fields(monkeypatch):
	request=oa(custom_purchase_source_id="source",custom_purchase_source_json=json.dumps({"eligible":True,"version":"v1","business_id":"DT-ORIGINAL","requested_amount":"999","detail_total_amount":"998","currency":"CNY","issues":["采购明细与申请金额不一致，请核对原单"]}),custom_cashier_payment_evidence=json.dumps({"paid_amount":"8","currency":"CNY","payment_evidence_status":"recorded"}))
	b=ReadBoundary([], [request], read={"Purchase Order","OA Purchase Request","Payment Entry"}, fields={"Purchase Order":{"name"},"OA Purchase Request":set(request)-{"approval_status","payment_amount","detail_total_amount","custom_purchase_source_json","custom_cashier_payment_evidence"},"Payment Entry":{"name"}})
	b.db.get_value=lambda *args,**kwargs: request
	row=connect(monkeypatch,b).get_unified_purchase_list()["rows"][0]
	assert row["approval_status"] is None and row["source_eligible"] is None
	assert row["requested_amount"] is None and row["cashier_paid_amount"] is None
	assert "不一致" not in (row["oa_warning"] or "")


@pytest.mark.parametrize("pending", ["加工服务采购单位待确认", "采购明细与申请金额不一致，请核对原单"])
def test_unbound_managed_list_uses_current_pending_reason_and_preserves_real_source_warnings(monkeypatch, pending):
	source = json.dumps({"version": "unchanged-source-version", "currency": "CNY", "detail_total_amount": "0",
		"issues": ["采购明细待完善", "采购明细与申请金额不一致，请核对原单", "原单交付日期需要核对"]})
	request = oa(items_json="[]", custom_purchase_source_id="source", custom_purchase_source_json=source,
		custom_purchase_pending_reason=pending)
	original = json.dumps(request, default=str, ensure_ascii=False)
	b = ReadBoundary([], [request], fields={"Purchase Order": {"name"}, "OA Purchase Request": set(request)})
	row = connect(monkeypatch, b).get_unified_purchase_list()["rows"][0]
	assert row["oa_warning"] == "原单交付日期需要核对；" + pending
	assert row["oa_references"][0]["warning"] == row["oa_warning"]
	assert row["source_version"] == "unchanged-source-version" and row["oa_amount"] == "0"
	assert json.dumps(request, default=str, ensure_ascii=False) == original
	assert len(b.private_reads) == 1


@pytest.mark.parametrize("link_direction", ["oa", "order"])
@pytest.mark.parametrize("pending", ["", "采购明细与申请金额不一致，请核对原单"])
def test_linked_adoption_only_removes_recomputed_generic_hints_and_never_projects_private_plan(monkeypatch, link_direction, pending):
	source = json.dumps({"version": "source-v1", "currency": "CNY", "detail_total_amount": "0",
		"issues": ["采购明细待完善", "采购明细与申请金额不一致，请核对原单", "原始附件需要核对"]})
	adoption = json.dumps({"source_version": "source-v1", "plan": {"items": [{"processor_phone": "PRIVATE-PHONE", "description": "PRIVATE-ADOPTION-DETAIL"}]}})
	request = oa(items_json="[]", purchase_order="PO-1" if link_direction == "oa" else None, custom_purchase_source_id="source", custom_purchase_source_json=source,
		custom_purchase_adaptation_json=adoption, custom_purchase_pending_reason=pending)
	b = ReadBoundary([po(custom_oa_purchase_expense="OA-1")], [request],
		fields={"Purchase Order": set(po()), "OA Purchase Request": set(request) - {"custom_purchase_adaptation_json"}})
	s = connect(monkeypatch, b)
	parsed = []; original_loads = json.loads
	def loads(value, *args, **kwargs):
		parsed.append(value); return original_loads(value, *args, **kwargs)
	monkeypatch.setattr(s.json, "loads", loads)
	result = s.get_unified_purchase_list(); row = result["rows"][0]
	assert row["oa_warning"] == "原始附件需要核对" + ("；" + pending if pending else "")
	assert row["source_version"] == "source-v1" and row["oa_amount"] == "0"
	assert request["custom_purchase_source_json"] == source and request["custom_purchase_adaptation_json"] == adoption
	assert "PRIVATE-" not in str(result) and "custom_purchase_adaptation_json" not in str(result) and "_adaptation_adopted" not in str(result)
	assert parsed.count(adoption) == 1 and len(b.private_reads) == 1
	assert "custom_purchase_adaptation_json" in b.private_reads[0][2]
	assert all("custom_purchase_adaptation_json" not in args["fields"] for _, args in b.calls)


@pytest.mark.parametrize("linked,adoption", [
	(False, '{"source_version":"v1","plan":{"items":[{}]}}'), (True, None), (True, "{}"), (True, '{"plan":null}'), (True, "invalid-json"),
])
def test_adoption_without_confirmed_link_and_legacy_or_invalid_adoption_do_not_hide_source_problems(monkeypatch, linked, adoption):
	request = oa(items_json="[]", purchase_order="PO-1" if linked else None, custom_purchase_source_id="source",
		custom_purchase_source_json=json.dumps({"version": "v1", "currency": "CNY", "issues": ["采购明细待完善"]}),
		custom_purchase_adaptation_json=adoption, custom_purchase_pending_reason="")
	orders = [po(custom_oa_purchase_expense="OA-1")] if linked else []
	b = ReadBoundary(orders, [request], fields={"Purchase Order": set(po()), "OA Purchase Request": set(request)})
	row = connect(monkeypatch, b).get_unified_purchase_list()["rows"][0]
	assert "采购明细待完善" in row["oa_warning"]


@pytest.mark.parametrize("version,adoption", [
	("v2", {"source_version": "v1", "plan": {"items": [{}]}}),
	("v2", {"plan": {"version": "v2", "items": [{}]}}),
	(None, {"plan": {"items": [{}]}}),
])
def test_stale_or_unversioned_linked_adoption_keeps_current_source_review_reasons(monkeypatch, version, adoption):
	source = json.dumps({"version": version, "currency": "CNY", "issues": ["采购明细待完善", "采购明细与申请金额不一致，请核对原单"]})
	request = oa(items_json="[]", purchase_order="PO-1", custom_purchase_source_id="source", custom_purchase_source_json=source,
		custom_purchase_adaptation_json=json.dumps(adoption), custom_purchase_pending_reason="原单已变更，请重新核对")
	b = ReadBoundary([po(custom_oa_purchase_expense="OA-1")], [request])
	row = connect(monkeypatch, b).get_unified_purchase_list()["rows"][0]
	assert row["oa_warning"] == "采购明细待完善；采购明细与申请金额不一致，请核对原单；原单已变更，请重新核对"
	assert row["source_version"] == version and request["custom_purchase_source_json"] == source


def test_denied_current_pending_reason_does_not_replace_visible_source_warnings_or_leak_adoption(monkeypatch):
	request = oa(items_json="[]", custom_purchase_source_id="source", custom_purchase_pending_reason="PRIVATE-PENDING",
		custom_purchase_source_json=json.dumps({"version": "v1", "currency": "CNY", "issues": ["采购明细待完善"]}),
		custom_purchase_adaptation_json='{"plan":{"items":[{"description":"PRIVATE-PLAN"}]}}')
	b = ReadBoundary([], [request], fields={"Purchase Order": {"name"}, "OA Purchase Request": set(request) - {"custom_purchase_pending_reason", "custom_purchase_adaptation_json"}})
	result = connect(monkeypatch, b).get_unified_purchase_list()
	assert "采购明细待完善" in result["rows"][0]["oa_warning"]
	assert "PRIVATE-" not in str(result)


def test_source_original_number_is_preserved_independently_of_native_field_autoname(monkeypatch):
	request=oa(oa_code="DT-PUR-internal-identity",custom_purchase_source_id="source",custom_purchase_source_json=json.dumps({"business_id":"DT-ORIGINAL","eligible":True,"currency":"CNY","detail_total_amount":"0","requested_amount":"0"}))
	b=ReadBoundary([], [request],fields={"Purchase Order":{"name"},"OA Purchase Request":set(request)}); b.db.get_value=lambda *args,**kwargs: request
	row=connect(monkeypatch,b).get_unified_purchase_list()["rows"][0]
	assert row["oa_number"] == "DT-ORIGINAL"


def progress_row(*, unpaid=0, internal=(), pending=(), review=False):
	phases = (["supplier_unpaid"] if unpaid > 0 else []) + (
		["internal_unsettled"] if any(row.get("state") == "payable" and row.get("payable_outstanding", 0) > 0 for row in internal) else []) + (
		["factory_pending"] if any(row.get("pending_stock_qty", 0) > 0 for row in pending) else [])
	return {"external": {"state": "exact", "settled": 0, "order_unpaid": unpaid, "currency": "USD"},
		"internal": list(internal), "factory_receipt": {"state": "exact", "quantities": list(pending)},
		"domestic_receipt": {"state": "none", "quantities": [], "warnings": []},
		"receipt_logistics": [], "review_required": review, "progress_phases": phases}


@pytest.mark.parametrize("external, label", [
	({"state": "restricted"}, "付款进度不可见"),
	({"state": "not_applicable"}, "外部付款不适用"),
	({"state": "review", "settled": 2, "order_unpaid": 3}, "付款口径待核对"),
	({"state": "exact", "settled": None, "order_unpaid": 3}, "付款口径待核对"),
	({"state": "exact", "settled": 2, "order_unpaid": 0}, "已付清"),
	({"state": "exact", "settled": 2, "order_unpaid": 3}, "部分付款"),
	({"state": "exact", "settled": 0, "order_unpaid": 3}, "未付款"),
])
def test_compact_payment_progress_export_preserves_visible_labels_without_inference(external, label):
	row = {"name": "PO", "order_progress": {"external": external}}
	assert backend()._export_data([row], ["payment_progress"])[1] == [label]
	assert row["order_progress"]["external"] == external


@pytest.mark.parametrize("received, label", [(None, "收货进度不可见"), (0, "未收货"), (50, "部分收货"), (100, "已收齐")])
def test_compact_receipt_progress_export_uses_native_percentage_not_domestic_evidence_state(received, label):
	row = {"name": "PO", "per_received": received, "order_progress": {"domestic_receipt": {"state": "native_received"}}}
	assert backend()._export_data([row], ["receipt_progress"])[1] == [label]


@pytest.mark.parametrize("phase", ["supplier_unpaid", "internal_unsettled", "factory_pending"])
def test_computed_phase_and_beneficiary_filter_before_page100_count_and_totals(phase):
	orders = []
	for index in range(300):
		matched = index % 2 == 0
		progress = progress_row(unpaid=2 if matched else 0,
			internal=[{"state": "payable" if matched else "draft_quote", "payable_outstanding": 3,
				"currency": "MXN"}], pending=[{"pending_stock_qty": 4 if matched else 0, "stock_uom": "Nos"}])
		orders.append(po(f"PO-{index:04}", grand_total=1, order_progress=progress,
			_role_context={"beneficiary_companies": ["FACTORY"], "company_confirmed": True}))
	result = backend().build_unified_purchase_payload(orders, [], start=100, page_length=100,
		filters={"beneficiary_company": "FACTORY", "progress_phase": phase})
	assert result["total_count"] == 150
	assert len(result["rows"]) == 50
	assert result["rows"][0]["name"] == "PO-0098"
	assert result["totals"]["orders"] == [{"currency": "USD", "amount": 150}]


def test_review_only_excludes_exact_rows_and_uses_all_authorized_rows_before_page():
	orders = [po(f"PO-{index:04}", grand_total=1, order_progress=progress_row(review=index % 2 == 0))
		for index in range(300)]
	result = backend().build_unified_purchase_payload(orders, [], start=100, filters={"review_only": True})
	assert result["total_count"] == 150
	assert len(result["rows"]) == 50
	assert result["totals"]["orders"] == [{"currency": "USD", "amount": 150}]


@pytest.mark.parametrize("filters", [{"progress_phase": "secret"}, {"review_only": "yes"}, {"review_only": 2}])
def test_new_computed_filters_reject_unknown_phase_and_non_boolean_review(filters):
	with pytest.raises(ValueError):
		backend().build_unified_purchase_payload([], [], filters=filters)


def test_managed_unconfirmed_source_hint_is_readable_but_not_buyer_or_confirmed_beneficiary():
	source = {"business_id": "DT-ORIGINAL", "beneficiary_company": "Raw Factory", "beneficiary_company_status": "ambiguous",
		"project": "Raw molds", "project_status": "unique", "currency": "CNY", "detail_total_amount": "1"}
	request = oa(target_company="Old applicant hint", custom_purchase_source_id="source",
		custom_purchase_source_json=json.dumps(source))
	s = backend()
	row = s.build_unified_purchase_payload([po(custom_oa_purchase_expense="OA-1")], [request])["rows"][0]
	assert row.get("role_context", {}).get("purchasing_company") == "Yuewei"
	assert "公司不一致" not in (row["oa_warning"] or "")
	assert s.build_unified_purchase_payload([], [request], filters={"company": "Old applicant hint"})["total_count"] == 0
	assert s.build_unified_purchase_payload([], [request], filters={"beneficiary_company": "Raw Factory"})["total_count"] == 0
	assert s.build_unified_purchase_payload([], [request], filters={"search": "Raw molds"})["total_count"] == 1


@pytest.mark.parametrize("size", [500, 2500])
def test_list_progress_uses_one_shared_batch_and_page_or_filter_scope(monkeypatch, size):
	b = ReadBoundary([po(f"PO-{index:04}", grand_total=1) for index in range(size)], oa_installed=False)
	s = connect(monkeypatch, b)
	calls = []
	def batch(names, include_items=False):
		calls.append((list(names), include_items))
		return {name: {**progress_row(unpaid=2), **({"item_fields": ["name", "warehouse"], "items": [{"name": name + "-ITEM", "warehouse": "W1"}]} if include_items else {})} for name in names}
	monkeypatch.setattr(s, "_load_order_progress", batch, raising=False)
	result = s.get_unified_purchase_list(start=100)
	assert len(calls) == 1 and len(calls[0][0]) == 100 and calls[0][1] is True
	assert all(row.get("order_progress", {}).get("external", {}).get("order_unpaid") == 2 for row in result["rows"])
	assert all(row["order_progress"]["items"][0]["name"] == row["name"] + "-ITEM" for row in result["rows"])
	calls.clear()
	result = s.get_unified_purchase_list(start=100, page_length=size, filters={"progress_phase": "supplier_unpaid"})
	assert len(calls) == 2 and len(calls[0][0]) == size and calls[0][1] is False
	assert len(calls[1][0]) == size - 100 and calls[1][1] is True
	assert result["total_count"] == size and len(result["rows"]) == size - 100
	assert result["totals"]["orders"] == [{"currency": "USD", "amount": size}]


def test_export_seven_groups_expand_readable_leaves_in_requested_order_without_losing_multi_currency():
	s = backend()
	progress = progress_row(unpaid=12.34567, internal=[
		{"state": "payable", "internal_order": "INT-1", "beneficiary_company": "FACTORY", "currency": "MXN",
			"payable_total": 120, "settled": 20, "payable_outstanding": 100, "warnings": []},
		{"state": "payable", "internal_order": "INT-2", "beneficiary_company": "FACTORY2", "currency": "USD",
			"payable_total": 3, "settled": 1, "payable_outstanding": 2, "warnings": []}],
		pending=[{"internal_order": "INT-1", "pending_stock_qty": 2.345, "stock_uom": "Nos"}])
	order = po(grand_total=45.6789, order_progress=progress, party_account_currency="CNY")
	groups = ["internal_settlement", "order_context", "supplier_context", "project_context", "external_payment",
		"receipt_logistics", "action_context", "name"]
	data = s.build_unified_purchase_export([order], [], columns=groups)
	assert data[0][0] == "内部订单"
	assert data[0].count(s.EXPORT_LABELS["name"]) == 1
	assert "预付款币种" in data[0]
	assert "出纳实付（未代表 ERP 入账）" in data[0]
	assert "INT-1" in str(data[1]) and "INT-2" in str(data[1])
	assert "MXN" in str(data[1]) and "USD" in str(data[1]) and "Nos" in str(data[1])
	assert "{'" not in str(data[1]) and '"external"' not in str(data[1])
	assert order["grand_total"] == 45.6789 and progress["external"]["order_unpaid"] == 12.34567


def test_material_export_keeps_whole_order_totals_and_alignment_without_filling_unknowns():
	s = backend()
	progress = {"currency": "CNY", "item_fields": ["name", "item_code", "warehouse", "qty", "amount"], "items": [
		{"name": "I-1", "item_code": "SAME", "warehouse": "W1", "qty": 0, "amount": None},
		{"name": "I-2", "item_code": "SAME", "warehouse": None, "qty": None, "amount": 12.34567, "rate": 999},
	]}
	data = s.build_unified_purchase_export([po(grand_total=100, order_progress=progress)], [], columns=["name", "item_code", "warehouse", "qty", "amount", "rate", "grand_total"])
	assert len(data) == 2
	assert data[1] == ["PO-1", "SAME\nSAME", "W1\n—", "0\n—", "—\n12.35", "—\n—", 100.0]
	assert progress["items"][1]["amount"] == 12.34567


def test_group_export_is_unbounded_and_shares_computed_filter_batch(monkeypatch, capture_xlsx):
	b = ReadBoundary([po(f"PO-{index:04}") for index in range(2601)], oa_installed=False)
	s = connect(monkeypatch, b)
	calls = []
	def batch(names):
		calls.append(list(names))
		return {name: progress_row(unpaid=1 if int(name[-4:]) % 2 else 0) for name in names}
	monkeypatch.setattr(s, "_load_order_progress", batch, raising=False)
	s.export_unified_purchase_list(filters={"progress_phase": "supplier_unpaid"}, columns=["order_context", "external_payment"])
	assert len(calls) == 1 and len(calls[0]) == 2601
	assert b"PO-2599" in b.response["filecontent"] and b"PO-2600" not in b.response["filecontent"]
	assert b.response["filecontent"].count(b"PO-") == 1300


def test_managed_private_source_evidence_reconciliation_parse_once_and_finance_acl_once(monkeypatch):
	s = backend()
	source = json.dumps({"version": "v1", "business_id": "DT-ORIGINAL", "currency": "CNY", "detail_total_amount": "2"})
	proof = json.dumps({"version": "proof-v1", "paid_amount": "8", "currency": "CNY", "payment_evidence_status": "recorded", "bank": "PRIVATE"})
	recon = json.dumps({"verified": True, "evidence_version": "proof-v1", "payment_entries": ["SECRET-PE"]})
	requests = [oa(f"OA-{index}", purchase_order="PO-1", custom_purchase_source_id="source", custom_purchase_source_json=source,
		custom_cashier_payment_evidence=proof, custom_purchase_payment_reconciliation=recon) for index in range(20)]
	b = ReadBoundary([po(custom_oa_purchase_expense="OA-0")], requests,
		read={"Purchase Order", "OA Purchase Request", "Payment Entry"},
		fields={"Purchase Order": set(po()), "OA Purchase Request": set(requests[0]), "Payment Entry": s.CASHIER_READ_FIELDS})
	s = connect(monkeypatch, b)
	parsed = []
	original = json.loads
	def loads(value, *args, **kwargs):
		parsed.append(value)
		return original(value, *args, **kwargs)
	monkeypatch.setattr(s.json, "loads", loads)
	row = s.get_unified_purchase_list()["rows"][0]
	assert len(b.private_reads) == 1
	assert parsed.count(source) == parsed.count(proof) == parsed.count(recon) == 20
	assert sum(dt == "Payment Entry" for dt, _ in b.field_calls) == 1
	assert "SECRET-PE" not in str(row) and "PRIVATE" not in str(row)
	assert all(reference.get("cashier_reconciliation_verified") is True for reference in row["oa_references"])


def test_reconciliation_requires_matching_version_and_finance_permission_without_erp_payment_invention(monkeypatch):
	request = oa(custom_purchase_source_id="source", custom_purchase_source_json='{"version":"v1"}',
		custom_cashier_payment_evidence='{"version":"proof-v2","paid_amount":"20","currency":"CNY"}',
		custom_purchase_payment_reconciliation='{"verified":true,"evidence_version":"proof-v1","payment_entries":["SECRET"]}')
	b = ReadBoundary([], [request], read={"OA Purchase Request", "Payment Entry"},
		fields={"OA Purchase Request": set(request), "Payment Entry": backend().CASHIER_READ_FIELDS, "Purchase Order": {"name"}})
	row = connect(monkeypatch, b).get_unified_purchase_list()["rows"][0]
	assert row.get("cashier_reconciliation_verified") is False
	assert row.get("order_progress") is None
	assert row["cashier_paid_amount"] == "20" and "SECRET" not in str(row)
	b.fields["Payment Entry"] = {"name"}
	row = connect(monkeypatch, b).get_unified_purchase_list()["rows"][0]
	assert row["cashier_paid_amount"] is None and row.get("cashier_reconciliation_verified") is None


def test_export_group_overlap_deduplicates_leaves_but_duplicate_unknown_input_is_rejected():
	s = backend()
	assert s._columns(["external_payment", "currency"]).count("currency") == 1
	for columns in (["external_payment", "external_payment"], ["order_context", "secret"], ["name", "name"]):
		with pytest.raises(ValueError):
			s._columns(columns)


def test_group_export_keeps_each_stock_uom_manual_provenance_and_unknown_ap_distinct():
	s = backend()
	progress = progress_row(internal=[{"state": "draft_quote", "internal_order": "QUOTE", "currency": "MXN", "warnings": ["报价不是应付"]}],
		pending=[{"internal_order": "INT-1", "pending_stock_qty": 2.3456, "received_stock_qty": 1, "stock_uom": "Nos"},
			{"internal_order": "INT-2", "pending_stock_qty": 3, "received_stock_qty": 2, "stock_uom": "Kg"}])
	progress["receipt_logistics"] = [{"name": "LINK", "cost_batch": "BATCH", "state": "reported", "reported_quantities": [{"qty": 5, "uom": "箱"}],
		"manual_nodes": [{"node": "reported_arrival", "note": "人工核对", "by": "Operator", "on": "2026-10-07"}],
		"provenance": [{"source_id": "COMMENT", "author": "Author", "time": "2026-10-07", "remark": "到货"}], "warnings": ["报告不等于入库"]}]
	data = s.build_unified_purchase_export([po(order_progress=progress)], [], columns=["internal_settlement", "receipt_logistics"])
	values = dict(zip(data[0], data[1]))
	assert values["内部应付总额（已提交）"] == "QUOTE: — MXN"
	assert values["工厂原生未收数量/单位"] == "INT-1: 2.35 Nos\nINT-2: 3 Kg"
	assert values["工厂原生收货数量/单位"] == "INT-1: 1 Nos\nINT-2: 2 Kg"
	assert "Author" in values["物流来源/作者/时间"] and "COMMENT" in values["物流来源/作者/时间"]
	assert "Operator" in values["人工物流节点"] and "5 箱" in values["物流报告数量/单位（不等于入库）"]
	assert progress["factory_receipt"]["quantities"][0]["pending_stock_qty"] == 2.3456


def test_order_group_preserves_multiple_oa_amount_currency_and_source_provenance_readably():
	data = backend().build_unified_purchase_export([po(custom_oa_purchase_expense="OA-1")],
		[oa(), oa("OA-2", purchase_order="PO-1", oa_code="DT-2", currency="USD", detail_total_amount=7)], columns=["order_context"])
	assert "OA 来源核对明细" in data[0]
	value = data[1][data[0].index("OA 来源核对明细")]
	assert "10.00 CNY" in value and "7.00 USD" in value and "DT-2" in value and "OA-1" in value
	assert "[{" not in value


@pytest.mark.parametrize("columns", [["external_payment"], ["cashier_paid_amount", "cashier_currency", "cashier_evidence_status",
	"cashier_reconciliation_verified", "cashier_reconciliation_warning"]])
def test_multiple_managed_oa_payment_export_keeps_aligned_per_source_evidence_in_each_selected_leaf(monkeypatch, capture_xlsx, columns):
	s = backend()
	requests = []
	for index, amount, currency, verified in ((1, "8", "CNY", True), (2, "3", "USD", False), (3, None, None, None)):
		proof = {"version": "proof-" + str(index), "paid_amount": amount, "currency": currency, "payment_evidence_status": "recorded"} if amount is not None else {}
		recon = {"verified": verified, "evidence_version": proof.get("version"), "payment_entries": ["SECRET-PE"]} if verified is not None else {}
		requests.append(oa("OA-" + str(index), purchase_order="PO-1", oa_code="DT-" + str(index), custom_purchase_source_id="source-" + str(index),
			custom_purchase_source_json=json.dumps({"version": "v1", "business_id": "DT-" + str(index), "currency": currency,
				"detail_total_amount": amount, "requested_amount": amount}), custom_cashier_payment_evidence=json.dumps(proof),
			custom_purchase_payment_reconciliation=json.dumps(recon)))
	b = ReadBoundary([po(custom_oa_purchase_expense="OA-1")], requests, read={"Purchase Order", "OA Purchase Request", "Payment Entry"},
		fields={"Purchase Order": set(po()), "OA Purchase Request": set(requests[0]), "Payment Entry": s.CASHIER_READ_FIELDS})
	s = connect(monkeypatch, b)
	projected = []
	original = s._progress_export_projection
	def projection(row):
		projected.append(row["name"])
		return original(row)
	monkeypatch.setattr(s, "_progress_export_projection", projection)
	s.export_unified_purchase_list(columns=columns)
	data, _ = __import__("ast").literal_eval(b.response["filecontent"].decode())
	values = dict(zip(data[0], data[1]))
	assert values[s.EXPORT_LABELS["cashier_paid_amount"]] == "OA-1 / DT-1: 8.00 CNY\nOA-2 / DT-2: 3.00 USD\nOA-3 / DT-3: — 币种待核对"
	assert values[s.EXPORT_LABELS["cashier_currency"]] == "OA-1 / DT-1: CNY\nOA-2 / DT-2: USD\nOA-3 / DT-3: —"
	assert values[s.EXPORT_LABELS["cashier_evidence_status"]] == "OA-1 / DT-1: recorded\nOA-2 / DT-2: recorded\nOA-3 / DT-3: —"
	assert values[s.EXPORT_LABELS["cashier_reconciliation_verified"]] == "OA-1 / DT-1: 已核对 ERP\nOA-2 / DT-2: 尚未核对 ERP\nOA-3 / DT-3: 未知（权限或证据待核对）"
	assert "OA-3 / DT-3:" in values[s.EXPORT_LABELS["cashier_reconciliation_warning"]]
	assert "SECRET-PE" not in str(data)
	assert len(b.private_reads) == 1 and projected == ["PO-1"]
	assert sum(dt == "Payment Entry" for dt, _ in b.field_calls) == 1


def test_single_source_cashier_payment_export_keeps_scalar_and_finance_denied_multiple_refs_stay_unknown(monkeypatch):
	request = oa(custom_purchase_source_id="source", custom_purchase_source_json='{"version":"v1"}',
		custom_cashier_payment_evidence='{"version":"proof","paid_amount":"8","currency":"CNY","payment_evidence_status":"recorded"}',
		custom_purchase_payment_reconciliation='{"verified":true,"evidence_version":"proof"}')
	b = ReadBoundary([po(custom_oa_purchase_expense="OA-1")], [request], read={"Purchase Order", "OA Purchase Request", "Payment Entry"},
		fields={"Purchase Order": set(po()), "OA Purchase Request": set(request), "Payment Entry": backend().CASHIER_READ_FIELDS})
	s = connect(monkeypatch, b)
	data = s._export_data(s.get_unified_purchase_list()["rows"], ["cashier_paid_amount", "cashier_currency", "cashier_reconciliation_verified"])
	assert data[1] == [8.0, "CNY", True]
	b.records["OA Purchase Request"].append({**request, "name": "OA-2", "purchase_order": "PO-1", "oa_code": "DT-2"})
	b.fields["Payment Entry"] = {"name"}
	data = s._export_data(s.get_unified_purchase_list()["rows"], ["cashier_paid_amount", "cashier_reconciliation_verified"])
	assert "8.00" not in str(data)
	assert data[1][1].count("未知（权限或证据待核对）") == 2
	assert "尚未核对 ERP" not in str(data)


def test_private_source_identity_field_denied_does_not_relabel_unconfirmed_managed_source_as_legacy_buyer(monkeypatch):
	request = oa(target_company="Old proposal", custom_purchase_source_id="SECRET-ID",
		custom_purchase_source_json='{"version":"v1","project":"Raw project"}', custom_purchase_company_confirmed=0)
	b = ReadBoundary([], [request], fields={"Purchase Order": {"name"}, "OA Purchase Request": set(request) - {
		"custom_purchase_source_id", "custom_purchase_source_json", "custom_purchase_company_confirmed"}})
	s = connect(monkeypatch, b)
	row = s.get_unified_purchase_list()["rows"][0]
	assert row["company"] is None and row["role_context"]["company_confirmed"] is False
	assert s.get_unified_purchase_list(filters={"company": "Old proposal"})["total_count"] == 0
	assert "SECRET-ID" not in str(row)
