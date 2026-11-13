"""Bounded projection of the OA archive's rawPayload, without cashier IO."""
from __future__ import annotations

from urllib.parse import urlencode

from .operating_expense_contract import digest

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

PAYMENT_NOTICE = "OA 接口尚未提供可核验的付款历史，付款登记待核对"


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def workflow(row):
    raw = row.get("raw_payload")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(_("OA 审批任务格式无效，请核对原单"))
    # Never combine contradictory copies of the same approval.
    for remote, field in (("originatorUserId", "originator_user_id"), ("status", "status"), ("result", "result")):
        if raw.get(remote) and row.get(field) and _text(raw[remote]).casefold() != _text(row[field]).casefold():
            raise ValueError(_("OA 审批身份或状态不一致，请核对原单"))
    tasks = raw.get("tasks") if raw.get("tasks") is not None else []
    operations = raw.get("operationRecords") if raw.get("operationRecords") is not None else []
    if any(not isinstance(items, list) or len(items) > 5000 or any(not isinstance(item, dict) for item in items)
           for items in (tasks, operations)):
        raise ValueError(_("OA 审批任务格式无效，请核对原单"))
    active, events, stages, seen = [], [], {}, set()
    for task in tasks:
        task_id = _text(task.get("taskId"))
        if not task_id or task_id in seen:
            raise ValueError(_("OA 审批任务身份缺失或重复，请核对原单"))
        seen.add(task_id)
        stage = _text(task.get("taskGroupName")) or _text(task.get("activityId"))
        activity = _text(task.get("activityId"))
        stages[activity] = stage
        user = _text(task.get("userId"))
        running = _text(task.get("status")).upper() == "RUNNING" and _text(row.get("status")).upper() == "RUNNING"
        if running:
            active.append({"id": task_id, "stage": stage, "status": "RUNNING", "activity_id": activity,
                           "entered_at": _text(task.get("createTime")), "assignees": [{"id": user, "name": None}] if user else []})
        # Operation records are the timeline authority when present. Otherwise
        # the task list still supplies bounded, explicitly attributed evidence.
        if not operations:
            events.append({"id": "task:" + task_id, "stage": stage, "operator": user, "operator_id": user,
                           "time": _text(task.get("finishTime")) or _text(task.get("createTime")),
                           "time_provenance": "oa.task", "result": _text(task.get("result")),
                           "current": running, "active": running})
    for index, operation in enumerate(operations):
        activity = _text(operation.get("activityId"))
        user = _text(operation.get("userId"))
        events.append({"id": "operation:" + str(index), "stage": stages.get(activity) or _text(operation.get("type")),
                       "operator": user, "operator_id": user, "time": _text(operation.get("date")),
                       "time_provenance": "oa.operation", "result": _text(operation.get("result")),
                       "comment": _text(operation.get("remark")), "current": False, "active": False})
    decision = {"can_register_payment": False, "reason": PAYMENT_NOTICE, "notice": "",
                "policy_version": "oa-archive-read-only-v1"}
    decision["evidence_fingerprint"] = digest({"identity": [row.get("corp_id"), row.get("process_instance_id")],
                                              "status": row.get("status"), "result": row.get("result"),
                                              "updated_at": str(row.get("updated_at") or ""), "tasks": tasks})
    return {"lookup_status": "found" if raw else "missing", "events": events, "current_tasks": active,
            "originator": {"id": row.get("originator_user_id"), "name": row.get("originator_user_name")},
            "approval_status": row.get("status"), "approval_result": row.get("result"),
            "source_updated_at": str(row.get("updated_at") or ""), "last_synced_at": str(row.get("updated_at") or ""),
            "original_url": "https://aflow.dingtalk.com/dingtalk/mobile/homepage.htm?" + urlencode({"procInstId": row["process_instance_id"]}),
            "payment_eligibility": decision}
