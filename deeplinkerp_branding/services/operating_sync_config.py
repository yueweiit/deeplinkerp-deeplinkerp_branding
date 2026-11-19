"""CN approval template configuration; existing company maps retain their identity."""
import re

from frappe import _

from .operating_oa_source import PROCESS_CODES


def templates(settings):
    rows = settings.get("approval_templates") or []
    if not rows:
        return [{"enabled": 1, "template_name": "Operating Expenses " + str(index), "process_code": code}
                for index, code in enumerate(PROCESS_CODES, 1)]
    return [{"enabled": int(bool(row.get("enabled"))), "template_name": row.get("template_name") or "",
             "process_code": row.get("process_code")} for row in rows]


def validate_templates(rows, previous):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ValueError(_("Configure between 1 and 100 approval templates."))
    result, codes = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(_("Invalid approval template."))
        code, name, enabled = row.get("process_code"), row.get("template_name"), row.get("enabled", 1)
        if (not isinstance(code, str) or not re.fullmatch(r"PROC-[A-Fa-f0-9]{8}(?:-[A-Fa-f0-9]{4}){3}-[A-Fa-f0-9]{12}", code)
                or code.upper() in codes or not isinstance(name, str) or not name.strip() or len(name) > 140
                or enabled not in (0, 1, "0", "1")):
            raise ValueError(_("Template names and unique PROC codes are required."))
        codes.add(code.upper())
        result.append({"enabled": int(enabled), "template_name": name.strip(), "process_code": code.upper()})
    if not {row["process_code"] for row in previous}.issubset(codes):
        raise ValueError(_("Disable historical templates instead of deleting or replacing their process codes."))
    return result


def process_codes(settings, *, enabled_only=False):
    return tuple(row["process_code"] for row in templates(settings) if not enabled_only or row["enabled"])
