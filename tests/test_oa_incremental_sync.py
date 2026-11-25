"""Incremental page transactions and archive protocol, with no external IO."""
import json
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import frappe
import pytest

from deeplinkerp_branding.services import mexico_expenses as mx, oa_sync_policy as policy, operating_oa_http as http
from test_mexico_expense_templates import sample

START = datetime(2026, 11, 25, 1, tzinfo=timezone.utc)


@pytest.fixture
def runtime(monkeypatch):
    template, row = sample()
    state = SimpleNamespace(settings=frappe._dict(enabled=1, templates=[frappe._dict(template)],
        business_timezone="Asia/Shanghai", config_version="config-v1", mapping_version="mapping-v1"),
        rows={}, writes=[], jobs=[], now=START, row=row, reader=Mock(), savepoint=None)
    def savepoint(*args):
        state.savepoint = deepcopy((state.settings, state.rows))
    def rollback(**kwargs):
        state.settings, state.rows = deepcopy(state.savepoint)
    def upsert(row, settings):
        state.writes.append(deepcopy(row))
        state.rows[mx.contract.digest([row["corp_id"], row["process_instance_id"]])] = json.dumps(row)
    def read(doctype, *, filters, **kwargs):
        assert doctype == mx.SOURCE
        return [frappe._dict(name=key, raw_json=raw) for key, raw in state.rows.items() if key in filters["name"][1]]
    monkeypatch.setattr(frappe, "db", SimpleNamespace(savepoint=savepoint, rollback=rollback, has_column=lambda *a: True))
    monkeypatch.setattr(frappe, "cache", SimpleNamespace(lock=lambda *a, **kw: nullcontext()))
    monkeypatch.setattr(frappe, "local", SimpleNamespace(site="isolated"))
    monkeypatch.setattr(frappe, "get_all", read)
    monkeypatch.setattr(frappe, "enqueue", lambda *a, **kw: state.jobs.append(kw))
    monkeypatch.setattr(frappe, "log_error", lambda **kw: None)
    monkeypatch.setattr(frappe, "throw", lambda text: (_ for _ in ()).throw(ValueError(text)))
    monkeypatch.setattr(mx, "_", lambda text: text)
    monkeypatch.setattr(mx, "_settings", lambda **kw: state.settings)
    monkeypatch.setattr(mx, "_save", lambda doc: None)
    monkeypatch.setattr(mx, "_upsert", upsert)
    monkeypatch.setattr(mx, "now_datetime", lambda: state.now.replace(tzinfo=None))
    monkeypatch.setattr(policy, "utc_now", lambda: state.now)
    monkeypatch.setattr(http, "configuration", lambda: ("url", [row["corp_id"]]))
    monkeypatch.setattr(http, "read_template_page", state.reader)
    def run(rows, *, more=False, until=None):
        state.reader.return_value = (rows, {"syncUntil": until or state.now.isoformat(),
            "hasMore": more, "nextCursor": "next" if more else None})
        return mx.sync_page(template_key=mx._template_key(state.settings.templates[0]))
    state.run = run
    return state


def stored(state):
    return policy.decode(state.settings.templates[0].sync_state)


def test_full_then_delta_skips_unchanged_and_catches_final_transition(runtime):
    s = runtime
    assert s.run([s.row])["processed"] == 1
    assert "updated_after" not in s.reader.call_args.kwargs
    assert stored(s)["reconciled_at"] == START.isoformat()
    s.now += timedelta(minutes=5)
    assert s.run([{**s.row, "updated_at": s.now.isoformat()}])["processed"] == 0
    assert len(s.writes) == 1
    assert s.reader.call_args.kwargs["updated_after"] == (START - timedelta(minutes=10)).isoformat()
    s.now += timedelta(minutes=5)
    ended = deepcopy(s.row)
    ended.update(status="COMPLETED", result="AGREE")
    ended["raw_payload"].update(status="COMPLETED", result="AGREE")
    assert s.run([ended])["processed"] == 1
    assert s.writes[-1]["status"] == "COMPLETED"
    assert stored(s)["watermark"] == s.now.isoformat()
    assert stored(s)["reconciled_at"] == START.isoformat()


def test_changes_inside_running_approval_are_saved_even_without_status_change(runtime):
    s = runtime
    s.run([s.row])
    changed = deepcopy(s.row)
    changed["raw_payload"]["formComponentValues"][1]["value"] = "200"
    s.now += timedelta(minutes=5)
    assert s.run([changed])["processed"] == 1
    assert s.writes[-1]["status"] == s.row["status"]
    assert s.writes[-1]["raw_payload"]["formComponentValues"][1]["value"] == "200"


def test_first_page_never_advances_completed_watermark_and_all_pages_keep_lower_bound(runtime):
    s = runtime
    s.run([s.row])
    s.now += timedelta(minutes=5)
    until = s.now.isoformat()
    s.run([{**s.row, "process_instance_id": "new"}], more=True)
    first = s.reader.call_args.kwargs
    assert stored(s)["watermark"] == START.isoformat()
    assert stored(s)["active"]["mode"] == "incremental"
    s.now += timedelta(minutes=5)
    s.run([], until=until)
    assert s.reader.call_args.kwargs == {**first, "cursor": "next"}
    assert stored(s)["watermark"] == until
    assert "active" not in stored(s)


def test_initial_empty_scan_is_a_valid_full_baseline(runtime):
    s = runtime
    s.run([])
    s.now += timedelta(minutes=5)
    assert s.run([s.row])["processed"] == 1
    assert "updated_after" in s.reader.call_args.kwargs


def test_failed_page_rolls_back_rows_and_watermark_then_retries(runtime, monkeypatch):
    s = runtime
    s.run([s.row])
    before = deepcopy(stored(s))
    original = mx._upsert
    def fail(row, settings):
        original(row, settings)
        raise ValueError("write failed")
    monkeypatch.setattr(mx, "_upsert", fail)
    s.now += timedelta(minutes=5)
    assert not s.run([{**s.row, "process_instance_id": "new"}])["ok"]
    assert stored(s) == before
    assert len(s.rows) == 1
    monkeypatch.setattr(mx, "_upsert", original)
    assert s.run([{**s.row, "process_instance_id": "new"}])["ok"]
    assert len(s.rows) == 2 and stored(s)["watermark"] == s.now.isoformat()


def test_expired_cursor_restarts_from_last_completed_watermark(runtime):
    s = runtime
    s.run([s.row])
    s.now += timedelta(minutes=5)
    s.run([], more=True)
    s.reader.side_effect = http.OAInvalidCursor("expired")
    assert not s.run([])["ok"]
    assert stored(s)["watermark"] == START.isoformat()
    assert "active" not in stored(s)
    assert s.settings.templates[0].cursor is None
    s.reader.side_effect = None
    s.run([])
    assert s.reader.call_args.kwargs["cursor"] is None
    assert s.reader.call_args.kwargs["updated_after"] == (START - timedelta(minutes=10)).isoformat()


def test_daily_reconciliation_and_configuration_change_start_full_scans(runtime):
    s = runtime
    s.run([s.row])
    s.now += timedelta(days=1)
    assert s.run([s.row])["processed"] == 1
    assert "updated_after" not in s.reader.call_args.kwargs
    assert stored(s)["reconciled_at"] == s.now.isoformat()
    s.settings.config_version = "config-v2"
    s.now += timedelta(minutes=5)
    s.run([s.row])
    assert "updated_after" not in s.reader.call_args.kwargs


def test_config_change_discards_cursor_from_other_query_scope(runtime):
    s = runtime
    s.run([s.row])
    s.now += timedelta(minutes=5)
    s.run([], more=True)
    s.settings.business_timezone = "America/Mexico_City"
    s.run([s.row])
    assert s.reader.call_args.kwargs == {"cursor": None}


def test_full_failure_does_not_postpone_reconciliation(runtime):
    s = runtime
    s.run([s.row])
    s.now += timedelta(days=1)
    s.reader.side_effect = ValueError("offline")
    assert not s.run([])["ok"]
    assert stored(s)["reconciled_at"] == START.isoformat()
    s.reader.side_effect = None
    s.run([])
    assert "updated_after" not in s.reader.call_args.kwargs


def test_new_configuration_cannot_inherit_old_watermark_after_partial_full_retry(runtime):
    s = runtime
    s.run([s.row])
    s.now += timedelta(minutes=5)
    s.settings.config_version = "new-config"
    s.run([s.row], more=True)
    assert not stored(s).get("watermark")
    s.reader.side_effect = http.OAInvalidCursor("expired")
    assert not s.run([])["ok"]
    s.reader.side_effect = None
    s.run([s.row])
    assert "updated_after" not in s.reader.call_args.kwargs


def test_disabled_template_is_not_read(runtime):
    s = runtime
    s.settings.templates[0].enabled = 0
    assert s.run([s.row])["disabled"]
    s.reader.assert_not_called()


def test_template_reader_transmits_same_incremental_bound_on_following_pages(monkeypatch):
    page = Mock(return_value=([], {"hasMore": False}))
    monkeypatch.setattr(http, "_page", page)
    for cursor in (None, "next"):
        http.read_template_page("url", "corp", "PROC-test", updated_after=START.isoformat(), cursor=cursor)
        params = page.call_args.args[2]
        assert params["updatedAfter"] == START.isoformat()
        assert params.get("cursor") == cursor
    with pytest.raises(ValueError):
        http.read_template_page("url", "corp", "PROC-test", updated_after="2026-11-25 01:00:00")


@pytest.mark.parametrize("hours,expected", [(23.99, "incremental"), (24, "full"), (25, "full")])
def test_daily_boundary_uses_last_full_not_last_delta(hours, expected):
    state = policy.completed({}, "scope", {"mode": "full"}, START.isoformat())
    state["watermark"] = (START + timedelta(hours=23)).isoformat()
    assert policy.plan(state, "scope", now=START + timedelta(hours=hours))["mode"] == expected
