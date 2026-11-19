"""OA cashier completion for reporting only, never bank/payment evidence."""
from datetime import datetime
from zoneinfo import ZoneInfo


# Exact source labels observed in CN templates; never match MX cashier nodes or
# infer a cashier from an approver identity, free-text remark or overall result.
CN_CASHIER_STAGES = frozenset({"出纳", "cn出纳cajero"})


def _timestamp(value):
    value = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("Cashier timestamp requires a timezone")
    return value


def project(row, tasks):
    result = {"version": 1, "status": "unknown", "completed_at": None, "date": None}
    state = str(row.get("status") or "").upper()
    outcome = str(row.get("result") or "").lower()
    if (row.get("deleted_at") or state not in {"RUNNING", "COMPLETED"}
            or outcome not in {"", "none", "agree"}
            or (state == "COMPLETED" and outcome != "agree")):
        return result
    relevant = [task for task in tasks if "".join(str(task.get("taskGroupName") or "").split()).casefold() in CN_CASHIER_STAGES]
    completed = []
    for task in relevant:
        status, outcome = str(task.get("status") or "").upper(), str(task.get("result") or "").upper()
        # Transfer/cancellation records are not payment events. The replacement
        # must independently finish; any remaining live/uncertain task blocks it.
        if (status, outcome) == ("CANCELED", "REDIRECTED"):
            continue
        if (status, outcome) != ("COMPLETED", "AGREE"):
            return result
        try:
            finish, start = _timestamp(task.get("finishTime")), _timestamp(task.get("createTime"))
            if finish < start:
                return result
        except (TypeError, ValueError, OverflowError):
            return result
        completed.append(finish)
    if completed:
        finish = max(completed).astimezone(ZoneInfo("Asia/Shanghai"))
        result.update(status="completed", completed_at=finish.isoformat(), date=finish.date().isoformat())
    return result


def eligible_date(row):
    """Validate the permission-filtered cached projection against current facts."""
    value = row.get("cashier_date")
    if (row.get("source_system") != "dingtalk-oa" or row.get("source_withdrawn")
            or row.get("approval_state") not in {"approved", "pending"}
            or row.get("source_conflict") or row.get("currency_conflict")
            or row.get("projection_conflicts")):
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date().isoformat()
        return value if value == parsed else None
    except (TypeError, ValueError):
        return None
