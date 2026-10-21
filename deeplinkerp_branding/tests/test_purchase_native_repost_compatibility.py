"""Exercise audited native installation without a site or business writes."""

import subprocess
import sys
import unittest
from textwrap import dedent


class NativeRepostCompatibilityTests(unittest.TestCase):
	def run_isolated(self, body):
		# Installation mutates captured native function objects. Each scenario
		# needs a fresh interpreter rather than leaving adapters in other tests.
		setup = dedent("""\
			from pathlib import Path
			from unittest.mock import Mock, patch
			import frappe
			from erpnext.accounts import general_ledger as gl
			from erpnext.stock.doctype.repost_item_valuation import repost_item_valuation as riv
			from deeplinkerp_branding.services import purchase_native_repost as native
			from deeplinkerp_branding.tests._purchase_test_support import native_throw
		""")
		result = subprocess.run(
			[sys.executable, "-c", setup + dedent(body)], capture_output=True, text=True, timeout=30,
		)
		self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

	def test_reviewed_native_sources_install_idempotently_without_changing_reversal(self):
		self.run_isolated("""\
			captured_repost = riv.repost
			captured_toggle = gl.toggle_debit_credit_if_negative
			reverse_code = gl.make_reverse_gl_entries.__code__
			with patch.object(frappe, 'throw', side_effect=native_throw), patch.object(frappe, 'db', Mock()) as db:
			    assert native.install() is riv
			    assert native.install() is riv
			    assert riv.repost is captured_repost
			    assert gl.toggle_debit_credit_if_negative is captured_toggle
			    assert gl.make_reverse_gl_entries.__code__ is reverse_code
			    db.sql.assert_not_called()
			    db.commit.assert_not_called()
		""")

	def test_unreviewed_native_source_remains_rejected(self):
		self.run_isolated("""\
			read_bytes = Path.read_bytes
			gl_path = Path(gl.__file__)
			def altered_source(path):
			    source = read_bytes(path)
			    return source + bytes([10]) + b'# unreviewed change' if path == gl_path else source
			with patch.object(frappe, 'throw', side_effect=native_throw), patch.object(Path, 'read_bytes', altered_source):
			    try:
			        native.install()
			    except frappe.ValidationError as error:
			        assert error.purchase_error_id == 'purchase_reversal_native_version_changed'
			    else:
			        raise AssertionError('Unreviewed native source was accepted')
		""")

	def test_runtime_callable_changes_remain_rejected(self):
		self.run_isolated("""\
			def replacement(gl_map):
			    return gl_map
			gl.toggle_debit_credit_if_negative.__code__ = replacement.__code__
			with patch.object(frappe, 'throw', side_effect=native_throw):
			    try:
			        native.install()
			    except frappe.ValidationError as error:
			        assert error.purchase_error_id == 'purchase_reversal_native_callable_changed'
			    else:
			        raise AssertionError('Changed native callable was accepted')
		""")
