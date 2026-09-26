from datetime import date as D

import pytest

from recur import next_occurrences as nxt


def test_every_days():
    assert nxt({"every_days": 3}, D(2026, 1, 1), 3) == [D(2026, 1, 1), D(2026, 1, 4), D(2026, 1, 7)]


def test_weekly():
    assert nxt({"weekly": ["mon", "wed"]}, D(2026, 1, 1), 4) == [D(2026, 1, 5), D(2026, 1, 7), D(2026, 1, 12),
                                                             D(2026, 1, 14)]


def test_weekly_includes_start():
    assert nxt({"weekly": ["mon"]}, D(2026, 1, 5), 1) == [D(2026, 1, 5)]


def test_monthly_clamp():
    assert nxt({"monthly_day": 31}, D(2026, 1, 15), 4) == [D(2026, 1, 31), D(2026, 2, 28), D(2026, 3, 31),
                                                          D(2026, 4, 30)]


def test_monthly_leap():
    assert nxt({"monthly_day": 30}, D(2028, 2, 1), 2) == [D(2028, 2, 29), D(2028, 3, 30)]


def test_monthly_start_after_day():
    assert nxt({"monthly_day": 10}, D(2026, 1, 15), 1) == [D(2026, 2, 10)]


def test_holidays():
    assert nxt({"every_days": 1}, D(2026, 1, 1), 3, holidays={D(2026, 1, 2)}) == [D(2026, 1, 1), D(2026, 1, 3),
                                                                                 D(2026, 1, 4)]


def test_zero():
    assert nxt({"every_days": 2}, D(2026, 1, 1), 0) == []


@pytest.mark.parametrize(("rule", "n"), [({"every_days": 1}, -1), ({"yearly": 1}, 1), ({"every_days": 0}, 1),
                                         ({"weekly": ["funday"]}, 1), ({"monthly_day": 32}, 1),
                                         ({"monthly_day": 0}, 1)])
def test_validation(rule, n):
    with pytest.raises(ValueError):
        nxt(rule, D(2026, 1, 1), n)
