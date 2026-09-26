"""Monthly report."""
from billing.core.timeutil import month_bounds


def monthly_window(year: int, month: int):
    return month_bounds(year, month)
