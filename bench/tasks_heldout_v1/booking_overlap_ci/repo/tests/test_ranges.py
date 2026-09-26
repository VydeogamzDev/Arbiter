from datetime import date

from scheduling.ranges import overlaps


def test_disjoint():
    assert not overlaps(date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 4), date(2026, 1, 5))


def test_back_to_back_same_day():
    # checkout day of one booking is the check-in day of the next: both occupy the 3rd
    assert overlaps(date(2026, 1, 1), date(2026, 1, 3), date(2026, 1, 3), date(2026, 1, 5))
