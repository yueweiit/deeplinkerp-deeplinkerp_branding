"""Bidirectional allocations reconcile once, with strict ownership and permissions."""
from copy import deepcopy
from types import SimpleNamespace
import json

import frappe
import pytest

from deeplinkerp_branding.services import regional_expense_contract as c
from deeplinkerp_branding.services import regional_expenses as s
from deeplinkerp_branding.services import mexico_expenses as mx
from deeplinkerp_branding.services import operating_expense_contract as money


def sample():
    source = dict(source_key="source", corp_id="corp", process_code="PROC-test", process_instance_id="approval",
        total_amount="100.00", currency="USD", approval_status="approved", cn_application_date="2026-11-02",
        approval_type="operating", approval_no="NO-1", applicant="Applicant")
    lines = [dict(line_key="original", source="source", source_entity="Department A", entity_key="entity",
        entity_kind="department", description="Services", amount="100.00", currency="USD", amount_raw="100",
        category="services", subcategory="legal_accounting", classification_path='[{"label":"Services"}]',
        table_id="table", source_row_id="row", base_issues=[], row_fingerprint="facts", period_month="2026-12-01")]
    entries = [dict(line_key="original", region="cn", company="China A", amount="30.00", management_category="service"),
        dict(line_key="original", region="mx", company="Mexico B", amount="70.00", management_category="")]
    return source, lines, entries


@pytest.fixture(autouse=True)
def translations(monkeypatch):
    monkeypatch.setattr(s, "_", lambda text: text)
    monkeypatch.setattr(money, "_", lambda text: text)
    monkeypatch.setattr(frappe, "throw", lambda text, *args: (_ for _ in ()).throw(ValueError(text)))


def test_installed_allocation_revision_uses_frappe_query_parser_and_tracks_changes(monkeypatch):
    import sqlite3
    from datetime import datetime
    from pypika import Query, Table
    from frappe.database import query as frappe_query
    from budget.services import cn_approval_sync as sync

    # Do not replace the aggregate result: compile the actual requested fields
    # with Frappe's validator and execute them against a real in-memory table.
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE "tabRegional Expense Allocation" (name TEXT, modified TEXT)')
    def get_all(doctype, *, fields, order_by=None):
        engine = frappe_query.Engine()
        engine.doctype, engine.table = doctype, Table("tab" + doctype)
        engine.function_aliases = set()
        engine.apply_permissions = False  # frappe.get_all's existing contract
        query = Query.from_(engine.table).select(*engine.parse_fields(fields))
        return [frappe._dict(dict(row)) for row in db.execute(query.get_sql()).fetchall()]
    monkeypatch.setattr(s, "installed", lambda: True)
    monkeypatch.setattr(frappe_query, "_", lambda value: value)
    monkeypatch.setattr(frappe, "get_all", get_all)
    monkeypatch.setattr(sync.settings, "sync_options", lambda: {"enabled": False})
    monkeypatch.setattr(sync, "now_datetime", lambda: datetime(2026, 11, 23))
    try:
        empty = s.revision()
        assert empty and sync.status()["allocation_revision"] == empty
        db.execute('INSERT INTO "tabRegional Expense Allocation" VALUES (?, ?)', ("a", "2026-11-23 10:00:00"))
        inserted = s.revision()
        assert inserted != empty and sync.status()["allocation_revision"] == inserted
        db.execute('UPDATE "tabRegional Expense Allocation" SET modified=? WHERE name=?', ("2026-11-23 11:00:00", "a"))
        updated = s.revision()
        assert updated != inserted
        db.execute('INSERT INTO "tabRegional Expense Allocation" VALUES (?, ?)', ("b", "2026-11-22 10:00:00"))
        assert s.revision() != updated
        assert sync.status()["allocation_revision"] == s.revision()
        db.execute('DELETE FROM "tabRegional Expense Allocation" WHERE name=?', ("b",))
        assert s.revision() == updated
    finally:
        db.close()


def test_split_preserves_exact_original_amounts_and_excludes_cn_from_mx():
    source, lines, entries = sample()
    validated = c.validate_allocations(entries, lines, {"China A": "China", "Mexico B": "Mexico"}, {"service"})
    result = c.allocated_lines(source, lines, validated, valid=True)
    assert [line["amount"] for line in result] == ["30.00", "70.00"]
    assert [line["expense_scope"] for line in result] == ["non_mx", "mx"]
    assert [line["company"] for line in result] == ["", "Mexico B"]
    assert [line["review_state"] for line in result] == ["excluded", "confirmed"]
    assert len({line["line_key"] for line in result}) == 2


@pytest.mark.parametrize("change", [
    {"amount": "31"}, {"amount": "-1"}, {"amount": "NaN"}, {"amount": "30.001"},
    {"amount": True}, {"line_key": "unknown"}, {"company": "Hidden"}, {"region": "unknown"},
    {"management_category": "unknown"}, {"region": "other"}, {"region": "pending"},
])
def test_rejects_unbalanced_invalid_and_unauthorized_allocations(change):
    _, lines, entries = sample()
    entries[0].update(change)
    with pytest.raises(ValueError):
        c.validate_allocations(entries, lines, {"China A": "China", "Mexico B": "Mexico"}, {"service"})


def test_mx_target_requires_mexican_company_and_never_uses_display_currency():
    _, lines, entries = sample()
    entries[1]["company"] = "China A"
    with pytest.raises(ValueError, match="authorized subsidiary"):
        c.validate_allocations(entries, lines, {"China A": "China"}, {"service"})


@pytest.mark.parametrize("issue", sorted(c.BLOCKING))
def test_bad_source_cannot_be_made_valid_by_allocating(issue):
    _, lines, entries = sample()
    lines[0]["base_issues"] = [issue]
    with pytest.raises(ValueError, match="Correct source"):
        c.validate_allocations(entries, lines, {"China A": "China", "Mexico B": "Mexico"}, {"service"})


def test_all_original_rows_must_be_allocated_independently():
    _, lines, entries = sample()
    lines.append({**lines[0], "line_key": "second", "amount": "10.00"})
    with pytest.raises(ValueError, match="each original"):
        c.validate_allocations(entries, lines, {"China A": "China", "Mexico B": "Mexico"}, {"service"})


def test_explicit_balanced_split_resolves_multiple_bearers_but_not_finance():
    source, lines, entries = sample()
    lines[0].update(base_issues=["allocation", "period"], period_month=None)
    clean = c.validate_allocations(entries, lines, {"China A": "China", "Mexico B": "Mexico"}, {"service"})
    result = c.allocated_lines(source, lines, clean, valid=True)
    assert "allocation" not in result[1]["issues"]
    assert result[1]["review_state"] == "needs_review"
    assert "period" in result[1]["issues"]


def test_changed_or_missing_source_never_retains_confirmed_amounts():
    source, lines, entries = sample()
    for current in (lines, [{**lines[0], "line_key": "replaced"}]):
        result = c.allocated_lines(source, current, entries, valid=False)
        assert all("amount" in line["base_issues"] and "stale" in line["issues"] for line in result)
        assert result[1]["review_state"] != "confirmed"


def test_workflow_changes_preserve_ownership_but_money_and_bearer_changes_do_not():
    source, lines, _ = sample()
    stamp = c.fingerprint(source, lines)
    assert c.fingerprint({**source, "approval_status": "withdrawn"}, [{**lines[0], "period_month": "2027-01-01"}]) == stamp
    for change in ({"amount": "101.00"}, {"source_entity": "Other"}, {"currency": "CNY"}, {"source_row_id": "changed"}):
        assert c.fingerprint(source, [{**lines[0], **change}]) != stamp


def make_record(origin="mx"):
    source, lines, entries = sample()
    record = frappe._dict(name="source", corp_id="corp", process_code="PROC-test", process_instance_id="approval",
        origin=origin, state="confirmed", modified="v1", facts_json=s.encode([source, lines]),
        allocations_json=s.encode(entries), reviewed_fingerprint=c.fingerprint(source, lines),
        review_note="Bearer confirmed", reviewed_by="Administrator", reviewed_at="2026-11-23",
        source_json=s.encode({"corp_id": "corp", "process_code": "PROC-test", "process_instance_id": "approval", "version": "owner"}),
        template_json='{"approval_type":"operating"}')
    return record, source, lines


def test_unified_projection_returns_each_split_once_and_respects_permissions(monkeypatch):
    record, _, _ = make_record()
    monkeypatch.setattr(s, "records", lambda: [record])
    ids, visible = s.management_projection({"companies": {"China A", "Mexico B"}}, include_legacy=False)
    assert ids == {("corp", "approval")}
    assert [row["amount"] for row in visible] == ["30.00", "70.00"]
    assert len({row["key"] for row in visible}) == 2
    assert [row["company"] for row in s.management_projection({"companies": {"Mexico B"}})[1]] == ["Mexico B"]


@pytest.mark.parametrize("label,mappings,expected", [
    ("Salaries", [], "salary"),
    ("Services", [{"kind": "operating", "source_path": ["Services"], "category": "service"}], "service"),
    ("Unknown item", [], ""),
])
def test_unified_mx_categories_use_current_rules_without_changing_cn_allocations(monkeypatch, label, mappings, expected):
    from budget.services import cn_approval_management_rules as rules
    record, source, lines = make_record()
    lines[0]["classification_path"] = s.encode([{"label": label}])
    record.facts_json = s.encode([source, lines])
    saved_allocations = record.allocations_json
    monkeypatch.setattr(s, "records", lambda: [record])
    configured = {**rules.defaults(), "category_mappings": mappings}
    _, visible = s.management_projection({"companies": {"China A", "Mexico B"}},
        include_legacy=False, management_rules=configured)
    cn, mx_line = visible
    assert cn["management_category"] == "service"
    assert mx_line["management_category"] == expected
    assert ("management_category" in mx_line["issues"]) is (not expected)
    assert mx_line["amount"] == "70.00"
    assert record.allocations_json == saved_allocations
    assert s.cn_projection({"companies": {"China A"}})[1][0]["management_category"] == "service"


@pytest.mark.parametrize("fields,expected", [([], "2026-10"),
    ([{"name": "申请日期", "value": "not-a-date"}], None),
    ([{"name": "申请日期", "value": ""}], None),
    ([{"name": "申请日期", "value": "2026-11-01"}, {"name": "申请日期", "value": "2026-10-01"}], None)])
@pytest.mark.parametrize("origin", ["cn", "mx"])
def test_submission_fallback_requires_missing_field_not_invalid_date(monkeypatch, fields, expected, origin):
    from deeplinkerp_branding.services import mexico_expenses as mx
    record, source, lines = make_record(origin)
    monkeypatch.setattr(mx, "_settings", lambda: frappe._dict(templates=[
        {"corp_id": record.corp_id, "process_code": record.process_code}]))
    source.update(cn_application_date=None, request_date="2026-10-31")
    record.facts_json=s.encode([source,lines])
    record.source_json=s.encode({"form_component_values":fields})
    monkeypatch.setattr(s,"records",lambda:[record])
    queries=[]
    def local_sources(doctype, **kwargs):
        queries.append(kwargs)
        return [record]
    monkeypatch.setattr(frappe,"get_all",local_sources)
    result=s.management_projection({"companies":{"Mexico B"}},include_legacy=False,submission_fallback=True)[1][0]
    assert result["month"] == expected
    assert len(queries) == 1
    assert s.cn_projection({"companies":{"China A"}})[1][0]["month"] is None


@pytest.mark.parametrize("template,expected", [
    (None, None),
    ({"corp_id": "another", "process_code": "PROC-test"}, None),
    ({"corp_id": "corp", "process_code": "PROC-another"}, None),
    ({"corp_id": "corp", "process_code": "PROC-test", "enabled": 0}, "2026-10"),
    ({"corp_id": "corp", "process_code": "PROC-test", "enabled": 1}, "2026-10"),
])
@pytest.mark.parametrize("submitted", [None, "2026-10-31"])
def test_cn_owned_submission_fallback_requires_exact_expense_template(monkeypatch, template, expected, submitted):
    from deeplinkerp_branding.services import mexico_expenses as mx
    record, source, lines = make_record("cn")
    source.update(cn_application_date=None, application_date_state="missing", request_date=submitted)
    record.facts_json = s.encode([source, lines])
    before = deepcopy(record)
    monkeypatch.setattr(s, "records", lambda: [record])
    monkeypatch.setattr(mx, "_settings", lambda: frappe._dict(templates=[template] if template else []))
    monkeypatch.setattr(frappe, "get_all", lambda *args, **kwargs: pytest.fail("date metadata is already available"))
    rows = s.management_projection({"companies": {"Mexico B"}}, include_legacy=False, submission_fallback=True)[1]
    assert len(rows) == 1 and rows[0]["company"] == "Mexico B"
    assert rows[0]["month"] == (expected if submitted else None)
    assert record == before  # No changes to manual decisions, ownership or stored facts.


@pytest.mark.parametrize("origin", ["cn", "mx"])
def test_cn_reads_both_origins_once_and_applies_company_permissions(monkeypatch, origin):
    record, _, _ = make_record(origin)
    monkeypatch.setattr(s, "records", lambda: [record])
    ids, visible = s.cn_projection({"companies": {"China A"}})
    assert ids == {("corp", "approval")}
    assert len(visible) == 1 and visible[0]["amount"] == "30.00"
    assert visible[0]["month"] == "2026-11" and visible[0]["currency"] == "USD"
    assert visible[0]["allocation_origin"] == origin
    assert s.cn_projection({"companies": {"Mexico B"}})[1] == []
    record.state = "stale"
    assert s.cn_projection({"companies": {"China A"}})[1][0]["amount"] is None


def test_cn_suppresses_duplicate_native_approval_but_keeps_budgets(monkeypatch):
    from budget.services import cn_approval_management as cn
    record, _, _ = make_record()
    monkeypatch.setattr(s, "records", lambda: [record])
    seen = []
    monkeypatch.setattr(cn, "analytical_lines", lambda rows, *_: seen.extend(rows) or [])
    native = {"corp_id": "corp", "process_instance_id": "approval", "kind": "operating"}
    budget = {**native, "kind": "budget"}
    result, snapshot = cn.with_regional_allocations({"rows": [native, budget], "has_snapshot": False}, {"companies": {"China A"}}, {})
    assert seen == [budget] and len(result) == 1 and snapshot["has_snapshot"] is True


@pytest.mark.parametrize("origin,caller", [("cn", "mx"), ("mx", "cn")])
def test_secondary_feed_cannot_replace_authoritative_facts(monkeypatch, origin, caller):
    record, source, lines = make_record(origin)
    monkeypatch.setattr(s, "record_for", lambda *a, **k: record)
    captured = []
    monkeypatch.setattr(s, "facts", lambda raw, *_: captured.append(raw) or (deepcopy(source), deepcopy(lines)))
    monkeypatch.setattr(s, "save_record", lambda doc: None)
    s.project_for_mx({"version": "foreign"}, {}, SimpleNamespace(business_timezone="America/Mexico_City"), origin=caller)
    assert captured[0]["version"] == "owner"


def test_source_change_stays_stale_until_explicit_reconfirmation(monkeypatch):
    record, source, lines = make_record()
    monkeypatch.setattr(s, "record_for", lambda *a, **k: record)
    monkeypatch.setattr(s, "save_record", lambda doc: None)
    monkeypatch.setattr(s, "facts", lambda *a: (deepcopy(source), [{**lines[0], "amount": "110.00"}]))
    _, routed = s.project_for_mx({}, {}, SimpleNamespace(business_timezone="America/Mexico_City"))
    assert record.state == "stale" and "stale" in routed[1]["issues"]
    monkeypatch.setattr(s, "facts", lambda *a: (deepcopy(source), deepcopy(lines)))
    s.project_for_mx({}, {}, SimpleNamespace(business_timezone="America/Mexico_City"))
    assert record.state == "stale"


@pytest.mark.parametrize("status", ["pending", "approved", "rejected", "withdrawn", "terminated"])
def test_current_approval_status_reaches_cn_without_changing_allocation(monkeypatch, status):
    record, source, lines = make_record()
    record.facts_json = s.encode([{**source, "approval_status": status}, lines])
    monkeypatch.setattr(s, "records", lambda: [record])
    result = s.cn_projection({"companies": {"China A"}})[1][0]
    assert result["status"] == status and result["amount"] == "30.00"


def test_admin_with_company_restrictions_cannot_edit_shared_source(monkeypatch):
    monkeypatch.setattr(frappe, "only_for", lambda role: None)
    monkeypatch.setattr(frappe, "get_all", lambda *a, **k: [frappe._dict(name="Visible"), frappe._dict(name="Hidden")])
    monkeypatch.setattr(frappe, "get_list", lambda *a, **k: ["Visible"])
    with pytest.raises(ValueError, match="every subsidiary"):
        s.require_manager()


def test_cn_company_access_never_grants_full_mixed_approval(monkeypatch):
    record, _, _ = make_record()
    monkeypatch.setattr(frappe, "get_doc", lambda *a: record)
    monkeypatch.setattr(mx, "_dingtalk_url", lambda *a: "private-url")
    result = s.cn_detail({"regional_allocation": "source"}, {"companies": {"China A"}, "manager": True})
    assert not result["approval_url"] and not result["tables"] and not result["attachments"]


def test_concurrent_editor_is_rejected_before_materialization(monkeypatch):
    record, _, _ = make_record()
    monkeypatch.setattr(s, "require_manager", lambda: {})
    monkeypatch.setattr(s, "require_connection", lambda record: None)
    monkeypatch.setattr(mx, "_settings", lambda **kw: SimpleNamespace())
    monkeypatch.setattr(frappe, "get_doc", lambda *a, **kw: record)
    monkeypatch.setattr(mx, "_upsert", lambda *a, **kw: pytest.fail("must not materialize stale edit"))
    with pytest.raises(ValueError, match="allocation changed"):
        s.save_allocation("mx", "", [], "note", "old", "old", allocation="source")


def test_schema_hides_raw_payload_and_prevents_generic_edits():
    from pathlib import Path
    path = Path(s.__file__).parents[1] / "deeplinkerp_branding/doctype/regional_expense_allocation/regional_expense_allocation.json"
    doc = json.loads(path.read_text())
    assert doc["permissions"] == [] and doc["track_changes"] == 1
    assert all(field["read_only"] for field in doc["fields"])


def test_cross_connection_records_fail_closed(monkeypatch):
    from deeplinkerp_branding.services import operating_oa_http as http
    monkeypatch.setattr(http, "configuration", lambda: ("new-url", ["corp"]))
    with pytest.raises(ValueError, match="another OA connection"):
        s.require_connection(frappe._dict(corp_id="corp", connection_fingerprint=digest_old()))


def test_mx_original_and_attachments_require_target_cn_company_permission(monkeypatch):
    monkeypatch.setattr(mx, "_mapping_version", lambda settings: settings.mapping_version or "")
    monkeypatch.setattr(s, "installed", lambda: True)
    monkeypatch.setattr(s, "allowed_companies", lambda *a: {"Mexico B"})
    monkeypatch.setattr(mx, "_companies", lambda *a: ["Mexico B"])
    monkeypatch.setattr(mx, "_manager", lambda *a: True)
    monkeypatch.setattr(mx, "_settings", lambda: frappe._dict(mapping_version="v1"))
    monkeypatch.setattr(frappe, "session", frappe._dict(user="Manager"))
    row = frappe._dict(name="cn-part", company="", regional_company="China A", is_active=1, company_basis="manual")
    monkeypatch.setattr(frappe, "get_all", lambda *a, **kw: [row])
    assert not mx.has_permission(row)
    assert not mx.source_has_permission(frappe._dict(name="approval"))
    assert not mx._whole_source_allowed("approval")
    monkeypatch.setattr(s, "allowed_companies", lambda *a: {"Mexico B", "China A"})
    assert mx.has_permission(row)
    assert mx._whole_source_allowed("approval")


def test_empty_original_line_list_cannot_drop_routed_money(monkeypatch):
    # A source parser failure must fail the entire synchronization, never
    # publish an empty MX projection while the CN plan still has old facts.
    record, source, lines = make_record()
    monkeypatch.setattr(s, "record_for", lambda *a, **kw: record)
    monkeypatch.setattr(s, "facts", lambda *a: (_ for _ in ()).throw(ValueError("invalid source")))
    monkeypatch.setattr(s, "save_record", lambda *a: pytest.fail("must not persist partial source"))
    with pytest.raises(ValueError, match="invalid source"):
        s.project_for_mx({}, {}, SimpleNamespace(business_timezone="America/Mexico_City"))


@pytest.mark.parametrize("origin", ["cn", "mx"])
def test_materialization_replaces_native_lines_and_replays_idempotently(monkeypatch, origin):
    """Exercise the actual MX writer over an isolated document store."""
    record, source, lines = make_record(origin)
    source.update(source_version="v2", request_date="2026-11-02")
    lines[0]["_source_entity_id"] = "organization-id"
    allocations = c.allocated_lines(source, lines, s.decode(record.allocations_json), valid=True)

    class Doc(frappe._dict):
        def is_new(self):
            return not self.get("name")

    store = {mx.SOURCE: {}, mx.LINE: {"old-native": Doc(name="old-native", source="source", is_active=1, amount="100.00")}}
    writes = []
    source.update(corp_id="corp", process_code="PROC-test")
    def get_doc(doctype, name, **kwargs):
        return store[doctype][name]
    def save(doc):
        doc.name = doc.get("name") or doc.get("line_key") or doc.get("source_key")
        store[doc.doctype][doc.name] = doc
        writes.append(doc.name)
    store[mx.LINE]["old-native"].doctype = mx.LINE
    monkeypatch.setattr(frappe, "db", SimpleNamespace(exists=lambda doctype, name: name in store[doctype]))
    monkeypatch.setattr(frappe, "get_doc", get_doc)
    monkeypatch.setattr(frappe, "new_doc", lambda doctype: Doc(doctype=doctype))
    monkeypatch.setattr(frappe, "get_all", lambda doctype, **kw: list(store[doctype].values()))
    monkeypatch.setattr(mx, "_save", save)
    monkeypatch.setattr(mx, "_template", lambda *a: {})
    monkeypatch.setattr(s, "project_for_mx", lambda *a, **kw: (deepcopy(source), deepcopy(allocations)))
    settings = Doc(company_mappings=[], mapping_version="v1", business_timezone="America/Mexico_City")
    mx._upsert({}, settings, regional_origin=origin)
    first_writes = list(writes)
    mx._upsert({}, settings, regional_origin=origin)
    assert writes == first_writes
    active = [line for line in store[mx.LINE].values() if line.is_active]
    assert len(active) == 2 and not store[mx.LINE]["old-native"].is_active
    assert sum(float(line.amount) for line in active) == 100
    assert [line.amount for line in active if line.expense_scope == "mx"] == ["70.00"]
    assert [line.regional_company for line in active if line.expense_scope == "non_mx"] == ["China A"]
    assert all("_source_entity_id" not in line for line in active)


def test_allocation_save_binds_revision_before_publishing_both_sides(monkeypatch):
    record, source, lines = make_record("cn")
    events = []
    monkeypatch.setattr(s, "require_manager", lambda: {"China A": "China", "Mexico B": "Mexico"})
    monkeypatch.setattr(s, "require_connection", lambda record: None)
    monkeypatch.setattr(s, "connection_key", lambda: "connection")
    monkeypatch.setattr(s, "categories", lambda: {"service": "Services"})
    monkeypatch.setattr(s, "facts", lambda *a: (source, lines))
    monkeypatch.setattr(s, "current_candidate", lambda *a, **kw: (json.loads(record.source_json), json.loads(record.template_json)))
    monkeypatch.setattr(s, "now_datetime", lambda: "2026-11-23")
    monkeypatch.setattr(s, "save_record", lambda doc: events.append("persist-plan"))
    monkeypatch.setattr(frappe, "session", frappe._dict(user="Administrator"))
    monkeypatch.setattr(frappe, "get_doc", lambda *a, **kw: record)
    monkeypatch.setattr(mx, "_settings", lambda **kw: events.append("lock-settings") or SimpleNamespace(business_timezone="America/Mexico_City"))
    monkeypatch.setattr(mx, "_upsert", lambda *a, **kw: events.append("materialize"))
    result = s.save_allocation("cn", "", record.allocations_json, "reviewed", "v1", record.reviewed_fingerprint,
        allocation=record.name, finance_node_names="MX Finance")
    assert events == ["lock-settings", "persist-plan", "materialize"]
    assert result["ok"] and record.state == "confirmed"
    assert json.loads(record.template_json)["finance_node_names"] == "MX Finance"


def test_cn_refresh_updates_only_its_owned_active_sources(monkeypatch):
    record, _, _ = make_record("cn")
    other, _, _ = make_record("mx")
    other.name = "mx-owned"
    monkeypatch.setattr(s, "installed", lambda: True)
    monkeypatch.setattr(s, "records", lambda: [record, other])
    monkeypatch.setattr(mx, "_settings", lambda **kw: "locked-settings")
    calls = []
    monkeypatch.setattr(mx, "_upsert", lambda raw, settings, **kw: calls.append((raw, settings, kw)))
    row = {"corp_id": "corp", "process_instance_id": "approval", "_routing_source": {"row": {"new": True}, "template": {"updated": True}}}
    s.refresh_cn([row], [{"corp_id": "corp", "process_code": "PROC-test"}])
    assert calls == [({"new": True}, "locked-settings", {"regional_origin": "cn", "regional_template": {"updated": True}})]


def test_cn_missing_active_source_marks_existing_allocation_stale(monkeypatch):
    record, _, _ = make_record("cn")
    monkeypatch.setattr(s, "installed", lambda: True)
    monkeypatch.setattr(s, "records", lambda: [record])
    monkeypatch.setattr(mx, "_settings", lambda **kw: None)
    monkeypatch.setattr(frappe, "get_doc", lambda *a, **kw: record)
    monkeypatch.setattr(s, "save_record", lambda doc: None)
    monkeypatch.setattr(mx, "_upsert", lambda *a, **kw: None)
    s.refresh_cn([], [{"corp_id": "corp", "process_code": "PROC-test"}])
    assert record.state == "stale"


def test_incremental_absence_is_not_a_deleted_approval(monkeypatch):
    record, _, _ = make_record("cn")
    before = deepcopy(record)
    monkeypatch.setattr(s, "installed", lambda: True)
    monkeypatch.setattr(s, "records", lambda: [record])
    monkeypatch.setattr(mx, "_settings", lambda **kw: None)
    monkeypatch.setattr(mx, "_upsert", lambda *a, **kw: pytest.fail("unchanged approval written"))
    monkeypatch.setattr(s, "save_record", lambda *a: pytest.fail("unchanged allocation written"))
    s.refresh_cn([], [{"corp_id": "corp", "process_code": "PROC-test"}], incremental=True)
    assert record == before


@pytest.fixture
def allocation_save(monkeypatch):
    """Keep source lookup/validation real; isolate only storage and projection."""
    from budget.integrations import cn_approval_history
    record, source, lines = make_record("cn")
    snapshot, writes, published = [], [], []
    raw = json.loads(record.source_json)
    template = {"corp_id": record.corp_id, "process_code": record.process_code, "approval_type": "operating"}
    row = {"key": "native-cn-key", "corp_id": record.corp_id, "process_code": record.process_code,
        "process_instance_id": record.process_instance_id, "kind": "operating",
        "_routing_source": {"row": raw, "template": template}}
    monkeypatch.setattr(s, "installed", lambda: True)
    monkeypatch.setattr(s, "records", lambda: [record])
    monkeypatch.setattr(s, "record_for", lambda *a, **kw: record)
    monkeypatch.setattr(s, "require_manager", lambda: {"China A": "China", "Mexico B": "Mexico"})
    monkeypatch.setattr(s, "require_connection", lambda record: None)
    monkeypatch.setattr(s, "connection_key", lambda: "connection")
    monkeypatch.setattr(s, "categories", lambda: {"service": "Services"})
    monkeypatch.setattr(s, "facts", lambda *a: (deepcopy(source), deepcopy(lines)))
    monkeypatch.setattr(s, "save_record", lambda doc: writes.append(deepcopy(doc)))
    monkeypatch.setattr(s, "now_datetime", lambda: "2026-11-23")
    monkeypatch.setattr(mx, "_settings", lambda **kw: frappe._dict(business_timezone="America/Mexico_City", templates=[]))
    monkeypatch.setattr(mx, "_template", lambda *a: template)
    monkeypatch.setattr(mx, "_upsert", lambda *a, **kw: published.append((a, kw)))
    monkeypatch.setattr(frappe, "get_doc", lambda doctype, *a, **kw:
        record if doctype == s.DOCTYPE else frappe._dict(raw_json=s.encode(raw)))
    monkeypatch.setattr(frappe, "session", frappe._dict(user="Administrator"))
    monkeypatch.setattr(cn_approval_history, "read_snapshot", lambda: {"rows": snapshot})
    return SimpleNamespace(record=record, source=source, lines=lines, row=row,
        snapshot=snapshot, writes=writes, published=published)


@pytest.mark.parametrize("entry", ["allocation", "cn-source", "mx-source"])
def test_missing_cn_source_cannot_be_reconfirmed_from_either_workbench(allocation_save, entry):
    case = allocation_save
    s.refresh_cn([], [{"corp_id": "corp", "process_code": "PROC-test"}])
    assert case.record.state == "stale"
    before = deepcopy(case.record)
    case.writes.clear()
    case.published.clear()
    # A different corporation/template with the same instance is not evidence.
    case.snapshot.extend([{**case.row, "corp_id": "other"}, {**case.row, "process_code": "PROC-other"}])
    args = {"origin": "mx" if entry == "mx-source" else "cn",
        "key": "source" if entry == "mx-source" else "native-cn-key"}
    if entry == "allocation":
        args["allocation"] = case.record.name
    if entry == "cn-source":
        case.snapshot.clear()
    with pytest.raises(ValueError, match="approval is unavailable"):
        s.save_allocation(**args, allocations=case.record.allocations_json, note="reviewed",
            expected_revision="v1", expected_fingerprint=case.record.reviewed_fingerprint)
    assert case.record == before and not case.writes and not case.published
    assert s.cn_projection({"companies": {"China A"}})[1][0]["amount"] is None


def test_reappeared_cn_source_can_be_reconfirmed_using_owner_identity(allocation_save):
    case = allocation_save
    case.record.state = "stale"
    case.snapshot.append({**case.row, "retired_template": True})
    # The MX caller's key differs from the native CN key. The saved owner wins.
    result = s.save_allocation("mx", "source", case.record.allocations_json, "reviewed", "v1",
        case.record.reviewed_fingerprint, finance_node_names="MX Finance")
    assert result["ok"] and case.record.origin == "cn" and case.record.state == "confirmed"
    assert len(case.writes) == len(case.published) == 1
    assert case.published[0][1]["regional_origin"] == "cn"


def test_changed_latest_source_requires_reloading_the_allocation_editor(allocation_save):
    case = allocation_save
    case.snapshot.append(case.row)
    case.lines[0]["amount"] = "110.00"
    with pytest.raises(ValueError, match="source changed"):
        s.save_allocation("cn", "", case.record.allocations_json, "reviewed", "v1",
            case.record.reviewed_fingerprint, allocation=case.record.name)
    assert not case.writes and not case.published


@pytest.mark.parametrize("origin", ["cn", "mx"])
@pytest.mark.parametrize("project_id,project_label", [
    ("TextField_97MDU018WPC0", "项目归属"), ("TextField_new", "Pertenencia del proyecto")])
def test_allocated_purchase_preserves_project_in_filters_report_and_export(monkeypatch, origin, project_id, project_label):
    import base64
    from io import BytesIO
    from openpyxl import load_workbook
    from test_mexico_expense_templates import sample as approval_sample, component
    from budget.services import cn_approval_contract as cn, cn_approval_management as management
    from budget.api import cn_approvals as api
    config, raw = approval_sample("purchase")
    raw.update(status="COMPLETED", result="AGREE")
    raw["raw_payload"].update(status="COMPLETED", result="AGREE")
    raw["raw_payload"]["formComponentValues"].extend([
        component(project_id, "PROJECT-QA", "TextField", project_label),
        component("DDDateField_B0K470VSL0O0", "2026-11-02", "DDDateField", "申请日期")])
    source, lines = s.facts(raw, config, "America/Mexico_City", origin == "cn")
    assert source["cn_project"] == next(item["value"] for item in cn.project(raw, config)["facts"] if item["label"] == "Project")
    record, _, _ = make_record(origin)
    record.facts_json = s.fact_payload(source, lines)
    record.allocations_json = s.encode([dict(line_key=lines[0]["line_key"], region="cn", company="China A", amount="100.00", management_category="service")])
    monkeypatch.setattr(s, "records", lambda: [record])
    monkeypatch.setattr(frappe.utils, "today", lambda: "2026-11-23")
    routed = s.cn_projection({"companies": {"China A"}})[1]
    filters = management.validate_filters({"project": "PROJECT-QA"})
    selected = management.selected_lines(routed, filters, details=True)
    assert len(selected) == 1 and selected[0]["project"] == "PROJECT-QA"
    assert management.selected_lines(routed, {**filters, "project": "", "no_project": "1"}, details=True) == []
    converted = management.convert_lines(selected, "CNY")
    groups = management.grouped(converted, ("company", "month", "management_category", "project"), 90)
    assert len(groups) == 1 and groups[0]["project"] == "PROJECT-QA" and groups[0]["purchase"] == "100.00"
    assert management.public_line(converted[0])["project"] == "PROJECT-QA"
    monkeypatch.setattr(management.approvals, "require_reader", lambda: None)
    monkeypatch.setattr(management.settings, "can_manage", lambda *a: False)
    monkeypatch.setattr(management, "load_lines", lambda: ({"has_snapshot": True}, routed,
        {"companies": {"China A"}}, management.rules_service.defaults()))
    monkeypatch.setattr(api, "_", lambda value: value)
    monkeypatch.setattr(frappe, "get_system_settings", lambda key: {"date_format": "yyyy-mm-dd", "time_format": "HH:mm:ss"}.get(key))
    report = management.report_data("purchase", filters)
    exported = api.export_management_report("purchase", filters)
    data = list(load_workbook(BytesIO(base64.b64decode(exported["content"]))).active.values)
    project_index = next(i for i, column in enumerate(report["columns"]) if column["fieldname"] == "project")
    amount_index = next(i for i, column in enumerate(report["columns"]) if column["fieldname"] == "purchase")
    assert data[-1][project_index] == "PROJECT-QA" and data[-1][amount_index] == 100


def test_project_uses_component_identity_and_never_guesses_conflicting_values():
    from test_mexico_expense_templates import sample as approval_sample, component
    from budget.services import cn_approval_contract as cn
    config, raw = approval_sample("purchase")
    fields = raw["raw_payload"]["formComponentValues"]
    fields.extend([component("TextField_97MDU018WPC0", "By ID", "TextField", "项目归属"),
        component("TextField_another", "By label", "TextField", "项目归属")])
    assert s.facts(raw, config, "America/Mexico_City")[0]["cn_project"] == "By ID"
    fields[-2]["id"] = "TextField_renamed"
    assert s.facts(raw, config, "America/Mexico_City")[0]["cn_project"] == ""
    assert not any(item["label"] == "Project" for item in cn.project(raw, config)["facts"])


def test_cn_private_archive_is_removed_before_public_approval_authorization():
    from budget.services.cn_approval_workbench import authorized
    row = {"corp_id": "corp", "entity": "China A", "issues": [], "_routing_source": {"raw": "private"}}
    result = authorized([row], {"aliases": {}, "organizations": {}, "known_companies": {"China A"}, "companies": {"China A"}, "manager": False})
    assert "_routing_source" not in result[0] and row["_routing_source"] == {"raw": "private"}


def test_cn_region_field_does_not_become_an_allocation_filter():
    from test_mexico_expense_templates import sample, component
    template, raw = sample()
    raw['raw_payload']['formComponentValues'].append(component('DDSelectField_1FEAISH1BZ4W0', 'anything', 'Unexpected'))
    _, native = s.facts(raw, template, 'America/Mexico_City')
    _, cn = s.facts(raw, template, 'America/Mexico_City', True)
    assert 'structure' in native[0]['base_issues']
    assert 'structure' not in cn[0]['base_issues']


@pytest.mark.parametrize("origin", ["cn", "mx"])
def test_pending_scope_remains_with_its_origin_and_cannot_look_complete(monkeypatch, origin):
    record, source, lines = make_record(origin)
    entry = dict(line_key="original", region="pending", company="", amount="100.00", management_category="")
    record.allocations_json = s.encode([entry])
    monkeypatch.setattr(s, "records", lambda: [record])
    _, cn = s.cn_projection({"companies": {"China A"}, "manager": True})
    mx_lines = c.allocated_lines(source, lines, [entry], valid=True, origin=origin)
    if origin == "cn":
        assert cn[0]["amount"] is None and "organization" in cn[0]["issues"]
        assert mx_lines[0]["expense_scope"] == "non_mx"
    else:
        assert cn == []
        assert mx_lines[0]["expense_scope"] == "mx" and mx_lines[0]["review_state"] == "needs_review"


def test_real_child_document_template_is_saved_as_fields_not_a_display_string(monkeypatch):
    class Child:
        def get(self, key):
            return {"approval_type": "operating", "process_code": "PROC-test", "finance_node_names": "MX Finance"}.get(key)
    record, source, lines = make_record()
    monkeypatch.setattr(s, "record_for", lambda *a, **kw: record)
    monkeypatch.setattr(s, "facts", lambda *a: (source, lines))
    monkeypatch.setattr(s, "save_record", lambda *a: None)
    s.project_for_mx({}, Child(), SimpleNamespace(business_timezone="America/Mexico_City"))
    saved = json.loads(record.template_json)
    assert isinstance(saved, dict) and saved["finance_node_names"] == "MX Finance"


def digest_old():
    return money.digest(["old-url"])
