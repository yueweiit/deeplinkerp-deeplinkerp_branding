"""Calendar comparisons and exact-money presentation for management reports."""
import calendar
from datetime import date, timedelta
from decimal import Decimal


def shift_month(day, offset):
    index = day.year * 12 + day.month - 1 + offset
    year, month = divmod(index, 12)
    month += 1
    return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1]))


def comparison_period(first, last, today):
    first, last, today = map(date.fromisoformat, (first, last, today))
    end = min(last, today)
    if first > end:
        return None
    full = first.day == 1 and end.day == calendar.monthrange(end.year, end.month)[1]
    if full:
        months = (end.year - first.year) * 12 + end.month - first.month + 1
        previous_start, previous_end = shift_month(first, -months), first - timedelta(days=1)
        mode = "calendar"
    elif first.day == 1 and first.year == end.year and first.month == end.month and end == today:
        previous_start, previous_end = shift_month(first, -1), shift_month(end, -1)
        mode = "month_to_date"
    else:
        previous_end = first - timedelta(days=1)
        previous_start = previous_end - (end - first)
        mode = "equal_days"
    if previous_start.year < 1900:
        return None
    return dict(from_date=first.isoformat(), to_date=end.isoformat(),
                previous_from=previous_start.isoformat(), previous_to=previous_end.isoformat(), mode=mode)


def amount(value):
    return Decimal(str(value or 0))


def money(value):
    return format(amount(value), ".2f")


def change(current, previous):
    current, previous = amount(current), amount(previous)
    return {"current": money(current), "previous": money(previous), "delta": money(current - previous),
            "percent": format((current - previous) / abs(previous) * 100, ".1f") if previous else None}


def ranking(current, previous, field):
    now = {r[field]: amount(r["amount"]) for r in current}
    before = {r[field]: amount(r["amount"]) for r in previous}
    total = sum(now.values(), Decimal(0))
    return [{"key": key, **change(now.get(key, 0), before.get(key, 0)),
             "share": format(now.get(key, 0) / total * 100, ".1f") if total else "0.0"}
            for key in sorted(now.keys() | before.keys(), key=lambda key: (-now.get(key, 0), key or ""))]
