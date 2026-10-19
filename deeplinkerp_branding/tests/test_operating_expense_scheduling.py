"""Background synchronization retains committed source pages and reports failures."""

import importlib.util
import sys
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from psycopg import OperationalError, errors

from deeplinkerp_branding.services import operating_expenses as service
from deeplinkerp_branding.services import operating_oa_source as oa
from deeplinkerp_branding.tests._purchase_test_support import install_native_throw


class OperatingSourceReadRetryTests(unittest.TestCase):
    UNTIL = "2026-10-06T00:00:00Z"
    PRIVATE = "private host password and source payload"

    def timeout(self, message="canceling statement due to statement timeout"):
        return errors.QueryCanceled(self.PRIVATE, info={ord("M"): message.encode(), ord("C"): b"57014"})

    def reader(self, plans):
        events, queries = [], []
        rows = [{"corp_id": "private-corp", "process_instance_id": "instance-1"},
                {"corp_id": "private-corp", "process_instance_id": "instance-2"}]

        @contextmanager
        def connection():
            attempt = sum(event[0] == "connect" for event in events)
            events.append(("connect", attempt))
            plan = plans[attempt]
            if plan.get("phase") == "connect":
                raise plan["error"]

            class Query:
                def execute(self, sql, params):
                    queries.append((sql, params))
                    if plan.get("phase") == "execute":
                        raise plan["error"]

                def fetchall(self):
                    if plan.get("phase") == "fetch":
                        raise plan["error"]
                    if plan.get("phase") == "partial_fetch":
                        def partial():
                            yield {"corp_id": "discarded-partial", "process_instance_id": "wrong-instance"}
                            raise plan["error"]
                        return partial()
                    return rows

            @contextmanager
            def cursor():
                yield Query()

            events.append(("open", attempt))
            try:
                yield SimpleNamespace(cursor=cursor)
            finally:
                events.append(("close", attempt))

        return connection, events, queries, rows

    def test_success_opens_one_connection_and_keeps_existing_page_contract(self):
        factory, events, queries, rows = self.reader([{}])
        result, cursor = oa.read_page(factory, 1, self.UNTIL)
        self.assertEqual(result, rows[:1])
        self.assertEqual(oa.decode_cursor(cursor), (self.UNTIL, "private-corp", "instance-1"))
        self.assertEqual(events, [("connect", 0), ("open", 0), ("close", 0)])
        self.assertEqual(len(queries), 1)

    def test_connection_timeout_reopens_once_and_returns_complete_page(self):
        failure = errors.ConnectionTimeout(self.PRIVATE)
        factory, events, queries, rows = self.reader([{"phase": "connect", "error": failure}, {}])
        with self.assertLogs(oa.__name__, level="WARNING"):
            result, cursor = oa.read_page(factory, 1, self.UNTIL)
        self.assertEqual(result, rows[:1])
        self.assertEqual(oa.decode_cursor(cursor), (self.UNTIL, "private-corp", "instance-1"))
        self.assertEqual(events, [("connect", 0), ("connect", 1), ("open", 1), ("close", 1)])
        self.assertEqual(len(queries), 1)

    def test_statement_timeout_restarts_complete_read_without_partial_rows_or_changed_snapshot(self):
        from deeplinkerp_branding.services.purchase_source_contract import PROCESS_CODES as purchase_codes
        identity = {"corp_id": "private-corp", "process_instance_id": "instance-1"}
        after = oa.encode_cursor(self.UNTIL, "earlier-corp", "earlier-instance")
        for phase in ("execute", "fetch", "partial_fetch"):
            for codes in (oa.PROCESS_CODES, purchase_codes):
                with self.subTest(phase=phase, codes=codes):
                    factory, events, queries, rows = self.reader([{"phase": phase, "error": self.timeout()}, {}])
                    with self.assertLogs(oa.__name__, level="WARNING"):
                        result, cursor = oa.read_page(factory, 1, self.UNTIL, after, identity, process_codes=codes)
                    self.assertEqual(result, rows[:1])
                    self.assertEqual(oa.decode_cursor(cursor), (self.UNTIL, "private-corp", "instance-1"))
                    self.assertEqual(events, [("connect", 0), ("open", 0), ("close", 0),
                                              ("connect", 1), ("open", 1), ("close", 1)])
                    self.assertEqual(len(queries), 2)
                    self.assertEqual(queries[0], queries[1])
                    sql, params = queries[1]
                    self.assertIsInstance(params, tuple)
                    self.assertEqual(params[0], list(codes))
                    self.assertEqual(params[3], oa.timestamp(self.UNTIL))
                    self.assertEqual(params[4:], ("earlier-corp", "earlier-instance", "private-corp", "instance-1", 2))
                    self.assertIn("updated_at <= %s", sql)
                    self.assertIn("(corp_id, process_instance_id) > (%s, %s)", sql)
                    self.assertIn("counts AS MATERIALIZED", sql)
                    self.assertIn("GROUP BY business_id", sql)
                    self.assertEqual("instance_counts AS MATERIALIZED" in sql, codes == purchase_codes)

    def test_second_retryable_failure_propagates_native_exception_without_third_connection(self):
        for phase, failure in (("connect", errors.ConnectionTimeout(self.PRIVATE)),
                               ("execute", self.timeout()), ("fetch", self.timeout())):
            with self.subTest(phase=phase):
                first = errors.ConnectionTimeout(self.PRIVATE) if phase == "connect" else self.timeout()
                factory, events, queries, _ = self.reader([
                    {"phase": phase, "error": first}, {"phase": phase, "error": failure},
                ])
                with self.assertLogs(oa.__name__, level="WARNING") as logged:
                    with self.assertRaises(type(failure)) as raised:
                        oa.read_page(factory, 1, self.UNTIL)
                self.assertIs(raised.exception, failure)
                self.assertEqual(sum(event[0] == "connect" for event in events), 2)
                self.assertEqual(len(logged.records), 1)
                self.assertEqual(len(queries), 0 if phase == "connect" else 2)

    def test_only_exact_driver_timeout_diagnostics_are_retryable(self):
        wrong_state = self.timeout()
        wrong_state.sqlstate = "57013"
        failures = [self.timeout("canceling statement due to user request"), self.timeout(""),
            self.timeout("Canceling statement due to statement timeout"), wrong_state,
            errors.QueryCanceled("canceling statement due to statement timeout"),
            errors.AdminShutdown(self.PRIVATE), errors.InsufficientPrivilege(self.PRIVATE),
            errors.InvalidPassword(self.PRIVATE), errors.UndefinedTable(self.PRIVATE), errors.DataException(self.PRIVATE),
            OperationalError(self.PRIVATE), TimeoutError(self.PRIVATE), ValueError("duplicate source identity"),
            type("ConnectionTimeout", (Exception,), {})(self.PRIVATE), KeyboardInterrupt()]
        for failure in failures:
            with self.subTest(error=type(failure), state=getattr(failure, "sqlstate", None)):
                factory, events, _, _ = self.reader([{"phase": "execute", "error": failure}])
                with self.assertNoLogs(oa.__name__, level="WARNING"):
                    with self.assertRaises(type(failure)) as raised:
                        oa.read_page(factory, 1, self.UNTIL)
                self.assertIs(raised.exception, failure)
                self.assertEqual(events, [("connect", 0), ("open", 0), ("close", 0)])

    def test_retry_log_contains_only_fixed_event_allowlisted_error_and_attempt(self):
        for failure in (errors.ConnectionTimeout(self.PRIVATE), self.timeout()):
            with self.subTest(error=type(failure)):
                factory, _, _, _ = self.reader([{"phase": "execute", "error": failure}, {}])
                with self.assertLogs(oa.__name__, level="WARNING") as logged:
                    oa.read_page(factory, 1, self.UNTIL)
                self.assertEqual(len(logged.records), 1)
                message = logged.records[0].getMessage()
                self.assertIn("oa_read_page_retry", message)
                self.assertIn(type(failure).__name__, message)
                self.assertIn("attempt=1", message)
                for private in (self.PRIVATE, "private-corp", "instance-1", "SELECT", "statement timeout"):
                    self.assertNotIn(private, message)
                self.assertIsNone(logged.records[0].exc_info)

    def test_module_import_and_success_work_without_optional_driver_and_keep_original_failures(self):
        spec = importlib.util.spec_from_file_location("deeplinkerp_branding.services.oa_without_driver", oa.__file__)
        without_driver = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"psycopg": None, "psycopg.errors": None}):
            spec.loader.exec_module(without_driver)
            factory, events, _, rows = self.reader([{}])
            self.assertEqual(without_driver.read_page(factory, 1, self.UNTIL)[0], rows[:1])
            failure = ValueError("source validation failed")
            factory, events, _, _ = self.reader([{"phase": "execute", "error": failure}])
            with self.assertRaises(ValueError) as raised:
                without_driver.read_page(factory, 1, self.UNTIL)
            self.assertIs(raised.exception, failure)
            self.assertEqual(sum(event[0] == "connect" for event in events), 1)


class OperatingExpenseSchedulingTests(unittest.TestCase):
    def setUp(self):
        self.db = Mock()
        self.settings = SimpleNamespace(enabled=1, cursor="completed-page", last_error=None)
        self.db.set_single_value.side_effect = lambda doctype, field, value: setattr(self.settings, field, value)
        patches = [patch.object(frappe, "db", self.db),
                   patch.object(frappe, "flags", frappe._dict()),
                   patch.object(service, "_settings", return_value=self.settings),
                   patch.object(service, "_save")]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_failure_is_durable_sanitized_and_propagates(self):
        with patch.object(service, "_sync_page", side_effect=ValueError("Bearer secret; source payload")):
            with self.assertRaisesRegex(RuntimeError, "同步失败"):
                service.scheduled_sync()
        self.assertEqual(self.settings.last_error, "同步失败，请管理员重试")
        self.assertEqual(self.settings.cursor, "completed-page")
        self.db.rollback.assert_called_once_with()
        self.db.commit.assert_called_once_with()

    def test_all_successful_pages_are_committed_before_next_page(self):
        events = []
        pages = iter([{"end": False, "count": 500}, {"end": False, "count": 500}, {"end": True, "count": 1}])
        def page():
            events.append("page")
            return next(pages)
        self.db.commit.side_effect = lambda: events.append("commit")
        with patch.object(service, "_sync_page", side_effect=page) as sync:
            service.scheduled_sync()
        self.assertEqual(sync.call_count, 3)
        self.assertEqual(events, ["page", "commit"] * 3)

    def test_later_failure_rolls_back_only_unfinished_page(self):
        with patch.object(service, "_sync_page", side_effect=[{"end": False}, TimeoutError("remote token")]):
            with self.assertRaisesRegex(RuntimeError, "同步失败"):
                service.scheduled_sync()
        self.assertEqual(self.db.commit.call_count, 2)  # Successful page, then safe error.
        self.db.rollback.assert_called_once_with()
        self.assertEqual(self.settings.cursor, "completed-page")

    def test_disabled_flag_skips_background_sync(self):
        self.settings.enabled = 0
        with patch.object(service, "_sync_page") as sync:
            service.scheduled_sync()
        sync.assert_not_called()
        self.db.commit.assert_not_called()

    def test_manual_sync_still_handles_one_page_without_commit(self):
        with patch.object(service, "_manager"), patch.object(service, "_sync_page", return_value={"end": False}) as sync:
            self.assertEqual(service.sync_operating_expenses(), {"end": False})
        sync.assert_called_once_with()
        self.db.commit.assert_not_called()

    def test_failure_writes_only_error_field_without_resaving_a_stale_cursor(self):
        with patch.object(service, "_sync_page", side_effect=ValueError("secret")), patch.object(service, "_save") as save:
            with self.assertRaises(RuntimeError):
                service.scheduled_sync()
        save.assert_not_called()
        self.db.set_single_value.assert_called_once_with(service.SETTINGS, "last_error", "同步失败，请管理员重试")


class OperatingJournalCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.db = Mock()
        self.db.exists.return_value = False
        guard = patch.object(frappe, "db", self.db)
        guard.start()
        self.addCleanup(guard.stop)
        install_native_throw(self)

    def journal(self, *, new=False, **fields):
        return frappe._dict(name="JE-COMPATIBILITY", is_new=lambda: new, **fields)

    def test_missing_module_leaves_new_and_existing_native_journals_untouched(self):
        self.db.get_value.side_effect = RuntimeError("Optional module table/column query")
        for new in (False, True):
            with self.subTest(new=new):
                journal = self.journal(new=new)
                before = dict(journal)
                service.validate_operating_journal(journal)
                self.assertEqual(dict(journal), before)
        self.db.get_value.assert_not_called()

    def test_missing_module_rejects_any_forged_operating_association(self):
        self.db.get_value.side_effect = RuntimeError("Optional module table/column query")
        for field in ("custom_operating_event_key", "custom_operating_source", "custom_operating_fingerprint", "custom_operating_recognition"):
            for new in (False, True):
                with self.subTest(field=field, new=new), self.assertRaisesRegex(frappe.ValidationError, "未启用运营费用"):
                    service.validate_operating_journal(self.journal(new=new, **{field: "forged"}))
        self.db.get_value.assert_not_called()

    def test_installed_module_still_protects_removal_of_existing_event_key(self):
        self.db.exists.return_value = True
        self.db.get_value.return_value = "existing-event"
        with self.assertRaisesRegex(frappe.ValidationError, "事件关联不可移除或修改"):
            service.validate_operating_journal(self.journal())

    def test_installed_module_still_checks_reverse_associations_without_event_key(self):
        self.db.exists.return_value = True
        journal = self.journal()
        self.db.get_value.side_effect = [None, frappe._dict(journal_entry=journal.name, operation="payment")]
        with self.assertRaisesRegex(frappe.ValidationError, "事件关联不可移除或修改"):
            service.validate_operating_journal(journal)
        self.assertEqual(self.db.get_value.call_args.args[:2], (service.EVENT, {"journal_entry": journal.name}))
