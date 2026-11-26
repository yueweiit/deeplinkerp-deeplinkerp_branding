"""Department allocations retain ownership, category and the original monetary total."""
import copy
import json
from decimal import Decimal
from unittest.mock import patch

import pytest

from deeplinkerp_branding.services import mexico_expense_projection as projection
from deeplinkerp_branding.services import mexico_expense_templates as templates
from deeplinkerp_branding.services import mexico_expenses as service
from deeplinkerp_branding.services import regional_expense_automatic as automatic
from deeplinkerp_branding.services import regional_expense_contract as allocation
from test_mexico_expense_templates import component, sample

TABLE = "TableField_1T6MS9XEQUE80"


@pytest.fixture(autouse=True)
def no_site_translation(monkeypatch):
    # Match the existing pure money/projection tests; no site or Redis is needed.
    monkeypatch.setattr("deeplinkerp_branding.services.operating_expense_contract._", lambda value: value)


def department_row(index, amount, department=None):
    return {"rowNumber": str(index), "rowValue": [
        {"key": "DepartmentField_1C2A4A8ZG8TC0", "label": "部门Departamento",
         "value": department or f"Department {index}", "extValue": {"id": f"org-{index}"}},
        {"key": "MoneyField_D5PL7PJDHD40", "label": "金额（元）Monto (yuan)", "value": amount},
        {"key": "TextField_1AZVE05LBECG0", "label": "备注Nota", "value": f"Note {index}"},
    ]}


def approval():
    config, row = sample()
    fields = row["raw_payload"]["formComponentValues"]
    fields[0]["extValue"] = {"id": "header-org"}
    fields[3]["value"] = "奖金Bonificaciones"
    rows = [department_row(i, value) for i, value in enumerate(("10.01", "20.02", "30.03", "39.94"), 1)]
    fields.append(component(TABLE, json.dumps(rows), "TableField", "明细detalle"))
    return config, row, rows


def set_rows(row, rows):
    row["raw_payload"]["formComponentValues"][-1]["value"] = json.dumps(rows)


def project(row, config):
    return projection.project(row, config, "Asia/Shanghai", include_finance=False)


def test_four_departments_preserve_amounts_category_notes_and_source_identity():
    config, row, _ = approval()
    header = copy.deepcopy(row)
    header["raw_payload"]["formComponentValues"].pop()
    _, baseline = project(header, config)
    source, lines = project(row, config)
    assert len(lines) == 4 and len({line["line_key"] for line in lines}) == 4
    assert {line["source_entity"] for line in lines} == {f"Department {i}" for i in range(1, 5)}
    assert {line["_source_entity_id"] for line in lines} == {f"org-{i}" for i in range(1, 5)}
    assert {line["description"] for line in lines} == {f"Note {i}" for i in range(1, 5)}
    assert sum(Decimal(line["amount"]) for line in lines) == Decimal(source["total_amount"]) == 100
    for line in lines:
        assert line["table_id"] == TABLE and line["entity_kind"] == "department"
        assert line["currency"] == "CNY" and line["base_issues"] == []
        assert line["subcategory"] == "bonus"
        for key in ("category", "subcategory", "classification_path", "classification_key"):
            assert line[key] == baseline[0][key]
    assert json.loads(source["raw_json"]) == row
    assert len(json.loads(source["details_json"])["tables"][0]["rows"]) == 4


def test_reordering_rows_keeps_line_identities_and_amounts():
    config, row, rows = approval()
    _, original = project(row, config)
    set_rows(row, list(reversed(rows)))
    _, reordered = project(row, config)
    assert {line["line_key"]: line["row_fingerprint"] for line in original} == {
        line["line_key"]: line["row_fingerprint"] for line in reordered}


@pytest.mark.parametrize("change,issue", [
    ("sum", "total"), ("invalid_amount", "amount"), ("negative", "amount"),
    ("duplicate_row", "identity"), ("missing_row", "identity"),
    ("missing_department", "allocation"), ("multiple_departments", "allocation"),
    ("duplicate_cell", "structure"),
])
def test_invalid_allocations_remain_blocked(change, issue):
    config, row, rows = approval()
    if change == "sum":
        rows[0]["rowValue"][1]["value"] = "11.01"
    elif change in ("invalid_amount", "negative"):
        rows[0]["rowValue"][1]["value"] = "invalid" if change == "invalid_amount" else "-1"
    elif change == "duplicate_row":
        rows[1]["rowNumber"] = rows[0]["rowNumber"]
    elif change == "missing_row":
        rows[0].pop("rowNumber")
    elif change in ("missing_department", "multiple_departments"):
        rows[0]["rowValue"][0]["value"] = "" if change == "missing_department" else '["A", "B"]'
    else:
        rows[0]["rowValue"].append(copy.deepcopy(rows[0]["rowValue"][1]))
    set_rows(row, rows)
    _, lines = project(row, config)
    assert issue in {i for line in lines for i in line["base_issues"]}
    assert any(allocation.BLOCKING & set(line["base_issues"]) for line in lines)


def test_cny_details_cannot_be_confirmed_as_header_usd():
    config, row, _ = approval()
    row["raw_payload"]["formComponentValues"][2]["value"] = "USD"
    source, lines = project(row, config)
    assert source["currency"] == "USD"
    assert all(line["currency"] == "CNY" and "currency" in line["base_issues"] for line in lines)
    assert all("total" not in line["base_issues"] for line in lines)


def test_existing_field_mapping_can_bind_a_copied_allocation_table():
    config, row, _ = approval()
    row["raw_payload"]["formComponentValues"][-1]["id"] = "TableField_copy"
    assert "structure" in project(row, config)[1][0]["base_issues"]
    config["field_mapping"] = json.dumps({"allocation_table": ["TableField_copy"]})
    config = templates.normalize(config)
    lines = project(row, config)[1]
    assert len(lines) == 4 and all(not line["base_issues"] for line in lines)
    assert {line["table_id"] for line in lines} == {"TableField_copy"}


def test_same_generic_label_is_not_enough_to_recognize_an_unknown_table():
    config, row, _ = approval()
    row["raw_payload"]["formComponentValues"][-1]["id"] = "TableField_unknown"
    assert "structure" in project(row, config)[1][0]["base_issues"]


def test_department_allocation_is_not_a_purchase_support_table():
    config, row, _ = approval()
    config["approval_type"] = "purchase"
    assert "structure" in project(row, config)[1][0]["base_issues"]


def test_known_allocation_id_with_wrong_component_type_is_rejected():
    config, row, _ = approval()
    row["raw_payload"]["formComponentValues"][-1]["componentType"] = "TextareaField"
    assert "structure" in project(row, config)[1][0]["base_issues"]


def test_allocation_cannot_also_be_mapped_as_supporting_items():
    config, row, _ = approval()
    config["field_mapping"] = json.dumps({"items_table": [TABLE]})
    assert "structure" in project(row, config)[1][0]["base_issues"]


def test_empty_allocation_keeps_valid_header_behavior():
    config, row, _ = approval()
    set_rows(row, [])
    _, lines = project(row, config)
    assert len(lines) == 1 and lines[0]["table_id"] == "header" and lines[0]["amount"] == "100.00"
    assert lines[0]["base_issues"] == []


def test_department_organization_mapping_does_not_reuse_header_company(monkeypatch):
    config, row, _ = approval()
    source, lines = project(row, config)
    context = {"companies": {"China A": "China", "China B": "China"}, "aliases": {}, "version": "v1",
        "rules": {"category_mappings": []}, "organizations": {
            (source["corp_id"], "header-org"): "China A",
            (source["corp_id"], "org-1"): "China B"}}
    monkeypatch.setattr(automatic.frappe, "get_installed_apps", lambda: ["budget"])
    entries, state = automatic.plan(source, lines, context)
    assert state == "confirmed"
    assert entries[0]["company"] == "China B" and entries[0]["amount"] == "10.01"
    assert all(entry["region"] == "pending" and not entry["company"] for entry in entries[1:])


def test_save_time_preview_accepts_supported_rows_and_rejects_unknown_structure():
    config, row, _ = approval()
    with patch.object(service.http, "configuration", return_value=("http://qa", [config["corp_id"]])), \
            patch.object(service.http, "read_template_page", return_value=([row], {})) as fetch:
        preview = service._preview_template(config, "Asia/Shanghai", management=True)
        assert preview["applications"] == 1 and preview["lines"] == 4
        assert "structure" not in preview["issues"]
        fetch.assert_called_once_with("http://qa", config["corp_id"], config["process_code"], limit=3)
        row["raw_payload"]["formComponentValues"][-1]["id"] = "TableField_unknown"
        with patch.object(service.frappe, "throw", side_effect=ValueError), \
                patch.object(service, "_", side_effect=lambda value: value), pytest.raises(ValueError):
            service._preview_template(config, "Asia/Shanghai", management=True)
