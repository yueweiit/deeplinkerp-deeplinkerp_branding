"""Reporting JSON projections must remain inside the native list permission scope."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class OperatingBudgetReportTest(unittest.TestCase):
    def test_only_permitted_parent_names_are_read_in_bounded_batches(self):
        rows = [{"name": "allowed-" + str(i), "amount": "100"} for i in range(501)]
        settings = SimpleNamespace(SOURCE="Operating Expense Source", _require_fields=Mock(),
                                   _list_rows=Mock(return_value=rows), _settings=Mock(return_value={}),
                                   sync_config=SimpleNamespace(templates=lambda settings: [
                                       {"process_code": "code", "template_name": "Configured name"}]))
        class Record(dict):
            __getattr__ = dict.__getitem__
        def query(sql, params, **kwargs):
            self.assertIn("JSON_VALID", sql)
            self.assertIn("WHERE name IN %(names)s", sql)
            self.assertNotIn("SELECT *", sql)
            return [Record(name=name, oa_identity=json.dumps({"corp_id": "corp"}),
                           reporting=json.dumps({"version": 1, "process_code": "code"})) for name in params["names"]]
        db = SimpleNamespace(sql=Mock(side_effect=query))
        module_name = "deeplinkerp_branding.services.operating_budget_report_test_target"
        path = Path(__file__).parents[1] / "deeplinkerp_branding/services/operating_budget_report.py"
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"frappe": SimpleNamespace(db=db),
                "deeplinkerp_branding.services.operating_expenses": settings}):
            spec.loader.exec_module(module)
            result = module.read_rows("2026-11-30")
        settings._require_fields.assert_called_once()
        settings._list_rows.assert_called_once_with({"date_to": "2026-11-30"})
        self.assertEqual([len(call.args[1]["names"]) for call in db.sql.call_args_list], [500, 1])
        self.assertEqual({row["name"] for row in result}, {row["name"] for row in rows})
        self.assertEqual(result[0]["reporting"], {"version": 1, "process_code": "code"})
        self.assertEqual(result[0]["process_name"], "Configured name")
        self.assertNotIn("source_json", result[0])
