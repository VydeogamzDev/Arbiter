"""Bookings are inclusive date ranges: a booking from the 3rd to the 5th occupies the 3rd, 4th and 5th."""
from datetime import date, timedelta


def overlaps(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    """Whether two inclusive bookings share at least one day."""
    return a_start <= b_end and b_start <= a_end


def free_days(start: date, end: date, bookings: list[tuple[date, date]]) -> list[date]:
    """Days in [start, end] that no booking occupies."""
    out = []
    day = start
    while day <= end:
        if not any(overlaps(day, day, s, e) for s, e in bookings):
            out.append(day)
        day += timedelta(days=1)
    return out
