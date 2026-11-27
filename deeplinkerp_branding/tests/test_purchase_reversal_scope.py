"""Bounded read-only native cancellation dependency collection."""

import importlib.util
import sys
import unittest
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe


def document(doctype, name, **values):
	doc = SimpleNamespace(
		**(
			dict(
				doctype=doctype,
				name=name,
				company="C",
				docstatus=1,
				supplier="Supplier",
				items=[],
				references=[],
				supplied_items=[],
				packed_items=[],
				posting_date="2026-01-10",
				posting_time="12:00:00",
				transaction_date="2026-01-10",
				status="To Receive and Bill",
				status_updater=[],
			)
			| values
		)
	)
	doc.get = lambda field, default=None: getattr(doc, field, default)
	doc.check_permission = Mock()
	return doc


def item(name, code="A", warehouse="W1", **values):
	return frappe._dict(
		dict(
			name=name,
			doctype="Purchase Receipt Item",
			item_code=code,
			warehouse=warehouse,
			stock_uom="Nos",
			stock_qty=1,
			qty=1,
		)
		| values
	)


class ReversalScopeTests(unittest.TestCase):
	def setUp(self):
		path = "deeplinkerp_branding.services.purchase_reversal_scope"
		self.assertIsNotNone(importlib.util.find_spec(path), "Read-only reversal scope collector is missing")
		self.scope = __import__(path, fromlist=["scope"])
		self.docs = {}
		self.sles = []
		self.bins = {("A", "W1"), ("A", "W2"), ("B", "W1"), ("B", "W2"), ("D", "W3")}
		self.queries = []
		# This SELECT simulator is a pure test double, not the current site's
		# LocalProxy. It must work under the site's AND site-free pytest entry.
		database = patch.object(frappe, "db", Mock())
		database.start()
		self.addCleanup(database.stop)

		def throw(message, exception=frappe.ValidationError, **kwargs):
			raise exception(message)

		messages = patch.object(frappe, "throw", side_effect=throw)
		messages.start()
		self.addCleanup(messages.stop)
		self.patches = [
			patch.object(self.scope, "_canonical"),
			patch.object(self.scope.service, "_read", side_effect=self.read),
			patch.object(self.scope.service, "_require_fields"),
			patch.object(frappe, "get_meta", side_effect=lambda dt: Mock(has_field=lambda field: True)),
			patch.object(frappe.db, "get_values", side_effect=self.rows),
			patch.object(frappe.db, "sql", side_effect=self.billing_rows),
			patch.object(frappe.db, "exists", return_value=False),
			patch.object(frappe.db, "get_single_value", return_value=0),
			patch.object(frappe.db, "set_value", side_effect=AssertionError("business write")),
			patch.object(frappe.db, "commit", side_effect=AssertionError("commit")),
			patch.object(frappe, "enqueue", side_effect=AssertionError("enqueue")),
		]
		for guard in self.patches:
			guard.start()
			self.addCleanup(guard.stop)

	def read(self, doctype, name, fields=()):
		if doctype == "Company":
			return document(doctype, name)
		if doctype == "Item":
			return document(doctype, name, is_stock_item=1, stock_uom="Nos", disabled=0)
		if doctype == "Warehouse":
			return self.docs.get((doctype, name), document(doctype, name, is_group=0, disabled=0))
		doc = self.docs[doctype, name]
		doc.check_permission("read")
		return doc

	def rows(self, doctype, filters, fields, **kwargs):
		self.assertGreater(kwargs.get("limit", 0), 0, "Every internal evidence query must be bounded")
		self.assertFalse(kwargs.get("for_update"), "Scope collection must not acquire write locks")
		self.queries.append((doctype, dict(filters), kwargs["limit"]))
		if doctype == "Bin":
			return (
				[frappe._dict(name="BIN")]
				if (filters["item_code"], filters["warehouse"]) in self.bins
				else []
			)
		if doctype == "Purchase Receipt Item":
			return [
				frappe._dict(parent=doc.name)
				for doc in self.docs.values()
				if doc.doctype == "Purchase Receipt"
				and doc.docstatus == 1
				and not doc.get("is_return")
				and any(
					row.get("purchase_order_item") in filters["purchase_order_item"][1] for row in doc.items
				)
			][: kwargs["limit"]]
		self.assertEqual(doctype, "Stock Ledger Entry")

		def matches(row):
			for key, condition in filters.items():
				value = row.get(key)
				if isinstance(condition, (list, tuple)):
					op, target = condition
					if op == "in" and value not in target:
						return False
					if op == "not in" and value in target:
						return False
					if op == ">=" and str(value) < str(target):
						return False
					if op == ">" and value <= target:
						return False
					if op == "!=" and value == target:
						return False
					if op == "is" and target == "not set" and value:
						return False
				elif value != condition:
					return False
			return True

		return [row for row in self.sles if matches(row)][: kwargs["limit"]]

	def billing_rows(self, query, values, **kwargs):
		self.assertTrue(query.startswith("SELECT DISTINCT"))
		self.assertIn("LIMIT %(limit)s", query)
		return [
			frappe._dict(parent=doc.name)
			for doc in self.docs.values()
			if doc.doctype == "Purchase Receipt"
			and doc.name not in values["known"]
			and doc.docstatus == 1
			and not doc.get("is_return")
			and any(row.get("purchase_order_item") in values["details"] for row in doc.items)
		][: values["limit"]]

	def add(self, doc):
		self.docs[doc.doctype, doc.name] = doc
		return doc

	def sle(self, doc, row, warehouse=None, **values):
		result = frappe._dict(
			dict(
				name="SLE-" + str(len(self.sles)),
				voucher_type=doc.doctype,
				voucher_no=doc.name,
				voucher_detail_no=row.name,
				item_code=row.item_code,
				warehouse=warehouse or row.get("warehouse") or row.get("t_warehouse"),
				company=doc.company,
				posting_datetime=doc.posting_date + " " + doc.posting_time,
				posting_date=doc.posting_date,
				posting_time=doc.posting_time,
				creation="2026-02-01 00:00:00",
				is_cancelled=0,
				actual_qty=1,
				dependant_sle_voucher_detail_no=None,
			)
			| values
		)
		self.sles.append(result)
		return result

	def test_document_scope_is_immutable_sorted_and_needs_no_existing_bin(self):
		root = self.add(
			document("Purchase Receipt", "PR", items=[item("R2", "B", "W2"), item("R1"), item("R3")])
		)
		self.bins.clear()
		result = self.scope.collect_document_scope(root)
		self.assertEqual(result.pairs, (self.scope.StockPair("A", "W1"), self.scope.StockPair("B", "W2")))
		self.assertEqual(result.vouchers, ())
		with self.assertRaises(FrozenInstanceError):
			result.company = "OTHER"
		self.assertEqual(self.queries, [])
		self.assertEqual(root.items[0].warehouse, "W2")

	def test_boundary_budget_old_new_pairs_are_one_union_not_two_root_limits(self):
		first = document("Purchase Receipt", "ROOT", docstatus=0, items=[item("R", warehouse="W1")])
		second = document("Purchase Receipt", "ROOT", docstatus=0, items=[item("R", warehouse="W2")])
		with patch.object(self.scope, "MAX_PAIRS", 1):
			self.scope.collect_document_scope(first)
			self.scope.collect_document_scope(second)
			budget = self.scope._ScopeBudget()
			self.scope._Collector(first, budget=budget)
			with self.assertRaises(frappe.ValidationError):
				self.scope._Collector(second, budget=budget)

	def test_ordinary_non_stock_invoice_draft_protects_real_draft_receipt_without_public_b1_relaxation(self):
		receipt = self.add(document("Purchase Receipt", "PR", docstatus=0, items=[item("R")]))
		invoice = document(
			"Purchase Invoice",
			"PI",
			docstatus=0,
			update_stock=0,
			items=[item("I", purchase_receipt=receipt.name, pr_detail="R")],
		)
		with self.assertRaises(frappe.ValidationError):
			self.scope.collect_document_scope(invoice)  # public capability remains strict
		result = self.scope.collect_boundary_scope(invoice)
		self.assertIn(self.scope.DocumentIdentity(receipt.doctype, receipt.name), result.sources)
		self.assertEqual(result.vouchers, ())
		for state, stock in ((1, 0), (0, 1)):
			with self.subTest(state=state, stock=stock):
				invoice.docstatus, invoice.update_stock = state, stock
				with self.assertRaises(frappe.ValidationError):
					self.scope.collect_boundary_scope(invoice)
		invoice.docstatus, invoice.update_stock = 0, 0
		for field, wrong in (("company", "OTHER"), ("supplier", "OTHER"), ("docstatus", 2)):
			with self.subTest(receipt_field=field):
				original = getattr(receipt, field)
				setattr(receipt, field, wrong)
				with self.assertRaises(frappe.ValidationError):
					self.scope.collect_boundary_scope(invoice)
				setattr(receipt, field, original)
		for field, wrong in (("pr_detail", "OTHER"), ("stock_uom", "Kg")):
			with self.subTest(invoice_detail_field=field):
				original = invoice.items[0][field]
				invoice.items[0][field] = wrong
				with self.assertRaises(frappe.ValidationError):
					self.scope.collect_boundary_scope(invoice)
				invoice.items[0][field] = original

	def test_boundary_budget_combines_real_source_and_root_typed_identities(self):
		po1 = self.add(document("Purchase Order", "PO1", items=[item("P1", doctype="Purchase Order Item")]))
		po2 = self.add(document("Purchase Order", "PO2", items=[item("P2", doctype="Purchase Order Item")]))
		first = document(
			"Purchase Receipt",
			"ROOT",
			docstatus=0,
			items=[item("R", purchase_order=po1.name, purchase_order_item="P1")],
		)
		second = document(
			"Purchase Receipt",
			"ROOT",
			docstatus=0,
			items=[item("R", purchase_order=po2.name, purchase_order_item="P2")],
		)
		with patch.object(self.scope, "MAX_VOUCHERS", 2):
			self.scope.collect_document_scope(first)
			self.scope.collect_document_scope(second)
			budget = self.scope._ScopeBudget()
			self.scope._Collector(first, budget=budget)
			with self.assertRaises(frappe.ValidationError):
				self.scope._Collector(second, budget=budget)

	def test_boundary_budget_shared_sle_names_are_cached_not_counted_twice_or_prefix_truncated(self):
		first = self.add(document("Purchase Receipt", "PR1", items=[item("R1")]))
		second = self.add(document("Purchase Receipt", "PR2", items=[item("R2")]))
		for doc in (first, second):
			self.sle(doc, doc.items[0])
		with patch.object(self.scope, "MAX_SLES", 2):
			budget = self.scope._ScopeBudget()
			one = self.scope._Collector(first, budget=budget)
			one.closure()
			before = len(self.queries)
			two = self.scope._Collector(second, budget=budget)
			two.closure()
			self.assertEqual({row.name for row in one.result().vouchers}, {"PR1", "PR2"})
			self.assertEqual(two.result().vouchers, one.result().vouchers)
			self.assertEqual(set(budget.sles), {"SLE-0", "SLE-1"})
			for doctype, filters, limit in self.queries[before:]:
				if doctype == "Stock Ledger Entry":
					self.assertEqual(limit, 1)
					self.assertEqual(filters["name"], ["not in", ["SLE-0", "SLE-1"]])

	def test_native_fixed_point_includes_cancelled_root_equal_time_and_whole_vouchers(self):
		root = self.add(document("Purchase Receipt", "PR", items=[item("R")]))
		self.sle(root, root.items[0], is_cancelled=1)
		transfer = self.add(
			document(
				"Stock Entry",
				"TRANSFER",
				purpose="Material Transfer",
				items=[item("T", s_warehouse="W1", t_warehouse="W2", warehouse=None)],
			)
		)
		self.sle(
			transfer,
			transfer.items[0],
			"W1",
			actual_qty=-1,
			dependant_sle_voucher_detail_no="T",
			creation="2026-01-01 00:00:00",
		)
		self.sle(transfer, transfer.items[0], "W2")
		repack = self.add(
			document(
				"Stock Entry",
				"REPACK",
				purpose="Repack",
				posting_date="2026-01-11",
				items=[
					item("IN", s_warehouse="W2", warehouse=None),
					item("OUT", "B", None, t_warehouse="W2", is_finished_item=1),
					item("EXTRA", "D", None, t_warehouse="W3"),
				],
			)
		)
		self.sle(repack, repack.items[0], "W2", actual_qty=-1, dependant_sle_voucher_detail_no="OUT")
		self.sle(repack, repack.items[1], "W2")
		self.sle(repack, repack.items[2], "W3")
		future = self.add(
			document(
				"Stock Entry",
				"FUTURE",
				purpose="Material Issue",
				posting_date="2026-01-12",
				items=[item("F", "D", None, s_warehouse="W3")],
			)
		)
		self.sle(future, future.items[0], "W3", actual_qty=-1)
		other = self.add(
			document("Purchase Receipt", "OTHER", company="Other", items=[item("O", "UNRELATED", "OTHER-WH")])
		)
		self.sle(other, other.items[0])
		result = self.scope.collect_cancellation_scope(root)
		self.assertEqual(
			result.vouchers,
			tuple(
				sorted(
					self.scope.DocumentIdentity(doc.doctype, doc.name)
					for doc in (root, transfer, repack, future)
				)
			),
		)
		self.assertEqual(
			result.pairs,
			tuple(
				sorted(
					self.scope.StockPair(*pair)
					for pair in (("A", "W1"), ("A", "W2"), ("B", "W2"), ("D", "W3"))
				)
			),
		)
		self.assertTrue(
			any(
				filters.get("is_cancelled") is None and filters.get("voucher_no") == "PR"
				for _, filters, _ in self.queries
			)
		)
		self.assertFalse(any("creation" in filters for _, filters, _ in self.queries))

	def test_gl_cartesian_match_guards_whole_voucher_without_fictional_stock_edges(self):
		root = self.add(document("Purchase Receipt", "PR", items=[item("A1"), item("B2", "B", "W2")]))
		for row in root.items:
			self.sle(root, row)
		cross = self.add(
			document(
				"Purchase Receipt",
				"CROSS",
				posting_date="2026-01-11",
				items=[item("C", "A", "W2"), item("X", "D", "W3")],
			)
		)
		for row in cross.items:
			self.sle(cross, row)
		future = self.add(
			document("Purchase Receipt", "FUTURE", posting_date="2026-01-12", items=[item("F", "D", "W3")])
		)
		self.sle(future, future.items[0])
		result = self.scope.collect_cancellation_scope(root)
		self.assertEqual({d.name for d in result.vouchers}, {"PR", "CROSS"})
		self.assertEqual(
			{(p.item_code, p.warehouse) for p in result.pairs},
			{("A", "W1"), ("B", "W2"), ("A", "W2"), ("D", "W3")},
		)
		self.assertNotIn(self.scope.StockPair("B", "W1"), result.pairs)

	def test_pair_time_anchor_does_not_scan_before_new_pair_exists(self):
		root = self.add(document("Purchase Receipt", "PR", items=[item("R")]))
		self.sle(root, root.items[0])
		transfer = self.add(
			document(
				"Stock Entry",
				"T",
				purpose="Material Transfer",
				posting_date="2026-01-12",
				items=[item("TROW", warehouse=None, s_warehouse="W1", t_warehouse="W2")],
			)
		)
		self.sle(transfer, transfer.items[0], "W1", actual_qty=-1, dependant_sle_voucher_detail_no="TROW")
		self.sle(transfer, transfer.items[0], "W2")
		old = self.add(
			document("Purchase Receipt", "OLD", posting_date="2026-01-09", items=[item("O", "A", "W2")])
		)
		self.sle(old, old.items[0])
		result = self.scope.collect_cancellation_scope(root)
		self.assertEqual({d.name for d in result.vouchers}, {"PR", "T"})

	def test_cancellation_requires_persisted_submitted_root_and_real_bins(self):
		root = self.add(document("Purchase Receipt", "PR", items=[item("R")]))
		self.sle(root, root.items[0])
		self.bins.clear()
		with self.assertRaises(frappe.ValidationError):
			self.scope.collect_cancellation_scope(root)
		self.scope.collect_document_scope(root)
		root.docstatus = 0
		with self.assertRaises(frappe.ValidationError):
			self.scope.collect_cancellation_scope(root)

	def test_source_resolution_reuses_real_row_company_item_uom_and_read_permissions(self):
		order = self.add(document("Purchase Order", "PO", items=[item("POI")]))
		receipt = self.add(
			document(
				"Purchase Receipt", "PR", items=[item("PRI", purchase_order="PO", purchase_order_item="POI")]
			)
		)
		invoice = self.add(
			document(
				"Purchase Invoice",
				"PI",
				items=[
					item("PII", purchase_order="PO", po_detail="POI", purchase_receipt="PR", pr_detail="PRI")
				],
			)
		)
		entry = self.add(
			document(
				"Payment Entry",
				"PE",
				party_type="Supplier",
				party="Supplier",
				references=[frappe._dict(reference_doctype="Purchase Invoice", reference_name="PI")],
			)
		)
		result = self.scope.collect_document_scope(entry)
		self.assertEqual(
			result.sources,
			tuple(
				sorted(
					self.scope.DocumentIdentity(doc.doctype, doc.name) for doc in (order, receipt, invoice)
				)
			),
		)
		for field, value in (("purchase_order_item", "OTHER"), ("stock_uom", "Kg"), ("item_code", "OTHER")):
			with self.subTest(field=field):
				old = receipt.items[0].get(field)
				receipt.items[0][field] = value
				with self.assertRaises(frappe.ValidationError):
					self.scope.collect_document_scope(receipt)
				receipt.items[0][field] = old
		order.check_permission.side_effect = frappe.PermissionError
		with self.assertRaises(frappe.PermissionError):
			self.scope.collect_document_scope(entry)

	def test_incoming_replacement_sources_and_warehouses_are_the_actual_scope(self):
		_order = self.add(document("Purchase Order", "NEW", items=[item("NEWROW", "B", "W2")]))
		incoming = document(
			"Purchase Receipt",
			"PR",
			items=[item("R", "B", "W2", purchase_order="NEW", purchase_order_item="NEWROW")],
		)
		result = self.scope.collect_document_scope(incoming)
		self.assertEqual(result.sources, (self.scope.DocumentIdentity("Purchase Order", "NEW"),))
		self.assertEqual(result.pairs, (self.scope.StockPair("B", "W2"),))
		self.docs["Warehouse", "W2"] = document("Warehouse", "W2", company="OTHER", is_group=0, disabled=0)
		with self.assertRaises(frappe.ValidationError):
			self.scope.collect_document_scope(incoming)

	def test_dependency_detail_and_wrong_company_are_not_filtered_away(self):
		root = self.add(document("Purchase Receipt", "PR", items=[item("R")]))
		self.sle(root, root.items[0], dependant_sle_voucher_detail_no="MISSING")
		with self.assertRaises(frappe.ValidationError):
			self.scope.collect_cancellation_scope(root)
		self.sles[0].dependant_sle_voucher_detail_no = None
		self.sles[0].company = "OTHER"
		with self.assertRaises(frappe.ValidationError):
			self.scope.collect_cancellation_scope(root)

	def test_native_voucher_number_type_collision_fails_closed(self):
		root = self.add(document("Purchase Receipt", "SAME", items=[item("R")]))
		self.sle(root, root.items[0])
		other = self.add(
			document(
				"Stock Entry",
				"SAME",
				purpose="Material Receipt",
				items=[item("X", "OTHER", None, t_warehouse="OUTSIDE")],
			)
		)
		self.sle(other, other.items[0], "OUTSIDE")
		with self.assertRaises(frappe.ValidationError):
			self.scope.collect_cancellation_scope(root)

	def test_bounded_queries_detect_remaining_capacity_plus_one_without_truncation(self):
		root = self.add(document("Purchase Receipt", "PR", items=[item("R")]))
		for _ in range(3):
			self.sle(root, root.items[0])
		with patch.object(self.scope, "MAX_SLES", 2):
			with self.assertRaises(frappe.ValidationError):
				self.scope.collect_cancellation_scope(root)
		self.assertEqual(self.queries[0][2], 3)

	def test_duplicate_frontiers_do_not_spend_the_unique_remaining_budget(self):
		root = self.add(document("Purchase Receipt", "PR", items=[item("R")]))
		self.sle(root, root.items[0])
		future = self.add(document("Purchase Receipt", "F", items=[item("F")]))
		self.sle(future, future.items[0])
		late = self.add(document("Purchase Receipt", "L", posting_date="2026-01-11", items=[item("L")]))
		self.sle(late, late.items[0])
		with patch.object(self.scope, "MAX_SLES", 3):
			result = self.scope.collect_cancellation_scope(root)
		self.assertEqual({d.name for d in result.vouchers}, {"PR", "F", "L"})
		self.assertTrue(any(limit == 1 and "name" in filters for _, filters, limit in self.queries))

	def test_each_hard_limit_accepts_boundary_and_refuses_overflow(self):
		# Small substitutions exercise all cap algorithms; no 50k database fixtures.
		for cap, boundary in (("MAX_PAIRS", 2), ("MAX_VOUCHERS", 2), ("MAX_SLES", 2)):
			with self.subTest(cap=cap):
				self.docs.clear()
				self.sles.clear()
				self.queries.clear()
				root = self.add(document("Purchase Receipt", "PR", items=[item("R")]))
				self.sle(root, root.items[0])
				future = self.add(
					document(
						"Purchase Receipt", "F", items=[item("F", "B" if cap == "MAX_PAIRS" else "A", "W1")]
					)
				)
				self.sle(future, future.items[0])
				if cap == "MAX_PAIRS":
					root.items.append(item("B", "B", "W1"))
				with patch.object(self.scope, cap, boundary):
					self.scope.collect_cancellation_scope(root)
				overflow = self.add(
					document(
						"Purchase Receipt",
						"OVERFLOW",
						items=[item("O", "D" if cap == "MAX_PAIRS" else "A", "W1")],
					)
				)
				if cap == "MAX_PAIRS":
					root.items.append(overflow.items[0])
				self.sle(overflow, overflow.items[0])
				with patch.object(self.scope, cap, boundary):
					with self.assertRaises(frappe.ValidationError):
						self.scope.collect_cancellation_scope(root)

	def test_installed_caps_are_exact(self):
		self.assertEqual(
			(self.scope.MAX_PAIRS, self.scope.MAX_VOUCHERS, self.scope.MAX_SLES), (2500, 5000, 50000)
		)

	def test_billing_receipts_are_actual_native_source_footprint_without_stock_propagation(self):
		_order = self.add(document("Purchase Order", "PO", items=[item("POI")]))
		_receipt = self.add(
			document(
				"Purchase Receipt", "PR", items=[item("PRI", purchase_order="PO", purchase_order_item="POI")]
			)
		)
		invoice = self.add(
			document(
				"Purchase Invoice",
				"PI",
				update_stock=0,
				items=[item("PII", purchase_order="PO", po_detail="POI")],
			)
		)
		result = self.scope.collect_document_scope(invoice)
		self.assertEqual(
			result.sources,
			(
				self.scope.DocumentIdentity("Purchase Order", "PO"),
				self.scope.DocumentIdentity("Purchase Receipt", "PR"),
			),
		)
		self.assertEqual(result.pairs, (self.scope.StockPair("A", "W1"),))
		self.assertEqual(result.vouchers, ())

	def test_non_group_company_warehouses_and_stock_field_permissions_are_required(self):
		root = self.add(document("Purchase Receipt", "PR", items=[item("R")]))
		for values in ({"is_group": 1}, {"disabled": 1}, {"company": "OTHER"}):
			with self.subTest(values=values):
				self.docs["Warehouse", "W1"] = document(
					"Warehouse", "W1", **({"is_group": 0, "disabled": 0} | values)
				)
				with self.assertRaises(frappe.ValidationError):
					self.scope.collect_document_scope(root)
		self.docs.pop(("Warehouse", "W1"))

		def fields(doctype, required, parenttype=None):
			if doctype == "Purchase Receipt Item" and "warehouse" in required:
				raise frappe.PermissionError

		with patch.object(self.scope.service, "_require_fields", side_effect=fields):
			with self.assertRaises(frappe.PermissionError):
				self.scope.collect_document_scope(root)
		root.check_permission.side_effect = frappe.PermissionError
		with self.assertRaises(frappe.PermissionError):
			self.scope.collect_document_scope(root)

	def test_retired_warehouse_history_collects_real_ledgers_but_new_document_stays_blocked(self):
		root = self.add(document("Purchase Receipt", "PR", items=[item("R")]))
		self.sle(root, root.items[0])
		self.docs["Warehouse", "W1"] = document("Warehouse", "W1", is_group=0, disabled=1)
		task = document("Repost Item Valuation", "RIV", item_code="A", warehouse="W1")

		def locked_rows(*args, **kwargs):
			self.assertTrue(kwargs.pop("for_update", False))
			return self.rows(*args, **kwargs)

		with patch.object(frappe.db, "get_values", side_effect=locked_rows):
			with self.assertRaises(frappe.ValidationError):
				self.scope.collect_repost_pair_scope(task)
			result = self.scope.collect_repost_pair_scope(task, allow_disabled_warehouses=True)
		self.assertEqual(result.company, "C")
		self.assertEqual(result.pairs, (self.scope.StockPair("A", "W1"),))
		self.assertEqual(result.sles, (self.sles[0].name,))
		self.assertEqual(self.docs["Warehouse", "W1"].disabled, 1)
		with self.assertRaises(frappe.ValidationError):
			self.scope.collect_document_scope(root)

	def test_historical_repost_scope_still_rejects_cross_company_and_group_warehouse(self):
		root = self.add(document("Purchase Receipt", "PR", items=[item("R")]))
		self.sle(root, root.items[0])
		task = document("Repost Item Valuation", "RIV", item_code="A", warehouse="W1")

		def locked_rows(*args, **kwargs):
			kwargs.pop("for_update", None)
			return self.rows(*args, **kwargs)

		for values in ({"company": "OTHER"}, {"is_group": 1}):
			with self.subTest(values=values):
				self.docs["Warehouse", "W1"] = document(
					"Warehouse", "W1", **({"disabled": 1, "is_group": 0} | values)
				)
				with patch.object(frappe.db, "get_values", side_effect=locked_rows):
					with self.assertRaises(frappe.ValidationError):
						self.scope.collect_repost_pair_scope(task, allow_disabled_warehouses=True)

	def test_actual_from_rejected_and_old_subcontract_pairs_are_in_footprint(self):
		root = self.add(
			document(
				"Purchase Receipt",
				"PR",
				is_old_subcontracting_flow=1,
				supplier_warehouse="W2",
				items=[item("R", from_warehouse="W2", rejected_warehouse="W3", rejected_qty=1)],
				supplied_items=[
					frappe._dict(
						doctype="Purchase Receipt Item Supplied",
						name="SUP",
						rm_item_code="D",
						stock_uom="Nos",
					)
				],
			)
		)
		result = self.scope.collect_document_scope(root)
		self.assertEqual(
			set(result.pairs),
			{
				self.scope.StockPair("A", "W1"),
				self.scope.StockPair("A", "W2"),
				self.scope.StockPair("A", "W3"),
				self.scope.StockPair("D", "W2"),
			},
		)
		root.doctype = "Purchase Order"
		root.supplied_items[0].reserve_warehouse = "W3"
		result = self.scope.collect_document_scope(root)
		self.assertEqual(
			set(result.pairs), {self.scope.StockPair("A", "W1"), self.scope.StockPair("D", "W3")}
		)

	def test_unknown_landed_cost_subcontract_and_controller_paths_refuse(self):
		unknown = document(
			"Landed Cost Voucher",
			"LCV",
			purchase_receipts=[
				frappe._dict(
					doctype="Landed Cost Purchase Receipt",
					receipt_document_type="Subcontracting Receipt",
					receipt_document="SCR",
				)
			],
		)
		with self.assertRaises(frappe.ValidationError):
			self.scope.collect_document_scope(unknown)
		root = document("Purchase Receipt", "PR", is_subcontracted=1)
		with self.assertRaises(frappe.ValidationError):
			self.scope.collect_document_scope(root)
		self.patches[0].stop()
		with patch.object(
			frappe, "get_hooks", return_value={"Purchase Receipt": ["custom.UnknownController"]}
		):
			with self.assertRaises(frappe.ValidationError):
				self.scope.collect_document_scope(document("Purchase Receipt", "PR"))

	def test_equal_time_and_earlier_creation_is_not_dropped_at_dependency_anchor(self):
		root = self.add(document("Purchase Receipt", "PR", items=[item("R")]))
		self.sle(root, root.items[0], is_cancelled=1)
		transfer = self.add(
			document(
				"Stock Entry",
				"T",
				purpose="Material Transfer",
				posting_date="2026-01-12",
				items=[item("TROW", warehouse=None, s_warehouse="W1", t_warehouse="W2")],
			)
		)
		self.sle(transfer, transfer.items[0], "W1", actual_qty=-1, dependant_sle_voucher_detail_no="TROW")
		self.sle(transfer, transfer.items[0], "W2")
		peer = self.add(
			document("Purchase Receipt", "PEER", posting_date="2026-01-12", items=[item("P", "A", "W2")])
		)
		self.sle(peer, peer.items[0], creation="2026-01-01 00:00:00")
		result = self.scope.collect_cancellation_scope(root)
		self.assertEqual({d.name for d in result.vouchers}, {"PR", "T", "PEER"})

	def test_wrong_dependency_company_and_cross_voucher_detail_fail(self):
		root = self.add(document("Purchase Receipt", "PR", items=[item("R")]))
		self.sle(root, root.items[0])
		transfer = self.add(
			document(
				"Stock Entry",
				"T",
				purpose="Material Transfer",
				posting_date="2026-01-11",
				items=[item("TROW", warehouse=None, s_warehouse="W1", t_warehouse="W2")],
			)
		)
		self.sle(transfer, transfer.items[0], "W1", actual_qty=-1, dependant_sle_voucher_detail_no="TROW")
		target = self.sle(transfer, transfer.items[0], "W2", company="OTHER")
		with self.assertRaises(frappe.ValidationError):
			self.scope.collect_cancellation_scope(root)
		target.company = "C"
		target.voucher_no = "OTHER"
		with self.assertRaises(frappe.ValidationError):
			self.scope.collect_cancellation_scope(root)

	def test_repack_null_trigger_does_not_call_native_dependency_expansion(self):
		root = self.add(document("Purchase Receipt", "PR", items=[item("R")]))
		self.sle(root, root.items[0])
		repack = self.add(
			document(
				"Stock Entry",
				"REPACK",
				purpose="Repack",
				posting_date="2026-01-11",
				items=[
					item("IN", "B", None, s_warehouse="W2"),
					item("OUT", "A", None, t_warehouse="W1"),
					item("EXTRA", "D", None, t_warehouse="W3", is_finished_item=1),
				],
			)
		)
		self.sle(repack, repack.items[0], "W2", actual_qty=-1, dependant_sle_voucher_detail_no="EXTRA")
		self.sle(repack, repack.items[1], "W1", dependant_sle_voucher_detail_no=None)
		self.sle(repack, repack.items[2], "W3")
		future = self.add(
			document(
				"Stock Entry",
				"FUTURE",
				purpose="Material Issue",
				posting_date="2026-01-12",
				items=[item("F", "D", None, s_warehouse="W3")],
			)
		)
		self.sle(future, future.items[0], "W3", actual_qty=-1)
		result = self.scope.collect_cancellation_scope(root)
		self.assertEqual({d.name for d in result.vouchers}, {"PR", "REPACK"})
		self.assertIn(self.scope.StockPair("D", "W3"), result.pairs)

	def test_actual_operating_links_on_source_and_future_stock_voucher_fail_closed(self):
		for field in ("custom_operating_source", "custom_operating_recognition"):
			with self.subTest(field=field):
				root = self.add(document("Purchase Receipt", "PR", items=[item("R")]))
				self.sles.clear()
				self.sle(root, root.items[0])
				future = self.add(
					document(
						"Purchase Invoice",
						"PI",
						update_stock=1,
						posting_date="2026-01-11",
						items=[item("PII")],
						**{field: "OPERATING"},
					)
				)
				self.sle(future, future.items[0])
				with self.assertRaises(frappe.ValidationError) as caught:
					self.scope.collect_cancellation_scope(root)
				self.assertEqual(caught.exception.purchase_error_id, "operating_dependency_unsupported")

	def test_procurement_sales_links_remain_unsupported(self):
		root = document("Purchase Order", "PO", inter_company_order_reference="SO", items=[item("POI")])
		with self.assertRaises(frappe.ValidationError) as caught:
			self.scope.collect_document_scope(root)
		self.assertEqual(caught.exception.purchase_error_id, "sales_dependency_unsupported")

	def test_mes_source_classification_requires_its_actual_field_permission(self):
		self.patches[0].stop()
		mes = "mes_integration.mes_integration.material_request.MESMaterialRequestPerformanceMixin"

		def hooks(kind, default):
			return {"Material Request": [mes]} if kind == "extend_doctype_class" else {}

		def require(doctype, fields, parenttype=None):
			if "custom_request_source" in fields:
				raise frappe.PermissionError("source field denied")

		mixin = type("MESMixin", (), {})
		controller = type("ExtendedSimpleNamespace", (mixin, SimpleNamespace), {})
		doc = controller(**vars(document("Material Request", "MR")))
		classify = Mock(return_value=False)
		with (
			patch.object(frappe, "get_hooks", side_effect=hooks),
			patch.object(
				frappe, "get_attr", side_effect=lambda path: mixin if path == mes else SimpleNamespace
			),
			patch("frappe.model.base_document.get_controller", return_value=controller),
			patch.object(self.scope.service, "_require_fields", side_effect=require),
			patch.dict(
				sys.modules,
				{
					"mes_integration.mes_integration.material_request": SimpleNamespace(
						is_mes_material_request=classify
					)
				},
			),
		):
			with self.assertRaises(frappe.PermissionError):
				self.scope.collect_document_scope(doc)
			classify.assert_not_called()

	def test_unknown_cached_controller_subclass_without_vetted_hooks_refuses(self):
		self.patches[0].stop()
		controller = type("UnknownCachedController", (SimpleNamespace,), {})
		doc = controller(**vars(document("Purchase Receipt", "PR")))
		with (
			patch.object(frappe, "get_hooks", return_value={}),
			patch.object(frappe, "get_attr", return_value=SimpleNamespace),
			patch("frappe.model.base_document.get_controller", return_value=controller),
		):
			with self.assertRaises(frappe.ValidationError):
				self.scope.collect_document_scope(doc)
		boundary = "deeplinkerp_branding.services.purchase_consistency.ProcurementControllerBoundary"
		ancestor = type("UnknownAncestor", (), {})
		mixin = type("Boundary", (ancestor,), {})
		extended = type("ExtendedSimpleNamespace", (mixin, SimpleNamespace), {})
		doc = extended(**vars(document("Purchase Receipt", "PR")))
		with (
			patch.object(
				frappe,
				"get_hooks",
				side_effect=lambda kind, default: (
					{"Purchase Receipt": [boundary]} if kind == "extend_doctype_class" else {}
				),
			),
			patch.object(
				frappe, "get_attr", side_effect=lambda path: mixin if path == boundary else SimpleNamespace
			),
			patch("frappe.model.base_document.get_controller", return_value=extended),
		):
			with self.assertRaises(frappe.ValidationError):
				self.scope.collect_document_scope(doc)

	def test_cancellation_roots_are_only_the_four_procurement_documents(self):
		for doctype in ("Purchase Order", "Purchase Receipt", "Purchase Invoice", "Payment Entry"):
			with self.subTest(allowed=doctype):
				root = self.add(document(doctype, doctype, party_type="Supplier", party="Supplier"))
				self.assertEqual(
					self.scope.collect_cancellation_scope(root).vouchers,
					(self.scope.DocumentIdentity(doctype, doctype),),
				)
		for doctype in (
			"Landed Cost Voucher",
			"Stock Entry",
			"Delivery Note",
			"Sales Invoice",
			"Stock Reconciliation",
			"Material Request",
		):
			with self.subTest(rejected=doctype):
				root = self.add(document(doctype, doctype, purchase_receipts=[]))
				with self.assertRaises(frappe.ValidationError):
					self.scope.collect_cancellation_scope(root)

	def test_landed_cost_footprint_includes_actual_receipt_and_vendor_invoice(self):
		receipt = self.add(document("Purchase Receipt", "PR", items=[item("R")]))
		vendor = self.add(document("Purchase Invoice", "VENDOR", items=[item("VI")]))
		root = document(
			"Landed Cost Voucher",
			"LCV",
			purchase_receipts=[
				frappe._dict(
					doctype="Landed Cost Purchase Receipt",
					receipt_document_type="Purchase Receipt",
					receipt_document=receipt.name,
				)
			],
			vendor_invoices=[frappe._dict(doctype="Landed Cost Vendor Invoice", vendor_invoice=vendor.name)],
		)
		result = self.scope.collect_document_scope(root)
		self.assertEqual(
			set(result.sources),
			{self.scope.DocumentIdentity(doc.doctype, doc.name) for doc in (receipt, vendor)},
		)
		self.assertEqual(result.pairs, (self.scope.StockPair("A", "W1"),))
		vendor.custom_operating_source = "OPERATING"
		with self.assertRaises(frappe.ValidationError) as caught:
			self.scope.collect_document_scope(root)
		self.assertEqual(caught.exception.purchase_error_id, "operating_dependency_unsupported")

	def test_payment_and_landed_cost_child_links_require_actual_parent_identity(self):
		order = self.add(document("Purchase Order", "PO"))
		receipt = self.add(document("Purchase Receipt", "PR"))
		invoice = self.add(document("Purchase Invoice", "PI"))
		for table in ("references", "purchase_receipts", "vendor_invoices"):
			with self.subTest(table=table):
				link = {
					"references": dict(
						doctype="Payment Entry Reference",
						reference_doctype=order.doctype,
						reference_name=order.name,
					),
					"purchase_receipts": dict(
						doctype="Landed Cost Purchase Receipt",
						receipt_document_type=receipt.doctype,
						receipt_document=receipt.name,
					),
					"vendor_invoices": dict(
						doctype="Landed Cost Vendor Invoice", vendor_invoice=invoice.name
					),
				}[table]
				row = frappe._dict(link | dict(parent="OTHER", parenttype="OTHER", parentfield=table))
				root = document(
					"Payment Entry" if table == "references" else "Landed Cost Voucher",
					"ROOT",
					party_type="Supplier",
					party="Supplier",
					**{table: [row]},
				)
				with self.assertRaises(frappe.ValidationError):
					self.scope.collect_document_scope(root)

	def test_sle_warehouse_is_bound_to_actual_detail_not_a_same_item_sibling(self):
		root = self.add(
			document("Purchase Receipt", "PR", items=[item("R1", "A", "W1"), item("R2", "A", "W2")])
		)
		self.sle(root, root.items[0], warehouse="W2")
		with self.assertRaises(frappe.ValidationError):
			self.scope.collect_cancellation_scope(root)

	def test_sle_row_paths_keep_from_rejected_transfer_and_old_supplied_warehouses(self):
		for path in ("from", "rejected", "transfer", "supplied"):
			with self.subTest(path=path):
				self.sles.clear()
				root = self.add(document("Purchase Receipt", "PR", items=[item("R")]))
				self.sle(root, root.items[0])
				if path == "transfer":
					target = self.add(
						document(
							"Stock Entry",
							"SE",
							purpose="Material Transfer",
							posting_date="2026-01-11",
							items=[item("SEI", warehouse=None, s_warehouse="W1", t_warehouse="W2")],
						)
					)
					self.sle(
						target, target.items[0], "W1", actual_qty=-1, dependant_sle_voucher_detail_no="SEI"
					)
					self.sle(target, target.items[0], "W2")
				elif path == "supplied":
					self.bins.add(("D", "W2"))
					root.is_old_subcontracting_flow = 1
					root.supplier_warehouse = "W2"
					root.supplied_items = [
						frappe._dict(
							name="SUP",
							doctype="Purchase Receipt Item Supplied",
							rm_item_code="D",
							stock_uom="Nos",
						)
					]
					self.sle(root, root.supplied_items[0], "W2", item_code="D", actual_qty=-1)
				else:
					setattr(root.items[0], path + "_warehouse", "W2")
					if path == "rejected":
						root.items[0].rejected_qty = 1
					self.sle(root, root.items[0], "W2", actual_qty=-1)
				result = self.scope.collect_cancellation_scope(root)
				self.assertIn(self.scope.StockPair("D" if path == "supplied" else "A", "W2"), result.pairs)

	def test_stock_entry_direct_and_transit_material_request_sources_are_actual_footprint(self):
		request = self.add(
			document(
				"Material Request", "MR", items=[item("MRI", "A", "W4", doctype="Material Request Item")]
			)
		)
		outgoing = self.add(
			document(
				"Stock Entry",
				"OUT",
				purpose="Material Transfer",
				add_to_transit=1,
				items=[
					item(
						"OUTI",
						doctype="Stock Entry Detail",
						warehouse=None,
						s_warehouse="W1",
						t_warehouse="W2",
						material_request="MR",
						material_request_item="MRI",
					)
				],
			)
		)
		result = self.scope.collect_document_scope(outgoing)
		self.assertEqual(result.sources, (self.scope.DocumentIdentity("Material Request", "MR"),))
		incoming = document(
			"Stock Entry",
			"IN",
			purpose="Material Transfer",
			outgoing_stock_entry="OUT",
			items=[
				item(
					"INI",
					doctype="Stock Entry Detail",
					warehouse=None,
					s_warehouse="W2",
					t_warehouse="W3",
					against_stock_entry="OUT",
					ste_detail="OUTI",
				)
			],
		)
		result = self.scope.collect_document_scope(incoming)
		self.assertEqual(
			set(result.sources),
			{self.scope.DocumentIdentity(doc.doctype, doc.name) for doc in (request, outgoing)},
		)
		self.assertIn(self.scope.StockPair("A", "W4"), result.pairs)

	def test_stock_entry_source_rows_require_real_identity_company_uom_and_acl(self):
		request = self.add(
			document("Material Request", "MR", items=[item("MRI", doctype="Material Request Item")])
		)
		root = document(
			"Stock Entry",
			"SE",
			purpose="Material Transfer",
			items=[
				item(
					"SEI",
					doctype="Stock Entry Detail",
					warehouse=None,
					s_warehouse="W1",
					t_warehouse="W2",
					material_request="MR",
					material_request_item="MRI",
				)
			],
		)
		for invalid in ("detail", "company", "uom", "parent", "read", "field"):
			with self.subTest(invalid=invalid):
				request.company = "C"
				request.items[0].stock_uom = "Nos"
				request.items[0].parent = "MR"
				request.items[0].parenttype = "Material Request"
				request.items[0].parentfield = "items"
				request.check_permission.side_effect = None
				root.items[0].material_request_item = "MRI"
				if invalid == "detail":
					root.items[0].material_request_item = "MISSING"
				if invalid == "company":
					request.company = "OTHER"
				if invalid == "uom":
					request.items[0].stock_uom = "Kg"
				if invalid == "parent":
					request.items[0].parent = "OTHER"
				if invalid == "read":
					request.check_permission.side_effect = frappe.PermissionError("MR denied")
				with patch.object(
					self.scope.service,
					"_require_fields",
					side_effect=(
						lambda dt, fields, parenttype=None: (_ for _ in ()).throw(
							frappe.PermissionError("MR field denied")
						)
						if invalid == "field" and dt == "Material Request Item"
						else None
					),
				):
					with self.assertRaises((frappe.ValidationError, frappe.PermissionError)):
						self.scope.collect_document_scope(root)

	def test_stock_entry_old_subcontract_header_freezes_real_supplied_reserve_pair(self):
		order = self.add(
			document(
				"Purchase Order",
				"PO",
				is_old_subcontracting_flow=1,
				is_subcontracted=1,
				items=[item("MAIN", "FG", "W2")],
				supplied_items=[
					frappe._dict(
						name="SUP",
						doctype="Purchase Order Item Supplied",
						rm_item_code="D",
						main_item_code="FG",
						stock_uom="Nos",
						reserve_warehouse="W3",
						parent="PO",
						parenttype="Purchase Order",
						parentfield="supplied_items",
					)
				],
			)
		)
		root = document(
			"Stock Entry",
			"SE",
			purchase_order="PO",
			purpose="Send to Subcontractor",
			items=[
				item(
					"SEI",
					"D",
					doctype="Stock Entry Detail",
					warehouse=None,
					s_warehouse="W1",
					t_warehouse="W2",
					po_detail="SUP",
					subcontracted_item="FG",
				)
			],
		)
		result = self.scope.collect_document_scope(root)
		self.assertEqual(result.sources, (self.scope.DocumentIdentity("Purchase Order", "PO"),))
		self.assertIn(self.scope.StockPair("D", "W3"), result.pairs)
		order.supplied_items[0].stock_uom = "Kg"
		with self.assertRaises(frappe.ValidationError):
			self.scope.collect_document_scope(root)
		order.supplied_items[0].stock_uom = "Nos"
		for field, value in (
			("po_detail", "MAIN"),
			("allow_alternative_item", 1),
			("original_item", "OTHER"),
		):
			with self.subTest(field=field):
				original = root.items[0].get(field)
				root.items[0][field] = value
				with self.assertRaises(frappe.ValidationError):
					self.scope.collect_document_scope(root)
				root.items[0][field] = original
		order.items[0].job_card = "JC"
		with self.assertRaises(frappe.ValidationError):
			self.scope.collect_document_scope(root)
		root.purpose = "Material Transfer"  # native WO release only runs for Send to Subcontractor
		self.scope.collect_document_scope(root)
		root.purpose = "Send to Subcontractor"
		root.purchase_order = None
		root.subcontracting_inward_order = "SIO"
		with self.assertRaises(frappe.ValidationError):
			self.scope.collect_document_scope(root)

	def test_stock_entry_transit_parent_detail_and_inherited_sources_cannot_be_substituted(self):
		for invalid in ("header", "detail", "parent", "company", "uom", "read", "field", "conflict", "half"):
			with self.subTest(invalid=invalid):
				request = self.add(
					document("Material Request", "MR", items=[item("MRI", doctype="Material Request Item")])
				)
				self.add(
					document("Material Request", "MR2", items=[item("MRI2", doctype="Material Request Item")])
				)
				outgoing = self.add(
					document(
						"Stock Entry",
						"OUT",
						purpose="Material Transfer",
						add_to_transit=1,
						items=[
							item(
								"OUTI",
								doctype="Stock Entry Detail",
								warehouse=None,
								s_warehouse="W1",
								t_warehouse="W2",
								parent="OUT",
								parenttype="Stock Entry",
								parentfield="items",
								material_request=request.name,
								material_request_item="MRI",
							)
						],
					)
				)
				self.add(document("Stock Entry", "OTHER", purpose="Material Transfer"))
				root = document(
					"Stock Entry",
					"IN",
					purpose="Material Transfer",
					outgoing_stock_entry="OUT",
					items=[
						item(
							"INI",
							doctype="Stock Entry Detail",
							warehouse=None,
							s_warehouse="W2",
							t_warehouse="W3",
							against_stock_entry="OUT",
							ste_detail="OUTI",
						)
					],
				)
				if invalid == "header":
					root.outgoing_stock_entry = "OTHER"
				if invalid == "detail":
					root.items[0].ste_detail = "MISSING"
				if invalid == "parent":
					outgoing.items[0].parent = "OTHER"
				if invalid == "company":
					outgoing.company = "OTHER"
				if invalid == "uom":
					outgoing.items[0].stock_uom = "Kg"
				if invalid == "read":
					outgoing.check_permission.side_effect = frappe.PermissionError("SE source denied")
				if invalid == "conflict":
					root.items[0].update(material_request="MR2", material_request_item="MRI2")
				if invalid == "half":
					root.items[0].against_stock_entry = None

				def fields(doctype, names, parenttype=None):
					if invalid == "field" and doctype == "Stock Entry Detail" and "ste_detail" in names:
						raise frappe.PermissionError("transit source field denied")

				with patch.object(self.scope.service, "_require_fields", side_effect=fields):
					with self.assertRaises((frappe.ValidationError, frappe.PermissionError)):
						self.scope.collect_document_scope(root)

	def test_invoice_billing_repost_uses_actual_receipt_anchor_only_when_native_setting_applies(self):
		receipt = self.add(document("Purchase Receipt", "PR", items=[item("R")]))
		self.sle(receipt, receipt.items[0])
		future = self.add(
			document("Purchase Receipt", "FUTURE", posting_date="2026-01-11", items=[item("F")])
		)
		self.sle(future, future.items[0])
		for enabled, is_return, update_billed, seeded in (
			(1, 0, 0, True),
			(0, 0, 0, False),
			(1, 1, 0, False),
			(1, 1, 1, True),
		):
			with self.subTest(enabled=enabled, is_return=is_return, update_billed=update_billed):
				root = self.add(
					document(
						"Purchase Invoice",
						"PI",
						posting_date="2026-01-15",
						is_return=is_return,
						update_billed_amount_in_purchase_receipt=update_billed,
						items=[item("PII", purchase_receipt="PR", pr_detail="R")],
					)
				)
				with patch.object(frappe.db, "get_single_value", return_value=enabled):
					result = self.scope.collect_cancellation_scope(root)
				expected = {self.scope.DocumentIdentity("Purchase Invoice", "PI")}
				if seeded:
					expected |= {
						self.scope.DocumentIdentity(doc.doctype, doc.name) for doc in (receipt, future)
					}
				self.assertEqual(set(result.vouchers), expected)
				self.assertEqual(
					result.posting_datetime, "2026-01-10 12:00:00" if seeded else "2026-01-15 12:00:00"
				)
				self.assertIn(self.scope.DocumentIdentity("Purchase Receipt", "PR"), result.sources)

	def test_invoice_fifo_potential_seeds_use_only_actual_else_po_details_including_stock_pi(self):
		_order = self.add(document("Purchase Order", "PO", items=[item("PO1"), item("PO2", "B", "W2")]))
		receipt = self.add(
			document(
				"Purchase Receipt", "PR", items=[item("R", purchase_order="PO", purchase_order_item="PO1")]
			)
		)
		unrelated = self.add(
			document(
				"Purchase Receipt",
				"OTHER",
				items=[item("O", "B", "W2", purchase_order="PO", purchase_order_item="PO2")],
			)
		)
		future = self.add(
			document("Purchase Receipt", "FUTURE", posting_date="2026-01-11", items=[item("F")])
		)
		self.sle(receipt, receipt.items[0])
		self.sle(unrelated, unrelated.items[0])
		self.sle(future, future.items[0])
		for update_stock in (0, 1):
			with self.subTest(update_stock=update_stock):
				root = self.add(
					document(
						"Purchase Invoice",
						"PI",
						posting_date="2026-01-15",
						update_stock=update_stock,
						items=[item("PII", purchase_order="PO", po_detail="PO1")],
					)
				)
				with patch.object(frappe.db, "get_single_value", return_value=1):
					result = self.scope.collect_cancellation_scope(root)
				self.assertEqual(
					set(result.vouchers),
					{self.scope.DocumentIdentity(doc.doctype, doc.name) for doc in (root, receipt, future)},
				)
				self.assertNotIn(self.scope.DocumentIdentity("Purchase Receipt", "OTHER"), result.sources)
				self.assertIn(
					self.scope.StockPair("B", "W2"), result.pairs
				)  # whole actual PO footprint, not stock seed
		root.items[0].purchase_receipt = "PR"
		root.items[0].pr_detail = "R"
		root.update_stock = 0
		with patch.object(frappe.db, "get_single_value", return_value=1):
			result = self.scope.collect_cancellation_scope(root)
		self.assertEqual(
			set(result.vouchers),
			{self.scope.DocumentIdentity(doc.doctype, doc.name) for doc in (root, receipt, future)},
		)
		self.assertFalse(any(query[0] == "Purchase Receipt Item" for query in self.queries))

	def test_invoice_repost_setting_metadata_missing_is_not_treated_as_disabled(self):
		root = self.add(document("Purchase Invoice", "PI"))
		with patch.object(frappe, "get_meta", return_value=Mock(has_field=lambda field: False)):
			with self.assertRaises(frappe.ValidationError):
				self.scope.collect_cancellation_scope(root)

	def test_ordinary_stock_entry_external_writers_refuse_without_blocking_future_valuation(self):
		for field in ("work_order", "job_card", "project", "asset_repair", "pick_list", "source_stock_entry"):
			with self.subTest(field=field):
				root = document("Stock Entry", "SE", purpose="Material Transfer", **{field: "UNADAPTED"})
				with self.assertRaises(frappe.ValidationError):
					self.scope.collect_document_scope(root)
		root = document(
			"Stock Entry",
			"SE",
			purpose="Material Transfer",
			inspection_required=1,
			items=[item("SEI", doctype="Stock Entry Detail", quality_inspection="QI")],
		)
		with self.assertRaises(frappe.ValidationError):
			self.scope.collect_document_scope(root)
		root = self.add(document("Purchase Receipt", "PR", items=[item("R")]))
		self.sle(root, root.items[0])
		future = self.add(
			document(
				"Stock Entry",
				"SE",
				purpose="Material Transfer",
				posting_date="2026-01-11",
				work_order="WO",
				job_card="JC",
				project="PROJECT",
				asset_repair="AR",
				pick_list="PICK",
				source_stock_entry="SOURCE",
				inspection_required=1,
				items=[item("SEI", doctype="Stock Entry Detail", quality_inspection="QI", s_warehouse="W1")],
			)
		)
		self.sle(future, future.items[0], "W1")
		result = self.scope.collect_cancellation_scope(root)
		self.assertEqual({doc.name for doc in result.vouchers}, {"PR", "SE"})

	def test_future_manufacture_consumption_cost_path_refuses_only_actual_stock_rate_dependency(self):
		root = self.add(document("Purchase Receipt", "PR", items=[item("R")]))
		self.sle(root, root.items[0])
		future = self.add(
			document(
				"Stock Entry",
				"MAN",
				purpose="Manufacture",
				work_order="WO",
				posting_date="2026-01-11",
				items=[
					item("RM", doctype="Stock Entry Detail", warehouse=None, s_warehouse="W1"),
					item(
						"FG",
						"D",
						doctype="Stock Entry Detail",
						warehouse=None,
						t_warehouse="W3",
						is_finished_item=1,
					),
				],
			)
		)
		self.sle(future, future.items[0], "W1", actual_qty=-1)
		self.sle(future, future.items[1], "W3")
		with (
			patch.object(frappe.db, "get_single_value", return_value=1),
			patch.object(frappe.db, "exists", side_effect=lambda dt, filters=None: dt == "Stock Entry"),
		):
			with self.assertRaises(frappe.ValidationError):
				self.scope.collect_cancellation_scope(root)
		result = self.scope.collect_cancellation_scope(root)  # configuration off does not invent WO writes
		self.assertEqual({doc.name for doc in result.vouchers}, {"PR", "MAN"})

	def test_positive_manufacture_finished_sle_actual_recalculation_also_checks_cost_path(self):
		root = self.add(document("Purchase Receipt", "PR", items=[item("R", "D", "W3")]))
		self.sle(root, root.items[0])
		future = self.add(
			document(
				"Stock Entry",
				"MAN",
				purpose="Manufacture",
				work_order="WO",
				posting_date="2026-01-11",
				items=[
					item("RM", doctype="Stock Entry Detail", warehouse=None, s_warehouse="W1"),
					item(
						"FG",
						"D",
						doctype="Stock Entry Detail",
						warehouse=None,
						t_warehouse="W3",
						is_finished_item=1,
					),
				],
			)
		)
		self.sle(future, future.items[0], "W1", actual_qty=-1)
		finished = self.sle(future, future.items[1], "W3", recalculate_rate=1)
		with (
			patch.object(frappe.db, "get_single_value", return_value=1),
			patch.object(frappe.db, "exists", side_effect=lambda dt, filters=None: dt == "Stock Entry"),
		):
			with self.assertRaises(frappe.ValidationError):
				self.scope.collect_cancellation_scope(root)
			finished.recalculate_rate = 0
			result = self.scope.collect_cancellation_scope(root)
		self.assertEqual({doc.name for doc in result.vouchers}, {"PR", "MAN"})
