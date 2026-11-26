"""Shared mapping choices must match save permissions and country-based ownership."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import frappe
import pytest
from pypika import Table

from deeplinkerp_branding.services import mexico_expenses as mx
from deeplinkerp_branding.services import regional_expenses as regional
from deeplinkerp_branding.services import regional_expense_automatic as automatic


@pytest.fixture
def runtime(monkeypatch):
    companies = [frappe._dict(name=name, country=country, is_group=group) for name, country, group in (
        ("China A", "China", 0), ("Mexico B", "Mexico", 0), ("Other", "", 0),
        ("Group", "China", 1), ("Forbidden", "China", 0))]
    env = SimpleNamespace(companies=companies, visible={"China A", "Mexico B", "Other", "Group"},
        saved=[], queued=[], queries=[])
    env.settings = frappe._dict(enabled=0, business_timezone="Asia/Shanghai", templates=[],
        company_mappings=[], mapping_version=mx.digest([]), config_version="v1")
    def get_list(doctype, *, filters=None, user=None, **kwargs):
        assert doctype == "Company" and user == "manager"
        env.queries.append(filters)
        return [row.name for row in companies if row.name in env.visible and
                all(row.get(key) == value for key, value in (filters or {}).items())]
    def save(doc):
        doc.company_mappings = [frappe._dict(row) for row in doc.company_mappings]
        env.saved.append(doc)
    def throw(message, exc=frappe.ValidationError, **kwargs):
        raise exc(message)
    monkeypatch.setattr(frappe, "session", SimpleNamespace(user="manager"))
    monkeypatch.setattr(frappe, "get_list", get_list)
    monkeypatch.setattr(frappe, "has_permission", lambda *args, **kwargs: True)
    monkeypatch.setattr(frappe, "throw", throw)
    monkeypatch.setattr(mx, "_", lambda text: text)
    monkeypatch.setattr(mx, "_manager", lambda user=None: True)
    monkeypatch.setattr(mx, "_settings", lambda **kwargs: env.settings)
    monkeypatch.setattr(mx, "_save", save)
    monkeypatch.setattr(regional, "installed", lambda: True)
    monkeypatch.setattr(automatic, "invalidate_configuration", lambda: None)
    monkeypatch.setattr(automatic, "enqueue_rebuild", lambda: env.queued.append(True))
    monkeypatch.setattr(mx.http, "configuration", lambda: pytest.fail("mapping save must not read OA"))
    return env


def save(runtime, entries):
    return mx.save_settings(entries, "Asia/Shanghai", 0, runtime.settings.config_version)


def test_mapping_choices_use_authorized_leaf_companies_while_legacy_reports_stay_mexico(runtime):
    assert set(mx._mapping_companies()) == {"China A", "Mexico B", "Other"}
    assert mx._companies() == ["Mexico B"]
    assert runtime.queries == [{"is_group": 0}, {"country": "Mexico", "is_group": 0}]
    with pytest.raises(frappe.PermissionError):
        mx._company_access("China A")


def test_saved_chinese_aliases_can_be_reopened_and_resaved_without_oa(runtime):
    entries = [{"source_entity": kind, "entity_kind": kind, "company": "China A"}
               for kind in ("entity", "department")]
    assert save(runtime, entries) == {"ok": True}
    assert [row.company for row in runtime.settings.company_mappings] == ["China A", "China A"]
    assert mx._can_manage_settings(runtime.settings)
    assert mx.settings_has_permission(runtime.settings, user="manager", ptype="read")
    assert save(runtime, entries) == {"ok": True}
    assert len(runtime.queued) == 1  # no-op save must not enqueue another rebuild
    assert len(runtime.saved) == 2
    # The two rows share one target scope lookup per save, plus settings access.
    assert len(runtime.queries) == 6


@pytest.mark.parametrize("company", ["Forbidden", "Group", "Missing", "", None, ["China A"], {"name": "China A"}])
def test_invalid_or_unauthorized_targets_cannot_be_saved(runtime, company):
    with pytest.raises(frappe.PermissionError):
        save(runtime, [{"source_entity": "Alias", "entity_kind": "entity", "company": company}])
    assert runtime.saved == runtime.queued == []
    assert runtime.settings.company_mappings == []


def test_document_permission_is_still_enforced(runtime, monkeypatch):
    monkeypatch.setattr(frappe, "has_permission", lambda *args, **kwargs: False)
    with pytest.raises(frappe.PermissionError):
        save(runtime, [{"source_entity": "Alias", "entity_kind": "entity", "company": "China A"}])
    assert runtime.saved == []


def test_losing_access_to_an_existing_chinese_mapping_blocks_read_and_replacement(runtime):
    save(runtime, [{"source_entity": "Alias", "entity_kind": "entity", "company": "China A"}])
    runtime.visible.remove("China A")
    assert not mx.settings_has_permission(runtime.settings, user="manager", ptype="read")
    with pytest.raises(frappe.PermissionError):
        mx._require_settings_access(runtime.settings)
    with pytest.raises(frappe.PermissionError):
        save(runtime, [])
    assert len(runtime.saved) == 1 and runtime.settings.company_mappings[0].company == "China A"


def test_non_mexico_targets_require_shared_routing_storage(runtime, monkeypatch):
    monkeypatch.setattr(regional, "installed", lambda: False)
    assert mx._mapping_companies() == ["Mexico B"]
    with pytest.raises(frappe.PermissionError):
        save(runtime, [{"source_entity": "Alias", "entity_kind": "entity", "company": "China A"}])
    assert runtime.saved == []


def test_entity_recommendations_include_chinese_companies_without_assigning_them(runtime, monkeypatch):
    query = MagicMock()
    for method in ("where", "groupby", "orderby", "offset", "limit"):
        getattr(query, method).return_value = query
    query.run.return_value = [frappe._dict(source_entity="China A", entity_kind="entity", line_count=2)]
    get_query = MagicMock(return_value=query)
    monkeypatch.setattr(frappe, "qb", SimpleNamespace(DocType=lambda name: Table(name), get_query=get_query))
    monkeypatch.setattr(mx, "_finance", lambda: None)
    monkeypatch.setattr(mx, "_mapping_version", lambda settings: "v1")
    result = mx.get_unmapped_entities()
    assert result["rows"][0]["suggestions"] == [{"company": "China A", "match": "normalized"}]
    assert get_query.call_args.kwargs["ignore_permissions"] is False
    assert runtime.saved == runtime.queued == []


def test_saved_aliases_route_by_company_country_not_source_currency(runtime, monkeypatch):
    from budget.services import cn_approval_settings, organization_mapping
    save(runtime, [{"source_entity": "Alias " + company, "entity_kind": "entity", "company": company}
                   for company in ("China A", "Mexico B", "Other")])
    runtime.settings.templates = [frappe._dict(corp_id="corp")]
    monkeypatch.setattr(frappe, "local", SimpleNamespace(site="isolated"))
    monkeypatch.setattr(frappe, "flags", frappe._dict())
    monkeypatch.setattr(frappe, "get_installed_apps", lambda: ["budget"])
    monkeypatch.setattr(frappe, "get_all", lambda *args, **kwargs: [row for row in runtime.companies if not row.is_group])
    monkeypatch.setattr(cn_approval_settings, "company_mappings", lambda: [])
    monkeypatch.setattr(cn_approval_settings, "management_rules", lambda: {"category_mappings": []})
    monkeypatch.setattr(organization_mapping, "approval_companies", lambda: {})
    context = automatic.configuration(runtime.settings)
    lines = [{"line_key": str(index), "source_entity": "Alias " + company, "entity_kind": "entity",
              "amount": "10.00", "base_issues": [], "classification_path": "[]"}
             for index, company in enumerate(("China A", "Mexico B", "Other"))]
    entries, state = automatic.plan({"corp_id": "corp", "approval_type": "expense", "currency": "MXN"}, lines, context)
    assert state == "confirmed"
    assert [row["region"] for row in entries] == ["cn", "mx", "pending"]
    assert entries[2]["assignment_issue"] == "company_country"
    assert [row["amount"] for row in entries] == ["10.00"] * 3
