"""Exact reporting-currency arithmetic; source approval facts remain unchanged."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

CURRENCIES = frozenset({"MXN", "USD", "CNY"})
CENT = Decimal("0.01")
RATE_PRECISION = Decimal("0.000000000001")


def positive_rate(value):
    try:
        rate = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return rate if rate.is_finite() and rate > 0 else None


def cross_rate(source_cny_rate, target_cny_rate):
    """Return target units per source unit, using the same rate date for both legs."""
    source, target = positive_rate(source_cny_rate), positive_rate(target_cny_rate)
    if source is None or target is None:
        raise ValueError("MX_FX_INVALID_RATE")
    with localcontext() as context:
        context.prec = 40
        return (source / target).quantize(RATE_PRECISION, rounding=ROUND_HALF_UP)


def converted_amount(amount, rate):
    """Round each original expense line once; aggregate the resulting cents."""
    factor = positive_rate(rate)
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if factor is None or not value.is_finite() or value < 0:
        return None
    with localcontext() as context:
        context.prec = 40
        return (value * factor).quantize(CENT, rounding=ROUND_HALF_UP)


def load_month_rate(currency, rate_date):
    """Reuse the existing FX transport without its costing-date fallback policy."""
    if currency == "CNY":
        return Decimal(1)
    import frappe
    from frappe import _
    try:
        from overseas_costing.services.fx_rate_service import fetch_cny_rate
    except ImportError:
        frappe.throw(_("The exchange rate service is unavailable; install Overseas Costing or contact the administrator"))
    key = f"mx-month-fx:v2:{rate_date}:{currency}"
    cached = frappe.cache.get_value(key)
    if cached:
        rate = positive_rate(cached)
        if rate is not None:
            return rate
    result = fetch_cny_rate(currency=currency, payment_date=rate_date)
    rate = positive_rate(result.get("cny_per_unit"))
    # The shared transport can default an absent rate date to the requested day.
    # Require the provider's own date as evidence for this exact-month-start policy.
    source_date = (result.get("raw") or {}).get("rateDate")
    if not result.get("ok") or result.get("rate_date") != rate_date or source_date != rate_date or rate is None:
        frappe.throw(_("Month-start exchange rate unavailable: {0} to CNY on {1}. Conversion is paused; retry after the rate is available.").format(currency, rate_date))
    # Cache only a validated exact-day response, never a failure or a prior-day rate.
    frappe.cache.set_value(key, str(rate), expires_in_sec=86400)
    return rate


class Conversion:
    def __init__(self, target, loader=None):
        if target not in CURRENCIES:
            raise ValueError("MX_FX_INVALID_CURRENCY")
        self.target = target
        self.loader = loader or load_month_rate
        self.rates = {}
        self.legs = {}

    def rate(self, currency, rate_date):
        if currency == self.target:
            return Decimal(1)
        if currency not in CURRENCIES or not rate_date:
            return None
        key = (currency, rate_date)
        if key not in self.rates:
            def leg(code):
                leg_key = (code, rate_date)
                if leg_key not in self.legs:
                    self.legs[leg_key] = Decimal(1) if code == "CNY" else self.loader(code, rate_date)
                return self.legs[leg_key]
            self.rates[key] = cross_rate(leg(currency), leg(self.target))
        return self.rates[key]

    def line(self, row):
        from datetime import date
        finance = row.get("finance_approved_date")
        basis = "finance" if finance else "request"
        raw_date = finance or row.get("request_date")
        try:
            rate_date = date.fromisoformat(str(raw_date)).replace(day=1).isoformat()
        except (ValueError, TypeError):
            rate_date = None
        factor = self.rate(row.get("currency"), rate_date)
        amount = converted_amount(row.get("amount"), factor)
        return {**row, "converted_amount": str(amount) if amount is not None else None,
                "reporting_currency": self.target, "exchange_rate": str(factor) if factor is not None else None,
                "exchange_rate_date": rate_date, "fx_date_basis": basis,
                "fx_estimated": not bool(finance),
                "fx_status": "converted" if amount is not None else "incomplete"}
