"""Permission-filtered, local approval facts for the CN budget report."""
import json

import frappe


def read_rows(end_date):
    from .operating_expenses import SOURCE, _list_rows, _require_fields, _settings, sync_config

    _require_fields(SOURCE, {"source_id", "source_company", "approval_state", "request_date"})
    rows = _list_rows({"date_to": end_date})
    # Parent identities have passed the same native/company permissions as the
    # CN worklist. Read only aliased reporting facts, never private source JSON.
    fields = {"oa_identity": "oa_identity", "reporting": "workflow_summary.reporting"}
    expressions = ", ".join(
        "JSON_EXTRACT(CASE WHEN JSON_VALID(source_json) THEN source_json ELSE '{}' END, '$."
        + path + "') AS `" + key + "`" for key, path in fields.items())
    facts = {}
    for offset in range(0, len(rows), 500):
        names = tuple(row["name"] for row in rows[offset:offset + 500])
        for row in frappe.db.sql("SELECT name, " + expressions +
                " FROM `tabOperating Expense Source` WHERE name IN %(names)s", {"names": names}, as_dict=True):
            facts[row.name] = {key: json.loads(row[key]) if row[key] else None for key in fields}
    names = {row["process_code"]: row["template_name"] for row in sync_config.templates(_settings())}
    result = []
    for row in rows:
        item = {**row, **facts.get(row["name"], {})}
        code = (item.get("reporting") or {}).get("process_code")
        item["process_name"] = names.get(code) or code
        result.append(item)
    return result
