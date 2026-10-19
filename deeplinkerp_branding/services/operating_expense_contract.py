"""Pure validation and stable financial facts for the read-only cashier contract."""
from __future__ import annotations

try:
    from frappe import _
except ImportError:
    def _(message):
        return message


import hashlib
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation

SOURCE_SYSTEM = "cashier-payment-archive"
SOURCE_URL = "https://payment.yueweiportal.com"
MAX_JSON_BYTES = 5 * 1024 * 1024


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def money(value, precision=None, positive=False):
    if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool) or value == "":
        raise ValueError(_("金额必须是精确十进制字符串"))
    try:
        result = Decimal(str(value))
        if not result.is_finite() or abs(result) >= Decimal("1e14") or (positive and result <= 0):
            raise ValueError(_("金额无效"))
        if precision is not None and result != result.quantize(Decimal(1).scaleb(-int(precision))):
            raise ValueError(_("金额精度不能由原生凭证完整表示，请先核查"))
        return result
    except InvalidOperation:
        raise ValueError(_("金额无效")) from None


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,140}", value):
        raise ValueError(_("来源标识无效"))
    return value


def attachment_path(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:/api/integrations/erp/(attachments|payment-vouchers)/[1-9][0-9]{0,18}|/oa-archive/[0-9a-f]{64})", value):
        raise ValueError(_("附件下载路径无效"))
    return value


def currency(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z]{3}", value):
        raise ValueError(_("来源币种无效，请核查"))
    return value


def validate_source(item):
    if not isinstance(item, dict) or len(json.dumps(item).encode()) > MAX_JSON_BYTES:
        raise ValueError(_("来源数据过大或格式无效"))
    if item.get("source_system") not in {SOURCE_SYSTEM, "dingtalk-oa"} or item.get("application_type") not in {"payment", "reimbursement", "unclassified"}:
        raise ValueError(_("来源协议无效"))
    identifier(item.get("source_id"))
    identifier(item.get("version"))
    currency(item.get("currency"))
    money(item.get("amount"))
    if item.get("request_date"):
        date.fromisoformat(item["request_date"])
    if not isinstance(item.get("approvals"), dict) or item["approvals"].get("eligibility") not in {"eligible", "blocked"} or not isinstance(item["approvals"].get("raw", {}), dict):
        raise ValueError(_("审批信息缺失"))
    for key in ("payments", "attachments"):
        if not isinstance(item.get(key), list) or len(item[key]) > 5000:
            raise ValueError(_("来源明细无效"))
        ids = set()
        for row in item[key]:
            if not isinstance(row, dict):
                raise ValueError(_("来源明细格式无效"))
            root = identifier(row.get("source_id"))
            if root in ids:
                raise ValueError(_("来源明细标识重复"))
            ids.add(root)
            if key == "payments":
                money(row.get("amount"))
                currency(row.get("currency"))
                if row.get("payment_date"):
                    date.fromisoformat(row["payment_date"])
            else:
                identifier(row.get("version"))
                attachment_path(row.get("url"))
    return item


def attachment_facts(rows):
    return sorted([{key: row.get(key) for key in ("source_id", "version", "filename", "payment_source_id")} for row in rows], key=lambda r: r["source_id"])


def expense_facts(item):
    attachments=item.get("attachments",[])
    if not isinstance(attachments,list) or any(not isinstance(row,dict) for row in attachments):
        raise ValueError(_("来源附件事实格式无效，请核对原单"))
    facts = {key: item.get(key) for key in ("source_system", "source_id", "source_company", "application_type", "application_type_raw", "applicant", "payee_name", "summary", "request_date", "currency", "original_source_amount", "original_source_currency", "storage_precision_warning", "source_conflict", "currency_conflict", "approvals")}
    if item.get("source_system") == "dingtalk-oa" and "company_mapping_source" in item:
        facts["source_company"] = item["company_mapping_source"]
    if item.get("source_system") == "dingtalk-oa" and isinstance(facts["approvals"], dict):
        approvals = facts["approvals"]
        if isinstance(approvals.get("raw"), dict):
            facts["approvals"] = {**approvals, "raw": {key: value for key, value in approvals["raw"].items() if not key.startswith("cashier_")}}
    facts["amount"] = format(money(item["amount"]).normalize(), "f")
    facts["attachments"] = attachment_facts([row for row in attachments if not row.get("payment_source_id")])
    return facts


def payment_facts(payment):
    facts = {key: payment.get(key) for key in ("source_id", "currency", "payment_date", "payment_account", "bank_reference", "payer", "remark", "source_type", "evidence_status")}
    facts["amount"] = format(money(payment["amount"]).normalize(), "f")
    return facts


def payment_intent_facts(item):
    """Freeze monetary intent, while rechecking live approval on every write.

    Voucher fingerprints remain unchanged/strict. For OA takeovers alone,
    cashier completion must not invalidate already registered partial payments.
    """
    facts = expense_facts(item)
    if item.get("source_system") == "dingtalk-oa":
        facts.pop("approvals", None)
        facts["oa_identity"] = item.get("oa_identity")
        facts["applicant_user_id"] = item.get("applicant_user_id")
    return facts


def payment_status(item):
    """Unknown actual balances cannot prove an unpaid or settled application."""
    try:
        if money(item.get("paid_amount")) < 0 or money(item.get("pending_amount")) < 0:
            return "付款待核对"
    except ValueError:
        return "付款待核对"
    return item.get("source_status") or "付款待核对"


def payment_decision(item):
    if item.get("source_conflict") or item.get("currency_conflict"):
        return {"can_register_payment": False, "reason": "来源归属或币种冲突，请核对"}
    if item.get("source_system") == "dingtalk-oa":
        approvals = item.get("approvals")
        original = approvals.get("raw") if isinstance(approvals, dict) else None
        if isinstance(original, dict) and (original.get("scope") == "withdrawn" or original.get("deleted_at")):
            return {"can_register_payment": False, "reason": "申请来源已移除或范围变化，请核对钉钉原单"}
        value = item.get("payment_eligibility")
        if not isinstance(value, dict) or type(value.get("can_register_payment")) is not bool:
            return {"can_register_payment": False, "reason": "审批证据待同步，请查看钉钉原单"}
        return value
    approvals = item.get("approvals")
    allowed = isinstance(approvals, dict) and approvals.get("eligibility") == "eligible"
    return {"can_register_payment": allowed, "reason": "" if allowed else "审批尚未通过，请核对"}


QUICK_TABS = ("all", "pending_work", "pending_payment", "approvals_running", "paid", "reconciliation")
MONEY_FIELDS = ("amount", "paid_amount", "pending_amount")


def _row_amounts(row):
    amounts = {}
    for field in MONEY_FIELDS:
        try:
            amounts[field] = money(row.get(field))
        except ValueError:
            amounts[field] = None
    return amounts


def _work_flags(row, amounts):
    """Visibility is not authorization; parse monetary facts once per row."""
    paid, pending = amounts["paid_amount"], amounts["pending_amount"]
    known = paid is not None and pending is not None and paid >= 0 and pending >= 0
    abnormal = (paid is not None and paid < 0) or (pending is not None and pending < 0)
    state = row.get("approval_state")
    unfinished = state not in {"rejected", "terminated", "withdrawn"} and not row.get("source_withdrawn")
    return {
        "all": True,
        "pending_work": unfinished and not abnormal and (not known or pending > 0),
        # Legacy callers retain the eligibility-filtered shortcut.
        "pending_payment": known and pending > 0 and (row.get("payment_eligibility") or {}).get("can_register_payment") is True,
        "approvals_running": state == "pending",
        "paid": known and pending == 0,
        "reconciliation": not known or not row.get("company") or state == "unknown" or bool(row.get("source_conflict") or row.get("currency_conflict") or row.get("projection_conflicts")),
    }


def quick_tab_matches(row, tab):
    if tab not in QUICK_TABS:
        raise ValueError(_("快捷筛选无效"))
    return _work_flags(row, _row_amounts(row))[tab]


def _accumulate_currency(buckets, row, amounts):
    bucket = buckets.setdefault(row.get("currency") or "未知", {
        "sums": {field: Decimal(0) for field in MONEY_FIELDS},
        "known": {field: 0 for field in MONEY_FIELDS}, "incomplete_fields": set(),
        "anomaly_count": 0, "anomaly_totals": {field: Decimal(0) for field in MONEY_FIELDS},
    })
    abnormal = any(value is not None and value < 0 for value in amounts.values())
    bucket["anomaly_count"] += int(abnormal)
    for field, value in amounts.items():
        if value is None:
            bucket["incomplete_fields"].add(field)
        elif value < 0 or (abnormal and field in {"paid_amount", "pending_amount"}):
            # Both balance sides of anomalous history are isolated together.
            bucket["anomaly_totals"][field] += value
            bucket["incomplete_fields"].add(field)
        else:
            bucket["sums"][field] += value
            bucket["known"][field] += 1


def _currency_result(buckets):
    result = {}
    for code, bucket in buckets.items():
        unknown = bucket["incomplete_fields"]
        result[code] = {field: None if field in unknown else str(bucket["sums"][field]) for field in MONEY_FIELDS}
        result[code].update(incomplete=bool(unknown), incomplete_fields=sorted(unknown),
            known_totals={field: str(bucket["sums"][field]) for field in unknown if bucket["known"][field]},
            anomaly_count=bucket["anomaly_count"],
            anomaly_totals={field: str(value) for field, value in bucket["anomaly_totals"].items() if value})
    return result


def currency_totals(rows):
    buckets = {}
    for row in rows:
        _accumulate_currency(buckets, row, _row_amounts(row))
    return _currency_result(buckets)


def worklist_summary(rows, tab):
    """One O(N) scan for all shortcut counts and the selected monetary totals."""
    if tab not in QUICK_TABS:
        raise ValueError(_("快捷筛选无效"))
    selected, buckets = [], {}
    counts = dict.fromkeys(QUICK_TABS, 0)
    for row in rows:
        amounts = _row_amounts(row)
        flags = _work_flags(row, amounts)
        blockers = []
        if any(value is not None and value < 0 for value in amounts.values()):
            blockers.append("付款金额异常，待核对")
        elif amounts["paid_amount"] is None or amounts["pending_amount"] is None:
            blockers.append("历史付款待核对")
        if not row.get("company"):
            blockers.append("法律公司待确认")
        elif any(issue in str(row.get("issues") or "") for issue in ("法律公司映射已变更", "法律公司映射已移除")):
            blockers.append("法律公司归属待复核")
        decision = row.get("payment_eligibility") or {}
        if decision.get("can_register_payment") is not True:
            blockers.append(decision.get("notice") or decision.get("reason") or "审批证据待核对")
        if row.get("projection_conflicts"):
            blockers.append("来源字段冲突，待核对")
        row["blocking_reason"] = "；".join(dict.fromkeys(blockers))
        for key, matches in flags.items():
            counts[key] += int(matches)
        if flags[tab]:
            selected.append(row)
            _accumulate_currency(buckets, row, amounts)
    return selected, counts, _currency_result(buckets)


def event_fingerprint(item, mapping, payment_id=None):
    facts = {"expense": expense_facts(item), "mapping": {k: v for k, v in mapping.items() if k not in {"payments", "approved_by", "approved_at", "source_version", "expense_fingerprint"}}}
    if payment_id:
        payment = next((p for p in item["payments"] if p["source_id"] == payment_id), None)
        if payment is None:
            raise ValueError(_("来源付款已撤回"))
        facts["payment"] = payment_facts(payment)
        facts["payment_mapping"] = mapping.get("payments", {}).get(payment_id)
        facts["evidence"] = attachment_facts([a for a in item["attachments"] if a.get("payment_source_id") == payment_id])
    return digest(_normalize_financial_numbers(facts))


def _normalize_financial_numbers(value):
    if isinstance(value, list):
        return [_normalize_financial_numbers(item) for item in value]
    if isinstance(value, dict):
        monetary = {"amount", "source_amount", "exchange_rate", "payable_exchange_rate", "bank_amount", "bank_exchange_rate"}
        return {key: format(money(item).normalize(), "f") if key in monetary and item is not None else _normalize_financial_numbers(item) for key, item in value.items()}
    return value
