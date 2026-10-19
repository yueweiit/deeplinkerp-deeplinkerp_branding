"""Exact operating payment facts. No IO, posting, or source mutation.

One pass over frozen history and local records, O(P) time and identifiers space.
Display rounding is deliberately not used to validate or sum payment facts.
"""

try:
    from frappe import _
except ImportError:
    def _(message):
        return message

from datetime import date
from decimal import Decimal

from deeplinkerp_branding.services.operating_expense_contract import money


def balance(history, records):
    if (history.get("payment_evidence_status") != "recorded"
            or history.get("source_conflict") or history.get("currency_conflict")
            or not isinstance(history.get("payments"), list)):
        raise ValueError(_("历史付款证据不完整，请先核对，不能按零付款接管"))
    amount = money(history.get("amount"), precision=2, positive=True)
    paid, seen = Decimal(0), set()
    for payment in history["payments"]:
        if (not isinstance(payment, dict) or not payment.get("source_id")
                or payment["source_id"] in seen or payment.get("evidence_status") != "recorded"
                or payment.get("currency") != history.get("currency")):
            raise ValueError(_("历史付款身份、证据或币种有冲突"))
        seen.add(payment["source_id"])
        paid += money(payment.get("amount"), precision=2, positive=True)
    if (paid != money(history.get("paid_amount"), precision=2)
            or amount - paid != money(history.get("pending_amount"), precision=2)):
        raise ValueError(_("历史付款明细与已付、待付合计不一致"))
    seen = set()
    for payment in records:
        name = payment.get("name")
        if not name or name in seen or payment.get("status") not in {"Registered", "Reversed"}:
            raise ValueError(_("ERP 付款记录身份或状态有冲突"))
        seen.add(name)
        if payment["status"] == "Registered":
            paid += money(payment.get("amount"), precision=2, positive=True)
    pending = amount - paid
    if pending < 0:
        raise ValueError(_("累计付款超过申请金额，请核对"))
    return {"paid_amount": format(paid, ".2f"), "pending_amount": format(pending, ".2f"),
            "source_status": "未付款" if paid == 0 else "已付款" if pending == 0 else "部分付款"}


def registration(values, pending, *, same_currency):
    if not isinstance(values, dict):
        raise ValueError(_("付款字段格式无效"))
    amount = money(values.get("amount"), precision=2, positive=True)
    bank_amount = money(values.get("bank_amount"), precision=2, positive=True)
    if amount > money(pending, precision=2):
        raise ValueError(_("本次付款超过待付金额"))
    if same_currency and bank_amount != amount:
        raise ValueError(_("同币种付款金额与抵扣申请金额必须一致"))
    payment_date = values.get("payment_date")
    if not isinstance(payment_date, str) or len(payment_date) != 10:
        raise ValueError(_("实际付款日期无效"))
    date.fromisoformat(payment_date)
    return {"amount": format(amount, ".2f"), "bank_amount": format(bank_amount, ".2f"),
            "payment_date": payment_date}
