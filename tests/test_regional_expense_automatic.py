"""Automatic routing must partition amounts, never infer ownership from the feed."""
import json
from copy import deepcopy

import frappe
import pytest

from deeplinkerp_branding.services import regional_expense_automatic as a
from deeplinkerp_branding.services import regional_expenses as s
from deeplinkerp_branding.services import mexico_expenses as mx
from test_regional_expenses import sample, make_record


@pytest.fixture
def context(monkeypatch):
    value = {"companies": {"China A": "China", "Mexico B": "Mexico", "Other": "United States", "Unset": ""},
        "aliases": {("corp", None, "Department A"): {"China A"}},
        "rules": {"category_mappings": []}, "version": "rules-v1"}
    monkeypatch.setattr(frappe, "get_installed_apps", lambda: ["budget"])
    monkeypatch.setattr(a, "configuration", lambda *_: value)
    return value


@pytest.mark.parametrize("entity,kind,company,region,issue", [
    ("China A", "entity", "China A", "cn", ""),
    ("Mexico B", "entity", "Mexico B", "mx", ""),
    ("Department A", "department", "China A", "cn", ""),
    ("China A", "department", "China A", "cn", ""),
    ("China A / Mexico B", "entity", "", "pending", "unmapped_company"),
    ("China A Ltd", "entity", "", "pending", "unmapped_company"),
    ("Other", "entity", "Other", "pending", "company_country"),
    ("Unset", "entity", "Unset", "pending", "company_country"),
    ("", "entity", "", "pending", "unmapped_company"),
])
def test_exact_company_or_scoped_alias_then_country(context, entity, kind, company, region, issue):
    source, lines, _ = sample()
    lines[0].update(source_entity=entity, entity_kind=kind)
    entries, state = a.plan(source, lines, context)
    assert state == "confirmed"
    assert entries[0]["region"] == region
    assert entries[0]["matched_company"] == company
    assert entries[0]["company"] == (company if region != "pending" else "")
    assert entries[0]["assignment_issue"] == issue
    assert entries[0]["amount"] == "100.00"


def test_aliases_never_cross_corporations_or_resolve_conflicts(context):
    source, lines, _ = sample()
    entries, _ = a.plan({**source, "corp_id": "another-corp"}, lines, context)
    assert entries[0]["region"] == "pending"
    context["aliases"][("corp", "department", "Department A")] = {"Mexico B"}
    entries, _ = a.plan(source, lines, context)
    assert entries[0]["assignment_issue"] == "conflicting_company_mapping"
    context["aliases"][("corp", None, "China A")] = {"Mexico B"}
    lines[0].update(source_entity="China A", entity_kind="entity")
    assert a.plan(source, lines, context)[0][0]["region"] == "pending"


def test_multi_bearer_line_is_not_evenly_split_and_bad_amounts_are_not_confirmed(context):
    source, lines, _ = sample()
    lines[0]["base_issues"] = ["allocation"]
    assert a.plan(source, lines, context)[0][0]["region"] == "pending"
    for issue in ("amount", "total", "currency", "structure", "identity"):
        lines[0]["base_issues"] = [issue]
        assert a.plan(source, lines, context)[1] == "stale"


def test_country_rules_are_independent_of_currency_and_approval_status(context):
    source, lines, _ = sample()
    for currency in ("CNY", "MXN", "USD"):
        for status in ("pending", "approved", "withdrawn"):
            entries, _ = a.plan({**source, "approval_status": status, "currency": currency}, lines, context)
            assert entries[0]["region"] == "cn"
    context["companies"]["China A"] = "Mexico"
    assert a.plan(source, lines, context)[0][0]["region"] == "mx"


@pytest.mark.parametrize("kind", ["operating", "purchase"])
@pytest.mark.parametrize("order", [("cn", "mx"), ("mx", "cn")])
@pytest.mark.parametrize("date_value,expected_month", [
    (None, "2026-10"), ("2026-11-02", "2026-11"), ("", "2026-10"), ("invalid", None),
])
def test_shared_template_reporting_is_independent_of_sync_order(monkeypatch, kind, order, date_value, expected_month):
    from test_mexico_expense_templates import sample as approval_sample, component
    from budget.services import expense_management as report

    template, raw = approval_sample(kind)
    template.update(finance_node_names="", finance_activity_ids="")
    raw.update(status="COMPLETED", result="AGREE")
    raw["raw_payload"].update(status="COMPLETED", result="AGREE")
    if date_value is not None:
        raw["raw_payload"]["formComponentValues"].append(
            component("DDDateField_B0K470VSL0O0", date_value, "DDDateField", "申请日期"))
    settings = frappe._dict(templates=[template], business_timezone="America/Mexico_City")
    configuration = {"companies": {"Company MX": "Mexico"}, "aliases": {}, "organizations": {},
        "rules": {}, "version": "test"}
    saved = []
    monkeypatch.setattr(s, "record_for", lambda *args, **kwargs: saved[0] if saved else None)
    monkeypatch.setattr(s, "records", lambda: saved)
    monkeypatch.setattr(s, "installed", lambda: True)
    monkeypatch.setattr(s, "connection_key", lambda: "test")
    monkeypatch.setattr(s, "save_record", lambda doc: saved.append(doc) if not saved else None)
    monkeypatch.setattr(frappe, "new_doc", lambda *args: frappe._dict(name="test", modified="test"))
    monkeypatch.setattr(frappe, "get_all", lambda *args, **kwargs: [])
    monkeypatch.setattr(a, "configuration", lambda *args: configuration)
    monkeypatch.setattr(mx, "_settings", lambda: settings)
    context = {"companies": {"Company MX"}, "manager": True}
    for origin in order:
        s.project_for_mx(deepcopy(raw), deepcopy(template), settings, origin=origin)
        identities, rows = s.management_projection(context, include_legacy=False, submission_fallback=True)
        assert identities == {(raw["corp_id"], raw["process_instance_id"])}
        assert len(rows) == 1
        item = report.normalize(rows[0])
        assert item["month"] == expected_month
        assert item["eligible"] is bool(expected_month)
        assert item["status"] == "approved" and item["amount"] == "100.00"
        assert item["date_basis"] == ("submission" if date_value in (None, "") else "application")
        assert ("period" in item["issues"]) is (not expected_month)
        assert saved[0].origin == order[0]
    # Report fallback does not rewrite the saved evidence or legacy CN dates.
    source, _ = s.decode(saved[0].facts_json)
    assert source["cn_application_date"] == (date_value if expected_month and date_value else None)
    assert source.get("finance_approved_date") is None
    assert s.management_projection(context, include_legacy=False)[1][0]["month"] == (
        expected_month if date_value else None)


@pytest.fixture
def registry(monkeypatch, context):
    record, source, lines = make_record()
    lines[0]["classification_path"] = '[{"label":"工资","source_label":"工资"}]'
    entries, _ = a.plan(source, lines, context)
    record.allocations_json = json.dumps(entries)
    record.reviewed_by = record.reviewed_at = None
    monkeypatch.setattr(s, "record_for", lambda *args, **kwargs: record)
    monkeypatch.setattr(s, "records", lambda: [record])
    monkeypatch.setattr(s, "facts", lambda *args: (deepcopy(source), deepcopy(lines)))
    monkeypatch.setattr(s, "save_record", lambda doc: None)
    monkeypatch.setattr(mx, "_settings", lambda **kwargs: frappe._dict(mapping_version="", business_timezone="America/Mexico_City"))
    return record, source, lines


def test_automatic_source_changes_update_without_manual_confirmation(registry):
    record, source, lines = registry
    lines[0]["amount"] = source["total_amount"] = "123.45"
    source["approval_status"] = "withdrawn"
    _, routed = s.project_for_mx({}, {}, mx._settings())
    assert record.state == "confirmed"
    assert record.reviewed_by is None and record.reviewed_at is None
    assert routed[0]["expense_scope"] == "non_mx"
    assert routed[0]["company_basis"] == "mapping"
    result = s.cn_projection({"companies": {"China A"}})[1]
    assert len(result) == 1 and result[0]["amount"] == "123.45"
    assert result[0]["status"] == "withdrawn"
    assert result[0]["management_category"] == "salary"
    assert s.cn_projection({"companies": {"Mexico B"}})[1] == []


def test_missing_source_stays_unavailable_on_opposite_feed_replay(registry):
    record, _, _ = registry
    record.origin = "cn"
    s.project_for_mx({}, {}, mx._settings(), origin="cn", source_available=False)
    assert record.state == "stale"
    s.project_for_mx({}, {}, mx._settings(), origin="mx")
    assert record.state == "stale"
    assert s.cn_projection({"companies": {"China A"}})[1][0]["amount"] is None
    s.project_for_mx({}, {}, mx._settings(), origin="cn")
    assert record.state == "confirmed"


def test_changed_mapping_version_blocks_old_cn_totals_until_reprojected(registry, context):
    record, _, _ = registry
    context["version"] = "rules-v2"
    assert s.cn_projection({"companies": {"China A"}})[1][0]["amount"] is None
    s.project_for_mx({}, {}, mx._settings())
    assert s.cn_projection({"companies": {"China A"}})[1][0]["amount"] == "100.00"


def test_manual_plan_is_not_replaced_by_automatic_country_rules(registry, context, monkeypatch):
    record, _, _ = registry
    _, _, manual = sample()
    record.allocations_json = json.dumps(manual)
    context["companies"]["China A"] = "Mexico"
    monkeypatch.setattr(a, "configuration", lambda *_: pytest.fail("manual plan must not consult auto rules"))
    s.project_for_mx({}, {}, mx._settings())
    assert json.loads(record.allocations_json) == manual


def test_both_company_countries_partition_one_approval_once(registry, context):
    record, source, lines = registry
    lines[0]["amount"] = "30.00"
    lines.append({**lines[0], "line_key": "second", "source_entity": "Mexico B", "entity_kind": "entity", "amount": "70.00"})
    _, mx_lines = s.project_for_mx({}, {}, mx._settings())
    assert [row["amount"] for row in mx_lines if row["expense_scope"] == "mx"] == ["70.00"]
    identities, cn_lines = s.cn_projection({"companies": {"China A"}})
    assert identities == {("corp", "approval")}
    assert [row["amount"] for row in cn_lines] == ["30.00"]
    assert cn_lines[0]["allocation_amounts"] == ["30.00", "70.00"]


def test_cn_refresh_creates_ownership_for_new_expenses_but_not_budgets(monkeypatch):
    monkeypatch.setattr(s, "installed", lambda: True)
    monkeypatch.setattr(s, "records", lambda: [])
    monkeypatch.setattr(mx, "_settings", lambda **kwargs: "settings")
    calls = []
    monkeypatch.setattr(mx, "_upsert", lambda *args, **kwargs: calls.append((args, kwargs)))
    row = {"corp_id": "corp", "process_code": "PROC", "process_instance_id": "id", "kind": "operating",
        "_routing_source": {"row": {"raw": True}, "template": {"template": True}}}
    s.refresh_cn([row, {**row, "process_instance_id": "budget", "kind": "budget"}], [{"corp_id": "corp", "process_code": "PROC"}])
    assert len(calls) == 1 and calls[0][1]["regional_origin"] == "cn"


def test_first_sync_persists_automatic_plan_without_human_review(monkeypatch, context):
    source, lines, _ = sample()
    monkeypatch.setattr(s, "installed", lambda: True)
    monkeypatch.setattr(s, "record_for", lambda *args, **kwargs: None)
    monkeypatch.setattr(s, "facts", lambda *args: (deepcopy(source), deepcopy(lines)))
    monkeypatch.setattr(s, "connection_key", lambda: "connection")
    monkeypatch.setattr(frappe, "get_all", lambda *args, **kwargs: [])
    monkeypatch.setattr(frappe, "new_doc", lambda *args: frappe._dict())
    saved = []
    monkeypatch.setattr(s, "save_record", lambda doc: saved.append(deepcopy(doc)))
    settings = frappe._dict(mapping_version="", business_timezone="America/Mexico_City")
    s.project_for_mx(source, {}, settings)
    assert len(saved) == 1
    assert saved[0].origin == "mx" and saved[0].state == "confirmed"
    assert a.is_automatic(json.loads(saved[0].allocations_json))
    assert saved[0].reviewed_by is None and saved[0].reviewed_at is None


def test_native_manual_company_decision_is_not_replaced_by_foreign_feed(monkeypatch, context):
    monkeypatch.setattr(s, "installed", lambda: True)
    monkeypatch.setattr(s, "record_for", lambda *args, **kwargs: None)
    monkeypatch.setattr(frappe, "get_all", lambda *args, **kwargs: ["reviewed"])
    monkeypatch.setattr(s, "facts", lambda *args: pytest.fail("manual override must not be reprojected"))
    monkeypatch.setattr(mx.projection, "project", lambda *args: pytest.fail("CN cannot overwrite native MX manual source"))
    raw = {"corp_id": "corp", "process_code": "PROC", "process_instance_id": "id"}
    assert mx._upsert(raw, frappe._dict(), regional_origin="cn", regional_template={"approval_type": "operating"}) is None


def test_native_manual_mx_approval_cannot_be_counted_again_from_cn_snapshot(monkeypatch):
    from budget.services import cn_approval_management as cn
    monkeypatch.setattr(a, "legacy_manual_identities", lambda: {("corp", "id")})
    monkeypatch.setattr(s, "records", lambda: [])
    seen = []
    monkeypatch.setattr(cn, "analytical_lines", lambda rows, *_: seen.extend(rows) or [])
    row = {"corp_id": "corp", "process_instance_id": "id", "kind": "operating"}
    cn.with_regional_allocations({"rows": [row, {**row, "kind": "budget"}]}, {}, {})
    assert seen == [{**row, "kind": "budget"}]


def test_first_load_coalesces_local_rebuild_and_only_committed_completion_unblocks(monkeypatch):
    from types import SimpleNamespace
    values, queued, after_commit = {}, [], []
    class Cache:
        def make_key(self, key): return key
        def get_value(self, key): return values.get(key)
        def set(self, key, value, **kwargs):
            if key in values: return False
            values[key] = value
            return True
        def set_value(self, key, value): values[key] = value
        def delete_value(self, key): values.pop(key, None)
    monkeypatch.setattr(frappe, "local", SimpleNamespace(site="unit"))
    monkeypatch.setattr(s, "installed", lambda: True)
    monkeypatch.setattr(frappe, "cache", lambda: Cache())
    monkeypatch.setattr(frappe, "db", SimpleNamespace(after_commit=SimpleNamespace(add=after_commit.append)))
    monkeypatch.setattr(frappe, "enqueue", lambda *args, **kwargs: queued.append((args, kwargs)))
    monkeypatch.setattr(mx, "_projection_version", lambda settings: "version")
    assert a.ensure_rebuild({"settings": True})
    assert a.ensure_rebuild({"settings": True})
    assert len(queued) == 1 and "reproject_cache" in queued[0][0][0]
    a.rebuild_completed("version")
    assert a.ensure_rebuild({"settings": True}), "uncommitted rows cannot be exposed as a completed rebuild"
    after_commit[0]()
    assert not a.ensure_rebuild({"settings": True})


def test_configuration_save_only_queues_after_commit(monkeypatch):
    from types import SimpleNamespace
    callbacks, queued = [], []
    monkeypatch.setattr(s, "installed", lambda: True)
    monkeypatch.setattr(a, "invalidate_configuration", lambda: None)
    monkeypatch.setattr(a, "ensure_rebuild", lambda: queued.append("local-rebuild"))
    monkeypatch.setattr(frappe, "db", SimpleNamespace(after_commit=SimpleNamespace(add=callbacks.append)))
    a.enqueue_rebuild()
    assert queued == [] and len(callbacks) == 1
    callbacks[0]()
    assert queued == ["local-rebuild"]


@pytest.mark.parametrize("kind", ["operating", "purchase"])
@pytest.mark.parametrize("origin", ["cn", "mx"])
def test_existing_organization_id_mapping_keeps_company_totals_and_permissions(context, monkeypatch, kind, origin):
    from budget.services import cn_approval_contract as native, cn_approval_management as management
    from test_mexico_expense_templates import sample as approval_sample
    template, row = approval_sample(kind)
    row["raw_payload"]["formComponentValues"][0].update(value="Department A", extValue='{"code":"org-1"}')
    source, lines = s.facts(row, template, "America/Mexico_City", origin == "cn")
    context["aliases"] = {}
    context["organizations"] = {(row["corp_id"], "org-1"): "China A"}
    previous = native.project(row, template)
    assert management.company_for(previous, {**context, "known_companies": set(context["companies"])}) == "China A"
    entries, state = a.plan(source, lines, context)
    assert entries[0]["company"] == "China A" and entries[0]["region"] == "cn"
    assert entries[0]["amount"] == "100.00" and state == "confirmed"
    record = frappe._dict(name="source", origin=origin, state=state, modified="v1", **{
        key: row[key] for key in ("corp_id", "process_code", "process_instance_id")},
        facts_json=s.encode([source, lines]), allocations_json=s.encode(entries))
    monkeypatch.setattr(s, "records", lambda: [record])
    monkeypatch.setattr(a, "legacy_manual_identities", lambda: set())
    monkeypatch.setattr(mx, "_settings", lambda: object())
    assert s.cn_projection({"companies": {"China A"}})[1][0]["amount"] == "100.00"
    assert s.cn_projection({"companies": {"Mexico B"}})[1] == []
    assert a.plan({**source, "corp_id": "another-corp"}, lines, context)[0][0]["region"] == "pending"
    context["aliases"][(row["corp_id"], None, "Department A")] = {"Mexico B"}
    assert a.plan(source, lines, context)[0][0]["assignment_issue"] == "conflicting_company_mapping"


@pytest.mark.parametrize("family", ["operating", "expense"])
@pytest.mark.parametrize("ext", [{"id": "detail-org"}, '[{"code":"detail-org"}]', '[{"id":"one"},{"id":"two"}]', 'invalid'])
def test_detail_organization_id_does_not_inherit_header_company(context, family, ext):
    from test_mexico_expense_templates import sample as approval_sample, component
    from test_mexico_expense_contract import approval, approve_finance
    from deeplinkerp_branding.services import mexico_expense_templates as templates
    if family == "operating":
        template, row = approval_sample()
        fields = row["raw_payload"]["formComponentValues"]
        fields[0].update(value="Header company", extValue='{"id":"header-org"}')
        fields.append(component("TableField_G2ELEALN0S80", json.dumps([{
            "rowNumber": "detail-1", "rowValue": [
                {"key": "department", "label": "部门Departamento", "value": "Detail department", "extValue": ext},
                {"key": "MoneyField_X5KBWAODJ1S0", "value": "100"},
            ]}]), "TableField"))
    else:
        template = next(t for t in templates.default_templates() if t["approval_type"] == family)
        row = approve_finance(approval())
        fields = row["form_component_values"]
        details = json.loads(fields[3]["value"])
        details[0]["rowValue"][0].update(value="Detail department", extValue=ext)
        fields[3]["value"] = json.dumps(details)
        fields.append({"id": "DDMultiSelectField_RWULY2WAUCG0", "componentType": "DDMultiSelectField",
            "value": "Header company", "extValue": '{"id":"header-org"}'})
    context["organizations"] = {(row["corp_id"], "header-org"): "China A", (row["corp_id"], "detail-org"): "Mexico B"}
    source, lines = s.facts(row, template, "America/Mexico_City")
    entry = a.plan(source, lines, context)[0][0]
    valid = ext in ({"id": "detail-org"}, '[{"code":"detail-org"}]')
    assert entry["region"] == ("mx" if valid else "pending")
    assert entry["company"] == ("Mexico B" if valid else "")


def test_configuration_reuses_source_bound_organizations_and_versions_changes(monkeypatch):
    from types import SimpleNamespace
    from budget.services import cn_approval_settings as cn, ledger_sync_settings
    source = {"corp_id": "corp", "fingerprint": "current", "bound_fingerprint": "current",
        "organization_mappings": [{"corp_id": "corp", "department_id": "org", "company": "China A"},
            {"corp_id": "other", "department_id": "org", "company": "Mexico B"}]}
    monkeypatch.setattr(frappe, "local", SimpleNamespace(site="unit"))
    flags = frappe._dict()
    monkeypatch.setattr(frappe, "flags", flags)
    monkeypatch.setattr(s, "installed", lambda: True)
    monkeypatch.setattr(frappe, "get_installed_apps", lambda: ["budget"])
    monkeypatch.setattr(frappe, "get_all", lambda *args, **kwargs: [frappe._dict(name="China A", country="China")])
    monkeypatch.setattr(cn, "company_mappings", lambda: [])
    monkeypatch.setattr(cn, "management_rules", lambda: {"category_mappings": []})
    monkeypatch.setattr(ledger_sync_settings, "current_source", lambda: source)
    settings = {"company_mappings": [], "templates": []}
    first = a.configuration(settings)
    assert first["organizations"] == {("corp", "org"): "China A"}
    source["organization_mappings"][0]["company"] = "Mexico B"
    flags.clear()
    second = a.configuration(settings)
    assert second["version"] != first["version"]
    source["bound_fingerprint"] = "old-source"
    flags.clear()
    third = a.configuration(settings)
    assert third["organizations"] == {} and third["version"] != second["version"]


def test_category_suggested_by_cn_ui_is_applied_on_automatic_rebuild(context, monkeypatch):
    from budget.services import cn_approval_management as management, cn_approval_settings as cn
    from budget.services.cn_approval_management_rules import clean
    from test_mexico_expense_contract import approval, approve_finance
    from deeplinkerp_branding.services import mexico_expense_templates as templates
    template = next(t for t in templates.default_templates() if t["approval_type"] == "expense")
    source, lines = s.facts(approve_finance(approval()), template, "America/Mexico_City")
    context["aliases"][(source["corp_id"], None, lines[0]["source_entity"])] = {"China A"}
    entries, state = a.plan(source, lines, context)
    record = frappe._dict(name="source", origin="mx", state=state, modified="v1", **{
        key: source[key] for key in ("corp_id", "process_code", "process_instance_id")},
        facts_json=s.encode([source, lines]), allocations_json=s.encode(entries))
    monkeypatch.setattr(s, "records", lambda: [record])
    monkeypatch.setattr(a, "legacy_manual_identities", lambda: set())
    monkeypatch.setattr(mx, "_settings", lambda: object())
    visible = lambda: s.cn_projection({"companies": {"China A"}})[1]
    monkeypatch.setattr(cn, "require_manager", lambda: None)
    monkeypatch.setattr(management, "load_lines", lambda: ({}, visible(), {}, context["rules"]))
    suggestion = cn.get_unmapped_categories()["rows"][0]
    assert suggestion["source_path"] == ["Utilities and Operations", "CFE联邦电力委员会"]
    context["rules"] = clean({"category_mappings": [{**suggestion, "category": "utilities"}]})
    record.allocations_json = s.encode(a.plan(source, lines, context)[0])
    assert visible()[0]["management_category"] == "utilities"
    assert "management_category" not in visible()[0]["issues"]
    assert cn.get_unmapped_categories()["rows"] == []


@pytest.mark.parametrize("company", ["China A", "Mexico B"])
def test_cn_projection_loads_settings_once_regardless_of_record_count(registry, monkeypatch, company):
    record, _, _ = registry
    monkeypatch.setattr(s, "records", lambda: [deepcopy(record) for _ in range(30)])
    reads = []
    monkeypatch.setattr(mx, "_settings", lambda: reads.append(True) or object())
    result = s.cn_projection({"companies": {company}})[1]
    assert len(result) == (30 if company == "China A" else 0)
    assert len(reads) == 1


def test_internal_organization_metadata_preserves_existing_manual_review_fingerprints():
    from test_mexico_expense_templates import sample as approval_sample
    from deeplinkerp_branding.services import regional_expense_contract as contract
    template, row = approval_sample("purchase")
    source, before = s.facts(row, template, "America/Mexico_City")
    row["raw_payload"]["formComponentValues"][0]["extValue"] = '{"id":"org-1"}'
    updated, after = s.facts(row, template, "America/Mexico_City")
    assert after[0]["_source_entity_id"] == "org-1"
    assert before[0]["row_fingerprint"] == after[0]["row_fingerprint"]
    assert contract.fingerprint(source, before) == contract.fingerprint(updated, after)


@pytest.mark.parametrize('label,company', [
    ('UV IMPRESION MX', 'UV IMPRESION'), ('uv  impresión (MX)', 'UV IMPRESION'),
    ('YW MOLDES  MX', 'YW MOLDES'), ('Amigomart', 'AmigoMart'),
    ('LATÍNGO MX', 'LATINGO MX'),
])
def test_unique_formatted_company_names_are_recognized_without_manual_alias(context, label, company):
    context['companies'] = {company: 'Mexico'}
    context['aliases'] = {}
    assert a.resolve_company('corp', {'source_entity': label, 'entity_kind': 'entity'}, context) == (company, '')


def test_automatic_name_matching_is_unique_country_aware_and_not_fuzzy(context):
    context['companies'] = {'UV IMPRESION': 'Mexico', 'UV IMPRESIÓN': 'Mexico'}
    line = {'source_entity': 'uv impresion MX', 'entity_kind': 'entity'}
    assert a.resolve_company('corp', line, context) == ('', 'conflicting_company_mapping')
    context['companies'] = {'UV IMPRESION': 'China'}
    assert a.resolve_company('corp', line, context) == ('', 'unmapped_company')
    context['companies'] = {'UV IMPRESION': 'Mexico'}
    for label in ('UV IMPRESION II', 'UV IMPRESON', 'UV', 'UV IMPRESION / China A'):
        assert a.resolve_company('corp', {**line, 'source_entity': label}, context) == ('', 'unmapped_company')
    context['aliases'][('corp', 'entity', 'uv impresion MX')] = {'China A'}
    context['companies']['China A'] = 'China'
    assert a.resolve_company('corp', line, context) == ('China A', '')


def test_normalized_aliases_preserve_corporation_and_entity_kind_scope(context):
    context['aliases'] = {('corp', 'department', 'Service Á'): {'China A'}}
    line = {'source_entity': 'SERVICE A', 'entity_kind': 'department'}
    assert a.resolve_company('corp', line, context) == ('China A', '')
    assert a.resolve_company('another-corp', line, context) == ('', 'unmapped_company')
    assert a.resolve_company('corp', {**line, 'entity_kind': 'entity'}, context) == ('', 'unmapped_company')
