import calendar
from datetime import date, timedelta

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _candidates(rule, start):
    if set(rule) == {"every_days"}:
        k = rule["every_days"]
        if not isinstance(k, int) or k < 1:
            raise ValueError("every_days must be >= 1")
        d = start
        while True:
            yield d
            d += timedelta(days=k)
    elif set(rule) == {"weekly"}:
        names = rule["weekly"]
        if not names or any(x not in DAYS for x in names):
            raise ValueError("bad weekday names")
        want = {DAYS.index(x) for x in names}
        d = start
        while True:
            if d.weekday() in want:
                yield d
            d += timedelta(days=1)
    elif set(rule) == {"monthly_day"}:
        day = rule["monthly_day"]
        if not isinstance(day, int) or not 1 <= day <= 31:
            raise ValueError("monthly_day must be 1..31")
        y, m = start.year, start.month
        while True:
            d = date(y, m, min(day, calendar.monthrange(y, m)[1]))
            if d >= start:
                yield d
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    else:
        raise ValueError(f"unknown rule {rule}")


def next_occurrences(rule: dict, start: date, n: int, holidays=()) -> list[date]:
    if n < 0:
        raise ValueError("n must be >= 0")
    gen = _candidates(rule, start)
    if n == 0:
        return []
    skip = set(holidays)
    out = []
    for d in gen:
        if d not in skip:
            out.append(d)
            if len(out) == n:
                break
    return out
