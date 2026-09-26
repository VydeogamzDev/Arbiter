"""Aging report."""
from billing.core.timeutil import month_bounds


def aging_window(year: int, month: int):
    return month_bounds(year, month)
